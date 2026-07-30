/* fable-trading dashboard frontend (vanilla JS + Lightweight Charts v4) */
/* allow: SIZE_OK -- single-file; r2: view-cache, trades page, a11y, keyboard, escape. */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
/* Shared pure formatters from format_helpers.js (loaded before app.js). */
const _F = globalThis.FableFmt || {};
const fmtPct = _F.fmtPct
  ? (x, digits = 2) => _F.fmtPct(x, digits)
  : (x, digits = 2) => (x === null || x === undefined || Number.isNaN(Number(x)) ? "—" : (100 * x).toFixed(digits) + "%");
const fmtPF = _F.fmtPF || ((x) => (x === null || x === undefined || Number.isNaN(Number(x)) ? "—" : Number(x).toFixed(2)));
const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const OUTCOME_CN = (globalThis.FableChart && globalThis.FableChart.OUTCOME_CN) || {
  tp: "止盈", sl: "止损", timeout: "超时", sl_ambiguous: "止损*", "": "未结束",
};
const OUTCOME_COLOR = (globalThis.FableChart && globalThis.FableChart.OUTCOME_COLOR) || {
  tp: "#059669", sl: "#dc2626", timeout: "#c98500", sl_ambiguous: "#dc2626",
};
const STATUS_CN = { open: "持有中", closed: "已结束" };
const appState = { universe: "swap", view: "overview" };
/** views loaded for current universe — skip refetch when tabbing back (TTL, not forever) */
const viewLoadedAt = new Map(); // view -> timestamp
/* keyboard 1–n: daily loop first, then tools */
const VIEW_ORDER = ["overview", "forward", "signals", "backtest", "probe", "shorttf", "radar"];
const chartTickMarkBj = _F.chartTickMarkBj || function chartTickMarkBj(time) {
  if (time == null) return "";
  if (typeof time === "object" && time.year != null) {
    return `${time.year}-${String(time.month).padStart(2, "0")}-${String(time.day).padStart(2, "0")}`;
  }
  const s = fmtBjTime(typeof time === "number" ? time : Number(time));
  return s.length >= 16 ? s.slice(5, 16) : s;
};

/* Shared K-line theme (chart_theme.js) — Claude short paper-chart contract */
const _C = globalThis.FableChart || null;
const CHART_LAYOUT = _C
  ? _C.chartLayout()
  : {
    layout: { background: { type: "solid", color: "#ffffff" }, textColor: "#6b7280", fontSize: 12 },
    grid: { vertLines: { color: "#eef1f6" }, horzLines: { color: "#eef1f6" } },
    localization: { locale: "zh-CN", timeFormatter: (t) => fmtChartTime(t) },
    timeScale: {
      borderColor: "#e5e7eb", timeVisible: true, secondsVisible: false,
      rightOffset: 10, barSpacing: 8, minBarSpacing: 2, tickMarkFormatter: chartTickMarkBj,
    },
    rightPriceScale: { borderColor: "#e5e7eb", scaleMargins: { top: 0.1, bottom: 0.14 }, entireTextOnly: false },
    crosshair: {
      mode: 1,
      vertLine: { color: "rgba(37,99,235,0.35)", width: 1, style: 2, labelBackgroundColor: "#2563eb" },
      horzLine: { color: "rgba(107,114,128,0.35)", width: 1, style: 2, labelBackgroundColor: "#6b7280" },
    },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { axisPressedMouseMove: { time: true, price: true }, mouseWheel: true, pinch: true },
  };
const pctFormat = { type: "custom", formatter: (v) => v.toFixed(2) + "%" };

/* ---------- Lightweight Charts TV-ish helpers (free tier) ---------- */
function fmtPx(v, digits) {
  if (_C && _C.fmtPx) return _C.fmtPx(v, digits);
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  if (digits != null) return n.toFixed(digits);
  const a = Math.abs(n);
  if (a >= 1000) return n.toFixed(2);
  if (a >= 1) return n.toFixed(4);
  if (a >= 0.01) return n.toFixed(6);
  return n.toPrecision(4);
}

const BJ_OFFSET_MS = _F.BJ_OFFSET_MS || 8 * 3600 * 1000;
const fmtBjTime = _F.fmtBjTime || function fmtBjTime(input, opts) {
  const seconds = opts && opts.seconds;
  if (input == null || input === "") return "—";
  let d;
  if (typeof input === "number") {
    const ms = input < 1e12 ? input * 1000 : input;
    d = new Date(ms);
  } else if (input instanceof Date) {
    d = input;
  } else {
    let s = String(input).trim();
    if (/^\d{4}-\d{2}-\d{2}/.test(s) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) {
      s = s.replace(" ", "T");
      if (!s.endsWith("Z")) s += "Z";
    }
    d = new Date(s);
  }
  if (Number.isNaN(d.getTime())) return String(input).slice(0, 16).replace("T", " ");
  const bj = new Date(d.getTime() + BJ_OFFSET_MS);
  const p = (n) => String(n).padStart(2, "0");
  let out = `${bj.getUTCFullYear()}-${p(bj.getUTCMonth() + 1)}-${p(bj.getUTCDate())} ${p(bj.getUTCHours())}:${p(bj.getUTCMinutes())}`;
  if (seconds) out += `:${p(bj.getUTCSeconds())}`;
  return out;
};
const fmtChartTime = _F.fmtChartTime || ((t) => (t == null ? "" : fmtBjTime(typeof t === "number" ? t : Number(t))));
const fmtLagMin = _F.fmtLagMin || function fmtLagMin(lagMin, freshMax) {
  const max = freshMax == null ? 20 : Number(freshMax);
  if (lagMin == null || lagMin === "" || Number.isNaN(Number(lagMin))) return { text: "—", fresh: false, cls: "" };
  const n = Number(lagMin);
  const fresh = n <= max;
  const text = n >= 60 ? (n / 60).toFixed(1) + "h" : Math.round(n) + "m";
  return { text: text + (fresh ? "" : " ·事后"), fresh, cls: fresh ? "pos" : "neg" };
};

/** Aggregate 15m candles into higher TF (client-side; data stays 15m on server). */
function aggregateCandles(candles, minutes) {
  if (!candles?.length || !minutes || minutes <= 15) return candles || [];
  const sec = minutes * 60;
  const out = [];
  let cur = null;
  for (const c of candles) {
    const bt = Math.floor(Number(c.time) / sec) * sec;
    if (!cur || cur.time !== bt) {
      if (cur) out.push(cur);
      cur = {
        time: bt,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: Number(c.volume) || 0,
      };
    } else {
      cur.high = Math.max(cur.high, c.high);
      cur.low = Math.min(cur.low, c.low);
      cur.close = c.close;
      cur.volume += Number(c.volume) || 0;
    }
  }
  if (cur) out.push(cur);
  return out;
}

function snapTimeToTf(t, minutes) {
  if (t == null || !minutes || minutes <= 15) return t;
  const sec = minutes * 60;
  return Math.floor(Number(t) / sec) * sec;
}

function smaSeries(candles, span) {
  const out = [];
  let sum = 0;
  for (let i = 0; i < candles.length; i++) {
    sum += candles[i].close;
    if (i >= span) sum -= candles[i - span].close;
    if (i >= span - 1) out.push({ time: candles[i].time, value: sum / span });
  }
  return out;
}

function emaSeriesFrom(candles, span) {
  const out = [];
  if (!candles.length) return out;
  const k = 2 / (span + 1);
  let ema = candles[0].close;
  for (let i = 0; i < candles.length; i++) {
    ema = i === 0 ? candles[i].close : candles[i].close * k + ema * (1 - k);
    if (i >= span - 1) out.push({ time: candles[i].time, value: ema });
  }
  return out;
}

/** Zoom to last N bars (TV-like default), leave room on the right. */
function showLastBars(chart, nBars, total) {
  if (!chart || !total) return;
  const n = Math.min(Math.max(nBars || 80, 20), total);
  const from = Math.max(0, total - n);
  const to = total + 4;
  try {
    chart.timeScale().setVisibleLogicalRange({ from: from - 0.5, to: to + 0.5 });
  } catch (_) { /* ignore */ }
}

/**
 * Wire OHLC legend strip for a candle series (TV-style top-left info).
 * @param {HTMLElement|null} el  e.g. #kline-ohlc
 * @param {*} candleSeries
 */
function wireOhlcLegend(chart, candleSeries, el, opts = {}) {
  if (!chart || !candleSeries || !el) return () => {};
  const onMove = (param) => {
    if (!param || !param.time || !param.seriesData) {
      if (opts.hideWhenEmpty) el.hidden = true;
      return;
    }
    const c = param.seriesData.get(candleSeries);
    if (!c || c.open == null) {
      if (opts.hideWhenEmpty) el.hidden = true;
      return;
    }
    el.hidden = false;
    const chg = c.close - c.open;
    const chgPct = c.open ? (100 * chg) / c.open : 0;
    const up = chg >= 0;
    const clsName = up ? "up" : "down";
    const timeStr = fmtChartTime(param.time);
    el.innerHTML =
      `<span class="ohlc-time">${timeStr}</span>` +
      `<span>O <b>${fmtPx(c.open)}</b></span>` +
      `<span>H <b>${fmtPx(c.high)}</b></span>` +
      `<span>L <b>${fmtPx(c.low)}</b></span>` +
      `<span>C <b class="${clsName}">${fmtPx(c.close)}</b></span>` +
      `<span class="${clsName}">${up ? "+" : ""}${chgPct.toFixed(2)}%</span>` +
      (c.volume != null
        ? ""
        : "");
    // volume comes from a separate series — optional second arg via opts.volByTime
    if (opts.volByTime && param.time != null) {
      const v = opts.volByTime.get(param.time);
      if (v != null) {
        el.innerHTML += `<span class="ohlc-vol">Vol <b>${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}</b></span>`;
      }
    }
  };
  chart.subscribeCrosshairMove(onMove);
  return () => {
    try { chart.unsubscribeCrosshairMove(onMove); } catch (_) { /* ignore */ }
  };
}

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
  if (_C && _C.makeChart) {
    try {
      return _C.makeChart(el, opts);
    } catch (err) {
      toast(err.message || "图表创建失败");
      throw err;
    }
  }
  // deep-merge crosshair so callers can override pieces without wiping Magnet mode
  const base = {
    ...CHART_LAYOUT,
    autoSize: true,
    ...opts,
    layout: { ...CHART_LAYOUT.layout, ...(opts.layout || {}) },
    grid: {
      vertLines: { ...CHART_LAYOUT.grid.vertLines, ...(opts.grid?.vertLines || {}) },
      horzLines: { ...CHART_LAYOUT.grid.horzLines, ...(opts.grid?.horzLines || {}) },
    },
    timeScale: { ...CHART_LAYOUT.timeScale, ...(opts.timeScale || {}) },
    rightPriceScale: { ...CHART_LAYOUT.rightPriceScale, ...(opts.rightPriceScale || {}) },
    crosshair: {
      ...CHART_LAYOUT.crosshair,
      ...(opts.crosshair || {}),
      vertLine: { ...CHART_LAYOUT.crosshair.vertLine, ...(opts.crosshair?.vertLine || {}) },
      horzLine: { ...CHART_LAYOUT.crosshair.horzLine, ...(opts.crosshair?.horzLine || {}) },
    },
  };
  return LightweightCharts.createChart(el, base);
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
  // every view switch (incl. direct #forward deep-link) must have the 4-chip strip
  if (typeof ensureFourChips === "function") ensureFourChips();
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
  if (name === "labeling") { if (need) loadLabelingHub().then(mark); else mark(); return; }
  if (name === "probe") { mark(); $("#probe-symbol")?.focus(); return; }
  if (name === "radar") {
    if (need) {
      const init = window.initScoutMtf;
      if (typeof init === "function") {
        Promise.resolve(init(force)).then(mark).catch((err) => {
          toast(`雷达：${err.message || err}`);
          mark();
        });
      } else {
        toast("雷达脚本未加载");
        mark();
      }
    } else mark();
    return;
  }
  if (name === "shorttf") { if (need) loadShortTf().then(mark); else mark(); return; }
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
  const name = (location.hash || "#overview").slice(1);
  if (name && document.getElementById("view-" + name)) showView(name, { pushHash: false });
});

/* Universe fixed to SWAP mainline — spot toggle removed from UI. */
appState.universe = "swap";

/* keyboard: 1-9 tabs, r refresh strip, / focus symbol when on signals */
document.addEventListener("keydown", (e) => {
  const tag = (e.target && e.target.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.target?.isContentEditable) return;
  if (e.key === "/" && appState.view === "signals") {
    e.preventDefault();
    openSymbolCombo();
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
const CHART_MA_ORDER = (_C && _C.MA_ORDER) || ["sma120", "sma60", "sma20", "ema120", "ema60", "ema20"];
const CHART_MA_STYLE = (_C && _C.MA_STYLE) || {
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
  if (_C && _C.addMaSeries) {
    _C.addMaSeries(chart, payload, sink);
    return;
  }
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
      drawExploreBoxes();
      highlightExploreRow(null);
    });
    $("#explore-to-signals")?.addEventListener("click", () => {
      const sym = exploreState.symbol;
      if (!sym) { toast("请先画出一个币种"); return; }
      showView("signals", { force: true });
      initSignals(true).then(() => {
        const key = `okx:${sym}`;
        setSymbolComboValue(key, { silent: true });
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
      drawExploreBoxes();
    });
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
    drawExploreBoxes();
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
  exploreSeries = exploreChart.addCandlestickSeries(
    (_C && _C.candlestickOptions()) || {
      upColor: "#059669", downColor: "#dc2626", borderVisible: false,
      wickUpColor: "#059669", wickDownColor: "#dc2626",
    }
  );
  exploreVol = exploreChart.addHistogramSeries({
    priceScaleId: "vol", priceFormat: { type: "volume" },
    priceLineVisible: false, lastValueVisible: false,
  });
  exploreChart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.72, bottom: 0 } });
  exploreChart.priceScale("right").applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });
  // Markers + canvas dense boxes; redraw on pan/zoom (min box size at full zoom).
  const redraw = () => drawExploreBoxes();
  exploreChart.timeScale().subscribeVisibleLogicalRangeChange(redraw);
  exploreChart.timeScale().subscribeSizeChange(redraw);
  window.addEventListener("resize", redraw);
  // TV-style OHLC strip (top-left over chart)
  wireOhlcLegend(exploreChart, exploreSeries, $("#explore-badge-ohlc"), { hideWhenEmpty: false });
  const ov = $("#explore-overlay");
  if (ov && !ov.dataset.wired) {
    ov.dataset.wired = "1";
    ov.addEventListener("click", (e) => {
      const rect = ov.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      // Prefer smallest containing hit (tightest box).
      let best = null;
      for (const h of exploreHitRects) {
        if (x >= h.x0 && x <= h.x1 && y >= h.y0 && y <= h.y1) {
          if (!best || (h.x1 - h.x0) < (best.x1 - best.x0)) best = h;
        }
      }
      if (best) focusExploreBox(best.id);
    });
  }
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
    exploreVol.setData(d.candles.map((c) =>
      (_C && _C.volPoint(c)) || {
        time: c.time, value: c.volume,
        color: c.close >= c.open ? "rgba(5,150,105,0.40)" : "rgba(220,38,38,0.35)",
      }
    ));
    addChartMaSeries(exploreChart, d, exploreEmas);
    exploreBoxes = d.dense_boxes || [];
    exploreState.lastCandles = d.candles;
    exploreState.lastEmas = chartMaMap(d);
    clearExploreFocusLines();
    // TV-like default: last ~120 bars, not full history crammed
    showLastBars(exploreChart, 120, (d.candles || []).length);
    applyExploreVisibility();
    // setTimeout (not only rAF): layout may settle after a frame
    requestAnimationFrame(() => drawExploreBoxes());
    setTimeout(() => drawExploreBoxes(), 60);
    setTimeout(() => drawExploreBoxes(), 200);
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

