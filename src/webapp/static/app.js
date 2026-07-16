/* fable-trading dashboard frontend (vanilla JS + Lightweight Charts v4) */
/* allow: SIZE_OK -- single-file; r2: view-cache, trades page, a11y, keyboard, escape. */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const fmtPct = (x, digits = 2) => (x === null || x === undefined || Number.isNaN(Number(x)) ? "—" : (100 * x).toFixed(digits) + "%");
const fmtPF = (x) => (x === null || x === undefined || Number.isNaN(Number(x)) ? "—" : Number(x).toFixed(2));
const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const OUTCOME_CN = { tp: "止盈", sl: "止损", timeout: "超时", sl_ambiguous: "止损*", "": "未结束" };
const OUTCOME_COLOR = { tp: "#199e70", sl: "#e66767", timeout: "#c98500", sl_ambiguous: "#e66767" };
const STATUS_CN = { open: "持有中", closed: "已结束" };
const appState = { universe: "swap", view: "explore" };
/** views loaded for current universe — skip refetch when tabbing back (TTL, not forever) */
const viewLoadedAt = new Map(); // view -> timestamp
const VIEW_ORDER = ["explore", "overview", "backtest", "signals", "forward", "ethmicro", "experiments", "agenda", "jobs", "data", "models"];
const CHART_LAYOUT = {
  layout: { background: { type: "solid", color: "#0a0c10" }, textColor: "#8b949e" },
  grid: { vertLines: { color: "#161b22" }, horzLines: { color: "#161b22" } },
  timeScale: { borderColor: "#30363d", timeVisible: true },
  rightPriceScale: { borderColor: "#30363d" },
  crosshair: { mode: 0 },
};
const pctFormat = { type: "custom", formatter: (v) => v.toFixed(2) + "%" };

/* ---------- fetch helpers (cache + abort + toast) ---------- */
const _jsonCache = new Map(); // url -> { t, data }
const CACHE_TTL_MS = 30_000;
/** Keep view-level skip aligned with JSON cache TTL so deploy updates surface without hard refresh. */
const VIEW_CACHE_TTL_MS = CACHE_TTL_MS;
let chartAbort = null;

function viewNeedsLoad(name, force = false) {
  if (force) return true;
  const t = viewLoadedAt.get(name);
  if (t == null) return true;
  return Date.now() - t >= VIEW_CACHE_TTL_MS;
}

function markViewLoaded(name) {
  viewLoadedAt.set(name, Date.now());
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function toast(msg, kind = "error") {
  const host = $("#toast-host");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

async function apiGet(url, { cache = false, signal, quiet = false } = {}) {
  if (cache) {
    const hit = _jsonCache.get(url);
    if (hit && Date.now() - hit.t < CACHE_TTL_MS) return hit.data;
  }
  let res;
  try {
    res = await fetch(url, { signal });
  } catch (err) {
    if (err?.name === "AbortError") throw err;
    if (!quiet) toast(`网络错误：${err.message || err}`);
    throw err;
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    if (!quiet) toast(`请求失败 ${res.status}${detail ? "：" + detail.slice(0, 120) : ""}`);
    throw new Error(`HTTP ${res.status}`);
  }
  const data = await res.json();
  if (cache) _jsonCache.set(url, { t: Date.now(), data });
  return data;
}

function makeChart(el, opts = {}) {
  if (typeof LightweightCharts === "undefined") {
    toast("图表库未加载，请刷新页面");
    throw new Error("LightweightCharts missing");
  }
  return LightweightCharts.createChart(el, { ...CHART_LAYOUT, autoSize: true, ...opts });
}

function apiUrl(path, params = {}) {
  const query = new URLSearchParams({ universe: appState.universe, ...params });
  return `${path}?${query.toString()}`;
}

function invalidateViews() {
  viewLoadedAt.clear();
  _jsonCache.clear();
}

/* ---------- sidebar + hash routing (Hummingbot multipage shell) ---------- */
function showView(name, { pushHash = true, force = false } = {}) {
  if (!name || name === "scout") return;
  const same = appState.view === name;
  appState.view = name;
  $$(".sb-item[data-view], .tab[data-view]").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === name));
  $$(".view").forEach((v) => {
    const active = v.id === "view-" + name;
    v.classList.toggle("hidden", !active);
    v.hidden = !active;
    v.setAttribute("aria-hidden", active ? "false" : "true");
  });
  if (pushHash && location.hash !== "#" + name) {
    history.replaceState(null, "", "#" + name);
  }
  // stop jobs poll when leaving
  if (name !== "jobs") stopJobsPoll?.();

  const need = viewNeedsLoad(name, force);
  const mark = () => markViewLoaded(name);
  if (name === "explore") {
    if (need) { loadExplore().then(mark); } else {
      mark();
      // chart may need resize after sidebar layout settles
      setTimeout(() => { try { drawExploreBoxes(); } catch (_) {} }, 80);
    }
    return;
  }
  if (name === "overview") {
    if (need) { loadOverview().then(mark); } else mark();
    return;
  }
  if (name === "backtest") { if (need) loadBacktest().then(mark); else mark(); return; }
  if (name === "signals") { initSignals(force || need); mark(); return; }
  if (name === "forward") { if (need) loadForward().then(mark); else mark(); return; }
  if (name === "ethmicro") { if (need) loadEthMicro().then(mark); else mark(); return; }
  // ops tabs always refresh lightly (cheap + may change)
  if (name === "experiments") loadExperiments();
  if (name === "agenda") loadAgenda();
  if (name === "jobs") loadJobsView();
  if (name === "data") loadDataHub();
  if (name === "models") loadModelHub();
  if (!same) {
    const scroller = document.querySelector(".app-main") || window;
    try { scroller.scrollTo({ top: 0, behavior: "smooth" }); } catch (_) { window.scrollTo(0, 0); }
  }
}
$$(".sb-item[data-view], .tab[data-view]").forEach((btn) =>
  btn.addEventListener("click", () => showView(btn.dataset.view)));
window.addEventListener("hashchange", () => {
  const name = (location.hash || "#explore").slice(1);
  if (name && document.getElementById("view-" + name)) showView(name, { pushHash: false });
});

$$("#universe-seg button").forEach((btn) =>
  btn.addEventListener("click", () => {
    $$("#universe-seg button").forEach((b) => b.classList.toggle("active", b === btn));
    appState.universe = btn.dataset.universe;
    invalidateViews();
    symbolsLoaded = false;
    currentKey = "";
    $("#symbol-list").innerHTML = "";
    $("#symbol-input").value = "";
    showView(appState.view || "overview", { force: true, pushHash: false });
  }));

/* keyboard: 1-9 tabs, r refresh strip, / focus symbol when on signals */
document.addEventListener("keydown", (e) => {
  const tag = (e.target && e.target.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.target?.isContentEditable) return;
  if (e.key === "/" && appState.view === "signals") {
    e.preventDefault();
    $("#symbol-input")?.focus();
    return;
  }
  if (e.key === "r" && !e.metaKey && !e.ctrlKey) {
    // Force-refresh status strip + current view (clears session view cache).
    invalidateViews();
    loadStatusStrip(true);
    showView(appState.view || "overview", { force: true, pushHash: false });
    return;
  }
  const n = Number(e.key);
  if (n >= 1 && n <= 9 && VIEW_ORDER[n - 1]) {
    e.preventDefault();
    showView(VIEW_ORDER[n - 1]);
  }
});

/* ---------- beginner explore (小白体验) ---------- */
const exploreState = {
  bars: 7 * 96, symbol: "", source: "okx", catalog: null, popular: [], focusId: null,
  showEma: true, showVol: true, showBoxes: true, lastCandles: null, lastEmas: null,
};
let exploreChart = null, exploreSeries = null, exploreVol = null, exploreEmas = [];
let exploreBoxes = [];
let exploreHitRects = [];
let exploreFocusLines = [];
let exploreWired = false;
let exploreTimes = [];
let exploreTimeIndex = new Map(); // time -> bar index for logical coords

// SMA/EMA 20·60·120 — same palette as TG/YOLO notify charts (display only).
const CHART_MA_ORDER = ["sma120", "sma60", "sma20", "ema120", "ema60", "ema20"];
const CHART_MA_STYLE = {
  sma20: { color: "#3d8fd1", lineStyle: 0, lineWidth: 1.2 },
  sma60: { color: "#5cb8b0", lineStyle: 0, lineWidth: 1.1 },
  sma120: { color: "#8a8aaa", lineStyle: 0, lineWidth: 1.0 },
  ema20: { color: "#f06024", lineStyle: 0, lineWidth: 1.2 },
  ema60: { color: "#faa03c", lineStyle: 0, lineWidth: 1.1 },
  ema120: { color: "#c84696", lineStyle: 0, lineWidth: 1.0 },
};

function chartMaMap(payload) {
  return payload?.mas || payload?.emas || {};
}

