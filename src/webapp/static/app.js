/* fable-trading dashboard frontend (vanilla JS + Lightweight Charts v4) */
const $ = (sel) => document.querySelector(sel);
const fmtPct = (x, digits = 2) => (100 * x).toFixed(digits) + "%";
const CHART_LAYOUT = {
  layout: { background: { type: "solid", color: "#1b1e24" }, textColor: "#9aa0a8" },
  grid: { vertLines: { color: "#242833" }, horzLines: { color: "#242833" } },
  timeScale: { borderColor: "#2e3340", timeVisible: true },
  rightPriceScale: { borderColor: "#2e3340" },
  crosshair: { mode: 0 },
};

/* ---------- tabs ---------- */
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
    $("#view-" + btn.dataset.view).classList.remove("hidden");
    if (btn.dataset.view === "backtest") loadBacktest();
    if (btn.dataset.view === "signals") initSignals();
  });
});

/* ---------- overview ---------- */
async function loadOverview() {
  const d = await (await fetch("/api/overview")).json();
  $("#stages").innerHTML = d.stages.map((s) => `
    <div class="stage">
      <h3>${s.name} <span class="chip ${s.status}">${
        { done: "已完成", passed: "验收通过", failed: "未通过" }[s.status] || s.status
      }</span></h3>
      <p>${s.summary}</p>
    </div>`).join("");
  $("#tiles").innerHTML = d.tiles.map((t) => `
    <div class="tile"><span class="lbl">${t.label}</span><b>${t.value}</b><small>${t.sub}</small></div>`).join("");
  const names = {
    net_positive: "扣费后净收益为正",
    "profit_factor_ge_1.3": "盈亏比 PF ≥ 1.3",
    max_drawdown_le_20pct: "最大回撤 ≤ 20%",
    n_trades_ge_100: "交易数 ≥ 100 笔",
  };
  $("#acceptance").innerHTML = Object.entries(d.acceptance)
    .map(([k, ok]) => `<li class="${ok ? "ok" : "fail"}">${names[k] || k}</li>`).join("");
  $("#next-note").textContent = "下一步：" + d.next;
}

/* ---------- backtest ---------- */
let btState = { cost: 0.003, window: "accept" };
let equityChart = null, equitySeries = null;

function segWire(id, key, parse, cb) {
  $(id).querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    $(id).querySelectorAll("button").forEach((x) => x.classList.toggle("active", x === b));
    btState[key] = parse(b.dataset[key]);
    cb();
  }));
}
segWire("#cost-seg", "cost", Number, loadBacktest);
segWire("#window-seg", "window", String, loadBacktest);

async function loadBacktest() {
  const d = await (await fetch(`/api/backtest?cost=${btState.cost}`)).json();
  const w = d[btState.window];
  const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
  $("#bt-tiles").innerHTML = `
    <div class="tile"><span class="lbl">交易笔数</span><b>${w.n_trades}</b><small>${btState.window === "accept" ? "验收窗口" : "全期"}</small></div>
    <div class="tile"><span class="lbl">净收益（对资金）</span><b class="${cls(w.net_return_on_capital)}">${fmtPct(w.net_return_on_capital)}</b><small>单笔均值 ${fmtPct(w.mean_net_per_trade, 3)}</small></div>
    <div class="tile"><span class="lbl">盈亏比 PF</span><b class="${w.profit_factor >= 1.3 ? "pos" : "neg"}">${w.profit_factor}</b><small>验收线 1.3</small></div>
    <div class="tile"><span class="lbl">最大回撤 / 胜率</span><b>${fmtPct(w.max_drawdown_pct)}</b><small>胜率 ${fmtPct(w.win_rate)}</small></div>`;

  if (!equityChart) {
    equityChart = LightweightCharts.createChart($("#equity-chart"), CHART_LAYOUT);
    equitySeries = equityChart.addAreaSeries({
      lineColor: "#3987e5", lineWidth: 2,
      topColor: "rgba(57,135,229,0.25)", bottomColor: "rgba(57,135,229,0.02)",
      priceFormat: { type: "custom", formatter: (v) => v.toFixed(2) + "%" },
    });
    new ResizeObserver(() => equityChart.applyOptions({ width: $("#equity-chart").clientWidth }))
      .observe($("#equity-chart"));
  }
  equitySeries.setData(w.equity);
  equityChart.timeScale().fitContent();

  const rows = await (await fetch(`/api/trades?window=${btState.window}&cost=${btState.cost}`)).json();
  $("#trades-count").textContent = `（最近 ${rows.length} 笔）`;
  $("#trades-table tbody").innerHTML = rows.map((r) => `
    <tr>
      <td>${r.entry_time.slice(0, 16)}</td>
      <td>${r.symbol}</td>
      <td class="num">${r.score.toFixed(3)}</td>
      <td class="outcome-${r.outcome}">${{ tp: "止盈", sl: "止损", timeout: "超时", sl_ambiguous: "止损*" }[r.outcome] || r.outcome}</td>
      <td class="num"><span class="${cls(r.gross_ret)}">${fmtPct(r.gross_ret, 2)}</span></td>
      <td class="num"><span class="${cls(r.net_ret)}">${fmtPct(r.net_ret, 2)}</span></td>
    </tr>`).join("");
}

