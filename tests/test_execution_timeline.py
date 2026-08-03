"""P0.6 acceptance F-01..F-07: decision and fill are causally ordered."""
from __future__ import annotations

import types

import numpy as np
import pandas as pd
import pytest

from src.judgment.execution_timeline import (
    FillEvidenceError,
    broker_fill_from_ledger,
    paper_fill_after_decision,
    resolve_outcome_after_fill,
)
from src.judgment.forward_records import actual_closed_rows, normalize_log


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-08-01 03:00", periods=5, freq="15min", tz="UTC"),
            "open": [100.0] * 5,
            # 03:15 touches the long TP before a 03:20 decision. Later bars do not.
            "high": [101.0, 106.0, 101.0, 101.0, 101.0],
            "low": [99.0] * 5,
            "close": [100.0] * 5,
            "atr14": [1.0] * 5,
        }
    )


def _protocol():
    return types.SimpleNamespace(
        side="long",
        tp_atr_mult=5.0,
        sl_atr_mult=2.0,
        horizon_bars=72,
        same_bar_policy="conservative_sl",
        gap_policy="barrier_price",
        return_convention="linear_long",
    )


def test_f01_and_f04_paper_fill_is_first_open_strictly_after_decision() -> None:
    fill = paper_fill_after_decision(_frame(), "2026-08-01 03:20:00+00:00")
    assert fill is not None
    assert pd.Timestamp(fill.fill_at) == pd.Timestamp("2026-08-01 03:30:00+00:00")
    assert fill.bar_i == 2
    assert fill.fill_px == 100.0


def test_paper_fill_at_exact_bar_open_waits_for_the_next_future_open() -> None:
    fill = paper_fill_after_decision(_frame(), "2026-08-01 03:15:00+00:00")
    assert fill is not None
    assert fill.bar_i == 2


def test_f02_prefill_tp_is_not_an_actual_postfill_tp() -> None:
    frame = _frame()
    fill = paper_fill_after_decision(frame, "2026-08-01 03:20:00+00:00")
    assert fill is not None
    outcome = resolve_outcome_after_fill(
        frame, signal_i=0, fill=fill, protocol=_protocol(), allow_partial=True
    )
    assert outcome.status == "open"
    assert outcome.outcome == ""
    assert outcome.gross_ret is None


def test_no_future_open_means_no_paper_fill() -> None:
    assert paper_fill_after_decision(_frame(), "2026-08-01 04:00:00+00:00") is None


def test_f03_candidate_without_fill_is_not_an_actual_closed_trade() -> None:
    row = {
        "source": "okx",
        "symbol": "BTC_USDT_SWAP",
        "signal_time": "2026-08-01 03:00:00+00:00",
        "status": "closed",
        "entry_status": "not_requested",
        "research_status": "closed",
        "research_gross_ret": 0.05,
        "realized_ret": np.nan,
        "actual_realized_ret": np.nan,
        "protocol_version": "p0_audit",
        "execution_eligible": False,
    }
    assert actual_closed_rows(normalize_log(pd.DataFrame([row]))).empty


def test_f07_broker_fill_requires_explicit_ledger_fill_fields() -> None:
    fill = broker_fill_from_ledger(
        {
            "fill_source": "broker_ledger",
            "fill_at": "2026-08-01 03:20:01+00:00",
            "fill_px": "99.75",
            "mark_px": 100.0,
        }
    )
    assert fill.source == "broker_ledger"
    assert fill.fill_px == pytest.approx(99.75)

    with pytest.raises(FillEvidenceError):
        broker_fill_from_ledger(
            {
                "fill_source": "broker_ledger",
                "entry_requested_at": "2026-08-01 03:20:00+00:00",
                "mark_px": 100.0,
            }
        )


def test_midbar_broker_fill_excludes_the_containing_bars_prefill_extremes() -> None:
    frame = _frame()
    fill = broker_fill_from_ledger(
        {
            "fill_source": "broker_ledger",
            "fill_at": "2026-08-01 03:20:00+00:00",
            "fill_px": 100.0,
        }
    )
    outcome = resolve_outcome_after_fill(
        frame, signal_i=0, fill=fill, protocol=_protocol(), allow_partial=True
    )
    assert outcome.status == "open"

