/* Notion task 56: source-linked reading library. Editorial source coverage and
 * the owner's browser-local study progress are separate facts. No trading APIs.
 * Text-only rendering keeps imported notes and source metadata out of HTML.
 */
(() => {
  "use strict";
  const KEY = "spike.trading-books.v1", DATA = "/static/trading-books.json";
  const DAYS = [1, 3, 7, 14, 30];
  const esc = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const validID = value => typeof value === "string" && /^[a-zA-Z0-9][a-zA-Z0-9:_-]{0,100}$/.test(value) && !["__proto__", "constructor", "prototype"].includes(value);
  const safeURL = value => { try { const u = new URL(value); return u.protocol === "https:" && !u.username && !u.password ? u.href : ""; } catch { return ""; } };
  const link = (url, label) => safeURL(url) ? `<a href="${esc(safeURL(url))}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>` : "";
  const dateKey = (d = new Date()) => `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
  const nextDate = (stage, now = new Date()) => { const d = new Date(now); d.setDate(d.getDate() + DAYS[Math.min(Math.max(0, stage), DAYS.length-1)]); return dateKey(d); };
  const emptyStore = () => ({ chapters: {}, last: null });
  function normalize(value) {
    if (!value || typeof value !== "object" || Array.isArray(value) || !value.chapters || Array.isArray(value.chapters) || typeof value.chapters !== "object") throw new Error("不是有效的阅读进度");
    const result = emptyStore(), entries = Object.entries(value.chapters);
    if (entries.length > 1000) throw new Error("进度条目过多");
    for (const [id, p] of entries) {
      if (!validID(id) || !p || typeof p !== "object" || !["unread", "reading", "done"].includes(p.status) || typeof p.notes !== "string" || p.notes.length > 30000 || !Number.isInteger(p.stage) || p.stage < -1 || p.stage > 4 || typeof p.next_review !== "string" || (p.next_review && (!/^\d{4}-\d{2}-\d{2}$/.test(p.next_review) || new Date(p.next_review+"T12:00:00Z").toISOString().slice(0,10) !== p.next_review))) throw new Error("进度格式不正确，未改变现有笔记");
      result.chapters[id] = {status:p.status, notes:p.notes, stage:p.stage, next_review:p.next_review};
    }
    result.last = validID(value.last) ? value.last : null;
    return result;
  }
  function merge(current, incoming) {
    const a = normalize(current), b = normalize(incoming);
    return {chapters:{...b.chapters,...a.chapters},last:a.last || b.last};
  }
  const typeLabel = b => ({book:"书籍",course:"官方课程",documentation:"官方文档",article:"短篇 / 手册"}[b.resource_type] || "书籍");
  const accessLabel = b => ({full:"免费完整原文",sample:"官方试读 / 正版入口",borrow:"电子借阅 / 正版入口",purchase:"官方购买入口",official_resource:"官方在线资料"}[b.access_status] || "待完整原文");
  const hasRating = b => Number.isFinite(b.rating) && Number.isInteger(b.rating_count);
  function filterBooks(books, query = "", topic = "", sort = "priority", kind = "", access = "") {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return books.filter(b => (!topic || b.topic === topic) && (!kind || b.resource_type === kind) && (!access || b.access_status === access || b.access_links.some(a=>a.kind===access)) && terms.every(t => `${b.title} ${b.original_title} ${b.author} ${b.topic}`.toLowerCase().includes(t))).sort((a,b) => sort === "rating" ? (b.rating ?? -1)-(a.rating ?? -1) || (b.rating_count ?? -1)-(a.rating_count ?? -1) : sort === "popular" ? (b.rating_count ?? -1)-(a.rating_count ?? -1) : a.priority-b.priority);
  }
  const complete = book => book.total_chapters > 0 && book.reviewed_chapters === book.total_chapters;
  function readerURL(book, chapter) {
    const url=book.pdf_reader_url;
    if (!url) return "";
    const local=`/static/trading-book-originals/${book.id}.pdf`;
    if (url!==local && (!safeURL(url) || url!==book.full_text_url)) return "";
    if (!Number.isInteger(chapter?.pdf_page) || chapter.pdf_page<1 || chapter.pdf_page>book.pdf_pages) return "";
    return `${url}#page=${chapter.pdf_page}&view=FitH`;
  }
  const bookletLabel = book => book.study && complete(book) ? "图解研读小册" : complete(book) ? "章节提纲 · 待深化" : "小册草稿";
  function booklet(book) {
    const deep = book.study ? window.SpikeBookStudy.markdown(book.study,book)+"\n\n---\n\n## 原章节索引与复述\n\n" : "";
    return deep + [`# ${book.title} · ${bookletLabel(book)}`, `${book.author} · ${book.original_title}`, "", `研读覆盖：${book.reviewed_chapters} / ${book.total_chapters ?? "未知"} ${book.chapter_unit || "章"}。${book.source_note}`, `来源：${safeURL(book.source_url) || "来源链接无效"}`, `边界：${book.scope}`, ...book.chapters.flatMap(c => ["",`## ${c.title}`,`${c.source_label}：${safeURL(c.source_url) || "来源链接无效"}`, ...c.sections.flatMap(s => ["",`### ${s.heading}`,...s.paragraphs]), "",`### 练习（编者）`,c.exercise, "",`### 自查要点（编者）`,c.answer])].join("\n\n");
  }
  function validateCatalog(data) {
    if (data?.schema_version !== 1 || !Array.isArray(data.books) || !data.books.length) throw new Error("书架数据格式不正确");
    const ids = new Set();
    for (const b of data.books) {
      if (!safeURL(b.source_url) || !validID(b.id) || ids.has(b.id) || !Array.isArray(b.chapters) || b.reviewed_chapters !== b.chapters.filter(c=>c.reviewed).length || (b.total_chapters !== null && (!Number.isInteger(b.total_chapters) || b.total_chapters < b.reviewed_chapters)) || !["full_text_available","awaiting_full_text","official_resource"].includes(b.source_status)) throw new Error("书籍研读范围不一致");
      if (!["book","course","documentation","article"].includes(b.resource_type) || !["full","sample","borrow","purchase","official_resource"].includes(b.access_status) || !Array.isArray(b.access_links) || b.access_links.some(a=>!safeURL(a.url) || typeof a.label!=="string") || (b.source_status==="full_text_available" && (!safeURL(b.full_text_url) || b.access_status!=="full")) || ((b.rating!==null || b.rating_count!==null) && (!hasRating(b) || b.rating<0 || b.rating>5 || b.rating_count<0))) throw new Error("资料来源或评分格式不正确");
      ids.add(b.id);
      const chapters = new Set();
      for (const c of b.chapters) { if (!safeURL(c.source_url) || !validID(c.id) || chapters.has(c.id) || !Array.isArray(c.sections) || !c.sections.every(s=>Array.isArray(s.paragraphs)) || (b.pdf_reader_url && !readerURL(b,c))) throw new Error("章节数据不正确"); chapters.add(c.id); }
      if (b.study) window.SpikeBookStudy.validate(b.study,b);
    }
    return data;
  }
  function validateOriginal(data, book) {
    if (data?.schema_version !== 1 || data.book_id !== book.id || data.source_sha256 !== book.source_sha256 || !Array.isArray(data.chapters) || data.chapters.length !== book.total_chapters) throw new Error("原文章节与书籍版本不一致");
    const ids = new Set();
    for (const c of data.chapters) {
      if (!validID(c.id) || ids.has(c.id) || !Array.isArray(c.paragraphs) || !c.paragraphs.length || !c.paragraphs.every(p => typeof p === "string" && p.trim())) throw new Error("原文章节内容不完整");
      ids.add(c.id);
    }
    if (!book.chapters.every(c => ids.has(c.id))) throw new Error("原文与小册章节无法对应");
    return data;
  }
  const state = {active:false, data:null, loading:false, error:"", message:"", storageError:"", blockedStorage:false, tab:"shelf", book:null, chapter:null, studyMode:"study", unit:null, query:"", topic:"", kind:"", access:"", sort:"priority", store:emptyStore(), pdfs:{}, pdfLoading:new Set(), pdfErrors:{}, originals:{}, originalLoading:new Set(), originalErrors:{}};
  try { const saved = window.localStorage.getItem(KEY); if (saved) state.store=normalize(JSON.parse(saved)); } catch { state.blockedStorage=true;state.storageError="旧进度无法读取，已停止覆盖；当前笔记只留在此页，请导出备份。"; }
  const root = () => document.getElementById("trading-books-workspace");
  const key = (b,c) => `${b.id}:${c.id}`;
  const progress = id => state.store.chapters[id] || {status:"unread",notes:"",stage:-1,next_review:""};
  const due = p => p.status === "done" && p.next_review && p.next_review <= dateKey();
  const selectedBook = () => state.data?.books.find(b => b.id === state.book);
  const selectedChapter = b => b?.chapters.find(c => c.id === state.chapter);
  const selectedUnit = b => b?.study?.units.find(u=>u.id===state.unit) || b?.study?.units[0];
  const selectedReading = b => state.tab==="book" && state.studyMode==="study" && b?.study ? selectedUnit(b) : selectedChapter(b);
  function resolveReading(b, chapter, unit, mode) {
    const c=b.chapters.find(c=>c.id===chapter)||b.chapters[0];
    const u=b.study?.units.find(u=>u.id===unit)||b.study?.units.find(u=>u.chapter_ids.includes(c?.id))||b.study?.units[0];
    return {chapter:c?.id,unit:u?.id,studyMode:mode==='chapters'?'chapters':'study'};
  }
  function save() {
    if(window.SpikeReading){
      const ok=window.SpikeReading.write(KEY,state.store,state.blockedStorage);
      state.storageError=ok?'':'保存失败，请重试或导出进度备份。';
      const label=root()?.querySelector('[data-books-save]');if(label)label.textContent=state.storageError||'已保存在本浏览器';
      return ok;
    }
    try { if(state.blockedStorage)throw new Error("Existing storage could not be read");window.localStorage.setItem(KEY, JSON.stringify(state.store)); state.storageError=""; }
    catch { state.storageError="浏览器没有保存成功，笔记只留在当前页；请导出进度备份。"; }
    const status = root()?.querySelector("[data-books-save]");
    if (status) status.textContent=state.storageError || "已保存在本浏览器";
  }
  function rating(b) {return hasRating(b) ? `<span class="bk-rating">${b.rating.toFixed(2)}<small> / 5</small></span><span class="bk-count">${b.rating_count.toLocaleString("zh-CN")} 人评价</span>` : `<span class="bk-count">${b.resource_type==='book'?'评分待核对 · 按专业主题收录':'官方资料 · 不套用图书评分'}</span>`;}
  function accessLinks(b) {
    return `<div class="bk-links">${link(b.full_text_url,"打开完整原文")}${b.access_links.map(a=>link(a.url,a.label)).join("")}${link(b.source_url,b.source_label)}${link(b.notion_url,"Notion 资料记录")}</div>`;
  }
  function sourcePanel(b, tab) {
    const available=b.source_status!=="awaiting_full_text";
    return `<section class="bk-pending"><span class="bk-tag">${esc(accessLabel(b))}</span><h3>${tab==='original'?'原文与阅读入口':available?'原文可读，小册待整理':'完整原文待取得'}</h3><p>${esc(b.source_note)}</p>${accessLinks(b)}<p>${available?'已核对来源，尚未逐章或逐课研读。打开原文不会自动标记已读，小册整理进度仍为 0。':'目前提供已核对的试读、借阅或购买入口；访问完整正文可能需要购买、馆卡或机构权限。尚未取得全文，不展示逐章小册。'}</p></section>`;
  }
  function card(b) {
    const coverage = b.chapters.length ? `${bookletLabel(b)} · ${b.reviewed_chapters}/${b.total_chapters} ${esc(b.chapter_unit || "章")}` : accessLabel(b);
    return `<article class="bk-card"><div class="bk-spine bk-tone-${b.priority%3}" aria-hidden="true"><span>${String(b.priority).padStart(2,"0")}</span><strong>${esc(b.topic)}</strong></div><div class="bk-card-main"><span class="bk-eyebrow">${esc(typeLabel(b))} · ${esc(b.author)}${b.year?' · '+esc(b.year):''}</span><h3>${esc(b.title)}</h3><p class="bk-original">${esc(b.original_title)}</p><div class="bk-rating-line">${rating(b)}</div><p>${esc(b.why)}</p><div class="bk-card-footer"><span class="bk-tag">${coverage}</span><button class="bk-button" data-book="${esc(b.id)}">${b.chapters.length ? "阅读小册" : "查看原文入口"} →</button></div></div></article>`;
  }
  function shelfResults() {
    const books=filterBooks(state.data.books,state.query,state.topic,state.sort,state.kind,state.access);
    return `<p class="bk-count" role="status">显示 ${books.length} / ${state.data.books.length} 项</p><div class="bk-grid">${books.map(card).join("") || '<p class="bk-empty">没有匹配资料，试试作者、英文书名或调整筛选。</p>'}</div>`;
  }
  function comparison() {
    const c=state.data.study_comparison;if(!c)return '';
    return `<details class="bk-study-map"><summary>四本对照阅读 · 同一个问题，不同的数据与尺度</summary><p>${esc(c.note)}</p>${window.SpikeBookStudy.table(c.columns,c.rows)}<div class="bk-links">${state.data.books.filter(b=>b.study).map(b=>`<button class="bk-button" data-book="${esc(b.id)}">${esc(b.title)} →</button>`).join('')}</div></details>`;
  }
  function shelf() {
    const books=state.data.books, bookCount=books.filter(b=>b.resource_type==="book").length, sourceCount=books.filter(b=>b.resource_type==="book" && b.source_status==="full_text_available").length;
    const topics=[...new Set(books.map(b=>b.topic))];
    return `<div class="book-shelf-board work-split"><header class="bk-hero"><div><span class="bk-eyebrow">READ, QUESTION, PRACTISE</span><h2>把一本书，读成自己的判断。</h2><p>先看来源与边界，再逐章复述，用真实记录检验。</p></div><div class="bk-numbers"><span><b>${bookCount}</b>本书 · ${books.length-bookCount} 项资料</span><span><b>${sourceCount}</b>本已取得全文</span><span><b>${books.filter(b=>b.resource_type==='book' && complete(b)).length}</b>本原文已研读</span></div></header><aside class="work-inspector book-inspector"><h2>查找阅读资料</h2><div class="bk-controls"><label>搜索书名或作者<input type="search" id="books-query" value="${esc(state.query)}" placeholder="如：威科夫、SMC、订单流…"></label><label>主题<select id="books-topic"><option value="">全部主题</option>${topics.map(t=>`<option${t===state.topic?' selected':''}>${esc(t)}</option>`).join("")}</select></label><label>类型<select id="books-kind">${[["","全部类型"],["book","书籍"],["course","官方课程"],["documentation","官方文档"],["article","短篇 / 手册"]].map(([v,l])=>`<option value="${v}"${v===state.kind?' selected':''}>${l}</option>`).join("")}</select></label><label>原文<select id="books-access">${[["","全部来源"],["full","免费完整原文"],["official_resource","官方在线资料"],["sample","官方试读"],["borrow","电子借阅"],["purchase","官方购买"]].map(([v,l])=>`<option value="${v}"${v===state.access?' selected':''}>${l}</option>`).join("")}</select></label><label>排序<select id="books-sort">${[["priority","建议顺序"],["rating","评分"],["popular","评价人数"]].map(([v,l])=>`<option value="${v}"${v===state.sort?' selected':''}>${l}</option>`).join("")}</select></label></div>${comparison()}</aside><div id="books-results">${shelfResults()}</div><details class="bk-method"><summary>这份书单怎样筛选</summary><p>${esc(state.data.selection_note)}</p><p>检索日期：${esc(state.data.checked_on)}。评分衡量读者评价，不能证明交易方法有效。建议顺序由编者按本工作台学习需求安排。原文覆盖、小册整理与你的已读进度分别记录。</p></details></div>`;
  }
  function bookView() {
    const b=selectedBook(); if (!b) return shelf();
    const c=selectedChapter(b), p=c?progress(key(b,c)):null;
    const source=`<details class="bk-method"><summary>版本、评分与原文来源</summary><p>${esc(b.source_note)}</p>${accessLinks(b)}${hasRating(b)?link(b.rating_url,`Goodreads · ${b.rating} 分 / ${b.rating_count.toLocaleString("zh-CN")} 人`):""}<p>来源核对：${esc(b.source_checked_on || state.data.checked_on)}。${esc(b.scope)}</p></details>`;
    const head=`<button class="bk-back" data-books-tab="shelf">← 返回书架</button><header class="bk-book-header"><span class="bk-eyebrow">${esc(b.topic)} · ${esc(b.author)}</span><h2 tabindex="-1">${esc(b.title)}</h2><p>${esc(b.original_title)}</p><div class="bk-rating-line">${rating(b)}</div><p class="bk-question">${esc(b.reading_question)}</p></header>${source}`;
    if (!c) return `${head}${sourcePanel(b,state.tab)}`;
    if (state.tab === "original") return `${head}${originalView(b,c)}`;
    if (b.study && state.studyMode=== "study") return `${head}${studyView(b)}`;
    return `${head}${studySwitch(b)}<div class="bk-editorial"><span>${bookletLabel(b)} · 原文研读 ${b.reviewed_chapters}/${b.total_chapters} ${esc(b.chapter_unit || "章")}${complete(b)?" · 全书覆盖":"，后续 "+(b.total_chapters-b.reviewed_chapters)+" "+(b.chapter_unit || "章")+"待整理"}</span><button class="bk-button" data-books-export="booklet">导出${bookletLabel(b)}</button></div><div class="bk-reading"><nav class="bk-toc" aria-label="书籍章节">${b.chapters.map(ch=>{const pr=progress(key(b,ch));return `<button data-chapter="${esc(ch.id)}"${ch.id===c.id?' aria-current="true"':''}><span>${esc(ch.title)}</span><small>${due(pr)?'待复习':pr.status==='done'?'已读':pr.status==='reading'?'在读':'未读'}</small></button>`;}).join("")}<p>以上是已整理章节。你读完小册，不会改变编者的原文研读范围。</p></nav><article class="bk-reader"><header><span class="bk-eyebrow">${esc(c.source_label)}</span><h3 tabindex="-1">${esc(c.title)}</h3>${link(c.source_url,"核对本章原文")}</header>${c.sections.map(s=>`<section><h4>${esc(s.heading)}</h4>${s.paragraphs.map(t=>`<p>${esc(t)}</p>`).join("")}</section>`).join("")}<section class="bk-exercise"><span class="bk-eyebrow">闭卷复述 · 编者练习</span><p>${esc(c.exercise)}</p><details><summary>写完再看自查要点</summary><p>${esc(c.answer)}</p></details></section><section class="bk-notes"><label for="books-note">我的复述、疑问与实例</label><textarea id="books-note" maxlength="30000" rows="5" placeholder="先用自己的话复述，再写一个反例…">${esc(p.notes)}</textarea><p data-books-save role="status">${esc(state.storageError || "笔记保存在本浏览器，可导出备份；不会自动同步到其他设备。")}</p><div class="bk-note-actions"><button class="bk-button primary" data-books-done>${p.status==='done'?'复习完成 · 排下一轮':'读完本章 · 排复习'}</button><span>${p.next_review?`下次复习 ${esc(p.next_review)}`:'复习间隔：1、3、7、14、30 天'}</span></div></section></article></div>`;
  }
  function studySwitch(b) {
    return b.study ? `<div class="bk-study-switch" role="group" aria-label="小册阅读方式"><button class="bk-button" data-study-mode="study" aria-pressed="${state.studyMode==='study'}">深度图解 · ${b.study.units.length} 单元</button><button class="bk-button" data-study-mode="chapters" aria-pressed="${state.studyMode==='chapters'}">原章节索引 · ${b.chapters.length} ${esc(b.chapter_unit||'章')}</button></div>` : '';
  }
  function studyView(b) {
    const study=b.study,u=selectedUnit(b),p=progress(key(b,u)),S=window.SpikeBookStudy;
    const sourceChapters=u.chapter_ids.map(id=>b.chapters.find(c=>c.id===id));
    const sourceLinks=sourceChapters.map(c=>`<button class="bk-button" data-study-source="${esc(c.id)}">${esc(c.source_label)} ↗</button>`).join('');
    const intro=`<details class="bk-study-map"${u.id===study.units[0].id?' open':''}><summary>全书导读 · 怎样读这本书</summary><p class="bk-question">${esc(study.overview.question)}</p><p>${esc(study.overview.thesis)}</p><ol class="bk-reading-path">${study.overview.reading_path.map(t=>`<li>${esc(t)}</li>`).join('')}</ol><details><summary>阅读范围与证据边界</summary>${study.overview.limits.map(t=>`<p>${esc(t)}</p>`).join('')}</details></details>`;
    const nav=`<nav class="bk-toc" aria-label="图解研读单元">${study.units.map(unit=>{const pr=progress(key(b,unit));return `<button data-study-unit="${esc(unit.id)}"${unit.id===u.id?' aria-current="true"':''}><span>${esc(unit.title)}</span><small>原文 ${esc(unit.chapter_ids.join('、'))} · ${due(pr)?'待复习':pr.status==='done'?'已读':pr.status==='reading'?'在读':'未读'}</small></button>`;}).join('')}<p>单元按主题串联原章节。旧章节笔记保留在“原章节索引”，单元练习单独记录。</p></nav>`;
    // Pair figures with the explanatory sections instead of an image dump.
    const sections=u.sections.map((section,i)=>`<section><h4>${esc(section.heading)}</h4>${section.paragraphs.map(t=>`<p>${esc(t)}</p>`).join('')}${i>0&&u.figures[i-1]?S.figure(u.figures[i-1],i-1):''}</section>`).join('');
    const remaining=u.figures.slice(Math.max(0,u.sections.length-1)).map((f,i)=>S.figure(f,i+u.sections.length-1)).join('');
    const exercises=u.exercises.map((e,i)=>`<section class="bk-exercise"><span class="bk-eyebrow">练习 ${i+1} · 先作答，再展开解析</span><p>${esc(e.prompt)}</p><details><summary>展开推理与答案</summary><p>${esc(e.answer)}</p></details></section>`).join('');
    const notes=`<section class="bk-notes"><label for="books-note">我的推演、反例与疑问</label><textarea id="books-note" maxlength="30000" rows="6" placeholder="先写出你看见的事实，再写推断；记录让判断失效的条件。">${esc(p.notes)}</textarea><p data-books-save role="status">${esc(state.storageError||'笔记保存在当前浏览器，可导出备份。')}</p><div class="bk-note-actions"><button class="bk-button primary" data-books-done>${p.status==='done'?'复习完成 · 排下一轮':'学完本单元 · 排复习'}</button><span>${p.next_review?`下次复习 ${esc(p.next_review)}`:'复习间隔：1、3、7、14、30 天'}</span></div></section>`;
    const synthesis=`<details class="bk-study-map"><summary>全书综合与术语对照</summary>${study.synthesis.map(s=>`<h4>${esc(s.heading)}</h4>${s.paragraphs.map(t=>`<p>${esc(t)}</p>`).join('')}`).join('')}${S.table(['术语','在本书中的含义','原章节'],study.terms.map(t=>[t.term,t.meaning,t.source_chapter]))}</details>`;
    const index=study.units.indexOf(u),pagebar=`<nav class="bk-unit-pagebar" aria-label="研读单元翻页"><span>${index+1} / ${study.units.length} 单元</span><div>${[[-1,'上一单元'],[1,'下一单元']].map(([delta,label])=>{const unit=study.units[index+delta];return `<button class="bk-button" ${unit?`data-study-unit="${esc(unit.id)}"`:'disabled'}>${label}</button>`;}).join('')}</div></nav>`;
    return `${studySwitch(b)}<div class="bk-editorial"><span>${study.units.length} 个图解单元 · 对应 ${new Set(study.units.flatMap(x=>x.chapter_ids)).size}/${b.chapters.length} 个原章节 · 内容整理不代表策略验证</span><button class="bk-button" data-books-export="booklet">导出图解小册与数据</button></div>${intro}<div class="bk-reading">${nav}<article class="bk-reader bk-study-reader">${pagebar}<header><span class="bk-eyebrow">深度研读 · 原文 ${esc(u.chapter_ids.join('、'))}</span><h3 tabindex="-1">${esc(u.title)}</h3><p class="bk-question">${esc(u.question)}</p><details class="bk-unit-sources"><summary>核对本单元原文与页码</summary><div class="bk-links">${sourceLinks}</div></details></header>${sections}${remaining}<section><h4>带走什么</h4><ul class="bk-takeaways">${u.takeaways.map(t=>`<li>${esc(t)}</li>`).join('')}</ul></section>${exercises}${notes}${synthesis}</article></div>`;
  }
  function originalView(b,c) {
    const original=state.originals[b.id], chapter=original?.chapters.find(ch=>ch.id===c.id);
    const error=state.originalErrors[b.id];
    const toc=`<nav class="bk-toc" aria-label="原文章节">${b.chapters.map(ch=>`<button data-chapter="${esc(ch.id)}"${ch.id===c.id?' aria-current="true"':''}><span>Chapter ${esc(ch.id)}</span><small>${esc(ch.title)}</small></button>`).join("")}</nav>`;
    const pdf=readerURL(b,c);
    const embedded=pdf && (state.pdfs[b.id] ? `${state.pdfs[b.id]}#page=${c.pdf_page}&view=FitH` : b.pdf_reader_url.startsWith("/") ? "" : pdf);
    const pdfBody=embedded ? `<iframe class="bk-pdf-frame" src="${esc(embedded)}" title="${esc(b.title)} · ${esc(c.source_label)}" referrerpolicy="no-referrer"></iframe>` : `<div class="bk-empty" role="status">${state.pdfErrors[b.id]?`${esc(state.pdfErrors[b.id])} <button class="bk-button" data-pdf-retry>重试加载 PDF</button>`:"正在载入完整扫描件…"}</div>`;
    const text=chapter ? chapter.paragraphs.map(p=>`<p>${esc(p)}</p>`).join("") : `<div role="status">${error?`<p>${esc(error)}</p><button class="bk-button" data-original-retry>重试加载原文</button>`:"正在读取文字…"}</div>`;
    const body=pdf ? `<article class="bk-reader bk-pdf-reader"><header><span class="bk-eyebrow">${esc(c.source_label)}</span><h3 tabindex="-1">${esc(c.title)}</h3><p>${esc(b.reader_note)}</p><div class="bk-links"><a href="${esc(pdf)}" target="_blank" rel="noopener noreferrer">独立打开本章 PDF ↗</a><button class="bk-button" data-books-tab="book">阅读本章小册 →</button></div></header><div class="bk-local-file"><label class="bk-button">打开已下载原件<input id="books-local-pdf" type="file" accept="application/pdf,.pdf"></label><p class="bk-count">文件只在当前浏览器打开，不上传；刷新后需重新选择。${state.pdfs[b.id] && !b.pdf_reader_url.startsWith("/") ? " 已核对原件版本，当前显示本地文件。" : ""}</p><p role="status">${esc(state.pdfErrors[b.id] || "")}</p></div>${pdfBody}<p class="bk-count">阅读器定位到 PDF 第 ${c.pdf_page} 页。若浏览器未显示 PDF，可使用上方“独立打开”；印刷页码见章节标注。</p>${b.original_path?`<details class="bk-ocr"><summary>本章可检索文字（OCR，图表以扫描件为准）</summary><div lang="en">${text}</div></details>`:""}</article>` : chapter ? `<article class="bk-reader bk-original-reader" lang="en"><header><span class="bk-eyebrow">${esc(c.source_label)}</span><h3 tabindex="-1">Chapter ${esc(c.id)}</h3>${link(c.source_url,"核对来源")}</header>${text}<button class="bk-button" data-books-tab="book" lang="zh-CN">阅读本章小册 →</button></article>` : `<div class="bk-empty">${b.original_path?text:"这本尚未提供内嵌原文。"}</div>`;
    return `<div class="bk-editorial"><span>英文原文 · ${b.year} · ${b.total_chapters} ${esc(b.chapter_unit || '章')}</span><div class="bk-links">${b.original_path?`<a href="/static/trading-book-originals/${esc(b.id)}.txt" target="_blank" rel="noopener noreferrer">完整文字与来源说明 ↗</a>`:""}<button class="bk-button" data-books-tab="book">阅读本章小册 →</button></div></div><div class="bk-reading">${toc}${body}</div>`;
  }
  let resourceVersion=0;
  const pdfRequests=new Map();
  function releasePDFs(except){
    for(const [id,url] of Object.entries(state.pdfs))if(id!==except){URL.revokeObjectURL(url);delete state.pdfs[id];}
  }
  async function installPDF(b,bytes,version=resourceVersion) {
    if(bytes.byteLength>32*1024*1024)throw new Error("PDF 超过 32 MB，请使用独立阅读器打开");
    const digest=await window.crypto.subtle.digest("SHA-256",bytes);
    const sha=Array.from(new Uint8Array(digest),x=>x.toString(16).padStart(2,"0")).join("");
    if(sha!==b.source_sha256)throw new Error("扫描件与已核对版本不一致");
    if(version!==resourceVersion||(state.active&&state.book!==b.id))return;
    releasePDFs(b.id);
    const previous=state.pdfs[b.id];
    state.pdfs[b.id]=URL.createObjectURL(new Blob([bytes],{type:"application/pdf"}));
    if(previous)URL.revokeObjectURL(previous);
    delete state.pdfErrors[b.id];
  }
  async function loadPDF(b) {
    // Blob-backed viewing keeps the site's anti-framing headers intact.
    if (!b?.pdf_reader_url?.startsWith("/") || !readerURL(b,b.chapters[0]) || state.pdfs[b.id] || state.pdfLoading.has(b.id)) return;
    state.pdfLoading.add(b.id);delete state.pdfErrors[b.id];
    const version=resourceVersion,controller=new AbortController();pdfRequests.set(b.id,controller);
    const timer=setTimeout(()=>controller.abort(),20000);
    try {
      const response=await fetch(b.pdf_reader_url,{signal:controller.signal});
      if(Number(response.headers.get("content-length"))>32*1024*1024)throw new Error("PDF 超过 32 MB，请使用独立阅读器打开");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const bytes=await response.arrayBuffer();
      await installPDF(b,bytes,version);
    } catch(error) {if(version===resourceVersion)state.pdfErrors[b.id]=`PDF 读取失败：${error.message}`;}
    finally {clearTimeout(timer);if(pdfRequests.get(b.id)===controller)pdfRequests.delete(b.id);state.pdfLoading.delete(b.id);if(state.active && state.tab==="original" && state.book===b.id)render();}
  }
  async function loadOriginal() {
    const b=selectedBook();
    void loadPDF(b);
    if (!b?.original_path || state.originals[b.id] || state.originalLoading.has(b.id)) return;
    if (b.original_path !== `/static/trading-book-originals/${b.id}.json`) {state.originalErrors[b.id]="原文地址不正确";render();return;}
    const version=resourceVersion;state.originalLoading.add(b.id);delete state.originalErrors[b.id];render();
    try {
      const response=await fetch(b.original_path,{cache:"no-cache"});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const original=validateOriginal(await response.json(),b);if(version===resourceVersion)state.originals[b.id]=original;
    } catch(error) {state.originalErrors[b.id]=`原文读取失败：${error.message}`;}
    finally {state.originalLoading.delete(b.id);if(state.active && state.tab==="original" && state.book===b.id)render();}
  }
  function reviews() {
    const rows=state.data.books.flatMap(b=>[...b.chapters,...(b.study?.units||[])].map(c=>({b,c,p:progress(key(b,c))}))).filter(x=>x.p.status!=="unread" || x.p.notes);
    return `<section class="bk-review"><h2>我的笔记与复习</h2><p>此处只记录你对已整理小册的学习。浏览一本候选书，不会变成“读完原文”。</p>${rows.length?rows.map(({b,c,p})=>`<article><span class="bk-tag">${due(p)?'到期复习':p.status==='done'?'已读':'在读'}</span><h3>${esc(b.title)} · ${esc(c.title)}</h3><p class="bk-note-preview">${esc(p.notes || "还没有笔记")}</p><p>${p.next_review?`下次复习：${esc(p.next_review)}`:'未排复习'}</p><button class="bk-button" data-review-book="${esc(b.id)}" data-review-chapter="${esc(c.id)}">回到本章 →</button></article>`).join(""):'<p class="bk-empty">写下第一条笔记，或读完一章后，这里就会显示进度。</p>'}</section>`;
  }
  function render() {
    if(!state.active)return;
    const el=root(); if (!el || !state.active) return;
    if (state.error) {el.innerHTML=`<div class="bk-empty"><h2>书架暂时未载入</h2><p>${esc(state.error)}</p><button class="bk-button" data-books-retry>重试</button></div>`;el.querySelector("[data-books-retry]").onclick=refresh;return;}
    if (!state.data) {el.innerHTML='<p class="research-empty">正在读取交易书架…</p>';return;}
    el.innerHTML=`<div class="bk-toolbar"><div class="segmented" role="group" aria-label="交易书架视图">${[["shelf","书架"],["original","原文"],["book","小册"],["reviews","笔记与复习"]].map(([v,l])=>`<button data-books-tab="${v}" aria-pressed="${state.tab===v}" class="${state.tab===v?'selected':''}">${l}</button>`).join("")}</div><div class="bk-tools"><button class="bk-button" data-books-export="progress">导出进度</button><label class="bk-button">导入进度<input id="books-import" type="file" accept="application/json,.json"></label></div></div><p class="bk-message" role="status">${esc(state.message || state.storageError)}</p>${state.tab==='shelf'?shelf():state.tab==='reviews'?reviews():bookView()}`;
    bind(el);
    syncLocation();
    const inStudy=state.tab==='book'&&state.studyMode==='study'&&Boolean(selectedBook()?.study);
    window.SpikeReading?.mount({root:el,view:'trading-books',key:KEY,hasExport:true,blocked:state.blockedStorage,chapters:inStudy?[]:selectedBook()?.chapters||[],current:state.chapter,
      open(id){state.chapter=id;render();root()?.querySelector('.bk-reader')?.scrollIntoView({block:'start',behavior:'smooth'});},reader:'.bk-reader',toc:inStudy?null:'.bk-toc',save,
      payload:()=>({format:'spike.trading-books',version:1,exported_at:new Date().toISOString(),store:state.store})});
    const current=el.querySelector('.bk-toc button[aria-current]');
    if(current)current.parentElement.scrollTop=Math.max(0,current.offsetTop-current.parentElement.offsetTop-20);
  }
  function chooseBook(id, chapter=null) {
    const b=state.data.books.find(x=>x.id===id); if(!b)return;
    state.book=id; state.chapter=b.chapters.some(c=>c.id===chapter)?chapter:b.chapters[0]?.id;
    if(b.study?.units.some(u=>u.id===chapter)){state.unit=chapter;state.chapter=selectedUnit(b).chapter_ids[0];state.studyMode="study";}else if(chapter){state.studyMode="chapters";}else {state.unit=b.study?.units[0]?.id;state.studyMode="study";}state.tab=b.chapters.length?"book":"original";state.store.last=id;save();render();
    root()?.querySelector(".bk-book-header h2")?.focus({preventScroll:false});
  }
  function download(text, name, type) {
    const url=URL.createObjectURL(new Blob([text],{type})), a=document.createElement("a");a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function bindCards(el) {el.querySelectorAll("[data-book]").forEach(b=>b.onclick=()=>chooseBook(b.dataset.book));}
  function bind(el) {
    bindCards(el);
    el.querySelectorAll('[data-study-mode]').forEach(button=>button.onclick=()=>{state.studyMode=button.dataset.studyMode;if(state.studyMode==='study')state.unit=selectedBook().study.units.find(u=>u.chapter_ids.includes(state.chapter))?.id;render();});
    el.querySelectorAll('[data-study-unit]').forEach(button=>button.onclick=()=>{state.unit=button.dataset.studyUnit;state.chapter=selectedUnit(selectedBook()).chapter_ids[0];state.studyMode="study";render();root()?.querySelector('.bk-reader h3')?.focus();});
    el.querySelectorAll('[data-study-source]').forEach(button=>button.onclick=()=>{state.chapter=button.dataset.studySource;state.tab="original";render();loadOriginal();});
    if(state.tab==="book"&&state.studyMode==="study"&&selectedBook()?.study)window.SpikeBookStudy.bindFigures(el,selectedUnit(selectedBook()).figures);
    el.querySelector("#books-local-pdf")?.addEventListener("change",async e=>{
      const file=e.target.files?.[0], b=selectedBook();if(!file || !b)return;
      try {if(file.size>50000000)throw new Error("文件超过 50 MB");await installPDF(b,await file.arrayBuffer());}
      catch(error){state.pdfErrors[b.id]=`本地原件未打开：${error.message}`;}
      if(state.book===b.id && state.tab==="original")render();
    });
    el.querySelectorAll("[data-books-tab]").forEach(b=>b.onclick=()=>{const next=b.dataset.booksTab;if(state.tab==="original"&&next==="book"){const unit=selectedBook()?.study?.units.find(u=>u.chapter_ids.includes(state.chapter));if(unit)state.unit=unit.id;}state.tab=next;state.message="";render();if(state.tab==="original")loadOriginal();});
    el.querySelector("[data-original-retry]")?.addEventListener("click",loadOriginal);
    el.querySelector("[data-pdf-retry]")?.addEventListener("click",()=>{void loadPDF(selectedBook());render();});
    el.querySelectorAll("[data-chapter]").forEach(b=>b.onclick=()=>{state.chapter=b.dataset.chapter;render();root()?.querySelector(".bk-reader h3")?.focus({preventScroll:false});});
    el.querySelectorAll("[data-review-book]").forEach(b=>b.onclick=()=>chooseBook(b.dataset.reviewBook,b.dataset.reviewChapter));
    for (const [id,prop,event] of [["books-query","query","input"],["books-topic","topic","change"],["books-sort","sort","change"],["books-kind","kind","change"],["books-access","access","change"]]) {
      el.querySelector(`#${id}`)?.addEventListener(event,e=>{state[prop]=e.target.value;const results=el.querySelector("#books-results");results.innerHTML=shelfResults();bindCards(results);syncLocation();});
    }
    el.querySelector("#books-note")?.addEventListener("input",e=>{const b=selectedBook(),c=selectedReading(b),id=key(b,c),p=progress(id);state.store.chapters[id]={...p,notes:e.target.value,status:p.status==='unread'?'reading':p.status};save();});
    el.querySelector("[data-books-done]")?.addEventListener("click",()=>{const b=selectedBook(),c=selectedReading(b),id=key(b,c),p=progress(id),stage=Math.min(p.stage+1,4);state.store.chapters[id]={...p,status:"done",stage,next_review:nextDate(stage)};save();render();});
    el.querySelectorAll("[data-books-export]").forEach(b=>b.onclick=()=>{
      if(b.dataset.booksExport==='booklet'){const book=selectedBook();download(booklet(book),`${book.id}-${complete(book)?"booklet":"draft"}.md`,"text/markdown;charset=utf-8");}
      else download(JSON.stringify({format:"spike.trading-books",version:1,exported_at:new Date().toISOString(),store:state.store},null,2),`trading-books-progress-${dateKey()}.json`,"application/json");
    });
    el.querySelector("#books-import")?.addEventListener("change",async e=>{
      const file=e.target.files?.[0];if(!file)return;
      try {if(file.size>2000000)throw new Error("文件超过 2 MB");const v=JSON.parse(await file.text());if(v.format!=="spike.trading-books" || v.version!==1)throw new Error("请选择本书架导出的进度文件");state.store=merge(state.store,v.store);save();state.message="已合并进度；同章冲突保留本浏览器笔记。";}
      catch(error){state.message=`导入失败：${error.message}`;}render();
    });
  }
  async function refresh() {
    if(state.loading)return;state.loading=true;state.error="";
    try {const response=await fetch(DATA,{cache:"no-cache"});if(!response.ok)throw new Error(`HTTP ${response.status}`);state.data=validateCatalog(await response.json());const b=state.data.books.find(x=>x.id===(state.book || state.store.last)) || state.data.books[0];state.book=b.id;Object.assign(state,resolveReading(b,state.chapter,state.unit,state.studyMode));}
    catch(error){state.error=`读取失败：${error.message}`;}finally{state.loading=false;render();}
  }
  function syncLocation(){window.SpikeNavigation?.sync('trading-books',{tab:state.tab,book:state.book,chapter:state.chapter,mode:state.studyMode,unit:state.unit,q:state.query,topic:state.topic,kind:state.kind,access:state.access,sort:state.sort});}
  window.SpikeNavigation?.register('trading-books',p=>{
    if(['shelf','original','book','reviews'].includes(p.tab))state.tab=p.tab;
    if(p.book)state.book=p.book;
    if(p.chapter)state.chapter=p.chapter;
    state.unit=p.unit||null;
    state.studyMode=p.mode==='chapters'?'chapters':p.mode==='study'||p.unit?'study':p.chapter?'chapters':'study';
    for(const [key,field] of [['q','query'],['topic','topic'],['kind','kind'],['access','access']])state[field]=(p[key]||'').slice(0,200);
    state.sort=['priority','rating','popular'].includes(p.sort)?p.sort:'priority';
  });
  const idle=window.SpikeRuntime?.idleRelease(()=>{
    if(state.active)return;resourceVersion++;for(const c of pdfRequests.values())c.abort();pdfRequests.clear();
    state.pdfLoading.clear();releasePDFs();state.originals={};root()?.replaceChildren();
  });
  window.SpikeTradingBooks={setActive(active){state.active=active;idle?.setActive(active);if(active){const b=selectedBook();if(b)Object.assign(state,resolveReading(b,state.chapter,state.unit,state.studyMode));render();if(!state.data)refresh();else if(state.tab==="original")loadOriginal();}},refresh};
  if(typeof module!=="undefined" && module.exports)module.exports={normalize,merge,filterBooks,booklet,validateCatalog,validateOriginal,nextDate,safeURL,esc,rating,sourcePanel,readerURL,originalView,loadPDF,installPDF,studyView,bookletLabel,resolveReading};
})();
