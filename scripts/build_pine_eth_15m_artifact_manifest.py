#!/usr/bin/env python3
"""Build or verify the complete ETH 15m research artifact hash manifest.

The manifest covers the frozen configuration, Pine surfaces, TradingView
reconciliation contract, generated result tables/charts, report, HTML and
executed notebook. It intentionally excludes raw market data, model paths,
ACTIVE state and itself. This utility never reads holdout data, fits a model,
changes a strategy parameter or touches a production surface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Iterable


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
OUTPUT = RESULTS / "artifact_manifest.json"

FIXED_FILES = (
    EXPERIMENT / "README.md",
    EXPERIMENT / "config.json",
    EXPERIMENT / "docker/Dockerfile",
    EXPERIMENT / "docker/requirements.txt",
    EXPERIMENT / "pine/allin_eth_15m_v9_research.pine",
    EXPERIMENT / "pine/allin_eth_15m_v10_volume_paper.pine",
    EXPERIMENT / "pine/allin_eth_15m_v11_long_only_paper.pine",
    EXPERIMENT / "pine/paper_variants_manifest.json",
    EXPERIMENT / "tradingview/README.md",
    EXPERIMENT / "tradingview/trades_normalized.template.csv",
    EXPERIMENT / "judgment/README.md",
    EXPERIMENT / "judgment/gate_manifest.template.json",
    EXPERIMENT / "judgment/judgment_scores.template.csv",
    EXPERIMENT / "notebooks/pine_eth_15m_v1_audit.ipynb",
    PROJECT / "analysis/p0_pine_eth_15m_v1_20260821.md",
    PROJECT / "analysis/html/p0_pine_eth_15m_v1_20260821.html",
)

FORBIDDEN_PARTS = {
    "data",
    "models",
    "ACTIVE",
    "active_bundle.json",
    "forward_log.csv",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "UNAVAILABLE"


def collect_paths() -> list[Path]:
    """Collect all declared artifacts without following directory symlinks."""

    result_files = [
        path
        for path in RESULTS.rglob("*")
        if path.is_file() and path.resolve() != OUTPUT.resolve()
    ]
    return sorted({*FIXED_FILES, *result_files}, key=lambda path: str(path))


def safe_relative_path(path: Path, *, project: Path = PROJECT) -> str:
    """Resolve one manifest path and reject production/raw-data surfaces."""

    if path.is_symlink():
        raise ValueError(f"manifest refuses symlinks: {path}")
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(project.resolve())
    except ValueError as exc:
        raise ValueError(f"artifact is outside repository: {path}") from exc
    if any(part in FORBIDDEN_PARTS for part in relative.parts):
        raise ValueError(f"forbidden artifact path: {relative}")
    try:
        manifest_relative = OUTPUT.relative_to(project)
    except ValueError:
        manifest_relative = Path("__outside_project__")
    if relative == manifest_relative:
        raise ValueError("artifact manifest cannot hash itself")
    return relative.as_posix()


def build_entries(paths: Iterable[Path], *, project: Path = PROJECT) -> list[dict[str, Any]]:
    entries = []
    seen: set[str] = set()
    for path in paths:
        relative = safe_relative_path(path, project=project)
        if relative in seen:
            raise ValueError(f"duplicate artifact path: {relative}")
        seen.add(relative)
        entries.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return sorted(entries, key=lambda row: row["path"])


def verify_entries(
    entries: Iterable[dict[str, Any]], *, project: Path = PROJECT
) -> dict[str, Any]:
    missing = []
    size_mismatch = []
    hash_mismatch = []
    unsafe = []
    entries = list(entries)
    for row in entries:
        relative = Path(str(row["path"]))
        path = project / relative
        try:
            actual_relative = safe_relative_path(path, project=project)
        except (FileNotFoundError, ValueError) as exc:
            if not path.exists():
                missing.append(relative.as_posix())
            else:
                unsafe.append({"path": relative.as_posix(), "error": str(exc)})
            continue
        if actual_relative != relative.as_posix():
            unsafe.append(
                {"path": relative.as_posix(), "error": "non-canonical relative path"}
            )
            continue
        actual_size = path.stat().st_size
        if actual_size != int(row["bytes"]):
            size_mismatch.append(
                {"path": relative.as_posix(), "expected": row["bytes"], "actual": actual_size}
            )
        actual_hash = sha256_file(path)
        if actual_hash != row["sha256"]:
            hash_mismatch.append(
                {"path": relative.as_posix(), "expected": row["sha256"], "actual": actual_hash}
            )
    passed = not (missing or size_mismatch or hash_mismatch or unsafe)
    return {
        "status": "pass" if passed else "fail",
        "artifact_count": len(entries),
        "missing": missing,
        "size_mismatch": size_mismatch,
        "hash_mismatch": hash_mismatch,
        "unsafe": unsafe,
    }


def build_manifest() -> dict[str, Any]:
    entries = build_entries(collect_paths())
    required = {
        "analysis/p0_pine_eth_15m_v1_20260821.md",
        "analysis/html/p0_pine_eth_15m_v1_20260821.html",
        "experiments/active/exp-pine-eth-15m-v1/pine/allin_eth_15m_v9_research.pine",
        "experiments/active/exp-pine-eth-15m-v1/results/validation.json",
        "experiments/active/exp-pine-eth-15m-v1/results/density_overlap_audit.json",
        "experiments/active/exp-pine-eth-15m-v1/results/judgment_gate_replay_contract.json",
        "experiments/active/exp-pine-eth-15m-v1/results/tradingview_compile_receipt.json",
    }
    present = {row["path"] for row in entries}
    absent = sorted(required - present)
    if absent:
        raise RuntimeError(f"required artifacts missing from manifest: {absent}")
    payload = {
        "artifact": "ETH 15m Pine complete research artifact manifest",
        "format_version": 1,
        "source_commit": repository_head(),
        "artifact_count": len(entries),
        "total_bytes": int(sum(int(row["bytes"]) for row in entries)),
        "files": entries,
        "scope": (
            "configuration, Pine surfaces, bounded result artifacts, charts, report, "
            "HTML, notebook and TradingView reconciliation contract"
        ),
        "excluded": [
            "raw market data",
            "models/ACTIVE and active_bundle.json",
            "forward_log.csv",
            "artifact_manifest.json itself",
        ],
        "holdout_consumed": False,
        "training_eligible": False,
        "production_eligible": False,
        "tradingview_parity_passed": False,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        manifest = json.loads(OUTPUT.read_text(encoding="utf-8"))
        result = verify_entries(manifest["files"])
        result["manifest_source_commit"] = manifest["source_commit"]
        result["holdout_consumed"] = manifest["holdout_consumed"]
        print(json.dumps(result, indent=2))
        return 0 if result["status"] == "pass" else 1
    payload = build_manifest()
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "source_commit": payload["source_commit"],
                "artifact_count": payload["artifact_count"],
                "total_bytes": payload["total_bytes"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