/** Map unix/business time → x via LWC; fall back to logical index. */
function exploreTimeToX(t) {
  if (!exploreChart || !exploreSeries) return null;
  let x = exploreSeries.timeToCoordinate(t);
  if (x != null) return x;
  const idx = exploreTimeIndex.has(t) ? exploreTimeIndex.get(t) : exploreTimes.findIndex((x) => x >= t);
  if (idx < 0) return null;
  try {
    return exploreChart.timeScale().logicalToCoordinate(idx);
  } catch (_) {
    return null;
  }
}

/** Hummingbot-style markers: start ▲ + tip-edge ◆ (right of dense window). */
function applyExploreMarkers() {
  if (!exploreSeries) return;
  if (!exploreState.showBoxes || !exploreBoxes.length) {
    try { exploreSeries.setMarkers([]); } catch (_) {}
    return;
  }
  const markers = [];
  for (const b of exploreBoxes) {
    const focused = exploreState.focusId === b.id;
    markers.push({
      time: b.t0,
      position: "belowBar",
      color: focused ? "#fbbf24" : "#1E90FF",
      shape: "arrowUp",
      text: focused ? `#${b.id}` : String(b.id),
    });
    if (b.t1 && b.t1 !== b.t0) {
      markers.push({
        time: b.t1,
        position: "aboveBar",
        color: focused ? "#fbbf24" : "#f472b6",
        shape: "circle",
        text: focused ? "tip" : "",
      });
    }
  }
  markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
  try { exploreSeries.setMarkers(markers); } catch (_) {}
}

/**
 * Canvas dense-box overlay synced to #explore-chart (LWC origin).
 * Full-zoom: expand by half barSpacing + min w/h; hide labels when too narrow.
 * See docs/learnings/chart-overlay-boxes-need-min-size-at-full-zoom.md
 */
function drawExploreBoxes() {
  applyExploreMarkers();
  const canvas = $("#explore-overlay");
  const chartEl = $("#explore-chart");
  if (!canvas || !chartEl || !exploreChart || !exploreSeries) return;
  exploreHitRects = [];
  if (!exploreState.showBoxes || !exploreBoxes.length) {
    canvas.width = 0;
    canvas.height = 0;
    canvas.style.display = "none";
    return;
  }
  canvas.style.display = "block";
  const w = chartEl.clientWidth;
  const h = chartEl.clientHeight;
  if (w < 8 || h < 8) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.style.left = `${chartEl.offsetLeft}px`;
  canvas.style.top = `${chartEl.offsetTop}px`;
  canvas.style.width = `${w}px`;
  canvas.style.height = `${h}px`;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  let barSpacing = 6;
  try {
    const opts = exploreChart.timeScale().options();
    if (opts && opts.barSpacing) barSpacing = opts.barSpacing;
  } catch (_) {}
  const padX = Math.max(barSpacing * 0.5, 2);
  const minW = Math.max(10, barSpacing * 2.5);
  const minH = 14;
  const showLabels = barSpacing >= 3.2;

  for (const b of exploreBoxes) {
    const x0raw = exploreTimeToX(b.t0);
    const x1raw = exploreTimeToX(b.t1);
    if (x0raw == null || x1raw == null) continue;
    let yHi = exploreSeries.priceToCoordinate(b.hi);
    let yLo = exploreSeries.priceToCoordinate(b.lo);
    if (yHi == null || yLo == null) continue;
    let x0 = Math.min(x0raw, x1raw) - padX;
    let x1 = Math.max(x0raw, x1raw) + padX;
    let y0 = Math.min(yHi, yLo);
    let y1 = Math.max(yHi, yLo);
    if (x1 - x0 < minW) {
      const mid = (x0 + x1) / 2;
      x0 = mid - minW / 2;
      x1 = mid + minW / 2;
    }
    if (y1 - y0 < minH) {
      const mid = (y0 + y1) / 2;
      y0 = mid - minH / 2;
      y1 = mid + minH / 2;
    }
    const focused = exploreState.focusId === b.id;
    const bw = x1 - x0;
    const bh = y1 - y0;
    ctx.fillStyle = focused ? "rgba(251,191,36,0.22)" : "rgba(45,212,191,0.14)";
    ctx.strokeStyle = focused ? "rgba(251,191,36,0.95)" : "rgba(45,212,191,0.75)";
    ctx.lineWidth = focused ? 2 : (barSpacing < 2 ? 1.5 : 1);
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x0, y0, bw, bh, 3);
    else ctx.rect(x0, y0, bw, bh);
    ctx.fill();
    ctx.stroke();
    // Tip-edge hatch on the right ~20% of the box (visual cue for tip candidate).
    const tipW = Math.max(4, bw * 0.2);
    ctx.fillStyle = focused ? "rgba(251,191,36,0.28)" : "rgba(244,114,182,0.22)";
    ctx.fillRect(x1 - tipW, y0, tipW, bh);

    exploreHitRects.push({ id: b.id, x0, x1, y0, y1 });

    if (showLabels || focused) {
      const label = `#${b.id}`;
      ctx.font = focused ? "bold 11px sans-serif" : "10px sans-serif";
      const tw = ctx.measureText(label).width;
      if (focused || tw + 6 < bw) {
        ctx.fillStyle = focused ? "#fbbf24" : "rgba(45,212,191,0.95)";
        ctx.fillText(label, x0 + 3, Math.max(11, y0 - 3));
      }
    }
  }
}

