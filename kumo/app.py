"""Kumo web app: HTML pages, the JSON API (also an Open WebUI tool server) and exports."""

import csv
import datetime as dt
import io
import json
import os
import secrets
import time
from pathlib import Path
from typing import Annotated, Literal

import httpx
import jinja2
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import ai, calllog, db, feednote, i18n, kbsync

HERE = Path(__file__).parent
STALE_SQL = "CAST(julianday(:today) - julianday(substr(d.stage_changed_at, 1, 10)) AS INTEGER)"


def today() -> str:
    return os.environ.get("KUMO_TODAY") or dt.date.today().isoformat()


def now() -> str:
    """The current time in JST, on the demo date when KUMO_TODAY is set."""
    t = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).replace(microsecond=0)
    return f"{today()}T{t.timetz().isoformat()}"


app = FastAPI(
    title="Kumo",
    version="0.1.0",
    description=(
        "Kumo is a small sales CRM. Use these tools to look up deals, companies and people, "
        "and to get AI summaries and next steps for a deal. Amounts are JPY. "
        "Kumoは営業CRMです。案件・会社・担当者の検索、案件のAI要約と次のアクションの取得に使います。金額は円です。"
    ),
)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals["debug_enabled"] = calllog.enabled


def page_theme(ctx) -> str:
    """light (default), dark or auto (follow the OS), from ?theme= or the kumo_theme cookie set by the stage toggle."""
    req = ctx.get("request")
    value = req and (req.query_params.get("theme") or req.cookies.get("kumo_theme"))
    return value if value in ("light", "dark", "auto") else "light"


templates.env.globals["page_theme"] = jinja2.pass_context(page_theme)
templates.env.filters["note"] = feednote.pick
JST = dt.timezone(dt.timedelta(hours=9))
i18n.install(templates.env)


def _age(created_at: str) -> float:
    return max(0.0, (dt.datetime.now(dt.UTC) - dt.datetime.fromisoformat(created_at)).total_seconds())


templates.env.filters["jst"] = lambda iso: dt.datetime.fromisoformat(iso).astimezone(JST).strftime("%H:%M:%S")
templates.env.filters["age"] = _age
templates.env.filters["short_model"] = lambda m: (m or "-").split("/")[-1]
templates.env.filters["k"] = lambda n: "-" if n is None else (f"{n / 1000:.1f}k" if n >= 1000 else str(n))
conn = db.connect()
# Fill Open WebUI's "Kumo CRM" knowledge base at startup (after a reseed, too), retrying while it starts.
kbsync.sync_later(conn, retries=30)


def require_key(authorization: Annotated[str | None, Header()] = None, write: bool = False) -> str:
    keys = {os.environ.get("KUMO_API_KEY"): "write", os.environ.get("KUMO_READ_KEY"): "read"}
    keys.pop(None, None)
    if not keys:
        return "write"  # no keys configured: local development
    token = (authorization or "").removeprefix("Bearer ").strip()
    for k, role in keys.items():
        if token and secrets.compare_digest(token, k):
            return role
    raise HTTPException(401, "Missing or wrong API key")


def require_write(role: Annotated[str, Depends(require_key)]) -> str:
    if role != "write":
        raise HTTPException(403, "This key is read-only")
    return role


Read = Annotated[str, Depends(require_key)]
Write = Annotated[str, Depends(require_write)]

DEAL_SELECT = f"""
SELECT d.id, d.name, d.owner, d.status, d.amount_jpy, d.expected_close_on, d.stage_changed_at, d.closed_at,
       d.lost_reason, d.created_at, s.key AS stage, s.name_ja AS stage_ja, s.name_en AS stage_en, s.probability,
       c.id AS company_id, c.legal_name AS company, c.name_romaji AS company_romaji, c.region, c.industry, c.tier,
       {STALE_SQL} AS days_in_stage
FROM deal d JOIN stage s ON s.id = d.stage_id JOIN company c ON c.id = d.company_id
"""


