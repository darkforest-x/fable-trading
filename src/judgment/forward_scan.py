"""SWAP candidate scanning and partial barrier outcome resolution.

Mainline (2026-07-15+): YOLO detector proposes candidates; LightGBM freeze
scores them; exits stay fixed TP5/SL2 (`resolve_forward_exit`). H1 shadow
reuses the same candidate/score path with scaled exits.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Optional

import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.data.universe import is_stockish
from src.judgment.candidates import MIN_GAP_BARS, WARMUP_BARS, add_indicators, strict_mask
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.forward_records import forward_key, open_keys
from src.judgment.forward_types import (
    BAR,
    CANDIDATE_SOURCE,
    SCALED_SL_MULT,
    SCALED_TP1_MULT,
    SCALED_TRAIL_MULT,
    SL_MULT,
    TP_MULT,
    ForwardExit,
    ForwardRecord,
    ForwardScanInput,
    ForwardScanResult,
)
from src.judgment.labeling import ATR_PCT_MIN, HORIZON_BARS
from src.judgment.yolo_candidates import load_yolo_model, scan_series_with_yolo

ExitResolver = Callable[[pd.DataFrame, int], Optional[ForwardExit]]


def scan_forward_records(
    scan: ForwardScanInput,
    *,
    exit_resolver: Optional[ExitResolver] = None,
) -> ForwardScanResult:
    """Scan SWAP series for threshold signals and resolve exits.

    `exit_resolver` defaults to mainline TP5/SL2. Pass
    `resolve_forward_exit_scaled` for the H1 shadow paper book.
    """
    resolve = exit_resolver or resolve_forward_exit
    records: list[ForwardRecord] = []
    scanned_series = 0
    candidates_seen = 0
    threshold_signals_seen = 0
    tracked_keys = open_keys(scan.existing_log)
    yolo_model = None
    if CANDIDATE_SOURCE == "yolo":
        yolo_model = load_yolo_model()
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if source != "okx" or not symbol.endswith("_USDT_SWAP"):
            continue
        if is_stockish(symbol):
            continue
        scanned_series += 1
        enriched = add_indicators(frame)
        signal_indices = set(
            forward_candidate_indices(enriched, frame=frame, yolo_model=yolo_model, start_time=scan.start_time)
        )
        tracked_times = {key[2] for key in tracked_keys if key[0] == source and key[1] == symbol}
        if tracked_times:
            signal_times = enriched["open_time"].astype(str)
            signal_indices.update(int(idx) for idx in signal_times[signal_times.isin(tracked_times)].index)
        if not signal_indices:
            continue
        featured = add_features(enriched)
        ordered_indices = sorted(signal_indices)
        feature_rows = extract_feature_rows(featured, ordered_indices)
        scores = scan.booster.predict(feature_rows[FEATURE_COLUMNS], num_iteration=scan.artifact.best_iteration)
        candidates_seen += len(ordered_indices)
        for row_pos, signal_i in enumerate(ordered_indices):
            signal_time = pd.Timestamp(enriched["open_time"].iloc[signal_i])
            key = forward_key(source, symbol, signal_time)
            tracked_open = key in tracked_keys
            if not tracked_open and signal_time < scan.start_time:
                continue
            score = float(scores[row_pos])
            if not tracked_open and score < scan.artifact.threshold:
                continue
            exit_state = resolve(enriched, signal_i)
            if exit_state is None:
                continue
            threshold_signals_seen += 1
            entry_i = signal_i + 1
            feature_row = feature_rows.iloc[row_pos]
            records.append(
                {
                    "source": source,
                    "symbol": symbol,
                    "is_stockish": is_stockish(symbol),
                    "signal_time": str(signal_time),
                    "detected_at": scan.detected_at,
                    "status": exit_state.status,
                    "score": score,
                    "threshold": scan.artifact.threshold,
                    "model_path": scan.artifact.relative_model_path,
                    "dataset_sha256": scan.artifact.dataset_sha256,
                    "signal_i": int(signal_i),
                    "entry_time": str(pd.Timestamp(enriched["open_time"].iloc[entry_i])),
                    "entry_price": float(enriched["open"].iloc[entry_i]),
                    "maker_filled": bool(float(enriched["low"].iloc[entry_i]) < float(enriched["open"].iloc[entry_i])),
                    "outcome": exit_state.outcome,
                    "label": exit_state.label,
                    "exit_offset": exit_state.exit_offset,
                    "exit_time": exit_state.exit_time,
                    "realized_ret": exit_state.realized_ret,
                    "atr_pct": float(feature_row["atr_pct"]),
                    "dense_run_len": int(feature_row["dense_run_len"]),
                }
            )
    return ForwardScanResult(records, scanned_series, candidates_seen, threshold_signals_seen)


def forward_candidate_indices(
    enriched: pd.DataFrame,
    *,
    frame: pd.DataFrame | None = None,
    yolo_model=None,
    start_time: pd.Timestamp | None = None,
) -> list[int]:
    """Mainline candidate bars: YOLO by default, rules if CANDIDATE_SOURCE=rules."""
    if CANDIDATE_SOURCE == "rules":
        return _rule_candidate_indices(enriched)
    # YOLO path
    raw = frame if frame is not None else enriched
    start_from_i = None
    if start_time is not None and "open_time" in raw.columns:
        times = pd.to_datetime(raw["open_time"], utc=True)
        hits = np.flatnonzero(times >= pd.Timestamp(start_time))
        if len(hits) == 0:
            # series ends before forward clock — skip (do NOT full-history YOLO)
            return []
        start_from_i = max(0, int(hits[0]) - 5)
    return scan_series_with_yolo(raw, yolo_model, start_from_i=start_from_i)


def _rule_candidate_indices(enriched: pd.DataFrame) -> list[int]:
    if len(enriched) < WARMUP_BARS + 2:
        return []
    mask = strict_mask(enriched, mode="expanded").fillna(False)
    idx = np.flatnonzero(mask.to_numpy())
    idx = idx[(idx >= WARMUP_BARS) & (idx + 1 < len(enriched))]
    if len(idx) == 0:
        return []
    scores = enriched["shape_score"].to_numpy()
    selected: list[int] = []
    for signal_i in sorted(idx, key=lambda item: scores[item], reverse=True):
        if all(abs(signal_i - previous) >= MIN_GAP_BARS for previous in selected):
            selected.append(int(signal_i))
    return sorted(selected)


def resolve_forward_exit(enriched: pd.DataFrame, signal_i: int) -> ForwardExit | None:
    entry_i = signal_i + 1
    if entry_i >= len(enriched):
        return None
    atr = float(enriched["atr14"].iloc[signal_i])
    entry = float(enriched["open"].iloc[entry_i])
    atr_pct = float(enriched["atr_pct"].iloc[signal_i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None
    if not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    last_i = entry_i + HORIZON_BARS - 1
    available_last_i = min(last_i, len(enriched) - 1)
    highs = enriched["high"].to_numpy()[entry_i : available_last_i + 1]
    lows = enriched["low"].to_numpy()[entry_i : available_last_i + 1]
    upper = entry + TP_MULT * atr
    lower = entry - SL_MULT * atr
    hit_up = highs >= upper
    hit_dn = lows <= lower
    up_first = int(np.argmax(hit_up)) if hit_up.any() else len(highs)
    dn_first = int(np.argmax(hit_dn)) if hit_dn.any() else len(highs)
    entry_time = pd.Timestamp(enriched["open_time"].iloc[entry_i])
    if up_first < dn_first:
        exit_offset = up_first + 1
        return ForwardExit("closed", "tp", 1, exit_offset, _exit_time(entry_time, exit_offset), upper / entry - 1)
    if dn_first < up_first:
        exit_offset = dn_first + 1
        return ForwardExit("closed", "sl", 0, exit_offset, _exit_time(entry_time, exit_offset), lower / entry - 1)
    if up_first == dn_first < len(highs):
        exit_offset = dn_first + 1
        return ForwardExit(
            "closed", "sl_ambiguous", 0, exit_offset, _exit_time(entry_time, exit_offset), lower / entry - 1
        )
    if available_last_i >= last_i:
        realized_ret = float(enriched["close"].iloc[last_i]) / entry - 1
        return ForwardExit(
            "closed", "timeout", 0, HORIZON_BARS, _exit_time(entry_time, HORIZON_BARS), realized_ret
        )
    return ForwardExit("open", "", -1, 0, "", float("nan"))


def resolve_forward_exit_scaled(
    enriched: pd.DataFrame,
    signal_i: int,
    *,
    tp1_mult: float = SCALED_TP1_MULT,
    trail_mult: float = SCALED_TRAIL_MULT,
    sl_mult: float = SCALED_SL_MULT,
    horizon: int = HORIZON_BARS,
) -> ForwardExit | None:
    """Partial-horizon port of `label_candidate_scaled` for forward shadow logs.

    Math matches labeling.py: hard SL until TP1 (half bank), then trail under
    running high; stop checked before target within a bar; trail uses prior-bar
    run_max. Incomplete horizon without a terminal barrier → status=open.
    """
    entry_i = signal_i + 1
    if entry_i >= len(enriched):
        return None
    atr = float(enriched["atr14"].iloc[signal_i])
    entry = float(enriched["open"].iloc[entry_i])
    atr_pct = float(enriched["atr_pct"].iloc[signal_i])
    if not np.isfinite(atr) or atr <= 0 or not np.isfinite(entry) or entry <= 0:
        return None
    if not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None

    last_i = entry_i + horizon - 1
    available_last_i = min(last_i, len(enriched) - 1)
    n_bars = available_last_i - entry_i + 1
    if n_bars <= 0:
        return None

    highs = enriched["high"].to_numpy()[entry_i : available_last_i + 1]
    lows = enriched["low"].to_numpy()[entry_i : available_last_i + 1]
    opens = enriched["open"].to_numpy()[entry_i : available_last_i + 1]
    entry_time = pd.Timestamp(enriched["open_time"].iloc[entry_i])

    hard_stop = entry - sl_mult * atr
    tp1 = entry + tp1_mult * atr
    ret1: float | None = None
    run_max = tp1

    for j in range(n_bars):
        if ret1 is None:
            if lows[j] <= hard_stop:  # stop first: conservative
                exit_price = min(hard_stop, float(opens[j]))
                ret = exit_price / entry - 1
                exit_offset = j + 1
                return ForwardExit("closed", "sl", 0, exit_offset, _exit_time(entry_time, exit_offset), ret)
            if highs[j] >= tp1:
                ret1 = tp1 / entry - 1
            continue  # phase-2 trailing starts on the NEXT bar
        stop = max(run_max - trail_mult * atr, hard_stop)
        if lows[j] <= stop:
            exit_price = min(stop, float(opens[j]))
            ret = 0.5 * ret1 + 0.5 * (exit_price / entry - 1)
            exit_offset = j + 1
            return ForwardExit(
                "closed", "scaled", int(ret > 0), exit_offset, _exit_time(entry_time, exit_offset), ret
            )
        run_max = max(run_max, float(highs[j]))

    if available_last_i >= last_i:
        timeout_close = float(enriched["close"].iloc[last_i])
        if ret1 is None:
            ret = timeout_close / entry - 1
            return ForwardExit(
                "closed", "timeout", int(ret > 0), horizon, _exit_time(entry_time, horizon), ret
            )
        ret = 0.5 * ret1 + 0.5 * (timeout_close / entry - 1)
        return ForwardExit(
            "closed", "scaled_timeout", int(ret > 0), horizon, _exit_time(entry_time, horizon), ret
        )
    return ForwardExit("open", "", -1, 0, "", float("nan"))


def _exit_time(entry_time: pd.Timestamp, exit_offset: int) -> str:
    return str(entry_time + exit_offset * BAR)
