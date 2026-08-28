#!/usr/bin/env python3
"""Run the frozen Owner 10k-positive/30k-negative YOLO on five UTC mover boards.

Inputs are the five complete UTC days preregistered before any market-data
read.  The daily Top20 selector is explicitly post-hoc: it ranks confirmed
same-day absolute returns and is not a live universe selector.  Every model
image is a causal W18--25 slice ending at its own confirmed bar, while the
mapped core is accepted only when it spans 4--5 bars and has 4--6 confirmation
bars before that image endpoint.

This adapter reuses the already audited fetch, rendering, mapping, inference,
deduplication and daily-board implementation.  It changes only the frozen
experiment identity, five-day calendar, new weight and its training-supported
geometry.  It never trains, tunes, promotes, deploys, writes canonical data or
touches forward/order state.

Usage:
  PYTHONPATH=. .venv/bin/python scripts/scan_15m_ma_launch_owner_yolo_recent5d.py --fetch
  PYTHONPATH=. .venv/bin/python scripts/scan_15m_ma_launch_owner_yolo_recent5d.py --scan
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from scripts import scan_15m_ma_launch_t3_daily_movers as common


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-owner-yolo-recent5d-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_OUT = ROOT / "analysis" / "output" / "ma_launch_owner_yolo_recent5d_v1"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
EXPECTED_DAYS = [
    pd.Timestamp("2026-08-23T00:00:00Z"),
    pd.Timestamp("2026-08-24T00:00:00Z"),
    pd.Timestamp("2026-08-25T00:00:00Z"),
    pd.Timestamp("2026-08-26T00:00:00Z"),
    pd.Timestamp("2026-08-27T00:00:00Z"),
]
EXPECTED_WINDOWS = tuple(range(18, 26))
EXPECTED_CORES = (4, 5)
EXPECTED_CONFIRMATIONS = (4, 5, 6)


class OwnerRecent5dError(RuntimeError):
    """Fail-closed preregistration, lineage or source-state error."""


def load_preregistration(path: Path) -> dict[str, Any]:
    """Load and enforce the exact Owner-authorized no-tuning scan contract."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("experiment_id") != EXPERIMENT_ID:
        raise OwnerRecent5dError("unexpected experiment_id")
    authorization = payload["owner_authorization"]
    if int(authorization["holdout_consumption_number_for_this_configuration"]) != 1:
        raise OwnerRecent5dError("holdout consumption number drifted")
    if authorization.get("training_or_tuning_authorized") is not False:
        raise OwnerRecent5dError("training/tuning must remain unauthorized")
    if authorization.get("production_or_promotion_authorized") is not False:
        raise OwnerRecent5dError("production/promotion must remain unauthorized")

    days = [common.utc(value) for value in payload["calendar"]["complete_days"]]
    if days != EXPECTED_DAYS:
        raise OwnerRecent5dError("calendar drifted from 2026-08-23..27 UTC")
    if payload["calendar"].get("current_partial_day_excluded") is not True:
        raise OwnerRecent5dError("partial UTC day must stay excluded")
    ranking = payload["ranking"]
    if int(ranking["top_per_day"]) != 20:
        raise OwnerRecent5dError("daily board must remain Top20")
    if ranking["causality"] != "post_hoc_same_day_ranking_not_live_selection":
        raise OwnerRecent5dError("ranking causality disclosure drifted")

    detector = payload["detector"]
    if tuple(map(int, detector["window_lengths"])) != EXPECTED_WINDOWS:
        raise OwnerRecent5dError("window support drifted from training W18..25")
    if tuple(map(int, detector["mapped_core_length_bars_allowed"])) != EXPECTED_CORES:
        raise OwnerRecent5dError("core geometry drifted from 4..5")
    if tuple(map(int, detector["mapped_confirmation_bars_allowed"])) != EXPECTED_CONFIRMATIONS:
        raise OwnerRecent5dError("confirmation geometry drifted from 4..6")
    if int(detector["scan_endpoint_extension_after_day_bars"]) != 6:
        raise OwnerRecent5dError("day extension must remain six bars")
    if float(detector["confidence"]) != 0.25 or float(detector["nms_iou"]) != 0.7:
        raise OwnerRecent5dError("inference threshold drifted")
    if int(detector["imgsz"]) != 960:
        raise OwnerRecent5dError("inference image size drifted")
    if detector.get("future_bars_rendered_into_inference") != 0:
        raise OwnerRecent5dError("inference image contains future bars")
    if detector.get("threshold_or_window_retuning_after_results") is not False:
        raise OwnerRecent5dError("post-result retuning switch drifted")
    if any(value is not False for value in payload["safety"].values()):
        raise OwnerRecent5dError("one or more safety switches drifted")
    return payload


