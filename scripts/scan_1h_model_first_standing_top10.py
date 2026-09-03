#!/usr/bin/env python3
"""Scan OKX 1h candles with YOLO first, then the current six-MA position gate.

The Owner asked to see ten fresh examples without restricting the search to the
previous pre-holdout candidate ledger.  This research-only scan therefore
freezes the current live OKX crypto USDT-swap universe and downloads a new,
bounded 1h snapshot for every eligible instrument.

Selection is causal.  The model sees only W18/W19 ending at proposal bar ``t``;
the deterministic second layer reads only ``close`` and SMA/EMA 20/60/120 at
``t``.  The latest 96 bars are removed from every inference frame before task
construction.  They are reattached only after the Top-10 identities have been
selected, so each global review chart can show four full days of future candles
without allowing future price action to affect candidate generation, gating,
deduplication, or ranking.

The checkpoint was trained on 15-minute charts.  Its 1h use here is explicitly
out-of-distribution completed-history research and cannot authorize a trade,
promotion, deployment, or training-data admission.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import scan_15m_ma_launch_t3_daily_movers as common  # noqa: E402
from scripts import scan_4h_ma_launch_yolo_latest as base  # noqa: E402
from scripts.scan_15m_ma_launch_model_compare_all3d import (  # noqa: E402
    price_text,
    x_at_float,
)
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.model_first_standing import (  # noqa: E402
    evaluate_model_first_standing,
    standing_decisions_equal,
)
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402


EXPERIMENT_ID = "exp-1h-okx-model-first-standing-top10-20260904-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
DEFAULT_RECOVERY_AMENDMENT = (
    ROOT
    / "experiments"
    / "active"
    / EXPERIMENT_ID
    / "recovery_batch_size_20260904.json"
)
BAR = "1H"
BAR_DELTA = pd.Timedelta(hours=1)
FETCH_ROWS = 396
FIRST_PAGE_LIMIT = 300
HISTORY_PAGE_LIMIT = 100
SCORED_ENDPOINTS = 120
REVIEW_PRE_BARS = 180
REVIEW_FUTURE_BARS = 96
TOP_K = 10
HOLDOUT_NUMBER = 20
CANVAS_WIDTH = base.CANVAS_WIDTH
CANVAS_HEIGHT = base.CANVAS_HEIGHT
MAIN_X = base.MAIN_X
MAIN_Y = base.MAIN_Y
MAIN_WIDTH = base.MAIN_WIDTH
MAIN_HEIGHT = base.MAIN_HEIGHT
INSET_WIDTH = base.INSET_WIDTH
INSET_HEIGHT = base.INSET_HEIGHT


class TopTenScanError(RuntimeError):
    """Fail closed on contract, source, causality, or artifact drift."""


def utc(value: object) -> pd.Timestamp:
    """Normalize one timestamp to UTC."""

    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def latest_closed_open(frozen_at: object) -> pd.Timestamp:
    """Return the latest 1h bar open fully knowable at ``frozen_at``."""

    return utc(frozen_at).floor(BAR_DELTA) - BAR_DELTA


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 identity."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(value: Any) -> str:
    """Serialize stable UTF-8 JSON for auditable ledgers."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write one readable JSON artifact."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write an ordered JSON Lines ledger."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(stable_json(dict(row)) + "\n" for row in rows), encoding="utf-8"
    )


def write_png(path: Path, image: np.ndarray) -> None:
    """Persist a PNG or fail instead of silently omitting review evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 4]):
        raise OSError(f"OpenCV failed to write {path}")


def load_preregistration(path: Path) -> dict[str, Any]:
    """Validate the immutable Owner-authorized scan contract."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise TopTenScanError("experiment identity drifted")
    auth = payload.get("owner_authorization") or {}
    if auth.get("holdout_read_authorized") is not True:
        raise TopTenScanError("new market holdout read is not authorized")
    if int(auth.get("holdout_consumption_number_for_checkpoint", -1)) != HOLDOUT_NUMBER:
        raise TopTenScanError("checkpoint holdout number drifted")
    if any(
        bool(auth.get(key))
        for key in (
            "training_or_tuning_authorized",
            "threshold_or_weight_change_authorized",
            "production_or_promotion_authorized",
            "orders_authorized",
        )
    ):
        raise TopTenScanError("an unauthorized mutation is enabled")

    scope = payload.get("scope") or {}
    expected_scope = {
        "bar": BAR,
        "retained_confirmed_rows_per_symbol": FETCH_ROWS,
        "scored_endpoints_per_usable_symbol": SCORED_ENDPOINTS,
        "review_pre_signal_bars": REVIEW_PRE_BARS,
        "review_future_bars": REVIEW_FUTURE_BARS,
        "top_k": TOP_K,
    }
    for key, expected in expected_scope.items():
        if scope.get(key) != expected:
            raise TopTenScanError(f"scope.{key} drifted")

    detector = payload.get("detector") or {}
    expected_detector = {
        "weights_sha256": base.EXPECTED_WEIGHT_SHA256,
        "imgsz": base.IMAGE_SIZE,
        "confidence": base.CONFIDENCE,
        "nms_iou": base.NMS_IOU,
        "window_lengths": list(base.WINDOW_LENGTHS),
        "mapped_core_length_bars_allowed": sorted(base.ALLOWED_CORES),
        "mapped_confirmation_bars_allowed": sorted(base.ALLOWED_CONFIRMATIONS),
        "same_symbol_same_direction_event_gap_bars": base.EVENT_GAP_BARS,
    }
    for key, expected in expected_detector.items():
        if detector.get(key) != expected:
            raise TopTenScanError(f"detector.{key} drifted")

    expected_gate = {
        "pipeline_order": "model_proposal_then_deterministic_bundle_position_gate",
        "long_current": "close[t] > max(sma20,sma60,sma120,ema20,ema60,ema120)[t]",
        "short_current": "close[t] < min(sma20,sma60,sma120,ema20,ema60,ema120)[t]",
        "prior_bar_condition": None,
        "first_cross_required": False,
        "epsilon": 0.0,
        "lookback_rows": 1,
    }
    if payload.get("position_gate") != expected_gate:
        raise TopTenScanError("current-position gate drifted")
    expected_selection = (
        "event_peak_confidence_desc_then_first_available_at_desc_then_symbol_asc_"
        "then_class_id_asc;future_fields_forbidden"
    )
    if (payload.get("selection") or {}).get("ranking") != expected_selection:
        raise TopTenScanError("Top-10 ranking drifted")
    if any(bool(value) for value in (payload.get("safety") or {}).values()):
        raise TopTenScanError("one or more safety switches are enabled")
    for item in (payload.get("implementation_dependencies") or {}).values():
        source = ROOT / str(item["path"])
        if not source.is_file() or sha256_file(source) != str(item["sha256"]):
            raise TopTenScanError(f"implementation dependency drifted: {source}")
    return payload


