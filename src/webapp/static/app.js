/* fable-trading dashboard frontend (vanilla JS + Lightweight Charts v4) */
const $ = (sel) => document.querySelector(sel);
const fmtPct = (x, digits = 2) => (100 * x).toFixed(digits) + "%";
const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const OUTCOME_CN = { tp: "止盈", sl: "止损", timeout: "超时", sl_ambiguous: "止损*" };
const OUTCOME_COLOR = { tp: "#199e70", sl: "#e66767", timeout: "#c98500", sl_ambiguous: "#e66767" };
const CHART_LAYOUT = {
  layout: { background: { type: "solid", color: "#1b1e24" }, textColor: "#9aa0a8" },
  grid: { vertLines: { color: "#242833" }, horzLines: { color: "#242833" } },
  timeScale: { borderColor: "#2e3340", timeVisible: true },
  rightPriceScale: { borderColor: "#2e3340" },
  crosshair: { mode: 0 },
};
const pctFormat = { type: "custom", formatter: (v) => v.toFixed(2) + "%" };

function makeChart(el, opts = {}) {
  return LightweightCharts.createChart(el, { ...CHART_LAYOUT, autoSize: true, ...opts });
}

/* ---------- tabs ---------- */
function showView(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
  $("#view-" + name).classList.remove("hidden");
  if (name === "backtest") loadBacktest();
  if (name === "signals") initSignals();
}
document.querySelectorAll(".tab").forEach((btn) =>
  btn.addEventListener("click", () => showView(btn.dataset.view)));

/* ---------- generic horizontal bars ---------- */
function renderHBars(el, rows) {
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

/* ---------- overview ---------- */
let sparkChart = null;
async function loadOverview() {
  const d = await (await fetch("/api/overview")).json();
  $("#verdict-banner").innerHTML =
    `当前判定：<b>阶段 3 第一轮验收未通过（PF 1.01 @ 0.3% 成本）</b> —— ${d.next}`;
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
    const s = sparkChart.addAreaSeries({
      lineColor: "#3987e5", lineWidth: 2, priceFormat: pctFormat,
      topColor: "rgba(57,135,229,0.25)", bottomColor: "rgba(57,135,229,0.02)",
    });
    s.setData(d.sparkline);
    sparkChart.timeScale().fitContent();
  }
}

/* ---------- backtest ---------- */
const btState = { cost: 0.003, window: "accept", outcome: "", filter: "", sort: "entry_time", dir: -1 };
let equityChart, equitySeries, ddChart, ddSeries, pfChart, pfSeries, pfLine;
let tradeRows = [];

function segWire(id, state, key, parse, cb) {
  $(id).querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    $(id).querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    state[key] = parse(b.dataset[key]);
    cb();
  }));
}
segWire("#cost-seg", btState, "cost", Number, loadBacktest);
segWire("#window-seg", btState, "window", String, loadBacktest);
segWire("#outcome-seg", btState, "outcome", String, renderTrades);
$("#trade-filter").addEventListener("input", (e) => { btState.filter = e.target.value.toUpperCase(); renderTrades(); });
document.querySelectorAll("#trades-table th.sortable").forEach((th) =>
  th.addEventListener("click", () => {
    const k = th.dataset.sort;
    btState.dir = btState.sort === k ? -btState.dir : -1;
    btState.sort = k;
    document.querySelectorAll("#trades-table th.sortable").forEach((h) =>
      h.textContent = h.textContent.replace(/ [↓↑]$/, "") + (h === th ? (btState.dir < 0 ? " ↓" : " ↑") : ""));
    renderTrades();
  }));

