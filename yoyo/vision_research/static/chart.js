// TradingView 4.2 uses only the server's closed, causal SPIKE window.
// API: https://tradingview.github.io/lightweight-charts/docs/4.2/api/interfaces/IChartApi
let chart;
let candles;
let movingAverages = [];
let observer;
let activeKey;
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
    timeScale: { borderColor: token('--border'), timeVisible: true, secondsVisible: false, rightOffset: 4, minBarSpacing: 2,
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
}

export function showChart(data, key) {
  if (activeKey === key && chart) return;
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
  candles.setData(data.candles.map(row => ({ time: row.t / 1000, open: row.o, high: row.h, low: row.l, close: row.c })));
  movingAverages = keys.map((field) => {
    const series = chart.addLineSeries({ lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    series.setData(data.candles.map(row => Number.isFinite(row[field]) ? { time: row.t / 1000, value: row[field] } : { time: row.t / 1000 }));
    return series;
  });
  applyChartTheme();
  chart.timeScale().fitContent();
  observer = new ResizeObserver(() => {
    if (chart && container.clientWidth && container.clientHeight) chart.resize(container.clientWidth, container.clientHeight);
  });
  observer.observe(container);
  activeKey = key;
}

export function fitChart() { chart?.timeScale().fitContent(); }

export function captureChart() {
  if (!chart) throw new Error('图表尚未准备好。');
  // The library screenshot excludes the crosshair and captures the visible plot.
  // Store these pixels before changing layout or awaiting any network operation.
  const canvas = chart.takeScreenshot();
  if (canvas.width * canvas.height > 6_000_000) throw new Error('图表截图超过 600 万像素，请缩小浏览器窗口后再识别。');
  return canvas.toDataURL('image/png');
}

export function destroyChart() {
  observer?.disconnect();
  chart?.remove();
  chart = null; candles = null; observer = null; activeKey = null; movingAverages = [];
}

new MutationObserver(applyChartTheme).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
