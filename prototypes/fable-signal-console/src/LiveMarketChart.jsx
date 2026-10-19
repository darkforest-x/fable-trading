import { useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LastPriceAnimationMode,
  LineSeries,
  createChart,
} from "lightweight-charts";

import { fetchMarketCandles, parseOkxStreamCandle } from "./marketClient.js";

const POLL_INTERVAL_MS = 15_000;
const HEARTBEAT_INTERVAL_MS = 20_000;

// Keep this display contract aligned with yoyo/layers/l1_detection/render.py
// and src/webapp/static/chart_theme.js: SMA/EMA 20, 60 and 120.
export const MARKET_MA_SERIES = Object.freeze([
  { key: "sma20", label: "SMA 20", period: 20, exponential: false, color: "#3d8fd1", lineWidth: 2 },
  { key: "ema20", label: "EMA 20", period: 20, exponential: true, color: "#f06024", lineWidth: 2 },
  { key: "sma60", label: "SMA 60", period: 60, exponential: false, color: "#5cb8b0", lineWidth: 1 },
  { key: "ema60", label: "EMA 60", period: 60, exponential: true, color: "#faa03c", lineWidth: 1 },
  { key: "sma120", label: "SMA 120", period: 120, exponential: false, color: "#8a8aaa", lineWidth: 1 },
  { key: "ema120", label: "EMA 120", period: 120, exponential: true, color: "#c84696", lineWidth: 1 },
]);

function movingAverage(candles, period, exponential = false) {
  if (!candles.length) return [];
  if (exponential) {
    const alpha = 2 / (period + 1);
    let value = candles[0].close;
    return candles.map((candle, index) => {
      value = index === 0 ? candle.close : candle.close * alpha + value * (1 - alpha);
      return { time: candle.time, value };
    });
  }
  let rolling = 0;
  const points = [];
  candles.forEach((candle, index) => {
    rolling += candle.close;
    if (index >= period) rolling -= candles[index - period].close;
    if (index >= period - 1) points.push({ time: candle.time, value: rolling / period });
  });
  return points;
}

function volumePoint(candle) {
  return {
    time: candle.time,
    value: candle.volume,
    color: candle.close >= candle.open ? "rgba(38, 166, 154, 0.32)" : "rgba(239, 83, 80, 0.32)",
  };
}

function formatPrice(value) {
  if (!Number.isFinite(value)) return "—";
  const digits = value >= 1_000 ? 1 : value >= 100 ? 2 : value >= 1 ? 3 : 6;
  return new Intl.NumberFormat("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function formatCstTime(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(seconds * 1000));
}

function createMarketChart(container) {
  return createChart(container, {
    autoSize: true,
    height: 380,
    layout: {
      background: { type: ColorType.Solid, color: "#080c10" },
      textColor: "#71808b",
      fontFamily: '"SFMono-Regular", Consolas, monospace',
      fontSize: 11,
      attributionLogo: true,
    },
    grid: {
      vertLines: { color: "#151c22", style: 1 },
      horzLines: { color: "#151c22", style: 1 },
    },
    crosshair: {
      mode: CrosshairMode.Normal,
      vertLine: { color: "#53616b", width: 1, style: 3, labelBackgroundColor: "#28323a" },
      horzLine: { color: "#53616b", width: 1, style: 3, labelBackgroundColor: "#28323a" },
    },
    rightPriceScale: { borderColor: "#26313a", scaleMargins: { top: 0.08, bottom: 0.24 } },
    timeScale: {
      borderColor: "#26313a",
      timeVisible: true,
      secondsVisible: false,
      rightOffset: 5,
      barSpacing: 8,
      minBarSpacing: 3,
    },
    handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: true },
    handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
    localization: {
      locale: "zh-CN",
      timeFormatter: (time) => formatCstTime(Number(time)),
      priceFormatter: formatPrice,
    },
  });
}

