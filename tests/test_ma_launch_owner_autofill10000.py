from __future__ import annotations

from collections import Counter

import cv2
import numpy as np
import pandas as pd

import yoyo.datasets.ma_launch_owner_autofill10000 as autofill
from yoyo.datasets.ma_launch_owner_autofill10000 import (
    _sign_test_p_string,
    event_nms,
    select_balanced,
)


def _row(
    *,
    symbol: str,
    direction: str,
    stamp: pd.Timestamp,
    distance: float,
    event_id: str,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "direction": direction,
        "core_end_time": stamp.isoformat(),
        "similarity_distance": distance,
        "event_id": event_id,
        "core_bars": 4,
    }


def test_event_nms_keeps_best_adjacent_event_and_separates_one_hour() -> None:
    start = pd.Timestamp("2024-01-01T00:00:00Z")
    rows = [
        _row(
            symbol="BTC_USDT_SWAP",
            direction="LONG",
            stamp=start + pd.Timedelta(minutes=minutes),
            distance=distance,
            event_id=str(minutes),
        )
        for minutes, distance in ((0, 0.45), (15, 0.25), (60, 0.35))
    ]
    selected = event_nms(rows, gap_bars=4)
    assert [row["event_id"] for row in selected] == ["15", "60"]


def test_balanced_selection_fills_quantiles_and_quotas() -> None:
    start = pd.Timestamp("2022-01-01T00:00:00Z")
    rows = []
    for direction in ("LONG", "SHORT"):
        for index in range(6_000):
            rows.append(
                _row(
                    symbol=f"S{index % 100:03d}_USDT_SWAP",
                    direction=direction,
                    stamp=start + pd.Timedelta(hours=index),
                    distance=0.1 + (index % 997) / 10_000,
                    event_id=f"{direction}-{index}",
                )
            )
    contract = {
        "total": 10_000,
        "target_per_side": 5_000,
        "time_bins_per_side": 10,
        "minimum_per_time_bin": 300,
        "max_per_symbol_per_side": 80,
        "max_per_utc_day_per_side": 80,
    }
    selected = select_balanced(rows, contract)
    assert len(selected) == 10_000
    assert Counter(row["direction"] for row in selected) == {"LONG": 5_000, "SHORT": 5_000}
    for direction in ("LONG", "SHORT"):
        side = [row for row in selected if row["direction"] == direction]
        bin_counts = Counter(row["time_bin"] for row in side)
        assert set(bin_counts) == set(range(10))
        assert min(bin_counts.values()) >= 300
        assert max(Counter(row["symbol"] for row in side).values()) <= 80


def test_sign_test_formats_tiny_exact_probability() -> None:
    assert _sign_test_p_string(10, 10) == "1.95312500E-3"


def test_contact_sheet_reads_atomic_building_path_without_final_file(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(autofill, "ROOT", tmp_path)
    final_dir = tmp_path / "experiment" / "results"
    building_image = (
        tmp_path
        / "experiment"
        / "results.building"
        / "public"
        / "images"
        / "sample.png"
    )
    building_image.parent.mkdir(parents=True)
    source = np.full((90, 160, 3), 255, dtype=np.uint8)
    cv2.rectangle(source, (60, 25), (100, 70), (0, 0, 255), 2)
    assert cv2.imwrite(str(building_image), source)
    contact = autofill._sample_contact_sheet(
        [
            {
                "image_path": "experiment/results/public/images/sample.png",
                "source_order": 1,
                "symbol": "BTC_USDT_SWAP",
                "direction": "LONG",
            }
        ],
        final_dir,
    )
    assert contact.shape == (1800, 2560, 3)


def test_balanced_selection_keeps_distinct_symbols_in_same_hour() -> None:
    start = pd.Timestamp("2022-01-01T00:00:00Z")
    rows = []
    for direction in ("LONG", "SHORT"):
        for hour in range(2):
            for symbol_index in range(2):
                rows.append(
                    _row(
                        symbol=f"S{symbol_index}_USDT_SWAP",
                        direction=direction,
                        stamp=start + pd.Timedelta(hours=hour),
                        distance=0.1 + symbol_index / 100,
                        event_id=f"{direction}-{hour}-{symbol_index}",
                    )
                )
    selected = select_balanced(
        rows,
        {
            "total": 8,
            "target_per_side": 4,
            "time_bins_per_side": 2,
            "minimum_per_time_bin": 1,
            "max_per_symbol_per_side": 4,
            "max_per_utc_day_per_side": 8,
        },
    )
    assert len(selected) == 8
    assert max(
        Counter(
            (row["direction"], pd.Timestamp(row["core_end_time"]).floor("h"))
            for row in selected
        ).values()
    ) == 2
