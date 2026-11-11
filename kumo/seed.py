"""Generate the demo data set.

Everything here is invented. Company and person names are built from parts and checked
against a short deny-list of real company names. The data set is reproducible: the same
seed and "today" always give the same rows.

The data tells a story for the demo:
* Kyushu grows fastest in the last six months.
* Several Kansai deals have been stuck in PoC for more than 30 days.
* One key account has a large deal in negotiation that has gone quiet.
* The current quarter ends with a gap to target.
"""

import datetime as dt
import random
import sqlite3

REGIONS = ["関東", "関西", "中部", "九州", "北海道", "東北", "中国・四国"]
REGION_WEIGHT = [0.34, 0.2, 0.14, 0.12, 0.06, 0.07, 0.07]
INDUSTRIES = ["製造", "物流", "小売", "金融", "医療", "不動産", "ITサービス", "建設", "食品", "教育"]

# Parts for invented company names: (kanji, kana, romaji).
NAME_HEADS = [
    ("翠嶺", "スイレイ", "Suirei"),
    ("雲雀", "ヒバリ", "Hibari"),
    ("蒼鷹", "ソウヨウ", "Soyo"),
    ("霞", "カスミ", "Kasumi"),
    ("凪", "ナギ", "Nagi"),
    ("朱鷺", "トキ", "Toki"),
    ("楓", "カエデ", "Kaede"),
    ("白樺", "シラカバ", "Shirakaba"),
    ("琥珀", "コハク", "Kohaku"),
    ("灯", "アカリ", "Akari"),
    ("千鳥", "チドリ", "Chidori"),
    ("若葉", "ワカバ", "Wakaba"),
    ("黎明", "レイメイ", "Reimei"),
    ("葵", "アオイ", "Aoi"),
    ("颯", "ハヤテ", "Hayate"),
    ("紬", "ツムギ", "Tsumugi"),
    ("椿", "ツバキ", "Tsubaki"),
    ("萌黄", "モエギ", "Moegi"),
    ("山吹", "ヤマブキ", "Yamabuki"),
    ("柊", "ヒイラギ", "Hiiragi"),
    ("菫", "スミレ", "Sumire"),
    ("茜", "アカネ", "Akane"),
    ("皐月", "サツキ", "Satsuki"),
    ("雫", "シズク", "Shizuku"),
    ("澪", "ミオ", "Mio"),
    ("紫苑", "シオン", "Shion"),
    ("群青", "グンジョウ", "Gunjo"),
    ("桔梗", "キキョウ", "Kikyo"),
    ("朧", "オボロ", "Oboro"),
    ("若竹", "ワカタケ", "Wakatake"),
]
# Each name head is used at most this many times, so no region fills up with one name.
MAX_PER_HEAD = 2
NAME_TAILS = [
    ("ロジスティクス", "ロジスティクス", "Logistics"),
    ("精工", "セイコウ", "Seiko"),
    ("テクノ", "テクノ", "Techno"),
    ("フーズ", "フーズ", "Foods"),
    ("ホールディングス", "ホールディングス", "Holdings"),
    ("メディカル", "メディカル", "Medical"),
    ("システムズ", "システムズ", "Systems"),
    ("建設", "ケンセツ", "Kensetsu"),
    ("商事", "ショウジ", "Shoji"),
    ("工業", "コウギョウ", "Kogyo"),
    ("ラボ", "ラボ", "Lab"),
    ("リテール", "リテール", "Retail"),
    ("ネットワークス", "ネットワークス", "Networks"),
]
# Real company names that the generator must never produce.
DENY = {"瑞穂ホールディングス", "瑞穂商事", "鈴鹿工業", "瑞穂フーズ"}

SURNAMES = [
    ("佐藤", "サトウ", "Sato"),
    ("鈴木", "スズキ", "Suzuki"),
    ("高橋", "タカハシ", "Takahashi"),
    ("田中", "タナカ", "Tanaka"),
    ("伊藤", "イトウ", "Ito"),
    ("渡辺", "ワタナベ", "Watanabe"),
    ("山本", "ヤマモト", "Yamamoto"),
    ("中村", "ナカムラ", "Nakamura"),
    ("小林", "コバヤシ", "Kobayashi"),
    ("加藤", "カトウ", "Kato"),
    ("吉田", "ヨシダ", "Yoshida"),
    ("山田", "ヤマダ", "Yamada"),
    ("松本", "マツモト", "Matsumoto"),
    ("井上", "イノウエ", "Inoue"),
    ("木村", "キムラ", "Kimura"),
]
GIVEN = [
    ("健太", "ケンタ", "Kenta"),
    ("美咲", "ミサキ", "Misaki"),
    ("大輔", "ダイスケ", "Daisuke"),
    ("彩", "アヤ", "Aya"),
    ("翔", "ショウ", "Sho"),
    ("陽子", "ヨウコ", "Yoko"),
    ("拓也", "タクヤ", "Takuya"),
    ("真由", "マユ", "Mayu"),
    ("誠", "マコト", "Makoto"),
    ("恵", "メグミ", "Megumi"),
    ("亮", "リョウ", "Ryo"),
    ("由紀", "ユキ", "Yuki"),
]
TITLES = [
    "情報システム部 部長",
    "DX推進室 室長",
    "経営企画部 課長",
    "製造部 主任",
    "代表取締役",
    "物流部 マネージャー",
    "購買部 担当",
    "CTO",
]
OWNERS = ["佐々木", "前田", "岡本", "藤井", "石川"]

