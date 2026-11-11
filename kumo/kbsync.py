"""Keeps a "Kumo CRM" knowledge base in Open WebUI in step with Kumo's deals.

Each deal becomes one Markdown document (company, stage, amount, owner, close date, the lost reason
and every activity note), plus one overview of the pipeline and the lost deals by reason. Open WebUI
embeds the documents itself (in the demo, with EmbeddingGemma 2 through Kumo's pass-through), so
models can search the CRM's notes with Open WebUI's own knowledge tools. Kumo's tool server is still
the way to get exact lists and numbers; the knowledge base is for "why" and "what happened" questions.

File names carry a hash of their content (deal-dl_0032-1a2b3c4d.md), so a sync compares names with
what is already in the knowledge base: unchanged deals are skipped, changed ones are replaced, and
deals that no longer exist (after a reseed) are removed. Nothing is stored on Kumo's side.

Off unless KUMO_OWUI_URL, KUMO_OWUI_EMAIL and KUMO_OWUI_PASSWORD are set. Kumo signs in to Open
WebUI as that user, which owns the knowledge base.
"""

import datetime as dt
import hashlib
import logging
import os
import re
import threading
import time

import httpx

log = logging.getLogger(__name__)
KB_NAME = "Kumo CRM"
KB_DESCRIPTION = (
    "Kumo の営業 CRM（架空のデモデータ）: 案件ごとの会社・ステージ・金額・担当・失注理由・商談メモ。"
    " Kumo sales CRM (fictional demo data): one document per deal with its notes, plus a pipeline overview."
)
LOST_REASONS = {
    "price": "価格 (price)",
    "timing": "時期 (timing)",
    "competitor": "競合 (competitor)",
    "no_budget": "予算なし (no budget)",
    "no_decision": "意思決定なし (no decision)",
    "requirements": "要件不一致 (requirements)",
}
_lock = threading.Lock()  # one sync at a time


def enabled() -> bool:
    return all(os.environ.get(k) for k in ("KUMO_OWUI_URL", "KUMO_OWUI_EMAIL", "KUMO_OWUI_PASSWORD"))


def _yen(n) -> str:
    return "未定" if n is None else f"{n:,}円"


def render_deal(conn, deal_id: str) -> tuple[str, str] | None:
    """(file name, Markdown) for one deal, or None if it does not exist."""
    d = conn.execute(
        "SELECT d.*, c.legal_name, c.name_romaji, c.industry, c.region, c.tier, s.name_ja AS stage_ja, "
        "s.name_en AS stage_en, s.probability FROM deal d JOIN company c ON c.id = d.company_id "
        "JOIN stage s ON s.id = d.stage_id WHERE d.id = ?",
        (deal_id,),
    ).fetchone()
    if d is None:
        return None
    people = conn.execute(
        "SELECT name_kanji, name_romaji, title FROM person WHERE company_id = ? ORDER BY id",
        (d["company_id"],),
    ).fetchall()
    acts = conn.execute(
        "SELECT a.occurred_at, a.kind, a.summary, a.body, p.name_kanji AS person FROM activity a "
        "LEFT JOIN person p ON p.id = a.person_id WHERE a.deal_id = ? ORDER BY a.occurred_at",
        (deal_id,),
    ).fetchall()
    lines = [
        f"# {d['legal_name']} — {d['name']}（{deal_id}）",
        "",
        f"- 案件 / Deal: {d['name']} ({deal_id})",
        f"- 会社 / Company: {d['legal_name']} ({d['name_romaji'] or ''}), {d['industry']}, {d['region']}"
        + ("、重要顧客 (key account)" if d["tier"] == "key" else ""),
        f"- ステージ / Stage: {d['stage_ja']} ({d['stage_en']}), 確度 {d['probability']}%",
        f"- 状態 / Status: {d['status']}" + (f"、クローズ日 {d['closed_at'][:10]}" if d["closed_at"] else ""),
        f"- 金額 / Amount: {_yen(d['amount_jpy'])}",
        f"- 担当 / Owner: {d['owner']}",
        f"- クローズ予定 / Expected close: {d['expected_close_on'] or '未定'}",
        f"- ステージ変更日 / Stage since: {d['stage_changed_at'][:10]}",
    ]
    if d["lost_reason"]:
        lines.append(f"- 失注理由 / Lost reason: {LOST_REASONS.get(d['lost_reason'], d['lost_reason'])}")
    if people:
        lines += ["", "## 先方担当者 / Contacts"]
        lines += [
            f"- {p['name_kanji']} ({p['name_romaji'] or ''}) {p['title'] or ''}".rstrip() for p in people
        ]
    lines += ["", "## 活動履歴 / Activity"]
    for a in acts:
        who = f"（{a['person']}）" if a["person"] else ""
        body = a["body"] if a["body"] and a["body"] != a["summary"] else ""
        lines.append(
            f"- {a['occurred_at'][:10]} [{a['kind']}] {a['summary']}{who}" + (f" — {body}" if body else "")
        )
    text = "\n".join(lines) + "\n"
    return f"deal-{deal_id}-{hashlib.sha256(text.encode()).hexdigest()[:8]}.md", text


