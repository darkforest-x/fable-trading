#!/usr/bin/env python3
"""Research a sparse daily K1->K2 trend runner across altcoin swaps.

Source rows are completed 15-minute OKX candles.  ``aggregate_complete_utc_days``
uses only the 96 rows inside each UTC day and discards incomplete/duplicate days.
All signal features at daily bar ``t`` use OHLCV at ``t`` or earlier: ATR14,
fast/slow HL2 moving averages, a three-bar fast-MA slope, prior-20 range and
volume medians, and a prior-120 median of completed BB20 widths.  A completed
K2 signal is entered only at the next contiguous daily open.  Future rows are
read only by ``resolve_trade`` to label the frozen execution policy.

Selection, audit, and pre-holdout confirmation are physically opened in order.
The bounded source loader is forbidden from materializing any row at or after
the repository holdout boundary.  This script never trains or promotes a model,
changes ACTIVE/forward state, writes TradingView, or touches live orders.
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
from scripts.research_btcusdtp_15m_dual_ma_runner import _stop_fill
from scripts.research_btcusdtp_15m_ma_state_trend import (
    json_value,
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
DAY = pd.Timedelta(days=1)
SOURCE_BAR = pd.Timedelta(minutes=15)


def utc(value: object) -> pd.Timestamp:
    """Return one timezone-aware UTC timestamp."""

    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def load_config(path: Path) -> dict[str, Any]:
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


def _assert_committed_receipt(path: Path, phase: str) -> dict[str, Any]:
    _assert_head_frozen(path)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if receipt.get("phase") != phase or not receipt.get("frozen", False):
        raise RuntimeError(f"{phase} receipt is not frozen")
    return receipt


def _frame_sha256(frame: pd.DataFrame, columns: Iterable[str]) -> str:
    payload = frame[list(columns)].to_csv(index=False, float_format="%.12g").encode()
    return hashlib.sha256(payload).hexdigest()


def aggregate_complete_utc_days(
    raw: pd.DataFrame, *, expected_bars: int = 96
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Aggregate exactly complete UTC days without filling missing source bars.

    Inputs are completed 15-minute ``open_time/open/high/low/close/volume`` rows.
    A daily output consumes exactly the rows from that same UTC calendar day;
    no previous or following day is consulted.  Any duplicate, missing, or
    off-grid source timestamp invalidates the entire daily bar.
    """

    frame = raw.copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True, errors="raise")
    frame = frame.sort_values("open_time", kind="mergesort").reset_index(drop=True)
    frame["utc_day"] = frame["open_time"].dt.floor("D")
    rows: list[dict[str, Any]] = []
    rejected = {"wrong_count": 0, "duplicate": 0, "off_grid": 0}
    for day, group in frame.groupby("utc_day", sort=True):
        times = group["open_time"].sort_values(kind="mergesort")
        if len(group) != expected_bars:
            rejected["wrong_count"] += 1
            continue
        if times.nunique() != expected_bars:
            rejected["duplicate"] += 1
            continue
        expected = pd.date_range(day, periods=expected_bars, freq=SOURCE_BAR)
        if not np.array_equal(times.to_numpy(), expected.to_numpy()):
            rejected["off_grid"] += 1
            continue
        ordered = group.sort_values("open_time", kind="mergesort")
        rows.append(
            {
                "open_time": day,
                "open": float(ordered["open"].iloc[0]),
                "high": float(ordered["high"].max()),
                "low": float(ordered["low"].min()),
                "close": float(ordered["close"].iloc[-1]),
                "volume": float(ordered["volume"].sum()),
                "source_rows": int(len(ordered)),
            }
        )
    daily = pd.DataFrame(rows)
    if len(daily):
        daily = daily.sort_values("open_time", kind="mergesort").reset_index(drop=True)
        daily["segment_id"] = daily["open_time"].diff().ne(DAY).cumsum().astype(int)
    else:
        daily = pd.DataFrame(
            columns=[
                "open_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "source_rows",
                "segment_id",
            ]
        )
    quality = {
        "source_days_seen": int(frame["utc_day"].nunique()),
        "complete_days": int(len(daily)),
        "discarded_days": int(sum(rejected.values())),
        **{f"discarded_{key}": int(value) for key, value in rejected.items()},
    }
    return daily, quality


def load_universe(
    config: Mapping[str, Any], *, end_exclusive: pd.Timestamp
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, dict[str, Any]]:
    """Physically load only the allowed 15m prefix and build complete daily bars."""

    source = config["source_contract"]
    holdout = utc(source["holdout_start"])
    end = utc(end_exclusive)
    if end > utc(source["safe_end_exclusive"]):
        raise RuntimeError("requested end exceeds preregistered safe end")
    frames: dict[str, pd.DataFrame] = {}
    quality_rows: list[dict[str, Any]] = []
    for symbol, relative in sorted(config["universe"]["instruments"].items()):
        try:
            raw = load_development_frame(
                ROOT / str(relative),
                safe_end=end,
                holdout_start=holdout,
                chunksize=int(source["parser_chunksize"]),
            ).copy()
        except ValueError as error:
            if not str(error).startswith("no development rows in "):
                raise
            quality_rows.append(
                {
                    "symbol": symbol,
                    "path": relative,
                    "source_rows_read": 0,
                    "source_prefix_sha256": None,
                    "source_days_seen": 0,
                    "complete_days": 0,
                    "discarded_days": 0,
                    "discarded_wrong_count": 0,
                    "discarded_duplicate": 0,
                    "discarded_off_grid": 0,
                    "first_daily_bar": pd.NaT,
                    "last_daily_bar": pd.NaT,
                    "daily_prefix_sha256": None,
                    "eligible_minimum_history": False,
                    "phase_source_status": "not_listed_yet",
                    "holdout_rows_read": 0,
                }
            )
            continue
        holdout_rows = int(raw["open_time"].ge(holdout).sum())
        if holdout_rows != int(source["repository_holdout_rows_allowed"]):
            raise RuntimeError(f"{symbol} loader materialized repository holdout")
        daily, aggregate_quality = aggregate_complete_utc_days(
            raw, expected_bars=int(source["intraday_bars_per_complete_day"])
        )
        daily = daily[daily["open_time"].lt(end)].reset_index(drop=True)
        eligible = len(daily) >= int(source["minimum_daily_history_bars"])
        if eligible:
            frames[str(symbol)] = daily
        quality_rows.append(
            {
                "symbol": symbol,
                "path": relative,
                "source_rows_read": int(len(raw)),
                "source_prefix_sha256": sha256_bounded_frame(raw),
                **aggregate_quality,
                "first_daily_bar": daily["open_time"].iloc[0] if len(daily) else pd.NaT,
                "last_daily_bar": daily["open_time"].iloc[-1] if len(daily) else pd.NaT,
                "daily_prefix_sha256": _frame_sha256(
                    daily,
                    ["open_time", "open", "high", "low", "close", "volume"],
                )
                if len(daily)
                else None,
                "eligible_minimum_history": bool(eligible),
                "phase_source_status": "eligible" if eligible else "insufficient_history",
                "holdout_rows_read": holdout_rows,
            }
        )
    quality = pd.DataFrame(quality_rows)
    summary = {
        "configured_symbols": int(len(config["universe"]["instruments"])),
        "eligible_symbols": int(len(frames)),
        "source_rows_read": int(quality["source_rows_read"].sum()),
        "complete_daily_bars": int(quality["complete_days"].sum()),
        "discarded_days": int(quality["discarded_days"].sum()),
        "first_daily_bar": quality["first_daily_bar"].min(),
        "last_daily_bar": quality["last_daily_bar"].max(),
        "bounded_end_exclusive": end,
        "holdout_start": holdout,
        "repository_holdout_rows_read": int(quality["holdout_rows_read"].sum()),
    }
    if summary["repository_holdout_rows_read"] != 0:
        raise RuntimeError("universe load crossed repository holdout")
    return frames, quality, summary


