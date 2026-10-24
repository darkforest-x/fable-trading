"""Ledger validation and descriptive analysis for the fixed SMA50 retest.

No price path is replayed. Native entries/exits/commissions are reconciled
against saved fills, then existing Monday statistics and market-week clustered
inference are reused. Golden spot results stay gross and never mix with crypto
net results. The later time segment is already researched, not an untouched
holdout. Results are independent-trade R and notional returns, not an account.
"""
from __future__ import annotations

import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_ma50_retest import EXP
from yoyo.evaluation.monday_ma6_analysis import read, group_name, stats
from yoyo.evaluation.monday_ma6_retest import finite
from yoyo.evaluation.range_framework import dump

CUT = pd.Timestamp('2023-01-01', tz='UTC')


def validate(folder):
    """Check native accounting and the market-entry/limit-target fee classes."""
    done = json.loads((folder / 'done.json').read_text())
    trades, fills = read(folder / 'trades.csv.gz'), read(folder / 'fills.csv.gz')
    entries = read(folder / 'entries.csv.gz')
    invalid = read(folder / 'invalid.csv.gz')
    assert len(trades) == done['positions']
    assert len(fills) == done['fills'] == 2 * len(trades)
    assert len(entries) == len(trades) + len(invalid) == done['entries']
    if trades.empty:
        return dict(symbol=done['symbol'], positions=0, fills=0, passed=True)
    assert trades.trade_id.is_unique
    assert set(trades.trade_id) == set(fills.trade_id)
    groups = fills.groupby('trade_id', sort=False)
    assert groups.size().eq(2).all()
    opening = fills[fills.phase.eq('entry')].set_index('trade_id')
    closing = fills[~fills.phase.eq('entry')].set_index('trade_id')
    assert opening.index.is_unique and closing.index.is_unique
    for col in ['price', 'quantity']:
        source_col = 'entry' if col == 'price' else col
        np.testing.assert_allclose(trades[source_col], trades.trade_id.map(opening[col]), atol=1e-10)
    np.testing.assert_allclose(trades.exit_price, trades.trade_id.map(closing.price), atol=1e-10)
    np.testing.assert_allclose(trades.quantity, trades.trade_id.map(closing.quantity))
    np.testing.assert_allclose(trades.gross_r, trades.side * (trades.exit_price - trades.entry) / trades.risk, atol=1e-8)
    gold = done['symbol'] == 'XAUUSD'
    commission = fills.price * fills.quantity * np.where(fills.phase.eq('target'), .0002, .0005)
    np.testing.assert_allclose(fills.commission, 0 if gold else commission, atol=5.1e-9, rtol=1e-10)
    if gold:
        assert trades.net_r.isna().all() and trades.fee_r.isna().all()
        np.testing.assert_allclose(trades.return_r, trades.gross_r, atol=1e-8)
    else:
        cost = trades.trade_id.map(groups.commission.sum()) / trades.quantity / trades.risk
        np.testing.assert_allclose(trades.fee_r, cost, atol=1e-8)
        np.testing.assert_allclose(trades.return_r, trades.gross_r - cost, atol=1e-8)
    assert set(trades.reason).issubset({0, 1, 4})
    assert trades.closed.eq(trades.reason.ne(0)).all()
    np.testing.assert_allclose(trades.return_pct, trades.return_r * trades.risk / trades.entry * 100, atol=1e-8)
    assert trades.entry_i.gt(trades.touch_i).all()
    # Candle-derived decisions precede or coincide with the synthetic close fill.
    assert pd.to_datetime(trades.entry_time, utc=True).eq(pd.to_datetime(trades.entry_interval_start, utc=True)).all()
    assert opening.bar_i.eq(opening.index.map(trades.set_index('trade_id').entry_i)).all()
    return dict(symbol=done['symbol'], positions=len(trades), fills=len(fills), passed=True)


