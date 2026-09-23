"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const project = path.resolve(__dirname, "../..");
const source = fs.readFileSync(path.join(project, "yoyo/monitor/static/manual.js"), "utf8");

const response = (body, status = 200) => ({
  ok: status < 400,
  status,
  headers: { get: () => body === null ? "" : "application/json" },
  json: async () => body,
});

class FormDataMock {
  constructor(form) { this.values = form.values || {}; }
  get(key) { return this.values[key] ?? ""; }
  getAll(key) {
    const value = this.values[key] ?? "";
    return Array.isArray(value) ? value : [value];
  }
}

const playbook = (overrides = {}) => ({
  id: "manual-playbook-aaaaaaaaaaaaaaaa", revision: 2, updated_at: "2026-09-24T01:00:00Z",
  name: "规则 <script>", status: "ready", setup: "密集后启动", context: "顺势",
  entry_rule: "收盘突破", invalidation_rule: "回到区间", exit_rule: "按计划退出",
  avoid_rule: "不追高", management_rule: "记录调整", ...overrides,
});

const session = (overrides = {}) => ({
  id: "manual-session-bbbbbbbbbbbbbbbb", revision: 3, updated_at: "2026-09-24T01:00:00Z",
  date: "2026-09-24", title: "盘前计划", market_context: "等待数据",
  watchlist: "BTC", focus: "", avoid: "", availability: "", stop_rule: "",
  risk_budget_usdt: null, ...overrides,
});

const ticket = (overrides = {}) => ({
  id: "manual-ticket-cccccccccccccccc", revision: 4, updated_at: "2026-09-24T01:00:00Z",
  symbol: "BTC-USDT-SWAP", side: "long", timeframe: "15m", playbook_id: playbook().id,
  playbook_revision: 2, session_id: null, mode: "real", status: "planned",
  thesis: "等待突破", trigger: "收盘突破", invalidation: "回到区间", exit_plan: "手动退出",
  signal_ref: "", planned_entry: 100, planned_stop: 95, risk_budget_usdt: 10,
  playbook_snapshot: playbook(), session_snapshot: null,
  execution: {}, outcome: { net_pnl_usdt: null, net_r: null }, review: null,
  ...overrides,
});

const overview = (overrides = {}) => ({
  playbooks: [playbook()],
  sessions: [session()],
  tickets: [ticket()],
  templates: [{
    id: "ma-launch", name: "均线启动草稿", status: "draft", setup: "原始设置",
    context: "背景", entry_rule: "", invalidation_rule: "", exit_rule: "",
    avoid_rule: "", management_rule: "", note: "历史提案，不含已确认周期。",
  }],
  summary: {
    watching: 0, planned: 1, open: 0, closed: 2, skipped: 0, pending_review: 1,
    real: { closed: 2, known_pnl: 1, unknown_pnl: 1, net_pnl_usdt: 0, cohorts: {
      prospective: { closed: 1, known_pnl: 1, unknown_pnl: 0, net_pnl_usdt: 0 },
      retrospective: { closed: 1, known_pnl: 0, unknown_pnl: 1, net_pnl_usdt: null },
    } },
    practice: { closed: 0, known_pnl: 0, unknown_pnl: 0, net_pnl_usdt: null, cohorts: {} },
  },
  sources: [
    { name: "历史个人手册", url: "https://example.com/manual-v2" },
    { name: "不安全来源", url: "javascript:alert(1)" },
  ],
  ...overrides,
});

function harness(fetchImpl) {
  const root = { innerHTML: "", listeners: {}, addEventListener(type, listener) { this.listeners[type] = listener; } };
  const window = {};
  vm.runInNewContext(source, {
    window,
    document: { getElementById: (id) => id === "manual-workspace" ? root : null },
    fetch: fetchImpl, FormData: FormDataMock, URL, Date, Intl, Number, String,
    Boolean, Array, Object, Math, JSON, Error, Promise, Set, encodeURIComponent, decodeURIComponent,
  }, { filename: "manual.js" });
  return { api: window.SpikeManual, root };
}

