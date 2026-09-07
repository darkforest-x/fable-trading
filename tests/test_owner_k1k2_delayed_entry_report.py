"""Synthetic saved-ledger reporting only; no real outputs, prices or git reads."""
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import owner_k1k2_delayed_entry_report as m

E = pd.Timestamp("2023-03-01T12:00Z")


def fixture_tables():
    cases = pd.DataFrame([dict(event_id=f"c{i}", decision_time=E, direction=1, initial_stop=95., signal_atr=2., fold="2023H1") for i in range(63)])
    controls = pd.concat([cases.iloc[:36]]*3, ignore_index=True)
    controls["parent_event_id"] = controls.event_id
    controls["event_id"] = [f"r{i}" for i in range(108)]
    assignments = cases[["event_id"]].assign(match_status=["matched"]*36+["no_match"]*27)
    frames = {name: pd.DataFrame(dict(unused=[])) for name in m.TABLES}
    frames.update(case_requests=cases, control_requests=controls, assignments=assignments)
    summary = dict(metrics={})
    for arm in m.ARMS:
        summary["metrics"][arm] = {}
        for cohort, requests in [("case", cases), ("control", controls)]:
            f = requests.copy()
            f["entry_time"] = E; f["exit_time"] = E+pd.Timedelta(minutes=30)
            f["entry_price"] = 100.; f["exit_price"] = 101.
            f["closed"] = True; f["request_known"] = True; f["outcome"] = "transition_colour_exit"
            f["gross_return"] = .01; f["net_return"] = .008
            f["request_gross"] = .01; f["request_net"] = .008
            if arm == "delayed":
                f.loc[0, ["closed", "entry_time", "exit_time", "outcome"]] = [False, pd.NaT, pd.NaT, "no_fill_expired"]
                f.loc[0, ["gross_return", "net_return"]] = np.nan
                f.loc[0, ["request_gross", "request_net"]] = 0.
            frames[arm+"_"+cohort+"_trades"] = f
            summary["metrics"][arm][cohort] = m.metric(f)
    for cohort in m.COHORTS:
        a, b = [frames[arm+"_"+cohort+"_trades"] for arm in m.ARMS]
        d = a[["event_id"]].copy()
        for measure in ("net", "gross"):
            for arm, f in zip(m.ARMS, (a, b)): d[arm+"_"+measure] = f["request_"+measure]
            d["delta_"+measure] = d["delayed_"+measure]-d["immediate_"+measure]
        d["delta_saved_cost"] = d.delta_net-d.delta_gross
        frames[cohort+"_changes"] = d
    pairs = []
    for i in range(36):
        row = dict(event_id=f"c{i}", complete_pair=True, controls=3)
        for measure in ("net", "gross"):
            for arm in m.ARMS:
                c = frames[arm+"_case_trades"].iloc[i]
                r = frames[arm+"_control_trades"].loc[controls.parent_event_id.eq(f"c{i}")]
                row[arm+"_excess_"+measure] = c["request_"+measure]-r["request_"+measure].mean()
            row["delta_excess_"+measure] = row["delayed_excess_"+measure]-row["immediate_excess_"+measure]
        pairs.append(row)
    frames["paired_contrasts"] = pd.DataFrame(pairs)
    return frames, summary


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ROOT", tmp_path)
    here = tmp_path/m.REL; here.mkdir(parents=True); (tmp_path/m.DATA).mkdir(parents=True)
    frames, summary = fixture_tables()
    config = dict(policy=m.POLICY, output_dir=str(m.DATA), builder_paths=["synthetic/source.py"])
    frozen = {}
    for name in [m.BUILDER, m.TESTS, "synthetic/source.py", str(m.REL/"PROJECT_PLAN.md")]:
        path = tmp_path/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("synthetic")
        frozen[name] = path.read_bytes()
    (here/"config.json").write_text(json.dumps(config)); frozen[str(m.REL/"config.json")] = (here/"config.json").read_bytes()
    files = {}
    for name, frame in frames.items():
        path = tmp_path/m.DATA/(name+".csv"); frame.to_csv(path, index=False, float_format="%.17g")
        files[str(path.relative_to(tmp_path))] = dict(sha256=m.sha(path), rows=len(frame), columns=list(frame))
    summary.update(source_commit="synthetic-engine", config_sha256=m.sha(here/"config.json"), files=files,
        generated_at="2026-09-07T13:00Z", holdout_evaluated=False, training_eligible=False, production_eligible=False)
    (here/"summary.json").write_text(json.dumps(summary))
    (here/"pre_outcome_receipt.json").write_text(json.dumps(dict(source_commit="synthetic-engine",
        config_sha256=summary["config_sha256"], generated_at="2026-09-07T12:00Z", outcomes_materialized=False,
        files={str(m.DATA/(n+".csv")): files[str(m.DATA/(n+".csv"))] for n in ("case_requests", "control_requests", "assignments")})))
    def git(args, **kwargs):
        if args[1] == "rev-parse": return "synthetic-report"
        name = args[2].partition(":")[2]
        if name not in frozen: raise ValueError("Not committed")
        return frozen[name]
    monkeypatch.setattr(m.subprocess, "check_output", git)
    return dict(root=tmp_path, here=here, frames=frames, summary=summary, frozen=frozen)


