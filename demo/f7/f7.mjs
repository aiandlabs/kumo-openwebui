// F7: the Nov 11 demo script, end to end on Kumo's /stage page, against real ai& models.
//
//   cd demo/f7 && npm ci && npx playwright install chromium
//   node f7.mjs --runs 3            # against the compose stack on localhost:8080 / :3000
//
// Each run resets the demo data (`kumo seed --force`, a Kumo restart, a clean terminal home),
// then plays the script: board → key-account deal → AI summary, next steps, notes to an
// update → Open WebUI: Muse Spark Open with Kumo's tools → Open Terminal Word report → open a call in
// the feed. It records pass/fail and latency per step, and checks every ai& call in Kumo's
// debug log for broken answers after tool rounds (an empty answer, or reasoning in the content).
//
// Needs KUMO_DEBUG=1 in the stack. Writes results, screenshots and the raw calls of any
// suspect answer to --out (default ./f7-results). Those files contain prompts and answers.
//
// Options (or the environment variables in brackets):
//   --kumo URL [KUMO_URL]  --webui URL [WEBUI_URL]  --project NAME [COMPOSE_PROJECT_NAME]
//   --compose-dir DIR      --model ID [F7_MODEL, default meta/muse-spark-open]
//   --runs N               --out DIR   --headed   --no-reset
//   Open WebUI admin: [F7_WEBUI_EMAIL], [F7_WEBUI_PASSWORD]; created on first use if no account exists.

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = parseArgs(process.argv.slice(2));
const KUMO = (args.kumo || process.env.KUMO_URL || 'http://localhost:8080').replace(/\/$/, '');
const WEBUI = (args.webui || process.env.WEBUI_URL || 'http://localhost:3000').replace(/\/$/, '');
const PROJECT = args.project || process.env.COMPOSE_PROJECT_NAME || '';
const COMPOSE_DIR = path.resolve(args['compose-dir'] || path.join(here, '..', '..'));
const MODEL = args.model || process.env.F7_MODEL || 'meta/muse-spark-open';
const RUNS = Number(args.runs || 1);
const OUT = path.resolve(args.out || 'f7-results');
const EMAIL = process.env.F7_WEBUI_EMAIL || 'admin@kumo.example';
const PASSWORD = process.env.F7_WEBUI_PASSWORD || 'kumo-demo-pass';

const KEY_DEAL = 'dl_0999';
const NOTES =
  '10/27 先方の情報システム部長と打ち合わせ。価格は12%の値引きで稟議を再提出する方向。SLAは99.9%で合意。' +
  '経理部の山田課長が新たに参加。11/5に最終条件を確認し、11月末の契約を目指す。';
const Q_TOOLS = '関西でPoCのまま30日以上止まっている案件は？';
const Q_REPORT = '今週のパイプラインレポートを Word で作って。データは Kumo から（KUMO_URL と KUMO_READ_KEY）';
const EXPECTED_STUCK = ['茜システムズ', '霞ロジスティクス', '朧フーズ']; // seed story, KUMO_TODAY=2026-10-26

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) continue;
    const key = a.slice(2);
    if (['headed', 'no-reset', 'self-test'].includes(key)) out[key] = true;
    else out[key] = argv[++i];
  }
  return out;
}

function compose(...cmd) {
  const env = { ...process.env };
  if (PROJECT) env.COMPOSE_PROJECT_NAME = PROJECT;
  // maxBuffer: the call log holds raw streams; one Word-report run is several MB.
  return execFileSync('docker', ['compose', ...cmd], {
    cwd: COMPOSE_DIR,
    env,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    maxBuffer: 512 * 1024 * 1024,
  });
}

