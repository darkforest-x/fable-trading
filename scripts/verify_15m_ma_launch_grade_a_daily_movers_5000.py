#!/usr/bin/env python3
"""Offline verifier for the 5,000-event Grade-A daily-mover mining run.

The verifier never calls the detector or a network endpoint.  It re-hashes all
source archives, reconstructs daily Top5/Top5 boards from their stored complete
universe rows, replays every retained W18 input pixel and semantic decision,
recomputes training overlap and both per-day/global event de-duplication, and
checks every delivered chart.  It does not mutate labels or datasets, train,
promote, deploy, or read holdout OHLCV.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np
import pandas as pd

from scripts import mine_15m_ma_launch_grade_a_daily_movers_5000 as mine
from scripts import scan_15m_ma_launch_grade_a_daily_movers as prior
from scripts import scan_crypto_grade_a_yolo_mtf_latest as latest
from yoyo.layers.l1_detection.data import ALL_MA_COLS


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = mine.DEFAULT_OUT


class VerificationError(RuntimeError):
    """Fail closed when an artifact cannot be independently reproduced."""


def assert_equal(actual: Any, expected: Any, label: str) -> None:
    """Raise one concise equality failure."""

    if actual != expected:
        raise VerificationError(f"{label}: {actual!r} != {expected!r}")


def verify_source_manifest(out: Path, summary: Mapping[str, Any]) -> int:
    """Hash every frozen archive and reject any holdout timestamp."""

    manifest = json.loads((out / "source_manifest.json").read_text(encoding="utf-8"))
    assert_equal(manifest["network_market_reads"], 0, "source network reads")
    assert_equal(manifest["holdout_ohlcv_rows_materialized"], 0, "source holdout rows")
    rows = list(manifest["archives"])
    for row in rows:
        path = ROOT / str(row["path"])
        if not path.is_file():
            raise VerificationError(f"source archive missing: {path}")
        assert_equal(mine.sha256_file(path), str(row["sha256"]), f"source SHA {path}")
        if mine.utc(row["last_bar_open"]) >= mine.HOLDOUT_START:
            raise VerificationError(f"source reached holdout: {path}")
    assert_equal(len({str(row["path"]) for row in rows}), len(rows), "source path uniqueness")
    return len(rows)


def rebuild_rankings(shard: Path, prereg: Mapping[str, Any], month: str) -> int:
    """Rebuild one month's Top5/Top5 boards from stored complete universe rows."""

    universe = pd.read_csv(shard / "universe_daily_returns.csv")
    recorded = pd.read_csv(shard / "daily_rankings.csv")
    gain_n = int(prereg["ranking"]["top_gainers_per_day"])
    loss_n = int(prereg["ranking"]["top_losers_per_day"])
    rebuilt: list[dict[str, Any]] = []
    for day, part in universe.groupby("day", sort=True):
        rows = part.to_dict("records")
        gainers, losers = prior.select_daily_board(rows, gainers=gain_n, losers=loss_n)
        for bucket, selected in (("gainer", gainers), ("loser", losers)):
            for rank, row in enumerate(selected, 1):
                rebuilt.append(
                    {
                        "day": str(day),
                        "exchange_symbol": str(row["exchange_symbol"]),
                        "mover_bucket": bucket,
                        "bucket_rank": rank,
                        "daily_return": float(row["daily_return"]),
                    }
                )
    expected = recorded.loc[
        :, ["day", "exchange_symbol", "mover_bucket", "bucket_rank", "daily_return"]
    ].to_dict("records")
    assert_equal(len(rebuilt), len(expected), f"ranking count {month}")
    for index, (left, right) in enumerate(zip(rebuilt, expected)):
        for key in ("day", "exchange_symbol", "mover_bucket", "bucket_rank"):
            assert_equal(left[key], right[key], f"ranking {month} row {index} {key}")
        if not np.isclose(left["daily_return"], float(right["daily_return"]), rtol=0, atol=1e-15):
            raise VerificationError(f"ranking return drift {month} row {index}")
    return len(rebuilt)


def compare_semantics(actual: Mapping[str, Any], expected: Mapping[str, Any], label: str) -> None:
    """Compare every frozen actual/flipped semantic field."""

    keys = (
        "semantic_gate_pass",
        "semantic_checks",
        "semantic_failed_checks",
        "semantic_features",
        "flipped_semantic_gate_pass",
        "flipped_semantic_checks",
        "flipped_semantic_failed_checks",
        "flipped_semantic_features",
        "causal_feature_last_i",
        "input_pixel_replay_sha256",
    )
    for key in keys:
        assert_equal(actual[key], expected[key], f"{label} {key}")


