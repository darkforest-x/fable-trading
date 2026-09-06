"""Synthetic saved V19 ledgers; no strategy imports or historical prices."""
from copy import deepcopy
from datetime import datetime, timedelta
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_reduce_v19", ROOT / "scripts/verify_hourly_impulse_failed_reduce_v19.py")
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)
FSPEC = importlib.util.spec_from_file_location("saved_v18_fixture", ROOT / "tests/test_verify_hourly_impulse_failed_confirm_v18.py")
f = importlib.util.module_from_spec(FSPEC)
FSPEC.loader.exec_module(f)


def opt_in(old):
    row = deepcopy(old)
    row.update(failed_reduce_enabled=True, failed_reduce_target_fraction=.5, failed_reduce_role="risk_reduction",
               failed_reduce_fill_count=0, failed_reduce_status="not_reduced_exit" if row["closed"] else "unknown_source",
               failed_reduce_fraction=0, failed_reduce_fill_time=None, failed_reduce_fill_price=None,
               failed_reduce_full_notional_gross_return=None, failed_reduce_realised_gross_return=0., failed_reduce_realised_net_return=0.)
    return row


def reduced(old, price=None, closed=True, hold=120, outcome="transition_colour_exit"):
    row = opt_in(old)
    start, fill = datetime.fromisoformat(row["entry_time"]), datetime.fromisoformat(row["exit_time"])
    end = start+timedelta(minutes=hold)
    price = 100+row["direction"] if price is None else price
    logs = json.loads(row["failed_confirm_events"])
    logs[-1]["observation"].update(fill_action="risk_reduce", fill_fraction=.5, fill_price=row["exit_price"], fill_available_at=fill.isoformat())
    first, last = float(v.v18.exact_gross(row, row["exit_price"])), float(v.v18.exact_gross(row, price))
    gross = .5*first+.5*last
    status = "risk_reduced_closed" if closed else "risk_reduced_censored"
    row.update(partial_fraction=.5, exit_remaining_fraction=.5, partial_exit_time=fill.isoformat(), partial_exit_price=row["exit_price"],
               failed_reduce_fill_count=1, failed_reduce_fraction=.5, failed_reduce_fill_time=fill.isoformat(),
               failed_reduce_fill_price=row["exit_price"], failed_reduce_full_notional_gross_return=first,
               failed_reduce_realised_gross_return=.5*first, failed_reduce_realised_net_return=.5*first-.001,
               realised_partial_gross_return=.5*first, failed_reduce_status=status, partial_fast_status=status,
               failed_launch_count=0, failed_launch_status=status, failed_confirm_status="confirmed_reduced_closed" if closed else "confirmed_reduced_censored",
               failed_confirm_events=json.dumps(logs), exit_time=end.isoformat(), exit_price=price,
               outcome=outcome if closed else "right_censored", closed=closed, hold_minutes=hold,
               gross_return=gross if closed else None, net_return=gross-.002 if closed else None,
               net_r=(gross-.002)/row["risk_pct"] if closed else None, marked_gross_return=gross, marked_net_return=gross-.002,
               transition_trigger_previous_open_time=(end-timedelta(minutes=30)).isoformat() if closed and outcome == "transition_colour_exit" else None,
               transition_trigger_open_time=(end-timedelta(minutes=15)).isoformat() if closed and outcome == "transition_colour_exit" else None,
               transition_trigger_available_at=end.isoformat() if closed and outcome == "transition_colour_exit" else None)
    excursion = row["direction"]*(price-row["entry_price"])/(row["risk_pct"]*row["entry_price"])
    row["max_favourable_r"] = max(row["max_favourable_r"], excursion)
    row["max_adverse_r"] = min(row["max_adverse_r"], excursion)
    if outcome == "hard_stop": row["max_adverse_r"] = -1.
    return row