function click(harnessValue, selector, dataset = {}) {
  const node = { dataset };
  harnessValue.root.listeners.click({ target: { closest: (candidate) => candidate === selector ? node : null } });
}

function form(kind, values, dataset = {}) {
  const marker = "data-manual-" + kind + "-form";
  return {
    marker, values, dataset: { manualDraft: kind + "-draft", recordId: "", revision: "", ...dataset },
    matches(selector) { return selector.includes(marker); },
  };
}

function submit(harnessValue, formNode) {
  harnessValue.root.listeners.submit({
    target: { closest: (selector) => selector.includes(formNode.marker) ? formNode : null },
    preventDefault() {},
  });
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

test("manual workspace separates real/practice and cohorts, preserves unknown PnL, and escapes sources and records", async () => {
  let reads = 0;
  const h = harness(async (url) => {
    assert.equal(url, "/api/research/manual");
    reads += 1;
    return response(overview());
  });
  h.api.setActive(false);
  assert.equal(reads, 0);
  await h.api.setActive(true);
  assert.equal(reads, 1);
  assert.match(h.root.innerHTML, /我的规则/);
  assert.match(h.root.innerHTML, /规则 &lt;script&gt;/);
  assert.doesNotMatch(h.root.innerHTML, /<script>/);
  assert.match(h.root.innerHTML, /历史来源模板/);
  assert.match(h.root.innerHTML, /历史提案，不含已确认周期/);

  click(h, "[data-manual-tab]", { manualTab: "tickets" });
  assert.match(h.root.innerHTML, /练习/);
  assert.match(h.root.innerHTML, /实盘手工记录/);
  assert.match(h.root.innerHTML, /事前留痕/);
  assert.match(h.root.innerHTML, /事后补录/);
  assert.match(h.root.innerHTML, /未填（未知）/);
  assert.match(h.root.innerHTML, /所有结果均由本人手工填写，尚未与交易所核验/);
  assert.match(h.root.innerHTML, /href="https:\/\/example\.com\/manual-v2"/);
  assert.doesNotMatch(h.root.innerHTML, /href="javascript:/);
  assert.doesNotMatch(source, /localStorage|setInterval/);
});

test("session update sends the local date unchanged and a 409 preserves escaped form values", async () => {
  const calls = [];
  const h = harness(async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", body: init.body });
    if (url === "/api/research/manual") return response(overview());
    if (url === "/api/research/manual/sessions/" + session().id) {
      return response({ detail: [{ loc: ["body", "expected_revision"], msg: "revision conflict" }] }, 409);
    }
    throw new Error("unexpected " + url);
  });
  await h.api.setActive(true);
  click(h, "[data-manual-tab]", { manualTab: "sessions" });
  const edit = form("session", {
    date: "2026-09-24", title: "复核 <plan>", market_context: "", watchlist: "BTC",
    focus: "", avoid: "", availability: "", stop_rule: "", risk_budget_usdt: "",
  }, { manualDraft: "session-" + session().id, recordId: session().id, revision: "3" });
  submit(h, edit);
  await settle();
  const put = calls.find((item) => item.method === "PUT");
  assert.ok(put);
  assert.equal(put.url, "/api/research/manual/sessions/" + session().id);
  assert.deepEqual(JSON.parse(put.body), {
    date: "2026-09-24", title: "复核 <plan>", market_context: "", watchlist: "BTC",
    focus: "", avoid: "", availability: "", stop_rule: "", risk_budget_usdt: null, expected_revision: 3,
  });
  assert.match(h.root.innerHTML, /版本冲突/);
  assert.match(h.root.innerHTML, /复核 &lt;plan&gt;/);
  assert.doesNotMatch(h.root.innerHTML, /<plan>/);
});

test("actual-fill event converts local time, permits unknown original stop, and keeps notes and PnL explicit", async () => {
  const calls = [];
  const current = overview({ tickets: [ticket({ status: "planned", execution: {} })] });
  const h = harness(async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", body: init.body });
    if (url === "/api/research/manual") return response(current);
    if (url === "/api/research/manual/tickets/" + ticket().id + "/events") return response({ ok: true }, 200);
    throw new Error("unexpected " + url);
  });
  await h.api.setActive(true);
  const event = form("event", {
    occurred_at: "2020-01-02T12:30", entry_price: "101.5", initial_stop: "",
    quantity: "2", protection_status: "confirmed", notes: "历史成交补录",
  }, { manualDraft: "event-" + ticket().id + "-open", manualAction: "open", manualTicketId: ticket().id, revision: "4" });
  submit(h, event);
  await settle();
  const call = calls.find((item) => item.url.endsWith("/events"));
  assert.ok(call);
  const payload = JSON.parse(call.body);
  assert.equal(payload.expected_revision, 4);
  assert.equal(payload.action, "open");
  assert.equal(payload.occurred_at, new Date("2020-01-02T12:30").toISOString());
  assert.equal(payload.entry_price, 101.5);
  assert.equal(payload.initial_stop, null);
  assert.equal(payload.quantity, 2);
  assert.equal(payload.protection_status, "confirmed");
  assert.equal(payload.notes, "历史成交补录");
  assert.equal(payload.net_pnl_usdt, null);
  assert.doesNotMatch(JSON.stringify(payload), /exchange|order|sync/i);
});

