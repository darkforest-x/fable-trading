#!/usr/bin/env python3
"""Build and audit a causal L2 global-context ranker for frozen L1 proposals.

The experiment is deliberately split into immutable phases.  ``--freeze-snapshot``
copies the preregistered pre-holdout interval into a byte-addressed snapshot;
``--scan`` runs the frozen Grade-A full40 native-1280 YOLO over every W18/W19
endpoint and emits one cross-midnight episode ledger; ``--build-dataset`` samples
the existing 28 causal L2 features at the last bar actually visible to L1 and
labels from the next bar only; ``--train-evaluate`` selects one tune-q90 score
gate and evaluates it once on the final pre-holdout period; ``--render`` creates
decision-only 168-bar global charts; and ``--verify`` reproduces every chart.
Split embargoes cover the complete 168-bar input plus 72-bar label exposure
(60 hours).  Directly or transitively overlapping same-symbol exposures form a
dependency block; only its earliest event participates in fit/tune/evaluation.

Inputs used by the L2 feature row are documented by
``yoyo.layers.l2_judgment.features``.  The latest possible feature input is the
L1 window-end bar.  Its open timestamp is ``window_end_time``; it becomes
available only at ``window_end_time + 15 minutes``.  Outcomes start at that next
bar's open and may look 72 bars forward.  No feature, detector pixel or model
score reads those outcome bars.

This is a completed-shape research path.  The frozen L1 has already consumed
2--9 post-core confirmation bars, so none of these results may be presented as
a tip/tip-1/tip-2 production signal.  The script never fetches network data,
promotes, deploys, mutates ACTIVE/frozen/forward state, sends Telegram, or
places orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l2-global-context-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "analysis" / "output" / "ma_launch_l2_global_context_v1"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
BAR_DELTA = pd.Timedelta(minutes=15)
EXPECTED_CLASS_NAMES = {0: "dense_long", 1: "dense_short"}
CLASS_COLORS = {0: (35, 165, 45), 1: (45, 45, 220)}
L2_CONTEXT_BARS = 168
ATR_BUCKET_LOOKBACK = 672
SEED = 42


class L2ExperimentError(RuntimeError):
    """Fail closed when experiment identity, causality or lineage drifts."""


def utc(value: object) -> pd.Timestamp:
    """Normalize one timestamp to UTC."""

    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def read_json(path: Path) -> dict[str, Any]:
    """Read one UTF-8 JSON object."""

    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically write a deterministic UTF-8 JSON object."""

    path.parent.mkdir(parents=True, exist_ok=True)
    building = path.with_suffix(path.suffix + ".building")
    building.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    building.replace(path)


def write_rows(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str] = ()) -> None:
    """Atomically write a stable CSV, including a declared empty schema."""

    path.parent.mkdir(parents=True, exist_ok=True)
    actual = sorted({key for row in rows for key in row}) if rows else list(columns)
    building = path.with_suffix(path.suffix + ".building")
    pd.DataFrame([dict(row) for row in rows], columns=actual).to_csv(building, index=False)
    building.replace(path)


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 identity."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(image: np.ndarray) -> str:
    """Hash exact BGR pixels instead of PNG container metadata."""

    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def repo_relative(path: Path) -> str:
    """Return a portable repository-relative path."""

    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def resolve_repo_path(value: object) -> Path:
    """Resolve one preregistered path without permitting path escape."""

    path = (ROOT / str(value).replace("\\", "/")).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise L2ExperimentError(f"path escapes repository: {value}") from exc
    return path


def load_preregistration(path: Path) -> dict[str, Any]:
    """Load and enforce the immutable research-only contract."""

    payload = read_json(path)
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise L2ExperimentError("unexpected experiment_id")
    l1 = payload["l1"]
    if tuple(map(int, l1["window_lengths"])) != (18, 19):
        raise L2ExperimentError("L1 window contract drifted")
    if float(l1["confidence"]) != 0.25 or float(l1["nms_iou"]) != 0.7:
        raise L2ExperimentError("L1 confidence/NMS drifted")
    if int(l1["imgsz"]) != 1280:
        raise L2ExperimentError("native inference size drifted")
    lineage = payload["five_model_lineage"]
    if lineage.get("selected_l1_key") != l1.get("key"):
        raise L2ExperimentError("five-model lineage does not select the frozen L1")
    if lineage.get("other_models_used_as_l2_features") is not False:
        raise L2ExperimentError("v1 must not mix incompatible detector contracts")
    outcome = payload["outcome"]
    expected = (5.0, 2.0, 72, 0.0015, 0.002)
    observed = (
        float(outcome["tp_atr_multiple"]),
        float(outcome["sl_atr_multiple"]),
        int(outcome["horizon_bars"]),
        float(outcome["decision_atr_pct_min"]),
        float(outcome["round_trip_cost_fraction"]),
    )
    if observed != expected:
        raise L2ExperimentError(f"outcome/cost contract drifted: {observed}")
    if payload["owner_authorization"].get("holdout_read_authorized") is not False:
        raise L2ExperimentError("this configuration must not read holdout")
    if any(payload["safety"].values()):
        raise L2ExperimentError("one or more safety switches drifted true")
    holdout = utc(payload["source"]["holdout_start"])
    if utc(payload["source"]["snapshot_end_exclusive"]) > holdout:
        raise L2ExperimentError("snapshot reaches beyond holdout boundary")
    if utc(payload["source"]["candidate_available_at_end_exclusive"]) + pd.Timedelta(
        minutes=15 * int(outcome["horizon_bars"])
    ) > holdout:
        raise L2ExperimentError("candidate horizon may cross into holdout")
    exposure_hours = (
        L2_CONTEXT_BARS * BAR_DELTA
        + int(outcome["horizon_bars"]) * BAR_DELTA
    ) / pd.Timedelta(hours=1)
    split_links = (
        ("train", "purge_train_tune", "tune"),
        ("tune", "purge_tune_validation", "final_preholdout_validation"),
    )
    for left_key, key, right_key in split_links:
        purge = payload["splits"][key]
        observed_hours = (
            utc(purge["end_exclusive"]) - utc(purge["start"])
        ) / pd.Timedelta(hours=1)
        if observed_hours != exposure_hours or float(purge["duration_hours"]) != exposure_hours:
            raise L2ExperimentError(
                f"{key} must cover the full input+label exposure: "
                f"{observed_hours}h != {exposure_hours}h"
            )
        if utc(payload["splits"][left_key]["available_at_end_exclusive"]) != utc(
            purge["start"]
        ) or utc(purge["end_exclusive"]) != utc(
            payload["splits"][right_key]["available_at_start"]
        ):
            raise L2ExperimentError(f"{key} is not contiguous with its learning splits")
    return payload


def verify_declared_file(path: Path, expected_sha: str, label: str) -> None:
    """Require one immutable input and its declared SHA."""

    if not path.is_file():
        raise L2ExperimentError(f"missing {label}: {path}")
    actual = sha256_file(path)
    if actual != expected_sha:
        raise L2ExperimentError(f"{label} hash drifted: {actual} != {expected_sha}")


def verify_immutable_inputs(prereg: Mapping[str, Any]) -> dict[str, Any]:
    """Verify the frozen detector, renderer, L1 manifest and L2 builders."""

    l1, l2 = prereg["l1"], prereg["l2"]
    lineage = prereg["five_model_lineage"]
    declared = (
        (resolve_repo_path(l1["weights"]), str(l1["weights_sha256"]), "L1 weights"),
        (
            resolve_repo_path(l1["training_manifest"]),
            str(l1["training_manifest_sha256"]),
            "L1 training manifest",
        ),
        (resolve_repo_path(l1["renderer"]), str(l1["renderer_sha256"]), "L1 renderer"),
        (
            resolve_repo_path(l2["feature_builder"]),
            str(l2["feature_builder_sha256"]),
            "L2 feature builder",
        ),
        (
            resolve_repo_path(l2["label_builder"]),
            str(l2["label_builder_sha256"]),
            "L2 label builder",
        ),
        (
            resolve_repo_path(lineage["comparison_preregistration"]),
            str(lineage["comparison_preregistration_sha256"]),
            "five-model comparison preregistration",
        ),
        (
            resolve_repo_path(lineage["comparison_summary"]),
            str(lineage["comparison_summary_sha256"]),
            "five-model comparison summary",
        ),
        (
            resolve_repo_path(lineage["model_summary"]),
            str(lineage["model_summary_sha256"]),
            "five-model model summary",
        ),
    )
    for path, expected, label in declared:
        verify_declared_file(path, expected, label)
    return {
        label: {"path": repo_relative(path), "sha256": expected}
        for path, expected, label in declared
    }