def verify_committed_sources(
    prereg_path: Path,
    payload: Mapping[str, Any],
    recovery_path: Path | None = None,
) -> str:
    """Require main plus committed builder and prereg before any market read."""

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise TopTenScanError("official scan must run on main")
    paths = [Path(__file__).resolve(), prereg_path.resolve()]
    if recovery_path is not None:
        paths.append(recovery_path.resolve())
    paths.extend(ROOT / str(item["path"]) for item in payload["implementation_dependencies"].values())
    relative = sorted({str(item.relative_to(ROOT)) for item in paths})
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *relative], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise TopTenScanError(f"scan sources must be committed before holdout read:\n{dirty}")
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def load_recovery_amendment(path: Path, snapshot: Path) -> dict[str, Any]:
    """Bind an implementation-only retry to the interrupted frozen snapshot."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise TopTenScanError("recovery experiment identity drifted")
    if payload.get("holdout_consumption_number_for_checkpoint") != HOLDOUT_NUMBER:
        raise TopTenScanError("recovery holdout number drifted")
    if payload.get("new_market_read_authorized") is not False:
        raise TopTenScanError("recovery must forbid another market read")
    if payload.get("semantic_or_selection_change") is not False:
        raise TopTenScanError("recovery cannot change signal semantics or selection")
    if any(bool(value) for value in (payload.get("safety") or {}).values()):
        raise TopTenScanError("recovery safety mutation is enabled")
    declared = payload.get("frozen_snapshot") or {}
    for filename in (
        "holdout_consumption_started.json",
        "universe.json",
        "fetch_audit.json",
    ):
        source = snapshot / filename
        if not source.is_file() or sha256_file(source) != str(declared.get(filename)):
            raise TopTenScanError(f"recovery snapshot drifted: {filename}")
    return payload


def load_frozen_snapshot(
    snapshot: Path, *, candle_dir: Path
) -> tuple[
    pd.Timestamp,
    list[str],
    dict[str, pd.DataFrame],
    list[dict[str, Any]],
    list[dict[str, str]],
]:
    """Load and copy the originally frozen holdout bytes without networking."""

    started = json.loads(
        (snapshot / "holdout_consumption_started.json").read_text(encoding="utf-8")
    )
    if started.get("experiment_id") != EXPERIMENT_ID:
        raise TopTenScanError("snapshot experiment identity drifted")
    if int(started.get("holdout_consumption_number_for_checkpoint", -1)) != HOLDOUT_NUMBER:
        raise TopTenScanError("snapshot holdout number drifted")
    frozen_at = utc(started["started_at"])
    if started.get("latest_closed_bar_open") != latest_closed_open(frozen_at).isoformat():
        raise TopTenScanError("snapshot frozen endpoint drifted")

    universe = json.loads((snapshot / "universe.json").read_text(encoding="utf-8"))
    instruments = list(map(str, universe.get("eligible_instruments") or []))
    if not instruments or instruments != sorted(set(instruments)):
        raise TopTenScanError("snapshot universe is empty, duplicated, or unsorted")
    audit_payload = json.loads((snapshot / "fetch_audit.json").read_text(encoding="utf-8"))
    audits = [dict(row) for row in audit_payload.get("usable") or []]
    failures = [dict(row) for row in audit_payload.get("failures") or []]
    if len(audits) + len(failures) != len(instruments):
        raise TopTenScanError("snapshot fetch ledger does not cover the universe")

    frames: dict[str, pd.DataFrame] = {}
    for audit in audits:
        symbol = str(audit["symbol"])
        source = snapshot / "candles" / f"{symbol}.csv"
        if not source.is_file() or sha256_file(source) != str(audit["sha256"]):
            raise TopTenScanError(f"snapshot candle drifted: {symbol}")
        destination = candle_dir / source.name
        shutil.copy2(source, destination)
        frame = pd.read_csv(destination)
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
        if len(frame) != FETCH_ROWS:
            raise TopTenScanError(f"snapshot row count drifted: {symbol}")
        if utc(frame.iloc[-1]["open_time"]) != latest_closed_open(frozen_at):
            raise TopTenScanError(f"snapshot latest bar drifted: {symbol}")
        if not bool((frame["open_time"].diff().iloc[1:] == BAR_DELTA).all()):
            raise TopTenScanError(f"snapshot candle gaps: {symbol}")
        frames[symbol] = frame
    return frozen_at, instruments, dict(sorted(frames.items())), audits, failures


def _parse_confirmed_rows(rows: Sequence[Sequence[Any]]) -> pd.DataFrame:
    """Parse and exactly deduplicate confirmed OKX 1h response rows."""

    parsed: dict[int, dict[str, Any]] = {}
    for row in rows:
        if len(row) < 6 or (len(row) > 8 and str(row[8]) == "0"):
            continue
        stamp = int(row[0])
        values = {
            "ts": stamp,
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        if stamp in parsed and parsed[stamp] != values:
            raise TopTenScanError(f"conflicting duplicate OKX row at {stamp}")
        parsed[stamp] = values
    frame = pd.DataFrame([parsed[key] for key in sorted(parsed)])
    if not frame.empty:
        frame["open_time"] = pd.to_datetime(frame["ts"], unit="ms", utc=True)
    return frame


def fetch_one(
    inst_id: str, frozen_at: pd.Timestamp
) -> tuple[str, pd.DataFrame, dict[str, Any] | None, str | None]:
    """Fetch exactly enough recent confirmed 1h bars from current + history APIs."""

    expected_latest = latest_closed_open(frozen_at)
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            current_url = (
                f"{common.CANDLES_URL}?instId={inst_id}&bar={BAR}&limit={FIRST_PAGE_LIMIT}"
            )
            first = list(common._request(current_url).get("data") or [])  # noqa: SLF001
            if not first:
                raise TopTenScanError("empty current-candles page")
            oldest = min(int(row[0]) for row in first)
            history_url = (
                f"{common.HISTORY_URL}?instId={inst_id}&bar={BAR}&"
                f"limit={HISTORY_PAGE_LIMIT}&after={oldest}"
            )
            second = list(common._request(history_url).get("data") or [])  # noqa: SLF001
            frame = _parse_confirmed_rows([*first, *second])
            if frame.empty:
                raise TopTenScanError("no confirmed candles")
            frame = frame.loc[frame["open_time"] <= expected_latest].copy()
            frame = frame.sort_values("open_time").reset_index(drop=True)
            if len(frame) < FETCH_ROWS:
                raise TopTenScanError(f"only {len(frame)} confirmed rows, need {FETCH_ROWS}")
            frame = frame.iloc[-FETCH_ROWS:].reset_index(drop=True)
            if utc(frame.iloc[-1]["open_time"]) != expected_latest:
                raise TopTenScanError(
                    f"stale latest bar {frame.iloc[-1]['open_time']} expected {expected_latest}"
                )
            gaps = frame["open_time"].diff().iloc[1:] != BAR_DELTA
            if bool(gaps.any()):
                raise TopTenScanError(f"non-contiguous 1h rows: {int(gaps.sum())} gaps")
            numeric = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
            if not bool(np.isfinite(numeric).all()):
                raise TopTenScanError("non-finite OHLCV")
            if bool((frame[["open", "high", "low", "close"]] <= 0).any().any()):
                raise TopTenScanError("non-positive OHLC")
            if bool((frame["high"] < frame[["open", "close"]].max(axis=1)).any()):
                raise TopTenScanError("high below candle body")
            if bool((frame["low"] > frame[["open", "close"]].min(axis=1)).any()):
                raise TopTenScanError("low above candle body")
            audit = {
                "inst_id": inst_id,
                "rows": len(frame),
                "first_open": utc(frame.iloc[0]["open_time"]).isoformat(),
                "last_open": utc(frame.iloc[-1]["open_time"]).isoformat(),
                "current_page_rows": len(first),
                "history_page_rows": len(second),
            }
            return inst_id, frame, audit, None
        except Exception as exc:  # noqa: BLE001 - every exclusion is receipted
            last_error = exc
            time.sleep(0.6 * (attempt + 1))
    return inst_id, pd.DataFrame(), None, f"{type(last_error).__name__}:{last_error}"


def fetch_market(
    instruments: Sequence[str],
    *,
    frozen_at: pd.Timestamp,
    candle_dir: Path,
    workers: int,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]], list[dict[str, str]]]:
    """Fetch the frozen all-eligible universe with bounded concurrency."""

    frames: dict[str, pd.DataFrame] = {}
    audits: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
        future_map = {
            executor.submit(fetch_one, inst_id, frozen_at): inst_id
            for inst_id in sorted(instruments)
        }
        done = 0
        for future in as_completed(future_map):
            inst_id, frame, audit, error = future.result()
            done += 1
            symbol = inst_id.replace("-", "_")
            if error is not None:
                failures.append({"symbol": symbol, "inst_id": inst_id, "error": error})
            else:
                destination = candle_dir / f"{symbol}.csv"
                frame.to_csv(destination, index=False)
                assert audit is not None
                audit = {**audit, "symbol": symbol, "sha256": sha256_file(destination)}
                frames[symbol] = frame
                audits.append(audit)
            if done % 25 == 0 or done == len(future_map):
                print(
                    f"fetch {done}/{len(future_map)} usable={len(frames)} failed={len(failures)}",
                    flush=True,
                )
    audits.sort(key=lambda row: str(row["symbol"]))
    failures.sort(key=lambda row: str(row["symbol"]))
    return dict(sorted(frames.items())), audits, failures


def decision_prefix(frame: pd.DataFrame) -> pd.DataFrame:
    """Physically remove the 96 review-only future bars before inference."""

    if len(frame) != FETCH_ROWS:
        raise TopTenScanError(f"unexpected source length: {len(frame)}")
    prefix = frame.iloc[:-REVIEW_FUTURE_BARS].copy().reset_index(drop=True)
    expected = REVIEW_PRE_BARS + SCORED_ENDPOINTS
    if len(prefix) != expected:
        raise TopTenScanError(f"decision prefix has {len(prefix)} rows, expected {expected}")
    return prefix


def proposal_direction(class_name: object) -> str:
    """Map the model's frozen class name without inferring a new side."""

    name = str(class_name)
    if name == "dense_long":
        return "LONG"
    if name == "dense_short":
        return "SHORT"
    raise TopTenScanError(f"unsupported model class: {name}")