def _true_range(segment: pd.DataFrame) -> np.ndarray:
    high = segment["high"].to_numpy(dtype=float)
    low = segment["low"].to_numpy(dtype=float)
    close = segment["close"].to_numpy(dtype=float)
    previous = np.r_[np.nan, close[:-1]]
    return np.nanmax(
        np.vstack((high - low, np.abs(high - previous), np.abs(low - previous))),
        axis=0,
    )


def _moving_average(source: pd.Series, kind: str, length: int) -> pd.Series:
    if kind == "SMA":
        return source.rolling(length, min_periods=length).mean()
    if kind == "EMA":
        return source.ewm(span=length, adjust=False, min_periods=length).mean()
    raise ValueError(f"unsupported MA kind: {kind}")


def build_profile(
    daily: pd.DataFrame, config: Mapping[str, Any], profile_id: str
) -> pd.DataFrame:
    """Build causal daily K1/K2 features independently inside each data segment.

    Reads daily OHLCV only through row ``t``. ATR uses 14 completed daily true
    ranges; MAs use current/prior HL2; fast slope uses three completed bars;
    K1 release denominators use the prior 20 ranges/volumes; BB normalization
    uses BB20 at ``t`` divided by the median of BB20 values ending at ``t-1``
    over at most 120 days.
    """

    profile = config["ma_profiles"][profile_id]
    episode = config["episode"]
    pieces: list[pd.DataFrame] = []
    for _, original in daily.groupby("segment_id", sort=True):
        part = original.copy().reset_index(drop=True)
        part["atr"] = pine_rma(_true_range(part), 14)
        hl2 = (part["high"].astype(float) + part["low"].astype(float)) / 2.0
        part["fast_ma"] = _moving_average(
            hl2, str(profile["fast_kind"]), int(profile["fast_length"])
        )
        part["slow_ma"] = _moving_average(
            hl2, str(profile["slow_kind"]), int(profile["slow_length"])
        )
        safe_atr = part["atr"].replace(0.0, np.nan)
        part["spread_atr"] = (part["fast_ma"] - part["slow_ma"]) / safe_atr
        part["fast_slope3_atr"] = (
            part["fast_ma"] - part["fast_ma"].shift(3)
        ) / (3.0 * safe_atr)
        candle_range = part["high"].astype(float) - part["low"].astype(float)
        part["prior_range_median20"] = candle_range.shift(1).rolling(
            20, min_periods=12
        ).median()
        part["prior_volume_median20"] = part["volume"].shift(1).rolling(
            20, min_periods=12
        ).median()
        bb_width = 4.0 * part["close"].rolling(20, min_periods=20).std(ddof=0)
        bb_base = bb_width.shift(1).rolling(120, min_periods=60).median()
        part["bb_width_ratio120"] = bb_width / bb_base.replace(0.0, np.nan)
        neutral = (
            part["spread_atr"].abs().le(float(episode["neutral_spread_atr_max"]))
            & part["fast_slope3_atr"].abs().le(
                float(episode["neutral_fast_slope3_atr_max"])
            )
            & part["bb_width_ratio120"].le(
                float(episode["neutral_bb_width_ratio120_max"])
            )
        )
        streak = int(episode["neutral_streak_bars"])
        part["neutral_bar"] = neutral.fillna(False)
        part["neutral_complete"] = (
            neutral.fillna(False).astype(int).rolling(streak, min_periods=streak).sum()
            >= streak
        )
        pieces.append(part)
    if not pieces:
        return daily.copy()
    frame = pd.concat(pieces, ignore_index=True)
    frame["segment_id"] = frame["open_time"].diff().ne(DAY).cumsum().astype(int)
    frame["ma_profile"] = profile_id
    return frame


def _k1_features(
    frame: pd.DataFrame, index: int, config: Mapping[str, Any]
) -> dict[str, Any] | None:
    row = frame.iloc[index]
    required = [
        row["atr"],
        row["fast_ma"],
        row["slow_ma"],
        row["prior_range_median20"],
        row["prior_volume_median20"],
    ]
    if not np.isfinite(np.asarray(required, dtype=float)).all() or float(row["atr"]) <= 0:
        return None
    open_price = float(row["open"])
    close = float(row["close"])
    high = float(row["high"])
    low = float(row["low"])
    fast = float(row["fast_ma"])
    slow = float(row["slow_ma"])
    atr = float(row["atr"])
    candle_range = high - low
    body = abs(close - open_price)
    if candle_range <= 0:
        return None
    morphology = config["morphology"]
    directions: list[int] = []
    if open_price <= fast and close > fast and close > open_price:
        directions.append(1)
    if open_price >= fast and close < fast and close < open_price:
        directions.append(-1)
    for direction in directions:
        close_location = (close - low) / candle_range if direction > 0 else (high - close) / candle_range
        signed_slow_side = direction * (close - slow) / atr
        if (
            body / atr >= float(morphology["k1_body_atr_min"])
            and candle_range / atr >= float(morphology["k1_range_atr_min"])
            and close_location >= float(morphology["k1_close_location_min"])
            and signed_slow_side >= float(morphology["k1_slow_side_tolerance_atr"])
        ):
            range_release = candle_range / float(row["prior_range_median20"])
            volume_release = float(row["volume"]) / float(row["prior_volume_median20"])
            votes = config["transition_votes"]
            return {
                "direction": direction,
                "k1_body_atr": body / atr,
                "k1_range_atr": candle_range / atr,
                "k1_close_location": close_location,
                "k1_signed_slow_side_atr": signed_slow_side,
                "k1_range_release": range_release,
                "k1_volume_release": volume_release,
                "vote_k1_range_release": bool(
                    range_release >= float(votes["k1_range_vs_prior20_min"])
                ),
                "vote_k1_volume_release": bool(
                    volume_release >= float(votes["k1_volume_vs_prior20_min"])
                ),
                "vote_k1_slow_break": bool(
                    signed_slow_side >= float(votes["k1_close_beyond_slow_atr_min"])
                ),
            }
    return None


def _k2_features(
    frame: pd.DataFrame,
    index: int,
    direction: int,
    config: Mapping[str, Any],
) -> dict[str, Any] | None:
    row = frame.iloc[index]
    values = [row["atr"], row["fast_ma"], row["slow_ma"], row["fast_slope3_atr"]]
    if not np.isfinite(np.asarray(values, dtype=float)).all() or float(row["atr"]) <= 0:
        return None
    open_price = float(row["open"])
    close = float(row["close"])
    high = float(row["high"])
    low = float(row["low"])
    fast = float(row["fast_ma"])
    atr = float(row["atr"])
    candle_range = high - low
    if candle_range <= 0:
        return None
    body_near = min(open_price, close) if direction > 0 else max(open_price, close)
    touch_depth = (fast - low) / atr if direction > 0 else (high - fast) / atr
    body_side = direction * (body_near - fast) / atr
    close_side = direction * (close - fast) / atr
    wick = min(open_price, close) - low if direction > 0 else high - max(open_price, close)
    wick_share = wick / candle_range
    body_ratio = abs(close - open_price) / candle_range
    morphology = config["morphology"]
    directional = direction * (close - open_price) > 0
    if not (
        touch_depth >= -float(morphology["k2_touch_tolerance_atr"])
        and touch_depth <= float(morphology["k2_touch_depth_atr_max"])
        and body_side >= float(morphology["k2_body_side_tolerance_atr"])
        and close_side >= float(morphology["k2_close_side_atr_min"])
        and wick_share >= float(morphology["k2_wick_share_min"])
        and body_ratio <= float(morphology["k2_body_ratio_max"])
        and (
            directional
            or not bool(morphology["k2_directional_body_required"])
        )
    ):
        return None
    signed_spread = direction * float(row["spread_atr"])
    signed_slope = direction * float(row["fast_slope3_atr"])
    votes = config["transition_votes"]
    return {
        "k2_touch_depth_atr": touch_depth,
        "k2_body_side_atr": body_side,
        "k2_close_side_atr": close_side,
        "k2_wick_share": wick_share,
        "k2_body_ratio": body_ratio,
        "k2_signed_spread_atr": signed_spread,
        "k2_signed_fast_slope3_atr": signed_slope,
        "vote_k2_trend_acceptance": bool(
            signed_spread >= float(votes["k2_signed_spread_atr_min"])
            and signed_slope >= float(votes["k2_signed_fast_slope3_atr_min"])
        ),
    }