def test_guard_full_saved_denominators_and_inputs_unchanged(tree):
    before = {name: f.copy(deep=True) for name, f in tree["frames"].items()}
    _, _, review, hashes = m.guard()
    assert review["support"] == dict(cases=63, controls=108, matched_cases=36, unmatched_cases=27)
    assert review["scope"]["inference_recomputed"] is False
    assert str(m.REL/"summary.json") in hashes
    for name in before: pd.testing.assert_frame_equal(before[name], tree["frames"][name])


@pytest.mark.parametrize("fault", ["fee", "cash_fee", "unknown_zero", "identity", "control_parent", "control_direction", "pair", "delta", "summary", "naive", "future"])
def test_evidence_inconsistency_rejected(fault):
    frames, summary = fixture_tables(); f = frames["delayed_case_trades"]
    if fault == "fee": f.loc[1, "net_return"] = .009
    elif fault == "cash_fee": f.loc[0, "request_net"] = -.002
    elif fault == "unknown_zero": f.loc[0, "request_known"] = False
    elif fault == "identity": f.loc[0, "initial_stop"] = 94.
    elif fault == "control_parent": frames["control_requests"].loc[0, "parent_event_id"] = "c50"
    elif fault == "control_direction": frames["control_requests"].loc[0, "direction"] = -1
    elif fault == "pair": frames["paired_contrasts"].loc[0, "delayed_excess_net"] = 0.
    elif fault == "delta": frames["case_changes"].loc[0, "delta_net"] = 0.
    elif fault == "summary": summary["metrics"]["delayed"]["case"]["known"] = 62
    else:
        f["decision_time"] = f.decision_time.astype(object)
        f.loc[0, "decision_time"] = "2023-03-01 12:00" if fault == "naive" else "2025-01-01T00:00Z"
    with pytest.raises((ValueError, AssertionError)): m.verify_tables(frames, summary)


@pytest.mark.parametrize("fault", ["cash_clock", "deadline", "entry_expiry", "invalid_risk", "infinite"])
def test_closed_risk_deadline_and_no_fill_clocks_rejected(fault):
    frames, summary = fixture_tables(); f = frames["delayed_case_trades"]
    if fault == "cash_clock": f.loc[0, "entry_time"] = E
    elif fault == "deadline": f.loc[1, "exit_time"] = E+pd.Timedelta(hours=72, minutes=5)
    elif fault == "entry_expiry": f.loc[1, ["entry_time", "exit_time"]] = E+pd.Timedelta(hours=1)
    elif fault == "invalid_risk": f.loc[1, "entry_price"] = 94.
    else: f.loc[1, "exit_price"] = np.inf
    with pytest.raises(ValueError): m.verify_tables(frames, summary)


