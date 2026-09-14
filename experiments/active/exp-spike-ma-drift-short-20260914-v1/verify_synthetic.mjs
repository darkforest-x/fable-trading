// Execute the shipped Pine text on deterministic synthetic OHLC only.
// PineTS is an auxiliary runtime, not TradingView's compiler or market evidence.
// API: https://docs.luxalgo.com/developers/pinets/initialization-and-usage
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '../../..');
const runtimeRoot = process.env.SPIKE_PINETS_RUNTIME || '/tmp/spike-ma-drift-runtime-20260914';
const require = createRequire(path.join(runtimeRoot, 'package.json'));
const { PineTS } = require('pinets');
const sourcePath = path.join(root, 'yoyo/evaluation/pine/spike_ma_drift_short_v1.pine');
const source = await fs.readFile(sourcePath, 'utf8');
const sha = value => crypto.createHash('sha256').update(value).digest('hex');
// Any unexpected external data access is a failing test, never a fallback.
globalThis.fetch = async () => { throw new Error('Network forbidden in synthetic verification'); };
const checks = [];
function check(name, fn) { fn(); checks.push(name); }

function candles(closes, minutes = 1) {
  const duration = minutes * 60_000;
  return closes.map((close, i) => ({
    openTime: Date.UTC(2025, 0, 1) + i * duration,
    closeTime: Date.UTC(2025, 0, 1) + (i + 1) * duration,
    open: i ? closes[i - 1] : close,
    high: Math.max(i ? closes[i - 1] : close, close) + 0.2,
    low: Math.min(i ? closes[i - 1] : close, close) - 0.2,
    close, volume: 100,
  }));
}
async function execute(rows, code = source) {
  return new PineTS(rows, 'SYNTHETIC', '1').run(code);
}
function series(context, name) {
  assert.ok(context.plots[name], `Missing diagnostic plot ${name}`);
  return context.plots[name].data.map(row => row.value);
}
function events(context, name) {
  return series(context, name).flatMap((value, i) => value === 1 ? [i] : []);
}

const flat = candles(Array(440).fill(100));
const negative = await execute(flat);
check('flat negative control has zero warnings and confirmations', () => {
  assert.deepEqual(events(negative, '预警诊断'), []);
  assert.deepEqual(events(negative, '确认诊断'), []);
});
const closes = [...Array(400).fill(100), ...Array.from({length: 12}, (_, i) => 100 - (i + 1) * 0.04), 97];
const rows = candles(closes);
const full = await execute(rows);
const warnings = events(full, '预警诊断');
const confirmations = events(full, '确认诊断');
check('slow drift produces warning before the synthetic impulse', () => {
  assert.ok(warnings.length > 0, 'Positive fixture must actually exercise a warning');
  assert.ok(warnings[0] >= 400 && warnings[0] < rows.length - 1);
});
check('confirmation follows an earlier warning, never the same bar', () => {
  assert.ok(confirmations.length > 0, 'Positive fixture must actually confirm');
  for (const confirmation of confirmations) assert.ok(warnings.some(w => w < confirmation));
  for (const confirmation of confirmations) assert.ok(!warnings.includes(confirmation));
});
const truncated = await execute(rows.slice(0, warnings[0] + 2));
check('future impulse cannot change previously closed events', () => {
  for (const name of ['预警诊断', '确认诊断']) {
    assert.deepEqual(series(full, name).slice(0, warnings[0] + 2), series(truncated, name));
  }
});
const futureChanged = rows.map((row, i) => i < warnings[0] + 2 ? row : {
  ...row, open: 1000 + i, high: 1100 + i, low: 900 + i, close: 1000 + i,
});
const mutated = await execute(futureChanged);
check('adversarial future replacement leaves the decision prefix intact', () => {
  for (const name of ['预警诊断', '确认诊断']) {
    assert.deepEqual(series(full, name).slice(0, warnings[0] + 2), series(mutated, name).slice(0, warnings[0] + 2));
  }
});
const gapRows = rows.map((row, i) => i < 402 ? row : {...row, openTime: row.openTime + 60_000, closeTime: row.closeTime + 60_000});
const gap = await execute(gapRows);
check('missing bar resets warmup and prevents evidence crossing the gap', () => {
  assert.deepEqual(events(gap, '预警诊断'), []);
  assert.deepEqual(events(gap, '确认诊断'), []);
});
const threeMinute = await execute(candles(closes, 3));
check('same synthetic bar geometry at 3m has the same bar-based events', () => {
  assert.deepEqual(events(threeMinute, '预警诊断'), warnings);
  assert.deepEqual(events(threeMinute, '确认诊断'), confirmations);
});

