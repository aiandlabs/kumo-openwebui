"""SQLite storage. One file, one schema, no ORM."""

import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS company (
    id TEXT PRIMARY KEY,
    legal_name TEXT NOT NULL,
    name_kana TEXT,
    name_romaji TEXT,
    corporate_number TEXT CHECK (corporate_number IS NULL OR length(corporate_number) = 13),
    industry TEXT NOT NULL,
    region TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'standard' CHECK (tier IN ('standard', 'key')),
    website TEXT
);
CREATE TABLE IF NOT EXISTS person (
    id TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES company(id),
    name_kanji TEXT NOT NULL,
    name_kana TEXT,
    name_romaji TEXT,
    title TEXT,
    email TEXT,
    phone TEXT
);
CREATE TABLE IF NOT EXISTS pipeline (
    id TEXT PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    name_ja TEXT NOT NULL,
    name_en TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stage (
    id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL REFERENCES pipeline(id),
    key TEXT NOT NULL,
    name_ja TEXT NOT NULL,
    name_en TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('open', 'won', 'lost')),
    position INTEGER NOT NULL,
    -- Chance of winning a deal in this stage, in percent. Used for the weighted forecast.
    probability INTEGER NOT NULL CHECK (probability BETWEEN 0 AND 100),
    UNIQUE (pipeline_id, key)
);
CREATE TABLE IF NOT EXISTS deal (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    company_id TEXT NOT NULL REFERENCES company(id),
    owner TEXT NOT NULL,
    stage_id TEXT NOT NULL REFERENCES stage(id),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'won', 'lost')),
    amount_jpy INTEGER CHECK (amount_jpy IS NULL OR amount_jpy >= 0),
    expected_close_on TEXT,
    created_at TEXT NOT NULL,
    stage_changed_at TEXT NOT NULL,
    closed_at TEXT,
    lost_reason TEXT CHECK (lost_reason IS NULL OR lost_reason IN
        ('price', 'timing', 'competitor', 'no_budget', 'no_decision', 'requirements')),
    CHECK ((status = 'open') = (closed_at IS NULL)),
    CHECK (lost_reason IS NULL OR status = 'lost')
);
CREATE TABLE IF NOT EXISTS activity (
    id TEXT PRIMARY KEY,
    deal_id TEXT NOT NULL REFERENCES deal(id),
    person_id TEXT REFERENCES person(id),
    kind TEXT NOT NULL CHECK (kind IN ('meeting', 'call', 'email', 'note')),
    occurred_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    body TEXT
);
CREATE TABLE IF NOT EXISTS ai_run (
    id TEXT PRIMARY KEY,
    deal_id TEXT NOT NULL REFERENCES deal(id),
    kind TEXT NOT NULL CHECK (kind IN ('summary', 'next_steps', 'notes_extract')),
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    created_at TEXT NOT NULL,
    output TEXT NOT NULL,
    applied_at TEXT
);
-- Debug log of calls to the ai& API (KUMO_DEBUG=1). See kumo/calllog.py.
CREATE TABLE IF NOT EXISTS llm_call (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('kumo', 'proxy')),
    caller TEXT NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    model TEXT,
    stream INTEGER NOT NULL,
    status INTEGER NOT NULL,  -- 0 while the call is in progress
    latency_ms INTEGER NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    request TEXT NOT NULL,
    response TEXT NOT NULL,
    -- Bumped on every insert and update, so the panel can fetch only what changed.
    seq INTEGER NOT NULL,
    first_seq INTEGER NOT NULL,
    -- One line on what the call did, {"en": ..., "ja": ...}, written afterwards by feednote.py.
    note TEXT
);
CREATE INDEX IF NOT EXISTS llm_call_created ON llm_call(created_at);
CREATE INDEX IF NOT EXISTS llm_call_seq ON llm_call(seq);
CREATE INDEX IF NOT EXISTS deal_stage ON deal(stage_id);
CREATE INDEX IF NOT EXISTS deal_company ON deal(company_id);
CREATE INDEX IF NOT EXISTS activity_deal ON activity(deal_id, occurred_at);
"""


def db_path() -> Path:
    return Path(os.environ.get("KUMO_DB", "kumo.db"))


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # The call log is disposable; drop it if it predates the columns the panel needs.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(llm_call)")}
    if cols and "seq" not in cols:
        conn.execute("DROP TABLE llm_call")
    elif cols and "note" not in cols:
        conn.execute("ALTER TABLE llm_call ADD COLUMN note TEXT")
    conn.executescript(SCHEMA)
    return conn


def rows(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


def row(cur: sqlite3.Cursor) -> dict | None:
    r = cur.fetchone()
    return dict(r) if r else None
