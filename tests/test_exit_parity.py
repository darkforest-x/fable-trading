"""Exit parity: backtest labeler vs forward resolver (TP5/SL2 mainline).

Deployment-consistency check (owner-approved 2026-07-20). The stage-3 backtest
(`src/backtest/run.py`) replays outcomes computed at dataset-build time by
`src.judgment.labeling.label_candidate` -- the mainline YOLO datasets are built
by `scripts/yolo_candidate_source.py` with explicit `tp_mult=5.0, sl_mult=2.0`.
The forward book (`src/judgment/forward_scan.resolve_forward_exit`) uses module
constants TP_MULT=5.0 / SL_MULT=2.0 / HORIZON_BARS=72.

These tests feed IDENTICAL synthetic OHLCV frames to both functions and assert
per-trade outcome / label / exit_offset / realized_ret / exit_time equality on
every closed path (TP, SL, same-bar ambiguous, timeout, gap-through), plus the
two intentional asymmetries (partial horizon -> open vs None; tip signal ->
open-pending vs None). Pure synthetic data; no production code is modified.

Run: .venv/bin/python -m pytest tests/test_exit_parity.py -v
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.judgment.forward_scan import resolve_forward_exit
from src.judgment.forward_types import BAR, SL_MULT, TP_MULT
from src.judgment.labeling import HORIZON_BARS, label_candidate

ENTRY = 100.0
ATR = 1.0
UPPER = ENTRY + TP_MULT * ATR   # 105.0
LOWER = ENTRY - SL_MULT * ATR   # 98.0


def _frame(
    n: int,
    *,
    highs: dict[int, float] | None = None,
    lows: dict[int, float] | None = None,
    opens: dict[int, float] | None = None,
    closes: dict[int, float] | None = None,
    atr_pct: float = 0.01,
) -> pd.DataFrame:
    """Signal row (index 0) + n post-signal path bars, contiguous 15m opens.

    Path bars default to a flat ENTRY price; `highs`/`lows`/... override single
    path bars by offset (0 = entry bar). ATR/atr_pct constant so barriers are
    UPPER/LOWER above.
    """
    h = [ENTRY] * n
    lo = [ENTRY] * n
    op = [ENTRY] * n
    cl = [ENTRY] * n
    for target, override in ((h, highs), (lo, lows), (op, opens), (cl, closes)):
        for j, price in (override or {}).items():
            target[j] = price
    total = n + 1
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-07-01", periods=total, freq="15min", tz="UTC"),
            "open": [ENTRY - 1.0] + op,
            "high": [ENTRY] + h,
            "low": [ENTRY - 1.0] + lo,
            "close": [ENTRY] + cl,
            "atr14": [ATR] * total,
            "atr_pct": [atr_pct] * total,
        }
    )


def _assert_closed_parity(frame: pd.DataFrame, signal_i: int = 0) -> tuple:
    """Run both exits on the same frame; assert field-by-field equality."""
    labeled = label_candidate(
        frame, signal_i, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON_BARS
    )
    forward = resolve_forward_exit(frame, signal_i)
    assert labeled is not None, "labeler unexpectedly rejected the candidate"
    assert forward is not None, "forward resolver unexpectedly rejected the candidate"
    assert forward.status == "closed"
    assert forward.outcome == labeled.outcome
    assert forward.label == labeled.label
    assert forward.exit_offset == labeled.exit_offset
    assert forward.realized_ret == pytest.approx(labeled.realized_ret, abs=1e-12)
    # exit_time contract: backtest computes signal_time + BAR + exit_offset*BAR
    # (build_signals / score_with_artifact); forward uses entry-bar open_time +
    # exit_offset*BAR. Equal on contiguous bars.
    signal_time = pd.Timestamp(frame["open_time"].iloc[signal_i])
    backtest_exit_time = signal_time + BAR + labeled.exit_offset * BAR
    assert forward.exit_time == str(backtest_exit_time)
    return labeled, forward


# --- closed paths: both sides must agree ------------------------------------


def test_tp_touch() -> None:
    frame = _frame(HORIZON_BARS, highs={10: UPPER})
    labeled, _ = _assert_closed_parity(frame)
    assert labeled.outcome == "tp"
    assert labeled.exit_offset == 11
    assert labeled.realized_ret == pytest.approx(UPPER / ENTRY - 1)


def test_tp_exact_barrier_touch_counts() -> None:
    # high == upper exactly: both use >= so the touch fills
    frame = _frame(HORIZON_BARS, highs={3: UPPER})
    labeled, _ = _assert_closed_parity(frame)
    assert labeled.outcome == "tp"


def test_sl_touch() -> None:
    frame = _frame(HORIZON_BARS, lows={5: LOWER - 0.25})
    labeled, _ = _assert_closed_parity(frame)
    assert labeled.outcome == "sl"
    assert labeled.exit_offset == 6
    assert labeled.realized_ret == pytest.approx(LOWER / ENTRY - 1)


def test_same_bar_double_touch_is_sl_ambiguous() -> None:
    frame = _frame(HORIZON_BARS, highs={7: UPPER + 1}, lows={7: LOWER - 1})
    labeled, _ = _assert_closed_parity(frame)
    assert labeled.outcome == "sl_ambiguous"
    assert labeled.label == 0
    assert labeled.realized_ret == pytest.approx(LOWER / ENTRY - 1)


def test_sl_earlier_than_tp_wins() -> None:
    frame = _frame(HORIZON_BARS, lows={2: LOWER}, highs={4: UPPER})
    labeled, _ = _assert_closed_parity(frame)
    assert labeled.outcome == "sl"
    assert labeled.exit_offset == 3


def test_tp_earlier_than_sl_wins() -> None:
    frame = _frame(HORIZON_BARS, highs={2: UPPER}, lows={4: LOWER})
    labeled, _ = _assert_closed_parity(frame)
    assert labeled.outcome == "tp"
    assert labeled.exit_offset == 3


def test_timeout_at_horizon_close() -> None:
    frame = _frame(HORIZON_BARS, closes={HORIZON_BARS - 1: 101.5})
    labeled, _ = _assert_closed_parity(frame)
    assert labeled.outcome == "timeout"
    assert labeled.label == 0
    assert labeled.exit_offset == HORIZON_BARS  # 72 bars = 18h
    assert labeled.realized_ret == pytest.approx(101.5 / ENTRY - 1)


def test_gap_down_through_sl_fills_at_barrier_both_sides() -> None:
    # Bar opens BELOW the stop: both implementations still fill at the barrier
    # price (shared idealization -- real fills would be at/below the open).
    frame = _frame(
        HORIZON_BARS,
        opens={6: LOWER - 3.0},
        lows={6: LOWER - 4.0},
        highs={6: LOWER - 1.0},
        closes={6: LOWER - 2.0},
    )
    labeled, forward = _assert_closed_parity(frame)
    assert labeled.outcome == "sl"
    assert labeled.realized_ret == pytest.approx(LOWER / ENTRY - 1)
    assert forward.realized_ret == pytest.approx(LOWER / ENTRY - 1)


def test_gap_up_through_tp_fills_at_barrier_both_sides() -> None:
    frame = _frame(
        HORIZON_BARS,
        opens={6: UPPER + 3.0},
        highs={6: UPPER + 4.0},
        lows={6: UPPER + 1.0},
        closes={6: UPPER + 2.0},
    )
    labeled, forward = _assert_closed_parity(frame)
    assert labeled.outcome == "tp"
    assert labeled.realized_ret == pytest.approx(UPPER / ENTRY - 1)
    assert forward.realized_ret == pytest.approx(UPPER / ENTRY - 1)


def test_entry_bar_itself_can_exit() -> None:
    # Barrier hit on the entry bar (offset 1) -- first bar of the window
    frame = _frame(HORIZON_BARS, lows={0: LOWER})
    labeled, _ = _assert_closed_parity(frame)
    assert labeled.outcome == "sl"
    assert labeled.exit_offset == 1


# --- intentional asymmetries (documented, by design) -------------------------


def test_partial_horizon_forward_open_labeler_none() -> None:
    # Only 10 path bars, no barrier touch: labeler cannot label (None),
    # forward keeps the position open for later pulses.
    frame = _frame(10)
    assert label_candidate(frame, 0, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON_BARS) is None
    forward = resolve_forward_exit(frame, 0)
    assert forward is not None
    assert forward.status == "open"
    assert forward.label == -1


def test_partial_horizon_with_barrier_touch_still_closes_identically() -> None:
    # Barrier resolved INSIDE the partial window: forward closes early with the
    # same fields the labeler would produce once the full horizon exists.
    partial = _frame(10, highs={4: UPPER})
    forward = resolve_forward_exit(partial, 0)
    full = _frame(HORIZON_BARS, highs={4: UPPER})
    labeled = label_candidate(full, 0, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON_BARS)
    assert forward is not None and labeled is not None
    assert forward.status == "closed"
    assert forward.outcome == labeled.outcome
    assert forward.exit_offset == labeled.exit_offset
    assert forward.realized_ret == pytest.approx(labeled.realized_ret)


def test_tip_signal_forward_open_pending_labeler_none() -> None:
    # signal bar is the newest closed bar: entry bar has not printed
    frame = _frame(5)
    tip_i = len(frame) - 1
    assert label_candidate(frame, tip_i, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON_BARS) is None
    forward = resolve_forward_exit(frame, tip_i)
    assert forward is not None
    assert forward.status == "open"


def test_atr_floor_rejects_both_sides() -> None:
    frame = _frame(HORIZON_BARS, atr_pct=0.0010)  # below ATR_PCT_MIN=0.0015
    assert label_candidate(frame, 0, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON_BARS) is None
    assert resolve_forward_exit(frame, 0) is None


# --- randomized fuzz over synthetic walks ------------------------------------


def test_fuzz_random_walks_agree_everywhere() -> None:
    """400 seeded random-walk series; every full-horizon candidate must agree
    on all output fields. Also checks all four outcomes actually occur."""
    rng = np.random.default_rng(20260720)
    outcomes_seen: set[str] = set()
    n_path = HORIZON_BARS + 5
    for series in range(400):
        # step scale drawn per-series so some walks are quiet (timeout) and
        # some violent (barrier hits / same-bar double touches)
        scale = rng.uniform(0.2, 3.0)
        mid = ENTRY + np.cumsum(rng.normal(0.0, scale, size=n_path + 1))
        spread = np.abs(rng.normal(0.0, scale, size=n_path + 1)) + 0.05
        total = n_path + 1
        frame = pd.DataFrame(
            {
                "open_time": pd.date_range(
                    "2026-07-01", periods=total, freq="15min", tz="UTC"
                ),
                "open": mid,
                "high": mid + spread,
                "low": mid - spread,
                "close": mid + rng.normal(0.0, 0.1, size=total),
                "atr14": [ATR] * total,
                "atr_pct": [0.01] * total,
            }
        )
        labeled = label_candidate(
            frame, 0, tp_mult=TP_MULT, sl_mult=SL_MULT, horizon=HORIZON_BARS
        )
        forward = resolve_forward_exit(frame, 0)
        if labeled is None:
            # invalid entry (non-positive open) must be rejected by both
            assert forward is None
            continue
        assert forward is not None
        assert forward.status == "closed"
        assert forward.outcome == labeled.outcome, f"series {series}"
        assert forward.label == labeled.label, f"series {series}"
        assert forward.exit_offset == labeled.exit_offset, f"series {series}"
        assert forward.realized_ret == pytest.approx(
            labeled.realized_ret, abs=1e-12
        ), f"series {series}"
        entry_time = pd.Timestamp(frame["open_time"].iloc[1])
        assert forward.exit_time == str(entry_time + labeled.exit_offset * BAR)
        outcomes_seen.add(labeled.outcome)
    assert {"tp", "sl", "sl_ambiguous", "timeout"} <= outcomes_seen, outcomes_seen