def query_deals(
    stage=None,
    region=None,
    owner=None,
    status=None,
    min_amount=None,
    stale_days=None,
    close_before=None,
    company_id=None,
    q=None,
    limit=200,
) -> list[dict]:
    where, args = [], {"today": today()}
    for col, val in (
        ("s.key", stage),
        ("c.region", region),
        ("d.owner", owner),
        ("d.status", status),
        ("c.id", company_id),
    ):
        if val:
            where.append(f"{col} = :{col.replace('.', '_')}")
            args[col.replace(".", "_")] = val
    if min_amount is not None:
        where.append("d.amount_jpy >= :min_amount")
        args["min_amount"] = min_amount
    if stale_days is not None:
        where.append(f"d.status = 'open' AND {STALE_SQL} >= :stale")
        args["stale"] = stale_days
    if close_before:
        where.append("d.expected_close_on <= :close_before")
        args["close_before"] = close_before
    if q:
        where.append(
            "(d.name LIKE :q OR c.legal_name LIKE :q OR c.name_kana LIKE :q OR c.name_romaji LIKE :q)"
        )
        args["q"] = f"%{q}%"
    sql = (
        DEAL_SELECT
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY d.amount_jpy DESC LIMIT :limit"
    )
    args["limit"] = limit
    return db.rows(conn.execute(sql, args))


# ---------------------------------------------------------------- JSON API (tool server)

StageKey = Literal["lead", "initial", "discovery", "poc", "negotiation", "won", "lost"]
Region = Literal["関東", "関西", "中部", "九州", "北海道", "東北", "中国・四国"]


@app.get(
    "/api/deals",
    operation_id="search_deals",
    tags=["deals"],
    summary="Search deals / 案件を検索",
    description="Find deals by stage, region, owner, status, minimum amount (JPY), days stuck in the current "
    "stage (stale_days), expected close date or name. Returns up to `limit` deals, largest first. "
    "ステージ・地域・担当・金額・停滞日数などで案件を検索します。",
)
def api_deals(
    _: Read,
    stage: StageKey | None = None,
    region: Region | None = None,
    owner: str | None = None,
    status: Literal["open", "won", "lost"] | None = None,
    min_amount: int | None = None,
    stale_days: Annotated[
        int | None, Query(description="Open deals with no stage change for at least this many days")
    ] = None,
    close_before: Annotated[str | None, Query(description="YYYY-MM-DD")] = None,
    q: Annotated[str | None, Query(description="Text in the deal or company name")] = None,
    limit: Annotated[int, Query(le=500)] = 50,
):
    deals = query_deals(stage, region, owner, status, min_amount, stale_days, close_before, None, q, limit)
    base = os.environ.get("KUMO_PUBLIC_URL", "").rstrip("/")
    for d in deals:
        d["url"] = f"{base}/deals/{d['id']}"
    return {"count": len(deals), "deals": deals}


@app.get(
    "/api/deals/{deal_id}",
    operation_id="get_deal",
    tags=["deals"],
    summary="Get one deal with its activities / 案件の詳細と活動履歴",
)
def api_deal(deal_id: str, _: Read):
    d = db.row(conn.execute(DEAL_SELECT + " WHERE d.id = :id", {"id": deal_id, "today": today()}))
    if not d:
        raise HTTPException(404, "No such deal")
    d["activities"] = db.rows(
        conn.execute(
            "SELECT a.occurred_at, a.kind, a.summary, a.body, p.name_kanji AS person FROM activity a "
            "LEFT JOIN person p ON p.id = a.person_id WHERE a.deal_id = ? ORDER BY a.occurred_at DESC",
            (deal_id,),
        )
    )
    d["people"] = db.rows(
        conn.execute(
            "SELECT id, name_kanji, name_romaji, title, email FROM person WHERE company_id = ?",
            (d["company_id"],),
        )
    )
    return d


class DealPatch(BaseModel):
    stage: StageKey | None = None
    amount_jpy: int | None = Field(None, ge=0)
    expected_close_on: str | None = None
    lost_reason: (
        Literal["price", "timing", "competitor", "no_budget", "no_decision", "requirements"] | None
    ) = None


