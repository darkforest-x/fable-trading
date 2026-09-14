"""Outcome algebra and development-only selection tripwires, no market input."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.release_eth_metrics import holm, inference, monthly_returns, settle
from yoyo.evaluation.release_eth_multitf import assert_frozen_prefix, features, select


def test_administrative_fee_updates_nonrecorded_peak_drawdown():
    f = pd.DataFrame({"open_time": pd.date_range("2024-01-01", periods=3, freq="15min", tz="UTC"), "close": 100.})
    p = dict(direction=1, qty=20., entry_price=100., entry_fee=2., entry_i=1,
             entry_time=f.open_time.iloc[1], initial_stop=97.)
    result = dict(final_equity=498., trades=pd.DataFrame(), open_position=p, fees=2., cash=498.,
                  equity=pd.DataFrame(), max_drawdown_path=.004, max_drawdown_close=.004,
                  path_peak=500., close_peak=500., nonpositive_equity_seen=False)
    settled = settle(result, f, 3, .001, 15)
    assert settled["final_equity"] == 496
    assert settled["max_drawdown_path"] == pytest.approx(.008)
    assert settled["max_drawdown_close"] == pytest.approx(.008)
    assert settled["trades"].net_pnl.sum() == -4
    assert settled["trades"].administrative_exit.all()


def test_no_match_inference_is_explicit_and_holm_retains_missing_test():
    t = pd.DataFrame({"score": [1., 2.], "net_return": [-.1, .2], "gross_return": [-.09, .21],
                      "signal_time": pd.to_datetime(["2024-01-02", "2024-01-03"], utc=True), "matched": False})
    r = inference(t)
    assert r["matched_p"] is None and r["matched_months"] == 0
    assert r["top_decile_control_bp"] is None
    assert r["matched_support_status"].startswith("unavailable")
    assert holm([.001, None, .04]) == pytest.approx([.003, 1., .08])


def test_month_close_belongs_to_month_just_ended():
    equity = pd.DataFrame({"time": pd.to_datetime(["2024-02-01", "2024-03-01"], utc=True), "equity": [550., 495.]})
    result = monthly_returns(equity)
    assert result.month.tolist() == ["2024-01", "2024-02"]
    assert result.return_pct.tolist() == pytest.approx([10., -10.])


def test_selection_cannot_consume_validation_and_does_not_chase_return_only():
    rows = []
    for minutes in (15, 60, 240):
        for period in ("dev2023", "dev2024"):
            for arm in ("C0", "C1", "C2", "C3", "C4", "C5"):
                stats = dict(natural_trades=12, return_pct=10 if arm == "C0" else 20,
                             excess_bp=2 if arm == "C2" else -1, nonpositive_equity_seen=False, path_dd_pct=5.)
                rows.append(dict(minutes=minutes, period=period, arm=arm, stats=stats))
    assert all(v["selected"] == "C2" for v in select(rows).values())
    rows[0]["period"] = "validation2025"
    with pytest.raises(ValueError, match="development"):
        select(rows)


def test_feature_prefix_causality_and_utc_calendar():
    times = pd.date_range("2023-01-01", periods=1000, freq="15min", tz="UTC")
    close = 100 + np.sin(np.arange(1000) / 20)
    raw = pd.DataFrame(dict(open_time=times, open=close, high=close+1, low=close-1, close=close, volume=1))
    whole = features(raw, 15, "C0")
    prefix = features(raw.iloc[:700], 15, "C0")
    pd.testing.assert_frame_equal(whole.iloc[:len(prefix)].reset_index(drop=True), prefix)
    expected = ~whole.open_time.dt.hour.between(21, 22) & whole.open_time.dt.dayofweek.ne(6)
    assert (whole.calendar_allowed == expected).all()


def test_validation_refuses_revised_development_prices():
    receipt = dict(consumed_prefix_sha256="a", rows=10, first_open="2023-01-01",
                   last_close="2025-01-01", end_exclusive="2025-01-01", source_mtime_ns=1)
    assert_frozen_prefix(dict(receipt, source_mtime_ns=2), receipt)
    with pytest.raises(ValueError, match="source changed"):
        assert_frozen_prefix(dict(receipt, consumed_prefix_sha256="b"), receipt)