def evaluate_candidates(
    candidates: Sequence[Mapping[str, Any]], frames: Mapping[str, pd.DataFrame]
) -> list[dict[str, Any]]:
    """Apply the one-row standing gate strictly after every model proposal."""

    decisions: list[dict[str, Any]] = []
    for index, source in enumerate(candidates, 1):
        row = dict(source)
        symbol = str(row["symbol"])
        end = int(row["window_end_i"])
        direction = proposal_direction(row["class_name"])
        actual = evaluate_model_first_standing(
            frames[symbol], proposal_end_i=end, direction=direction
        )
        flipped = evaluate_model_first_standing(
            frames[symbol],
            proposal_end_i=end,
            direction="SHORT" if direction == "LONG" else "LONG",
        )
        row.update(
            {
                "candidate_id": f"standing_{index:06d}",
                "direction": direction,
                "available_at": (
                    utc(row["window_end_time"]) + BAR_DELTA
                ).isoformat(),
                "standing_gate_pass": bool(actual.passed),
                "standing_current_close": float(actual.current_close),
                "standing_bundle_edge": float(actual.current_bundle_edge),
                "flipped_standing_gate_pass": bool(flipped.passed),
                "causal_feature_last_i": end,
            }
        )
        decisions.append(row)
    return decisions