function addMarketSeries(chart) {
  const candleSeries = chart.addSeries(CandlestickSeries, {
    upColor: "#20b895",
    downColor: "#f15b64",
    wickUpColor: "#50c9af",
    wickDownColor: "#f07980",
    borderVisible: false,
    lastValueVisible: true,
    priceLineVisible: true,
    lastPriceAnimation: LastPriceAnimationMode.OnDataUpdate,
  });
  const maSeries = new Map(MARKET_MA_SERIES.map((definition) => [
    definition.key,
    chart.addSeries(LineSeries, {
      color: definition.color,
      lineWidth: definition.lineWidth,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
      title: "",
    }),
  ]));
  const volumeSeries = chart.addSeries(HistogramSeries, {
    priceFormat: { type: "volume" },
    priceScaleId: "volume",
    priceLineVisible: false,
    lastValueVisible: false,
  });
  volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  return { candleSeries, maSeries, volumeSeries };
}

function setMarketSeriesData(series, candles, movingAverages = null) {
  series.candleSeries.setData(candles);
  MARKET_MA_SERIES.forEach((definition) => {
    const points = movingAverages?.[definition.key]
      ?? movingAverage(candles, definition.period, definition.exponential);
    series.maSeries.get(definition.key).setData(points);
  });
  series.volumeSeries.setData(candles.map(volumePoint));
}

function replayXCoordinate(chart, candles, logicalIndex) {
  if (candles.length < 2) return null;
  const anchorIndex = Math.max(0, Math.min(candles.length - 2, Math.floor(logicalIndex)));
  const x0 = chart.timeScale().timeToCoordinate(candles[anchorIndex].time);
  const x1 = chart.timeScale().timeToCoordinate(candles[anchorIndex + 1].time);
  if (![x0, x1].every(Number.isFinite)) return null;
  return x0 + (logicalIndex - anchorIndex) * (x1 - x0);
}