def verify_month(
    shard: Path,
    *,
    prereg: Mapping[str, Any],
    gates: Mapping[str, Any],
    training: Mapping[str, Any],
    month: str,
    config_hash: str,
) -> dict[str, int]:
    """Replay one monthly shard without model inference."""

    shard_summary = mine.load_completed_shard(shard, month=month, config_hash=config_hash)
    ranking_count = rebuild_rankings(shard, prereg, month)
    decisions = mine.read_jsonl(shard / "semantic_decisions.jsonl")
    task_specs = mine.read_jsonl(shard / "task_specs.jsonl")
    specs_by_id = {str(row["task_id"]): row for row in task_specs}
    symbols = sorted({str(row["exchange_symbol"]) for row in decisions})
    frames, _ = mine.load_selected_frames(
        prereg,
        month=month,
        archive_root=ROOT / prereg["data"]["archive_root"],
        symbols=symbols,
    )

    unique_tasks: dict[str, str] = {}
    for row in decisions:
        task_id = str(row["task_id"])
        spec = specs_by_id.get(task_id)
        if spec is None:
            raise VerificationError(f"decision task missing: {month} {task_id}")
        frame = frames[str(row["exchange_symbol"])]
        window = frame.iloc[int(row["window_start_i"]) : int(row["window_end_i"]) + 1]
        assert_equal(len(window), 18, f"W18 length {task_id}")
        ma = window.loc[:, list(ALL_MA_COLS)].to_numpy(dtype=float)
        atr = window["atr"].to_numpy(dtype=float)
        minimum = float(np.min((ma.max(axis=1) - ma.min(axis=1)) / atr))
        if minimum > 1.5:
            raise VerificationError(f"prefilter drift: {task_id} {minimum}")
        if not np.isclose(
            minimum,
            float(spec["prefilter_min_six_ma_envelope_atr"]),
            rtol=0,
            atol=1e-12,
        ):
            raise VerificationError(f"prefilter value drift: {task_id}")
        if task_id not in unique_tasks:
            image, _ = mine.render_chart(window, out_path=None)
            digest = mine.pixel_sha256(image)
            assert_equal(digest, str(row["input_pixel_sha256"]), f"input pixels {task_id}")
            unique_tasks[task_id] = digest

    overlap = prior.annotate_training_overlap(decisions, training)
    overlap_keys = (
        "exact_training_coordinate_matches",
        "exact_training_input_matches",
        "exact_training_sample_kinds",
        "exact_training_splits",
        "exact_training_sample_ids",
        "near_training_positive_event",
        "nearest_training_positive_event_id",
        "nearest_training_positive_core_end_distance_bars",
        "novelty_status",
    )
    for index, (recorded, replayed) in enumerate(zip(decisions, overlap)):
        for key in overlap_keys:
            assert_equal(recorded[key], replayed[key], f"overlap {month} {index} {key}")

    semantic_replay = latest.evaluate_semantic_candidates(
        decisions,
        frames,
        gates,
        timeframe="15m",
    )
    for index, (recorded, replayed) in enumerate(zip(decisions, semantic_replay)):
        compare_semantics(recorded, replayed, f"semantic {month} {index}")

    recorded_events = mine.read_jsonl(shard / "events.jsonl")
    replayed_events = prior.deduplicate_review_events(
        semantic_replay,
        gap_bars=int(prereg["detector"]["same_symbol_event_gap_bars"]),
    )
    for event in replayed_events:
        event["source_month"] = month
        event["event_id"] = (
            f"mover5000_{month.replace('-', '')}_{event['exchange_symbol']}_"
            f"{mine.utc(event['core_end_time']):%Y%m%dT%H%M}"
        )
    identity = lambda row: (
        str(row["event_id"]),
        str(row["input_pixel_sha256"]),
        str(row["novelty_status"]),
        bool(row["semantic_gate_pass"]),
        int(row["candidate_count"]),
    )
    assert_equal(
        [identity(row) for row in replayed_events],
        [identity(row) for row in recorded_events],
        f"monthly events {month}",
    )
    return {
        "ranking_rows": ranking_count,
        "task_specs": len(task_specs),
        "semantic_decisions": len(decisions),
        "unique_input_pixels": len(unique_tasks),
        "monthly_events": len(recorded_events),
        "source_artifacts": len(shard_summary["artifact_sha256"]),
    }


