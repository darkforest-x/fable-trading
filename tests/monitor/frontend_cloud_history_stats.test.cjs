const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {summarize, DEFAULT_FEES} = require('../../yoyo/monitor/static/cloud-history-stats.js');

function plan(overrides = {}) {
  return {
    id: 'signal-1', symbol: 'BTC-USDT-SWAP', week: '2026-11-02',
    confirmed_ms: Date.parse('2026-11-03T12:00:00Z'), side: 1,
    entry: 100, stop: 90, plan_state: 'ready', ...overrides
  };
}

function closed(exitR, exitPrice, overrides = {}) {
  return {state: 'stopped', exit_state: 'stop', exit_r: exitR,
    exit_price: exitPrice, exit_bar_confirmed: true, lowerR: 2, upperR: 8, ...overrides};
}

test('confirmed long outcomes subtract maker-entry and taker-exit fees in stop-risk units', () => {
  const result = summarize([plan()], {pathFor: () => closed(1.5, 115)});
  const feesR = (100 * DEFAULT_FEES.maker + 115 * DEFAULT_FEES.taker) / 10;
  assert.equal(result.closed, 1);
  assert.equal(result.grossR, 1.5);
  assert.ok(Math.abs(result.feesR - feesR) < 1e-12);
  assert.ok(Math.abs(result.netR - (1.5 - feesR)) < 1e-12);
  assert.equal(result.wins, 1);
  assert.equal(result.winRate, 1);
  assert.equal(result.averageR, result.netR);
  assert.deepEqual(result.fees, {entry: {role: 'maker', rate: 0.0002}, exit: {role: 'taker', rate: 0.0005}});
  assert.match(result.feeMeaning, /not actual fills/);
});

test('short-side costs use positive prices and the absolute stop-risk distance', () => {
  const short = plan({id: 'short', symbol: 'ETH-USDT-SWAP', side: -1, entry: 100, stop: 110});
  const result = summarize([short], {pathFor: () => closed(2, 80, {state: 'cloud_flipped', exit_state: 'cloud_flip'})});
  assert.ok(Math.abs(result.feesR - ((100 * 0.0002 + 80 * 0.0005) / 10)) < 1e-12);
  assert.ok(Math.abs(result.netR - (2 - result.feesR)) < 1e-12);
  assert.equal(result.bySymbol['ETH-USDT-SWAP'].grossR, 2);
});

test('a confirmed opening-gap stop can lose more than one R and remains a loss', () => {
  const result = summarize([plan()], {pathFor: () => closed(-2.2, 78, {exit_at_open: true})});
  assert.equal(result.grossR, -2.2);
  assert.ok(result.netR < -2.2);
  assert.equal(result.losses, 1);
  assert.equal(result.references[0].exit_price, 78);
});

test('one earliest reference per symbol and NY week is counted without mutating inputs', () => {
  const first = plan({id: 'first', confirmed_ms: 10, week: '2026-11-02'});
  const laterSameWeek = plan({id: 'later', confirmed_ms: 20, week: '2026-11-02'});
  const otherSymbol = plan({id: 'other-symbol', symbol: 'ETH-USDT-SWAP', confirmed_ms: 30});
  const otherWeek = plan({id: 'other-week', week: '2026-11-09', confirmed_ms: 40});
  const input = [laterSameWeek, otherWeek, otherSymbol, first];
  const before = JSON.stringify(input);
  const seen = [];
  const result = summarize(input, {pathFor: item => { seen.push(item.id); return closed(1, 110); }});
  assert.equal(result.closed, 3);
  assert.deepEqual(seen.sort(), ['first', 'other-symbol', 'other-week']);
  assert.equal(JSON.stringify(input), before);
  assert.equal(Object.keys(result.bySymbol).length, 2);
  assert.equal(result.firstConfirmationMs, 10);
  assert.equal(result.lastConfirmationMs, 40);
});

test('week fallback follows New York Monday across UTC midnight', () => {
  const beforeNyMonday = plan({id: 'sun-night', week: undefined, range: undefined,
    signal_open_ms: Date.parse('2026-11-02T03:30:00Z'), confirmed_ms: 1});
  const afterNyMonday = plan({id: 'mon-after-midnight', week: undefined, range: undefined,
    signal_open_ms: Date.parse('2026-11-02T05:30:00Z'), confirmed_ms: 2});
  const result = summarize([beforeNyMonday, afterNyMonday], {pathFor: () => closed(1, 110)});
  assert.equal(result.closed, 2);
  assert.deepEqual(result.references.map(row => row.week), ['2026-10-26', '2026-11-02']);
});

