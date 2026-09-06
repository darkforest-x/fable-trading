"""Synthetic V18 saved ledgers only; no raw prices or strategy imports."""
from copy import deepcopy
from datetime import datetime, timedelta
import importlib.util
import json
from collections import Counter, defaultdict
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_confirm_v18", ROOT / "scripts/verify_hourly_impulse_failed_confirm_v18.py")
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)
FSPEC = importlib.util.spec_from_file_location("saved_v17_fixtures", ROOT / "tests/test_verify_hourly_impulse_failed_launch_v17.py")
f = importlib.util.module_from_spec(FSPEC)
FSPEC.loader.exec_module(f)


def first_full(direction=1, price=None):
    return f.failed(f.baseline(f.f.trade("x", 0, direction=direction), price=price))


def confirm_fields(row):
    row = deepcopy(row)
    row.update(failed_confirm_enabled=True, failed_confirm_required=2, failed_confirm_create_count=0,
               failed_confirm_confirm_count=0, failed_confirm_cancel_count=0, failed_confirm_priority_termination_count=0,
               failed_confirm_status="prior_exit" if row["closed"] else "unknown_source", failed_confirm_last_reason="",
               failed_confirm_events="[]", failed_confirm_created_at=None, failed_confirm_due_at=None,
               failed_confirm_previous_open_time=None, failed_confirm_open_time=None, failed_confirm_available_at=None,
               failed_confirm_open_price=None, failed_confirm_gross_return=None, failed_confirm_slow_open_time=None,
               failed_confirm_slow_available_at=None, failed_confirm_slow_side=None, failed_confirm_slow_state="unknown")
    return row


