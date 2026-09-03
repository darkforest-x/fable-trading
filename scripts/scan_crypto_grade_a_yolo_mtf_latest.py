#!/usr/bin/env python3
"""Scan the latest OKX crypto market with the frozen Grade-A YOLO checkpoint.

The scan is deliberately research-only.  The checkpoint was trained on 15-minute
charts, so 1h/4h/1d inference is out-of-distribution; the Owner also rejected the
prior 4h review surface.  This script does not reinterpret those facts.  It runs
the unchanged checkpoint and unchanged causal semantic gate to produce a ranked
human-review queue, never a trade authorization.

The four frozen views are:

* 15m and 1h: the latest fully closed endpoint only;
* 4h and 1d UTC: every fully closed endpoint in the latest 15-day window.

Every model input contains W18/W19 bars and the six SMA/EMA 20/60/120 columns.
The semantic gate reads only ``open/high/low/close`` and those derived moving
averages through the scored endpoint.  Core predicates read the mapped 4/5-bar
core, ATR14 is anchored at ``core_end + 2``, and post3/post5 are evaluated only
when already visible.  No later candle, volume, return, symbol identity, market
ranking, or outcome enters the model or semantic decision.  The 24h ticker data
is retained only as delivery context.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_15m_ma_launch_t3_daily_movers as common  # noqa: E402
from scripts import scan_4h_ma_launch_yolo_latest as base  # noqa: E402
from scripts.scan_15m_ma_launch_model_compare_all3d import (  # noqa: E402
    price_text,
    x_at_float,
)
from src.scout_mtf.tf_scan import fetch_candles  # noqa: E402
from yoyo.datasets.fifteen_minute_launch_candidates import (  # noqa: E402
    add_candidate_features,
)
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402
from yoyo.layers.l1_detection.semantic_gate import (  # noqa: E402
    compute_causal_core_semantics,
    evaluate_causal_semantic_gate,
)


EXPERIMENT_ID = "exp-crypto-grade-a-yolo-mtf-latest-20260903-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results" / "scan"
DEFAULT_RECOVERY_AMENDMENT = (
    ROOT
    / "experiments"
    / "active"
    / EXPERIMENT_ID
    / "recovery_atr_column_20260903.json"
)
PARENT_GATE_PREREG = (
    ROOT
    / "experiments/active/exp-15m-ma-launch-owner-yolo-causal-semantic-gate-v1"
    / "preregistration.json"
)

WEIGHTS = base.WEIGHTS
EXPECTED_WEIGHT_SHA256 = base.EXPECTED_WEIGHT_SHA256
CONFIDENCE = base.CONFIDENCE
NMS_IOU = base.NMS_IOU
IMAGE_SIZE = base.IMAGE_SIZE
WINDOW_LENGTHS = base.WINDOW_LENGTHS
ALLOWED_CORES = base.ALLOWED_CORES
ALLOWED_CONFIRMATIONS = base.ALLOWED_CONFIRMATIONS
EVENT_GAP_BARS = base.EVENT_GAP_BARS
MODEL_NAME = base.MODEL_NAME
MIN_HISTORY_ROWS = 140
FETCH_LIMIT = 300
RECENT_DAYS = 15


@dataclass(frozen=True)
class TimeframeSpec:
    """One immutable market clock and its bounded endpoint policy."""

    key: str
    label: str
    okx_bar: str
    delta_minutes: int
    lookback_endpoints: int
    holdout_number: int
    priority: int

    @property
    def delta(self) -> pd.Timedelta:
        return pd.Timedelta(minutes=self.delta_minutes)


TIMEFRAMES: tuple[TimeframeSpec, ...] = (
    TimeframeSpec("15m", "15m", "15m", 15, 1, 13, 1),
    TimeframeSpec("1h", "1h", "1H", 60, 1, 14, 2),
    TimeframeSpec("4h", "4h", "4H", 240, 90, 15, 3),
    TimeframeSpec("1d", "日线", "1Dutc", 1440, 15, 16, 4),
)
SPEC_BY_KEY = {spec.key: spec for spec in TIMEFRAMES}
TF_ORDER = {spec.key: spec.priority for spec in TIMEFRAMES}

CANVAS_WIDTH = base.CANVAS_WIDTH
CANVAS_HEIGHT = base.CANVAS_HEIGHT
MAIN_X = base.MAIN_X
MAIN_Y = base.MAIN_Y
MAIN_WIDTH = base.MAIN_WIDTH
MAIN_HEIGHT = base.MAIN_HEIGHT
INSET_WIDTH = base.INSET_WIDTH
INSET_HEIGHT = base.INSET_HEIGHT
CONTEXT_BARS = 128


class MultiTimeframeScanError(RuntimeError):
    """Fail closed on source, time, model, semantic, or artifact drift."""


def utc(value: object) -> pd.Timestamp:
    """Normalize one timestamp to UTC."""

    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 identity."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(value: Any) -> str:
    """Serialize artifact metadata without platform-specific whitespace."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a deterministic UTF-8 JSON receipt."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write deterministic JSON Lines."""

    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(stable_json(dict(row)) + "\n")


def latest_closed_open(frozen_at: object, spec: TimeframeSpec) -> pd.Timestamp:
    """Return the latest bar open whose full interval ended by ``frozen_at``."""

    return utc(frozen_at).floor(spec.delta) - spec.delta


def earliest_endpoint_open(frozen_at: object, spec: TimeframeSpec) -> pd.Timestamp:
    """Return the first endpoint included by the immutable lookback count."""

    return latest_closed_open(frozen_at, spec) - (spec.lookback_endpoints - 1) * spec.delta


def load_preregistration(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and enforce the exact no-tuning, no-production scan contract."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise MultiTimeframeScanError("unexpected experiment_id")
    auth = payload.get("owner_authorization") or {}
    if auth.get("holdout_read_authorized") is not True:
        raise MultiTimeframeScanError("latest-market holdout read is not authorized")
    if auth.get("training_or_tuning_authorized") is not False:
        raise MultiTimeframeScanError("training/tuning must remain unauthorized")
    if auth.get("production_or_promotion_authorized") is not False:
        raise MultiTimeframeScanError("production/promotion must remain unauthorized")
    declared = payload.get("timeframes") or {}
    for spec in TIMEFRAMES:
        row = declared.get(spec.key) or {}
        expected = {
            "okx_bar": spec.okx_bar,
            "delta_minutes": spec.delta_minutes,
            "lookback_endpoints": spec.lookback_endpoints,
            "holdout_consumption_number_for_checkpoint": spec.holdout_number,
        }
        for key, value in expected.items():
            if row.get(key) != value:
                raise MultiTimeframeScanError(f"{spec.key} {key} drifted")
    detector = payload.get("detector") or {}
    if detector.get("weights_sha256") != EXPECTED_WEIGHT_SHA256:
        raise MultiTimeframeScanError("weight identity drifted in preregistration")
    if float(detector.get("confidence", -1)) != CONFIDENCE:
        raise MultiTimeframeScanError("confidence drifted")
    if float(detector.get("nms_iou", -1)) != NMS_IOU:
        raise MultiTimeframeScanError("NMS drifted")
    if list(detector.get("window_lengths") or []) != list(WINDOW_LENGTHS):
        raise MultiTimeframeScanError("window contract drifted")
    limitations = payload.get("limitations") or {}
    if limitations.get("prior_4h_owner_verdict") != "都不太行":
        raise MultiTimeframeScanError("prior 4h Owner rejection is not acknowledged")
    if limitations.get("outputs_are_trade_authorizations") is not False:
        raise MultiTimeframeScanError("research outputs cannot become trade authorizations")
    safety = payload.get("safety") or {}
    if any(bool(value) for value in safety.values()):
        raise MultiTimeframeScanError("one or more safety mutation switches are enabled")

    parent = json.loads(PARENT_GATE_PREREG.read_text(encoding="utf-8"))
    gates = dict(parent["treatment"]["frozen_morphology_gate"])
    if gates != dict(payload["semantic_gate"]["frozen_morphology_gate"]):
        raise MultiTimeframeScanError("semantic-gate thresholds differ from parent")
    return payload, gates


def verify_sources_committed(
    prereg_path: Path,
    prereg: Mapping[str, Any],
    recovery_path: Path | None = None,
) -> str:
    """Require main plus committed builder and frozen protocol bytes.

    A recovery amendment may bind one later builder commit after an honestly
    receipted implementation failure.  It cannot change any model, gate,
    timeframe, universe, or ranking contract from the original preregistration.
    """

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise MultiTimeframeScanError("official scan must run on main")
    script = Path(__file__).resolve()
    paths = [script.relative_to(ROOT), prereg_path.resolve().relative_to(ROOT)]
    recovery: Mapping[str, Any] | None = None
    if recovery_path is not None:
        recovery_path = recovery_path.resolve()
        paths.append(recovery_path.relative_to(ROOT))
        recovery = json.loads(recovery_path.read_text(encoding="utf-8"))
        if recovery.get("experiment_id") != EXPERIMENT_ID:
            raise MultiTimeframeScanError("recovery experiment identity drifted")
        if recovery.get("original_prereg_source_commit") != prereg.get("source_commit"):
            raise MultiTimeframeScanError("recovery does not bind the original preregistration")
        if recovery.get("change_scope") != "add_missing_inherited_pine_rma_atr14_column_only":
            raise MultiTimeframeScanError("recovery change scope is not the frozen ATR repair")
        if recovery.get("model_gate_timeframe_universe_ranking_changed") is not False:
            raise MultiTimeframeScanError("recovery must preserve every analytical contract")
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise MultiTimeframeScanError(f"scan sources must be committed:\n{dirty}")
    source_commit = subprocess.check_output(
        ["git", "log", "-1", "--format=%H", "--", str(paths[0])], cwd=ROOT, text=True
    ).strip()
    expected_commit = (
        str(recovery.get("corrected_builder_commit"))
        if recovery is not None
        else str(prereg.get("source_commit"))
    )
    if source_commit != expected_commit:
        raise MultiTimeframeScanError(
            f"builder commit {source_commit} differs from frozen source binding"
        )
    return source_commit


def ticker_context(
    ticker_rows: Sequence[Mapping[str, Any]], eligible: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Build delivery-only 24h ticker context for the frozen eligible universe."""

    raw_by_id = {str(row.get("instId") or ""): row for row in ticker_rows}
    context: dict[str, dict[str, Any]] = {}
    for inst_id in eligible:
        record = raw_by_id.get(inst_id)
        if record is None:
            raise MultiTimeframeScanError(f"eligible ticker context missing: {inst_id}")
        try:
            last = float(record.get("last") or 0)
            open_24h = float(record.get("open24h") or 0)
            base_volume = float(record.get("volCcy24h") or 0)
        except (TypeError, ValueError) as exc:
            raise MultiTimeframeScanError(
                f"invalid ticker context: {inst_id}"
            ) from exc
        if last <= 0 or open_24h <= 0 or base_volume < 0:
            raise MultiTimeframeScanError(f"non-positive ticker context: {inst_id}")
        change = (last / open_24h - 1.0) * 100.0
        volume = base_volume * last
        base_name = inst_id.split("-", 1)[0]
        symbol = inst_id.replace("-", "_")
        context[symbol] = {
            "symbol": symbol,
            "inst_id": inst_id,
            "base": base_name,
            "last": float(last),
            "change_24h_pct": float(change),
            "quote_volume_24h_usdt": float(volume),
        }
    return context