def assemble(cases, controls, new_cases, new_controls):
    tables, summary = f.assemble(cases, controls, new_cases, new_controls)
    # The production episode builder preserves all original mother identity
    # columns; the inherited compact fixture only carries serial essentials.
    for arm in v.ARMS:
        for population in ("case", "control"):
            trades = v.indexed(tables[arm][population+"_trades"])
            for episode in tables[arm][population+"_episodes"]:
                trade = trades[episode["event_id"]]
                for field in v.EPISODE_IDENTITY_FIELDS:
                    episode[field] = trade[field]
                if population == "control":
                    episode["parent_event_id"] = trade["parent_event_id"]
        episodes = v.indexed(tables[arm]["case_episodes"])
        for serial in tables[arm]["single_pending"]:
            serial.update(episodes[serial["event_id"]])
    summary["experiment_id"] = v.EXPERIMENT_ID
    summary["arms"]["baseline"]["policy"] = deepcopy(v.BASE_POLICY)
    summary["arms"]["candidate"]["policy"] = deepcopy(v.CANDIDATE_POLICY)
    return tables, summary


def fixture(full=False):
    old, _ = f.fixture(full)
    cases, controls = (old["candidate"][key] for key in ("case_trades", "control_trades"))
    return assemble(cases, controls, [reduced(r) if r["outcome"] == "fast_failed_launch" else opt_in(r) for r in cases],
                    [reduced(r) if r["outcome"] == "fast_failed_launch" else opt_in(r) for r in controls])


def run(data, full=False):
    return v.verify_tables(*data, expected_counts=(251, 462, 154) if full else (4, 6, 2))


@pytest.mark.parametrize("full", [False, True])
def test_all_original_cases_controls_pairs_unknowns_and_both_lifecycles(full):
    result = run(fixture(full), full)
    assert result["counts"] == dict(cases=251 if full else 4, controls=462 if full else 6, matched=154 if full else 2, unmatched=97 if full else 2)
    assert result["effects"]["excess_delta"]["unknown_pairs"] == (97 if full else 2)
    assert result["accounting"]["confirmation_lifecycle"]["baseline/case"]["confirmed"] > 0
    assert result["accounting"]["risk_reductions"]["baseline/case"] == 0
    assert result["accounting"]["failed_launch_exits"]["candidate/case"] == 0


@pytest.mark.parametrize("direction", [-1, 1])
@pytest.mark.parametrize("last", [-2., 0., .2, 1.])
def test_original_half_can_reduce_loss_leave_loss_or_recover(direction, last):
    old = f.confirmed(f.first_full(direction), 100+direction*.2)
    new = reduced(old, 100+direction*last)
    v.v18.check_candidate(old)
    v.check_candidate(old, new)
    assert new["partial_fast_realised_net_return"] == 0
    assert new["failed_reduce_realised_net_return"] == 0
    assert new["net_return"] == pytest.approx(.001+.5*last/100-.002)


@pytest.mark.parametrize("direction", [-1, 1])
def test_exact_cost_both_legs_never_becomes_floating_winner(direction):
    old = f.confirmed(f.first_full(direction), 100+direction*.2)
    new = reduced(old, 100+direction*.2)
    v.check_candidate(old, new)
    assert new["net_return"] == 0
    new["net_return"] = 1e-18
    with pytest.raises(v.VerificationError, match="sign|floating winner"):
        v.check_reduced_accounting(new)


