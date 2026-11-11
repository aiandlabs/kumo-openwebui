import importlib
import io
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kumo import db, seed


@pytest.fixture()
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("KUMO_DB", str(tmp_path / "kumo.db"))
    monkeypatch.setenv("KUMO_TODAY", "2026-10-26")
    monkeypatch.setenv("KUMO_API_KEY", "write-key")
    monkeypatch.setenv("KUMO_READ_KEY", "read-key")
    conn = db.connect()
    seed.seed(conn)
    conn.close()
    import kumo.app

    mod = importlib.reload(kumo.app)
    return mod


def client(app, key="read-key"):
    return TestClient(app.app, headers={"Authorization": f"Bearer {key}"})


def test_seed_is_deterministic(tmp_path):
    a, b = db.connect(tmp_path / "a.db"), db.connect(tmp_path / "b.db")
    assert seed.seed(a) == seed.seed(b)
    q = "SELECT id, name, amount_jpy, stage_id FROM deal ORDER BY id"
    assert [tuple(r) for r in a.execute(q)] == [tuple(r) for r in b.execute(q)]


def test_seed_has_the_demo_story(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    seed.seed(conn)
    names = {r[0].removesuffix("株式会社") for r in conn.execute("SELECT legal_name FROM company")}
    assert not names & seed.DENY
    stuck = conn.execute(
        "SELECT COUNT(*) FROM deal d JOIN company c ON c.id = d.company_id JOIN stage s ON s.id = d.stage_id "
        "WHERE c.region = '関西' AND s.key = 'poc' AND julianday('2026-10-26') - julianday(substr(d.stage_changed_at,1,10)) > 30"
    ).fetchone()[0]
    assert stuck >= 3
    assert conn.execute("SELECT status FROM deal WHERE id = 'dl_0999'").fetchone()[0] == "open"


def test_seed_looks_like_a_real_pipeline(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    seed.seed(conn)
    names = [r[0] for r in conn.execute("SELECT legal_name FROM company")]
    for head, *_ in seed.NAME_HEADS:
        assert sum(n.startswith(head) for n in names) <= seed.MAX_PER_HEAD
    assert not any(n.startswith(("瑞穂", "鈴鹿")) for n in names)
    # Outside the story deals, most open deals moved stage in the last 30 days.
    stale, total = conn.execute(
        "SELECT SUM(julianday('2026-10-26') - julianday(substr(stage_changed_at,1,10)) > 30), COUNT(*) "
        "FROM deal WHERE status = 'open'"
    ).fetchone()
    assert stale / total < 0.4


def test_pipeline_summary_has_weighted_forecast(app):
    r = client(app, "read-key").get("/api/pipeline/summary").json()
    poc = next(s for s in r["by_stage"] if s["stage"] == "poc")
    assert poc["probability"] == 50
    assert poc["weighted_amount_jpy"] == poc["amount_jpy"] // 2


def test_api_needs_a_key(app):
    assert TestClient(app.app).get("/api/deals").status_code == 401
    assert client(app).get("/api/deals").status_code == 200


def test_search_stale_kansai_poc(app):
    r = client(app).get("/api/deals", params={"region": "関西", "stage": "poc", "stale_days": 30}).json()
    assert r["count"] >= 3
    assert all(d["days_in_stage"] >= 30 and d["region"] == "関西" for d in r["deals"])


def test_read_key_cannot_write(app):
    deal = client(app).get("/api/deals", params={"status": "open", "limit": 1}).json()["deals"][0]
    assert client(app).patch(f"/api/deals/{deal['id']}", json={"stage": "won"}).status_code == 403
    r = client(app, "write-key").patch(f"/api/deals/{deal['id']}", json={"stage": "won"})
    assert r.status_code == 200 and r.json()["status"] == "won" and r.json()["closed_at"]


def test_writes_use_the_demo_date(app):
    deal = client(app).get("/api/deals", params={"status": "open", "limit": 1}).json()["deals"][0]
    w = client(app, "write-key")
    w.post(f"/api/deals/{deal['id']}/activities", json={"kind": "call", "summary": "x"})
    r = w.patch(f"/api/deals/{deal['id']}", json={"stage": "negotiation"}).json()
    assert r["stage_changed_at"].startswith("2026-10-26") and r["days_in_stage"] == 0
    assert r["activities"][0]["occurred_at"].startswith("2026-10-26")


def test_exports(app):
    c = client(app)
    csv_text = c.get("/api/export/deals.csv").content.decode("utf-8-sig")
    assert csv_text.splitlines()[0].startswith("id,name,owner")
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(c.get("/api/export/deals.xlsx").content))
    assert set(wb.sheetnames) == {"deals", "activities", "companies"}


def test_openapi_operations_for_tools(app):
    ops = {
        op["operationId"]
        for p in client(app).get("/openapi.json").json()["paths"].values()
        for op in p.values()
    }
    assert {
        "search_deals",
        "get_deal",
        "pipeline_summary",
        "ai_deal_summary",
        "ai_next_steps",
        "export_deals_xlsx",
    } <= ops


class FakeLLM:
    def __init__(self, content):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.content = content

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )


