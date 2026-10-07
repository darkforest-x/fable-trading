/* Learning drafts, versioned knowledge and editable platform editions.
 * Local persistence only; exporting or recording a URL never publishes content.
 */
(() => {
  "use strict";
  const views = ["learning", "knowledge", "content"];
  const names = { learning: "学习计划", knowledge: "知识条目", content: "内容母稿" };
  const stages = {
    learning: { planned: "待学习", learning: "学习中", review: "待复习", completed: "已完成", archived: "已归档" },
    knowledge: { draft: "草稿", organized: "已整理", archived: "已归档" },
    content: { draft: "草稿", ready: "待发布", published: "已登记发布" },
  };
  const evidenceNames = { unverified: "待验证", testing: "验证中", supported: "有支持证据", refuted: "有反面证据" };
  const kinds = { concept: "概念", principle: "原则", hypothesis: "假设", lesson: "经验", method: "方法" };
  const state = { view: "", loaded: false, loading: false, hub: {}, rows: { learning: [], knowledge: [], content: [] }, error: "", message: "", drafts: {}, filters: {} };
  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const arr = (value) => Array.isArray(value) ? value : [];
  const copy = (value) => JSON.parse(JSON.stringify(value));
  const lines = (value) => String(value || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
  const safeURL = (value) => { try { const u = new URL(value); return ["https:", "http:"].includes(u.protocol) && !u.username && !u.password ? u.href : ""; } catch { return ""; } };
  const link = (value, label) => safeURL(value) ? `<a href="${esc(safeURL(value))}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>` : "";
  const topicName = (id) => arr(state.hub.topics).find((x) => x.id === id)?.name || id;
  const platformName = (id) => arr(state.hub.platforms).find((x) => x.id === id)?.name || id;
  const statusOf = (view, row) => view === "learning" ? row.stage : view === "knowledge" ? row.status : arr(row.variants).some((x) => x.status === "published") ? "published" : arr(row.variants).some((x) => x.status === "ready") ? "ready" : "draft";
  function today() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
  function due(row) { return row.next_review && row.next_review <= today() && !["completed", "archived"].includes(row.stage); }
  async function api(path, options = {}) {
    const response = await fetch(`/api/research/${path}`, { cache: "no-store", headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) }, ...options });
    const data = await response.json();
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : arr(data.detail).map((x) => `${arr(x.loc).slice(1).join(" / ")}：${x.msg}`).join("；") || `请求失败 HTTP ${response.status}`);
    return data;
  }
  function filterRows(view) {
    const f = state.filters[view] || {};
    return state.rows[view].filter((x) => (!f.topic || x.topic === f.topic) && (view === "content" ? (!f.status && !f.platform || arr(x.variants).some((v) => (!f.status || v.status === f.status) && (!f.platform || v.platform === f.platform))) : !f.status || statusOf(view, x) === f.status)
      && (!f.evidence || x.evidence_status === f.evidence) && (!f.due || due(x))
      && (!f.query || `${x.title} ${x.summary || ""} ${x.question || ""} ${x.angle || ""} ${arr(x.source_urls).join(" ")}`.toLowerCase().includes(f.query.toLowerCase())));
  }
  function options(values, selected) { return Object.entries(values).map(([value, label]) => `<option value="${esc(value)}"${value === selected ? " selected" : ""}>${esc(label)}</option>`).join(""); }
  function select(name, label, values, value) { return `<label>${label}<select name="${name}">${options(values, value)}</select></label>`; }
  function field(name, label, value, multiline = false, attrs = "") {
    return `<label>${label}${multiline ? `<textarea name="${name}" rows="${name === "body" ? 10 : 3}" ${attrs}>${esc(value)}</textarea>` : `<input name="${name}" value="${esc(value)}" ${attrs}>`}</label>`;
  }
  function knowledgeChoices(d, content = false) {
    const selected = content ? arr(d.knowledge_refs).map((x) => x.id) : arr(d.knowledge_ids);
    return `<fieldset class="knowledge-choices"><legend>${content ? "引用知识 · 保存所选版本快照" : "关联知识"}</legend>${state.rows.knowledge.length ? state.rows.knowledge.map((k) => {
      const ref = arr(d.knowledge_refs).find((x) => x.id === k.id);
      const source = content && ref ? arr(d.knowledge_snapshots).find((x) => x.id === ref.id && x.revision === ref.revision) || k : k;
      return `<label><input type="checkbox" name="${content ? "knowledge_refs" : "knowledge_ids"}" value="${esc(content ? `${k.id}@${ref?.revision || k.revision}` : k.id)}"${selected.includes(k.id) ? " checked" : ""}>${esc(source.title)} <small>v${ref?.revision || k.revision} · ${esc(evidenceNames[source.evidence_status])}${ref && ref.revision !== k.revision ? ` · 当前新版 v${k.revision}；此处保留引用版` : ""}</small></label>`;
    }).join("") : `<p>先在知识库保存条目，再在这里关联。</p>`}</fieldset>`;
  }
  function variantsHTML(d) {
    return `<div class="knowledge-section-heading"><h3>平台版本</h3><span>各版独立编辑，视频平台保存脚本</span></div><div class="knowledge-platforms">${arr(state.hub.platforms).map((p) => `<button type="button" class="research-button" data-add-platform="${esc(p.id)}"${arr(d.variants).some((x) => x.platform === p.id) ? " disabled" : ""}>+ ${esc(p.name)}</button>`).join("")}</div>
      ${arr(d.variants).map((v) => {
        const prefix = `variant-${v.platform}-`, p = arr(state.hub.platforms).find((x) => x.id === v.platform) || {};
        return `<fieldset class="knowledge-edition"><legend>${esc(p.name || v.platform)} · ${esc(p.format)}</legend><p class="research-caption">${esc(p.guidance)}</p>
          ${field(prefix + "title", "平台标题", v.title, false, 'maxlength="200"')}${field(prefix + "body", "平台正文 / 视频脚本", v.body, true, 'maxlength="60000"')}
          <div class="knowledge-form-grid">${select(prefix + "status", "发布记录", stages.content, v.status)}${field(prefix + "published_at", "实际发布时间（含时区）", v.published_at, false, 'placeholder="2026-09-24T18:00:00+08:00"')}</div>
          ${field(prefix + "published_url", "已发布作品链接", v.published_url, false, 'type="url" placeholder="https://…"')}
          <div class="knowledge-actions"><button type="button" class="research-button" data-copy-edition="${esc(v.platform)}">复制此版</button><button type="button" class="research-button" data-remove-platform="${esc(v.platform)}">移除此版草稿</button></div></fieldset>`;
      }).join("")}`;
  }
  function formHTML(view) {
    const d = state.drafts[view]; if (!d) return "";
    const topics = Object.fromEntries(arr(state.hub.topics).map((x) => [x.id, x.name]));
    let body = field("title", view === "content" ? "母稿标题" : "标题", d.title, false, 'required maxlength="200"');
    if (view !== "content") body += `<div class="knowledge-form-grid">${select("topic", "知识领域", topics, d.topic)}${select(view === "learning" ? "stage" : "status", "整理进度", stages[view], view === "learning" ? d.stage : d.status)}</div>`;
    if (view === "learning") body += field("question", "这次要回答的问题", d.question, true) + field("goal", "学会后能做什么 / 交付什么", d.goal, true) + field("source_urls", "学习资料链接（每行一个）", arr(d.source_urls).join("\n"), true)
      + field("notes", "学习笔记", d.notes, true) + field("retrieval_notes", "不看资料，用自己的话解释 / 写出疑问", d.retrieval_notes, true)
      + field("next_review", "下次复习日期", d.next_review, false, 'type="date"') + knowledgeChoices(d);
    if (view === "knowledge") body += `<div class="knowledge-form-grid">${select("kind", "条目类型", kinds, d.kind)}${select("evidence_status", "证据状态", evidenceNames, d.evidence_status)}</div>`
      + field("summary", "一句话观点", d.summary, true) + field("body", "解释、规则与例子", d.body, true)
      + field("source_urls", "来源链接（每行一个）", arr(d.source_urls).join("\n"), true) + field("evidence", "支持 / 反对证据，包括失败结果", d.evidence, true)
      + field("limitations", "适用边界与尚未确定的问题", d.limitations, true) + field("experiment_ids", "关联实验 ID（每行一个，可留空）", arr(d.experiment_ids).join("\n"), true)
      + `<p class="research-caption"><a href="#experiments">查看实验登记 ↗</a> · 整理完成不等于研究已验证；知识条目不会自动启用交易原则或策略。</p>`
      + field("notion_url", "Notion 正式知识页（归档后填写）", d.notion_url, false, 'type="url" placeholder="https://app.notion.com/p/…"');
    if (view === "content") body += `<div class="knowledge-form-grid">${field("audience", "写给谁", d.audience)}${field("angle", "本篇要解决的问题 / 切入角度", d.angle)}</div>` + knowledgeChoices(d, true)
      + field("body", "内容母稿", d.body, true, 'maxlength="60000"') + `<p class="research-caption">保存时保留引用知识的指定版本。平台版本由母稿和引用生成编辑骨架，需补写、核对后再发布。</p>` + variantsHTML(d);
    return `<section class="knowledge-editor"><div class="knowledge-section-heading"><div><h2>${d.id ? "编辑" : "新建"}${names[view]}</h2><span>${d.id ? `当前 v${d.revision} · 修改将保留旧版` : "尚未保存"}</span></div><button type="button" class="research-button" data-close-editor>收起草稿</button></div>
      <form data-knowledge-form="${view}">${body}<div class="knowledge-actions"><button class="research-button primary" type="submit"${d.saving ? " disabled" : ""}>${d.saving ? "保存中…" : "保存"}</button><button type="button" class="research-button" data-discard-draft${d.saving ? " disabled" : ""}>放弃未保存修改</button>${d.id && view === "content" ? `<a class="research-button" href="/api/research/content/${encodeURIComponent(d.id)}/export">导出已保存版本</a>` : ""}<small>草稿切页保留；保存后可跨设备读取。</small></div>
      ${d.error ? `<p class="research-error" role="alert">${esc(d.error)}</p>` : ""}</form>
      ${arr(d.history).length ? `<details class="knowledge-history"><summary>历史版本 · ${d.history.length}</summary>${d.history.map((h) => `<details><summary>v${h.revision} · ${esc(h.created_at || h.updated_at)} · ${esc(h.title)}</summary><pre>${esc(JSON.stringify(h, null, 2))}</pre></details>`).join("")}</details>` : ""}</section>`;
  }
  function cardsHTML(view) {
    const rows = filterRows(view);
    return `<p class="research-caption">${rows.length} / ${state.rows[view].length} 条</p><div class="knowledge-card-grid">${rows.map((row) => `<article class="knowledge-card"><div class="knowledge-card-tags"><span class="research-badge">${esc(view === "content" ? `${arr(row.variants).filter((v) => v.status === "published").length} / ${arr(row.variants).length} 版已登记发布` : stages[view][statusOf(view, row)])}</span>${view !== "content" ? `<span>${esc(topicName(row.topic))}</span>` : `<span>${arr(row.variants).length} 个平台版本</span>`}${view === "knowledge" ? `<span class="research-badge">${esc(evidenceNames[row.evidence_status])}</span>` : ""}</div>
      <h3>${esc(row.title)}</h3><p>${esc(row.summary || row.question || row.angle || "尚未补充说明")}</p>
      ${view === "learning" && row.next_review ? `<p class="${due(row) ? "knowledge-due" : "research-caption"}">复习：${esc(row.next_review)}${due(row) ? " · 已到复习日期" : ""}</p>` : ""}
      ${view === "content" ? `<div class="knowledge-card-tags">${arr(row.variants).map((v) => `<span>${esc(platformName(v.platform))} · ${esc(stages.content[v.status])}</span>`).join("")}</div>` : ""}
      <div class="knowledge-actions"><button type="button" class="research-button" data-edit-record="${esc(row.id)}">编辑 / 查看</button>${view === "learning" ? `<button type="button" class="research-button" data-distill="${esc(row.id)}">提炼为知识</button>` : view === "knowledge" ? `<button type="button" class="research-button" data-compose="${esc(row.id)}">用这条写内容</button>${link(row.notion_url, "正式知识页")}` : `<a class="research-button" href="/api/research/content/${encodeURIComponent(row.id)}/export">导出</a>`}<small>v${row.revision}</small></div></article>`).join("") || `<div class="knowledge-empty"><h3>${state.rows[view].length ? "没有符合筛选条件的记录" : `从第一条${names[view]}开始`}</h3><p>${state.rows[view].length ? "调整领域、状态或搜索词。" : view === "learning" ? "把正在读的文章或遇到的问题，变成一项有产出的学习计划。" : view === "knowledge" ? "用自己的话解释一个观点，并保留来源、证据和适用边界。" : "选择知识条目形成母稿，再展开为各平台的文章、短帖或视频脚本。"}</p></div>`}</div>`;
  }
  function render(view = state.view) {
    if (!views.includes(view)) return;
    const root = $(`${view}-workspace`); if (!root) return;
    const f = state.filters[view] || {}, d = state.drafts[view];
    const notion = state.hub.notion || {};
    root.innerHTML = `<div class="knowledge-hero"><div><span class="research-kicker">SPIKE · 学习与表达</span><h2>${view === "learning" ? "带着问题学习，留下自己的答案" : view === "knowledge" ? "把零散经验连接成可复用的知识" : "一份知识积累，多种表达方式"}</h2><p>${view === "learning" ? "确定问题 → 学习与复述 → 复习 → 提炼知识 → 研究或输出" : view === "knowledge" ? "按领域组织概念、原则、假设与经验；把已知、待验证和失败分清楚。" : "母稿 → 平台版本 → 人工核对 → 导出发布 → 登记作品链接"}</p></div><div class="knowledge-hub-links">${link(notion.hub, "Notion 知识中枢")}${link(view === "learning" ? notion.resources : notion.notes, view === "learning" ? "资料库" : "学习笔记")}</div></div>
      <div class="knowledge-step-links"><a href="#learning">01 学习中心</a><a href="#knowledge">02 知识库</a><a href="#experiments">03 研究验证</a><a href="#content">04 内容工作室</a></div>
      <p class="research-caption">${view === "content" ? "支持平台草稿、复制和导出；发布链接由你登记，当前没有自动发布连接。" : "这里管理本机学习与知识草稿；正式知识沿用 Notion 中枢，归档后关联页面。当前尚未自动同步。"}</p>
      ${view !== "content" ? `<div class="knowledge-topic-grid">${arr(state.hub.topics).map((t) => `<button type="button" class="knowledge-topic${f.topic === t.id ? " selected" : ""}" data-topic="${esc(t.id)}" aria-pressed="${f.topic === t.id}"><strong>${esc(t.name)}</strong><span>${esc(t.description)}</span><small>${state.rows[view].filter((x) => x.topic === t.id).length} 条</small></button>`).join("")}</div>` : ""}
      ${state.error ? `<p class="research-error" role="alert">${esc(state.error)}</p>` : ""}${state.message ? `<p class="knowledge-message" role="status">${esc(state.message)}</p>` : ""}
      <div class="research-toolbar"><div><input type="search" data-filter="query" placeholder="搜索标题、观点或来源" aria-label="搜索${names[view]}" value="${esc(f.query)}"><select data-filter="status" aria-label="筛选状态"><option value="">全部状态</option>${options(stages[view], f.status)}</select>${view === "content" ? `<select data-filter="platform" aria-label="筛选平台"><option value="">全部平台</option>${options(Object.fromEntries(arr(state.hub.platforms).map((p) => [p.id, p.name])), f.platform)}</select>` : ""}${view === "knowledge" ? `<select data-filter="evidence" aria-label="筛选证据状态"><option value="">全部证据状态</option>${options(evidenceNames, f.evidence)}</select>` : ""}${view === "learning" ? `<label class="knowledge-inline"><input type="checkbox" data-filter="due"${f.due ? " checked" : ""}>待复习</label>` : ""}<button type="button" class="research-button" data-clear-filters>清除筛选</button></div><div><button type="button" class="research-button" data-refresh-hub${state.loading ? " disabled" : ""}>${state.loading ? "读取中…" : "刷新"}</button><button type="button" class="research-button primary" data-new-record>${d ? "继续编辑草稿" : "+ 新建" + names[view]}</button></div></div>
      ${d && !d.collapsed ? formHTML(view) : ""}<div id="${view}-cards">${state.loaded ? cardsHTML(view) : `<p class="research-empty">${state.error ? "读取失败，请重试。" : "正在读取学习与知识记录…"}</p>`}</div>`;
  }
  async function refresh() {
    if (state.loading) return;
    capture(); state.loading = true; state.error = ""; render();
    try {
      const [hub, learning, knowledge, content] = await Promise.all([api("knowledge-hub"), api("learning"), api("knowledge"), api("content")]);
      if (!hub || !Array.isArray(hub.topics) || [learning, knowledge, content].some((x) => !Array.isArray(x?.items))) throw new Error("知识工作区返回格式不完整，请刷新重试。");
      state.hub = hub; state.rows = { learning: learning.items, knowledge: knowledge.items, content: content.items }; state.loaded = true;
    } catch (error) { state.error = error.message; }
    finally { state.loading = false; render(); }
  }
  function blank(view) {
    const common = { title: "", revision: 0, topic: "market" };
    if (view === "learning") return { ...common, question: "", goal: "", stage: "planned", source_urls: [], notes: "", retrieval_notes: "", next_review: "", knowledge_ids: [] };
    if (view === "knowledge") return { ...common, kind: "concept", status: "draft", evidence_status: "unverified", summary: "", body: "", source_urls: [], evidence: "", limitations: "", experiment_ids: [], notion_url: "" };
    return { title: "", revision: 0, audience: "", angle: "", body: "", knowledge_refs: [], variants: [] };
  }
  function payloadFrom(form, view, draft) {
    const fd = new FormData(form), get = (name) => String(fd.get(name) || "");
    const payload = { title: get("title"), expected_revision: draft.revision || 0 };
    if (view !== "content") Object.assign(payload, { topic: get("topic"), source_urls: lines(get("source_urls")) });
    if (view === "learning") Object.assign(payload, { question: get("question"), goal: get("goal"), stage: get("stage"), notes: get("notes"), retrieval_notes: get("retrieval_notes"), next_review: get("next_review"), knowledge_ids: fd.getAll("knowledge_ids").map(String) });
    if (view === "knowledge") for (const name of ["kind", "status", "evidence_status", "summary", "body", "evidence", "limitations", "notion_url"]) payload[name] = get(name);
    if (view === "knowledge") payload.experiment_ids = lines(get("experiment_ids"));
    if (view === "content") Object.assign(payload, { audience: get("audience"), angle: get("angle"), body: get("body"), knowledge_refs: fd.getAll("knowledge_refs").map((x) => { const [id, revision] = String(x).split("@"); return { id, revision: Number(revision) }; }),
      variants: arr(draft.variants).map((v) => Object.fromEntries([["platform", v.platform], ...["title", "body", "status", "published_url", "published_at"].map((name) => [name, get(`variant-${v.platform}-${name}`)])])) });
    return payload;
  }
  function capture(form = null, view = state.view) {
    const d = state.drafts[view]; if (!d || d.saving) return;
    form = form || $(`${view}-workspace`)?.querySelector?.("[data-knowledge-form]");
    if (form) Object.assign(d, payloadFrom(form, view, d));
  }
  function startDraft(view, seed = {}) {
    if (state.drafts[view]) { state.drafts[view].collapsed = false; state.message = `已保留正在编辑的${names[view]}，请先保存后再新建。`; }
    else { state.drafts[view] = { ...blank(view), ...seed }; state.message = ""; }
    if (state.view !== view) window.location.hash = view;
    else render();
  }
  async function edit(id) {
    const view = state.view;
    if (state.drafts[view]) { startDraft(view); return; }
    try { state.drafts[view] = await api(`${view}/${encodeURIComponent(id)}`); render(); }
    catch (error) { state.error = error.message; render(); }
  }
  async function save(form) {
    const view = form.dataset.knowledgeForm, d = state.drafts[view]; if (!d || d.saving) return;
    capture(form, view); const payload = payloadFrom(form, view, d);
    d.saving = true; d.error = ""; render(view);
    try {
      const result = await api(view + (d.id ? `/${encodeURIComponent(d.id)}` : ""), { method: d.id ? "PUT" : "POST", body: JSON.stringify(payload) });
      state.rows[view] = [result, ...state.rows[view].filter((x) => x.id !== result.id)];
      state.message = `已保存${names[view]} · v${result.revision}`;
      delete state.drafts[view];
      if (view === "knowledge" && d.learningSource) {
        const parent = d.learningSource;
        const clean = Object.fromEntries(["title", "topic", "question", "goal", "stage", "source_urls", "notes", "retrieval_notes", "next_review", "knowledge_ids"].map((key) => [key, parent[key]]));
        clean.expected_revision = parent.revision; clean.knowledge_ids = [...new Set([...arr(parent.knowledge_ids), result.id])];
        try { const updated = await api(`learning/${encodeURIComponent(parent.id)}`, { method: "PUT", body: JSON.stringify(clean) }); state.rows.learning = state.rows.learning.map((x) => x.id === updated.id ? updated : x); }
        catch (error) { state.message += `；知识已保存，学习计划关联未成功：${error.message}`; }
      }
    } catch (error) { d.error = error.message; }
    finally { d.saving = false; render(view); }
  }
  function sourceText(d) {
    return arr(d.knowledge_refs).map((ref) => {
      const k = arr(d.knowledge_snapshots).find((x) => x.id === ref.id && x.revision === ref.revision) || state.rows.knowledge.find((x) => x.id === ref.id && x.revision === ref.revision);
      if (!k) return `引用 ${ref.id} v${ref.revision}：请查看已保存的来源快照。`;
      return `${k.title}（v${ref.revision} · ${evidenceNames[k.evidence_status]}）\n${k.summary || ""}\n证据：${k.evidence || "待补充"}\n边界：${k.limitations || "待补充"}\n${arr(k.source_urls).join("\n")}`;
    }).join("\n\n");
  }
  function editionBody(platform, d) {
    const p = arr(state.hub.platforms).find((x) => x.id === platform);
    const main = d.body || "[补充自己的解释、例子与结论]";
    const structure = ["bilibili", "douyin", "youtube"].includes(platform) ? `开场问题\n${d.angle || d.title}\n\n讲解与画面提示\n${main}\n\n结尾与下一步\n[补充]` : platform === "x" ? `主帖\n${d.angle || d.title}\n\n展开与后续帖\n${main}` : `${d.angle || d.title}\n\n${main}`;
    return `${structure}\n\n来源与适用边界\n${sourceText(d) || "[补充来源与边界]"}\n\n[编辑提示：${p?.guidance || "按平台读者调整表达"}]`;
  }
  function setView(view) {
    capture(); if (state.view !== view) state.message = "";
    state.view = views.includes(view) ? view : "";
    if (!state.view) return;
    render(); if (!state.loaded) return refresh();
  }
  for (const view of views) {
    const root = $(`${view}-workspace`); if (!root) continue;
    root.addEventListener("submit", (event) => { const form = event.target.closest("[data-knowledge-form]"); if (form) { event.preventDefault(); save(form); } });
    root.addEventListener("input", (event) => {
      const f = event.target.dataset.filter;
      if (f) { state.filters[view] = { ...state.filters[view], [f]: event.target.type === "checkbox" ? event.target.checked : event.target.value }; const target = $(`${view}-cards`); if (target) target.innerHTML = cardsHTML(view); }
      else capture(event.target.closest("[data-knowledge-form]"), view);
    });
    root.addEventListener("change", (event) => { if (event.target.dataset.filter) { state.filters[view] = { ...state.filters[view], [event.target.dataset.filter]: event.target.type === "checkbox" ? event.target.checked : event.target.value }; const target = $(`${view}-cards`); if (target) target.innerHTML = cardsHTML(view); } else capture(event.target.closest("[data-knowledge-form]"), view); });
    root.addEventListener("click", async (event) => {
      const target = event.target.closest("button"); if (!target) return;
      const a = target.dataset; capture();
      if ("refreshHub" in a) return refresh();
      if ("newRecord" in a) return startDraft(view);
      if ("discardDraft" in a) { if (window.confirm("放弃当前未保存的修改？已保存的历史记录会保留。")) { delete state.drafts[view]; state.message = "已放弃未保存的修改。"; render(); } return; }
      if ("closeEditor" in a) { state.drafts[view].collapsed = true; return render(); }
      if ("clearFilters" in a) { state.filters[view] = {}; return render(); }
      if ("topic" in a) { state.filters[view] = { ...state.filters[view], topic: state.filters[view]?.topic === a.topic ? "" : a.topic }; return render(); }
      if (a.editRecord) return edit(a.editRecord);
      if (a.distill) { const row = state.rows.learning.find((x) => x.id === a.distill); return startDraft("knowledge", { title: row.title, topic: row.topic, summary: row.retrieval_notes || "", body: row.notes || "", source_urls: copy(row.source_urls), learningSource: copy(row) }); }
      if (a.compose) { const k = state.rows.knowledge.find((x) => x.id === a.compose); return startDraft("content", { title: k.title, angle: k.summary, body: k.body, knowledge_refs: [{ id: k.id, revision: k.revision }], knowledge_snapshots: [copy(k)] }); }
      const d = state.drafts.content;
      if (a.addPlatform && d) { d.variants.push({ platform: a.addPlatform, title: d.title, body: editionBody(a.addPlatform, d), status: "draft", published_url: "", published_at: "" }); return render(); }
      if (a.removePlatform && d) { d.variants = d.variants.filter((x) => x.platform !== a.removePlatform); return render(); }
      if (a.copyEdition && d) { const v = d.variants.find((x) => x.platform === a.copyEdition); try { await navigator.clipboard.writeText(`${v.title}\n\n${v.body}`); state.message = "已复制平台草稿，尚未发布。"; } catch { state.message = "当前浏览器无法复制，请手动选择正文，或保存后导出。"; } render(); }
    });
  }
  window.SpikeKnowledge = { setView, refresh };
})();
