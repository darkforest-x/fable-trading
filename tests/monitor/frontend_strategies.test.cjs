"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const source = fs.readFileSync(path.join(root, "yoyo/monitor/static/strategies.js"), "utf8");
const page = fs.readFileSync(path.join(root, "yoyo/monitor/static/index.html"), "utf8");
const app = fs.readFileSync(path.join(root, "yoyo/monitor/static/app.js"), "utf8");

const response = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => body === null ? "" : "application/json" },
  json: async () => body,
});

class FormDataMock {
  constructor(form) { this.values = form.values || {}; }
  get(key) { return this.values[key] ?? ""; }
  getAll(key) { const value = this.values[key] ?? []; return Array.isArray(value) ? value : [value]; }
}

function harness(fetchImpl) {
  const listeners = {};
  const elements = new Map();
  const element = (key) => {
    if (!elements.has(key)) elements.set(key, { innerHTML: "", textContent: "", value: "", disabled: false, dataset: {} });
    return elements.get(key);
  };
  const rootElement = element("strategies-workspace");
  const paperRoot = element("paper-workspace");
  const paperResults = element("paper-results");
  const document = { activeElement: null, getElementById: (id) => id === "strategies-workspace" ? rootElement : id === "paper-workspace" ? paperRoot : null };
  paperRoot.querySelector = (selector) => selector === "[data-paper-results]" ? paperResults : null;
  const window = { crypto: { randomUUID: () => "request-uuid-1" }, setView: (view) => { window.lastView = view; } };
  vm.runInNewContext(source, {
    window, document,
    fetch: fetchImpl, FormData: FormDataMock, Date, Intl, Number, String, Boolean, Array, Object, Math, JSON, Error, Promise, encodeURIComponent,
  }, { filename: "strategies.js" });
  for (const target of [rootElement, paperRoot]) target.addEventListener = (type, listener) => { listeners[`${target === rootElement ? "strategies" : "paper"}:${type}`] = listener; };
  return { api: window.SpikeStrategies, paper: window.SpikePaper, window, root: rootElement, paperRoot, paperResults, document, listeners, element };
}

const strategy = (overrides = {}) => ({
  id: "spike-v12", name: "SPIKE <img src=x onerror=alert(1)>", version: "V12.8", description: "研究登记策略",
  kind: "signal", status: "research", paper_supported: true, timeframes: ["15m", "1H"], side: "long/short",
  entry_rule: "只在下一根未来开盘", exit_rule: "遵循登记规则", cost_bp: 20, notes: "既有备注",
  factor_ids: ["factor-a"], experiment_ids: ["exp-a"], revision: 3, production_eligible: false,
  source_hash: "abc123", ...overrides,
});

function click(listener, selector, dataset = {}) {
  const matched = { dataset };
  listener({ target: { closest: (value) => value === selector ? matched : null } });
}

