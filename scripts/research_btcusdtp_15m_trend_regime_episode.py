#!/usr/bin/env python3
"""Research a causal one-signal-per-trend-regime BTCUSDT.P 15m episode.

The detector is an exact Python replay of the completed-bar K1/K2 morphology in
``fable_15m_k1_k2_episode_v3.pine``.  Every feature used to qualify a signal is
available no later than the completed K2 bar ``t``: OHLCV through ``t``,
Pine/Wilder ATR14, EMA30(HL2), SMA60(HL2), and the four-bar EMA30 slope.  Entry
is the next contiguous bar open.  Only trade outcome resolution reads future
bars.

The experiment changes numeric trend-regime parameters one at a time in the
registered order.  A position exit never rearms a consumed regime.  Rearming
requires either a causally observed opposite strong regime or a completed
neutral dwell.  Repository holdout begins on 2026-05-04 and is physically
excluded by the loader contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd

from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import load_featured
from scripts.research_btcusdtp_15m_dual_ma_runner import (
    BAR_DELTA,
    _assignment_metrics,
    add_dual_references,
    matched_controls,
    resolve_runner,
)
from scripts.research_btcusdtp_15m_ma_state_trend import (
    fold_label,
    json_value,
    metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_two_key_candle_ma_retest_1h import sha256_file


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-btcusdtp-15m-trend-regime-episode-preholdout-20260904-v1"
EXPERIMENT = ROOT / "experiments" / "active" / EXPERIMENT_ID
CONFIG_PATH = EXPERIMENT / "config.json"
RESULTS = EXPERIMENT / "results"
SELECTION_RECEIPT = RESULTS / "selection_receipt.json"
SCRIPT_PATH = Path(__file__).resolve()
PINE_V3_PATH = (
    ROOT
    / "experiments/active/exp-btcusdtp-15m-high-recall-l2-trend-runner-preholdout-20260904-v1/pine/fable_15m_k1_k2_episode_v3.pine"
)


def load_config() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_frame(config: Mapping[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load complete 15m bars strictly before the registered audit end.

    Source columns are native 5m OHLCV.  ``load_featured`` aggregates only
    complete contiguous UTC-aligned triples and derives ATR14 without reading
    any row at or after the repository holdout boundary.
    """

    compatibility = {
        "source": config["source"],
        "window": {
            "holdout_start": config["source"]["holdout_start"],
            "validation_end_exclusive": config["splits"]["audit_end_exclusive"],
        },
    }
    base, quality = load_featured(compatibility, "15m")
    if int(quality["holdout_rows_read"]) != 0:
        raise RuntimeError("trend-regime loader materialized repository holdout")
    frame = add_dual_references(base, "EMA30", "SMA60")
    atr = frame["atr"].astype(float).replace(0.0, np.nan)
    frame["fast_slow_spread_atr"] = (
        frame["reference_ma"].astype(float) - frame["trend_ma"].astype(float)
    ) / atr
    frame["fast_slope4_atr_per_bar"] = (
        frame["reference_ma"].astype(float)
        - frame.groupby("segment_id", sort=False)["reference_ma"].shift(4)
    ) / (atr * 4.0)
    side = np.sign(
        ((frame["high"] + frame["low"]) / 2.0) - frame["reference_ma"]
    )
    flips = side.ne(side.groupby(frame["segment_id"], sort=False).shift(1)).astype(int)
    frame["ma_side_flips_24"] = (
        flips.groupby(frame["segment_id"], sort=False)
        .rolling(24, min_periods=24)
        .sum()
        .reset_index(level=0, drop=True)
    )
    changes = frame.groupby("segment_id", sort=False)["close"].diff().abs()
    signed_move = (
        frame["close"]
        - frame.groupby("segment_id", sort=False)["close"].shift(24)
    ).abs()
    path = (
        changes.groupby(frame["segment_id"], sort=False)
        .rolling(24, min_periods=24)
        .sum()
        .reset_index(level=0, drop=True)
    )
    frame["efficiency24"] = signed_move / path.replace(0.0, np.nan)
    return frame, quality


