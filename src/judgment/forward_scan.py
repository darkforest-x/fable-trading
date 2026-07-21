"""SWAP candidate scanning and partial barrier outcome resolution.

Mainline (2026-07-15+): YOLO detector proposes candidates; LightGBM freeze
scores them; exits stay fixed TP5/SL2 (`resolve_forward_exit`). H1 shadow
reuses the same candidate/score path with scaled exits.
"""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
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
from src.judgment.yolo_candidates import (
    get_tip_edge_rejected,
    load_yolo_model,
    reset_tip_edge_rejected,
    scan_series_with_yolo,
)

ExitResolver = Callable[[pd.DataFrame, int], Optional[ForwardExit]]

# Recent-tail length for live scans (see jobs assembly below).
LIVE_TAIL_BARS = 2000


def _forward_workers() -> int:
    """Series-level parallelism for live YOLO. Override with FABLE_FORWARD_WORKERS."""
    raw = os.environ.get("FABLE_FORWARD_WORKERS", "").strip()
    if raw:
        try:
            return max(1, min(8, int(raw)))
        except ValueError:
            pass
    # Default 3: render can overlap; predict is locked inside yolo_candidates.
    return 3


def scan_forward_records(
    scan: ForwardScanInput,
    *,
    exit_resolver: Optional[ExitResolver] = None,
    yolo_weights: str | Path | None = None,
    yolo_mode: str = "live",
) -> ForwardScanResult:
    """Scan SWAP series for threshold signals and resolve exits.

    `exit_resolver` defaults to mainline TP5/SL2. Pass
    `resolve_forward_exit_scaled` for the H1 shadow paper book.

    `yolo_weights` / `yolo_mode` override the mainline detector for shadow
    books (e.g. v12 tip-only). Mainline callers leave defaults.
    """
    resolve = exit_resolver or resolve_forward_exit
    records: list[ForwardRecord] = []
    scanned_series = 0
    candidates_seen = 0
    threshold_signals_seen = 0
    tracked_keys = open_keys(scan.existing_log)
    yolo_model = None
    if CANDIDATE_SOURCE == "yolo":
        yolo_model = load_yolo_model(yolo_weights) if yolo_weights is not None else load_yolo_model()

    jobs: list[tuple[str, str, pd.DataFrame]] = []
    for source, symbol, frame in iter_series(bar="15m", min_bars=500):
        if source != "okx" or not symbol.endswith("_USDT_SWAP"):
            continue
        if is_stockish(symbol):
            continue
        # Live scans only need a recent tail, not 400 days: indicators/MAs were
        # recomputed over the FULL history for every series every pulse, and
        # that pandas cost grows with the archive. 2000 bars (~3 weeks) keeps
        # every lookback numerically converged at the bars we score (max
        # rolling=168, WARMUP=288; the EWMs -- EMA120/ATR14 -- differ only at
        # the 1e-11 level after this much warm-up) and caps how far back a
        # pulse can "discover" old signals, which the freshness gates would
        # reject anyway.
        jobs.append((source, symbol, frame.tail(LIVE_TAIL_BARS).reset_index(drop=True)))
    scanned_series = len(jobs)
    workers = _forward_workers() if CANDIDATE_SOURCE == "yolo" else 1
    wlabel = str(yolo_weights) if yolo_weights is not None else "owner_best"
    print(
        f"forward_scan: series={scanned_series} workers={workers} source={CANDIDATE_SOURCE} "
        f"yolo_mode={yolo_mode} weights={wlabel}",
        flush=True,
    )
    reset_tip_edge_rejected()

    def _discover(job: tuple[str, str, pd.DataFrame]) -> tuple[str, str, pd.DataFrame, pd.DataFrame, list[int]]:
        """Phase 1 (parallel-safe): indicators + YOLO/rules indices only."""
        source, symbol, frame = job
        enriched = add_indicators(frame)
        signal_indices = set(
            forward_candidate_indices(
                enriched,
                frame=frame,
                yolo_model=yolo_model,
                start_time=scan.start_time,
                yolo_mode=yolo_mode,
            )
        )
        tracked_times = {key[2] for key in tracked_keys if key[0] == source and key[1] == symbol}
        if tracked_times:
            signal_times = enriched["open_time"].astype(str)
            signal_indices.update(
                int(idx) for idx in signal_times[signal_times.isin(tracked_times)].index
            )
        return source, symbol, frame, enriched, sorted(signal_indices)

    t_discover = time.monotonic()
    discovered: list[tuple[str, str, pd.DataFrame, pd.DataFrame, list[int]]] = []
    if workers <= 1:
        discovered = [_discover(job) for job in jobs]
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_discover, job) for job in jobs]
            for fut in as_completed(futs):
                discovered.append(fut.result())
    t_phase2 = time.monotonic()
    tip_edge_n = get_tip_edge_rejected()
    print(
        f"forward_scan: discover_wall={t_phase2 - t_discover:.0f}s "
        f"(indicators+render+predict, {workers} workers) "
        f"tip_edge_rejected={tip_edge_n}",
        flush=True,
    )

    # Phase 2 (sequential): LightGBM predict + barrier resolve (not thread-safe).
    for source, symbol, frame, enriched, ordered_indices in discovered:
        if not ordered_indices:
            continue
        featured = add_features(enriched)
        feature_rows = extract_feature_rows(featured, ordered_indices)
        scores = scan.booster.predict(
            feature_rows[FEATURE_COLUMNS], num_iteration=scan.artifact.best_iteration
        )
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
            # Tip signal: entry bar hasn't printed. entry_time is known (next
            # bar open = signal bar close time); entry_price uses the signal
            # bar close as a PROXY so TG/executor have a sane number, and
            # maker_filled stays empty as the "entry pending backfill" sentinel
            # -- merge_forward_log overwrites all three with the true next-bar
            # values on the following pulse.
            tip_pending = entry_i >= len(enriched)
            if tip_pending:
                entry_time = str(signal_time + pd.Timedelta(minutes=15))
                entry_price = float(enriched["close"].iloc[signal_i])
                maker_filled = None
            else:
                entry_time = str(pd.Timestamp(enriched["open_time"].iloc[entry_i]))
                entry_price = float(enriched["open"].iloc[entry_i])
                maker_filled = bool(
                    float(enriched["low"].iloc[entry_i]) < float(enriched["open"].iloc[entry_i])
                )
            # Tiered sizing (owner 2026-07-20): tier is stamped at detection
            # time from the artifact sidecar; artifacts without sizing_tiers
            # (shadow books, stubs) log the legacy 1x.
            tiers = getattr(scan.artifact, "sizing_tiers", None)
            if tiers is not None:
                tier, size_mult = tiers.tier_for_score(score, scan.artifact.threshold)
            else:
                tier, size_mult = "", 1.0
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
                    "entry_time": entry_time,
                    "entry_price": entry_price,
                    "maker_filled": maker_filled,
                    "outcome": exit_state.outcome,
                    "label": exit_state.label,
                    "exit_offset": exit_state.exit_offset,
                    "exit_time": exit_state.exit_time,
                    "realized_ret": exit_state.realized_ret,
                    "atr_pct": float(feature_row["atr_pct"]),
                    "dense_run_len": int(feature_row["dense_run_len"]),
                    "tier": tier,
                    "size_mult": size_mult,
                }
            )
    print(
        f"forward_scan: phase2_wall={time.monotonic() - t_phase2:.0f}s "
        f"(features+score+resolve, {sum(1 for d in discovered if d[4])} series with candidates)",
        flush=True,
    )
    return ForwardScanResult(records, scanned_series, candidates_seen, threshold_signals_seen)


