"""Synthetic only: no archive prices, historical trades, or strategy imports."""
import ast
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


PATH = Path(__file__).resolve().parents[1]/"scripts/verify_hourly_impulse_breadth_v21.py"
SPEC = importlib.util.spec_from_file_location("breadth_saved_verifier", PATH)
v = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(v)


def iso(time):
    return time.isoformat()


def fixture(*, mode="flip", gap=None, start=None):
    """Explicit small cases and own controls; no real row identities or quotes."""
    start = start or datetime(2022, 12, 29, tzinfo=timezone.utc)
    cutoff = datetime(2023, 1, 1, 8, tzinfo=timezone.utc)
    trace = []
    look = {}
    for asset_index, symbol in enumerate(v.SYMBOLS):
        segment, history, count, previous = -1, [], 0, None
        t = start
        while t < cutoff:
            if gap == (symbol, t):
                t += timedelta(hours=1)
                continue
            if previous is None or t-previous != timedelta(hours=1):
                segment += 1
                count, history = 0, []
            i = int((t-datetime(2022, 12, 29, tzinfo=timezone.utc)).total_seconds()/3600)
            if mode == "neutral":
                price = 1000+i if asset_index < 2 else 1000-i
            elif mode == "flat":
                price = 1000
            else:
                price = 1000+i if i < 75 else 500-(i-75)
            history.append(float(price))
            count += 1
            score = sum(1 if price >= old else -1 for old in history[-51:-1]) if count >= 51 else None
            row = dict(symbol=symbol, open_time=iso(t), open=price, high=price+1,
                low=price-1, close=price, volume=100, hl2=price, trscore=score,
                count=count, segment_id=segment, available_at=iso(t+timedelta(hours=1)),
                window_start=iso(t-timedelta(hours=50)) if score is not None else None)
            trace.append(row)
            look[symbol, t] = row
            previous = t
            t += timedelta(hours=1)
    base = datetime(2023, 1, 1, tzinfo=timezone.utc)
    identities = [("case_a", "case", 0, 1), ("case_b", "case", 8, -1),
                  ("control_a", "control", 1, 1), ("control_b", "control", 2, 1), ("control_c", "control", 4, 1)]
    context = []
    for event_id, population, hours, direction in identities:
        t = base+timedelta(hours=hours)
        row = dict(event_id=event_id, population=population, fold="2023H1", signal_time=iso(t),
                   decision_time=iso(t+timedelta(hours=1)), direction=direction,
                   parent_event_id="case_a" if population == "control" else "")
        scores, missing = [], False
        for symbol in v.SYMBOLS:
            source = look.get((symbol, t-timedelta(hours=1)))
            missing |= source is None
            for field in ("score", "count", "available_at", "window_start"):
                value = source["trscore" if field == "score" else field] if source else 0 if field == "count" else None
                row[f"breadth_{symbol}_{field}"] = value
            if source and source["trscore"] is not None:
                scores.append(source["trscore"])
        known = len(scores) == 4
        score = sum(scores)/200 if known else None
        row.update(breadth_known=known, breadth_score=score, breadth_cutoff=iso(t),
            breadth_available_at=iso(t) if known else None, breadth_source_count=len(scores),
            breadth_reason=("neutral" if score == 0 else "known") if known else "missing_external_hour" if missing else "insufficient_history",
            breadth_gate_state="unknown" if not known else "accepted" if direction*score > 0 else "abstain")
        context.append(row)
    return package(context, trace)