def _clamp01(value: float) -> float:
    return float(np.clip(value, 0.0, 1.0))


def build_v3_pairs(frame: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    """Replay the V3 K1/K2 morphology on completed bars only.

    K1 reads bars ``t-gap`` for registered gaps 2--8.  K2 and all intermediate
    path checks end at ``t``.  The function returns raw directional candidates;
    position or regime state is applied later.
    """

    rule = config["baseline"]
    open_ = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    atr = frame["atr"].to_numpy(dtype=float)
    fast = frame["reference_ma"].to_numpy(dtype=float)
    slow = frame["trend_ma"].to_numpy(dtype=float)
    slope = frame["fast_slope4_atr_per_bar"].to_numpy(dtype=float)
    spread = frame["fast_slow_spread_atr"].to_numpy(dtype=float)
    flips = frame["ma_side_flips_24"].to_numpy(dtype=float)
    efficiency = frame["efficiency24"].to_numpy(dtype=float)
    segments = frame["segment_id"].to_numpy(dtype=int)
    times = frame["open_time"].to_numpy()
    rows: list[dict[str, Any]] = []
    gap_min = int(rule["k1_k2_gap_min"])
    gap_max = int(rule["k1_k2_gap_max"])

    for signal_i in range(max(240, gap_max), len(frame)):
        safe_atr = atr[signal_i]
        candle_range = high[signal_i] - low[signal_i]
        if not np.isfinite(safe_atr) or safe_atr <= 0.0 or candle_range <= 0.0:
            continue
        for direction in (1, -1):
            body = abs(close[signal_i] - open_[signal_i])
            wick = (
                min(open_[signal_i], close[signal_i]) - low[signal_i]
                if direction > 0
                else high[signal_i] - max(open_[signal_i], close[signal_i])
            )
            wick_share = wick / candle_range
            body_ratio = body / candle_range
            close_side = direction * (close[signal_i] - fast[signal_i]) / safe_atr
            touch_depth = (
                (fast[signal_i] - low[signal_i]) / safe_atr
                if direction > 0
                else (high[signal_i] - fast[signal_i]) / safe_atr
            )
            body_trend_side = (
                min(open_[signal_i], close[signal_i]) >= fast[signal_i]
                if direction > 0
                else max(open_[signal_i], close[signal_i]) <= fast[signal_i]
            )
            k2_ok = bool(
                np.isfinite(close_side)
                and np.isfinite(touch_depth)
                and wick_share >= float(rule["k2_wick_share_min"])
                and body_ratio <= float(rule["k2_body_ratio_max"])
                and close_side >= 0.0
                and 0.0 <= touch_depth <= float(rule["k2_touch_depth_atr_max"])
                and body_trend_side
            )
            if not k2_ok:
                continue

            best: dict[str, Any] | None = None
            for gap in range(gap_min, gap_max + 1):
                k1_i = signal_i - gap
                if segments[k1_i] != segments[signal_i]:
                    continue
                k1_atr = atr[k1_i]
                k1_range = high[k1_i] - low[k1_i]
                if not np.isfinite(k1_atr) or k1_atr <= 0.0 or k1_range <= 0.0:
                    continue
                directional_body = direction * (close[k1_i] - open_[k1_i]) / k1_atr
                range_atr = k1_range / k1_atr
                close_location = (
                    (close[k1_i] - low[k1_i]) / k1_range
                    if direction > 0
                    else (high[k1_i] - close[k1_i]) / k1_range
                )
                open_side = direction * (open_[k1_i] - fast[k1_i]) / k1_atr
                k1_close_side = direction * (close[k1_i] - fast[k1_i]) / k1_atr
                crosses_body = (
                    open_[k1_i] <= fast[k1_i] and close[k1_i] >= fast[k1_i]
                    if direction > 0
                    else open_[k1_i] >= fast[k1_i] and close[k1_i] <= fast[k1_i]
                )
                k1_ok = bool(
                    crosses_body
                    and directional_body >= float(rule["k1_body_atr_min"])
                    and range_atr >= float(rule["k1_range_atr_min"])
                    and close_location >= float(rule["k1_close_location_min"])
                    and open_side <= 0.0
                    and k1_close_side >= 0.0
                )
                if not k1_ok:
                    continue
                path_ok = True
                for step in range(1, gap):
                    middle_i = signal_i - step
                    middle_atr = atr[middle_i]
                    middle_side = (
                        direction * (close[middle_i] - fast[middle_i]) / middle_atr
                        if np.isfinite(middle_atr) and middle_atr > 0.0
                        else np.nan
                    )
                    if not np.isfinite(middle_side) or middle_side < float(
                        rule["wrong_side_close_tolerance_atr"]
                    ):
                        path_ok = False
                        break
                if not path_ok:
                    continue
                quality = float(
                    np.mean(
                        [
                            _clamp01(directional_body / 1.25),
                            _clamp01(range_atr / 2.0),
                            _clamp01(close_location),
                            _clamp01(wick_share / 0.60),
                        ]
                    )
                )
                candidate = {
                    "signal_i": signal_i,
                    "signal_time": times[signal_i],
                    "direction": direction,
                    "signal_family": "strict_k1_k2",
                    "signal_score": quality,
                    "signal_atr": safe_atr,
                    "signal_ma": fast[signal_i],
                    "k1_i": k1_i,
                    "k1_gap": gap,
                    "k1_body_atr": directional_body,
                    "k1_range_atr": range_atr,
                    "k1_close_location": close_location,
                    "k2_wick_share": wick_share,
                    "k2_body_ratio": body_ratio,
                    "k2_close_side_atr": close_side,
                    "k2_touch_depth_atr": touch_depth,
                    "signed_fast_slow_spread_atr": direction * spread[signal_i],
                    "signed_fast_slope4_atr_per_bar": direction * slope[signal_i],
                    "ma_side_flips_24": flips[signal_i],
                    "efficiency24": efficiency[signal_i],
                    "fast_ma": fast[signal_i],
                    "slow_ma": slow[signal_i],
                }
                if best is None or quality > float(best["signal_score"]):
                    best = candidate
            if best is not None:
                rows.append(best)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["signal_i", "signal_score", "direction"],
        ascending=[True, False, False],
        kind="mergesort",
    ).reset_index(drop=True)


