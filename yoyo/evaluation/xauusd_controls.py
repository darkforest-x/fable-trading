"""Outcome-blind, event-level controls for the frozen XAUUSD research search.

Matching uses decision-time UTC month, the caller's causal ATR/price volatility
bucket and the actual trade direction. Candidate control bars must be ready
and have no entry request in either direction. A bar/side is never reused.
Features are read only at their own decision bars; future minute prices and
exit flags are used solely to measure outcomes after identities are frozen.

Controls use the BID plus fixed round-trip cost convention, not bid/ask fills
or a portfolio simulation. Each actual trade receives exactly n_controls or
none. Inference acts on equally weighted monthly means of matched trade
differences; it is a sign-symmetry test, not a randomized trading experiment.
"""
from __future__ import annotations

from itertools import product
from typing import Any

import numpy as np
import pandas as pd


_MINUTE = pd.Timedelta(minutes=1)
_PAIR_COLUMNS = [
    'actual_trade_id', 'actual_decision_index', 'actual_decision_time',
    'month', 'volbin', 'side', 'score', 'actual_gross_bp', 'actual_net_bp',
    'control_number', 'control_decision_index', 'control_decision_time',
    'control_entry_index', 'control_entry_time', 'control_entry_price',
    'control_exit_decision_index', 'control_exit_decision_time',
    'control_exit_index', 'control_exit_time', 'control_exit_price',
    'control_exit_kind', 'control_gross_bp', 'control_net_bp',
    'gross_excess_bp', 'excess_bp',
]


