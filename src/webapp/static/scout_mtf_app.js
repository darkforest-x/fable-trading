/* scout_mtf light console — radar + TV-style trade kline drill-down */
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];

const state = {
  data: null,
  paper: null,
  positions: null,
  grade: "",
  open: null,
  paperOpen: null,
  posOpen: null,
  chart: null,
  series: null,
  maSeries: [],
  levelSeries: [],
  priceLines: [],
  chartKey: null,
  chartBar: "15m",
  lastChartOpts: null,
  ohlcWired: false,
};

const TF_ORDER = ["1m", "3m", "5m", "15m", "30m"];
const OUTCOME_CN = { tp: "止盈", sl: "止损", timeout: "超时", sl_ambiguous: "止损*" };
const MA_COLORS = {
  sma20: "rgba(156,163,175,0.9)",
  ema20: "rgba(55,65,81,0.95)",
  sma60: "rgba(96,165,250,0.85)",
  ema60: "rgba(37,99,235,0.9)",
  sma120: "rgba(192,132,252,0.8)",
  ema120: "rgba(124,58,237,0.9)",
};

function hint(msg, kind = "") {
  const el = $("#hint");
  if (!el) return;
  el.textContent = msg;
  el.classList.remove("ok", "err");
  if (kind) el.classList.add(kind);
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  if (!res.ok) {
    const t = await res.text().catch(() => "");
    throw new Error(`请求失败 ${res.status}${t ? "：" + t.slice(0, 100) : ""}`);
  }
  return res.json();
}

function shortSym(s) {
  return String(s || "").replace(/_USDT_SWAP$/, "").replace(/-USDT-SWAP$/, "").replace(/_USDT$/, "");
}

function fmtChg(x) {
  if (x == null || Number.isNaN(Number(x))) return "—";
  const n = Number(x);
  return (n >= 0 ? "+" : "") + n.toFixed(1) + "%";
}

function gradeLabel(g) {
  return { A: "看", B: "观", C: "略" }[g] || g || "—";
}

function gradeTitle(g) {
  return { A: "值得看", B: "观察", C: "忽略" }[g] || g;
}

function tfClass(cell) {
  if (!cell || !cell.ok || cell.vote == null) return "cold";
  const v = Number(cell.vote);
  if (cell.dense || v >= 0.62) return "hot";
  if (v >= 0.48) return "warm";
  return "cold";
}

function tfText(bar, cell) {
  if (!cell || !cell.ok || cell.vote == null) return `${bar} ·`;
  const n = Math.round(100 * Number(cell.vote));
  const mark = cell.dense ? "●" : "○";
  return `${bar} ${mark}${n}`;
}

function filtered(rows) {
  if (!state.grade) return rows;
  return rows.filter((r) => r.grade === state.grade);
}

function escapeAttr(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;");
}
function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function outcomeLabel(o) {
  return OUTCOME_CN[o] || o || "—";
}

function toUnix(t) {
  if (t == null || t === "") return null;
  if (typeof t === "number" && Number.isFinite(t)) {
    return t > 1e12 ? Math.floor(t / 1000) : Math.floor(t);
  }
  const s = String(t).trim();
  if (!s) return null;
  // ms epoch as string
  if (/^\d{13}$/.test(s)) return Math.floor(Number(s) / 1000);
  if (/^\d{10}$/.test(s)) return Number(s);
  const d = new Date(s.includes("T") || s.includes(" ") ? s.replace(" ", "T") : s);
  if (Number.isNaN(d.getTime())) return null;
  return Math.floor(d.getTime() / 1000);
}

function moneyLabel(usd, prefix = "") {
  if (usd == null || !Number.isFinite(Number(usd))) return prefix;
  const n = Number(usd);
  const sign = n >= 0 ? "+" : "";
  return `${prefix}${prefix ? " " : ""}${sign}${n.toFixed(2)}U`;
}

