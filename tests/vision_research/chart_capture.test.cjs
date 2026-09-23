const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const chartPath = path.join(__dirname, '../../yoyo/vision_research/static/chart.js');
const chartSource = fs.readFileSync(chartPath, 'utf8')
  .replace(/^export function /gm, 'function ')
  + '\n;globalThis.__chartApi = { showChart, fitChart, captureChart, destroyChart, applyChartTheme };';

const candleRows = Array.from({ length: 120 }, (_, index) => ({
  t: 1_700_000_000_000 + index * 60_000,
  o: 100 + index,
  h: 102 + index,
  l: 99 + index,
  c: 101 + index,
  sma20: 100 + index,
  ema20: 100 + index,
  sma60: 100 + index,
  ema60: 100 + index,
  sma120: 100 + index,
  ema120: 100 + index,
}));

function createHarness() {
  let range = null;
  let seriesBarCount = 0;
  let screenshotCalls = 0;
  let markers = [];
  let capturedMarkers = null;
  let screenshotFailure = false;
  const screenshotDataUrl = 'data:image/png;base64,c2NyZWVuc2hvdA==';
  const scale = {
    getVisibleLogicalRange: () => range && { ...range },
    setVisibleLogicalRange: (next) => { range = { ...next }; },
    fitContent: () => {
      const lastIndex = seriesBarCount - 1;
      range = { from: 0, to: lastIndex + 4 };
    },
  };
  const series = {
    setData: (rows) => { seriesBarCount = rows.length; },
    setMarkers: (value) => { markers = JSON.parse(JSON.stringify(value)); },
    applyOptions: () => {},
  };
  const chart = {
    addCandlestickSeries: () => series,
    addLineSeries: () => ({ setData: () => {}, applyOptions: () => {} }),
    applyOptions: () => {},
    remove: () => {},
    takeScreenshot: () => {
      screenshotCalls += 1;
      capturedMarkers = JSON.parse(JSON.stringify(markers));
      if (screenshotFailure) throw new Error('screenshot failed');
      return { width: 800, height: 600, toDataURL: () => screenshotDataUrl };
    },
    timeScale: () => scale,
  };
  const container = { clientWidth: 800, clientHeight: 600 };
  const context = vm.createContext({
    document: {
      body: {},
      documentElement: {},
      getElementById: () => container,
    },
    getComputedStyle: () => ({
      fontFamily: 'sans-serif',
      getPropertyValue: () => '#ffffff',
    }),
    MutationObserver: class { observe() {} },
    ResizeObserver: class { observe() {} disconnect() {} },
    window: {
      LightweightCharts: {
        createChart: () => chart,
      },
    },
  });
  vm.runInContext(chartSource, context, { filename: chartPath });
  return {
    api: context.__chartApi,
    setRange: (next) => { range = next && { ...next }; },
    getRange: () => range && { ...range },
    screenshotCalls: () => screenshotCalls,
    screenshotDataUrl,
    markers: () => markers,
    capturedMarkers: () => capturedMarkers,
    failScreenshot: () => { screenshotFailure = true; },
  };
}

function openChart(harness) {
  harness.api.showChart({ candles: candleRows, chart_sha256: 'a'.repeat(64) }, 'signal-1');
}

test('initial fit captures the latest full bar with matching viewport metadata', () => {
  const harness = createHarness();
  openChart(harness);

  const capture = harness.api.captureChart();
  assert.equal(capture.dataUrl, harness.screenshotDataUrl);
  assert.deepEqual({ ...capture.viewport }, {
    from: 0,
    to: candleRows.length - 1 + 4,
    bar_count: candleRows.length,
    last_bar_open_ms: candleRows.at(-1).t,
  });
  assert.equal(harness.screenshotCalls(), 1);
});

test('a panned historical viewport cannot be captured for recognition', () => {
  const harness = createHarness();
  openChart(harness);
  harness.setRange({ from: 0, to: candleRows.length - 1 - 30 });

  assert.throws(
    () => harness.api.captureChart(),
    /当前视图没有完整显示最新K线，请先点「回到盘口」后再识别。/,
  );
  assert.equal(harness.screenshotCalls(), 0);
});

