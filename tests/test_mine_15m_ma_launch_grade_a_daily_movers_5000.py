"""Contracts for the resumable 5,000-event daily-mover mining pipeline."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import mine_15m_ma_launch_grade_a_daily_movers_5000 as mine
from scripts import remote_infer_15m_ma_launch_grade_a_taskpack as worker
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features


def _prereg() -> dict:
    return {
        "detector": {
            "scan_endpoint_extension_after_day_bars": 9,
            "minimum_contiguous_history_bars_at_endpoint": 140,
            "causal_prefilter": {"max_min_six_ma_envelope_atr": 1.5},
        }
    }


def _frame(*, spread: float) -> pd.DataFrame:
    times = pd.date_range("2025-09-28T00:00:00Z", periods=500, freq="15min")
    close = np.linspace(100.0, 101.0, len(times))
    raw = pd.DataFrame(
        {
            "open_time": times,
            "open": close - 0.01,
            "high": close + 0.08,
            "low": close - 0.08,
            "close": close,
            "volume": 100.0,
            "exchange_symbol": "TESTUSDT",
        }
    )
    frame = add_candidate_features(raw)
    for index, column in enumerate(mine.ALL_MA_COLS):
        frame[column] = close + (index - 2.5) * spread
    frame["atr"] = 1.0
    return frame


def _board() -> dict:
    return {
        "source_month": "2025-10",
        "day": "2025-10-01T00:00:00+00:00",
        "exchange_symbol": "TESTUSDT",
        "mover_bucket": "gainer",
        "bucket_rank": 1,
        "rank_label": "G1",
        "board_order": 1,
        "daily_return": 0.2,
        "eligible_symbol_days": 500,
    }


def test_search_months_is_newest_first_and_bounded() -> None:
    payload = {"calendar": {"earliest_month": "2025-08", "latest_month": "2025-10"}}

    assert mine.search_months(payload) == ["2025-10", "2025-09", "2025-08"]


def test_official_preregistration_freezes_5000_without_holdout() -> None:
    payload, gates = mine.load_preregistration(mine.DEFAULT_PREREG)

    assert payload["detector"]["target_novel_review_events_minimum"] == 5000
    assert payload["calendar"]["latest_month"] == "2025-10"
    assert payload["owner_authorization"]["holdout_read_authorized"] is False
    assert payload["safety"]["training"] is False
    assert payload["safety"]["label_or_dataset_mutation"] is False
    assert payload["_protocol_amendment"]["underflow_policy"] == {
        "positive_tail": "take_up_to_5_strictly_positive_returns",
        "negative_tail": "take_up_to_5_strictly_negative_returns",
        "zero_return_backfill": False,
        "opposite_sign_backfill": False,
        "skip_underflow_day": False,
    }
    assert gates == payload["semantic_gate"]["frozen_morphology_gate"]


def test_sign_tail_underflow_keeps_available_strict_signs_without_backfill() -> None:
    day = pd.Timestamp("2024-11-06T00:00:00Z")
    rows = [
        {"exchange_symbol": f"P{index}USDT", "daily_return": value, "day": day.isoformat()}
        for index, value in enumerate([0.5, 0.4, 0.3, 0.2, 0.1, 0.05])
    ]
    rows.extend(
        [
            {"exchange_symbol": "N1USDT", "daily_return": -0.03, "day": day.isoformat()},
            {"exchange_symbol": "N2USDT", "daily_return": -0.01, "day": day.isoformat()},
            {"exchange_symbol": "Z1USDT", "daily_return": 0.0, "day": day.isoformat()},
            {"exchange_symbol": "Z2USDT", "daily_return": 0.0, "day": day.isoformat()},
        ]
    )
    prereg = {"ranking": {"top_gainers_per_day": 5, "top_losers_per_day": 5}}

    selected = mine.build_daily_tail_rows(prereg, month="2024-11", day=day, pool=rows)

    assert [row["rank_label"] for row in selected] == [
        "G1",
        "G2",
        "G3",
        "G4",
        "G5",
        "L1",
        "L2",
    ]
    assert all(float(row["daily_return"]) != 0.0 for row in selected)
    assert len({row["exchange_symbol"] for row in selected}) == 7


def test_headerless_binance_archive_gets_canonical_columns(tmp_path: Path) -> None:
    path = tmp_path / "TESTUSDT-15m-2022-06.zip"
    rows = [
        "1654041600000,8.26,8.33,8.20,8.26,90006.25,1654042499999,744256.0,2497,47567.75,393143.1,0",
        "1654042500000,8.26,8.40,8.21,8.35,80000.00,1654043399999,664000.0,2200,40000.00,332000.0,0",
    ]
    with zipfile.ZipFile(path, "w") as bundle:
        bundle.writestr("TESTUSDT-15m-2022-06.csv", "\n".join(rows) + "\n")

    frame = mine.read_month_archive(path, symbol="TESTUSDT", month="2022-06")

    assert list(frame.columns) == [*mine.prior.CSV_COLUMNS, "exchange_symbol"]
    assert len(frame) == 2
    assert frame.iloc[0]["open_time"] == pd.Timestamp("2022-06-01T00:00:00Z")
    assert frame.iloc[1]["close"] == 8.35


def test_visible_w18_prefilter_accepts_dense_and_rejects_wide() -> None:
    dense, _, dense_stats = mine.build_tasks(
        _prereg(), frames={"TESTUSDT": _frame(spread=0.1)}, rankings=[_board()]
    )
    wide, _, wide_stats = mine.build_tasks(
        _prereg(), frames={"TESTUSDT": _frame(spread=0.4)}, rankings=[_board()]
    )

    assert len(dense) == 105
    assert not wide
    assert dense_stats["prefilter_pass"] == 105
    assert wide_stats["prefilter_reject"] == 105
    assert all(row["window_len"] == 18 for row in dense)


def test_global_dedup_collapses_midnight_cluster_and_keeps_semantic() -> None:
    base = {
        "exchange_symbol": "TESTUSDT",
        "source_month": "2025-10",
        "day": "2025-10-01T00:00:00+00:00",
        "window_end_time": "2025-10-01T23:45:00+00:00",
        "first_detection_bar_open_time": "2025-10-01T23:30:00+00:00",
        "last_detection_bar_open_time": "2025-10-01T23:45:00+00:00",
        "core_end_time": "2025-10-01T23:15:00+00:00",
        "confidence": 0.8,
        "event_peak_confidence": 0.8,
        "candidate_count": 1,
        "semantic_candidate_count": 0,
        "semantic_gate_pass": False,
        "event_has_exact_training_input": False,
        "event_near_training_positive": False,
        "novelty_status": "new_event_review",
        "model_direction": "LONG",
    }
    later = {
        **base,
        "source_month": "2025-11",
        "day": "2025-10-02T00:00:00+00:00",
        "window_end_time": "2025-10-02T00:15:00+00:00",
        "first_detection_bar_open_time": "2025-10-02T00:00:00+00:00",
        "last_detection_bar_open_time": "2025-10-02T00:15:00+00:00",
        "core_end_time": "2025-10-01T23:45:00+00:00",
        "confidence": 0.5,
        "event_peak_confidence": 0.5,
        "semantic_candidate_count": 1,
        "semantic_gate_pass": True,
    }

    events = mine.deduplicate_global_events([base, later], gap_bars=5)

    assert len(events) == 1
    assert events[0]["semantic_gate_pass"] is True
    assert events[0]["confidence"] == 0.5
    assert events[0]["cross_day_cluster_members"] == 2


def test_task_pack_round_trip_is_hash_checked(tmp_path: Path) -> None:
    frame = _frame(spread=0.1)
    frame.loc[:118, "sma120"] = np.nan
    specs, _, _ = mine.build_tasks(
        _prereg(), frames={"TESTUSDT": frame}, rankings=[_board()]
    )
    pack = mine.create_task_pack(
        tmp_path,
        month="2025-10",
        frames={"TESTUSDT": frame},
        specs=specs[:3],
        config_hash="config",
    )

    frames, tasks, receipt = worker.load_pack(
        tmp_path,
        expected_pack_sha256=pack["pack_receipt_sha256"],
    )

    assert list(frames) == ["TESTUSDT"]
    assert len(tasks) == 3
    assert receipt["config_hash"] == "config"
    assert tuple(frames["TESTUSDT"].columns) == worker.FRAME_COLUMNS
    assert np.isnan(frames["TESTUSDT"].iloc[0]["sma120"])


def test_task_pack_rejects_receipt_tamper(tmp_path: Path) -> None:
    frame = _frame(spread=0.1)
    specs, _, _ = mine.build_tasks(
        _prereg(), frames={"TESTUSDT": frame}, rankings=[_board()]
    )
    pack = mine.create_task_pack(
        tmp_path,
        month="2025-10",
        frames={"TESTUSDT": frame},
        specs=specs[:1],
        config_hash="config",
    )
    receipt_path = tmp_path / "pack_receipt.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    payload["task_count"] = 2
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    try:
        worker.load_pack(
            tmp_path,
            expected_pack_sha256=pack["pack_receipt_sha256"],
        )
    except worker.TaskPackError as exc:
        assert "receipt SHA" in str(exc)
    else:
        raise AssertionError("tampered pack receipt was accepted")
