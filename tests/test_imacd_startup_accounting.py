"""Synthetic arithmetic and timing checks; no repository market data are read."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.imacd_startup_accounting import (
    compound_portfolio,
    outcome,
    outcome_arrays,
)


def bars(open_, close=None, high=None, low=None):
    open_ = np.asarray(open_, dtype=float)
    close = open_.copy() if close is None else np.asarray(close, dtype=float)
    return pd.DataFrame(
        {"open": open_, "high": np.maximum(open_, close) if high is None else high,
         "low": np.minimum(open_, close) if low is None else low, "close": close},
        index=pd.date_range("2024-01-01", periods=len(open_), freq="1h", tz="UTC"),
    )


def features(b, md, atr=10):
    return pd.DataFrame({"md": md, "atr": atr}, index=b.index)


def event(b, f, i, side, last, event_id):
    return dict(event_id=event_id, **outcome(b, f, i, side, last))


def test_long_next_open_neutral_exit_and_gap_extremes():
    b = bars([90, 100, 110, 120, 999], [91, 105, 107, 130, 999],
             high=[91, 108, 115, 900, 999], low=[90, 98, 102, 1, 999])
    f = features(b, [1, 1, 0, -1, -1])
    r = outcome(b, f, 0, 1, 4)
    assert r["entry_i"] == 1 and r["entry_price"] == 100
    assert r["exit_i"] == 3 and r["exit_price"] == 120
    assert r["gross_bp"] == pytest.approx(2000)
    assert r["net_bp"] == pytest.approx(1980)
    # Exit open gap is included; subsequent extreme exit-bar high/low are not.
    assert r["mfe_bp"] == pytest.approx(2000)
    assert r["mae_bp"] == pytest.approx(-200)
    assert r["mfe_atr"] == pytest.approx(2)
    assert r["mae_atr"] == pytest.approx(-0.2)
    assert r["hold_bars"] == 2
    assert r["entry_open_time"] == r["signal_decision_close_time"] == b.index[1].isoformat()
    assert r["exit_decision_close_time"] == r["exit_fill_open_time"] == b.index[3].isoformat()
    assert r["boundary_mark_close_time"] is None


def test_short_extremes_and_signal_md_does_not_exit_same_bar():
    b = bars([90, 100, 85, 80], [90, 93, 90, 50],
             high=[90, 104, 95, 500], low=[90, 92, 83, 1])
    f = features(b, [0, -1, 0, 1])
    r = outcome(b, f, 0, -1, 3)
    assert r["exit_i"] == 3 and r["exit_kind"] == "natural"
    assert r["gross_bp"] == pytest.approx(2000)
    assert r["mfe_bp"] == pytest.approx(2000)
    assert r["mae_bp"] == pytest.approx(-400)
    assert r["mfe_atr"] == pytest.approx(2)


@pytest.mark.parametrize("md", [[1, 1, 1, 1], [1, 1, 1, 0]])
def test_boundary_mark_includes_last_bar_and_last_close_fee(md):
    b = bars([90, 100, 110, 115], [90, 105, 115, 120],
             high=[90, 108, 117, 125], low=[90, 98, 109, 114])
    f = features(b, md)
    r = event(b, f, 0, 1, 3, "boundary")
    assert r["exit_kind"] == "boundary_mark"
    assert r["exit_price"] == 120 and r["hold_bars"] == 3
    assert r["mfe_bp"] == pytest.approx(2500)
    assert r["exit_fill_open_time"] is None
    assert r["boundary_mark_close_time"] == (b.index[3] + pd.Timedelta(hours=1)).isoformat()
    m, eq, accepted = compound_portfolio(b, [r], 0, 3)
    assert accepted == ["boundary"]
    assert m["ending_equity"] == pytest.approx(1.198)
    close = eq.loc[eq.kind == "close"]
    assert len(close) == len(b)
    assert close.iloc[-1].equity == pytest.approx(1.198)
    assert list(eq.kind[-3:]) == ["boundary_pre_fee", "boundary_exit_fee", "close"]


def test_fold_does_not_see_exit_or_high_low_beyond_last():
    b = bars([90, 100, 110, 115, 999], [90, 105, 115, 120, 999])
    f = features(b, [1, 1, 1, 1, 0])
    r = outcome(b, f, 0, 1, 3)
    b2 = b.copy()
    b2.loc[b.index[4], ["high", "low"]] = [999999, 0.001]
    assert outcome(b2, f, 0, 1, 3) == r


def test_atr_is_from_signal_close_and_missing_atr_stays_missing():
    b = bars([90, 100, 110, 120])
    f = features(b, [1, 1, 0, 0], [10, 2, 1, 0.1])
    assert outcome(b, f, 0, 1, 3)["mfe_atr"] == pytest.approx(2)
    f.loc[b.index[0], "atr"] = np.nan
    r = outcome(b, f, 0, 1, 3)
    assert np.isnan(r["mfe_atr"])
    assert r["gross_bp"] == pytest.approx(2000)


def test_batch_segment_extremes_match_naive_held_window():
    rng = np.random.default_rng(452)
    opens = 100 + rng.uniform(-5, 5, 512)
    closes = 100 + rng.uniform(-5, 5, 512)
    b = bars(opens, closes, np.maximum(opens, closes) + 2, np.minimum(opens, closes) - 2)
    f = features(b, rng.choice([-1, 0, 1], len(b)))
    indexes = rng.integers(0, 509, 800)
    sides = rng.choice([-1, 1], len(indexes))
    result = outcome_arrays(b, f, indexes, sides, 510)
    for row in result:
        stop = row["exit_i"] if row["exit_kind"] == "natural" else 511
        held = b.iloc[row["entry_i"]:stop]
        top = max(held.high.max(), row["entry_price"], row["exit_price"])
        bottom = min(held.low.min(), row["entry_price"], row["exit_price"])
        expected = (top - row["entry_price"]) if row["side"] == 1 else (row["entry_price"] - bottom)
        assert row["mfe_atr"] == pytest.approx(expected / 10)


def test_compounding_overlap_and_same_open_exit_then_reentry():
    b = bars([90, 100, 108, 110, 100, 99])
    f = features(b, [1, 1, 0, -1, -1, 0])
    first = event(b, f, 0, 1, 5, "first")
    duplicate = event(b, f, 1, 1, 5, "overlap")
    second = event(b, f, 2, -1, 5, "second")
    assert first["exit_i"] == second["entry_i"] == 3
    m, eq, accepted = compound_portfolio(b, [second, first, duplicate], 0, 5)
    assert accepted == ["first", "second"]
    assert m["trades"] == 2 and m["overlap_blocked"] == 1
    # +10% less20bp, then +10% less20bp, each on then-current equity.
    assert m["ending_equity"] == pytest.approx(1.098 ** 2)
    assert m["net_return_pct"] == pytest.approx((1.098 ** 2 - 1) * 100)
    kinds = list(eq.loc[(eq.bar_i == 3) & (eq.kind != "close"), "kind"])
    assert kinds == ["open", "exit_fee", "entry_fee"]


def test_boundary_does_not_allow_entry_at_same_bar_open():
    b = bars([90, 100, 105, 110])
    f = features(b, [1, 1, 1, 1])
    first = event(b, f, 0, 1, 3, "first")
    at_boundary = event(b, f, 2, 1, 3, "blocked")
    m, _, accepted = compound_portfolio(b, [first, at_boundary], 0, 3)
    assert accepted == ["first"]
    assert m["overlap_blocked"] == 1


def test_drawdown_counts_fee_jumps_and_idle_closes():
    b = bars([100, 100, 100, 100])
    f = features(b, [1, 0, 0, 0])
    r = event(b, f, 0, 1, 3, "flat")
    m, eq, _ = compound_portfolio(b, [r], 0, 3)
    assert m["net_return_pct"] == pytest.approx(-0.2)
    assert m["max_drawdown_pct"] == pytest.approx(0.2)
    assert eq.loc[eq.kind == "entry_fee", "equity"].iloc[0] == pytest.approx(0.999)
    assert eq.loc[eq.kind == "exit_fee", "equity"].iloc[0] == pytest.approx(0.998)
    assert eq.loc[(eq.kind == "close") & (eq.bar_i == 3), "equity"].iloc[0] == pytest.approx(0.998)


def test_drawdown_uses_open_gaps_and_does_not_fabricate_high_low_execution():
    b = bars([100, 100, 120, 110], [100, 130, 125, 110],
             high=[100, 140, 200, 200], low=[100, 1, 1, 1])
    f = features(b, [1, 1, 0, 0])
    r = event(b, f, 0, 1, 3, "long")
    m, _, _ = compound_portfolio(b, [r], 0, 3)
    # Peak after entry fee is1.299, final equity is1.098. Intrabar1 is not sampled.
    assert m["max_drawdown_pct"] == pytest.approx((1.299 - 1.098) / 1.299 * 100)
    assert m["sampled_drawdown_only"] is True
    assert m["insolvent"] is False


def test_insolvency_latches_and_does_not_compound_recovered_equity():
    b = bars([100, 100, 220, 90, 80, 70], [100, 110, 95, 90, 80, 70])
    f = features(b, [-1, -1, 0, 1, 1, 0])
    first = event(b, f, 0, -1, 5, "short")
    second = event(b, f, 2, 1, 5, "no_restart")
    m, eq, accepted = compound_portfolio(b, [first, second], 0, 5)
    assert accepted == ["short"]
    assert m["insolvent"] is True and m["insolvency_blocked"] == 1
    assert m["first_insolvent_phase"] == "open"
    assert m["first_insolvent_time"] == b.index[2].isoformat()
    assert eq.equity.min() == pytest.approx(-0.201)
    assert m["max_drawdown_pct"] > 100
    # Existing position follows its declared exit; no liquidation is invented.
    assert m["ending_equity"] == pytest.approx(1.098)
    assert m["liquidation_model"] is False


def test_empty_portfolio_is_cash_through_whole_fold():
    b = bars([100, 200, 10, 500])
    m, eq, accepted = compound_portfolio(b, [], 1, 3)
    assert m["ending_equity"] == 1 and m["net_return_pct"] == 0
    assert m["max_drawdown_pct"] == 0 and m["trades"] == 0
    assert accepted == [] and (eq.equity == 1).all()
    assert list(eq.loc[eq.kind == "close", "bar_i"]) == [1, 2, 3]
    assert outcome_arrays(b, features(b, [0, 0, 0, 0]), [], [], 3) == []


def test_validation_rejects_unfillable_or_mismatched_events():
    b = bars([100, 100, 100])
    f = features(b, [1, 0, 0])
    with pytest.raises(ValueError, match="entry open"):
        outcome(b, f, 2, 1, 2)
    with pytest.raises(ValueError, match="direction"):
        outcome(b, f, 0, 0, 2)
    with pytest.raises(ValueError, match="identical indexes"):
        outcome(b, f.iloc[::-1], 0, 1, 2)
    r = event(b, f, 0, 1, 2, "a")
    with pytest.raises(ValueError, match="fill prices"):
        compound_portfolio(b, [dict(r, entry_price=999)], 0, 2)
    with pytest.raises(ValueError, match="one symbol/timeframe"):
        compound_portfolio(b, [dict(r, symbol="BTC"), dict(r, symbol="ETH", event_id="b")], 0, 2)