function addChartMaSeries(chart, payload, sink) {
  const lines = chartMaMap(payload);
  for (const name of CHART_MA_ORDER) {
    const data = lines[name];
    if (!data || !data.length) continue;
    const st = CHART_MA_STYLE[name] || { color: "#666", lineStyle: 0, lineWidth: 1 };
    const s = chart.addLineSeries({
      color: st.color,
      lineWidth: st.lineWidth,
      lineStyle: st.lineStyle,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    s.setData(data);
    sink.push(s);
  }
}

function normalizeSymbol(raw) {
  let symbol = String(raw || "").trim().toUpperCase();
  if (!symbol) return "";
  if (!symbol.includes("_")) symbol = `${symbol}_USDT_SWAP`;
  if (!symbol.includes("USDT")) symbol = symbol.replace("_SWAP", "") + "_USDT_SWAP";
  if (symbol.endsWith("_USDT")) symbol = symbol + "_SWAP";
  return symbol;
}

function parseExploreQuery() {
  const q = new URLSearchParams(location.search);
  const sym = q.get("symbol") || q.get("s");
  const bars = q.get("bars");
  if (sym) exploreState.symbol = normalizeSymbol(sym);
  if (bars && Number(bars) > 0) exploreState.bars = Number(bars);
}

function writeExploreQuery() {
  if (appState.view !== "explore") return;
  const q = new URLSearchParams();
  if (exploreState.symbol) q.set("symbol", exploreState.symbol);
  if (exploreState.bars) q.set("bars", String(exploreState.bars));
  const qs = q.toString();
  history.replaceState(null, "", `${location.pathname}${qs ? "?" + qs : ""}#explore`);
}

function loadRecentSymbols() {
  try { return JSON.parse(localStorage.getItem("fable_explore_recent") || "[]"); }
  catch (_) { return []; }
}
function pushRecentSymbol(sym) {
  if (!sym) return;
  let list = loadRecentSymbols().filter((s) => s !== sym);
  list.unshift(sym);
  list = list.slice(0, 8);
  localStorage.setItem("fable_explore_recent", JSON.stringify(list));
  renderRecentChips();
}
function renderRecentChips() {
  const host = $("#explore-recent");
  if (!host) return;
  const list = loadRecentSymbols();
  if (!list.length) { host.hidden = true; host.innerHTML = ""; return; }
  host.hidden = false;
  host.innerHTML = `<span class="note">最近：</span>` + list.map((s) =>
    `<button type="button" class="chip-btn ${s === exploreState.symbol ? "active" : ""}" data-sym="${escapeHtml(s)}">${escapeHtml(s.replace("_USDT_SWAP","").replace("_USDT",""))}</button>`
  ).join("");
  host.querySelectorAll("button[data-sym]").forEach((b) => b.addEventListener("click", () => {
    exploreState.symbol = b.dataset.sym;
    if ($("#explore-symbol")) try { $("#explore-symbol").value = b.dataset.sym; } catch(_){}
    if ($("#explore-symbol-free")) $("#explore-symbol-free").value = b.dataset.sym;
    runExplore();
  }));
}


async function loadExplore() {
  parseExploreQuery();
  if (!exploreState.catalog) {
    const cat = await apiGet(apiUrl("/api/explore/catalog"), { cache: true });
    exploreState.catalog = cat;
    exploreState.popular = (cat.popular || []).map((r) => r.symbol);
    const howto = $("#explore-howto");
    if (howto) {
      howto.innerHTML = (cat.howto || []).map((s, i) =>
        `<li><b>${i + 1}</b>${escapeHtml(s.replace(/^[①-⑩\d]+[、.．\s]*/, ""))}</li>`
      ).join("");
    }
    const sel = $("#explore-symbol");
    const list = $("#explore-symbol-list");
    const popular = cat.popular || [];
    const all = cat.all || [];
    if (sel) {
      sel.innerHTML = popular.map((r) =>
        `<option value="${escapeHtml(r.symbol)}">${escapeHtml(r.symbol.replace("_USDT_SWAP", "").replace("_USDT", ""))}</option>`
      ).join("") + (all.length ? `<option value="">—— 全部（用输入框）——</option>` : "");
      if (!exploreState.symbol && popular[0]) exploreState.symbol = popular[0].symbol;
      if (exploreState.symbol) {
        try { sel.value = exploreState.symbol; } catch (_) {}
      }
    }
    if (list) list.innerHTML = all.map((r) => `<option value="${escapeHtml(r.symbol)}">`).join("");
    const free = $("#explore-symbol-free");
    if (free && exploreState.symbol) free.value = exploreState.symbol;
    const seg = $("#explore-range-seg");
    if (seg && !seg.dataset.ready) {
      seg.dataset.ready = "1";
      const ranges = cat.ranges || [];
      let activeIdx = ranges.findIndex((r) => r.bars === exploreState.bars);
      if (activeIdx < 0) activeIdx = Math.min(2, ranges.length - 1);
      seg.innerHTML = ranges.map((r, i) =>
        `<button type="button" data-bars="${r.bars}" class="${i === activeIdx ? "active" : ""}">${escapeHtml(r.label)}</button>`
      ).join("");
      if (ranges[activeIdx]) exploreState.bars = ranges[activeIdx].bars;
      seg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
        seg.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
        exploreState.bars = Number(b.dataset.bars);
        runExplore();
      }));
    }
  }
  if (!exploreWired) {
    exploreWired = true;
    $("#explore-go")?.addEventListener("click", () => runExplore());
    $("#explore-fit")?.addEventListener("click", () => {
      exploreState.focusId = null;
      clearExploreFocusLines();
      exploreChart?.timeScale().fitContent();
      applyExploreMarkers();
      highlightExploreRow(null);
    });
    $("#explore-to-signals")?.addEventListener("click", () => {
      const sym = exploreState.symbol;
      if (!sym) { toast("请先画出一个币种"); return; }
      showView("signals", { force: true });
      initSignals(true).then(() => {
        const key = `okx:${sym}`;
        if ($("#symbol-input")) $("#symbol-input").value = key;
        loadChart(key);
      });
    });
    $("#explore-prev")?.addEventListener("click", () => stepPopular(-1));
    $("#explore-next")?.addEventListener("click", () => stepPopular(1));
    $("#explore-random")?.addEventListener("click", () => {
      const all = (exploreState.catalog?.all || []).map((r) => r.symbol);
      const pool = all.length ? all : exploreState.popular;
      if (!pool.length) return;
      exploreState.symbol = pool[Math.floor(Math.random() * pool.length)];
      if ($("#explore-symbol-free")) $("#explore-symbol-free").value = exploreState.symbol;
      if ($("#explore-symbol")) try { $("#explore-symbol").value = exploreState.symbol; } catch(_){}
      runExplore();
    });
    $("#explore-copy")?.addEventListener("click", async () => {
      writeExploreQuery();
      const url = location.href;
      try {
        await navigator.clipboard.writeText(url);
        toast("链接已复制", "ok");
      } catch (_) {
        prompt("复制链接：", url);
      }
    });
    $("#explore-show-ema")?.addEventListener("change", (e) => {
      exploreState.showEma = e.target.checked;
      applyExploreVisibility();
    });
    $("#explore-show-vol")?.addEventListener("change", (e) => {
      exploreState.showVol = e.target.checked;
      applyExploreVisibility();
    });
    $("#explore-show-boxes")?.addEventListener("change", (e) => {
      exploreState.showBoxes = e.target.checked;
      if (!e.target.checked) clearExploreFocusLines();
      applyExploreMarkers();
    });
    $("#overview-go-explore")?.addEventListener("click", () => showView("explore", { force: true }));
    $("#explore-symbol")?.addEventListener("change", (e) => {
      if (e.target.value) {
        exploreState.symbol = e.target.value;
        if ($("#explore-symbol-free")) $("#explore-symbol-free").value = e.target.value;
        runExplore();
      }
    });
    $("#explore-symbol-free")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        const v = e.target.value.trim();
        if (v) exploreState.symbol = normalizeSymbol(v);
        runExplore();
      }
    });
    document.addEventListener("keydown", onExploreKeys);
  }
  renderRecentChips();
  if (exploreState.symbol) await runExplore();
}

function onExploreKeys(e) {
  if (appState.view !== "explore") return;
  const tag = (e.target && e.target.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  if (e.key === "ArrowLeft") { e.preventDefault(); stepPopular(-1); }
  if (e.key === "ArrowRight") { e.preventDefault(); stepPopular(1); }
  if (e.key === "Enter") { e.preventDefault(); runExplore(); }
  if (e.key === "j" || e.key === "J") { e.preventDefault(); stepExploreBox(1); }
  if (e.key === "k" || e.key === "K") { e.preventDefault(); stepExploreBox(-1); }
  if (e.key === "0") {
    e.preventDefault();
    exploreState.focusId = null;
    clearExploreFocusLines();
    exploreChart?.timeScale().fitContent();
    applyExploreMarkers();
    highlightExploreRow(null);
  }
}
function stepExploreBox(delta) {
  if (!exploreBoxes.length) return;
  const ids = exploreBoxes.map((b) => b.id);
  let i = ids.indexOf(exploreState.focusId);
  if (i < 0) i = delta > 0 ? -1 : 0;
  i = (i + delta + ids.length) % ids.length;
  focusExploreBox(ids[i]);
}
function applyExploreVisibility() {
  exploreEmas.forEach((s) => {
    try { s.applyOptions({ visible: exploreState.showEma }); } catch (_) {}
  });
  if (exploreVol) {
    try { exploreVol.applyOptions({ visible: exploreState.showVol }); } catch (_) {}
  }
}

function stepPopular(delta) {
  const list = exploreState.popular || [];
  if (!list.length) return;
  let i = list.indexOf(exploreState.symbol);
  if (i < 0) i = 0;
  i = (i + delta + list.length) % list.length;
  exploreState.symbol = list[i];
  if ($("#explore-symbol")) $("#explore-symbol").value = list[i];
  if ($("#explore-symbol-free")) $("#explore-symbol-free").value = list[i];
  runExplore();
}

function ensureExploreChart() {
  if (exploreChart) return;
  const el = $("#explore-chart");
  if (!el) return;
  exploreChart = makeChart(el);
  exploreSeries = exploreChart.addCandlestickSeries({
    upColor: "#2ECC71", downColor: "#E74C3C", borderVisible: false,
    wickUpColor: "#2ECC71", wickDownColor: "#E74C3C",
  });
  exploreVol = exploreChart.addHistogramSeries({
    priceScaleId: "vol", priceFormat: { type: "volume" },
    priceLineVisible: false, lastValueVisible: false,
  });
  exploreChart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  // Markers follow the series natively; only re-apply focus chrome on range change.
  const redraw = () => applyExploreMarkers();
  exploreChart.timeScale().subscribeVisibleLogicalRangeChange(redraw);
  exploreChart.timeScale().subscribeSizeChange(redraw);
  window.addEventListener("resize", redraw);
  exploreChart.subscribeCrosshairMove((param) => {
    const badge = $("#explore-badge");
    const ohlc = $("#explore-badge-ohlc");
    if (!badge || !ohlc) return;
    if (!param || !param.time || !param.seriesData) {
      badge.hidden = true;
      return;
    }
    const c = param.seriesData.get(exploreSeries);
    if (!c || c.open == null) { badge.hidden = true; return; }
    badge.hidden = false;
    ohlc.textContent = `O ${Number(c.open).toFixed(2)}  H ${Number(c.high).toFixed(2)}  L ${Number(c.low).toFixed(2)}  C ${Number(c.close).toFixed(2)}`;
  });
}

async function runExplore() {
  let symbol = normalizeSymbol(
    ($("#explore-symbol-free")?.value || "").trim()
    || ($("#explore-symbol")?.value || "").trim()
    || exploreState.symbol
  );
  if (!symbol) { toast("请先选择币种"); return; }
  exploreState.symbol = symbol;
  exploreState.focusId = null;
  writeExploreQuery();
  const btn = $("#explore-go");
  if (btn) { btn.disabled = true; btn.textContent = "识别中…"; }
  const prog = $("#explore-progress");
  if (prog) prog.hidden = false;
  $("#explore-meta").innerHTML = `<span>加载 ${escapeHtml(symbol)} …</span>`;
  try {
    ensureExploreChart();
    const d = await apiGet(apiUrl(`/api/explore/chart/okx/${encodeURIComponent(symbol)}`, {
      bars: exploreState.bars,
    }));
    exploreEmas.forEach((s) => exploreChart.removeSeries(s));
    exploreEmas = [];
    exploreTimes = (d.candles || []).map((c) => c.time);
    exploreTimeIndex = new Map(exploreTimes.map((t, i) => [t, i]));
    exploreSeries.setData(d.candles);
    exploreVol.setData(d.candles.map((c) => ({
      time: c.time, value: c.volume,
      color: c.close >= c.open ? "rgba(46,204,113,0.28)" : "rgba(231,76,60,0.28)",
    })));
    addChartMaSeries(exploreChart, d, exploreEmas);
    exploreBoxes = d.dense_boxes || [];
    exploreState.lastCandles = d.candles;
    exploreState.lastEmas = chartMaMap(d);
    clearExploreFocusLines();
    exploreChart.timeScale().fitContent();
    applyExploreVisibility();
    // setTimeout (not only rAF): fitContent/layout may settle after a frame
    requestAnimationFrame(() => applyExploreMarkers());
    setTimeout(() => applyExploreMarkers(), 60);
    setTimeout(() => applyExploreMarkers(), 200);
    pushRecentSymbol(symbol);
    const st = d.stats || {};
    $("#explore-meta").innerHTML = `
      <span><b>${escapeHtml(symbol)}</b></span>
      <span>K 线 <b>${d.n_candles}</b> 根 · 15m</span>
      <span class="chip-dense">密集 ${d.n_boxes}</span>
      <span class="note">宇宙：${escapeHtml(d.universe || appState.universe)}</span>`;
    const statsEl = $("#explore-stats");
    if (statsEl) {
      statsEl.hidden = false;
      statsEl.innerHTML = `
        <div class="tile"><span class="lbl">n_boxes</span><b>${st.n_boxes ?? d.n_boxes ?? 0}</b><small>本窗口</small></div>
        <div class="tile"><span class="lbl">boxes / day</span><b>${st.boxes_per_day ?? "—"}</b><small>个/天</small></div>
        <div class="tile"><span class="lbl">avg bars</span><b>${st.avg_bars ?? "—"}</b><small>根 15m</small></div>
        <div class="tile"><span class="lbl">coverage</span><b>${st.coverage_pct ?? "—"}%</b><small>K 线占比</small></div>`;
    }
    const tip = $("#explore-tip");
    if (tip) { tip.hidden = false; tip.textContent = d.tip || ""; }
    renderExploreBoxList();
    const fit = $("#explore-fit");
    if (fit) fit.hidden = false;
  } catch (err) {
    if (err?.name !== "AbortError") {
      $("#explore-meta").innerHTML = `<span class="neg">加载失败：${escapeHtml(err.message || err)}</span>`;
      toast(`Dense Explore：${err.message || err}`);
    }
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "▶ Visualize / Scan"; }
    const prog2 = $("#explore-progress");
    if (prog2) prog2.hidden = true;
  }
}

function renderExploreBoxList() {
  const tbody = $("#explore-boxes-table tbody");
  const count = $("#explore-box-count");
  if (count) count.textContent = `（${exploreBoxes.length}）`;
  if (!tbody) return;
  if (!exploreBoxes.length) {
    tbody.innerHTML = `<tr class="no-click"><td colspan="4" class="empty-state">本窗口未检出密集段 — 换更长时段或别的币试试</td></tr>`;
    return;
  }
  const rows = exploreBoxes.slice().reverse();
  tbody.innerHTML = rows.map((b) => `
    <tr class="clickable ${exploreState.focusId === b.id ? "focused" : ""}" data-box-id="${b.id}">
      <td>${b.id}</td>
      <td class="note">${escapeHtml(b.start_iso || "")} → ${escapeHtml((b.end_iso || "").slice(5))}</td>
      <td class="num">${b.bars}</td>
      <td class="num" title="full_spread 均值，越小越紧">${b.mean_full_spread != null ? (b.mean_full_spread * 100).toFixed(3) + "%" : "—"}</td>
    </tr>`).join("");
  if (!tbody.dataset.delegated) {
    tbody.dataset.delegated = "1";
    tbody.addEventListener("click", (e) => {
      const tr = e.target.closest("tr[data-box-id]");
      if (!tr) return;
      focusExploreBox(Number(tr.dataset.boxId));
    });
  }
}