export function LiveMarketChart({ symbol, onMetaChange }) {
  const containerRef = useRef(null);
  const [view, setView] = useState({ status: "loading", bar: null, error: "" });
  const fitContentRef = useRef(() => {});

  useEffect(() => {
    if (!containerRef.current) return undefined;
    let disposed = false;
    let socket = null;
    let heartbeatTimer = null;
    let pollTimer = null;
    let reconnectTimer = null;
    let reconnectDelay = 3_000;
    let streamContract = null;
    let hasFit = false;
    let lastMessageAt = Date.now();
    const candles = [];

    // Lightweight Charts v5 API and real-time update contract:
    // https://tradingview.github.io/lightweight-charts/docs/migrations/from-v4-to-v5
    // https://tradingview.github.io/lightweight-charts/docs/api/interfaces/ISeriesApi#update
    const chart = createMarketChart(containerRef.current);
    const series = addMarketSeries(chart);
    const { candleSeries, maSeries, volumeSeries } = series;

    fitContentRef.current = () => chart.timeScale().fitContent();

    const publish = (status, candle, error = "") => {
      if (disposed) return;
      const previous = candles.at(-2);
      const change = candle && previous && previous.close
        ? ((candle.close / previous.close) - 1) * 100
        : null;
      const meta = {
        symbol,
        status,
        lastPrice: candle?.close ?? null,
        change,
        candleTime: candle?.time ?? null,
        receivedAt: Math.floor(Date.now() / 1000),
        confirmed: candle?.confirmed ?? null,
        error,
      };
      setView({ status, bar: candle, error });
      onMetaChange?.(meta);
    };

    const renderAll = (incoming, status) => {
      candles.splice(0, candles.length, ...incoming);
      setMarketSeriesData(series, candles);
      if (!hasFit) {
        chart.timeScale().fitContent();
        hasFit = true;
      }
      publish(status, candles.at(-1));
    };

    const renderUpdate = (candle) => {
      const latest = candles.at(-1);
      if (latest && candle.time < latest.time) return;
      if (latest && candle.time === latest.time) candles[candles.length - 1] = candle;
      else candles.push(candle);
      if (candles.length > 300) candles.shift();
      candleSeries.update(candle);
      volumeSeries.update(volumePoint(candle));
      MARKET_MA_SERIES.forEach((definition) => {
        const point = movingAverage(candles, definition.period, definition.exponential).at(-1);
        if (point) maSeries.get(definition.key).update(point);
      });
      publish("live", candle);
    };

    const scheduleReconnect = () => {
      if (disposed || reconnectTimer || !streamContract) return;
      publish("polling", candles.at(-1));
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        connectStream();
      }, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 30_000);
    };

    const connectStream = () => {
      if (disposed || !streamContract) return;
      const currentSocket = new WebSocket(streamContract.url);
      socket = currentSocket;
      publish("connecting", candles.at(-1));
      currentSocket.addEventListener("open", () => {
        if (disposed || socket !== currentSocket) return;
        reconnectDelay = 3_000;
        lastMessageAt = Date.now();
        currentSocket.send(JSON.stringify({
          id: `fable${Date.now()}`,
          op: "subscribe",
          args: [{ channel: streamContract.channel, instId: streamContract.instrument }],
        }));
        heartbeatTimer = window.setInterval(() => {
          if (currentSocket.readyState === WebSocket.OPEN && Date.now() - lastMessageAt >= HEARTBEAT_INTERVAL_MS) {
            currentSocket.send("ping");
          }
        }, HEARTBEAT_INTERVAL_MS);
      });
      currentSocket.addEventListener("message", (event) => {
        lastMessageAt = Date.now();
        if (event.data === "pong") return;
        let payload;
        try {
          payload = JSON.parse(event.data);
        } catch {
          return;
        }
        if (payload.event === "error") {
          publish("polling", candles.at(-1), payload.msg || "OKX WebSocket 订阅失败");
          return;
        }
        const candle = parseOkxStreamCandle(payload.data?.[0]);
        if (candle) renderUpdate(candle);
        else if (payload.event === "subscribe") publish("live", candles.at(-1));
      });
      currentSocket.addEventListener("close", () => {
        if (heartbeatTimer) window.clearInterval(heartbeatTimer);
        heartbeatTimer = null;
        if (socket === currentSocket) socket = null;
        scheduleReconnect();
      });
      currentSocket.addEventListener("error", () => {
        publish("polling", candles.at(-1), "WebSocket 暂不可用，已切换 REST 轮询");
        currentSocket.close();
      });
    };

    const syncRest = async (initial = false) => {
      try {
        const payload = await fetchMarketCandles(symbol);
        if (disposed) return;
        streamContract = payload.stream;
        renderAll(payload.candles, socket?.readyState === WebSocket.OPEN ? "live" : "polling");
        if (initial && !socket) connectStream();
      } catch (error) {
        publish("error", candles.at(-1), error.message || "实时行情加载失败");
      }
    };

    syncRest(true);
    pollTimer = window.setInterval(() => syncRest(false), POLL_INTERVAL_MS);
    return () => {
      disposed = true;
      if (pollTimer) window.clearInterval(pollTimer);
      if (heartbeatTimer) window.clearInterval(heartbeatTimer);
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      if (socket) socket.close();
      chart.remove();
      fitContentRef.current = () => {};
    };
  }, [onMetaChange, symbol]);

  const labels = {
    loading: "加载行情",
    connecting: "连接实时流",
    live: "实时流已连接",
    polling: "轮询同步",
    error: "行情离线",
  };
  const bar = view.bar;

  return (
    <div className="live-chart-shell">
      <div className="live-chart-toolbar">
        <div className="ohlc-strip" aria-live="polite">
          <span>O <b>{formatPrice(bar?.open)}</b></span>
          <span>H <b>{formatPrice(bar?.high)}</b></span>
          <span>L <b>{formatPrice(bar?.low)}</b></span>
          <span>C <b>{formatPrice(bar?.close)}</b></span>
        </div>
        <div className="stream-tools">
          <span className={`stream-status stream-${view.status}`}><i />{labels[view.status]}</span>
          <button type="button" onClick={() => fitContentRef.current()}>适配窗口</button>
        </div>
      </div>
      <div className="trading-chart" ref={containerRef} aria-label={`${symbol} OKX 15 分钟实时交互 K 线`} />
      <div className="live-chart-safety">
        <span>OKX PUBLIC · 15m</span>
        <strong>{view.error || "拖拽 / 缩放 / 十字线 · 实时行情仅展示，不送入 L1/L2"}</strong>
      </div>
    </div>
  );
}

