#!/usr/bin/env python3
"""Build Local Signal V2 P2 round-1 from B2-mined hard negatives.

The script has separable ``prepare``, ``mine``, and ``assemble`` modes so the
expensive inference step can run on the LAN GPU without changing dataset
semantics. Candidate negatives come from the P1 strict-time empty-background
sampler with a different frozen seed. A candidate becomes a hard negative only
when the frozen B2 detector produces a box at conf >= 0.35. Future outcomes are
never read. Train/val windows remain inside their original time blocks.

Round 1 copies the complete B2 P1 dataset and only adds mined hard negatives.
Training is intentionally outside this script and must cold-start from the same
``yolo11s.pt`` recipe as P1 B2 to keep hard negatives as the sole experiment
variable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
BASE_DATASET = PROJECT / "datasets/local_signal_v2_p1_b2_w30"
CANDIDATE_ROOT = PROJECT / "datasets/local_signal_v2_p2_hardneg_candidates_r1"
OUT_DATASET = PROJECT / "datasets/local_signal_v2_p2_hardneg_r1"
BASE_WEIGHTS = PROJECT / "analysis/output/p1_local_signal_v2/training/B2/weights/best.pt"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
CANDIDATE_SEED = 20260811
CANDIDATE_NEG_RATIO = 6.0
MINE_THRESHOLD = 0.35
PREDICT_CONF_FLOOR = 0.001
PREDICT_IOU = 0.70
PROTOCOL = "local_signal_v2_p2_hardneg_r1_20260811"


def read_json(path: Path) -> object:
    return json.loads(path.read_text())


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hard_negative_event_id(row: dict) -> str:
    raw = f"{row['symbol']}|{row['split']}|{row['win_start']}|{row['win_len']}"
    return "hn_" + hashlib.sha1(raw.encode()).hexdigest()[:16]


def prepare_candidates(base: Path, candidate_root: Path) -> dict:
    """Generate deterministic unseen negative candidates inside frozen blocks."""
    from scripts.build_local_signal_v2_stageb import (
        add_negatives,
        derive_negative_time_bounds,
    )

    positives = read_json(base / "w20_manifest.json")
    base_negatives = read_json(base / "w20_neg_manifest.json")
    if not isinstance(positives, list) or not isinstance(base_negatives, list):
        raise ValueError("base manifests must be lists")
    bounds = derive_negative_time_bounds(positives)
    rows = add_negatives(
        positives,
        candidate_root,
        ratio=CANDIDATE_NEG_RATIO,
        seed=CANDIDATE_SEED,
        time_bounds=bounds,
        protocol=PROTOCOL,
        fixed_window_len=30,
    )
    base_stems = {str(row["stem"]) for row in base_negatives}
    rows = [row for row in rows if str(row["stem"]) not in base_stems]
    rows.sort(key=lambda row: (row["split"], row["symbol"], int(row["win_start"])))
    if any(pd.Timestamp(row["end_time"]) >= HOLDOUT_START for row in rows):
        raise ValueError("candidate bank touches holdout")
    write_jsonl(candidate_root / "candidate_neg_manifest.jsonl", rows)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "seed": CANDIDATE_SEED,
        "negative_candidates_per_positive": CANDIDATE_NEG_RATIO,
        "base_negative_stems_excluded": len(base_stems),
        "candidates": len(rows),
        "by_split": {
            split: sum(row["split"] == split for row in rows)
            for split in ("train", "val")
        },
        "time_bounds": {key: str(value) for key, value in bounds.items()},
        "holdout_read": False,
    }
    (candidate_root / "candidate_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    return summary


def mine_candidates(
    candidate_root: Path,
    weights: Path,
    predictions_path: Path,
    *,
    device: str,
    batch: int,
) -> dict:
    """Run the frozen B2 detector once and persist all candidate predictions."""
    from ultralytics import YOLO

    rows = read_jsonl(candidate_root / "candidate_neg_manifest.jsonl")
    model = YOLO(str(weights))
    predictions: dict[str, list[dict]] = {}
    for start in range(0, len(rows), batch):
        chunk = rows[start : start + batch]
        paths = [str(Path(row["out_img"])) for row in chunk]
        results = model.predict(
            paths,
            conf=PREDICT_CONF_FLOOR,
            iou=PREDICT_IOU,
            imgsz=960,
            device=device,
            verbose=False,
        )
        for row, result in zip(chunk, results):
            boxes: list[dict] = []
            if result.boxes is not None and len(result.boxes):
                xywhn = result.boxes.xywhn.cpu().numpy()
                confidence = result.boxes.conf.cpu().numpy()
                boxes = [
                    {
                        "confidence": float(score),
                        "xywhn": [float(value) for value in box[:4]],
                    }
                    for box, score in zip(xywhn, confidence)
                ]
            predictions[str(row["stem"])] = boxes
        if start == 0 or min(start + batch, len(rows)) % 512 == 0 or start + batch >= len(rows):
            print(f"mine {min(start + batch, len(rows))}/{len(rows)}", flush=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "weights": str(weights),
        "weights_sha256": sha256_file(weights),
        "predict_conf_floor": PREDICT_CONF_FLOOR,
        "predict_iou": PREDICT_IOU,
        "mine_threshold": MINE_THRESHOLD,
        "candidate_count": len(rows),
        "predictions": predictions,
        "holdout_read": False,
    }
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    predictions_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def select_hard_negatives(
    candidates: list[dict], predictions: dict[str, list[dict]], threshold: float
) -> list[dict]:
    """Return every unique candidate with at least one box at ``threshold``."""
    selected: list[dict] = []
    seen: set[str] = set()
    for row in candidates:
        stem = str(row["stem"])
        if stem in seen:
            raise ValueError(f"duplicate candidate stem: {stem}")
        seen.add(stem)
        boxes = [box for box in predictions.get(stem, []) if float(box["confidence"]) >= threshold]
        if not boxes:
            continue
        selected.append(
            {
                **row,
                "hard_negative_type": "b2_false_positive_conf035",
                "hard_negative_event_id": hard_negative_event_id(row),
                "mining_max_confidence": max(float(box["confidence"]) for box in boxes),
                "mining_box_count": len(boxes),
            }
        )
    return sorted(selected, key=lambda row: (row["split"], row["symbol"], int(row["win_start"])))


def _rewrite_base_manifest_row(row: dict, out: Path) -> dict:
    updated = dict(row)
    split = str(row["split"])
    image_name = Path(str(row["image_path"])).name
    label_name = Path(str(row["label_path"])).name
    updated["image_path"] = str((out / "images" / split / image_name).relative_to(PROJECT))
    updated["label_path"] = str((out / "labels" / split / label_name).relative_to(PROJECT))
    updated["p2_dataset_version"] = out.name
    return updated


def _rewrite_w20_row(row: dict, out: Path) -> dict:
    updated = dict(row)
    split = str(row["split"])
    updated["out_img"] = str(out / "images" / split / Path(str(row["out_img"])).name)
    updated["out_lbl"] = str(out / "labels" / split / Path(str(row["out_lbl"])).name)
    return updated


def assemble_dataset(
    base: Path, candidate_root: Path, out: Path, predictions_path: Path
) -> dict:
    """Copy P1 B2 and append mined train/val hard negatives."""
    if out.exists():
        raise FileExistsError(f"refusing to overwrite existing dataset: {out}")
    candidates = read_jsonl(candidate_root / "candidate_neg_manifest.jsonl")
    payload = read_json(predictions_path)
    if not isinstance(payload, dict) or not isinstance(payload.get("predictions"), dict):
        raise ValueError("invalid mining predictions")
    selected = select_hard_negatives(candidates, payload["predictions"], MINE_THRESHOLD)
    if not selected:
        raise ValueError("no hard negatives mined")
    shutil.copytree(base, out)

    base_manifest = read_jsonl(base / "manifest.jsonl")
    p2_manifest = [_rewrite_base_manifest_row(row, out) for row in base_manifest]
    positives = [_rewrite_w20_row(row, out) for row in read_json(base / "w20_manifest.json")]
    negatives = [_rewrite_w20_row(row, out) for row in read_json(base / "w20_neg_manifest.json")]
    hard_bank: list[dict] = []
    for row in selected:
        split = str(row["split"])
        target_stem = f"{row['stem']}_p2hnr1"
        image_target = out / "images" / split / f"{target_stem}.png"
        label_target = out / "labels" / split / f"{target_stem}.txt"
        shutil.copy2(Path(row["out_img"]), image_target)
        label_target.write_text("")
        end_time = pd.Timestamp(row["end_time"])
        start_time = pd.Timestamp(row["start_time"])
        if end_time >= HOLDOUT_START:
            raise ValueError("selected hard negative touches holdout")
        negative_row = {
            **row,
            "stem": target_stem,
            "kind": "hard_negative_b2_false_positive",
            "out_img": str(image_target),
            "out_lbl": str(label_target),
            "image_sha256": sha256_file(image_target),
            "label_sha256": sha256_file(label_target),
        }
        negatives.append(negative_row)
        manifest_row = {
            "sample_id": target_stem,
            "event_id": row["hard_negative_event_id"],
            "sample_type": "hard_negative",
            "hard_negative_type": row["hard_negative_type"],
            "source_dataset_version": candidate_root.name,
            "p2_dataset_version": out.name,
            "symbol": row["symbol"],
            "timeframe": "15m",
            "split": split,
            "window_start_bar": int(row["win_start"]),
            "window_len": int(row["win_len"]),
            "window_start_timestamp": str(start_time),
            "window_end_timestamp": str(end_time),
            "visible_end_timestamp": str(end_time),
            "decision_timestamp": str(end_time),
            "future_bars": 0,
            "image_path": str(image_target.relative_to(PROJECT)),
            "label_path": str(label_target.relative_to(PROJECT)),
            "image_exists": True,
            "label_exists": True,
            "image_sha256": negative_row["image_sha256"],
            "label_sha256": negative_row["label_sha256"],
            "mining_threshold": MINE_THRESHOLD,
            "mining_max_confidence": row["mining_max_confidence"],
            "mining_box_count": row["mining_box_count"],
            "renderer_version": row.get("renderer_version"),
            "stage": "P2",
            "mode": "hard_negative_round1",
        }
        p2_manifest.append(manifest_row)
        hard_bank.append({**negative_row, "image_path": manifest_row["image_path"], "label_path": manifest_row["label_path"]})

    write_jsonl(out / "manifest.jsonl", p2_manifest)
    write_jsonl(out / "hard_negative_bank.jsonl", hard_bank)
    (out / "w20_manifest.json").write_text(json.dumps(positives, ensure_ascii=False, indent=2) + "\n")
    (out / "w20_neg_manifest.json").write_text(json.dumps(negatives, ensure_ascii=False, indent=2) + "\n")
    base_summary = read_json(base / "stageb_summary.json")
    counts = {
        "train_positive": sum(row["split"] == "train" for row in positives),
        "val_positive": sum(row["split"] == "val" for row in positives),
        "train_easy_negative": sum(row["split"] == "train" for row in negatives if row.get("kind") == "empty_bg"),
        "val_easy_negative": sum(row["split"] == "val" for row in negatives if row.get("kind") == "empty_bg"),
        "train_hard_negative": sum(row["split"] == "train" for row in hard_bank),
        "val_hard_negative": sum(row["split"] == "val" for row in hard_bank),
    }
    summary = {
        **base_summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "out": str(out),
        "p2_round": 1,
        "base_dataset": str(base),
        "candidate_root": str(candidate_root),
        "candidate_seed": CANDIDATE_SEED,
        "candidate_neg_ratio": CANDIDATE_NEG_RATIO,
        "mine_threshold": MINE_THRESHOLD,
        "counts_p2": counts,
        "counts": {
            "train": counts["train_positive"],
            "val": counts["val_positive"],
            "train_neg": counts["train_easy_negative"] + counts["train_hard_negative"],
            "val_neg": counts["val_easy_negative"] + counts["val_hard_negative"],
        },
        "n_pos_manifest": len(positives),
        "n_neg_manifest": len(negatives),
        "negative_to_positive_ratio": len(negatives) / len(positives),
        "hard_negative_share_of_negatives": len(hard_bank) / len(negatives),
        "future_outcome_used": False,
        "holdout_read": False,
    }
    for name in ("stageb_summary.json", "w20_summary.json", "p2_summary.json"):
        (out / name).write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\nnames:\n  0: dense_start\n"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("prepare", "mine", "assemble", "all"), default="all")
    parser.add_argument("--base", type=Path, default=BASE_DATASET)
    parser.add_argument("--candidate-root", type=Path, default=CANDIDATE_ROOT)
    parser.add_argument("--out", type=Path, default=OUT_DATASET)
    parser.add_argument("--weights", type=Path, default=BASE_WEIGHTS)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--device", default="mps")
    parser.add_argument("--batch", type=int, default=16)
    args = parser.parse_args()
    predictions = args.predictions or args.candidate_root / "mining_predictions.json"
    if args.mode in {"prepare", "all"}:
        print(json.dumps(prepare_candidates(args.base, args.candidate_root), ensure_ascii=False, indent=2))
    if args.mode in {"mine", "all"}:
        payload = mine_candidates(
            args.candidate_root,
            args.weights,
            predictions,
            device=args.device,
            batch=args.batch,
        )
        print(f"predictions={payload['candidate_count']} -> {predictions}")
    if args.mode in {"assemble", "all"}:
        summary = assemble_dataset(args.base, args.candidate_root, args.out, predictions)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
