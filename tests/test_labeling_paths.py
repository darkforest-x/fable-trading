from __future__ import annotations

import pandas as pd
import pytest

from src.judgment.labeling import label_candidate


def _frame(highs: list[float], lows: list[float], closes: list[float] | None = None) -> pd.DataFrame:
    if closes is None:
        closes = [100.0] * len(highs)
    return pd.DataFrame(
        {
            "open": [99.0] + [100.0] * len(highs),
            "high": [100.0] + highs,
            "low": [99.0] + lows,
            "close": [100.0] + closes,
            "atr14": [1.0] * (len(highs) + 1),
            "atr_pct": [0.01] * (len(highs) + 1),
        }
    )


def test_label_candidate_hits_take_profit_first() -> None:
    outcome = label_candidate(
        _frame(highs=[101.0, 102.5, 101.0], lows=[99.5, 99.5, 99.5]),
        0,
        tp_mult=2.0,
        sl_mult=1.0,
        horizon=3,
    )

    assert outcome is not None
    assert outcome.label == 1
    assert outcome.outcome == "tp"
    assert outcome.exit_offset == 2
    assert outcome.entry_price == 100.0
    assert outcome.realized_ret == pytest.approx(0.02)


def test_label_candidate_hits_stop_loss_first() -> None:
    outcome = label_candidate(
        _frame(highs=[101.0, 101.0, 101.0], lows=[98.5, 99.5, 99.5]),
        0,
        tp_mult=2.0,
        sl_mult=1.0,
        horizon=3,
    )

    assert outcome is not None
    assert outcome.label == 0
    assert outcome.outcome == "sl"
    assert outcome.exit_offset == 1
    assert outcome.realized_ret == pytest.approx(-0.01)


def test_label_candidate_times_out_at_horizon_close() -> None:
    outcome = label_candidate(
        _frame(highs=[101.0, 101.0, 101.0], lows=[99.5, 99.5, 99.5], closes=[100.0, 100.0, 101.0]),
        0,
        tp_mult=2.0,
        sl_mult=1.0,
        horizon=3,
    )

    assert outcome is not None
    assert outcome.label == 0
    assert outcome.outcome == "timeout"
    assert outcome.exit_offset == 3
    assert outcome.realized_ret == pytest.approx(0.01)


def test_label_candidate_ambiguous_bar_is_conservative_stop() -> None:
    outcome = label_candidate(
        _frame(highs=[102.5, 101.0, 101.0], lows=[98.5, 99.5, 99.5]),
        0,
        tp_mult=2.0,
        sl_mult=1.0,
        horizon=3,
    )

    assert outcome is not None
    assert outcome.label == 0
    assert outcome.outcome == "sl_ambiguous"
    assert outcome.exit_offset == 1
    assert outcome.realized_ret == pytest.approx(-0.01)