export function ReplayMarketChart({ symbol, replay }) {
  const containerRef = useRef(null);
  const [overlayBoxes, setOverlayBoxes] = useState([]);
  const fitContentRef = useRef(() => {});

  useEffect(() => {
    if (!containerRef.current || !replay?.candles?.length) return undefined;
    const container = containerRef.current;
    const chart = createMarketChart(container);
    const series = addMarketSeries(chart);
    const candles = replay.candles;
    let animationFrame = null;

    setMarketSeriesData(series, candles, replay.moving_averages);
    chart.timeScale().fitContent();
    fitContentRef.current = () => chart.timeScale().fitContent();

    const updateOverlayBoxes = () => {
      const projected = (replay.boxes || []).map((box, index) => {
        const x0 = replayXCoordinate(chart, candles, box.logical_from);
        const x1 = replayXCoordinate(chart, candles, box.logical_to);
        const y0 = series.candleSeries.priceToCoordinate(box.price_top);
        const y1 = series.candleSeries.priceToCoordinate(box.price_bottom);
        if (![x0, x1, y0, y1].every(Number.isFinite)) return null;
        return {
          ...box,
          key: `${box.class_name}-${index}`,
          left: Math.min(x0, x1),
          top: Math.min(y0, y1),
          width: Math.max(Math.abs(x1 - x0), 2),
          height: Math.max(Math.abs(y1 - y0), 2),
        };
      }).filter(Boolean);
      setOverlayBoxes(projected);
    };
    const scheduleOverlayUpdate = () => {
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
      animationFrame = window.requestAnimationFrame(() => {
        animationFrame = null;
        updateOverlayBoxes();
      });
    };

    chart.timeScale().subscribeVisibleLogicalRangeChange(scheduleOverlayUpdate);
    const resizeObserver = new ResizeObserver(scheduleOverlayUpdate);
    resizeObserver.observe(container);
    const projectionEvents = ["wheel", "pointermove", "pointerup", "dblclick"];
    projectionEvents.forEach((eventName) => {
      container.addEventListener(eventName, scheduleOverlayUpdate, { passive: true });
    });
    scheduleOverlayUpdate();

    return () => {
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
      projectionEvents.forEach((eventName) => {
        container.removeEventListener(eventName, scheduleOverlayUpdate);
      });
      resizeObserver.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(scheduleOverlayUpdate);
      chart.remove();
      fitContentRef.current = () => {};
    };
  }, [replay]);

  const lastBar = replay?.candles?.at(-1);
  return (
    <div className="live-chart-shell replay-market-shell">
      <div className="live-chart-toolbar">
        <div className="ohlc-strip">
          <span>O <b>{formatPrice(lastBar?.open)}</b></span>
          <span>H <b>{formatPrice(lastBar?.high)}</b></span>
          <span>L <b>{formatPrice(lastBar?.low)}</b></span>
          <span>C <b>{formatPrice(lastBar?.close)}</b></span>
        </div>
        <div className="stream-tools">
          <span className="stream-status stream-replay"><i />历史模型窗口</span>
          <button type="button" onClick={() => fitContentRef.current()}>适配窗口</button>
        </div>
      </div>
      <div className="replay-chart-stage">
        <div className="trading-chart" ref={containerRef} aria-label={`${symbol} 15 分钟历史模型回放交互 K 线`} />
        {overlayBoxes.map((box) => (
          <span
            className="interactive-model-box"
            key={box.key}
            style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
            aria-label={`${box.class_name} 信号框，置信度 ${box.confidence.toFixed(2)}`}
          >
            <b>{box.confidence.toFixed(2)}</b>
          </span>
        ))}
        <span className="model-input-badge replay-window-badge">
          VERIFIED MODEL WINDOW · {replay.window_bars} BARS
        </span>
      </div>
      <div className="live-chart-safety replay-chart-safety">
        <span>PRE-HOLDOUT REPLAY · 15m</span>
        <strong>同一模型输入窗口 · 六均线 · 检测框来自真实 L1 推理</strong>
      </div>
    </div>
  );
}
