"""Build canonical 30m/60m/240m Profit3R source batches from archived 30m bars.

The archive is read once per venue-market source with the timestamp-first
reader used by the historical snapshot builder.  All rows closed before the
Profit3R plan cutoff are retained; 60m and 240m bars are emitted only for full
UTC-aligned buckets.  This creates discovery inputs and provenance manifests,
not signals, labels, or training data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

from yoyo.data.ma_snapshot_inputs import ROOT, aggregate, bounded_local


EXPERIMENT_ID = "exp-ma-profit3r-20260922-v1"
ARCHIVE_ROOT = ROOT / "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/data/normalized"
TARGET_MINUTES = (30, 60, 240)
WARMUP_BARS = 1200


class ProfitHighSourceError(RuntimeError):
    """Raised for an invalid high-timeframe source build contract."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError as exc:
        raise ProfitHighSourceError(f"path escapes repository: {path}") from exc


def _write_json(path: Path, payload: object) -> None:
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _canonical_csv_bytes(frame: pd.DataFrame) -> bytes:
    """Serialize the public canonical columns identically for hash and output."""

    columns = ["ts", "open_time", "open", "high", "low", "close", "volume"]
    return frame.loc[:, columns].to_csv(index=False, lineterminator="\n").encode("utf-8")


def _write_immutable_csv(path: Path, frame: pd.DataFrame) -> str:
    payload = _canonical_csv_bytes(frame)
    digest = hashlib.sha256(payload).hexdigest()
    if path.exists():
        if _sha256(path) != digest:
            raise ProfitHighSourceError(f"existing canonical output SHA drift: {path}")
        return digest
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
    return digest


def _market_identity(path: Path, venue: str) -> tuple[str, str]:
    suffix = "_30m.csv.gz"
    if not path.name.endswith(suffix):
        raise ProfitHighSourceError(f"unexpected archive filename: {path.name}")
    market = path.name[: -len(suffix)]
    if venue == "binance" and market.endswith("USDT"):
        return market[:-4], market
    if venue == "okx" and market.endswith("-USDT-SWAP"):
        return market.removesuffix("-USDT-SWAP"), market
    raise ProfitHighSourceError(f"unsupported {venue} perpetual archive name: {path.name}")


def _gap_count(frame: pd.DataFrame, minutes: int) -> int:
    if len(frame) < 2:
        return 0
    expected = int(minutes) * 60_000
    return int((frame["ts"].diff().iloc[1:] != expected).sum())


def _time_range(frame: pd.DataFrame, minutes: int) -> dict[str, str | None]:
    if frame.empty:
        return {"first_open_utc": None, "last_close_utc": None}
    return {
        "first_open_utc": pd.Timestamp(int(frame["ts"].iloc[0]), unit="ms", tz="UTC").isoformat(),
        "last_close_utc": pd.Timestamp(
            int(frame["ts"].iloc[-1]) + int(minutes) * 60_000, unit="ms", tz="UTC"
        ).isoformat(),
    }


def _status_for_rows(rows: int) -> str:
    if rows == 0:
        return "empty"
    return "ready" if rows >= WARMUP_BARS else "insufficient_warmup"


def build_archive_source(
    archive_path: Path, *, venue: str, cutoff_ms: int, output_dir: Path
) -> dict[str, Any]:
    """Read one compressed 30m archive once and derive every requested timeframe."""

    asset, market = _market_identity(archive_path, venue)
    original_sha = _sha256(archive_path)
    frame, reader_audit = bounded_local(archive_path, 30, cutoff_ms, 0)
    normalized_sha = hashlib.sha256(_canonical_csv_bytes(frame)).hexdigest()
    base = {
        "archive_path": _relative(archive_path),
        "original_sha256": original_sha,
        "normalized_30m_sha256": normalized_sha,
        "venue": venue,
        "market": market,
        "symbol": asset,
        "reader_audit": reader_audit,
        "source_30m_rows": len(frame),
        "source_30m_gap_count": _gap_count(frame, 30),
        **_time_range(frame, 30),
    }
    if frame.empty:
        return {"status": "empty", "base": base, "sources": []}

    records: list[dict[str, Any]] = []
    for minutes in TARGET_MINUTES:
        canonical = aggregate(frame, 30, minutes, cutoff_ms)
        destination = output_dir / f"{venue}_{market}_{minutes}m.csv"
        canonical_sha = _write_immutable_csv(destination, canonical)
        status = _status_for_rows(len(canonical))
        records.append({
            **base,
            "source_path": _relative(destination),
            "sha256": canonical_sha,
            "canonical_sha256": canonical_sha,
            "bar_minutes": minutes,
            "rows": len(canonical),
            "gap_count": _gap_count(canonical, minutes),
            "warmup_bars_required": WARMUP_BARS,
            "warmup_bars_available": len(canonical),
            "status": status,
            "coverage": "archive_full_before_cutoff",
            **_time_range(canonical, minutes),
        })
    return {"status": "materialized", "base": base, "sources": records}


