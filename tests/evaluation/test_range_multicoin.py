"""Input continuity and native fill visualization semantics."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.range_multicoin import aggregate_minutes, load_symbol
from yoyo.evaluation.range_native_preview import native_view


def minutes():
    ts = pd.date_range("2025-01-01", periods=10, freq="min", tz="UTC").as_unit("ns").asi8//1_000_000
    return pd.DataFrame(dict(ts=ts, open=2., high=3., low=1., close=2.5))


def test_continuity_rejects_missing_and_conflicting_rows():
    data = minutes()
    args = ("2025-01-01T00:00:00Z", "2025-01-01T00:10:00Z")
    assert len(aggregate_minutes(data, *args)) == 2
    with pytest.raises(ValueError, match="Missing"):
        aggregate_minutes(data.drop(index=3), *args)
    bad = data.iloc[[0]].copy()
    bad["close"] = 2.8
    with pytest.raises(ValueError, match="Conflicting"):
        aggregate_minutes(pd.concat([data, bad]), *args)


def test_native_chart_preserves_side_time_and_micro_price():
    data = pd.DataFrame(dict(ts=[0, 300_000], open=4e-6, high=5e-6, low=3e-6, close=4e-6))
    trades = pd.DataFrame(dict(trade_id=["s"], population=["signal"], side=[-1],
                               net_r=[2.], peak_r=[4.], reason=[3]))
    fills = pd.DataFrame(dict(trade_id=["s", "s"], phase=["entry", "3"],
                              ts_ns=[300_000_000_000, 600_000_000_000],
                              quantity=[1., 1.], price=[4e-6, 3.5e-6]))
    view = native_view(data, trades, fills, "PEPE", "1970-01-01T00:00Z", "1970-01-01T01:00Z")
    assert view._fills.order_side.tolist() == ["SELL", "BUY"]
    assert view._fills.action.tolist() == ["开空", "平空"]
    np.testing.assert_array_equal(view._fills.last_px, fills.price)
    assert view._fills.ts_init.tolist() == [bar["ts_init"] for bar in view._bars]


def test_native_sources_require_agreement_and_full_coverage(tmp_path):
    five = aggregate_minutes(minutes(), "2025-01-01T00:00Z", "2025-01-01T00:10Z")
    a, b = tmp_path/"a.csv", tmp_path/"b.csv"
    five.to_csv(a, index=False)
    five.iloc[[1]].to_csv(b, index=False)
    cfg = dict(warmup="2025-01-01T00:00Z", end="2025-01-01T00:10Z",
               native_5m_sources={"ZEC": {"paths": [str(a), str(b)], "receipts": []}})
    merged, _ = load_symbol("ZEC", cfg)
    assert len(merged) == 2
    five.loc[1, "close"] = 2.8
    five.iloc[[1]].to_csv(b, index=False)
    with pytest.raises(ValueError, match="conflicting duplicate"):
        load_symbol("ZEC", cfg)
    cfg["native_5m_sources"]["ZEC"]["paths"] = [str(b)]
    with pytest.raises(ValueError, match="missing"):
        load_symbol("ZEC", cfg)