def fetch_one(
    symbol: str,
    inst_id: str,
    spec: TimeframeSpec,
    frozen_at: pd.Timestamp,
) -> tuple[str, str, pd.DataFrame, str | None]:
    """Fetch one bounded, fully closed, contiguous OKX candle page."""

    expected_latest = latest_closed_open(frozen_at, spec)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            frame = fetch_candles(inst_id, spec.okx_bar, FETCH_LIMIT).copy()
            if frame.empty:
                raise MultiTimeframeScanError("empty confirmed candle page")
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            frame = frame.loc[frame["open_time"] <= expected_latest].copy()
            frame = frame.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
            if len(frame) < MIN_HISTORY_ROWS:
                raise MultiTimeframeScanError(f"only {len(frame)} closed rows")
            if utc(frame.iloc[-1]["open_time"]) != expected_latest:
                raise MultiTimeframeScanError(
                    f"stale latest bar {frame.iloc[-1]['open_time']} expected {expected_latest}"
                )
            diffs = frame["open_time"].diff().iloc[1:]
            gaps = int((diffs != spec.delta).sum())
            if gaps:
                raise MultiTimeframeScanError(f"non-contiguous bars: {gaps} gaps")
            numeric = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
            if not np.isfinite(numeric).all():
                raise MultiTimeframeScanError("non-finite OHLCV")
            if (frame[["open", "high", "low", "close"]] <= 0).any().any():
                raise MultiTimeframeScanError("non-positive OHLC")
            return spec.key, symbol, frame, None
        except Exception as exc:  # noqa: BLE001 - every exclusion is receipted
            last_error = exc
            time.sleep(0.6 * (attempt + 1))
    return spec.key, symbol, pd.DataFrame(), f"{type(last_error).__name__}:{last_error}"


def fetch_market(
    universe: Mapping[str, Mapping[str, Any]],
    *,
    frozen_at: pd.Timestamp,
    candle_root: Path,
    workers: int,
) -> tuple[
    dict[str, dict[str, pd.DataFrame]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, str]]],
]:
    """Fetch all four timeframe pages with bounded concurrency and receipts."""

    frames: dict[str, dict[str, pd.DataFrame]] = {spec.key: {} for spec in TIMEFRAMES}
    audits: dict[str, list[dict[str, Any]]] = {spec.key: [] for spec in TIMEFRAMES}
    failures: dict[str, list[dict[str, str]]] = {spec.key: [] for spec in TIMEFRAMES}
    jobs = [
        (spec, symbol, str(meta["inst_id"]))
        for spec in TIMEFRAMES
        for symbol, meta in sorted(universe.items())
    ]
    for spec in TIMEFRAMES:
        (candle_root / spec.key).mkdir(parents=True)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {
            executor.submit(fetch_one, symbol, inst_id, spec, frozen_at): (spec, symbol)
            for spec, symbol, inst_id in jobs
        }
        for done, future in enumerate(as_completed(future_map), 1):
            key, symbol, frame, error = future.result()
            spec = SPEC_BY_KEY[key]
            if error is not None:
                failures[key].append({"symbol": symbol, "error": error})
            else:
                path = candle_root / key / f"{symbol}.csv"
                frame.to_csv(path, index=False)
                frames[key][symbol] = frame
                audits[key].append(
                    {
                        "symbol": symbol,
                        "rows": len(frame),
                        "first_bar_open": utc(frame.iloc[0]["open_time"]).isoformat(),
                        "last_bar_open": utc(frame.iloc[-1]["open_time"]).isoformat(),
                        "last_bar_available_at": (
                            utc(frame.iloc[-1]["open_time"]) + spec.delta
                        ).isoformat(),
                        "sha256": sha256_file(path),
                    }
                )
            if done % 25 == 0 or done == len(jobs):
                print(
                    f"fetch {done:04d}/{len(jobs)} {key:<3} {symbol:<24} "
                    f"rows={len(frame):>3} {'OK' if error is None else 'FAIL'}",
                    flush=True,
                )
    for key in frames:
        audits[key].sort(key=lambda row: str(row["symbol"]))
        failures[key].sort(key=lambda row: str(row["symbol"]))
    return frames, audits, failures


def enrich_model_frames(
    frames: Mapping[str, pd.DataFrame],
) -> dict[str, pd.DataFrame]:
    """Add the parent gate's exact causal ATR14 before model-task creation.

    ``base.build_tasks`` supplies the renderer's six moving averages but not
    the inherited Pine-RMA ATR14 required by the frozen semantic gate.  The
    parent gate used :func:`add_candidate_features`, so this adapter reuses
    that exact implementation.  ``base.build_tasks`` subsequently recomputes
    the same renderer MA columns while preserving ``atr`` unchanged.
    """

    enriched = {
        symbol: add_candidate_features(frame)
        for symbol, frame in sorted(frames.items())
    }
    for symbol, frame in enriched.items():
        if "atr" not in frame.columns:
            raise MultiTimeframeScanError(f"ATR enrichment missing for {symbol}")
        finite = pd.to_numeric(frame["atr"], errors="coerce").iloc[MIN_HISTORY_ROWS - 1 :]
        if finite.empty or not bool(np.isfinite(finite.to_numpy(dtype=float)).all()):
            raise MultiTimeframeScanError(f"ATR enrichment is non-finite for {symbol}")
    return enriched