def test_half_known_but_unknown_remainder_is_not_known_trade_or_free_capacity():
    tables, _ = fixture()
    a, z = tables["baseline"], tables["candidate"]
    first = next(r for r in a["case_trades"] if r["outcome"] == "fast_failed_launch")
    new = reduced(first, closed=False, hold=10)
    z["case_trades"] = [new if r["event_id"] == first["event_id"] else r for r in z["case_trades"]]
    data = assemble(a["case_trades"], a["control_trades"], z["case_trades"], z["control_trades"])
    result = run(data)
    assert result["effects"]["case_delta"]["unknown_pairs"] == 1
    assert new["failed_reduce_realised_gross_return"] is not None and new["net_return"] is None
    info = v.mechanism_summary(v.indexed(data[0]["baseline"]["case_trades"]), v.indexed(data[0]["candidate"]["case_trades"]))
    assert info["remainder_unknown_count"] == 1
    assert info["risk_realised_net_unknown_remainder_event_bp"] == new["failed_reduce_realised_net_return"]*10000
    assert info["risk_realised_net_event_bp"] == pytest.approx(info["risk_realised_net_known_pairs_event_bp"]+info["risk_realised_net_unknown_remainder_event_bp"])
    new["net_return"] = new["failed_reduce_realised_net_return"]
    with pytest.raises(v.VerificationError, match="Unknown remainder"):
        v.check_reduced_accounting(new)


@pytest.mark.parametrize("field,value", [
    ("failed_reduce_fraction", 1), ("failed_reduce_fill_count", 2), ("failed_reduce_role", "profit_tp"),
    ("partial_fast_fill_count", 1), ("failed_launch_count", 1), ("partial_fast_realised_net_return", -.001),
    ("exit_remaining_fraction", 0), ("realised_partial_gross_return", .02), ("marked_net_return", .02),
    ("risk_pct", .5), ("initial_stop", 95), ("entry_price", 101), ("failed_reduce_fill_price", 99),
    ("failed_confirm_gross_return", .01), ("partial_fast_flip_count", 2), ("signal_close", 101),
    ("failed_reduce_status", "risk_reduced_censored"), ("failed_confirm_status", "confirmed_closed"),
    ("max_favourable_r", 0), ("failed_confirm_required", 1), ("partial_fraction", .25)])
def test_mutated_reduction_accounting_or_original_decision_rejected(field, value):
    old = f.confirmed(f.first_full())
    new = reduced(old)
    new[field] = value
    with pytest.raises(v.VerificationError): v.check_candidate(old, new)


@pytest.mark.parametrize("kind", ["extra_pending", "quote", "slow", "second_clock", "missing_fill", "new_flip"])
def test_exact_confirmation_evidence_and_no_new_pending(kind):
    old = f.confirmed(f.first_full())
    new = reduced(old)
    log = json.loads(new["failed_confirm_events"])
    if kind == "extra_pending": log.append(deepcopy(log[0]))
    if kind == "quote": log[-1]["observation"]["open_price"] += .1
    if kind == "slow": log[-1]["observation"]["slow"]["side"] *= -1
    if kind == "second_clock": log[-1]["observed_at"] = log[-1]["created_at"]
    if kind == "missing_fill": del log[-1]["observation"]["fill_available_at"]
    if kind == "new_flip": log[-1]["action"] = "created"
    new["failed_confirm_events"] = json.dumps(log)
    with pytest.raises(v.VerificationError): v.check_candidate(old, new)


def test_post_reduction_real_edges_are_logged_but_never_a_second_fast_fill():
    old = f.confirmed(f.first_full())
    new = reduced(old)
    events = json.loads(new["partial_fast_events"])
    events.append(f.f.f.edge(new, minutes=20, action="already_partial"))
    new.update(partial_fast_events=json.dumps(events), partial_fast_flip_count=2)
    v.check_candidate(old, new)
    events[-1]["action"] = "executed"
    new["partial_fast_events"] = json.dumps(events)
    with pytest.raises(v.VerificationError, match="second fast fill"):
        v.check_candidate(old, new)


def test_prior_full_absent_every_old_field_unchanged_including_winners():
    old = f.confirm_fields(f.f.opt_in(f.f.baseline(f.f.f.trade("x", 0), eligible=False)))
    new = opt_in(old)
    v.check_candidate(old, new)
    new["partial_fast_reset_count"] = 2
    with pytest.raises(v.VerificationError): v.check_candidate(old, new)


