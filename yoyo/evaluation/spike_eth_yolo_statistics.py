"""Descriptive statistics for the frozen ETH V9 entry-time YOLO study.

Source: ``exp-spike-eth-v9-yolo-entry-20260915-v1/PROJECT_PLAN.md``.  This
module is deliberately an aggregation-only boundary: it reads the producer's
three saved CSV ledgers and never opens OHLCV, model weights, or replay code.
The hit associations and score diagnostics are observational descriptions,
not threshold selection or production admission evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


ARMS = ("v9", "v9_same", "v9_lower", "v9_both")
DEFAULT_TIMEFRAMES = ("3m", "5m", "15m", "30m", "1H", "4H", "1Dutc")
GROUPS = ("all", "same_hit", "same_miss", "lower_hit", "lower_miss", "both_hit")
SPLIT = pd.Timestamp("2026-08-14T16:00:00Z")


def sha256(path: Path) -> str:
    """Return the SHA-256 digest without interpreting a source ledger."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_seed(seed: int, *parts: str) -> int:
    """Derive order-independent random streams for fixed report cells."""
    text = "|".join((str(seed), *map(str, parts))).encode("utf-8")
    return int.from_bytes(hashlib.sha256(text).digest()[:8], "big")


def _as_bool(values: pd.Series, name: str) -> pd.Series:
    """Parse saved boolean CSV columns without treating arbitrary text as true."""
    if values.dtype == bool:
        return values
    lowered = values.astype(str).str.strip().str.lower()
    mapping = {"true": True, "1": True, "false": False, "0": False, "": False, "nan": False}
    unknown = ~lowered.isin(mapping)
    if unknown.any():
        raise ValueError(f"{name} has invalid booleans: {sorted(lowered[unknown].unique())[:3]}")
    return lowered.map(mapping).astype(bool)


def _rename_detection_columns(signals: pd.DataFrame) -> pd.DataFrame:
    """Accept the producer's underscore schema and its documented compact spelling."""
    aliases = {
        "samehit": "same_hit", "lowerhit": "lower_hit",
        "samescore": "same_score", "lowerscore": "lower_score",
    }
    result = signals.rename(columns={old: new for old, new in aliases.items() if old in signals and new not in signals})
    required = {"event_key", "timeframe", "same_hit", "lower_hit", "same_score", "lower_score", "rv"}
    missing = sorted(required.difference(result.columns))
    if missing:
        raise ValueError(f"signals_detected.csv missing columns {missing}")
    return result


def _read_ledgers(output_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, str]]:
    """Read exactly the three producer artifacts permitted by this study plan."""
    names = ("trades.csv", "controls.csv", "signals_detected.csv")
    paths = {name: output_dir / name for name in names}
    absent = [name for name, path in paths.items() if not path.is_file()]
    if absent:
        raise FileNotFoundError(f"missing producer result files: {absent}")
    trades = pd.read_csv(paths["trades.csv"])
    controls = pd.read_csv(paths["controls.csv"])
    signals = _rename_detection_columns(pd.read_csv(paths["signals_detected.csv"]))
    return trades, controls, signals, {name: sha256(path) for name, path in paths.items()}


