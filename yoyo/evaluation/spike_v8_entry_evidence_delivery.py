"""Bind the fixed V8 entry-evidence reports and local artifacts by SHA-256.

This packaging step reads completed research outputs only; it neither reads
price caches nor recomputes trading outcomes. It is not a new holdout scoring
configuration. Large data and chart files remain local and are hash-bound.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-v8-entry-evidence-20260914-v1"


def main() -> None:
    result = EXP / "results/full_v1"
    cases = EXP / "cases_v2"
    if not json.loads((result / "manifest.json").read_text())["complete"]:
        raise ValueError("incomplete research output")
    if not json.loads((cases / "receipt.json").read_text())["complete"]:
        raise ValueError("incomplete case gallery")
    for receipt in (result / "receipt.json", cases / "receipt.json"):
        content = json.loads(receipt.read_text())
        expected = content.get("output_sha256", content)
        for name, digest in expected.items():
            if hashlib.sha256((receipt.parent / name).read_bytes()).hexdigest() != digest:
                raise ValueError(f"output hash mismatch: {receipt.parent / name}")
    report = ROOT / "analysis/p1_spike_v8_entry_evidence_20260914.md"
    html = ROOT / "analysis/html/p1_spike_v8_entry_evidence_20260914.html"
    files = [Path(__file__), report, html]
    files += [p for folder in (result, cases) for p in folder.iterdir() if p.is_file()]
    files += [EXP / name for name in ("config.json", "PROJECT_PLAN.md", "holdout_receipt.json")]
    records = [{"path": str(p.relative_to(ROOT)), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "size_bytes": p.stat().st_size} for p in sorted(set(files))]
    payload = {"complete": True, "generated_at": datetime.now(timezone.utc).isoformat(),
               "head_at_packaging": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "records": records, "new_price_cache_reads": 0,
               "training_eligible": False, "production_eligible": False}
    path = EXP / "delivery_receipt.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({"path": str(path), "files": len(records), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
