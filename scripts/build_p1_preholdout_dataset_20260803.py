#!/usr/bin/env python3
"""Build the immutable P1 pre-holdout short L2 dataset, never a model.

Stages are deliberately gated: ``fixture`` writes synthetic canonical evidence;
``dry-run`` replays a few real pre-holdout detector windows twice; ``full`` is
allowed only when both machine-readable gates pass.  The full stage checkpoints
complete per-symbol shards under ``data/p1/_staging`` and atomically publishes a
content-addressed CSV plus manifest after all 344 live-universe symbols finish.

No command imports a trainer, reads an ACTIVE model, creates an active bundle,
deploys, notifies, or calls an exchange client.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

# On macOS/arm64 this must happen before pandas/numpy initialize their native
# pools.  Delaying it until ``run_dry`` reproducibly SIGSEGVs inside detector
# inference even though the exact same image/model succeeds in a clean process.
import cv2
import torch

cv2.setNumThreads(0)
torch.set_num_threads(1)
try:
    torch.set_num_interop_threads(1)
except RuntimeError:
    pass

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from src.detection.render import ChartTransform, render_chart  # noqa: E402
from src.judgment.candidates import add_indicators  # noqa: E402
from src.judgment.features import (  # noqa: E402
    FEATURE_COLUMNS,
    add_features,
    extract_feature_rows_for_semantics,
)
from src.judgment.p1_build import (  # noqa: E402
    detect_historical_windows,
    normalized_boxes,
    select_live_parity_observations,
)
from src.judgment.p1_dataset import (  # noqa: E402
    CandidateObservation,
    DATASET_COLUMNS,
    FEATURE_SCHEMA,
    FEATURE_SEMANTICS,
    HOLDOUT_CUTOFF,
    PROTOCOL_VERSION,
    P1DatasetContractError,
    assign_event_groups,
    build_candidate_row,
    file_sha256,
    load_immutable_dataset,
    load_preholdout_candles,
    schema_sha256,
    stable_hash,
    utc_now,
    write_dataset_csv,
)
from src.judgment.yolo_candidates import (  # noqa: E402
    DEFAULT_CONF,
    TIP_EDGE_BARS,
    WINDOW,
    load_yolo_model,
    map_box_to_signal,
    resolve_default_weights,
)

BASELINE_DIR = PROJECT / "analysis/output/p1_data_baseline_20260803"
FIXTURE_AUDIT = PROJECT / "analysis/output/p1_fixture_20260803.json"
DRY_AUDIT = PROJECT / "analysis/output/p1_dry_run_20260803.json"
FULL_AUDIT = PROJECT / "analysis/output/p1_preholdout_dataset_rebuild_20260803.json"
DATA_ROOT = PROJECT / "data/p1"
SIGNAL_START = pd.Timestamp("2026-02-01T00:00:00Z")


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT, text=True).strip()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".partial")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def load_baseline() -> tuple[dict[str, Any], dict[str, Any]]:
    environment = json.loads((BASELINE_DIR / "environment.json").read_text(encoding="utf-8"))
    raw = json.loads((BASELINE_DIR / "raw_inputs.json").read_text(encoding="utf-8"))
    if environment["safety"]["post_cutoff_ohlcv_rows_materialized"] != 0:
        raise P1DatasetContractError("baseline is not pre-holdout safe")
    if environment["universe"]["research_symbol_count"] != 344:
        raise P1DatasetContractError("baseline universe is not the live 344-symbol universe")
    if raw["holdout_cutoff"].replace("+00:00", "Z") != "2026-05-04T00:00:00Z":
        raise P1DatasetContractError("baseline cutoff mismatch")
    return environment, raw


def detector_identity(environment: dict[str, Any]) -> tuple[Path, str, str]:
    configured = PROJECT / environment["detector"]["configured_path"]
    resolved = configured.resolve()
    expected = environment["detector"]["sha256"]
    if file_sha256(resolved) != expected:
        raise P1DatasetContractError("detector bytes changed after P1.0")
    if resolve_default_weights().resolve() != resolved:
        raise P1DatasetContractError("current detector no longer resolves to P1.0 input")
    return resolved, str(resolved.relative_to(PROJECT)), expected


def configure_native_threads() -> dict[str, int]:
    """Avoid the reproducible macOS OpenCV/torch native-thread SIGSEGV.

    Rendering still overlaps through the explicit Python thread pool.  Disabling
    OpenCV's nested pool and fixing torch's pools changes scheduling only; the
    dry-run repeats detector bytes and array-vs-PNG boxes to prove semantics.
    """
    import cv2
    import torch

    cv2.setNumThreads(0)
    torch.set_num_threads(1)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        # Safe when another earlier call already initialized the same process.
        pass
    return {"opencv_threads": int(cv2.getNumThreads()), "torch_threads": int(torch.get_num_threads())}


def _synthetic_frame() -> pd.DataFrame:
    index = np.arange(430, dtype=float)
    close = 100.0 + 0.002 * index + 0.25 * np.sin(index / 8.0)
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=len(index), freq="15min", tz="UTC"),
            "open": close,
            "high": close + 0.35,
            "low": close - 0.35,
            "close": close,
            "volume": 1000.0 + index % 19,
        }
    )


def _fixture_observation(symbol: str, signal_i: int = 320) -> CandidateObservation:
    return CandidateObservation(
        source="fixture",
        symbol=symbol,
        window_start_i=signal_i - WINDOW + 1,
        window_end_i=signal_i,
        latest_closed_i=signal_i,
        mapped_signal_i=signal_i,
        global_tip_age_bars=0,
        box_x_center=0.98,
        box_y_center=0.5,
        box_width=0.02,
        box_height=0.1,
        box_confidence=0.9,
        box_class_id=0,
    )


def _mapping_fixture() -> list[dict[str, Any]]:
    transform = ChartTransform(
        n_bars=WINDOW,
        width=1280,
        height=742,
        left=12,
        top=12,
        plot_w=1256,
        plot_h=718,
        price_min=90.0,
        price_max=110.0,
        candle_half_w=2,
    )
    cases = [
        ("tip", 500, 199, True, 0),
        ("tip_1", 500, 198, True, 1),
        ("tip_2", 498, 199, True, 2),
        ("tip_3", 497, 199, False, 3),
    ]
    evidence = []
    for name, window_end, local_bar, accepted, age in cases:
        width = 0.01
        right_norm = transform.x_at(local_bar) / transform.width
        mapped = map_box_to_signal(
            cx=right_norm - width / 2,
            w=width,
            tf=transform,
            window_start_i=window_end - WINDOW + 1,
            n_bars=WINDOW,
            frame_length=501,
            latest_closed_i=500,
            tip_edge_bars=TIP_EDGE_BARS,
            apply_tip_edge=True,
            max_global_tip_age_bars=2,
        )
        evidence.append(
            {
                "case": name,
                "accepted": mapped.accepted,
                "expected_accepted": accepted,
                "mapped_signal_i": mapped.mapped_signal_i,
                "global_tip_age_bars": mapped.global_tip_age_bars,
                "expected_age": age,
                "rejection_reason": mapped.rejection_reason,
                "parity": mapped.accepted == accepted and mapped.global_tip_age_bars == age,
            }
        )
    return evidence


def run_fixture() -> dict[str, Any]:
    fixture_dir = DATA_ROOT / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    expected = {"tp": "tp", "sl": "sl", "same_bar": "sl_ambiguous", "timeout": "timeout"}
    for scenario in ("tp", "sl", "same_bar", "timeout"):
        frame = _synthetic_frame()
        signal_i = 320
        entry_i = signal_i + 1
        featured = add_features(add_indicators(frame))
        featured.loc[signal_i, "atr14"] = 1.0
        featured.loc[signal_i, "atr_pct"] = 0.01
        entry = float(frame.loc[entry_i, "open"])
        if scenario == "tp":
            frame.loc[entry_i, "low"] = entry - 6.0
        elif scenario == "sl":
            frame.loc[entry_i, "high"] = entry + 3.0
        elif scenario == "same_bar":
            frame.loc[entry_i, "low"] = entry - 6.0
            frame.loc[entry_i, "high"] = entry + 3.0
        row, reject = build_candidate_row(
            frame=frame,
            featured=featured,
            observation=_fixture_observation(f"{scenario.upper()}_USDT_SWAP", signal_i),
            build_id="p1_fixture_v1",
            detector_path="models/fixture.pt",
            detector_sha256="f" * 64,
        )
        if reject or row is None:
            raise P1DatasetContractError(f"fixture {scenario} rejected: {reject}")
        if row["exit_reason"] != expected[scenario]:
            raise P1DatasetContractError(
                f"fixture {scenario}: {row['exit_reason']} != {expected[scenario]}"
            )
        rows.append(row)
    rows = assign_event_groups(rows)
    path_a = fixture_dir / "p1_fixture_a.csv"
    path_b = fixture_dir / "p1_fixture_b.csv"
    hash_a = write_dataset_csv(rows, path_a)
    hash_b = write_dataset_csv(rows, path_b)

    # Feature-direction fixture: same causal source row, fixed order, explicit side transform.
    feature_frame = add_features(add_indicators(_synthetic_frame()))
    long_row = extract_feature_rows_for_semantics(
        feature_frame, [320], feature_semantics="side_aligned_v1", side="long"
    ).iloc[0]
    short_row = extract_feature_rows_for_semantics(
        feature_frame, [320], feature_semantics="side_aligned_v1", side="short"
    ).iloc[0]
    feature_evidence = {
        "column_order_equal": list(long_row.index) == FEATURE_COLUMNS == list(short_row.index),
        "short_ret_12_is_negative_long": bool(
            np.isclose(float(short_row["ret_12"]), -float(long_row["ret_12"]))
        ),
        "feature_source_max_i": 320,
        "future_feature_rows_read": 0,
    }

    # Boundary fixture proves cutoff row OHLC strings are not converted.
    boundary = fixture_dir / "cutoff_boundary.csv"
    boundary.write_text(
        "open_time,open,high,low,close,volume\n"
        "2026-05-03T23:45:00Z,1,2,0.5,1.5,10\n"
        "2026-05-04T00:00:00Z,NOT_READ,NOT_READ,NOT_READ,NOT_READ,NOT_READ\n",
        encoding="utf-8",
    )
    _, cutoff_stats = load_preholdout_candles(boundary)
    mapping = _mapping_fixture()
    identities = [
        abs(float(row["net_ret_swap_taker"]) - (float(row["gross_ret"]) - 0.001)) < 1e-15
        for row in rows
    ]
    accepted = all(
        [
            hash_a == hash_b,
            all(item["parity"] for item in mapping),
            feature_evidence["column_order_equal"],
            feature_evidence["short_ret_12_is_negative_long"],
            cutoff_stats["post_cutoff_ohlcv_rows_materialized"] == 0,
            all(identities),
            all(
                pd.Timestamp(row["entry_time_research"]) > pd.Timestamp(row["signal_time"])
                for row in rows
            ),
        ]
    )
    audit = {
        "stage": "p1_fixture",
        "verdict": "accepted" if accepted else "rejected",
        "generated_at": utc_now(),
        "protocol_version": PROTOCOL_VERSION,
        "dataset_paths": [str(path_a.relative_to(PROJECT)), str(path_b.relative_to(PROJECT))],
        "dataset_sha256": hash_a,
        "repeat_sha256": hash_b,
        "deterministic_bytes": hash_a == hash_b,
        "row_count": len(rows),
        "outcomes": dict(Counter(row["exit_reason"] for row in rows)),
        "mapping": mapping,
        "feature_parity": feature_evidence,
        "cost_identity_all": all(identities),
        "cutoff": cutoff_stats,
        "holdout_rows_read": 0,
        "trained": False,
        "threshold_changed": False,
        "active_modified": False,
        "deployed": False,
        "ordered": False,
    }
    write_json(FIXTURE_AUDIT, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return audit


def _raw_lookup(raw: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(item["source"], item["symbol"]): item for item in raw["raw_inputs"]}


def _load_real_frame(item: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = PROJECT / item["path"]
    frame, stats = load_preholdout_candles(path)
    if stats["post_cutoff_ohlcv_rows_materialized"] != 0:
        raise P1DatasetContractError(f"{item['symbol']}: materialized holdout OHLC")
    return frame, stats


def _rows_for_observations(
    *,
    frame: pd.DataFrame,
    observations: list[CandidateObservation],
    build_id: str,
    detector_path: str,
    detector_sha256: str,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    featured = add_features(add_indicators(frame))
    rows = []
    rejects: Counter[str] = Counter()
    for observation in observations:
        row, reason = build_candidate_row(
            frame=frame,
            featured=featured,
            observation=observation,
            build_id=build_id,
            detector_path=detector_path,
            detector_sha256=detector_sha256,
        )
        if row is None:
            rejects[str(reason or "unknown")] += 1
        else:
            rows.append(row)
    return rows, rejects


def _transport_parity(
    *, model: Any, frame: pd.DataFrame, window_end_i: int, device: str
) -> dict[str, Any]:
    from src.detection.data import add_mas

    start = window_end_i - WINDOW + 1
    image, _ = render_chart(add_mas(frame).iloc[start : window_end_i + 1])
    fixture_dir = DATA_ROOT / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    png = fixture_dir / "transport_parity.png"
    import cv2

    if not cv2.imwrite(str(png), image):
        raise RuntimeError("could not write transport parity PNG")
    array_result = model.predict(image, conf=DEFAULT_CONF, verbose=False, device=device)[0]
    png_result = model.predict(str(png), conf=DEFAULT_CONF, verbose=False, device=device)[0]
    a = normalized_boxes(array_result)
    b = normalized_boxes(png_result)
    same_count = len(a) == len(b)
    max_delta = 0.0
    if same_count and a:
        for left, right in zip(a, b):
            if left["class_id"] != right["class_id"]:
                max_delta = float("inf")
                break
            max_delta = max(
                max_delta,
                *(abs(float(left[key]) - float(right[key])) for key in ("x", "y", "w", "h", "confidence")),
            )
    return {
        "array_boxes": a,
        "png_boxes": b,
        "same_count": same_count,
        "max_abs_delta": max_delta,
        "accepted": same_count and max_delta <= 1e-7,
    }


def _legacy_dry_plan(raw: dict[str, Any], n_symbols: int, n_proposals: int) -> list[dict[str, Any]]:
    proposals = pd.read_csv(PROJECT / raw["proposals"]["path"], usecols=["symbol", "signal_time"])
    proposals["signal_time"] = pd.to_datetime(proposals["signal_time"], utc=True)
    proposals = proposals.sort_values(["symbol", "signal_time"], kind="stable")
    symbols = list(proposals["symbol"].drop_duplicates().head(int(n_symbols)))
    out = []
    for symbol in symbols:
        times = list(
            proposals.loc[proposals["symbol"] == symbol, "signal_time"].head(int(n_proposals))
        )
        out.append({"source": "okx", "symbol": symbol, "proposal_times": times})
    return out


def _real_replay_once(
    *,
    plan: list[dict[str, Any]],
    raw: dict[str, Any],
    model: Any,
    device: str,
    build_id: str,
    detector_path: str,
    detector_sha256: str,
    batch_size: int,
    render_workers: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], tuple[pd.DataFrame, int] | None]:
    lookup = _raw_lookup(raw)
    all_rows = []
    symbols = []
    first_window: tuple[pd.DataFrame, int] | None = None
    for spec in plan:
        print(f"dry-run: loading {spec['symbol']}", flush=True)
        item = lookup[(spec["source"], spec["symbol"])]
        frame, load_stats = _load_real_frame(item)
        time_to_i = {timestamp: index for index, timestamp in enumerate(frame["open_time"])}
        proposal_indices = [
            int(time_to_i[timestamp])
            for timestamp in spec["proposal_times"]
            if timestamp in time_to_i
        ]
        if not proposal_indices:
            raise P1DatasetContractError(f"dry-run proposal times missing for {spec['symbol']}")
        pulse_indices = sorted(set(proposal_indices))
        window_ends = sorted(
            {
                latest - back
                for latest in pulse_indices
                for back in (0, 1, 2)
                if latest - back >= WINDOW - 1
            }
        )
        if first_window is None:
            # Use a known proposal window so array-vs-PNG parity exercises a
            # positive box, not merely the empty-output path at latest-2.
            first_window = (frame, proposal_indices[0])
        local, detection_stats = detect_historical_windows(
            frame=frame,
            source=spec["source"],
            symbol=spec["symbol"],
            model=model,
            window_end_indices=window_ends,
            device=device,
            batch_size=batch_size,
            render_workers=render_workers,
        )
        print(
            f"dry-run: detected {spec['symbol']} windows={len(window_ends)} "
            f"boxes={len(local)}",
            flush=True,
        )
        observations, selection_stats = select_live_parity_observations(
            local,
            pulse_latest_indices=pulse_indices,
        )
        rows, rejects = _rows_for_observations(
            frame=frame,
            observations=observations,
            build_id=build_id,
            detector_path=detector_path,
            detector_sha256=detector_sha256,
        )
        all_rows.extend(rows)
        symbols.append(
            {
                "source": spec["source"],
                "symbol": spec["symbol"],
                "load": load_stats,
                "proposal_indices": proposal_indices,
                "detection": detection_stats,
                "selection": selection_stats,
                "row_count": len(rows),
                "row_rejections": dict(sorted(rejects.items())),
            }
        )
    return assign_event_groups(all_rows), {"symbols": symbols}, first_window


def run_dry(args: argparse.Namespace) -> dict[str, Any]:
    fixture = json.loads(FIXTURE_AUDIT.read_text(encoding="utf-8"))
    if fixture.get("verdict") != "accepted":
        raise P1DatasetContractError("fixture gate is not accepted")
    native_threads = configure_native_threads()
    print(f"dry-run: native_threads={native_threads}", flush=True)
    environment, raw = load_baseline()
    weights, detector_path, detector_sha = detector_identity(environment)
    model = load_yolo_model(weights)
    plan = _legacy_dry_plan(raw, args.dry_symbols, args.dry_proposals)
    build_id = "p1_real_dry_v1"
    start = time.monotonic()
    rows_a, stats_a, first_window = _real_replay_once(
        plan=plan,
        raw=raw,
        model=model,
        device=args.device,
        build_id=build_id,
        detector_path=detector_path,
        detector_sha256=detector_sha,
        batch_size=args.batch_size,
        render_workers=args.render_workers,
    )
    print(f"dry-run: first replay rows={len(rows_a)}", flush=True)
    rows_b, stats_b, _ = _real_replay_once(
        plan=plan,
        raw=raw,
        model=model,
        device=args.device,
        build_id=build_id,
        detector_path=detector_path,
        detector_sha256=detector_sha,
        batch_size=args.batch_size,
        render_workers=args.render_workers,
    )
    print(f"dry-run: repeat replay rows={len(rows_b)}", flush=True)
    dry_dir = DATA_ROOT / "dry_run"
    path_a = dry_dir / "p1_real_dry_a.csv"
    path_b = dry_dir / "p1_real_dry_b.csv"
    hash_a = write_dataset_csv(rows_a, path_a)
    hash_b = write_dataset_csv(rows_b, path_b)
    if first_window is None:
        raise P1DatasetContractError("dry-run did not schedule a transport parity window")
    transport = _transport_parity(
        model=model,
        frame=first_window[0],
        window_end_i=first_window[1],
        device=args.device,
    )
    accepted = all(
        [
            len(rows_a) > 0,
            hash_a == hash_b,
            transport["accepted"],
            all(item["load"]["post_cutoff_ohlcv_rows_materialized"] == 0 for item in stats_a["symbols"]),
            all(int(row["global_tip_age_bars"]) <= 2 for row in rows_a),
            all(str(row["feature_semantics"]) == FEATURE_SEMANTICS for row in rows_a),
            all(pd.Timestamp(row["signal_time"]) < HOLDOUT_CUTOFF for row in rows_a),
        ]
    )
    audit = {
        "stage": "p1_real_dry_run",
        "verdict": "accepted" if accepted else "rejected",
        "generated_at": utc_now(),
        "git_commit": git("rev-parse", "HEAD"),
        "protocol_version": PROTOCOL_VERSION,
        "detector_path": detector_path,
        "detector_sha256": detector_sha,
        "device": args.device,
        "native_threads": native_threads,
        "conf": DEFAULT_CONF,
        "plan": [
            {
                "source": item["source"],
                "symbol": item["symbol"],
                "proposal_times": [str(value) for value in item["proposal_times"]],
            }
            for item in plan
        ],
        "first_build": stats_a,
        "repeat_build": stats_b,
        "row_count": len(rows_a),
        "dataset_paths": [str(path_a.relative_to(PROJECT)), str(path_b.relative_to(PROJECT))],
        "dataset_sha256": hash_a,
        "repeat_sha256": hash_b,
        "deterministic_bytes": hash_a == hash_b,
        "transport_parity": transport,
        "post_cutoff_ohlcv_rows_materialized": 0,
        "holdout_rows_read": 0,
        "elapsed_seconds": time.monotonic() - start,
        "trained": False,
        "threshold_changed": False,
        "active_modified": False,
        "deployed": False,
        "ordered": False,
    }
    write_json(DRY_AUDIT, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return audit


def _full_spec(environment: dict[str, Any], raw: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_sha256": schema_sha256(),
        "source_commit": git("rev-parse", "HEAD"),
        "raw_prefix_sha256": raw["combined_preholdout_prefix_sha256"],
        "detector_sha256": environment["detector"]["sha256"],
        "universe_symbols": raw["research_symbols"],
        "signal_start": SIGNAL_START.isoformat(),
        "holdout_cutoff": HOLDOUT_CUTOFF.isoformat(),
        "device": args.device,
        "conf": DEFAULT_CONF,
        "tip_edge_bars": TIP_EDGE_BARS,
        "global_tip_age_bars": 2,
        "batch_size": args.batch_size,
        "render_workers": args.render_workers,
    }


def _write_symbol_shard(
    *,
    item: dict[str, Any],
    model: Any,
    args: argparse.Namespace,
    staging: Path,
    build_id: str,
    detector_path: str,
    detector_sha: str,
    spec_hash: str,
) -> dict[str, Any]:
    symbol = item["symbol"]
    shard = staging / f"{symbol}.csv"
    meta_path = staging / f"{symbol}.json"
    if shard.is_file() and meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (
            meta.get("spec_hash") == spec_hash
            and meta.get("source_prefix_sha256") == item["preholdout_prefix_sha256"]
            and meta.get("shard_sha256") == file_sha256(shard)
        ):
            return {**meta, "resumed": True}
        raise P1DatasetContractError(f"stale or corrupt shard exists for {symbol}")

    frame, load_stats = _load_real_frame(item)
    times = pd.to_datetime(frame["open_time"], utc=True)
    eligible = np.flatnonzero((times >= SIGNAL_START).to_numpy())
    if len(eligible):
        first_pulse = max(WINDOW - 1, int(eligible[0]))
        pulse_indices = list(range(first_pulse, len(frame)))
        window_ends = list(range(max(WINDOW - 1, first_pulse - 2), len(frame)))
        local, detection_stats = detect_historical_windows(
            frame=frame,
            source=item["source"],
            symbol=symbol,
            model=model,
            window_end_indices=window_ends,
            device=args.device,
            batch_size=args.batch_size,
            render_workers=args.render_workers,
            signal_i_lo=int(eligible[0]),
        )
        observations, selection_stats = select_live_parity_observations(
            local,
            pulse_latest_indices=pulse_indices,
        )
        rows, rejects = _rows_for_observations(
            frame=frame,
            observations=observations,
            build_id=build_id,
            detector_path=detector_path,
            detector_sha256=detector_sha,
        )
    else:
        detection_stats = {
            "windows_scheduled": 0,
            "windows_rendered": 0,
            "predicted_boxes": 0,
            "locally_accepted_boxes": 0,
            "mapping_rejections": {},
        }
        selection_stats = {
            "pulse_raw_signal_count": 0,
            "pulse_after_min_gap_count": 0,
            "pulse_after_global_age_count": 0,
            "unique_candidate_count": 0,
            "global_tip_age_distribution": {},
        }
        rows, rejects = [], Counter()
    shard_sha = write_dataset_csv(rows, shard)
    meta = {
        "source": item["source"],
        "symbol": symbol,
        "spec_hash": spec_hash,
        "source_prefix_sha256": item["preholdout_prefix_sha256"],
        "shard_path": str(shard.relative_to(PROJECT)),
        "shard_sha256": shard_sha,
        "row_count": len(rows),
        "load": load_stats,
        "detection": detection_stats,
        "selection": selection_stats,
        "row_rejections": dict(sorted(rejects.items())),
        "completed": True,
    }
    write_json(meta_path, meta)
    return {**meta, "resumed": False}


def _read_shard_rows(symbol_meta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for meta in sorted(symbol_meta, key=lambda item: (item["source"], item["symbol"])):
        path = PROJECT / meta["shard_path"]
        if file_sha256(path) != meta["shard_sha256"]:
            raise P1DatasetContractError(f"shard hash changed: {path}")
        if int(meta["row_count"]) == 0:
            continue
        frame = pd.read_csv(path)
        if list(frame.columns) != DATASET_COLUMNS:
            raise P1DatasetContractError(f"shard schema mismatch: {path}")
        rows.extend(frame.to_dict(orient="records"))
    return rows


def _quantiles(series: pd.Series) -> dict[str, float | None]:
    numeric = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    if numeric.empty:
        return {key: None for key in ("min", "p01", "p25", "p50", "p75", "p99", "max", "mean")}
    return {
        "min": float(numeric.min()),
        "p01": float(numeric.quantile(0.01)),
        "p25": float(numeric.quantile(0.25)),
        "p50": float(numeric.quantile(0.50)),
        "p75": float(numeric.quantile(0.75)),
        "p99": float(numeric.quantile(0.99)),
        "max": float(numeric.max()),
        "mean": float(numeric.mean()),
    }


def _audit_dataset(frame: pd.DataFrame, symbol_meta: list[dict[str, Any]]) -> dict[str, Any]:
    if frame.empty:
        return {"accepted": False, "reason": "empty dataset"}
    signal_time = pd.to_datetime(frame["signal_time"], utc=True, errors="raise")
    entry_time = pd.to_datetime(frame["entry_time_research"], utc=True, errors="raise")
    feature_as_of = pd.to_datetime(frame["feature_as_of"], utc=True, errors="raise")
    interval_start = pd.to_datetime(frame["interval_start"], utc=True, errors="raise")
    interval_end = pd.to_datetime(frame["interval_end"], utc=True, errors="raise")
    gross = pd.to_numeric(frame["gross_ret"], errors="raise")
    fee = pd.to_numeric(frame["fee_swap_taker"], errors="raise")
    net = pd.to_numeric(frame["net_ret_swap_taker"], errors="raise")
    identity_error = (net - (gross - fee)).abs()
    feature_audit = {}
    for name in FEATURE_COLUMNS:
        numeric = pd.to_numeric(frame[name], errors="coerce")
        finite = numeric.replace([np.inf, -np.inf], np.nan)
        feature_audit[name] = {
            "missing_rate": float(finite.isna().mean()),
            "inf_count": int(np.isinf(numeric.to_numpy(dtype=float, na_value=np.nan)).sum()),
            "constant": bool(finite.dropna().nunique() <= 1),
            "distribution": _quantiles(finite),
        }
    group_sizes = frame.groupby("event_group_id", dropna=False).size()
    flags = frame["data_quality_flags"].fillna("").astype(str)
    audit = {
        "accepted": True,
        "row_count": len(frame),
        "symbol_count": int(frame["symbol"].nunique()),
        "source_count": int(frame["source"].nunique()),
        "timeframe_values": sorted(frame["timeframe"].astype(str).unique()),
        "side_values": sorted(frame["side"].astype(str).unique()),
        "first_signal_time": signal_time.min().isoformat(),
        "max_signal_time": signal_time.max().isoformat(),
        "cutoff": HOLDOUT_CUTOFF.isoformat(),
        "holdout_signal_rows": int((signal_time >= HOLDOUT_CUTOFF).sum()),
        "detector_confidence": _quantiles(frame["box_confidence"]),
        "global_tip_age_distribution": {
            str(key): int(value)
            for key, value in sorted(frame["global_tip_age_bars"].value_counts().items())
        },
        "duplicate_candidate_id": int(frame["candidate_id"].duplicated().sum()),
        "duplicate_event_id": int(frame["event_id"].duplicated().sum()),
        "feature_columns": FEATURE_COLUMNS,
        "feature_count": len(FEATURE_COLUMNS),
        "features": feature_audit,
        "feature_as_of_after_signal": int((feature_as_of > signal_time).sum()),
        "feature_source_after_signal": int(
            (
                pd.to_numeric(frame["feature_source_max_i"], errors="raise")
                > pd.to_numeric(frame["mapped_signal_i"], errors="raise")
            ).sum()
        ),
        "entry_not_after_signal": int((entry_time <= signal_time).sum()),
        "entry_delay_minutes": _quantiles((entry_time - signal_time).dt.total_seconds() / 60.0),
        "exit_reason_counts": {
            str(key): int(value) for key, value in sorted(frame["exit_reason"].value_counts().items())
        },
        "exit_offset": _quantiles(frame["exit_offset"]),
        "gross_ret": _quantiles(gross),
        "net_ret_swap_taker": _quantiles(net),
        "fee_swap_taker_values": sorted(float(value) for value in fee.unique()),
        "cost_identity_max_abs_error": float(identity_error.max()),
        "cost_identity_failures": int((identity_error > 1e-14).sum()),
        "invalid_intervals": int((interval_end < interval_start).sum()),
        "event_group_count": int(group_sizes.size),
        "event_group_size": _quantiles(group_sizes),
        "overlap_rows": int(group_sizes[group_sizes > 1].sum()),
        "data_quality_flagged_rows": int((flags != "").sum()),
        "post_cutoff_ohlcv_rows_materialized": int(
            sum(item["load"]["post_cutoff_ohlcv_rows_materialized"] for item in symbol_meta)
        ),
        "completed_universe_symbols": len(symbol_meta),
        "rendered_windows": int(sum(item["detection"]["windows_rendered"] for item in symbol_meta)),
        "predicted_boxes": int(sum(item["detection"]["predicted_boxes"] for item in symbol_meta)),
        "row_rejections": dict(
            sorted(
                sum(
                    (Counter(item["row_rejections"]) for item in symbol_meta),
                    Counter(),
                ).items()
            )
        ),
        "deterministic_samples": frame.sort_values("candidate_id", kind="stable")
        .head(20)[
            [
                "candidate_id",
                "symbol",
                "signal_time",
                "global_tip_age_bars",
                "box_confidence",
                "exit_reason",
                "gross_ret",
                "net_ret_swap_taker",
            ]
        ]
        .to_dict(orient="records"),
    }
    audit["accepted"] = all(
        [
            audit["holdout_signal_rows"] == 0,
            audit["duplicate_candidate_id"] == 0,
            audit["duplicate_event_id"] == 0,
            audit["feature_count"] == 28,
            audit["feature_as_of_after_signal"] == 0,
            audit["feature_source_after_signal"] == 0,
            audit["entry_not_after_signal"] == 0,
            audit["cost_identity_failures"] == 0,
            audit["invalid_intervals"] == 0,
            audit["data_quality_flagged_rows"] == 0,
            audit["post_cutoff_ohlcv_rows_materialized"] == 0,
            audit["completed_universe_symbols"] == 344,
            audit["timeframe_values"] == ["15m"],
            audit["side_values"] == ["short"],
            not any(item["missing_rate"] > 0 or item["inf_count"] > 0 for item in feature_audit.values()),
        ]
    )
    return audit


def run_full(args: argparse.Namespace) -> dict[str, Any]:
    fixture = json.loads(FIXTURE_AUDIT.read_text(encoding="utf-8"))
    dry = json.loads(DRY_AUDIT.read_text(encoding="utf-8"))
    if fixture.get("verdict") != "accepted" or dry.get("verdict") != "accepted":
        raise P1DatasetContractError("fixture and dry-run gates must both be accepted")
    if git("branch", "--show-current") != "main":
        raise P1DatasetContractError("full P1 build requires main")
    native_threads = configure_native_threads()
    environment, raw = load_baseline()
    weights, detector_path, detector_sha = detector_identity(environment)
    if dry.get("detector_sha256") != detector_sha:
        raise P1DatasetContractError("dry-run detector differs from full detector")
    protected_before = {
        path: file_sha256(PROJECT / path)
        for path in ("models/ACTIVE", "data/forward_log.csv", "data/executor_ledger.jsonl")
    }
    if (PROJECT / "models/active_bundle.json").exists():
        raise P1DatasetContractError("active bundle unexpectedly exists")

    spec = _full_spec(environment, raw, args)
    spec_hash = stable_hash(json.dumps(spec, sort_keys=True, separators=(",", ":")))
    build_id = f"p1_20260803_{spec_hash[:16]}"
    staging = DATA_ROOT / "_staging" / build_id
    staging.mkdir(parents=True, exist_ok=True)
    write_json(staging / "build_spec.json", {"build_id": build_id, "spec_hash": spec_hash, **spec})
    model = load_yolo_model(weights)
    if args.partition_count < 1:
        raise P1DatasetContractError("partition_count must be positive")
    if not (0 <= args.partition_index < args.partition_count):
        raise P1DatasetContractError("partition_index must be in [0, partition_count)")
    selected_inputs = [
        item
        for item_index, item in enumerate(raw["raw_inputs"])
        if item_index % args.partition_count == args.partition_index
    ]
    symbol_meta = []
    started = time.monotonic()
    for index, item in enumerate(selected_inputs, 1):
        before = time.monotonic()
        meta = _write_symbol_shard(
            item=item,
            model=model,
            args=args,
            staging=staging,
            build_id=build_id,
            detector_path=detector_path,
            detector_sha=detector_sha,
            spec_hash=spec_hash,
        )
        symbol_meta.append(meta)
        print(
            f"[{index}/{len(selected_inputs)} p{args.partition_index}/{args.partition_count}] "
            f"{item['symbol']}: rows={meta['row_count']} "
            f"windows={meta['detection']['windows_rendered']} resumed={meta['resumed']} "
            f"wall={time.monotonic()-before:.1f}s total={(time.monotonic()-started)/60:.1f}m",
            flush=True,
        )

    if args.partition_count > 1:
        checkpoint = {
            "stage": "p1_full_partition_checkpoint",
            "verdict": "checkpointed",
            "generated_at": utc_now(),
            "git_commit": git("rev-parse", "HEAD"),
            "build_id": build_id,
            "spec_hash": spec_hash,
            "partition_index": args.partition_index,
            "partition_count": args.partition_count,
            "completed_symbols": len(symbol_meta),
            "expected_symbols": len(selected_inputs),
            "row_count": int(sum(item["row_count"] for item in symbol_meta)),
            "elapsed_seconds": time.monotonic() - started,
            "holdout_rows_read": 0,
            "post_cutoff_ohlcv_rows_materialized": int(
                sum(item["load"]["post_cutoff_ohlcv_rows_materialized"] for item in symbol_meta)
            ),
        }
        write_json(staging / f"partition_{args.partition_index}_of_{args.partition_count}.json", checkpoint)
        print(json.dumps(checkpoint, ensure_ascii=False, indent=2))
        return checkpoint

    # A single-partition invocation is the only finalizer. It revalidates and
    # resumes every symbol shard, including shards written by earlier disjoint
    # partition workers, before assembling the immutable dataset.
    rows = assign_event_groups(_read_shard_rows(symbol_meta))
    assembly_a = staging / "assembled_a.csv"
    assembly_b = staging / "assembled_b.csv"
    hash_a = write_dataset_csv(rows, assembly_a)
    hash_b = write_dataset_csv(rows, assembly_b)
    deterministic = hash_a == hash_b
    if not deterministic:
        raise P1DatasetContractError("full dataset assembly is not byte deterministic")
    dataset_path = DATA_ROOT / f"p1_short_l2_preholdout_{hash_a[:16]}.csv"
    if dataset_path.exists():
        if file_sha256(dataset_path) != hash_a:
            raise P1DatasetContractError("content-addressed output path has different bytes")
    else:
        shutil.copyfile(assembly_a, dataset_path)
    frame = pd.read_csv(dataset_path)
    audit = _audit_dataset(frame, symbol_meta)

    source_paths = [
        "src/data/loader.py",
        "src/detection/render.py",
        "src/judgment/yolo_candidates.py",
        "src/judgment/features.py",
        "src/judgment/outcomes.py",
        "src/judgment/labeling.py",
        "src/judgment/p1_dataset.py",
        "src/judgment/p1_build.py",
        "src/costs.py",
        "scripts/build_p1_preholdout_dataset_20260803.py",
    ]
    training_eligible = bool(audit["accepted"] and deterministic)
    manifest = {
        "manifest_version": "p1_immutable_dataset_manifest_v1",
        "generated_at": utc_now(),
        "build_id": build_id,
        "protocol_version": PROTOCOL_VERSION,
        "training_eligible": training_eligible,
        "training_eligibility_checks": audit,
        "dataset_path": str(dataset_path.relative_to(PROJECT)),
        "dataset_sha256": hash_a,
        "dataset_size_bytes": dataset_path.stat().st_size,
        "row_count": len(frame),
        "schema_sha256": schema_sha256(),
        "columns": DATASET_COLUMNS,
        "feature_columns": FEATURE_COLUMNS,
        "feature_schema": FEATURE_SCHEMA,
        "feature_semantics": FEATURE_SEMANTICS,
        "protocol": {
            "source": "okx",
            "timeframe": "15m",
            "side": "short",
            "signal_start": SIGNAL_START.isoformat(),
            "holdout_cutoff": HOLDOUT_CUTOFF.isoformat(),
            "detector_path": detector_path,
            "detector_sha256": detector_sha,
            "detector_conf": DEFAULT_CONF,
            "tip_edge_bars": TIP_EDGE_BARS,
            "global_tip_age_max_bars": 2,
            "entry": "next_bar_open",
            "tp_atr_mult": 5.0,
            "sl_atr_mult": 2.0,
            "horizon_bars": 72,
            "same_bar_policy": "conservative_sl",
            "return_convention": "linear_short",
            "cost_fields": ["gross_ret", "fee_swap_taker", "net_ret_swap_taker"],
        },
        "inputs": {
            "raw_input_manifest": str((BASELINE_DIR / "raw_inputs.json").relative_to(PROJECT)),
            "raw_input_manifest_sha256": file_sha256(BASELINE_DIR / "raw_inputs.json"),
            "combined_preholdout_prefix_sha256": raw["combined_preholdout_prefix_sha256"],
            "universe_symbol_count": len(raw["research_symbols"]),
            "universe_symbols": raw["research_symbols"],
            "source_commit": spec["source_commit"],
            "source_hashes": {path: file_sha256(PROJECT / path) for path in source_paths},
        },
        "build_command": (
            "PYTHONPATH=. .venv/bin/python scripts/build_p1_preholdout_dataset_20260803.py "
            f"full --device {args.device} --batch-size {args.batch_size} "
            f"--render-workers {args.render_workers}"
        ),
        "fixture_audit": str(FIXTURE_AUDIT.relative_to(PROJECT)),
        "fixture_audit_sha256": file_sha256(FIXTURE_AUDIT),
        "dry_run_audit": str(DRY_AUDIT.relative_to(PROJECT)),
        "dry_run_audit_sha256": file_sha256(DRY_AUDIT),
        "safety": {
            "holdout_rows_read": 0,
            "post_cutoff_ohlcv_rows_materialized": 0,
            "trained": False,
            "threshold_changed": False,
            "active_bundle_created": False,
            "active_modified": False,
            "deployed": False,
            "ordered": False,
        },
        "native_threads": native_threads,
    }
    manifest_path = DATA_ROOT / f"p1_short_l2_preholdout_{hash_a[:16]}.manifest.json"
    write_json(manifest_path, manifest)
    # Fail-closed consumer is part of the acceptance gate, not a future promise.
    load_immutable_dataset(manifest_path)

    protected_after = {
        path: file_sha256(PROJECT / path)
        for path in ("models/ACTIVE", "data/forward_log.csv", "data/executor_ledger.jsonl")
    }
    result = {
        "stage": "p1_full_preholdout_build",
        "verdict": "accepted" if training_eligible and protected_before == protected_after else "rejected",
        "generated_at": utc_now(),
        "git_commit": git("rev-parse", "HEAD"),
        "build_id": build_id,
        "dataset_path": manifest["dataset_path"],
        "dataset_sha256": hash_a,
        "repeat_dataset_sha256": hash_b,
        "deterministic_bytes": deterministic,
        "dataset_manifest_path": str(manifest_path.relative_to(PROJECT)),
        "dataset_manifest_sha256": file_sha256(manifest_path),
        "audit": audit,
        "symbol_shards": symbol_meta,
        "protected_hashes_before": protected_before,
        "protected_hashes_after": protected_after,
        "protected_hashes_unchanged": protected_before == protected_after,
        "elapsed_seconds": time.monotonic() - started,
        "safety": manifest["safety"],
    }
    write_json(FULL_AUDIT, result)
    print(json.dumps({key: result[key] for key in (
        "verdict", "build_id", "dataset_path", "dataset_sha256",
        "dataset_manifest_path", "elapsed_seconds"
    )}, ensure_ascii=False, indent=2))
    if result["verdict"] != "accepted":
        raise P1DatasetContractError("full dataset audit rejected")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    sub.add_parser("fixture")
    dry = sub.add_parser("dry-run")
    full = sub.add_parser("full")
    for target in (dry, full):
        target.add_argument("--device", default="cpu")
        target.add_argument("--batch-size", type=int, default=8)
        target.add_argument("--render-workers", type=int, default=4)
    dry.add_argument("--dry-symbols", type=int, default=2)
    dry.add_argument("--dry-proposals", type=int, default=4)
    full.add_argument("--partition-count", type=int, default=1)
    full.add_argument("--partition-index", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.stage == "fixture":
        audit = run_fixture()
    elif args.stage == "dry-run":
        audit = run_dry(args)
    else:
        audit = run_full(args)
    return 0 if audit.get("verdict") in {"accepted", "checkpointed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
