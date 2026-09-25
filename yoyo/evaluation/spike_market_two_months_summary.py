"""Reproducible two-month all-market summary for the frozen SPIKE replay.

Signals are selected by signal close in [2026-07-25T00:00Z,
2026-09-25T00:00Z). Closed-trade outcomes use the inherited fixed 20 bp
round-trip cost. Censored marks stay in a separate ledger and never enter win
rates or realised-return summaries. Cross-symbol sums in R are trade units,
not portfolio returns; drawdown and loss streaks are calculated per symbol.
"""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import math
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v128_recent_report import block_inference

START = pd.Timestamp('2026-07-25T00:00:00Z')
END = pd.Timestamp('2026-09-25T00:00:00Z')
COST = 0.002
TIMEFRAMES = (5, 15, 60)
ARMS = ('v9_both', 'joint')
TABLES = ('trades', 'statuses', 'candidates', 'controls')
EXPECTED_ARCHIVED_SYMBOLS = 638
EXPECTED_UNIVERSE_SYMBOLS = 658
MFE_LEVELS = (0.5, 1, 2, 3, 5, 10)


def digest(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _bool(value) -> bool:
    if pd.isna(value):
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer, float, np.floating)):
        return bool(value)
    key = str(value).strip().lower()
    if key in {'true', 't', 'yes', 'y', '1'}:
        return True
    if key in {'false', 'f', 'no', 'n', '0', '', 'nan', 'none'}:
        return False
    raise ValueError(f'Invalid boolean value: {value!r}')


def _number(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors='coerce')


def _mean(series: pd.Series) -> float:
    x = pd.to_numeric(series, errors='coerce').dropna()
    return float(x.mean()) if len(x) else math.nan


def _median(series: pd.Series) -> float:
    x = pd.to_numeric(series, errors='coerce').dropna()
    return float(x.median()) if len(x) else math.nan


def _drawdown_and_streak(frame: pd.DataFrame) -> tuple[float, int]:
    if frame.empty:
        return math.nan, 0
    ordered = frame.sort_values(['exit_time', 'entry_time', 'trade_key'], kind='mergesort')
    values = _number(ordered, 'net_r').to_numpy(float)
    equity = np.r_[0.0, np.cumsum(values)]
    drawdown = float((np.maximum.accumulate(equity) - equity).max())
    best = run = 0
    for value in values:
        run = run + 1 if value < 0 else 0
        best = max(best, run)
    return drawdown, best


