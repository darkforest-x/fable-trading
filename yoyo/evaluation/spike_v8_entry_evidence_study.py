"""Causal entry-evidence strata for the frozen two-year SPIKE V8 ledger.

This is a same-entry diagnostic: it authenticates the existing 3,531 V8 cache
streams, reads only OHLC, BB, and six-MA values at or before each V8 signal
confirmation close, and joins unchanged frozen V8 outcomes afterwards.  It
never reconstructs V6/V8 masks, changes costs or exits, fetches data, or turns
future HTF bars into a signal-time feature.

New labels are deliberately independent: a directional order run inside the
most recent eligible BB episode, BB/MA-width overlap in that episode, and a
completed adverse higher-timeframe background.  Missing BB/HTF evidence remains
an explicit unknown coverage state, never a negative label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_exit_policy_study import load_verified_stream


EXP = Path("experiments/active/exp-spike-v8-entry-evidence-20260914-v1")
CONFIG = EXP / "config.json"
PLAN = EXP / "PROJECT_PLAN.md"
HOLDOUT = EXP / "holdout_receipt.json"
TEST = Path("tests/evaluation/test_spike_v8_entry_evidence_study.py")
MA = ("s20", "e20", "s60", "e60", "s120", "e120")
MEMORY_BARS = 12
RUN_BARS = 3
MA_HISTORY = 256
MA_QUANTILE = 0.20
HTF_MAP = {30: 60, 60: 240, 240: 1440}


def sha256(path: Path) -> str:
    """Return the SHA-256 of one committed or frozen file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clean(paths: Iterable[Path]) -> bool:
    """Require official builders and their frozen definition files at HEAD."""
    root = Path.cwd().resolve()
    for path in paths:
        try:
            relative = path.resolve().relative_to(root)
        except ValueError:
            return False
        if subprocess.run(["git", "cat-file", "-e", f"HEAD:{relative}"], capture_output=True).returncode:
            return False
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(relative)]).returncode:
            return False
    return True


def _basic_valid(bars: pd.DataFrame, gap: pd.Series, minutes: int) -> np.ndarray:
    """Validate causal OHLC/six-MA rows and reset at gaps or wrong cadence."""
    required = {"open", "high", "low", "close", *MA}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError("missing six-MA fields: " + ", ".join(sorted(missing)))
    if not bars.index.equals(gap.index):
        raise ValueError("bars and data_gap must share one clock")
    values = bars.loc[:, ["open", "high", "low", "close", *MA]].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    cadence = np.ones(len(bars), dtype=bool)
    if len(bars) > 1:
        cadence[1:] = np.diff(bars.index.asi8) == pd.Timedelta(minutes=int(minutes)).value
    return (
        np.isfinite(values).all(axis=1)
        & (values[:, 3] > 0)
        & (values[:, 1] >= np.maximum.reduce((values[:, 0], values[:, 2], values[:, 3])))
        & (values[:, 2] <= np.minimum.reduce((values[:, 0], values[:, 1], values[:, 3])))
        & cadence
        & ~pd.Series(gap, index=bars.index).fillna(True).to_numpy(bool)
    )


