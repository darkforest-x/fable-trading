#!/usr/bin/env python3
"""Compare five frozen 15m MA-launch YOLO checkpoints on one full-universe snapshot.

The owner requested a visual comparison of every detector discussed in the
current conversation.  This program deliberately separates the only network
phase from the five inference phases:

* ``--fetch`` discovers the current-live OKX crypto USDT-SWAP universe once,
  downloads a bounded confirmed 15m history for every eligible instrument, and
  freezes hashes under ``analysis/output``;
* ``--scan`` loads that immutable snapshot and runs the five fixed checkpoints
  with their own historical window/core/confirmation contracts;
* ``--render`` rebuilds one high-resolution, one-raw-box document per emitted
  episode locally from the snapshot and records exact input-pixel parity; and
* ``--verify`` re-renders every delivered document without network or model
  inference.

All detector inputs consist only of causal OHLCV and causal SMA/EMA 20/60/120
through the individual window endpoint.  A model's post-core confirmation bars
are part of that historical detector's original completed-shape contract; this
is a retrospective visual comparison, never a tip/tip-1/tip-2 signal, trading
claim, threshold search, training run, promotion, deployment, or order action.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

# A checked-in scanner must work both as ``python -m`` and as the explicit
# executable command recorded in its report.  Python otherwise puts only the
# ``scripts/`` directory on sys.path when this file is invoked directly.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import scan_15m_ma_launch_t3_daily_movers as common
from yoyo.layers.l1_detection.data import ALL_MA_COLS, WARMUP_BARS, add_mas
from yoyo.layers.l1_detection.render import (
    IMG_HEIGHT,
    IMG_WIDTH,
    ChartTransform,
    render_chart,
)


ROOT = REPOSITORY_ROOT
EXPERIMENT_ID = "exp-15m-ma-launch-model-compare-all3d-20260831-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "analysis" / "output" / "ma_launch_model_compare_all3d_20260831_v1"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
EXPECTED_DAYS = tuple(pd.date_range("2026-08-28", "2026-08-31", inclusive="left", freq="1D", tz="UTC"))
EXPECTED_MODEL_KEYS = (
    "legacy_t3_10k_960",
    "legacy_t3_10k_1280",
    "legacy_owner_10k_neg30k_960",
    "grade_a8k_neg24k_epoch6_960",
    "grade_a8k_neg24k_full40_1280",
)
BAR_DELTA = pd.Timedelta(minutes=15)
CONTEXT_BARS = 128
CANVAS_WIDTH = 1920
CANVAS_HEIGHT = 1400
MAIN_X = 20
MAIN_Y = 118
MAIN_WIDTH = 1880
MAIN_HEIGHT = 780
INSET_WIDTH = 700
INSET_HEIGHT = 406


class ModelCompareError(RuntimeError):
    """Fail closed on an identity, data, inference, or visual-parity violation."""


def utc(value: object) -> pd.Timestamp:
    """Normalize one timestamp to UTC."""

    stamp = pd.Timestamp(value)
    return stamp.tz_convert("UTC") if stamp.tzinfo else stamp.tz_localize("UTC")


def read_json(path: Path) -> dict[str, Any]:
    """Read one UTF-8 JSON object."""

    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a deterministic UTF-8 JSON receipt."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 file identity."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pixel_sha256(image: np.ndarray) -> str:
    """Hash exact BGR pixels rather than their PNG container bytes."""

    return hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest()


def resolve_repo_path(value: object) -> Path:
    """Resolve one preregistered repository-relative path without path escape."""

    path = (ROOT / str(value).replace("\\", "/")).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ModelCompareError(f"path escapes repository: {value}") from exc
    return path


def repo_relative(path: Path) -> str:
    """Return a portable repository-relative artifact path."""

    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def write_rows(path: Path, rows: Sequence[Mapping[str, Any]], *, empty_columns: Sequence[str]) -> None:
    """Write a deterministic CSV with an explicit empty schema."""

    columns = sorted({key for row in rows for key in row}) if rows else list(empty_columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([dict(row) for row in rows], columns=columns).to_csv(path, index=False)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """Write ordered JSONL evidence."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_png(path: Path, image: np.ndarray) -> None:
    """Write a PNG or fail instead of silently delivering no chart."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise ModelCompareError(f"could not write PNG: {path}")


def _positive_geometry(row: Mapping[str, Any]) -> tuple[int, int, int] | None:
    """Read historical positive geometry despite the two manifest schemas.

    The t-3 weak-label manifest stores geometry under ``geometry`` whereas the
    later owner datasets keep it at top level.  This is a read-only lineage
    check; it never infers a new label geometry.
    """

    if not str(row.get("sample_kind", "")).startswith("positive"):
        return None
    geometry = row.get("geometry")
    source: Mapping[str, Any] = geometry if isinstance(geometry, Mapping) else row
    try:
        window = source.get("window_len", source.get("window_bars"))
        if window is None:
            window = int(row["window_end_i"]) - int(row["window_start_i"]) + 1
        core = source.get("core_len", source.get("core_bars", row.get("core_bars")))
        post = source.get(
            "confirmation_bars",
            source.get("post_bars", source.get("post_core_context_bars", row.get("post_bars"))),
        )
        if window is None or core is None or post is None:
            return None
        return int(window), int(core), int(post)
    except (KeyError, TypeError, ValueError):
        return None


def verify_training_support(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Prove each inference contract is supported by its named positives."""

    manifest = resolve_repo_path(spec["training_manifest"])
    expected_sha = str(spec["training_manifest_sha256"])
    if not manifest.is_file() or sha256_file(manifest) != expected_sha:
        raise ModelCompareError(f"training manifest identity drifted: {spec['key']}")
    observed: list[tuple[int, int, int]] = []
    with manifest.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            geometry = _positive_geometry(json.loads(line))
            if geometry is not None:
                observed.append(geometry)
    if len(observed) != int(spec["training_positive_rows"]):
        raise ModelCompareError(
            f"positive-count drift for {spec['key']}: {len(observed)} != {spec['training_positive_rows']}"
        )
    windows = {item[0] for item in observed}
    cores = {item[1] for item in observed}
    posts = {item[2] for item in observed}
    detector = spec["detector"]
    for field, actual, allowed in (
        ("window", windows, set(map(int, detector["window_lengths"]))),
        ("core", cores, set(map(int, detector["mapped_core_length_bars_allowed"]))),
        ("confirmation", posts, set(map(int, detector["mapped_confirmation_bars_allowed"]))),
    ):
        if not allowed.issubset(actual):
            raise ModelCompareError(
                f"{spec['key']} scans unsupported {field}s: {sorted(allowed - actual)}"
            )
    return {
        "manifest": repo_relative(manifest),
        "manifest_sha256": expected_sha,
        "positive_rows": len(observed),
        "positive_windows_observed": sorted(windows),
        "positive_cores_observed": sorted(cores),
        "positive_confirmations_observed": sorted(posts),
    }


