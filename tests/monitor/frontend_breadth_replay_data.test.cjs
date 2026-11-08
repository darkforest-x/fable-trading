'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const historyAdapter = require('../../yoyo/monitor/static/breadth-book-history.js');
const adapter = require('../../yoyo/monitor/static/breadth-replay-data.js');
const backtest = require('../../yoyo/monitor/static/strategy-backtest.js');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, '../../yoyo/monitor/static/strategy-booklet/breadth-ema20-data.json'), 'utf8'));
const closeTo = (actual, expected, tolerance = 1e-8) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);

test('frozen event list is one row per event with time, branch and symbol filters plus paging', () => {
  const all = adapter.create(data);
  const first = all.page({ offset: 0, limit: 50, sort: 'time', direction: 'desc' });
  assert.equal(first.total, 398);
  assert.equal(first.track, 'breadth-ema20');
  assert.equal(first.sort, 'time');
  assert.equal(first.direction, 'desc');
  assert.equal(first.symbol, '');
  assert.equal(first.rows.length, 50);
  assert.equal(first.has_more, true);
  assert.ok(Number.isInteger(first.rows[0].id) && first.rows[0].id > 0);
  assert.match(first.rows[0].event_id, /^(BTC|ALT)\|/);
  assert.equal(first.rows[0].venue, 'Binance USD-M');
  assert.equal(first.rows[0].member_pnl, undefined);
  assert.equal(first.focus.event_id, first.rows[0].event_id);
  assert.equal(first.meta.track, 'breadth-ema20');
  assert.equal(first.meta.trades, 398);
  assert.equal(backtest.validate(first, { track: 'breadth-ema20', sort: 'time', direction: 'desc', symbol: '' }, 0), first);
  const history = historyAdapter.createBreadthBookHistory(data, { track: 'breadth-ema20', scope: 'all_history_fee_only' });
  const settledPnl = new Map(Object.values(history.dayRows).flat().map(row => [row.eventId, row.pnl]));
  closeTo(first.rows[0].pnl, settledPnl.get(first.rows[0].event_id));
  assert.equal(first.rows[0].fills.entry_start_ms, first.rows[0].entry_ms);
  assert.equal(first.rows[0].fills.exit_kind, 'point');
  assert.equal(first.rows[0].point_times, true);

  const year = all.page({ kind: 'ALT', period: '2026', limit: 100 });
  assert.equal(year.total, 25);
  assert.ok(year.rows.every(row => row.asset === 'ALT' && row.branch_label === '山寨篮子'));
  assert.ok(year.rows.every(row => row.symbol === row.member_symbols[0]));

  const chineseEvent = data.events.find(event => event.asset === 'ALT' &&
    event.members.some(member => member.symbol === '我踏马来了USDT'));
  assert.ok(chineseEvent);
  const chinese = all.page({ kind: 'ALT', symbol: '我踏马来了USDT', limit: 100 });
  assert.ok(chinese.total > 0);
  assert.ok(chinese.rows.every(row => row.symbol === '我踏马来了USDT'));
  assert.ok(backtest.valid(chinese.rows[0]));
  assert.equal(backtest.validate(chinese, { track: 'breadth-ema20', sort: 'time', direction: 'desc', symbol: '我踏马来了USDT' }, 0), chinese);
  assert.throws(() => all.page({ symbol: 'NOT-IN-FROZEN-LISTUSDT' }), /冻结成员名单/);
  assert.throws(() => all.page({ period: '2026-13' }), /YYYY/);
});

test('funded scope resolves the Sep 16 Beijing BTC event and keeps both native exit legs', () => {
  const replay = adapter.create(data, { scope: '2026_actual_funded' });
  assert.equal(replay.page({ limit: 100 }).total, 48);
  const event = replay.resolve('BTC|2026-09-15T19:20:00Z');
  assert.equal(event.branch, 'case');
  assert.equal(event.branch_label, 'BTC 急跌');
  assert.equal(event.entry_ms, Date.parse('2026-09-15T19:20:00Z'));
  assert.equal(event.fills.exit_legs.length, 2);
  assert.deepEqual(event.fills.exit_legs.map(leg => [leg.time_ms, leg.price, leg.size_fraction, leg.fill_type]), [
    [Date.parse('2026-09-21T15:05:00Z'), 86325, 0.5, 'limit_maker'],
    [Date.parse('2026-09-21T19:20:00Z'), 86280, 0.5, 'market_taker'],
  ]);
  assert.match(event.fills.exit_legs[0].label, /部分止盈.*限价.*50%/);
  assert.match(event.fills.exit_legs[1].label, /6天到期.*市价.*50%/);
  assert.ok(Number.isInteger(event.id) && event.id > 0);
  assert.equal(event.fills.exit_start_ms, Date.parse('2026-09-21T19:20:00Z'));
  assert.equal(event.fills.exit_price, 86280);
  assert.equal(event.return_pct, 13.143360854924676);
  assert.equal(event.member_return_pct, null);
  assert.equal(event.chart_request, undefined);
  assert.throws(() => replay.resolve('BTC|missing-event'), /找不到冻结事件/);
});

