"use strict";

// Contract tests for the versioned simulated breakout + SPIKE ledger.
// Run: node --test tests/monitor/frontend_lines.test.cjs
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const root = path.resolve(__dirname, "../..");
const app = fs.readFileSync(path.join(root, "yoyo/monitor/static/app.js"), "utf8");
const cutoff = app.indexOf("  function redact(value)");
assert.ok(cutoff > 0);
const basis = "v11_2_box_joint_rsi7_since_entry_same_tf_streak_next_open_v2_net_cost";
const snapshot = Date.parse("2026-09-20T11:25:40Z");
const response = (value) => ({ ok: true, headers: { get: () => "application/json" }, json: async () => value });
const ledger = (extra = {}) => ({ items: [], basis, selected_performance_version: "current", as_of_ms: snapshot,
  available_performance_versions: [{ value: "current", label: "开仓后RSI第7个 · 当前回算" },
    { value: "baseline", label: "原价格退出 · 初始快照" }, { value: "previous", label: "旧全局计数 · 修正前快照" }],
  projection_versions: [basis], stats: { total: 1, active: 1, closed: 0, unknown: 0, measured_active: 1,
    measured_closed: 0, realized_r: null, floating_r: 2.5, win_rate: null, profit: 0, loss: 0, breakeven: 0 },
  by_timeframe: [], ...extra });
function harness(fetchImpl = async () => response({})) {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) {
      const classes = new Set();
      elements.set(id, { innerHTML: "", textContent: "", value: "", disabled: false,
        classList: { toggle: (name, force) => { if (force) classes.add(name); else classes.delete(name); },
          contains: (name) => classes.has(name) } });
    }
    return elements.get(id);
  };
  const sandbox = { AbortController, Date, Intl, Map, Set, Promise, Number, String, Boolean, Array, Object, Math,
    RegExp, Error, TypeError, JSON, encodeURIComponent, fetch: fetchImpl,
    setTimeout: (...args) => { const timer = setTimeout(...args); timer.unref(); return timer; }, clearTimeout,
    document: { getElementById: getElement, querySelectorAll: () => [] } };
  vm.runInNewContext(`${app.slice(0, cutoff)}
    renderErrors = () => {};
    globalThis.__client = { state, loadLines, renderLines, renderLinesStats, linePerformanceView, rsiProgress, changeLinesPerformanceVersion };
  })();`, sandbox);
  const client = sandbox.__client;
  client.state.view = "joints";
  client.state.lines.kind = "joint";
  return { ...client, getElement };
}
const active = { status: "active", basis, performance_version: basis, current_r: 2.5, peak_r: 4,
  entry_price: 100, stop_price: 99, bars_held: 20, rsi_run_side: -1, rsi_run_count: 6, rsi_counter_known: true };

test("ledger sends the selected version with the other filters", async () => {
  const paths = [];
  const client = harness(async (url) => { paths.push(url); return response(url.includes("/ledger?") ? ledger() : {}); });
  Object.assign(client.state.lines, { performanceVersion: "baseline", period: "week", outcome: "loss", sort: "r_desc",
    liveOnly: true, timeframe: "1H", search: "ETH & BTC" });
  await client.loadLines();
  const query = new URL(paths.find((url) => url.includes("/ledger?")), "http://local").searchParams;
  assert.deepEqual(Object.fromEntries(query), { kind: "joint", limit: "2000", performance_version: "baseline",
    period: "week", outcome: "loss", sort: "r_desc", scope: "live", timeframe: "1H", search: "ETH & BTC" });
});

test("a response from the previous version cannot replace the chosen version", async () => {
  let resolveLedger;
  const client = harness(async (url) => url.includes("/ledger?")
    ? new Promise((resolve) => { resolveLedger = resolve; }) : response({}));
  const pending = client.loadLines();
  client.state.lines.performanceVersion = "baseline";
  client.state.lines.revision++;
  client.state.lines.ledger = ledger({ selected_performance_version: "baseline" });
  resolveLedger(response(ledger()));
  await pending;
  assert.equal(client.state.lines.ledger.selected_performance_version, "baseline");
});

test("RSI exit at a loss is distinguished from a price stop", () => {
  const client = harness();
  const p = { ...active, status: "loss", exit_r: -0.3, exit_price: 99.7,
    exit_reason: "rsi_seventh_reverse_next_open" };
  const view = client.linePerformanceView({ performance: p });
  assert.match(view.badge, /亏损退出/);
  assert.match(view.note, /RSI 第7个空头大菱形 · 全平/);
  assert.doesNotMatch(view.badge, /止损/);
  assert.match(client.linePerformanceView({ performance: { ...p, exit_reason: "initial_stop" } }).badge, /已止损/);
});