def enrich(trades):
    x = trades.copy()
    for col in ['entry_time', 'exit_interval_end']:
        x[col] = pd.to_datetime(x[col], utc=True, format='mixed')
    x['closed'] = x.reason.ne(0)
    local = x.entry_time.dt.tz_convert('America/New_York')
    x['actual_entry_week'] = (local.dt.tz_localize(None).dt.normalize() - pd.to_timedelta(local.dt.dayofweek, unit='D')).dt.strftime('%Y-%m-%d')
    x['period'] = np.where(x.entry_time < CUT, 'earlier', 'later')
    x['cross_cut'] = x.entry_time.lt(CUT) & x.exit_interval_end.ge(CUT)
    x['asset_group'] = x.symbol.map(group_name)
    x['year'] = x.entry_time.dt.year
    x['month'] = x.entry_time.dt.strftime('%Y-%m')
    x['sl_legs'] = x.reason.eq(1).astype(int)
    x['tp_legs'] = x.reason.eq(4).astype(int)
    x['target_unreachable'] = False
    x['interior_fee_positive_mfe'] = x.interior_fee_positive_mfe.astype(str).str.lower().isin(['true', '1', '1.0'])
    x['entry_time_beijing'] = x.entry_time.dt.tz_convert('Asia/Shanghai').astype(str)
    x['exit_time_beijing'] = x.exit_interval_end.dt.tz_convert('Asia/Shanghai').astype(str)
    x['outcome'] = np.where(~x.closed, 'censored', np.where(x.return_r.gt(0), 'win', np.where(x.return_r.lt(0), 'loss', 'flat')))
    return x


def summarize(group, inference=True, basis=None):
    out = stats(group, with_inference=inference, basis=basis)
    out['inference'] = 'Market-week bootstrap95%; paired two-sided clustered sign-flip p; one predeclared setup, no parameter selection'
    signal = group[group.population.eq('signal')]
    out['missing_complete_control_groups_n'] = len(signal) - out['fully_matched_signal_n']
    out['control_comparison_scope'] = 'Closed complete1signal+3controls only; conditional on observable matched outcomes, not all opportunities'
    if out['basis'] == 'gross':
        for key in ['fee_positive_excursion_then_sl_n', 'losing_fee_positive_then_sl_n']:
            out[key] = None
    return finite(out)


