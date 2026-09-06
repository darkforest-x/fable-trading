"""Saved-only synthetic V20 auditor tests; never read prices or outcomes."""
import ast
from collections import Counter
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import gzip
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


PATH = Path(__file__).resolve().parents[1]/"scripts/verify_hourly_impulse_structure_v20.py"
SPEC = importlib.util.spec_from_file_location("v20_saved_auditor_test", PATH)
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def clock(hour):
    return (START + timedelta(hours=hour)).isoformat()


def fixture():
    """Explicit expected trace: unknown, up, same-side recross, down, gap reset."""
    trace = []
    for i in range(28):
        if i == 26:
            continue
        row = dict(open_time=clock(i), open=100., high=101., low=99., close=100., volume=1., segment_id=int(i > 26))
        if i == 10:
            row.update(high=110., low=90.)
        if i in (21, 23):
            row.update(high=112., close=111.)
        if i == 24:
            row.update(low=88., close=89.)
        active = 20 <= i <= 25
        state = 1 if 21 <= i <= 23 else -1 if 24 <= i <= 25 else None
        before = 1 if 22 <= i <= 24 else -1 if i == 25 else None
        change = 1 if i == 21 else -1 if i == 24 else 0
        row.update(structure_available_at=clock(i+1), structure_count=i+1 if i < 26 else i-26,
            structure_segment_id=int(i > 26), structure_state_before=before, structure_state=state,
            structure_break_direction=change, structure_high=110. if active else None,
            structure_low=90. if active else None, structure_high_origin=clock(10) if active else None,
            structure_low_origin=clock(10) if active else None,
            structure_high_confirmed_at=clock(21) if active else None,
            structure_low_confirmed_at=clock(21) if active else None,
            structure_last_break_available_at=clock(22) if state == 1 else clock(25) if state == -1 else None,
            structure_signal_close=row["close"], structure_break_on_k1=bool(change), structure_known=state is not None,
            structure_reason="known" if state is not None else "no_confirmed_break" if active else "warmup")
        trace.append(row)
    lookup = {r["open_time"]: r for r in trace}

    def original(identity, i, direction, parent=None):
        row = dict(event_id=identity, signal_time=clock(i), decision_time=clock(i+1),
            direction=direction, signal_close=lookup[clock(i)]["close"], fold="2024H1",
            source_id="same-original-source", original_float=.123456)
        if parent is not None:
            row["parent_event_id"] = parent
        return row

    mothers = [original("c0", 21, 1), original("c1", 22, -1), original("c2", 27, 1)]
    controls = [original("r0", 22, 1, "c0"), original("r1", 23, 1, "c0"), original("r2", 25, 1, "c0")]
    assignments = [dict(event_id=r["event_id"], fold=r["fold"], decision_time=r["decision_time"],
        match_status="matched" if i == 0 else "missing_causal_matching_support" if i == 2 else "insufficient_exact_controls")
        for i, r in enumerate(mothers)]
    context = []
    for population, rows in (("case", mothers), ("control", controls)):
        for row in rows:
            source = lookup[row["signal_time"]]
            new = dict(row, population=population, **{f: source[f] for f in v.TRACE_FIELDS})
            new["structure_raw_segment_id"] = 2 if row["event_id"] == "c2" else 0
            new["structure_gate_state"] = "unknown" if source["structure_state"] is None else (
                "accepted" if source["structure_state"] == row["direction"] else "abstain")
            context.append(new)
    counts = []
    for population in ("case", "control"):
        rows = [r for r in context if r["population"] == population]
        dimensions = {"all": ["all"], "fold": list(v.FOLDS), "direction": ["1", "-1"],
            "month": ["%d-%02d" % (y, m) for y in (2023, 2024) for m in range(1, 13)]}
        for dimension, keys in dimensions.items():
            for key in keys:
                part = [r for r in rows if dimension == "all" or (
                    r["fold"] == key if dimension == "fold" else str(r["direction"]) == key if dimension == "direction"
                    else r["decision_time"][:7] == key)]
                states = Counter(r["structure_gate_state"] for r in part)
                counts.append(dict(population=population, dimension=dimension, key=key, total=len(part),
                    **{s: states[s] for s in v.STATES}, accepted_rate=states["accepted"]/len(part) if part else None))
    matched = [dict(event_id="c0", fold="2024H1", case_state="accepted", control_ids="r0|r1|r2",
        control_total=3, control_accepted=2, control_abstain=1, control_unknown=0, all_known=True)]
    summary = dict(population={"case": dict(total=3, accepted=1, abstain=1, unknown=1),
        "control": dict(total=3, accepted=2, abstain=1, unknown=0)},
        support_values=dict(events=1, minimum_fold_events=0, active_months=1, minimum_fold_months=0),
        support_gates={k: False for k in v.SUPPORT}, support_pass=False,
        matching=dict(assigned=1, unassigned=2, coverage=1/3, required=.9, **{"pass": False}),
        status="insufficient_support_no_outcomes", outcomes_read=False, holdout_consumed=False,
        training_eligible=False, production_eligible=False, independent_validation=False,
        overall_goal_achieved=False, new_intrabar_replays=0)
    return dict(context=context, hourly_trace=trace, counts=counts, matched=matched, summary=summary,
        mothers=mothers, controls=controls, assignments=assignments, expected_counts=(3, 3, 1))


