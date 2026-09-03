"""Contract tests for the pre-holdout Grade-A daily-mover mining scan."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scripts import scan_15m_ma_launch_grade_a_daily_movers as scan
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features


def test_preregistration_is_preholdout_and_frozen() -> None:
    prereg, gates = scan.load_preregistration(scan.DEFAULT_PREREG)

    assert prereg["calendar"]["end_exclusive"] == "2025-11-01T00:00:00Z"
    assert prereg["owner_authorization"]["holdout_read_authorized"] is False
    assert prereg["safety"]["training"] is False
    assert prereg["safety"]["label_or_dataset_mutation"] is False
    assert gates == prereg["semantic_gate"]["frozen_morphology_gate"]


def test_preregistration_rejects_holdout_boundary(tmp_path: Path) -> None:
    payload = json.loads(scan.DEFAULT_PREREG.read_text(encoding="utf-8"))
    payload["calendar"]["end_exclusive"] = "2026-05-05T00:00:00Z"
    path = tmp_path / "preregistration.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(scan.DailyMoversError, match="calendar end"):
        scan.load_preregistration(path)


def test_exact_day_requires_all_96_ordered_bars() -> None:
    day = pd.Timestamp("2025-10-04T00:00:00Z")
    frame = pd.DataFrame({"open_time": pd.date_range(day, periods=96, freq="15min")})

    assert scan._is_exact_day(frame, day)
    assert not scan._is_exact_day(frame.drop(index=20), day)


def test_daily_board_balances_tails_and_ties_by_symbol() -> None:
    rows = [
        {"exchange_symbol": f"P{index:02d}USDT", "daily_return": value}
        for index, value in enumerate([0.4, 0.3, 0.2, 0.1, 0.05, 0.01])
    ] + [
        {"exchange_symbol": f"N{index:02d}USDT", "daily_return": value}
        for index, value in enumerate([-0.4, -0.3, -0.2, -0.1, -0.05, -0.01])
    ]
    rows.extend(
        [
            {"exchange_symbol": "AAAUSDT", "daily_return": 0.5},
            {"exchange_symbol": "AABUSDT", "daily_return": 0.5},
        ]
    )

    gainers, losers = scan.select_daily_board(rows, gainers=5, losers=5)

    assert [row["exchange_symbol"] for row in gainers[:2]] == ["AAAUSDT", "AABUSDT"]
    assert len(gainers) == len(losers) == 5
    assert all(float(row["daily_return"]) > 0 for row in gainers)
    assert all(float(row["daily_return"]) < 0 for row in losers)
    assert float(losers[0]["daily_return"]) == -0.4


def _feature_frame() -> pd.DataFrame:
    times = pd.date_range("2025-09-28T00:00:00Z", periods=500, freq="15min")
    close = np.linspace(10.0, 12.0, len(times)) + 0.01 * np.sin(np.arange(len(times)))
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": close - 0.01,
            "high": close + 0.04,
            "low": close - 0.04,
            "close": close,
            "volume": np.full(len(times), 100.0),
            "exchange_symbol": "TESTUSDT",
        }
    )
    return add_candidate_features(frame)


def test_task_builder_scores_only_bounded_day_endpoints() -> None:
    prereg, _ = scan.load_preregistration(scan.DEFAULT_PREREG)
    board = {
        "day": "2025-10-01T00:00:00+00:00",
        "exchange_symbol": "TESTUSDT",
        "mover_bucket": "gainer",
        "bucket_rank": 1,
        "rank_label": "G1",
        "board_order": 1,
        "daily_return": 0.2,
        "eligible_symbol_days": 100,
    }

    tasks, audits = scan.build_tasks(prereg, frames={"TESTUSDT": _feature_frame()}, rankings=[board])

    assert len(tasks) == (96 + 9) * 2
    assert audits[0]["scannable"] is True
    _, _, first = tasks[0]
    _, _, last = tasks[-1]
    assert scan.utc(first["window_end_time"]) == pd.Timestamp("2025-10-01T00:00:00Z")
    assert scan.utc(last["window_end_time"]) == pd.Timestamp("2025-10-02T02:00:00Z")
    assert scan.utc(last["window_end_time"]) < pd.Timestamp("2025-10-02T02:15:00Z")


def test_overlap_requires_coordinates_and_decoded_pixels(monkeypatch: pytest.MonkeyPatch) -> None:
    end = pd.Timestamp("2025-10-10T04:15:00Z")
    start = end - 17 * scan.BAR_DELTA
    exact_key = ("TESTUSDT", start.isoformat(), end.isoformat(), 18)
    training = {
        "exact": {
            exact_key: [
                {
                    "sample_kind": "negative",
                    "split": "train",
                    "dataset_sample_id": "sample-1",
                    "event_id": "",
                    "image_path": "images/train/sample.png",
                    "image_sha256": "unused",
                }
            ]
        },
        "positive_events": {
            ("TESTUSDT", "LONG"): [
                {"event_id": "positive-1", "core_end_time": "2025-10-10T03:00:00+00:00", "split": "train"}
            ]
        },
    }
    monkeypatch.setattr(scan, "_training_image_pixel_hash", lambda _: "pixel-hash")
    candidate = {
        "exchange_symbol": "TESTUSDT",
        "window_end_time": end.isoformat(),
        "window_len": 18,
        "input_pixel_sha256": "pixel-hash",
        "class_id": 0,
        "core_end_time": "2025-10-10T03:15:00+00:00",
    }

    [result] = scan.annotate_training_overlap([candidate], training)

    assert result["exact_training_input_matches"] == 1
    assert result["near_training_positive_event"] is True
    assert result["novelty_status"] == "exact_training_input"


def test_review_event_prefers_semantic_candidate_within_cluster() -> None:
    base_row = {
        "day": "2025-10-10T00:00:00+00:00",
        "exchange_symbol": "TESTUSDT",
        "symbol": "TESTUSDT",
        "mover_bucket": "gainer",
        "rank_label": "G1",
        "daily_return": 0.2,
        "class_id": 0,
        "class_name": "dense_long",
        "window_len": 18,
        "core_length_bars": 4,
        "confirmation_bars": 2,
        "input_pixel_sha256": "hash",
        "exact_training_input_matches": 0,
        "near_training_positive_event": False,
        "novelty_status": "new_event_review",
    }
    rows = [
        {
            **base_row,
            "core_end_i": 200,
            "core_end_time": "2025-10-10T03:00:00+00:00",
            "window_end_time": "2025-10-10T03:30:00+00:00",
            "confidence": 0.8,
            "semantic_gate_pass": False,
        },
        {
            **base_row,
            "core_end_i": 201,
            "core_end_time": "2025-10-10T03:15:00+00:00",
            "window_end_time": "2025-10-10T03:45:00+00:00",
            "confidence": 0.5,
            "semantic_gate_pass": True,
        },
    ]

    events = scan.deduplicate_review_events(rows, gap_bars=5)

    assert len(events) == 1
    assert events[0]["semantic_gate_pass"] is True
    assert events[0]["confidence"] == 0.5
    assert events[0]["review_bucket"] == "candidate_positive"
    assert events[0]["direction_matches_completed_day"] is True