def apply_patch(deal_id: str, patch: DealPatch) -> None:
    stamp = now()
    if patch.stage:
        st = db.row(conn.execute("SELECT id, category FROM stage WHERE key = ?", (patch.stage,)))
        status = st["category"] if st["category"] in ("won", "lost") else "open"
        conn.execute(
            "UPDATE deal SET stage_id = ?, status = ?, stage_changed_at = ?, closed_at = ?, lost_reason = ? WHERE id = ?",
            (
                st["id"],
                status,
                stamp,
                stamp if status != "open" else None,
                (patch.lost_reason or "no_decision") if status == "lost" else None,
                deal_id,
            ),
        )
    if patch.amount_jpy is not None:
        conn.execute("UPDATE deal SET amount_jpy = ? WHERE id = ?", (patch.amount_jpy, deal_id))
    if patch.expected_close_on:
        conn.execute("UPDATE deal SET expected_close_on = ? WHERE id = ?", (patch.expected_close_on, deal_id))
    conn.commit()
    kbsync.sync_later(conn, [deal_id])


@app.patch(
    "/api/deals/{deal_id}",
    operation_id="update_deal",
    tags=["deals"],
    summary="Change a deal's stage, amount or close date / 案件を更新（書き込み）",
    description="WRITE operation: ask the user to confirm before calling. 書き込み操作です。実行前に必ずユーザーに確認してください。",
)
def api_patch(deal_id: str, patch: DealPatch, _: Write):
    if not db.row(conn.execute("SELECT id FROM deal WHERE id = ?", (deal_id,))):
        raise HTTPException(404, "No such deal")
    apply_patch(deal_id, patch)
    return api_deal(deal_id, "write")


@app.get(
    "/api/pipeline/summary",
    operation_id="pipeline_summary",
    tags=["pipeline"],
    summary="Pipeline totals by stage and region, with the weighted forecast / ステージ別・地域別の集計と確度加重の見込み",
)
def api_pipeline(_: Read):
    by_stage = db.rows(
        conn.execute(
            "SELECT s.key AS stage, s.name_ja, s.probability, COUNT(d.id) AS deals, "
            "COALESCE(SUM(d.amount_jpy), 0) AS amount_jpy, "
            "COALESCE(SUM(d.amount_jpy * s.probability / 100), 0) AS weighted_amount_jpy "
            "FROM stage s LEFT JOIN deal d ON d.stage_id = s.id GROUP BY s.id ORDER BY s.position"
        )
    )
    by_region = db.rows(
        conn.execute(
            "SELECT c.region, COUNT(d.id) AS open_deals, COALESCE(SUM(d.amount_jpy), 0) AS open_amount_jpy "
            "FROM deal d JOIN company c ON c.id = d.company_id WHERE d.status = 'open' GROUP BY c.region ORDER BY 3 DESC"
        )
    )
    return {"today": today(), "by_stage": by_stage, "open_by_region": by_region}


@app.get(
    "/api/companies",
    operation_id="search_companies",
    tags=["companies"],
    summary="Search companies / 会社を検索",
)
def api_companies(
    _: Read,
    q: str | None = None,
    region: Region | None = None,
    tier: Literal["standard", "key"] | None = None,
):
    where, args = [], {}
    if q:
        where.append("(legal_name LIKE :q OR name_kana LIKE :q OR name_romaji LIKE :q)")
        args["q"] = f"%{q}%"
    if region:
        where.append("region = :region")
        args["region"] = region
    if tier:
        where.append("tier = :tier")
        args["tier"] = tier
    sql = (
        "SELECT * FROM company" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY legal_name"
    )
    return {"companies": db.rows(conn.execute(sql, args))}


@app.get(
    "/api/people", operation_id="search_people", tags=["companies"], summary="Search people / 担当者を検索"
)
def api_people(_: Read, q: str | None = None, company_id: str | None = None):
    where, args = [], {}
    if q:
        where.append("(p.name_kanji LIKE :q OR p.name_kana LIKE :q OR p.name_romaji LIKE :q)")
        args["q"] = f"%{q}%"
    if company_id:
        where.append("p.company_id = :cid")
        args["cid"] = company_id
    sql = (
        "SELECT p.*, c.legal_name AS company FROM person p JOIN company c ON c.id = p.company_id"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY p.name_kana LIMIT 100"
    )
    return {"people": db.rows(conn.execute(sql, args))}


class ActivityIn(BaseModel):
    kind: Literal["meeting", "call", "email", "note"] = "note"
    summary: str = Field(max_length=80)
    body: str | None = None