def ma_features(bars: pd.DataFrame, gap: pd.Series, minutes: int) -> pd.DataFrame:
    """Build causal six-MA order, width and slope fields from current/past bars.

    Columns used are ``open/high/low/close`` and ``s20/e20/s60/e60/s120/e120``.
    MA-width P20 uses the preceding 256 valid rows in the same contiguous
    cadence/geometry segment; it never reads a later row.
    """
    valid = _basic_valid(bars, gap, minutes)
    close = pd.to_numeric(bars.close, errors="coerce").to_numpy(float)
    md = pd.to_numeric(bars.md, errors="coerce").to_numpy(float) if "md" in bars else np.full(len(bars), np.nan)
    sb = pd.to_numeric(bars.sb, errors="coerce").to_numpy(float) if "sb" in bars else np.full(len(bars), np.nan)
    lines = bars.loc[:, MA].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    segment = np.cumsum(~valid)
    long_order = np.zeros(len(bars), dtype=int)
    short_order = np.zeros(len(bars), dtype=int)
    for left_group, right_group in ((0, 1), (0, 2), (1, 2)):
        for left in (2 * left_group, 2 * left_group + 1):
            for right in (2 * right_group, 2 * right_group + 1):
                long_order += lines[:, left] > lines[:, right]
                short_order += lines[:, left] < lines[:, right]
    width = lines.max(axis=1) - lines.min(axis=1)
    width_close = width / close
    width_series = pd.Series(width_close, index=bars.index).where(valid)
    threshold = width_series.groupby(segment).transform(
        lambda item: item.shift(1).rolling(MA_HISTORY, min_periods=MA_HISTORY).quantile(MA_QUANTILE)
    )
    tight_known = valid & np.isfinite(threshold.to_numpy(float))
    tight = tight_known & (width_close <= threshold.to_numpy(float))
    centers = np.column_stack(((lines[:, 0] + lines[:, 1]) / 2, (lines[:, 2] + lines[:, 3]) / 2, (lines[:, 4] + lines[:, 5]) / 2))
    slopes = np.full_like(centers, np.nan)
    if len(bars) > 3:
        same_segment = segment[3:] == segment[:-3]
        positions = np.flatnonzero(same_segment) + 3
        slopes[positions] = centers[positions] - centers[positions - 3]
    slope_vote = (slopes > 0).sum(axis=1) - (slopes < 0).sum(axis=1)
    return pd.DataFrame(
        {
            "close": close,
            "md": md,
            "sb": sb,
            "valid": valid,
            "segment_id": segment,
            "ma_order_long": long_order,
            "ma_order_short": short_order,
            "ma_width_close": width_close,
            "ma_width_p20_prior256": threshold,
            "ma_tight_known": tight_known,
            "ma_tight": tight,
            "fast_center": centers[:, 0],
            "middle_center": centers[:, 1],
            "slow_center": centers[:, 2],
            "fast_slope3": slopes[:, 0],
            "middle_slope3": slopes[:, 1],
            "slow_slope3": slopes[:, 2],
            "slope_vote": slope_vote,
        },
        index=bars.index,
    )


@dataclass(frozen=True)
class BBEpisode:
    """A latest qualifying BB run located strictly in one prior 12-bar window."""

    found: bool
    start_i: int = -1
    end_i: int = -1
    window_length: int = 0
    age_bars: int = -1
    truncated_left: bool = False
    true_start_i: int = -1
    known_total_length: int = 0


def recent_bb_episode(compressed: np.ndarray, valid: np.ndarray, confirmation_i: int) -> BBEpisode:
    """Find the latest >=3 BB-compressed run wholly before ``confirmation_i``.

    Eligibility is restricted to the preceding 12 bars.  The returned true
    start/known length may look farther left only to describe a run cut by that
    fixed window; the label itself uses solely ``start_i:end_i`` in-window.
    """
    if confirmation_i <= 0:
        return BBEpisode(False)
    low = max(0, confirmation_i - MEMORY_BARS)
    high = confirmation_i - 1
    i = high
    selected: tuple[int, int] | None = None
    while i >= low:
        if not valid[i]:
            # A run before this point is not connected to confirmation.
            break
        if not compressed[i]:
            i -= 1
            continue
        end = i
        while i >= low and valid[i] and compressed[i]:
            i -= 1
        start = i + 1
        if end - start + 1 >= RUN_BARS:
            selected = (start, end)
            break
    if selected is None:
        return BBEpisode(False)
    start, end = selected
    true_start = start
    while true_start > 0 and valid[true_start - 1] and compressed[true_start - 1]:
        true_start -= 1
    truncated = start == low and true_start < start
    return BBEpisode(
        True,
        start_i=start,
        end_i=end,
        window_length=end - start + 1,
        age_bars=confirmation_i - end,
        truncated_left=truncated,
        true_start_i=true_start,
        known_total_length=end - true_start + 1,
    )


def _run3(values: np.ndarray) -> bool:
    """Return whether any three consecutive true values occur."""
    if len(values) < RUN_BARS:
        return False
    return bool(np.convolve(values.astype(int), np.ones(RUN_BARS, dtype=int), mode="valid").max(initial=0) >= RUN_BARS)


