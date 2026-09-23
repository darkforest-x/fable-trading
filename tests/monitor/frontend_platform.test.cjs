"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const staticRoot = path.join(root, "yoyo/monitor/static");
const source = fs.readFileSync(path.join(staticRoot, "platform.js"), "utf8");
const page = fs.readFileSync(path.join(staticRoot, "index.html"), "utf8");
const app = fs.readFileSync(path.join(staticRoot, "app.js"), "utf8");
const research = fs.readFileSync(path.join(staticRoot, "research.js"), "utf8");

const response = (body, status = 200) => ({
  ok: status < 400, status,
  headers: { get: () => body === null ? "" : "application/json" },
  json: async () => body,
});

class FormDataMock {
  constructor(form) { this.values = form.values || {}; }
  get(key) { return this.values[key] ?? ""; }
  getAll(key) {
    const value = this.values[key] ?? [];
    return Array.isArray(value) ? value : [value];
  }
}

function harness(fetchImpl) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) {
      const listeners = {};
      const node = {
        _innerHTML: "", textContent: "", value: "", disabled: false, dataset: {}, listeners,
        get innerHTML() { return this._innerHTML; },
        set innerHTML(value) {
          this._innerHTML = value;
          if (id === "platform-workspace") {
            const open = value.indexOf('<section id="platform-pipelines"');
            const close = open < 0 ? -1 : value.indexOf("</section>", open);
            if (open >= 0 && close >= 0) {
              const start = value.indexOf(">", open) + 1;
              element("platform-pipelines")._innerHTML = value.slice(start, close);
            }
          }
        },
        addEventListener(type, listener) { listeners[type] = listener; },
        focus() { this.focused = true; },
      };
      elements.set(id, node);
    }
    return elements.get(id);
  };
  const window = { crypto: { randomUUID: () => "00000000-0000-4000-8000-000000000001" }, location: { hash: "" } };
  vm.runInNewContext(source, {
    window, document: { getElementById: element }, fetch: fetchImpl, FormData: FormDataMock, URL,
    Date, Intl, Number, String, Boolean, Array, Object, Math, JSON, Error, Promise, encodeURIComponent,
  }, { filename: "platform.js" });
  return { platform: window.SpikePlatform, models: window.SpikeModels, window, element };
}

const closestEvent = (selector, node, extra = {}) => ({
  target: { closest: (candidate) => candidate === selector ? node : null },
  preventDefault() {}, ...extra,
});
const overview = (extra = {}) => ({
  generated_at: "2026-09-24T01:02:03Z",
  layers: [{ id: "data", name: "数据层", description: "真实登记", inputs: ["行情"], outputs: ["数据集"], count: 197,
    components: [{ id: "catalog", name: "目录 <script>", kind: "registry", view: "datasets", status: "available", description: "有界说明", source_path: "yoyo/data/catalog.py" }] }],
  cross_cutting: ["实验登记与失败记录"],
  summary: { datasets: 197, factors: 52, models: 35, strategies: 4, experiments: 12, backtest_runs: 3, paper_runs: 2 },
  sources: [{ name: "可信来源", url: "https://example.com/docs" }, { name: "危险来源", url: "javascript:alert(1)" }],
  pipelines: [{ id: "rules", name: "规则路线", steps: ["行情", "策略", "评估"] }],
  ...extra,
});
const modelFamily = (overrides = {}) => ({ id: "vision_review", name: "VLM 辅助能力", role: "人工复核", description: "研究入口", view: "vision", count: 1, ...overrides });
const model = (overrides = {}) => ({
  id: "vision-capability", name: "VLM <img src=x onerror=alert(1)>", family: "vision_review", kind: "research_capability",
  status: "research", source_path: "yoyo/vision_research", metadata_path: null, feature_count: null,
  feature_semantics: null, objective: "辅助样本复核", experiment_ids: ["exp-1"], artifact_ids: [],
  local_exists: true, training_eligible: false, production_eligible: false, notes: "原始来源备注\n\n复核备注：旧人工笔记",
  annotation_notes: "旧人工笔记", revision: 3,
  audit: { status: "review_required", checked_at: "2026-09-24T00:00:00Z", checks: [{ name: "source_path", status: "pass", detail: "目录可读" }] },
  ...overrides,
});
const plan = (overrides = {}) => ({
  id: "pipeline-0123456789abcdef", name: "冻结规则路线", route: "rules", revision: 1, stage: "research",
  dataset_ids: ["dataset-1"], factor_ids: [], model_ids: [], strategy_id: "spike-v128", experiment_id: "exp-1", notes: "备注",
  validation: { status: "ready", blockers: [], backtest: { allowed: true, reason: "冻结口径匹配" }, paper: { allowed: false, reason: "当前条件不满足" } },
  ...overrides,
});
const options = {
  datasets: [{ id: "dataset-1", name: "BTC 原始数据" }], factors: [{ id: "factor-1", name: "趋势因子" }],
  models: [{ id: "model-1", name: "YOLO 权重", family: "yolo_detector" }],
  strategies: [{ id: "spike-v128", name: "SPIKE V12.8" }], experiments: [{ id: "exp-1", name: "冻结实验" }],
};