@app.post(
    "/api/deals/{deal_id}/activities",
    operation_id="add_activity",
    tags=["deals"],
    summary="Add an activity to a deal / 活動を記録（書き込み）",
    description="WRITE operation: ask the user to confirm before calling. 書き込み操作です。実行前に必ず確認してください。",
)
def api_add_activity(deal_id: str, a: ActivityIn, _: Write):
    aid = "ac_" + secrets.token_hex(5)
    conn.execute(
        "INSERT INTO activity VALUES (?, ?, NULL, ?, ?, ?, ?)",
        (
            aid,
            deal_id,
            a.kind,
            now(),
            a.summary,
            a.body or a.summary,
        ),
    )
    conn.commit()
    kbsync.sync_later(conn, [deal_id])
    return {"id": aid}


@app.post(
    "/api/deals/{deal_id}/ai/summary",
    operation_id="ai_deal_summary",
    tags=["ai"],
    summary="AI summary of a deal (runs on ai&) / 案件のAI要約",
)
def api_ai_summary(deal_id: str, _: Read, lang: Literal["ja", "en"] = "ja"):
    return ai.summary(conn, deal_id, lang)


@app.post(
    "/api/deals/{deal_id}/ai/next-steps",
    operation_id="ai_next_steps",
    tags=["ai"],
    summary="AI next steps for a deal (runs on ai&) / 次のアクションをAIが提案",
)
def api_ai_next(deal_id: str, _: Read, lang: Literal["ja", "en"] = "ja"):
    return ai.next_steps(conn, deal_id, lang, today=today())


class NotesIn(BaseModel):
    notes: str = Field(min_length=10)


@app.post(
    "/api/deals/{deal_id}/ai/notes",
    operation_id="ai_notes_to_update",
    tags=["ai"],
    summary="Turn meeting notes into a proposed update (nothing is saved) / 議事メモから更新案を作成（保存はしない）",
)
def api_ai_notes(deal_id: str, body: NotesIn, _: Read):
    return ai.notes_extract(conn, deal_id, body.notes, today=today())


@app.get(
    "/api/export/deals.csv",
    operation_id="export_deals_csv",
    tags=["export"],
    summary="All deals as CSV / 全案件をCSVで出力",
)
def export_csv(_: Read):
    deals = query_deals(limit=10_000)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(deals[0].keys()))
    w.writeheader()
    w.writerows(deals)
    return Response(
        buf.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=kumo_deals.csv"},
    )


@app.get(
    "/api/export/deals.xlsx",
    operation_id="export_deals_xlsx",
    tags=["export"],
    summary="All deals and activities as Excel / 全案件と活動をExcelで出力",
)
def export_xlsx(_: Read):
    from openpyxl import Workbook

    wb = Workbook()
    for title, data in (
        ("deals", query_deals(limit=10_000)),
        ("activities", db.rows(conn.execute("SELECT * FROM activity ORDER BY occurred_at"))),
        ("companies", db.rows(conn.execute("SELECT * FROM company"))),
    ):
        ws = wb.create_sheet(title)
        ws.append(list(data[0].keys()))
        for r in data:
            ws.append(list(r.values()))
    wb.remove(wb["Sheet"])
    buf = io.BytesIO()
    wb.save(buf)
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=kumo_pipeline.xlsx"},
    )


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


# ---------------------------------------------------------------- HTML pages


def stages() -> list[dict]:
    return db.rows(conn.execute("SELECT * FROM stage ORDER BY position"))


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def board(request: Request, region: str | None = None, owner: str | None = None):
    deals = query_deals(region=region, owner=owner, limit=10_000)
    cols = [{**s, "deals": [d for d in deals if d["stage"] == s["key"]]} for s in stages()]
    for c in cols:
        c["total"] = sum(d["amount_jpy"] or 0 for d in c["deals"])
        c["count"] = len(c["deals"])  # before closed columns are cut to their latest 8 cards
        if c["category"] != "open":
            c["deals"] = sorted(c["deals"], key=lambda d: d["closed_at"] or "", reverse=True)[:8]
    regions = [r[0] for r in conn.execute("SELECT DISTINCT region FROM company ORDER BY region")]
    owners = [r[0] for r in conn.execute("SELECT DISTINCT owner FROM deal ORDER BY owner")]
    return templates.TemplateResponse(
        request,
        "board.html",
        {
            "cols": cols,
            "region": region,
            "owner": owner,
            "regions": regions,
            "owners": owners,
            "today": today(),
        },
    )


