"""V11 frozen orchestration and full-population diagnostic tests; no prices."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_launch_research as r


def base():
    return {"execution": {"max_hours": 72, "cost_fraction": .002, "stop_first": True},
            "development_folds": deepcopy(r.FOLDS)}


def test_saved_config_and_only_one_rule():
    config = json.loads((r.EXPERIMENT/"config.json").read_text())
    r.verify_config(config, base())
    a,b = deepcopy(config["policies"])
    a.pop("id"); b.pop("id")
    assert b.pop("launch_deadline_minutes") == 60
    assert b.pop("launch_progress_r") == .5
    assert a == b
    assert len(config["inputs"]) == 9
    assert len(config["mother_inputs"]) == 4
    assert len(r.SOURCES) == len(set(r.SOURCES))
    assert config["known_support"]["matched"] == 154
    assert config["selection"]["matched_coverage"] == .9


@pytest.mark.parametrize("key,value", [("launch_deadline_minutes", 30), ("launch_progress_r", 1),
    ("management_minutes", 15), ("decision_minutes", 15), ("ma_length", 20), ("confirmations", True),
    ("exit_mode", "colour"), ("new_filter", True)])
def test_policy_drift_fails(key,value):
    config = deepcopy(r.frozen_config())
    config["policies"][1][key] = value
    with pytest.raises(ValueError): r.verify_config(config,base())


@pytest.mark.parametrize("change", ["fee", "max", "stop", "fold", "holdout", "numericfalse", "training", "input", "extra", "gate"])
def test_scope_or_cost_drift_fails(change):
    config,b = deepcopy(r.frozen_config()),base()
    if change == "fee": b["execution"]["cost_fraction"] = .001
    elif change == "max": b["execution"]["max_hours"] = 24
    elif change == "stop": b["execution"]["stop_first"] = False
    elif change == "fold": b["development_folds"][-1][-1] = "2026-01-01"
    elif change == "holdout": config["holdout_consumed"] = True
    elif change == "numericfalse": config["holdout_consumed"] = 0
    elif change == "training": config["training_eligible"] = True
    elif change == "input": config["inputs"].pop("summary.json")
    elif change == "extra": config["allow_audit"] = True
    else: config["selection"]["matched_coverage"] = .6
    with pytest.raises(ValueError): r.verify_config(config,b)


def population():
    case = pd.DataFrame({"event_id": [f"c{i}" for i in range(251)],
        "decision_time": pd.date_range("2024-01-01",periods=251,freq="h",tz="UTC"), "fold": "2024H1"})
    control = pd.DataFrame({"event_id": [f"r{i}" for i in range(462)],
        "decision_time": pd.date_range("2024-02-01",periods=462,freq="h",tz="UTC"),
        "fold": "2024H1", "parent_event_id": [f"c{i//3}" for i in range(462)]})
    mothers = {"case":case, "control":control}
    contexts = {k:v.copy() for k,v in mothers.items()}
    assignments = case[["event_id"]].assign(match_status=["matched"]*154+["insufficient_exact_controls"]*97)
    return mothers,contexts,assignments


def test_original_counts_and_assignment_all_preserved():
    r.validate_population(*population())


@pytest.mark.parametrize("mutation", ["missingcase", "duplicated", "future", "embargo", "unknownfold", "nothour", "reuse", "partial", "rematch", "missingassignment"])
def test_invalid_population_fails_before_prices(mutation):
    m,c,a = population()
    if mutation == "missingcase": m["case"] = m["case"].iloc[1:]
    elif mutation == "duplicated": m["case"].loc[0,"event_id"] = "c1"
    elif mutation == "future": m["case"].loc[0,"decision_time"] = pd.Timestamp("2025-01-01",tz="UTC")
    elif mutation == "embargo": m["case"].loc[0,"decision_time"] = pd.Timestamp("2024-06-29",tz="UTC")
    elif mutation == "unknownfold": m["case"].loc[0,"fold"] = "future"
    elif mutation == "nothour": m["case"].loc[0,"decision_time"] += pd.Timedelta(minutes=5)
    elif mutation == "reuse": m["control"].loc[0,"decision_time"] = m["control"].loc[1,"decision_time"]
    elif mutation == "partial": m["control"].loc[0,"parent_event_id"] = "c154"
    elif mutation == "rematch": a.loc[0,"match_status"] = "insufficient_exact_controls"
    else: a = a.iloc[1:]
    c = {k:v.copy() for k,v in m.items()}
    with pytest.raises((ValueError,AssertionError)): r.validate_population(m,c,a)


def trades():
    e = pd.Timestamp("2024-01-01",tz="UTC")
    old = pd.DataFrame({"event_id":["loss","win","same","unknown"], "entry_time":e,
        "entry_price":100., "direction":1, "initial_stop":98., "signal_atr":1.,
        "risk_pct":.02, "risk_atr":2., "closed":[True,True,True,False],
        "net_return":[-.01,.02,-.003,np.nan], "gross_return":[-.008,.022,-.001,np.nan],
        "hold_minutes":[120,90,10,5], "outcome":["transition_colour_exit"]*3+["right_censored"],
        "exit_time":[e+pd.Timedelta(minutes=i) for i in (120,90,10,5)]})
    new = old.copy()
    new.loc[:1,"hold_minutes"] = 60
    new.loc[:1,"exit_time"] = e+pd.Timedelta(minutes=60)
    new.loc[:1,"outcome"] = "launch_timeout_exit"
    new.loc[:1,"net_return"] = [.001,-.002]
    new.loc[:1,"gross_return"] = [.003,0.]
    return old,new


def test_all_pair_mechanics_keep_zero_unknown_and_winner_sacrifice():
    old,new = trades()
    joined,groups,info = r.paired_mechanics(old,new)
    assert len(joined) == 4
    assert joined.difference.iloc[2] == 0
    assert np.isnan(joined.difference.iloc[3])
    assert info["transitions"] == {"loss_to_win":1,"win_to_loss":1,"loss_to_loss":1,"unknown":1}
    assert info["known"] == 3 and info["timeout_exits"] == 2
    assert groups.n.sum() == 4
    assert info["distributions"]["difference"]["unknown"] == 1


@pytest.mark.parametrize("mutation", ["entry", "stop", "time", "late", "old60", "unexpected_return", "unexpected_exit"])
def test_timeout_cannot_change_entry_or_delay_other_exits(mutation):
    old,new = trades()
    if mutation == "entry": new.loc[0,"entry_price"] += 1
    elif mutation == "stop": new.loc[0,"initial_stop"] -= 1
    elif mutation == "time": new.loc[0,"hold_minutes"] = 55
    elif mutation == "late": new.loc[0,"hold_minutes"] = 125
    elif mutation == "old60": old.loc[0,"hold_minutes"] = 60
    elif mutation == "unexpected_return": new.loc[2,"net_return"] = 1
    else: new.loc[2,"exit_time"] += pd.Timedelta(minutes=5)
    with pytest.raises((ValueError,AssertionError)): r.paired_mechanics(old,new)


def test_flat_not_mislabeled_loss():
    old,new = trades()
    new.loc[0,"net_return"] = 0
    joined,_,_ = r.paired_mechanics(old,new)
    assert joined.loc[0,"win_loss_transition"] == "includes_flat"