def load_frozen_market(
    source: Path,
    *,
    candle_root: Path,
) -> tuple[
    pd.Timestamp,
    dict[str, dict[str, Any]],
    dict[str, dict[str, pd.DataFrame]],
    dict[str, list[dict[str, Any]]],
    dict[str, list[dict[str, str]]],
]:
    """Recover the originally frozen candles without another market read."""

    source = source.resolve()
    start_path = source / "holdout_consumption_started.json"
    universe_path = source / "universe.json"
    failure_path = source / "failure_receipt.json"
    for path in (start_path, universe_path, failure_path):
        if not path.is_file():
            raise MultiTimeframeScanError(f"recovery source is missing {path.name}")
    started = json.loads(start_path.read_text(encoding="utf-8"))
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    if started.get("experiment_id") != EXPERIMENT_ID or failure.get("experiment_id") != EXPERIMENT_ID:
        raise MultiTimeframeScanError("recovery source experiment identity drifted")
    expected_numbers = {spec.key: spec.holdout_number for spec in TIMEFRAMES}
    if started.get("holdout_consumption_numbers_for_checkpoint") != expected_numbers:
        raise MultiTimeframeScanError("recovery holdout numbers drifted")
    if "missing columns: ['atr']" not in str(failure.get("error")):
        raise MultiTimeframeScanError("recovery source is not the receipted ATR failure")
    frozen_at = utc(started["started_at"])
    for spec in TIMEFRAMES:
        declared = started["scope"][spec.key]
        if declared.get("latest_closed_open") != latest_closed_open(frozen_at, spec).isoformat():
            raise MultiTimeframeScanError(f"{spec.key} recovery endpoint drifted")
        if declared.get("earliest_endpoint_open") != earliest_endpoint_open(frozen_at, spec).isoformat():
            raise MultiTimeframeScanError(f"{spec.key} recovery lookback drifted")

    universe_payload = json.loads(universe_path.read_text(encoding="utf-8"))
    if utc(universe_payload["frozen_at"]) != frozen_at:
        raise MultiTimeframeScanError("recovery universe timestamp drifted")
    universe = {
        str(row["symbol"]): dict(row) for row in list(universe_payload.get("symbols") or [])
    }
    if not universe or len(universe) != len(universe_payload.get("symbols") or []):
        raise MultiTimeframeScanError("recovery universe is empty or duplicated")

    frames: dict[str, dict[str, pd.DataFrame]] = {spec.key: {} for spec in TIMEFRAMES}
    audits: dict[str, list[dict[str, Any]]] = {spec.key: [] for spec in TIMEFRAMES}
    failures: dict[str, list[dict[str, str]]] = {spec.key: [] for spec in TIMEFRAMES}
    for spec in TIMEFRAMES:
        destination_dir = candle_root / spec.key
        destination_dir.mkdir(parents=True)
        for symbol in sorted(universe):
            source_path = source / "candles" / spec.key / f"{symbol}.csv"
            if not source_path.is_file():
                failures[spec.key].append(
                    {"symbol": symbol, "error": "preserved_initial_fetch_failure"}
                )
                continue
            destination = destination_dir / source_path.name
            shutil.copy2(source_path, destination)
            frame = pd.read_csv(destination)
            frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
            if len(frame) < MIN_HISTORY_ROWS:
                raise MultiTimeframeScanError(f"short recovered history: {symbol} {spec.key}")
            if utc(frame.iloc[-1]["open_time"]) != latest_closed_open(frozen_at, spec):
                raise MultiTimeframeScanError(f"stale recovered endpoint: {symbol} {spec.key}")
            diffs = frame["open_time"].diff().iloc[1:]
            if not bool((diffs == spec.delta).all()):
                raise MultiTimeframeScanError(f"recovered candle gaps: {symbol} {spec.key}")
            numeric = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
            if not bool(np.isfinite(numeric).all()):
                raise MultiTimeframeScanError(f"non-finite recovered OHLCV: {symbol} {spec.key}")
            if bool((frame[["open", "high", "low", "close"]] <= 0).any().any()):
                raise MultiTimeframeScanError(f"non-positive recovered OHLC: {symbol} {spec.key}")
            frames[spec.key][symbol] = frame
            audits[spec.key].append(
                {
                    "symbol": symbol,
                    "rows": len(frame),
                    "first_bar_open": utc(frame.iloc[0]["open_time"]).isoformat(),
                    "last_bar_open": utc(frame.iloc[-1]["open_time"]).isoformat(),
                    "last_bar_available_at": (utc(frame.iloc[-1]["open_time"]) + spec.delta).isoformat(),
                    "sha256": sha256_file(destination),
                }
            )
    return frozen_at, universe, frames, audits, failures


def evaluate_semantic_candidates(
    candidates: Sequence[Mapping[str, Any]],
    frames: Mapping[str, pd.DataFrame],
    gates: Mapping[str, Any],
    *,
    timeframe: str,
) -> list[dict[str, Any]]:
    """Replay pixels and apply the frozen causal gate to every structural box."""

    output: list[dict[str, Any]] = []
    for index, source in enumerate(candidates, 1):
        row = dict(source)
        row["candidate_id"] = f"{timeframe}_structural_{index:06d}"
        row["timeframe"] = timeframe
        frame = frames[str(row["symbol"])]
        start = int(row["window_start_i"])
        observed = int(row["window_end_i"])
        core_start = int(row["core_start_i"])
        core_end = int(row["core_end_i"])
        if not 0 <= start <= core_start <= core_end + 2 <= observed < len(frame):
            raise MultiTimeframeScanError(f"invalid causal indices: {row['candidate_id']}")
        exact_input = frame.iloc[start : observed + 1]
        replay, _ = render_chart(exact_input, out_path=None)
        replay_hash = base.pixel_sha256(replay)
        if replay_hash != str(row["input_pixel_sha256"]):
            raise MultiTimeframeScanError(f"input pixel drift: {row['candidate_id']}")

        causal = frame.iloc[: observed + 1]
        direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
        flipped = "SHORT" if direction == "LONG" else "LONG"
        features = compute_causal_core_semantics(
            causal,
            core_start_i=core_start,
            core_end_i=core_end,
            observed_end_i=observed,
            direction=direction,
        )
        decision = evaluate_causal_semantic_gate(features, gates)
        flipped_features = compute_causal_core_semantics(
            causal,
            core_start_i=core_start,
            core_end_i=core_end,
            observed_end_i=observed,
            direction=flipped,
        )
        flipped_decision = evaluate_causal_semantic_gate(flipped_features, gates)
        row.update(
            {
                "semantic_gate_pass": bool(decision.passed),
                "semantic_checks": dict(decision.checks),
                "semantic_failed_checks": list(decision.failed_checks),
                "semantic_features": features.to_dict(),
                "flipped_semantic_gate_pass": bool(flipped_decision.passed),
                "flipped_semantic_checks": dict(flipped_decision.checks),
                "flipped_semantic_failed_checks": list(flipped_decision.failed_checks),
                "flipped_semantic_features": flipped_features.to_dict(),
                "causal_feature_last_i": observed,
                "input_pixel_replay_sha256": replay_hash,
            }
        )
        output.append(row)
        if index % 250 == 0 or index == len(candidates):
            print(f"semantic {timeframe} {index}/{len(candidates)}", flush=True)
    return output