function focusExploreBox(id) {
  const b = exploreBoxes.find((x) => x.id === id);
  if (!b || !exploreChart || !exploreSeries) return;
  exploreState.focusId = id;
  highlightExploreRow(id);
  clearExploreFocusLines();
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
  drawExploreBoxes();
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

/* ---------- status strip (owner detector / judgment / forward) ---------- */
function paintLiveTruth(od, ja, fw, paperLive) {
  const n = fw.decision_trades ?? 0;
  const target = fw.decision_target ?? 100;
  const prog = Math.round(100 * (fw.progress || 0));
  const countEl = $("#live-fwd-count");
  const barEl = $("#live-fwd-bar");
  const noteEl = $("#live-fwd-note");
  if (countEl) countEl.textContent = `${n} / ${target}`;
  if (barEl) barEl.style.width = `${prog}%`;
  if (noteEl) {
    if (fw.stall_reason) noteEl.textContent = fw.stall_reason;
    else if (n >= target) noteEl.textContent = "裁决样本已满 · 可做终审";
    else noteEl.textContent = `还差 ${fw.decision_remaining ?? target - n} 笔新鲜 maker 成交 · lag≤${fw.fresh_detect_min ?? 30}min`;
  }
  const det = $("#live-det");
  if (det) {
    const lab = od.label || od.source_run;
    det.textContent = lab
      ? String(lab).replace(/^owner_/, "").replace(/_star/, "")
      : (od.exists ? "已加载" : "缺失");
  }
  const jud = $("#live-jud");
  if (jud) {
    const thr = ja.threshold_val_q90 != null ? Number(ja.threshold_val_q90).toFixed(4) : "—";
    jud.textContent = ja.exists ? `阈值 ${thr}` : "未设置";
  }
  const log = $("#live-log");
  if (log) {
    const hs = fw.hindsight_excluded ?? 0;
    log.textContent = `open ${fw.open_rows ?? 0} · closed ${fw.closed_rows ?? 0} · 事后排除 ${hs}`;
  }
  // v10 paper live (sim) line
  const paperEl = $("#live-paper");
  if (paperEl) {
    const pl = paperLive || {};
    if (pl && pl.available) {
      paperEl.textContent = `${pl.n_fresh || 0}新/${pl.n_fired || 0}总`;
      paperEl.title = pl.scanned_at ? `最近扫描 ${String(pl.scanned_at).slice(0, 16)} UTC` : "";
    } else {
      paperEl.textContent = "无";
      paperEl.title = "尚未跑 live_signal_tg.py --send";
    }
  }
  const hintWrap = $("#live-hint-wrap");
  const hint = $("#live-hint");
  if (hintWrap && hint) {
    if (fw.stall_reason && n === 0) {
      hintWrap.hidden = false;
      hint.textContent = fw.stall_reason;
    } else {
      hintWrap.hidden = true;
    }
  }
  const truth = $("#live-truth");
  if (truth) {
    truth.classList.toggle("is-empty", n === 0);
    truth.classList.toggle("is-ready", n >= target);
  }
}

function ensureFourChips() {
  const strip = document.getElementById("status-strip");
  if (!strip) return;
  // Force layout to 4 columns (defensive against any old CSS rule)
  strip.style.display = "grid";
  strip.style.gridTemplateColumns = "repeat(4, minmax(120px, 1fr))";
  const wanted = [
    {id:"status-owner",   k:"检测器",      v:"…"},
    {id:"status-judgment", k:"判断 ACTIVE", v:"…"},
    {id:"status-forward",  k:"前向",        v:"…"},
    {id:"status-paper",    k:"v10纸面",     v:"…"},
  ];
  wanted.forEach(w => {
    if (!document.getElementById(w.id)) {
      const div = document.createElement("div");
      div.className = "status-item skeleton";
      div.id = w.id;
      div.innerHTML = `<span class="status-k">${w.k}</span><span class="status-v">${w.v}</span>`;
      strip.appendChild(div);
    }
  });
}

// Nuclear option: no matter what the initial HTML had (even 3 chips from a cached old index.html),
// rewrite the strip to contain exactly the 4 we want, then populate paper from its API.
function normalizeStatusStrip() {
  const strip = document.getElementById("status-strip");
  if (!strip) return;
  strip.style.display = "grid";
  strip.style.gridTemplateColumns = "repeat(4, minmax(120px, 1fr))";
  const order = ["status-owner", "status-judgment", "status-forward", "status-paper"];
  const labels = { "status-owner": "检测器", "status-judgment": "判断 ACTIVE", "status-forward": "前向", "status-paper": "v10纸面" };
  // Ensure all 4 exist as direct children
  order.forEach(id => {
    if (!document.getElementById(id)) {
      const d = document.createElement("div");
      d.className = "status-item skeleton";
      d.id = id;
      d.innerHTML = `<span class="status-k">${labels[id]}</span><span class="status-v">…</span>`;
      strip.appendChild(d);
    }
  });
  // If there are more than 4 direct .status-item, keep only the wanted ones (in order)
  const kids = Array.from(strip.children).filter(el => el.classList && el.classList.contains("status-item"));
  if (kids.length > 4) {
    const have = new Set(order);
    kids.forEach(el => { if (el.id && !have.has(el.id)) el.remove(); });
  }
  // Populate the paper chip value right away from its dedicated endpoint
  const p = document.getElementById("status-paper");
  if (p) {
    fetch("/api/live-paper", { cache: "no-store" }).then(r => r.json()).then(j => {
      if (!p || !p.isConnected) return;
      p.classList.remove("skeleton");
      if (j && j.available) {
        const nf = j.n_fresh || 0, nt = j.n_fired || 0;
        p.innerHTML = `<span class="status-k">v10纸面</span><span class="status-v">${nf}新/${nt}总</span><span class="status-sub">门${j.gate_min||30}m</span>`;
        p.classList.add("status-click");
        p.onclick = () => { location.hash = "#overview"; };
      } else {
        p.innerHTML = `<span class="status-k">v10纸面</span><span class="status-v">无</span><span class="status-sub">未扫描</span>`;
      }
    }).catch(() => {
      if (p && p.isConnected) p.innerHTML = `<span class="status-k">v10纸面</span><span class="status-v">—</span><span class="status-sub">见总览</span>`;
    });
  }
}

function ensurePaperChip() { return document.getElementById("status-paper"); }

function fillPaperStatusChip(paperEl, pl) {
  if (!paperEl) return;
  paperEl.classList.remove("skeleton");
  if (pl && pl.available) {
    paperEl.classList.add("status-click");
    paperEl.dataset.jump = "overview";
    paperEl.title = "点此查看总览上的 v10 纸面信号";
    const nf = pl.n_fresh || 0;
    const nt = pl.n_fired || 0;
    paperEl.innerHTML = `<span class="status-k">v10纸面</span>
      <span class="status-v">${nf}新/${nt}总</span>
      <span class="status-sub">门${pl.gate_min || 30}m</span>`;
    paperEl.classList.toggle("good", nf > 0);
    paperEl.classList.toggle("warn", nf === 0 && nt > 0);
  } else {
    paperEl.innerHTML = `<span class="status-k">v10纸面</span><span class="status-v">无</span><span class="status-sub">未扫描</span>`;
  }
}

async function loadStatusStrip(force = false) {
  if (force) {
    for (const [k] of [..._jsonCache.keys()]) {
      if (k.includes("/api/status-strip") || k.includes("/api/live-paper")) _jsonCache.delete(k);
    }
  }
  // Always ensure exactly 4 chips exist in the strip, on every page and every refresh.
  if (typeof ensureFourChips === "function") ensureFourChips();
  try {
    let d = {};
    try {
      d = await apiGet("/api/status-strip", { cache: !force, quiet: true });
    } catch (_) {
      d = {};
    }
    if (typeof ensureFourChips === "function") ensureFourChips();
    const od = d.owner_detector || {};
    const ja = d.judgment_active || {};
    const fw = d.forward || {};
    const fr = d.freshness || {};
    const tr = d.train || {};
    const tip = d.tip_pulse || {};
    const ownerEl = $("#status-owner");
    const judEl = $("#status-judgment");
    const fwdEl = $("#status-forward");
    const paperEl = ensurePaperChip();
    const metaEl = $("#status-meta");

    if (ownerEl) {
      ownerEl.classList.remove("skeleton");
      ownerEl.classList.add("status-click");
      ownerEl.dataset.jump = "probe";
      ownerEl.title = od.note || "点此打开盘口检测";
      const f1 = od.frozen_eval_f1 != null ? Number(od.frozen_eval_f1).toFixed(3) : null;
      const run = od.label || od.source_run || "—";
      const shortRun = String(run).replace(/^owner_/, "").replace(/_star/, "");
      const interim = !!od.interim || /v10|short_star/i.test(String(run));
      ownerEl.classList.toggle("good", !!od.exists && !interim && (od.frozen_eval_f1 || 0) >= 0.6);
      ownerEl.classList.toggle("warn", !od.exists || interim);
      const sub = !od.exists
        ? "空转"
        : (interim
          ? (f1 ? `F1 ${f1} · 临时 L1` : "临时 L1 · tip 扫描")
          : (f1 ? `F1 ${f1} · 冻结评测` : "主线检测"));
      ownerEl.innerHTML = `<span class="status-k">检测器</span>
        <span class="status-v">${escapeHtml(shortRun)}</span>
        <span class="status-sub">${escapeHtml(sub)}</span>`;
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
      fwdEl.classList.add("status-click");
      fwdEl.dataset.jump = "forward";
      fwdEl.title = "点此打开前向裁决";
      const n = fw.decision_trades ?? 0;
      const target = fw.decision_target ?? 100;
      const prog = Math.round(100 * (fw.progress || 0));
      fwdEl.classList.toggle("good", n >= target);
      fwdEl.classList.toggle("warn", n === 0);
      const openN = fw.open_rows ?? 0;
      const totalN = fw.total_rows ?? 0;
      const hs = fw.hindsight_excluded ?? 0;
      const stall = fw.stall_reason || "";
      fwdEl.innerHTML = `<span class="status-k">前向裁决</span>
        <span class="status-v">${n} / ${target}（${prog}%）</span>
        <span class="status-bar-mini"><span style="width:${prog}%"></span></span>
        <span class="status-sub" title="${escapeHtml(stall)}">open ${openN} · 事后 ${hs} · 日志 ${totalN}${stall ? " · " + escapeHtml(stall) : ""}</span>`;
    }

    // Prefer dedicated endpoint so chip works even if status-strip is stale.
    let pl = (d && d.paper_live) || null;
    try {
      const pp = await apiGet("/api/live-paper", { cache: false, quiet: true });
      if (pp && pp.available) pl = pp;
    } catch (_) { /* non-fatal */ }
    fillPaperStatusChip(paperEl, pl);

    if (metaEl) {
      const g = fr.gate_min ?? fw.fresh_detect_min ?? 30;
      const ep = tr.epoch;
      const tgt = tr.epochs_target ?? 40;
      const done = tr.status === "done" || tr.stable_pt;
      const alive = !!tr.alive;
      let trainTxt = "v13 —";
      if (done) trainTxt = "v13 已落盘";
      else if (ep != null) trainTxt = `v13 ${ep}/${tgt}${alive ? "" : " · idle"}`;
      const tf = tip.tip_fire;
      const tipTxt = tf != null ? `tip ${tf}` : "tip —";
      const paperTxt = pl && pl.available ? `v10纸面 ${pl.n_fresh || 0}新/${pl.n_fired || 0}总` : "v10纸面 无";
      metaEl.innerHTML = [
        `lag≤${g}m`,
        trainTxt,
        tipTxt,
        paperTxt,
        `<a href="/debug_viz.html">调试</a>`,
      ].join(`<span class="sep">·</span>`);
      metaEl.title = [fr.note, tr.note, tip.note, (pl && pl.label) || ""].filter(Boolean).join(" · ");
    }
    paintLiveTruth(od, ja, fw, pl);
    if (force) toast("状态条已刷新", "ok");
  } catch (_) {
    if (typeof ensureFourChips === "function") ensureFourChips();
    const o = document.getElementById("status-owner");
    if (o) { o.classList.remove("skeleton"); o.innerHTML = '<span class="status-k">检测器</span><span class="status-v">暂不可用</span>'; }
    const j = document.getElementById("status-judgment");
    if (j) { j.classList.remove("skeleton"); j.innerHTML = '<span class="status-k">判断 ACTIVE</span><span class="status-v">暂不可用</span>'; }
    const f = document.getElementById("status-forward");
    if (f) { f.classList.remove("skeleton"); f.innerHTML = '<span class="status-k">前向</span><span class="status-v">暂不可用</span>'; }
    const p = document.getElementById("status-paper");
    if (p) {
      p.classList.remove("skeleton");
      p.innerHTML = '<span class="status-k">v10纸面</span><span class="status-v">—</span><span class="status-sub">见总览</span>';
      apiGet("/api/live-paper", { cache: false, quiet: true }).then((pp) => {
        fillPaperStatusChip(p, pp);
      }).catch(() => {});
    }
    const metaEl = $("#status-meta");
    if (metaEl) metaEl.textContent = "状态暂不可用";
  }
}

/* ---------- labeling hub ---------- */
async function loadLabelingHub() {
  const view = $("#view-labeling");
  view?.classList.add("loading");
  try {
    const d = await apiGet("/api/labeling-hub", { cache: false });
    const s = d.summary || {};
    $("#label-summary").innerHTML = `
      <div class="tile"><span class="lbl">网站入口</span><b>${s.n_sites ?? 0}</b><small>hub.json 可改</small></div>
      <div class="tile"><span class="lbl">最新轮次</span><b>${s.latest_round != null ? "R" + s.latest_round : "—"}</b><small>manifest</small></div>
      <div class="tile"><span class="lbl">任务包</span><b>${s.n_packs ?? 0}</b><small>tasks_*.json</small></div>
      <div class="tile"><span class="lbl">审计页</span><b>${s.n_audits ?? 0}</b><small>静态 HTML</small></div>`;
    $("#label-account-hint").textContent = d.account_hint || "";

    const roleBadge = (role) => {
      const map = { primary: "主", tunnel: "隧道", local: "本机" };
      return map[role] || role || "";
    };
    $("#label-sites").innerHTML = (d.sites || []).length
      ? (d.sites || []).map((site) => `
        <a class="link-card ${site.role === "primary" ? "primary" : ""}" href="${escapeHtml(site.url)}" target="_blank" rel="noopener">
          <div class="link-card-top">
            <strong>${escapeHtml(site.name || site.url)}</strong>
            ${site.role ? `<span class="chip ${site.role === "primary" ? "passed" : "done"}">${escapeHtml(roleBadge(site.role))}</span>` : ""}
          </div>
          <div class="link-card-url">${escapeHtml(site.url)}</div>
          <div class="link-card-note">${escapeHtml(site.note || "")}</div>
        </a>`).join("")
      : `<div class="empty-state">暂无入口，编辑 output/label_studio/hub.json</div>`;

    $("#label-audits").innerHTML = (d.audits || []).map((a) => `
      <a class="link-row ${a.exists ? "" : "missing"}" href="${escapeHtml(a.url)}" target="_blank" rel="noopener">
        <span class="link-row-name">${escapeHtml(a.name)}</span>
        <span class="link-row-meta">${a.exists ? (a.size_kb != null ? a.size_kb + " KB" : "打开") : "缺失"} · ${escapeHtml(a.note || "")}</span>
      </a>`).join("") || `<div class="empty-state">无审计页</div>`;

    $("#label-maintain").innerHTML = (d.maintain || []).map((m) => `
      <div class="maintain-item">
        <b>${escapeHtml(m.title || "")}</b>
        <p>${escapeHtml(m.body || "")}</p>
      </div>`).join("") || "";

    const mbody = $("#label-manifest-table tbody");
    if (mbody) {
      $("#label-manifest-count").textContent = (d.manifests || []).length
        ? `（${(d.manifests || []).length}）` : "";
      mbody.innerHTML = (d.manifests || []).length
        ? (d.manifests || []).map((m) => `
          <tr class="no-click">
            <td><b>R${escapeHtml(String(m.round ?? "—"))}</b> <span class="note">${escapeHtml(m.file || "")}</span></td>
            <td class="num">${m.count ?? "—"}</td>
            <td class="num">${m.chunks ?? "—"}</td>
            <td class="note">${escapeHtml(m.weights || "—")}</td>
            <td class="num">${m.seed ?? "—"}</td>
          </tr>`).join("")
        : `<tr class="no-click"><td colspan="5" class="empty-state">暂无 round*_manifest.json</td></tr>`;
    }

    const pbody = $("#label-pack-table tbody");
    if (pbody) {
      $("#label-pack-count").textContent = (d.packs || []).length
        ? `（显示 ${(d.packs || []).length}）` : "";
      pbody.innerHTML = (d.packs || []).length
        ? (d.packs || []).map((p) => `
          <tr class="no-click">
            <td title="${escapeHtml(p.path || "")}"><code>${escapeHtml(p.file)}</code></td>
            <td class="num">${p.n_tasks ?? "—"}</td>
            <td class="num">${p.size_mb != null ? p.size_mb + " MB" : "—"}</td>
            <td class="note">${escapeHtml(p.mtime || "")}</td>
          </tr>`).join("")
        : `<tr class="no-click"><td colspan="4" class="empty-state">output/label_studio 下无 tasks_*.json（需 rsync 到 VPS）</td></tr>`;
    }
  } catch (err) {
    if (err?.name !== "AbortError") {
      toast(`打标页：${err.message || err}`);
      $("#label-sites").innerHTML = `<div class="empty-state neg">加载失败：${escapeHtml(String(err.message || err))}</div>`;
    }
  } finally {
    view?.classList.remove("loading");
  }
}
$("#label-refresh")?.addEventListener("click", () => loadLabelingHub());

/* ---------- overview (architecture + live truth + tip-replay) ---------- */
let sparkChart = null, sparkSeries = null;
/** last status-strip snapshot for architecture paint */
let _lastStatusStrip = null;

/**
 * Build architecture cards + detail table from status-strip + overview.
 * Two-layer model (YOLO detect + LightGBM judge) + execution + verdict.
 */
function paintArchitecture(strip, overview, paper) {
  const od = (strip && strip.owner_detector) || {};
  const ja = (strip && strip.judgment_active) || {};
  const fw = (strip && strip.forward) || {};
  const fr = (strip && strip.freshness) || {};
  const tr = (strip && strip.train) || {};
  const pl = paper || (strip && strip.paper_live) || {};

  // --- L1 detector (owner 2026-07-31: short_star_v10 interim) ---
  const detLiveOk = !!od.exists;
  const detInterim = !!od.interim || /v10|short_star/i.test(String(od.label || od.source_run || ""));
  const detName = detLiveOk
    ? String(od.label || od.source_run || "owner_best")
        .replace(/^owner_/, "")
        .replace(/_star/, "")
    : "none";
  const detState = detLiveOk ? (detInterim ? "warn" : "ok") : (pl.available ? "warn" : "off");
  const detBadge = !detLiveOk ? (pl.available ? "纸面" : "空转")
    : (detInterim ? "v10 临时" : "ACTIVE");
  const detVer = detLiveOk ? detName : (pl.available ? "v10 纸面" : "detector=none");
  const detSub = detLiveOk
    ? (detInterim
      ? `tip 扫描 · conf 0.30 · 非 tip-smoke 金标 · ${od.note ? "见明细" : "判断仍 v11"}`
      : `F1 ${od.frozen_eval_f1 != null ? Number(od.frozen_eval_f1).toFixed(3) : "—"} · tip-only`)
    : (pl.available
      ? `纸面 ${pl.n_fresh || 0}新/${pl.n_fired || 0}总 · conf ${Number(pl.conf || 0.3).toFixed(2)} · 不写账`
      : (od.note || "无检测权重 · 管道空转"));

  // --- L2 judgment ---
  const judOk = !!ja.exists;
  const art = String(ja.artifact_id || "");
  // Prefer human tag: v11_reg from frozen_…_yolo_v11_reg_…
  let judTag = "—";
  const mV = art.match(/v(\d+)(?:_reg)?/i);
  if (mV) judTag = `v${mV[1]}${/reg/i.test(art) ? " reg" : ""}`;
  else if (art) judTag = art.replace(/^frozen_/, "").slice(0, 22);
  const thr = ja.threshold_val_q90 != null ? Number(ja.threshold_val_q90).toFixed(4) : "—";
  const obj = ja.objective || ( /reg/i.test(art) ? "regression" : "—");
  const judVer = judOk ? judTag : "未挂 ACTIVE";
  const judSub = judOk
    ? `阈值 ${thr} · ${obj} · ${ja.dataset_name || "—"}`
    : (ja.note || "models/ACTIVE 未设置");
  const judState = judOk ? "ok" : "off";
  const judBadge = judOk ? "ACTIVE" : "OFF";

  // --- L3 execution ---
  const gate = fr.gate_min ?? fw.fresh_detect_min ?? 30;
  const n = fw.decision_trades ?? 0;
  const target = fw.decision_target ?? 100;
  const execVer = pl.available ? "纸面 + 前向" : "前向日志";
  const execSub = `新鲜门 ${gate}min · 日志 ${fw.total_rows ?? 0} · 事后剔 ${fw.hindsight_excluded ?? 0} · 不自动 promote`;
  const execState = (fw.exists || pl.available) ? (n > 0 ? "ok" : "warn") : "off";
  const execBadge = n > 0 ? "采集中" : "待命";

  // --- Verdict ---
  const pfTile = (overview?.tiles || []).find((t) => /PF/i.test(String(t.label || "")));
  const pfStr = pfTile ? String(pfTile.value) : "—";
  const verdVer = `${n} / ${target}`;
  const verdSub = `tip-replay PF ${pfStr} · 确认只认前向新鲜`;
  const verdState = n >= target ? "ok" : (n > 0 ? "warn" : "off");
  const verdBadge = n >= target ? "满额" : "进行中";

  const lede = $("#arch-lede");
  if (lede) {
    lede.textContent = [
      "L1 YOLO 形态检测 → L2 LightGBM 打分 → L3 执行/记账 → 前向 100 笔终审。",
      detLiveOk ? `检测实盘 ${detName}` : "检测实盘未挂（纸面 v10 旁路）",
      judOk ? `判断 ACTIVE ${judTag}` : "判断未挂",
    ].join(" · ");
  }
  const meta = $("#arch-meta");
  if (meta) {
    meta.innerHTML = [
      `<span class="arch-pill">15m · SWAP</span>`,
      `<span class="arch-pill">tip-only</span>`,
      `<span class="arch-pill">TP5 / SL2</span>`,
      `<span class="arch-pill">fresh ≤${gate}m</span>`,
      judOk ? `<span class="arch-pill">阈 ${thr}</span>` : "",
    ].filter(Boolean).join("");
  }

  const card = (o) => `
    <div class="arch-card state-${o.state}${o.jump ? " clickable" : ""}"
         ${o.jump ? `data-jump="${escapeHtml(o.jump)}" role="button" tabindex="0"` : ""}
         title="${escapeHtml(o.title || "")}">
      <span class="arch-badge ${o.state}">${escapeHtml(o.badge)}</span>
      <span class="arch-layer">${escapeHtml(o.layer)}</span>
      <span class="arch-name">${escapeHtml(o.name)}</span>
      <b class="arch-ver">${escapeHtml(o.ver)}</b>
      <small class="arch-sub">${escapeHtml(o.sub)}</small>
    </div>`;

  const flow = $("#arch-flow");
  if (flow) {
    flow.innerHTML = [
      card({
        layer: "L1 · 2a", name: "检测层 YOLO", ver: detVer, sub: detSub,
        state: detState, badge: detBadge, jump: "probe",
        title: od.weights_path || od.note || "检测层",
      }),
      `<div class="arch-arrow" aria-hidden="true">→</div>`,
      card({
        layer: "L2 · 2b", name: "判断层 LightGBM", ver: judVer, sub: judSub,
        state: judState, badge: judBadge, jump: "signals",
        title: ja.artifact_id || "判断层 ACTIVE",
      }),
      `<div class="arch-arrow" aria-hidden="true">→</div>`,
      card({
        layer: "L3", name: "执行层", ver: execVer, sub: execSub,
        state: execState, badge: execBadge, jump: "forward",
        title: "forward_log · paper live · 无自动下单",
      }),
      `<div class="arch-arrow" aria-hidden="true">→</div>`,
      card({
        layer: "裁决", name: "前向终审", ver: verdVer, sub: verdSub,
        state: verdState, badge: verdBadge, jump: "forward",
        title: "确认级只认前向新鲜 100 笔",
      }),
    ].join("");
    // clickable cards
    flow.querySelectorAll(".arch-card.clickable").forEach((el) => {
      el.addEventListener("click", () => {
        const j = el.dataset.jump;
        if (j) showView(j);
      });
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          const j = el.dataset.jump;
          if (j) showView(j);
        }
      });
    });
  }

  // detail table
  const tbody = $("#arch-tbody");
  if (tbody) {
    const tag = (state, text) => `<span class="tag ${state}">${escapeHtml(text)}</span>`;
    const rows = [
      {
        layer: "L1 检测",
        role: "YOLO 盘口 tip 形态 · 只扫 tip/tip-1/tip-2",
        ver: detLiveOk
          ? `${detName}${od.weights_path ? ` · ${od.weights_path}` : ""}`
          : "未挂权重（detector=none）",
        status: detLiveOk
          ? tag(detInterim ? "warn" : "ok", detInterim ? "v10 临时" : "实盘在线")
          : tag("warn", pl.available ? "仅纸面 v10" : "空转"),
        note: detLiveOk
          ? (od.note || od.eval_set || "晋升门 = 真 tip 金标 + tip-smoke")
          : (pl.available
            ? `旁路：scripts/live_signal_tg.py · ${pl.n_fresh || 0}/${pl.n_fired || 0} 新鲜 · 不写 forward`
            : (od.note || "无可用权重")),
      },
      {
        layer: "L2 判断",
        role: "LightGBM 排序 / 阀门 · TP5/SL2 障碍",
        ver: judOk
          ? `${art || judTag} · 阈 ${thr}`
          : "—",
        status: judOk ? tag("ok", "ACTIVE") : tag("off", "未挂"),
        note: judOk
          ? `${obj} · ${ja.dataset_name || ja.dataset_path || "—"} · ${String(ja.created_at || "").slice(0, 10)}`
          : "models/ACTIVE 指针不存在",
      },
      {
        layer: "L3 执行",
        role: "前向记账 · 纸面模拟 ·（真金需 owner）",
        ver: `forward_log · 门 ${gate}min`,
        status: tag(execState === "ok" ? "ok" : "warn", execBadge),
        note: `裁决样本 ${n}/${target} · closed ${fw.closed_rows ?? 0} · 事后剔 ${fw.hindsight_excluded ?? 0} · 不自动 promote`,
      },
      {
        layer: "旁路 / 研究",
        role: "不进主线裁决",
        ver: [
          pl.available ? "v10 纸面扫描" : null,
          tr.name || tr.stable_pt ? (tr.name || "train artifact") : null,
          "多周期雷达（规则，非 YOLO/LGB）",
        ].filter(Boolean).join(" · ") || "—",
        status: tag("warn", "非主线"),
        note: "纸面/雷达/研究权重不得自动写入 forward 或 promote ACTIVE",
      },
    ];
    tbody.innerHTML = rows.map((r) => `
      <tr>
        <td><b>${escapeHtml(r.layer)}</b></td>
        <td>${escapeHtml(r.role)}</td>
        <td class="mono">${escapeHtml(r.ver)}</td>
        <td>${r.status}</td>
        <td>${escapeHtml(r.note)}</td>
      </tr>`).join("");
  }

  const foot = $("#arch-foot");
  if (foot) {
    foot.textContent = [
      "铁律：不自动 promote · holdout 需 owner 批准 · 检测只认盘口 tip",
      `三门新鲜度同值 ${gate}min`,
      "确认级只认前向新鲜 100 笔",
    ].join(" · ");
  }
}

