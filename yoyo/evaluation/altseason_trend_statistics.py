"""Outcome-blind matching and monthly inference for fixed altseason policies.

Contract: exp-altseason-donchian-ewmac-20260910-v1/PROJECT_PLAN.md.
Matching reads only bar-open timestamps, causal volatility buckets and regimes;
decision time is bar open + four hours. Inference first averages all finite
paired effects within each UTC month, giving months equal weight. This module
never reads prices from disk and never selects a strategy or changes its gates.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd
from scipy.stats import rankdata


def _finite_mean(values, *, median=False):
    a = np.asarray(values, dtype=float)
    a = a[np.isfinite(a)]
    return float(np.median(a) if median else a.mean()) if len(a) else None


def _numbers(frame, name):
    if name not in frame:
        return np.full(len(frame), np.nan)
    return pd.to_numeric(frame[name], errors="coerce").to_numpy(dtype=float)


def _flag(frame, name):
    if name not in frame:
        return np.zeros(len(frame), dtype=bool)
    return frame[name].fillna(False).eq(True).to_numpy(dtype=bool)


def match_indexes(features, candidate_indexes, eligible_indexes, seed):
    """Return at most one distinct, nonself control per positional candidate.

    Features require vol_bucket and regime plus UTC-aware 4H OPEN timestamps.
    Candidate membership does not exclude that position from control pools.
    Missing matching fields never produce matches; unknown regime is a valid
    stratum. No outcome, signal-strength or future-return column is read.
    """
    if not isinstance(features.index, pd.DatetimeIndex) or features.index.tz is None:
        raise ValueError("features require timezone-aware bar-open timestamps")
    if not features.index.is_unique or not features.index.is_monotonic_increasing:
        raise ValueError("feature timestamps must be unique and ordered")
    if not {"vol_bucket", "regime"}.issubset(features.columns):
        raise ValueError("features require vol_bucket and regime")
    groups = defaultdict(list)
    candidates = list(candidate_indexes)
    eligible = list(eligible_indexes)
    for indexes in (candidates, eligible):
        if any(isinstance(i, (bool, np.bool_)) or not isinstance(i, (int, np.integer))
               or not 0 <= i < len(features) for i in indexes):
            raise ValueError("indexes must be valid integer positions")
        if len(indexes) != len(set(indexes)):
            raise ValueError("indexes must not contain duplicates")
    times = features.index.tz_convert("UTC") + pd.Timedelta(hours=4)
    buckets = features.vol_bucket.to_numpy()
    regimes = features.regime.to_numpy()

    def key(i):
        if pd.isna(buckets[i]) or pd.isna(regimes[i]):
            return None
        if regimes[i] not in {"strong", "other", "unknown"}:
            raise ValueError("regime must be strong, other or unknown")
        bucket = float(buckets[i])
        if not np.isfinite(bucket) or not 0 <= bucket <= 4 or bucket != int(bucket):
            return None
        return (times[i].strftime("%Y-%m"), buckets[i], regimes[i], times[i].hour)

    pools = defaultdict(list)
    for i in sorted(eligible):
        k = key(i)
        if k is not None:
            pools[k].append(int(i))
    result = {int(i): None for i in candidates}
    for i in sorted(candidates):
        k = key(i)
        if k is not None:
            groups[k].append(int(i))
    rng = np.random.default_rng(seed)
    for k in sorted(groups, key=str):
        order = np.asarray(groups[k], dtype=int)
        available = list(pools[k])
        rng.shuffle(order)
        rng.shuffle(available)
        assigned = []
        for value in order:
            i = int(value)
            choices = [n for n, j in enumerate(available) if j != i]
            if choices:
                result[i] = available.pop(choices[-1])
                assigned.append(i)
            elif available and assigned:
                # Repair an avoidable final self-match by swapping an earlier
                # assignment. Distinct candidates make both links nonself.
                previous = assigned[0]
                result[i] = result[previous]
                result[previous] = available.pop()
                assigned.append(i)
    return result


def paired_block_inference(values, blocks, seed, n_resamples=9999):
    """Equal-month mean, month bootstrap CI and one-sided sign-flip p.

    Exact sign flips enumerate all 2**B patterns for <=16 months; otherwise
    Monte Carlo uses the +1 correction. Fewer than six months has no inferential
    p-value. Its bootstrap interval remains a descriptive resampling interval.
    Zero monthly effects remain in the sample, including exact p=1 when all
    effects vanish. Nonfinite effects or missing block identifiers are omitted.
    """
    if isinstance(n_resamples, bool) or not isinstance(n_resamples, (int, np.integer)) or n_resamples < 1:
        raise ValueError("n_resamples must be a positive integer")
    v = np.asarray(values, dtype=float)
    b = np.asarray(blocks, dtype=object)
    if v.ndim != 1 or b.ndim != 1 or len(v) != len(b):
        raise ValueError("values and blocks must be equal-length vectors")
    valid = np.isfinite(v) & pd.notna(b)
    grouped = pd.DataFrame({"value": v[valid], "block": b[valid]})
    means = grouped.groupby("block", sort=True).value.mean().to_numpy(dtype=float)
    n = len(means)
    result = dict(n_pairs=int(valid.sum()), n_blocks=n, mean=None, ci_low=None,
                  ci_high=None, p=None, method="insufficient_months")
    if not n:
        return result
    observed = float(means.mean())
    rng = np.random.default_rng(seed)
    bootstrap = means[rng.integers(0, n, size=(n_resamples, n))].mean(axis=1)
    lo, hi = np.quantile(bootstrap, [.025, .975])
    result.update(mean=observed, ci_low=float(lo), ci_high=float(hi))
    if n < 6:
        return result
    tolerance = np.finfo(float).eps * max(float(np.abs(means).max()), 1e-300) * n * 8
    if n <= 16:
        patterns = np.arange(2 ** n, dtype=np.uint32)[:, None]
        signs = 2 * ((patterns >> np.arange(n, dtype=np.uint32)) & 1).astype(float) - 1
        sampled = (signs * means).mean(axis=1)
        result.update(p=float(np.count_nonzero(sampled >= observed - tolerance) / len(sampled)),
                      method="exact_month_sign_flip")
    else:
        signs = rng.choice(np.asarray([-1., 1.]), size=(n_resamples, n))
        sampled = (signs * means).mean(axis=1)
        result.update(p=float((1 + np.count_nonzero(sampled >= observed - tolerance)) / (n_resamples + 1)),
                      method="monte_carlo_month_sign_flip_plus_one")
    return result


def risk_sized_return(net_return, initial_risk_frac):
    """Return on initial sleeve equity using fixed 1% risk and cash fee cap."""
    value, risk = np.broadcast_arrays(np.asarray(net_return, dtype=float),
                                     np.asarray(initial_risk_frac, dtype=float))
    valid = np.isfinite(value) & np.isfinite(risk) & (risk > 0)
    result = np.full(value.shape, np.nan)
    result[valid] = np.minimum(.01 / risk[valid], 1 / 1.001) * value[valid]
    return float(result) if result.ndim == 0 else result


def event_descriptives(events):
    """Describe valid events; only natural exits contribute win rate and PF.

    Gross/net, holding and excursion summaries include boundary marks, with
    their counts explicit. Winner concentration is the largest three positive
    net returns divided by all positive net returns; the removal diagnostic
    deletes only those actual positive winners, never arbitrary losing rows.
    """
    valid = _flag(events, "valid")
    natural = valid & _flag(events, "natural_exit") & ~_flag(events, "censored")
    selected = events.loc[valid]
    gross, net = _numbers(selected, "gross_return"), _numbers(selected, "net_return")
    natural_net = _numbers(events.loc[natural], "net_return")
    natural_net = natural_net[np.isfinite(natural_net)]
    profit = float(natural_net[natural_net > 0].sum())
    loss = float(-natural_net[natural_net < 0].sum())
    result = dict(n=len(events), n_valid=int(valid.sum()), n_natural=int(natural.sum()),
                  n_censored=int((valid & _flag(events, "censored")).sum()),
                  mean_gross_bp=_finite_mean(gross * 1e4), median_gross_bp=_finite_mean(gross * 1e4, median=True),
                  mean_net_bp=_finite_mean(net * 1e4), median_net_bp=_finite_mean(net * 1e4, median=True),
                  win_rate=float((natural_net > 0).mean()) if len(natural_net) else None,
                  profit_factor=profit / loss if loss > 0 else None)
    for source, target, scale in [("hold_hours", "hold_hours", 1), ("mfe_return", "mfe_bp", 1e4),
                                  ("mae_return", "mae_bp", 1e4), ("capture_ratio", "capture_ratio", 1)]:
        values = _numbers(selected, source) * scale
        result["mean_" + target] = _finite_mean(values)
        result["median_" + target] = _finite_mean(values, median=True)
    finite = net[np.isfinite(net)]
    winners = np.flatnonzero(finite > 0)
    top = winners[np.argsort(-finite[winners], kind="stable")[:3]]
    positive_total = float(finite[winners].sum())
    result.update(top3_positive_profit_share=float(finite[top].sum() / positive_total) if positive_total else None,
                  net_ex_top3_mean_bp=_finite_mean(np.delete(finite, top) * 1e4))
    return result


def _months(events):
    if "month" in events:
        return events.month.to_numpy(dtype=object)
    if "decision_time" in events:
        times = pd.to_datetime(events.decision_time, utc=True)
    elif "decision_close_time" in events:
        times = pd.to_datetime(events.decision_close_time, utc=True)
    elif "signal_time" in events:
        # The engine's signal_time is already the decision CLOSE timestamp.
        times = pd.to_datetime(events.signal_time, utc=True)
    else:
        return np.full(len(events), None, dtype=object)
    return times.dt.strftime("%Y-%m").to_numpy(dtype=object)


def score_diagnostics(events, seed):
    """Natural-exit AUC and a descriptive score-selected top decile.

    Top-decile membership retains all ties at the nominal tenth-percent
    boundary, so a saturated forecast may select more than 10%. At least ten
    finite natural-exit observations are required. Its p is
    a monthly sign-flip test of paired, risk-sized random-entry excess, not an
    independent permutation of individual ranking labels. No future outcome
    is used to sort or resolve score ties.
    """
    score, net = _numbers(events, "score"), _numbers(events, "net_return")
    mask = (_flag(events, "valid") & _flag(events, "natural_exit") & ~_flag(events, "censored")
            & np.isfinite(score) & np.isfinite(net))
    scored = events.loc[mask]
    values = score[mask]
    positive = net[mask] > 0
    npos, nneg = int(positive.sum()), int((~positive).sum())
    result = dict(n_scored=len(scored), n_positive=npos, n_negative=nneg, auc=None,
                  top_n=0, top_mean_gross_bp=None, top_mean_net_bp=None,
                  top_matched_n=0, top_mean_matched_excess_bp=None,
                  top_mean_risk_sized_excess=None, top_p=None, top_n_blocks=0,
                  top_ci_low=None, top_ci_high=None,
                  top_test_description="Top-decile monthly paired risk-sized random-entry excess; not ranking-label permutation",
                  reason="")
    reasons = []
    if npos and nneg:
        ranks = rankdata(values, method="average")
        result["auc"] = float((ranks[positive].sum() - npos * (npos + 1) / 2) / (npos * nneg))
    else:
        reasons.append("AUC requires both positive and nonpositive natural exits")
    if len(scored) < 10:
        reasons.append("Top decile requires at least ten scored natural exits")
        result["reason"] = "; ".join(reasons)
        return result
    threshold = np.sort(values)[-math.ceil(len(scored) * .1)]
    top = scored.loc[values >= threshold]
    excess = _numbers(top, "net_return") - _numbers(top, "matched_net_return")
    risk_excess = _numbers(top, "risk_sized_excess")
    matched = np.isfinite(excess)
    result.update(top_n=len(top), top_mean_gross_bp=_finite_mean(_numbers(top, "gross_return") * 1e4),
                  top_mean_net_bp=_finite_mean(_numbers(top, "net_return") * 1e4),
                  top_matched_n=int(matched.sum()), top_mean_matched_excess_bp=_finite_mean(excess * 1e4))
    inference = paired_block_inference(risk_excess, _months(top), seed)
    result.update(top_mean_risk_sized_excess=inference["mean"], top_p=inference["p"],
                  top_n_blocks=inference["n_blocks"], top_ci_low=inference["ci_low"], top_ci_high=inference["ci_high"])
    if inference["n_blocks"] < 6:
        reasons.append("Top-decile inference requires at least six matched months")
    result["reason"] = "; ".join(reasons)
    return result


def holm(pvalues, total_tests=None):
    """Holm adjustment retaining predeclared tests whose p is unavailable."""
    values = list(pvalues)
    total = len(values) if total_tests is None else total_tests
    if isinstance(total, bool) or not isinstance(total, (int, np.integer)) or total < len(values):
        raise ValueError("total_tests must be an integer no smaller than the submitted family")
    result = [None] * len(values)
    available = []
    for i, value in enumerate(values):
        if value is None or pd.isna(value):
            continue
        if not np.isfinite(value) or not 0 <= value <= 1:
            raise ValueError("p-values must be in [0, 1] or missing")
        available.append((float(value), i))
    previous = 0.
    for rank, (value, i) in enumerate(sorted(available)):
        previous = max(previous, min(1., (total - rank) * value))
        result[i] = previous
    return result
