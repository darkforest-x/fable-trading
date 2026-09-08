"use strict";

// Run with: node --test tests/monitor/frontend_cards.test.cjs
// Execute the actual browser client in a VM with a deliberately small DOM stub.
// No dependencies, browser, network, market data or notification service is used.
// This checks client decisions and emitted markup; it is not browser layout QA.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const clientPath = path.join(root, "yoyo/monitor/static/app.js");
const indexPath = path.join(root, "yoyo/monitor/static/index.html");
const NOW = 1_789_000_000_000;
const PROTOCOL = "imacd-yolo-confirmation-monitor-v1";
const TV_PROTOCOL = "imacd-tv-visible-start-monitor-v3";

function decode(value) {
  return String(value).replace(/&(amp|lt|gt|quot|#39);/g, (_, name) =>
    ({ amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" })[name]);
}

function attributes(text) {
  const result = {};
  for (const match of text.matchAll(/([\w:-]+)="([^"]*)"/g)) result[match[1]] = decode(match[2]);
  return result;
}

function harness({ allowChartFixture = false, nowStep = 0, bridgeReply = null, apiReplies = {} } = {}) {
  let now = NOW;
  const elements = new Map();
  const document = { activeElement: null, hidden: false, listeners: new Map() };
  class Element {
    constructor(tagName = "DIV", attrs = {}) {
      this.tagName = tagName.toUpperCase();
      this.attrs = { ...attrs };
      this.dataset = {};
      for (const [name, value] of Object.entries(attrs)) {
        if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
      }
      const classes = new Set((attrs.class || "").split(/\s+/).filter(Boolean));
      this.classList = {
        add: (...names) => names.forEach((name) => classes.add(name)),
        remove: (...names) => names.forEach((name) => classes.delete(name)),
        contains: (name) => classes.has(name),
        toggle: (name, force) => {
          const value = force === undefined ? !classes.has(name) : Boolean(force);
          if (value) classes.add(name); else classes.delete(name);
          return value;
        },
      };
      this.listeners = new Map();
      this.style = {};
      this.children = [];
      this.descendants = [];
      this.textContent = "";
      this.value = attrs.value || "";
      this._html = "";
    }
    set innerHTML(value) {
      this._html = String(value);
      this.children = [];
      this.descendants = [];
      // Preserve ancestors so delegated clicks on card content and inside the
      // independent preview button exercise actual closest() boundaries.
      const stack = [this];
      for (const match of this._html.matchAll(/<(\/)?([a-z][a-z0-9]*)\b([^>]*)>/gi)) {
        if (match[1]) { if (stack.length > 1) stack.pop(); continue; }
        const child = new Element(match[2], attributes(match[3]));
        child.parent = stack.at(-1);
        child.parent.children.push(child);
        for (const ancestor of stack) ancestor.descendants.push(child);
        if (!/\/\s*$/.test(match[3]) && !/^(br|hr|img|input|meta|link)$/i.test(match[2])) stack.push(child);
      }
    }
    get innerHTML() { return this._html; }
    setAttribute(name, value) { this.attrs[name] = String(value); }
    getAttribute(name) { return this.attrs[name] ?? null; }
    removeAttribute(name) { delete this.attrs[name]; }
    addEventListener(name, callback) {
      if (!this.listeners.has(name)) this.listeners.set(name, []);
      this.listeners.get(name).push(callback);
    }
    dispatch(name, extra = {}) {
      const event = { type: name, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
      for (const listener of this.listeners.get(name) || []) listener(event);
      return event;
    }
    matches(selector) {
      if (selector.startsWith("[")) return selector.slice(1, -1).split("=")[0] in this.attrs;
      if (selector.startsWith(".")) return this.classList.contains(selector.slice(1));
      return this.tagName === selector.toUpperCase();
    }
    querySelectorAll(selector) { return this.descendants.filter((item) => item.matches(selector)); }
    querySelector(selector) {
      if (["h3", "p"].includes(selector)) {
        this._parts ||= {};
        this._parts[selector] ||= new Element(selector);
        return this._parts[selector];
      }
      return this.querySelectorAll(selector)[0] || null;
    }
    closest(selector) {
      if (selector === "details") return this._details ||= new Element("DETAILS");
      return this.matches(selector) ? this : this.parent?.closest(selector) || null;
    }
    focus(options) { document.activeElement = this; this.focusOptions = options; }
    scrollIntoView() {}
  }
  const markup = fs.readFileSync(indexPath, "utf8");
  const nodes = [];
  for (const match of markup.matchAll(/<([a-z][a-z0-9]*)\b([^>]*)>/gi)) {
    const attrs = attributes(match[2]);
    const element = new Element(match[1], attrs);
    nodes.push(element);
    if (attrs.id) elements.set(attrs.id, element);
  }
  document.getElementById = (id) => elements.get(id) || null;
  document.querySelectorAll = (selector) => [...nodes, ...nodes.flatMap((node) => node.descendants)].filter((node) => node.matches(selector));
  document.addEventListener = (name, callback) => document.listeners.set(name, callback);
  class ClockDate extends Date {
    static now() {
      const result = now;
      now += nowStep;
      return result;
    }
  }
  const networkCalls = [];
  const networkRequests = [];
  const context = vm.createContext({
    document, Date: ClockDate, Intl, console, AbortController, setTimeout, clearTimeout,
    window: { innerWidth: 1280, addEventListener() {}, matchMedia: () => ({ matches: true }) },
    location: { hash: "#signals" }, history: { replaceState() {} },
    fetch: (url, options = {}) => {
      networkCalls.push(url);
      networkRequests.push({ url, ...options });
      if (Object.hasOwn(apiReplies, url)) {
        const reply = apiReplies[url];
        if (reply instanceof Error) return Promise.reject(reply);
        return Promise.resolve({ ok: true, headers: { get: () => "application/json" }, json: async () => reply });
      }
      if (url === "/api/tradingview/open" && bridgeReply) {
        return typeof bridgeReply === "function" ? bridgeReply(options) : Promise.resolve(bridgeReply);
      }
      if (allowChartFixture && url.startsWith("/api/chart?")) {
        return Promise.resolve({ ok: true, headers: { get: () => "application/json" }, json: async () => ({ candles: [] }) });
      }
      return Promise.reject(new Error("Network is forbidden in this unit test"));
    },
  });
  const source = fs.readFileSync(clientPath, "utf8");
  const bootstrap = '  setView(location.hash.slice(1) || "signals", false);';
  assert.equal(source.split(bootstrap).length, 2, "client bootstrap must be uniquely identified");
  const exposed = ["state", "isFresh", "filteredSignals", "notification", "notificationHTML", "renderSignals", "isBuilding", "renderWatch", "renderDetail", "refresh", "modelOverlayHTML", "isConfirmed"];
  vm.runInContext(source.slice(0, source.indexOf(bootstrap)) + `\n  globalThis.client = { ${exposed.join(", ")} };\n})();`, context, { filename: clientPath });
  const client = context.client;
  client.state.status = { protocol: PROTOCOL, now_ms: NOW, runtime: { signal_kind: "yolo_confirmed", fresh_minutes: 30 } };
  client.state.statusReceivedAt = NOW;
  client.state.signalsLoaded = true;
  client.state.candidatesLoaded = true;
  client.state.marketsLoaded = true;
  return { client, document, get: (id) => elements.get(id), advance: (ms) => { now += ms; }, networkCalls, networkRequests };
}

function signal(id, ageMinutes = 1, extra = {}) {
  return {
    id: String(id), symbol: "BTC-USDT-SWAP", timeframe: "1H", kind: "yolo_confirmed", protocol: PROTOCOL,
    side: "long", price: 105.25, bar_close_ms: NOW - ageMinutes * 60_000,
    near_zero_bars: 44, zero_bars: 9, is_fresh: true,
    indicator: { kind: "tv_start", protocol: TV_PROTOCOL, price: 102.5, bar_close_ms: NOW - 180 * 60_000, bar_open_ms: NOW - 240 * 60_000 },
    model: { status: "confirmed", wait_bars: 3, max_wait_bars: 9, confidence: .79 },
    notification_status: "sent", bark_notification_status: "unknown", ...extra,
  };
}

const ids = (element) => element.querySelectorAll("[data-signal-id]").map((card) => card.dataset.signalId);

test("freshness is inclusive at zero and the runtime limit, excluding future/expired timestamps", () => {
  const { client } = harness();
  const cases = [[0, true], [30 * 60_000, true], [30 * 60_000 + 1, false], [-1, false]];
  for (const [age, expected] of cases) assert.equal(client.isFresh(signal("x", 0, { bar_close_ms: NOW - age })), expected, `age=${age}`);
  assert.equal(client.isFresh(signal("x", 0, { bar_close_ms: null })), false);
  assert.equal(client.isFresh(signal("x", 0, { bar_close_ms: "invalid" })), false);
});

test("freshness requires API approval and a known matching runtime protocol", () => {
  const { client } = harness();
  for (const flag of [false, undefined, null, "true"]) assert.equal(client.isFresh(signal("x", 1, { is_fresh: flag })), false);
  const status = client.state.status;
  client.state.status = null;
  assert.equal(client.isFresh(signal("x")), false);
  client.state.status = { ...status, protocol: "older-protocol" };
  assert.equal(client.isFresh(signal("x")), false);
  client.state.status = { ...status, runtime: { ...status.runtime, signal_kind: "zero_breakout" } };
  assert.equal(client.isFresh(signal("x")), false);
  client.state.status = { ...status, runtime: { signal_kind: "yolo_confirmed" } };
  assert.equal(client.isFresh(signal("x")), false);
});

test("the freshness duration follows runtime rather than a hardcoded 30 minutes", () => {
  const { client } = harness();
  client.state.status.runtime.fresh_minutes = 15;
  assert.equal(client.isFresh(signal("boundary", 15)), true);
  assert.equal(client.isFresh(signal("expired", 15, { bar_close_ms: NOW - 900_001 })), false);
});

test("a skewed browser clock uses calibrated server time plus elapsed reception time", () => {
  const { client, advance } = harness();
  advance(8 * 3_600_000);
  client.state.statusReceivedAt = NOW + 8 * 3_600_000;
  assert.equal(client.isFresh(signal("x", 29)), true);
  advance(60_001);
  assert.equal(client.isFresh(signal("x", 29)), false);
});

test("cached API fresh flags expire locally without a successful next poll", () => {
  const { client, advance } = harness();
  const item = signal("cached", 29);
  assert.equal(client.isFresh(item), true);
  advance(60_001);
  assert.equal(item.is_fresh, true, "the cached API payload remains unchanged");
  assert.equal(client.isFresh(item), false);
});

test("one render crossing the freshness boundary never duplicates or inconsistently labels a card", () => {
  const { client, get } = harness({ nowStep: 1 });
  // The first clock read is exactly the inclusive boundary. Every later read
  // advances one millisecond, so independently reclassifying each group would
  // put the same event into both the fresh and historical arrays.
  client.state.signals = [signal("boundary", 30)];
  client.renderSignals();
  assert.deepEqual(ids(get("signal-rows")), ["boundary"]);
  assert.match(get("signal-rows").innerHTML, /fresh-heading/);
  assert.match(get("signal-rows").innerHTML, /class="signal-card[^\"]*\bis-fresh\b/);
  assert.doesNotMatch(get("signal-rows").innerHTML, /更早确认|已记录确认/);
  client.renderSignals();
  assert.deepEqual(ids(get("signal-rows")), ["boundary"]);
  assert.doesNotMatch(get("signal-rows").innerHTML, /fresh-heading|class="signal-card[^\"]*\bis-fresh\b/);
  assert.match(get("signal-rows").innerHTML, /已记录确认/);
});

test("status or signal fetch failure demotes cached cards; an unrelated watch failure does not", () => {
  const { client, get } = harness();
  client.state.signals = [signal("cached")];
  for (const failed of ["status", "signals"]) {
    client.state.errors = { [failed]: "offline" };
    assert.equal(client.isFresh(client.state.signals[0]), false);
    client.renderSignals();
    assert.deepEqual(ids(get("signal-rows")), ["cached"], "failed poll keeps cached signal inspectable");
    assert.doesNotMatch(get("signal-rows").innerHTML, /class="signal-card[^\"]*\bis-fresh\b/);
  }
  client.state.errors = { markets: "offline" };
  assert.equal(client.isFresh(client.state.signals[0]), true);
});

test("signal filters combine symbol, timeframe, side and canonical kind without mutating the cache", () => {
  const { client } = harness();
  client.state.signals = [signal("match", 1, { symbol: "ETH-USDT-SWAP", timeframe: "15m", side: "short" }), signal("wrong-side", 1, { symbol: "ETH-USDT-SWAP", timeframe: "15m" }), signal("wrong-tf", 1, { symbol: "ETH-USDT-SWAP", side: "short" }), signal("wrong-kind", 1, { symbol: "ETH-USDT-SWAP", timeframe: "15m", side: "short", kind: "entry" }), signal("wrong-symbol")];
  client.state.search = "eth-usdt";
  client.state.timeframe = "15m";
  client.state.side = "short";
  assert.deepEqual(Array.from(client.filteredSignals(), (item) => item.id), ["match"]);
  assert.equal(client.state.signals.length, 5);
});

test("signal cards paginate in 24s across fresh/history groups without duplicates or changing totals", () => {
  const { client, get, networkCalls } = harness();
  client.state.signals = Array.from({ length: 55 }, (_, index) => signal(index, index + 1));
  client.state.signalTotal = 1025;
  client.renderSignals();
  assert.equal(client.state.rowLimit, 24);
  assert.deepEqual(ids(get("signal-rows")), Array.from({ length: 24 }, (_, i) => String(i)));
  assert.match(get("filtered-count").textContent, /55/);
  get("load-more-signals").dispatch("click");
  assert.equal(client.state.rowLimit, 48);
  assert.deepEqual(ids(get("signal-rows")), Array.from({ length: 48 }, (_, i) => String(i)));
  assert.match(get("signal-rows").innerHTML, /signal-group-heading/);
  assert.match(get("signal-rows").innerHTML, /signal-card-grid/);
  get("load-more-signals").dispatch("click");
  assert.equal(ids(get("signal-rows")).length, 55);
  assert.equal(new Set(ids(get("signal-rows"))).size, 55);
  assert.equal(client.state.signalTotal, 1025);
  assert.match(get("filtered-count").textContent, /55/);
  assert.equal(get("load-more-signals").classList.contains("hidden"), true);
  assert.equal(networkCalls.length, 0, "rendering cards never loads every card's chart");
});

test("signal cards preserve selection and restore keyboard focus through nested group wrappers", () => {
  const { client, get, document } = harness();
  const chosen = signal("chosen");
  client.state.signals = [chosen, signal("second", 40)];
  client.state.selected = { ...chosen };
  client.renderSignals();
  const first = get("signal-rows").querySelectorAll("[data-signal-id]")[0];
  assert.equal(first.tagName, "BUTTON");
  assert.equal(first.closest(".signal-card").classList.contains("selected"), true);
  first.focus();
  client.renderSignals();
  const replacement = get("signal-rows").querySelectorAll("[data-signal-id]")[0];
  assert.notEqual(first, replacement);
  assert.equal(document.activeElement, replacement);
  assert.equal(document.activeElement.focusOptions.preventScroll, true);
  const preview = get("signal-rows").querySelectorAll("[data-preview-signal-id]")[0];
  assert.equal(preview.getAttribute("aria-pressed"), "true");
  preview.focus();
  client.renderSignals();
  assert.equal(document.activeElement.dataset.previewSignalId, "chosen", "refresh preserves the secondary action's focus");
});

test("TG and Bark retain independent outcomes and do not imply device delivery from Bark acceptance", () => {
  const { client } = harness();
  const item = signal("channels", 40, { notification_status: "sent", bark_notification_status: "failed", is_fresh: false });
  assert.equal(client.notification(item, "telegram")[1], "sent");
  assert.equal(client.notification(item, "bark")[1], "failed");
  item.notification_status = "unknown";
  item.bark_notification_status = "sent";
  const markup = client.notificationHTML(item);
  assert.match(markup, /TG<\/span><span>回执未知/);
  assert.match(markup, /Bark<\/span><span>服务已接受/);
  assert.match(markup, /不代表手机已收到或已读/);
  assert.doesNotMatch(markup, /Bark<\/span><span>已送达/);
});

test("card markup escapes untrusted symbol/id/text and preserves full price precision", () => {
  const { client, get } = harness();
  const payload = 'BAD"><img src=x onerror="alert(1)">&\'';
  client.state.signals = [signal(payload, 1, { symbol: payload, price: 0.000000123456, near_zero_bars: '<script>alert(1)</script>' })];
  client.renderSignals();
  const markup = get("signal-rows").innerHTML;
  assert.doesNotMatch(markup, /<img|<script|data-signal-id="BAD">/);
  assert.match(markup, /&lt;img/);
  assert.match(markup, /&quot;/);
  assert.match(markup, /0\.000000123456/);
  assert.deepEqual(ids(get("signal-rows")), [payload]);
});

test("watch building excludes erroneous, stale and unready rows even when counters are high", () => {
  const { client } = harness();
  const item = { symbol: "ETH-USDT-SWAP", timeframe: "4H", near_zero_bars: 44, focus: true, ready: true };
  assert.equal(client.isBuilding(item), true);
  for (const change of [{ error: "fetch_failed" }, { stale: true }, { ready: false }]) assert.equal(client.isBuilding({ ...item, ...change }), false);
  assert.equal(client.isBuilding({ ...item, focus: false, near_zero_bars: 0, phase: "building" }), false, "a phase string alone cannot invent near-zero accumulation");
});

test("watch cards paginate separately, keep real counters and show unknown values honestly", () => {
  const { client, get } = harness();
  client.state.markets = Array.from({ length: 51 }, (_, index) => ({ symbol: `COIN${String(index).padStart(2, "0")}-USDT-SWAP`, timeframe: "1H", near_zero_bars: 51 - index, zero_bars: 0, ready: true, phase: "building", price: 1 + index }));
  client.renderWatch();
  assert.equal(client.state.watchLimit, 24);
  assert.equal(get("watch-rows").querySelectorAll("[data-market-symbol]").length, 24);
  assert.match(get("watch-count").textContent, /51/);
  get("load-more-watch").dispatch("click");
  assert.equal(get("watch-rows").querySelectorAll("[data-market-symbol]").length, 48);
  get("load-more-watch").dispatch("click");
  const cards = get("watch-rows").querySelectorAll("[data-market-symbol]");
  assert.equal(cards.length, 51);
  assert.equal(new Set(cards.map((card) => `${card.dataset.marketSymbol}|${card.dataset.marketTimeframe}`)).size, 51);
  assert.equal(client.state.rowLimit, 24, "watch pagination must not change signal pagination");
  assert.equal(get("load-more-watch").classList.contains("hidden"), true);
  client.state.watchScope = "all";
  client.state.markets = [{ symbol: "UNKNOWN-USDT-SWAP", timeframe: "4H", phase: "loading", ready: false }];
  client.renderWatch();
  assert.match(get("watch-rows").innerHTML, /数据预热/);
  assert.match(get("watch-rows").innerHTML, /—/);
  assert.doesNotMatch(get("watch-rows").innerHTML, /NaN|undefined|null/);
});

test("unknown freshness metadata is labelled pending rather than inventing a minute budget", () => {
  for (const change of [{ runtime: { signal_kind: "yolo_confirmed" } }, { now_ms: null }, { protocol: "old" }]) {
    const { client, get } = harness();
    client.state.status = { ...client.state.status, ...change };
    client.state.signals = [signal("cached")];
    assert.equal(client.isFresh(client.state.signals[0]), false);
    client.renderSignals();
    assert.match(get("signal-rows").innerHTML, /新鲜状态待同步/);
    assert.doesNotMatch(get("signal-rows").innerHTML, /— 分钟|NaN 分钟|undefined 分钟/);
  }
  const { client, get } = harness();
  client.state.statusReceivedAt = null;
  client.state.signals = [signal("cached")];
  assert.equal(client.isFresh(client.state.signals[0]), false);
  client.renderSignals();
  assert.match(get("signal-rows").innerHTML, /新鲜状态待同步/);
});

test("an empty filter clears stale detail and aborts its request; clearing the filter restores one selected chart", async () => {
  const { client, get, networkCalls } = harness({ allowChartFixture: true });
  let aborted = 0;
  const chosen = signal("first");
  client.state.signals = [chosen, signal("second", 5, { symbol: "ETH-USDT-SWAP" })];
  client.state.selected = { ...chosen };
  client.state.chartController = { abort() { aborted++; } };
  client.state.chartRequest = 9;
  client.state.chartKey = "old-chart";
  client.state.chart = { candles: [] };
  client.state.rowLimit = 72;
  get("symbol-search").value = "NO_SUCH_SYMBOL";
  get("symbol-search").dispatch("input");
  assert.equal(client.state.selected, null);
  assert.equal(client.state.chartKey, null);
  assert.equal(client.state.chart, null);
  assert.equal(client.state.chartRequest, 10);
  assert.equal(aborted, 1);
  assert.equal(client.state.rowLimit, 24);
  assert.equal(get("detail-content").classList.contains("hidden"), true);
  assert.equal(get("back-to-signals").classList.contains("hidden"), true);
  assert.deepEqual(ids(get("signal-rows")), []);
  get("symbol-search").value = "";
  get("symbol-search").dispatch("input");
  await new Promise(setImmediate);
  assert.equal(client.state.selected.id, "first");
  assert.equal(get("detail-content").classList.contains("hidden"), false);
  assert.equal(get("signal-rows").querySelectorAll("[data-preview-signal-id]")[0].getAttribute("aria-pressed"), "true");
  assert.equal(networkCalls.length, 1);
  assert.match(networkCalls[0], /symbol=BTC-USDT-SWAP&timeframe=1H/);
});

test("timeframe and direction controls replace an incompatible selection and reset only signal pagination", async () => {
  const { client, document, get, networkCalls } = harness({ allowChartFixture: true });
  client.state.signals = [signal("hour"), signal("quarter", 3, { timeframe: "15m", side: "short" })];
  client.state.selected = { ...client.state.signals[0] };
  client.state.rowLimit = 72;
  client.state.watchLimit = 48;
  document.querySelectorAll("[data-timeframe]").find((button) => button.dataset.timeframe === "15m").dispatch("click");
  await new Promise(setImmediate);
  assert.equal(client.state.timeframe, "15m");
  assert.equal(client.state.selected.id, "quarter");
  assert.equal(client.state.rowLimit, 24);
  assert.equal(client.state.watchLimit, 48);
  assert.deepEqual(ids(get("signal-rows")), ["quarter"]);
  get("side-filter").value = "long";
  get("side-filter").dispatch("change");
  assert.equal(client.state.selected, null);
  assert.deepEqual(ids(get("signal-rows")), []);
  assert.equal(networkCalls.length, 1);
});

test("watch refresh failure keeps the cached row but hides its current-state claims", () => {
  const { client, get } = harness();
  const item = { symbol: "ETH-USDT-SWAP", timeframe: "4H", near_zero_bars: 44, zero_bars: 20, dense: true, focus: true, ready: true, phase: "ready", htf_side: "long" };
  client.state.markets = [item];
  client.state.errors.markets = "offline";
  client.renderWatch();
  const markup = get("watch-rows").innerHTML;
  assert.equal(get("watch-rows").querySelectorAll("[data-market-symbol]").length, 1);
  assert.match(markup, /缓存 · 待同步/);
  assert.doesNotMatch(markup, /已达蓄势门槛|>44[ <]|>已密集<|>多头</);
  assert.equal(item.near_zero_bars, 44, "display degradation does not rewrite cached market facts");
  delete client.state.errors.markets;
  client.renderWatch();
  assert.match(get("watch-rows").innerHTML, /已达蓄势门槛/);
});

test("watch rerender restores focus by both symbol and timeframe and search resets its page", () => {
  const { client, document, get } = harness();
  client.state.markets = ["1H", "4H"].map((timeframe) => ({ symbol: "ETH-USDT-SWAP", timeframe, near_zero_bars: 20, ready: true }));
  client.state.watchLimit = 72;
  client.renderWatch();
  get("watch-rows").querySelectorAll("[data-market-symbol]").find((card) => card.dataset.marketTimeframe === "4H").focus();
  client.renderWatch();
  assert.equal(document.activeElement.dataset.marketSymbol, "ETH-USDT-SWAP");
  assert.equal(document.activeElement.dataset.marketTimeframe, "4H");
  assert.equal(document.activeElement.focusOptions.preventScroll, true);
  get("watch-search").value = "eth";
  get("watch-search").dispatch("input");
  assert.equal(client.state.watchLimit, 24);
  assert.equal(get("watch-rows").querySelectorAll("[data-market-symbol]").length, 2);
});

test("previewing a watch card and returning keeps observation provenance and restores preview focus", async () => {
  const { client, document, get, networkCalls } = harness({ allowChartFixture: true });
  client.state.view = "watch";
  client.state.markets = [{ symbol: "ETH-USDT-SWAP", timeframe: "4H", near_zero_bars: 44, phase: "ready", focus: true, ready: true, price: 100 }];
  client.renderWatch();
  const card = get("watch-rows").querySelectorAll("[data-market-symbol]")[0];
  get("watch-rows").dispatch("click", { target: card });
  await new Promise(setImmediate);
  assert.equal(client.state.view, "signals");
  assert.equal(client.state.detailOrigin, "watch");
  assert.equal(client.state.selected.kind, undefined, "watch selection never becomes a synthetic tv_start");
  assert.match(get("detail-price-caption").textContent, /观察结构/);
  assert.match(get("back-to-signals").textContent, /返回蓄势观察/);
  assert.equal(networkCalls.length, 1);
  get("back-to-signals").dispatch("click");
  assert.equal(client.state.view, "watch");
  assert.equal(document.activeElement.dataset.marketSymbol, card.dataset.marketSymbol);
  assert.equal(document.activeElement.dataset.marketTimeframe, card.dataset.marketTimeframe);
  assert.equal(document.activeElement.focusOptions.preventScroll, true);
});

function bridgeResponse(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, headers: { get: () => "application/json" }, json: async () => body };
}

function market(symbol = "ETH-USDT-SWAP", timeframe = "4H") {
  return { symbol, timeframe, near_zero_bars: 44, focus: true, ready: true, phase: "ready", price: 100 };
}

test("signal card primary clicks select the exact record and send one matching app request", async () => {
  for (const kind of ["confirmed", "pending"]) {
    const symbol = "BTC-USD-SWAP", timeframe = "15m";
    const { client, get, networkRequests } = harness({ allowChartFixture: true, bridgeReply: bridgeResponse({ requested: true, symbol, timeframe }) });
    client.state.signals = [signal("same-id", 1, { symbol, timeframe })];
    client.state.candidates = [candidate("same-id", "pending", { symbol, timeframe })];
    client.state.signalScope = kind;
    client.renderSignals();
    const primary = get("signal-rows").querySelectorAll("[data-signal-id]")[0];
    get("signal-rows").dispatch("click", { target: primary });
    await new Promise(setImmediate);
    assert.equal(client.state.selected.kind, kind === "confirmed" ? "yolo_confirmed" : "tv_start");
    assert.equal(client.state.selected.id, "same-id");
    const posts = networkRequests.filter((request) => request.method === "POST");
    assert.equal(posts.length, 1);
    assert.deepEqual(JSON.parse(posts[0].body), { symbol, timeframe });
    assert.equal(client.state.view, "signals");
    assert.match(get("detail-timeframe").textContent, /15m/);
  }
});

test("card body content and blank card surface use the same primary action in both feeds", async () => {
  for (const feed of ["signal", "watch"]) for (const surface of ["content", "blank"]) {
    const symbol = "ETH-USDT-SWAP", timeframe = "4H";
    const { client, get, networkRequests } = harness({ allowChartFixture: true, bridgeReply: bridgeResponse({ requested: true, symbol, timeframe }) });
    client.state.signals = [signal("body", 1, { symbol, timeframe })];
    client.state.markets = [market(symbol, timeframe)];
    client.renderSignals(); client.renderWatch();
    const rows = get(`${feed}-rows`);
    const target = surface === "blank" ? rows.querySelector(`.${feed}-card`) : rows.querySelector(".card-symbol").querySelector("strong");
    rows.dispatch("click", { target });
    await new Promise(setImmediate);
    const posts = networkRequests.filter((request) => request.method === "POST");
    assert.equal(posts.length, 1, `${feed} ${surface}`);
    assert.deepEqual(JSON.parse(posts[0].body), { symbol, timeframe });
  }
});

test("Enter and Space activate each primary card once and cancel native duplicate clicks", async () => {
  for (const feed of ["signal", "watch"]) for (const key of ["Enter", " "]) {
    const symbol = "ETH-USDT-SWAP", timeframe = "1H";
    const { client, get, networkRequests } = harness({ allowChartFixture: true, bridgeReply: bridgeResponse({ requested: true, symbol, timeframe }) });
    client.state.signals = [signal("keyboard", 1, { symbol, timeframe })];
    client.state.markets = [market(symbol, timeframe)];
    client.renderSignals(); client.renderWatch();
    const rows = get(`${feed}-rows`);
    let primary = rows.querySelector("[data-tradingview-action]");
    primary.focus();
    const unrelated = rows.dispatch("keydown", { target: primary, key: "ArrowDown" });
    assert.equal(unrelated.defaultPrevented, false);
    const activation = rows.dispatch("keydown", { target: primary, key });
    assert.equal(activation.defaultPrevented, true, "native button click and Space scrolling must be cancelled");
    await new Promise(setImmediate);
    primary = rows.querySelector("[data-tradingview-action]");
    const repeat = rows.dispatch("keydown", { target: primary, key, repeat: true });
    assert.equal(repeat.defaultPrevented, true);
    assert.equal(networkRequests.filter((request) => request.method === "POST").length, 1, `${feed}: ${key}`);
    assert.equal(client.state.selected.symbol, symbol);
  }
});

test("nested preview clicks and native preview keyboard activation never launch TradingView", async () => {
  for (const feed of ["signal", "watch"]) {
    const { client, get, networkRequests } = harness({ allowChartFixture: true });
    client.state.signals = [signal("preview")];
    client.state.markets = [market()];
    client.renderSignals(); client.renderWatch();
    const rows = get(`${feed}-rows`);
    const preview = rows.querySelector(".card-preview");
    assert.equal(preview.tagName, "BUTTON");
    assert.equal(preview.parent.closest("button"), null, "preview must not be nested inside a primary button");
    const keyboard = rows.dispatch("keydown", { target: preview, key: " " });
    assert.equal(keyboard.defaultPrevented, false, "preview keeps native keyboard activation");
    rows.dispatch("click", { target: preview.querySelector("span") });
    await new Promise(setImmediate);
    assert.equal(client.state.selected.symbol, feed === "signal" ? "BTC-USDT-SWAP" : "ETH-USDT-SWAP");
    assert.equal(client.state.view, "signals");
    assert.equal(networkRequests.filter((request) => request.method === "POST").length, 0);
    assert.equal(networkRequests.filter((request) => request.url.startsWith("/api/chart?")).length, 1);
  }
});

test("whole watch cards select and open their own symbol and period, preserving watch filters", async () => {
  for (const [symbol, timeframe] of [["BTC-USDT-SWAP", "15m"], ["ETH-USDT-SWAP", "1H"], ["BTC-USD-SWAP", "4H"],
    ["ETH-USDC-SWAP", "1H"], ["A-BC-SWAP", "15m"], [`${"A".repeat(30)}-${"B".repeat(10)}-SWAP`, "4H"]]) {
    const { client, get, networkRequests } = harness({ allowChartFixture: true, bridgeReply: bridgeResponse({ requested: true, symbol, timeframe }) });
    client.state.view = "watch";
    client.state.selected = signal("unrelated", 1, { symbol: "OTHER-USDT-SWAP" });
    client.state.watchSearch = symbol.split("-")[0];
    client.state.watchTimeframe = timeframe;
    client.state.watchLimit = 48;
    client.state.markets = [market(symbol, timeframe)];
    client.renderWatch();
    const before = JSON.stringify({ search: client.state.search, timeframe: client.state.timeframe, watchLimit: client.state.watchLimit, watchSearch: client.state.watchSearch, watchTimeframe: client.state.watchTimeframe });
    const opener = get("watch-rows").querySelectorAll("[data-tradingview-action]")[0];
    const event = get("watch-rows").dispatch("click", { target: opener });
    assert.equal(event.defaultPrevented, true);
    assert.equal(client.state.tradingViewPending, true);
    assert.equal(client.state.syncing, false, "app opening must not block market refresh");
    assert.equal(get("watch-rows").querySelectorAll("[data-tradingview-action]")[0].disabled, true);
    await new Promise(setImmediate);
    const posts = networkRequests.filter((request) => request.method === "POST");
    assert.equal(posts.length, 1);
    const request = posts[0];
    assert.equal(request.url, "/api/tradingview/open");
    assert.equal(request.method, "POST");
    assert.equal(request.headers["Content-Type"], "application/json");
    assert.equal(request.headers["X-Spike-Action"], "open-tradingview");
    assert.deepEqual(JSON.parse(request.body), { symbol, timeframe });
    assert.equal(client.state.view, "watch");
    assert.equal(client.state.selected.symbol, symbol);
    assert.equal(client.state.selected.timeframe, timeframe);
    assert.equal(client.state.detailOrigin, "watch");
    assert.equal(JSON.stringify({ search: client.state.search, timeframe: client.state.timeframe, watchLimit: client.state.watchLimit, watchSearch: client.state.watchSearch, watchTimeframe: client.state.watchTimeframe }), before);
    assert.equal(client.state.tradingViewPending, false);
    assert.equal(get("watch-rows").querySelectorAll("[data-tradingview-action]")[0].disabled, false);
    assert.match(get("tradingview-status").textContent, /已请求 TradingView 打开/);
    assert.doesNotMatch(get("tradingview-status").textContent, /已成功|已经打开|已切换/);
    assert.equal(get("tradingview-status").getAttribute("role"), "status");
  }
});

test("an app request prevents double clicks across cards and keeps refreshed actions busy", async () => {
  let resolve;
  const pending = new Promise((done) => { resolve = done; });
  const { client, get, networkRequests } = harness({ allowChartFixture: true, bridgeReply: () => pending });
  client.state.markets = [market(), market("BTC-USDT-SWAP", "15m")];
  client.state.signals = [signal("busy-signal")];
  client.renderSignals();
  client.renderWatch();
  get("watch-rows").dispatch("click", { target: get("watch-rows").querySelectorAll("[data-tradingview-action]")[0] });
  const request = JSON.parse(networkRequests.find((request) => request.method === "POST").body);
  client.renderWatch();
  const buttons = get("watch-rows").querySelectorAll("[data-tradingview-action]");
  assert.equal(buttons.length, 2);
  assert.ok(buttons.every((button) => button.disabled));
  get("watch-rows").dispatch("click", { target: buttons[1] });
  get("signal-rows").dispatch("keydown", { target: get("signal-rows").querySelectorAll("[data-signal-id]")[0], key: "Enter" });
  get("tradingview-open").dispatch("click");
  assert.equal(client.state.selected.symbol, request.symbol, "ignored concurrent activation must not replace the selected card");
  assert.equal(networkRequests.filter((request) => request.method === "POST").length, 1);
  assert.ok(get("signal-rows").querySelectorAll("[data-tradingview-action]").every((button) => button.disabled));
  assert.ok(get("watch-rows").querySelectorAll("[data-tradingview-label]").every((label) => label.textContent === "正在打开…"));
  resolve(bridgeResponse({ requested: true, ...request }));
  await new Promise(setImmediate);
  assert.ok(buttons.every((button) => !button.disabled));
});

test("bridge errors remain visible verbatim as text and permit a later manual retry", async () => {
  for (const status of [400, 403, 409, 503]) {
    const detail = `本机打开失败 ${status} <script>not markup</script>`;
    const { client, get, networkRequests } = harness({ bridgeReply: bridgeResponse({ detail }, status) });
    client.state.markets = [market()];
    client.renderWatch();
    const button = get("watch-rows").querySelectorAll("[data-tradingview-action]")[0];
    get("watch-rows").dispatch("click", { target: button });
    await new Promise(setImmediate);
    assert.equal(get("tradingview-status").textContent, `无法请求 TradingView：${detail}`);
    assert.equal(get("tradingview-status").innerHTML, "", "error detail must never be assigned as HTML");
    assert.equal(get("tradingview-status").classList.contains("error"), true);
    assert.equal(get("tradingview-status").classList.contains("hidden"), false);
    assert.equal(button.disabled, false);
    get("watch-rows").dispatch("click", { target: button });
    await new Promise(setImmediate);
    assert.equal(networkRequests.filter((request) => request.method === "POST").length, 2);
  }
});

test("a mismatched success receipt does not claim the requested chart was opened", async () => {
  const { client, get } = harness({ bridgeReply: bridgeResponse({ requested: true, symbol: "WRONG-USDT-SWAP", timeframe: "4H" }) });
  client.state.markets = [market()];
  client.renderWatch();
  get("watch-rows").dispatch("click", { target: get("watch-rows").querySelectorAll("[data-tradingview-action]")[0] });
  await new Promise(setImmediate);
  assert.match(get("tradingview-status").textContent, /未返回有效/);
  assert.equal(get("tradingview-status").classList.contains("error"), true);
});

test("detail app action shares the bridge and retains an explicit web fallback", async () => {
  const { client, get, networkRequests } = harness({ allowChartFixture: true, bridgeReply: bridgeResponse({ requested: true, symbol: "ETH-USDT-SWAP", timeframe: "15m" }) });
  client.state.selected = signal("detail", 1, { symbol: "ETH-USDT-SWAP", timeframe: "15m" });
  client.renderDetail();
  await new Promise(setImmediate);
  assert.equal(get("tradingview-open").tagName, "BUTTON");
  assert.match(fs.readFileSync(indexPath, "utf8"), /data-tradingview-label="在 TradingView 打开">在 TradingView 打开<\/span>/);
  assert.equal(get("tradingview-web").tagName, "A");
  assert.match(get("tradingview-web").href, /symbol=OKX%3AETHUSDT.P&interval=15$/);
  assert.equal(get("tradingview-web").getAttribute("target"), "_blank");
  assert.equal(networkRequests.filter((request) => request.method === "POST").length, 0);
  get("tradingview-open").dispatch("click");
  await new Promise(setImmediate);
  const posts = networkRequests.filter((request) => request.method === "POST");
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(posts[0].body), { symbol: "ETH-USDT-SWAP", timeframe: "15m" });
});

test("rendering, polling and filter changes never send automatic app or notification posts", async () => {
  const { client, get, document, networkRequests } = harness({ allowChartFixture: true });
  client.state.markets = [market()];
  client.state.signals = [signal("passive")];
  client.renderWatch();
  client.renderSignals();
  get("watch-search").value = "ETH";
  get("watch-search").dispatch("input");
  document.querySelectorAll("[data-watch-timeframe]").find((button) => button.dataset.watchTimeframe === "4H").dispatch("click");
  await client.refresh();
  await new Promise(setImmediate);
  assert.ok(networkRequests.length > 0);
  assert.equal(networkRequests.filter((request) => request.method === "POST").length, 0);
  assert.ok(networkRequests.every((request) => !/tradingview\/open|notification|telegram|bark/.test(request.url)));
});

test("watch preview and app buttons are siblings, preserve one window count and action-specific focus", () => {
  const { client, document, get, networkRequests } = harness();
  client.state.markets = Array.from({ length: 27 }, (_, i) => market(`COIN${i}-USDT-SWAP`, i % 2 ? "1H" : "4H"));
  client.renderWatch();
  let previews = get("watch-rows").querySelectorAll("[data-market-symbol]");
  let apps = get("watch-rows").querySelectorAll("[data-tradingview-action]");
  assert.equal(previews.length, 24);
  assert.equal(apps.length, 24);
  assert.match(get("watch-count").textContent, /27 个窗口/);
  const html = get("watch-rows").innerHTML;
  assert.equal((html.match(/<article class="watch-card">/g) || []).length, 24);
  for (const match of html.matchAll(/<button\b[^>]*>([\s\S]*?)<\/button>/g)) assert.doesNotMatch(match[1], /<(?:button|a)\b/);
  apps[3].focus();
  const focused = { symbol: apps[3].dataset.tvSymbol, timeframe: apps[3].dataset.tvTimeframe };
  client.renderWatch();
  assert.equal(document.activeElement.dataset.tradingviewAction, "watch");
  assert.equal(document.activeElement.dataset.tvSymbol, focused.symbol);
  assert.equal(document.activeElement.dataset.tvTimeframe, focused.timeframe);
  get("load-more-watch").dispatch("click");
  previews = get("watch-rows").querySelectorAll("[data-market-symbol]");
  apps = get("watch-rows").querySelectorAll("[data-tradingview-action]");
  assert.equal(previews.length, 27);
  assert.equal(apps.length, 27);
  assert.equal(networkRequests.length, 0);
});

test("unsupported periods and malformed symbols cannot launch the app", () => {
  const { client, get, networkRequests } = harness();
  client.state.markets = [market("ETH-USDT-SWAP", "1Dutc"), market('BAD\"><script>', "1H"),
    ...["-USDC-SWAP", `${"A".repeat(31)}-USDC-SWAP`, "ETH-U-SWAP", `ETH-${"B".repeat(11)}-SWAP`, "eth-USDC-SWAP", "ETH-usdc-SWAP"]
      .map((symbol) => market(symbol, "1H"))];
  client.renderWatch();
  const buttons = get("watch-rows").querySelectorAll("[data-tradingview-action]");
  assert.ok(buttons.every((button) => button.disabled));
  for (const button of buttons) get("watch-rows").dispatch("click", { target: button });
  assert.equal(networkRequests.length, 0);
  assert.doesNotMatch(get("watch-rows").innerHTML, /<script>/);
  assert.match(get("tradingview-status").textContent, /不支持/);
});


function candidate(id, status = "pending", extra = {}) {
  return { ...signal(id), kind: "tv_start", protocol: TV_PROTOCOL, indicator: undefined,
    model: { status, wait_bars: 2, max_wait_bars: 9, last_checked_close_ms: NOW - 60_000, expires_at_ms: NOW + 7 * 3_600_000 }, ...extra };
}

test("the confirmed feed rejects raw arrows, wrong protocols and nonconfirmed model states", () => {
  const { client, get } = harness();
  const raw = candidate("arrow");
  const error = signal("error", 1, { model: { status: "error" } });
  const legacy = signal("legacy", 1, { protocol: TV_PROTOCOL });
  client.state.signals = [raw, error, legacy, signal("confirmed")];
  client.renderSignals();
  assert.deepEqual(ids(get("signal-rows")), ["confirmed"]);
  for (const item of [raw, error, legacy]) assert.equal(client.isFresh(item), false);
  assert.match(get("signal-rows").innerHTML, /模型确认收盘价/);
  assert.match(get("signal-rows").innerHTML, /原箭头 102.5/);
  assert.equal(client.state.signalScope, "confirmed");
});

test("candidate stages remain separate, errors stay visible and notification receipts cannot leak", () => {
  const { client, get } = harness();
  client.state.candidates = [candidate("pending"), candidate("error", "error"), candidate("expired", "expired"), candidate("invalid", "invalidated"), candidate("passed", "confirmed")];
  client.state.signals = [signal("confirmed")];
  client.state.signalTotal = 100;
  client.state.candidateTotal = 200;
  client.state.signalScope = "pending";
  client.renderSignals();
  assert.deepEqual(ids(get("signal-rows")), ["pending", "error"]);
  assert.equal(get("candidate-count").textContent, "2");
  assert.match(get("signal-rows").innerHTML, /检测异常/);
  assert.match(get("signal-rows").innerHTML, /原箭头收盘价/);
  assert.match(get("signal-rows").innerHTML, /候选记录 · 不触发通知/);
  assert.doesNotMatch(get("signal-rows").innerHTML, /新鲜确认|is-fresh|data-notification-channel/);
  assert.equal(client.state.signalTotal, 100);
  assert.match(get("signal-window-note").textContent, /共 200/);
  client.state.signalScope = "all";
  client.renderSignals();
  assert.deepEqual(ids(get("signal-rows")), ["pending", "error", "expired", "invalid", "passed"]);
  assert.match(get("signal-rows").innerHTML, /等待已到期|结构失效|模型已通过/);
});

test("scope controls select only their own collection and preserve both event clocks", async () => {
  const { client, get, document, networkRequests } = harness({ allowChartFixture: true });
  client.state.signals = [signal("same-id")];
  client.state.candidates = [candidate("same-id")];
  client.state.selected = { ...client.state.signals[0] };
  document.querySelectorAll("[data-signal-scope]").find((button) => button.dataset.signalScope === "pending").dispatch("click");
  await new Promise(setImmediate);
  assert.equal(client.state.selected.kind, "tv_start");
  assert.equal(get("detail-price-caption").textContent, "原箭头收盘价");
  assert.match(get("detail-facts").innerHTML, /等待截止|最近检测收盘/);
  document.querySelectorAll("[data-signal-scope]").find((button) => button.dataset.signalScope === "confirmed").dispatch("click");
  await new Promise(setImmediate);
  assert.equal(client.state.selected.kind, "yolo_confirmed");
  assert.match(get("detail-facts").innerHTML, /原箭头收盘价/);
  assert.match(get("detail-facts").innerHTML, /模型确认收盘价/);
  assert.match(get("detail-facts").innerHTML, /检测分数 · 非胜率/);
  assert.match(get("detail-facts").innerHTML, /102.5|105.25/);
  assert.equal(networkRequests.filter((request) => request.method === "POST").length, 0);
});

test("freshness starts at model confirmation, never at an older original arrow", () => {
  const { client } = harness();
  const item = signal("late", 1);
  item.indicator.bar_close_ms = NOW - 24 * 3_600_000;
  assert.equal(client.isFresh(item), true);
  item.bar_close_ms = NOW - 31 * 60_000;
  item.indicator.bar_close_ms = NOW - 60_000;
  assert.equal(client.isFresh(item), false);
});

test("model overlay anchors original core and confirmation without using subsequent extrema", () => {
  const { client } = harness();
  const item = signal("draw", 0, { bar_open_ms: 300, bar_close_ms: 400, price: 12,
    model: { status: "confirmed", core_start_ms: 100, core_end_ms: 200, window_end_ms: 300, wait_bars: 2 } });
  const candles = [{ t: 100, h: 11, l: 9 }, { t: 200, h: 12, l: 8 }, { t: 300, h: 15, l: 10 }, { t: 400, h: 90, l: 1 }];
  const bounds = { step: 10, top: 0, bottom: 80 };
  const before = client.modelOverlayHTML(item, candles, (i) => i * 10 + 5, (price) => 100 - price, bounds);
  assert.match(before.core, /x="0" y="88" width="20" height="4"/);
  assert.match(before.confirmation, /x1="25" x2="25"/);
  candles[3].h = 900;
  const after = client.modelOverlayHTML(item, candles, (i) => i * 10 + 5, (price) => 100 - price, bounds);
  assert.equal(before.core, after.core);
  assert.equal(client.modelOverlayHTML(candidate("raw"), candles, () => 0, () => 0, bounds).core, "");
  item.model.core_end_ms = 500;
  assert.equal(client.modelOverlayHTML(item, candles, () => 0, () => 0, bounds).confirmation, "");
});


test("polling requests separate APIs, rejects mixed event payloads and keeps independent totals", async () => {
  const replies = {
    "/api/status": { protocol: PROTOCOL, now_ms: NOW, runtime: { signal_kind: "yolo_confirmed", fresh_minutes: 30, model_gate: { loaded: true } } },
    "/api/signals?limit=2000&kind=yolo_confirmed": { items: [candidate("raw"), signal("model")], total: 17 },
    "/api/candidates?limit=2000": { items: [candidate("pending"), signal("not-a-candidate")], total: 43 },
    "/api/markets": { items: [] },
  };
  const { client, get, networkRequests } = harness({ allowChartFixture: true, apiReplies: replies });
  await client.refresh();
  await new Promise(setImmediate);
  assert.deepEqual(Array.from(client.state.signals, (item) => item.id), ["model"]);
  assert.deepEqual(Array.from(client.state.candidates, (item) => item.id), ["pending"]);
  assert.equal(client.state.signalTotal, 17);
  assert.equal(client.state.candidateTotal, 43);
  assert.equal(get("model-gate-notice").classList.contains("hidden"), true);
  assert.equal(client.state.selected.id, "model", "initial refresh may select a preview without opening the app");
  assert.equal(networkRequests.filter((request) => request.method === "POST").length, 0);
  replies["/api/candidates?limit=2000"] = new Error("temporarily offline");
  await client.refresh();
  assert.equal(client.state.candidates[0].id, "pending", "candidate error preserves its cache");
  assert.equal(client.isFresh(client.state.signals[0]), true, "unrelated candidate API failure cannot invalidate a confirmed timestamp");
  assert.match(get("error-notice").textContent, /指标候选/);
});
