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
  runtime: { notification_mode: "two_stage", timeframes: ["15m"], model_gate: { loaded: true } },
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
  const document = { hidden: false, getElementById: get, querySelectorAll: (selector) => selector === "[data-view]" ? nav : [] };
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
  return { ...sandbox.client, get, calls, history, nav, document };
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
  assert.equal(c.get("refresh-button").attrs["aria-label"], "刷新模拟实盘");
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
