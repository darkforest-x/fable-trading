"""Freeze research-only OKX 15m history without changing canonical market data.

The 54-instrument universe is inherited from its saved source manifest, never
selected by current returns. SOPH and USELESS are separately tagged owner
illustrations. Each symbol merges explicitly named deep/fetched/recent CSVs;
overlapping OHLCV must agree (relative tolerance 1e-12, no absolute tolerance).
Conflicting quotes, invalid geometry, missing bars and an incomplete requested
tail reject that whole symbol. No source wins a disagreement and no bar is filled.

``src.data.fetch_okx`` skips API rows with the string confirm="0", but accepts
missing/other flags and does not retain confirm in its CSV. These legacy files
therefore cannot prove exchange confirmation. This builder independently checks
that the UTC cutoff is an already closed 15m boundary and retains only candles
whose close is <= cutoff. If a source carries confirm, retained rows must be "1".
Source-file SHA256 covers the original bytes; output SHA256 covers bounded merged
CSV bytes. No feature, label, exchange request, credentials or trading action is
part of this module. CLI construction requires this builder to be committed first.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
STEP_MS = 15 * 60 * 1000
OHLCV = ["open", "high", "low", "close", "volume"]
OUTPUT_COLUMNS = ["ts", *OHLCV, "open_time"]
ILLUSTRATIONS = ("SOPH", "USELESS")
OVERLAP_RTOL = 1e-12


class HistoryValidationError(ValueError):
    """A rejected symbol carries structured diagnostics for the manifest."""

    def __init__(self, reason: str, audit: dict[str, Any]):
        super().__init__(reason)
        self.audit = {**audit, "status": "rejected", "reason": reason}


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def utc_cutoff(end: object, *, now: object | None = None) -> pd.Timestamp:
    """Require an explicit UTC, aligned, already-closed exclusive boundary."""
    stamp = pd.Timestamp(end)
    if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError("end must be an explicit UTC timestamp")
    stamp = stamp.tz_convert("UTC")
    current = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    if current.tzinfo is None:
        raise ValueError("now must be timezone aware")
    if stamp != stamp.floor("15min") or stamp > current.tz_convert("UTC").floor("15min"):
        raise ValueError("end must be an already closed 15m boundary")
    return stamp


def _symbol(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Z0-9]{1,30}", value) is None:
        raise ValueError("symbol must be an uppercase OKX base identity")
    return value


def _iso(timestamp_ms: int) -> str:
    return pd.Timestamp(int(timestamp_ms), unit="ms", tz="UTC").isoformat()


def merge_symbol(
    symbol: str,
    source_paths: Sequence[Path],
    *,
    end: object,
    expected_sha256: Mapping[str, str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return continuous bounded OHLCV and provenance; never write source files.

    ``source_paths`` explicitly orders identical-overlap representation only;
    order cannot resolve conflicting values. Expected hashes use absolute paths.
    Required source columns are ts (UTC open milliseconds) and OHLCV. An optional
    open_time column must agree with ts; optional confirm must be 1 before cutoff.
    Only retained candles' price/volume fields are validated and materialized.
    """
    symbol = _symbol(symbol)
    cutoff = utc_cutoff(end)
    cutoff_ms = int(cutoff.value // 1_000_000)
    paths = list(dict.fromkeys(Path(p).resolve() for p in source_paths))
    audit: dict[str, Any] = dict(symbol=symbol, status="pending", end_exclusive=cutoff.isoformat(),
        sources=[], conflicts=[], gap_count=0, missing_bars=0, overlap_timestamps=0,
        overlap_rows=0, overlap_rtol=OVERLAP_RTOL, overlap_atol=0.0)
    if not paths:
        raise HistoryValidationError("missing_sources", audit)
    frames = []
    for source_number, path in enumerate(paths):
        source: dict[str, Any] = {"path": str(path)}
        audit["sources"].append(source)
        try:
            payload = path.read_bytes()
            source.update(sha256=_sha(payload), size_bytes=len(payload))
            expected = (expected_sha256 or {}).get(str(path))
            if expected is not None and source["sha256"] != expected:
                raise HistoryValidationError("source_hash_mismatch", audit)
            header = next(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))
            if len(header) != len(set(header)) or not set(["ts", *OHLCV]).issubset(header):
                raise HistoryValidationError("invalid_source_columns", audit)
            # Keep strings until the timestamp cutoff has excluded future prices.
            raw = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False)
            if not raw.ts.str.fullmatch(r"[0-9]+").all():
                raise HistoryValidationError("invalid_timestamp", audit)
            stamps = pd.to_numeric(raw.ts, errors="raise").to_numpy(dtype=np.int64)
            if np.any(stamps % STEP_MS):
                raise HistoryValidationError("misaligned_timestamp", audit)
            keep = stamps <= cutoff_ms - STEP_MS
            source.update(rows=len(raw), retained_rows=int(keep.sum()), excluded_rows=int((~keep).sum()),
                confirmation="explicit_confirm_column" if "confirm" in raw else "legacy_csv_confirmation_not_retained")
            raw = raw.loc[keep].copy()
            raw["ts"] = stamps[keep]
            if raw.empty:
                continue
            if "confirm" in raw and not raw["confirm"].eq("1").all():
                raise HistoryValidationError("unconfirmed_source_rows", audit)
            if "open_time" in raw:
                clock = pd.to_datetime(raw.open_time, utc=True, errors="raise")
                expected_clock = pd.to_datetime(raw.ts, unit="ms", utc=True)
                if not clock.equals(expected_clock):
                    raise HistoryValidationError("open_time_disagrees_with_ts", audit)
            values = raw[OHLCV].apply(pd.to_numeric, errors="raise").to_numpy(dtype=float)
            o, h, l, c, v = values.T
            invalid = (~np.isfinite(values).all(axis=1) | (np.minimum.reduce([o, h, l, c]) <= 0)
                       | (v < 0) | (h < np.maximum.reduce([o, l, c])) | (l > np.minimum(o, c)))
            if invalid.any():
                source["invalid_rows"] = int(invalid.sum())
                source["invalid_open_times"] = [_iso(t) for t in raw.ts.to_numpy()[invalid][:5]]
                raise HistoryValidationError("invalid_ohlcv", audit)
            frame = pd.DataFrame(values, columns=OHLCV)
            frame.insert(0, "ts", raw.ts.to_numpy(dtype=np.int64))
            frame["_source"] = source_number
            source.update(start=_iso(int(frame.ts.min())), end_close=_iso(int(frame.ts.max())+STEP_MS))
            frames.append(frame)
        except HistoryValidationError:
            raise
        except (OSError, ValueError, TypeError, OverflowError, StopIteration, UnicodeError) as error:
            source["error_type"] = type(error).__name__
            raise HistoryValidationError("unreadable_or_invalid_source", audit) from error
    if not frames:
        raise HistoryValidationError("no_closed_rows_before_cutoff", audit)
    merged = pd.concat(frames, ignore_index=True).sort_values("ts", kind="mergesort")
    repeated = merged.loc[merged.ts.duplicated(keep=False)]
    audit["overlap_timestamps"] = int(repeated.ts.nunique())
    audit["overlap_rows"] = int(len(repeated) - repeated.ts.nunique())
    reference = repeated.groupby("ts", sort=False)[OHLCV].transform("first")
    mismatch = ~np.isclose(repeated[OHLCV].to_numpy(dtype=float), reference.to_numpy(dtype=float),
                           rtol=OVERLAP_RTOL, atol=0.0)
    conflict_stamps = repeated.loc[mismatch.any(axis=1), "ts"].unique()
    for timestamp in conflict_stamps[:5]:
        mask = repeated.ts.eq(timestamp).to_numpy()
        group = repeated.loc[mask]
        audit["conflicts"].append(dict(open_time=_iso(timestamp),
            columns=[name for name, flag in zip(OHLCV, mismatch[mask].any(axis=0)) if flag],
            source_paths=[str(paths[int(i)]) for i in group["_source"].unique()]))
    audit["conflict_timestamps"] = len(conflict_stamps)
    if len(conflict_stamps):
        raise HistoryValidationError("overlapping_quotes_disagree", audit)
    merged = merged.drop_duplicates("ts", keep="first").drop(columns="_source").reset_index(drop=True)
    stamps = merged.ts.to_numpy(dtype=np.int64)
    differences = np.diff(stamps)
    gaps = np.flatnonzero(differences != STEP_MS)
    audit.update(rows=len(merged), start=_iso(stamps[0]), end_close=_iso(stamps[-1]+STEP_MS),
        gap_count=int(len(gaps)), missing_bars=int(sum((differences[gaps] // STEP_MS) - 1)),
        gaps=[dict(after_open=_iso(stamps[i]), next_open=_iso(stamps[i+1]),
                   missing_bars=int(differences[i] // STEP_MS - 1)) for i in gaps[:10]])
    tail_missing = max(0, (cutoff_ms - STEP_MS - int(stamps[-1])) // STEP_MS)
    audit["tail_missing_bars"] = tail_missing
    if len(gaps) or tail_missing:
        raise HistoryValidationError("missing_intervals" if len(gaps) else "incomplete_requested_tail", audit)
    merged["open_time"] = pd.to_datetime(merged.ts, unit="ms", utc=True)
    audit["status"] = "complete"
    return merged[OUTPUT_COLUMNS], audit


def _matching_paths(directory: Path, symbol: str) -> list[Path]:
    pattern = re.compile(rf"okx_{re.escape(symbol)}_USDT_SWAP_15m_[0-9]+\.csv")
    return sorted(p.resolve() for p in directory.glob(f"okx_{symbol}_USDT_SWAP_15m_*.csv")
                  if pattern.fullmatch(p.name))


def prepare_history(
    universe: Path, out_dir: Path, *, end: object, recent_dir: Path | None = None,
    repo_root: Path = ROOT, expected_pool_size: int = 54, builder_commit: str | None = None,
) -> dict[str, Any]:
    """Freeze valid symbols and rejection diagnostics in a new research directory.

    CLI always requires 54 frozen pool members; expected_pool_size supports small
    synthetic API fixtures. No rejected symbol gets an output CSV. A partial build
    has status=rejected and is evidence of missing data, not a usable full pool.
    """
    cutoff = utc_cutoff(end)
    root, target = Path(repo_root).resolve(), Path(out_dir).resolve()
    if target.exists():
        raise ValueError("out_dir already exists; frozen outputs must not be overwritten")
    for name in ("kline_fetched", "kline_deep", "kline_cache"):
        canonical = (root / "data" / name).resolve()
        if target == canonical or canonical in target.parents:
            raise ValueError("out_dir must not be inside canonical market data")
    universe = Path(universe).resolve()
    universe_bytes = universe.read_bytes()
    manifest = json.loads(universe_bytes)
    rows = manifest.get("sources")
    if not isinstance(rows, list) or len(rows) != expected_pool_size:
        raise ValueError("frozen universe size differs from its required pool size")
    symbols = [_symbol(row["symbol"]) for row in rows]
    if len(set(symbols)) != len(symbols) or set(symbols).intersection(ILLUSTRATIONS):
        raise ValueError("frozen pool has duplicates or includes separate owner illustrations")
    specs = []
    for row in rows:
        path = (root / row["path"]).resolve()
        symbol = row["symbol"]
        if (root / "data/kline_deep").resolve() not in path.parents or not re.fullmatch(
                rf"okx_{re.escape(symbol)}_USDT_SWAP_15m_[0-9]+\.csv", path.name):
            raise ValueError("frozen source is not the declared deep USDT-SWAP 15m file")
        if re.fullmatch(r"[a-f0-9]{64}", str(row.get("sha256", ""))) is None:
            raise ValueError("frozen universe requires source SHA256")
        specs.append((symbol, "frozen_pool", [path], {str(path): row["sha256"]}))
    specs += [(symbol, "owner_illustration", [], {}) for symbol in ILLUSTRATIONS]
    report: dict[str, Any] = dict(schema_version=1, end_exclusive=cutoff.isoformat(),
        universe_path=str(universe), universe_sha256=_sha(universe_bytes),
        pool_symbols=symbols, illustration_symbols=list(ILLUSTRATIONS), expected_pool_size=expected_pool_size,
        builder_path=str(Path(__file__).resolve()), builder_sha256=_sha(Path(__file__).read_bytes()),
        builder_commit=builder_commit,
        selection="Inherited existing deep pool; owner illustrations excluded from pooled claims",
        canonical_market_data_written=False, research_data_written=True,
        training_eligible=False, production_eligible=False,
        symbols=[], files={})
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".altcoin-history-", dir=target.parent) as temporary:
        staged = Path(temporary) / "frozen"
        staged.mkdir()
        for symbol, cohort, primary, expected in specs:
            sources = primary + _matching_paths(root / "data/kline_fetched", symbol)
            if recent_dir is not None:
                sources += _matching_paths(Path(recent_dir), symbol)
            try:
                frame, audit = merge_symbol(symbol, sources, end=cutoff, expected_sha256=expected)
                relative = f"{cohort}/{symbol}_USDT_SWAP_15m.csv"
                destination = staged / relative
                destination.parent.mkdir(exist_ok=True)
                frame.to_csv(destination, index=False, float_format="%.17g", lineterminator="\n")
                output_hash = _sha(destination.read_bytes())
                audit.update(output_path=str(target / relative), output_sha256=output_hash)
                report["files"][relative] = output_hash
            except HistoryValidationError as error:
                audit = error.audit
            audit["cohort"] = cohort
            report["symbols"].append(audit)
        report["complete_pool_symbols"] = sum(x["status"] == "complete" and x["cohort"] == "frozen_pool" for x in report["symbols"])
        report["complete_illustrations"] = sum(x["status"] == "complete" and x["cohort"] == "owner_illustration" for x in report["symbols"])
        report["status"] = "complete" if all(x["status"] == "complete" for x in report["symbols"]) else "rejected"
        (staged / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        if target.exists():
            raise ValueError("out_dir appeared during build; preserved unchanged")
        os.rename(staged, target)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--end", required=True, help="exclusive, already closed UTC 15m boundary")
    parser.add_argument("--recent-dir", type=Path)
    args = parser.parse_args(argv)
    relative = str(Path(__file__).resolve().relative_to(ROOT))
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        committed = subprocess.check_output(["git", "show", f"{commit}:{relative}"], cwd=ROOT, stderr=subprocess.DEVNULL)
        if committed != Path(__file__).read_bytes():
            raise ValueError("commit the history builder before constructing real data")
        report = prepare_history(args.universe, args.out_dir, end=args.end, recent_dir=args.recent_dir,
                                 builder_commit=commit)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"History build refused: {error}")
        return 1
    print(json.dumps({key: report[key] for key in ("status", "complete_pool_symbols", "complete_illustrations")}, ensure_ascii=False))
    return 0 if report["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