def build_regime_table(
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    params: Mapping[str, float | int],
) -> pd.DataFrame:
    """Build a causal hysteretic trend state from EMA30/SMA60 and EMA slope.

    At bar ``t`` a strong long state requires both EMA30-SMA60 spread and the
    four-bar EMA30 slope to meet their positive thresholds; short is mirrored.
    A consumed state can rearm only after the opposite strong state appears or
    after every bar in a registered neutral dwell has small absolute spread and
    slope.  No future bar is used to label the state at ``t``.
    """

    regime = config["trend_regime"]
    spread = frame["fast_slow_spread_atr"].to_numpy(dtype=float)
    slope = frame["fast_slope4_atr_per_bar"].to_numpy(dtype=float)
    segments = frame["segment_id"].to_numpy(dtype=int)
    entry_spread = float(params["entry_spread_atr"])
    entry_slope = float(params["entry_slope_atr_per_bar"])
    neutral_spread = float(regime["neutral_abs_spread_atr_max"])
    neutral_slope = float(regime["neutral_abs_slope_atr_per_bar_max"])
    neutral_dwell = int(params["neutral_dwell_bars"])
    strong_dwell = int(params["strong_dwell_bars"])
    directions = np.zeros(len(frame), dtype=int)
    ids = np.full(len(frame), -1, dtype=int)
    starts = np.full(len(frame), -1, dtype=int)
    neutral_runs = np.zeros(len(frame), dtype=int)
    state = 0
    regime_id = -1
    regime_start = -1
    neutral_run = 0
    strong_run = 0
    strong_run_direction = 0
    last_segment = -1

    for index in range(len(frame)):
        if segments[index] != last_segment:
            state = 0
            regime_id = -1
            regime_start = -1
            neutral_run = 0
            strong_run = 0
            strong_run_direction = 0
            last_segment = segments[index]
        current_spread = spread[index]
        current_slope = slope[index]
        if not np.isfinite(current_spread) or not np.isfinite(current_slope):
            directions[index] = state
            ids[index] = regime_id
            starts[index] = regime_start
            continue
        strong = 0
        if (
            current_spread > 0.0
            and current_slope >= 0.0
            and current_spread >= entry_spread
            and current_slope >= entry_slope
        ):
            strong = 1
        elif (
            current_spread < 0.0
            and current_slope <= 0.0
            and -current_spread >= entry_spread
            and -current_slope >= entry_slope
        ):
            strong = -1

        if strong == 0:
            strong_run = 0
            strong_run_direction = 0
        elif strong == strong_run_direction:
            strong_run += 1
        else:
            strong_run_direction = strong
            strong_run = 1
        confirmed_strong = strong if strong_run >= strong_dwell else 0

        if state == 0 and confirmed_strong != 0:
            state = confirmed_strong
            regime_id += 1
            regime_start = index
            neutral_run = 0
        elif state != 0 and confirmed_strong == -state:
            state = confirmed_strong
            regime_id += 1
            regime_start = index
            neutral_run = 0
        elif state != 0:
            neutral = bool(
                abs(current_spread) <= neutral_spread
                and abs(current_slope) <= neutral_slope
            )
            neutral_run = neutral_run + 1 if neutral else 0
            if neutral_run >= neutral_dwell:
                state = 0
                regime_start = -1
                neutral_run = 0
        directions[index] = state
        ids[index] = regime_id
        starts[index] = regime_start
        neutral_runs[index] = neutral_run
    return pd.DataFrame(
        {
            "regime_direction": directions,
            "regime_id": ids,
            "regime_start_i": starts,
            "neutral_run": neutral_runs,
        }
    )


