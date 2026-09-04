#!/usr/bin/env python3
"""Research asset-specific 15m K1->K2 trend profiles without holdout access.

Signal inputs use only the completed K2 bar ``t`` and earlier.  The K1/K2
morphology is shared across assets, while a preregistered sequential search
changes one categorical/scalar factor at a time: MA architecture, causal
compression-release-acceptance vote threshold, runner ATR buffer, and the
small banked fraction.  Entry is the next contiguous bar open.  Only outcome
resolution reads the following 96 bars.

The bounded CSV loader stops before the repository holdout boundary.  Selection,
audit, and final pre-holdout confirmation are physically separated by committed
receipts so later periods cannot influence earlier parameter choices.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

import scripts.research_btcusdtp_15m_trend_regime_episode as regime_engine
from scripts.backtest_two_key_candle_pine_v8_btc_1h import signflip_p
from scripts.research_btcusdtp_15m_dual_ma_runner import (
    BAR_DELTA,
    _atr_buckets,
    _stop_fill,
    add_dual_references,
)
from scripts.research_btcusdtp_15m_ma_state_trend import (
    json_value,
    metrics,
    utc,
    write_csv,
    write_json,
)
from scripts.research_pine_eth_15m import (
    load_development_frame,
    sha256_bounded_frame,
)
from scripts.research_two_key_candle_ma_retest_1h import pine_rma, sha256_file

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).resolve()


def load_config(path: Path) -> dict[str, Any]:
    """Load one committed asset experiment contract."""

    return json.loads(path.read_text(encoding="utf-8"))


def _assert_head_frozen(path: Path) -> None:
    relative = path.relative_to(ROOT).as_posix()
    subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    expected = subprocess.check_output(["git", "show", f"HEAD:{relative}"], cwd=ROOT)
    if hashlib.sha256(expected).digest() != hashlib.sha256(path.read_bytes()).digest():
        raise RuntimeError(f"{relative} differs from frozen HEAD")


def _assert_committed_receipt(path: Path, expected_phase: str) -> dict[str, Any]:
    _assert_head_frozen(path)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("phase") != expected_phase or not receipt.get("frozen", False):
        raise RuntimeError(f"{expected_phase} receipt is not committed and frozen")
    return receipt


def _true_range(segment: pd.DataFrame) -> np.ndarray:
    high = segment["high"].to_numpy(dtype=float)
    low = segment["low"].to_numpy(dtype=float)
    close = segment["close"].to_numpy(dtype=float)
    previous = np.r_[np.nan, close[:-1]]
    return np.nanmax(
        np.vstack((high - low, np.abs(high - previous), np.abs(low - previous))),
        axis=0,
    )


def load_bounded_frame(
    config: Mapping[str, Any], *, end_exclusive: pd.Timestamp
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read a causal prefix and derive ATR14 within contiguous segments.

    Inputs are ``open_time/open/high/low/close/volume``.  The only rolling
    feature here is Wilder/Pine ATR14, calculated independently per segment.
    The configured parser chunk must fit entirely between ``end_exclusive``
    and the repository holdout boundary.
    """

    source = config["source_contract"]
    holdout = utc(source["holdout_start"])
    raw = load_development_frame(
        ROOT / str(config["instrument"]["data_path"]),
        safe_end=utc(end_exclusive),
        holdout_start=holdout,
        chunksize=int(source["parser_chunksize"]),
    ).copy()
    raw = raw.reset_index(drop=True)
    raw["segment_id"] = raw["open_time"].diff().ne(BAR_DELTA).cumsum().astype(int)
    atr = np.full(len(raw), np.nan, dtype=float)
    for _, segment in raw.groupby("segment_id", sort=True):
        atr[segment.index.to_numpy(dtype=int)] = pine_rma(_true_range(segment), 14)
    raw["atr"] = atr
    quality = {
        "path": str(config["instrument"]["data_path"]),
        "bounded_prefix_sha256": sha256_bounded_frame(raw),
        "rows_read": len(raw),
        "first_bar": raw["open_time"].iloc[0],
        "last_bar": raw["open_time"].iloc[-1],
        "bounded_end_exclusive": utc(end_exclusive),
        "holdout_start": holdout,
        "holdout_rows_read": int(raw["open_time"].ge(holdout).sum()),
        "segments": int(raw["segment_id"].nunique()),
    }
    if quality["holdout_rows_read"] != int(source["repository_holdout_rows_allowed"]):
        raise RuntimeError("bounded loader materialized repository holdout")
    return raw, quality


