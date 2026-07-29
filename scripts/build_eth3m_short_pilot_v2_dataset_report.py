#!/usr/bin/env python3
"""Thin CLI for the ETH 3m v2 dataset audit report builder."""
from __future__ import annotations

import json

from src.reporting.eth3m_v2_report_data import DATASET, OUT, REPORT_MD, _read_json
from src.reporting.eth3m_v2_report_narrative import build_artifact, build_markdown


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = _read_json(DATASET / "build_meta.json")
    validation = _read_json(OUT / "validation.json")
    receipt = _read_json(DATASET / "owner_confirmation_receipt.json")
    if validation["status"] not in {"pass", "passed"}:
        raise RuntimeError("refusing to report a failed dataset validation")
    artifact = build_artifact(meta, validation, receipt)
    (OUT / "artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (OUT / "report_notes.json").write_text(
        json.dumps(
            {
                "charts_omitted": False,
                "chart_reason": "A single horizontal bar chart is included because the 30/107/150 evidence split makes the positive-sample scarcity and quarantined weak labels materially easier to see. Integrity gates remain tabular.",
                "performance_metrics_omitted": True,
                "performance_reason": "No v2 model has been trained.",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    REPORT_MD.write_text(build_markdown(meta, validation, receipt), encoding="utf-8")
    print(json.dumps({"artifact": str(OUT / "artifact.json"), "markdown": str(REPORT_MD)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
