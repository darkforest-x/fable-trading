"""SWAP candidate scanning and partial TP5/SL2 outcome resolution."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.judgment.candidates import MIN_GAP_BARS, WARMUP_BARS, add_indicators, strict_mask
from src.judgment.features import FEATURE_COLUMNS, add_features, extract_feature_rows
from src.judgment.forward_records import forward_key, open_keys
from src.judgment.forward_types import (
    BAR,
    SL_MULT,
    TP_MULT,
    ForwardExit,
    ForwardRecord,
    ForwardScanInput,
    ForwardScanResult,
)
from src.judgment.labeling import ATR_PCT_MIN, HORIZON_BARS


def scan_forward_records(scan: ForwardScanInput) -> ForwardScanResult:
    records: list[ForwardRecord] = []
    scanned_series = 0
    candidates_seen = 0
    threshold_signals_seen = 0
    tracked_keys = open_keys(scan.existing_log)
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if source != "okx" or not symbol.endswith("_USDT_SWAP"):
            continue
        scanned_series += 1
        enriched = add_indicators(frame)
        signal_indices = set(forward_candidate_indices(enriched))
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
            exit_state = resolve_forward_exit(enriched, signal_i)
            if exit_state is None:
                continue
            threshold_signals_seen += 1
            entry_i = signal_i + 1
            feature_row = feature_rows.iloc[row_pos]
            records.append(
                {
                    "source": source,
                    "symbol": symbol,
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


def forward_candidate_indices(enriched: pd.DataFrame) -> list[int]:
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


def _exit_time(entry_time: pd.Timestamp, exit_offset: int) -> str:
    return str(entry_time + exit_offset * BAR)