def _profile_frame(
    raw: pd.DataFrame, config: Mapping[str, Any], ma_profile_id: str
) -> pd.DataFrame:
    """Build causal trigger/trend and transition features for one MA profile.

    Reads OHLCV only through each row ``t``.  The longest normalizer uses the
    previous 96 completed bars; K1 release uses the previous 24 ranges.
    """

    profile = config["ma_profiles"][ma_profile_id]
    frame = add_dual_references(raw, str(profile["fast"]), str(profile["slow"]))
    safe_atr = frame["atr"].astype(float).replace(0.0, np.nan)
    frame["fast_slow_spread_atr"] = (
        frame["reference_ma"].astype(float) - frame["trend_ma"].astype(float)
    ) / safe_atr
    frame["fast_slope4_atr_per_bar"] = frame[
        "reference_slope_atr_per_bar"
    ].astype(float)
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

    pieces: list[pd.DataFrame] = []
    for _, segment in frame.groupby("segment_id", sort=True):
        part = segment.copy()
        close = part["close"].astype(float)
        candle_range = part["high"].astype(float) - part["low"].astype(float)
        bb_width = 4.0 * close.rolling(20, min_periods=20).std(ddof=0)
        bb_base = bb_width.shift(1).rolling(96, min_periods=48).median()
        part["bb_width_ratio96"] = bb_width / bb_base.replace(0.0, np.nan)
        part["prior_range_median24"] = candle_range.shift(1).rolling(
            24, min_periods=16
        ).median()
        pieces.append(part)
    return pd.concat(pieces).sort_index().reset_index(drop=True)


def _engine_config(config: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "baseline": dict(config["morphology"]),
        "trend_regime": dict(config["trend_regime"]),
    }


def _attach_transition_features(
    pairs: pd.DataFrame, frame: pd.DataFrame, config: Mapping[str, Any]
) -> pd.DataFrame:
    """Attach pre-K1 compression, K1 release, and K2 acceptance votes.

    ``pre_k1_bb_ratio`` is observed at ``K1-1``; ``k1_release_ratio`` compares
    K1 range with the preceding 24 ranges; K2 acceptance ends on completed K2.
    No post-K2 value is accessed.
    """

    if pairs.empty:
        return pairs.copy()
    output = pairs.copy()
    k1 = output["k1_i"].to_numpy(dtype=int)
    pre = np.maximum(k1 - 1, 0)
    k1_range = (
        frame.loc[k1, "high"].to_numpy(dtype=float)
        - frame.loc[k1, "low"].to_numpy(dtype=float)
    )
    prior_range = frame.loc[k1, "prior_range_median24"].to_numpy(dtype=float)
    output["pre_k1_bb_ratio"] = frame.loc[
        pre, "bb_width_ratio96"
    ].to_numpy(dtype=float)
    output["k1_release_ratio"] = np.divide(
        k1_range,
        prior_range,
        out=np.full(len(output), np.nan, dtype=float),
        where=np.isfinite(prior_range) & (prior_range > 0.0),
    )
    transition = config["transition_vote"]
    output["vote_pre_k1_compression"] = output["pre_k1_bb_ratio"].le(
        float(transition["pre_k1_bb_ratio_max"])
    )
    output["vote_k1_release"] = output["k1_release_ratio"].ge(
        float(transition["k1_release_ratio_min"])
    )
    output["vote_k2_acceptance"] = (
        output["k2_close_side_atr"].ge(
            float(transition["k2_close_side_atr_min"])
        )
        & output["k2_touch_depth_atr"].le(
            float(transition["k2_touch_depth_atr_max"])
        )
        & output["k2_wick_share"].ge(float(transition["k2_wick_share_min"]))
    )
    vote_columns = [
        "vote_pre_k1_compression",
        "vote_k1_release",
        "vote_k2_acceptance",
    ]
    output["transition_votes"] = output[vote_columns].fillna(False).sum(axis=1)
    return output


