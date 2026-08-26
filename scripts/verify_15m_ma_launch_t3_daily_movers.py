#!/usr/bin/env python3
"""Verify the frozen three-day t-3 daily-mover scan without network access.

The verifier reads the exact disposable OHLCV snapshots and CSV outputs bound
by the fetch and scan receipts.  It checks artifact identity, three complete
UTC Top20 boards, 15-minute continuity, mapped core/confirmation geometry,
event deduplication, class/count reconciliation, and rendered PNG identity.
No future market data is fetched and no training, tuning, promotion, forward,
deployment, or order state is touched.

Usage:
  PYTHONPATH=. .venv/bin/python \
    scripts/verify_15m_ma_launch_t3_daily_movers.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
import pandas as pd

from scripts.scan_15m_ma_launch_t3_daily_movers import (
    DEFAULT_OUT,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    EXPERIMENT_ID,
    load_preregistration,
    sha256_file,
    utc,
)


ROOT = Path(__file__).resolve().parents[1]
BAR_DELTA = pd.Timedelta(minutes=15)
CLASS_NAMES = {0: "dense_long", 1: "dense_short"}


class MoversVerificationError(RuntimeError):
    """Fail-closed artifact or semantic verification error."""


def require(condition: bool, message: str) -> None:
    """Raise a stable verification error when one contract is false."""

    if not condition:
        raise MoversVerificationError(message)


def require_equal(actual: Any, expected: Any, label: str) -> None:
    """Require exact equality with a readable mismatch."""

    require(actual == expected, f"{label}: expected {expected!r}, got {actual!r}")


def load_json(path: Path) -> dict[str, Any]:
    """Read one JSON object and reject non-object roots."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(payload, dict), f"{path} must contain a JSON object")
    return payload


def verify_sources_committed(prereg_path: Path) -> str:
    """Bind the verifier to committed scan, preregistration, and verifier bytes."""

    paths = [
        Path(__file__).resolve().relative_to(ROOT),
        (ROOT / "scripts" / "scan_15m_ma_launch_t3_daily_movers.py").relative_to(ROOT),
        prereg_path.resolve().relative_to(ROOT),
    ]
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    require_equal(branch, "main", "official verification branch")
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    require(not dirty, f"verification sources must be committed:\n{dirty}")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    require(len(commit) == 40, "could not resolve verifier commit")
    return commit


def assert_rank_contract(frame: pd.DataFrame, days: Iterable[pd.Timestamp]) -> None:
    """Check exact daily Top20 ranks sorted by absolute confirmed return."""

    require_equal(len(frame), 60, "daily ranking rows")
    require(not frame.duplicated(["day", "rank"]).any(), "duplicate day/rank")
    require(not frame.duplicated(["day", "symbol"]).any(), "duplicate day/symbol")
    expected_days = list(days)
    require_equal(sorted(frame["day"].unique().tolist()), sorted(expected_days), "rank days")
    for day, group in frame.groupby("day", sort=True):
        ordered = group.sort_values("rank", kind="stable").reset_index(drop=True)
        require_equal(ordered["rank"].tolist(), list(range(1, 21)), f"{day} ranks")
        expected = ordered.sort_values(
            ["abs_return", "symbol"], ascending=[False, True], kind="stable"
        )["symbol"].tolist()
        require_equal(ordered["symbol"].tolist(), expected, f"{day} Top20 order")
        require_equal(
            set(ordered["eligible_daily_universe"].astype(int)),
            {274},
            f"{day} eligible universe",
        )