def bb_episode_evidence(
    bars: pd.DataFrame, bb: pd.DataFrame, gap: pd.Series, minutes: int, event_i: np.ndarray, sides: np.ndarray
) -> pd.DataFrame:
    """Evaluate the two BB-episode labels at supplied confirmation bar ordinals."""
    needed = {"bb_compressed", "v7_ready"}
    missing = needed - set(bb.columns)
    if missing or not bb.index.equals(bars.index):
        raise ValueError("BB diagnostic lacks aligned " + ", ".join(sorted(missing)))
    ma = ma_features(bars, gap, minutes)
    bb_valid = ma.valid.to_numpy(bool) & bb.v7_ready.fillna(False).to_numpy(bool)
    compressed = bb.bb_compressed.fillna(False).to_numpy(bool)
    rows: list[dict[str, object]] = []
    for i, side in zip(event_i, sides):
        episode = recent_bb_episode(compressed, bb_valid, int(i))
        recent_low = max(0, int(i) - MEMORY_BARS)
        discontinuous = not bb_valid[recent_low : int(i) + 1].all()
        row: dict[str, object] = {
            "current_ma_order_long": int(ma.ma_order_long.iloc[int(i)]),
            "current_ma_order_short": int(ma.ma_order_short.iloc[int(i)]),
            "current_ma_width_close": float(ma.ma_width_close.iloc[int(i)]),
            "current_ma_tight": bool(ma.ma_tight.iloc[int(i)]),
            "bb_episode_status": "eligible" if episode.found else ("discontinuous_recent_window" if discontinuous else "no_qualifying_recent_episode"),
            "bb_episode_start_i": episode.start_i,
            "bb_episode_end_i": episode.end_i,
            "bb_episode_window_length": episode.window_length,
            "bb_episode_age_bars": episode.age_bars,
            "bb_episode_truncated_left": episode.truncated_left,
            "bb_episode_true_start_i": episode.true_start_i,
            "bb_episode_known_total_length": episode.known_total_length,
            "bb_episode_directional_order9_run3": pd.NA,
            "bb_episode_directional_order12_run3": pd.NA,
            "bb_ma_tight_overlap_status": "missing_bb_episode",
            "bb_ma_tight_overlap_run3": pd.NA,
        }
        if episode.found:
            take = slice(episode.start_i, episode.end_i + 1)
            order = ma.ma_order_long.to_numpy(int)[take] if int(side) == 1 else ma.ma_order_short.to_numpy(int)[take]
            row["bb_episode_directional_order9_run3"] = _run3(order >= 9)
            row["bb_episode_directional_order12_run3"] = _run3(order >= 12)
            known = ma.ma_tight_known.to_numpy(bool)[take]
            if known.all():
                overlap = compressed[take] & ma.ma_tight.to_numpy(bool)[take]
                row["bb_ma_tight_overlap_status"] = "eligible"
                row["bb_ma_tight_overlap_run3"] = _run3(overlap)
            else:
                row["bb_ma_tight_overlap_status"] = "insufficient_ma_width_history"
        rows.append(row)
    return pd.DataFrame(rows)


def _daily_bars_from_4h(bars: pd.DataFrame, gap: pd.Series) -> tuple[pd.DataFrame, pd.Series]:
    """Aggregate only complete UTC days from a frozen four-hour cache."""
    if not bars.index.equals(gap.index):
        raise ValueError("daily aggregation requires aligned 4H source")
    start, end = bars.index.min().floor("D"), bars.index.max().floor("D")
    dates = pd.date_range(start, end, freq="D", tz="UTC")
    records: list[dict[str, float]] = []
    bad: list[bool] = []
    basic = _basic_valid(bars, gap, 240)
    for day in dates:
        expected = pd.date_range(day, periods=6, freq="4h", tz="UTC")
        positions = bars.index.get_indexer(expected)
        okay = (positions >= 0).all() and basic[positions].all()
        if okay:
            part = bars.iloc[positions]
            records.append({"open": float(part.open.iloc[0]), "high": float(part.high.max()), "low": float(part.low.min()), "close": float(part.close.iloc[-1])})
            bad.append(False)
        else:
            records.append({"open": math.nan, "high": math.nan, "low": math.nan, "close": math.nan})
            bad.append(True)
    daily = pd.DataFrame(records, index=dates)
    segment = np.cumsum(np.asarray(bad, dtype=bool))
    for period in (20, 60, 120):
        daily[f"s{period}"] = daily.close.where(~np.asarray(bad)).groupby(segment).transform(
            lambda item: item.rolling(period, min_periods=period).mean()
        )
        daily[f"e{period}"] = daily.close.where(~np.asarray(bad)).groupby(segment).transform(
            lambda item: item.ewm(span=period, adjust=False, min_periods=period).mean()
        )
    return daily, pd.Series(bad, index=dates)