def _outcome_metrics(frame: pd.DataFrame, cost: float = COST) -> dict:
    """Describe closed outcomes only; callers expose censored counts separately."""
    censored = frame['_censored'] if '_censored' in frame else pd.Series(False, index=frame.index)
    all_n = len(frame)
    censored_mfe = _number(frame.loc[censored], 'mfe_known_r')
    f = frame.loc[~censored].copy()
    net_r = _number(f, 'net_r')
    gross_r = _number(f, 'gross_r')
    net_bp = _number(f, 'net_return') * 10_000
    gross_bp = _number(f, 'gross_return') * 10_000
    fees_r = cost / _number(f, 'initial_risk_frac')
    mfe = _number(f, 'mfe_known_r')
    upper = _number(f, 'mfe_upper_r')
    mfe_net = mfe - fees_r
    wins, losses = net_r[net_r > 0], net_r[net_r < 0]
    out = {
        'trades_taken': int(all_n), 'closed_trades': int(len(f)),
        'censored_unclosed_trades': int(censored.sum()),
        'censored_mfe_known_n': int(censored_mfe.notna().sum()),
        'censored_mfe_known_mean_r': _mean(censored_mfe),
        'censored_mfe_known_median_r': _median(censored_mfe),
        'wins_net': int(net_r.gt(0).sum()), 'losses_net': int(net_r.lt(0).sum()),
        'breakeven_net': int(net_r.eq(0).sum()),
        'fee_only_losses': int((_number(f, 'gross_return').ge(0) & _number(f, 'net_return').lt(0)).sum()),
        'win_rate_net': float(net_r.gt(0).mean()) if len(f) else math.nan,
        'gross_win_rate': float(_number(f, 'gross_return').gt(0).mean()) if len(f) else math.nan,
        'mean_net_r': _mean(net_r), 'median_net_r': _median(net_r),
        'sum_net_r_trade_units_nonportfolio': float(net_r.sum()) if len(f) else 0.0,
        'mean_gross_r': _mean(gross_r), 'median_gross_r': _median(gross_r),
        'sum_gross_r_trade_units_nonportfolio': float(gross_r.sum()) if len(f) else 0.0,
        'mean_net_bp': _mean(net_bp), 'median_net_bp': _median(net_bp),
        'mean_gross_bp': _mean(gross_bp), 'median_gross_bp': _median(gross_bp),
        'profit_factor_net_r': float(wins.sum() / -losses.sum()) if len(losses) else math.nan,
        'profit_factor_gross_r': (float(gross_r[gross_r > 0].sum() / -gross_r[gross_r < 0].sum())
                                  if (gross_r < 0).any() else math.nan),
        'mean_cost_r': _mean(fees_r), 'median_cost_r': _median(fees_r),
        'sum_cost_r_trade_units_nonportfolio': float(fees_r.sum()) if len(f) else 0.0,
        'fixed_round_trip_cost_bp': cost * 10_000,
        'mfe_known_n': int(mfe.notna().sum()), 'mfe_known_mean_r': _mean(mfe),
        'mfe_known_median_r': _median(mfe),
        'mfe_known_q25_r': float(mfe.quantile(.25)) if mfe.notna().any() else math.nan,
        'mfe_known_q75_r': float(mfe.quantile(.75)) if mfe.notna().any() else math.nan,
        'mfe_known_q90_r': float(mfe.quantile(.90)) if mfe.notna().any() else math.nan,
        'mfe_known_max_r': float(mfe.max()) if mfe.notna().any() else math.nan,
        'mfe_upper_mean_r': _mean(upper),
        'stop_bar_excursion_ambiguous_n': (int(f['stop_bar_excursion_ambiguous'].map(_bool).sum())
                                           if 'stop_bar_excursion_ambiguous' in f else 0),
        'net_floating_profit_then_loss_n': int((mfe_net.gt(0) & net_r.lt(0)).sum()),
        'net_floating_profit_n': int(mfe_net.gt(0).sum()),
        'mean_holding_hours': _mean(_number(f, 'holding_hours')),
        'median_holding_hours': _median(_number(f, 'holding_hours')),
    }
    # Excursion thresholds are overlapping descriptive counts, not exit rules.
    for level in MFE_LEVELS:
        tag = str(level).replace('.', 'p')
        hit = mfe.ge(level)
        out[f'mfe_ge_{tag}r_n'] = int(hit.sum())
        out[f'mfe_ge_{tag}r_then_net_loss_n'] = int((hit & net_r.lt(0)).sum())
    # Remove the highest price-return trade(s) to show concentration sensitivity.
    # The associated R totals remain trade units and are not a portfolio curve.
    price_order = net_bp.sort_values(ascending=False, kind='mergesort').index
    if len(price_order):
        one_removed = f.drop(index=price_order[:1])
        n_remove = max(1, math.ceil(.01 * len(f)))
        tail_removed = f.drop(index=price_order[:n_remove])
        out.update(best_net_return_bp=float(net_bp.max()),
            mean_net_bp_ex_best_price_return=float(_mean(_number(one_removed, 'net_return') * 10_000)),
            mean_net_r_ex_best_price_return=_mean(_number(one_removed, 'net_r')),
            sum_net_r_ex_best_price_return=float(_number(one_removed, 'net_r').sum()),
            top_1pct_price_return_removed_n=int(n_remove),
            mean_net_bp_ex_top_1pct_price_returns=_mean(_number(tail_removed, 'net_return') * 10_000),
            mean_net_r_ex_top_1pct_price_returns=_mean(_number(tail_removed, 'net_r')),
            sum_net_r_ex_top_1pct_price_returns=float(_number(tail_removed, 'net_r').sum()))
    else:
        out.update(best_net_return_bp=math.nan, mean_net_bp_ex_best_price_return=math.nan,
            mean_net_r_ex_best_price_return=math.nan, sum_net_r_ex_best_price_return=0.0,
            top_1pct_price_return_removed_n=0, mean_net_bp_ex_top_1pct_price_returns=math.nan,
            mean_net_r_ex_top_1pct_price_returns=math.nan, sum_net_r_ex_top_1pct_price_returns=0.0)
    return out


def _monthly_labels() -> list[str]:
    return list(pd.period_range(START.tz_localize(None), (END - pd.Timedelta(nanoseconds=1)).tz_localize(None), freq='M').astype(str))


def _status_count_rows(statuses: pd.DataFrame, keys: list[str]) -> dict:
    if statuses.empty or 'status' not in statuses:
        values = {}
    else:
        values = statuses.status.fillna('missing').astype(str).value_counts().to_dict()
    return {f'status_{str(k).replace(" ", "_")}': int(v) for k, v in values.items()}


def _overlap_pairs(rows: pd.DataFrame) -> int:
    """Count within-symbol control holding-interval overlaps, excluding touching endpoints."""
    total = 0
    if rows.empty:
        return total
    for _, group in rows.groupby(['symbol', 'timeframe_min'], dropna=False):
        intervals = group[['control_signal_close', 'control_exit_time']].dropna().sort_values('control_signal_close')
        active: list[int] = []
        for start, end in intervals.itertuples(index=False, name=None):
            s, e = pd.Timestamp(start).value, pd.Timestamp(end).value
            active = [x for x in active if x > s]
            total += len(active)
            heapq.heappush(active, e)
    return total


def _holm_six(p_values: list[float]) -> list[float]:
    """Holm-adjust a fixed six-test family; unavailable tests remain NaN."""
    result = [math.nan] * 6
    finite = [(float(p), i) for i, p in enumerate(p_values) if np.isfinite(p)]
    finite.sort()
    running = 0.0
    for rank, (p, idx) in enumerate(finite):
        running = max(running, min(1.0, p * (6 - rank)))
        result[idx] = running
    return result


def _normalise(frame: pd.DataFrame, table: str, symbol: str, minutes: int) -> pd.DataFrame:
    f = frame.copy()
    for key, value in (('symbol', symbol), ('timeframe_min', minutes)):
        if key not in f:
            f[key] = value
        elif len(f) and not f[key].astype(str).eq(str(value)).all():
            raise ValueError(f'{table} stream identity mismatch: {symbol}/{minutes}')
    if len(f) and 'arm' in f and not f.arm.isin(ARMS).all():
        raise ValueError(f'{table} has unknown arm in {symbol}/{minutes}')
    return f


