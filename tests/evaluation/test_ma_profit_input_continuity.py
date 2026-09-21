"""Known-input continuity gates for MA-profit labelling and rendering."""
from __future__ import annotations

import pandas as pd
import pytest

from yoyo.datasets.ma_profit_dataset import ProfitDatasetError, event_assets
from yoyo.datasets.ma_profit_pipeline import split_for_event


MINUTES, CORE_END = 3, 1250


def frame(n: int = 1280) -> pd.DataFrame:
    base = [100.0 + index * 0.01 for index in range(n)]
    return pd.DataFrame({
        "open_time": pd.date_range("2025-01-01T00:00:00Z", periods=n, freq=f"{MINUTES}min"),
        "open": base, "high": [value + 0.5 for value in base],
        "low": [value - 0.5 for value in base], "close": [value + 0.1 for value in base], "volume": 1.0,
    })


def with_gap(source: pd.DataFrame, after_i: int) -> pd.DataFrame:
    """Introduce one missing expected timestamp between ``after_i - 1`` and it."""
    result = source.copy()
    result.loc[after_i:, "open_time"] += pd.Timedelta(minutes=MINUTES)
    return result


def plan() -> dict:
    return {
        "label_contract": {"confirmation_bars": 5, "horizon_hours": 12},
        "splits": {
            "train_end_exclusive": "2026-01-01T00:00:00Z",
            "validation_end_exclusive": "2027-01-01T00:00:00Z",
            "test_end_exclusive": "2028-01-01T00:00:00Z",
        },
    }


def row(source: pd.DataFrame) -> dict:
    start_i = CORE_END - 3
    return {
        "event_id": "continuity", "cluster_id": "continuity", "canonical_asset": "X", "direction": "LONG",
        "bar_minutes": MINUTES, "core_start_time": source.open_time.iloc[start_i].isoformat(),
        "core_end_time": source.open_time.iloc[CORE_END].isoformat(), "split": "val",
        "profit": {"retained": False, "outcome": "SL",
                   "decision_close_time_utc": (source.open_time.iloc[CORE_END + 5] + pd.Timedelta(minutes=MINUTES)).isoformat()},
    }


@pytest.mark.parametrize("gap_i", [100, CORE_END - 8, CORE_END + 3])
@pytest.mark.parametrize("retained", [True, False])
def test_warmup_or_visible_known_input_gap_purges_and_rejects_assets(gap_i: int, retained: bool):
    source = with_gap(frame(), gap_i)
    split, reason = split_for_event(source, CORE_END - 3, CORE_END, MINUTES, plan())
    assert (split, reason) == ("purged", "known_input_gap")
    event = row(source)
    event["profit"].update(retained=retained, outcome="TP" if retained else "SL")
    with pytest.raises(ProfitDatasetError, match="known input gap"):
        event_assets(source, event)


@pytest.mark.parametrize("gap_i", [20, CORE_END + 6])
def test_gap_outside_known_input_does_not_kill_event(gap_i: int):
    source = with_gap(frame(), gap_i)
    split, reason = split_for_event(source, CORE_END - 3, CORE_END, MINUTES, plan())
    assert (split, reason) == ("train", "")
    assets = event_assets(source, row(source))
    assert [asset["variant"] for asset in assets] == ["A"]


def test_continuous_known_input_remains_unchanged():
    source = frame()
    original = source.copy(deep=True)
    assert split_for_event(source, CORE_END - 3, CORE_END, MINUTES, plan()) == ("train", "")
    assert [asset["variant"] for asset in event_assets(source, row(source))] == ["A"]
    pd.testing.assert_frame_equal(source, original)