async function waitHealthy(url, ms = 60_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    try {
      if ((await fetch(url)).ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`${url} not healthy after ${ms} ms`);
}

async function reset() {
  compose('exec', '-T', 'kumo', 'kumo', 'seed', '--force');
  compose('restart', 'kumo');
  compose('exec', '-T', 'open-terminal', 'sh', '-c', 'find /home/user -maxdepth 3 \\( -name "*.docx" -o -name "*.png" -o -name "*.xlsx" -o -name "*.csv" \\) -delete; touch /tmp/f7-start');
  await waitHealthy(`${KUMO}/healthz`);
}

// ---------------------------------------------------------------- calls from Kumo's debug log

function loggedCalls() {
  const py = `
import json, os, sqlite3
c = sqlite3.connect(os.environ["KUMO_DB"]); c.row_factory = sqlite3.Row
rows = c.execute("SELECT id, created_at, caller, path, kind, model, stream, status, latency_ms, input_tokens, output_tokens, request, response FROM llm_call WHERE kind != 'model list' ORDER BY seq").fetchall()
print(json.dumps([dict(r) for r in rows], ensure_ascii=False))`;
  return JSON.parse(compose('exec', '-T', 'kumo', 'python', '-c', py));
}

function parseResponse(raw) {
  const r = { content: '', reasoning: '', tools: [], finish: null, id: null };
  const take = (ch) => {
    const m = ch.delta || ch.message || {};
    r.content += m.content || '';
    r.reasoning += m.reasoning_content || m.reasoning || '';
    for (const tc of m.tool_calls || []) if (tc.function?.name) r.tools.push(tc.function.name);
    if (ch.finish_reason) r.finish = ch.finish_reason;
  };
  if (raw.trimStart().startsWith('data:')) {
    for (const line of raw.split('\n')) {
      if (!line.startsWith('data:') || line.trim() === 'data: [DONE]') continue;
      try {
        const ev = JSON.parse(line.slice(5));
        r.id = r.id || ev.id;
        (ev.choices || []).forEach(take);
      } catch {}
    }
  } else {
    try {
      const body = JSON.parse(raw);
      r.id = body.id;
      (body.choices || []).forEach(take);
    } catch {}
  }
  return r;
}

// Simplified-Chinese character forms that Japanese does not use (Japanese writes 們這麼説覚…
// or 実現問題戸様過還応認従為時対関 instead). Shared characters such as 会 (会社, 機会), 着 (到着)
// and 没 (没頭) are left out: they gave false positives in the first F7 batch. Two or more hits
// are needed. A real leak seen on 26 Oct 2026 had five.
const ZH_ONLY = /[们这么说觉吗呢让该应认为从时对关于还过实现问题户样]/g;
const zhHits = (text) => new Set(text.match(ZH_ONLY) || []).size;
const REASONING_EN = /\b(The user|Let me|I need to|I should|I'll present|Let's)\b/;

function aia2108(call) {
  if (call.status !== 200 || !['chat', 'tool round'].includes(call.kind)) return null;
  if (!call.request.includes('"tools"') && call.kind !== 'tool round') return null;
  const r = parseResponse(call.response || '');
  if (r.tools.length) return null; // a tool-call turn, not a final answer
  const content = r.content.trim();
  if (r.finish === 'stop' && !content) return { type: 'empty final answer', ...r };
  if (zhHits(content) >= 2) return { type: 'reasoning (Chinese) in content', ...r };
  if (REASONING_EN.test(content)) return { type: 'reasoning (English) in content', ...r };
  const last = content.split('\n').filter(Boolean).pop() || '';
  if (r.finish === 'stop' && last.startsWith('|') && !last.trim().endsWith('|')) return { type: 'answer cut off mid-table', ...r };
  return null;
}

// ---------------------------------------------------------------- the script

let failShot = null;

async function step(run, name, fn) {
  const t0 = Date.now();
  const s = { name, ok: false, ms: 0, note: '' };
  run.steps.push(s);
  try {
    s.note = (await fn()) || '';
    s.ok = true;
  } catch (e) {
    s.note = String(e.message || e).split('\n')[0].slice(0, 300);
    await failShot?.(name);
  }
  s.ms = Date.now() - t0;
  console.log(`  ${s.ok ? 'ok  ' : 'FAIL'} ${name} (${(s.ms / 1000).toFixed(1)} s)${s.note ? ' — ' + s.note : ''}`);
  return s.ok;
}

async function ensureWebuiAccount() {
  const r = await fetch(`${WEBUI}/api/v1/auths/signin`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  if (r.ok) return;
  const s = await fetch(`${WEBUI}/api/v1/auths/signup`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ name: 'Demo Admin', email: EMAIL, password: PASSWORD }),
  });
  if (!s.ok) throw new Error(`Open WebUI: cannot sign in or sign up as ${EMAIL} (${s.status})`);
}

