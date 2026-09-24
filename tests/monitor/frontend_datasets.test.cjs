"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../yoyo/monitor/static/datasets.js"), "utf8");

const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
function harness(fetchImpl) {
  const listeners = {};
  const elements = new Map();
  const element = (key) => {
    if (!elements.has(key)) elements.set(key, { innerHTML: "", textContent: "", value: "", disabled: false });
    return elements.get(key);
  };
  const root = {
    innerHTML: "",
    addEventListener: (type, listener) => { listeners[type] = listener; },
    querySelector: element,
  };
  const window = {};
  vm.runInNewContext(source, {
    window, document: { getElementById: (id) => id === "datasets-workspace" ? root : null },
    navigator: { clipboard: { writeText: async () => {} } }, fetch: fetchImpl,
    Date, Intl, Number, String, Boolean, Array, Object, Math, JSON, Error, Promise, encodeURIComponent,
  });
  return { api: window.SpikeDatasets, root, listeners, element };
}
const catalog = (items, extra = {}) => ({
  indexed: true, generated_at: "2026-09-24T01:00:00Z",
  summary: { dataset_count: items.length, file_count: 2, total_bytes: 4096, managed_bytes: 2048, duplicate_bytes: 512, missing_count: 0 },
  categories: [{ id: "ohlcv", label: "OHLCV", count: items.filter((item) => item.category === "ohlcv").length }],
  items, maintenance: { status: "idle", error: null }, ...extra,
});
const dataset = (overrides = {}) => ({
  id: "btc-15m", name: "BTC 15 分钟历史", category: "ohlcv", category_label: "OHLCV",
  path: "data/market/btc", original_paths: ["data/archive/btc.csv"], storage: "managed",
  file_count: 2, total_bytes: 2048, formats: ["parquet"], exchanges: ["okx"], timeframes: ["15m"],
  symbols_count: 1, start: "2025-01-01", end: "2025-02-01", notes: ["来源：OKX 历史归档；保留 v2。"],
  backtest_ready: false, ...overrides,
});
const click = (listener, selector, datasetValues = {}) => listener({
  target: { closest: () => ({ dataset: datasetValues, matches: (candidate) => candidate === selector }) },
});

