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


def mocked_run(monkeypatch, tmp_path, fail_at):
    """Exercise real run/replay_arm sequencing with no real file or price reads.

    Only temporary config/base/receipt files exist. Every prior evidence read,
    source constructor and simulation is replaced; the original parity and
    orchestration functions still run. The deliberate failure marks a stage,
    never supplies a saved research outcome.
    """
    calls = []
    experiment = tmp_path/"experiment"
    experiment.mkdir()
    config = deepcopy(r.frozen_config())
    (experiment/"config.json").write_text(json.dumps(config))
    base_path = tmp_path/r.BASE_CONFIG
    base_path.parent.mkdir(parents=True)
    base_config = base()
    base_config["baseline"] = {"synthetic": True}
    base_path.write_text(json.dumps(base_config))
    monkeypatch.setattr(r, "ROOT", tmp_path)
    monkeypatch.setattr(r, "EXPERIMENT", experiment)
    stamp = pd.Timestamp("2024-02-01", tz="UTC")
    mothers = {label: pd.DataFrame({"event_id": [label], "decision_time": stamp,
        "signal_time": stamp-pd.Timedelta(hours=1), "fold": "2024H1"}) for label in ("case", "control")}
    contexts = {label: frame.assign(context_marker=1) for label, frame in mothers.items()}
    prior_prefix = "direct_k1_stop__transition_colour_"
    expected_hashes = {base_path: r.BASE_SHA256}
    for folder, hashes in ((tmp_path/r.MOTHERS, r.MOTHER_INPUTS), (tmp_path/r.PARENT, r.INPUTS)):
        expected_hashes.update({folder/name: expected for name, expected in hashes.items()})

    def digest(path):
        calls.append("hash:"+path.name)
        assert path in expected_hashes, "Unexpected read outside synthetic pinned inputs"
        return "wrong" if fail_at == "hash:"+path.name else expected_hashes[path]

    def committed(paths):
        calls.append("sources")
        if fail_at == "sources":
            raise RuntimeError("synthetic source guard")
        assert all(path.is_relative_to(tmp_path) for path in paths)
        return [{"path": "synthetic_builder", "sha256": "synthetic"}]

    def read_frame(path):
        calls.append("read:"+path.name)
        assert path.is_relative_to(tmp_path), "Real evidence access forbidden"
        if path.name == "original_mothers.csv.gz":
            return mothers["case"].copy()
        if path.name == "control_mothers.csv.gz":
            return mothers["control"].copy()
        if path.name.endswith("_context.csv.gz"):
            label = "control" if "control_context" in path.name else "case"
            frame = contexts[label].copy()
            frame.attrs["stage"] = label+"_context"
            return frame
        assert path.name.startswith(prior_prefix)
        stage = path.name[len(prior_prefix):].split(".")[0]
        label = "control" if stage.startswith("control") else "case"
        frame = contexts[label].assign(observed=True)
        frame.attrs["stage"] = stage
        return frame

    def read_assignments(path, *args, **kwargs):
        assert path == tmp_path/r.MOTHERS/"assignments.csv", "Unexpected CSV/price read"
        calls.append("read:assignments.csv")
        return pd.DataFrame({"event_id": ["case"], "match_status": ["matched"]})

    def population(*args):
        calls.append("population")
        if fail_at == "population":
            raise ValueError("synthetic population guard")

    class FakeStudy:
        def __init__(self, supplied, phase):
            calls.append("study")
            assert supplied == base_config and phase == "development"
            if fail_at == "study":
                raise RuntimeError("synthetic data boundary")
            self.raw = object()  # Deliberately not a dataframe or price array.

        def entries(self, specification):
            assert specification == {"synthetic": True}
            calls.append("entries")
            return mothers["case"].copy()

        def featured(self, minutes, kind, length):
            assert (minutes, kind, length) == (5, "SMA", 40)
            calls.append("features")
            return object()

    native_parity = r.assert_saved_parity

    def parity(before, after):
        stage = before.attrs.get("stage", "entries")
        calls.append("parity:"+stage)
        if stage == fail_at:
            raise AssertionError("synthetic parity failure: "+stage)
        native_parity(before, after)

    def context(raw, featured, requests):
        label = requests.event_id.iloc[0]
        calls.append("context:"+label)
        return contexts[label].copy()

    def simulate(study, requests, policy):
        label = requests.event_id.iloc[0]
        calls.append("simulate:"+policy["id"]+":"+label)
        assert policy == r.POLICIES[0], "Candidate reached before complete baseline parity"
        return contexts[label].assign(observed=True)

    native_replay = r.replay_arm

    def replay(study, policy, *args, **kwargs):
        calls.append("arm:"+policy["id"])
        return native_replay(study, policy, *args, **kwargs)

    monkeypatch.setattr(r, "digest", digest)
    monkeypatch.setattr(r, "committed_sources", committed)
    monkeypatch.setattr(r, "read_frame", read_frame)
    monkeypatch.setattr(r.pd, "read_csv", read_assignments)
    monkeypatch.setattr(r, "validate_population", population)
    monkeypatch.setattr(r, "Study", FakeStudy)
    monkeypatch.setattr(r, "assert_saved_parity", parity)
    monkeypatch.setattr(r, "attach_entry_colour_context", context)
    monkeypatch.setattr(r, "simulate_requests", simulate)
    monkeypatch.setattr(r, "episode_ledger", lambda mother, status, trade: trade.copy())
    monkeypatch.setattr(r, "matched_episodes", lambda cases, controls: (cases.copy(), {}))
    monkeypatch.setattr(r, "single_pending_ledger", lambda episodes: episodes.assign(portfolio_selected=True))
    monkeypatch.setattr(r, "replay_arm", replay)
    monkeypatch.setattr(r.subprocess, "check_output", lambda *args, **kwargs: "0"*40)
    return calls, experiment/"results", experiment/"config.json"