@app.get("/deals/{deal_id}", response_class=HTMLResponse, include_in_schema=False)
def deal_page(request: Request, deal_id: str):
    d = api_deal(deal_id, "read")
    runs = db.rows(
        conn.execute("SELECT * FROM ai_run WHERE deal_id = ? ORDER BY created_at DESC LIMIT 10", (deal_id,))
    )
    for r in runs:
        r["output"] = json.loads(r["output"])
    return templates.TemplateResponse(request, "deal.html", {"d": d, "stages": stages(), "runs": runs})


@app.post("/deals/{deal_id}/stage", include_in_schema=False)
def deal_stage(deal_id: str, stage: Annotated[str, Form()]):
    apply_patch(deal_id, DealPatch(stage=stage))
    return RedirectResponse(f"/deals/{deal_id}", status_code=303)


@app.post("/deals/{deal_id}/ai/{kind}", response_class=HTMLResponse, include_in_schema=False)
def deal_ai(request: Request, deal_id: str, kind: str, notes: Annotated[str | None, Form()] = None):
    lang = i18n.lang_of(request)
    if kind == "summary":
        run = ai.summary(conn, deal_id, lang)
    elif kind == "next-steps":
        run = ai.next_steps(conn, deal_id, lang, today=today())
    elif kind == "notes":
        run = ai.notes_extract(conn, deal_id, notes or "", today=today())
    else:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "_ai_run.html", {"r": run, "d": {"id": deal_id}})


@app.get("/lang/{lang}", include_in_schema=False)
def set_lang(lang: Literal["en", "ja"], next: str = "/"):
    """The language toggle without JavaScript. Only local paths are allowed as `next`."""
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(i18n.COOKIE, lang, max_age=365 * 24 * 3600, samesite="lax")
    return response


@app.post("/deals/{deal_id}/ai/notes/{run_id}/apply", include_in_schema=False)
def deal_apply(deal_id: str, run_id: str, items: Annotated[list[str] | None, Form()] = None):
    run = db.row(
        conn.execute(
            "SELECT * FROM ai_run WHERE id = ? AND deal_id = ? AND kind = 'notes_extract'", (run_id, deal_id)
        )
    )
    if not run:
        raise HTTPException(404)
    out, items = json.loads(run["output"]), set(items or [])
    if "activity" in items:
        api_add_activity(
            deal_id,
            ActivityIn(kind="meeting", summary=out["activity_summary"][:80], body=out["notes"]),
            "write",
        )
    patch = DealPatch(
        stage=out["proposed_stage"] if "stage" in items else None,
        expected_close_on=out["proposed_close_on"] if "close" in items else None,
        amount_jpy=out["proposed_amount_jpy"] if "amount" in items else None,
    )
    apply_patch(deal_id, patch)
    conn.execute(
        "UPDATE ai_run SET applied_at = ? WHERE id = ?", (dt.datetime.now(dt.UTC).isoformat(), run_id)
    )
    conn.commit()
    return RedirectResponse(f"/deals/{deal_id}", status_code=303)


# ---------------------------------------------------------------------------
# Debug panel (KUMO_DEBUG=1): every call to the ai& API, from Kumo and from apps that
# use the pass-through below.

# Headers that belong to one connection and must not be forwarded.
HOP_HEADERS = {
    "host",
    "content-length",
    "connection",
    "accept-encoding",
    "transfer-encoding",
    "content-encoding",
}


EMBEDDINGS_URL = os.environ.get("KUMO_EMBEDDINGS_URL", "").rstrip("/")


def _embeddings_headers() -> dict[str, str]:
    """Extra headers for KUMO_EMBEDDINGS_URL, from KUMO_EMBEDDINGS_HEADERS (a JSON object)."""
    raw = os.environ.get("KUMO_EMBEDDINGS_HEADERS", "").strip()
    return {str(k): str(v) for k, v in json.loads(raw).items()} if raw else {}


