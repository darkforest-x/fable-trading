"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../../yoyo/monitor/static/knowledge.js"), "utf8");
const result = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const knowledge = { id: "knowledge-a", revision: 2, title: "风险 <script>", topic: "risk", kind: "principle", status: "organized", evidence_status: "unverified", summary: "有待验证", body: "测试正文", source_urls: ["https://example.com/source"], evidence: "", limitations: "样本不足", experiment_ids: [], notion_url: "javascript:alert(1)" };
const learning = { id: "learning-a", revision: 1, title: "学习问题", topic: "market", stage: "learning", source_urls: [], question: "为什么", goal: "自己的解释", notes: "笔记", retrieval_notes: "复述", next_review: "2020-01-01", knowledge_ids: [] };
class FormDataMock {
  constructor(form) { this.values = form.values; }
  get(key) { return this.values[key] ?? ""; }
  getAll(key) { const value = this.values[key]; return Array.isArray(value) ? value : value ? [value] : []; }
}
function harness(custom) {
  const roots = {};
  for (const v of ["learning", "knowledge", "content"]) for (const id of [`${v}-workspace`, `${v}-cards`]) roots[id] = { innerHTML: "", listeners: {}, form: null, addEventListener(type, fn) { this.listeners[type] = fn; }, querySelector() { return this.form; } };
  const calls = [], window = { location: { hash: "" }, confirm: () => true };
  const defaults = {
    "knowledge-hub": { topics: [{ id: "market", name: "市场机制", description: "理解市场" }, { id: "risk", name: "风险与执行", description: "管理风险" }], platforms: [{ id: "zhihu", name: "知乎", format: "长文", guidance: "保留来源" }], notion: { hub: "https://app.notion.com/p/hub", notes: "javascript:alert(1)" } },
    learning: { items: [learning] }, knowledge: { items: [knowledge] }, content: { items: [] },
  };
  vm.runInNewContext(source, { window, document: { getElementById: (id) => roots[id] || null }, FormData: FormDataMock, URL, Date, Intl, navigator: { clipboard: { writeText: async () => {} } },
    fetch: async (url, options) => { const route = url.replace("/api/research/", ""); calls.push({ route, options }); return (await custom?.(route, options)) || result(defaults[route]); },
  });
  return { api: window.SpikeKnowledge, roots, calls, window };
}
async function click(h, view, dataset) { await h.roots[`${view}-workspace`].listeners.click({ target: { closest: () => ({ dataset }) } }); }
function fill(h, view, values) {
  const form = { dataset: { knowledgeForm: view }, values };
  h.roots[`${view}-workspace`].form = form;
  h.roots[`${view}-workspace`].listeners.input({ target: { dataset: {}, closest: () => form } });
  return form;
}
async function submit(h, view, form) {
  h.roots[`${view}-workspace`].form = null;
  h.roots[`${view}-workspace`].listeners.submit({ target: { closest: () => form }, preventDefault() {} });
  await new Promise((resolve) => setImmediate(resolve));
}
test("three views load once, expose honest Notion links and escape persisted text", async () => {
  const h = harness(); await h.api.setView("knowledge");
  assert.equal(h.calls.length, 4);
  const html = h.roots["knowledge-workspace"].innerHTML;
  assert.match(html, /风险 &lt;script&gt;/); assert.doesNotMatch(html, /href="javascript:/);
  assert.match(html, /当前尚未自动同步/); assert.match(html, /待验证/);
  await h.api.setView("content"); await h.api.setView("learning"); assert.equal(h.calls.length, 4);
  assert.match(h.roots["content-workspace"].innerHTML, /当前没有自动发布连接/);
  assert.match(h.roots["learning-workspace"].innerHTML, /已到复习日期/);
});
test("topic, status and evidence filters combine without losing entered drafts", async () => {
  const h = harness(); await h.api.setView("knowledge");
  await click(h, "knowledge", { topic: "market" }); assert.match(h.roots["knowledge-workspace"].innerHTML, /没有符合筛选条件/);
  await click(h, "knowledge", { clearFilters: "" }); assert.match(h.roots["knowledge-workspace"].innerHTML, /风险 &lt;script&gt;/);
  h.roots["knowledge-workspace"].listeners.change({ target: { dataset: { filter: "evidence" }, value: "supported" } });
  assert.match(h.roots["knowledge-cards"].innerHTML, /没有符合筛选条件/);
});
test("save conflict retains original revision and unsaved draft across navigation", async () => {
  const h = harness(async (route, options) => route === "knowledge/knowledge-a" ? options.method === "PUT" ? result({ detail: "版本冲突" }, 409) : result({ ...knowledge, history: [] }) : null);
  await h.api.setView("knowledge"); await click(h, "knowledge", { editRecord: knowledge.id });
  const form = fill(h, "knowledge", { ...knowledge, title: "自己的新标题", source_urls: knowledge.source_urls.join("\n"), experiment_ids: "" });
  await submit(h, "knowledge", form);
  assert.match(h.roots["knowledge-workspace"].innerHTML, /版本冲突/);
  await h.api.setView("learning"); await h.api.setView("knowledge");
  assert.match(h.roots["knowledge-workspace"].innerHTML, /自己的新标题/);
  const request = JSON.parse(h.calls.find((c) => c.options.method === "PUT").options.body);
  assert.equal(request.expected_revision, 2);
});
test("knowledge becomes content draft with evidence and version reference; never publishes", async () => {
  const h = harness(); await h.api.setView("knowledge"); await click(h, "knowledge", { compose: knowledge.id });
  assert.equal(h.window.location.hash, "content"); await h.api.setView("content");
  await click(h, "content", { addPlatform: "zhihu" });
  const html = h.roots["content-workspace"].innerHTML;
  assert.match(html, /knowledge-a@2/); assert.match(html, /样本不足/); assert.match(html, /待验证/);
  assert.match(html, /保留来源/); assert.equal(h.calls.some((c) => c.options.method), false);
});
test("failed load offers retry and never renders a false empty success", async () => {
  const h = harness(() => { throw new Error("连接失败"); }); await h.api.setView("learning");
  assert.match(h.roots["learning-workspace"].innerHTML, /连接失败/);
  assert.match(h.roots["learning-workspace"].innerHTML, /读取失败，请重试/);
});
