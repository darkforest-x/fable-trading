"""V39 saved-ledger review and canonical report packaging; no market I/O.

Technical report: methodology/experiment validation, not a profitability claim.
Inputs are the committed V39 config/source, summary and its exact saved CSVs.
Review independently checks original request support and original-notional
20bp accounting. It does NOT reconstruct native MA/raw paths, reexecute trading,
recompute inference or certify all narrative claims. Mixed-source markdown has
no misleading block-wide sourceId. Tables already in MD need no extra chart.
Notebook cells execute sequentially via Python exec, not a Jupyter kernel.
Canonical shape follows the installed analytics-app-core / V38 report builder.
Pandas CSV round_trip: https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.read_csv.html
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
REL = Path("experiments/active/exp-btcusdtp-owner-k1k2-delayed-entry-20260907-v39")
DATA = Path("data/owner_k1k2_delayed_entry_v39")
REPORT = "analysis/p1_btcusdtp_owner_k1k2_delayed_entry_v39_20260907.md"
BUILDER = "yoyo/evaluation/owner_k1k2_delayed_entry_report.py"
TESTS = "tests/test_owner_k1k2_delayed_entry_report.py"
POLICY = dict(management_minutes=5, exit_mode="transition_colour", confirmations=1, max_hours=72, cost_fraction=.002)
ARMS, COHORTS = ("immediate", "delayed"), {"case": 63, "control": 108}
TABLES = ("case_requests", "control_requests", "assignments", "case_changes", "control_changes", "paired_contrasts", "fold_metrics") + tuple(
    c + "_entry_" + s for c in COHORTS for s in ("audit", "trace")) + tuple(
    a + "_" + c + "_" + s for a in ARMS for c in COHORTS for s in ("trades", "single_position"))


def safe(name):
    p = Path(name)
    if not isinstance(name, (str, Path)) or p.is_absolute() or any(x in str(name) for x in ("..", "\\", ":")) or not p.parts:
        raise ValueError("Unsafe relative identity")
    path = ROOT / p
    if path.resolve() != path.absolute(): raise ValueError("Symlink source/output prohibited")
    return path


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, value):
    with safe(name).open("x") as stream: json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def committed(name, commit):
    path = safe(name)
    if subprocess.check_output(["git", "show", commit + ":" + str(name)], cwd=ROOT) != path.read_bytes():
        raise ValueError("Commit exact source before reporting: " + str(name))


def same(x, y, label):
    try: np.testing.assert_allclose(x, y, rtol=1e-12, atol=1e-12, equal_nan=True)
    except (AssertionError, TypeError) as exc: raise ValueError(label + " differs") from exc


def clock(series, nullable=False):
    for value in series.dropna():
        t = pd.Timestamp(value)
        if t.tzinfo is None or t < pd.Timestamp("2023-01-01T00:00Z") or t >= pd.Timestamp("2025-01-01T00:00Z") or t != t.floor("5min"):
            raise ValueError("Invalid explicit UTC-era source clock")
    if not nullable and series.isna().any(): raise ValueError("Missing required clock")
    return pd.to_datetime(series, utc=True, format="mixed")


def metric(frame):
    known = frame.request_known; closed = frame.loc[frame.closed]; n = closed.net_return
    loss = -n.clip(upper=0).sum()
    finite = lambda x: float(x) if pd.notna(x) and np.isfinite(x) else None
    return dict(requests=len(frame), known=int(known.sum()), unknown=int((~known).sum()),
        known_no_fill=int((known & (frame.entry_time.isna() | frame.outcome.eq("entry_invalid_risk"))).sum()),
        executed_closed=len(closed), mean_request_net_bp=finite(frame.request_net.mean()*1e4) if known.all() else None,
        mean_request_gross_bp=finite(frame.request_gross.mean()*1e4) if known.all() else None,
        mean_known_request_net_bp=finite(frame.loc[known, "request_net"].mean()*1e4),
        mean_trade_net_bp=finite(n.mean()*1e4), median_trade_net_bp=finite(n.median()*1e4),
        trade_win_rate=finite(n.gt(0).mean()), trade_pf=finite(n.clip(lower=0).sum()/loss) if loss > 0 else None,
        outcomes=frame.outcome.value_counts().to_dict())


def verify_tables(tables, summary):
    """All original rows, own controls and known/cash/unknown; no inference replay."""
    req = {c: tables[c + "_requests"].set_index("event_id", verify_integrity=True) for c in COHORTS}
    assignment = tables["assignments"].set_index("event_id", verify_integrity=True)
    matched = set(assignment.index[assignment.match_status.eq("matched")])
    parent_counts = req["control"].parent_event_id.value_counts()
    if (set(assignment.index) != set(req["case"].index) or len(matched) != 36 or set(parent_counts.index) != matched
            or not parent_counts.eq(3).all() or set(req["case"].index) & set(req["control"].index)):
        raise ValueError("Original 63/108/36 support differs")
    for c, expected in COHORTS.items():
        if len(req[c]) != expected: raise ValueError("Original denominator differs")
        if not req[c].direction.isin([-1, 1]).all(): raise ValueError("Direction differs")
        if not clock(req[c].decision_time).eq(clock(req[c].decision_time).dt.floor("h")).all(): raise ValueError("Non-hour request")
    for row in req["control"].itertuples():
        if row.direction != req["case"].loc[row.parent_event_id, "direction"]: raise ValueError("Control direction transfer differs")
    results, metrics = {}, {}
    for arm in ARMS:
        metrics[arm] = {}
        for cohort in COHORTS:
            f = tables[arm + "_" + cohort + "_trades"].set_index("event_id", verify_integrity=True)
            if set(f.index) != set(req[cohort].index): raise ValueError("Result identity differs")
            f = f.loc[req[cohort].index].copy()
            for col in ("closed", "request_known"):
                if not f[col].map(lambda v: isinstance(v, (bool, np.bool_))).all(): raise ValueError("Unknown boolean")
            for col in ("decision_time", "entry_time", "exit_time"):
                f[col] = clock(f[col], nullable=col != "decision_time")
            for col in ("decision_time", "fold", "direction", "initial_stop", "signal_atr"):
                target = clock(req[cohort][col]) if col == "decision_time" else req[cohort][col]
                if col in ("direction", "initial_stop", "signal_atr"): same(f[col], target, "Original " + col)
                elif not f[col].equals(target): raise ValueError("Original " + col + " differs")
            cash = f.request_known & ~f.closed
            if (not f.loc[cash, "outcome"].str.match(r"^(no_fill_stop|no_fill_expired|entry_invalid_risk)$").all()
                    or f.loc[~f.closed, ["net_return", "gross_return"]].notna().any().any()
                    or f.loc[~f.request_known, ["request_net", "request_gross"]].notna().any().any()
                    or not f.loc[f.closed, "request_known"].all()): raise ValueError("Cash/unknown/closed confusion")
            same(f.loc[cash, ["request_net", "request_gross"]], 0, "No-fill fee")
            if f.loc[cash & f.outcome.str.startswith("no_fill_"), ["entry_time", "exit_time"]].notna().any().any(): raise ValueError("No-fill has exposure clock")
            done = f.loc[f.closed]
            if (done.entry_time.isna().any() or done.exit_time.isna().any() or not done.entry_price.gt(0).all()
                    or not done.exit_price.gt(0).all() or not (done.exit_time >= done.entry_time).all()
                    or not np.isfinite(done[["entry_price", "exit_price", "gross_return", "net_return", "request_net", "request_gross"]]).all().all()
                    or not (done.entry_time >= done.decision_time).all() or not (done.entry_time < done.decision_time+pd.Timedelta(hours=1)).all()
                    or not (done.exit_time <= done.decision_time+pd.Timedelta(hours=72)).all()
                    or not (done.direction*(done.entry_price-done.initial_stop)).gt(0).all()): raise ValueError("Closed fill invalid")
            same(done.gross_return, done.direction*(done.exit_price/done.entry_price-1), "Fill gross")
            same(done.net_return, done.gross_return-.002, "20bp cost")
            same(done.request_net, done.net_return, "Request net"); same(done.request_gross, done.gross_return, "Request gross")
            measured = metric(f)
            for k, v in measured.items():
                expected = summary["metrics"][arm][cohort][k]
                if isinstance(v, (float, int)) and v is not None: same(v, expected, "Summary " + k)
                elif v != expected: raise ValueError("Summary " + k + " differs")
            metrics[arm][cohort] = measured; results[arm, cohort] = f
    for cohort in COHORTS:
        delta = tables[cohort+"_changes"].set_index("event_id", verify_integrity=True)
        if set(delta.index) != set(req[cohort].index): raise ValueError("Change identity differs")
        delta = delta.loc[req[cohort].index]
        for measure in ("net", "gross"):
            for arm in ARMS: same(delta[arm+"_"+measure], results[arm, cohort]["request_"+measure], "Change arm")
            same(delta["delta_"+measure], delta["delayed_"+measure]-delta["immediate_"+measure], "Change delta")
        same(delta.delta_saved_cost, delta.delta_net-delta.delta_gross, "Saved cost delta")
    p = tables["paired_contrasts"].set_index("event_id", verify_integrity=True)
    if set(p.index) != matched: raise ValueError("Paired identity differs")
    for event_id in matched:
        ids = req["control"].index[req["control"].parent_event_id.eq(event_id)]
        known = all(results[a, "case"].loc[event_id, "request_known"] and results[a, "control"].loc[ids, "request_known"].all() for a in ARMS)
        if p.loc[event_id, "complete_pair"] != known or p.loc[event_id, "controls"] != 3: raise ValueError("Pair completeness differs")
        for measure in ("net", "gross"):
            for arm in ARMS:
                value = results[arm, "case"].loc[event_id, "request_"+measure] - results[arm, "control"].loc[ids, "request_"+measure].mean() if known else np.nan
                same(p.loc[event_id, arm+"_excess_"+measure], value, "Own three-control excess")
            same(p.loc[event_id, "delta_excess_"+measure], p.loc[event_id, "delayed_excess_"+measure]-p.loc[event_id, "immediate_excess_"+measure], "Paired delta")
    return dict(support=dict(cases=63, controls=108, matched_cases=36, unmatched_cases=27), metrics=metrics,
        scope=dict(saved_accounting=True, raw_replay=False, sma_recomputed=False, inference_recomputed=False, single_position_recomputed=False))


def guard():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    for name in (BUILDER, TESTS): committed(name, commit)
    config = json.loads(safe(REL/"config.json").read_text()); summary = json.loads(safe(REL/"summary.json").read_text())
    if config["policy"] != POLICY or config["output_dir"] != str(DATA) or any(summary[k] is not False for k in ("holdout_evaluated", "training_eligible", "production_eligible")):
        raise ValueError("Frozen report scope differs")
    if summary["config_sha256"] != sha(safe(REL/"config.json")): raise ValueError("Config SHA differs")
    sources = [BUILDER, TESTS, str(REL/"summary.json"), str(REL/"config.json"), str(REL/"PROJECT_PLAN.md")]
    for name in config["builder_paths"] + sources[3:]: committed(name, summary["source_commit"])
    sources += config["builder_paths"]
    pre = json.loads(safe(REL/"pre_outcome_receipt.json").read_text()); sources.append(str(REL/"pre_outcome_receipt.json"))
    frozen = {str(DATA/(name+".csv")): summary["files"][str(DATA/(name+".csv"))] for name in ("case_requests", "control_requests", "assignments")}
    if (pre["files"] != frozen or pre["outcomes_materialized"] is not False or pre["source_commit"] != summary["source_commit"]
            or pre["config_sha256"] != summary["config_sha256"] or pd.Timestamp(pre["generated_at"]).tzinfo is None
            or pd.Timestamp(summary["generated_at"]).tzinfo is None or pd.Timestamp(pre["generated_at"]) >= pd.Timestamp(summary["generated_at"])):
        raise ValueError("Pre-outcome identity freeze differs")
    if set(summary["files"]) != {str(DATA/(name+".csv")) for name in TABLES}: raise ValueError("Saved file whitelist differs")
    frames = {}
    for name, receipt in summary["files"].items():
        if sha(safe(name)) != receipt["sha256"]: raise ValueError("Saved file SHA differs")
        frame = pd.read_csv(safe(name), float_precision="round_trip")
        if len(frame) != receipt["rows"] or list(frame) != receipt["columns"]: raise ValueError("Saved CSV schema differs")
        frames[Path(name).stem] = frame
    hashes = {str(name): sha(safe(name)) for name in sources + list(summary["files"])}
    return commit, summary, verify_tables(frames, summary), hashes


def notebook_template():
    sections = [("markdown", "## tl;dr\n\nExecution pending; saved-ledger review only."),
        ("markdown", "## Context & Methods\n\n### Key Assumptions\n63 cases /108 controls /36 fixed triples;20bp original-notional costs. Sequential Python exec, not a Jupyter kernel. No raw/MA/inference/serial replay."),
        ("code", "from yoyo.evaluation.owner_k1k2_delayed_entry_report import guard\ncommit, summary, review, hashes = guard()\nprint('Saved byte/source and accounting checks passed')"),
        ("markdown", "## Data\n\nOnly V39 summary and its exact saved CSVs; no archive access."),
        ("code", "import json\nprint(json.dumps(review['support'], sort_keys=True))"),
        ("markdown", "## Results\n\nRequest contributions retain no-fill zero and unknown NaN; completed-trade metrics are separate."),
        ("code", "print(json.dumps(review['metrics'], ensure_ascii=False, sort_keys=True))"),
        ("markdown", "## Takeaways\n\nPassing saved accounting does not establish profitability, raw-clock correctness or statistical independence.")]
    cells = [dict(id="v39-"+str(i), cell_type=kind, metadata={}, source=text.splitlines(keepends=True),
        **(dict(execution_count=None, outputs=[]) if kind == "code" else {})) for i, (kind, text) in enumerate(sections)]
    return dict(nbformat=4, nbformat_minor=5, metadata=dict(language_info=dict(name="python"),
        execution=dict(method="stdlib_sequential_exec", jupyter_kernel=False, nbformat_certified=False)), cells=cells)


def prepare():
    names = [REL/name for name in ("report_data.json", "review.ipynb", "report_prepare_receipt.json")]
    if any(safe(name).exists() for name in names): raise ValueError("One-shot report output exists")
    book, namespace, count = notebook_template(), {}, 0
    for cell in book["cells"]:
        if cell["cell_type"] != "code": continue
        capture = io.StringIO(); count += 1
        with contextlib.redirect_stdout(capture): exec(compile("".join(cell["source"]), "review.ipynb", "exec"), namespace)
        cell.update(execution_count=count, outputs=[dict(output_type="stream", name="stdout", text=capture.getvalue().splitlines(keepends=True))])
    commit, summary, review, hashes = [namespace[k] for k in ("commit", "summary", "review", "hashes")]
    book["cells"][0]["source"] = ["## tl;dr\n\nSaved accounting checks passed for all63 cases/108 controls and36 fixed triples; no profitability certification.\n"]
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    data = dict(generated_at=stamp, source_commit=commit, summary_sha256=hashes[str(REL/"summary.json")], source_hashes=hashes, review=review)
    if any(sha(safe(p)) != h for p, h in hashes.items()): raise ValueError("Inputs changed during review")
    write(names[1], book); write(names[0], data)
    write(names[2], dict(source_commit=commit, generated_at=stamp, source_hashes=hashes,
        report_data_sha256=sha(safe(names[0])), notebook_sha256=sha(safe(names[1])), executed_code_cells=3, execution_method="stdlib_sequential_exec"))
    return data


def package():
    names = [REL/name for name in ("artifact.json", "artifact_build_receipt.json")]
    if any(safe(name).exists() for name in names): raise ValueError("One-shot artifact exists")
    commit, _, review, hashes = guard(); committed(REPORT, commit)
    data = json.loads(safe(REL/"report_data.json").read_text()); receipt = json.loads(safe(REL/"report_prepare_receipt.json").read_text())
    if (receipt["report_data_sha256"] != sha(safe(REL/"report_data.json")) or receipt["notebook_sha256"] != sha(safe(REL/"review.ipynb"))
            or data["source_hashes"] != hashes or receipt["source_hashes"] != hashes or data["review"] != review): raise ValueError("Prepared evidence differs")
    body = safe(REPORT).read_text(); sections = re.split(r"(?m)(?=^## )", body)
    if len(sections) < 2 or not body.startswith("# ") or any(s.startswith("# ") for s in sections[1:]): raise ValueError("Missing report title/sections")
    title = body.splitlines()[0][2:].strip()
    sources = [dict(id=i, label=label, path=str(path)) for i, label, path in [("summary", "V39 saved economic summary", REL/"summary.json"),
        ("review_data", "Saved accounting and exact input hashes", REL/"report_data.json"), ("notebook", "Sequential Python review; not raw replay", REL/"review.ipynb"), ("report", "Reviewed definitions and narrative", REPORT)]]
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    artifact = dict(surface="report", manifest=dict(version=1, surface="report", title=title, generatedAt=stamp,
        blocks=[dict(id="section_"+str(i), type="markdown", layout="full", body=s) for i, s in enumerate(sections)],
        charts=[], cards=[], tables=[], filters=[], sources=sources), snapshot=dict(version=1, status="ready", generatedAt=stamp, datasets={}), sources=sources)
    committed(REPORT, commit)
    if any(sha(safe(p)) != h for p, h in hashes.items()): raise ValueError("Inputs changed during packaging")
    write(names[0], artifact); write(names[1], dict(source_commit=commit, artifact_sha256=sha(safe(names[0])),
        report_sha256=sha(safe(REPORT)), source_hashes=hashes, sections=len(sections), charts=0, canonical_validation="delegated_to_official_packager"))
    return artifact


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("mode", choices=["prepare", "package"])
    mode = parser.parse_args().mode
    prepare() if mode == "prepare" else package()
    print(json.dumps(dict(mode=mode, status="saved_one_shot")))