def _candidate_setups(
    frame: pd.DataFrame,
    pairs: pd.DataFrame,
    config: Mapping[str, Any],
    params: Mapping[str, Any],
) -> pd.DataFrame:
    """Choose at most one accepted K1->K2 signal per causal trend regime."""

    if pairs.empty:
        return pd.DataFrame()
    live = pairs[
        pairs["signed_fast_slow_spread_atr"].gt(
            float(config["entry_liveness"]["current_signed_spread_min_exclusive"])
        )
        & pairs["signed_fast_slope4_atr_per_bar"].ge(
            float(config["entry_liveness"]["current_signed_slope_min_inclusive"])
        )
        & pairs["transition_votes"].ge(int(params["transition_min_votes"]))
    ].copy()
    if live.empty:
        return pd.DataFrame()
    regime_params = {
        key: config["entry_liveness"][key]
        for key in (
            "entry_spread_atr",
            "entry_slope_atr_per_bar",
            "strong_dwell_bars",
            "neutral_dwell_bars",
        )
    }
    table = regime_engine.build_regime_table(
        frame, _engine_config(config), regime_params
    )
    instrument = str(config["instrument"]["research_symbol"])
    rows: list[dict[str, Any]] = []
    consumed: set[tuple[int, int, int]] = set()
    horizon = int(config["execution"]["horizon_bars"])
    for signal_i, group in live.groupby("signal_i", sort=True):
        signal_i = int(signal_i)
        direction = int(table.loc[signal_i, "regime_direction"])
        regime_id = int(table.loc[signal_i, "regime_id"])
        if direction == 0 or regime_id < 0:
            continue
        key = (int(frame.loc[signal_i, "segment_id"]), regime_id, direction)
        if key in consumed:
            continue
        eligible = group[group["direction"].eq(direction)].sort_values(
            ["transition_votes", "signal_score"],
            ascending=[False, False],
            kind="mergesort",
        )
        if eligible.empty:
            continue
        chosen = eligible.iloc[0].to_dict()
        entry_i = signal_i + 1
        end_i = entry_i + horizon - 1
        if end_i >= len(frame):
            continue
        if (
            frame.loc[entry_i, "open_time"] - frame.loc[signal_i, "open_time"]
            != BAR_DELTA
            or int(frame.loc[end_i, "segment_id"])
            != int(frame.loc[signal_i, "segment_id"])
        ):
            continue
        regime_start = int(table.loc[signal_i, "regime_start_i"])
        identity = (
            f"{instrument}|15m|{direction}|{utc(chosen['signal_time']).isoformat()}|"
            f"{int(chosen['k1_i'])}|{params['ma_profile']}|"
            f"votes{int(params['transition_min_votes'])}"
        )
        rows.append(
            {
                **chosen,
                "setup_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
                "entry_i": entry_i,
                "entry_time": frame.loc[entry_i, "open_time"],
                "entry_price": float(frame.loc[entry_i, "open"]),
                "regime_id": regime_id,
                "regime_start_i": regime_start,
                "regime_age_bars": signal_i - regime_start,
                "ma_profile": str(params["ma_profile"]),
                "fast_reference": str(frame.loc[signal_i, "trigger_reference"]),
                "slow_reference": str(frame.loc[signal_i, "trend_reference"]),
            }
        )
        consumed.add(key)
    return pd.DataFrame(rows)


