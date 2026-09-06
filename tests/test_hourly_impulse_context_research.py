"""Synthetic V3 gates, selection and complete-pair outcome accounting."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_context_research as r


def config():
    return {"selection": {"minimum_events": 80, "minimum_per_fold": 12,
            "positive_folds": 4, "minimum_profit_factor": 1.1,
            "minimum_active_months": 12, "minimum_months_per_fold": 3,
            "matched_coverage": .9}}


def passing():
    return ({"events": 80, "minimum_fold_events": 12, "positive_folds": 4,
             "mean_net_bp": 11, "profit_factor": 1.2,
             "extra_10bp_mean_net_bp": 1, "leave_top_two_mean_net_bp": 1},
            {"coverage": .9, "mean_excess_bp": 1}, {"mean_net_bp": 1},
            {"active_months": 12, "minimum_months_per_fold": 3})


def test_all_registered_gates_pass_at_inclusive_sample_edges():
    args = passing()
    assert all(r.development_gates(*args, config()).values())
    assert args == passing()


@pytest.mark.parametrize("position,key,value,gate", [
    (0, "events", 79, "samples"), (0, "minimum_fold_events", 11, "samples"),
    (0, "positive_folds", 3, "positive_folds"), (0, "profit_factor", 1.1, "profit_factor"),
    (0, "mean_net_bp", 0, "net_profit"), (0, "mean_net_bp", np.nan, "net_profit"),
    (0, "extra_10bp_mean_net_bp", 0, "cost_stress"),
    (0, "leave_top_two_mean_net_bp", 0, "leave_top_two"),
    (1, "coverage", .8999, "matched_coverage"),
    (1, "mean_excess_bp", 0, "matched_excess"), (1, "mean_excess_bp", None, "matched_excess"),
    (2, "mean_net_bp", 0, "single_position"),
    (3, "active_months", 11, "month_support"), (3, "minimum_months_per_fold", 2, "month_support"),
])
def test_each_failed_gate_prevents_promotion(position, key, value, gate):
    args = passing()
    args[position][key] = value
    assert not r.development_gates(*args, config())[gate]


def test_empty_metrics_fail_closed():
    gates = r.development_gates({}, {}, {}, {"active_months":0,"minimum_months_per_fold":0}, config())
    assert not any(gates.values())


def test_infinite_pf_passes_its_gate_but_not_other_safety_gates():
    args = passing()
    args[0]["profit_factor"] = np.inf
    args[0]["events"] = 2
    gates = r.development_gates(*args,config())
    assert gates["profit_factor"] and not gates["samples"]


def test_gates_precede_ranking_and_replay_cannot_be_finalist():
    rows = [
        {"arm":{"id":"original"}, "metrics":{"robust_score_bp":999,"worst_fold_bp":999}, "gates":{"ok":True}},
        {"arm":{"id":"prior4h_trend"}, "metrics":{"robust_score_bp":99,"worst_fold_bp":99}, "gates":{"ok":False}},
        {"arm":{"id":"prior4h_not_chasing"}, "metrics":{"robust_score_bp":1,"worst_fold_bp":1}, "gates":{"ok":True}},
    ]
    before = deepcopy(rows)
    assert r.choose_finalist(rows)["arm"]["id"] == "prior4h_not_chasing"
    assert rows == before
    rows[-1]["gates"]["ok"] = False
    assert r.choose_finalist(rows) is None


def test_holm_is_limited_to_two_new_tests_and_missing_is_not_significant():
    assert r.holm_two({"prior4h_trend":.03,"prior4h_not_chasing":.01}) == {"prior4h_not_chasing":.02,"prior4h_trend":.03}
    assert r.holm_two({"prior4h_trend":np.nan,"prior4h_not_chasing":.02})["prior4h_trend"] == 1
    with pytest.raises(ValueError):
        r.holm_two({"original":.01,"prior4h_trend":.01})


def test_month_support_respects_empty_fold_and_closed_finite_trade_only():
    t = pd.DataFrame({"entry_time":["2023-01-01","2023-02-01","2023-03-01"],
                      "fold":["a"]*3,"closed":[True,True,False],"net_return":[.1,np.nan,.1]})
    assert r.month_support(t,["a","b"]) == {"active_months":1,"minimum_months_per_fold":0,"by_fold":{"a":1,"b":0}}


def test_complete_three_control_pair_required_and_stale_pnl_overwritten():
    cases = pd.DataFrame({"event_id":["a","b","c"], "entry_time":pd.to_datetime(["2023-01-01","2023-02-01","2023-03-01"],utc=True),
                          "closed":[True,True,False],"net_return":[.02,.04,.99]})
    controls = pd.DataFrame({"parent_event_id":["a"]*3+["b"]*3+["c"]*3,
                            "entry_time":pd.date_range("2023-01-01",periods=9,freq="1h",tz="UTC"),
                            "closed":[True]*5+[False]+[True]*3,
                            "net_return":[.01]*9})
    assignments = pd.DataFrame({"event_id":["a","b","c"],"match_status":["matched"]*3,"event_net_return":[999]*3})
    final, info = r.attach_outcomes(assignments,cases,controls)
    assert final["match_status"].tolist() == ["matched"]*3
    assert final.loc[0,"excess"] == pytest.approx(.01)
    assert final.loc[1:,"excess"].isna().all()
    assert info["paired_events"] == 1 and info["coverage"] == .5
    assert info["unique_control_times"] == 9 and info["closed_control_rows"] == 8
    assert assignments["event_net_return"].eq(999).all()


def test_no_assignments_is_an_explicit_zero_coverage_result():
    _, info = r.attach_outcomes(pd.DataFrame(),pd.DataFrame(),pd.DataFrame())
    assert info["coverage"] == 0 and info["paired_events"] == 0


def test_arm_filter_uses_unsigned_hourly_features_before_event_projection():
    times = pd.date_range("2023-01-01",periods=2,freq="h",tz="UTC")
    hourly = pd.DataFrame({"open_time":times,"ma_slope_atr":[-.1,.1],"context_valid":[True,True],
                           "context_available":times-pd.Timedelta(hours=1),
                           "context_side":[-1,1],"context_slope_atr":[-.1,.1],
                           "close":[99,101],"ma":[100,100],"atr":[1,1]})
    common = pd.DataFrame({"event_id":["s","l"],"signal_time":times,"direction":[-1,1]})
    arm = {"id":"hourly_slope","require_hourly_slope":True,"require_context_trend":False,"max_extension_atr":99}
    assert r.filtered_events(common,hourly,arm)["event_id"].tolist() == ["s","l"]
