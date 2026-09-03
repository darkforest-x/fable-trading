"""Focused contract tests for the Owner's ten-signal 1h review scan."""

from __future__ import annotations

import pandas as pd

from scripts.scan_1h_model_first_standing_top10 import (
    FETCH_ROWS,
    REVIEW_FUTURE_BARS,
    SCORED_ENDPOINTS,
    TopTenScanError,
    decision_prefix,
    deduplicate_events,
    select_top_events,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame({"value": range(FETCH_ROWS)})


def _candidate(
    *,
    symbol: str,
    class_id: int,
    endpoint: int,
    core_end: int,
    confidence: float,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "class_id": class_id,
        "class_name": "dense_long" if class_id == 0 else "dense_short",
        "window_end_i": endpoint,
        "window_end_time": pd.Timestamp("2026-08-20T00:00:00Z")
        + pd.Timedelta(hours=endpoint),
        "window_len": 18,
        "core_end_i": core_end,
        "confidence": confidence,
    }


def test_decision_prefix_physically_reserves_all_future_rows() -> None:
    prefix = decision_prefix(_frame())
    assert len(prefix) == FETCH_ROWS - REVIEW_FUTURE_BARS
    assert len(prefix) == 180 + SCORED_ENDPOINTS
    assert int(prefix.iloc[-1]["value"]) == FETCH_ROWS - REVIEW_FUTURE_BARS - 1


def test_dedup_retains_earliest_detection_not_later_peak() -> None:
    rows = [
        _candidate(symbol="AAA_USDT_SWAP", class_id=0, endpoint=190, core_end=187, confidence=0.40),
        _candidate(symbol="AAA_USDT_SWAP", class_id=0, endpoint=192, core_end=189, confidence=0.91),
    ]
    events = deduplicate_events(rows)
    assert len(events) == 1
    assert int(events[0]["window_end_i"]) == 190
    assert float(events[0]["confidence"]) == 0.40
    assert float(events[0]["event_peak_confidence"]) == 0.91


def test_long_and_short_are_distinct_event_units() -> None:
    rows = [
        _candidate(symbol="AAA_USDT_SWAP", class_id=0, endpoint=190, core_end=187, confidence=0.70),
        _candidate(symbol="AAA_USDT_SWAP", class_id=1, endpoint=191, core_end=188, confidence=0.75),
    ]
    assert len(deduplicate_events(rows)) == 2


def test_selection_is_stable_and_rejects_future_fields() -> None:
    events = deduplicate_events(
        [
            _candidate(symbol="BBB_USDT_SWAP", class_id=0, endpoint=190, core_end=187, confidence=0.70),
            _candidate(symbol="AAA_USDT_SWAP", class_id=0, endpoint=191, core_end=188, confidence=0.90),
        ]
    )
    selected = select_top_events(events, top_k=1)
    assert str(selected[0]["symbol"]) == "AAA_USDT_SWAP"
    contaminated = [{**events[0], "review_directional_move_96h_pct": 99.0}]
    try:
        select_top_events(contaminated, top_k=1)
    except TopTenScanError as exc:
        assert "future/outcome fields reached selection" in str(exc)
    else:  # pragma: no cover - explicit fail message is more useful than pytest.raises here
        raise AssertionError("selection accepted a future-derived field")
