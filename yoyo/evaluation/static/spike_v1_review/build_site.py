#!/usr/bin/env python3
"""Build the immutable local SPIKE V1 review site from an already-built data contract."""
from __future__ import annotations
import argparse
import json
import math
import shutil
from pathlib import Path

SOURCE = Path(__file__).resolve().parent

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True, help="Directory containing manifest.json and charts/")
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = args.data_dir / "manifest.json"
    if not manifest.is_file(): raise SystemExit(f"missing manifest: {manifest}")
    payload = json.loads(manifest.read_text())
    records = payload.get("records")
    if not isinstance(records, list) or len(records) != 133: raise SystemExit("manifest must contain exactly 133 records")
    available, missing = 0, 0
    for record in records:
        if not isinstance(record, dict): raise SystemExit("records must be objects")
        if record.get("status") == "missing" or record.get("state", {}).get("status") == "missing":
            if not record.get("error"): raise SystemExit("missing record needs an explicit error")
            missing += 1
            continue
        chart_path = record.get("chart_path") if isinstance(record, dict) else None
        if not isinstance(chart_path, str) or not chart_path.startswith("charts/"):
            raise SystemExit("every record needs a relative charts/ chart_path")
        source = (args.data_dir / chart_path).resolve()
        if args.data_dir.resolve() not in source.parents or not source.is_file():
            raise SystemExit(f"missing controlled chart data: {chart_path}")
        chart = json.loads(source.read_text())
        candles = chart.get("candles")
        if not isinstance(candles, list) or not candles:
            raise SystemExit(f"chart needs nonempty candles: {chart_path}")
        for candle in candles:
            if not isinstance(candle, dict) or not all(
                value is not None and not isinstance(value, bool) and math.isfinite(float(value))
                for value in (candle.get("t"), candle.get("o"), candle.get("h"), candle.get("l"), candle.get("c"))
            ):
                raise SystemExit(f"chart needs finite OHLC timestamps: {chart_path}")
        available += 1
    if available + missing != 133: raise SystemExit("invalid review record accounting")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "app.js", "styles.css"):
        shutil.copy2(SOURCE / name, args.out_dir / name)
    shutil.copytree(SOURCE / "vendor", args.out_dir / "vendor", dirs_exist_ok=True)
    shutil.copytree(args.data_dir, args.out_dir / "data", dirs_exist_ok=True)
    print(json.dumps({"site": str(args.out_dir), "records": len(records), "available": available, "missing": missing, "schema_version": payload.get("schema_version")}, ensure_ascii=False))

if __name__ == "__main__": main()
