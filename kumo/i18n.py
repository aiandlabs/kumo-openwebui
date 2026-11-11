"""English and Japanese for the web pages. English is the default.

Templates are written in English; `_("...")` returns the Japanese when the page is in Japanese.
The language comes from `?lang=` or the `kumo_lang` cookie (set by the toggle in the header).

The demo data is Japanese. In English, fixed vocabularies (stages, regions, industries, titles...)
are translated and company and person names are shown in romaji; free text such as activity notes
stays as written.
"""

from jinja2 import pass_context

LANGS = ("en", "ja")
DEFAULT = "en"
COOKIE = "kumo_lang"


def lang_of(request) -> str:
    if request is None:
        return DEFAULT
    lang = request.query_params.get("lang") or request.cookies.get(COOKIE)
    return lang if lang in LANGS else DEFAULT


JA = {
    # Header and board
    "Pipeline": "パイプライン",
    "Debug": "デバッグ",
    "ai& sample app": "ai& サンプルアプリ",
    "Internal business app example": "社内業務アプリの例",
    "a sample sales CRM": "営業 CRM のサンプル",
    "latest {n} shown": "最新 {n} 件を表示",
    "Sales CRM": "営業 CRM",
    "Fictional demo data": "デモデータ（架空）",
    "All regions": "全地域",
    "All owners": "全担当",
    "As of {date}": "基準日 {date}",
    "{n} deals · ¥{amount}": "{n}件 · ¥{amount}",
    "{n} deal · ¥{amount}": "{n}件 · ¥{amount}",
    "Key account": "重点",
    "Key": "重点",
    "{n}d": "{n}日",
    "← Pipeline": "← パイプライン",
    # Deal page
    "Company": "会社",
    "Stage": "ステージ",
    "Amount": "金額",
    "Expected close": "受注予定日",
    "Owner": "担当",
    "Lost reason": "見送り理由",
    "Contacts": "先方担当者",
    "Activity": "活動履歴",
    "AI assistant": "AI アシスタント",
    "runs on ai& models": "ai& のモデルで実行",
    "Summary": "要約",
    "Next steps": "次のアクション",
    "Working…": "生成中…",
    "Paste meeting notes to get a proposed update (nothing is saved until you confirm)": (
        "議事メモを貼り付けると、更新案を作ります（保存は確認後）"
    ),
    "Propose update from notes": "議事メモから更新案",
    "by {date}": "{date}まで",
    "Proposed update from meeting notes": "議事メモからの更新案",
    "Applied": "反映済み",
    "Log activity: {text}": "活動を記録：{text}",
    "Stage → {value}": "ステージ → {value}",
    "Expected close → {value}": "受注予定日 → {value}",
    "Amount → {value}": "金額 → {value}",
    "New contact: {value}": "新しい担当者：{value}",
    "Apply selected": "選んだ項目を反映",
    "{model} · {input} in / {output} out tokens": "{model} · 入力 {input} / 出力 {output} トークン",
    # Debug panel
    "Debug panel is off": "デバッグパネルは無効です",
    "debug-off-help": (
        "<code>KUMO_DEBUG=1</code> で起動すると、ai& API への呼び出し（Kumo 自身と、"
        "<code>/proxy/&lt;アプリ名&gt;/v1</code> を経由する Open WebUI など）をすべてここに表示します。"
        "プロンプトと応答を保存するので、デモと開発のときだけ使ってください。"
    ),
    "ai& API calls": "ai& API 呼び出し",
    "Refreshes every 2 s · keeps the latest 500": "2秒ごとに更新 · 最新500件を保存",
    "Clear": "クリア",
    "Click a row to see the request and response.": "行をクリックすると、リクエストとレスポンスを表示します。",
    "Time": "時刻",
    "Caller": "呼び出し元",
    "Kind": "種類",
    "Model": "モデル",
    "Status": "状態",
    "Latency": "時間",
    "Input": "入力",
    "Output": "出力",
    "No calls yet.": "まだ呼び出しはありません。",
    "Calls": "呼び出し",
    "Input tokens": "入力トークン",
    "Output tokens": "出力トークン",
    "via the pass-through": "プロキシ経由",
    "Endpoint": "エンドポイント",
    "Tokens": "トークン",
    "{input} in / {output} out": "入力 {input} / 出力 {output}",
    "Response (assembled from the stream)": "応答（ストリームを組み立てたもの）",
    "Tool calls:": "ツール呼び出し:",
    "Reasoning ({n} characters)": "推論（{n} 文字）",
    "(no text)": "（本文なし）",
    "Tool calls only (no text)": "ツール呼び出しのみ（本文なし）",
    "Request": "リクエスト",
    "Response (raw)": "レスポンス（生データ）",
    "Request (raw)": "リクエスト（生データ）",
    "Details": "詳細",
    "debug-feed-off": "<code>KUMO_DEBUG=1</code> で起動すると、ai& API への呼び出しがここに表示されます。",
    'Running <b class="elapsed">0.0</b> s': '実行中 <b class="elapsed">0.0</b> 秒',
    "{n} s": "{n} 秒",
    "The user asked": "ユーザーの入力",
    "{n} messages": "メッセージ {n} 件",
    "{n} tool definitions": "ツール定義 {n} 個",
    "Running…": "実行中…",
    "Tools called": "呼び出したツール",
    "Answer": "応答",
    "<b>{calls}</b> calls · <b>{input}</b> in · <b>{output}</b> out tokens": (
        "<b>{calls}</b> 回 · 入力 <b>{input}</b> · 出力 <b>{output}</b> トークン"
    ),
    "All on api.aiand.com": "すべて api.aiand.com",
    "Stage view": "ステージ",
    "Not reachable yet. Trying again…": "まだ接続できません。再試行しています…",
}
# Long texts are keyed by name; this is their English.
EN = {
    "debug-off-help": (
        "Start Kumo with <code>KUMO_DEBUG=1</code> to see every call to the ai& API here: Kumo's own and "
        "those from apps such as Open WebUI that go through <code>/proxy/&lt;app name&gt;/v1</code>. "
        "The log keeps prompts and answers, so use it only for demos and development."
    ),
    "debug-feed-off": "Start Kumo with <code>KUMO_DEBUG=1</code> to see calls to the ai& API here.",
}

