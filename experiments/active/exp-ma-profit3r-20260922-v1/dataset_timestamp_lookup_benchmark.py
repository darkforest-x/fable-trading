"""Reproduce the frozen-versus-current MA-profit timestamp lookup benchmark."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.datasets import ma_profit_dataset as candidate
from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frozen_module(commit: str) -> types.ModuleType:
    source = subprocess.check_output(
        ["git", "show", f"{commit}:yoyo/datasets/ma_profit_dataset.py"], cwd=ROOT, text=True,
    )
    module = types.ModuleType(f"ma_profit_dataset_frozen_{commit}")
    module.__file__ = str(ROOT / "yoyo/datasets/ma_profit_dataset.py")
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


def _assets_equal(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    """Compare every returned field, including encoded image bytes."""

    return left == right and [item["png"] for item in left] == [item["png"] for item in right]


def _timings(function: Any, frame: Any, row: dict[str, Any], repeats: int) -> list[float]:
    values = []
    for _ in range(repeats):
        gc.collect()
        started = time.perf_counter()
        result = function(frame, row)
        values.append(time.perf_counter() - started)
        if not result:
            raise RuntimeError("benchmark event unexpectedly produced no assets")
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--baseline-commit", required=True)
    parser.add_argument("--positive-event-id", required=True)
    parser.add_argument("--negative-event-id", required=True)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.repeats < 1:
        raise ValueError("repeats must be positive")

    rows = [json.loads(line) for line in args.ledger.read_text().splitlines() if line]
    by_id = {str(row["event_id"]): row for row in rows}
    positive, negative = by_id[args.positive_event_id], by_id[args.negative_event_id]
    baseline = _frozen_module(args.baseline_commit)

    def prefix(row: dict[str, Any]) -> tuple[Any, Path]:
        source = Path(row["source_path"])
        source = source if source.is_absolute() else ROOT / source
        frame, _ = read_preholdout_prefix(
            source, end_exclusive=row["profit"]["decision_close_time_utc"], bar_minutes=row["bar_minutes"],
        )
        return frame, source

    frame, source = prefix(positive)
    negative_frame, negative_source = prefix(negative)
    baseline_positive, candidate_positive = baseline.event_assets(frame, positive), candidate.event_assets(frame, positive)
    baseline_negative = baseline.event_assets(negative_frame, negative)
    candidate_negative = candidate.event_assets(negative_frame, negative)
    if not _assets_equal(baseline_positive, candidate_positive) or not _assets_equal(baseline_negative, candidate_negative):
        raise RuntimeError("frozen/current output parity failed")
    old_times = _timings(baseline.event_assets, frame, positive, args.repeats)
    new_times = _timings(candidate.event_assets, frame, positive, args.repeats)
    old_median, new_median = sorted(old_times)[len(old_times) // 2], sorted(new_times)[len(new_times) // 2]
    print(json.dumps({
        "baseline_commit": args.baseline_commit,
        "baseline_seconds": old_times,
        "candidate_seconds": new_times,
        "median_speedup": old_median / new_median,
        "positive": {"event_id": positive["event_id"], "source_path": str(source.relative_to(ROOT)),
                     "source_sha256": _sha256(source), "prefix_rows": len(frame),
                     "variants": [item["variant"] for item in candidate_positive], "byte_and_metadata_equal": True},
        "negative": {"event_id": negative["event_id"], "source_path": str(negative_source.relative_to(ROOT)),
                     "source_sha256": _sha256(negative_source), "variants": [item["variant"] for item in candidate_negative],
                     "byte_and_metadata_equal": True},
    }, sort_keys=True))


if __name__ == "__main__":
    main()
