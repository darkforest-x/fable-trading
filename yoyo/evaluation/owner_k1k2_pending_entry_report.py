"""Package only the frozen V38 saved-output clock audit, never market/outcome data.

Inputs are summary/config plus exactly eight CSVs under the V38 output directory.
Request identity and original stops/ATR are retained; event status and visited
five-minute clocks are subsequent audit observations, not original-time features.
STATUS_SQL is actually executed on all 171 saved events, preserving case/control
denominators 63/108 and unknown/pending categories. No return, fill, policy replay,
threshold, archive reader, or V37 outcome is consulted.

The companion notebook has five markdown sections and three code cells executed sequentially via Python's
stdlib exec/redirect_stdout in a fresh namespace. This is NOT Jupyter-kernel or
nbformat certification; its narrow nbformat-4 JSON structure is validated here.
The final canonical artifact is handed to the existing portable HTML packager.
Pandas source: https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.read_csv.html
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.owner_k1k2_pending_entry_audit import STATUS_SQL, validate_support

ROOT = Path(__file__).resolve().parents[2]
REL = Path("experiments/active/exp-btcusdtp-owner-k1k2-pending-entry-20260907-v38")
HERE = ROOT / REL
DATA = Path("data/owner_k1k2_pending_entry_v38")
REPORT = "analysis/p1_btcusdtp_owner_k1k2_pending_entry_v38_20260907.md"
TITLE = "K1/K2 Pending Entry Audit · V38"
BUILDER = "yoyo/evaluation/owner_k1k2_pending_entry_report.py"
TESTS = "tests/test_owner_k1k2_pending_entry_report.py"
TABLES = ("requests", "assignments", "events", "trace", "prefix_checks",
          "status_counts", "fold_counts", "seed_counts")
STATUSES = {"eligible", "invalidated_open", "invalidated_wait_bar", "unknown_raw",
            "unknown_management", "pending_at_cutoff"}
AUDIT_SOURCES = {
    "yoyo/data/k1k2_pending_entry.py", "yoyo/evaluation/owner_k1k2_pending_entry_audit.py",
    "tests/test_k1k2_pending_entry.py", "tests/test_owner_k1k2_pending_entry_audit.py",
    "yoyo/data/hourly_impulse.py", "yoyo/evaluation/owner_k1k2_genuine_flow.py",
    "yoyo/data/k1k2_genuine_flow_alignment.py",
}
CELL_IDS = ("v38-context", "v38-status-sql", "v38-checks")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(value):
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value


def _write(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(_json(value), stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def _new_outputs(names):
    if any((HERE / name).exists() for name in names):
        raise ValueError("One-shot report evidence already exists; refuse overwrite")


def _committed(path, commit):
    if subprocess.check_output(["git", "show", commit + ":" + path], cwd=ROOT) != (ROOT / path).read_bytes():
        raise ValueError("Commit exact reporting/audit source first: " + path)


def _clock(series, *, nullable=False):
    values = []
    for value in series:
        if pd.isna(value):
            if not nullable:
                raise ValueError("Missing required saved clock")
            values.append(pd.NaT)
            continue
        t = pd.Timestamp(value)
        if t.tzinfo is None or not pd.Timestamp("2023-01-01", tz="UTC") <= t < pd.Timestamp("2025-01-01", tz="UTC"):
            raise ValueError("Saved clock outside explicit authorized UTC era")
        if t != t.floor("5min"):
            raise ValueError("Saved clock not on five-minute boundary")
        values.append(t.tz_convert("UTC"))
    return pd.Series(values, index=series.index, dtype="datetime64[ns, UTC]")


def _same(actual, expected, keys, label):
    if set(actual) != set(expected):
        raise ValueError(label + " columns drift")
    a = actual.sort_values(keys).reset_index(drop=True)
    b = expected[actual.columns].sort_values(keys).reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(a, b, check_dtype=False, rtol=1e-12, atol=1e-12)
    except AssertionError as exc:
        raise ValueError(label + " values drift") from exc


def guard():
    """Freeze/hash/clock preflight; reads no path outside explicit V38 sources."""
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    for path in [BUILDER, TESTS]:
        _committed(path, commit)
    summary = json.loads((HERE / "summary.json").read_text())
    config = json.loads((HERE / "config.json").read_text())
    if (summary["status"] != "clock_audit_complete_not_economic"
            or [summary[k] for k in ["requests", "cases", "controls", "original_matched_cases", "original_unmatched_cases"]] != [171, 63, 108, 36, 27]
            or summary["administrative_observation_minutes"] != 60 or summary["entry_expiry"] is not None
            or any(summary[k] is not False for k in ["holdout_evaluated", "training_eligible", "production_eligible",
                                                    "economics_materialized", "observed_live_delivery_verified"])):
        raise ValueError("V38 audit-only scope/support drift")
    if (config["output_dir"] != str(DATA) or set(config["builder_paths"]) != AUDIT_SOURCES
            or config["audit_only"] is not True or config["economic_outcomes"] is not False
            or config["entry_expiry"] is not None or config["audit_minutes"] != 60
            or summary["config_sha256"] != sha(HERE / "config.json")):
        raise ValueError("V38 frozen configuration drift")
    sources = [str(REL / "config.json"), str(REL / "PROJECT_PLAN.md")] + sorted(AUDIT_SOURCES)
    for path in sources:
        _committed(path, summary["source_commit"])
    expected = {str(DATA / (name + ".csv")) for name in TABLES}
    if set(summary["files"]) != expected:
        raise ValueError("Exactly eight saved V38 CSVs are permitted")
    hashes = {path: sha(ROOT / path) for path in sources + [BUILDER, TESTS, str(REL / "summary.json")]}
    # Validate every hash and every header before materializing any numeric rows.
    for path in sorted(expected):
        p = ROOT / path
        if p.resolve() != p.absolute() or sha(p) != summary["files"][path]["sha256"]:
            raise ValueError("Saved V38 bytes/path drift: " + path)
        hashes[path] = sha(p)
        columns = list(pd.read_csv(p, nrows=0).columns)
        if columns != summary["files"][path]["columns"]:
            raise ValueError("Saved schema drift")
        if any(re.search(r"return|profit|pnl|^exit_|future_label", c, re.I) for c in columns):
            raise ValueError("Economic/outcome column in clock audit")
        for name in [c for c in columns if c in {"decision_time", "audit_cutoff", "status_known_at", "eligible_at",
                                                "invalidated_interval_start", "observed_at", "completed_bar_open", "terminal"}]:
            _clock(pd.read_csv(p, usecols=[name])[name], nullable=name in {"eligible_at", "invalidated_interval_start"})
    frames = {name: pd.read_csv(ROOT / DATA / (name + ".csv"), float_precision="round_trip") for name in TABLES}
    for name, frame in frames.items():
        if len(frame) != summary["files"][str(DATA / (name + ".csv"))]["rows"]:
            raise ValueError("Saved row count drift")
    validate_ledgers(frames, summary)
    return commit, summary, frames, hashes


def validate_ledgers(frames, summary):
    """Reconcile original support, terminal clocks, traces and saved aggregates."""
    requests, assignments, events = (frames[k].copy() for k in ["requests", "assignments", "events"])
    for frame in [requests, events]:
        frame["decision_time"] = _clock(frame.decision_time)
        frame["audit_cutoff"] = _clock(frame.audit_cutoff)
    validate_support(requests, assignments)
    if (len(events) != 171 or not events.event_id.is_unique
            or not events.status.isin(STATUSES).all() or not events.seed_state.isin(["aligned", "opposite", "unknown"]).all()):
        raise ValueError("Event identity/status invalid")
    _same(events[requests.columns], requests, ["event_id"], "Original request fields")
    if not requests.audit_cutoff.eq(requests.decision_time + pd.Timedelta(hours=1)).all():
        raise ValueError("Administrative observation window changed")
    events["status_known_at"] = _clock(events.status_known_at)
    events["eligible_at"] = _clock(events.eligible_at, nullable=True)
    events["invalidated_interval_start"] = _clock(events.invalidated_interval_start, nullable=True)
    if (not events.waiting_bars.isin(range(13)).all()
            or not events.status_known_at.eq(events.decision_time + pd.to_timedelta(events.waiting_bars * 5, unit="min")).all()):
        raise ValueError("Terminal waiting clock drift")
    eligible = events.status.eq("eligible")
    risk = events[["reference_open", "risk_pct", "risk_atr"]]
    if (not np.isfinite(risk.loc[eligible].to_numpy(float)).all() or not risk.loc[eligible].gt(0).all().all()
            or not risk.loc[~eligible].isna().all().all() or not events.loc[~eligible, "eligible_at"].isna().all()
            or not events.loc[eligible, "eligible_at"].eq(events.loc[eligible, "status_known_at"]).all()):
        raise ValueError("Only eligible observations may have risk/entry references")
    r = events.loc[eligible]
    distance = r.direction * (r.reference_open - r.initial_stop)
    if not (np.allclose(r.risk_pct, distance / r.reference_open, rtol=1e-12, atol=1e-12)
            and np.allclose(r.risk_atr, distance / r.signal_atr, rtol=1e-12, atol=1e-12)):
        raise ValueError("Original-stop eligible risk drift")
    pending = events.status.eq("pending_at_cutoff")
    if not events.loc[pending, "status_known_at"].eq(events.loc[pending, "audit_cutoff"]).all():
        raise ValueError("Pending is administrative right-censoring")
    stopped = events.status.eq("invalidated_wait_bar")
    if (not events.loc[stopped, "invalidated_interval_start"].eq(events.loc[stopped, "status_known_at"] - pd.Timedelta(minutes=5)).all()
            or not events.loc[~stopped, "invalidated_interval_start"].isna().all()):
        raise ValueError("Invalidated waiting-bar clock drift")
    trace = frames["trace"].copy()
    trace["observed_at"] = _clock(trace.observed_at)
    trace["completed_bar_open"] = _clock(trace.completed_bar_open)
    if (trace.duplicated(["event_id", "observed_at"]).any() or set(trace.event_id) != set(events.event_id)
            or not trace.completed_bar_open.eq(trace.observed_at - pd.Timedelta(minutes=5)).all()
            or len(trace) != summary["trace_rows"]):
        raise ValueError("Trace grid/support drift")
    for row in events.itertuples():
        observed = trace.loc[trace.event_id.eq(row.event_id), "observed_at"].tolist()
        if observed != list(pd.date_range(row.decision_time, row.status_known_at, freq="5min")):
            raise ValueError("Trace does not preserve every visited boundary")
    proof = frames["prefix_checks"].copy()
    if (len(proof) != 171 or not proof.event_id.is_unique or set(proof.event_id) != set(events.event_id)
            or not proof[["events_equal", "trace_equal", "current_hlc_masked"]].eq(True).all().all()
            or summary["prefix_checks"] != {"events": 171, "all_passed": True}):
        raise ValueError("Full original prefix proof required")
    proof["terminal"] = _clock(proof.terminal)
    _same(proof[["event_id", "cohort", "terminal"]],
          events[["event_id", "cohort", "status_known_at"]].rename(columns={"status_known_at": "terminal"}),
          ["event_id"], "Prefix identity/clock")
    expected_negative = dict(synthetic_cases=1, correct_unchanged=True, deliberately_wrong_future_gate_changed=True)
    if summary["negative_control"] != expected_negative or summary["sql"] != STATUS_SQL:
        raise ValueError("Recorded causal negative control/SQL drift")


def execute_counts(frames, summary):
    """Execute the actual audit SQL and independently reconcile grouped outputs."""
    with sqlite3.connect(":memory:") as connection:
        frames["events"].to_sql("audit_events", connection, index=False)
        counts = pd.read_sql_query(STATUS_SQL, connection)
    for expected in [frames["status_counts"], pd.DataFrame(summary["counts"])]:
        _same(counts, expected, ["status", "cohort"], "STATUS_SQL")
    if counts.events.sum() != 171:
        raise ValueError("SQL lost original support")
    for cohort, denominator in [("case", 63), ("control", 108)]:
        rows = counts.loc[counts.cohort.eq(cohort)]
        if rows.events.sum() != denominator or not rows.denominator.eq(denominator).all() or not np.isclose(rows.share.sum(), 1):
            raise ValueError("Status-share denominator drift")
    for table, keys in [("fold_counts", ["fold", "cohort", "status"]), ("seed_counts", ["cohort", "seed_state", "status"])]:
        actual = frames["events"].groupby(keys, dropna=False).size().rename("events").reset_index()
        for expected in [frames[table], pd.DataFrame(summary[table])]:
            _same(actual, expected, keys, table)
    return counts


def notebook_template():
    cells = [
        "# tl;dr / Context & Methods\n"
        "# Saved V38 audit only. Eligibility is not a fill or profit.\n"
        "# Key assumptions: original 63/108 requests, fixed stops, 60-minute administrative observation.\n"
        "import json\nfrom yoyo.evaluation import owner_k1k2_pending_entry_report as report\n"
        "commit, summary, frames, hashes = report.guard()\n"
        "print(json.dumps({'scope': 'V38 saved outputs only; no market/outcome reads', 'source_hashes': hashes}, indent=2))\n",
        "# Data / Results: execute the exact STATUS_SQL on all saved observations.\n"
        "counts = report.execute_counts(frames, summary)\n"
        "print(report.STATUS_SQL)\nprint(counts.to_json(orient='records', indent=2))\n",
        "# Takeaways: preserve unknown/pending, no cash-zero or return inference.\n"
        "assert counts.events.sum() == 171\n"
        "assert summary['prefix_checks'] == {'events': 171, 'all_passed': True}\n"
        "print(json.dumps({'requests': 171, 'case': 63, 'control': 108, 'prefix_passed': 171, 'status_counts': counts.to_dict('records'), "
        "'economic_outcomes': False, 'interpretation': 'Eligibility and waiting clocks only; pending is right-censored, unknown remains unknown.'}))\n",
    ]
    code_cells = [dict(cell_type="code", id=cell_id, metadata={}, source=source.splitlines(keepends=True),
                       execution_count=None, outputs=[]) for cell_id, source in zip(CELL_IDS, cells)]
    def markdown(cell_id, source):
        return dict(cell_type="markdown", id=cell_id, metadata={}, source=source.splitlines(keepends=True))
    ordered = [markdown("v38-tldr", "## tl;dr\n\nExecution pending; no observed claims yet.\n"),
        markdown("v38-methods", "## Context & Methods\n\nThis is a saved-output clock audit, not a backtest or a fill simulation.\n\n"
                 "### Key Assumptions\n\nOriginal case/control requests and fixed reference stops are unchanged. The one-hour observation limit is administrative, not an entry expiry.\n\n"
                 "Prefix comparisons normalize opaque segment labels; both paths share the SMA implementation, so this is not independent formula verification.\n\n"
                 "Three code cells run with stdlib sequential exec and captured stdout, not a Jupyter kernel or nbformat certification.\n"),
        code_cells[0], markdown("v38-data", "## Data\n\nOnly the eight hash-verified V38 saved CSVs are read. The next cell executes STATUS_SQL on every original event, without dropping unknown or pending observations.\n"),
        code_cells[1], markdown("v38-results", "## Results\n\nThe grouped rows above retain event count, original cohort denominator, share, immediate/delayed eligibility, and observed waiting minutes. Shares are fractions from 0 to 1, not returns.\n"),
        code_cells[2], markdown("v38-takeaways", "## Takeaways\n\nEligibility is not an executed trade or a profitable trade. Unknown remains unknown; pending is administrative right-censoring and cannot be assigned cash-zero return. A separately frozen economic policy is still required.\n")]
    return dict(nbformat=4, nbformat_minor=5,
        metadata=dict(language_info=dict(name="python", version=sys.version.split()[0]),
                      execution=dict(method="stdlib_sequential_exec", jupyter_kernel=False,
                                     scope="Three fixed code cells; captured stdout; narrow JSON schema validation, not nbformat certification")),
        cells=ordered)


def _notebook_tldr(rows):
    lines = ["## tl;dr\n\n", "**Observed clock support, not profitability.**\n\n"]
    for cohort, denominator in [("case", 63), ("control", 108)]:
        group = [r for r in rows if r["cohort"] == cohort]
        if sum(r["events"] for r in group) != denominator:
            raise ValueError("Notebook observed denominator drift")
        eligible = sum(r["events"] for r in group if r["status"] == "eligible")
        pending = sum(r["events"] for r in group if r["status"] == "pending_at_cutoff")
        unknown = sum(r["events"] for r in group if r["status"] in {"unknown_raw", "unknown_management"})
        lines.append(f"- {cohort}: {eligible}/{denominator} eligible; {pending} pending and {unknown} unknown.\n")
    lines.append("\nAll 171 original requests remain in the denominators. No economic outcomes were calculated.\n")
    return "".join(lines).splitlines(keepends=True)


def validate_notebook(notebook, *, executed):
    """Validate the narrow generated nbformat-4 schema and exact cell programs."""
    template = notebook_template()
    if (set(notebook) != {"nbformat", "nbformat_minor", "metadata", "cells"}
            or notebook["nbformat"] != 4 or notebook["nbformat_minor"] != 5
            or set(notebook["metadata"]) != {"language_info", "execution"}
            or notebook["metadata"]["language_info"].get("name") != "python"
            or notebook["metadata"]["execution"] != template["metadata"]["execution"]
            or len(notebook["cells"]) != 8):
        raise ValueError("Unexpected notebook schema/execution claim")
    code_number = 0
    for cell, expected in zip(notebook["cells"], template["cells"]):
        source = expected["source"]
        if executed and cell["id"] == "v38-tldr":
            try:
                last_code = [c for c in notebook["cells"] if c["cell_type"] == "code"][-1]
                observed = json.loads("".join(last_code["outputs"][0]["text"]))
                source = _notebook_tldr(observed["status_counts"])
            except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError("Notebook observed summary missing") from exc
        if (set(cell) != set(expected) or cell["cell_type"] != expected["cell_type"] or cell["id"] != expected["id"]
                or cell["source"] != source or cell["metadata"] != {}):
            raise ValueError("Unexpected notebook cell program/order")
        if cell["cell_type"] == "markdown":
            continue
        code_number += 1
        if cell["execution_count"] != (code_number if executed else None):
            raise ValueError("Unexpected notebook execution order")
        if executed:
            outputs = cell["outputs"]
            if (len(outputs) != 1 or set(outputs[0]) != {"output_type", "name", "text"}
                    or outputs[0]["output_type"] != "stream" or outputs[0]["name"] != "stdout"
                    or not isinstance(outputs[0]["text"], list) or not outputs[0]["text"]
                    or not all(isinstance(line, str) for line in outputs[0]["text"])):
                raise ValueError("Notebook stdout receipt missing")
        elif cell["outputs"] != []:
            raise ValueError("Unexecuted notebook has cached outputs")
    json.dumps(notebook, allow_nan=False)


def execute_notebook(notebook):
    validate_notebook(notebook, executed=False)
    namespace = {"__name__": "__main__"}
    code_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
    for i, cell in enumerate(code_cells, 1):
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            exec(compile("".join(cell["source"]), "review.ipynb:" + cell["id"], "exec"), namespace)
        cell["execution_count"] = i
        cell["outputs"] = [dict(output_type="stream", name="stdout", text=capture.getvalue().splitlines(keepends=True))]
    notebook["cells"][0]["source"] = _notebook_tldr(namespace["counts"].to_dict("records"))
    validate_notebook(notebook, executed=True)
    return namespace


def prepare():
    _new_outputs(["report_data.json", "review.ipynb", "report_prepare_receipt.json"])
    notebook = notebook_template()
    namespace = execute_notebook(notebook)
    commit, summary, frames, hashes = [namespace[key] for key in ["commit", "summary", "frames", "hashes"]]
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    data = dict(source_commit=commit, audit_source_commit=summary["source_commit"], generated_at=stamp,
                builder_sha256=hashes[BUILDER], summary_sha256=hashes[str(REL / "summary.json")], source_hashes=hashes,
                status="clock_audit_complete_not_economic", query=STATUS_SQL,
                data=dict(status_counts=namespace["counts"].to_dict("records"),
                          fold_counts=frames["fold_counts"].to_dict("records"), seed_counts=frames["seed_counts"].to_dict("records")),
                support=dict(requests=171, cases=63, controls=108, original_matched_cases=36, original_unmatched_cases=27),
                prefix_checks=summary["prefix_checks"], negative_control=summary["negative_control"],
                notebook_execution=notebook["metadata"]["execution"], economics_materialized=False)
    # Recheck exact input/source bytes immediately before one-shot materialization.
    if any(sha(ROOT / path) != value for path, value in hashes.items()):
        raise ValueError("Inputs changed during reporting")
    _write(HERE / "review.ipynb", notebook)
    _write(HERE / "report_data.json", data)
    _write(HERE / "report_prepare_receipt.json", dict(source_commit=commit, generated_at=stamp, source_hashes=hashes,
        report_data_sha256=sha(HERE / "report_data.json"), notebook_sha256=sha(HERE / "review.ipynb"),
        executed_code_cells=3, execution_method="stdlib_sequential_exec", jupyter_kernel=False))
    return _json(data)


def package():
    _new_outputs(["artifact.json", "artifact_build_receipt.json"])
    commit, summary, frames, hashes = guard()
    saved = json.loads((HERE / "report_data.json").read_text())
    receipt = json.loads((HERE / "report_prepare_receipt.json").read_text())
    if (receipt["report_data_sha256"] != sha(HERE / "report_data.json")
            or receipt["notebook_sha256"] != sha(HERE / "review.ipynb")
            or receipt["source_hashes"] != hashes or saved["source_hashes"] != hashes
            or saved["summary_sha256"] != sha(HERE / "summary.json") or saved["query"] != STATUS_SQL
            or saved["economics_materialized"] is not False):
        raise ValueError("Prepared report evidence drift")
    validate_notebook(json.loads((HERE / "review.ipynb").read_text()), executed=True)
    counts = execute_counts(frames, summary)
    _same(counts, pd.DataFrame(saved["data"]["status_counts"]), ["status", "cohort"], "Prepared chart rows")
    _committed(REPORT, commit)
    sections = [part.strip() for part in re.split(r"(?m)(?=^## )", (ROOT / REPORT).read_text().strip())]
    if (len(sections) < 5 or sections[0] != "# " + TITLE
            or not sections[1].startswith("## Executive Summary\n")
            or sum(section.splitlines()[0] == "## 等待后的状态" for section in sections) != 1):
        raise ValueError("Incomplete reviewed report spine/chart anchor")
    sources = [dict(id="report", label="V38 · 已审核定义与结论", path=REPORT),
               dict(id="summary", label="V38 · 冻结审计摘要", path=str(REL / "summary.json")),
               dict(id="review", label="V38 · 原配对分母与时钟独立复核", path=str(REL / "REVIEW.md")),
               dict(id="review_data", label="V38 · 保存结果复算与来源哈希", path=str(REL / "report_data.json")),
               dict(id="notebook", label="V38 · 标准库顺序执行的三单元复核", path=str(REL / "review.ipynb"))]
    sources.append(dict(id="status_sql", label="V38 · 原 171 个请求的状态统计", path=str(DATA / "events.csv"),
        query=dict(sql=STATUS_SQL, language="sql", engine="sqlite", executed_at=saved["generated_at"],
                   tables_used=["main.audit_events"], filters=["All original V38 case63/control108 requests, 2023–2024; no status exclusions"],
                   description="Executed on saved V38 events; reconciled with all saved and summary counts",
                   metric_definitions={"events": "Count of original requests with this observed terminal audit status",
                       "denominator": "All original requests in the cohort: case63/control108",
                       "share": "events/denominator; fraction0..1, including unknown and administrative pending",
                       "observed_wait_minutes": "Mean waiting_bars times5; observation duration, not time in a position"})))
    blocks = []
    for i, section in enumerate(sections):
        block = dict(id="section_" + str(i), type="markdown", layout="full", body=section)
        # Narrative now includes matched-support findings from REVIEW as well
        # as summary counts; do not assign a misleading single-source tooltip.
        blocks.append(block)
        if section.splitlines()[0] == "## 等待后的状态":
            if not section.partition("\n")[2].strip():
                raise ValueError("Status chart requires adjacent reviewed interpretation")
            blocks.append(dict(id="status_chart", type="chart", layout="full", chartId="status_shares"))
    chart = dict(id="status_shares", type="bar", title="等待状态占比 · Case / Control", dataset="status_counts", sourceId="status_sql",
                 description="Case n=63；Control n=108；share 为 0–1 比例。Unknown / pending 保留，不代表零收益。",
                 showDescription=True, palette=dict(kind="categorical", name="blueGold"), legend=dict(position="top", title="Cohort"),
                 encodings=dict(x=dict(field="status", type="nominal"), y=dict(field="share", type="quantitative"),
                                color=dict(field="cohort", type="nominal"),
                                tooltip=[dict(field=field, type="quantitative") for field in
                                         ["events", "denominator", "immediately_eligible", "delayed_eligible", "observed_wait_minutes"]]))
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    artifact = dict(surface="report", manifest=dict(version=1, surface="report", title=TITLE, generatedAt=stamp,
                    blocks=blocks, charts=[chart], cards=[], tables=[], filters=[], sources=sources),
                    snapshot=dict(version=1, status="ready", generatedAt=stamp, datasets=dict(status_counts=counts.to_dict("records"))), sources=sources)
    if any(sha(ROOT / path) != value for path, value in hashes.items()):
        raise ValueError("Inputs changed during packaging")
    _committed(REPORT, commit)
    _write(HERE / "artifact.json", artifact)
    _write(HERE / "artifact_build_receipt.json", dict(source_commit=commit, generated_at=stamp, source_hashes=hashes,
        builder_sha256=sha(ROOT / BUILDER), summary_sha256=sha(HERE / "summary.json"), report_sha256=sha(ROOT / REPORT),
        report_data_sha256=sha(HERE / "report_data.json"), notebook_sha256=sha(HERE / "review.ipynb"),
        artifact_sha256=sha(HERE / "artifact.json"), charts=1, sections=len(sections)))
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "package"])
    arguments = parser.parse_args()
    result = prepare() if arguments.phase == "prepare" else package()
    print(json.dumps(dict(phase=arguments.phase, status="saved_one_shot")))
