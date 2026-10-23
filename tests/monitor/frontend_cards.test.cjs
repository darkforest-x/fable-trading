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

test("personal observation action copies signal identity without prices, fills, or opening TradingView", () => {
  const start = app.indexOf("  function activateRow(event, type)");
  const end = app.indexOf('  ["click", "keydown"].forEach', start);
  assert.ok(start > 0 && end > start);
  const received = [], views = [];
  const context = {
    window: { SpikeManual: { fromSignal: (value) => received.push(value) } },
    setView: (view) => views.push(view),
  };
  vm.runInNewContext(app.slice(start, end) + "\nthis.run = activateRow;", context);
  const button = { dataset: { manualSymbol: "BTC-USDT-SWAP", manualSide: "short", manualTimeframe: "1H",
    manualRef: 'signal:id1 · replay · bar_close_ms=123' } };
  const event = { type: "click", target: { closest: () => button }, preventDefault() {} };
  context.run(event, "signal");
  assert.deepEqual(JSON.parse(JSON.stringify(received)), [{ symbol: "BTC-USDT-SWAP", side: "short",
    timeframe: "1H", signal_ref: 'signal:id1 · replay · bar_close_ms=123' }]);
  assert.deepEqual(views, ["manual"]);
  context.run({ ...event, type: "keydown" }, "signal");
  assert.equal(received.length, 1);
});

function element() {
  const classes = new Set();
  return { disabled: false, classList: { add: (name) => classes.add(name), remove: (name) => classes.delete(name),
    contains: (name) => classes.has(name), toggle: (name, force) => {
      const enabled = force ?? !classes.has(name);
      if (enabled) classes.add(name); else classes.delete(name);
      return enabled;
    } }, textContent: "", innerHTML: "",
    style: {}, setAttribute() {}, removeAttribute() {}, querySelectorAll: () => [], closest: () => ({ open: false }) };
}

function harness(fetchImpl, window = undefined, layout = "cards") {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, element());
    return elements.get(id);
  };
  const instrumented = `${app.slice(0, cutoff)}
  state.view = "signals";
  state.signalLayout = ${JSON.stringify(layout)};
  const renderSignalsView = renderSignals;
  renderErrors = renderStatus = renderSignals = renderWatch = () => {};
  globalThis.__harness = { state, refresh, changeSignalPane, changeSignalLayout, renderHistoryStatistics, loadEarlierRawSignals, invalidateSignalQuery, performanceView, signalCardHTML, signalHistoryHTML, normalizeV1Event, ledgerPath, renderLedgerStats, renderSignalsView, openTradingView, remoteTradingViewURL };
})();`;
  const sandbox = {
    window: window || {}, AbortController, Date, Intl, Map, Set, Promise, Number, String, Boolean, Array, Object, Math, RegExp, Error, TypeError, JSON, encodeURIComponent,
    // API's 12s abort guard is production behavior. Do not keep the Node test
    // process alive if a deliberately stale mocked request finishes after its
    // revision was discarded.
    fetch: fetchImpl, setTimeout: (...args) => { const timer = setTimeout(...args); timer.unref(); return timer; }, clearTimeout,
    document: { getElementById: getElement, querySelectorAll: () => [] },
  };
  vm.runInNewContext(instrumented, sandbox, { filename: "app-ledger-harness.js" });
  return { ...sandbox.__harness, getElement, document: sandbox.document };
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

test('account pane pauses ledger requests and analysis restores the records layout', async () => {
  const requests=[], account=[];
  const client=harness(async path=>{requests.push(path);return jsonResponse(path==='/api/status'?{}:ledger([], {closed_statistics:{total:0}}));},
    {SpikeOKXExecution:{setActive:value=>account.push(value),refresh:()=>Promise.resolve()}});
  client.changeSignalPane('account');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(client.state.signalPane,'account');
  assert.ok(requests.every(path=>path==='/api/status'));
  assert.equal(account.at(-1),true);
  client.changeSignalPane('overview');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(query(client.ledgerPath()).get('outcome'),'closed');
  assert.equal(account.at(-1),false);
  client.changeSignalPane('records');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(client.state.signalLayout,'cards');
  assert.equal(query(client.ledgerPath()).get('outcome'),'all');
});