def verify_immutable_inputs(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Verify weight, renderer, and positive-manifest identities for one model."""

    weights = resolve_repo_path(spec["weights"])
    expected_weight_sha = str(spec["weights_sha256"])
    if not weights.is_file() or sha256_file(weights) != expected_weight_sha:
        raise ModelCompareError(f"weight identity drifted: {spec['key']}")
    renderer = resolve_repo_path(spec["renderer"])
    expected_renderer_sha = str(spec["renderer_sha256"])
    if not renderer.is_file() or sha256_file(renderer) != expected_renderer_sha:
        raise ModelCompareError(f"renderer identity drifted: {spec['key']}")
    return {
        "weights": {"path": repo_relative(weights), "sha256": expected_weight_sha},
        "renderer": {"path": repo_relative(renderer), "sha256": expected_renderer_sha},
        "training_support": verify_training_support(spec),
    }


def load_preregistration(path: Path = DEFAULT_PREREG) -> dict[str, Any]:
    """Load and fail closed on the exact five-model owner-authorized contract."""

    payload = read_json(path)
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise ModelCompareError("unexpected experiment id")
    auth = payload.get("owner_authorization", {})
    if auth.get("new_inference_authorized") is not True:
        raise ModelCompareError("missing explicit new-inference authorization")
    if auth.get("telegram_delivery_authorized") is not False:
        raise ModelCompareError("Telegram must remain disabled")
    for key in (
        "training_or_tuning_authorized",
        "threshold_or_weight_change_authorized",
        "production_or_promotion_authorized",
    ):
        if auth.get(key) is not False:
            raise ModelCompareError(f"unsafe authorization switch: {key}")
    days = tuple(utc(value) for value in payload["calendar"]["complete_days"])
    if days != EXPECTED_DAYS or payload["calendar"].get("current_partial_day_excluded") is not True:
        raise ModelCompareError("complete-day calendar drifted")
    universe = payload["universe"]
    if universe.get("venue") != "OKX" or universe.get("instrument_type") != "USDT-SWAP":
        raise ModelCompareError("universe identity drifted")
    if universe.get("ranking") is not None:
        raise ModelCompareError("this comparison must not rank or select a Top-N universe")
    snapshot = payload["snapshot"]
    if int(snapshot["ma_warmup_bars"]) < WARMUP_BARS:
        raise ModelCompareError("MA warmup is shorter than the renderer requires")
    models = payload.get("models")
    if not isinstance(models, list) or tuple(item.get("key") for item in models) != EXPECTED_MODEL_KEYS:
        raise ModelCompareError("model set or ordering drifted")
    for spec in models:
        detector = spec.get("detector", {})
        if float(detector.get("confidence", -1)) != 0.25 or float(detector.get("nms_iou", -1)) != 0.7:
            raise ModelCompareError(f"threshold/NMS drifted: {spec.get('key')}")
        if int(detector.get("imgsz", 0)) not in (960, 1280):
            raise ModelCompareError(f"unsupported native imgsz: {spec.get('key')}")
        if not all(int(value) > 0 for value in detector.get("window_lengths", [])):
            raise ModelCompareError(f"empty/invalid window contract: {spec.get('key')}")
        if not all(int(value) > 0 for value in detector.get("mapped_core_length_bars_allowed", [])):
            raise ModelCompareError(f"empty/invalid core contract: {spec.get('key')}")
        if not all(int(value) > 0 for value in detector.get("mapped_confirmation_bars_allowed", [])):
            raise ModelCompareError(f"empty/invalid post-core contract: {spec.get('key')}")
        if int(detector["scan_endpoint_extension_after_day_bars"]) != max(
            map(int, detector["mapped_confirmation_bars_allowed"])
        ):
            raise ModelCompareError(f"endpoint extension drifted: {spec.get('key')}")
    safety = payload.get("safety", {})
    if any(value is not False for value in safety.values()):
        raise ModelCompareError("one or more safety flags are not false")
    return payload


def verify_sources_committed(prereg_path: Path) -> str:
    """Require the scanner and preregistration to be committed before a fetch."""

    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    if branch != "main":
        raise ModelCompareError("official scan must run on main")
    paths = [Path(__file__).resolve().relative_to(ROOT), prereg_path.resolve().relative_to(ROOT)]
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise ModelCompareError(f"scanner/prereg must be committed before market reads:\n{dirty}")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if len(commit) != 40:
        raise ModelCompareError("could not resolve source commit")
    return commit


def _daily_return(frame: pd.DataFrame, day: pd.Timestamp) -> float:
    """Compute a descriptive daily return only for display; no rank is created."""

    segment = frame.loc[(frame["open_time"] >= day) & (frame["open_time"] < day + pd.Timedelta(days=1))]
    if len(segment) != 96:
        raise ModelCompareError(f"day is not exactly 96 bars: {day}")
    return float(segment.iloc[-1]["close"] / segment.iloc[0]["open"] - 1.0)


def _require_contiguous(frame: pd.DataFrame, *, start: pd.Timestamp, end: pd.Timestamp) -> None:
    """Require exactly the bounded confirmed 15m range, without an implicit gap fill."""

    expected = int((end - start) / BAR_DELTA)
    times = pd.to_datetime(frame["open_time"], utc=True)
    if len(frame) != expected or times.iloc[0] != start or times.iloc[-1] != end - BAR_DELTA:
        raise ModelCompareError(f"bounded snapshot length/time drift: got={len(frame)} expected={expected}")
    if times.duplicated().any() or not times.is_monotonic_increasing:
        raise ModelCompareError("snapshot contains duplicate or unsorted timestamps")
    if len(frame) > 1 and not (times.diff().iloc[1:] == BAR_DELTA).all():
        raise ModelCompareError("snapshot contains a non-15m gap")


def fetch_phase(
    prereg: Mapping[str, Any], *, out: Path, results: Path, workers: int, source_commit: str
) -> dict[str, Any]:
    """Freeze one all-eligible-symbol shared source snapshot from public OKX."""

    receipt_path = results / "fetch_receipt.json"
    if out.exists() or receipt_path.exists():
        raise FileExistsError("refusing to overwrite an all-universe market snapshot")
    building = out.with_name(f"{out.name}.building")
    if building.exists():
        raise FileExistsError(f"stale building path exists: {building}")
    building.mkdir(parents=True)
    snapshot_dir = building / "kline_snapshot"
    snapshot_dir.mkdir()
    try:
        tickers = list(common._request(common.TICKERS_URL).get("data") or [])  # noqa: SLF001
        instruments = list(common._request(common.INSTRUMENTS_URL).get("data") or [])  # noqa: SLF001
        eligible = common.eligible_instruments(tickers, instruments)
        days = [utc(value) for value in prereg["calendar"]["complete_days"]]
        max_window = max(
            max(map(int, spec["detector"]["window_lengths"])) for spec in prereg["models"]
        )
        max_extension = max(
            int(spec["detector"]["scan_endpoint_extension_after_day_bars"])
            for spec in prereg["models"]
        )
        history_bars = int(prereg["snapshot"]["ma_warmup_bars"]) + max_window
        start = days[0] - history_bars * BAR_DELTA
        end = days[-1] + pd.Timedelta(days=1) + max_extension * BAR_DELTA
        outcomes: dict[str, tuple[pd.DataFrame, int, str | None]] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(common.fetch_15m_frame, inst_id, start=start, end=end): inst_id
                for inst_id in eligible
            }
            for number, future in enumerate(as_completed(futures), 1):
                inst_id = futures[future]
                returned, frame, raw_rows, error = future.result()
                if returned != inst_id:
                    raise ModelCompareError("fetch identity changed in flight")
                outcomes[inst_id] = (frame, int(raw_rows), error)
                print(
                    f"fetch [{number:03d}/{len(eligible):03d}] {inst_id:<28} "
                    f"rows={len(frame):>4} {'OK' if error is None else error}",
                    flush=True,
                )
        universe_rows: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        frames_ok = 0
        for inst_id in eligible:
            frame, raw_rows, error = outcomes[inst_id]
            status = "usable"
            detail = ""
            try:
                if error is not None:
                    raise ModelCompareError(error)
                frame = frame.copy()
                frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
                for column in ("open", "high", "low", "close", "volume"):
                    frame[column] = pd.to_numeric(frame[column], errors="raise")
                frame = frame.loc[
                    (frame["open_time"] >= start) & (frame["open_time"] < end),
                    ["open_time", "open", "high", "low", "close", "volume"],
                ].reset_index(drop=True)
                _require_contiguous(frame, start=start, end=end)
                for day in days:
                    _daily_return(frame, day)
            except Exception as exc:  # noqa: BLE001 - exclusion must be receipted, never silent
                status = "excluded"
                detail = f"{type(exc).__name__}:{exc}"
            row = {
                "inst_id": inst_id,
                "symbol": inst_id.replace("-", "_"),
                "raw_rows_received": raw_rows,
                "status": status,
                "detail": detail,
            }
            if status == "usable":
                symbol = str(row["symbol"])
                path = snapshot_dir / f"{symbol}.csv"
                frame.to_csv(path, index=False)
                row.update(
                    {
                        "snapshot_path": f"kline_snapshot/{path.name}",
                        "snapshot_sha256": sha256_file(path),
                        "snapshot_rows": len(frame),
                        **{f"return_{day:%Y%m%d}": _daily_return(frame, day) for day in days},
                    }
                )
                snapshots.append(
                    {
                        "inst_id": inst_id,
                        "symbol": symbol,
                        "filename": path.name,
                        "sha256": row["snapshot_sha256"],
                        "rows": len(frame),
                    }
                )
                frames_ok += 1
            universe_rows.append(row)
        if frames_ok == 0:
            raise ModelCompareError("no current-live symbol supplied a complete snapshot")
        write_rows(building / "universe.csv", universe_rows, empty_columns=("inst_id", "status"))
        os.replace(building, out)
        universe_path = out / "universe.csv"
        receipt = {
            "protocol": prereg["protocol"],
            "experiment_id": EXPERIMENT_ID,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_commit": source_commit,
            "holdout_read_authorized_by_owner_request": True,
            "holdout_consumption_number_by_model_configuration": {
                spec["key"]: int(spec["holdout_consumption_number_for_this_configuration"])
                for spec in prereg["models"]
            },
            "complete_days": [day.isoformat() for day in days],
            "universe_definition": prereg["universe"],
            "eligible_instruments": len(eligible),
            "usable_instruments": frames_ok,
            "excluded_instruments": len(eligible) - frames_ok,
            "snapshot_start": start.isoformat(),
            "snapshot_end_exclusive": end.isoformat(),
            "required_rows_per_usable_symbol": int((end - start) / BAR_DELTA),
            "maximum_window_bars": max_window,
            "maximum_post_core_bars": max_extension,
            "universe_csv": repo_relative(universe_path),
            "universe_csv_sha256": sha256_file(universe_path),
            "snapshot_files": snapshots,
            "network_reads_during_fetch": "OKX public instruments/tickers plus bounded confirmed 15m candles only",
            "training_or_tuning": False,
            "threshold_or_weight_changed": False,
            "active_or_frozen_changed": False,
            "promoted": False,
            "deployed": False,
            "forward_state_changed": False,
            "orders_placed": False,
            "telegram_sent": False,
            "training_eligible": False,
            "production_eligible": False,
        }
        write_json(receipt_path, receipt)
        return receipt
    except Exception:
        if building.exists():
            shutil.rmtree(building)
        raise


def load_snapshot(
    prereg: Mapping[str, Any], *, out: Path, results: Path
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Load a fetch-receipted all-universe snapshot with no network path."""

    receipt_path = results / "fetch_receipt.json"
    if not receipt_path.is_file():
        raise FileNotFoundError("fetch receipt missing; run --fetch once before inference")
    receipt = read_json(receipt_path)
    if receipt.get("experiment_id") != EXPERIMENT_ID:
        raise ModelCompareError("fetch receipt experiment identity drifted")
    if [utc(value) for value in receipt["complete_days"]] != list(EXPECTED_DAYS):
        raise ModelCompareError("snapshot calendar drifted")
    universe_path = resolve_repo_path(receipt["universe_csv"])
    if sha256_file(universe_path) != str(receipt["universe_csv_sha256"]):
        raise ModelCompareError("universe receipt bytes drifted")
    frames: dict[str, pd.DataFrame] = {}
    expected_rows = int(receipt["required_rows_per_usable_symbol"])
    start = utc(receipt["snapshot_start"])
    end = utc(receipt["snapshot_end_exclusive"])
    for identity in receipt["snapshot_files"]:
        symbol = str(identity["symbol"])
        path = out / "kline_snapshot" / str(identity["filename"])
        if not path.is_file() or sha256_file(path) != str(identity["sha256"]):
            raise ModelCompareError(f"snapshot bytes drifted: {symbol}")
        frame = pd.read_csv(path)
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="raise")
        _require_contiguous(frame, start=start, end=end)
        if len(frame) != expected_rows:
            raise ModelCompareError(f"snapshot row count drifted: {symbol}")
        frames[symbol] = frame
    if len(frames) != int(receipt["usable_instruments"]):
        raise ModelCompareError("usable snapshot count drifted")
    return frames, receipt