def package(context, trace):
    states = ("accepted", "abstain", "unknown")
    def counts(rows):
        c = Counter(r["breadth_gate_state"] for r in rows)
        return dict(total=len(rows), **{s: c[s] for s in states})
    parts = {p: [r for r in context if r["population"] == p] for p in ("case", "control")}
    rows = []
    dimensions = {"all": ["all"], "fold": list(v.FOLDS), "direction": ["1", "-1"],
                  "month": [f"{year}-{mo:02d}" for year in (2023, 2024) for mo in range(1, 13)]}
    for pop, part in parts.items():
        for dimension, keys in dimensions.items():
            for key in keys:
                subset = [r for r in part if dimension == "all" or (str(r["direction"]) if dimension == "direction" else
                          r["decision_time"][:7] if dimension == "month" else r["fold"]) == key]
                c = counts(subset)
                rows.append(dict(population=pop, dimension=dimension, key=key, **c,
                                 accepted_rate=c["accepted"]/c["total"] if c["total"] else None))
    a = context[0]
    c = counts(parts["control"])
    matched = [dict(event_id="case_a", fold="2023H1", case_state=a["breadth_gate_state"],
        control_ids="control_a|control_b|control_c", all_known=a["breadth_gate_state"] != "unknown" and c["unknown"] == 0,
        **{"control_"+key: value for key, value in c.items()})]
    admitted = [r for r in parts["case"] if r["breadth_gate_state"] == "accepted"]
    months = {r["decision_time"][:7] for r in admitted}
    summary = dict(population={p: counts(part) for p, part in parts.items()},
        support_values=dict(events=len(admitted), minimum_fold_events=0, active_months=len(months), minimum_fold_months=0),
        support_gates={key: False for key in v.SUPPORT}, support_pass=False,
        matching=dict(assigned=1, unassigned=1, coverage=.5, required=.9, **{"pass": False}),
        outcomes_read=False, status="insufficient_support_no_outcomes", new_intrabar_replays=0,
        **{key: False for key in ("holdout_consumed", "independent_validation", "training_eligible", "production_eligible", "overall_goal_achieved")})
    return context, trace, rows, matched, summary


def verify(parts):
    return v.verify_tables(*parts, expected_counts=(2, 3, 1))


def test_full_arithmetic_own_controls_and_denominators():
    parts = fixture()
    result = verify(parts)
    assert result["population"] == {"case": {"total": 2, "accepted": 2, "abstain": 0, "unknown": 0},
                                     "control": {"total": 3, "accepted": 2, "abstain": 1, "unknown": 0}}
    assert result["count_rows"] == 62
    assert result["matched_groups"] == result["unmatched"] == 1
    assert result["saved_hourly_scores_recomputed"] is True
    assert result["raw5_aggregation_recomputed"] is False
    assert result["economic_accounting_verified"] is False


def test_ties_preserve_source_bullish_score_not_zero():
    parts = fixture(mode="flat")
    verify(parts)
    assert parts[0][0]["breadth_score"] == 1
    assert parts[0][1]["breadth_gate_state"] == "abstain"


def test_true_zero_is_known_abstention():
    parts = fixture(mode="neutral")
    verify(parts)
    assert all(r["breadth_known"] and r["breadth_reason"] == "neutral" and r["breadth_gate_state"] == "abstain" for r in parts[0])


@pytest.mark.parametrize("kind", ["warmup", "gap", "missing_exact"])
def test_unknown_support_does_not_skip_or_fill(kind):
    t = datetime(2023, 1, 1, tzinfo=timezone.utc)
    if kind == "warmup":
        parts = fixture(start=t-timedelta(hours=50))
    else:
        parts = fixture(gap=("ETHUSDT", t-timedelta(hours=1 if kind == "missing_exact" else 3)))
    result = verify(parts)
    first = parts[0][0]
    assert first["breadth_gate_state"] == "unknown" and first["breadth_score"] is None
    assert first["breadth_available_at"] is None
    assert result["population"]["case"]["unknown"] >= 1
    assert first["breadth_reason"] == ("missing_external_hour" if kind == "missing_exact" else "insufficient_history")


@pytest.mark.parametrize("field,value", [("trscore", 48), ("hl2", 9999), ("count", 1),
    ("window_start", "2023-01-01T00:00:00Z"), ("segment_id", 9), ("available_at", "2023-01-01T00:00:00Z")])
def test_trace_diagnostic_tampering_rejected(field, value):
    parts = fixture()
    parts[1][70][field] = value
    with pytest.raises(v.VerificationError):
        verify(parts)


