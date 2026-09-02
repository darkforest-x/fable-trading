"""Pure contract tests for the frozen latest A-share 15m scanner."""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.scan_15m_ashare_yolo_latest import (
    BAR_DELTA,
    CUTOFF_CLOSE_CST,
    CUTOFF_OPEN_UTC,
    SESSION_CLOSE_SLOTS,
    AShareScanError,
    _parse_kline_payload,
    validate_against_schedule,
)


def _line(timestamp: str, value: float = 10.0) -> str:
    return f"{timestamp},{value},{value + 0.1},{value + 0.2},{value - 0.2},100,1000,0,0,0,0"


def _schedule_frame() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    day = pd.Timestamp("2026-08-19", tz="Asia/Shanghai")
    while len(rows) < 160:
        if day.weekday() < 5:
            for slot in sorted(SESSION_CLOSE_SLOTS):
                close_time = pd.Timestamp(f"{day.date()} {slot}", tz="Asia/Shanghai")
                if close_time <= CUTOFF_CLOSE_CST:
                    rows.append(
                        {
                            "raw_close_time": close_time,
                            "open_time": (close_time - BAR_DELTA).tz_convert("UTC"),
                            "open": 10.0,
                            "high": 10.2,
                            "low": 9.8,
                            "close": 10.1,
                            "volume": 100.0,
                            "amount": 1000.0,
                            "secid": "1.000001",
                            "adjustment": "none",
                        }
                    )
        day += pd.Timedelta(days=1)
    frame = pd.DataFrame(rows).tail(160).reset_index(drop=True)
    frame.loc[len(frame) - 1, "raw_close_time"] = CUTOFF_CLOSE_CST
    frame.loc[len(frame) - 1, "open_time"] = CUTOFF_OPEN_UTC
    return frame


def test_parse_kline_uses_close_labels_and_excludes_post_cutoff() -> None:
    payload = {
        "data": {
            "klines": [
                _line("2026-09-02 11:15"),
                _line("2026-09-02 11:30", 10.2),
                _line("2026-09-02 13:15", 10.4),
            ]
        }
    }
    frame = _parse_kline_payload(payload, secid="0.000001", adjustment="qfq")
    assert len(frame) == 2
    assert pd.Timestamp(frame.iloc[-1]["raw_close_time"]) == CUTOFF_CLOSE_CST
    assert pd.Timestamp(frame.iloc[-1]["open_time"]) == CUTOFF_OPEN_UTC
    assert frame.iloc[-1]["adjustment"] == "qfq"


def test_parse_kline_rejects_non_session_close_label() -> None:
    payload = {"data": {"klines": [_line("2026-09-02 09:30")]}}
    with pytest.raises(AShareScanError, match="unexpected 15m close labels"):
        _parse_kline_payload(payload, secid="0.000001", adjustment="qfq")


def test_schedule_gate_requires_exact_reference_tail() -> None:
    reference = _schedule_frame()
    candidate = reference.copy()
    candidate["secid"] = "0.000001"
    candidate["adjustment"] = "qfq"
    validate_against_schedule(candidate, reference, secid="0.000001")

    candidate.loc[10, "raw_close_time"] = (
        candidate.loc[10, "raw_close_time"] + BAR_DELTA
    )
    candidate.loc[10, "open_time"] = candidate.loc[10, "open_time"] + BAR_DELTA
    with pytest.raises(AShareScanError, match="schedule_mismatch"):
        validate_against_schedule(candidate, reference, secid="0.000001")