def build_task_batches(
    frame: pd.DataFrame,
    *,
    day: pd.Timestamp,
    symbol: str,
    inst_id: str,
    detector: Mapping[str, Any],
    batch_size: int,
) -> tuple[
    pd.DataFrame,
    Iterable[list[tuple[np.ndarray, ChartTransform, dict[str, Any]]]],
    Counter[str],
]:
    """Yield bounded causal input batches shared by one geometry-equivalent group.

    A complete all-universe pass has up to 816 input windows for one
    symbol-day under the oldest detector.  Retaining that many 1280x742 BGR
    arrays consumes more than two GiB of host RAM and turns a normal scan into
    an avoidable memory-pressure failure.  This generator keeps just one
    inference batch in memory, then presents the *same exact pixel arrays* to
    all checkpoints that share the window contract before releasing them.
    """

    enriched = add_mas(frame)
    times = pd.to_datetime(enriched["open_time"], utc=True)
    extension = int(detector["scan_endpoint_extension_after_day_bars"])
    endpoint_end = day + pd.Timedelta(days=1) + extension * BAR_DELTA
    endpoint_indices = np.flatnonzero((times >= day) & (times < endpoint_end))
    daily_return = _daily_return(frame, day)
    stats: Counter[str] = Counter()
    def batches() -> Iterable[list[tuple[np.ndarray, ChartTransform, dict[str, Any]]]]:
        tasks: list[tuple[np.ndarray, ChartTransform, dict[str, Any]]] = []
        for endpoint in endpoint_indices:
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
                            "day": day.isoformat(),
                            "rank": 0,
                            "symbol": symbol,
                            "inst_id": inst_id,
                            "daily_return": daily_return,
                            "window_len": window_len,
                            "window_start_i": start_i,
                            "window_end_i": end_i,
                            "window_end_time": utc(times.iloc[end_i]).isoformat(),
                        },
                    )
                )
                if len(tasks) == batch_size:
                    yield tasks
                    tasks = []
        if tasks:
            yield tasks

    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return enriched, batches(), stats