def test_explicit_long_short_gap_trace_and_all_denominators():
    result = v.verify_tables(**fixture())
    assert result["status"] == "passed"
    assert result["hourly_rows"] == 27
    assert result["population"]["case"]["unknown"] == 1
    assert result["count_rows"] == 62
    assert result["matched_groups"] == 1 and result["unmatched"] == 2
    assert result["raw_aggregation_verified"] is False
    assert result["economics_verified"] is False


@pytest.mark.parametrize("field,value", [
    ("structure_high_origin", clock(9)), ("structure_high_confirmed_at", clock(20)),
    ("structure_high_confirmed_at", clock(21).replace("00+", "00.000000001+")),
    ("structure_low_confirmed_at", clock(10)), ("structure_high", 109.),
    ("structure_state", 1), ("structure_known", True), ("structure_reason", "known"),
    ("structure_state_before", 1), ("structure_last_break_available_at", clock(21)),
])
def test_future_backfill_offbyone_or_corrupt_pivot_metadata_rejected(field, value):
    f = fixture()
    f["hourly_trace"][20][field] = value
    with pytest.raises(v.VerificationError):
        v.verify_tables(**f)


@pytest.mark.parametrize("field,value", [
    ("structure_state", -1), ("structure_high", 110.), ("structure_low", 90.),
    ("structure_state_before", -1), ("structure_count", 28),
    ("structure_last_break_available_at", clock(25)), ("structure_segment_id", 0),
])
def test_gap_carry_rejected(field, value):
    f = fixture()
    f["hourly_trace"][26][field] = value
    with pytest.raises(v.VerificationError):
        v.verify_tables(**f)


def test_same_side_recross_is_not_new_directional_break():
    f = fixture()
    f["hourly_trace"][23]["structure_break_direction"] = 1
    with pytest.raises(v.VerificationError):
        v.verify_tables(**f)


@pytest.mark.parametrize("field,value", [("structure_gate_state", "accepted"),
    ("structure_state", 1), ("structure_available_at", clock(22))])
def test_case_gate_copied_to_opposite_control_rejected(field, value):
    f = fixture()
    f["context"][-1][field] = value
    with pytest.raises(v.VerificationError):
        v.verify_tables(**f)


def test_deleted_unknown_and_coerced_zero_rejected():
    f = fixture()
    f["context"].pop(2)
    with pytest.raises(v.VerificationError):
        v.verify_tables(**f)
    f = fixture()
    f["context"][2]["structure_gate_state"] = "abstain"
    with pytest.raises(v.VerificationError):
        v.verify_tables(**f)


@pytest.mark.parametrize("field,value", [("source_id", "foreign"), ("fold", "2023H2"),
    ("direction", -1), ("signal_close", 110.), ("event_id", "changed"),
    ("original_float", .12346), ("decision_time", clock(23))])
def test_full_original_request_parity(field, value):
    f = fixture()
    f["context"][0][field] = value
    with pytest.raises(v.VerificationError):
        v.verify_tables(**f)


@pytest.mark.parametrize("part", ["counts", "matched", "assignments", "hourly_trace"])
def test_duplicate_rows_rejected(part):
    f = fixture()
    f[part].append(deepcopy(f[part][0]))
    with pytest.raises(v.VerificationError):
        v.verify_tables(**f)


def test_missing_empty_month_count_rejected():
    f = fixture()
    f["counts"].pop()
    with pytest.raises(v.VerificationError):
        v.verify_tables(**f)


