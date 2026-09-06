"""Synthetic saved-hour validation; no market/result data or strategy imports."""
import ast
from copy import deepcopy
from datetime import timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT/"scripts/verify_hourly_impulse_breadth_change_v22.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v = load("v22_saved_verifier", PATH)
# Reuse only the prior synthetic source generator/support-table packer.
old = load("v22_old_synthetic_fixture", Path(__file__).with_name("test_verify_hourly_impulse_breadth_v21.py"))


def fixture(**kwargs):
    context, trace, *_ = old.fixture(**kwargs)
    by_time = {(r["symbol"], v.stamp(r["open_time"])): r for r in trace}
    for row in context:
        for field in list(row):
            if field.startswith("breadth_"):
                del row[field]
        signal = v.stamp(row["signal_time"])
        now_scores, prior_scores, missing = [], [], False
        for symbol in v.SYMBOLS:
            sources = []
            for prefix, time in (("", signal-v.HOUR), ("previous_", signal-2*v.HOUR)):
                source = by_time.get((symbol, time))
                sources.append(source)
                missing |= source is None
                for field in ("score", "count", "available_at", "window_start", "segment_id", "bar_open"):
                    key = {"score": "trscore", "bar_open": "open_time"}.get(field, field)
                    row["breadth_"+symbol+"_"+prefix+field] = source[key] if source else 0 if field == "count" else None
            a, b = sources
            if a and b and a["segment_id"] == b["segment_id"] and a["trscore"] is not None and b["trscore"] is not None:
                now_scores.append(a["trscore"])
                prior_scores.append(b["trscore"])
        known = len(now_scores) == 4
        raw = sum(now_scores)-sum(prior_scores) if known else None
        row.update(breadth_raw_sum_change=raw, breadth_change=raw/200 if known else None,
            breadth_mean_now=sum(now_scores)/200 if known else None,
            breadth_mean_previous=sum(prior_scores)/200 if known else None,
            breadth_score=raw/400 if known else None, breadth_known=known,
            breadth_cutoff=row["signal_time"], breadth_available_at=row["signal_time"] if known else None,
            breadth_source_count=len(now_scores), breadth_gate_state="unknown" if not known else
            "accepted" if row["direction"]*raw > 0 else "abstain",
            breadth_reason=("neutral" if raw == 0 else "known") if known else
            "missing_external_hour" if missing else "insufficient_history")
    parts = old.package(context, trace)
    parts[4]["matching"]["pass_gate"] = parts[4]["matching"].pop("pass")
    return parts


def check(parts):
    return v.verify_tables(*parts, expected_counts=(2, 3, 1))


def test_two_exact_hours_half_scaling_and_fixed_population():
    parts = fixture()
    result = check(parts)
    assert result["count_rows"] == 62
    assert result["matched_groups"] == result["unmatched"] == 1
    assert result["adjacent_change_recomputed"] is True
    assert result["economic_accounting_verified"] is False
    assert result["raw5_aggregation_recomputed"] is False
    for row in parts[0]:
        assert row["breadth_change"] == 2*row["breadth_score"]
        assert row["breadth_raw_sum_change"] == row["breadth_change"]*200
        assert v.stamp(row["breadth_ETHUSDT_available_at"])-v.stamp(row["breadth_ETHUSDT_previous_available_at"]) == v.HOUR


@pytest.mark.parametrize("mirror", [False, True])
def test_extreme_integer_change_admits_matching_direction_without_level_gate(mirror):
    context, trace, *_ = fixture()
    if mirror:
        for row in trace:
            original = row.copy()
            for field in ("open", "close", "hl2"):
                row[field] = 3000-original[field]
            row["high"], row["low"] = 3000-original["low"], 3000-original["high"]
            if row["trscore"] is not None:
                row["trscore"] *= -1  # This synthetic source has no within-asset ties.
        for row in context:
            for field in row:
                if field.startswith("breadth_") and (field.endswith("_score") or field in
                        ("breadth_raw_sum_change", "breadth_change", "breadth_mean_now", "breadth_mean_previous")):
                    if row[field] is not None:
                        row[field] *= -1
    for row in context:
        row["direction"] = 1 if mirror else -1
        row["breadth_gate_state"] = "accepted" if row["direction"]*row["breadth_raw_sum_change"] > 0 else "abstain"
    parts = old.package(context, trace)
    parts[4]["matching"]["pass_gate"] = parts[4]["matching"].pop("pass")
    check(parts)
    edge = next(r for r in context if abs(r["breadth_raw_sum_change"]) == 400)
    assert edge["breadth_gate_state"] == "accepted"
    assert abs(edge["breadth_change"]) == 2 and abs(edge["breadth_score"]) == 1