test("strategy registry lazy-loads, escapes untrusted descriptions, and saves research associations only", async () => {
  const calls = [];
  const p = harness(async (url, options = {}) => {
    calls.push({ url, method: options.method || "GET", body: options.body });
    if (url === "/api/research/strategies") return response({ items: [
      strategy({ kind: "spike_burst_v128" }),
      strategy({ id: "joint", kind: "joint", name: "联合" }),
      strategy({ id: "trendline", kind: "trendline_breakout", name: "趋势线" }),
      strategy({ id: "yolo", kind: "yolo_confirmed", name: "模型确认" }),
    ], sources: [{ name: "Upstream", url: "https://github.com/example/project" }] });
    if (url === "/api/research/strategies/spike-v12") return response({ item: strategy({ notes: "更新备注", factor_ids: ["factor-a", "factor-b"], experiment_ids: ["exp-b"], revision: 4 }) });
    throw new Error(`unexpected request ${url}`);
  });
  assert.equal(calls.length, 0);
  await p.api.setActive(true);
  assert.equal(calls[0].url, "/api/research/strategies");
  assert.match(p.root.innerHTML, /SPIKE &lt;img src=x onerror=alert\(1\)&gt;/);
  assert.match(p.root.innerHTML, /启动信号/);
  assert.match(p.root.innerHTML, /联合信号/);
  assert.match(p.root.innerHTML, /趋势线突破/);
  assert.match(p.root.innerHTML, /模型确认/);
  assert.doesNotMatch(p.root.innerHTML, /<img src=x/);
  assert.match(p.root.innerHTML, /生产资格/);
  assert.match(p.root.innerHTML, /登记为否/);
  assert.match(p.root.innerHTML, /在模拟实盘中选择/);
  assert.ok(p.root.innerHTML.includes("https://github.com/example/project"));
  click(p.listeners["strategies:click"], "[data-paper-strategy]", { paperStrategy: "spike-v12" });
  assert.equal(p.window.lastView, "paper");

  const form = {
    dataset: { strategyEdit: "spike-v12", revision: "3" },
    values: { stage: "research", notes: "更新备注", factor_ids: "factor-a, factor-b, factor-a", experiment_ids: "exp-b" },
  };
  const event = { target: { closest: (selector) => selector === "form[data-strategy-edit]" ? form : null }, preventDefault() {} };
  p.listeners["strategies:submit"](event);
  await new Promise((resolve) => setTimeout(resolve, 0));
  const put = calls.find((call) => call.method === "PUT");
  assert.equal(put.url, "/api/research/strategies/spike-v12");
  assert.deepEqual(JSON.parse(put.body), {
    expected_revision: 3, stage: "research", notes: "更新备注",
    factor_ids: ["factor-a", "factor-b"], experiment_ids: ["exp-b"],
  });
  assert.doesNotMatch(put.body, /train|promot|production_eligible/i);
});