function highlightExploreRow(id) {
  $$("#explore-boxes-table tr[data-box-id]").forEach((tr) => {
    tr.classList.toggle("focused", Number(tr.dataset.boxId) === id);
  });
}

function clearExploreFocusLines() {
  if (!exploreSeries) { exploreFocusLines = []; return; }
  exploreFocusLines.forEach((pl) => {
    try { exploreSeries.removePriceLine(pl); } catch (_) {}
  });
  exploreFocusLines = [];
}

/** Hummingbot-style markers on candle series (no external canvas overlay). */
function applyExploreMarkers() {
  if (!exploreSeries) return;
  if (!exploreState.showBoxes || !exploreBoxes.length) {
    try { exploreSeries.setMarkers([]); } catch (_) {}
    return;
  }
  const markers = exploreBoxes.map((b) => {
    const focused = exploreState.focusId === b.id;
    return {
      time: b.t0,
      position: "belowBar",
      color: focused ? "#fbbf24" : "#1E90FF",
      shape: focused ? "arrowUp" : "arrowUp",
      text: focused ? `#${b.id}` : String(b.id),
    };
  });
  // LWC requires markers sorted by time ascending
  markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
  try { exploreSeries.setMarkers(markers); } catch (_) {}
}

function focusExploreBox(id) {
  const b = exploreBoxes.find((x) => x.id === id);
  if (!b || !exploreChart || !exploreSeries) return;
  exploreState.focusId = id;
  highlightExploreRow(id);
  applyExploreMarkers();
  clearExploreFocusLines();
  // Focus band: hi/lo price lines (HB uses markers; we add range chrome when selected)
  try {
    exploreFocusLines.push(exploreSeries.createPriceLine({
      price: b.hi, color: "rgba(251,191,36,0.75)", lineWidth: 1, lineStyle: 2, title: `#${id} hi`,
    }));
    exploreFocusLines.push(exploreSeries.createPriceLine({
      price: b.lo, color: "rgba(251,191,36,0.75)", lineWidth: 1, lineStyle: 2, title: `#${id} lo`,
    }));
  } catch (_) {}
  const pad = 24 * 900;
  const from = b.t0 - pad;
  const to = b.t1 + pad;
  let i0 = exploreTimes.findIndex((t) => t >= from);
  let i1 = exploreTimes.findIndex((t) => t >= to);
  if (i0 < 0) i0 = 0;
  if (i1 < 0) i1 = exploreTimes.length - 1;
  exploreChart.timeScale().setVisibleLogicalRange({ from: Math.max(0, i0 - 2), to: i1 + 2 });
}

/** Keep name for callers that still invoke drawExploreBoxes after fit/toggle. */
function drawExploreBoxes() {
  applyExploreMarkers();
}

/* ---------- generic horizontal bars ---------- */
function renderHBars(el, rows) {
  if (!rows.length) {
    el.innerHTML = `<div class="empty-state">暂无数据</div>`;
    return;
  }
  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.value)), 1e-9);
  el.innerHTML = rows.map((r) => {
    const w = (50 * Math.abs(r.value)) / maxAbs;
    const style = r.value >= 0 ? `left:50%;width:${w}%` : `right:50%;width:${w}%`;
    return `<div class="hbar">
      <span class="lbl" title="${r.label}">${r.label}</span>
      <span class="track"><span class="fill ${cls(r.value)}" style="${style}"></span></span>
      <span class="val ${cls(r.value)}">${r.text}</span>
    </div>`;
  }).join("");
}

/* ---------- status strip (owner detector / forward / scout) ---------- */
async function loadStatusStrip(force = false) {
  if (force) {
    for (const [k] of [..._jsonCache.keys()]) {
      if (k.includes("/api/status-strip")) _jsonCache.delete(k);
    }
  }
  try {
    const d = await apiGet("/api/status-strip", { cache: !force, quiet: true });
    const od = d.owner_detector || {};
    const ja = d.judgment_active || {};
    const fw = d.forward || {};
    const sc = d.scout || {};
    const ownerEl = $("#status-owner");
    const judEl = $("#status-judgment");
    const fwdEl = $("#status-forward");
    const scoutEl = $("#status-scout");
    if (ownerEl) {
      ownerEl.classList.remove("skeleton");
      const f1 = od.frozen_eval_f1 != null ? Number(od.frozen_eval_f1).toFixed(3) : "—";
      const p = od.precision != null ? Number(od.precision).toFixed(2) : "—";
      ownerEl.classList.toggle("good", (od.frozen_eval_f1 || 0) >= 0.6);
      ownerEl.classList.toggle("warn", (od.frozen_eval_f1 || 0) > 0 && od.frozen_eval_f1 < 0.6);
      ownerEl.innerHTML = `<span class="status-k">检测器</span>
        <span class="status-v">F1 ${f1} · P ${p}</span>
        <span class="status-sub">${escapeHtml(od.source_run || "—")} · 冻结评测</span>`;
    }
    if (judEl) {
      judEl.classList.remove("skeleton");
      const thr = ja.threshold_val_q90 != null ? Number(ja.threshold_val_q90).toFixed(4) : "—";
      const shortId = ja.artifact_id
        ? String(ja.artifact_id).replace(/^frozen_/, "").replace(/_\d{8}$/, "")
        : "—";
      judEl.classList.toggle("good", !!ja.exists && ja.threshold_val_q90 != null);
      judEl.classList.toggle("warn", !ja.exists);
      judEl.innerHTML = `<span class="status-k">判断 ACTIVE</span>
        <span class="status-v">阈值 ${thr}</span>
        <span class="status-sub" title="${escapeHtml(ja.artifact_id || "")}">${escapeHtml(shortId)} · ${escapeHtml(ja.dataset_name || ja.note || "—")}</span>`;
    }
    if (fwdEl) {
      fwdEl.classList.remove("skeleton");
      const prog = Math.round(100 * (fw.progress || 0));
      fwdEl.classList.toggle("good", (fw.decision_trades || 0) >= (fw.decision_target || 100));
      fwdEl.innerHTML = `<span class="status-k">前向闸门</span>
        <span class="status-v">${fw.decision_trades ?? 0} / ${fw.decision_target ?? 100}（${prog}%）</span>
        <span class="status-sub">closed ${fw.closed_rows ?? 0} · 剩余 ${fw.decision_remaining ?? "—"}</span>`;
    }
    if (scoutEl) {
      scoutEl.classList.remove("skeleton");
      const when = sc.latest_mtime ? sc.latest_mtime.slice(5, 16).replace("T", " ") : "—";
      const sym = escapeHtml(sc.latest_symbol || "打开哨兵");
      scoutEl.innerHTML = `<span class="status-k">视觉侦察</span>
        <span class="status-v"><a href="${sc.href || "/scout.html"}" target="_blank" rel="noopener">${sym} ↗</a></span>
        <span class="status-sub">${sc.n_frames ?? 0} 帧 · ${when}</span>`;
    }
    if (force) toast("状态条已刷新", "ok");
  } catch (_) {
    ["status-owner", "status-judgment", "status-forward", "status-scout"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) {
        el.classList.remove("skeleton");
        const v = el.querySelector(".status-v");
        if (v) v.textContent = "暂不可用";
      }
    });
  }
}

/* ---------- overview ---------- */
let sparkChart = null, sparkSeries = null;
async function loadOverview() {
  $("#view-overview")?.classList.add("loading");
  try {
    const d = await apiGet(apiUrl("/api/overview"), { cache: true });
  $("#verdict-banner").innerHTML = `<b>${d.verdict}</b> ${d.next}`;
  $("#stages").innerHTML = d.stages.map((s) => `
    <div class="stage">
      <h3>${s.name} <span class="chip ${s.status}">${
        { done: "已完成", passed: "验收通过", failed: "未通过" }[s.status] || s.status
      }</span></h3>
      <p>${s.summary}</p>
    </div>`).join("");
  const tile = (t) => `<div class="tile"><span class="lbl">${t.label}</span><b>${t.value}</b><small>${t.sub}</small></div>`;
  $("#tiles").innerHTML = d.tiles.map(tile).join("");
  $("#coverage").innerHTML = d.coverage.map(tile).join("");
  const names = {
    net_positive: "扣费后净收益为正",
    "profit_factor_ge_1.3": "盈亏比 PF ≥ 1.3",
    max_drawdown_le_20pct: "最大回撤 ≤ 20%",
    n_trades_ge_100: "交易数 ≥ 100 笔",
  };
  $("#acceptance").innerHTML = Object.entries(d.acceptance)
    .map(([k, ok]) => `<li class="${ok ? "ok" : "fail"}">${names[k] || k}</li>`).join("");
  if (!sparkChart) {
    sparkChart = makeChart($("#spark-chart"), { timeScale: { visible: true, borderColor: "#2e3340" } });
    sparkSeries = sparkChart.addAreaSeries({
      lineColor: "#3987e5", lineWidth: 2, priceFormat: pctFormat,
      topColor: "rgba(57,135,229,0.25)", bottomColor: "rgba(57,135,229,0.02)",
    });
  }
  sparkSeries.setData(d.sparkline);
  sparkChart.timeScale().fitContent();
  } catch (err) {
    if (err?.name !== "AbortError") {
      $("#verdict-banner").innerHTML = `<b>总览加载失败</b> ${err.message || err}`;
    }
  } finally {
    $("#view-overview")?.classList.remove("loading");
  }
}

async function loadEthMicro() {
  const view = $("#view-ethmicro");
  view?.classList.add("loading");
  try {
    const d = await apiGet("/api/eth-micro", { cache: true });
    $("#ethmicro-note").textContent = d.note || "";
    const best = d.best_bar_by_top_net || "—";
    const mon = d.monitor || {};
    const nSig = (d.recent_signals || []).length;
    $("#ethmicro-tiles").innerHTML = `
      <div class="tile"><span class="lbl">品种</span><b>${escapeHtml(d.symbol || "ETH_USDT_SWAP")}</b><small>独立通道</small></div>
      <div class="tile"><span class="lbl">回测最优 bar</span><b>${escapeHtml(best)}</b><small>按 top 净@0.2%</small></div>
      <div class="tile"><span class="lbl">监控最近</span><b>${escapeHtml((mon.ts || "—").toString().slice(0, 19))}</b><small>新信号 ${mon.new_signals ?? "—"}</small></div>
      <div class="tile"><span class="lbl">信号日志</span><b>${nSig}</b><small>最近最多 50 条</small></div>`;
    const tbody = $("#ethmicro-bt-table tbody");
    if (tbody) {
      tbody.innerHTML = (d.backtest_table || []).map((r) => {
        if (r.status !== "ok") {
          return `<tr><td>${escapeHtml(r.bar)}</td><td>${escapeHtml(r.status)}</td><td class="num">${r.n_candidates ?? "—"}</td>
            <td class="num" colspan="8">—</td></tr>`;
        }
        return `<tr>
          <td><b>${escapeHtml(r.bar)}</b></td>
          <td class="pos">ok</td>
          <td class="num">${r.n_candidates ?? "—"}</td>
          <td class="num">${r.n_val ?? "—"}</td>
          <td class="num">${r.val_auc != null ? Number(r.val_auc).toFixed(3) : "—"}</td>
          <td class="num ${cls(r.top_net_0p2)}">${r.top_net_0p2 != null ? fmtPct(r.top_net_0p2, 3) : "—"}</td>
          <td class="num">${r.accept_n ?? "—"}</td>
          <td class="num ${r.accept_pf >= 1.3 ? "pos" : "neg"}">${fmtPF(r.accept_pf)}</td>
          <td class="num ${cls(r.accept_net_cap)}">${fmtPct(r.accept_net_cap)}</td>
          <td class="num">${r.full_n ?? "—"}</td>
          <td class="num">${fmtPF(r.full_pf)}</td>
        </tr>`;
      }).join("") || `<tr><td colspan="11">尚无回测产物，请跑 scripts/eth_micro_backtest.py</td></tr>`;
    }
    $("#ethmicro-monitor").textContent = mon && Object.keys(mon).length
      ? JSON.stringify(mon, null, 2)
      : "监控尚未运行。PYTHONPATH=. python3 scripts/eth_micro_monitor.py --loop";
    const sigBody = $("#ethmicro-sig-table tbody");
    $("#ethmicro-sig-count").textContent = `（${nSig}）`;
    if (sigBody) {
      sigBody.innerHTML = (d.recent_signals || []).map((s) => `<tr>
        <td>${escapeHtml(String(s.signal_time || "").slice(0, 19))}</td>
        <td>${escapeHtml(s.bar)}</td>
        <td class="num">${s.entry_price != null ? Number(s.entry_price).toFixed(2) : "—"}</td>
        <td class="num">${s.score != null ? Number(s.score).toFixed(4) : "—"}</td>
        <td class="num">${s.tp_price != null ? Number(s.tp_price).toFixed(2) : "—"}</td>
        <td class="num">${s.sl_price != null ? Number(s.sl_price).toFixed(2) : "—"}</td>
        <td>${escapeHtml(String(s.notified_at || "").slice(0, 19))}</td>
      </tr>`).join("") || `<tr><td colspan="7">暂无实时信号</td></tr>`;
    }
  } catch (err) {
    if (err?.name !== "AbortError") toast(`ETH Micro：${err.message || err}`);
  } finally {
    view?.classList.remove("loading");
  }
}