async function loadOverview() {
  $("#view-overview")?.classList.add("loading");
  let strip = null;
  let paper = null;
  try {
    // Refresh strip so live-truth panel matches current forward clock.
    await loadStatusStrip(false);
    try {
      strip = await apiGet("/api/status-strip", { cache: false, quiet: true });
      _lastStatusStrip = strip;
    } catch (_) {
      strip = _lastStatusStrip;
    }
    try {
      paper = await apiGet("/api/live-paper", { cache: false, quiet: true });
    } catch (_) { /* optional */ }

    const d = await apiGet(apiUrl("/api/overview"), { cache: false });
    paintArchitecture(strip || {}, d, paper);

    // Strip legacy look-ahead parenthetical so the banner never re-sells PF 6.x.
    let verdictRaw = String(d.verdict || "暂无摘要");
    verdictRaw = verdictRaw.replace(/（旧[^）]*）/g, "").replace(/\(旧[^)]*\)/g, "").trim();
    const v = escapeHtml(verdictRaw);
    const n = d.next ? `<span class="banner-next">${escapeHtml(d.next)}</span>` : "";
    $("#verdict-banner").innerHTML = `<span class="banner-k">回测终审</span><b>${v}</b>${n}`;
    const tile = (t) =>
      `<div class="tile"><span class="tile-code">EVIDENCE</span><span class="lbl">${escapeHtml(t.label)}</span><b>${escapeHtml(String(t.value))}</b><small>${escapeHtml(t.sub || "")}</small></div>`;
    const tiles = d.tiles || [];
    $("#tiles").innerHTML = tiles.length
      ? tiles.map(tile).join("")
      : `<div class="empty-state">暂无关键指标 · 检查 /api/overview</div>`;
    const names = {
      net_positive: "扣费后净收益为正",
      "profit_factor_ge_1.3": "盈亏比 PF ≥ 1.3",
      max_drawdown_le_20pct: "最大回撤 ≤ 20%（tip-replay 未测）",
      n_trades_ge_100: "交易数 ≥ 100 笔",
    };
    const acc = Object.entries(d.acceptance || {});
    $("#acceptance").innerHTML = acc.length
      ? acc.map(([k, ok]) =>
          `<li class="${ok ? "ok" : "fail"}"><span class="check-mark" aria-hidden="true">${ok ? "✓" : "○"}</span>${names[k] || k}</li>`
        ).join("")
      : `<li class="fail"><span class="check-mark" aria-hidden="true">○</span>暂无 tip-replay 验收数据</li>`;
    // Honest tip-replay tiles only — never paint look-ahead equity (+245% era).
    const honestHost = $("#overview-honest-tiles");
    if (honestHost) {
      const honest = (d.tiles || []).filter((t) =>
        /tip-replay|PF|每笔净|胜率/i.test(String(t.label || "") + String(t.sub || ""))
      );
      const show = honest.length ? honest : (d.tiles || []).slice(1, 3);
      honestHost.innerHTML = show.length
        ? show.map(tile).join("")
        : `<div class="note">见上方磁贴 · 明细在「回测」页</div>`;
    }
    // Destroy any leftover spark chart from older builds still in memory.
    if (typeof sparkChart !== "undefined" && sparkChart) {
      try { sparkChart.remove(); } catch (_) { /* ignore */ }
      sparkChart = null;
      sparkSeries = null;
    }
  } catch (err) {
    if (err?.name !== "AbortError") {
      $("#verdict-banner").innerHTML = `<b class="neg">总览加载失败</b> ${escapeHtml(String(err.message || err))}`;
    }
  } finally {
    $("#view-overview")?.classList.remove("loading");
  }
  // v10 paper live signals (read-only)
  try { await loadLivePaperOverview(); } catch (_) {}
}

async function loadLivePaperOverview() {
  const host = $("#live-paper-body");
  if (!host) return;
  try {
    const d = await apiGet("/api/live-paper", { cache: false });
    if (!d || !d.available) {
      host.innerHTML = `无最近扫描。<br>运行：<code>python3 scripts/live_signal_tg.py --tip-only --send</code>`;
      return;
    }
    const fresh = d.n_fresh || 0;
    const tot = d.n_fired || 0;
    const gate = d.gate_min || 30;
    const ts = d.scanned_at ? String(d.scanned_at).slice(0,16) : "—";
    let html = `<b>${fresh} 新鲜 / ${tot} 总</b> · 门 ${gate}min · 扫描 ${ts} UTC · conf ${Number(d.conf||0.3).toFixed(2)}`;
    const hits = Array.isArray(d.hits) ? d.hits.slice(0, 6) : [];
    if (hits.length) {
      html += "<div style=\"margin-top:6px\">" + hits.map(h => {
        const f = h.fresh ? "<b style=\"color:#166534\">新鲜</b>" : "<span style=\"color:#854d0e\">超门</span>";
        const img = h.png_url ? `<a href=\"${escapeHtml(h.png_url)}\" target=\"_blank\" rel=\"noopener\">图</a>` : "";
        return `<span class=\"chip\">${escapeHtml(h.symbol)} conf ${Number(h.conf||0).toFixed(2)} ${f} ${img}</span>`;
      }).join(" ") + "</div>";
    }
    host.innerHTML = html;
  } catch (e) {
    if (host) host.textContent = "加载失败: " + (e && e.message ? e.message : e);
  }
}

// wire refresh button on overview
$("#live-paper-refresh")?.addEventListener("click", async () => {
  const host = $("#live-paper-body");
  if (host) host.textContent = "刷新中…";
  try { await loadLivePaperOverview(); } catch(_) {}
});

function _fmtNum(x, digits = 3) {
  if (x == null || x === "" || Number.isNaN(Number(x))) return "—";
  return Number(x).toFixed(digits);
}
function _linkList(links) {
  if (!links) return "";
  const items = [];
  for (const [k, v] of Object.entries(links)) {
    if (!v) continue;
    const s = String(v);
    // Only HTTP-mounted paths become clickable; *_path are repo-relative notes.
    if (s.startsWith("/debug-artifacts/") || s.startsWith("http")) {
      items.push(`<a href="${escapeHtml(s)}" target="_blank" rel="noopener">${escapeHtml(k)}</a>`);
    } else {
      items.push(`<code title="仓库路径">${escapeHtml(k)}: ${escapeHtml(s)}</code>`);
    }
  }
  return items.length ? items.join(" · ") : "—";
}

async function loadShortTf() {
  const view = $("#view-shorttf");
  view?.classList.add("loading");
  try {
    const d = await apiGet("/api/short-tf", { cache: false });
    if ($("#shorttf-sub")) $("#shorttf-sub").textContent = d.subtitle || "";
    if ($("#shorttf-note")) $("#shorttf-note").textContent = d.note || "";
    const disc = $("#shorttf-discipline");
    if (disc) {
      disc.innerHTML = (d.discipline || []).map((x) => `<li>${escapeHtml(x)}</li>`).join("");
    }
    const v1 = d.v1 || {};
    const v2 = d.v2 || {};
    const oos = v1.strict_oos || {};
    const val = v2.val || {};
    const gates = v2.gates || {};
    $("#shorttf-tiles").innerHTML = `
      <div class="tile"><span class="lbl">标的</span><b>ETH 3m</b><small>做空 short-start pilot</small></div>
      <div class="tile ${String(v1.verdict || "").includes("FAIL") ? "warn" : ""}"><span class="lbl">v1 检测</span><b>${escapeHtml(v1.verdict || "—")}</b><small>OOS 开火率 ${escapeHtml(oos.raw_fire_rate_pct || "—")}</small></div>
      <div class="tile ${String(v2.verdict || "").includes("FAIL") ? "warn" : ""}"><span class="lbl">v2 分类</span><b>${escapeHtml(v2.verdict || "—")}</b><small>val TP ${val.tp ?? "—"} / FP ${val.fp ?? "—"}</small></div>
      <div class="tile"><span class="lbl">纪律</span><b>未 promote</b><small>holdout 未动 · 不写 forward</small></div>`;

    if ($("#shorttf-v1-verdict")) $("#shorttf-v1-verdict").textContent = v1.verdict || "";
    if ($("#shorttf-v1-body")) {
      if (!v1.available) {
        $("#shorttf-v1-body").textContent = "无 v1 回测产物";
      } else {
        $("#shorttf-v1-body").innerHTML = `
          <div class="tf-row"><span>结论</span><b class="neg">${escapeHtml(v1.verdict || "—")}</b></div>
          <div class="tf-row"><span>训练图</span><b>${v1.training_images ?? "—"}</b></div>
          <div class="tf-row"><span>OOS 窗</span><b class="mono" style="font-size:12px">${escapeHtml(oos.window || "—")}</b></div>
          <div class="tf-row"><span>eligible bars</span><b>${oos.eligible_bars ?? "—"}</b></div>
          <div class="tf-row"><span>raw 开火 / 率</span><b>${oos.raw_fires ?? "—"} · ${escapeHtml(oos.raw_fire_rate_pct || "—")}</b></div>
          <div class="tf-row"><span>去重信号</span><b>${oos.dedup_signals ?? "—"}</b></div>
          <div class="tf-row"><span>净均值 @20bp</span><b class="neg">${escapeHtml(oos.net_mean_20bp_pct || "—")}</b></div>
          <div class="tf-row"><span>胜率 / PF</span><b>${escapeHtml(oos.net_win_rate_pct || "—")} · ${_fmtNum(oos.net_pf, 3)}</b></div>
          <div class="tf-row"><span>配对超额 t</span><b>${_fmtNum(oos.paired_excess_t, 2)}</b></div>
          <div class="tf-row"><span>协议</span><b>次根开盘 · 持有 60×3m · 成本 20bp · MIN_GAP 18</b></div>
          <p class="note" style="margin-top:8px">v1 在严格 OOS 上几乎每根 bar 都开火（99.7%），无法当稀疏事件策略。</p>`;
      }
    }
    if ($("#shorttf-v1-links")) $("#shorttf-v1-links").innerHTML = "链接：" + _linkList(v1.links);

    if ($("#shorttf-v2-verdict")) $("#shorttf-v2-verdict").textContent = v2.verdict || "";
    if ($("#shorttf-v2-body")) {
      if (!v2.available) {
        $("#shorttf-v2-body").textContent = "无 v2 诊断产物";
      } else {
        const tr = v2.train || {};
        const bl = v2.baseline_first_below || {};
        $("#shorttf-v2-body").innerHTML = `
          <div class="tf-row"><span>结论</span><b class="neg">${escapeHtml(v2.verdict || "—")}</b></div>
          <div class="tf-row"><span>阈值政策</span><b>${escapeHtml(v2.threshold_policy || "p=0.50 固定")}</b></div>
          <div class="tf-row"><span>train @0.50</span><b>TP${tr.tp ?? "—"}/FP${tr.fp ?? "—"}/TN${tr.tn ?? "—"}/FN${tr.fn ?? "—"}</b></div>
          <div class="tf-row"><span>val @0.50</span><b class="neg">TP${val.tp ?? "—"}/FP${val.fp ?? "—"}/TN${val.tn ?? "—"}/FN${val.fn ?? "—"}</b></div>
          <div class="tf-row"><span>val balanced acc</span><b>${_fmtNum(val.balanced_accuracy, 2)}</b></div>
          <div class="tf-row"><span>val ROC AUC</span><b>${_fmtNum(val.roc_auc, 3)}</b></div>
          <div class="tf-row"><span>预注册门</span><b class="neg">TP≥${gates.tp_min ?? 6} 实际 ${gates.actual_tp ?? "—"} · ${gates.passed ? "PASS" : "FAIL"}</b></div>
          <div class="tf-row"><span>因果规则基线</span><b>首次跌破六 MA · TP${bl.tp ?? "—"}/FP${bl.fp ?? "—"}/FN${bl.fn ?? "—"}</b></div>
          <div class="tf-row"><span>weights</span><b class="mono" style="font-size:11px">${escapeHtml(v2.weights_sha256 || "—")}</b></div>
          <p class="note" style="margin-top:8px">val 全判 no_start（TP=0）；top1≈多数类。规则基线明显好于图像模型。fail-fast 未跑 smoke/promote。</p>`;
      }
    }
    if ($("#shorttf-v2-links")) $("#shorttf-v2-links").innerHTML = "链接：" + _linkList(v2.links);
  } catch (err) {
    if (err?.name !== "AbortError") toast(`ETH 3m：${err.message || err}`);
  } finally {
    view?.classList.remove("loading");
  }
}
$("#shorttf-to-ethmicro")?.addEventListener("click", () => showView("ethmicro", { force: true }));

