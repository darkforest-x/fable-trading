"use strict";

// Run with: node --test tests/monitor/frontend_cards.test.cjs
// Contract coverage for the static signal-ledger client. Browser QA owns layout;
// this file verifies the API, card and stale-response boundaries without HTTP.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const app = fs.readFileSync(path.join(root, "yoyo/monitor/static/app.js"), "utf8");
const page = fs.readFileSync(path.join(root, "yoyo/monitor/static/index.html"), "utf8");
const cutoff = app.indexOf("  function redact(value)");
assert.ok(cutoff > 0, "test harness must exclude browser event bindings");

const rawLive = Object.freeze({
  id: "raw-live-pepe-30-1", source: "live", confirmation: "raw", timeframe_min: 30,
  signal_close_time: "2026-09-11T01:00:00Z", executable_entry_time: null,
  source_sha256: "a".repeat(64), venue: "okx", symbol: "PEPE-USDT-SWAP",
  direction: "long", risk: 0.000000094, initial_stop: 0.000002620,
  is_closed: true, entry_reference: "next_open", signal_close: 0.000002714,
});
const shortLive = Object.freeze({ ...rawLive, id: "short-live-eth-60-2", symbol: "ETH-USDT-SWAP",
  direction: "short", timeframe_min: 60, signal_close_time: "2026-09-11T02:00:00Z" });
const yoloLive = Object.freeze({ ...shortLive, id: "yolo-live-eth-60-3", confirmation: "yolo" });

function element() {
  return { disabled: false, classList: { add() {}, remove() {}, toggle() {} }, textContent: "", innerHTML: "",
    style: {}, setAttribute() {}, removeAttribute() {}, closest: () => ({ open: false }) };
}

function harness(fetchImpl) {
  const instrumented = `${app.slice(0, cutoff)}
  renderErrors = renderStatus = renderSignals = renderWatch = () => {};
  globalThis.__harness = { state, refresh, loadEarlierRawSignals, invalidateSignalQuery, performanceView, signalCardHTML, normalizeV1Event, ledgerPath };
})();`;
  const sandbox = {
    AbortController, Date, Intl, Map, Set, Promise, Number, String, Boolean, Array, Object, Math, RegExp, Error, TypeError, JSON, encodeURIComponent,
    // API's 12s abort guard is production behavior. Do not keep the Node test
    // process alive if a deliberately stale mocked request finishes after its
    // revision was discarded.
    fetch: fetchImpl, setTimeout: (...args) => { const timer = setTimeout(...args); timer.unref(); return timer; }, clearTimeout,
    document: { getElementById: () => element(), querySelectorAll: () => [] },
  };
  vm.runInNewContext(instrumented, sandbox, { filename: "app-ledger-harness.js" });
  return sandbox.__harness;
}

function jsonResponse(value) {
  return { ok: true, headers: { get: () => "application/json" }, json: async () => value };
}

function ledger(items = [], extra = {}) {
  return {
    items, total: items.length, has_more: false, as_of_ms: Date.now(),
    stats: { total: items.length, long: 0, short: 0, active: 0, closed: 0, measured_active: 0,
      measured_closed: 0, realized_r: null, floating_r: null, win_rate: null, profit: 0, loss: 0,
      breakeven: 0, missing_r: items.length },
    by_timeframe: [], ...extra,
  };
}

function query(pathname) {
  return new URL(pathname, "http://monitor.local").searchParams;
}

test("signal page keeps cards, TV entry, long/short filter, ledger stats, and source controls", () => {
  assert.match(page, /data-signal-source="live"/);
  assert.match(page, /data-signal-source="replay"/);
  assert.match(page, /id="side-filter"/);
  assert.match(page, /id="outcome-filter"/);
  assert.match(page, /id="sort-filter"/);
  assert.match(page, /aria-label="全量信号 R 统计"/);
  assert.match(page, /data-ledger-period="today"/);
  assert.match(page, /data-ledger-period="week"/);
  assert.match(page, /data-ledger-period="all"/);
  assert.match(page, /data-timeframe="15"/);
  assert.match(page, /data-timeframe="30"/);
  assert.match(page, /data-timeframe="60"/);
  assert.match(page, /data-timeframe="240"/);
  assert.match(app, /data-tradingview-action="signal"/);
  assert.match(app, /整卡打开 TradingView ↗/);
  assert.doesNotMatch(page, /detail-panel|chart-dialog|页内预览/);
});

