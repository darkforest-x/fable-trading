"""Full pre-holdout audit for the 5m causal-v2 diagnostic dataset.

Checks exact YOLO bytes and coordinates, manifest/file pairing, event/split
isolation, source-index lineage, visible-right-edge causality, label-horizon
sealing, duplicate hashes and a shuffled-linkage null.  It never opens a bar at
or after the canonical holdout boundary and never mutates the dataset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.windows.verify_yolo_dataset import verify_dataset  # noqa: E402
from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix  # noqa: E402
from yoyo.datasets.ma_launch_5m_causal import (  # noqa: E402
    BAR_MINUTES,
    CONTRACT_VERSION,
    assert_manifest_timing,
    split_from_decision_at,
)
from yoyo.datasets.ma_launch_owner_recrop_review import HOLDOUT_START  # noqa: E402
from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

DEFAULT_CANDIDATES = (
    ROOT / "analysis/output/ma_launch_5m_candidates_20260830/candidates_5m.jsonl"
)
DEFAULT_DATASET = ROOT / "datasets/ma_launch_5m_outcome_causal_v2"
DEFAULT_OUTCOMES = (
    ROOT / "analysis/output/ma_launch_5m_outcomes_causal_v2_20260831/outcomes.jsonl"
)
DEFAULT_OUT = (
    ROOT
    / "experiments/active/exp-5m-ma-launch-outcome-causal-v2/results/causality_audit.json"
)
NULL_PERMUTATIONS = 1000
NULL_SEED = 20260831


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--outcomes", type=Path, default=DEFAULT_OUTCOMES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--skip-source-lineage", action="store_true")
    args = parser.parse_args()

    candidates_path = args.candidates.resolve()
    dataset = args.dataset.resolve()
    outcomes_path = args.outcomes.resolve()
    out_path = args.out.resolve()
    rows = _load_jsonl(dataset / "manifest.jsonl")
    candidate_rows = _load_jsonl(candidates_path)
    outcome_rows = _load_jsonl(outcomes_path)
    candidates = {str(row["event_id"]): row for row in candidate_rows}
    outcomes = {str(row["event_id"]): row for row in outcome_rows}
    findings: dict[str, list[dict[str, object]]] = {
        "confirmed_blocker": [],
        "expected_by_contract": [],
        "review_queue": [],
        "tool_limitation": [],
    }

    if len(candidates) != len(candidate_rows):
        findings["confirmed_blocker"].append(
            {"check": "candidate_event_uniqueness", "rows": len(candidate_rows), "unique": len(candidates)}
        )
    if len(outcomes) != len(outcome_rows):
        findings["confirmed_blocker"].append(
            {"check": "outcome_event_uniqueness", "rows": len(outcome_rows), "unique": len(outcomes)}
        )
    manifest_event_counts = Counter(str(row["event_id"]) for row in rows)
    duplicate_events = sorted(
        event_id for event_id, count in manifest_event_counts.items() if count != 1
    )
    if duplicate_events:
        findings["confirmed_blocker"].append(
            {
                "check": "one_image_per_event",
                "count": len(duplicate_events),
                "examples": duplicate_events[:10],
            }
        )

    try:
        format_receipt = verify_dataset(
            dataset,
            dataset / "verification_contract.json",
            verify_file_hashes=True,
        )
    except Exception as exc:  # noqa: BLE001
        findings["confirmed_blocker"].append(
            {"check": "yolo_format_and_bytes", "error": f"{type(exc).__name__}: {exc}"}
        )
        format_receipt = {"passed": False}

    causal_failures: list[dict[str, object]] = []
    outcome_failures: list[dict[str, object]] = []
    candidate_failures: list[dict[str, object]] = []
    split_failures: list[dict[str, object]] = []
    train_events: set[str] = set()
    val_events: set[str] = set()
    train_hashes: set[str] = set()
    val_hashes: set[str] = set()
    negative_empty = 0
    for row in rows:
        event_id = str(row["event_id"])
        try:
            assert_manifest_timing(row)
            decision_at = pd.Timestamp(row["decision_at"])
            visible_end_at = pd.Timestamp(row["visible_end_at"])
            outcome_start_at = pd.Timestamp(row["outcome_start_at"])
            horizon_end_at = pd.Timestamp(row["horizon_end_at"])
            if decision_at != visible_end_at or decision_at != outcome_start_at:
                raise ValueError("decision, visible end and next-bar open are not the same instant")
            if horizon_end_at > pd.Timestamp(HOLDOUT_START):
                raise ValueError("label horizon reaches the holdout boundary")
            if str(row["outcome_contract"]) != CONTRACT_VERSION:
                raise ValueError("unknown outcome contract")
        except Exception as exc:  # noqa: BLE001
            causal_failures.append({"event_id": event_id, "error": str(exc)})

        outcome = outcomes.get(event_id)
        if outcome is None:
            outcome_failures.append({"event_id": event_id, "error": "missing outcome row"})
        else:
            barrier_outcome = str(outcome["barrier_outcome"])
            expected_kind = (
                "positive" if barrier_outcome == "tp" else "negative" if barrier_outcome == "sl" else None
            )
            lineage_pairs = {
                "source_path": (row.get("source_path"), outcome.get("source_path")),
                "core_start_i": (row.get("core_start_i"), outcome.get("core_start_i")),
                "core_end_i": (row.get("core_end_i"), outcome.get("core_end_i")),
                "decision_i": (row.get("decision_i"), outcome.get("decision_i")),
                "visible_end_i": (row.get("visible_end_i"), outcome.get("visible_end_i")),
                "outcome_start_i": (row.get("outcome_start_i"), outcome.get("outcome_start_i")),
                "decision_at": (row.get("decision_at"), outcome.get("decision_at")),
                "outcome_contract": (row.get("outcome_contract"), outcome.get("outcome_contract")),
            }
            drift = {
                key: {"manifest": values[0], "outcome": values[1]}
                for key, values in lineage_pairs.items()
                if str(values[0]) != str(values[1])
            }
            if expected_kind is None:
                drift["barrier_outcome"] = {
                    "manifest": row.get("sample_kind"),
                    "outcome": barrier_outcome,
                }
            if drift:
                outcome_failures.append({"event_id": event_id, "drift": drift})
            elif row["sample_kind"] != expected_kind:
                outcome_failures.append(
                    {
                        "event_id": event_id,
                        "expected": expected_kind,
                        "observed": row["sample_kind"],
                    }
                )

        candidate = candidates.get(event_id)
        if candidate is None:
            candidate_failures.append({"event_id": event_id, "error": "missing candidate row"})
        else:
            candidate_pairs = {
                "source_path": (row.get("source_path"), candidate.get("source_path")),
                "core_start_i": (row.get("core_start_i"), candidate.get("source_core_start_i")),
                "core_end_i": (row.get("core_end_i"), candidate.get("source_core_end_i")),
                "symbol": (row.get("symbol"), candidate.get("symbol")),
                "trade_direction": (row.get("trade_direction"), candidate.get("direction")),
            }
            drift = {
                key: {"manifest": values[0], "candidate": values[1]}
                for key, values in candidate_pairs.items()
                if str(values[0]) != str(values[1])
            }
            if drift:
                candidate_failures.append({"event_id": event_id, "drift": drift})

        expected_split = split_from_decision_at(row["decision_at"])
        if expected_split is None or row.get("split") != expected_split:
            split_failures.append(
                {
                    "event_id": event_id,
                    "decision_at": row.get("decision_at"),
                    "expected": expected_split,
                    "observed": row.get("split"),
                }
            )

        split = str(row["split"])
        if split == "train":
            train_events.add(event_id)
            train_hashes.add(str(row["image_sha256"]))
        elif split == "val":
            val_events.add(event_id)
            val_hashes.add(str(row["image_sha256"]))
        else:
            split_failures.append({"event_id": event_id, "observed": split, "error": "unknown split"})
        if row["sample_kind"] == "negative":
            negative_empty += 1

    if causal_failures:
        findings["confirmed_blocker"].append(
            {
                "check": "causal_timing",
                "count": len(causal_failures),
                "examples": causal_failures[:10],
            }
        )
    if outcome_failures:
        findings["confirmed_blocker"].append(
            {
                "check": "outcome_lineage",
                "count": len(outcome_failures),
                "examples": outcome_failures[:10],
            }
        )
    if candidate_failures:
        findings["confirmed_blocker"].append(
            {
                "check": "candidate_lineage",
                "count": len(candidate_failures),
                "examples": candidate_failures[:10],
            }
        )
    if split_failures:
        findings["confirmed_blocker"].append(
            {
                "check": "time_split_and_purge",
                "count": len(split_failures),
                "examples": split_failures[:10],
            }
        )

    split_overlap = sorted(train_events & val_events)
    image_overlap = sorted(train_hashes & val_hashes)
    image_hash_counts = Counter(str(row["image_sha256"]) for row in rows)
    duplicate_image_hashes = sorted(
        digest for digest, count in image_hash_counts.items() if count != 1
    )
    if split_overlap:
        findings["confirmed_blocker"].append(
            {"check": "event_split_overlap", "count": len(split_overlap), "examples": split_overlap[:10]}
        )
    if image_overlap:
        findings["confirmed_blocker"].append(
            {"check": "image_hash_split_overlap", "count": len(image_overlap), "examples": image_overlap[:10]}
        )
    if duplicate_image_hashes:
        findings["confirmed_blocker"].append(
            {
                "check": "duplicate_image_pixels",
                "count": len(duplicate_image_hashes),
                "examples": duplicate_image_hashes[:10],
            }
        )

    source_failures: list[dict[str, object]] = []
    render_failures: list[dict[str, object]] = []
    rerendered_images = 0
    if not args.skip_source_lineage:
        grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in rows:
            grouped[str(row["source_path"])].append(row)
        duration = pd.Timedelta(minutes=BAR_MINUTES)
        for number, (source, source_rows) in enumerate(sorted(grouped.items()), 1):
            try:
                frame, _ = read_preholdout_prefix(
                    ROOT / source,
                    end_exclusive=HOLDOUT_START,
                    bar_minutes=BAR_MINUTES,
                )
                frame = add_mas(frame)
                times = pd.to_datetime(frame["open_time"], utc=True)
                for row in source_rows:
                    decision_i = int(row["decision_i"])
                    observed = times.iloc[decision_i] + duration
                    if observed != pd.Timestamp(row["decision_at"]):
                        source_failures.append(
                            {
                                "event_id": row["event_id"],
                                "expected": row["decision_at"],
                                "observed": observed.isoformat(),
                            }
                        )
                        continue
                    outcome_start_i = int(row["outcome_start_i"])
                    if times.iloc[outcome_start_i] != pd.Timestamp(row["outcome_start_at"]):
                        source_failures.append(
                            {
                                "event_id": row["event_id"],
                                "error": "outcome-start source timestamp mismatch",
                            }
                        )
                        continue
                    window_start = int(row["window_start_i"])
                    window_end = int(row["window_end_i"])
                    if window_start < 0 or window_end >= len(frame) or window_start > window_end:
                        source_failures.append(
                            {"event_id": row["event_id"], "error": "source window is out of range"}
                        )
                        continue
                    window = frame.iloc[window_start : window_end + 1]
                    rerendered, _ = render_chart(window, out_path=None)
                    stored = cv2.imread(
                        str(dataset / str(row["image_path"])), cv2.IMREAD_COLOR
                    )
                    rerendered_images += 1
                    if stored is None or stored.shape != rerendered.shape or not np.array_equal(
                        stored, rerendered
                    ):
                        render_failures.append(
                            {
                                "event_id": row["event_id"],
                                "image_path": row["image_path"],
                                "stored_shape": None if stored is None else list(stored.shape),
                                "rerendered_shape": list(rerendered.shape),
                            }
                        )
            except Exception as exc:  # noqa: BLE001
                source_failures.append(
                    {"source_path": source, "error": f"{type(exc).__name__}: {exc}"}
                )
            if number % 50 == 0:
                print(f"  lineage {number}/{len(grouped)} sources", flush=True)
        if source_failures:
            findings["confirmed_blocker"].append(
                {
                    "check": "source_index_lineage",
                    "count": len(source_failures),
                    "examples": source_failures[:10],
                }
            )
        if render_failures:
            findings["confirmed_blocker"].append(
                {
                    "check": "source_to_pixel_rerender",
                    "count": len(render_failures),
                    "examples": render_failures[:10],
                }
            )
    else:
        findings["tool_limitation"].append(
            {"check": "source_index_lineage", "reason": "explicitly skipped by CLI"}
        )

    # Null control: break the event-to-outcome linkage while preserving the exact
    # class balance, then ask whether every image still receives its observed kind.
    observed_kinds = np.array([str(row["sample_kind"]) for row in rows], dtype=object)
    outcome_kinds = np.array(
        [
            "positive"
            if outcomes.get(str(row["event_id"]), {}).get("barrier_outcome") == "tp"
            else "negative"
            if outcomes.get(str(row["event_id"]), {}).get("barrier_outcome") == "sl"
            else "missing_or_excluded"
            for row in rows
        ],
        dtype=object,
    )
    actual_matches = int((observed_kinds == outcome_kinds).sum())
    rng = np.random.default_rng(NULL_SEED)
    null_matches = np.empty(NULL_PERMUTATIONS, dtype=int)
    for index in range(NULL_PERMUTATIONS):
        null_matches[index] = int((observed_kinds == rng.permutation(outcome_kinds)).sum())
    null_p = (1 + int((null_matches >= actual_matches).sum())) / (NULL_PERMUTATIONS + 1)

    findings["expected_by_contract"].append(
        {
            "check": "empty_negative_labels",
            "count": negative_empty,
            "reason": "SL outcome images are declared background negatives",
        }
    )
    findings["expected_by_contract"].append(
        {
            "check": "eligibility",
            "training_eligible": False,
            "production_eligible": False,
            "reason": "rule-proposed diagnostic outcomes are not Owner Gold",
        }
    )
    findings["review_queue"].append(
        {
            "check": "owner_shape_adjudication",
            "status": "pending",
            "reason": (
                "TP/SL is an outcome label, not proof that the rule-proposed core is "
                "Owner-confirmed L1 Gold; this dataset remains ineligible for training"
            ),
        }
    )

    receipt = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "auditor_commit": _git_head(),
        "auditor_path": Path(__file__).resolve().relative_to(ROOT).as_posix(),
        "auditor_sha256": _sha256_file(Path(__file__).resolve()),
        "candidates": candidates_path.relative_to(ROOT).as_posix(),
        "candidate_sha256": _sha256_file(candidates_path),
        "dataset": dataset.relative_to(ROOT).as_posix(),
        "outcomes": outcomes_path.relative_to(ROOT).as_posix(),
        "contract": CONTRACT_VERSION,
        "passed": not findings["confirmed_blocker"],
        "rows": len(rows),
        "positive": int((observed_kinds == "positive").sum()),
        "negative": int((observed_kinds == "negative").sum()),
        "train_events": len(train_events),
        "val_events": len(val_events),
        "event_split_overlap": len(split_overlap),
        "image_hash_split_overlap": len(image_overlap),
        "duplicate_manifest_events": len(duplicate_events),
        "duplicate_image_hashes": len(duplicate_image_hashes),
        "causal_failures": len(causal_failures),
        "outcome_lineage_failures": len(outcome_failures),
        "candidate_lineage_failures": len(candidate_failures),
        "split_failures": len(split_failures),
        "source_lineage_failures": len(source_failures),
        "source_to_pixel_rerendered": rerendered_images,
        "source_to_pixel_failures": len(render_failures),
        "format_receipt": format_receipt,
        "null_control": {
            "method": "permute outcome kinds across event ids while preserving class balance",
            "permutations": NULL_PERMUTATIONS,
            "seed": NULL_SEED,
            "actual_matches": actual_matches,
            "null_mean_matches": float(null_matches.mean()),
            "null_max_matches": int(null_matches.max()),
            "p_value": null_p,
        },
        "findings": findings,
        "holdout_rows_read": 0,
        "training_eligible": False,
        "production_eligible": False,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, indent=2, ensure_ascii=False))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
