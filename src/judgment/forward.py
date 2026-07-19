"""Forward tracking entrypoint for the frozen tp5_sl2 SWAP model.

Also provides H1 scaled *shadow* tracking: same mainline freeze for entry
scoring/threshold, but exit outcomes from scaled barrier math, written only to
`data/forward_log_h1_scaled.csv` (never mainline `forward_log.csv`).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import pandas as pd

from src.judgment.forward_records import (
    merge_forward_log,
    read_forward_log,
    write_forward_log,
)
from src.judgment.forward_scan import (
    ExitResolver,
    forward_candidate_indices,
    resolve_forward_exit,
    resolve_forward_exit_scaled,
    scan_forward_records,
)
from src.judgment.forward_types import (
    FORWARD_LOG_H1_SCALED_PATH,
    FORWARD_LOG_PATH,
    FORWARD_START,
    ForwardRecord,
    ForwardRunSummary,
    ForwardScanInput,
    ForwardSummaryJson,
)
from src.judgment.frozen import DEFAULT_FROZEN_CONFIG, latest_artifact

__all__ = (
    "FORWARD_LOG_H1_SCALED_PATH",
    "FORWARD_LOG_PATH",
    "FORWARD_START",
    "ForwardRecord",
    "ForwardRunSummary",
    "ForwardSummaryJson",
    "forward_candidate_indices",
    "merge_forward_log",
    "normalize_start_time",
    "resolve_forward_exit",
    "resolve_forward_exit_scaled",
    "run_forward_tracking",
    "run_forward_tracking_h1_shadow",
    "summary_to_json",
)


def run_forward_tracking(
    output_path: Path = FORWARD_LOG_PATH,
    start_time: pd.Timestamp = FORWARD_START,
) -> ForwardRunSummary:
    return _run_forward_tracking(
        output_path=output_path,
        start_time=start_time,
        exit_resolver=resolve_forward_exit,
    )


def run_forward_tracking_h1_shadow(
    output_path: Path = FORWARD_LOG_H1_SCALED_PATH,
    start_time: pd.Timestamp = FORWARD_START,
) -> ForwardRunSummary:
    """Shadow paper book for H1 scaled exits.

    Entry signals: mainline frozen TP5/SL2 SWAP model + val-q90 threshold
    (identical candidate universe and score filter). Outcomes: scaled 2.5 bank
    + 3 trail via `resolve_forward_exit_scaled`.

    Refuses to write into the mainline log path. Legacy
    `models/frozen_scaled_25_t3_*` is a stub and is intentionally not loaded;
    a proper scaled-label freeze is a future owner step (see plan doc).
    """
    resolved = Path(output_path).resolve()
    if resolved == Path(FORWARD_LOG_PATH).resolve():
        raise ValueError(
            "H1 shadow must not write to mainline data/forward_log.csv; "
            f"use {FORWARD_LOG_H1_SCALED_PATH} (or another non-mainline path)"
        )
    return _run_forward_tracking(
        output_path=output_path,
        start_time=start_time,
        exit_resolver=resolve_forward_exit_scaled,
    )


def _run_forward_tracking(
    *,
    output_path: Path,
    start_time: pd.Timestamp,
    exit_resolver: ExitResolver,
) -> ForwardRunSummary:
    import os

    # OpenMP/thread clash between torch and lightgbm can hang multi-series YOLO scans.
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")

    normalized_start = normalize_start_time(start_time)
    artifact = latest_artifact(DEFAULT_FROZEN_CONFIG)
    if artifact is None:
        raise FileNotFoundError("missing frozen artifact; run scripts/freeze_model.py first")
    existing = read_forward_log(output_path)
    # Load YOLO *before* LightGBM booster when YOLO is the candidate source —
    # reverse order has hung on Apple Silicon mid-scan (0% CPU sleep).
    from src.judgment.forward_types import CANDIDATE_SOURCE
    from src.judgment.yolo_candidates import load_yolo_model

    if CANDIDATE_SOURCE == "yolo":
        load_yolo_model()
    scan = scan_forward_records(
        ForwardScanInput(
            artifact=artifact,
            booster=lgb.Booster(model_file=str(artifact.model_path)),
            detected_at=datetime.now(timezone.utc).isoformat(),
            start_time=normalized_start,
            existing_log=existing,
        ),
        exit_resolver=exit_resolver,
    )
    # Same-symbol minimum gap (2026-07-19): the validated pool spaces signals
    # >= MIN_GAP_BARS (18 bars = 4.5h) per symbol, but each live pulse scans
    # independently, so one move emitted KAITO signals at 03:00/03:15/03:30/
    # 04:00 -- four counts of the same trade. Enforce the pool's spacing here:
    # a NEW signal is dropped if the log (or this batch) already has one for
    # the same symbol within the gap. Exit updates of existing keys pass through.
    from src.judgment.candidates import MIN_GAP_BARS
    from src.judgment.forward_records import row_key as _row_key

    gap = pd.Timedelta(minutes=15 * MIN_GAP_BARS)
    known_keys = {_row_key(r) for r in existing.to_dict("records")} if not existing.empty else set()
    sym_times: dict[str, list[pd.Timestamp]] = {}
    if not existing.empty:
        for _, r in existing.iterrows():
            sym_times.setdefault(str(r["symbol"]), []).append(
                pd.Timestamp(r["signal_time"]).tz_localize("UTC")
                if pd.Timestamp(r["signal_time"]).tzinfo is None
                else pd.Timestamp(r["signal_time"]).tz_convert("UTC"))
    gapped_records = []
    for rec in scan.records:
        key = _row_key(rec) if isinstance(rec, dict) else None
        ts = pd.Timestamp(rec["signal_time"])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        sym = str(rec["symbol"])
        if key not in known_keys and any(abs(ts - t) < gap for t in sym_times.get(sym, [])):
            continue
        gapped_records.append(rec)
        sym_times.setdefault(sym, []).append(ts)
    merged = merge_forward_log(existing, gapped_records)
    write_forward_log(output_path, merged.frame)
    # Telegram: only mainline path, only brand-new signal keys (not exit updates).
    if Path(output_path).resolve() == Path(FORWARD_LOG_PATH).resolve() and merged.new_signals:
        try:
            from src.judgment.forward_records import forward_key, row_key
            from src.notify_signal import notify_new_forward_signals

            existing_keys = {row_key(r) for r in existing.to_dict("records")} if not existing.empty else set()
            now_utc = pd.Timestamp.now(tz="UTC")
            brand_new: list[dict] = []
            for rec in scan.records:
                key = forward_key(rec["source"], rec["symbol"], pd.Timestamp(rec["signal_time"]))
                if key in existing_keys:
                    continue
                # Alert only on ACTIONABLE signals. A catch-up scan backfills
                # history whose outcome is already sealed (status=closed, or a
                # signal bar hours old) -- on 2026-07-18 the first yolo-source
                # pulse pushed dozens of those to the channel and the owner
                # reasonably asked why OKX had not traded them. Match the
                # executor freshness gate (max_signal_age_min=55) so TG never
                # pages about trades nobody can take.
                if str(rec.get("status", "")).lower() != "open":
                    continue
                sig_ts = pd.Timestamp(rec["signal_time"])
                if sig_ts.tzinfo is None:
                    sig_ts = sig_ts.tz_localize("UTC")
                if now_utc - sig_ts > pd.Timedelta(minutes=55):
                    continue
                row = dict(rec)
                # absolute ATR for chart TP/SL (atr_pct ≈ atr14/close)
                if row.get("atr14") is None and row.get("atr_pct") and row.get("entry_price"):
                    row["atr14"] = float(row["entry_price"]) * float(row["atr_pct"])
                brand_new.append(row)
            n_sent = notify_new_forward_signals(brand_new)
            print(f"tg_signal: new={len(brand_new)} sent_ok={n_sent}")
        except Exception as exc:  # noqa: BLE001 -- never block forward tracking
            print(f"tg_signal: skipped ({exc})")
    open_rows = int((merged.frame["status"] != "closed").sum()) if not merged.frame.empty else 0
    closed_rows = int((merged.frame["status"] == "closed").sum()) if not merged.frame.empty else 0
    return ForwardRunSummary(
        artifact=artifact,
        start_time=normalized_start,
        scanned_series=scan.scanned_series,
        candidates_seen=scan.candidates_seen,
        threshold_signals_seen=scan.threshold_signals_seen,
        new_signals=merged.new_signals,
        closed_updates=merged.closed_updates,
        total_rows=int(len(merged.frame)),
        open_rows=open_rows,
        closed_rows=closed_rows,
        output=output_path,
    )


def normalize_start_time(value: pd.Timestamp) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def summary_to_json(summary: ForwardRunSummary) -> str:
    return json.dumps(summary.to_json(), ensure_ascii=False, indent=2)