test("platform overview loads once, renders reported asset counts, and safely links registered components", async () => {
  const calls = [];
  const p = harness(async (url) => {
    calls.push(url);
    if (url === "/api/research/platform") return response(overview());
    if (url === "/api/research/pipelines") return response({ items: [], templates: [], options });
    throw new Error(`unexpected request ${url}`);
  });
  assert.equal(calls.length, 0);
  await p.platform.setActive(true);
  assert.deepEqual(calls, ["/api/research/platform", "/api/research/pipelines"]);
  const html = p.element("platform-workspace").innerHTML;
  assert.match(html, /个人交易、策略研究和自动策略验证，共用一套基础能力/);
  assert.match(html, /197 数据集/);
  assert.match(html, /模型已经准确或策略已经通过/);
  assert.match(html, /目录 &lt;script&gt;/);
  assert.doesNotMatch(html, /<script>alert/);
  assert.match(html, /href="#datasets"/);
  assert.match(html, /可信来源/);
  assert.match(html, /https:\/\/example\.com\/docs/);
  assert.match(html, /危险来源/);
  assert.doesNotMatch(html, /href="javascript:/);
  assert.match(html, /实验登记与失败记录/);
});

test("views preserve loading, recoverable error, and honest empty states", async () => {
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  const p = harness(() => pending);
  const loading = p.models.setActive(true);
  assert.match(p.element("models-workspace").innerHTML, /正在读取模型登记/);
  finish(response({ families: [], items: [], gates: {} }));
  await loading;
  assert.match(p.element("models-workspace").innerHTML, /模型接口没有登记制品/);

  const failed = harness(async () => { throw new Error("目录服务暂不可用"); });
  await failed.platform.setActive(true);
  assert.match(failed.element("platform-workspace").innerHTML, /目录服务暂不可用/);
  assert.match(failed.element("platform-workspace").innerHTML, /data-platform-refresh/);
});

test("models search and family filters use catalog values; audit and notes use explicit research endpoints", async () => {
  const calls = [];
  const p = harness(async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", body: init.body });
    if (url === "/api/research/models") return response({ families: [modelFamily(), modelFamily({ id: "l2_research", name: "LightGBM", view: "models", count: 1 })], items: [model(), model({ id: "l2-model", name: "TP5 SL2", family: "l2_research", source_path: "models/model.txt", notes: "历史模型" })], gates: {
      training: { allowed: false, reason: "当前阶段禁止新训练" }, production: { allowed: false, reason: "没有生产包" },
    } });
    if (url === "/api/research/models/vision-capability/audit") return response({ status: "incomplete", checked_at: "2026-09-24T02:00:00Z", checks: [
      { name: "hash", status: "not_rehashed", detail: "未读取权重" }, { name: "metadata", status: "declared_only", detail: "仅登记声明" },
      { name: "feature", status: "computed", detail: "从 header 读取" }, { name: "gate", status: "disabled", detail: "资格关闭" },
      { name: "path", status: "missing", detail: "未找到文件" }, { name: "reference", status: "unknown", detail: "无法判断" },
    ] });
    if (url === "/api/research/models/vision-capability") return response({ item: model({ status: "archived", revision: 4, annotation_notes: "新研究笔记" }) });
    throw new Error(`unexpected request ${url}`);
  });
  await p.models.setActive(true);
  assert.equal(calls[0].url, "/api/research/models");
  let html = p.element("models-workspace").innerHTML;
  assert.match(html, /VLM &lt;img src=x onerror=alert\(1\)&gt;/);
  assert.doesNotMatch(html, /<img src=x/);
  assert.match(html, /工程身份检查/);
  assert.match(html, /登记为否/);
  assert.match(html, /原始来源备注/);
  assert.match(html, /旧人工笔记/);
  assert.match(html, /annotation_notes|研究备注/);
  assert.match(html, /<textarea[^>]*>旧人工笔记<\/textarea>/);
  assert.match(html, /data-experiment="exp-1"/);
  assert.match(html, /href="#vision"/);
  assert.doesNotMatch(html, /<button[^>]*>(?:[^<]*(?:训练|promote|提升资格)[^<]*)<\/button>/i);

  p.element("models-workspace").listeners.input({ target: { id: "model-search", value: "not a match" } });
  assert.match(p.element("model-results").innerHTML, /没有匹配的模型/);
  p.element("models-workspace").listeners.input({ target: { id: "model-search", value: "VLM" } });
  assert.match(p.element("model-results").innerHTML, /VLM &lt;img/);
  assert.equal(p.element("model-result-count").textContent, "显示 1 / 2 项");
  const familySelect = { id: "model-family", value: "l2_research", focus() { this.focused = true; } };
  p.element("models-workspace").listeners.change({ target: familySelect });
  html = p.element("models-workspace").innerHTML;
  assert.match(html, /没有匹配的模型/);
  assert.equal(p.element("model-family").focused, true);
  p.element("models-workspace").listeners.change({ target: { id: "model-family", value: "all", focus() {} } });

  const auditNode = { dataset: { modelAudit: "vision-capability" } };
  p.element("models-workspace").listeners.click(closestEvent("[data-model-audit]", auditNode));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const auditCall = calls.find((call) => call.method === "POST");
  assert.deepEqual(auditCall, { url: "/api/research/models/vision-capability/audit", method: "POST", body: undefined });
  const auditHtml = p.element("model-results").innerHTML;
  assert.match(auditHtml, /信息不完整/);
  assert.match(auditHtml, /未重新计算哈希/);
  assert.match(auditHtml, /仅登记声明/);
  assert.match(auditHtml, /本次计算/);
  assert.match(auditHtml, /未执行/);
  assert.match(auditHtml, /缺失/);
  assert.match(auditHtml, /未知/);

  const form = { dataset: { modelEdit: "vision-capability", revision: "3" }, values: { stage: "archived", notes: "新研究笔记" } };
  p.element("models-workspace").listeners.submit(closestEvent('form[data-model-edit]', form));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const put = calls.find((call) => call.method === "PUT");
  assert.equal(put.url, "/api/research/models/vision-capability");
  assert.deepEqual(JSON.parse(put.body), { expected_revision: 3, stage: "archived", notes: "新研究笔记" });
  assert.doesNotMatch(put.body, /原始来源备注/);
});