def test_ai_summary_records_usage(app):
    from kumo import ai

    llm = FakeLLM("先方は価格とSLAで保留中です。")
    run = ai.summary(app.conn, "dl_0999", llm=llm)
    assert run["output"]["text"].startswith("先方")
    assert run["input_tokens"] == 100 and llm.calls[0]["model"] == ai.MODELS["summary"]
    assert "dl_0999" not in llm.calls[0]["messages"][1]["content"]  # context is human-readable, not ids


def test_notes_extract_uses_json_schema(app):
    from kumo import ai

    out = {
        "activity_summary": "価格合意",
        "proposed_stage": "won",
        "proposed_close_on": "2026-11-15",
        "proposed_amount_jpy": 90000000,
        "new_people": [],
        "reasoning": "先方が発注を決定",
    }
    llm = FakeLLM(json.dumps(out, ensure_ascii=False))
    run = ai.notes_extract(
        app.conn, "dl_0999", "先方部長より発注の意向。金額は9,000万円で合意。", llm=llm, today="2026-10-26"
    )
    assert llm.calls[0]["response_format"]["type"] == "json_schema"
    assert run["output"]["proposed_stage"] == "won"
    # Nothing is written to the deal until the user applies it.
    assert app.conn.execute("SELECT status FROM deal WHERE id = 'dl_0999'").fetchone()[0] == "open"


def test_board_and_deal_pages_render(app):
    c = TestClient(app.app)
    assert "Pipeline" in c.get("/").text
    assert "AI assistant" in c.get("/deals/dl_0999").text


