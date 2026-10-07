"use strict";

// Notification-center API, filtering, escaping, and stale-response contract.
// Run: node --test tests/monitor/frontend_notifications.test.cjs
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../../yoyo/monitor/static/notifications.js"), "utf8");
const response = (body, status = 200) => ({ ok: status < 400, status,
  headers: { get: () => "application/json" }, json: async () => body });
const route = (topic, channel, enabled = false) => ({ topic, channel, label: topic,
  enabled, activated_ms: 100, timeframes: ["15m", "30m", "1H", "4H"],
  sides: topic === "joint" ? ["long"] : ["long", "short"], armed: true });
const snapshot = (overrides = {}) => ({ as_of_ms: 1700000000000, notice: "订阅只影响后续事件。",
  channels: [
    { id: "telegram", name: "Telegram", configured: true, enabled: true,
      counts: { sent: 91, pending: 2, sending: 1, failed: 3, unknown: 4, skipped: 5 }, last_success_ms: 1699999990000 },
    { id: "bark", name: "Bark", configured: false, enabled: false,
      counts: { sent: 0, pending: 0, sending: 0, failed: 0, unknown: 0, skipped: 0 }, last_success_ms: null },
  ],
  routes: ["spike_v128", "yolo_confirmation", "joint"].flatMap((topic) => [route(topic, "telegram"), route(topic, "bark")]),
  ...overrides });
const event = (overrides = {}) => ({ id: "evt-1", event_id: "evt-1", channel: "telegram",
  topic: "spike_v128", label: "SPIKE V12.8 启动", symbol: "BTC-USDT-SWAP", timeframe: "15m",
  side: "short", kind: "spike_burst_v128", protocol: "v12.8", bar_close_ms: 1699999900000,
  updated_ms: 1700000000000, status: "sent", attempts: 1, error: null, price: 100, ...overrides });

function makeElement() {
  const listeners = {};
  const classes = new Set();
  const attrs = {};
  return {
    innerHTML: "", textContent: "", value: "", disabled: false, dataset: {}, attrs, listeners,
    addEventListener(type, listener) { listeners[type] = listener; },
    fire(type, event = {}) { listeners[type]?.({ target: this, ...event }); },
    getAttribute(name) { return attrs[name] ?? null; },
    setAttribute(name, value) { attrs[name] = String(value); },
    classList: {
      toggle(name, force) { const enabled = force ?? !classes.has(name); if (enabled) classes.add(name); else classes.delete(name); return enabled; },
      add(name) { classes.add(name); }, remove(name) { classes.delete(name); }, contains(name) { return classes.has(name); },
    },
  };
}

function harness(fetchImpl) {
  const elements = new Map();
  const getElement = (id) => {
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
  };
  const timers = new Set();
  const window = {};
  const document = { hidden: false, getElementById: getElement };
  vm.runInNewContext(source, {
    window, document, fetch: fetchImpl, AbortController, Date, Intl, Number, String,
    Boolean, Array, Object, Math, JSON, Promise, Error, URLSearchParams, encodeURIComponent,
    setTimeout: (callback, delay) => { const timer = setTimeout(callback, delay); timer.unref?.(); timers.add(timer); return timer; },
    clearTimeout: (timer) => { clearTimeout(timer); timers.delete(timer); },
    setInterval: () => Symbol("interval"), clearInterval: () => {},
  });
  return { api: window.SpikeNotifications, element: getElement, timers };
}

const flush = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
async function activate(client) {
  client.api.setActive(true);
  await client.api.refresh();
  await flush();
}
function clickDelegated(listener, selector, dataset, checked = null) {
  const target = { dataset, disabled: false, closest: (value) => value === selector ? target : null,
    getAttribute: () => checked };
  listener({ target });
}

