#!/usr/bin/env python3
"""Rebuild 4h YOLO delivery charts so every scored window is visible.

The detector results are immutable inputs here.  This utility replays each
saved event from frozen candles with the current delivery-context rule, verifies
the exact model-input pixel hash inside ``render_event``, then moves the old
charts/overviews into a recoverable backup before promoting the rebuilt files.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import cv2
import pandas as pd

from scripts import scan_4h_ma_launch_yolo_latest as base
from yoyo.layers.l1_detection.data import add_mas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    summary_path = out / "summary.json"
    signals_path = out / "signals.csv"
    if not summary_path.is_file() or not signals_path.is_file():
        raise FileNotFoundError(f"incomplete scan output: {out}")
    backup = out / "delivery_pre_context_fix"
    staging = out / ".delivery_context_rebuild"
    if backup.exists() or staging.exists():
        raise FileExistsError("delivery context backup/staging already exists")

    summary_sha_before = base.sha256_file(summary_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    events = [dict(event) for event in summary["signals"]]
    frames: dict[str, pd.DataFrame] = {}
    for symbol in sorted({str(event["symbol"]) for event in events}):
        frame = pd.read_csv(out / "candles" / f"{symbol}.csv")
        frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
        frames[symbol] = add_mas(frame)

    staging.mkdir()
    staging_charts = staging / "charts"
    staging_charts.mkdir()
    chart_paths: list[Path] = []
    for order, event in enumerate(events, 1):
        image = base.render_event(
            event,
            frame=frames[str(event["symbol"])],
            order=order,
            total=len(events),
        )
        filename = Path(str(event["chart"])).name
        path = staging_charts / filename
        if not cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
            raise base.FourHourYoloError(f"could not write rebuilt chart: {path}")
        event["chart"] = f"charts/{filename}"
        event["chart_sha256"] = base.sha256_file(path)
        chart_paths.append(path)
    base.build_overview(chart_paths, events, staging)

    backup.mkdir()
    shutil.copy2(summary_path, backup / "summary.json")
    shutil.copy2(signals_path, backup / "signals.csv")
    shutil.move(str(out / "charts"), str(backup / "charts"))
    old_overviews = sorted(out.glob("overview*.png"))
    for path in old_overviews:
        shutil.move(str(path), str(backup / path.name))
    shutil.move(str(staging_charts), str(out / "charts"))
    for path in sorted(staging.glob("overview*.png")):
        shutil.move(str(path), str(out / path.name))
    staging.rmdir()

    pd.DataFrame(events).to_csv(signals_path, index=False)
    summary["signals"] = events
    summary["delivery_context_rule"] = "min(latest_80_start, scored_window_start)"
    summary["delivery_context_rebuilt_at"] = datetime.now(timezone.utc).isoformat()
    summary["delivery_context_old_artifacts"] = "delivery_pre_context_fix"
    base.write_json(summary_path, summary)
    receipt = {
        "verdict": "PASS",
        "rebuilt_at": summary["delivery_context_rebuilt_at"],
        "events_rebuilt": len(events),
        "model_inference": 0,
        "network_reads": 0,
        "model_inputs_replayed": len(events),
        "summary_sha256_before": summary_sha_before,
        "summary_sha256_after": base.sha256_file(summary_path),
        "backup": "delivery_pre_context_fix",
        "delivery_context_rule": summary["delivery_context_rule"],
    }
    base.write_json(out / "delivery_context_rebuild.json", receipt)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
