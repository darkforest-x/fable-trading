#!/usr/bin/env python3
"""Build the immutable local SPIKE V1 review site from an already-built data contract."""
from __future__ import annotations
import argparse
import json
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
    for record in records:
        chart_path = record.get("chart_path") if isinstance(record, dict) else None
        if not isinstance(chart_path, str) or not chart_path.startswith("charts/"):
            raise SystemExit("every record needs a relative charts/ chart_path")
        source = (args.data_dir / chart_path).resolve()
        if args.data_dir.resolve() not in source.parents or not source.is_file():
            raise SystemExit(f"missing controlled chart data: {chart_path}")
        chart = json.loads(source.read_text())
        if not isinstance(chart.get("candles"), list) or not chart["candles"]:
            raise SystemExit(f"chart needs nonempty candles: {chart_path}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "app.js", "styles.css"):
        shutil.copy2(SOURCE / name, args.out_dir / name)
    shutil.copytree(SOURCE / "vendor", args.out_dir / "vendor", dirs_exist_ok=True)
    shutil.copytree(args.data_dir, args.out_dir / "data", dirs_exist_ok=True)
    print(json.dumps({"site": str(args.out_dir), "records": len(records), "schema_version": payload.get("schema_version")}, ensure_ascii=False))

if __name__ == "__main__": main()
