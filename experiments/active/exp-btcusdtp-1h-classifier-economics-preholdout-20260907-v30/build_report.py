"""Saved V30 report preparation and canonical portable-artifact packaging.

Real SQLite descriptive queries, no new labels/selection/inference. Sources
are hash-verified before and after. Explicit one-to-one joins preserve all
251 original mothers and retain unknowns separately. Query SQL is provenance.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import sqlite3
import subprocess

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
REL = HERE.relative_to(ROOT)
REPORT = Path("analysis/p1_btcusdtp_hourly_classifier_economics_v30_20260907.md")
TITLE = "Classifier Economics · V30"
QUERIES = {
 "groups": """SELECT c.classifier_gate_state AS state,COUNT(*) AS events,
 AVG(c.gross_markout)*10000 AS gross_bp,AVG(c.cost_threshold_markout)*10000 AS net_bp,
 AVG(c.policy_cost_threshold_markout)*10000 AS policy_bp,
 SUM(c.cost_threshold_markout>0) AS positive,
 SUM(c.gross_markout<=0) AS wrong_direction,
 SUM(c.gross_markout>0 AND c.cost_threshold_markout<=0) AS cost_erased,
 AVG(m.control_mean_cost_threshold_markout)*10000 AS controls_bp,
 AVG(m.cost_threshold_excess_markout)*10000 AS excess_bp,
 COUNT(m.cost_threshold_excess_markout) AS paired_n
 FROM cases c JOIN mothers m USING(event_id,horizon_hours)
 WHERE c.horizon_hours=4 GROUP BY c.classifier_gate_state ORDER BY state""",
 "halves": """SELECT c.fold,COUNT(*) AS original_n,
 SUM(c.classifier_gate_state='accepted') AS accepted_n,
 AVG(c.cost_threshold_markout)*10000 AS baseline_bp,
 AVG(CASE WHEN c.classifier_gate_state='accepted' THEN c.cost_threshold_markout END)*10000 AS accepted_bp,
 AVG(CASE WHEN c.classifier_gate_state='accepted' THEN m.control_mean_cost_threshold_markout END)*10000 AS accepted_controls_bp,
 AVG(c.policy_cost_threshold_markout)*10000 AS policy_bp,
 AVG(m.control_policy_mean)*10000 AS control_policy_bp,
 AVG(m.policy_excess)*10000 AS policy_excess_bp
 FROM cases c JOIN mothers m USING(event_id,horizon_hours)
 WHERE c.horizon_hours=4 GROUP BY c.fold ORDER BY c.fold""",
 "monthly": """SELECT mother_month AS month,COUNT(*) AS events,
 AVG(accepted_cost)*10000 AS accepted_bp,
 AVG(control_mean_cost_threshold_markout)*10000 AS controls_bp,
 AVG(accepted_excess)*10000 AS excess_bp,
 SUM(accepted_cost>0) AS positive
 FROM mothers WHERE horizon_hours=4 AND classifier_gate_state='accepted'
 GROUP BY mother_month ORDER BY mother_month""",
 "failure_reasons": """WITH flags AS (SELECT c.*,
 ((c.direction=1 AND c.classifier_center>c.classifier_previous_center) OR
  (c.direction=-1 AND c.classifier_center<=c.classifier_previous_center)) slope_pass,
 ((c.direction=1 AND c.signal_close>c.classifier_center+c.classifier_step) OR
  (c.direction=-1 AND c.signal_close<c.classifier_center-c.classifier_step)) distance_pass
 FROM contexts c), categories AS (SELECT *,CASE WHEN classifier_known=0 THEN 'unknown'
 WHEN slope_pass AND distance_pass THEN 'accepted' WHEN slope_pass THEN 'distance_only'
 WHEN distance_pass THEN 'slope_only' ELSE 'slope_and_distance' END AS reason FROM flags)
 SELECT c.reason,COUNT(*) AS events,AVG(l.gross_markout)*10000 AS gross_bp,
 AVG(l.cost_threshold_markout)*10000 AS net_bp,SUM(l.cost_threshold_markout>0) AS positive,
 AVG(m.control_mean_cost_threshold_markout)*10000 AS controls_bp,
 AVG(m.cost_threshold_excess_markout)*10000 AS excess_bp,COUNT(m.cost_threshold_excess_markout) AS paired_n
 FROM categories c JOIN cases l ON c.event_id=l.event_id AND l.horizon_hours=4
 JOIN mothers m ON m.event_id=l.event_id AND m.horizon_hours=l.horizon_hours
 GROUP BY c.reason ORDER BY c.reason""",
 "loss_ledger": """SELECT c.event_id,c.decision_time,c.fold,c.direction,c.classifier_gate_state,
 c.gross_markout*10000 AS gross_bp,c.cost_threshold_markout*10000 AS net_bp,
 m.control_mean_cost_threshold_markout*10000 AS controls_bp,
 m.cost_threshold_excess_markout*10000 AS excess_bp,
 CASE WHEN c.gross_markout<=0 THEN 'wrong_direction'
 WHEN c.cost_threshold_markout<=0 THEN 'cost_erased' ELSE 'above_cost' END AS outcome_type
 FROM cases c JOIN mothers m USING(event_id,horizon_hours)
 WHERE c.horizon_hours=4 ORDER BY c.decision_time,c.event_id""",
 "policy_decomposition": """SELECT COUNT(*) AS known_gate_n,
 SUM(classifier_gate_state='abstain') AS abstain_n,
 AVG(CASE WHEN classifier_gate_state='abstain' THEN 0.002 ELSE 0 END)*10000 AS avoided_cost_bp,
 AVG(CASE WHEN classifier_gate_state='abstain' THEN -gross_markout ELSE 0 END)*10000 AS avoided_gross_bp,
 AVG(policy_cost_threshold_markout-cost_threshold_markout)*10000 AS delta_bp,
 AVG(cost_threshold_markout)*10000 AS baseline_bp,
 AVG(policy_cost_threshold_markout)*10000 AS policy_bp
 FROM cases WHERE horizon_hours=4 AND classifier_gate_state!='unknown'""",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def query_records(con, query):
    frame = pd.read_sql_query(query, con)
    return frame.astype(object).where(pd.notna(frame), None).to_dict("records")


def checked():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if subprocess.check_output(["git", "show", commit+":"+str(REL/"build_report.py")], cwd=ROOT) != Path(__file__).read_bytes():
        raise ValueError("Commit report builder first")
    summary = json.loads((HERE/"results/summary.json").read_text())
    audit = json.loads((HERE/"audit.json").read_text())
    config = json.loads((HERE/"config.json").read_text())
    if audit["status"] != "passed" or audit["summary_sha256"] != sha(HERE/"results/summary.json"):
        raise ValueError("Saved-numeric audit missing/drifted")
    for path, value in config["inputs"].items():
        if sha(ROOT/path) != value:
            raise ValueError("Saved input drift")
    for name, value in summary["output_hashes"].items():
        if sha(HERE/"results"/name) != value:
            raise ValueError("Saved output drift")
    return commit, summary, config


def prepare():
    commit, summary, config = checked()
    context = next(path for path in config["inputs"] if path.endswith("/case_context.csv.gz"))
    with sqlite3.connect(":memory:") as con:
        for table, path in (("cases", HERE/"results/case_ledger.csv.gz"),
                            ("mothers", HERE/"results/mother_ledger.csv.gz"), ("contexts", ROOT/context)):
            pd.read_csv(path).to_sql(table, con, index=False)
        data = {name: query_records(con, query) for name, query in QUERIES.items()}
    if sum(r["events"] for r in data["groups"]) != 251 or len(data["loss_ledger"]) != 251:
        raise ValueError("Report joins changed mother grid")
    expected = dict(accepted=78, distance_only=67, slope_only=33, slope_and_distance=70, unknown=3)
    if {r["reason"]: r["events"] for r in data["failure_reasons"]} != expected or len(data["monthly"]) != 24:
        raise ValueError("Diagnostic categories/month coverage drifted")
    if checked()[:2] != (commit, summary):
        raise ValueError("Inputs changed during report query")
    write(HERE/"report_data.json", dict(data=data, queries=QUERIES, source_commit=commit,
        summary_sha256=sha(HERE/"results/summary.json"), generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        additional_inference=False, raw_prices_read=False))
    print(json.dumps({k:(v[:10] if k in ("monthly", "loss_ledger") else v) for k,v in data.items()}, ensure_ascii=False, indent=2))


def package():
    commit, summary, _ = checked()
    saved = json.loads((HERE/"report_data.json").read_text())
    if saved["summary_sha256"] != sha(HERE/"results/summary.json"):
        raise ValueError("Report data drift")
    md = (ROOT/REPORT).read_text()
    sections = [s.strip() for s in re.split(r"(?m)(?=^## )", md.strip())]
    if not md.startswith("# "+TITLE+"\n") or len(sections) < 8:
        raise ValueError("Incomplete technical narrative")
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    sources = [dict(id="report", label="V30 · 完整指标、方法与风险", path=str(REPORT))]
    for name in ("monthly", "groups"):
        sources.append(dict(id=name, label="V30 · 保存逐母标签的真实描述查询", path=str(REL/"report_data.json"),
            query=dict(sql=QUERIES[name], engine="sqlite", language="sql", tables_used=["cases", "mothers"],
                executed_at=saved["generated_at"], description="固定4h标签，非账户净值或执行收益",
                filters=["2023–2024 UTC; frozen original251 and own744 controls; no new selection"],
                metric_definitions={"net_bp":"(direction*(open[E+4h]-open[E])/open[E]-0.002)*10000; group arithmetic mean",
                    "controls_bp":"mean of each original three-control mean; no classifier filtering in conditional comparator"})))
    monthly = []
    for row in saved["data"]["monthly"]:
        for series, field in (("Accepted K1", "accepted_bp"), ("Original controls", "controls_bp")):
            monthly.append(dict(row, series=series, net_bp=row[field]))
    common = dict(showDescription=True, palette=dict(kind="categorical", name="blueGold"))
    charts = [dict(id="monthly", type="line", title="逐月固定4h成本后均值",
        description="2023–2024 UTC · 78个保留入口与原234控制，月内事件加权；不是净值曲线",
        dataset="monthly", sourceId="monthly", **common,
        encodings=dict(x=dict(field="month", type="temporal", label="UTC月份"),
            y=dict(field="net_bp", type="quantitative", label="bp"), color=dict(field="series", type="nominal", label="组别"),
            tooltip=[dict(field="events", type="quantitative", label="母事件数"),dict(field="positive", type="quantitative", label="成本后正向母事件数")]))]
    # Exact subgroup comparisons are tables in the narrative; the single chart
    # is the 24-point time pattern, not decorative duplication of those tables.
    blocks = []
    for i, section in enumerate(sections):
        blocks.append(dict(id="section_"+str(i), type="markdown", layout="full", body=section, **({"sourceId":"report"} if i else {})))
        if section.startswith("## 四个半年"):
            blocks.append(dict(id="monthly_chart", type="chart", layout="full", chartId="monthly"))
    if sum(block["type"] == "chart" for block in blocks) != 1:
        raise ValueError("Missing monthly narrative chart")
    artifact = dict(surface="report", manifest=dict(version=1, surface="report", title=TITLE, generatedAt=stamp,
        filters=[], cards=[], charts=charts, tables=[], blocks=blocks, sources=sources),
        snapshot=dict(version=1, generatedAt=stamp, status="ready", datasets=dict(monthly=monthly)), sources=sources)
    write(HERE/"artifact.json", artifact)
    write(HERE/"artifact_build_receipt.json", dict(source_commit=commit, report_sha256=sha(ROOT/REPORT),
        report_data_sha256=sha(HERE/"report_data.json"), summary_sha256=sha(HERE/"results/summary.json"),
        sections=len(sections), charts=1, all_sections_preserved=True, generated_at=stamp))
    print(json.dumps(dict(sections=len(sections), charts=1, monthly_rows=len(monthly))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "package"])
    args = parser.parse_args()
    prepare() if args.phase == "prepare" else package()