let forwardChart, forwardSeries, forwardDdChart, forwardDdSeries;
async function loadForward() {
  $("#view-forward").classList.add("loading");
  try {
  const d = await apiGet("/api/forward", { cache: true });
  const m = d.metrics;
  $("#forward-tiles").innerHTML = `
    <div class="tile"><span class="lbl">裁决样本</span><b>${d.decision_trades}</b><small>maker-filled closed / ${d.decision_target}</small></div>
    <div class="tile"><span class="lbl">前向 PF</span><b class="${m.profit_factor >= 1.3 ? "pos" : m.profit_factor === null ? "" : "neg"}">${fmtPF(m.profit_factor)}</b><small>${d.cost_label}</small></div>
    <div class="tile"><span class="lbl">胜率</span><b>${fmtPct(m.win_rate, 1)}</b><small>仅已成交闭合样本</small></div>
    <div class="tile"><span class="lbl">净收益（对资金）</span><b class="${cls(m.net_return_on_capital)}">${fmtPct(m.net_return_on_capital)}</b><small>累计 ${fmtPct(m.total_net_units, 2)} 名义</small></div>`;
  $("#forward-progress").style.width = `${Math.round(100 * d.progress)}%`;
  $("#forward-progress-label").textContent = `${d.decision_trades} / ${d.decision_target}`;
  $("#forward-progress-note").textContent =
    d.decision_remaining > 0 ? `距裁决线还差 ${d.decision_remaining} 笔；日志 ${d.total_rows} 条，open ${d.open_rows} 条` : "已达到裁决样本线";
  $("#forward-count").textContent = `（${d.total_rows} 条；closed ${d.closed_rows}）`;

  if (!forwardChart) {
    forwardChart = makeChart($("#forward-chart"));
    forwardSeries = forwardChart.addAreaSeries({
      lineColor: "#3987e5", lineWidth: 2, priceFormat: pctFormat,
      topColor: "rgba(57,135,229,0.25)", bottomColor: "rgba(57,135,229,0.02)",
    });
    forwardDdChart = makeChart($("#forward-dd-chart"), { timeScale: { visible: false } });
    forwardDdSeries = forwardDdChart.addAreaSeries({
      lineColor: "#e66767", lineWidth: 1, priceFormat: pctFormat,
      topColor: "rgba(230,103,103,0.02)", bottomColor: "rgba(230,103,103,0.3)",
      invertFilledArea: true,
    });
  }
  forwardSeries.setData(d.equity);
  forwardDdSeries.setData(d.drawdown);
  forwardChart.timeScale().fitContent();
  forwardDdChart.timeScale().fitContent();
  renderHBars($("#forward-outcomes"), d.outcomes.map((r) => ({
    label: OUTCOME_CN[r.label] || r.label,
    value: r.value,
    text: `${r.value.toFixed(2)}%·${r.text}`,
  })));
  $("#forward-table tbody").innerHTML = d.rows.length ? d.rows.map((r) => `
    <tr>
      <td>${String(r.signal_time || "").slice(0, 16).replace("T", " ")}</td>
      <td>${r.symbol || ""}</td>
      <td>${STATUS_CN[r.status] || r.status || ""}</td>
      <td>${r.maker_filled ? "filled" : "miss"}</td>
      <td class="outcome-${r.outcome || "open"}">${OUTCOME_CN[r.outcome || ""] || r.outcome || ""}</td>
      <td class="num">${r.score === null ? "—" : Number(r.score).toFixed(3)}</td>
      <td class="num"><span class="${cls(r.net_ret)}">${fmtPct(r.net_ret)}</span></td>
    </tr>`).join("") : `<tr class="no-click"><td colspan="7" class="empty-state">暂无前向信号</td></tr>`;
  } catch (err) {
    if (err?.name !== "AbortError") toast(`前向页：${err.message || err}`);
  } finally {
    $("#view-forward")?.classList.remove("loading");
  }
}

/* ---------- backtest ---------- */
const btState = { cost: 0.003, window: "accept", outcome: "", filter: "", scoreMin: 0, sort: "entry_time", dir: -1 };
let equityChart, equitySeries, ddChart, ddSeries, pfChart, pfSeries, pfLine;
let tradeRows = [];
const TRADES_PAGE = 120;
let tradesShow = TRADES_PAGE;

function segWire(id, state, key, parse, cb) {
  $(id).querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    $(id).querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    state[key] = parse(b.dataset[key]);
    cb();
  }));
}
segWire("#cost-seg", btState, "cost", Number, loadBacktest);
segWire("#window-seg", btState, "window", String, loadBacktest);
segWire("#outcome-seg", btState, "outcome", String, () => { tradesShow = TRADES_PAGE; renderTrades(); });
$("#trade-filter").addEventListener("input", (e) => {
  btState.filter = e.target.value.toUpperCase();
  tradesShow = TRADES_PAGE;
  renderTrades();
});
$("#score-threshold").addEventListener("input", (e) => {
  btState.scoreMin = Number(e.target.value);
  $("#score-threshold-label").textContent = btState.scoreMin.toFixed(3);
  tradesShow = TRADES_PAGE;
  renderTrades();
});
document.querySelectorAll("#trades-table th.sortable").forEach((th) =>
  th.addEventListener("click", () => {
    const k = th.dataset.sort;
    btState.dir = btState.sort === k ? -btState.dir : -1;
    btState.sort = k;
    document.querySelectorAll("#trades-table th.sortable").forEach((h) =>
      h.textContent = h.textContent.replace(/ [↓↑]$/, "") + (h === th ? (btState.dir < 0 ? " ↓" : " ↑") : ""));
    renderTrades();
  }));

function renderBacktestCompare(cmp) {
  const panel = $("#bt-compare-panel");
  const note = $("#bt-compare-note");
  const tbody = $("#bt-compare-table tbody");
  if (!panel || !tbody) return;
  if (!cmp || !cmp.available) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  panel.classList.toggle("stale-panel", !!cmp.stale);
  if (note) {
    note.hidden = false;
    note.classList.toggle("warn-banner", !!cmp.stale);
    note.classList.toggle("note", !cmp.stale);
    if (cmp.stale) {
      const live = cmp.live_active || {};
      const liveBits = [
        live.artifact_id ? `当前 ACTIVE=${live.artifact_id}` : null,
        live.threshold_val_q90 != null ? `阈值=${Number(live.threshold_val_q90).toFixed(4)}` : null,
        live.dataset_name ? `池=${live.dataset_name}` : null,
      ].filter(Boolean).join(" · ");
      note.innerHTML = `<b>对照表已过期</b> — 以下数字不是当前主线。${liveBits ? `<br><span class="status-sub">${escapeHtml(liveBits)}</span>` : ""}`
        + (cmp.stale_reasons || []).map((r) => `<br>· ${escapeHtml(r)}`).join("")
        + `<br>请以总览/动态回测（本页上方磁贴）为准。`;
    } else {
      note.textContent = cmp.note || "ACTIVE=回归；SHADOW=二分类。验收窗已消耗，仅对照。";
    }
  }
  tbody.innerHTML = (cmp.rows || []).map((r) => {
    const a = r.accept || {};
    const f = r.full || {};
    const ok100 = (r.acceptance_check || {}).n_trades_ge_100;
    const thr = r.threshold == null ? "—" : (Math.abs(r.threshold) < 0.05 ? Number(r.threshold).toFixed(4) : Number(r.threshold).toFixed(3));
    const roleLabel = cmp.stale && r.role === "ACTIVE" ? "旧ACTIVE" : r.role;
    const roleChip = r.role === "ACTIVE" ? (cmp.stale ? "chip warn" : "chip passed") : r.role === "SHADOW" ? "chip done" : "chip";
    return `<tr class="${cmp.stale ? "row-stale" : ""}">
      <td><span class="${roleChip}">${escapeHtml(roleLabel)}</span> ${escapeHtml(r.label || r.key)}</td>
      <td>${escapeHtml(r.objective || "—")}</td>
      <td class="num">${thr}</td>
      <td class="num">${r.n_eligible ?? "—"}</td>
      <td class="num"><b>${a.n_trades ?? "—"}</b></td>
      <td class="num ${cls(a.net_return_on_capital)}">${fmtPct(a.net_return_on_capital)}</td>
      <td class="num ${a.profit_factor >= 1.3 ? "pos" : "neg"}">${fmtPF(a.profit_factor)}</td>
      <td class="num">${fmtPct(a.win_rate)}</td>
      <td class="num">${f.n_trades ?? "—"}</td>
      <td class="num ${cls(f.net_return_on_capital)}">${fmtPct(f.net_return_on_capital)}</td>
      <td class="num">${fmtPF(f.profit_factor)}</td>
      <td class="${ok100 ? "pos" : "neg"}">${ok100 ? "✓" : "✗"}</td>
    </tr>`;
  }).join("");
}

