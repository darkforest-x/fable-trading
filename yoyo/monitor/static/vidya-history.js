/* VIDYA 回踩 · 历史回测: the report's nine configurations with trades drawn on candles.
   Data: /api/vidya-retest/history (report basis) and /api/vidya-retest/history/candles. Read only. */
(() => {
  "use strict";
  const DAY = 864e5;
  const SPANS = [["1天", 1], ["3天", 3], ["1周", 7], ["2周", 14], ["1月", 30], ["3月", 91], ["1年", 365]];
  const S = {data: null, error: null, loading: false, cfg: "H", win: null, focus: null, candles: null, candleKey: null,
    candleError: null, candleSeq: 0, side: "all", outcome: "all", year: "all", reason: "all", limit: 150};
  let box = null;
  const esc = (v) => String(v ?? "—").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const fin = (v) => typeof v === "number" && Number.isFinite(v);
  const num = (v, d = 2) => fin(v) ? v.toLocaleString("zh-CN", {maximumFractionDigits: d}) : "—";
  const price = (v) => fin(v) ? v.toLocaleString("zh-CN", {maximumFractionDigits: 2}) : "—";
  const signed = (v, d = 2) => fin(v) ? `${v > 0 ? "+" : ""}${v.toFixed(d)}` : "—";
  const tone = (v) => fin(v) ? (v > 0 ? "r-positive" : v < 0 ? "r-negative" : "") : "";
  const fmt = (ms, opts) => new Intl.DateTimeFormat("zh-CN", {timeZone: "Asia/Shanghai", hour12: false, ...opts}).format(new Date(ms));
  const full = (ms) => ms == null ? "—" : fmt(ms, {year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"});
  const bjYear = (ms) => Number(fmt(ms, {year: "numeric"}).replace(/\D/g, ""));
  const cfgOf = (key) => S.data?.configs.find(c => c.config === key);
  const barMs = (c) => (c?.tf === "1h" ? 3600e3 : 900e3);
  const tradesOf = (key) => (S.data?.trades || []).filter(t => t.config === key);
  // report basis: entry at the confirming close; stop/target fill inside the exit bar, trend exits at its close
  const entryX = (t, bar) => t.entry_ms + bar;
  const exitX = (t, bar) => t.exit_ms + (t.reason === "趋势变色" ? bar : bar / 2);

  async function getJSON(url) {
    const r = await fetch(url, {cache: "no-store"});
    if (!r.ok) {
      let detail = `读取失败 ${r.status}`;
      try { detail = (await r.json()).detail || detail; } catch (_) { /* keep */ }
      throw new Error(detail);
    }
    return r.json();
  }

  async function load() {
    if (S.data || S.loading) return;
    S.loading = true;
    try { S.data = await getJSON("/api/vidya-retest/history"); S.error = null; defaultWindow(); }
    catch (e) { S.error = e.message || "读取失败"; }
    finally { S.loading = false; render(); }
  }

  function bounds() {
    const c = cfgOf(S.cfg), first = Date.parse(S.data.meta.first), last = Date.parse(S.data.meta.last) + barMs(c);
    return [first, last];
  }

  function setWindow(start, span) {
    const [lo, hi] = bounds();
    span = Math.min(span, hi - lo);
    start = Math.max(lo, Math.min(start, hi - span));
    S.win = {start, end: start + span};
  }

  function defaultWindow() {
    const c = cfgOf(S.cfg), ts = tradesOf(S.cfg), bar = barMs(c);
    const span = (c?.tf === "1h" ? 14 : 3) * DAY;
    const last = ts.length ? ts[ts.length - 1] : null;
    S.focus = last ? last.tid : null;
    const [, hi] = bounds();
    setWindow(last ? entryX(last, bar) - span * 0.55 : hi - span, span);
  }

  function focusTrade(tid) {
    const t = (S.data?.trades || []).find(x => x.tid === tid);
    if (!t) return;
    const bar = barMs(cfgOf(t.config)), a = entryX(t, bar), b = exitX(t, bar);
    const span = Math.max((b - a) * 3, 120 * bar);   // zoom to the trade with room on both sides
    S.focus = tid;
    setWindow((a + b) / 2 - span / 2, span);
    drawChart(true);
    box.querySelectorAll("[data-vh-tid]").forEach(r => r.classList.toggle("vr-sel", r.dataset.vhTid === tid));
    box.querySelector("#vh-chart-card")?.scrollIntoView({block: "start", behavior: "smooth"});
  }

  async function fetchCandles() {
    const c = cfgOf(S.cfg), el = box?.querySelector("#vh-chart");
    if (!c || !el || !S.win) return;
    const maxBars = Math.max(80, Math.min(900, Math.floor((el.clientWidth || 800) / 3)));
    const key = [c.tf, c.length, Math.round(S.win.start), Math.round(S.win.end), maxBars].join("|");
    if (key === S.candleKey && S.candles) return;
    const seq = ++S.candleSeq;
    try {
      const q = new URLSearchParams({tf: c.tf, length: c.length, start_ms: Math.floor(S.win.start), end_ms: Math.ceil(S.win.end), max_bars: maxBars});
      const data = await getJSON(`/api/vidya-retest/history/candles?${q}`);
      if (seq !== S.candleSeq) return;
      Object.assign(S, {candles: data, candleKey: key, candleError: null});
    } catch (e) {
      if (seq !== S.candleSeq) return;
      S.candleError = e.message || "读取 K 线失败";
    }
    drawChart(false);
  }

  function drawChart(refetch) {
    const el = box?.querySelector("#vh-chart");
    if (!el) return;
    const label = box.querySelector("#vh-range");
    if (label && S.win) label.textContent = `${full(S.win.start)} 至 ${full(S.win.end)}`;
    if (S.win) box.querySelectorAll("[data-vh-span]").forEach(b => {
      const on = Math.abs(Number(b.dataset.vhSpan) * DAY - (S.win.end - S.win.start)) < DAY / 2;
      b.classList.toggle("selected", on); b.setAttribute("aria-pressed", on);
    });
    if (refetch) { fetchCandles(); }
    const k = S.candles, c = cfgOf(S.cfg);
    if (S.candleError) { el.innerHTML = `<div class="notice" role="alert">${esc(S.candleError)}</div>`; return; }
    if (!k || !c || !S.win || k.tf !== c.tf || k.length !== c.length) { el.innerHTML = `<p class="research-empty">正在读取 K 线…</p>`; if (!refetch) fetchCandles(); return; }
    const rows = k.rows.filter(r => r[0] + k.bar_ms * k.bucket > S.win.start && r[0] < S.win.end);
    if (!rows.length) { el.innerHTML = `<p class="quiet-text">这段时间没有 K 线。</p>`; return; }
    const W = Math.max(320, el.clientWidth || 800), H = W < 640 ? 330 : 430, L = 6, R = 64, T = 10, B = 26, pw = W - L - R;
    const bar = k.bar_ms, step = bar * k.bucket;
    const x = (ms) => L + (ms - S.win.start) / (S.win.end - S.win.start) * pw;
    const visible = tradesOf(S.cfg).filter(t => exitX(t, bar) >= S.win.start && entryX(t, bar) <= S.win.end);
    let lo = Infinity, hi = -Infinity;
    rows.forEach(r => { lo = Math.min(lo, r[3]); hi = Math.max(hi, r[2]); if (fin(r[5])) { lo = Math.min(lo, r[5]); hi = Math.max(hi, r[5]); } });
    visible.forEach(t => [t.entry, t.exit, t.stop, t.target].forEach(v => { if (fin(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }));
    const pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;
    const y = (v) => T + (hi - v) / (hi - lo) * (H - T - B);
    const cw = Math.max(1, pw / rows.length * 0.68);
    const parts = [];
    const ticks = 5;
    for (let i = 0; i <= ticks; i++) {
      const v = lo + (hi - lo) * i / ticks, yy = y(v);
      parts.push(`<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" class="vh-grid"/><text x="${W - R + 6}" y="${yy + 4}" class="vh-axis">${esc(price(v))}</text>`);
    }
    const spanDays = (S.win.end - S.win.start) / DAY;
    const tf = spanDays <= 3 ? {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"} : spanDays <= 120 ? {month: "2-digit", day: "2-digit"} : {year: "numeric", month: "2-digit"};
    const xt = Math.max(3, Math.floor(pw / 130));
    for (let i = 0; i <= xt; i++) {
      const ms = S.win.start + (S.win.end - S.win.start) * i / xt, xx = x(ms);
      parts.push(`<text x="${Math.min(W - R - 4, Math.max(L + 2, xx))}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === xt ? "end" : "middle"}" class="vh-axis">${esc(fmt(ms, tf))}</text>`);
    }
    rows.forEach(r => {
      const cx = x(r[0] + step / 2), col = r[4] >= r[1] ? "up" : "down";
      parts.push(`<line x1="${cx}" x2="${cx}" y1="${y(r[2])}" y2="${y(r[3])}" class="vh-wick ${col}"/><rect x="${cx - cw / 2}" y="${y(Math.max(r[1], r[4]))}" width="${cw}" height="${Math.max(1, Math.abs(y(r[1]) - y(r[4])))}" class="vh-body ${col}"/>`);
    });
    let seg = [], segUp = null;
    const flush = () => { if (seg.length > 1) parts.push(`<polyline points="${seg.join(" ")}" class="vh-trend ${segUp ? "up" : "down"}"/>`); seg = []; };
    rows.forEach(r => {
      if (!fin(r[5])) { flush(); segUp = null; return; }
      if (segUp !== null && r[6] !== segUp) flush();
      segUp = r[6];
      seg.push(`${x(r[0] + step / 2).toFixed(1)},${y(r[5]).toFixed(1)}`);
    });
    flush();
    visible.forEach(t => {
      const xa = x(entryX(t, bar)), xb = x(exitX(t, bar)), win = t.R > 0, big = t.tid === S.focus, s = big ? 1.35 : 1;
      const ye = y(t.entry), dir = t.side === "做多" ? 1 : -1, z = 7 * s;
      parts.push(`<g data-tid="${esc(t.tid)}" class="vh-trade${big ? " focus" : ""}">
        <line x1="${xa}" x2="${xb}" y1="${y(t.stop)}" y2="${y(t.stop)}" class="vh-stop"/>
        ${fin(t.target) ? `<line x1="${xa}" x2="${xb}" y1="${y(t.target)}" y2="${y(t.target)}" class="vh-target"/>` : ""}
        <line x1="${xa}" x2="${xb}" y1="${ye}" y2="${y(t.exit)}" class="vh-conn ${win ? "win" : "loss"}"/>
        <path d="M${xa},${ye}L${xa - z * 0.75},${ye + dir * z * 1.4}L${xa + z * 0.75},${ye + dir * z * 1.4}Z" class="vh-entry"/>
        <circle cx="${xb}" cy="${y(t.exit)}" r="${4.5 * s}" class="vh-exit ${win ? "win" : "loss"}"/></g>`);
    });
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" class="vh-svg" role="img" aria-label="K 线与历史交易">${parts.join("")}<line class="vh-cross" y1="${T}" y2="${H - B}" x1="0" x2="0" style="display:none"/></svg><div class="vr-tip vh-tip" hidden></div>
      <p class="quiet-text">${k.bucket > 1 ? `每根 K 线合并了 ${esc(k.bucket)} 根 ${c.tf === "1h" ? "1 小时" : "15 分钟"} K 线；缩短时间范围可看原始 K 线。` : `${c.tf === "1h" ? "1 小时" : "15 分钟"} K 线。`} 粗线是 Length ${esc(c.length)} 的 VIDYA 趋势线，这段时间内有 ${esc(visible.length)} 笔交易。</p>`;
    const svg = el.querySelector("svg"), tip = el.querySelector(".vh-tip"), cross = el.querySelector(".vh-cross");
    svg.addEventListener("mousemove", (ev) => {
      const pt = svg.getBoundingClientRect(), scale = W / pt.width, px = (ev.clientX - pt.left) * scale;
      const g = ev.target.closest?.("[data-tid]");
      let lines;
      if (g) {
        const t = visible.find(v => v.tid === g.dataset.tid);
        lines = [`${t.tid} · ${t.side} · ${signed(t.R)}R`, `入场 ${full(entryX(t, bar))} @ ${price(t.entry)}`,
          `止损 ${price(t.stop)}${fin(t.target) ? " · 止盈 " + price(t.target) : " · 趋势变色离场"}`,
          `${S.cfg === "H" || S.cfg === "I" ? "回踩趋势线" : "回踩枢轴线"} ${price(t.level)} · Delta ${num(t.delta, 1)}%`,
          `出场 ${full(exitX(t, bar))} @ ${price(t.exit)} · ${t.reason}`];
        cross.style.display = "none";
      } else {
        const ms = S.win.start + (px - L) / pw * (S.win.end - S.win.start);
        let best = null, bd = Infinity;
        rows.forEach(r => { const d = Math.abs(r[0] + step / 2 - ms); if (d < bd) { bd = d; best = r; } });
        if (!best) return;
        const cx = x(best[0] + step / 2);
        cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.style.display = "";
        lines = [`${full(best[0])}${k.bucket > 1 ? ` 起 ${k.bucket} 根` : ""}`, `开 ${price(best[1])} · 高 ${price(best[2])}`, `低 ${price(best[3])} · 收 ${price(best[4])}`,
          `趋势 ${best[6] ? "多" : "空"} · Delta ${num(best[7], 1)}%`];
      }
      tip.innerHTML = lines.map((l, i) => `<div${i === 0 ? ' class="vh-tip-head"' : ""}>${esc(l)}</div>`).join("");
      tip.hidden = false;
      const bx = ev.clientX - el.getBoundingClientRect().left, by = ev.clientY - el.getBoundingClientRect().top;
      tip.style.left = `${Math.max(0, Math.min(bx + 14, el.clientWidth - tip.offsetWidth - 4))}px`;
      tip.style.top = `${Math.max(0, by - tip.offsetHeight - 10)}px`;
    });
    svg.addEventListener("mouseleave", () => { tip.hidden = true; cross.style.display = "none"; });
    svg.addEventListener("click", (ev) => { const g = ev.target.closest?.("[data-tid]"); if (g) focusTrade(g.dataset.tid); });
  }

  function equity(trades) {
    if (!trades.length) return `<p class="quiet-text">没有交易。</p>`;
    const W = 800, H = 170, P = 28;
    let cum = 0;
    const pts = [0, ...trades.map(t => (cum += t.R))];
    const lo = Math.min(...pts), hi = Math.max(...pts), span = hi - lo || 1;
    const x = (i) => P + (W - 2 * P) * i / (pts.length - 1), y = (v) => H - P - (H - 2 * P) * (v - lo) / span;
    return `<svg class="vr-equity" viewBox="0 0 ${W} ${H}" role="img" aria-label="累计 R"><line x1="${P}" x2="${W - P}" y1="${y(0)}" y2="${y(0)}" class="vr-zero"/>
      <polyline points="${pts.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")}" class="vr-line ${cum >= 0 ? "up" : "down"}"/>
      <text x="${P}" y="${y(hi) - 6}" class="vr-axis">${esc(signed(hi, 1))}R</text><text x="${P}" y="${Math.min(H - 6, y(lo) + 16)}" class="vr-axis">${esc(signed(lo, 1))}R</text>
      <text x="${W - P}" y="${y(cum) - 8}" text-anchor="end" class="vr-axis strong">${esc(signed(cum, 2))}R · ${esc(trades.length)} 笔</text></svg>`;
  }

  const seg = (name, value, opts) => `<div class="segmented" role="group">${opts.map(([k, v]) => `<button type="button" data-vh-${name}="${esc(k)}" class="${String(value) === String(k) ? "selected" : ""}" aria-pressed="${String(value) === String(k)}">${esc(v)}</button>`).join("")}</div>`;

  function table(head, rows) {
    return `<div class="v130-table-wrap"><table class="v130-table vr-table"><thead><tr>${head.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
  }

  function render() {
    if (!box) return;
    if (!S.data) { box.innerHTML = S.error ? `<div class="notice" role="alert">${esc(S.error)}</div>` : `<p class="research-empty">正在读取历史回测…</p>`; return; }
    const d = S.data, m = d.meta, c = cfgOf(S.cfg), all = tradesOf(S.cfg);
    const bd = (dim) => c.breakdown.filter(b => b.dim === dim).sort((a, b) => a.ord - b.ord)
      .map(b => `<tr><td>${esc(b.bucket)}</td><td>${esc(b.n)}</td><td>${esc(num(b.win_rate, 1))}%</td><td class="${tone(b.avg_R)}">${esc(signed(b.avg_R))}R</td><td class="${tone(b.sum_R)}">${esc(signed(b.sum_R, 1))}R</td></tr>`);
    const years = [...new Set(all.map(t => bjYear(t.entry_ms)))];
    const shown = all.filter(t => (S.side === "all" || t.side === S.side) && (S.outcome === "all" || (S.outcome === "win" ? t.R > 0 : t.R <= 0))
      && (S.year === "all" || String(bjYear(t.entry_ms)) === S.year) && (S.reason === "all" || t.reason === S.reason)).slice().reverse();
    const span = S.win ? S.win.end - S.win.start : 3 * DAY;
    const tl = d.trendline;
    box.innerHTML = `
      <div class="notice">口径与回测报告完全一致：币安 ETHUSDT 永续 ${esc(String(m.first).slice(0, 10))} 至 ${esc(String(m.last).slice(0, 10))}（15 分钟 ${esc(num(m.bars_15m, 0))} 根，1 小时由它合成）；在确认 K 线收盘价入场，手续费每边 0.05%，市价成交加 0.01% 滑点，每笔按账户 1% 风险复利。fable-trading 标准口径（下一根开盘成交、20bp）的结果在页面下方“趋势线回踩 · 三个币对照”。C 到 I 的改进思路是看了数据后选出的，带有挑选偏差。</div>
      <div class="ms-controls">${seg("cfg", S.cfg, d.configs.map(x => [x.config, x.short]))}</div>
      <p class="vr-desc"><strong>${esc(c.name)}</strong>：${esc(c.desc)}</p>
      <div class="ms-overview vr-overview">
        <article><span>交易笔数</span><strong>${esc(num(c.trades, 0))}</strong><small>做多 ${esc(c.long_n)} · 做空 ${esc(c.short_n)}</small></article>
        <article><span>胜率</span><strong>${esc(num(c.win_rate, 1))}%</strong><small>${c.breakeven_wr != null ? `打平需要 ${esc(num(c.breakeven_wr, 1))}%（未算成本）` : `平均赚 ${esc(signed(c.avg_win_R))}R · 平均亏 ${esc(signed(c.avg_loss_R))}R`}</small></article>
        <article><span>每笔平均（扣成本）</span><strong class="${tone(c.avg_R)}">${esc(signed(c.avg_R))}R</strong><small>不算成本 ${esc(signed(c.avg_R_gross))}R · 成本 ${esc(num(c.cost_R))}R</small></article>
        <article><span>总收益（每笔风险 1%）</span><strong class="${tone(c.ret_pct)}">${esc(signed(c.ret_pct, 1))}%</strong><small>起始 100 → ${esc(num(c.final_equity, 1))}</small></article>
        <article><span>最大回撤</span><strong>${esc(num(c.max_dd_pct, 1))}%</strong><small>最长连亏 ${esc(c.max_losing_streak)} 笔</small></article>
        <article><span>盈亏因子</span><strong>${esc(c.pf == null ? "—" : num(c.pf, 2))}</strong><small>平均持仓 ${esc(num(c.avg_hold_h, 1))} 小时 · 平均止损距离 ${esc(num(c.avg_risk_pct, 2))}%</small></article>
      </div>
      <section class="ms-section" id="vh-chart-card"><div class="section-toolbar"><h2>K 线上的进出场点</h2><span class="quiet-text" id="vh-range"></span></div>
        <div class="ms-controls">${seg("span", Math.round(span / DAY), SPANS.map(([l, n]) => [n, l]))}
          <div class="segmented" role="group"><button type="button" data-vh-move="-1">‹ 往前</button><button type="button" data-vh-move="1">往后 ›</button><button type="button" data-vh-move="last">最近一笔</button></div></div>
        <div class="vr-legend"><span><i class="vh-key entry"></i>入场（尖角对准入场价）</span><span><i class="vh-key win"></i>出场赚钱</span><span><i class="vh-key loss"></i>出场亏钱</span><span><i class="vh-key stop"></i>止损</span><span><i class="vh-key target"></i>止盈</span><span>鼠标停在 K 线或标记上看详情，点标记或下方交易可跳转</span></div>
        <div id="vh-chart" class="vh-chart"></div></section>
      <section class="ms-section"><div class="section-toolbar"><h2>累计 R</h2><span class="quiet-text">按平仓先后排列，每笔已扣成本</span></div>${equity(all)}</section>
      <section class="ms-section"><div class="section-toolbar"><h2>每年赚亏</h2><span class="quiet-text">按北京时间的入场年份</span></div>
        ${table(["年份", "笔数", "胜率", "每笔平均", "合计", "当年收益（1% 风险）"], [...c.yearly.map(yr => `<tr><td>${esc(yr.year)}</td><td>${esc(yr.n)}</td><td>${esc(num(yr.win_rate, 1))}%</td><td class="${tone(yr.avg_R)}">${esc(signed(yr.avg_R))}R</td><td class="${tone(yr.sum_R)}">${esc(signed(yr.sum_R, 1))}R</td><td class="${tone(yr.ret_pct)}">${esc(signed(yr.ret_pct, 1))}%</td></tr>`),
          `<tr><td>2022-01 至 2024-09<small>检验期</small></td><td>${esc(c.holdout_n)}</td><td></td><td class="${tone(c.holdout_avgR)}">${esc(signed(c.holdout_avgR))}R</td><td></td><td></td></tr>`,
          `<tr><td>2024-10 以后<small>挑选期</small></td><td>${esc(c.recent_n)}</td><td></td><td class="${tone(c.recent_avgR)}">${esc(signed(c.recent_avgR))}R</td><td></td><td></td></tr>`])}</section>
      <div class="vh-grid3">
        <section class="ms-section"><div class="section-toolbar"><h2>做多和做空</h2></div>${table(["方向", "笔数", "胜率", "每笔平均", "合计"], bd("方向"))}</section>
        <section class="ms-section"><div class="section-toolbar"><h2>怎么离场的</h2></div>${table(["出场原因", "笔数", "胜率", "每笔平均", "合计"], bd("出场原因"))}</section>
        <section class="ms-section"><div class="section-toolbar"><h2>入场时的 Delta</h2></div>${table(["Delta 区间", "笔数", "胜率", "每笔平均", "合计"], bd("入场 Delta"))}</section>
      </div>
      <section class="ms-section"><div class="section-toolbar"><h2>九个方案对比</h2><span class="quiet-text">点一行切换方案</span></div>
        <div class="v130-table-wrap"><table class="v130-table vr-table vr-combos"><thead><tr><th>方案</th><th>笔数</th><th>胜率</th><th>每笔平均</th><th>不算成本</th><th>总收益</th><th>最大回撤</th><th>检验期每笔</th><th>挑选期每笔</th></tr></thead><tbody>
          ${d.configs.map(x => `<tr data-vh-pick="${esc(x.config)}" tabindex="0" class="${x.config === S.cfg ? "vr-sel" : ""}"><td><strong>${esc(x.short)}</strong><small>${esc(x.timeframe)} · Length ${esc(x.length)}</small></td><td>${esc(num(x.trades, 0))}</td><td>${esc(num(x.win_rate, 1))}%</td><td class="${tone(x.avg_R)}">${esc(signed(x.avg_R))}R</td><td class="${tone(x.avg_R_gross)}">${esc(signed(x.avg_R_gross))}R</td><td class="${tone(x.ret_pct)}">${esc(signed(x.ret_pct, 1))}%</td><td>${esc(num(x.max_dd_pct, 1))}%</td><td class="${tone(x.holdout_avgR)}">${esc(signed(x.holdout_avgR))}R</td><td class="${tone(x.recent_avgR)}">${esc(signed(x.recent_avgR))}R</td></tr>`).join("")}
        </tbody></table></div></section>
      ${tl ? `<section class="ms-section"><div class="section-toolbar"><h2>趋势线回踩 · 三个币对照</h2><span class="quiet-text">fable-trading 标准口径：下一根开盘成交、20bp 往返</span></div>
        <p class="quiet-text">随机入场 = 同一个币、同一个月、同方向趋势里随机入场，止损距离和离场规则与真实交易相同；p 越小越不像碰巧，本仓的标准是 p&lt;0.01。</p>
        ${table(["币种 / 版本", "笔数", "胜率", "每笔平均", "总收益", "最大回撤", "检验期每笔", "挑选期每笔", "随机入场", "超出随机", "p"], Object.entries(tl.main).map(([key, b]) => { const [coin, sid] = key.split("|"), x = b.summary || {}, r = b.matched_random || {};
          return `<tr><td><strong>${esc(coin)}</strong><small>${esc(tl.strategies?.[sid]?.label || sid)}</small></td><td>${esc(x.trades)}</td><td>${esc(num(x.win_rate, 1))}%</td><td class="${tone(x.avg_r)}">${esc(signed(x.avg_r, 3))}R</td><td class="${tone(x.return_pct)}">${esc(signed(x.return_pct, 1))}%</td><td>${esc(num(x.max_dd_pct, 1))}%</td><td class="${tone(b.before_2024_10?.avg_r)}">${esc(signed(b.before_2024_10?.avg_r, 3))}R</td><td class="${tone(b.from_2024_10?.avg_r)}">${esc(signed(b.from_2024_10?.avg_r, 3))}R</td><td>${esc(signed(r.random_mean_r))}R</td><td class="${tone(r.excess_r)}">${esc(signed(r.excess_r))}R</td><td>${esc(num(r.p_value, 4))}</td></tr>`; }))}
        <div class="section-toolbar"><h2>趋势线回踩 · Length 敏感性（ETH，标准口径）</h2></div>
        ${table(["Length", "2R 版每笔", "2R 版检验期 / 挑选期", "趋势离场版每笔", "趋势离场版检验期 / 挑选期"], [8, 9, 10, 11, 12, 13, 14].map(n => { const a = tl.length_sensitivity?.[`tl-2r|L${n}`] || {}, b = tl.length_sensitivity?.[`tl-trend|L${n}`] || {};
          return `<tr class="${n === 10 ? "vr-sel" : ""}"><td><strong>${n}</strong>${n === 10 ? "<small>当前使用</small>" : ""}</td><td class="${tone(a.summary?.avg_r)}">${esc(signed(a.summary?.avg_r, 3))}R<small>${esc(a.summary?.trades ?? "—")} 笔</small></td><td>${esc(signed(a.before_2024_10?.avg_r, 3))} / ${esc(signed(a.from_2024_10?.avg_r, 3))}</td><td class="${tone(b.summary?.avg_r)}">${esc(signed(b.summary?.avg_r, 3))}R<small>${esc(b.summary?.trades ?? "—")} 笔</small></td><td>${esc(signed(b.before_2024_10?.avg_r, 3))} / ${esc(signed(b.from_2024_10?.avg_r, 3))}</td></tr>`; }))}</section>` : ""}
      <section class="ms-section"><div class="section-toolbar"><h2>交易明细</h2><span class="quiet-text">${esc(shown.length)} 笔 · 北京时间 · 点一行在 K 线上查看</span></div>
        <div class="ms-controls">${seg("side", S.side, [["all", "全部方向"], ["做多", "做多"], ["做空", "做空"]])}${seg("outcome", S.outcome, [["all", "全部"], ["win", "盈利"], ["loss", "亏损"]])}
          <label class="ms-select">年份 <select id="vh-year"><option value="all">全部</option>${years.map(yy => `<option value="${yy}" ${S.year === String(yy) ? "selected" : ""}>${yy}</option>`).join("")}</select></label>
          <label class="ms-select">出场原因 <select id="vh-reason"><option value="all">全部</option>${[...new Set(all.map(t => t.reason))].map(r => `<option value="${esc(r)}" ${S.reason === r ? "selected" : ""}>${esc(r)}</option>`).join("")}</select></label></div>
        <div class="v130-table-wrap vr-scroll"><table class="v130-table vr-table vr-combos"><thead><tr><th>编号 / 方向</th><th>入场</th><th>出场</th><th>入场 / 出场价</th><th>止损 / 止盈</th><th>回踩价位 / Delta</th><th>原因</th><th>结果</th><th>资金</th></tr></thead><tbody>
          ${shown.slice(0, S.limit).map(t => `<tr data-vh-tid="${esc(t.tid)}" tabindex="0" class="${t.tid === S.focus ? "vr-sel" : ""}">
            <td><strong>${esc(t.tid)}</strong><small>${esc(t.side)}</small></td>
            <td>${esc(full(entryX(t, barMs(c))))}<small>确认 K 线收盘</small></td>
            <td>${esc(full(exitX(t, barMs(c))))}<small>持仓 ${esc(num(t.hold_h, 2))} 小时</small></td>
            <td>${esc(price(t.entry))}<small>${esc(price(t.exit))}</small></td>
            <td>${esc(price(t.stop))}<small>${t.target == null ? "无固定止盈" : esc(price(t.target))}</small></td>
            <td>${esc(price(t.level))}<small>Delta ${esc(num(t.delta, 1))}% · 止损距离 ${esc(num(t.stop_pct, 2))}%</small></td>
            <td>${esc(t.reason)}</td>
            <td class="${tone(t.R)}"><strong>${esc(signed(t.R))}R</strong><small>未扣成本 ${esc(signed(t.R_gross))}R</small></td>
            <td>${esc(num(t.equity, 1))}</td></tr>`).join("")}
        </tbody></table></div>
        ${shown.length > S.limit ? `<button type="button" class="load-more" id="vh-more">显示更多（还有 ${esc(shown.length - S.limit)} 笔）</button>` : ""}</section>`;
    bind();
    drawChart(true);
  }

  function bind() {
    const on = (sel, fn) => box.querySelectorAll(sel).forEach(el => el.addEventListener("click", () => fn(el)));
    on("[data-vh-cfg]", el => { S.cfg = el.dataset.vhCfg; S.limit = 150; S.year = "all"; S.reason = "all"; S.candles = null; S.candleKey = null; defaultWindow(); render(); });
    on("[data-vh-pick]", el => { S.cfg = el.dataset.vhPick; S.limit = 150; S.year = "all"; S.reason = "all"; S.candles = null; S.candleKey = null; defaultWindow(); render(); window.scrollTo?.({top: 0, behavior: "smooth"}); });
    on("[data-vh-span]", el => { const span = Number(el.dataset.vhSpan) * DAY, mid = (S.win.start + S.win.end) / 2; setWindow(mid - span / 2, span);
      box.querySelectorAll("[data-vh-span]").forEach(b => { const sel = b === el; b.classList.toggle("selected", sel); b.setAttribute("aria-pressed", sel); }); drawChart(true); });
    on("[data-vh-move]", el => {
      const span = S.win.end - S.win.start;
      if (el.dataset.vhMove === "last") { const ts = tradesOf(S.cfg); if (ts.length) focusTrade(ts[ts.length - 1].tid); return; }
      setWindow(S.win.start + Number(el.dataset.vhMove) * span * 0.8, span); drawChart(true);
    });
    on("[data-vh-side]", el => { S.side = el.dataset.vhSide; render(); });
    on("[data-vh-outcome]", el => { S.outcome = el.dataset.vhOutcome; render(); });
    on("[data-vh-tid]", el => focusTrade(el.dataset.vhTid));
    box.querySelectorAll("[data-vh-tid],[data-vh-pick]").forEach(el => el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); el.click(); } }));
    box.querySelector("#vh-year")?.addEventListener("change", e => { S.year = e.target.value; render(); });
    box.querySelector("#vh-reason")?.addEventListener("change", e => { S.reason = e.target.value; render(); });
    box.querySelector("#vh-more")?.addEventListener("click", () => { S.limit += 300; render(); });
  }

  window.SpikeVidyaHistory = {
    mount(element) { box = element; render(); load(); },
  };
})();