def deduplicate_events(
    passing: Sequence[Mapping[str, Any]], *, gap_bars: int = base.EVENT_GAP_BARS
) -> list[dict[str, Any]]:
    """Collapse W18/W19 and nearby endpoints within symbol and direction.

    The event retains the earliest passing endpoint as its actionable marker.
    Peak confidence is stored separately for model-only review ranking, so a
    later view is never backdated and mistaken for the first detection.
    """

    groups: defaultdict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for item in passing:
        row = dict(item)
        groups[(str(row["symbol"]), int(row["class_id"]))].append(row)
    events: list[dict[str, Any]] = []
    for (symbol, class_id), rows in sorted(groups.items()):
        peaks = common.deduplicate_hits(rows, gap_bars=gap_bars)
        for peak in peaks:
            related = [
                row
                for row in rows
                if abs(int(row["core_end_i"]) - int(peak["core_end_i"])) < gap_bars
            ]
            first_open = min(utc(row["window_end_time"]) for row in related)
            first = max(
                (row for row in related if utc(row["window_end_time"]) == first_open),
                key=lambda row: (float(row["confidence"]), -int(row["window_len"])),
            )
            event = dict(first)
            event.update(
                {
                    "event_peak_confidence": float(peak["confidence"]),
                    "event_peak_endpoint": str(peak["window_end_time"]),
                    "first_detection_endpoint": first_open.isoformat(),
                    "first_available_at": (first_open + BAR_DELTA).isoformat(),
                    "event_candidate_count": len(related),
                    "representative_rule": "earliest_passing_endpoint_then_highest_confidence",
                    "dedup_symbol": symbol,
                    "dedup_class_id": class_id,
                }
            )
            events.append(event)
    events.sort(
        key=lambda row: (
            utc(row["first_available_at"]),
            str(row["symbol"]),
            int(row["class_id"]),
        )
    )
    for index, row in enumerate(events, 1):
        row["event_id"] = f"event_{index:04d}"
    return events


def select_top_events(events: Sequence[Mapping[str, Any]], top_k: int = TOP_K) -> list[dict[str, Any]]:
    """Select model-ranked review events without consulting any future field."""

    forbidden_fragments = (
        "future",
        "return",
        "mfe",
        "mae",
        "profit",
        "outcome",
        "review_",
        "directional_move",
    )
    for row in events:
        bad = [
            key
            for key in row
            if any(fragment in str(key).lower() for fragment in forbidden_fragments)
        ]
        if bad:
            raise TopTenScanError(f"future/outcome fields reached selection: {sorted(bad)}")
    ranked = sorted(
        (dict(row) for row in events),
        key=lambda row: (
            -float(row["event_peak_confidence"]),
            -utc(row["first_available_at"]).value,
            str(row["symbol"]),
            int(row["class_id"]),
        ),
    )[: max(0, int(top_k))]
    for index, row in enumerate(ranked, 1):
        row["review_rank"] = index
        row["selection_policy"] = (
            "event_peak_confidence_desc_then_first_available_at_desc_then_symbol_asc_"
            "then_class_id_asc;future_fields_forbidden"
        )
    return ranked


def append_review_outcomes(
    selected: Sequence[Mapping[str, Any]], full_frames: Mapping[str, pd.DataFrame]
) -> list[dict[str, Any]]:
    """Attach descriptive future moves only after Top-10 identity is frozen."""

    output: list[dict[str, Any]] = []
    for source in selected:
        row = dict(source)
        frame = full_frames[str(row["symbol"])]
        end = int(row["window_end_i"])
        reference = float(frame.iloc[end]["close"])
        sign = 1.0 if int(row["class_id"]) == 0 else -1.0
        for horizon in (24, 48, 96):
            future_close = float(frame.iloc[end + horizon]["close"])
            row[f"review_directional_move_{horizon}h_pct"] = (
                sign * (future_close / reference - 1.0) * 100.0
            )
        future = frame.iloc[end + 1 : end + REVIEW_FUTURE_BARS + 1]
        if int(row["class_id"]) == 0:
            favorable = float(future["high"].max() / reference - 1.0)
            adverse = float(future["low"].min() / reference - 1.0)
        else:
            favorable = float(1.0 - future["low"].min() / reference)
            adverse = float(1.0 - future["high"].max() / reference)
        row.update(
            {
                "review_reference_close": reference,
                "review_mfe_96h_pct": favorable * 100.0,
                "review_mae_96h_pct": adverse * 100.0,
                "review_outcomes_used_for_selection": False,
            }
        )
        output.append(row)
    return output


