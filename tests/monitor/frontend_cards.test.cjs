"use strict";

// Run with: node --test tests/monitor/frontend_cards.test.cjs
// These are contract fixtures for the static SPIKE V1 client. They intentionally
// use no HTTP service or market data: browser QA runs once the worker API is ready.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const app = fs.readFileSync(path.join(root, "yoyo/monitor/static/app.js"), "utf8");
const page = fs.readFileSync(path.join(root, "yoyo/monitor/static/index.html"), "utf8");

const rawLive = Object.freeze({
  id: "raw-live-pepe-30-1", source: "live", confirmation: "raw", timeframe_min: 30,
  signal_close_time: "2026-09-11T01:00:00Z", executable_entry_time: null,
  source_sha256: "a".repeat(64), venue: "okx", symbol: "PEPE-USDT-SWAP",
  direction: "long", risk: 0.000000094, initial_stop: 0.000002620,
  is_closed: true, entry_reference: "next_open", signal_close: 0.000002714,
});
const yoloLive = Object.freeze({
  ...rawLive, id: "yolo-live-pepe-60-2", confirmation: "yolo", timeframe_min: 60,
  executable_entry_time: "2026-09-11T02:00:00Z",
});
const rawLive15m = Object.freeze({
  ...rawLive, id: "raw-live-pepe-15-4", timeframe_min: 15,
});
const rawReplay = Object.freeze({
  ...rawLive, id: "raw-replay-pepe-240-3", source: "replay", timeframe_min: 240,
  executable_entry_time: null,
});
const pending = Object.freeze({ ...rawLive, id: "pending", is_closed: false });


function refreshHarness(fetchImpl) {
  const cutoff = app.indexOf("  function redact(value)");
  assert.ok(cutoff > 0, "refresh harness must cut before browser event bindings");
  const instrumented = `${app.slice(0, cutoff)}
  renderErrors = renderStatus = renderSignals = renderWatch = () => {};
  globalThis.__refreshHarness = { state, refresh, queueRefresh, loadEarlierRawSignals, invalidateSignalQuery, performanceView };
})();`;
  const classList = { add() {}, remove() {}, toggle() {} };
  const element = { disabled: false, classList, textContent: "", innerHTML: "", style: {}, setAttribute() {}, removeAttribute() {} };
  const sandbox = {
    AbortController, Date, Intl, Map, Set, Promise, Number, String, Boolean,
    Array, Object, Math, RegExp, Error, TypeError, JSON, encodeURIComponent,
    fetch: fetchImpl, setTimeout, clearTimeout,
    document: { getElementById: () => element, querySelectorAll: () => [] },
  };
  vm.runInNewContext(instrumented, sandbox, { filename: "app-refresh-harness.js" });
  return sandbox.__refreshHarness;
}

function jsonResponse(value) {
  return { ok: true, headers: { get: () => "application/json" }, json: async () => value };
}

function cardHarness() {
  const cutoff = app.indexOf("  function redact(value)");
  assert.ok(cutoff > 0, "card harness must cut before browser event bindings");
  const instrumented = `${app.slice(0, cutoff)}
  globalThis.__cardHarness = { state, normalizeV1Event, signalCardHTML, performanceView };
})();`;
  const sandbox = {
    AbortController, Date, Intl, Map, Set, Promise, Number, String, Boolean,
    Array, Object, Math, RegExp, Error, TypeError, JSON, encodeURIComponent,
    document: { getElementById: () => ({}), querySelectorAll: () => [] },
  };
  vm.runInNewContext(instrumented, sandbox, { filename: "app-card-harness.js" });
  return sandbox.__cardHarness;
}

function consume(row) {
  if (!row || typeof row !== "object") throw new TypeError("API item must be an object");
  const timeframe = Number(row.timeframe_min);
  if (!Number.isInteger(timeframe) || ![5, 15, 30, 60, 240, 1440].includes(timeframe)) throw new TypeError("unsupported timeframe");
  if (!["live", "replay"].includes(row.source)) throw new TypeError("unknown source");
  if (!["raw", "yolo", "raw_yolo"].includes(row.confirmation)) throw new TypeError("unknown confirmation");
  if (!["long", "short"].includes(row.direction)) throw new TypeError("unknown direction");
  const close = Date.parse(row.signal_close_time);
  if (!Number.isFinite(close)) throw new TypeError("signal close must be UTC parseable");
  return {
    timeframe,
    source: row.source,
    confirmation: row.confirmation,
    closed: row.is_closed === true,
    executableEntry: row.executable_entry_time ? Date.parse(row.executable_entry_time) : null,
    notification: row.source === "replay" ? "muted" : "receipt-required",
    close,
  };
}

