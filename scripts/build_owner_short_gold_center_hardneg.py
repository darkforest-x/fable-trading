#!/usr/bin/env python3
"""Build the Owner-short 1:3 hard-negative arm without reading holdout.

The completed 1:1 baseline is immutable.  This builder copies it and changes
one training variable only: two hard negatives are appended per train positive.
Hard negatives come from two auditable sources:

1. compact crops around boxes the Owner explicitly classified as ``long``;
   they are visually close platform/start structures but the wrong direction
   for the short-only detector;
2. model-ranked empty backgrounds from the original train time block, outside
   every known Owner box (both sides) plus a 12-bar guard.

The final hard-negative W12--19 histogram is exactly twice the train-positive
histogram.  Existing train positives/easy negatives and the complete validation
set remain byte-identical.  Model score only ranks background candidates; no
score threshold is selected or promoted, no future return is read, and the
2026-05-04+ holdout is never materialized.

Phases are separable so the builder is committed before artifacts exist and the
``mine`` phase can run on the LAN RTX 3060.  This script never starts training.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
YOYO_REPO = Path.home() / "yoyo-trading"
for module_path in (ROOT, YOYO_REPO):
    if str(module_path) not in sys.path:
        sys.path.insert(0, str(module_path))

from yoyo.layers.l1_detection.data import add_mas  # noqa: E402
from yoyo.layers.l1_detection.render import render_chart  # noqa: E402

from scripts.build_owner_eth_shortdelay_calibration import load_preholdout_prefix  # noqa: E402
from scripts.build_owner_gold_center_crop_review import central_core, dynamic_context  # noqa: E402
from scripts.build_owner_short_gold_center_dataset import (  # noqa: E402
    MA_WARMUP_BARS,
    NEG_GUARD_BARS,
    overlaps,
    owner_forbidden_intervals,
)


PROTOCOL = "owner_short_gold_center_hardneg_r1_20260811"
BASE = ROOT / "datasets/owner_short_gold_center_v1"
CANDIDATE_ROOT = ROOT / "datasets/owner_short_gold_center_hardneg_candidates_r1"
OUT = ROOT / "datasets/owner_short_gold_center_hardneg_r1"
AUDIT_HTML = ROOT / "analysis/html/p2_owner_short_gold_center_hardneg_audit200_20260811.html"
WEIGHTS = (
    ROOT
    / "analysis/output/lsv2_stageb/owner_lsv2_short_gold_center_v1_ft/weights/best.pt"
)
OWNER_SHEET = ROOT / "analysis/output/owner_side_review/review_sheet.csv"
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
BAR_MINUTES = 15
POOL_PER_POSITIVE = 6
HARD_PER_POSITIVE = 2
PREDICT_CONF_FLOOR = 0.001
PREDICT_IOU = 0.70
IMG_SIZE = 960
MAX_RANDOM_TRIES = 2000


def stable_seed(*parts: object) -> int:
    payload = "|".join(map(str, parts)).encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:8], 16)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def owner_long_plans(
    sheet: pd.DataFrame,
    *,
    source_by_symbol: dict[str, str],
    train_end: pd.Timestamp,
    short_forbidden: dict[str, list[tuple[int, int]]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Create train-only compact wrong-direction plans from Owner long boxes."""
    planned: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    seen: set[tuple[Any, ...]] = set()
    for row in sheet[sheet["owner_side"].astype(str).str.lower().eq("long")].to_dict("records"):
        symbol = str(row["symbol"])
        if symbol not in source_by_symbol:
            skips["symbol_outside_short_training_universe"] += 1
            continue
        owner_end = int(row["cut_global"])
        owner_start = owner_end - int(row["width_bars"]) + 1
        core_start, core_end = central_core(owner_start, owner_end)
        pre_bars, post_bars = dynamic_context(owner_start, owner_end, core_start, core_end)
        win_start = core_start - pre_bars
        win_end = core_end + post_bars
        end_time = pd.Timestamp(row["cut_time"]) + timedelta(
            minutes=(win_end - owner_end) * BAR_MINUTES
        )
        if end_time > train_end:
            skips["after_train_end"] += 1
            continue
        if end_time >= HOLDOUT_START:
            raise ValueError("Owner-long plan touches holdout")
        # A long-labelled crop is useful only when it does not also cover a
        # short Owner box.  Keep the same 12-bar semantic guard as backgrounds.
        if overlaps((win_start, win_end), short_forbidden.get(symbol, [])):
            skips["overlaps_owner_short_guard"] += 1
            continue
        key = (symbol, win_start, win_end, core_start, core_end)
        if key in seen:
            skips["duplicate_target"] += 1
            continue
        seen.add(key)
        planned.append(
            {
                "sample_id": f"hnlong_{row['box_id']}",
                "owner_box_id": str(row["box_id"]),
                "symbol": symbol,
                "source_csv": source_by_symbol[symbol],
                "split": "train",
                "win_start": win_start,
                "win_end": win_end,
                "win_len": win_end - win_start + 1,
                "core_start": core_start,
                "core_end": core_end,
                "core_bars": core_end - core_start + 1,
                "end_time": end_time.isoformat(),
                "hard_negative_source": "owner_explicit_long_wrong_direction",
                "owner_semantic_verdict": "long",
                "later_return_used": False,
                "model_score_used": False,
            }
        )
    planned.sort(key=lambda row: (int(row["win_len"]), str(row["symbol"]), int(row["win_start"])))
    return planned, dict(skips)