# Fixed vocabularies in the demo data, Japanese → English.
VOCAB_EN = {
    "region": {
        "関東": "Kanto",
        "関西": "Kansai",
        "中部": "Chubu",
        "九州": "Kyushu",
        "北海道": "Hokkaido",
        "東北": "Tohoku",
        "中国・四国": "Chugoku-Shikoku",
    },
    "industry": {
        "製造": "Manufacturing",
        "物流": "Logistics",
        "小売": "Retail",
        "金融": "Finance",
        "医療": "Healthcare",
        "不動産": "Real estate",
        "ITサービス": "IT services",
        "建設": "Construction",
        "食品": "Food",
        "教育": "Education",
    },
    "theme": {
        "社内ナレッジ検索": "Internal knowledge search",
        "問い合わせ自動応答": "Automated inquiry replies",
        "議事録要約": "Meeting minutes summaries",
        "契約書レビュー": "Contract review",
        "需要予測": "Demand forecasting",
        "コード生成支援": "Code generation assistant",
        "多言語翻訳": "Multilingual translation",
        "社内チャットボット": "Internal chatbot",
        "帳票読み取り": "Form reading",
        "営業メール作成支援": "Sales email drafting",
        "全社生成AI基盤": "Company-wide generative AI platform",
    },
    "title": {
        "情報システム部 部長": "Head of IT",
        "DX推進室 室長": "Head of Digital Transformation",
        "経営企画部 課長": "Corporate Planning Manager",
        "製造部 主任": "Manufacturing Supervisor",
        "代表取締役": "CEO",
        "物流部 マネージャー": "Logistics Manager",
        "購買部 担当": "Procurement",
        "CTO": "CTO",
    },
    "owner": {"佐々木": "Sasaki", "前田": "Maeda", "岡本": "Okamoto", "藤井": "Fujii", "石川": "Ishikawa"},
}
# Codes stored in English, shown in Japanese.
VOCAB_JA = {
    "activity": {"meeting": "面談", "call": "電話", "email": "メール", "note": "メモ"},
    "lost_reason": {
        "price": "価格",
        "timing": "時期",
        "competitor": "競合",
        "no_budget": "予算なし",
        "no_decision": "決定せず",
        "requirements": "要件不一致",
    },
    "call_kind": {
        "chat": "チャット",
        "tool round": "ツール結果を受けて",
        "background task": "裏の処理",
        "structured output": "JSON 出力",
        "embeddings": "埋め込み",
        "rerank": "リランク",
        "model list": "モデル一覧",
    },
    "caller": {
        "kumo: summary": "CRM · 要約",
        "kumo: next steps": "CRM · 次のアクション",
        "kumo: notes": "CRM · 議事メモ",
    },
}
CALLER_EN = {
    "kumo: summary": "CRM · summary",
    "kumo: next steps": "CRM · next steps",
    "kumo: notes": "CRM · notes",
    "open-webui": "Open WebUI",
}
LOST_EN = {"no_budget": "no budget", "no_decision": "no decision"}