def test_pages_are_english_by_default_and_toggle_to_japanese(app):
    c = TestClient(app.app)
    board = c.get("/").text
    assert (
        '<html lang="en" data-theme="light">' in board and "Proof of concept" in board and "Kansai" in board
    )
    # The toggle sets a cookie and goes back to the page it came from (local paths only).
    r = c.get("/lang/ja?next=/deals/dl_0999", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/deals/dl_0999"
    assert c.cookies.get("kumo_lang") == "ja"
    deal = c.get("/deals/dl_0999").text
    assert '<html lang="ja" data-theme="light">' in deal and "AI アシスタント" in deal
    assert c.get("/lang/en?next=//evil.example", follow_redirects=False).headers["location"] == "/"
    # ?lang= wins over the cookie, for links into a given language.
    assert "Pipeline" in c.get("/?lang=en").text


def test_ai_answers_in_the_page_language(app, monkeypatch):
    seen = []
    monkeypatch.setattr(
        app.ai,
        "summary",
        lambda conn, deal_id, lang="ja": (
            seen.append(lang)
            or {
                "id": "ai_x",
                "deal_id": deal_id,
                "kind": "summary",
                "model": "m",
                "input_tokens": 1,
                "output_tokens": 1,
                "output": {"text": "ok"},
                "applied_at": None,
            }
        ),
    )
    c = TestClient(app.app)
    c.post("/deals/dl_0999/ai/summary")
    c.cookies.set("kumo_lang", "ja")
    c.post("/deals/dl_0999/ai/summary")
    assert seen == ["en", "ja"]


@pytest.mark.parametrize("lang", ["en", "ja"])
def test_notes_proposal_renders_and_applies(app, monkeypatch, lang):
    # The proposal card formats "Log activity: {text}"; a `text` placeholder must not clash
    # with the translation helper's own arguments (it returned HTTP 500 in the F7 rehearsal).
    from kumo import ai

    out = {
        "activity_summary": "値引き12%で稟議再提出",
        "proposed_stage": "negotiation",
        "proposed_close_on": "2026-11-30",
        "proposed_amount_jpy": None,
        "new_people": [{"name": "山田", "title": "経理部 課長"}],
        "reasoning": "SLA合意、価格は再稟議",
    }
    real = ai.notes_extract
    monkeypatch.setattr(
        app.ai,
        "notes_extract",
        lambda conn, deal_id, notes, today=None: real(
            conn, deal_id, notes, llm=FakeLLM(json.dumps(out, ensure_ascii=False)), today=today
        ),
    )
    c = TestClient(app.app)
    c.cookies.set("kumo_lang", lang)
    r = c.post("/deals/dl_0999/ai/notes", data={"notes": "11/5に最終条件を確認。経理部の山田課長が参加。"})
    assert r.status_code == 200
    assert "値引き12%で稟議再提出" in r.text and "2026-11-30" in r.text
    run_id = app.conn.execute("SELECT id FROM ai_run WHERE kind = 'notes_extract'").fetchone()[0]
    r = c.post(f"/deals/dl_0999/ai/notes/{run_id}/apply", data={"items": ["activity", "close"]})
    assert r.status_code == 200
    assert app.conn.execute("SELECT expected_close_on FROM deal WHERE id = 'dl_0999'").fetchone()[0] == (
        "2026-11-30"
    )


SSE = (
    'data: {"model":"moonshotai/kimi-k3","choices":[{"delta":{"reasoning_content":"考え中"}}]}\n\n'
    'data: {"model":"moonshotai/kimi-k3","choices":[{"delta":{"content":"関西の案件は3件です。"}}]}\n\n'
    'data: {"model":"moonshotai/kimi-k3","choices":[],"usage":{"prompt_tokens":321,"completion_tokens":12}}\n\n'
    "data: [DONE]\n\n"
)


def test_debug_is_off_by_default(app, monkeypatch):
    import httpx

    real = httpx.AsyncClient
    upstream = httpx.MockTransport(lambda req: httpx.Response(200, json={"data": []}))
    monkeypatch.setattr(app.httpx, "AsyncClient", lambda **kw: real(transport=upstream, **kw))
    c = client(app)
    assert "Debug panel is off" in c.get("/debug").text
    # The pass-through still works, but nothing is stored.
    assert c.get("/proxy/open-webui/v1/models").json() == {"data": []}
    assert app.conn.execute("SELECT COUNT(*) FROM llm_call").fetchone()[0] == 0


def test_proxy_relays_stream_and_logs_it(app, monkeypatch):
    import httpx

    monkeypatch.setenv("KUMO_DEBUG", "1")
    seen = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=SSE.encode())

    real = httpx.AsyncClient
    monkeypatch.setattr(
        app.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(upstream), **kw)
    )
    body = {
        "model": "moonshotai/kimi-k3",
        "stream": True,
        "messages": [{"role": "user", "content": "関西は？"}, {"role": "tool", "content": "[]"}],
    }
    c = TestClient(app.app)
    r = c.post(
        "/proxy/open-webui/v1/chat/completions", json=body, headers={"Authorization": "Bearer caller-key"}
    )
    assert r.text == SSE  # the caller gets the stream unchanged
    assert seen == {"url": "https://api.aiand.com/v1/chat/completions", "auth": "Bearer caller-key"}
    row = app.conn.execute("SELECT * FROM llm_call").fetchone()
    assert (row["caller"], row["kind"], row["model"], row["stream"]) == (
        "open-webui",
        "tool round",
        "moonshotai/kimi-k3",
        1,
    )
    assert (row["input_tokens"], row["output_tokens"]) == (321, 12)
    assert "関西の案件は3件です。" in c.get(f"/debug/calls/{row['id']}").text
    assert "Open WebUI" in c.get("/debug/calls").text


def test_kumo_ai_calls_are_logged(app, monkeypatch):
    import httpx
    from openai import OpenAI

    from kumo import ai, calllog

    monkeypatch.setenv("KUMO_DEBUG", "1")
    reply = {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": ai.MODELS["summary"],
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "保留中です。"}}
        ],
        "usage": {"prompt_tokens": 200, "completion_tokens": 9, "total_tokens": 209},
    }
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=reply))
    llm = OpenAI(
        base_url=ai.BASE_URL,
        api_key="k",
        http_client=calllog.http_client(app.conn, "kumo: summary", transport),
    )
    ai.summary(app.conn, "dl_0999", llm=llm)
    row = app.conn.execute("SELECT * FROM llm_call").fetchone()
    assert (row["source"], row["caller"], row["path"], row["input_tokens"]) == (
        "kumo",
        "kumo: summary",
        "/chat/completions",
        200,
    )
    assert "dl_0999" not in row["request"] and "保留中です。" in row["response"]


