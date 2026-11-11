"""One-line notes for the call feed: what each ai& API call actually did, in at most 16 words.

When a logged call finishes, a fast ai& model reads a short digest of it (what was asked, the
tools called, the answer, or what was embedded or reranked) and writes one line in English and
one in Japanese. The card then shows the line in the page's language.

The note calls go straight to the ai& API with a plain HTTP client, so they are never logged
themselves and never get notes of their own. Off unless KUMO_FEED_NOTES=1 (and KUMO_DEBUG=1,
since there is no feed without it).
"""

import json
import logging
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx

from . import calllog

MODEL = os.environ.get("KUMO_MODEL_FEED_NOTE", "google/gemma-4-31b-it")
MAX_WORDS = 16
SKIP_KINDS = {"model list"}
log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="feednote")
_pending = threading.Semaphore(24)  # A burst (a big upload) skips notes instead of queueing for minutes.

PROMPT = """You write the one-line notes in a live feed of AI API calls, shown on a projector at a developer meetup.
Each note says what this one call actually did, concretely: what it looked up, decided, produced or embedded.
Name the real things (deal names, tools, files, regions) when the digest has them. No hype, no "this call".

Reply with exactly two lines:
EN: <English, at most 14 words>
JA: <Japanese, at most 40 characters>

Digest of the call:
"""


def enabled() -> bool:
    return (
        calllog.enabled()
        and os.environ.get("KUMO_FEED_NOTES") == "1"
        and bool(os.environ.get("AIAND_API_KEY"))
    )


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content or [] if isinstance(p, dict))


def digest(row: dict) -> str:
    """A few lines that tell the note model what happened, without the whole request and response."""
    from .app import _assembled  # app imports calllog, which imports this module lazily

    try:
        req = json.loads(row["request"]) if row["request"] else {}
    except ValueError:
        req = {}
    req = req if isinstance(req, dict) else {}
    lines = [
        f"kind: {row['kind']}",
        f"caller: {row['caller']}",
        f"model: {row.get('model') or req.get('model') or '?'}",
        f"HTTP status: {row['status']}",
    ]
    if row["kind"] == "embeddings":
        inputs = req.get("input")
        inputs = inputs if isinstance(inputs, list) else [inputs]
        first = str(inputs[0] if inputs else "")
        # EmbeddingGemma's task prefixes say what the text is; spell that out instead of passing them on.
        role = "texts"
        if first.startswith("task: ") and "| query: " in first:
            role, first = (
                "search queries (embedded to find matching documents)",
                first.split("| query: ", 1)[1],
            )
        elif first.startswith("title: ") and "| text: " in first:
            role, first = "document chunks (embedded for a knowledge base)", first.split("| text: ", 1)[1]
        lines += [f"{role}: {len(inputs)}", f"first one: {_clip(first, 300)}"]
        return "\n".join(lines)
    if row["kind"] == "rerank":
        docs = req.get("documents") or []
        lines += [f"query: {_clip(req.get('query'), 200)}", f"documents ranked: {len(docs)}"]
        if docs:
            first = docs[0] if isinstance(docs[0], str) else (docs[0] or {}).get("text", "")
            lines.append(f"first document: {_clip(first, 200)}")
        return "\n".join(lines)
    messages = req.get("messages") or []
    system = next((_text(m.get("content")) for m in messages if m.get("role") == "system"), "")
    user = next((_text(m.get("content")) for m in reversed(messages) if m.get("role") == "user"), "")
    if system:
        lines.append(f"system prompt: {_clip(system, 300)}")
    if user:
        lines.append(f"last user message: {_clip(user, 400)}")
    if messages and messages[-1].get("role") == "tool":
        # A tool round: the model is reading what a tool returned.
        calls = [
            (tc.get("function") or {}).get("name")
            for m in messages
            if m.get("role") == "assistant"
            for tc in m.get("tool_calls") or []
        ]
        lines.append(f"tools called earlier in this turn: {', '.join(c for c in calls[-4:] if c)}")
        lines.append(f"latest tool result: {_clip(_text(messages[-1].get('content')), 400)}")
    out = _assembled(row["response"] or "") or {}
    if out.get("tools"):
        lines.append(f"this call asked to run tools: {', '.join(out['tools'][:6])}")
    if out.get("content"):
        lines.append(f"answer: {_clip(out['content'], 500)}")
    return "\n".join(lines)


def _parse(text: str) -> dict | None:
    note = {}
    for line in text.splitlines():
        head, _, rest = line.partition(":")
        if head.strip().upper() in ("EN", "JA") and rest.strip():
            note[head.strip().lower()] = rest.strip()
    if "en" not in note:
        return None
    words = note["en"].split()
    if len(words) > MAX_WORDS:
        note["en"] = " ".join(words[:MAX_WORDS]) + "…"
    note.setdefault("ja", note["en"])
    return note


def write(conn, call_id: str, transport: httpx.BaseTransport | None = None) -> dict | None:
    """Writes the note for one finished call and bumps its seq so the feed redraws the card."""
    row = conn.execute("SELECT * FROM llm_call WHERE id = ?", (call_id,)).fetchone()
    if row is None or row["status"] == 0 or row["kind"] in SKIP_KINDS:
        return None
    row = dict(row)
    base = os.environ.get("AIAND_BASE_URL", "https://api.aiand.com/v1").rstrip("/")
    with httpx.Client(timeout=30, transport=transport) as client:
        r = client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {os.environ.get('AIAND_API_KEY', '')}"},
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": PROMPT + digest(row)}],
                "max_tokens": 120,
                "temperature": 0.2,
            },
        )
    r.raise_for_status()
    note = _parse(r.json()["choices"][0]["message"].get("content") or "")
    if note is None:
        return None
    with calllog._lock:
        conn.execute(
            "UPDATE llm_call SET note = ?, seq = ? WHERE id = ?",
            (json.dumps(note, ensure_ascii=False), calllog._next_seq(conn), call_id),
        )
        conn.commit()
    return note


def schedule(conn, call_id: str | None) -> None:
    """Writes the note in the background, so the call itself is never slowed down."""
    if call_id is None or not enabled() or not _pending.acquire(blocking=False):
        return

    def run():
        try:
            write(conn, call_id)
        except (httpx.HTTPError, sqlite3.Error, KeyError, IndexError, TypeError, ValueError) as e:
            # A missing note is fine; the card still shows everything else.
            log.warning("feed note for %s failed: %s", call_id, e)
        finally:
            _pending.release()

    _pool.submit(run)


def pick(note: str | None, lang: str) -> str:
    """The note in the page's language, for the card template."""
    if not note:
        return ""
    try:
        d = json.loads(note)
    except ValueError:
        return ""
    return d.get(lang) or d.get("en") or ""
