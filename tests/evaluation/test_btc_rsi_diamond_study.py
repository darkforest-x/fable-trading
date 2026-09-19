"""Independent vector-label and partial-position marked-accounting checks."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import btc_rsi_diamond_study as study
from yoyo.evaluation.btc_rsi_diamond_scaleout import replay, trace_position


def case():
    ix = pd.date_range("2025-01-01T00:00Z", periods=36, freq="5min")
    prices = np.full(36, 100.)
    prices[6:10] = [90, 80, 70, 60]
    f = pd.DataFrame(dict(open=prices, close=prices, high=prices+1, low=prices-1, volume=1.), index=ix)
    s = pd.DataFrame(dict(strong_side=[1]*5+[-1]*4), index=ix[1:10])
    return f,s


@pytest.mark.parametrize("side", [-1,1])
@pytest.mark.parametrize("end_i", [8,12])
def test_vector_labels_match_reference_including_partial_censoring(side,end_i):
    f,s = case()
    s.strong_side *= side
    entries = f.index[[4,5,6]]
    values, remaining, _ = study.fast_outcomes(f,s,entries,side,f.index[end_i])
    for j,entry in enumerate(entries):
        ref = trace_position(f,s,entry,side,f.index[end_i])["positions"].iloc[0]
        assert values[j] == pytest.approx(ref.terminal_marked_net_return)
        assert remaining[j] == pytest.approx(ref.remaining_fraction)


def test_marked_curve_respects_shrinking_quantity_and_excludes_final_exit_bar_extremes(monkeypatch):
    f,s = case()
    f.loc[f.index[9], ["high","low","close"]] = [1000,1,999]
    monkeypatch.setattr(study,"START",f.index[0])
    monkeypatch.setattr(study,"END",f.index[12])
    positions,curve = study.path_metrics(f,replay(f,s,f.index[0],f.index[12]))
    assert positions.iloc[0].raw_price_mae == pytest.approx(-.4)
    assert curve.net_pnl.iloc[6] == pytest.approx(-.102)
    assert curve.net_pnl.iloc[-1] == pytest.approx(-.252)
    assert positions.iloc[0].partial_exits == 4


def test_vector_early_marks_do_not_use_post_split_prices():
    f,s = case()
    a = study.fast_outcomes(f,s,[f.index[5]],1,f.index[8])[0]
    f.loc[f.index[8]:,["open","high","low","close"]] = [900,1000,1,999]
    b = study.fast_outcomes(f,s,[f.index[5]],1,f.index[8])[0]
    np.testing.assert_array_equal(a,b)
