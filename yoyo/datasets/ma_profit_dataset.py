"""Materialize causal-view YOLO inputs from frozen, future-labelled MA events.

Only the label resolver may inspect the later barrier horizon.  Images stop at
the original five-bar confirmation, compute HL2 moving averages from a bounded
1,200-bar prefix, and never burn boxes or outcome text into pixels.  A retained
TP is a training-positive only in the train split; val/test retain every resolved
event as an evaluation candidate and use empty labels for non-winners.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

import cv2
import numpy as np
import pandas as pd

from yoyo.datasets.fifteen_minute_launch_candidates import read_preholdout_prefix, sha256_file
from yoyo.datasets.ma_rope_filter import SIX_MA_COLUMNS
from yoyo.datasets.ma_launch_owner_recrop_review import core_box
from yoyo.layers.l1_detection import render as chart_render


ROOT = Path(__file__).resolve().parents[2]
WIDTH, HEIGHT, SUPPORT_BARS = 1280, 742, 1200
UP_BLUE, DOWN_PURPLE = (232, 120, 40), (174, 60, 107)  # BGR


class ProfitDatasetError(RuntimeError):
    """Raised when frozen outcome lineage cannot produce a causal-view input."""


def _utc(value: object) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        raise ProfitDatasetError(f"timestamp requires timezone: {value!r}")
    return stamp.tz_convert("UTC")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _committed(paths: list[Path]) -> str:
    if subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip() != "main":
        raise ProfitDatasetError("dataset builder must run on main")
    relative = [str(path.resolve().relative_to(ROOT)) for path in paths]
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", *relative], cwd=ROOT, text=True).strip()
    if dirty:
        raise ProfitDatasetError("commit builder, plan, and frozen ledger before materializing:\n" + dirty)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def add_hl2_mas(frame: pd.DataFrame) -> pd.DataFrame:
    """Add causal HL2 SMA/EMA 20/60/120 without using rows outside ``frame``."""

    output = frame.copy()
    hl2 = (output["high"] + output["low"]) / 2.0
    for period in (20, 60, 120):
        output[f"sma{period}"] = hl2.rolling(period).mean()
        output[f"ema{period}"] = hl2.ewm(span=period, adjust=False).mean()
    return output


def _recolor_candles(image: np.ndarray) -> np.ndarray:
    """Keep the shared renderer geometry while applying the requested blue/purple candles."""

    output = image.copy()
    output[np.all(output == chart_render.CANDLE_GREEN, axis=2)] = UP_BLUE
    output[np.all(output == chart_render.CANDLE_RED, axis=2)] = DOWN_PURPLE
    return output


def label_line(asset: Mapping[str, Any]) -> str:
    """Serialize one YOLO label; evaluation negatives deliberately stay empty."""

    if not asset["positive"]:
        return ""
    box = asset["box"]
    return (
        f"{asset['class_id']} {box['cx_norm']:.8f} {box['cy_norm']:.8f} "
        f"{box['w_norm']:.8f} {box['h_norm']:.8f}\n"
    )


def asset_stem(event_id: str, variant: str) -> str:
    """Keep arbitrary source event identities out of Windows filesystem names."""

    return "event_" + hashlib.sha256(event_id.encode("utf-8")).hexdigest()[:32] + "_" + variant


def _cohort_controls(plan: Mapping[str, Any], events_path: Path) -> tuple[dict[str, Any], list[Path]]:
    """Require a frozen, independently counted training cohort before rendering."""

    from yoyo.datasets.ma_profit_cohort import verify_selected_cohort

    paths: dict[str, Path] = {}
    for stem in ("training_contract", "selection_receipt"):
        raw = str(plan.get(stem + "_path", ""))
        if not raw:
            raise ProfitDatasetError(f"missing {stem}_path")
        path = Path(raw)
        paths[stem] = path if path.is_absolute() else ROOT / path
        if sha256_file(paths[stem]) != plan.get(stem + "_sha256"):
            raise ProfitDatasetError(f"{stem} SHA drift")
    receipt = verify_selected_cohort(events_path, paths["selection_receipt"], paths["training_contract"])
    return receipt, list(paths.values())


def arms_for_asset(split: str, variant: str) -> tuple[str, ...]:
    """Route one view into the preregistered A/B training recipes."""

    if split == "train":
        return ("A",) if variant == "A" else ("B",)
    if split in {"val", "test"} and variant == "A":
        return ("A", "B")
    return ()


def _window_asset(
    frame: pd.DataFrame, *, core_start_i: int, core_end_i: int, pre_bars: int, post_bars: int,
    support_start_i: int,
) -> tuple[bytes, dict[str, Any], dict[str, Any]]:
    """Render one no-future window and return PNG bytes plus its exact core box."""

    end_i, start_i = core_end_i + post_bars, core_start_i - pre_bars
    if support_start_i < 0 or start_i < support_start_i or end_i >= len(frame):
        raise ProfitDatasetError("window/support outside source")
    # Shared earliest support makes all overlapping A/B bars use identical MAs.
    causal = add_hl2_mas(frame.iloc[support_start_i:end_i + 1].reset_index(drop=True))
    local_start, local_end = start_i - support_start_i, end_i - support_start_i
    window = causal.iloc[local_start:local_end + 1].reset_index(drop=True)
    if len(window) != pre_bars + (core_end_i - core_start_i + 1) + post_bars:
        raise ProfitDatasetError("window bar count drift")
    image, transform = chart_render.render_chart(window, width=WIDTH, height=HEIGHT)
    image = _recolor_candles(image)
    ok, encoded = cv2.imencode(".png", image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not ok:
        raise ProfitDatasetError("PNG encoding failed")
    box = core_box(transform, window, start_local=pre_bars, end_local=pre_bars + core_end_i - core_start_i)
    return encoded.tobytes(), box, {"window_start_i": start_i, "window_end_i": end_i, "visible_end_open_time_utc": pd.Timestamp(frame["open_time"].iloc[end_i]).isoformat(), "feature_support_start_i": support_start_i, "feature_support_start_utc": pd.Timestamp(frame["open_time"].iloc[support_start_i]).isoformat()}


def event_assets(frame: pd.DataFrame, row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Create A plus train-only B render variants for one ledger event, entirely in memory."""

    profit = dict(row["profit"])
    core_start_time, core_end_time = _utc(row["core_start_time"]), _utc(row["core_end_time"])
    times = pd.to_datetime(frame["open_time"], utc=True)
    positions = {stamp: index for index, stamp in enumerate(times)}
    if core_start_time not in positions or core_end_time not in positions:
        raise ProfitDatasetError(f"core timestamp absent: {row['event_id']}")
    start_i, end_i = positions[core_start_time], positions[core_end_time]
    if end_i - start_i + 1 not in {4, 5}:
        raise ProfitDatasetError(f"core length drift: {row['event_id']}")
    bar_minutes = int(row["bar_minutes"])
    if not (times.iloc[start_i:end_i + 1].diff().dropna() == pd.Timedelta(minutes=bar_minutes)).all():
        raise ProfitDatasetError("core source gap")
    decision_i = end_i + 5
    if decision_i >= len(frame):
        raise ProfitDatasetError("confirmation missing")
    decision_close = times.iloc[decision_i] + pd.Timedelta(minutes=bar_minutes)
    if decision_close != _utc(profit["decision_close_time_utc"]):
        raise ProfitDatasetError("ledger decision time disagrees with source c+5 close")
    if not bool(profit.get("retained", False)) and row["split"] == "train":
        return []
    if row["split"] not in {"train", "val", "test"} or profit.get("outcome") not in {"TP", "SL", "TIMEOUT"}:
        return []
    support_start = start_i - 11 - SUPPORT_BARS
    if support_start < 0:
        raise ProfitDatasetError("insufficient 1200-bar MA support")
    # The source group may have been loaded through a later event, but this
    # event's rendered input has no access beyond its own confirmation close.
    frame = frame.iloc[:decision_i + 1].copy()
    variants = [("A", 9, 5)]
    if row["split"] == "train": variants.extend((("B1", 7, 5), ("B2", 11, 5)))
    if row["direction"] not in {"LONG", "SHORT"}:
        raise ProfitDatasetError(f"unsupported direction: {row['direction']!r}")
    result = []
    for variant, pre, post in variants:
        png, box, visible = _window_asset(frame, core_start_i=start_i, core_end_i=end_i, pre_bars=pre, post_bars=post, support_start_i=support_start)
        visible["visible_end_close_time_utc"] = (
            _utc(visible["visible_end_open_time_utc"]) + pd.Timedelta(minutes=bar_minutes)
        ).isoformat()
        visible["decision_at_utc"] = decision_close.isoformat()
        positive = bool(profit.get("retained", False))
        result.append({"variant": variant, "png": png, "box": box, "visible": visible, "positive": positive,
                       "class_id": 0 if row["direction"] == "LONG" else 1, "core_start_i": start_i, "core_end_i": end_i,
                       "label_horizon_end_utc": profit.get("label_window_end_utc")})
    return result


