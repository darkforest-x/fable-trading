/** Bounded anonymous data-source trial, independent of the production renderer.
 * Uses published v4 getCandles/watchCandles, fixed symbols and closed-bar OHLC
 * matched against the existing OKX display API. No account or trading calls.
 * Usage: node probe.mjs /absolute/npm-prefix /absolute/raw-output-directory
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
const root = path.dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(await fs.readFile(path.join(root, 'config.json'), 'utf8'));
const require = createRequire(path.join(path.resolve(process.argv[2]), 'package.json'));
const packageInfo = require('@mathieuc/tradingview/package.json');
if (packageInfo.version !== config.version) throw Error('Unexpected package version');
const { getCandles, watchCandles } = await import(pathToFileURL(require.resolve('@mathieuc/tradingview/data')));
const rawRoot = path.resolve(process.argv[3]);
await fs.mkdir(rawRoot, { recursive: true });
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const errorInfo = e => ({ code: e.code || e.name, message: String(e.message).slice(0, 250) });
const result = { started_at: new Date().toISOString(), node: process.version,
  source_commit: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
  package: { name: packageInfo.name, version: packageInfo.version },
  credentials_used: false, cases: [], raw_directory: rawRoot };

async function baseline(symbol, period) {
  const deadline = Date.now() + config.timeout_ms;
  while (Date.now() < deadline) {
    const response = await fetch(config.baseline + '?' + new URLSearchParams({ symbol, period }), { signal: AbortSignal.timeout(5000) });
    if (!response.ok) throw Error('Baseline HTTP ' + response.status);
    const data = await response.json();
    if (!data.loading && data.candles.length) return { ...data, candles: data.candles.slice(-160) };
    if (data.error && !data.loading) throw Error(data.error);
    await wait(1000);
  }
  throw Error('Baseline loading timeout');
}

function compare(candles, original, step) {
  const cutoff = Math.min(original.last_close_ms / 1000, Math.floor(Date.now() / 1000 / step) * step);
  const tv = candles.filter(c => c.time + step <= cutoff);
  const okx = new Map(original.candles.map(c => [c.time / 1000, c]));
  let matched = 0, exact = 0, maxAbs = 0, maxRelative = 0;
  const mismatches = [];
  for (const c of tv) {
    const b = okx.get(c.time); if (!b) continue;
    matched++;
    const changes = ['open', 'high', 'low', 'close'].filter(k => {
      const difference = Math.abs(c[k] - b[k]);
      maxAbs = Math.max(maxAbs, difference);
      maxRelative = Math.max(maxRelative, difference / Math.max(Math.abs(b[k]), 1e-12));
      return difference > Math.max(1e-9, Math.abs(b[k]) * 1e-10);
    });
    if (!changes.length) exact++;
    else if (mismatches.length < 3) mismatches.push({ time: c.time, changes, tv: c, okx: b });
  }
  const shifted = tv.map(c => okx.get(c.time - step)).filter(Boolean);
  const shiftedCloseMatches = tv.filter(c => { const b = okx.get(c.time - step); return b && Math.abs(c.close - b.close) <= Math.max(1e-9, Math.abs(b.close) * 1e-10); }).length;
  return { closed_tv: tv.length, matched, ohlc_equal_within_tolerance: exact,
    unmatched_tv_times: tv.length - matched, max_absolute_ohlc_difference: maxAbs,
    max_relative_ohlc_difference: maxRelative, mismatch_examples: mismatches,
    negative_control: { method: 'Shift OKX timestamps by one bar', matched: shifted.length, close_equal: shiftedCloseMatches } };
}

for (const [symbol, period, timeframe, step] of config.cases) {
  const ticker = 'OKX:' + symbol.replace('-SWAP', '').replaceAll('-', '') + '.P';
  const start = performance.now();
  const entry = { symbol, period, ticker, timeframe };
  const [tv, okx] = await Promise.allSettled([
    getCandles({ symbol: ticker, timeframe, count: config.count, timeoutMs: config.timeout_ms, signal: AbortSignal.timeout(config.timeout_ms + 2000) })
      .then(candles => ({ candles, initial_ms: Math.round(performance.now() - start) })),
    baseline(symbol, period)
  ]);
  if (tv.status === 'fulfilled') {
    Object.assign(entry, { tv_count: tv.value.candles.length, tv_initial_ms: tv.value.initial_ms, tv_last: tv.value.candles.at(-1) });
    if (okx.status === 'fulfilled') entry.comparison = compare(tv.value.candles, okx.value, step);
  } else entry.tv_error = errorInfo(tv.reason);
  if (okx.status === 'rejected') entry.okx_error = errorInfo(okx.reason);
  await fs.writeFile(path.join(rawRoot, symbol + '-' + period + '.json'), JSON.stringify({ tv: tv.status === 'fulfilled' ? tv.value : entry.tv_error, okx: okx.status === 'fulfilled' ? okx.value : entry.okx_error }) + '\n', { flag: 'wx' });
  result.cases.push(entry);
  console.log(JSON.stringify(entry));
}

const updates = [], watchErrors = [];
let watcher;
const began = performance.now();
try {
  watcher = await watchCandles({ symbol: 'OKX:BTCUSDT.P', timeframe: '1', count: 30,
    timeoutMs: config.timeout_ms, signal: AbortSignal.timeout(config.timeout_ms + config.watch_ms + 5000) }, {
    onData(candles) { updates.push({ received_ms: Date.now(), candle: candles.at(-1) }); },
    onError(e) { watchErrors.push(errorInfo(e)); }
  });
  result.live = { startup_ms: Math.round(performance.now() - began) };
  await wait(config.watch_ms);
} catch (e) { result.live = { error: errorInfo(e) }; }
finally { await watcher?.stop(); }
const changed = updates.filter((u, i) => i && u.candle.time === updates[i-1].candle.time && ['open', 'high', 'low', 'close', 'volume'].some(k => u.candle[k] !== updates[i-1].candle[k]));
Object.assign(result.live, { observation_ms: config.watch_ms, updates: updates.length, intrabar_changes: changed.length, errors: watchErrors, stopped: true });
await fs.writeFile(path.join(rawRoot, 'live.json'), JSON.stringify(updates) + '\n', { flag: 'wx' });
result.finished_at = new Date().toISOString();
await fs.writeFile(path.join(root, 'summary.json'), JSON.stringify(result, null, 2) + '\n', { flag: 'wx' });
console.log(JSON.stringify({ live: result.live, summary: path.join(root, 'summary.json') }));