test("paper form submits selected real options and run actions preserve distinct server states", async () => {
  const calls = [];
  let status = "running";
  let heartbeat = null;
  const run = () => ({ id: "run-1", strategy_id: "spike-v12", strategy_name: "SPIKE", strategy_version: "V12.8", status, created_ms: 1800000000000,
    admit_after_ms: 1800000001000, heartbeat_ms: heartbeat, error: status === "error" ? "worker failed" : null,
    spec: { symbol_scope: "okx_all_usdt", symbols: null, timeframes: ["15m"], cost_bp: 20, entry_rule: "实际观察后下一次未来开盘", exit_rule: "登记退出规则", exit_fill_policy: "precommitted-closed-bar-rules-event-time-v1", source_hash: "abc123" },
    metrics: { accepted: 2, closed: 1, open: 1, pending: 0, skipped: 3, net_r: 0.4, win_rate: 1 }, revision: 2 });
  const p = harness(async (url, options = {}) => {
    calls.push({ url, method: options.method || "GET", body: options.body });
    if (url === "/api/research/strategies") return response({ items: [strategy()], sources: [] });
    if (url === "/api/research/paper/runs" && options.method === "POST") return response(run(), 201);
    if (url === "/api/research/paper/runs") return response({ items: [run()], now_ms: 1800000002000 });
    if (url === "/api/research/paper/runs/run-1?limit=50&offset=0") return response({ ...run(), total: 0, offset: 0, limit: 50, decisions: [] });
    if (url.startsWith("/api/research/paper/runs/run-1/")) {
      status = url.endsWith("/pause") ? "paused" : url.endsWith("/resume") ? "running" : "stopped";
      return response(run());
    }
    throw new Error(`unexpected request ${options.method || "GET"} ${url}`);
  });
  await p.paper.setActive(true);
  assert.match(p.paperRoot.innerHTML, /OKX 全部 USDT 永续（默认）/);
  assert.match(p.paperRoot.innerHTML, /只读取触发信号的合约，不会额外下载行情/);
  assert.doesNotMatch(p.paperRoot.innerHTML, /name="symbols"/);
  assert.match(p.paperRoot.innerHTML, /固定往返成本 20 bp/);
  assert.match(p.paperRoot.innerHTML, /统计使用 R，不代表账户收益/);
  assert.match(p.paperRoot.innerHTML, /不计资金费和盘口滑点/);
  assert.match(p.paperRoot.innerHTML, /只有任务创建后的新观察会进入记录/);
  assert.match(p.paperRoot.innerHTML, /等待后台启动/);

  const form = { values: { strategy_id: "spike-v12", symbol_scope: "okx_all_usdt", timeframes: ["15m", "1H"] } };
  p.listeners["paper:submit"]({ target: { closest: (selector) => selector === "form[data-paper-create]" ? form : null }, preventDefault() {} });
  await new Promise((resolve) => setTimeout(resolve, 0));
  const create = calls.find((call) => call.method === "POST" && call.url === "/api/research/paper/runs");
  assert.ok(create);
  assert.deepEqual(JSON.parse(create.body), {
    strategy_id: "spike-v12", symbol_scope: "okx_all_usdt", symbols: null, timeframes: ["15m", "1H"], request_id: "request-uuid-1",
  });
  assert.match(p.paperRoot.innerHTML, /观察设置 OKX 全部 USDT 永续/);
  assert.match(p.paperRoot.innerHTML, /data-run-action="pause"/);
  assert.match(p.paperRoot.innerHTML, /合计 R 0\.40/);
  assert.match(p.paperRoot.innerHTML, /暂停只停止新的入场/);
  assert.match(p.paperRoot.innerHTML, /停止时未平仓记录会保留为截尾/);
  assert.match(p.paperRoot.innerHTML, /未建模逐次退出下单延迟/);
  assert.match(p.paperRoot.innerHTML, /闭合 OHLC 计算结果不等同真实报价/);
  assert.match(p.paperRoot.innerHTML, /下载源码快照/);
  assert.match(p.paperRoot.innerHTML, /href="\/api\/research\/paper\/runs\/run-1\/export"/);
  assert.match(p.paperRoot.innerHTML, /href="\/api\/research\/paper\/runs\/run-1\/sources"/);
  assert.match(p.paperRoot.innerHTML, /成交策略标识/);
  assert.match(p.paperRoot.innerHTML, /precommitted-closed-bar-rules-event-time-v1/);
  heartbeat = 1800000002000 - 91000;
  const activeInput = { value: "SOL-USDT-SWAP" };
  activeInput.closest = (selector) => selector === ".paper-create-form" ? {} : null;
  p.document.activeElement = activeInput;
  const beforePeriodicMarkup = p.paperRoot.innerHTML;
  await p.paper.refresh("periodic");
  assert.equal(p.paperRoot.innerHTML, beforePeriodicMarkup, "periodic refresh must not replace the create form DOM");
  assert.equal(p.document.activeElement, activeInput, "periodic refresh must preserve focus and the active input value");
  assert.equal(activeInput.value, "SOL-USDT-SWAP");
  assert.match(p.paperResults.innerHTML, /后台未更新/);
  p.document.activeElement = null;
  click(p.listeners["paper:click"], "[data-paper-refresh]");
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.match(p.paperRoot.innerHTML, /后台未更新/);

  click(p.listeners["paper:click"], "[data-run-action]", { runAction: "pause", runId: "run-1" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(calls.some((call) => call.url === "/api/research/paper/runs/run-1/pause" && call.method === "POST"));
  assert.match(p.paperRoot.innerHTML, /data-run-action="resume"/);
  click(p.listeners["paper:click"], "[data-run-action]", { runAction: "resume", runId: "run-1" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(calls.some((call) => call.url === "/api/research/paper/runs/run-1/resume" && call.method === "POST"));
  click(p.listeners["paper:click"], "[data-run-action]", { runAction: "stop", runId: "run-1" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.ok(calls.some((call) => call.url === "/api/research/paper/runs/run-1/stop" && call.method === "POST"));
  assert.doesNotMatch(p.paperRoot.innerHTML, /data-run-action="stop"/);
});

test("paper decisions preserve within-bar time precision and stopped trades as censored", async () => {
  const stoppedRun = {
    id: "run-stop", strategy_id: "spike-v12", strategy_name: "SPIKE", strategy_version: "V12.8",
    status: "stopped", created_ms: 1800000000000, spec: { symbols: ["BTC-USDT-SWAP"], timeframes: ["15m"], cost_bp: 20 },
    metrics: { accepted: 2, closed: 1, open: 0, pending: 0, skipped: 0, censored: 1, net_r: 0.2, win_rate: 1 },
  };
  const p = harness(async (url) => {
    if (url === "/api/research/strategies") return response({ items: [strategy()], sources: [] });
    if (url === "/api/research/paper/runs") return response({ items: [stoppedRun], now_ms: 1800000002000 });
    if (url === "/api/research/paper/runs/run-stop?limit=50&offset=0") return response({
      ...stoppedRun, total: 2, offset: 0, limit: 50, decisions: [
        { event_id: "closed-1", symbol: "BTC-USDT-SWAP", timeframe: "15m", side: "long", signal_close_ms: 1800000000000,
          observed_ms: 1800000000100, scheduled_entry_ms: 1800000001000, status: "closed",
          trade: { status: "closed", entry_time_ms: 1800000001000, entry_price: 100, exit_price: 101, exit_time_ms: 1800000000000,
            exit_time_precision: "within_bar", exit_bar_end_ms: 1800000900000,
            initial_stop: 98, net_r: 0.5, unrealized_r: null } },
        { event_id: "stopped-2", symbol: "BTC-USDT-SWAP", timeframe: "15m", side: "short", signal_close_ms: 1800000000000,
          observed_ms: 1800000000200, scheduled_entry_ms: 1800000001000, status: "stopped", reason: "run_stopped_with_open_position",
          trade: { status: "stopped", entry_time_ms: 1800000001000, entry_price: 100, exit_price: null, exit_time_ms: null,
            exit_time_precision: null, initial_stop: 102, net_r: null, unrealized_r: null } },
      ],
    });
    throw new Error("unexpected request " + url);
  });
  await p.paper.setActive(true);
  assert.match(p.paperRoot.innerHTML, /K线区间，非秒级精确时点/);
  assert.match(p.paperRoot.innerHTML, /2027\/01\/15 16:00:00 – 2027\/01\/15 16:15:00/);
  assert.match(p.paperRoot.innerHTML, /2027\/01\/15 16:15:00/);
  assert.match(p.paperRoot.innerHTML, /截尾（无模拟平仓）/);
  assert.match(p.paperRoot.innerHTML, /停止时未模拟平仓；未记录退出时间/);
  assert.match(p.paperRoot.innerHTML, /已实现 R · 未平仓截尾/);
  assert.match(p.paperRoot.innerHTML, /观察设置 BTC-USDT-SWAP/);
  assert.doesNotMatch(p.paperRoot.innerHTML, /停止时未模拟平仓[^<]*.*已实现 R<\/dt><dd>0\.000/);
});

test("paper custom scope retains its draft and accepts more than 20 symbols up to 2000", async () => {
  const calls = [];
  const p = harness(async (url, options = {}) => {
    calls.push({ url, method: options.method || "GET", body: options.body });
    if (url === "/api/research/strategies") return response({ items: [strategy()], sources: [] });
    if (url === "/api/research/paper/runs" && options.method === "POST") return response({ id: "custom-run", status: "running" }, 201);
    if (url === "/api/research/paper/runs") return response({ items: [], now_ms: 1800000002000 });
    throw new Error(`unexpected request ${options.method || "GET"} ${url}`);
  });
  await p.paper.setActive(true);
  const formNode = {};
  p.listeners["paper:change"]({ target: { name: "symbol_scope", value: "custom", closest: () => formNode } });
  const draftInput = { name: "symbols", value: "SOL-USDT-SWAP, XRP-USDT-SWAP", closest: () => formNode };
  p.listeners["paper:input"]({ target: draftInput });
  p.listeners["paper:change"]({ target: { name: "symbol_scope", value: "okx_all_usdt", closest: () => formNode } });
  assert.doesNotMatch(p.paperRoot.innerHTML, /name="symbols"/);
  p.listeners["paper:change"]({ target: { name: "symbol_scope", value: "custom", closest: () => formNode } });
  assert.match(p.paperRoot.innerHTML, /name="symbols" value="SOL-USDT-SWAP, XRP-USDT-SWAP"/);
  assert.match(p.paperRoot.innerHTML, /最多 2000 个/);

  const symbols = Array.from({ length: 21 }, (_, index) => `COIN${index}-USDT-SWAP`);
  const customForm = { values: { strategy_id: "spike-v12", symbol_scope: "custom", symbols: symbols.join(", "), timeframes: ["15m"] } };
  p.listeners["paper:submit"]({ target: { closest: (selector) => selector === "form[data-paper-create]" ? customForm : null }, preventDefault() {} });
  await new Promise((resolve) => setTimeout(resolve, 0));
  const create = calls.find((call) => call.method === "POST" && call.url === "/api/research/paper/runs");
  assert.ok(create);
  const payload = JSON.parse(create.body);
  assert.equal(payload.symbol_scope, "custom");
  assert.deepEqual(payload.symbols, symbols);

  const tooManySymbols = Array.from({ length: 2001 }, (_, index) => `COIN${index}-USDT-SWAP`).join(", ");
  const tooManyForm = { values: { strategy_id: "spike-v12", symbol_scope: "custom", symbols: tooManySymbols, timeframes: ["15m"] } };
  p.listeners["paper:submit"]({ target: { closest: (selector) => selector === "form[data-paper-create]" ? tooManyForm : null }, preventDefault() {} });
  assert.match(p.paperRoot.innerHTML, /自选合约最多 2000 个/);
  assert.equal(calls.filter((call) => call.method === "POST" && call.url === "/api/research/paper/runs").length, 1);

  const invalidForm = { values: { strategy_id: "spike-v12", symbol_scope: "custom", symbols: "BTCUSDT", timeframes: ["15m"] } };
  p.listeners["paper:submit"]({ target: { closest: (selector) => selector === "form[data-paper-create]" ? invalidForm : null }, preventDefault() {} });
  assert.match(p.paperRoot.innerHTML, /合约格式应为 BTC-USDT-SWAP/);
  assert.equal(calls.filter((call) => call.method === "POST" && call.url === "/api/research/paper/runs").length, 1);
});

test("primary nav uses strategy and paper views while old watch, warmup, and shadow views remain reachable", () => {
  for (const view of ["strategies", "paper"]) {
    assert.match(page, new RegExp(`data-view="${view}"`));
    assert.match(page, new RegExp(`id="${view}-view"`));
    assert.match(app, new RegExp(`"${view}"`));
  }
  for (const [view, label] of [["watch", "蓄势观察"], ["warmup", "预热历史"], ["shadow", "旧版影子记录"]]) {
    assert.doesNotMatch(page, new RegExp(`class="nav-item" data-view="${view}"`));
    if (view !== "shadow") assert.match(page, new RegExp(`href="#${view}"`));
    assert.match(app, new RegExp(`${view}: \\["${label}"`));
  }
  assert.match(source, /href="#shadow">查看保留的 V7 \/ V8 前向影子旧视图/);
  assert.match(page, /\/static\/strategies\.js/);
  assert.match(page, /\/static\/strategies\.css/);
  const navStart = page.indexOf('<nav class="nav-list"');
  assert.ok(navStart >= 0);
  const nav = page.slice(navStart, page.indexOf("</nav>", navStart));
  assert.ok(nav.indexOf('data-view="strategies"') < nav.indexOf('data-view="experiments"'));
  assert.ok(nav.indexOf('data-view="experiments"') < nav.indexOf('data-view="backtests"'));
  assert.ok(nav.indexOf('data-view="backtests"') < nav.indexOf('data-view="paper"'));
});

test("legacy shadow view renders when its removed navigation count node is absent", () => {
  const start = app.indexOf("  function renderShadow() {");
  const end = app.indexOf("\n  async function loadShadow()", start);
  assert.ok(start >= 0 && end > start);
  const renderShadow = app.slice(start, end);
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      textContent: "", innerHTML: "", classList: { toggle() {} }, querySelectorAll: () => [],
    });
    return elements.get(id);
  };
  const $ = (id) => id === "nav-shadow-count" ? null : element(id);
  assert.doesNotThrow(() => vm.runInNewContext(`${renderShadow}\nrenderShadow();`, {
    $, state: { shadowStatus: null, shadowMarket: [], shadowEvents: [], shadowTimeframe: "all" },
    document: { activeElement: null }, number: (value) => String(value ?? "—"), numeric: (value) => Number(value || 0),
    renderTradingViewButtons() {},
  }));
  assert.match(elements.get("shadow-event-count").textContent, /—/);
});
