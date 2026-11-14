"""Historical Monday 1.618 same-candle wick-reclaim cohort.

The stored score and first weekly-side 5m touch are frozen inputs. A market
entry is submitted at the touch candle close only when its wick strictly
crossed the unrounded Monday 1.618 level and that same close strictly
reclaimed the level. The signal candle is not part of post-entry excursion.
Each event is an independent NautilusTrader HEDGING position with a static
stop at the touch candle's low/high and a 72-hour maximum holding window.

This module also exports ``evaluate_symbol_events`` for reuse with another
venue's bars and event rows. Frame indexes are UTC candle-close timestamps;
events use the schema documented in that function.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-monday-reclaimed-wick-mfe-20261115-v1"
HIST_EXP = ROOT / "experiments/active/exp-monday-limit-reclaim-20261024-v1"
SCORE_AUDIT = ROOT / "experiments/active/exp-monday-limit-score-leak-audit-20261027-v1/scores_causal.csv.gz"
FROZEN_SCORES = ROOT / "experiments/active/exp-monday-limit-be-scalein-20261026-v1/scores.csv.gz"
OCT_EXP = ROOT / "experiments/active/exp-monday-limit-live-october-20261101-v1"
OCT_SCORES = OCT_EXP / "scored.csv.gz"
OHLC = ["open", "high", "low", "close"]
HORIZONS_H = (1, 6, 24, 72)
THRESHOLDS_PCT = (2, 5, 10)
STEP = pd.Timedelta(minutes=5)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(payload), ensure_ascii=False, indent=2) + "\n")


def file_stamp(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        return {"path": str(path), "readable": False}
    stat = path.stat()
    return {"path": str(path), "readable": True, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _to_utc(value: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _read_stored_scores() -> tuple[pd.DataFrame, dict[str, Any]]:
    audit = pd.read_csv(SCORE_AUDIT, usecols=["event", "stored"])
    original = pd.read_csv(FROZEN_SCORES, usecols=["event", "score"])
    joined = audit.merge(original, on="event", how="inner", validate="one_to_one")
    delta = (joined.stored - joined.score).abs()
    info = {
        "audit_rows": len(audit),
        "original_frozen_rows": len(original),
        "common_rows": len(joined),
        "audit_only_rows": int((~audit.event.isin(original.event)).sum()),
        "original_only_rows": int((~original.event.isin(audit.event)).sum()),
        "common_max_abs_score_delta": float(delta.max()) if len(delta) else None,
        "common_nonzero_delta_rows": int(delta.gt(1e-12).sum()),
        "source": str(SCORE_AUDIT.relative_to(ROOT)),
        "authoritative_column": "stored",
        "parity_source": str(FROZEN_SCORES.relative_to(ROOT)),
    }
    # The audit's `stored` column is the exact touch-bar score used in the
    # frozen ledger. Keep it as authority; never replace it with a refit.
    return audit.rename(columns={"event": "event_id", "stored": "score"}), info


def _historical_events(symbol: str) -> pd.DataFrame:
    path = HIST_EXP / "symbols" / symbol / "trades.csv.gz"
    columns = [
        "event_id", "symbol", "week", "side", "monday_high", "monday_low", "level",
        "touch_i_hint", "touch_interval_open", "population", "reclaim_minutes", "exit_mode",
    ]
    rows = pd.read_csv(path, usecols=columns)
    rows = rows[(rows.population == "signal") & (rows.reclaim_minutes == 5)
                & (rows.exit_mode == "near_monday_edge")].copy()
    rows = rows.drop_duplicates("event_id", keep="first")
    rows["entry_open_time"] = pd.to_datetime(rows.touch_interval_open, utc=True)
    rows["event_source"] = "frozen_historical_ledger"
    return rows.drop(columns=["population", "reclaim_minutes", "exit_mode", "touch_interval_open"])


def _october_events(historical_ids: set[str], old_scores: pd.DataFrame,
                    allowed_symbols: set[str]) -> pd.DataFrame:
    if not OCT_SCORES.exists():
        return pd.DataFrame()
    rows = pd.read_csv(OCT_SCORES)
    required = {"event", "symbol", "week", "side", "level", "t", "close", "score_stored", "tick"}
    missing = required - set(rows.columns)
    if missing:
        raise ValueError(f"October score source lacks {sorted(missing)}")
    rows = rows[rows.symbol.isin(allowed_symbols)].copy()
    rows["entry_open_time"] = pd.to_datetime(rows.t, utc=True)
    # The legacy archive ends on Sep 23. October's output repeats events from
    # July onward, so only add event keys absent from the frozen historical
    # touch ledger. Overlapping October scores are retained for a score-drift
    # audit, but cannot overwrite the old stored score.
    old_score_map = old_scores.set_index("event_id").score
    score_overlap = rows[rows.event.isin(old_score_map.index)].copy()
    if len(score_overlap):
        score_overlap["old_stored_score"] = score_overlap.event.map(old_score_map)
        score_overlap["score_delta_vs_frozen"] = score_overlap.score_stored - score_overlap.old_stored_score
        drift = score_overlap[["event", "score_delta_vs_frozen"]].copy()
    else:
        drift = pd.DataFrame(columns=["event", "score_delta_vs_frozen"])
    rows = rows[~rows.event.isin(historical_ids)].copy()
    rows = rows[rows.entry_open_time >= pd.Timestamp("2026-09-24T00:00:00Z")]
    rows = rows.rename(columns={"event": "event_id", "t": "touch_open_raw",
                                "score_stored": "score", "close": "expected_close"})
    old_score_for_row = rows.event_id.map(old_score_map)
    rows["score_source"] = np.where(old_score_for_row.notna(), "original_frozen_stored", "october_stored_extension")
    rows["score"] = old_score_for_row.where(old_score_for_row.notna(), rows.score)
    rows["monday_high"] = np.nan
    rows["monday_low"] = np.nan
    rows["touch_i_hint"] = np.nan
    rows["event_source"] = "frozen_live_october_score_extension"
    rows = rows[["event_id", "symbol", "week", "side", "monday_high", "monday_low", "level",
                 "touch_i_hint", "entry_open_time", "score", "event_source", "tick", "expected_close"]]
    rows["score_source"] = np.where(rows.event_id.isin(old_score_map.index),
                                    "original_frozen_stored", "october_stored_extension")
    rows.attrs["overlap_drift"] = drift
    return rows


def _load_october_bars(symbol: str) -> pd.DataFrame:
    path = OCT_EXP / "symbols" / symbol / "new_bars.csv.gz"
    if not path.exists():
        return pd.DataFrame(columns=[*OHLC, "volume"], index=pd.DatetimeIndex([], tz="UTC"))
    frame = pd.read_csv(path)
    ts = pd.to_datetime(frame.ts, unit="ms", utc=True)
    frame = frame.drop(columns=["ts"])
    frame.index = pd.DatetimeIndex(ts, name="open_time")
    return frame[[*OHLC, "volume"]].astype(float).sort_index()


def _round_market_frame(frame: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    minimum = float(frame[OHLC].to_numpy().min())
    decimals = max(2, min(9, int(np.ceil(-np.log10(minimum))) + 7))
    tick = 10.0 ** (-decimals)
    rounded = frame.copy()
    rounded[OHLC] = rounded[OHLC].round(decimals)
    return rounded, tick


def _check_bar_frame(frame: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame = frame.sort_index().copy()
    if frame.index.tz is None:
        raise ValueError(f"{symbol}: source bar index is not timezone-aware")
    frame.index = frame.index.tz_convert("UTC").as_unit("ns")
    if frame.index.has_duplicates:
        # Prefer the pre-existing history row at a shared boundary, but only
        # when the overlapping OHLCV prints agree exactly.
        duplicate = frame.index[frame.index.duplicated(keep=False)].unique()
        conflicts = []
        for ts in duplicate:
            group = frame.loc[[ts], OHLC]
            if group.nunique(dropna=False).gt(1).any():
                conflicts.append(str(ts))
        if conflicts:
            raise ValueError(f"{symbol}: conflicting duplicate source bars: {conflicts[:3]}")
        frame = frame[~frame.index.duplicated(keep="first")]
    values = frame[OHLC].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError(f"{symbol}: nonfinite/nonpositive OHLC")
    if ((frame.high < frame[["open", "close", "low"]].max(axis=1))
            | (frame.low > frame[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError(f"{symbol}: invalid OHLC envelope")
    gaps = np.flatnonzero(np.diff(frame.index.as_unit("ns").asi8) != STEP.value) + 1
    return frame, {
        "symbol": symbol, "rows": len(frame), "first_open_time": str(frame.index[0]),
        "last_open_time": str(frame.index[-1]), "gaps": int(len(gaps)),
        "first_gaps": [str(frame.index[i]) for i in gaps[:12]],
    }


def _event_from_bar(row: Any, frame: pd.DataFrame, index: pd.DatetimeIndex,
                    tick: float, source: str) -> dict[str, Any] | None:
    try:
        i = int(index.get_loc(row.entry_open_time))
    except KeyError:
        return None
    bar = frame.iloc[i]
    side = int(row.side)
    level = float(row.level)
    strict_reclaim = (float(bar.low) < level and float(bar.close) > level) if side == 1 else (
        float(bar.high) > level and float(bar.close) < level)
    if not strict_reclaim:
        return None
    entry = float(bar.close)
    stop = float(bar.low if side == 1 else bar.high)
    risk = abs(entry - stop)
    if not risk > tick * 1e-7:
        raise ValueError(f"{row.event_id}: strict wick/reclaim yielded no stop width")
    if source == "frozen_live_october_score_extension":
        expected_close = float(row.expected_close)
        if abs(entry - expected_close) > max(tick * 0.51, 2 * math.ulp(entry)):
            raise ValueError(f"{row.event_id}: frozen saved close does not match new bar")
    event_tick = min(tick, float(getattr(row, "tick", tick)))
    return {
        "event_id": str(row.event_id), "symbol": str(row.symbol), "week": str(row.week),
        "side": side, "score": float(row.score), "event_source": source,
        "entry_open_time": index[i], "entry_time": index[i] + STEP,
        "entry_i_source": i, "entry": entry, "stop": stop, "risk": risk,
        "stop_width_pct": risk / entry * 100.0, "level": level,
        "signal_open": float(bar.open), "signal_high": float(bar.high),
        "signal_low": float(bar.low), "signal_close": float(bar.close),
        "tick": event_tick,
    }


def _plan_window(event: dict[str, Any], frame: pd.DataFrame,
                 index: pd.DatetimeIndex, bar_minutes: int = 5) -> dict[str, Any]:
    entry_i = int(event["entry_i_source"])
    bars_per_hour = 60 // bar_minutes
    target_i = entry_i + 72 * bars_per_hour
    gap_i = None
    if entry_i + 1 < len(index):
        right = min(target_i, len(index) - 1)
        gaps = np.flatnonzero(np.diff(index[entry_i:right + 1].as_unit("ns").asi8)
                              != pd.Timedelta(minutes=bar_minutes).value) + entry_i + 1
        if len(gaps):
            gap_i = int(gaps[0])
    if gap_i is not None:
        end_i = gap_i - 1
        censor = "source_gap"
    elif target_i >= len(index):
        end_i = len(index) - 1
        censor = "source_end"
    else:
        end_i = target_i
        censor = ""
    event["end_i_source"] = end_i
    event["censor_reason"] = censor
    event["window_full_72h"] = not bool(censor)
    if end_i <= entry_i:
        event["native_status"] = "no_post_entry_bar_before_data_gap_or_end"
        return event
    event["native_status"] = "queued"
    return event


def _merge_windows(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    ranges = sorted((int(e["entry_i_source"]), int(e["end_i_source"])) for e in events
                    if e.get("native_status") == "queued")
    merged: list[list[int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def _break_even_price(entry: float, side: int, fee: float, slippage: float) -> float:
    move = (2.0 * fee + 2.0 * slippage) / (1.0 - side * fee)
    return entry * (1.0 + side * move)


def _path_metrics(event: dict[str, Any], frame: pd.DataFrame, index: pd.DatetimeIndex,
                  result: dict[str, Any] | None, fee: float, slippage_bp: float,
                  bar_minutes: int) -> dict[str, Any]:
    side, entry_i = int(event["side"]), int(event["entry_i_source"])
    end_i = int(event["end_i_source"])
    result = result or {}
    stop_hit = int(result.get("reason", -1)) == 1
    stop_i = int(result.get("exit_i_source", end_i)) if stop_hit else None
    exit_i = int(result.get("exit_i_source", end_i))
    gap_stop = bool(stop_hit and result.get("stop_gap", False))
    bars_per_hour = 60 // bar_minutes
    be = _break_even_price(float(event["entry"]), side, fee, slippage_bp / 10_000.0)
    event["cost_break_even_price"] = be

    def favorable_at(i: int) -> float:
        bar = frame.iloc[i]
        high = float(bar.high if side > 0 else bar.low)
        return max(0.0, side * (high - float(event["entry"])) / float(event["entry"]) * 100.0)

    for hours in HORIZONS_H:
        target_i = entry_i + hours * bars_per_hour
        stop_before_h = stop_hit and stop_i is not None and stop_i <= target_i
        complete = stop_before_h or target_i <= end_i
        event[f"observed_{hours}h_complete"] = bool(complete)
        path_end = min(target_i, end_i, (stop_i - 1 if stop_before_h and stop_i is not None else end_i))
        vals = [favorable_at(i) for i in range(entry_i + 1, path_end + 1)]
        event[f"mfe_{hours}h_pct"] = max(vals, default=0.0) if complete else np.nan
        event[f"mfe_{hours}h_bp"] = event[f"mfe_{hours}h_pct"] * 100.0 if complete else np.nan
        if complete:
            if stop_before_h:
                event[f"mark_net_{hours}h_bp"] = float(result.get("net_notional_bp", np.nan)) - 2 * slippage_bp
                event[f"mark_source_{hours}h"] = "native_initial_stop"
            else:
                mark_i = target_i
                close = float(frame.close.iloc[mark_i])
                gross_bp = side * (close - float(event["entry"])) / float(event["entry"]) * 10_000.0
                fee_bp = fee * 10_000.0 * (1.0 + close / float(event["entry"]))
                event[f"mark_net_{hours}h_bp"] = gross_bp - fee_bp - 2 * slippage_bp
                event[f"mark_source_{hours}h"] = "market_close_mark"
        else:
            event[f"mark_net_{hours}h_bp"] = np.nan
            event[f"mark_source_{hours}h"] = "incomplete_data_window"

    # Explicit same-stop-bar OHLC sensitivity. Static-stop terminal fills are
    # path invariant (one barrier only), but favorable excursion on the stop
    # candle depends on whether its favorable extreme precedes the stop.
    direct_end = (stop_i - 1) if stop_hit and stop_i is not None else exit_i
    direct_end = max(entry_i, min(direct_end, len(index) - 1))
    direct_values = [favorable_at(i) for i in range(entry_i + 1, direct_end + 1)]
    direct_mfe = max(direct_values, default=0.0)
    native_mfe = float(result.get("peak_r", 0.0)) * float(event["risk"]) / float(event["entry"]) * 100.0
    event["mfe_pre_stop_pct"] = direct_mfe
    event["mfe_pre_stop_native_pct"] = native_mfe
    event["mfe_pre_stop_matches_native"] = abs(direct_mfe - native_mfe) <= 1e-6
    if not event["mfe_pre_stop_matches_native"]:
        raise RuntimeError(f"{event['event_id']}: direct pre-stop MFE differs from native peak")
    event["mfe_ohlc_favorable_first_pct"] = event["mfe_pre_stop_pct"]
    event["mfe_olhc_favorable_first_pct"] = event["mfe_pre_stop_pct"]
    if stop_hit and stop_i is not None and not gap_stop:
        stopbar_mfe = favorable_at(stop_i)
        # In an OHLC path the high comes before the low: favorable first for a
        # long stop, adverse first for a short. OLHC reverses those cases.
        event["mfe_ohlc_favorable_first_pct"] = max(event["mfe_pre_stop_pct"], stopbar_mfe) if side > 0 else event["mfe_pre_stop_pct"]
        event["mfe_olhc_favorable_first_pct"] = max(event["mfe_pre_stop_pct"], stopbar_mfe) if side < 0 else event["mfe_pre_stop_pct"]
    event["stopbar_mfe_delta_ohlc_pct"] = event["mfe_ohlc_favorable_first_pct"] - event["mfe_pre_stop_pct"]
    event["stopbar_mfe_delta_olhc_pct"] = event["mfe_olhc_favorable_first_pct"] - event["mfe_pre_stop_pct"]

    # Reusable raw thresholds for the parent summary. The conservative hit
    # requires the favorable threshold before the stop bar; separate OHLC and
    # OLHC flags expose a same-bar trigger/retrace ordering ambiguity.
    trigger_observed_end = stop_i - 1 if stop_hit and stop_i is not None else end_i
    trigger_observed_end = max(entry_i, trigger_observed_end)
    retrace_observed_end = stop_i if stop_hit and stop_i is not None else end_i
    retrace_observed_end = max(entry_i, retrace_observed_end)
    price_field = "high" if side > 0 else "low"
    adverse_field = "low" if side > 0 else "high"
    be_cross = lambda i: (float(frame[adverse_field].iloc[i]) <= be if side > 0
                          else float(frame[adverse_field].iloc[i]) >= be)
    for threshold in THRESHOLDS_PCT:
        trigger = float(event["entry"]) * (1.0 + side * threshold / 100.0)
        hits = []
        for i in range(entry_i + 1, trigger_observed_end + 1):
            price = float(frame[price_field].iloc[i])
            if side * (price - trigger) >= 0:
                hits.append(i)
        trigger_i = hits[0] if hits else None
        event[f"hit_{threshold}pct_before_stop"] = trigger_i is not None
        event[f"hit_{threshold}pct_in_stopbar"] = False
        if stop_hit and stop_i is not None and not gap_stop:
            stop_price = float(frame[price_field].iloc[stop_i])
            event[f"hit_{threshold}pct_in_stopbar"] = side * (stop_price - trigger) >= 0
        event[f"threshold_{threshold}pct_first_bar"] = int(trigger_i) if trigger_i is not None else None
        strictly_later_retrace = (any(be_cross(i) for i in range(trigger_i + 1, retrace_observed_end + 1))
                                  if trigger_i is not None else False)
        trigger_close_retrace = bool(
            trigger_i is not None
            and side * (float(frame.close.iloc[trigger_i]) - be) <= 0
        )
        event[f"back_to_cost_be_{threshold}pct_strict_later_bar"] = bool(strictly_later_retrace)
        event[f"back_to_cost_be_{threshold}pct_trigger_close"] = trigger_close_retrace
        event[f"back_to_cost_be_{threshold}pct_later_bar"] = bool(
            strictly_later_retrace or trigger_close_retrace)
        # Same-bar trigger and cost-BE crossing: OHLC favors long, OLHC favors
        # short. The opposite ordering treats the adverse point as first.
        trigger_and_be = bool(trigger_i is not None and be_cross(trigger_i))
        event[f"back_to_cost_be_{threshold}pct_samebar_ohlc"] = bool(trigger_and_be and side > 0)
        event[f"back_to_cost_be_{threshold}pct_samebar_olhc"] = bool(trigger_and_be and side < 0)
        event[f"original_stop_after_{threshold}pct"] = bool(
            stop_hit and stop_i is not None and trigger_i is not None and stop_i > trigger_i)
        event[f"samebar_trigger_stop_ohlc_{threshold}pct"] = bool(
            stop_hit and stop_i is not None and trigger_i is None and event[f"hit_{threshold}pct_in_stopbar"] and side > 0)
        event[f"samebar_trigger_stop_olhc_{threshold}pct"] = bool(
            stop_hit and stop_i is not None and trigger_i is None and event[f"hit_{threshold}pct_in_stopbar"] and side < 0)
        event[f"gap_stop_after_{threshold}pct"] = bool(
            stop_hit and gap_stop and trigger_i is not None and stop_i > trigger_i)
    return event


def evaluate_symbol_events(bars_1m_or_5m: pd.DataFrame, events: pd.DataFrame, *,
                           bar_minutes: int = 5, fee_rate: float = 0.0005,
                           slippage_bp_per_fill: float = 10.0, symbol: str = "SYMBOL") -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Run independent close-market events in NautilusTrader.

    ``bars_1m_or_5m`` must be OHLCV sorted by UTC **bar-close time**. ``events``
    requires ``trade_id``, ``entry_time``, ``side``, ``entry``, and ``stop``;
    entry_time names the completed signal-bar close. The matching bar is kept
    in the frame but excluded from MFE/stop execution; its order fills at close
    and protection can first execute on the next bar. Optional event columns
    ``score``, ``event_id``, and ``week`` are passed through. Returns native
    trade rows and fill rows with observed MFE/mark/threshold fields. This is
    the adapter used for the Binance cohort and can receive the parent's OKX
    event table.
    """
    if bar_minutes not in (1, 5):
        raise ValueError("bar_minutes must be 1 or 5")
    if events.empty:
        return pd.DataFrame(), pd.DataFrame(), {"positions": 0, "fills": 0}
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native

    step = pd.Timedelta(minutes=bar_minutes)
    frame = bars_1m_or_5m.copy().sort_index()
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("bars index must be a timezone-aware UTC DatetimeIndex of close times")
    frame.index = frame.index.tz_convert("UTC").as_unit("ns")
    frame = frame[[*OHLC, "volume"]]
    if frame.index.has_duplicates or not np.isfinite(frame[OHLC].to_numpy(float)).all():
        raise ValueError("bars contain duplicate close times or nonfinite OHLC")
    close_lookup = pd.Series(np.arange(len(frame), dtype=int), index=frame.index)
    prepared: list[dict[str, Any]] = []
    for row in events.to_dict("records"):
        event = dict(row)
        event["entry_time"] = _to_utc(event["entry_time"])
        if event["entry_time"] not in close_lookup.index:
            event["native_status"] = "entry_bar_missing"
            prepared.append(event)
            continue
        i = int(close_lookup.loc[event["entry_time"]])
        event["entry_i_source"] = i
        event["entry"] = float(event["entry"])
        event["stop"] = float(event["stop"])
        event["side"] = int(event["side"])
        event["risk"] = abs(event["entry"] - event["stop"])
        event["stop_width_pct"] = event["risk"] / event["entry"] * 100.0
        event["censor_reason"] = ""
        event["native_status"] = "queued"
        _plan_window(event, frame, frame.index, bar_minutes)
        prepared.append(event)
    return _execute_events(frame, prepared, symbol=symbol, bar_minutes=bar_minutes,
                           fee_rate=fee_rate, slippage_bp_per_fill=slippage_bp_per_fill)