def verify_selected_causality(
    selected: Sequence[Mapping[str, Any]],
    *,
    full_frames: Mapping[str, pd.DataFrame],
    decision_frames: Mapping[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    """Replay model pixels and mutate all post-endpoint bars for each selection."""

    checks: list[dict[str, Any]] = []
    for row in selected:
        symbol = str(row["symbol"])
        end = int(row["window_end_i"])
        start = int(row["window_start_i"])
        direction = proposal_direction(row["class_name"])
        replay, _ = render_chart(decision_frames[symbol].iloc[start : end + 1], out_path=None)
        replay_hash = base.pixel_sha256(replay)
        if replay_hash != str(row["input_pixel_sha256"]):
            raise TopTenScanError(f"model pixel replay drift: {row['event_id']}")

        raw = full_frames[symbol].copy()
        future_mask = raw.index > end
        multipliers = np.linspace(7.0, 70.0, int(future_mask.sum()))
        for column in ("open", "high", "low", "close", "volume"):
            raw.loc[future_mask, column] = (
                raw.loc[future_mask, column].to_numpy(dtype=float) * multipliers
            )
        original = evaluate_model_first_standing(
            decision_frames[symbol], proposal_end_i=end, direction=direction
        )
        mutated = evaluate_model_first_standing(
            add_mas(raw), proposal_end_i=end, direction=direction
        )
        if not standing_decisions_equal(original, mutated):
            raise TopTenScanError(f"future mutation changed gate: {row['event_id']}")
        checks.append(
            {
                "event_id": row["event_id"],
                "symbol": symbol,
                "model_input_pixel_sha256": replay_hash,
                "pixel_replay_pass": True,
                "future_mutation_rows": int(future_mask.sum()),
                "future_mutation_gate_pass": True,
            }
        )
    return checks


def _put_text(
    image: np.ndarray,
    value: str,
    origin: tuple[int, int],
    *,
    scale: float = 0.48,
    color: tuple[int, int, int] = (32, 32, 32),
    thickness: int = 1,
) -> None:
    cv2.putText(
        image,
        value,
        origin,
        cv2.FONT_HERSHEY_SIMPLEX,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def _dashed_vertical(image: np.ndarray, x: int) -> None:
    for y in range(8, image.shape[0] - 8, 24):
        cv2.line(image, (x, y), (x, min(image.shape[0] - 8, y + 15)), (25, 25, 25), 2)


def render_event(row: Mapping[str, Any], full_frame: pd.DataFrame) -> np.ndarray:
    """Render 180 past + signal + 96 future bars and the exact model inset."""

    enriched = add_mas(full_frame)
    start = int(row["window_start_i"])
    end = int(row["window_end_i"])
    context_start = end - REVIEW_PRE_BARS + 1
    context_end_exclusive = end + REVIEW_FUTURE_BARS + 1
    if context_start < 0 or context_end_exclusive > len(enriched):
        raise TopTenScanError(f"review context bounds failed: {row['event_id']}")

    exact, input_tf = render_chart(enriched.iloc[start : end + 1], out_path=None)
    if base.pixel_sha256(exact) != str(row["input_pixel_sha256"]):
        raise TopTenScanError(f"chart input replay drift: {row['event_id']}")
    exact_box = exact.copy()
    raw_x0, raw_y0, raw_x1, raw_y1 = base.normalized_box_corners(row)
    color = common.CLASS_COLORS[int(row["class_id"])]
    cv2.rectangle(exact_box, (raw_x0, raw_y0), (raw_x1, raw_y1), color, 4, cv2.LINE_AA)

    context = enriched.iloc[context_start:context_end_exclusive].copy()
    main, context_tf = render_chart(
        context, width=MAIN_WIDTH, height=MAIN_HEIGHT, out_path=None
    )
    x0, y0, x1, y1 = base.project_raw_box(
        row,
        input_tf=input_tf,
        context_tf=context_tf,
        context_start_i=context_start,
    )
    cv2.rectangle(main, (x0, y0), (x1, y1), color, 4, cv2.LINE_AA)
    local_signal = end - context_start
    signal_x = x_at_float(context_tf, local_signal)
    shade = main.copy()
    cv2.rectangle(
        shade,
        (signal_x + 1, 0),
        (MAIN_WIDTH - 1, MAIN_HEIGHT - 1),
        (224, 229, 235),
        -1,
    )
    main = cv2.addWeighted(shade, 0.28, main, 0.72, 0)
    _dashed_vertical(main, signal_x)
    _put_text(main, "SIGNAL", (max(5, signal_x - 32), 28), scale=0.45, thickness=2)

    canvas = np.full((CANVAS_HEIGHT, CANVAS_WIDTH, 3), 247, dtype=np.uint8)
    symbol = str(row["symbol"]).replace("_USDT_SWAP", "")
    side = proposal_direction(row["class_name"])
    available = utc(row["first_available_at"]).tz_convert("Asia/Shanghai")
    _put_text(
        canvas,
        (
            f"#{int(row['review_rank']):02d}/{TOP_K:02d}  {symbol}USDT.P  1h  {side}  "
            f"model peak conf {float(row['event_peak_confidence']):.3f}"
        ),
        (24, 38),
        scale=0.69,
        thickness=2,
    )
    _put_text(
        canvas,
        (
            f"first pipeline availability CST {available:%Y-%m-%d %H:%M} | "
            f"current-close six-MA standing gate PASS | no first-cross condition"
        ),
        (24, 72),
        scale=0.46,
        color=(55, 55, 55),
    )
    _put_text(
        canvas,
        "Left: 180 causal bars. Shaded right: exactly 96 FUTURE REVIEW-ONLY bars, excluded before inference and ranking.",
        (24, 102),
        scale=0.44,
        color=(55, 55, 150),
        thickness=2,
    )
    canvas[MAIN_Y : MAIN_Y + MAIN_HEIGHT, MAIN_X : MAIN_X + MAIN_WIDTH] = main

    times = pd.to_datetime(context["open_time"], utc=True).dt.tz_convert("Asia/Shanghai")
    times = times.reset_index(drop=True)
    for local_i in np.linspace(0, len(context) - 1, 7).round().astype(int):
        x = MAIN_X + x_at_float(context_tf, int(local_i))
        stamp = times.iloc[int(local_i)]
        _put_text(
            canvas,
            f"{stamp:%m-%d %H:%M}",
            (max(0, x - 48), MAIN_Y + MAIN_HEIGHT + 24),
            scale=0.39,
            color=(75, 75, 75),
        )
    for fraction in np.linspace(0.08, 0.92, 5):
        price = context_tf.price_max - fraction * (context_tf.price_max - context_tf.price_min)
        y = MAIN_Y + int(round(context_tf.top + fraction * context_tf.plot_h))
        _put_text(
            canvas,
            price_text(price),
            (CANVAS_WIDTH - 118, y),
            scale=0.39,
            color=(75, 75, 75),
        )

    footer = 924
    _put_text(canvas, "POST-SELECTION REVIEW (not PnL)", (28, footer), scale=0.59, thickness=2)
    _put_text(
        canvas,
        (
            f"Directional close move: 24h {float(row['review_directional_move_24h_pct']):+.2f}% | "
            f"48h {float(row['review_directional_move_48h_pct']):+.2f}% | "
            f"96h {float(row['review_directional_move_96h_pct']):+.2f}%"
        ),
        (28, footer + 34),
        scale=0.46,
    )
    _put_text(
        canvas,
        (
            f"96h MFE {float(row['review_mfe_96h_pct']):+.2f}% | "
            f"MAE {float(row['review_mae_96h_pct']):+.2f}% | no fees, slippage, TP/SL, or trade claim"
        ),
        (28, footer + 66),
        scale=0.44,
    )
    _put_text(
        canvas,
        "OOD: checkpoint trained on 15m images; this 1h completed-history review is not production validation.",
        (28, footer + 98),
        scale=0.44,
        color=(45, 45, 180),
        thickness=2,
    )
    _put_text(
        canvas,
        "RESEARCH ONLY - NOT ACTIVE / NOT A TRADE AUTHORIZATION",
        (28, footer + 130),
        scale=0.46,
        color=(45, 45, 180),
        thickness=2,
    )

    _put_text(
        canvas,
        "EXACT CAUSAL MODEL INPUT",
        (CANVAS_WIDTH - INSET_WIDTH - 18, footer),
        scale=0.58,
        thickness=2,
    )
    inset = cv2.resize(exact_box, (INSET_WIDTH, INSET_HEIGHT), interpolation=cv2.INTER_AREA)
    inset_x, inset_y = CANVAS_WIDTH - INSET_WIDTH - 18, footer + 18
    canvas[inset_y : inset_y + INSET_HEIGHT, inset_x : inset_x + INSET_WIDTH] = inset
    cv2.rectangle(
        canvas,
        (inset_x, inset_y),
        (inset_x + INSET_WIDTH - 1, inset_y + INSET_HEIGHT - 1),
        (65, 65, 65),
        2,
    )
    return canvas


def build_gallery(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    """Build a scrollable Owner-facing gallery for the ten global charts."""

    cards: list[str] = []
    for row in rows:
        symbol = str(row["symbol"]).replace("_USDT_SWAP", "")
        side = proposal_direction(row["class_name"])
        when = utc(row["first_available_at"]).tz_convert("Asia/Shanghai")
        cards.append(
            "<article><h2>"
            + html.escape(
                f"#{int(row['review_rank']):02d} {symbol}USDT.P · {side} · "
                f"{when:%Y-%m-%d %H:%M} CST · conf {float(row['event_peak_confidence']):.3f}"
            )
            + "</h2><p>96h directional move "
            + html.escape(f"{float(row['review_directional_move_96h_pct']):+.2f}%")
            + "（只作事后审核，未参与选择；不等于实际盈亏）。</p><img loading='lazy' src='charts/"
            + html.escape(str(row["chart_filename"]))
            + "'></article>"
        )
    document = f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>
<title>OKX 1h 模型先 + 当前六均线站位 Top-10</title>
<style>body{{font-family:system-ui;margin:24px;background:#f4f5f7;color:#181818}}article{{background:white;padding:18px;margin:24px 0;border:1px solid #ddd}}img{{width:100%;height:auto;border:1px solid #aaa}}code{{background:#eee;padding:2px 5px}}</style></head>
<body><h1>OKX USDT 永续 1h · 模型先检测 + 代码检查当前站位 · Top-10</h1>
<p>不是旧候选复用：本页来自本轮重新下载的全市场原始 K 线与重新推理。每图左侧 180 根历史；虚线右侧 96 根未来 K 线只用于人工审核，选 Top-10 前已从推理帧物理删除。</p>
<p>排序只用模型事件峰值置信度、信号时间与稳定字典序；没有用未来涨跌。模型原生训练周期为 15m，因此这些 1h 图只属于 OOD 研究样本。</p>
{''.join(cards)}</body></html>"""
    path.write_text(document, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument(
        "--resume-frozen",
        type=Path,
        default=None,
        help="Resume from an interrupted, fully receipted market snapshot without networking.",
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
        raise TopTenScanError(
            "--resume-frozen and --recovery-amendment must be supplied together"
        )
    payload = load_preregistration(prereg_path)
    recovery = (
        load_recovery_amendment(recovery_path, resume_source)
        if recovery_path is not None and resume_source is not None
        else None
    )
    if recovery is not None and int(args.batch_size) != int(
        recovery["runtime_only_change"]["recovery_batch_size"]
    ):
        raise TopTenScanError("runtime batch size differs from recovery amendment")
    source_commit = verify_committed_sources(prereg_path, payload, recovery_path)
    weights = ROOT / str(payload["detector"]["weights"])
    if sha256_file(weights) != base.EXPECTED_WEIGHT_SHA256:
        raise TopTenScanError("frozen checkpoint bytes drifted")

    started = time.perf_counter()
    if resume_source is None:
        frozen_at = utc(datetime.now(timezone.utc))
    else:
        frozen_at = utc(
            json.loads(
                (resume_source / "holdout_consumption_started.json").read_text(
                    encoding="utf-8"
                )
            )["started_at"]
        )
    building.mkdir(parents=True)
    candle_dir = building / "candles"
    chart_dir = building / "review" / "charts"
    model_input_dir = building / "model_inputs"
    candle_dir.mkdir()
    chart_dir.mkdir(parents=True)
    model_input_dir.mkdir()
    shutil.copy2(prereg_path, building / "preregistration.json")
    if resume_source is None:
        write_json(
            building / "holdout_consumption_started.json",
            {
                "experiment_id": EXPERIMENT_ID,
                "started_at": frozen_at.isoformat(),
                "source_commit": source_commit,
                "holdout_consumption_number_for_checkpoint": HOLDOUT_NUMBER,
                "latest_closed_bar_open": latest_closed_open(frozen_at).isoformat(),
                "failure_still_consumes_holdout": True,
                "network_read_not_yet_started_at_receipt_write": True,
            },
        )
    else:
        assert recovery_path is not None
        shutil.copy2(
            resume_source / "holdout_consumption_started.json",
            building / "holdout_consumption_started.json",
        )
        shutil.copy2(recovery_path, building / "recovery_amendment.json")
        write_json(
            building / "recovery_started.json",
            {
                "experiment_id": EXPERIMENT_ID,
                "recovery_started_at": datetime.now(timezone.utc).isoformat(),
                "original_frozen_at": frozen_at.isoformat(),
                "source_commit": source_commit,
                "new_market_reads": 0,
                "recovery_batch_size": int(args.batch_size),
            },
        )

    try:
        if resume_source is None:
            ticker_rows = list(common._request(common.TICKERS_URL).get("data") or [])  # noqa: SLF001
            instrument_rows = list(common._request(common.INSTRUMENTS_URL).get("data") or [])  # noqa: SLF001
            instruments = common.eligible_instruments(ticker_rows, instrument_rows)
            if not instruments:
                raise TopTenScanError("eligible OKX universe is empty")
            write_json(
                building / "universe.json",
                {
                    "frozen_at": frozen_at.isoformat(),
                    "rule": (
                        "all current live OKX instCategory=1 crypto USDT swaps with positive "
                        "ticker; project blocked and stockish bases excluded; no return or volume ranking"
                    ),
                    "eligible_instruments": instruments,
                    "eligible_count": len(instruments),
                },
            )
            full_frames, fetch_audits, failures = fetch_market(
                instruments,
                frozen_at=frozen_at,
                candle_dir=candle_dir,
                workers=args.workers,
            )
            write_json(
                building / "fetch_audit.json",
                {"usable": fetch_audits, "failures": failures},
            )
        else:
            (
                recovered_frozen_at,
                instruments,
                full_frames,
                fetch_audits,
                failures,
            ) = load_frozen_snapshot(resume_source, candle_dir=candle_dir)
            if recovered_frozen_at != frozen_at:
                raise TopTenScanError("recovered frozen_at drifted")
            shutil.copy2(resume_source / "universe.json", building / "universe.json")
            shutil.copy2(resume_source / "fetch_audit.json", building / "fetch_audit.json")
        if not full_frames:
            raise TopTenScanError("all market fetches failed")

        raw_decision_frames = {
            symbol: decision_prefix(frame) for symbol, frame in full_frames.items()
        }
        enriched_decision_frames, tasks = base.build_tasks(
            raw_decision_frames, lookback_endpoints=SCORED_ENDPOINTS
        )
        expected_tasks = len(enriched_decision_frames) * SCORED_ENDPOINTS * len(base.WINDOW_LENGTHS)
        if len(tasks) != expected_tasks:
            raise TopTenScanError(f"expected {expected_tasks} model tasks, got {len(tasks)}")

        from ultralytics import YOLO

        device = base.choose_device(args.device)
        model = YOLO(str(weights))
        names = {int(key): str(value) for key, value in model.names.items()}
        if names != common.CLASS_NAMES:
            raise TopTenScanError(f"checkpoint class map drifted: {names}")
        print(
            f"inference device={device} symbols={len(enriched_decision_frames)} tasks={len(tasks)}",
            flush=True,
        )
        structural, stats = base.infer(
            model,
            tasks,
            frames=enriched_decision_frames,
            device=device,
            batch_size=max(1, int(args.batch_size)),
        )
        decisions = evaluate_candidates(structural, enriched_decision_frames)
        passing = [row for row in decisions if bool(row["standing_gate_pass"])]
        events = deduplicate_events(passing)
        selected_pre_outcome = select_top_events(events, TOP_K)
        selected = append_review_outcomes(selected_pre_outcome, full_frames)
        checks = verify_selected_causality(
            selected_pre_outcome,
            full_frames=full_frames,
            decision_frames=enriched_decision_frames,
        )

        pd.DataFrame(decisions).to_csv(building / "standing_decisions.csv", index=False)
        pd.DataFrame(passing).to_csv(building / "passing_candidates.csv", index=False)
        pd.DataFrame(events).to_csv(building / "deduplicated_events.csv", index=False)
        write_jsonl(building / "standing_decisions.jsonl", decisions)
        write_json(building / "causality_verification.json", {"checks": checks})

        rendered: list[dict[str, Any]] = []
        for row in selected:
            item = dict(row)
            symbol = str(item["symbol"]).replace("_USDT_SWAP", "")
            stamp = utc(item["first_detection_endpoint"]).strftime("%Y%m%dT%H%MZ")
            filename = (
                f"{int(item['review_rank']):02d}_{symbol}_{item['direction']}_{stamp}_global.png"
            )
            chart = render_event(item, full_frames[str(item["symbol"])])
            write_png(chart_dir / filename, chart)
            model_image, _ = render_chart(
                enriched_decision_frames[str(item["symbol"])].iloc[
                    int(item["window_start_i"]) : int(item["window_end_i"]) + 1
                ],
                out_path=None,
            )
            x0, y0, x1, y1 = base.normalized_box_corners(item)
            cv2.rectangle(
                model_image,
                (x0, y0),
                (x1, y1),
                common.CLASS_COLORS[int(item["class_id"])],
                4,
                cv2.LINE_AA,
            )
            write_png(model_input_dir / filename.replace("_global", "_model_input"), model_image)
            item["chart_filename"] = filename
            rendered.append(item)
        pd.DataFrame(rendered).to_csv(building / "selected_top10.csv", index=False)
        build_gallery(building / "review" / "gallery.html", rendered)

        side_counts = Counter(str(row["direction"]) for row in rendered)
        positive_96h = sum(
            float(row["review_directional_move_96h_pct"]) > 0 for row in rendered
        )
        summary = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": source_commit,
            "original_prereg_source_commit": (
                json.loads(
                    (resume_source / "holdout_consumption_started.json").read_text(
                        encoding="utf-8"
                    )
                )["source_commit"]
                if resume_source is not None
                else source_commit
            ),
            "frozen_at": frozen_at.isoformat(),
            "holdout_consumption_number_for_checkpoint": HOLDOUT_NUMBER,
            "recovery": {
                "used": resume_source is not None,
                "new_market_reads": 0 if resume_source is not None else None,
                "amendment": (
                    str(recovery_path.relative_to(ROOT)) if recovery_path is not None else None
                ),
                "runtime_batch_size": int(args.batch_size),
                "semantic_or_selection_change": (
                    bool(recovery["semantic_or_selection_change"])
                    if recovery is not None
                    else False
                ),
            },
            "source": {
                "venue": "OKX",
                "bar": BAR,
                "eligible_instruments": len(instruments),
                "usable_instruments": len(full_frames),
                "excluded_instruments": len(failures),
                "confirmed_rows_per_usable_symbol": FETCH_ROWS,
                "latest_closed_bar_open": latest_closed_open(frozen_at).isoformat(),
            },
            "causal_scan": {
                "future_review_bars_physically_removed_before_inference": REVIEW_FUTURE_BARS,
                "scored_endpoints_per_symbol": SCORED_ENDPOINTS,
                "window_lengths": list(base.WINDOW_LENGTHS),
                "model_tasks": len(tasks),
                "raw_boxes": int(stats["raw_boxes"]),
                "structurally_accepted_boxes": len(structural),
                "standing_gate_pass_boxes": len(passing),
                "direction_flipped_gate_pass_boxes": sum(
                    bool(row["flipped_standing_gate_pass"]) for row in decisions
                ),
                "deduplicated_events": len(events),
            },
            "selection": {
                "requested": TOP_K,
                "delivered": len(rendered),
                "ranking": payload["selection"]["ranking"],
                "future_or_outcome_fields_used": False,
                "side_counts": dict(sorted(side_counts.items())),
            },
            "post_selection_review": {
                "future_bars_per_chart": REVIEW_FUTURE_BARS,
                "positive_directional_move_at_96h": positive_96h,
                "non_positive_directional_move_at_96h": len(rendered) - positive_96h,
                "is_profit_measurement": False,
                "fees_slippage_tp_sl_applied": False,
            },
            "verification": {
                "selected_pixel_replays": len(checks),
                "selected_future_mutation_passes": sum(
                    bool(row["future_mutation_gate_pass"]) for row in checks
                ),
                "review_charts": len(rendered),
            },
            "limitations": {
                "checkpoint_training_clock": "15m",
                "scan_clock": "1h",
                "out_of_distribution": True,
                "precision_or_profit_claim_allowed": False,
                "top10_is_model_ranked_review_queue_not_winner_selection": True,
            },
            "safety": payload["safety"],
            "wall_seconds": round(time.perf_counter() - started, 3),
        }
        write_json(building / "summary.json", summary)
        building.replace(out)
        print(stable_json(summary), flush=True)
        return 0
    except Exception as exc:
        write_json(
            building / "failure_receipt.json",
            {
                "experiment_id": EXPERIMENT_ID,
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
                "holdout_consumed": True,
                "wall_seconds": round(time.perf_counter() - started, 3),
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