// After an upgrade, Open WebUI shows its release notes once per user, over the chat. Clicking
// "Okay, Let's Go!" records them as seen; Escape alone does not. Dismiss it before the demo starts.
async function dismissWhatsNew(root) {
  const ok = root.getByRole('button', { name: /OK、始めましょう|Okay, Let's Go/ });
  try {
    await ok.waitFor({ timeout: 5_000 });
    await ok.click();
    return true;
  } catch {
    return false;
  }
}

async function waitAnswer(webui, timeout) {
  // Generating: the 停止 / Stop button shows. Done: 再生成 / Regenerate shows on the last answer.
  // 0.13 opens new files (the .docx report) in a preview tab beside the chat, which hides
  // the answer. Keep switching back to the chat tab until the answer is done.
  const last = webui.locator('.chat-assistant').last();
  const regen = webui.locator('button[aria-label="再生成"], button[aria-label="Regenerate"]').last();
  const chatTab = webui.locator('#chat-panel-main-chat');
  const deadline = Date.now() + timeout;
  for (;;) {
    if (await regen.isVisible().catch(() => false)) break;
    if (Date.now() > deadline) throw new Error(`no finished answer after ${timeout / 1000} s`);
    if ((await chatTab.count()) && (await chatTab.getAttribute('aria-selected')) === 'false') {
      await chatTab.click().catch(() => {});
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  return (await last.innerText()).trim();
}

async function playRun(browser, n) {
  const run = { run: n, started: new Date().toISOString(), steps: [], suspects: [], calls: 0 };
  const dir = path.join(OUT, `run-${n}`);
  fs.mkdirSync(dir, { recursive: true });
  console.log(`Run ${n}`);
  if (!args['no-reset']) await reset();
  const t0 = Date.now();

  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, acceptDownloads: true });
  // Sign in to Open WebUI first (setup, not part of the demo): the stage frame shares the session.
  const login = await ctx.newPage();
  await login.goto(`${WEBUI}/auth`);
  // 0.12 used type=email; 0.13 uses type=text name=email.
  await login.fill('input[name=email], input[type=email]', EMAIL);
  await login.fill('input[type=password]', PASSWORD);
  await login.keyboard.press('Enter');
  await login.waitForSelector('#chat-input', { timeout: 60_000 });
  if (await dismissWhatsNew(login)) console.log('  (dismissed Open WebUI release notes)');
  await login.close();

  const page = await ctx.newPage();
  const kumo = page.frameLocator('#kumo');
  const webui = page.frameLocator('#webui');
  const feed = page.frameLocator('#feed');
  const shot = (name) => page.screenshot({ path: path.join(dir, `${name}.png`) }).catch(() => {});
  failShot = (name) => shot(`FAILED-${name.replace(/[^\w.-]+/g, '_')}`);

  await step(run, '1. Stage and pipeline board', async () => {
    await page.goto(`${KUMO}/stage?webui=${encodeURIComponent(WEBUI)}`);
    await kumo.locator('.board .col').first().waitFor({ timeout: 15_000 });
    const cols = await kumo.locator('.board .col').count();
    await shot('1-board');
    return `${cols} stage columns`;
  });

  await step(run, '2a. Key-account deal page', async () => {
    await kumo.locator(`a.card[href="/deals/${KEY_DEAL}"]`).click();
    await kumo.locator('button', { hasText: /^(Summary|要約)$/ }).waitFor({ timeout: 15_000 });
  });

  const aiCard = async (button, timeout = 120_000) => {
    const before = await kumo.locator('#ai .ai-out').count();
    await kumo.locator('button', { hasText: button }).first().click();
    await kumo.locator('#ai .ai-out').nth(before).waitFor({ timeout });
    const card = kumo.locator('#ai .ai-out').first();
    const text = (await card.innerText()).trim();
    if (/error|エラー|失敗/i.test(text) && text.length < 200) throw new Error(text);
    return { card, text };
  };

  await step(run, '2b. AI summary', async () => {
    const { card } = await aiCard(/^(Summary|要約)$/);
    const body = (await card.locator('p').first().innerText()).trim();
    if (body.length < 40) throw new Error(`summary too short: ${JSON.stringify(body)}`);
    await shot('2b-summary');
    return (await card.locator('.model').innerText()).trim();
  });

  await step(run, '2c. Next steps', async () => {
    const { card } = await aiCard(/^(Next steps|次のアクション)$/);
    const steps = await card.locator('ol li').count();
    if (steps < 1) throw new Error('no steps');
    return `${steps} steps`;
  });

  await step(run, '2d. Meeting notes to an update, accepted', async () => {
    await kumo.locator('textarea[name=notes]').fill(NOTES);
    const { card } = await aiCard(/Propose update|議事メモから更新案/);
    const items = await card.locator('input[name=items]').count();
    if (items < 1) throw new Error('no proposed items');
    await shot('2d-proposal');
    await card.locator('button', { hasText: /Apply selected|選んだ項目を反映/ }).click();
    await kumo.locator('.acts li', { hasText: '山田' }).first().waitFor({ timeout: 15_000 }).catch(() => {});
    const applied = await kumo.locator('#ai .ai-out').first().innerText();
    if (!/Applied|反映済/.test(applied)) throw new Error('update not applied');
    return `${items} items proposed and applied`;
  });

  let chatOk = false;
  await step(run, `3. Open WebUI: ${MODEL} with Kumo's tools`, async () => {
    await page.locator('nav button[data-pane=webui]').click();
    await webui.locator('#chat-input').waitFor({ timeout: 30_000 });
    await dismissWhatsNew(webui);
    await webui.getByRole('button', { name: /選択したモデル|Selected model/ }).click();
    await webui.getByRole('option', { name: new RegExp(MODEL.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&'), 'i') }).click();
    // 0.12: an Integrations button. 0.13: Tools moved under the More (+) menu.
    const integ = webui.getByRole('button', { name: /^(連携|Integrations)$/ });
    if (await integ.count()) await integ.click();
    else await webui.locator('#input-menu-button').click();
    await webui.getByRole('menuitem', { name: /ツール|Tools/ }).click();
    const sw = webui.getByRole('menuitemcheckbox', { name: 'Kumo CRM' });
    // Kumo CRM is on by default for every model now (Open WebUI's default model metadata); tick it only if
    // it is off. Never close this menu with Escape inside Open WebUI: in 0.13.3 that cancels the change.
    if ((await sw.getAttribute('aria-checked')) !== 'true') await sw.click();
    await page.keyboard.press('Escape');
    await webui.locator('#chat-input').click();
    await page.keyboard.type(Q_TOOLS);
    await page.keyboard.press('Enter');
    const answer = await waitAnswer(webui, 240_000);
    fs.writeFileSync(path.join(dir, '3-answer.txt'), answer);
    await shot('3-answer');
    const found = EXPECTED_STUCK.filter((c) => answer.includes(c));
    if (!answer.includes('search_deals')) throw new Error('no Kumo tool call shown');
    if (found.length !== EXPECTED_STUCK.length) throw new Error(`answer names ${found.length}/3 stuck deals`);
    chatOk = true;
    return `3/3 stuck deals named`;
  });

  await step(run, '4. Open Terminal: Word report', async () => {
    // A new chat with the terminal on, as on stage.
    await webui.getByRole('link', { name: /新しいチャット|New Chat/ }).first().click();
    await webui.locator('#chat-input').waitFor({ timeout: 30_000 });
    await webui.getByRole('button', { name: /^(ターミナル|Terminal)$/ }).click();
    await webui.getByRole('menuitemradio', { name: 'Open Terminal' }).click();
    await page.keyboard.press('Escape');
    // The new chat keeps the selected model and tools.
    await webui.locator('#chat-input').click();
    await page.keyboard.type(Q_REPORT);
    await page.keyboard.press('Enter');
    const answer = await waitAnswer(webui, 600_000);
    fs.writeFileSync(path.join(dir, '4-answer.txt'), answer);
    await shot('4-report');
    // 0.13: open the report's preview tab, as on stage, and keep a screenshot of it.
    const preview = webui.getByRole('tab', { name: /\.docx/ });
    if (await preview.count()) { await preview.first().click(); await page.waitForTimeout(3000); await shot('4-preview'); }
    const files = compose('exec', '-T', 'open-terminal', 'sh', '-c', 'find /home/user -name "*.docx" -newer /tmp/f7-start').trim();
    if (!files) throw new Error('no new .docx in the terminal home folder');
    const docx = files.split('\n')[0];
    const local = path.join(dir, path.basename(docx));
    execFileSync('docker', ['cp', `${compose('ps', '-q', 'open-terminal').trim()}:${docx}`, local]);
    const listing = execFileSync('unzip', ['-l', local], { encoding: 'utf8' });
    if (!listing.includes('word/document.xml')) throw new Error(`${docx} is not a Word file`);
    const images = (listing.match(/word\/media\//g) || []).length;
    if (!answer) throw new Error('empty final answer in the UI');
    return `${path.basename(docx)}, ${images} images`;
  });

  await step(run, '5. Open a call in the feed', async () => {
    // The newest finished chat call: its question, tools, reasoning and answer.
    await feed.locator('article.card.k-chat:not(.pending) summary, article.card.k-tool-round:not(.pending) summary').first().click();
    await feed.locator('article.card details[open] .detail').first().waitFor({ timeout: 15_000 });
    await shot('5-feed');
  });

  run.ms = Date.now() - t0;
  // Every ai& call in this run, checked for broken answers after tool rounds.
  const calls = loggedCalls();
  run.calls = calls.length;
  run.callsByModel = calls.reduce((m, c) => ((m[c.model] = (m[c.model] || 0) + 1), m), {});
  run.callLatency = calls.map((c) => ({ kind: c.kind, model: c.model, ms: c.latency_ms, status: c.status, out: c.output_tokens }));
  fs.writeFileSync(path.join(dir, 'calls.json'), JSON.stringify(calls, null, 2));
  for (const c of calls) {
    const hit = aia2108(c);
    if (!hit) continue;
    const file = path.join(dir, `suspect-${c.id}.json`);
    fs.writeFileSync(file, JSON.stringify({ type: hit.type, response_id: hit.id, finish_reason: hit.finish, content: hit.content, reasoning: hit.reasoning, call: c }, null, 2));
    run.suspects.push({ call: c.id, type: hit.type, model: c.model, kind: c.kind, file });
  }
  run.pass = run.steps.every((s) => s.ok) && run.suspects.length === 0;
  await ctx.close();
  console.log(`  run ${n}: ${run.pass ? 'PASS' : 'FAIL'} in ${(run.ms / 1000).toFixed(0)} s, ${run.calls} ai& calls, ${run.suspects.length} suspect answers`);
  return run;
}

if (args['self-test'] !== undefined || process.argv.includes('--self-test')) {
  const sse = (content, reasoning = '') =>
    `data: ${JSON.stringify({ id: 't', choices: [{ delta: { reasoning_content: reasoning, content } }] })}\n\n` +
    `data: ${JSON.stringify({ id: 't', choices: [{ delta: {}, finish_reason: 'stop' }] })}\n\ndata: [DONE]\n\n`;
  const call = (content, reasoning) => ({ status: 200, kind: 'tool round', request: '{"tools":[]}', response: sse(content, reasoning) });
  const cases = [
    ['関西でPoCのまま30日以上停滞している案件は **3件** あります。\n\n| 案件 | 担当 | 金額 | 停滞日数 |感觉有点奇怪，用户用中文问了一个部分的问题。', 'reasoning (Chinese) in content'],
    ['', 'empty final answer'],
    ['合計 **2,400万円** です。\n\n| 案件 | 会社 | 担当 | 金額 | 停滞日数 |\n|---|---|---|---|---|\n| A | B | C | D | E |', null],
    ['茜システムズが **45日滞留** — 最大の受注機会。今週の商談設定が急務。到着後に着手。', null],
    ['| 案件 | 担当 |\n|---|---|\n| 茜システムズ | 岡本', 'answer cut off mid-table'],
  ];
  let bad = 0;
  for (const [content, want] of cases) {
    const got = aia2108(call(content, '考え中'))?.type ?? null;
    if (got !== want) { bad++; console.log(`FAIL want ${want} got ${got}: ${content.slice(0, 40)}`); }
  }
  console.log(bad ? `${bad} self-test case(s) failed` : `self-test: ${cases.length} cases ok`);
  process.exit(bad ? 1 : 0);
}

fs.mkdirSync(OUT, { recursive: true });
await ensureWebuiAccount();
const browser = await chromium.launch({ headless: !args.headed });
const results = [];
for (let i = 1; i <= RUNS; i++) {
  results.push(await playRun(browser, i));
  fs.writeFileSync(path.join(OUT, 'results.json'), JSON.stringify(results, null, 2));
}
await browser.close();
const passed = results.filter((r) => r.pass).length;
console.log(`\n${passed}/${results.length} runs passed. Results: ${path.join(OUT, 'results.json')}`);
process.exit(passed === results.length ? 0 : 1);