def _execute_events(frame: pd.DataFrame, events: list[dict[str, Any]], *, symbol: str,
                    bar_minutes: int, fee_rate: float, slippage_bp_per_fill: float) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native

    index = frame.index
    step = pd.Timedelta(minutes=bar_minutes)
    windows = _merge_windows(events)
    pieces = []
    source_to_engine: dict[int, int] = {}
    cursor = 0
    for start, end in windows:
        part = frame.iloc[start:end + 1].copy()
        for source_i in range(start, end + 1):
            source_to_engine[source_i] = cursor + source_i - start
        pieces.append(part)
        cursor += len(part)
    if not pieces:
        skipped = pd.DataFrame(events)
        return skipped, pd.DataFrame(), {"positions": 0, "fills": 0, "engine": "NautilusTrader 1.231.0"}
    engine_frame = pd.concat(pieces).sort_index()
    if engine_frame.index.has_duplicates:
        raise ValueError(f"{symbol}: merged event windows have duplicate timestamps")

    rows = []
    metadata = {}
    for event in events:
        if event.get("native_status") != "queued":
            continue
        a, b = int(event["entry_i_source"]), int(event["end_i_source"])
        if a not in source_to_engine or b not in source_to_engine:
            raise RuntimeError(f"{event['event_id']}: merged window lost event bounds")
        trade_id = str(event.get("trade_id", event["event_id"]))
        event["trade_id"] = trade_id
        event["engine_entry_i"] = source_to_engine[a]
        event["engine_end_i"] = source_to_engine[b]
        rows.append({"trade_id": trade_id, "entry_i": event["engine_entry_i"],
                     "side": int(event["side"]), "entry": float(event["entry"]),
                     "stop": float(event["stop"]), "risk": float(event["risk"]),
                     "end_i": event["engine_end_i"]})
        metadata[trade_id] = event
    if not rows:
        return pd.DataFrame(events), pd.DataFrame(), {"positions": 0, "fills": 0, "engine": "NautilusTrader 1.231.0"}
    entries = pd.DataFrame(rows)
    if entries.trade_id.duplicated().any():
        raise ValueError(f"{symbol}: duplicate native trade IDs")
    tick = float(min(float(e.get("tick", 1e-9)) for e in events if e.get("native_status") == "queued"))
    policy = ExitPolicy(
        trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0.0,
        fill_maker_fee=fee_rate, fill_taker_fee=fee_rate,
    )
    native_rows, fills, audit = run_native(
        engine_frame, np.full(len(engine_frame), np.nan), np.zeros(len(engine_frame)),
        entries, policy, tick=tick, market_tick=tick, symbol=symbol.removesuffix("USDT"),
        execution_minutes=bar_minutes, entry_order_mode="market",
    )
    native_rows["exit_i_engine"] = native_rows.exit_i.astype(int)
    native_rows["entry_i_engine"] = native_rows.entry_i.astype(int)
    native_rows["exit_time"] = pd.to_datetime(native_rows.exit_time, utc=True)
    native_rows["framework_exit_time"] = pd.to_datetime(native_rows.framework_exit_time, utc=True)
    native_rows["stop_gap"] = False
    combined = []
    for native in native_rows.to_dict("records"):
        event = metadata[str(native["trade_id"])].copy()
        exit_engine_i = int(native["exit_i_engine"])
        # The event's component is the unique source range containing this
        # exit. The engine timestamp itself remains the reliable audit key.
        exit_close = pd.Timestamp(native["framework_exit_time"])
        try:
            exit_source_i = int(index.get_loc(exit_close))
        except KeyError:
            exit_source_i = int(event["end_i_source"])
        native["exit_i_source"] = exit_source_i
        is_stop = int(native["reason"]) == 1
        if is_stop:
            stop_bar_open = float(frame.open.iloc[exit_source_i])
            stop_gap = int(native["side"]) * (stop_bar_open - float(native["stop"])) < -tick * 1e-7
            native["stop_gap"] = bool(stop_gap)
        native["exit_i_source"] = exit_source_i
        event["exit_time"] = native["exit_time"]
        event["framework_exit_time"] = native["framework_exit_time"]
        event["exit_price"] = float(native["exit_price"])
        event["exit_reason"] = "initial_wick_stop" if is_stop else "72h_close_or_data_censor"
        event["native_reason"] = int(native["reason"])
        event["stop_gap"] = bool(native["stop_gap"])
        event["exit_i_source"] = exit_source_i
        event["native_net_before_slippage_bp"] = float(native["net_notional_bp"])
        event["native_net_after_slippage_bp"] = float(native["net_notional_bp"]) - 2 * slippage_bp_per_fill
        event["native_fee_bp"] = float(native["fee_r"]) * float(native["risk"]) / float(native["entry"]) * 10_000.0
        event["actual_outcome_complete"] = bool(is_stop or not event["censor_reason"])
        event["censored"] = bool(event["censor_reason"] and not is_stop)
        event["native_peak_r"] = float(native["peak_r"])
        event["native_final_stop"] = float(native["protection"])
        event["native_trade_id"] = str(native["trade_id"])
        event["entry_i_engine"] = int(native["entry_i_engine"])
        event["exit_i_engine"] = exit_engine_i
        enriched = _path_metrics(event, frame, index, native, fee_rate, slippage_bp_per_fill, bar_minutes)
        # Prices in this study are historical Binance candles; fees are the
        # owner-read OKX Lv1 taker rate. Slippage is a separate 10bp/fill debit.
        combined.append(enriched)
    result = pd.DataFrame(combined)
    fills = fills.copy()
    if len(fills):
        trade_symbols = {k: v["symbol"] for k, v in metadata.items()}
        fills["symbol"] = fills.trade_id.map(trade_symbols)
    return result, fills, audit


