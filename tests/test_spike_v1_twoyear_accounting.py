"""Economic-accounting contracts for the covered SPIKE Burst V1 ledger."""

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_v1_twoyear_allmarkets import (
    _entry_risk_fraction,
    _performance_summary,
    _read_utc_source,
)


def test_next_open_risk_uses_frozen_initial_stop_and_rejects_nonpositive_risk():
    """A signal close of 100 may execute at 110, so risk is 20/110 not close risk."""
    signal_close, initial_stop, next_open = 100.0, 90.0, 110.0
    assert signal_close == 100.0  # Documents the distinct signal-close reference.
    assert _entry_risk_fraction(next_open, initial_stop) == pytest.approx(20.0 / 110.0)
    assert np.isnan(_entry_risk_fraction(90.0, 90.0))


def _scrambled_rows() -> pd.DataFrame:
    return pd.DataFrame([
        {"event_id": "later-loss", "venue": "okx", "timeframe_min": 60,
         "entry_time": "2025-01-02T00:00:00Z", "exit_time": "2025-01-03T00:00:00Z",
         "net_return": -0.20, "censored": False},
        {"event_id": "censored-windfall", "venue": "okx", "timeframe_min": 60,
         "entry_time": "2025-01-04T00:00:00Z", "exit_time": "2025-01-05T00:00:00Z",
         "net_return": 0.90, "censored": True},
        {"event_id": "early-win", "venue": "okx", "timeframe_min": 60,
         "entry_time": "2025-01-01T00:00:00Z", "exit_time": "2025-01-02T00:00:00Z",
         "net_return": 0.10, "censored": False},
    ])


def test_summary_excludes_censored_rows_and_uses_stable_event_sequence_drawdown():
    frame = _scrambled_rows()
    frame[["entry_time", "exit_time"]] = frame[["entry_time", "exit_time"]].apply(pd.to_datetime, utc=True)
    summary = _performance_summary(frame, ["venue", "timeframe_min"]).iloc[0]
    reordered = _performance_summary(frame.sample(frac=1.0, random_state=7), ["venue", "timeframe_min"]).iloc[0]

    assert summary["signal_rows"] == 3
    assert summary["trades"] == 2
    assert summary["censored"] == 1
    assert summary["wins"] == 1
    assert summary["win_rate"] == pytest.approx(0.5)
    assert summary["profit_factor"] == pytest.approx(0.5)
    assert summary["net_return"] == pytest.approx(-0.1)
    assert summary["event_sequence_drawdown"] == pytest.approx(-0.2)
    for key in ("trades", "wins", "win_rate", "profit_factor", "net_return", "event_sequence_drawdown", "censored"):
        assert reordered[key] == pytest.approx(summary[key])


def test_empty_frozen_source_has_a_real_empty_utc_timeline(tmp_path):
    path = tmp_path / "empty.csv.gz"
    pd.DataFrame(columns=["open", "high", "low", "close", "volume", "quote_volume"]).to_csv(path, compression="gzip")
    source = _read_utc_source(path)
    assert source.empty
    assert isinstance(source.index, pd.DatetimeIndex)
    assert str(source.index.tz) == "UTC"


def test_invalid_nonempty_frozen_timeline_fails_closed(tmp_path):
    path = tmp_path / "invalid.csv"
    path.write_text(",open,high,low,close,volume,quote_volume\nnot-a-clock,1,1,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid frozen source timeline"):
        _read_utc_source(path)
