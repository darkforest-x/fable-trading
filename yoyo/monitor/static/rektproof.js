/* RektProof 区间: the RektProof (Arthur) range-trading handbook, his channel archive, and a planner
 * for the owner's weekly Monday-range system (Notion task 建立一周区间交易系统).
 * The handbook and messages are paid-group material and stay out of this public repo: they are built
 * on this machine into /static/rektproof/ (gitignored). Reading progress, notes, weekly plans and
 * results live in this browser's localStorage and can be exported as JSON. Nothing here trades.
 */
(() => {
  "use strict";
  const BASE = "/static/rektproof";
  const core = window.SpikeRektproofCore;
  const DATA_URL = `${BASE}/handbook.json`, MSG_URL = `${BASE}/messages.json`, PDF_URL = `${BASE}/handbook.pdf`;
  const QA = new URL(window.location.href).searchParams.get("rp-qa") === "1";
  const STORE_KEY = QA ? "spike.rektproof.qa.v3" : "spike.rektproof.v1";
  const NOTION_TASK = "https://app.notion.com/p/nick-wiki/3f98856479af8001a45ff70d37b53ff4";
  const INTERVALS = [1, 3, 7, 14, 30];
  const STATUS = { unread: "未读", reading: "在读", done: "已读", review: "待复习" };
  const COST = 0.002; // round trip, the repo standard
  let storageBlocked = false;
  const state = { active: false, loading: false, data: null, error: null, tab: "learn", filter: "all", current: null, message: "",
    store: load(), msgs: null, msgError: null, query: "", tag: "", limit: 80, form: null, storageError: "", errors: [], importing:null };
  const root = () => document.getElementById("rektproof-workspace");
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let searchTimer = null, calcTimer = null, sequence = 0;
  const uid = () => window.crypto?.randomUUID?.() || `${Date.now()}-${++sequence}-${Math.random().toString(36).slice(2)}`;

  function load() {
    try {
      return core.normalize(JSON.parse(window.localStorage.getItem(STORE_KEY) || "{}"));
    } catch { storageBlocked=true; return core.normalize({}); }
  }
  function save() {
    if(window.SpikeReading){
      const saved=window.SpikeReading.write(STORE_KEY,state.store,storageBlocked);
      state.storageError=saved?'':'保存失败，请重试或导出进度备份。';return saved;
    }
    try { if(storageBlocked)throw new Error('Unreadable prior data');window.localStorage.setItem(STORE_KEY,JSON.stringify(state.store));state.storageError='';return true; }
    catch { state.storageError='保存失败，请导出进度备份。';return false; }
  }
  function ymd(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
  const today = () => ymd(new Date());
  function addDays(n) { const d = new Date(); d.setDate(d.getDate() + n); return ymd(d); }
  function thisMonday() { const ny = new Intl.DateTimeFormat("en-CA", {timeZone:"America/New_York",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date()); const part = k => ny.find(x=>x.type===k).value; const d=new Date(`${part("year")}-${part("month")}-${part("day")}T00:00:00Z`); d.setUTCDate(d.getUTCDate()-((d.getUTCDay()+6)%7));return d.toISOString().slice(0,10); }
  const chapters = () => (state.data?.items || []).filter((x) => x.kind === "chapter");
  const progress = (id) => state.store.chapters[id] || { status: "unread", notes: "", stage: -1, next_review: "" };
  function update(id, patch) { state.store.chapters[id] = { ...progress(id), ...patch, updated: new Date().toISOString() }; save(); }
  function status(id) { const p = progress(id); return p.status === "done" && p.next_review && p.next_review <= today() ? "review" : p.status; }
  const num = core.number;
  const fmt = (v) => v == null ? "—" : Math.abs(v) >= 10000 ? v.toFixed(1) : Math.abs(v) >= 10 ? v.toFixed(2) : Math.abs(v) >= 1 ? v.toFixed(3) : v.toPrecision(4);

  function overview() {
    const list = chapters(), done = list.filter((c) => ["done", "review"].includes(status(c.id))).length;
    const due = list.filter((c) => status(c.id) === "review").length, pct = list.length ? Math.round((done / list.length) * 100) : 0;
    const plans = Object.values(state.store.plans), scored = plans.filter((p) => p.kind === "trade" && num(p.result) != null);
    const sumR = scored.reduce((s, p) => s + num(p.result), 0);
    return `<div class="ms-overview rp-overview">
      <article><span>阅读进度</span><strong>${done} / ${list.length} 章</strong><div class="rp-bar"><i style="width:${pct}%"></i></div></article>
      <article><span>今天待复习</span><strong class="${due ? "rp-due" : ""}">${due} 章</strong><small>读完一章自动排复习：1、3、7、14、30 天</small></article>
      <article><span>我的计划与逐笔记录</span><strong>${plans.length} 条 · ${scored.length ? `${sumR >= 0 ? "+" : ""}${sumR.toFixed(2)}R` : "未填交易结果"}</strong><small>逐笔 R 简单合计，不是账户收益 · ${esc(state.data.backtest?.status || "")}</small></article>
      <article><span>小册版本</span><strong>${esc(state.data.version)} · ${esc((state.data.built_at || "").slice(0, 10))}</strong><small><a href="${PDF_URL}" target="_blank" rel="noopener">下载 PDF</a> · ${esc(state.data.figures)} 张示意图 · ${(state.data.study?.threads?.reduce((n,t)=>n+t.images.length,0)||0)+(state.data.study?.channel?.messages?.reduce((n,m)=>n+m.images.length,0)||0)} 处原图引用</small></article>
    </div>`;
  }

  function tabs() {
    const items = [["learn", "学习路线"], ["read", "逐章阅读"], ["cases", "交易案例"], ["replay", "回放练习"], ["msgs", "频道原话"], ["system", "我们的系统"], ["monday", "周一区间"], ["about", "原图与资料"]];
    return `<div class="ms-controls"><div class="segmented" role="group" aria-label="RektProof 视图">${items.map(([v, label]) =>
      `<button type="button" data-rp-tab="${v}" class="${v === state.tab ? "selected" : ""}" aria-pressed="${v === state.tab}">${label}</button>`).join("")}</div>
      <span class="quiet-text" role="status" aria-live="polite">${esc(state.message)}</span></div>`;
  }

  /* ---------- 逐章阅读 ---------- */
  function navHTML() {
    const show = (id) => state.filter === "all" || status(id) === state.filter;
    let html = "", part = "", count = 0;
    for (const item of state.data.items) {
      if (item.kind === "part") { part = `<div class="rp-nav-part">${esc(item.title)}</div>`; continue; }
      if (!show(item.id)) continue;
      html += part; part = ""; count++;
      const st = status(item.id), on = item.id === state.current;
      html += `<button type="button" class="rp-nav-item ${on ? "active" : ""}" data-rp-chapter="${esc(item.id)}"${on ? ' aria-current="true"' : ""}>
      <i class="rp-dot rp-${st}" aria-hidden="true"></i><span class="sr-only">${STATUS[st]} </span><span>${esc(item.title)}</span></button>`;
    }
    const filters = [["all", "全部"], ["review", "待复习"], ["unread", "未读"], ["reading", "在读"]];
    return `<nav class="rp-nav" aria-label="小册章节"><div class="rp-nav-filter">${filters.map(([v, l]) =>
      `<button type="button" data-rp-filter="${v}" class="${v === state.filter ? "selected" : ""}">${l}</button>`).join("")}</div>
      ${count ? html : `<p class="quiet-text">没有符合筛选的章节。</p>`}</nav>`;
  }

  function readerHTML() {
    const list = chapters(), idx = list.findIndex((c) => c.id === state.current), ch = list[idx];
    if (!ch) return `<div class="empty-state"><h3>选择左侧章节开始</h3></div>`;
    const p = progress(ch.id), st = status(ch.id), prev = list[idx - 1], next = list[idx + 1];
    const review = p.next_review ? `下次复习 ${esc(p.next_review)}${p.stage >= 0 ? ` · 第 ${p.stage + 1} 轮` : ""}` : "读完后排第一次复习";
    const actions = st === "review" ? `<button type="button" class="rp-btn primary" data-rp-act="reviewed">复习完成 · 排下一轮</button>`
      : st === "done" ? `<button type="button" class="rp-btn" data-rp-act="reviewed">提前复习一轮</button>`
        : `<button type="button" class="rp-btn primary" data-rp-act="done">读完 · 排复习</button>`;
    return `<article class="rp-reader">
      <header class="rp-reader-head"><div><span class="rp-chip rp-${st}">${STATUS[st]}</span><h2>${esc(ch.title)}</h2><small>${review}</small></div>
        <div class="rp-actions">${actions}${st !== "unread" ? `<button type="button" class="rp-btn ghost" data-rp-act="reset">设为未读</button>` : ""}</div></header>
      <div class="rpb">${ch.html}</div>
      <section class="rp-notes"><label for="rp-note">我的笔记 · 本章</label>
        <textarea id="rp-note" rows="5" maxlength="20000" placeholder="复述要点、疑问、和自己交易的对照……（自动保存在本机浏览器）">${esc(p.notes)}</textarea><small id="rp-note-status" role="status" aria-live="polite"></small></section>
      <footer class="rp-pager">
        ${prev ? `<button type="button" class="rp-btn" data-rp-chapter="${esc(prev.id)}">← ${esc(prev.title)}</button>` : "<span></span>"}
        ${next ? `<button type="button" class="rp-btn" data-rp-chapter="${esc(next.id)}">${esc(next.title)} →</button>` : "<span></span>"}
      </footer></article>`;
  }

  /* ---------- 频道原话 ---------- */
  const mark = core.highlight;

  function msgsHTML() {
    if (state.msgError) return `<div class="empty-state"><h3>本机还没有频道存档</h3><p class="quiet-text">${esc(state.msgError)}</p></div>`;
    const m = state.msgs;
    if (!m) return `<p class="research-empty">正在读取频道消息…</p>`;
    const q = state.query.trim().toLowerCase();
    const hit = m.messages.filter((x) => (!state.tag || x.tags.includes(state.tag)) && (!q || x.en.toLowerCase().includes(q) || (x.zh||"").toLowerCase().includes(q) || (x.corrected_zh||"").toLowerCase().includes(q))).reverse();
    const counts = Object.fromEntries(m.tags.map((t) => [t, m.messages.filter((x) => x.tags.includes(t)).length]));
    const rows = hit.slice(0, state.limit).map((x) => `<li><div class="rp-msg-head"><time>${esc((x.published_at||x.t).replace("T", " "))} · ${x.published_at?"UTC · 已核对频道":"存档时间（原时区未记录）"}</time>
        ${x.attachment_verified?`<span class="rp-img">已核对 · 独立原附件 ${x.images.length} 张${x.img!==x.images.length?"（旧计数可能含引用预览）":""}</span>`:x.img?`<span class="rp-img">旧存档附件计数 ${x.img} · 原文件待核对</span>`:""}${x.who ? `<span class="ms-badge ms-badge-warn">${esc(x.who)}</span>` : ""}
        ${x.tags.map((t) => `<i class="rp-tag">${esc(t)}</i>`).join("")}</div>
        <p class="rp-en">${mark(x.en, state.query.trim())}</p>${x.corrected_zh ? `<p class="rp-zh"><b>术语校订：</b>${mark(x.corrected_zh,state.query.trim())}</p>` : ""}${x.zh ? `<details class="rp-translation"><summary>群内机器原译（可能误译术语）</summary><p class="rp-zh">${mark(x.zh, state.query.trim())}</p></details>` : ""}${x.images?.length?`<div class="rp-gallery">${x.images.map((im,i)=>window.SpikeRektproofStudy.image(im.path,`频道原附件 ${i+1}`,{data:state.data.study,esc,base:BASE})).join("")}</div>`:""}${x.source?`<p><a href="${esc(x.source)}" target="_blank" rel="noopener noreferrer">该条频道消息 ↗</a></p>`:""}<small class="quiet-text">存档 ID：${esc(x.id)}</small></li>`).join("");
    return `<section class="ms-section"><div class="section-toolbar"><h2>${esc(m.channel)}</h2>
        <span class="quiet-text">${esc(m.range[0]?.slice(0, 10))} 至 ${esc(m.range[1]?.slice(0, 10))} · ${m.messages.length} 条 · 中文为群内机器翻译</span></div>
      <input id="rp-search" type="search" placeholder="搜英文或中文，例如 monday、npoc、3 tap、止损……" value="${esc(state.query)}" aria-label="搜索频道消息">
      <div class="rp-chips"><button type="button" data-rp-tag="" class="${state.tag ? "" : "selected"}">全部</button>${m.tags.map((t) =>
        `<button type="button" data-rp-tag="${esc(t)}" class="${t === state.tag ? "selected" : ""}">${esc(t)}<small>${counts[t]}</small></button>`).join("")}</div>
      <p class="quiet-text">命中 ${hit.length} 条，新的在前。原始抓取 ${m.raw_count || 373} 条，补录消息 ${m.recovered_additions||0} 条，当前展示 ${m.messages.length} 条。现有存档缺少 2026 年 7–9 月，首末日期不代表连续覆盖；无法确认作者的条目保留未知。</p>
      <ul class="rp-msgs">${rows || `<li class="quiet-text">没有命中。</li>`}</ul>
      ${hit.length > state.limit ? `<div class="rp-more"><button type="button" class="rp-btn" data-rp-act="more">再显示 80 条</button></div>` : ""}</section>`;
  }

  async function loadMsgs() {
    if (state.msgs || state.msgError) return;
    try {
      const r = await fetch(MSG_URL, { cache: "no-cache" });
      if (!r.ok) throw new Error(`读取 ${MSG_URL} 失败：HTTP ${r.status}`);
      state.msgs = await r.json();
    } catch (error) { state.msgError = error.message; }
    render();
  }

  /* ---------- 周一区间 ---------- */
  function blankForm() { return { id:"", kind:"plan", monday: thisMonday(), coin: "BTC", high: "", low: "", side: "long", entry: "", stop: "", notes: "", checks: {}, result: "" }; }

  function levels(h, l) {
    const r = h - l, out = [];
    for (const k of [1.618, 1.27, 1.13]) out.push({ label: `${k} ↑ 扩展`, price: l + k * r, cls: "rp-ext", key: k === 1.13 });
    out.push({ label: "周一高", price: h, cls: "rp-edge" });
    out.push({ label: "0.705（从低点量）", price: l + 0.705 * r }, { label: "0.618（从低点量）", price: l + 0.618 * r, key: true },
      { label: "0.5 中点", price: l + 0.5 * r }, { label: "0.618（从高点量）", price: h - 0.618 * r, key: true }, { label: "0.705（从高点量）", price: h - 0.705 * r });
    out.push({ label: "周一低", price: l, cls: "rp-edge" });
    for (const k of [1.13, 1.27, 1.618]) out.push({ label: `${k} ↓ 扩展`, price: h - k * r, cls: "rp-ext", key: k === 1.13 });
    return out;
  }

  const calc = core.calculate;

  function mondayHTML() {
    const f = state.form || (state.form = state.store.draft || blankForm()), c = calc(f), list = state.data.checklist || [];
    const lv = c ? levels(num(f.high), num(f.low)) : [];
    const calcRow = c ? `<div class="rp-calc"><span>区间宽度 <b>${(c.range_pct * 100).toFixed(2)}%</b></span>
        <span>${f.side === "long" ? "下方" : "上方"} 1.13 扩展 <b>${fmt(c.ext113)}</b></span>
        ${c.risk_pct != null ? `<span>止损距离 <b>${(c.risk_pct * 100).toFixed(2)}%</b></span>
        <span>成本占 <b>${c.cost_r.toFixed(2)}R</b></span>
        <span>到近端 0.618 <b>${c.rNear.toFixed(2)}R</b></span><span>到远端 0.618 <b>${c.rFar.toFixed(2)}R</b></span><span>到对侧 1.13 <b>${c.r2.toFixed(2)}R</b></span>`
        : `<span>填入场价和止损价，算成本占多少 R 和各止盈的盈亏比</span>`}</div>` : "";
    const levelTable = lv.length ? `<div class="rp-table-wrap"><table class="rp-levels"><thead><tr><th>斐波那契（拉在周一区间上）</th><th>价格</th></tr></thead><tbody>
      ${lv.map((x) => `<tr class="${x.cls || ""}${x.key ? " rp-key" : ""}"><td>${esc(x.label)}</td><td>${fmt(x.price)}</td></tr>`).join("")}</tbody></table></div>` : "";
    const checks = `<ul class="rp-check">${list.map((it) => `<li><input type="checkbox" id="rp-c-${esc(it.id)}" data-rp-check="${esc(it.id)}"${f.checks?.[it.id] ? " checked" : ""}>
      <label for="rp-c-${esc(it.id)}"><em>${esc(it.stage)}</em> ${esc(it.text)}</label></li>`).join("")}</ul>`;
    const saved = Object.entries(state.store.plans).sort((a, b) => String(b[1].saved||b[1].monday).localeCompare(String(a[1].saved||a[1].monday)));
    const rows = saved.map(([k, p]) => { const v = p.kind === "trade" ? num(p.result) : null; return `<tr><td>${esc(p.monday)}</td><td>${esc(p.coin)}<small class="rp-record-kind">${({plan:"计划",trade:"逐笔交易",no_trade:"未交易"})[p.kind] || "旧记录"}</small></td><td>${p.side === "long" ? "多" : "空"}</td>
      <td>${esc(p.low)} – ${esc(p.high)}</td><td>${Object.values(p.checks || {}).filter(Boolean).length} / ${list.length}</td>
      <td class="${v == null ? "" : v >= 0 ? "rp-pos" : "rp-neg"}">${v == null ? "—" : `${v >= 0 ? "+" : ""}${v}R`}</td>
      <td><button type="button" class="rp-link" data-rp-load="${esc(k)}">打开</button> · <button type="button" class="rp-link" data-rp-del="${esc(k)}">移到回收站</button></td></tr>`; }).join("");
    return `<section class="ms-section"><div class="section-toolbar"><h2>周计划与逐笔记录</h2><span class="quiet-text">每条记录独立保存；草稿自动保存在本机</span></div>
      ${state.errors.length ? `<div class="rp-errors" role="alert">${state.errors.map(e=>`<p>${esc(e)}</p>`).join("")}</div>` : ""}
      <div class="rp-plan">
        <div class="rp-plan-top">
          <label>记录类型<select data-rp-field="kind">${[["plan","周计划"],["trade","逐笔交易"],["no_trade","观察／未交易"]].map(([v,l])=>`<option value="${v}"${f.kind===v?" selected":""}>${l}</option>`).join("")}</select></label>
          <label>周一日期<input type="date" data-rp-field="monday" value="${esc(f.monday)}"></label>
          <label>币种<input data-rp-field="coin" value="${esc(f.coin)}" maxlength="20"></label>
          <label>周一高<input data-rp-field="high" inputmode="decimal" value="${esc(f.high)}" placeholder="例如 2578.44"></label>
          <label>周一低<input data-rp-field="low" inputmode="decimal" value="${esc(f.low)}" placeholder="例如 2407.50"></label>
          <label>方向<select data-rp-field="side"><option value="long"${f.side === "long" ? " selected" : ""}>做多（扫周一低点后收回）</option><option value="short"${f.side === "short" ? " selected" : ""}>做空（扫周一高点后收回）</option></select></label>
          <label>入场价<input data-rp-field="entry" inputmode="decimal" value="${esc(f.entry)}"></label>
          <label>止损价<input data-rp-field="stop" inputmode="decimal" value="${esc(f.stop)}"></label>
          <label>结果（R）<input data-rp-field="result" inputmode="decimal" value="${esc(f.result)}" placeholder="平仓后填，例如 -1 或 1.6"></label>
        </div>
        ${calcRow}${levelTable}
        <div><div class="section-toolbar"><h2>小册研究清单</h2></div><p class="quiet-text">MSB、FVG、偏离极值止损属于研究细化，尚未替代第 49 条的 5m 企稳与最近低点规则。执行前先在「我们的系统」和本笔备注写明采用的版本。</p>${checks}</div>
        <label>计划与复盘<textarea data-rp-field="notes" rows="4" maxlength="8000" placeholder="扫哪一端、深到哪档扩展、在哪里失效；结束后写下有没有按计划执行">${esc(f.notes)}</textarea></label>
        <div class="rp-actions"><button type="button" class="rp-btn primary" data-rp-act="save-plan">${f.id?"更新当前记录":"保存新记录"}</button><button type="button" class="rp-btn ghost" data-rp-act="new-plan">新建空白</button><button type="button" class="rp-btn" data-rp-act="duplicate-plan">另存为新记录</button>
          <span class="quiet-text">成本按往返 0.2% 计算。盈亏比为未扣成本的价格距离；近端／远端止盈须自行选定。清单只辅助学习，不代表系统已验证。</span></div>
      </div></section>
      <section class="ms-section"><div class="section-toolbar"><h2>我的记录（${saved.length} 条）</h2><span class="quiet-text">不做的周也记下来，避免只记好看的样本</span></div>
        <div class="rp-table-wrap"><table class="rp-table"><thead><tr><th>周一</th><th>币种</th><th>方向</th><th>周一区间</th><th>清单</th><th>结果</th><th></th></tr></thead>
        <tbody>${rows || `<tr><td colspan="7" class="quiet-text">还没有记录。</td></tr>`}</tbody></table></div>
        ${Object.entries(state.store.trash).map(([id,p])=>`<p class="quiet-text">已移除 ${esc(p.monday)} ${esc(p.coin)} <button class="rp-link" data-rp-restore="${esc(id)}">恢复记录</button></p>`).join("")}</section>`;
  }

  /* ---------- 资料与迭代 ---------- */
  function aboutHTML() {
    const d = state.data;
    const src = (d.sources || []).map((s) => `<li><span class="rp-tier rp-tier-${esc(s.tier)}">${esc(s.tier)}</span><div><strong>${s.url?`<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)} ↗</a>`:esc(s.title)}</strong><p>${esc(s.note)}</p></div></li>`).join("");
    const its = (d.iterations || []).map((it) => `<li><div><strong>${esc(it.version)} · ${esc(it.title)}</strong>
      <span class="ms-badge${it.status === "planned" ? " ms-badge-warn" : ""}">${it.status === "planned" ? "计划中" : "已完成"}</span>${it.date ? `<span class="quiet-text">${esc(it.date)}</span>` : ""}</div>
      ${(it.notes || []).map((n) => `<p>${esc(n)}</p>`).join("")}</li>`).join("");
    return `${window.SpikeRektproofStudy.gallery(studyContext())}<section class="ms-section"><div class="section-toolbar"><h2>资料可信度与覆盖缺口</h2></div><ul class="rp-sources">${src}</ul></section>
      <section class="ms-section"><div class="section-toolbar"><h2>回测</h2></div><div class="research-callout">
        <p><b>${esc(d.backtest?.experiment)}</b> · ${esc(d.backtest?.status)}</p><p>${esc(d.backtest?.primary)}</p><p>${esc(d.backtest?.fib113)}</p></div></section>
      <section class="ms-section"><div class="section-toolbar"><h2>小册版本</h2></div><ul class="ms-iterations">${its}</ul></section>
      <section class="ms-section"><div class="section-toolbar"><h2>怎么迭代</h2></div><div class="research-callout">
        <p>有新资料或要勘误时：改本机的小册源文件，按下面的命令重建，刷新本页即可，阅读进度、笔记和周记录都会保留。小册内容只在本机，不进公开仓库。</p>
        <p class="research-mono">${esc(d.rebuild)}</p>
        <p>任务：<a href="${NOTION_TASK}" target="_blank" rel="noopener noreferrer">Notion · 建立一周区间交易系统 ↗</a></p></div></section>
      <section class="ms-section"><div class="section-toolbar"><h2>我的数据</h2></div><div class="research-callout">
        <p>进度、笔记、交易记录和练习答案存在当前浏览器。备份可以下载或直接复制；导入默认合并，保留本机冲突记录。</p>
        <div class="rp-actions"><button type="button" class="rp-btn" data-rp-act="export">导出 JSON</button>
        <button class="rp-btn" data-rp-act="restore-import">恢复导入前副本</button><label class="rp-btn">导入 JSON<input type="file" accept="application/json,.json" id="rp-import" hidden></label><button class="rp-btn" data-rp-act="paste-import">粘贴备份</button></div></div></section>`;
  }

  /* ---------- render / events ---------- */
  function studyContext() { return {data:state.data?.study || {},store:state.store,esc,uid,save,render,open,base:BASE,showImage,tab:state.tab}; }
  // Native dialog gives keyboard dismissal and focus containment (MDN HTML dialog).
  function modal(title, body) {
    const dlg = document.createElement("dialog"); dlg.className = "rp-modal";
    dlg.innerHTML = `<header><h2>${esc(title)}</h2><button class="rp-btn" data-close autofocus>关闭</button></header>${body}`;
    dlg.setAttribute("aria-labelledby","rp-modal-title");dlg.querySelector("h2").id="rp-modal-title";
    const tokens=getComputedStyle(root());for(const key of ["ink","mut","grid","page","card","up","dn","acc","acc2","vio","zone","zone2","upf","dnf"])dlg.style.setProperty(`--${key}`,tokens.getPropertyValue(`--${key}`));
    document.body.appendChild(dlg); dlg.querySelector("[data-close]").addEventListener("click",()=>dlg.close());
    dlg.addEventListener("close",()=>dlg.remove()); dlg.showModal(); return dlg;
  }
  function showImage(src, title) {
    if (!/^\/static\/rektproof\/images\/[\w.-]+$/.test(src)) return;
    const dlg=modal(title,`<p>滚动查看原始分辨率；图片保留作者原始标注。</p><div class="rp-actions"><button class="rp-btn" data-zoom="out">缩小</button><button class="rp-btn" data-zoom="in">放大</button><a class="rp-btn" href="${esc(src)}" target="_blank" rel="noopener">打开图片</a></div><div class="rp-image-scroll"><img src="${esc(src)}" alt="${esc(title)}"></div>`);
    let scale=1; dlg.querySelectorAll("[data-zoom]").forEach(b=>b.addEventListener("click",()=>{scale=Math.max(.5,Math.min(4,scale+(b.dataset.zoom==="in"?.5:-.5)));dlg.querySelector("img").style.width=`${scale*100}%`;}));
  }
  function backupDialog() {
    const value=JSON.stringify({format:"spike.rektproof",exported_at:new Date().toISOString(),handbook:state.data?.version,store:state.store},null,2);
    const blob=new Blob([value],{type:"application/json"}),url=URL.createObjectURL(blob);
    const dlg=modal("备份已准备",`<p>下载后请确认文件已保存；也可全选下面的文本并复制到本机文件。</p><a class="rp-btn" download="rektproof-${today()}.json" href="${url}">下载备份文件</a><label class="rp-backup-label">完整备份 JSON<textarea id="rp-backup-text" readonly rows="12">${esc(value)}</textarea></label>`);
    dlg.addEventListener("close",()=>URL.revokeObjectURL(url));
  }
  function previewImport(raw, restore=false) {
    try {
      const incoming=core.normalize(JSON.parse(raw),true);
      const dlg=modal("检查备份后导入",`<p>备份包含 ${Object.keys(incoming.chapters).length} 章、${Object.keys(incoming.plans).length} 条记录、${Object.keys(incoming.attempts).length} 次判断。</p><label>导入方式<select id="rp-import-mode"><option value="merge">合并，保留本机冲突记录</option><option value="replace">替换，先在本机保存恢复副本</option></select></label><p>替换会改变当前内容；本机恢复副本保留在浏览器中。请先导出独立文件。</p><button class="rp-btn primary" id="rp-accept-import">确认导入</button><p role="alert" id="rp-import-error"></p>`);
      if(restore)dlg.querySelector("#rp-import-mode").value="replace";
      dlg.querySelector("#rp-accept-import").addEventListener("click",()=>{
        const before=state.store;
        try {
          window.localStorage.setItem(`${STORE_KEY}.before-import`,JSON.stringify(before));
          state.store=dlg.querySelector("#rp-import-mode").value==="replace"?incoming:core.merge(before,incoming);
          if(!save())throw new Error(state.storageError);
          state.form=null; state.current=state.store.last; state.message="已按选定方式导入；导入前的本机副本可恢复"; dlg.close();render();
        } catch(error){state.store=before;dlg.querySelector("#rp-import-error").textContent=error.message;}
      });
    } catch(error){state.message=`导入失败：${error.message}`;render();}
  }
  function render() {
    if(!state.active)return;
    const el = root();
    if (!el || !state.active) return;
    if (state.error) {
      el.innerHTML = `<div class="empty-state"><h3>本机还没有小册数据</h3><p>小册内容不进公开仓库，需要在这台电脑上生成到 <span class="research-mono">yoyo/monitor/static/rektproof/</span>。</p>
        <p class="research-mono">python3 ~/Documents/Codex/2026-10-15/rektproof/work/build.py &lt;输出目录&gt; --frontend yoyo/monitor/static/rektproof</p><p class="quiet-text">${esc(state.error)}</p></div>`;
      return;
    }
    if (!state.data) { el.innerHTML = `<p class="research-empty">正在读取小册…</p>`; return; }
    const study=window.SpikeRektproofStudy;
    const body = ["learn","cases","replay"].includes(state.tab) ? study.render(studyContext()) : state.tab === "system" ? window.SpikePlaybooks.weekly() : state.tab === "msgs" ? msgsHTML() : state.tab === "monday" ? mondayHTML() : state.tab === "about" ? aboutHTML()
      : `<div class="rp-layout">${navHTML()}${readerHTML()}</div>`;
    el.innerHTML = `${QA?'<p class="rp-qa">功能测试沙盒：不会改动个人学习记录。</p>':""}<div class="reader-workspace">${tabs()}<details class="reader-overview" data-work-disclosure="rektproof-progress"><summary>阅读进度与资料来源</summary>${overview()}</details><p id="rp-storage-status" role="alert">${esc(state.storageError)}</p><div class="reader-content">${body}</div></div>`;
    bind(el);
    window.SpikeNavigation?.sync('rektproof',{tab:state.tab, chapter:state.current, filter:state.filter});
    window.SpikeReading?.mount({root:el,view:'rektproof',key:STORE_KEY,blocked:storageBlocked,chapters:chapters(),current:state.current,open,reader:'.rp-reader',toc:'.rp-nav',overview:'.rp-overview',save,payload:()=>({format:"spike.rektproof",exported_at:new Date().toISOString(),handbook:state.data?.version,store:state.store})});
    window.SpikePlaybooks?.bind(el);
    study.bind(el,studyContext());
    el.querySelectorAll(".rpb figure img").forEach(img=>{ const b=document.createElement("button");b.type="button";b.className="rp-btn";b.textContent="放大原图";b.addEventListener("click",()=>showImage(img.getAttribute("src"),img.alt));img.before(b);img.addEventListener("click",()=>showImage(img.getAttribute("src"),img.alt)); });
    el.querySelectorAll(".rpb svg.fig").forEach((svg)=>{
      const button=document.createElement("button");button.type="button";button.className="rp-btn rp-figure-open";button.textContent="放大图示";
      button.addEventListener("click",()=>{const dlg=modal(svg.getAttribute("aria-label")||"教学示意图",`<p>左右滚动查看完整图示，可继续放大阅读标注。</p><div class="rp-actions"><button class="rp-btn" data-svg-scale="2">放大 ×2</button><button class="rp-btn" data-svg-scale="1">还原大小</button></div><div class="rp-svg-scroll rpb">${svg.outerHTML}</div>`);const figure=dlg.querySelector("svg");figure.style.width="1000px";dlg.querySelectorAll("[data-svg-scale]").forEach(b=>b.addEventListener("click",()=>figure.style.width=`${Number(b.dataset.svgScale)*1000}px`));});svg.before(button);
    });
  }

  function open(id) {
    state.current = id; state.store.last = id;
    if (progress(id).status === "unread") update(id, { status: "reading" }); else save();
    state.tab = "read"; render();
    const heading=root()?.querySelector(".rp-reader h2");if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}
    root()?.querySelector(".rp-reader")?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }

  function act(kind) {
    clearTimeout(calcTimer);
    const id = state.current, p = progress(id);
    if (kind === "done") update(id, { status: "done", stage: 0, next_review: addDays(INTERVALS[0]) });
    if (kind === "reviewed") { const stage = Math.min(p.stage + 1, INTERVALS.length - 1); update(id, { status: "done", stage, next_review: addDays(INTERVALS[Math.max(stage, 0)]), last_review: today() }); }
    if (kind === "reset") update(id, { status: "unread", stage: -1, next_review: "" });
    if (kind === "more") state.limit += 80;
    if (kind === "new-plan") { state.form = blankForm(); state.store.draft=state.form;state.errors=[];state.message="新草稿";save(); }
    if (kind === "duplicate-plan") { state.form={...state.form,id:""};state.message="已复制为新草稿，保存后生成独立记录";state.store.draft=state.form;save(); }
    if (kind === "save-plan") {
      const f = state.form;
      state.errors=core.validatePlan(f);
      if(!state.errors.length){const id=f.id||uid();state.form={...f,id,coin:f.coin.trim().toUpperCase(),saved:new Date().toISOString()};state.store.plans[id]=JSON.parse(JSON.stringify(state.form));state.store.draft=state.form;if(save())state.message=`已保存独立记录 ${f.monday} ${state.form.coin}`;}
    }
    if (kind === "export") {
      backupDialog(); return;
    }
    if(kind === "restore-import") { const raw=window.localStorage.getItem(`${STORE_KEY}.before-import`);if(raw)previewImport(raw,true);else{state.message="尚无导入前副本";render();}return; }
    if(kind === "paste-import") {const dlg=modal("粘贴备份",'<label class="rp-backup-label">备份 JSON<textarea id="rp-paste" rows="10"></textarea></label><button class="rp-btn primary" id="rp-preview">检查备份</button>');dlg.querySelector("#rp-preview").addEventListener("click",()=>{const raw=dlg.querySelector("#rp-paste").value;dlg.close();previewImport(raw);});return;}
    render();
  }

  function importFile(file) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        previewImport(String(reader.result));
      } catch (error) { state.message = `导入失败：${error.message}`; }
    };
    reader.readAsText(file);
  }

  function bind(el) {
    el.querySelectorAll("[data-rp-tab]").forEach((b) => b.addEventListener("click", () => { clearTimeout(searchTimer);clearTimeout(calcTimer);state.tab = b.dataset.rpTab; state.message = ""; render(); if (state.tab === "msgs") loadMsgs(); }));
    el.querySelectorAll("[data-rp-filter]").forEach((b) => b.addEventListener("click", () => { state.filter = b.dataset.rpFilter; render(); }));
    el.querySelectorAll("[data-rp-chapter]").forEach((b) => b.addEventListener("click", () => open(b.dataset.rpChapter)));
    el.querySelectorAll("[data-rp-act]").forEach((b) => b.addEventListener("click", () => act(b.dataset.rpAct)));
    el.querySelectorAll("[data-rp-tag]").forEach((b) => b.addEventListener("click", () => { state.tag = b.dataset.rpTag; state.limit = 80; render(); }));
    el.querySelector("#rp-search")?.addEventListener("input", (e) => {
      state.query = e.target.value; state.limit = 80; clearTimeout(searchTimer);
      searchTimer = setTimeout(() => { const pos = e.target.selectionStart; render(); const s = root()?.querySelector("#rp-search"); if (s) { s.focus(); s.setSelectionRange(pos, pos); } }, 250);
    });
    el.querySelector("#rp-note")?.addEventListener("input", (e) => {
      const id = state.current, value = e.target.value;
      update(id, { notes: value });const label=el.querySelector("#rp-note-status");if(label)label.textContent=state.storageError||"已保存到本机";
    });
    el.querySelectorAll("[data-rp-field]").forEach((inp) => inp.addEventListener(inp.tagName === "SELECT" || inp.type === "date" ? "change" : "input", () => {
      state.form[inp.dataset.rpField] = inp.value;
      state.store.draft=state.form;save();
      if (["high", "low", "side", "entry", "stop"].includes(inp.dataset.rpField)) {
        clearTimeout(calcTimer);
        calcTimer = setTimeout(() => { const name = inp.dataset.rpField, pos = inp.selectionStart; render();
          const again = root()?.querySelector(`[data-rp-field="${name}"]`); if (again) { again.focus(); if (pos != null && again.setSelectionRange) again.setSelectionRange(pos, pos); } }, 350);
      }
    }));
    el.querySelectorAll("[data-rp-check]").forEach((b) => b.addEventListener("change", () => { state.form.checks = { ...(state.form.checks || {}), [b.dataset.rpCheck]: b.checked };state.store.draft=state.form;save(); }));
    el.querySelectorAll("[data-rp-load]").forEach((b) => b.addEventListener("click", () => { state.form = JSON.parse(JSON.stringify({ ...blankForm(), ...state.store.plans[b.dataset.rpLoad] })); state.store.draft=state.form;state.errors=[];state.message = "";save();render(); }));
    el.querySelectorAll("[data-rp-del]").forEach((b) => b.addEventListener("click", () => {
      const id=b.dataset.rpDel;if(state.form?.id===id){state.form=blankForm();state.store.draft=state.form;}state.store.trash[id]=state.store.plans[id];delete state.store.plans[id];save();render();
    }));
    el.querySelectorAll("[data-rp-restore]").forEach(b=>b.addEventListener("click",()=>{const id=b.dataset.rpRestore;state.store.plans[id]=state.store.trash[id];delete state.store.trash[id];save();render();}));
    el.querySelector("#rp-import")?.addEventListener("change", (e) => { if (e.target.files?.[0]) importFile(e.target.files[0]); });
  }

  async function refresh() {
    if (!state.active || state.loading) return;
    state.loading = true;
    try {
      const response = await fetch(DATA_URL, { cache: "no-cache" });
      if (!response.ok) throw new Error(`读取 ${DATA_URL} 失败：HTTP ${response.status}`);
      state.data = await response.json(); state.error = null;
      state.msgs=null;state.msgError=null;
      const ids = chapters().map((c) => c.id);
      if (!ids.includes(state.current)) state.current = ids.includes(state.store.last) ? state.store.last : ids[0] || null;
    } catch (error) { state.error = error.message; }
    finally { state.loading = false; }
    render();
    if(state.tab==="msgs")loadMsgs();
  }

  window.SpikeNavigation?.register('rektproof',p=>{
    if(['read', 'system', 'msgs', 'monday', 'about', 'learn', 'cases', 'replay'].includes(p.tab))state.tab=p.tab;
    if(p.chapter)state.current=p.chapter;
    state.filter=p.filter?.slice(0,200)||'all';
  });
  const idle=window.SpikeRuntime?.idleRelease(()=>{if(!state.active)root()?.replaceChildren();});
  window.SpikeRektproof = { openSystem() { state.tab="system"; render(); }, refresh, setActive(value) { state.active = value;idle?.setActive(value); if (value) { if (state.data) { render(); if(state.tab==="msgs")loadMsgs(); } else refresh(); } } };
})();