def _validate_and_prepare(
    trades: pd.DataFrame, controls: pd.DataFrame, signals: pd.DataFrame, timeframes: Sequence[str]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    required_trades = {
        "arm", "event_key", "timeframe", "side", "entry_time", "signal_i", "net_r", "gross_r",
        "net_return", "gross_return", "initial_risk_frac", "censored",
    }
    required_controls = {"arm", "event_key", "entry_time", "matched", "target_net_r", "control_net_r"}
    for name, frame, required in (("trades.csv", trades, required_trades), ("controls.csv", controls, required_controls)):
        missing = sorted(required.difference(frame.columns))
        if missing:
            raise ValueError(f"{name} missing columns {missing}")

    trades = trades.copy()
    controls = controls.copy()
    signals = signals.copy()
    for frame, label in ((trades, "trades"), (controls, "controls"), (signals, "signals")):
        frame["event_key"] = frame.event_key.astype(str)
        if frame.event_key.eq("").any() or frame.event_key.eq("nan").any():
            raise ValueError(f"{label} has an empty event_key")
    for frame, label in ((trades, "trades"), (controls, "controls")):
        frame["arm"] = frame.arm.astype(str)
        if frame.duplicated(["arm", "event_key"]).any():
            raise ValueError(f"{label} has duplicate (arm, event_key) rows")
    if signals.duplicated("event_key").any():
        raise ValueError("signals_detected.csv has duplicate event_key rows")
    if not set(trades.arm).issubset(ARMS) or not set(controls.arm).issubset(ARMS):
        raise ValueError("unexpected replay arm")
    observed_timeframes = set(trades.timeframe.astype(str)).union(set(signals.timeframe.astype(str)))
    unexpected_timeframes = sorted(observed_timeframes.difference(timeframes))
    if unexpected_timeframes:
        raise ValueError(f"saved ledgers contain unexpected timeframes {unexpected_timeframes}")

    for frame, column in ((trades, "censored"), (controls, "matched"), (signals, "same_hit"), (signals, "lower_hit")):
        frame[column] = _as_bool(frame[column], column)
    for frame, columns in ((trades, ("net_r", "gross_r", "net_return", "gross_return", "initial_risk_frac")),
                           (controls, ("target_net_r", "control_net_r")),
                           (signals, ("same_score", "lower_score", "rv"))):
        for column in columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    for frame, label in ((trades, "trades"), (controls, "controls")):
        frame["entry_time"] = pd.to_datetime(frame.entry_time, utc=True, errors="raise")
        if frame.entry_time.isna().any():
            raise ValueError(f"{label} has an invalid entry_time")
    signals["timeframe"] = signals.timeframe.astype(str)
    trades["timeframe"] = trades.timeframe.astype(str)
    if not np.isfinite(trades.loc[~trades.censored, ["net_r", "gross_r", "net_return", "gross_return"]]).all().all():
        raise ValueError("a closed trade has a non-finite outcome")

    signal_cols = ["event_key", "timeframe", "same_hit", "lower_hit", "same_score", "lower_score", "rv"]
    if "side" in signals:
        signal_cols.append("side")
    merged = trades.merge(signals[signal_cols], on="event_key", how="left", suffixes=("", "_signal"), validate="many_to_one")
    if merged.timeframe_signal.isna().any():
        raise ValueError("trade event missing its saved detection record")
    if not merged.timeframe.eq(merged.timeframe_signal).all():
        raise ValueError("trade/detection timeframe identity drift")
    merged = merged.drop(columns="timeframe_signal")
    if "side_signal" in merged and not merged.side.astype(str).eq(merged.side_signal.astype(str)).all():
        raise ValueError("trade/detection side identity drift")
    merged = merged.drop(columns=[column for column in ("side_signal",) if column in merged])
    for column in ("same_hit", "lower_hit", "same_score", "lower_score", "rv"):
        saved = f"{column}_signal"
        if saved not in merged:
            continue
        if column in trades:
            if column.endswith("_hit"):
                equal = merged[column].eq(merged[saved])
            else:
                current, canonical = merged[column].to_numpy(float), merged[saved].to_numpy(float)
                equal = pd.Series(np.isclose(current, canonical, rtol=1e-12, atol=1e-12, equal_nan=True), index=merged.index)
            if not equal.all():
                raise ValueError(f"trade/detection {column} identity drift")
        # The detection ledger is canonical even where old producer columns agree.
        merged[column] = merged[saved]
        merged = merged.drop(columns=saved)

    keys = pd.MultiIndex.from_frame(merged[["arm", "event_key"]])
    control_keys = pd.MultiIndex.from_frame(controls[["arm", "event_key"]])
    if not keys.isin(control_keys).all():
        raise ValueError("a trade is missing its saved control row")
    paired_target = merged[["arm", "event_key", "net_r"]].merge(
        controls[["arm", "event_key", "target_net_r"]], on=["arm", "event_key"], how="left", validate="one_to_one"
    )
    comparable = np.isfinite(paired_target.net_r) & np.isfinite(paired_target.target_net_r)
    if comparable.any() and not np.allclose(paired_target.loc[comparable, "net_r"], paired_target.loc[comparable, "target_net_r"], rtol=1e-9, atol=1e-9):
        raise ValueError("control target_net_r no longer matches its trade")
    return merged, controls, signals


def wilson95(successes: int, total: int) -> tuple[float | None, float | None]:
    """Two-sided 95% Wilson interval for a closed-trade win proportion."""
    if total <= 0:
        return None, None
    z = 1.959963984540054
    p = successes / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def rank_auc(scores: Sequence[float], labels: Sequence[bool]) -> float | None:
    """AUC from average ranks, preserving score ties without sklearn."""
    score = np.asarray(scores, dtype=float)
    label = np.asarray(labels, dtype=bool)
    valid = np.isfinite(score)
    score, label = score[valid], label[valid]
    return _rank_auc_array(score, label)


def _rank_auc_array(score: np.ndarray, label: np.ndarray) -> float | None:
    """Rank AUC for already finite score arrays, optimized for permutation loops."""
    positive = int(label.sum())
    negative = len(label) - positive
    if not len(score) or not positive or not negative:
        return None
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score), dtype=float)
    start = 0
    while start < len(score):
        end = start + 1
        while end < len(score) and score[order[end]] == score[order[start]]:
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2
        start = end
    u = ranks[label].sum() - positive * (positive + 1) / 2
    return float(u / (positive * negative))