@app.api_route("/proxy/{caller}/v1/{path:path}", methods=["GET", "POST"], include_in_schema=False)
async def llm_proxy(caller: str, path: str, request: Request):
    """Forwards an OpenAI-compatible call to ai& unchanged and logs it. Point another app's
    base URL at http://kumo:8080/proxy/<app name>/v1 to see its calls in the panel. The caller's
    own API key is forwarded; Kumo adds none. Calls are logged only when KUMO_DEBUG=1.

    With KUMO_EMBEDDINGS_URL set, /embeddings and /rerank go there instead (ai&'s embeddings
    and rerank endpoint, in private preview), with the headers in KUMO_EMBEDDINGS_HEADERS in
    place of the caller's key."""
    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_HEADERS}
    base_url = ai.BASE_URL
    if EMBEDDINGS_URL and path in ("embeddings", "rerank"):
        base_url = EMBEDDINGS_URL
        headers = {k: v for k, v in headers.items() if k.lower() != "authorization"}
        headers.update(_embeddings_headers())
    client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10))
    started = time.monotonic()
    content_type = request.headers.get("content-type", "")
    call_id = calllog.start(
        conn,
        source="proxy",
        caller=caller,
        method=request.method,
        path="/" + path,
        request_raw=body,
        request_type=content_type,
    )
    try:
        upstream = await client.send(
            client.build_request(
                request.method,
                f"{base_url}/{path}",
                params=request.query_params,
                headers=headers,
                content=body,
            ),
            stream=True,
        )
    except httpx.HTTPError as e:
        await client.aclose()
        calllog.finish(
            conn,
            call_id,
            status=502,
            started=started,
            response_raw=str(e).encode(),
            response_type="text/plain",
        )
        raise HTTPException(502, f"ai& API unreachable: {e}") from e
    chunks: list[bytes] = []

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                chunks.append(chunk)
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()
            calllog.finish(
                conn,
                call_id,
                status=upstream.status_code,
                started=started,
                response_raw=b"".join(chunks),
                response_type=upstream.headers.get("content-type", ""),
            )

    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_HEADERS}
    return StreamingResponse(relay(), status_code=upstream.status_code, headers=out_headers)


def _assembled(response: str) -> dict | None:
    """The answer the user saw, the reasoning and the tools called, from a streamed or a plain
    chat completion. None for other responses."""
    content, reasoning, tools = [], [], []
    if response.lstrip().startswith("data:"):
        for line in response.splitlines():
            if not line.startswith("data:") or line.strip() == "data: [DONE]":
                continue
            try:
                ev = json.loads(line[5:])
            except ValueError:
                continue
            for ch in ev.get("choices") or []:
                delta = ch.get("delta") or {}
                content.append(delta.get("content") or "")
                reasoning.append(delta.get("reasoning_content") or "")
                tools += [(tc.get("function") or {}).get("name") for tc in delta.get("tool_calls") or []]
    else:
        try:
            body = json.loads(response)
        except ValueError:
            return None
        choices = body.get("choices") if isinstance(body, dict) else None
        if not choices:
            return None
        msg = choices[0].get("message") or {}
        content.append(msg.get("content") or "")
        reasoning.append(msg.get("reasoning_content") or "")
        tools += [(tc.get("function") or {}).get("name") for tc in msg.get("tool_calls") or []]
    return {"content": "".join(content), "reasoning": "".join(reasoning), "tools": [t for t in tools if t]}


def _request_summary(request: str) -> dict:
    """The last thing the user asked, and how big the request was."""
    try:
        body = json.loads(request)
    except ValueError:
        return {}
    messages = body.get("messages") or [] if isinstance(body, dict) else []
    prompt = ""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        # Skip user messages that a client adds after a tool result (Open WebUI does this for images).
        if m.get("role") != "user" or (i > 0 and messages[i - 1].get("role") == "tool"):
            continue
        c = m.get("content")
        prompt = (
            c if isinstance(c, str) else " ".join(p.get("text", "") for p in c or [] if isinstance(p, dict))
        )
        break
    return {"prompt": prompt, "messages": len(messages), "tools": len(body.get("tools") or [])}


def _pretty(text: str) -> str:
    try:
        return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except ValueError:
        return text


CARD_COLUMNS = "id, created_at, source, caller, path, kind, model, stream, status, latency_ms, input_tokens, output_tokens, seq, first_seq, note"
# Open WebUI lists models often; the compact feed leaves those calls out.
FEED_WHERE = "kind != 'model list'"