def _entry_event(
    row: Mapping[str, Any], frame: pd.DataFrame, policy: str
) -> dict[str, Any] | None:
    signal_i = int(row["signal_i"])
    entry_i = signal_i + 1
    if entry_i >= len(frame):
        return None
    if (
        int(frame.loc[entry_i, "segment_id"])
        != int(frame.loc[signal_i, "segment_id"])
        or frame.loc[entry_i, "open_time"] - frame.loc[signal_i, "open_time"]
        != BAR_DELTA
    ):
        return None
    identity = (
        f"BTC-USDT-SWAP|15m|{int(row['direction'])}|"
        f"{utc(row['signal_time']).isoformat()}|{int(row['k1_i'])}|{policy}"
    )
    return {
        **dict(row),
        "setup_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
        "entry_i": entry_i,
        "entry_time": frame.loc[entry_i, "open_time"],
        "entry_price": float(frame.loc[entry_i, "open"]),
    }


def _resolve(
    event: Mapping[str, Any], frame: pd.DataFrame, config: Mapping[str, Any]
) -> dict[str, Any] | None:
    execution = config["execution"]
    result = resolve_runner(
        frame,
        event,
        "ma_trail1_after_2atr",
        int(execution["horizon_bars"]),
        float(execution["initial_disaster_stop_atr"]),
        5.0,
    )
    if not result.get("resolved"):
        return None
    gross = float(result["gross_return"])
    cost = float(execution["round_trip_cost_fraction"])
    risk_fraction = (
        float(execution["initial_disaster_stop_atr"])
        * float(event["signal_atr"])
        / float(event["entry_price"])
    )
    return {
        **dict(event),
        **result,
        "net_return": gross - cost,
        "risk_fraction": risk_fraction,
        "return_r": gross / risk_fraction,
        "net_return_r": (gross - cost) / risk_fraction,
    }