# (key, Japanese, English, category, win probability in percent)
STAGES = [
    ("lead", "リード", "Lead", "open", 5),
    ("initial", "初回面談", "Initial meeting", "open", 15),
    ("discovery", "ヒアリング", "Discovery", "open", 30),
    ("poc", "PoC", "Proof of concept", "open", 50),
    ("negotiation", "条件交渉", "Negotiation", "open", 75),
    ("won", "受注", "Won", "won", 100),
    ("lost", "見送り", "Lost", "lost", 0),
]
LOST_REASONS = ["price", "timing", "competitor", "no_budget", "no_decision", "requirements"]
DEAL_THEMES = [
    "社内ナレッジ検索",
    "問い合わせ自動応答",
    "議事録要約",
    "契約書レビュー",
    "需要予測",
    "コード生成支援",
    "多言語翻訳",
    "社内チャットボット",
    "帳票読み取り",
    "営業メール作成支援",
]

MEETING_NOTES = [
    "先方は{theme}の精度を重視。日本国内でデータが処理されることを確認したいとのこと。",
    "情報システム部と打ち合わせ。セキュリティチェックシートの提出を依頼された。",
    "PoCの評価基準を合意。回答の正確性と応答速度を2週間で測定する。",
    "予算は来期で確保予定。決裁者は{title}。",
    "競合製品と比較中。価格よりもサポート体制と国内データ保管を重視している。",
    "PoC結果の報告会。現場からは好評だが、稟議の時期が未定。",
    "見積もりを提示。ボリュームディスカウントの相談あり。",
]


def _id(prefix: str, n: int) -> str:
    return f"{prefix}_{n:04d}"