async function loadBacktest() {
  $("#view-backtest").classList.add("loading");
  try {
  const [d, rows, cmp] = await Promise.all([
    apiGet(apiUrl("/api/backtest", { cost: btState.cost }), { cache: true }),
    apiGet(apiUrl("/api/trades", { window: btState.window, cost: btState.cost }), { cache: true }),
    apiGet(`/api/backtest/compare?cost=${btState.cost}`, { cache: true }).catch(() => ({ available: false })),
  ]);
  tradeRows = rows;
  tradesShow = TRADES_PAGE;
  const w = d[btState.window];
  renderBacktestCompare(cmp);

  // regression scores are not probabilities — stretch the filter slider to score range
  const slider = $("#score-threshold");
  if (slider && d.score_range) {
    const lo = Number(d.score_range.min);
    const hi = Number(d.score_range.max);
    if (Number.isFinite(lo) && Number.isFinite(hi) && hi > lo) {
      const step = Math.max((hi - lo) / 200, 1e-5);
      slider.min = String(lo);
      slider.max = String(hi);
      slider.step = String(step);
      if (btState.scoreMin < lo || btState.scoreMin > hi) {
        btState.scoreMin = lo;
        slider.value = String(lo);
        $("#score-threshold-label").textContent = lo.toFixed(4);
      }
    }
  }
  const thrNote = d.score_semantics === "predicted_realized_ret"
    ? `ACTIVE 回归 · 阈值 ${d.score_threshold != null ? Number(d.score_threshold).toFixed(4) : "—"}（预测收益 q90）`
    : null;
  if (thrNote && $("#threshold-note")) {
    $("#threshold-note").textContent = thrNote + "；滑块只过滤明细。";
  }

  $("#bt-tiles").innerHTML = `
    <div class="tile"><span class="lbl">交易笔数</span><b>${w.n_trades}</b><small>${escapeHtml(d.universe_label)} · ${btState.window === "accept" ? "验收窗口" : "全期"}</small></div>
    <div class="tile"><span class="lbl">净收益（对资金）</span><b class="${cls(w.net_return_on_capital)}">${fmtPct(w.net_return_on_capital)}</b><small>单笔均值 ${fmtPct(w.mean_net_per_trade, 3)}</small></div>
    <div class="tile"><span class="lbl">盈亏比 PF</span><b class="${w.profit_factor >= 1.3 ? "pos" : "neg"}">${fmtPF(w.profit_factor)}</b><small>验收线 1.3</small></div>
    <div class="tile"><span class="lbl">最大回撤 / 胜率</span><b>${fmtPct(w.max_drawdown_pct)}</b><small>胜率 ${fmtPct(w.win_rate)}</small></div>`;

  if (!equityChart) {
    equityChart = makeChart($("#equity-chart"));
    equitySeries = equityChart.addAreaSeries({
      lineColor: "#3987e5", lineWidth: 2, priceFormat: pctFormat,
      topColor: "rgba(57,135,229,0.25)", bottomColor: "rgba(57,135,229,0.02)",
    });
    ddChart = makeChart($("#dd-chart"), { timeScale: { visible: false } });
    ddSeries = ddChart.addAreaSeries({
      lineColor: "#e66767", lineWidth: 1, priceFormat: pctFormat,
      topColor: "rgba(230,103,103,0.02)", bottomColor: "rgba(230,103,103,0.3)",
      invertFilledArea: true,
    });
  }
  equitySeries.setData(w.equity); equityChart.timeScale().fitContent();
  ddSeries.setData(w.drawdown); ddChart.timeScale().fitContent();

  renderHBars($("#decile-bars"), w.decile.map((r) => ({
    label: `D${r.decile}${r.decile === 10 ? "（最高分）" : ""}`,
    value: r.mean_net, text: r.mean_net.toFixed(2) + "%",
  })).reverse());

  if (!pfChart) {
    pfChart = makeChart($("#pf-chart"), { timeScale: { visible: false } });
    pfSeries = pfChart.addLineSeries({
      color: "#3987e5", lineWidth: 2,
      priceFormat: { type: "custom", formatter: (v) => v.toFixed(2) },
    });
    pfLine = pfSeries.createPriceLine({ price: 1.3, color: "#9aa0a8", lineStyle: 2, title: "验收线" });
  }
  // x 轴借用 time 槽位表示成本（0.10% -> 0.50%），仅作形状展示
  pfSeries.setData(d.pf_curve.map((p, i) => ({ time: 1700000000 + i * 86400, value: p.pf })));
  pfChart.timeScale().fitContent();

  renderHBars($("#monthly-bars"), w.monthly.map((r) => ({
    label: r.month, value: r.value, text: r.value.toFixed(2) + "%",
  })));
  const sym = [...w.per_symbol.best, ...w.per_symbol.worst.slice().reverse()];
  renderHBars($("#symbol-bars"), sym.map((r) => ({
    label: r.symbol.replace("_USDT_SWAP", "").replace("_USDT", ""), value: r.net, text: `${r.net.toFixed(1)}%·${r.n}笔`,
  })));

  renderTrades();
  } catch (err) {
    if (err?.name !== "AbortError") toast(`回测页：${err.message || err}`);
  } finally {
    $("#view-backtest")?.classList.remove("loading");
  }
}

function filteredTradeRows() {
  let rows = tradeRows;
  if (btState.outcome) rows = rows.filter((r) => r.outcome.startsWith(btState.outcome));
  if (btState.filter) rows = rows.filter((r) => r.symbol.includes(btState.filter));
  if (btState.scoreMin > 0) rows = rows.filter((r) => r.score >= btState.scoreMin);
  return rows.slice().sort((a, b) => {
    const va = a[btState.sort], vb = b[btState.sort];
    return (va < vb ? -1 : va > vb ? 1 : 0) * btState.dir;
  });
}

function renderTrades() {
  const beforeScoreFilter = (() => {
    let r = tradeRows;
    if (btState.outcome) r = r.filter((x) => x.outcome.startsWith(btState.outcome));
    if (btState.filter) r = r.filter((x) => x.symbol.includes(btState.filter));
    return r.length;
  })();
  const rows = filteredTradeRows();
  $("#trades-count").textContent =
    btState.scoreMin > 0 ? `（${rows.length}/${beforeScoreFilter} 笔）` : `（${rows.length} 笔）`;
  $("#threshold-note").textContent =
    btState.scoreMin > 0 ? "只过滤下方成交明细，不改变净值/PF 或验收结论。" : "只过滤下方成交明细，不改变净值/PF。";
  const tbody = $("#trades-table tbody");
  const shown = rows.slice(0, tradesShow);
  tbody.innerHTML = shown.map((r, i) => `
    <tr data-i="${i}" data-source="${escapeHtml(r.source)}" data-symbol="${escapeHtml(r.symbol)}" data-entry="${escapeHtml(r.entry_time)}">
      <td>${escapeHtml(String(r.entry_time).slice(0, 16))}</td>
      <td>${escapeHtml(r.symbol)}</td>
      <td class="num">${r.score.toFixed(3)}</td>
      <td class="outcome-${escapeHtml(r.outcome)}">${OUTCOME_CN[r.outcome] || escapeHtml(r.outcome)}</td>
      <td class="num"><span class="${cls(r.gross_ret)}">${fmtPct(r.gross_ret)}</span></td>
      <td class="num"><span class="${cls(r.net_ret)}">${fmtPct(r.net_ret)}</span></td>
    </tr>`).join("");
  const more = $("#trades-more");
  if (more) {
    const left = rows.length - shown.length;
    more.hidden = left <= 0;
    more.textContent = left > 0 ? `再显示 ${Math.min(TRADES_PAGE, left)} 笔（剩余 ${left}）` : "";
  }
  if (!tbody.dataset.delegated) {
    tbody.dataset.delegated = "1";
    tbody.addEventListener("click", (e) => {
      const tr = e.target.closest("tr[data-entry]");
      if (!tr) return;
      focusTrade(tr.dataset.source, tr.dataset.symbol, tr.dataset.entry);
    });
  }
}

$("#trades-more")?.addEventListener("click", () => {
  tradesShow += TRADES_PAGE;
  renderTrades();
});

/* ---------- signals browser ---------- */
let symbolsLoaded = false, klineChart, klineSeries, volumeSeries, emaSeries = [];
let bandSeries, pathSeries, barrier = { tp: 4, sl: 2 };
let currentKey = "", currentMarkers = [], currentTimes = [], priceLines = [], chartReq = 0;
let currentThreshold = 0;
let lastFocusRange = null;
let symbolInputWired = false;
const sigState = { bars: 1500, showMa: true };
segWire("#bars-seg", sigState, "bars", Number, () => currentKey && loadChart(currentKey));
$("#signals-show-ma")?.addEventListener("change", (e) => {
  sigState.showMa = !!e.target.checked;
  emaSeries.forEach((s) => s.applyOptions({ visible: sigState.showMa }));
});

function ensureKlineChart() {
  if (klineChart) return;
  klineChart = makeChart($("#kline-chart"));
  // autoSize's first real layout resets the view; replay the focus position
  klineChart.timeScale().subscribeSizeChange(() => {
    if (lastFocusRange) klineChart.timeScale().setVisibleLogicalRange(lastFocusRange);
  });
  // full-height translucent band marking the dense-MA window of the focused trade
  bandSeries = klineChart.addAreaSeries({
    priceScaleId: "band", lineVisible: false, priceLineVisible: false,
    lastValueVisible: false, crosshairMarkerVisible: false,
    autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }),
    topColor: "rgba(57,135,229,0.14)", bottomColor: "rgba(57,135,229,0.14)",
  });
  klineChart.priceScale("band").applyOptions({ visible: false, scaleMargins: { top: 0, bottom: 0 } });
  klineSeries = klineChart.addCandlestickSeries({
    upColor: "#34d399", downColor: "#f87171", borderVisible: false,
    wickUpColor: "#34d399", wickDownColor: "#f87171",
  });
  volumeSeries = klineChart.addHistogramSeries({
    priceScaleId: "vol", priceFormat: { type: "volume" },
    priceLineVisible: false, lastValueVisible: false,
  });
  klineChart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  // entry->exit path segment of the focused trade
  pathSeries = klineChart.addLineSeries({
    lineWidth: 3, priceLineVisible: false, lastValueVisible: false,
    crosshairMarkerVisible: false, autoscaleInfoProvider: () => null,
  });
}

async function initSignals(force = false) {
  if (symbolsLoaded && !force) return;
  symbolsLoaded = true;
  let rows;
  try {
    rows = await apiGet(apiUrl("/api/symbols"), { cache: true });
  } catch (err) {
    $("#symbol-info").textContent = "币种列表加载失败";
    return;
  }
  $("#symbol-list").innerHTML = rows.map((r) =>
    `<option value="${r.source}:${r.symbol}">${r.symbol}（成交 ${r.n_trades} / 合格 ${r.n_eligible}）</option>`).join("");
  if (!symbolInputWired) {
    let t = null;
    const input = $("#symbol-input");
    input.addEventListener("change", () => loadChart(input.value));
    input.addEventListener("input", () => {
      clearTimeout(t);
      t = setTimeout(() => {
        const v = input.value.trim();
        if (v.includes(":") || v.includes("_USDT")) loadChart(v.includes(":") ? v : `okx:${v}`);
      }, 350);
    });
    symbolInputWired = true;
  }
  const first = rows.find((r) => r.n_trades > 0) || rows[0];
  if (first && !currentKey) {
    $("#symbol-input").value = `${first.source}:${first.symbol}`;
    loadChart($("#symbol-input").value);
  }
}



async function loadChart(key, focusEntry = null) {
  const [source, symbol] = key.split(":");
  if (!symbol) return;
  currentKey = key;
  ensureKlineChart();          // synchronous: no await between check and create
  const reqId = ++chartReq;    // stale responses (slow links) are dropped
  if (chartAbort) chartAbort.abort();
  chartAbort = new AbortController();
  $("#view-signals").classList.add("loading");
  let d;
  try {
    d = await apiGet(apiUrl(`/api/chart/${source}/${symbol}`, { bars: sigState.bars }), {
      signal: chartAbort.signal,
    });
  } catch (err) {
    if (err?.name === "AbortError" || reqId !== chartReq) return;
    $("#symbol-info").textContent = "找不到该序列或加载失败";
    $("#view-signals").classList.remove("loading");
    return;
  }
  $("#view-signals").classList.remove("loading");
  if (reqId !== chartReq) return;
  barrier = { tp: d.tp_mult, sl: d.sl_mult };
  currentThreshold = d.threshold;

  priceLines.forEach((l) => klineSeries.removePriceLine(l)); priceLines = [];
  emaSeries.forEach((s) => klineChart.removeSeries(s)); emaSeries = [];
  bandSeries.setData([]); pathSeries.setData([]);
  currentTimes = d.candles.map((c) => c.time);
  klineSeries.setData(d.candles);
  volumeSeries.setData(d.candles.map((c) => ({
    time: c.time, value: c.volume,
    color: c.close >= c.open ? "rgba(31,167,125,0.35)" : "rgba(230,103,103,0.35)",
  })));
  addChartMaSeries(klineChart, d, emaSeries);
  emaSeries.forEach((s) => s.applyOptions({ visible: sigState.showMa }));
  currentMarkers = d.markers;
  const markerList = [];
  for (const m of d.markers) {
    if (!m.eligible && !m.traded) continue;
    markerList.push({
      time: m.time, position: "belowBar",
      shape: m.traded ? "arrowUp" : "circle",
      color: m.traded ? (OUTCOME_COLOR[m.outcome] || "#8b93a1") : "#8b93a1",
      text: m.traded ? `${(100 * m.ret).toFixed(1)}%` : "",
      size: m.traded ? 2 : 1,
    });
    if (m.traded) markerList.push({  // exit marker: start->end is visible at a glance
      time: m.exit_time, position: "aboveBar", shape: "square",
      color: OUTCOME_COLOR[m.outcome] || "#8b93a1", size: 1,
    });
  }
  klineSeries.setMarkers(markerList.sort((a, b) => a.time - b.time));
  klineChart.timeScale().fitContent();

  const n = d.markers.length, el = d.markers.filter((m) => m.eligible).length,
    tr = d.markers.filter((m) => m.traded).length;
  $("#symbol-info").textContent =
    `${symbol}：窗口内候选 ${n}，合格（≥${d.threshold}）${el}，成交 ${tr}`;

  const traded = d.markers.filter((m) => m.traded).sort((a, b) => b.time - a.time);
  const missed = d.markers.filter((m) => m.eligible && !m.traded).sort((a, b) => b.time - a.time);
  $("#side-count").textContent = `（${traded.length} 笔）`;
  $("#symbol-trades").innerHTML = "<tbody>" + (traded.length ? traded.map((m) => `
    <tr data-entry-ts="${m.entry_time}">
      <td>${new Date(m.time * 1000).toISOString().slice(5, 16).replace("T", " ")}</td>
      <td class="outcome-${m.outcome}">${OUTCOME_CN[m.outcome] || m.outcome}</td>
      <td class="num"><span class="${cls(m.ret)}">${fmtPct(m.ret, 1)}</span></td>
    </tr>`).join("") : `<tr class="no-click"><td colspan="3" class="empty-state">暂无成交</td></tr>`) + "</tbody>";
  $("#symbol-trades").querySelectorAll("tr[data-entry-ts]").forEach((row) =>
    row.addEventListener("click", () => {
      $("#symbol-trades").querySelectorAll("tr").forEach((x) => x.classList.toggle("focused", x === row));
      focusMarker(Number(row.dataset.entryTs));
    }));
  $("#missed-count").textContent = `（${missed.length} 条）`;
  $("#symbol-missed").innerHTML = "<tbody>" + (missed.length ? missed.slice(0, 80).map((m) => `
    <tr tabindex="0" data-entry-ts="${m.entry_time}">
      <td>${new Date(m.time * 1000).toISOString().slice(5, 16).replace("T", " ")}</td>
      <td class="num">${Number(m.score).toFixed(3)}</td>
      <td class="num">${m.dense_len}根</td>
    </tr>`).join("") : `<tr class="no-click"><td colspan="3" class="empty-state">暂无合格未成交</td></tr>`) + "</tbody>";
  $("#symbol-missed").querySelectorAll("tr[data-entry-ts]").forEach((row, i) => {
    const marker = missed[i];
    row.addEventListener("click", () => {
      $("#symbol-missed").querySelectorAll("tr").forEach((x) => x.classList.toggle("focused", x === row));
      focusMarker(Number(row.dataset.entryTs));
    });
    row.addEventListener("mouseenter", (e) => showSignalTooltip(e, marker));
    row.addEventListener("mousemove", (e) => positionSignalTooltip(e));
    row.addEventListener("mouseleave", hideSignalTooltip);
    row.addEventListener("focus", (e) => showSignalTooltip(e, marker));
    row.addEventListener("blur", hideSignalTooltip);
  });

  // default: focus the most recent trade so entry/exit/barriers show immediately
  if (!focusEntry && traded.length) focusEntry = traded[0].entry_time;
  if (focusEntry) {
    focusMarker(focusEntry);
    const row = $(`#symbol-trades tr[data-entry-ts="${focusEntry}"]`);
    if (row) row.classList.add("focused");
  }
}

