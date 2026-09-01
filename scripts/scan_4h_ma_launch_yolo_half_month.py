#!/usr/bin/env python3
"""Scan the frozen all-symbol 4h snapshot over the latest 15 calendar days.

This experiment changes one analytical variable relative to the 2026-09-01
all-universe latest scan: confirmed inference endpoints expand from 6 to 90.
The frozen universe, candle bytes, 15m-trained YOLO weight, confidence/NMS,
W18/W19 inputs, structural box contract, and five-bar event gap are unchanged.

The source snapshot is reused without network access so universe membership and
the latest completed 4h bar cannot drift while the longer inference runs.  This
remains an out-of-distribution research scan, never a production trade signal.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import pandas as pd

from scripts import scan_4h_ma_launch_yolo_latest as base


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "analysis/output/ma_launch_4h_yolo_alluniverse_20260901_v1"
DEFAULT_OUT = ROOT / "analysis/output/ma_launch_4h_yolo_halfmonth_20260901_v1"
LOOKBACK_ENDPOINTS = 90
HOLDOUT_CONSUMPTION_NUMBER = 6
EXPERIMENT_ID = "p1_4h_yolo_alluniverse_halfmonth_20260901_v1"


def load_frozen_snapshot(
    source: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, pd.DataFrame]]:
    """Load and hash-check the prior all-universe snapshot without networking."""

    summary_path = source / "summary.json"
    universe_path = source / "universe.json"
    if not summary_path.is_file() or not universe_path.is_file():
        raise base.FourHourYoloError(f"incomplete frozen source snapshot: {source}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    universe = json.loads(universe_path.read_text(encoding="utf-8"))
    if summary.get("universe_mode") != "all_eligible" or universe.get("mode") != "all_eligible":
        raise base.FourHourYoloError("half-month experiment requires the frozen all-eligible universe")
    if summary.get("weights_sha256") != base.EXPECTED_WEIGHT_SHA256:
        raise base.FourHourYoloError("source scan used a different checkpoint")
    if int(summary.get("lookback_confirmed_4h_bars", -1)) != base.LOOKBACK_ENDPOINTS:
        raise base.FourHourYoloError("source scan is not the frozen six-endpoint baseline")

    frames: dict[str, pd.DataFrame] = {}
    for audit in summary["fetch_audits"]:
        symbol = str(audit["symbol"])
        path = source / "candles" / f"{symbol}.csv"
        if base.sha256_file(path) != str(audit["sha256"]):
            raise base.FourHourYoloError(f"source candle hash drifted: {symbol}")
        frame = pd.read_csv(path)
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
        gaps = frame["open_time"].diff().iloc[1:]
        if not (gaps == base.BAR_DELTA).all():
            raise base.FourHourYoloError(f"source contains a non-4h gap: {symbol}")
        frames[symbol] = frame
    if len(frames) != int(summary["usable_symbols"]):
        raise base.FourHourYoloError("source usable-symbol count drifted")
    return summary, universe, frames


def daily_event_counts(events: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Count event onsets by Beijing calendar day and frozen direction."""

    counts: dict[str, Counter[str]] = {}
    for event in events:
        day = (
            pd.Timestamp(event["first_available_at"])
            .tz_convert("Asia/Shanghai")
            .strftime("%Y-%m-%d")
        )
        bucket = counts.setdefault(day, Counter())
        bucket["events"] += 1
        bucket["long"] += int(str(event["class_name"]) == "dense_long")
        bucket["short"] += int(str(event["class_name"]) == "dense_short")
    return {day: dict(sorted(bucket.items())) for day, bucket in sorted(counts.items())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--device", default=None)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()

    source = args.source.resolve()
    out = args.out.resolve()
    building = out.with_name(out.name + ".building")
    if out.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite experiment output: {out}")
    if base.sha256_file(base.WEIGHTS) != base.EXPECTED_WEIGHT_SHA256:
        raise base.FourHourYoloError("frozen YOLO weight identity drifted")

    source_summary, universe, frames = load_frozen_snapshot(source)
    started = time.perf_counter()
    building.mkdir(parents=True)
    candle_dir = building / "candles"
    chart_dir = building / "charts"
    candle_dir.mkdir()
    chart_dir.mkdir()
    try:
        shutil.copy2(source / "universe.json", building / "universe.json")
        for symbol in sorted(frames):
            shutil.copy2(source / "candles" / f"{symbol}.csv", candle_dir / f"{symbol}.csv")
        contract = {
            "experiment_id": EXPERIMENT_ID,
            "registered_before_inference": True,
            "single_changed_variable": "lookback_confirmed_4h_endpoints: 6 -> 90",
            "lookback_endpoints": LOOKBACK_ENDPOINTS,
            "calendar_interpretation": "90 confirmed 4h endpoints = 15 x 24h / 4h",
            "source_snapshot": str(source.relative_to(ROOT)),
            "source_summary_sha256": base.sha256_file(source / "summary.json"),
            "source_universe_sha256": base.sha256_file(source / "universe.json"),
            "weight_sha256": base.EXPECTED_WEIGHT_SHA256,
            "confidence": base.CONFIDENCE,
            "nms_iou": base.NMS_IOU,
            "window_lengths": list(base.WINDOW_LENGTHS),
            "core_lengths": sorted(base.ALLOWED_CORES),
            "confirmation_bars": sorted(base.ALLOWED_CONFIRMATIONS),
            "same_symbol_gap_bars": base.EVENT_GAP_BARS,
            "holdout_consumption_number_for_checkpoint": HOLDOUT_CONSUMPTION_NUMBER,
            "owner_authorization_scope": (
                "Owner explicitly requested an experiment detecting the most recent half-month "
                "after reviewing the full-universe latest-endpoint result."
            ),
            "network_reads": 0,
            "threshold_or_weight_changed": False,
        }
        base.write_json(building / "experiment_contract.json", contract)

        enriched, tasks = base.build_tasks(
            frames,
            lookback_endpoints=LOOKBACK_ENDPOINTS,
        )
        device = base.choose_device(args.device)
        from ultralytics import YOLO

        model = YOLO(str(base.WEIGHTS))
        names = {int(key): str(value) for key, value in model.names.items()}
        if names != base.CLASS_NAMES:
            raise base.FourHourYoloError(f"class map drifted: {names}")
        print(
            f"half-month inference device={device} symbols={len(frames)} tasks={len(tasks)}",
            flush=True,
        )
        candidates, stats = base.infer(
            model,
            tasks,
            frames=enriched,
            device=device,
            batch_size=max(1, args.batch_size),
        )

        events = base.deduplicate(candidates)
        for event in events:
            latest_market_bar_open = base.utc(
                enriched[str(event["symbol"])].iloc[-1]["open_time"]
            )
            event["latest_market_bar_open_time"] = latest_market_bar_open.isoformat()
            event["latest_market_bar_available_at"] = (
                latest_market_bar_open + base.BAR_DELTA
            ).isoformat()
            event["is_current_latest_bar"] = (
                base.utc(event["window_end_time"]) == latest_market_bar_open
            )

        chart_paths: list[Path] = []
        for order, event in enumerate(events, 1):
            image = base.render_event(
                event,
                frame=enriched[str(event["symbol"])],
                order=order,
                total=len(events),
            )
            side = "LONG" if int(event["class_id"]) == 0 else "SHORT"
            symbol = str(event["symbol"]).replace("_USDT_SWAP", "")
            path = chart_dir / f"{order:03d}_{symbol}_{side}.png"
            if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
                raise base.FourHourYoloError(f"could not write chart: {path}")
            event["chart"] = f"charts/{path.name}"
            event["chart_sha256"] = base.sha256_file(path)
            chart_paths.append(path)
        base.build_overview(chart_paths, events, building)

        pd.DataFrame(candidates).to_csv(building / "accepted_candidates.csv", index=False)
        pd.DataFrame(events).to_csv(building / "signals.csv", index=False)
        sides = Counter(str(event["class_name"]) for event in events)
        current = [event for event in events if bool(event["is_current_latest_bar"])]
        current_sides = Counter(str(event["class_name"]) for event in current)
        symbol_counts = Counter(str(event["symbol"]) for event in events)
        latest_open = max(base.utc(frame.iloc[-1]["open_time"]) for frame in frames.values())
        earliest_endpoint_open = latest_open - (LOOKBACK_ENDPOINTS - 1) * base.BAR_DELTA

        summary = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "experiment_id": EXPERIMENT_ID,
            "model": base.MODEL_NAME,
            "weights": str(base.WEIGHTS.relative_to(ROOT)),
            "weights_sha256": base.EXPECTED_WEIGHT_SHA256,
            "source_timeframe": "15m",
            "inference_timeframe": "4h",
            "out_of_distribution": True,
            "research_only": True,
            "data_source_mode": "frozen_snapshot_reuse",
            "network_reads": 0,
            "source_snapshot": str(source.relative_to(ROOT)),
            "source_summary_sha256": contract["source_summary_sha256"],
            "source_universe_sha256": contract["source_universe_sha256"],
            "universe_mode": "all_eligible",
            "universe_rule": str(universe["rule"]),
            "universe_symbols": len(universe["symbols"]),
            "usable_symbols": len(frames),
            "excluded_symbols": source_summary["excluded_symbols"],
            "lookback_confirmed_4h_bars": LOOKBACK_ENDPOINTS,
            "scan_first_endpoint_bar_open": earliest_endpoint_open.isoformat(),
            "scan_first_endpoint_available_at": (
                earliest_endpoint_open + base.BAR_DELTA
            ).isoformat(),
            "scan_last_endpoint_bar_open": latest_open.isoformat(),
            "scan_last_endpoint_available_at": (latest_open + base.BAR_DELTA).isoformat(),
            "windows_scored": int(stats["windows_scored"]),
            "raw_boxes": int(stats["raw_boxes"]),
            "accepted_structural_boxes": int(stats["accepted_structural_boxes"]),
            "deduplicated_events": len(events),
            "long_events": int(sides["dense_long"]),
            "short_events": int(sides["dense_short"]),
            "symbols_with_events": len(symbol_counts),
            "current_latest_bar_events": len(current),
            "current_latest_bar_long_events": int(current_sides["dense_long"]),
            "current_latest_bar_short_events": int(current_sides["dense_short"]),
            "daily_event_onsets_cst": daily_event_counts(events),
            "events_per_symbol": dict(sorted(symbol_counts.items())),
            "detector_contract": {
                "confidence": base.CONFIDENCE,
                "nms_iou": base.NMS_IOU,
                "imgsz": base.IMAGE_SIZE,
                "window_lengths": list(base.WINDOW_LENGTHS),
                "core_lengths": sorted(base.ALLOWED_CORES),
                "confirmation_bars": sorted(base.ALLOWED_CONFIRMATIONS),
                "same_symbol_gap_bars": base.EVENT_GAP_BARS,
            },
            "stats": dict(sorted(stats.items())),
            "fetch_audits": source_summary["fetch_audits"],
            "signals": events,
            "wall_seconds": round(time.perf_counter() - started, 3),
            "holdout_consumed": True,
            "holdout_consumption_number_for_checkpoint": HOLDOUT_CONSUMPTION_NUMBER,
            "owner_authorization_scope": contract["owner_authorization_scope"],
            "single_changed_variable": contract["single_changed_variable"],
            "threshold_or_weight_changed": False,
            "trained": False,
            "promoted": False,
            "deployed": False,
            "forward_state_changed": False,
            "orders_placed": False,
            "production_eligible": False,
        }
        base.write_json(building / "summary.json", summary)
        building.replace(out)
        print(
            f"complete tasks={len(tasks)} candidates={len(candidates)} events={len(events)} "
            f"output={out}",
            flush=True,
        )
        return 0
    except Exception:
        base.write_json(
            building / "failure_receipt.json",
            {
                "failed_at": datetime.now(timezone.utc).isoformat(),
                "source": str(source),
                "out": str(out),
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