@pytest.mark.parametrize("mutation", ["drop", "pairs", "serial", "cost", "unknown", "policy", "holdout"])
def test_full_table_denominators_serial_and_economics_cannot_drift(mutation):
    tables, summary = fixture()
    if mutation == "drop": tables["candidate"]["case_trades"].pop()
    if mutation == "pairs": tables["candidate"]["control_trades"][0]["parent_event_id"] = "case3"
    if mutation == "serial": tables["candidate"]["single_pending"][0]["portfolio_selected"] = False
    if mutation == "cost": tables["candidate"]["case_trades"][0]["net_return"] += .001
    if mutation == "unknown": tables["excess_delta"][-1]["difference"] = 0
    if mutation == "policy": summary["arms"]["candidate"]["policy"]["ma_length"] = 20
    if mutation == "holdout": summary["holdout_consumed"] = True
    with pytest.raises(v.VerificationError): run((tables, summary))


@pytest.mark.parametrize("arm", v.ARMS)
def test_coordinated_episode_serial_and_matched_fold_corruption_rejected(arm):
    tables, summary = fixture()
    for name in ("case_episodes", "single_pending", "matched"):
        row = next(r for r in tables[arm][name] if r["event_id"] == "case0")
        row["fold"] = "2024H2"
    # All three derived ledgers still agree, but the immutable source trade
    # belongs to 2023H1. They cannot invent another occupancy bucket.
    assert tables[arm]["case_trades"][0]["fold"] == "2023H1"
    with pytest.raises(v.VerificationError): run((tables, summary))


@pytest.mark.parametrize("arm", v.ARMS)
@pytest.mark.parametrize("population", ["case", "control"])
@pytest.mark.parametrize("field,value", [
    ("fold", "2024H2"), ("direction", -7), ("initial_stop", 99.9),
    ("signal_atr", 17), ("signal_close", 17),
    ("signal_time", "2023-01-01T23:00:00.000000001+00:00"),
    ("decision_time", "2023-01-02T00:00:00.000000001+00:00"),
])
def test_all_episode_own_entry_identity_fields_are_anchored_to_trade(arm, population, field, value):
    tables, summary = fixture()
    tables[arm][population+"_episodes"][0][field] = value
    with pytest.raises(v.VerificationError): run((tables, summary))


@pytest.mark.parametrize("field", sorted(v.EPISODE_IDENTITY_FIELDS))
def test_episode_required_identity_cannot_be_dropped(field):
    tables, summary = fixture()
    del tables["candidate"]["control_episodes"][0][field]
    with pytest.raises(v.VerificationError): run((tables, summary))


@pytest.mark.parametrize("field,value", [("entry_price", 101), ("entry_open", 102),
    ("parent_event_id", "foreign"), ("source_feature", 123),
    ("mother_signal_time", "2023-01-01T23:00:00.000000001+00:00")])
def test_additional_shared_immutable_fields_are_not_an_identity_allowlist(field, value):
    tables, _ = fixture()
    trade = deepcopy(tables["baseline"]["control_trades"][0])
    episode = deepcopy(tables["baseline"]["control_episodes"][0])
    if field not in trade:
        trade[field] = trade["signal_time"] if field.endswith("_time") else 100
    episode[field] = trade[field]
    v.check_episode_identity(trade, episode)
    episode[field] = value
    with pytest.raises(v.VerificationError): v.check_episode_identity(trade, episode)


def test_shared_event_id_must_be_exact_even_outside_table_indexing():
    tables, _ = fixture()
    trade, episode = tables["baseline"]["case_trades"][0], tables["baseline"]["case_episodes"][0]
    episode["event_id"] = "foreign"
    with pytest.raises(v.VerificationError): v.check_episode_identity(trade, episode)


def test_candidate_cannot_remove_previously_shared_original_feature():
    tables, summary = fixture()
    for arm in v.ARMS:
        tables[arm]["control_trades"][0]["source_feature"] = 10.
    tables["baseline"]["control_episodes"][0]["source_feature"] = 10.
    with pytest.raises(v.VerificationError, match="episode lost original column"):
        run((tables, summary))