def gettext(lang: str, text: str, /, **kw) -> str:
    out = (JA if lang == "ja" else EN).get(text, text)
    return out.format(**kw) if kw else out


def vocab(lang: str, value, kind: str):
    """A value from the data in the page's language."""
    if value is None:
        return value
    if lang == "en":
        if kind == "caller":
            return CALLER_EN.get(value, value)
        if kind == "lost_reason":
            return LOST_EN.get(value, value)
        return VOCAB_EN.get(kind, {}).get(value, value)
    if kind == "caller" and value == "open-webui":
        return "Open WebUI"
    return VOCAB_JA.get(kind, {}).get(value, value)


def deal_name(lang: str, d: dict) -> str:
    """'翠嶺ロジスティクス 需要予測' in English is 'Suirei Logistics · Demand forecasting'."""
    if lang != "en" or not d.get("company_romaji"):
        return d["name"]
    theme = d["name"].split(" ", 1)[1] if " " in d["name"] else ""
    return f"{d['company_romaji']} · {VOCAB_EN['theme'].get(theme, theme)}" if theme else d["company_romaji"]


def deal_theme(lang: str, d: dict) -> str:
    """The deal name without the company: '需要予測', or 'Demand forecasting' in English."""
    theme = d["name"].split(" ", 1)[1] if " " in d["name"] else d["name"]
    return VOCAB_EN["theme"].get(theme, theme) if lang == "en" else theme


def yen(lang: str, amount) -> str:
    """Short amounts for the board: 1,200万 in Japanese, 12.0M in English (without the ¥)."""
    amount = amount or 0
    return f"{amount / 1_000_000:,.1f}M" if lang == "en" else f"{amount / 10_000:,.0f}万"


def paren(lang: str, text: str) -> str:
    return f"（{text}）" if lang == "ja" else f" ({text})"


def install(env) -> None:
    """Adds `_`, `lang` and the vocabulary filters to a Jinja environment."""

    def page_lang(ctx) -> str:
        return ctx.get("lang") or lang_of(ctx.get("request"))

    env.globals["_"] = pass_context(lambda ctx, text, /, **kw: gettext(page_lang(ctx), text, **kw))
    env.globals["page_lang"] = pass_context(page_lang)
    env.filters["loc"] = pass_context(lambda ctx, value, kind: vocab(page_lang(ctx), value, kind))
    env.filters["deal_name"] = pass_context(lambda ctx, d: deal_name(page_lang(ctx), d))
    env.filters["deal_theme"] = pass_context(lambda ctx, d: deal_theme(page_lang(ctx), d))
    env.filters["yen"] = pass_context(lambda ctx, n: yen(page_lang(ctx), n))
    env.filters["paren"] = pass_context(lambda ctx, text: paren(page_lang(ctx), text))
    env.filters["stage_name"] = pass_context(
        lambda ctx, s: s["name_ja"] if page_lang(ctx) == "ja" else s["name_en"]
    )
    env.filters["company_name"] = pass_context(
        lambda ctx, d: (d.get("company_romaji") or d["company"]) if page_lang(ctx) == "en" else d["company"]
    )
    env.filters["person_name"] = pass_context(
        lambda ctx, p: (
            (p.get("name_romaji") or p["name_kanji"]) if page_lang(ctx) == "en" else p["name_kanji"]
        )
    )