def test_feed_shows_calls_in_progress_then_replaces_them(app, monkeypatch):
    import re
    import time

    from kumo import calllog

    monkeypatch.setenv("KUMO_DEBUG", "1")
    c = TestClient(app.app)
    page = c.get("/debug?compact=1").text
    cursor = int(re.search(r"/debug/feed\?after=(\d+)", page).group(1))
    body = json.dumps(
        {"model": "moonshotai/kimi-k3", "messages": [{"role": "user", "content": "関西は？"}]}
    ).encode()
    call = calllog.start(
        app.conn,
        source="proxy",
        caller="open-webui",
        method="POST",
        path="/chat/completions",
        request_raw=body,
        request_type="application/json",
    )
    feed = c.get(f"/debug/feed?after={cursor}").text
    assert 'hx-swap-oob="afterbegin:#cards"' in feed and "Running" in feed and f"card-{call}" in feed
    cursor = int(re.search(r"/debug/feed\?after=(\d+)", feed).group(1))
    reply = {
        "choices": [{"message": {"content": "3件です。", "reasoning_content": "考えた"}}],
        "usage": {"prompt_tokens": 1500, "completion_tokens": 20},
    }
    calllog.finish(
        app.conn,
        call,
        status=200,
        started=time.monotonic() - 2.5,
        response_raw=json.dumps(reply).encode(),
        response_type="application/json",
    )
    feed = c.get(f"/debug/feed?after={cursor}").text
    # The finished call replaces its own card; it is not added again.
    assert "afterbegin" not in feed and f'id="card-{call}"' in feed and "Running" not in feed
    assert "1.5k → 20" in feed and "2.5 s" in feed
    c.cookies.set("kumo_lang", "ja")
    assert "2.5 秒" in c.get(f"/debug/feed?after={cursor - 1}").text
    detail = c.get(f"/debug/calls/{call}?compact=1").text
    assert "関西は？" in detail and "3件です。" in detail and "考えた" in detail
    # Nothing changed since: only the totals and the poller come back.
    cursor = int(re.search(r"/debug/feed\?after=(\d+)", feed).group(1))
    assert "card-" not in c.get(f"/debug/feed?after={cursor}").text


def test_feed_leaves_out_model_lists(app, monkeypatch):
    from kumo import calllog

    monkeypatch.setenv("KUMO_DEBUG", "1")
    calllog.record(
        app.conn,
        source="proxy",
        caller="open-webui",
        method="GET",
        path="/models",
        status=200,
        started=0,
        request_raw=b"",
        request_type="",
        response_raw=b"{}",
        response_type="application/json",
    )
    assert "card-" not in TestClient(app.app).get("/debug?compact=1").text


def test_stage_page(app, monkeypatch):
    monkeypatch.setenv("KUMO_STAGE_WEBUI_URL", "http://localhost:3100")
    page = TestClient(app.app).get("/stage").text
    assert 'src="http://localhost:3100"' in page and 'src="/debug?compact=1"' in page


def test_stage_page_keeps_its_frames_and_brand(app):
    c = TestClient(app.app)
    page = c.get("/stage", params={"webui": "http://localhost:3600"}).text
    for frame in (
        'id="kumo" name="kumo" src="/"',
        'id="webui" name="webui" src="http://localhost:3600"',
        'id="feed"',
    ):
        assert frame in page
    assert 'data-pane="kumo"' in page and 'data-pane="webui"' in page and "kumoLang" in page
    assert "Internal business app example" in page and "/static/aiand-logo.png" in page
    assert 'data-theme="light"' in page and 'data-theme-set="dark"' in page
    for logo in ("/static/aiand-logo.png", "/static/aiand-logo-dark.png"):
        assert c.get(logo).headers["content-type"] == "image/png"
    ja = c.get("/stage", params={"lang": "ja"}).text
    assert (
        '<html lang="ja" data-theme="light">' in ja
        and "社内業務アプリの例" in ja
        and "営業 CRM" in ja
        and "まだ接続できません" in ja
    )