const helper = source.split('// BEGIN PURE STEP HELPER')[1].split('// END PURE STEP HELPER')[0];
const probe = `//@version=6
indicator("MA drift synthetic state assertions")
${helper}
f_assert(bool ok, string name) =>
    if not ok
        runtime.error(name)
    true
if barstate.isfirst
    [p0, b0, l0, e0, w0, c0, x0, t0] = f_step(false, na, na, na, true, true, false, true, 99.0, 400, 8, 12)
    f_assert(p0 and w0 and not c0 and l0 == 99.0 and b0 == 400, "arm-only on setup bar")
    [p1, b1, l1, e1, w1, c1, x1, t1] = f_step(true, 400, 99.0, na, true, true, false, true, 95.0, 400, 8, 12)
    f_assert(p1 and not c1 and not w1 and l1 == 99.0, "same-bar breakout rejected; low frozen")
    [p2, b2, l2, e2, w2, c2, x2, t2] = f_step(true, 400, 99.0, na, true, true, false, false, 98.0, 401, 8, 12)
    f_assert(p2 and l2 == 99.0 and not w2 and not c2, "ongoing setup cannot trail or rearm low")
    [p3, b3, l3, e3, w3, c3, x3, t3] = f_step(true, 400, 99.0, na, true, true, false, true, 95.0, 408, 8, 12)
    f_assert(not p3 and c3 and not w3 and e3 == 408, "last allowed bar can confirm")
    [p4, b4, l4, e4, w4, c4, x4, t4] = f_step(true, 400, 99.0, na, true, true, false, true, 95.0, 409, 8, 12)
    f_assert(not p4 and t4 and not c4 and not w4, "expiry beats late breakout")
    [p5, b5, l5, e5, w5, c5, x5, t5] = f_step(true, 400, 99.0, na, true, true, true, true, 95.0, 401, 8, 12)
    f_assert(not p5 and x5 and not c5 and not w5, "reclaim cancels before confirmation")
    [p6, b6, l6, e6, w6, c6, x6, t6] = f_step(true, 400, 99.0, na, false, true, false, true, 95.0, 401, 8, 12)
    f_assert(not p6 and na(l6) and na(b6) and not c6 and not w6, "invalid input fails closed")
    [p7, b7, l7, e7, w7, c7, x7, t7] = f_step(false, na, na, 408, true, true, false, false, 95.0, 420, 8, 12)
    f_assert(not p7 and not w7, "cooldown includes twelve completed following bars")
    [p8, b8, l8, e8, w8, c8, x8, t8] = f_step(false, na, na, 408, true, true, false, false, 95.0, 421, 8, 12)
    f_assert(p8 and w8 and not c8, "first bar after cooldown can rearm")
plot(1, "probe_pass")
`;
const state = await execute(flat.slice(0, 3), probe);
check('nine exact-source state-helper assertions pass', () => assert.equal(series(state, 'probe_pass').at(-1), 1));
const out = {
  generated_at: new Date().toISOString(), source_sha256: sha(source), helper_sha256: sha(helper),
  verifier_sha256: sha(await fs.readFile(fileURLToPath(import.meta.url))),
  runtime: 'PineTS 0.9.33 (auxiliary; not native TradingView)',
  market_data_read: false, holdout_scoring: false, synthetic_rows: rows.length,
  warning_bars: warnings, confirmation_bars: confirmations, checks,
  native_compile: 'recorded separately; not established by this runner',
};
await fs.mkdir(path.join(here, 'results'), {recursive: true});
await fs.writeFile(path.join(here, 'results/verification.json'), JSON.stringify(out, null, 2) + '\n');
console.log(JSON.stringify(out, null, 2));