def committed_source_identity(prereg_path: Path, *, replicated_commit: str | None) -> str:
    """Require committed builders locally or an explicit immutable worker identity."""

    script = Path(__file__).resolve()
    try:
        inside = subprocess.check_output(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        inside = "false"
    if inside == "true":
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=ROOT, text=True
        ).strip()
        if branch != "main":
            raise L2ExperimentError("official run requires main")
        owned = [script.relative_to(ROOT), prereg_path.resolve().relative_to(ROOT)]
        dirty = subprocess.check_output(
            ["git", "status", "--short", "--", *map(str, owned)], cwd=ROOT, text=True
        ).strip()
        if dirty:
            raise L2ExperimentError(f"builder/prereg must be committed before run:\n{dirty}")
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if replicated_commit is None or len(replicated_commit) != 40:
        raise L2ExperimentError(
            "replicated worker requires --replicated-source-commit with the committed builder SHA"
        )
    return replicated_commit


def normalize_ohlcv(path: Path) -> pd.DataFrame:
    """Read canonical OHLCV columns and reject malformed rows."""

    frame = pd.read_csv(path)
    required = {"open_time", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise L2ExperimentError(f"{path.name} missing columns: {missing}")
    out = frame[["open_time", "open", "high", "low", "close", "volume"]].copy()
    out["open_time"] = pd.to_datetime(out["open_time"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        out[column] = pd.to_numeric(out[column], errors="coerce")
    if out.isna().any().any():
        raise L2ExperimentError(f"{path.name} contains invalid OHLCV rows")
    out = out.sort_values("open_time").reset_index(drop=True)
    if out["open_time"].duplicated().any():
        raise L2ExperimentError(f"{path.name} contains duplicate timestamps")
    return out


def assert_exact_grid(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, label: str) -> None:
    """Require exactly one closed 15m row over the half-open interval."""

    expected = pd.date_range(start, end, inclusive="left", freq="15min")
    actual = pd.DatetimeIndex(frame["open_time"])
    if len(actual) != len(expected) or not actual.equals(expected):
        missing = expected.difference(actual)
        extra = actual.difference(expected)
        raise L2ExperimentError(
            f"{label} is not exact contiguous 15m data: rows={len(actual)}/{len(expected)} "
            f"missing={len(missing)} extra={len(extra)}"
        )


def source_symbol(path: Path) -> str:
    """Recover ``BASE_USDT_SWAP`` from one deep-cache filename."""

    name = path.stem
    if not name.startswith("okx_") or "_15m_" not in name:
        raise L2ExperimentError(f"unexpected source filename: {path.name}")
    return name.removeprefix("okx_").rsplit("_15m_", 1)[0]


def freeze_snapshot(
    prereg: Mapping[str, Any], *, out: Path, results: Path, source_commit: str
) -> dict[str, Any]:
    """Freeze the exact pre-holdout source interval without network reads."""

    receipt_path = results / "snapshot_receipt.json"
    snapshot_dir = out / "snapshot"
    if receipt_path.exists():
        receipt = read_json(receipt_path)
        for row in receipt["files"]:
            path = out / str(row["snapshot_path"])
            verify_declared_file(path, str(row["sha256"]), "snapshot file")
        return receipt
    start = utc(prereg["source"]["snapshot_start"])
    end = utc(prereg["source"]["snapshot_end_exclusive"])
    source_files = sorted(ROOT.glob(str(prereg["source"]["files_glob"])))
    if len(source_files) != int(prereg["source"]["required_file_count"]):
        raise L2ExperimentError(
            f"deep source count drifted: {len(source_files)} != "
            f"{prereg['source']['required_file_count']}"
        )
    building = out / "snapshot.building"
    if building.exists():
        shutil.rmtree(building)
    building.mkdir(parents=True)
    rows: list[dict[str, Any]] = []
    try:
        for number, path in enumerate(source_files, 1):
            symbol = source_symbol(path)
            frame = normalize_ohlcv(path)
            mask = (frame["open_time"] >= start) & (frame["open_time"] < end)
            bounded = frame.loc[mask].reset_index(drop=True)
            assert_exact_grid(bounded, start, end, symbol)
            target = building / f"{symbol}.csv"
            bounded.to_csv(target, index=False)
            rows.append(
                {
                    "symbol": symbol,
                    "source_path": repo_relative(path),
                    "source_sha256": sha256_file(path),
                    "snapshot_path": f"snapshot/{target.name}",
                    "rows": len(bounded),
                    "sha256": sha256_file(target),
                }
            )
            print(f"freeze [{number:02d}/{len(source_files):02d}] {symbol}", flush=True)
        if snapshot_dir.exists():
            raise FileExistsError(f"refusing to replace existing snapshot: {snapshot_dir}")
        building.replace(snapshot_dir)
    except Exception:
        if building.exists():
            shutil.rmtree(building)
        raise
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "snapshot_start": start.isoformat(),
        "snapshot_end_exclusive": end.isoformat(),
        "symbols": len(rows),
        "rows": sum(int(row["rows"]) for row in rows),
        "files": rows,
        "network_reads": 0,
        "holdout_rows_read": 0,
    }
    write_json(receipt_path, payload)
    return payload


def load_snapshot(prereg: Mapping[str, Any], *, out: Path, results: Path) -> dict[str, pd.DataFrame]:
    """Load and hash-check every frozen source file."""

    receipt_path = results / "snapshot_receipt.json"
    if not receipt_path.is_file():
        raise L2ExperimentError("snapshot receipt missing; run --freeze-snapshot first")
    receipt = read_json(receipt_path)
    if int(receipt["symbols"]) != int(prereg["source"]["required_file_count"]):
        raise L2ExperimentError("snapshot symbol count drifted")
    start = utc(prereg["source"]["snapshot_start"])
    end = utc(prereg["source"]["snapshot_end_exclusive"])
    frames: dict[str, pd.DataFrame] = {}
    for row in receipt["files"]:
        path = out / str(row["snapshot_path"])
        verify_declared_file(path, str(row["sha256"]), "snapshot file")
        frame = normalize_ohlcv(path)
        assert_exact_grid(frame, start, end, str(row["symbol"]))
        frames[str(row["symbol"])] = frame
    return frames


def load_training_intervals(prereg: Mapping[str, Any]) -> dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]]:
    """Load every L1 positive/negative visible interval for overlap exclusion."""

    path = resolve_repo_path(prereg["l1"]["training_manifest"])
    grouped: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = defaultdict(list)
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            symbol = str(row.get("symbol") or row.get("exchange_symbol") or "")
            start, end = row.get("window_start_time"), row.get("window_end_time")
            if symbol and start is not None and end is not None:
                grouped[symbol].append((utc(start), utc(end)))
    for symbol in grouped:
        grouped[symbol].sort()
    return dict(grouped)


def overlaps_any_interval(
    start: pd.Timestamp, end: pd.Timestamp, intervals: Sequence[tuple[pd.Timestamp, pd.Timestamp]]
) -> bool:
    """Return whether one closed visible interval intersects any frozen training view."""

    for old_start, old_end in intervals:
        if old_start > end:
            break
        if old_end >= start:
            return True
    return False


def map_prediction_to_core(
    *, cx: float, width: float, transform: Any, window_start_i: int, window_end_i: int
) -> dict[str, int]:
    """Map one normalized raw prediction to discrete source-bar geometry."""

    x0 = (float(cx) - float(width) / 2.0) * transform.width
    x1 = (float(cx) + float(width) / 2.0) * transform.width
    centers = np.asarray([transform.x_at(index) for index in range(transform.n_bars)])
    local_start = int(np.argmin(np.abs(centers - x0)))
    local_end = int(np.argmin(np.abs(centers - x1)))
    if local_end < local_start:
        local_start, local_end = local_end, local_start
    core_start_i = int(window_start_i) + local_start
    core_end_i = int(window_start_i) + local_end
    return {
        "core_start_i": core_start_i,
        "core_end_i": core_end_i,
        "core_length_bars": core_end_i - core_start_i + 1,
        "confirmation_bars": int(window_end_i) - core_end_i,
        "core_start_local": local_start,
        "core_end_local": local_end,
    }


