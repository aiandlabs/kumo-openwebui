# Kumo

A small sales CRM for Japanese teams. Its AI features (deal summaries, next steps, meeting notes to structured updates) run on an [ai&](https://aiand.com) account.

Kumo is an ai& reference app. It also works with [Open WebUI](https://openwebui.com): models in Open WebUI read the pipeline through Kumo's tool server and write reports in the Open Terminal.

All data in Kumo's demo set is generated. The companies, people and documents are fictional.

Kumo is a toy, not a product. It is here to show a pattern: an internal business app whose AI features run on ai&, connected to Open WebUI so people can work with its data in chat. If you're building something like this for your own organization, fork it and make it yours. Issues and PRs are welcome.

Kumo's pages are in English or Japanese: use the EN / 日本語 switch in the header (English by default). The AI features answer in the page's language. The demo data itself is Japanese; in English, stages, regions, industries and job titles are translated and names are shown in romaji, while activity notes stay as written.

## Run the full demo

You need Docker and an ai& API key.

```sh
cp .env.example .env      # set AIAND_API_KEY; change the other keys
docker compose up
```

| Service | URL | What it is |
|---------|-----|------------|
| Kumo | http://localhost:8080 | The CRM. Seeds the demo data on first start. |
| Open WebUI | http://localhost:3000 | Chat with ai& models. The first account you create is the admin. |
| Open Terminal | (inside Open WebUI) | A sandbox where models run Python and write files. |

Open WebUI starts already connected to ai&, to Kumo's tool server and to the Open Terminal. These connections are admin-only at first: to give them to other users, add a group in Admin › Settings › Integrations › (connection) › Access, and grant the group the models it should use.

Knowledge bases: with `KUMO_OWUI_EMAIL` and `KUMO_OWUI_PASSWORD` set in `.env` (an Open WebUI account; the admin you create works), Kumo keeps a "Kumo CRM" knowledge base in Open WebUI in step with the deals' notes. To add the sales playbook too, upload the files in `demo/knowledge/` to Workspace › Knowledge.

Knowledge search uses Open WebUI's built-in embedding model by default. ai& embeddings and rerank are in private preview; with access, see `docker-compose.aiand-embeddings.yml`.

Try:

* Kumo: open a deal, click **Summary** and **Next steps**, paste meeting notes and click **Propose update from notes**.
* Open WebUI, with Tools › Kumo CRM on: 「関西でPoCのまま30日以上止まっている案件は？」
* Open WebUI, with Terminal › Open Terminal on: 「今週のパイプラインレポートを Word で作って。データは Kumo から（KUMO_URL と KUMO_READ_KEY）」
* Open WebUI, with the knowledge base synced: "Why have we been losing deals? Check the deal notes in the knowledge base too.", then "Which of those lessons apply to the deals we still have open?"
* With write access (below): "Add a short note to each of those deals with the lesson and one next step." The model asks before it writes, and the notes appear on the deals in Kumo.

Open WebUI's Kumo tools are read-only by default. To let models add notes to deals, set `WEBUI_KUMO_KEY` in `.env` to the same value as `KUMO_API_KEY` before the first start. (Open WebUI saves the connection on its first start; after that, change the key in Admin › Settings › Integrations.)

## Debug panel

With `KUMO_DEBUG=1` (on in `.env.example`), http://localhost:8080/debug shows every call to the ai& API as it happens: Kumo's own AI features and, through Kumo's pass-through at `/proxy/<app>/v1`, every call Open WebUI makes (chats, tool rounds, background tasks such as titles, and embeddings once they go through ai&). Click a call to see the request, the response, the reasoning and the token counts.

For a demo, open http://localhost:8080/stage: Kumo and Open WebUI on the left two thirds (switch with the tabs or Alt+1 / Alt+2), and the live call feed (`/debug?compact=1`) on the right third. The feed follows Kumo's language switch. Calls appear the moment they start, with a running timer, and update when they finish. Click a call to see the question, the answer and the reasoning. Kumo and Open WebUI must be on the same site (for example two ports on one host, or two subdomains of one domain), or the browser blocks Open WebUI's sign-in inside the frame.

The panel stores prompts and answers in Kumo's database (the newest 500 calls). Use it for demos and development only. In production, point Open WebUI straight at `https://api.aiand.com/v1`.

## Rehearse the demo (F7)

`demo/f7/f7.mjs` plays a 5-minute demo script on `/stage` with Playwright, against real ai& models: board, key-account deal, summary, next steps, notes to an update, Muse Spark Open with Kumo's tools, the Open Terminal Word report, and a call opened in the feed. Each run resets the demo data first. It records pass/fail and latency per step and checks every ai& call in the debug log for broken answers after tool rounds, saving the raw request and stream of any it finds.

```sh
cd demo/f7 && npm ci && npx playwright install chromium
node f7.mjs --runs 3 --out f7-results      # stack on localhost:8080 / :3000 with KUMO_DEBUG=1
```

## Run Kumo alone

```sh
uv sync
export AIAND_API_KEY=...
uv run kumo serve          # http://localhost:8080
uv run pytest
```

| Variable | Default | Meaning |
|----------|---------|---------|
| `AIAND_API_KEY` | (required for AI features) | ai& API key |
| `KUMO_API_KEY` | (none) | Bearer key that can read and write. With no keys set, the API is open. |
| `KUMO_READ_KEY` | (none) | Bearer key that can only read |
| `WEBUI_KUMO_KEY` | `KUMO_READ_KEY` | Key Open WebUI's Kumo tools use. Set it to `KUMO_API_KEY`'s value to let models add notes to deals. |
| `KUMO_DB` | `kumo.db` | SQLite file |
| `KUMO_DEBUG` | off | `1` turns on the debug panel and call log |
| `KUMO_TODAY` | today | Pin "today" so the demo data always looks the same |
| `KUMO_MODEL_SUMMARY`, `KUMO_MODEL_NEXT_STEPS`, `KUMO_MODEL_NOTES` | see `kumo/ai.py` | ai& models for each feature |
| `KUMO_OWUI_URL`, `KUMO_OWUI_EMAIL`, `KUMO_OWUI_PASSWORD` | (none) | Open WebUI account that owns the "Kumo CRM" knowledge base. All three set turns the sync on. |
| `KUMO_EMBEDDINGS_URL`, `KUMO_EMBEDDINGS_HEADERS` | (none) | ai& embeddings and rerank endpoint (private preview), and extra headers as a JSON object |

The API is documented at `/docs`.

## Contributing

Forks and PRs are welcome. Run `uv run ruff check . && uv run ruff format --check . && uv run pytest` before opening a PR; CI runs the same checks and a `docker compose` smoke test.

---

## 日本語

Kumo は日本の営業チーム向けの小さな CRM です。AI 機能（案件の要約、次の一手、議事録からの更新）は [ai&](https://aiand.com) のモデルで動きます。

Kumo は ai& のリファレンスアプリです。[Open WebUI](https://openwebui.com) とも連携します。Open WebUI のモデルが Kumo のツールサーバーからパイプラインを読み、Open Terminal でレポートを作ります。

デモデータはすべて生成されたものです。会社、人物、資料はすべて架空です。

画面は英語と日本語に対応しています。ヘッダーの EN / 日本語 で切り替えます（既定は英語）。AI 機能は画面の言語で答えます。

### デモ一式を動かす

Docker と ai& の API キーが必要です。

```sh
cp .env.example .env      # AIAND_API_KEY を設定し、他のキーも変更する
docker compose up
```

* Kumo: http://localhost:8080 （初回起動時にデモデータを投入）
* Open WebUI: http://localhost:3000 （最初に作ったアカウントが管理者）

Open WebUI は ai&、Kumo のツールサーバー、Open Terminal に接続済みの状態で起動します。最初は管理者だけが使えます。他のユーザーに使わせるには、管理者設定 › 連携 › 各接続 › アクセス でグループを追加し、そのグループに使わせるモデルも許可してください。

`.env` に `KUMO_OWUI_EMAIL` と `KUMO_OWUI_PASSWORD`（Open WebUI のアカウント）を設定すると、Kumo が案件メモを Open WebUI の「Kumo CRM」ナレッジに同期します。営業資料として `demo/knowledge/` のファイルをワークスペース › ナレッジにアップロードすることもできます。

Open WebUI の Kumo ツールは既定で読み取り専用です。モデルに案件へのメモ追加を許可するには、初回起動の前に `.env` の `WEBUI_KUMO_KEY` に `KUMO_API_KEY` と同じ値を設定します（書き込む前にモデルがユーザーに確認します）。初回起動後は 管理者設定 › 連携 で変更してください。

ナレッジ検索は既定で Open WebUI 内蔵の埋め込みモデルを使います。ai& の埋め込み・リランクはプライベートプレビュー中です。利用できる場合は `docker-compose.aiand-embeddings.yml` を参照してください。

### デバッグパネル

`KUMO_DEBUG=1` のとき、http://localhost:8080/debug に ai& API への呼び出しがすべてリアルタイムで表示されます。Kumo 自身の AI 機能と、Kumo の中継（`/proxy/<アプリ名>/v1`）を経由する Open WebUI の呼び出し（チャット、ツール呼び出し、タイトル生成などの裏の処理）です。プロンプトと応答を保存するので、デモと開発のときだけ使ってください。

デモでは http://localhost:8080/stage を開きます。左の 2/3 に Kumo と Open WebUI（タブまたは Alt+1 / Alt+2 で切り替え）、右の 1/3 に呼び出しのライブ表示が出ます。Kumo で言語を切り替えると、ライブ表示も同じ言語になります。

### Kumo だけを動かす

```sh
uv sync
export AIAND_API_KEY=...
uv run kumo serve          # http://localhost:8080
```

Kumo はおもちゃのサンプルで、製品ではありません。自社向けに同じような仕組みを作る方は、自由にフォークしてください。Issue や PR も歓迎します。

ライセンス: Apache-2.0