function showSignalTooltip(event, marker) {
  const edge = marker.score - currentThreshold;
  $("#signal-tooltip").innerHTML = `<b>合格未成交 · ${new Date(marker.time * 1000).toISOString().slice(5, 16).replace("T", " ")}</b>
    <dl>
      <dt>score</dt><dd>${Number(marker.score).toFixed(4)}</dd>
      <dt>阈值差</dt><dd class="${cls(edge)}">${edge >= 0 ? "+" : ""}${edge.toFixed(4)}</dd>
      <dt>ATR%</dt><dd>${fmtPct(marker.atr_pct, 2)}</dd>
      <dt>密集长度</dt><dd>${marker.dense_len} 根</dd>
      <dt>标签收益</dt><dd class="${cls(marker.ret)}">${fmtPct(marker.ret, 2)}</dd>
      <dt>入场价</dt><dd>${Number(marker.entry_price).toPrecision(7)}</dd>
    </dl>`;
  $("#signal-tooltip").hidden = false;
  positionSignalTooltip(event);
}

function positionSignalTooltip(event) {
  const tip = $("#signal-tooltip");
  if (tip.hidden) return;
  const pad = 12, width = tip.offsetWidth || 260, height = tip.offsetHeight || 170;
  let x = event.clientX, y = event.clientY;
  if (!Number.isFinite(x) || !Number.isFinite(y)) {
    const rect = event.currentTarget.getBoundingClientRect();
    x = rect.right;
    y = rect.top;
  }
  const left = Math.min(window.innerWidth - width - pad, Math.max(pad, x + pad));
  const top = Math.min(window.innerHeight - height - pad, Math.max(pad, y + pad));
  tip.style.left = `${left}px`;
  tip.style.top = `${top}px`;
}

function hideSignalTooltip() {
  $("#signal-tooltip").hidden = true;
}

function focusMarker(entryTs) {
  const m = currentMarkers.find((x) => x.entry_time === entryTs);
  if (!m) return;
  priceLines.forEach((l) => klineSeries.removePriceLine(l)); priceLines = [];
  pathSeries.setData([]);
  klineChart.priceScale("right").applyOptions({ autoScale: true });
  const entry = m.entry_price;
  const exitPrice = entry * (1 + m.ret);
  const outcomeColor = OUTCOME_COLOR[m.outcome] || "#9aa0a8";
  const showTpBarrier = m.outcome !== "tp";
  const showSlBarrier = m.outcome !== "sl" && m.outcome !== "sl_ambiguous";
  priceLines.push(klineSeries.createPriceLine({
    price: entry, color: "#9aa0a8", lineStyle: 2, title: "入场",
  }));
  if (showTpBarrier) {
    priceLines.push(klineSeries.createPriceLine({  // v-label barriers of this trade
      price: entry * (1 + barrier.tp * m.atr_pct), color: "rgba(31,167,125,0.8)",
      lineStyle: 3, title: `止盈目标 +${barrier.tp}×ATR`,
    }));
  }
  if (showSlBarrier) {
    priceLines.push(klineSeries.createPriceLine({
      price: entry * (1 - barrier.sl * m.atr_pct), color: "rgba(230,103,103,0.8)",
      lineStyle: 3, title: `止损线 -${barrier.sl}×ATR`,
    }));
  }
  priceLines.push(klineSeries.createPriceLine({
    price: exitPrice, color: outcomeColor, lineStyle: 0,
    title: `出场 ${OUTCOME_CN[m.outcome] || m.outcome}`,
  }));
  // dense-MA window band (the "cluster box" this signal fired from)
  bandSeries.setData([
    { time: m.time - Math.max(m.dense_len, 1) * 900, value: 1 },
    { time: m.time, value: 1 },
  ]);
  // entry -> exit path
  pathSeries.applyOptions({ color: outcomeColor });
  pathSeries.setData([
    { time: m.entry_time, value: entry },
    { time: m.exit_time, value: exitPrice },
  ]);
  // position via logical bar indices (robust against time-mapping quirks);
  // setTimeout, not rAF -- rAF never fires in background tabs
  let i0 = currentTimes.findIndex((t) => t >= m.time);
  let i1 = currentTimes.findIndex((t) => t >= m.exit_time);
  if (i0 < 0) i0 = currentTimes.length - 1;
  if (i1 < 0) i1 = currentTimes.length - 1;
  lastFocusRange = { from: i0 - 96, to: i1 + 96 };
  setTimeout(() => klineChart.timeScale().setVisibleLogicalRange(lastFocusRange), 60);
}

async function focusTrade(source, symbol, entryTimeStr) {
  showView("signals");
  await initSignals();
  const entryTs = Math.floor(new Date(entryTimeStr.replace(" ", "T") + (entryTimeStr.includes("+") ? "" : "Z")).getTime() / 1000);
  const key = `${source}:${symbol}`;
  $("#symbol-input").value = key;
  sigState.bars = 40000;
  document.querySelectorAll("#bars-seg button").forEach((b) =>
    b.classList.toggle("active", b.dataset.bars === "40000"));
  await loadChart(key, entryTs);
}

/* ---------- P2.5 ops: token + experiments + agenda ---------- */
const opsState = {
  authRequired: false,
  executorEnabled: false,
  token: sessionStorage.getItem("ops_api_token") || "",
  jobTypes: [],
  selectedJobId: null,
  pollTimer: null,
};

const JOB_STATUS_CN = {
  queued: "排队",
  running: "运行中",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
  timeout: "超时",
};

function opsHeaders(extra = {}) {
  const h = { ...extra };
  if (opsState.token) h["X-Ops-Token"] = opsState.token;
  return h;
}

async function opsFetch(path, params = {}, options = {}) {
  const q = new URLSearchParams(params);
  const url = q.toString() ? `${path}?${q}` : path;
  const res = await fetch(url, {
    method: options.method || "GET",
    headers: opsHeaders(options.headers || {}),
    body: options.body,
  });
  if (res.status === 401 || res.status === 503) {
    const detail = (await res.json().catch(() => ({}))).detail || res.statusText;
    throw new Error(detail);
  }
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`;
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text();
}

async function refreshOpsAuthUi() {
  try {
    const st = await (await fetch("/api/ops/status")).json();
    opsState.authRequired = !!st.ops_auth_required;
    opsState.executorEnabled = !!st.executor_enabled;
    const wrap = $("#ops-token-wrap");
    if (wrap) {
      wrap.hidden = !opsState.authRequired;
      if (opsState.token && $("#ops-token-input")) $("#ops-token-input").value = opsState.token;
    }
  } catch (_) { /* ignore */ }
}

$("#ops-token-save")?.addEventListener("click", () => {
  opsState.token = ($("#ops-token-input")?.value || "").trim();
  if (opsState.token) sessionStorage.setItem("ops_api_token", opsState.token);
  else sessionStorage.removeItem("ops_api_token");
  const active = document.querySelector(".tab.active")?.dataset.view;
  if (active === "experiments") loadExperiments();
  if (active === "agenda") loadAgenda();
  if (active === "jobs") loadJobsView();
  if (active === "data") loadDataHub();
  if (active === "models") loadModelHub();
});

function fmtMetric(x, digits = 4) {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return "—";
  return Number(x).toFixed(digits);
}

async function loadExperiments() {
  const note = $("#exp-auth-note");
  const tbody = $("#exp-table tbody");
  if (!tbody) return;
  if (note) note.hidden = true;
  tbody.innerHTML = `<tr><td colspan="9" class="note">加载中…</td></tr>`;
  try {
    const kind = $("#exp-kind")?.value || "";
    const q = $("#exp-q")?.value || "";
    const sort = $("#exp-sort")?.value || "mtime";
    const data = await opsFetch("/api/ops/experiments", { kind, q, sort, order: "desc" });
    if ($("#exp-count")) $("#exp-count").textContent = `${data.count} 个产物`;
    if (!data.items?.length) {
      tbody.innerHTML = `<tr><td colspan="9"><div class="empty-state">analysis/output 无 JSON，或筛选为空</div></td></tr>`;
      return;
    }
    tbody.innerHTML = data.items.map((it) => {
      const m = it.metrics || {};
      const report = it.report_path
        ? `<span class="note">${String(it.report_path).replace(/^analysis\//, "")}</span>`
        : "—";
      const mtime = it.mtime_iso ? it.mtime_iso.slice(0, 16).replace("T", " ") : "—";
      return `<tr class="clickable" data-exp-id="${it.id}">
        <td><b>${it.id}</b></td>
        <td>${it.kind || "—"}</td>
        <td>${it.config ?? "—"}</td>
        <td class="num">${fmtMetric(m.val_auc, 3)}</td>
        <td class="num">${fmtMetric(m.perm_p, 3)}</td>
        <td class="num">${fmtMetric(m.top_net_maker, 4)}</td>
        <td class="num">${m.n_val ?? m.n ?? "—"}</td>
        <td>${report}</td>
        <td class="note">${mtime}</td>
      </tr>`;
    }).join("");
    tbody.querySelectorAll("tr[data-exp-id]").forEach((tr) => {
      tr.addEventListener("click", () => openExperiment(tr.dataset.expId));
    });
  } catch (err) {
    if (note) {
      note.hidden = false;
      note.innerHTML = `<b>无法加载实验表</b>：${err.message}<br>若 OPS_AUTH_MODE=token，请在右上角粘贴 token。`;
    }
    tbody.innerHTML = "";
  }
}

