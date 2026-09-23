// TradingView 4.2 uses a confirmed SPIKE seed plus a separate live observation.
// API: https://tradingview.github.io/lightweight-charts/docs/4.2/api/interfaces/IChartApi
let chart;
let candles;
let movingAverages = [];
let observer;
let activeKey;
let activeDataHash;
let candleCount = 0;
let firstCandleTimeMs;
let candleIntervalMs;
let markerData;
let displayMarkers = [];
const keys = ['sma20', 'ema20', 'sma60', 'ema60', 'sma120', 'ema120'];
const host = () => document.getElementById('tradingview-chart');
const token = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const axisTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false });
const axisDate = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit' });
const crosshairTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });

function palette() {
  return {
    layout: { background: { type: 'solid', color: token('--surface') }, textColor: token('--text-muted'), fontSize: 14, fontFamily: getComputedStyle(document.body).fontFamily },
    grid: { vertLines: { color: token('--chart-grid') }, horzLines: { color: token('--chart-grid') } },
    rightPriceScale: { borderColor: token('--border'), scaleMargins: { top: .12, bottom: .12 } },
    timeScale: { borderColor: token('--border'), timeVisible: true, secondsVisible: false, rightOffset: 8, minBarSpacing: 2,
      tickMarkFormatter: (time, type) => (type < 3 ? axisDate : axisTime).format(new Date(time * 1000)) },
    crosshair: { mode: 0, vertLine: { color: token('--text-muted'), labelBackgroundColor: token('--text-soft') }, horzLine: { color: token('--text-muted'), labelBackgroundColor: token('--text-soft') } },
  };
}

export function applyChartTheme() {
  if (!chart) return;
  chart.applyOptions(palette());
  const up = token('--chart-up'), down = token('--chart-down');
  candles.applyOptions({ upColor: up, downColor: down, wickUpColor: up, wickDownColor: down });
  movingAverages.forEach((series, index) => series.applyOptions({ color: token(`--ma-${keys[index]}`) }));
  updateSignalMarkers(markerData);
}

// SPIKE reports candle CLOSE time, while Lightweight Charts uses candle OPEN
// time. Only the exact, already-closed source candle can receive the marker.
// These are source annotations for the user, never model decisions.
function signalMarkersFor(data) {
  const p = data?.provenance;
  if (!p || !['signal_close', 'live_observation'].includes(p.time_boundary)
      || !['long', 'short'].includes(p.side)) return [];
  const close = p.signal_bar_close_ms ?? p.bar_close_ms;
  const duration = p.timeframe_min * 60_000;
  if (!Number.isFinite(close) || !Number.isFinite(duration) || duration <= 0) return [];
  const open = close - duration;
  const row = data.candles?.find(item => item.t === open);
  if (!row || row.is_closed === false) return [];
  const long = p.side === 'long';
  const label = long ? '做多' : '做空';
  const narrow = host()?.clientWidth > 0 && host().clientWidth < 600;
  return [{
    time: open / 1000, position: long ? 'belowBar' : 'aboveBar',
    shape: long ? 'arrowUp' : 'arrowDown', color: token(long ? '--chart-up' : '--chart-down'),
    text: narrow ? label : `${label} ${axisTime.format(new Date(close))}`,
    id: `spike:${p.id || close}`, size: 1.3,
  }];
}

function updateSignalMarkers(data) {
  markerData = data;
  displayMarkers = signalMarkersFor(data);
  candles?.setMarkers(displayMarkers);
}