def simulate_v3(
    pairs: pd.DataFrame, frame: pd.DataFrame, config: Mapping[str, Any]
) -> pd.DataFrame:
    """Replay V3's position-lifetime lock and last-K1 de-duplication."""

    if pairs.empty:
        return pd.DataFrame()
    output: list[dict[str, Any]] = []
    flat_from_i = -1
    last_k1 = {1: -1, -1: -1}
    for signal_i, group in pairs.groupby("signal_i", sort=True):
        signal_i = int(signal_i)
        if signal_i < flat_from_i:
            continue
        options = {
            int(row["direction"]): row
            for row in group.sort_values(
                ["signal_score", "direction"],
                ascending=[False, False],
                kind="mergesort",
            ).to_dict("records")
        }
        long = options.get(1)
        short = options.get(-1)
        long_unused = long is not None and int(long["k1_i"]) != last_k1[1]
        short_unused = short is not None and int(short["k1_i"]) != last_k1[-1]
        chosen: dict[str, Any] | None = None
        if long_unused and (
            short is None
            or not short_unused
            or float(long["signal_score"]) >= float(short["signal_score"])
        ):
            chosen = long
        elif short_unused:
            chosen = short
        if chosen is None:
            continue
        event = _entry_event(chosen, frame, "position_lifetime_v3")
        if event is None:
            continue
        resolved = _resolve(event, frame, config)
        if resolved is None:
            continue
        output.append(resolved)
        direction = int(chosen["direction"])
        last_k1[direction] = int(chosen["k1_i"])
        flat_from_i = int(resolved["exit_i"])
    return pd.DataFrame(output)


def simulate_regime(
    pairs: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    params: Mapping[str, float | int],
) -> pd.DataFrame:
    """Emit at most one resolved trade per causal directional trend regime."""

    if pairs.empty:
        return pd.DataFrame()
    table = build_regime_table(frame, config, params)
    output: list[dict[str, Any]] = []
    consumed: set[tuple[int, int, int]] = set()
    flat_from_i = -1
    segments = frame["segment_id"].to_numpy(dtype=int)
    for signal_i, group in pairs.groupby("signal_i", sort=True):
        signal_i = int(signal_i)
        if signal_i < flat_from_i:
            continue
        direction = int(table.loc[signal_i, "regime_direction"])
        regime_id = int(table.loc[signal_i, "regime_id"])
        if direction == 0 or regime_id < 0:
            continue
        key = (int(segments[signal_i]), regime_id, direction)
        if key in consumed:
            continue
        eligible = group[group["direction"].eq(direction)].sort_values(
            ["signal_score", "direction"],
            ascending=[False, False],
            kind="mergesort",
        )
        if eligible.empty:
            continue
        chosen = eligible.iloc[0].to_dict()
        chosen.update(
            {
                "regime_id": regime_id,
                "regime_start_i": int(table.loc[signal_i, "regime_start_i"]),
                "regime_age_bars": signal_i
                - int(table.loc[signal_i, "regime_start_i"]),
                "entry_spread_atr": float(params["entry_spread_atr"]),
                "entry_slope_atr_per_bar": float(
                    params["entry_slope_atr_per_bar"]
                ),
                "neutral_dwell_bars": int(params["neutral_dwell_bars"]),
                "strong_dwell_bars": int(params["strong_dwell_bars"]),
            }
        )
        event = _entry_event(chosen, frame, "trend_regime_v4")
        if event is None:
            continue
        resolved = _resolve(event, frame, config)
        if resolved is None:
            continue
        output.append(resolved)
        consumed.add(key)
        flat_from_i = int(resolved["exit_i"])
    return pd.DataFrame(output)


def _window_events(
    events: pd.DataFrame, start: object, end: object
) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    return events[
        events["entry_time"].map(utc).ge(utc(start))
        & events["entry_time"].map(utc).lt(utc(end))
    ].copy()


def density_metrics(
    events: pd.DataFrame,
    *,
    start: object,
    end: object,
    baseline: pd.DataFrame | None = None,
) -> dict[str, Any]:
    selected = _window_events(events, start, end)
    days = (utc(end) - utc(start)).total_seconds() / 86400.0
    armed = int(selected["runner_armed"].sum()) if len(selected) else 0
    values = metrics(selected)
    deltas = (
        selected.sort_values("entry_time")["entry_time"].diff().dt.total_seconds()
        / 3600.0
        if len(selected)
        else pd.Series(dtype=float)
    )
    result = {
        **values,
        "runner_armed_events": armed,
        "runner_armed_precision": float(armed / len(selected)) if len(selected) else np.nan,
        "signals_per_30d": float(len(selected) * 30.0 / days),
        "within_24h_previous_share": float(deltas.le(24.0).mean())
        if len(deltas)
        else np.nan,
        "median_hours_between_signals": float(deltas.median())
        if len(deltas)
        else np.nan,
    }
    if baseline is not None:
        baseline_selected = _window_events(baseline, start, end)
        baseline_armed = int(baseline_selected["runner_armed"].sum())
        result["event_retention"] = float(len(selected) / len(baseline_selected)) if len(baseline_selected) else np.nan
        result["runner_armed_recall"] = float(armed / baseline_armed) if baseline_armed else np.nan
    return result


