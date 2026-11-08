'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const adapter = require('../../yoyo/monitor/static/breadth-book-history.js');
const book = require('../../yoyo/monitor/static/reclaim-strategy-book.js');

const data = JSON.parse(fs.readFileSync(path.join(__dirname, '../../yoyo/monitor/static/strategy-booklet/breadth-ema20-data.json'), 'utf8'));
const create = scope => adapter.createBreadthBookHistory(data, { track: 'breadth-ema20', scope });
const closeTo = (actual, expected, tolerance = 1e-7) => assert.ok(Math.abs(actual - expected) <= tolerance, `${actual} != ${expected}`);

test('frozen all-history ledger projects into the existing settled-history contract', () => {
  const h = create('all_history_fee_only');
  assert.equal(globalThis.SpikeBreadthHistory, adapter);
  assert.equal(book.validateHistory(h, 'breadth-ema20'), h);
  assert.equal(h.schema, 1);
  assert.equal(h.refilled, false);
  assert.equal(h.points.length, 399);
  assert.deepEqual(h.points[0], [Date.parse('2020-01-01T00:00:00Z'), 1000, 0, 0, 1000]);
  closeTo(h.summary.end, 5665.972646107157);
  closeTo(h.summary.return_pct, data.summaries.all_history_fee_only.growth_pct);
  assert.equal(h.summary.trades, 398);
  assert.equal(h.summary.max_drawdown_pct, data.summaries.all_history_fee_only.max_drawdown_pct);
  closeTo(h.summary.settlement_max_drawdown_pct, Math.max(...h.points.map(point => -point[3])));
  assert.notEqual(h.summary.settlement_max_drawdown_pct, h.summary.max_drawdown_pct);
  assert.match(h.meta.note, /全历史统一扣除手续费/);
  assert.match(h.meta.note, /不含资金费/);
  assert.match(h.meta.note, /同步 5 分钟收盘盯市最大回撤/);
  assert.equal(h.meta.funding_included, false);
  assert.equal(h.distribution.values.reduce((sum, value) => sum + value, 0), 398);
  assert.equal(h.holding.values.reduce((sum, value) => sum + value, 0), 398);
  assert.equal(h.sides.length, 1);
  assert.equal(h.sides[0][0], '做多');
  assert.equal(h.sides[0][1], 398);
  assert.equal(h.recent.length, 50);

  const chart = book.equityOptions(h);
  assert.equal(chart.series[0].data.length, h.points.length);
  assert.deepEqual(chart.series[0].data[0], [h.points[0][0], h.points[0][1]]);
  assert.match(book.historyHTML(h), /2020-01-01/);
});

test('2026 funded projection uses only funded returns and retains its own 1,000 USDT restart', () => {
  const h = create('2026_actual_funded');
  assert.equal(book.validateHistory(h, 'breadth-ema20'), h);
  assert.equal(h.points.length, 49);
  assert.equal(h.summary.trades, 48);
  closeTo(h.points[0][1], 1000);
  closeTo(h.summary.end, 1420.2771557613758);
  closeTo(h.summary.return_pct, 42.027715577302);
  assert.equal(h.summary.max_drawdown_pct, data.summaries['2026_actual_funded'].max_drawdown_pct);
  assert.equal(h.meta.funding_included, true);
  assert.match(h.meta.note, /只统计 2026 年的入场/);
  assert.match(h.meta.note, /计入已记录的资金费/);
  assert.equal(h.monthly.length, 10);
  assert.equal(h.yearly.length, 1);
  assert.equal(Object.values(h.dayRows).flat().length, 48);

  const sourceEvent = data.events.find(row => row.entry_time.startsWith('2026-') && row.funded_pct !== row.net_pct);
  const projected = Object.values(h.dayRows).flat().find(row => row.eventId === sourceEvent.event_id);
  assert.equal(projected.return_pct, sourceEvent.funded_pct);
  assert.notEqual(projected.return_pct, sourceEvent.net_pct);
  closeTo(Object.values(h.dayRows).flat().reduce((sum, row) => sum + row.pnl, 0), h.summary.pnl);
});

test('calendar-month and event-day projections reconcile to settlement points in UTC', () => {
  const h = create('all_history_fee_only');
  assert.equal(h.monthly.length, 82);
  assert.equal(h.yearly.length, 7);
  closeTo(h.monthly.reduce((growth, [, value]) => growth * (1 + value / 100), 1), h.summary.end / 1000);

  for (const month of ['2020-01', '2020-02', '2025-10', '2026-10']) {
    const calendar = book.monthCalendar(h, month);
    assert.ok(calendar, `expected ${month} in monthly history`);
    for (const day of calendar.days.filter(row => row.covered)) {
      const rows = h.dayRows[day.date] || [];
      const ledgerPnl = rows.reduce((sum, row) => sum + row.pnl, 0);
      closeTo(day.pnl, ledgerPnl);
      for (const row of rows) assert.equal(new Date(row.exit_ms).toISOString().slice(0, 10), day.date);
    }
  }

  const January = book.monthCalendar(h, '2020-01');
  assert.ok(h.dayRows['2020-01-30']?.length);
  assert.equal(January.days.find(day => day.date === '2020-01-30').changed, true);
  const monthGrowth = h.monthly.find(([month]) => month === '2020-02')[1];
  assert.ok(Number.isFinite(monthGrowth));
  assert.deepEqual(h.yearly.map(([year]) => year), ['2020', '2021', '2022', '2023', '2024', '2025', '2026']);
});

test('adapter fails closed on an unknown scope or a ledger that no longer reconciles', () => {
  assert.throws(() => adapter.createBreadthBookHistory(data, { track: 'breadth-ema20', scope: 'unknown' }), /未知/);
  const changed = JSON.parse(JSON.stringify(data));
  changed.equity_curves.all_history_fee_only_settlement.points[1].combined_equity += 0.01;
  assert.throws(() => adapter.createBreadthBookHistory(changed, { track: 'breadth-ema20' }), /曲线|对账/);
  assert.throws(() => adapter.createBreadthBookHistory(data, { track: '' }), /策略标识/);
});