def verify_training_geometry(manifest_path: Path) -> dict[str, Any]:
    """Prove the inference window/core/confirmation support comes from labels."""

    positives = 0
    window_counts: Counter[int] = Counter()
    core_counts: Counter[int] = Counter()
    confirmation_counts: Counter[int] = Counter()
    with manifest_path.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("sample_kind") != "positive":
                continue
            positives += 1
            window_counts[int(row["window_end_i"]) - int(row["window_start_i"]) + 1] += 1
            core_counts[int(row["core_bars"])] += 1
            confirmation_counts[int(row["post_core_context_bars"])] += 1
    if positives != 10_000:
        raise OwnerRecent5dError(f"positive training rows drifted: {positives}")
    if tuple(sorted(window_counts)) != EXPECTED_WINDOWS:
        raise OwnerRecent5dError(f"training window support drifted: {window_counts}")
    if tuple(sorted(core_counts)) != EXPECTED_CORES:
        raise OwnerRecent5dError(f"training core support drifted: {core_counts}")
    if tuple(sorted(confirmation_counts)) != EXPECTED_CONFIRMATIONS:
        raise OwnerRecent5dError(
            f"training confirmation support drifted: {confirmation_counts}"
        )
    return {
        "positive_rows": positives,
        "window_counts": dict(sorted(window_counts.items())),
        "core_counts": dict(sorted(core_counts.items())),
        "confirmation_counts": dict(sorted(confirmation_counts.items())),
    }


def verify_immutable_inputs(prereg: dict[str, Any]) -> dict[str, Any]:
    """Hash the delivered weight, training manifest and shared renderer."""

    detector = prereg["detector"]
    records: dict[str, Any] = {}
    for key, hash_key in (
        ("weights", "weights_sha256"),
        ("training_manifest", "training_manifest_sha256"),
        ("renderer", "renderer_sha256"),
    ):
        path = ROOT / str(detector[key])
        if not path.is_file():
            raise OwnerRecent5dError(f"missing immutable input: {path}")
        actual = common.sha256_file(path)
        expected = str(detector[hash_key])
        if actual != expected:
            raise OwnerRecent5dError(f"{key} hash drifted: {actual}")
        records[key] = {"path": str(path.relative_to(ROOT)), "sha256": actual}
    records["training_geometry"] = verify_training_geometry(
        ROOT / str(detector["training_manifest"])
    )
    return records


def verify_sources_committed(prereg_path: Path) -> str:
    """Require main and committed adapter/shared-builder/prereg bytes before reads."""

    paths = [
        Path(__file__).resolve().relative_to(ROOT),
        (ROOT / "scripts" / "scan_15m_ma_launch_t3_daily_movers.py").relative_to(ROOT),
        prereg_path.resolve().relative_to(ROOT),
    ]
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    if branch != "main":
        raise OwnerRecent5dError("official scan must run on main")
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    if dirty:
        raise OwnerRecent5dError(f"scan sources must be committed before holdout reads:\n{dirty}")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if len(commit) != 40:
        raise OwnerRecent5dError("could not resolve source commit")
    return commit


def main() -> int:
    """Run exactly one preregistered fetch or frozen inference phase."""

    parser = argparse.ArgumentParser(description=__doc__)
    phase = parser.add_mutually_exclusive_group(required=True)
    phase.add_argument("--fetch", action="store_true")
    phase.add_argument("--scan", action="store_true")
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    if args.workers < 1 or args.batch_size < 1:
        parser.error("--workers and --batch-size must be positive")
    prereg_path = args.prereg.resolve()
    prereg = load_preregistration(prereg_path)
    verify_immutable_inputs(prereg)
    source_commit = verify_sources_committed(prereg_path)
    if args.fetch:
        common.fetch_and_rank(
            prereg,
            out=args.out.resolve(),
            results=args.results.resolve(),
            workers=args.workers,
            source_commit=source_commit,
        )
    else:
        common.scan_and_render(
            prereg,
            out=args.out.resolve(),
            results=args.results.resolve(),
            device=common.choose_device(args.device),
            batch_size=args.batch_size,
            source_commit=source_commit,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