test('unchanged signal rows preserve DOM while changed facts replace the content', () => {
  const client=harness(async()=>jsonResponse({})), rows=client.getElement('signal-rows');
  let writes=0, html='';
  Object.defineProperty(rows,'innerHTML',{get:()=>html,set:value=>{writes++;html=value;}});
  client.state.ledger=ledger([rawLive]);
  client.state.directSignals=[client.normalizeV1Event(rawLive,'live')];
  client.renderSignalsView();const first=writes;
  client.renderSignalsView();assert.equal(writes,first);
  client.state.ledger=ledger([shortLive]);
  client.state.directSignals=[client.normalizeV1Event(shortLive,'live')];
  client.renderSignalsView();assert.equal(writes,first+1);
});

test("signal page keeps cards, filters and ledger stats without a chart-opening action", () => {
  assert.match(page, /data-signal-source="live"/);
  assert.match(page, /data-signal-source="replay"/);
  assert.match(page, /data-signal-source="legacy"/);
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
  assert.doesNotMatch(app, /data-tradingview-action="signal"/);
  assert.match(app, /整卡打开 TradingView ↗/); // Other views retain their own chart action.
  assert.doesNotMatch(page, /detail-panel|chart-dialog|页内预览/);
});

test("card version comes from the event and archived V9 is never relabelled V12.8", () => {
  const client = harness(async () => jsonResponse({}));
  const current = client.normalizeV1Event({ ...rawLive, kind: "spike_burst_v128",
    protocol: "spike-burst-v128-monitor-v1", display_scope: "warmup" });
  assert.match(client.signalCardHTML(current), /V12\.8 启动/);
  const old = client.normalizeV1Event({ ...rawLive, kind: "spike_burst_v9",
    protocol: "spike-burst-v9-monitor-v1", display_scope: "legacy" });
  assert.match(client.signalCardHTML(old), /V9 启动/);
  assert.match(client.signalCardHTML(old), /旧版归档/);
  assert.doesNotMatch(client.signalCardHTML(old), /V12\.8/);
});

test("the signal center defaults to the V13.1 retest cohort on live data only", () => {
  const client = harness(async () => jsonResponse({}));
  client.state.view = "signals";
  client.renderSignalsView();
  assert.equal(client.state.signalScope, "retest");
  assert.match(client.getElement("signal-section-title").textContent, /V13\.1 回踩确认/);
  const card = client.normalizeV1Event({ ...rawLive, kind: "spike_burst_v130_retest", confirmation: "retest",
    protocol: "spike-burst-v130-retest-monitor-v1", entry_reference: "next_open_reference_not_fill",
    anchor_close_ms: rawLive.bar_close_ms - 900000 * 6, retest_close_ms: rawLive.bar_close_ms - 900000,
    wait_bars: 6, trail_atr: 4 });
  const html = client.signalCardHTML(card);
  assert.match(html, /V13\.1 回踩确认/);
  assert.match(html, /4ATR 追踪/);
  assert.match(html, /并非账户实际成交/);
  assert.doesNotMatch(html, /V12\.8/);
});

test("entering current warmup from the legacy tab resets the displayed version context", () => {
  const client = harness(async () => jsonResponse({}));
  client.state.signalScope = "direct";
  client.state.signalSource = "legacy";
  client.state.view = "signals";
  client.renderSignalsView();
  assert.match(client.getElement("signal-section-title").textContent, /V9 旧版/);
  client.state.view = "warmup";
  client.renderSignalsView();
  assert.match(client.getElement("signal-section-title").textContent, /V12\.8/);
  assert.match(client.getElement("signal-scope-note").textContent, /H1 SMA60/);
});

