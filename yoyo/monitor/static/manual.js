/* Personal manual-trading journal. This view records user-entered plans and fills;
 * it has no exchange client and never submits or synchronizes orders. */
(() => {
  "use strict";

  const rootId = "manual-workspace";
  const endpoint = "/api/research/manual";
  const $ = (id) => document.getElementById(id);
  const arr = (value) => Array.isArray(value) ? value : [];
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value || {}, key);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
  const finite = (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value));
  const num = (value) => finite(value) ? Number(value) : null;
  const fmtNum = (value, digits = 2) => finite(value)
    ? Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits })
    : "未填（未知）";
  const fmtCount = (value) => finite(value) ? Number(value).toLocaleString("zh-CN") : "—";
  const recordLabel = (value) => value === null || value === undefined || value === "" ? "未记录" : String(value);
  const labels = {
    tabs: { playbooks: "我的规则", sessions: "盘前计划", tickets: "机会与交易", review: "复盘" },
    playbookStatus: { draft: "草稿", ready: "本人确认可用", archived: "已归档" },
    ticketStatus: { watching: "观察中", planned: "已计划", open: "已成交（人工登记）", closed: "已平仓", skipped: "已放弃" },
    action: { open: "登记已成交", close: "登记全部平仓", skip: "记录放弃", review: "保存复盘", reconcile: "补记 / 更正", manage: "保存持仓记录" },
    mode: { practice: "练习", real: "实盘手工记录" },
    side: { long: "做多", short: "做空" },
    adherence: { followed: "遵守计划", deviated: "偏离计划", unknown: "未确认" },
    protection: { unconfirmed: "未确认", confirmed: "本人确认已设置", none: "未设置" },
  };
  const state = {
    active: false, bound: false, loading: false, loaded: false,
    loadError: "", actionError: "", actionMessage: "", data: null,
    tab: "playbooks", selectedPlaybookId: "", playbookCreate: true,
    selectedSessionId: "", sessionCreate: true,
    selectedTicketId: "", ticketCreate: false, ticketDetail: null,
    detailLoading: false, detailError: "", savingKey: "",
    drafts: Object.create(null), dirtyDrafts: new Set(), ticketDraftCounter: 0,
    ticketDraftKey: "ticket-new", pendingSignal: null,
  };

  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  function validationMessage(detail) {
    if (typeof detail === "string") return detail;
    if (!Array.isArray(detail)) return "";
    return detail.map((entry) => {
      if (typeof entry === "string") return entry;
      if (!entry || typeof entry !== "object") return String(entry);
      const location = arr(entry.loc).filter((part) => part !== "body").join(".");
      const message = entry.msg || entry.message || JSON.stringify(entry);
      return (location ? location + "：" : "") + message;
    }).filter(Boolean).join("；");
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      cache: "no-store",
      headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) },
      ...options,
    });
    let body = null;
    const type = response.headers?.get?.("content-type") || "";
    if (type.includes("json")) {
      try { body = await response.json(); } catch { body = null; }
    }
    if (!response.ok) {
      const detail = validationMessage(body?.detail) || validationMessage(body?.errors) || validationMessage(body?.message);
      const message = detail || (response.status === 409 ? "记录版本已变化，请先刷新并检查最新版本。" : "服务返回 HTTP " + response.status);
      throw new ApiError(message, response.status);
    }
    return body;
  }

  function safeExternal(value) {
    try {
      const url = new URL(String(value || ""));
      return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : "";
    } catch { return ""; }
  }

  function sourceHTML() {
    const sources = arr(state.data?.sources);
    if (!sources.length) return "";
    return '<div class="manual-sources" aria-label="规则来源">' +
      sources.map((source) => {
        const name = esc(source?.name || "来源");
        const href = safeExternal(source?.url || source?.href);
        return href
          ? '<a href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + name + ' ↗</a>'
          : '<span>' + name + '</span>';
      }).join("") + "</div>";
  }

  function rememberForm(form) {
    const key = form?.dataset?.manualDraft;
    if (!key) return;
    state.dirtyDrafts.add(key);
    const values = new FormData(form);
    const names = new Set();
    if (form.elements) {
      for (const element of Array.from(form.elements)) if (element.name) names.add(element.name);
    }
    if (form.values && typeof form.values === "object") Object.keys(form.values).forEach((name) => names.add(name));
    const draft = Object.create(null);
    for (const name of names) {
      const all = values.getAll(name);
      draft[name] = all.length > 1 ? all : values.get(name) ?? "";
    }
    state.drafts[key] = draft;
  }

  function val(draft, field, record, fallback = "") {
    if (own(draft, field)) return draft[field];
    if (own(record, field)) return record[field];
    return fallback;
  }

  function input(name, label, value, options = {}) {
    const type = options.type || "text";
    const max = options.maxlength ? ' maxlength="' + esc(options.maxlength) + '"' : "";
    const min = options.min !== undefined ? ' min="' + esc(options.min) + '"' : "";
    const step = options.step ? ' step="' + esc(options.step) + '"' : "";
    const required = options.required ? " required" : "";
    const placeholder = options.placeholder ? ' placeholder="' + esc(options.placeholder) + '"' : "";
    const help = options.help ? '<small>' + esc(options.help) + "</small>" : "";
    return '<label class="manual-field"><span>' + esc(label) + '</span><input name="' + esc(name) +
      '" type="' + esc(type) + '" value="' + esc(value) + '"' + max + min + step + required + placeholder +
      (options.autocomplete ? ' autocomplete="' + esc(options.autocomplete) + '"' : "") + ">" + help + "</label>";
  }

  function textarea(name, label, value, options = {}) {
    const rows = options.rows || 3;
    const max = options.maxlength ? ' maxlength="' + esc(options.maxlength) + '"' : "";
    const required = options.required ? " required" : "";
    const help = options.help ? '<small>' + esc(options.help) + "</small>" : "";
    return '<label class="manual-field manual-field-wide"><span>' + esc(label) + '</span><textarea name="' + esc(name) +
      '" rows="' + rows + '"' + max + required + ">" + esc(value) + "</textarea>" + help + "</label>";
  }

  function select(name, label, value, choices, options = {}) {
    const required = options.required ? " required" : "";
    const help = options.help ? '<small>' + esc(options.help) + "</small>" : "";
    const optionsHTML = choices.map((choice) => {
      const itemValue = choice.value;
      return '<option value="' + esc(itemValue) + '"' + (String(itemValue) === String(value ?? "") ? " selected" : "") + ">" +
        esc(choice.label) + "</option>";
    }).join("");
    return '<label class="manual-field"><span>' + esc(label) + '</span><select name="' + esc(name) + '"' + required + ">" +
      optionsHTML + "</select>" + help + "</label>";
  }

  function numberField(name, label, value, options = {}) {
    return input(name, label, value, {
      type: "number", step: "any", min: options.min, required: options.required,
      placeholder: options.placeholder, help: options.help,
    });
  }

  function formMessage() {
    if (state.actionError) return '<div class="research-error manual-message" role="alert">' + esc(state.actionError) + "</div>";
    if (state.actionMessage) return '<div class="manual-message manual-success" role="status">' + esc(state.actionMessage) + "</div>";
    return "";
  }

  function signalPromptHTML() {
    if (!state.pendingSignal || !state.dirtyDrafts.size) return "";
    return '<div class="manual-signal-prompt" role="status"><div><strong>当前有未保存的编辑</strong>' +
      '<p>已保留当前草稿和新信号。明确选择后才会切换到新机会表单。</p></div>' +
      '<button type="button" class="research-button primary" data-manual-use-signal>使用此信号新建观察</button>' +
      '<button type="button" class="research-button" data-manual-keep-draft>保留当前草稿</button></div>';
  }

  function renderTabs() {
    return '<nav class="manual-tabs" role="tablist" aria-label="个人手动交易工作区">' +
      Object.entries(labels.tabs).map(([id, label]) =>
        '<button type="button" role="tab" data-manual-tab="' + id + '" aria-selected="' + (state.tab === id) + '"' +
        (state.tab === id ? ' class="selected"' : "") + ">" + label + "</button>").join("") + "</nav>";
  }

  function playbookFields(draft, record) {
    const fields = [
      ["setup", "形态与前提", 3], ["context", "市场环境", 3], ["entry_rule", "入场规则", 3],
      ["invalidation_rule", "失效条件", 3], ["exit_rule", "退出规则", 3], ["avoid_rule", "不做条件", 3],
      ["management_rule", "持仓管理", 3],
    ];
    return '<div class="manual-form-grid">' + fields.map(([key, title, rows]) =>
      textarea(key, title, val(draft, key, record), { rows, maxlength: 4000 })).join("") + "</div>";
  }

  function playbookRow(item) {
    const selected = String(item.id) === String(state.selectedPlaybookId);
    return '<button type="button" class="manual-record' + (selected ? " selected" : "") +
      '" data-manual-select-playbook="' + esc(item.id) + '"><span class="manual-record-main"><strong>' +
      esc(item.name || "未命名规则") + '</strong><small>v' + esc(item.revision ?? "?") + " · " +
      esc(labels.playbookStatus[item.status] || item.status || "草稿") + "</small></span><span class=\"manual-arrow\">›</span></button>";
  }

  function templateCard(template) {
    return '<article class="manual-template"><div><strong>' + esc(template.name || template.id) +
      '</strong><span class="research-badge">' + esc(labels.playbookStatus[template.status] || "规则草稿") +
      '</span></div><p>' + esc(template.note || "来源与适用条件需本人复核。") + '</p><button type="button" class="research-button" data-manual-template="' +
      esc(template.id) + '">套用为新规则草稿</button></article>';
  }

  function playbookEditor() {
    const records = arr(state.data?.playbooks);
    const record = records.find((item) => String(item.id) === String(state.selectedPlaybookId));
    const creating = state.playbookCreate || !record;
    const key = creating ? "playbook-new" : "playbook-" + record.id;
    const draft = state.drafts[key] || {};
    const selectedStatus = val(draft, "status", record, "draft");
    const statusChoices = [
      { value: "draft", label: "草稿" }, { value: "ready", label: "本人确认可用" },
      { value: "archived", label: "归档" },
    ];
    const templateChoices = [{ value: "", label: "不套用模板" }].concat(
      arr(state.data?.templates).map((item) => ({ value: item.id, label: item.name || item.id })),
    );
    const sourceValue = val(draft, "source_template", record, "");
    return '<form class="manual-card manual-editor" data-manual-playbook-form data-manual-draft="' + key +
      '" data-record-id="' + esc(record?.id || "") + '" data-revision="' + esc(record?.revision ?? "") + '">' +
      '<div class="manual-card-heading"><div><p class="manual-kicker">我的规则</p><h3>' +
      (creating ? "创建个人规则" : "编辑个人规则") + '</h3></div><button class="research-button" type="button" data-manual-new-playbook>新建</button></div>' +
      '<div class="manual-form-grid">' +
      input("name", "规则名称", val(draft, "name", record), { maxlength: 120, required: true, placeholder: "例如：均线密集启动观察规则" }) +
      select("status", "本人使用状态", selectedStatus, statusChoices, { help: "“可用”只表示本人确认规则内容完整，不代表历史验证或盈利。" }) +
      select("source_template", "来源草稿", sourceValue, templateChoices, { help: "模板来自历史提案；周期、风险和退出细节仍需本人确认。" }) +
      "</div>" + playbookFields(draft, record) +
      '<div class="manual-form-footer"><span>' + (record ? "当前版本 v" + esc(record.revision ?? "?") : "尚未保存") +
      '</span><button type="submit" class="research-button primary"' + (state.savingKey === key ? " disabled" : "") + ">" +
      (state.savingKey === key ? "正在保存…" : "保存规则版本") + "</button></div></form>";
  }

  function renderPlaybooks() {
    const records = arr(state.data?.playbooks);
    const templates = arr(state.data?.templates);
    return '<section class="manual-pane"><div class="manual-heading"><div><h2>我的规则</h2><p>规则是个人计划依据；完整状态不代表经过历史验证。</p></div>' +
      '<button type="button" class="research-button primary" data-manual-new-playbook>＋ 新建规则</button></div>' +
      '<div class="manual-two-column"><aside class="manual-list-panel"><h3>已保存规则</h3>' +
      (records.length ? '<div class="manual-record-list">' + records.map(playbookRow).join("") + "</div>" :
        '<p class="manual-empty">尚无个人规则。可以从空白草稿或下方模板开始。</p>') +
      (templates.length ? '<h3 class="manual-subheading">历史来源模板</h3><div class="manual-template-list">' +
        templates.map(templateCard).join("") + "</div>" : "") + "</aside>" +
      '<div class="manual-editor-panel">' + playbookEditor() + "</div></div>" +
      '<p class="manual-callout">“本人确认可用”只代表规则字段已由本人补齐。模板保留原始来源和历史边界，不带入未经确认的周期或风险数值。</p></section>';
  }

  function sessionRow(item) {
    const selected = String(item.id) === String(state.selectedSessionId);
    return '<button type="button" class="manual-record' + (selected ? " selected" : "") +
      '" data-manual-select-session="' + esc(item.id) + '"><span class="manual-record-main"><strong>' +
      esc(item.date || "日期未记录") + " · " + esc(item.title || "未命名计划") +
      '</strong><small>v' + esc(item.revision ?? "?") + " · " + esc(item.updated_at || "未记录更新时间") +
      "</small></span><span class=\"manual-arrow\">›</span></button>";
  }

  function sessionEditor() {
    const records = arr(state.data?.sessions);
    const record = records.find((item) => String(item.id) === String(state.selectedSessionId));
    const creating = state.sessionCreate || !record;
    const key = creating ? "session-new" : "session-" + record.id;
    const draft = state.drafts[key] || {};
    const date = val(draft, "date", record, "");
    const risk = val(draft, "risk_budget_usdt", record, "");
    return '<form class="manual-card manual-editor" data-manual-session-form data-manual-draft="' + key +
      '" data-record-id="' + esc(record?.id || "") + '" data-revision="' + esc(record?.revision ?? "") + '">' +
      '<div class="manual-card-heading"><div><p class="manual-kicker">盘前计划</p><h3>' +
      (creating ? "创建盘前计划" : "编辑盘前计划") + "</h3></div></div>" +
      '<div class="manual-form-grid">' +
      input("date", "计划日期", date, { type: "date", required: true }) +
      input("title", "计划标题", val(draft, "title", record), { maxlength: 120, required: true }) +
      textarea("market_context", "市场环境", val(draft, "market_context", record), { maxlength: 4000 }) +
      textarea("watchlist", "观察列表", val(draft, "watchlist", record), { maxlength: 2000, help: "按你自己的格式记录，例如一行一个合约。" }) +
      textarea("focus", "当日重点", val(draft, "focus", record), { maxlength: 4000 }) +
      textarea("avoid", "当日回避事项", val(draft, "avoid", record), { maxlength: 4000 }) +
      textarea("availability", "可盯盘时间", val(draft, "availability", record), { maxlength: 2000 }) +
      textarea("stop_rule", "停止交易规则", val(draft, "stop_rule", record), { maxlength: 4000 }) +
      numberField("risk_budget_usdt", "自定风险预算（USDT）", risk, { min: "0.00000001", help: "留空表示未填写；不自动给出建议值。" }) +
      "</div><div class=\"manual-form-footer\"><span>" + (record ? "当前版本 v" + esc(record.revision ?? "?") : "尚未保存") +
      '</span><button type="submit" class="research-button primary"' + (state.savingKey === key ? " disabled" : "") + ">" +
      (state.savingKey === key ? "正在保存…" : "保存计划") + "</button></div></form>";
  }

  function renderSessions() {
    const records = arr(state.data?.sessions);
    return '<section class="manual-pane"><div class="manual-heading"><div><h2>盘前计划</h2><p>记录当天环境、观察对象、时间安排和本人设定的停止条件。</p></div>' +
      '<button type="button" class="research-button primary" data-manual-new-session>＋ 新建计划</button></div>' +
      '<div class="manual-two-column"><aside class="manual-list-panel"><h3>已保存计划</h3>' +
      (records.length ? '<div class="manual-record-list">' + records.map(sessionRow).join("") + "</div>" :
        '<p class="manual-empty">尚无盘前计划。</p>') + "</aside><div class=\"manual-editor-panel\">" +
      sessionEditor() + "</div></div>" +
      '<p class="manual-callout">风险预算完全由本人输入；本页不会代填风险金额、止损距离或仓位。</p></section>';
  }

  function modeSummary(mode) {
    const summary = state.data?.summary?.[mode] || {};
    const label = labels.mode[mode];
    const pnl = finite(summary.net_pnl_usdt) ? fmtNum(summary.net_pnl_usdt) + " USDT" : "未填（未知）";
    const cohorts = summary.cohorts || {};
    const cohort = (key, title) => {
      const row = cohorts[key] || {};
      const amount = finite(row.net_pnl_usdt) ? fmtNum(row.net_pnl_usdt) + " USDT" : "未填（未知）";
      return '<div class="manual-cohort"><strong>' + title + "</strong><span>" + fmtCount(row.closed) +
        " 笔已平仓 · 已填盈亏 " + fmtCount(row.known_pnl) + " 笔 · 未知 " + fmtCount(row.unknown_pnl) +
        " 笔 · 已填盈亏合计 " + esc(amount) + "</span></div>";
    };
    return '<article class="manual-summary-card"><span>' + esc(label) + '</span><strong>' +
      fmtCount(summary.closed) + ' 笔已平仓</strong><div><span>明确填写净盈亏：' + fmtCount(summary.known_pnl) +
      " 笔</span><span>净盈亏合计：" + esc(pnl) + '</span><span>仍未知：' + fmtCount(summary.unknown_pnl) +
      " 笔</span></div>" + cohort("prospective", "事前留痕") +
      cohort("retrospective", "事后补录") + "</article>";
  }

  function ticketRef(item) {
    return encodeURIComponent(String(item.id)) + "@" + String(item.revision ?? "");
  }

  function ticketRow(item) {
    const selected = String(item.id) === String(state.selectedTicketId);
    const mode = labels.mode[item.mode] || item.mode || "未记录";
    const status = labels.ticketStatus[item.status] || item.status || "未记录";
    const pnl = item.status === "closed" ? item.outcome?.net_pnl_usdt : null;
    return '<button type="button" class="manual-record manual-ticket-record' + (selected ? " selected" : "") +
      '" data-manual-select-ticket="' + esc(item.id) + '"><span class="manual-record-main"><strong>' +
      esc(item.symbol || "未填写合约") + " · " + esc(labels.side[item.side] || item.side || "方向未填") +
      '</strong><small>' + esc(mode) + " · " + esc(status) + " · v" + esc(item.revision ?? "?") +
      '</small><small>' + esc(item.updated_at || "未记录更新时间") +
      (item.status === "closed" ? " · 净盈亏 " + esc(fmtNum(pnl)) + (finite(pnl) ? " USDT" : "") : "") +
      "</small></span><span class=\"manual-arrow\">›</span></button>";
  }

  function ticketSummaryHTML() {
    const summary = state.data?.summary || {};
    return '<div class="manual-summary-grid">' + modeSummary("practice") + modeSummary("real") +
      '<article class="manual-summary-card manual-summary-counts"><span>记录状态</span><strong>' +
      "观察 " + fmtCount(summary.watching) + " · 计划 " + fmtCount(summary.planned) + " · 已成交 " +
      fmtCount(summary.open) + '</strong><div><span>已平仓 ' + fmtCount(summary.closed) +
      "</span><span>已放弃 " + fmtCount(summary.skipped) + "</span><span>待复盘 " +
      fmtCount(summary.pending_review) + "</span></div></article></div>" +
      '<p class="manual-caption">练习与实盘记录分开汇总。事前留痕与事后补录分组展示。所有结果均由本人手工填写，尚未与交易所核验；未填写的盈亏继续标为未知，不按 0 计算。</p>';
  }

  function playbookRefChoices(record, draft) {
    const books = arr(state.data?.playbooks);
    const currentRefs = new Set();
    const choices = [{ value: "", label: "不关联个人规则" }];
    for (const book of books) {
      const ref = ticketRef(book);
      currentRefs.add(ref);
      choices.push({
        value: ref,
        label: (book.name || book.id) + " · v" + (book.revision ?? "?") + " · " +
          (labels.playbookStatus[book.status] || book.status || "草稿"),
      });
    }
    const selectedId = val(draft, "playbook_id", record, "");
    const selectedRevision = val(draft, "playbook_revision", record, "");
    if (selectedId) {
      const oldRef = encodeURIComponent(String(selectedId)) + "@" + String(selectedRevision ?? "");
      if (!currentRefs.has(oldRef)) choices.push({ value: oldRef, label: "已关联规则旧版本 v" + (selectedRevision || "?") });
    }
    return choices;
  }

  function currentPlaybook(ref) {
    if (!ref) return null;
    const split = String(ref).lastIndexOf("@");
    if (split < 0) return null;
    let id = "";
    try { id = decodeURIComponent(String(ref).slice(0, split)); } catch { return null; }
    const revision = Number(String(ref).slice(split + 1));
    return arr(state.data?.playbooks).find((item) => String(item.id) === id && Number(item.revision) === revision) || null;
  }

  function sessionChoices(record, draft) {
    const choices = [{ value: "", label: "不关联盘前计划" }];
    for (const item of arr(state.data?.sessions)) choices.push({
      value: item.id, label: (item.date || "日期未记录") + " · " + (item.title || item.id),
    });
    const value = val(draft, "session_id", record, "");
    if (value && !arr(state.data?.sessions).some((item) => String(item.id) === String(value))) {
      choices.push({ value, label: "已关联计划（当前列表中不存在）" });
    }
    return choices;
  }

  function ticketEditor() {
    const tickets = arr(state.data?.tickets);
    const record = tickets.find((item) => String(item.id) === String(state.selectedTicketId));
    const creating = state.ticketCreate || !record;
    if (!creating && !["watching", "planned"].includes(record.status)) return "";
    const key = creating ? state.ticketDraftKey : "ticket-" + record.id;
    const draft = state.drafts[key] || {};
    const initialRef = record?.playbook_id
      ? encodeURIComponent(String(record.playbook_id)) + "@" + String(record.playbook_revision ?? "")
      : "";
    const defaultRef = initialRef || "";
    const ref = val(draft, "playbook_ref", record, defaultRef);
    const selectedBook = currentPlaybook(ref);
    const hiddenId = val(draft, "playbook_id", record, selectedBook?.id || "");
    const hiddenRevision = val(draft, "playbook_revision", record, selectedBook?.revision ?? "");
    const statuses = [{ value: "watching", label: "观察中（可不完整）" }, { value: "planned", label: "已计划（需完整条件）" }];
    const sides = [{ value: "long", label: "做多" }, { value: "short", label: "做空" }];
    const modes = [{ value: "practice", label: "练习" }, { value: "real", label: "实盘手工记录" }];
    return '<form class="manual-card manual-editor" data-manual-ticket-form data-manual-draft="' + key +
      '" data-record-id="' + esc(record?.id || "") + '" data-revision="' + esc(record?.revision ?? "") + '">' +
      '<div class="manual-card-heading"><div><p class="manual-kicker">机会记录</p><h3>' +
      (creating ? "记录新机会" : "编辑未成交计划") + "</h3></div></div>" +
      '<div class="manual-form-grid">' +
      input("symbol", "合约", val(draft, "symbol", record), { required: true, maxlength: 40, placeholder: "BTC-USDT-SWAP" }) +
      select("side", "方向", val(draft, "side", record, "long"), sides) +
      input("timeframe", "观察周期", val(draft, "timeframe", record), { required: true, maxlength: 20, placeholder: "例如 15m" }) +
      select("playbook_ref", "个人规则版本", ref, playbookRefChoices(record, draft), { help: "选规则时会复制对应文字到下面，可逐项修改。" }) +
      '<input type="hidden" name="playbook_id" value="' + esc(hiddenId || "") + '">' +
      '<input type="hidden" name="playbook_revision" value="' + esc(hiddenRevision || "") + '">' +
      select("session_id", "盘前计划", val(draft, "session_id", record, ""), sessionChoices(record, draft)) +
      select("mode", "记录类别", val(draft, "mode", record, "practice"), modes) +
      select("status", "当前状态", val(draft, "status", record, "watching"), statuses) +
      numberField("planned_entry", "计划参考入场价", val(draft, "planned_entry", record, ""), { min: "0.00000001" }) +
      numberField("planned_stop", "计划止损价", val(draft, "planned_stop", record, ""), { min: "0.00000001" }) +
      numberField("risk_budget_usdt", "本人设定风险预算（USDT）", val(draft, "risk_budget_usdt", record, ""), { min: "0.00000001" }) +
      textarea("thesis", "交易理由", val(draft, "thesis", record), { maxlength: 4000 }) +
      textarea("trigger", "触发条件", val(draft, "trigger", record), { maxlength: 4000 }) +
      textarea("invalidation", "失效条件", val(draft, "invalidation", record), { maxlength: 4000 }) +
      textarea("exit_plan", "退出计划", val(draft, "exit_plan", record), { maxlength: 4000 }) +
      textarea("signal_ref", "信号或图表来源引用", val(draft, "signal_ref", record), {
        maxlength: 500, help: "填写信号标识或图表链接，也可从信号中心、突破 + spike 或趋势线突破加入观察。",
      }) +
      "</div><div class=\"manual-form-footer\"><span>" + (record ? "当前版本 v" + esc(record.revision ?? "?") : "观察中可保存不完整假设") +
      '</span><button type="submit" class="research-button primary"' + (state.savingKey === key ? " disabled" : "") + ">" +
      (state.savingKey === key ? "正在保存…" : record ? "保存计划修改" : "保存机会") + "</button></div></form>";
  }

  function ticketSnapshotHTML(ticket) {
    const book = ticket.playbook_snapshot;
    const session = ticket.session_snapshot;
    return '<div class="manual-snapshot-grid"><article><h4>规则快照</h4><p>' +
      esc(book ? (book.name || book.id) + " · v" + (book.revision ?? "?") : "未关联个人规则") +
      '</p><small>保存机会时固定的文字版本</small></article><article><h4>盘前计划快照</h4><p>' +
      esc(session ? (session.date || "日期未记录") + " · " + (session.title || session.id) : "未关联盘前计划") +
      "</p><small>保存机会时固定的文字版本</small></article></div>";
  }

  function fact(label, value) {
    return '<div class="manual-fact"><dt>' + esc(label) + "</dt><dd>" + esc(recordLabel(value)) + "</dd></div>";
  }

  function ticketFacts(ticket) {
    const execution = ticket.execution || {};
    const outcome = ticket.outcome || {};
    const review = ticket.review || {};
    const mode = labels.mode[ticket.mode] || ticket.mode;
    const management = arr(ticket.management);
    const latestManagement = management.length ? management[management.length - 1] : null;
    const protectionStatus = latestManagement?.protection_status || execution.protection_status;
    const protection = labels.protection[protectionStatus] || protectionStatus;
    const pnl = outcome.net_pnl_usdt;
    return '<dl class="manual-facts">' +
      fact("方向 / 类别", (labels.side[ticket.side] || ticket.side || "未记录") + " · " + mode) +
      fact("规则版本", ticket.playbook_id ? (ticket.playbook_snapshot?.name || "关联规则") + " · v" + (ticket.playbook_revision ?? "?") : "未关联") +
      fact("机会状态", labels.ticketStatus[ticket.status] || ticket.status) +
      fact("理由", ticket.thesis) + fact("触发条件", ticket.trigger) + fact("失效条件", ticket.invalidation) +
      fact("退出计划", ticket.exit_plan) + fact("自由来源引用", ticket.signal_ref) +
      fact("实际成交时间", execution.opened_at) + fact("实际平均成交价", execution.entry_price) +
      fact("原始止损", execution.initial_stop) + fact("初始止损状态", execution.initial_stop == null ? "未记录，初始风险与 R 未知" : "本人记录") +
      fact("基础币总数量", execution.quantity) + fact("初始价格风险（不含费用 / funding / 缺口）", execution.initial_risk_usdt) +
      fact("本人记录的保护确认（最新）", protection || "未确认") + fact("已补录历史成交", execution.backfilled === true ? "是" : execution.backfilled === false ? "否" : "未记录") +
      fact("平仓时间", outcome.closed_at) + fact("净盈亏（交易所实际总额）", pnl == null ? "未填（未知）" : fmtNum(pnl) + " USDT") +
      fact("净收益 / 初始价格风险 R", outcome.net_r == null ? "未填（未知）" :
        String(outcome.net_r) + "（净盈亏 ÷ 初始价格风险）") +
      fact("复盘遵守情况", labels.adherence[review.adherence] || review.adherence) +
      fact("复盘事实", review.notes) + fact("下一步改进", review.lesson) + "</dl>";
  }

  function managementHTML(items) {
    const rows = arr(items);
    if (!rows.length) return "";
    return '<section class="manual-management"><div class="manual-section-heading"><h4>持仓管理记录</h4>' +
      '<span>本人登记；没有交易所同步</span></div><ol class="manual-history">' + rows.map((item) =>
        '<li><span class="manual-history-dot"></span><div><strong>' +
        esc(labels.protection[item.protection_status] || item.protection_status || "未填写保护状态") +
        '</strong><time>' + esc(item.recorded_at || "时间未记录") + '</time><p>' +
        esc(item.notes || "未附备注") + "</p></div></li>").join("") + "</ol></section>";
  }

  function historyHTML(items) {
    if (!items.length) return '<p class="manual-empty">暂无事件历史。</p>';
    return '<ol class="manual-history">' + items.map((item) => {
      const last = item.last_event || {};
      const actionKey = item.action || last.action || "save";
      const execution = item.execution || {};
      const outcome = item.outcome || {};
      const review = item.review || {};
      const skipped = item.skipped || {};
      const management = arr(item.management);
      const latestManagement = management.length ? management[management.length - 1] : {};
      const reconciliation = outcome.reconciliation || {};
      let note = item.notes || "";
      if (actionKey === "open") note = note || execution.notes || "";
      else if (actionKey === "close") note = note || outcome.notes || "";
      else if (actionKey === "skip") note = note || skipped.notes || "";
      else if (actionKey === "review") note = note || review.notes || "";
      else if (actionKey === "reconcile") note = note || reconciliation.notes || "";
      else if (actionKey === "manage") note = note || latestManagement.notes || "";
      const action = labels.action[actionKey] || (actionKey === "save" ? "保存计划版本" : actionKey || "记录");
      const when = item.occurred_at || last.occurred_at ||
        (actionKey === "open" ? execution.opened_at : "") ||
        (actionKey === "close" ? outcome.closed_at : "") ||
        (actionKey === "skip" ? skipped.recorded_at : "") ||
        (actionKey === "review" ? review.recorded_at : "") ||
        (actionKey === "manage" ? latestManagement.recorded_at : "") ||
        (actionKey === "reconcile" ? reconciliation.recorded_at : "") ||
        last.recorded_at || item.updated_at || item.created_at || "时间未记录";
      let detail = "";
      if (actionKey === "open") {
        detail = "实际均价 " + fmtNum(execution.entry_price) + " · 原始止损 " +
          (finite(execution.initial_stop) ? fmtNum(execution.initial_stop) : "未记录") +
          " · 基础币数量 " + fmtNum(execution.quantity);
      }
      if (actionKey === "manage") {
        detail = labels.protection[latestManagement.protection_status] || latestManagement.protection_status || "";
      }
      let value = "";
      if (actionKey === "close" || actionKey === "reconcile") {
        value = outcome.net_pnl_usdt == null
          ? "净盈亏未填（未知）"
          : "净盈亏 " + fmtNum(outcome.net_pnl_usdt) + " USDT";
      }
      if (actionKey === "review" && review.lesson) detail = (detail ? detail + " · " : "") + "下一步：" + review.lesson;
      const parts = [note || "未附文字备注", detail, value].filter(Boolean).join(" · ");
      const revision = item.revision == null ? "" : "v" + item.revision + " · ";
      return '<li><span class="manual-history-dot"></span><div><strong>' + esc(revision + action) + "</strong><time>" +
        esc(when) + "</time><p>" + esc(parts) + "</p></div></li>";
    }).join("") + "</ol>";
  }

  function eventForm(ticket, action, title, options = {}) {
    const key = "event-" + ticket.id + "-" + action;
    const draft = state.drafts[key] || {};
    const revision = ticket.revision ?? "";
    let fields = "";
    if (action === "open") {
      fields = '<div class="manual-form-grid">' +
        input("occurred_at", "实际成交时间（本地时间）", val(draft, "occurred_at", null), {
          type: "datetime-local", required: true, help: "填写本人确认的实际成交时间；保存时换算为带时区 ISO 时间。",
        }) +
        numberField("entry_price", "实际平均成交价", val(draft, "entry_price", null), { required: true, min: "0.00000001" }) +
        numberField("initial_stop", "原始止损（可留空）", val(draft, "initial_stop", null), {
          min: "0.00000001", help: "不知道原始止损时留空；初始风险和净 R 会保持未知。",
        }) +
        numberField("quantity", "基础币总数量", val(draft, "quantity", null), {
          required: true, min: "0.00000001", help: "USDT 线性合约填基础币数量，不填合约张数。",
        }) +
        select("protection_status", "本人填写的保护确认", val(draft, "protection_status", null, "unconfirmed"), [
          { value: "unconfirmed", label: "未确认" }, { value: "confirmed", label: "本人确认已设置" }, { value: "none", label: "未设置" },
        ], { help: "不读取交易所，也不代表系统验证了保护单。" }) +
        textarea("notes", "成交备注", val(draft, "notes", null), { maxlength: 8000 }) + "</div>";
    } else if (action === "close") {
      fields = '<div class="manual-form-grid">' +
        input("occurred_at", "全部平仓时间（本地时间）", val(draft, "occurred_at", null), { type: "datetime-local", required: true }) +
        numberField("net_pnl_usdt", "实际净盈亏（USDT，可留空）", val(draft, "net_pnl_usdt", null), {
          help: "按交易所实际平仓总额填写，已扣手续费并包含 funding；留空表示未知，0 表示实际为零。",
        }) +
        textarea("notes", "平仓备注", val(draft, "notes", null), { maxlength: 8000 }) + "</div>";
    } else if (action === "skip") {
      fields = '<div class="manual-form-grid">' + textarea("notes", "放弃原因", val(draft, "notes", null), {
        maxlength: 8000, required: true,
      }) + "</div>";
    } else if (action === "review") {
      fields = '<div class="manual-form-grid">' +
        textarea("notes", "复盘事实", val(draft, "notes", null), { maxlength: 8000, required: true }) +
        select("adherence", "计划执行情况", val(draft, "adherence", null, "unknown"), [
          { value: "unknown", label: "未确认" }, { value: "followed", label: "遵守计划" }, { value: "deviated", label: "偏离计划" },
        ]) +
        textarea("lesson", "下一步改进", val(draft, "lesson", null), { maxlength: 4000, required: true }) + "</div>";
    } else if (action === "reconcile") {
      fields = '<div class="manual-form-grid">' +
        numberField("net_pnl_usdt", "确认后的实际净盈亏（USDT）", val(draft, "net_pnl_usdt", null, ticket.outcome?.net_pnl_usdt ?? ""), {
          help: "留空会把净盈亏恢复为未知；填写 0 表示实际为零。",
        }) +
        textarea("notes", "补记 / 更正依据", val(draft, "notes", null), { maxlength: 8000, required: true }) + "</div>" +
        '<p class="manual-caption">这里只追加更正记录，不会改写原计划、成交或平仓事件。</p>';
    } else if (action === "manage") {
      fields = '<div class="manual-form-grid">' +
        select("protection_status", "本人填写的保护确认", val(draft, "protection_status", null, "unconfirmed"), [
          { value: "unconfirmed", label: "未确认" }, { value: "confirmed", label: "本人确认已设置" }, { value: "none", label: "未设置" },
        ], { help: "只记录本人确认，不检查交易所或保护单状态。" }) +
        textarea("notes", "持仓管理记录", val(draft, "notes", null), {
          maxlength: 8000, required: true, help: "可写本人手工完成的部分止盈、移动止损或其他调整；不自动更改数量、风险或盈亏。",
        }) + "</div>";
    }
    return '<form class="manual-event-form" data-manual-event-form data-manual-action="' + action +
      '" data-manual-ticket-id="' + esc(ticket.id) + '" data-revision="' + esc(revision) +
      '" data-manual-draft="' + key + '"><h4>' + esc(title) + "</h4>" + fields +
      '<button type="submit" class="research-button' + (options.primary ? " primary" : "") +
      '"' + (state.savingKey === key ? " disabled" : "") + ">" +
      (state.savingKey === key ? "正在保存…" : esc(labels.action[action] || "保存记录")) + "</button></form>";
  }

  function ticketActions(ticket) {
    if (ticket.status === "watching" || ticket.status === "planned") {
      return '<div class="manual-action-grid">' +
        eventForm(ticket, "open", "登记本人已经成交", { primary: true }) +
        eventForm(ticket, "skip", "放弃机会并保留原因") +
        '<p class="manual-callout manual-span">部分成交或加仓无法自动拆分。请等最终整合后的实际仓位数据可用后，再登记汇总数量与平均价，并在备注说明整合方式。本页不会连接交易所。</p>' +
        "</div>";
    }
    if (ticket.status === "open") {
      return '<div class="manual-action-grid">' +
        eventForm(ticket, "manage", "记录持仓期间的人工管理") +
        eventForm(ticket, "close", "登记全部平仓", { primary: true }) +
        '<p class="manual-callout manual-span">部分止盈、加仓或移动止损需在持仓管理记录中写明；系统不会自动计算部分成交、仓位变化或盈亏。</p>' +
        "</div>";
    }
    if (ticket.status === "closed" || ticket.status === "skipped") {
      return '<div class="manual-action-grid">' +
        eventForm(ticket, "review", "复盘已结束记录") +
        (ticket.status === "closed" ? eventForm(ticket, "reconcile", "补记或更正已平仓盈亏") : "") +
        "</div>";
    }
    return "";
  }

  function ticketDetailHTML() {
    const detail = state.ticketDetail;
    if (state.detailLoading) return '<div class="manual-card"><p class="manual-empty">正在读取机会详情与事件历史…</p></div>';
    if (state.detailError) return '<div class="manual-card"><div class="research-error" role="alert">' + esc(state.detailError) +
      '</div><button type="button" class="research-button" data-manual-retry-detail>重新读取</button></div>';
    if (!detail) return '<div class="manual-card"><p class="manual-empty">选择左侧记录以查看计划、成交和复盘。</p></div>';
    const ticket = detail.ticket || detail.item || detail.record || detail;
    const history = arr(detail.history || ticket.history);
    return '<article class="manual-card manual-detail-card"><div class="manual-card-heading"><div><p class="manual-kicker">' +
      esc(labels.mode[ticket.mode] || "手工记录") + " · v" + esc(ticket.revision ?? "?") + "</p><h3>" +
      esc(ticket.symbol || "未填写合约") + " · " + esc(labels.side[ticket.side] || ticket.side || "方向未填") +
      '</h3></div><span class="research-badge">' + esc(labels.ticketStatus[ticket.status] || ticket.status || "未记录") +
      "</span></div>" + ticketSnapshotHTML(ticket) + ticketFacts(ticket) +
      '<div class="manual-section-heading"><h4>事件历史</h4><span>追加记录保留原计划和前序版本</span></div>' +
      historyHTML(history) + managementHTML(ticket.management) + ticketActions(ticket) + "</article>";
  }

  function renderTickets() {
    const records = arr(state.data?.tickets);
    return '<section class="manual-pane"><div class="manual-heading"><div><h2>机会与交易</h2><p>只记录本人确认的想法、成交和平仓；所有状态都由本人手工登记。</p></div>' +
      '<button type="button" class="research-button primary" data-manual-new-ticket>＋ 记录新机会</button></div>' +
      ticketSummaryHTML() +
      '<div class="manual-ticket-layout"><aside class="manual-list-panel"><h3>机会记录 <span class="research-badge">' +
      fmtCount(records.length) + "</span></h3>" +
      (records.length ? '<div class="manual-record-list manual-ticket-list">' + records.map(ticketRow).join("") + "</div>" :
        '<p class="manual-empty">尚无机会记录。观察状态允许先保存不完整假设。</p>') +
      '<div class="manual-related-links"><span>关联信号页面</span><a href="#signals">信号中心 ↗</a><a href="#joints">突破 + spike ↗</a><a href="#breaks">趋势线突破 ↗</a></div></aside>' +
      '<div class="manual-ticket-main">' + (state.ticketCreate || (records.find((item) => String(item.id) === String(state.selectedTicketId))?.status === "watching" ||
        records.find((item) => String(item.id) === String(state.selectedTicketId))?.status === "planned") ? ticketEditor() : "") +
      ticketDetailHTML() + "</div></div>" +
      '<p class="manual-callout">这本账没有交易所连接或自动下单功能。成交填写平均成交价、原始止损和基础币数量；初始价格风险不含费用、funding 与跳空。只有本人填写的净盈亏才进入结果汇总。</p>' +
      "</section>";
  }

  function reviewQueue() {
    const records = arr(state.data?.tickets);
    const rows = records.filter((item) => (item.status === "closed" || item.status === "skipped") && !item.review);
    return '<section class="manual-review-section"><div class="manual-section-heading"><div><h3>待复盘</h3><span>结束记录尚未填写复盘</span></div><strong>' +
      rows.length + " 条</strong></div>" + (rows.length ? '<div class="manual-review-list">' + rows.map((item) =>
        '<button class="manual-record" type="button" data-manual-review-ticket="' + esc(item.id) + '"><span class="manual-record-main"><strong>' +
        esc(item.symbol || "未填写合约") + " · " + esc(labels.ticketStatus[item.status]) + '</strong><small>' +
        esc(labels.mode[item.mode] || item.mode) + " · " + esc(item.updated_at || "未记录") + "</small></span><span class=\"manual-arrow\">打开复盘 ›</span></button>",
      ).join("") + "</div>" : '<p class="manual-empty">没有待复盘的结束记录。</p>') + "</section>";
  }

  function renderReview() {
    const records = arr(state.data?.tickets).filter((item) => ["closed", "skipped"].includes(item.status));
    return '<section class="manual-pane"><div class="manual-heading"><div><h2>复盘</h2><p>保留当时事实、计划执行情况和下一步改进。</p></div>' +
      '<button type="button" class="research-button" data-manual-refresh>刷新记录</button></div>' +
      reviewQueue() + '<div class="manual-two-column manual-review-layout"><aside class="manual-list-panel"><h3>已结束记录</h3>' +
      (records.length ? '<div class="manual-record-list">' + records.map(ticketRow).join("") + "</div>" :
        '<p class="manual-empty">尚无已平仓或已放弃记录。</p>') +
      "</aside><div>" + ticketDetailHTML() + "</div></div></section>";
  }

  function render() {
    const root = $(rootId);
    if (!root) return;
    const data = state.data;
    let body = "";
    if (!data && state.loading) body = '<p class="research-empty">正在读取个人手工记录…</p>';
    else if (!data) body = '<div class="research-error" role="alert">' + esc(state.loadError || "尚未读取个人手工记录。") +
      '</div><button type="button" class="research-button" data-manual-refresh>重新读取</button>';
    else if (state.tab === "playbooks") body = renderPlaybooks();
    else if (state.tab === "sessions") body = renderSessions();
    else if (state.tab === "tickets") body = renderTickets();
    else body = renderReview();
    root.innerHTML = '<div class="manual-workspace">' +
      '<header class="manual-hero"><div><p class="manual-kicker">PERSONAL JOURNAL</p><h2>个人手动交易台</h2>' +
      '<p>由本人记录规则、计划、成交与复盘；本页面不会下单、连接交易所或自动同步仓位。</p></div>' +
      '<button type="button" class="research-button" data-manual-refresh' + (state.loading ? " disabled" : "") + ">" +
      (state.loading ? "读取中…" : "刷新记录") + "</button></header>" +
      (state.actionError || state.actionMessage ? formMessage() : "") +
      (state.data && state.loadError ? '<div class="research-error manual-message" role="alert">' + esc(state.loadError) + "</div>" : "") +
      signalPromptHTML() +
      renderTabs() + body + sourceHTML() + "</div>";
  }

  async function refresh() {
    if (state.loading) return;
    state.loading = true;
    state.loadError = "";
    render();
    try {
      const data = await api(endpoint);
      if (!data || !Array.isArray(data.playbooks) || !Array.isArray(data.sessions) || !Array.isArray(data.tickets)) {
        throw new Error("个人手工记录接口返回格式有误。");
      }
      state.data = data;
      state.loaded = true;
      if (state.pendingSignal && !state.dirtyDrafts.size) applySignalDraft(state.pendingSignal);
      if (!state.selectedPlaybookId && data.playbooks.length) {
        state.selectedPlaybookId = String(data.playbooks[0].id);
        state.playbookCreate = false;
      }
      if (!state.selectedSessionId && data.sessions.length) {
        state.selectedSessionId = String(data.sessions[0].id);
        state.sessionCreate = false;
      }
      if (!state.selectedTicketId && state.tab === "tickets" && !state.ticketCreate && data.tickets.length) {
        state.selectedTicketId = String(data.tickets[0].id);
        state.ticketCreate = false;
      }
      const selected = data.tickets.find((item) => String(item.id) === String(state.selectedTicketId));
      if (state.tab === "tickets" && selected) await loadTicketDetail(selected.id, true);
      else render();
    } catch (error) {
      state.loadError = error?.message || "读取个人手工记录失败。";
      render();
    } finally {
      state.loading = false;
      render();
    }
  }

  async function loadTicketDetail(id, quiet = false) {
    state.selectedTicketId = String(id);
    state.ticketCreate = false;
    state.ticketDetail = null;
    state.detailError = "";
    state.detailLoading = true;
    if (!quiet) render();
    try {
      state.ticketDetail = await api(endpoint + "/tickets/" + encodeURIComponent(id));
    } catch (error) {
      state.detailError = error?.message || "读取机会详情失败。";
    } finally {
      state.detailLoading = false;
      render();
    }
  }

  function activateTab(tab) {
    if (!own(labels.tabs, tab)) return;
    state.tab = tab;
    state.actionError = "";
    state.actionMessage = "";
    render();
    if (tab === "tickets" && state.data && !state.ticketCreate) {
      const record = arr(state.data.tickets).find((item) => String(item.id) === String(state.selectedTicketId));
      if (record && !state.ticketDetail) loadTicketDetail(record.id, true);
    }
  }

  function formValues(form) {
    const values = new FormData(form);
    const output = {};
    const names = new Set();
    if (form.elements) for (const element of Array.from(form.elements)) if (element.name) names.add(element.name);
    if (form.values) Object.keys(form.values).forEach((name) => names.add(name));
    for (const name of names) output[name] = values.get(name) ?? "";
    return output;
  }

  function textValue(values, key) { return String(values[key] ?? "").trim(); }
  function nullableNumber(values, key, positive = false) {
    const raw = String(values[key] ?? "").trim();
    if (!raw) return null;
    const value = Number(raw);
    if (!Number.isFinite(value) || (positive && value <= 0)) throw new Error("请为“" + key + "”填写有效的正数。");
    return value;
  }
  function datetimeIso(value, required = false) {
    const raw = String(value || "").trim();
    if (!raw) {
      if (required) throw new Error("请填写实际发生时间。");
      return null;
    }
    const date = new Date(raw);
    if (!Number.isFinite(date.getTime())) throw new Error("发生时间格式无效。");
    return date.toISOString();
  }

  function readyPlaybookFor(ref) {
    const book = currentPlaybook(ref);
    return book && book.status === "ready" ? book : null;
  }

  function playbookPayload(form, record) {
    const values = formValues(form);
    const status = textValue(values, "status");
    const ruleFields = ["setup", "context", "entry_rule", "invalidation_rule", "exit_rule", "avoid_rule", "management_rule"];
    if (textValue(values, "name").length < 2) throw new Error("规则名称至少需要 2 个字符。");
    if (status === "ready" && ruleFields.some((field) => !textValue(values, field))) {
      throw new Error("本人确认可用前，请补齐形态、环境、入场、失效、退出、不做条件和持仓管理。");
    }
    const source = textValue(values, "source_template");
    const payload = {
      name: textValue(values, "name"),
      setup: String(values.setup || ""), context: String(values.context || ""),
      entry_rule: String(values.entry_rule || ""), invalidation_rule: String(values.invalidation_rule || ""),
      exit_rule: String(values.exit_rule || ""), avoid_rule: String(values.avoid_rule || ""),
      management_rule: String(values.management_rule || ""), status,
      source_template: ["ma-launch", "bb-reversal"].includes(source) ? source : null,
    };
    if (record) payload.expected_revision = Number(record.revision);
    return payload;
  }

  function sessionPayload(form, record) {
    const values = formValues(form);
    const date = textValue(values, "date");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error("计划日期需填写为 YYYY-MM-DD。");
    if (textValue(values, "title").length < 2) throw new Error("计划标题至少需要 2 个字符。");
    const risk = nullableNumber(values, "risk_budget_usdt", true);
    const payload = {
      date, title: textValue(values, "title"),
      market_context: String(values.market_context || ""), watchlist: String(values.watchlist || ""),
      focus: String(values.focus || ""), avoid: String(values.avoid || ""),
      availability: String(values.availability || ""), stop_rule: String(values.stop_rule || ""),
      risk_budget_usdt: risk,
    };
    if (record) payload.expected_revision = Number(record.revision);
    return payload;
  }

  function parsePlaybookRef(ref) {
    if (!ref) return { id: null, revision: null };
    const split = String(ref).lastIndexOf("@");
    if (split < 0) return { id: null, revision: null };
    try {
      return { id: decodeURIComponent(String(ref).slice(0, split)), revision: Number(String(ref).slice(split + 1)) };
    } catch { return { id: null, revision: null }; }
  }

  function ticketPayload(form, record) {
    const values = formValues(form);
    const ref = String(values.playbook_ref || "");
    const playbook = currentPlaybook(ref);
    const association = parsePlaybookRef(ref);
    const status = textValue(values, "status");
    const side = textValue(values, "side");
    const plannedEntry = nullableNumber(values, "planned_entry");
    const plannedStop = nullableNumber(values, "planned_stop");
    const budget = nullableNumber(values, "risk_budget_usdt", true);
    const payload = {
      symbol: textValue(values, "symbol").toUpperCase(),
      side, timeframe: textValue(values, "timeframe"),
      playbook_id: association.id, playbook_revision: association.revision,
      session_id: textValue(values, "session_id") || null,
      mode: textValue(values, "mode"), status,
      thesis: String(values.thesis || ""), trigger: String(values.trigger || ""),
      invalidation: String(values.invalidation || ""), exit_plan: String(values.exit_plan || ""),
      signal_ref: String(values.signal_ref || ""),
      planned_entry: plannedEntry, planned_stop: plannedStop, risk_budget_usdt: budget,
    };
    if (record) payload.expected_revision = Number(record.revision);
    if (!/^[A-Z0-9]{2,24}-USDT-SWAP$/.test(payload.symbol)) {
      throw new Error("合约需使用 OKX USDT 线性永续格式，例如 BTC-USDT-SWAP。");
    }
    if (!payload.timeframe) throw new Error("请填写观察周期。");
    if (association.id && (!Number.isInteger(association.revision) || association.revision < 1)) {
      throw new Error("请重新选择有效的个人规则版本。");
    }
    if (status === "planned") {
      if (!playbook || playbook.status !== "ready") throw new Error("正式计划需关联本人确认可用的规则当前版本。");
      if (!payload.thesis.trim() || !payload.trigger.trim() || !payload.invalidation.trim() || !payload.exit_plan.trim()) {
        throw new Error("正式计划需填写交易理由、触发条件、失效条件和退出计划。");
      }
      if (!finite(plannedEntry) || plannedEntry <= 0 || !finite(plannedStop) || plannedStop <= 0 || !finite(budget) || budget <= 0) {
        throw new Error("正式计划需填写正数计划入场价、计划止损价和本人风险预算。");
      }
      if ((side === "long" && plannedStop >= plannedEntry) || (side === "short" && plannedStop <= plannedEntry)) {
        throw new Error("计划止损需位于入场价的亏损方向。");
      }
    }
    return payload;
  }

  function eventPayload(form, ticket) {
    const values = formValues(form);
    const action = form.dataset.manualAction;
    const payload = {
      expected_revision: Number(ticket.revision), action,
      occurred_at: datetimeIso(values.occurred_at, action === "open" || action === "close"),
      entry_price: null, initial_stop: null, quantity: null, net_pnl_usdt: null,
      notes: String(values.notes || "").trim(),
      adherence: String(values.adherence || "unknown"),
      lesson: String(values.lesson || ""),
    };
    if (action === "open") {
      payload.entry_price = nullableNumber(values, "entry_price", true);
      payload.initial_stop = nullableNumber(values, "initial_stop", true);
      payload.quantity = nullableNumber(values, "quantity", true);
      payload.protection_status = String(values.protection_status || "unconfirmed");
      if (!payload.entry_price || !payload.quantity) throw new Error("登记已成交需填写实际平均成交价和基础币总数量。");
      if (payload.initial_stop !== null &&
          ((ticket.side === "long" && payload.initial_stop >= payload.entry_price) ||
           (ticket.side === "short" && payload.initial_stop <= payload.entry_price))) {
        throw new Error("原始止损需位于入场价的亏损方向；不知道时可留空。");
      }
      if (new Date(payload.occurred_at).getTime() > Date.now()) throw new Error("实际成交时间不能晚于当前时间。");
    } else if (action === "close") {
      payload.net_pnl_usdt = nullableNumber(values, "net_pnl_usdt");
      if (new Date(payload.occurred_at).getTime() > Date.now()) throw new Error("平仓时间不能晚于当前时间。");
    } else if (action === "skip" && !payload.notes) {
      throw new Error("放弃机会时请记录原因。");
    } else if (action === "review" && (!payload.notes || !String(values.lesson || "").trim())) {
      throw new Error("复盘需填写事实和下一步改进。");
    } else if (action === "reconcile") {
      payload.net_pnl_usdt = nullableNumber(values, "net_pnl_usdt");
      if (!payload.notes) throw new Error("补记或更正净盈亏时请说明依据。");
    } else if (action === "manage") {
      payload.protection_status = String(values.protection_status || "unconfirmed");
      if (!payload.notes) throw new Error("请填写持仓管理记录。");
    }
    return payload;
  }

  async function saveRecord(form, kind) {
    const key = form.dataset.manualDraft;
    rememberForm(form);
    const id = form.dataset.recordId || "";
    const record = kind === "playbook" ? arr(state.data?.playbooks).find((item) => String(item.id) === String(id))
      : kind === "session" ? arr(state.data?.sessions).find((item) => String(item.id) === String(id))
        : arr(state.data?.tickets).find((item) => String(item.id) === String(id));
    const payload = kind === "playbook" ? playbookPayload(form, record)
      : kind === "session" ? sessionPayload(form, record) : ticketPayload(form, record);
    const collection = kind === "playbook" ? "playbooks" : kind === "session" ? "sessions" : "tickets";
    const path = id ? endpoint + "/" + collection + "/" + encodeURIComponent(id) : endpoint + "/" + collection;
    const method = id ? "PUT" : "POST";
    state.savingKey = key;
    state.actionError = "";
    state.actionMessage = "";
    render();
    try {
      const result = await api(path, { method, body: JSON.stringify(payload) });
      const saved = result?.item || result?.record || result;
      delete state.drafts[key];
      state.dirtyDrafts.delete(key);
      if (kind === "playbook" && saved?.id) {
        state.selectedPlaybookId = String(saved.id);
        state.playbookCreate = false;
      } else if (kind === "session" && saved?.id) {
        state.selectedSessionId = String(saved.id);
        state.sessionCreate = false;
      } else if (kind === "ticket" && saved?.id) {
        state.selectedTicketId = String(saved.id);
        state.ticketCreate = false;
        state.ticketDetail = null;
        state.ticketDraftKey = "ticket-new";
        state.tab = "tickets";
      }
      state.savingKey = "";
      state.actionMessage = "已保存。所有成交、盈亏和风险数值均为本人手工记录。";
      await refresh();
    } catch (error) {
      state.savingKey = "";
      state.actionError = (error.status === 409 ? "版本冲突；表单内容已保留。请刷新并核对最新版本后再处理。 " : "") +
        (error?.message || "保存失败。");
      render();
    }
  }

  async function saveEvent(form) {
    const key = form.dataset.manualDraft;
    rememberForm(form);
    const id = form.dataset.manualTicketId;
    const ticket = arr(state.data?.tickets).find((item) => String(item.id) === String(id));
    if (!ticket) {
      state.actionError = "找不到这条机会记录；刷新后重新选择。";
      render();
      return;
    }
    const payload = eventPayload(form, ticket);
    state.savingKey = key;
    state.actionError = "";
    state.actionMessage = "";
    render();
    try {
      await api(endpoint + "/tickets/" + encodeURIComponent(id) + "/events", {
        method: "POST", body: JSON.stringify(payload),
      });
      delete state.drafts[key];
      state.dirtyDrafts.delete(key);
      state.ticketDetail = null;
      state.savingKey = "";
      state.actionMessage = "事件已登记。此页面不会连接交易所或同步仓位。";
      await refresh();
    } catch (error) {
      state.savingKey = "";
      state.actionError = (error.status === 409 ? "版本冲突；表单内容已保留。请刷新并核对最新版本后再处理。 " : "") +
        (error?.message || "事件保存失败。");
      render();
    }
  }

  function applyPlaybookToTicket(form) {
    rememberForm(form);
    const key = form.dataset.manualDraft;
    const draft = state.drafts[key] || Object.create(null);
    const book = currentPlaybook(draft.playbook_ref);
    draft.playbook_id = book?.id || "";
    draft.playbook_revision = book?.revision ?? "";
    if (book) {
      draft.thesis = [book.setup, book.context].filter(Boolean).join("\n\n");
      if (book.avoid_rule) draft.thesis += (draft.thesis ? "\n\n" : "") + "避免情形：" + book.avoid_rule;
      draft.trigger = book.entry_rule || "";
      draft.invalidation = book.invalidation_rule || "";
      draft.exit_plan = [book.exit_rule, book.management_rule].filter(Boolean).join("\n\n");
    }
    state.drafts[key] = draft;
    render();
  }

  function nextTicketDraftKey() {
    state.ticketDraftCounter += 1;
    return "ticket-new-" + state.ticketDraftCounter;
  }

  function applySignalDraft(signal) {
    state.pendingSignal = null;
    state.ticketCreate = true;
    state.selectedTicketId = "";
    state.ticketDetail = null;
    state.ticketDraftKey = nextTicketDraftKey();
    state.drafts[state.ticketDraftKey] = {
      symbol: signal.symbol, side: signal.side, timeframe: signal.timeframe,
      signal_ref: signal.signal_ref, status: "watching", mode: "practice",
    };
    state.dirtyDrafts.delete(state.ticketDraftKey);
    state.tab = "tickets";
    state.actionError = "";
    state.actionMessage = "已填入一个未保存的观察草稿。价格、止损、数量和盈亏均保持空白。";
    render();
  }

  function fromSignal(value = {}) {
    const signal = {
      symbol: String(value.symbol || "").trim().toUpperCase(),
      side: String(value.side || "").trim().toLowerCase(),
      timeframe: String(value.timeframe || "").trim(),
      signal_ref: String(value.signal_ref || "").trim().slice(0, 500),
    };
    if (!/^[A-Z0-9]{2,24}-USDT-SWAP$/.test(signal.symbol) ||
        !["long", "short"].includes(signal.side) || !signal.timeframe) {
      state.tab = "tickets";
      state.actionError = "无法创建观察草稿：请检查合约、方向和周期。";
      render();
      return false;
    }
    state.tab = "tickets";
    state.actionError = "";
    state.actionMessage = "";
    if (state.dirtyDrafts.size) {
      state.pendingSignal = signal;
      render();
      return true;
    }
    if (state.data) {
      applySignalDraft(signal);
      return true;
    }
    state.pendingSignal = signal;
    render();
    if (!state.loading) refresh();
    return true;
  }

  function bind() {
    const root = $(rootId);
    if (!root || state.bound) return;
    state.bound = true;
    root.addEventListener("click", (event) => {
      const target = event.target;
      const tab = target.closest?.("[data-manual-tab]");
      if (tab) { activateTab(tab.dataset.manualTab); return; }
      if (target.closest?.("[data-manual-refresh]")) { refresh(); return; }
      if (target.closest?.("[data-manual-new-playbook]")) {
        state.selectedPlaybookId = ""; state.playbookCreate = true; state.actionError = ""; render(); return;
      }
      if (target.closest?.("[data-manual-new-session]")) {
        state.selectedSessionId = ""; state.sessionCreate = true; state.actionError = ""; render(); return;
      }
      if (target.closest?.("[data-manual-new-ticket]")) {
        state.selectedTicketId = ""; state.ticketCreate = true; state.ticketDetail = null;
        state.ticketDraftKey = nextTicketDraftKey();
        state.actionError = ""; state.tab = "tickets"; render(); return;
      }
      if (target.closest?.("[data-manual-use-signal]")) {
        if (state.pendingSignal) applySignalDraft(state.pendingSignal);
        return;
      }
      if (target.closest?.("[data-manual-keep-draft]")) {
        state.pendingSignal = null;
        state.actionMessage = "当前未保存草稿已保留；新信号仍保留在来源页面。";
        render();
        return;
      }
      const selectedBook = target.closest?.("[data-manual-select-playbook]");
      if (selectedBook) {
        state.selectedPlaybookId = selectedBook.dataset.manualSelectPlaybook;
        state.playbookCreate = false; state.actionError = ""; render(); return;
      }
      const selectedSession = target.closest?.("[data-manual-select-session]");
      if (selectedSession) {
        state.selectedSessionId = selectedSession.dataset.manualSelectSession;
        state.sessionCreate = false; state.actionError = ""; render(); return;
      }
      const templateButton = target.closest?.("[data-manual-template]");
      if (templateButton) {
        const template = arr(state.data?.templates).find((item) => String(item.id) === String(templateButton.dataset.manualTemplate));
        if (template) {
          state.selectedPlaybookId = "";
          state.playbookCreate = true;
          state.drafts["playbook-new"] = {
            name: template.name || "", setup: template.setup || "", context: template.context || "",
            entry_rule: template.entry_rule || "", invalidation_rule: template.invalidation_rule || "",
            exit_rule: template.exit_rule || "", avoid_rule: template.avoid_rule || "",
            management_rule: template.management_rule || "", status: "draft",
            source_template: ["ma-launch", "bb-reversal"].includes(template.id) ? template.id : "",
          };
          state.actionError = ""; render();
        }
        return;
      }
      const selectedTicket = target.closest?.("[data-manual-select-ticket]");
      if (selectedTicket) { loadTicketDetail(selectedTicket.dataset.manualSelectTicket); return; }
      const reviewTicket = target.closest?.("[data-manual-review-ticket]");
      if (reviewTicket) {
        state.tab = "review";
        loadTicketDetail(reviewTicket.dataset.manualReviewTicket);
        return;
      }
      if (target.closest?.("[data-manual-retry-detail]")) {
        if (state.selectedTicketId) loadTicketDetail(state.selectedTicketId);
      }
    });
    root.addEventListener("input", (event) => {
      const form = event.target.closest?.("form[data-manual-draft]");
      if (form) rememberForm(form);
    });
    root.addEventListener("change", (event) => {
      const form = event.target.closest?.("form[data-manual-draft]");
      if (!form) return;
      if (event.target.name === "playbook_ref" && form.matches?.("[data-manual-ticket-form]")) {
        applyPlaybookToTicket(form);
        return;
      }
      rememberForm(form);
    });
    root.addEventListener("submit", (event) => {
      const form = event.target.closest?.("form[data-manual-playbook-form], form[data-manual-session-form], form[data-manual-ticket-form], form[data-manual-event-form]");
      if (!form) return;
      event.preventDefault();
      try {
        if (form.matches("[data-manual-playbook-form]")) saveRecord(form, "playbook");
        else if (form.matches("[data-manual-session-form]")) saveRecord(form, "session");
        else if (form.matches("[data-manual-ticket-form]")) saveRecord(form, "ticket");
        else saveEvent(form);
      } catch (error) {
        rememberForm(form);
        state.actionError = error?.message || "无法保存这条记录。";
        render();
      }
    });
  }

  async function setActive(active) {
    state.active = Boolean(active);
    bind();
    if (state.active && !state.loaded && !state.loading) return refresh();
    render();
  }

  window.SpikeManual = { setActive, refresh, fromSignal };
})();
