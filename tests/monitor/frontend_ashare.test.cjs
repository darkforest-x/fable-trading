"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const ui = require("../../yoyo/monitor/static/ashare-backtest.js");
test("missing values cannot masquerade as zero research returns", () => {
  for (const value of [null,undefined,NaN,Infinity,"0"]) assert.equal(ui.percent(value),"—");
  assert.equal(ui.percent(0),"0.00%");
  assert.equal(ui.number(null),"—");
});
test("version and calendar filters retain exactly the selected cells", () => {
  const rows = ["v1","v8"].flatMap(version => ["1D","1W"].map(timeframe => ({version,timeframe})));
  assert.deepEqual(ui.filter(rows,"v8","1W"),[{version:"v8",timeframe:"1W"}]);
  assert.equal(ui.filter(rows,"all","1D").length,2);
});
test("report links stay inside the fixed static artifact directory", () => {
  for (const url of ["https://evil.test", "javascript:alert(1)","/static/ashare-backtest/../a", "//evil.test/a"]) assert.equal(ui.safeLink(url),null);
  assert.equal(ui.safeLink("/static/ashare-backtest/report.html"),"/static/ashare-backtest/report.html");
});
test("unavailable and unsafe labels remain explicit escaped text", () => {
  assert.match(ui.table([]),/尚无已完成/);
  const html = ui.table([{version:'<img src=x onerror=alert(1)>',timeframe:'1D',win_rate:null}]);
  assert.ok(!html.includes('<img'));
  assert.match(html,/&lt;IMG/);
  assert.match(html,/—/);
});
test("owner-stopped coverage distinguishes uncollected stocks from failures", () => {
  const text = ui.coverageText({universe_count:3280,covered_symbols:2587,failed_symbols:43,
    collection:{owner_stopped:true,uncollected:650,errors:43}});
  assert.match(text,/已覆盖 2,587/);
  assert.match(text,/采集失败 43/);
  assert.match(text,/评估隔离 0/);
  assert.match(text,/未采集 650/);
  assert.ok(!text.includes("待重试"));
});