/* ---------- 盘口检测（单币一键探测, subprocess-backed） ---------- */
let probeRunning = false;

function renderProbeCards(r) {
  const host = $("#probe-cards");
  if (!host) return;
  const signals = r.signals || [];
  if (r.error) {
    host.hidden = false;
    host.innerHTML = `<div class="probe-card bad"><div class="probe-card-k">错误</div><div class="probe-card-v">${escapeHtml(String(r.error))}</div></div>`;
    return;
  }
  if (!signals.length) {
    host.hidden = false;
    host.innerHTML = `
      <div class="probe-card">
        <div class="probe-card-k">结果</div>
        <div class="probe-card-v">当前盘口无信号</div>
        <div class="probe-card-s">${escapeHtml(r.symbol || "—")} · bar ${escapeHtml(String(r.bar_time || r.last_bar || "—"))}</div>
      </div>
      <div class="probe-card">
        <div class="probe-card-k">检测</div>
        <div class="probe-card-v">${escapeHtml(r.detector || r.weights || "v12")}</div>
        <div class="probe-card-s">无 tip 开火属常态</div>
      </div>`;
    return;
  }
  host.hidden = false;
  host.innerHTML = signals.map((s, i) => {
    const tradeable = !!s.tradeable;
    const score = s.score != null ? Number(s.score).toFixed(4) : "—";
    const thr = s.threshold != null ? Number(s.threshold).toFixed(4) : "—";
    const lag = s.lag_min != null ? `${Number(s.lag_min).toFixed(1)}m` : "—";
    const conf = s.conf != null ? Number(s.conf).toFixed(2) : (s.confidence != null ? Number(s.confidence).toFixed(2) : "—");
    const tier = s.tier || s.size_mult != null ? `${s.tier || "—"} ×${s.size_mult ?? 1}` : "—";
    return `<div class="probe-card ${tradeable ? "good" : "warn"}">
      <div class="probe-card-k">信号 ${i + 1} · ${tradeable ? "可开单级" : "不可开"}</div>
      <div class="probe-card-v">score ${score} <span class="probe-thr">/ ${thr}</span></div>
      <div class="probe-card-s">conf ${conf} · lag ${lag} · tier ${escapeHtml(String(tier))}</div>
    </div>`;
  }).join("");
}

async function runProbe() {
  if (probeRunning) return;
  const symbol = ($("#probe-symbol")?.value || "").trim();
  const meta = $("#probe-meta");
  const out = $("#probe-result");
  const cards = $("#probe-cards");
  const btn = $("#probe-run");
  const histBtn = $("#probe-history-run");
  const link = $("#probe-history-link");
  if (!symbol) { toast("请输入币种，如 BTC 或 MOODENG_USDT_SWAP"); return; }
  probeRunning = true;
  if (btn) { btn.disabled = true; btn.textContent = "检测中…"; }
  if (histBtn) histBtn.disabled = true;
  if (meta) meta.textContent = "拉最新K线 + YOLO tip + 判断打分…";
  if (out) out.textContent = "…";
  if (cards) { cards.hidden = true; cards.innerHTML = ""; }
  if (link) { link.hidden = true; link.innerHTML = ""; }
  try {
    const res = await fetch("/api/check-symbol", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol }),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok || !data) throw new Error(`HTTP ${res.status}`);
    if (!data.ok) {
      if (meta) meta.textContent = data.detail || "检测失败";
      if (out) out.textContent = "（无结果）";
      if (cards) {
        cards.hidden = false;
        cards.innerHTML = `<div class="probe-card bad"><div class="probe-card-k">失败</div><div class="probe-card-v">${escapeHtml(data.detail || "检测失败")}</div></div>`;
      }
      if (!data.busy) toast(data.detail || "检测失败");
      return;
    }
    const r = data.result || {};
    if (out) out.textContent = r.text || JSON.stringify(r, null, 2);
    renderProbeCards(r);
    if (meta) {
      const detNote = r.detector_note ? ` · ${r.detector_note}` : "";
      if (r.error) meta.textContent = r.error;
      else if (!(r.signals || []).length) {
        meta.textContent = `${r.symbol || symbol}：当前盘口无信号 · 检测器 ${r.detector || "—"}${detNote}`;
      } else {
        const tradeable = (r.signals || []).filter((s) => s.tradeable).length;
        meta.textContent =
          `${r.symbol || symbol}：检出 ${(r.signals || []).length} 个 · 可开单 ${tradeable}`
          + ` · ${r.detector || ""}${detNote}`;
      }
    }
  } catch (err) {
    if (meta) meta.textContent = "";
    if (out) out.textContent = "（请求失败）";
    toast(`盘口检测失败：${err.message || err}`);
  } finally {
    probeRunning = false;
    if (btn) { btn.disabled = false; btn.textContent = "▶ 即时检测"; }
    if (histBtn) histBtn.disabled = false;
  }
}

