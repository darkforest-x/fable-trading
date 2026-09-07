"""Source-first V39 delivery repair; preserve the rejected artifact and receipts.

Chart contract: compare original-case request net means across four fixed half
years, immediate versus delayed; eight rows, grouped native bar, blue/gold,
neutral zero reference, full-width after temporal-stability prose. This is not
account equity or an OOS test. Unknown request means remain null, never zero.
Only saved V39 files are read; no raw OHLC, economic replay or inference rerun.
Canonical chart schema: installed analytics-app/charting/chart-contract.ts and
scripts/chart_contract.py; official portable packager owns validation/rendering.
"""
import copy
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
REL = Path("experiments/active/exp-btcusdtp-owner-k1k2-delayed-entry-20260907-v39")
DATA = Path("data/owner_k1k2_delayed_entry_v39")
REPORT = "analysis/p1_btcusdtp_owner_k1k2_delayed_entry_v39_20260907.md"
BUILDER = "yoyo/evaluation/owner_k1k2_delayed_entry_delivery.py"
TESTS = "tests/test_owner_k1k2_delayed_entry_delivery.py"
OLD_SHA = "f7daae5f3ff49a84221b821658d937edf6792d8d25189de324b9cafeab2b1436"
SUMMARY_SHA = "de1cf77e6a5de534c4d5fc6995958d13cee846581ea98a2ace0aacc11ab74d48"
FOLDS = ("2023H1", "2023H2", "2024H1", "2024H2")
ARMS = ("immediate", "delayed")
ANCHOR = "## 匹配对照与时间稳定性"
SQL = """SELECT fold, arm, CASE WHEN SUM(request_known)=COUNT(*)
THEN SUM(request_net)*10000.0/COUNT(*) ELSE NULL END AS mean_request_net_bp,
COUNT(*) AS requests, SUM(request_known) AS known, COUNT(*)-SUM(request_known) AS unknown,
SUM(closed) AS executed_closed FROM saved_case_requests GROUP BY fold, arm
ORDER BY fold, CASE arm WHEN 'immediate' THEN 0 ELSE 1 END"""


def safe(name):
    p = Path(name); result = ROOT/p
    if p.is_absolute() or any(s in str(name) for s in ("..", "\\", ":")) or result.resolve() != result.absolute(): raise ValueError("Unsafe path")
    return result


def sha(name): return hashlib.sha256(safe(name).read_bytes()).hexdigest()


def committed(name, commit):
    if subprocess.check_output(["git", "show", commit+":"+str(name)], cwd=ROOT) != safe(name).read_bytes(): raise ValueError("Commit exact source/MD first")


def rows_from_saved(trades, folds):
    """Original request sum/n, not surviving known/traded-subset average."""
    selected = folds.loc[folds.cohort.eq("case")].set_index(["arm", "fold"], verify_integrity=True)
    if set(selected.index) != {(a, f) for a in ARMS for f in FOLDS}: raise ValueError("Four-fold eight-row contract")
    rows = []; identities = None
    for arm in ARMS:
        frame = trades[arm]
        if len(frame) != 63 or frame.event_id.isna().any() or not frame.event_id.is_unique or set(frame.fold) != set(FOLDS): raise ValueError("Original case population")
        own = frame.set_index("event_id").fold.to_dict()
        if identities is not None and own != identities: raise ValueError("Original fold identity differs")
        identities = own
        for flag in ("request_known", "closed"):
            if not frame[flag].map(lambda v: isinstance(v, (bool, np.bool_))).all(): raise ValueError("Unknown boolean")
        if (frame.loc[~frame.request_known, "request_net"].notna().any() or not np.isfinite(frame.loc[frame.request_known, "request_net"]).all()): raise ValueError("Unknown/known net confusion")
        for fold in FOLDS:
            g = frame.loc[frame.fold.eq(fold)]; saved = selected.loc[arm, fold]
            value = float(g.request_net.sum()/len(g)*1e4) if g.request_known.all() else None
            counts = dict(requests=len(g), known=int(g.request_known.sum()), unknown=int((~g.request_known).sum()), executed_closed=int(g.closed.sum()))
            if any(saved[k] != v for k, v in counts.items()): raise ValueError("Fold denominator differs")
            if value is None:
                if pd.notna(saved.mean_request_net_bp): raise ValueError("Unknown mean reported known")
            elif not np.isclose(saved.mean_request_net_bp, value, rtol=1e-12, atol=1e-12): raise ValueError("Fold mean differs")
            rows.append(dict(arm=arm, fold=fold, mean_request_net_bp=value, **counts))
    rows = sorted(rows, key=lambda r: (FOLDS.index(r["fold"]), ARMS.index(r["arm"])))
    with sqlite3.connect(":memory:") as con:
        pd.concat([trades[a][["fold", "request_net", "request_known", "closed"]].assign(arm=a) for a in ARMS]).to_sql("saved_case_requests", con, index=False)
        sql_rows = pd.read_sql_query(SQL, con)
    pd.testing.assert_frame_equal(sql_rows, pd.DataFrame(rows)[list(sql_rows)], check_dtype=False, check_exact=False, rtol=1e-12, atol=1e-12)
    return [{k: None if pd.isna(v) else v for k, v in r.items()} for r in sql_rows.to_dict("records")]


