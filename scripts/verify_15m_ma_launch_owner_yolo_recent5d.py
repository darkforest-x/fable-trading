#!/usr/bin/env python3
"""Verify the five-day Owner YOLO mover scan without network access.

The verifier binds every receipt and disposable CSV to its SHA, checks five
complete UTC Top20 boards, snapshot continuity and pre-window MA context,
training-supported W18--25/core4--5/confirmation4--6 geometry, five-bar event
deduplication, rendered PNG identity and every no-production safety flag.
It performs no market-data request, training, tuning, promotion or trading
state mutation.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import cv2
import numpy as np
import pandas as pd

from scripts import scan_15m_ma_launch_t3_daily_movers as common
from scripts.scan_15m_ma_launch_owner_yolo_recent5d import (
    DEFAULT_OUT,
    DEFAULT_PREREG,
    DEFAULT_RESULTS,
    EXPECTED_CONFIRMATIONS,
    EXPECTED_CORES,
    EXPECTED_DAYS,
    EXPECTED_WINDOWS,
    EXPERIMENT_ID,
    load_preregistration,
    verify_immutable_inputs,
)


ROOT = Path(__file__).resolve().parents[1]
BAR_DELTA = pd.Timedelta(minutes=15)
CLASS_NAMES = {0: "dense_long", 1: "dense_short"}


class OwnerRecent5dVerificationError(RuntimeError):
    """Fail-closed artifact, data or semantic verification error."""


def require(condition: bool, message: str) -> None:
    """Raise a stable verification error when one contract is false."""

    if not condition:
        raise OwnerRecent5dVerificationError(message)


def equal(actual: Any, expected: Any, label: str) -> None:
    """Require exact equality with a readable mismatch."""

    require(actual == expected, f"{label}: expected {expected!r}, got {actual!r}")


def load_json(path: Path) -> dict[str, Any]:
    """Read one JSON object and reject non-object roots."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(payload, dict), f"{path} must contain a JSON object")
    return payload


def resolve_receipt_path(value: object) -> Path:
    """Resolve a repository-relative receipt path from POSIX or Windows output.

    Official writers now serialize POSIX separators.  The compatibility branch
    keeps the already completed Windows CUDA receipt verifiable without
    rewriting its bytes or invalidating the remote/local SHA audit.
    """

    raw = str(value).replace("\\", "/")
    relative = PurePosixPath(raw)
    require(raw and not relative.is_absolute(), f"receipt path must be relative: {value!r}")
    require(".." not in relative.parts and ":" not in raw, f"unsafe receipt path: {value!r}")
    resolved = ROOT.joinpath(*relative.parts).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as error:
        raise OwnerRecent5dVerificationError(
            f"receipt path escapes repository: {value!r}"
        ) from error
    return resolved


def verify_sources_committed(prereg_path: Path) -> str:
    """Bind verification to committed adapter, shared builder and contract."""

    paths = [
        Path(__file__).resolve().relative_to(ROOT),
        (ROOT / "scripts" / "scan_15m_ma_launch_owner_yolo_recent5d.py").relative_to(ROOT),
        (ROOT / "scripts" / "scan_15m_ma_launch_t3_daily_movers.py").relative_to(ROOT),
        prereg_path.resolve().relative_to(ROOT),
    ]
    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=ROOT, text=True
    ).strip()
    equal(branch, "main", "official verification branch")
    dirty = subprocess.check_output(
        ["git", "status", "--short", "--", *map(str, paths)], cwd=ROOT, text=True
    ).strip()
    require(not dirty, f"verification sources must be committed:\n{dirty}")
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    require(len(commit) == 40, "could not resolve verifier commit")
    return commit