@pytest.mark.parametrize("failed_table", ["case_trades", "case_episodes", "control_trades",
    "control_episodes", "matched", "single_pending"])
def test_run_any_original_six_table_parity_failure_prevents_all_candidate_calls(monkeypatch, tmp_path, failed_table):
    calls, results, _ = mocked_run(monkeypatch, tmp_path, failed_table)
    with pytest.raises(AssertionError, match="synthetic parity failure"):
        r.run()
    assert calls.count("arm:5m_native40") == 1
    assert "arm:5m_native40_launch60" not in calls
    assert not any("simulate:5m_native40_launch60" in call for call in calls)
    assert "parity:"+failed_table in calls
    assert not (results/"candidate").exists() and not (results/"anchor_parity.json").exists()
    failure = json.loads((results/"failure.json").read_text())
    assert failure["type"] == "AssertionError" and failed_table in failure["message"]
    assert (results/"started.json").exists() and not (results/"summary.json").exists()


@pytest.mark.parametrize("label", ["case", "control"])
def test_run_bad_regenerated_context_prevents_any_arm_or_execution(monkeypatch, tmp_path, label):
    calls, results, _ = mocked_run(monkeypatch, tmp_path, label+"_context")
    with pytest.raises(AssertionError, match="synthetic parity failure"):
        r.run()
    assert calls.count("study") == 1 and "context:"+label in calls
    assert not any(call.startswith(("arm:", "simulate:")) for call in calls)
    assert (results/"failure.json").exists() and not (results/"baseline").exists()


@pytest.mark.parametrize("guard", ["sources", "population", "hash:config.json"] +
    ["hash:"+name for name in list(r.MOTHER_INPUTS)+list(r.INPUTS)])
def test_run_source_constructor_cannot_precede_any_input_guard(monkeypatch, tmp_path, guard):
    calls, results, _ = mocked_run(monkeypatch, tmp_path, guard)
    with pytest.raises((ValueError, RuntimeError)):
        r.run()
    assert "study" not in calls
    assert not any(call.startswith(("arm:", "simulate:")) for call in calls)
    assert not results.exists()


def test_run_invalid_frozen_config_stops_before_sources_inputs_or_study(monkeypatch, tmp_path):
    calls, results, path = mocked_run(monkeypatch, tmp_path, "unused")
    config = json.loads(path.read_text())
    config["policies"][1]["launch_progress_r"] = 1.0
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="Frozen V11"):
        r.run()
    assert calls == ["hash:config.json"]
    assert not results.exists()


def test_run_all_inputs_validated_before_first_synthetic_data_constructor(monkeypatch, tmp_path):
    calls, results, _ = mocked_run(monkeypatch, tmp_path, "study")
    with pytest.raises(RuntimeError, match="synthetic data boundary"):
        r.run()
    assert calls[-1] == "study"
    for guard in ["sources", "population"] + ["hash:"+name for name in list(r.MOTHER_INPUTS)+list(r.INPUTS)]:
        assert calls.index(guard) < calls.index("study")
    failure = json.loads((results/"failure.json").read_text())
    assert failure["type"] == "RuntimeError" and failure["message"] == "synthetic data boundary"


@pytest.mark.parametrize("failed_stage", ["study", "case_context", "control_trades", "single_pending"])
def test_run_preserves_first_failure_and_partial_outputs_on_second_attempt(monkeypatch, tmp_path, failed_stage):
    calls, results, _ = mocked_run(monkeypatch, tmp_path, failed_stage)
    with pytest.raises((AssertionError, RuntimeError)):
        r.run()
    snapshot = {path.relative_to(results): path.read_bytes() for path in results.rglob("*") if path.is_file()}
    assert "failure.json" in {str(path) for path in snapshot}
    study_calls = calls.count("study")
    with pytest.raises(ValueError, match="no overwrite"):
        r.run()
    assert calls.count("study") == study_calls
    assert snapshot == {path.relative_to(results): path.read_bytes() for path in results.rglob("*") if path.is_file()}


def test_run_existing_nonempty_output_directory_is_not_overwritten_even_without_summary(monkeypatch, tmp_path):
    calls, results, _ = mocked_run(monkeypatch, tmp_path, "unused")
    results.mkdir()
    artifact = results/"original.csv"
    artifact.write_bytes(b"synthetic previous attempt\n")
    with pytest.raises(ValueError, match="no overwrite"):
        r.run()
    assert "study" not in calls and artifact.read_bytes() == b"synthetic previous attempt\n"
    assert sorted(path.name for path in results.iterdir()) == ["original.csv"]
