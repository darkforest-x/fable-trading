/* fable-trading dashboard frontend (vanilla JS + Lightweight Charts v4) */
/* allow: SIZE_OK -- legacy single-file dashboard kept stable for scoped P2-10 UI polish. */
const $ = (sel) => document.querySelector(sel);
const fmtPct = (x, digits = 2) => (x === null || x === undefined || Number.isNaN(Number(x)) ? "—" : (100 * x).toFixed(digits) + "%");
const fmtPF = (x) => (x === null || x === undefined || Number.isNaN(Number(x)) ? "—" : Number(x).toFixed(2));
const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
const OUTCOME_CN = { tp: "止盈", sl: "止损", timeout: "超时", sl_ambiguous: "止损*", "": "未结束" };
const OUTCOME_COLOR = { tp: "#199e70", sl: "#e66767", timeout: "#c98500", sl_ambiguous: "#e66767" };
const STATUS_CN = { open: "持有中", closed: "已结束" };
const appState = { universe: "swap" };
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

function apiUrl(path, params = {}) {
  const query = new URLSearchParams({ universe: appState.universe, ...params });
  return `${path}?${query.toString()}`;
}

/* ---------- tabs ---------- */
function showView(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => {
    const active = v.id === "view-" + name;
    v.classList.toggle("hidden", !active);
    v.hidden = !active;
    v.setAttribute("aria-hidden", active ? "false" : "true");
  });
  if (name === "backtest") loadBacktest();
  if (name === "signals") initSignals();
  if (name === "forward") loadForward();
  if (name === "experiments") loadExperiments();
  if (name === "agenda") loadAgenda();
  if (name === "jobs") loadJobsView();
  if (name === "data") loadDataHub();
  if (name === "models") loadModelHub();
}
document.querySelectorAll(".tab").forEach((btn) =>
  btn.addEventListener("click", () => showView(btn.dataset.view)));

document.querySelectorAll("#universe-seg button").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll("#universe-seg button").forEach((b) => b.classList.toggle("active", b === btn));
    appState.universe = btn.dataset.universe;
    symbolsLoaded = false;
    currentKey = "";
    $("#symbol-list").innerHTML = "";
    $("#symbol-input").value = "";
    const active = document.querySelector(".tab.active")?.dataset.view || "overview";
    if (active === "overview") loadOverview();
    if (active === "backtest") loadBacktest();
    if (active === "signals") initSignals(true);
  }));

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

/* ---------- overview ---------- */
let sparkChart = null, sparkSeries = null;
async function loadOverview() {
  const d = await (await fetch(apiUrl("/api/overview"))).json();
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
}

let forwardChart, forwardSeries, forwardDdChart, forwardDdSeries;
async function loadForward() {
  $("#view-forward").classList.add("loading");
  const d = await (await fetch("/api/forward")).json();
  $("#view-forward").classList.remove("loading");
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
}

/* ---------- backtest ---------- */
const btState = { cost: 0.003, window: "accept", outcome: "", filter: "", scoreMin: 0, sort: "entry_time", dir: -1 };
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
$("#score-threshold").addEventListener("input", (e) => {
  btState.scoreMin = Number(e.target.value);
  $("#score-threshold-label").textContent = btState.scoreMin.toFixed(3);
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

async function loadBacktest() {
  $("#view-backtest").classList.add("loading");
  const [d, rows] = await Promise.all([
    (await fetch(apiUrl("/api/backtest", { cost: btState.cost }))).json(),
    (await fetch(apiUrl("/api/trades", { window: btState.window, cost: btState.cost }))).json(),
  ]);
  tradeRows = rows;
  const w = d[btState.window];

  $("#bt-tiles").innerHTML = `
    <div class="tile"><span class="lbl">交易笔数</span><b>${w.n_trades}</b><small>${d.universe_label} · ${btState.window === "accept" ? "验收窗口" : "全期"}</small></div>
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
  $("#view-backtest").classList.remove("loading");
}

function renderTrades() {
  let rows = tradeRows;
  if (btState.outcome) rows = rows.filter((r) => r.outcome.startsWith(btState.outcome));
  if (btState.filter) rows = rows.filter((r) => r.symbol.includes(btState.filter));
  const beforeScoreFilter = rows.length;
  if (btState.scoreMin > 0) rows = rows.filter((r) => r.score >= btState.scoreMin);
  rows = rows.slice().sort((a, b) => {
    const va = a[btState.sort], vb = b[btState.sort];
    return (va < vb ? -1 : va > vb ? 1 : 0) * btState.dir;
  });
  $("#trades-count").textContent =
    btState.scoreMin > 0 ? `（${rows.length}/${beforeScoreFilter} 笔）` : `（${rows.length} 笔）`;
  $("#threshold-note").textContent =
    btState.scoreMin > 0 ? "只过滤下方成交明细，不改变净值/PF 或验收结论。" : "只过滤下方成交明细，不改变净值/PF。";
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
let currentThreshold = 0;
let lastFocusRange = null;
let symbolInputWired = false;
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
    autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 1 } }),
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
    crosshairMarkerVisible: false, autoscaleInfoProvider: () => null,
  });
}

async function initSignals(force = false) {
  if (symbolsLoaded && !force) return;
  symbolsLoaded = true;
  const rows = await (await fetch(apiUrl("/api/symbols"))).json();
  $("#symbol-list").innerHTML = rows.map((r) =>
    `<option value="${r.source}:${r.symbol}">${r.symbol}（成交 ${r.n_trades} / 合格 ${r.n_eligible}）</option>`).join("");
  if (!symbolInputWired) {
    $("#symbol-input").addEventListener("change", () => loadChart($("#symbol-input").value));
    symbolInputWired = true;
  }
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
  const resp = await fetch(apiUrl(`/api/chart/${source}/${symbol}`, { bars: sigState.bars }));
  $("#view-signals").classList.remove("loading");
  if (reqId !== chartReq) return;
  if (!resp.ok) { $("#symbol-info").textContent = "找不到该序列"; return; }
  const d = await resp.json();
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
  return `<div class="tile"><div class="tile-label">${label}</div><div class="tile-value">${value}</div>${sub ? `<div class="tile-sub">${sub}</div>` : ""}</div>`;
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

refreshOpsAuthUi();
loadOverview();
