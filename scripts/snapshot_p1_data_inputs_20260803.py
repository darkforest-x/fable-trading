#!/usr/bin/env python3
"""Snapshot authoritative P1 inputs without materializing holdout OHLC rows.

The universe is imported from the same loader and stockish gate used by
``scan_forward_records``.  The frozen detector-evaluation ruler is deliberately
not a universe gate: HANDOFF calls it an old-task ruler, and the live proposal
path never imports it.  For each raw candle file, only the exact byte prefix
whose ``open_time`` is strictly before 2026-05-04 UTC is hashed; the first
boundary timestamp stops the scan and its OHLC fields are never materialized.

Writes only ``analysis/output/p1_data_baseline_20260803``.  It does not fetch,
train, score, mutate data/, touch ACTIVE, create a bundle, deploy, or trade.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "analysis/output/p1_data_baseline_20260803"
CUTOFF = datetime.fromisoformat("2026-05-04T00:00:00+00:00")
PROPOSALS = PROJECT / "data/judgment_v10_wide.csv"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=PROJECT, text=True).strip()


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def hash_allowed_prefix(path: Path) -> dict[str, Any]:
    """Hash the header plus rows before cutoff, stopping at the first boundary timestamp."""
    digest = hashlib.sha256()
    count = 0
    first: datetime | None = None
    last: datetime | None = None
    boundary_timestamp_checked = False
    with path.open("rb") as handle:
        header_raw = handle.readline()
        if not header_raw:
            raise ValueError(f"empty candle file: {path}")
        header = next(csv.reader([header_raw.decode("utf-8").rstrip("\r\n")]))
        if "open_time" not in header:
            raise ValueError(f"missing open_time: {path}")
        time_i = header.index("open_time")
        digest.update(header_raw)
        for raw in handle:
            text = raw.decode("utf-8")
            fields = next(csv.reader([text.rstrip("\r\n")]))
            if len(fields) <= time_i:
                raise ValueError(f"short candle row in {path}")
            timestamp = parse_time(fields[time_i])
            if timestamp >= CUTOFF:
                boundary_timestamp_checked = True
                break
            digest.update(raw)
            count += 1
            first = timestamp if first is None else min(first, timestamp)
            last = timestamp if last is None else max(last, timestamp)
    return {
        "path": str(path.relative_to(PROJECT)),
        "source_size_bytes": path.stat().st_size,
        "source_mtime_ns": path.stat().st_mtime_ns,
        "preholdout_prefix_rows": count,
        "preholdout_prefix_first": first.isoformat() if first else None,
        "preholdout_prefix_last": last.isoformat() if last else None,
        "preholdout_prefix_sha256": digest.hexdigest(),
        "boundary_timestamp_checked": boundary_timestamp_checked,
        "post_cutoff_ohlcv_rows_materialized": 0,
    }


def inspect_proposals(path: Path) -> dict[str, Any]:
    count = 0
    symbols: set[str] = set()
    first: datetime | None = None
    last: datetime | None = None
    holdout_rows = 0
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            timestamp = parse_time(row["signal_time"])
            count += 1
            symbols.add(row["symbol"])
            first = timestamp if first is None else min(first, timestamp)
            last = timestamp if last is None else max(last, timestamp)
            holdout_rows += int(timestamp >= CUTOFF)
    return {
        "path": str(path.relative_to(PROJECT)),
        "sha256": sha256(path),
        "row_count": count,
        "symbols_with_fires": len(symbols),
        "first_signal_time": first.isoformat() if first else None,
        "last_signal_time": last.isoformat() if last else None,
        "holdout_rows": holdout_rows,
        "role": "current-v10 causal-tip proposal ledger; signal_i stores proposal window tip and must be remapped",
    }


def write_json(name: str, value: dict[str, Any]) -> None:
    (OUT / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    from src.data.loader import list_series
    from src.data.universe import is_stockish
    from src.detection.owner_eval import is_eval_symbol
    from src.judgment.yolo_candidates import default_weights_label, resolve_default_weights

    branch = git("branch", "--show-current")
    if branch != "main":
        raise SystemExit(f"P1 snapshot requires main, got {branch!r}")
    detector_link = resolve_default_weights()
    detector_resolved = detector_link.resolve()
    if detector_resolved.name != "owner_short_star_v10.pt":
        raise SystemExit(f"unexpected detector resolution: {detector_link} -> {detector_resolved}")

    groups = list_series(bar="15m")
    live_keys = sorted(
        (source, symbol)
        for source, symbol in groups
        if source == "okx" and symbol.endswith("_USDT_SWAP") and not is_stockish(symbol)
    )
    research_keys = list(live_keys)
    if not live_keys:
        raise SystemExit("authoritative universe resolved empty")
    if any(len(groups[key]) != 1 for key in research_keys):
        raise SystemExit("P1 requires one unambiguous raw candle file per research symbol")

    raw_inputs = [hash_allowed_prefix(groups[key][0]) for key in research_keys]
    raw_by_symbol = []
    for (source, symbol), evidence in zip(research_keys, raw_inputs):
        raw_by_symbol.append({"source": source, "symbol": symbol, **evidence})
    combined = hashlib.sha256()
    for item in raw_by_symbol:
        combined.update(item["source"].encode())
        combined.update(b"\0")
        combined.update(item["symbol"].encode())
        combined.update(b"\0")
        combined.update(item["preholdout_prefix_sha256"].encode())
        combined.update(b"\n")

    proposals = inspect_proposals(PROPOSALS)
    if proposals["holdout_rows"] != 0:
        raise SystemExit("proposal ledger contains holdout rows")
    proposal_symbols = set()
    with PROPOSALS.open(newline="", encoding="utf-8") as handle:
        proposal_symbols = {row["symbol"] for row in csv.DictReader(handle)}
    research_symbols = {symbol for _, symbol in research_keys}
    if not proposal_symbols.issubset(research_symbols):
        raise SystemExit("proposal symbols do not fit the authoritative live universe")

    # The legacy v10 ledger was built on a Windows/remote tree where the frozen
    # eval manifest was absent: its proposal symbols intersect the SHA1 fallback
    # eval set at exactly zero, but 27 are members of today's materialized ruler.
    # This proves the old builder's effective exclusion without promoting that
    # detector-evaluation split into a live-universe authority.
    fallback_eval_symbols = {
        symbol
        for _, symbol in live_keys
        if int(hashlib.sha1(symbol.encode()).hexdigest(), 16) % 7 == 0
    }
    materialized_eval_symbols = {
        symbol for _, symbol in live_keys if is_eval_symbol(symbol)
    }

    source_files = [
        "src/data/loader.py",
        "src/data/universe.py",
        "src/detection/owner_eval.py",
        "src/detection/render.py",
        "src/judgment/yolo_candidates.py",
        "src/judgment/features.py",
        "src/judgment/outcomes.py",
        "src/judgment/labeling.py",
        "src/costs.py",
        "scripts/dump_v9_candidates_dual_label.py",
    ]
    source_hashes = {path: sha256(PROJECT / path) for path in source_files}
    protected_paths = ["models/ACTIVE", "data/forward_log.csv", "data/executor_ledger.jsonl"]
    protected = {path: sha256(PROJECT / path) for path in protected_paths}

    environment = {
        "snapshot_version": "p1_data_baseline_20260803",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git("rev-parse", "HEAD"),
        "branch": branch,
        "origin_main": git("rev-parse", "origin/main"),
        "python_executable": sys.executable,
        "python_version": sys.version,
        "os": platform.platform(),
        "packages": {
            name: package_version(name)
            for name in ("torch", "torchvision", "ultralytics", "lightgbm", "pandas", "numpy")
        },
        "holdout_cutoff": CUTOFF.isoformat(),
        "detector": {
            "configured_path": str(detector_link.relative_to(PROJECT)),
            "symlink_target": os.readlink(detector_link) if detector_link.is_symlink() else None,
            "resolved_path": str(detector_resolved.relative_to(PROJECT)),
            "label": default_weights_label(detector_link),
            "sha256": sha256(detector_resolved),
        },
        "universe": {
            "live_rule": "list_series(15m) + source=okx + *_USDT_SWAP + !is_stockish",
            "live_symbol_count": len(live_keys),
            "research_rule": "same 344-symbol universe as scan_forward_records; owner_eval is not a live gate",
            "research_symbol_count": len(research_keys),
            "frozen_eval_excluded_count": 0,
            "old_task_eval_ruler_symbol_count": len(materialized_eval_symbols),
            "conflicting_authorities": [],
            "authority_evidence": {
                "runtime_source": "src/judgment/forward_scan.py::scan_forward_records",
                "handoff_statement": "owner_eval_frozen is an old-task ruler, reference only, not a tip decision",
                "legacy_ledger_fallback_eval_count": len(fallback_eval_symbols),
                "legacy_ledger_intersection_with_fallback_eval": len(
                    proposal_symbols & fallback_eval_symbols
                ),
                "legacy_ledger_intersection_with_materialized_eval": len(
                    proposal_symbols & materialized_eval_symbols
                ),
                "legacy_ledger_role": "audit reference only; not universe authority",
            },
        },
        "safety": {
            "post_cutoff_ohlcv_rows_materialized": sum(
                item["post_cutoff_ohlcv_rows_materialized"] for item in raw_by_symbol
            ),
            "trained": False,
            "threshold_changed": False,
            "active_bundle_created": (PROJECT / "models/active_bundle.json").exists(),
            "deployed": False,
            "order_triggered": False,
        },
    }
    raw_manifest = {
        "manifest_version": "p1_raw_inputs_v1",
        "holdout_cutoff": CUTOFF.isoformat(),
        "combined_preholdout_prefix_sha256": combined.hexdigest(),
        "live_symbols": [symbol for _, symbol in live_keys],
        "research_symbols": [symbol for _, symbol in research_keys],
        "proposal_symbols": sorted(proposal_symbols),
        "raw_inputs": raw_by_symbol,
        "proposals": proposals,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    write_json("environment.json", environment)
    write_json("raw_inputs.json", raw_manifest)
    write_json("source_hashes.json", {"git_commit": environment["git_commit"], "files": source_hashes})
    write_json("protected_hashes.json", protected)
    print(
        json.dumps(
            {
                "out": str(OUT.relative_to(PROJECT)),
                "live_symbols": len(live_keys),
                "research_symbols": len(research_keys),
                "proposal_rows": proposals["row_count"],
                "proposal_symbols": proposals["symbols_with_fires"],
                "raw_prefix_hash": combined.hexdigest(),
                "post_cutoff_ohlcv_rows_materialized": 0,
                "detector_sha256": environment["detector"]["sha256"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
