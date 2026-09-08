"""Review one actual HL2 training label per positive Grade-A event.

Reuse the frozen manual-pack grouping, prefix guard, temporal checks and writes.
Main/context PNGs and source labels are immutable copies; no central reboxing is
performed. Future-only SMA/EMA 20/60/120 use (high+low)/2 on the original prefix.
All proposals remain unconfirmed and no dataset gains training eligibility.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets import grade_a_manual_pack as manual
from yoyo.datasets.ma_launch_owner_grade_a_hl2 import with_hl2_mas
from yoyo.layers.l1_detection.render import ChartTransform, render_chart

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_hl2_v1"
PACK = ROOT / "datasets/grade_a_hl2_review_20260908_v1"
PREREG = ROOT / "experiments/active/exp-yolo-dataset-consolidation-20260908-v1/preregistration.json"
OLD_LINEAGE = ROOT / "datasets/grade_a_manual_events_20260907_v1/admin/lineage.jsonl"
MANIFEST_SHA256 = "ec93d6bfd04cc84a24a34cd745af2c74943f9a75e664831060619511ba60f6d7"
PROTOCOL = "grade_a_hl2_training_label_review_v1"
FAMILY = "grade_a_hl2"
FALSE_FLAGS = {"training_eligible": False, "production_eligible": False,
               "sample_owner_geometry_confirmed": False, "new_gold": False}
EXTRA_FIELDS = ("direction", "window_bars", "source_core_start_i", "source_core_end_i",
                "core_start_time", "core_end_time", "source_comparison_anchor_i",
                "baseline_image_sha256", "baseline_label_sha256")


def source_identity() -> tuple[str, dict]:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)
    if git("branch", "--show-current").decode().strip() != "main":
        raise ValueError("review build requires main")
    paths = [Path(__file__), ROOT / "tests/test_grade_a_hl2_review_pack.py", PREREG,
        Path(manual.__file__), ROOT / "yoyo/datasets/ma_launch_owner_grade_a_hl2.py",
        ROOT / "yoyo/datasets/fifteen_minute_launch_candidates.py",
        ROOT / "yoyo/datasets/ma_launch_owner_recrop_review.py",
        ROOT / "yoyo/datasets/ma_rope_filter.py", ROOT / "yoyo/contracts/holdout.py",
        ROOT / "yoyo/layers/l1_detection/data.py", ROOT / "yoyo/layers/l1_detection/render.py"]
    identities = {}
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        if path.read_bytes() != git("show", f"HEAD:{relative}"):
            raise ValueError(f"commit source before build: {relative}")
        identities[relative] = manual.sha(path)
    return git("rev-parse", "HEAD").decode().strip(), identities


def metadata(rows: list[dict], old_rows: list[dict], *, expected: bool = True) -> tuple[dict, dict, dict]:
    """Whole-manifest date gates precede every image, label and OHLCV read."""
    groups = manual.group_events(rows)
    if expected and (len(rows) != 32000 or len(groups) != 4172):
        raise ValueError("source population drift")
    old = {r["dataset_sample_id"]: r for r in old_rows}
    if len(old) != len(old_rows) or set(old) != {r["dataset_sample_id"] for r in rows}:
        raise ValueError("close-transform lineage coverage drift")
    positives, transforms = {}, {}
    for key, group in groups.items():
        for r in group:
            if (r["moving_average_price_source"] != "hl2"
                    or r["canvas_transform_source"] != "baseline_close_transform"
                    or r["annotation_drawn_into_image"] is not False):
                raise ValueError("HL2 clean-canvas metadata drift")
            prior = old[r["dataset_sample_id"]]
            for field in manual.VARIANT_FIELDS:
                expected_value = r.get("baseline_"+field, r.get(field)) if field in {"image_sha256", "label_sha256"} else r[field]
                if prior[field] != expected_value:
                    raise ValueError(f"close-transform lineage mismatch: {field}")
            tf = ChartTransform(**prior["chart_transform"])
            if (tf.width, tf.height, tf.n_bars) != (1280, 742, int(r["window_bars"])) or tf.price_max <= tf.price_min:
                raise ValueError("invalid frozen chart transform")
            transforms[r["dataset_sample_id"]] = asdict(tf)
        if key[0] == "positive":
            if len({r["direction"] for r in group}) != 1 or group[0]["direction"] not in {"LONG", "SHORT"}:
                raise ValueError("event direction mismatch")
            for r in group:
                if (r["boxes_per_image"] != 1 or int(r["source_core_start_i"]) != int(r["window_start_i"])+int(r["pre_bars"])
                        or int(r["source_core_end_i"])-int(r["source_core_start_i"])+1 != int(r["core_bars"])
                        or not int(r["window_start_i"]) <= int(r["source_core_start_i"]) <= int(r["source_core_end_i"]) <= int(r["window_end_i"])):
                    raise ValueError("source core metadata mismatch")
            positives[key] = group
    inventory = {"source_variants": len(rows), "source_event_groups": len(groups),
        "positive_events": len(positives), "positive_variants": sum(len(g) for g in positives.values()),
        "negative_events": sum(k[0] == "negative" for k in groups),
        "negative_variants": sum(len(g) for k, g in groups.items() if k[0] == "negative"),
        "negative_inventory_metadata_only": True}
    if expected and (inventory["positive_events"], inventory["positive_variants"], inventory["negative_events"], inventory["negative_variants"]) != (1043, 8000, 3129, 24000):
        raise ValueError("positive/negative population drift")
    return positives, transforms, inventory


def asset_path(row: dict, kind: str) -> Path:
    relative = Path(row[f"{kind}_path"])
    path = DATASET / relative
    folder, suffix = ("images", ".png") if kind == "image" else ("labels", ".txt")
    if (relative.is_absolute() or len(relative.parts) != 3 or relative.parts[:2] != (folder, row["split"])
            or relative.suffix != suffix or path.resolve() != DATASET.resolve()/relative):
        raise ValueError("source asset path escapes declared role")
    return path


def parse_label(content: bytes, row: dict) -> dict:
    """Map actual YOLO txt to LS percent; never generate a replacement core box."""
    lines = content.decode().splitlines()
    if len(lines) != 1 or len(lines[0].split()) != 5:
        raise ValueError("positive must have exactly one actual YOLO label")
    cls, *numbers = lines[0].split()
    if cls not in {"0", "1"} or int(cls) != (0 if row["direction"] == "LONG" else 1):
        raise ValueError("actual label class/direction mismatch")
    cx, cy, width, height = map(float, numbers)
    x, y = cx-width/2, cy-height/2
    if (not all(math.isfinite(v) for v in (cx, cy, width, height)) or width <= 0 or height <= 0
            or min(x, y) < 0 or x+width > 1 or y+height > 1):
        raise ValueError("actual label geometry invalid")
    for key, value in zip(("cx_norm", "cy_norm", "w_norm", "h_norm"), (cx, cy, width, height)):
        if abs(float(row["box"][key])-value) > 1e-9:
            raise ValueError("actual label disagrees with frozen manifest")
    return {"class_id": int(cls), "source_label_text": content.decode(), "yolo_cxcywh": [cx, cy, width, height],
            "xyxy_px": [x*1280, y*742, (x+width)*1280, (y+height)*742],
            "ls_value": {"x": x*100, "y": y*100, "width": width*100, "height": height*100,
                         "rotation": 0, "rectanglelabels": ["多头" if cls == "0" else "空头"]}}


def checked_assets(rows: list[dict]) -> dict:
    labels = {}
    for r in rows:
        for kind in ("image", "label"):
            if manual.sha(asset_path(r, kind)) != r[f"{kind}_sha256"]:
                raise ValueError(f"source {kind} SHA drift")
        labels[r["dataset_sample_id"]] = parse_label(asset_path(r, "label").read_bytes(), r)
    return labels


def review_id(group: list[dict]) -> str:
    return hashlib.sha256((PROTOCOL+"|"+group[0]["event_id"]).encode()).hexdigest()[:24]


def freeze_main(group: list[dict], transforms: dict, labels: dict) -> dict:
    rep, context = manual.representative(group), manual.context_variant(group)
    rid = review_id(group)
    roles = {"image": f"input_images/{rid}.png", "original_image": f"input_images/{rid}.png",
             "comparison_image": f"context_images/{rid}.png", "future_image": f"future_only/images/{rid}.png"}
    assets = {}
    for role, r in (("image", rep), ("comparison_image", context)):
        content = asset_path(r, "image").read_bytes()
        if hashlib.sha256(content).hexdigest() != r["image_sha256"]:
            raise ValueError("source image changed while copying")
        manual.frozen_write(PACK/roles[role], content)
        assets[roles[role]] = r["image_sha256"]
    lineage = []
    for r in group:
        entry = {k: r[k] for k in manual.VARIANT_FIELDS+EXTRA_FIELDS}
        entry.update({"review_id": rid, "chart_transform": transforms[r["dataset_sample_id"]],
            "actual_training_label": labels[r["dataset_sample_id"]],
            "is_representative": r["dataset_sample_id"] == rep["dataset_sample_id"],
            "is_context": r["dataset_sample_id"] == context["dataset_sample_id"]})
        lineage.append(entry)
    record = {"review_id": rid, "protocol_id": PROTOCOL, "source_event_id": rep["event_id"],
        "dataset_family": FAMILY, "source_dataset": DATASET.relative_to(ROOT).as_posix(),
        "source_path": rep["source_path"], "split": rep["split"], "direction": rep["direction"],
        "representative_sample_id": rep["dataset_sample_id"], "context_sample_id": context["dataset_sample_id"],
        "variant_sample_ids": [r["dataset_sample_id"] for r in group], "lineage": lineage,
        "asset_roles": roles, "assets": assets, "actual_training_label": labels[rep["dataset_sample_id"]],
        "main_start_time": rep["window_start_time"], "main_end_time": rep["window_end_time"],
        "chart_transform": transforms[rep["dataset_sample_id"]], "moving_average_price_source": "hl2",
        "future_values_used_for_proposal": False, **FALSE_FLAGS}
    manual.frozen_write(PACK/f"admin/main/{rid}.json", manual.json_bytes(record))
    return record


def render_future(frame: pd.DataFrame, row: dict, transform: dict, original: bytes) -> tuple[bytes, dict]:
    """HL2 feature initialization is over the full safe prefix, never a cropped frame."""
    hl2 = with_hl2_mas(frame)
    raw, _ = render_chart(manual.checked_window(hl2, row), fixed_transform=ChartTransform(**transform))
    copied = cv2.imdecode(np.frombuffer(original, dtype=np.uint8), cv2.IMREAD_COLOR)
    if copied is None or copied.shape != raw.shape or not np.array_equal(copied, raw):
        raise ValueError("HL2 main replay differs from actual training PNG")
    window, info = manual.bounded_future_window(hl2, row)
    future, tf = render_chart(window)
    separator = None
    if info["actual_future_bars"]:
        n = info["input_bars"]
        separator = round((tf.x_at(n-1)+tf.x_at(n))/2)
        cv2.line(future, (separator, tf.top), (separator, tf.top+tf.plot_h), (160, 160, 160), 1, cv2.LINE_AA)
    ok, encoded = cv2.imencode(".png", future, [cv2.IMWRITE_PNG_COMPRESSION, 4])
    if not ok:
        raise ValueError("future PNG encoding failed")
    return encoded.tobytes(), {**info, "chart_transform": asdict(tf), "input_end_separator_x_px": separator,
        "moving_average_price_source": "hl2", "moving_average_formula": "(high + low) / 2",
        "main_replay_pixel_sha256": hashlib.sha256(raw.tobytes()).hexdigest(),
        "review_only": True, "used_for_proposal": False, "training_eligible": False}


def task_for(record: dict) -> dict:
    future, rid = record["future"], record["review_id"]
    side = "多头" if record["direction"] == "LONG" else "空头"
    data = {"review_id": rid, "protocol_id": PROTOCOL, "source_event_id": record["source_event_id"],
        "dataset_family": FAMILY, "caption": f"HL2 自动原训练框（非金标） · {side} · 原图 {future['input_bars']} 根 · 后续 {future['actual_future_bars']}/40 根。可调整框后提交。"}
    if future["missing_future_reason"]:
        data["caption"] += manual.MISSING_REASONS[future["missing_future_reason"]]+"。"
    for role, relative in record["asset_roles"].items():
        data[role] = f"/data/local-files/?d=label_studio/{PACK.name}/{relative}"
        data[role+"_sha256"] = record["assets"][relative]
    result = {"id": rid[:10], "from_name": "pattern", "to_name": "image", "type": "rectanglelabels",
              "original_width": 1280, "original_height": 742, "image_rotation": 0,
              "value": record["actual_training_label"]["ls_value"]}
    return {"data": data, "predictions": [{"model_version": PROTOCOL, "result": [result]}]}


def build() -> dict:
    head, code = source_identity()
    prereg = json.loads(PREREG.read_text())
    if prereg["protocol_id"] != PROTOCOL or any(prereg[k] is not False for k in
            ("new_training", "new_model_inference", "new_gold", "training_eligible", "production_eligible", "holdout_read")):
        raise ValueError("preregistered protocol/safety drift")
    for relative, digest in prereg["source_sha256"].items():
        if manual.sha(ROOT/relative) != digest:
            raise ValueError(f"frozen metadata input changed: {relative}")
    if manual.sha(DATASET/"manifest.jsonl") != MANIFEST_SHA256:
        raise ValueError("HL2 manifest changed")
    rows = [json.loads(s) for s in (DATASET/"manifest.jsonl").read_text().splitlines()]
    old = [json.loads(s) for s in OLD_LINEAGE.read_text().splitlines()]
    groups, transforms, inventory = metadata(rows, old)
    if any(inventory[k] != v for k, v in prereg["expected_counts"].items()):
        raise ValueError("preregistered population differs")
    positive_rows = [r for g in groups.values() for r in g]
    labels = checked_assets(positive_rows)
    identity = {"protocol_id": PROTOCOL, "source_sha256": prereg["source_sha256"], "code_sha256": code,
                "runtime": {"cv2": cv2.__version__, "numpy": np.__version__, "pandas": pd.__version__}}
    manual.frozen_write(PACK/"admin/build_identity.json", manual.json_bytes(identity))
    started_path = PACK/"admin/started.json"
    if not started_path.exists():
        manual.frozen_write(started_path, manual.json_bytes({"source_commit": head, "created_at": datetime.now(timezone.utc).isoformat()}))
    # Every source label and copied main/context is frozen before reading future OHLCV.
    main = {review_id(g): freeze_main(g, transforms, labels) for g in groups.values()}
    by_source = defaultdict(list)
    for g in groups.values():
        by_source[g[0]["source_path"]].append(g)
    completed = []
    for source, source_groups in sorted(by_source.items()):
        pending = [g for g in source_groups if not (PACK/f"admin/events/{review_id(g)}.json").exists()]
        if pending:
            path = (ROOT/source).resolve()
            if not path.is_relative_to(ROOT):
                raise ValueError("OHLCV path escapes repository")
            bound = manual.source_read_end([manual.representative(g) for g in source_groups])
            frame, audit = manual.read_preholdout_prefix(path, end_exclusive=bound)
            if audit["holdout_ohlcv_rows_materialized"] != 0:
                raise ValueError("unsafe source prefix")
            audit["end_exclusive"] = bound.isoformat()
            manual.frozen_write(PACK/f"admin/sources/{hashlib.sha256(source.encode()).hexdigest()[:24]}.json", manual.json_bytes(audit))
        for group in source_groups:
            rid, rep = review_id(group), manual.representative(group)
            progress = PACK/f"admin/events/{rid}.json"
            if not progress.exists():
                record = dict(main[rid])
                content, future = render_future(frame, rep, record["chart_transform"], (PACK/record["asset_roles"]["image"]).read_bytes())
                relative = record["asset_roles"]["future_image"]
                manual.frozen_write(PACK/relative, content)
                record["assets"] = {**record["assets"], relative: hashlib.sha256(content).hexdigest()}
                record["future"] = future
                manual.frozen_write(progress, manual.json_bytes(record))
            record = json.loads(progress.read_text())
            if any(record[k] != main[rid][k] for k in main[rid] if k != "assets"):
                raise ValueError("resume event metadata differs")
            for relative, digest in record["assets"].items():
                if manual.sha(PACK/relative) != digest:
                    raise ValueError("resume/copy asset SHA mismatch")
            completed.append(record)
        print(f"HL2 review: {len(completed)}/1043 events", flush=True)
    completed.sort(key=lambda r: r["review_id"])
    checked_assets(positive_rows)
    if source_identity()[1] != code or any(manual.sha(ROOT/p) != h for p, h in prereg["source_sha256"].items()):
        raise ValueError("code or metadata changed during build")
    for name, records in (("manifest.jsonl", completed), ("admin/lineage.jsonl", [v for r in completed for v in r["lineage"]])):
        manual.frozen_write(PACK/name, b"".join((json.dumps(r, sort_keys=True, ensure_ascii=False)+"\n").encode() for r in records))
    manual.frozen_write(PACK/"tasks.json", manual.json_bytes([task_for(r) for r in completed]))
    manual.frozen_write(PACK/"future_only/manifest.json", manual.json_bytes({"protocol_id": PROTOCOL,
        "training_eligible": False, "items": [{"review_id": r["review_id"], "image_sha256": r["assets"][r["asset_roles"]["future_image"]], **r["future"]} for r in completed]}))
    if any(p.suffix == ".txt" or "labels" in p.parts for p in (PACK/"future_only").rglob("*")):
        raise ValueError("future directory contains labels")
    receipt = {**json.loads(started_path.read_text()), **identity, **inventory, **FALSE_FLAGS,
        "tasks": len(completed), "main_replay_passed": len(completed), "verified_positive_image_label_pairs": len(positive_rows),
        "event_split_counts": dict(Counter(r["split"] for r in completed)),
        "event_direction_counts": dict(Counter(r["direction"] for r in completed)),
        "main_start_min": min(r["main_start_time"] for r in completed), "main_end_max": max(r["main_end_time"] for r in completed),
        "actual_future_bars_counts": dict(Counter(str(r["future"]["actual_future_bars"]) for r in completed)),
        "missing_future_reasons": dict(Counter(r["future"]["missing_future_reason"] or "complete" for r in completed)),
        "tasks_sha256": manual.sha(PACK/"tasks.json"), "manifest_sha256": manual.sha(PACK/"manifest.jsonl"),
        "lineage_sha256": manual.sha(PACK/"admin/lineage.jsonl"),
        "output_sha256": {p: manual.sha(PACK/p) for p in ["manifest.jsonl", "admin/lineage.jsonl", "tasks.json", "future_only/manifest.json"]},
        "source_prefix_audit_sha256": {str(p.relative_to(PACK)): manual.sha(p) for p in sorted((PACK/"admin/sources").glob("*.json"))},
        "holdout_read": False, "future_used_for_labels": False, "new_training": False, "new_model_inference": False,
        "status": "ready_for_review_of_actual_hl2_training_labels"}
    manual.frozen_write(PACK/"admin/build_receipt.json", manual.json_bytes(receipt))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build"])
    parser.parse_args()
    print(json.dumps(build(), ensure_ascii=False, indent=2))