def verify_rankings(frame: pd.DataFrame) -> dict[str, Any]:
    """Check exact five Top20 boards and stable absolute-return ordering."""

    equal(len(frame), 100, "daily ranking rows")
    require(not frame.duplicated(["day", "rank"]).any(), "duplicate day/rank")
    require(not frame.duplicated(["day", "symbol"]).any(), "duplicate day/symbol")
    equal(sorted(frame["day"].unique().tolist()), EXPECTED_DAYS, "ranked days")
    universes: dict[str, int] = {}
    for day, group in frame.groupby("day", sort=True):
        ordered = group.sort_values("rank", kind="stable").reset_index(drop=True)
        equal(ordered["rank"].astype(int).tolist(), list(range(1, 21)), f"{day} ranks")
        expected = ordered.sort_values(
            ["abs_return", "symbol"], ascending=[False, True], kind="stable"
        )["symbol"].tolist()
        equal(ordered["symbol"].tolist(), expected, f"{day} Top20 order")
        universe = set(ordered["eligible_daily_universe"].astype(int))
        require(len(universe) == 1 and next(iter(universe)) >= 20, f"{day} universe drift")
        universes[common.utc(day).isoformat()] = next(iter(universe))
    return {
        "symbol_days": len(frame),
        "unique_symbols": int(frame["symbol"].nunique()),
        "eligible_universe_by_day": universes,
    }


def verify_snapshots(
    *, out: Path, fetch: dict[str, Any], rankings: pd.DataFrame
) -> dict[str, Any]:
    """Verify selected snapshots, day continuity and 120-bar MA warmup context."""

    records = fetch["snapshot_files"]
    equal({str(row["symbol"]) for row in records}, set(rankings["symbol"]), "snapshot symbols")
    total_rows = 0
    exact_days = 0
    minimum_context_bars = 120
    for record in records:
        symbol = str(record["symbol"])
        path = out / "kline_snapshot" / f"{symbol}.csv"
        require(path.is_file(), f"missing snapshot: {path}")
        equal(common.sha256_file(path), record["sha256"], f"{symbol} snapshot SHA")
        frame = pd.read_csv(path, parse_dates=["open_time"])
        equal(len(frame), int(record["rows"]), f"{symbol} rows")
        require(frame["open_time"].is_monotonic_increasing, f"{symbol} timestamps unsorted")
        require(not frame["open_time"].duplicated().any(), f"{symbol} duplicate timestamps")
        require(
            np.isfinite(frame[["open", "high", "low", "close", "volume"]]).all().all(),
            f"{symbol} non-finite OHLCV",
        )
        require(
            (frame["high"] >= frame[["open", "close", "low"]].max(axis=1)).all(),
            f"{symbol} high violates OHLC",
        )
        require(
            (frame["low"] <= frame[["open", "close", "high"]].min(axis=1)).all(),
            f"{symbol} low violates OHLC",
        )
        for day in sorted(rankings.loc[rankings["symbol"] == symbol, "day"].tolist()):
            day_positions = np.flatnonzero(
                (frame["open_time"] >= day)
                & (frame["open_time"] < day + pd.Timedelta(days=1))
            )
            equal(len(day_positions), 96, f"{symbol} {day.date()} bar count")
            require(int(day_positions[0]) >= minimum_context_bars, f"{symbol} lacks MA120 context")
            day_times = frame.iloc[day_positions]["open_time"]
            require(
                (day_times.diff().dropna() == BAR_DELTA).all(),
                f"{symbol} {day.date()} has a 15m gap",
            )
            exact_days += 1
        total_rows += len(frame)
    equal(exact_days, 100, "exact ranked symbol-days")
    equal(total_rows, int(fetch["snapshot_rows_materialized"]), "snapshot total rows")
    return {
        "snapshot_files": len(records),
        "snapshot_rows": total_rows,
        "exact_symbol_days": exact_days,
        "minimum_context_bars": minimum_context_bars,
    }


