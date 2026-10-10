"""Frozen candidate set and the selection / pass rule of the 5m exit test."""
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v129_eth5m_exit as study


def test_candidates_are_the_parent_tightening_set():
    assert list(study.POLICIES) == ["incumbent", "trail3", "trail2", "arm1", "be1", "lock1", "half2"]
    cfg = study.config()
    assert cfg["side_cost"] == .001 and cfg["production_eligible"] is False


def _frame(deltas):
    """Ordinary signal trades; incumbent net_r 0, each policy shifted by its delta per sample."""
    rows = []
    for sample, per in deltas.items():
        for k in range(40):
            for policy in study.POLICIES:
                shift = 0.0 if policy == "incumbent" else per.get(policy, -0.1)
                rows.append({"sample": sample, "arm": "ordinary", "cohort": "signal", "trade_key": f"{sample}{k}",
                             "policy": policy, "net_r": shift + (0.01 if k % 2 else -0.01), "parity": "ok",
                             "exit_time": pd.Timestamp("2025-01-01", tz="UTC") + pd.Timedelta(hours=k)})
    return pd.DataFrame(rows)


def test_selection_and_pass_rule():
    good = _frame({"okx_select": {"trail2": .3, "be1": .1}, "okx_check": {"trail2": .2}, "binance_check": {"trail2": .1}})
    verdict = study.decide(study.summarize(good, 1), good, 1)
    assert verdict["selected"] == "trail2" and verdict["passed"]
    flip = _frame({"okx_select": {"trail2": .3}, "okx_check": {"trail2": .2}, "binance_check": {"trail2": -.1}})
    assert study.decide(study.summarize(flip, 1), flip, 1)["passed"] is False
    none = _frame({"okx_select": {}, "okx_check": {}, "binance_check": {}})
    assert study.decide(study.summarize(none, 1), none, 1)["selected"] is None