def _profit_factor(values: pd.Series) -> float | None:
    positive = float(values.clip(lower=0).sum())
    negative = float((-values.clip(upper=0)).sum())
    return positive / negative if negative > 0 else None


def _controls_for(part: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    """Return only closed, finite matched pairs for the exact reported cohort."""
    keys = part[["arm", "event_key", "censored"]].copy()
    matched = keys.merge(controls, on=["arm", "event_key"], how="left", validate="one_to_one")
    valid = (~matched.censored) & matched.matched & np.isfinite(matched.target_net_r) & np.isfinite(matched.control_net_r)
    return matched.loc[valid].copy()


def summarize(part: pd.DataFrame, controls: pd.DataFrame) -> dict[str, Any]:
    """Use censored rows for coverage only and paired rows for paired means only."""
    closed = part.loc[~part.censored]
    wins = int(closed.net_r.gt(0).sum())
    lower, upper = wilson95(wins, len(closed))
    pairs = _controls_for(part, controls)
    excess = pairs.target_net_r - pairs.control_net_r
    return {
        "n": int(len(part)), "closed": int(len(closed)), "censored": int(part.censored.sum()),
        "net_wins": wins, "net_winrate": (float(wins / len(closed)) if len(closed) else None),
        "net_winrate_wilson95_low": lower, "net_winrate_wilson95_high": upper,
        "mean_net_r": (float(closed.net_r.mean()) if len(closed) else None),
        "total_net_r": (float(closed.net_r.sum()) if len(closed) else None),
        "mean_gross_r": (float(closed.gross_r.mean()) if len(closed) else None),
        "total_gross_r": (float(closed.gross_r.sum()) if len(closed) else None),
        "pf_net_r": _profit_factor(closed.net_r),
        "mean_net_bp": (float(closed.net_return.mean() * 10_000) if len(closed) else None),
        "mean_gross_bp": (float(closed.gross_return.mean() * 10_000) if len(closed) else None),
        "matched_pairs": int(len(pairs)), "unmatched": int(len(part) - len(pairs)),
        "paired_target_mean_r": (float(pairs.target_net_r.mean()) if len(pairs) else None),
        "paired_control_mean_r": (float(pairs.control_net_r.mean()) if len(pairs) else None),
        "paired_excess_mean_r": (float(excess.mean()) if len(pairs) else None),
    }


def _group_mask(part: pd.DataFrame, group: str) -> pd.Series:
    if group == "all":
        return pd.Series(True, index=part.index)
    if group == "same_hit":
        return part.same_hit
    if group == "same_miss":
        return ~part.same_hit
    if group == "lower_hit":
        return part.lower_hit
    if group == "lower_miss":
        return ~part.lower_hit
    if group == "both_hit":
        return part.same_hit & part.lower_hit
    raise ValueError(group)


def make_summary(trades: pd.DataFrame, controls: pd.DataFrame, timeframes: Sequence[str]) -> pd.DataFrame:
    """Report baseline detector cohorts plus the four serial admission arms."""
    rows: list[dict[str, Any]] = []
    baseline = trades.loc[trades.arm.eq("v9")]
    for timeframe in timeframes:
        base_tf = baseline.loc[baseline.timeframe.eq(timeframe)]
        for group in GROUPS:
            rows.append({"population": "v9_detection_group", "arm": "v9", "timeframe": timeframe, "group": group,
                         **summarize(base_tf.loc[_group_mask(base_tf, group)], controls)})
        for arm in ARMS:
            part = trades.loc[trades.arm.eq(arm) & trades.timeframe.eq(timeframe)]
            rows.append({"population": "serial_arm", "arm": arm, "timeframe": timeframe, "group": "all",
                         **summarize(part, controls)})
    return pd.DataFrame(rows)


def make_descriptives(trades: pd.DataFrame, controls: pd.DataFrame, timeframes: Sequence[str]) -> pd.DataFrame:
    """Fixed direction and clock-halves for baseline V9, with no post-hoc split search."""
    rows: list[dict[str, Any]] = []
    baseline = trades.loc[trades.arm.eq("v9")]
    for timeframe in timeframes:
        part = baseline.loc[baseline.timeframe.eq(timeframe)]
        for label, mask in (("long", part.side.eq(1) | part.side.astype(str).str.lower().eq("long")),
                            ("short", part.side.eq(-1) | part.side.astype(str).str.lower().eq("short")),
                            ("half1", part.entry_time < SPLIT), ("half2", part.entry_time >= SPLIT)):
            rows.append({"arm": "v9", "timeframe": timeframe,
                         "slice_type": "direction" if label in {"long", "short"} else "chronological_half",
                         "slice": label, "split_utc": SPLIT.isoformat(), **summarize(part.loc[mask], controls)})
    return pd.DataFrame(rows)


def _log_choose(n: int, k: int) -> float:
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float | None:
    """Two-sided Fisher exact p value for the displayed 2x2 table."""
    if min(a, b, c, d) < 0:
        raise ValueError("Fisher table cannot be negative")
    row1, row2, col1 = a + b, c + d, a + c
    total = row1 + row2
    if not total or not row1 or not row2 or not col1 or col1 == total:
        return None
    low, high = max(0, col1 - row2), min(row1, col1)
    denominator = _log_choose(total, col1)
    probabilities = [(x, math.exp(_log_choose(row1, x) + _log_choose(row2, col1 - x) - denominator)) for x in range(low, high + 1)]
    observed = dict(probabilities)[a]
    return float(min(1.0, sum(probability for _, probability in probabilities if probability <= observed + 1e-12)))


def _week_id(times: pd.Series) -> pd.Series:
    """Return ISO labels aligned to the original event rows, not a new clock index."""
    iso = pd.DatetimeIndex(times).isocalendar()
    labels = (iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2)).to_numpy()
    return pd.Series(labels, index=times.index)


