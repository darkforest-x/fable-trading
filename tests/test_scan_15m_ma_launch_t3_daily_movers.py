"""Contract tests for the bounded t-3 daily-mover visual scan."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from scripts.scan_15m_ma_launch_t3_daily_movers import (
    DEFAULT_PREREG,
    deduplicate_hits,
    eligible_instruments,
    load_preregistration,
    map_prediction_to_core,
    parse_confirmed_candles,
    rank_daily_rows,
)
from yoyo.datasets.ma_launch_t3_training import yolo_box_from_core
from yoyo.layers.l1_detection.data import add_mas
from yoyo.layers.l1_detection.render import render_chart


def test_official_preregistration_is_frozen() -> None:
    payload = load_preregistration(DEFAULT_PREREG)
    assert payload["calendar"]["complete_days"] == [
        "2026-08-23T00:00:00Z",
        "2026-08-24T00:00:00Z",
        "2026-08-25T00:00:00Z",
    ]
    assert payload["detector"]["window_lengths"] == [14, 18, 22]
    assert payload["detector"]["confidence"] == 0.25
    assert payload["safety"]["production_eligible"] is False


def test_universe_excludes_project_blocked_and_stockish() -> None:
    rows = [
        {"instId": "BTC-USDT-SWAP", "last": "100"},
        {"instId": "USDC-USDT-SWAP", "last": "1"},
        {"instId": "NVDA-USDT-SWAP", "last": "10"},
        {"instId": "ETH-USDC-SWAP", "last": "10"},
        {"instId": "BAD-USDT-SWAP", "last": "0"},
    ]
    assert eligible_instruments(rows) == ["BTC-USDT-SWAP"]


def test_daily_ranking_uses_absolute_return_and_symbol_tie_break() -> None:
    day = pd.Timestamp("2026-08-23T00:00:00Z")
    rows = {
        "A": {day: {"symbol": "A_USDT_SWAP", "daily_return": 0.10}},
        "B": {day: {"symbol": "B_USDT_SWAP", "daily_return": -0.30}},
        "C": {day: {"symbol": "C_USDT_SWAP", "daily_return": 0.30}},
    }
    ranked = rank_daily_rows(rows, [day], top=3)
    assert [row["symbol"] for row in ranked] == [
        "B_USDT_SWAP",
        "C_USDT_SWAP",
        "A_USDT_SWAP",
    ]
    assert [row["rank"] for row in ranked] == [1, 2, 3]


def test_confirmed_parser_is_bounded_and_drops_open_bar() -> None:
    start = pd.Timestamp("2026-08-23T00:00:00Z")
    end = start + pd.Timedelta(hours=1)
    rows = []
    for number, minute in enumerate((-15, 0, 15, 30, 60)):
        ts = int((start + pd.Timedelta(minutes=minute)).timestamp() * 1000)
        confirm = "0" if minute == 30 else "1"
        rows.append([str(ts), "1", "2", "0.5", str(1 + number / 10), "10", "0", "0", confirm])
    frame = parse_confirmed_candles(rows, target_start=start, target_end=end)
    assert frame["open_time"].tolist() == [start, start + pd.Timedelta(minutes=15)]


def test_exact_training_box_maps_back_to_same_core_and_confirmation() -> None:
    count = 18
    base = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-08-23", periods=160, freq="15min", tz="UTC"),
            "open": [100 + index * 0.01 for index in range(160)],
            "high": [101 + index * 0.01 for index in range(160)],
            "low": [99 + index * 0.01 for index in range(160)],
            "close": [100.2 + index * 0.01 for index in range(160)],
            "volume": [1000.0] * 160,
        }
    )
    window = add_mas(base).iloc[-count:].reset_index(drop=True)
    _, transform = render_chart(window, out_path=None)
    box = yolo_box_from_core(transform, window, 8, 13)
    mapped = map_prediction_to_core(
        cx=box[0],
        width=box[2],
        transform=transform,
        window_start_i=100,
        window_end_i=117,
    )
    assert mapped == {
        "core_start_i": 108,
        "core_end_i": 113,
        "core_length_bars": 6,
        "confirmation_bars": 4,
        "core_start_local": 8,
        "core_end_local": 13,
    }


def test_dedup_keeps_highest_confidence_within_five_bars() -> None:
    hits = [
        {"core_end_i": 100, "confidence": 0.40, "class_name": "dense_long"},
        {"core_end_i": 103, "confidence": 0.80, "class_name": "dense_short"},
        {"core_end_i": 108, "confidence": 0.55, "class_name": "dense_long"},
    ]
    kept = deduplicate_hits(hits, gap_bars=5)
    assert [(row["core_end_i"], row["confidence"]) for row in kept] == [
        (103, 0.80),
        (108, 0.55),
    ]


def test_script_and_prereg_paths_are_inside_repository() -> None:
    assert isinstance(DEFAULT_PREREG, Path)
    assert DEFAULT_PREREG.is_file()
