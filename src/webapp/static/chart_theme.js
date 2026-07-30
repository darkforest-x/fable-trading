/**
 * Shared K-line theme + trade-overlay helpers for the dashboard.
 * Used by app.js (signals / explore) and scout_mtf_app.js (radar).
 *
 * Visual contract (Claude short paper-chart / SOXL style):
 *   - candles: green up / red down
 *   - entry: blue solid + blue arrow (↓ short / ↑ long)
 *   - TP: green dashed  止盈 NxATR
 *   - SL: red dashed    止损 NxATR
 *   - exit: outcome-colored circle (no heavy exit price line)
 *   - MAs: same TG/YOLO SMA·EMA 20/60/120 palette everywhere
 */
(function (root) {
  "use strict";

  const F = root.FableFmt || {};

  /* ---------- palette ---------- */
  const CANDLE = {
    upColor: "#059669",
    downColor: "#dc2626",
    borderVisible: false,
    wickUpColor: "#059669",
    wickDownColor: "#dc2626",
  };

  const VOL = {
    up: "rgba(5,150,105,0.40)",
    down: "rgba(220,38,38,0.35)",
  };

  /** Trade overlay — Claude SOXL */
  const TRADE = {
    entry: "#2563eb",
    tp: "#059669",
    sl: "#dc2626",
    signal: "#64748b",
    mark: "#9ca3af",
    pathWin: "#059669",
    pathLose: "#dc2626",
    dim: "rgba(148,163,184,0.45)",
  };

  const OUTCOME_COLOR = {
    tp: "#059669",
    sl: "#dc2626",
    timeout: "#c98500",
    sl_ambiguous: "#dc2626",
  };

  const OUTCOME_CN = {
    tp: "止盈",
    sl: "止损",
    timeout: "超时",
    sl_ambiguous: "止损*",
    "": "未结束",
  };
  // keep empty-key for closed-unknown outcomes

  // SMA/EMA 20·60·120 — same as TG/YOLO notify charts
  const MA_ORDER = ["sma120", "sma60", "sma20", "ema120", "ema60", "ema20"];
  const MA_STYLE = {
    sma20: { color: "#3d8fd1", lineStyle: 0, lineWidth: 1.2 },
    sma60: { color: "#5cb8b0", lineStyle: 0, lineWidth: 1.1 },
    sma120: { color: "#8a8aaa", lineStyle: 0, lineWidth: 1.0 },
    ema20: { color: "#f06024", lineStyle: 0, lineWidth: 1.2 },
    ema60: { color: "#faa03c", lineStyle: 0, lineWidth: 1.1 },
    ema120: { color: "#c84696", lineStyle: 0, lineWidth: 1.0 },
  };

  /* ---------- small utils ---------- */
  function fmtPx(v, digits) {
    const n = Number(v);
    if (!Number.isFinite(n)) return "—";
    if (digits != null) return n.toFixed(digits);
    const a = Math.abs(n);
    if (a >= 1000) return n.toFixed(2);
    if (a >= 1) return n.toFixed(4);
    if (a >= 0.01) return n.toFixed(6);
    return n.toPrecision(4);
  }

  function fmtChartTime(t) {
    if (F.fmtChartTime) return F.fmtChartTime(t);
    if (t == null) return "";
    return String(t);
  }

  function chartTickMarkBj(time) {
    if (F.chartTickMarkBj) return F.chartTickMarkBj(time);
    return fmtChartTime(time);
  }

  function isShortSide(side) {
    const s = String(side || "short").toLowerCase();
    return s === "short" || s === "s" || s === "空" || s === "sell";
  }

  function outcomeColor(outcome, openPos) {
    if (openPos) return TRADE.entry;
    return OUTCOME_COLOR[outcome] || "#64748b";
  }

  function shortBarrierPrices(entry, atrAbs, atrPct, tpM, slM, pre) {
    if (pre && pre.tp_price != null && pre.sl_price != null) {
      return { tp: Number(pre.tp_price), sl: Number(pre.sl_price) };
    }
    const a = Number.isFinite(atrAbs) && atrAbs > 0
      ? atrAbs
      : (Number.isFinite(atrPct) && atrPct > 0 ? entry * atrPct : null);
    if (a == null) return { tp: null, sl: null };
    const tpMult = Number(tpM) || 5;
    const slMult = Number(slM) || 2;
    return { tp: entry - tpMult * a, sl: entry + slMult * a };
  }

  function longBarrierPrices(entry, atrAbs, atrPct, tpM, slM, pre) {
    if (pre && pre.tp_price != null && pre.sl_price != null) {
      return { tp: Number(pre.tp_price), sl: Number(pre.sl_price) };
    }
    const a = Number.isFinite(atrAbs) && atrAbs > 0
      ? atrAbs
      : (Number.isFinite(atrPct) && atrPct > 0 ? entry * atrPct : null);
    if (a == null) return { tp: null, sl: null };
    const tpMult = Number(tpM) || 5;
    const slMult = Number(slM) || 2;
    return { tp: entry + tpMult * a, sl: entry - slMult * a };
  }

  function shortExitFromRet(entry, ret) {
    if (!Number.isFinite(entry) || !Number.isFinite(ret)) return null;
    return entry * (1 - ret);
  }

  function longExitFromRet(entry, ret) {
    if (!Number.isFinite(entry) || !Number.isFinite(ret)) return null;
    return entry * (1 + ret);
  }

  /* ---------- chart factory ---------- */
  function chartLayout(overrides) {
    const opts = overrides || {};
    const base = {
      layout: { background: { type: "solid", color: "#ffffff" }, textColor: "#6b7280", fontSize: 12 },
      grid: { vertLines: { color: "#eef1f6" }, horzLines: { color: "#eef1f6" } },
      localization: {
        locale: "zh-CN",
        timeFormatter: (t) => fmtChartTime(t),
      },
      timeScale: {
        borderColor: "#e5e7eb",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 10,
        barSpacing: 8,
        minBarSpacing: 2,
        tickMarkFormatter: chartTickMarkBj,
      },
      rightPriceScale: {
        borderColor: "#e5e7eb",
        scaleMargins: { top: 0.08, bottom: 0.14 },
        entireTextOnly: false,
      },
      crosshair: {
        mode: 1,
        vertLine: { color: "rgba(37,99,235,0.35)", width: 1, style: 2, labelBackgroundColor: "#2563eb" },
        horzLine: { color: "rgba(107,114,128,0.35)", width: 1, style: 2, labelBackgroundColor: "#6b7280" },
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: { time: true, price: true }, mouseWheel: true, pinch: true },
      autoSize: true,
    };
    return {
      ...base,
      ...opts,
      layout: { ...base.layout, ...(opts.layout || {}) },
      grid: {
        vertLines: { ...base.grid.vertLines, ...(opts.grid && opts.grid.vertLines) },
        horzLines: { ...base.grid.horzLines, ...(opts.grid && opts.grid.horzLines) },
      },
      timeScale: { ...base.timeScale, ...(opts.timeScale || {}) },
      rightPriceScale: { ...base.rightPriceScale, ...(opts.rightPriceScale || {}) },
      crosshair: {
        ...base.crosshair,
        ...(opts.crosshair || {}),
        vertLine: { ...base.crosshair.vertLine, ...(opts.crosshair && opts.crosshair.vertLine) },
        horzLine: { ...base.crosshair.horzLine, ...(opts.crosshair && opts.crosshair.horzLine) },
      },
    };
  }

  function makeChart(el, opts) {
    if (typeof LightweightCharts === "undefined") {
      throw new Error("LightweightCharts missing");
    }
    return LightweightCharts.createChart(el, chartLayout(opts));
  }

  function candlestickOptions() {
    return { ...CANDLE };
  }

  function volPoint(c) {
    return {
      time: c.time,
      value: c.volume,
      color: c.close >= c.open ? VOL.up : VOL.down,
    };
  }

  function addMaSeries(chart, payloadOrMap, sink) {
    const lines = payloadOrMap && (payloadOrMap.mas || payloadOrMap.emas)
      ? (payloadOrMap.mas || payloadOrMap.emas)
      : (payloadOrMap || {});
    for (let i = 0; i < MA_ORDER.length; i++) {
      const name = MA_ORDER[i];
      const data = lines[name];
      if (!data || !data.length) continue;
      const st = MA_STYLE[name] || { color: "#666", lineStyle: 0, lineWidth: 1 };
      const s = chart.addLineSeries({
        color: st.color,
        lineWidth: st.lineWidth,
        lineStyle: st.lineStyle,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      s.setData(data);
      if (sink) sink.push(s);
    }
  }

  /* ---------- overlay primitives ---------- */
  /**
   * @param {object} store  { priceLines: [], levelSeries: [] }
   * @param {*} candleSeries
   * @param {*} chart
   */
  function clearTradeLevels(store, candleSeries, chart) {
    if (!store) return;
    (store.priceLines || []).forEach((l) => {
      try { candleSeries.removePriceLine(l); } catch (_) { /* ignore */ }
    });
    store.priceLines = [];
    (store.levelSeries || []).forEach((s) => {
      try { chart.removeSeries(s); } catch (_) { /* ignore */ }
    });
    store.levelSeries = [];
  }

  function addTradeLevel(store, candleSeries, chart, price, color, title, t0, t1, lineStyle) {
    if (price == null || !Number.isFinite(Number(price)) || !candleSeries) return;
    const p = Number(price);
    const style = lineStyle == null ? 0 : lineStyle;
    if (!store.priceLines) store.priceLines = [];
    if (!store.levelSeries) store.levelSeries = [];
    store.priceLines.push(candleSeries.createPriceLine({
      price: p,
      color: color,
      lineWidth: 1,
      lineStyle: style,
      axisLabelVisible: true,
      title: title || "",
    }));
    if (t0 != null && t1 != null && t1 >= t0 && chart) {
      const seg = chart.addLineSeries({
        color: color,
        lineWidth: style === 0 ? 2 : 1,
        lineStyle: style,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      seg.setData([
        { time: t0, value: p },
        { time: t1, value: p },
      ]);
      store.levelSeries.push(seg);
    }
  }

  /**
   * Entry / exit markers — Claude short/long.
   * @param {object} o
   * @param {number} o.tEntry
   * @param {number} [o.tExit]
   * @param {number} [o.tSignal]
   * @param {number} o.entry
   * @param {number|null} [o.exitPrice]
   * @param {string} [o.side]  short|long
   * @param {string} [o.outcome]
   * @param {boolean} [o.openPos]
   * @param {boolean} [o.includePriceInText=true]
   */
  function buildTradeMarkers(o) {
    const short = isShortSide(o.side);
    const entry = Number(o.entry);
    const markers = [];
    if (o.tSignal != null && o.tSignal !== o.tEntry) {
      markers.push({
        time: o.tSignal,
        position: "aboveBar",
        shape: "arrowDown",
        color: TRADE.signal,
        text: "信号",
        size: 1,
      });
    }
    if (o.tEntry != null && Number.isFinite(entry)) {
      const sideCn = short ? "空" : "多";
      const text = o.includePriceInText === false
        ? (short ? "入场 空" : "入场 多")
        : `入场 ${fmtPx(entry)} ${sideCn}`;
      markers.push({
        time: o.tEntry,
        position: short ? "aboveBar" : "belowBar",
        shape: short ? "arrowDown" : "arrowUp",
        color: TRADE.entry,
        text: text,
        size: 2,
      });
    }
    if (!o.openPos && o.tExit != null && o.exitPrice != null && Number.isFinite(Number(o.exitPrice))) {
      const ex = Number(o.exitPrice);
      const exitBelow = short ? ex <= entry : ex >= entry;
      markers.push({
        time: o.tExit,
        position: exitBelow ? "belowBar" : "aboveBar",
        shape: "circle",
        color: outcomeColor(o.outcome, false),
        text: OUTCOME_CN[o.outcome] || "出",
        size: 2,
      });
    }
    return markers;
  }

  /**
   * Paint entry / TP / SL / path / markers on a chart (shared by signals + radar).
   *
   * @param {object} ctx
   * @param {*} ctx.chart
   * @param {*} ctx.series          candlestick series
   * @param {object} ctx.store      { priceLines, levelSeries }
   * @param {*|null} [ctx.pathSeries]  optional reusable path line series
   * @param {object} opts
   * @param {number} opts.entry
   * @param {number|null} [opts.exit]
   * @param {number|null} [opts.tp]
   * @param {number|null} [opts.sl]
   * @param {number|null} [opts.mark]
   * @param {number} opts.tEntry
   * @param {number} opts.tExit
   * @param {number|null} [opts.tSignal]
   * @param {string} [opts.side]     default short
   * @param {string} [opts.outcome]
   * @param {number|null} [opts.ret]
   * @param {boolean} [opts.openPos]
   * @param {number} [opts.tpMult=5]
   * @param {number} [opts.slMult=2]
   * @param {boolean} [opts.drawPath=true]
   * @param {boolean} [opts.setMarkers=true]
   * @param {string} [opts.tpTitle]
   * @param {string} [opts.slTitle]
   * @param {string} [opts.entryTitle]
   */
  function paintTradeOverlay(ctx, opts) {
    if (!ctx || !ctx.chart || !ctx.series || !opts) return false;
    const entry = Number(opts.entry);
    if (!Number.isFinite(entry) || entry <= 0) return false;
    const short = isShortSide(opts.side);
    const tpM = Number(opts.tpMult != null ? opts.tpMult : 5);
    const slM = Number(opts.slMult != null ? opts.slMult : 2);
    const t0 = opts.tEntry;
    const t1 = opts.tExit != null ? opts.tExit : opts.tEntry;
    const tLine0 = opts.tSignal != null ? opts.tSignal : t0;
    const tpPx = opts.tp != null && Number.isFinite(Number(opts.tp)) ? Number(opts.tp) : null;
    const slPx = opts.sl != null && Number.isFinite(Number(opts.sl)) ? Number(opts.sl) : null;
    const exitPx = opts.exit != null && Number.isFinite(Number(opts.exit)) ? Number(opts.exit) : null;
    const markPx = opts.mark != null && Number.isFinite(Number(opts.mark)) ? Number(opts.mark) : null;
    const openPos = !!opts.openPos;
    const outcome = opts.outcome || "";
    const exitCol = outcomeColor(outcome, openPos);

    clearTradeLevels(ctx.store, ctx.series, ctx.chart);
    try {
      ctx.chart.priceScale("right").applyOptions({ autoScale: true });
    } catch (_) { /* ignore */ }

    const tpTitle = opts.tpTitle != null ? opts.tpTitle : `止盈 ${tpM}xATR`;
    const slTitle = opts.slTitle != null ? opts.slTitle : `止损 ${slM}xATR`;
    const entryTitle = opts.entryTitle != null ? opts.entryTitle : "入场";

    // draw order: SL (top for short) → entry → TP so labels read naturally
    if (slPx != null) addTradeLevel(ctx.store, ctx.series, ctx.chart, slPx, TRADE.sl, slTitle, tLine0, t1, 2);
    addTradeLevel(ctx.store, ctx.series, ctx.chart, entry, TRADE.entry, entryTitle, t0, t1, 0);
    if (tpPx != null) addTradeLevel(ctx.store, ctx.series, ctx.chart, tpPx, TRADE.tp, tpTitle, tLine0, t1, 2);
    if (markPx != null) {
      addTradeLevel(ctx.store, ctx.series, ctx.chart, markPx, TRADE.mark,
        openPos ? "标记价" : "最新", t0, t1, 3);
    }

    const pathEnd = openPos
      ? (markPx != null ? markPx : entry)
      : (exitPx != null ? exitPx : entry);
    if (opts.drawPath !== false && pathEnd != null) {
      const pathColor = (opts.ret != null && Number(opts.ret) < 0) || exitCol === OUTCOME_COLOR.sl
        ? TRADE.pathLose
        : (outcome === "tp" ? TRADE.pathWin : exitCol);
      if (ctx.pathSeries) {
        ctx.pathSeries.applyOptions({
          color: pathColor,
          lineWidth: 1,
          lineStyle: 2,
        });
        ctx.pathSeries.setData([
          { time: t0, value: entry },
          { time: t1, value: pathEnd },
        ]);
      } else {
        const path = ctx.chart.addLineSeries({
          color: pathColor,
          lineWidth: 1,
          lineStyle: 2,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        path.setData([
          { time: t0, value: entry },
          { time: t1, value: pathEnd },
        ]);
        ctx.store.levelSeries.push(path);
      }
    } else if (ctx.pathSeries) {
      try { ctx.pathSeries.setData([]); } catch (_) { /* ignore */ }
    }

    if (opts.setMarkers !== false) {
      const markers = buildTradeMarkers({
        tEntry: t0,
        tExit: t1,
        tSignal: opts.tSignal,
        entry: entry,
        exitPrice: exitPx,
        side: short ? "short" : "long",
        outcome: outcome,
        openPos: openPos,
        includePriceInText: opts.includePriceInText !== false,
      });
      try {
        ctx.series.setMarkers(markers);
      } catch (_) { /* ignore */ }
    }
    return true;
  }

  function zoomAround(chart, times, t0, t1, pad) {
    if (!chart || !times || !times.length) return null;
    const p = pad == null ? 40 : pad;
    let i0 = times.findIndex((t) => t >= t0);
    let i1 = times.findIndex((t) => t >= t1);
    if (i0 < 0) i0 = Math.max(0, times.length - p);
    if (i1 < 0) i1 = times.length - 1;
    const range = {
      from: Math.max(0, i0 - p),
      to: Math.min(times.length + 4, i1 + p),
    };
    setTimeout(() => {
      try {
        chart.timeScale().setVisibleLogicalRange(range);
      } catch (_) { /* ignore */ }
    }, 50);
    return range;
  }

  function showLastBars(chart, nBars, total) {
    if (!chart || !total) return;
    const n = Math.min(Math.max(nBars || 80, 20), total);
    const from = Math.max(0, total - n);
    try {
      chart.timeScale().setVisibleLogicalRange({ from: from - 0.5, to: total + 4 });
    } catch (_) { /* ignore */ }
  }

  /**
   * Overview markers for a list of tip-replay / trade rows (short by default).
   */
  function overviewTradeMarkers(rows, opts) {
    const o = opts || {};
    const snap = o.snapTime || ((t) => t);
    const defaultSide = o.side || "short";
    const out = [];
    for (let i = 0; i < rows.length; i++) {
      const m = rows[i];
      if (!m.eligible && !m.traded) continue;
      const short = isShortSide(m.side || defaultSide);
      const t = snap(m.time);
      if (m.traded) {
        out.push({
          time: t,
          position: short ? "aboveBar" : "belowBar",
          shape: short ? "arrowDown" : "arrowUp",
          color: TRADE.entry,
          text: "",
          size: 1,
        });
        if (m.exit_time) {
          out.push({
            time: snap(m.exit_time),
            position: "aboveBar",
            shape: "circle",
            color: OUTCOME_COLOR[m.outcome] || "#64748b",
            size: 0,
          });
        }
      } else {
        out.push({
          time: t,
          position: "belowBar",
          shape: "circle",
          color: "rgba(100,116,139,0.55)",
          text: "",
          size: 0,
        });
      }
    }
    // de-dupe
    const seen = new Set();
    const deduped = [];
    out.sort((a, b) => a.time - b.time || (a.position === "belowBar" ? -1 : 1));
    for (let i = 0; i < out.length; i++) {
      const mk = out[i];
      const k = mk.time + "|" + mk.position + "|" + mk.shape;
      if (seen.has(k)) continue;
      seen.add(k);
      deduped.push(mk);
    }
    return deduped;
  }

  root.FableChart = {
    CANDLE: CANDLE,
    VOL: VOL,
    TRADE: TRADE,
    OUTCOME_COLOR: OUTCOME_COLOR,
    OUTCOME_CN: OUTCOME_CN,
    MA_ORDER: MA_ORDER,
    MA_STYLE: MA_STYLE,
    fmtPx: fmtPx,
    isShortSide: isShortSide,
    shortBarrierPrices: shortBarrierPrices,
    longBarrierPrices: longBarrierPrices,
    shortExitFromRet: shortExitFromRet,
    longExitFromRet: longExitFromRet,
    chartLayout: chartLayout,
    makeChart: makeChart,
    candlestickOptions: candlestickOptions,
    volPoint: volPoint,
    addMaSeries: addMaSeries,
    clearTradeLevels: clearTradeLevels,
    addTradeLevel: addTradeLevel,
    buildTradeMarkers: buildTradeMarkers,
    paintTradeOverlay: paintTradeOverlay,
    overviewTradeMarkers: overviewTradeMarkers,
    zoomAround: zoomAround,
    showLastBars: showLastBars,
    outcomeColor: outcomeColor,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
