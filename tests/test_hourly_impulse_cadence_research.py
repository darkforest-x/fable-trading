"""V9 orchestration contracts: synthetic inputs only, no archive or backtest."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_cadence_research as research
from yoyo.evaluation import hourly_impulse_management_research as v8


def configuration():
    return json.loads((Path(__file__).parents[1]/"experiments/active"/research.EXPERIMENT_ID/"config.json").read_text())


def base_config():
    return {"execution": {"max_hours": 72, "cost_fraction": .002, "stop_first": True},
            "development_folds": deepcopy(research.DEVELOPMENT_FOLDS)}


def requests(count=2, prefix="case"):
    t = pd.Timestamp("2024-01-01T01:00:00Z")
    return pd.DataFrame({"event_id": [f"{prefix}_{i}" for i in range(count)],
        "decision_time": t, "mother_decision_time": t, "fold": "2024H1",
        "direction": [1 if i % 2 == 0 else -1 for i in range(count)],
        "initial_stop": 90., "signal_atr": 2., "ltf_entry_state": "old_frozen_diagnostic"})


def zones(count=959):
    return pd.DataFrame({"zone_id": [f"zone_{i}" for i in range(count)],
                         "zone_arm_time": pd.Timestamp("2024-01-01", tz="UTC")})


def study():
    times = pd.date_range("2024-01-01T00:50:00Z", periods=3, freq="5min")
    raw = pd.DataFrame({"open_time": times, "open": 100., "high": 102., "low": 99., "close": 101., "segment_id": 7})
    mg = raw.assign(ma=100., ma_side=1, segment_id="independent_counter")
    mg.attrs["bar_minutes"] = 5
    calls = []
    def featured(minutes, kind, length):
        calls.append((minutes, kind, length))
        return mg
    return SimpleNamespace(raw=raw, featured=featured, calls=calls)


def test_frozen_config_has_one_only_treatment_and_same_twelve_v7_inputs():
    config = configuration()
    research.verify_config(config, base_config())
    old, new = deepcopy(config["policies"])
    assert "decision_minutes" not in old
    assert new.pop("decision_minutes") == 15
    old.pop("id")
    new.pop("id")
    assert old == new
    previous = json.loads((Path(__file__).parents[1]/"experiments/active/exp-btcusdtp-1h-management-spec-preholdout-20260906-v8/config.json").read_text())
    for name in ("base_config", "base_config_sha256", "parent_results", "inputs", "selection", "inference"):
        assert config[name] == previous[name]
    assert len(config["inputs"]) == 12
    assert research.evaluate_arm is v8.evaluate_arm
    assert research.paired_effects is v8.paired_effects
    assert research.assert_saved_parity is v8.assert_saved_parity
    assert len(research.SOURCES) == len(set(research.SOURCES))
    assert set(v8.SOURCES).issubset(research.SOURCES)
    assert "tests/test_hourly_impulse_transition_cadence.py" in research.SOURCES
    assert "yoyo/evaluation/hourly_impulse_management_diagnostics.py" in research.SOURCES


@pytest.mark.parametrize("change", ["ma", "management", "decision", "baseline_decision", "confirm", "boolean_confirm",
    "exit", "cost", "hours", "stop_first", "fold", "parent", "base_hash", "input_hash", "missing_input", "gate", "inference",
    "holdout", "audit", "production", "training", "extra", "numeric_false"])
def test_any_frozen_configuration_drift_fails(change):
    config, base = configuration(), base_config()
    policy = config["policies"][1]
    if change in ("ma", "management", "decision", "confirm", "boolean_confirm", "exit"):
        key, value = {"ma": ("ma_length", 20), "management": ("management_minutes", 15),
            "decision": ("decision_minutes", 30), "confirm": ("confirmations", 2),
            "boolean_confirm": ("confirmations", True), "exit": ("exit_mode", "colour")}[change]
        policy[key] = value
    elif change == "baseline_decision": config["policies"][0]["decision_minutes"] = 5
    elif change == "cost": base["execution"]["cost_fraction"] = .001
    elif change == "hours": base["execution"]["max_hours"] = 96
    elif change == "stop_first": base["execution"]["stop_first"] = False
    elif change == "fold": base["development_folds"][-1][-1] = "2026-01-01"
    elif change == "parent": config["parent_results"] = "another_results"
    elif change == "base_hash": config["base_config_sha256"] = "0"*64
    elif change == "input_hash": config["inputs"]["case_requests.csv.gz"] = "0"*64
    elif change == "missing_input": config["inputs"].pop("support.json")
    elif change == "gate": config["selection"]["minimum_profit_factor"] = 1.
    elif change == "inference": config["inference"]["joint_required"] = ["case_delta"]
    elif change == "holdout": config["holdout_consumed"] = True
    elif change == "audit": config["no_audit_entry_point"] = False
    elif change == "production": config["production_eligible"] = True
    elif change == "training": config["training_eligible"] = True
    elif change == "numeric_false": config["training_eligible"] = 0
    else: config["new_filter"] = True
    with pytest.raises(ValueError):
        research.verify_config(config, base)


def test_context_twice_is_exact_and_old_frozen_fields_preserved():
    subject = study()
    original = {"case": requests(), "control": requests(3, "control")}
    saved = deepcopy(original)
    prepared = research.prepare_contexts(subject, original)
    assert subject.calls == [(5, "SMA", 40)]*4
    for label in original:
        old, new = (prepared[p["id"]][label] for p in research.POLICIES)
        pd.testing.assert_frame_equal(old, new, check_exact=True)
        pd.testing.assert_frame_equal(original[label], saved[label], check_exact=True)
        pd.testing.assert_frame_equal(old[original[label].columns], original[label], check_exact=True)
        assert old.mg_entry_state.tolist() == ["aligned", "opposite"] + (["aligned"] if label == "control" else [])


@pytest.mark.parametrize("mutation", ["state", "tiny_float", "order", "column"])
def test_context_full_exact_parity_fails_before_any_evaluator(mutation, monkeypatch):
    helper = research.attach_management_context
    calls = []
    def corrupted(*args):
        result = helper(*args)
        calls.append(True)
        if len(calls) == 2:
            if mutation == "state": result.loc[0, "mg_entry_state"] = "opposite"
            elif mutation == "tiny_float": result.loc[0, "signal_atr"] += 1e-13
            elif mutation == "order": result = result.iloc[::-1]
            else: result["hidden_difference"] = True
        return result
    monkeypatch.setattr(research, "attach_management_context", corrupted)
    with pytest.raises((AssertionError, ValueError)):
        research.prepare_contexts(study(), {"case": requests()})


@pytest.mark.parametrize("change", ["count", "duplicate", "null_id", "future", "past", "null_time"])
def test_full_fixed_input_support_and_development_scope_fail_closed(change):
    populations = {"case": requests(286), "control": requests(849, "control")}
    source_zones = zones()
    research.validate_fixed_inputs(populations, source_zones)
    if change == "count": populations["case"] = populations["case"].iloc[:-1]
    elif change == "duplicate": source_zones.loc[0, "zone_id"] = "zone_1"
    elif change == "null_id": populations["control"].loc[0, "event_id"] = None
    elif change == "null_time": source_zones.loc[0, "zone_arm_time"] = pd.NaT
    else: populations["case"].loc[0, "decision_time"] = pd.Timestamp("2025-01-01" if change == "future" else "2022-12-31", tz="UTC")
    with pytest.raises(ValueError):
        research.validate_fixed_inputs(populations, source_zones)


def mechanics_trades(values):
    data = requests(len(values))
    return data.assign(entry_time=data.decision_time, entry_price=100., risk_pct=.1, risk_atr=5.,
                       net_return=values, gross_return=np.asarray(values)+.002, outcome="transition_colour_exit",
                       hold_minutes=5., max_favourable_r=1., mg_entry_state="aligned")


def test_mechanisms_keep_unknown_outcomes_out_of_loss_counts_and_name_check_clock():
    before, after = mechanics_trades([.01, -.01, np.nan]), mechanics_trades([-.01, .02, .03])
    tables, info = research.cadence_mechanisms(before, after)
    paired = tables["paired_case_mechanics"]
    assert info["total_pairs"] == 3 and info["known_pairs"] == 2 and info["unknown_pairs"] == 1
    assert info["native_management_minutes_both"] == 5
    assert tables["win_loss_transitions"].n.sum() == 2
    assert tables["exit_transitions"].n.sum() == 2
    assert paired.loc[2, ["old_win", "new_win"]].isna().all()
    assert "net_return_check5m" in paired and "net_return_check15m" in paired
    assert "outcome_check15m" in tables["exit_transitions"]
    assert not any(c.endswith(("_5m", "_15m")) for c in paired)
    distributions = info["distribution_checks"]
    assert distributions["net_check5m"]["total"] == 3
    assert distributions["net_check5m"]["missing_count"] == 1
    assert distributions["case_delta"]["quantiles_bp"]["0.0"] == pytest.approx(-200.)
    assert distributions["case_delta"]["quantiles_bp"]["1.0"] == pytest.approx(300.)
    assert all(d["outliers_removed"] == 0 and d["shapiro_used_for_selection"] is False for d in distributions.values())


def test_distribution_retains_extreme_and_all_quantiles_and_degenerate_unavailability():
    data = pd.DataFrame({"net_return_5m": [0., 0., 0., np.nan],
                         "net_return_15m": [-.5, .001, 3., np.nan]})
    data["difference"] = data.net_return_15m-data.net_return_5m
    result = research.distribution_checks(data)
    assert set(result["case_delta"]["quantiles_bp"]) == {"0.0", "0.05", "0.25", "0.5", "0.75", "0.95", "1.0"}
    assert result["case_delta"]["quantiles_bp"]["1.0"] == 30000.
    assert result["case_delta"]["sd_bp"] == pytest.approx(data.difference.std()*1e4)
    assert "shapiro_unavailable" in result["net_check5m"]
    assert "shapiro_p" in result["case_delta"] or "shapiro_unavailable" in result["case_delta"]


def test_serial_all_source_denominator_unknown_selected_never_zero_or_dropped():
    before = requests(4).assign(entry_event_id=["a", "b", "c", None],
        portfolio_selected=[True, False, False, False], episode_net_return=[.01, .02, -.03, np.nan])
    after = before.copy()
    after["portfolio_selected"] = [True, True, False, False]
    after["episode_net_return"] = [np.nan, .02, -.03, np.nan]
    table = research.serial_intentions(before, after)
    assert table.zones.tolist() == [4, 4]
    assert table.skipped_emitted_requests.tolist() == [2, 1]
    assert table.skipped_winners.tolist() == [1, 0]
    assert table.skipped_losers.tolist() == [1, 1]
    assert table.loc[0, "mean_net_bp_per_original_zone"] == 25.
    assert pd.isna(table.loc[1, "mean_net_bp_per_original_zone"])
    assert table.loc[1, "unknown_selected_zones"] == 1


def mock_run(monkeypatch, tmp_path, *, fail=None):
    config, base = configuration(), base_config()
    experiment = tmp_path/"experiment"
    experiment.mkdir()
    (experiment/"config.json").write_text(json.dumps(config))
    base_path = tmp_path/config["base_config"]
    base_path.parent.mkdir(parents=True)
    base_path.write_text(json.dumps(base))
    (experiment/"PROJECT_PLAN.md").write_text("synthetic plan")
    populations = {"case": requests(286), "control": requests(849, "control")}
    source_zones = zones()
    calls, written = [], {}
    monkeypatch.setattr(research, "ROOT", tmp_path)
    monkeypatch.setattr(research, "EXPERIMENT", experiment)
    def digest(path):
        path = Path(path)
        calls.append(("hash", path.name))
        if path == base_path: return research.BASE_CONFIG_SHA256 if fail != "base_hash" else "wrong"
        if path.name in research.FROZEN_INPUTS:
            return research.FROZEN_INPUTS[path.name] if fail != "input_hash" else "wrong"
        return "synthetic_saved_output_hash"
    monkeypatch.setattr(research, "digest", digest)
    def committed(paths):
        calls.append(("committed", [str(p) for p in paths]))
        if fail == "uncommitted": raise RuntimeError("uncommitted")
        return [{"path": "synthetic_builder", "sha256": "synthetic"}]
    monkeypatch.setattr(research, "committed_sources", committed)
    def read(path):
        calls.append(("read", path.name))
        if path.name == "source_zones.csv.gz": return source_zones
        if path.name == "assignments.csv.gz": return pd.DataFrame()
        return populations[path.name.split("_")[0]]
    monkeypatch.setattr(research, "read_frame", read)
    def support(*args):
        calls.append(("support",))
        return {"passed": fail != "support"}
    monkeypatch.setattr(research, "support_info", support)
    def make_study(config, phase):
        calls.append(("Study", phase))
        assert phase == "development"
        assert (experiment/"results/started.json").exists()
        assert sum(c[0] == "hash" and c[1] in research.FROZEN_INPUTS for c in calls) == 12
        subject = study()
        subject.source_receipt = {"synthetic": True}
        return subject
    monkeypatch.setattr(research, "Study", make_study)
    monkeypatch.setattr(research.subprocess, "check_output", lambda *a, **k: "synthetic_commit")
    writer = research.write_json
    def write(path, value):
        calls.append(("write_json", path.name))
        written[path.name] = value
        return writer(path, value)
    monkeypatch.setattr(research, "write_json", write)
    def evaluate(subject, policy, entries, controls, source, result, cfg, parent=None):
        calls.append(("evaluate", policy["id"], parent))
        assert written["contexts_frozen.json"]["exact_context_parity"] is True
        assert len(list((experiment/"results").glob("*_context.csv.gz"))) == 4
        assert cfg == config
        output = {}
        for label, incoming in (("case", entries), ("control", controls)):
            output[label] = incoming.assign(entry_time=incoming.decision_time, entry_price=100., risk_pct=.1, risk_atr=5.)
        if fail == "changed_entry" and policy["id"] == research.POLICIES[1]["id"]:
            output["case"].loc[0, "entry_price"] += 1
        result.mkdir()
        return {"policy": policy, "gates": {"frozen_economic": True}}, output, output, pd.DataFrame(), pd.DataFrame()
    monkeypatch.setattr(research, "evaluate_arm", evaluate)
    def pairs(*args):
        calls.append(("paired",))
        counts = {"case_delta": 286, "excess_delta": 283, "serial_delta": 959}
        effect = {name: {"n": n, "mean_bp": 1., "ci95_bp": [1., 2.], "month_cluster_p": .001} for name, n in counts.items()}
        if fail == "joint_gate": effect["excess_delta"]["mean_bp"] = -1.
        return {name: pd.DataFrame({"difference": [0.]}) for name in counts}, effect
    monkeypatch.setattr(research, "paired_effects", pairs)
    monkeypatch.setattr(research, "cadence_mechanisms", lambda *args: ({"paired_case_mechanics": pd.DataFrame(),
        "win_loss_transitions": pd.DataFrame(), "exit_transitions": pd.DataFrame()}, {"synthetic": True}))
    monkeypatch.setattr(research, "serial_intentions", lambda *args: pd.DataFrame({"zones": [959, 959]}))
    return experiment, calls, written


def test_mock_runner_commit_hash_support_contexts_precede_outcomes_and_baseline_replays_parent(monkeypatch, tmp_path):
    experiment, calls, written = mock_run(monkeypatch, tmp_path)
    research.run()
    positions = {key: next(i for i, call in enumerate(calls) if call[0] == key) for key in ("committed", "read", "support", "Study", "evaluate", "paired")}
    assert list(positions.values()) == sorted(positions.values())
    evaluated = [c for c in calls if c[0] == "evaluate"]
    assert evaluated[0][1:] == ("5m_native40", tmp_path/research.PARENT_RESULTS)
    assert evaluated[1][1:] == ("5m_native40_check15m", None)
    final = written["summary.json"]
    assert final["status"] == "development_pass_requires_prospective_validation"
    assert final["holdout_price_rows"] == 0
    assert all(final[name] is False for name in ("audit_opened", "independent_confirmation", "training_eligible", "production_eligible"))
    assert final["exact_entry_context_parity"] is True
    assert (experiment/"results/win_loss_transitions.csv").exists()
    with pytest.raises(RuntimeError, match="output already exists"):
        research.run()


@pytest.mark.parametrize("failure", ["uncommitted", "base_hash", "input_hash", "support"])
def test_mock_preflight_rejects_before_any_Study_price_load(failure, monkeypatch, tmp_path):
    _, calls, _ = mock_run(monkeypatch, tmp_path, fail=failure)
    with pytest.raises((RuntimeError, ValueError)):
        research.run()
    assert not any(c[0] in ("Study", "evaluate") for c in calls)


def test_mock_entry_drift_stops_before_paired_inference(monkeypatch, tmp_path):
    _, calls, _ = mock_run(monkeypatch, tmp_path, fail="changed_entry")
    with pytest.raises((AssertionError, ValueError)):
        research.run()
    assert not any(c[0] == "paired" for c in calls)


def test_mock_positive_absolute_change_cannot_bypass_joint_excess_gate(monkeypatch, tmp_path):
    _, _, written = mock_run(monkeypatch, tmp_path, fail="joint_gate")
    research.run()
    assert written["summary.json"]["status"] == "rejected_development_no_audit"
    assert not written["summary.json"]["gates"]["excess_delta_improves"]
