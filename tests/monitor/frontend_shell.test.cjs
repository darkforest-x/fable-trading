"use strict";

// Shared navigation/status behavior; rendering must not start strategies or AI.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const app = fs.readFileSync(path.join(__dirname, "../../yoyo/monitor/static/app.js"), "utf8");
const cutoff = app.indexOf("  function redact(value)");
const response = (value) => ({ ok: true, headers: { get: () => "application/json" }, json: async () => value });
const status = () => ({ protocol: "spike-burst-v128-monitor-v1", started_at_ms: Date.now() - 60000,
  runtime: { notification_mode: "two_stage", timeframes: ["15m"], model_gate: { loaded: true }, v130: { signals_24h: 44 } },
  counts: { indicator_starts_24h: 44 }, universe: { count: 491 } });

function element() {
  const classes = new Set();
  return { textContent: "", innerHTML: "", style: {}, dataset: {}, attrs: {},
    classList: { add: (name) => classes.add(name), remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name), toggle(name, force) {
        const on = force ?? !classes.has(name); if (on) classes.add(name); else classes.delete(name); return on;
      } },
    setAttribute(key, value) { this.attrs[key] = value; }, removeAttribute(key) { delete this.attrs[key]; },
    closest: () => ({ open: false }), focus() { this.focused = true; },
    scrollIntoView() { this.scrolled = true; },
  };
}

function harness(fetchImpl = async () => response(status())) {
  const nodes = new Map(), history = [], calls = [];
  const get = (id) => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); };
  const nav = ["signals", "paper", "datasets", "manual", "vision"].map((view) => Object.assign(element(), { dataset: { view } }));
  const location = { hash: "#signals" };
  const document = { body: element(), hidden: false, getElementById: get, querySelectorAll: (selector) => selector === "[data-view]" ? nav : [] };
  const sandbox = { AbortController, Date, Intl, Map, Set, Promise, Number, String, Boolean, Array, Object,
    Math, RegExp, Error, TypeError, JSON, encodeURIComponent, location, document,
    history: { pushState: (_, __, hash) => { history.push(hash); location.hash = hash; } },
    window: { scrollTo() {}, SpikePaper: { setActive() {}, refresh: async () => {} } },
    fetch: async (...args) => { calls.push(args[0]); return fetchImpl(...args); },
    setTimeout: (...args) => { const timer = setTimeout(...args); timer.unref(); return timer; }, clearTimeout,
  };
  vm.runInNewContext(`${app.slice(0, cutoff)}
    renderSignals = renderWatch = () => {};
    globalThis.client = { state, setView, refreshShellStatus, refresh, renderStatus };
  })();`, sandbox);
  return { ...sandbox.client, get, calls, history, nav, document, window:sandbox.window };
}

test("direct workspace entry loads shared status without downloading a signal ledger", async () => {
  const c = harness();
  c.setView("paper", false);
  await c.refreshShellStatus();
  assert.deepEqual(c.calls, ["/api/status"]);
  assert.equal(c.get("nav-signal-count").textContent, "44");
  assert.match(c.get("sidebar-runtime").textContent, /已运行/);
  assert.equal(c.get("connection-label").classList.contains("hidden"), true);
  assert.equal(c.get("model-gate-notice").classList.contains("hidden"), true);
  assert.equal(c.get("refresh-button").attrs["aria-label"], "刷新策略前向账本");
});

test("direct notification entry and periodic refresh keep connection badges current", async () => {
  const c = harness();
  c.setView("notifications", false);
  await c.refreshShellStatus();
  assert.deepEqual(c.calls, ["/api/status"]);
  assert.match(c.get("connection-label").innerHTML, /已连接/);
  assert.match(c.get("sidebar-runtime").textContent, /已运行/);
  c.state.statusReceivedAt = 0;
  await c.refresh("periodic");
  assert.deepEqual(c.calls, ["/api/status", "/api/status"]);
});

test("concurrent status refreshes share one request and recover visibly after failure", async () => {
  let finish;
  const c = harness(() => new Promise((resolve) => { finish = resolve; }));
  const first = c.refreshShellStatus(true), second = c.refreshShellStatus(true);
  assert.equal(first, second);
  assert.equal(c.calls.length, 1);
  finish(response(status()));
  await first;
  await c.refreshShellStatus();
  assert.equal(c.calls.length, 1, "navigation within the status interval should reuse the snapshot");

  let failing = true;
  const recovery = harness(async () => { if (failing) throw new Error("unavailable"); return response(status()); });
  await recovery.refreshShellStatus(true);
  assert.equal(recovery.get("sidebar-runtime").textContent, "连接中断 · 自动重连");
  assert.equal(recovery.get("local-light").classList.contains("online"), false);
  failing = false;
  await recovery.refreshShellStatus(true);
  assert.equal(recovery.get("local-light").classList.contains("online"), true);
  assert.equal(recovery.state.errors.status, undefined);
});