def _prepare_trades(trades: pd.DataFrame, cost: float = COST) -> pd.DataFrame:
    f = trades.copy()
    if f.empty:
        for col, default in {'_censored': False, 'net_return': np.nan, 'gross_return': np.nan,
                             'net_r': np.nan, 'gross_r': np.nan}.items():
            if col not in f:
                f[col] = default
        return f
    if 'censored' not in f:
        raise ValueError('trades table missing censored flag')
    f['_censored'] = f.censored.map(_bool)
    for col in ('signal_close', 'entry_time', 'exit_time'):
        if col in f:
            f[col] = pd.to_datetime(f[col], utc=True, errors='coerce')
    if f.signal_close.isna().any() or not (f.signal_close.ge(START) & f.signal_close.lt(END)).all():
        raise ValueError('trade signal_close outside the requested half-open window')
    closed = f.loc[~f._censored]
    required = ('net_return', 'gross_return', 'net_r', 'gross_r', 'initial_risk_frac')
    if any(c not in closed for c in required):
        raise ValueError('closed trades missing return/risk fields')
    if len(closed):
        if closed.exit_time.isna().any() or closed.entry_time.isna().any():
            raise ValueError('closed trade has invalid entry/exit timestamp')
        if closed.exit_time.lt(closed.entry_time).any() or closed.exit_time.ge(END).any():
            raise ValueError('closed trade exits outside the observable replay window')
        risk = _number(closed, 'initial_risk_frac')
        if risk.isna().any() or (risk <= 0).any():
            raise ValueError('closed trade has invalid initial risk fraction')
        checks = [
            (_number(closed, 'gross_return') - _number(closed, 'net_return'), cost),
            (_number(closed, 'net_r') - _number(closed, 'net_return') / risk, 0.0),
            (_number(closed, 'gross_r') - _number(closed, 'gross_return') / risk, 0.0),
        ]
        for delta, expected in checks:
            if delta.isna().any() or not np.allclose(delta, expected, rtol=1e-8, atol=1e-9):
                raise ValueError('closed trade cost/R identity mismatch')
    f['net_bp'] = _number(f, 'net_return') * 10_000
    f['gross_bp'] = _number(f, 'gross_return') * 10_000
    f['fee_r'] = cost / _number(f, 'initial_risk_frac')
    f['mfe_known_net_r'] = _number(f, 'mfe_known_r') - f.fee_r
    f['net_float_then_loss'] = f.mfe_known_net_r.gt(0) & _number(f, 'net_r').lt(0) & ~f._censored
    f['month_utc'] = f.signal_close.dt.strftime('%Y-%m')
    if 'holding_hours' not in f and {'entry_time', 'exit_time'} <= set(f):
        f['holding_hours'] = (f.exit_time - f.entry_time).dt.total_seconds() / 3600
    return f