test("pipeline plans create/update by revision and only start modes explicitly allowed by validation", async () => {
  const calls = [];
  let current = null;
  const p = harness(async (url, init = {}) => {
    const method = init.method || "GET";
    calls.push({ url, method, body: init.body });
    if (url === "/api/research/platform") return response(overview());
    if (url === "/api/research/pipelines" && method === "GET") return response({ items: current ? [current] : [], templates: [
      { id: "template-rules", route: "rules", name: "规则模板", description: "现有适配器" },
      { id: "template-vlm", route: "vlm", name: "VLM模板", description: "尚未适配" },
    ], options });
    if (url === "/api/research/pipelines" && method === "POST") {
      const body = JSON.parse(init.body);
      current = plan({ ...body, id: "pipeline-0123456789abcdef", revision: 1, validation: { status: "ready", blockers: [], backtest: { allowed: true, reason: "可回测" }, paper: { allowed: false, reason: "模拟不可用" } } });
      return response(current, 201);
    }
    if (url === "/api/research/pipelines/pipeline-0123456789abcdef" && method === "PUT") {
      const body = JSON.parse(init.body);
      current = plan({ ...current, ...body, revision: 2, validation: { status: "blocked", blockers: ["因子适配器尚未实现"], backtest: { allowed: false, reason: "不接受额外因子" }, paper: { allowed: false, reason: "不接受额外因子" } } });
      return response(current);
    }
    if (url === "/api/research/pipelines/pipeline-0123456789abcdef/runs") return response({ kind: "backtest", run: { id: "job-1" } }, 202);
    throw new Error(`unexpected request ${method} ${url}`);
  });
  await p.platform.setActive(true);
  let html = p.element("platform-pipelines").innerHTML;
  assert.match(html, /版本化研究流程/);
  assert.match(html, /套用到计划/);
  assert.match(html, /maxlength="120"/);
  assert.match(html, /multiple/);
  const workspace = p.element("platform-workspace");
  const createForm = { dataset: { pipelineId: "", revision: "" }, values: {
    name: "因果研究计划", route: "rules", dataset_ids: ["dataset-1"], factor_ids: ["factor-1"], model_ids: [],
    strategy_id: "spike-v128", experiment_id: "exp-1", notes: "研究计划", stage: "research",
  } };
  workspace.listeners.submit(closestEvent("form[data-pipeline-form]", createForm));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const createCall = calls.find((call) => call.method === "POST" && call.url === "/api/research/pipelines");
  assert.deepEqual(JSON.parse(createCall.body), {
    name: "因果研究计划", route: "rules", dataset_ids: ["dataset-1"], factor_ids: ["factor-1"], model_ids: [],
    strategy_id: "spike-v128", experiment_id: "exp-1", notes: "研究计划", stage: "research",
  });
  html = p.element("platform-pipelines").innerHTML;
  assert.match(html, /已接通/);
  assert.match(html, /回测固定周期/);
  assert.match(html, /15m \+ 1H/);
  assert.match(html, /class="pipeline-paper-timeframes" hidden/);
  assert.ok(html.indexOf("</form>") < html.indexOf("<form data-pipeline-run-form"), "run form must be a sibling of the edit form");

  const updateForm = { dataset: { pipelineId: current.id, revision: "1" }, values: {
    name: "更新后的因果计划", route: "rules", dataset_ids: ["dataset-1"], factor_ids: ["factor-1"], model_ids: [],
    strategy_id: "spike-v128", experiment_id: "exp-1", notes: "补充验证", stage: "research",
  } };
  workspace.listeners.submit(closestEvent("form[data-pipeline-form]", updateForm));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const putCall = calls.find((call) => call.method === "PUT");
  assert.equal(putCall.url, "/api/research/pipelines/pipeline-0123456789abcdef");
  assert.deepEqual(JSON.parse(putCall.body), {
    name: "更新后的因果计划", route: "rules", dataset_ids: ["dataset-1"], factor_ids: ["factor-1"], model_ids: [],
    strategy_id: "spike-v128", experiment_id: "exp-1", notes: "补充验证", stage: "research", expected_revision: 1,
  });
  html = p.element("platform-pipelines").innerHTML;
  assert.match(html, /因子适配器尚未实现/);
  assert.match(html, /运行受限/);
  assert.doesNotMatch(html, /data-pipeline-run-form/);

  current = plan({ ...current, revision: 2, validation: { status: "ready", blockers: [], backtest: { allowed: true, reason: "固定周期" }, paper: { allowed: true, reason: "可模拟" } } });
  await p.platform.refresh();
  html = p.element("platform-pipelines").innerHTML;
  assert.match(html, /name="timeframes"/);
  assert.match(html, /模拟观察周期/);
  const backtestBlock = { hidden: false }, paperBlock = { hidden: true };
  const runFormNode = { querySelector: (selector) => selector === ".pipeline-backtest-timeframes" ? backtestBlock : paperBlock };
  workspace.listeners.change({ target: { name: "mode", value: "paper", closest: () => runFormNode } });
  assert.equal(backtestBlock.hidden, true);
  assert.equal(paperBlock.hidden, false);
  workspace.listeners.change({ target: { name: "mode", value: "backtest", closest: () => runFormNode } });
  assert.equal(backtestBlock.hidden, false);
  assert.equal(paperBlock.hidden, true);
  const runForm = { dataset: { pipelineId: current.id, revision: "2" }, values: {
    mode: "backtest", symbols: "BTCUSDT, ethusdt", timeframes: ["15m"],
  } };
  workspace.listeners.submit(closestEvent("form[data-pipeline-run-form]", runForm));
  await new Promise((resolve) => setTimeout(resolve, 0));
  const runCall = calls.find((call) => call.method === "POST" && call.url.endsWith("/runs"));
  assert.deepEqual(JSON.parse(runCall.body), {
    expected_revision: 2, mode: "backtest", symbols: ["BTCUSDT", "ETHUSDT"], timeframes: ["15m", "1H"],
    request_id: "00000000-0000-4000-8000-000000000001",
  });
  assert.equal(p.window.location.hash, "#backtests");
});

