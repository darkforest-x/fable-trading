"""Economic distinctions that must survive the V1+ reporting layer."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_v1_plus_report import BASELINE, PLUS, metrics, exact_pairs
from yoyo.evaluation.spike_v1_plus_controls import protection_outcome
from yoyo.evaluation.spike_v1_plus_report import concentration_table


def rows():
    common = dict(stream_key="okx_60_x", side=1, entry_price=100., entry_time="2025-10-01T01:00Z", exit_time="2025-10-01T03:00Z", signal_bar_open="2025-10-01T00:00Z", exit_reason="stop", mfe_r=20.)
    return pd.DataFrame([{**common, "cohort":BASELINE, "net_return":.12, "net_r":12., "censored":False},
                         {**common, "cohort":PLUS, "net_return":-.002, "net_r":-.2, "censored":False},
                         {**common, "signal_bar_open":"2025-10-02T00:00Z", "cohort":PLUS, "net_return":.8, "net_r":80., "censored":True}])


def test_censored_peak_is_not_realized_winner():
    m = metrics(rows().loc[lambda t:t.cohort.eq(PLUS)])
    assert m["entries"] == 2 and m["realized"] == 1 and m["realized_ge10r"] == 0
    assert m["mfe_ge10r"] == 1 and m["net_r_sum"] == -.2


def test_exact_pair_does_not_replace_lost_trend_with_new_censored_trade():
    p = exact_pairs(rows())
    assert len(p) == 2
    assert p.baseline_realized_ge10r.sum() == 1
    assert p.same_entry_retained_ge10r.sum() == 0
    assert p.realized_net_r_change.dropna().iloc[0] == -12.2


def test_duplicate_entry_is_rejected():
    t = rows()
    with pytest.raises(ValueError, match="ambiguous"):
        exact_pairs(pd.concat([t, t.iloc[[0]]]))


def test_empty_side_has_unknown_win_rate():
    m = metrics(rows().iloc[:0])
    assert m["entries"] == 0 and np.isnan(m["win_rate"])


def test_concentration_excludes_boundary_estimates_and_keeps_losses():
    t = rows().assign(asset="ETH", timeframe_min=60)
    result = concentration_table(t)
    plus = result.loc[result.cohort.eq(PLUS) & result.period.eq("full")].iloc[0]
    assert plus.total_net_r == -.2
    assert plus.without_top10_net_r == -.2
    assert pd.isna(plus.top_asset_positive_r_share)


def test_null_benchmark_checks_stop_on_entry_bar_before_profit():
    a = {"open":np.array([100.,100.]), "high":np.array([101.,110.]), "low":np.array([99.,95.]),
         "close":np.array([100.,108.]), "atr":np.array([1.,1.]),
         "recentLow":np.array([99.,95.]), "recentHigh":np.array([101.,110.])}
    result = protection_outcome(a,0,1,True,.01,1)
    # 2-ATR initial SL is98; same-bar high cannot activate protection before that stop.
    assert result["censored"] is False
    assert result["net_return"] == pytest.approx(-.022)
    assert result["net_r"] == pytest.approx(-1.1)


def test_null_benchmark_does_not_admit_gap_beyond_initial_stop():
    a = {"open":np.array([100.,95.]), "high":np.array([101.,100.]), "low":np.array([99.,94.]),
         "close":np.array([100.,98.]), "atr":np.array([1.,1.]),
         "recentLow":np.array([99.,94.]), "recentHigh":np.array([101.,101.])}
    assert protection_outcome(a,0,1,True,.01,1) is None
