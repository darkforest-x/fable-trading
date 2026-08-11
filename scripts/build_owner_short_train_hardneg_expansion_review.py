#!/usr/bin/env python3
"""Build the second train-time hard-negative expansion review.

The page intentionally ranks the 617 still-unreviewed detector events toward
491 Owner-confirmed false fires and away from 1,283 Owner-positive references.
The expected useful answer is therefore often ``3 = not target``.  Selection
uses only data through the decision bar; future 48 bars are attached only after
the fixed 200-event selection for human review.

No labels, training eligibility, holdout reads, or production changes occur.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scripts.backtest_owner_short_gold_center_recent import read_jsonl, sha256_file, write_jsonl
from scripts.build_owner_short_hardneg_canary_review import render_event, render_html, utc
from scripts.build_owner_short_train_hardneg_review import (
    BLOCK_ROOT,
    DATASET_SUMMARY,
    EXPECTED_WEIGHTS_SHA256,
    HOLDOUT_START,
    REFERENCE_MANIFEST,
    ROOT,
    WEIGHTS,
    _load_enriched,
    block_specs,
    causal_feature_vector,
    mean_knn_distance,
)
from scripts.build_owner_short_train_positive_retrieval_review import (
    HARDNEG_REVIEW_DIR,
    SOURCE_POOL,
    TRAIN_POSITIVE_MANIFEST,
    _gold_reference_vectors,
    _postval_reference_vectors,
    _train_review_reference_vectors,
)


PROTOCOL = "owner_short_train_hardneg_expansion200_v2_20260811"
POSITIVE_REVIEW_DIR = ROOT / "analysis/output/owner_short_train_positive_retrieval100_v1"
HARDNEG_REVIEW_LABELED = HARDNEG_REVIEW_DIR / "owner_review_labeled_manifest.jsonl"
HARDNEG_REVIEW_SUMMARY = HARDNEG_REVIEW_DIR / "owner_review_summary.json"
POSITIVE_REVIEW_LABELED = POSITIVE_REVIEW_DIR / "owner_review_labeled_manifest.jsonl"
POSITIVE_REVIEW_SUMMARY = POSITIVE_REVIEW_DIR / "owner_review_summary.json"
OUTPUT = ROOT / "analysis/output/owner_short_train_hardneg_expansion200_v2"
OUTPUT_HTML = ROOT / "analysis/html/p2_owner_short_train_hardneg_expansion200_v2_20260811.html"
BLOCK_QUOTAS = {
    "B01_20250715": 21,
    "B02_20250915": 60,
    "B03_20251115": 60,
    "B04_20260115": 59,
}
REVIEW_TOTAL = sum(BLOCK_QUOTAS.values())
K_NEIGHBOURS = 7


def select_hard_negative_diverse(
    rows: list[dict[str, Any]],
    quotas: dict[str, int] = BLOCK_QUOTAS,
) -> list[dict[str, Any]]:
    """Take fixed block quotas, preferring symbol diversity before rank fill."""
    by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_block[str(row["candidate_block"])].append(row)
    selected: list[dict[str, Any]] = []
    for block_id, quota in quotas.items():
        ranked = sorted(
            by_block.get(block_id, []),
            key=lambda row: (
                -float(row["hard_negative_affinity_v2"]),
                -float(row["event_conf_max"]),
                str(row["decision_time"]),
                str(row["event_id"]),
            ),
        )
        chosen: list[dict[str, Any]] = []
        symbol_counts: Counter[str] = Counter()
        for cap in (1, 2, 3, quota):
            for row in ranked:
                if row in chosen or symbol_counts[str(row["symbol"])] >= cap:
                    continue
                chosen.append(row)
                symbol_counts[str(row["symbol"])] += 1
                if len(chosen) == quota:
                    break
            if len(chosen) == quota:
                break
        if len(chosen) != quota:
            raise ValueError(f"{block_id}: need {quota} hard-negative candidates, have {len(chosen)}")
        selected.extend(chosen)
    return selected


def _validate_review_summary(path: Path, expected: dict[str, int]) -> dict[str, Any]:
    summary = json.loads(path.read_text(encoding="utf-8"))
    if summary["counts"] != expected:
        raise ValueError(f"Owner review count drift: {path}")
    if not all(summary["quality_gates"].values()):
        raise ValueError(f"Owner review quality gate failed: {path}")
    return summary


def _score_remaining(
    source_pool: list[dict[str, Any]],
    reviewed_rows: list[dict[str, Any]],
    *,
    positive_reference: np.ndarray,
    negative_reference: np.ndarray,
    mean: np.ndarray,
    scale: np.ndarray,
    block_root: Path,
) -> list[dict[str, Any]]:
    reviewed_ids = {str(row["event_id"]) for row in reviewed_rows}
    remaining = [dict(row) for row in source_pool if str(row["event_id"]) not in reviewed_ids]
    if len(remaining) != 617:
        raise ValueError(f"expected 617 unreviewed events, got {len(remaining)}")
    spec_by_id = {str(spec["block_id"]): spec for spec in block_specs(block_root)}
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    vectors: list[np.ndarray] = []
    for row in remaining:
        if row.get("selection_future_used"):
            raise ValueError(f"source candidate already used future: {row['event_id']}")
        if row.get("touches_owner_box_guard"):
            raise ValueError(f"source candidate touches Owner box: {row['event_id']}")
        key = (str(row["candidate_block"]), str(row["symbol"]))
        if key not in frames:
            path = Path(spec_by_id[key[0]]["audit_snapshot"]) / "kline_snapshot" / f"{key[1]}.csv"
            frames[key] = _load_enriched(path)
        vectors.append(causal_feature_vector(row, frames[key]))
    query = (np.vstack(vectors) - mean) / scale
    positive_distance = mean_knn_distance(query, positive_reference, k=K_NEIGHBOURS)
    negative_distance = mean_knn_distance(query, negative_reference, k=K_NEIGHBOURS)
    for row, pos_distance, neg_distance in zip(remaining, positive_distance, negative_distance):
        row.update(
            {
                "nearest_owner_positive_distance_v2": float(pos_distance),
                "nearest_owner_negative_distance_v2": float(neg_distance),
                "hard_negative_affinity_v2": float(pos_distance - neg_distance),
                "hard_negative_expansion_future_used": False,
                "hard_negative_expansion_protocol": PROTOCOL,
                "training_eligible": False,
            }
        )
    return remaining


def build(
    *,
    block_root: Path = BLOCK_ROOT,
    output: Path = OUTPUT,
    output_html: Path = OUTPUT_HTML,
) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if sha256_file(WEIGHTS) != EXPECTED_WEIGHTS_SHA256:
        raise ValueError("local hard-negative model SHA drift")
    _validate_review_summary(
        HARDNEG_REVIEW_SUMMARY,
        {"pending": 0, "target": 18, "rebox": 0, "hard_negative": 182},
    )
    _validate_review_summary(
        POSITIVE_REVIEW_SUMMARY,
        {"pending": 0, "target": 45, "rebox": 0, "hard_negative": 55},
    )
    dataset_summary = json.loads(DATASET_SUMMARY.read_text(encoding="utf-8"))
    train_end = utc(dataset_summary["split_profile"]["train_end_max"])

    postval_rows = read_jsonl(REFERENCE_MANIFEST)
    hardneg_review_rows = read_jsonl(HARDNEG_REVIEW_LABELED)
    positive_review_rows = read_jsonl(POSITIVE_REVIEW_LABELED)
    gold_positive = _gold_reference_vectors(read_jsonl(TRAIN_POSITIVE_MANIFEST), train_end)
    postval_positive, postval_negative = _postval_reference_vectors(postval_rows)
    hardneg_positive, hardneg_negative = _train_review_reference_vectors(hardneg_review_rows, block_root)
    active_positive, active_negative = _train_review_reference_vectors(positive_review_rows, block_root)
    actual_counts = (
        len(gold_positive),
        len(postval_positive),
        len(hardneg_positive),
        len(active_positive),
        len(postval_negative),
        len(hardneg_negative),
        len(active_negative),
    )
    if actual_counts != (1143, 77, 18, 45, 254, 182, 55):
        raise ValueError(f"reference count drift: {actual_counts}")
    positive_raw = np.vstack(
        [gold_positive, np.vstack(postval_positive), np.vstack(hardneg_positive), np.vstack(active_positive)]
    )
    negative_raw = np.vstack(
        [np.vstack(postval_negative), np.vstack(hardneg_negative), np.vstack(active_negative)]
    )
    joint = np.vstack([positive_raw, negative_raw])
    mean = joint.mean(axis=0)
    scale = joint.std(axis=0)
    scale[scale < 1e-8] = 1.0
    positive_reference = (positive_raw - mean) / scale
    negative_reference = (negative_raw - mean) / scale

    source_pool = read_jsonl(SOURCE_POOL)
    reviewed_rows = [*hardneg_review_rows, *positive_review_rows]
    scored_pool = _score_remaining(
        source_pool,
        reviewed_rows,
        positive_reference=positive_reference,
        negative_reference=negative_reference,
        mean=mean,
        scale=scale,
        block_root=block_root,
    )
    selected = select_hard_negative_diverse(scored_pool)
    selected.sort(
        key=lambda row: (
            -float(row["hard_negative_affinity_v2"]),
            str(row["candidate_block"]),
            str(row["event_id"]),
        )
    )
    output.mkdir(parents=True, exist_ok=False)
    scored_path = output / "hard_negative_scored_pool.jsonl"
    write_jsonl(scored_path, scored_pool)
    selected_source: list[dict[str, Any]] = []
    for number, row in enumerate(selected, 1):
        item = dict(row)
        item["review_id"] = f"N{number:03d}"
        item["review_context"] = (
            f"训练块 {item['candidate_block']} · 难负例相似度 {float(item['hard_negative_affinity_v2']):.3f}"
        )
        selected_source.append(item)
    selected_path = output / "selected_candidates.jsonl"
    write_jsonl(selected_path, selected_source)

    review_rows: list[dict[str, Any]] = []
    render_frames: dict[tuple[str, str], pd.DataFrame] = {}
    spec_by_id = {str(spec["block_id"]): spec for spec in block_specs(block_root)}
    for number, row in enumerate(selected_source, 1):
        key = (str(row["candidate_block"]), str(row["symbol"]))
        if key not in render_frames:
            path = Path(spec_by_id[key[0]]["audit_snapshot"]) / "kline_snapshot" / f"{key[1]}.csv"
            render_frames[key] = _load_enriched(path)
        rendered = render_event(row, render_frames[key], output, str(row["review_id"]))
        rendered["training_eligibility_reason"] = "hard-negative expansion review only; pending Owner decision"
        review_rows.append(rendered)
        if number % 25 == 0 or number == len(selected_source):
            print(f"hard-negative expansion render [{number}/{len(selected_source)}]", flush=True)
    review_manifest = output / "review_manifest.jsonl"
    write_jsonl(review_manifest, review_rows)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(
        render_html(
            review_rows,
            selected_path,
            output_html,
            protocol=PROTOCOL,
            title="第三臂难负例扩充200张审核（预期多数不对）",
            heading="Owner-short · 难负例扩充200张",
            description="本页专门找模型误报，预期多数按3；左图只到decision，右图未来48根仅供人工判断。",
            notice="本页目标就是收集“不对”：不是目标按3，3是有效难负例而不是失败；真正目标形态才按1，形态对但框偏按2。",
        ),
        encoding="utf-8",
    )

    selected_counts = Counter(str(row["candidate_block"]) for row in review_rows)
    image_paths = [
        ROOT / str(row[path_key])
        for row in review_rows
        for path_key in ("causal_input_path", "causal_review_path", "future_review_path")
    ]
    reviewed_ids = {str(row["event_id"]) for row in reviewed_rows}
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "weights_sha256": EXPECTED_WEIGHTS_SHA256,
        "train_end": train_end.isoformat(),
        "source_candidate_pool": len(source_pool),
        "already_reviewed_excluded": len(reviewed_ids),
        "unreviewed_scored_pool": len(scored_pool),
        "selected_review": len(review_rows),
        "selected_by_block": dict(selected_counts),
        "selected_symbols": len({str(row["symbol"]) for row in review_rows}),
        "reference_counts": {
            "positive_total": len(positive_reference),
            "negative_total": len(negative_reference),
            "train_owner_gold_positive": len(gold_positive),
            "postval_semantic_positive": len(postval_positive),
            "train_review_positive": len(hardneg_positive) + len(active_positive),
            "postval_hard_negative": len(postval_negative),
            "train_review_hard_negative": len(hardneg_negative) + len(active_negative),
        },
        "hard_negative_affinity_v2": {
            "p10": float(np.quantile([float(row["hard_negative_affinity_v2"]) for row in review_rows], 0.10)),
            "median": float(np.median([float(row["hard_negative_affinity_v2"]) for row in review_rows])),
            "p90": float(np.quantile([float(row["hard_negative_affinity_v2"]) for row in review_rows], 0.90)),
        },
        "future_used_for_selection": False,
        "holdout_read": False,
        "owner_decisions_preselected": 0,
        "training_eligible": 0,
        "labels_created": 0,
        "html": str(output_html.relative_to(ROOT)),
        "source_pool_sha256": sha256_file(SOURCE_POOL),
        "hardneg_review_labeled_sha256": sha256_file(HARDNEG_REVIEW_LABELED),
        "positive_review_labeled_sha256": sha256_file(POSITIVE_REVIEW_LABELED),
        "hard_negative_scored_pool_sha256": sha256_file(scored_path),
        "selected_candidates_sha256": sha256_file(selected_path),
        "review_manifest_sha256": sha256_file(review_manifest),
        "html_sha256": sha256_file(output_html),
        "quality_gates": {
            "exactly_200_unique_events": len(review_rows) == REVIEW_TOTAL
            and len({str(row["event_id"]) for row in review_rows}) == REVIEW_TOTAL,
            "fixed_block_quotas": dict(selected_counts) == BLOCK_QUOTAS,
            "all_new_unreviewed_events": not ({str(row["event_id"]) for row in review_rows} & reviewed_ids),
            "all_decisions_within_train": all(utc(row["decision_time"]) <= train_end for row in review_rows),
            "all_future_context_within_train": all(utc(row["future_review_end_time"]) <= train_end for row in review_rows),
            "no_owner_box_overlap": all(not row["touches_owner_box_guard"] for row in review_rows),
            "selection_is_causal": all(not row["hard_negative_expansion_future_used"] for row in review_rows),
            "nothing_training_eligible": all(not row["training_eligible"] for row in review_rows),
            "no_label_directory": not (output / "labels").exists(),
            "strictly_preholdout": all(utc(row["future_review_end_time"]) < HOLDOUT_START for row in review_rows),
            "all_three_images_exist": len(image_paths) == REVIEW_TOTAL * 3 and all(path.is_file() for path in image_paths),
            "all_full_48_future": all(int(row["future_review_bars"]) == 48 for row in review_rows),
        },
    }
    if not all(summary["quality_gates"].values()):
        raise RuntimeError(summary["quality_gates"])
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--block-root", type=Path, default=BLOCK_ROOT)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--html", type=Path, default=OUTPUT_HTML)
    args = parser.parse_args()
    result = build(block_root=args.block_root, output=args.out, output_html=args.html)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