def process_prediction_batch(
    model: Any,
    tasks: Sequence[tuple[np.ndarray, Any, dict[str, Any]]],
    *,
    frame: pd.DataFrame,
    detector: Mapping[str, Any],
    device: str,
    batch_size: int,
    training_intervals: Sequence[tuple[pd.Timestamp, pd.Timestamp]],
    stats: Counter[str],
) -> list[dict[str, Any]]:
    """Infer one image batch and keep only structurally legal, non-overlap boxes."""

    predictions = model.predict(
        source=[item[0] for item in tasks],
        imgsz=int(detector["imgsz"]),
        conf=float(detector["confidence"]),
        iou=float(detector["nms_iou"]),
        batch=min(batch_size, len(tasks)),
        device=device,
        verbose=False,
    )
    if len(predictions) != len(tasks):
        raise L2ExperimentError("prediction count differs from input count")
    times = pd.to_datetime(frame["open_time"], utc=True)
    allowed_cores = set(map(int, detector["mapped_core_length_bars_allowed"]))
    allowed_posts = set(map(int, detector["mapped_confirmation_bars_allowed"]))
    hits: list[dict[str, Any]] = []
    for prediction, (input_image, transform, meta) in zip(predictions, tasks):
        stats["windows_scored"] += 1
        boxes = prediction.boxes
        if boxes is None or len(boxes) == 0:
            continue
        stats["windows_with_any_box"] += 1
        for xywhn, class_id, confidence in zip(
            boxes.xywhn.cpu().numpy(), boxes.cls.cpu().numpy(), boxes.conf.cpu().numpy()
        ):
            stats["raw_boxes"] += 1
            cid = int(class_id)
            if cid not in EXPECTED_CLASS_NAMES:
                stats["reject_unknown_class"] += 1
                continue
            mapped = map_prediction_to_core(
                cx=float(xywhn[0]),
                width=float(xywhn[2]),
                transform=transform,
                window_start_i=int(meta["window_start_i"]),
                window_end_i=int(meta["window_end_i"]),
            )
            if mapped["core_length_bars"] not in allowed_cores:
                stats["reject_core_length"] += 1
                continue
            if mapped["confirmation_bars"] not in allowed_posts:
                stats["reject_confirmation"] += 1
                continue
            window_start_time = utc(times.iloc[int(meta["window_start_i"])])
            window_end_time = utc(times.iloc[int(meta["window_end_i"])])
            if overlaps_any_interval(window_start_time, window_end_time, training_intervals):
                stats["reject_l1_training_interval_overlap"] += 1
                continue
            core = frame.iloc[mapped["core_start_i"] : mapped["core_end_i"] + 1]
            hits.append(
                {
                    **meta,
                    **mapped,
                    "window_start_time": window_start_time.isoformat(),
                    "window_end_time": window_end_time.isoformat(),
                    "available_at": (window_end_time + BAR_DELTA).isoformat(),
                    "prediction_cx_norm": float(xywhn[0]),
                    "prediction_cy_norm": float(xywhn[1]),
                    "prediction_w_norm": float(xywhn[2]),
                    "prediction_h_norm": float(xywhn[3]),
                    "input_width": int(transform.width),
                    "input_height": int(transform.height),
                    "input_n_bars": int(transform.n_bars),
                    "input_pixel_sha256": pixel_sha256(input_image),
                    "class_id": cid,
                    "class_name": EXPECTED_CLASS_NAMES[cid],
                    "side": "long" if cid == 0 else "short",
                    "confidence": float(confidence),
                    "core_start_time": utc(times.iloc[mapped["core_start_i"]]).isoformat(),
                    "core_end_time": utc(times.iloc[mapped["core_end_i"]]).isoformat(),
                    "core_high": float(core["high"].max()),
                    "core_low": float(core["low"].min()),
                }
            )
            stats["accepted_structural_boxes"] += 1
    return hits


def cluster_symbol_episodes(
    model_key: str, symbol: str, candidates: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge overlapping visible decision intervals across UTC midnight."""

    ordered = sorted(
        (dict(row) for row in candidates),
        key=lambda row: (
            int(row["core_start_i"]),
            int(row["window_end_i"]),
            -float(row["confidence"]),
            int(row["window_len"]),
        ),
    )
    clusters: list[list[dict[str, Any]]] = []
    active: list[dict[str, Any]] = []
    active_end: int | None = None
    for row in ordered:
        start_i, end_i = int(row["core_start_i"]), int(row["window_end_i"])
        if active and active_end is not None and start_i > active_end:
            clusters.append(active)
            active, active_end = [], None
        active.append(row)
        active_end = end_i if active_end is None else max(active_end, end_i)
    if active:
        clusters.append(active)
    annotated: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    for sequence, cluster in enumerate(clusters, 1):
        representative = min(
            cluster,
            key=lambda row: (
                int(row["window_end_i"]),
                -float(row["confidence"]),
                int(row["core_end_i"]),
                int(row["window_len"]),
            ),
        )
        stamp = utc(representative["available_at"])
        episode_id = (
            f"{model_key}_{symbol.replace('_USDT_SWAP', '')}_"
            f"{stamp:%Y%m%dT%H%M}_{sequence:04d}"
        )
        counts = Counter(str(row["class_name"]) for row in cluster)
        for row in cluster:
            annotated.append({**row, "episode_id": episode_id})
        episodes.append(
            {
                **representative,
                "episode_id": episode_id,
                "episode_sequence": sequence,
                "episode_candidate_count": len(cluster),
                "episode_interval_start_i": min(int(row["core_start_i"]) for row in cluster),
                "episode_interval_end_i": max(int(row["window_end_i"]) for row in cluster),
                "episode_max_confidence": max(float(row["confidence"]) for row in cluster),
                "episode_long_candidates": int(counts["dense_long"]),
                "episode_short_candidates": int(counts["dense_short"]),
                "episode_mixed_class": bool(counts["dense_long"] and counts["dense_short"]),
                "representative_rule": "earliest_window_end_then_highest_confidence",
            }
        )
    annotated.sort(key=lambda row: (int(row["window_end_i"]), int(row["window_len"])))
    for number, row in enumerate(annotated, 1):
        row["candidate_id"] = f"{model_key}_{symbol}_candidate_{number:07d}"
    return annotated, episodes


def scan_one_symbol(
    symbol: str,
    frame: pd.DataFrame,
    *,
    model: Any,
    prereg: Mapping[str, Any],
    device: str,
    batch_size: int,
    training_intervals: Sequence[tuple[pd.Timestamp, pd.Timestamp]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Scan every preregistered endpoint for one symbol without future pixels."""

    from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas
    from yoyo.layers.l1_detection.render import render_chart

    detector = prereg["l1"]
    enriched = add_mas(frame)
    times = pd.to_datetime(enriched["open_time"], utc=True)
    available = times + BAR_DELTA
    start = utc(prereg["source"]["candidate_available_at_start"])
    end = utc(prereg["source"]["candidate_available_at_end_exclusive"])
    endpoints = np.flatnonzero((available >= start) & (available < end))
    tasks: list[tuple[np.ndarray, Any, dict[str, Any]]] = []
    hits: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()

    def flush() -> None:
        if not tasks:
            return
        hits.extend(
            process_prediction_batch(
                model,
                tasks,
                frame=enriched,
                detector=detector,
                device=device,
                batch_size=batch_size,
                training_intervals=training_intervals,
                stats=stats,
            )
        )
        tasks.clear()

    for endpoint in endpoints:
        end_i = int(endpoint)
        for window_len in map(int, detector["window_lengths"]):
            start_i = end_i - window_len + 1
            if start_i < 0:
                stats["skip_insufficient_rows"] += 1
                continue
            window = enriched.iloc[start_i : end_i + 1]
            if window.loc[:, list(ALL_MA_COLS)].isna().any().any():
                stats["skip_ma_warmup"] += 1
                continue
            image, transform = render_chart(window, out_path=None)
            tasks.append(
                (
                    image,
                    transform,
                    {
                        "model_key": str(detector["key"]),
                        "symbol": symbol,
                        "inst_id": symbol.replace("_", "-"),
                        "window_len": window_len,
                        "window_start_i": start_i,
                        "window_end_i": end_i,
                    },
                )
            )
            if len(tasks) >= batch_size:
                flush()
    flush()
    annotated, episodes = cluster_symbol_episodes(str(detector["key"]), symbol, hits)
    stats["accepted_candidates"] = len(annotated)
    stats["overlap_episodes"] = len(episodes)
    stats["candidate_available_endpoints"] = len(endpoints)
    return annotated, episodes, dict(stats)


def scan_phase(
    prereg: Mapping[str, Any],
    *,
    out: Path,
    results: Path,
    source_commit: str,
    device: str,
    batch_size: int,
) -> dict[str, Any]:
    """Run the frozen detector with per-symbol receipts for safe resume."""

    terminal = results / "scan_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    immutable = verify_immutable_inputs(prereg)
    frames = load_snapshot(prereg, out=out, results=results)
    intervals = load_training_intervals(prereg)
    from ultralytics import YOLO

    model = YOLO(str(resolve_repo_path(prereg["l1"]["weights"])))
    names = {int(key): str(value) for key, value in model.names.items()}
    if names != EXPECTED_CLASS_NAMES:
        raise L2ExperimentError(f"model class names drifted: {names}")
    per_symbol = out / "scan_by_symbol"
    per_symbol.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    totals: Counter[str] = Counter()
    completed: list[dict[str, Any]] = []
    symbols = sorted(frames)
    for number, symbol in enumerate(symbols, 1):
        symbol_dir = per_symbol / symbol
        receipt_path = symbol_dir / "receipt.json"
        candidates_path = symbol_dir / "accepted_candidates.csv"
        episodes_path = symbol_dir / "episodes.csv"
        if receipt_path.exists():
            receipt = read_json(receipt_path)
            verify_declared_file(candidates_path, str(receipt["candidates_sha256"]), "symbol candidates")
            verify_declared_file(episodes_path, str(receipt["episodes_sha256"]), "symbol episodes")
            stats = dict(receipt["stats"])
        else:
            symbol_dir.mkdir(parents=True, exist_ok=True)
            candidates, episodes, stats = scan_one_symbol(
                symbol,
                frames[symbol],
                model=model,
                prereg=prereg,
                device=device,
                batch_size=batch_size,
                training_intervals=intervals.get(symbol, ()),
            )
            write_rows(candidates_path, candidates, ("symbol", "candidate_id", "episode_id"))
            write_rows(episodes_path, episodes, ("symbol", "episode_id"))
            receipt = {
                "symbol": symbol,
                "source_sha256": sha256_file(out / "snapshot" / f"{symbol}.csv"),
                "candidates": len(candidates),
                "episodes": len(episodes),
                "candidates_sha256": sha256_file(candidates_path),
                "episodes_sha256": sha256_file(episodes_path),
                "stats": stats,
            }
            write_json(receipt_path, receipt)
        totals.update({key: int(value) for key, value in stats.items()})
        completed.append(dict(receipt))
        print(
            f"scan [{number:02d}/{len(symbols):02d}] {symbol:<24} "
            f"windows={int(stats.get('windows_scored', 0)):,} "
            f"episodes={int(stats.get('overlap_episodes', 0)):,}",
            flush=True,
        )
    candidate_frames = [
        pd.read_csv(per_symbol / symbol / "accepted_candidates.csv") for symbol in symbols
    ]
    episode_frames = [pd.read_csv(per_symbol / symbol / "episodes.csv") for symbol in symbols]
    candidates = pd.concat(candidate_frames, ignore_index=True) if candidate_frames else pd.DataFrame()
    episodes = pd.concat(episode_frames, ignore_index=True) if episode_frames else pd.DataFrame()
    candidates_path = out / "accepted_candidates.csv"
    episodes_path = out / "episodes.csv"
    candidates.to_csv(candidates_path, index=False)
    episodes.to_csv(episodes_path, index=False)
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "device": device,
        "batch_size": batch_size,
        "immutable_inputs": immutable,
        "symbols": len(symbols),
        "raw_accepted_candidates": len(candidates),
        "overlap_episodes": len(episodes),
        "episode_classes": dict(sorted(Counter(episodes.get("class_name", [])).items())),
        "mixed_class_episodes": int(episodes.get("episode_mixed_class", pd.Series(dtype=bool)).astype(bool).sum()),
        "scan_totals": dict(sorted(totals.items())),
        "accepted_candidates_path": repo_relative(candidates_path),
        "accepted_candidates_sha256": sha256_file(candidates_path),
        "episodes_path": repo_relative(episodes_path),
        "episodes_sha256": sha256_file(episodes_path),
        "per_symbol": completed,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "network_reads": 0,
        "holdout_rows_read": 0,
        "training_or_tuning": False,
        "promoted": False,
        "deployed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "production_eligible": False,
    }
    write_json(terminal, payload)
    return payload