def test_episode_identity_allows_csv_numeric_and_exact_timezone_serialization():
    tables, _ = fixture()
    trade = tables["baseline"]["control_trades"][0]
    episode = deepcopy(tables["baseline"]["control_episodes"][0])
    episode["initial_stop"] = str(trade["initial_stop"])
    episode["signal_time"] = trade["signal_time"].replace("+00:00", "Z")
    v.check_episode_identity(trade, episode)


def test_four_stdlib_verifier_dependency_pins_no_strategy_import():
    names = {Path(mod.__file__).name for mod in (v, v.v18, v.v17, v.h)}
    assert len(names) == 4
    text = Path(v.__file__).read_text()
    assert "from yoyo" not in text and "import yoyo" not in text and "simulate_events(" not in text


def source_fixture(tmp_path, monkeypatch):
    results, summary, contents = f.source_fixture(tmp_path, monkeypatch)
    started = json.loads((results/"started.json").read_text())
    old_identity = str(results.parent.relative_to(tmp_path))
    new_parent = tmp_path/"experiments/active"/v.EXPERIMENT_ID
    results.parent.rename(new_parent)
    results = new_parent/"results"
    renamed = {k.replace(old_identity+"/", str(new_parent.relative_to(tmp_path))+"/"): data for k, data in contents.items()}
    contents.clear(); contents.update(renamed)
    config = json.loads((results.parent/"config.json").read_text())
    config.update(experiment_id=v.EXPERIMENT_ID, policies=[v.BASE_POLICY, v.CANDIDATE_POLICY], parent_results=v.PARENT,
                  structure_reference=v.STRUCTURE_REFERENCE, structure_columns=v.STRUCTURE_COLUMNS, structure_inputs={})
    parent = tmp_path/v.PARENT/"saved.json"
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.write_bytes((tmp_path/f.v.PARENT/"saved.json").read_bytes())
    for name in ("case_trades.csv.gz", "control_trades.csv.gz"):
        path = tmp_path/v.STRUCTURE_REFERENCE/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic structure fixture")
        config["structure_inputs"][name] = v.sha(path)
    summary["structure_inputs"] = config["structure_inputs"]
    (results.parent/"config.json").write_text(json.dumps(config))
    config_id = str((results.parent/"config.json").relative_to(tmp_path))
    contents[config_id] = (results.parent/"config.json").read_bytes()
    runner = "yoyo/evaluation/hourly_impulse_failed_reduce_research.py"
    contents[runner] = contents.pop("yoyo/evaluation/hourly_impulse_failed_confirm_research.py")
    (tmp_path/runner).write_bytes(contents[runner])
    sources = [dict(path=name, sha256=v.hashlib.sha256(data).hexdigest()) for name, data in contents.items()]
    started["sources"] = sources
    summary.update(sources=sources, config_sha256=v.sha(results.parent/"config.json"))
    (results/"started.json").write_text(json.dumps(started))
    summary["output_hashes"]["started.json"] = v.sha(results/"started.json")
    (results/"summary.json").write_text(json.dumps(summary))
    return results, summary, contents


def test_all_committed_strategy_and_structure_hashes_separate_from_current_auditor(tmp_path, monkeypatch):
    results, summary, _ = source_fixture(tmp_path, monkeypatch)
    receipt = v.verify_sources(tmp_path, results, summary)
    assert receipt["committed_sources_verified"] == 5
    assert all("scripts/verify_" not in r["path"] for r in receipt["source_pins"])