def _htf_features(context, target_minutes: int) -> tuple[pd.DataFrame, int, str]:
    """Return one authenticated higher timeframe feature frame or derived UTC day."""
    if target_minutes == 1440:
        daily, gap = _daily_bars_from_4h(context.cache["bars"], context.cache["data_gap"])
        return ma_features(daily, gap, 1440), 1440, "derived_complete_utc_day_from_same_4h_cache"
    return ma_features(context.cache["bars"], context.cache["data_gap"], context.minutes), context.minutes, context.key


def attach_htf_evidence(
    local_events: pd.DataFrame,
    htf: pd.DataFrame | None,
    htf_minutes: int,
    source_key: str | None,
    source_kind: str,
) -> pd.DataFrame:
    """Attach the last fully closed higher-timeframe state as of V8 confirmation.

    A missing source, no completed bar, or insufficient MA history is an
    explicit unknown status.  The C label is true iff an adverse HTF order is
    at least 9/12 and its close remains on the adverse side of fast center.
    """
    defaults = {
        "htf_target_minutes": htf_minutes,
        "htf_source_stream_key": source_key,
        "htf_source_kind": source_kind,
        "htf_status": "missing_source_stream" if htf is None else "no_completed_htf_bar",
        "htf_bar_open": pd.NaT,
        "htf_bar_close_time": pd.NaT,
        "htf_order_long": np.nan,
        "htf_order_short": np.nan,
        "htf_order_against_signal": np.nan,
        "htf_close": np.nan,
        "htf_fast_center": np.nan,
        "htf_fast_slope3": np.nan,
        "htf_middle_slope3": np.nan,
        "htf_slow_slope3": np.nan,
        "htf_slope_vote": np.nan,
        "htf_slope_recovery_toward_signal": pd.NA,
        "htf_md": np.nan,
        "htf_sb": np.nan,
        "htf_opposed_completed": pd.NA,
    }
    rows: list[dict[str, object]] = []
    if htf is None:
        return pd.DataFrame([defaults.copy() for _ in range(len(local_events))])
    closes = htf.index + pd.Timedelta(minutes=htf_minutes)
    times = pd.to_datetime(local_events.signal_confirm_time, utc=True)
    positions = closes.searchsorted(times, side="right") - 1
    for event, pos in zip(local_events.itertuples(index=False), positions):
        row = defaults.copy()
        if pos < 0:
            rows.append(row)
            continue
        feature = htf.iloc[int(pos)]
        row.update({
            "htf_bar_open": htf.index[int(pos)],
            "htf_bar_close_time": closes[int(pos)],
            "htf_order_long": int(feature.ma_order_long),
            "htf_order_short": int(feature.ma_order_short),
            "htf_close": float(feature.get("close", np.nan)),
            "htf_fast_center": float(feature.fast_center),
            "htf_fast_slope3": float(feature.fast_slope3),
            "htf_middle_slope3": float(feature.middle_slope3),
            "htf_slow_slope3": float(feature.slow_slope3),
            "htf_slope_vote": int(feature.slope_vote),
            "htf_md": float(feature.md),
            "htf_sb": float(feature.sb),
        })
        side = int(event.side)
        against = int(feature.ma_order_short if side == 1 else feature.ma_order_long)
        row["htf_order_against_signal"] = against
        row["htf_slope_recovery_toward_signal"] = bool(int(feature.slope_vote) * side > 0)
        finite = bool(feature.valid and np.isfinite(feature.fast_center) and np.isfinite(feature.get("close", np.nan)))
        if pd.Timestamp(event.signal_confirm_time) - closes[int(pos)] >= pd.Timedelta(minutes=htf_minutes):
            row["htf_status"] = "stale_htf_bar"
        elif not finite:
            row["htf_status"] = "insufficient_or_invalid_htf_history"
        else:
            row["htf_status"] = "eligible"
            row["htf_opposed_completed"] = bool(against >= 9 and side * (float(feature.get("close")) - float(feature.fast_center)) <= 0)
        rows.append(row)
    return pd.DataFrame(rows)