test("catalog lazy-loads, explains logical storage, uses the single-stream example, and escapes indexed text", async () => {
  const paths = [];
  const ready = dataset({ id: "eth-ready", name: "ETH <script>", backtest_ready: true,
    start: "aggregate-start", end: "aggregate-end",
    example: { symbol: "ETH-USDT", timeframe: "1h", exchange: "okx", start: "2025-02-01", end: "2025-02-03" } });
  const p = harness(async (url) => { paths.push(url); return response(catalog([ready, dataset()])); });
  assert.equal(paths.length, 0);
  await p.api.setActive(true);
  assert.deepEqual(paths, ["/api/research/datasets"]);
  assert.match(p.root.innerHTML, /文件逻辑大小/);
  assert.match(p.root.innerHTML, /集中行情大小/);
  assert.match(p.root.innerHTML, /已处理重复块/);
  assert.match(p.root.innerHTML, /APFS 的 COW 共享会使物理占用不同/);
  assert.doesNotMatch(p.root.innerHTML, /磁盘总占用|重复数据占用|backtest_ready|没有删除接口/);
  const cards = p.element("#dataset-records").innerHTML.split('<article class="dataset-card">').slice(1);
  assert.equal(cards.length, 2);
  assert.match(cards[0], /read_market_data/);
  assert.match(cards[0], /symbol=&#39;ETH-USDT&#39;/);
  assert.match(cards[0], /exchange=&#39;okx&#39;,/);
  assert.match(cards[0], /timeframe=&#39;1h&#39;/);
  assert.match(cards[0], /start=&#39;2025-02-01&#39;/);
  const sample = cards[0].match(/<pre><code>([\s\S]*?)<\/code>/)?.[1] || "";
  assert.doesNotMatch(sample, /aggregate-start|aggregate-end/);
  assert.match(cards[0], /ETH &lt;script&gt;/);
  assert.ok(!cards[0].includes("<script>"));
  assert.match(cards[1], /回测读取：未登记/);
  assert.ok(!cards[1].includes('class="dataset-example"'));
  assert.match(cards[1], /不代表 YOLO 训练资格或生产准入/);
});

test("category and search filters reset directory pagination and keep filtered totals", async () => {
  const items = Array.from({ length: 30 }, (_, index) => dataset({
    id: `dataset-${index}`, name: `Market sample ${index}`, category: index < 27 ? "ohlcv" : "funding",
    notes: index < 25 ? ["searchable"] : ["other"],
  }));
  const p = harness(async () => response(catalog([
    ...items,
  ], { categories: [{ id: "ohlcv", label: "OHLCV", count: 27 }, { id: "funding", label: "资金费率", count: 3 }] })));
  await p.api.setActive(true);
  assert.match(p.element("#dataset-list-pagination").innerHTML, /第 1 \/ 3 页/);
  assert.equal((p.element("#dataset-records").innerHTML.match(/class="dataset-card"/g) || []).length, 12);
  await click(p.listeners.click, "[data-dataset-list-page]", { datasetListPage: "next" });
  await click(p.listeners.click, "[data-dataset-list-page]", { datasetListPage: "next" });
  assert.match(p.element("#dataset-list-pagination").innerHTML, /第 3 \/ 3 页/);
  assert.match(p.element("#dataset-records").innerHTML, /dataset-24/);
  await click(p.listeners.click, "[data-dataset-category]", { datasetCategory: "ohlcv" });
  assert.match(p.element("#dataset-result-count").textContent, /筛选结果 27 \/ 全部 30/);
  assert.equal(p.element("#dataset-category").value, "ohlcv");
  assert.match(p.element(".dataset-category-grid").innerHTML, /data-dataset-category="ohlcv" aria-pressed="true"/);
  assert.match(p.element(".dataset-category-grid").innerHTML, /data-dataset-category="funding" aria-pressed="false"/);
  assert.match(p.element("#dataset-list-pagination").innerHTML, /第 1 \/ 3 页/);
  assert.match(p.element("#dataset-records").innerHTML, /dataset-0/);
  assert.doesNotMatch(p.element("#dataset-records").innerHTML, /dataset-24/);
  await click(p.listeners.click, "[data-dataset-list-page]", { datasetListPage: "next" });
  await click(p.listeners.click, "[data-dataset-list-page]", { datasetListPage: "next" });
  assert.match(p.element("#dataset-list-pagination").innerHTML, /第 3 \/ 3 页/);
  p.listeners.input({ target: { id: "dataset-search", value: "searchable" } });
  assert.match(p.element("#dataset-result-count").textContent, /筛选结果 25 \/ 全部 30/);
  assert.match(p.element("#dataset-list-pagination").innerHTML, /第 1 \/ 3 页/);
  assert.match(p.element("#dataset-records").innerHTML, /dataset-0/);
  assert.doesNotMatch(p.element("#dataset-records").innerHTML, /dataset-24/);
  p.listeners.input({ target: { id: "dataset-search", value: "not found" } });
  assert.match(p.element("#dataset-records").innerHTML, /没有匹配的数据集/);
  assert.match(p.element("#dataset-result-count").textContent, /筛选结果 0 \/ 全部 30/);
  assert.equal(p.element("#dataset-list-pagination").innerHTML, "");
  assert.match(p.element("#dataset-records").innerHTML, /data-dataset-reset/);
  p.listeners.input({ target: { id: "dataset-search", value: "" } });
  p.element("#dataset-category").value = "funding";
  p.listeners.change({ target: { id: "dataset-category", value: "funding" } });
  assert.match(p.element(".dataset-category-grid").innerHTML, /data-dataset-category="funding" aria-pressed="true"/);
  assert.match(p.element(".dataset-category-grid").innerHTML, /data-dataset-category="" aria-pressed="false"/);
  assert.match(p.element("#dataset-result-count").textContent, /筛选结果 3 \/ 全部 30/);
  p.element("#dataset-search").value = "not found";
  p.listeners.input({ target: { id: "dataset-search", value: "not found" } });
  await click(p.listeners.click, "[data-dataset-reset]");
  assert.equal(p.element("#dataset-search").value, "");
  assert.equal(p.element("#dataset-category").value, "");
  assert.match(p.element("#dataset-result-count").textContent, /筛选结果 30 \/ 全部 30/);
});

test("missing-file summary locates affected datasets only when per-dataset counts exist", async () => {
  const items = [
    dataset({ id: "complete", name: "Complete market", missing_count: 0 }),
    dataset({ id: "missing-one", name: "Market with one missing file", missing_count: 1 }),
    dataset({ id: "missing-two", name: "Market with several missing files", missing_count: 6 }),
  ];
  const p = harness(async () => response(catalog(items, {
    summary: { dataset_count: 3, file_count: 12, total_bytes: 4096, managed_bytes: 4096, duplicate_bytes: 0, missing_count: 7 },
  })));
  await p.api.setActive(true);
  assert.match(p.root.innerHTML, /查看受影响数据集（2）/);
  assert.match(p.element("#dataset-records").innerHTML, /class="dataset-card has-missing-files"/);
  assert.match(p.element("#dataset-records").innerHTML, /6 个缺失文件/);
  assert.match(p.root.innerHTML, /<h2>币圈数据集<\/h2>/);
  assert.doesNotMatch(p.root.innerHTML, /<h1/);

  await click(p.listeners.click, "[data-dataset-missing-filter]");
  assert.match(p.root.innerHTML, /data-dataset-missing-filter aria-pressed="true">取消缺失筛选/);
  assert.match(p.element("#dataset-result-count").textContent, /筛选结果 2 \/ 全部 3/);
  assert.doesNotMatch(p.element("#dataset-records").innerHTML, /Complete market|>complete</);
  assert.match(p.element("#dataset-records").innerHTML, /missing-one/);
  assert.match(p.element("#dataset-records").innerHTML, /missing-two/);
  p.element("#dataset-search").value = "no such dataset";
  p.listeners.input({ target: { id: "dataset-search", value: "no such dataset" } });
  assert.match(p.element("#dataset-records").innerHTML, /当前筛选条件下没有匹配的数据集/);
  await click(p.listeners.click, "[data-dataset-reset]");
  assert.equal(p.element("#dataset-search").value, "");
  assert.match(p.element("#dataset-result-count").textContent, /筛选结果 3 \/ 全部 3/);
  assert.match(p.element("#dataset-records").innerHTML, /Complete market/);
  assert.match(p.root.innerHTML, /data-dataset-missing-filter aria-pressed="false">查看受影响数据集（2）/);

  const unbroken = harness(async () => response(catalog([dataset()], {
    summary: { dataset_count: 1, file_count: 2, total_bytes: 2048, managed_bytes: 2048, duplicate_bytes: 0, missing_count: 5 },
  })));
  await unbroken.api.setActive(true);
  assert.match(unbroken.root.innerHTML, /没有逐数据集缺失数，无法定位受影响条目/);
  assert.doesNotMatch(unbroken.root.innerHTML, /data-dataset-missing-filter/);
  assert.doesNotMatch(unbroken.element("#dataset-records").innerHTML, /个缺失文件/);
});

test("ready dataset without a concrete example gets replaceable parameters, not aggregate dates", async () => {
  const p = harness(async () => response(catalog([dataset({ backtest_ready: true })])));
  await p.api.setActive(true);
  const card = p.element("#dataset-records").innerHTML;
  assert.match(card, /替换为币种/);
  const sample = card.match(/<pre><code>([\s\S]*?)<\/code>/)?.[1] || "";
  assert.match(sample, /exchange=&#39;替换为市场&#39;,/);
  assert.match(card, /替换为周期/);
  assert.match(card, /替换为开始时间/);
  assert.doesNotMatch(sample, /2025-01-01|2025-02-01/);
});

test("file details request 50 rows at a time and expose only listed read-only download paths", async () => {
  const urls = [];
  const p = harness(async (url) => {
    urls.push(url);
    if (url.includes("/file?")) throw new Error("file body must not be fetched by the catalog");
    if (url.includes("?limit=")) {
      const offset = Number(new URL(url, "http://local").searchParams.get("offset"));
      return response({ dataset: dataset(), total: 51, limit: 50, offset,
        files: [{ path: `btc-${offset}.parquet`, size_bytes: 1024, format: "parquet", symbol: "BTC-USDT", exchange: "okx", timeframe: "15m", start: "2025-01-01", end: "2025-02-01", row_count: 99 }] });
    }
    return response(catalog([dataset()]));
  });
  await p.api.setActive(true);
  await click(p.listeners.click, "[data-dataset-details]", { datasetDetails: "btc-15m" });
  assert.ok(urls.includes("/api/research/datasets/btc-15m?limit=50&offset=0"));
  assert.match(p.element("#dataset-records").innerHTML, /btc-0\.parquet/);
  assert.match(p.element("#dataset-records").innerHTML, /\/api\/research\/datasets\/btc-15m\/file\?path=btc-0\.parquet/);
  await click(p.listeners.click, "[data-dataset-page]", { datasetPage: "next", datasetId: "btc-15m" });
  assert.ok(urls.includes("/api/research/datasets/btc-15m?limit=50&offset=50"));
  assert.match(p.element("#dataset-records").innerHTML, /btc-50\.parquet/);
});

test("unindexed catalog and running maintenance remain explicit and can be refreshed", async () => {
  let calls = 0;
  const p = harness(async () => {
    calls++;
    return response(catalog([], { indexed: false, maintenance: { status: "running", error: null } }));
  });
  await p.api.setActive(true);
  assert.match(p.root.innerHTML, /正在重新索引/);
  assert.match(p.element("#dataset-records").innerHTML, /数据目录尚未建立索引/);
  assert.match(p.root.innerHTML, /刷新目录/);
  await click(p.listeners.click, "[data-dataset-refresh]");
  assert.equal(calls, 2);
});

test("reindex submits a same-origin POST without parameters and leaves cleanup read-only", async () => {
  const calls = [];
  const p = harness(async (url, options = {}) => {
    calls.push({ url, method: options.method || "GET", body: options.body });
    if (url.endsWith("/datasets/reindex")) return response(null, 202);
    const running = calls.filter((call) => call.method === "GET").length > 1;
    return response(catalog([], { indexed: false, maintenance: { status: running ? "running" : "idle", error: null } }));
  });
  await p.api.setActive(true);
  await click(p.listeners.click, "[data-dataset-reindex]");
  assert.deepEqual(calls.map(({ url, method, body }) => ({ url, method, body })), [
    { url: "/api/research/datasets", method: "GET", body: undefined },
    { url: "/api/research/datasets/reindex", method: "POST", body: undefined },
    { url: "/api/research/datasets", method: "GET", body: undefined },
  ]);
  assert.match(p.root.innerHTML, /索引任务正在后台运行/);
  assert.doesNotMatch(p.root.innerHTML, /没有删除接口|backtest_ready/);
});
