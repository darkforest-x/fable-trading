from types import SimpleNamespace

import pandas as pd

from scripts.build_eth3m_entry_timing_calibration30 import (
    assign_event_ids,
    proposed_entry_index,
    select_calibration_rows,
)
from src.detection.data import ALL_MA_COLS


def test_assign_event_ids_keeps_tasks_within_sixty_minutes_together():
    rows = pd.DataFrame(
        {
            "candidate_time": pd.to_datetime(
                [
                    "2026-03-01T00:00:00Z",
                    "2026-03-01T00:59:00Z",
                    "2026-03-01T02:00:00Z",
                ],
                utc=True,
            )
        }
    )
    out = assign_event_ids(rows)
    assert out["event_id"].tolist() == [1, 1, 2]


def test_select_calibration_rows_is_event_unique_and_balanced():
    times = pd.date_range("2026-03-01", periods=40, freq="2h", tz="UTC")
    rows = pd.DataFrame(
        {
            "task_id": range(1, 41),
            "owner_is_target": 1,
            "candidate_time": times,
            "box_start_time": times - pd.Timedelta(minutes=36),
            "first_below_all_mas_lag_bars": [10] * 40,
            "consumed_exceeds_remaining": [0] * 20 + [1] * 20,
            "v10_conf": [0.31 + i * 0.01 for i in range(40)],
        }
    )
    selected = select_calibration_rows(rows)
    assert len(selected) == 30
    assert selected["event_id"].nunique() == 30
    assert selected["consumed_exceeds_remaining"].value_counts().to_dict() == {0: 15, 1: 15}


def test_proposed_entry_is_first_close_below_all_mas_inside_box():
    times = pd.date_range("2026-03-01", periods=6, freq="3min", tz="UTC")
    frame = pd.DataFrame({"open_time": times, "close": [11, 10.5, 9.9, 9.8, 9.7, 9.6]})
    for column in ALL_MA_COLS:
        frame[column] = 10.0
    positions = pd.Series(frame.index.to_numpy(), index=times)
    row = SimpleNamespace(
        task_id=7,
        box_start_time=times[1],
        candidate_time=times[5],
    )
    entry_i, signal_i, box_start_i = proposed_entry_index(
        row, ma_frame=frame, position_by_time=positions
    )
    assert (entry_i, signal_i, box_start_i) == (2, 5, 1)