async function loadBacktest() {
  $("#view-backtest").classList.add("loading");
  const [d, rows] = await Promise.all([
    (await fetch(`/api/backtest?cost=${btState.cost}`)).json(),
    (await fetch(`/api/trades?window=${btState.window}&cost=${btState.cost}`)).json(),
  ]);
  tradeRows = rows;
  const w = d[btState.window];

  $("#bt-tiles").innerHTML = `
    <div class="tile"><span class="lbl">交易笔数</span><b>${w.n_trades}</b><small>${btState.window === "accept" ? "验收窗口" : "全期"}</small></div>
    <div class="tile"><span class="lbl">净收益（对资金）</span><b class="${cls(w.net_return_on_capital)}">${fmtPct(w.net_return_on_capital)}</b><small>单笔均值 ${fmtPct(w.mean_net_per_trade, 3)}</small></div>
    <div class="tile"><span class="lbl">盈亏比 PF</span><b class="${w.profit_factor >= 1.3 ? "pos" : "neg"}">${w.profit_factor}</b><small>验收线 1.3</small></div>
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
    label: r.symbol.replace("_USDT", ""), value: r.net, text: `${r.net.toFixed(1)}%·${r.n}笔`,
  })));

  renderTrades();
  $("#view-backtest").classList.remove("loading");
}

function renderTrades() {
  let rows = tradeRows;
  if (btState.outcome) rows = rows.filter((r) => r.outcome.startsWith(btState.outcome));
  if (btState.filter) rows = rows.filter((r) => r.symbol.includes(btState.filter));
  rows = rows.slice().sort((a, b) => {
    const va = a[btState.sort], vb = b[btState.sort];
    return (va < vb ? -1 : va > vb ? 1 : 0) * btState.dir;
  });
  $("#trades-count").textContent = `（${rows.length} 笔）`;
  $("#trades-table tbody").innerHTML = rows.slice(0, 400).map((r, i) => `
    <tr data-i="${i}" data-source="${r.source}" data-symbol="${r.symbol}" data-entry="${r.entry_time}">
      <td>${r.entry_time.slice(0, 16)}</td>
      <td>${r.symbol}</td>
      <td class="num">${r.score.toFixed(3)}</td>
      <td class="outcome-${r.outcome}">${OUTCOME_CN[r.outcome] || r.outcome}</td>
      <td class="num"><span class="${cls(r.gross_ret)}">${fmtPct(r.gross_ret)}</span></td>
      <td class="num"><span class="${cls(r.net_ret)}">${fmtPct(r.net_ret)}</span></td>
    </tr>`).join("");
  $("#trades-table tbody").querySelectorAll("tr").forEach((tr) =>
    tr.addEventListener("click", () => focusTrade(tr.dataset.source, tr.dataset.symbol, tr.dataset.entry)));
}

/* ---------- signals browser ---------- */
let symbolsLoaded = false, klineChart, klineSeries, volumeSeries, emaSeries = [];
let bandSeries, pathSeries, barrier = { tp: 4, sl: 2 };
let currentKey = "", currentMarkers = [], currentTimes = [], priceLines = [], chartReq = 0;
let lastFocusRange = null;
const sigState = { bars: 3000 };
segWire("#bars-seg", sigState, "bars", Number, () => currentKey && loadChart(currentKey));

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
    topColor: "rgba(57,135,229,0.14)", bottomColor: "rgba(57,135,229,0.14)",
  });
  klineChart.priceScale("band").applyOptions({ visible: false, scaleMargins: { top: 0, bottom: 0 } });
  klineSeries = klineChart.addCandlestickSeries({
    upColor: "#1fa77d", downColor: "#e66767", borderVisible: false,
    wickUpColor: "#1fa77d", wickDownColor: "#e66767",
  });
  volumeSeries = klineChart.addHistogramSeries({
    priceScaleId: "vol", priceFormat: { type: "volume" },
    priceLineVisible: false, lastValueVisible: false,
  });
  klineChart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  // entry->exit path segment of the focused trade
  pathSeries = klineChart.addLineSeries({
    lineWidth: 3, priceLineVisible: false, lastValueVisible: false,
    crosshairMarkerVisible: false,
  });
}

async function initSignals() {
  if (symbolsLoaded) return;
  symbolsLoaded = true;
  const rows = await (await fetch("/api/symbols")).json();
  $("#symbol-list").innerHTML = rows.map((r) =>
    `<option value="${r.source}:${r.symbol}">${r.symbol}（成交 ${r.n_trades} / 合格 ${r.n_eligible}）</option>`).join("");
  $("#symbol-input").addEventListener("change", () => loadChart($("#symbol-input").value));
  const first = rows.find((r) => r.n_trades > 0) || rows[0];
  if (first && !currentKey) {
    $("#symbol-input").value = `${first.source}:${first.symbol}`;
    loadChart($("#symbol-input").value);
  }
}

const EMA_COLORS = {
  8: "rgba(57,135,229,0.9)", 13: "rgba(57,135,229,0.7)", 21: "rgba(57,135,229,0.55)",
  34: "rgba(57,135,229,0.4)", 55: "rgba(57,135,229,0.3)",
  144: "#9085e9", 200: "#d55181",
};

async function loadChart(key, focusEntry = null) {
  const [source, symbol] = key.split(":");
  if (!symbol) return;
  currentKey = key;
  ensureKlineChart();          // synchronous: no await between check and create
  const reqId = ++chartReq;    // stale responses (slow links) are dropped
  $("#view-signals").classList.add("loading");
  const resp = await fetch(`/api/chart/${source}/${symbol}?bars=${sigState.bars}`);
  $("#view-signals").classList.remove("loading");
  if (reqId !== chartReq) return;
  if (!resp.ok) { $("#symbol-info").textContent = "找不到该序列"; return; }
  const d = await resp.json();
  if (reqId !== chartReq) return;
  barrier = { tp: d.tp_mult, sl: d.sl_mult };

  priceLines.forEach((l) => klineSeries.removePriceLine(l)); priceLines = [];
  emaSeries.forEach((s) => klineChart.removeSeries(s)); emaSeries = [];
  bandSeries.setData([]); pathSeries.setData([]);
  currentTimes = d.candles.map((c) => c.time);
  klineSeries.setData(d.candles);
  volumeSeries.setData(d.candles.map((c) => ({
    time: c.time, value: c.volume,
    color: c.close >= c.open ? "rgba(31,167,125,0.35)" : "rgba(230,103,103,0.35)",
  })));
  for (const [span, data] of Object.entries(d.emas)) {
    const s = klineChart.addLineSeries({
      color: EMA_COLORS[span] || "#666", lineWidth: span >= 144 ? 2 : 1,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    s.setData(data);
    emaSeries.push(s);
  }
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
  $("#side-count").textContent = `（${traded.length} 笔）`;
  $("#symbol-trades").innerHTML = "<tbody>" + traded.map((m) => `
    <tr data-entry-ts="${m.entry_time}">
      <td>${new Date(m.time * 1000).toISOString().slice(5, 16).replace("T", " ")}</td>
      <td class="outcome-${m.outcome}">${OUTCOME_CN[m.outcome] || m.outcome}</td>
      <td class="num"><span class="${cls(m.ret)}">${fmtPct(m.ret, 1)}</span></td>
    </tr>`).join("") + "</tbody>";
  $("#symbol-trades").querySelectorAll("tr").forEach((row) =>
    row.addEventListener("click", () => {
      $("#symbol-trades").querySelectorAll("tr").forEach((x) => x.classList.toggle("focused", x === row));
      focusMarker(Number(row.dataset.entryTs));
    }));

  // default: focus the most recent trade so entry/exit/barriers show immediately
  if (!focusEntry && traded.length) focusEntry = traded[0].entry_time;
  if (focusEntry) {
    focusMarker(focusEntry);
    const row = $(`#symbol-trades tr[data-entry-ts="${focusEntry}"]`);
    if (row) row.classList.add("focused");
  }
}

function focusMarker(entryTs) {
  const m = currentMarkers.find((x) => x.entry_time === entryTs);
  if (!m) return;
  priceLines.forEach((l) => klineSeries.removePriceLine(l)); priceLines = [];
  const entry = m.entry_price;
  const exitPrice = entry * (1 + m.ret);
  const outcomeColor = OUTCOME_COLOR[m.outcome] || "#9aa0a8";
  priceLines.push(klineSeries.createPriceLine({
    price: entry, color: "#9aa0a8", lineStyle: 2, title: "入场",
  }));
  priceLines.push(klineSeries.createPriceLine({  // v-label barriers of this trade
    price: entry * (1 + barrier.tp * m.atr_pct), color: "rgba(31,167,125,0.8)",
    lineStyle: 3, title: `止盈目标 +${barrier.tp}×ATR`,
  }));
  priceLines.push(klineSeries.createPriceLine({
    price: entry * (1 - barrier.sl * m.atr_pct), color: "rgba(230,103,103,0.8)",
    lineStyle: 3, title: `止损线 -${barrier.sl}×ATR`,
  }));
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

loadOverview();