@pytest.mark.parametrize("mode", ["flat", "neutral"])
def test_level_can_be_nonzero_but_change_exact_zero_is_known_abstention(mode):
    parts = fixture(mode=mode)
    check(parts)
    assert all(r["breadth_known"] and r["breadth_score"] == 0 and r["breadth_gate_state"] == "abstain" for r in parts[0])
    if mode == "flat":
        assert all(r["breadth_mean_now"] == r["breadth_mean_previous"] == 1 for r in parts[0])


@pytest.mark.parametrize("kind", ["only51", "gap", "missing_now", "missing_previous"])
def test_unknown_preserves_both_diagnostics_and_all_denominators(kind):
    t = old.datetime(2023, 1, 1, tzinfo=old.timezone.utc)
    if kind == "only51":
        parts = fixture(start=t-timedelta(hours=51))
    else:
        lag = {"gap": 10, "missing_now": 1, "missing_previous": 2}[kind]
        parts = fixture(gap=("ETHUSDT", t-timedelta(hours=lag)))
    result = check(parts)
    row = parts[0][0]
    assert row["breadth_gate_state"] == "unknown" and row["breadth_source_count"] < 4
    for field in ("raw_sum_change", "mean_now", "mean_previous", "change", "score", "available_at"):
        assert row["breadth_"+field] is None
    assert result["population"]["case"]["total"] == 2


@pytest.mark.parametrize("field,value", [
    ("breadth_raw_sum_change", 2), ("breadth_change", .01), ("breadth_score", .005),
    ("breadth_mean_now", 0), ("breadth_mean_previous", .99),
    ("breadth_ETHUSDT_score", 48), ("breadth_ETHUSDT_previous_score", -50),
    ("breadth_ETHUSDT_count", 51), ("breadth_ETHUSDT_previous_count", 0),
    ("breadth_ETHUSDT_segment_id", 7), ("breadth_ETHUSDT_previous_segment_id", 7),
    ("breadth_ETHUSDT_bar_open", "2023-01-01T00:00:00Z"),
    ("breadth_ETHUSDT_previous_bar_open", "2022-12-31T23:00:00Z"),
    ("breadth_ETHUSDT_previous_available_at", "2023-01-01T00:00:00Z"),
    ("breadth_ETHUSDT_previous_window_start", "2022-12-29T22:00:00.000000001Z"),
    ("breadth_source_count", 3), ("breadth_cutoff", "2023-01-01T01:00:00Z"),
    ("breadth_known", False), ("breadth_gate_state", "accepted"),
    ("breadth_reason", "known"), ("breadth_available_at", None),
])
def test_every_change_diagnostic_is_independently_validated(field, value):
    parts = fixture(mode="flat")
    parts[0][0][field] = value
    with pytest.raises(v.VerificationError):
        check(parts)


def test_tiny_float_residual_must_not_turn_integer_zero_into_signal():
    parts = fixture(mode="flat")
    parts[0][0]["breadth_score"] = 1e-16
    with pytest.raises(v.VerificationError, match="zero"):
        check(parts)


@pytest.mark.parametrize("field,value", [("trscore", 48), ("hl2", 9999), ("count", 1),
    ("segment_id", 9), ("available_at", "2023-01-01T00:00:00Z"),
    ("window_start", "2023-01-01T00:00:00Z"), ("volume", "nan"), ("low", 999999)])
def test_rank_and_clock_rebuilt_from_hourly_ohlc_not_saved_claims(field, value):
    parts = fixture()
    parts[1][70][field] = value
    with pytest.raises(v.VerificationError):
        check(parts)