test('a latest bar with less than half a logical unit visible at either edge is blocked', () => {
  const harness = createHarness();
  openChart(harness);
  const lastIndex = candleRows.length - 1;

  harness.setRange({ from: 0, to: lastIndex + 0.49 });
  assert.throws(() => harness.api.captureChart(), /没有完整显示最新K线/);
  harness.setRange({ from: lastIndex - 0.49, to: lastIndex + 4 });
  assert.throws(() => harness.api.captureChart(), /没有完整显示最新K线/);
  assert.equal(harness.screenshotCalls(), 0);
});

test('missing, non-finite, and reversed logical ranges are blocked', () => {
  const harness = createHarness();
  openChart(harness);
  const latestRange = { from: 0, to: candleRows.length - 1 + 4 };

  for (const range of [
    null,
    { from: 0, to: Number.NaN },
    { from: Number.POSITIVE_INFINITY, to: Number.POSITIVE_INFINITY },
    { from: 10, to: 9 },
  ]) {
    harness.setRange(range);
    assert.throws(() => harness.api.captureChart(), /没有完整显示最新K线/);
  }
  harness.setRange(latestRange);
  assert.equal(harness.api.captureChart().viewport.last_bar_open_ms, candleRows.at(-1).t);
});

test('fitChart restores a capturable tail viewport after panning', () => {
  const harness = createHarness();
  openChart(harness);
  harness.setRange({ from: 10, to: candleRows.length - 1 - 20 });

  harness.api.fitChart();
  const capture = harness.api.captureChart();
  assert.ok(harness.getRange().to >= candleRows.length - 1 + 0.5);
  assert.equal(capture.viewport.last_bar_open_ms, candleRows.at(-1).t);
});

function signalChart(side = 'short') {
  return { candles: candleRows, chart_sha256: 'b'.repeat(64),
    provenance: { id: 'source-signal', time_boundary: 'live_observation', side,
      timeframe_min: 1, signal_bar_close_ms: candleRows.at(-3).t + 60_000 } };
}

test('source signal is anchored to its closed candle, not the next candle at close time', () => {
  const harness = createHarness();
  for (const side of ['short', 'long']) {
    const data = signalChart(side);
    harness.api.showChart(data, side);
    const [marker] = harness.markers();
    assert.equal(marker.time, candleRows.at(-3).t / 1000);
    assert.equal(marker.position, side === 'long' ? 'belowBar' : 'aboveBar');
    assert.equal(marker.shape, side === 'long' ? 'arrowUp' : 'arrowDown');
    assert.match(marker.text, side === 'long' ? /做多/ : /做空/);
    const closeTime = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour: '2-digit', minute: '2-digit', hour12: false }).format(data.provenance.signal_bar_close_ms);
    assert.ok(marker.text.endsWith(closeTime));
  }
});

test('markers remain display-only and are restored after capture or capture failure', () => {
  const harness = createHarness();
  harness.api.showChart(signalChart(), 'one');
  const original = harness.markers();
  harness.api.captureChart();
  assert.deepEqual(harness.capturedMarkers(), []);
  assert.deepEqual(harness.markers(), original);
  harness.failScreenshot();
  assert.throws(() => harness.api.captureChart(), /screenshot failed/);
  assert.deepEqual(harness.capturedMarkers(), []);
  assert.deepEqual(harness.markers(), original);
});

test('same-price refresh and theme refresh retain current source marker semantics', () => {
  const harness = createHarness();
  const data = signalChart();
  harness.api.showChart(data, 'one');
  harness.api.applyChartTheme();
  assert.equal(harness.markers().length, 1);
  data.provenance.side = 'long';
  harness.api.showChart(data, 'one');
  assert.equal(harness.markers()[0].shape, 'arrowUp');
  data.candles = candleRows.slice(-2);
  data.chart_sha256 = 'c'.repeat(64);
  harness.api.showChart(data, 'one');
  assert.deepEqual(harness.markers(), []);
});

test('no source marker is invented for replay, unknown directions, gaps, or a forming source bar', () => {
  const harness = createHarness();
  const cases = [
    data => { data.provenance.time_boundary = 'historical_replay'; },
    data => { data.provenance.side = 'unknown'; },
    data => { data.provenance.signal_bar_close_ms += 1; },
    data => { data.candles = data.candles.map((row, i) => ({ ...row, is_closed: i !== 117 })); },
  ];
  cases.forEach((modify, i) => {
    const data = signalChart();
    modify(data);
    harness.api.showChart(data, 'invalid-' + i);
    assert.deepEqual(harness.markers(), []);
  });
});
