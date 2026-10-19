/* Bit浪浪体系: the 主升浪 method as a study + pre-trade workspace.
 * Handbook, livestream index, transcripts and cases are built on this machine into /static/langlang/
 * (gitignored: they are derived from third-party videos and this repo is public). Reading progress,
 * notes, case answers and pre-trade checks live in this browser's localStorage and can be exported.
 * Nothing here trades or notifies.
 */
(() => {
  "use strict";
  const BASE = "/static/langlang/";
  const STORE_KEY = "spike.langlang.v1";
  const INTERVALS = [1, 3, 7, 14, 30];
  const STATUS = { unread: "未读", reading: "在读", done: "已读", review: "待复习" };
  const EVIDENCE = { usable: ["可直接用", "ok"], rejected: ["已测·否决", "bad"], partial: ["部分可用", "mid"], untested: ["未检验", "mid"], suggestive: ["有正面迹象·待验证", "mid"], principle: ["原则·不需回测", "ok"] };
  const TIER = { A: "一手：本人直播/问答", B: "二手：基于直播/交割单的整理", C: "转述：来源不明或与一手矛盾" };
  const TABS = [["system", "体系图"], ["leaders", "找龙头方法"], ["read", "逐章阅读"], ["sessions", "直播档案"], ["cases", "案例盲测"], ["check", "开单检查"], ["evidence", "证据与迭代"]];
  let storageBlocked = false;
  const state = { active: false, loading: false, data: null, error: null, tab: "system", filter: "all", current: null, message: "",
    sessions: null, sessionsError: null, kind: "all", tag: "all", openSession: null,
    tx: null, txError: null, txLoading: false, query: "", cases: null, casesError: null, blind: true, revealed: {},
    draft: null, store: load() };
  const root = () => document.getElementById("langlang-workspace");
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  let noteTimer = null, searchTimer = null, searchClient=null, searchPage=0, searchKey="", searchResult=null, searchPending="", searchPageFocus=false;

  function load() {
    const blank = { chapters: {}, practices: {}, cases: {}, checks: [], last: null };
    try {
      const v = JSON.parse(window.localStorage.getItem(STORE_KEY) || "{}");
      return { chapters: obj(v.chapters), practices: obj(v.practices), cases: obj(v.cases), checks: Array.isArray(v.checks) ? v.checks : [], last: typeof v.last === "string" ? v.last : null };
    } catch { storageBlocked=true; return blank; }
  }
  function obj(v) { return v && typeof v === "object" && !Array.isArray(v) ? v : {}; }
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
  const hms = (t) => { t = Math.max(0, Math.floor(t)); const h = Math.floor(t / 3600), m = Math.floor(t % 3600 / 60), s = t % 60; return `${h ? `${h}:` : ""}${String(m).padStart(h ? 2 : 1, "0")}:${String(s).padStart(2, "0")}`; };
  const ytAt = (url, t) => `${url}${url.includes("?") ? "&" : "?"}t=${Math.max(0, Math.floor(t))}s`;
  const chapters = () => (state.data?.items || []).filter((x) => x.kind === "chapter");
  const progress = (id) => state.store.chapters[id] || { status: "unread", notes: "", stage: -1, next_review: "" };
  function update(id, patch) { state.store.chapters[id] = { ...progress(id), ...patch, updated: new Date().toISOString() }; save(); }
  function status(id) { const p = progress(id); return p.status === "done" && p.next_review && p.next_review <= today() ? "review" : p.status; }
  const short = (title) => title.replace(/^(\d+)　/, "$1 · ").replace(/^附录 ?([A-Z])　/, "$1 · ");

  async function getJSON(name) {
    const r = await fetch(BASE + name, { cache: "no-cache" });
    if (!r.ok) throw new Error(`读取 ${BASE}${name} 失败：HTTP ${r.status}`);
    return r.json();
  }

  // ---------- overview + tabs ----------
  function overview() {
    const list = chapters(), done = list.filter((c) => ["done", "review"].includes(status(c.id))).length;
    const due = list.filter((c) => status(c.id) === "review").length;
    const pct = list.length ? Math.round((done / list.length) * 100) : 0;
    const lib = state.data.library || {};
    const checks = state.store.checks.length, passed = state.store.checks.filter((c) => c.verdict === "go").length;
    return `<div class="ms-overview ll-overview">
      <article><span>阅读进度</span><strong>${done} / ${list.length} 章</strong><div class="ll-bar"><i style="width:${pct}%"></i></div></article>
      <article><span>今天待复习</span><strong class="${due ? "ll-due" : ""}">${due} 章</strong><small>${due ? "在「逐章阅读」左侧筛选「待复习」" : "读完一章自动排复习：1、3、7、14、30 天"}</small></article>
      <article><span>资料库</span><strong><span class="ll-quantity">${esc(lib.sessions ?? "—")} 段</span> · <span class="ll-quantity">${esc(lib.hours ?? "—")} 小时</span></strong><small>${esc(lib.note || "本人直播录屏 + 问答切片 + 二手整理")}</small></article>
      <article><span>小册版本</span><strong>${esc(state.data.version)}</strong><small><time>${esc((state.data.built_at || "").slice(0, 10))}</time></small><small><a href="${BASE}handbook.pdf" target="_blank" rel="noopener">下载 PDF</a> · 开单检查 ${checks} 次（${passed} 次全过）</small></article>
    </div>`;
  }
  function tabs() {
    return `<div class="ms-controls"><div class="segmented" role="group" aria-label="Bit浪浪体系视图">${TABS.map(([v, label]) =>
      `<button type="button" data-ll-tab="${v}" class="${v === state.tab ? "selected" : ""}" aria-pressed="${v === state.tab}">${label}</button>`).join("")}</div>
      ${state.message ? `<span class="quiet-text">${esc(state.message)}</span>` : ""}</div>`;
  }
  const evBadge = (k) => { const [label, tone] = EVIDENCE[k] || [k, "mid"]; return `<span class="ll-ev ll-ev-${tone}">${esc(label)}</span>`; };

  // ---------- 体系图 ----------
  function systemHTML() {
    const sys = state.data.system || {};
    const stages = (sys.stages || []).map((s) => `<article class="ll-stage ll-stage-${esc(s.trade)}"><header><b>${esc(s.no)}</b><span>${esc(s.name)}</span>
      <em>${s.trade === "ok" ? "做" : s.trade === "bad" ? "逆势·不建议" : "次一级"}</em></header><p>${esc(s.text)}</p></article>`).join("");
    const steps = (sys.steps || []).map((s, i) => `<article class="ll-step" id="ll-step-${esc(s.id)}">
      <header><span class="ll-step-no">${i + 1}</span><div><h3>${esc(s.title)}</h3><p class="ll-q">${esc(s.question)}</p></div>${evBadge(s.evidence)}</header>
      <dl><div><dt>他的说法</dt><dd>${esc(s.rule)}</dd></div>${s.novice ? `<div><dt>可执行标准</dt><dd>${esc(s.novice)}</dd></div>` : ""}
      ${s.check ? `<div><dt>我们的检验</dt><dd>${esc(s.check)}</dd></div>` : ""}</dl>
      <footer>${(s.chapters || []).map((c) => `<button type="button" class="ll-link" data-ll-goto="${esc(c)}">读：${esc(short(chapters().find((x) => x.id === c)?.title || c))}</button>`).join("")}
        ${s.tag ? `<button type="button" class="ll-link" data-ll-tagjump="${esc(s.tag)}">看直播原话</button>` : ""}</footer></article>`).join('<div class="ll-arrow" aria-hidden="true">→</div>');
    return `${sys.headline ? `<p class="ll-headline">${esc(sys.headline)}</p>` : ""}
      ${sys.stage_svg ? `<section class="ms-section"><div class="section-toolbar"><h2>六个点位：①④ 最好，②⑥ 次之，③⑤ 逆势不建议</h2><span class="quiet-text">他 2024-04-21 的原话排序；六个点以外的单子都算“犯病”</span></div><div class="llb ll-figwrap">${sys.stage_svg}</div><div class="ll-stages">${stages}</div></section>` : ""}
      <section class="ms-section"><div class="section-toolbar"><h2>开单前的六步</h2><span class="quiet-text">每一步都标了检验状态：哪些能直接用，哪些已被回测否决</span></div><div class="ll-steps">${steps}</div></section>
      ${(sys.principles || []).length ? `<section class="ms-section"><div class="section-toolbar"><h2>他反复强调的几句话</h2></div><ul class="ll-principles">${sys.principles.map((p) =>
        `<li><b>${esc(p.title)}</b><span>${esc(p.text)}</span>${p.src ? `<a href="${esc(p.src.url)}" target="_blank" rel="noopener noreferrer" class="ll-ts">${esc(p.src.label)}</a>` : ""}</li>`).join("")}</ul></section>` : ""}`;
  }

  // ---------- 逐章阅读 ----------
  function navHTML() {
    const show = (id) => state.filter === "all" || status(id) === state.filter;
    let html = "";
    for (const item of state.data.items) {
      if (item.kind === "part") { html += `<div class="ll-nav-part">${esc(item.title)}</div>`; continue; }
      if (!show(item.id)) continue;
      const st = status(item.id);
      html += `<button type="button" class="ll-nav-item ${item.id === state.current ? "active" : ""}" data-ll-chapter="${esc(item.id)}"${item.id === state.current ? ' aria-current="true"' : ""}>
        <i class="ll-dot ll-${st}" title="${STATUS[st]}"></i><span>${esc(short(item.title))}</span>${item.new ? '<em class="ll-new">新</em>' : ""}</button>`;
    }
    const filters = [["all", "全部"], ["review", "待复习"], ["unread", "未读"], ["reading", "在读"]];
    return `<nav class="ll-nav" aria-label="小册章节"><div class="ll-nav-filter">${filters.map(([v, l]) =>
      `<button type="button" data-ll-filter="${v}" class="${v === state.filter ? "selected" : ""}">${l}</button>`).join("")}</div>${html || `<p class="quiet-text">没有符合筛选的章节。</p>`}</nav>`;
  }
  function readerHTML() {
    const list = chapters(), idx = list.findIndex((c) => c.id === state.current), ch = list[idx];
    if (!ch) return `<div class="empty-state"><h3>选择左侧章节开始</h3></div>`;
    const p = progress(ch.id), st = status(ch.id), prev = list[idx - 1], next = list[idx + 1];
    const review = p.next_review ? `下次复习 ${esc(p.next_review)}${p.stage >= 0 ? ` · 第 ${p.stage + 1} 轮` : ""}` : "读完后排第一次复习";
    const actions = st === "review" ? `<button type="button" class="ll-btn primary" data-ll-act="reviewed">复习完成 · 排下一轮</button>`
      : st === "done" ? `<button type="button" class="ll-btn" data-ll-act="reviewed">提前复习一轮</button>`
        : `<button type="button" class="ll-btn primary" data-ll-act="done">读完 · 排复习</button>`;
    return `<article class="ll-reader">
      <header class="ll-reader-head"><div><span class="ll-chip ll-${st}">${STATUS[st]}</span><h2>${esc(ch.title)}</h2><small>${review}</small></div>
        <div class="ll-actions">${actions}${st !== "unread" ? `<button type="button" class="ll-btn ghost" data-ll-act="reset">设为未读</button>` : ""}</div></header>
      <div class="llb">${ch.html}</div>
      <section class="ll-notes"><label for="ll-note">我的笔记 · 本章</label>
        <textarea id="ll-note" rows="5" maxlength="20000" placeholder="用自己的话复述、记疑问、对照自己的交易……（自动保存在本机浏览器）">${esc(p.notes)}</textarea></section>
      <footer class="ll-pager">${prev ? `<button type="button" class="ll-btn" data-ll-chapter="${esc(prev.id)}">← ${esc(short(prev.title))}</button>` : "<span></span>"}
        ${next ? `<button type="button" class="ll-btn" data-ll-chapter="${esc(next.id)}">${esc(short(next.title))} →</button>` : "<span></span>"}</footer>
    </article>`;
  }

  // ---------- 直播档案 + 原话搜索 ----------
  function sessionsHTML() {
    if (state.sessionsError) return missing(state.sessionsError);
    const s = state.sessions;
    if (!s) return `<p class="research-empty">正在读取直播档案…</p>`;
    const kinds = [["all", "全部"], ...Object.entries(s.kinds || {})];
    const tags = [["all", "全部主题"], ...(s.tags || []).map((t) => [t, t])];
    const q = state.query.trim();
    const head = `<div class="ll-sess-tools">
      <input type="search" id="ll-search" value="${esc(state.query)}" placeholder="搜他的原话：箱体、二波、止损、仓位、接针、PEOPLE……" aria-label="搜索直播转写">
      <div class="ll-chips">${kinds.map(([v, l]) => `<button type="button" data-ll-kind="${esc(v)}" class="${v === state.kind ? "selected" : ""}">${esc(l)}</button>`).join("")}</div>
      <div class="ll-chips">${tags.map(([v, l]) => `<button type="button" data-ll-tag="${esc(v)}" class="${v === state.tag ? "selected" : ""}">${esc(l)}</button>`).join("")}</div></div>`;
    if (q) return head + searchHTML(q);
    const rows = (s.sessions || []).filter((x) => (state.kind === "all" || x.kind === state.kind) && (state.tag === "all" || (x.tags || []).includes(state.tag)));
    const body = rows.map((x) => {
      const open = state.openSession === x.id;
      const pts = (x.points || []).filter((p) => state.tag === "all" || (p.tags || []).includes(state.tag) || (x.tags || []).includes(state.tag));
      return `<article class="ll-sess ${open ? "open" : ""}">
        <header data-ll-open="${esc(x.id)}" role="button" tabindex="0" aria-expanded="${open}">
          <span class="research-mono">${esc(x.date || "日期不详")}</span><strong>${esc(x.title)}</strong>
          <span class="ms-badge${x.tier === "A" ? "" : " ms-badge-warn"}" title="${esc(TIER[x.tier] || "")}">${esc(s.kinds?.[x.kind] || x.kind)}</span>
          <span class="quiet-text">${esc(hms(x.duration || 0))}</span></header>
        ${x.summary ? `<p class="ll-sum">${esc(x.summary)}</p>` : ""}
        ${open ? `<ol class="ll-points">${pts.map((p) => `<li><a class="ll-ts" href="${esc(ytAt(x.url, p.t))}" target="_blank" rel="noopener noreferrer">${esc(hms(p.t))}</a>
          <span>${esc(p.text)}</span>${(p.tags || []).map((t) => `<em class="ll-tag">${esc(t)}</em>`).join("")}</li>`).join("") || "<li class=\"quiet-text\">这一段还没整理要点，可以用上面的搜索查原话。</li>"}</ol>
          ${(x.trades || []).length ? `<div class="ll-sess-extra"><b>这场讲到的单子</b><ul>${x.trades.map((t) => `<li><span class="research-mono">${esc(t.symbol)}</span> ${esc(t.when || "")} · ${esc(t.kind)} → <em>${esc(t.result || "")}</em></li>`).join("")}</ul></div>` : ""}
          ${x.forecast ? `<div class="ll-sess-extra"><b>当场的大级别判断</b><ul>${Object.entries(x.forecast).filter(([k]) => k !== "date").map(([k, v]) => `<li><span class="research-mono">${esc(k.toUpperCase())}</span> ${esc(v)}</li>`).join("")}</ul></div>` : ""}
          ${x.picks && (x.picks.coins || []).length ? `<div class="ll-sess-extra"><b>当场点名关注</b> ${x.picks.coins.map((c) => `<span class="ll-coin">${esc(c)}</span>`).join("")}${x.picks.note ? `<small>${esc(x.picks.note)}</small>` : ""}</div>` : ""}
          <p class="ll-src"><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">打开原视频 ↗</a>${x.uploader ? ` · 上传者 ${esc(x.uploader)}` : ""}${x.note ? ` · ${esc(x.note)}` : ""}</p>` : ""}
      </article>`;
    }).join("");
    return `${head}<p class="quiet-text ll-count">${rows.length} 段 · 点标题展开要点，时间戳直接跳到原视频对应位置</p><div class="ll-sessions">${body}</div>`;
  }
  function searchHTML(q) {
    if (state.txError) return `<p class="research-error">${esc(state.txError)}</p>`;
    if (!state.tx) { loadTx(); return `<p class="research-empty" role="status">正在载入全文转写…</p>`; }
    searchClient ||= window.SpikeTranscriptSearch.create(state.tx,state.sessions.sessions||[]);
    const key=JSON.stringify([q,state.kind,searchPage]);
    if(searchKey!==key){
      if(searchPending!==key){
        searchPending=key;
        searchClient.search(q,state.kind,searchPage*50,50).then(result=>{
          if(searchPending!==key)return;
          searchPending='';
          const lastPage=Math.max(0,Math.ceil(result.total/50)-1);
          if(searchPage>lastPage){searchPage=lastPage;render();return;}
          searchKey=key;searchResult=result;
          if(state.active&&state.tab==='sessions'){
            render();
            if(searchPageFocus){
              root()?.querySelector('.ll-search-pages button:not(:disabled)')?.focus({preventScroll:true});
              root()?.querySelector('.ll-count')?.scrollIntoView({block:'start'});searchPageFocus=false;
            }
          }
        }).catch(()=>{if(searchPending===key){searchPending='';state.txError='搜索暂时失败，请刷新后重试。';render();}});
      }
      return '<p class="research-empty" role="status">正在搜索转写…</p>';
    }
    const mark=text=>window.SpikeTranscriptSearch.highlight(text,q);
    const meta=new Map((state.sessions.sessions||[]).map(x=>[x.id,x]));
    const {total,hits}=searchResult;
    return `<p class="quiet-text ll-count" role="status">「${esc(q)}」共 ${total} 处 · 每页 50 处。转写由语音识别生成，有错字；以原视频为准。</p>
      <ul class="ll-hits">${hits.map(hit=>{const m=meta.get(hit.id);return `<li><div><span class="research-mono">${esc(m.date||'')}</span> <b>${esc(m.title)}</b> <a class="ll-ts" href="${esc(ytAt(m.url,hit.time))}" target="_blank" rel="noopener noreferrer">${esc(hms(hit.time))} ↗</a></div><p>${mark(hit.text)}</p></li>`;}).join('')}</ul>
      <nav class="ll-search-pages" aria-label="搜索结果翻页"><button class="ll-btn" data-search-page="-1" ${searchPage===0?'disabled':''}>上一页</button><span>第 ${searchPage+1} / ${Math.max(1,Math.ceil(total/50))} 页</span><button class="ll-btn" data-search-page="1" ${(searchPage+1)*50>=total?'disabled':''}>下一页</button></nav>`;
  }
  async function loadTx() {
    if (state.tx || state.txLoading) return;
    state.txLoading = true;
    try { state.tx = await getJSON("transcripts.json"); state.txError=null; } catch (e) { state.txError = e.message; }
    state.txLoading = false; render();
  }

  // ---------- 案例盲测 ----------
  function casesHTML() {
    if (state.casesError) return missing(state.casesError);
    const c = state.cases;
    if (!c) return `<p class="research-empty">正在读取案例…</p>`;
    const scored = c.cases.filter((x) => state.store.cases[x.id]?.answer), right = scored.filter((x) => state.store.cases[x.id].answer === x.answer_key).length;
    const cards = c.cases.map((x) => {
      const open = !state.blind || state.revealed[x.id], mine = state.store.cases[x.id] || {};
      return `<article class="ll-case ${open ? "revealed" : ""}">
        <header><div class="ll-case-title"><strong>${esc(x.title)}</strong><span class="ms-badge">${esc(x.stage)}</span><span class="quiet-text">${esc(x.when)}</span></div>
          ${state.blind && !open ? `<button type="button" class="ll-btn" data-ll-reveal="${esc(x.id)}">揭晓</button>` : `<span class="ll-verdict ll-tone-${esc(x.tone || "mid")}">${esc(x.verdict || "")}</span>`}</header>
        <p>${esc(x.setup)}</p>
        <div class="llb ll-chart">${open || !x.svg_blind ? x.svg : x.svg_blind}</div>
        <div class="ll-quiz"><b>${esc(x.question)}</b><div class="ll-chips">${(x.options || []).map(([k, label]) =>
          `<button type="button" data-ll-answer="${esc(x.id)}|${esc(k)}" class="${mine.answer === k ? "selected" : ""}${open && k === x.answer_key ? " ll-key" : ""}">${esc(label)}</button>`).join("")}</div></div>
        <div class="ll-reveal">${(x.reveal || []).map((r) => `<p class="ll-meta"><b>${esc(r[0])}：</b>${esc(r[1])}</p>`).join("")}
          ${(x.links || []).length ? `<p class="ll-src">${x.links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)} ↗</a>`).join(" · ")}</p>` : ""}</div>
        <textarea class="ll-case-note" data-ll-case-note="${esc(x.id)}" rows="2" maxlength="4000" placeholder="揭晓前先写：我判断它在第几阶段？进场点、止损、两道止盈放哪？">${esc(mine.note || "")}</textarea>
      </article>`;
    }).join("");
    return `<div class="section-toolbar ll-case-bar"><h2>案例盲测（${c.cases.length} 个）</h2>
      <span class="quiet-text">已作答 ${scored.length} 个，和他的做法一致 ${right} 个</span>
      <button type="button" class="ll-btn ${state.blind ? "primary" : ""}" data-ll-act="blind">${state.blind ? "盲测中 · 图只画到他做决定那一刻" : "开启盲测"}</button></div>
      <p class="quiet-text">${esc(c.intro || "")}</p><div class="ll-cases ${state.blind ? "ll-blind" : ""}">${cards}</div>`;
  }

  // ---------- 开单检查 ----------
  function blankDraft() { return { symbol: "", kind: "alt", stop: "", tp1: "", tp2: "", answers: {}, note: "" }; }
  function evaluate(d) {
    const items = state.data.checklist || [];
    const stop = parseFloat(d.stop), tp1 = parseFloat(d.tp1), tp2 = parseFloat(d.tp2);
    const cap = d.kind === "btc" ? 10 : 5;
    const lev = stop > 0 ? Math.min(cap, 10 / stop) : null;
    const rr = stop > 0 && tp1 > 0 && tp2 > 0 ? ((tp1 + tp2) / 2) / stop : null;
    const fails = items.filter((it) => d.answers[it.id] === "no").map((it) => it.text);
    const open = items.filter((it) => !d.answers[it.id]).length;
    if (rr !== null && rr < 3) fails.push(`两道止盈平均只有止损的 ${rr.toFixed(1)} 倍（要 ≥3）`);
    const verdict = fails.length ? "stop" : open || rr === null ? "pending" : "go";
    return { lev, rr, fails, open, verdict, cap };
  }
  function checkHTML() {
    const d = state.draft || (state.draft = blankDraft()), ev = evaluate(d);
    const groups = {};
    for (const it of state.data.checklist || []) (groups[it.step] = groups[it.step] || []).push(it);
    const verdictText = ev.verdict === "go" ? "全部通过：可以按计划开单" : ev.verdict === "stop" ? "有一条不满足：不开" : `还有 ${ev.open} 项没回答${ev.rr === null ? "，止损/止盈没填" : ""}`;
    const form = `<form class="ll-check" id="ll-check" autocomplete="off">
      <div class="ll-check-top"><label>币种<input name="symbol" value="${esc(d.symbol)}" placeholder="如 PEOPLE" maxlength="20"></label>
        <label>类型<select name="kind"><option value="alt"${d.kind === "alt" ? " selected" : ""}>山寨（≤5 倍）</option><option value="btc"${d.kind === "btc" ? " selected" : ""}>BTC/ETH（≤10 倍）</option></select></label>
        <label>止损距离 %<input name="stop" inputmode="decimal" value="${esc(d.stop)}" placeholder="2.5"></label>
        <label>止盈一 %<input name="tp1" inputmode="decimal" value="${esc(d.tp1)}" placeholder="4"></label>
        <label>止盈二 %<input name="tp2" inputmode="decimal" value="${esc(d.tp2)}" placeholder="20"></label></div>
      <div class="ll-calc"><span>最大倍数 <b>${ev.lev ? ev.lev.toFixed(1) : "—"}</b>（10 ÷ 止损%，上限 ${ev.cap}）</span>
        <span>平均止盈 / 止损 <b class="${ev.rr !== null && ev.rr < 3 ? "ll-bad" : ""}">${ev.rr ? ev.rr.toFixed(1) : "—"}</b>（≥3 才开）</span>
        <span>一次止损 ≈ 这一份的 <b>${ev.lev && d.stop ? (ev.lev * parseFloat(d.stop)).toFixed(0) : "—"}%</b></span></div>
      ${Object.entries(groups).map(([step, items]) => `<fieldset><legend>${esc(step)}</legend>${items.map((it) => `<div class="ll-q-row">
        <span>${esc(it.text)}${it.hint ? `<small>${esc(it.hint)}</small>` : ""}</span>
        <div class="ll-yn" role="group">${[["yes", "是"], ["no", "否"]].map(([v, l]) => `<button type="button" data-ll-yn="${esc(it.id)}|${v}" class="${d.answers[it.id] === v ? `selected ll-${v}` : ""}">${l}</button>`).join("")}</div></div>`).join("")}</fieldset>`).join("")}
      <label class="ll-wide">备注<textarea name="note" rows="2" maxlength="2000" placeholder="位置判断、拐点在哪、压力位在哪……">${esc(d.note)}</textarea></label>
      <div class="ll-verdict-row ll-v-${ev.verdict}"><strong>${esc(verdictText)}</strong>${ev.fails.length ? `<ul>${ev.fails.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
        <div class="ll-actions"><button type="button" class="ll-btn primary" data-ll-act="savecheck"${ev.verdict === "pending" ? " disabled" : ""}>记一笔检查</button><button type="button" class="ll-btn ghost" data-ll-act="clearcheck">清空</button></div></div>
    </form>`;
    const rows = state.store.checks.slice().reverse().map((c) => `<tr><td class="research-mono">${esc(c.at.slice(0, 16).replace("T", " "))}</td><td>${esc(c.symbol || "—")}</td>
      <td>${c.verdict === "go" ? '<span class="ll-ev ll-ev-ok">全过</span>' : '<span class="ll-ev ll-ev-bad">不开</span>'}</td><td>${c.lev ? c.lev.toFixed(1) : "—"}×</td><td>${c.rr ? c.rr.toFixed(1) : "—"}</td>
      <td><input class="ll-result" data-ll-result="${esc(c.id)}" value="${esc(c.result ?? "")}" placeholder="实际 R" inputmode="decimal" aria-label="实际结果 R"></td><td>${esc((c.fails || []).join("；") || c.note || "")}</td></tr>`).join("");
    const done = state.store.checks.filter((c) => c.result !== undefined && c.result !== "" && !isNaN(parseFloat(c.result)));
    const sumR = done.reduce((a, c) => a + parseFloat(c.result), 0);
    return `<p class="quiet-text">把「开单前检查清单」做成表单：任何一条答「否」就不开。记录只存在本机浏览器；之后回填实际 R，攒够样本再和计划值对照。</p>${form}
      <section class="ms-section"><div class="section-toolbar"><h2>检查记录（${state.store.checks.length}）</h2><span class="quiet-text">${done.length ? `已回填 ${done.length} 笔，合计 ${sumR >= 0 ? "+" : ""}${sumR.toFixed(2)}R，平均 ${(sumR / done.length).toFixed(2)}R` : "还没有回填结果"}</span></div>
      ${rows ? `<div class="ll-table-wrap"><table class="ll-table"><thead><tr><th>时间</th><th>币种</th><th>结论</th><th>倍数</th><th>盈亏比</th><th>实际</th><th>不满足 / 备注</th></tr></thead><tbody>${rows}</tbody></table></div>` : `<p class="quiet-text">还没有记录。</p>`}</section>`;
  }

  // ---------- 证据与迭代 ----------
  function evidenceHTML() {
    const e = state.data.evidence || {};
    const src = (e.sources || []).map((s) => `<li><span class="ll-tier ll-tier-${esc(s.tier)}" title="${esc(TIER[s.tier] || "")}">${esc(s.tier)}</span>
      <div><b>${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>` : esc(s.title)}</b><p>${esc(s.note)}</p></div></li>`).join("");
    const its = (state.data.iterations || []).map((it) => `<li><div><strong>${esc(it.version)} · ${esc(it.title)}</strong>
      <span class="ms-badge${it.status === "planned" ? " ms-badge-warn" : ""}">${it.status === "planned" ? "计划中" : "已完成"}</span>${it.date ? `<span class="quiet-text">${esc(it.date)}</span>` : ""}</div>
      ${(it.notes || []).map((n) => `<p>${esc(n)}</p>`).join("")}</li>`).join("");
    const practices = (state.data.practices || []).map((p) => { const done = !!state.store.practices[p.id];
      return `<li class="${done ? "done" : ""}"><label><input type="checkbox" data-ll-practice="${esc(p.id)}"${done ? " checked" : ""}><span>${esc(p.text)}</span></label>${done ? `<small>完成于 ${esc(state.store.practices[p.id])}</small>` : ""}</li>`; }).join("");
    return `${e.tests ? `<section class="ms-section"><div class="section-toolbar"><h2>检验状态</h2></div><div class="llb">${e.tests}</div></section>` : ""}
      ${practices ? `<section class="ms-section"><div class="section-toolbar"><h2>练习清单</h2></div><ul class="ll-practice">${practices}</ul></section>` : ""}
      <section class="ms-section"><div class="section-toolbar"><h2>资料来源与可信度</h2><span class="quiet-text">A 一手 · B 二手 · C 转述/存疑</span></div><ul class="ll-sources">${src}</ul></section>
      <section class="ms-section"><div class="section-toolbar"><h2>小册版本</h2></div><ul class="ms-iterations">${its}</ul></section>
      <section class="ms-section"><div class="section-toolbar"><h2>怎么迭代</h2></div><div class="research-callout">
        <p>有新资料（直播、交割单、勘误）时：改本机小册源文件，按下面命令重建，刷新本页即可，阅读进度、笔记、盲测答案和检查记录都会保留。小册内容只在本机，不进公开仓库。</p>
        <p class="research-mono">${esc(state.data.rebuild)}</p>
        ${e.notion ? `<p>Notion：${e.notion.map((n) => `<a href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">${esc(n.label)} ↗</a>`).join(" · ")}</p>` : ""}</div></section>
      <section class="ms-section"><div class="section-toolbar"><h2>我的学习记录</h2></div><div class="research-callout">
        <p>进度、笔记、盲测答案和开单检查存在这台电脑的浏览器里。换浏览器或清理站点数据前，先导出备份。</p>
        <div class="ll-actions"><button type="button" class="ll-btn" data-ll-act="export">导出 JSON</button>
        <label class="ll-btn">导入 JSON<input type="file" accept="application/json,.json" id="ll-import" hidden></label></div></div></section>`;
  }

  function missing(msg) {
    return `<div class="empty-state"><h3>本机还没有这部分数据</h3><p>Bit浪浪小册和直播档案不进公开仓库，需要在这台电脑上生成到 <span class="research-mono">yoyo/monitor/static/langlang/</span>。</p>
      <p class="research-mono">python3 ~/Documents/Codex/2026-10-11/bitlanglang/work/build.py &lt;输出目录&gt; --frontend yoyo/monitor/static/langlang</p><p class="quiet-text">${esc(msg)}</p></div>`;
  }

  function render() {
    if(!state.active)return;
    const el = root();
    if (!el || !state.active) return;
    window.SpikeAutoLeaders?.unmount();
    if (state.tab === "auto-leaders") {
      location.hash="auto-leaders"; return;
      el.innerHTML = `${tabs()}<div id="auto-leaders-board"></div>`;
      bind(el);
      window.SpikeNavigation?.sync('langlang', {tab:state.tab});
      window.SpikeAutoLeaders?.mount(el.querySelector('#auto-leaders-board'));
      return;
    }
    if (state.error) { el.innerHTML = missing(state.error); return; }
    if (!state.data) { el.innerHTML = `<p class="research-empty">正在读取 Bit浪浪小册…</p>`; return; }
    const keep = document.activeElement?.id === "ll-search" ? document.activeElement.selectionStart : null;
    const body = { leaders: window.SpikePlaybooks.leaders, system: systemHTML, sessions: sessionsHTML, cases: casesHTML, check: checkHTML, evidence: evidenceHTML }[state.tab]?.() ?? `<div class="ll-layout">${navHTML()}${readerHTML()}</div>`;
    el.innerHTML = `<div class="reader-workspace">${tabs()}<details class="reader-overview" data-work-disclosure="langlang-progress"><summary>阅读进度与资料来源</summary>${overview()}</details><div class="reader-content">${body}</div></div>`;
    bind(el);
    window.SpikeNavigation?.sync('langlang',{tab:state.tab, chapter:state.current, filter:state.filter, q:state.query, page:searchPage, kind:state.kind, tag:state.tag});
    window.SpikeReading?.mount({root:el,view:'langlang',key:STORE_KEY,blocked:storageBlocked,chapters:chapters(),current:state.current,open,reader:'.ll-reader',toc:'.ll-nav',overview:'.ll-overview',save,payload:()=>({exported_at:new Date().toISOString(),handbook:state.data?.version,store:state.store})});
    window.SpikePlaybooks?.bind(el);
    if (keep !== null) { const s = el.querySelector("#ll-search"); s?.focus(); s?.setSelectionRange?.(keep, keep); }
  }

  function open(id, scroll = true) {
    state.current = id; state.store.last = id;
    if (progress(id).status === "unread") update(id, { status: "reading" }); else save();
    state.tab = "read"; render();
    if (scroll) root()?.querySelector(".ll-reader")?.scrollIntoView?.({ block: "start", behavior: "smooth" });
  }
  function setTab(tab) {
    state.tab = tab; state.message = ""; render();
    if (tab === "sessions") loadSessions();
    if (tab === "cases") loadCases();
  }
  async function loadSessions() {
    if (state.sessions || state.sessionsError) return;
    try { state.sessions = await getJSON("sessions.json"); } catch (e) { state.sessionsError = e.message; }
    render();
  }
  async function loadCases() {
    if (state.cases || state.casesError) return;
    try { state.cases = await getJSON("cases.json"); } catch (e) { state.casesError = e.message; }
    render();
  }

  function act(kind) {
    const id = state.current, p = progress(id);
    if (kind === "done") update(id, { status: "done", stage: 0, next_review: addDays(INTERVALS[0]) });
    if (kind === "reviewed") { const stage = Math.min(p.stage + 1, INTERVALS.length - 1); update(id, { status: "done", stage, next_review: addDays(INTERVALS[Math.max(stage, 0)]), last_review: today() }); }
    if (kind === "reset") update(id, { status: "unread", stage: -1, next_review: "" });
    if (kind === "blind") { state.blind = !state.blind; state.revealed = {}; }
    if (kind === "clearcheck") state.draft = blankDraft();
    if (kind === "savecheck") {
      const d = state.draft, ev = evaluate(d);
      if (ev.verdict === "pending") return;
      state.store.checks.push({ id: `c${Date.now()}`, at: new Date().toISOString(), symbol: d.symbol.trim().toUpperCase(), kind: d.kind, stop: d.stop, tp1: d.tp1, tp2: d.tp2,
        lev: ev.lev, rr: ev.rr, verdict: ev.verdict, fails: ev.fails, answers: d.answers, note: d.note });
      save(); state.draft = blankDraft(); state.message = ev.verdict === "go" ? "已记录：全部通过" : "已记录：这单不开";
    }
    if (kind === "export") {
      const blob = new Blob([JSON.stringify({ exported_at: new Date().toISOString(), handbook: state.data?.version, store: state.store }, null, 2)], { type: "application/json" });
      const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `langlang-progress-${today()}.json`; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      state.message = "已导出";
    }
    render();
  }
  function importFile(file) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const v = JSON.parse(String(reader.result)), s = v.store || v;
        if (!s || typeof s.chapters !== "object") throw new Error("文件里没有进度数据");
        state.store = { chapters: obj(s.chapters), practices: obj(s.practices), cases: obj(s.cases), checks: Array.isArray(s.checks) ? s.checks : [], last: s.last || null };
        save(); state.message = "已导入";
      } catch (error) { state.message = `导入失败：${error.message}`; }
      render();
    };
    reader.readAsText(file);
  }

  function bind(el) {
    const on = (sel, ev, fn) => el.querySelectorAll(sel).forEach((n) => n.addEventListener(ev, fn));
    on("[data-ll-tab]", "click", (e) => setTab(e.currentTarget.dataset.llTab));
    on("[data-ll-filter]", "click", (e) => { state.filter = e.currentTarget.dataset.llFilter; render(); });
    on("[data-ll-chapter]", "click", (e) => open(e.currentTarget.dataset.llChapter));
    on("[data-ll-goto]", "click", (e) => open(e.currentTarget.dataset.llGoto));
    on("[data-ll-act]", "click", (e) => act(e.currentTarget.dataset.llAct));
    on("[data-ll-tagjump]", "click", (e) => { state.tag = e.currentTarget.dataset.llTagjump; state.kind = "all"; state.query = ""; setTab("sessions"); });
    on("[data-ll-kind]", "click", (e) => { state.kind = e.currentTarget.dataset.llKind; searchPage=0; render(); });
    on("[data-ll-tag]", "click", (e) => { state.tag = e.currentTarget.dataset.llTag; state.query = ""; render(); });
    const toggle = (n) => { const id = n.dataset.llOpen; state.openSession = state.openSession === id ? null : id; render(); };
    on("[data-ll-open]", "click", (e) => toggle(e.currentTarget));
    on("[data-ll-open]", "keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(e.currentTarget); } });
    const searchInput=el.querySelector('#ll-search');
    const scheduleSearch=e=>{if(e.isComposing)return;const value=e.target.value;clearTimeout(searchTimer);searchTimer=setTimeout(()=>{state.query=value;searchPage=0;render();},180);};
    searchInput?.addEventListener('input',scheduleSearch);
    searchInput?.addEventListener('compositionend',scheduleSearch);
    on('[data-search-page]','click',e=>{searchPage+=Number(e.currentTarget.dataset.searchPage);searchPageFocus=true;render();});
    on("[data-ll-reveal]", "click", (e) => { state.revealed[e.currentTarget.dataset.llReveal] = true; render(); });
    on("[data-ll-answer]", "click", (e) => {
      const [id, k] = e.currentTarget.dataset.llAnswer.split("|");
      state.store.cases[id] = { ...(state.store.cases[id] || {}), answer: k, answered: new Date().toISOString() }; save(); render();
    });
    on("[data-ll-case-note]", "input", (e) => {
      const id = e.target.dataset.llCaseNote, v = e.target.value; clearTimeout(noteTimer);
      state.store.cases[id] = { ...(state.store.cases[id] || {}), note: v }; save();
    });
    on("[data-ll-practice]", "change", (e) => {
      const id = e.target.dataset.llPractice;
      if (e.target.checked) state.store.practices[id] = today(); else delete state.store.practices[id];
      save(); render();
    });
    el.querySelector("#ll-note")?.addEventListener("input", (e) => { const id = state.current, v = e.target.value; clearTimeout(noteTimer); update(id, { notes: v }); });
    el.querySelector("#ll-import")?.addEventListener("change", (e) => { if (e.target.files?.[0]) importFile(e.target.files[0]); });
    const form = el.querySelector("#ll-check");
    if (form) {
      form.addEventListener("submit", (e) => e.preventDefault());
      // Text fields update the draft without re-rendering (keeps focus); the computed row refreshes on change.
      form.querySelectorAll("input,select,textarea").forEach((n) => {
        n.addEventListener("input", () => { state.draft[n.name] = n.value; });
        n.addEventListener("change", () => { state.draft[n.name] = n.value; if (n.name !== "note") render(); });
      });
      on("[data-ll-yn]", "click", (e) => { const [id, v] = e.currentTarget.dataset.llYn.split("|"); state.draft.answers[id] = state.draft.answers[id] === v ? undefined : v; render(); });
    }
    on("[data-ll-result]", "change", (e) => {
      const c = state.store.checks.find((x) => x.id === e.target.dataset.llResult);
      if (c) { c.result = e.target.value.trim(); save(); render(); }
    });
  }

  async function refresh() {
    if (!state.active || state.loading) return;
    state.loading = true;
    try {
      state.data = await getJSON("handbook.json"); state.error = null;
      const ids = chapters().map((c) => c.id);
      if (!ids.includes(state.current)) state.current = ids.includes(state.store.last) ? state.store.last : ids[0] || null;
    } catch (error) { state.error = error.message; }
    finally { state.loading = false; }
    render();
    if (state.tab === "sessions") loadSessions();
    if (state.tab === "cases") loadCases();
  }

  window.SpikeNavigation?.register('langlang',p=>{
    if(['system', 'auto-leaders', 'leaders', 'read', 'sessions', 'cases', 'check', 'evidence'].includes(p.tab))state.tab=p.tab;
    if(p.chapter)state.current=p.chapter;
    state.filter=p.filter?.slice(0,200)||'all';
    state.query=p.q?.slice(0,200)||'';
    searchPage=Math.max(0,Math.min(5000,Number.parseInt(p.page,10)||0));
    state.kind=p.kind?.slice(0,200)||'all';
    state.tag=p.tag?.slice(0,200)||'all';
  });
  const idle=window.SpikeRuntime?.idleRelease(()=>{
    if(state.active)return;clearTimeout(searchTimer);searchPending='';searchKey='';searchResult=null;
    searchClient?.dispose();searchClient=null;state.tx=null;state.txError=null;root()?.replaceChildren();
  });
  window.SpikeLanglang = { openLeaders() { state.tab="leaders"; render(); }, refresh, setActive(value) { const wasActive=state.active; state.active = value;idle?.setActive(value); if (!value && wasActive) window.SpikeAutoLeaders?.unmount(); if (value) { if (state.data) { render(); if(state.tab==="sessions")loadSessions();if(state.tab==="cases")loadCases(); } else { render(); refresh(); } } } };
})();