test("navigation groups and hooks include platform/model views while preserving VLM research wording", () => {
  const nav = page.match(/<nav class="nav-list"[\s\S]*?<\/nav>/)?.[0] || "";
  const labels = [...nav.matchAll(/class="sidebar-label nav-group-label"[^>]*>([^<]+)/g)].map((match) => match[1]);
  assert.deepEqual(labels, ["个人交易", "基础能力", "研究开发", "验证与运行", "运维"]);
  assert.ok(nav.indexOf('data-view="platform"') < nav.indexOf('data-view="datasets"'));
  assert.ok(nav.indexOf('data-view="models"') < nav.indexOf('data-view="research"'));
  assert.ok(nav.indexOf('data-view="backtests"') < nav.indexOf('data-view="paper"'));
  assert.match(page, /href="#platform"|data-view="platform"/);
  assert.match(page, /id="platform-view"/);
  assert.match(page, /id="models-view"/);
  assert.match(page, /\/static\/platform\.css/);
  assert.match(page, /\/static\/platform\.js/);
  assert.match(app, /window\.SpikePlatform\?\.setActive\(state\.view === "platform"\)/);
  assert.match(app, /window\.SpikeModels\?\.setActive\(state\.view === "models"\)/);
  assert.match(app, /if \(state\.view === "platform"\)/);
  assert.match(app, /if \(state\.view === "models"\)/);
  assert.match(research, /VLM验证/);
  assert.doesNotMatch(research, /视觉验证/);
  assert.match(fs.readFileSync(path.join(staticRoot, "platform.css"), "utf8"), /@media\(max-width:1240px\) \{ \.nav-list>\.nav-group-label \{ display:none; \} \}/);
});