def assert_snapshot_contract(
    *,
    out: Path,
    fetch_receipt: dict[str, Any],
    rankings: pd.DataFrame,
    days: list[pd.Timestamp],
) -> dict[str, int]:
    """Verify all selected-symbol snapshot hashes and 15m continuity."""

    snapshot_rows = fetch_receipt["snapshot_files"]
    require_equal(len(snapshot_rows), 50, "snapshot file count")
    require_equal(
        {row["symbol"] for row in snapshot_rows},
        set(rankings["symbol"]),
        "snapshot symbols",
    )
    total_rows = 0
    exact_days = 0
    for record in snapshot_rows:
        symbol = str(record["symbol"])
        path = out / "kline_snapshot" / f"{symbol}.csv"
        require(path.is_file(), f"missing snapshot: {path}")
        require_equal(sha256_file(path), record["sha256"], f"{symbol} snapshot SHA")
        frame = pd.read_csv(path, parse_dates=["open_time"])
        require_equal(len(frame), int(record["rows"]), f"{symbol} row count")
        require_equal(len(frame), 485, f"{symbol} canonical context rows")
        require(frame["open_time"].is_monotonic_increasing, f"{symbol} timestamps unsorted")
        require(not frame["open_time"].duplicated().any(), f"{symbol} duplicate timestamps")
        deltas = frame["open_time"].diff().dropna()
        require((deltas == BAR_DELTA).all(), f"{symbol} has a 15m gap")
        require(np.isfinite(frame[["open", "high", "low", "close", "volume"]]).all().all(),
                f"{symbol} has non-finite OHLCV")
        require((frame["high"] >= frame[["open", "close", "low"]].max(axis=1)).all(),
                f"{symbol} high violates OHLC")
        require((frame["low"] <= frame[["open", "close", "high"]].min(axis=1)).all(),
                f"{symbol} low violates OHLC")
        ranked_days = set(rankings.loc[rankings["symbol"] == symbol, "day"])
        for day in ranked_days:
            count = int(((frame["open_time"] >= day) & (frame["open_time"] < day + pd.Timedelta(days=1))).sum())
            require_equal(count, 96, f"{symbol} {day.date()} bars")
            exact_days += 1
        total_rows += len(frame)
    require_equal(exact_days, 60, "exact contiguous ranked symbol-days")
    require_equal(total_rows, int(fetch_receipt["snapshot_rows_materialized"]), "snapshot total rows")
    return {"snapshot_files": len(snapshot_rows), "snapshot_rows": total_rows, "exact_days": exact_days}


def assert_signal_contract(
    signals: pd.DataFrame,
    rankings: pd.DataFrame,
    days: list[pd.Timestamp],
    *,
    expected_count: int,
) -> dict[str, Any]:
    """Verify mapped geometry, attribution, class semantics, and event spacing."""

    require_equal(len(signals), expected_count, "deduplicated signal count")
    require(not signals.empty, "signals must not be empty")
    keys = rankings[["day", "rank", "symbol", "daily_return"]]
    joined = signals.merge(keys, on=["day", "rank", "symbol"], suffixes=("", "_rank"), how="left")
    require(not joined["daily_return_rank"].isna().any(), "signal missing from ranked board")
    require(np.allclose(joined["daily_return"], joined["daily_return_rank"], rtol=0, atol=1e-12),
            "signal daily return drift")
    require(signals["confidence"].between(0.25, 1.0).all(), "confidence outside [0.25, 1]")
    require(set(signals["class_id"].astype(int)).issubset(CLASS_NAMES), "unknown class id")
    require(
        all(CLASS_NAMES[int(row.class_id)] == row.class_name for row in signals.itertuples()),
        "class id/name mismatch",
    )
    require(set(signals["core_length_bars"].astype(int)).issubset({4, 5, 6, 7}),
            "core length outside 4..7")
    require(set(signals["confirmation_bars"].astype(int)).issubset({3, 4, 5}),
            "confirmation outside 3..5")
    require(set(signals["window_len"].astype(int)).issubset({14, 18, 22}),
            "window length drift")
    require(
        ((signals["core_end_i"] - signals["core_start_i"] + 1) == signals["core_length_bars"]).all(),
        "core index geometry mismatch",
    )
    require(
        ((signals["window_end_i"] - signals["core_end_i"]) == signals["confirmation_bars"]).all(),
        "confirmation index geometry mismatch",
    )
    require(
        ((signals["window_end_i"] - signals["window_start_i"] + 1) == signals["window_len"]).all(),
        "window index geometry mismatch",
    )
    require(
        ((signals["core_end_time"] - signals["core_start_time"]) / BAR_DELTA + 1
         == signals["core_length_bars"]).all(),
        "core timestamp geometry mismatch",
    )
    require(
        ((signals["window_end_time"] - signals["core_end_time"]) / BAR_DELTA
         == signals["confirmation_bars"]).all(),
        "confirmation timestamp geometry mismatch",
    )
    require(set(signals["day"].unique()) == set(days), "signal days drift")
    require(
        ((signals["core_end_time"] >= signals["day"]) &
         (signals["core_end_time"] < signals["day"] + pd.Timedelta(days=1))).all(),
        "signal core end outside ranked UTC day",
    )
    for (day, symbol), group in signals.groupby(["day", "symbol"], sort=True):
        differences = group.sort_values("core_end_i")["core_end_i"].diff().dropna()
        require((differences >= 5).all(), f"dedup gap below five bars: {day} {symbol}")
    class_counts = Counter(signals["class_name"])
    aligned = (
        ((signals["daily_return"] > 0) & (signals["class_name"] == "dense_long"))
        | ((signals["daily_return"] < 0) & (signals["class_name"] == "dense_short"))
    )
    return {
        "signals": len(signals),
        "class_counts": dict(sorted(class_counts.items())),
        "direction_aligned": int(aligned.sum()),
        "direction_aligned_rate": round(float(aligned.mean()), 6),
        "confidence_mean": round(float(signals["confidence"].mean()), 6),
        "confidence_median": round(float(signals["confidence"].median()), 6),
    }