test("V1 page exposes live/replay and 15m/30m/1H/4H signal controls", () => {
  const filters = page.match(/aria-label="信号周期"([\s\S]*?)<\/div>/)?.[1] || "";
  assert.match(filters, /data-timeframe="15"/);
  assert.match(filters, /data-timeframe="30"/);
  assert.match(filters, /data-timeframe="60"/);
  assert.match(filters, /data-timeframe="240"/);
  assert.doesNotMatch(filters, /data-timeframe="5"|1Dutc/);
  assert.match(page, /data-signal-source="live"/);
  assert.match(page, /data-signal-source="replay"/);
  assert.match(page, /原版 V1 · 仅多头 · 已收盘/);
});

test("real-time raw V1 fixture remains separate from YOLO supplemental confirmation", () => {
  const raw = consume(rawLive);
  const yolo = consume(yoloLive);
  assert.deepEqual(raw, {
    timeframe: 30, source: "live", confirmation: "raw", closed: true,
    executableEntry: null, notification: "receipt-required", close: Date.parse(rawLive.signal_close_time),
  });
  assert.equal(yolo.confirmation, "yolo");
  assert.equal(yolo.executableEntry, Date.parse(yoloLive.executable_entry_time));
  assert.match(app, /confirmation=yolo/);
  assert.match(app, /confirmation=raw/);
  assert.match(app, /confirmation=raw_yolo/);
  assert.match(app, /YOLO 是原始 V1 之后的补充确认，不是启动门/);
  assert.match(app, /confirmed \? item\?\.source === "live"/);
  assert.doesNotMatch(app, /imacd-yolo-confirmation-monitor-v1/);
});

test("replay fixture is labeled historical and never inherits a live notification", () => {
  const replay = consume(rawReplay);
  assert.equal(replay.source, "replay");
  assert.equal(replay.notification, "muted");
  assert.equal(replay.executableEntry, null);
  assert.match(app, /历史回放不通知/);
  assert.match(app, /source=\$\{source\}/);
  assert.doesNotMatch(page, /detail-panel|chart-dialog|页内预览/);
});

test("cards distinguish active profit and stopped loss using signal-path R", () => {
  const { state, normalizeV1Event, signalCardHTML } = cardHarness();
  state.status = { now_ms: Date.now(), runtime: { notification_channels: [] } };
  state.statusReceivedAt = Date.now();
  const active = normalizeV1Event({ ...rawLive, performance: {
    status: "active", current_r: 2.35, peak_r: 3.1, stop_price: 0.0000027,
    trailing_active: true, bars_held: 12,
  } }, "live");
  const loss = normalizeV1Event({ ...rawLive, id: "loss", performance: {
    status: "loss", current_r: -1, exit_r: -1, peak_r: 0.25,
    stop_price: 0.00000262, trailing_active: false, bars_held: 4,
  } }, "live");
  assert.match(signalCardHTML(active), /outcome-active-win/);
  assert.match(signalCardHTML(active), /运行中 · \+2\.35R/);
  assert.match(signalCardHTML(active), /最高 R<\/dt><dd>\+3\.10R/);
  assert.match(signalCardHTML(active), /跟随保护/);
  assert.match(signalCardHTML(loss), /outcome-loss/);
  assert.match(signalCardHTML(loss), /已止损 · -1\.00R/);
  assert.match(signalCardHTML(loss), /信号收盘参考，并非账户实际成交/);
});

