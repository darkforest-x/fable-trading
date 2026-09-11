/* Browser-independent execution checks for the review site's two distinct bar clocks. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

const root = path.resolve(__dirname, "../..");
const timingSource = fs.readFileSync(path.join(root, "yoyo/evaluation/static/spike_v1_review/timing.js"), "utf8");
const appSource = fs.readFileSync(path.join(root, "yoyo/evaluation/static/spike_v1_review/app.js"), "utf8");
const sandbox = {};
vm.runInNewContext(timingSource, sandbox, { filename: "timing.js" });
const { resolveChartTiming } = sandbox.SpikeV1ReviewTiming;

test("original V1 marker uses candle bar open while initial SL starts at confirmed close", () => {
  const timing = resolveChartTiming(
    { signal_bar_open_ms: 1_000, signal_close_ms: 2_800 },
    { signal_bar_open_ms: 900, signal_close_ms: 2_700 },
    { bar_open_ms: 800, time_ms: 2_600 },
  );
  assert.deepEqual({ ...timing }, { signalBarMs: 1_000, confirmationMs: 2_800 });
  assert.match(appSource, /markers\.push\(marker\(signalBarMs,/);
  assert.match(appSource, /signalGuide\(priceChart, priceContainer, signalBarMs\)/);
  assert.match(appSource, /initialStopGuide\(priceChart, priceSeries, priceContainer, confirmationMs,/);
});

test("payload timing wins over record fallbacks without conflating next-open entry", () => {
  const timing = resolveChartTiming(
    { signal_bar_open_ms: 10_000, signal_close_ms: 13_600 },
    { signal_bar_open_ms: 9_000, signal_close_ms: 12_600 },
    { bar_open_ms: 8_000, time_ms: 11_600 },
  );
  assert.equal(timing.signalBarMs, 10_000);
  assert.equal(timing.confirmationMs, 13_600);
  assert.notEqual(timing.signalBarMs, timing.confirmationMs);
});
