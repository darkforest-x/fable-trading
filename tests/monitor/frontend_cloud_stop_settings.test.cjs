const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const stops = require('../../yoyo/monitor/static/cloud-stop-settings.js');

const validRange = (symbol = 'BTC-USDT-SWAP') => ({
  state: 'ready', symbol, week: '2026-11-02', lower: 93.82, upper: 116.18,
});
const signal = (side = 1, symbol = 'BTC-USDT-SWAP') => ({
  id: 'signal-1', symbol, side, entry: side === 1 ? 93.82 : 116.18,
  stop: side === 1 ? 90 : 120, target: side === 1 ? 116.18 : 93.82,
  planned_rr: 1, plan_state: 'ready', plan_reason: '', week: '2026-11-02',
  range: validRange(symbol), ema20: 108, ema50: 103,
  provenance: 'backfill', eligible: true, raw: { id: 'raw-1' },
});

test('UMD export is available as a pure browser global', () => {
  const window = {};
  vm.runInNewContext(fs.readFileSync(require.resolve('../../yoyo/monitor/static/cloud-stop-settings.js'), 'utf8'), { window });
  assert.equal(window.SpikeCloudStopSettings.KEY, stops.KEY);
  assert.deepEqual(JSON.parse(JSON.stringify(window.SpikeCloudStopSettings.DEFAULTS)), { btcEth: 1.5, other: 3 });
});

test('default projection matches backend V5 stops for both sides and exact BTC/ETH bases', () => {
  for (const [symbol, fraction] of [
    ['BTC-USDT-SWAP', 0.015], ['ETH-USDT-SWAP', 0.015],
    ['BTCB-USDT-SWAP', 0.03], ['ETHW-USDT-SWAP', 0.03],
  ]) {
    for (const side of [1, -1]) {
      const source = signal(side, symbol);
      const result = stops.project(source, stops.DEFAULTS);
      assert.equal(result.stop_pct, fraction);
      assert.equal(result.stop, source.entry * (1 - side * fraction));
      assert.ok(Math.abs(result.planned_rr - ((source.target - source.entry) * side) / (source.entry * fraction)) < 1e-12);
      assert.equal(result.plan_state, 'ready');
      assert.equal(result.reference_policy, 'touched-side-1618-entry-symbol-fixed-stop-v5');
      assert.equal(source.stop, side === 1 ? 90 : 120);
    }
  }
});

test('custom percentages persist and reproject from entry rather than the prior stop', () => {
  let saved = null;
  const storage = {
    getItem(key) { assert.equal(key, stops.KEY); return saved; },
    setItem(key, value) { assert.equal(key, stops.KEY); saved = value; },
  };
  const chosen = { btcEth: '2.25', other: 4.5 };
  assert.equal(stops.write(storage, chosen), true);
  const settings = stops.read(storage);
  assert.deepEqual(settings, { btcEth: 2.25, other: 4.5 });
  const once = stops.project(signal(1), settings);
  const twice = stops.project(once, settings);
  assert.equal(once.stop, 93.82 * (1 - 0.0225));
  assert.equal(once.stop_pct, 0.0225);
  assert.equal(twice.stop, once.stop);
  assert.equal(twice.planned_rr, once.planned_rr);
  assert.equal(twice.stop_settings_source, 'browser-preference');
  assert.equal(twice.reference_policy, 'touched-side-1618-entry-configurable-fixed-stop-v1');
});

test('invalid settings values fall back per tier and invalid percentages are rejected', () => {
  for (const value of ['', '   ', 'NaN', 'Infinity', '1x', null, NaN, Infinity, -Infinity, 0.09, 50.01, true]) {
    assert.equal(stops.isValidPercentage(value), false, String(value));
  }
  assert.equal(stops.isValidPercentage(0.1), true);
  assert.equal(stops.isValidPercentage('50'), true);
  assert.deepEqual(stops.normalize({ btcEth: ' ', other: 4 }), { btcEth: 1.5, other: 4 });
  assert.deepEqual(stops.read({ getItem: () => '{broken' }), stops.DEFAULTS);
  assert.deepEqual(stops.read({ getItem: () => { throw Error('denied'); } }), stops.DEFAULTS);
  assert.equal(stops.write({ setItem: () => { throw Error('denied'); } }, { btcEth: 2, other: 3 }), false);
  assert.equal(stops.write(null, {}), false);
});

test('missing or invalid references remain invalid and do not gain planned R', () => {
  const missingRange = { ...signal(), range: undefined };
  const invalidPlan = stops.project(missingRange, { btcEth: 2, other: 4 });
  assert.equal(invalidPlan.stop, 93.82 * 0.98);
  assert.equal(invalidPlan.stop_pct, 0.02);
  assert.equal(invalidPlan.planned_rr, null);
  assert.equal(invalidPlan.plan_state, 'invalid');
  assert.match(invalidPlan.plan_reason, /区间/);

  const staleInvalid = stops.project({ ...signal(), plan_state: 'invalid', plan_reason: '原始范围不匹配' });
  assert.equal(staleInvalid.plan_state, 'invalid');
  assert.equal(staleInvalid.planned_rr, null);
  assert.equal(staleInvalid.plan_reason, '原始范围不匹配');
});

test('projection preserves signal identity, source metadata, and original reference', () => {
  const source = signal(-1, 'SOL-USDT-SWAP');
  source.original_reference = { entry: 115, stop: 121, target: 94, planned_rr: 3, basis: 'saved', tag: 'kept' };
  const before = JSON.stringify(source);
  const projected = stops.project(source, { btcEth: 2, other: 4 });
  assert.equal(JSON.stringify(source), before);
  assert.equal(projected.id, source.id);
  assert.equal(projected.symbol, source.symbol);
  assert.equal(projected.ema20, source.ema20);
  assert.equal(projected.ema50, source.ema50);
  assert.equal(projected.provenance, source.provenance);
  assert.equal(projected.eligible, source.eligible);
  assert.deepEqual(projected.raw, source.raw);
  assert.deepEqual(projected.original_reference, source.original_reference);
  assert.notEqual(projected.original_reference, source.original_reference);
  assert.equal(projected.entry, source.entry);
  assert.equal(projected.stop, source.entry * 1.04);
  assert.ok(Math.abs(projected.planned_rr - ((source.target - source.entry) * source.side) / (source.entry * 0.04)) < 1e-12);
});
