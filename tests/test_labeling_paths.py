from __future__ import annotations

import pandas as pd
import pytest

from src.judgment.labeling import (
    label_candidate,
    label_candidate_breakeven,
    label_candidate_scaled,
)


def _frame(
    highs: list[float],
    lows: list[float],
    closes: list[float] | None = None,
    opens: list[float] | None = None,
) -> pd.DataFrame:
    if closes is None:
        closes = [100.0] * len(highs)
    if opens is None:
        opens = [100.0] * len(highs)
    return pd.DataFrame(
        {
            "open": [99.0] + list(opens),
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


def test_label_candidate_signal_close_entry_uses_signal_close() -> None:
    """Fill at signal close; path still from next bar. Only price differs."""
    # signal close=100; next open=101 → next_open fill is 101, signal_close is 100
    frame = _frame(
        highs=[103.0, 103.0, 103.0],
        lows=[100.5, 100.5, 100.5],
        opens=[101.0, 101.0, 101.0],
        closes=[101.0, 101.0, 101.0],
    )
    # override signal close (index 0)
    frame.loc[0, "close"] = 100.0

    next_o = label_candidate(frame, 0, tp_mult=2.0, sl_mult=1.0, horizon=3, entry="next_open")
    close_o = label_candidate(frame, 0, tp_mult=2.0, sl_mult=1.0, horizon=3, entry="signal_close")
    assert next_o is not None and close_o is not None
    assert next_o.entry_price == pytest.approx(101.0)
    assert close_o.entry_price == pytest.approx(100.0)
    # TP at entry+2*atr: next_open → 103, signal_close → 102; both hit bar0 high=103
    assert next_o.outcome == "tp"
    assert close_o.outcome == "tp"
    assert close_o.realized_ret == pytest.approx(0.02)
    assert next_o.realized_ret == pytest.approx(2.0 / 101.0)


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


# --- H1 scaled: four barrier paths (atr=1, entry=100) ---
# hard_stop = 100 - 2*1 = 98; tp1 = 100 + 2.5*1 = 102.5; trail = 3*ATR


def test_scaled_hits_hard_stop_before_tp1() -> None:
    outcome = label_candidate_scaled(
        _frame(highs=[101.0, 101.0, 101.0], lows=[97.5, 99.0, 99.0]),
        0,
        tp1_mult=2.5,
        trail_mult=3.0,
        sl_mult=2.0,
        horizon=3,
    )
    assert outcome is not None
    assert outcome.outcome == "sl"
    assert outcome.label == 0
    assert outcome.exit_offset == 1
    assert outcome.realized_ret == pytest.approx(-0.02)


def test_scaled_tp1_then_trail_stop() -> None:
    # bar0: hit tp1 (high>=102.5), no hard stop; bar1: trail stop below run_max=tp1
    # trail stop = 102.5 - 3 = 99.5
    outcome = label_candidate_scaled(
        _frame(highs=[103.0, 101.0, 101.0], lows=[99.5, 99.0, 99.0]),
        0,
        tp1_mult=2.5,
        trail_mult=3.0,
        sl_mult=2.0,
        horizon=3,
    )
    assert outcome is not None
    assert outcome.outcome == "scaled"
    assert outcome.exit_offset == 2
    # half at tp1 (+2.5%), half at stop 99.5 (-0.5%) → +1.0%
    assert outcome.realized_ret == pytest.approx(0.5 * 0.025 + 0.5 * (99.5 / 100.0 - 1))


def test_scaled_timeout_without_tp1() -> None:
    outcome = label_candidate_scaled(
        _frame(
            highs=[101.0, 101.0, 101.0],
            lows=[99.0, 99.0, 99.0],
            closes=[100.0, 100.0, 100.5],
        ),
        0,
        tp1_mult=2.5,
        trail_mult=3.0,
        sl_mult=2.0,
        horizon=3,
    )
    assert outcome is not None
    assert outcome.outcome == "timeout"
    assert outcome.exit_offset == 3
    assert outcome.realized_ret == pytest.approx(0.005)


def test_scaled_timeout_after_tp1() -> None:
    # tp1 on bar0; subsequent lows stay above trailing stop as run_max rises
    outcome = label_candidate_scaled(
        _frame(
            highs=[103.0, 104.0, 105.0],
            lows=[100.0, 102.0, 103.0],
            closes=[102.0, 103.0, 104.0],
        ),
        0,
        tp1_mult=2.5,
        trail_mult=3.0,
        sl_mult=2.0,
        horizon=3,
    )
    assert outcome is not None
    assert outcome.outcome == "scaled_timeout"
    assert outcome.exit_offset == 3
    assert outcome.realized_ret == pytest.approx(0.5 * 0.025 + 0.5 * 0.04)


# --- H2 breakeven: four paths (atr=1, entry=100) ---
# upper=105, trigger=101.5, initial stop=98


def test_breakeven_hits_initial_stop() -> None:
    outcome = label_candidate_breakeven(
        _frame(highs=[101.0, 101.0, 101.0], lows=[97.5, 99.0, 99.0]),
        0,
        tp_mult=5.0,
        sl_mult=2.0,
        be_trigger=1.5,
        horizon=3,
    )
    assert outcome is not None
    assert outcome.outcome == "sl"
    assert outcome.label == 0
    assert outcome.realized_ret == pytest.approx(-0.02)


def test_breakeven_hits_tp() -> None:
    outcome = label_candidate_breakeven(
        _frame(highs=[101.0, 106.0, 101.0], lows=[99.0, 99.0, 99.0]),
        0,
        tp_mult=5.0,
        sl_mult=2.0,
        be_trigger=1.5,
        horizon=3,
    )
    assert outcome is not None
    assert outcome.outcome == "tp"
    assert outcome.label == 1
    assert outcome.exit_offset == 2
    assert outcome.realized_ret == pytest.approx(0.05)


def test_breakeven_armed_then_stop_at_entry() -> None:
    # bar0: high 102 >= trigger 101.5 → arm stop=entry; bar1: low 99.5 <= 100 → be exit
    outcome = label_candidate_breakeven(
        _frame(highs=[102.0, 101.0, 101.0], lows=[99.5, 99.5, 99.5]),
        0,
        tp_mult=5.0,
        sl_mult=2.0,
        be_trigger=1.5,
        horizon=3,
    )
    assert outcome is not None
    assert outcome.outcome == "be"
    assert outcome.exit_offset == 2
    assert outcome.realized_ret == pytest.approx(0.0)


def test_breakeven_timeout() -> None:
    outcome = label_candidate_breakeven(
        _frame(
            highs=[101.0, 101.0, 101.0],
            lows=[99.0, 99.0, 99.0],
            closes=[100.0, 100.0, 100.8],
        ),
        0,
        tp_mult=5.0,
        sl_mult=2.0,
        be_trigger=1.5,
        horizon=3,
    )
    assert outcome is not None
    assert outcome.outcome == "timeout"
    assert outcome.exit_offset == 3
    assert outcome.realized_ret == pytest.approx(0.008)