async function openExperiment(id) {
  const panel = $("#exp-detail-panel");
  if (!panel) return;
  panel.hidden = false;
  $("#exp-detail-id").textContent = id;
  $("#exp-detail-json").textContent = "加载中…";
  $("#exp-detail-report").textContent = "";
  try {
    const d = await opsFetch(`/api/ops/experiments/${encodeURIComponent(id)}`);
    $("#exp-detail-meta").textContent = [d.path, d.report_path || "无关联报告", d.kind].filter(Boolean).join(" · ");
    $("#exp-detail-json").textContent = JSON.stringify({
      rows_preview: (d.rows || []).slice(0, 20),
      n_rows: (d.rows || []).length,
    }, null, 2);
    $("#exp-detail-report").textContent = d.report_markdown || "（无 markdown 报告）";
  } catch (err) {
    $("#exp-detail-json").textContent = String(err.message || err);
  }
}

async function loadAgenda() {
  const pre = $("#agenda-md");
  const meta = $("#agenda-meta");
  if (!pre) return;
  pre.textContent = "加载中…";
  try {
    const d = await opsFetch("/api/ops/agenda");
    if (meta) {
      meta.textContent = d.exists
        ? `${d.path} · ${d.mtime_iso || "—"}`
        : (d.note || "议程不存在");
    }
    pre.textContent = d.markdown || "（空）";
  } catch (err) {
    if (meta) meta.textContent = "";
    pre.textContent = `加载失败：${err.message}`;
  }
}

$("#exp-refresh")?.addEventListener("click", () => loadExperiments());
$("#exp-kind")?.addEventListener("change", () => loadExperiments());
$("#exp-sort")?.addEventListener("change", () => loadExperiments());
let expQTimer = null;
$("#exp-q")?.addEventListener("input", () => {
  clearTimeout(expQTimer);
  expQTimer = setTimeout(() => loadExperiments(), 250);
});

/* ---------- P2.5 Phase2 jobs tab ---------- */
function stopJobsPoll() {
  if (opsState.pollTimer) {
    clearInterval(opsState.pollTimer);
    opsState.pollTimer = null;
  }
}

function startJobsPoll() {
  stopJobsPoll();
  opsState.pollTimer = setInterval(() => {
    const active = document.querySelector(".tab.active")?.dataset.view;
    if (active !== "jobs") {
      stopJobsPoll();
      return;
    }
    refreshJobsList(true);
    if (opsState.selectedJobId) openJobDetail(opsState.selectedJobId, true);
  }, 1000);
}

function selectedJobType() {
  const id = $("#job-type-select")?.value;
  return (opsState.jobTypes || []).find((j) => j.job_type === id) || null;
}

function collectJobParams() {
  const form = $("#job-params-form");
  const params = {};
  if (!form) return params;
  form.querySelectorAll("[data-param]").forEach((el) => {
    const name = el.dataset.param;
    if (el.type === "number") {
      const v = el.value === "" ? null : Number(el.value);
      if (v !== null && !Number.isNaN(v)) params[name] = v;
    } else if (el.value !== "") {
      params[name] = el.value;
    }
  });
  return params;
}

function renderJobParamsForm() {
  const jt = selectedJobType();
  const form = $("#job-params-form");
  const desc = $("#job-type-desc");
  const preview = $("#job-cmd-preview");
  if (!form) return;
  if (!jt) {
    form.innerHTML = "";
    if (desc) desc.textContent = "";
    if (preview) preview.textContent = "";
    return;
  }
  if (desc) {
    desc.textContent = `${jt.description_zh || ""} · 超时 ${Math.round((jt.timeout_sec || 0) / 60)} 分钟 · 产物 ${jt.artifacts_hint || "—"}`;
  }
  if (!jt.params?.length) {
    form.innerHTML = `<div class="note">此任务无参数（固定 argv）。</div>`;
  } else {
    form.innerHTML = jt.params.map((p) => {
      if (p.kind === "enum" || p.kind === "path_enum") {
        const opts = (p.choices || []).map((c) =>
          `<option value="${c}" ${c === p.default ? "selected" : ""}>${c}</option>`
        ).join("");
        return `<div class="param-row">
          <label for="param-${p.name}">${p.name}</label>
          <select id="param-${p.name}" data-param="${p.name}">${opts}</select>
          <span class="note">${p.description || ""}</span>
        </div>`;
      }
      if (p.kind === "int") {
        return `<div class="param-row">
          <label for="param-${p.name}">${p.name}</label>
          <input id="param-${p.name}" data-param="${p.name}" type="number"
            min="${p.min ?? ""}" max="${p.max ?? ""}" value="${p.default ?? ""}">
          <span class="note">${p.description || ""} [${p.min ?? "?"}–${p.max ?? "?"}]</span>
        </div>`;
      }
      return "";
    }).join("");
  }
  form.querySelectorAll("[data-param]").forEach((el) => {
    el.addEventListener("change", updateJobCmdPreview);
    el.addEventListener("input", updateJobCmdPreview);
  });
  updateJobCmdPreview();
  updateJobRunEnabled();
}

function updateJobCmdPreview() {
  const jt = selectedJobType();
  const preview = $("#job-cmd-preview");
  if (!preview || !jt) return;
  const params = collectJobParams();
  // Client-side human summary only (server re-validates); mirror whitelist shape.
  let parts = ["python3"];
  const map = {
    build_dataset: () => {
      const a = ["-m", "src.judgment.build_dataset", "--mode", params.mode || "strict",
        "--bar", params.bar || "15m", "--horizon-bars", String(params.horizon_bars ?? 96)];
      if (params.out) a.push("--out", params.out);
      return a;
    },
    barrier_sweep: () => ["-m", "src.judgment.barrier_sweep"],
    swap_replication: () => ["scripts/swap_replication.py"],
    update_okx: () => ["-m", "src.data.update_okx", "--bar", params.bar || "15m"],
    forward_track: () => ["scripts/forward_track.py"],
    deploy_self: () => null,
  };
  const builder = map[jt.job_type];
  if (jt.job_type === "deploy_self") {
    preview.textContent = "将执行：bash scripts/deploy_vps.sh";
    return;
  }
  if (!builder) {
    preview.textContent = "";
    return;
  }
  preview.textContent = `将执行：python3 ${builder().join(" ")}`;
}

function updateJobRunEnabled() {
  const btn = $("#job-run-btn");
  if (!btn) return;
  btn.disabled = !opsState.executorEnabled || !selectedJobType();
}

async function loadJobsView() {
  await refreshOpsAuthUi();
  const banner = $("#jobs-executor-banner");
  const authNote = $("#jobs-auth-note");
  if (banner) {
    if (!opsState.executorEnabled) {
      banner.hidden = false;
      banner.classList.add("warn-banner");
      banner.innerHTML = `<b>执行器已禁用</b>：本实例 ENABLE_JOB_EXECUTOR≠1（VPS 默认）。请在 Mac 看板用环境变量开启任务执行；此页仍可浏览 job 类型与历史。`;
    } else {
      banner.hidden = true;
      banner.innerHTML = "";
    }
  }
  if (authNote) authNote.hidden = true;
  try {
    const data = await opsFetch("/api/ops/job-types");
    opsState.executorEnabled = !!data.executor_enabled;
    opsState.jobTypes = data.items || [];
    if (banner && !opsState.executorEnabled) {
      banner.hidden = false;
      banner.classList.add("warn-banner");
      banner.innerHTML = `<b>执行器已禁用</b>：本实例 ENABLE_JOB_EXECUTOR≠1（VPS 默认）。请在 Mac 看板开启后再创建任务。`;
    }
    const sel = $("#job-type-select");
    if (sel) {
      const prev = sel.value;
      sel.innerHTML = opsState.jobTypes.map((j) =>
        `<option value="${j.job_type}">${j.title_zh} (${j.job_type})</option>`
      ).join("");
      if (prev && opsState.jobTypes.some((j) => j.job_type === prev)) sel.value = prev;
      renderJobParamsForm();
    }
    await refreshJobsList();
    startJobsPoll();
  } catch (err) {
    if (authNote) {
      authNote.hidden = false;
      authNote.innerHTML = `<b>无法加载任务页</b>：${err.message}<br>若 OPS_AUTH_MODE=token，请在右上角粘贴 token。`;
    }
  }
  updateJobRunEnabled();
}

async function refreshJobsList(silent = false) {
  const tbody = $("#jobs-table tbody");
  if (!tbody) return;
  try {
    const data = await opsFetch("/api/ops/jobs", { limit: "50", offset: "0" });
    if ($("#jobs-count")) $("#jobs-count").textContent = `（${data.total || 0}）`;
    if (!data.items?.length) {
      tbody.innerHTML = `<tr><td colspan="6"><div class="empty-state">暂无任务</div></td></tr>`;
      return;
    }
    tbody.innerHTML = data.items.map((j) => {
      const st = j.status || "";
      const created = (j.created_at || "").slice(0, 19).replace("T", " ");
      const active = j.id === opsState.selectedJobId ? "active-row" : "";
      return `<tr class="clickable ${active}" data-job-id="${j.id}">
        <td class="note">${created || "—"}</td>
        <td>${j.job_type || "—"}</td>
        <td><span class="status-chip ${st}">${JOB_STATUS_CN[st] || st}</span></td>
        <td class="note">${(j.summary || "").slice(0, 80)}</td>
        <td class="num">${j.exit_code ?? "—"}</td>
        <td class="note">${(j.id || "").slice(0, 8)}</td>
      </tr>`;
    }).join("");
    tbody.querySelectorAll("tr[data-job-id]").forEach((tr) => {
      tr.addEventListener("click", () => openJobDetail(tr.dataset.jobId));
    });
  } catch (err) {
    if (!silent) {
      tbody.innerHTML = `<tr><td colspan="6" class="note">加载失败：${err.message}</td></tr>`;
    }
  }
}

async function openJobDetail(jobId, silent = false) {
  opsState.selectedJobId = jobId;
  const meta = $("#job-active-meta");
  const pre = $("#job-log-pre");
  const cancelBtn = $("#job-cancel-btn");
  try {
    const d = await opsFetch(`/api/ops/jobs/${encodeURIComponent(jobId)}`, { log_lines: "300" });
    if (meta) {
      meta.textContent = `${d.job_type} · ${JOB_STATUS_CN[d.status] || d.status} · ${d.id?.slice(0, 12) || ""}`;
    }
    if (pre) pre.textContent = d.log_tail || "（日志为空）";
    if (cancelBtn) {
      const canCancel = opsState.executorEnabled && (d.status === "queued" || d.status === "running");
      cancelBtn.hidden = !canCancel;
    }
    // Highlight row
    document.querySelectorAll("#jobs-table tr[data-job-id]").forEach((tr) => {
      tr.classList.toggle("active-row", tr.dataset.jobId === jobId);
    });
  } catch (err) {
    if (!silent && pre) pre.textContent = `加载日志失败：${err.message}`;
  }
}

$("#job-type-select")?.addEventListener("change", () => renderJobParamsForm());
$("#job-refresh-btn")?.addEventListener("click", () => refreshJobsList());
$("#job-run-btn")?.addEventListener("click", async () => {
  const jt = selectedJobType();
  const msg = $("#job-create-msg");
  if (!jt) return;
  if (!opsState.executorEnabled) {
    if (msg) msg.textContent = "执行器已禁用";
    return;
  }
  const params = collectJobParams();
  const confirmText = `${jt.confirm_zh || "确认运行此任务？"}\n\n类型：${jt.job_type}\n预览见页面摘要（服务端按白名单组装 argv，不可编辑 shell）。`;
  if (!window.confirm(confirmText)) return;
  if (msg) msg.textContent = "提交中…";
  try {
    const job = await opsFetch("/api/ops/jobs", {}, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_type: jt.job_type, params }),
    });
    if (msg) msg.textContent = `已排队 ${job.id?.slice(0, 8) || ""}`;
    opsState.selectedJobId = job.id;
    await refreshJobsList();
    await openJobDetail(job.id);
    startJobsPoll();
  } catch (err) {
    if (msg) msg.textContent = `失败：${err.message}`;
  }
});
$("#job-cancel-btn")?.addEventListener("click", async () => {
  if (!opsState.selectedJobId || !opsState.executorEnabled) return;
  if (!window.confirm("确认取消该任务？将发送 SIGTERM。")) return;
  try {
    await opsFetch(`/api/ops/jobs/${encodeURIComponent(opsState.selectedJobId)}/cancel`, {}, {
      method: "POST",
    });
    await openJobDetail(opsState.selectedJobId);
    await refreshJobsList();
  } catch (err) {
    const msg = $("#job-create-msg");
    if (msg) msg.textContent = `取消失败：${err.message}`;
  }
});

