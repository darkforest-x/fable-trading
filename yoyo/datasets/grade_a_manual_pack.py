"""Prepare blank manual review of all frozen Grade-A event groups.

The Owner requested complete event-level annotation with up to 40 future bars.
Original close-rendered inputs are copied unchanged; future context is physically
separate and never becomes a label or training input. Features use only close
SMA/EMA 20/60/120 from the original source prefix, preserving EMA initialization.
Every source variant retains its own bar/price ChartTransform for later mapping;
an annotation in one normalized image coordinate system is not another's label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pandas as pd

from yoyo.contracts.holdout import HOLDOUT_START
from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix
from yoyo.datasets.ma_rope_filter import add_six_mas
from yoyo.layers.l1_detection.render import make_chart_transform, render_chart

ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "datasets/ma_launch_owner_grade_a8000_yolo_neg24000_v1"
MANIFEST_SHA256 = "22e95465b072fdfc4b0284f439c73a7f1cc9be9ab998ea768b2857a7cec798e2"
PACK = ROOT / "datasets/grade_a_manual_events_20260907_v1"
PREREG = ROOT / "experiments/active/exp-15m-grade-a-labelstudio-manual-20260907-v1/preregistration.json"
PROTOCOL_ID = "manual_from_blank_future40_v1"
BAR = pd.Timedelta(minutes=15)
FUTURE_BARS = 40
VARIANT_FIELDS = ("dataset_sample_id", "image_path", "image_sha256", "label_path", "label_sha256",
    "split", "window_start_i", "window_end_i", "window_start_time", "window_end_time",
    "variant_id", "variant_index", "pre_bars", "post_bars", "core_bars")
MISSING_REASONS = {"source_end": "数据已到末尾", "source_gap": "未来有缺根",
                   "holdout_boundary": "已到保留集边界"}


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for part in iter(lambda: handle.read(1 << 20), b""):
            digest.update(part)
    return digest.hexdigest()


def stamp(value: object) -> pd.Timestamp:
    value = pd.Timestamp(value)
    if value.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return value.tz_convert("UTC")


def event_key(row: dict) -> tuple[str, str]:
    kind = row["sample_kind"]
    if kind not in {"positive", "negative"}:
        raise ValueError("unknown source sample kind")
    return kind, row["event_id" if kind == "positive" else "negative_event_id"]


def representative(group: list[dict]) -> dict:
    return min(group, key=lambda r: (-int(r["window_bars"]), -int(r["post_bars"]),
                                    int(r["variant_index"]), r["dataset_sample_id"]))


def context_variant(group: list[dict]) -> dict:
    return min(group, key=lambda r: (stamp(r["window_start_time"]),
                                    -int(r["window_bars"]), r["dataset_sample_id"]))


def group_events(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    groups, ids = defaultdict(list), set()
    for row in rows:
        start, end = stamp(row["window_start_time"]), stamp(row["window_end_time"])
        n = int(row["window_bars"])
        if n not in {18, 19} or end-start != (n-1)*BAR or end+BAR > HOLDOUT_START:
            raise ValueError("source input window violates the pre-holdout contract")
        if int(row["window_end_i"])-int(row["window_start_i"]) != n-1:
            raise ValueError("source index/time disagreement")
        if row["split"] not in {"train", "val"} or row["dataset_sample_id"] in ids:
            raise ValueError("duplicate sample or invalid split")
        ids.add(row["dataset_sample_id"])
        groups[event_key(row)].append(row)
    for group in groups.values():
        if len({(r["source_path"], r["split"]) for r in group}) != 1:
            raise ValueError("event crosses source or split")
    return dict(groups)


def source_read_end(representatives: list[dict]) -> pd.Timestamp:
    return min(pd.Timestamp(HOLDOUT_START),
               max(stamp(r["window_end_time"])+(FUTURE_BARS+1)*BAR for r in representatives))


def checked_window(frame: pd.DataFrame, row: dict) -> pd.DataFrame:
    window = frame.iloc[int(row["window_start_i"]):int(row["window_end_i"])+1]
    times = pd.to_datetime(window["open_time"], utc=True)
    if (len(window) != int(row["window_bars"]) or times.empty
            or times.iloc[0] != stamp(row["window_start_time"])
            or times.iloc[-1] != stamp(row["window_end_time"])
            or not times.diff().iloc[1:].eq(BAR).all()
            or times.iloc[-1]+BAR > HOLDOUT_START):
        raise ValueError("original input is incomplete or its timestamps changed")
    return window


def bounded_future_window(frame: pd.DataFrame, row: dict) -> tuple[pd.DataFrame, dict]:
    """Keep the complete input and stop future context at its first unavailable bar."""
    original = checked_window(frame, row)
    end, actual, reason = int(row["window_end_i"]), 0, None
    for offset in range(1, FUTURE_BARS+1):
        expected = stamp(row["window_end_time"])+offset*BAR
        if expected+BAR > HOLDOUT_START:
            reason = "holdout_boundary"
            break
        if end+offset >= len(frame):
            reason = "source_end"
            break
        if stamp(frame.iloc[end+offset]["open_time"]) != expected:
            reason = "source_gap"
            break
        actual += 1
    window = frame.iloc[int(row["window_start_i"]):end+actual+1]
    return window, {"requested_future_bars": FUTURE_BARS, "actual_future_bars": actual,
        "missing_future_reason": reason, "input_bars": len(original), "review_bars": len(window),
        "input_start_bar_open": stamp(row["window_start_time"]).isoformat(),
        "input_end_bar_open": stamp(row["window_end_time"]).isoformat(),
        "input_available_at": (stamp(row["window_end_time"])+BAR).isoformat(),
        "review_end_bar_open": stamp(window.iloc[-1]["open_time"]).isoformat(),
        "review_available_at": (stamp(window.iloc[-1]["open_time"])+BAR).isoformat()}


def frozen_write(path: Path, content: bytes) -> None:
    """Resume identical output only; an unfinished write never replaces evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"existing output differs: {path}")
        return
    temporary = path.with_name(path.name+".pending")
    temporary.write_bytes(content)
    temporary.replace(path)


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)+"\n").encode()


