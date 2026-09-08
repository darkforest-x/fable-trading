"""Extend the active 1,043 human review references to 150 future 15m bars.

Only the separate review canvas changes. Source HL2 SMA/EMA 20/60/120 use high
and low on the complete approved prefix; the input and labels remain immutable.
Old 2,513 references are byte-identical symlinks, not rerendered or relabelled.
The whole metadata population passes date gates before any media/OHLCV access.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import cv2
import numpy as np
import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.datasets import grade_a_hl2_review_pack as hl2
from yoyo.datasets import grade_a_manual_pack as manual
from yoyo.datasets import owner_review_export as export
from yoyo.datasets.ma_launch_owner_grade_a_hl2 import with_hl2_mas
from yoyo.layers.l1_detection.render import ChartTransform, render_chart

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "datasets/owner_review_future150_20260908_v1"
PREREG = ROOT / "experiments/active/exp-yolo-dataset-consolidation-20260908-v1/review_future150_preregistration.json"
PROTOCOL = "owner_review_future150_v1"
FUTURE_BARS = 150
BANNER_HEIGHT = 32
FALSE_FLAGS = {"training_eligible": False, "production_eligible": False,
    "new_training": False, "new_model_inference": False, "new_gold": False,
    "holdout_read": False, "label_studio_mutation_in_builder": False}
REASONS = {"holdout_boundary": "HOLDOUT BOUNDARY", "source_gap": "SOURCE GAP",
           "source_end": "SOURCE END"}


def source_identity() -> tuple[str, dict]:
    """Freeze implementation bytes, allowing only unrelated main HEAD movement."""
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=ROOT)
    if git("branch", "--show-current").decode().strip() != "main":
        raise ValueError("future reference build requires main")
    paths = [Path(__file__), ROOT / "tests/test_review_future_context.py", PREREG,
        Path(hl2.__file__), Path(manual.__file__), Path(export.__file__),
        ROOT / "yoyo/datasets/ma_launch_owner_grade_a_hl2.py",
        ROOT / "yoyo/datasets/fifteen_minute_launch_candidates.py",
        ROOT / "yoyo/datasets/ma_launch_owner_recrop_review.py",
        ROOT / "yoyo/datasets/ma_rope_filter.py", ROOT / "yoyo/contracts/holdout.py",
        ROOT / "yoyo/layers/l1_detection/data.py", ROOT / "yoyo/layers/l1_detection/render.py"]
    hashes = {}
    for path in paths:
        name = path.relative_to(ROOT).as_posix()
        if path.read_bytes() != git("show", "HEAD:" + name):
            raise ValueError("commit source before building: " + name)
        hashes[name] = manual.sha(path)
    return git("rev-parse", "HEAD").decode().strip(), hashes


def source_read_end(rows: list[dict]) -> pd.Timestamp:
    return min(pd.Timestamp(HOLDOUT_START), max(
        manual.stamp(r["window_end_time"]) + (FUTURE_BARS + 1) * manual.BAR for r in rows))


def bounded_future_window(frame: pd.DataFrame, row: dict) -> tuple[pd.DataFrame, dict]:
    """Preserve the complete input, then stop at the first missing future bar."""
    original = manual.checked_window(frame, row)
    end, actual, reason = int(row["window_end_i"]), 0, None
    for offset in range(1, FUTURE_BARS + 1):
        expected = manual.stamp(row["window_end_time"]) + offset * manual.BAR
        if expected + manual.BAR > HOLDOUT_START:
            reason = "holdout_boundary"
            break
        if end + offset >= len(frame):
            reason = "source_end"
            break
        if manual.stamp(frame.iloc[end + offset]["open_time"]) != expected:
            reason = "source_gap"
            break
        actual += 1
    window = frame.iloc[int(row["window_start_i"]):end + actual + 1]
    return window, {"requested_future_bars": FUTURE_BARS, "actual_future_bars": actual,
        "missing_future_reason": reason, "input_bars": len(original), "review_bars": len(window),
        "input_start_bar_open": manual.stamp(row["window_start_time"]).isoformat(),
        "input_end_bar_open": manual.stamp(row["window_end_time"]).isoformat(),
        "input_available_at": (manual.stamp(row["window_end_time"]) + manual.BAR).isoformat(),
        "review_end_bar_open": manual.stamp(window.iloc[-1]["open_time"]).isoformat(),
        "review_available_at": (manual.stamp(window.iloc[-1]["open_time"]) + manual.BAR).isoformat()}


def render_future(frame_with_hl2: pd.DataFrame, row: dict) -> tuple[bytes, dict]:
    """Draw all input/future bars below a count banner; no candle is cropped."""
    window, info = bounded_future_window(frame_with_hl2, row)
    chart, tf = render_chart(window, width=1280, height=742 - BANNER_HEIGHT)
    separator = None
    if info["actual_future_bars"]:
        n = info["input_bars"]
        separator = round((tf.x_at(n - 1) + tf.x_at(n)) / 2)
        cv2.line(chart, (separator, tf.top), (separator, tf.top + tf.plot_h),
                 (110, 110, 110), 1, cv2.LINE_AA)
    canvas = np.full((742, 1280, 3), 255, np.uint8)
    canvas[BANNER_HEIGHT:] = chart
    banner = f"INPUT {info['input_bars']}  |  FUTURE {info['actual_future_bars']} / 150  |  15m"
    if info["missing_future_reason"]:
        banner += "  |  " + REASONS[info["missing_future_reason"]]
    cv2.putText(canvas, banner, (12, 23), cv2.FONT_HERSHEY_SIMPLEX, .62,
                (45, 45, 45), 1, cv2.LINE_AA)
    cv2.line(canvas, (0, 31), (1279, 31), (215, 215, 215), 1)
    ok, encoded = cv2.imencode(".png", canvas, [cv2.IMWRITE_PNG_COMPRESSION, 4])
    if not ok:
        raise ValueError("future PNG encoding failed")
    return encoded.tobytes(), {**info, "chart_transform": asdict(tf),
        "chart_y_offset_px": BANNER_HEIGHT, "canvas_width": 1280, "canvas_height": 742,
        "input_end_separator_x_px": separator, "banner_text": banner,
        "moving_average_price_source": "hl2", "moving_average_formula": "(high + low) / 2",
        "review_only": True, "used_for_proposal": False, "training_eligible": False}


def replay_main(frame_with_hl2: pd.DataFrame, row: dict, transform: dict, original: bytes) -> str:
    """Prove the current source prefix still reproduces the frozen input pixels."""
    replay, _ = render_chart(manual.checked_window(frame_with_hl2, row),
                             fixed_transform=ChartTransform(**transform))
    frozen = cv2.imdecode(np.frombuffer(original, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frozen is None or frozen.shape != replay.shape or not np.array_equal(frozen, replay):
        raise ValueError("current OHLCV/HL2 prefix does not replay the original main PNG")
    return hashlib.sha256(replay.tobytes()).hexdigest()


def inside(base: Path, relative: str) -> Path:
    """Reject traversal and symlinks in a declared source/output path."""
    rel = Path(relative)
    if rel.is_absolute() or not rel.parts or any(p in {"..", "."} for p in rel.parts):
        raise ValueError("path escapes declared root")
    path = base / rel
    if base.is_symlink() or path.resolve() != base.absolute() / rel:
        raise ValueError("path escapes root or contains symlink")
    return path


def image_relative(rid: str) -> str:
    if not isinstance(rid, str) or re.fullmatch(r"[0-9a-f]{24}", rid) is None:
        raise ValueError("invalid review identity for lookup path")
    return f"images/{rid}/image.png"


def write(relative: str, content: bytes) -> None:
    path = inside(PACK, relative)
    if path.with_name(path.name + ".pending").exists():
        raise ValueError("unknown pending output requires inspection")
    manual.frozen_write(path, content)


def link_legacy(rid: str, target: Path, digest: str) -> dict:
    """Link only an explicitly verified legacy future, preserving its bytes."""
    relative = image_relative(rid)
    path = PACK / relative
    inside(PACK, str(Path(relative).parent))
    if target.is_symlink() or manual.sha(target) != digest:
        raise ValueError("legacy future target changed")
    if path.is_symlink():
        if path.resolve(strict=True) != target.resolve(strict=True) or manual.sha(path) != digest:
            raise ValueError("legacy future lookup target changed")
    elif path.exists():
        raise ValueError("unknown existing legacy lookup is not the registered symlink")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(os.path.relpath(target, path.parent))
    return {"image_path": relative, "image_sha256": digest,
        "symlink_target": str(target.relative_to(ROOT))}


def checked_metadata(expected: dict, old: list[dict], new: list[dict], rows: list[dict],
                     *, exact: bool = True) -> dict:
    """Validate all review/training dates before reading even one image byte."""
    groups = manual.group_events(rows)
    positives = {hl2.review_id(g): g for key, g in groups.items() if key[0] == "positive"}
    if (len({r["review_id"] for r in old + new}) != len(old) + len(new)
            or {r["review_id"] for r in old + new} != set(expected)
            or {r["review_id"] for r in new} != set(positives)):
        raise ValueError("review population/positive lineage drift")
    if exact and (len(old), len(new), len(rows), sum(map(len, positives.values()))) != (2513, 1043, 32000, 8000):
        raise ValueError("review/training population drift")
    for record in old + new:
        rid = record["review_id"]
        image_relative(rid)
        start, end = (manual.stamp(record[k]) for k in ("main_start_time", "main_end_time"))
        future = record["future"]
        future_end = manual.stamp(future["review_end_bar_open"])
        available = manual.stamp(future["review_available_at"])
        if (start > end or end + manual.BAR > HOLDOUT_START or future_end < end
                or available != future_end + manual.BAR or available > HOLDOUT_START
                or future.get("requested_future_bars") != 40
                or not 0 <= future.get("actual_future_bars", -1) <= 40
                or future_end != end + future["actual_future_bars"] * manual.BAR):
            raise ValueError("review input/future metadata crosses boundary or drifts")
        pack = export.NEW_PACK if rid in positives else export.OLD_PACK
        data = expected[rid]["data"]
        if data["protocol_id"] != (export.NEW_PROTOCOL if rid in positives else export.OLD_PROTOCOL):
            raise ValueError("review protocol changed")
        for role, relative in record["asset_roles"].items():
            inside(pack, relative)
            if (record["assets"].get(relative) != data.get(role + "_sha256")
                    or data.get(role) != f"/data/local-files/?d=label_studio/{pack.name}/{relative}"):
                raise ValueError("review media identity differs")
        if rid not in positives:
            reference = record["original_reference_check"]
            if record["original_reference_allowed"]:
                if not reference["allowed"] or manual.stamp(reference["window_available_at"]) > HOLDOUT_START:
                    raise ValueError("legacy original reference crosses holdout")
            elif (reference["allowed"] or record["assets"][record["asset_roles"]["original_image"]]
                  != record["assets"][record["asset_roles"]["image"]]):
                raise ValueError("blocked legacy reference must be the safe main placeholder")
    for record in new:
        group = positives[record["review_id"]]
        rep = manual.representative(group)
        if (record["representative_sample_id"] != rep["dataset_sample_id"]
                or record["main_start_time"] != rep["window_start_time"]
                or record["main_end_time"] != rep["window_end_time"]
                or record["source_path"] != rep["source_path"]
                or record["assets"][record["asset_roles"]["image"]] != rep["image_sha256"]):
            raise ValueError("representative input lineage changed")
        source = Path(record["source_path"])
        if source.is_absolute() or not (ROOT / source).resolve().is_relative_to(ROOT):
            raise ValueError("OHLCV path escapes repository")
        for row in group:
            if row["moving_average_price_source"] != "hl2" or row["annotation_drawn_into_image"] is not False:
                raise ValueError("training input HL2 identity changed")
            for kind in ("image", "label"):
                hl2.asset_path(row, kind)
    return positives


def media_snapshot(old: list[dict], new: list[dict], groups: dict) -> dict:
    """Hash immutable training positives and registered review assets, no scoring."""
    hashes = {}
    for pack, records in ((export.OLD_PACK, old), (export.NEW_PACK, new)):
        for record in records:
            for relative, digest in record["assets"].items():
                path = inside(pack, relative)
                if manual.sha(path) != digest:
                    raise ValueError("original review asset SHA drift: " + str(path))
                hashes[str(path.relative_to(ROOT))] = digest
    for group in groups.values():
        for row in group:
            for kind in ("image", "label"):
                path = hl2.asset_path(row, kind)
                if manual.sha(path) != row[kind + "_sha256"]:
                    raise ValueError("original training asset SHA drift")
                hashes[str(path.relative_to(ROOT))] = row[kind + "_sha256"]
    return {"files": len(hashes), "sha256": hashes,
        "snapshot_sha256": hashlib.sha256(manual.json_bytes(hashes)).hexdigest()}


def audit_output(expected_files: set[str], legacy_ids: set[str]) -> None:
    """No unknown files, labels, directory links or escaped generated images."""
    if PACK.is_symlink() or PACK.resolve() != PACK.absolute():
        raise ValueError("future output root contains a symlink")
    allowed_links = {image_relative(rid) for rid in legacy_ids}
    for path in PACK.rglob("*"):
        relative = path.relative_to(PACK).as_posix()
        if "labels" in path.parts or path.suffix == ".txt":
            raise ValueError("future-only output contains labels")
        if path.is_symlink() and relative not in allowed_links:
            raise ValueError("unexpected output symlink")
        if not path.is_dir() and relative not in expected_files:
            raise ValueError("unknown output: " + relative)


def build() -> dict:
    head, code = source_identity()
    prereg = json.loads(PREREG.read_text())
    if (prereg["protocol_id"] != PROTOCOL or prereg["requested_future_bars"] != FUTURE_BARS
            or prereg["output_pack"] != PACK.relative_to(ROOT).as_posix()
            or any(prereg[k] is not False for k in FALSE_FLAGS)
            or (prereg["expected_new_events"], prereg["expected_legacy_events"],
                prereg["expected_positive_variants"]) != (1043, 2513, 8000)):
        raise ValueError("future reference preregistration drift")
    for name, digest in prereg["source_sha256"].items():
        if manual.sha(inside(ROOT, name)) != digest:
            raise ValueError("preregistered metadata changed: " + name)
    expected, _, source_info = export.read_sources()
    if source_info["new_pack_status"] != "completed":
        raise ValueError("completed HL2 review pack is required")
    def read_jsonl(path):
        return [json.loads(line) for line in path.read_text().splitlines() if line]
    old = read_jsonl(export.OLD_PACK / "manifest.jsonl")
    new = read_jsonl(export.NEW_PACK / "manifest.jsonl")
    rows = read_jsonl(hl2.DATASET / "manifest.jsonl")
    groups = checked_metadata(expected, old, new, rows)
    by_source = defaultdict(list)
    for record in new:
        by_source[record["source_path"]].append(record)
    source_files = {source: "admin/sources/" + hashlib.sha256(source.encode()).hexdigest()[:24] + ".json"
                    for source in by_source}
    expected_files = {"manifest.jsonl", "admin/build_identity.json", "admin/started.json",
        "admin/build_receipt.json", "admin/immutable_media_before.json", "admin/immutable_media_after.json"}
    expected_files.update(source_files.values())
    for record in old + new:
        expected_files.update({image_relative(record["review_id"]), f"admin/events/{record['review_id']}.json"})
    audit_output(expected_files, {r["review_id"] for r in old})
    before = media_snapshot(old, new, groups)
    identity = {"protocol_id": PROTOCOL, "requested_future_bars": FUTURE_BARS,
        "code_sha256": code, "source_sha256": prereg["source_sha256"],
        "read_source_metadata_sha256": source_info["files_sha256"],
        "runtime": {"cv2": cv2.__version__, "numpy": np.__version__, "pandas": pd.__version__}}
    write("admin/build_identity.json", manual.json_bytes(identity))
    if not (PACK / "admin/started.json").exists():
        write("admin/started.json", manual.json_bytes({"source_commit": head,
            "created_at": datetime.now(timezone.utc).isoformat()}))
    write("admin/immutable_media_before.json", manual.json_bytes(before))
    completed = []
    for record in sorted(old, key=lambda r: r["review_id"]):
        rid, relative = record["review_id"], record["asset_roles"]["future_image"]
        linked = link_legacy(rid, inside(export.OLD_PACK, relative), record["assets"][relative])
        result = {"review_id": rid, "source_protocol_id": export.OLD_PROTOCOL,
            "mode": "legacy_future40_symlink", **linked, "future": record["future"], **FALSE_FLAGS}
        write(f"admin/events/{rid}.json", manual.json_bytes(result))
        completed.append(result)
    print(f"future150: preserved {len(old)} legacy references", flush=True)
    for index, (source, records) in enumerate(sorted(by_source.items()), 1):
        bound = source_read_end([manual.representative(groups[r["review_id"]]) for r in records])
        frame, audit = manual.read_preholdout_prefix((ROOT / source).resolve(), end_exclusive=bound)
        if audit["holdout_ohlcv_rows_materialized"] != 0:
            raise ValueError("prefix materialized holdout values")
        audit["end_exclusive"] = bound.isoformat()
        write(source_files[source], manual.json_bytes(audit))
        frame = with_hl2_mas(frame)
        for record in sorted(records, key=lambda r: r["review_id"]):
            rid = record["review_id"]
            representative = manual.representative(groups[rid])
            original = inside(export.NEW_PACK, record["asset_roles"]["image"]).read_bytes()
            replay_sha = replay_main(frame, representative, record["chart_transform"], original)
            content, future = render_future(frame, representative)
            relative = image_relative(rid)
            write(relative, content)
            result = {"review_id": rid, "source_protocol_id": export.NEW_PROTOCOL,
                "mode": "new_future150", "image_path": relative,
                "image_sha256": hashlib.sha256(content).hexdigest(), "future": future,
                "source_path": source, "source_prefix_audit": source_files[source],
                "original_input_sha256": record["assets"][record["asset_roles"]["image"]],
                "main_replay_pixel_sha256": replay_sha, **FALSE_FLAGS}
            write(f"admin/events/{rid}.json", manual.json_bytes(result))
            completed.append(result)
        print(f"future150: {len(completed) - len(old)}/1043 new; source {index}/{len(by_source)} {source}", flush=True)
    after = media_snapshot(old, new, groups)
    if after != before:
        raise ValueError("immutable inputs changed during future render")
    write("admin/immutable_media_after.json", manual.json_bytes(after))
    if (source_identity()[1] != code or any(manual.sha(ROOT / p) != h for p, h in prereg["source_sha256"].items())
            or export.read_sources()[2] != source_info):
        raise ValueError("code/source metadata changed during build")
    completed.sort(key=lambda r: r["review_id"])
    write("manifest.jsonl", b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n").encode() for r in completed))
    new_results = [r for r in completed if r["mode"] == "new_future150"]
    receipt = {**json.loads((PACK / "admin/started.json").read_text()), **identity,
        "new_events": len(new_results), "legacy_events": len(old), "lookup_images": len(completed),
        "main_replay_passed": len(new_results),
        "verified_positive_image_label_pairs": sum(map(len, groups.values())),
        "immutable_media_before_sha256": before["snapshot_sha256"],
        "immutable_media_after_sha256": after["snapshot_sha256"], "immutable_media_files": before["files"],
        "actual_future_bars_counts": dict(Counter(str(r["future"]["actual_future_bars"]) for r in new_results)),
        "missing_future_reasons": dict(Counter(r["future"]["missing_future_reason"] or "complete" for r in new_results)),
        "manifest_sha256": manual.sha(PACK / "manifest.jsonl"),
        "source_prefix_audit_sha256": {p: manual.sha(PACK / p) for p in sorted(source_files.values())},
        "task_data_writes": 0, "prediction_writes": 0, "annotation_writes": 0, "draft_writes": 0,
        "future_used_for_training_input": False, "future_used_for_labels": False,
        "status": "ready_for_human_review_future150", **FALSE_FLAGS}
    write("admin/build_receipt.json", manual.json_bytes(receipt))
    audit_output(expected_files, {r["review_id"] for r in old})
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build"])
    parser.parse_args()
    print(json.dumps(build(), ensure_ascii=False, indent=2))
