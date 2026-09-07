"""Review historical Owner boxes without replacing their semantic evidence.

The Owner authorized reuse and geometric refinement of 2,513 direction boxes.
The original interval and direction remain facts; the historical central-half
4--7-bar interval is a baseline, not a newly identified onset. A proposal uses
only that same core's full wicks and six SMA/EMA 20/60/120 values with 4% padding,
via the existing recrop helper. All new canvases are review-only. Future context
is rendered in a separate pass and never supplied to proposal selection.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
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
from yoyo.datasets.ma_launch_owner_recrop_review import core_box
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS, add_six_mas
from yoyo.layers.l1_detection.render import render_chart

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "datasets/owner_box_refinement_20260907_v1"
PREREG = ROOT / "experiments/active/exp-owner-box-refinement-20260907-v1/preregistration.json"
SHEET = ROOT / "analysis/output/owner_side_review/review_sheet.csv"
SCORES = ROOT / "datasets/owner_short_gold_center_v1/review/ma_rope_prefilter_v1/admin/owner_2525_scores.jsonl"
STARS = ROOT / "data/benchmark_exemplars.json"
SOURCE_SHA256 = {
    str(SHEET.relative_to(ROOT)): "bb7081e7e1821c5f791486fae0f29caf18307b104bbb07156c35883781071c9a",
    str(SCORES.relative_to(ROOT)): "0752df544308e6c26f647fc1a520375a551ed003e234d12f3e50dd8a05deef0b",
    str(STARS.relative_to(ROOT)): "5acc5d8f68d2671ecd9351bcfc417bc1d27fa7f201d8cd83ec1293b7b231a762",
}
PROTOCOL_ID = "owner_geometry_refinement_v1"
BAR = pd.Timedelta(minutes=15)
FUTURE_BARS = 40
MISSING = {"source_end": "数据已到末尾", "source_gap": "未来有缺根", "holdout_boundary": "已到保留集边界"}
DEPENDENCIES = (
    "yoyo/datasets/owner_box_refinement.py", "tests/test_owner_box_refinement.py",
    "yoyo/contracts/holdout.py", "yoyo/datasets/fifteen_minute_launch_candidates.py",
    "yoyo/datasets/ma_rope_filter.py", "yoyo/datasets/ma_launch_owner_recrop_review.py",
    "yoyo/layers/l1_detection/render.py", "yoyo/layers/l1_detection/data.py",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def stable_id(*values: object) -> str:
    return hashlib.sha256("|".join(map(str, values)).encode()).hexdigest()[:24]


def stamp(value: object) -> pd.Timestamp:
    value = pd.Timestamp(value)
    if value.tzinfo is None or pd.isna(value):
        raise ValueError("an explicit valid UTC timestamp is required")
    return value.tz_convert("UTC")


def frozen_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"existing output differs: {path}")
        return
    temporary = path.with_name(path.name + ".pending")
    temporary.write_bytes(content)
    temporary.replace(path)


def png(image: np.ndarray) -> bytes:
    ok, data = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 4])
    if not ok:
        raise ValueError("PNG encoding failed")
    return data.tobytes()


def source_identity(prereg_path: Path = PREREG) -> tuple[str, dict]:
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    if branch != "main":
        raise ValueError("build requires the shared main branch")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    identities = {}
    for path in [*(ROOT / name for name in DEPENDENCIES), prereg_path]:
        relative = path.resolve().relative_to(ROOT).as_posix()
        content = subprocess.check_output(["git", "show", f"{head}:{relative}"], cwd=ROOT)
        identities[relative] = sha(path)
        if hashlib.sha256(content).hexdigest() != identities[relative]:
            raise ValueError(f"commit source before building: {relative}")
    return head, identities


def central_core(start: int, end: int) -> tuple[int, int]:
    width = end - start + 1
    if width < 4:
        raise ValueError("original Owner interval has fewer than four bars")
    count = max(4, min(7, math.ceil(width / 2)))
    first = start + (width - count) // 2
    return first, first + count - 1


def box_iou(a: list, b: list) -> float:
    def corners(box):
        x, y, w, h = map(float, box)
        return x - w / 2, y - h / 2, x + w / 2, y + h / 2
    x, y, r, btm = corners(a)
    xx, yy, rr, bb = corners(b)
    intersection = max(0, min(r, rr) - max(x, xx)) * max(0, min(btm, bb) - max(y, yy))
    union = (r-x)*(btm-y) + (rr-xx)*(bb-yy) - intersection
    return intersection / union if union > 0 else 0.0


def plan_rows(rows: list[dict], score_rows: list[dict], stars: dict) -> tuple[list[dict], list[dict]]:
    scores = {r["sample_id"]: r for r in score_rows}
    if len(scores) != len(score_rows) or set(scores) != {r["box_id"] for r in rows} or len({r["box_id"] for r in rows}) != len(rows):
        raise ValueError("sheet and source mapping must join one-to-one")
    plans, excluded = [], []
    for row in rows:
        rid, side, time = row["box_id"], row["owner_side"], stamp(row["cut_time"])
        cut, width, b0, b1 = (int(row[k]) for k in ("cut_global", "width_bars", "bar_b0", "bar_b1"))
        score = scores[rid]
        if time + BAR > HOLDOUT_START or cut < 0:
            raise ValueError("Owner anchor violates the pre-holdout close boundary")
        if (score["symbol"] != row["symbol"] or score["owner_side"] != side
                or int(score["decision_bar"]) != cut or stamp(score["decision_time"]) != time):
            raise ValueError("source mapping index/time/side mismatch")
        if side not in {"short", "long", "skip"} or not 0 <= b0 <= b1 < 200 or width != b1-b0+1:
            raise ValueError("invalid Owner direction or original interval")
        if side == "skip":
            excluded.append({"box_id": rid, "owner_side": side, "reason": "historical_owner_skip"})
            continue
        original_start, start = cut-b1, cut-width+1
        core_start, core_end = central_core(start, cut)
        main_start = start-6
        main_end = min(cut+3, cut+int((HOLDOUT_START-time)//BAR)-1)
        if main_start < 0 or main_end < cut:
            raise ValueError("incomplete main review interval")
        yolo = [float(row[k]) for k in ("yolo_xc", "yolo_yc", "yolo_w", "yolo_h")]
        if not all(math.isfinite(v) for v in yolo) or yolo[2] <= 0 or yolo[3] <= 0:
            raise ValueError("invalid original Owner rectangle")
        matches = stars.get(row["stem"], {})
        is_star = any(box_iou(yolo, [b[k] for k in ("cx", "cy", "w", "h")]) >= .999 for b in matches.get("boxes", []))
        original_close = time + (200-b1)*BAR
        plans.append({"review_id": stable_id(PROTOCOL_ID, rid), "box_id": rid,
            "symbol": row["symbol"], "owner_side": side, "original_split": row["split"],
            "owner_row_sha256": hashlib.sha256(encoded(row)).hexdigest(),
            "source_path": score["resolved_source_csv"], "cut_global": cut, "cut_time": time.isoformat(),
            "original_geometry": {"yolo_xywh": yolo, "bar_b0": b0, "bar_b1": b1,
                "source_start_i": start, "source_end_i": cut, "window_start_i": original_start,
                "window_end_i": original_start+199, "window_available_at": original_close.isoformat(),
                "win_mode": row["win_mode"], "stem": row["stem"], "box_index": int(row["box_index"]),
                "y_geometry_scope": "historical_canvas_only_not_reprojected"},
            "original_reference_allowed": original_close <= HOLDOUT_START,
            "original_preview_path": str(Path("analysis/output/owner_side_review") / row["preview_path"]),
            "historical_image_path": row["image_path"], "exact_star": is_star,
            "source_export": matches.get("source_export") if is_star else None,
            "native_ls_reference": None, "direction_confirmation": "owner_sample_level",
            "sample_owner_geometry_confirmed": False, "training_eligible": False, "production_eligible": False,
            "baseline": {"core_start_i": core_start, "core_end_i": core_end,
                "method": "historical_central_half_ceil_clamped_4_7", "sample_confirmed": False},
            "main_start_i": main_start, "main_end_i": main_end,
            "main_start_time": (time+(main_start-cut)*BAR).isoformat(),
            "main_end_time": (time+(main_end-cut)*BAR).isoformat(),
            "alias_candidate_group": stable_id(row["symbol"], side, start, cut)})
    aliases = Counter(p["alias_candidate_group"] for p in plans)
    for p in plans:
        p["alias_candidate_count"] = aliases[p["alias_candidate_group"]]
    for symbol in sorted({p["symbol"] for p in plans}):
        block, edge = [], -1
        for p in sorted((p for p in plans if p["symbol"] == symbol), key=lambda p: (p["original_geometry"]["window_start_i"], p["box_id"])):
            a, b = p["original_geometry"]["window_start_i"], p["original_geometry"]["window_end_i"]
            if a > edge and block:
                _assign_block(block)
                block = []
            block.append(p)
            edge = max(edge, b)
        _assign_block(block)
    return sorted(plans, key=lambda p: (not p["exact_star"], stamp(p["cut_time"]), p["box_id"])), excluded


def _assign_block(block: list[dict]) -> None:
    identity = stable_id("original_window_overlap", *(p["box_id"] for p in block))
    for p in block:
        p["original_window_dependency_id"] = identity


def checked_window(frame: pd.DataFrame, plan: dict) -> pd.DataFrame:
    cut, start, end = plan["cut_global"], plan["main_start_i"], plan["main_end_i"]
    if cut >= len(frame) or stamp(frame.iloc[cut]["open_time"]) != stamp(plan["cut_time"]):
        raise ValueError("source cut index/time mismatch")
    window = frame.iloc[start:end+1]
    times = pd.to_datetime(window["open_time"], utc=True)
    if (len(window) != end-start+1 or times.empty or times.iloc[0] != stamp(plan["main_start_time"])
            or times.iloc[-1] != stamp(plan["main_end_time"]) or times.iloc[-1]+BAR > HOLDOUT_START
            or not times.diff().iloc[1:].eq(BAR).all()):
        raise ValueError("main review interval is incomplete or not continuous")
    return window


def geometry(window: pd.DataFrame, transform, plan: dict) -> dict:
    """Use only main-window core values; never accept a future frame or select onset."""
    if len(window) != plan["main_end_i"]-plan["main_start_i"]+1:
        raise ValueError("geometry accepts exactly the main review window")
    a, b = (plan["baseline"][k]-plan["main_start_i"] for k in ("core_start_i", "core_end_i"))
    proposal = core_box(transform, window, start_local=a, end_local=b, pad_fraction=.04)
    if not proposal["contains_core_wicks_and_six_mas"]:
        raise ValueError("proposal clipped core geometry")
    core = window.iloc[a:b+1]
    baseline = {"x0": transform.x_at(a)-transform.candle_half_w,
        "x1": transform.x_at(b)+transform.candle_half_w,
        "y0": transform.y_at(float(core["high"].max())), "y1": transform.y_at(float(core["low"].min()))}
    return {"proposal": proposal, "wick_reference_box": baseline,
            "chart_transform": asdict(transform), "future_values_used_for_proposal": False}


def comparison(image: np.ndarray, plan: dict, geo: dict, transform) -> np.ndarray:
    out = image.copy()
    a = plan["original_geometry"]["source_start_i"]-plan["main_start_i"]
    b = plan["cut_global"]-plan["main_start_i"]
    cv2.rectangle(out, (0, 0), (1279, 51), (255, 255, 255), -1)
    n = b-a+1
    core_n = plan["baseline"]["core_end_i"]-plan["baseline"]["core_start_i"]+1
    detail = f"Original {n} bars -> central {core_n}; blue: original X only; orange: wick reference; green: wick+6MA+4%; no onset inference"
    cv2.putText(out, detail, (12, 17), cv2.FONT_HERSHEY_SIMPLEX, .38, (55, 55, 55), 1, cv2.LINE_AA)
    cv2.line(out, (transform.x_at(a), 38), (transform.x_at(b), 38), (210, 110, 30), 3)
    for key, color, thickness in (("wick_reference_box", (20, 155, 235), 2), ("proposal", (45, 165, 35), 2)):
        box = geo[key]
        cv2.rectangle(out, (round(box["x0"]), round(box["y0"])), (round(box["x1"]), round(box["y1"])), color, thickness, cv2.LINE_AA)
    for i in range(transform.n_bars):
        cv2.putText(out, str(i+1), (max(0, transform.x_at(i)-5), 740), cv2.FONT_HERSHEY_SIMPLEX, .3, (60, 60, 60), 1)
    return out


def original_reference(plan: dict, clean: bytes) -> tuple[bytes, str]:
    # Crucially, no file access occurs in the disallowed branch.
    if not plan["original_reference_allowed"]:
        return clean, "png"
    path = (ROOT/plan["original_preview_path"]).resolve()
    if not path.is_relative_to(ROOT/"analysis/output/owner_side_review"):
        raise ValueError("original preview path escapes its source directory")
    return path.read_bytes(), "jpg"


def verify_original_timestamps(path: Path, plans: list[dict]) -> dict:
    """Inspect timestamps only before opening any historically long reference.

    Index arithmetic alone cannot prove a full W200 is safe when CSVs contain
    gaps. The Owner-authorized timestamp-only pass can inspect a boundary clock,
    but never loads OHLCV columns. Both canonical timestamp representations must
    agree; an unexpected alias/schema fails explicitly.
    """
    last = max(p["original_geometry"]["window_end_i"] for p in plans)
    clocks = pd.read_csv(path, usecols=["ts", "open_time"], nrows=last+1)
    times = pd.to_datetime(clocks["open_time"], utc=True, errors="raise")
    epoch_times = pd.to_datetime(clocks["ts"], unit="ms", utc=True, errors="raise")
    if times.isna().any() or not times.equals(epoch_times):
        raise ValueError("canonical ts/open_time columns disagree")
    checks = {}
    for p in plans:
        a, b = (p["original_geometry"][k] for k in ("window_start_i", "window_end_i"))
        check = {"allowed": False, "reason": None, "window_start_bar_open": None,
                 "window_end_bar_open": None, "window_available_at": None}
        if a < 0 or b >= len(times):
            check["reason"] = "source_end"
        else:
            window = times.iloc[a:b+1]
            check.update(window_start_bar_open=window.iloc[0].isoformat(), window_end_bar_open=window.iloc[-1].isoformat(),
                         window_available_at=(window.iloc[-1]+BAR).isoformat())
            if (window+BAR > HOLDOUT_START).any():
                check["reason"] = "holdout_boundary"
            elif not window.diff().iloc[1:].eq(BAR).all():
                check["reason"] = "source_gap"
            elif times.iloc[p["cut_global"]] != stamp(p["cut_time"]):
                check["reason"] = "source_time_mismatch"
            else:
                check["allowed"] = True
        checks[p["review_id"]] = check
    return {"checks": checks, "verified_original_references": sum(c["allowed"] for c in checks.values()),
            "timestamp_rows_inspected": len(times), "holdout_timestamp_rows_inspected": int((times >= HOLDOUT_START).sum()),
            "columns_read": ["ts", "open_time"], "ohlcv_materialized": False}


def window_sha256(window: pd.DataFrame) -> str:
    columns = ["open_time", "open", "high", "low", "close", "volume", *SIX_MA_COLUMNS]
    view = window.loc[:, columns].copy()
    view["open_time"] = pd.to_datetime(view["open_time"], utc=True).map(lambda t: t.isoformat())
    return hashlib.sha256(view.to_csv(index=False, float_format="%.17g", lineterminator="\n").encode()).hexdigest()


def future_window(frame: pd.DataFrame, plan: dict) -> tuple[pd.DataFrame, dict]:
    checked_window(frame, plan)
    end, actual, reason = plan["main_end_i"], 0, None
    for offset in range(1, FUTURE_BARS+1):
        expected = stamp(plan["main_end_time"])+offset*BAR
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
    window = frame.iloc[plan["main_start_i"]:end+actual+1]
    return window, {"requested_future_bars": 40, "actual_future_bars": actual,
        "missing_future_reason": reason, "start_bar_open": plan["main_start_time"],
        "main_end_bar_open": plan["main_end_time"], "main_available_at": (stamp(plan["main_end_time"])+BAR).isoformat(),
        "review_end_bar_open": stamp(window.iloc[-1]["open_time"]).isoformat(),
        "review_available_at": (stamp(window.iloc[-1]["open_time"])+BAR).isoformat(),
        "main_bars": end-plan["main_start_i"]+1, "review_bars": len(window),
        "review_only": True, "training_eligible": False}


def task_for(record: dict, pack: Path) -> dict:
    rid, p = record["review_id"], record["proposal"]
    future = record["future"]
    a = record["baseline"]["core_start_i"]-record["main_start_i"]+1
    b = record["baseline"]["core_end_i"]-record["main_start_i"]+1
    side = "多头" if record["owner_side"] == "long" else "空头"
    caption = f"{'⭐ ' if record['exact_star'] else ''}{side} · 核心第 {a}–{b} 根 · 后续 {future['actual_future_bars']}/40 根。框合适可直接提交，边界不对可拖动。"
    if future["missing_future_reason"]:
        caption += MISSING[future["missing_future_reason"]]+"。"
    if not record["original_reference_allowed"]:
        caption += "原图越界或不完整，已用安全画布替代。"
    data = {"review_id": rid, "protocol_id": PROTOCOL_ID, "caption": caption}
    for key, relative in record["asset_roles"].items():
        data[key] = f"/data/local-files/?d=label_studio/{pack.name}/{relative}"
        data[f"{key}_sha256"] = record["assets"][relative]
    result = {"id": rid[:10], "from_name": "pattern", "to_name": "image", "type": "rectanglelabels",
        "original_width": 1280, "original_height": 742, "image_rotation": 0,
        "value": {"x": p["x0"]/1280*100, "y": p["y0"]/742*100,
            "width": (p["x1"]-p["x0"])/1280*100, "height": (p["y1"]-p["y0"])/742*100,
            "rotation": 0, "rectanglelabels": ["多头" if record["owner_side"] == "long" else "空头"]}}
    return {"data": data, "predictions": [{"model_version": PROTOCOL_ID, "result": [result]}]}


def build(output_dir: Path = PACK, prereg_path: Path = PREREG) -> dict:
    head, code = source_identity(prereg_path)
    prereg = json.loads(prereg_path.read_text())
    if prereg.get("protocol_id", PROTOCOL_ID) != PROTOCOL_ID:
        raise ValueError("preregistered protocol changed")
    for relative, digest in SOURCE_SHA256.items():
        if prereg["source_sha256"].get(relative) != digest or sha(ROOT/relative) != digest:
            raise ValueError(f"frozen source changed: {relative}")
    with SHEET.open() as handle:
        rows = list(csv.DictReader(handle))
    scores = [json.loads(line) for line in SCORES.read_text().splitlines() if line]
    plans, excluded = plan_rows(rows, scores, json.loads(STARS.read_text())["exemplars"])
    if (len(plans), len(excluded), sum(p["exact_star"] for p in plans)) != (2513, 12, 104):
        raise ValueError("frozen population changed")
    identity = {"protocol_id": PROTOCOL_ID, "source_sha256": SOURCE_SHA256, "code_sha256": code,
        "runtime": {"cv2": cv2.__version__, "numpy": np.__version__, "pandas": pd.__version__}}
    frozen_write(output_dir/"admin/build_identity.json", encoded(identity))
    started_path = output_dir/"admin/started.json"
    if not started_path.exists():
        frozen_write(started_path, encoded({"source_commit": head, "created_at": datetime.now(timezone.utc).isoformat()}))
    started = json.loads(started_path.read_text())
    frozen_write(output_dir/"admin/excluded.json", encoded(excluded))
    by_source = defaultdict(list)
    for plan in plans:
        by_source[plan["source_path"]].append(plan)
    completed = {}
    for source, group in sorted(by_source.items()):
        path = (ROOT/source).resolve()
        if not path.is_relative_to(ROOT/"data/kline_fetched") or not path.is_file():
            raise ValueError("source CSV is absent or outside the local source root")
        timestamp_audit = verify_original_timestamps(path, group)
        frozen_write(output_dir/f"admin/sources/{stable_id(source)}_original_timestamps.json", encoded(timestamp_audit))
        for plan in group:
            plan["original_reference_arithmetic_allowed"] = plan["original_reference_allowed"]
            plan["original_reference_check"] = timestamp_audit["checks"][plan["review_id"]]
            plan["original_reference_allowed"] = plan["original_reference_check"]["allowed"]
        pending = [p for p in group if not (output_dir/f"admin/events/{p['review_id']}.json").exists()]
        if pending:
            # Phase one sees only the maximum main-window prefix for this symbol.
            bound = min(HOLDOUT_START, max(stamp(p["main_end_time"])+BAR for p in group))
            frame, audit = read_preholdout_prefix(path, end_exclusive=bound)
            frozen_write(output_dir/f"admin/sources/{stable_id(source)}_main.json", encoded({**audit, "end_exclusive": bound.isoformat()}))
            frame = add_six_mas(frame)
            for plan in group:
                window = checked_window(frame, plan)
                image, tf = render_chart(window)
                geo = geometry(window, tf, plan)
                clean = png(image)
                original, ext = original_reference(plan, clean)
                rid = plan["review_id"]
                roles = {"image": f"annotation_images/{rid}.png", "original_image": f"original_reference_only/{rid}.{ext}",
                    "comparison_image": f"comparison_images/{rid}.png"}
                contents = {roles["image"]: clean, roles["original_image"]: original,
                    roles["comparison_image"]: png(comparison(image, plan, geo, tf))}
                for relative, content in contents.items():
                    frozen_write(output_dir/relative, content)
                record = {**plan, **geo, "asset_roles": roles,
                    "assets": {k: hashlib.sha256(v).hexdigest() for k, v in contents.items()},
                    "main_window_ohlcv_ma_sha256": window_sha256(window),
                    "main_canvas_role": "review_only_not_a_training_input", "new_gold": False}
                frozen_write(output_dir/f"admin/geometry/{rid}.json", encoded(record))
            del frame
            # Only after every proposal is frozen may human future data be read.
            bound = min(HOLDOUT_START, max(stamp(p["main_end_time"])+(FUTURE_BARS+1)*BAR for p in group))
            frame, audit = read_preholdout_prefix(path, end_exclusive=bound)
            frozen_write(output_dir/f"admin/sources/{stable_id(source)}_future.json", encoded({**audit, "end_exclusive": bound.isoformat()}))
            frame = add_six_mas(frame)
            for plan in pending:
                rid = plan["review_id"]
                record = json.loads((output_dir/f"admin/geometry/{rid}.json").read_text())
                if window_sha256(checked_window(frame, plan)) != record["main_window_ohlcv_ma_sha256"]:
                    raise ValueError("main OHLCV/MA changed between proposal and future reads")
                window, meta = future_window(frame, plan)
                image, tf = render_chart(window)
                separator = None
                if meta["actual_future_bars"]:
                    n = meta["main_bars"]
                    separator = round((tf.x_at(n-1)+tf.x_at(n))/2)
                    cv2.line(image, (separator, tf.top), (separator, tf.top+tf.plot_h), (180, 60, 180), 2, cv2.LINE_AA)
                relative = f"future_only/images/{rid}.png"
                content = png(image)
                frozen_write(output_dir/relative, content)
                record["asset_roles"]["future_image"] = relative
                record["assets"][relative] = hashlib.sha256(content).hexdigest()
                record["future"] = {**meta, "chart_transform": asdict(tf), "main_end_separator_x_px": separator}
                frozen_write(output_dir/f"admin/events/{rid}.json", encoded(record))
        for plan in group:
            record = json.loads((output_dir/f"admin/events/{plan['review_id']}.json").read_text())
            if any(record.get(k) != v for k, v in plan.items()):
                raise ValueError("resume plan identity changed")
            for relative, digest in record["assets"].items():
                target = (output_dir/relative).resolve()
                if not target.is_relative_to(output_dir.resolve()) or sha(target) != digest:
                    raise ValueError("resume image identity changed")
            if record["original_reference_allowed"] and sha(ROOT/record["original_preview_path"]) != record["assets"][record["asset_roles"]["original_image"]]:
                raise ValueError("original historical preview changed")
            completed[plan["review_id"]] = record
        print(f"owner refinement: {len(completed)}/2513", flush=True)
    records = [completed[p["review_id"]] for p in plans]
    if source_identity(prereg_path)[1] != code or any(sha(ROOT/p) != h for p, h in SOURCE_SHA256.items()):
        raise ValueError("source changed during build")
    frozen_write(output_dir/"manifest.jsonl", b"".join((json.dumps(r, ensure_ascii=False, sort_keys=True)+"\n").encode() for r in records))
    frozen_write(output_dir/"future_only/manifest.json", encoded({"protocol_id": PROTOCOL_ID, "review_only": True,
        "training_eligible": False, "items": [{"review_id": r["review_id"], "image": r["asset_roles"]["future_image"],
            "image_sha256": r["assets"][r["asset_roles"]["future_image"]], **r["future"]} for r in records]}))
    frozen_write(output_dir/"tasks.json", encoded([task_for(r, output_dir) for r in records]))
    if any("labels" in p.parts or p.suffix == ".txt" for p in (output_dir/"future_only").rglob("*")):
        raise ValueError("future-only labels are forbidden")
    aliases = Counter(p["alias_candidate_group"] for p in plans)
    summary = {**identity, **started, "tasks": len(records), "excluded_owner_skip": len(excluded),
        "owner_side_counts": dict(Counter(p["owner_side"] for p in plans)), "exact_star": 104,
        "candidate_alias_groups": sum(n>1 for n in aliases.values()), "aliases_not_merged": sum(n-1 for n in aliases.values()),
        "original_images_copied": sum(r["original_reference_allowed"] for r in records),
        "original_images_blocked_before_open": sum(not r["original_reference_allowed"] for r in records),
        "original_image_block_reasons": dict(Counter(r["original_reference_check"]["reason"] for r in records if not r["original_reference_allowed"])),
        "actual_future_bars_counts": dict(Counter(str(r["future"]["actual_future_bars"]) for r in records)),
        "future_missing_reasons": dict(Counter(r["future"]["missing_future_reason"] or "complete" for r in records)),
        "main_start_min": min(p["main_start_time"] for p in plans), "main_end_max": max(p["main_end_time"] for p in plans),
        "tasks_sha256": sha(output_dir/"tasks.json"), "manifest_sha256": sha(output_dir/"manifest.jsonl"),
        "future_manifest_sha256": sha(output_dir/"future_only/manifest.json"), "holdout_ohlcv_read": False,
        "new_training": False, "new_model_inference": False, "new_gold": False,
        "training_eligible": False, "production_eligible": False, "status": "ready_for_owner_geometry_review"}
    frozen_write(output_dir/"admin/build_receipt.json", encoded(summary))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["build"])
    parser.parse_args()
    print(json.dumps(build(), ensure_ascii=False, indent=2))
