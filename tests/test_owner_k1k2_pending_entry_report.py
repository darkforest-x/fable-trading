"""V38 report-only contracts on synthetic saved CSVs in pytest tmp directories.

No real market, outcome, experiment output or git object is read. Git freeze
checks use an in-memory synthetic snapshot; notebook cells execute for real in
sequence and their stdout is captured. This is not Jupyter-kernel certification.
"""
import copy
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import owner_k1k2_pending_entry_report as m


E = pd.Timestamp("2023-03-03T12:00:00Z")


def synthetic_tables():
    cases = pd.DataFrame([dict(event_id="case" + str(i), decision_time=E, direction=1,
        initial_stop=95., signal_atr=2., fold="2023H1", gap_bars=3, ma=99., body_ratio=.7,
        audit_cutoff=E + pd.Timedelta(hours=1), cohort="case") for i in range(63)])
    controls = pd.concat([cases.iloc[:36].copy() for _ in range(3)], ignore_index=True)
    controls["parent_event_id"] = controls.event_id
    controls["event_id"] = ["control" + str(i) for i in range(108)]
    controls["cohort"] = "control"
    requests = pd.concat([cases, controls], ignore_index=True)
    assignments = cases[["event_id", "decision_time", "fold"]].copy()
    assignments["match_status"] = ["matched"] * 36 + ["no_match"] * 27
    events = requests.copy()
    statuses = ["eligible", "eligible", "invalidated_open", "invalidated_wait_bar",
                "unknown_raw", "unknown_management", "pending_at_cutoff"]
    waits = [0, 2, 0, 1, 1, 2, 12]
    events["status"] = [statuses[i % 7] for i in range(171)]
    events["waiting_bars"] = [waits[i % 7] for i in range(171)]
    events["status_reason"] = "synthetic"
    events["seed_state"] = np.where(events.waiting_bars.eq(0), "aligned", "opposite")
    events["status_known_at"] = events.decision_time + pd.to_timedelta(events.waiting_bars * 5, unit="min")
    events["eligible_at"] = events.status_known_at.where(events.status.eq("eligible"))
    for name, value in [("reference_open", 100.), ("risk_pct", .05), ("risk_atr", 2.5)]:
        events[name] = np.where(events.status.eq("eligible"), value, np.nan)
    events["invalidated_interval_start"] = (events.status_known_at - pd.Timedelta(minutes=5)).where(events.status.eq("invalidated_wait_bar"))
    trace = pd.DataFrame([dict(event_id=row.event_id, observed_at=t,
        completed_bar_open=t - pd.Timedelta(minutes=5), state="opposite")
        for row in events.itertuples() for t in pd.date_range(row.decision_time, row.status_known_at, freq="5min")])
    proof = events[["event_id", "cohort", "status_known_at"]].rename(columns={"status_known_at": "terminal"})
    proof = proof.assign(prefix_rows=41, events_equal=True, trace_equal=True, current_hlc_masked=True)
    with sqlite3.connect(":memory:") as con:
        events.to_sql("audit_events", con, index=False)
        counts = pd.read_sql_query(m.STATUS_SQL, con)
    frames = dict(requests=requests, assignments=assignments, events=events, trace=trace,
                  prefix_checks=proof, status_counts=counts)
    for name, keys in [("fold_counts", ["fold", "cohort", "status"]), ("seed_counts", ["cohort", "seed_state", "status"])]:
        frames[name] = events.groupby(keys).size().rename("events").reset_index()
    return frames