test('ALT selection remains a single basket row while exposing the selected member separately', () => {
  const replay = adapter.create(data, { scope: '2026_actual_funded' });
  const eventId = 'ALT|2026-09-15T14:45:00Z';
  const basket = replay.resolve(eventId);
  const member = replay.resolve(eventId, '1000FLOKIUSDT');
  assert.equal(basket.asset, 'ALT');
  assert.equal(basket.branch_label, '山寨篮子');
  assert.equal(basket.member_symbols.length, 36);
  assert.equal(basket.symbol, basket.member_symbols[0]);
  assert.equal(member.id, basket.id);
  assert.ok(Number.isInteger(member.id) && member.id > 0);
  assert.equal(member.symbol, '1000FLOKIUSDT');
  assert.equal(member.selected_member_symbol, '1000FLOKIUSDT');
  assert.equal(member.return_pct, -1.7427106335107392);
  assert.equal(member.member_return_pct, -1.9894087251297332);
  assert.notEqual(member.return_pct, member.member_return_pct);
  assert.equal(member.member_net_pct, -1.9882241813602117);
  assert.equal(member.member_funded_pct, -1.9894087251297332);
  assert.equal(member.member_pnl, undefined);
  assert.equal(member.fills.exit_legs.length, 1);
  assert.equal(member.fills.exit_legs[0].time_ms, Date.parse('2026-09-15T20:45:00Z'));
  assert.equal(member.fills.exit_legs[0].price, 0.02337);
  assert.equal(member.fills.exit_legs[0].fill_type, undefined);
  assert.equal(member.fills.exit_legs[0].size_fraction, undefined);
  assert.throws(() => replay.resolve(eventId, 'BTCUSDT'), /不属于这次冻结/);

  const chineseEvent = 'ALT|2026-09-23T14:15:00Z';
  const chinese = replay.resolve(chineseEvent, '我踏马来了USDT');
  assert.equal(chinese.symbol, '我踏马来了USDT');
  assert.equal(chinese.chart_request, undefined);
});

test('settlement and filter indexes are cached once per data and scope', () => {
  const isolatedData = JSON.parse(JSON.stringify(data));
  const original = historyAdapter.createBreadthBookHistory;
  let calls = 0;
  historyAdapter.createBreadthBookHistory = (...args) => {
    calls++;
    return original(...args);
  };
  try {
    const first = adapter.create(isolatedData);
    first.page({ kind: 'ALT', period: '2026', symbol: '1000FLOKIUSDT' });
    adapter.create(isolatedData, { period: '2026' }).page({ kind: 'case' });
    assert.equal(calls, 1);
    adapter.create(isolatedData, { scope: '2026_actual_funded' }).page();
    assert.equal(calls, 2);
  } finally {
    historyAdapter.createBreadthBookHistory = original;
  }
});

test('cancelled load can reopen and retry; successful ledger is cached', async () => {
  const modulePath = require.resolve('../../yoyo/monitor/static/breadth-replay-data.js');
  delete require.cache[modulePath];
  const freshAdapter = require(modulePath);
  const controller = new AbortController();
  let startFetch;
  const started = new Promise(resolve => { startFetch = resolve; });
  let calls = 0;
  const cancelled = freshAdapter.load({ signal: controller.signal, fetcher: async (url, options) => {
    calls++;
    assert.equal(url, '/static/strategy-booklet/breadth-ema20-data.json');
    startFetch();
    return new Promise((resolve, reject) => {
      options.signal.addEventListener('abort', () => {
        const error = Error('cancelled');
        error.name = 'AbortError';
        reject(error);
      }, { once: true });
    });
  } });
  await started;
  controller.abort();
  await assert.rejects(cancelled, error => error.name === 'AbortError');

  const loaded = await freshAdapter.load({ fetcher: async url => {
    calls++;
    assert.equal(url, '/static/strategy-booklet/breadth-ema20-data.json');
    return { ok: true, json: async () => data };
  } });
  assert.equal(loaded.strategy_id, 'breadth-ema20');
  const again = await freshAdapter.load({ fetcher: async () => { calls++; throw Error('should remain cached'); } });
  assert.equal(again, loaded);
  assert.equal(calls, 2);
  assert.deepEqual(Object.keys(freshAdapter).sort(), ['create', 'load']);
});