@pytest.mark.parametrize("mutation", ["source", "input", "output", "config", "extra", "structure"])
def test_original_source_pin_and_output_changes_fail_closed(tmp_path, monkeypatch, mutation):
    results, summary, contents = source_fixture(tmp_path, monkeypatch)
    if mutation == "source": contents["yoyo/layers/l3_backtest/hourly_impulse.py"] = b"changed"
    if mutation == "input": (tmp_path/v.PARENT/"saved.json").write_text("changed")
    if mutation == "output": summary["output_hashes"]["started.json"] = "0"*64
    if mutation == "config": (results.parent/"config.json").write_text("{}")
    if mutation == "extra": (results/"extra.json").write_text("{}")
    if mutation == "structure": (tmp_path/v.STRUCTURE_REFERENCE/"case_trades.csv.gz").write_bytes(b"changed")
    with pytest.raises((v.VerificationError, KeyError)):
        v.verify_sources(tmp_path, results, summary)


def export_fixture(tmp_path):
    results, _, _, write = f.export_fixture(tmp_path)
    new_parent = tmp_path/"experiments/active"/v.EXPERIMENT_ID
    results.parent.rename(new_parent)
    results = new_parent/"results"
    tables, summary = fixture()
    native, fast = f.f.f.context_rows(tables)
    config = json.loads((results.parent/"config.json").read_text())
    config.update(parent_results=v.PARENT, structure_reference=v.STRUCTURE_REFERENCE,
                  structure_columns=v.STRUCTURE_COLUMNS, structure_inputs={})
    for arm in v.ARMS:
        for name, filename in v.TABLE_FILES.items():
            write(results/arm/filename, tables[arm][name])
            if arm == "baseline": write(tmp_path/v.PARENT/filename, tables[arm][name])
    for population in ("case", "control"):
        # Structural reference deliberately contains absurd returns. Only the
        # eight explicitly pinned path fields may be borrowed by the audit.
        reference = [dict(r, gross_return=99., net_return=88.) for r in tables["candidate"][population+"_trades"]]
        path = tmp_path/v.STRUCTURE_REFERENCE/(population+"_trades.csv.gz")
        write(path, reference)
        config["structure_inputs"][path.name] = v.sha(path)
    (results.parent/"config.json").write_text(json.dumps(config))
    summary["structure_inputs"] = config["structure_inputs"]
    receipt = dict(reference=v.STRUCTURE_REFERENCE, inputs=config["structure_inputs"], columns=v.STRUCTURE_COLUMNS,
                   checks={p: dict(rows=len(tables["candidate"][p+"_trades"]), columns=8) for p in ("case", "control")}, pnl_borrowed=False)
    (results/"remainder_structure_parity.json").write_text(json.dumps(receipt))
    for name in v.DELTAS: write(results/(name+".csv"), tables[name])
    anchor = {k: dict(rows=len(rows), columns=len(rows[0])) for k, rows in tables["baseline"].items()}
    (results/"anchor_parity.json").write_text(json.dumps(anchor))
    write(results/"native_entry_context.csv.gz", native)
    write(results/"fast_entry_context.csv.gz", fast)
    frozen = json.loads((results/"context_frozen.json").read_text())
    frozen.update(context_sha256=v.sha(results/"native_entry_context.csv.gz"), fast_context_sha256=v.sha(results/"fast_entry_context.csv.gz"))
    (results/"context_frozen.json").write_text(json.dumps(frozen))
    summary["native_context"] = frozen["counts"]
    edges, confirmations = [], []
    for arm in v.ARMS:
        for population in ("case", "control"):
            for row in tables[arm][population+"_trades"]:
                for event in json.loads(row["partial_fast_events"]):
                    event = deepcopy(event)
                    for key in ("previous_fast", "current_fast", "slow"): event[key] = json.dumps(event[key])
                    edges.append(dict(arm=arm, population=population, event_id=row["event_id"], **event))
                for record in json.loads(row["failed_confirm_events"]):
                    confirmations.append(dict(arm=arm, population=population, event_id=row["event_id"], action=record["action"], evidence_json=json.dumps(record)))
    write(results/"fast_edges.csv.gz", edges)
    write(results/"confirmation_events.csv.gz", confirmations)
    summary["mechanics"] = {}
    for population in ("case", "control"):
        a, z = (v.indexed(tables[arm][population+"_trades"]) for arm in v.ARMS)
        summary["mechanics"][population] = v.mechanism_summary(a, z)
        write(results/("reduced_"+population+"_mechanics.csv"), v.mechanic_rows(a, z))
        write(results/("reduced_"+population+"_groups.csv"), summary["mechanics"][population]["groups"])
    monthly = []
    for arm in v.ARMS:
        values = [r["net_return"] for r in tables[arm]["case_trades"]]
        monthly.append(dict(arm=arm, fold="2023H1", month="2023-01", n=len(values), known=len(values), mean_net_bp=sum(values)*10000/len(values)))
    write(results/"monthly_case_net.csv", monthly)
    return results, v.load_tables(results), summary, write