test("YOLO cards prefer the latest raw-event R state over their confirmation snapshot", () => {
  const { state, normalizeV1Event, performanceView } = cardHarness();
  const raw = normalizeV1Event({ ...rawLive, performance: {
    status: "profit", current_r: 4.2, exit_r: 4.2, peak_r: 6.1,
    stop_price: 0.000003108, trailing_active: true, bars_held: 33,
  } }, "live");
  const confirmed = normalizeV1Event({ ...yoloLive, source_event_id: raw.id, indicator: {
    ...rawLive, id: raw.id, performance: {
      status: "active", current_r: 0.3, peak_r: 0.4, stop_price: rawLive.initial_stop,
    },
  } }, "live");
  state.directSignals = [raw];
  const view = performanceView(confirmed);
  assert.equal(view.className, "outcome-win");
  assert.equal(view.value, "+4.20R");
  assert.equal(view.peak, "+6.10R");
});

test("YOLO refresh reloads current raw R after query invalidation", async () => {
  const paths = [];
  const latest = {
    status: "profit", current_r: 5.4, exit_r: 5.4, peak_r: 7.2,
    stop_price: 0.000003201, trailing_active: true, bars_held: 41,
  };
  const harness = refreshHarness(async (path) => {
    paths.push(path);
    if (path === "/api/status") return jsonResponse({ now_ms: Date.now(), runtime: {} });
    if (path.includes("confirmation=raw_yolo")) return jsonResponse({ items: [], total: 0, next_cursor: null });
    if (path.includes("confirmation=yolo")) return jsonResponse({ items: [{
      ...yoloLive, source_event_id: rawLive.id, indicator: {
        ...rawLive, id: rawLive.id, performance: {
          status: "active", current_r: 0.2, peak_r: 0.3,
          stop_price: rawLive.initial_stop, trailing_active: false, bars_held: 2,
        },
      },
    }], total: 1, next_cursor: null });
    return jsonResponse({ items: [{ ...rawLive, timeframe_min: 60, performance: latest }], total: 1, next_cursor: null });
  });
  const { state, refresh, invalidateSignalQuery, performanceView } = harness;
  state.directSignals = [{ stale: true }];
  invalidateSignalQuery();
  state.signalScope = "confirmed";
  state.timeframe = "60";
  await refresh();
  assert.equal(state.directSignals.length, 0, "the hidden overlay must not become visible raw cards or receipts");
  assert.equal(state.performanceSignals.length, 1);
  assert.ok(paths.some((path) => path.includes("confirmation=yolo") && path.includes("timeframe=1H")));
  assert.ok(paths.some((path) => path.includes("confirmation=raw") && path.includes("timeframe=1H")));
  const view = performanceView(state.signals[0]);
  assert.equal(view.className, "outcome-win");
  assert.equal(view.value, "+5.40R");
  assert.equal(view.peak, "+7.20R");
});

test("idle YOLO gate is shown as standby, while loading and error remain distinct", () => {
  assert.match(app, /const gateIdle = gate\.status === "idle" && gate\.loaded !== true && Number\(gate\.queue_depth \|\| 0\) === 0/);
  assert.match(app, /YOLO 待命；当前没有合格原始 V1 候选/);
  assert.match(app, /YOLO 模型正在加载；原始 V1 启动不受影响/);
  assert.match(app, /模型检测异常/);
});

