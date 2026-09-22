"""Contract tests for the SPIKE V1 vs V12.6 low-timeframe replay builder."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_lowtf_v1_v126 as lowtf

ETH15 = Path("data/kline_deep/okx_ETH_USDT_SWAP_15m_158499.csv")


def _minutes(n: int, start: str = "2026-01-01T00:00Z", seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, .1, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + .05
    low = np.minimum(open_, close) - .05
    index = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=index)


def test_complete_bars_drop_incomplete_and_invalid_buckets():
    base = _minutes(12)
    base = base.drop(base.index[4])                     # bucket 03:00-05:59 loses a minute
    base.iloc[7, base.columns.get_loc("high")] = 0.0    # invalid row poisons bucket 06-08
    bars, dropped = lowtf.complete_bars(base, 3)
    assert list(bars.index.minute) == [0, 9]
    assert dropped == 2
    first = base.iloc[:3]
    assert bars.iloc[0].open == first.open.iloc[0] and bars.iloc[0].close == first.close.iloc[-1]
    assert bars.iloc[0].high == first.high.max() and bars.iloc[0].volume == 3.0


def test_htf_map_matches_pine_auto_rule():
    text = Path("yoyo/evaluation/pine/spike_burst_v12_6.pine").read_text()
    assert 's <= 60 ? "5" : s <= 300 ? "15" : s <= 900 ? "60"' in text
    assert lowtf.HTF_OF == {1: 5, 3: 15, 5: 15}
    assert "timeframe.multiplier == 15" in text and "timeframe.multiplier == 5" in text


def test_serial_skips_while_in_position():
    times = pd.date_range("2026-01-01", periods=10, freq="1min", tz="UTC")

    def evaluate(i):
        return "closed", {"signal_i": i, "exit_i": i + 3, "censored": False}

    trades, statuses = lowtf.serial([1, 2, 5, 6], "v126", evaluate, 10, "k", times)
    assert [t["signal_i"] for t in trades] == [1, 5]
    assert [s["status"] for s in statuses] == ["closed", "skipped_in_position", "closed", "skipped_in_position"]


def test_controls_are_deterministic_and_stratified():
    cfg = lowtf.config()
    n = 400
    index = pd.date_range("2026-08-31T20:00Z", periods=n, freq="1min", tz="UTC")
    close = np.full(n, 100.0)
    atr = np.where(np.arange(n) < 200, .03, .3)         # two buckets: 0.0003 and 0.003
    f = pd.DataFrame({"close": close, "atr": atr}, index=index)
    gap, ready = np.zeros(n, bool), np.ones(n, bool)
    seen = []

    def evaluate(j):
        seen.append(j)
        return "closed", {"net_r": float(j), "net_return": 0.0, "censored": False}

    trades = [{"trade_key": "a", "arm": "v126", "signal_i": 10, "censored": False},
              {"trade_key": "b", "arm": "v126", "signal_i": 300, "censored": False}]
    one = lowtf.controls(f, gap, ready, trades, evaluate, minutes=1, cfg=cfg)
    two = lowtf.controls(f, gap, ready, trades, evaluate, minutes=1, cfg=cfg)
    pd.testing.assert_frame_equal(one, two)
    assert 0 <= one.control_signal_i[0] < 200 and one.control_signal_i[0] != 10
    assert 200 <= one.control_signal_i[1] < n and one.control_signal_i[1] != 300
    assert one.vol_bin.tolist() == [0, 3]


def test_non_5m_charts_have_no_ma_gate():
    bars = lowtf.complete_bars(_minutes(3000), 3)[0]
    base5m = lowtf.complete_bars(_minutes(3000), 5)[0]
    facts = lowtf.pine_facts(bars, base5m, "ETH", 0.01, 3)
    assert np.isnan(facts["ma"]).all()
    five = lowtf.pine_facts(base5m, base5m, "ETH", 0.01, 5)
    # 5m requires 1200 contiguous completed 15m buckets; 3000 minutes cannot reach it.
    assert np.isnan(five["ma"]).all() and not five["v9"].any()


@pytest.mark.skipif(not ETH15.exists(), reason="local OKX ETH 15m cache absent")
def test_v1_native_rows_match_two_year_ledger_rule():
    from yoyo.evaluation import spike_v1_twoyear_allmarkets as old
    from yoyo.evaluation.spike_burst_replay import features, replay

    raw = pd.read_csv(ETH15, usecols=["ts", "open", "high", "low", "close", "volume"])
    raw = raw.loc[(raw.ts >= pd.Timestamp("2024-06-01T00:00Z").value // 10**6)
                  & (raw.ts < pd.Timestamp("2025-06-01T00:00Z").value // 10**6)]
    bars = pd.DataFrame(raw[["open", "high", "low", "close", "volume"]].to_numpy(float),
                        index=pd.to_datetime(raw.ts.to_numpy(), unit="ms", utc=True),
                        columns=["open", "high", "low", "close", "volume"])
    step = pd.Timedelta(minutes=15).value
    bars = np.split(bars, np.flatnonzero(np.diff(bars.index.asi8) != step) + 1)[0]
    item = {"venue": "okx", "symbol": "ETH-USDT-SWAP", "asset": "ETH", "tick": .01}
    expected = pd.DataFrame(old._trade_rows(item, bars, 15))
    ff = features(bars)
    got = pd.DataFrame(lowtf.v1_native_rows(bars, replay(ff, .01), ff, minutes=15, start=old.START, end=old.END, key="k"))
    assert len(expected) > 5 and len(got) == len(expected)
    for col in ("entry_time", "entry_price", "exit_time", "exit_price", "exit_reason", "net_return", "censored"):
        assert got[col].tolist() == expected[col].tolist(), col
    np.testing.assert_allclose(got.net_r.to_numpy(float), expected.net_r.to_numpy(float), equal_nan=True)