def analyze(partial=False):
    folders = sorted(p.parent for p in (EXP / 'symbols').glob('*/done.json'))
    if not partial and len(folders) != 633:
        raise RuntimeError(f'Full analysis requires633 successes; currently{len(folders)}')
    if not folders:
        raise RuntimeError('No completed native results')
    dest = EXP / ('analysis-partial' if partial else 'analysis')
    dest.mkdir(parents=True, exist_ok=True)
    validations = [validate(folder) for folder in folders]
    tables = [read(folder / 'trades.csv.gz') for folder in folders]
    nonempty = [table for table in tables if not table.empty]
    opportunities = pd.concat([read(folder / 'opportunities.csv.gz') for folder in folders], ignore_index=True)
    entries = pd.concat([read(folder / 'entries.csv.gz') for folder in folders], ignore_index=True)
    metrics, breakdown = [], []
    trades = enrich(pd.concat(nonempty, ignore_index=True)) if nonempty else pd.DataFrame()
    if not trades.empty:
        scopes = {name: trades[trades.asset_group.eq(name)] for name in ['BTC', 'ETH', 'XAUUSD', 'ALTCOINS']}
        scopes['CRYPTO_ALL'] = trades[trades.symbol.ne('XAUUSD')]
        for name, pool in scopes.items():
            if pool.empty:
                continue
            for period in ['all', 'earlier', 'later']:
                group = pool if period == 'all' else pool[pool.period.eq(period) & ~pool.cross_cut]
                metrics.append(dict(group=name, period=period, **summarize(group, basis='gross' if name == 'XAUUSD' else 'net')))
        for (symbol, year, side), group in trades.groupby(['symbol', 'year', 'side']):
            breakdown.append(dict(symbol=symbol, year=int(year), side=int(side), **summarize(group, False)))
        trades.to_csv(dest / 'all-trades.csv.gz', index=False)
    opportunities.to_csv(dest / 'all-opportunities.csv.gz', index=False)
    entries.to_csv(dest / 'all-entries.csv.gz', index=False)
    pd.DataFrame(metrics).to_csv(dest / 'metrics.csv', index=False)
    pd.DataFrame(breakdown).to_csv(dest / 'year-side-symbol.csv', index=False)
    cn = {'group': '分组', 'period': '时间段', 'basis': '收益口径', 'filled_n': '成交数',
          'closed_n': '已决数', 'censored_n': '未决数', 'wins': '盈利数', 'losses': '亏损数',
          'win_rate': '胜率', 'mean_r': '平均R', 'mean_bp': '平均基点', 'mean_win_r': '平均盈利R',
          'mean_loss_r': '平均亏损R', 'payoff': '实际盈亏比', 'profit_factor': '盈利因子',
          'planned_rr_median': '计划盈亏比中位数', 'median_holding_hours': '持仓小时中位数',
          'longest_loss_streak': '跨币按平仓排序最长连亏', 'longest_stop_streak': '跨币最长连续止损',
          'longest_single_symbol_loss_streak': '单币最长连续亏损',
          'profitable_excursion_then_sl_n': '完整持仓K线浮盈后止损数',
          'fee_positive_excursion_then_sl_n': '完整持仓K线含费浮盈后止损数',
          'fully_matched_signal_n': '完整随机配对数', 'matched_signal_mean_r': '配对信号平均R',
          'matched_control_mean_r': '配对随机平均R', 'paired_excess_r': '配对超额R',
          'paired_ci_low': '超额95区间下限', 'paired_ci_high': '超额95区间上限', 'paired_p': '配对p值'}
    if metrics:
        pd.DataFrame(metrics).reindex(columns=cn).rename(columns=cn).to_csv(dest / '核心统计.csv', index=False, encoding='utf-8-sig')
    source = [json.loads((folder / 'done.json').read_text()) for folder in folders]
    summary = dict(experiment_id=EXP.name, status='partial' if partial else 'complete',
                   assets_complete=len(folders), expected_assets=633,
                   opportunities=len(opportunities), candidate_entries=len(entries),
                   native_positions=sum(row['positions'] for row in source),
                   native_fills=sum(row['fills'] for row in source),
                   invalid_tick_geometry=sum(row['invalid'] for row in source),
                   funnel=opportunities.status.value_counts().to_dict() if 'status' in opportunities else {},
                   metrics=metrics, generated_at=str(pd.Timestamp.now(tz='UTC')),
                   data_coverage=[{key: row[key] for key in ['symbol', 'source_rows', 'first', 'last']} for row in source],
                   analysis_source_commit=subprocess.check_output(['git', 'log', '-1', '--format=%H', '--', __file__], text=True).strip(),
                   native_run_state=json.loads((EXP / 'run-state.json').read_text()) if (EXP / 'run-state.json').exists() else None,
                   scope='One fixed SMA50 setup; independent R/notional, no leverage/capital compounding',
                   exclusions='Censored source boundaries excluded from economic win/return statistics; cross-split positions excluded from split views',
                   fees='Crypto per-fill taker.05percent entry/SL andmaker.02percent TP; gold grossonly; no funding/slippage',
                   execution='Nautilus1.231.0 once; O-H-L-C, synthetic liquidity, close-price market entry withzero latency',
                   excursion_scope='Complete bars strictly betweenentry andexit; conservative lower-bound excursions',
                   production_eligible=False, training_eligible=False)
    dump(dest / 'validation.json', finite(dict(passed=True, assets=len(validations), details=validations)))
    dump(dest / 'summary.json', finite(summary))
    print(json.dumps(finite({key: summary[key] for key in ['status', 'assets_complete', 'native_positions', 'native_fills', 'funnel']})), flush=True)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--partial', action='store_true')
    analyze(parser.parse_args().partial)