def week_block_bootstrap(
    frame: pd.DataFrame, hit_column: str, *, draws: int, seed: int
) -> dict[str, Any]:
    """Resample complete UTC ISO-week blocks for hit-minus-miss win-rate CIs."""
    if draws <= 0:
        raise ValueError("bootstrap_draws must be positive")
    grouped = frame.assign(_week=_week_id(frame.entry_time)).groupby("_week", sort=True)
    blocks = list(grouped)
    if not blocks:
        return {"bootstrap_blocks": 0, "bootstrap_draws": draws, "bootstrap_valid_draws": 0,
                "bootstrap_invalid_draws": draws, "bootstrap_ci_low": None, "bootstrap_ci_high": None}
    counts = np.asarray([
        (int(group[hit_column].sum()), int(group.loc[group[hit_column], "net_r"].gt(0).sum()),
         int((~group[hit_column]).sum()), int(group.loc[~group[hit_column], "net_r"].gt(0).sum()))
        for _, group in blocks
    ], dtype=np.int64)
    rng = np.random.default_rng(seed)
    choices = rng.integers(0, len(counts), size=(draws, len(counts)))
    totals = counts[choices].sum(axis=1)
    valid = (totals[:, 0] > 0) & (totals[:, 2] > 0)
    estimates = totals[valid, 1] / totals[valid, 0] - totals[valid, 3] / totals[valid, 2]
    ci = np.quantile(estimates, [.025, .975]) if len(estimates) else (np.nan, np.nan)
    return {"bootstrap_blocks": len(blocks), "bootstrap_draws": draws, "bootstrap_valid_draws": int(len(estimates)),
            "bootstrap_invalid_draws": int(draws - len(estimates)), "bootstrap_ci_low": (float(ci[0]) if np.isfinite(ci[0]) else None),
            "bootstrap_ci_high": (float(ci[1]) if np.isfinite(ci[1]) else None)}


