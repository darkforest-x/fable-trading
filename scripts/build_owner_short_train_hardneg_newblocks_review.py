#!/usr/bin/env python3
"""Build a third 200-event hard-negative review from unused train blocks.

Candidates come only from five new 12-hour blocks that were not used by the
earlier B01--B05 review pages.  Ranking uses the cumulative Owner morphology
references available before this build: frozen train gold positives, the
post-validation review (reference only), and all three completed train-time
reviews.  Candidate features end at ``decision_time``; the following 48 bars
are loaded only after the fixed selection for the human audit panel.

This script creates no labels, does not make rows training-eligible, does not
read holdout, and does not start training or mutate production configuration.
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
    BLOCK_ROOT as OLD_BLOCK_ROOT,
    DATASET_SUMMARY,
    EXPECTED_WEIGHTS_SHA256,
    HOLDOUT_START,
    OWNER_SHEET,
    ROOT,
    WEIGHTS,
    _load_enriched,
    _validate_block,
    causal_feature_vector,
    mean_knn_distance,
    owner_forbidden_time_intervals,
    touches_forbidden,
)
from scripts.build_owner_short_train_positive_retrieval_review import (
    TRAIN_POSITIVE_MANIFEST,
    _gold_reference_vectors,
    _postval_reference_vectors,
    _train_review_reference_vectors,
)


PROTOCOL = "owner_short_train_hardneg_newblocks200_v3_20260811"
BLOCK_ROOT = ROOT / "analysis/output/owner_short_train_hardneg_blocks_v2"
OUTPUT = ROOT / "analysis/output/owner_short_train_hardneg_newblocks200_v3"
OUTPUT_HTML = ROOT / "analysis/html/p2_owner_short_train_hardneg_newblocks200_v3_20260811.html"
REFERENCE_MANIFEST = (
    ROOT
    / "analysis/output/owner_short_gold_center_hardneg_canary_review331_v3"
    / "owner_review_labeled_manifest.jsonl"
)
REVIEW_DIRS = (
    (
        ROOT / "analysis/output/owner_short_train_hardneg_review200_v1",
        {"pending": 0, "target": 18, "rebox": 0, "hard_negative": 182},
    ),
    (
        ROOT / "analysis/output/owner_short_train_positive_retrieval100_v1",
        {"pending": 0, "target": 45, "rebox": 0, "hard_negative": 55},
    ),
    (
        ROOT / "analysis/output/owner_short_train_hardneg_expansion200_v2",
        {"pending": 0, "target": 25, "rebox": 0, "hard_negative": 175},
    ),
)
BLOCKS = (
    ("C01_20250615", "2025-06-15T12:00:00Z"),
    ("C02_20250815", "2025-08-15T12:00:00Z"),
    ("C03_20251015", "2025-10-15T12:00:00Z"),
    ("C04_20251215", "2025-12-15T12:00:00Z"),
    ("C05_20260215", "2026-02-15T12:00:00Z"),
)
REVIEW_PER_BLOCK = 40
REVIEW_TOTAL = len(BLOCKS) * REVIEW_PER_BLOCK
K_NEIGHBOURS = 7
BAR_MINUTES = 15


def block_specs(root: Path = BLOCK_ROOT) -> list[dict[str, Any]]:
    """Return the five unused, frozen train-block contracts."""
    specs: list[dict[str, Any]] = []
    for block_id, scan_end_value in BLOCKS:
        scan_end = utc(scan_end_value)
        audit_end = scan_end + pd.Timedelta(minutes=BAR_MINUTES * 48)
        base = root / block_id
        specs.append(
            {
                "block_id": block_id,
                "scan_end": scan_end,
                "audit_end": audit_end,
                "scan_snapshot": base / "scan_snapshot",
                "audit_snapshot": base / "audit_snapshot",
                "merged_scan": base / "merged",
            }
        )
    return specs


def select_hard_negative_diverse(
    rows: list[dict[str, Any]],
    *,
    total: int = REVIEW_TOTAL,
    preferred_per_block: int = REVIEW_PER_BLOCK,
) -> list[dict[str, Any]]:
    """Select balanced quotas without inventing rows for a sparse block."""
    by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_block[str(row["candidate_block"])].append(row)
    quotas = allocate_block_quotas(
        rows,
        total=total,
        preferred_per_block=preferred_per_block,
    )
    selected: list[dict[str, Any]] = []
    for block_id, _scan_end in BLOCKS:
        ranked = sorted(
            by_block.get(block_id, []),
            key=lambda row: (
                -float(row["hard_negative_affinity_v3"]),
                -float(row["event_conf_max"]),
                str(row["decision_time"]),
                str(row["event_id"]),
            ),
        )
        quota = quotas[block_id]
        chosen: list[dict[str, Any]] = []
        chosen_ids: set[str] = set()
        symbol_counts: Counter[str] = Counter()
        for cap in (1, 2, 3, max(quota, 1)):
            for row in ranked:
                event_id = str(row["event_id"])
                symbol = str(row["symbol"])
                if event_id in chosen_ids or symbol_counts[symbol] >= cap:
                    continue
                chosen.append(row)
                chosen_ids.add(event_id)
                symbol_counts[symbol] += 1
                if len(chosen) == quota:
                    break
            if len(chosen) == quota:
                break
        if len(chosen) != quota:
            raise ValueError(f"{block_id}: need {quota} candidates, have {len(chosen)}")
        selected.extend(chosen)
    return selected


def allocate_block_quotas(
    rows: list[dict[str, Any]],
    *,
    total: int = REVIEW_TOTAL,
    preferred_per_block: int = REVIEW_PER_BLOCK,
) -> dict[str, int]:
    """Keep sparse blocks intact and distribute their shortfall round-robin."""
    available = Counter(str(row["candidate_block"]) for row in rows)
    ordered_blocks = [block_id for block_id, _scan_end in BLOCKS]
    quotas = {
        block_id: min(preferred_per_block, int(available.get(block_id, 0)))
        for block_id in ordered_blocks
    }
    remaining = total - sum(quotas.values())
    if remaining < 0:
        raise ValueError("preferred block quotas exceed requested total")
    while remaining:
        progressed = False
        for block_id in ordered_blocks:
            if quotas[block_id] >= int(available.get(block_id, 0)):
                continue
            quotas[block_id] += 1
            remaining -= 1
            progressed = True
            if not remaining:
                break
        if not progressed:
            raise ValueError(f"need {total} candidates, only {sum(available.values())} available")
    return quotas


def _validate_review(directory: Path, expected_counts: dict[str, int]) -> list[dict[str, Any]]:
    summary_path = directory / "owner_review_summary.json"
    manifest_path = directory / "owner_review_labeled_manifest.jsonl"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if summary.get("counts") != expected_counts:
        raise ValueError(f"Owner review count drift: {summary_path}")
    if not all(summary.get("quality_gates", {}).values()):
        raise ValueError(f"Owner review quality gate failed: {summary_path}")
    return read_jsonl(manifest_path)


def _reference_arrays(
    train_end: pd.Timestamp,
) -> tuple[np.ndarray, np.ndarray, dict[str, int], list[dict[str, Any]]]:
    postval_rows = read_jsonl(REFERENCE_MANIFEST)
    postval_positive, postval_negative = _postval_reference_vectors(postval_rows)
    train_rows: list[dict[str, Any]] = []
    train_positive: list[np.ndarray] = []
    train_negative: list[np.ndarray] = []
    for directory, expected_counts in REVIEW_DIRS:
        rows = _validate_review(directory, expected_counts)
        positives, negatives = _train_review_reference_vectors(rows, OLD_BLOCK_ROOT)
        train_rows.extend(rows)
        train_positive.extend(positives)
        train_negative.extend(negatives)
    gold_positive = _gold_reference_vectors(read_jsonl(TRAIN_POSITIVE_MANIFEST), train_end)
    actual = (
        len(gold_positive),
        len(postval_positive),
        len(train_positive),
        len(postval_negative),
        len(train_negative),
    )
    if actual != (1143, 77, 88, 254, 412):
        raise ValueError(f"cumulative reference count drift: {actual}")
    positive_raw = np.vstack([gold_positive, np.vstack(postval_positive), np.vstack(train_positive)])
    negative_raw = np.vstack([np.vstack(postval_negative), np.vstack(train_negative)])
    counts = {
        "frozen_train_owner_gold_positive": len(gold_positive),
        "postval_semantic_positive_reference_only": len(postval_positive),
        "train_review_positive": len(train_positive),
        "postval_hard_negative_reference_only": len(postval_negative),
        "train_review_hard_negative": len(train_negative),
        "positive_total": len(positive_raw),
        "negative_total": len(negative_raw),
    }
    return positive_raw, negative_raw, counts, train_rows


def _candidate_pool(
    *,
    specs: list[dict[str, Any]],
    train_end: pd.Timestamp,
    positive_raw: np.ndarray,
    negative_raw: np.ndarray,
) -> tuple[list[dict[str, Any]], dict[str, Any], Counter[str]]:
    joint = np.vstack([positive_raw, negative_raw])
    mean = joint.mean(axis=0)
    scale = joint.std(axis=0)
    scale[scale < 1e-8] = 1.0
    positive_reference = (positive_raw - mean) / scale
    negative_reference = (negative_raw - mean) / scale
    forbidden = owner_forbidden_time_intervals(pd.read_csv(OWNER_SHEET))
    pool: list[dict[str, Any]] = []
    vectors: list[np.ndarray] = []
    block_audit: dict[str, Any] = {}
    skips: Counter[str] = Counter()
    for spec in specs:
        events, audit = _validate_block(spec, train_end=train_end)
        block_id = str(spec["block_id"])
        block_audit[block_id] = audit
        frames: dict[str, pd.DataFrame] = {}
        for event in events:
            if utc(event["decision_time"]) > utc(spec["scan_end"]):
                skips["decision_after_block"] += 1
                continue
            if touches_forbidden(event, forbidden):
                skips["touches_owner_box_guard"] += 1
                continue
            symbol = str(event["symbol"])
            source = Path(spec["audit_snapshot"]) / "kline_snapshot" / f"{symbol}.csv"
            if not source.exists():
                skips["missing_audit_symbol"] += 1
                continue
            if symbol not in frames:
                frames[symbol] = _load_enriched(source)
            try:
                vector = causal_feature_vector(event, frames[symbol])
            except (KeyError, ValueError):
                skips["causal_feature_unavailable"] += 1
                continue
            pool.append(
                {
                    **event,
                    "candidate_block": block_id,
                    "owner_box_guard_bars": 12,
                    "touches_owner_box_guard": False,
                    "hard_negative_newblocks_future_used": False,
                    "hard_negative_newblocks_protocol": PROTOCOL,
                    "training_eligible": False,
                }
            )
            vectors.append(vector)
    if not pool:
        raise ValueError("no new-block candidate survived safety filters")
    query = (np.vstack(vectors) - mean) / scale
    positive_distance = mean_knn_distance(query, positive_reference, k=K_NEIGHBOURS)
    negative_distance = mean_knn_distance(query, negative_reference, k=K_NEIGHBOURS)
    for row, pos_distance, neg_distance in zip(pool, positive_distance, negative_distance):
        row["nearest_owner_positive_distance_v3"] = float(pos_distance)
        row["nearest_owner_negative_distance_v3"] = float(neg_distance)
        row["hard_negative_affinity_v3"] = float(pos_distance - neg_distance)
    return pool, block_audit, skips


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
    dataset_summary = json.loads(DATASET_SUMMARY.read_text(encoding="utf-8"))
    train_end = utc(dataset_summary["split_profile"]["train_end_max"])
    positive_raw, negative_raw, reference_counts, train_review_rows = _reference_arrays(train_end)
    specs = block_specs(block_root)
    pool, block_audit, skips = _candidate_pool(
        specs=specs,
        train_end=train_end,
        positive_raw=positive_raw,
        negative_raw=negative_raw,
    )
    selected_quotas = allocate_block_quotas(pool)
    selected = select_hard_negative_diverse(pool)
    selected.sort(
        key=lambda row: (
            -float(row["hard_negative_affinity_v3"]),
            str(row["candidate_block"]),
            str(row["event_id"]),
        )
    )

    output.mkdir(parents=True, exist_ok=False)
    pool_path = output / "candidate_pool.jsonl"
    write_jsonl(pool_path, pool)
    selected_rows: list[dict[str, Any]] = []
    for number, row in enumerate(selected, 1):
        item = dict(row)
        item["review_id"] = f"M{number:03d}"
        item["review_context"] = (
            f"新训练块 {item['candidate_block']} · 难负例相似度 {float(item['hard_negative_affinity_v3']):.3f}"
        )
        selected_rows.append(item)
    selected_path = output / "selected_candidates.jsonl"
    write_jsonl(selected_path, selected_rows)

    review_rows: list[dict[str, Any]] = []
    render_frames: dict[tuple[str, str], pd.DataFrame] = {}
    spec_by_id = {str(spec["block_id"]): spec for spec in specs}
    for number, row in enumerate(selected_rows, 1):
        key = (str(row["candidate_block"]), str(row["symbol"]))
        if key not in render_frames:
            source = Path(spec_by_id[key[0]]["audit_snapshot"]) / "kline_snapshot" / f"{key[1]}.csv"
            render_frames[key] = _load_enriched(source)
        rendered = render_event(row, render_frames[key], output, str(row["review_id"]))
        rendered["training_eligibility_reason"] = "new-block review only; pending Owner decision"
        review_rows.append(rendered)
        if number % 25 == 0 or number == len(selected_rows):
            print(f"new-block hard-negative render [{number}/{len(selected_rows)}]", flush=True)
    review_manifest = output / "review_manifest.jsonl"
    write_jsonl(review_manifest, review_rows)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(
        render_html(
            review_rows,
            selected_path,
            output_html,
            protocol=PROTOCOL,
            title="新训练时间块难负例扩挖200张审核",
            heading="Owner-short · 新时间块难负例200张",
            description="来自五个未使用训练块；左图只到decision，右图未来48根仅供人工判断。",
            notice="本页继续收集模型误报：不是目标按3；真正目标按1；形态对但框偏按2。所有样本仍不会自动进入训练。",
        ),
        encoding="utf-8",
    )

    selected_counts = Counter(str(row["candidate_block"]) for row in review_rows)
    selected_ids = {str(row["event_id"]) for row in review_rows}
    prior_ids = {str(row["event_id"]) for row in train_review_rows}
    image_paths = [
        ROOT / str(row[key])
        for row in review_rows
        for key in ("causal_input_path", "causal_review_path", "future_review_path")
    ]
    scan_totals = {
        "symbol_blocks": sum(int(row["symbols"]) for row in block_audit.values()),
        "bar_endpoints": sum(int(row["bar_endpoints"]) for row in block_audit.values()),
        "window_exposures": sum(int(row["window_exposures"]) for row in block_audit.values()),
        "raw_detections": sum(int(row["raw_detections"]) for row in block_audit.values()),
        "deduplicated_events": sum(int(row["events"]) for row in block_audit.values()),
    }
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "weights_sha256": EXPECTED_WEIGHTS_SHA256,
        "train_end": train_end.isoformat(),
        "blocks": block_audit,
        "scan_totals": scan_totals,
        "candidate_pool": len(pool),
        "candidate_skips": dict(skips),
        "selected_review": len(review_rows),
        "selected_by_block": dict(selected_counts),
        "selected_block_quotas": selected_quotas,
        "selected_symbols": len({str(row["symbol"]) for row in review_rows}),
        "reference_counts": reference_counts,
        "hard_negative_affinity_v3": {
            "p10": float(np.quantile([float(row["hard_negative_affinity_v3"]) for row in review_rows], 0.10)),
            "median": float(np.median([float(row["hard_negative_affinity_v3"]) for row in review_rows])),
            "p90": float(np.quantile([float(row["hard_negative_affinity_v3"]) for row in review_rows], 0.90)),
        },
        "future_used_for_selection": False,
        "holdout_read": False,
        "owner_decisions_preselected": 0,
        "training_eligible": 0,
        "labels_created": 0,
        "html": str(output_html.relative_to(ROOT)),
        "candidate_pool_sha256": sha256_file(pool_path),
        "selected_candidates_sha256": sha256_file(selected_path),
        "review_manifest_sha256": sha256_file(review_manifest),
        "html_sha256": sha256_file(output_html),
        "quality_gates": {
            "exactly_200_unique_events": len(review_rows) == REVIEW_TOTAL and len(selected_ids) == REVIEW_TOTAL,
            "balanced_dynamic_block_quotas": dict(selected_counts) == selected_quotas,
            "all_events_new_vs_prior_reviews": not (selected_ids & prior_ids),
            "all_decisions_within_train": all(utc(row["decision_time"]) <= train_end for row in review_rows),
            "all_future_context_within_train": all(
                utc(row["future_review_end_time"]) <= train_end for row in review_rows
            ),
            "no_owner_box_overlap": all(not row["touches_owner_box_guard"] for row in review_rows),
            "selection_is_causal": all(not row["hard_negative_newblocks_future_used"] for row in review_rows),
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
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
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
