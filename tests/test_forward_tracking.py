from __future__ import annotations

import math

import pandas as pd

from src.judgment.forward import ForwardRecord, merge_forward_log, resolve_forward_exit


def _record(status: str, detected_at: str = "2026-07-09T00:00:00+00:00") -> ForwardRecord:
    return {
        "source": "okx",
        "symbol": "BTC_USDT_SWAP",
        "signal_time": "2026-07-08 00:00:00+00:00",
        "detected_at": detected_at,
        "status": status,
        "score": 0.5,
        "threshold": 0.4,
        "model_path": "models/frozen.txt",
        "dataset_sha256": "abc",
        "signal_i": 1,
        "entry_time": "2026-07-08 00:15:00+00:00",
        "entry_price": 100.0,
        "maker_filled": True,
        "outcome": "tp" if status == "closed" else "",
        "label": 1 if status == "closed" else -1,
        "exit_offset": 1 if status == "closed" else 0,
        "exit_time": "2026-07-08 00:30:00+00:00" if status == "closed" else "",
        "realized_ret": 0.05 if status == "closed" else math.nan,
        "atr_pct": 0.01,
        "dense_run_len": 8,
    }


def test_merge_forward_log_updates_open_row_without_duplicate() -> None:
    existing = pd.DataFrame([_record("open", "first-seen")])

    result = merge_forward_log(existing, [_record("closed", "later-seen")])

    assert result.new_signals == 0
    assert result.closed_updates == 1
    assert len(result.frame) == 1
    row = result.frame.iloc[0]
    assert row["detected_at"] == "first-seen"
    assert row["status"] == "closed"
    assert row["outcome"] == "tp"


def test_merge_forward_log_is_idempotent_for_closed_rows() -> None:
    existing = pd.DataFrame([_record("closed", "first-seen")])

    result = merge_forward_log(existing, [_record("closed", "later-seen")])

    assert result.new_signals == 0
    assert result.closed_updates == 0
    assert len(result.frame) == 1
    assert result.frame.iloc[0]["detected_at"] == "first-seen"


def test_resolve_forward_exit_marks_open_before_horizon_without_barrier() -> None:
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-07-08", periods=4, freq="15min", tz="UTC"),
            "open": [99.0, 100.0, 100.0, 100.0],
            "high": [100.0, 101.0, 101.0, 101.0],
            "low": [99.0, 99.0, 99.0, 99.0],
            "close": [100.0, 100.0, 100.0, 100.0],
            "atr14": [1.0, 1.0, 1.0, 1.0],
            "atr_pct": [0.01, 0.01, 0.01, 0.01],
        }
    )

    outcome = resolve_forward_exit(frame, 1)

    assert outcome is not None
    assert outcome.status == "open"
    assert outcome.label == -1


def test_resolve_forward_exit_closes_on_partial_tp_hit() -> None:
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-07-08", periods=4, freq="15min", tz="UTC"),
            "open": [99.0, 100.0, 100.0, 100.0],
            "high": [100.0, 101.0, 106.0, 101.0],
            "low": [99.0, 99.0, 99.0, 99.0],
            "close": [100.0, 100.0, 100.0, 100.0],
            "atr14": [1.0, 1.0, 1.0, 1.0],
            "atr_pct": [0.01, 0.01, 0.01, 0.01],
        }
    )

    outcome = resolve_forward_exit(frame, 1)

    assert outcome is not None
    assert outcome.status == "closed"
    assert outcome.outcome == "tp"
    assert outcome.exit_offset == 1
