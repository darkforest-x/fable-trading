"""Rebuild the XAUUSD coverage audit from quote-gap timestamps and metadata.

This reads no prices and makes no network requests. It audits the gap artifact
emitted by the HistData reader, whose source-time contract is fixed EST without
DST. All audit windows are UTC and missing intervals are [previous + 1m, next).
Weekend/holiday classifications are descriptive hints, never a trading-session
calendar or grounds for silently dropping gaps. No returns are computed.

The CLI requires this exact builder to exist in the supplied Git commit before
writing the formal audit. It preserves the original receipt and recomputes all
counts/lists from current inputs before comparing them with that receipt.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd


BUILDER_PATH = "yoyo/evaluation/xauusd_gap_quality.py"
PERIODS = (
    ("selection_2024_2025", "2024-01-01", "2026-01-01"),
    ("confirmation_2026_jan_may", "2026-01-01", "2026-06-01"),
    ("development_2023", "2023-01-01", "2024-01-01"),
)
HOLIDAY_CANDIDATES = {
    "2023-12-29": "New Year extended weekend",
    "2024-01-15": "January US holiday", "2024-02-19": "February US holiday",
    "2024-03-28": "Easter/Good Friday", "2024-05-27": "May US holiday",
    "2024-06-19": "June US holiday", "2024-07-04": "July US holiday",
    "2024-09-02": "September US holiday", "2024-11-28": "Thanksgiving",
    "2024-11-29": "post-Thanksgiving", "2024-12-24": "Christmas",
    "2024-12-31": "New Year", "2025-01-20": "January US holiday",
    "2025-02-17": "February US holiday", "2025-04-17": "Easter/Good Friday",
    "2025-05-26": "May US holiday", "2025-06-19": "June US holiday",
    "2025-07-04": "July US holiday", "2025-09-01": "September US holiday",
    "2025-11-27": "Thanksgiving", "2025-11-28": "post-Thanksgiving",
    "2025-12-24": "Christmas", "2025-12-31": "New Year",
    "2026-01-19": "January US holiday", "2026-02-16": "February US holiday",
    "2026-04-02": "Easter/Good Friday", "2026-05-25": "May US holiday",
}


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def read_gaps(csv_payload: bytes, metadata: dict[str, Any]) -> pd.DataFrame:
    """Validate gap arithmetic, chronology and flags before classification."""
    frame = pd.read_csv(io.BytesIO(csv_payload))
    columns = {"previous", "next", "missing_minutes", "possible_weekend",
               "possible_daily_closure", "unclassified"}
    if not columns.issubset(frame.columns):
        raise ValueError("quote-gap schema changed")
    for column in ("previous", "next"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="raise")
        if frame[column].isna().any() or not frame[column].eq(frame[column].dt.floor("min")).all():
            raise ValueError("gap timestamps must be valid UTC minute boundaries")
    for column in ("possible_weekend", "possible_daily_closure", "unclassified"):
        if not pd.api.types.is_bool_dtype(frame[column].dtype):
            raise ValueError("source gap flags must be booleans")
    frame["missing_minutes"] = pd.to_numeric(frame["missing_minutes"], errors="raise")
    expected = (frame["next"] - frame["previous"]).dt.total_seconds() / 60 - 1
    if not (expected.eq(frame["missing_minutes"]) & expected.gt(0)).all():
        raise ValueError("gap duration does not match endpoint arithmetic")
    if (not frame["previous"].is_monotonic_increasing
            or frame["previous"].duplicated().any()
            or frame["previous"].lt(frame["next"].shift()).any()):
        raise ValueError("gap intervals are unsorted, duplicated, or overlapping")
    if len(frame) != metadata["nonconsecutive_minute_gaps"]:
        raise ValueError("gap count does not reconcile with source metadata")
    frame["missing_start"] = frame["previous"] + pd.Timedelta(minutes=1)
    frame["usual_weekend_shape"] = (
        frame["previous"].dt.dayofweek.eq(4) & frame["next"].dt.dayofweek.eq(6)
        & frame["previous"].dt.hour.between(20, 21) & frame["next"].dt.hour.between(22, 23)
        & frame["missing_minutes"].between(2820, 3010)
    )
    return frame


def classify_gap(row: pd.Series) -> tuple[str, str]:
    """Use observed endpoints only; no class establishes valid market closure."""
    day = row["previous"].strftime("%Y-%m-%d")
    if day in {"2024-01-12", "2025-12-05"}:
        return "unexplained_early_friday_end", (
            "Friday endpoint precedes the common 21:59 UTC endpoint by several hours; "
            "the source weekend flag does not explain those preceding hours."
        )
    if day == "2025-10-21" and row["missing_minutes"] == 120:
        return "unexplained_extended_daily_gap", (
            "120 absent minutes versus the common 60-minute daily gap; source daily-closure flag is only a hint."
        )
    if row["usual_weekend_shape"]:
        return "usual_weekend_shape_hint", "Common observed weekend shape; no venue-calendar verification."
    if day in HOLIDAY_CANDIDATES:
        return "holiday_candidate_unverified", (
            HOLIDAY_CANDIDATES[day] + " date-based candidate; source execution venue is unverified."
        )
    return "unexplained_or_unverified_gap", "No verified ordinary-calendar explanation."


def _gap_record(row: pd.Series) -> dict[str, Any]:
    label, note = classify_gap(row)
    return {
        "previous_quote_utc": row["previous"].isoformat(),
        "next_quote_utc": row["next"].isoformat(),
        "missing_start_utc": row["missing_start"].isoformat(),
        "missing_end_exclusive_utc": row["next"].isoformat(),
        "missing_minutes_full": int(row["missing_minutes"]),
        "missing_minutes_in_period": int(row["window_missing_minutes"]),
        "source_flags": {key: bool(row[key]) for key in
                         ("possible_weekend", "possible_daily_closure", "unclassified")},
        "classification": label, "note": note,
    }


def _metadata_rows(metadata: dict[str, Any], start: pd.Timestamp, end: pd.Timestamp) -> int:
    """Require whole retained archive summaries inside this particular window."""
    total = 0
    for archive in metadata["archives"]:
        retained = archive["retained"]
        if not retained["rows"]:
            continue
        first, last = pd.Timestamp(retained["time_min"]), pd.Timestamp(retained["time_max"])
        if last < start or first >= end:
            continue
        if first < start or last >= end:
            raise ValueError("period partially overlaps retained archive; metadata alone cannot verify row count")
        total += int(retained["rows"])
    return total


def audit_period(frame: pd.DataFrame, metadata: dict[str, Any],
                 name: str, start: str, end: str) -> dict[str, Any]:
    start_utc, end_utc = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    if (start_utc < pd.Timestamp(metadata["time_min"])
            or end_utc > pd.Timestamp(metadata["time_close_max"])):
        raise ValueError("audit period extends outside the observed source span")
    part = frame[(frame["next"] > start_utc) & (frame["missing_start"] < end_utc)].copy()
    part["window_missing_minutes"] = ((part["next"].clip(upper=end_utc)
                                       - part["missing_start"].clip(lower=start_utc))
                                      .dt.total_seconds() / 60).astype(int)
    large = part[part["missing_minutes"].ge(120)]
    usual = large[large["usual_weekend_shape"]]
    other = large[~large["usual_weekend_shape"]]
    observed = int((end_utc - start_utc).total_seconds() / 60) - int(part["window_missing_minutes"].sum())
    if observed != _metadata_rows(metadata, start_utc, end_utc):
        raise ValueError(f"{name}: derived rows do not reconcile with source metadata")
    intraday = part[
        part["previous"].dt.dayofweek.lt(5)
        & part["previous"].dt.normalize().eq(part["next"].dt.normalize())
        & part["missing_start"].dt.hour.ge(1) & part["next"].dt.hour.le(21)
        & part["missing_minutes"].ge(60)
    ]
    elapsed = (part["next"] - part["previous"]).dt.total_seconds() / 3600
    result = {
        "start_utc": start_utc.isoformat(), "end_exclusive_utc": end_utc.isoformat(),
        "observed_rows": observed, "row_count_reconciles_with_source_metadata": True,
        "coverage_completeness_proven": False, "all_gap_count": len(part),
        "all_absent_calendar_minutes": int(part["window_missing_minutes"].sum()),
        "large_gap_threshold_minutes": 120, "large_gap_count": len(large),
        "large_gap_absent_calendar_minutes": int(large["window_missing_minutes"].sum()),
        "usual_weekend_shape_large_count": len(usual),
        "usual_weekend_shape_large_absent_calendar_minutes": int(usual["window_missing_minutes"].sum()),
        "other_large_count": len(other),
        "other_large_absent_calendar_minutes": int(other["window_missing_minutes"].sum()),
        "same_day_01_to_21_utc_weekday_gaps_at_least_60_min_count": len(intraday),
        "same_day_01_to_21_utc_weekday_gaps_at_least_60_min_absent_minutes": int(intraday["window_missing_minutes"].sum()),
        "longest_elapsed_hours": float(elapsed.max()) if len(elapsed) else None,
        "has_any_gap_elapsed_over_80_hours": bool(elapsed.gt(80).any()),
    }
    if name != "development_2023":
        result["all_large_gaps"] = [_gap_record(row) for _, row in large.iterrows()]
        result["large_gaps_other_than_usual_weekend_shape"] = [_gap_record(row) for _, row in other.iterrows()]
        small = part[(~part["possible_weekend"]) & (~part["possible_daily_closure"])
                     & part["missing_minutes"].ge(10) & part["missing_minutes"].lt(120)]
        result["additional_notable_smaller_gaps"] = [_gap_record(row) for _, row in small.iterrows()]
    else:
        monthly = intraday.groupby(intraday["previous"].dt.strftime("%Y-%m")).agg(
            gap_count=("missing_minutes", "size"), absent_minutes=("window_missing_minutes", "sum"))
        result["intraday_gap_monthly"] = {
            month: {"gap_count": int(row.gap_count), "absent_minutes": int(row.absent_minutes)}
            for month, row in monthly.iterrows()
        }
        result["intraday_gap_first_previous_utc"] = intraday["previous"].min().isoformat() if len(intraday) else None
        result["intraday_gap_last_next_utc"] = intraday["next"].max().isoformat() if len(intraday) else None
        baseline = sum(int(a["rows"]) for a in metadata["archives"] if a["archive_token"] == "2022")
        if not baseline:
            raise ValueError("2022 metadata is missing for the declared annual comparison")
        result.update({
            "source_rows_2022_comparison": baseline, "fewer_rows_than_2022": baseline - observed,
            "fewer_rows_than_2022_pct": 100 * (baseline - observed) / baseline,
            "annual_comparison_limitation": "Different weekdays and holidays prevent this row difference from being an exact missing-trading-minute estimate.",
            "intraday_examples": [_gap_record(row) for _, row in intraday.head(3).iterrows()],
        })
    return result


def compare_original(original: dict[str, Any], rebuilt: dict[str, Any]) -> dict[str, Any]:
    """Compare recomputed numbers and timestamp lists, not merely byte hashes."""
    inputs = {key: original["inputs"].get(key) == rebuilt["inputs"][key]
              for key in ("source_metadata_sha256", "quote_gaps_sha256")}
    period_checks = {}
    gap_keys = ("previous_quote_utc", "next_quote_utc", "missing_start_utc",
                "missing_end_exclusive_utc", "missing_minutes_full", "missing_minutes_in_period", "source_flags")
    for name, current in rebuilt["periods"].items():
        prior = original["periods"][name]
        metrics = {key: prior.get(key) == value for key, value in current.items()
                   if not isinstance(value, (list, dict)) and key != "annual_comparison_limitation"}
        lists = {}
        for key, value in current.items():
            if isinstance(value, list):
                before = [{k: row[k] for k in gap_keys} for row in prior.get(key, [])]
                after = [{k: row[k] for k in gap_keys} for row in value]
                lists[key] = before == after
        if "intraday_gap_monthly" in current:
            metrics["intraday_gap_monthly"] = prior.get("intraday_gap_monthly") == current["intraday_gap_monthly"]
        period_checks[name] = {"metrics_match": all(metrics.values()), "gap_lists_match": all(lists.values()),
                               "metric_checks": metrics, "gap_list_checks": lists}
    prior_csv_hash = original["inputs"].get("quote_gaps_decompressed_sha256")
    current_csv_hash = rebuilt["inputs"]["quote_gaps_decompressed_sha256"]
    decompressed_match = prior_csv_hash == current_csv_hash if prior_csv_hash is not None else None
    explanation = "Input artifact bytes match the original audit."
    if not inputs["quote_gaps_sha256"]:
        explanation = (
            "Compressed bytes changed but decompressed CSV bytes are identical: container-level change."
            if decompressed_match is True else
            "Compressed bytes changed. Gzip timestamp/container regeneration is possible, but the original receipt cannot establish that cause; use fresh period/list comparisons below."
        )
    return {"input_hash_checks": inputs, "decompressed_csv_hash_matches_original": decompressed_match,
            "period_checks": period_checks, "all_period_metrics_and_lists_match": all(
                check["metrics_match"] and check["gap_lists_match"] for check in period_checks.values()),
            "interpretation": explanation,
            "method": "All current periods were independently recomputed from the current CSV before comparison; hashes were not merely replaced."}


def build_audit(metadata_bytes: bytes, gap_gzip_bytes: bytes, *, results_dir: Path,
                source_commit: str, source_sha256: str, original: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = json.loads(metadata_bytes)
    csv_payload = gzip.decompress(gap_gzip_bytes)
    frame = read_gaps(csv_payload, metadata)
    periods = {name: audit_period(frame, metadata, name, start, end) for name, start, end in PERIODS}
    output = {
        "experiment_id": "exp-xauusd-system-search-20260908-v1", "audit_version": "gap-coverage-heuristic-v1",
        "audit_revision": 2, "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "builder": {"path": BUILDER_PATH, "source_commit": source_commit, "sha256": source_sha256},
        "training_eligible": False, "production_eligible": False, "audit_only": True,
        "returns_calculated": False, "raw_market_data_written": False,
        "inputs": {"source_metadata": str(results_dir / "source_metadata.json"),
                   "source_metadata_sha256": _sha(metadata_bytes),
                   "quote_gaps": str(results_dir / "quote_gaps.csv.gz"),
                   "quote_gaps_sha256": _sha(gap_gzip_bytes),
                   "quote_gaps_decompressed_sha256": _sha(csv_payload)},
        "method": {
            "input_scope": "Only source metadata and quote-gap timestamps/flags; no OHLC, positions, downloads or returns.",
            "missing_interval": "[previous quote + 1 minute, next quote), clipped to each UTC period before summing.",
            "large_gap": "Full missing_minutes >=120; period totals clip cross-boundary gaps.",
            "usual_weekend_shape_hint": "Friday previous UTC hour 20/21, Sunday next hour 22/23, 2820-3010 missing minutes inclusive. Grouping only, not an approved session mask.",
            "intraday_diagnostic": "Same UTC weekday; missing start hour >=1; next quote hour <=21; missing_minutes >=60.",
            "holiday_candidates": "Unverified date-based hints for an unidentified source venue.",
            "gap_formula_matches_all_rows": True,
        },
        "periods": periods,
        "findings": [
            f"2023 diagnostic intraday gaps: {periods['development_2023']['same_day_01_to_21_utc_weekday_gaps_at_least_60_min_count']} gaps, {periods['development_2023']['same_day_01_to_21_utc_weekday_gaps_at_least_60_min_absent_minutes']} absent minutes; inspect the monthly table for concentration.",
            "Inspect unexplained_early_friday_end, unexplained_extended_daily_gap and additional_notable_smaller_gaps; source weekend/daily flags do not resolve them.",
            "Confirmation absence of a detected large unexplained pattern is not proof of complete coverage.",
            "Potential signal/execution/stop-observation effects were not measured because no trade ledger or returns were read.",
        ],
        "limitations": [
            "Absent calendar minutes include legitimate closures and cannot be equated with missing tradeable minutes.",
            "No authoritative historical session calendar for the source execution venue is available in these inputs.",
            "Structural validity and absence of conflicting duplicates do not prove temporal completeness.",
            "No gaps were filled, deleted, promoted to guaranteed valid, or used to change trading rules.",
        ],
    }
    if original is not None:
        output["original_audit_verification"] = compare_original(original, output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    commit = subprocess.check_output(["git", "rev-parse", args.source_commit], cwd=repo, text=True).strip()
    frozen = subprocess.check_output(["git", "show", f"{commit}:{BUILDER_PATH}"], cwd=repo)
    if frozen != Path(__file__).read_bytes():
        raise RuntimeError("Builder is not identical to supplied committed source; refusing formal audit generation")
    directory = args.results_dir
    target, preserved = directory / "data_quality_audit.json", directory / "data_quality_audit_original.json"
    if not preserved.exists() and target.exists():
        shutil.copyfile(target, preserved)
    original_bytes = preserved.read_bytes() if preserved.exists() else None
    original = json.loads(original_bytes) if original_bytes is not None else None
    metadata_bytes = (directory / "source_metadata.json").read_bytes()
    gap_bytes = (directory / "quote_gaps.csv.gz").read_bytes()
    audit = build_audit(metadata_bytes, gap_bytes, results_dir=directory,
                        source_commit=commit, source_sha256=_sha(frozen), original=original)
    if original_bytes is not None:
        audit["original_audit_verification"].update({"original_path": str(preserved), "original_sha256": _sha(original_bytes)})
    # Fail rather than attach hashes to a different concurrent source revision.
    if ((directory / "source_metadata.json").read_bytes() != metadata_bytes
            or (directory / "quote_gaps.csv.gz").read_bytes() != gap_bytes):
        raise RuntimeError("Audit inputs changed during build; no new audit was written")
    target.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"path": str(target.resolve()), "audit_revision": 2,
                      "verification": audit.get("original_audit_verification")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
