"""Synthetic tests for H1 scaled forward shadow exit + safety rails."""
from __future__ import annotations

from pathlib import Path

import math
import pytest
import pandas as pd

from src.judgment.forward import (
    FORWARD_LOG_H1_SCALED_PATH,
    FORWARD_LOG_PATH,
    run_forward_tracking_h1_shadow,
)
from src.judgment.forward_scan import resolve_forward_exit, resolve_forward_exit_scaled
from src.judgment.labeling import label_candidate_scaled


def _ohlc_frame(
    *,
    n: int,
    entry: float = 100.0,
    atr: float = 1.0,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    opens: list[float] | None = None,
    closes: list[float] | None = None,
) -> pd.DataFrame:
    """Bars layout: index 0 = signal, index 1+ = post-entry path."""
    # signal bar + entry bar + (n-1) more path bars → n path bars after signal when
    # we use signal_i=0 and entry at 1. Build total length = 1 + n.
    if highs is None:
        highs = [entry] * n
    if lows is None:
        lows = [entry] * n
    if opens is None:
        opens = [entry] * n
    if closes is None:
        closes = [entry] * n
    assert len(highs) == len(lows) == len(opens) == len(closes) == n
    total = n + 1  # signal row + n entry-path rows
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-07-08", periods=total, freq="15min", tz="UTC"),
            "open": [entry - 1.0] + list(opens),
            "high": [entry] + list(highs),
            "low": [entry - 1.0] + list(lows),
            "close": [entry] + list(closes),
            "atr14": [atr] * total,
            "atr_pct": [0.01] * total,
        }
    )
    return frame


def test_resolve_forward_exit_scaled_marks_open_before_horizon() -> None:
    # Only 3 path bars available; default horizon 72 → open
    frame = _ohlc_frame(n=3, highs=[101.0, 101.0, 101.0], lows=[99.5, 99.5, 99.5])
    outcome = resolve_forward_exit_scaled(frame, 0)
    assert outcome is not None
    assert outcome.status == "open"
    assert outcome.label == -1
    assert math.isnan(outcome.realized_ret)


def test_resolve_forward_exit_scaled_hard_stop_before_tp1() -> None:
    # SL at entry - 2*ATR = 98; hit on first path bar
    frame = _ohlc_frame(
        n=4,
        highs=[100.5, 100.5, 100.5, 100.5],
        lows=[97.5, 99.0, 99.0, 99.0],
        opens=[100.0, 100.0, 100.0, 100.0],
    )
    outcome = resolve_forward_exit_scaled(frame, 0, horizon=4)
    assert outcome is not None
    assert outcome.status == "closed"
    assert outcome.outcome == "sl"
    assert outcome.label == 0
    assert outcome.exit_offset == 1
    assert outcome.realized_ret == pytest.approx(-0.02)


def test_resolve_forward_exit_scaled_half_bank_then_trail() -> None:
    # TP1 at +2.5 ATR = 102.5 on bar 0; trail stop on later bar.
    # After TP1, trail = run_max - 3*ATR; run_max starts at tp1=102.5 → stop=99.5
    # Bar 1: low 99.0 hits trail stop 99.5
    frame = _ohlc_frame(
        n=4,
        highs=[103.0, 103.0, 103.0, 103.0],
        lows=[100.0, 99.0, 99.0, 99.0],
        opens=[100.0, 100.0, 100.0, 100.0],
        closes=[102.0, 100.0, 100.0, 100.0],
    )
    outcome = resolve_forward_exit_scaled(frame, 0, horizon=4)
    assert outcome is not None
    assert outcome.status == "closed"
    assert outcome.outcome == "scaled"
    assert outcome.exit_offset == 2
    # ret1 = 2.5/100; trail exit at stop 99.5 → -0.5%; blend 0.5*(0.025 + -0.005)=0.01
    assert outcome.realized_ret == pytest.approx(0.01)


def test_resolve_forward_exit_scaled_matches_label_candidate_scaled() -> None:
    frame = _ohlc_frame(
        n=5,
        highs=[103.0, 104.0, 104.0, 104.0, 104.0],
        lows=[100.0, 100.0, 99.0, 99.0, 99.0],
        opens=[100.0] * 5,
        closes=[102.0, 103.0, 100.0, 100.0, 100.0],
    )
    forward = resolve_forward_exit_scaled(frame, 0, horizon=5)
    labeled = label_candidate_scaled(frame, 0, horizon=5)
    assert forward is not None and labeled is not None
    assert forward.status == "closed"
    assert forward.outcome == labeled.outcome
    assert forward.label == labeled.label
    assert forward.exit_offset == labeled.exit_offset
    assert forward.realized_ret == pytest.approx(labeled.realized_ret)


def test_resolve_forward_exit_scaled_timeout_without_tp1() -> None:
    frame = _ohlc_frame(
        n=3,
        highs=[101.0, 101.0, 101.0],
        lows=[99.5, 99.5, 99.5],
        closes=[100.5, 100.5, 101.0],
    )
    outcome = resolve_forward_exit_scaled(frame, 0, horizon=3)
    assert outcome is not None
    assert outcome.status == "closed"
    assert outcome.outcome == "timeout"
    assert outcome.exit_offset == 3
    assert outcome.realized_ret == pytest.approx(0.01)


def test_mainline_tp_path_still_uses_fixed_barriers() -> None:
    # High enough for scaled TP1 (2.5) but not TP5 — mainline stays open/timeout
    frame = _ohlc_frame(
        n=3,
        highs=[103.0, 103.0, 103.0],
        lows=[99.5, 99.5, 99.5],
        closes=[102.0, 102.0, 102.0],
    )
    mainline = resolve_forward_exit(frame, 0)  # uses HORIZON 72 → open
    assert mainline is not None
    assert mainline.status == "open"
    scaled = resolve_forward_exit_scaled(frame, 0, horizon=3)
    assert scaled is not None
    assert scaled.status == "closed"
    assert scaled.outcome in {"scaled", "scaled_timeout", "timeout"}


def test_h1_shadow_refuses_mainline_log_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point mainline constant check at a temp path and attempt write there.
    main = tmp_path / "forward_log.csv"
    main.write_text("source\n", encoding="utf-8")
    monkeypatch.setattr("src.judgment.forward.FORWARD_LOG_PATH", main)
    with pytest.raises(ValueError, match="mainline"):
        run_forward_tracking_h1_shadow(output_path=main)


def test_shadow_default_path_differs_from_mainline() -> None:
    assert FORWARD_LOG_H1_SCALED_PATH.name == "forward_log_h1_scaled.csv"
    assert FORWARD_LOG_H1_SCALED_PATH != FORWARD_LOG_PATH
    assert FORWARD_LOG_PATH.name == "forward_log.csv"