def verify_signals(signals: pd.DataFrame, rankings: pd.DataFrame) -> dict[str, Any]:
    """Verify mapped label geometry, attribution, classes and dedup spacing."""

    if signals.empty:
        return {
            "signals": 0,
            "class_counts": {},
            "direction_aligned": 0,
            "direction_aligned_rate": None,
        }
    keys = rankings[["day", "rank", "symbol", "daily_return"]]
    joined = signals.merge(keys, on=["day", "rank", "symbol"], suffixes=("", "_rank"), how="left")
    require(not joined["daily_return_rank"].isna().any(), "signal outside ranked board")
    require(
        np.allclose(joined["daily_return"], joined["daily_return_rank"], rtol=0, atol=1e-12),
        "signal daily return drift",
    )
    require(signals["confidence"].between(0.25, 1.0).all(), "confidence outside [0.25,1]")
    require(set(signals["class_id"].astype(int)).issubset(CLASS_NAMES), "unknown class")
    require(
        all(CLASS_NAMES[int(row.class_id)] == row.class_name for row in signals.itertuples()),
        "class id/name mismatch",
    )
    require(
        set(signals["core_length_bars"].astype(int)).issubset(EXPECTED_CORES),
        "core length outside training support",
    )
    require(
        set(signals["confirmation_bars"].astype(int)).issubset(EXPECTED_CONFIRMATIONS),
        "confirmation outside training support",
    )
    require(
        set(signals["window_len"].astype(int)).issubset(EXPECTED_WINDOWS),
        "window length outside training support",
    )
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
        ((signals["core_end_time"] >= signals["day"])
         & (signals["core_end_time"] < signals["day"] + pd.Timedelta(days=1))).all(),
        "core end outside ranked day",
    )
    for (day, symbol), group in signals.groupby(["day", "symbol"], sort=True):
        gaps = group.sort_values("core_end_i")["core_end_i"].diff().dropna()
        require((gaps >= 5).all(), f"dedup gap below five: {day} {symbol}")
    counts = Counter(signals["class_name"])
    aligned = (
        ((signals["daily_return"] > 0) & (signals["class_name"] == "dense_long"))
        | ((signals["daily_return"] < 0) & (signals["class_name"] == "dense_short"))
    )
    return {
        "signals": len(signals),
        "class_counts": dict(sorted(counts.items())),
        "direction_aligned": int(aligned.sum()),
        "direction_aligned_rate": round(float(aligned.mean()), 6),
        "confidence_mean": round(float(signals["confidence"].mean()), 6),
        "confidence_median": round(float(signals["confidence"].median()), 6),
    }


def verify_pngs(scan: dict[str, Any]) -> list[dict[str, Any]]:
    """Verify overview plus five daily render bytes and decoded dimensions."""

    records = [scan["overview"], *scan["daily_images"]]
    equal(len(records), 6, "rendered PNG count")
    verified = []
    for record in records:
        path = resolve_receipt_path(record["path"])
        require(path.is_file(), f"missing PNG: {path}")
        equal(common.sha256_file(path), record["sha256"], f"{path.name} SHA")
        equal(path.stat().st_size, int(record["size_bytes"]), f"{path.name} bytes")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        require(image is not None, f"could not decode {path}")
        height, width = image.shape[:2]
        equal(width, int(record["width"]), f"{path.name} width")
        equal(height, int(record["height"]), f"{path.name} height")
        verified.append({"path": record["path"], "sha256": record["sha256"], "width": width, "height": height})
    return verified


