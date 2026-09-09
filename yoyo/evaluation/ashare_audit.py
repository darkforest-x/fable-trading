"""Reproduce the frozen A-share source audit using local evidence only.

This implementation was extracted AFTER the original one-off audit. Its commit
does not prove that the original audit was produced by an already committed
builder. Run this CLI to a NEW output file; the original evidence is preserved.
No provider client, socket, strategy, parameter search, or mutable source writer
is called. Raw/HFQ validation reuses the frozen data module's pure functions.

Inputs are the existing universe, exclusions, source receipts, daily files and
per-security basic metadata. Original per-security basic snapshots outrank a
fetch-attempt log. Corporate-action tests use each traded session and its previous
traded session, never future returns. Outputs are sidecars only.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from yoyo.evaluation.ashare_data import board_for_code, merge_adjusted, validate_daily


class AShareAuditError(ValueError):
    """Frozen source evidence cannot support a complete audit."""


def _require(condition: Any, message: str) -> None:
    if not bool(condition):
        raise AShareAuditError(message)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> Any:
    return json.loads(path.read_text())


def verify_receipts(data: Path) -> list[dict[str, Any]]:
    """Check every receipt plus orphan raw/basic source files; never repair."""
    sources = [data / "universe_raw.csv"]
    sources.extend((data / "source").glob("*.csv"))
    sources.extend((data / "basic").glob("*.csv"))
    for source in sources:
        _require(source.exists() and source.with_suffix(source.suffix + ".json").exists(),
                 f"missing frozen source/receipt pair: {source}")
    records = []
    for path in sorted(data.rglob("*.csv.json")):
        source = Path(str(path)[:-5])
        receipt = _json(path)
        _require(source.exists(), f"orphan source receipt: {path}")
        sha = _sha(source)
        _require(sha == receipt.get("sha256"), f"hash mismatch: {source}")
        key = hashlib.sha256(json.dumps(receipt["request"], sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
        _require(key == receipt.get("request_key"), f"request fingerprint mismatch: {source}")
        rows = len(pd.read_csv(source, dtype=str, keep_default_na=False))
        _require(rows == receipt.get("rows"), f"receipt row count mismatch: {source}")
        records.append({"path": str(source), "receipt_path": str(path), "sha256": sha,
                        "request_key": key, "rows": receipt["rows"], "request": receipt["request"]})
    return records


def _listing_metadata(data: Path, code: str) -> dict[str, Any]:
    source = data / "basic" / f"{code}.csv"
    if not source.exists():
        return {"code": code, "ipoDate": None, "outDate": None, "metadata_complete": False,
                "reason": "No complete per-security metadata snapshot; no new network query"}
    basic = pd.read_csv(source, dtype=str, keep_default_na=False)
    _require({"code", "ipoDate", "outDate"}.issubset(basic.columns)
             and len(basic) == 1 and basic.code.iloc[0] == code, f"invalid basic metadata: {code}")
    return {"code": code, "ipoDate": basic.ipoDate.iloc[0], "outDate": basic.outDate.iloc[0],
            "source": str(source), "source_sha256": _sha(source), "metadata_complete": True}


def _compare_daily(actual: pd.DataFrame, expected: pd.DataFrame, code: str) -> None:
    _require(list(actual.columns) == list(expected.columns) and len(actual) == len(expected),
             f"derived daily schema/count mismatch: {code}")
    for column in ["date", "code", "board"]:
        _require(actual[column].fillna("").tolist() == expected[column].fillna("").tolist(),
                 f"derived daily identity mismatch: {code}/{column}")
    columns = [c for c in actual if c not in ["date", "code", "board"]]
    _require(np.allclose(actual[columns].to_numpy(float), expected[columns].to_numpy(float),
                        rtol=1e-12, atol=1e-12, equal_nan=True), f"derived daily numeric mismatch: {code}")


def corporate_action_rows(frame: pd.DataFrame, code: str, excluded: bool) -> list[dict[str, Any]]:
    """Use raw preclose/current factor and only the previous traded close."""
    traded = frame[frame.tradestatus == 1]
    previous = traded.shift()
    mask = (traded.factor / previous.factor - 1).abs() > 1e-5
    rows = []
    for index in traded.index[mask]:
        error = float(traded.loc[index, "raw_preclose"] * traded.loc[index, "factor"]
                      / previous.loc[index, "close"] - 1)
        rows.append({"code": code, "date": traded.loc[index, "date"],
                     "raw_previous_close": float(previous.loc[index, "raw_close"]),
                     "raw_preclose_reference": float(traded.loc[index, "raw_preclose"]),
                     "factor_before": float(previous.loc[index, "factor"]),
                     "factor_after": float(traded.loc[index, "factor"]),
                     "hfq_previous_close": float(previous.loc[index, "close"]),
                     "hfq_preclose": float(traded.loc[index, "preclose"]),
                     "reference_error_fraction": error, "research_excluded": excluded})
    return rows


def _count_word(value: int) -> str:
    return {1: "one", 2: "two", 3: "three"}.get(value, str(value))


def build_audit(data: Path, *, original_universe: Path | None = None,
                builder_commit: str = "unknown") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Rebuild a compatible audit without modifying any frozen input bytes."""
    data = Path(data)
    universe = _json(data / "universe.json")
    exclusions = _json(data / "exclusions.json")
    codes = universe["codes"]
    excluded = set(exclusions["codes"])
    _require(len(codes) == len(set(codes)), "duplicate frozen universe code")
    _require(all(board_for_code(code) in {"main_sh", "main_sz"} for code in codes), "non-mainboard code in universe")
    _require(excluded.issubset(codes) and all(exclusions["codes"].values()), "invalid exclusion codes/reasons")
    _require(exclusions.get("created_before_parameter_selection") is True and bool(exclusions.get("rule")),
             "missing preselection exclusion provenance")
    original_path = original_universe or data.parent / "data" / "universe.json"
    original = _json(original_path)
    original_mainboard = [code for code in original["codes"] if board_for_code(code) in {"main_sh", "main_sz"}]
    _require(codes == original_mainboard, "frozen mainboard selection differs from original universe")
    hashes = verify_receipts(data)
    issues, stocks, actions, metadata = [], [], [], []
    start, end = "2015-01-01", "2025-12-31"
    for code in codes:
        frames = []
        for adjustment in ["3", "1"]:
            source = data / "source" / f"{code}_{adjustment}.csv"
            try:
                frame = validate_daily(pd.read_csv(source, dtype=str, keep_default_na=False),
                                       code=code, start=start, end=end, adjustment=adjustment)
                frames.append(frame)
            except Exception as error:
                issues.append({"code": code, "adjustment": adjustment, "path": str(source), "error": str(error)})
                _require(code in excluded, f"unexcluded source unavailable: {code}: {error}")
        complete = len(frames) == 2
        record = {"code": code, "source_complete": complete, "research_eligible": code not in excluded,
                  "exclusion_reason": exclusions["codes"].get(code), "board": board_for_code(code)}
        listing = _listing_metadata(data, code)
        metadata.append(listing)
        if complete:
            expected = merge_adjusted(*frames)
            path = data / "daily" / f"{code}.csv"
            daily = pd.read_csv(path)
            _compare_daily(daily, expected, code)
            _require(not daily.empty, f"empty supposedly complete daily history: {code}")
            record.update({"rows": len(daily), "first_date": str(daily.date.iloc[0]),
                           "last_date": str(daily.date.iloc[-1]), "traded_rows": int((daily.tradestatus == 1).sum()),
                           "st_rows": int((daily.isST == 1).sum()),
                           "traded_st_rows": int(((daily.tradestatus == 1) & (daily.isST == 1)).sum()),
                           "suspended_rows": int((daily.tradestatus == 0).sum()),
                           "daily_path": str(path), "daily_sha256": _sha(path)})
            actions.extend(corporate_action_rows(daily, code, code in excluded))
        else:
            record.update({"rows": 0, "first_date": None, "last_date": None, "traded_rows": 0,
                           "st_rows": 0, "traded_st_rows": 0, "suspended_rows": 0})
        record.update({"ipoDate": listing["ipoDate"], "outDate": listing["outDate"],
                       "listing_metadata_complete": listing["metadata_complete"]})
        stocks.append(record)
    benchmark_path = data / "daily" / "sh.000300.csv"
    benchmark = pd.read_csv(benchmark_path)
    benchmark_raw = validate_daily(pd.read_csv(data / "source" / "sh.000300_3.csv", dtype=str,
                                               keep_default_na=False), code="sh.000300", start=start,
                                    end=end, adjustment="3")
    _compare_daily(benchmark, merge_adjusted(benchmark_raw, benchmark_raw), "sh.000300")
    _require(not benchmark.empty and benchmark.factor.eq(1).all() and benchmark.adjustflag.eq(3).all()
             and benchmark.close.eq(benchmark.raw_close).all(), "benchmark is not native raw price index")
    discontinuities = [a for a in actions if abs(a["reference_error_fraction"]) > .005]
    _require(all(a["code"] in excluded for a in discontinuities), "unexcluded nonordinary corporate action")
    normal = [a for a in actions if abs(a["reference_error_fraction"]) <= .005]
    delisted = [m for m in metadata if m.get("outDate") and m["outDate"] <= end]
    missing = [m["code"] for m in metadata if not m["metadata_complete"]]
    source_count = sum(s["source_complete"] for s in stocks)
    usable = [s for s in stocks if s["research_eligible"]]
    number_breaks = len({a["code"] for a in discontinuities})
    honesty = [
        f"The frozen {len(codes)}-code pool is preserved; {_count_word(len(excluded))} securities are unavailable for research under preselection data-quality rules, not replaced.",
        f"Raw/HFQ complete stocks are {source_count}; {len(usable)} remain eligible after {_count_word(number_breaks)} extraordinary corporate-action discontinuities.",
        ("Listing metadata is a present retrieval used only for audit, never selection or predictors; one excluded security has no complete individual metadata snapshot."
         if len(missing) == 1 and missing[0] in excluded else
         f"Listing metadata is a present retrieval used only for audit, never selection or predictors; {len(missing)} securities lack complete individual metadata snapshots."),
        "Original network failure logs and rejected adjustment responses are preserved; current hash verification does not erase their history.",
        "Data audit complete does not mean complete economic rights/cash-flow reconstruction or profitable parameters.",
    ]
    audit = {"schema": "spike-ashare-daily-data-audit-v1", "complete": True, "offline_only": True,
             "network_queries_this_finalize": 0, "created_at": datetime.now(timezone.utc).isoformat(),
             "builder_commit": builder_commit, "data_directory": str(data.resolve()),
             "frozen_universe_count": len(codes), "source_complete_stock_count": source_count,
             "research_usable_stock_count": len(usable), "excluded_stock_count": len(excluded),
             "same_mainboard_codes_as_original_350": True, "replacements": 0,
             "universe_sha256": _sha(data / "universe.json"), "exclusions_sha256": _sha(data / "exclusions.json"),
             "exclusions_path": str((data / "exclusions.json").resolve()), "exclusions": exclusions,
             "all_source_hashes_verified": True, "source_receipt_count": len(hashes), "hash_failures": 0,
             "source_semantic_issues": issues, "source_complete_rows": sum(s["rows"] for s in stocks),
             "research_usable_rows": sum(s["rows"] for s in usable),
             "research_traded_rows": sum(s["traded_rows"] for s in usable),
             "research_st_rows": sum(s["st_rows"] for s in usable),
             "research_traded_st_rows": sum(s["traded_st_rows"] for s in usable),
             "research_suspended_rows": sum(s["suspended_rows"] for s in usable),
             "listing_metadata_missing_codes": missing, "known_delisted_by_sample_end": delisted,
             "known_delisted_count": len(delisted),
             "usable_known_delisted_count": sum(m["code"] not in excluded for m in delisted),
             "corporate_action_reference_audit": {"factor_change_sessions": len(actions),
                 "ordinary_reference_continuity_sessions": len(normal),
                 "ordinary_max_abs_error_fraction": max((abs(a["reference_error_fraction"]) for a in normal), default=0.0),
                 "threshold": .005, "nonordinary_discontinuities": discontinuities,
                 "all_discontinuities_excluded_before_parameter_selection": True},
             "benchmark": {"code": "sh.000300", "rows": len(benchmark),
                 "first_date": str(benchmark.date.iloc[0]), "last_date": str(benchmark.date.iloc[-1]),
                 "source_adjustflag": 3, "factor": 1,
                 "kind": "native price index; not a dividend-reinvestment total-return index",
                 "daily_path": str(benchmark_path.resolve()), "daily_sha256": _sha(benchmark_path),
                 "hfq_query_snapshot_present_but_unused": (data / "source" / "sh.000300_1.csv").exists()},
             "stocks": stocks, "source_hashes": hashes, "listing_metadata": metadata, "honesty": honesty}
    return audit, actions


