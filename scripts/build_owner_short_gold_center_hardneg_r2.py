#!/usr/bin/env python3
"""Build the fixed-size Owner-confirmed hard-negative replacement arm.

This builder changes one variable relative to
``owner_short_gold_center_hardneg_r1``: the composition of the 2,286 train
hard negatives.  Owner-confirmed false fires are selected first inside each
frozen W12--W19 bucket, then the strongest existing R1 negatives fill the
remaining capacity.  Train positives, easy negatives, validation, total hard
count, and the W histogram stay unchanged.

Only ``causal_input_path`` is copied into training.  The separate 48-bar
future chart used by the Owner during label review is never copied into the
dataset.  Future context is allowed for the label, not for model input.
Holdout is not read and this script never starts training or promotes weights.
"""

from __future__ import annotations

import argparse
import html
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2

from scripts.build_owner_short_gold_center_hardneg import (
    ROOT,
    _rewrite_base_path,
    read_jsonl,
    sha256_file,
    verify_base_copy,
    write_jsonl,
)


PROTOCOL = "owner_short_gold_center_hardneg_r2_ownerconfirmed_20260811"
BASE = ROOT / "datasets/owner_short_gold_center_v1"
R1 = ROOT / "datasets/owner_short_gold_center_hardneg_r1"
OUT = ROOT / "datasets/owner_short_gold_center_hardneg_r2_ownerconfirmed"
AUDIT_HTML = (
    ROOT
    / "analysis/html/p2_owner_short_gold_center_hardneg_r2_audit200_20260811.html"
)
STAGE_A_BEST = (
    ROOT
    / "analysis/output/lsv2_stagea/owner_lsv2_stagea_randomcrop_v1_cold/weights/best.pt"
)
TRAINER = ROOT / "src/detection/train.py"
TRAIN_WRAPPER = ROOT / "scripts/train_w20_midbox_on_3060.sh"
EXPECTED_COUNTS = {
    "train_positive": 1143,
    "val_positive": 202,
    "train_easy_negative": 1143,
    "val_easy_negative": 200,
    "train_hard_negative": 2286,
}

REVIEW_SOURCES = (
    {
        "path": ROOT
        / "analysis/output/owner_short_train_hardneg_review200_v1/owner_review_labeled_manifest.jsonl",
        "causal_field": "selection_future_used",
    },
    {
        "path": ROOT
        / "analysis/output/owner_short_train_positive_retrieval100_v1/owner_review_labeled_manifest.jsonl",
        "causal_field": "selection_future_used",
    },
    {
        "path": ROOT
        / "analysis/output/owner_short_train_hardneg_expansion200_v2/owner_review_labeled_manifest.jsonl",
        "causal_field": "selection_future_used",
    },
    {
        "path": ROOT
        / "analysis/output/owner_short_train_hardneg_newblocks200_v3/owner_review_labeled_manifest.jsonl",
        "causal_field": "hard_negative_newblocks_future_used",
    },
)


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _canonical_time(value: object) -> str:
    """Normalize equivalent UTC spellings for semantic interval deduplication."""
    from pandas import Timestamp  # local import keeps unit selection tests light

    return Timestamp(value).tz_convert("UTC").isoformat()


def semantic_interval(row: dict[str, Any]) -> tuple[str, str, str, int]:
    """Return symbol/start/end/W for a raw chart interval, independent of pixels."""
    if row.get("selected_hard_kind") == "owner_confirmed_false_fire" or "event_id" in row:
        start = row["window_start_time"]
        end = row["decision_time"]
        window = int(row["window_len"])
    else:
        start = row.get("start_time")
        end = row.get("end_time")
        window = int(row["win_len"])
    if start is None or end is None:
        # Owner-long rows predate a start_time field. Their raw index interval
        # is still unique, and they cannot collide with active-learning blocks.
        start = f"index:{int(row['win_start'])}"
        end = f"index:{int(row['win_end'])}"
    else:
        start = _canonical_time(start)
        end = _canonical_time(end)
    return str(row["symbol"]), str(start), str(end), window


