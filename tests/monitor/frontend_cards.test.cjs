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
const PROTOCOL = "imacd-tv-visible-start-monitor-v3";

function decode(value) {
  return String(value).replace(/&(amp|lt|gt|quot|#39);/g, (_, name) =>
    ({ amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" })[name]);
}

function attributes(text) {
  const result = {};
  for (const match of text.matchAll(/([\w:-]+)="([^"]*)"/g)) result[match[1]] = decode(match[2]);
  return result;
}

function harness({ allowChartFixture = false, nowStep = 0 } = {}) {
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
      this.descendants = Array.from(this._html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g), (match) => {
        const child = new Element("BUTTON", attributes(match[1]));
        child.parent = this;
        child._html = match[2];
        return child;
      });
      // Group wrappers mean cards are descendants, not direct children. This
      // catches the old table-row focus-restoration approach after regrouping.
      this.children = /^\s*<button\b/.test(this._html) ? this.descendants : this._html ? [new Element("DIV")] : [];
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
      const event = { type: name, target: this, preventDefault() {}, ...extra };
      for (const listener of this.listeners.get(name) || []) listener(event);
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
  const context = vm.createContext({
    document, Date: ClockDate, Intl, console, AbortController, setTimeout, clearTimeout,
    window: { innerWidth: 1280, addEventListener() {}, matchMedia: () => ({ matches: true }) },
    location: { hash: "#signals" }, history: { replaceState() {} },
    fetch: (url) => {
      networkCalls.push(url);
      if (allowChartFixture && url.startsWith("/api/chart?")) {
        return Promise.resolve({ ok: true, headers: { get: () => "application/json" }, json: async () => ({ candles: [] }) });
      }
      return Promise.reject(new Error("Network is forbidden in this unit test"));
    },
  });
  const source = fs.readFileSync(clientPath, "utf8");
  const bootstrap = '  setView(location.hash.slice(1) || "signals", false);';
  assert.equal(source.split(bootstrap).length, 2, "client bootstrap must be uniquely identified");
  const exposed = ["state", "isFresh", "filteredSignals", "notification", "notificationHTML", "renderSignals", "isBuilding", "renderWatch"];
  vm.runInContext(source.slice(0, source.indexOf(bootstrap)) + `\n  globalThis.client = { ${exposed.join(", ")} };\n})();`, context, { filename: clientPath });
  const client = context.client;
  client.state.status = { protocol: PROTOCOL, now_ms: NOW, runtime: { signal_kind: "tv_start", fresh_minutes: 30 } };
  client.state.statusReceivedAt = NOW;
  client.state.signalsLoaded = true;
  client.state.marketsLoaded = true;
  return { client, document, get: (id) => elements.get(id), advance: (ms) => { now += ms; }, networkCalls };
}

function signal(id, ageMinutes = 1, extra = {}) {
  return {
    id: String(id), symbol: "BTC-USDT-SWAP", timeframe: "1H", kind: "tv_start", protocol: PROTOCOL,
    side: "long", price: 105.25, bar_close_ms: NOW - ageMinutes * 60_000,
    near_zero_bars: 44, zero_bars: 9, is_fresh: true,
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
  client.state.status = { ...status, runtime: { signal_kind: "tv_start" } };
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
  assert.doesNotMatch(get("signal-rows").innerHTML, /更早启动|已记录启动/);
  client.renderSignals();
  assert.deepEqual(ids(get("signal-rows")), ["boundary"]);
  assert.doesNotMatch(get("signal-rows").innerHTML, /fresh-heading|class="signal-card[^\"]*\bis-fresh\b/);
  assert.match(get("signal-rows").innerHTML, /已记录启动/);
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
  assert.equal(first.getAttribute("aria-pressed"), "true");
  first.focus();
  client.renderSignals();
  const replacement = get("signal-rows").querySelectorAll("[data-signal-id]")[0];
  assert.notEqual(first, replacement);
  assert.equal(document.activeElement, replacement);
  assert.equal(document.activeElement.focusOptions.preventScroll, true);
  assert.equal(get("signal-rows").listeners.has("keydown"), false, "native button activation should not be duplicated by a keydown handler");
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
  for (const change of [{ runtime: { signal_kind: "tv_start" } }, { now_ms: null }, { protocol: "old" }]) {
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
  assert.equal(get("signal-rows").querySelectorAll("[data-signal-id]")[0].getAttribute("aria-pressed"), "true");
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

test("opening a watch card and returning keeps observation provenance and restores its card focus", async () => {
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
  assert.equal(document.activeElement, card);
  assert.equal(document.activeElement.focusOptions.preventScroll, true);
});
