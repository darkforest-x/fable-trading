"""Render one causally aligned 5m diagnostic image per MA-launch event.

The visible window ends at ``core_end + 2`` and entry uses that completed bar's
close.  No chart contains a bar after entry.  TP outcomes receive the original
rule-proposed core box; SL outcomes are empty-label negatives; timeout and
ambiguous outcomes are excluded.  This remains a diagnostic outcome-image
dataset, not Owner Gold and not an L1 production-training dataset.

Market columns used for rendering are open_time, open, high, low, close and the
six moving averages derived causally from them.  The full source is truncated
before holdout by ``read_preholdout_prefix``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix  # noqa: E402
from yoyo.datasets.ma_launch_5m_causal import (  # noqa: E402
    BAR_MINUTES,
    CONTRACT_VERSION,
    HORIZON_BARS,
    PRE_CORE_BARS,
    assert_manifest_timing,
    split_from_decision_at,
    timing_from_core_end,
)
from yoyo.datasets.ma_launch_owner_recrop_review import HOLDOUT_START  # noqa: E402
from yoyo.layers.l1_detection.data import ALL_MA_COLS, add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

CANDIDATES = ROOT / "analysis/output/ma_launch_5m_candidates_20260830/candidates_5m.jsonl"
OUTCOMES = ROOT / "analysis/output/ma_launch_5m_outcomes_causal_v2_20260831/outcomes.jsonl"
DEFAULT_DST = ROOT / "datasets/ma_launch_5m_outcome_causal_v2"
DEFAULT_RECEIPTS = (
    ROOT / "experiments/active/exp-5m-ma-launch-outcome-causal-v2/results"
)
PAD_FRACTION = 0.04
CLASS_ID = {"LONG": 0, "SHORT": 1}
MA_COLS = list(ALL_MA_COLS)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_head() -> str:
    """Return the committed builder revision used for this artifact."""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def core_box(
    transform: Any,
    window: pd.DataFrame,
    start_local: int,
    end_local: int,
) -> dict[str, float]:
    """Map the rule-proposed core geometry into one YOLO row."""
    core = window.iloc[start_local : end_local + 1]
    values = np.concatenate(
        (
            core["high"].to_numpy(float),
            core["low"].to_numpy(float),
            core.loc[:, MA_COLS].to_numpy(float).ravel(),
        )
    )
    if not np.isfinite(values).all():
        raise ValueError("non-finite core geometry")
    high, low = float(values.max()), float(values.min())
    pad = (high - low) * PAD_FRACTION
    x0 = transform.x_at(start_local) - transform.candle_half_w - 2
    x1 = transform.x_at(end_local) + transform.candle_half_w + 2
    y0, y1 = transform.y_at(high + pad), transform.y_at(low - pad)
    x0, x1 = max(0.0, min(x0, x1)), min(float(transform.width), max(x0, x1))
    y0, y1 = max(0.0, min(y0, y1)), min(float(transform.height), max(y0, y1))
    return {
        "cx": (x0 + x1) / 2 / transform.width,
        "cy": (y0 + y1) / 2 / transform.height,
        "w": (x1 - x0) / transform.width,
        "h": (y1 - y0) / transform.height,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=CANDIDATES)
    parser.add_argument("--outcomes", type=Path, default=OUTCOMES)
    parser.add_argument("--dst", type=Path, default=DEFAULT_DST)
    parser.add_argument("--receipt-dir", type=Path, default=DEFAULT_RECEIPTS)
    parser.add_argument("--limit-sources", type=int, default=0)
    args = parser.parse_args()

    candidates_path = args.candidates.resolve()
    outcomes_path = args.outcomes.resolve()
    destination = args.dst.resolve()
    receipt_dir = args.receipt_dir.resolve()
    if destination.exists():
        raise SystemExit(f"refusing to overwrite existing dataset: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)

    candidate_rows = [
        json.loads(line) for line in candidates_path.read_text().splitlines() if line.strip()
    ]
    candidates = {str(row["event_id"]): row for row in candidate_rows}
    if len(candidates) != len(candidate_rows):
        raise SystemExit("candidate event_id values are not unique")
    outcomes = [
        json.loads(line) for line in outcomes_path.read_text().splitlines() if line.strip()
    ]
    if len({str(row["event_id"]) for row in outcomes}) != len(outcomes):
        raise SystemExit("outcome event_id values are not unique")

    by_source: dict[str, list[dict[str, object]]] = defaultdict(list)
    for outcome in outcomes:
        event_id = str(outcome["event_id"])
        if str(outcome.get("outcome_contract")) != CONTRACT_VERSION:
            raise SystemExit(f"outcome contract mismatch: {event_id}")
        if int(outcome.get("horizon_bars", -1)) != HORIZON_BARS:
            raise SystemExit(f"outcome horizon mismatch: {event_id}")
        candidate = candidates.get(event_id)
        if candidate is None:
            raise SystemExit(f"outcome has no candidate lineage: {event_id}")
        if str(candidate["source_path"]) != str(outcome["source_path"]):
            raise SystemExit(f"source lineage mismatch: {event_id}")
        by_source[str(outcome["source_path"])].append(outcome)

    sources = sorted(by_source)
    if args.limit_sources:
        sources = sources[: args.limit_sources]
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.building-", dir=destination.parent))
    stats: Counter[str] = Counter()
    manifest: list[dict[str, object]] = []
    dimensions: tuple[int, int] | None = None

    try:
        for split in ("train", "val"):
            (stage / "images" / split).mkdir(parents=True)
            (stage / "labels" / split).mkdir(parents=True)

        for source_number, source in enumerate(sources, 1):
            try:
                frame, _ = read_preholdout_prefix(
                    ROOT / source,
                    end_exclusive=HOLDOUT_START,
                    bar_minutes=BAR_MINUTES,
                )
                frame = add_mas(frame)
            except Exception as exc:  # noqa: BLE001
                stats[f"source unreadable: {type(exc).__name__}"] += len(by_source[source])
                continue
            times = pd.to_datetime(frame["open_time"], utc=True)

            for outcome in sorted(by_source[source], key=lambda row: str(row["event_id"])):
                event_id = str(outcome["event_id"])
                candidate = candidates[event_id]
                barrier_outcome = str(outcome["barrier_outcome"])
                if barrier_outcome == "tp":
                    label_class = str(outcome["direction"])
                    sample_kind = "positive"
                elif barrier_outcome == "sl":
                    label_class = None
                    sample_kind = "negative"
                else:
                    stats[f"dropped outcome: {barrier_outcome}"] += 1
                    continue

                core_start = int(candidate["source_core_start_i"])
                timing = timing_from_core_end(int(candidate["source_core_end_i"]))
                if timing.decision_i != int(outcome["decision_i"]):
                    raise ValueError(f"decision lineage mismatch: {event_id}")
                decision_at = pd.Timestamp(outcome["decision_at"])
                split = split_from_decision_at(decision_at)
                if split is None:
                    stats["dropped in purge band"] += 1
                    continue

                window_start = core_start - PRE_CORE_BARS
                window_end = timing.visible_end_i
                if window_start < 200 or window_end >= len(frame):
                    stats["window out of range"] += 1
                    continue
                observed_decision = times.iloc[timing.decision_i] + pd.Timedelta(
                    minutes=BAR_MINUTES
                )
                if observed_decision != decision_at:
                    raise ValueError(f"decision timestamp mismatch: {event_id}")

                window = frame.iloc[window_start : window_end + 1]
                if window[MA_COLS].isna().any().any():
                    stats["MA warmup incomplete"] += 1
                    continue

                image, transform = render_chart(window, out_path=None)
                current_dimensions = (int(image.shape[1]), int(image.shape[0]))
                if dimensions is None:
                    dimensions = current_dimensions
                elif dimensions != current_dimensions:
                    raise ValueError(
                        f"render dimensions drifted: expected {dimensions}, got {current_dimensions}"
                    )

                prefix = "P" if sample_kind == "positive" else "N"
                safe_symbol = str(outcome["symbol"]).replace("/", "_")
                stem = f"{prefix}_{safe_symbol}_{event_id}"
                image_path = stage / "images" / split / f"{stem}.png"
                label_path = stage / "labels" / split / f"{stem}.txt"
                if not cv2.imwrite(str(image_path), image):
                    raise OSError(f"failed to write {image_path}")

                box: dict[str, float] | None = None
                if label_class is None:
                    label_path.write_text("", encoding="utf-8")
                else:
                    box = core_box(
                        transform,
                        window,
                        core_start - window_start,
                        timing.core_end_i - window_start,
                    )
                    label_path.write_text(
                        f"{CLASS_ID[label_class]} {box['cx']:.6f} {box['cy']:.6f} "
                        f"{box['w']:.6f} {box['h']:.6f}\n",
                        encoding="utf-8",
                    )

                record: dict[str, object] = {
                    "dataset_sample_id": stem,
                    "sample_kind": sample_kind,
                    "barrier_outcome": barrier_outcome,
                    "split": split,
                    "symbol": str(outcome["symbol"]),
                    "trade_direction": str(outcome["direction"]),
                    "event_id": event_id,
                    "timeframe": "5m",
                    "source_path": source,
                    "core_start_i": core_start,
                    "core_end_i": timing.core_end_i,
                    "decision_i": timing.decision_i,
                    "visible_end_i": timing.visible_end_i,
                    "outcome_start_i": timing.outcome_start_i,
                    "window_start_i": window_start,
                    "window_end_i": window_end,
                    "pre_core_bars": PRE_CORE_BARS,
                    "post_core_bars": 2,
                    "decision_at": str(outcome["decision_at"]),
                    "visible_end_at": str(outcome["visible_end_at"]),
                    "outcome_start_at": str(outcome["outcome_start_at"]),
                    "horizon_end_at": str(outcome["horizon_end_at"]),
                    "entry_price_source": "decision_close",
                    "outcome_contract": CONTRACT_VERSION,
                    "label_origin": "rule_proposal",
                    "training_eligible": False,
                    "production_eligible": False,
                    "image_path": f"images/{split}/{stem}.png",
                    "label_path": f"labels/{split}/{stem}.txt",
                    "image_sha256": sha256_file(image_path),
                    "label_sha256": sha256_file(label_path),
                    "image_width": current_dimensions[0],
                    "image_height": current_dimensions[1],
                }
                if label_class is None:
                    record["negative_event_id"] = event_id
                else:
                    record["direction"] = label_class
                    record["box"] = box
                assert_manifest_timing(record)
                manifest.append(record)
                stats[f"{sample_kind} {split}"] += 1

            if source_number % 50 == 0:
                print(f"  {source_number}/{len(sources)} sources, {len(manifest)} renders")

        by_pixels: dict[str, list[dict[str, object]]] = defaultdict(list)
        for record in manifest:
            by_pixels[str(record["image_sha256"])].append(record)
        dropped_stems: set[str] = set()
        for group in by_pixels.values():
            if len(group) == 1:
                continue
            kinds = {str(record["sample_kind"]) for record in group}
            ordered = sorted(group, key=lambda record: str(record["event_id"]))
            if len(kinds) > 1:
                dropped_stems.update(str(record["dataset_sample_id"]) for record in ordered)
                stats["dropped: identical pixels, contradictory labels"] += len(ordered)
            else:
                dropped_stems.update(
                    str(record["dataset_sample_id"]) for record in ordered[1:]
                )
                stats["dropped: identical pixels, same label"] += len(ordered) - 1

        kept: list[dict[str, object]] = []
        for record in manifest:
            stem = str(record["dataset_sample_id"])
            if stem not in dropped_stems:
                kept.append(record)
                continue
            (stage / str(record["image_path"])).unlink()
            (stage / str(record["label_path"])).unlink()
        manifest = sorted(kept, key=lambda row: (str(row["split"]), str(row["dataset_sample_id"])))

        manifest_path = stage / "manifest.jsonl"
        manifest_path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in manifest
            ),
            encoding="utf-8",
        )
        (stage / "data.yaml").write_text(
            f"path: {destination}\ntrain: images/train\nval: images/val\n"
            "names:\n  0: dense_long\n  1: dense_short\n",
            encoding="utf-8",
        )

        counts: dict[str, dict[str, int]] = {}
        classes: dict[str, Counter[str]] = {}
        events: dict[str, Counter[str]] = {}
        for split in ("train", "val"):
            selected = [row for row in manifest if row["split"] == split]
            positive = [row for row in selected if row["sample_kind"] == "positive"]
            negative = [row for row in selected if row["sample_kind"] == "negative"]
            counts[split] = {
                "images": len(selected),
                "labels": len(selected),
                "positive": len(positive),
                "negative": len(negative),
            }
            classes[split] = Counter(
                str(CLASS_ID[str(row["direction"])]) for row in positive
            )
            events[split] = Counter(str(row["sample_kind"]) for row in selected)
        counts["total"] = {
            "images": len(manifest),
            "labels": len(manifest),
            "positive": sum(row["sample_kind"] == "positive" for row in manifest),
            "negative": sum(row["sample_kind"] == "negative" for row in manifest),
        }

        build_summary = {
            "schema_version": 2,
            "passed": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator_commit": git_head(),
            "generator_path": Path(__file__).resolve().relative_to(ROOT).as_posix(),
            "generator_sha256": sha256_file(Path(__file__).resolve()),
            "dataset": destination.relative_to(ROOT).as_posix(),
            "contract": CONTRACT_VERSION,
            "role": "diagnostic_outcome_image_dataset_not_owner_gold",
            "render_contract": {
                "renders_per_event": 1,
                "pre_core_bars": PRE_CORE_BARS,
                "post_core_bars": 2,
                "visible_end_equals_decision": True,
            },
            "label_contract": {
                "entry": "decision_close",
                "outcome_starts": "next_bar",
                "horizon_bars": HORIZON_BARS,
                "tp_atr": 5.0,
                "sl_atr": 2.0,
                "tp_is_positive": True,
                "sl_is_empty_label_negative": True,
            },
            "counts": counts,
            "class_instances_by_split": {
                split: dict(sorted(counter.items())) for split, counter in classes.items()
            },
            "events_by_split": {
                split: dict(sorted(counter.items())) for split, counter in events.items()
            },
            "source_dimensions": list(dimensions or (0, 0)),
            "stats": dict(sorted(stats.items())),
            "candidate_sha256": sha256_file(candidates_path),
            "outcomes_sha256": sha256_file(outcomes_path),
            "holdout_rows_read": 0,
            "training_eligible": False,
            "production_eligible": False,
        }
        summary_path = stage / "build_summary.json"
        _write_json(summary_path, build_summary)

        verification_contract = {
            "schema_version": 1,
            "experiment_id": "exp-5m-ma-launch-outcome-causal-v2",
            "training": {"classes": {"0": "dense_long", "1": "dense_short"}},
            "immutable_inputs": {
                "manifest_sha256": sha256_file(manifest_path),
                "build_summary_sha256": sha256_file(summary_path),
                "source_dimensions": list(dimensions or (0, 0)),
                "counts": counts,
                "class_instances_by_split": {
                    split: dict(sorted(counter.items())) for split, counter in classes.items()
                },
                "events_by_split": {
                    split: dict(sorted(counter.items())) for split, counter in events.items()
                },
            },
        }
        _write_json(stage / "verification_contract.json", verification_contract)

        destination.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(destination)
        receipt_dir.mkdir(parents=True, exist_ok=True)
        for name in ("build_summary.json", "verification_contract.json"):
            shutil.copy2(destination / name, receipt_dir / name)

    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    print("\n=== causal dataset ===")
    print(json.dumps(build_summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