def verify_pngs(scan_receipt: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify committed render bytes and decoded dimensions."""

    records = [scan_receipt["overview"], *scan_receipt["daily_images"]]
    verified: list[dict[str, Any]] = []
    for record in records:
        path = ROOT / record["path"]
        require(path.is_file(), f"missing rendered PNG: {path}")
        require_equal(sha256_file(path), record["sha256"], f"{path.name} SHA")
        require_equal(path.stat().st_size, int(record["size_bytes"]), f"{path.name} bytes")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        require(image is not None, f"could not decode {path}")
        height, width = image.shape[:2]
        require_equal(width, int(record["width"]), f"{path.name} width")
        require_equal(height, int(record["height"]), f"{path.name} height")
        verified.append({"path": record["path"], "sha256": record["sha256"], "width": width, "height": height})
    return verified


def verify(
    *, prereg_path: Path, out: Path, results: Path, output_path: Path
) -> dict[str, Any]:
    """Run every local verification and write one immutable JSON receipt."""

    prereg = load_preregistration(prereg_path)
    verifier_commit = verify_sources_committed(prereg_path)
    fetch_path = results / "fetch_receipt.json"
    scan_path = results / "scan_receipt.json"
    require(fetch_path.is_file() and scan_path.is_file(), "fetch/scan receipt missing")
    fetch_receipt = load_json(fetch_path)
    scan_receipt = load_json(scan_path)
    protocol = prereg["protocol"]
    for label, receipt in (("fetch", fetch_receipt), ("scan", scan_receipt)):
        require_equal(receipt["protocol"], protocol, f"{label} protocol")
        require_equal(receipt["experiment_id"], EXPERIMENT_ID, f"{label} experiment")
        require_equal(int(receipt["holdout_consumption_number_for_this_configuration"]), 1,
                      f"{label} holdout consumption")
    require_equal(scan_receipt["weights_sha256"], prereg["detector"]["weights_sha256"], "weight identity")
    weight_path = ROOT / scan_receipt["weights_path"]
    require(weight_path.is_file(), "frozen weight file missing")
    require_equal(sha256_file(weight_path), scan_receipt["weights_sha256"], "weight file SHA")

    rankings_path = ROOT / fetch_receipt["daily_rankings_path"]
    universe_path = ROOT / fetch_receipt["universe_snapshot_path"]
    signals_path = ROOT / scan_receipt["signals_path"]
    stats_path = ROOT / scan_receipt["scan_stats_path"]
    for path in (rankings_path, universe_path, signals_path, stats_path):
        require(path.is_file(), f"missing local artifact: {path}")
    require_equal(sha256_file(rankings_path), fetch_receipt["daily_rankings_sha256"], "rankings SHA")
    require_equal(sha256_file(universe_path), fetch_receipt["universe_snapshot_sha256"], "universe SHA")
    require_equal(sha256_file(signals_path), scan_receipt["signals_sha256"], "signals SHA")
    require_equal(sha256_file(stats_path), scan_receipt["scan_stats_sha256"], "scan stats SHA")

    days = [utc(value) for value in prereg["calendar"]["complete_days"]]
    rankings = pd.read_csv(rankings_path, parse_dates=["day"])
    rankings["abs_return"] = rankings["daily_return"].abs()
    assert_rank_contract(rankings, days)
    snapshot_summary = assert_snapshot_contract(
        out=out, fetch_receipt=fetch_receipt, rankings=rankings, days=days
    )
    signals = pd.read_csv(
        signals_path,
        parse_dates=["day", "core_start_time", "core_end_time", "window_end_time"],
    )
    signal_summary = assert_signal_contract(
        signals, rankings, days, expected_count=int(scan_receipt["deduplicated_events"])
    )
    require_equal(signal_summary["class_counts"], scan_receipt["class_counts"], "class counts")
    stats = pd.read_csv(stats_path, parse_dates=["day"])
    require_equal(len(stats), 60, "scan stats rows")
    require_equal(int(stats["windows_scored"].sum()), 18180, "windows scored")
    require_equal(int(stats["raw_boxes"].fillna(0).sum()), 500, "raw boxes")
    require_equal(int(stats["accepted_before_dedup"].sum()), 495, "accepted before dedup")
    require_equal(int(stats["dedup_removed"].sum()), 399, "dedup removed")
    require_equal(int(stats["deduplicated_events"].sum()), len(signals), "stats signal total")
    pngs = verify_pngs(scan_receipt)

    require(scan_receipt["ranking_is_post_hoc"] is True, "post-hoc disclosure missing")
    require(scan_receipt["economic_backtest"] is False, "economic backtest flag drift")
    require(scan_receipt["threshold_or_window_retuned"] is False, "retuning flag drift")
    for field in (
        "training_or_tuning", "active_or_frozen_changed", "promoted",
        "deployed", "orders_placed", "production_eligible",
    ):
        require(scan_receipt[field] is False, f"unsafe flag true: {field}")

    require(not output_path.exists(), f"refusing to overwrite verification receipt: {output_path}")
    payload = {
        "protocol": protocol,
        "experiment_id": EXPERIMENT_ID,
        "verifier_commit": verifier_commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_consumption_number_for_this_configuration": 1,
        "network_reads_during_verification": 0,
        "fetch_receipt_sha256": sha256_file(fetch_path),
        "scan_receipt_sha256": sha256_file(scan_path),
        "ranked_symbol_days": len(rankings),
        "ranked_days": [day.isoformat() for day in days],
        "snapshot_summary": snapshot_summary,
        "signal_summary": signal_summary,
        "scan_totals": {
            "windows_scored": 18180,
            "raw_boxes": 500,
            "accepted_before_dedup": 495,
            "dedup_removed": 399,
            "deduplicated_events": len(signals),
        },
        "verified_pngs": pngs,
        "checks_passed": [
            "artifact_hashes",
            "three_complete_utc_top20_boards",
            "live_crypto_instcategory1_universe_receipt",
            "fifty_snapshot_hashes",
            "sixty_exact_96_bar_contiguous_symbol_days",
            "ohlcv_invariants",
            "core4_to_7_and_confirmation3_to_5_geometry",
            "five_bar_event_deduplication",
            "class_and_count_reconciliation",
            "four_png_decode_dimensions_and_hashes",
            "post_hoc_and_non_production_disclosures",
        ],
        "training_or_tuning": False,
        "production_eligible": False,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    results = args.results.resolve()
    output = args.output.resolve() if args.output else results / "qa_receipt.json"
    payload = verify(
        prereg_path=args.prereg.resolve(),
        out=args.out.resolve(),
        results=results,
        output_path=output,
    )
    print(
        f"verification passed: ranked={payload['ranked_symbol_days']} "
        f"events={payload['signal_summary']['signals']} -> {output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