def summarize_frames(trades: pd.DataFrame, statuses: pd.DataFrame, candidates: pd.DataFrame,
                     controls: pd.DataFrame, coverage: pd.DataFrame,
                     cost: float = COST, stat_seed: int = 923129) -> dict[str, pd.DataFrame]:
    """Aggregate already verified stream ledgers; also used for focused fixtures."""
    trades = _prepare_trades(trades, cost)
    candidates, statuses, controls = candidates.copy(), statuses.copy(), controls.copy()
    for f in (candidates, statuses):
        if not f.empty and 'signal_close' in f:
            f['signal_close'] = pd.to_datetime(f.signal_close, utc=True, errors='coerce')
            if f.signal_close.isna().any() or not (f.signal_close.ge(START) & f.signal_close.lt(END)).all():
                raise ValueError('candidate/status signal_close outside requested window')
    if not controls.empty:
        controls['matched'] = controls.matched.map(_bool)
        if trades.empty or 'trade_key' not in controls or not set(controls.trade_key).issubset(set(trades.trade_key)):
            raise ValueError('controls include an unknown target trade')
        target_censored = trades.set_index('trade_key')._censored
        controls['_target_censored'] = controls.trade_key.map(target_censored).map(_bool)
        if (controls.matched & controls._target_censored).any():
            raise ValueError('a censored target is marked as matched')
        target_returns = trades.set_index('trade_key').net_return
        controls['_ledger_target_net_return'] = controls.trade_key.map(target_returns)
        comparable = controls['_ledger_target_net_return'].notna() & pd.to_numeric(controls.target_net_return, errors='coerce').notna()
        if comparable.any() and not np.allclose(
            pd.to_numeric(controls.loc[comparable, 'target_net_return'], errors='coerce'),
            pd.to_numeric(controls.loc[comparable, '_ledger_target_net_return'], errors='coerce'),
            rtol=1e-8, atol=1e-10):
            raise ValueError('control target return does not match its trade ledger')
        for col in ('control_signal_close', 'control_exit_time'):
            if col in controls:
                controls[col] = pd.to_datetime(controls[col], utc=True, errors='coerce')
        controls['same_window_control'] = (controls.matched & ~controls._target_censored
            & controls.control_signal_close.ge(START) & controls.control_signal_close.lt(END)
            & controls.control_exit_time.ge(controls.control_signal_close)
            & controls.control_exit_time.lt(END))
    else:
        controls = pd.DataFrame(columns=['symbol', 'timeframe_min', 'arm', 'matched', 'same_window_control'])

    if not trades.empty:
        if 'trade_key' not in trades or trades.trade_key.duplicated().any():
            raise ValueError('trade_key missing or duplicated')
    coverage = coverage.copy()
    if coverage.empty or not {'symbol', 'timeframe_min'} <= set(coverage):
        raise ValueError('coverage must include every stream, including zero-trade streams')
    streams = coverage[['symbol', 'timeframe_min']].drop_duplicates()
    skeleton = streams.assign(_join=1).merge(pd.DataFrame({'arm': ARMS, '_join': 1}), on='_join').drop(columns='_join')

    by_symbol_rows = []
    for row in skeleton.to_dict('records'):
        symbol, minutes, arm = row['symbol'], int(row['timeframe_min']), row['arm']
        t = trades[(trades.symbol == symbol) & (trades.timeframe_min == minutes) & (trades.arm == arm)] if len(trades) else trades
        c = candidates[(candidates.symbol == symbol) & (candidates.timeframe_min == minutes) & (candidates.arm == arm)] if len(candidates) else candidates
        s = statuses[(statuses.symbol == symbol) & (statuses.timeframe_min == minutes) & (statuses.arm == arm)] if len(statuses) else statuses
        metric = _outcome_metrics(t, cost)
        dd, streak = _drawdown_and_streak(t.loc[~t._censored] if len(t) else t)
        by_symbol_rows.append({**row, 'raw_signal_candidates': int(len(c)), 'status_rows': int(len(s)),
            'skipped_in_position': int(s.status.eq('skipped_in_position').sum()) if len(s) and 'status' in s else 0,
            **metric, 'closed_trade_drawdown_r_single_symbol': dd, 'max_loss_streak_single_symbol': streak})
    by_symbol = pd.DataFrame(by_symbol_rows)

    metrics_rows, side_rows, month_rows = [], [], []
    status_names = sorted(statuses.status.dropna().astype(str).unique()) if len(statuses) and 'status' in statuses else []
    for minutes in TIMEFRAMES:
        for arm in ARMS:
            t = trades[(trades.timeframe_min == minutes) & (trades.arm == arm)] if len(trades) else trades
            c = candidates[(candidates.timeframe_min == minutes) & (candidates.arm == arm)] if len(candidates) else candidates
            s = statuses[(statuses.timeframe_min == minutes) & (statuses.arm == arm)] if len(statuses) else statuses
            bs = by_symbol[(by_symbol.timeframe_min == minutes) & (by_symbol.arm == arm)]
            closed_symbols = bs[bs.closed_trades.gt(0)]
            row = {'timeframe_min': minutes, 'arm': arm, 'raw_signal_candidates': int(len(c)),
                'status_rows': int(len(s)), 'candidate_status_count_delta': int(len(s) - len(c)),
                'skipped_in_position': int(s.status.eq('skipped_in_position').sum()) if len(s) and 'status' in s else 0,
                **_outcome_metrics(t, cost)}
            for status in status_names:
                row[f'status_{status.replace(" ", "_")}'] = int(s.status.astype(str).eq(status).sum()) if len(s) and 'status' in s else 0
            symbol_net_bp = closed_symbols.mean_net_bp
            symbol_net_r = closed_symbols.mean_net_r
            row.update(symbols_with_closed_trades=int(len(closed_symbols)),
                profitable_symbols_by_mean_net_bp=int(symbol_net_bp.gt(0).sum()),
                profitable_symbol_share_by_mean_net_bp=float(symbol_net_bp.gt(0).mean()) if len(closed_symbols) else math.nan,
                equal_weight_mean_symbol_net_bp=_mean(symbol_net_bp),
                equal_weight_median_symbol_net_bp=_median(symbol_net_bp),
                equal_weight_mean_symbol_net_r=_mean(symbol_net_r),
                equal_weight_median_symbol_net_r=_median(symbol_net_r),
                median_single_symbol_drawdown_r=_median(closed_symbols.closed_trade_drawdown_r_single_symbol),
                max_single_symbol_drawdown_r=(float(closed_symbols.closed_trade_drawdown_r_single_symbol.max())
                                              if len(closed_symbols) else math.nan),
                max_single_symbol_loss_streak=(int(closed_symbols.max_loss_streak_single_symbol.max())
                                               if len(closed_symbols) else 0),
                symbols_with_no_signals=int((bs.raw_signal_candidates == 0).sum()))
            metrics_rows.append(row)

            for side in (-1, 1):
                tf = t[_number(t, 'side').eq(side)] if len(t) and 'side' in t else t.iloc[0:0]
                cf = c[_number(c, 'side').eq(side)] if len(c) and 'side' in c else c.iloc[0:0]
                sf = s[_number(s, 'side').eq(side)] if len(s) and 'side' in s else s.iloc[0:0]
                side_rows.append({'timeframe_min': minutes, 'arm': arm, 'side': side,
                    'raw_signal_candidates': int(len(cf)), 'status_rows': int(len(sf)),
                    'skipped_in_position': int(sf.status.eq('skipped_in_position').sum()) if len(sf) and 'status' in sf else 0,
                    **_outcome_metrics(tf, cost)})
            for month in _monthly_labels():
                tm = t[t.month_utc.eq(month)] if len(t) and 'month_utc' in t else t.iloc[0:0]
                cm = c[c.signal_close.dt.strftime('%Y-%m').eq(month)] if len(c) and 'signal_close' in c else c.iloc[0:0]
                sm = s[s.signal_close.dt.strftime('%Y-%m').eq(month)] if len(s) and 'signal_close' in s else s.iloc[0:0]
                month_rows.append({'timeframe_min': minutes, 'arm': arm, 'month_utc': month,
                    'raw_signal_candidates': int(len(cm)), 'status_rows': int(len(sm)),
                    'skipped_in_position': int(sm.status.eq('skipped_in_position').sum()) if len(sm) and 'status' in sm else 0,
                    **_outcome_metrics(tm, cost)})

    paired_rows, weekly_rows = [], []
    for minutes in TIMEFRAMES:
        for arm in ARMS:
            g = controls[(controls.timeframe_min == minutes) & (controls.arm == arm)].copy() if len(controls) else controls
            all_closed = int(len(trades[(trades.timeframe_min == minutes) & (trades.arm == arm) & ~trades._censored])) if len(trades) else 0
            good = g[g.same_window_control].copy() if len(g) else g
            for col in ('target_net_return', 'control_net_return'):
                if col not in good:
                    good[col] = np.nan
                good[col] = pd.to_numeric(good[col], errors='coerce')
            good = good.dropna(subset=['target_net_return', 'control_net_return'])
            good['target_net_bp'] = good.target_net_return * 10_000
            good['control_net_bp'] = good.control_net_return * 10_000
            good['excess_bp'] = good.target_net_bp - good.control_net_bp
            if 'utc_week' not in good:
                good['utc_week'] = good.control_signal_close.dt.strftime('%G-W%V')
            if len(good):
                for week, wg in good.groupby('utc_week', dropna=False):
                    weekly_rows.append({'timeframe_min': minutes, 'arm': arm, 'utc_week': week,
                        'matched_n': len(wg), 'target_mean_net_bp': float(wg.target_net_bp.mean()),
                        'control_mean_net_bp': float(wg.control_net_bp.mean()),
                        'excess_mean_net_bp': float(wg.excess_bp.mean()), 'excess_sum_bp': float(wg.excess_bp.sum())})
                seed = stat_seed + minutes * 10 + (0 if arm == 'v9_both' else 1)
                inference = block_inference(good.excess_bp.to_numpy(), good.utc_week.to_numpy(), seed=seed)
            else:
                inference = {'weeks': 0, 'mean_excess_bp': math.nan, 'ci_low_bp': math.nan,
                             'ci_high_bp': math.nan, 'p_one_sided': math.nan, 'minimum_p_resolution': math.nan}
            sigcol = 'control_sig' if 'control_sig' in good else None
            if sigcol:
                ids = good['symbol'].astype(str) + ':' + good['timeframe_min'].astype(str) + ':' + good[sigcol].astype(str)
            elif 'control_signal_close' in good:
                ids = good['symbol'].astype(str) + ':' + good['timeframe_min'].astype(str) + ':' + good.control_signal_close.astype(str)
            else:
                ids = pd.Series(dtype=str)
            reuse = ids.value_counts() if len(ids) else pd.Series(dtype=int)
            paired_rows.append({'timeframe_min': minutes, 'arm': arm, 'closed_target_n': all_closed,
                'inherited_matched_n': int(g.matched.sum()) if len(g) else 0,
                'same_window_matched_n': int(len(g[g.same_window_control])) if len(g) else 0,
                'same_window_pair_complete_n': int(len(good)),
                'matched_out_of_window_or_invalid_n': int((g.matched & ~g.same_window_control).sum()) if len(g) else 0,
                'unmatched_or_boundary_target_n': max(0, all_closed - int(len(good))),
                'target_mean_net_bp_matched': _mean(good.target_net_bp) if len(good) else math.nan,
                'control_mean_net_bp_same_window': _mean(good.control_net_bp) if len(good) else math.nan,
                'mean_excess_net_bp': _mean(good.excess_bp) if len(good) else math.nan,
                'reused_control_assignment_excess_n': int(len(ids) - ids.nunique()) if len(ids) else 0,
                'distinct_reused_control_signal_n': int((reuse > 1).sum()) if len(reuse) else 0,
                'max_control_reuse': int(reuse.max()) if len(reuse) else 0,
                'control_interval_overlap_pairs_within_symbol': _overlap_pairs(good),
                **{f'weekly_{k}': v for k, v in inference.items()}})
    metrics = pd.DataFrame(metrics_rows)
    paired = pd.DataFrame(paired_rows)
    shared_by_minutes = {}
    if len(controls):
        across = controls[controls.same_window_control].copy()
        if len(across):
            if 'control_sig' in across:
                across['_control_id'] = across.control_sig.astype(str)
            else:
                across['_control_id'] = across.control_signal_close.astype(str)
            across['_stream_control_id'] = (across.symbol.astype(str) + ':'
                + across.timeframe_min.astype(str) + ':' + across['_control_id'])
            use = across.groupby(['timeframe_min', '_stream_control_id']).agg(
                arms=('arm', 'nunique'), assignments=('arm', 'size'))
            for minutes, part in use[use.arms.gt(1)].groupby(level=0):
                shared_by_minutes[int(minutes)] = {
                    'shared_control_ids_across_arms_n': int(len(part)),
                    'shared_control_assignments_across_arms_n': int(part.assignments.sum()),
                }
    for col in ('shared_control_ids_across_arms_n', 'shared_control_assignments_across_arms_n'):
        paired[col] = paired.timeframe_min.map(lambda m: shared_by_minutes.get(int(m), {}).get(col, 0))
    paired['holm_p_one_sided_six_groups'] = _holm_six(paired.weekly_p_one_sided.tolist())
    return {'metrics': metrics, 'by_symbol': by_symbol, 'by_side': pd.DataFrame(side_rows),
        'by_month': pd.DataFrame(month_rows), 'matched_comparison': paired,
        'matched_weekly': pd.DataFrame(weekly_rows), 'trades': trades,
        'censored_trades': trades[trades._censored].copy(), 'statuses': statuses,
        'candidates': candidates, 'controls': controls}


