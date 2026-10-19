/* 订单流框架: chapter-by-chapter study of the local order-flow handbook.
 * The handbook itself is course-derived and stays out of this public repo: it is built on this
 * machine into /static/orderflow/ (gitignored). Progress, notes and review dates live in this
 * browser's localStorage and can be exported as JSON. Nothing here trades or notifies.
 */
(() => {
  "use strict";
  const DATA_URL = "/static/orderflow/handbook.json";
  const PDF_URL = "/static/orderflow/handbook.pdf";
  const CASES_URL = "/static/orderflow/practice.json";
  const STORE_KEY = "spike.orderflow.v1";
  const NOTION_TRACKER = "https://app.notion.com/p/nick-wiki/3f28856479af8068b0cef31b5baec989";
  // Days until the next review after each completed pass (simple spaced repetition).
  const INTERVALS = [1, 3, 7, 14, 30];
  const STATUS = { unread: "未读", reading: "在读", done: "已读", review: "待复习" };
  const SOURCE = { course: "课程作业", editor: "编者建议" };
  let storageBlocked = false;
  const state = { active: false, loading: false, data: null, error: null, tab: "read", filter: "all", current: null, message: "", store: load(),
    cases: null, casesError: null, blind: false, revealed: {} };
  const root = () => document.getElementById("orderflow-workspace");
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let noteTimer = null;

  function load() {
    try {
      const v = JSON.parse(window.localStorage.getItem(STORE_KEY) || "{}");
      return { chapters: v.chapters && typeof v.chapters === "object" ? v.chapters : {}, practices: v.practices && typeof v.practices === "object" ? v.practices : {},
        trades: v.trades && typeof v.trades === "object" ? v.trades : {}, last: typeof v.last === "string" ? v.last : null };
    } catch { storageBlocked=true; return { chapters: {}, practices: {}, trades: {}, last: null }; }
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
  const chapters = () => (state.data?.items || []).filter((x) => x.kind === "chapter");
  const progress = (id) => state.store.chapters[id] || { status: "unread", notes: "", stage: -1, next_review: "" };
  function update(id, patch) { state.store.chapters[id] = { ...progress(id), ...patch, updated: new Date().toISOString() }; save(); }
  function status(id) {
    const p = progress(id);
    return p.status === "done" && p.next_review && p.next_review <= today() ? "review" : p.status;
  }
  const short = (title) => title.replace(/^第(\d+)章　/, "$1 · ").replace(/^附录([A-D])　/, "$1 · ");

  function overview() {
    const list = chapters(), done = list.filter((c) => ["done", "review"].includes(status(c.id))).length;
    const due = list.filter((c) => status(c.id) === "review").length;
    const ps = state.data.practices || [], pdone = ps.filter((p) => state.store.practices[p.id]).length;
    const pct = list.length ? Math.round((done / list.length) * 100) : 0;
    return `<div class="ms-overview of-overview">
      <article><span>阅读进度</span><strong>${done} / ${list.length} 章</strong><div class="of-bar"><i style="width:${pct}%"></i></div></article>
      <article><span>今天待复习</span><strong class="${due ? "of-due" : ""}">${due} 章</strong><small>${due ? "在左侧筛选「待复习」" : "读完一章后自动排复习：1、3、7、14、30 天"}</small></article>
      <article><span>练习清单</span><strong>${pdone} / ${ps.length}</strong><small>课程作业与编者建议</small></article>
      <article><span>小册版本</span><strong>${esc(state.data.version)} · ${esc((state.data.built_at || "").slice(0, 10))}</strong><small><a href="${PDF_URL}" target="_blank" rel="noopener">下载 PDF</a> · ${esc(state.data.figures)} 张示意图</small></article>
    </div>`;
  }

  function tabs() {
    const items = [["read", "逐章阅读"], ["cases", "实战分析"], ["practice", "练习清单"], ["iterate", "迭代记录"]];
    return `<div class="ms-controls"><div class="segmented" role="group" aria-label="订单流框架视图">${items.map(([v, label]) =>
      `<button type="button" data-of-tab="${v}" class="${v === state.tab ? "selected" : ""}" aria-pressed="${v === state.tab}">${label}</button>`).join("")}</div>
      ${state.message ? `<span class="quiet-text">${esc(state.message)}</span>` : ""}</div>`;
  }

  function navHTML() {
    const show = (id) => state.filter === "all" || status(id) === state.filter;
    let html = "";
    for (const item of state.data.items) {
      if (item.kind === "part") { html += `<div class="of-nav-part">${esc(item.title)}</div>`; continue; }
      if (!show(item.id)) continue;
      const st = status(item.id);
      html += `<button type="button" class="of-nav-item ${item.id === state.current ? "active" : ""}" data-of-chapter="${esc(item.id)}"${item.id === state.current ? ' aria-current="true"' : ""}>
        <i class="of-dot of-${st}" title="${STATUS[st]}"></i><span>${esc(short(item.title))}</span></button>`;
    }
    const filters = [["all", "全部"], ["review", "待复习"], ["unread", "未读"], ["reading", "在读"]];
    return `<nav class="of-nav" aria-label="小册章节">
      <div class="of-nav-filter">${filters.map(([v, l]) => `<button type="button" data-of-filter="${v}" class="${v === state.filter ? "selected" : ""}">${l}</button>`).join("")}</div>
      ${html || `<p class="quiet-text">没有符合筛选的章节。</p>`}
    </nav>`;
  }

  function readerHTML() {
    const list = chapters(), idx = list.findIndex((c) => c.id === state.current), ch = list[idx];
    if (!ch) return `<div class="empty-state"><h3>选择左侧章节开始</h3></div>`;
    const p = progress(ch.id), st = status(ch.id), prev = list[idx - 1], next = list[idx + 1];
    const review = p.next_review ? `下次复习 ${esc(p.next_review)}${p.stage >= 0 ? ` · 第 ${p.stage + 1} 轮` : ""}` : "读完后排第一次复习";
    const actions = st === "review"
      ? `<button type="button" class="of-btn primary" data-of-act="reviewed">复习完成 · 排下一轮</button>`
      : st === "done" ? `<button type="button" class="of-btn" data-of-act="reviewed">提前复习一轮</button>`
        : `<button type="button" class="of-btn primary" data-of-act="done">读完 · 排复习</button>`;
    return `<article class="of-reader">
      <header class="of-reader-head"><div><span class="of-chip of-${st}">${STATUS[st]}</span><h2>${esc(ch.title)}</h2><small>${review}</small></div>
        <div class="of-actions">${actions}${st !== "unread" ? `<button type="button" class="of-btn ghost" data-of-act="reset">设为未读</button>` : ""}</div></header>
      <div class="ofb">${ch.html}</div>
      <section class="of-notes"><label for="of-note">我的笔记 · 本章</label>
        <textarea id="of-note" rows="5" maxlength="20000" placeholder="复述要点、疑问、和自己交易的对照……（自动保存在本机浏览器）">${esc(p.notes)}</textarea></section>
      <footer class="of-pager">
        ${prev ? `<button type="button" class="of-btn" data-of-chapter="${esc(prev.id)}">← ${esc(short(prev.title))}</button>` : "<span></span>"}
        ${next ? `<button type="button" class="of-btn" data-of-chapter="${esc(next.id)}">${esc(short(next.title))} →</button>` : "<span></span>"}
      </footer>
    </article>`;
  }

  function practiceHTML() {
    const title = (id) => chapters().find((c) => c.id === id)?.title || id;
    return `<section class="ms-section"><ul class="of-practice">${(state.data.practices || []).map((p) => {
      const done = !!state.store.practices[p.id];
      return `<li class="${done ? "done" : ""}"><label><input type="checkbox" data-of-practice="${esc(p.id)}"${done ? " checked" : ""}>
        <span>${esc(p.text)}</span></label><div class="of-practice-meta"><span class="ms-badge">${esc(SOURCE[p.source] || p.source)}</span>
        <button type="button" class="of-link" data-of-goto="${esc(p.chapter)}">${esc(short(title(p.chapter)))}</button>${done ? ` · 完成于 ${esc(state.store.practices[p.id])}` : ""}</div></li>`;
    }).join("")}</ul></section>`;
  }

  function iterateHTML() {
    const rows = (state.data.iterations || []).map((it) => `<li><div><strong>${esc(it.version)} · ${esc(it.title)}</strong>
      <span class="ms-badge${it.status === "planned" ? " ms-badge-warn" : ""}">${it.status === "planned" ? "计划中" : "已完成"}</span>${it.date ? `<span class="quiet-text">${esc(it.date)}</span>` : ""}</div>
      ${(it.notes || []).map((n) => `<p>${esc(n)}</p>`).join("")}</li>`).join("");
    return `<section class="ms-section"><div class="section-toolbar"><h2>小册版本</h2></div><ul class="ms-iterations">${rows}</ul></section>
      <section class="ms-section"><div class="section-toolbar"><h2>怎么迭代</h2></div><div class="research-callout">
        <p>课程有新内容或要勘误时：改本机的小册源文件，按下面的命令重建，刷新本页即可，阅读进度和笔记会保留。小册内容只在本机，不进公开仓库。</p>
        <p class="research-mono">${esc(state.data.rebuild)}</p>
        <p>任务跟踪：<a href="${NOTION_TRACKER}" target="_blank" rel="noopener noreferrer">Notion 任务跟踪器 #45 ↗</a></p></div></section>
      <section class="ms-section"><div class="section-toolbar"><h2>我的学习记录</h2></div><div class="research-callout">
        <p>进度、笔记和复习日期存在这台电脑的浏览器里。换浏览器或清理站点数据前，先导出备份。</p>
        <div class="of-actions"><button type="button" class="of-btn" data-of-act="export">导出进度 JSON</button>
        <label class="of-btn">导入进度 JSON<input type="file" accept="application/json,.json" id="of-import" hidden></label></div></div></section>`;
  }

  const pct = (v, d = 1) => typeof v === "number" ? `${(v * 100).toFixed(d)}%` : "—";
  const side = (s) => s === "long" ? `<span class="of-side long">做多</span>` : `<span class="of-side short">做空</span>`;

  function casesHTML() {
    if (state.casesError) return `<div class="empty-state"><h3>本机还没有实战分析数据</h3><p class="research-mono">python3 ~/Documents/Codex/2026-10-10/bang/work/astro_tools/build_practice.py …</p><p class="quiet-text">${esc(state.casesError)}</p></div>`;
    const c = state.cases;
    if (!c) return `<p class="research-empty">正在读取实战分析…</p>`;
    const s = c.stats;
    const cards = `<div class="ms-overview of-overview">
      <article><span>统一尺度：TP1 先于等距反向</span><strong>${s.sym_wins} / ${s.sym_n} 笔</strong><small>15 笔里入场价全部在喊单时间附近真实成交过</small></article>
      <article><span>同条件随机入场</span><strong>${pct(s.random_mean, 0)}</strong><small>单边检验 p ≈ ${s.p_value < 0.001 ? s.p_value.toExponential(1) : s.p_value.toFixed(3)}（样本小，仅作参考）</small></article>
      <article><span>跟单视角（发帖时入场）</span><strong>${s.follow_wins} / ${s.follow_n} 笔 · ${s.follow_ev_r >= 0 ? "+" : ""}${s.follow_ev_r}R</strong><small>平均滑点 ${s.follow_slip_mean}% · 未计手续费</small></article>
      <article><span>TP1 距离（中位）</span><strong>${s.tp1_dist_median}%</strong><small>范围 ${s.tp1_dist_range?.[0]}%–${s.tp1_dist_range?.[1]}% · 发帖滞后中位 ${s.lag_median_h} 小时</small></article>
    </div>`;
    const profile = `<section class="ms-section"><div class="section-toolbar"><h2>打法画像</h2><span class="quiet-text">编者根据 640 条消息与行情核对整理</span></div>
      <dl class="of-profile">${c.profile.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl></section>`;
    const trades = c.trades.map((t) => {
      const open = !state.blind || state.revealed[t.id];
      const note = state.store.trades?.[t.id]?.note || "";
      const hours = (t.tp_hours || []).map((h, i) => `TP${i + 1} ${h == null ? "未到" : `${h}h`}`).join(" · ");
      const f = t.follow;
      return `<article class="of-trade ${open ? "revealed" : ""}" id="of-trade-${esc(t.id)}">
        <header><div class="of-trade-title"><strong>${esc(t.id)}</strong>${side(t.side)}<span>入场 ${esc((t.entry / 1000).toFixed(1))}k</span><span class="quiet-text">成交 ${esc(t.fill_bj)}（北京）</span></div>
          <span class="of-verdict of-tone-${esc(t.verdict_tone)} of-reveal">${esc(t.verdict)}</span>
          ${state.blind && !open ? `<button type="button" class="of-btn" data-of-reveal="${esc(t.id)}">揭晓结果</button>` : ""}</header>
        <p>${esc(t.summary)}</p>
        <div class="ofb of-chart">${t.svg}</div>
        <div class="of-reveal">
          <p class="of-meta"><b>作者自报：</b>${esc(t.reported)}</p>
          ${t.note ? `<p class="of-meta"><b>核对说明：</b>${esc(t.note)}</p>` : ""}
          <p class="of-meta"><b>价格核对：</b>${t.tps.length ? `${esc(hours)} · ` : ""}最大浮盈 ${t.mfe_pct}% · 最大浮亏 ${t.mae_pct}%${t.mae_before_tp1_pct != null ? `（TP1 前 ${t.mae_before_tp1_pct}%）` : ""}${t.random_rate != null ? ` · 同条件随机 ${pct(t.random_rate, 0)}` : ""}${f ? ` · 跟单：${f.entry} 入场，滑点 ${f.slip_pct}%，盈亏比 ${f.r_multiple}，${f.result === 1 ? "TP1 先到" : f.result === 0 ? "反向先到" : "7 天内未定"}` : ""}</p>
        </div>
        <div class="of-trade-foot"><span>对应章节：</span>${(t.tag_names || []).map(([id, name]) => `<button type="button" class="of-link" data-of-goto="${esc(id)}">${esc(name)}</button>`).join("")}
          <span class="of-src">${(t.links || []).map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">原帖${i + 1}</a>`).join(" ")}</span></div>
        <textarea class="of-trade-note" data-of-trade-note="${esc(t.id)}" rows="2" maxlength="4000" placeholder="我的判断 / 复盘（遮住结果时先写下你会怎么做）">${esc(note)}</textarea>
      </article>`;
    }).join("");
    const unscored = `<section class="ms-section"><div class="section-toolbar"><h2>细节不足的喊单（未计入统计）</h2></div><ul class="of-unscored">${c.unscored.map((u) =>
      `<li><span class="research-mono">${esc(u.date)}</span>${side(u.side)}<span>${esc(u.note)}</span></li>`).join("")}</ul></section>`;
    const method = `<section class="ms-section"><div class="section-toolbar"><h2>方法与局限</h2></div><div class="research-callout">
      <p>数据：<a href="${esc(c.source.mirror)}" target="_blank" rel="noopener noreferrer">${esc(c.source.mirror)}</a>（${esc(c.source.period)}，${c.source.messages} 条）· ${esc(c.source.klines)}</p>
      <ol>${c.method.map((m) => `<li>${esc(m)}</li>`).join("")}</ol><p><b>必须知道的局限</b></p><ul>${c.caveats.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div></section>`;
    return `${cards}
      <section class="ms-section"><div class="section-toolbar"><h2>6–10 月走势与喊单位置</h2></div><div class="ofb of-chart">${c.overview_svg}</div></section>
      ${profile}
      <section class="ms-section"><div class="section-toolbar"><h2>逐笔复盘（${c.trades.length} 笔）</h2>
        <button type="button" class="of-btn ${state.blind ? "primary" : ""}" data-of-act="blind">${state.blind ? "盲测中 · 点「揭晓」看结果" : "盲测练习：遮住入场后的走势"}</button></div>
        <div class="of-trades ${state.blind ? "of-blind" : ""}">${trades}</div></section>
      ${unscored}${method}`;
  }

  async function loadCases() {
    if (state.cases || state.casesError) return;
    try {
      const r = await fetch(CASES_URL, { cache: "no-cache" });
      if (!r.ok) throw new Error(`读取 ${CASES_URL} 失败：HTTP ${r.status}`);
      state.cases = await r.json();
    } catch (error) { state.casesError = error.message; }
    render();
  }

  function render() {
    if(!state.active)return;
    const el = root();
    if (!el || !state.active) return;
    if (state.error) {
      el.innerHTML = `<div class="empty-state"><h3>本机还没有小册数据</h3><p>小册内容不进公开仓库，需要在这台电脑上生成到 <span class="research-mono">yoyo/monitor/static/orderflow/</span>。</p><p class="research-mono">python3 ~/Documents/Codex/2026-10-10/bang/work/handbook/build.py &lt;输出目录&gt; --frontend yoyo/monitor/static/orderflow</p><p class="quiet-text">${esc(state.error)}</p></div>`;
      return;
    }
    if (!state.data) { el.innerHTML = `<p class="research-empty">正在读取小册…</p>`; return; }
    const body = state.tab === "practice" ? practiceHTML() : state.tab === "iterate" ? iterateHTML() : state.tab === "cases" ? casesHTML() : `<div class="of-layout">${navHTML()}${readerHTML()}</div>`;
    el.innerHTML = `<div class="reader-workspace">${tabs()}<details class="reader-overview" data-work-disclosure="orderflow-progress"><summary>阅读进度与资料来源</summary>${overview()}</details><div class="reader-content">${body}</div></div>`;
    bind(el);
    window.SpikeNavigation?.sync('orderflow',{tab:state.tab, chapter:state.current, filter:state.filter});
    window.SpikeReading?.mount({root:el,view:'orderflow',key:STORE_KEY,blocked:storageBlocked,chapters:chapters(),current:state.current,open,reader:'.of-reader',toc:'.of-nav',overview:'.of-overview',save,payload:()=>({exported_at:new Date().toISOString(),handbook:state.data?.version,store:state.store})});
  }

  function open(id, scroll = true) {
    state.current = id; state.store.last = id;
    if (progress(id).status === "unread") update(id, { status: "reading" }); else save();
    state.tab = "read"; render();
    if (scroll) root()?.querySelector(".of-reader")?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }

  function act(kind) {
    const id = state.current, p = progress(id);
    if (kind === "done") update(id, { status: "done", stage: 0, next_review: addDays(INTERVALS[0]) });
    if (kind === "reviewed") { const stage = Math.min(p.stage + 1, INTERVALS.length - 1); update(id, { status: "done", stage, next_review: addDays(INTERVALS[Math.max(stage, 0)]), last_review: today() }); }
    if (kind === "reset") update(id, { status: "unread", stage: -1, next_review: "" });
    if (kind === "blind") { state.blind = !state.blind; state.revealed = {}; }
    if (kind === "export") {
      const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(), handbook: state.data?.version, store: state.store }, null, 2)], { type: "application/json" });
      const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `orderflow-progress-${today()}.json`; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      state.message = "已导出进度";
    }
    render();
  }

  function importFile(file) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const v = JSON.parse(String(reader.result)), s = v.store || v;
        if (!s || typeof s.chapters !== "object") throw new Error("文件里没有进度数据");
        state.store = { chapters: s.chapters || {}, practices: s.practices || {}, trades: s.trades || {}, last: s.last || null };
        save(); state.message = "已导入进度";
      } catch (error) { state.message = `导入失败：${error.message}`; }
      render();
    };
    reader.readAsText(file);
  }

  function bind(el) {
    el.querySelectorAll("[data-of-tab]").forEach((b) => b.addEventListener("click", () => { state.tab = b.dataset.ofTab; state.message = ""; render(); if (state.tab === "cases") loadCases(); }));
    el.querySelectorAll("[data-of-reveal]").forEach((b) => b.addEventListener("click", () => {
      state.revealed[b.dataset.ofReveal] = true;
      const card = b.closest(".of-trade"); card?.classList.add("revealed"); b.remove();
    }));
    el.querySelectorAll("[data-of-trade-note]").forEach((ta) => ta.addEventListener("input", () => {
      const id = ta.dataset.ofTradeNote; clearTimeout(noteTimer);
      state.store.trades = state.store.trades || {}; state.store.trades[id] = { note: ta.value, updated: new Date().toISOString() }; save();
    }));
    el.querySelectorAll("[data-of-filter]").forEach((b) => b.addEventListener("click", () => { state.filter = b.dataset.ofFilter; render(); }));
    el.querySelectorAll("[data-of-chapter]").forEach((b) => b.addEventListener("click", () => open(b.dataset.ofChapter)));
    el.querySelectorAll("[data-of-goto]").forEach((b) => b.addEventListener("click", () => open(b.dataset.ofGoto)));
    el.querySelectorAll("[data-of-act]").forEach((b) => b.addEventListener("click", () => act(b.dataset.ofAct)));
    el.querySelectorAll("[data-of-practice]").forEach((b) => b.addEventListener("change", () => {
      if (b.checked) state.store.practices[b.dataset.ofPractice] = today(); else delete state.store.practices[b.dataset.ofPractice];
      save(); render();
    }));
    el.querySelector("#of-note")?.addEventListener("input", (e) => {
      const id = state.current, value = e.target.value;
      clearTimeout(noteTimer); update(id, { notes: value });
    });
    el.querySelector("#of-import")?.addEventListener("change", (e) => { if (e.target.files?.[0]) importFile(e.target.files[0]); });
  }

  async function refresh() {
    if (!state.active || state.loading) return;
    state.loading = true;
    try {
      const response = await fetch(DATA_URL, { cache: "no-cache" });
      if (!response.ok) throw new Error(`读取 ${DATA_URL} 失败：HTTP ${response.status}`);
      state.data = await response.json(); state.error = null;
      const ids = chapters().map((c) => c.id);
      if (!ids.includes(state.current)) state.current = ids.includes(state.store.last) ? state.store.last : ids[0] || null;
    } catch (error) { state.error = error.message; }
    finally { state.loading = false; }
    render();
  }

  window.SpikeNavigation?.register('orderflow',p=>{
    if(['read', 'practice', 'iterate', 'cases'].includes(p.tab))state.tab=p.tab;
    if(p.chapter)state.current=p.chapter;
    state.filter=p.filter?.slice(0,200)||'all';
  });
  const idle=window.SpikeRuntime?.idleRelease(()=>{if(!state.active)root()?.replaceChildren();});
  window.SpikeOrderflow = { refresh, setActive(value) { state.active = value;idle?.setActive(value); if (value) { if (state.data) { render(); if(state.tab==="cases")loadCases(); } else refresh(); } } };
})();
