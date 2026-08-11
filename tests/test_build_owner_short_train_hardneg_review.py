"""Tests for causal train-time hard-negative candidate review construction."""

import numpy as np
import pandas as pd

from scripts.build_owner_short_train_hardneg_review import (
    causal_feature_vector,
    mean_knn_distance,
    owner_forbidden_time_intervals,
    select_diverse,
    touches_forbidden,
)
from yoyo.layers.l1_detection.data import add_mas


def _event(event_id: str, block: str, symbol: str, affinity: float) -> dict:
    return {
        "event_id": event_id,
        "candidate_block": block,
        "symbol": symbol,
        "hard_negative_affinity": affinity,
        "event_conf_max": 0.5,
        "decision_time": "2025-01-01T00:00:00Z",
    }


def test_causal_feature_vector_ignores_rows_after_decision() -> None:
    times = pd.date_range("2025-01-01", periods=220, freq="15min", tz="UTC")
    base = np.linspace(100.0, 120.0, len(times))
    frame = pd.DataFrame(
        {
            "open_time": times,
            "open": base,
            "high": base + 1,
            "low": base - 1,
            "close": base + 0.2,
            "volume": 1.0,
        }
    )
    event = {
        "event_id": "e1",
        "window_start_time": times[140].isoformat(),
        "decision_time": times[154].isoformat(),
        "window_len": 15,
        "predicted_core_bars": 5,
        "decision_delay_bars": 3,
        "x1n": 0.3,
        "x2n": 0.7,
        "y1n": 0.2,
        "y2n": 0.5,
    }
    original = causal_feature_vector(event, add_mas(frame))
    changed = frame.copy()
    changed.loc[155:, ["open", "high", "low", "close"]] *= 100
    changed_vector = causal_feature_vector(event, add_mas(changed))

    assert np.allclose(original, changed_vector)


def test_owner_guard_uses_complete_causal_window() -> None:
    sheet = pd.DataFrame(
        [
            {
                "symbol": "ETH_USDT_SWAP",
                "cut_time": "2025-01-01T12:00:00Z",
                "width_bars": 10,
            }
        ]
    )
    forbidden = owner_forbidden_time_intervals(sheet, guard_bars=12)
    touching = {
        "symbol": "ETH_USDT_SWAP",
        "window_start_time": "2025-01-01T08:00:00Z",
        "decision_time": "2025-01-01T10:00:00Z",
    }
    clear = {
        "symbol": "ETH_USDT_SWAP",
        "window_start_time": "2024-12-31T00:00:00Z",
        "decision_time": "2024-12-31T02:00:00Z",
    }

    assert touches_forbidden(touching, forbidden)
    assert not touches_forbidden(clear, forbidden)


def test_knn_distance_is_smallest_for_matching_reference() -> None:
    query = np.asarray([[0.0, 0.0], [10.0, 10.0]])
    reference = np.asarray([[0.0, 0.0], [0.1, 0.1], [9.9, 9.9]])

    distances = mean_knn_distance(query, reference, k=1)

    assert np.allclose(distances, [0.0, np.sqrt(0.02)])


def test_diverse_selection_prefers_one_symbol_per_block(monkeypatch) -> None:
    from scripts import build_owner_short_train_hardneg_review as module

    monkeypatch.setattr(module, "BLOCKS", (("B1", "2025-01-01"), ("B2", "2025-02-01")))
    rows = []
    for block in ("B1", "B2"):
        for index in range(4):
            rows.append(_event(f"{block}-{index}", block, f"S{index}", 10 - index))

    selected = select_diverse(rows, per_block=3)

    assert len(selected) == 6
    assert {row["symbol"] for row in selected if row["candidate_block"] == "B1"} == {"S0", "S1", "S2"}