def holm14(values: Sequence[float | None]) -> list[float | None]:
    """Holm-adjust 14 predeclared associations, retaining unavailable slots."""
    result: list[float | None] = [None] * len(values)
    valid = [(index, float(value)) for index, value in enumerate(values) if value is not None and np.isfinite(value)]
    running = 0.0
    for rank, (index, value) in enumerate(sorted(valid, key=lambda item: item[1])):
        running = max(running, min(1.0, value * (14 - rank)))
        result[index] = running
    return result


def make_associations(trades: pd.DataFrame, timeframes: Sequence[str], *, draws: int, seed: int) -> pd.DataFrame:
    """Compute the predeclared 7x2 hit association family on closed baseline trades."""
    rows: list[dict[str, Any]] = []
    baseline = trades.loc[trades.arm.eq("v9") & ~trades.censored]
    for timeframe in timeframes:
        part = baseline.loc[baseline.timeframe.eq(timeframe)]
        for detector, hit_column in (("same", "same_hit"), ("lower", "lower_hit")):
            hit = part.loc[part[hit_column]]
            miss = part.loc[~part[hit_column]]
            a, b = int(hit.net_r.gt(0).sum()), int(hit.net_r.le(0).sum())
            c, d = int(miss.net_r.gt(0).sum()), int(miss.net_r.le(0).sum())
            estimable = bool(len(hit) and len(miss) and (a + c) and (b + d))
            fisher = fisher_two_sided(a, b, c, d) if estimable else None
            row = {"arm": "v9", "timeframe": timeframe, "detector": detector, "n_closed": int(len(part)),
                   "n_hit": int(len(hit)), "n_miss": int(len(miss)), "hit_win": a, "hit_loss": b,
                   "miss_win": c, "miss_loss": d,
                   "hit_winrate": (float(a / len(hit)) if len(hit) else None),
                   "miss_winrate": (float(c / len(miss)) if len(miss) else None),
                   "winrate_difference_hit_minus_miss": (float(a / len(hit) - c / len(miss)) if len(hit) and len(miss) else None),
                   "fisher_two_sided_p": fisher, "association_available": estimable,
                   **week_block_bootstrap(part, hit_column, draws=draws, seed=_stable_seed(seed, "bootstrap", timeframe, detector))}
            rows.append(row)
    result = pd.DataFrame(rows)
    result["holm14_p"] = holm14(result.fisher_two_sided_p.tolist())
    return result


def _top_decile(part: pd.DataFrame, score_column: str) -> pd.DataFrame:
    """Use event_key only to make a tied score boundary deterministic."""
    n = max(1, int(math.ceil(len(part) * .10)))
    return part.sort_values([score_column, "event_key"], ascending=[False, True], kind="mergesort").iloc[:n].copy()


