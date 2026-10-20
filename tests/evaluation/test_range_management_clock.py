"""Management ATR causality, cache identity, and native close-order timing."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.monday_range_v2_2_reference import atr
from yoyo.evaluation.pepe_range_exits import prepare
from yoyo.evaluation.range_framework import management_context, prepared
from yoyo.evaluation.range_management_study import holm
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def frame():
    rng = np.random.default_rng(991)
    closes = 100+np.cumsum(rng.normal(0, .05, 800))
    opening = np.r_[closes[0], closes[:-1]]
    return pd.DataFrame(dict(open=opening, high=np.maximum(opening, closes)+.1,
                             low=np.minimum(opening, closes)-.1, close=closes),
                        index=pd.date_range("2025-01-01T00:05Z", periods=800, freq="5min"))


def test_holm_correction_keeps_hypothesis_order():
    np.testing.assert_allclose(holm([.1, .01, .02]), [.1, .03, .04])


def test_context_default_parity_actual_5m_atr_and_prefix_causality():
    data = frame()
    original, default = prepare(data), management_context(data)
    five = management_context(data, 5)
    for key in ("bars", "hour", "atr", "signal", "observed"):
        np.testing.assert_array_equal(original[key], default[key])
    np.testing.assert_array_equal(five["atr"], atr(data).to_numpy())
    np.testing.assert_array_equal(five["hour"], original["hour"])
    assert np.isnan(five["atr"][:13]).all()
    assert np.isfinite(five["atr"][13:]).all()
    assert np.isnan(default["atr"][data.index.minute % 15 != 0]).all()
    np.testing.assert_array_equal(management_context(data.iloc[:467], 5)["atr"],
                                  five["atr"][:467])
    with pytest.raises(ValueError):
        management_context(data, 30)


def test_management_clock_cache_cannot_reuse_wrong_atr(tmp_path):
    data = frame()
    bars = data.reset_index(drop=True)
    bars.insert(0, "ts", (data.index.as_unit("ns").asi8//1_000_000)-300_000)
    bars.to_csv(tmp_path/"bars.csv", index=False)
    pd.DataFrame([dict(pair_id=0, entry_i=200, side=1, segment=0, week=1,
                       bucket=0, end_i=700, entry_time=str(data.index[200]),
                       population="signal")]).to_csv(tmp_path/"entries.csv", index=False)
    cfg = dict(data=str(tmp_path/"bars.csv"), entry_candidates=str(tmp_path/"entries.csv"),
               stop_window_5m=5, buffer_percent=.05, tick=.01)
    original, first_entries, hit = prepared(cfg, tmp_path/"cache")
    assert not hit
    five, next_entries, hit = prepared(dict(cfg, management_minutes=5), tmp_path/"cache")
    assert not hit
    pd.testing.assert_frame_equal(first_entries, next_entries)
    _, _, hit = prepared(dict(cfg, management_minutes=5), tmp_path/"cache")
    assert hit
    assert not np.array_equal(original["atr"], five["atr"], equal_nan=True)


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("opposite", [False, True])
def test_native_off_quarter_activation_and_next_bar_protection(side, opposite):
    bars = np.array([[100, 110, 90, 100], [100, 106, 99.5, 105],
                     [105, 106, 103, 104], [104, 105, 102, 103]], dtype=float)
    if side == -1:
        bars = 200-bars
        bars[:, [1, 2]] = bars[:, [2, 1]]
    data = pd.DataFrame(bars, columns=["open", "high", "low", "close"],
                        index=pd.date_range("2025-01-01T00:50Z", periods=4, freq="5min"))
    entry = pd.DataFrame([dict(trade_id="clock", entry_i=0, side=side, entry=100,
                               stop=100-side, risk=1, end_i=3)])
    # Wide ATR isolates H1 reversal; tight ATR isolates a newly tightened stop.
    value = 4 if opposite else .25
    hour = np.array([0, 0, -side if opposite else 0, 0])
    five, _, _ = run_native(data, np.array([np.nan, value, value, value]), hour,
                            entry, ExitPolicy(), tick=.01, symbol="TEST")
    fifteen, _, _ = run_native(data, np.array([np.nan, np.nan, value, np.nan]), hour,
                               entry, ExitPolicy(), tick=.01, symbol="TEST")
    assert five.iloc[0].arm_i == 1
    assert five.iloc[0].exit_i == 2
    assert five.iloc[0].reason == (3 if opposite else 2)
    assert five.iloc[0].exit_price == pytest.approx(100+4*side)
    assert fifteen.iloc[0].arm_i == -1
    assert fifteen.iloc[0].reason == 0