test("refresh preserves an expanded TV check only for the same signal", () => {
  const client = harness(async () => jsonResponse({}));
  const rows = client.getElement("signal-rows");
  const fresh = [{ dataset: { signalCheck: "same" }, open: false }, { dataset: { signalCheck: "other" }, open: false }];
  rows.querySelectorAll = (selector) => selector.includes("[open]")
    ? [{ dataset: { signalCheck: "same" }, open: true }] : fresh;
  client.renderSignalsView();
  assert.equal(fresh[0].open, true);
  assert.equal(fresh[1].open, false);
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

test("history forces all closed outcomes and switching to cards changes its query", async () => {
  const ended = [rawLive, shortLive].map(row => ({ ...row, performance: { status: "loss", exit_r: -1 } }));
  const client = harness(async (path) => path === "/api/status" ? jsonResponse({})
    : jsonResponse(ledger(ended, { total: 61, has_more: true, closed_statistics: { total: 61 } })), undefined, "history");
  assert.match(app, /signalLayout: "cards", signalPane: "records"/);
  client.state.outcome = "profit"; // A remembered card filter cannot exclude historical losses.
  await client.refresh();
  assert.equal(client.state.signalLayout, "history");
  client.renderSignalsView();
  assert.match(client.getElement("signal-rows").innerHTML, /aria-label="历史开单列表"/);
  assert.equal(client.getElement("signal-window-note").textContent, "共 61 条 · 第 1 / 3 页");
  assert.equal(query(client.ledgerPath()).get("outcome"), "closed");
  assert.equal(client.getElement("signal-outcome-control").classList.contains("hidden"), true);
  client.state.signalLayout = "cards";
  client.renderSignalsView();
  assert.match(client.getElement("signal-rows").innerHTML, /signal-card-grid/);
  assert.equal(query(client.ledgerPath()).get("outcome"), "profit");
  assert.equal(client.getElement("signal-outcome-control").classList.contains("hidden"), false);
  assert.match(page, /信号参考 · 非账户成交/);
});

test("history uses projected entry and realized R, never close price or peak R", () => {
  const client = harness(async () => jsonResponse({}));
  const item = client.normalizeV1Event({ ...shortLive, price: 8888, performance: {
    status: "loss", entry_price: 2711.5, entry_time_ms: 1792000000000,
    exit_price: 2799, exit_time_ms: 1792010000000, exit_r: -1.24, current_r: 5, peak_r: 8,
    initial_stop: 2788, bars_held: 3, exit_reason: "raw_opposite_next_open", round_trip_cost: 0.002,
  } });
  const html = client.signalHistoryHTML([item]);
  const row = html.slice(html.indexOf('<tr class="signal-history-row">'), html.indexOf('<tr id="signal-history-detail-'));
  assert.match(row, /2,711\.50/);
  assert.match(row, /2,799\.00/);
  assert.match(row, /-1\.24R/);
  assert.doesNotMatch(row, /8,888|\+5\.00R|\+8\.00R/);
  assert.match(html, /反向信号退出/);
  assert.match(html, /0\.20%/);
});

test("history leaves missing and unknown outcomes blank while preserving genuine zero R", () => {
  const client = harness(async () => jsonResponse({}));
  const unknown = client.normalizeV1Event({ ...rawLive, performance: {
    status: "unknown", current_r: 9, peak_r: 12, entry_price: null, reason: "awaiting_next_closed_bar",
  } });
  const html = client.signalHistoryHTML([unknown]);
  const row = html.slice(0, html.indexOf('<tr id="signal-history-detail-'));
  assert.match(row, /状态未知/);
  assert.doesNotMatch(row, /0\.00R|9\.00R|12\.00R|0\.000002714/);
  assert.match(html, /等待入场参考/);
  for (const [status, extra, expected] of [
    ["active", { current_r: 0, exit_price: 9876, exit_r: 9 }, "运行中 · 浮动"],
    ["breakeven", { exit_r: 0 }, "保本退出"],
    ["profit", { exit_r: null, current_r: 5 }, "盈利退出"],
  ]) {
    const result = client.signalHistoryHTML([{ ...unknown, performance: { status, ...extra } }]);
    const first = result.slice(0, result.indexOf('<tr id="signal-history-detail-'));
    assert.match(first, new RegExp(expected));
    if (status === "profit") assert.doesNotMatch(first, /0\.00R|5\.00R/);
    else assert.match(first, /0\.00R/);
    if (status === "active") assert.doesNotMatch(first, /9,876|9\.00R/);
  }
});

test("history details survive refresh by identity but clear on a new query", () => {
  const client = harness(async () => jsonResponse({}), undefined, "history");
  const item = client.normalizeV1Event(rawLive);
  client.state.directSignals = [item];
  client.state.ledger = ledger([item]);
  client.state.signalDetailId = item.id;
  for (let i = 0; i < 2; i++) {
    client.renderSignalsView();
    assert.match(client.getElement("signal-rows").innerHTML, /aria-expanded="true"/);
  }
  client.invalidateSignalQuery();
  assert.equal(client.state.signalDetailId, null);
  client.renderSignalsView();
  assert.equal(client.getElement("signal-rows").innerHTML, "");
});

test("history statistics use the server total, preserve zeroes and never infer from the visible page", () => {
  const client = harness(async () => jsonResponse({}), undefined, "history");
  client.state.ledger = ledger([], { closed_statistics: { total: 2003, measured: 2003, net_r: 0,
    average_r: 0, win_rate: 0, profit_factor: 0, payoff_ratio: null, max_drawdown_r: 3,
    max_consecutive_wins: 0, max_consecutive_losses: null, average_holding_ms: 60000,
    measured_holding: 2003, wins: 0, losses: 0, breakeven: 2003, missing_r: 0,
    by_timeframe: [{ label: "15m", total: 2003, wins: 0, losses: 0, breakeven: 2003, net_r: 0, average_r: 0 }] } });
  client.renderHistoryStatistics();
  const html = client.getElement("history-statistics").innerHTML;
  assert.match(html, /2,003 笔/);
  assert.match(html, /0\.00R/);
  assert.match(html, /0\.0%/);
  assert.match(html, /3\.00R/);
  assert.match(html, /按退出记录日/);
  assert.doesNotMatch(html, /Infinity|NaN|undefined/);
});

test("history rejects an unfiltered or old response instead of displaying active records", async () => {
  const client = harness(async (path) => path === "/api/status" ? jsonResponse({})
    : jsonResponse(ledger([{ ...rawLive, performance: { status: "active" } }], { closed_statistics: {} })), undefined, "history");
  await client.refresh();
  assert.equal(client.state.ledger, null);
  assert.match(client.state.errors.directSignals, /统计数据格式/);
});

test("switching history and cards clears old results and discards a late old response", async () => {
  const pending = [];
  const client = harness(path => path === "/api/status" ? Promise.resolve(jsonResponse({}))
    : new Promise(resolve => pending.push({ path, resolve })), undefined, "history");
  const first = client.refresh();
  client.changeSignalLayout("cards");
  assert.equal(client.state.page, 0);
  assert.equal(client.state.ledger, null);
  pending[0].resolve(jsonResponse(ledger([], { closed_statistics: { total: 0 } })));
  await first;
  assert.equal(query(pending[1].path).get("outcome"), "all");
  assert.equal(client.state.ledger, null);
  pending[1].resolve(jsonResponse(ledger([rawLive])));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(client.state.directSignals.length, 1);
});

test("history escapes stored identities and has no TradingView action", () => {
  const client = harness(async () => jsonResponse({}));
  const html = client.signalHistoryHTML([client.normalizeV1Event({ ...rawLive,
    id: '\"><img src=x onerror=alert(1)>', symbol: '<svg onload=alert(1)>',
  })]);
  assert.doesNotMatch(html, /<img|<svg onload/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /data-tradingview-action="signal"/);
  assert.doesNotMatch(html, /card-primary-action|onclick=/);
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
  assert.equal(view.badge, "仅入场参考");
  assert.doesNotMatch(client.signalCardHTML(unknown), /0\.00R/);
});

test("untracked current cohort shows unknown counts even with an empty page", () => {
  const client = harness(async () => jsonResponse({}));
  const stats = { ...ledger().stats, total: 62, long: 16, short: 46, unknown: 62, missing_r: 62 };
  client.state.status = { protocol: "spike-burst-v128-yolo-confirmation-v1" };
  client.state.ledger = ledger([], { total: 62, stats, by_timeframe: [{ ...stats, timeframe: "15m" }] });
  client.renderLedgerStats();
  assert.equal(client.getElement("stats-total").textContent, "62");
  assert.equal(client.getElement("stats-availability").classList.contains("hidden"), false);
  assert.match(client.getElement("stats-availability").textContent, /62 条 SPIKE.*尚未跟踪持仓与退出/);
  assert.match(client.getElement("stats-side-note").textContent, /多 16 · 空 46 · 状态未知 62/);
  assert.match(client.getElement("stats-active-note").textContent, /持仓状态未知/);
  assert.match(client.getElement("stats-closed-note").textContent, /退出状态未知/);
  assert.doesNotMatch(client.getElement("stats-win-note").textContent, /盈利 0/);
  assert.equal(client.getElement("stats-winrate").textContent, "—");
  assert.match(client.getElement("stats-timeframes").innerHTML, /<td>62<\/td><td>—<\/td><td>—<\/td><td>62<\/td>/);
});

test("mixed outcomes retain measured R and disclose unknown observations", () => {
  const client = harness(async () => jsonResponse({}));
  const stats = { ...ledger().stats, total: 4, active: 1, closed: 2, unknown: 1, missing_r: 1,
    long: 2, short: 2, measured_active: 1, measured_closed: 2, realized_r: 2, floating_r: -0.5,
    profit: 1, loss: 1, win_rate: 0.5 };
  client.state.ledger = ledger([], { stats, by_timeframe: [{ ...stats, timeframe: "1H" }] });
  client.renderLedgerStats();
  assert.match(client.getElement("stats-availability").textContent, /1 条信号的持仓或退出状态未知/);
  assert.equal(client.getElement("stats-realized").textContent, "+2.00R");
  assert.equal(client.getElement("stats-floating").textContent, "-0.50R");
  assert.equal(client.getElement("stats-winrate").textContent, "50.0%");
  assert.equal(client.getElement("stats-closed-note").textContent, "2 笔有 R / 2 笔已结束");
  assert.match(client.getElement("stats-timeframes").innerHTML, /<td>4<\/td><td>1<\/td><td>2<\/td><td>1<\/td>/);
});

test("an empty filter clears the unknown warning without implying pending computation", () => {
  const client = harness(async () => jsonResponse({}));
  client.state.ledger = ledger([], { stats: { ...ledger().stats, total: 1, unknown: 1 } });
  client.renderLedgerStats();
  client.state.ledger = ledger();
  client.renderLedgerStats();
  assert.equal(client.getElement("stats-availability").classList.contains("hidden"), true);
  assert.equal(client.getElement("stats-active-note").textContent, "当前筛选无信号");
  assert.equal(client.getElement("stats-total").textContent, "0");
  assert.match(page, /value="unknown">等待 \/ 未知/);
  assert.doesNotMatch(page, /value="unknown">等待计算/);
});

test("both long and short cards retain direction without a whole-card action", () => {
  const client = harness(async () => jsonResponse({}));
  for (const row of [rawLive, shortLive]) {
    const card = client.signalCardHTML(client.normalizeV1Event(row, "live"));
    assert.doesNotMatch(card, /data-tradingview-action|card-primary-action/);
    assert.doesNotMatch(card, /信号 K 线|card-footer|notification-stack|data-manual-observe/);
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

test("client preserves display-only Bark state, Unicode search, and the approved glass appearance", () => {
  assert.match(app, /const DISPLAY_ONLY_NOTE = "仅前端 · Bark 已关闭"/);
  assert.match(app, /if \(channel === "bark" && isDisplayOnly\(item\)\) return \[DISPLAY_ONLY_NOTE, "muted"\]/);
  assert.match(app, /normalize\("NFKC"\)\.toUpperCase\(\)\.replace\(\/\[\^\\p\{L\}\\p\{N\}\]\/gu, ""\)/);
  assert.match(page, /id="theme-select"/);
  assert.match(page, /value="light"/);
  assert.match(page, /data-design="apple-glass"/);
  assert.doesNotMatch(page, /value="dark"/);
});


test("public workspace opens charts on the visitor device without contacting the Mac desktop", async () => {
  const opens = [], requests = [];
  const h = harness(async (url) => { requests.push(url); return jsonResponse({}); }, {
    location: { hostname: "example.trycloudflare.com" }, open: (...args) => opens.push(args),
  });
  await h.openTradingView({ symbol: "BTC-USDT-SWAP", timeframe: "15m" });
  assert.deepEqual(opens, [["https://www.tradingview.com/chart/?symbol=OKX%3ABTCUSDT.P&interval=15", "_blank", "noopener,noreferrer"]]);
  assert.deepEqual(requests, []);
  assert.equal(h.remoteTradingViewURL({symbol:"OKX:ETHUSDC.P",timeframe:"1D"}), "https://www.tradingview.com/chart/?symbol=OKX%3AETHUSDC.P&interval=1D");
  assert.equal(h.remoteTradingViewURL({symbol:"https://evil.invalid",timeframe:"15m"}), "");
});

test("loopback workspace preserves its explicit desktop opener", async () => {
  const requests = [];
  const h = harness(async (url) => { requests.push(url); return jsonResponse({requested:true,verified:true,timeframe:"1H"}); }, {
    location: { hostname: "127.0.0.1" }, open: () => { throw new Error("must not open browser"); },
  });
  await h.openTradingView({ symbol: "BTC-USDT-SWAP", timeframe: "1H" });
  assert.deepEqual(requests, ["/api/tradingview/open"]);
});

test("signal clicks and keys never enter the TradingView dispatch", () => {
  const start=app.indexOf('  function activateRow(event, type)');
  const end=app.indexOf('  ["click", "keydown"].forEach', start);
  const context={};
  vm.runInNewContext(app.slice(start,end)+'\nthis.run = activateRow;',context);
  for (const type of ['click','keydown']) context.run({type,key:'Enter',
    target:{closest:selector=>selector==='[data-tradingview-action]'?{dataset:{}}:null},
    preventDefault(){assert.fail('details must keep its default behavior');}
  },'signal');
});

test('signal URL restoration applies defaults instead of leaking filters from the previous visit',()=>{
 const state={search:'ETH',page:4,timeframe:'60',side:'short',period:'week',outcome:'loss',sort:'r_desc',signalLayout:'history',signalPane:'account',signalScope:'retest',signalSource:'legacy'};
 const controls=new Map(Object.entries({'symbol-search':[], 'side-filter':['all','long','short'],'outcome-filter':['all','active','profit','loss','breakeven','unknown'],'sort-filter':['newest','oldest','r_desc','r_asc']}).map(([id,values])=>[id,{options:values.map(value=>({value}))}]));
 const groups={'timeframe':['all','15','30','60','240'],'ledger-period':['all','today','week'],'signal-scope':['retest'],'signal-source':['live','replay','legacy']};
 let invalidations=0;
 const ctx={state,$:id=>controls.get(id),window:{},invalidateSignalQuery(){invalidations++;},renderSignals(){},document:{querySelectorAll(selector){const attr=selector.slice(6,-1);return (groups[attr]||[]).map(value=>({getAttribute:()=>value,classList:{toggle(){}},setAttribute(){}}));}}};
 const code=app.slice(app.indexOf('  function restoreSignalRoute('),app.indexOf("  for(const view of ['signals','warmup'])"));
 const restore=vm.runInNewContext(code+';restoreSignalRoute',ctx);
 restore({});assert.equal(state.signalLayout,'cards');assert.equal(state.signalPane,'records');assert.equal(state.signalSource,'live');
 for(const field of ['side','period','outcome','timeframe'])assert.equal(state[field],'all');
 assert.equal(state.search,'');assert.equal(state.page,0);assert.equal(state.sort,'newest');assert.equal(invalidations,1);
 restore({side:'long',timeframe:'15',layout:'history',q:'BTC',page:'2'});assert.equal(state.side,'long');assert.equal(state.timeframe,'15');assert.equal(state.page,2);assert.equal(state.signalLayout,'history');
 restore({side:'invalid',timeframe:'bad',sort:'invalid',layout:'nope'});assert.equal(state.side,'all');assert.equal(state.timeframe,'all');assert.equal(state.sort,'newest');assert.equal(state.signalLayout,'cards');
});

test('polling preserves keyboard focus on an expanded signal summary by record identity',()=>{
 const client=harness(async()=>jsonResponse({})),rows=client.getElement('signal-rows');
 const summary={dataset:{},parentElement:{dataset:{signalRow:'same'}},matches:()=>true};
 client.document.activeElement=summary;
 const replacement={focus(){client.document.activeElement=this;}},same={dataset:{signalRow:'same'},open:false,querySelector:()=>replacement},other={dataset:{signalRow:'other'},open:false,querySelector:()=>({focus(){throw Error('wrong record');}})};
 rows.querySelectorAll=selector=>selector==='.signal-row-details[open]'?[{dataset:{signalRow:'same'}}]:selector==='.signal-row-details'?[same,other]:[];
 Object.defineProperty(rows,'innerHTML',{set(){client.document.activeElement={dataset:{}};}});
 client.renderSignalsView();assert.equal(client.document.activeElement,replacement);assert.equal(same.open,true);assert.equal(other.open,false);
});