@pytest.mark.parametrize("kind", ["duplicate", "missing_count", "wrong_parent", "wrong_direction", "reused_control", "pairs", "summary", "outcomes", "structure"])
def test_support_identity_and_no_outcome_leakage_fail_closed(kind):
    parts = fixture()
    if kind == "duplicate": parts[0].append(deepcopy(parts[0][0]))
    elif kind == "missing_count": parts[2].pop()
    elif kind == "wrong_parent": parts[0][-1]["parent_event_id"] = "case_b"
    elif kind == "wrong_direction": parts[0][-1]["direction"] = -1
    elif kind == "reused_control":
        parts[0][-1] = deepcopy(parts[0][-2])
        parts[0][-1]["event_id"] = "control_c"
    elif kind == "pairs": parts[3][0]["control_ids"] = "control_a|control_b|fake"
    elif kind == "summary": parts[4]["support_values"]["events"] += 1
    elif kind == "outcomes": parts[4]["outcomes_read"] = True
    else: parts[0][0]["structure_gate_state"] = "accepted"
    with pytest.raises(v.VerificationError):
        check(parts)


def test_later_saved_suffix_does_not_change_earlier_request():
    context, trace, *_ = fixture()
    first = [deepcopy(context[0])]
    cutoff = v.stamp(first[0]["signal_time"])
    prefix = [r for r in trace if v.stamp(r["available_at"]) <= cutoff]
    v.context_facts(first, prefix)
    v.context_facts(context, trace)
    assert first[0] == context[0]


def test_config_exact_change_and_unchanged_execution_without_importing_runner():
    config = json.loads((ROOT/v.EXPERIMENT_PATH/"config.json").read_text())
    v.verify_config(config)
    for field, value in (("rank_length", 40), ("change_hours", 5), ("absolute_mean_alignment", True),
                         ("change_hours", True), ("absolute_mean_alignment", 0),
                         ("bookkeeping_score", "raw_divided200"), ("zero", "accepted"),
                         ("forward_fill", True), ("universe", ["BTCUSDT", *v.SYMBOLS[1:]])):
        altered = deepcopy(config)
        altered["gate"][field] = value
        with pytest.raises(v.VerificationError):
            v.verify_config(altered)
    altered = deepcopy(config)
    altered["fixed_execution"]["cost_fraction"] = .003
    with pytest.raises(v.VerificationError):
        v.verify_config(altered)


def test_dependency_sha_required_before_import(tmp_path):
    file = tmp_path/"helpers.py"
    with pytest.raises(ValueError, match="dependency"):
        v.load_helpers(file)
    file.write_text("raise AssertionError('untrusted code must not execute')")
    with pytest.raises(ValueError, match="dependency"):
        v.load_helpers(file)


def test_no_strategy_or_dataframe_imports_in_dependency_closure():
    for path in (PATH, ROOT/"scripts/verify_hourly_impulse_breadth_v21.py"):
        tree = ast.parse(path.read_text())
        modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m and m.startswith(("yoyo", "pandas", "numpy", "scipy")) for m in modules)


def test_missing_results_and_failed_run_do_not_emit_success_receipt(tmp_path, capsys):
    out = tmp_path/"verification.json"
    assert v.main(["--root", str(tmp_path), "--out", str(out)]) == 1
    assert not out.exists()
    folder = tmp_path/v.EXPERIMENT_PATH/"results"
    folder.mkdir(parents=True)
    (folder/"failure.json").write_text("{}")
    assert v.main(["--root", str(tmp_path)]) == 1
    assert "Failed run" in capsys.readouterr().out