test("channel cards and history distinguish service acceptance and safely render API data", async () => {
  const calls = [];
  const p = harness(async (url, options = {}) => {
    calls.push({ url, options });
    return url === "/api/notifications" ? response(snapshot()) :
      response({ items: [event(), event({ id: "evt-2", status: "skipped", label: "<img src=x onerror=alert(1)>", error: "subscription_disabled", price: null })], total: 2, offset: 0 });
  });
  await activate(p);
  assert.ok(calls.every(({ options }) => !options.method || options.method === "GET"), "opening and refreshing the page are read-only");
  const cards = p.element("notification-channel-cards").innerHTML;
  assert.match(cards, /累计服务已接受/);
  assert.match(cards, />91</);
  assert.match(cards, /未配置/);
  assert.match(p.element("notification-route-matrix").innerHTML, /SPIKE V12\.8 普通启动/);
  const rows = p.element("notification-history-rows").innerHTML;
  assert.match(rows, /服务已接受/);
  assert.match(rows, /BTC-USDT-SWAP/);
  assert.match(rows, /来源订阅已关闭/);
  assert.match(rows, /&lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(rows, /<img src=x onerror=alert\(1\)>/i);
  assert.match(rows, /<td>—<\/td>/, "null price remains unavailable instead of becoming zero");
  p.api.setActive(false);
});

test("filters use the API contract and pagination advances by 50 rows", async () => {
  const paths = [];
  const p = harness(async (url) => {
    paths.push(url);
    if (url === "/api/notifications") return response(snapshot());
    return response({ items: Array.from({ length: 50 }, (_, index) => event({ id: `evt-${index}` })), total: 105, offset: Number(new URL(url, "https://local").searchParams.get("offset")) });
  });
  await activate(p);
  p.element("notification-filter-channel").value = "telegram";
  p.element("notification-filter-channel").fire("change");
  p.element("notification-filter-topic").value = "spike_v128";
  p.element("notification-filter-topic").fire("change");
  p.element("notification-filter-status").value = "failed";
  p.element("notification-filter-status").fire("change");
  p.element("notification-filter-side").value = "short";
  p.element("notification-filter-side").fire("change");
  p.element("notification-filter-timeframe").value = "1H";
  p.element("notification-filter-timeframe").fire("change");
  p.element("notification-filter-search").value = "BTC <x>";
  p.element("notification-filter-search").fire("input");
  await new Promise((resolve) => setTimeout(resolve, 300));
  const filtered = paths.filter((url) => url.startsWith("/api/notifications/events?")).at(-1);
  const query = new URL(filtered, "https://local").searchParams;
  for (const [key, value] of Object.entries({ channel: "telegram", topic: "spike_v128", status: "failed", side: "short", timeframe: "1H", search: "BTC <x>", offset: "0", limit: "50" })) {
    assert.equal(query.get(key), value, `query field ${key}`);
  }
  clickDelegated(p.element("notification-history-pagination").listeners?.click, "[data-notification-page]", { notificationPage: "next" });
  await flush();
  const paged = paths.filter((url) => url.startsWith("/api/notifications/events?")).at(-1);
  assert.equal(new URL(paged, "https://local").searchParams.get("offset"), "50");
  p.api.setActive(false);
});

test("stale event responses are ignored when a newer filter result arrives", async () => {
  const pending = [];
  const p = harness((url) => {
    if (url === "/api/notifications") return Promise.resolve(response(snapshot()));
    return new Promise((resolve) => pending.push({ url, resolve }));
  });
  p.api.setActive(true);
  p.element("notification-filter-topic").value = "spike_v128";
  p.element("notification-filter-topic").fire("change");
  assert.equal(pending.length, 2);
  pending[1].resolve(response({ items: [event({ label: "新结果" })], total: 1, offset: 0 }));
  await flush();
  assert.match(p.element("notification-history-rows").innerHTML, /新结果/);
  pending[0].resolve(response({ items: [event({ label: "旧结果" })], total: 1, offset: 0 }));
  await flush();
  assert.match(p.element("notification-history-rows").innerHTML, /新结果/);
  assert.doesNotMatch(p.element("notification-history-rows").innerHTML, /旧结果/);
  p.api.setActive(false);
});

test("route changes send the explicit action header and preserve the server state after failure", async () => {
  const requests = [];
  let current = snapshot();
  let rejectNext = false;
  const p = harness(async (url, options = {}) => {
    requests.push({ url, options });
    if (url === "/api/notifications") return response(current);
    if (url.startsWith("/api/notifications/routes/")) {
      if (rejectNext) { rejectNext = false; return response({}, 503); }
      current = snapshot({ routes: current.routes.map((item) => item.topic === "spike_v128" && item.channel === "telegram" ? { ...item, enabled: JSON.parse(options.body).enabled } : item) });
      return response(current);
    }
    return response({ items: [], total: 0, offset: 0 });
  });
  await activate(p);
  const routeRoot = p.element("notification-route-matrix");
  clickDelegated(routeRoot.listeners.click, "[data-route-topic][data-route-channel]",
    { routeTopic: "spike_v128", routeChannel: "telegram" }, "false");
  await flush();
  const put = requests.find((call) => call.options.method === "PUT");
  assert.ok(put);
  assert.equal(requests.filter((call) => call.options.method === "PUT").length, 1, "one switch action sends one update");
  assert.equal(put.url, "/api/notifications/routes/spike_v128/telegram");
  assert.equal(put.options.headers["x-spike-action"], "update-notification-route");
  assert.deepEqual(JSON.parse(put.options.body), { enabled: true });
  assert.match(routeRoot.innerHTML, /aria-checked="true"/);

  rejectNext = true;
  clickDelegated(routeRoot.listeners.click, "[data-route-topic][data-route-channel]",
    { routeTopic: "spike_v128", routeChannel: "telegram" }, "true");
  await new Promise((resolve) => setTimeout(resolve, 0));
  await flush();
  assert.equal(requests.filter((call) => call.options.method === "PUT").length, 2, "only the second explicit switch action sends another update");
  assert.match(routeRoot.innerHTML, /aria-checked="true"/, "failed save leaves the last server-confirmed value visible");
  assert.match(p.element("notifications-notice").textContent, /保存订阅失败/);
  p.api.setActive(false);
});