def _stream_folder_index(raw: Path) -> dict[tuple[str, str, int], Path]:
    """Locate a same-venue/symbol HTF source from receipt metadata only."""
    index: dict[tuple[str, str, int], Path] = {}
    folders = sorted(path for path in (raw / "streams").iterdir() if (path / "completion.json").is_file())
    for folder in folders:
        receipt = pd.read_csv(folder / "receipt.csv", nrows=1).iloc[0]
        key = (str(receipt.venue), str(receipt.symbol), int(receipt.minutes))
        if key in index:
            raise ValueError(f"duplicate frozen stream identity: {key}")
        index[key] = folder
    return index


def _metrics(part: pd.DataFrame) -> dict[str, float | int]:
    wins = part.net_r.gt(0)
    gain = float(part.loc[wins, "net_r"].sum())
    loss = float(-part.loc[part.net_r.lt(0), "net_r"].sum())
    return {"n": len(part), "win_rate": float(wins.mean()) if len(part) else math.nan, "mean_net_r": float(part.net_r.mean()) if len(part) else math.nan, "profit_factor": gain / loss if loss else math.inf, "net_r": float(part.net_r.sum()), "realized_10r": int(part.net_r.ge(10).sum())}


def summarize(events: pd.DataFrame, label: str, *, keys: list[str]) -> pd.DataFrame:
    """Score one already-known label and full complement on frozen closed trades."""
    rows: list[dict[str, object]] = []
    e = events.loc[events.scoring_closed].copy()
    for values, all_part in e.groupby(keys, dropna=False):
        part = all_part.loc[all_part[label].notna()].copy()
        eligible10 = int(part.net_r.ge(10).sum())
        full10 = int(all_part.net_r.ge(10).sum())
        for cohort, mask in (("target", part[label].astype(bool)), ("complement", ~part[label].astype(bool))):
            chosen = part.loc[mask]
            chosen10 = int(chosen.net_r.ge(10).sum())
            row = dict(zip(keys, values)) | {
                "label": label,
                "cohort": cohort,
                **_metrics(chosen),
                "full_scoring_closed": len(all_part),
                "known_scoring_closed": len(part),
                "unknown_scoring_closed": len(all_part) - len(part),
                "signal_retention_of_full": len(chosen) / len(all_part) if len(all_part) else math.nan,
                "signal_retention_of_known": len(chosen) / len(part) if len(part) else math.nan,
                "full_original_10r": full10,
                "eligible_original_10r": eligible10,
                "full_original_10r_retention": chosen10 / full10 if full10 else math.nan,
                "eligible_original_10r_retention": chosen10 / eligible10 if eligible10 else math.nan,
            }
            rows.append(row)
    return pd.DataFrame(rows)


def pairs(events: pd.DataFrame, label: str) -> pd.DataFrame:
    """Pair target rows with no-reuse non-target rows inside the fixed causal block."""
    e = events.loc[events.scoring_closed & events[label].notna()].copy()
    e["target"] = e[label].astype(bool)
    rows: list[dict[str, object]] = []
    fields = ["stream_key", "period", "calendar_month", "side", "causal_volatility_bucket"]
    for _, block in e.groupby(fields, dropna=False):
        targets = block.loc[block.target].sort_values(["signal_confirm_time", "trade_id"])
        controls = block.loc[~block.target].copy()
        for target in targets.itertuples(index=False):
            common = {"label": label, "target_trade_id": target.trade_id, "asset": target.asset, "period": target.period, "timeframe_min": target.timeframe_min, "side": target.side, "calendar_month": target.calendar_month, "causal_volatility_bucket": target.causal_volatility_bucket}
            if int(target.causal_volatility_bucket) < 0:
                rows.append(common | {"matched": False, "missing_reason": "insufficient_causal_volatility_history"})
                continue
            if controls.empty:
                rows.append(common | {"matched": False, "missing_reason": "no_non_target_in_exact_match_block"})
                continue
            distances = (pd.to_datetime(controls.signal_confirm_time, utc=True) - pd.Timestamp(target.signal_confirm_time)).abs()
            chosen = controls.assign(_distance=distances).sort_values(["_distance", "trade_id"]).iloc[0]
            rows.append(common | {"matched": True, "control_trade_id": chosen.trade_id, "target_net_r": target.net_r, "control_net_r": chosen.net_r, "difference_r": target.net_r - chosen.net_r, "target_win": bool(target.net_r > 0), "control_win": bool(chosen.net_r > 0), "win_difference": int(target.net_r > 0) - int(chosen.net_r > 0), "target_signal_confirm_time": target.signal_confirm_time, "control_signal_confirm_time": chosen.signal_confirm_time})
            controls = controls.loc[controls.trade_id.ne(chosen.trade_id)]
    return pd.DataFrame(rows)