def _worker(archive_path: str, venue: str, cutoff_ms: int, output_dir: str) -> dict[str, Any]:
    """Pickleable per-gzip worker; each archive is decompressed exactly once."""

    path = Path(archive_path)
    base: dict[str, Any] = {"archive_path": _relative(path), "venue": venue}
    try:
        asset, market = _market_identity(path, venue)
        base.update({"symbol": asset, "market": market, "original_sha256": _sha256(path)})
        return build_archive_source(path, venue=venue, cutoff_ms=cutoff_ms, output_dir=Path(output_dir))
    except Exception as exc:
        return {
            "status": "failed",
            "base": base,
            "sources": [],
            "error_type": type(exc).__name__,
            "error": str(exc),
        }


def _archive_tasks() -> list[tuple[Path, str]]:
    tasks: list[tuple[Path, str]] = []
    for venue in ("binance", "okx"):
        tasks.extend((path, venue) for path in sorted((ARCHIVE_ROOT / venue).glob("*.csv.gz")))
    if not tasks:
        raise ProfitHighSourceError(f"no normalized archives found under {ARCHIVE_ROOT}")
    return tasks


def build(plan_path: Path, *, workers: int = 4) -> dict[str, Any]:
    """Create all high-timeframe inputs and one miner-compatible manifest per timeframe."""

    if not 1 <= workers <= 4:
        raise ProfitHighSourceError("workers must be between 1 and 4")
    plan_path = plan_path.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("experiment_id") != EXPERIMENT_ID:
        raise ProfitHighSourceError("wrong experiment plan")
    cutoff = pd.Timestamp(plan["discovery"]["data_end_exclusive"])
    if cutoff.tzinfo is None:
        raise ProfitHighSourceError("plan cutoff must include timezone")
    cutoff_ms = int(cutoff.tz_convert("UTC").value // 1_000_000)
    experiment_dir = plan_path.parent
    output_dir = experiment_dir / "inputs/high"
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks = _archive_tasks()
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker, str(path), venue, cutoff_ms, str(output_dir)) for path, venue in tasks]
        for index, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if index == 1 or index % 100 == 0 or index == len(tasks):
                print(f"high-source archives completed {index}/{len(tasks)}", flush=True)

    records = [record for result in results for record in result["sources"]]
    failures = [
        {"status": result["status"], **result["base"], **({"error_type": result["error_type"], "error": result["error"]} if result["status"] == "failed" else {})}
        for result in results if result["status"] != "materialized"
    ]
    plan_sha = _sha256(plan_path)
    builder_sha = _sha256(Path(__file__))
    manifest_paths: dict[str, str] = {}
    for minutes in TARGET_MINUTES:
        rows = sorted(
            [record for record in records if int(record["bar_minutes"]) == minutes],
            key=lambda row: (row["symbol"], row["venue"], row["market"]),
        )
        manifest = {
            "schema_version": 1,
            "experiment_id": EXPERIMENT_ID,
            "plan_sha256": plan_sha,
            "builder_sha256": builder_sha,
            "bar_minutes": minutes,
            "sources": rows,
            "failed_or_empty_archives": failures,
            "source_count": len(rows),
            "ready_count": sum(row["status"] == "ready" for row in rows),
            "insufficient_warmup_count": sum(row["status"] == "insufficient_warmup" for row in rows),
            "failure_count": len(failures),
            "fallback": "none; malformed/empty archive inputs are reported, never synthesized",
        }
        manifest_path = experiment_dir / f"sources_high_{minutes}m.json"
        _write_json(manifest_path, manifest)
        manifest_paths[str(minutes)] = _relative(manifest_path)
    summary = {
        "experiment_id": EXPERIMENT_ID,
        "archive_tasks": len(tasks),
        "materialized_archives": sum(result["status"] == "materialized" for result in results),
        "failed_or_empty_archives": len(failures),
        "manifest_paths": manifest_paths,
        "output_dir": _relative(output_dir),
        "plan_sha256": plan_sha,
        "builder_sha256": builder_sha,
    }
    _write_json(experiment_dir / "high_source_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(build(args.plan, workers=args.workers), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