test("open-position management is a manual event and surfaces top-level protection history", async () => {
  const calls = [];
  const open = ticket({
    revision: 6, status: "open",
    execution: { opened_at: "2026-09-24T01:00:00Z", entry_price: 100, initial_stop: null, quantity: 1, initial_risk_usdt: null },
    management: [{ protection_status: "confirmed", notes: "本人确认保护状态", recorded_at: "2026-09-24T02:00:00Z" }],
  });
  const h = harness(async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", body: init.body });
    if (url === "/api/research/manual") return response(overview({ tickets: [open] }));
    if (url === "/api/research/manual/tickets/" + open.id) return response({ ...open, history: [] });
    if (url === "/api/research/manual/tickets/" + open.id + "/events") return response({ ok: true });
    throw new Error("unexpected " + url);
  });
  await h.api.setActive(true);
  click(h, "[data-manual-tab]", { manualTab: "tickets" });
  click(h, "[data-manual-select-ticket]", { manualSelectTicket: open.id });
  await settle();
  assert.match(h.root.innerHTML, /记录持仓期间的人工管理/);
  assert.match(h.root.innerHTML, /本人确认保护状态/);
  const manage = form("event", { protection_status: "none", notes: "本人手工移动止损，数量未变" }, {
    manualDraft: "event-" + open.id + "-manage", manualAction: "manage", manualTicketId: open.id, revision: "6",
  });
  submit(h, manage);
  await settle();
  const call = calls.find((item) => item.url.endsWith("/events"));
  assert.ok(call);
  const payload = JSON.parse(call.body);
  assert.equal(payload.action, "manage");
  assert.equal(payload.expected_revision, 6);
  assert.equal(payload.protection_status, "none");
  assert.equal(payload.notes, "本人手工移动止损，数量未变");
  assert.equal(payload.entry_price, null);
  assert.equal(payload.initial_stop, null);
  assert.equal(payload.net_pnl_usdt, null);
});

