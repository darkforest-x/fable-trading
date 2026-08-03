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
from src.judgment.outcomes import OutcomeContractError, resolve_barrier_outcome
from src.judgment.protocol import StrategyProtocol
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
from yoyo.layers.l1_detection.scan import (
    DiscoveredSeries,
    candidate_indices as _l1_candidate_indices,
    discover as _l1_discover,
    rule_candidate_indices as _rule_candidate_indices,
)
from src.judgment.yolo_candidates import (
    enforce_global_tip_age,
    get_global_tip_age_rejected,
    get_tip_edge_rejected,
    load_yolo_model,
    reset_global_tip_age_rejected,
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
    if exit_resolver is not None:
        resolve = exit_resolver
    elif protocol is not None:
        resolve = lambda frame, index: resolve_forward_exit_for_protocol(  # noqa: E731
            frame, index, protocol
        )
    else:
        resolve = resolve_forward_exit_short if trade_side == "short" else resolve_forward_exit
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
    reset_global_tip_age_rejected()

    # Phase 1 lives in L1 now, thread pool and clock included. Only the boundary
    # moved: the concurrency that was measured against the 15-minute pulse budget
    # is the same code, and discover_wall is still printed from the same two reads.
    discovered_series = _l1_discover(
        jobs,
        candidate_source=candidate_source,
        yolo_model=yolo_model,
        start_time=scan.start_time,
        yolo_mode=yolo_mode,
        max_tip_age_bars=(
            getattr(protocol, "max_tip_age_bars", 2) if protocol is not None else 2
        ),
        tracked_keys=tracked_keys,
        workers=workers,
    )
    discovered = [
        (d.source, d.symbol, d.frame, d.enriched, d.signal_indices, d.detected_at)
        for d in discovered_series
    ]
    t_phase2 = time.monotonic()
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
            # Decision exists only after this candidate's score and threshold
            # comparison have completed. No earlier next-open may become a fill.
            decision_at = _utc_now_iso()
            exit_state = resolve(enriched, signal_i)
            if exit_state is None:
                continue
            threshold_signals_seen += 1
            feature_row = feature_rows.iloc[row_pos]
            # Research convention may still resolve next-bar-open, but those
            # prices/outcomes are never written as actual entry/fill/PnL.
            reference_px = float(enriched["close"].iloc[signal_i])
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
                    "entry_time": "",
                    "entry_price": float("nan"),
                    "maker_filled": None,
                    "outcome": exit_state.outcome,
                    "label": exit_state.label,
                    "exit_offset": exit_state.exit_offset,
                    "exit_time": exit_state.exit_time,
                    "realized_ret": float("nan"),
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
                    "decision_at": decision_at,
                    "execution_eligible": execution_eligible,
                    "model_sha256": model_sha256,
                    "detector_sha256": detector_sha256,
                    "candidate_detected_at": candidate_detected_at,
                    "signal_closed_at": str(signal_time + BAR),
                    "entry_mode": (
                        getattr(protocol, "live_entry_mode", "protocol_unspecified")
                        if protocol is not None else "research_only"
                    ),
                    "entry_status": "not_requested",
                    "entry_requested_at": "",
                    "fill_source": "",
                    "fill_at": "",
                    "fill_px": float("nan"),
                    "reference_px": reference_px,
                    "research_status": exit_state.status,
                    "research_outcome": exit_state.outcome,
                    "research_label": exit_state.label,
                    "research_exit_offset": exit_state.exit_offset,
                    "research_exit_time": exit_state.exit_time,
                    "research_gross_ret": exit_state.realized_ret,
                    "actual_outcome": "",
                    "actual_exit_at": "",
                    "actual_exit_px": float("nan"),
                    "actual_realized_ret": float("nan"),
                    "actual_return_semantics": "",
                    "return_convention": (
                        getattr(
                            protocol,
                            "return_convention",
                            "linear_short" if trade_side == "short" else "linear_long",
                        ) if protocol is not None
                        else ("linear_short" if trade_side == "short" else "linear_long")
                    ),
                    "target_ret_column": (
                        getattr(protocol, "target_ret_column", "")
                        if protocol is not None else ""
                    ),
                    "target_semantics": (
                        getattr(protocol, "target_semantics", "gross")
                        if protocol is not None else "gross"
                    ),
                    "target_cost_included": (
                        getattr(protocol, "target_cost_included", False)
                        if protocol is not None else False
                    ),
                    "reporting_route": (
                        getattr(protocol, "reporting_route", "gross")
                        if protocol is not None else "gross"
                    ),
                }
            )
    print(
        f"forward_scan: phase2_wall={time.monotonic() - t_phase2:.0f}s "
        f"(features+score+resolve, {sum(1 for d in discovered if d[4])} series with candidates)",
        flush=True,
    )
    return ForwardScanResult(records, scanned_series, candidates_seen, threshold_signals_seen)






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
    return _resolve_fixed_forward(
        enriched,
        signal_i,
        side="long",
        tp_mult=TP_MULT,
        sl_mult=SL_MULT,
        horizon=HORIZON_BARS,
        same_bar_policy="conservative_sl",
        gap_policy="barrier_price",
        return_convention="linear_long",
    )