async function runProbeHistory() {
  if (probeRunning) return;
  const symbol = ($("#probe-symbol")?.value || "").trim();
  const days = Math.max(7, Math.min(800, Number($("#probe-days")?.value || 365)));
  const meta = $("#probe-meta");
  const out = $("#probe-result");
  const cards = $("#probe-cards");
  const btn = $("#probe-run");
  const histBtn = $("#probe-history-run");
  const link = $("#probe-history-link");
  if (!symbol) { toast("请输入币种，如 ETH"); return; }
  probeRunning = true;
  if (btn) btn.disabled = true;
  if (histBtn) { histBtn.disabled = true; histBtn.textContent = "历史扫描中…"; }
  if (meta) meta.textContent = `历史检测 ${days} 天 · full 扫描 + 判断（可能 2–10 分钟）…`;
  if (out) out.textContent = "历史扫描进行中，请勿关闭页面…";
  if (cards) { cards.hidden = true; cards.innerHTML = ""; }
  if (link) { link.hidden = true; link.innerHTML = ""; }
  try {
    const res = await fetch("/api/probe-history", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol, days, conf: 0.30 }),
    });
    const data = await res.json().catch(() => null);
    if (!res.ok || !data) throw new Error(`HTTP ${res.status}`);
    if (!data.ok) {
      if (meta) meta.textContent = data.detail || "历史检测失败";
      if (out) out.textContent = data.detail || "（失败）";
      if (cards) {
        cards.hidden = false;
        cards.innerHTML = `<div class="probe-card bad"><div class="probe-card-k">失败</div><div class="probe-card-v">${escapeHtml(data.detail || "失败")}</div></div>`;
      }
      if (!data.busy) toast(data.detail || "历史检测失败");
      return;
    }
    const r = data.result || {};
    const nFire = r.n_raw_fires ?? (r.signals || []).length;
    const nEl = r.n_eligible ?? 0;
    const nClosed = r.closed_n ?? 0;
    if (meta) {
      meta.textContent =
        `${r.symbol || symbol} · ${r.days || days} 天 · 开火 ${nFire} · 合格 ${nEl} · 已平仓 ${nClosed}`
        + (r.detector ? ` · ${r.detector}` : "");
    }
    if (cards) {
      cards.hidden = false;
      const mean = r.closed_mean_ret != null ? `${(100 * Number(r.closed_mean_ret)).toFixed(2)}%` : "—";
      const wr = r.closed_win_rate != null ? `${(100 * Number(r.closed_win_rate)).toFixed(1)}%` : "—";
      cards.innerHTML = `
        <div class="probe-card good"><div class="probe-card-k">YOLO 开火</div><div class="probe-card-v">${nFire}</div>
          <div class="probe-card-s">${escapeHtml(String(r.window_start || "").slice(0, 16))} → ${escapeHtml(String(r.window_end || "").slice(0, 16))}</div></div>
        <div class="probe-card"><div class="probe-card-k">合格(分+ATR)</div><div class="probe-card-v">${nEl}</div>
          <div class="probe-card-s">过阈 ${r.n_passed_score ?? "—"} · 阈值 ${r.threshold ?? "—"}</div></div>
        <div class="probe-card"><div class="probe-card-k">纸面平仓</div><div class="probe-card-v">${nClosed}</div>
          <div class="probe-card-s">均收益 ${mean} · 胜率 ${wr}</div></div>
        <div class="probe-card"><div class="probe-card-k">检测器</div><div class="probe-card-v" style="font-size:13px">${escapeHtml(String(r.detector || "—").split("/").pop() || "—")}</div>
          <div class="probe-card-s">full 扫描 · 非即时 tip</div></div>`;
    }
    const url = r.report_url || "";
    if (link && url) {
      link.hidden = false;
      link.innerHTML =
        `HTML 报告：<a href="${escapeHtml(url)}" target="_blank" rel="noopener">${escapeHtml(url)}</a>`
        + (r.report_html ? ` · <code>${escapeHtml(r.report_html)}</code>` : "");
    }
    if (out) {
      const head = [
        `=== 历史检测 ${r.symbol || symbol} · ${r.days || days} 天 ===`,
        `窗口: ${r.window_start || "—"} → ${r.window_end || "—"}`,
        `开火 ${nFire} · 过阈 ${r.n_passed_score ?? "—"} · 合格 ${nEl} · 平仓 ${nClosed}`,
        r.report_url ? `报告: ${r.report_url}` : "",
        "（明细见 HTML 表）",
      ].filter(Boolean);
      out.textContent = head.join("\n");
    }
    if (url) toast(`历史报告已生成 · 开火 ${nFire}`);
    else toast(`历史检测完成 · 开火 ${nFire}`);
  } catch (err) {
    if (meta) meta.textContent = "";
    if (out) out.textContent = "（请求失败）";
    toast(`历史检测失败：${err.message || err}`);
  } finally {
    probeRunning = false;
    if (btn) { btn.disabled = false; btn.textContent = "▶ 即时检测"; }
    if (histBtn) { histBtn.disabled = false; histBtn.textContent = "📜 历史检测"; }
  }
}
$("#probe-run")?.addEventListener("click", runProbe);
$("#probe-history-run")?.addEventListener("click", runProbeHistory);
$("#probe-symbol")?.addEventListener("keydown", (e) => { if (e.key === "Enter") runProbe(); });
$("#probe-chips")?.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-sym]");
  if (!btn) return;
  const input = $("#probe-symbol");
  if (input) input.value = btn.dataset.sym;
  $$("#probe-chips .chip-btn").forEach((b) => b.classList.toggle("active", b === btn));
  runProbe();
});
// Jump buttons (overview / status strip) + external href chips
document.addEventListener("click", (e) => {
  const hrefEl = e.target.closest("[data-href]");
  if (hrefEl && hrefEl.dataset.href) {
    e.preventDefault();
    location.href = hrefEl.dataset.href;
    return;
  }
  const el = e.target.closest("[data-jump]");
  if (!el) return;
  const name = el.dataset.jump;
  if (name && document.getElementById("view-" + name)) {
    e.preventDefault();
    showView(name);
  }
});

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
        <td>${escapeHtml(fmtBjTime(s.signal_time))}</td>
        <td>${escapeHtml(s.bar)}</td>
        <td class="num">${s.entry_price != null ? Number(s.entry_price).toFixed(2) : "—"}</td>
        <td class="num">${s.score != null ? Number(s.score).toFixed(4) : "—"}</td>
        <td class="num">${s.tp_price != null ? Number(s.tp_price).toFixed(2) : "—"}</td>
        <td class="num">${s.sl_price != null ? Number(s.sl_price).toFixed(2) : "—"}</td>
        <td>${escapeHtml(fmtBjTime(s.notified_at))}</td>
      </tr>`).join("") || `<tr><td colspan="7">暂无实时信号</td></tr>`;
    }
  } catch (err) {
    if (err?.name !== "AbortError") toast(`ETH Micro：${err.message || err}`);
  } finally {
    view?.classList.remove("loading");
  }
}

let forwardChart, forwardSeries, forwardDdChart, forwardDdSeries;
let forwardTabulator = null;
let forwardRowsCache = [];
let forwardFreshFilter = "";
let forwardSymFilter = "";
let forwardTableWired = false;

function wireForwardTableControls() {
  if (forwardTableWired) return;
  forwardTableWired = true;
  const sel = $("#forward-fresh-sel");
  if (sel) {
    sel.addEventListener("change", () => {
      forwardFreshFilter = sel.value || "";
      applyForwardTableFilters();
    });
  }
  $("#forward-sym-filter")?.addEventListener("input", (e) => {
    forwardSymFilter = String(e.target.value || "").trim().toUpperCase();
    applyForwardTableFilters();
  });
}

function applyForwardTableFilters() {
  if (!forwardTabulator) return;
  forwardTabulator.setFilter((row) => {
    const d = row.getData();
    if (forwardFreshFilter === "1" && !d._fresh) return false;
    if (forwardFreshFilter === "0" && d._fresh) return false;
    if (forwardSymFilter && !String(d.symbol || "").toUpperCase().includes(forwardSymFilter)) return false;
    return true;
  });
}

function buildForwardTabulatorRows(d) {
  const freshMin = d.fresh_detect_min ?? 30;
  return (d.rows || []).map((r) => {
    const source = r.source || "okx";
    const entry = r.entry_time || r.signal_time || "";
    const overlay = {
      source,
      symbol: r.symbol || "",
      signal_time: r.signal_time || "",
      entry_time: r.entry_time || r.signal_time || "",
      exit_time: r.exit_time || "",
      entry_price: r.entry_price,
      atr_pct: r.atr_pct,
      outcome: r.outcome || "",
      status: r.status || "",
      realized_ret: r.realized_ret != null ? r.realized_ret : r.net_ret,
      net_ret: r.net_ret,
      dense_run_len: r.dense_run_len || 0,
      tp_mult: 5,
      sl_mult: 2,
    };
    const lagInfo = fmtLagMin(r.lag_min, freshMin);
    const isFresh = r.fresh === true || lagInfo.fresh;
    const symShort = String(r.symbol || "").replace(/_USDT_SWAP$/, "").replace(/_USDT$/, "");
    return {
      signal_time: r.signal_time,
      detected_at: r.detected_at,
      lag_min: r.lag_min,
      lag_text: lagInfo.text,
      lag_cls: lagInfo.cls,
      symbol: r.symbol,
      symbol_short: symShort,
      status: r.status,
      status_cn: STATUS_CN[r.status] || r.status || "",
      maker_filled: !!r.maker_filled,
      outcome: r.outcome || "",
      outcome_cn: OUTCOME_CN[r.outcome || ""] || r.outcome || "—",
      score: r.score,
      net_ret: r.net_ret,
      _fresh: isFresh,
      _source: source,
      _entry: entry,
      _overlay: overlay,
    };
  });
}

function ensureForwardTabulator() {
  const host = $("#forward-table");
  if (!host || typeof Tabulator === "undefined") return null;
  if (forwardTabulator) return forwardTabulator;
  forwardTabulator = new Tabulator(host, {
    data: [],
    layout: "fitDataStretch",
    height: "420px",
    placeholder: "暂无前向信号 · 脉冲扫到 tip 新鲜成交后显示在此",
    reactiveData: false,
    selectableRows: 1,
    columns: [
      {
        title: "信号", field: "signal_time", width: 148, sorter: "string",
        formatter: (cell) => escapeHtml(fmtBjTime(cell.getValue())),
      },
      {
        title: "检出", field: "detected_at", width: 148, sorter: "string",
        formatter: (cell) => escapeHtml(fmtBjTime(cell.getValue())),
      },
      {
        title: "延迟", field: "lag_min", width: 72, hozAlign: "right", sorter: "number",
        formatter: (cell) => {
          const row = cell.getRow().getData();
          return `<span class="${escapeHtml(row.lag_cls || "")}">${escapeHtml(row.lag_text || "—")}</span>`;
        },
      },
      {
        title: "币种", field: "symbol_short", width: 88, sorter: "string",
        formatter: (cell) => `<b>${escapeHtml(cell.getValue() || "")}</b>`,
      },
      { title: "状态", field: "status_cn", width: 72, sorter: "string" },
      {
        title: "Maker", field: "maker_filled", width: 72, hozAlign: "center",
        formatter: (cell) => cell.getValue()
          ? `<span class="fwd-plain">filled</span>`
          : `<span class="fwd-plain">miss</span>`,
      },
      {
        title: "结果", field: "outcome", width: 72, sorter: "string",
        formatter: (cell) => {
          const row = cell.getRow().getData();
          return `<span class="outcome-${escapeHtml(row.outcome || "open")}">${escapeHtml(row.outcome_cn)}</span>`;
        },
      },
      {
        title: "分数", field: "score", width: 72, hozAlign: "right", sorter: "number",
        formatter: (cell) => {
          const v = cell.getValue();
          return v == null || v === "" ? "—" : Number(v).toFixed(3);
        },
      },
      {
        title: "净收益", field: "net_ret", width: 84, hozAlign: "right", sorter: "number",
        formatter: (cell) => {
          const v = cell.getValue();
          return `<span class="${cls(v)}">${fmtPct(v)}</span>`;
        },
      },
      {
        title: "新鲜", field: "_fresh", width: 56, hozAlign: "center",
        formatter: (cell) => cell.getValue()
          ? `<span class="fwd-plain">新鲜</span>`
          : `<span class="fwd-plain warn">事后</span>`,
      },
    ],
    initialSort: [{ column: "signal_time", dir: "desc" }],
    rowFormatter: (row) => {
      const el = row.getElement();
      el.classList.add("clickable");
      if (!row.getData()._fresh) el.classList.add("row-stale");
      else el.classList.remove("row-stale");
    },
  });
  forwardTabulator.on("rowClick", (_e, row) => {
    const r = row.getData();
    if (!r.symbol) return;
    focusTrade(r._source || "okx", r.symbol, r._entry, r._overlay);
  });
  return forwardTabulator;
}

async function loadForward() {
  $("#view-forward").classList.add("loading");
  // Guarantee the v10 paper chip is present on the forward page (even with old cached HTML)
  if (typeof normalizeStatusStrip === "function") normalizeStatusStrip();
  else if (typeof ensureFourChips === "function") ensureFourChips();
  try {
  wireForwardTableControls();
  const d = await apiGet("/api/forward", { cache: true });
  const m = d.metrics;
  const hEx = d.hindsight_excluded ?? 0;
  const freshMin = d.fresh_detect_min ?? 20;
  $("#forward-tiles").innerHTML = `
    <div class="tile"><span class="lbl">裁决样本</span><b>${d.decision_trades}</b><small>新鲜≤${freshMin}m · / ${d.decision_target}</small></div>
    <div class="tile"><span class="lbl">事后剔除</span><b>${hEx}</b><small>检出延迟 &gt; ${freshMin} 分钟</small></div>
    <div class="tile"><span class="lbl">前向 PF</span><b class="${m.profit_factor >= 1.3 ? "pos" : m.profit_factor === null ? "" : "neg"}">${fmtPF(m.profit_factor)}</b><small>${d.cost_label} · 仅裁决样本</small></div>
    <div class="tile"><span class="lbl">净收益（对资金）</span><b class="${cls(m.net_return_on_capital)}">${fmtPct(m.net_return_on_capital)}</b><small>裁决样本 · 日志 ${d.total_rows} 条</small></div>`;
  $("#forward-progress").style.width = `${Math.round(100 * d.progress)}%`;
  $("#forward-progress-label").textContent = `${d.decision_trades} / ${d.decision_target}`;
  $("#forward-progress-note").textContent =
    d.decision_remaining > 0
      ? `距裁决线还差 ${d.decision_remaining} 笔；日志 ${d.total_rows} 条，open ${d.open_rows} 条；事后剔除 ${hEx}`
      : "已达到裁决样本线";
  $("#forward-count").textContent = `（${d.total_rows} 条；closed ${d.closed_rows}；延迟列=检出−信号）`;

  if (!forwardChart) {
    forwardChart = makeChart($("#forward-chart"));
    forwardSeries = forwardChart.addAreaSeries({
      lineColor: "#2563eb", lineWidth: 2, priceFormat: pctFormat,
      topColor: "rgba(37,99,235,0.22)", bottomColor: "rgba(37,99,235,0.02)",
    });
    forwardDdChart = makeChart($("#forward-dd-chart"), { timeScale: { visible: false } });
    forwardDdSeries = forwardDdChart.addAreaSeries({
      lineColor: "#dc2626", lineWidth: 1, priceFormat: pctFormat,
      topColor: "rgba(220,38,38,0.02)", bottomColor: "rgba(220,38,38,0.28)",
      invertFilledArea: true,
    });
  }
  const eq = d.equity || [];
  const dd = d.drawdown || [];
  if (eq.length) {
    forwardSeries.setData(eq);
    forwardChart.timeScale().fitContent();
  } else {
    forwardSeries.setData([]);
  }
  if (dd.length) {
    forwardDdSeries.setData(dd);
    forwardDdChart.timeScale().fitContent();
  } else {
    forwardDdSeries.setData([]);
  }
  const outcomes = d.outcomes || [];
  if (outcomes.length) {
    renderHBars($("#forward-outcomes"), outcomes.map((r) => ({
      label: OUTCOME_CN[r.label] || r.label,
      value: r.value,
      text: `${r.value.toFixed(2)}%·${r.text}`,
    })));
  } else {
    $("#forward-outcomes").innerHTML = `<div class="empty-state">暂无裁决样本 · 新鲜 maker 成交后显示分布</div>`;
  }

  forwardRowsCache = buildForwardTabulatorRows(d);
  const table = ensureForwardTabulator();
  if (table) {
    table.replaceData(forwardRowsCache);
    applyForwardTableFilters();
  } else {
    // Fallback if Tabulator failed to load — plain HTML table body
    const host = $("#forward-table");
    if (host) {
      host.innerHTML = `<div class="empty-state">Tabulator 未加载；检查 vendor/tabulator.min.js</div>`;
    }
  }
  } catch (err) {
    if (err?.name !== "AbortError") toast(`前向页：${err.message || err}`);
  } finally {
    $("#view-forward")?.classList.remove("loading");
  }
}

/* ---------- backtest (tip-replay only; look-ahead legacy archived) ---------- */
const btState = { outcome: "", filter: "", sort: "entry_time", dir: -1 };
let equityChart, equitySeries, ddChart, ddSeries;
let tradeRows = [];
const TRADES_PAGE = 120;
let tradesShow = TRADES_PAGE;

function segWire(id, state, key, parse, cb) {
  const host = $(id);
  if (!host) return;
  host.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    host.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    state[key] = parse(b.dataset[key]);
    cb();
  }));
}
segWire("#outcome-seg", btState, "outcome", String, () => { tradesShow = TRADES_PAGE; renderTrades(); });
$("#trade-filter")?.addEventListener("input", (e) => {
  btState.filter = e.target.value.toUpperCase();
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

function renderTipReplay(t) {
  const body = $("#tr-body"); const note = $("#tr-note"); const sub = $("#tr-sub");
  if (!body) return;
  if (!t || t.available === false) {
    body.innerHTML = `<div class="tile"><span class="lbl">状态</span><b>暂无</b><small>尚无 tip-replay 终审</small></div>`;
    if (note) note.textContent = (t && t.note) || "历史前视回测已归档，不再展示。";
    return;
  }
  const passCls = t.gate_pass ? "pos" : "neg";
  const kindTag = t.clean ? "holdout 干净窗口" : "pre-holdout 发现级（偏乐观）";
  const oc = t.outcomes || {};
  const ocTxt = ["tp", "sl", "timeout"].map((k) => `${OUTCOME_CN[k] || k} ${oc[k] || 0}`).join(" · ");
  if (sub) sub.textContent = `${kindTag} · ${t.n_symbols || "?"} 币 · ${t.window || ""}`;
  body.innerHTML = `
    <div class="tile"><span class="lbl">交易笔数</span><b>${t.n_trades ?? "—"}</b><small>开火 ${t.fire_per_1k_bars != null ? Number(t.fire_per_1k_bars).toFixed(1) : "—"}/千bar</small></div>
    <div class="tile"><span class="lbl">累计净收益</span><b class="${cls(t.total_net_units)}">${t.total_net_units != null ? (100 * t.total_net_units).toFixed(2) + "%" : "—"}</b><small>单笔均值 ${t.mean_net_per_trade != null ? (100 * t.mean_net_per_trade).toFixed(3) + "%" : "—"}</small></div>
    <div class="tile"><span class="lbl">盈亏比 PF</span><b class="${(t.profit_factor || 0) >= 1.3 ? "pos" : "neg"}">${fmtPF(t.profit_factor)}</b><small>验收线 1.3</small></div>
    <div class="tile"><span class="lbl">胜率 / 闸门</span><b>${t.win_rate != null ? (100 * t.win_rate).toFixed(1) + "%" : "—"}</b><small class="${passCls}">${t.gate_pass ? "达标 ✓" : "未达标 ✗"} · ${escapeHtml(ocTxt)}</small></div>`;
  if (note) {
    note.textContent = [
      t.note || "",
      t.protocol || "",
      t.weights ? `权重 ${t.weights}` : "",
      t.source_file ? `源 ${t.source_file}` : "",
      "旧前视回测（PF≈6.6）已归档下线",
    ].filter(Boolean).join(" · ");
  }
}

async function loadBacktest() {
  $("#view-backtest")?.classList.add("loading");
  try {
    const t = await apiGet("/api/backtest/tip_replay", { cache: false });
    renderTipReplay(t);
    tradeRows = (t && t.available && Array.isArray(t.trades)) ? t.trades : [];
    tradesShow = TRADES_PAGE;

    if (!equityChart && $("#equity-chart")) {
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
    if (equitySeries) {
      equitySeries.setData(t?.equity || []);
      equityChart?.timeScale().fitContent();
    }
    if (ddSeries) {
      ddSeries.setData(t?.drawdown || []);
      ddChart?.timeScale().fitContent();
    }
    if ($("#monthly-bars")) {
      renderHBars($("#monthly-bars"), (t?.monthly || []).map((r) => ({
        label: r.month, value: r.value, text: Number(r.value).toFixed(2) + "%",
      })));
    }
    renderTrades();
  } catch (err) {
    if (err?.name !== "AbortError") toast(`回测页：${err.message || err}`);
  } finally {
    $("#view-backtest")?.classList.remove("loading");
  }
}

function filteredTradeRows() {
  let rows = tradeRows;
  if (btState.outcome) rows = rows.filter((r) => String(r.outcome || "").startsWith(btState.outcome));
  if (btState.filter) rows = rows.filter((r) => String(r.symbol || "").includes(btState.filter));
  return rows.slice().sort((a, b) => {
    const va = a[btState.sort], vb = b[btState.sort];
    return (va < vb ? -1 : va > vb ? 1 : 0) * btState.dir;
  });
}

function renderTrades() {
  const rows = filteredTradeRows();
  if ($("#trades-count")) $("#trades-count").textContent = `（${rows.length} 笔 · tip-replay）`;
  const tbody = $("#trades-table tbody");
  if (!tbody) return;
  const shown = rows.slice(0, tradesShow);
  tbody.innerHTML = shown.map((r, i) => {
    const scoreTxt = r.score == null || Number.isNaN(Number(r.score)) ? "—" : Number(r.score).toFixed(3);
    return `<tr data-i="${i}" data-source="${escapeHtml(r.source || "okx")}" data-symbol="${escapeHtml(r.symbol)}" data-entry="${escapeHtml(r.entry_time)}">
      <td>${escapeHtml(fmtBjTime(r.entry_time))}</td>
      <td>${escapeHtml(r.symbol)}</td>
      <td class="num">${scoreTxt}</td>
      <td class="outcome-${escapeHtml(r.outcome || "")}">${OUTCOME_CN[r.outcome] || escapeHtml(r.outcome || "—")}</td>
      <td class="num"><span class="${cls(r.gross_ret)}">${fmtPct(r.gross_ret)}</span></td>
      <td class="num"><span class="${cls(r.net_ret)}">${fmtPct(r.net_ret)}</span></td>
    </tr>`;
  }).join("");
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
let bandSeries, pathSeries, barrier = { tp: 5, sl: 2 }; // tip-replay short TP5/SL2
/** Horizontal TV-style order segments (entry / TP / SL) for focused trade */
let tradeLevelSeries = [];
let currentKey = "", currentMarkers = [], currentTimes = [], priceLines = [], chartReq = 0;
let currentThreshold = 0;
let lastFocusRange = null;
let symbolInputWired = false;
/** raw 15m payload from /api/chart — re-rendered when TF changes */
let sigRawPayload = null;
let sigVolByTime = new Map();
let sigLastFocusEntry = null;
/** When set, paint entry/exit/TP/SL from this trade (forward log) after chart loads */
let pendingTradeOverlay = null;
/** maMode: off | ema | sma | all — default EMA only (cleaner chart). */
/* Default 9000 bars (~3 months) so tip-replay holdout window (May–Jul) is on chart. */
const sigState = { bars: 9000, maMode: "ema", tfMin: 15 };

function setTradeFocusCard(html) {
  const card = $("#trade-focus-card");
  const body = $("#trade-focus-body");
  if (!card || !body) return;
  if (!html) {
    card.hidden = true;
    body.innerHTML = "";
    return;
  }
  body.innerHTML = html;
  card.hidden = false;
}

function updateSignalsLegend() {
  const el = $("#signals-legend span[data-leg='ma']");
  if (!el) return;
  const mode = sigState.maMode || "ema";
  if (mode === "off") el.innerHTML = `<i class="dot" style="background:#94a3b8"></i>均线关`;
  else if (mode === "sma") el.innerHTML = `<i class="dot" style="background:#3b82f6"></i>SMA 20/60/120`;
  else if (mode === "all") el.innerHTML = `<i class="dot" style="background:#f97316"></i>SMA+EMA`;
  else el.innerHTML = `<i class="dot" style="background:#f97316"></i>EMA 20/60/120`;
}

function parseTimeToUnix(entryTimeStr) {
  if (entryTimeStr == null || entryTimeStr === "") return null;
  const raw = String(entryTimeStr).trim();
  if (/^\d{10}$/.test(raw)) return Number(raw);
  if (/^\d{13}$/.test(raw)) return Math.floor(Number(raw) / 1000);
  let s = raw.replace(" ", "T");
  if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) s += "Z";
  const ms = Date.parse(s);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null;
}

/** Short TP/SL from entry (Claude paper-chart convention). */
function shortBarrierPrices(entry, atrAbs, atrPct, tpM, slM, precomputed) {
  if (_C && _C.shortBarrierPrices) {
    return _C.shortBarrierPrices(entry, atrAbs, atrPct, tpM, slM, precomputed);
  }
  if (precomputed?.tp_price != null && precomputed?.sl_price != null) {
    return { tp: Number(precomputed.tp_price), sl: Number(precomputed.sl_price) };
  }
  const a = Number.isFinite(atrAbs) && atrAbs > 0
    ? atrAbs
    : (Number.isFinite(atrPct) && atrPct > 0 ? entry * atrPct : null);
  if (a == null) return { tp: null, sl: null };
  return { tp: entry - tpM * a, sl: entry + slM * a };
}

function shortExitFromRet(entry, ret) {
  if (_C && _C.shortExitFromRet) return _C.shortExitFromRet(entry, ret);
  if (!Number.isFinite(entry) || !Number.isFinite(ret)) return null;
  // short ret ≈ (entry - exit) / entry
  return entry * (1 - ret);
}

/** Shared store for createPriceLine + segment series (FableChart clear/add). */
function _tradeStore() {
  return { priceLines, levelSeries: tradeLevelSeries };
}
function _syncTradeStore(store) {
  priceLines = store.priceLines || [];
  tradeLevelSeries = store.levelSeries || [];
}

/**
 * Draw entry / TP / SL / exit — Claude short-chart style (via FableChart).
 * tip-replay mainline is SHORT: TP below entry (green), SL above (red),
 * blue arrowDown + "入场 … 空" at entry.
 * @param {object} ov
 */
function paintTradeOverlay(ov) {
  if (!ov || !klineSeries || !klineChart) return false;
  const entry = Number(ov.entry_price);
  if (!Number.isFinite(entry) || entry <= 0) return false;

  const tf = sigState.tfMin || 15;
  const atrPct = Number(ov.atr_pct);
  const atrAbs = Number(ov.atr);
  const tpM = Number(ov.tp_mult ?? barrier.tp ?? 5);
  const slM = Number(ov.sl_mult ?? barrier.sl ?? 2);
  const side = ov.side || "short";
  const { tp: tpPx, sl: slPx } = shortBarrierPrices(entry, atrAbs, atrPct, tpM, slM, ov);
  const atrDisp = Number.isFinite(atrPct) && atrPct > 0
    ? atrPct
    : (Number.isFinite(atrAbs) && atrAbs > 0 && entry > 0 ? atrAbs / entry : null);

  let tSig = parseTimeToUnix(ov.signal_time) || parseTimeToUnix(ov.time);
  let t0 = parseTimeToUnix(ov.entry_time) || tSig || sigLastFocusEntry;
  let t1 = parseTimeToUnix(ov.exit_time);
  const openPos = !t1 || ov.status === "open" || (!ov.outcome && ov.status !== "closed");
  if (!t1 && currentTimes.length) t1 = currentTimes[currentTimes.length - 1];
  if (tSig != null) tSig = snapTimeToTf(tSig, tf);
  if (t0 != null) t0 = snapTimeToTf(t0, tf);
  if (t1 != null) t1 = snapTimeToTf(t1, tf);
  if (t0 == null) return false;
  if (t1 == null || t1 < t0) t1 = t0;

  const ret = ov.realized_ret != null ? Number(ov.realized_ret)
    : (ov.net_ret != null ? Number(ov.net_ret)
      : (ov.ret != null ? Number(ov.ret) : null));
  const exitPrice = ov.exit_price != null && Number.isFinite(Number(ov.exit_price))
    ? Number(ov.exit_price)
    : (Number.isFinite(ret) ? shortExitFromRet(entry, ret) : null);
  const outcome = ov.outcome || (openPos ? "" : "");

  const store = _tradeStore();
  const pathEnd = openPos
    ? (currentTimes.length ? candlesCloseAt(t1) ?? entry : entry)
    : (exitPrice ?? entry);

  if (_C && _C.paintTradeOverlay) {
    _C.paintTradeOverlay(
      { chart: klineChart, series: klineSeries, store, pathSeries },
      {
        entry,
        exit: exitPrice,
        tp: tpPx,
        sl: slPx,
        mark: openPos ? pathEnd : null,
        tEntry: t0,
        tExit: t1,
        tSignal: tSig,
        side,
        outcome,
        ret,
        openPos,
        tpMult: tpM,
        slMult: slM,
      }
    );
    _syncTradeStore(store);
  } else {
    // Fallback without chart_theme.js
    _clearTradeLevels();
    if (slPx != null) _addTradeLevel(slPx, "#dc2626", `止损 ${slM}xATR`, tSig != null ? tSig : t0, t1, 2);
    _addTradeLevel(entry, "#2563eb", "入场", t0, t1, 0);
    if (tpPx != null) _addTradeLevel(tpPx, "#059669", `止盈 ${tpM}xATR`, tSig != null ? tSig : t0, t1, 2);
    try {
      klineSeries.setMarkers([{
        time: t0, position: "aboveBar", shape: "arrowDown", color: "#2563eb",
        text: `入场 ${fmtPx(entry)} 空`, size: 2,
      }]);
    } catch (_) { /* ignore */ }
  }

  const z = _C && _C.zoomAround
    ? _C.zoomAround(klineChart, currentTimes, tSig != null ? tSig : t0, t1, Math.max(40, 40))
    : null;
  if (z) lastFocusRange = z;
  else {
    let i0 = currentTimes.findIndex((t) => t >= (tSig != null ? tSig : t0));
    let i1 = currentTimes.findIndex((t) => t >= t1);
    if (i0 < 0) i0 = currentTimes.length - 1;
    if (i1 < 0) i1 = currentTimes.length - 1;
    const pad = Math.max(40, Math.floor((i1 - i0) * 1.4) || 40);
    lastFocusRange = { from: Math.max(0, i0 - pad), to: i1 + pad };
    setTimeout(() => klineChart.timeScale().setVisibleLogicalRange(lastFocusRange), 60);
  }

  const retPctStr = Number.isFinite(ret) ? `${ret >= 0 ? "+" : ""}${(100 * ret).toFixed(2)}%` : "—";
  const info = $("#symbol-info");
  if (info) {
    info.textContent = [
      openPos ? "持仓中" : "已平",
      outcome ? (OUTCOME_CN[outcome] || outcome) : null,
      Number.isFinite(ret) ? retPctStr : null,
      "做空 tip-replay",
    ].filter(Boolean).join(" · ");
  }
  setTradeFocusCard(`
    <div class="tf-row"><span>结果</span><b class="${cls(ret)}">${escapeHtml(OUTCOME_CN[outcome] || (openPos ? "持有" : "—"))} ${retPctStr}</b></div>
    <div class="tf-row"><span>方向</span><b>做空 · TP${tpM}/SL${slM}</b></div>
    <div class="tf-row"><span>入场</span><b>${fmtPx(entry)}</b></div>
    <div class="tf-row"><span>出场</span><b>${exitPrice != null ? fmtPx(exitPrice) : "—"}</b></div>
    <div class="tf-row"><span>止盈 / 止损</span><b>${tpPx != null ? fmtPx(tpPx) : "—"} / ${slPx != null ? fmtPx(slPx) : "—"}</b></div>
    ${atrDisp != null ? `<div class="tf-row"><span>ATR%</span><b>${(100 * atrDisp).toFixed(3)}%</b></div>` : ""}
    <div class="tf-row"><span>来源</span><b>${escapeHtml(openPos ? "前向持仓" : "tip-replay")}</b></div>
    <div class="tf-row"><span>时间</span><b class="mono">${escapeHtml(fmtBjTime(ov.entry_time || ov.signal_time))}</b></div>
  `);
  window.__lastTradeOverlay = ov;
  return true;
}

function candlesCloseAt(t) {
  if (!sigRawPayload?.candles?.length) return null;
  // find last candle at or before t in displayed TF is hard; use raw
  let best = null;
  for (const c of sigRawPayload.candles) {
    if (c.time <= t) best = c.close;
    else break;
  }
  return best;
}
segWire("#bars-seg", sigState, "bars", Number, () => currentKey && loadChart(currentKey, sigLastFocusEntry));
// TF seg (client aggregate)
(function wireTfSeg() {
  const host = $("#tf-seg");
  if (!host) return;
  host.querySelectorAll("button[data-tf]").forEach((btn) => {
    btn.addEventListener("click", () => {
      host.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      sigState.tfMin = Number(btn.dataset.tf) || 15;
      if (sigRawPayload) {
        applySignalChartData(sigRawPayload, null);
        // re-paint last forward overlay if any
        if (window.__lastTradeOverlay) {
          setTimeout(() => paintTradeOverlay(window.__lastTradeOverlay), 20);
        } else if (sigLastFocusEntry) {
          focusMarker(sigLastFocusEntry);
        }
      }
    });
  });
})();
// MA mode: off | ema | sma | all
(function wireMaSeg() {
  const host = $("#ma-seg");
  if (!host) return;
  host.querySelectorAll("button[data-ma]").forEach((btn) => {
    btn.addEventListener("click", () => {
      host.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      sigState.maMode = btn.dataset.ma || "ema";
      updateSignalsLegend();
      if (sigRawPayload) {
        const keepFocus = sigLastFocusEntry;
        const ov = window.__lastTradeOverlay;
        applySignalChartData(sigRawPayload, null);
        if (ov) setTimeout(() => paintTradeOverlay(ov), 20);
        else if (keepFocus) focusMarker(keepFocus);
      }
    });
  });
  updateSignalsLegend();
})();
$("#signals-fit-trade")?.addEventListener("click", () => {
  if (!klineChart || !lastFocusRange) {
    toast("请先点一笔成交聚焦", "info");
    return;
  }
  try {
    klineChart.timeScale().setVisibleLogicalRange(lastFocusRange);
  } catch (_) { /* ignore */ }
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
    topColor: "rgba(37,99,235,0.10)", bottomColor: "rgba(37,99,235,0.10)",
  });
  klineChart.priceScale("band").applyOptions({ visible: false, scaleMargins: { top: 0, bottom: 0 } });
  klineSeries = klineChart.addCandlestickSeries(
    (_C && _C.candlestickOptions()) || {
      upColor: "#059669", downColor: "#dc2626", borderVisible: false,
      wickUpColor: "#059669", wickDownColor: "#dc2626",
    }
  );
  volumeSeries = klineChart.addHistogramSeries({
    priceScaleId: "vol", priceFormat: { type: "volume" },
    priceLineVisible: false, lastValueVisible: false,
  });
  // taller volume pane for scannable density
  klineChart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.72, bottom: 0 } });
  klineChart.priceScale("right").applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });
  // entry->exit path segment of the focused trade
  pathSeries = klineChart.addLineSeries({
    lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false,
    crosshairMarkerVisible: false, autoscaleInfoProvider: () => null,
  });
  wireOhlcLegend(klineChart, klineSeries, $("#kline-ohlc"), {
    hideWhenEmpty: false,
    volByTime: sigVolByTime,
  });
}

/** Paint candles/MAs/markers for current TF from cached 15m payload. */
function applySignalChartData(d, focusEntry = null) {
  if (!d || !klineSeries) return;
  const tf = sigState.tfMin || 15;
  const candles = aggregateCandles(d.candles || [], tf);
  sigVolByTime.clear();
  for (const c of candles) sigVolByTime.set(c.time, c.volume);

  if (typeof _clearTradeLevels === "function") _clearTradeLevels();
  emaSeries.forEach((s) => {
    try { klineChart.removeSeries(s); } catch (_) { /* ignore */ }
  });
  emaSeries = [];
  bandSeries.setData([]);
  pathSeries.setData([]);

  currentTimes = candles.map((c) => c.time);
  klineSeries.setData(candles);
  volumeSeries.setData(candles.map((c) =>
    (_C && _C.volPoint(c)) || {
      time: c.time,
      value: c.volume,
      color: c.close >= c.open ? "rgba(5,150,105,0.40)" : "rgba(220,38,38,0.35)",
    }
  ));

  // MAs: default EMA only; mode off|ema|sma|all — TG/YOLO palette via CHART_MA_STYLE
  const maMode = sigState.maMode || "ema";
  const maDefs = [];
  if (maMode === "sma" || maMode === "all") {
    maDefs.push(
      { key: "sma20", span: 20 },
      { key: "sma60", span: 60 },
      { key: "sma120", span: 120 },
    );
  }
  if (maMode === "ema" || maMode === "all") {
    maDefs.push(
      { key: "ema20", span: 20 },
      { key: "ema60", span: 60 },
      { key: "ema120", span: 120 },
    );
  }
  for (const m of maDefs) {
    const pts = m.key.startsWith("ema") ? emaSeriesFrom(candles, m.span) : smaSeries(candles, m.span);
    if (!pts.length) continue;
    const st = CHART_MA_STYLE[m.key] || { color: "#666", lineWidth: 1, lineStyle: 0 };
    const s = klineChart.addLineSeries({
      color: st.color,
      lineWidth: st.lineWidth,
      lineStyle: st.lineStyle || 0,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    s.setData(pts);
    emaSeries.push(s);
  }

  currentMarkers = d.markers || [];
  // Overview: shared short markers (blue ↓)
  const deduped = (_C && _C.overviewTradeMarkers)
    ? _C.overviewTradeMarkers(currentMarkers, {
      side: d.side || "short",
      snapTime: (t) => snapTimeToTf(t, tf),
    })
    : (() => {
      const markerList = [];
      for (const m of currentMarkers) {
        if (!m.eligible && !m.traded) continue;
        if (!m.traded) continue;
        markerList.push({
          time: snapTimeToTf(m.time, tf),
          position: "aboveBar",
          shape: "arrowDown",
          color: "#2563eb",
          text: "",
          size: 1,
        });
      }
      return markerList;
    })();
  klineSeries.setMarkers(deduped);

  // Default zoom: last ~120 bars of current TF (TV-like), unless focusing a trade
  if (focusEntry) {
    focusMarker(focusEntry);
  } else {
    lastFocusRange = null;
    const nShow = tf >= 240 ? 90 : tf >= 60 ? 120 : 140;
    showLastBars(klineChart, nShow, candles.length);
  }
}

/** Symbol rows for combobox: { key, source, symbol, short, n_trades, n_eligible } */
let sigSymbolRows = [];

function shortSymbol(sym) {
  return String(sym || "").replace(/_USDT_SWAP$/, "").replace(/_USDT$/, "");
}

function setSymbolComboValue(key, { silent = false } = {}) {
  const hidden = $("#symbol-input");
  const label = $("#sym-combo-label");
  if (hidden) hidden.value = key || "";
  if (label) {
    if (!key) label.textContent = "选择币种…";
    else {
      const [, sym] = key.split(":");
      label.textContent = shortSymbol(sym || key);
      label.title = key;
    }
  }
  // highlight selected in open list
  $$("#sym-combo-list .sym-combo-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.key === key);
  });
  if (!silent && key) loadChart(key);
}

function renderSymbolComboList(filter = "") {
  const list = $("#sym-combo-list");
  const empty = $("#sym-combo-empty");
  if (!list) return;
  const q = filter.trim().toUpperCase();
  const rows = !q
    ? sigSymbolRows
    : sigSymbolRows.filter((r) =>
        r.short.toUpperCase().includes(q) ||
        r.symbol.toUpperCase().includes(q) ||
        r.key.toUpperCase().includes(q));
  // prefer traded first already sorted; cap display for perf
  const show = rows.slice(0, 200);
  list.innerHTML = show.map((r) => {
    const meta = r.n_trades > 0
      ? `成交 ${r.n_trades}`
      : (r.n_eligible > 0 ? `合格 ${r.n_eligible}` : "—");
    const active = r.key === currentKey || r.key === ($("#symbol-input")?.value || "");
    return `<button type="button" class="sym-combo-item${active ? " active" : ""}" role="option" data-key="${escapeHtml(r.key)}">
      <span class="sym-combo-name">${escapeHtml(r.short)}</span>
      <span class="sym-combo-meta">${escapeHtml(meta)}</span>
    </button>`;
  }).join("");
  if (empty) empty.hidden = show.length > 0;
}

function openSymbolCombo() {
  const panel = $("#sym-combo-panel");
  const btn = $("#sym-combo-btn");
  if (!panel || !btn) return;
  panel.hidden = false;
  btn.setAttribute("aria-expanded", "true");
  document.body.classList.add("sym-combo-open");
  renderSymbolComboList($("#sym-combo-search")?.value || "");
  setTimeout(() => $("#sym-combo-search")?.focus(), 0);
}

function closeSymbolCombo() {
  const panel = $("#sym-combo-panel");
  const btn = $("#sym-combo-btn");
  if (panel) panel.hidden = true;
  if (btn) btn.setAttribute("aria-expanded", "false");
  document.body.classList.remove("sym-combo-open");
}

function wireSymbolCombo() {
  if (symbolInputWired) return;
  symbolInputWired = true;
  const btn = $("#sym-combo-btn");
  const panel = $("#sym-combo-panel");
  const search = $("#sym-combo-search");
  const list = $("#sym-combo-list");
  if (!btn || !panel) return;

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (panel.hidden) openSymbolCombo();
    else closeSymbolCombo();
  });
  search?.addEventListener("input", () => renderSymbolComboList(search.value));
  search?.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeSymbolCombo();
      btn.focus();
    }
    if (e.key === "Enter") {
      const first = list?.querySelector(".sym-combo-item");
      if (first) {
        e.preventDefault();
        setSymbolComboValue(first.dataset.key);
        closeSymbolCombo();
      }
    }
  });
  list?.addEventListener("click", (e) => {
    const item = e.target.closest(".sym-combo-item");
    if (!item) return;
    setSymbolComboValue(item.dataset.key);
    closeSymbolCombo();
  });
  document.addEventListener("click", (e) => {
    if (!panel.hidden && !e.target.closest("#sym-combo")) closeSymbolCombo();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) closeSymbolCombo();
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
  sigSymbolRows = (rows || []).map((r) => ({
    key: `${r.source}:${r.symbol}`,
    source: r.source,
    symbol: r.symbol,
    short: shortSymbol(r.symbol),
    n_trades: r.n_trades || 0,
    n_eligible: r.n_eligible || 0,
  }));
  // traded first, then by name
  sigSymbolRows.sort((a, b) => (b.n_trades - a.n_trades) || a.short.localeCompare(b.short));
  wireSymbolCombo();
  renderSymbolComboList();

  const first = sigSymbolRows.find((r) => r.n_trades > 0) || sigSymbolRows[0];
  if (first && !currentKey) {
    setSymbolComboValue(first.key, { silent: true });
    loadChart(first.key);
  } else if (currentKey) {
    setSymbolComboValue(currentKey, { silent: true });
  }
}



async function loadChart(key, focusEntry = null) {
  const [source, symbol] = key.split(":");
  if (!symbol) return;
  currentKey = key;
  setSymbolComboValue(key, { silent: true });
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
  barrier = {
    tp: d.tp_mult != null ? Number(d.tp_mult) : 5,
    sl: d.sl_mult != null ? Number(d.sl_mult) : 2,
  };
  currentThreshold = d.threshold;
  sigRawPayload = d;
  sigLastFocusEntry = focusEntry;
  // If we have an explicit forward/backtest overlay, skip marker-only focus
  const useOverlay = pendingTradeOverlay && (
    !focusEntry
    || Math.abs(parseTimeToUnix(pendingTradeOverlay.entry_time) - Number(focusEntry)) < 2
    || parseTimeToUnix(pendingTradeOverlay.entry_time) === Number(focusEntry)
  );
  applySignalChartData(d, useOverlay ? null : focusEntry);
  if (pendingTradeOverlay) {
    const ov = pendingTradeOverlay;
    pendingTradeOverlay = null;
    window.__lastTradeOverlay = ov;
    // paint after series data is set
    setTimeout(() => paintTradeOverlay(ov), 30);
  } else {
    window.__lastTradeOverlay = null;
    if (!focusEntry) setTradeFocusCard(null);
  }

  const n = d.markers.length;
  const tr = d.markers.filter((m) => m.traded).length;
  const tfLabel = sigState.tfMin >= 240 ? "4H" : sigState.tfMin >= 60 ? "1H" : "15m";
  const srcNote = d.marker_source === "tip_replay" ? "tip-replay" : (d.marker_source || "—");
  if (!$("#symbol-info")?.textContent?.includes("前向")) {
    $("#symbol-info").textContent =
      `${symbol} · ${tfLabel} · ${srcNote}：成交 ${tr}${n !== tr ? ` / 标记 ${n}` : ""}`;
  }

  const traded = d.markers.filter((m) => m.traded).sort((a, b) => b.time - a.time);
  $("#side-count").textContent = `（${traded.length} 笔）`;
  $("#symbol-trades").innerHTML = "<tbody>" + (traded.length ? traded.map((m) => `
    <tr data-entry-ts="${m.entry_time}">
      <td>${escapeHtml(fmtBjTime(m.time))}</td>
      <td class="outcome-${m.outcome}">${OUTCOME_CN[m.outcome] || m.outcome}</td>
      <td class="num"><span class="${cls(m.ret)}">${fmtPct(m.ret, 1)}</span></td>
    </tr>`).join("") : `<tr class="no-click"><td colspan="3" class="empty-state">本币种无 tip-replay 成交</td></tr>`) + "</tbody>";
  $("#symbol-trades").querySelectorAll("tr[data-entry-ts]").forEach((row) =>
    row.addEventListener("click", () => {
      $("#symbol-trades").querySelectorAll("tr").forEach((x) => x.classList.toggle("focused", x === row));
      focusMarker(Number(row.dataset.entryTs));
    }));
  // Look-ahead "eligible missed" retired with scored_signals.
  $("#missed-count").textContent = "";
  $("#symbol-missed").innerHTML =
    `<tbody><tr class="no-click"><td colspan="3" class="empty-state">`
    + `仅 tip-replay 成交标记<br><span class="note">${escapeHtml(d.marker_note || "旧前视候选/合格未成交已下线")}</span>`
    + `</td></tr></tbody>`;

  // default: focus the most recent trade — but not if a forward-log overlay is active
  if (!window.__lastTradeOverlay) {
    if (!focusEntry && traded.length) focusEntry = traded[0].entry_time;
    if (focusEntry) {
      sigLastFocusEntry = focusEntry;
      focusMarker(focusEntry);
      const row = $(`#symbol-trades tr[data-entry-ts="${focusEntry}"]`);
      if (row) row.classList.add("focused");
    }
  }
}