def test_explicit_receipt_create_only(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(v, "verify", lambda *a, **kw: {"status": "passed"})
    out = tmp_path/"receipt.json"
    assert v.main(["--root", str(tmp_path), "--out", str(out)]) == 0
    original = out.read_bytes()
    assert v.main(["--root", str(tmp_path), "--out", str(out)]) == 1
    assert out.read_bytes() == original


def source_fixture(monkeypatch):
    names = sorted(v.SOURCE_FILES | {v.EXPERIMENT_PATH+"/config.json", v.EXPERIMENT_PATH+"/PROJECT_PLAN.md", v.BASE})
    content = {name: ("synthetic "+name).encode() for name in names}
    pins = [{"path": n, "sha256": hashlib.sha256(b).hexdigest()} for n, b in content.items()]
    started = dict(sources=pins, builder_commit="a"*40, at="2026-09-07T00:00:00Z")
    summary = dict(sources=deepcopy(pins), config_sha256=hashlib.sha256(content[v.EXPERIMENT_PATH+"/config.json"]).hexdigest())
    config = dict(base_config_sha256=hashlib.sha256(content[v.BASE]).hexdigest())
    monkeypatch.setattr(v, "PINE_SHA", hashlib.sha256(content[v.PINE]).hexdigest())
    return content, started, summary, config


@pytest.mark.parametrize("failure", [None, "missing", "bytes", "unavailable", "late_commit"])
def test_committed_sources_not_current_worktree(tmp_path, monkeypatch, failure):
    content, started, summary, config = source_fixture(monkeypatch)
    if failure == "missing":
        started["sources"].pop()
        summary["sources"] = deepcopy(started["sources"])
    def run(command, **kwargs):
        if failure == "unavailable": raise subprocess.CalledProcessError(128, command)
        if command[2] == "-s":
            return subprocess.CompletedProcess(command, 0, stdout="99999999999" if failure == "late_commit" else "1")
        data = content[command[2].split(":", 1)[1]]
        return subprocess.CompletedProcess(command, 0, stdout=data+b"changed" if failure == "bytes" else data)
    monkeypatch.setattr(v.subprocess, "run", run)
    if failure:
        with pytest.raises((v.VerificationError, subprocess.CalledProcessError)):
            v.verify_sources(tmp_path, started, summary, config)
    else:
        assert v.verify_sources(tmp_path, started, summary, config) == len(content)


def receipt_fixture(tmp_path):
    for name in v.CSV_NAMES:
        (tmp_path/name).write_bytes(("synthetic "+name).encode())
    hashes = {name: v.sha(tmp_path/name) for name in v.CSV_NAMES}
    started = dict(at="2026-09-07T00:00:00Z")
    frozen = dict(at="2026-09-07T00:00:01Z", requests=713, outcomes_read=False, output_hashes=hashes.copy())
    summary = dict(generated_at="2026-09-07T00:00:03Z", output_hashes=hashes.copy())
    (tmp_path/"context_frozen.json").write_text(json.dumps(frozen))
    return started, frozen, summary


@pytest.mark.parametrize("mutation", [None, "missing_hash", "extra_file", "changed_bytes", "checkpoint_hash", "count", "pre_read", "future_freeze", "early_freeze"])
def test_output_hash_coverage_and_freeze_before_outcomes(tmp_path, mutation):
    started, frozen, summary = receipt_fixture(tmp_path)
    if mutation == "missing_hash": summary["output_hashes"].pop("counts.csv.gz")
    elif mutation == "extra_file": (tmp_path/"undeclared.csv.gz").write_bytes(b"x")
    elif mutation == "changed_bytes": (tmp_path/"entry_context.csv.gz").write_bytes(b"changed")
    elif mutation == "checkpoint_hash": frozen["output_hashes"]["counts.csv.gz"] = "0"*64
    elif mutation == "count": frozen["requests"] = 712
    elif mutation == "pre_read": frozen["outcomes_read"] = True
    elif mutation == "future_freeze": frozen["at"] = "2026-09-07T00:00:03.000000001Z"
    elif mutation == "early_freeze": frozen["at"] = "2026-09-06T23:59:59.999999999Z"
    if mutation:
        with pytest.raises(v.VerificationError):
            v.verify_output_receipts(tmp_path, started, frozen, summary)
    else:
        assert v.verify_output_receipts(tmp_path, started, frozen, summary) == summary["output_hashes"]


@pytest.mark.parametrize("mutation", [None, "marker", "economics", "outcome_csv"])
def test_insufficient_support_never_silently_accepts_outcome_access(tmp_path, mutation):
    _, frozen, summary = receipt_fixture(tmp_path)
    hashes = summary["output_hashes"]
    if mutation == "marker": (tmp_path/"outcomes_started.json").write_text("{}")
    elif mutation == "economics": summary["economics"] = {}
    elif mutation == "outcome_csv": hashes["case_delta.csv.gz"] = "0"*64
    if mutation:
        with pytest.raises(v.VerificationError):
            v.verify_outcome_access(tmp_path, frozen, summary, False, hashes)
    else:
        v.verify_outcome_access(tmp_path, frozen, summary, False, hashes)


@pytest.mark.parametrize("mutation", [None, "missing", "before", "after", "hash", "replay"])
def test_supported_outcome_marker_exact_clock_and_checkpoint(tmp_path, mutation):
    _, frozen, summary = receipt_fixture(tmp_path)
    access = dict(at="2026-09-07T00:00:02Z", frozen_context_sha256=v.sha(tmp_path/"context_frozen.json"), new_intrabar_replays=0)
    if mutation == "before": access["at"] = "2026-09-07T00:00:00.999999999Z"
    elif mutation == "after": access["at"] = "2026-09-07T00:00:03.000000001Z"
    elif mutation == "hash": access["frozen_context_sha256"] = "0"*64
    elif mutation == "replay": access["new_intrabar_replays"] = 1
    if mutation != "missing":
        (tmp_path/"outcomes_started.json").write_text(json.dumps(access))
    if mutation:
        with pytest.raises(v.VerificationError):
            v.verify_outcome_access(tmp_path, frozen, summary, True, summary["output_hashes"])
    else:
        v.verify_outcome_access(tmp_path, frozen, summary, True, summary["output_hashes"])


def input_fixture(tmp_path, monkeypatch):
    folder = tmp_path/v.TRACE_PARENT
    folder.mkdir(parents=True)
    trace_path = folder/"external_hourly_trace.csv.gz"
    trace_path.write_bytes(b"synthetic trace placeholder; reader mocked in this input-receipt unit test")
    trace_sha = v.sha(trace_path)
    prior = dict(at="2026-09-06T00:00:00Z", requests=713, outcomes_read=False,
                 output_hashes={trace_path.name: trace_sha})
    (folder/"context_frozen.json").write_text(json.dumps(prior))
    freeze_sha = v.sha(folder/"context_frozen.json")
    monkeypatch.setattr(v, "TRACE_SHA", trace_sha)
    monkeypatch.setattr(v, "FREEZE_SHA", freeze_sha)
    receipt = dict(path=v.TRACE_PARENT+"/"+trace_path.name, sha256=trace_sha,
        parent_freeze_sha256=freeze_sha, saved_hour_rows=70168, first_hour="2023-01-01T00:00:00Z",
        last_hour="2023-01-01T00:00:00Z", raw5_prices_read=False, prices_2025_plus_materialized=0, new_intrabar_replays=0)
    frozen = dict(at="2026-09-07T00:00:00Z", input_receipt=deepcopy(receipt))
    summary = dict(input_receipt=receipt)
    calls = []
    def read(path):
        assert path == trace_path
        calls.append(path)
        # Shape/rank reconstruction is covered separately; this unit tests
        # the saved input byte/receipt gate BEFORE any CSV materialization.
        return [{"open_time": "2023-01-01T00:00:00Z"}]*70168
    monkeypatch.setattr(v, "read_csv", read)
    return folder, frozen, summary, calls


@pytest.mark.parametrize("mutation", [None, "trace_bytes", "freeze_bytes", "receipt", "rawread", "laterprice", "rows", "parent_after_child"])
def test_pinned_input_receipts_fail_before_csv_materialization(tmp_path, monkeypatch, mutation):
    folder, frozen, summary, calls = input_fixture(tmp_path, monkeypatch)
    if mutation == "trace_bytes": (folder/"external_hourly_trace.csv.gz").write_bytes(b"changed")
    elif mutation == "freeze_bytes": (folder/"context_frozen.json").write_text("{}")
    elif mutation == "receipt": summary["input_receipt"]["sha256"] = "0"*64
    elif mutation in ("rawread", "laterprice", "rows"):
        key, value = {"rawread": ("raw5_prices_read", True), "laterprice": ("prices_2025_plus_materialized", 1), "rows": ("saved_hour_rows", 1)}[mutation]
        summary["input_receipt"][key] = value
        frozen["input_receipt"][key] = value
    elif mutation == "parent_after_child": frozen["at"] = "2026-09-05T00:00:00Z"
    if mutation:
        with pytest.raises(v.VerificationError):
            v.verify_input(tmp_path, frozen, summary)
        assert not calls
    else:
        assert len(v.verify_input(tmp_path, frozen, summary)) == 70168
        assert len(calls) == 1