@pytest.mark.parametrize("change", ["score", "direction", "clock", "mean", "zero_unknown", "source_count", "structure"])
def test_context_gate_and_identity_failures(change):
    parts = fixture(mode="neutral" if change == "zero_unknown" else "flip")
    row = parts[0][0]
    if change == "score": row["breadth_ETHUSDT_score"] = 48
    elif change == "direction": parts[0][-1]["direction"] = -1
    elif change == "clock": row["breadth_cutoff"] = row["decision_time"]
    elif change == "mean": row["breadth_score"] = 50
    elif change == "zero_unknown": row["breadth_known"] = False
    elif change == "source_count": row["breadth_source_count"] = 3
    else: row["structure_gate_state"] = "accepted"
    with pytest.raises(v.VerificationError):
        verify(parts)


@pytest.mark.parametrize("change", ["duplicate", "wrong_asset", "order", "future", "ohlc", "nonfinite", "timezone"])
def test_saved_source_rejects_bad_clock_or_values(change):
    parts = fixture()
    trace = parts[1]
    if change == "duplicate": trace.insert(1, deepcopy(trace[0]))
    elif change == "wrong_asset": trace[0]["symbol"] = "BTCUSDT"
    elif change == "order": trace[1], trace[2] = trace[2], trace[1]
    elif change == "future": trace[-1]["open_time"] = "2023-01-01T08:00:00Z"
    elif change == "ohlc": trace[0]["low"] = trace[0]["high"]+1
    elif change == "nonfinite": trace[0]["volume"] = "inf"
    else: trace[0]["open_time"] = "2022-12-29 00:00:00"
    with pytest.raises(v.VerificationError):
        verify(parts)


@pytest.mark.parametrize("change", ["omitted_month", "duplicate_month", "counts", "matched_ids", "partial_group", "summary", "outcomes", "missing_summary"])
def test_population_counts_and_support_are_complete(change):
    parts = list(fixture())
    if change == "omitted_month": parts[2].pop()
    elif change == "duplicate_month": parts[2].append(deepcopy(parts[2][0]))
    elif change == "counts": parts[2][0]["total"] = 1
    elif change == "matched_ids": parts[3][0]["control_ids"] = "control_a|control_b|other"
    elif change == "partial_group": parts[0].pop()
    elif change == "summary": parts[4]["support_values"]["events"] += 1
    elif change == "outcomes": parts[4]["outcomes_read"] = True
    else: parts[4] = None
    with pytest.raises(v.VerificationError):
        verify(parts)


def test_clock_parity_strings_and_nanos():
    old = [dict(event_id="e", known_5m_available="2023-01-01T00:00:00Z", value="1.0")]
    new = [dict(event_id="e", known_5m_available="2023-01-01 08:00:00+08:00", value="1.0000000000000002")]
    v.parity(old, new)
    new[0]["known_5m_available"] = "2023-01-01T00:00:00.000000001Z"
    with pytest.raises(v.VerificationError, match="clock"):
        v.parity(old, new)


def test_prefix_scores_not_revised_by_later_saved_prices():
    context, trace, *_ = fixture()
    cutoff = v.stamp(context[-1]["signal_time"])
    early = [row for row in trace if v.stamp(row["available_at"]) <= cutoff]
    before = v.rebuild_trace(early, cutoff)
    later = v.rebuild_trace(trace, max(v.stamp(r["signal_time"]) for r in context))
    for symbol in v.SYMBOLS:
        assert before[symbol] == {k: value for k, value in later[symbol].items() if k < cutoff}


def test_reader_refuses_outcome_header_before_rows(tmp_path):
    p = tmp_path/"bad.csv"
    p.write_text("event_id,net_return\nsecret,should_not_parse\n")
    with pytest.raises(v.VerificationError, match="Outcome"):
        v.read_csv(p)
    p.write_text("event_id,event_id\na,b\n")
    with pytest.raises(v.VerificationError, match="header"):
        v.read_csv(p)


def test_json_duplicates_and_unsafe_identity(tmp_path):
    p = tmp_path/"bad.json"
    p.write_text('{"x":1,"x":2}')
    with pytest.raises(v.VerificationError, match="Duplicate"):
        v.read_json(p)
    for identity in ("../other", "/data/file", "data//file", "data/./file"):
        with pytest.raises(v.VerificationError):
            v.safe_path(tmp_path, identity)