def _collect_candidates(config: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    from yoyo.evaluation.monday_limit_reclaim_inputs import jobs

    score_table, score_audit = _read_stored_scores()
    score_map = score_table.set_index("event_id").score
    jobs_list = jobs()
    alt_jobs = [j for j in jobs_list if j["group"] == "crypto_5m" and j["symbol"] not in {"BTCUSDT", "ETHUSDT"}]
    job_map = {j["symbol"]: j for j in alt_jobs}
    symbols = set(job_map)
    historical: list[pd.DataFrame] = []
    source_stamps: dict[str, Any] = {}
    historical_event_ids: set[str] = set()
    for job in alt_jobs:
        symbol = job["symbol"]
        ledger_path = HIST_EXP / "symbols" / symbol / "trades.csv.gz"
        if not ledger_path.exists():
            raise FileNotFoundError(f"Missing frozen first-touch ledger: {ledger_path}")
        rows = _historical_events(symbol)
        historical_event_ids.update(rows.event_id.astype(str))
        rows["score"] = rows.event_id.map(score_map)
        rows["event_source"] = "frozen_historical_ledger"
        historical.append(rows)
        source_stamps[symbol] = {
            "ledger": file_stamp(ledger_path),
            "bar_paths": [file_stamp(Path(p)) for p in job["paths"]],
        }
    hist = pd.concat(historical, ignore_index=True) if historical else pd.DataFrame()
    score_event_ids = set(score_table.event_id.astype(str))
    hist["score_present"] = hist.event_id.isin(score_event_ids)
    hist["score_gt_0_5"] = hist.score.gt(0.5)

    october = _october_events(historical_event_ids, score_table, symbols)
    overlap_drift = october.attrs.get("overlap_drift", pd.DataFrame())
    if len(october):
        october["score_present"] = True
        october["score_gt_0_5"] = october.score.gt(0.5)
        all_candidates = pd.concat([hist, october], ignore_index=True, sort=False)
    else:
        all_candidates = hist
    all_candidates = all_candidates[all_candidates.score_present].copy()
    all_candidates["score_gt_0_5"] = all_candidates.score.gt(0.5)
    # Only strict >0.5 rows will read bar histories and enter the engine.
    selected = all_candidates[all_candidates.score_gt_0_5].copy()
    selected_by_symbol = {s: g.to_dict("records") for s, g in selected.groupby("symbol", sort=True)}
    score_audit.update(
        historical_altcoin_first_touch_rows=len(hist),
        historical_score_matched=int(hist.score_present.sum()),
        historical_score_missing=int((~hist.score_present).sum()),
        historical_score_gt_0_5=int(hist.score_gt_0_5.sum()),
        october_added_events=len(october),
        october_added_score_gt_0_5=int(october.score.gt(0.5).sum()) if len(october) else 0,
        october_score_overlap_rows=len(overlap_drift),
        october_overlap_max_abs_delta=(float(overlap_drift.score_delta_vs_frozen.abs().max()) if len(overlap_drift) else None),
        selected_score_gt_0_5=len(selected),
        selected_symbols=selected.symbol.nunique(),
    )
    EXP.mkdir(parents=True, exist_ok=True)
    all_candidates.to_csv(EXP / "first_touch_score_audit.csv.gz", index=False)
    if len(overlap_drift):
        overlap_drift.to_csv(EXP / "october_score_overlap_drift.csv.gz", index=False)
    dump(EXP / "input_manifest.json", {
        "score_audit": score_audit,
        "source_stamps": source_stamps,
        "october_scores": file_stamp(OCT_SCORES),
        "frozen_scores": file_stamp(FROZEN_SCORES),
        "score_audit_path": file_stamp(SCORE_AUDIT),
        "historical_source_experiment": str(HIST_EXP.relative_to(ROOT)),
    })
    return selected_by_symbol, {"score_audit": score_audit, "job_map": job_map,
                                 "historical_event_ids": historical_event_ids}


def _symbol_bars(symbol: str, job: dict[str, Any], *, has_historical: bool,
                 has_october: bool) -> tuple[pd.DataFrame, float, dict[str, Any]]:
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job

    pieces = []
    quality = {}
    if has_historical:
        frame, quality = load_job(job)
        pieces.append(frame)
    if has_october:
        frame = _load_october_bars(symbol)
        if len(frame):
            pieces.append(frame)
        else:
            quality["october_new_bars_missing"] = True
    if not pieces:
        raise ValueError(f"{symbol}: no bars available for selected events")
    combined = pd.concat(pieces).sort_index()
    combined, coverage = _check_bar_frame(combined, symbol)
    rounded, tick = _round_market_frame(combined)
    coverage.update(quality={k: quality[k] for k in ("duplicate_rows", "gaps", "gap_count", "first", "last") if k in quality})
    return rounded, tick, coverage


def process_symbol(symbol: str, records: list[dict[str, Any]], job: dict[str, Any],
                   config: dict[str, Any]) -> dict[str, Any]:
    start = time.perf_counter()
    dest = EXP / "symbols" / symbol
    dest.mkdir(parents=True, exist_ok=True)
    hist_records = [r for r in records if r["event_source"] == "frozen_historical_ledger"]
    oct_records = [r for r in records if r["event_source"] == "frozen_live_october_score_extension"]
    frame, tick, coverage = _symbol_bars(symbol, job, has_historical=bool(hist_records),
                                         has_october=bool(oct_records))
    index = frame.index
    raw_events = []
    rejected = []
    for record in records:
        row = dict(record)
        row["entry_open_time"] = _to_utc(row["entry_open_time"])
        source = str(row["event_source"])
        if row["entry_open_time"] not in index:
            row["reject_reason"] = "touch_bar_missing"
            rejected.append(row)
            continue
        if source == "frozen_live_october_score_extension":
            event_tick = float(row.get("tick", tick))
        else:
            event_tick = tick
        row["tick"] = event_tick
        event = _event_from_bar(type("Event", (), row), frame, index, tick, source)
        if event is None:
            row["reject_reason"] = "strict_wick_or_same_close_reclaim_failed"
            rejected.append(row)
            continue
        raw_events.append(_plan_window(event, frame, index))
    queued = [r for r in raw_events if r.get("native_status") == "queued"]
    no_future = [r for r in raw_events if r.get("native_status") != "queued"]
    if queued:
        # Native strategy uses close timestamps. Input bars are stored by open.
        # The index is shifted exactly once; entry_i still names the signal bar.
        close_frame = frame.copy()
        close_frame.index = close_frame.index + STEP
        events_df = pd.DataFrame(queued)
        for item in queued:
            item["entry_time"] = item["entry_open_time"] + STEP
            item["trade_id"] = item["event_id"]
        event_inputs = pd.DataFrame(queued)
        event_inputs["trade_id"] = event_inputs.event_id
        # Convert signal-bar opens to the close-time index required by the
        # reusable helper without changing the original entry index semantics.
        events_df = event_inputs[["trade_id", "event_id", "symbol", "week", "side", "entry_time",
                                  "entry", "stop", "score", "risk", "tick", "entry_i_source",
                                  "end_i_source", "censor_reason", "window_full_72h", "native_status",
                                  "stop_width_pct", "level", "entry_open_time", "event_source",
                                  "signal_open", "signal_high", "signal_low", "signal_close"]].copy()
        events_df["native_entry_i_source"] = events_df.entry_i_source
        # The reusable adapter binds entries by the close-time index itself.
        events_df["entry_time"] = pd.to_datetime(events_df.entry_time, utc=True)
        native_rows, fills, audit = evaluate_symbol_events(
            close_frame, events_df, bar_minutes=5,
            fee_rate=float(config["taker_fee_rate"]),
            slippage_bp_per_fill=float(config["slippage_bp_per_fill"]), symbol=symbol,
        )
        # Reuse the pre-planned horizon/censor bounds; the helper independently
        # derives them from the same event close index.
        if len(native_rows):
            event_map = {str(r["event_id"]): r for r in queued}
            for k, row in native_rows.iterrows():
                original = event_map[str(row.event_id)]
                for col in ("censor_reason", "window_full_72h", "end_i_source", "entry_open_time", "event_source"):
                    native_rows.at[k, col] = original.get(col)
                # The timestamp-derived close frame uses the same position
                # indexes; this cross-check catches accidental signal-bar use.
                if pd.Timestamp(row.entry_time) != pd.Timestamp(original["entry_time"]):
                    raise RuntimeError(f"{row.event_id}: native entry time differs from signal close")
            native_rows.to_csv(dest / "trades.csv.gz", index=False)
            fills.to_csv(dest / "fills.csv.gz", index=False)
        else:
            native_rows = pd.DataFrame()
            audit = {"positions": 0, "fills": 0, "engine": "NautilusTrader 1.231.0"}
    else:
        native_rows = pd.DataFrame()
        fills = pd.DataFrame()
        audit = {"positions": 0, "fills": 0, "engine": "NautilusTrader 1.231.0"}
    if rejected:
        pd.DataFrame(rejected).to_csv(dest / "rejected_candidates.csv.gz", index=False)
    pd.DataFrame(raw_events).to_csv(dest / "strict_events.csv.gz", index=False)
    done = {
        "symbol": symbol, "selected_score_events": len(records),
        "strict_wick_reclaim_events": len(raw_events), "queued_native_events": len(queued),
        "rejected_touch_events": len(rejected), "no_future_bar_events": len(no_future),
        "native_rows": len(native_rows), "fills": int(len(fills)),
        "coverage": coverage, "engine_audit": audit,
        "seconds": time.perf_counter() - start,
    }
    dump(dest / "done.json", done)
    return done


def summarize() -> dict[str, Any]:
    trade_paths = sorted((EXP / "symbols").glob("*/trades.csv.gz"))
    done_paths = sorted((EXP / "symbols").glob("*/done.json"))
    done = [json.loads(p.read_text()) for p in done_paths]
    strict_total = int(sum(d.get("strict_wick_reclaim_events", 0) for d in done))
    no_future_total = int(sum(d.get("no_future_bar_events", 0) for d in done))
    input_manifest_path = EXP / "input_manifest.json"
    input_manifest = json.loads(input_manifest_path.read_text()) if input_manifest_path.exists() else {}
    frames = [pd.read_csv(p) for p in trade_paths]
    trades = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if trades.empty:
        summary = {"n": 0, "status": "no_native_trades", "strict_wick_reclaim_events": strict_total,
                   "no_future_bar_events": no_future_total, "symbols_processed": len(done),
                   "score_audit": input_manifest.get("score_audit", {})}
        dump(EXP / "summary.json", summary)
        return summary
    trades["entry_time"] = pd.to_datetime(trades.entry_time, utc=True)
    trades["year"] = trades.entry_time.dt.year
    trades["period"] = np.select(
        [trades.year.lt(2025), trades.year.eq(2025), trades.year.ge(2026)],
        ["2020-2024", "2025", "2026+"], default="other")
    trades["actual_outcome_complete"] = trades.actual_outcome_complete.astype(bool)
    trades["censored"] = trades.censored.astype(bool)
    trades["stop_hit"] = trades.native_reason.astype(int).eq(1)
    metrics = []
    for (period, side), group in trades.groupby(["period", "side"], dropna=False):
        complete = group[group.actual_outcome_complete]
        rec = {
            "period": period, "side": "long" if int(side) > 0 else "short",
            "events": len(group), "complete_outcomes": len(complete),
            "censored": int(group.censored.sum()), "initial_stop_n": int(group.stop_hit.sum()),
            "stop_pct": float(100 * group.stop_hit.mean()),
            "mean_net_72h_bp": float(complete.native_net_after_slippage_bp.mean()) if len(complete) else None,
            "median_net_72h_bp": float(complete.native_net_after_slippage_bp.median()) if len(complete) else None,
            "win_pct_after_cost": float(100 * complete.native_net_after_slippage_bp.gt(0).mean()) if len(complete) else None,
            "stop_width_median_pct": float(group.stop_width_pct.median()),
            "mfe_pre_stop_median_pct": float(group.mfe_pre_stop_pct.median()),
        }
        for h in HORIZONS_H:
            col = f"mfe_{h}h_pct"
            rec[f"mfe_{h}h_n"] = int(group[col].notna().sum())
            rec[f"mfe_{h}h_median_pct"] = float(group[col].median()) if group[col].notna().any() else None
            rec[f"mark_net_{h}h_median_bp"] = float(group[f"mark_net_{h}h_bp"].median()) if group[f"mark_net_{h}h_bp"].notna().any() else None
        for th in THRESHOLDS_PCT:
            hit = group[f"hit_{th}pct_before_stop"].astype(bool)
            rec[f"hit_{th}pct_n"] = int(hit.sum())
            rec[f"back_to_be_{th}pct_later_bar_n"] = int(group[f"back_to_cost_be_{th}pct_later_bar"].astype(bool).sum())
            rec[f"same_stopbar_{th}pct_ohlc_n"] = int(group[f"samebar_trigger_stop_ohlc_{th}pct"].astype(bool).sum())
            rec[f"same_stopbar_{th}pct_olhc_n"] = int(group[f"samebar_trigger_stop_olhc_{th}pct"].astype(bool).sum())
        metrics.append(rec)
    metrics_df = pd.DataFrame(metrics)
    metrics_df.to_csv(EXP / "metrics_by_period_side.csv", index=False)
    trades.to_csv(EXP / "trades.csv.gz", index=False)
    source_coverage = pd.DataFrame([{
        "symbol": d["symbol"], "rows": d["coverage"]["rows"],
        "first_open_time": d["coverage"]["first_open_time"],
        "last_open_time": d["coverage"]["last_open_time"], "gaps": d["coverage"]["gaps"],
        "strict_events": d["strict_wick_reclaim_events"], "native_events": d["native_rows"],
        "censored": int(pd.read_csv(EXP / "symbols" / d["symbol"] / "trades.csv.gz").censored.astype(bool).sum())
        if (EXP / "symbols" / d["symbol"] / "trades.csv.gz").exists() else 0,
    } for d in done])
    source_coverage.to_csv(EXP / "bar_coverage.csv", index=False)
    summary = {
        "experiment_id": "exp-monday-reclaimed-wick-mfe-20261115-v1",
        "status": "complete",
        "engine": "NautilusTrader 1.231.0 / yoyo.layers.l3_backtest.nautilus_independent.run_native",
        "n": len(trades), "symbols": int(trades.symbol.nunique()),
        "score_min": float(trades.score.min()), "score_max": float(trades.score.max()),
        "entry_first": str(trades.entry_time.min()), "entry_last": str(trades.entry_time.max()),
        "strict_wick_reclaim_events": strict_total,
        "native_trade_rows": int(len(trades)),
        "no_future_bar_events": no_future_total,
        "complete_outcomes": int(trades.actual_outcome_complete.sum()),
        "censored_outcomes": int(trades.censored.sum()),
        "native_initial_stop_n": int(trades.stop_hit.sum()),
        "native_72h_close_n": int((trades.native_reason.eq(0) & ~trades.censored).sum()),
        "raw_after_cost_net_bp": {
            "mean": float(trades.loc[trades.actual_outcome_complete, "native_net_after_slippage_bp"].mean()),
            "median": float(trades.loc[trades.actual_outcome_complete, "native_net_after_slippage_bp"].median()),
        },
        "by_period_side": metrics,
        "bar_data": {
            "historical": "Binance UM 5m prepared inputs through 2026-09-23",
            "extension": "Binance UM 5m prepared live-october inputs through 2026-10-27 23:55 UTC",
            "known_break": "2026-10-28 daily archive missing; windows crossing any 5m gap are censored at the prior bar",
            "latest_score_touch_open": str(trades.entry_open_time.max()),
            "last_observed_bar_open": str(source_coverage.last_open_time.max()),
        },
        "costs": {
            "taker_fee_each_fill": 0.0005,
            "slippage_per_fill_bp": 10,
            "net_rule": "Nautilus actual fee per fill; subtract 10bp entry + 10bp exit on original entry notional separately",
            "maker_fee": 0.0002,
            "funding": "not modeled",
        },
        "limitations": [
            "Historical candles are Binance UM; fee rate is the owner-read OKX Lv1 proxy.",
            "Independent events can overlap; this is not a funded portfolio.",
            "Single-stop static outcomes do not depend on OHLC extreme ordering; stop-bar MFE is conservative by exclusion with OHLC/OLHC favorable-first sensitivities in each row.",
            "The October score extension differs from old stored scores on overlapping events; only new event IDs are appended, and old stored scores retain precedence.",
        ],
        "symbols_processed": len(done),
        "score_audit": input_manifest.get("score_audit", {}),
        "total_run_seconds": float(sum(d["seconds"] for d in done)),
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "training_eligible": False, "production_eligible": False,
    }
    dump(EXP / "summary.json", summary)
    return summary


def _committed() -> None:
    paths = ["yoyo/evaluation/monday_reclaimed_wick_mfe.py",
             "experiments/active/exp-monday-reclaimed-wick-mfe-20261115-v1/config.json"]
    for path in paths:
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", path], cwd=ROOT).returncode or \
                subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            raise RuntimeError("Commit the builder and config before reading or running the historical cohort")


def run(workers: int = 2) -> dict[str, Any]:
    _committed()
    config_path = EXP / "config.json"
    config = json.loads(config_path.read_text())
    selected_by_symbol, prepared = _collect_candidates(config)
    job_map = prepared["job_map"]
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    run_manifest = {
        "source_commit": source_commit, "selected_symbols": sorted(selected_by_symbol),
        "selected_score_events": sum(map(len, selected_by_symbol.values())),
        "score_audit": prepared["score_audit"], "workers": workers,
    }
    dump(EXP / "run_manifest.json", run_manifest)
    failures = []
    results = []
    # Two independent symbols at once limit the load against the external
    # Elements SE archive while the framework runs each symbol's merged event
    # windows as one native path.
    from concurrent.futures import ProcessPoolExecutor, as_completed
    symbols = sorted(selected_by_symbol)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        future = {pool.submit(process_symbol, symbol, selected_by_symbol[symbol],
                              job_map[symbol], config): symbol for symbol in symbols}
        for i, item in enumerate(as_completed(future), start=1):
            symbol = future[item]
            try:
                result = item.result()
                results.append(result)
                print(json.dumps({"progress": f"{i}/{len(symbols)}", **result}, default=str), flush=True)
            except Exception as exc:
                failure = {"symbol": symbol, "error": repr(exc)}
                failures.append(failure)
                dump(EXP / "symbols" / symbol / "error.json", failure)
                print(json.dumps(failure), flush=True)
    dump(EXP / "execution.json", {
        "expected_symbols": len(symbols), "completed_symbols": len(results),
        "failures": failures, "complete": not failures,
        "strict_events": sum(r["strict_wick_reclaim_events"] for r in results),
        "native_rows": sum(r["native_rows"] for r in results),
        "seconds": sum(r["seconds"] for r in results),
    })
    if failures:
        raise RuntimeError(f"{len(failures)} symbol execution failures; inspect execution.json")
    return summarize()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    result = summarize() if args.summarize else run(args.workers)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