def _group_models(models: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    """Group only models with byte-identical candidate-window generation."""

    groups: dict[tuple[tuple[int, ...], int], list[Mapping[str, Any]]] = defaultdict(list)
    for spec in models:
        detector = spec["detector"]
        key = (
            tuple(map(int, detector["window_lengths"])),
            int(detector["scan_endpoint_extension_after_day_bars"]),
        )
        groups[key].append(spec)
    return [groups[key] for key in sorted(groups)]


def cluster_episodes(
    model_key: str, candidates: Sequence[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Aggregate overlapping candidate decision intervals per day and symbol.

    This is a fixed review aggregation, not confidence suppression.  The
    representative is the first model-available raw box; later stronger boxes
    stay in the raw-candidate ledger and only describe the same episode.
    """

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[(str(candidate["day"])[:10], str(candidate["symbol"]))].append(dict(candidate))
    annotated: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    for (day, symbol), rows in sorted(grouped.items()):
        ordered = sorted(
            rows,
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
            episode_id = f"{model_key}_{day.replace('-', '')}_{symbol.replace('_USDT_SWAP', '')}_{sequence:03d}"
            class_counts = Counter(str(row["class_name"]) for row in cluster)
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
                    "episode_long_candidates": int(class_counts["dense_long"]),
                    "episode_short_candidates": int(class_counts["dense_short"]),
                    "representative_rule": "earliest_window_end_then_highest_confidence",
                }
            )
    annotated.sort(
        key=lambda row: (
            str(row["day"]),
            str(row["symbol"]),
            int(row["window_end_i"]),
            int(row["window_len"]),
            -float(row["confidence"]),
        )
    )
    for number, row in enumerate(annotated, 1):
        row["candidate_id"] = f"{model_key}_candidate_{number:07d}"
    episodes.sort(
        key=lambda row: (str(row["day"]), str(row["symbol"]), int(row["window_end_i"])))
    return annotated, episodes


def scan_phase(
    prereg: Mapping[str, Any], *, out: Path, results: Path, device: str, batch_size: int, source_commit: str
) -> dict[str, Any]:
    """Run all frozen checkpoints against identical source bytes, without rendering."""

    receipt_path = results / "scan_receipt.json"
    if receipt_path.exists():
        raise FileExistsError("refusing to overwrite a scan receipt")
    frames, fetch_receipt = load_snapshot(prereg, out=out, results=results)
    model_specs = list(prereg["models"])
    immutable = {str(spec["key"]): verify_immutable_inputs(spec) for spec in model_specs}
    from ultralytics import YOLO

    models: dict[str, Any] = {}
    for spec in model_specs:
        key = str(spec["key"])
        model = YOLO(str(resolve_repo_path(spec["weights"])))
        names = {int(class_id): str(name) for class_id, name in model.names.items()}
        if names != common.CLASS_NAMES:
            raise ModelCompareError(f"class names drifted for {key}: {names}")
        models[key] = model

    all_candidates: dict[str, list[dict[str, Any]]] = {str(spec["key"]): [] for spec in model_specs}
    all_events: dict[str, list[dict[str, Any]]] = {str(spec["key"]): [] for spec in model_specs}
    scan_rows: dict[str, list[dict[str, Any]]] = {str(spec["key"]): [] for spec in model_specs}
    stats_total: dict[str, Counter[str]] = {str(spec["key"]): Counter() for spec in model_specs}
    days = [utc(value) for value in prereg["calendar"]["complete_days"]]
    total_symbol_days = len(frames) * len(days)
    started = time.perf_counter()
    for group in _group_models(model_specs):
        group_detector = group[0]["detector"]
        for other in group[1:]:
            if tuple(other["detector"]["window_lengths"]) != tuple(group_detector["window_lengths"]):
                raise ModelCompareError("model grouping mixed window contracts")
        print(
            f"scan group {','.join(str(spec['key']) for spec in group)} "
            f"windows={list(group_detector['window_lengths'])}",
            flush=True,
        )
        number = 0
        for day in days:
            for symbol, frame in sorted(frames.items()):
                number += 1
                inst_id = symbol.replace("_", "-")
                enriched, task_batches, prep_stats = build_task_batches(
                    frame,
                    day=day,
                    symbol=symbol,
                    inst_id=inst_id,
                    detector=group_detector,
                    batch_size=batch_size,
                )
                per_model_candidates: dict[str, list[dict[str, Any]]] = {
                    str(spec["key"]): [] for spec in group
                }
                prediction_stats: dict[str, Counter[str]] = {
                    str(spec["key"]): Counter() for spec in group
                }
                for tasks in task_batches:
                    for spec in group:
                        key = str(spec["key"])
                        detector = spec["detector"]
                        candidates = common._predict_batches(  # noqa: SLF001 - shared audited mapper
                            models[key],
                            tasks,
                            batch_size=batch_size,
                            conf=float(detector["confidence"]),
                            iou=float(detector["nms_iou"]),
                            imgsz=int(detector["imgsz"]),
                            device=device,
                            day=day,
                            frame=enriched,
                            allowed_cores=set(map(int, detector["mapped_core_length_bars_allowed"])),
                            allowed_confirmations=set(map(int, detector["mapped_confirmation_bars_allowed"])),
                            stats=prediction_stats[key],
                        )
                        per_model_candidates[key].extend(
                            {**row, "model_key": key} for row in candidates
                        )
                for spec in group:
                    key = str(spec["key"])
                    detector = spec["detector"]
                    stats = Counter(prep_stats)
                    stats.update(prediction_stats[key])
                    candidates = per_model_candidates[key]
                    events = common.deduplicate_hits(
                        candidates, gap_bars=int(detector["same_symbol_event_gap_bars"])
                    )
                    stats["accepted_before_dedup"] = len(candidates)
                    stats["deduplicated_events"] = len(events)
                    stats["dedup_removed"] = len(candidates) - len(events)
                    all_candidates[key].extend(candidates)
                    all_events[key].extend(events)
                    stats_total[key].update(stats)
                    scan_rows[key].append(
                        {
                            "model_key": key,
                            "day": day.isoformat(),
                            "symbol": symbol,
                            "daily_return": _daily_return(frame, day),
                            **dict(stats),
                        }
                    )
                if number % 20 == 0 or number == total_symbol_days:
                    counts = " ".join(f"{str(spec['key'])}:{len(all_candidates[str(spec['key'])])}" for spec in group)
                    print(
                        f"scan [{number:04d}/{total_symbol_days:04d}] {day:%m-%d} {symbol:<22} candidates={counts}",
                        flush=True,
                    )
    model_receipts: dict[str, Any] = {}
    for spec in model_specs:
        key = str(spec["key"])
        model_out = out / "models" / key
        model_out.mkdir(parents=True, exist_ok=True)
        annotated, episodes = cluster_episodes(key, all_candidates[key])
        candidates_path = model_out / "accepted_candidates.csv"
        events_path = model_out / "five_bar_events.csv"
        episodes_path = model_out / "episodes.csv"
        stats_path = model_out / "scan_stats.csv"
        write_rows(candidates_path, annotated, empty_columns=("model_key", "candidate_id", "episode_id"))
        write_rows(events_path, all_events[key], empty_columns=("model_key", "symbol", "day"))
        write_rows(episodes_path, episodes, empty_columns=("model_key", "episode_id", "symbol", "day"))
        write_rows(stats_path, scan_rows[key], empty_columns=("model_key", "day", "symbol"))
        classes = Counter(str(row["class_name"]) for row in episodes)
        model_receipt = {
            "model_key": key,
            "display_name": spec["display_name"],
            "weights_sha256": spec["weights_sha256"],
            "native_imgsz": int(spec["detector"]["imgsz"]),
            "detector_contract": spec["detector"],
            "holdout_consumption_number_for_this_configuration": int(
                spec["holdout_consumption_number_for_this_configuration"]
            ),
            "immutable_inputs": immutable[key],
            "raw_candidates": len(annotated),
            "five_bar_events": len(all_events[key]),
            "overlap_episodes": len(episodes),
            "episode_classes": dict(sorted(classes.items())),
            "candidate_csv": repo_relative(candidates_path),
            "candidate_csv_sha256": sha256_file(candidates_path),
            "five_bar_events_csv": repo_relative(events_path),
            "five_bar_events_csv_sha256": sha256_file(events_path),
            "episodes_csv": repo_relative(episodes_path),
            "episodes_csv_sha256": sha256_file(episodes_path),
            "scan_stats_csv": repo_relative(stats_path),
            "scan_stats_csv_sha256": sha256_file(stats_path),
            "scan_totals": dict(sorted(stats_total[key].items())),
        }
        write_json(results / "models" / key / "scan_receipt.json", model_receipt)
        model_receipts[key] = model_receipt
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": source_commit,
        "device": device,
        "batch_size": batch_size,
        "complete_days": [day.isoformat() for day in days],
        "snapshot_fetch_receipt": repo_relative(results / "fetch_receipt.json"),
        "snapshot_fetch_receipt_sha256": sha256_file(results / "fetch_receipt.json"),
        "usable_symbol_days": total_symbol_days,
        "usable_unique_symbols": len(frames),
        "models": model_receipts,
        "wall_seconds": round(time.perf_counter() - started, 3),
        "network_reads_during_scan": 0,
        "training_or_tuning": False,
        "threshold_or_weight_changed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    write_json(receipt_path, payload)
    return payload


def normalized_box_corners(row: Mapping[str, Any]) -> tuple[int, int, int, int]:
    """Recover one preserved raw YOLO rectangle in exact 1280x742 pixels."""

    values = (
        float(row["prediction_cx_norm"]),
        float(row["prediction_cy_norm"]),
        float(row["prediction_w_norm"]),
        float(row["prediction_h_norm"]),
    )
    if not all(np.isfinite(values)) or not all(0.0 < value <= 1.0 for value in values):
        raise ModelCompareError(f"invalid normalized raw prediction: {values}")
    cx, cy, width, height = values
    x0 = int(round((cx - width / 2.0) * IMG_WIDTH))
    x1 = int(round((cx + width / 2.0) * IMG_WIDTH))
    y0 = int(round((cy - height / 2.0) * IMG_HEIGHT))
    y1 = int(round((cy + height / 2.0) * IMG_HEIGHT))
    x0, x1 = sorted((max(0, min(IMG_WIDTH - 1, x0)), max(0, min(IMG_WIDTH - 1, x1))))
    y0, y1 = sorted((max(0, min(IMG_HEIGHT - 1, y0)), max(0, min(IMG_HEIGHT - 1, y1))))
    if x1 <= x0 or y1 <= y0:
        raise ModelCompareError("raw box collapses after clipping")
    return x0, y0, x1, y1


def render_exact_input(enriched: pd.DataFrame, row: Mapping[str, Any]) -> np.ndarray:
    """Re-render the exact causal model input and prove its remote pixel hash."""

    window = enriched.iloc[int(row["window_start_i"]) : int(row["window_end_i"]) + 1]
    if len(window) != int(row["window_len"]):
        raise ModelCompareError("episode input length drifted")
    image, _ = render_chart(window, out_path=None)
    if image.shape != (IMG_HEIGHT, IMG_WIDTH, 3):
        raise ModelCompareError("renderer output dimensions drifted")
    if pixel_sha256(image) != str(row["input_pixel_sha256"]):
        raise ModelCompareError("local re-render differs from model-scored input pixels")
    return image


def draw_raw_prediction(image: np.ndarray, row: Mapping[str, Any]) -> np.ndarray:
    """Overlay exactly one original model rectangle, never a reconstructed candle box."""

    output = image.copy()
    x0, y0, x1, y1 = normalized_box_corners(row)
    cv2.rectangle(output, (x0, y0), (x1, y1), common.CLASS_COLORS[int(row["class_id"])], 4, cv2.LINE_AA)
    return output


def inverse_x(transform: ChartTransform, pixel_x: float) -> float:
    """Map one pixel x coordinate back to a fractional bar index."""

    if transform.n_bars <= 1:
        return 0.0
    return (float(pixel_x) - transform.left) / transform.plot_w * (transform.n_bars - 1)


def inverse_y(transform: ChartTransform, pixel_y: float) -> float:
    """Map one pixel y coordinate back to its source price."""

    return transform.price_max - (float(pixel_y) - transform.top) / transform.plot_h * (
        transform.price_max - transform.price_min
    )


def x_at_float(transform: ChartTransform, index: float) -> int:
    """Project a fractional bar coordinate onto a chart x coordinate."""

    if transform.n_bars <= 1:
        return transform.left
    return int(round(transform.left + float(index) / (transform.n_bars - 1) * transform.plot_w))


def project_raw_box(
    row: Mapping[str, Any], *, input_tf: ChartTransform, context_tf: ChartTransform, context_start_i: int
) -> dict[str, float | int]:
    """Inverse-project a raw detector rectangle into a full-context chart."""

    raw_x0, raw_y0, raw_x1, raw_y1 = normalized_box_corners(row)
    global_x0 = int(row["window_start_i"]) + inverse_x(input_tf, raw_x0)
    global_x1 = int(row["window_start_i"]) + inverse_x(input_tf, raw_x1)
    price_high = inverse_y(input_tf, raw_y0)
    price_low = inverse_y(input_tf, raw_y1)
    check = (
        x_at_float(input_tf, global_x0 - int(row["window_start_i"])),
        x_at_float(input_tf, global_x1 - int(row["window_start_i"])),
        input_tf.y_at(price_high),
        input_tf.y_at(price_low),
    )
    if max(abs(left - right) for left, right in zip(check, (raw_x0, raw_x1, raw_y0, raw_y1))) > 1:
        raise ModelCompareError("raw box inverse/reprojection exceeds one pixel")
    return {
        "raw_x0_px": raw_x0,
        "raw_y0_px": raw_y0,
        "raw_x1_px": raw_x1,
        "raw_y1_px": raw_y1,
        "global_x0_bar": global_x0,
        "global_x1_bar": global_x1,
        "price_high": price_high,
        "price_low": price_low,
        "context_x0_px": x_at_float(context_tf, global_x0 - context_start_i),
        "context_x1_px": x_at_float(context_tf, global_x1 - context_start_i),
        "context_y0_px": context_tf.y_at(price_high),
        "context_y1_px": context_tf.y_at(price_low),
    }


def put_text(
    image: np.ndarray,
    text: str,
    origin: tuple[int, int],
    *,
    scale: float = 0.58,
    color: tuple[int, int, int] = (28, 28, 28),
    thickness: int = 1,
) -> None:
    """Draw stable OpenCV text with anti-aliasing."""

    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def dashed_vertical(image: np.ndarray, x: int, y0: int, y1: int) -> None:
    """Draw the actual completed detection endpoint."""

    for start in range(y0, y1 + 1, 20):
        cv2.line(image, (x, start), (x, min(y1, start + 12)), (35, 35, 35), 2, cv2.LINE_AA)


def context_bounds(frame_len: int, decision_i: int) -> tuple[int, int]:
    """Center a 128-bar review around detection where history allows it."""

    if frame_len < CONTEXT_BARS:
        raise ModelCompareError("snapshot too short for full-context review")
    preferred_start = decision_i - 90
    start = max(0, min(frame_len - CONTEXT_BARS, preferred_start))
    return start, start + CONTEXT_BARS - 1


def price_text(value: float) -> str:
    """Format a price label without unreliable scientific notation."""

    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{value:,.1f}"
    if magnitude >= 10:
        return f"{value:.3f}"
    if magnitude >= 1:
        return f"{value:.4f}"
    if magnitude >= 0.01:
        return f"{value:.5f}"
    return f"{value:.7f}"


def render_episode(
    row: Mapping[str, Any], *, spec: Mapping[str, Any], order: int, total: int, enriched: pd.DataFrame
) -> tuple[np.ndarray, dict[str, Any]]:
    """Render one high-resolution whole-context record with one preserved raw box."""

    clean = render_exact_input(enriched, row)
    input_overlay = draw_raw_prediction(clean, row)
    decision_i = int(row["window_end_i"])
    context_start_i, context_end_i = context_bounds(len(enriched), decision_i)
    context = enriched.iloc[context_start_i : context_end_i + 1]
    context_times = pd.to_datetime(context["open_time"], utc=True)
    if len(context) != CONTEXT_BARS:
        raise ModelCompareError("review context length drifted")
    main, context_tf = render_chart(context, width=MAIN_WIDTH, height=MAIN_HEIGHT, out_path=None)
    model_window = enriched.iloc[int(row["window_start_i"]) : decision_i + 1]
    _input, input_tf = render_chart(model_window, out_path=None)
    if pixel_sha256(_input) != str(row["input_pixel_sha256"]):
        raise ModelCompareError("input transform source differs from actual inference image")
    projection = project_raw_box(row, input_tf=input_tf, context_tf=context_tf, context_start_i=context_start_i)
    x0, x1 = sorted((int(projection["context_x0_px"]), int(projection["context_x1_px"])))
    y0, y1 = sorted((int(projection["context_y0_px"]), int(projection["context_y1_px"])))
    x0, x1 = max(0, x0), min(MAIN_WIDTH - 1, x1)
    y0, y1 = max(0, y0), min(MAIN_HEIGHT - 1, y1)
    cv2.rectangle(main, (x0, y0), (x1, y1), common.CLASS_COLORS[int(row["class_id"])], 5, cv2.LINE_AA)
    local_decision = decision_i - context_start_i
    detection_x = x_at_float(context_tf, local_decision)
    if local_decision < CONTEXT_BARS - 1:
        shaded = main.copy()
        cv2.rectangle(shaded, (detection_x + 1, 0), (MAIN_WIDTH - 1, MAIN_HEIGHT - 1), (228, 231, 235), -1)
        main = cv2.addWeighted(shaded, 0.25, main, 0.75, 0)
    dashed_vertical(main, detection_x, 10, MAIN_HEIGHT - 18)
    put_text(main, "DETECT", (max(4, detection_x - 30), 28), scale=0.48, thickness=2)

    canvas = np.full((CANVAS_HEIGHT, CANVAS_WIDTH, 3), 247, dtype=np.uint8)
    direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
    detect_at = utc(row["window_end_time"])
    core_start, core_end = utc(row["core_start_time"]), utc(row["core_end_time"])
    put_text(
        canvas,
        f"{str(row['symbol']).replace('_USDT_SWAP', '')}USDT.P 15m | {spec['display_name']} | {order:04d}/{total:04d}",
        (24, 40),
        scale=0.70,
        thickness=2,
    )
    put_text(
        canvas,
        f"ALL current-live OKX crypto USDT-SWAP | {str(row['day'])[:10]} UTC | {direction} conf {float(row['confidence']):.3f} | core {core_start:%m-%d %H:%M}..{core_end:%H:%M} UTC | detect {detect_at:%m-%d %H:%M} UTC",
        (24, 75),
        scale=0.46,
        color=(60, 60, 60),
    )
    put_text(
        canvas,
        f"one merged episode / one preserved raw four-coordinate box | W{int(row['window_len'])}, core{int(row['core_length_bars'])}, post{int(row['confirmation_bars'])} | grey bars are review-only after DETECT",
        (24, 105),
        scale=0.46,
        color=(85, 85, 85),
    )
    canvas[MAIN_Y : MAIN_Y + MAIN_HEIGHT, MAIN_X : MAIN_X + MAIN_WIDTH] = main
    for local_i in (0, 24, 48, 72, 96, 127):
        x = MAIN_X + x_at_float(context_tf, local_i)
        stamp = utc(context_times.iloc[local_i])
        put_text(canvas, f"{stamp:%m-%d %H:%M}", (max(0, x - 50), MAIN_Y + MAIN_HEIGHT + 25), scale=0.42, color=(80, 80, 80))
    for fraction in np.linspace(0.05, 0.95, 5):
        price = context_tf.price_max - fraction * (context_tf.price_max - context_tf.price_min)
        y = MAIN_Y + int(round(context_tf.top + fraction * context_tf.plot_h))
        put_text(canvas, price_text(price), (CANVAS_WIDTH - 118, y), scale=0.42, color=(75, 75, 75))
    footer_y = 944
    put_text(canvas, "HOW TO READ", (28, footer_y), scale=0.66, thickness=2)
    put_text(canvas, "Top: 128 consecutive 15m candles. Colored rectangle is the actual detector raw box, inverse-projected without redrawing.", (28, footer_y + 35), scale=0.45)
    put_text(canvas, "Dashed DETECT marks the completed model input right edge. Right: exact 1280x742 image scored by this model.", (28, footer_y + 65), scale=0.45)
    put_text(canvas, "This is completed-history pattern retrieval only, not a live trade signal or an outcome label.", (28, footer_y + 95), scale=0.45, color=(85, 85, 85))
    put_text(canvas, "EXACT MODEL INPUT", (CANVAS_WIDTH - INSET_WIDTH - 18, footer_y), scale=0.60, thickness=2)
    inset = cv2.resize(input_overlay, (INSET_WIDTH, INSET_HEIGHT), interpolation=cv2.INTER_AREA)
    inset_x, inset_y = CANVAS_WIDTH - INSET_WIDTH - 18, footer_y + 18
    canvas[inset_y : inset_y + INSET_HEIGHT, inset_x : inset_x + INSET_WIDTH] = inset
    cv2.rectangle(canvas, (inset_x, inset_y), (inset_x + INSET_WIDTH - 1, inset_y + INSET_HEIGHT - 1), (65, 65, 65), 2)
    metadata = {
        **projection,
        "event_order": order,
        "events_total": total,
        "model_key": str(spec["key"]),
        "display_name": str(spec["display_name"]),
        "episode_id": str(row["episode_id"]),
        "episode_candidate_count": int(row["episode_candidate_count"]),
        "day": str(row["day"]),
        "symbol": str(row["symbol"]),
        "class_id": int(row["class_id"]),
        "class_name": str(row["class_name"]),
        "confidence": float(row["confidence"]),
        "window_len": int(row["window_len"]),
        "window_start_i": int(row["window_start_i"]),
        "window_end_i": decision_i,
        "window_end_time": detect_at.isoformat(),
        "core_start_i": int(row["core_start_i"]),
        "core_end_i": int(row["core_end_i"]),
        "core_start_time": core_start.isoformat(),
        "core_end_time": core_end.isoformat(),
        "core_length_bars": int(row["core_length_bars"]),
        "confirmation_bars": int(row["confirmation_bars"]),
        "context_start_i": context_start_i,
        "context_end_i": context_end_i,
        "context_bars": CONTEXT_BARS,
        "post_detection_review_bars": context_end_i - decision_i,
        "model_input_pixel_sha256": pixel_sha256(clean),
        "boxes_per_document": 1,
        "canvas_width": CANVAS_WIDTH,
        "canvas_height": CANVAS_HEIGHT,
    }
    return canvas, metadata


def _load_model_episodes(out: Path, spec: Mapping[str, Any], expected_sha: str) -> list[dict[str, Any]]:
    """Load an exact scan ledger and verify it matches its per-model receipt."""

    key = str(spec["key"])
    receipt = read_json(ROOT / "experiments" / "active" / EXPERIMENT_ID / "results" / "models" / key / "scan_receipt.json")
    if str(receipt["episodes_csv_sha256"]) != expected_sha:
        raise ModelCompareError(f"scan receipt/aggregate identity drift: {key}")
    path = out / "models" / key / "episodes.csv"
    if sha256_file(path) != expected_sha:
        raise ModelCompareError(f"episode bytes drifted: {key}")
    frame = pd.read_csv(path)
    return frame.to_dict("records")


def _pairwise_overlap(left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Greedily match same-day/symbol episode cores within one 15m bar.

    A pair can be timing-similar even if it predicts opposite directions; that
    distinction is reported rather than discarded.  The statistic is descriptive
    proposal stability, not a label-quality or profitability score.
    """

    right_by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in right:
        right_by_key[(str(row["day"])[:10], str(row["symbol"]))].append(dict(row))
    used: set[tuple[str, str, int]] = set()
    matches = same_direction = direction_flip = 0
    for row in sorted(left, key=lambda item: (str(item["day"]), str(item["symbol"]), int(item["core_end_i"]))):
        key = (str(row["day"])[:10], str(row["symbol"]))
        options = [
            other
            for other in right_by_key[key]
            if (key[0], key[1], int(other["episode_sequence"])) not in used
            and abs(int(row["core_end_i"]) - int(other["core_end_i"])) <= 1
        ]
        if not options:
            continue
        best = min(options, key=lambda other: (abs(int(row["core_end_i"]) - int(other["core_end_i"])), -float(other["confidence"])))
        used.add((key[0], key[1], int(best["episode_sequence"])))
        matches += 1
        if int(row["class_id"]) == int(best["class_id"]):
            same_direction += 1
        else:
            direction_flip += 1
    union = len(left) + len(right) - matches
    return {
        "left_episodes": len(left),
        "right_episodes": len(right),
        "time_matched_within_one_bar": matches,
        "same_direction_matches": same_direction,
        "direction_flip_matches": direction_flip,
        "proposal_jaccard": matches / union if union else 1.0,
    }


def build_comparison_summary(prereg: Mapping[str, Any], *, out: Path, results: Path) -> dict[str, Any]:
    """Build one deterministic comparison table from the five emitted ledgers."""

    scan = read_json(results / "scan_receipt.json")
    if scan.get("experiment_id") != EXPERIMENT_ID:
        raise ModelCompareError("aggregate scan receipt identity drifted")
    summaries: list[dict[str, Any]] = []
    episodes_by_key: dict[str, list[dict[str, Any]]] = {}
    for spec in prereg["models"]:
        key = str(spec["key"])
        receipt = scan["models"][key]
        episodes = _load_model_episodes(out, spec, str(receipt["episodes_csv_sha256"]))
        episodes_by_key[key] = episodes
        counts = Counter(str(row["class_name"]) for row in episodes)
        summaries.append(
            {
                "model_key": key,
                "display_name": spec["display_name"],
                "native_imgsz": int(spec["detector"]["imgsz"]),
                "windows": ",".join(map(str, spec["detector"]["window_lengths"])),
                "cores": ",".join(map(str, spec["detector"]["mapped_core_length_bars_allowed"])),
                "confirmations": ",".join(map(str, spec["detector"]["mapped_confirmation_bars_allowed"])),
                "raw_candidates": int(receipt["raw_candidates"]),
                "five_bar_events": int(receipt["five_bar_events"]),
                "episodes": len(episodes),
                "long_episodes": int(counts["dense_long"]),
                "short_episodes": int(counts["dense_short"]),
                "symbol_days_with_episodes": len({(str(row["day"]), str(row["symbol"])) for row in episodes}),
            }
        )
    pairs: list[dict[str, Any]] = []
    for left_index, left_key in enumerate(EXPECTED_MODEL_KEYS):
        for right_key in EXPECTED_MODEL_KEYS[left_index + 1 :]:
            pairs.append(
                {
                    "left_model_key": left_key,
                    "right_model_key": right_key,
                    **_pairwise_overlap(episodes_by_key[left_key], episodes_by_key[right_key]),
                }
            )
    summary_path = results / "model_summary.csv"
    pairs_path = results / "pairwise_episode_overlap.csv"
    write_rows(summary_path, summaries, empty_columns=("model_key",))
    write_rows(pairs_path, pairs, empty_columns=("left_model_key", "right_model_key"))
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary_csv": repo_relative(summary_path),
        "summary_csv_sha256": sha256_file(summary_path),
        "pairwise_overlap_csv": repo_relative(pairs_path),
        "pairwise_overlap_csv_sha256": sha256_file(pairs_path),
        "models": summaries,
        "pairwise_overlap": pairs,
        "economic_backtest": False,
        "interpretation": "Descriptive proposal output comparison only; different historical window/post-core contracts remain a material confound.",
    }
    write_json(results / "comparison_summary.json", payload)
    return payload


def render_phase(prereg: Mapping[str, Any], *, out: Path, results: Path) -> dict[str, Any]:
    """Render complete per-model episode galleries and a comparison summary locally."""

    if (results / "render_receipt.json").exists() or (results / "charts").exists():
        raise FileExistsError("refusing to overwrite rendered comparison outputs")
    frames, _fetch = load_snapshot(prereg, out=out, results=results)
    scan = read_json(results / "scan_receipt.json")
    summary = build_comparison_summary(prereg, out=out, results=results)
    charts_building = results / "charts.building"
    charts_building.mkdir(parents=True)
    all_manifests: dict[str, list[dict[str, Any]]] = {}
    try:
        for spec in prereg["models"]:
            key = str(spec["key"])
            episodes = _load_model_episodes(out, spec, str(scan["models"][key]["episodes_csv_sha256"]))
            folder = charts_building / key
            folder.mkdir()
            manifest: list[dict[str, Any]] = []
            for order, row in enumerate(episodes, 1):
                symbol = str(row["symbol"])
                image, metadata = render_episode(row, spec=spec, order=order, total=len(episodes), enriched=add_mas(frames[symbol]))
                direction = "LONG" if int(row["class_id"]) == 0 else "SHORT"
                path = folder / (
                    f"{order:04d}_{symbol.replace('_USDT_SWAP', '')}USDT_P_{direction}_{utc(row['window_end_time']):%Y%m%dT%H%MZ}.png"
                )
                write_png(path, image)
                metadata.update(
                    {
                        "image_path": repo_relative(results / "charts" / key / path.name),
                        "image_sha256": sha256_file(path),
                        "image_size_bytes": path.stat().st_size,
                    }
                )
                manifest.append(metadata)
                if order % 50 == 0 or order == len(episodes):
                    print(f"render {key} [{order:04d}/{len(episodes):04d}]", flush=True)
            all_manifests[key] = manifest
        os.replace(charts_building, results / "charts")
    except Exception:
        if charts_building.exists():
            shutil.rmtree(charts_building)
        raise
    bundle_rows: list[dict[str, Any]] = []
    for spec in prereg["models"]:
        key = str(spec["key"])
        manifest = all_manifests[key]
        model_results = results / "models" / key
        manifest_jsonl = model_results / "render_manifest.jsonl"
        manifest_csv = model_results / "render_manifest.csv"
        write_jsonl(manifest_jsonl, manifest)
        write_rows(manifest_csv, manifest, empty_columns=("event_order", "episode_id", "image_path"))
        archive_path = model_results / "all_signal_charts.zip"
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(manifest_jsonl, manifest_jsonl.name)
            archive.write(manifest_csv, manifest_csv.name)
            for chart in sorted((results / "charts" / key).glob("*.png")):
                archive.write(chart, f"charts/{chart.name}")
        bundle_rows.append(
            {
                "model_key": key,
                "documents": len(manifest),
                "manifest_jsonl": repo_relative(manifest_jsonl),
                "manifest_sha256": sha256_file(manifest_jsonl),
                "archive": repo_relative(archive_path),
                "archive_sha256": sha256_file(archive_path),
                "archive_size_bytes": archive_path.stat().st_size,
            }
        )
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "comparison_summary_sha256": sha256_file(results / "comparison_summary.json"),
        "models": bundle_rows,
        "documents_total": sum(item["documents"] for item in bundle_rows),
        "network_reads_during_render": 0,
        "model_inference_during_render": 0,
        "training_or_tuning": False,
        "threshold_or_weight_changed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    write_json(results / "render_receipt.json", payload)
    return {**payload, "comparison_summary": summary}


def verify_phase(prereg: Mapping[str, Any], *, out: Path, results: Path) -> dict[str, Any]:
    """Prove every delivered chart reproduces exactly from its frozen episode."""

    if not (results / "render_receipt.json").is_file():
        raise FileNotFoundError("render receipt missing; run --render before --verify")
    frames, _fetch = load_snapshot(prereg, out=out, results=results)
    exact_inputs = exact_rerenders = exact_pngs = 0
    unique_images: set[str] = set()
    per_model: dict[str, Any] = {}
    for spec in prereg["models"]:
        key = str(spec["key"])
        manifest_path = results / "models" / key / "render_manifest.jsonl"
        rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line]
        source_rows = pd.read_csv(out / "models" / key / "episodes.csv")
        model_inputs = model_rerenders = model_pngs = 0
        for order, row in enumerate(rows, 1):
            if int(row["event_order"]) != order or int(row["boxes_per_document"]) != 1:
                raise ModelCompareError(f"manifest order/box count drifted: {key}")
            selected = source_rows.loc[source_rows["episode_id"] == row["episode_id"]]
            if len(selected) != 1:
                raise ModelCompareError(f"source episode identity drifted: {key}")
            source = selected.iloc[0].to_dict()
            image, metadata = render_episode(
                source,
                spec=spec,
                order=order,
                total=len(rows),
                enriched=add_mas(frames[str(source["symbol"])]),
            )
            path = resolve_repo_path(row["image_path"])
            actual = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if actual is None or actual.shape != (CANVAS_HEIGHT, CANVAS_WIDTH, 3):
                raise ModelCompareError(f"missing/wrong-shape chart: {path}")
            if not np.array_equal(actual, image):
                raise ModelCompareError(f"pixel rerender drifted: {path}")
            digest = sha256_file(path)
            if digest != str(row["image_sha256"]):
                raise ModelCompareError(f"PNG hash drifted: {path}")
            if str(metadata["model_input_pixel_sha256"]) != str(row["model_input_pixel_sha256"]):
                raise ModelCompareError(f"model input pixel identity drifted: {key}")
            unique_images.add(digest)
            model_inputs += 1
            model_rerenders += 1
            model_pngs += 1
        exact_inputs += model_inputs
        exact_rerenders += model_rerenders
        exact_pngs += model_pngs
        per_model[key] = {
            "documents": len(rows),
            "exact_model_inputs": model_inputs,
            "exact_pixel_rerenders": model_rerenders,
            "exact_png_hash_matches": model_pngs,
        }
    if len(unique_images) != exact_pngs:
        raise ModelCompareError("rendered chart PNGs are not globally unique")
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "per_model": per_model,
        "exact_model_inputs": exact_inputs,
        "exact_pixel_rerenders": exact_rerenders,
        "exact_png_hash_matches": exact_pngs,
        "unique_chart_pngs": len(unique_images),
        "network_reads_during_verification": 0,
        "model_inference_during_verification": 0,
        "training_or_tuning": False,
        "threshold_or_weight_changed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    write_json(results / "qa_receipt.json", payload)
    return payload


def main() -> int:
    """Dispatch exactly one explicit phase."""

    parser = argparse.ArgumentParser(description=__doc__)
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--fetch", action="store_true")
    phase.add_argument("--scan", action="store_true")
    phase.add_argument("--render", action="store_true")
    phase.add_argument("--verify", action="store_true")
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--worker-source-commit",
        default=None,
        help="Only for a disposable 3060 bundle: record the already-verified Mac source commit.",
    )
    args = parser.parse_args()
    if args.workers < 1 or args.batch_size < 1:
        parser.error("--workers and --batch-size must be positive")
    prereg_path = args.prereg.resolve()
    prereg = load_preregistration(prereg_path)
    if args.worker_source_commit is not None:
        source_commit = str(args.worker_source_commit)
        if len(source_commit) != 40 or any(char not in "0123456789abcdef" for char in source_commit):
            parser.error("--worker-source-commit must be a lowercase 40-character SHA")
        if args.fetch:
            parser.error("a disposable worker may never perform the network fetch")
    else:
        source_commit = verify_sources_committed(prereg_path)
    if args.fetch:
        fetch_phase(prereg, out=args.out.resolve(), results=args.results.resolve(), workers=args.workers, source_commit=source_commit)
    elif args.scan:
        device = common.choose_device(args.device)
        scan_phase(prereg, out=args.out.resolve(), results=args.results.resolve(), device=device, batch_size=args.batch_size, source_commit=source_commit)
    elif args.render:
        render_phase(prereg, out=args.out.resolve(), results=args.results.resolve())
    else:
        verify_phase(prereg, out=args.out.resolve(), results=args.results.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