def score_permutation_p(
    part: pd.DataFrame, score_column: str, *, draws: int, seed: int
) -> float | None:
    """One-sided AUC permutation p-value, shuffling fixed scores within direction."""
    observed = rank_auc(part[score_column], part.net_r.gt(0))
    if observed is None:
        return None
    scores = part[score_column].to_numpy(float)
    labels = part.net_r.gt(0).to_numpy(bool)
    sides = part.side.astype(str).to_numpy()
    groups = [np.flatnonzero(sides == side) for side in sorted(set(sides))]
    rng = np.random.default_rng(seed)
    greater = 0
    for _ in range(draws):
        shuffled = scores.copy()
        for indices in groups:
            shuffled[indices] = rng.permutation(shuffled[indices])
        value = _rank_auc_array(shuffled, labels)
        greater += bool(value is not None and value >= observed - 1e-15)
    return float((greater + 1) / (draws + 1))


def make_ranking(trades: pd.DataFrame, controls: pd.DataFrame, timeframes: Sequence[str], *, draws: int, seed: int) -> pd.DataFrame:
    """Describe fixed YOLO/RV ranking on actual closed baseline trades only."""
    rows: list[dict[str, Any]] = []
    baseline = trades.loc[trades.arm.eq("v9") & ~trades.censored]
    for timeframe in timeframes:
        tf = baseline.loc[baseline.timeframe.eq(timeframe)]
        for feature in ("same_score", "lower_score", "rv"):
            part = tf.loc[np.isfinite(tf[feature])].copy()
            auc = rank_auc(part[feature], part.net_r.gt(0))
            constant = bool(len(part) and part[feature].nunique(dropna=True) <= 1)
            available = bool(auc is not None and not constant)
            top = _top_decile(part, feature) if len(part) else part
            pairs = _controls_for(top, controls) if len(top) else pd.DataFrame()
            cutoff = float(top[feature].iloc[-1]) if len(top) else None
            cutoff_total = int(part[feature].eq(cutoff).sum()) if cutoff is not None else 0
            cutoff_selected = int(top[feature].eq(cutoff).sum()) if cutoff is not None else 0
            rows.append({"arm": "v9", "timeframe": timeframe, "feature": feature, "n_closed": int(len(tf)),
                         "n_scored": int(len(part)), "positive_net": int(part.net_r.gt(0).sum()),
                         "negative_or_zero_net": int(part.net_r.le(0).sum()), "ranking_available": available,
                         "unavailable_reason": ("constant_score" if constant else "no_binary_outcome" if auc is None else None),
                         "auc_rank": (auc if available else None), "top_decile_n": int(len(top)),
                         "top_cutoff_score": cutoff, "top_cutoff_tie_total": cutoff_total,
                         "top_cutoff_tie_selected": cutoff_selected,
                         "top_mean_gross_r": (float(top.gross_r.mean()) if len(top) else None),
                         "top_mean_net_r": (float(top.net_r.mean()) if len(top) else None),
                         "top_mean_gross_bp": (float(top.gross_return.mean() * 10_000) if len(top) else None),
                         "top_mean_net_bp": (float(top.net_return.mean() * 10_000) if len(top) else None),
                         "top_matched_pairs": int(len(pairs)),
                         "top_matched_random_excess_mean_r": (float((pairs.target_net_r - pairs.control_net_r).mean()) if len(pairs) else None),
                         "permutation_draws": draws,
                         "permutation_auc_p": (score_permutation_p(part, feature, draws=draws,
                            seed=_stable_seed(seed, "permutation", timeframe, feature)) if available else None)})
    return pd.DataFrame(rows)


