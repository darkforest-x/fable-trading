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


def test_resolve_forward_exit_tip_signal_is_pending_open() -> None:
    """Signal bar == newest closed bar: record as open, never drop (tip path)."""
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

    outcome = resolve_forward_exit(frame, 3)  # tip bar

    assert outcome is not None
    assert outcome.status == "open"
    assert outcome.label == -1


def test_resolve_forward_exit_tip_signal_still_gated_on_atr() -> None:
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-07-08", periods=2, freq="15min", tz="UTC"),
            "open": [99.0, 100.0],
            "high": [100.0, 101.0],
            "low": [99.0, 99.0],
            "close": [100.0, 100.0],
            "atr14": [1.0, 1.0],
            "atr_pct": [0.01, 0.0001],  # below ATR_PCT_MIN at tip
        }
    )

    assert resolve_forward_exit(frame, 1) is None


def _tip_record() -> ForwardRecord:
    rec = _record("open", "tip-seen")
    rec["entry_price"] = 100.5  # proxy: signal bar close
    rec["maker_filled"] = None  # entry-pending sentinel
    return rec


def test_merge_backfills_tip_entry_fields_once_entry_bar_prints() -> None:
    existing = pd.DataFrame([_tip_record()])

    update = _record("open", "later-seen")  # real entry now known
    result = merge_forward_log(existing, [update])

    assert result.new_signals == 0
    row = result.frame.iloc[0]
    assert row["detected_at"] == "tip-seen"  # lag accounting keeps first-seen
    assert row["entry_price"] == 100.0
    assert row["maker_filled"] == True  # noqa: E712 -- object column
    assert row["status"] == "open"


def test_merge_backfills_entry_even_when_close_arrives_same_pulse() -> None:
    existing = pd.DataFrame([_tip_record()])

    result = merge_forward_log(existing, [_record("closed", "later-seen")])

    assert result.closed_updates == 1
    row = result.frame.iloc[0]
    assert row["detected_at"] == "tip-seen"
    assert row["entry_price"] == 100.0
    assert row["maker_filled"] == True  # noqa: E712
    assert row["status"] == "closed"
    assert row["outcome"] == "tp"


def test_merge_does_not_reopen_or_touch_confirmed_entry() -> None:
    confirmed = _record("open", "first-seen")
    existing = pd.DataFrame([confirmed])

    shifted = _record("open", "later-seen")
    shifted["entry_price"] = 42.0  # a re-scan must not rewrite confirmed entries
    result = merge_forward_log(existing, [shifted])

    row = result.frame.iloc[0]
    assert row["entry_price"] == 100.0
    assert row["detected_at"] == "first-seen"