def _sign_flip_p(block_means: np.ndarray, *, seed: int = 20260914, draws: int = 10_000) -> float:
    """Return a fixed two-sided plus-one block sign-flip p value."""
    if len(block_means) == 0:
        return math.nan
    observed = abs(float(block_means.mean()))
    signs = np.random.default_rng(seed).choice((-1.0, 1.0), size=(draws, len(block_means)))
    simulated = np.abs((signs * block_means).mean(axis=1))
    return float((1 + (simulated >= observed).sum()) / (draws + 1))


def _block_bootstrap_interval(block_means: np.ndarray, *, seed: int, draws: int = 10_000) -> tuple[float, float]:
    """Return fixed-seed equal-block percentile limits for descriptive context."""
    if len(block_means) == 0:
        return math.nan, math.nan
    picked = np.random.default_rng(seed).integers(0, len(block_means), size=(draws, len(block_means)))
    samples = block_means[picked].mean(axis=1)
    return float(np.quantile(samples, .025)), float(np.quantile(samples, .975))


def matched_summary(pair_table: pd.DataFrame) -> pd.DataFrame:
    """Summarize no-reuse pairs with exploratory asset-by-month sign flips."""
    rows: list[dict[str, object]] = []
    good = pair_table.loc[pair_table.matched].copy()
    for (period, label), part in good.groupby(["period", "label"], dropna=False):
        grouped = part.groupby(["asset", "calendar_month"], dropna=False)
        r_blocks = grouped.difference_r.mean().to_numpy(float)
        win_blocks = grouped.win_difference.mean().to_numpy(float)
        r_low, r_high = _block_bootstrap_interval(r_blocks, seed=20260914 + len(rows))
        w_low, w_high = _block_bootstrap_interval(win_blocks, seed=20261014 + len(rows))
        rows.append({
            "period": period,
            "label": label,
            "matched": len(part),
            "pair_weighted_mean_difference_r": float(part.difference_r.mean()),
            "block_equal_weight_mean_difference_r": float(r_blocks.mean()),
            "block_equal_weight_r_ci95_low": r_low,
            "block_equal_weight_r_ci95_high": r_high,
            "pair_weighted_win_rate_difference": float(part.win_difference.mean()),
            "block_equal_weight_win_rate_difference": float(win_blocks.mean()),
            "block_equal_weight_win_ci95_low": w_low,
            "block_equal_weight_win_ci95_high": w_high,
            "asset_month_blocks": len(r_blocks),
            "block_sign_flip_p": _sign_flip_p(r_blocks, seed=20260914 + len(rows)),
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["holm_p"] = np.nan
    for period, idx in out.groupby("period").groups.items():
        order = out.loc[idx, "block_sign_flip_p"].sort_values().index.tolist()
        running = 0.0
        total = len(order)
        for rank, row in enumerate(order):
            running = max(running, min(1.0, float(out.at[row, "block_sign_flip_p"]) * (total - rank)))
            out.at[row, "holm_p"] = running
    return out


def _event_key(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.signal_bar_open = pd.to_datetime(out.signal_bar_open, utc=True)
    return out


def run(output: Path, *, official: bool = False) -> None:
    """Authenticate all streams, emit fixed labels, and score existing V8 outcomes."""
    config = json.loads(CONFIG.read_text())
    entry = Path(config["entry_process"])
    cycle = Path(config["ma_cycle"])
    raw = Path(config["raw"])
    evidence_path = entry / "same_entry_evidence.csv.gz"
    cycle_path = cycle / "event_states.csv.gz"
    checks = {
        entry / "manifest.json": config["entry_process_manifest_sha256"],
        evidence_path: config["entry_process_evidence_sha256"],
        cycle / "manifest.json": config["ma_cycle_manifest_sha256"],
        cycle_path: config["ma_cycle_event_states_sha256"],
        raw / "manifest.json": config["raw_manifest_sha256"],
    }
    for path, expected in checks.items():
        if sha256(path) != expected:
            raise ValueError(f"frozen input changed: {path}")
    if official and (not _clean((Path(__file__), CONFIG, PLAN, HOLDOUT, TEST)) or EXP not in output.parents):
        raise ValueError("official run requires committed clean source in this experiment")
    events = _event_key(pd.read_csv(evidence_path))
    if len(events) != int(config["expected_v8_events"]) or int(events.scoring_closed.sum()) != int(config["expected_scoring_closed"]):
        raise ValueError("frozen V8 event universe changed")
    cycle_events = _event_key(pd.read_csv(cycle_path, usecols=["stream_key", "signal_bar_open", "side", "period", "causal_volatility_bucket", "order_long", "order_short", "width_close", "compression_qualified"]))
    join = ["stream_key", "signal_bar_open", "side", "period"]
    if cycle_events.duplicated(join).any():
        raise ValueError("MA-cycle causal bucket is not unique per event")
    cycle_events = cycle_events.rename(columns={"order_long": "cycle_order_long", "order_short": "cycle_order_short", "width_close": "cycle_width_close", "compression_qualified": "cycle_compression_qualified"})
    events = events.merge(cycle_events, on=join, how="left", validate="one_to_one")
    if events.causal_volatility_bucket.isna().any():
        raise ValueError("missing MA-cycle causal volatility bucket")
    events["calendar_month"] = pd.to_datetime(events.entry_time, utc=True).dt.strftime("%Y-%m")
    folders = sorted(path for path in (raw / "streams").iterdir() if (path / "completion.json").is_file())
    if len(folders) != int(config["expected_streams"]):
        raise ValueError("unexpected frozen stream count")
    folder_index = _stream_folder_index(raw)
    groups = {key: part.copy() for key, part in events.groupby("stream_key", sort=False)}
    output.mkdir(parents=True, exist_ok=False)
    parts: list[pd.DataFrame] = []
    audit: list[dict[str, object]] = []
    for number, folder in enumerate(folders, 1):
        context = load_verified_stream(folder)
        subset = groups.get(context.key)
        audit_row = {"stream_key": context.key, **context.identity, "cache_sha256": context.receipt["cache_sha256"], "event_source_used": subset is not None, "authenticated": True}
        if subset is not None:
            stamps = pd.to_datetime(subset.signal_bar_open, utc=True)
            event_i = context.cache["bars"].index.get_indexer(stamps)
            if (event_i < 0).any():
                raise ValueError(f"event absent from authenticated cache: {context.key}")
            bb = bb_episode_evidence(context.cache["bars"], context.cache["bb"], context.cache["data_gap"], context.minutes, event_i, subset.side.to_numpy(int))
            if not np.array_equal(bb.current_ma_order_long.to_numpy(int), subset.cycle_order_long.to_numpy(int)) or not np.array_equal(bb.current_ma_order_short.to_numpy(int), subset.cycle_order_short.to_numpy(int)):
                raise ValueError(f"current six-MA order parity differs from frozen MA-cycle evidence: {context.key}")
            target = HTF_MAP[int(context.minutes)]
            htf: pd.DataFrame | None = None
            source_key: str | None = None
            source_kind = "missing_same_venue_symbol_source"
            if target == 1440:
                htf, _, source_kind = _htf_features(context, target)
                source_key = context.key
            else:
                htf_folder = folder_index.get((context.identity["venue"], context.identity["symbol"], target))
                if htf_folder is not None:
                    higher = load_verified_stream(htf_folder)
                    htf, _, source_key = _htf_features(higher, target)
                    source_kind = "authenticated_same_venue_symbol_cache"
            attached = attach_htf_evidence(subset, htf, target, source_key, source_kind)
            out = subset.reset_index(drop=True).join(bb).join(attached)
            parts.append(out)
        audit.append(audit_row)
        if number % 100 == 0 or number == len(folders):
            print(json.dumps({"streams_authenticated": number, "expected": len(folders)}), flush=True)
    result = pd.concat(parts, ignore_index=True)
    if len(result) != len(events) or result.duplicated(join).any():
        raise ValueError("event output does not preserve the frozen V8 universe")
    # Preserve the expensive authenticated feature scan before any optional
    # pairing/statistical reduction can fail.  A downstream repair can read
    # this immutable same-entry table without rescanning caches or V6/V8.
    result.to_csv(output / "event_evidence.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    pd.DataFrame(audit).to_csv(output / "input_stream_audit.csv", index=False)
    (output / "scan_manifest.json").write_text(json.dumps({
        "complete": True,
        "official": official,
        "events": len(result),
        "scoring_closed": int(result.scoring_closed.sum()),
        "streams_authenticated": len(audit),
        "purpose": "authenticated causal event-evidence scan; pair statistics are a later reduction",
    }, indent=2))
    labels = ["bb_episode_directional_order9_run3", "bb_ma_tight_overlap_run3", "htf_opposed_completed"]
    coverage_rows: list[dict[str, object]] = []
    for label in labels:
        for period, part in result.groupby("period", dropna=False):
            coverage_rows.append({"label": label, "period": period, "events": len(part), "known": int(part[label].notna().sum()), "unknown": int(part[label].isna().sum()), "true": int(part[label].eq(True).sum()), "false": int(part[label].eq(False).sum())})
    coverage = pd.DataFrame(coverage_rows)
    outcomes = pd.concat([summarize(result, label, keys=["period"]) for label in labels], ignore_index=True)
    by_tf_side = pd.concat([summarize(result, label, keys=["period", "timeframe_min", "side"]) for label in labels], ignore_index=True)
    by_month = pd.concat([summarize(result, label, keys=["period", "calendar_month"]) for label in labels], ignore_index=True)
    matched = pd.concat([pairs(result, label) for label in labels], ignore_index=True)
    status_coverage = pd.concat(
        [
            result.groupby(["period", "bb_episode_status"], dropna=False).size().rename("n").reset_index().assign(kind="bb_episode"),
            result.groupby(["period", "bb_ma_tight_overlap_status"], dropna=False).size().rename("n").reset_index().rename(columns={"bb_ma_tight_overlap_status": "status"}).assign(kind="bb_ma_tight_overlap"),
            result.groupby(["period", "htf_status"], dropna=False).size().rename("n").reset_index().rename(columns={"htf_status": "status"}).assign(kind="htf"),
        ],
        ignore_index=True,
    )
    status_coverage.loc[status_coverage.kind.eq("bb_episode"), "status"] = status_coverage.loc[status_coverage.kind.eq("bb_episode"), "bb_episode_status"]
    status_coverage = status_coverage.loc[:, ["kind", "period", "status", "n"]]
    coverage.to_csv(output / "coverage.csv", index=False)
    outcomes.to_csv(output / "outcome_summary.csv", index=False)
    by_tf_side.to_csv(output / "timeframe_side_summary.csv", index=False)
    by_month.to_csv(output / "monthly_summary.csv", index=False)
    matched.to_csv(output / "same_stream_month_side_volatility_pairs.csv", index=False)
    matched_summary(matched).to_csv(output / "matched_summary.csv", index=False)
    status_coverage.to_csv(output / "status_coverage.csv", index=False)
    identity = {str(path): sha256(path) for path in (Path(__file__), CONFIG, PLAN, HOLDOUT, TEST, *checks.keys())}
    (output / "manifest.json").write_text(json.dumps({"complete": True, "official": official, "source": identity, "events": len(result), "scoring_closed": int(result.scoring_closed.sum()), "streams_authenticated": len(audit), "labels": labels, "research_only": True, "no_execution_replay": True}, indent=2))
    (output / "receipt.json").write_text(json.dumps({path.name: sha256(path) for path in output.iterdir() if path.is_file()}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--official", action="store_true")
    args = parser.parse_args()
    run(args.output, official=args.official)