def forward_candidate_indices(
    enriched: pd.DataFrame,
    *,
    frame: pd.DataFrame | None = None,
    yolo_model=None,
    start_time: pd.Timestamp | None = None,
    yolo_mode: str = "live",
) -> list[int]:
    """Mainline candidate bars: YOLO by default, rules if CANDIDATE_SOURCE=rules."""
    if CANDIDATE_SOURCE == "rules":
        return _rule_candidate_indices(enriched)
    # YOLO path
    raw = frame if frame is not None else enriched
    start_from_i = None
    if start_time is not None and "open_time" in raw.columns:
        times = pd.to_datetime(raw["open_time"], utc=True)
        st = pd.Timestamp(start_time)
        if st.tzinfo is None:
            st = st.tz_localize("UTC")
        else:
            st = st.tz_convert("UTC")
        hits = np.flatnonzero(times >= st)
        if len(hits) == 0:
            # FORWARD_START often sits *inside* the still-open 15m bar (e.g. start
            # 16:30 while last *closed* open_time is 16:15). Returning [] here
            # blanked the whole live gate after the 2026-07-19 retest clock reset
            # (candidates_seen=0 on 344 series). Still scan the tip; the score
            # stage already drops signal_time < start_time for new rows.
            start_from_i = max(0, len(raw) - 10)
        else:
            start_from_i = max(0, int(hits[0]) - 5)
    mode = yolo_mode if yolo_mode in ("live", "tip", "full") else "live"
    return scan_series_with_yolo(raw, yolo_model, start_from_i=start_from_i, mode=mode)


def _rule_candidate_indices(enriched: pd.DataFrame) -> list[int]:
    if len(enriched) < WARMUP_BARS + 2:
        return []
    mask = strict_mask(enriched, mode="expanded").fillna(False)
    idx = np.flatnonzero(mask.to_numpy())
    # live fallback path: the tip bar is a valid signal (entry backfills next pulse)
    idx = idx[(idx >= WARMUP_BARS) & (idx < len(enriched))]
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
    atr = float(enriched["atr14"].iloc[signal_i])
    atr_pct = float(enriched["atr_pct"].iloc[signal_i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    if not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    if entry_i >= len(enriched):
        # Tip signal (2026-07-20 real-time path): the signal bar IS the newest
        # closed bar, so the entry bar has not printed yet. Record it as open
        # with pending entry fields (backfilled next pulse) instead of dropping
        # it -- dropping cost 15-22 min of edge on every live signal.
        return ForwardExit("open", "", -1, 0, "", float("nan"))
    entry = float(enriched["open"].iloc[entry_i])
    if not np.isfinite(entry) or entry <= 0:
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
    atr = float(enriched["atr14"].iloc[signal_i])
    atr_pct = float(enriched["atr_pct"].iloc[signal_i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    if not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    if entry_i >= len(enriched):
        # tip signal: entry bar not printed yet (see resolve_forward_exit)
        return ForwardExit("open", "", -1, 0, "", float("nan"))
    entry = float(enriched["open"].iloc[entry_i])
    if not np.isfinite(entry) or entry <= 0:
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