def plan_background_pool(
    train_positives: list[dict[str, Any]],
    base_negatives: list[dict[str, Any]],
    long_plans: list[dict[str, Any]],
    frames: dict[str, pd.DataFrame],
    forbidden: dict[str, list[tuple[int, int]]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Sample six unseen safe backgrounds per train positive before scoring."""
    lower_time = min(pd.Timestamp(row["start_time"]) for row in train_positives)
    upper_time = max(pd.Timestamp(row["end_time"]) for row in train_positives)
    used: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for row in [*train_positives, *base_negatives, *long_plans]:
        used[str(row["symbol"])].add((int(row["win_start"]), int(row["win_end"])))
    candidates: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    for positive in sorted(train_positives, key=lambda row: (row["symbol"], row["sample_id"])):
        symbol = str(positive["symbol"])
        frame = frames[symbol]
        times = pd.to_datetime(frame["open_time"], utc=True)
        win_len = int(positive["win_len"])
        low = max(MA_WARMUP_BARS, int(times.searchsorted(lower_time, side="left")))
        high = min(
            len(frame) - win_len,
            int(times.searchsorted(upper_time, side="right")) - win_len,
        )
        if high < low:
            skips["no_time_room"] += POOL_PER_POSITIVE
            continue
        for slot in range(POOL_PER_POSITIVE):
            rng = np.random.default_rng(
                stable_seed(PROTOCOL, "background", positive["sample_id"], slot)
            )
            chosen: tuple[int, int] | None = None
            for _attempt in range(MAX_RANDOM_TRIES):
                start = int(rng.integers(low, high + 1))
                interval = (start, start + win_len - 1)
                if interval in used[symbol]:
                    continue
                if overlaps(interval, forbidden.get(symbol, [])):
                    continue
                chosen = interval
                break
            if chosen is None:
                # Deterministic exhaustive fallback preserves the all-Owner-box
                # guard and exact uniqueness; it never relaxes into holdout.
                offset = stable_seed(PROTOCOL, positive["sample_id"], slot, "fallback") % (high - low + 1)
                for step in range(high - low + 1):
                    start = low + (offset + step) % (high - low + 1)
                    interval = (start, start + win_len - 1)
                    if interval in used[symbol] or overlaps(interval, forbidden.get(symbol, [])):
                        continue
                    chosen = interval
                    break
            if chosen is None:
                skips["sampling_exhausted"] += 1
                continue
            used[symbol].add(chosen)
            start, end = chosen
            candidates.append(
                {
                    "sample_id": f"hncand_{positive['sample_id']}_{slot:02d}",
                    "matched_positive_id": str(positive["sample_id"]),
                    "symbol": symbol,
                    "source_csv": str(positive["source_csv"]),
                    "split": "train",
                    "win_start": start,
                    "win_end": end,
                    "win_len": win_len,
                    "start_time": pd.Timestamp(times.iloc[start]).isoformat(),
                    "end_time": pd.Timestamp(times.iloc[end]).isoformat(),
                    "selection_method": "same_train_block_outside_all_owner_boxes_rank_later",
                    "owner_guard_bars": NEG_GUARD_BARS,
                    "later_return_used": False,
                    "model_score_used": False,
                }
            )
    return candidates, dict(skips)


def load_frames(
    positives: list[dict[str, Any]],
    negatives: list[dict[str, Any]],
    long_plans: list[dict[str, Any]],
) -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, Any]]]:
    source_by_symbol = {str(row["symbol"]): str(row["source_csv"]) for row in positives}
    required_end: dict[str, int] = defaultdict(int)
    for row in [*positives, *negatives, *long_plans]:
        symbol = str(row["symbol"])
        required_end[symbol] = max(required_end[symbol], int(row["win_end"]))
    frames: dict[str, pd.DataFrame] = {}
    audits: dict[str, dict[str, Any]] = {}
    for symbol in sorted(required_end):
        frame, audit = load_preholdout_prefix(
            ROOT / source_by_symbol[symbol], required_end[symbol]
        )
        frames[symbol] = frame
        audits[symbol] = audit
    return frames, audits


def render_empty_rows(
    rows: list[dict[str, Any]],
    frames: dict[str, pd.DataFrame],
    output_dir: Path,
    kind: str,
) -> list[dict[str, Any]]:
    enriched: dict[str, pd.DataFrame] = {}
    rendered: list[dict[str, Any]] = []
    for number, row in enumerate(rows, 1):
        symbol = str(row["symbol"])
        if symbol not in enriched:
            enriched[symbol] = add_mas(frames[symbol])
        window = enriched[symbol].iloc[int(row["win_start"]) : int(row["win_end"]) + 1]
        if len(window) != int(row["win_len"]):
            raise ValueError(f"window length mismatch: {row['sample_id']}")
        image, _transform = render_chart(window.reset_index(drop=True), out_path=None)
        image_path = output_dir / "images" / kind / f"{row['sample_id']}.png"
        label_path = output_dir / "labels" / kind / f"{row['sample_id']}.txt"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(image_path), image):
            raise OSError(image_path)
        label_path.write_text("", encoding="utf-8")
        rendered.append(
            {
                **row,
                "candidate_kind": kind,
                "image_path": str(image_path),
                "label_path": str(label_path),
                "image_sha256": sha256_file(image_path),
                "label_sha256": sha256_file(label_path),
            }
        )
        if number % 512 == 0 or number == len(rows):
            print(f"render {kind} {number}/{len(rows)}", flush=True)
    return rendered


def prepare(base: Path, candidate_root: Path, sheet_path: Path) -> dict[str, Any]:
    if candidate_root.exists():
        raise FileExistsError(f"refusing to overwrite {candidate_root}")
    positives = read_jsonl(base / "positive_manifest.jsonl")
    negatives = read_jsonl(base / "negative_manifest.jsonl")
    train_positives = [row for row in positives if row["split"] == "train"]
    summary = json.loads((base / "summary.json").read_text(encoding="utf-8"))
    train_end = pd.Timestamp(summary["split_profile"]["train_end_max"])
    sheet = pd.read_csv(sheet_path)
    sheet["cut_time"] = pd.to_datetime(sheet["cut_time"], utc=True, errors="raise")
    source_by_symbol = {str(row["symbol"]): str(row["source_csv"]) for row in positives}
    short_sheet = sheet[sheet["owner_side"].astype(str).str.lower().eq("short")].copy()
    short_forbidden = owner_forbidden_intervals(short_sheet)
    long_plans, long_skips = owner_long_plans(
        sheet,
        source_by_symbol=source_by_symbol,
        train_end=train_end,
        short_forbidden=short_forbidden,
    )
    frames, read_audits = load_frames(positives, negatives, long_plans)
    all_forbidden = owner_forbidden_intervals(sheet)
    candidates, candidate_skips = plan_background_pool(
        train_positives,
        negatives,
        long_plans,
        frames,
        all_forbidden,
    )
    rendered_long = render_empty_rows(long_plans, frames, candidate_root, "owner_long")
    rendered_candidates = render_empty_rows(
        candidates, frames, candidate_root, "model_mined_pool"
    )
    write_jsonl(candidate_root / "owner_long_manifest.jsonl", rendered_long)
    write_jsonl(candidate_root / "background_candidate_manifest.jsonl", rendered_candidates)
    target_by_w = {
        str(window): HARD_PER_POSITIVE * count
        for window, count in sorted(
            Counter(int(row["win_len"]) for row in train_positives).items()
        )
    }
    protocol = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "base_dataset": str(base.relative_to(ROOT)),
        "base_summary_sha256": sha256_file(base / "summary.json"),
        "train_positive": len(train_positives),
        "train_easy_negative": sum(row["split"] == "train" for row in negatives),
        "val_positive": sum(row["split"] == "val" for row in positives),
        "val_easy_negative": sum(row["split"] == "val" for row in negatives),
        "hard_negative_target": HARD_PER_POSITIVE * len(train_positives),
        "hard_negative_target_by_w": target_by_w,
        "owner_long_candidates": len(rendered_long),
        "background_pool": len(rendered_candidates),
        "background_pool_per_positive": POOL_PER_POSITIVE,
        "long_skips": long_skips,
        "background_skips": candidate_skips,
        "train_end": train_end.isoformat(),
        "holdout_read": False,
        "future_outcome_used": False,
        "model_score_used_in_prepare": False,
        "selection_rule": "all_safe_owner_long_then_top_model_score_per_W_to_exact_2x_positive_W",
        "score_threshold_selected": False,
        "validation_mutated": False,
        "training_started": False,
        "source_read_audits": read_audits,
    }
    (candidate_root / "protocol.json").write_text(
        json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return protocol


def mine(
    candidate_root: Path,
    weights: Path,
    predictions_path: Path,
    *,
    device: str,
    batch: int,
) -> dict[str, Any]:
    from ultralytics import YOLO  # noqa: PLC0415

    candidates = read_jsonl(candidate_root / "background_candidate_manifest.jsonl")
    model = YOLO(str(weights))
    predictions: list[dict[str, Any]] = []
    for start in range(0, len(candidates), batch):
        chunk = candidates[start : start + batch]
        results = model.predict(
            [str(row["image_path"]) for row in chunk],
            conf=PREDICT_CONF_FLOOR,
            iou=PREDICT_IOU,
            imgsz=IMG_SIZE,
            device=device,
            batch=batch,
            verbose=False,
            augment=False,
            save=False,
        )
        for row, result in zip(chunk, results):
            scores = (
                result.boxes.conf.detach().cpu().numpy().astype(float).tolist()
                if result.boxes is not None and len(result.boxes)
                else []
            )
            predictions.append(
                {
                    "sample_id": row["sample_id"],
                    "max_confidence": max(scores, default=0.0),
                    "box_count_at_floor": len(scores),
                }
            )
        done = min(start + batch, len(candidates))
        if done % 512 == 0 or done == len(candidates):
            print(f"mine {done}/{len(candidates)}", flush=True)
    write_jsonl(predictions_path, predictions)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "weights": str(weights),
        "weights_sha256": sha256_file(weights),
        "candidates": len(candidates),
        "predict_conf_floor": PREDICT_CONF_FLOOR,
        "predict_iou": PREDICT_IOU,
        "score_threshold_selected": False,
        "holdout_read": False,
    }
    (predictions_path.parent / "mining_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def select_hard_negatives(
    train_positives: list[dict[str, Any]],
    owner_long: list[dict[str, Any]],
    backgrounds: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Freeze exact W targets: all Owner-long first, then score-ranked backgrounds."""
    target = Counter(int(row["win_len"]) for row in train_positives)
    target = Counter({window: HARD_PER_POSITIVE * count for window, count in target.items()})
    selected: list[dict[str, Any]] = []
    selected_by_w: Counter[int] = Counter()
    for row in sorted(owner_long, key=lambda item: (int(item["win_len"]), item["sample_id"])):
        window = int(row["win_len"])
        if selected_by_w[window] >= target[window]:
            continue
        selected.append({**row, "selected_hard_kind": "owner_long", "selection_rank": None})
        selected_by_w[window] += 1
    score_by_id = {str(row["sample_id"]): row for row in predictions}
    by_w: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in backgrounds:
        score = score_by_id.get(str(row["sample_id"]))
        if score is None:
            raise ValueError(f"missing mining score: {row['sample_id']}")
        by_w[int(row["win_len"])].append({**row, **score})
    mined_selected = 0
    for window in sorted(target):
        need = target[window] - selected_by_w[window]
        ranked = sorted(
            by_w[window],
            key=lambda row: (-float(row["max_confidence"]), str(row["sample_id"])),
        )
        if len(ranked) < need:
            raise ValueError(f"W{window}: need {need} backgrounds, have {len(ranked)}")
        for rank, row in enumerate(ranked[:need], 1):
            selected.append(
                {
                    **row,
                    "selected_hard_kind": "model_ranked_background",
                    "selection_rank": rank,
                }
            )
            selected_by_w[window] += 1
            mined_selected += 1
    if selected_by_w != target:
        raise ValueError(f"hard-negative W mismatch: selected={selected_by_w} target={target}")
    profile = {
        "target_by_w": dict(sorted(target.items())),
        "selected_by_w": dict(sorted(selected_by_w.items())),
        "selected_total": len(selected),
        "owner_long_selected": sum(row["selected_hard_kind"] == "owner_long" for row in selected),
        "model_ranked_selected": mined_selected,
        "model_ranked_zero_score": sum(
            row["selected_hard_kind"] == "model_ranked_background"
            and float(row["max_confidence"]) == 0
            for row in selected
        ),
    }
    return selected, profile


def _rewrite_base_path(value: str, base: Path, out: Path) -> str:
    path = ROOT / value
    try:
        relative = path.relative_to(base)
    except ValueError:
        return value
    return str((out / relative).relative_to(ROOT))


def verify_base_copy(base: Path, out: Path) -> dict[str, int]:
    checked = 0
    for top in ("images", "labels"):
        for source in sorted((base / top).rglob("*")):
            expected_suffix = ".png" if top == "images" else ".txt"
            if (
                not source.is_file()
                or source.name.startswith("._")
                or source.suffix.lower() != expected_suffix
            ):
                continue
            target = out / source.relative_to(base)
            if not target.exists() or sha256_file(source) != sha256_file(target):
                raise ValueError(f"base copy drift: {source}")
            checked += 1
    return {"byte_identical_base_files": checked}


def assemble(
    base: Path,
    candidate_root: Path,
    out: Path,
    predictions_path: Path,
) -> dict[str, Any]:
    if out.exists():
        raise FileExistsError(f"refusing to overwrite {out}")
    positives = read_jsonl(base / "positive_manifest.jsonl")
    negatives = read_jsonl(base / "negative_manifest.jsonl")
    train_positives = [row for row in positives if row["split"] == "train"]
    owner_long = read_jsonl(candidate_root / "owner_long_manifest.jsonl")
    backgrounds = read_jsonl(candidate_root / "background_candidate_manifest.jsonl")
    predictions = read_jsonl(predictions_path)
    selected, selection_profile = select_hard_negatives(
        train_positives, owner_long, backgrounds, predictions
    )
    shutil.copytree(base, out)
    copy_profile = verify_base_copy(base, out)
    hard_manifest: list[dict[str, Any]] = []
    for number, row in enumerate(selected, 1):
        stem = f"hard_{number:05d}_{row['sample_id']}"
        image_target = out / "images/train" / f"{stem}.png"
        label_target = out / "labels/train" / f"{stem}.txt"
        shutil.copy2(Path(row["image_path"]), image_target)
        label_target.write_text("", encoding="utf-8")
        hard_manifest.append(
            {
                **row,
                "sample_id": stem,
                "source_sample_id": row["sample_id"],
                "split": "train",
                "class": "hard_negative",
                "image_path": str(image_target.relative_to(ROOT)),
                "label_path": str(label_target.relative_to(ROOT)),
                "image_sha256": sha256_file(image_target),
                "label_sha256": sha256_file(label_target),
                "holdout_read": False,
                "future_outcome_used": False,
            }
        )
    rewritten_positives = []
    for row in positives:
        item = dict(row)
        item["image_path"] = _rewrite_base_path(str(row["image_path"]), base, out)
        item["label_path"] = _rewrite_base_path(str(row["label_path"]), base, out)
        rewritten_positives.append(item)
    rewritten_negatives = []
    for row in negatives:
        item = dict(row)
        item["image_path"] = _rewrite_base_path(str(row["image_path"]), base, out)
        item["label_path"] = _rewrite_base_path(str(row["label_path"]), base, out)
        rewritten_negatives.append(item)
    rewritten_negatives.extend(hard_manifest)
    joint_hashes = [
        (str(row["image_sha256"]), str(row["label_sha256"]))
        for row in [*rewritten_positives, *rewritten_negatives]
    ]
    duplicate_joint_hashes = len(joint_hashes) - len(set(joint_hashes))
    if duplicate_joint_hashes:
        raise ValueError(f"duplicate image+label training examples: {duplicate_joint_hashes}")
    write_jsonl(out / "positive_manifest.jsonl", rewritten_positives)
    write_jsonl(out / "negative_manifest.jsonl", rewritten_negatives)
    write_jsonl(out / "hard_negative_manifest.jsonl", hard_manifest)
    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\n"
        "names:\n  0: owner_short_platform\n",
        encoding="utf-8",
    )
    counts = {
        "train_positive": sum(row["split"] == "train" for row in positives),
        "val_positive": sum(row["split"] == "val" for row in positives),
        "train_easy_negative": sum(row["split"] == "train" for row in negatives),
        "val_easy_negative": sum(row["split"] == "val" for row in negatives),
        "train_hard_negative": len(hard_manifest),
    }
    if counts != {
        "train_positive": 1143,
        "val_positive": 202,
        "train_easy_negative": 1143,
        "val_easy_negative": 200,
        "train_hard_negative": 2286,
    }:
        raise ValueError(f"unexpected frozen counts: {counts}")
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "base_dataset": str(base.relative_to(ROOT)),
        "dataset": str(out.relative_to(ROOT)),
        "counts": counts,
        "train_negative_to_positive": (
            counts["train_easy_negative"] + counts["train_hard_negative"]
        )
        / counts["train_positive"],
        "hard_share_of_train_negatives": counts["train_hard_negative"]
        / (counts["train_easy_negative"] + counts["train_hard_negative"]),
        "selection_profile": selection_profile,
        "duplicate_joint_sha256": duplicate_joint_hashes,
        **copy_profile,
        "validation_mutated": False,
        "holdout_read": False,
        "future_outcome_used": False,
        "score_threshold_selected": False,
        "training_started": False,
        "auto_promote": False,
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _audit_cards(rows: list[dict[str, Any]], *, section: str) -> str:
    cards: list[str] = []
    for number, row in enumerate(rows, 1):
        source = Path("../../") / str(row["image_path"])
        score = row.get("max_confidence")
        score_text = "Owner人工long" if score is None else f"baseline max conf {float(score):.3f}"
        band = ""
        if row.get("core_start") is not None:
            window = int(row["win_len"])
            local_start = int(row["core_start"]) - int(row["win_start"])
            local_end = int(row["core_end"]) - int(row["win_start"])
            left = max(0.0, local_start / max(1, window - 1) * 98.0 + 1.0)
            right = min(100.0, local_end / max(1, window - 1) * 98.0 + 1.0)
            band = f'<i class="core" style="left:{left:.3f}%;width:{max(1.0,right-left):.3f}%"></i>'
        cards.append(
            f"""<article><h3>{section} #{number:03d} · {html.escape(str(row['symbol']))} · W{int(row['win_len'])}</h3>
<p>{html.escape(score_text)} · {html.escape(str(row.get('end_time','')))}</p>
<a class="chart" href="{source.as_posix()}" target="_blank"><img loading="lazy" src="{source.as_posix()}" alt="hard negative">{band}</a></article>"""
        )
    return "".join(cards)


def build_audit(dataset: Path, output: Path, *, per_kind: int = 100) -> dict[str, Any]:
    """Write a real 200-card HTML audit, not three contact-sheet thumbnails."""
    rows = read_jsonl(dataset / "hard_negative_manifest.jsonl")
    owner_long = [row for row in rows if row["selected_hard_kind"] == "owner_long"]
    model_ranked = [
        row for row in rows if row["selected_hard_kind"] == "model_ranked_background"
    ]
    owner_long.sort(key=lambda row: stable_seed(PROTOCOL, "audit", row["sample_id"]))
    model_ranked.sort(
        key=lambda row: (-float(row["max_confidence"]), str(row["sample_id"]))
    )
    owner_chosen = owner_long[:per_kind]
    model_chosen = model_ranked[:per_kind]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Owner-short hard negative 200张审计</title>
<style>body{{margin:0;background:#edf2f5;color:#172631;font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}header{{background:#14212c;color:#fff;padding:25px 30px}}header p{{margin:6px 0;color:#d5e0e8}}main{{padding:18px;max-width:1600px;margin:auto}}h2{{grid-column:1/-1;margin:18px 0 0}}section{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}article{{background:white;border-radius:10px;padding:10px;box-shadow:0 2px 8px #0002}}h3{{font-size:14px;margin:0}}article p{{font-size:12px;color:#60707c;margin:5px 0}}.chart{{display:block;position:relative}}img{{display:block;width:100%}}.core{{position:absolute;top:0;bottom:0;background:#ff980033;border-left:3px solid #f57c00;border-right:3px solid #f57c00;box-sizing:border-box;pointer-events:none}}@media(max-width:900px){{section{{grid-template-columns:1fr}}}}</style></head>
<body><header><h1>Owner-short hard negative · 200张逐图审计</h1><p>不是3张大拼图：下面是200张独立可点开的真实训练图。</p><p>前100张：Owner明确判long的相似平台，橙色竖带只在审计层标示原long核心；后100张：baseline最高分的安全背景。训练图片本身没有橙带、文字或预测框。</p><p>全部来自train截止前；不读未来收益、不读holdout、不读val作选择。</p></header><main>
<section><h2>Owner-long方向反类（100张）</h2>{_audit_cards(owner_chosen, section='Owner-long')}</section>
<section><h2>模型最高分安全背景（100张）</h2>{_audit_cards(model_chosen, section='Model-ranked')}</section>
</main></body></html>""",
        encoding="utf-8",
    )
    try:
        dataset_name = str(dataset.relative_to(ROOT))
    except ValueError:
        dataset_name = str(dataset)
    try:
        output_name = str(output.relative_to(ROOT))
    except ValueError:
        output_name = str(output)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "dataset": dataset_name,
        "owner_long_available": len(owner_long),
        "owner_long_shown": len(owner_chosen),
        "model_ranked_available": len(model_ranked),
        "model_ranked_shown": len(model_chosen),
        "holdout_read": False,
        "future_outcome_used": False,
        "output": output_name,
    }
    (output.parent / f"{output.stem}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--mode", choices=("prepare", "mine", "assemble", "audit"), required=True)
    root.add_argument("--base", type=Path, default=BASE)
    root.add_argument("--candidate-root", type=Path, default=CANDIDATE_ROOT)
    root.add_argument("--out", type=Path, default=OUT)
    root.add_argument("--weights", type=Path, default=WEIGHTS)
    root.add_argument("--sheet", type=Path, default=OWNER_SHEET)
    root.add_argument("--predictions", type=Path, default=None)
    root.add_argument("--device", default="mps")
    root.add_argument("--batch", type=int, default=32)
    root.add_argument("--audit-html", type=Path, default=AUDIT_HTML)
    return root


def main() -> int:
    args = parser().parse_args()
    predictions = args.predictions or args.candidate_root / "mining_predictions.jsonl"
    if args.mode == "prepare":
        result = prepare(args.base, args.candidate_root, args.sheet)
    elif args.mode == "mine":
        result = mine(
            args.candidate_root,
            args.weights,
            predictions,
            device=args.device,
            batch=args.batch,
        )
    elif args.mode == "assemble":
        result = assemble(args.base, args.candidate_root, args.out, predictions)
    else:
        result = build_audit(args.out, args.audit_html)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