def test_request_summary_skips_messages_added_after_tool_results(app):
    body = {
        "messages": [
            {"role": "user", "content": "レポートを作って"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "ok"},
            {
                "role": "user",
                "content": [{"type": "text", "text": "Here are the images from the tool results above."}],
            },
        ],
        "tools": [{}, {}],
    }
    s = app._request_summary(json.dumps(body))
    assert s == {"prompt": "レポートを作って", "messages": 4, "tools": 2}


def test_proxy_sends_embeddings_and_rerank_to_the_preview_endpoint(app, monkeypatch):
    import httpx

    monkeypatch.setattr(app, "EMBEDDINGS_URL", "https://embeddings.example/v1")
    monkeypatch.setenv("KUMO_EMBEDDINGS_HEADERS", '{"X-Preview-Key": "preview-key"}')
    seen = []

    def upstream(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                str(request.url),
                request.headers.get("authorization"),
                request.headers.get("x-preview-key"),
            )
        )
        return httpx.Response(200, json={"data": []})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        app.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(upstream), **kw)
    )
    c = TestClient(app.app)
    auth = {"Authorization": "Bearer caller-key"}
    c.post(
        "/proxy/open-webui/v1/embeddings",
        json={"model": "google/embeddinggemma-2", "input": ["x"]},
        headers=auth,
    )
    c.post(
        "/proxy/open-webui/v1/rerank",
        json={"model": "BAAI/bge-reranker-v2-m3", "query": "q", "documents": ["d"]},
        headers=auth,
    )
    c.post("/proxy/open-webui/v1/chat/completions", json={"model": "m", "messages": []}, headers=auth)
    assert seen == [
        (
            "https://embeddings.example/v1/embeddings",
            None,
            "preview-key",
        ),  # the caller's key is not sent there
        ("https://embeddings.example/v1/rerank", None, "preview-key"),
        ("https://api.aiand.com/v1/chat/completions", "Bearer caller-key", None),
    ]


def test_feed_notes_describe_each_call(app, monkeypatch):
    import httpx

    from kumo import calllog, feednote

    monkeypatch.setenv("KUMO_DEBUG", "1")
    monkeypatch.setenv("AIAND_API_KEY", "k")
    asked = {}

    def ai(request: httpx.Request) -> httpx.Response:
        asked["body"] = json.loads(request.content)
        words = " ".join(["word"] * 20)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": f"EN: {words}\nJA: 関西の停滞案件を3件探した"}}]}
        )

    req = {
        "model": "meta/muse-spark-open",
        "messages": [
            {"role": "user", "content": "関西でPoCのまま止まっている案件は？"},
            {"role": "assistant", "tool_calls": [{"function": {"name": "search_deals"}}]},
            {"role": "tool", "content": '[{"name": "Kasumi Logistics"}]'},
        ],
    }
    call_id = calllog.start(
        app.conn,
        source="proxy",
        caller="open-webui",
        method="POST",
        path="/chat/completions",
        request_raw=json.dumps(req).encode(),
        request_type="application/json",
    )
    calllog.finish(
        app.conn,
        call_id,
        status=200,
        started=0,
        response_raw=b'{"choices": [{"message": {"content": "3 deals"}}]}',
        response_type="application/json",
    )
    seq_before = app.conn.execute("SELECT seq FROM llm_call WHERE id = ?", (call_id,)).fetchone()[0]
    note = feednote.write(app.conn, call_id, transport=httpx.MockTransport(ai))
    prompt = asked["body"]["messages"][0]["content"]
    assert "search_deals" in prompt and "Kasumi Logistics" in prompt and "関西でPoC" in prompt
    assert (
        len(note["en"].rstrip("…").split()) == feednote.MAX_WORDS
        and note["ja"] == "関西の停滞案件を3件探した"
    )
    row = app.conn.execute("SELECT note, seq FROM llm_call WHERE id = ?", (call_id,)).fetchone()
    assert row["seq"] > seq_before  # the feed redraws the card
    assert feednote.pick(row["note"], "ja") == "関西の停滞案件を3件探した"


def test_feed_notes_are_off_unless_asked(app, monkeypatch):
    from kumo import feednote

    monkeypatch.setenv("KUMO_DEBUG", "1")
    monkeypatch.setenv("AIAND_API_KEY", "k")
    monkeypatch.delenv("KUMO_FEED_NOTES", raising=False)
    assert not feednote.enabled()
    monkeypatch.setenv("KUMO_FEED_NOTES", "1")
    assert feednote.enabled()