function pctLabel(ret) {
  if (ret == null || !Number.isFinite(Number(ret))) return "";
  const n = 100 * Number(ret);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function fmtPx(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const a = Math.abs(n);
  if (a >= 1000) return n.toFixed(2);
  if (a >= 1) return n.toFixed(4);
  if (a >= 0.01) return n.toFixed(6);
  return n.toPrecision(4);
}

function fmtChartTime(t) {
  if (t == null) return "";
  const d = new Date(Number(t) * 1000);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
}

/* ---------- chart (TradingView-style entry / TP / SL / exit) ---------- */

function ensureChart() {
  const host = $("#scout-chart");
  const card = $("#chart-card");
  if (!host || typeof LightweightCharts === "undefined") {
    throw new Error("图表库未加载，请刷新页面");
  }
  card.hidden = false;
  if (state.chart) {
    state.chart.applyOptions({ width: host.clientWidth, height: host.clientHeight });
    return state.chart;
  }
  state.chart = LightweightCharts.createChart(host, {
    autoSize: true,
    layout: {
      background: { type: "solid", color: "#fafbfc" },
      textColor: "#6b7280",
      fontSize: 12,
    },
    grid: {
      vertLines: { color: "#eef0f4" },
      horzLines: { color: "#eef0f4" },
    },
    rightPriceScale: {
      borderColor: "#e5e7eb",
      scaleMargins: { top: 0.08, bottom: 0.08 },
    },
    timeScale: {
      borderColor: "#e5e7eb",
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 6,
      barSpacing: 7,
      minBarSpacing: 2,
    },
    crosshair: {
      mode: 1, // Magnet
      vertLine: { color: "rgba(107,114,128,0.45)", width: 1, style: 2, labelBackgroundColor: "#6b7280" },
      horzLine: { color: "rgba(107,114,128,0.45)", width: 1, style: 2, labelBackgroundColor: "#6b7280" },
    },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
    handleScale: { axisPressedMouseMove: { time: true, price: true }, mouseWheel: true, pinch: true },
  });
  state.series = state.chart.addCandlestickSeries({
    upColor: "#26a69a",
    downColor: "#ef5350",
    borderVisible: false,
    wickUpColor: "#26a69a",
    wickDownColor: "#ef5350",
  });
  if (!state.ohlcWired) {
    state.ohlcWired = true;
    state.chart.subscribeCrosshairMove((param) => {
      const el = $("#scout-ohlc");
      if (!el) return;
      if (!param || !param.time || !param.seriesData) return;
      const c = param.seriesData.get(state.series);
      if (!c || c.open == null) return;
      const chg = c.close - c.open;
      const chgPct = c.open ? (100 * chg) / c.open : 0;
      const up = chg >= 0;
      const cls = up ? "up" : "down";
      el.innerHTML =
        `<span class="ohlc-time">${fmtChartTime(param.time)}</span>` +
        `<span>O <b>${fmtPx(c.open)}</b></span>` +
        `<span>H <b>${fmtPx(c.high)}</b></span>` +
        `<span>L <b>${fmtPx(c.low)}</b></span>` +
        `<span>C <b class="${cls}">${fmtPx(c.close)}</b></span>` +
        `<span class="${cls}">${up ? "+" : ""}${chgPct.toFixed(2)}%</span>`;
    });
  }
  return state.chart;
}

function clearChartOverlays() {
  if (!state.series) return;
  state.priceLines.forEach((l) => {
    try { state.series.removePriceLine(l); } catch (_) { /* ignore */ }
  });
  state.priceLines = [];
  state.levelSeries.forEach((s) => {
    try { state.chart.removeSeries(s); } catch (_) { /* ignore */ }
  });
  state.levelSeries = [];
  state.maSeries.forEach((s) => {
    try { state.chart.removeSeries(s); } catch (_) { /* ignore */ }
  });
  state.maSeries = [];
  try { state.series.setMarkers([]); } catch (_) { /* ignore */ }
}

function addLevelLine(price, color, title, t0, t1, style = 0) {
  if (price == null || !Number.isFinite(Number(price)) || !state.series) return;
  const p = Number(price);
  const axisTitle = title ? `${title} ${fmtPx(p)}` : fmtPx(p);
  state.priceLines.push(state.series.createPriceLine({
    price: p,
    color,
    lineWidth: 1,
    lineStyle: style, // 0 solid, 2 dashed, 3 dotted
    axisLabelVisible: true,
    title: axisTitle,
  }));
  // Horizontal segment over trade window (TV order line feel)
  if (t0 != null && t1 != null && t1 >= t0) {
    const seg = state.chart.addLineSeries({
      color,
      lineWidth: 2,
      lineStyle: style,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      autoscaleInfoProvider: () => null,
    });
    seg.setData([
      { time: t0, value: p },
      { time: t1, value: p },
    ]);
    state.levelSeries.push(seg);
  }
}

function zoomAround(times, t0, t1, pad = 40) {
  if (!state.chart || !times.length) return;
  let i0 = times.findIndex((t) => t >= t0);
  let i1 = times.findIndex((t) => t >= t1);
  if (i0 < 0) i0 = Math.max(0, times.length - pad);
  if (i1 < 0) i1 = times.length - 1;
  const from = Math.max(0, i0 - pad);
  const to = Math.min(times.length - 1, i1 + pad);
  setTimeout(() => {
    state.chart.timeScale().setVisibleLogicalRange({ from: from - 0.5, to: to + 0.5 });
  }, 40);
}

/**
 * @param {object} opts
 * @param {string} opts.instId
 * @param {string} [opts.bar]
 * @param {string} [opts.title]
 * @param {string} [opts.sub]
 * @param {number|null} [opts.entry]
 * @param {number|null} [opts.exit]
 * @param {number|null} [opts.tp]
 * @param {number|null} [opts.sl]
 * @param {number|null} [opts.mark]
 * @param {string|number|null} [opts.entryTime]
 * @param {string|number|null} [opts.exitTime]
 * @param {number|null} [opts.upl]
 * @param {number|null} [opts.notional]
 * @param {number|null} [opts.ret]
 * @param {string} [opts.outcome]
 * @param {boolean} [opts.openPos]
 */
async function openTradeChart(opts) {
  const card = $("#chart-card");
  if (!card) return;
  const instId = opts.instId;
  if (!instId) {
    hint("缺少合约 ID，无法加载 K 线", "err");
    return;
  }
  state.lastChartOpts = { ...opts };
  const bar = opts.bar || state.chartBar || "15m";
  state.chartBar = bar;
  // sync TF buttons
  $$("#chart-tf-seg button").forEach((b) => b.classList.toggle("active", b.dataset.bar === bar));
  $("#chart-title").textContent = opts.title || shortSym(instId);
  $("#chart-sub").textContent = opts.sub || "加载 K 线…";
  card.hidden = false;
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });

  try {
    ensureChart();
  } catch (err) {
    hint(err.message, "err");
    return;
  }

  const q = new URLSearchParams({ inst_id: instId, bar, limit: "300" });
  let data;
  try {
    data = await api(`/api/scout-mtf/chart?${q}`);
  } catch (err) {
    $("#chart-sub").textContent = err.message;
    hint(err.message, "err");
    return;
  }
  if (!data.ok || !(data.candles || []).length) {
    $("#chart-sub").textContent = data.error || "无 K 线数据";
    return;
  }

  clearChartOverlays();
  const candles = data.candles;
  const times = candles.map((c) => c.time);
  state.series.setData(candles);
  // seed OHLC legend with last bar
  const last = candles[candles.length - 1];
  const ohlcEl = $("#scout-ohlc");
  if (ohlcEl && last) {
    const chg = last.close - last.open;
    const chgPct = last.open ? (100 * chg) / last.open : 0;
    const up = chg >= 0;
    const cls = up ? "up" : "down";
    ohlcEl.innerHTML =
      `<span class="ohlc-time">${fmtChartTime(last.time)}</span>` +
      `<span>O <b>${fmtPx(last.open)}</b></span>` +
      `<span>H <b>${fmtPx(last.high)}</b></span>` +
      `<span>L <b>${fmtPx(last.low)}</b></span>` +
      `<span>C <b class="${cls}">${fmtPx(last.close)}</b></span>` +
      `<span class="${cls}">${up ? "+" : ""}${chgPct.toFixed(2)}%</span>`;
  }

  // Display MAs
  for (const [key, color] of Object.entries(MA_COLORS)) {
    const pts = data.mas?.[key];
    if (!pts || !pts.length) continue;
    const s = state.chart.addLineSeries({
      color,
      lineWidth: key.startsWith("ema") ? 2 : 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    s.setData(pts);
    state.maSeries.push(s);
  }

  const lastT = times[times.length - 1];
  let tEntry = toUnix(opts.entryTime);
  let tExit = toUnix(opts.exitTime);
  if (tEntry == null) tEntry = times[Math.max(0, times.length - 80)];
  if (tExit == null) tExit = opts.openPos ? lastT : times[Math.min(times.length - 1, times.length - 10)];
  if (tExit < tEntry) tExit = lastT;

  const entry = opts.entry != null ? Number(opts.entry) : null;
  const exitPx = opts.exit != null ? Number(opts.exit) : null;
  const tp = opts.tp != null ? Number(opts.tp) : null;
  const sl = opts.sl != null ? Number(opts.sl) : null;
  const mark = opts.mark != null ? Number(opts.mark) : null;

  // USD labels (open pos: live upl; closed: estimate from ret * notional or % only)
  let entryTitle = "入场";
  let exitTitle = "出场";
  let tpTitle = "TP";
  let slTitle = "SL";
  if (opts.openPos) {
    entryTitle = moneyLabel(opts.upl, "入场");
    if (mark != null && entry != null) {
      const d = mark - entry;
      const sign = d >= 0 ? "+" : "";
      entryTitle = `入场 ${sign}${((d / entry) * 100).toFixed(2)}%`;
      if (opts.upl != null) entryTitle = moneyLabel(opts.upl, "持仓");
    }
  } else if (opts.ret != null) {
    exitTitle = `出场 ${outcomeLabel(opts.outcome)} ${pctLabel(opts.ret)}`;
    entryTitle = "入场";
  }
  if (tp != null && entry != null) {
    const tpRet = tp / entry - 1;
    tpTitle = `TP ${pctLabel(tpRet)}`;
    if (opts.notional != null) tpTitle = moneyLabel(opts.notional * tpRet, "TP");
  }
  if (sl != null && entry != null) {
    const slRet = sl / entry - 1;
    slTitle = `SL ${pctLabel(slRet)}`;
    if (opts.notional != null) slTitle = moneyLabel(opts.notional * slRet, "SL");
  }

  if (tp != null) addLevelLine(tp, "#26a69a", tpTitle, tEntry, tExit, 2);
  if (sl != null) addLevelLine(sl, "#ff9800", slTitle, tEntry, tExit, 2);
  if (entry != null) addLevelLine(entry, "#2962ff", entryTitle, tEntry, tExit, 0);
  if (exitPx != null && !opts.openPos) {
    addLevelLine(exitPx, "#7b61ff", exitTitle, tEntry, tExit, 0);
  }
  if (mark != null) {
    addLevelLine(mark, "#9ca3af", opts.openPos ? "标记价" : "最新", tEntry, lastT, 3);
  }

  // Path entry → exit (or mark)
  const pathEnd = opts.openPos ? (mark ?? entry) : exitPx;
  if (entry != null && pathEnd != null) {
    const path = state.chart.addLineSeries({
      color: (opts.ret != null && opts.ret < 0) || (opts.upl != null && opts.upl < 0) ? "#ef5350" : "#26a69a",
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      autoscaleInfoProvider: () => null,
    });
    path.setData([
      { time: tEntry, value: entry },
      { time: tExit, value: pathEnd },
    ]);
    state.levelSeries.push(path);
  }

  const markers = [];
  if (entry != null) {
    markers.push({
      time: tEntry,
      position: "belowBar",
      shape: "arrowUp",
      color: "#2962ff",
      text: "入",
      size: 2,
    });
  }
  if (exitPx != null && !opts.openPos) {
    markers.push({
      time: tExit,
      position: "aboveBar",
      shape: "arrowDown",
      color: opts.ret != null && opts.ret >= 0 ? "#26a69a" : "#ef5350",
      text: outcomeLabel(opts.outcome).slice(0, 2),
      size: 2,
    });
  }
  state.series.setMarkers(markers.sort((a, b) => a.time - b.time));

  // Default zoom: if trade window, focus it; else last ~100 bars (TV-like)
  if (opts.entry != null || opts.exit != null || opts.openPos) {
    zoomAround(times, tEntry, tExit, 40);
  } else {
    const n = Math.min(100, times.length);
    const from = Math.max(0, times.length - n);
    setTimeout(() => {
      state.chart.timeScale().setVisibleLogicalRange({
        from: from - 0.5,
        to: times.length + 4,
      });
    }, 40);
  }

  const bits = [
    data.bar || bar,
    `${candles.length} 根`,
    entry != null ? `入 ${fmtPx(entry)}` : null,
    exitPx != null ? `出 ${fmtPx(exitPx)}` : null,
    opts.outcome ? outcomeLabel(opts.outcome) : null,
  ].filter(Boolean);
  $("#chart-sub").textContent = bits.join(" · ");
  state.chartKey = `${instId}:${bar}:${tEntry}:${tExit}`;
  hint(`已打开 ${shortSym(instId)} · ${bar}`, "ok");
}

function closeChart() {
  const card = $("#chart-card");
  if (card) card.hidden = true;
  state.chartKey = null;
}

/* ---------- list renders ---------- */

function renderStats(d) {
  const st = d.status || {};
  const sum = d.summary || { A: 0, B: 0, C: 0 };
  $("#st-a").textContent = sum.A ?? 0;
  $("#st-b").textContent = sum.B ?? 0;
  $("#st-c").textContent = sum.C ?? 0;
  const run = $("#st-run");
  const box = run.closest(".stat");
  if (st.running) {
    run.textContent = "扫描中";
    box?.classList.add("running");
    $("#st-time").textContent = "请稍候…";
  } else {
    run.textContent = d.available ? "已更新" : "未扫描";
    box?.classList.remove("running");
    const t = d.generated_at || st.latest_mtime || "";
    $("#st-time").textContent = t
      ? String(t).replace("T", " ").replace(/\+00:00|Z/, " UTC").slice(0, 19)
      : "点右上角开始";
  }
}

function renderList(d) {
  const host = $("#list");
  const rows = filtered(d.rows || []);
  if (!rows.length) {
    host.innerHTML = `<div class="empty">${
      d.available ? "这一档暂时没有币" : (d.message || "还没有结果，点右上角「开始扫描」")
    }</div>`;
    return;
  }

  host.innerHTML = rows.map((r) => {
    const open = state.open === r.symbol ? "open" : "";
    const chgCls = Number(r.chg24h_pct) >= 0 ? "up" : "down";
    const side = r.rank_side === "gain" ? "涨幅榜" : "跌幅榜";
    const tfs = TF_ORDER.map((bar) => {
      const c = r.tf?.[bar];
      return `<span class="tf ${tfClass(c)}" data-bar="${escapeAttr(bar)}" title="${escapeAttr((c?.note || bar) + " · 点此看该周期K线")}">${escapeHtml(tfText(bar, c))}</span>`;
    }).join("");

    const details = TF_ORDER.map((bar) => {
      const c = r.tf?.[bar] || {};
      const vote = c.vote == null ? "—" : Number(c.vote).toFixed(2);
      const dens = c.dense ? "密集" : "一般";
      const pos = c.above_ema55 === true ? "站上均线" : c.above_ema55 === false ? "均线下方" : "—";
      return `<div class="detail-item" data-bar="${escapeAttr(bar)}" title="点此看 ${escapeAttr(bar)} K线"><b>${bar} · ${vote}</b><span>${dens} · ${pos}</span></div>`;
    }).join("");

    const inst = r.inst_id || String(r.symbol || "").replace(/_/g, "-");
    return `
      <article class="row ${open}" data-symbol="${escapeAttr(r.symbol)}" data-inst="${escapeAttr(inst)}" data-last="${escapeAttr(r.last ?? "")}">
        <div class="badge ${escapeAttr(r.grade)}" title="${escapeAttr(gradeTitle(r.grade))}">${escapeHtml(gradeLabel(r.grade))}</div>
        <div class="main">
          <div class="sym">${escapeHtml(shortSym(r.symbol))}</div>
          <div class="sym-sub">${escapeHtml(side)} #${r.rank ?? "—"} · 综合 ${r.composite != null ? Number(r.composite).toFixed(2) : "—"} · 点开看K线</div>
        </div>
        <div class="chg ${chgCls}">${fmtChg(r.chg24h_pct)}</div>
        <div class="score">${r.composite != null ? Number(r.composite).toFixed(2) : "—"}</div>
        <div class="tfs">${tfs}</div>
        <div class="detail">
          <div>${escapeHtml(gradeTitle(r.grade))} · 点周期标签切换 K 线周期</div>
          <div class="detail-grid">${details}</div>
        </div>
      </article>`;
  }).join("");
}

async function openRadarChart(rowEl, bar = "15m") {
  if (!rowEl) return;
  const inst = rowEl.dataset.inst || String(rowEl.dataset.symbol || "").replace(/_/g, "-");
  const sym = shortSym(rowEl.dataset.symbol || inst);
  const last = rowEl.dataset.last !== "" && rowEl.dataset.last != null
    ? Number(rowEl.dataset.last)
    : null;
  const r = (state.data?.rows || []).find((x) => x.symbol === rowEl.dataset.symbol);
  const grade = r?.grade ? gradeTitle(r.grade) : "";
  const comp = r?.composite != null ? Number(r.composite).toFixed(2) : "—";
  const useBar = bar || state.chartBar || "15m";
  await openTradeChart({
    instId: inst,
    bar: useBar,
    title: `${sym} · ${useBar}`,
    sub: `${grade || "雷达"} · 综合 ${comp} · 滚轮缩放 / 十字线`,
    mark: Number.isFinite(last) ? last : null,
    openPos: false,
  });
}

function renderPaper(p) {
  if (!p || (p.ok === false && !p.available && !p.n_trades)) {
    $("#p-st").textContent = p?.error ? "失败" : "未跑";
    $("#p-n").textContent = "—";
    $("#p-wr").textContent = "—";
    $("#p-net").textContent = "—";
    $("#p-net").classList.remove("up", "down");
    $("#paper-list").innerHTML = `<div class="empty soft">${escapeHtml(p?.error || p?.message || "先扫描，再点「模拟测试」")}</div>`;
    return;
  }
  if (p.ok === false && p.error) {
    $("#p-st").textContent = "失败";
    $("#paper-list").innerHTML = `<div class="empty soft">${escapeHtml(p.error)}</div>`;
    return;
  }
  $("#p-st").textContent = "已完成";
  $("#p-n").textContent = p.n_trades ?? 0;
  $("#p-wr").textContent = p.win_rate == null ? "—" : (100 * Number(p.win_rate)).toFixed(0) + "%";
  const net = Number(p.total_net_units || 0);
  const netEl = $("#p-net");
  netEl.textContent = (net >= 0 ? "+" : "") + (100 * net).toFixed(2) + "%";
  netEl.classList.toggle("up", net > 0);
  netEl.classList.toggle("down", net < 0);

  const rows = (p.symbols || []).filter((s) => s.ok);
  if (!rows.length) {
    $("#paper-list").innerHTML = `<div class="empty soft">这些币最近历史上几乎没有「密集+站上均线」入场</div>`;
    return;
  }
  $("#paper-list").innerHTML = rows.map((s) => {
    const mean = s.mean_net == null ? "—" : ((100 * Number(s.mean_net)).toFixed(2) + "%");
    const total = s.total_net == null ? "—" : ((Number(s.total_net) >= 0 ? "+" : "") + (100 * Number(s.total_net)).toFixed(2) + "%");
    const wr = s.win_rate == null ? "—" : ((100 * Number(s.win_rate)).toFixed(0) + "%");
    const name = String(s.symbol || "").replace(/_USDT_SWAP$/, "");
    const open = state.paperOpen === s.symbol ? "open" : "";
    const trades = s.trades || [];
    const tradeHtml = trades.length
      ? trades.map((t, i) => {
          const n = Number(t.net_ret || 0);
          const cls = n >= 0 ? "up" : "down";
          return `<div class="trade-line" data-paper-trade="${escapeAttr(s.symbol)}" data-trade-i="${i}">
            <span><b>#${i + 1}</b> ${escapeHtml(String(t.signal_time || t.entry_time || "").slice(0, 16))}</span>
            <span>${escapeHtml(outcomeLabel(t.outcome))}</span>
            <span>进 ${t.entry_px ?? "—"}</span>
            <span>出 ${t.exit_px ?? "—"}</span>
            <span class="${cls}">${(n >= 0 ? "+" : "") + (100 * n).toFixed(2)}%</span>
          </div>`;
        }).join("")
      : `<div class="muted">暂无成交明细</div>`;
    return `<div class="paper-row ${open}" data-paper-sym="${escapeAttr(s.symbol)}" data-inst="${escapeAttr(s.inst_id || "")}">
      <div><div class="name">${escapeHtml(name)}</div><div class="muted">${escapeHtml(s.grade || "")} · 点开看每笔 · 再点一笔看 K 线</div></div>
      <div class="num">${s.n_trades ?? 0} 笔</div>
      <div class="num">${wr}</div>
      <div class="num">${mean}</div>
      <div class="num ${Number(s.total_net) >= 0 ? "up" : "down"}">${total}</div>
      <div class="trade-detail">${tradeHtml}</div>
    </div>`;
  }).join("");
}

function renderPositions(pos) {
  const host = $("#pos-list");
  if (!host) return;
  if (!pos || pos.ok === false) {
    host.innerHTML = `<div class="empty soft">${escapeHtml(pos?.error || "无法读取持仓")}</div>`;
    return;
  }
  const rows = pos.positions || [];
  if (!rows.length) {
    host.innerHTML = `<div class="empty soft">当前没有合约持仓 · ${escapeHtml(pos.environment || "")}</div>`;
    return;
  }
  host.innerHTML = rows.map((p) => {
    const open = state.posOpen === p.inst_id ? "open" : "";
    const name = String(p.symbol || p.inst_id || "").replace(/_USDT_SWAP$/, "").replace(/-USDT-SWAP$/, "");
    const upl = Number(p.upl || 0);
    const uplCls = upl >= 0 ? "up" : "down";
    const side = p.pos_side === "long" ? "多" : p.pos_side === "short" ? "空" : (p.pos_side || "—");
    return `<div class="paper-row pos-row ${open}" data-pos-id="${escapeAttr(p.inst_id)}">
      <div>
        <div class="name">${escapeHtml(name)}</div>
        <div class="muted">${escapeHtml(side)} · ${escapeHtml(String(p.pos))} 张 · 点开看 K 线</div>
      </div>
      <div class="num">${escapeHtml(String(p.lever || "—"))}x</div>
      <div class="num">${p.avg_px != null ? Number(p.avg_px).toPrecision(6) : "—"}</div>
      <div class="num">${p.notional_usd != null ? Number(p.notional_usd).toFixed(1) + "U" : "—"}</div>
      <div class="num ${uplCls}">${(upl >= 0 ? "+" : "") + upl.toFixed(3)}U</div>
      <div class="pos-detail">
        <div>合约 ${escapeHtml(p.inst_id || "")} · 模式 ${escapeHtml(p.mgn_mode || "—")} · 环境 ${escapeHtml(pos.environment || "—")}</div>
        <div class="pos-detail-grid">
          <div><span>均价</span><b>${p.avg_px != null ? Number(p.avg_px) : "—"}</b></div>
          <div><span>标记价</span><b>${p.mark_px != null ? Number(p.mark_px) : "—"}</b></div>
          <div><span>浮盈</span><b class="${uplCls}">${(upl >= 0 ? "+" : "") + upl.toFixed(4)} U</b></div>
          <div><span>名义</span><b>${p.notional_usd != null ? Number(p.notional_usd).toFixed(2) + " U" : "—"}</b></div>
          <div><span>保证金</span><b>${p.margin != null ? Number(p.margin).toFixed(4) : "—"}</b></div>
          <div><span>收益率</span><b>${p.upl_ratio != null ? (100 * Number(p.upl_ratio)).toFixed(2) + "%" : "—"}</b></div>
        </div>
      </div>
    </div>`;
  }).join("");
}

async function openPositionChart(p) {
  // Estimate TP/SL for display using strategy default TP5/SL2 on ~ATR-less: use 5%/2% of entry as soft guide
  // Prefer pure entry+mark when no ATR; user sees live P&L like TV.
  const entry = p.avg_px != null ? Number(p.avg_px) : null;
  const mark = p.mark_px != null ? Number(p.mark_px) : null;
  // Soft visual barriers (not exchange orders): ± strategy-ish levels from entry
  let tp = null;
  let sl = null;
  if (entry != null) {
    // Use ~ATR proxy: 1% of price as "1 ATR" for display only when live ATR unavailable
    const atrProxy = entry * 0.01;
    tp = entry + 5 * atrProxy;
    sl = entry - 2 * atrProxy;
  }
  await openTradeChart({
    instId: p.inst_id,
    bar: "15m",
    title: `${shortSym(p.inst_id || p.symbol)} 持仓`,
    sub: "实盘只读 · 蓝=入场 绿=参考TP 橙=参考SL 灰=标记价",
    entry,
    mark,
    tp,
    sl,
    entryTime: p.created_time,
    openPos: true,
    upl: p.upl != null ? Number(p.upl) : null,
    notional: p.notional_usd != null ? Number(p.notional_usd) : null,
  });
}

async function openPaperTradeChart(symRow, trade) {
  const inst = symRow.inst_id || String(symRow.symbol || "").replace(/_/g, "-");
  await openTradeChart({
    instId: inst,
    bar: "15m",
    title: `${shortSym(symRow.symbol)} 模拟 #`,
    sub: "纸面回放 · 入场 / 出场 / TP5·SL2",
    entry: trade.entry_px,
    exit: trade.exit_px,
    tp: trade.tp_px,
    sl: trade.sl_px,
    entryTime: trade.entry_time || trade.signal_time,
    exitTime: trade.exit_time,
    ret: trade.net_ret != null ? trade.net_ret : trade.gross_ret,
    outcome: trade.outcome,
    openPos: false,
    notional: 100, // paper unit notional for USD-style axis labels
  });
}

async function loadPositions() {
  try {
    const pos = await api("/api/scout-mtf/positions");
    state.positions = pos;
    renderPositions(pos);
  } catch (err) {
    $("#pos-list").innerHTML = `<div class="empty soft">${escapeHtml(err.message)}</div>`;
  }
}

async function loadLatest() {
  try {
    const [d, p] = await Promise.all([
      api("/api/scout-mtf/latest"),
      api("/api/scout-mtf/paper").catch(() => null),
    ]);
    state.data = d;
    state.paper = p;
    renderStats(d);
    renderList(d);
    if (p) renderPaper(p);
    loadPositions();
  } catch (err) {
    hint(err.message, "err");
    $("#list").innerHTML = `<div class="empty">加载失败</div>`;
  }
}

function setBusy(busy, label) {
  ["#btn-run", "#btn-paper", "#btn-refresh", "#btn-pos"].forEach((sel) => {
    const el = $(sel);
    if (el) el.disabled = busy;
  });
  if (label) $("#btn-run").textContent = label;
  if (!busy) {
    $("#btn-run").textContent = "开始扫描";
    $("#btn-paper").textContent = "模拟测试";
  }
}

async function runScan() {
  setBusy(true, "扫描中…");
  hint("正在扫描榜单与多周期，大约半分钟…", "ok");
  if (state.data) {
    state.data.status = { ...(state.data.status || {}), running: true };
    renderStats(state.data);
  }
  try {
    const d = await api("/api/scout-mtf/run", {
      method: "POST",
      body: JSON.stringify({
        top: 12,
        min_vol: 5_000_000,
        include_loss: true,
        max_symbols: 16,
      }),
    });
    if (d.ok === false) {
      hint(d.error || "扫描失败", "err");
      return;
    }
    state.data = d;
    state.open = null;
    renderStats(d);
    renderList(d);
    const s = d.summary || {};
    hint(`扫描完成：值得看 ${s.A ?? 0} · 观察 ${s.B ?? 0} · 忽略 ${s.C ?? 0}`, "ok");
  } catch (err) {
    hint(err.message, "err");
  } finally {
    setBusy(false);
  }
}

async function runPaper() {
  setBusy(true);
  $("#btn-paper").textContent = "模拟中…";
  hint("纸面回放 A/B 涨幅币最近 15m…", "ok");
  try {
    const p = await api("/api/scout-mtf/paper-run", { method: "POST", body: "{}" });
    state.paper = p;
    renderPaper(p);
    if (p.ok === false) {
      hint(p.error || "模拟失败", "err");
      return;
    }
    const net = Number(p.total_net_units || 0);
    hint(
      `模拟完成：${p.n_trades ?? 0} 笔 · 胜率 ${
        p.win_rate == null ? "—" : (100 * p.win_rate).toFixed(0) + "%"
      } · 合计净 ${(net >= 0 ? "+" : "") + (100 * net).toFixed(2)}%`,
      "ok"
    );
  } catch (err) {
    hint(err.message, "err");
  } finally {
    setBusy(false);
  }
}

function wire() {
  $("#btn-run")?.addEventListener("click", () => runScan());
  $("#btn-paper")?.addEventListener("click", () => runPaper());
  $("#btn-refresh")?.addEventListener("click", () => loadLatest());
  $("#btn-pos")?.addEventListener("click", () => loadPositions());
  $("#btn-chart-close")?.addEventListener("click", () => closeChart());
  $("#chart-tf-seg")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-bar]");
    if (!btn || !state.lastChartOpts) return;
    const bar = btn.dataset.bar;
    state.chartBar = bar;
    $$("#chart-tf-seg button").forEach((b) => b.classList.toggle("active", b === btn));
    await openTradeChart({ ...state.lastChartOpts, bar });
  });
  $$("#tabs .tab").forEach((b) => {
    b.addEventListener("click", () => {
      $$("#tabs .tab").forEach((x) => x.classList.toggle("active", x === b));
      state.grade = b.dataset.g || "";
      if (state.data) renderList(state.data);
    });
  });
  $("#list")?.addEventListener("click", async (e) => {
    const row = e.target.closest(".row[data-symbol]");
    if (!row) return;
    // Click a TF pill / detail cell → that timeframe's kline
    const tfHit = e.target.closest(".tf[data-bar], .detail-item[data-bar]");
    const bar = tfHit?.dataset?.bar || "15m";
    const sym = row.dataset.symbol;
    const same = state.open === sym;
    state.open = same && !tfHit ? null : sym;
    if (state.data) renderList(state.data);
    // Re-query row after re-render (DOM replaced)
    const fresh = $(`#list .row[data-symbol="${CSS.escape(sym)}"]`);
    if (state.open) {
      await openRadarChart(fresh || row, bar);
    } else {
      closeChart();
    }
  });
  $("#paper-list")?.addEventListener("click", async (e) => {
    const tradeEl = e.target.closest(".trade-line[data-paper-trade]");
    if (tradeEl) {
      e.stopPropagation();
      const sym = tradeEl.dataset.paperTrade;
      const i = Number(tradeEl.dataset.tradeI);
      const row = (state.paper?.symbols || []).find((s) => s.symbol === sym);
      const trade = row?.trades?.[i];
      if (!row || !trade) return;
      $$(".trade-line").forEach((x) => x.classList.toggle("active", x === tradeEl));
      await openPaperTradeChart(row, trade);
      return;
    }
    const row = e.target.closest(".paper-row[data-paper-sym]");
    if (!row) return;
    const sym = row.dataset.paperSym;
    state.paperOpen = state.paperOpen === sym ? null : sym;
    if (state.paper) renderPaper(state.paper);
  });
  $("#pos-list")?.addEventListener("click", async (e) => {
    const row = e.target.closest(".pos-row[data-pos-id]");
    if (!row) return;
    const id = row.dataset.posId;
    state.posOpen = state.posOpen === id ? null : id;
    if (state.positions) renderPositions(state.positions);
    if (state.posOpen) {
      const p = (state.positions.positions || []).find((x) => x.inst_id === id);
      if (p) await openPositionChart(p);
    } else {
      closeChart();
    }
  });
}

wire();
loadLatest();