def validate_confirmed_row(
    row: dict[str, Any], *, causal_field: str, source_manifest: Path
) -> dict[str, Any]:
    """Validate one Owner-confirmed negative and normalize it for selection."""
    required_true = ("owner_confirmed",)
    required_false = (
        "holdout_read",
        "future_data_in_causal_input",
        "touches_owner_box_guard",
    )
    if row.get("owner_decision") != "hard_negative":
        raise ValueError("validate_confirmed_row received a non-negative decision")
    if any(row.get(field) is not True for field in required_true):
        raise ValueError(f"unconfirmed Owner decision: {row.get('event_id')}")
    if any(row.get(field) is not False for field in required_false):
        raise ValueError(f"unsafe causal input flags: {row.get('event_id')}")
    if causal_field not in row or row[causal_field] is not False:
        raise ValueError(
            f"missing/unsafe builder causal proof {causal_field}: {row.get('event_id')}"
        )
    if int(row["window_len"]) not in range(12, 20):
        raise ValueError(f"unexpected window length: {row.get('event_id')}")
    image_path = ROOT / str(row["causal_input_path"])
    if not image_path.is_file():
        raise FileNotFoundError(image_path)
    if sha256_file(image_path) != str(row["causal_input_sha256"]):
        raise ValueError(f"causal image hash mismatch: {row.get('event_id')}")
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (742, 1280):
        raise ValueError(
            f"causal image geometry mismatch: {row.get('event_id')} "
            f"{None if image is None else image.shape}"
        )
    normalized = {
        **row,
        "selected_hard_kind": "owner_confirmed_false_fire",
        "source_review_manifest": _relative_or_absolute(source_manifest),
        "source_image_path": str(row["causal_input_path"]),
        "win_len": int(row["window_len"]),
        "win_start": int(row["window_start_i"]),
        "win_end": int(row["decision_i"]),
        "start_time": str(row["window_start_time"]),
        "end_time": str(row["decision_time"]),
        "model_score_used": True,
        "score_threshold_selected": False,
        "future_data_in_training_image": False,
        "owner_label_future_review_available": bool(row.get("future_review_only")),
    }
    return normalized