def _fold_bounds(label: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    if label == "2026P1":
        return utc("2026-01-01"), utc("2026-03-01")
    year = int(label[:4])
    half = int(label[-1])
    if half == 1:
        return utc(f"{year}-01-01"), utc(f"{year}-07-01")
    return utc(f"{year}-07-01"), utc(f"{year + 1}-01-01")


def fold_density_table(
    events: pd.DataFrame,
    folds: Iterable[str],
    *,
    baseline: pd.DataFrame | None = None,
) -> pd.DataFrame:
    rows = []
    for label in folds:
        start, end = _fold_bounds(label)
        rows.append(
            {
                "fold": label,
                **density_metrics(
                    events,
                    start=start,
                    end=end,
                    baseline=baseline,
                ),
            }
        )
    return pd.DataFrame(rows)


def _candidate_values(config: Mapping[str, Any], factor: str) -> list[float | int]:
    mapping = {
        "entry_spread_atr": "entry_spread_atr_candidates",
        "entry_slope_atr_per_bar": "entry_slope_atr_per_bar_candidates",
        "strong_dwell_bars": "strong_dwell_bar_candidates",
        "neutral_dwell_bars": "neutral_dwell_bar_candidates",
    }
    return list(config["trend_regime"][mapping[factor]])


def _rank_row(
    row: Mapping[str, Any], *, target_density: float
) -> tuple[float, float, float, float, float]:
    if not bool(row["eligible"]):
        return (1.0, float("inf"), float("inf"), float("inf"), float("inf"))
    density = float(row["median_signals_per_30d"])
    above_target = density > target_density
    return (
        0.0,
        1.0 if above_target else 0.0,
        density if above_target else -float(row["median_runner_armed_precision"]),
        -float(row["runner_armed_recall"]),
        -float(row["median_runner_armed_precision"]),
    )


def select_phase(config: dict[str, Any]) -> None:
    frame, quality = load_frame(config)
    pairs = build_v3_pairs(frame, config)
    baseline = simulate_v3(pairs, frame, config)
    splits = config["splits"]
    dev_start = utc(splits["development_start_inclusive"])
    dev_end = utc(splits["development_end_exclusive"])
    baseline_dev = _window_events(baseline, dev_start, dev_end)
    folds = list(map(str, splits["development_folds"]))
    params: dict[str, float | int] = deepcopy(config["selection"]["initial"])
    grid_rows: list[dict[str, Any]] = []
    stage = 0
    for factor in config["selection"]["ordered_single_factors"]:
        stage += 1
        rows: list[dict[str, Any]] = []
        for value in _candidate_values(config, factor):
            candidate_params = deepcopy(params)
            candidate_params[str(factor)] = value
            events = simulate_regime(pairs, frame, config, candidate_params)
            dev = _window_events(events, dev_start, dev_end)
            table = fold_density_table(dev, folds, baseline=baseline_dev)
            runner_recall = (
                float(dev["runner_armed"].sum() / baseline_dev["runner_armed"].sum())
                if len(baseline_dev) and int(baseline_dev["runner_armed"].sum())
                else np.nan
            )
            eligible = bool(
                np.isfinite(runner_recall)
                and runner_recall
                >= float(config["selection"]["minimum_runner_recall"])
            )
            row = {
                "stage": stage,
                "factor": factor,
                "value": value,
                **candidate_params,
                "events": len(dev),
                "runner_armed_events": int(dev["runner_armed"].sum()) if len(dev) else 0,
                "runner_armed_recall": runner_recall,
                "median_signals_per_30d": float(table["signals_per_30d"].median()),
                "max_signals_per_30d": float(table["signals_per_30d"].max()),
                "median_runner_armed_precision": float(
                    table["runner_armed_precision"].median()
                ),
                "median_mean_net_bp": float(table["mean_net_bp"].median()),
                "worst_fold_mean_net_bp": float(table["mean_net_bp"].min()),
                "eligible": eligible,
            }
            rows.append(row)
            grid_rows.append(row)
        if not any(bool(row["eligible"]) for row in rows):
            raise RuntimeError(f"no eligible value for {factor}: {rows}")
        winner = min(
            rows,
            key=lambda row: _rank_row(
                row,
                target_density=float(config["selection"]["target_signals_per_30d"]),
            ),
        )
        params[str(factor)] = winner[str(factor)]

    selected = simulate_regime(pairs, frame, config, params)
    selected_dev = _window_events(selected, dev_start, dev_end)
    baseline_table = fold_density_table(baseline_dev, folds, baseline=baseline_dev)
    selected_table = fold_density_table(selected_dev, folds, baseline=baseline_dev)
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(pairs, RESULTS / "v3_raw_pairs.csv.gz")
    write_csv(baseline_dev, RESULTS / "selection_v3_baseline_trades.csv.gz")
    write_csv(selected_dev, RESULTS / "selection_regime_trades.csv.gz")
    write_csv(pd.DataFrame(grid_rows), RESULTS / "selection_coordinate_grid.csv")
    write_csv(baseline_table.assign(policy="V3 position lock"), RESULTS / "selection_v3_fold_metrics.csv")
    write_csv(selected_table.assign(policy="V4 trend regime"), RESULTS / "selection_v4_fold_metrics.csv")
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "selection",
        "status": "frozen_for_audit",
        "selected_params": params,
        "source": quality,
        "holdout_rows_read": int(quality["holdout_rows_read"]),
        "raw_pair_rows": len(pairs),
        "development_baseline": density_metrics(
            baseline_dev, start=dev_start, end=dev_end, baseline=baseline_dev
        ),
        "development_selected": density_metrics(
            selected_dev, start=dev_start, end=dev_end, baseline=baseline_dev
        ),
        "selection_order": list(config["selection"]["ordered_single_factors"]),
        "selection_grid_sha256": sha256_file(RESULTS / "selection_coordinate_grid.csv"),
        "config_sha256": sha256_file(CONFIG_PATH),
        "script_sha256": sha256_file(SCRIPT_PATH),
        "pine_v3_sha256": sha256_file(PINE_V3_PATH),
        "audit_rows_read": 0,
        "repository_holdout_rows_read": 0,
    }
    write_json(SELECTION_RECEIPT, receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


def _assert_committed(path: Path, expected_sha: str) -> None:
    relative = path.relative_to(ROOT).as_posix()
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    committed = subprocess.check_output(
        ["git", "show", f"HEAD:{relative}"], cwd=ROOT
    )
    actual = hashlib.sha256(committed).hexdigest()
    if actual != expected_sha:
        raise RuntimeError(f"{relative} is not frozen in HEAD at {expected_sha}")


def _control_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "signal_contract": {
            "horizon_bars": int(config["execution"]["horizon_bars"]),
            "round_trip_cost_fraction": float(
                config["execution"]["round_trip_cost_fraction"]
            ),
            "initial_disaster_stop_atr": float(
                config["execution"]["initial_disaster_stop_atr"]
            ),
            "fixed_target_atr": 5.0,
        },
        "matched_control": config["matched_control"],
    }