/* ---------- signals browser ---------- */
let symbolsLoaded = false, klineChart = null, klineSeries = null, emaSeries = [];
async function initSignals() {
  if (symbolsLoaded) return;
  symbolsLoaded = true;
  const rows = await (await fetch("/api/symbols")).json();
  $("#symbol-list").innerHTML = rows.map((r) =>
    `<option value="${r.source}:${r.symbol}">${r.symbol}（成交 ${r.n_trades} / 合格 ${r.n_eligible}）</option>`).join("");
  $("#symbol-input").addEventListener("change", () => loadChart($("#symbol-input").value));
  const first = rows.find((r) => r.n_trades > 0) || rows[0];
  if (first) {
    $("#symbol-input").value = `${first.source}:${first.symbol}`;
    loadChart($("#symbol-input").value);
  }
}

const EMA_COLORS = {
  8: "rgba(57,135,229,0.9)", 13: "rgba(57,135,229,0.7)", 21: "rgba(57,135,229,0.55)",
  34: "rgba(57,135,229,0.4)", 55: "rgba(57,135,229,0.3)",
  144: "#9085e9", 200: "#d55181",
};

async function loadChart(key) {
  const [source, symbol] = key.split(":");
  if (!symbol) return;
  const resp = await fetch(`/api/chart/${source}/${symbol}`);
  if (!resp.ok) { $("#symbol-info").textContent = "找不到该序列"; return; }
  const d = await resp.json();

  if (!klineChart) {
    klineChart = LightweightCharts.createChart($("#kline-chart"), CHART_LAYOUT);
    klineSeries = klineChart.addCandlestickSeries({
      upColor: "#1fa77d", downColor: "#e66767", borderVisible: false,
      wickUpColor: "#1fa77d", wickDownColor: "#e66767",
    });
    new ResizeObserver(() => klineChart.applyOptions({ width: $("#kline-chart").clientWidth }))
      .observe($("#kline-chart"));
  }
  emaSeries.forEach((s) => klineChart.removeSeries(s));
  emaSeries = [];
  klineSeries.setData(d.candles);
  for (const [span, data] of Object.entries(d.emas)) {
    const s = klineChart.addLineSeries({
      color: EMA_COLORS[span] || "#666", lineWidth: span >= 144 ? 2 : 1,
      priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    s.setData(data);
    emaSeries.push(s);
  }
  const outcomeColor = { tp: "#199e70", sl: "#e66767", timeout: "#c98500", sl_ambiguous: "#e66767" };
  const markers = d.markers
    .filter((m) => m.eligible || m.traded)
    .map((m) => ({
      time: m.time,
      position: "belowBar",
      shape: m.traded ? "arrowUp" : "circle",
      color: m.traded ? (outcomeColor[m.outcome] || "#8b93a1") : "#8b93a1",
      text: m.traded ? `${(100 * m.ret).toFixed(1)}%` : "",
      size: m.traded ? 2 : 1,
    }));
  klineSeries.setMarkers(markers);
  klineChart.timeScale().fitContent();
  const n = d.markers.length, el = d.markers.filter((m) => m.eligible).length,
    tr = d.markers.filter((m) => m.traded).length;
  $("#symbol-info").textContent =
    `${symbol}：窗口内候选 ${n} 个，合格（分数≥${d.threshold}）${el} 个，实际成交 ${tr} 笔`;
}

loadOverview();
