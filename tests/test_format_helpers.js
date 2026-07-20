/**
 * Node smoke tests for static/format_helpers.js (no DOM).
 * Run: node tests/test_format_helpers.js
 */
"use strict";

const path = require("path");
const F = require(path.join(__dirname, "../src/webapp/static/format_helpers.js"));

let failed = 0;
function assert(cond, msg) {
  if (!cond) {
    failed += 1;
    console.error("FAIL:", msg);
  } else {
    console.log("ok:", msg);
  }
}

// Beijing: UTC 2026-07-19 16:00 → 2026-07-20 00:00
assert(F.fmtBjTime("2026-07-19T16:00:00Z") === "2026-07-20 00:00", "UTC Z → BJ next day midnight");
assert(F.fmtBjTime(1784476800) === "2026-07-20 00:00", "unix sec → BJ");
assert(F.fmtBjTime(null) === "—", "null time");
assert(F.fmtBjTime("2026-07-19 16:00:00") === "2026-07-20 00:00", "naive UTC string treated as Z");

const lagFresh = F.fmtLagMin(12, 20);
assert(lagFresh.fresh === true && lagFresh.text === "12m" && lagFresh.cls === "pos", "fresh lag 12m");
const lagStale = F.fmtLagMin(45, 20);
assert(lagStale.fresh === false && lagStale.text.includes("事后") && lagStale.cls === "neg", "stale lag");
const lagHour = F.fmtLagMin(90, 20);
assert(lagHour.text.startsWith("1.5h"), "lag hours");
assert(F.fmtLagMin(null).text === "—", "null lag");

assert(F.fmtPct(0.1234) === "12.34%", "pct default digits");
assert(F.fmtPct(null) === "—", "pct null");
assert(F.fmtPF(1.35) === "1.35", "pf");
assert(F.fmtPF(null) === "—", "pf null");

const tick = F.chartTickMarkBj(1784476800);
assert(tick === "07-20 00:00", "chart tick BJ short");

if (failed) {
  console.error("\n" + failed + " assertion(s) failed");
  process.exit(1);
}
console.log("\nall format_helpers checks passed");