def _feed_totals() -> dict:
    t = db.row(
        conn.execute(
            f"SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            f"COALESCE(SUM(output_tokens), 0) AS output_tokens FROM llm_call WHERE {FEED_WHERE}"
        )
    )
    t["models"] = db.rows(
        conn.execute(
            f"SELECT model, COUNT(*) AS calls FROM llm_call WHERE {FEED_WHERE} AND model IS NOT NULL "
            "GROUP BY model ORDER BY 2 DESC"
        )
    )
    return t


def _cursor() -> int:
    return conn.execute("SELECT COALESCE(MAX(seq), 0) FROM llm_call").fetchone()[0]


@app.get("/debug", response_class=HTMLResponse, include_in_schema=False)
def debug_page(request: Request, compact: bool = False):
    if not compact:
        return templates.TemplateResponse(request, "debug.html", {"enabled": calllog.enabled()})
    calls = db.rows(
        conn.execute(
            f"SELECT {CARD_COLUMNS} FROM llm_call WHERE {FEED_WHERE} ORDER BY first_seq DESC LIMIT 40"
        )
    )
    return templates.TemplateResponse(
        request,
        "debug_compact.html",
        {"enabled": calllog.enabled(), "calls": calls, "totals": _feed_totals(), "cursor": _cursor()},
    )


@app.get("/debug/feed", response_class=HTMLResponse, include_in_schema=False)
def debug_feed(request: Request, after: int = 0):
    """What changed since `after`: new calls go on top of the feed, finished calls replace their card."""
    changed = db.rows(
        conn.execute(
            f"SELECT {CARD_COLUMNS} FROM llm_call WHERE {FEED_WHERE} AND seq > ? ORDER BY first_seq DESC LIMIT 100",
            (after,),
        )
    )
    return templates.TemplateResponse(
        request,
        "_debug_feed.html",
        {
            "new": [c for c in changed if c["first_seq"] > after],
            "updated": [c for c in changed if c["first_seq"] <= after],
            "totals": _feed_totals(),
            "cursor": max(_cursor(), after),
        },
    )


@app.get("/debug/calls", response_class=HTMLResponse, include_in_schema=False)
def debug_calls(request: Request):
    calls = db.rows(
        conn.execute(
            "SELECT id, created_at, source, caller, path, kind, model, stream, status, latency_ms, "
            "input_tokens, output_tokens FROM llm_call ORDER BY created_at DESC LIMIT 100"
        )
    )
    totals = db.rows(
        conn.execute(
            "SELECT COALESCE(model, '-') AS model, COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
            "COALESCE(SUM(output_tokens), 0) AS output_tokens FROM llm_call GROUP BY 1 ORDER BY 2 DESC"
        )
    )
    return templates.TemplateResponse(request, "_debug_calls.html", {"calls": calls, "totals": totals})


@app.get("/debug/calls/{call_id}", response_class=HTMLResponse, include_in_schema=False)
def debug_call(request: Request, call_id: str, compact: bool = False):
    c = db.row(conn.execute("SELECT * FROM llm_call WHERE id = ?", (call_id,)))
    if not c:
        raise HTTPException(404)
    c["summary"] = _request_summary(c["request"])
    c["request"] = _pretty(c["request"])
    c["assembled"] = _assembled(c["response"])
    c["response"] = _pretty(c["response"])
    template = "_debug_call_compact.html" if compact else "_debug_call.html"
    return templates.TemplateResponse(request, template, {"c": c})


@app.post("/debug/clear", include_in_schema=False)
def debug_clear(compact: Annotated[bool, Form()] = False):
    conn.execute("DELETE FROM llm_call")
    conn.commit()
    return RedirectResponse("/debug?compact=1" if compact else "/debug", status_code=303)


@app.get("/stage", response_class=HTMLResponse, include_in_schema=False)
def stage(request: Request, webui: str | None = None):
    """For demos: Kumo and Open WebUI on the left two thirds (tabs), the live call feed on the right third.
    `webui` overrides KUMO_STAGE_WEBUI_URL, for when the browser reaches Open WebUI by another name."""
    webui = webui or os.environ.get("KUMO_STAGE_WEBUI_URL", "http://localhost:3000")
    return templates.TemplateResponse(request, "stage.html", {"webui": webui})