def test_cli_missing_summary_fails_without_output(tmp_path, capsys):
    results = tmp_path/v.EXPERIMENT_PATH/"results"
    results.mkdir(parents=True)
    output = tmp_path/"independent.json"
    assert v.main(["--root", str(tmp_path), "--out", str(output)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
    assert not output.exists()


def test_cli_explicit_receipt_success_does_not_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(v, "verify", lambda *args, **kwargs: {"status": "passed"})
    output = tmp_path/"nested/receipt.json"
    assert v.main(["--root", str(tmp_path), "--out", str(output)]) == 0
    assert json.loads(output.read_text()) == {"status": "passed"}
    with pytest.raises(v.VerificationError, match="Preserve"):
        v.main(["--root", str(tmp_path), "--out", str(output)])


def test_stdlib_standalone_no_transitive_strategy_or_previous_verifier_dependency():
    tree = ast.parse(PATH.read_text())
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    modules |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not any(m and (m.startswith(("yoyo", "pandas", "numpy", "scipy")) or "verify_hourly" in m) for m in modules)
    assert "importlib" not in modules


def source_fixture(monkeypatch):
    names = sorted(v.SOURCE_FILES | {v.EXPERIMENT_PATH+"/config.json", v.EXPERIMENT_PATH+"/PROJECT_PLAN.md", v.BASE})
    content = {name: ("synthetic "+name).encode() for name in names}
    pins = [{"path": name, "sha256": hashlib.sha256(content[name]).hexdigest()} for name in names]
    started = dict(sources=pins, builder_commit="a"*40, at="2026-09-06T00:00:00Z")
    summary = dict(sources=deepcopy(pins), config_sha256=hashlib.sha256(content[v.EXPERIMENT_PATH+"/config.json"]).hexdigest())
    config = dict(base_config_sha256=hashlib.sha256(content[v.BASE]).hexdigest())
    monkeypatch.setattr(v, "PINE_SHA", hashlib.sha256(content[v.PINE]).hexdigest())
    return content, started, summary, config


def test_sources_retrieved_from_builder_not_current_worktree(tmp_path, monkeypatch):
    content, started, summary, config = source_fixture(monkeypatch)
    called = []
    def run(command, **kwargs):
        called.append(command)
        if command[2] == "-s":
            return subprocess.CompletedProcess(command, 0, stdout="1\n")
        assert command[2].startswith(started["builder_commit"]+":")
        return subprocess.CompletedProcess(command, 0, stdout=content[command[2].split(":", 1)[1]])
    monkeypatch.setattr(v.subprocess, "run", run)
    assert v.verify_sources(tmp_path, started, summary, config) == len(content)
    assert len(called) == len(content)+1


@pytest.mark.parametrize("failure", ["missing_source", "byte_drift", "unavailable_commit", "started_before_commit"])
def test_source_pin_failure_never_skipped(tmp_path, monkeypatch, failure):
    content, started, summary, config = source_fixture(monkeypatch)
    if failure == "missing_source":
        started["sources"].pop()
        summary["sources"] = deepcopy(started["sources"])
    def run(command, **kwargs):
        if failure == "unavailable_commit":
            raise subprocess.CalledProcessError(128, command)
        if command[2] == "-s":
            return subprocess.CompletedProcess(command, 0, stdout="99999999999" if failure == "started_before_commit" else "1")
        value = content[command[2].split(":", 1)[1]]
        return subprocess.CompletedProcess(command, 0, stdout=value+b"changed" if failure == "byte_drift" else value)
    monkeypatch.setattr(v.subprocess, "run", run)
    with pytest.raises(v.VerificationError):
        v.verify_sources(tmp_path, started, summary, config)


def test_config_single_variable_checks_without_strategy_import():
    # Static JSON is a design contract, not a market/outcome fixture.
    path = PATH.parents[1]/v.EXPERIMENT_PATH/"config.json"
    config = json.loads(path.read_text())
    v.verify_config(config)
    for field, value in (("rank_length", 40), ("cutoff", "own_K1_close"), ("forward_fill", True),
                          ("universe", ["BTCUSDT", *v.SYMBOLS[1:]]), ("extra_structure_ma_volume_gate", True)):
        changed = deepcopy(config)
        changed["gate"][field] = value
        with pytest.raises(v.VerificationError):
            v.verify_config(changed)
