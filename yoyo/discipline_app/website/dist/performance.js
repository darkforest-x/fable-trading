(() => {
  "use strict";

  const byId = id => document.getElementById(id);
  const color = { ice: "#79d8f5", amber: "#e6af60", grid: "#252b32", axis: "#a4acb6" };
  const number = (n, places = 1) => n.toLocaleString("en-US", {maximumFractionDigits: places});
  const pct = n => (n > 0 ? "+" : n < 0 ? "−" : "") + number(Math.abs(n), 2) + "%";
  const date = t => new Date(t).toISOString().slice(0, 10);
  const compactNumber = n => n >= 1000000 ? number(n / 1000000, 1) + "M" : n >= 10000 ? number(n / 1000, 1) + "k" : number(n, 0);
  const make = (tag, text, className) => {
    const el = document.createElement(tag);
    if (text !== null) el.textContent = text;
    if (className) el.className = className;
    return el;
  };

  let strategies = [];
  let selected = 0;
  let cursor = null;
  let scheduled = 0;
  let geometry = null;
  const cards = [];
  const annualCharts = [];

  function surface(canvas) {
    const width = canvas.clientWidth, height = canvas.clientHeight;
    if (width < 2 || height < 2) return null;
    const dpr = Math.min(3, window.devicePixelRatio || 1);
    if (canvas.width !== Math.round(width * dpr) || canvas.height !== Math.round(height * dpr)) {
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return {ctx, width, height};
  }

  function drawSeries(canvas, strategy, risk, miniature, showCursor) {
    const s = surface(canvas);
    if (!s) return;
    const {ctx, width, height} = s;
    const pts = strategy.points;
    const field = risk ? 2 : 1;
    const stroke = risk ? color.amber : color.ice;
    const left = miniature ? 1 : 4, right = width - (miniature ? 1 : 49);
    const top = miniature ? 3 : 12, bottom = height - (miniature ? 3 : risk ? 20 : 9);
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    let low = Infinity, high = -Infinity;
    for (const point of pts) { low = Math.min(low, point[field]); high = Math.max(high, point[field]); }
    if (risk) { high = 0; low = -Math.max(1, Math.ceil(-low)); }
    else if (high > low) {
      const pad = (high - low) * .04;
      low = Math.max(0, low - pad); high += pad;
    }
    const x = t => left + (right - left) * (t - t0) / (t1 - t0 || 1);
    const y = v => bottom - (bottom - top) * (v - low) / (high - low || 1);

    if (!miniature) {
      ctx.font = '10px ui-monospace, Menlo, monospace';
      ctx.fillStyle = color.axis; ctx.textAlign = "right"; ctx.textBaseline = "middle";
      const ticks = risk ? 2 : 3;
      for (let i = 0; i < ticks; i++) {
        const value = high - (high - low) * i / (ticks - 1);
        const pos = y(value);
        ctx.beginPath(); ctx.strokeStyle = color.grid; ctx.lineWidth = .7;
        ctx.moveTo(left, pos); ctx.lineTo(right + 3, pos); ctx.stroke();
        ctx.fillText(risk ? (value === 0 ? "0" : "−" + number(-value, 0)) + "%" : compactNumber(value), width - 1, pos);
      }
      if (risk) {
        ctx.textBaseline = "bottom";
        const tickCount = width < 470 ? 3 : 5;
        for (let i = 0; i < tickCount; i++) {
          const t = t0 + (t1 - t0) * i / (tickCount - 1);
          ctx.textAlign = i === 0 ? "left" : i === tickCount - 1 ? "right" : "center";
          ctx.fillText(date(t).slice(0, 7), x(t), height - 1);
        }
      }
    }

    const line = new Path2D();
    pts.forEach((point, i) => {
      if (!i) line.moveTo(x(point[0]), y(point[field]));
      else {
        if (strategy.curveMode === "settlement") line.lineTo(x(point[0]), y(pts[i - 1][field]));
        line.lineTo(x(point[0]), y(point[field]));
      }
    });
    const area = new Path2D(line);
    const zero = risk ? top : bottom;
    area.lineTo(right, zero); area.lineTo(left, zero); area.closePath();
    const gradient = ctx.createLinearGradient(0, top, 0, bottom);
    gradient.addColorStop(0, stroke + (risk ? "10" : "38"));
    gradient.addColorStop(1, stroke + (risk ? "40" : "00"));
    ctx.fillStyle = gradient; ctx.fill(area);
    ctx.strokeStyle = stroke; ctx.lineJoin = "round"; ctx.lineWidth = miniature ? 1.35 : 1.8;
    ctx.stroke(line);

    if (risk && !miniature && cursor === null) {
      const trough = pts.reduce((worst, point) => point[2] < worst[2] ? point : worst, pts[0]);
      const px = x(trough[0]), py = y(trough[2]);
      ctx.fillStyle = color.amber;
      ctx.beginPath(); ctx.arc(px, py, 2.7, 0, Math.PI * 2); ctx.fill();
      ctx.font = '10px ui-monospace, Menlo, monospace';
      ctx.textAlign = px < (left + right) / 2 ? "left" : "right";
      ctx.textBaseline = "bottom";
      ctx.fillText(date(trough[0]).slice(0, 7) + " 低点", px + (ctx.textAlign === "left" ? 5 : -5), py - 4);
    }
    if (showCursor && cursor !== null) {
      const point = pts[cursor], px = x(point[0]), py = y(point[field]);
      ctx.strokeStyle = "#a6b7c3"; ctx.lineWidth = .8; ctx.setLineDash([3, 4]);
      ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, bottom); ctx.stroke(); ctx.setLineDash([]);
      ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2); ctx.fillStyle = stroke; ctx.fill();
    }
    if (!risk && !miniature) geometry = {left, right, t0, t1};
  }

  function drawAll() {
    scheduled = 0;
    if (!strategies.length) return;
    const strategy = strategies[selected];
    drawSeries(byId("equity-chart"), strategy, false, false, true);
    drawSeries(byId("drawdown-chart"), strategy, true, false, true);
    drawSeries(byId("phone-equity"), strategies[0], false, true, false);
    drawSeries(byId("phone-drawdown"), strategies[0], true, true, false);
    cards.forEach((card, i) => drawSeries(card.querySelector("canvas"), strategies[i], false, true, false));
    for (const {canvas, value, min, max} of annualCharts) {
      const s = surface(canvas);
      if (!s) continue;
      const {ctx, width, height} = s;
      const unit = (height - 4) / (max - min || 1);
      const baseline = 2 + max * unit;
      const barHeight = value === 0 ? 0 : Math.max(1, Math.abs(value) * unit);
      ctx.fillStyle = color.grid; ctx.fillRect(3, baseline, width - 6, 1);
      ctx.fillStyle = value >= 0 ? color.ice : color.amber;
      ctx.fillRect(width * .28, value >= 0 ? baseline - barHeight : baseline, width * .44, barHeight);
    }
  }
  function redraw() { if (!scheduled) scheduled = requestAnimationFrame(drawAll); }

  function updateQuote() {
    const strat = strategies[selected];
    const point = strat.points[cursor === null ? strat.points.length - 1 : cursor];
    byId("readout").textContent = date(point[0]) + " UTC　资金 " + number(point[1], 2) + " USDT　回撤 " + pct(point[2]);
  }

  function show(index) {
    selected = index; cursor = null;
    const strategy = strategies[index];
    cards.forEach((b, i) => b.setAttribute("aria-pressed", String(i === index)));
    byId("case-title").textContent = strategy.name;
    byId("case-period").textContent = strategy.period.start + " — " + strategy.period.end;
    byId("metric-return").textContent = pct(strategy.cumulativeReturnPct);
    byId("metric-dd").textContent = pct(-Math.abs(strategy.maxDrawdownPct));
    byId("metric-end").textContent = number(strategy.terminal, 2);
    byId("metric-trades").textContent = number(strategy.trades, 0);
    byId("chart-scale").textContent = "期初 " + number(strategy.seed, 0) + " USDT";
    byId("method").textContent = strategy.drawdownBasis;
    byId("costs").textContent = strategy.sourceSampling + " " + strategy.fees;
    byId("limits").textContent = strategy.limitations;
    byId("years").replaceChildren();
    annualCharts.length = 0;
    const values = strategy.annualReturns.map(row => row.returnPct);
    const maxAnnual = Math.max(0, ...values), minAnnual = Math.min(0, ...values);
    for (const row of strategy.annualReturns) {
      const cell = make("div", null, "year-item"), canvas = make("canvas");
      canvas.setAttribute("aria-hidden", "true");
      cell.append(make("span", String(row.year)), canvas, make("b", pct(row.returnPct), row.returnPct < 0 ? "negative" : ""));
      byId("years").append(cell);
      annualCharts.push({canvas, value:row.returnPct, min:minAnnual, max:maxAnnual});
    }
    updateQuote(); redraw();
  }

  function closest(points, t) {
    let low = 0, high = points.length - 1;
    while (low < high) {
      const mid = (low + high) >>> 1;
      if (points[mid][0] < t) low = mid + 1;
      else high = mid;
    }
    return low > 0 && t - points[low - 1][0] < points[low][0] - t ? low - 1 : low;
  }
  function inspect(event) {
    if (!geometry || !strategies.length) return;
    const {left, right, t0, t1} = geometry;
    const px = event.clientX - event.currentTarget.getBoundingClientRect().left;
    cursor = closest(strategies[selected].points, t0 + (px - left) / (right - left) * (t1 - t0));
    updateQuote(); redraw();
  }
  for (const chart of [byId("equity-chart"), byId("drawdown-chart")]) {
    chart.addEventListener("pointerdown", inspect);
    chart.addEventListener("pointermove", event => { if (event.pointerType === "mouse" || event.buttons === 1) inspect(event); });
    chart.addEventListener("keydown", event => {
      if (!strategies.length || !["ArrowLeft","ArrowRight","Home","End","Escape"].includes(event.key)) return;
      event.preventDefault();
      const last = strategies[selected].points.length - 1;
      if (event.key === "Escape") cursor = null;
      else if (event.key === "Home") cursor = 0;
      else if (event.key === "End") cursor = last;
      else cursor = Math.min(last, Math.max(0, (cursor ?? last) + (event.key === "ArrowLeft" ? -1 : 1)));
      updateQuote(); redraw();
    });
  }

  const phone = byId("phone"), studio = byId("studio");
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  function resetTilt() { phone.style.removeProperty("--rx"); phone.style.removeProperty("--ry"); }
  studio.addEventListener("pointermove", event => {
    if (reducedMotion.matches) return;
    const rect = studio.getBoundingClientRect();
    phone.style.setProperty("--ry", ((event.clientX - rect.left) / rect.width * 22 - 27) + "deg");
    phone.style.setProperty("--rx", (13 - (event.clientY - rect.top) / rect.height * 13) + "deg");
  });
  for (const name of ["pointerleave","pointercancel","pointerup"]) studio.addEventListener(name, resetTilt);
  reducedMotion.addEventListener("change", resetTilt);
  new ResizeObserver(redraw).observe(byId("performance"));

  async function start() {
    const response = await fetch("assets/strategy-performance.json");
    if (!response.ok) throw Error("missing performance data");
    strategies = (await response.json()).strategies;
    for (const [i, strategy] of strategies.entries()) {
      const btn = make("button", null, "case"), text = make("span"), canvas = make("canvas");
      btn.type = "button"; canvas.setAttribute("aria-hidden", "true");
      text.append(make("b", strategy.name), make("small", pct(strategy.cumulativeReturnPct)));
      btn.append(text, canvas); btn.addEventListener("click", () => show(i));
      cards.push(btn); byId("strategy-picker").append(btn);
    }
    const hero = strategies[0];
    byId("phone-name").textContent = hero.name;
    byId("phone-return").textContent = pct(hero.cumulativeReturnPct);
    byId("phone-risk").textContent = pct(-Math.abs(hero.maxDrawdownPct));
    byId("phone-dates").textContent = hero.period.start + " — " + hero.period.end;
    show(0);
    const legacy = {"#library":"performance","#chart-lab":"performance","#monitor":"platform","#example-trade":"performance"};
    const anchor = legacy[location.hash];
    if (anchor) byId(anchor).scrollIntoView({behavior:"instant"});
  }
  start().catch(() => { byId("case-title").textContent = "回测数据暂时无法加载，请刷新重试。"; });
})();