test("overview never presents a persisted ready phase as fresh all-market coverage", () => {
  assert.match(app, /counts\.ready` describes persisted feature phase/);
  assert.match(app, /已扫描 \$\{number\(complete\)\} \/ \$\{number\(total\)\} · 预热\/追平中/);
  assert.match(app, /已扫描 \$\{number\(complete\)\} \/ \$\{number\(total\)\} · 已覆盖，按收盘刷新/);
  assert.doesNotMatch(app, /个窗口已就绪/);
});

test("selected timeframe is filtered by the API before its 2000-row limit", () => {
  assert.match(app, /const apiTimeframe =/);
  assert.match(app, /&timeframe=\$\{encodeURIComponent\(apiTimeframe\(queryTimeframe\)/);
  assert.match(app, /runtimeTimeframes.*uiTimeframe/s);
  assert.match(app, /"30m": "30", "1H": "60", "4H": "240"/);
});

test("pending, malformed, and empty API cases do not become executable live signals", () => {
  assert.equal(consume(pending).closed, false);
  assert.throws(() => consume({ ...rawLive, source: "cache" }), /unknown source/);
  assert.equal(consume({ ...rawLive, timeframe_min: 5 }).timeframe, 5);
  assert.equal(consume({ ...rawLive, direction: "short" }).closed, true);
  assert.throws(() => consume({ ...rawLive, direction: "flat" }), /unknown direction/);
  assert.throws(() => consume(null), /API item/);
  assert.match(app, /服务返回的数据格式有误/);
  assert.match(app, /!\["long", "short"\]\.includes\(direction\)/);
  assert.match(app, /尚无导入的历史回放记录/);
});

test("whole-card TradingView requests normalize the UI timeframe and the inline preview is gone", () => {
  assert.match(app, /const request = \{ symbol: item\.symbol, timeframe: apiTimeframe\(item\.timeframe\) \}/);
  assert.match(app, /data-tradingview-action="signal"/);
  assert.match(app, /title="点击整张卡片，在本机 TradingView 打开"/);
  assert.match(app, /整卡打开 TradingView ↗/);
  assert.doesNotMatch(app, /data-preview-signal-id|function renderChart|function loadChart|function renderDetail/);
  assert.doesNotMatch(page, /detail-panel|chart-dialog|页内预览/);
});

test("15m raw and YOLO rows survive the shared signal and warmup routes as Bark-muted display-only records", async () => {
  const paths = [];
  const harness = refreshHarness(async (path) => {
    paths.push(path);
    if (path === "/api/status") {
      return jsonResponse({ now_ms: Date.now(), runtime: {
        fresh_minutes: 30, notification_mode: "two_stage", timeframes: ["15m", "30m"],
        display_only_timeframes: ["15m"], bark_timeframes: ["30m"],
      } });
    }
    const confirmation = path.includes("confirmation=raw_yolo") ? "raw_yolo" : "raw";
    return jsonResponse({ items: [{ ...rawLive15m, id: `15m-${confirmation}`, confirmation, display_scope: "warmup" }], total: 1, next_cursor: null });
  });
  const { state, refresh } = harness;
  state.view = "warmup";
  state.timeframe = "15";
  await refresh();
  assert.ok(paths.some((path) => path.includes("source=warmup") && path.includes("timeframe=15m") && path.includes("confirmation=raw")));
  assert.ok(paths.some((path) => path.includes("source=warmup") && path.includes("timeframe=15m") && path.includes("confirmation=raw_yolo")));
  assert.equal(state.directSignals[0].timeframe, "15");
  assert.equal(state.rawYoloSignals[0].timeframe, "15");
  assert.match(app, /const DISPLAY_ONLY_NOTE = "仅前端 · Bark 已关闭"/);
  assert.match(app, /if \(channel === "bark" && isDisplayOnly\(item\)\) return \[DISPLAY_ONLY_NOTE, "muted"\]/);
});

test("15m live cards retain raw and YOLO rows, use TradingView interval 15, and show the display-only Bark state", () => {
  const { state, normalizeV1Event, signalCardHTML } = cardHarness();
  state.status = { now_ms: Date.now(), runtime: {
    fresh_minutes: 30, notification_mode: "two_stage", notification_channels: ["bark"],
    display_only_timeframes: ["15m"], bark_timeframes: ["30m"],
  } };
  state.statusReceivedAt = Date.now();
  const raw = normalizeV1Event(rawLive15m, "live");
  const yolo = normalizeV1Event({ ...rawLive15m, id: "yolo-15", confirmation: "raw_yolo" }, "live");
  state.directSignals = [raw, yolo];

  const rawCard = signalCardHTML(raw);
  const yoloCard = signalCardHTML(yolo);
  assert.match(rawCard, /<span class="card-timeframe">15m<\/span>/);
  assert.match(rawCard, /data-tv-timeframe="15"/);
  assert.match(rawCard, /Bark<\/span><span>仅前端 · Bark 已关闭/);
  assert.match(yoloCard, /YOLO 补充确认/);
  assert.match(yoloCard, /data-tv-timeframe="15"/);
});

test("source or timeframe switches discard prior API results and queue the current request", () => {
  assert.match(app, /refreshQueued: null, signalQueryRevision: 0/);
  assert.match(app, /function invalidateSignalQuery\(\).*state\.signals = \[\];.*state\.directSignals = \[\];/s);
  assert.match(app, /if \(state\.syncing\) \{ queueRefresh\(trigger\); return; \}/);
  assert.match(app, /const queryRevision = state\.signalQueryRevision;.*const querySource = signalQuerySource\(\);.*const queryView = state\.view;.*const queryTimeframe = state\.timeframe;/s);
  assert.match(app, /if \(queryRevision !== state\.signalQueryRevision \|\| querySource !== signalQuerySource\(\) \|\| queryView !== state\.view \|\| queryTimeframe !== state\.timeframe\) return;/);
  assert.match(app, /if \(state\.refreshQueued\) \{\s*const queuedTrigger = state\.refreshQueued;\s*state\.refreshQueued = null;\s*refresh\(queuedTrigger\);/s);
});

test("Unicode replay symbols remain searchable without retaining an unrelated cached card", () => {
  const normalSearch = (value) => String(value).normalize("NFKC").toUpperCase().replace(/[^\p{L}\p{N}]/gu, "");
  assert.equal(normalSearch("龙虾_USDT"), "龙虾USDT");
  assert.equal(normalSearch("币安人生-USDT"), "币安人生USDT");
  assert.match(app, /normalize\("NFKC"\)\.toUpperCase\(\)\.replace\(\/\[\^\\p\{L\}\\p\{N\}\]\/gu, ""\)/);
  assert.doesNotMatch(app, /sameSelection|clearSelectedSignal|state\.selected/);
});

test("replay symbols without a swap separator do not repeat their quote asset", () => {
  const shortSymbol = (symbol) => {
    const value = String(symbol || "—").replace(/-(USDT|USD)-SWAP$/, "").replace(/USDT\.P$/, "");
    return value.endsWith("USDT") && value !== "USDT" ? value.slice(0, -4) : value;
  };
  assert.equal(shortSymbol("AIXBTUSDT"), "AIXBT");
  assert.equal(shortSymbol("DATA-USDT-SWAP"), "DATA");
  assert.equal(shortSymbol("PEPEUSDT.P"), "PEPE");
  assert.match(app, /整卡打开 TradingView ↗/);
});

test("replay direct cards can page older raw records without replacing loaded pages", () => {
  assert.match(app, /before_close_ms=\$\{encodeURIComponent\(cursor\.close_ms\)\}/);
  assert.match(app, /before_id=\$\{encodeURIComponent\(cursor\.event_id\)\}/);
  assert.match(page, /id="load-earlier-signals"/);
  assert.match(app, /\$\("load-earlier-signals"\)\.classList\.toggle\("hidden", !hasEarlierPage\)/);
  assert.match(app, /const SIGNAL_PAGE_SIZE = 500/);
  assert.match(app, /limit=\$\{SIGNAL_PAGE_SIZE\}&source=\$\{source\}/);
  assert.match(app, /limit=\$\{SIGNAL_PAGE_SIZE\}&source=\$\{encodeURIComponent\(querySource\)\}/);
  assert.match(app, /加载更早记录（再取最多 \$\{SIGNAL_PAGE_SIZE\.toLocaleString/);
  assert.match(app, /\$\("load-earlier-signals"\)\.addEventListener\("click", loadEarlierRawSignals\)/);
  assert.match(app, /当前页 \$\{number\(source\.length\)\} 条；可直接读取更早记录/);
  assert.match(app, /state\.rawPaged = true/);
  assert.match(app, /key === "directSignals" && state\.rawPaged/);
  assert.match(app, /已加载 \$\{number\(source\.length\)\} 条；可继续读取更早记录/);
});


test("signals refresh never requests market summaries and Watch loads them once", () => {
  const refreshStart = app.indexOf("async function refresh(trigger = \"manual\")");
  const watchLoad = app.indexOf("async function loadMarkets()");
  assert.ok(refreshStart >= 0 && watchLoad > refreshStart);
  assert.doesNotMatch(app.slice(refreshStart, watchLoad), /\/api\/markets/);
  assert.match(app, /if \(state\.view === "watch"\) loadMarkets\(\);/);
  assert.match(app, /async function loadMarkets\(\)[\s\S]*state\.marketsLoaded \|\| state\.marketsLoading/);
  assert.match(app, /api\("\/api\/markets"\)/);
  assert.match(app, /marketsLoading: false, marketsRetryTimer: null/);
  assert.match(app, /A legacy summary backfill returns 503/);
  assert.match(app, /if \(!state\.marketsRetryTimer && state\.view === "watch"\)/);
  assert.match(app, /if \(state\.view === "watch" && !state\.marketsLoaded\) loadMarkets\(\);/);
});


test("cursor pages serialize periodic refresh and retain their own failure notice", () => {
  assert.match(app, /if \(state\.rawLoadingMore\) \{ queueRefresh\(trigger\); return; \}/);
  assert.match(app, /state\.errors\.earlierSignals = error\.message \|\| "请求失败"/);
  assert.match(app, /delete state\.errors\.earlierSignals/);
  assert.match(app, /earlierSignals: "更早历史记录"/);
  assert.match(app, /state\.rawLoadingMore = false;[\s\S]*if \(state\.refreshQueued\) \{[\s\S]*refresh\(queuedTrigger\);/);
});


test("refresh execution polls loaded replay status only and retains manual/live/retry/cursor semantics", async () => {
  const paths = [];
  let failReplayOnce = false;
  const harness = refreshHarness(async (path) => {
    paths.push(path);
    if (path.startsWith("/api/status")) return jsonResponse({ now_ms: Date.now(), runtime: {} });
    if (path.includes("source=replay") && failReplayOnce) {
      failReplayOnce = false;
      throw new Error("temporary replay failure");
    }
    const source = path.includes("source=replay") ? "replay" : "live";
    const confirmation = path.includes("confirmation=raw_yolo") ? "raw_yolo" : path.includes("confirmation=yolo") ? "yolo" : "raw";
    return jsonResponse({ items: [{ ...rawLive, id: `${source}-${confirmation}-${paths.length}`, source, confirmation }], total: 1, next_cursor: null });
  });
  const { state, refresh } = harness;

  state.signalSource = "replay";
  state.directSignalsLoaded = true;
  await refresh("periodic");
  assert.deepEqual(paths, ["/api/status"]);

  paths.length = 0;
  await refresh();
  assert.equal(paths.filter((path) => path.startsWith("/api/signals")).length, 1);
  assert.match(paths.find((path) => path.startsWith("/api/signals")), /source=replay.*confirmation=raw/);

  paths.length = 0;
  state.signalSource = "live";
  state.directSignalsLoaded = true;
  await refresh("periodic");
  assert.equal(paths.filter((path) => path.startsWith("/api/signals")).length, 2);
  assert.ok(paths.some((path) => path.includes("confirmation=raw")));
  assert.ok(paths.some((path) => path.includes("confirmation=raw_yolo")));

  paths.length = 0;
  state.signalSource = "replay";
  state.directSignalsLoaded = false;
  failReplayOnce = true;
  await refresh("periodic");
  assert.equal(state.directSignalsLoaded, false);
  assert.ok(paths.some((path) => path.startsWith("/api/signals")));
  paths.length = 0;
  await refresh("periodic");
  assert.equal(state.directSignalsLoaded, true);
  assert.ok(paths.some((path) => path.startsWith("/api/signals")));

  paths.length = 0;
  state.rawLoadingMore = true;
  await refresh("periodic");
  await refresh();
  assert.equal(state.refreshQueued, "manual");
  assert.deepEqual(paths, []);
});


test("replay raw paging does not fetch hidden YOLO families", () => {
  assert.match(app, /const queryScope = state\.signalScope/);
  assert.match(app, /else if \(shouldRefreshSignalList\(trigger\) && queryScope === "direct"\) \{[\s\S]*confirmation=raw\$\{timeframe\}[\s\S]*if \(\["live", "warmup"\]\.includes\(querySource\)\) requests\.push\(\{ key: "rawYoloSignals"/);
  assert.match(app, /if \(shouldRefreshSignalList\(trigger\) && queryScope === "confirmed"\) \{[\s\S]*confirmation=yolo\$\{timeframe\}/);
  assert.match(app, /const results = await Promise\.allSettled\(requests\.map\(\(request\) => api\(request\.path\)\)\)/);
  assert.match(app, /Hidden families are fetched only when the reader actually switches to them/);
});

test("warmup nav queries its own source while preserving live/replay controls", () => {
  assert.match(page, /data-view="warmup"[^>]*aria-label="预热历史"/);
  assert.match(app, /warmup: \["预热历史", "初次启动前的回算信号，仅供复盘，不触发通知。"\]/);
  assert.match(page, /id="warmup-notice"[^>]*>保留的服务回执仅作历史事实显示；这里不推断、补发或触发通知。/);
  assert.match(page, /data-signal-source="live"/);
  assert.match(page, /data-signal-source="replay"/);
  assert.match(app, /const signalQuerySource = \(\) => state\.view === "warmup" \? "warmup" : state\.signalSource;/);
  assert.match(app, /const changesWarmupScope = previousView === "warmup" \|\| state\.view === "warmup";[\s\S]*const entersSignalView = !signalView\(previousView\) && signalView\(state\.view\);[\s\S]*if \(changesWarmupScope \|\| entersSignalView\)[\s\S]*invalidateSignalQuery\(\);[\s\S]*if \(signalView\(state\.view\)\) refresh\(\);/);
  assert.match(app, /\["live", "warmup"\]\.includes\(querySource\).*confirmation=raw_yolo/s);
});

test("warmup rows remain live-chart records but cannot become fresh or notification-pending", async () => {
  const paths = [];
  const harness = refreshHarness(async (path) => {
    paths.push(path);
    if (path === "/api/status") return jsonResponse({ now_ms: Date.now(), runtime: { fresh_minutes: 30, notification_mode: "two_stage" } });
    const confirmation = path.includes("confirmation=raw_yolo") ? "raw_yolo" : "raw";
    return jsonResponse({ items: [{ ...rawLive, id: `warmup-${confirmation}`, confirmation, display_scope: "warmup", is_fresh: true }], total: 1, next_cursor: null });
  });
  const { state, refresh } = harness;
  state.view = "warmup";
  state.signalSource = "replay";
  await refresh();
  assert.ok(paths.some((path) => path.includes("source=warmup") && path.includes("confirmation=raw")));
  assert.ok(paths.some((path) => path.includes("source=warmup") && path.includes("confirmation=raw_yolo")));
  assert.equal(state.directSignals[0].source, "live", "warmup keeps the underlying live chart route");
  assert.equal(state.directSignals[0].display_scope, "warmup");
  assert.match(app, /if \(isWarmupRecord\(item\)\) return false;/);
  assert.match(app, /if \(isWarmupRecord\(item\)\) \{[\s\S]*保存回执 · 服务已接受[\s\S]*return \["预热历史不通知", "muted"\];/);
  assert.match(app, /isWarmupRecord\(item\) \? "回算信号 · 仅供复盘"/);
});

test("late warmup pages cannot overwrite a later live route or its cursor", async () => {
  const paths = [];
  const pending = [];
  const harness = refreshHarness((path) => new Promise((resolve) => {
    paths.push(path);
    pending.push(() => resolve(jsonResponse(path === "/api/status" ? { now_ms: Date.now(), runtime: {} } : { items: [{ ...rawLive, id: "late-warmup", display_scope: "warmup" }], total: 1, next_cursor: { close_ms: 1, event_id: "late" } })));
  }));
  const { state, refresh, loadEarlierRawSignals } = harness;
  state.view = "warmup";
  const topPage = refresh();
  assert.ok(paths.some((path) => path.includes("source=warmup")));
  state.view = "watch";
  state.signalQueryRevision++;
  pending.splice(0).forEach((resolve) => resolve());
  await topPage;
  assert.equal(state.directSignals.length, 0);
  assert.equal(state.rawNextCursor, null);

  state.view = "warmup";
  state.rawHasMore = true;
  state.rawNextCursor = { close_ms: 99, event_id: "warmup-boundary" };
  const olderPage = loadEarlierRawSignals();
  assert.ok(paths.some((path) => path.includes("source=warmup") && path.includes("before_id=warmup-boundary")));
  state.view = "watch";
  state.signalQueryRevision++;
  pending.splice(0).forEach((resolve) => resolve());
  await olderPage;
  assert.equal(state.rawNextCursor.event_id, "warmup-boundary", "stale pagination cannot replace the prior cursor");
  assert.equal(state.directSignals.length, 0);
});