def build_artifact(original, markdown, rows, stamp):
    artifact = copy.deepcopy(original); manifest = artifact["manifest"]
    if manifest["charts"] or artifact["snapshot"]["datasets"] or any(b["type"] != "markdown" for b in manifest["blocks"]): raise ValueError("Expected rejected MD-only artifact")
    if not markdown.startswith("# "+manifest["title"]+"\n"): raise ValueError("Report title drift")
    sections = re.split(r"(?m)(?=^## )", markdown)
    if sum(s.splitlines()[0] == ANCHOR for s in sections) != 1: raise ValueError("Unique temporal section required")
    blocks = []
    for i, section in enumerate(sections):
        blocks.append(dict(id="section_"+str(i), type="markdown", layout="full", body=section))
        if section.splitlines()[0] == ANCHOR: blocks.append(dict(id="fold_comparison", type="chart", layout="full", chartId="fold_net"))
    source = dict(id="fold_net_source", label="V39 · 四半年原请求净均值", path=str(DATA/"fold_metrics.csv"),
        query=dict(sql=SQL, language="sql", engine="sqlite", id=BUILDER+":rows_from_saved", executed_at=stamp,
            tables_used=["main.saved_case_requests"],
            description="Actual in-memory SQLite aggregation of saved data/owner_k1k2_delayed_entry_v39/immediate_case_trades.csv and data/owner_k1k2_delayed_entry_v39/delayed_case_trades.csv; independently reconciled in Python to data/owner_k1k2_delayed_entry_v39/fold_metrics.csv. SQL did not perform the original economic replay.",
            filters=["Cases only, all original63 requests in each arm, four fixed2023–2024 half-years; unknowns retained"],
            metric_definitions=dict(mean_request_net_bp="Original-request mean in bp; known no-fill contributes0; any unknown makes full-fold mean null", requests="All original case requests in this half-year")))
    if any(s["id"] == source["id"] for s in manifest["sources"]): raise ValueError("Duplicate chart source")
    chart = dict(id="fold_net", type="bar", title="四半年原请求净均值 · 两种入场方式", dataset="fold_net", sourceId=source["id"],
        description="2023–2024；每臂63个原请求，分半年；bp/请求，未成交已知为0。不是账户累计收益；未知均值不填0。", showDescription=True,
        palette=dict(kind="categorical", name="blueGold"), legend=dict(position="bottom", title="Entry policy"),
        settings=dict(groupMode="grouped", sort="none"), referenceLines=[dict(axis="y", value=0, color="neutral", label="0 bp")],
        encodings=dict(x=dict(field="fold", type="ordinal"), y=dict(field="mean_request_net_bp", type="quantitative", unit="bp"),
            color=dict(field="arm", type="nominal"), tooltip=[dict(field=k, type="quantitative") for k in ("mean_request_net_bp", "requests", "known", "unknown", "executed_closed")]))
    manifest.update(blocks=blocks, charts=[chart], generatedAt=stamp, sources=manifest["sources"]+[source])
    artifact["sources"] = copy.deepcopy(manifest["sources"]); artifact["snapshot"].update(generatedAt=stamp, datasets=dict(fold_net=rows))
    return artifact


def run():
    outputs = [REL/"artifact_final.json", REL/"artifact_final_receipt.json"]
    if any(safe(p).exists() for p in outputs): raise ValueError("One-shot delivery exists")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    for name in (BUILDER, TESTS, REPORT): committed(name, commit)
    if sha(REL/"artifact.json") != OLD_SHA or sha(REL/"summary.json") != SUMMARY_SHA: raise ValueError("Frozen original artifact/summary drift")
    receipt = json.loads(safe(REL/"artifact_build_receipt.json").read_text()); summary = json.loads(safe(REL/"summary.json").read_text())
    if receipt["artifact_sha256"] != OLD_SHA or receipt["source_hashes"][str(REL/"summary.json")] != SUMMARY_SHA: raise ValueError("Original receipt linkage differs")
    hashes = {str(p): sha(p) for p in (BUILDER, TESTS, REPORT, REL/"artifact.json", REL/"artifact_build_receipt.json", REL/"summary.json")}
    frames = {}
    for name in ("fold_metrics", "immediate_case_trades", "delayed_case_trades"):
        path = DATA/(name+".csv"); meta = summary["files"][str(path)]
        if sha(path) != meta["sha256"]: raise ValueError("Saved CSV SHA drift")
        f = pd.read_csv(safe(path), float_precision="round_trip")
        if len(f) != meta["rows"] or list(f) != meta["columns"]: raise ValueError("Saved CSV shape drift")
        hashes[str(path)] = meta["sha256"]; frames[name] = f
    rows = rows_from_saved({a: frames[a+"_case_trades"] for a in ARMS}, frames["fold_metrics"])
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    artifact = build_artifact(json.loads(safe(REL/"artifact.json").read_text()), safe(REPORT).read_text(), rows, stamp)
    if any(sha(p) != h for p, h in hashes.items()): raise ValueError("Sources changed during delivery")
    for name in (BUILDER, TESTS, REPORT): committed(name, commit)
    with safe(outputs[0]).open("x") as out: json.dump(artifact, out, ensure_ascii=False, indent=2, allow_nan=False)
    result = dict(source_commit=commit, generated_at=stamp, inputs=hashes, artifact_sha256=sha(outputs[0]), rows=8, charts=1,
        economic_replay=False, original_artifact_preserved=True, canonical_validation="pending_official_packager")
    with safe(outputs[1]).open("x") as out: json.dump(result, out, ensure_ascii=False, indent=2, allow_nan=False)
    return result


if __name__ == "__main__":
    run(); print(json.dumps(dict(status="saved_one_shot", artifact=str(REL/"artifact_final.json"))))