@pytest.fixture
def tree(tmp_path, monkeypatch):
    here = tmp_path / m.REL
    here.mkdir(parents=True)
    (tmp_path / m.DATA).mkdir(parents=True)
    frames = synthetic_tables()
    config = dict(output_dir=str(m.DATA), audit_only=True, economic_outcomes=False,
                  entry_expiry=None, audit_minutes=60, builder_paths=sorted(m.AUDIT_SOURCES))
    sources = list(m.AUDIT_SOURCES) + [m.BUILDER, m.TESTS, str(m.REL / "PROJECT_PLAN.md")]
    for name in sources:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Synthetic frozen source\n")
    (here / "config.json").write_text(json.dumps(config))
    sources.append(str(m.REL / "config.json"))
    frozen = {name: (tmp_path / name).read_bytes() for name in sources}
    files = {}
    for name, frame in frames.items():
        path = tmp_path / m.DATA / (name + ".csv")
        frame.to_csv(path, index=False, float_format="%.17g")
        files[str(path.relative_to(tmp_path))] = dict(sha256=m.sha(path), rows=len(frame), columns=list(frame))
    summary = dict(source_commit="synthetic-audit", config_sha256=m.sha(here / "config.json"),
        status="clock_audit_complete_not_economic", requests=171, cases=63, controls=108,
        original_matched_cases=36, original_unmatched_cases=27, administrative_observation_minutes=60,
        entry_expiry=None, holdout_evaluated=False, training_eligible=False, production_eligible=False,
        economics_materialized=False, observed_live_delivery_verified=False,
        counts=frames["status_counts"].to_dict("records"), trace_rows=len(frames["trace"]),
        fold_counts=frames["fold_counts"].to_dict("records"), seed_counts=frames["seed_counts"].to_dict("records"),
        prefix_checks=dict(events=171, all_passed=True), sql=m.STATUS_SQL, files=files,
        negative_control=dict(synthetic_cases=1, correct_unchanged=True, deliberately_wrong_future_gate_changed=True))
    (here / "summary.json").write_text(json.dumps(summary))
    def git(args, **kwargs):
        if args[1] == "rev-parse":
            return "synthetic-report\n"
        return frozen[args[2].split(":", 1)[1]]
    monkeypatch.setattr(m, "ROOT", tmp_path)
    monkeypatch.setattr(m, "HERE", here)
    monkeypatch.setattr(m.subprocess, "check_output", git)
    return dict(root=tmp_path, here=here, frames=frames, summary=summary, config=config, frozen=frozen)


def update_csv(tree, name, frame):
    path = tree["root"] / m.DATA / (name + ".csv")
    frame.to_csv(path, index=False, float_format="%.17g")
    tree["summary"]["files"][str(path.relative_to(tree["root"]))] = dict(sha256=m.sha(path), rows=len(frame), columns=list(frame))
    (tree["here"] / "summary.json").write_text(json.dumps(tree["summary"]))


def add_narrative(tree):
    narrative = ("# " + m.TITLE + "\n\n## Executive Summary\n\nOnly synthetic clock support, not returns.\n\n"
                 "## 原始请求与分母\n\nAll 63 cases and 108 controls remain.\n\n"
                 "## 等待后的状态\n\nRead each share against its original cohort. Unknown and pending are not cash zero.\n\n"
                 "## 下一步与开放问题\n\nFreeze a separate economic policy before scoring.\n\n"
                 "## 风险与假设\n\nPrefix paths share the SMA implementation; opaque segment labels are normalized.\n")
    path = tree["root"] / m.REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(narrative)
    tree["frozen"][m.REPORT] = path.read_bytes()
    return path


def test_guard_reads_only_exact_eight_saved_tables_and_preserves_inputs(tree, monkeypatch):
    calls = []
    read_csv = pd.read_csv
    def tracked(path, **kwargs):
        calls.append((Path(path), kwargs))
        return read_csv(path, **kwargs)
    monkeypatch.setattr(m.pd, "read_csv", tracked)
    commit, summary, frames, hashes = m.guard()
    assert commit == "synthetic-report" and len(frames["events"]) == 171
    assert {path for path, _ in calls} == {tree["root"] / m.DATA / (name + ".csv") for name in m.TABLES}
    assert all(path in hashes for path in summary["files"])
    assert hashes[m.BUILDER] == m.sha(tree["root"] / m.BUILDER)
    assert frames["events"].loc[frames["events"].status.ne("eligible"), "risk_pct"].isna().all()


@pytest.mark.parametrize("fault", ["builder", "config", "source", "csv", "whitelist", "economic", "support"])
def test_freeze_hash_and_scope_drift_rejected(tree, fault):
    if fault in {"builder", "source"}:
        name = m.BUILDER if fault == "builder" else sorted(m.AUDIT_SOURCES)[0]
        (tree["root"] / name).write_text("changed\n")
    elif fault == "config":
        (tree["here"] / "config.json").write_text("{}")
    elif fault == "csv":
        (tree["root"] / m.DATA / "events.csv").write_text("event_id\nchanged\n")
    else:
        summary = tree["summary"]
        if fault == "whitelist": summary["files"]["../forbidden.csv"] = {}
        elif fault == "economic": summary["economics_materialized"] = True
        else: summary["cases"] = 62
        (tree["here"] / "summary.json").write_text(json.dumps(summary))
    with pytest.raises((ValueError, KeyError)):
        m.guard()


@pytest.mark.parametrize("fault", ["outcome", "future", "naive", "grid"])
def test_header_or_timestamp_preflight_rejects_before_numeric_read(tree, monkeypatch, fault):
    frame = tree["frames"]["events"].copy()
    if fault == "outcome": frame["net_return"] = .3
    else:
        frame["decision_time"] = frame.decision_time.astype(object)
        frame.loc[0, "decision_time"] = {"future": "2025-01-01T00:00:00Z", "naive": "2023-03-03T12:00:00", "grid": "2023-03-03T12:01:00Z"}[fault]
    update_csv(tree, "events", frame)
    original = pd.read_csv
    def read(path, **kwargs):
        assert "float_precision" not in kwargs, "numeric materialization reached before preflight failure"
        return original(path, **kwargs)
    monkeypatch.setattr(m.pd, "read_csv", read)
    with pytest.raises(ValueError): m.guard()