test("menu navigation preserves browser history and the skip link keeps the current page", async () => {
  const c = harness();
  c.setView("manual");
  c.setView("datasets");
  c.setView("datasets");
  c.setView("main", false);
  await c.refreshShellStatus();
  assert.deepEqual(c.history, ["#manual", "#datasets"]);
  assert.equal(c.state.view, "datasets");
  assert.equal(c.get("main").focused, true);
  const current = c.nav.find((button) => button.dataset.view === "datasets");
  assert.equal(current.attrs["aria-current"], "page");
  assert.equal(current.title, "数据集");
  assert.equal(current.scrolled, true);
});

test("hidden pages pause periodic fetching and VLM has no inert global refresh button", async () => {
  const c = harness();
  c.setView("vision");
  await c.refreshShellStatus();
  assert.equal(c.get("refresh-button").classList.contains("hidden"), true);
  c.document.hidden = true;
  await c.refresh("periodic");
  assert.equal(c.calls.length, 1);
  c.setView("paper");
  assert.equal(c.get("refresh-button").classList.contains("hidden"), false);
});

test("refresh feedback belongs to the current page even when another request finishes late", async () => {
  const c=harness();let finishPaper,finishModels;
  c.window.SpikePaper.refresh=()=>new Promise(resolve=>{finishPaper=resolve;});
  c.window.SpikeModels={setActive(){},refresh:()=>new Promise(resolve=>{finishModels=resolve;})};
  c.setView('paper');const paper=c.refresh();
  assert.equal(c.get('refresh-button').disabled,true);assert.equal(c.get('refresh-button').attrs['aria-busy'],'true');
  c.setView('models');assert.equal(c.get('refresh-button').disabled,false);
  const models=c.refresh();finishPaper();await paper;
  assert.equal(c.get('refresh-button').disabled,true,'a late response must not enable another page’s busy refresh button');
  finishModels();await models;
  assert.equal(c.get('refresh-button').disabled,false);assert.equal(c.get('refresh-button').attrs['aria-busy'],'false');
  c.window.SpikeModels.refresh=async()=>{throw new Error('offline');};
  await assert.rejects(c.refresh(),/offline/);assert.equal(c.get('refresh-button').disabled,false);
});

test("selecting the current page from the drawer returns focus to content",()=>{
  const c=harness();c.state.view='paper';c.document.body.classList.add('nav-open');
  c.setView('paper',false);
  assert.equal(c.get('main').focused,true);assert.equal(c.document.body.classList.contains('nav-open'),false);
  assert.equal(c.history.length,0);
});

test('late module completion cannot reactivate an abandoned route', async () => {
  const c=harness(), pending=new Map(), active=[];
  c.window.SpikeModules={ensure:view=>new Promise(resolve=>pending.set(view,resolve))};
  c.window.SpikeManual={setActive:value=>active.push(['manual',value])};
  c.window.SpikePlatform={setActive:value=>active.push(['platform',value])};
  c.setView('manual');c.setView('platform');
  pending.get('platform')();await new Promise(setImmediate);
  pending.get('manual')();await new Promise(setImmediate);
  assert.equal(active.filter(([name,value])=>name==='manual'&&value).length,0);
  assert.equal(active.filter(([name,value])=>name==='platform'&&value).length,1);
  assert.equal(c.state.view,'platform');assert.equal(c.get('module-load-state').hidden,true);
});

test("daily detection and leader entries are independent workspaces", async () => {
  const c=harness();
  c.setView('auto-leaders',false);
  assert.equal(c.get('page-title').textContent,'自动找龙头');
  assert.equal(c.get('auto-leaders-view').classList.contains('hidden'),false);
  assert.equal(c.get('langlang-view').classList.contains('hidden'),true);
  c.setView('yolo-detect',false);
  assert.equal(c.get('page-title').textContent,'均线密集检测');
  assert.equal(c.get('yolo-detect-view').classList.contains('hidden'),false);
  assert.equal(c.get('primary-metrics').classList.contains('hidden'),true);
});

test('Monday Fib has an independent route, lifecycle and manual refresh',async()=>{
 const c=harness(),active=[];let refreshes=0;
 c.window.SpikeMondayFib={setActive:value=>active.push(value),refresh:async()=>refreshes++};
 c.setView('monday-fib',false);
 assert.equal(c.get('monday-fib-view').classList.contains('hidden'),false);
 assert.equal(c.get('page-title').textContent,'周一1.618监控');
 assert.equal(c.get('primary-metrics').classList.contains('hidden'),true);
 assert.equal(active.at(-1),true);
 await c.refresh();assert.equal(refreshes,1);
 c.setView('research',false);assert.equal(active.at(-1),false);
});