def _utc(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError('Window boundaries must be finite')
    return timestamp.tz_localize('UTC') if timestamp.tzinfo is None else timestamp.tz_convert('UTC')


def _index(frame: pd.DataFrame, name: str) -> pd.DatetimeIndex:
    index = frame.index
    if not isinstance(index, pd.DatetimeIndex) or index.tz is None:
        raise ValueError(f'{name} requires a timezone-aware DatetimeIndex')
    if index.hasnans or index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError(f'{name} timestamps must be finite, unique and increasing')
    return index.tz_convert('UTC')


def holm(pvalues: Any) -> np.ndarray:
    """Holm adjustment across the entire supplied, preregistered family.

    Missing tests remain NaN but count in family size, equivalent to unobserved
    p=1 hypotheses for adjustment of the finite tests. Pass all 189 candidate
    rows together, not only significant rows or the selected configurations.
    """
    values = np.asarray(pvalues, dtype=float)
    if values.ndim != 1:
        raise ValueError('pvalues must be one dimensional')
    finite = np.isfinite(values)
    if np.isinf(values).any() or ((values[finite] < 0) | (values[finite] > 1)).any():
        raise ValueError('Finite p-values must lie in [0, 1]')
    result = np.full(len(values), np.nan)
    order = np.flatnonzero(finite)
    order = order[np.argsort(values[order], kind='stable')]
    adjusted = values[order] * (len(values) - np.arange(len(order)))
    result[order] = np.minimum(1.0, np.maximum.accumulate(adjusted))
    return result


def _inference(cases: pd.DataFrame, seed: int) -> dict[str, Any]:
    """Exact monthly sign flips through 12 months; otherwise 1999 draws."""
    result = dict(months=0, monthly_mean_excess_bp=None, p=None,
                  ci_low=None, ci_high=None, inference_status='no_complete_pairs',
                  inference_estimand='equal-weight monthly mean of per-trade net excess',
                  inference_draws=1999, p_method=None)
    if cases.empty:
        return result
    values = cases.groupby('month', sort=True).excess_bp.mean().to_numpy(float)
    count = len(values)
    rng = np.random.default_rng(seed)
    observed = float(values.sum())
    tolerance = 1e-12 * max(1.0, float(np.abs(values).sum()))
    if count <= 12:
        signs = np.asarray(list(product((-1, 1), repeat=count)), dtype=np.int8)
        p = float(np.mean((signs * values).sum(axis=1) >= observed - tolerance))
        method = 'exact_monthly_sign_flip'
    else:
        signs = rng.choice([-1, 1], size=(1999, count))
        p = float((1 + np.count_nonzero((signs * values).sum(axis=1) >= observed - tolerance)) / 2000)
        method = 'monte_carlo_monthly_sign_flip'
    result.update(months=count, monthly_mean_excess_bp=float(values.mean()), p=p,
                  p_method=method, inference_status='ok' if count > 1 else 'one_month_no_bootstrap_ci')
    if count > 1:
        samples = rng.integers(0, count, size=(1999, count))
        boot = values[samples].mean(axis=1)
        result.update(ci_low=float(np.quantile(boot, .025)),
                      ci_high=float(np.quantile(boot, .975)))
    return result


def _score_stats(actual: pd.DataFrame) -> dict[str, Any]:
    """Fixed abs(md)/ATR score; outcomes never choose the upper score decile."""
    result = dict(score_n=0, score_auc=None, score_auc_status='no_finite_scores',
                  score_top_decile_n=0, score_top_decile_gross_bp=None,
                  score_top_decile_net_bp=None, score_top_decile_win_pct=None)
    if actual.empty:
        return result
    scored = actual.loc[np.isfinite(actual.score)].copy()
    if scored.empty:
        return result
    wins = scored.net_bp > 0
    n1, n0 = int(wins.sum()), int((~wins).sum())
    result.update(score_n=len(scored), score_auc_status='single_profit_class')
    if n1 and n0:
        ranks = scored.score.rank(method='average')
        result.update(score_auc=float((ranks.loc[wins].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)),
                      score_auc_status='ok')
    top = scored.sort_values(['score', 'decision_index', 'trade_id'],
                             ascending=[False, True, True], kind='stable').head(int(np.ceil(len(scored) / 10)))
    result.update(score_top_decile_n=len(top),
                  score_top_decile_gross_bp=float(top.gross_bp.mean()),
                  score_top_decile_net_bp=float(top.net_bp.mean()),
                  score_top_decile_win_pct=float((top.net_bp > 0).mean() * 100))
    return result


def matched_controls(
    minutes: pd.DataFrame, bars: pd.DataFrame, features: pd.DataFrame,
    entry: Any, exit_long: Any, exit_short: Any, ledger: pd.DataFrame,
    start: Any, end: Any, seed: int = 20260908, cost_bp: float = 20,
    n_controls: int = 3,
) -> dict[str, Any]:
    """Match actual accepted trades, then measure independent control events.

    Ledger requires trade_id, decision_index (original bars position), side,
    gross_bp and net_bp. Minute entry/exit indices in output refer to the
    original minutes frame. A control decision fills the first actual minute
    OPEN at/after time_close. Its first side-specific exit decision strictly
    after that decision fills by the same rule, including market gaps. With
    no in-window executable exit, use the final complete minute CLOSE.

    Actual returns are retained from the engine ledger. Controls are linear
    event outcomes, not funded accounts: no portfolio bankruptcy floor applies.
    Any actual bankruptcy/floor cases are exposed and disable inferential p/CI
    because these exit/capital rules are then not comparable.
    """
    start, end = _utc(start), _utc(end)
    if start >= end or not np.isfinite(cost_bp) or cost_bp < 0:
        raise ValueError('Invalid time window or round-trip cost')
    if not isinstance(n_controls, (int, np.integer)) or n_controls < 1:
        raise ValueError('n_controls must be a positive integer')
    mi, bi = _index(minutes, 'minutes'), _index(bars, 'bars')
    if not features.index.equals(bars.index):
        raise ValueError('features index must match bars exactly')
    if not {'ready', 'volbin', 'md', 'atr'}.issubset(features.columns):
        raise ValueError('features requires ready, volbin, md and atr')
    if 'time_close' not in bars or not {'open', 'close'}.issubset(minutes.columns):
        raise ValueError('bars.time_close and minute open/close prices are required')
    decisions = pd.DatetimeIndex(pd.to_datetime(bars.time_close, utc=True))
    if decisions.hasnans or decisions.has_duplicates or not decisions.is_monotonic_increasing or not (decisions > bi).all():
        raise ValueError('Decision closes must be increasing and follow their bar opens')
    signals = []
    for name, values in [('entry', entry), ('exit_long', exit_long), ('exit_short', exit_short)]:
        if isinstance(values, pd.Series) and not values.index.equals(bars.index):
            raise ValueError(f'{name} Series index must match bars')
        a = np.asarray(values)
        if a.ndim != 1 or len(a) != len(bars) or not np.isin(a, [-1, 0, 1] if name == 'entry' else [False, True]).all():
            raise ValueError(f'Invalid {name} array')
        signals.append(a.astype(np.int8 if name == 'entry' else bool))
    entries, xl, xs = signals
    first = int(mi.searchsorted(start, side='left'))
    stop = max(first, int(mi.searchsorted(end - _MINUTE, side='right')))
    times = mi[first:stop]
    prices = minutes.iloc[first:stop][['open', 'close']].to_numpy(float)
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError('In-window minute prices must be finite and positive')
    if len(times) and (times.asi8 % _MINUTE.value).any():
        raise ValueError('Minute opens must lie on whole-minute boundaries')
    fill = times.searchsorted(decisions, side='left')
    within = (decisions >= start) & (decisions < end) & (fill < len(times))
    vol = features.volbin.to_numpy(float)
    eligible = within & features.ready.fillna(False).to_numpy(bool) & np.isfinite(vol) & (vol >= 1)
    months = decisions.strftime('%Y-%m')
    required = {'trade_id', 'decision_index', 'side', 'gross_bp', 'net_bp'}
    if len(ledger) and not required.issubset(ledger.columns):
        raise ValueError('ledger is missing actual trade identity or return columns')
    actual = ledger.copy() if len(ledger) else pd.DataFrame(columns=sorted(required))
    actual['score'] = np.nan
    if len(actual):
        positions = actual.decision_index.to_numpy(float)
        sides = actual.side.to_numpy(float)
        if (not np.isfinite(positions).all() or not np.equal(positions, np.floor(positions)).all()
                or (positions < 0).any() or (positions >= len(bars)).any() or not np.isin(sides, [-1, 1]).all()):
            raise ValueError('Invalid actual decision index or side')
        positions = positions.astype(int)
        if actual.trade_id.duplicated().any() or not eligible[positions].all() or not (entries[positions] == sides).all():
            raise ValueError('Actual trades must be unique, eligible and match entry requests')
        if not np.isfinite(actual[['gross_bp', 'net_bp']].to_numpy(float)).all():
            raise ValueError('Actual returns must be finite')
        score = features.md.abs() / features.atr.replace(0, np.nan)
        actual['score'] = score.to_numpy()[positions]
        actual['decision_index'] = positions
        actual = actual.sort_values(['decision_index', 'side', 'trade_id'], kind='stable')
    rng = np.random.default_rng(seed)
    pools: dict[tuple[str, int, int], list[int]] = {}
    for i in np.flatnonzero(eligible & (entries == 0)):
        for side in (-1, 1):
            pools.setdefault((months[i], int(vol[i]), side), []).append(int(i))
    for key in sorted(pools):
        rng.shuffle(pools[key])
    # Freeze every pairing before looking at any control exit or future price.
    assignments = []
    for row in actual.to_dict('records'):
        i, side = int(row['decision_index']), int(row['side'])
        pool = pools.get((months[i], int(vol[i]), side), [])
        if len(pool) >= n_controls:
            assignments.append((row, [pool.pop() for _ in range(n_controls)]))
    exit_indices = {1: np.flatnonzero(xl), -1: np.flatnonzero(xs)}
    pairs = []
    for row, controls in assignments:
        i, side = int(row['decision_index']), int(row['side'])
        for ordinal, control in enumerate(controls, 1):
            a = int(fill[control])
            sequence = exit_indices[side]
            place = int(sequence.searchsorted(control, side='right'))
            j = int(sequence[place]) if place < len(sequence) else None
            natural = j is not None and decisions[j] < end and fill[j] < len(times)
            z = int(fill[j]) if natural else len(times) - 1
            entry_price = float(prices[a, 0])
            exit_price = float(prices[z, 0 if natural else 1])
            gross = side * (exit_price / entry_price - 1) * 10000
            net = gross - cost_bp
            pairs.append(dict(
                actual_trade_id=row['trade_id'], actual_decision_index=i,
                actual_decision_time=decisions[i], month=months[i], volbin=int(vol[i]), side=side,
                score=row['score'], actual_gross_bp=float(row['gross_bp']), actual_net_bp=float(row['net_bp']),
                control_number=ordinal, control_decision_index=control, control_decision_time=decisions[control],
                control_entry_index=first+a, control_entry_time=times[a], control_entry_price=entry_price,
                control_exit_decision_index=j if natural else None,
                control_exit_decision_time=decisions[j] if natural else pd.NaT,
                control_exit_index=first+z, control_exit_time=times[z] if natural else times[z]+_MINUTE,
                control_exit_price=exit_price, control_exit_kind='signal' if natural else 'boundary',
                control_gross_bp=gross, control_net_bp=net,
                gross_excess_bp=float(row['gross_bp'])-gross, excess_bp=float(row['net_bp'])-net))
    pairs = pd.DataFrame(pairs, columns=_PAIR_COLUMNS)
    cases = (pairs.groupby('actual_trade_id', sort=False).agg(
        month=('month', 'first'), actual_gross_bp=('actual_gross_bp', 'first'),
        actual_net_bp=('actual_net_bp', 'first'), control_gross_bp=('control_gross_bp', 'mean'),
        control_net_bp=('control_net_bp', 'mean'), excess_bp=('excess_bp', 'mean'))
        if len(pairs) else pd.DataFrame())
    bankruptcy = int(actual.exit_reason.eq('bankruptcy').sum()) if 'exit_reason' in actual else 0
    floors = int(actual.bankruptcy_floor_adjustment.ne(0).sum()) if 'bankruptcy_floor_adjustment' in actual else 0
    summary = dict(
        actual_n=len(actual), matched_n=len(cases), unmatched_n=len(actual)-len(cases), controls_n=len(pairs),
        n_controls=n_controls, seed=seed, cost_bp=float(cost_bp), scope='event_level_not_portfolio',
        matching='XAUUSD / decision UTC month / causal volbin / actual direction; no bar-side reuse',
        actual_mean_gross_bp=float(actual.gross_bp.mean()) if len(actual) else None,
        actual_mean_net_bp=float(actual.net_bp.mean()) if len(actual) else None,
        matched_actual_mean_gross_bp=float(cases.actual_gross_bp.mean()) if len(cases) else None,
        matched_actual_mean_net_bp=float(cases.actual_net_bp.mean()) if len(cases) else None,
        control_mean_gross_bp=float(cases.control_gross_bp.mean()) if len(cases) else None,
        control_mean_net_bp=float(cases.control_net_bp.mean()) if len(cases) else None,
        excess_bp=float(cases.excess_bp.mean()) if len(cases) else None,
        actual_bankruptcy_n=bankruptcy, actual_floor_adjustment_n=floors,
        **_score_stats(actual), **_inference(cases, seed))
    if bankruptcy or floors:
        summary.update(p=None, ci_low=None, ci_high=None,
                       inference_status='actual_bankruptcy_rules_not_comparable_to_linear_controls')
    summary['paired_p'] = summary['p']
    summary['top_decile_gross_bp'] = summary['score_top_decile_gross_bp']
    summary['top_decile_net_bp'] = summary['score_top_decile_net_bp']
    return {'summary': summary, 'pairs': pairs}