@pytest.mark.parametrize("field,value", [("control_ids", "r0|r1|r3"), ("control_accepted", 3),
    ("control_unknown", 1), ("all_known", False), ("case_state", "abstain")])
def test_matched_support_all_three_unchanged(field, value):
    f = fixture()
    f["matched"][0][field] = value
    with pytest.raises(v.VerificationError):
        v.verify_tables(**f)


@pytest.mark.parametrize("field,value", [("outcomes_read", True), ("support_pass", True),
    ("overall_goal_achieved", True), ("independent_validation", True), ("holdout_consumed", True)])
def test_false_success_and_support_bypass_rejected(field, value):
    f = fixture()
    f["summary"][field] = value
    with pytest.raises(v.VerificationError):
        v.verify_tables(**f)


def test_timezone_and_nanosecond_precision():
    assert v.stamp("2024-01-01T08:00:00+08:00") == v.stamp(clock(0))
    assert v.stamp("2024-01-01T00:00:00.000000001Z") == v.stamp(clock(0)) + 1
    for value in ("2024-01-01", 1704067200, "2024-01-01T00:00:00", True):
        with pytest.raises(v.VerificationError):
            v.stamp(value)


def test_tie_inclusive_flat_window_confirms_but_never_knows_direction():
    trace = []
    for i in range(23):
        row = dict(open_time=clock(i), open=100., high=101., low=99., close=100., segment_id=0)
        row.update({field: None for field in v.TRACE_FIELDS})
        row.update(structure_available_at=clock(i+1), structure_count=i+1, structure_segment_id=0,
            structure_break_direction=0, structure_known=False, structure_break_on_k1=False,
            structure_signal_close=100., structure_reason="warmup" if i < 20 else "no_confirmed_break")
        if i >= 20:
            row.update(structure_high=101., structure_low=99., structure_high_origin=clock(i-10),
                structure_low_origin=clock(i-10), structure_high_confirmed_at=clock(i+1),
                structure_low_confirmed_at=clock(i+1))
        trace.append(row)
    facts = v.reconstruct_trace(trace)
    assert all(not r["structure_known"] for r in facts.values())
    assert facts[v.stamp(clock(22))]["structure_high_confirmed_at"] == v.stamp(clock(23))