test("ledger requests forward every filter, period, sort, source, and page parameter", async () => {
  const paths = [];
  const client = harness(async (path) => {
    paths.push(path);
    return path === "/api/status" ? jsonResponse({ now_ms: Date.now(), runtime: {} }) : jsonResponse(ledger());
  });
  const { state, refresh } = client;
  Object.assign(state, { signalSource: "replay", signalScope: "confirmed", period: "week", outcome: "loss",
    sort: "r_desc", page: 2, timeframe: "60", side: "short", search: "ETH & BTC" });
  await refresh();
  const path = paths.find((value) => value.startsWith("/api/signals?view=ledger&"));
  assert.ok(path, "signal refresh must use the ledger endpoint");
  assert.deepEqual(Object.fromEntries(query(path)), {
    view: "ledger", source: "replay", confirmation: "yolo", period: "week", outcome: "loss", sort: "r_desc",
    offset: "48", limit: "24", search: "ETH & BTC", timeframe: "1H", side: "short",
  });
});

test("R-ranked server response retains its order instead of client time sorting", async () => {
  const highR = { ...rawLive, id: "old-high-r", signal_close_time: "2026-09-10T01:00:00Z",
    performance: { status: "profit", exit_r: 8, peak_r: 9 } };
  const lowR = { ...rawLive, id: "new-low-r", signal_close_time: "2026-09-12T01:00:00Z",
    performance: { status: "loss", exit_r: -1, peak_r: 0 } };
  const client = harness(async (path) => path === "/api/status"
    ? jsonResponse({ now_ms: Date.now(), runtime: {} })
    : jsonResponse(ledger([highR, lowR], { total: 22 })));
  client.state.sort = "r_desc";
  await client.refresh();
  assert.deepEqual(client.state.directSignals.map((item) => item.id), ["old-high-r", "new-low-r"]);
  assert.equal(client.state.directSignalTotal, 22);
});

test("YOLO uses the server-projected original R when origin_close_ms is present", () => {
  const client = harness(async () => jsonResponse({}));
  const staleEmbedded = { status: "active", current_r: 0.25, peak_r: 0.4, bars_held: 2 };
  const serverProjection = { status: "profit", exit_r: 5.4, current_r: 5.4, peak_r: 7.2, bars_held: 41 };
  const yolo = client.normalizeV1Event({ ...yoloLive, origin_close_ms: Date.parse(shortLive.signal_close_time),
    performance: serverProjection, indicator: { ...shortLive, performance: staleEmbedded } }, "live");
  client.state.directSignals = [client.normalizeV1Event({ ...shortLive, performance: { status: "loss", exit_r: -1 } }, "live")];
  const view = client.performanceView(yolo);
  assert.equal(view.className, "outcome-win");
  assert.equal(view.value, "+5.40R");
  assert.equal(view.peak, "+7.20R");
  assert.match(app, /if \(Object\.hasOwn\(item, "origin_close_ms"\)\) return item\.performance \|\| null;/);
});

test("unknown outcome remains unavailable rather than being reported as zero R", () => {
  const client = harness(async () => jsonResponse({}));
  const unknown = client.normalizeV1Event(rawLive, "live");
  const view = client.performanceView(unknown);
  assert.equal(view.className, "outcome-unknown");
  assert.equal(view.value, "—");
  assert.equal(view.badge, "走势计算中");
  assert.doesNotMatch(client.signalCardHTML(unknown), /0\.00R/);
});