def semantic_content(value: Any) -> Any:
    """Ignore creation/version/location metadata, never financial/data facts."""
    ignored = {"created_at", "builder_commit", "data_directory", "path", "receipt_path", "source"}
    if isinstance(value, dict):
        return {key: semantic_content(item) for key, item in value.items()
                if key not in ignored and not key.endswith("_path")}
    if isinstance(value, list):
        return [semantic_content(item) for item in value]
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--original-universe", type=Path)
    parser.add_argument("--builder-commit", default="unknown")
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    _require(not output.exists(), "output exists; original evidence must not be overwritten")
    _require(args.data.resolve() not in output.parents, "audit output must be outside the frozen data directory")
    audit, _ = build_audit(args.data, original_universe=args.original_universe,
                           builder_commit=args.builder_commit)
    if args.compare:
        _require(semantic_content(_json(args.compare)) == semantic_content(audit),
                 "reproduced audit differs in substantive content")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as handle:
        json.dump(audit, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(json.dumps({"output": str(output), "source_receipts": audit["source_receipt_count"],
                      "source_complete_stocks": audit["source_complete_stock_count"],
                      "research_usable_stocks": audit["research_usable_stock_count"],
                      "known_delisted": audit["known_delisted_count"],
                      "semantic_comparison_passed": bool(args.compare)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