def _load_run(root: Path) -> tuple[dict, dict, dict, dict, dict]:
    root = Path(root)
    run = root / 'run_v1'
    manifest_path = run / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if not manifest.get('complete') or manifest.get('errors'):
        raise ValueError('run manifest is incomplete or has errors')
    if manifest.get('subset'):
        raise ValueError('refusing a subset replay for the all-market summary')
    identity_path = run / 'identity.json'
    identity = json.loads(identity_path.read_text()) if identity_path.exists() else {}
    if not identity or _fingerprint(identity) != manifest.get('run_identity'):
        raise ValueError('run identity fingerprint disagrees with manifest')
    receipts = manifest.get('receipts', [])
    if not receipts:
        raise ValueError('run manifest has no stream receipts')
    seen = set()
    tables = {name: [] for name in TABLES}
    coverage_rows, receipt_hashes = [], {}
    expected_files = {f'{name}.csv.gz' for name in TABLES}
    for receipt in receipts:
        symbol, minutes = str(receipt['symbol']), int(receipt['minutes'])
        key = (symbol, minutes)
        if key in seen:
            raise ValueError(f'duplicate stream receipt: {key}')
        seen.add(key)
        if minutes not in TIMEFRAMES:
            raise ValueError(f'unexpected timeframe: {minutes}')
        if receipt.get('run_identity') != manifest.get('run_identity'):
            raise ValueError(f'run identity mismatch for {key}')
        summary = receipt.get('summary')
        if not isinstance(summary, dict) or str(summary.get('symbol')) != symbol or int(summary.get('minutes')) != minutes:
            raise ValueError(f'missing/mismatched coverage summary: {key}')
        folder = run / 'streams' / f'{symbol}_{minutes}m'
        disk_receipt_path = folder / 'receipt.json'
        disk_receipt = json.loads(disk_receipt_path.read_text())
        if disk_receipt != receipt:
            raise ValueError(f'manifest/stream receipt mismatch: {key}')
        receipt_hashes[str(disk_receipt_path)] = digest(disk_receipt_path)
        files = receipt.get('files', {})
        if set(files) != expected_files:
            raise ValueError(f'stream output set mismatch for {key}: {sorted(files)}')
        for filename in sorted(expected_files):
            path = folder / filename
            if digest(path) != files[filename]:
                raise ValueError(f'output sha mismatch: {path}')
            receipt_hashes[str(path)] = files[filename]
            table = filename.removesuffix('.csv.gz')
            part = _normalise(read_csv(path), table, symbol, minutes)
            if not part.empty:
                part['stream_key'] = f'{symbol}_{minutes}m'
                tables[table].append(part)
        coverage_rows.append({**summary, 'symbol': symbol, 'timeframe_min': minutes,
            'stream_key': f'{symbol}_{minutes}m'})
    symbol_set = {symbol for symbol, _ in seen}
    expected = {(symbol, minutes) for symbol in symbol_set for minutes in TIMEFRAMES}
    if seen != expected:
        missing = sorted(expected - seen)[:10]
        raise ValueError(f'coverage inventory missing streams, e.g. {missing}')
    streams_dir = run / 'streams'
    actual_dirs = {p.name for p in streams_dir.iterdir() if p.is_dir()}
    expected_dirs = {f'{symbol}_{minutes}m' for symbol, minutes in seen}
    if actual_dirs != expected_dirs:
        raise ValueError('run stream directory inventory disagrees with receipts')

    config_path = root / 'config.json'
    config = json.loads(config_path.read_text())
    if pd.Timestamp(config['start']) != START or pd.Timestamp(config['end']) != END:
        raise ValueError('config window differs from requested half-open window')
    if not math.isclose(float(config['round_trip_cost']), COST, rel_tol=0, abs_tol=1e-12):
        raise ValueError('round-trip cost differs from fixed 20 bp')
    universe_info = {}
    data_dir = Path(config['data_dir'])
    if not data_dir.is_absolute():
        data_dir = Path.cwd() / data_dir
    universe_path = data_dir / 'universe.json'
    data_manifest_path = data_dir / 'manifest.json'
    if not universe_path.exists() or not data_manifest_path.exists():
        raise ValueError('complete universe and input-data manifests are required')
    universe = json.loads(universe_path.read_text())
    symbols = set(map(str, universe.get('symbols', [])))
    if symbols != symbol_set:
        raise ValueError('run streams do not cover the staged complete universe')
    archived = int(universe.get('archived_count', -1))
    if archived != EXPECTED_ARCHIVED_SYMBOLS or len(symbols) != EXPECTED_UNIVERSE_SYMBOLS:
        raise ValueError(f'universe count mismatch: archived={archived}, symbols={len(symbols)}')
    meta = universe.get('metadata', {})
    universe_info = {'archived_count': archived, 'universe_symbol_count': len(symbols),
        'addition_count': len(universe.get('additions', [])),
        'current_status_counts': pd.Series([v.get('current_status', 'unknown') for v in meta.values()]).value_counts().to_dict(),
        'universe_sha256': digest(universe_path)}
    if meta:
        coverage_meta = pd.DataFrame([{'symbol': k, **v} for k, v in meta.items()])
        coverage = pd.DataFrame(coverage_rows).merge(coverage_meta, on='symbol', how='left', suffixes=('', '_universe'), validate='many_to_one')
    else:
        coverage = pd.DataFrame(coverage_rows)
    receipt_hashes[str(universe_path)] = universe_info['universe_sha256']
    data_manifest = json.loads(data_manifest_path.read_text())
    if not data_manifest.get('complete') or data_manifest.get('failed'):
        raise ValueError('source data stage is incomplete')
    receipt_hashes[str(data_manifest_path)] = digest(data_manifest_path)
    if identity.get('input_manifest_sha256') != digest(data_manifest_path):
        raise ValueError('run identity does not pin the input data manifest')
    data_rows = pd.DataFrame(data_manifest.get('streams', []))
    if not data_rows.empty:
        keep = [c for c in ('symbol', 'tail_status', 'recent_rows', 'recent_positive_volume_rows', 'window_missing_5m', 'gap_count') if c in data_rows]
        coverage = coverage.merge(data_rows[keep], on='symbol', how='left', validate='many_to_one')
    combined = {}
    for name in TABLES:
        xs = tables[name]
        combined[name] = pd.concat(xs, ignore_index=True) if xs else pd.DataFrame()
    paths = {'manifest': manifest_path, 'identity': identity_path, 'config': config_path}
    hashes = {str(path): digest(path) for path in paths.values() if path.exists()}
    hashes.update(receipt_hashes)
    return combined, coverage, config, {'hashes': hashes, 'universe': universe_info}, manifest