def split_name(available_at: pd.Timestamp, prereg: Mapping[str, Any]) -> str:
    """Assign one event to train/tune/final-validation or an explicit purge."""

    splits = prereg["splits"]
    for name, key in (
        ("train", "train"),
        ("tune", "tune"),
        ("final_validation", "final_preholdout_validation"),
    ):
        spec = splits[key]
        if utc(spec["available_at_start"]) <= available_at < utc(spec["available_at_end_exclusive"]):
            return name
    return "purge"


def causal_atr_quintile(atr_pct: pd.Series) -> pd.Series:
    """Bucket current ATR against only its trailing 672 bars, including itself."""

    pct = atr_pct.rolling(ATR_BUCKET_LOOKBACK, min_periods=96).rank(pct=True)
    bucket = np.ceil(pct * 5).clip(1, 5)
    return bucket.astype("Int64")


def feature_outcome_row(
    episode: Mapping[str, Any], featured: pd.DataFrame, *, prereg: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Build one causal side-aligned feature row and fixed future label."""

    from yoyo.layers.l2_judgment.features import FEATURE_COLUMNS, extract_feature_rows_for_side
    from yoyo.layers.l2_judgment.labeling import label_candidate, label_short_candidate

    signal_i = int(episode["window_end_i"])
    side = str(episode["side"])
    features = extract_feature_rows_for_side(featured, [signal_i], side).iloc[0]
    if features.isna().all():
        return None
    outcome_spec = prereg["outcome"]
    labeler = label_candidate if side == "long" else label_short_candidate
    outcome = labeler(
        featured,
        signal_i,
        tp_mult=float(outcome_spec["tp_atr_multiple"]),
        sl_mult=float(outcome_spec["sl_atr_multiple"]),
        horizon=int(outcome_spec["horizon_bars"]),
        entry="next_open",
    )
    if outcome is None:
        return None
    feature_time = utc(featured["open_time"].iloc[signal_i])
    available_at = feature_time + BAR_DELTA
    input_start_i = signal_i - L2_CONTEXT_BARS + 1
    if input_start_i < 0:
        return None
    exposure_start = utc(featured["open_time"].iloc[input_start_i])
    exposure_end_exclusive = available_at + int(outcome_spec["horizon_bars"]) * BAR_DELTA
    declared_available = utc(episode["available_at"])
    if available_at != declared_available:
        raise L2ExperimentError("available_at differs from final visible bar close")
    row = {
        "episode_id": str(episode["episode_id"]),
        "symbol": str(episode["symbol"]),
        "side": side,
        "class_id": int(episode["class_id"]),
        "feature_bar_i": signal_i,
        "feature_bar_time": feature_time.isoformat(),
        "available_at": available_at.isoformat(),
        "signal_time": available_at.isoformat(),
        "exposure_start_time": exposure_start.isoformat(),
        "exposure_end_exclusive": exposure_end_exclusive.isoformat(),
        "split": split_name(available_at, prereg),
        "l1_confidence": float(episode["confidence"]),
        "l1_episode_max_confidence": float(episode["episode_max_confidence"]),
        "window_len": int(episode["window_len"]),
        "window_start_i": int(episode["window_start_i"]),
        "window_end_i": signal_i,
        "core_start_i": int(episode["core_start_i"]),
        "core_end_i": int(episode["core_end_i"]),
        "confirmation_bars": int(episode["confirmation_bars"]),
        "prediction_cx_norm": float(episode["prediction_cx_norm"]),
        "prediction_cy_norm": float(episode["prediction_cy_norm"]),
        "prediction_w_norm": float(episode["prediction_w_norm"]),
        "prediction_h_norm": float(episode["prediction_h_norm"]),
        "input_pixel_sha256": str(episode["input_pixel_sha256"]),
        "atr_quintile": int(featured["atr_quintile"].iloc[signal_i]),
        "label": int(outcome.label),
        "outcome": str(outcome.outcome),
        "exit_offset": int(outcome.exit_offset),
        "entry_price": float(outcome.entry_price),
        "realized_ret": float(outcome.realized_ret),
        "net_ret": float(outcome.realized_ret) - float(outcome_spec["round_trip_cost_fraction"]),
    }
    row.update({column: float(features[column]) for column in FEATURE_COLUMNS})
    return row


def assign_dependency_blocks(events: pd.DataFrame) -> pd.DataFrame:
    """Assign connected full input-plus-label exposure blocks per symbol.

    Each event occupies the half-open interval from the first 168-bar context
    timestamp through the close of its 72-bar outcome path.  Directly or
    transitively overlapping intervals share a block.  Only the earliest
    available event is an independent fit/tune/evaluation representative;
    later events remain scoreable and renderable.
    """

    required = {
        "symbol",
        "episode_id",
        "available_at",
        "exposure_start_time",
        "exposure_end_exclusive",
        "split",
    }
    missing = sorted(required - set(events.columns))
    if missing:
        raise L2ExperimentError(f"dependency inputs missing columns: {missing}")
    out = events.copy()
    out["_exposure_start"] = pd.to_datetime(out["exposure_start_time"], utc=True)
    out["_exposure_end"] = pd.to_datetime(out["exposure_end_exclusive"], utc=True)
    out["_available"] = pd.to_datetime(out["available_at"], utc=True)
    out = out.sort_values(
        ["symbol", "_exposure_start", "_exposure_end", "_available", "episode_id"]
    )
    block_ids: dict[int, str] = {}
    learning = out[out["split"] != "purge"].sort_values(
        ["symbol", "_exposure_start", "_exposure_end"]
    )
    for symbol, group in learning.groupby("symbol", sort=True):
        active_end: pd.Timestamp | None = None
        active_split: str | None = None
        for _, row in group.iterrows():
            start = row["_exposure_start"]
            if active_end is not None and str(row["split"]) != active_split and start < active_end:
                raise L2ExperimentError(
                    f"full exposure crosses splits for {symbol}: "
                    f"{active_split} -> {row['split']}"
                )
            active_end = row["_exposure_end"] if active_end is None else max(
                active_end, row["_exposure_end"]
            )
            active_split = str(row["split"])

    for (symbol, split), group in out.groupby(["symbol", "split"], sort=True):
        sequence = 0
        active_end: pd.Timestamp | None = None
        active_id = ""
        for index, row in group.iterrows():
            start = row["_exposure_start"]
            end = row["_exposure_end"]
            if active_end is None or start >= active_end:
                sequence += 1
                active_id = f"{symbol}_{split}_dependency_{sequence:06d}"
                active_end = end
            else:
                active_end = max(active_end, end)
            block_ids[int(index)] = active_id
    out["dependency_block_id"] = pd.Series(block_ids)
    out["dependency_block_size"] = out.groupby("dependency_block_id")[
        "episode_id"
    ].transform("size").astype(int)
    first_indices = (
        out.sort_values(["_available", "episode_id"])
        .groupby("dependency_block_id", sort=False)
        .head(1)
        .index
    )
    out["dependency_representative"] = out.index.isin(first_indices)
    split_span = out.groupby("dependency_block_id")["split"].nunique()
    crossing = split_span[split_span > 1]
    if not crossing.empty:
        raise L2ExperimentError(
            f"dependency blocks cross time splits: {crossing.index[:10].tolist()}"
        )
    return out.drop(columns=["_exposure_start", "_exposure_end", "_available"]).sort_values(
        ["available_at", "symbol", "episode_id"]
    )


def candidate_control_pool(
    featured: pd.DataFrame,
    *,
    symbol: str,
    episode_indices: Sequence[int],
    prereg: Mapping[str, Any],
) -> dict[tuple[str, int, int], list[int]]:
    """Index final-validation control bars under the exact causal match keys."""

    from yoyo.layers.l2_judgment.features import FEATURE_COLUMNS
    from yoyo.contracts.outcomes import ATR_PCT_MIN

    times = pd.to_datetime(featured["open_time"], utc=True)
    available = times + BAR_DELTA
    spec = prereg["splits"]["final_preholdout_validation"]
    start, end = utc(spec["available_at_start"]), utc(spec["available_at_end_exclusive"])
    horizon = int(prereg["outcome"]["horizon_bars"])
    prohibited = np.zeros(len(featured), dtype=bool)
    for index in episode_indices:
        lo, hi = max(0, int(index) - 72), min(len(featured), int(index) + 73)
        prohibited[lo:hi] = True
    feature_ready = featured[list(FEATURE_COLUMNS)].notna().all(axis=1).to_numpy()
    valid = (
        (available >= start).to_numpy()
        & (available < end).to_numpy()
        & feature_ready
        & (~prohibited)
        & (featured["atr_pct"].to_numpy(dtype=float) >= float(ATR_PCT_MIN))
    )
    valid &= np.arange(len(featured)) + horizon < len(featured)
    pools: dict[tuple[str, int, int], list[int]] = defaultdict(list)
    for index in np.flatnonzero(valid):
        stamp = utc(available.iloc[int(index)])
        bucket = featured["atr_quintile"].iloc[int(index)]
        if pd.isna(bucket):
            continue
        key = (stamp.strftime("%Y-%m"), int(stamp.hour // 8), int(bucket))
        pools[key].append(int(index))
    return dict(pools)


def deterministic_control_rows(
    events: pd.DataFrame,
    featured_by_symbol: Mapping[str, pd.DataFrame],
    episode_indices_by_symbol: Mapping[str, Sequence[int]],
    *,
    prereg: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Create eight no-replacement exact-match assignments for final validation."""

    from yoyo.layers.l2_judgment.labeling import label_candidate, label_short_candidate

    if "dependency_representative" not in events:
        raise L2ExperimentError("matched controls require dependency representatives")
    final_events = events[
        (events["split"] == "final_validation")
        & events["dependency_representative"].astype(bool)
    ].copy()
    pools_by_symbol = {
        symbol: candidate_control_pool(
            featured_by_symbol[symbol],
            symbol=symbol,
            episode_indices=episode_indices_by_symbol[symbol],
            prereg=prereg,
        )
        for symbol in sorted(set(final_events["symbol"]))
    }
    outcome_spec = prereg["outcome"]
    rows: list[dict[str, Any]] = []
    assignments = int(prereg["matched_control"]["deterministic_assignments"])
    for assignment in range(assignments):
        used: set[tuple[str, int]] = set()
        order = sorted(
            final_events.to_dict("records"),
            key=lambda row: hashlib.sha256(
                f"event-order:{SEED}:{assignment}:{row['episode_id']}".encode()
            ).hexdigest(),
        )
        for event in order:
            symbol = str(event["symbol"])
            stamp = utc(event["available_at"])
            key = (stamp.strftime("%Y-%m"), int(stamp.hour // 8), int(event["atr_quintile"]))
            candidates = pools_by_symbol[symbol].get(key, [])
            ranked = sorted(
                candidates,
                key=lambda index: hashlib.sha256(
                    f"control:{SEED}:{assignment}:{event['episode_id']}:{index}".encode()
                ).hexdigest(),
            )
            chosen = next((index for index in ranked if (symbol, index) not in used), None)
            if chosen is None:
                continue
            featured = featured_by_symbol[symbol]
            side = str(event["side"])
            labeler = label_candidate if side == "long" else label_short_candidate
            outcome = labeler(
                featured,
                chosen,
                tp_mult=float(outcome_spec["tp_atr_multiple"]),
                sl_mult=float(outcome_spec["sl_atr_multiple"]),
                horizon=int(outcome_spec["horizon_bars"]),
                entry="next_open",
            )
            if outcome is None:
                continue
            used.add((symbol, chosen))
            control_time = utc(featured["open_time"].iloc[chosen]) + BAR_DELTA
            rows.append(
                {
                    "assignment": assignment,
                    "episode_id": str(event["episode_id"]),
                    "symbol": symbol,
                    "side": side,
                    "event_available_at": stamp.isoformat(),
                    "control_feature_bar_i": chosen,
                    "control_available_at": control_time.isoformat(),
                    "month": key[0],
                    "utc_8h_bucket": key[1],
                    "atr_quintile": key[2],
                    "control_label": int(outcome.label),
                    "control_outcome": str(outcome.outcome),
                    "control_realized_ret": float(outcome.realized_ret),
                    "control_net_ret": float(outcome.realized_ret)
                    - float(outcome_spec["round_trip_cost_fraction"]),
                }
            )
    return rows


def build_dataset(
    prereg: Mapping[str, Any], *, out: Path, results: Path, source_commit: str
) -> dict[str, Any]:
    """Build causal event features, outcomes and exact matched controls."""

    terminal = results / "dataset_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    immutable = verify_immutable_inputs(prereg)
    scan_receipt = read_json(results / "scan_receipt.json")
    episodes_path = resolve_repo_path(scan_receipt["episodes_path"])
    verify_declared_file(episodes_path, str(scan_receipt["episodes_sha256"]), "episode ledger")
    episodes = pd.read_csv(episodes_path)
    frames = load_snapshot(prereg, out=out, results=results)
    from yoyo.data.indicators import add_indicators
    from yoyo.layers.l2_judgment.features import FEATURE_COLUMNS, add_features

    featured_by_symbol: dict[str, pd.DataFrame] = {}
    episode_indices_by_symbol: dict[str, list[int]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for number, symbol in enumerate(sorted(frames), 1):
        featured = add_features(add_indicators(frames[symbol]))
        featured["atr_quintile"] = causal_atr_quintile(featured["atr_pct"])
        featured_by_symbol[symbol] = featured
        subset = episodes[episodes["symbol"] == symbol]
        for episode in subset.to_dict("records"):
            episode_indices_by_symbol[symbol].append(int(episode["window_end_i"]))
            try:
                row = feature_outcome_row(episode, featured, prereg=prereg)
            except (ValueError, IndexError) as exc:
                raise L2ExperimentError(f"dataset row failed for {episode['episode_id']}: {exc}") from exc
            if row is None:
                rejected["feature_or_outcome_unavailable"] += 1
                continue
            if any(not np.isfinite(float(row[column])) for column in FEATURE_COLUMNS):
                rejected["nonfinite_feature"] += 1
                continue
            rows.append(row)
        print(f"dataset [{number:02d}/{len(frames):02d}] {symbol} rows={len(rows):,}", flush=True)
    dataset = pd.DataFrame(rows).sort_values(["available_at", "symbol", "episode_id"])
    if dataset.empty:
        raise L2ExperimentError("no causal L2 rows were built")
    dataset = assign_dependency_blocks(dataset)
    if dataset["episode_id"].duplicated().any():
        raise L2ExperimentError("dataset contains duplicate episodes")
    if (pd.to_datetime(dataset["feature_bar_time"], utc=True) + BAR_DELTA != pd.to_datetime(
        dataset["available_at"], utc=True
    )).any():
        raise L2ExperimentError("feature bar/available_at causal relation drifted")
    if (pd.to_datetime(dataset["available_at"], utc=True) >= utc(prereg["source"]["holdout_start"])).any():
        raise L2ExperimentError("dataset contains holdout candidates")
    dataset_path = out / "l2_dataset.csv"
    dataset.to_csv(dataset_path, index=False)
    controls = deterministic_control_rows(
        dataset,
        featured_by_symbol,
        episode_indices_by_symbol,
        prereg=prereg,
    )
    controls_path = out / "matched_controls.csv"
    write_rows(controls_path, controls, ("assignment", "episode_id", "control_net_ret"))
    counts = Counter(dataset["split"])
    representative = dataset[dataset["dependency_representative"].astype(bool)]
    block_counts = Counter(representative["split"])
    split_ranges = {}
    for name, group in dataset.groupby("split"):
        times = pd.to_datetime(group["available_at"], utc=True)
        split_ranges[str(name)] = {
            "n": len(group),
            "start": times.min().isoformat(),
            "end": times.max().isoformat(),
        }
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "immutable_inputs": immutable,
        "scan_receipt_sha256": sha256_file(results / "scan_receipt.json"),
        "episodes_in": len(episodes),
        "rows_out": len(dataset),
        "split_counts": dict(sorted((str(key), int(value)) for key, value in counts.items())),
        "split_dependency_block_counts": dict(
            sorted((str(key), int(value)) for key, value in block_counts.items())
        ),
        "dependency_blocks": int(dataset["dependency_block_id"].nunique()),
        "dependency_representatives": int(dataset["dependency_representative"].sum()),
        "maximum_dependency_block_events": int(dataset["dependency_block_size"].max()),
        "split_ranges": split_ranges,
        "side_counts": dict(sorted(Counter(dataset["side"]).items())),
        "label_rate": float(dataset["label"].mean()),
        "reject_reasons": dict(sorted(rejected.items())),
        "feature_columns": list(FEATURE_COLUMNS),
        "feature_count": len(FEATURE_COLUMNS),
        "feature_semantics": prereg["l2"]["feature_semantics"],
        "dataset_path": repo_relative(dataset_path),
        "dataset_sha256": sha256_file(dataset_path),
        "matched_controls": len(controls),
        "matched_events": len({row["episode_id"] for row in controls}),
        "matched_controls_path": repo_relative(controls_path),
        "matched_controls_sha256": sha256_file(controls_path),
        "holdout_rows_read": 0,
        "production_eligible": False,
    }
    write_json(terminal, payload)
    return payload


def safe_metrics(y: np.ndarray, scores: np.ndarray, returns: np.ndarray, cost: float) -> dict[str, Any]:
    """Compute diagnostic and economic rank metrics without hiding small samples."""

    from scipy.stats import spearmanr
    from sklearn.metrics import average_precision_score, roc_auc_score

    n = len(scores)
    k = max(1, n // 10)
    top = np.argsort(scores)[-k:]
    classes = np.unique(y)
    auc = float(roc_auc_score(y, scores)) if len(classes) == 2 else None
    pr = float(average_precision_score(y, scores)) if len(classes) == 2 else None
    rho = spearmanr(scores, returns).statistic if n > 1 else np.nan
    return {
        "n": n,
        "positive_rate": float(np.mean(y)),
        "roc_auc": auc,
        "pr_auc": pr,
        "spearman_score_vs_return": None if not np.isfinite(rho) else float(rho),
        "pool_gross_mean": float(np.mean(returns)),
        "pool_net_mean": float(np.mean(returns) - cost),
        "top_decile": {
            "n": len(top),
            "gross_mean": float(np.mean(returns[top])),
            "net_mean": float(np.mean(returns[top]) - cost),
            "win_rate": float(np.mean(y[top])),
        },
    }


def outcome_permutation_pvalue(
    scores: np.ndarray, returns: np.ndarray, *, n_perm: int = 10_000
) -> float:
    """Test whether the fixed score top-decile mean exceeds shuffled outcomes."""

    rng = np.random.default_rng(SEED)
    k = max(1, len(scores) // 10)
    top = np.argsort(scores)[-k:]
    observed = float(np.mean(returns[top]))
    hits = 0
    for _ in range(n_perm):
        if float(np.mean(rng.permutation(returns)[top])) >= observed:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def selected_metrics(frame: pd.DataFrame, mask: np.ndarray, cost: float) -> dict[str, Any]:
    """Summarize one frozen-threshold selected event subset."""

    chosen = frame.loc[mask]
    if chosen.empty:
        return {"n": 0, "pass_rate": 0.0, "gross_mean": None, "net_mean": None, "win_rate": None}
    return {
        "n": len(chosen),
        "pass_rate": float(len(chosen) / len(frame)),
        "gross_mean": float(chosen["realized_ret"].mean()),
        "net_mean": float(chosen["realized_ret"].mean() - cost),
        "win_rate": float(chosen["label"].mean()),
    }


def matched_control_metrics(
    validation: pd.DataFrame,
    controls: pd.DataFrame,
    selected_ids: set[str],
    *,
    required_assignments: int,
) -> dict[str, Any]:
    """Compare frozen L2 selections with every preregistered control assignment.

    An assignment with zero matched selected events is missing evidence, not a
    vacuous win.  The aggregate pass flag therefore requires all assignment IDs
    ``0..required_assignments-1`` to be present and usable before checking that
    every paired excess return is positive.
    """

    event = validation.set_index("episode_id")
    rows: list[dict[str, Any]] = []
    grouped = {
        int(assignment): group
        for assignment, group in controls.groupby("assignment")
    }
    required_ids = set(range(int(required_assignments)))
    for assignment in sorted(required_ids):
        group = grouped.get(assignment, controls.iloc[0:0])
        paired = group[group["episode_id"].isin(selected_ids)].copy()
        paired = paired[paired["episode_id"].isin(event.index)]
        if paired.empty:
            rows.append({"assignment": assignment, "n": 0})
            continue
        event_net = event.loc[paired["episode_id"], "net_ret"].to_numpy(dtype=float)
        control_net = paired["control_net_ret"].to_numpy(dtype=float)
        rows.append(
            {
                "assignment": assignment,
                "n": len(paired),
                "event_net_mean": float(event_net.mean()),
                "control_net_mean": float(control_net.mean()),
                "event_minus_control_mean": float((event_net - control_net).mean()),
            }
        )
    usable = [row for row in rows if int(row.get("n", 0)) > 0]
    usable_ids = {int(row["assignment"]) for row in usable}
    complete = usable_ids == required_ids
    return {
        "assignments": rows,
        "required_assignment_count": int(required_assignments),
        "usable_assignment_count": len(usable_ids),
        "missing_assignments": sorted(required_ids - usable_ids),
        "complete_assignment_coverage": complete,
        "all_assignments_positive": complete and all(
            float(row["event_minus_control_mean"]) > 0 for row in usable
        ),
        "mean_event_minus_control": (
            float(np.mean([row["event_minus_control_mean"] for row in usable])) if usable else None
        ),
        "minimum_pairs_per_assignment": min((int(row["n"]) for row in usable), default=0),
    }


def train_evaluate(
    prereg: Mapping[str, Any], *, out: Path, results: Path, source_commit: str
) -> dict[str, Any]:
    """Fit once on train/tune and evaluate once on final pre-holdout validation."""

    terminal = results / "training_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    dataset_receipt = read_json(results / "dataset_receipt.json")
    dataset_path = resolve_repo_path(dataset_receipt["dataset_path"])
    controls_path = resolve_repo_path(dataset_receipt["matched_controls_path"])
    verify_declared_file(dataset_path, str(dataset_receipt["dataset_sha256"]), "L2 dataset")
    verify_declared_file(
        controls_path, str(dataset_receipt["matched_controls_sha256"]), "matched controls"
    )
    data = pd.read_csv(dataset_path)
    controls = pd.read_csv(controls_path)
    data["dependency_representative"] = (
        data["dependency_representative"].astype(str).str.lower() == "true"
    )
    train_events = data[data["split"] == "train"].copy()
    tune_events = data[data["split"] == "tune"].copy()
    validation_events = data[data["split"] == "final_validation"].copy()
    train = train_events[train_events["dependency_representative"]].copy()
    tune = tune_events[tune_events["dependency_representative"]].copy()
    validation = validation_events[
        validation_events["dependency_representative"]
    ].copy()
    if min(len(train), len(tune), len(validation)) == 0:
        raise L2ExperimentError("one or more preregistered splits are empty")
    from yoyo.layers.l2_judgment.features import FEATURE_COLUMNS
    from yoyo.layers.l2_judgment.train import train_model

    model = train_model(train, tune, feature_columns=FEATURE_COLUMNS, objective="regression")
    baseline = train_model(train, tune, feature_columns=["ma_spread_pct"], objective="regression")
    tune_score = model.predict(tune[FEATURE_COLUMNS], num_iteration=model.best_iteration)
    threshold = float(np.quantile(tune_score, 0.9))
    validation_event_score = model.predict(
        validation_events[FEATURE_COLUMNS], num_iteration=model.best_iteration
    )
    scored = validation_events.copy()
    scored["l2_score"] = validation_event_score
    validation = scored[scored["dependency_representative"]].copy()
    validation_score = validation["l2_score"].to_numpy(dtype=float)
    baseline_score = baseline.predict(
        validation[["ma_spread_pct"]], num_iteration=baseline.best_iteration
    )
    cost = float(prereg["outcome"]["round_trip_cost_fraction"])
    main_metrics = safe_metrics(
        validation["label"].to_numpy(dtype=int),
        validation_score,
        validation["realized_ret"].to_numpy(dtype=float),
        cost,
    )
    baseline_metrics = safe_metrics(
        validation["label"].to_numpy(dtype=int),
        baseline_score,
        validation["realized_ret"].to_numpy(dtype=float),
        cost,
    )
    selection = validation_score >= threshold
    selection_metrics = selected_metrics(validation, selection, cost)
    selected_ids = set(validation.loc[selection, "episode_id"].astype(str))
    control_metrics = matched_control_metrics(
        validation,
        controls,
        selected_ids,
        required_assignments=int(
            prereg["matched_control"]["deterministic_assignments"]
        ),
    )
    pvalue = outcome_permutation_pvalue(
        validation_score, validation["realized_ret"].to_numpy(dtype=float)
    )
    scored["l2_threshold"] = threshold
    scored["l2_keep"] = scored["l2_score"].to_numpy(dtype=float) >= threshold
    scored_path = out / "final_validation_scored.csv"
    scored.to_csv(scored_path, index=False)
    models_dir = out / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / "l2_global_context_v1.txt"
    baseline_path = models_dir / "l2_ma_spread_baseline_v1.txt"
    model.save_model(str(model_path))
    baseline.save_model(str(baseline_path))
    importance = pd.DataFrame(
        {
            "feature": FEATURE_COLUMNS,
            "gain": model.feature_importance(importance_type="gain"),
            "split": model.feature_importance(importance_type="split"),
        }
    ).sort_values("gain", ascending=False)
    importance_path = out / "feature_importance.csv"
    importance.to_csv(importance_path, index=False)
    gate = {
        "top_decile_net_positive": bool(main_metrics["top_decile"]["net_mean"] > 0),
        "outcome_permutation_p_lt_0_01": bool(pvalue < 0.01),
        "frozen_threshold_net_positive": bool(
            selection_metrics["net_mean"] is not None and selection_metrics["net_mean"] > 0
        ),
        "minimum_30_selected_dependency_blocks": bool(selection_metrics["n"] >= 30),
        "beats_matched_controls_every_assignment": bool(
            control_metrics["all_assignments_positive"]
        ),
    }
    gate["passed"] = all(gate.values())
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "dataset_sha256": dataset_receipt["dataset_sha256"],
        "objective": "regression_gross_realized_ret",
        "feature_semantics": prereg["l2"]["feature_semantics"],
        "feature_columns": list(FEATURE_COLUMNS),
        "splits": {
            "train": len(train),
            "tune": len(tune),
            "final_validation": len(validation),
        },
        "split_event_counts": {
            "train": len(train_events),
            "tune": len(tune_events),
            "final_validation": len(validation_events),
        },
        "best_iteration": int(model.best_iteration),
        "baseline_best_iteration": int(baseline.best_iteration),
        "tune_score_q90_threshold": threshold,
        "final_validation": main_metrics,
        "final_validation_frozen_threshold": selection_metrics,
        "outcome_permutation_p": pvalue,
        "matched_control": control_metrics,
        "single_feature_baseline": baseline_metrics,
        "primary_gate": gate,
        "feature_importance_top10": importance.head(10).to_dict("records"),
        "model_path": repo_relative(model_path),
        "model_sha256": sha256_file(model_path),
        "baseline_model_path": repo_relative(baseline_path),
        "baseline_model_sha256": sha256_file(baseline_path),
        "scored_validation_path": repo_relative(scored_path),
        "scored_validation_sha256": sha256_file(scored_path),
        "feature_importance_path": repo_relative(importance_path),
        "feature_importance_sha256": sha256_file(importance_path),
        "holdout_consumed": False,
        "promoted": False,
        "deployed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "production_eligible": False,
    }
    write_json(terminal, payload)
    return payload


def normalized_box_corners(row: Mapping[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    """Recover the preserved raw YOLO rectangle in exact input pixels."""

    cx, cy, bw, bh = (
        float(row["prediction_cx_norm"]),
        float(row["prediction_cy_norm"]),
        float(row["prediction_w_norm"]),
        float(row["prediction_h_norm"]),
    )
    x0, x1 = int(round((cx - bw / 2) * width)), int(round((cx + bw / 2) * width))
    y0, y1 = int(round((cy - bh / 2) * height)), int(round((cy + bh / 2) * height))
    return (
        max(0, min(width - 1, min(x0, x1))),
        max(0, min(height - 1, min(y0, y1))),
        max(0, min(width - 1, max(x0, x1))),
        max(0, min(height - 1, max(y0, y1))),
    )


def render_global_chart(row: Mapping[str, Any], frame: pd.DataFrame) -> np.ndarray:
    """Render one decision-only 168-bar chart with the raw L1 box reprojected."""

    from yoyo.layers.l1_detection.data import add_mas
    from yoyo.layers.l1_detection.render import render_chart

    enriched = add_mas(frame)
    signal_i = int(row["feature_bar_i"])
    context_start = signal_i - L2_CONTEXT_BARS + 1
    if context_start < 0:
        raise L2ExperimentError("insufficient global context")
    context = enriched.iloc[context_start : signal_i + 1]
    chart, context_tf = render_chart(context, width=1920, height=1113, out_path=None)
    input_window = enriched.iloc[int(row["window_start_i"]) : signal_i + 1]
    exact_input, input_tf = render_chart(input_window, out_path=None)
    if pixel_sha256(exact_input) != str(row["input_pixel_sha256"]):
        raise L2ExperimentError(f"L1 input pixel parity failed for {row['episode_id']}")
    raw_x0, raw_y0, raw_x1, raw_y1 = normalized_box_corners(
        row, input_tf.width, input_tf.height
    )

    def inverse_x(pixel: int) -> float:
        return (pixel - input_tf.left) / input_tf.plot_w * (input_tf.n_bars - 1)

    def inverse_y(pixel: int) -> float:
        return input_tf.price_max - (pixel - input_tf.top) / input_tf.plot_h * (
            input_tf.price_max - input_tf.price_min
        )

    global_x0 = int(row["window_start_i"]) + inverse_x(raw_x0)
    global_x1 = int(row["window_start_i"]) + inverse_x(raw_x1)
    local_x0, local_x1 = global_x0 - context_start, global_x1 - context_start
    x0 = int(round(context_tf.left + local_x0 / (context_tf.n_bars - 1) * context_tf.plot_w))
    x1 = int(round(context_tf.left + local_x1 / (context_tf.n_bars - 1) * context_tf.plot_w))
    y0, y1 = context_tf.y_at(inverse_y(raw_y0)), context_tf.y_at(inverse_y(raw_y1))
    canvas = np.full((1250, 1920, 3), 255, dtype=np.uint8)
    canvas[137:, :] = chart
    color = CLASS_COLORS[int(row["class_id"])]
    cv2.rectangle(canvas, (x0, y0 + 137), (x1, y1 + 137), color, 5, cv2.LINE_AA)
    state = "KEEP" if bool(row["l2_keep"]) else "REJECT"
    title = (
        f"L2 {state} | {row['symbol']} | {str(row['side']).upper()} | "
        f"available {utc(row['available_at']):%Y-%m-%d %H:%M} UTC"
    )
    detail = (
        f"score={float(row['l2_score']):.6f} threshold={float(row['l2_threshold']):.6f} "
        f"L1_conf={float(row['l1_confidence']):.3f} | 168 closed bars, no future outcome shown"
    )
    cv2.putText(canvas, title, (28, 48), cv2.FONT_HERSHEY_SIMPLEX, 0.92, (20, 20, 20), 2, cv2.LINE_AA)
    cv2.putText(canvas, detail, (28, 94), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (60, 60, 60), 1, cv2.LINE_AA)
    return canvas


def stratified_examples(frame: pd.DataFrame, *, keep: bool, limit: int = 50) -> pd.DataFrame:
    """Select balanced LONG/SHORT decision examples without manual deletion."""

    subset = frame[frame["l2_keep"].astype(bool) == keep].copy()
    if keep:
        subset = subset.sort_values(["l2_score", "l1_confidence"], ascending=False)
    else:
        subset = subset.sort_values(["l1_confidence", "l2_score"], ascending=[False, True])
    per_side = max(1, limit // 2)
    chosen = pd.concat(
        [subset[subset["side"] == side].head(per_side) for side in ("long", "short")],
        ignore_index=True,
    )
    if len(chosen) < limit:
        remaining = subset[~subset["episode_id"].isin(chosen["episode_id"])]
        chosen = pd.concat([chosen, remaining.head(limit - len(chosen))], ignore_index=True)
    return chosen.head(limit)


def render_phase(prereg: Mapping[str, Any], *, out: Path, results: Path) -> dict[str, Any]:
    """Render kept/rejected final-validation decisions as browseable individual PNGs."""

    terminal = results / "render_receipt.json"
    if terminal.exists():
        return read_json(terminal)
    training = read_json(results / "training_receipt.json")
    scored_path = resolve_repo_path(training["scored_validation_path"])
    verify_declared_file(scored_path, str(training["scored_validation_sha256"]), "scored validation")
    scored = pd.read_csv(scored_path)
    scored["l2_keep"] = scored["l2_keep"].astype(str).str.lower().map({"true": True, "false": False})
    frames = load_snapshot(prereg, out=out, results=results)
    charts_dir = out / "charts"
    if charts_dir.exists():
        raise FileExistsError(f"refusing to replace charts: {charts_dir}")
    rows: list[dict[str, Any]] = []
    for group, keep in (("kept", True), ("rejected_high_l1", False)):
        selected = stratified_examples(scored, keep=keep, limit=50)
        for number, row in enumerate(selected.to_dict("records"), 1):
            image = render_global_chart(row, frames[str(row["symbol"])])
            filename = (
                f"{number:03d}_{row['symbol']}_{str(row['side']).upper()}_"
                f"{utc(row['available_at']):%Y%m%dT%H%M}_{row['episode_id'][-8:]}.png"
            )
            path = charts_dir / group / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise L2ExperimentError(f"could not write chart: {path}")
            rows.append(
                {
                    "group": group,
                    "display_order": number,
                    "episode_id": str(row["episode_id"]),
                    "symbol": str(row["symbol"]),
                    "side": str(row["side"]),
                    "available_at": str(row["available_at"]),
                    "l1_confidence": float(row["l1_confidence"]),
                    "l2_score": float(row["l2_score"]),
                    "l2_threshold": float(row["l2_threshold"]),
                    "l2_keep": bool(keep),
                    "chart_path": repo_relative(path),
                    "chart_png_sha256": sha256_file(path),
                    "chart_pixel_sha256": pixel_sha256(image),
                }
            )
    ledger = out / "chart_manifest.csv"
    write_rows(ledger, rows, ("group", "episode_id", "chart_path"))
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "charts": len(rows),
        "groups": dict(sorted(Counter(row["group"] for row in rows).items())),
        "manifest_path": repo_relative(ledger),
        "manifest_sha256": sha256_file(ledger),
        "future_outcome_pixels": 0,
        "manual_deletion": False,
        "production_eligible": False,
    }
    write_json(terminal, payload)
    return payload


def verify_phase(prereg: Mapping[str, Any], *, out: Path, results: Path) -> dict[str, Any]:
    """Recreate every delivered chart and verify causal and byte lineage."""

    render_receipt = read_json(results / "render_receipt.json")
    manifest_path = resolve_repo_path(render_receipt["manifest_path"])
    verify_declared_file(manifest_path, str(render_receipt["manifest_sha256"]), "chart manifest")
    training = read_json(results / "training_receipt.json")
    scored = pd.read_csv(resolve_repo_path(training["scored_validation_path"]))
    scored_by_id = {str(row["episode_id"]): row for row in scored.to_dict("records")}
    frames = load_snapshot(prereg, out=out, results=results)
    manifest = pd.read_csv(manifest_path)
    failures: list[str] = []
    for row in manifest.to_dict("records"):
        source = scored_by_id[str(row["episode_id"])]
        source["l2_keep"] = str(source["l2_keep"]).lower() == "true"
        image = render_global_chart(source, frames[str(source["symbol"])])
        chart_path = resolve_repo_path(row["chart_path"])
        if sha256_file(chart_path) != str(row["chart_png_sha256"]):
            failures.append(f"png:{row['episode_id']}")
        if pixel_sha256(image) != str(row["chart_pixel_sha256"]):
            failures.append(f"pixels:{row['episode_id']}")
        if utc(source["feature_bar_time"]) + BAR_DELTA != utc(source["available_at"]):
            failures.append(f"clock:{row['episode_id']}")
        if utc(source["available_at"]) >= utc(prereg["source"]["holdout_start"]):
            failures.append(f"holdout:{row['episode_id']}")
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "charts_checked": len(manifest),
        "failures": failures,
        "passed": not failures,
        "holdout_rows_read": 0,
        "production_eligible": False,
    }
    write_json(results / "qa_receipt.json", payload)
    if failures:
        raise L2ExperimentError(f"chart QA failed: {failures[:10]}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    phases = parser.add_mutually_exclusive_group(required=True)
    phases.add_argument("--freeze-snapshot", action="store_true")
    phases.add_argument("--scan", action="store_true")
    phases.add_argument("--build-dataset", action="store_true")
    phases.add_argument("--train-evaluate", action="store_true")
    phases.add_argument("--render", action="store_true")
    phases.add_argument("--verify", action="store_true")
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--device", default="0")
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--replicated-source-commit", default=None)
    args = parser.parse_args()
    if args.batch <= 0:
        raise SystemExit("--batch must be positive")
    prereg_path = args.prereg.resolve()
    prereg = load_preregistration(prereg_path)
    source_commit = committed_source_identity(
        prereg_path, replicated_commit=args.replicated_source_commit
    )
    args.out.mkdir(parents=True, exist_ok=True)
    args.results.mkdir(parents=True, exist_ok=True)
    if args.freeze_snapshot:
        payload = freeze_snapshot(
            prereg, out=args.out, results=args.results, source_commit=source_commit
        )
    elif args.scan:
        payload = scan_phase(
            prereg,
            out=args.out,
            results=args.results,
            source_commit=source_commit,
            device=str(args.device),
            batch_size=int(args.batch),
        )
    elif args.build_dataset:
        payload = build_dataset(
            prereg, out=args.out, results=args.results, source_commit=source_commit
        )
    elif args.train_evaluate:
        payload = train_evaluate(
            prereg, out=args.out, results=args.results, source_commit=source_commit
        )
    elif args.render:
        payload = render_phase(prereg, out=args.out, results=args.results)
    else:
        payload = verify_phase(prereg, out=args.out, results=args.results)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