@pytest.mark.parametrize("fault", ["case_missing", "control_link", "stop_changed", "unknown_risk", "bad_risk",
    "bad_status", "late_clock", "pending_clock", "trace_missing", "trace_duplicate", "prefix_missing", "prefix_false"])
def test_saved_support_clock_and_null_contracts_rejected(tree, fault):
    frames = copy.deepcopy(tree["frames"])
    if fault == "case_missing": frames["requests"] = frames["requests"].iloc[1:]
    elif fault == "control_link": frames["requests"].loc[170, "parent_event_id"] = "case60"
    elif fault == "stop_changed": frames["events"].loc[0, "initial_stop"] = 96.
    elif fault == "unknown_risk": frames["events"].loc[4, "risk_pct"] = 0.
    elif fault == "bad_risk": frames["events"].loc[0, "risk_atr"] = 3.
    elif fault == "bad_status": frames["events"].loc[0, "status"] = "cash_zero"
    elif fault == "late_clock": frames["events"].loc[0, "status_known_at"] = E + pd.Timedelta(minutes=5)
    elif fault == "pending_clock": frames["events"].loc[6, "status"] = "eligible"
    elif fault == "trace_missing": frames["trace"] = frames["trace"].iloc[1:]
    elif fault == "trace_duplicate": frames["trace"] = pd.concat([frames["trace"], frames["trace"].iloc[:1]])
    elif fault == "prefix_missing": frames["prefix_checks"] = frames["prefix_checks"].iloc[1:]
    elif fault == "prefix_false": frames["prefix_checks"].loc[0, "trace_equal"] = False
    with pytest.raises(ValueError): m.validate_ledgers(frames, tree["summary"])


def test_actual_sql_preserves_denominators_unknown_pending_and_inputs(tree, monkeypatch):
    before = {name: frame.copy(deep=True) for name, frame in tree["frames"].items()}
    queries = []
    read = pd.read_sql_query
    def tracked(query, con):
        queries.append(query)
        return read(query, con)
    monkeypatch.setattr(m.pd, "read_sql_query", tracked)
    counts = m.execute_counts(tree["frames"], tree["summary"])
    assert queries == [m.STATUS_SQL]
    assert set(counts.status) == m.STATUSES
    assert counts.loc[counts.cohort.eq("case"), "events"].sum() == 63
    assert counts.loc[counts.cohort.eq("control"), "events"].sum() == 108
    assert counts.loc[counts.cohort.eq("case"), "denominator"].eq(63).all()
    assert counts.loc[counts.cohort.eq("control"), "denominator"].eq(108).all()
    assert counts.groupby("cohort").share.sum().eq(1).all()
    for name in before: pd.testing.assert_frame_equal(before[name], tree["frames"][name])


@pytest.mark.parametrize("table", ["status_counts", "fold_counts", "seed_counts"])
def test_aggregate_drift_rejected(tree, table):
    tree["frames"][table].loc[0, "events"] += 1
    with pytest.raises(ValueError): m.execute_counts(tree["frames"], tree["summary"])


def test_prepare_executes_three_fixed_cells_and_records_exact_hashes(tree):
    result = m.prepare()
    notebook = json.loads((tree["here"] / "review.ipynb").read_text())
    m.validate_notebook(notebook, executed=True)
    code = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    markdown = [cell for cell in notebook["cells"] if cell["cell_type"] == "markdown"]
    assert [cell["execution_count"] for cell in code] == [1, 2, 3]
    assert [cell["source"][0].strip() for cell in markdown] == ["## tl;dr", "## Context & Methods", "## Data", "## Results", "## Takeaways"]
    assert "### Key Assumptions" in "".join(markdown[1]["source"])
    assert "WITH totals" in "".join(code[1]["outputs"][0]["text"])
    assert "171" in "".join(code[2]["outputs"][0]["text"])
    assert markdown[0]["source"] == m._notebook_tldr(result["data"]["status_counts"])
    assert "Execution pending" not in "".join(markdown[0]["source"])
    receipt = json.loads((tree["here"] / "report_prepare_receipt.json").read_text())
    assert receipt["jupyter_kernel"] is False and receipt["executed_code_cells"] == 3
    assert receipt["notebook_sha256"] == m.sha(tree["here"] / "review.ipynb")
    assert receipt["report_data_sha256"] == m.sha(tree["here"] / "report_data.json")
    assert result["summary_sha256"] == m.sha(tree["here"] / "summary.json")
    assert result["builder_sha256"] == m.sha(tree["root"] / m.BUILDER)
    with pytest.raises(ValueError, match="One-shot"): m.prepare()


