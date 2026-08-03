"""SWAP candidate scanning and partial barrier outcome resolution.

Mainline (2026-07-31+ short_star v10): YOLO proposes candidates; LightGBM freeze
scores them; exits are side-aware TP5/SL2.
  - long  → resolve_forward_exit (upper TP / lower SL)
  - short → resolve_forward_exit_short (lower TP / upper SL; PnL = 1 - exit/entry)
H1 shadow reuses the same candidate/score path with scaled exits (long geometry).

P0 fix 2026-07-31: never hardcode side=long when the frozen config is short.
Executor remains long-only and will skip_unsupported_side on short until short
execution is owner-enabled.

P0 fix 2026-08-03: trade side and feature semantics are two different facts and
were briefly collapsed into one. Side decides the barrier geometry -- that part
was right. Which extractor to call is not a property of the trade; it is a
property of the coordinate system the model was TRAINED in, and it must come from
the artifact. Choosing the extractor by side served the short v10 model six
negated features (align_short_feature_rows flips ext_up, close_vs_ema55,
close_vs_ema200, order_score, slow_slope_12 and ret_*), while that model's own
training pool holds unaligned values -- confirmed by recomputing 14 rows both
ways, exact on plain, zero matches on aligned. See
analysis/p0_baseline_audit_20260803.md.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from src.data.loader import iter_series
from src.data.universe import is_stockish
from src.judgment.candidates import MIN_GAP_BARS, WARMUP_BARS, add_indicators, strict_mask
from src.judgment.features import (
    FEATURE_COLUMNS,
    add_features,
    extract_feature_rows_for_semantics,
)
from src.judgment.forward_records import forward_key, open_keys
from src.judgment.forward_types import (
    BAR,
    CANDIDATE_SOURCE,
    RUNTIME_MODE,
    SCALED_SL_MULT,
    SCALED_TP1_MULT,
    SCALED_TRAIL_MULT,
    SL_MULT,
    TP_MULT,
    ForwardExit,
    ForwardRecord,
    ForwardScanInput,
    ForwardScanResult,
    validate_candidate_source,
)
from src.judgment.labeling import ATR_PCT_MIN, HORIZON_BARS
from src.judgment.yolo_candidates import (
    get_tip_edge_rejected,
    load_yolo_model,
    reset_tip_edge_rejected,
    resolve_tip_conf,
    resolve_yolo_mode,
    scan_series_with_yolo,
)

ExitResolver = Callable[[pd.DataFrame, int], Optional[ForwardExit]]

# Recent-tail length for live scans (see jobs assembly below).
LIVE_TAIL_BARS = 2000


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    yolo_mode: str | None = None,
) -> ForwardScanResult:
    """Scan SWAP series for threshold signals and resolve exits.

    `exit_resolver` defaults to side-aware TP5/SL2 (long or short from artifact).
    Pass `resolve_forward_exit_scaled` for the H1 shadow paper book (long geometry).

    `yolo_weights` / `yolo_mode` override the mainline detector for shadow
    books (e.g. v12 tip-only). Mainline callers leave defaults; unset
    `yolo_mode` resolves from env ``FABLE_YOLO_MODE`` (default live).
    """
    candidate_source = validate_candidate_source(CANDIDATE_SOURCE, RUNTIME_MODE)
    protocol = scan.protocol
    if RUNTIME_MODE == "production" and protocol is None:
        raise RuntimeError("production forward scan requires a verified strategy protocol")
    trade_side = protocol.side if protocol is not None else _artifact_trade_side(scan.artifact)
    if protocol is not None:
        artifact_side = _declared_artifact_side(scan.artifact)
        if artifact_side is not None and artifact_side != protocol.side:
            raise RuntimeError(
                f"artifact side={artifact_side!r} does not match protocol side={protocol.side!r}"
            )
        artifact_semantics = _artifact_feature_semantics(scan.artifact)
        if artifact_semantics != protocol.feature_semantics:
            raise RuntimeError(
                "artifact feature semantics does not match verified protocol: "
                f"{artifact_semantics!r} != {protocol.feature_semantics!r}"
            )
    resolve = exit_resolver or (
        resolve_forward_exit_short if trade_side == "short" else resolve_forward_exit
    )
    if yolo_mode is None:
        yolo_mode = resolve_yolo_mode("live")
    tip_conf = resolve_tip_conf()
    records: list[ForwardRecord] = []
    scanned_series = 0
    candidates_seen = 0
    threshold_signals_seen = 0
    tracked_keys = open_keys(scan.existing_log)
    # Provenance comes from the protocol object passed by the production loader;
    # never rediscover a global bundle mid-scan. Explicit research scans without
    # a protocol remain observable but execution-ineligible.
    protocol_version = (
        protocol.protocol_version if protocol is not None
        else f"artifact:{Path(scan.artifact.relative_model_path).stem}"
    )
    strategy_id = protocol.strategy_id if protocol is not None else "unbundled_research"
    execution_eligible = bool(protocol.execution_eligible) if protocol is not None else False
    model_sha256 = protocol.model_sha256 if protocol is not None else ""
    detector_sha256 = protocol.detector_sha256 if protocol is not None else ""
    row_threshold = protocol.threshold if protocol is not None else scan.artifact.threshold
    yolo_model = None
    if candidate_source == "yolo":
        try:
            exact_weights = (
                yolo_weights
                if yolo_weights is not None
                else (protocol.detector_path if protocol is not None else None)
            )
            yolo_model = load_yolo_model(exact_weights) if exact_weights is not None else load_yolo_model()
        except (FileNotFoundError, ImportError) as exc:
            # No usable weights on disk (owner_best / v10 / v16 all missing).
            # Idle discovery; open rows still resolve. Owner 2026-07-31: when
            # owner_short_star_v10.pt exists, resolve_default_weights uses it
            # so this branch is only true if even v10 is gone.
            print(f"forward_scan: detector=none ({exc}) — no weights; "
                  "no candidate discovery this pulse", flush=True)
            yolo_model = None

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
    workers = _forward_workers() if candidate_source == "yolo" else 1
    if yolo_weights is not None:
        wlabel = str(yolo_weights)
    else:
        try:
            from src.judgment.yolo_candidates import default_weights_label, resolve_default_weights

            wlabel = default_weights_label(resolve_default_weights())
        except Exception:  # noqa: BLE001
            wlabel = "none"
    tip_conf_s = f"{tip_conf:.2f}" if tip_conf is not None else "off"
    print(
        f"forward_scan: series={scanned_series} workers={workers} source={candidate_source} "
        f"yolo_mode={yolo_mode} tip_conf={tip_conf_s} weights={wlabel}",
        flush=True,
    )
    reset_tip_edge_rejected()

    def _discover(
        job: tuple[str, str, pd.DataFrame]
    ) -> tuple[str, str, pd.DataFrame, pd.DataFrame, list[int], str]:
        """Phase 1 (parallel-safe): indicators + YOLO/rules indices only."""
        source, symbol, frame = job
        enriched = add_indicators(frame)
        if candidate_source == "yolo" and yolo_model is None:
            # detector=none idle mode: no discovery, tracked rows still resolve
            signal_indices: set[int] = set()
        else:
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
        return source, symbol, frame, enriched, sorted(signal_indices), _utc_now_iso()

    t_discover = time.monotonic()
    discovered: list[tuple[str, str, pd.DataFrame, pd.DataFrame, list[int], str]] = []
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
    for source, symbol, frame, enriched, ordered_indices, candidate_detected_at in discovered:
        if not ordered_indices:
            continue
        featured = add_features(enriched)
        feature_semantics = (
            protocol.feature_semantics
            if protocol is not None
            else _artifact_feature_semantics(scan.artifact)
        )
        feature_rows = extract_feature_rows_for_semantics(
            featured,
            ordered_indices,
            feature_semantics=feature_semantics,
            side=trade_side,
        )
        scores = scan.booster.predict(
            feature_rows[FEATURE_COLUMNS], num_iteration=scan.artifact.best_iteration
        )
        candidates_seen += len(ordered_indices)
        for row_pos, signal_i in enumerate(ordered_indices):
            signal_time = pd.Timestamp(enriched["open_time"].iloc[signal_i])
            key = forward_key(source, symbol, signal_time, trade_side, protocol_version)
            tracked_open = key in tracked_keys
            if not tracked_open and signal_time < scan.start_time:
                continue
            score = float(scores[row_pos])
            passes_threshold = (
                protocol.passes_threshold(score)
                if protocol is not None
                else score >= scan.artifact.threshold
            )
            if not tracked_open and not passes_threshold:
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
                # Long: dipped below open → possible buy fill; short: spiked above open.
                o = float(enriched["open"].iloc[entry_i])
                if trade_side == "short":
                    maker_filled = bool(float(enriched["high"].iloc[entry_i]) > o)
                else:
                    maker_filled = bool(float(enriched["low"].iloc[entry_i]) < o)
            # Tiered sizing (owner 2026-07-20): tier is stamped at detection
            # time from the artifact sidecar; artifacts without sizing_tiers
            # (shadow books, stubs) log the legacy 1x.
            tiers = getattr(scan.artifact, "sizing_tiers", None)
            if tiers is not None:
                tier, size_mult = tiers.tier_for_score(score, row_threshold)
            else:
                tier, size_mult = "", 1.0
            records.append(
                {
                    "source": source,
                    "symbol": symbol,
                    "is_stockish": is_stockish(symbol),
                    "signal_time": str(signal_time),
                    "detected_at": candidate_detected_at,
                    "status": exit_state.status,
                    "score": score,
                    "threshold": row_threshold,
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
                    "side": trade_side,
                    "protocol_version": protocol_version,
                    "strategy_id": strategy_id,
                    "feature_semantics": (
                        protocol.feature_semantics
                        if protocol is not None
                        else _artifact_feature_semantics(scan.artifact)
                    ),
                    # Stamped per candidate, not per batch: a scan covering 344
                    # symbols finishes them minutes apart, and sharing the scan's
                    # start time would claim decisions that had not happened yet
                    # (acceptance F-05).
                    "decision_at": _utc_now_iso(),
                    "execution_eligible": execution_eligible,
                    "model_sha256": model_sha256,
                    "detector_sha256": detector_sha256,
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
    """Candidate bars under the validated production/research source contract."""
    candidate_source = validate_candidate_source(CANDIDATE_SOURCE, RUNTIME_MODE)
    if candidate_source == "rules":
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
    return scan_series_with_yolo(
        raw,
        yolo_model,
        start_from_i=start_from_i,
        mode=mode,
        tip_conf=resolve_tip_conf(),
    )


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


def _artifact_feature_semantics(artifact: object) -> str:
    """Which extractor this artifact was trained with. Never inferred from side.

    Absent → legacy_unaligned, matching frozen.py's default and the measured
    reality of every pre-2026-08-03 artifact.
    """
    value = getattr(artifact, "feature_semantics", None)
    return "legacy_unaligned" if value is None else str(value)


def _extract_rows_for_artifact(
    featured: pd.DataFrame,
    signal_indices: list[int],
    artifact: object,
    trade_side: str,
) -> pd.DataFrame:
    """Feature rows in the coordinate system the model was trained in.

    trade_side is accepted but deliberately does NOT select the extractor -- it is
    passed only so a side_aligned_v1 artifact knows which direction to align to.
    """
    return extract_feature_rows_for_semantics(
        featured,
        signal_indices,
        feature_semantics=_artifact_feature_semantics(artifact),
        side=trade_side,
    )


def _artifact_trade_side(artifact: object) -> str:
    """Resolve long|short from frozen artifact config (default long for stubs)."""
    cfg = getattr(artifact, "config", None)
    side = getattr(cfg, "side", None) if cfg is not None else None
    if side is None:
        side = getattr(artifact, "side", None)
    if side is None:
        return "long"
    side_s = str(side).strip().lower()
    if side_s not in {"long", "short"}:
        return "long"
    return side_s


def _declared_artifact_side(artifact: object) -> str | None:
    """Return an explicitly declared artifact side, without a legacy default."""
    cfg = getattr(artifact, "config", None)
    side = getattr(cfg, "side", None) if cfg is not None else getattr(artifact, "side", None)
    if side is None:
        return None
    value = str(side).strip().lower()
    return value if value in {"long", "short"} else None


def resolve_forward_exit(enriched: pd.DataFrame, signal_i: int) -> ForwardExit | None:
    """Long TP5/SL2 partial-horizon resolver (upper=TP, lower=SL)."""
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


def resolve_forward_exit_short(enriched: pd.DataFrame, signal_i: int) -> ForwardExit | None:
    """Short TP5/SL2 partial-horizon resolver.

    Geometry mirrors label_short_candidate / dump short pools:
      TP = entry - TP_MULT*ATR (price fall), SL = entry + SL_MULT*ATR (rally).
    realized_ret uses short conventional PnL ``1 - exit/entry`` (matches
    net_barrier_* builders on the v10 wide/short pools; positive when price falls).
    Intra-bar both-touch → SL (conservative), same as long path.
    """
    entry_i = signal_i + 1
    atr = float(enriched["atr14"].iloc[signal_i])
    atr_pct = float(enriched["atr_pct"].iloc[signal_i])
    if not np.isfinite(atr) or atr <= 0:
        return None
    if not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    if entry_i >= len(enriched):
        return ForwardExit("open", "", -1, 0, "", float("nan"))
    entry = float(enriched["open"].iloc[entry_i])
    if not np.isfinite(entry) or entry <= 0:
        return None
    last_i = entry_i + HORIZON_BARS - 1
    available_last_i = min(last_i, len(enriched) - 1)
    highs = enriched["high"].to_numpy()[entry_i : available_last_i + 1]
    lows = enriched["low"].to_numpy()[entry_i : available_last_i + 1]
    # short: TP below, SL above
    tp = entry - TP_MULT * atr
    sl = entry + SL_MULT * atr
    if tp <= 0:
        return None
    hit_tp = lows <= tp
    hit_sl = highs >= sl
    tp_first = int(np.argmax(hit_tp)) if hit_tp.any() else len(highs)
    sl_first = int(np.argmax(hit_sl)) if hit_sl.any() else len(highs)
    entry_time = pd.Timestamp(enriched["open_time"].iloc[entry_i])
    if tp_first < sl_first:
        exit_offset = tp_first + 1
        return ForwardExit(
            "closed", "tp", 1, exit_offset, _exit_time(entry_time, exit_offset), 1.0 - tp / entry
        )
    if sl_first < tp_first:
        exit_offset = sl_first + 1
        return ForwardExit(
            "closed", "sl", 0, exit_offset, _exit_time(entry_time, exit_offset), 1.0 - sl / entry
        )
    if tp_first == sl_first < len(highs):
        exit_offset = sl_first + 1
        return ForwardExit(
            "closed",
            "sl_ambiguous",
            0,
            exit_offset,
            _exit_time(entry_time, exit_offset),
            1.0 - sl / entry,
        )
    if available_last_i >= last_i:
        exit_px = float(enriched["close"].iloc[last_i])
        return ForwardExit(
            "closed",
            "timeout",
            0,
            HORIZON_BARS,
            _exit_time(entry_time, HORIZON_BARS),
            1.0 - exit_px / entry,
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
