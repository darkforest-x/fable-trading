"""Derive auditable UTC-aligned 2m MA-profit inputs from frozen 1m CSVs.

This deliberately prepares sources only.  It neither scans events nor changes
the current 1m queue, plan, eligibility, labels, or training configuration.
Every two-minute bar delegates its OHLCV calculation to the established
``ma_snapshot_inputs.aggregate`` implementation and drops incomplete buckets.
"""
from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any, Mapping

import numpy as np
import pandas as pd

from yoyo.data.ma_snapshot_inputs import aggregate
from yoyo.datasets.ma_profit_pipeline import ROOT


AGGREGATE_PATH = ROOT / "yoyo/data/ma_snapshot_inputs.py"
REQUIRED_COLUMNS = ("ts", "open", "high", "low", "close", "volume")
QUALITY_RULES = {
    "utc_epoch_aligned": True,
    "strictly_monotonic_unique_parent_minutes": True,
    "finite_positive_ohlc": True,
    "finite_nonnegative_volume": True,
    "valid_ohlc_geometry": True,
    "incomplete_two_minute_buckets": "drop_without_fill",
    "derived_time_gaps": "preserve_for_known_input_gate",
}


class TwoMinuteSourceError(ValueError):
    """A proposed two-minute source lacks immutable parent lineage or quality."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dump(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n")


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _path(value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else ROOT / path


def _utc_cutoff(value: object) -> tuple[str, int]:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise TwoMinuteSourceError("cutoff must be explicit UTC")
    stamp = stamp.tz_convert("UTC")
    millis = int(stamp.value // 1_000_000)
    if millis % 60_000:
        raise TwoMinuteSourceError("cutoff must be UTC minute aligned")
    return stamp.isoformat(), millis


def _read_contract(path: Path, sources_path: Path) -> tuple[dict[str, Any], dict[str, Path], int]:
    try:
        contract = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise TwoMinuteSourceError("invalid two-minute contract") from exc
    if not isinstance(contract, dict) or contract.get("schema_version") != 1:
        raise TwoMinuteSourceError("two-minute contract schema must be 1")
    if contract.get("interval") != {"source_minutes": 1, "target_minutes": 2}:
        raise TwoMinuteSourceError("contract must bind a 1m-to-2m interval")
    if contract.get("quality_rules") != QUALITY_RULES:
        raise TwoMinuteSourceError("contract quality/boundary rules drift")
    if contract.get("training_eligible") is not False or contract.get("production_eligible") is not False:
        raise TwoMinuteSourceError("two-minute source eligibility must remain false")
    if contract.get("source_manifest_sha256") != sha256(sources_path):
        raise TwoMinuteSourceError("contract source manifest SHA drift")
    if contract.get("aggregation_code_sha256") != sha256(AGGREGATE_PATH):
        raise TwoMinuteSourceError("contract aggregate implementation SHA drift")
    required = {"original_plan_path": "original_plan_sha256", "parent_extension_1m_path": "parent_extension_1m_sha256"}
    bound: dict[str, Path] = {}
    for path_key, sha_key in required.items():
        if not contract.get(path_key) or not contract.get(sha_key):
            raise TwoMinuteSourceError(f"contract lacks {path_key} binding")
        candidate = _path(contract[path_key])
        if not candidate.exists() or sha256(candidate) != contract[sha_key]:
            raise TwoMinuteSourceError(f"contract {path_key} SHA drift")
        bound[path_key] = candidate
    cutoff_utc, cutoff_ms = _utc_cutoff(contract.get("cutoff_utc"))
    if contract.get("cutoff_utc") != cutoff_utc:
        raise TwoMinuteSourceError("contract cutoff must use canonical UTC ISO form")
    try:
        plan = json.loads(bound["original_plan_path"].read_text())
        plan_cutoff, plan_cutoff_ms = _utc_cutoff(plan["discovery"]["data_end_exclusive"])
        extension = json.loads(bound["parent_extension_1m_path"].read_text())
        parent_cutoff, parent_cutoff_ms = _utc_cutoff(extension["archive_max_exclusive"])
    except (KeyError, OSError, ValueError, TypeError) as exc:
        raise TwoMinuteSourceError("original plan or parent 1m extension lacks a valid UTC cutoff") from exc
    if parent_cutoff_ms > plan_cutoff_ms:
        raise TwoMinuteSourceError("parent 1m archive cutoff exceeds original plan data end")
    if extension.get("source_plan_sha256") != sha256(bound["original_plan_path"]):
        raise TwoMinuteSourceError("parent 1m extension original-plan SHA drift")
    if parent_cutoff != cutoff_utc:
        raise TwoMinuteSourceError("contract cutoff differs from frozen 1m extension cutoff")
    requested = contract.get("requested_source_paths")
    if not isinstance(requested, list) or not requested or any(not isinstance(item, str) for item in requested):
        raise TwoMinuteSourceError("contract must name every requested 1m source")
    if len(requested) != len(set(requested)) or contract.get("requested_source_count") != len(requested):
        raise TwoMinuteSourceError("contract requested source collection is not unique/complete")
    return contract, bound, cutoff_ms


def _sources(path: Path, contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise TwoMinuteSourceError("invalid frozen 1m source manifest") from exc
    rows = payload.get("sources") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise TwoMinuteSourceError("frozen 1m source manifest lacks sources")
    if isinstance(payload, dict) and payload.get("source_coverage_complete") is not True:
        raise TwoMinuteSourceError("frozen 1m source manifest coverage is not complete")
    requested = set(contract["requested_source_paths"])
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise TwoMinuteSourceError("source manifest has non-object entry")
        source = str(raw.get("source_path", ""))
        expected_sha = raw.get("sha256", raw.get("csv_sha256"))
        if not source or not isinstance(expected_sha, str) or int(raw.get("bar_minutes", -1)) != 1:
            raise TwoMinuteSourceError("source manifest entry is not a pinned 1m source")
        if any(not isinstance(raw.get(field), str) or not raw[field].strip() for field in ("symbol", "venue")):
            raise TwoMinuteSourceError("source manifest entry lacks a non-empty symbol/venue identity")
        if source.endswith(".gz") or not source.endswith(".csv"):
            raise TwoMinuteSourceError("two-minute parent must be a plain CSV")
        if source in seen:
            raise TwoMinuteSourceError("source manifest has duplicate source path")
        seen.add(source)
        result.append({**dict(raw), "source_path": source, "sha256": expected_sha})
    if seen != requested:
        raise TwoMinuteSourceError("source manifest does not exactly cover the contract request collection")
    return sorted(result, key=lambda row: row["source_path"])


def _parent_frame(path: Path, cutoff_ms: int) -> pd.DataFrame:
    try:
        # Preserve the raw epoch token: float inference can erase a fractional
        # millisecond before the exact Decimal validation below sees it.
        frame = pd.read_csv(path, usecols=list(REQUIRED_COLUMNS), dtype={"ts": str})
    except (OSError, ValueError) as exc:
        raise TwoMinuteSourceError(f"invalid parent CSV: {path}") from exc
    if frame.empty:
        raise TwoMinuteSourceError("parent CSV has no rows")
    try:
        timestamps: list[int] = []
        low, high = np.iinfo(np.int64).min, np.iinfo(np.int64).max
        for raw in frame["ts"].tolist():
            epoch = Decimal(str(raw))
            if not epoch.is_finite() or epoch != epoch.to_integral_value():
                raise TwoMinuteSourceError("parent timestamps must be finite integer epoch milliseconds")
            parsed = int(epoch)
            if parsed < low or parsed > high:
                raise TwoMinuteSourceError("parent timestamp exceeds int64 epoch range")
            timestamps.append(parsed)
        frame["ts"] = np.asarray(timestamps, dtype=np.int64)
        for column in REQUIRED_COLUMNS[1:]:
            frame[column] = pd.to_numeric(frame[column], errors="raise")
    except TwoMinuteSourceError:
        raise
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TwoMinuteSourceError("parent CSV has nonnumeric OHLCV") from exc
    ts = frame["ts"].to_numpy(dtype="int64")
    values = frame[["open", "high", "low", "close", "volume"]].to_numpy(dtype=float)
    if np.any(ts % 60_000) or np.any(np.diff(ts) <= 0) or np.any(ts + 60_000 > cutoff_ms):
        raise TwoMinuteSourceError("parent UTC minute ordering/alignment/cutoff invalid")
    if not np.isfinite(values).all() or np.any(values[:, :4] <= 0) or np.any(values[:, 4] < 0):
        raise TwoMinuteSourceError("parent OHLCV finite/positive/nonnegative validation failed")
    if np.any(values[:, 1] < np.maximum(values[:, 0], values[:, 3])) or np.any(values[:, 2] > np.minimum(values[:, 0], values[:, 3])):
        raise TwoMinuteSourceError("parent OHLC geometry invalid")
    frame.insert(1, "open_time", pd.to_datetime(frame["ts"], unit="ms", utc=True))
    return frame[["ts", "open_time", "open", "high", "low", "close", "volume"]]


def _incomplete_buckets(frame: pd.DataFrame) -> dict[str, Any]:
    duration = 120_000
    buckets = (frame.ts.to_numpy(dtype="int64") // duration) * duration
    counts = Counter(int(bucket) for bucket in buckets)
    first, last = int(buckets[0]), int(buckets[-1])
    entries = [(bucket, counts.get(bucket, 0)) for bucket in range(first, last + duration, duration) if counts.get(bucket, 0) != 2]
    # Adjacent equally incomplete buckets are compacted, so a long source gap
    # remains explicit without creating a million-row receipt.
    ranges: list[dict[str, Any]] = []
    for bucket, observed in entries:
        if ranges and ranges[-1]["observed_parent_minutes"] == observed and ranges[-1]["end_epoch_ms"] + duration == bucket:
            ranges[-1]["end_epoch_ms"] = bucket
            ranges[-1]["buckets"] += 1
        else:
            ranges.append({"start_epoch_ms": bucket, "end_epoch_ms": bucket, "buckets": 1,
                           "observed_parent_minutes": observed})
    incomplete = {bucket for bucket, _ in entries}
    leading = int(first in incomplete)
    trailing = int(last in incomplete)
    edge_buckets = len({bucket for bucket in (first, last) if bucket in incomplete})
    return {"dropped_incomplete_bucket_count": len(entries), "incomplete_bucket_ranges": ranges,
            "leading_partial_bucket_count": leading, "trailing_partial_bucket_count": trailing,
            "internal_incomplete_bucket_count": len(entries) - edge_buckets}


def _derived_gaps(frame: pd.DataFrame) -> int:
    if len(frame) < 2:
        return 0
    return int(np.count_nonzero(np.diff(frame.ts.to_numpy(dtype="int64")) != 120_000))


def _tracked_clean_commit(path: Path) -> str:
    """Return the commit for one required tracked file; ignored files cannot pass."""

    path = Path(path).resolve()
    try:
        relative = str(path.relative_to(ROOT))
    except ValueError as exc:
        raise TwoMinuteSourceError(f"frozen input escapes repository: {path}") from exc
    if not path.is_file():
        raise TwoMinuteSourceError(f"missing frozen input: {relative}")
    try:
        subprocess.check_output(["git", "ls-files", "--error-unmatch", "--", relative], cwd=ROOT, text=True)
    except subprocess.CalledProcessError as exc:
        raise TwoMinuteSourceError(f"frozen input is untracked or ignored: {relative}") from exc
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--", relative], cwd=ROOT, text=True)
    if dirty.strip():
        raise TwoMinuteSourceError(f"frozen input is not clean: {relative}")
    commit = subprocess.check_output(["git", "log", "-1", "--format=%H", "--", relative], cwd=ROOT, text=True).strip()
    if not commit:
        raise TwoMinuteSourceError(f"frozen input has no commit: {relative}")
    return commit


def formal_guard(contract: Path, sources: Path, bound: Mapping[str, Path]) -> str:
    """Require tracked, clean semantic code and frozen JSON inputs on ``main``."""

    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
    if branch != "main":
        raise TwoMinuteSourceError("main required for formal two-minute source preparation")
    for required in (Path(__file__), AGGREGATE_PATH, contract, sources, *bound.values()):
        _tracked_clean_commit(required)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def build(contract_path: Path, sources_path: Path, output: Path) -> dict[str, Any]:
    """Materialize one deterministic 2m CSV at a time and return a batch receipt."""

    contract_path, sources_path, output = (Path(value).resolve() for value in (contract_path, sources_path, output))
    if output.exists():
        raise FileExistsError(f"refusing to overwrite two-minute output: {output}")
    contract, bound, cutoff_ms = _read_contract(contract_path, sources_path)
    rows = _sources(sources_path, contract)
    builder_commit = formal_guard(contract_path, sources_path, bound)
    output.mkdir(parents=True)
    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for item in rows:
        parent = _path(item["source_path"])
        try:
            if not parent.is_file() or sha256(parent) != item["sha256"]:
                raise TwoMinuteSourceError("parent source SHA drift or missing CSV")
            frame = _parent_frame(parent, cutoff_ms)
            incomplete = _incomplete_buckets(frame)
            derived = aggregate(frame, 1, 2, cutoff_ms)
            if derived.empty:
                raise TwoMinuteSourceError("parent has no complete two-minute bucket")
            destination = output / "series" / (parent.stem + "_2m.csv")
            if destination.exists():
                raise FileExistsError(f"refusing to overwrite derived CSV: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            derived.to_csv(destination, index=False, lineterminator="\n", float_format="%.17g", date_format="%Y-%m-%dT%H:%M:%S%z")
            identity = {key: item[key] for key in ("symbol", "venue", "market", "asset", "canonical_asset") if key in item}
            results.append({**identity,
                "source_path": _relative(destination), "sha256": sha256(destination), "bar_minutes": 2,
                "parent_source_path": item["source_path"], "parent_source_sha256": item["sha256"],
                "parent_source_metadata": item,
                "aggregation_code_sha256": sha256(AGGREGATE_PATH), "contract_sha256": sha256(contract_path),
                "rows": len(derived), "first_time": None if derived.empty else pd.Timestamp(derived.ts.iloc[0], unit="ms", tz="UTC").isoformat(),
                "last_time": None if derived.empty else pd.Timestamp(derived.ts.iloc[-1], unit="ms", tz="UTC").isoformat(),
                "derived_non_bar_gaps": _derived_gaps(derived), **incomplete})
        except (OSError, ValueError, TwoMinuteSourceError) as exc:
            failures.append({"parent_source_path": item["source_path"], "reason": str(exc)})
    receipt = {"schema_version": 1, "builder_commit": builder_commit, "builder_sha256": sha256(Path(__file__)),
        "aggregation_code_sha256": sha256(AGGREGATE_PATH), "contract_path": _relative(contract_path),
        "contract_sha256": sha256(contract_path), "parent_source_manifest_path": _relative(sources_path),
        "parent_source_manifest_sha256": sha256(sources_path), "cutoff_utc": contract["cutoff_utc"],
        "interval": {"source_minutes": 1, "target_minutes": 2}, "requested_sources": len(rows),
        "verified_sources": len(results), "failed_sources": len(failures), "source_coverage_complete": not failures,
        "batch_gate_open": not failures and len(results) == len(rows), "training_eligible": False,
        "production_eligible": False, "sources": results, "failures": failures}
    _dump(output / "derived_sources.json", receipt)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.contract, args.sources, args.out), sort_keys=True))


if __name__ == "__main__":
    main()