@pytest.mark.parametrize("fault", ["cell_count", "order", "program", "kernel", "kernel_spec", "output", "execution_count", "summary", "markdown"])
def test_notebook_program_schema_and_execution_claims_fail_closed(tree, fault):
    notebook = m.notebook_template()
    m.execute_notebook(notebook)
    code = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    if fault == "cell_count": notebook["cells"].pop()
    elif fault == "order": notebook["cells"].reverse()
    elif fault == "program": code[0]["source"].append("print('unexpected')\n")
    elif fault == "kernel": notebook["metadata"]["execution"]["jupyter_kernel"] = True
    elif fault == "kernel_spec": notebook["metadata"]["kernelspec"] = {"name": "invented"}
    elif fault == "output": code[1]["outputs"] = []
    elif fault == "execution_count": code[1]["execution_count"] = 7
    elif fault == "summary": notebook["cells"][0]["source"] = ["## tl;dr\n", "Invented results\n"]
    else: notebook["cells"][1]["source"] = ["## Wrong methods\n"]
    with pytest.raises(ValueError): m.validate_notebook(notebook, executed=True)


def test_package_uses_reviewed_sections_actual_sql_and_original_share_data(tree):
    m.prepare()
    path = add_narrative(tree)
    artifact = m.package()
    manifest = artifact["manifest"]
    assert manifest["title"] == m.TITLE and len(manifest["charts"]) == 1
    assert manifest["charts"][0]["palette"]["name"] == "blueGold"
    chart_at = next(i for i, block in enumerate(manifest["blocks"]) if block["type"] == "chart")
    assert manifest["blocks"][chart_at - 1]["body"].startswith("## 等待后的状态\n")
    assert "sourceId" not in manifest["blocks"][chart_at - 1]
    assert "sourceId" not in manifest["blocks"][1]
    assert [block["body"] for block in manifest["blocks"] if block["type"] == "markdown"] == [part.strip() for part in __import__("re").split(r"(?m)(?=^## )", path.read_text().strip())]
    source = next(s for s in manifest["sources"] if s["id"] == "status_sql")
    assert source["query"]["sql"] == m.STATUS_SQL and source["query"]["tables_used"] == ["main.audit_events"]
    pd.testing.assert_frame_equal(pd.DataFrame(artifact["snapshot"]["datasets"]["status_counts"]),
                                  tree["frames"]["status_counts"])
    receipt = json.loads((tree["here"] / "artifact_build_receipt.json").read_text())
    assert receipt["report_sha256"] == m.sha(path)
    assert receipt["artifact_sha256"] == m.sha(tree["here"] / "artifact.json")
    assert "jupyter_kernel" not in artifact["snapshot"]
    with pytest.raises(ValueError, match="One-shot"): m.package()


@pytest.mark.parametrize("fault", ["uncommitted_md", "anchor", "summary_heading", "empty_interpretation", "notebook", "report_data"])
def test_package_rejects_unreviewed_or_changed_evidence_before_writing(tree, fault):
    m.prepare()
    path = add_narrative(tree)
    if fault == "uncommitted_md": path.write_text(path.read_text() + "\nUnreviewed.\n")
    elif fault in {"anchor", "summary_heading", "empty_interpretation"}:
        text = path.read_text()
        if fault == "anchor": text = text.replace("## 等待后的状态", "## Wrong anchor")
        elif fault == "summary_heading": text = text.replace("## Executive Summary", "## Summary")
        else: text = text.replace("Read each share against its original cohort. Unknown and pending are not cash zero.", "")
        path.write_text(text)
        tree["frozen"][m.REPORT] = path.read_bytes()
    else:
        output = tree["here"] / ("review.ipynb" if fault == "notebook" else "report_data.json")
        output.write_text("{}")
    with pytest.raises(ValueError): m.package()
    assert not (tree["here"] / "artifact.json").exists()


@pytest.mark.parametrize("phase,name", [("prepare", "review.ipynb"), ("prepare", "report_prepare_receipt.json"),
                                      ("package", "artifact_build_receipt.json")])
def test_any_existing_output_blocks_before_input_read(tree, monkeypatch, phase, name):
    (tree["here"] / name).write_text("existing evidence")
    monkeypatch.setattr(m, "guard", lambda: pytest.fail("Should refuse before input access"))
    with pytest.raises(ValueError, match="One-shot"): getattr(m, phase)()
    assert (tree["here"] / name).read_text() == "existing evidence"