def test_csv_roundtrip_keeps_nullable_and_boolean_semantics(tmp_path):
    f = fixture()
    for key in ("context", "hourly_trace", "counts", "matched", "mothers", "controls", "assignments"):
        path = tmp_path/(key+".csv.gz")
        rows = f[key]
        fields = sorted(set().union(*(row.keys() for row in rows)))
        with gzip.open(path, "wt", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        f[key] = v.read_csv(path)
    assert v.verify_tables(**f)["status"] == "passed"


@pytest.mark.parametrize("field", ["net_return", "outcome", "exit_time", "mfe", "policy_fee_fraction"])
def test_support_reader_refuses_economics_before_rows(tmp_path, field):
    path = tmp_path/"bad.csv"
    path.write_text("event_id,"+field+"\nx,secret\n")
    with pytest.raises(v.VerificationError, match="Outcome schema"):
        v.read_csv(path)


def test_no_strategy_import_or_market_reader():
    tree = ast.parse(PATH.read_text())
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not any((module or "").startswith(("yoyo", "pandas", "numpy")) for module in imports)
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not calls & {"load_source", "resample_complete", "add_structure_context", "evaluate_cached", "simulate_events"}


def test_config_gate_cannot_relax_after_support():
    config = json.loads((PATH.parents[1]/v.EXPERIMENT_PATH/"config.json").read_text())
    v.verify_config(config)
    for key in ("gate", "support", "phase_end_exclusive", "request_inputs"):
        changed = deepcopy(config)
        changed[key] = None
        with pytest.raises(v.VerificationError):
            v.verify_config(changed)


def test_safe_paths_reject_outside_and_symlink(tmp_path):
    for value in ("../escape", "/absolute"):
        with pytest.raises(v.VerificationError):
            v.safe_path(tmp_path, value)
    (tmp_path/"link").symlink_to(tmp_path/"target")
    with pytest.raises(v.VerificationError):
        v.safe_path(tmp_path, "link")


def test_read_json_hash_and_cli_fail_without_data(tmp_path, monkeypatch, capsys):
    path = tmp_path/"x.json"
    path.write_text('{"only":"synthetic"}')
    assert v.read_json(path) == {"only": "synthetic"}
    assert len(v.sha(path)) == 64
    monkeypatch.setattr("sys.argv", [str(PATH), "--root", str(tmp_path)])
    assert v.main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"


def disk_fixture(tmp_path, monkeypatch, resumed=False):
    """Temporary saved-only files; git responses are synthetic byte snapshots."""
    f = fixture()
    results = tmp_path/v.EXPERIMENT_PATH/"results"
    results.mkdir(parents=True)

    def json_file(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def csv_file(path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "wt", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=sorted(set().union(*(r.keys() for r in rows))))
            writer.writeheader()
            writer.writerows(rows)

    for name, key in (("entry_context", "context"), ("hourly_trace", "hourly_trace"),
                      ("counts", "counts"), ("matched_support", "matched")):
        csv_file(results/(name+".csv.gz"), f[key])
    for name, key in (("original_mothers.csv.gz", "mothers"), ("control_mothers.csv.gz", "controls"),
                      ("assignments.csv", "assignments")):
        csv_file(tmp_path/v.PARENT_PATH/name, f[key])
    json_file(tmp_path/v.PARENT_PATH/"assignment_receipt.json", {"synthetic": True})
    monkeypatch.setattr(v, "INPUTS", {name: v.sha(tmp_path/v.PARENT_PATH/name) for name in v.INPUTS})
    config = json.loads((PATH.parents[1]/v.EXPERIMENT_PATH/"config.json").read_text())
    base = {"source": {"sha256": "a"*64}}
    json_file(tmp_path/v.BASE_PATH, base)
    monkeypatch.setattr(v, "BASE_SHA256", v.sha(tmp_path/v.BASE_PATH))
    config.update(request_inputs=dict(v.INPUTS), base_config_sha256=v.BASE_SHA256)
    json_file(results.parent/"config.json", config)
    (results.parent/"PROJECT_PLAN.md").write_text("Frozen synthetic plan, no prices.")
    pine = tmp_path/v.PINE
    pine.parent.mkdir(parents=True)
    pine.write_text("// synthetic public source")
    monkeypatch.setattr(v, "PINE_SHA256", v.sha(pine))
    runner = "yoyo/evaluation/hourly_impulse_structure_research.py"
    path = tmp_path/runner
    path.parent.mkdir(parents=True)
    path.write_text("# original synthetic runner")
    monkeypatch.setattr(v, "SOURCE_FILES", {runner, v.PINE})
    source_names = {runner, v.PINE, v.BASE_PATH, v.EXPERIMENT_PATH+"/config.json", v.EXPERIMENT_PATH+"/PROJECT_PLAN.md"}
    original = {name: (tmp_path/name).read_bytes() for name in source_names}
    sources = [dict(path=name, sha256=v.sha(tmp_path/name)) for name in sorted(source_names)]
    hashes = {name: v.sha(results/name) for name in v.CSV_NAMES}
    wall = lambda minute: "2026-09-06T12:%02d:00+00:00" % minute
    receipt = dict(sha256="a"*64, holdout_price_rows=0,
        phase_price_last_open="2024-12-31T23:55:00Z", physical_last_open="2026-02-28T23:55:00Z")
    frozen = dict(at=wall(2), requests=713, output_hashes=hashes, outcomes_read=False, source_receipt=receipt)
    json_file(results/"context_frozen.json", frozen)
    json_file(results/"started.json", dict(at=wall(1), sources=sources, builder_commit="original"))
    summary = dict(f["summary"], experiment_id=v.EXPERIMENT_ID, sources=sources, output_hashes=hashes,
        config_sha256=v.sha(results.parent/"config.json"), source_receipt=receipt, generated_at=wall(8))
    if resumed:
        path.write_text("# serialization-only synthetic runner fix")
        summary.update(resume_sources=[dict(path=name, sha256=v.sha(tmp_path/name)) for name in sorted(source_names)],
            resume_builder_commit="resume", preserved_first_failure="failure.json", frozen_features_recomputed=False,
            support_pass=True, outcomes_read=True, status="fixed_episode_gate_comparison_not_independent_validation")
        json_file(results/"failure.json", dict(at=wall(4), status="failed_not_evidence"))
        json_file(results/"outcomes_started.json", dict(at=wall(3), context_frozen_sha256=v.sha(results/"context_frozen.json"),
            cached_fixed_episode_accounting_only=True, intrabar_replays=0))
        json_file(results/"outcomes_resumed_1.json", dict(at=wall(6), context_frozen_sha256=v.sha(results/"context_frozen.json"),
            cached_fixed_episode_accounting_only=True, intrabar_replays=0))
    resumed_snapshot = {name: (tmp_path/name).read_bytes() for name in source_names}
    json_file(results/"summary.json", summary)

    def git_show(args, **kwargs):
        commit, name = args[2].split(":", 1)
        return SimpleNamespace(stdout=(original if commit == "original" else resumed_snapshot)[name])

    monkeypatch.setattr(v.subprocess, "run", git_show)
    monkeypatch.setattr(v.subprocess, "check_output", lambda args, **kwargs:
        str(v.stamp(wall(0 if args[-1] == "original" else 5))//v.NS))
    original_verify = v.verify_tables

    def tiny_verify(*args, **kwargs):
        if resumed:
            # This fixture isolates receipt validation; the real support gate
            # is independently exercised by the verify_tables tests above.
            args = (*args[:4], dict(args[4], **f["summary"]))
        return original_verify(*args, **kwargs, expected_counts=(3, 3, 1))

    monkeypatch.setattr(v, "verify_tables", tiny_verify)
    return results


def test_saved_file_source_checkpoint_hashes_without_market_access(tmp_path, monkeypatch):
    results = disk_fixture(tmp_path, monkeypatch)
    output = v.verify(results, root=tmp_path)
    assert output["support_output_hashes_verified"] == 4
    assert output["request_input_hashes_verified"] == 4
    assert output["resumed_accounting"] is False
    assert output["raw_price_archive_read"] is False


@pytest.mark.parametrize("target", ["source", "output", "request", "config", "source_receipt", "freeze_order"])
def test_source_output_or_receipt_mutation_fails(tmp_path, monkeypatch, target):
    results = disk_fixture(tmp_path, monkeypatch)
    if target == "source":
        (tmp_path/"yoyo/evaluation/hourly_impulse_structure_research.py").write_text("changed")
    elif target == "output":
        (results/"counts.csv.gz").write_bytes(b"changed")
    elif target == "request":
        (tmp_path/v.PARENT_PATH/"assignments.csv").write_text("changed")
    elif target == "config":
        (results.parent/"config.json").write_text("{}")
    else:
        path = results/("summary.json" if target == "source_receipt" else "context_frozen.json")
        obj = v.read_json(path)
        if target == "source_receipt":
            obj["source_receipt"]["holdout_price_rows"] = 1
        else:
            obj["at"] = "2026-09-06T11:59:00Z"
        path.write_text(json.dumps(obj))
    with pytest.raises((v.VerificationError, KeyError)):
        v.verify(results, root=tmp_path)


def test_failure_without_legitimate_resume_is_rejected(tmp_path, monkeypatch):
    results = disk_fixture(tmp_path, monkeypatch)
    (results/"failure.json").write_text('{}')
    with pytest.raises(v.VerificationError, match="Failure requires"):
        v.verify(results, root=tmp_path)


def test_serialization_resume_pins_original_and_new_code_separately(tmp_path, monkeypatch):
    results = disk_fixture(tmp_path, monkeypatch, resumed=True)
    output = v.verify(results, root=tmp_path)
    assert output["resumed_accounting"] is True
    assert output["builder_commit"] == "original"
    assert output["resume_builder_commit"] == "resume"


@pytest.mark.parametrize("target", ["feature_recomputed", "resume_hash", "resume_clock", "unpermitted_source"])
def test_resume_cannot_regenerate_feature_or_rewrite_history(tmp_path, monkeypatch, target):
    results = disk_fixture(tmp_path, monkeypatch, resumed=True)
    path = results/"summary.json"
    summary = v.read_json(path)
    if target == "feature_recomputed":
        summary["frozen_features_recomputed"] = True
    elif target == "resume_hash":
        summary["resume_sources"][0]["sha256"] = "b"*64
    elif target == "unpermitted_source":
        row = next(r for r in summary["resume_sources"] if r["path"] == v.PINE)
        row["sha256"] = "b"*64
    else:
        path = results/"outcomes_resumed_1.json"
        receipt = v.read_json(path)
        receipt["at"] = "2026-09-06T12:03:00Z"
        path.write_text(json.dumps(receipt))
        with pytest.raises(v.VerificationError):
            v.verify(results, root=tmp_path)
        return
    path.write_text(json.dumps(summary))
    with pytest.raises(v.VerificationError):
        v.verify(results, root=tmp_path)