def build_episode_signals(
    frame: pd.DataFrame,
    symbol: str,
    config: Mapping[str, Any],
    params: Mapping[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Scan causal neutral episodes and accept only the first morphological K2.

    The state machine consumes a neutral episode when K1 is armed, whether or
    not K2 later appears.  Therefore repeated crossings in one range cannot
    rearm.  All K2 votes are observed on completed K2; a vote rejection also
    consumes the episode and never waits for a more favorable later K2.
    """

    attempts: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    morphology = config["morphology"]
    min_gap = int(morphology["k1_k2_gap_min"])
    max_gap = int(params["k1_k2_gap_max"])
    min_votes = int(params["transition_min_votes"])
    recency = int(params["neutral_recency_bars"])
    minimum_history = int(config["source_contract"]["minimum_daily_history_bars"])
    for segment_id, segment in frame.groupby("segment_id", sort=True):
        indices = segment.index.to_numpy(dtype=int)
        available = False
        episode_id = 0
        last_neutral_i: int | None = None
        pending: dict[str, Any] | None = None
        previous_neutral_complete = False
        for local_position, index in enumerate(indices):
            neutral_complete = bool(frame.loc[index, "neutral_complete"])
            new_neutral = neutral_complete and not previous_neutral_complete
            previous_neutral_complete = neutral_complete
            if new_neutral:
                episode_id += 1
                available = True
                pending = None
            if neutral_complete:
                last_neutral_i = index

            if pending is not None:
                gap = index - int(pending["k1_i"])
                if gap > max_gap:
                    pending["attempt_status"] = "expired_without_k2"
                    attempts.append(pending)
                    pending = None
                elif gap >= min_gap:
                    k2 = _k2_features(frame, index, int(pending["direction"]), config)
                    if k2 is not None:
                        vote_values = [
                            bool(pending["vote_k1_range_release"]),
                            bool(pending["vote_k1_volume_release"]),
                            bool(pending["vote_k1_slow_break"]),
                            bool(k2["vote_k2_trend_acceptance"]),
                        ]
                        vote_count = int(sum(vote_values))
                        required_vote = params.get("required_vote")
                        required_ok = bool(
                            required_vote is None
                            or pending.get(str(required_vote), k2.get(str(required_vote), False))
                        )
                        accepted = bool(vote_count >= min_votes and required_ok)
                        signal_score = float(
                            0.35 * min(float(pending["k1_range_release"]), 4.0)
                            + 0.15 * min(float(pending["k1_volume_release"]), 4.0)
                            + 0.20 * max(float(k2["k2_signed_spread_atr"]), -1.0)
                            + 0.20 * max(float(k2["k2_signed_fast_slope3_atr"]), -1.0)
                            + 0.10 * float(k2["k2_wick_share"])
                        )
                        identity = (
                            f"{symbol}|1D|{int(pending['direction'])}|"
                            f"{utc(frame.loc[index, 'open_time']).isoformat()}|"
                            f"{int(pending['k1_i'])}|{params['ma_profile']}|{episode_id}"
                        )
                        pair = {
                            **pending,
                            **k2,
                            "attempt_status": "k2_accepted" if accepted else "k2_vote_rejected",
                            "setup_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
                            "symbol": symbol,
                            "segment_id": int(segment_id),
                            "episode_id": episode_id,
                            "k2_i": index,
                            "signal_i": index,
                            "k1_k2_gap": gap,
                            "signal_time": frame.loc[index, "open_time"],
                            "signal_atr": float(frame.loc[index, "atr"]),
                            "k2_low": float(frame.loc[index, "low"]),
                            "k2_high": float(frame.loc[index, "high"]),
                            "transition_votes": vote_count,
                            "accepted_by_votes": accepted,
                            "signal_score": signal_score,
                            "ma_profile": str(params["ma_profile"]),
                        }
                        attempts.append({**pending, "attempt_status": pair["attempt_status"]})
                        pairs.append(pair)
                        pending = None
                        continue
                    direction = int(pending["direction"])
                    atr = float(frame.loc[index, "atr"])
                    signed_close = direction * (
                        float(frame.loc[index, "close"])
                        - float(frame.loc[index, "fast_ma"])
                    ) / atr if np.isfinite(atr) and atr > 0 else -np.inf
                    if signed_close < float(
                        morphology["middle_wrong_side_close_tolerance_atr"]
                    ):
                        pending["attempt_status"] = "invalidated_wrong_side_close"
                        attempts.append(pending)
                        pending = None

            if (
                pending is None
                and available
                and last_neutral_i is not None
                and index >= minimum_history
                and 1 <= index - last_neutral_i <= recency
            ):
                k1 = _k1_features(frame, index, config)
                if k1 is not None:
                    pending = {
                        **k1,
                        "symbol": symbol,
                        "segment_id": int(segment_id),
                        "episode_id": episode_id,
                        "neutral_end_i": int(last_neutral_i),
                        "neutral_to_k1_bars": index - int(last_neutral_i),
                        "k1_i": index,
                        "k1_time": frame.loc[index, "open_time"],
                    }
                    available = False
        if pending is not None:
            pending["attempt_status"] = "right_censored_without_k2"
            attempts.append(pending)
    return pd.DataFrame(attempts), pd.DataFrame(pairs)


def _setup_rows(
    pairs: pd.DataFrame,
    frame: pd.DataFrame,
    phase_start: pd.Timestamp,
    phase_end: pd.Timestamp,
    config: Mapping[str, Any],
) -> pd.DataFrame:
    if pairs.empty:
        return pd.DataFrame()
    minimum_remaining = int(config["execution"]["minimum_phase_remaining_bars"])
    rows: list[dict[str, Any]] = []
    for pair in pairs[pairs["accepted_by_votes"]].to_dict("records"):
        entry_i = int(pair["signal_i"]) + 1
        if entry_i >= len(frame):
            continue
        entry_time = utc(frame.loc[entry_i, "open_time"])
        if not (phase_start <= entry_time < phase_end):
            continue
        if entry_time - utc(pair["signal_time"]) != DAY:
            continue
        segment_id = int(frame.loc[entry_i, "segment_id"])
        segment_last = int(frame.index[frame["segment_id"].eq(segment_id)].max())
        phase_last_candidates = frame.index[frame["open_time"].lt(phase_end)]
        if not len(phase_last_candidates):
            continue
        phase_last = int(phase_last_candidates.max())
        available = min(segment_last, phase_last) - entry_i + 1
        if available < minimum_remaining:
            continue
        rows.append(
            {
                **pair,
                "entry_i": entry_i,
                "entry_time": entry_time,
                "entry_price": float(frame.loc[entry_i, "open"]),
                "available_phase_bars": available,
            }
        )
    return pd.DataFrame(rows)


def resolve_trade(
    frame: pd.DataFrame,
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    phase_end: pd.Timestamp,
) -> dict[str, Any]:
    """Resolve structural risk, gradual R banks and a completed-close MA trail."""

    execution = config["execution"]
    entry_i = int(event["entry_i"])
    direction = int(event["direction"])
    entry = float(event["entry_price"])
    signal_atr = float(event["signal_atr"])
    if not np.isfinite(signal_atr) or signal_atr <= 0 or entry <= 0:
        return {"resolved": False, "reason": "invalid_entry_or_atr"}
    segment_id = int(frame.loc[entry_i, "segment_id"])
    same_segment = frame.index[frame["segment_id"].eq(segment_id)]
    phase_rows = frame.index[frame["open_time"].lt(phase_end)]
    if not len(same_segment) or not len(phase_rows):
        return {"resolved": False, "reason": "missing_phase_rows"}
    horizon_end = min(
        entry_i + int(execution["maximum_horizon_bars"]) - 1,
        int(same_segment.max()),
        int(phase_rows.max()),
    )
    if horizon_end - entry_i + 1 < int(execution["minimum_phase_remaining_bars"]):
        return {"resolved": False, "reason": "insufficient_phase_horizon"}

    override = event.get("risk_atr_override")
    if override is None or not np.isfinite(float(override)):
        stop = (
            float(event["k2_low"]) - float(execution["stop_buffer_atr"]) * signal_atr
            if direction > 0
            else float(event["k2_high"]) + float(execution["stop_buffer_atr"]) * signal_atr
        )
        risk_distance = direction * (entry - stop)
        risk_atr = risk_distance / signal_atr
    else:
        risk_atr = float(override)
        risk_distance = risk_atr * signal_atr
        stop = entry - direction * risk_distance
    if (
        risk_distance <= 0
        or risk_atr < float(execution["minimum_risk_atr"])
        or risk_atr > float(execution["maximum_risk_atr"])
    ):
        return {"resolved": False, "reason": "structural_risk_out_of_bounds"}

    bank_total = float(params["bank_total_fraction"])
    levels = np.asarray(execution["bank_levels_r"], dtype=float)
    weights = np.asarray(execution["bank_level_weights"], dtype=float)
    weights = bank_total * weights / weights.sum()
    targets = entry + direction * levels * risk_distance
    active_stop = stop
    stop_source = "structural"
    remaining = 1.0
    realized_gross = 0.0
    bank_hits = 0
    runner_armed = False
    runner_arm_i: int | None = None
    exit_i: int | None = None
    exit_price: float | None = None
    outcome = ""
    mfe_until_exit = 0.0
    mae_until_exit = 0.0
    full_mfe = 0.0
    full_mae = 0.0

    for index in range(entry_i, horizon_end + 1):
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
        while bank_hits < len(targets):
            target = float(targets[bank_hits])
            hit = high >= target if direction > 0 else low <= target
            if not hit:
                break
            fraction = float(weights[bank_hits])
            realized_gross += fraction * direction * (target / entry - 1.0)
            remaining -= fraction
            bank_hits += 1
        signed_close_r = direction * (close - entry) / risk_distance
        if (
            not runner_armed
            and signed_close_r >= float(execution["runner_arm_on_completed_close_r"])
        ):
            runner_armed = True
            runner_arm_i = index
        if runner_armed:
            reference_column = "slow_ma" if params["trail_reference"] == "slow" else "fast_ma"
            reference = float(frame.loc[index, reference_column])
            atr = float(frame.loc[index, "atr"])
            candidate = reference - direction * float(params["runner_buffer_atr"]) * atr
            improves = (direction > 0 and candidate > active_stop) or (
                direction < 0 and candidate < active_stop
            )
            sane_side = (direction > 0 and candidate < close) or (
                direction < 0 and candidate > close
            )
            if np.isfinite(candidate) and improves and sane_side:
                active_stop = candidate
                stop_source = f"{params['trail_reference']}_ma_runner"

    if exit_i is None:
        exit_i = horizon_end
        exit_price = float(frame.loc[exit_i, "close"])
        phase_limited = horizon_end < entry_i + int(execution["maximum_horizon_bars"]) - 1
        outcome = "phase_end_timeout" if phase_limited else "horizon_timeout"
    gross = realized_gross + remaining * direction * (float(exit_price) / entry - 1.0)
    cost = float(execution["round_trip_cost_fraction"])
    risk_fraction = risk_distance / entry
    gross_r = gross / risk_fraction
    net = gross - cost
    return {
        **dict(event),
        "resolved": True,
        "policy": "gradual_bank_completed_close_ma_runner",
        "outcome": outcome,
        "exit_i": int(exit_i),
        "exit_time": utc(frame.loc[exit_i, "open_time"]) + DAY,
        "exit_price": float(exit_price),
        "hold_bars": int(exit_i - entry_i + 1),
        "initial_stop": float(stop),
        "risk_distance": risk_distance,
        "risk_atr": risk_atr,
        "risk_fraction": risk_fraction,
        "gross_return": gross,
        "net_return": net,
        "gross_return_r": gross_r,
        "net_return_r": net / risk_fraction,
        "bank_total_fraction": bank_total,
        "bank_hits": bank_hits,
        "banked_gross_return": realized_gross,
        "remaining_fraction": remaining,
        "runner_armed": runner_armed,
        "runner_arm_i": runner_arm_i,
        "trail_reference": str(params["trail_reference"]),
        "runner_buffer_atr": float(params["runner_buffer_atr"]),
        "final_active_stop": active_stop,
        "mfe_at_exit_r": mfe_until_exit / risk_distance,
        "mae_at_exit_r": mae_until_exit / risk_distance,
        "horizon_mfe_r": full_mfe / risk_distance,
        "horizon_mae_r": full_mae / risk_distance,
        "captured_gross_r": gross_r,
        "gave_back_r": mfe_until_exit / risk_distance - gross_r,
    }


def _lock_and_resolve(
    setups: pd.DataFrame,
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    phase_end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if setups.empty:
        return setups.copy(), pd.DataFrame(), pd.DataFrame()
    accepted: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    occupied_through = -1
    for event in setups.sort_values("entry_i", kind="mergesort").to_dict("records"):
        if int(event["entry_i"]) <= occupied_through:
            rejected.append({**event, "rejection_reason": "symbol_position_lock"})
            continue
        result = resolve_trade(frame, event, config, params, phase_end=phase_end)
        if not result.get("resolved"):
            rejected.append({**event, "rejection_reason": result.get("reason")})
            continue
        accepted.append(event)
        trades.append(result)
        occupied_through = int(result["exit_i"])
    return pd.DataFrame(accepted), pd.DataFrame(trades), pd.DataFrame(rejected)


def profit_factor(values: Iterable[float]) -> float:
    array = np.asarray(list(values), dtype=float)
    wins = float(array[array > 0].sum())
    losses = float(-array[array < 0].sum())
    if losses <= 0:
        return float("inf") if wins > 0 else float("nan")
    return wins / losses


def _week_signflip(values: pd.DataFrame, column: str, seed: int) -> tuple[int, float]:
    if values.empty:
        return 0, float("nan")
    weeks = pd.to_datetime(values["entry_time"], utc=True).dt.to_period("W-SUN").astype(str)
    clustered = values.assign(_week=weeks).groupby("_week")[column].mean()
    return int(len(clustered)), float(signflip_p(clustered, resamples=100_000, seed=seed))


def trade_metrics(trades: pd.DataFrame, *, p_seed: int) -> dict[str, Any]:
    if trades.empty:
        return {
            "events": 0,
            "symbols": 0,
            "mean_gross_bp": np.nan,
            "mean_net_bp": np.nan,
            "median_net_bp": np.nan,
            "win_rate": np.nan,
            "profit_factor": np.nan,
            "mean_net_r": np.nan,
            "median_hold_bars": np.nan,
            "p95_net_bp": np.nan,
            "max_net_bp": np.nan,
            "positive_symbol_share": np.nan,
            "runner_armed_share": np.nan,
            "banked_any_share": np.nan,
            "top_score_decile_events": 0,
            "top_score_decile_mean_net_bp": np.nan,
            "week_clusters": 0,
            "week_cluster_signflip_p": np.nan,
        }
    symbol_means = trades.groupby("symbol")["net_return"].mean()
    count = max(1, int(np.ceil(len(trades) * 0.10)))
    top = trades.nlargest(count, ["signal_score", "transition_votes"])
    weeks, p = _week_signflip(trades, "net_return", p_seed)
    return {
        "events": int(len(trades)),
        "symbols": int(trades["symbol"].nunique()),
        "mean_gross_bp": float(trades["gross_return"].mean() * 1e4),
        "mean_net_bp": float(trades["net_return"].mean() * 1e4),
        "median_net_bp": float(trades["net_return"].median() * 1e4),
        "win_rate": float(trades["net_return"].gt(0).mean()),
        "profit_factor": float(profit_factor(trades["net_return"])),
        "mean_net_r": float(trades["net_return_r"].mean()),
        "median_hold_bars": float(trades["hold_bars"].median()),
        "p95_net_bp": float(trades["net_return"].quantile(0.95) * 1e4),
        "max_net_bp": float(trades["net_return"].max() * 1e4),
        "positive_symbol_share": float(symbol_means.gt(0).mean()),
        "runner_armed_share": float(trades["runner_armed"].mean()),
        "banked_any_share": float(trades["bank_hits"].gt(0).mean()),
        "top_score_decile_events": int(len(top)),
        "top_score_decile_mean_net_bp": float(top["net_return"].mean() * 1e4),
        "week_clusters": weeks,
        "week_cluster_signflip_p": p,
    }


def _fold_table(
    trades: pd.DataFrame, folds: list[Mapping[str, Any]], *, p_seed: int
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    entry_times = pd.to_datetime(trades["entry_time"], utc=True) if len(trades) else pd.Series(dtype="datetime64[ns, UTC]")
    for offset, fold in enumerate(folds):
        mask = entry_times.ge(utc(fold["start_inclusive"])) & entry_times.lt(
            utc(fold["end_exclusive"])
        ) if len(trades) else np.zeros(0, dtype=bool)
        rows.append(
            {
                "fold": str(fold["id"]),
                **trade_metrics(trades.loc[mask].copy(), p_seed=p_seed + offset),
            }
        )
    return pd.DataFrame(rows)


def _summary(
    trades: pd.DataFrame,
    folds: list[Mapping[str, Any]],
    config: Mapping[str, Any],
    phase: str,
) -> dict[str, Any]:
    seed = int(config["matched_control"]["p_seed"])
    table = _fold_table(trades, folds, p_seed=seed)
    fold_means = table["mean_net_bp"].to_numpy(dtype=float)
    fold_counts = table["events"].to_numpy(dtype=int)
    finite = bool(len(fold_means) and np.isfinite(fold_means).all())
    base = trade_metrics(trades, p_seed=seed)
    minimums = config["selection"]["phase_minimums"][phase]
    return {
        **base,
        "positive_folds": int(np.sum(fold_means > 0)) if finite else 0,
        "total_folds": int(len(table)),
        "minimum_fold_events": int(fold_counts.min()) if len(fold_counts) else 0,
        "robust_score_bp": float(np.median(fold_means) - 0.5 * np.std(fold_means, ddof=0))
        if finite
        else np.nan,
        "eligible": bool(
            len(trades) >= int(minimums["events_total"])
            and int(base["symbols"]) >= int(minimums["symbols_total"])
            and len(fold_counts)
            and np.all(fold_counts >= int(minimums["events_per_fold"]))
            and finite
        ),
    }


def _portfolio(
    trades: pd.DataFrame, config: Mapping[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Apply the preregistered six-position risk cap to closed-equity cash flows."""

    if trades.empty:
        return pd.DataFrame(), pd.DataFrame(), {
            "candidate_events": 0,
            "accepted_events": 0,
            "capacity_rejections": 0,
            "total_return": np.nan,
            "closed_equity_max_drawdown": np.nan,
            "maximum_concurrent_positions": 0,
            "maximum_open_initial_risk_fraction": 0.0,
        }
    contract = config["portfolio"]
    risk_fraction = float(contract["risk_fraction_per_trade"])
    cap = int(contract["maximum_concurrent_positions"])
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    active: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    curve: list[dict[str, Any]] = []
    max_concurrent = 0
    max_open_risk = 0.0

    def settle(until: pd.Timestamp) -> None:
        nonlocal active, equity, peak, max_drawdown
        closing = sorted(
            [position for position in active if utc(position["exit_time"]) <= until],
            key=lambda position: (utc(position["exit_time"]), position["setup_id"]),
        )
        for position in closing:
            pnl = float(position["risk_cash"]) * float(position["net_return_r"])
            equity += pnl
            peak = max(peak, equity)
            drawdown = equity / peak - 1.0
            max_drawdown = min(max_drawdown, drawdown)
            curve.append(
                {
                    "time": utc(position["exit_time"]),
                    "setup_id": position["setup_id"],
                    "symbol": position["symbol"],
                    "event": "exit",
                    "pnl_equity_fraction_initial": pnl,
                    "equity": equity,
                    "drawdown": drawdown,
                }
            )
        closing_ids = {position["setup_id"] for position in closing}
        active = [position for position in active if position["setup_id"] not in closing_ids]

    ordered = trades.sort_values(
        ["entry_time", "transition_votes", "signal_score", "symbol"],
        ascending=[True, False, False, True],
        kind="mergesort",
    )
    for entry_time, group in ordered.groupby("entry_time", sort=True):
        entry_stamp = utc(entry_time)
        settle(entry_stamp)
        for trade in group.to_dict("records"):
            if len(active) >= cap:
                decisions.append({**trade, "portfolio_accepted": False, "portfolio_reason": "capacity"})
                continue
            risk_cash = equity * risk_fraction
            position = {**trade, "risk_cash": risk_cash}
            active.append(position)
            max_concurrent = max(max_concurrent, len(active))
            open_risk = sum(float(item["risk_cash"]) for item in active) / max(equity, 1e-12)
            max_open_risk = max(max_open_risk, open_risk)
            decisions.append(
                {
                    **trade,
                    "portfolio_accepted": True,
                    "portfolio_reason": "accepted",
                    "portfolio_entry_equity": equity,
                    "portfolio_risk_cash": risk_cash,
                }
            )
            curve.append(
                {
                    "time": entry_stamp,
                    "setup_id": trade["setup_id"],
                    "symbol": trade["symbol"],
                    "event": "entry",
                    "pnl_equity_fraction_initial": 0.0,
                    "equity": equity,
                    "drawdown": equity / peak - 1.0,
                }
            )
    settle(pd.Timestamp.max.tz_localize("UTC"))
    decision_frame = pd.DataFrame(decisions)
    curve_frame = pd.DataFrame(curve).sort_values(["time", "event"], kind="mergesort")
    accepted_count = int(decision_frame["portfolio_accepted"].sum())
    return decision_frame, curve_frame, {
        "candidate_events": int(len(trades)),
        "accepted_events": accepted_count,
        "capacity_rejections": int(len(trades) - accepted_count),
        "total_return": float(equity - 1.0),
        "closed_equity_max_drawdown": float(max_drawdown),
        "maximum_concurrent_positions": int(max_concurrent),
        "maximum_open_initial_risk_fraction": float(max_open_risk),
    }


def _params_key(params: Mapping[str, Any]) -> str:
    payload = json.dumps(dict(params), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def evaluate_params(
    universe: Mapping[str, pd.DataFrame],
    config: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    phase: str,
    profile_cache: dict[tuple[str, str], pd.DataFrame] | None = None,
) -> dict[str, Any]:
    cache = profile_cache if profile_cache is not None else {}
    phase_spec = config["splits"][phase]
    start = utc(phase_spec["start_inclusive"])
    end = utc(phase_spec["end_exclusive"])
    all_attempts: list[pd.DataFrame] = []
    all_pairs: list[pd.DataFrame] = []
    all_setups: list[pd.DataFrame] = []
    all_trades: list[pd.DataFrame] = []
    all_rejections: list[pd.DataFrame] = []
    selected_frames: dict[str, pd.DataFrame] = {}
    for symbol, daily in sorted(universe.items()):
        key = (symbol, str(params["ma_profile"]))
        if key not in cache:
            cache[key] = build_profile(daily, config, str(params["ma_profile"]))
        frame = cache[key]
        selected_frames[symbol] = frame
        attempts, pairs = build_episode_signals(frame, symbol, config, params)
        setups = _setup_rows(pairs, frame, start, end, config)
        accepted, trades, rejected = _lock_and_resolve(
            setups, frame, config, params, phase_end=end
        )
        if len(attempts):
            all_attempts.append(attempts)
        if len(pairs):
            all_pairs.append(pairs)
        if len(accepted):
            all_setups.append(accepted)
        if len(trades):
            all_trades.append(trades)
        if len(rejected):
            all_rejections.append(rejected)
    attempts = pd.concat(all_attempts, ignore_index=True) if all_attempts else pd.DataFrame()
    pairs = pd.concat(all_pairs, ignore_index=True) if all_pairs else pd.DataFrame()
    setups = pd.concat(all_setups, ignore_index=True) if all_setups else pd.DataFrame()
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    rejections = pd.concat(all_rejections, ignore_index=True) if all_rejections else pd.DataFrame()
    folds = _fold_table(
        trades,
        list(phase_spec["folds"]),
        p_seed=int(config["matched_control"]["p_seed"]),
    )
    summary = _summary(trades, list(phase_spec["folds"]), config, phase)
    portfolio_trades, portfolio_curve, portfolio_summary = _portfolio(trades, config)
    return {
        "frames": selected_frames,
        "attempts": attempts,
        "pairs": pairs,
        "setups": setups,
        "trades": trades,
        "rejections": rejections,
        "folds": folds,
        "summary": summary,
        "portfolio_trades": portfolio_trades,
        "portfolio_curve": portfolio_curve,
        "portfolio_summary": portfolio_summary,
    }


def _rank_candidate(
    summary: Mapping[str, Any], incumbent: Mapping[str, Any], config: Mapping[str, Any]
) -> tuple[int, float, float, float]:
    incumbent_p95 = float(incumbent["p95_net_bp"])
    candidate_p95 = float(summary["p95_net_bp"])
    tail_ok = bool(
        not np.isfinite(incumbent_p95)
        or incumbent_p95 <= 0
        or candidate_p95 >= float(config["selection"]["p95_retention_min"]) * incumbent_p95
    )
    eligible = bool(summary["eligible"] and tail_ok)
    return (
        1 if eligible else 0,
        float(summary["robust_score_bp"]) if eligible else -np.inf,
        float(summary["mean_net_bp"]) if eligible else -np.inf,
        candidate_p95 if eligible else -np.inf,
    )


def _causality_probe(
    universe: Mapping[str, pd.DataFrame],
    config: Mapping[str, Any],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    cutoff = utc("2023-07-01T00:00:00Z")
    symbols = [symbol for symbol in ("AAVE", "ADA", "SOL", "XRP", "LINK") if symbol in universe]
    compared = 0
    for symbol in symbols:
        original_daily = universe[symbol]
        mutated_daily = original_daily.copy()
        future = mutated_daily["open_time"].ge(cutoff)
        for column in ("open", "high", "low", "close"):
            mutated_daily.loc[future, column] = mutated_daily.loc[future, column].astype(float) * 1.37
        mutated_daily.loc[future, "volume"] = mutated_daily.loc[future, "volume"].astype(float) * 9.0
        original = build_profile(original_daily, config, str(params["ma_profile"]))
        changed = build_profile(mutated_daily, config, str(params["ma_profile"]))
        _, original_pairs = build_episode_signals(original, symbol, config, params)
        _, changed_pairs = build_episode_signals(changed, symbol, config, params)
        columns = ["setup_id", "direction", "k1_time", "signal_time", "transition_votes", "signal_score"]
        left = original_pairs[original_pairs["signal_time"].map(utc).lt(cutoff)][columns].reset_index(drop=True) if len(original_pairs) else pd.DataFrame(columns=columns)
        right = changed_pairs[changed_pairs["signal_time"].map(utc).lt(cutoff)][columns].reset_index(drop=True) if len(changed_pairs) else pd.DataFrame(columns=columns)
        if not left.equals(right):
            raise AssertionError(f"future mutation changed pre-cutoff signals for {symbol}")
        compared += len(left)
    return {
        "symbols": symbols,
        "cutoff": cutoff,
        "compared_pre_cutoff_pairs": int(compared),
        "different_rows": 0,
        "passed": True,
    }


def _eligible_control_indices(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    config: Mapping[str, Any],
) -> np.ndarray:
    eligible = np.zeros(len(frame), dtype=bool)
    minimum = int(config["execution"]["minimum_phase_remaining_bars"])
    for signal_i in range(len(frame) - 1):
        entry_i = signal_i + 1
        entry_time = utc(frame.loc[entry_i, "open_time"])
        if not (start <= entry_time < end):
            continue
        if entry_time - utc(frame.loc[signal_i, "open_time"]) != DAY:
            continue
        same_segment = frame.index[frame["segment_id"].eq(int(frame.loc[entry_i, "segment_id"]))]
        phase_rows = frame.index[frame["open_time"].lt(end)]
        available = min(int(same_segment.max()), int(phase_rows.max())) - entry_i + 1
        values = [frame.loc[signal_i, "atr"], frame.loc[signal_i, "fast_ma"], frame.loc[signal_i, "slow_ma"]]
        eligible[signal_i] = bool(
            available >= minimum and np.isfinite(np.asarray(values, dtype=float)).all()
        )
    return eligible


def _atr_quintiles(frame: pd.DataFrame, eligible: np.ndarray) -> np.ndarray:
    buckets = np.full(len(frame), -1, dtype=int)
    indices = np.flatnonzero(eligible)
    if not len(indices):
        return buckets
    relative = frame.loc[indices, "atr"].astype(float) / frame.loc[indices, "close"].astype(float)
    labels = pd.qcut(relative.rank(method="first"), q=min(5, len(relative)), labels=False)
    buckets[indices] = labels.to_numpy(dtype=int)
    return buckets


def _halfyear(stamp: pd.Timestamp) -> str:
    value = utc(stamp)
    return f"{value.year}H{1 if value.month <= 6 else 2}"


def matched_random(
    trades: pd.DataFrame,
    frames: Mapping[str, pd.DataFrame],
    config: Mapping[str, Any],
    params: Mapping[str, Any],
    *,
    phase: str,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Match controls by symbol, half-year, direction and relative-ATR quintile."""

    empty_summary = {
        "matched_events": 0,
        "candidate_mean_net_bp": np.nan,
        "control_mean_net_bp": np.nan,
        "excess_bp": np.nan,
        "week_clusters": 0,
        "week_cluster_signflip_p": np.nan,
        "control_reuse_count": 0,
    }
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame(), empty_summary
    spec = config["splits"][phase]
    start = utc(spec["start_inclusive"])
    end = utc(spec["end_exclusive"])
    required = int(config["matched_control"]["controls_per_event"])
    radius = int(config["matched_control"]["exclude_radius_bars"])
    seed = str(config["matched_control"]["seed"])
    p_seed = int(config["matched_control"]["p_seed"])
    pools: dict[str, dict[tuple[str, int], list[int]]] = {}
    buckets_by_symbol: dict[str, np.ndarray] = {}
    signal_indices: dict[str, list[int]] = {
        symbol: group["signal_i"].astype(int).tolist()
        for symbol, group in trades.groupby("symbol", sort=True)
    }
    for symbol, frame in frames.items():
        eligible = _eligible_control_indices(frame, start, end, config)
        buckets = _atr_quintiles(frame, eligible)
        buckets_by_symbol[symbol] = buckets
        pool: dict[tuple[str, int], list[int]] = {}
        protected = signal_indices.get(symbol, [])
        for index in np.flatnonzero(eligible & (buckets >= 0)):
            if any(abs(int(index) - signal_i) <= radius for signal_i in protected):
                continue
            key = (_halfyear(frame.loc[index, "open_time"]), int(buckets[index]))
            pool.setdefault(key, []).append(int(index))
        pools[symbol] = pool

    used: set[tuple[str, int]] = set()
    control_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []
    for event in trades.sort_values(["entry_time", "setup_id"], kind="mergesort").to_dict("records"):
        symbol = str(event["symbol"])
        frame = frames[symbol]
        signal_i = int(event["signal_i"])
        bucket = int(buckets_by_symbol[symbol][signal_i])
        key = (_halfyear(utc(event["signal_time"])), bucket)
        choices = sorted(
            [index for index in pools[symbol].get(key, []) if (symbol, index) not in used],
            key=lambda index: hashlib.sha256(
                f"{seed}|{event['setup_id']}|{symbol}|{index}".encode()
            ).hexdigest(),
        )
        if len(choices) < required:
            pair_rows.append(
                {
                    "setup_id": event["setup_id"],
                    "symbol": symbol,
                    "entry_time": event["entry_time"],
                    "match_status": "unmatched",
                    "available_controls": len(choices),
                }
            )
            continue
        results: list[dict[str, Any]] = []
        for assignment, signal_index in enumerate(choices[:required]):
            used.add((symbol, signal_index))
            entry_i = signal_index + 1
            control_event = {
                "setup_id": f"control-{symbol}-{signal_index}-{int(event['direction'])}",
                "symbol": symbol,
                "signal_i": signal_index,
                "signal_time": frame.loc[signal_index, "open_time"],
                "entry_i": entry_i,
                "entry_time": frame.loc[entry_i, "open_time"],
                "entry_price": float(frame.loc[entry_i, "open"]),
                "direction": int(event["direction"]),
                "signal_atr": float(frame.loc[signal_index, "atr"]),
                "risk_atr_override": float(event["risk_atr"]),
                "transition_votes": np.nan,
                "signal_score": np.nan,
                "ma_profile": params["ma_profile"],
            }
            result = resolve_trade(frame, control_event, config, params, phase_end=end)
            if not result.get("resolved"):
                continue
            results.append(result)
            control_rows.append(
                {
                    "candidate_setup_id": event["setup_id"],
                    "assignment": assignment,
                    "symbol": symbol,
                    "control_signal_i": signal_index,
                    "control_entry_time": result["entry_time"],
                    "direction": int(event["direction"]),
                    "calendar_halfyear": key[0],
                    "atr_quintile": key[1],
                    "copied_risk_atr": float(event["risk_atr"]),
                    "control_net_return": float(result["net_return"]),
                }
            )
        if len(results) != required:
            pair_rows.append(
                {
                    "setup_id": event["setup_id"],
                    "symbol": symbol,
                    "entry_time": event["entry_time"],
                    "match_status": "resolution_failed",
                    "available_controls": len(results),
                }
            )
            continue
        control_mean = float(np.mean([row["net_return"] for row in results]))
        pair_rows.append(
            {
                "setup_id": event["setup_id"],
                "symbol": symbol,
                "entry_time": event["entry_time"],
                "match_status": "matched_exact",
                "matched_control_count": required,
                "candidate_net_return": float(event["net_return"]),
                "control_mean_net_return": control_mean,
                "paired_excess_return": float(event["net_return"]) - control_mean,
            }
        )
    controls = pd.DataFrame(control_rows)
    pairs = pd.DataFrame(pair_rows)
    matched = pairs[pairs.get("match_status", pd.Series(dtype=str)).eq("matched_exact")].copy()
    if not len(matched):
        return controls, pairs, empty_summary
    weeks, p = _week_signflip(matched, "paired_excess_return", p_seed)
    return controls, pairs, {
        "matched_events": int(len(matched)),
        "candidate_mean_net_bp": float(matched["candidate_net_return"].mean() * 1e4),
        "control_mean_net_bp": float(matched["control_mean_net_return"].mean() * 1e4),
        "excess_bp": float(matched["paired_excess_return"].mean() * 1e4),
        "week_clusters": weeks,
        "week_cluster_signflip_p": p,
        "control_reuse_count": 0,
    }


def failure_diagnostics(trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if trades.empty:
        return pd.DataFrame(), pd.DataFrame()
    detailed = trades.copy()

    def classify(row: pd.Series) -> str:
        if row["net_return"] > 0 and row["gave_back_r"] >= 3.0:
            return "winner_large_giveback"
        if row["bank_hits"] > 0 and row["net_return"] <= 0:
            return "banked_then_net_loss"
        if row["runner_armed"] and row["net_return"] <= 0:
            return "runner_armed_then_net_loss"
        if row["outcome"] == "structural_stop" and row["bank_hits"] == 0:
            return "false_launch_structural_stop"
        if row["net_return"] <= 0 and row["horizon_mfe_r"] >= 2.0:
            return "loss_despite_two_r_opportunity"
        if row["net_return"] <= 0:
            return "no_followthrough_or_timeout_loss"
        if row["net_return_r"] >= 5.0:
            return "large_trend_winner"
        return "ordinary_winner"

    detailed["failure_mode"] = detailed.apply(classify, axis=1)
    grouped = (
        detailed.groupby("failure_mode", as_index=False)
        .agg(
            events=("setup_id", "size"),
            symbols=("symbol", "nunique"),
            mean_net_bp=("net_return", lambda values: float(values.mean() * 1e4)),
            total_net_bp=("net_return", lambda values: float(values.sum() * 1e4)),
            mean_mfe_r=("horizon_mfe_r", "mean"),
            mean_giveback_r=("gave_back_r", "mean"),
            mean_k1_range_release=("k1_range_release", "mean"),
            mean_k1_volume_release=("k1_volume_release", "mean"),
            mean_gap=("k1_k2_gap", "mean"),
        )
        .sort_values("total_net_bp", kind="mergesort")
    )
    return detailed, grouped


def selection_phase(config_path: Path, config: dict[str, Any]) -> dict[str, Any]:
    prereg = config_path.with_name("preregistration.json")
    for path in (config_path, prereg, SCRIPT_PATH):
        _assert_head_frozen(path)
    end = utc(config["splits"]["selection"]["end_exclusive"])
    universe, source_quality, source_summary = load_universe(config, end_exclusive=end)
    params = deepcopy(config["selection"]["initial"])
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    grid_rows: list[dict[str, Any]] = []
    minimum_gain = float(config["selection"]["minimum_robust_gain_bp"])
    for stage, factor in enumerate(config["selection"]["ordered_factors"], start=1):
        incumbent = evaluate_params(
            universe, config, params, phase="selection", profile_cache=cache
        )
        candidates: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
        for value in config["selection"]["candidates"][factor]:
            trial = deepcopy(params)
            trial[str(factor)] = value
            evaluated = evaluate_params(
                universe, config, trial, phase="selection", profile_cache=cache
            )
            candidates.append((value, trial, evaluated))
            grid_rows.append(
                {
                    "stage": stage,
                    "factor": factor,
                    "value": value,
                    "params_key": _params_key(trial),
                    **trial,
                    **evaluated["summary"],
                    **{f"portfolio_{key}": val for key, val in evaluated["portfolio_summary"].items()},
                }
            )
        best_value, best_params, best = max(
            candidates,
            key=lambda item: _rank_candidate(item[2]["summary"], incumbent["summary"], config),
        )
        incumbent_rank = _rank_candidate(incumbent["summary"], incumbent["summary"], config)
        best_rank = _rank_candidate(best["summary"], incumbent["summary"], config)
        improvement = best_rank[1] - incumbent_rank[1]
        selected_value = params[factor]
        if best_rank[0] == 1 and (
            incumbent_rank[0] == 0
            or best_value == params[factor]
            or improvement >= minimum_gain
        ):
            params = best_params
            selected_value = best_value
        for row in grid_rows:
            if row["stage"] == stage:
                row["stage_selected"] = bool(row["value"] == selected_value)

    final = evaluate_params(universe, config, params, phase="selection", profile_cache=cache)
    baseline_params = deepcopy(config["selection"]["initial"])
    baseline = evaluate_params(
        universe, config, baseline_params, phase="selection", profile_cache=cache
    )
    single_params = deepcopy(baseline_params)
    single_params["transition_min_votes"] = 0
    single_params["required_vote"] = "vote_k1_range_release"
    single = evaluate_params(
        universe, config, single_params, phase="selection", profile_cache=cache
    )
    causality = _causality_probe(universe, config, params)
    experiment = ROOT / "experiments" / "active" / str(config["experiment_id"])
    results = experiment / "results"
    results.mkdir(parents=True, exist_ok=True)
    write_csv(source_quality, results / "selection_source_quality.csv")
    write_csv(pd.DataFrame(grid_rows), results / "selection_coordinate_grid.csv")
    for key in ("attempts", "pairs", "setups", "trades", "rejections"):
        write_csv(final[key], results / f"selection_final_{key}.csv.gz")
    write_csv(final["folds"], results / "selection_final_folds.csv")
    write_csv(final["portfolio_trades"], results / "selection_portfolio_trades.csv.gz")
    write_csv(final["portfolio_curve"], results / "selection_portfolio_equity.csv")
    write_csv(baseline["trades"], results / "selection_baseline_trades.csv.gz")
    write_csv(single["trades"], results / "selection_single_feature_trades.csv.gz")
    receipt = {
        "experiment_id": config["experiment_id"],
        "phase": "selection",
        "frozen": True,
        "status": "frozen_for_audit" if final["summary"]["eligible"] else "research_only_sample_gate_failed",
        "selected_params": params,
        "source": source_summary,
        "baseline_params": baseline_params,
        "baseline": baseline["summary"],
        "single_feature_params": single_params,
        "single_feature_baseline": single["summary"],
        "candidate": final["summary"],
        "portfolio": final["portfolio_summary"],
        "causality_mutation": causality,
        "audit_rows_read": 0,
        "confirmation_rows_read": 0,
        "repository_holdout_rows_read": int(source_summary["repository_holdout_rows_read"]),
        "hashes": {
            "config_sha256": sha256_file(config_path),
            "preregistration_sha256": sha256_file(prereg),
            "script_sha256": sha256_file(SCRIPT_PATH),
            "grid_sha256": sha256_file(results / "selection_coordinate_grid.csv"),
            "source_quality_sha256": sha256_file(results / "selection_source_quality.csv"),
        },
    }
    write_json(results / "selection_receipt.json", receipt)
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))
    return receipt


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
    end = utc(config["splits"][phase]["end_exclusive"])
    universe, source_quality, source_summary = load_universe(config, end_exclusive=end)
    cache: dict[tuple[str, str], pd.DataFrame] = {}
    params = dict(selection["selected_params"])
    candidate = evaluate_params(universe, config, params, phase=phase, profile_cache=cache)
    baseline_params = dict(selection["baseline_params"])
    baseline = evaluate_params(
        universe, config, baseline_params, phase=phase, profile_cache=cache
    )
    single_params = dict(selection["single_feature_params"])
    single = evaluate_params(universe, config, single_params, phase=phase, profile_cache=cache)
    controls, matched_pairs, matched = matched_random(
        candidate["trades"], candidate["frames"], config, params, phase=phase
    )
    failure_detail, failure_summary = failure_diagnostics(candidate["trades"])
    symbol_table = pd.DataFrame(
        [
            {"symbol": symbol, **trade_metrics(group, p_seed=int(config["matched_control"]["p_seed"]))}
            for symbol, group in candidate["trades"].groupby("symbol", sort=True)
        ]
    )
    prefix = phase
    write_csv(source_quality, results / f"{prefix}_source_quality.csv")
    for key in ("attempts", "pairs", "setups", "trades", "rejections"):
        write_csv(candidate[key], results / f"{prefix}_candidate_{key}.csv.gz")
    write_csv(candidate["folds"], results / f"{prefix}_candidate_folds.csv")
    write_csv(candidate["portfolio_trades"], results / f"{prefix}_portfolio_trades.csv.gz")
    write_csv(candidate["portfolio_curve"], results / f"{prefix}_portfolio_equity.csv")
    write_csv(baseline["trades"], results / f"{prefix}_baseline_trades.csv.gz")
    write_csv(single["trades"], results / f"{prefix}_single_feature_trades.csv.gz")
    write_csv(controls, results / f"{prefix}_matched_controls.csv.gz")
    write_csv(matched_pairs, results / f"{prefix}_matched_pairs.csv")
    write_csv(failure_detail, results / f"{prefix}_failure_detail.csv.gz")
    write_csv(failure_summary, results / f"{prefix}_failure_modes.csv")
    write_csv(symbol_table, results / f"{prefix}_symbol_metrics.csv")

    summary = candidate["summary"]
    gates = config["acceptance_gates"]
    required_positive_folds = int(
        np.ceil(float(gates["positive_fold_share_min"]) * int(summary["total_folds"]))
    )
    gate_checks = {
        "sample_eligible": bool(summary["eligible"]),
        "mean_net_positive": bool(float(summary["mean_net_bp"]) > 0),
        "profit_factor_above_one": bool(float(summary["profit_factor"]) > 1),
        "positive_fold_share": bool(int(summary["positive_folds"]) >= required_positive_folds),
        "positive_symbol_share": bool(
            float(summary["positive_symbol_share"]) >= float(gates["positive_symbol_share_min"])
        ),
        "week_cluster_signflip_p": bool(
            float(summary["week_cluster_signflip_p"]) < float(gates["week_cluster_signflip_p_max"])
        ),
        "matched_excess_positive": bool(float(matched["excess_bp"]) > 0),
        "matched_random_p": bool(
            float(matched["week_cluster_signflip_p"]) < float(gates["matched_random_p_max"])
        ),
        "p95_vs_baseline_retained": bool(
            not np.isfinite(float(baseline["summary"]["p95_net_bp"]))
            or float(baseline["summary"]["p95_net_bp"]) <= 0
            or float(summary["p95_net_bp"])
            >= float(gates["p95_vs_baseline_retention_min"])
            * float(baseline["summary"]["p95_net_bp"])
        ),
        "portfolio_total_return_positive": bool(
            float(candidate["portfolio_summary"]["total_return"]) > 0
        ),
        "portfolio_drawdown": bool(
            abs(float(candidate["portfolio_summary"]["closed_equity_max_drawdown"]))
            <= float(gates["portfolio_closed_equity_max_drawdown_max"])
        ),
    }
    all_gates = bool(all(gate_checks.values()))
    receipt = {
        "experiment_id": config["experiment_id"],
        "phase": phase,
        "frozen": True,
        "status": "passed_preholdout_research_gates" if all_gates else "research_only_failed_gates",
        "selected_params": params,
        "source": source_summary,
        "baseline": baseline["summary"],
        "single_feature_baseline": single["summary"],
        "candidate": summary,
        "portfolio": candidate["portfolio_summary"],
        "matched_random": matched,
        "gate_checks": gate_checks,
        "all_registered_gates_pass": all_gates,
        "selection_receipt_sha256": sha256_file(results / "selection_receipt.json"),
        "audit_receipt_sha256": sha256_file(results / "audit_receipt.json")
        if audit_receipt is not None
        else None,
        "repository_holdout_rows_read": int(source_summary["repository_holdout_rows_read"]),
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