def confirmed(old, price=None):
    row = confirm_fields(old)
    edge = json.loads(row["partial_fast_events"])[-1]
    edge["action"] = "failed_launch_pending"
    row["partial_fast_events"] = json.dumps([edge])
    created = datetime.fromisoformat(edge["available_at"])
    due = created + timedelta(minutes=5)
    slow_at = due.replace(minute=due.minute // 15 * 15)
    price = edge["open_price"] if price is None else price
    gross = float(v.exact_gross(row, price))
    obs = dict(available_at=due.isoformat(), open_price=price, gross_return=gross,
               profit_qualified=v.h.profit_qualified(price, row["entry_price"], row["direction"]),
               previous_fast=deepcopy(edge["current_fast"]),
               current_fast=f.f.bar(created, -row["direction"]), fast_reason="valid", fast_consecutive=True,
               slow=f.f.bar(slow_at-timedelta(minutes=15), row["direction"], native="native-2"),
               slow_available_at=slow_at.isoformat(), slow_state="aligned", slow_reason="valid")
    create = dict(pending_id=1, action="created", reason="failed_profit_edge", created_at=created.isoformat(),
                  due_at=due.isoformat(), observed_at=created.isoformat(), edge=deepcopy(edge), observation=None, terminal=None)
    resolution = dict(create, action="confirmed", reason="consecutive_opposite_failed_profit",
                      observed_at=due.isoformat(), observation=obs)
    row.update(exit_time=due.isoformat(), exit_price=price, hold_minutes=(due-datetime.fromisoformat(row["entry_time"])).total_seconds()/60,
               gross_return=gross, net_return=gross-.002, net_r=(gross-.002)/row["risk_pct"], marked_gross_return=gross,
               marked_net_return=gross-.002, failed_confirm_create_count=1, failed_confirm_confirm_count=1,
               failed_confirm_status="confirmed_closed", failed_confirm_last_reason=resolution["reason"],
               failed_confirm_events=json.dumps([create, resolution]), failed_confirm_created_at=created.isoformat(),
               failed_confirm_due_at=due.isoformat(), failed_confirm_previous_open_time=edge["current_fast"]["open_time"],
               failed_confirm_open_time=created.isoformat(), failed_confirm_available_at=due.isoformat(),
               failed_confirm_open_price=price, failed_confirm_gross_return=gross,
               failed_confirm_slow_open_time=obs["slow"]["open_time"], failed_confirm_slow_available_at=obs["slow_available_at"],
               failed_confirm_slow_side=row["direction"], failed_confirm_slow_state="aligned")
    return row


def assemble(cases, controls, new_cases, new_controls):
    tables, summary = f.f.assemble(cases, controls, new_cases, new_controls)
    summary["experiment_id"] = v.EXPERIMENT_ID
    summary["arms"]["baseline"]["policy"] = deepcopy(v.BASE_POLICY)
    summary["arms"]["candidate"]["policy"] = deepcopy(v.CANDIDATE_POLICY)
    return tables, summary


def fixture(full=False):
    original, _ = f.fixture(full)
    cases, controls = (original["candidate"][key] for key in ("case_trades", "control_trades"))
    return assemble(cases, controls,
                    [confirmed(r) if r["outcome"] == "fast_failed_launch" else confirm_fields(r) for r in cases],
                    [confirmed(r) if r["outcome"] == "fast_failed_launch" else confirm_fields(r) for r in controls])


def run(data, full=False):
    return v.verify_tables(*data, expected_counts=(251, 462, 154) if full else (4, 6, 2))


@pytest.mark.parametrize("direction", [-1, 1])
def test_original_full_decimal_equality_accounting(direction):
    row = first_full(direction, 100. + direction * .2)
    v.check_baseline(row)
    assert row["net_return"] == 0
    row["net_return"] = 1e-18
    with pytest.raises(v.VerificationError, match="exact zero"):
        v.check_accounting(row)


@pytest.mark.parametrize("field,value", [
    ("entry_price", 101), ("risk_pct", .2), ("risk_atr", 3), ("initial_stop", 89),
    ("net_return", .01), ("gross_return", .01), ("net_r", 5), ("hold_minutes", 10),
    ("partial_fraction", .5), ("exit_remaining_fraction", .5), ("realised_partial_gross_return", .01),
    ("partial_fast_realised_net_return", .01), ("marked_gross_return", .02),
    ("closed", False), ("max_favourable_r", -1), ("max_adverse_r", 1), ("funding_modelled", True),
])
def test_independent_accounting_mutations_fail(field, value):
    row = first_full()
    row[field] = value
    with pytest.raises(v.VerificationError):
        v.check_accounting(row)


def test_original_profitable_half_is_weighted_and_whole_censor_unknown():
    row = f.opt_in(f.baseline(f.f.trade("x", 0), eligible=False))
    v.check_baseline(row)
    row.update(closed=False, outcome="right_censored", gross_return=None, net_return=None, net_r=None,
               transition_trigger_previous_open_time=None, transition_trigger_open_time=None, transition_trigger_available_at=None)
    v.check_accounting(row)
    row["net_return"] = row["partial_fast_realised_net_return"]
    with pytest.raises(v.VerificationError, match="Censored whole"):
        v.check_accounting(row)


def test_nonfull_must_preserve_all_existing_fields_not_only_returns():
    old = f.opt_in(f.baseline(f.f.trade("x", 0), eligible=False))
    new = deepcopy(old)
    assert v.check_pair_path(old, new) is False
    new["partial_fast_reset_count"] = 3
    with pytest.raises(v.VerificationError):
        v.check_pair_path(old, new)


@pytest.mark.parametrize("field,value", [("ma", 101), ("signal_close", 99), ("entry_price", 101),
                                          ("partial_fast_initial_ma", 102), ("transition_initial_state", "unknown")])
def test_delayed_path_still_preserves_all_original_entry_and_seed_fields(field, value):
    old = first_full()
    new = deepcopy(old)
    new[field] = value
    with pytest.raises(v.VerificationError):
        v.check_original_entry(old, new)


def test_extended_path_cannot_shrink_excursions_or_fill_at_first_quote():
    old = first_full()
    new = deepcopy(old)
    with pytest.raises(v.VerificationError, match="wait"):
        v.check_pair_path(old, new)
    new["exit_time"] = (datetime.fromisoformat(old["exit_time"]) + timedelta(minutes=5)).isoformat()
    log = json.loads(new["partial_fast_events"])
    log[-1]["action"] = "failed_launch_pending"
    new["partial_fast_events"] = json.dumps(log)
    assert v.check_pair_path(old, new) is True
    new["max_favourable_r"] = 0
    with pytest.raises(v.VerificationError, match="excursions"):
        v.check_pair_path(old, new)


def test_paired_groups_keep_unknown_counterfactual_and_win_loss_denominators():
    a = first_full()
    z = deepcopy(a)
    z["net_return"] = .01
    old, new = {"a": a, "b": dict(a, net_return=None)}, {"a": z, "b": z}
    result = v.paired_groups(old, new)
    assert result["all"]["n"] == 2 and result["all"]["known"] == 1
    assert result["transitions"] == {"loss_to_win": 1, "flat_or_unknown": 1}
    assert result["all"]["mean_delta_bp"] == pytest.approx((.01 - a["net_return"]) * 10000)


def test_three_verifier_dependency_closure_and_no_strategy_import():
    assert Path(v.v17.__file__).name == "verify_hourly_impulse_failed_launch_v17.py"
    assert Path(v.h.__file__).name == "verify_hourly_impulse_dual_partial_v16.py"
    source = (ROOT / "scripts/verify_hourly_impulse_failed_confirm_v18.py").read_text()
    assert "from yoyo" not in source and "import yoyo" not in source and "simulate_events(" not in source


@pytest.mark.parametrize("full", [False, True])
def test_all_original_opportunities_controls_and_unknown_pairs(full):
    result = run(fixture(full), full)
    assert result["counts"] == dict(cases=251 if full else 4, controls=462 if full else 6,
                                    matched=154 if full else 2, unmatched=97 if full else 2)
    assert result["effects"]["excess_delta"]["unknown_pairs"] == (97 if full else 2)
    assert result["accounting"]["serial_recomputed"] is True


@pytest.mark.parametrize("direction", [-1, 1])
def test_confirmation_fill_uses_second_quote_and_decimal_exact_zero(direction):
    old = first_full(direction)
    new = confirmed(old, 100. + direction * .2)
    v.check_pair_path(old, new)
    assert v.check_candidate(new)["confirmed"] == 1
    v.check_accounting(new)
    assert new["failed_launch_trigger_open_price"] != new["failed_confirm_open_price"]
    assert new["net_return"] == 0


@pytest.mark.parametrize("mutation", ["clock", "due", "not_opposite", "stale_slow", "slow_unknown", "source_reset",
                                      "half", "first_price", "missing_created", "repeat", "latch", "fake_flip"])
def test_confirmation_lifecycle_clock_causality_and_firstness_reject_mutations(mutation):
    old = first_full()
    new = confirmed(old)
    logs = json.loads(new["failed_confirm_events"])
    last, obs = logs[-1], logs[-1]["observation"]
    if mutation == "clock": last["observed_at"] = last["created_at"]
    elif mutation == "due": last["due_at"] = last["created_at"]
    elif mutation == "not_opposite": obs["current_fast"] = f.f.bar(datetime.fromisoformat(obs["current_fast"]["open_time"]), 1)
    elif mutation == "stale_slow": obs["slow_available_at"] = obs["slow"]["open_time"]
    elif mutation == "slow_unknown": obs["slow_state"] = "unknown"
    elif mutation == "source_reset": obs["current_fast"]["raw_segment_id"] = "different"
    elif mutation == "half": new["partial_fraction"] = .5
    elif mutation == "first_price": new["failed_confirm_open_price"] = 100.2
    elif mutation == "missing_created": logs.pop(0)
    elif mutation == "repeat": logs.append(deepcopy(last))
    elif mutation == "latch": last["action"] = "cancelled"; last["reason"] = "profit_recovered"
    elif mutation == "fake_flip": new["partial_fast_flip_count"] += 1
    new["failed_confirm_events"] = json.dumps(logs)
    with pytest.raises(v.VerificationError):
        v.check_candidate(new)


def cancelled_recovery(old):
    """The second fast bar realigns; only a subsequent true edge may half."""
    new = confirmed(old)
    logs = json.loads(new["failed_confirm_events"])
    last = logs[-1]
    when = datetime.fromisoformat(last["observation"]["current_fast"]["open_time"])
    last.update(action="cancelled", reason="fast_not_opposite")
    last["observation"]["current_fast"] = f.f.bar(when, old["direction"])
    underlying = f.opt_in(f.baseline(f.f.trade(old["event_id"], 0, direction=old["direction"]), eligible=True))
    fields = {key: value for key, value in new.items() if key.startswith("failed_confirm_")}
    row = confirm_fields(underlying)
    row.update(fields)
    for key in row:
        if key.startswith("failed_confirm_") and key not in ("failed_confirm_enabled", "failed_confirm_required", "failed_confirm_create_count",
            "failed_confirm_created_at", "failed_confirm_due_at", "failed_confirm_events", "failed_confirm_cancel_count",
            "failed_confirm_status", "failed_confirm_last_reason", "failed_confirm_confirm_count", "failed_confirm_priority_termination_count"):
            row[key] = "unknown" if key.endswith("_state") else None
    row.update(failed_confirm_confirm_count=0, failed_confirm_cancel_count=1, failed_confirm_status="prior_exit",
               failed_confirm_last_reason="fast_not_opposite", failed_confirm_events=json.dumps(logs))
    edges = json.loads(row["partial_fast_events"])
    edges[0]["action"] = "failed_launch_pending"
    edges[0]["gross_return"] = float(v.exact_gross(row, edges[0]["open_price"]))
    row["partial_fast_events"] = json.dumps(edges)
    return row


def test_cancelled_edge_consumed_later_true_edge_can_restore_half():
    old = first_full()
    new = cancelled_recovery(old)
    v.check_pair_path(old, new)
    result = v.check_candidate(new)
    v.check_accounting(new)
    assert result == dict(created=1, cancelled=1, terminated=0, confirmed=0)
    assert new["partial_fraction"] == .5


def test_complete_bundle_imports_with_three_files(tmp_path):
    for name in ("verify_hourly_impulse_failed_confirm_v18.py", "verify_hourly_impulse_failed_launch_v17.py",
                 "verify_hourly_impulse_dual_partial_v16.py"):
        (tmp_path / name).write_bytes((ROOT / "scripts" / name).read_bytes())
    spec = importlib.util.spec_from_file_location("portable_confirm_v18", tmp_path / "verify_hourly_impulse_failed_confirm_v18.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.verify_tables(*fixture(), expected_counts=(4, 6, 2))["status"] == "passed"


def terminated(old, unknown=False):
    reference = confirmed(old)
    log = json.loads(reference["failed_confirm_events"])
    row = confirm_fields(f.opt_in(old))
    row["partial_fast_events"] = reference["partial_fast_events"]
    end = old["exit_time"] if unknown else reference["exit_time"]
    price = old["exit_price"] if unknown else old["initial_stop"]
    gross = old["direction"] * (price / old["entry_price"] - 1)
    row.update(outcome="data_gap_censored" if unknown else "hard_stop", closed=not unknown,
               exit_time=end, exit_price=price,
               hold_minutes=(datetime.fromisoformat(end)-datetime.fromisoformat(row["entry_time"])).total_seconds()/60,
               gross_return=None if unknown else gross, net_return=None if unknown else gross-.002,
               net_r=None if unknown else (gross-.002)/old["risk_pct"], marked_gross_return=gross, marked_net_return=gross-.002,
               max_adverse_r=old["max_adverse_r"] if unknown else -1., partial_fast_status="unknown_source" if unknown else "no_partial_exit",
               failed_launch_status="unknown_source" if unknown else "prior_exit", failed_confirm_status="unknown_source" if unknown else "prior_exit",
               failed_confirm_create_count=1, failed_confirm_priority_termination_count=1,
               failed_confirm_created_at=reference["failed_confirm_created_at"], failed_confirm_due_at=reference["failed_confirm_due_at"])
    resolution = dict(log[0], action="terminated", reason=row["outcome"], observed_at=end,
                      terminal={"outcome": row["outcome"], "closed": row["closed"], "exit_time": end, "exit_price": price})
    row.update(failed_confirm_events=json.dumps([log[0], resolution]), failed_confirm_last_reason=row["outcome"])
    return row


@pytest.mark.parametrize("unknown", [False, True])
def test_priority_stop_and_censor_terminate_without_future_confirmation(unknown):
    old = first_full()
    new = terminated(old, unknown)
    v.check_pair_path(old, new)
    v.check_accounting(new)
    assert v.check_candidate(new) == dict(created=1, cancelled=0, terminated=1, confirmed=0)
    log = json.loads(new["failed_confirm_events"])
    log[-1]["observation"] = json.loads(confirmed(old)["failed_confirm_events"])[-1]["observation"]
    new["failed_confirm_events"] = json.dumps(log)
    with pytest.raises(v.VerificationError, match="future colour"):
        v.check_candidate(new)


def test_known_baseline_new_unknown_stays_unknown_in_all_paired_tables():
    tables, _ = fixture()
    tables["candidate"]["case_trades"][0] = terminated(tables["baseline"]["case_trades"][0], unknown=True)
    data = assemble(tables["baseline"]["case_trades"], tables["baseline"]["control_trades"],
                    tables["candidate"]["case_trades"], tables["candidate"]["control_trades"])
    result = run(data)
    assert result["effects"]["case_delta"]["unknown_pairs"] == 1
    assert result["effects"]["excess_delta"]["unknown_pairs"] == 3
    data[0]["case_delta"][0]["after"] = 0
    with pytest.raises(v.VerificationError):
        run(data)


def test_confirmation_delay_requires_each_arm_serial_recompute():
    tables, _ = fixture()
    old = tables["baseline"]["case_trades"][0]
    recovered = cancelled_recovery(old)
    tables["candidate"]["case_trades"][0] = recovered
    next_old = f.failed(f.baseline(f.f.trade("case1", 1, direction=-1)))
    tables["baseline"]["case_trades"][1] = next_old
    tables["candidate"]["case_trades"][1] = confirmed(next_old)
    data = assemble(tables["baseline"]["case_trades"], tables["baseline"]["control_trades"],
                    tables["candidate"]["case_trades"], tables["candidate"]["control_trades"])
    run(data)
    assert data[0]["baseline"]["single_pending"][1]["portfolio_selected"] is True
    assert data[0]["candidate"]["single_pending"][1]["portfolio_selected"] is False
    assert data[0]["serial_delta"][1]["after"] == 0
    data[0]["candidate"]["single_pending"][1]["portfolio_selected"] = True
    with pytest.raises(v.VerificationError, match="Serial occupancy"):
        run(data)


@pytest.mark.parametrize("mutation", ["case", "control", "rematch", "unknown_zero", "policy", "delta", "mean", "serial", "fee"])
def test_population_pairing_configuration_and_effect_corruptions(mutation):
    tables, summary = fixture()
    if mutation == "case": tables["candidate"]["case_trades"].pop()
    elif mutation == "control": tables["candidate"]["control_trades"].pop()
    elif mutation == "rematch": tables["candidate"]["control_trades"][0]["parent_event_id"] = "case3"
    elif mutation == "unknown_zero": tables["candidate"]["matched"][-1]["excess"] = 0
    elif mutation == "policy": summary["arms"]["candidate"]["policy"]["confirmations"] = 2
    elif mutation == "delta": tables["case_delta"][0]["difference"] = .01
    elif mutation == "mean": summary["effects"]["case_delta"]["mean_bp"] = 99
    elif mutation == "serial": tables["candidate"]["single_pending"].pop()
    else: tables["candidate"]["case_trades"][0]["net_return"] += .001
    with pytest.raises(v.VerificationError):
        run((tables, summary))


def source_fixture(tmp_path, monkeypatch):
    results, summary, contents, started = f.source_fixture(tmp_path, monkeypatch)
    old_identity = str(results.parent.relative_to(tmp_path))
    new_parent = tmp_path / "experiments/active" / v.EXPERIMENT_ID
    results.parent.rename(new_parent)
    results = new_parent / "results"
    new_identity = str(new_parent.relative_to(tmp_path))
    renamed = {name.replace(old_identity + "/", new_identity + "/"): data for name, data in contents.items()}
    contents.clear()
    contents.update(renamed)
    config = json.loads((results.parent / "config.json").read_text())
    config.update(experiment_id=v.EXPERIMENT_ID, policies=[v.BASE_POLICY, v.CANDIDATE_POLICY], parent_results=v.PARENT)
    parent = tmp_path / v.PARENT / "saved.json"
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.write_bytes((tmp_path / f.v.PARENT / "saved.json").read_bytes())
    (results.parent / "config.json").write_text(json.dumps(config))
    config_id = str((results.parent / "config.json").relative_to(tmp_path))
    contents[config_id] = (results.parent / "config.json").read_bytes()
    runner = "yoyo/evaluation/hourly_impulse_failed_confirm_research.py"
    contents[runner] = contents.pop("yoyo/evaluation/hourly_impulse_failed_launch_research.py")
    (tmp_path / runner).write_bytes(contents[runner])
    sources = [dict(path=name, sha256=v.hashlib.sha256(content).hexdigest()) for name, content in contents.items()]
    started["sources"] = sources
    summary.update(sources=sources, config_sha256=v.sha(results.parent / "config.json"))
    (results / "started.json").write_text(json.dumps(started))
    summary["output_hashes"]["started.json"] = v.sha(results / "started.json")
    (results / "summary.json").write_text(json.dumps(summary))
    return results, summary, contents


def test_all_source_pins_come_from_original_git_commit_not_current_auditor(tmp_path, monkeypatch):
    results, summary, _ = source_fixture(tmp_path, monkeypatch)
    receipt = v.verify_sources(tmp_path, results, summary)
    assert receipt["committed_sources_verified"] == 5 and receipt["output_hashes_verified"] == 1
    assert all("scripts/verify_" not in r["path"] for r in receipt["source_pins"])


@pytest.mark.parametrize("mutation", ["source", "input", "output", "config", "extra"])
def test_source_or_output_corruption_cannot_be_silently_skipped(tmp_path, monkeypatch, mutation):
    results, summary, contents = source_fixture(tmp_path, monkeypatch)
    if mutation == "source": contents["yoyo/layers/l3_backtest/hourly_impulse.py"] = b"changed"
    elif mutation == "input": (tmp_path / v.PARENT / "saved.json").write_text("changed")
    elif mutation == "output": summary["output_hashes"]["started.json"] = "0" * 64
    elif mutation == "config": (results.parent / "config.json").write_text("{}")
    else: (results / "extra.json").write_text("{}")
    with pytest.raises((v.VerificationError, KeyError)):
        v.verify_sources(tmp_path, results, summary)


def export_fixture(tmp_path):
    results, _, _, write = f.export_fixture(tmp_path)
    new_parent = tmp_path / "experiments/active" / v.EXPERIMENT_ID
    results.parent.rename(new_parent)
    results = new_parent / "results"
    tables, summary = fixture()
    native, fast = f.f.context_rows(tables)
    config = json.loads((results.parent / "config.json").read_text())
    config["parent_results"] = v.PARENT
    (results.parent / "config.json").write_text(json.dumps(config))
    for arm in v.ARMS:
        for name, filename in v.TABLE_FILES.items():
            write(results / arm / filename, tables[arm][name])
            if arm == "baseline":
                write(tmp_path / v.PARENT / filename, tables[arm][name])
    for name in v.DELTAS:
        write(results / (name + ".csv"), tables[name])
    anchor = {key: dict(rows=len(rows), columns=len(rows[0])) for key, rows in tables["baseline"].items()}
    (results / "anchor_parity.json").write_text(json.dumps(anchor))
    write(results / "native_entry_context.csv.gz", native)
    write(results / "fast_entry_context.csv.gz", fast)
    frozen = json.loads((results / "context_frozen.json").read_text())
    frozen.update(context_sha256=v.sha(results / "native_entry_context.csv.gz"), fast_context_sha256=v.sha(results / "fast_entry_context.csv.gz"))
    (results / "context_frozen.json").write_text(json.dumps(frozen))
    summary["native_context"] = frozen["counts"]
    edges, confirmations = [], []
    for arm in v.ARMS:
        for population in ("case", "control"):
            for row in tables[arm][population + "_trades"]:
                for edge in json.loads(row["partial_fast_events"]):
                    edge = deepcopy(edge)
                    for key in ("previous_fast", "current_fast", "slow"):
                        edge[key] = json.dumps(edge[key])
                    edges.append(dict(arm=arm, population=population, event_id=row["event_id"], **edge))
                if arm == "candidate":
                    for record in json.loads(row["failed_confirm_events"]):
                        confirmations.append(dict(arm=arm, population=population, event_id=row["event_id"],
                                                  action=record["action"], evidence_json=json.dumps(record)))
    write(results / "fast_edges.csv.gz", edges)
    write(results / "confirmation_events.csv.gz", confirmations)
    summary["mechanics"] = {}
    for population in ("case", "control"):
        rows = []
        for a, z in zip(tables["baseline"][population + "_trades"], tables["candidate"][population + "_trades"]):
            before, after = a["net_return"], z["net_return"]
            delta = after-before if before is not None and after is not None else None
            if delta is None:
                before, after = None, None
            transition = "flat_or_unknown" if before is None or after is None or before == 0 or after == 0 else ("win" if before > 0 else "loss") + "_to_" + ("win" if after > 0 else "loss")
            changed = a["outcome"] == "fast_failed_launch"
            row = dict(event_id=a["event_id"], mother_decision_time=a["mother_decision_time"],
                       baseline_net_bp=None if before is None else before*10000, candidate_net_bp=None if after is None else after*10000,
                       delta_net_bp=None if delta is None else delta*10000, exit_delay_minutes=z["hold_minutes"]-a["hold_minutes"],
                       outcome_transition=transition, baseline_failed_full=changed, candidate_confirmed_full=z["outcome"] == "fast_failed_launch",
                       candidate_partial_executed=z["partial_fraction"] == .5, candidate_pending_created=z["failed_confirm_create_count"],
                       candidate_pending_cancelled=z["failed_confirm_cancel_count"], candidate_pending_terminated=z["failed_confirm_priority_termination_count"],
                       recovered_winner=changed and after is not None and after > 0, newly_unknown=a["closed"] and not z["closed"])
            for arm, trade in (("baseline", a), ("candidate", z)):
                for suffix, field in (("exit_time", "exit_time"), ("exit_reason", "outcome"), ("mfe_r", "max_favourable_r"), ("hold_minutes", "hold_minutes")):
                    row[arm + "_" + suffix] = trade[field]
            rows.append(row)
        by_group = defaultdict(list)
        for row in rows:
            by_group[row["outcome_transition"]].append(row)
        groups = []
        for label, part in by_group.items():
            known = [r for r in part if r["delta_net_bp"] is not None]
            groups.append(dict(group=label, n=len(part), known=len(known), old_mean_net_bp=sum(r["baseline_net_bp"] for r in known)/len(known),
                               new_mean_net_bp=sum(r["candidate_net_bp"] for r in known)/len(known), mean_delta_bp=sum(r["delta_net_bp"] for r in known)/len(known),
                               sum_delta_event_bp=sum(r["delta_net_bp"] for r in known)))
        changed = [r for r in rows if r["baseline_failed_full"]]
        summary["mechanics"][population] = dict(total=len(rows), known=len(rows), transitions=dict(Counter(r["outcome_transition"] for r in rows)), groups=groups,
            baseline_failed_full_count=len(changed), candidate_confirmed_full_count=sum(r["candidate_confirmed_full"] for r in rows),
            unchanged_paths=len(rows)-len(changed), pending_events=sum(r["candidate_pending_created"] for r in rows),
            cancelled_pending_events=sum(r["candidate_pending_cancelled"] for r in rows), priority_terminated_pending_events=sum(r["candidate_pending_terminated"] for r in rows),
            changed_improved=sum(r["delta_net_bp"] > 1e-8 for r in changed), changed_hurt=sum(r["delta_net_bp"] < -1e-8 for r in changed),
            changed_unknown_pairs=0, recovered_winners=sum(r["recovered_winner"] for r in rows), newly_unknown=0,
            restored_partial_paths=sum(r["baseline_failed_full"] and r["candidate_partial_executed"] for r in rows),
            baseline_partial_count=sum(r["partial_fraction"] == .5 for r in tables["baseline"][population + "_trades"]),
            candidate_partial_count=sum(r["candidate_partial_executed"] for r in rows), later_exits=sum(r["exit_delay_minutes"] > 0 for r in rows),
            earlier_exits=sum(r["exit_delay_minutes"] < 0 for r in rows), same_exit_time=sum(r["exit_delay_minutes"] == 0 for r in rows))
        write(results / ("confirmed_" + population + "_mechanics.csv"), rows)
        write(results / ("confirmed_" + population + "_groups.csv"), groups)
    monthly = []
    for arm in v.ARMS:
        values = [r["net_return"] for r in tables[arm]["case_trades"]]
        monthly.append(dict(arm=arm, fold="2023H1", month="2023-01", n=len(values), known=len(values), mean_net_bp=sum(values)*10000/len(values)))
    write(results / "monthly_case_net.csv", monthly)
    return results, v.load_tables(results), summary, write


def test_all_saved_lineage_confirmation_export_and_mechanic_arithmetic(tmp_path):
    results, tables, summary, _ = export_fixture(tmp_path)
    run((tables, summary))
    receipt = v.verify_lineage(tmp_path, results, tables, summary)
    assert receipt["anchor_tables"] == 6 and receipt["native_context_rows"] == 20 and receipt["fast_context_rows"] == 10
    assert receipt["recorded_confirmation_events"] == 12
    assert v.verify_mechanics_exports(results, tables, summary) == dict(case_mechanics_rows=4, control_mechanics_rows=6, monthly_rows=2)


@pytest.mark.parametrize("mutation", ["lifecycle_drop", "lifecycle_action", "lifecycle_clock", "monthly_mean", "mechanic_delta",
                                      "summary_count", "summary_mean", "anchor_column", "freeze_outcome"])
def test_saved_export_or_receipt_mutations_fail(tmp_path, mutation):
    results, tables, summary, write = export_fixture(tmp_path)
    if mutation.startswith("lifecycle"):
        rows = v.read_csv(results / "confirmation_events.csv.gz")
        if mutation == "lifecycle_drop": rows.pop()
        elif mutation == "lifecycle_action": rows[0]["action"] = "confirmed"
        else:
            record = json.loads(rows[0]["evidence_json"]); record["observed_at"] = record["due_at"]
            rows[0]["evidence_json"] = json.dumps(record)
        write(results / "confirmation_events.csv.gz", rows)
    elif mutation == "monthly_mean":
        rows = v.read_csv(results / "monthly_case_net.csv"); rows[0]["mean_net_bp"] = "99"; write(results / "monthly_case_net.csv", rows)
    elif mutation == "mechanic_delta":
        rows = v.read_csv(results / "confirmed_case_mechanics.csv"); rows[0]["delta_net_bp"] = "99"; write(results / "confirmed_case_mechanics.csv", rows)
    elif mutation == "summary_count": summary["mechanics"]["case"]["pending_events"] += 1
    elif mutation == "summary_mean": summary["mechanics"]["case"]["groups"][0]["mean_delta_bp"] = 99
    elif mutation == "anchor_column": del tables["baseline"]["case_trades"][0]["max_adverse_r"]
    else:
        frozen = json.loads((results / "context_frozen.json").read_text()); frozen["outcomes_hashed_or_read"] = True
        (results / "context_frozen.json").write_text(json.dumps(frozen))
    with pytest.raises((v.VerificationError, KeyError)):
        run((tables, summary))
        v.verify_lineage(tmp_path, results, tables, summary)
        v.verify_mechanics_exports(results, tables, summary)