def test_genuine_unknown_retained_without_cash_or_complete_population_mean():
    frames, summary = fixture_tables(); f = frames["delayed_case_trades"]
    f.loc[62, ["closed", "request_known", "outcome"]] = [False, False, "right_censored"]
    f.loc[62, ["gross_return", "net_return", "request_gross", "request_net"]] = np.nan
    summary["metrics"]["delayed"]["case"] = m.metric(f)
    d = frames["case_changes"]
    d.loc[62, ["delayed_net", "delayed_gross", "delta_net", "delta_gross", "delta_saved_cost"]] = np.nan
    review = m.verify_tables(frames, summary)
    assert review["metrics"]["delayed"]["case"]["unknown"] == 1
    assert review["metrics"]["delayed"]["case"]["mean_request_net_bp"] is None


@pytest.mark.parametrize("name", ["../bad", "/tmp/bad", "data/../bad", "data\\bad", "https://bad"])
def test_unsafe_identity_rejected(tree, name):
    with pytest.raises(ValueError): m.safe(name)


def test_symlink_rejected(tree):
    (tree["root"]/"shortcut").symlink_to(tree["here"], target_is_directory=True)
    with pytest.raises(ValueError): m.safe("shortcut/summary.json")


@pytest.mark.parametrize("fault", ["source", "csv", "receipt", "whitelist"])
def test_source_and_receipt_rejected_before_prepare(tree, fault):
    if fault == "source": (tree["root"]/m.BUILDER).write_text("changed")
    elif fault == "csv": (tree["root"]/m.DATA/"case_requests.csv").write_text("changed")
    elif fault == "receipt":
        p = tree["here"]/"pre_outcome_receipt.json"; r = json.loads(p.read_text()); r["generated_at"] = "2026-09-07T14:00Z"; p.write_text(json.dumps(r))
    else:
        tree["summary"]["files"]["../bad"] = {}; (tree["here"]/"summary.json").write_text(json.dumps(tree["summary"]))
    with pytest.raises(ValueError): m.prepare()
    assert not (tree["here"]/"report_data.json").exists()


def narrative(tree, commit=True):
    path = tree["root"]/m.REPORT; path.parent.mkdir(parents=True, exist_ok=True)
    body = "# K1/K2 Delayed Entry · V39\n\n## Summary\n\nSynthetic only.\n\n## Counts\n\n| n |\n|--|\n|63|\n\n## Limits\n\nNot a raw replay.\n"
    path.write_text(body)
    if commit: tree["frozen"][m.REPORT] = path.read_bytes()
    return body


def test_prepare_executes_three_cells_and_is_one_shot(tree):
    data = m.prepare(); book = json.loads((tree["here"]/"review.ipynb").read_text())
    codes = [c for c in book["cells"] if c["cell_type"] == "code"]
    assert [c["execution_count"] for c in codes] == [1, 2, 3]
    assert all(c["outputs"][0]["text"] for c in codes)
    assert book["metadata"]["execution"]["jupyter_kernel"] is False
    assert data["review"]["metrics"]["delayed"]["case"]["known_no_fill"] == 1
    with pytest.raises(ValueError, match="One-shot"): m.prepare()


def test_package_preserves_full_markdown_four_safe_sources_and_one_shot(tree):
    m.prepare(); body = narrative(tree); artifact = m.package()
    assert "".join(b["body"] for b in artifact["manifest"]["blocks"]) == body
    assert artifact["manifest"]["title"] == "K1/K2 Delayed Entry · V39"
    assert len(artifact["sources"]) == 4 and not artifact["manifest"]["charts"]
    assert all("sourceId" not in b for b in artifact["manifest"]["blocks"])
    assert all(not Path(s["path"]).is_absolute() for s in artifact["sources"])
    with pytest.raises(ValueError, match="One-shot"): m.package()


def test_package_refuses_uncommitted_md_and_tampered_notebook(tree):
    m.prepare(); narrative(tree, commit=False)
    with pytest.raises(ValueError, match="Not committed"): m.package()
    narrative(tree); (tree["here"]/"review.ipynb").write_text("changed")
    with pytest.raises(ValueError, match="Prepared evidence"): m.package()