def make_diagnostics(trades: pd.DataFrame, timeframes: Sequence[str]) -> pd.DataFrame:
    """Robust distribution diagnostics; intentionally no normality test is implied."""
    rows: list[dict[str, Any]] = []
    baseline = trades.loc[trades.arm.eq("v9") & ~trades.censored]
    for timeframe in timeframes:
        tf = baseline.loc[baseline.timeframe.eq(timeframe)]
        for group in GROUPS:
            subset = tf.loc[_group_mask(tf, group)]
            for metric, values in (("net_r", subset.net_r), ("gross_r", subset.gross_r),
                                   ("net_bp", subset.net_return * 10_000), ("gross_bp", subset.gross_return * 10_000)):
                values = values[np.isfinite(values)].astype(float)
                q1, q3 = (values.quantile(.25), values.quantile(.75)) if len(values) else (np.nan, np.nan)
                iqr = q3 - q1 if len(values) else np.nan
                lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
                outliers = int(((values < lower) | (values > upper)).sum()) if len(values) else 0
                rows.append({"arm": "v9", "timeframe": timeframe, "group": group, "metric": metric, "n": int(len(values)),
                             "mean": (float(values.mean()) if len(values) else None),
                             "sd": (float(values.std(ddof=1)) if len(values) > 1 else None),
                             "median": (float(values.median()) if len(values) else None),
                             "q1": (float(q1) if np.isfinite(q1) else None), "q3": (float(q3) if np.isfinite(q3) else None),
                             "iqr": (float(iqr) if np.isfinite(iqr) else None),
                             "outlier_lower_fence": (float(lower) if np.isfinite(lower) else None),
                             "outlier_upper_fence": (float(upper) if np.isfinite(upper) else None), "outlier_n": outliers})
    return pd.DataFrame(rows)


def make_attribution(trades: pd.DataFrame, timeframes: Sequence[str]) -> pd.DataFrame:
    """Attribute serial-arm 10R-winner changes and report event-R drawdown.

    Cumulative R is sorted by realized ``exit_time`` and is only a sequence of
    equal-risk events.  It deliberately is not a cash account, exposure, or
    portfolio drawdown calculation.
    """
    rows: list[dict[str, Any]] = []
    baseline = trades.loc[trades.arm.eq("v9") & ~trades.censored]
    for timeframe in timeframes:
        original = baseline.loc[baseline.timeframe.eq(timeframe)]
        original_winners = set(original.loc[original.net_r.ge(10), "event_key"])
        for arm in ARMS:
            closed = trades.loc[trades.arm.eq(arm) & trades.timeframe.eq(timeframe) & ~trades.censored]
            ordered = closed.sort_values(["exit_time", "event_key"], kind="mergesort").copy()
            cumulative = ordered.net_r.cumsum()
            # An event sequence begins at zero R, even when its first closed
            # trade loses.  Without this floor the first loss is invisible.
            peak = cumulative.cummax().clip(lower=0.)
            drawdown = peak - cumulative
            retained = original_winners.intersection(set(closed.event_key))
            lost = original_winners.difference(set(closed.event_key))
            added = set(closed.loc[closed.net_r.ge(10), "event_key"]).difference(original_winners)
            rows.append({"timeframe": timeframe, "arm": arm, "closed": int(len(closed)),
                         "net10_r_trades": int(closed.net_r.ge(10).sum()),
                         "original_v9_net10_r_trades": int(len(original_winners)),
                         "original_net10_r_retained": int(len(retained)),
                         "original_net10_r_lost": int(len(lost)), "new_net10_r_added": int(len(added)),
                         "max_cumulative_net_r_drawdown": (float(drawdown.max()) if len(drawdown) else None),
                         "drawdown_order": "closed trades sorted by exit_time,event_key",
                         "drawdown_unit": "event cumulative net R; not account or portfolio drawdown"})
    return pd.DataFrame(rows)


