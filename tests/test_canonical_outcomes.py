"""P0 E-01..E-10: one fixed-barrier resolver for label/replay/forward."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.judgment.forward_scan import resolve_forward_exit_for_protocol
from src.judgment.labeling import label_short_candidate
from src.judgment.outcomes import (
    OutcomeContractError,
    gross_return,
    resolve_barrier_after_close,
    resolve_barrier_outcome,
)

ENTRY = 100.0
ATR = 1.0
HORIZON = 72


def _frame(
    n_path: int,
    *,
    highs: dict[int, float] | None = None,
    lows: dict[int, float] | None = None,
    opens: dict[int, float] | None = None,
    closes: dict[int, float] | None = None,
) -> pd.DataFrame:
    total = n_path + 1  # signal row + entry/path rows
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-04-01", periods=total, freq="15min", tz="UTC"),
            "open": [99.0] + [ENTRY] * n_path,
            "high": [100.0] + [ENTRY] * n_path,
            "low": [99.0] + [ENTRY] * n_path,
            "close": [ENTRY] + [ENTRY] * n_path,
            "atr14": [ATR] * total,
            "atr_pct": [0.01] * total,
        }
    )
    for column, overrides in (
        ("high", highs), ("low", lows), ("open", opens), ("close", closes)
    ):
        for offset, value in (overrides or {}).items():
            frame.loc[1 + offset, column] = value
    return frame


def _resolve(frame: pd.DataFrame, *, allow_partial: bool = False):
    return resolve_barrier_outcome(
        frame,
        side="short",
        entry_i=1,
        entry_price=ENTRY,
        atr=ATR,
        tp_atr_mult=5.0,
        sl_atr_mult=2.0,
        horizon_bars=HORIZON,
        same_bar_policy="conservative_sl",
        gap_policy="barrier_price",
        return_convention="linear_short",
        allow_partial=allow_partial,
    )


@pytest.mark.parametrize(
    ("frame", "outcome", "gross"),
    [
        (_frame(HORIZON, lows={0: 95.0}), "tp", 0.05),
        (_frame(HORIZON, highs={0: 102.0}), "sl", -0.02),
        (_frame(HORIZON, lows={0: 95.0}, highs={0: 102.0}), "sl_ambiguous", -0.02),
        (_frame(HORIZON, closes={HORIZON - 1: 97.0}), "timeout", 0.03),
    ],
)
def test_short_tp_sl_ambiguous_and_timeout(frame, outcome: str, gross: float) -> None:
    result = _resolve(frame)
    assert result.status == "closed"
    assert result.outcome == outcome
    assert result.gross_ret == pytest.approx(gross)


def test_partial_path_stays_open() -> None:
    result = _resolve(_frame(10), allow_partial=True)
    assert result.status == "open"
    assert result.gross_ret is None
    with pytest.raises(OutcomeContractError, match="full horizon"):
        _resolve(_frame(10), allow_partial=False)


def test_exact_touch_counts_and_gap_uses_declared_barrier_price() -> None:
    exact = _resolve(_frame(HORIZON, lows={3: 95.0}))
    gap = _resolve(
        _frame(HORIZON, opens={3: 90.0}, highs={3: 94.0}, lows={3: 89.0})
    )
    assert exact.outcome == gap.outcome == "tp"
    assert exact.exit_price == gap.exit_price == 95.0
    assert exact.gross_ret == gap.gross_ret == pytest.approx(0.05)


@pytest.mark.parametrize(
    ("entry", "atr"),
    [(0.0, 1.0), (-1.0, 1.0), (np.nan, 1.0), (100.0, 0.0), (100.0, np.nan)],
)
def test_non_positive_or_nan_entry_and_atr_are_rejected(entry: float, atr: float) -> None:
    with pytest.raises(OutcomeContractError):
        resolve_barrier_outcome(
            _frame(HORIZON),
            side="short",
            entry_i=1,
            entry_price=entry,
            atr=atr,
            tp_atr_mult=5.0,
            sl_atr_mult=2.0,
            horizon_bars=HORIZON,
            same_bar_policy="conservative_sl",
            gap_policy="barrier_price",
            return_convention="linear_short",
            allow_partial=False,
        )


def test_return_conventions_are_explicit_and_not_interchangeable() -> None:
    assert gross_return(100.0, 95.0, "linear_short") == pytest.approx(0.05)
    assert gross_return(100.0, 95.0, "inverse_short") == pytest.approx(100 / 95 - 1)
    assert gross_return(100.0, 105.0, "linear_long") == pytest.approx(0.05)
    with pytest.raises(OutcomeContractError, match="unknown"):
        gross_return(100.0, 95.0, "shortish")


def test_label_replay_and_forward_share_one_short_closed_result() -> None:
    frame = _frame(HORIZON, lows={4: 95.0})
    canonical = _resolve(frame)
    label = label_short_candidate(frame, 0, tp_mult=5.0, sl_mult=2.0, horizon=72)
    protocol = SimpleNamespace(
        side="short",
        tp_atr_mult=5.0,
        sl_atr_mult=2.0,
        horizon_bars=72,
        same_bar_policy="conservative_sl",
        gap_policy="barrier_price",
        return_convention="linear_short",
    )
    forward = resolve_forward_exit_for_protocol(frame, 0, protocol)

    assert label is not None and forward is not None
    assert (label.outcome, label.label, label.exit_offset, label.realized_ret) == (
        canonical.outcome,
        canonical.label,
        canonical.exit_offset,
        pytest.approx(canonical.gross_ret),
    )
    assert (forward.outcome, forward.label, forward.exit_offset, forward.realized_ret) == (
        canonical.outcome,
        canonical.label,
        canonical.exit_offset,
        pytest.approx(canonical.gross_ret),
    )


def test_forward_protocol_supplies_tp_sl_horizon_explicitly() -> None:
    frame = _frame(3, lows={2: 95.0})
    protocol = SimpleNamespace(
        side="short",
        tp_atr_mult=5.0,
        sl_atr_mult=2.0,
        horizon_bars=3,
        same_bar_policy="conservative_sl",
        gap_policy="barrier_price",
        return_convention="linear_short",
    )
    result = resolve_forward_exit_for_protocol(frame, 0, protocol)
    assert result is not None
    assert result.status == "closed"
    assert result.outcome == "tp"


def test_close_entry_excludes_the_decision_bars_earlier_high_and_low() -> None:
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-04-01", periods=4, freq="5min", tz="UTC"),
            "open": [100.0] * 4,
            "high": [106.0, 100.0, 100.0, 100.0],
            "low": [98.0, 100.0, 100.0, 100.0],
            "close": [100.0, 100.0, 100.0, 100.0],
        }
    )

    result = resolve_barrier_after_close(
        frame,
        side="long",
        decision_i=0,
        atr=1.0,
        tp_atr_mult=5.0,
        sl_atr_mult=2.0,
        horizon_bars=3,
        same_bar_policy="conservative_sl",
        gap_policy="barrier_price",
        return_convention="linear_long",
        allow_partial=False,
        bar_duration=pd.Timedelta(minutes=5),
    )

    assert result.outcome == "timeout"
    assert result.entry_price == 100.0
    assert result.exit_time == "2026-04-01 00:20:00+00:00"


def test_close_entry_allows_the_first_bar_after_decision_to_hit() -> None:
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-04-01", periods=3, freq="5min", tz="UTC"),
            "open": [100.0] * 3,
            "high": [100.0, 105.0, 100.0],
            "low": [100.0] * 3,
            "close": [100.0] * 3,
        }
    )

    result = resolve_barrier_after_close(
        frame,
        side="long",
        decision_i=0,
        atr=1.0,
        tp_atr_mult=5.0,
        sl_atr_mult=2.0,
        horizon_bars=2,
        same_bar_policy="conservative_sl",
        gap_policy="barrier_price",
        return_convention="linear_long",
        allow_partial=False,
        bar_duration=pd.Timedelta(minutes=5),
    )

    assert result.outcome == "tp"
    assert result.exit_offset == 1
    assert result.exit_time == "2026-04-01 00:10:00+00:00"
