"""Synthetic delivery-only tests: no actual research evidence or price I/O."""
import copy
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import owner_k1k2_delayed_entry_delivery as m


def evidence():
    trades, rows = {}, []
    for arm in m.ARMS:
        f = pd.DataFrame(dict(event_id=[f"c{i}" for i in range(63)], fold=np.repeat(m.FOLDS, [20, 15, 14, 14]),
            request_known=True, closed=True, request_net=np.linspace(-.02, .015, 63)))
        if arm == "delayed": f.loc[0, ["closed", "request_net"]] = [False, 0.]
        trades[arm] = f
        for fold in m.FOLDS:
            g = f.loc[f.fold.eq(fold)]
            rows.append(dict(arm=arm, cohort="case", fold=fold, requests=len(g), known=len(g), unknown=0,
                executed_closed=int(g.closed.sum()), mean_request_net_bp=g.request_net.mean()*1e4))
    return trades, pd.DataFrame(rows)


def old_artifact():
    sources = [dict(id=str(i), label="Synthetic source", path="relative/file"+str(i)) for i in range(4)]
    return dict(surface="report", manifest=dict(version=1, surface="report", title="K1/K2 Delayed Entry · V39",
        generatedAt="2026-09-07T00:00Z", blocks=[dict(id="old", type="markdown", layout="full", body="# Historical rejected text\n")],
        charts=[], tables=[], cards=[], filters=[], sources=sources),
        snapshot=dict(version=1, status="ready", generatedAt="2026-09-07T00:00Z", datasets={}), sources=copy.deepcopy(sources))


def narrative():
    return "# K1/K2 Delayed Entry · V39\n\n## Summary\n\nSynthetic.\n\n"+m.ANCHOR+"\n\n| fold | n |\n|--|--|\n| H1 | 20 |\n\n## Reproduce\n\nUse artifact_final.json.\n"


def test_eight_rows_request_not_trade_denominator_and_originals_unchanged():
    trades, folds = evidence(); before = copy.deepcopy(trades)
    rows = m.rows_from_saved(trades, folds)
    assert len(rows) == 8
    assert [(r["fold"], r["arm"]) for r in rows] == [(f, a) for f in m.FOLDS for a in m.ARMS]
    delayed = rows[1]
    assert delayed["requests"] == 20 and delayed["executed_closed"] == 19
    assert delayed["mean_request_net_bp"] == pytest.approx(trades["delayed"].iloc[:20].request_net.sum()/20*1e4)
    for arm in m.ARMS: pd.testing.assert_frame_equal(trades[arm], before[arm])


def test_unknown_fold_mean_stays_null_not_known_subset_or_zero():
    trades, folds = evidence(); f = trades["delayed"]
    f.loc[0, ["request_known", "request_net"]] = [False, np.nan]
    idx = folds.arm.eq("delayed") & folds.fold.eq("2023H1")
    folds.loc[idx, ["known", "unknown", "mean_request_net_bp"]] = [19, 1, np.nan]
    rows = m.rows_from_saved(trades, folds)
    assert rows[1]["mean_request_net_bp"] is None and rows[1]["unknown"] == 1
    json.dumps(rows, allow_nan=False)


@pytest.mark.parametrize("fault", ["mean", "count", "duplicate", "missing_fold", "unknown_zero", "identity", "missing_case"])
def test_saved_mean_count_identity_mutations_rejected(fault):
    trades, folds = evidence()
    if fault == "mean": folds.loc[0, "mean_request_net_bp"] += 1
    elif fault == "count": folds.loc[0, "requests"] -= 1
    elif fault == "duplicate": folds = pd.concat([folds, folds.iloc[:1]])
    elif fault == "missing_fold": folds = folds.iloc[1:]
    elif fault == "unknown_zero": trades["delayed"].loc[0, "request_known"] = False
    elif fault == "identity": trades["delayed"].loc[0, "event_id"] = "changed"
    else: trades["delayed"] = trades["delayed"].iloc[1:]
    with pytest.raises(ValueError): m.rows_from_saved(trades, folds)