def verify(*, prereg_path: Path, out: Path, results: Path, output: Path) -> dict[str, Any]:
    """Run the complete offline verification and write one immutable receipt."""

    prereg = load_preregistration(prereg_path)
    immutable = verify_immutable_inputs(prereg)
    verifier_commit = verify_sources_committed(prereg_path)
    fetch_path, scan_path = results / "fetch_receipt.json", results / "scan_receipt.json"
    require(fetch_path.is_file() and scan_path.is_file(), "fetch/scan receipt missing")
    fetch, scan = load_json(fetch_path), load_json(scan_path)
    for label, receipt in (("fetch", fetch), ("scan", scan)):
        equal(receipt["protocol"], prereg["protocol"], f"{label} protocol")
        equal(receipt["experiment_id"], EXPERIMENT_ID, f"{label} experiment")
        equal(int(receipt["holdout_consumption_number_for_this_configuration"]), 1, f"{label} holdout use")
    equal(scan["weights_sha256"], prereg["detector"]["weights_sha256"], "weight identity")

    ranking_path = resolve_receipt_path(fetch["daily_rankings_path"])
    universe_path = resolve_receipt_path(fetch["universe_snapshot_path"])
    signals_path = resolve_receipt_path(scan["signals_path"])
    stats_path = resolve_receipt_path(scan["scan_stats_path"])
    for path in (ranking_path, universe_path, signals_path, stats_path):
        require(path.is_file(), f"missing output: {path}")
    equal(common.sha256_file(ranking_path), fetch["daily_rankings_sha256"], "ranking SHA")
    equal(common.sha256_file(universe_path), fetch["universe_snapshot_sha256"], "universe SHA")
    equal(common.sha256_file(signals_path), scan["signals_sha256"], "signals SHA")
    equal(common.sha256_file(stats_path), scan["scan_stats_sha256"], "scan stats SHA")

    rankings = pd.read_csv(ranking_path, parse_dates=["day"])
    rankings["abs_return"] = rankings["daily_return"].abs()
    ranking_summary = verify_rankings(rankings)
    snapshot_summary = verify_snapshots(out=out, fetch=fetch, rankings=rankings)
    signals = pd.read_csv(
        signals_path,
        parse_dates=["day", "core_start_time", "core_end_time", "window_end_time"],
    )
    signal_summary = verify_signals(signals, rankings)
    equal(signal_summary["signals"], int(scan["deduplicated_events"]), "signal total")
    equal(signal_summary["class_counts"], scan["class_counts"], "class counts")
    stats = pd.read_csv(stats_path, parse_dates=["day"])
    equal(len(stats), 100, "scan stats rows")
    equal(int(stats["deduplicated_events"].sum()), len(signals), "stats signal total")
    scan_totals = {
        key: int(stats[key].fillna(0).sum())
        for key in (
            "windows_scored",
            "windows_with_any_box",
            "raw_boxes",
            "accepted_structural_boxes",
            "accepted_before_dedup",
            "dedup_removed",
            "deduplicated_events",
        )
        if key in stats
    }
    equal(scan_totals["windows_scored"], 81_600, "complete five-day inference windows")
    pngs = verify_pngs(scan)

    require(scan["ranking_is_post_hoc"] is True, "post-hoc disclosure missing")
    require(scan["economic_backtest"] is False, "economic flag drift")
    require(scan["threshold_or_window_retuned"] is False, "retuning flag drift")
    for field in (
        "training_or_tuning",
        "active_or_frozen_changed",
        "promoted",
        "deployed",
        "orders_placed",
        "production_eligible",
    ):
        require(scan[field] is False, f"unsafe flag true: {field}")
    require(not output.exists(), f"refusing to overwrite QA receipt: {output}")
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "verifier_commit": verifier_commit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdout_consumption_number_for_this_configuration": 1,
        "network_reads_during_verification": 0,
        "immutable_inputs": immutable,
        "fetch_receipt_sha256": common.sha256_file(fetch_path),
        "scan_receipt_sha256": common.sha256_file(scan_path),
        "ranking_summary": ranking_summary,
        "snapshot_summary": snapshot_summary,
        "signal_summary": signal_summary,
        "scan_totals": scan_totals,
        "verified_pngs": pngs,
        "checks_passed": [
            "artifact_hashes",
            "five_complete_utc_top20_boards",
            "live_crypto_instcategory1_universe",
            "one_hundred_exact_96_bar_symbol_days",
            "ma120_precontext",
            "ohlcv_invariants",
            "training_supported_w18_to_25_core4_to_5_confirmation4_to_6",
            "five_bar_event_deduplication",
            "six_png_hash_and_decode",
            "post_hoc_and_non_production_disclosures",
        ],
        "training_or_tuning": False,
        "production_eligible": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    """Parse paths, run offline verification and print the bounded summary."""

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
        output=output,
    )
    print(
        f"verification passed: boards={payload['ranking_summary']['symbol_days']} "
        f"events={payload['signal_summary']['signals']} -> {output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