test("active cards show known, unknown and pending RSI states without a PnL gate", () => {
  const client = harness();
  assert.match(client.linePerformanceView({ performance: active }).note, /空头大菱形 6 \/ 7/);
  assert.match(client.rsiProgress({ ...active, rsi_counter_known: false }), /入场后计数暂不可用/);
  assert.match(client.rsiProgress({ ...active, current_r: -0.5, rsi_exit_pending: true }), /待次根开盘全平/);
  assert.match(client.rsiProgress({ ...active, rsi_run_side: 1 }), /入场后空头大菱形 0 \/ 7/);
  assert.match(client.rsiProgress({ ...active, rsi_run_side: 0, rsi_run_count: 0 }), /入场后空头大菱形 0 \/ 7/);
});

test("the baseline is a fixed snapshot and is never described as the latest position", () => {
  const client = harness();
  client.state.lines.performanceVersion = "baseline";
  client.state.lines.ledger = ledger({ selected_performance_version: "baseline", performance_snapshot_ms: snapshot });
  const view = client.linePerformanceView({ performance: active });
  assert.match(view.badge, /快照时运行中/);
  assert.equal(view.valueLabel, "快照浮动 R");
  assert.match(view.note, /原价格退出快照，不再更新/);
  assert.doesNotMatch(view.note, /最新收盘|6 \/ 7/);
  client.renderLines();
  assert.match(client.getElement("lines-stats-basis").textContent, /2026\/09\/20 19:25:40/);
  assert.match(client.getElement("lines-stats-basis").textContent, /不作新旧规则同步收益对照/);
  assert.match(client.getElement("lines-exit-rule").textContent, /数值固定，不是当前持仓/);
  assert.equal(client.getElement("lines-performance-version").disabled, false);
});

test("current rules are only named when the backend advertises the new policy", () => {
  const client = harness();
  client.renderLines();
  assert.match(client.getElement("lines-exit-rule").textContent, /等待退出规则同步/);
  client.state.lines.ledger = ledger();
  client.renderLines();
  assert.match(client.getElement("lines-exit-rule").textContent, /从本仓开仓后开始计数/);
  assert.match(client.getElement("lines-exit-rule").textContent, /同周期大菱形连续同色、异色重置/);
  assert.match(client.getElement("lines-exit-rule").textContent, /收盘确认，次根开盘全平/);
});

test("previous global-count projection is also a frozen snapshot with an explicit label", () => {
  const client = harness();
  client.state.lines.performanceVersion = "previous";
  client.state.lines.ledger = ledger({ selected_performance_version: "previous", performance_snapshot_ms: snapshot });
  client.renderLines();
  const view = client.linePerformanceView({ performance: active });
  assert.match(view.badge, /快照时运行中/);
  assert.match(view.note, /修正前全局计数快照，不再更新/);
  assert.doesNotMatch(view.note, /入场后空头大菱形/);
  assert.match(client.getElement("lines-exit-rule").textContent, /修正前全局计数快照/);
});

test("the prior running global counter is not labelled as the corrected entry counter", () => {
  const client = harness();
  const oldBasis = "v11_2_box_joint_rsi7_same_tf_streak_next_open_v1_net_cost";
  client.state.lines.ledger = ledger({ basis: oldBasis });
  client.renderLines();
  assert.match(client.getElement("lines-exit-rule").textContent, /旧全局计数仍在运行/);
  assert.equal(client.rsiProgress({ ...active, basis: oldBasis, performance_version: oldBasis }), "");
});

test("choosing a version clears the rendered prior values before the debounced request", () => {
  const client = harness();
  client.state.lines.ledger = ledger();
  client.renderLines();
  assert.equal(client.getElement("lines-stats-floating").textContent, "+2.50R");
  client.changeLinesPerformanceVersion("baseline");
  assert.equal(client.getElement("lines-stats-floating").textContent, "—");
  assert.equal(client.getElement("lines-rows").innerHTML, "");
  assert.equal(client.getElement("lines-performance-version").value, "baseline");
  assert.equal(client.getElement("lines-performance-version").disabled, false);
  assert.match(client.getElement("lines-exit-rule").textContent, /正在读取所选退出规则/);
});