def source_identity() -> tuple[str, dict[str, str]]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    paths = [Path(__file__), PREREG, ROOT/"yoyo/contracts/holdout.py", ROOT/"yoyo/layers/l1_detection/render.py",
        ROOT/"yoyo/layers/l1_detection/data.py", ROOT/"yoyo/datasets/ma_rope_filter.py",
        ROOT/"yoyo/datasets/fifteen_minute_launch_candidates.py"]
    identities = {}
    for path in paths:
        relative = path.resolve().relative_to(ROOT).as_posix()
        committed = subprocess.check_output(["git", "show", f"{head}:{relative}"], cwd=ROOT)
        identities[relative] = sha(path)
        if hashlib.sha256(committed).hexdigest() != identities[relative]:
            raise ValueError(f"commit source before building: {relative}")
    return head, identities


def verify_source_files(rows: list[dict]) -> None:
    for row in rows:
        for kind in ("image", "label"):
            path = (DATASET/row[f"{kind}_path"]).resolve()
            if not path.is_relative_to(DATASET.resolve()) or sha(path) != row[f"{kind}_sha256"]:
                raise ValueError(f"original {kind} SHA drift: {row['dataset_sample_id']}")


def build() -> dict:
    head, code_hashes = source_identity()
    prereg = json.loads(PREREG.read_text())
    for relative, expected in prereg["source_sha256"].items():
        if sha(ROOT/relative) != expected:
            raise ValueError(f"preregistered input changed: {relative}")
    if prereg["source_sha256"][str((DATASET/"manifest.jsonl").relative_to(ROOT))] != MANIFEST_SHA256 or sha(DATASET/"manifest.jsonl") != MANIFEST_SHA256:
        raise ValueError("frozen source manifest changed")
    rows = [json.loads(line) for line in (DATASET/"manifest.jsonl").read_text().splitlines()]
    groups = group_events(rows)
    if len(rows) != 32000 or len(groups) != 4172:
        raise ValueError("expected exactly 32000 variants and 4172 event groups")
    verify_source_files(rows)
    identity = {"protocol_id": PROTOCOL_ID, "manifest_sha256": MANIFEST_SHA256,
        "source_hashes": code_hashes, "runtime": {"cv2": cv2.__version__, "numpy": np.__version__, "pandas": pd.__version__}}
    frozen_write(PACK/"admin/build_identity.json", json_bytes(identity))
    start_path = PACK/"admin/started.json"
    if not start_path.exists():
        frozen_write(start_path, json_bytes({"source_commit": head, "created_at": datetime.now(timezone.utc).isoformat()}))
    started = json.loads(start_path.read_text())
    by_source = defaultdict(list)
    for key, group in groups.items():
        rid = hashlib.sha256((PROTOCOL_ID+"|"+"|".join(key)).encode()).hexdigest()[:24]
        by_source[group[0]["source_path"]].append((rid, group))
    completed = []
    for source, entries in sorted(by_source.items()):
        pending = [pair for pair in entries if not (PACK/f"admin/events/{pair[0]}.json").exists()]
        if pending:
            path = (ROOT/source).resolve()
            if not path.is_relative_to(ROOT):
                raise ValueError("source path escapes repository")
            bound = source_read_end([representative(g) for _, g in entries])
            frame, audit = read_preholdout_prefix(path, end_exclusive=bound)
            if audit["holdout_ohlcv_rows_materialized"] != 0:
                raise ValueError("prefix reader materialized holdout")
            audit["end_exclusive"] = bound.isoformat()
            source_id = hashlib.sha256(source.encode()).hexdigest()[:24]
            frozen_write(PACK/f"admin/sources/{source_id}.json", json_bytes(audit))
            frame = add_six_mas(frame)
        for rid, group in sorted(entries):
            progress = PACK/f"admin/events/{rid}.json"
            if not progress.exists():
                rep, context = representative(group), context_variant(group)
                raw, _ = render_chart(checked_window(frame, rep), out_path=None)
                original = cv2.imread(str(DATASET/rep["image_path"]), cv2.IMREAD_COLOR)
                if original is None or raw.shape != original.shape or not np.array_equal(raw, original):
                    raise ValueError(f"representative input pixel replay failed: {rid}")
                window, future_meta = bounded_future_window(frame, rep)
                future, future_tf = render_chart(window, out_path=None)
                separator = None
                if future_meta["actual_future_bars"]:
                    n = future_meta["input_bars"]
                    separator = int(round((future_tf.x_at(n-1)+future_tf.x_at(n))/2))
                    cv2.line(future, (separator, future_tf.top),
                             (separator, future_tf.top+future_tf.plot_h), (160, 160, 160), 1, cv2.LINE_AA)
                future_meta["input_end_separator_x_px"] = separator
                future_meta["input_end_separator_x_norm"] = separator/future_tf.width if separator is not None else None
                ok, encoded = cv2.imencode(".png", future, [cv2.IMWRITE_PNG_COMPRESSION, 4])
                if not ok:
                    raise ValueError("future PNG encoding failed")
                assets = {f"input_images/{rid}.png": (DATASET/rep["image_path"]).read_bytes(),
                    f"context_images/{rid}.png": (DATASET/context["image_path"]).read_bytes(),
                    f"future_only/images/{rid}.png": encoded.tobytes()}
                for relative, content in assets.items():
                    frozen_write(PACK/relative, content)
                transforms = [{**{key: r[key] for key in VARIANT_FIELDS}, "review_id": rid,
                    "is_representative": r["dataset_sample_id"] == rep["dataset_sample_id"],
                    "is_context": r["dataset_sample_id"] == context["dataset_sample_id"],
                    "chart_transform": asdict(make_chart_transform(checked_window(frame, r)))} for r in group]
                record = {"review_id": rid, "source_event_key": list(event_key(rep)), "source_path": rep["source_path"],
                    "assets": {relative: hashlib.sha256(content).hexdigest() for relative, content in assets.items()},
                    "input_pixel_sha256": hashlib.sha256(raw.tobytes()).hexdigest(), "lineage": transforms,
                    "future": {**future_meta, "chart_transform": asdict(future_tf), "review_only": True,
                               "training_eligible": False}}
                frozen_write(progress, json_bytes(record))
            record = json.loads(progress.read_text())
            if record["source_event_key"] != list(event_key(group[0])) or record["review_id"] != rid:
                raise ValueError("resume event identity mismatch")
            for relative, digest in record["assets"].items():
                if sha(PACK/relative) != digest:
                    raise ValueError("resume asset SHA drift")
            completed.append(record)
        print(f"manual pack: {len(completed)}/4172 events; {source}", flush=True)
    tasks, lineage, futures = [], [], []
    for record in sorted(completed, key=lambda r: r["review_id"]):
        rid, future = record["review_id"], record["future"]
        data = {"review_id": rid, "event_id": rid, "protocol_id": PROTOCOL_ID,
            "actual_future_bars": future["actual_future_bars"], "missing_future_reason": future["missing_future_reason"],
            "caption": f"原图 {future['input_bars']} 根；后文 {future['actual_future_bars']}/40 根"
                + (f"（{MISSING_REASONS[future['missing_future_reason']]}）" if future["missing_future_reason"] else "")}
        for key, folder in (("image", "input_images"), ("context_image", "context_images"), ("future_image", "future_only/images")):
            relative = f"{folder}/{rid}.png"
            data[key] = f"/data/local-files/?d=label_studio/{PACK.name}/{relative}"
            data[f"{key}_sha256"] = record["assets"][relative]
        tasks.append({"data": data})
        lineage.extend(record["lineage"])
        futures.append({"review_id": rid, **future})
    verify_source_files(rows)
    if sha(DATASET/"manifest.jsonl") != MANIFEST_SHA256 or source_identity()[1] != code_hashes:
        raise ValueError("source manifest or code changed during build")
    frozen_write(PACK/"admin/lineage.jsonl", b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True)+"\n").encode() for r in lineage))
    frozen_write(PACK/"future_only/manifest.json", json_bytes({"protocol_id": PROTOCOL_ID, "training_eligible": False, "items": futures}))
    frozen_write(PACK/"tasks.json", json_bytes(tasks))
    if any("labels" in p.parts or p.suffix == ".txt" for p in (PACK/"future_only").rglob("*")):
        raise ValueError("future-only directory contains labels")
    receipt = {**started, **identity, "events": len(tasks), "source_variants": len(lineage),
        "original_input_replay_passed": len(tasks), "original_image_and_label_hashes_verified": len(rows),
        "actual_future_bars_counts": dict(Counter(str(r["future"]["actual_future_bars"]) for r in completed)),
        "future_missing_reasons": dict(Counter(r["future"]["missing_future_reason"] or "complete" for r in completed)),
        "tasks_sha256": sha(PACK/"tasks.json"), "lineage_sha256": sha(PACK/"admin/lineage.jsonl"),
        "future_manifest_sha256": sha(PACK/"future_only/manifest.json"), "status": "ready_for_manual_annotation",
        "holdout_read": False, "new_inference": False, "training_eligible": False, "production_eligible": False}
    frozen_write(PACK/"admin/build_receipt.json", json_bytes(receipt))
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build"])
    parser.parse_args()
    print(json.dumps(build(), ensure_ascii=False, indent=2))
