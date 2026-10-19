/* VIDYA 趋势线回踩: live paper book on every OKX USDT perpetual (15m). Paper only: no orders, no pushes. */
(() => {
  "use strict";
  const state = {active: false, pending: false, signature: null, strategy: "tl-2r", source: "live", data: null, trades: null,
    market: null, error: null, tradeSearch: "", tradeOutcome: "all", tradeLimit: 100, marketScope: "near",
    marketSort: "dist", marketSearch: "", symbolLimit: 30,
    view: "live"};
  const REASONS = {delta: "Delta 未达门槛", position_open: "已有仓位", stale: "信号已过期（补算）", stop_beyond_fill: "开盘越过止损"};
  const EXITS = {target: "止盈", stop: "止损", trend: "趋势变色"};
  const STATUS = {starting: "启动中", loading: "首轮加载", running: "运行中", error: "出错，60 秒后重试"};
  const root = () => document.getElementById("vidya-workspace");
  const esc = (v) => String(v ?? "—").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const fin = (v) => typeof v === "number" && Number.isFinite(v);
  const clock = (ms) => ms == null ? "—" : new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false}).format(new Date(ms));
  const closeOf = (openMs) => fin(openMs) ? openMs + 900000 : null;   // 15m bars are stored by open time
  const num = (v, d = 2) => fin(v) ? v.toLocaleString("zh-CN", {maximumFractionDigits: d}) : "—";
  const price = (v) => fin(v) ? v.toLocaleString("zh-CN", {maximumSignificantDigits: 8}) : "—";
  const signed = (v, d = 2) => fin(v) ? `${v > 0 ? "+" : ""}${v.toFixed(d)}` : "—";
  const tone = (v) => fin(v) ? (v > 0 ? "r-positive" : v < 0 ? "r-negative" : "") : "";
  const sideName = (s) => s === "long" ? "多 ↑" : s === "short" ? "空 ↓" : "—";
  const coin = (symbol) => String(symbol || "").replace(/-USDT-SWAP$/, "");
  const tvUrl = (symbol) => `https://www.tradingview.com/chart/?symbol=${encodeURIComponent("OKX:" + String(symbol).replace(/-SWAP$/, "").replace(/-/g, "") + ".P")}&interval=15`;
  const tvLink = (symbol) => `<a href="${esc(tvUrl(symbol))}" target="_blank" rel="noopener noreferrer">${esc(coin(symbol))} ↗</a>`;
  const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v != null)).toString();

  async function getJSON(url) {
    const response = await fetch(url, {cache: "no-store"});
    if (!response.ok) {
      let detail = `读取失败 ${response.status}`;
      try { detail = (await response.json()).detail || detail; } catch (_) { /* keep status text */ }
      throw new Error(detail);
    }
    return response.json();
  }

  async function refresh() {
    if (!state.active || state.pending || document.hidden) return;
    if (state.view === "backtest") return;
    state.pending = true;
    try {
      const [data, trades, market] = await Promise.all([
        getJSON(`/api/vidya-retest/overview?${qs({strategy: state.strategy, source: state.source})}`),
        getJSON(`/api/vidya-retest/trades?${qs({strategy: state.strategy, source: state.source, symbol: state.tradeSearch, outcome: state.tradeOutcome, limit: state.tradeLimit})}`),
        getJSON(`/api/vidya-retest/market?${qs({scope: state.marketScope, sort: state.marketSort, search: state.marketSearch})}`),
      ]);
      const {now_ms: _ignored, ...stable} = data;
      const signature = JSON.stringify([stable, trades, market]);
      const changed = signature !== state.signature || state.error;
      Object.assign(state, {data, trades, market, error: null});
      if (changed && renderable()) { state.signature = signature; render(); }
    } catch (error) {
      state.error = error.message || "读取失败";
      if (renderable()) render();
    } finally {
      state.pending = false;
    }
  }

  // The shell polls every 15s; never replace the DOM under a control the user is typing in.
  function renderable() {
    const el = root(), focused = document.activeElement;
    return !(el && focused && el.contains(focused) && /^(INPUT|SELECT|TEXTAREA)$/.test(focused.tagName));
  }

  function equityChart(points) {
    if (!points?.length) return `<div class="empty-state vr-empty"><h3>还没有平仓记录</h3><p>${state.source === "live" ? "实时模拟从启用时刻开始，只统计之后出现的新信号。" : "预热回算在每个币种首次加载时生成。"}</p></div>`;
    const W = 800, H = 170, pad = 28, xs = [0, ...points.map(p => p.cum_r)];
    const lo = Math.min(0, ...xs), hi = Math.max(0, ...xs), span = hi - lo || 1;
    const x = (i) => pad + (W - 2 * pad) * (i / Math.max(1, points.length));
    const y = (v) => H - pad - (H - 2 * pad) * ((v - lo) / span);
    const path = [`${x(0)},${y(0)}`, ...points.map((p, i) => `${x(i + 1).toFixed(1)},${y(p.cum_r).toFixed(1)}`)].join(" ");
    const last = points[points.length - 1];
    return `<svg class="vr-equity" viewBox="0 0 ${W} ${H}" role="img" aria-label="累计 R 曲线">
      <line x1="${pad}" x2="${W - pad}" y1="${y(0)}" y2="${y(0)}" class="vr-zero"/>
      <polyline points="${path}" class="vr-line ${last.cum_r >= 0 ? "up" : "down"}"/>
      <text x="${pad}" y="${y(hi) - 6}" class="vr-axis">${esc(signed(hi, 1))}R</text>
      <text x="${pad}" y="${Math.min(H - 6, y(lo) + 16)}" class="vr-axis">${esc(signed(lo, 1))}R</text>
      <text x="${W - pad}" y="${y(last.cum_r) - 8}" text-anchor="end" class="vr-axis strong">${esc(signed(last.cum_r, 2))}R · ${esc(points.length)} 笔</text>
    </svg>`;
  }

  function positionsTable(rows) {
    if (!rows?.length) return `<p class="quiet-text">当前没有持仓或待成交的模拟单。</p>`;
    return `<div class="v130-table-wrap"><table class="v130-table vr-table"><thead><tr><th>合约 / 方向</th><th>状态</th><th>信号 · 北京</th><th>入场</th><th>止损 / 止盈</th><th>现价</th><th>浮动 R</th></tr></thead><tbody>${rows.map(p => `<tr>
      <td><strong>${tvLink(p.symbol)}</strong><small>${sideName(p.side)} · Delta ${esc(num(p.delta, 1))}%</small></td>
      <td>${p.status === "open" ? "持仓中" : "等下一根开盘成交"}</td>
      <td>${esc(clock(closeOf(p.signal_ms)))}<small>回踩线 ${esc(price(p.level))}</small></td>
      <td>${p.status === "open" ? `${esc(price(p.entry))}<small>${esc(clock(p.entry_ms))} 开盘</small>` : "—"}</td>
      <td>${esc(price(p.stop))}<small>${p.target == null ? (p.status === "open" ? "趋势变色离场" : "成交后计算") : "止盈 " + esc(price(p.target))}</small></td>
      <td>${esc(price(p.mark))}<small>${esc(clock(closeOf(p.mark_bar_ms)))} 收盘</small></td>
      <td class="${tone(p.unrealized_r)}">${p.unrealized_r == null ? "—" : esc(signed(p.unrealized_r)) + "R"}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function signalsTable(rows) {
    if (!rows?.length) return `<p class="quiet-text">暂时没有回踩确认。只有趋势线被影线碰到、收盘守住且下一步确认后才会出现在这里。</p>`;
    return `<div class="v130-table-wrap vr-scroll"><table class="v130-table vr-table"><thead><tr><th>合约 / 方向</th><th>确认收盘 · 北京</th><th>趋势线</th><th>Delta</th><th>结果</th></tr></thead><tbody>${rows.map(e => {
      if (e.kind === "gap") return `<tr><td><strong>${esc(coin(e.symbol))}</strong></td><td>${esc(clock(closeOf(e.bar_ms)))}</td><td colspan="3">停机超过可取历史，已从最新一根重新开始</td></tr>`;
      const ok = e.kind === "signal";
      return `<tr><td><strong>${tvLink(e.symbol)}</strong><small>${sideName(e.side)}</small></td><td>${esc(clock(closeOf(e.bar_ms)))}<small>${e.touch_ms != null && e.touch_ms !== e.bar_ms ? "碰线 " + esc(clock(closeOf(e.touch_ms))) : "碰线当根确认"}</small></td><td>${esc(price(e.level))}</td><td class="${ok ? "r-positive" : ""}">${esc(num(e.delta, 1))}%</td><td>${ok ? `<span class="ms-badge">入场信号</span><small>止损 ${esc(price(e.stop))}</small>` : `<span class="vr-skip">${esc(REASONS[e.reason] || e.reason)}</span>`}</td></tr>`;
    }).join("")}</tbody></table></div>`;
  }

  function tradesTable(t) {
    const rows = t?.items || [];
    const head = `<div class="ms-controls"><label class="search-field"><input id="vr-trade-search" type="search" placeholder="搜索合约，例如 ETH" aria-label="搜索交易合约" value="${esc(state.tradeSearch)}"></label>
      <div class="segmented" role="group" aria-label="盈亏筛选">${[["all", "全部"], ["win", "盈利"], ["loss", "亏损"]].map(([k, v]) => `<button type="button" data-vr-outcome="${k}" class="${state.tradeOutcome === k ? "selected" : ""}" aria-pressed="${state.tradeOutcome === k}">${v}</button>`).join("")}</div>
      <span class="quiet-text">${esc(t?.total ?? 0)} 笔</span></div>`;
    if (!rows.length) return head + `<p class="quiet-text">没有符合条件的平仓记录。</p>`;
    return head + `<div class="v130-table-wrap vr-scroll"><table class="v130-table vr-table"><thead><tr><th>合约 / 方向</th><th>入场 · 北京</th><th>出场 · 北京</th><th>入场 / 出场</th><th>止损 / 止盈</th><th>Delta</th><th>原因</th><th>结果</th></tr></thead><tbody>${rows.map(r => `<tr>
      <td><strong>${tvLink(r.symbol)}</strong><small>${sideName(r.side)}</small></td>
      <td>${esc(clock(r.entry_ms))}<small>信号 ${esc(clock(closeOf(r.signal_ms)))}</small></td>
      <td>${esc(clock(closeOf(r.exit_ms)))}<small>${esc(r.bars)} 根</small></td>
      <td>${esc(price(r.entry))}<small>${esc(price(r.exit))}</small></td>
      <td>${esc(price(r.stop))}<small>${r.target == null ? "无固定止盈" : esc(price(r.target))}</small></td>
      <td>${esc(num(r.delta, 1))}%<small>止损距离 ${esc(num(r.risk_pct, 2))}%</small></td>
      <td>${esc(EXITS[r.reason] || r.reason)}</td>
      <td class="${tone(r.r)}"><strong>${esc(signed(r.r))}R</strong><small>未扣成本 ${esc(signed(r.r_gross))}R</small></td></tr>`).join("")}</tbody></table></div>
      ${t.total > rows.length ? `<button type="button" class="load-more" id="vr-more-trades">显示更多</button>` : ""}`;
  }

  function symbolsTable(rows) {
    if (!rows?.length) return `<p class="quiet-text">还没有可统计的币种。</p>`;
    const shown = rows.slice(0, state.symbolLimit);
    return `<div class="v130-table-wrap"><table class="v130-table vr-table"><thead><tr><th>合约</th><th>笔数</th><th>胜率</th><th>每笔平均</th><th>合计</th></tr></thead><tbody>${shown.map(r => `<tr><td><strong>${tvLink(r.symbol)}</strong></td><td>${esc(r.trades)}</td><td>${esc(num(r.wins / r.trades * 100, 1))}%</td><td class="${tone(r.avg_r)}">${esc(signed(r.avg_r))}R</td><td class="${tone(r.sum_r)}">${esc(signed(r.sum_r))}R</td></tr>`).join("")}</tbody></table></div>
      ${rows.length > shown.length ? `<button type="button" class="load-more" id="vr-more-symbols">显示全部 ${esc(rows.length)} 个币种</button>` : ""}`;
  }

  function marketTable(m) {
    const rows = m?.items || [];
    const head = `<div class="ms-controls"><div class="segmented" role="group" aria-label="市场范围">${[["near", "接近回踩"], ["strong", "强趋势"], ["all", "全部币种"]].map(([k, v]) => `<button type="button" data-vr-scope="${k}" class="${state.marketScope === k ? "selected" : ""}" aria-pressed="${state.marketScope === k}">${v}</button>`).join("")}</div>
      <label class="ms-select">排序 <select id="vr-market-sort">${[["dist", "离趋势线最近"], ["delta", "Delta 最强"], ["symbol", "合约名"]].map(([k, v]) => `<option value="${k}" ${state.marketSort === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="search-field"><input id="vr-market-search" type="search" placeholder="搜索合约" aria-label="搜索市场合约" value="${esc(state.marketSearch)}"></label>
      <span class="quiet-text">${esc(rows.length)} 个</span></div>`;
    if (!rows.length) return head + `<p class="quiet-text">${state.marketScope === "near" ? "目前没有强趋势且价格离趋势线 1 ATR 以内的币种。" : "没有符合条件的币种。"}</p>`;
    return head + `<div class="v130-table-wrap vr-scroll"><table class="v130-table vr-table"><thead><tr><th>合约</th><th>趋势</th><th>Delta</th><th>收盘价</th><th>趋势线</th><th>离线距离</th><th>最近收盘 · 北京</th></tr></thead><tbody>${rows.map(r => `<tr>
      <td><strong>${tvLink(r.symbol)}</strong>${r.near ? `<span class="ms-badge">接近回踩</span>` : ""}</td>
      <td class="${r.trend === "up" ? "r-positive" : "r-negative"}">${r.trend === "up" ? "多头" : "空头"}</td>
      <td>${esc(num(r.delta, 1))}%</td><td>${esc(price(r.close))}</td><td>${esc(price(r.line))}</td>
      <td>${fin(r.dist_atr) ? esc(num(r.dist_atr, 2)) + " ATR" : "—"}</td><td>${esc(clock(closeOf(r.bar_ms)))}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function render() {
    const el = root();
    if (!el) return;
    const tabs = `<div class="segmented vr-tabs" role="group" aria-label="视图">${[["live", "实时模拟盘"], ["backtest", "历史回测"]].map(([k, v]) => `<button type="button" data-vr-view="${k}" class="${state.view === k ? "selected" : ""}" aria-pressed="${state.view === k}">${v}</button>`).join("")}</div>`;
    if (state.view === "backtest") {
      el.innerHTML = tabs + `<div id="vr-history"></div>`;
      bind();
      window.SpikeVidyaHistory?.mount(el.querySelector("#vr-history"));
      return;
    }
    const d = state.data;
    if (!d) {
      el.innerHTML = tabs + (state.error ? `<div class="notice" role="alert">${esc(state.error)}</div>` : `<p class="research-empty">正在读取 VIDYA 回踩模拟盘…</p>`);
      bind();
      return;
    }
    const s = d.stats || {}, st = d.status || {}, mk = d.market || {};
    const open = (d.positions || []).filter(p => p.status === "open").length;
    const pending = (d.positions || []).length - open;
    const progress = st.total ? `${st.completed ?? 0}/${st.total}` : "—";
    el.innerHTML = tabs + `
      ${state.error ? `<div class="notice" role="alert">刷新失败：${esc(state.error)}；下面是上一次读取的数据。</div>` : ""}
      <div class="ms-overview vr-overview">
        <article><span>监控币种 · OKX 加密币 USDT 永续</span><strong>${esc(mk.universe ?? 0)}</strong><small>多头 ${esc(mk.up ?? 0)} · 空头 ${esc(mk.down ?? 0)} · 接近回踩 ${esc(mk.near ?? 0)}</small></article>
        <article><span>运行状态</span><strong>${esc(STATUS[st.status] || st.status || "—")}</strong><small>本轮 ${esc(progress)} · K 线收盘 ${esc(clock(closeOf(st.cycle_bar_ms)))}${st.errors ? ` · 错误 ${esc(st.errors)}` : ""}</small></article>
        <article><span>持仓 / 待成交</span><strong>${esc(open)} / ${esc(pending)}</strong><small>启用于 ${esc(clock(d.activation?.activated_ms))}</small></article>
        <article><span>已平仓 · 胜率</span><strong>${esc(s.trades ?? 0)} 笔 · ${s.win_rate == null ? "—" : esc(num(s.win_rate, 1)) + "%"}</strong><small>盈亏因子 ${esc(s.pf == null ? "—" : num(s.pf, 2))}</small></article>
        <article><span>累计 / 每笔平均</span><strong class="${tone(s.total_r)}">${esc(signed(s.total_r))}R</strong><small>每笔 ${esc(s.avg_r == null ? "—" : signed(s.avg_r, 3) + "R")}（已扣 20bp）</small></article>
        <article><span>每笔风险 1% 复利</span><strong class="${tone(s.return_pct)}">${esc(signed(s.return_pct))}%</strong><small>最大回撤 ${esc(num(s.max_dd_pct, 2))}%</small></article>
      </div>
      <div class="ms-controls">
        <div class="segmented" role="group" aria-label="策略">${(d.strategies || []).map(x => `<button type="button" data-vr-strategy="${esc(x.id)}" class="${state.strategy === x.id ? "selected" : ""}" aria-pressed="${state.strategy === x.id}">${esc(x.label)}</button>`).join("")}</div>
        <div class="segmented" role="group" aria-label="数据来源"><button type="button" data-vr-source="live" class="${state.source === "live" ? "selected" : ""}" aria-pressed="${state.source === "live"}">实时模拟</button><button type="button" data-vr-source="warmup" class="${state.source === "warmup" ? "selected" : ""}" aria-pressed="${state.source === "warmup"}">预热回算</button></div>
      </div>
      <div class="notice">模拟盘，不下单、不推送。规则：15 分钟图 VIDYA（Length 10）趋势线被影线碰到、收盘守住；碰线 K 线同向直接确认，反向等下一根同向 K 线；入场时 Delta 至少 ±50%；止损在两根 K 线极值外 1 倍 ATR(14)；${state.strategy === "tl-2r" ? "止盈 2 倍风险" : "趋势云变色收盘离场（碰线容差 0.2 ATR）"}。下一根开盘成交，成本按 20bp 往返。${state.source === "warmup" ? "预热回算是首次加载时按同一规则回放约 10 天历史，仅供对照，不计入实时统计。" : ""}
        ETH 15m 2022-01～2026-10 回测（同样次开成交、20bp）：2R 版 53 笔每笔 +0.30R，趋势离场版 66 笔每笔 +0.56R；BTC、SOL 的结果不一致，全币种表现以这里的前向记录为准。</div>
      <section class="ms-section"><div class="section-toolbar"><h2>累计 R</h2><span class="quiet-text">按平仓时间排列，每笔已扣成本</span></div>${equityChart(d.equity)}</section>
      <section class="ms-section"><div class="section-toolbar"><h2>持仓与待成交</h2><span class="quiet-text">现价为最近一根已收盘 K 线</span></div>${positionsTable(d.positions)}</section>
      <section class="ms-section"><div class="section-toolbar"><h2>最近的回踩确认</h2><span class="quiet-text">包含因 Delta 或已有仓位被跳过的确认</span></div>${signalsTable(d.signals)}</section>
      <section class="ms-section"><div class="section-toolbar"><h2>已平仓交易</h2></div>${tradesTable(state.trades)}</section>
      <section class="ms-section"><div class="section-toolbar"><h2>币种表现</h2><span class="quiet-text">按笔数排序</span></div>${symbolsTable(d.by_symbol)}</section>
      <section class="ms-section"><div class="section-toolbar"><h2>全市场状态</h2><span class="quiet-text">接近回踩 = 强趋势（Delta ≥ ±50%）且最新 K 线离趋势线 1 ATR 以内，只作观察</span></div>${marketTable(state.market)}</section>
      <p class="quiet-text">下一轮 ${esc(clock(st.next_cycle_ms))} · 本页每 15 秒检查更新${d.service ? ` · 后台进程 ${d.service.worker_alive ? "运行中" : "未运行"}` : ""}。</p>`;
    bind();
  }

  function bind() {
    const el = root();
    el.querySelectorAll("[data-vr-strategy]").forEach(b => b.addEventListener("click", () => { state.strategy = b.dataset.vrStrategy; state.tradeLimit = 100; state.signature = null; refresh(); }));
    el.querySelectorAll("[data-vr-source]").forEach(b => b.addEventListener("click", () => { state.source = b.dataset.vrSource; state.tradeLimit = 100; refresh(); }));
    el.querySelectorAll("[data-vr-outcome]").forEach(b => b.addEventListener("click", () => { state.tradeOutcome = b.dataset.vrOutcome; refresh(); }));
    el.querySelectorAll("[data-vr-scope]").forEach(b => b.addEventListener("click", () => { state.marketScope = b.dataset.vrScope; refresh(); }));
    el.querySelector("#vr-trade-search")?.addEventListener("change", e => { state.tradeSearch = e.target.value.trim(); refresh(); });
    el.querySelector("#vr-market-search")?.addEventListener("change", e => { state.marketSearch = e.target.value.trim(); refresh(); });
    el.querySelector("#vr-market-sort")?.addEventListener("change", e => { state.marketSort = e.target.value; refresh(); });
    el.querySelector("#vr-more-trades")?.addEventListener("click", () => { state.tradeLimit = Math.min(1000, state.tradeLimit + 200); refresh(); });
    el.querySelector("#vr-more-symbols")?.addEventListener("click", () => { state.symbolLimit = 1000; render(); });
    el.querySelectorAll("[data-vr-view]").forEach(b => b.addEventListener("click", () => {
      state.view = b.dataset.vrView; state.signature = null; render();
      if (state.view !== "backtest") refresh();
    }));
  }

  function setActive(value) {
    state.active = value;
    if (value) {
      state.signature = null;
      render();
      refresh();
    }
  }

  window.SpikeVidya = {refresh, setActive};
})();