def render_overview(conn) -> tuple[str, str]:
    """One document with the pipeline by stage and the lost deals grouped by reason."""
    by_stage = conn.execute(
        "SELECT s.name_ja, s.name_en, COUNT(d.id) AS n, COALESCE(SUM(d.amount_jpy), 0) AS amount "
        "FROM stage s LEFT JOIN deal d ON d.stage_id = s.id GROUP BY s.id ORDER BY s.position"
    ).fetchall()
    lost = conn.execute(
        "SELECT d.id, d.name, d.lost_reason, d.closed_at, d.amount_jpy, c.legal_name, c.region, c.industry "
        "FROM deal d JOIN company c ON c.id = d.company_id WHERE d.status = 'lost' ORDER BY d.closed_at DESC"
    ).fetchall()
    lines = ["# Kumo パイプライン概要 / Pipeline overview", "", "## ステージ別 / By stage"]
    lines += [f"- {s['name_ja']} ({s['name_en']}): {s['n']}件, {_yen(s['amount'])}" for s in by_stage]
    lines += ["", "## 失注案件（理由別） / Lost deals by reason"]
    for key, label in LOST_REASONS.items():
        group = [x for x in lost if x["lost_reason"] == key]
        if not group:
            continue
        lines.append(f"### {label}: {len(group)}件")
        lines += [
            f"- {x['legal_name']} {x['name']} ({x['id']}), {x['region']}, {x['industry']}, "
            f"{_yen(x['amount_jpy'])}, 失注日 {(x['closed_at'] or '')[:10]}"
            for x in group
        ]
    other = [x for x in lost if x["lost_reason"] not in LOST_REASONS]
    if other:
        lines.append(f"### 理由未記録 (no reason recorded): {len(other)}件")
        lines += [f"- {x['legal_name']} {x['name']} ({x['id']})" for x in other]
    text = "\n".join(lines) + "\n"
    return f"overview-{hashlib.sha256(text.encode()).hexdigest()[:8]}.md", text


class OpenWebUI:
    """The few Open WebUI calls the sync needs, signed in as the knowledge base's owner."""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.base = os.environ["KUMO_OWUI_URL"].rstrip("/")
        self.http = httpx.Client(timeout=120, transport=transport)
        r = self.http.post(
            f"{self.base}/api/v1/auths/signin",
            json={"email": os.environ["KUMO_OWUI_EMAIL"], "password": os.environ["KUMO_OWUI_PASSWORD"]},
        )
        r.raise_for_status()
        self.http.headers["Authorization"] = f"Bearer {r.json()['token']}"

    def _json(self, method: str, path: str, **kw):
        r = self.http.request(method, f"{self.base}{path}", **kw)
        r.raise_for_status()
        return r.json()

    def knowledge_id(self) -> str:
        listed = self._json("GET", "/api/v1/knowledge/")
        items = listed.get("items", listed) if isinstance(listed, dict) else listed
        for kb in items or []:
            if kb.get("name") == KB_NAME:
                return kb["id"]
        return self._json(
            "POST", "/api/v1/knowledge/create", json={"name": KB_NAME, "description": KB_DESCRIPTION}
        )["id"]

    def files(self, kb_id: str) -> dict[str, str]:
        """File name -> file id, for the files already in the knowledge base."""
        out, page = {}, 1
        while True:
            listed = self._json("GET", f"/api/v1/knowledge/{kb_id}/files", params={"page": page})
            items = listed.get("items", []) if isinstance(listed, dict) else listed
            for f in items:
                name = f.get("filename") or (f.get("meta") or {}).get("name")
                if name:
                    out[name] = f["id"]
            if not items or (isinstance(listed, dict) and len(out) >= listed.get("total", len(out))):
                return out
            page += 1

    def add(self, kb_id: str, name: str, text: str) -> None:
        f = self._json(
            "POST",
            "/api/v1/files/",
            params={"process": "true", "process_in_background": "false"},
            files={"file": (name, text.encode(), "text/markdown")},
        )
        self._json("POST", f"/api/v1/knowledge/{kb_id}/file/add", json={"file_id": f["id"]})

    def remove(self, kb_id: str, file_id: str) -> None:
        self._json("POST", f"/api/v1/knowledge/{kb_id}/file/remove", json={"file_id": file_id})


def sync(conn, deal_ids: list[str] | None = None, client: OpenWebUI | None = None) -> dict:
    """Brings the knowledge base up to date: all deals, or just `deal_ids` (and the overview)."""
    with _lock:
        owui = client or OpenWebUI()
        kb_id = owui.knowledge_id()
        have = owui.files(kb_id)
        ids = deal_ids or [r[0] for r in conn.execute("SELECT id FROM deal ORDER BY id")]
        want = {}
        for deal_id in ids:
            doc = render_deal(conn, deal_id)
            if doc:
                want[doc[0]] = doc[1]
        overview = render_overview(conn)
        want[overview[0]] = overview[1]

        def stale(name: str) -> bool:
            if name.startswith("overview-"):
                return True
            m = re.match(r"deal-(dl_\w+?)-[0-9a-f]{8}\.md$", name)
            # A full sync also drops deals that no longer exist; a partial one only touches its deals.
            return bool(m) and (deal_ids is None or m.group(1) in deal_ids)

        removed = [name for name in have if stale(name) and name not in want]
        added = [name for name in want if name not in have]
        for name in removed:
            owui.remove(kb_id, have[name])
        for name in added:
            owui.add(kb_id, name, want[name])
        return {
            "knowledge_id": kb_id,
            "added": len(added),
            "removed": len(removed),
            "kept": len(want) - len(added),
        }


def sync_later(conn, deal_ids: list[str] | None = None, retries: int = 0) -> None:
    """Runs sync() in the background. At startup, retries while Open WebUI is still starting."""
    if not enabled():
        return

    def run():
        for attempt in range(retries + 1):
            try:
                result = sync(conn, deal_ids)
                log.warning("knowledge base sync: %s", result)
                return
            except (httpx.HTTPError, KeyError, ValueError) as e:
                if attempt == retries:
                    log.warning("knowledge base sync failed: %s", e)
                    return
                time.sleep(10)

    threading.Thread(target=run, name=f"kbsync-{dt.datetime.now():%H%M%S}", daemon=True).start()