function showSignalTooltip(event, marker) {
  const edge = marker.score - currentThreshold;
  $("#signal-tooltip").innerHTML = `<b>合格未成交 · ${escapeHtml(fmtBjTime(marker.time))}</b>
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

function _clearTradeLevels() {
  const store = _tradeStore();
  if (_C && _C.clearTradeLevels) {
    _C.clearTradeLevels(store, klineSeries, klineChart);
    _syncTradeStore(store);
  } else {
    priceLines.forEach((l) => {
      try { klineSeries.removePriceLine(l); } catch (_) { /* ignore */ }
    });
    priceLines = [];
    tradeLevelSeries.forEach((s) => {
      try { klineChart.removeSeries(s); } catch (_) { /* ignore */ }
    });
    tradeLevelSeries = [];
  }
  if (pathSeries) pathSeries.setData([]);
}

function _addTradeLevel(price, color, title, t0, t1, lineStyle = 0) {
  const store = _tradeStore();
  if (_C && _C.addTradeLevel) {
    _C.addTradeLevel(store, klineSeries, klineChart, price, color, title, t0, t1, lineStyle);
    _syncTradeStore(store);
    return;
  }
  if (price == null || !Number.isFinite(Number(price))) return;
  const p = Number(price);
  priceLines.push(klineSeries.createPriceLine({
    price: p, color, lineWidth: 1, lineStyle,
    axisLabelVisible: true, title: title || "",
  }));
  if (t0 != null && t1 != null && t1 >= t0 && klineChart) {
    const seg = klineChart.addLineSeries({
      color, lineWidth: lineStyle === 0 ? 2 : 1, lineStyle,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    seg.setData([{ time: t0, value: p }, { time: t1, value: p }]);
    tradeLevelSeries.push(seg);
  }
}

function focusMarker(entryTs) {
  const ts = Number(entryTs);
  sigLastFocusEntry = ts;
  const m = currentMarkers.find((x) => Number(x.entry_time) === ts || Number(x.time) === ts);
  if (!m) {
    // Forward/open rows may not be in backtest marker list — still zoom to time
    if (Number.isFinite(ts) && currentTimes.length && klineChart) {
      let i0 = currentTimes.findIndex((t) => t >= ts);
      if (i0 < 0) i0 = currentTimes.length - 1;
      lastFocusRange = { from: i0 - 80, to: i0 + 80 };
      setTimeout(() => klineChart.timeScale().setVisibleLogicalRange(lastFocusRange), 60);
    }
    return;
  }
  // Single path: Claude short overlay (no second card rewrite / no long-geometry leftover)
  const ov = {
    entry_price: m.entry_price,
    exit_price: m.exit_price,
    atr: m.atr,
    atr_pct: m.atr_pct,
    tp_price: m.tp_price,
    sl_price: m.sl_price,
    tp_mult: m.tp_mult ?? barrier.tp ?? 5,
    sl_mult: m.sl_mult ?? barrier.sl ?? 2,
    signal_time: m.time,
    entry_time: m.entry_time || m.time,
    exit_time: m.exit_time,
    outcome: m.outcome,
    ret: m.ret,
    realized_ret: m.ret,
    status: "closed",
    side: m.side || "short",
  };
  const painted = paintTradeOverlay(ov);
  if (!painted) {
    const tf = sigState.tfMin || 15;
    const t0 = snapTimeToTf(m.entry_time || m.time, tf);
    let i0 = currentTimes.findIndex((t) => t >= t0);
    if (i0 < 0) i0 = currentTimes.length - 1;
    lastFocusRange = { from: i0 - 80, to: i0 + 80 };
    setTimeout(() => klineChart.timeScale().setVisibleLogicalRange(lastFocusRange), 60);
  }
}

async function focusTrade(source, symbol, entryTimeStr, overlay = null) {
  showView("signals");
  await initSignals();
  const entryTs = parseTimeToUnix(entryTimeStr);
  pendingTradeOverlay = overlay || null;
  const key = `${source || "okx"}:${symbol}`;
  setSymbolComboValue(key, { silent: true });
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
  // OPS token UI removed from sidebar (VPS job executor is off; no daily need).
  try {
    const st = await (await fetch("/api/ops/status")).json();
    opsState.authRequired = !!st.ops_auth_required;
    opsState.executorEnabled = !!st.executor_enabled;
  } catch (_) { /* ignore */ }
}

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
  const saved = localStorage.getItem("fable_clauseos_theme");
  applyTheme(saved === "dark" ? "dark" : "light");
  $("#theme-toggle")?.addEventListener("click", () => {
    const next = document.body.classList.contains("theme-dark") ? "light" : "dark";
    localStorage.setItem("fable_clauseos_theme", next);
    location.reload();
  });
}
function applyTheme(mode) {
  document.documentElement.classList.toggle("theme-dark", mode === "dark");
  document.documentElement.classList.toggle("theme-light", mode !== "dark");
  document.body.classList.toggle("theme-dark", mode === "dark");
  document.body.classList.toggle("theme-light", mode !== "dark");
  const btn = $("#theme-toggle");
  if (btn) {
    btn.textContent = mode === "dark" ? "切到白色" : "切到深色";
    btn.disabled = false;
    btn.removeAttribute("aria-disabled");
    btn.setAttribute("aria-label", mode === "dark" ? "切换到白色主题" : "切换到深色主题");
  }
  if (mode === "dark") {
    CHART_LAYOUT.layout.background.color = "#080b09";
    CHART_LAYOUT.layout.textColor = "#79857f";
    CHART_LAYOUT.grid.vertLines.color = "#141a16";
    CHART_LAYOUT.grid.horzLines.color = "#141a16";
    CHART_LAYOUT.timeScale.borderColor = "#202822";
    CHART_LAYOUT.rightPriceScale.borderColor = "#202822";
    CHART_LAYOUT.crosshair.vertLine.color = "rgba(104,231,142,0.38)";
    CHART_LAYOUT.crosshair.vertLine.labelBackgroundColor = "#287a48";
    CHART_LAYOUT.crosshair.horzLine.color = "rgba(150,163,156,0.3)";
    CHART_LAYOUT.crosshair.horzLine.labelBackgroundColor = "#445149";
  } else {
    CHART_LAYOUT.layout.background.color = "#ffffff";
    CHART_LAYOUT.layout.textColor = "#6b7280";
    CHART_LAYOUT.grid.vertLines.color = "#eef1f6";
    CHART_LAYOUT.grid.horzLines.color = "#eef1f6";
    CHART_LAYOUT.timeScale.borderColor = "#e5e7eb";
    CHART_LAYOUT.rightPriceScale.borderColor = "#e5e7eb";
    CHART_LAYOUT.crosshair.vertLine.color = "rgba(40,140,78,0.35)";
    CHART_LAYOUT.crosshair.vertLine.labelBackgroundColor = "#2f9e59";
    CHART_LAYOUT.crosshair.horzLine.color = "rgba(107,114,128,0.35)";
    CHART_LAYOUT.crosshair.horzLine.labelBackgroundColor = "#6b7280";
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
  // Force the 4-chip strip on every page/view (including direct #forward deep links and old cached index.html)
  if (typeof normalizeStatusStrip === "function") normalizeStatusStrip();
  else if (typeof ensureFourChips === "function") ensureFourChips();
  loadStatusStrip();
  $("#status-refresh")?.addEventListener("click", () => loadStatusStrip(true));
  const hash = (location.hash || "").slice(1);
  const initial = hash && document.getElementById("view-" + hash) ? hash : "overview";
  showView(initial, { pushHash: false });
  // one more tick after the view mounted (SPA nav, old HTML, etc.)
  setTimeout(() => { if (typeof normalizeStatusStrip === "function") normalizeStatusStrip(); else if (typeof ensureFourChips === "function") ensureFourChips(); }, 260);
  // refresh strip every 2 min (cheap) and keep re-normalizing
  setInterval(() => { if (typeof normalizeStatusStrip === "function") normalizeStatusStrip(); else if (typeof ensureFourChips === "function") ensureFourChips(); loadStatusStrip(false); }, 120_000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
