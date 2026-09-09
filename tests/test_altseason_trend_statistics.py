"""Synthetic counterexamples for altseason matching and monthly evidence."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.altseason_trend_statistics import (
    event_descriptives, holm, match_indexes, paired_block_inference,
    risk_sized_return, score_diagnostics,
)


def features(n=30):
    return pd.DataFrame({"vol_bucket": 2, "regime": "strong", "net_return": 0.},
                        index=pd.date_range("2025-01-01", periods=n, freq="4h", tz="UTC"))


def events(n=20):
    return pd.DataFrame({"valid": True, "natural_exit": True, "censored": False,
                         "gross_return": np.linspace(-.098, .102, n),
                         "net_return": np.linspace(-.1, .1, n), "initial_risk_frac": .02,
                         "score": np.arange(n), "mfe_return": .2, "mae_return": .02,
                         "capture_ratio": .5, "hold_hours": 24., "month": "2025-01"})


def test_matching_preserves_full_stratum_never_self_or_reuses_or_reads_outcomes():
    f = features(180)
    f.loc[f.index[60:120], "regime"] = "other"
    f.loc[f.index[120:], "vol_bucket"] = 3
    candidates = list(range(0, len(f), 3))
    a = match_indexes(f, candidates, range(len(f)), 5)
    changed = f.copy()
    changed["net_return"] = np.linspace(-1e99, 1e99, len(f))
    assert a == match_indexes(changed, candidates, range(len(f)), 5)
    controls = [j for j in a.values() if j is not None]
    assert len(controls) == len(set(controls))
    for i, j in a.items():
        assert i != j and j is not None
        assert f.vol_bucket.iloc[i] == f.vol_bucket.iloc[j]
        assert f.regime.iloc[i] == f.regime.iloc[j]
        di, dj = f.index[[i, j]] + pd.Timedelta(hours=4)
        assert (di.strftime("%Y-%m"), di.hour) == (dj.strftime("%Y-%m"), dj.hour)


def test_matching_shortage_and_small_derangement_are_explicit():
    f = features(20)
    for seed in range(20):
        pairs = match_indexes(f, [0, 6, 12], [0, 6, 12], seed)
        assert all(i != j and j is not None for i, j in pairs.items())
        assert len(set(pairs.values())) == 3
    assert match_indexes(f, [0], [0], 1) == {0: None}
    short = match_indexes(f, [0, 6, 12], [0, 6], 2)
    assert sum(j is None for j in short.values()) == 1
    f.loc[f.index[0], "vol_bucket"] = np.nan
    assert match_indexes(f, [0], range(20), 1) == {0: None}


def test_matching_month_uses_decision_not_bar_open():
    f = features(190)
    f.index = pd.date_range("2025-01-01 20:00", periods=190, freq="4h", tz="UTC")
    assert f.index[180] == pd.Timestamp("2025-01-31 20:00", tz="UTC")
    # It is February at the decision; January's same-hour candidate cannot match.
    assert match_indexes(f, [180], [0], 1) == {180: None}
    assert match_indexes(f, [180], [186], 1) == {180: 186}


def test_months_not_coin_counts_are_independent_units():
    values = [1.] * 100 + [-1., 1., -1., 1., -1.]
    months = ["2025-01"] * 100 + [f"2025-{i:02d}" for i in range(2, 7)]
    result = paired_block_inference(values, months, 4)
    assert result["n_pairs"] == 105 and result["n_blocks"] == 6
    assert result["mean"] == pytest.approx(0)
    assert result["p"] > .5
    assert result["method"] == "exact_month_sign_flip"


def test_exact_positive_zero_missing_and_single_month():
    positive = paired_block_inference(np.ones(6), list("abcdef"), 1)
    assert positive["p"] == pytest.approx(1 / 64)
    zero = paired_block_inference(np.zeros(6), list("abcdef"), 1)
    assert zero["p"] == 1 and zero["ci_low"] == zero["ci_high"] == 0
    single = paired_block_inference([2., np.nan, 9.], ["a", "b", None], 1)
    assert single["n_pairs"] == single["n_blocks"] == 1
    assert single["p"] is None and single["ci_low"] == single["ci_high"] == 2
    assert paired_block_inference([], [], 1)["mean"] is None


def test_monte_carlo_has_plus_one_and_is_repeatable():
    result = paired_block_inference(np.ones(17), np.arange(17), 7, n_resamples=999)
    assert result == paired_block_inference(np.ones(17), np.arange(17), 7, n_resamples=999)
    assert result["p"] >= .001
    assert result["method"] == "monte_carlo_month_sign_flip_plus_one"


def test_natural_win_and_profit_factor_exclude_censored_and_invalid():
    e = events(5)
    e["net_return"] = [.1, -.05, 100., -100., 0.]
    e.loc[2, ["natural_exit", "censored"]] = [False, True]
    e.loc[3, "valid"] = False
    result = event_descriptives(e)
    assert [result[k] for k in ["n", "n_valid", "n_natural", "n_censored"]] == [5, 4, 3, 1]
    assert result["win_rate"] == pytest.approx(1 / 3)
    assert result["profit_factor"] == 2
    assert result["mean_net_bp"] == pytest.approx((.1 - .05 + 100) / 4 * 1e4)


def test_abnormal_winner_and_no_fake_winners():
    e = events(10)
    e["net_return"] = [-.01] * 9 + [10.]
    result = event_descriptives(e)
    assert result["top3_positive_profit_share"] == 1
    assert result["net_ex_top3_mean_bp"] == pytest.approx(-100)
    e["net_return"] = -.02
    result = event_descriptives(e)
    assert result["top3_positive_profit_share"] is None
    assert result["net_ex_top3_mean_bp"] == pytest.approx(-200)
    assert event_descriptives(pd.DataFrame())["n_valid"] == 0


def test_risk_sizing_cash_fee_cap_and_invalid_risk():
    assert risk_sized_return(.2, .04) == pytest.approx(.05)
    assert risk_sized_return(.2, .001) == pytest.approx(.2 / 1.001)
    assert np.isnan(risk_sized_return(.2, 0))
    assert np.allclose(risk_sized_return([.2, -.2], [.04, .04]), [.05, -.05])


def test_auc_ties_and_top_decile_are_selected_on_score():
    e = events(20)
    result = score_diagnostics(e, 1)
    assert result["auc"] == 1 and result["top_n"] == 2
    assert result["top_mean_net_bp"] == pytest.approx(e.net_return.tail(2).mean() * 1e4)
    e["score"] = 1.
    result = score_diagnostics(e, 1)
    assert result["auc"] == .5
    assert result["top_n"] == 20
    assert result["top_mean_net_bp"] == pytest.approx(e.net_return.mean() * 1e4)
    e["net_return"] = -.01
    assert score_diagnostics(e, 1)["auc"] is None
    assert score_diagnostics(e.head(5), 1)["top_n"] == 0


def test_top_decile_uses_monthly_risk_excess_and_honest_test_name():
    e = events(60)
    e["matched_net_return"] = 0.
    e["risk_sized_excess"] = .01
    e.loc[54:, "month"] = [f"2025-{i:02d}" for i in range(1, 7)]
    result = score_diagnostics(e, 1)
    assert result["top_n"] == result["top_matched_n"] == result["top_n_blocks"] == 6
    assert result["top_mean_risk_sized_excess"] == pytest.approx(.01)
    assert result["top_p"] == pytest.approx(1 / 64)
    assert "not ranking-label" in result["top_test_description"]
    e["month"] = "2025-01"
    assert score_diagnostics(e, 1)["top_p"] is None


def test_holm_keeps_predeclared_missing_tests_and_validates():
    result = holm([.04, .001, None], total_tests=3)
    assert result == [.08, .003, None]
    assert holm([.5, .5, np.nan], 4) == [1., 1., None]
    with pytest.raises(ValueError):
        holm([.1, .2], 1)
    with pytest.raises(ValueError):
        holm([1.01])


def test_lowest_volatility_bucket_is_a_valid_matching_stratum():
    f = features(20)
    f["vol_bucket"] = 0
    assert match_indexes(f, [0], [6], 1) == {0: 6}


def test_statistics_does_not_add_four_hours_to_engine_decision_time():
    from yoyo.evaluation.altseason_trend_statistics import _months
    e = pd.DataFrame({"signal_time": pd.to_datetime(["2025-01-31T20:00:00Z", "2025-02-01T00:00:00Z"])})
    assert list(_months(e)) == ["2025-01", "2025-02"]
