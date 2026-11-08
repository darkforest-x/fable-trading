/* Project the frozen breadth-EMA20 settlement ledger into strategy-book history shape. */
(function attach(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.SpikeBreadthHistory = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function createApi() {
  'use strict';

  const INITIAL_EQUITY = 1000;
  const SLEEVE_WEIGHT = 0.5;
  const DISTRIBUTION_LABELS = ['≤−30', '−30~−10', '−10~−3', '−3~−1', '−1~0', '0~1', '1~3', '3~10', '10~30', '≥30'];
  const HOLDING_LABELS = ['<1h', '1–4h', '4–12h', '12–24h', '1–3天', '≥3天'];
  const DISTRIBUTION_EDGES = [-30, -10, -3, -1, 0, 1, 3, 10, 30];
  const HOLDING_EDGES = [1, 4, 12, 24, 72];
  const closeEnough = (a, b) => Number.isFinite(a) && Number.isFinite(b) &&
    Math.abs(a - b) <= Math.max(1e-9, Math.abs(b) * 1e-9);
  const finite = value => typeof value === 'number' && Number.isFinite(value);

  function utcMillis(value, field) {
    if (typeof value !== 'string' || !value.endsWith('Z')) throw Error(`无效 UTC 时间：${field}`);
    const ms = Date.parse(value);
    if (!Number.isFinite(ms)) throw Error(`无效 UTC 时间：${field}`);
    return ms;
  }

  function utcText(ms) {
    return new Date(ms).toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, '+00:00').replace(/Z$/, '+00:00');
  }

  function monthKey(ms) {
    return new Date(ms).toISOString().slice(0, 7);
  }

  function monthStart(key) {
    const [year, month] = key.split('-').map(Number);
    return Date.UTC(year, month - 1, 1);
  }

  function shiftMonth(key, count) {
    const date = new Date(monthStart(key));
    date.setUTCMonth(date.getUTCMonth() + count);
    return date.toISOString().slice(0, 7);
  }

  function histogram(values, edges, labels) {
    const counts = Array(labels.length).fill(0);
    for (const value of values) {
      let index = 0;
      while (index < edges.length && value >= edges[index]) index++;
      counts[index]++;
    }
    return { labels: [...labels], values: counts, unit: '笔' };
  }

  function validateInputs(data, track, scope) {
    if (data?.schema_version !== 'breadth-ema20.v1' || data.strategy_id !== 'breadth-ema20') {
      throw Error('全市场下轨冻结账本版本不匹配');
    }
    if (typeof track !== 'string' || !track) throw Error('缺少策略标识');
    if (!['all_history_fee_only', '2026_actual_funded'].includes(scope)) throw Error('未知的全市场下轨账本区间');
    const funded = scope === '2026_actual_funded';
    const summary = data.summaries?.[scope];
    const curve = data.equity_curves?.[`${scope}_settlement`];
    if (!summary || !curve || !Array.isArray(curve.points) || !Array.isArray(data.events)) {
      throw Error('全市场下轨冻结账本缺少统计或结算曲线');
    }
    if (summary.funding_included !== funded || curve.marking !== 'realized exit settlements only; not intratrade 5-minute marks' ||
        curve.return_field !== (funded ? 'funded_pct' : 'net_pct')) {
      throw Error('全市场下轨账本的费用或资金费口径不匹配');
    }
    const sleeves = data.rules?.account?.sleeves;
    if (!closeEnough(sleeves?.BTC, SLEEVE_WEIGHT) || !closeEnough(sleeves?.ALT, SLEEVE_WEIGHT) ||
        data.rules.account.capital_transfers_between_sleeves !== false) {
      throw Error('全市场下轨组合不符合等额独立袖套口径');
    }
    const events = data.events.filter(row => !funded || row.entry_time?.slice(0, 4) === '2026');
    if (events.length !== summary.event_count || curve.points.length !== events.length + 1) {
      throw Error('全市场下轨事件数与冻结结算曲线不一致');
    }
    const seen = new Set();
    const ordered = events.map(row => {
      if (!row || typeof row.event_id !== 'string' || seen.has(row.event_id) || !['BTC', 'ALT'].includes(row.asset)) {
        throw Error('全市场下轨账本含重复或无效事件');
      }
      seen.add(row.event_id);
      const entryMs = utcMillis(row.entry_time, 'entry_time');
      const exitMs = utcMillis(row.exit_time, 'exit_time');
      if (exitMs < entryMs) throw Error('全市场下轨事件在入场前退出');
      const returnPct = row[curve.return_field];
      if (!finite(returnPct)) throw Error(`事件 ${row.event_id} 缺少 ${curve.return_field}`);
      return { source: row, entryMs, exitMs, returnPct };
    }).sort((a, b) => a.exitMs - b.exitMs || a.source.event_id.localeCompare(b.source.event_id));
    if (curve.points.length < 2 || !finite(curve.points[0]?.combined_equity) ||
        !closeEnough(curve.points[0].combined_equity, 1)) {
      throw Error('全市场下轨结算曲线缺少 1,000 USDT 起步点');
    }
    let previousTime = -Infinity;
    for (const point of curve.points) {
      const time = utcMillis(point?.time, 'equity_curve.time');
      if (time < previousTime || !['btc', 'alt', 'combined'].every(key => finite(point[`${key}_equity`]) && point[`${key}_equity`] > 0)) {
        throw Error('全市场下轨结算曲线顺序或净值无效');
      }
      if (!closeEnough(point.combined_equity, (point.btc_equity + point.alt_equity) / 2)) {
        throw Error('全市场下轨结算曲线的袖套合计不一致');
      }
      previousTime = time;
    }
    let btc = 1, alt = 1;
    for (let i = 0; i < ordered.length; i++) {
      const event = ordered[i];
      const point = curve.points[i + 1];
      if (event.exitMs !== utcMillis(point?.time, 'equity_curve.settlement_time')) {
        throw Error('事件退出时间与冻结结算曲线不一致');
      }
      if (event.source.asset === 'BTC') btc *= 1 + event.returnPct / 100;
      else alt *= 1 + event.returnPct / 100;
      if (!closeEnough(btc, point.btc_equity) || !closeEnough(alt, point.alt_equity)) {
        throw Error(`事件 ${event.source.event_id} 无法与冻结袖套净值对账`);
      }
    }
    if (!closeEnough(summary.growth_pct, (curve.points.at(-1).combined_equity - 1) * 100) ||
        !finite(summary.max_drawdown_pct)) {
      throw Error('全市场下轨摘要与冻结结算曲线不一致');
    }
    return { funded, summary, curve, events: ordered };
  }

  function createBreadthBookHistory(data, { track = 'breadth-ema20', scope = 'all_history_fee_only' } = {}) {
    const { funded, summary, curve, events } = validateInputs(data, track, scope);
    const settlementPoints = curve.points;
    let peak = 0;
    const points = settlementPoints.map(point => {
      const equity = INITIAL_EQUITY * point.combined_equity;
      peak = Math.max(peak, equity);
      const drawdownPct = peak ? 100 * (equity / peak - 1) : 0;
      return [utcMillis(point.time, 'equity_curve.time'), equity,
        100 * (equity / INITIAL_EQUITY - 1), drawdownPct, INITIAL_EQUITY];
    });
    const settlementMaxDrawdown = Math.max(0, ...points.map(point => -point[3]));
    const dailyRows = {};
    const tradeRows = events.map((event, index) => {
      const source = event.source;
      const portfolioPnl = INITIAL_EQUITY * (settlementPoints[index + 1].combined_equity - settlementPoints[index].combined_equity);
      const row = {
        eventId: source.event_id,
        symbol: source.asset === 'BTC' ? 'BTCUSDT' : 'ALT篮子',
        entry_ms: event.entryMs,
        exit_ms: event.exitMs,
        pnl: portfolioPnl,
        return_pct: event.returnPct,
        asset: source.asset,
        source: source.source,
      };
      const day = new Date(event.exitMs).toISOString().slice(0, 10);
      (dailyRows[day] ||= []).push(row);
      return row;
    });
    const winRows = tradeRows.filter(row => row.pnl > 0);
    const lossRows = tradeRows.filter(row => row.pnl < 0);
    const grossWins = winRows.reduce((sum, row) => sum + row.pnl, 0);
    const grossLosses = -lossRows.reduce((sum, row) => sum + row.pnl, 0);
    const profitFactorState = lossRows.length ? 'finite' : winRows.length ? 'no_losses' : 'empty';
    const startMs = points[0][0], endMs = points.at(-1)[0];
    const monthly = [];
    let balanceAtMonthStart = INITIAL_EQUITY, pointIndex = 1;
    for (let key = monthKey(startMs), finalMonth = monthKey(endMs); key <= finalMonth; key = shiftMonth(key, 1)) {
      const nextMonth = monthStart(shiftMonth(key, 1));
      let monthEndBalance = balanceAtMonthStart;
      while (pointIndex < points.length && points[pointIndex][0] < nextMonth) {
        monthEndBalance = points[pointIndex][1];
        pointIndex++;
      }
      monthly.push([key, balanceAtMonthStart > 0 ? 100 * (monthEndBalance / balanceAtMonthStart - 1) : null]);
      balanceAtMonthStart = monthEndBalance;
    }
    const yearly = [];
    for (const year of [...new Set(monthly.map(([key]) => key.slice(0, 4)))]) {
      const returns = monthly.filter(([key]) => key.startsWith(year)).map(([, value]) => value);
      yearly.push([year, returns.some(value => value == null) ? null :
        (returns.reduce((growth, value) => growth * (1 + value / 100), 1) - 1) * 100]);
    }
    const returns = tradeRows.map(row => row.return_pct);
    const durations = events.map(event => (event.exitMs - event.entryMs) / 3600000);
    const winningAverage = winRows.length ? grossWins / winRows.length : 0;
    const losingAverage = lossRows.length ? -grossLosses / lossRows.length : 0;
    const endEquity = points.at(-1)[1];
    const drawdownNote = `同步 5 分钟收盘盯市最大回撤 ${summary.max_drawdown_pct.toFixed(2)}%；结算曲线最大回撤 ${settlementMaxDrawdown.toFixed(2)}%，曲线不含持仓浮动盈亏。`;
    const scopeNote = funded
      ? '只统计 2026 年的入场，从 1,000 USDT 重新起算，扣除手续费并计入已记录的资金费。'
      : '全历史统一扣除手续费，不含资金费。';
    const meta = {
      track,
      start: new Date(startMs).toISOString().slice(0, 10),
      end: utcText(endMs),
      note: `${scopeNote}BTC 与山寨篮子各从 500 USDT 起步，分别复利。信号统计至 ${new Date(Date.parse(data.signal_cutoff_exclusive_utc)-1).toISOString().slice(0,10)}（UTC）；之后平仓的交易仍计入。${drawdownNote}`,
      sources: [data.provenance?.experiment_id || 'breadth-ema20 frozen ledger'],
      scope,
      funding_included: funded,
      settlement_marking: curve.marking,
      mark_to_market_drawdown_basis: summary.max_drawdown_basis,
      signal_cutoff_exclusive_utc: data.signal_cutoff_exclusive_utc,
    };
    const sides = [[
      '做多', tradeRows.length,
      tradeRows.length ? 100 * winRows.length / tradeRows.length : null,
      tradeRows.reduce((sum, row) => sum + row.pnl, 0),
    ]];
    const recent = [...tradeRows].reverse().slice(0, 50).map(row => [
      row.symbol, row.entry_ms, row.exit_ms, '多', row.pnl, row.return_pct,
    ]);
    const history = {
      schema: 1,
      meta,
      refilled: false,
      points,
      monthly,
      yearly,
      summary: {
        start: INITIAL_EQUITY,
        end: endEquity,
        pnl: endEquity - INITIAL_EQUITY,
        return_pct: 100 * (endEquity / INITIAL_EQUITY - 1),
        // The owner-facing headline uses the frozen synchronized 5-minute drawdown.
        // The plotted p[3] and settlement_max_drawdown_pct remain settlement-only.
        max_drawdown_pct: summary.max_drawdown_pct,
        mark_to_market_max_drawdown_pct: summary.max_drawdown_pct,
        settlement_max_drawdown_pct: settlementMaxDrawdown,
        max_drawdown_basis: summary.max_drawdown_basis,
        win_pct: tradeRows.length ? 100 * winRows.length / tradeRows.length : null,
        trades: tradeRows.length,
        profit_factor: grossLosses ? grossWins / grossLosses : null,
        profit_factor_state: profitFactorState,
        cash_added: 0,
        stake_budget: INITIAL_EQUITY,
        avg_win: winningAverage,
        avg_loss: losingAverage,
      },
      distribution: histogram(returns, DISTRIBUTION_EDGES, DISTRIBUTION_LABELS),
      holding: histogram(durations, HOLDING_EDGES, HOLDING_LABELS),
      sides,
      recent,
      dayRows: dailyRows,
      source_experiment: data.provenance?.experiment_id || null,
    };
    return history;
  }

  return { createBreadthBookHistory };
});
