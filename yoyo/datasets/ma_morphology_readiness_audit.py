"""Audit learning-design risks separately from v6 asset/format integrity.

Uses only frozen manifests, actual TXT labels, recorded predictions and training
logs. No inference, training, relabeling or price access is performed. The
metadata-only horizontal template control is not detection mAP: it tests how
much x geometry is predetermined by the crop protocol, not recognition quality.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import random
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "datasets/ma_launch_owner1500_morph_v6_positions_ready_20260925_v2"
OLD = ROOT / "experiments/active/exp-ma-morphology-negatives-20260922-v3"
REPLAY = ROOT / "experiments/active/exp-ma-morphology-top20-20260922-v1"


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def horizontal_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    left, right = max(a[0], b[0]), min(a[1], b[1])
    intersection = max(0.0, right - left)
    return intersection / (a[1] - a[0] + b[1] - b[0] - intersection)


def audit() -> dict:
    manifest = DATASET / "manifest.jsonl"
    rows = read_rows(manifest)
    corrected = [r for r in rows if not r.get("fixed_reference_only")]
    strata, horizontal, train_templates, test = {}, {}, defaultdict(list), []
    for split in ("train", "val", "test"):
        selected = [r for r in corrected if r["split"] == split]
        positive = [r for r in selected if r["sample_kind"] == "positive"]
        negative = [r for r in selected if r["sample_kind"] == "negative"]
        kinds = Counter(r.get("negative_kind") for r in negative)
        strata[split] = {
            "positive_images": len(positive), "negative_images": len(negative),
            "negative_kind_images": dict(kinds),
            "hard_negative_events": len({r["event_id"] for r in negative if r.get("negative_kind") == "grade_a_hard"}),
            "sample_owner_confirmed_positive_images": sum(r.get("sample_owner_confirmed") is True for r in positive),
        }
        centers, widths = Counter(), Counter()
        for row in positive:
            _, cx, _, width, _ = map(float, (DATASET / row["label_path"]).read_text().split())
            centers[f"{cx:.3f}"] += 1
            widths[f"{width:.3f}"] += 1
            key = (row["core_bars"], row["variant"])
            interval = (cx - width / 2, cx + width / 2)
            if split == "train":
                train_templates[key].append(interval)
            elif split == "test":
                test.append((key, interval))
        horizontal[split] = {"center_histogram_round3": dict(centers), "width_histogram_round3": dict(widths)}
    templates = {key: (median(v[0] for v in values), median(v[1] for v in values)) for key, values in train_templates.items()}
    actual = [horizontal_iou(templates[key], box) for key, box in test]
    # Label-assignment permutation control, stratified by original core length.
    rng = random.Random(0)
    shuffled, groups = [], defaultdict(list)
    for key, interval in test:
        groups[key[0]].append((key, interval))
    for group in groups.values():
        keys = [key for key, _ in group]
        rng.shuffle(keys)
        shuffled.extend(horizontal_iou(templates[key], box) for key, (_, box) in zip(keys, group))
    predictions = REPLAY / "results/predictions.jsonl"
    post = Counter()
    scored = 0
    for row in read_rows(predictions):
        if row.get("status") != "scored":
            continue
        scored += 1
        for box in row["boxes"]:
            post[str(row["n_bars"] - 1 - box["predicted_core_end_local"])] += 1
    duration = {}
    source_paths = [manifest, predictions, OLD / "results_summary.json", OLD / "evaluation_diagnosis/padding_comparison.json"]
    for arm in ("A", "B"):
        path = OLD / f"collected/training/arm_{arm}/results.csv"
        records = list(csv.DictReader(path.open()))
        last = {k.strip(): v for k, v in records[-1].items()}
        duration[arm] = {"epochs": int(last["epoch"]), "recorded_epoch_time_seconds": float(last["time"])}
        source_paths.append(path)
    digest_splits = defaultdict(set)
    event_splits = defaultdict(set)
    for row in rows:
        digest_splits[row["image_sha256"]].add(row["split"])
        event_splits[row["event_id"]].add(row["split"])
    padding = json.loads((OLD / "evaluation_diagnosis/padding_comparison.json").read_text())
    return {
        "status": "learning_design_not_accepted", "training_suitability": "blocked",
        "asset_integrity": "previous_dual_host_audit_passed_not_a_learning_quality_verdict",
        "dataset": str(DATASET.relative_to(ROOT)), "strata": strata, "horizontal_geometry": horizontal,
        "positive_events": len({r["event_id"] for r in corrected if r["sample_kind"] == "positive"}),
        "positive_selection_events": dict(Counter(r.get("positive_selection") for r in corrected if r["sample_kind"] == "positive" and r["variant"] == "R5")),
        "metadata_only_horizontal_control": {
            "uses_images": False, "uses_class_or_vertical_coordinates": False,
            "templates_fitted_on": "train only; key=(core_bars,variant)", "test_images": len(test),
            "mean_horizontal_iou": mean(actual), "fraction_horizontal_iou_ge_0_5": mean(x >= .5 for x in actual),
            "shuffled_variant_mean_horizontal_iou": mean(shuffled),
            "shuffled_variant_fraction_horizontal_iou_ge_0_5": mean(x >= .5 for x in shuffled),
            "seed": 0, "interpretation": "Crop metadata predicts x geometry; this is not detector accuracy or a false-positive estimate.",
        },
        "recorded_v6a_replay": {"scored_windows": scored, "raw_boxes": sum(post.values()), "post_bar_histogram": dict(post), "post5_fraction": post["5"] / sum(post.values()), "false_positive_rate": "unknown_without_ground_truth"},
        "recorded_v6a_padding_map50": {k: v["metrics"]["metrics/mAP50(B)"] for k, v in padding["runs"].items()},
        "old_training_duration": duration,
        "cross_split_checks": {"exact_image_hash_conflicts": sum(len(s) > 1 for s in digest_splits.values()), "same_event_id_conflicts": sum(len(s) > 1 for s in event_splits.values()), "near_duplicate_visual_audit": "not_completed"},
        "confirmed_gaps": ["Only three fixed post-bar positions and two width templates; no off-template position challenge", "No hard negatives in corrected test views", "All positive events are outcome-selected rule candidates, not individually owner-confirmed gold", "Old v6A padding sensitivity remains an unresolved model limitation", "Monitor adapter uses Grade-A/CLOSE/green-red/W18-W19, while the new candidate uses HL2/blue-purple/W20-W21; no interchangeability claim is justified"],
        "sources": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
        "training_started": False, "model_changed": False, "production_eligible": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = audit()
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": result["status"], "strata": result["strata"], "control": result["metadata_only_horizontal_control"], "replay": result["recorded_v6a_replay"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
