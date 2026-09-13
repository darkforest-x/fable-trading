"""Bind completed V8 process-study reports and local evidence without rescoring.

This delivery inventory reads file bytes and recorded completion/parity flags.
It does not select parameters, generate labels, or alter trading outcomes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


ENTRY = Path("experiments/active/exp-spike-v8-entry-process-20260913-v1")
EXIT = Path("experiments/active/exp-spike-v8-early-exit-20260913-v1")
REPORT = Path("analysis/p1_spike_v8_entry_process_early_exit_20260913.md")
HTML = Path("analysis/html/p1_spike_v8_entry_process_early_exit_20260913.html")


def identity(path: Path) -> dict[str, object]:
    return {"path": str(path), "size_bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> None:
    entry = json.loads((ENTRY / "results/full_v1/manifest.json").read_text())
    exits = json.loads((EXIT / "results/full_v1/manifest.json").read_text())
    if not all(m["complete"] and m["official"] and m["streams"] == 3531 for m in (entry, exits)):
        raise ValueError("Both official complete 3531-stream manifests are required")
    stream_receipts = sorted((EXIT / "results/full_v1/streams").glob("*.json"))
    if len(stream_receipts) != 3531 or not all(json.loads(p.read_text())["v8_baseline_parity"] for p in stream_receipts):
        raise ValueError("Every exit stream must have passed frozen V8 baseline parity")
    if "待正式回放" in REPORT.read_text() or "待本轮动态" in REPORT.read_text():
        raise ValueError("Report still has pending results")
    paths = [REPORT, HTML, Path(__file__), EXIT / "dependency_audit_while_running.json"]
    for folder in (ENTRY / "results/full_v1", EXIT / "results/full_v1",
                   ENTRY / "results/charts_v2", ENTRY / "delivery"):
        paths.extend(p for p in sorted(folder.iterdir()) if p.is_file())
    receipt = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "complete": True, "streams": 3531, "entry_signals": entry["signals"],
        "baseline_parity_streams": len(stream_receipts),
        "stream_receipt_inventory_sha256": hashlib.sha256(json.dumps(
            [identity(p) for p in stream_receipts], sort_keys=True).encode()).hexdigest(),
        "selected_exit_policy": exits["selection"]["selected_policy"],
        "training_eligible": False, "production_eligible": False,
        "data_use": "authorized configuration-specific holdout-era use #1; reused nonblind historical data",
        "files": [identity(p) for p in paths],
        "notion_records": [
            "https://app.notion.com/p/3da8856479af8175a060c3b2c812e90e",
            "https://app.notion.com/p/3da8856479af8117952fd723d8432c78"],
        "limitations": "H1/H2 are same-entry strata; H3 is dynamic independent-stream replay. No live changes or actual shared-account claim.",
    }
    output = ENTRY / "delivery_receipt.json"
    output.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"receipt": str(output), "files": len(paths), "parity_streams": len(stream_receipts)}))


if __name__ == "__main__":
    main()