def test_feed_note_digest_says_what_was_embedded():
    from kumo import feednote

    row = {
        "kind": "embeddings",
        "caller": "open-webui",
        "model": "google/embeddinggemma-2",
        "status": 200,
        "response": "",
    }
    q = feednote.digest(
        {**row, "request": json.dumps({"input": ["task: search result | query: 本社はどこ？"]})}
    )
    d = feednote.digest(
        {**row, "request": json.dumps({"input": ["title: none | text: ai& は横浜", "title: none | text: b"]})}
    )
    assert "search queries" in q and "本社はどこ？" in q and "task:" not in q
    assert "document chunks (embedded for a knowledge base): 2" in d and "title:" not in d


def test_knowledge_documents_carry_the_deal_and_its_notes(app):
    from kumo import kbsync

    lost = app.conn.execute(
        "SELECT id FROM deal WHERE status = 'lost' AND lost_reason IS NOT NULL LIMIT 1"
    ).fetchone()[0]
    name, text = kbsync.render_deal(app.conn, lost)
    assert name.startswith(f"deal-{lost}-") and name.endswith(".md")
    assert "失注理由 / Lost reason" in text and "活動履歴 / Activity" in text
    assert kbsync.render_deal(app.conn, lost) == (name, text)  # deterministic, so unchanged deals are skipped
    assert kbsync.render_deal(app.conn, "dl_nope") is None
    _, overview = kbsync.render_overview(app.conn)
    assert "Lost deals by reason" in overview and lost in overview


def test_knowledge_sync_adds_changes_and_removes(app, monkeypatch):
    import re

    import httpx

    from kumo import kbsync

    monkeypatch.setenv("KUMO_OWUI_URL", "http://owui")
    monkeypatch.setenv("KUMO_OWUI_EMAIL", "a@example.com")
    monkeypatch.setenv("KUMO_OWUI_PASSWORD", "pw")
    kb = {}  # file id -> name, the fake knowledge base
    log = []

    def owui(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v1/auths/signin":
            return httpx.Response(200, json={"token": "t"})
        assert request.headers["authorization"] == "Bearer t"
        if path == "/api/v1/knowledge/":
            return httpx.Response(200, json={"items": [{"id": "kb1", "name": "Kumo CRM"}]})
        if path == "/api/v1/knowledge/kb1/files":
            items = [{"id": i, "filename": n} for i, n in kb.items()]
            return httpx.Response(200, json={"items": items, "total": len(items)})
        if path == "/api/v1/files/":
            name = re.search(rb'filename="([^"]+)"', request.content).group(1).decode()
            fid = f"f{len(log)}"
            log.append(("upload", name))
            kb[fid] = name  # added for real by file/add below; kept here to know its name
            return httpx.Response(200, json={"id": fid, "filename": name})
        if path == "/api/v1/knowledge/kb1/file/add":
            return httpx.Response(200, json={})
        if path == "/api/v1/knowledge/kb1/file/remove":
            fid = json.loads(request.content)["file_id"]
            log.append(("remove", kb.pop(fid)))
            return httpx.Response(200, json={})
        raise AssertionError(path)

    def client():
        return kbsync.OpenWebUI(transport=httpx.MockTransport(owui))

    first = kbsync.sync(app.conn, client=client())
    deals = app.conn.execute("SELECT COUNT(*) FROM deal").fetchone()[0]
    assert first["added"] == deals + 1 and first["removed"] == 0  # every deal plus the overview
    again = kbsync.sync(app.conn, client=client())
    assert again["added"] == 0 and again["removed"] == 0  # nothing changed, nothing sent

    app.conn.execute("UPDATE deal SET amount_jpy = amount_jpy + 1 WHERE id = 'dl_0032'")
    log.clear()
    one = kbsync.sync(app.conn, ["dl_0032"], client=client())
    assert one["added"] == 2 and one["removed"] == 2  # that deal and the overview, replaced
    assert {k for k, n in log if n.startswith("deal-")} == {"upload", "remove"}
    assert all("dl_0032" in n for _, n in log if n.startswith("deal-"))


def test_board_counts_every_closed_deal_not_just_the_cards_shown(app):
    page = TestClient(app.app).get("/").text
    lost = app.conn.execute("SELECT COUNT(*) FROM deal WHERE status = 'lost'").fetchone()[0]
    assert lost > 8 and f"{lost} deals" in page and "latest 8 shown" in page