def verify_final_queue(
    out: Path,
    months: Sequence[str],
    prereg: Mapping[str, Any],
) -> tuple[int, int]:
    """Rebuild cross-day/month de-duplication and verify every delivered chart."""

    per_day = mine.load_all_events(out / "shards", months)
    replayed = mine.deduplicate_global_events(per_day, gap_bars=5)
    recorded = mine.read_jsonl(out / "review_queue.jsonl")
    assert_equal(len(replayed), len(recorded), "global event count")
    keys = (
        "event_id",
        "source_month",
        "exchange_symbol",
        "core_end_time",
        "input_pixel_sha256",
        "novelty_status",
        "semantic_gate_pass",
        "candidate_count",
        "review_rank",
    )
    for index, (left, right) in enumerate(zip(replayed, recorded)):
        for key in keys:
            assert_equal(left[key], right[key], f"global event {index} {key}")

    chart_checks = 0
    archive_root = ROOT / str(prereg["data"]["archive_root"])
    for month in months:
        month_rows = [row for row in recorded if str(row["source_month"]) == month]
        symbols = sorted({str(row["exchange_symbol"]) for row in month_rows})
        frames, _ = mine.load_selected_frames(
            prereg,
            month=month,
            archive_root=archive_root,
            symbols=symbols,
        )
        for right in month_rows:
            index = int(right["review_rank"]) - 1
            chart = out / str(right["model_input_chart"])
            if not chart.is_file():
                raise VerificationError(f"delivered chart missing: {chart}")
            assert_equal(
                mine.sha256_file(chart),
                str(right["model_input_chart_sha256"]),
                f"delivered chart SHA {index}",
            )
            delivered = cv2.imread(str(chart), cv2.IMREAD_COLOR)
            expected = prior.render_exact_model_input(
                right,
                frames[str(right["exchange_symbol"])],
            )
            if delivered is None or not np.array_equal(delivered, expected):
                raise VerificationError(f"delivered chart pixels drifted: {chart}")
            chart_checks += 1
        print(f"verified delivered charts {month} count={len(month_rows)}", flush=True)
    novel = sum(row["novelty_status"] == "new_event_review" for row in recorded)
    return novel, chart_checks


def run(out: Path, prereg_path: Path) -> dict[str, Any]:
    """Run the complete offline verification and publish its receipt."""

    prereg, gates = mine.load_preregistration(prereg_path)
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    config_hash = mine.sha256_file(prereg_path)
    assert_equal(summary["config_hash"], config_hash, "summary config hash")
    assert_equal(summary["holdout_consumed"], False, "summary holdout")
    assert_equal(summary["network_market_reads"], 0, "summary network reads")
    assert_equal(summary["trained"], False, "summary trained")
    assert_equal(summary["automatic_gold_or_label_mutation"], False, "summary label mutation")
    months = list(map(str, summary["months_newest_first"]))
    expected_search = mine.search_months(prereg)[: len(months)]
    assert_equal(months, expected_search, "contiguous newest-first months")

    source_archives = verify_source_manifest(out, summary)
    training = prior.load_training_index(prereg)
    totals: Counter[str] = Counter()
    for month in months:
        totals.update(
            verify_month(
                out / "shards" / month,
                prereg=prereg,
                gates=gates,
                training=training,
                month=month,
                config_hash=config_hash,
            )
        )
        print(f"verified month {month}", flush=True)

    novel, chart_checks = verify_final_queue(out, months, prereg)
    target = int(prereg["detector"]["target_novel_review_events_minimum"])
    if novel < target:
        raise VerificationError(f"novel target failed: {novel} < {target}")
    if len(months) > 1:
        prior_events = mine.deduplicate_global_events(
            mine.load_all_events(out / "shards", months[:-1]), gap_bars=5
        )
        prior_novel = sum(row["novelty_status"] == "new_event_review" for row in prior_events)
        if prior_novel >= target:
            raise VerificationError(
                f"stopping rule did not stop at first full-month boundary: {prior_novel}"
            )
    else:
        prior_novel = 0

    receipt = {
        "schema_version": 1,
        "experiment_id": mine.EXPERIMENT_ID,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "passed": True,
        "months": len(months),
        "first_month": months[-1],
        "last_month": months[0],
        "source_archive_sha_checks": source_archives,
        **dict(totals),
        "global_novel_events": novel,
        "prior_month_boundary_novel_events": prior_novel,
        "delivered_chart_sha_and_dimension_checks": chart_checks,
        "model_inference_calls": 0,
        "network_market_reads": 0,
        "holdout_ohlcv_rows_read": 0,
        "labels_or_dataset_mutated": False,
        "trained": False,
        "promoted": False,
        "deployed": False,
        "trading_state_changed": False,
    }
    mine.write_json(out / "verification.json", receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--prereg", type=Path, default=mine.DEFAULT_PREREG)
    args = parser.parse_args()
    receipt = run(args.out.resolve(), args.prereg.resolve())
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