def resolve_forward_exit_short(enriched: pd.DataFrame, signal_i: int) -> ForwardExit | None:
    """Short TP5/SL2 partial-horizon resolver.

    Geometry mirrors label_short_candidate / dump short pools:
      TP = entry - TP_MULT*ATR (price fall), SL = entry + SL_MULT*ATR (rally).
    realized_ret uses short conventional PnL ``1 - exit/entry`` (matches
    net_barrier_* builders on the v10 wide/short pools; positive when price falls).
    Intra-bar both-touch → SL (conservative), same as long path.
    """
    return _resolve_fixed_forward(
        enriched,
        signal_i,
        side="short",
        tp_mult=TP_MULT,
        sl_mult=SL_MULT,
        horizon=HORIZON_BARS,
        same_bar_policy="conservative_sl",
        gap_policy="barrier_price",
        return_convention="linear_short",
    )


def resolve_forward_exit_for_protocol(
    enriched: pd.DataFrame,
    signal_i: int,
    protocol: StrategyProtocol,
) -> ForwardExit | None:
    """Production adapter: every economic input comes from the verified bundle."""
    return _resolve_fixed_forward(
        enriched,
        signal_i,
        side=protocol.side,
        tp_mult=protocol.tp_atr_mult,
        sl_mult=protocol.sl_atr_mult,
        horizon=protocol.horizon_bars,
        same_bar_policy=protocol.same_bar_policy,
        gap_policy=protocol.gap_policy,
        return_convention=protocol.return_convention,
    )


def _resolve_fixed_forward(
    enriched: pd.DataFrame,
    signal_i: int,
    *,
    side: str,
    tp_mult: float,
    sl_mult: float,
    horizon: int,
    same_bar_policy: str,
    gap_policy: str,
    return_convention: str,
) -> ForwardExit | None:
    entry_i = signal_i + 1
    try:
        atr = float(enriched["atr14"].iloc[signal_i])
        atr_pct = float(enriched["atr_pct"].iloc[signal_i])
    except (IndexError, TypeError, ValueError):
        return None
    if not np.isfinite(atr) or atr <= 0:
        return None
    if not np.isfinite(atr_pct) or atr_pct < ATR_PCT_MIN:
        return None
    if entry_i >= len(enriched):
        return ForwardExit("open", "", -1, 0, "", float("nan"))
    entry = float(enriched["open"].iloc[entry_i])
    try:
        resolved = resolve_barrier_outcome(
            enriched,
            side=side,
            entry_i=entry_i,
            entry_price=entry,
            atr=atr,
            tp_atr_mult=tp_mult,
            sl_atr_mult=sl_mult,
            horizon_bars=horizon,
            same_bar_policy=same_bar_policy,
            gap_policy=gap_policy,
            return_convention=return_convention,
            allow_partial=True,
        )
    except OutcomeContractError:
        return None
    if resolved.status == "open":
        return ForwardExit("open", "", -1, 0, "", float("nan"))
    assert resolved.label is not None and resolved.gross_ret is not None
    return ForwardExit(
        "closed",
        resolved.outcome,
        resolved.label,
        resolved.exit_offset,
        str(resolved.exit_time or ""),
        resolved.gross_ret,
    )


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
    reset_global_tip_age_rejected,


def forward_candidate_indices(
    enriched: pd.DataFrame,
    *,
    frame: pd.DataFrame | None = None,
    yolo_model=None,
    start_time: pd.Timestamp | None = None,
    yolo_mode: str = "live",
    max_tip_age_bars: int = 2,
) -> list[int]:
    """Candidate bars under the validated production/research source contract.

    Thin wrapper over yoyo.layers.l1_detection.scan.candidate_indices. The
    provenance check stays on this side because validating production-vs-research
    is a property of assembling a pulse, not of detection; L1 takes the answer as
    an argument rather than consulting the environment itself.
    """
    source = validate_candidate_source(CANDIDATE_SOURCE, RUNTIME_MODE)
    return _l1_candidate_indices(
        enriched,
        candidate_source=source,
        frame=frame,
        yolo_model=yolo_model,
        start_time=start_time,
        yolo_mode=yolo_mode,
        max_tip_age_bars=max_tip_age_bars,
    )