def _coverage_status(coverage: pd.DataFrame) -> pd.DataFrame:
    f = coverage.copy()
    for col in ('window_bars_expected', 'window_bars_actual', 'window_gap_count', 'valid_ready_window_bars'):
        if col not in f:
            f[col] = 0
        f[col] = pd.to_numeric(f[col], errors='coerce').fillna(0).astype(int)
    # Raw bar timestamps use open-time windows; valid_ready_window_bars uses
    # the signal's close clock. Keep them as separate observations because a
    # half-open edge can change the latter count by one without a missing raw bar.
    f['ready_signal_bars'] = f['valid_ready_window_bars']
    f['coverage_status'] = np.select([
        f.window_bars_actual.eq(0),
        f.window_bars_actual.ne(f.window_bars_expected) | f.window_gap_count.gt(0),
    ], ['no_recent_bars', 'partial_raw_window_bars'], default='complete_raw_window_bars')
    if 'zero_activity' not in f:
        f['zero_activity'] = False
    return f.sort_values(['symbol', 'timeframe_min']).reset_index(drop=True)


def summarize(root: Path | str, output: Path | str | None = None) -> dict:
    """Verify a completed full run, write CSV summaries, return its receipt."""
    root = Path(root)
    own = Path(__file__).resolve().relative_to(Path.cwd())
    try:
        committed = subprocess.check_output(['git', 'show', f'HEAD:{own}'])
    except subprocess.CalledProcessError as exc:
        raise ValueError('Commit summary builder before constructing results') from exc
    if committed != own.read_bytes():
        raise ValueError('Commit summary builder before constructing results')
    tables, coverage, config, pins, manifest = _load_run(root)
    coverage = _coverage_status(coverage)
    out = Path(output) if output is not None else root / 'summary_v1'
    if out.exists():
        raise FileExistsError(f'refusing to overwrite existing summary: {out}')
    results = summarize_frames(tables['trades'], tables['statuses'], tables['candidates'],
        tables['controls'], coverage, cost=COST, stat_seed=int(config.get('stat_seed', 923129)))
    coverage_summary = []
    for minutes, rows in coverage.groupby('timeframe_min'):
        coverage_summary.append({'timeframe_min': int(minutes), 'streams_total': int(len(rows)),
            'streams_complete': int(rows.coverage_status.eq('complete_raw_window_bars').sum()),
            'streams_partial': int(rows.coverage_status.eq('partial_raw_window_bars').sum()),
            'streams_no_bars': int(rows.coverage_status.eq('no_recent_bars').sum())})
    results['metrics'] = results['metrics'].merge(pd.DataFrame(coverage_summary), on='timeframe_min', how='left')
    results['extreme_trades'] = _extremes(results['trades'])
    results['coverage'] = coverage
    output_files = {
        'metrics.csv': results['metrics'], 'by_symbol.csv': results['by_symbol'],
        'by_side.csv': results['by_side'], 'by_month.csv': results['by_month'],
        'matched_comparison.csv': results['matched_comparison'], 'matched_weekly.csv': results['matched_weekly'],
        'coverage.csv': results['coverage'], 'trades.csv.gz': results['trades'],
        'trades_cn.csv': _trades_chinese(results['trades']),
        'censored_trades.csv.gz': results['censored_trades'], 'extreme_trades.csv': results['extreme_trades'],
        'statuses.csv.gz': results['statuses'], 'candidates.csv.gz': results['candidates'],
        'controls.csv.gz': results['controls'],
    }
    out.mkdir(parents=True)
    file_hashes = {}
    for name, frame in output_files.items():
        path = out / name
        if name.endswith('.gz'):
            frame.to_csv(path, index=False, compression={'method': 'gzip', 'mtime': 0})
        else:
            frame.to_csv(path, index=False)
        file_hashes[str(path)] = digest(path)
    universe = pins['universe']
    receipt = {'window_start_inclusive': START.isoformat(), 'window_end_exclusive': END.isoformat(),
        'timezone': 'UTC', 'round_trip_cost': COST, 'source_run_identity': manifest['run_identity'],
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'input_sha256': pins['hashes'], 'output_sha256': file_hashes,
        **universe,
        'streams': int(len(coverage)), 'symbols': int(coverage.symbol.nunique()),
        'trade_rows': int(len(results['trades'])), 'closed_trade_rows': int((~results['trades']._censored).sum()),
        'censored_trade_rows': int(results['trades']._censored.sum()),
        'disclosures': [
            'v9_both and joint are separate, potentially overlapping ledgers.',
            'Censored/unclosed rows are preserved separately and excluded from wins and realised return metrics.',
            'MFE uses inherited mfe_known_r, the conservative pre-exit OHLC lower bound; upper wick ambiguity remains descriptive.',
            'All-symbol R sums are risk-normalized trade units, not portfolio returns.',
            'Drawdown and loss streaks are computed within each symbol/timeframe/arm only.',
            'Matched comparisons use only matched controls whose signal and exit both fall within the requested window; inference is in net price bp with UTC-week blocks.',
            'Controls may be reused and their holding intervals may overlap; counts are included in matched_comparison.csv.',
            'Fixed round-trip cost is 20 bp; no realised funding series is available.',
            'Extreme rows are ranked independently by net price return and net R to expose denominator effects.',
            'Tail sensitivity removes the best one trade and best 1% by net price bp, not by net R.',
            'Exchange contract statuses are a current snapshot, not a historical count of active contracts during the replay window.',
        ]}
    (out / 'receipt.json').write_text(json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False, default=_json_default) + '\n')
    return receipt