def load_confirmed_hard_negatives(
    sources: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load all reviewed hard negatives and enforce one-to-one causal inputs."""
    confirmed: list[dict[str, Any]] = []
    source_counts: dict[str, int] = {}
    for spec in sources:
        path = Path(spec["path"])
        rows = read_jsonl(path)
        chosen = [row for row in rows if row.get("owner_decision") == "hard_negative"]
        source_counts[_relative_or_absolute(path)] = len(chosen)
        for row in chosen:
            confirmed.append(
                validate_confirmed_row(
                    row,
                    causal_field=str(spec["causal_field"]),
                    source_manifest=path,
                )
            )
    event_ids = [str(row["event_id"]) for row in confirmed]
    content_hashes = [str(row["causal_input_sha256"]) for row in confirmed]
    intervals = [semantic_interval(row) for row in confirmed]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("duplicate confirmed event_id")
    if len(content_hashes) != len(set(content_hashes)):
        raise ValueError("duplicate confirmed causal image")
    if len(intervals) != len(set(intervals)):
        raise ValueError("duplicate confirmed semantic interval")
    audit = {
        "available": len(confirmed),
        "by_source": source_counts,
        "by_w": dict(sorted(Counter(row["win_len"] for row in confirmed).items())),
        "unique_events": len(set(event_ids)),
        "unique_images": len(set(content_hashes)),
        "unique_intervals": len(set(intervals)),
        "holdout_read": False,
        "future_data_in_training_images": False,
        "owner_label_future_review_available": True,
    }
    return confirmed, audit


def select_replacement_hard_negatives(
    r1_rows: list[dict[str, Any]],
    confirmed_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Keep the exact R1 W histogram while replacing it with confirmed fires."""
    target = Counter(int(row["win_len"]) for row in r1_rows)
    confirmed_by_w: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in confirmed_rows:
        confirmed_by_w[int(row["win_len"])].append(row)

    selected_confirmed: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for window in sorted(target):
        ranked = sorted(
            confirmed_by_w[window],
            key=lambda row: (-float(row["event_conf_max"]), str(row["event_id"])),
        )
        capacity = target[window]
        for rank, row in enumerate(ranked[:capacity], 1):
            selected_confirmed.append({**row, "replacement_rank_in_w": rank})
        deferred.extend(ranked[capacity:])

    selected_intervals = {semantic_interval(row) for row in selected_confirmed}
    old_by_w: dict[int, list[dict[str, Any]]] = defaultdict(list)
    semantic_old_collisions = 0
    for row in r1_rows:
        if semantic_interval(row) in selected_intervals:
            semantic_old_collisions += 1
            continue
        old_by_w[int(row["win_len"])].append(row)

    selected_old: list[dict[str, Any]] = []
    for window in sorted(target):
        confirmed_count = sum(row["win_len"] == window for row in selected_confirmed)
        need = target[window] - confirmed_count
        ranked_old = sorted(
            old_by_w[window],
            key=lambda row: (
                0 if row["selected_hard_kind"] == "owner_long" else 1,
                -float(row.get("max_confidence", 0.0)),
                str(row["sample_id"]),
            ),
        )
        if len(ranked_old) < need:
            raise ValueError(f"W{window}: need {need} retained R1 rows, have {len(ranked_old)}")
        selected_old.extend(ranked_old[:need])

    selected = [*selected_confirmed, *selected_old]
    actual = Counter(int(row["win_len"]) for row in selected)
    if actual != target:
        raise ValueError(f"replacement W drift: actual={actual} target={target}")
    if len(selected) != len(r1_rows):
        raise ValueError("fixed total hard-negative count drift")
    if len({semantic_interval(row) for row in selected}) != len(selected):
        raise ValueError("duplicate semantic interval after replacement")

    profile = {
        "target_by_w": dict(sorted(target.items())),
        "selected_by_w": dict(sorted(actual.items())),
        "owner_confirmed_available": len(confirmed_rows),
        "owner_confirmed_selected": len(selected_confirmed),
        "owner_confirmed_deferred_for_bucket_overflow": len(deferred),
        "confirmed_selected_by_w": dict(
            sorted(Counter(row["win_len"] for row in selected_confirmed).items())
        ),
        "confirmed_deferred_by_w": dict(
            sorted(Counter(row["win_len"] for row in deferred).items())
        ),
        "retained_r1_by_kind": dict(
            sorted(Counter(row["selected_hard_kind"] for row in selected_old).items())
        ),
        "semantic_old_collisions_excluded": semantic_old_collisions,
        "score_threshold_selected": False,
    }
    return selected, deferred, profile


def _copy_hard_row(row: dict[str, Any], target_image: Path) -> None:
    if row["selected_hard_kind"] == "owner_confirmed_false_fire":
        source = ROOT / str(row["source_image_path"])
    else:
        source = ROOT / str(row["image_path"])
    shutil.copy2(source, target_image)


def _training_preregistration(out: Path) -> dict[str, Any]:
    if not STAGE_A_BEST.is_file():
        raise FileNotFoundError(STAGE_A_BEST)
    recipe = {
        "protocol": PROTOCOL,
        "dataset": _relative_or_absolute(out),
        "initialization": _relative_or_absolute(STAGE_A_BEST),
        "initialization_sha256": sha256_file(STAGE_A_BEST),
        "trainer": _relative_or_absolute(TRAINER),
        "trainer_sha256": sha256_file(TRAINER),
        "wrapper": _relative_or_absolute(TRAIN_WRAPPER),
        "wrapper_sha256": sha256_file(TRAIN_WRAPPER),
        "run_name": "owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft",
        "epochs": 40,
        "patience": 10,
        "batch": 8,
        "imgsz": 960,
        "seed": 0,
        "finetune": True,
        "optimizer": "AdamW",
        "lr0": 0.0001,
        "warmup_epochs": 0.5,
        "rect": True,
        "augmentation": {
            "fliplr": 0.0,
            "flipud": 0.0,
            "mosaic": 0.0,
            "mixup": 0.0,
            "copy_paste": 0.0,
            "hsv_h": 0.0,
            "hsv_s": 0.0,
            "hsv_v": 0.0,
        },
        "command": (
            "FABLE_3060_HOST=zzc@<current-ip> bash scripts/train_w20_midbox_on_3060.sh "
            "--dataset datasets/owner_short_gold_center_hardneg_r2_ownerconfirmed "
            "--base analysis/output/lsv2_stagea/owner_lsv2_stagea_randomcrop_v1_cold/weights/best.pt "
            "--name owner_lsv2_short_gold_center_hardneg_r2_ownerconfirmed_ft "
            "--epochs 40 --patience 10 --batch 8 --seed 0 --finetune"
        ),
        "single_variable": "hard_negative_composition_only",
        "training_started": False,
        "separate_owner_authorization_required": True,
        "auto_promote": False,
        "holdout_read": False,
    }
    (out / "training_preregistration.json").write_text(
        json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return recipe


def assemble(base: Path, r1: Path, out: Path) -> dict[str, Any]:
    if out.exists():
        raise FileExistsError(f"refusing to overwrite {out}")
    positives = read_jsonl(base / "positive_manifest.jsonl")
    easy_negatives = read_jsonl(base / "negative_manifest.jsonl")
    r1_hard = read_jsonl(r1 / "hard_negative_manifest.jsonl")
    confirmed, confirmed_audit = load_confirmed_hard_negatives(REVIEW_SOURCES)

    base_hashes = {str(row["image_sha256"]) for row in [*positives, *easy_negatives]}
    confirmed_hashes = {str(row["causal_input_sha256"]) for row in confirmed}
    r1_hashes = {str(row["image_sha256"]) for row in r1_hard}
    if base_hashes & confirmed_hashes:
        raise ValueError("confirmed causal input duplicates base dataset image")
    # A pixel-identical old hard would silently lower effective replacement.
    if r1_hashes & confirmed_hashes:
        raise ValueError("confirmed causal input duplicates R1 hard image")

    selected, deferred, selection_profile = select_replacement_hard_negatives(
        r1_hard, confirmed
    )
    shutil.copytree(base, out)
    copy_profile = verify_base_copy(base, out)

    hard_manifest: list[dict[str, Any]] = []
    for number, row in enumerate(selected, 1):
        kind = str(row["selected_hard_kind"])
        suffix = (
            f"hnowner_{row['event_id']}"
            if kind == "owner_confirmed_false_fire"
            else f"r1_{row.get('source_sample_id', row['sample_id'])}"
        )
        stem = f"hard_{number:05d}_{suffix}"
        image_target = out / "images/train" / f"{stem}.png"
        label_target = out / "labels/train" / f"{stem}.txt"
        _copy_hard_row(row, image_target)
        label_target.write_text("", encoding="utf-8")
        hard_manifest.append(
            {
                **row,
                "sample_id": stem,
                "source_sample_id": str(row.get("sample_id", row.get("event_id"))),
                "split": "train",
                "class": "hard_negative",
                "image_path": str(image_target.relative_to(ROOT)),
                "label_path": str(label_target.relative_to(ROOT)),
                "image_sha256": sha256_file(image_target),
                "label_sha256": sha256_file(label_target),
                "holdout_read": False,
                "future_data_in_training_image": False,
            }
        )

    rewritten_positives: list[dict[str, Any]] = []
    for row in positives:
        item = dict(row)
        item["image_path"] = _rewrite_base_path(str(row["image_path"]), base, out)
        item["label_path"] = _rewrite_base_path(str(row["label_path"]), base, out)
        rewritten_positives.append(item)
    rewritten_negatives: list[dict[str, Any]] = []
    for row in easy_negatives:
        item = dict(row)
        item["image_path"] = _rewrite_base_path(str(row["image_path"]), base, out)
        item["label_path"] = _rewrite_base_path(str(row["label_path"]), base, out)
        rewritten_negatives.append(item)
    rewritten_negatives.extend(hard_manifest)

    all_rows = [*rewritten_positives, *rewritten_negatives]
    joint_hashes = [(row["image_sha256"], row["label_sha256"]) for row in all_rows]
    duplicate_joint = len(joint_hashes) - len(set(joint_hashes))
    if duplicate_joint:
        raise ValueError(f"duplicate image+label rows: {duplicate_joint}")
    if len({semantic_interval(row) for row in hard_manifest}) != len(hard_manifest):
        raise ValueError("semantic hard-negative interval duplicate after copy")

    write_jsonl(out / "positive_manifest.jsonl", rewritten_positives)
    write_jsonl(out / "negative_manifest.jsonl", rewritten_negatives)
    write_jsonl(out / "hard_negative_manifest.jsonl", hard_manifest)
    write_jsonl(out / "confirmed_deferred_manifest.jsonl", deferred)
    (out / "data.yaml").write_text(
        f"path: {out.resolve()}\ntrain: images/train\nval: images/val\n"
        "names:\n  0: owner_short_platform\n",
        encoding="utf-8",
    )

    counts = {
        "train_positive": sum(row["split"] == "train" for row in positives),
        "val_positive": sum(row["split"] == "val" for row in positives),
        "train_easy_negative": sum(row["split"] == "train" for row in easy_negatives),
        "val_easy_negative": sum(row["split"] == "val" for row in easy_negatives),
        "train_hard_negative": len(hard_manifest),
    }
    if counts != EXPECTED_COUNTS:
        raise ValueError(f"frozen count drift: {counts}")
    train_images = len(list((out / "images/train").glob("*.png")))
    val_images = len(list((out / "images/val").glob("*.png")))
    if (train_images, val_images) != (4572, 402):
        raise ValueError(f"filesystem count drift: train={train_images} val={val_images}")
    preregistration = _training_preregistration(out)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "protocol": PROTOCOL,
        "base_dataset": _relative_or_absolute(base),
        "comparison_dataset": _relative_or_absolute(r1),
        "dataset": _relative_or_absolute(out),
        "counts": counts,
        "filesystem_counts": {"train_images": train_images, "val_images": val_images},
        "train_negative_to_positive": 3.0,
        "hard_share_of_train_negatives": 2 / 3,
        "confirmed_source_audit": confirmed_audit,
        "selection_profile": selection_profile,
        "duplicate_joint_sha256": duplicate_joint,
        "duplicate_semantic_hard_intervals": 0,
        **copy_profile,
        "base_positive_easy_val_mutated": False,
        "validation_mutated": False,
        "window_histogram_mutated": False,
        "future_data_in_training_images": False,
        "owner_label_future_review_available": True,
        "holdout_read": False,
        "score_threshold_selected": False,
        "training_started": False,
        "auto_promote": False,
        "training_preregistration_sha256": sha256_file(
            out / "training_preregistration.json"
        ),
        "initialization_sha256": preregistration["initialization_sha256"],
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _audit_cards(rows: list[dict[str, Any]], title: str) -> str:
    cards: list[str] = []
    for number, row in enumerate(rows, 1):
        source = Path("../../") / str(row["image_path"])
        score = row.get("event_conf_max", row.get("max_confidence"))
        score_text = "Owner long方向反类" if score is None else f"score {float(score):.3f}"
        cards.append(
            f'<article><h3>{html.escape(title)} #{number:03d} · '
            f'{html.escape(str(row["symbol"]))} · W{int(row["win_len"])}</h3>'
            f'<p>{html.escape(score_text)} · {html.escape(str(row.get("end_time", "")))}</p>'
            f'<a href="{source.as_posix()}" target="_blank"><img loading="lazy" '
            f'src="{source.as_posix()}" alt="training hard negative"></a></article>'
        )
    return "".join(cards)


def build_audit(dataset: Path, output: Path) -> dict[str, Any]:
    """Render 200 actual training images with no review overlays."""
    rows = read_jsonl(dataset / "hard_negative_manifest.jsonl")
    confirmed = [r for r in rows if r["selected_hard_kind"] == "owner_confirmed_false_fire"]
    owner_long = [r for r in rows if r["selected_hard_kind"] == "owner_long"]
    model_background = [
        r for r in rows if r["selected_hard_kind"] == "model_ranked_background"
    ]
    confirmed.sort(key=lambda r: (-float(r["event_conf_max"]), str(r["event_id"])))
    owner_long.sort(key=lambda r: str(r["sample_id"]))
    model_background.sort(
        key=lambda r: (-float(r.get("max_confidence", 0.0)), str(r["sample_id"]))
    )
    shown = {
        "owner_confirmed_false_fire": confirmed[:100],
        "retained_owner_long": owner_long[:50],
        "retained_model_background": model_background[:50],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    sections = "".join(
        f"<section><h2>{html.escape(title)}（{len(items)}张）</h2>"
        f"{_audit_cards(items, title)}</section>"
        for title, items in shown.items()
    )
    output.write_text(
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>第三臂实际训练图200张审计</title><style>"
        "body{margin:0;background:#edf2f5;color:#172631;font-family:-apple-system,"
        "BlinkMacSystemFont,'PingFang SC',sans-serif}header{background:#14212c;color:#fff;"
        "padding:24px 30px}header p{color:#d5e0e8;margin:6px 0}main{padding:18px;"
        "max-width:1600px;margin:auto}section{display:grid;grid-template-columns:"
        "repeat(3,minmax(0,1fr));gap:12px}h2{grid-column:1/-1}article{background:#fff;"
        "padding:10px;border-radius:10px;box-shadow:0 2px 8px #0002}h3{font-size:14px;"
        "margin:0}p{font-size:12px;color:#60707c;margin:5px 0}img{width:100%;display:block}"
        "@media(max-width:900px){section{grid-template-columns:1fr}}</style></head><body>"
        "<header><h1>第三臂 · 实际训练输入200张审计</h1>"
        "<p>这里显示的是最终会送进YOLO的原图：无橙框、无预测框、无未来48根。</p>"
        "<p>100张Owner确认误报 + 50张保留Owner-long + 50张保留旧模型背景。</p>"
        "<p>本页只核对输入内容，不重新要求Owner裁决。</p></header><main>"
        f"{sections}</main></body></html>",
        encoding="utf-8",
    )
    result = {
        "protocol": PROTOCOL,
        "dataset": _relative_or_absolute(dataset),
        "output": _relative_or_absolute(output),
        "shown": {key: len(value) for key, value in shown.items()},
        "future_data_in_training_images": False,
        "holdout_read": False,
    }
    (output.parent / f"{output.stem}_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("assemble", "audit"), required=True)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--r1", type=Path, default=R1)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--audit-html", type=Path, default=AUDIT_HTML)
    args = parser.parse_args()
    result = (
        assemble(args.base, args.r1, args.out)
        if args.mode == "assemble"
        else build_audit(args.out, args.audit_html)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
