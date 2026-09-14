"""Package only already verified ETH research outputs, never original OHLC."""
from pathlib import Path
import hashlib
import json
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "experiments/active/exp-release-eth-multitf-20260914-v1"


def identity(path):
    return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size}


def main():
    audit = json.loads((EXP / "results/evidence_audit.json").read_text())
    if not audit["all_passed"] or audit["result_rows"] != 136:
        raise SystemExit("complete audited study required")
    manifest_path = EXP / "results/delivery_manifest.json"
    files = [f for f in sorted((EXP / "results").rglob("*")) if f.is_file() and f != manifest_path]
    files += [EXP / f for f in ("PROJECT_PLAN.md", "REPORT_NARRATIVE.md", "release_eth_research.pine", "PINE_PARITY_LIMITATIONS.md")]
    files += [ROOT / f for f in ("analysis/p1_release_eth_multitf_20260914.md", "analysis/html/p1_release_eth_multitf_20260914.html")]
    bundle = EXP / "release_eth_complete_evidence.zip"
    if bundle.exists() or manifest_path.exists():
        raise SystemExit("package already exists; do not overwrite evidence")
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for f in files:
            archive.write(f, str(f.relative_to(ROOT)))
    with zipfile.ZipFile(bundle) as archive:
        if archive.testzip() is not None:
            raise AssertionError("archive CRC failure")
    manifest = {"experiment_id": "exp-release-eth-multitf-20260914-v1", "schema_version": 1,
                "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "backtest_builder_commit": "959329b09972cc91305904ae0492792cc1908b54",
                "selection_commit": "b7deadb018", "report_commit": "ecd37ea8ec",
                "holdout_consumed": False, "training_eligible": False, "production_eligible": False,
                "result_rows": audit["result_rows"], "case_trades_with_overlaps": audit["case_trades"],
                "controls_with_overlaps": audit["control_trades"], "all_ledger_and_matching_checks_passed": True,
                "synthetic_compatibility_boundary_tests_passed": 104,
                "html_validation": {"tables": 14, "table_column_mismatches": 0, "embedded_images": 4,
                                    "plots_visually_inspected": 4, "browser_page_visual_qa": False},
                "native_pine_compiled": False, "native_tradingview_ledger_parity": False,
                "funding_fully_included": False, "additional_slippage_included": False,
                "frozen_candidates": {"15m": "C2", "1h": "C3", "4h": "C1"},
                "pine_default": "C0 execution baseline; Auto only reproduces frozen trials",
                "verdict": "15m C2 exploratory, statistical gate failed;1h C3 and4h C1 validation failed",
                "bundle": identity(bundle), "files": [identity(f) for f in files]}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"manifest": identity(manifest_path), "bundle": identity(bundle), "files": len(files)}))


if __name__ == "__main__":
    main()