def _extremes(trades: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    columns = ['rank_basis', 'rank_type', 'rank', 'symbol', 'timeframe_min', 'arm', 'trade_key',
               'signal_close', 'entry_time', 'exit_time', 'side', 'exit_reason', 'net_return', 'net_bp',
               'net_r', 'gross_return', 'gross_r', 'initial_risk_frac', 'mfe_known_r', 'mfe_upper_r']
    rows = []
    if trades.empty:
        return pd.DataFrame(columns=columns)
    f = trades.loc[~trades._censored].copy()
    for (minutes, arm), group in f.groupby(['timeframe_min', 'arm']):
        for basis in ('net_return', 'net_r'):
            if basis not in group:
                continue
            values = pd.to_numeric(group[basis], errors='coerce')
            for kind, ascending in (('winner', False), ('loser', True)):
                chosen = group.assign(_rank_value=values).sort_values(['_rank_value', 'trade_key'], ascending=[ascending, True]).head(n)
                for rank, (_, row) in enumerate(chosen.iterrows(), 1):
                    rows.append({'rank_basis': basis, 'rank_type': kind, 'rank': rank,
                        **{col: row.get(col) for col in columns if col not in ('rank_basis', 'rank_type', 'rank')}})
    return pd.DataFrame(rows, columns=columns)


def _trades_chinese(trades: pd.DataFrame) -> pd.DataFrame:
    """Owner-facing audit columns; every source row, including censored, is kept."""
    columns = ['交易标识', '币种', '周期', '类型', '方向', '信号时间北京时间',
        '入场时间北京时间', '退出时间北京时间', '入场价', '初始止损', '退出价', '退出原因',
        '毛R', '净R', '净收益百分比', '最高已确认浮盈R', '浮盈上界R', '成本R', '是否未平或删失']
    if trades.empty:
        return pd.DataFrame(columns=columns)
    f = trades.copy()
    for source, target in (('signal_close', '信号时间北京时间'), ('entry_time', '入场时间北京时间'),
                           ('exit_time', '退出时间北京时间')):
        f[target] = pd.to_datetime(f[source], utc=True, errors='coerce').dt.tz_convert('Asia/Shanghai').dt.strftime('%Y-%m-%d %H:%M:%S')
    f['周期'] = f.timeframe_min.map({5: '5m', 15: '15m', 60: '1h'}).fillna(f.timeframe_min.astype(str) + 'm')
    f['类型'] = f.arm.map({'v9_both': '普通', 'joint': '联合'}).fillna(f.arm.astype(str))
    f['方向'] = _number(f, 'side').map({1: '多', -1: '空'})
    f['净收益百分比'] = _number(f, 'net_return') * 100
    f['是否未平或删失'] = f._censored.map({True: '是', False: '否'})
    f = f.rename(columns={'trade_key': '交易标识', 'symbol': '币种', 'entry_price': '入场价',
        'initial_stop': '初始止损', 'exit_price': '退出价', 'exit_reason': '退出原因',
        'gross_r': '毛R', 'net_r': '净R', 'mfe_known_r': '最高已确认浮盈R',
        'mfe_upper_r': '浮盈上界R', 'fee_r': '成本R'})
    return f[columns]


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    return str(value)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path, help='experiment root containing run_v1/')
    parser.add_argument('--output', type=Path, help='summary output directory (default: <root>/summary_v1)')
    args = parser.parse_args()
    result = summarize(args.root, args.output)
    print(json.dumps({k: result[k] for k in ('streams', 'symbols', 'trade_rows', 'closed_trade_rows', 'censored_trade_rows')}, indent=2))