export function showChart(data, key) {
  if (activeKey === key && chart) {
    if (data?.chart_sha256 && data.chart_sha256 === activeDataHash) {
      updateSignalMarkers(data);
      return;
    }
    const scale = chart.timeScale();
    const logicalRange = scale.getVisibleLogicalRange();
    const followedTail = !logicalRange || logicalRange.to >= candleCount - 1.5;
    const oldCount = candleCount;
    const oldFirst = firstCandleTimeMs;
    const oldInterval = candleIntervalMs;
    updateSeries(data);
    preserveLogicalRange(scale, logicalRange, oldCount, oldFirst, oldInterval, followedTail);
    activeDataHash = data?.chart_sha256;
    return;
  }
  destroyChart();
  if (!window.LightweightCharts) throw new Error('TradingView 图表组件未载入，请刷新页面。');
  const container = host();
  chart = window.LightweightCharts.createChart(container, {
    ...palette(), width: container.clientWidth, height: container.clientHeight,
    localization: { locale: 'zh-CN', timeFormatter: time => crosshairTime.format(new Date(time * 1000)) },
    handleScroll: { vertTouchDrag: false },
  });
  const minimum = Math.min(...data.candles.map(row => row.l));
  const precision = Math.min(10, Math.max(2, 4 - Math.floor(Math.log10(minimum))));
  candles = chart.addCandlestickSeries({ borderVisible: false, priceFormat: { type: 'price', precision, minMove: 10 ** -precision } });
  movingAverages = keys.map((field) => {
    return chart.addLineSeries({ lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  });
  updateSeries(data);
  applyChartTheme();
  chart.timeScale().fitContent();
  observer = new ResizeObserver(() => {
    if (chart && container.clientWidth && container.clientHeight) {
      chart.resize(container.clientWidth, container.clientHeight);
      updateSignalMarkers(markerData);
    }
  });
  observer.observe(container);
  activeKey = key;
  activeDataHash = data?.chart_sha256;
}

function updateSeries(data) {
  const rows = Array.isArray(data?.candles) ? data.candles : [];
  if (!rows.length || !candles) return;
  candles.setData(rows.map(row => ({ time: row.t / 1000, open: row.o, high: row.h, low: row.l, close: row.c })));
  movingAverages.forEach((series, index) => {
    const field = keys[index];
    series.setData(rows.map(row => Number.isFinite(row[field])
      ? { time: row.t / 1000, value: row[field] }
      : { time: row.t / 1000 }));
  });
  candleCount = rows.length;
  firstCandleTimeMs = Number(rows[0].t);
  candleIntervalMs = rows.length > 1 ? Number(rows[1].t) - Number(rows[0].t) : undefined;
  updateSignalMarkers(data);
}

function preserveLogicalRange(scale, range, oldLength, oldFirst, oldInterval, followedTail) {
  const length = candleCount;
  if (!range || !length) return;
  const width = range.to - range.from;
  if (followedTail) {
    const rightOffset = range.to - (oldLength - 1);
    const to = length - 1 + rightOffset;
    try { scale.setVisibleLogicalRange({ from: to - width, to }); } catch { /* Keep the chart library's valid range. */ }
    return;
  }
  const delta = oldInterval > 0 && Number.isFinite(oldFirst)
    ? (firstCandleTimeMs - oldFirst) / oldInterval
    : 0;
  try { scale.setVisibleLogicalRange({ from: range.from - delta, to: range.to - delta }); }
  catch { /* Keep the chart library's valid range. */ }
}

export function fitChart() { chart?.timeScale().fitContent(); }

export function captureChart() {
  if (!chart) throw new Error('图表尚未准备好。');
  const scale = chart.timeScale();
  const range = scale.getVisibleLogicalRange();
  const lastIndex = candleCount - 1;
  const validRange = range
    && Number.isFinite(range.from)
    && Number.isFinite(range.to)
    && range.from <= range.to;
  if (!validRange || lastIndex < 0
      || range.from > lastIndex - 0.5
      || range.to < lastIndex + 0.5) {
    throw new Error('当前视图没有完整显示最新K线，请先点「回到盘口」后再识别。');
  }
  const lastBarOpenMs = candleCount === 1
    ? firstCandleTimeMs
    : firstCandleTimeMs + lastIndex * candleIntervalMs;
  if (!Number.isFinite(lastBarOpenMs)) {
    throw new Error('当前视图没有完整显示最新K线，请先点「回到盘口」后再识别。');
  }
  // Human-facing signal arrows must not suggest the answer to the reviewer.
  // setMarkers is synchronous; takeScreenshot flushes the chart's pending paint.
  // Restore the display even if screenshot encoding or size validation fails.
  candles.setMarkers([]);
  try {
    const canvas = chart.takeScreenshot();
    if (canvas.width * canvas.height > 6_000_000) throw new Error('图表截图超过 600 万像素，请缩小浏览器窗口后再识别。');
    return {
      dataUrl: canvas.toDataURL('image/png'),
      viewport: {
        from: range.from,
        to: range.to,
        bar_count: candleCount,
        last_bar_open_ms: lastBarOpenMs,
      },
    };
  } finally {
    candles.setMarkers(displayMarkers);
  }
}

export function destroyChart() {
  observer?.disconnect();
  chart?.remove();
  chart = null; candles = null; observer = null; activeKey = null; activeDataHash = null;
  candleCount = 0; firstCandleTimeMs = undefined; candleIntervalMs = undefined; movingAverages = [];
  markerData = undefined; displayMarkers = [];
}

new MutationObserver(applyChartTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