def test_baseline_full_old_columns_two_arm_logs_and_structure_only_reference(tmp_path):
    results, tables, summary, _ = export_fixture(tmp_path)
    run((tables, summary))
    assert v.verify_lineage(tmp_path, results, tables, summary)["recorded_confirmation_events"] == 24
    receipt = v.verify_structure(tmp_path, results, tables, summary)
    assert receipt["rows"] == dict(case=4, control=6) and receipt["return_columns_used"] is False
    assert v.verify_mechanics_exports(results, tables, summary) == dict(case_mechanics_rows=4, control_mechanics_rows=6, monthly_rows=2)


@pytest.mark.parametrize("mutation", ["baseline_log", "fill_evidence", "anchor", "freeze", "structure_time", "structure_id"])
def test_saved_prefix_context_or_structure_corruption_fails(tmp_path, mutation):
    results, tables, summary, write = export_fixture(tmp_path)
    if mutation in ("baseline_log", "fill_evidence"):
        rows = v.read_csv(results/"confirmation_events.csv.gz")
        if mutation == "baseline_log": rows = [r for r in rows if r["arm"] != "baseline"]
        else:
            row = next(r for r in rows if r["arm"] == "candidate" and r["action"] == "confirmed")
            record = json.loads(row["evidence_json"]); record["observation"]["fill_fraction"] = 1
            row["evidence_json"] = json.dumps(record)
        write(results/"confirmation_events.csv.gz", rows)
    elif mutation == "anchor": del tables["baseline"]["case_trades"][0]["signal_close"]
    elif mutation == "freeze":
        frozen = json.loads((results/"context_frozen.json").read_text()); frozen["outcomes_hashed_or_read"] = True
        (results/"context_frozen.json").write_text(json.dumps(frozen))
    else:
        path = tmp_path/v.STRUCTURE_REFERENCE/"case_trades.csv.gz"
        rows = v.read_csv(path)
        if mutation == "structure_id": rows[0]["event_id"] = "foreign"
        else: rows[0]["exit_time"] = rows[0]["entry_time"]
        write(path, rows)
        config = json.loads((results.parent/"config.json").read_text())
        config["structure_inputs"][path.name] = v.sha(path)
        (results.parent/"config.json").write_text(json.dumps(config))
    with pytest.raises((v.VerificationError, KeyError)):
        v.verify_lineage(tmp_path, results, tables, summary)
        v.verify_structure(tmp_path, results, tables, summary)


def test_cli_new_receipt_only_and_no_inferential_or_raw_claim(tmp_path, monkeypatch, capsys):
    import sys
    results = tmp_path/"results"
    results.mkdir()
    out = tmp_path/"receipt.json"
    def fake_verify(directory, summary):
        assert directory == results and summary is None
        return dict(status="passed", **v.SCOPE)
    monkeypatch.setattr(v, "verify", fake_verify)
    monkeypatch.setattr(sys, "argv", ["verify", "--results", str(results), "--out", str(out)])
    assert v.main() == 0
    saved = out.read_bytes()
    assert json.loads(saved)["raw_replay"] is False
    assert v.main() == 1 and out.read_bytes() == saved
    assert '"status": "failed"' in capsys.readouterr().out
