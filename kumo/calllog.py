"""Debug log of every call to the ai& API, for the /debug panel.

Two kinds of calls are logged, both as the raw HTTP request and response:
* Kumo's own AI features, through httpx event hooks on Kumo's OpenAI client.
* Calls from other apps (Open WebUI) that use Kumo's pass-through at /v1/* instead of
  calling ai& directly.

Off unless KUMO_DEBUG=1, because the log stores prompts and answers. Only the newest
KEEP calls are kept.
"""

import datetime as dt
import json
import os
import threading
import time
import uuid

import httpx

KEEP = 500
MAX_BODY = 200_000
_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("KUMO_DEBUG") == "1"


def _body(raw: bytes, content_type: str) -> str:
    if "json" not in content_type and "event-stream" not in content_type:
        return f"<{len(raw):,} bytes, {content_type or 'unknown type'}>"
    text = raw.decode("utf-8", errors="replace")
    return (
        text if len(text) <= MAX_BODY else text[:MAX_BODY] + f"\n… ({len(text) - MAX_BODY:,} more characters)"
    )


def _kind(path: str, request: dict) -> str:
    """A short label for the panel: what this call is for."""
    if "embeddings" in path:
        return "embeddings"
    if "rerank" in path:
        return "rerank"
    if "models" in path:
        return "model list"
    messages = request.get("messages") or []
    system = " ".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    # Open WebUI's titles, tags and follow-ups use prompts that start with "### Task:".
    if any("### Task:" in str(m.get("content", "")) for m in messages):
        return "background task"
    if messages and messages[-1].get("role") == "tool":
        return "tool round"
    if "json_schema" in json.dumps(request.get("response_format") or {}) or "JSON" in system:
        return "structured output"
    return "chat"


def _usage(response_text: str) -> tuple[str | None, int | None, int | None]:
    """Model and token counts from a JSON response or the chunks of a stream."""
    model, usage = None, None
    candidates = [response_text]
    if response_text.lstrip().startswith("data:"):
        candidates = [ln[5:].strip() for ln in response_text.splitlines() if ln.startswith("data:")]
    for c in candidates:
        try:
            ev = json.loads(c)
        except ValueError:
            continue
        if isinstance(ev, dict):
            model = ev.get("model") or model
            usage = ev.get("usage") or usage
    usage = usage or {}
    return model, usage.get("prompt_tokens"), usage.get("completion_tokens")


def _next_seq(conn) -> int:
    return conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM llm_call").fetchone()[0]


def start(
    conn, *, source: str, caller: str, method: str, path: str, request_raw: bytes, request_type: str
) -> str | None:
    """Logs a call as in progress (status 0) and returns its id, or None when logging is off."""
    if not enabled():
        return None
    try:
        request = json.loads(request_raw) if request_raw and "json" in request_type else {}
    except ValueError:
        request = {}
    request = request if isinstance(request, dict) else {}
    row = {
        "id": "call_" + uuid.uuid4().hex[:12],
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "source": source,
        "caller": caller,
        "method": method,
        "path": path,
        "kind": _kind(path, request),
        "model": request.get("model"),
        "stream": int(bool(request.get("stream"))),
        "request": _body(request_raw, request_type) if request_raw else "",
    }
    with _lock:
        row["seq"] = _next_seq(conn)
        conn.execute(
            "INSERT INTO llm_call (id, created_at, source, caller, method, path, kind, model, stream, status, "
            "latency_ms, request, response, seq, first_seq) VALUES (:id, :created_at, :source, :caller, :method, "
            ":path, :kind, :model, :stream, 0, 0, :request, '', :seq, :seq)",
            row,
        )
        conn.execute(
            "DELETE FROM llm_call WHERE id NOT IN (SELECT id FROM llm_call ORDER BY created_at DESC LIMIT ?)",
            (KEEP,),
        )
        conn.commit()
    return row["id"]


def finish(
    conn, call_id: str | None, *, status: int, started: float, response_raw: bytes, response_type: str
) -> None:
    """Fills in the result of a call logged by start()."""
    if call_id is None:
        return
    response_text = _body(response_raw, response_type)
    model, tokens_in, tokens_out = _usage(response_text)
    with _lock:
        conn.execute(
            "UPDATE llm_call SET status = ?, latency_ms = ?, input_tokens = ?, output_tokens = ?, response = ?, "
            "model = COALESCE(model, ?), seq = ? WHERE id = ?",
            (
                status,
                int((time.monotonic() - started) * 1000),
                tokens_in,
                tokens_out,
                response_text,
                model,
                _next_seq(conn),
                call_id,
            ),
        )
        conn.commit()
    from . import feednote  # imported here: feednote imports this module

    feednote.schedule(conn, call_id)


def record(
    conn,
    *,
    source,
    caller,
    method,
    path,
    status,
    started,
    request_raw,
    request_type,
    response_raw,
    response_type,
) -> None:
    """Logs a call that has already finished."""
    call_id = start(
        conn,
        source=source,
        caller=caller,
        method=method,
        path=path,
        request_raw=request_raw,
        request_type=request_type,
    )
    finish(
        conn, call_id, status=status, started=started, response_raw=response_raw, response_type=response_type
    )


def http_client(conn, caller: str, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """An httpx client for Kumo's OpenAI client that logs each request and response."""

    def on_request(request: httpx.Request) -> None:
        request.extensions["kumo_started"] = time.monotonic()
        request.extensions["kumo_call"] = start(
            conn,
            source="kumo",
            caller=caller,
            method=request.method,
            path=request.url.path.removeprefix("/v1"),
            request_raw=request.content,
            request_type=request.headers.get("content-type", ""),
        )

    def on_response(response: httpx.Response) -> None:
        response.read()
        req = response.request
        finish(
            conn,
            req.extensions.get("kumo_call"),
            status=response.status_code,
            started=req.extensions.get("kumo_started", time.monotonic()),
            response_raw=response.content,
            response_type=response.headers.get("content-type", ""),
        )

    return httpx.Client(
        event_hooks={"request": [on_request], "response": [on_response]}, timeout=120, transport=transport
    )
