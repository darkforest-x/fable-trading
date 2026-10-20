import numpy as np
import pandas as pd

from yoyo.evaluation.eth_h1_gainers_mfe16 import event_stats, matched_vol, paths, rank_inputs


def panel(n=120, k=4):
    rng = np.random.default_rng(7)
    idx = pd.date_range("2026-08-01", periods=n, freq="h", tz="UTC")
    c = pd.DataFrame(100 * np.exp(rng.normal(0, .01, (n, k)).cumsum(axis=0)), index=idx,
                     columns=[f"S{j}" for j in range(k)])
    o = c.shift().fillna(c.iloc[0])
    return dict(open=o, high=np.maximum(o, c) + .5, low=np.minimum(o, c) - .5, close=c,
                volume=pd.DataFrame(1., index=idx, columns=c.columns))


def test_rank_inputs_ignore_future_bars():
    p = panel()
    cut = 90
    full = rank_inputs(p)
    pre = rank_inputs({k: v.iloc[:cut] for k, v in p.items()})
    for a, b in zip(full, pre):
        pd.testing.assert_frame_equal(a.iloc[:cut], b)


def test_r24_and_beijing_midnight_anchor():
    p = panel()
    _, r24, bjt, _ = rank_inputs(p)
    c, o = p["close"], p["open"]
    i = 50
    assert np.isclose(r24.iloc[i, 0], c.iloc[i, 0] / c.iloc[i-24, 0] - 1)
    # Bar opening 15:00 UTC decides at 16:00 = Beijing midnight: anchor is the previous day.
    t = pd.Timestamp("2026-08-03 15:00", tz="UTC")
    a = pd.Timestamp("2026-08-02 16:00", tz="UTC")
    assert np.isclose(bjt.loc[t, "S0"], c.loc[t, "S0"] / o.loc[a, "S0"] - 1)
    t = pd.Timestamp("2026-08-03 16:00", tz="UTC")
    assert np.isclose(bjt.loc[t, "S0"], c.loc[t, "S0"] / o.loc[t, "S0"] - 1)


def test_gap_in_rank_window_removes_eligibility():
    p = panel()
    p["close"].iloc[40, 1] = np.nan
    elig, r24, _, _ = rank_inputs(p)
    assert not elig.iloc[40:65, 1].any() and elig.iloc[65, 1]
    assert r24.iloc[40:65, 1].isna().all()


def test_peak_hour_and_pre_peak_drawdown():
    idx = pd.date_range("2026-08-01", periods=20, freq="h", tz="UTC")
    f = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1.), index=idx)
    f.iloc[0, f.columns.get_loc("high")] = 500  # signal bar extreme is excluded
    f.iloc[4, f.columns.get_loc("low")] = 90
    f.iloc[6, f.columns.get_loc("high")] = 120
    f.iloc[9, f.columns.get_loc("low")] = 80  # after the peak
    r = paths(f, 16, .002).iloc[0]
    assert r.peak_hour == 6 and np.isclose(r.mfe_bp, 2000)
    assert np.isclose(r.mae_before_peak_bp, -1000) and np.isclose(r.mae_bp, -2000)
    assert np.isnan(paths(f, 16, .002).mfe_bp.iloc[5])


def test_vol_match_excludes_board_and_breaks_ties_by_symbol():
    v = pd.Series({"A": .02, "B": .019, "C": .021, "D": .05, "E": np.nan})
    assert matched_vol(v, ["A"], ["C", "B", "D", "E"], 2) == {"A": ["B", "C"]}
    assert matched_vol(v, ["E"], ["B"], 1) == {"E": []}


def test_event_stats_signs():
    s = event_stats([1., 2., 3.], 999, 1)
    assert s["n"] == 3 and s["positive"] == 3 and s["ci_low"] > 0