def audit_phase(config: dict[str, Any]) -> None:
    receipt = json.loads(SELECTION_RECEIPT.read_text(encoding="utf-8"))
    if receipt.get("status") != "frozen_for_audit":
        raise RuntimeError("selection receipt is not frozen for audit")
    _assert_committed(CONFIG_PATH, str(receipt["config_sha256"]))
    _assert_committed(SCRIPT_PATH, str(receipt["script_sha256"]))
    _assert_committed(SELECTION_RECEIPT, sha256_file(SELECTION_RECEIPT))
    frame, quality = load_frame(config)
    pairs = build_v3_pairs(frame, config)
    if sha256_file(PINE_V3_PATH) != str(receipt["pine_v3_sha256"]):
        raise RuntimeError("V3 Pine source changed after selection")
    baseline = simulate_v3(pairs, frame, config)
    selected = simulate_regime(
        pairs, frame, config, receipt["selected_params"]
    )
    splits = config["splits"]
    start = utc(splits["audit_start_inclusive"])
    end = utc(splits["audit_end_exclusive"])
    baseline_audit = _window_events(baseline, start, end)
    selected_audit = _window_events(selected, start, end)
    audit_folds = list(map(str, splits["audit_slices"]))
    baseline_table = fold_density_table(
        baseline_audit, audit_folds, baseline=baseline_audit
    )
    selected_table = fold_density_table(
        selected_audit, audit_folds, baseline=baseline_audit
    )
    controls, pairs_control = matched_controls(
        selected_audit,
        frame,
        _control_config(config),
        policy="ma_trail1_after_2atr",
        start=start,
        end=end,
    )
    matched = pairs_control[pairs_control["match_status"].eq("matched_exact")].copy()
    paired_p = signflip_p(
        matched["paired_excess_return"], resamples=100_000, seed=90415
    )
    assignments = _assignment_metrics(controls)
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(baseline_audit, RESULTS / "audit_v3_baseline_trades.csv.gz")
    write_csv(selected_audit, RESULTS / "audit_v4_regime_trades.csv.gz")
    write_csv(baseline_table.assign(policy="V3 position lock"), RESULTS / "audit_v3_fold_metrics.csv")
    write_csv(selected_table.assign(policy="V4 trend regime"), RESULTS / "audit_v4_fold_metrics.csv")
    write_csv(controls, RESULTS / "audit_matched_controls.csv.gz")
    write_csv(pairs_control, RESULTS / "audit_matched_control_pairs.csv")
    write_json(RESULTS / "audit_control_assignments.json", assignments)
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "phase": "audit",
        "status": "research_display_only",
        "selected_params": receipt["selected_params"],
        "source": quality,
        "holdout_rows_read": int(quality["holdout_rows_read"]),
        "baseline": density_metrics(
            baseline_audit, start=start, end=end, baseline=baseline_audit
        ),
        "selected": density_metrics(
            selected_audit, start=start, end=end, baseline=baseline_audit
        ),
        "matched_control": {
            "matched_events": len(matched),
            "mean_candidate_net_bp": float(
                matched["candidate_net_return"].mean() * 1e4
            )
            if len(matched)
            else np.nan,
            "mean_control_net_bp": float(
                matched["control_mean_net_return"].mean() * 1e4
            )
            if len(matched)
            else np.nan,
            "mean_paired_excess_bp": float(
                matched["paired_excess_return"].mean() * 1e4
            )
            if len(matched)
            else np.nan,
            "paired_signflip_p": paired_p,
            "assignment_metrics": assignments,
        },
        "gates": {
            "density_reduced_by_at_least_60pct": bool(
                len(selected_audit) <= 0.4 * len(baseline_audit)
            ),
            "runner_precision_improved": bool(
                selected_audit["runner_armed"].mean()
                > baseline_audit["runner_armed"].mean()
            )
            if len(selected_audit) and len(baseline_audit)
            else False,
            "net_mean_positive": bool(selected_audit["net_return"].mean() > 0.0)
            if len(selected_audit)
            else False,
            "matched_excess_positive": bool(
                len(matched) and matched["paired_excess_return"].mean() > 0.0
            ),
            "matched_p_lt_0_01": bool(np.isfinite(paired_p) and paired_p < 0.01),
        },
        "repository_holdout_rows_read": 0,
        "tradingview_or_production_mutated": False,
    }
    write_json(RESULTS / "audit_summary.json", summary)
    print(json.dumps(json_value(summary), ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("select", "audit"), required=True)
    args = parser.parse_args()
    config = load_config()
    if args.phase == "select":
        select_phase(config)
    else:
        audit_phase(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