test("both long and short cards keep a whole-card TradingView action", () => {
  const client = harness(async () => jsonResponse({}));
  for (const row of [rawLive, shortLive]) {
    const card = client.signalCardHTML(client.normalizeV1Event(row, "live"));
    assert.match(card, /data-tradingview-action="signal"/);
    assert.match(card, /title="点击整张卡片，在本机 TradingView 打开"/);
    assert.match(card, row.direction === "short" ? /class="direction-name">空头</ : /class="direction-name">多头</);
  }
});

test("a response for a prior source/filter revision is discarded", async () => {
  let resolveLedger;
  const client = harness((path) => {
    if (path === "/api/status") return Promise.resolve(jsonResponse({ now_ms: Date.now(), runtime: {} }));
    return new Promise((resolve) => { resolveLedger = resolve; });
  });
  const pending = client.refresh();
  await Promise.resolve();
  client.state.signalSource = "replay";
  client.state.outcome = "loss";
  client.invalidateSignalQuery();
  resolveLedger(jsonResponse(ledger([{ ...rawLive, id: "stale-live" }])));
  await pending;
  assert.equal(client.state.ledger, null);
  assert.equal(client.state.directSignals.length, 0);
});

test("page changes are serialized: the next ledger request starts after the prior one settles", async () => {
  const paths = [];
  const pendingLedgers = [];
  let activeLedgers = 0;
  let maximumActiveLedgers = 0;
  const client = harness((path) => {
    paths.push(path);
    if (path === "/api/status") return Promise.resolve(jsonResponse({ now_ms: Date.now(), runtime: {} }));
    activeLedgers++;
    maximumActiveLedgers = Math.max(maximumActiveLedgers, activeLedgers);
    return new Promise((resolve) => pendingLedgers.push(() => { activeLedgers--; resolve(jsonResponse(ledger())); }));
  });
  const first = client.refresh();
  await Promise.resolve();
  client.state.page = 1;
  client.invalidateSignalQuery();
  const queued = client.refresh();
  assert.equal(paths.filter((value) => value.startsWith("/api/signals?view=ledger&")).length, 1);
  pendingLedgers.shift()();
  await first;
  await new Promise((resolve) => setTimeout(resolve, 0));
  const ledgerPaths = paths.filter((value) => value.startsWith("/api/signals?view=ledger&"));
  assert.equal(ledgerPaths.length, 2);
  assert.equal(query(ledgerPaths[0]).get("offset"), "0");
  assert.equal(query(ledgerPaths[1]).get("offset"), "24");
  assert.equal(maximumActiveLedgers, 1);
  pendingLedgers.shift()();
  await queued;
  await new Promise((resolve) => setTimeout(resolve, 0));
});

test("previous-page control returns to an earlier offset without cursor overlap", async () => {
  const paths = [];
  const client = harness(async (path) => {
    paths.push(path);
    return path === "/api/status" ? jsonResponse({ now_ms: Date.now(), runtime: {} }) : jsonResponse(ledger());
  });
  client.state.page = 2;
  client.loadEarlierRawSignals();
  await new Promise((resolve) => setTimeout(resolve, 0));
  const request = paths.find((value) => value.startsWith("/api/signals?view=ledger&"));
  assert.equal(query(request).get("offset"), "24");
  assert.doesNotMatch(request, /before_close_ms|before_id/);
});

test("client preserves display-only Bark state, Unicode search, and theme selector", () => {
  assert.match(app, /const DISPLAY_ONLY_NOTE = "仅前端 · Bark 已关闭"/);
  assert.match(app, /if \(channel === "bark" && isDisplayOnly\(item\)\) return \[DISPLAY_ONLY_NOTE, "muted"\]/);
  assert.match(app, /normalize\("NFKC"\)\.toUpperCase\(\)\.replace\(\/\[\^\\p\{L\}\\p\{N\}\]\/gu, ""\)/);
  assert.match(page, /id="theme-select"/);
  assert.match(page, /value="dark"/);
  assert.match(page, /value="light"/);
});