/* ---------- P2.5 Phase3 data + model hubs (read-only) ---------- */

function shortSha(s, n = 12) {
  if (!s) return "—";
  const t = String(s);
  return t.length <= n ? t : t.slice(0, n) + "…";
}

function tileHtml(label, value, sub = "") {
  return `<div class="tile"><span class="lbl">${label}</span><b>${value}</b><small>${sub || ""}</small></div>`;
}

async function loadDataHub() {
  const note = $("#data-auth-note");
  if (note) note.hidden = true;
  const tbody = $("#data-coverage-table tbody");
  if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="note">加载中…</td></tr>`;
  try {
    const d = await opsFetch("/api/ops/data-hub");
    if ($("#data-generated")) {
      $("#data-generated").textContent = d.generated_at
        ? `生成于 ${d.generated_at} · 只读`
        : "只读";
    }
    const cov = d.coverage || {};
    const tiles = $("#data-coverage-tiles");
    if (tiles) {
      tiles.innerHTML = [
        tileHtml("series 合计", cov.series_total ?? "—"),
        tileHtml("files 合计", cov.file_total ?? "—"),
        tileHtml("fetched", cov.fetched_exists ? "有" : "无", cov.fetched_dir || ""),
        tileHtml("cache", cov.cache_exists ? "有" : "无", cov.cache_dir || ""),
      ].join("");
    }
    if (tbody) {
      const rows = cov.by_bar || [];
      if (!rows.length) {
        tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">无 bar 覆盖数据</div></td></tr>`;
      } else {
        tbody.innerHTML = rows.map((r) => `
          <tr>
            <td>${r.bar}</td>
            <td class="num">${r.series_n}</td>
            <td class="num">${r.file_n}</td>
            <td class="num">${r.named_rows_sum ?? "—"}</td>
            <td class="num">${r.raw_fetched_csv ?? "—"}</td>
            <td class="num">${r.raw_cache_csv ?? "—"}</td>
            <td>${r.latest_mtime ? String(r.latest_mtime).slice(0, 19) : "—"}</td>
          </tr>`).join("");
      }
    }
    const audit = d.audit || {};
    const ameta = $("#data-audit-meta");
    if (ameta) {
      if (!audit.exists) {
        ameta.textContent = `审计摘要不存在（${audit.path || "analysis/output/data_audit_summary.json"}）。可先跑 scripts/data_audit.py。`;
      } else {
        ameta.textContent = `${audit.path || ""} · mtime ${audit.mtime ? String(audit.mtime).slice(0, 19) : "—"}${audit.report_exists ? ` · 报告 ${audit.report_path}` : ""}`;
      }
    }
    const atiles = $("#data-audit-tiles");
    const s = audit.summary || {};
    if (atiles) {
      if (audit.exists && s && typeof s === "object") {
        atiles.innerHTML = [
          tileHtml("series_total", s.series_total ?? "—"),
          tileHtml("flagged", s.flagged ?? "—"),
          tileHtml("blacklist 候选", s.blacklist_candidate_n ?? "—"),
          tileHtml("okx swap15 stale", s.okx_swap15_stale ?? "—", s.okx_swap15_n != null ? `of ${s.okx_swap15_n}` : ""),
        ].join("");
      } else {
        atiles.innerHTML = "";
      }
    }
    const apre = $("#data-audit-json");
    if (apre) {
      if (audit.error) apre.textContent = audit.error;
      else if (audit.summary) apre.textContent = JSON.stringify(audit.summary, null, 2);
      else apre.textContent = "（无摘要）";
    }
    const fwd = d.forward || {};
    const ftiles = $("#data-forward-tiles");
    if (ftiles) {
      ftiles.innerHTML = [
        tileHtml("日志行", fwd.exists ? (fwd.total_rows ?? 0) : "无文件"),
        tileHtml("closed", fwd.closed_rows ?? "—"),
        tileHtml("决策笔数", `${fwd.decision_trades ?? 0} / ${fwd.decision_target ?? 100}`),
        tileHtml("进度", fwd.progress != null ? `${Math.round(100 * Number(fwd.progress))}%` : "—",
          fwd.decision_remaining != null ? `剩余 ${fwd.decision_remaining}` : ""),
      ].join("");
    }
    const fmeta = $("#data-forward-meta");
    if (fmeta) {
      fmeta.textContent = fwd.exists
        ? `path ${fwd.path || "—"} · latest detected_at ${fwd.latest_detected_at || "—"} · mtime ${fwd.mtime ? String(fwd.mtime).slice(0, 19) : "—"}`
        : `无 forward 日志（${fwd.path || "data/forward_log.csv"}）`;
    }
    const parts = d.part_files_live || {};
    const ptiles = $("#data-parts-tiles");
    if (ptiles) {
      ptiles.innerHTML = [
        tileHtml("part 文件数", parts.count ?? 0),
        tileHtml("fetched_dir", parts.fetched_dir || "—"),
        tileHtml("截断", parts.truncated ? "是" : "否"),
      ].join("");
    }
    const pmeta = $("#data-parts-meta");
    if (pmeta) {
      pmeta.textContent = parts.hint || "Resume: python3 -m src.data.fetch_okx --symbols <SYM> --bar 15m --workers 1";
    }
    const pbody = $("#data-parts-table tbody");
    if (pbody) {
      const items = parts.items || [];
      if (!items.length) {
        pbody.innerHTML = `<tr><td colspan="4"><div class="empty-state">无 .part.csv（拉取已齐或目录空）</div></td></tr>`;
      } else {
        pbody.innerHTML = items.map((it) => `
          <tr>
            <td>${it.name || "—"}</td>
            <td class="num">${it.rows_approx ?? "—"}</td>
            <td class="num">${it.bytes != null ? it.bytes : "—"}</td>
            <td>${it.mtime ? String(it.mtime).slice(0, 19) : "—"}</td>
          </tr>`).join("");
      }
    }
  } catch (err) {
    if (note) {
      note.hidden = false;
      note.innerHTML = `<b>无法加载数据中枢</b>：${err.message}<br>若 OPS_AUTH_MODE=token，请在右上角粘贴 token。`;
    }
    if (tbody) tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state">加载失败</div></td></tr>`;
  }
}

async function loadModelHub() {
  const note = $("#models-auth-note");
  if (note) note.hidden = true;
  const tbody = $("#models-table tbody");
  if (tbody) tbody.innerHTML = `<tr><td colspan="9" class="note">加载中…</td></tr>`;
  try {
    const d = await opsFetch("/api/ops/model-hub");
    if ($("#models-generated")) {
      $("#models-generated").textContent = d.generated_at
        ? `生成于 ${d.generated_at} · ${d.count || 0} 个 · paired ${d.paired_count || 0}`
        : "";
    }
    const active = d.active || {};
    const badge = $("#models-active-badge");
    if (badge) {
      if (active.exists && active.artifact_id) {
        badge.innerHTML = `ACTIVE → <b>${active.artifact_id}</b>`;
      } else {
        badge.textContent = "ACTIVE 指针未设置（models/ACTIVE 不存在）";
      }
    }
    if (tbody) {
      const items = d.items || [];
      if (!items.length) {
        tbody.innerHTML = `<tr><td colspan="9"><div class="empty-state">models/ 无 frozen_* 工件</div></td></tr>`;
      } else {
        tbody.innerHTML = items.map((it) => {
          const fp = it.fingerprint || {};
          const thr = it.threshold_val_q90;
          const thrStr = thr === null || thr === undefined ? "—" : Number(thr).toFixed(6);
          const pairCn = {
            paired: "✓ 双文件",
            missing_txt: "缺 .txt",
            missing_json: "缺 .json",
            missing_both: "—",
          }[it.pair_status] || it.pair_status;
          const fpCn = {
            ok: "ok",
            mismatch: "mismatch",
            unverifiable: "unverifiable",
            no_fingerprint: "—",
            no_json: "—",
            skipped: "skipped",
            error: "error",
          }[fp.fingerprint_status] || (fp.fingerprint_status || "—");
          const rowCls = it.is_active ? "active-row" : "";
          return `<tr class="${rowCls}">
            <td><code>${it.artifact_id}</code></td>
            <td>${pairCn}</td>
            <td>${it.config || "—"}</td>
            <td class="num">${thrStr}</td>
            <td title="${it.dataset_sha256 || ""}"><code>${shortSha(it.dataset_sha256, 10)}</code></td>
            <td title="${fp.note || fp.actual_sha256 || ""}">${fpCn}</td>
            <td class="num">${it.n_features ?? "—"}</td>
            <td>${it.created_at ? String(it.created_at).slice(0, 19) : "—"}</td>
            <td>${it.is_active ? "●" : ""}</td>
          </tr>`;
        }).join("");
      }
    }
  } catch (err) {
    if (note) {
      note.hidden = false;
      note.innerHTML = `<b>无法加载模型中枢</b>：${err.message}<br>若 OPS_AUTH_MODE=token，请在右上角粘贴 token。`;
    }
    if (tbody) tbody.innerHTML = `<tr><td colspan="9"><div class="empty-state">加载失败</div></td></tr>`;
  }
}

$("#data-refresh")?.addEventListener("click", () => loadDataHub());
$("#models-refresh")?.addEventListener("click", () => loadModelHub());


/* ---------- theme ---------- */
function initTheme() {
  const saved = localStorage.getItem("fable_theme") || "dark";
  applyTheme(saved);
  $("#theme-toggle")?.addEventListener("click", () => {
    const next = document.body.classList.contains("theme-light") ? "dark" : "light";
    localStorage.setItem("fable_theme", next);
    location.reload();
  });
}
function applyTheme(mode) {
  document.body.classList.toggle("theme-light", mode === "light");
  const btn = $("#theme-toggle");
  if (btn) btn.textContent = mode === "light" ? "深色" : "浅色";
  // chart layout colors
  if (mode === "light") {
    CHART_LAYOUT.layout.background.color = "#ffffff";
    CHART_LAYOUT.layout.textColor = "#5c6b82";
    CHART_LAYOUT.grid.vertLines.color = "#eef1f6";
    CHART_LAYOUT.grid.horzLines.color = "#eef1f6";
    CHART_LAYOUT.timeScale.borderColor = "#d8dee9";
    CHART_LAYOUT.rightPriceScale.borderColor = "#d8dee9";
  } else {
    // Hummingbot-ish dark chart paper
    CHART_LAYOUT.layout.background.color = "#0a0c10";
    CHART_LAYOUT.layout.textColor = "#8b949e";
    CHART_LAYOUT.grid.vertLines.color = "#161b22";
    CHART_LAYOUT.grid.horzLines.color = "#161b22";
    CHART_LAYOUT.timeScale.borderColor = "#30363d";
    CHART_LAYOUT.rightPriceScale.borderColor = "#30363d";
  }
}

function initNavDrawer() {
  const burger = $("#nav-burger");
  const closeBtn = $("#nav-close");
  const backdrop = $("#nav-backdrop");
  const sidebar = $("#sidebar");
  if (!burger || !sidebar) return;
  const setOpen = (open) => {
    document.body.classList.toggle("nav-open", open);
    burger.setAttribute("aria-expanded", open ? "true" : "false");
    if (backdrop) backdrop.hidden = !open;
  };
  burger.addEventListener("click", () => setOpen(!document.body.classList.contains("nav-open")));
  closeBtn?.addEventListener("click", () => setOpen(false));
  backdrop?.addEventListener("click", () => setOpen(false));
  // close after navigating on small screens
  $$(".sb-item[data-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (window.matchMedia("(max-width: 960px)").matches) setOpen(false);
    });
  });
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && document.body.classList.contains("nav-open")) setOpen(false);
  });
}

function boot() {
  initTheme();
  initNavDrawer();
  refreshOpsAuthUi();
  loadStatusStrip();
  if (!localStorage.getItem("fable_explore_seen")) {
    setTimeout(() => {
      toast("提示：从「密集探索」选币看密集框；←→ 换币，j/k 切换框", "ok");
      localStorage.setItem("fable_explore_seen", "1");
    }, 800);
  }
  $("#status-refresh")?.addEventListener("click", () => loadStatusStrip(true));
  $("#overview-go-explore")?.addEventListener("click", () => showView("explore", { force: true }));
  const hash = (location.hash || "").slice(1);
  const initial = hash && document.getElementById("view-" + hash) ? hash : "explore";
  showView(initial, { pushHash: false });
  // refresh strip every 2 min (cheap)
  setInterval(() => loadStatusStrip(false), 120_000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