def resolve_trade(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve one micro-bank plus completed-close MA-runner trade.

    The active stop is checked before same-bar targets.  A runner update based
    on close ``t`` becomes active only on bar ``t+1``.  Partial profit never
    raises the remaining stop, preserving the trend tail.
    """

    execution = config["execution"]
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    signal_atr = float(event["signal_atr"])
    horizon = int(execution["horizon_bars"])
    end_i = min(entry_i + horizon - 1, len(frame) - 1)
    if int(frame.loc[end_i, "segment_id"]) != int(frame.loc[entry_i, "segment_id"]):
        return {"resolved": False, "reason": "horizon_crosses_gap"}

    initial_stop_atr = float(execution["initial_disaster_stop_atr"])
    runner_buffer = float(params["runner_buffer_atr"])
    bank_total = float(params["bank_total_fraction"])
    levels = list(map(float, execution["bank_levels_atr"])) if bank_total > 0 else []
    tranche = bank_total / len(levels) if levels else 0.0
    targets = [entry + direction * level * signal_atr for level in levels]
    active_stop = entry - direction * initial_stop_atr * signal_atr
    stop_source = "hard"
    remaining = 1.0
    realized_gross = 0.0
    partial_hits = 0
    runner_armed = False
    runner_arm_i: int | None = None
    exit_i: int | None = None
    exit_price: float | None = None
    outcome = ""
    mfe_until_exit = 0.0
    mae_until_exit = 0.0
    full_mfe = 0.0
    full_mae = 0.0

    for index in range(entry_i, end_i + 1):
        open_price = float(frame.loc[index, "open"])
        high = float(frame.loc[index, "high"])
        low = float(frame.loc[index, "low"])
        close = float(frame.loc[index, "close"])
        favourable = high - entry if direction > 0 else entry - low
        adverse = entry - low if direction > 0 else high - entry
        full_mfe = max(full_mfe, favourable)
        full_mae = max(full_mae, adverse)
        if exit_i is not None:
            continue
        mfe_until_exit = max(mfe_until_exit, favourable)
        mae_until_exit = max(mae_until_exit, adverse)

        hit_stop = low <= active_stop if direction > 0 else high >= active_stop
        if hit_stop:
            exit_i = index
            exit_price = _stop_fill(open_price, active_stop, direction)
            outcome = f"{stop_source}_stop"
            continue

        while partial_hits < len(targets):
            target = targets[partial_hits]
            target_hit = high >= target if direction > 0 else low <= target
            if not target_hit:
                break
            realized_gross += tranche * direction * (target / entry - 1.0)
            remaining -= tranche
            partial_hits += 1

        signed_close_atr = direction * (close - entry) / signal_atr
        if (
            not runner_armed
            and signed_close_atr
            >= float(execution["runner_arm_on_completed_close_atr"])
        ):
            runner_armed = True
            runner_arm_i = index
        if runner_armed:
            candidate = float(frame.loc[index, "trend_ma"]) - (
                direction * runner_buffer * float(frame.loc[index, "atr"])
            )
            improves = (direction > 0 and candidate > active_stop) or (
                direction < 0 and candidate < active_stop
            )
            if improves:
                active_stop = candidate
                stop_source = "ma_runner"

    if exit_i is None:
        exit_i = end_i
        exit_price = float(frame.loc[end_i, "close"])
        outcome = "timeout"
    signed_remainder = direction * (float(exit_price) / entry - 1.0)
    gross = realized_gross + remaining * signed_remainder
    cost = float(execution["round_trip_cost_fraction"])
    risk_fraction = initial_stop_atr * signal_atr / entry
    captured_atr = gross * entry / signal_atr
    return {
        **dict(event),
        "resolved": True,
        "policy": "micro_bank_completed_close_ma_runner",
        "outcome": outcome,
        "exit_i": exit_i,
        "exit_time": frame.loc[exit_i, "open_time"] + BAR_DELTA,
        "exit_price": float(exit_price),
        "hold_bars": exit_i - entry_i + 1,
        "gross_return": gross,
        "net_return": gross - cost,
        "risk_fraction": risk_fraction,
        "return_r": gross / risk_fraction,
        "net_return_r": (gross - cost) / risk_fraction,
        "runner_armed": runner_armed,
        "runner_arm_i": runner_arm_i,
        "runner_buffer_atr": runner_buffer,
        "bank_total_fraction": bank_total,
        "partial_hits": partial_hits,
        "banked_gross_return": realized_gross,
        "remaining_fraction": remaining,
        "final_active_stop": active_stop,
        "mfe_at_exit_atr": mfe_until_exit / signal_atr,
        "mae_at_exit_atr": mae_until_exit / signal_atr,
        "horizon_mfe_atr": full_mfe / signal_atr,
        "horizon_mae_atr": full_mae / signal_atr,
        "capture_of_horizon_mfe": (
            captured_atr / (full_mfe / signal_atr) if full_mfe > 0 else np.nan
        ),
        "gave_back_atr": mfe_until_exit / signal_atr - captured_atr,
    }


def _apply_lock_and_replay(
    setups: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    params: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Apply one-position-at-a-time lock using the evaluated execution policy."""

    if setups.empty:
        return setups.copy(), pd.DataFrame()
    accepted: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    flat_from_i = -1
    for event in setups.sort_values("entry_i", kind="mergesort").to_dict("records"):
        if int(event["entry_i"]) <= flat_from_i:
            continue
        result = resolve_trade(frame, event, config, params)
        if not result.get("resolved"):
            continue
        accepted.append(event)
        trades.append(result)
        flat_from_i = int(result["exit_i"])
    return pd.DataFrame(accepted), pd.DataFrame(trades)


def _fold_table(trades: pd.DataFrame, folds: list[Mapping[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for fold in folds:
        start = utc(fold["start_inclusive"])
        end = utc(fold["end_exclusive"])
        selected = trades[
            trades["entry_time"].map(utc).ge(start)
            & trades["entry_time"].map(utc).lt(end)
        ].copy() if len(trades) else trades.copy()
        rows.append({"fold": str(fold["id"]), **metrics(selected)})
    return pd.DataFrame(rows)


def _summary(
    trades: pd.DataFrame,
    folds: list[Mapping[str, Any]],
    config: Mapping[str, Any],
    phase: str,
) -> dict[str, Any]:
    table = _fold_table(trades, folds)
    means = table["mean_net_bp"].to_numpy(dtype=float)
    counts = table["events"].to_numpy(dtype=int)
    finite = bool(len(means) and np.isfinite(means).all())
    start = utc(config["splits"][phase]["start_inclusive"])
    end = utc(config["splits"][phase]["end_exclusive"])
    days = (end - start).total_seconds() / 86400.0
    base = metrics(trades)
    p95 = float(trades["net_return"].quantile(0.95) * 1e4) if len(trades) else np.nan
    positives = trades.loc[trades["net_return"].gt(0.0), "net_return"] if len(trades) else pd.Series(dtype=float)
    top_count = max(1, int(np.ceil(len(trades) * 0.10))) if len(trades) else 0
    top_positive = (
        trades.nlargest(top_count, "net_return")["net_return"].clip(lower=0.0).sum()
        if len(trades)
        else 0.0
    )
    total_positive = float(positives.sum()) if len(positives) else 0.0
    minimums = config["selection"]["phase_minimums"].get(phase, {})
    return {
        **base,
        "p95_net_bp": p95,
        "positive_folds": int(np.sum(means > 0.0)) if finite else 0,
        "total_folds": len(table),
        "minimum_fold_events": int(counts.min()) if len(counts) else 0,
        "signals_per_30d": float(len(trades) * 30.0 / days) if days > 0 else np.nan,
        "runner_armed_events": int(trades["runner_armed"].sum()) if len(trades) else 0,
        "runner_armed_share": float(trades["runner_armed"].mean()) if len(trades) else np.nan,
        "top_decile_positive_pnl_share": (
            float(top_positive / total_positive) if total_positive > 0 else np.nan
        ),
        "robust_score_bp": (
            float(np.median(means) - 0.5 * np.std(means, ddof=0))
            if finite
            else np.nan
        ),
        "eligible": bool(
            len(trades) >= int(minimums.get("events_total", 0))
            and len(counts)
            and np.all(counts >= int(minimums.get("events_per_fold", 0)))
            and finite
        ),
    }


def _params_key(params: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(params), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def evaluate_params(
    raw: pd.DataFrame,
    config: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    phase: str,
) -> dict[str, Any]:
    profile = _profile_frame(raw, config, str(params["ma_profile"]))
    pairs = regime_engine.build_v3_pairs(profile, _engine_config(config))
    pairs = _attach_transition_features(pairs, profile, config)
    candidate_setups = _candidate_setups(profile, pairs, config, params)
    start = utc(config["splits"][phase]["start_inclusive"])
    end = utc(config["splits"][phase]["end_exclusive"])
    candidate_setups = candidate_setups[
        candidate_setups["entry_time"].map(utc).ge(start)
        & candidate_setups["entry_time"].map(utc).lt(end)
    ].copy() if len(candidate_setups) else candidate_setups.copy()
    setups, trades = _apply_lock_and_replay(
        candidate_setups, profile, config, params
    )
    folds = list(config["splits"][phase]["folds"])
    return {
        "frame": profile,
        "pairs": pairs,
        "candidate_setups": candidate_setups,
        "setups": setups,
        "trades": trades,
        "folds": _fold_table(trades, folds),
        "summary": _summary(trades, folds, config, phase),
    }


def _rank_candidate(
    summary: Mapping[str, Any],
    incumbent: Mapping[str, Any],
    config: Mapping[str, Any],
) -> tuple[int, float, float, float]:
    tail_min = float(config["selection"]["p95_retention_min"])
    incumbent_p95 = float(incumbent["p95_net_bp"])
    candidate_p95 = float(summary["p95_net_bp"])
    tail_ok = bool(
        not np.isfinite(incumbent_p95)
        or incumbent_p95 <= 0.0
        or candidate_p95 >= incumbent_p95 * tail_min
    )
    eligible = bool(summary["eligible"] and tail_ok)
    return (
        1 if eligible else 0,
        float(summary["robust_score_bp"]) if eligible else -np.inf,
        float(summary["mean_net_bp"]) if eligible else -np.inf,
        float(summary["p95_net_bp"]) if eligible else -np.inf,
    )


def _factor_values(config: Mapping[str, Any], factor: str) -> list[Any]:
    return list(config["selection"]["candidates"][factor])


def _causality_probe(
    raw: pd.DataFrame, config: Mapping[str, Any], params: Mapping[str, Any]
) -> dict[str, Any]:
    """Mutate future OHLCV and require all pre-cutoff signal identities to match."""

    split_start = utc(config["splits"]["selection"]["start_inclusive"])
    split_end = utc(config["splits"]["selection"]["end_exclusive"])
    cutoff_candidates = raw.index[
        raw["open_time"].ge(split_start) & raw["open_time"].lt(split_end)
    ].to_numpy(dtype=int)
    if len(cutoff_candidates) < 4:
        raise RuntimeError("not enough selection rows for future-mutation probe")
    cutoff = int(cutoff_candidates[int(np.floor(0.75 * (len(cutoff_candidates) - 1)))])
    mutated = raw.copy()
    future = mutated.index > cutoff
    for column in ("open", "high", "low", "close"):
        mutated.loc[future, column] = mutated.loc[future, column].astype(float) * 1.37
    mutated.loc[future, "volume"] = mutated.loc[future, "volume"].astype(float) * 9.0

    def identity(source: pd.DataFrame) -> pd.DataFrame:
        frame = _profile_frame(source, config, str(params["ma_profile"]))
        pairs = regime_engine.build_v3_pairs(frame, _engine_config(config))
        pairs = _attach_transition_features(pairs, frame, config)
        columns = [
            "signal_i",
            "direction",
            "k1_i",
            "k1_gap",
            "transition_votes",
            "signal_score",
        ]
        return pairs.loc[pairs["signal_i"].le(cutoff), columns].reset_index(drop=True)

    original = identity(raw)
    changed = identity(mutated)
    same = original.equals(changed)
    if not same:
        raise AssertionError("future mutation changed pre-cutoff signal identities")
    return {
        "cutoff_i": cutoff,
        "cutoff_time": raw.loc[cutoff, "open_time"],
        "compared_rows": len(original),
        "different_rows": 0,
        "passed": True,
    }


def selection_phase(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    prereg = config_path.with_name("preregistration.json")
    for path in (config_path, prereg, SCRIPT_PATH):
        _assert_head_frozen(path)
    end = utc(config["splits"]["selection"]["end_exclusive"])
    raw, quality = load_bounded_frame(config, end_exclusive=end)
    params = deepcopy(config["selection"]["initial"])
    grid_rows: list[dict[str, Any]] = []
    minimum_gain = float(config["selection"]["minimum_robust_gain_bp"])

    for stage, factor in enumerate(config["selection"]["ordered_factors"], start=1):
        stage_results: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
        incumbent_value = params[factor]
        incumbent_stage = evaluate_params(raw, config, params, phase="selection")
        for value in _factor_values(config, str(factor)):
            trial_params = deepcopy(params)
            trial_params[str(factor)] = value
            evaluated = evaluate_params(raw, config, trial_params, phase="selection")
            row = {
                "stage": stage,
                "factor": factor,
                "value": value,
                "params_key": _params_key(trial_params),
                **trial_params,
                **evaluated["summary"],
            }
            stage_results.append((value, trial_params, evaluated))
            grid_rows.append(row)
        best_value, best_params, best = max(
            stage_results,
            key=lambda item: _rank_candidate(
                item[2]["summary"], incumbent_stage["summary"], config
            ),
        )
        incumbent_rank = _rank_candidate(
            incumbent_stage["summary"], incumbent_stage["summary"], config
        )
        best_rank = _rank_candidate(best["summary"], incumbent_stage["summary"], config)
        improvement = best_rank[1] - incumbent_rank[1]
        if (
            best_rank[0] == 1
            and (
                incumbent_rank[0] == 0
                or float(improvement) >= minimum_gain
                or best_value == incumbent_value
            )
        ):
            params = best_params
            selected_value = best_value
        else:
            selected_value = incumbent_value
        for row in grid_rows:
            if row["stage"] == stage:
                row["stage_selected"] = bool(row["value"] == selected_value)

    final = evaluate_params(raw, config, params, phase="selection")
    baseline_params = deepcopy(config["selection"]["initial"])
    baseline = evaluate_params(raw, config, baseline_params, phase="selection")
    causality = _causality_probe(raw, config, params)
    experiment = ROOT / "experiments" / "active" / str(config["experiment_id"])
    results = experiment / "results"
    results.mkdir(parents=True, exist_ok=True)
    write_csv(pd.DataFrame(grid_rows), results / "selection_coordinate_grid.csv")
    write_csv(final["pairs"], results / "selection_final_pairs.csv.gz")
    write_csv(final["setups"], results / "selection_final_setups.csv.gz")
    write_csv(final["trades"], results / "selection_final_trades.csv.gz")
    write_csv(final["folds"], results / "selection_final_folds.csv")
    write_csv(baseline["trades"], results / "selection_baseline_trades.csv.gz")
    write_csv(baseline["folds"], results / "selection_baseline_folds.csv")
    receipt = {
        "experiment_id": config["experiment_id"],
        "phase": "selection",
        "frozen": bool(final["summary"]["eligible"]),
        "status": (
            "frozen_for_audit"
            if bool(final["summary"]["eligible"])
            else "rejected_before_audit"
        ),
        "selected_params": params,
        "source": quality,
        "baseline_params": baseline_params,
        "baseline": baseline["summary"],
        "candidate": final["summary"],
        "causality_mutation": causality,
        "audit_rows_read": 0,
        "confirmation_rows_read": 0,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "hashes": {
            "config_sha256": sha256_file(config_path),
            "preregistration_sha256": sha256_file(prereg),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "grid_sha256": sha256_file(results / "selection_coordinate_grid.csv"),
        },
    }
    write_json(results / "selection_receipt.json", receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def _matched_random(
    trades: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    phase: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame(), {
            "matched_events": 0,
            "candidate_mean_net_bp": np.nan,
            "control_mean_net_bp": np.nan,
            "excess_bp": np.nan,
            "signflip_p": np.nan,
        }
    start = utc(config["splits"][phase]["start_inclusive"])
    end = utc(config["splits"][phase]["end_exclusive"])
    horizon = int(config["execution"]["horizon_bars"])
    eligible = np.zeros(len(frame), dtype=bool)
    for signal_i in range(len(frame) - horizon - 1):
        entry_i = signal_i + 1
        last_i = entry_i + horizon - 1
        eligible[signal_i] = bool(
            start <= utc(frame.loc[entry_i, "open_time"]) < end
            and utc(frame.loc[last_i, "open_time"] + BAR_DELTA) <= end
            and int(frame.loc[signal_i, "segment_id"])
            == int(frame.loc[last_i, "segment_id"])
            and np.isfinite(float(frame.loc[signal_i, "atr"]))
            and np.isfinite(float(frame.loc[signal_i, "trend_ma"]))
        )
    buckets = _atr_buckets(frame, eligible)
    months = frame["open_time"].dt.strftime("%Y-%m").to_numpy()
    blocks = (frame["open_time"].dt.hour.to_numpy(dtype=int) // 6).astype(int)
    signal_indices = set(trades["signal_i"].astype(int))
    pool: dict[tuple[str, int, int], list[int]] = {}
    for index in np.flatnonzero(eligible & (buckets >= 0)):
        if int(index) in signal_indices:
            continue
        key = (str(months[index]), int(blocks[index]), int(buckets[index]))
        pool.setdefault(key, []).append(int(index))
    matching = config["matched_control"]
    required = int(matching["controls_per_event"])
    radius = int(matching["exclude_radius_bars"])
    seed = str(matching["seed"])
    control_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    for event in trades.to_dict("records"):
        signal_i = int(event["signal_i"])
        key = (str(months[signal_i]), int(blocks[signal_i]), int(buckets[signal_i]))
        choices = sorted(
            (index for index in pool.get(key, []) if abs(index - signal_i) > radius),
            key=lambda index: hashlib.sha256(
                f"{seed}|{event['setup_id']}|{index}".encode()
            ).hexdigest(),
        )
        if len(choices) < required:
            pair_rows.append(
                {
                    "setup_id": event["setup_id"],
                    "match_status": "unmatched",
                    "matched_control_count": len(choices),
                }
            )
            continue
        values: list[float] = []
        for assignment, control_i in enumerate(choices[:required]):
            control_event = {
                "setup_id": f"control-{control_i}-{int(event['direction'])}",
                "signal_i": control_i,
                "signal_time": frame.loc[control_i, "open_time"],
                "entry_i": control_i + 1,
                "entry_time": frame.loc[control_i + 1, "open_time"],
                "entry_price": float(frame.loc[control_i + 1, "open"]),
                "direction": int(event["direction"]),
                "signal_atr": float(frame.loc[control_i, "atr"]),
                "transition_votes": np.nan,
                "ma_profile": params["ma_profile"],
            }
            result = resolve_trade(frame, control_event, config, params)
            if not result.get("resolved"):
                continue
            values.append(float(result["net_return"]))
            control_rows.append(
                {
                    "candidate_setup_id": event["setup_id"],
                    "assignment": assignment,
                    "control_i": control_i,
                    "control_time": frame.loc[control_i, "open_time"],
                    "direction": int(event["direction"]),
                    "calendar_month": key[0],
                    "utc_six_hour_block": key[1],
                    "atr_quintile": key[2],
                    "control_net_return": result["net_return"],
                }
            )
        if len(values) != required:
            continue
        control_mean = float(np.mean(values))
        pair_rows.append(
            {
                "setup_id": event["setup_id"],
                "match_status": "matched_exact",
                "matched_control_count": required,
                "candidate_net_return": event["net_return"],
                "control_mean_net_return": control_mean,
                "paired_excess_return": float(event["net_return"]) - control_mean,
            }
        )
    controls = pd.DataFrame(control_rows)
    pairs = pd.DataFrame(pair_rows)
    matched = pairs[pairs.get("match_status", pd.Series(dtype=str)).eq("matched_exact")].copy()
    excess = matched["paired_excess_return"].astype(float) if len(matched) else pd.Series(dtype=float)
    summary = {
        "matched_events": len(matched),
        "candidate_mean_net_bp": (
            float(matched["candidate_net_return"].mean() * 1e4)
            if len(matched)
            else np.nan
        ),
        "control_mean_net_bp": (
            float(matched["control_mean_net_return"].mean() * 1e4)
            if len(matched)
            else np.nan
        ),
        "excess_bp": float(excess.mean() * 1e4) if len(excess) else np.nan,
        "signflip_p": (
            float(signflip_p(excess, resamples=100_000, seed=int(matching["p_seed"])))
            if len(excess)
            else np.nan
        ),
    }
    return controls, pairs, summary


def _failure_modes(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for trade in trades.to_dict("records"):
        net = float(trade["net_return"])
        if net > 0.0:
            mode = "winner_large_giveback" if float(trade["gave_back_atr"]) >= 2.0 else "winner_retained"
        elif bool(trade["runner_armed"]):
            mode = "armed_profit_given_back"
        elif int(trade["hold_bars"]) <= 3 and float(trade["mfe_at_exit_atr"]) < 1.0:
            mode = "early_stop_no_followthrough"
        elif float(trade["horizon_mfe_atr"]) >= 2.0:
            mode = "early_stop_then_later_recovered"
        else:
            mode = "false_launch_or_other_loss"
        rows.append({**trade, "failure_mode": mode})
    detailed = pd.DataFrame(rows)
    summary = (
        detailed.groupby("failure_mode", as_index=False)
        .agg(
            events=("setup_id", "size"),
            mean_net_bp=("net_return", lambda values: float(values.mean() * 1e4)),
            total_net_bp=("net_return", lambda values: float(values.sum() * 1e4)),
            mean_horizon_mfe_atr=("horizon_mfe_atr", "mean"),
            mean_giveback_atr=("gave_back_atr", "mean"),
        )
        .sort_values("total_net_bp")
    )
    return summary


def _score_diagnostic(trades: pd.DataFrame) -> dict[str, Any]:
    if trades.empty or trades["transition_votes"].nunique(dropna=True) < 2:
        return {"auc_win": np.nan, "top_decile_events": 0, "top_decile_mean_net_bp": np.nan}
    y = trades["net_return"].gt(0.0).astype(int)
    auc = float(roc_auc_score(y, trades["transition_votes"])) if y.nunique() == 2 else np.nan
    count = max(1, int(np.ceil(len(trades) * 0.10)))
    top = trades.nlargest(count, ["transition_votes", "signal_score"])
    return {
        "auc_win": auc,
        "top_decile_events": len(top),
        "top_decile_mean_net_bp": float(top["net_return"].mean() * 1e4),
    }


def evaluation_phase(
    config_path: Path, config: dict[str, Any], *, phase: str
) -> dict[str, Any]:
    experiment = ROOT / "experiments" / "active" / str(config["experiment_id"])
    results = experiment / "results"
    selection = _assert_committed_receipt(results / "selection_receipt.json", "selection")
    audit_receipt: dict[str, Any] | None = None
    if phase == "confirmation":
        audit_receipt = _assert_committed_receipt(results / "audit_receipt.json", "audit")
    for path in (config_path, config_path.with_name("preregistration.json"), SCRIPT_PATH):
        _assert_head_frozen(path)
    params = dict(selection["selected_params"])
    end = utc(config["splits"][phase]["end_exclusive"])
    raw, quality = load_bounded_frame(config, end_exclusive=end)
    candidate = evaluate_params(raw, config, params, phase=phase)
    baseline_params = dict(selection["baseline_params"])
    baseline = evaluate_params(raw, config, baseline_params, phase=phase)
    controls, pairs, matched = _matched_random(
        candidate["trades"], candidate["frame"], config, params, phase=phase
    )
    failures = _failure_modes(candidate["trades"])
    score = _score_diagnostic(candidate["trades"])
    gates = config["acceptance_gates"]
    summary = candidate["summary"]
    gate_checks = {
        "sample_eligible": bool(summary["eligible"]),
        "mean_net_positive": bool(float(summary["mean_net_bp"]) > 0.0),
        "profit_factor_above_one": bool(float(summary["profit_factor"]) > 1.0),
        "positive_fold_share": bool(
            int(summary["positive_folds"])
            >= int(np.ceil(float(gates["positive_fold_share_min"]) * int(summary["total_folds"])))
        ),
        "matched_excess_positive": bool(float(matched["excess_bp"]) > 0.0),
        "matched_random_p_below": bool(
            float(matched["signflip_p"]) < float(gates["matched_random_p_max"])
        ),
        "p95_vs_baseline_retained": bool(
            not np.isfinite(float(baseline["summary"]["p95_net_bp"]))
            or float(baseline["summary"]["p95_net_bp"]) <= 0.0
            or float(summary["p95_net_bp"])
            >= float(gates["p95_vs_baseline_retention_min"])
            * float(baseline["summary"]["p95_net_bp"])
        ),
    }
    all_gates = bool(all(gate_checks.values()))
    prefix = "audit" if phase == "audit" else "confirmation"
    write_csv(candidate["pairs"], results / f"{prefix}_candidate_pairs.csv.gz")
    write_csv(candidate["setups"], results / f"{prefix}_candidate_setups.csv.gz")
    write_csv(candidate["trades"], results / f"{prefix}_candidate_trades.csv.gz")
    write_csv(candidate["folds"], results / f"{prefix}_candidate_folds.csv")
    write_csv(baseline["trades"], results / f"{prefix}_baseline_trades.csv.gz")
    write_csv(baseline["folds"], results / f"{prefix}_baseline_folds.csv")
    write_csv(controls, results / f"{prefix}_matched_controls.csv.gz")
    write_csv(pairs, results / f"{prefix}_matched_pairs.csv")
    write_csv(failures, results / f"{prefix}_failure_modes.csv")
    receipt = {
        "experiment_id": config["experiment_id"],
        "phase": phase,
        "frozen": True,
        "status": (
            "passed_all_registered_gates" if all_gates else "research_only_failed_gates"
        ),
        "selected_params": params,
        "source": quality,
        "baseline": baseline["summary"],
        "candidate": candidate["summary"],
        "matched_random": matched,
        "transition_score": score,
        "gate_checks": gate_checks,
        "all_registered_gates_pass": all_gates,
        "selection_receipt_sha256": sha256_file(results / "selection_receipt.json"),
        "audit_receipt_sha256": (
            sha256_file(results / "audit_receipt.json")
            if audit_receipt is not None
            else None
        ),
        "repository_holdout_rows_read": int(quality["holdout_rows_read"]),
        "production_or_live_changed": False,
    }
    write_json(results / f"{prefix}_receipt.json", receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument(
        "--phase", required=True, choices=("selection", "audit", "confirmation")
    )
    args = parser.parse_args()
    config_path = args.config.resolve()
    config = load_config(config_path)
    if args.phase == "selection":
        selection_phase(config_path, config)
    else:
        evaluation_phase(config_path, config, phase=str(args.phase))


if __name__ == "__main__":
    main()
