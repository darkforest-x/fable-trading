"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const source = fs.readFileSync(path.resolve(__dirname, "../../yoyo/monitor/static/strategies.js"), "utf8");

test("V13.1 paper displays independent results and signed entry comparison without making up an unknown return", async () => {
  const root = { innerHTML: "", dataset: {}, addEventListener() {} };
  const window = {};
  const run = { id: "new", strategy_id: "spike-v131-retest", status: "running",
    spec: { timeframes: ["15m"], symbol_scope: "okx_all_usdt", cost_bp: 20 },
    metrics: { accepted: 3, closed: 2, open: 1, net_r: 1, win_rate: .5,
      by_side: { long: { closed: 1, net_r: -1 }, short: { closed: 1, net_r: 2 } },
      by_asset_class: { stocks: { closed: 1, net_r: 2 }, unknown: { closed: 0, net_r: null } },
      entry_comparisons: 3, mean_entry_adverse_bp: -3.4 } };
  const decision = { symbol: "TEST-USDT-SWAP", timeframe: "15m", side: "short", status: "closed",
    trade: { comparison_policy: "exact", entry_price: 99, reference_entry_price: 100,
      entry_adverse_bp: 100, observation_delay_ms: 180000, entry_delay_ms: 900000,
      asset_class: "stocks", cost_r: .2, net_r: 2 } };
  vm.runInNewContext(source, { window, document: { activeElement: null, getElementById: () => root },
    fetch: async (url) => ({ ok: true, headers: { get: () => "application/json" }, json: async () =>
      url.endsWith("/strategies") ? { items: [{ id: "spike-v131-retest", paper_supported: true, timeframes: ["15m"] }] } :
      url.endsWith("/runs") ? { items: [run] } : { ...run, decisions: [decision], total: 1 } }),
    Date, Intl, Number, String, Boolean, Array, Object, Math, JSON, Error, Promise, encodeURIComponent });
  await window.SpikePaper.setActive(true);
  for (const text of ["独立于信号中心原参考收益", "多头：1 笔结束 · -1.00R", "空头：1 笔结束 · 2.00R",
    "股票类：1 笔结束 · 2.00R", "未分类：0 笔结束 · 收益待积累", "-3.4 bp", "100.00 bp（正值较差）",
    "原参考开盘", "3.0 / 15.0 分钟", "0.200R（20bp）", "未计资金费和盘口滑点"]) assert.ok(root.innerHTML.includes(text), text);
  assert.doesNotMatch(root.innerHTML, /NaN|undefined/);
  assert.ok(root.innerHTML.includes('name="timeframes" value="15m"'));
  assert.ok(!root.innerHTML.includes('name="timeframes" value="1H"'));
});
