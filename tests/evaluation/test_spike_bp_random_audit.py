"""Contract tests for the bp re-expression of SPIKE matched-random comparisons."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_bp_random_audit as audit


def _pairs(n_months: int = 6, per: int = 20, target_risk=.01, control_risk=.005, edge_bp=0.0) -> pd.DataFrame:
    rows = []
    rng = np.random.default_rng(3)
    for m in range(n_months):
        for k in range(per):
            gross = rng.normal(0, .004)
            t_ret = gross + edge_bp / 1e4 - .002
            c_ret = gross - .002
            rows.append({"study": "s", "arm": "a", "timeframe_min": 60, "side": 1, "symbol": "X",
                         "entry_time": pd.Timestamp(f"2025-{m + 4:02d}-15T00:00Z"),
                         "target_net_r": t_ret / target_risk, "target_net_return": t_ret, "target_risk": target_risk,
                         "control_net_r": c_ret / control_risk, "control_net_return": c_ret,
                         "control_risk": audit._risk(pd.Series([c_ret]), pd.Series([c_ret / control_risk]))[0]})
    return pd.DataFrame(rows)


def cfg():
    return json.loads(audit.CONFIG.read_text())


def test_control_risk_is_recovered_from_net_return_and_net_r():
    r = audit._risk(pd.Series([-.003, .0, .01]), pd.Series([-1.5, 0.0, 2.0]))
    assert np.isclose(r[0], .002) and np.isnan(r[1]) and np.isclose(r[2], .005)


def test_equal_gross_paths_give_zero_bp_excess_but_positive_r_excess_when_control_stops_are_narrower():
    table = audit.summarize(_pairs(), cfg())
    full = table[(table.period == "full") & (table.side == "all")].iloc[0]
    assert abs(full.excess_bp) < 1e-9
    # Same bp paths, control risk half: cost gap 0.2 - 0.4 = +0.2R inflates R excess.
    assert np.isclose(full.cost_gap_contribution_r, .2)
    assert full.excess_r > 0


def test_r_excess_decomposes_into_gross_excess_minus_cost_gap():
    p = _pairs(edge_bp=5.0, control_risk=.01)
    table = audit.summarize(p, cfg())
    full = table[(table.period == "full") & (table.side == "all")].iloc[0]
    assert np.isclose(full.excess_bp, 5.0)
    assert np.isclose(full.cost_gap_contribution_r, 0.0)
    assert np.isclose(full.excess_r, 5.0 / 1e4 / .01)


def test_verdict_rules_follow_plan():
    t = pd.DataFrame([
        {"study": "s", "arm": "a", "timeframe_min": 60, "side": "all", "period": "full", "pairs": 10,
         "excess_r": .3, "excess_r_p": .001, "excess_bp": 1.0, "excess_bp_ci_low": -1.0, "excess_bp_p": .2},
        {"study": "s", "arm": "a", "timeframe_min": 60, "side": "all", "period": "later", "pairs": 5,
         "excess_r": .2, "excess_r_p": .01, "excess_bp": 2.0, "excess_bp_ci_low": -1.0, "excess_bp_p": .2},
        {"study": "s", "arm": "b", "timeframe_min": 60, "side": "all", "period": "full", "pairs": 10,
         "excess_r": .3, "excess_r_p": .001, "excess_bp": 9.0, "excess_bp_ci_low": 2.0, "excess_bp_p": .001},
        {"study": "s", "arm": "b", "timeframe_min": 60, "side": "all", "period": "later", "pairs": 5,
         "excess_r": .2, "excess_r_p": .01, "excess_bp": 3.0, "excess_bp_ci_low": -1.0, "excess_bp_p": .1},
    ])
    v = audit.verdicts(t).set_index("arm").verdict
    assert v["a"] == "r_only" and v["b"] == "bp_confirmed"