test('forming exits are pending, open paths stay open, and gaps are excluded', () => {
  const rows = [
    plan({id: 'forming'}),
    plan({id: 'open', week: '2026-11-09', confirmed_ms: 2}),
    plan({id: 'gap', week: '2026-11-16', confirmed_ms: 3}),
    plan({id: 'unavailable', week: '2026-11-23', confirmed_ms: 4})
  ];
  const paths = new Map([
    ['forming', {state: 'cloud_flipped', exit_state: 'cloud_flip', exit_r: 5,
      exit_price: 150, exit_bar_confirmed: false, lowerR: 0, upperR: 9}],
    ['open', {state: 'open', exit_state: null, lowerR: 2, upperR: 10}],
    ['gap', {state: 'gap', exit_state: null, lowerR: 1, upperR: 100}],
    ['unavailable', null]
  ]);
  const result = summarize(rows, {pathFor: item => paths.get(item.id)});
  assert.equal(result.closed, 0);
  assert.equal(result.pending, 1);
  assert.equal(result.open, 1);
  assert.equal(result.gap, 1);
  assert.equal(result.unavailable, 1);
  assert.equal(result.grossR, null);
  assert.equal(result.netR, null);
  assert.equal(result.feesR, null);
  assert.equal(result.averageR, null);
  assert.equal(result.winRate, null);
});

test('unsupported exits and invalid risk are excluded instead of treated as zero returns', () => {
  const rows = [plan({id: 'target'}), plan({id: 'bad-stop', week: '2026-11-09', confirmed_ms: 2, stop: 100})];
  const result = summarize(rows, {pathFor: () => ({state: 'stopped', exit_state: 'target',
    exit_r: 20, exit_price: 120, exit_bar_confirmed: true})});
  assert.equal(result.closed, 0);
  assert.equal(result.unsupported, 1);
  assert.equal(result.invalid, 1);
  assert.equal(result.netR, null);
  assert.equal(result.winRate, null);
});

test('pending and gap references do not enter the closed-trade win-rate denominator', () => {
  const rows = [plan({id: 'winner'}), plan({id: 'pending', week: '2026-11-09', confirmed_ms: 2}),
    plan({id: 'gap', week: '2026-11-16', confirmed_ms: 3})];
  const result = summarize(rows, {pathFor: item => item.id === 'winner' ? closed(0.5, 105) :
    item.id === 'pending' ? {state: 'stopped', exit_state: 'stop', exit_r: -1, exit_price: 90, exit_bar_confirmed: false} :
      {state: 'gap', exit_state: null}});
  assert.equal(result.closed, 1);
  assert.equal(result.wins, 1);
  assert.equal(result.pending, 1);
  assert.equal(result.gap, 1);
  assert.equal(result.winRate, 1);
  assert.equal(result.references.length, 3);
});

test('the callback receives the current custom-stop plan and the result uses its recomputed path', () => {
  const custom = plan({stop: 95});
  const original = JSON.stringify(custom);
  let receivedStop = null;
  const result = summarize([custom], {pathFor: item => {
    receivedStop = item.stop;
    const risk = item.entry - item.stop;
    item.stop = 1; // Callback only receives a copy.
    return closed((110 - item.entry) / risk, 110);
  }});
  assert.equal(receivedStop, 95);
  assert.equal(JSON.stringify(custom), original);
  assert.equal(result.grossR, 2);
  assert.ok(Math.abs(result.feesR - ((100 * 0.0002 + 110 * 0.0005) / 5)) < 1e-12);
});

test('same-bar MFE ambiguity never changes the confirmed exit aggregate', () => {
  const source = [plan()];
  const lowMfe = summarize(source, {pathFor: () => closed(0.75, 107.5, {lowerR: 0, upperR: 0.5, ambiguous: true})});
  const highMfe = summarize(source, {pathFor: () => closed(0.75, 107.5, {lowerR: 0, upperR: 50, ambiguous: true})});
  for (const key of ['grossR', 'netR', 'feesR', 'closed', 'wins', 'losses', 'winRate', 'averageR']) {
    assert.equal(lowMfe[key], highMfe[key], key);
  }
});

test('custom fees, coverage metadata, and browser export are explicit', () => {
  const coverage = {weeks: ['2026-11-02'], complete: false};
  const result = summarize([plan()], {pathFor: () => closed(1, 110), coverage,
    fees: {maker: 0.001, taker: 0.002}});
  assert.deepEqual(result.coverage, coverage);
  assert.match(result.coverageScope, /not assumed to include all history/);
  assert.equal(result.fees.entry.rate, 0.001);
  assert.equal(result.fees.exit.rate, 0.002);
  assert.ok(result.feesR > 0);
  const window = {};
  vm.runInNewContext(fs.readFileSync(require.resolve('../../yoyo/monitor/static/cloud-history-stats.js'), 'utf8'), {window, Intl, Date, Object, Number, Map, Set, TypeError});
  assert.equal(typeof window.SpikeCloudHistoryStats.summarize, 'function');
});