def make_coverage(trades: pd.DataFrame, signals: pd.DataFrame, timeframes: Sequence[str]) -> pd.DataFrame:
    """Describe saved detections separately from the V9 trades they occupied.

    The signal denominator intentionally contains every saved detection record,
    including a signal that did not become a V9 trade because a serial position
    was already open.  This is coverage, never an outcome filter.
    """
    rows: list[dict[str, Any]] = []
    baseline = trades.loc[trades.arm.eq("v9")]
    for timeframe in timeframes:
        detected = signals.loc[signals.timeframe.eq(timeframe)]
        actual = baseline.loc[baseline.timeframe.eq(timeframe)]
        row: dict[str, Any] = {"timeframe": timeframe, "signals_n": int(len(detected)),
                               "actual_v9_trades_n": int(len(actual))}
        for prefix, part in (("signal", detected), ("actual_v9", actual)):
            denominator = len(part)
            for label, column in (("same_hit", "same_hit"), ("lower_hit", "lower_hit"), ("both_hit", None)):
                count = int((part.same_hit & part.lower_hit).sum()) if column is None else int(part[column].sum())
                row[f"{prefix}_{label}_n"] = count
                row[f"{prefix}_{label}_rate"] = float(count / denominator) if denominator else None
        rows.append(row)
    return pd.DataFrame(rows)


def _finite_json(value: Any) -> Any:
    """Convert NumPy/pandas non-finite values to JSON null, never NaN tokens."""
    if isinstance(value, Mapping):
        return {str(key): _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    if value is pd.NA or (isinstance(value, pd.Timestamp) and pd.isna(value)):
        return None
    return value


def _load_config(config: Mapping[str, Any] | Path | str) -> dict[str, Any]:
    if isinstance(config, Mapping):
        return dict(config)
    return json.loads(Path(config).read_text())


def run(output_dir: Path | str, config: Mapping[str, Any] | Path | str) -> dict[str, Any]:
    """Aggregate already generated study ledgers into stable CSV/JSON statistics."""
    output = Path(output_dir)
    cfg = _load_config(config)
    timeframes = tuple(cfg.get("statistics_timeframes", cfg.get("lower_timeframes", {}).keys() or DEFAULT_TIMEFRAMES))
    if tuple(timeframes) != DEFAULT_TIMEFRAMES:
        raise ValueError("statistics require the frozen seven non-base timeframes in chronological order")
    draws = int(cfg.get("bootstrap_draws", 2000))
    permutations = int(cfg.get("permutations", 9999))
    seed = int(cfg.get("statistics_seed", 915152))
    if draws != 2000 or permutations != 9999:
        raise ValueError("frozen statistics require bootstrap_draws=2000 and permutations=9999")
    trades, controls, signals, input_hashes = _read_ledgers(output)
    trades, controls, _ = _validate_and_prepare(trades, controls, signals, timeframes)

    products = {
        "summary.csv": make_summary(trades, controls, timeframes),
        "descriptives.csv": make_descriptives(trades, controls, timeframes),
        "association.csv": make_associations(trades, timeframes, draws=draws, seed=seed),
        "ranking.csv": make_ranking(trades, controls, timeframes, draws=permutations, seed=seed),
        "diagnostics.csv": make_diagnostics(trades, timeframes),
        "attribution.csv": make_attribution(trades, timeframes),
        "coverage.csv": make_coverage(trades, signals, timeframes),
    }
    for name, frame in products.items():
        frame.to_csv(output / name, index=False)
    output_hashes = {name: sha256(output / name) for name in products}
    summary = _finite_json({
        "complete": True, "source": "frozen ETH V9 entry-time YOLO generated ledgers only",
        "input_files": {name: {"path": str(output / name), "sha256": digest} for name, digest in input_hashes.items()},
        "output_files": {name: {"path": str(output / name), "sha256": digest} for name, digest in output_hashes.items()},
        "configuration": {"statistics_timeframes": list(timeframes), "split_utc": SPLIT.isoformat(),
                          "statistics_seed": seed, "bootstrap_draws": draws, "permutations": permutations,
                          "holm_family_size": 14},
        "row_counts": {name: int(len(frame)) for name, frame in products.items()},
        "interpretation": "descriptive association and ranking diagnostics; no threshold/model selection or causal claim",
    })
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="producer results directory containing the three CSV ledgers")
    parser.add_argument("--config", required=True, type=Path, help="frozen experiment config JSON")
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.config), ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