def flatten_semantic_candidate(row: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten nested semantic metadata for stable CSV inspection."""

    flat = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "semantic_checks",
            "semantic_failed_checks",
            "semantic_features",
            "flipped_semantic_checks",
            "flipped_semantic_failed_checks",
            "flipped_semantic_features",
        }
    }
    flat["semantic_failed_checks"] = "|".join(map(str, row["semantic_failed_checks"]))
    flat["flipped_semantic_failed_checks"] = "|".join(
        map(str, row["flipped_semantic_failed_checks"])
    )
    for key, value in dict(row["semantic_features"]).items():
        flat[f"semantic_{key}"] = value
    return flat


def deduplicate_events(
    candidates: Sequence[Mapping[str, Any]],
    *,
    spec: TimeframeSpec,
    frames: Mapping[str, pd.DataFrame],
    frozen_at: pd.Timestamp,
) -> list[dict[str, Any]]:
    """Collapse W18/W19 and adjacent endpoints using the frozen five-bar rule."""

    by_symbol: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_symbol[str(candidate["symbol"])].append(dict(candidate))
    events: list[dict[str, Any]] = []
    for symbol, rows in sorted(by_symbol.items()):
        peaks = common.deduplicate_hits(rows, gap_bars=EVENT_GAP_BARS)
        for peak in peaks:
            related = [
                row
                for row in rows
                if abs(int(row["core_end_i"]) - int(peak["core_end_i"])) < EVENT_GAP_BARS
            ]
            first_open = min(utc(row["window_end_time"]) for row in related)
            last_open = max(utc(row["window_end_time"]) for row in related)
            latest = max(
                (row for row in related if utc(row["window_end_time"]) == last_open),
                key=lambda row: float(row["confidence"]),
            )
            event = dict(latest)
            event.update(
                {
                    "timeframe": spec.key,
                    "timeframe_label": spec.label,
                    "bar_delta_minutes": spec.delta_minutes,
                    "first_detection_bar_open_time": first_open.isoformat(),
                    "last_detection_bar_open_time": last_open.isoformat(),
                    "first_available_at": (first_open + spec.delta).isoformat(),
                    "last_available_at": (last_open + spec.delta).isoformat(),
                    "event_peak_confidence": float(peak["confidence"]),
                    "event_peak_bar_open_time": utc(peak["window_end_time"]).isoformat(),
                    "event_peak_available_at": (
                        utc(peak["window_end_time"]) + spec.delta
                    ).isoformat(),
                    "classes_observed": sorted({str(row["class_name"]) for row in related}),
                    "candidate_count": len(related),
                    "representative_rule": (
                        "latest_detection_endpoint_then_highest_confidence; "
                        "event peak retained separately"
                    ),
                    "semantic_gate_applied": True,
                    "semantic_gate_pass": True,
                    "holdout_consumption_number_for_checkpoint": spec.holdout_number,
                }
            )
            latest_market_open = utc(frames[symbol].iloc[-1]["open_time"])
            event["latest_market_bar_open_time"] = latest_market_open.isoformat()
            event["latest_market_bar_available_at"] = (
                latest_market_open + spec.delta
            ).isoformat()
            event["is_current_latest_bar"] = utc(event["window_end_time"]) == latest_market_open
            event["age_hours_at_frozen"] = max(
                0.0,
                float((frozen_at - utc(event["first_available_at"])) / pd.Timedelta(hours=1)),
            )
            events.append(event)

    events.sort(
        key=lambda row: (
            utc(row["first_available_at"]),
            float(row["confidence"]),
            str(row["symbol"]),
        ),
        reverse=True,
    )
    first_allowed = earliest_endpoint_open(frozen_at, spec) + spec.delta
    for sequence, event in enumerate(events, 1):
        event["event_id"] = (
            f"{spec.key}_yolo_{sequence:04d}_"
            f"{str(event['symbol']).replace('_USDT_SWAP', '')}"
        )
        if utc(event["first_available_at"]) < first_allowed:
            raise MultiTimeframeScanError(
                f"{event['event_id']} begins before frozen lookback: {first_allowed}"
            )
    return events


def rank_events(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Create a deterministic review order without inventing a cross-TF score.

    Confidence is ranked only within each timeframe.  Overall review order uses
    same-direction timeframe overlap, total timeframe overlap, timeframe level,
    within-timeframe confidence rank, then recency.  It is not a probability or
    economic ranking.
    """

    ranked = [dict(row) for row in events]
    by_tf: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    by_symbol: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ranked:
        by_tf[str(row["timeframe"])].append(row)
        by_symbol[str(row["symbol"])].append(row)

    for timeframe, rows in by_tf.items():
        ordered = sorted(
            rows,
            key=lambda row: (
                -float(row["confidence"]),
                -utc(row["first_available_at"]).value,
                str(row["symbol"]),
                str(row["event_id"]),
            ),
        )
        count = len(ordered)
        for index, row in enumerate(ordered, 1):
            row["confidence_rank_within_timeframe"] = index
            row["events_in_timeframe"] = count
            row["confidence_percentile_within_timeframe"] = (
                1.0 if count == 1 else 1.0 - (index - 1) / (count - 1)
            )

    for symbol, rows in by_symbol.items():
        timeframes = sorted({str(row["timeframe"]) for row in rows}, key=TF_ORDER.get)
        sides = sorted({str(row["class_name"]) for row in rows})
        for row in rows:
            same_side_tfs = sorted(
                {
                    str(other["timeframe"])
                    for other in rows
                    if str(other["class_name"]) == str(row["class_name"])
                },
                key=TF_ORDER.get,
            )
            row["symbol_timeframes"] = timeframes
            row["symbol_timeframe_count"] = len(timeframes)
            row["same_side_timeframes"] = same_side_tfs
            row["same_side_timeframe_count"] = len(same_side_tfs)
            row["direction_conflict_for_symbol"] = len(sides) > 1

    ranked.sort(
        key=lambda row: (
            -int(row["same_side_timeframe_count"]),
            -int(row["symbol_timeframe_count"]),
            -TF_ORDER[str(row["timeframe"])],
            int(row["confidence_rank_within_timeframe"]),
            -utc(row["first_available_at"]).value,
            str(row["symbol"]),
        )
    )
    for index, row in enumerate(ranked, 1):
        row["review_rank"] = index
        row["review_rank_policy"] = (
            "same-side timeframe count desc; total timeframe count desc; "
            "1d>4h>1h>15m; within-timeframe confidence rank; recency"
        )
    return ranked


def _put_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    *,
    scale: float = 0.50,
    color: tuple[int, int, int] = (30, 30, 30),
    thickness: int = 1,
) -> None:
    cv2.putText(
        image,
        text,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def _dashed_vertical(image: np.ndarray, x: int, y0: int, y1: int) -> None:
    for start in range(y0, y1 + 1, 20):
        cv2.line(image, (x, start), (x, min(y1, start + 12)), (35, 35, 35), 2)


def delivery_context_start(*, frame_length: int, window_start_i: int) -> int:
    """Keep the scored W18/W19 visible and retain current-market context."""

    if not 0 <= window_start_i < frame_length:
        raise ValueError("window_start_i is outside frame")
    return min(max(0, frame_length - CONTEXT_BARS), window_start_i)


def render_event(
    row: Mapping[str, Any],
    *,
    frame: pd.DataFrame,
    total: int,
) -> np.ndarray:
    """Render the full review chart and exact W18/W19 model input."""

    spec = SPEC_BY_KEY[str(row["timeframe"])]
    start_i = int(row["window_start_i"])
    end_i = int(row["window_end_i"])
    model_window = frame.iloc[start_i : end_i + 1]
    clean, input_tf = render_chart(model_window, out_path=None)
    if base.pixel_sha256(clean) != str(row["input_pixel_sha256"]):
        raise MultiTimeframeScanError(f"model input replay drift: {row['event_id']}")
    overlay = clean.copy()
    raw_x0, raw_y0, raw_x1, raw_y1 = base.normalized_box_corners(row)
    cv2.rectangle(
        overlay,
        (raw_x0, raw_y0),
        (raw_x1, raw_y1),
        common.CLASS_COLORS[int(row["class_id"])],
        4,
        cv2.LINE_AA,
    )

    context_start_i = delivery_context_start(
        frame_length=len(frame), window_start_i=start_i
    )
    context = frame.iloc[context_start_i:]
    main, context_tf = render_chart(
        context, width=MAIN_WIDTH, height=MAIN_HEIGHT, out_path=None
    )
    x0, y0, x1, y1 = base.project_raw_box(
        row,
        input_tf=input_tf,
        context_tf=context_tf,
        context_start_i=context_start_i,
    )
    cv2.rectangle(
        main,
        (x0, y0),
        (x1, y1),
        common.CLASS_COLORS[int(row["class_id"])],
        5,
        cv2.LINE_AA,
    )
    local_detect = end_i - context_start_i
    detect_x = x_at_float(context_tf, local_detect)
    if local_detect < len(context) - 1:
        shaded = main.copy()
        cv2.rectangle(
            shaded,
            (detect_x + 1, 0),
            (MAIN_WIDTH - 1, MAIN_HEIGHT - 1),
            (228, 231, 235),
            -1,
        )
        main = cv2.addWeighted(shaded, 0.25, main, 0.75, 0)
    _dashed_vertical(main, detect_x, 8, MAIN_HEIGHT - 15)
    _put_text(main, "DETECT", (max(4, detect_x - 32), 27), scale=0.48, thickness=2)

    canvas = np.full((CANVAS_HEIGHT, CANVAS_WIDTH, 3), 247, dtype=np.uint8)
    side = "LONG" if int(row["class_id"]) == 0 else "SHORT"
    symbol = str(row["symbol"]).replace("_USDT_SWAP", "")
    available = utc(row["first_available_at"]).tz_convert("Asia/Shanghai")
    core_start = utc(row["core_start_time"]).tz_convert("Asia/Shanghai")
    core_end = utc(row["core_end_time"]).tz_convert("Asia/Shanghai")
    status = "CURRENT" if bool(row["is_current_latest_bar"]) else "RECENT"
    _put_text(
        canvas,
        (
            f"#{int(row['review_rank']):03d}/{total:03d}  {symbol}USDT.P  {spec.label}  "
            f"{status} {side}  conf {float(row['confidence']):.3f}  "
            f"TF-rank {int(row['confidence_rank_within_timeframe'])}/"
            f"{int(row['events_in_timeframe'])}"
        ),
        (24, 38),
        scale=0.69,
        thickness=2,
    )
    _put_text(
        canvas,
        (
            f"available CST {available:%Y-%m-%d %H:%M} | core {core_start:%m-%d %H:%M}.."
            f"{core_end:%m-%d %H:%M} | same-side TF {int(row['same_side_timeframe_count'])} "
            f"({','.join(row['same_side_timeframes'])}) | "
            f"direction conflict {'YES' if row['direction_conflict_for_symbol'] else 'NO'}"
        ),
        (24, 72),
        scale=0.47,
        color=(55, 55, 55),
    )
    _put_text(
        canvas,
        (
            f"Frozen {MODEL_NAME} | W{int(row['window_len'])} core"
            f"{int(row['core_length_bars'])} post{int(row['confirmation_bars'])} | "
            f"MA envelope {float(row['semantic_ma_envelope_atr']):.2f} ATR | "
            f"post2 {float(row['semantic_post2_progress_atr']):+.2f} ATR"
        ),
        (24, 102),
        scale=0.45,
        color=(75, 75, 75),
    )
    canvas[MAIN_Y : MAIN_Y + MAIN_HEIGHT, MAIN_X : MAIN_X + MAIN_WIDTH] = main

    times = pd.to_datetime(context["open_time"], utc=True).dt.tz_convert("Asia/Shanghai")
    times = times.reset_index(drop=True)
    for local_i in np.linspace(0, len(context) - 1, 6).round().astype(int):
        x = MAIN_X + x_at_float(context_tf, int(local_i))
        stamp = times.iloc[int(local_i)]
        _put_text(
            canvas,
            f"{stamp:%m-%d %H:%M}",
            (max(0, x - 48), MAIN_Y + MAIN_HEIGHT + 24),
            scale=0.40,
            color=(80, 80, 80),
        )
    for fraction in np.linspace(0.08, 0.92, 5):
        price = context_tf.price_max - fraction * (context_tf.price_max - context_tf.price_min)
        y = MAIN_Y + int(round(context_tf.top + fraction * context_tf.plot_h))
        _put_text(
            canvas,
            price_text(price),
            (CANVAS_WIDTH - 118, y),
            scale=0.40,
            color=(75, 75, 75),
        )

    footer_y = 926
    _put_text(canvas, "HOW TO READ", (28, footer_y), scale=0.64, thickness=2)
    _put_text(
        canvas,
        "Top: completed candles through the frozen market tip. Rectangle is the unchanged YOLO box; DETECT is its right edge.",
        (28, footer_y + 34),
        scale=0.43,
    )
    domain_note = (
        "Native 15m model clock."
        if spec.key == "15m"
        else f"OOD {spec.label}: the checkpoint was trained only on 15m charts."
    )
    _put_text(canvas, domain_note, (28, footer_y + 64), scale=0.43)
    rejection_note = (
        "Prior 4h Owner review was rejected ('都不太行'); this new snapshot does not reverse that verdict."
        if spec.key == "4h"
        else "Confidence ranks review order within this timeframe; it is not win probability."
    )
    _put_text(
        canvas,
        rejection_note,
        (28, footer_y + 94),
        scale=0.43,
        color=(45, 45, 180),
        thickness=2,
    )
    _put_text(
        canvas,
        "RESEARCH CANDIDATE ONLY — NOT ACTIVE / NOT A TRADE AUTHORIZATION",
        (28, footer_y + 124),
        scale=0.45,
        color=(45, 45, 180),
        thickness=2,
    )
    _put_text(
        canvas,
        "EXACT MODEL INPUT",
        (CANVAS_WIDTH - INSET_WIDTH - 18, footer_y),
        scale=0.60,
        thickness=2,
    )
    inset = cv2.resize(overlay, (INSET_WIDTH, INSET_HEIGHT), interpolation=cv2.INTER_AREA)
    inset_x, inset_y = CANVAS_WIDTH - INSET_WIDTH - 18, footer_y + 18
    canvas[inset_y : inset_y + INSET_HEIGHT, inset_x : inset_x + INSET_WIDTH] = inset
    cv2.rectangle(
        canvas,
        (inset_x, inset_y),
        (inset_x + INSET_WIDTH - 1, inset_y + INSET_HEIGHT - 1),
        (65, 65, 65),
        2,
    )
    return canvas


def build_contact_sheets(
    events: Sequence[Mapping[str, Any]], chart_root: Path, out: Path
) -> list[Path]:
    """Render combined and per-timeframe ranked overview pages."""

    pages: list[Path] = []
    groups: list[tuple[str, list[Mapping[str, Any]]]] = [("all", list(events))]
    groups.extend(
        (spec.key, [row for row in events if row["timeframe"] == spec.key])
        for spec in TIMEFRAMES
    )
    for group_name, rows in groups:
        group_dir = out / f"overview_{group_name}"
        group_dir.mkdir()
        if not rows:
            blank = np.full((720, 1280, 3), 247, dtype=np.uint8)
            _put_text(blank, f"{group_name}: zero semantic-gate research candidates", (120, 350), scale=0.8, thickness=2)
            path = group_dir / "page_01.png"
            cv2.imwrite(str(path), blank)
            pages.append(path)
            continue
        for page_number, start in enumerate(range(0, len(rows), 9), 1):
            subset = rows[start : start + 9]
            thumb_w, thumb_h = 620, 426
            sheet = np.full((3 * thumb_h + 82, 3 * thumb_w, 3), 240, dtype=np.uint8)
            _put_text(
                sheet,
                f"Ranked multi-timeframe research candidates | {group_name} | page {page_number}",
                (24, 34),
                scale=0.72,
                thickness=2,
            )
            _put_text(
                sheet,
                "Semantic-gate survivors only; within-TF confidence; OOD higher timeframes; not trade signals",
                (24, 66),
                scale=0.46,
                color=(45, 45, 180),
                thickness=2,
            )
            for slot, event in enumerate(subset):
                path = chart_root / str(event["chart"])
                image = cv2.imread(str(path))
                if image is None:
                    raise MultiTimeframeScanError(f"could not read chart: {path}")
                thumb = cv2.resize(image, (thumb_w, thumb_h), interpolation=cv2.INTER_AREA)
                row_i, col_i = divmod(slot, 3)
                y, x = 82 + row_i * thumb_h, col_i * thumb_w
                sheet[y : y + thumb_h, x : x + thumb_w] = thumb
                label = (
                    f"#{int(event['review_rank']):03d} "
                    f"{str(event['symbol']).replace('_USDT_SWAP', '')} "
                    f"{event['timeframe']} "
                    f"{'L' if int(event['class_id']) == 0 else 'S'} "
                    f"{float(event['confidence']):.3f}"
                )
                cv2.rectangle(sheet, (x + 4, y + 4), (x + 300, y + 31), (250, 250, 250), -1)
                _put_text(sheet, label, (x + 10, y + 25), scale=0.52, thickness=2)
            page = group_dir / f"page_{page_number:02d}.png"
            cv2.imwrite(str(page), sheet, [cv2.IMWRITE_PNG_COMPRESSION, 3])
            pages.append(page)
        shutil.copy2(group_dir / "page_01.png", out / f"overview_{group_name}.png")
    return pages


def build_summary_figure(
    events: Sequence[Mapping[str, Any]],
    stats: Mapping[str, Mapping[str, Any]],
    path: Path,
) -> None:
    """Render event counts, gate survival, and the first review ranks."""

    event_frame = pd.DataFrame(events)
    labels = [spec.label for spec in TIMEFRAMES]
    long_counts = [
        sum(row["timeframe"] == spec.key and int(row["class_id"]) == 0 for row in events)
        for spec in TIMEFRAMES
    ]
    short_counts = [
        sum(row["timeframe"] == spec.key and int(row["class_id"]) == 1 for row in events)
        for spec in TIMEFRAMES
    ]
    structural = [int(stats[spec.key]["accepted_structural_boxes"]) for spec in TIMEFRAMES]
    semantic = [int(stats[spec.key]["semantic_pass_boxes"]) for spec in TIMEFRAMES]
    rates = [100.0 * passed / total if total else 0.0 for passed, total in zip(semantic, structural)]

    plt.style.use("seaborn-v0_8-whitegrid")
    fig = plt.figure(figsize=(17, 11), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, height_ratios=(0.88, 1.12))
    ax_count = fig.add_subplot(grid[0, 0])
    x = np.arange(len(labels))
    ax_count.bar(x, long_counts, color="#2f6f9f", label="LONG")
    ax_count.bar(x, short_counts, bottom=long_counts, color="#d8a24a", label="SHORT")
    ax_count.set_xticks(x, labels)
    ax_count.set_ylabel("deduplicated semantic events")
    ax_count.set_title("Research candidates by timeframe", loc="left", weight="bold")
    ax_count.legend(frameon=False, ncol=2)
    for index, total in enumerate(np.asarray(long_counts) + np.asarray(short_counts)):
        ax_count.text(index, float(total) + 0.15, str(int(total)), ha="center", fontsize=9)

    ax_gate = fig.add_subplot(grid[0, 1])
    bars = ax_gate.bar(x, rates, color="#476f95")
    ax_gate.set_xticks(x, labels)
    ax_gate.set_ylim(0, max(10.0, max(rates, default=0.0) * 1.25))
    ax_gate.set_ylabel("semantic survival of structural boxes (%)")
    ax_gate.set_title("Frozen semantic-gate survival", loc="left", weight="bold")
    for rect, rate, passed, total in zip(bars, rates, semantic, structural):
        ax_gate.text(
            rect.get_x() + rect.get_width() / 2,
            rect.get_height() + 0.25,
            f"{rate:.1f}%\n{passed}/{total}",
            ha="center",
            va="bottom",
            fontsize=8,
        )

    ax_rank = fig.add_subplot(grid[1, :])
    top = event_frame.head(20).iloc[::-1] if not event_frame.empty else event_frame
    if top.empty:
        ax_rank.text(0.5, 0.5, "No semantic-gate research candidates", ha="center", va="center")
        ax_rank.set_axis_off()
    else:
        names = [
            f"#{int(row.review_rank):03d} {str(row.symbol).replace('_USDT_SWAP', '')} "
            f"{row.timeframe} {'LONG' if int(row.class_id) == 0 else 'SHORT'}"
            for row in top.itertuples()
        ]
        bars = ax_rank.barh(np.arange(len(top)), top["confidence"], color="#476f95")
        ax_rank.set_yticks(np.arange(len(top)), names)
        ax_rank.set_xlim(0, 1.03)
        ax_rank.set_xlabel("detector confidence (comparable only within timeframe)")
        ax_rank.set_title("First 20 rows in the deterministic review order", loc="left", weight="bold")
        for rect, value in zip(bars, top["confidence"]):
            ax_rank.text(float(value) + 0.01, rect.get_y() + rect.get_height() / 2, f"{float(value):.3f}", va="center", fontsize=8)

    fig.suptitle(
        "LATEST OKX CRYPTO MULTI-TIMEFRAME YOLO REVIEW QUEUE\n"
        "15m/1h latest endpoint · 4h/1d latest 15 days · frozen Grade-A checkpoint",
        fontsize=16,
        weight="bold",
    )
    fig.text(
        0.5,
        -0.012,
        "Higher-timeframe outputs are OOD research proposals. Prior 4h Owner rejection remains in force. No result authorizes a trade.",
        ha="center",
        color="#9f2d2d",
        fontsize=10,
        weight="bold",
    )
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def build_gallery_html(
    events: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    path: Path,
) -> None:
    """Build a local filterable gallery backed by the exact ranked rows."""

    counts = summary["event_counts"]
    cards: list[str] = []
    table_rows: list[str] = []
    for event in events:
        side = "LONG" if int(event["class_id"]) == 0 else "SHORT"
        symbol = str(event["symbol"]).replace("_USDT_SWAP", "")
        available = utc(event["first_available_at"]).tz_convert("Asia/Shanghai")
        chart = html.escape(f"charts/{event['chart']}")
        warning = " · 方向冲突" if bool(event["direction_conflict_for_symbol"]) else ""
        overlap = ",".join(map(str, event["same_side_timeframes"]))
        table_rows.append(
            "<tr>"
            f"<td>{int(event['review_rank'])}</td><td>{html.escape(symbol)}</td>"
            f"<td>{html.escape(str(event['timeframe_label']))}</td><td>{side}</td>"
            f"<td>{float(event['confidence']):.4f}</td>"
            f"<td>{int(event['confidence_rank_within_timeframe'])}/{int(event['events_in_timeframe'])}</td>"
            f"<td>{html.escape(overlap)}</td><td>{available:%m-%d %H:%M}</td>"
            f"<td><a href=\"#{html.escape(str(event['event_id']))}\">看图</a></td></tr>"
        )
        cards.append(
            f"""
<article class="card" id="{html.escape(str(event['event_id']))}" data-tf="{event['timeframe']}" data-side="{side}" data-symbol="{html.escape(symbol.lower())}">
  <div class="meta"><strong>#{int(event['review_rank']):03d} {html.escape(symbol)} · {event['timeframe_label']} · {side}</strong>
    <span>conf {float(event['confidence']):.4f} · 周期内 #{int(event['confidence_rank_within_timeframe'])}/{int(event['events_in_timeframe'])} · 同向周期 {html.escape(overlap)}{warning}</span></div>
  <div class="sub">首次可见（北京时间）{available:%Y-%m-%d %H:%M} · W{int(event['window_len'])}/core{int(event['core_length_bars'])}/post{int(event['confirmation_bars'])}</div>
  <a href="{chart}"><img loading="lazy" src="{chart}" alt="{html.escape(symbol)} {event['timeframe']} {side} model review chart"></a>
</article>"""
        )
    payload = html.escape(stable_json({"event_counts": counts, "generated_at": summary["generated_at"]}))
    path.write_text(
        f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>最新四周期模型研究候选排序</title>
<style>
:root{{--bg:#0f141a;--panel:#171e26;--line:#303b47;--text:#edf2f7;--muted:#9eabb8;--accent:#6ba3d6;--warn:#f0b35c}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:1500px;margin:auto;padding:24px}}h1{{margin:.2rem 0}}.lead{{color:var(--muted);max-width:1100px}}
.warning{{border-left:4px solid var(--warn);background:#231e17;padding:12px 16px;margin:18px 0}}
.kpis{{display:grid;grid-template-columns:repeat(5,minmax(120px,1fr));gap:10px;margin:18px 0}}.kpi{{background:var(--panel);border:1px solid var(--line);padding:12px;border-radius:8px}}.kpi b{{font-size:1.35rem;display:block}}
.controls{{position:sticky;top:0;z-index:3;background:#0f141aec;padding:12px 0;display:flex;gap:8px;flex-wrap:wrap}}button,input{{background:#1c2630;color:var(--text);border:1px solid var(--line);border-radius:6px;padding:8px 12px}}button.active{{border-color:var(--accent);color:#baddff}}
.tablewrap{{overflow:auto;background:var(--panel);border:1px solid var(--line);border-radius:8px;margin:14px 0 28px}}table{{width:100%;border-collapse:collapse;white-space:nowrap}}th,td{{padding:8px 10px;border-bottom:1px solid #29333d;text-align:left}}th{{position:sticky;top:0;background:#1d2630}}a{{color:#83b9eb}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}}.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;scroll-margin-top:72px}}.card img{{display:block;width:100%;height:auto}}.meta{{display:flex;justify-content:space-between;gap:12px;padding:12px 14px 4px}}.meta span,.sub{{color:var(--muted);font-size:.9rem}}.sub{{padding:0 14px 10px}}.hidden{{display:none}}
@media(max-width:900px){{.grid{{grid-template-columns:1fr}}.kpis{{grid-template-columns:repeat(2,1fr)}}.meta{{display:block}}}}
</style></head><body><main>
<h1>最新四周期模型研究候选排序</h1>
<p class="lead">OKX 合资格加密 USDT 永续；15m、1h 仅最新已收盘端点，4h、日线允许最近 15 天。排序先看同向跨周期重合，再看周期层级、周期内置信度名次与新鲜度；没有把不同周期的置信度硬加成一个胜率。</p>
<div class="warning"><strong>不是可交易信号。</strong> 当前生产 detector=none；1h/4h/日线是 15m 模型跨周期 OOD。上一轮 4h 全图终审“都不太行”的否决仍然有效，本页只是新快照的人审队列。</div>
<div class="kpis">
  <div class="kpi"><b>{len(events)}</b>总候选</div>
  <div class="kpi"><b>{counts['15m']['total']}</b>15m</div>
  <div class="kpi"><b>{counts['1h']['total']}</b>1h</div>
  <div class="kpi"><b>{counts['4h']['total']}</b>4h / 15天</div>
  <div class="kpi"><b>{counts['1d']['total']}</b>日线 / 15天</div>
</div>
<div class="controls"><button class="active" data-filter="all">全部</button><button data-filter="15m">15m</button><button data-filter="1h">1h</button><button data-filter="4h">4h</button><button data-filter="1d">日线</button><button data-filter="LONG">LONG</button><button data-filter="SHORT">SHORT</button><input id="search" placeholder="搜索币种"></div>
<h2>排名表</h2><div class="tablewrap"><table><thead><tr><th>总排名</th><th>币种</th><th>周期</th><th>方向</th><th>conf</th><th>周期内排名</th><th>同向周期</th><th>首次可见 CST</th><th>图</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></div>
<h2>模型原图</h2><div class="grid">{''.join(cards)}</div>
<script type="application/json" id="receipt">{payload}</script>
<script>
const cards=[...document.querySelectorAll('.card')], buttons=[...document.querySelectorAll('button[data-filter]')], search=document.querySelector('#search');let filter='all';
function apply(){{const q=search.value.trim().toLowerCase();cards.forEach(c=>{{const ok=(filter==='all'||c.dataset.tf===filter||c.dataset.side===filter)&&(!q||c.dataset.symbol.includes(q));c.classList.toggle('hidden',!ok)}})}}
buttons.forEach(b=>b.onclick=()=>{{filter=b.dataset.filter;buttons.forEach(x=>x.classList.toggle('active',x===b));apply()}});search.oninput=apply;
</script></main></body></html>""",
        encoding="utf-8",
    )


def paired_direction_null(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize actual versus flipped-direction semantic survival."""

    actual = sum(bool(row["semantic_gate_pass"]) for row in rows)
    flipped = sum(bool(row["flipped_semantic_gate_pass"]) for row in rows)
    actual_only = sum(
        bool(row["semantic_gate_pass"]) and not bool(row["flipped_semantic_gate_pass"])
        for row in rows
    )
    flipped_only = sum(
        bool(row["flipped_semantic_gate_pass"]) and not bool(row["semantic_gate_pass"])
        for row in rows
    )
    discordant = actual_only + flipped_only
    if discordant:
        small = min(actual_only, flipped_only)
        tail = sum(math.comb(discordant, k) for k in range(small + 1)) / (2**discordant)
        p_value = min(1.0, 2.0 * tail)
    else:
        p_value = 1.0
    return {
        "pairs": len(rows),
        "actual_direction_pass": actual,
        "flipped_direction_pass": flipped,
        "actual_only": actual_only,
        "flipped_only": flipped_only,
        "paired_exact_two_sided_p": p_value,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument(
        "--resume-frozen",
        type=Path,
        default=None,
        help="Recover an honestly receipted failed run without another market read.",
    )
    parser.add_argument(
        "--recovery-amendment",
        type=Path,
        default=None,
        help="Committed amendment binding the exact implementation-only recovery.",
    )
    args = parser.parse_args()

    prereg_path = args.prereg.resolve()
    out = args.out.resolve()
    building = out.with_name(out.name + ".building")
    if out.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite output: {out}")
    resume_source = args.resume_frozen.resolve() if args.resume_frozen is not None else None
    recovery_path = (
        args.recovery_amendment.resolve()
        if args.recovery_amendment is not None
        else None
    )
    if (resume_source is None) != (recovery_path is None):
        raise MultiTimeframeScanError(
            "--resume-frozen and --recovery-amendment must be provided together"
        )
    prereg, gates = load_preregistration(prereg_path)
    source_commit = verify_sources_committed(prereg_path, prereg, recovery_path)
    if sha256_file(WEIGHTS) != EXPECTED_WEIGHT_SHA256:
        raise MultiTimeframeScanError("frozen YOLO weight identity drifted")

    started = time.perf_counter()
    if resume_source is None:
        frozen_at = utc(datetime.now(timezone.utc))
    else:
        prior_start = json.loads(
            (resume_source / "holdout_consumption_started.json").read_text(encoding="utf-8")
        )
        frozen_at = utc(prior_start["started_at"])
    building.mkdir(parents=True)
    candle_root = building / "candles"
    chart_root = building / "charts"
    chart_root.mkdir()
    shutil.copy2(prereg_path, building / "preregistration.json")
    if recovery_path is not None:
        shutil.copy2(recovery_path, building / "recovery_amendment.json")
    write_json(
        building / "holdout_consumption_started.json",
        {
            "started_at": frozen_at.isoformat(),
            "recovery_started_at": (
                datetime.now(timezone.utc).isoformat() if resume_source is not None else None
            ),
            "resumed_from_receipted_failure": resume_source is not None,
            "experiment_id": EXPERIMENT_ID,
            "source_commit": source_commit,
            "original_prereg_source_commit": prereg["source_commit"],
            "holdout_consumption_numbers_for_checkpoint": {
                spec.key: spec.holdout_number for spec in TIMEFRAMES
            },
            "scope": {
                spec.key: {
                    "latest_closed_open": latest_closed_open(frozen_at, spec).isoformat(),
                    "earliest_endpoint_open": earliest_endpoint_open(frozen_at, spec).isoformat(),
                    "lookback_endpoints": spec.lookback_endpoints,
                }
                for spec in TIMEFRAMES
            },
        },
    )

    try:
        if resume_source is None:
            ticker_rows = list(common._request(common.TICKERS_URL).get("data") or [])  # noqa: SLF001
            instrument_rows = list(common._request(common.INSTRUMENTS_URL).get("data") or [])  # noqa: SLF001
            eligible = common.eligible_instruments(ticker_rows, instrument_rows)
            if not eligible:
                raise MultiTimeframeScanError("eligible universe is empty")
            universe = ticker_context(ticker_rows, eligible)
            frames, fetch_audits, fetch_failures = fetch_market(
                universe,
                frozen_at=frozen_at,
                candle_root=candle_root,
                workers=args.workers,
            )
            universe_rule = (
                "all current live OKX instCategory=1 crypto USDT swaps with positive "
                "ticker; project blocked and stockish bases excluded"
            )
        else:
            (
                recovered_frozen_at,
                universe,
                frames,
                fetch_audits,
                fetch_failures,
            ) = load_frozen_market(resume_source, candle_root=candle_root)
            if recovered_frozen_at != frozen_at:
                raise MultiTimeframeScanError("recovered freeze timestamp drifted")
            universe_rule = "preserved from the original frozen OKX snapshot"
            recovery_dir = building / "recovery"
            recovery_dir.mkdir()
            shutil.copy2(
                resume_source / "holdout_consumption_started.json",
                recovery_dir / "original_holdout_consumption_started.json",
            )
            shutil.copy2(
                resume_source / "failure_receipt.json",
                recovery_dir / "original_failure_receipt.json",
            )
            shutil.copy2(
                resume_source / "universe.json",
                recovery_dir / "original_universe.json",
            )
        write_json(
            building / "universe.json",
            {
                "frozen_at": frozen_at.isoformat(),
                "rule": universe_rule,
                "symbols": [universe[key] for key in sorted(universe)],
            },
        )
        print(
            f"frozen universe: {len(universe)} symbols"
            f"{' (offline recovery)' if resume_source is not None else ''}",
            flush=True,
        )
        if any(not frames[spec.key] for spec in TIMEFRAMES):
            raise MultiTimeframeScanError("one or more timeframes have zero usable symbols")

        from ultralytics import YOLO

        device = base.choose_device(args.device)
        model = YOLO(str(WEIGHTS))
        names = {int(key): str(value) for key, value in model.names.items()}
        if names != common.CLASS_NAMES:
            raise MultiTimeframeScanError(f"class map drifted: {names}")

        all_events: list[dict[str, Any]] = []
        all_decisions: dict[str, list[dict[str, Any]]] = {}
        scan_stats: dict[str, dict[str, Any]] = {}
        enriched_by_tf: dict[str, dict[str, pd.DataFrame]] = {}
        for spec in TIMEFRAMES:
            print(
                f"inference {spec.key} device={device} symbols={len(frames[spec.key])} "
                f"endpoints={spec.lookback_endpoints}",
                flush=True,
            )
            semantic_ready = enrich_model_frames(frames[spec.key])
            enriched, tasks = base.build_tasks(
                semantic_ready, lookback_endpoints=spec.lookback_endpoints
            )
            candidates, stats = base.infer(
                model,
                tasks,
                frames=enriched,
                device=device,
                batch_size=max(1, args.batch_size),
            )
            decisions = evaluate_semantic_candidates(
                candidates, enriched, gates, timeframe=spec.key
            )
            passed = [row for row in decisions if bool(row["semantic_gate_pass"])]
            passed_flat = [flatten_semantic_candidate(row) for row in passed]
            events = deduplicate_events(
                passed_flat,
                spec=spec,
                frames=enriched,
                frozen_at=frozen_at,
            )
            all_decisions[spec.key] = decisions
            all_events.extend(events)
            enriched_by_tf[spec.key] = enriched
            null = paired_direction_null(decisions)
            scan_stats[spec.key] = {
                **dict(sorted(stats.items())),
                "tasks": len(tasks),
                "usable_symbols": len(enriched),
                "excluded_symbols": len(fetch_failures[spec.key]),
                "accepted_structural_boxes": int(stats["accepted_structural_boxes"]),
                "semantic_pass_boxes": len(passed),
                "semantic_rejected_boxes": len(decisions) - len(passed),
                "deduplicated_events": len(events),
                "direction_flip_null": null,
            }
            tf_dir = building / spec.key
            tf_dir.mkdir()
            pd.DataFrame([flatten_semantic_candidate(row) for row in decisions]).to_csv(
                tf_dir / "structural_candidates.csv", index=False
            )
            pd.DataFrame(passed_flat).to_csv(tf_dir / "semantic_candidates.csv", index=False)
            write_jsonl(tf_dir / "semantic_decisions.jsonl", decisions)
            print(
                f"{spec.key} tasks={len(tasks)} structural={len(decisions)} "
                f"semantic={len(passed)} events={len(events)}",
                flush=True,
            )

        ranked = rank_events(all_events)
        for event in ranked:
            meta = universe[str(event["symbol"])]
            event["ticker_last_at_freeze"] = float(meta["last"])
            event["ticker_change_24h_pct"] = float(meta["change_24h_pct"])
            event["ticker_quote_volume_24h_usdt"] = float(meta["quote_volume_24h_usdt"])

        total = len(ranked)
        for event in ranked:
            spec = SPEC_BY_KEY[str(event["timeframe"])]
            symbol_label = str(event["symbol"]).replace("_USDT_SWAP", "")
            side = "LONG" if int(event["class_id"]) == 0 else "SHORT"
            relative = Path(spec.key) / (
                f"{int(event['review_rank']):04d}_{symbol_label}_{side}.png"
            )
            path = chart_root / relative
            path.parent.mkdir(exist_ok=True)
            image = render_event(
                event,
                frame=enriched_by_tf[spec.key][str(event["symbol"])],
                total=total,
            )
            if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise MultiTimeframeScanError(f"could not write chart: {path}")
            event["chart"] = relative.as_posix()
            event["chart_sha256"] = sha256_file(path)

        pd.DataFrame(ranked).to_csv(building / "ranked_signals.csv", index=False)
        write_jsonl(building / "ranked_signals.jsonl", ranked)
        event_counts: dict[str, dict[str, int]] = {}
        for spec in TIMEFRAMES:
            rows = [row for row in ranked if row["timeframe"] == spec.key]
            event_counts[spec.key] = {
                "total": len(rows),
                "long": sum(int(row["class_id"]) == 0 for row in rows),
                "short": sum(int(row["class_id"]) == 1 for row in rows),
                "current_latest_bar": sum(bool(row["is_current_latest_bar"]) for row in rows),
            }
        multi_tf_symbols = sorted(
            {
                str(row["symbol"])
                for row in ranked
                if int(row["symbol_timeframe_count"]) >= 2
            }
        )
        summary = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "frozen_at": frozen_at.isoformat(),
            "source_commit": source_commit,
            "original_prereg_source_commit": prereg["source_commit"],
            "resumed_from_receipted_failure": resume_source is not None,
            "additional_market_read_during_recovery": False if resume_source is not None else None,
            "model": MODEL_NAME,
            "weights": str(WEIGHTS.relative_to(ROOT)),
            "weights_sha256": EXPECTED_WEIGHT_SHA256,
            "device": device,
            "universe_symbols": len(universe),
            "universe_rule": "all current eligible OKX crypto USDT swaps",
            "timeframes": {
                spec.key: {
                    **asdict(spec),
                    "latest_closed_open": latest_closed_open(frozen_at, spec).isoformat(),
                    "first_scored_endpoint_open": earliest_endpoint_open(frozen_at, spec).isoformat(),
                    "out_of_distribution": spec.key != "15m",
                }
                for spec in TIMEFRAMES
            },
            "fetch_audits": fetch_audits,
            "fetch_failures": fetch_failures,
            "scan_stats": scan_stats,
            "event_counts": event_counts,
            "ranked_events": len(ranked),
            "symbols_with_events": len({str(row["symbol"]) for row in ranked}),
            "multi_timeframe_symbols": multi_tf_symbols,
            "multi_timeframe_symbol_count": len(multi_tf_symbols),
            "ranking_policy": (
                "same-side timeframe count desc; total timeframe count desc; "
                "1d>4h>1h>15m; confidence rank within timeframe; recency"
            ),
            "cross_timeframe_confidence_combined": False,
            "prior_4h_owner_rejection_preserved": True,
            "research_only": True,
            "holdout_consumed": True,
            "holdout_consumption_numbers_for_checkpoint": {
                spec.key: spec.holdout_number for spec in TIMEFRAMES
            },
            "trained": False,
            "threshold_or_weight_changed": False,
            "promoted": False,
            "active_or_frozen_changed": False,
            "forward_state_changed": False,
            "deployed": False,
            "telegram_sent": False,
            "orders_placed": False,
            "production_eligible": False,
            "wall_seconds": round(time.perf_counter() - started, 3),
        }
        build_summary_figure(ranked, scan_stats, building / "summary_overview.png")
        build_contact_sheets(ranked, chart_root, building)
        build_gallery_html(ranked, summary, building / "gallery.html")
        write_json(building / "summary.json", summary)
        building.replace(out)
        print(
            f"complete events={len(ranked)} multi_tf_symbols={len(multi_tf_symbols)} output={out}",
            flush=True,
        )
        return 0
    except Exception as exc:
        write_json(
            building / "failure_receipt.json",
            {
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "experiment_id": EXPERIMENT_ID,
                "error": f"{type(exc).__name__}:{exc}",
                "holdout_consumption_numbers_for_checkpoint": {
                    spec.key: spec.holdout_number for spec in TIMEFRAMES
                },
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
