"""AI features. Every model call goes to the ai& API (OpenAI-compatible)."""

import datetime as dt
import json
import os
import uuid
from pathlib import Path

from openai import OpenAI

from . import calllog

PROMPTS = Path(__file__).parent / "prompts"
BASE_URL = os.environ.get("AIAND_BASE_URL", "https://api.aiand.com/v1")
MODELS = {
    "summary": os.environ.get("KUMO_MODEL_SUMMARY", "zai-org/glm-5.3-flash"),
    "next_steps": os.environ.get("KUMO_MODEL_NEXT_STEPS", "zai-org/glm-5.3-flash"),
    "notes_extract": os.environ.get("KUMO_MODEL_NOTES", "zai-org/glm-5.3-flash"),
}

NEXT_STEPS_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "reason": {"type": "string"},
                    "due_on": {"type": "string", "description": "YYYY-MM-DD"},
                },
                "required": ["action", "reason", "due_on"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["steps"],
    "additionalProperties": False,
}

NOTES_SCHEMA = {
    "type": "object",
    "properties": {
        "activity_summary": {"type": "string", "description": "One line, 40 characters or fewer"},
        "proposed_stage": {
            "type": ["string", "null"],
            "enum": ["lead", "initial", "discovery", "poc", "negotiation", "won", "lost", None],
        },
        "proposed_close_on": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
        "proposed_amount_jpy": {"type": ["integer", "null"]},
        "new_people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "title": {"type": ["string", "null"]}},
                "required": ["name", "title"],
                "additionalProperties": False,
            },
        },
        "reasoning": {"type": "string"},
    },
    "required": [
        "activity_summary",
        "proposed_stage",
        "proposed_close_on",
        "proposed_amount_jpy",
        "new_people",
        "reasoning",
    ],
    "additionalProperties": False,
}


def client(conn=None, caller: str = "kumo") -> OpenAI:
    key = os.environ.get("AIAND_API_KEY")
    if not key:
        raise RuntimeError("AIAND_API_KEY is not set")
    if conn is not None and calllog.enabled():
        return OpenAI(base_url=BASE_URL, api_key=key, http_client=calllog.http_client(conn, caller))
    return OpenAI(base_url=BASE_URL, api_key=key)


def deal_context(conn, deal_id: str) -> str:
    d = conn.execute(
        """SELECT d.*, c.legal_name, c.region, c.industry, c.tier, s.name_ja AS stage_ja
           FROM deal d JOIN company c ON c.id = d.company_id JOIN stage s ON s.id = d.stage_id WHERE d.id = ?""",
        (deal_id,),
    ).fetchone()
    if not d:
        raise KeyError(deal_id)
    acts = conn.execute(
        "SELECT occurred_at, kind, body FROM activity WHERE deal_id = ? ORDER BY occurred_at DESC LIMIT 20",
        (deal_id,),
    ).fetchall()
    lines = [
        f"案件: {d['name']}",
        f"会社: {d['legal_name']}（{d['region']}・{d['industry']}・{d['tier']}）",
        f"ステージ: {d['stage_ja']}（{d['stage_changed_at'][:10]} から）",
        f"金額: {d['amount_jpy']:,} 円",
        f"受注予定日: {d['expected_close_on']}",
        f"担当: {d['owner']}",
        "",
        "活動履歴（新しい順）:",
    ]
    lines += [f"- {a['occurred_at'][:10]} [{a['kind']}] {a['body']}" for a in acts]
    return "\n".join(lines)


def _record(conn, deal_id, kind, model, resp, output) -> dict:
    run = {
        "id": "ai_" + uuid.uuid4().hex[:12],
        "deal_id": deal_id,
        "kind": kind,
        "model": model,
        "input_tokens": getattr(resp.usage, "prompt_tokens", None) if resp.usage else None,
        "output_tokens": getattr(resp.usage, "completion_tokens", None) if resp.usage else None,
        "created_at": dt.datetime.now(dt.UTC).isoformat(),
        "output": json.dumps(output, ensure_ascii=False),
        "applied_at": None,
    }
    conn.execute(
        "INSERT INTO ai_run VALUES (:id, :deal_id, :kind, :model, :input_tokens, :output_tokens, :created_at, :output, :applied_at)",
        run,
    )
    conn.commit()
    return {**run, "output": output}


def _prompt(name: str, lang: str) -> str:
    return (
        (PROMPTS / f"{name}.md")
        .read_text(encoding="utf-8")
        .replace("{{language}}", "日本語" if lang == "ja" else "English")
    )


def summary(conn, deal_id: str, lang: str = "ja", llm: OpenAI | None = None) -> dict:
    model = MODELS["summary"]
    resp = (llm or client(conn, "kumo: summary")).chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _prompt("summary", lang)},
            {"role": "user", "content": deal_context(conn, deal_id)},
        ],
        reasoning_effort="low",
    )
    return _record(conn, deal_id, "summary", model, resp, {"text": resp.choices[0].message.content.strip()})


def next_steps(
    conn, deal_id: str, lang: str = "ja", llm: OpenAI | None = None, today: str | None = None
) -> dict:
    model = MODELS["next_steps"]
    today = today or dt.date.today().isoformat()
    resp = (llm or client(conn, "kumo: next steps")).chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _prompt("next_steps", lang)},
            {"role": "user", "content": f"今日: {today}\n\n" + deal_context(conn, deal_id)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "next_steps", "schema": NEXT_STEPS_SCHEMA, "strict": True},
        },
        reasoning_effort="low",
    )
    return _record(conn, deal_id, "next_steps", model, resp, json.loads(resp.choices[0].message.content))


def notes_extract(
    conn, deal_id: str, notes: str, llm: OpenAI | None = None, today: str | None = None
) -> dict:
    model = MODELS["notes_extract"]
    today = today or dt.date.today().isoformat()
    resp = (llm or client(conn, "kumo: notes")).chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _prompt("notes_extract", "ja")},
            {
                "role": "user",
                "content": f"今日: {today}\n\n{deal_context(conn, deal_id)}\n\n---\n新しい議事メモ:\n{notes}",
            },
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "notes_update", "schema": NOTES_SCHEMA, "strict": True},
        },
        reasoning_effort="low",
    )
    out = json.loads(resp.choices[0].message.content)
    out["notes"] = notes
    return _record(conn, deal_id, "notes_extract", model, resp, out)