def test_chart_preserves_current_markdown_original_artifact_and_safe_native_spec():
    old = old_artifact(); before = copy.deepcopy(old); trades, folds = evidence()
    result = m.build_artifact(old, narrative(), m.rows_from_saved(trades, folds), "2026-09-07T01:00Z")
    assert old == before
    blocks = result["manifest"]["blocks"]
    assert "".join(b["body"] for b in blocks if b["type"] == "markdown") == narrative()
    index = next(i for i, b in enumerate(blocks) if b["type"] == "chart")
    assert blocks[index-1]["body"].startswith(m.ANCHOR)
    chart = result["manifest"]["charts"][0]
    assert chart["referenceLines"][0]["value"] == 0 and chart["settings"]["groupMode"] == "grouped"
    assert chart["palette"]["name"] == "blueGold" and chart["encodings"]["color"]["field"] == "arm"
    assert len(result["snapshot"]["datasets"]) == 1 and len(result["sources"]) == 5
    assert result["sources"][:-1] == before["sources"]
    assert result["sources"][-1]["query"]["sql"] == m.SQL
    json.dumps(result, allow_nan=False)


def test_documented_sql_really_executes(monkeypatch):
    calls = []; read = m.pd.read_sql_query
    def tracked(sql, connection):
        calls.append(sql)
        return read(sql, connection)
    monkeypatch.setattr(m.pd, "read_sql_query", tracked)
    trades, folds = evidence()
    assert len(m.rows_from_saved(trades, folds)) == 8 and calls == [m.SQL]


@pytest.mark.parametrize("fault", ["title", "anchor", "duplicate_anchor", "existing_chart"])
def test_native_artifact_rejects_ambiguous_insertions(fault):
    old = old_artifact(); body = narrative(); trades, folds = evidence()
    if fault == "title": body = body.replace("K1/K2", "Different")
    elif fault == "anchor": body = body.replace(m.ANCHOR, "## Other")
    elif fault == "duplicate_anchor": body += m.ANCHOR+"\nAgain\n"
    else: old["manifest"]["charts"] = [dict(id="old")]
    with pytest.raises(ValueError): m.build_artifact(old, body, m.rows_from_saved(trades, folds), "stamp")


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "ROOT", tmp_path)
    here = tmp_path/m.REL; here.mkdir(parents=True); (tmp_path/m.DATA).mkdir(parents=True)
    frozen = {}
    for name, text in [(m.BUILDER, "synthetic source"), (m.TESTS, "synthetic tests"), (m.REPORT, narrative())]:
        p = tmp_path/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text); frozen[name] = p.read_bytes()
    (here/"artifact.json").write_text(json.dumps(old_artifact()))
    trades, folds = evidence(); files = {}
    for name, f in [("fold_metrics", folds)]+[(a+"_case_trades", trades[a]) for a in m.ARMS]:
        path = m.DATA/(name+".csv"); f.to_csv(tmp_path/path, index=False, float_format="%.17g")
        files[str(path)] = dict(sha256=m.sha(path), rows=len(f), columns=list(f))
    (here/"summary.json").write_text(json.dumps(dict(files=files)))
    monkeypatch.setattr(m, "OLD_SHA", m.sha(m.REL/"artifact.json")); monkeypatch.setattr(m, "SUMMARY_SHA", m.sha(m.REL/"summary.json"))
    (here/"artifact_build_receipt.json").write_text(json.dumps(dict(artifact_sha256=m.OLD_SHA, source_hashes={str(m.REL/"summary.json"): m.SUMMARY_SHA})))
    def git(args, **kwargs):
        if args[1] == "rev-parse": return "synthetic-report-commit"
        return frozen[args[2].partition(":")[2]]
    monkeypatch.setattr(m.subprocess, "check_output", git)
    return tmp_path, here


def test_one_shot_new_files_preserve_old_bytes(tree):
    root, here = tree
    before = {p: p.read_bytes() for p in here.iterdir()}
    receipt = m.run()
    assert receipt["rows"] == 8 and receipt["original_artifact_preserved"] is True
    assert all(p.read_bytes() == value for p, value in before.items())
    with pytest.raises(ValueError, match="One-shot"): m.run()


@pytest.mark.parametrize("fault", ["builder", "md", "csv", "old_artifact", "summary"])
def test_source_first_and_saved_hashes_rejected_before_new_output(tree, fault):
    root, here = tree
    path = {"builder": root/m.BUILDER, "md": root/m.REPORT, "csv": root/m.DATA/"fold_metrics.csv", "old_artifact": here/"artifact.json", "summary": here/"summary.json"}[fault]
    path.write_text("changed")
    with pytest.raises(ValueError): m.run()
    assert not (here/"artifact_final.json").exists()


@pytest.mark.parametrize("path", ["../bad", "/tmp/bad", "data/../bad", "data\\bad", "https://bad"])
def test_unsafe_paths_rejected(tree, path):
    with pytest.raises(ValueError): m.safe(path)
