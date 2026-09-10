"use strict";

// Run with: node --test tests/monitor/frontend_cards.test.cjs
// These are contract fixtures for the static SPIKE V1 client. They intentionally
// use no HTTP service or market data: browser QA runs once the worker API is ready.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

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
const rawReplay = Object.freeze({
  ...rawLive, id: "raw-replay-pepe-240-3", source: "replay", timeframe_min: 240,
  executable_entry_time: null,
});
const pending = Object.freeze({ ...rawLive, id: "pending", is_closed: false });

function consume(row) {
  if (!row || typeof row !== "object") throw new TypeError("API item must be an object");
  const timeframe = Number(row.timeframe_min);
  if (!Number.isInteger(timeframe) || ![30, 60, 240].includes(timeframe)) throw new TypeError("unsupported timeframe");
  if (!["live", "replay"].includes(row.source)) throw new TypeError("unknown source");
  if (!["raw", "yolo", "raw_yolo"].includes(row.confirmation)) throw new TypeError("unknown confirmation");
  if (row.direction !== "long") throw new TypeError("SPIKE V1 is long-only");
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

test("V1 page exposes only live/replay and 30m/1H/4H signal controls", () => {
  const filters = page.match(/aria-label="信号周期"([\s\S]*?)<\/div>/)?.[1] || "";
  assert.match(filters, /data-timeframe="30"/);
  assert.match(filters, /data-timeframe="60"/);
  assert.match(filters, /data-timeframe="240"/);
  assert.doesNotMatch(filters, /5m|15m|1Dutc/);
  assert.match(page, /data-signal-source="live"/);
  assert.match(page, /data-signal-source="replay"/);
  assert.match(page, /V1 · 仅多头 · 已收盘 K 线/);
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
  assert.match(app, /回放未提供成交时钟/);
  assert.match(app, /source=\$\{source\}/);
  assert.match(app, /\/api\/replay\/chart\?event_id=/);
  assert.match(app, /信号之后的 K 线仅用于回看/);
});

test("covered replay receipts distinguish realized facts from censored rows without a performance claim", () => {
  assert.match(app, /function coveredLedgerFacts\(item\)/);
  assert.match(app, /covered_linked_realized_unverified/);
  assert.match(app, /已关联 · 未独立收益审核/);
  assert.match(app, /replayExitReason.*protective_stop: "保护止损"/);
  assert.match(app, /回测退出 · 北京时间/);
  assert.match(app, /单笔净 R/);
  assert.match(app, /非账户收益，未独立核验/);
  assert.match(app, /covered_linked_censored_unverified/);
  assert.match(app, /未实现；不计胜率、PF 或净收益/);
  assert.match(app, /尚未关联 v2 覆盖账本 · 不展示收益/);
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
  assert.throws(() => consume({ ...rawLive, timeframe_min: 15 }), /unsupported timeframe/);
  assert.throws(() => consume({ ...rawLive, direction: "short" }), /long-only/);
  assert.throws(() => consume(null), /API item/);
  assert.match(app, /服务返回的数据格式有误/);
  assert.match(app, /direction !== "long"/);
  assert.match(app, /尚未确认 · 不作为可执行 V1/);
  assert.match(app, /尚无导入的历史回放记录/);
});

test("chart uses UTC timestamp distance, preserves gaps, and plots only explicit stop prices", () => {
  const timestamps = [Date.parse("2026-09-11T00:00:00Z"), Date.parse("2026-09-11T00:30:00Z"), Date.parse("2026-09-11T04:30:00Z")];
  const first = timestamps[0];
  const range = timestamps.at(-1) + 30 * 60_000 - first;
  const x = (time) => (time - first + 15 * 60_000) / range;
  assert.ok(x(timestamps[2]) - x(timestamps[1]) > 4 * (x(timestamps[1]) - x(timestamps[0])));
  assert.match(app, /const firstTime = Number\(candles\[0\]\.t\)/);
  assert.match(app, /timeRange = Math\.max\(nominalMs, lastTime - firstTime\)/);
  assert.match(app, /initial_stop/);
  assert.match(app, /may be a distance or a ratio/);
  assert.match(app, /chart-risk-line/);
  assert.match(app, /pointerdown/);
  assert.match(app, /event\.deltaY/);
});

test("live chart translates the display timeframe to the monitor API timeframe", () => {
  assert.match(app, /const chartTimeframe = apiTimeframe\(item\.timeframe\)/);
  assert.match(app, /timeframe=\$\{encodeURIComponent\(chartTimeframe\)\}/);
  assert.match(app, /图表周期不受当前 V1 服务支持/);
});

test("changing source or timeframe cannot retain a stale detail chart", () => {
  assert.match(app, /const sameSelection = .*a\.source === b\.source.*a\.confirmation === b\.confirmation/s);
  assert.match(app, /state\.chartController\?\.abort\(\);\s*state\.chartRequest\+\+;/s);
  assert.match(app, /!currentItems\.some\(\(item\) => sameSelection\(state\.selected, item\)\)/);
  assert.match(app, /request !== state\.chartRequest \|\| !sameSelection\(state\.selected, item\)/);
  assert.match(app, /state\.signalSource = button\.dataset\.signalSource;\s*state\.rowLimit = 24; invalidateSignalQuery\(\);/s);
});

test("source or timeframe switches discard prior API results and queue the current request", () => {
  assert.match(app, /refreshQueued: false, signalQueryRevision: 0/);
  assert.match(app, /function invalidateSignalQuery\(\).*state\.signals = \[\];.*state\.directSignals = \[\];.*clearSelectedSignal\(\);/s);
  assert.match(app, /if \(state\.syncing\) \{ state\.refreshQueued = true; return; \}/);
  assert.match(app, /const queryRevision = state\.signalQueryRevision;.*const querySource = state\.signalSource;.*const queryTimeframe = state\.timeframe;/s);
  assert.match(app, /if \(queryRevision !== state\.signalQueryRevision \|\| querySource !== state\.signalSource \|\| queryTimeframe !== state\.timeframe\) return;/);
  assert.match(app, /if \(state\.refreshQueued\) \{\s*state\.refreshQueued = false;\s*refresh\(\);/s);
  assert.match(app, /\$\("chart-container"\)\.removeAttribute\("aria-label"\)/);
  assert.match(app, /正在加载 \$\{sourceName\(item\)\} \$\{shortSymbol\(item\.symbol\)\}/);
});

test("replay symbols without a swap separator do not repeat their quote asset", () => {
  const shortSymbol = (symbol) => {
    const value = String(symbol || "—").replace(/-(USDT|USD)-SWAP$/, "").replace(/USDT\.P$/, "");
    return value.endsWith("USDT") && value !== "USDT" ? value.slice(0, -4) : value;
  };
  assert.equal(shortSymbol("AIXBTUSDT"), "AIXBT");
  assert.equal(shortSymbol("DATA-USDT-SWAP"), "DATA");
  assert.equal(shortSymbol("PEPEUSDT.P"), "PEPE");
  assert.match(app, /页内预览 \$\{venue\} \$\{shortSymbol\(item\.symbol\)\}/);
});

test("stale ledger evidence hides its prior outcome until an immutable snapshot relinks it", () => {
  assert.match(app, /stale_evidence_unverified/);
  assert.match(app, /账本证据已过期 · 不展示收益/);
  assert.match(app, /等待新的不可变账本快照重链/);
});


test("replay direct cards can page older raw records without replacing loaded pages", () => {
  assert.match(app, /before_close_ms=\$\{encodeURIComponent\(cursor\.close_ms\)\}/);
  assert.match(app, /before_id=\$\{encodeURIComponent\(cursor\.event_id\)\}/);
  assert.match(app, /加载更早记录（每页最多 2,000 条）/);
  assert.match(app, /state\.rawPaged = true/);
  assert.match(app, /key === "directSignals" && state\.rawPaged/);
  assert.match(app, /已加载 \$\{number\(source\.length\)\} 条；可继续读取更早记录/);
});


test("missing frozen OHLC remains unverified and is never replaced with a live chart", () => {
  assert.match(app, /link\?\.link_status === "ohlc_missing"/);
  assert.match(app, /缺少同源冻结 OHLC · 不展示收益/);
  assert.match(app, /不使用其他交易所或当前行情替代/);
});
