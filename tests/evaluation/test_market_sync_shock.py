"""Causality and mechanics of the market-wide synchronized shock study."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import market_sync_shock as m


def bars(rets, volume=None, start="2024-01-01"):
    index = pd.date_range(start, periods=len(rets), freq="15min", tz="UTC")
    opens = np.full(len(rets), 100.0)
    return pd.DataFrame({"open": opens, "close": opens * (1 + np.asarray(rets, float)),
                         "volume": np.ones(len(rets)) if volume is None else np.asarray(volume, float)}, index=index)


def test_features_at_t_ignore_every_later_bar():
    rng = np.random.default_rng(0)
    base = bars(rng.normal(0, .01, 300), rng.uniform(1, 2, 300))
    changed = base.copy()
    changed.iloc[200:, :] = changed.iloc[200:, :] * 7
    a, b = m.causal_features(base, 96), m.causal_features(changed, 96)
    pd.testing.assert_frame_equal(a.iloc[:200], b.iloc[:200])
    assert a.z.iloc[:96].isna().all() and np.isfinite(a.z.iloc[96])


def test_current_bar_is_excluded_from_its_own_baseline():
    rets = [0.001] * 96 + [0.05]
    f = m.causal_features(bars(rets, [1.0] * 96 + [10.0]), 96)
    assert f.z.iloc[-1] == pytest.approx(50.0) and f.vr.iloc[-1] == pytest.approx(10.0)


def test_resample_keeps_only_complete_valid_buckets():
    ts = np.arange(0, 12) * 300_000
    raw = pd.DataFrame({"ts": np.delete(ts, 7), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})
    out = m.resample(raw, 15)
    assert list(out.index) == [0, 900_000, 2_700_000]


def frame(z, vr, ret):
    return pd.DataFrame({"z": z, "vr": vr, "ret": ret})


def test_detect_needs_both_leaders_same_side_volume_breadth_and_respects_cooldown():
    n = 30
    up = np.full(n, .01)
    z = np.zeros(n); z[[3, 5, 20]] = 3.5
    vr = np.full(n, 4.0)
    breadth = pd.Series(np.full(n, .8))
    btc = frame(z, vr, up)
    eth = frame(z, vr, up)
    ev = m.detect(btc, eth, breadth, z_min=3, v_min=3, b_min=.75, kind="sync", cooldown=12)
    assert ev.i.tolist() == [3, 20] and ev.side.tolist() == [1, 1]
    eth_down = frame(z, vr, -up)
    assert m.detect(btc, eth_down, breadth, z_min=3, v_min=3, b_min=.75, kind="sync", cooldown=12).empty
    assert m.detect(btc, frame(z, np.full(n, 2.0), up), breadth, z_min=3, v_min=3, b_min=.75, kind="sync", cooldown=12).empty
    narrow = pd.Series(np.full(n, .3))
    assert m.detect(btc, eth, narrow, z_min=3, v_min=3, b_min=.75, kind="sync", cooldown=12).empty
    assert m.detect(btc, eth, narrow, z_min=3, v_min=3, b_min=.75, kind="lead", cooldown=12).i.tolist() == [3, 20]


def test_trade_enters_next_open_exits_h_close_and_basket_uses_mask():
    opens = np.array([[1., 1.], [2., 4.], [3., 5.], [4., 6.]])
    closes = opens + .5
    i = np.array([0, 2])
    single = m.trade_returns(opens[:, [0]], closes[:, [0]], i, 2)[:, 0]
    assert single[0] == pytest.approx(3.5 / 2 - 1) and np.isnan(single[1])
    mask = np.array([[True, False], [True, True]])
    basket = m.trade_returns(opens, closes, np.array([0, 1]), 1, mask)
    assert basket[0] == pytest.approx(2.5 / 2 - 1)
    assert basket[1] == pytest.approx(np.mean([3.5 / 3 - 1, 5.5 / 5 - 1]))


def test_month_block_sign_flip_separates_signal_from_noise():
    months = np.repeat(np.arange(24), 5).astype(str)
    assert m.month_block_sign_flip(np.full(120, .01), months, 0, 4000) < .001
    noise = np.random.default_rng(1).normal(0, .01, 120)
    assert m.month_block_sign_flip(noise - noise.mean(), months, 0, 4000) > .5


def test_universe_for_a_month_ranks_only_the_previous_month(monkeypatch):
    days = pd.date_range("2024-01-01", "2024-02-29", freq="1D", tz="UTC")

    def fake(symbol, start, end):
        ts = (days.asi8 // 10**6)
        vol = np.where(days < pd.Timestamp("2024-02-01", tz="UTC"), 1.0 if symbol == "AUSDT" else 2.0,
                       100.0 if symbol == "AUSDT" else 1.0)
        return pd.DataFrame({"ts": ts, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": vol})

    monkeypatch.setattr(m, "read_5m", fake)
    out = m.monthly_universe(["AUSDT", "BUSDT", "BTCUSDT", "USDCUSDT"], pd.Timestamp("2024-02-01", tz="UTC"),
                             pd.Timestamp("2024-03-01", tz="UTC"), 1)
    assert out["2024-02"] == ["BUSDT"] and out["2024-03"] == ["AUSDT"]