def build(plan_path: Path, events_path: Path, output: Path) -> dict[str, Any]:
    """Build a new root only after frozen cohort capacity and independence pass."""

    plan, events_path, output = json.loads(plan_path.read_text()), events_path.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite dataset root: {output}")
    if sha256_file(events_path) != str(plan["events_sha256"]):
        raise ProfitDatasetError("frozen event ledger SHA drift")
    _, control_paths = _cohort_controls(plan, events_path)
    commit = _committed([Path(__file__), ROOT / "yoyo/datasets/ma_profit_cohort.py", plan_path.resolve(), events_path, *control_paths])
    rows = _jsonl(events_path)
    if not rows: raise ProfitDatasetError("empty event ledger")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts: Counter[str] = Counter()
    seen_event_ids: set[str] = set()
    source_hashes: dict[str, str] = {}
    for row in rows:
        event_id = str(row.get("event_id", ""))
        if not event_id or event_id in seen_event_ids:
            raise ProfitDatasetError(f"event_id must occur once: {event_id!r}")
        seen_event_ids.add(event_id)
        source_path = str(row.get("source_path", ""))
        if not source_path:
            raise ProfitDatasetError(f"source path absent: {event_id}")
        if source_path not in source_hashes:
            source_file = Path(source_path)
            source_file = source_file if source_file.is_absolute() else ROOT / source_file
            source_hashes[source_path] = sha256_file(source_file)
        if str(row.get("source_sha256", "")) != source_hashes[source_path]:
            raise ProfitDatasetError(f"ledger source SHA drift: {source_path}")
        split, profit = str(row.get("split", "")), dict(row.get("profit", {}))
        outcome = str(profit.get("outcome", "UNKNOWN"))
        if split == "purged":
            counts["excluded_purged"] += 1
        elif outcome not in {"TP", "SL", "TIMEOUT"}:
            counts[f"excluded_{outcome.lower()}"] += 1
        elif split == "train" and not bool(profit.get("retained", False)):
            counts["excluded_train_nonwinner"] += 1
        elif split not in {"train", "val", "test"}:
            raise ProfitDatasetError(f"unsupported split for resolved event: {split!r}")
        else:
            groups[str(row["source_path"])].append(row)
    output.mkdir(parents=True)
    for split in ("train", "val", "test"):
        (output / "images" / split).mkdir(parents=True)
        (output / "labels" / split).mkdir(parents=True)
    manifest: list[dict[str, Any]] = []
    arm_images: dict[str, dict[str, list[str]]] = {
        arm: {split: [] for split in ("train", "val", "test")} for arm in ("A", "B")
    }
    arm_events: dict[str, dict[str, set[str]]] = {
        arm: {split: set() for split in ("train", "val", "test")} for arm in ("A", "B")
    }
    for source_path, source_rows in groups.items():
        max_close = max(_utc(row["profit"]["decision_close_time_utc"]) for row in source_rows)
        bar_minutes = int(source_rows[0]["bar_minutes"])
        if any(int(row["bar_minutes"]) != bar_minutes for row in source_rows): raise ProfitDatasetError("mixed timeframe source group")
        source_file = Path(source_path)
        source_file = source_file if source_file.is_absolute() else ROOT / source_file
        source_sha = source_hashes[source_path]
        frame, _ = read_preholdout_prefix(source_file, end_exclusive=max_close, bar_minutes=bar_minutes)
        for row in source_rows:
            for asset in event_assets(frame, row):
                stem = asset_stem(str(row['event_id']), str(asset['variant']))
                arms = arms_for_asset(str(row["split"]), str(asset["variant"]))
                if not arms:
                    continue
                image_rel = f"images/{row['split']}/{stem}.png"
                label_rel = f"labels/{row['split']}/{stem}.txt"
                (output / image_rel).write_bytes(asset["png"])
                label = label_line(asset)
                (output / label_rel).write_text(label)
                manifest.append({"arms": list(arms), "event_id": row["event_id"], "cluster_id": row["cluster_id"], "canonical_asset": row["canonical_asset"], "core_end_time": row["core_end_time"], "bar_minutes": bar_minutes, "split": row["split"], "variant": asset["variant"], "direction": row["direction"], "source_path": source_path, "source_sha256": source_sha, "image_path": image_rel, "image_sha256": hashlib.sha256(asset["png"]).hexdigest(), "label_path": label_rel, "label_sha256": sha256_file(output / label_rel), "class_id": asset["class_id"] if asset["positive"] else None, "box": asset["box"] if asset["positive"] else None, "visible_end_open_time_utc": asset["visible"]["visible_end_open_time_utc"], "visible_end_close_time_utc": asset["visible"]["visible_end_close_time_utc"], "decision_at_utc": asset["visible"]["decision_at_utc"], "feature_support_start_i": asset["visible"]["feature_support_start_i"], "feature_support_start_utc": asset["visible"]["feature_support_start_utc"], "label_horizon_end_utc": asset["label_horizon_end_utc"], "training_eligible": False, "production_eligible": False})
                for arm in arms:
                    arm_images[arm][str(row["split"])].append(f"./{image_rel}")
                    arm_events[arm][str(row["split"])].add(str(row["event_id"]))
                    counts[f"{arm}_{row['split']}_images"] += 1
    _write_jsonl(output / "manifest.jsonl", manifest)
    names = plan.get("render", {}).get("classes", {"0": "profitlong", "1": "profitshort"})
    if {str(key) for key in names} != {"0", "1"}:
        raise ProfitDatasetError("plan render.classes must define exactly class IDs 0 and 1")
    for split in ("val", "test"):
        if arm_images["A"][split] != arm_images["B"][split]:
            raise AssertionError(f"{split} must be common between A and B")
        (output / f"{split}.txt").write_text("\n".join(arm_images["A"][split]) + "\n")
    for arm in ("A", "B"):
        (output / f"train_{arm}.txt").write_text("\n".join(arm_images[arm]["train"]) + "\n")
        (output / f"data_{arm}.yaml").write_text(
            f"path: {output}\ntrain: train_{arm}.txt\nval: val.txt\ntest: test.txt\nnc: 2\n"
            f"names: [{names['0']}, {names['1']}]\n"
        )
    summary = {"builder_commit": commit, "plan_sha256": sha256_file(plan_path), "events_sha256": sha256_file(events_path), "images": dict(counts), "arms": {arm: {split: {"images": len(arm_images[arm][split]), "events": len(arm_events[arm][split])} for split in ("train", "val", "test")} for arm in ("A", "B")}, "training_authorized": plan.get("owner_authorization", {}).get("training_authorized", False), "training_eligible": False, "production_eligible": False, "ma_source": "hl2", "support_bars": SUPPORT_BARS}
    summary.update({
        "manifest_sha256": sha256_file(output / "manifest.jsonl"),
        "training_contract_sha256": plan["training_contract_sha256"],
        "selection_receipt_sha256": plan["selection_receipt_sha256"],
    })
    _write_json(output / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path); parser.add_argument("--events", required=True, type=Path); parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(); print(json.dumps(build(args.plan, args.events, args.out), ensure_ascii=False))


if __name__ == "__main__": main()