def seed(conn: sqlite3.Connection, *, rng_seed: int = 11, today: dt.date | None = None) -> dict:
    """Fill an empty database. Returns counts."""
    today = today or dt.date(2026, 10, 26)
    rng = random.Random(rng_seed)
    cur = conn.cursor()

    cur.execute(
        "INSERT INTO pipeline VALUES (?, ?, ?, ?)",
        ("pl_default", "default", "標準パイプライン", "Default pipeline"),
    )
    stage_ids = {}
    for pos, (key, ja, en, cat, prob) in enumerate(STAGES):
        sid = f"st_{key}"
        stage_ids[key] = sid
        cur.execute(
            "INSERT INTO stage VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sid, "pl_default", key, ja, en, cat, pos, prob),
        )

    companies = []
    used = set()
    head_count: dict[str, int] = {}
    while len(companies) < 60:
        head, tail = rng.choice(NAME_HEADS), rng.choice(NAME_TAILS)
        name = head[0] + tail[0]
        if name in used or name in DENY or head_count.get(head[0], 0) >= MAX_PER_HEAD:
            continue
        used.add(name)
        head_count[head[0]] = head_count.get(head[0], 0) + 1
        region = rng.choices(REGIONS, REGION_WEIGHT)[0]
        cid = _id("co", len(companies) + 1)
        tier = "key" if rng.random() < 0.12 else "standard"
        companies.append({"id": cid, "region": region, "tier": tier, "name": name})
        cur.execute(
            "INSERT INTO company VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cid,
                f"{name}株式会社",
                head[1] + tail[1],
                f"{head[2]} {tail[2]}",
                None,
                rng.choice(INDUSTRIES),
                region,
                tier,
                f"https://{head[2].lower()}-{tail[2].lower()}.example.jp",
            ),
        )

    people_by_company: dict[str, list[str]] = {}
    pn = 0
    for c in companies:
        for _ in range(rng.randint(1, 4)):
            pn += 1
            s, g = rng.choice(SURNAMES), rng.choice(GIVEN)
            pid = _id("pe", pn)
            people_by_company.setdefault(c["id"], []).append(pid)
            cur.execute(
                "INSERT INTO person VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pid,
                    c["id"],
                    s[0] + " " + g[0],
                    s[1] + " " + g[1],
                    f"{g[2]} {s[2]}",
                    rng.choice(TITLES),
                    f"{g[2].lower()}.{s[2].lower()}@{c['id']}.example.jp",
                    f"03-{rng.randint(1000, 9999)}-{rng.randint(1000, 9999)}",
                ),
            )

    deals = []
    an = 0
    for dn in range(1, 121):
        c = rng.choice(companies)
        created = today - dt.timedelta(days=rng.randint(5, 365))
        months_ago = (today - created).days / 30
        # Kyushu: more and bigger deals in the last six months.
        boost = 1.8 if (c["region"] == "九州" and months_ago < 6) else 1.0
        amount = int(
            rng.choice([2, 3, 4, 6, 8, 12, 18, 24, 36]) * 1_000_000 * boost * (2 if c["tier"] == "key" else 1)
        )
        age = (today - created).days
        if age > 150 and rng.random() < 0.75:
            status = rng.choices(["won", "lost"], [0.45, 0.55])[0]
        elif age > 60 and rng.random() < 0.3:
            status = rng.choices(["won", "lost"], [0.5, 0.5])[0]
        else:
            status = "open"
        if status == "open":
            stage = rng.choices(
                ["lead", "initial", "discovery", "poc", "negotiation"], [0.15, 0.2, 0.25, 0.25, 0.15]
            )[0]
            # Most open deals moved stage in the last four weeks; a few have gone quiet.
            quiet = rng.randint(29, 90) if rng.random() < 0.2 else rng.randint(0, 28)
            changed = today - dt.timedelta(days=min(quiet, age))
            closed = None
        else:
            stage = status
            changed = created + dt.timedelta(days=rng.randint(20, max(21, min(age, 160))))
            closed = changed
        # Kansai: several deals stuck in PoC.
        if c["region"] == "関西" and status == "open" and rng.random() < 0.6:
            stage = "poc"
            changed = today - dt.timedelta(days=rng.randint(32, 75))
        changed = min(changed, today)
        close_on = (changed + dt.timedelta(days=rng.randint(20, 90))) if status == "open" else changed
        did = _id("dl", dn)
        theme = rng.choice(DEAL_THEMES)
        deals.append(
            {
                "id": did,
                "company": c,
                "status": status,
                "theme": theme,
                "created": created,
                "changed": changed,
            }
        )
        cur.execute(
            "INSERT INTO deal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                did,
                f"{c['name']} {theme}",
                c["id"],
                rng.choice(OWNERS),
                stage_ids[stage],
                status,
                amount,
                close_on.isoformat(),
                created.isoformat() + "T09:00:00+09:00",
                changed.isoformat() + "T10:00:00+09:00",
                (closed.isoformat() + "T17:00:00+09:00") if closed else None,
                rng.choice(LOST_REASONS) if status == "lost" else None,
            ),
        )
        # Activities between creation and today (or close).
        end = closed or today
        span = max(1, (end - created).days)
        for _ in range(rng.randint(2, 8)):
            an += 1
            when = created + dt.timedelta(days=rng.randint(0, span))
            note = rng.choice(MEETING_NOTES).format(theme=theme, title=rng.choice(TITLES))
            kind = rng.choice(["meeting", "call", "email", "note"])
            people = people_by_company.get(c["id"]) or [None]
            cur.execute(
                "INSERT INTO activity VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    _id("ac", an),
                    did,
                    rng.choice(people),
                    kind,
                    when.isoformat() + "T14:00:00+09:00",
                    note[:40],
                    note,
                ),
            )

    # One key account with a large deal in negotiation that has gone quiet.
    key = next(c for c in companies if c["tier"] == "key")
    cur.execute(
        "INSERT INTO deal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "dl_0999",
            f"{key['name']} 全社生成AI基盤",
            key["id"],
            "佐々木",
            stage_ids["negotiation"],
            "open",
            96_000_000,
            (today + dt.timedelta(days=20)).isoformat(),
            (today - dt.timedelta(days=140)).isoformat() + "T09:00:00+09:00",
            (today - dt.timedelta(days=45)).isoformat() + "T10:00:00+09:00",
            None,
            None,
        ),
    )
    an += 1
    cur.execute(
        "INSERT INTO activity VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            _id("ac", an),
            "dl_0999",
            (people_by_company.get(key["id"]) or [None])[0],
            "meeting",
            (today - dt.timedelta(days=45)).isoformat() + "T15:00:00+09:00",
            "条件交渉：価格とSLAで保留",
            "価格とSLAの条件で先方の稟議が保留。次回日程は未定。競合他社も提案中との情報あり。",
        ),
    )
    conn.commit()
    return {"companies": len(companies), "people": pn, "deals": len(deals) + 1, "activities": an}