test("first signal waits for data without claiming there are unsaved edits", async () => {
  let resolveFetch;
  const h = harness(() => new Promise((resolve) => { resolveFetch = resolve; }));
  h.api.fromSignal({ symbol: "BTC-USDT-SWAP", side: "long", timeframe: "15m", signal_ref: "signal:7" });
  assert.doesNotMatch(h.root.innerHTML, /当前有未保存的编辑/);
  resolveFetch(response(overview()));
  await settle();
  assert.match(h.root.innerHTML, /value="BTC-USDT-SWAP"/);
  assert.match(h.root.innerHTML, /未保存的观察草稿/);
  assert.doesNotMatch(h.root.innerHTML, /当前有未保存的编辑/);
});

test("signal handoff keeps dirty drafts until the user explicitly switches to the incoming signal", async () => {
  const h = harness(async (url) => {
    if (url === "/api/research/manual") return response(overview());
    throw new Error("unexpected " + url);
  });
  await h.api.setActive(true);
  click(h, "[data-manual-new-ticket]");
  const oldDraft = form("ticket", { symbol: "ETH-USDT-SWAP", mode: "practice", status: "watching" }, {
    manualDraft: "ticket-new-1",
  });
  h.root.listeners.input({ target: { closest: (selector) => selector === "form[data-manual-draft]" ? oldDraft : null } });
  assert.equal(h.api.fromSignal({
    symbol: "BTC-USDT-SWAP", side: "long", timeframe: "15m", signal_ref: "signal:7 · live · 123",
  }), true);
  assert.match(h.root.innerHTML, /已保留当前草稿和新信号/);
  assert.match(h.root.innerHTML, /使用此信号新建观察/);
  click(h, "[data-manual-keep-draft]");
  assert.match(h.root.innerHTML, /value="ETH-USDT-SWAP"/);
  assert.equal(h.api.fromSignal({
    symbol: "BTC-USDT-SWAP", side: "short", timeframe: "1H", signal_ref: "joint:3 · replay · 456",
  }), true);
  click(h, "[data-manual-use-signal]");
  assert.match(h.root.innerHTML, /value="BTC-USDT-SWAP"/);
  assert.match(h.root.innerHTML, /joint:3 · replay · 456/);
  assert.match(h.root.innerHTML, /value="practice" selected/);
  assert.match(h.root.innerHTML, /观察中（可不完整）/);
});

test("version-snapshot history renders action notes and leaves missing closed PnL unknown", async () => {
  const closed = ticket({
    status: "closed", execution: { opened_at: "2026-09-23T10:00:00Z", entry_price: 100, quantity: 1, initial_stop: null },
    management: [{ protection_status: "confirmed", notes: "本人确认保护单已设置", recorded_at: "2026-09-23T10:30:00Z" }],
    outcome: { closed_at: "2026-09-23T11:00:00Z", net_pnl_usdt: null, net_r: null, notes: "平仓时盈亏未记" },
  });
  const h = harness(async (url) => {
    if (url === "/api/research/manual") return response(overview({ tickets: [closed] }));
    if (url === "/api/research/manual/tickets/" + closed.id) return response({
      ...closed,
      history: [
        { ...closed, revision: 1, last_event: null },
        { ...closed, revision: 5, last_event: { action: "close", recorded_at: "2026-09-23T11:00:01Z" } },
      ],
    });
    throw new Error("unexpected " + url);
  });
  await h.api.setActive(true);
  click(h, "[data-manual-tab]", { manualTab: "tickets" });
  click(h, "[data-manual-select-ticket]", { manualSelectTicket: closed.id });
  await settle();
  assert.match(h.root.innerHTML, /v1 · 保存计划版本/);
  assert.match(h.root.innerHTML, /v5 · 登记全部平仓/);
  assert.match(h.root.innerHTML, /平仓时盈亏未记/);
  assert.match(h.root.innerHTML, /净盈亏未填（未知）/);
  assert.match(h.root.innerHTML, /未记录，初始风险与 R 未知/);
  assert.match(h.root.innerHTML, /本人记录的保护确认（最新）/);
  assert.match(h.root.innerHTML, /本人确认保护单已设置/);
  assert.doesNotMatch(h.root.innerHTML, /r_basis/);
});
