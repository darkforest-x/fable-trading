"""V10 native report from saved pre-entry support evidence, never price outcomes.

Data Analytics0.2.10 canonical artifact contract owns HTML rendering. The sole
chart counts first ordered stage below three controls among unmatched mothers;
this is a descriptive decomposition, not causal importance of each exclusion.
Source columns are event_id,match_status and ordered *_count fields saved by
hourly_impulse_matching_support. No extra historical windows are introduced.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import sqlite3

import pandas as pd

from yoyo.evaluation.hourly_impulse_management_report import _fence, SOURCE_MARKER
from yoyo.evaluation.hourly_impulse_report import ROOT, safe_identity


MARKER = "<!-- V10_SHORTAGE -->"
STAGES = ["same_month", "same_utc6h", "same_vol_bucket", "same_5m_colour", "same_hourly_colour",
          "same_slope", "fold_embargo", "vol_support", "atr_support", "entry_open_support",
          "entry_continuity_support", "five_minute_support", "hourly_support", "cross_exclusion",
          "actual_mother_exclusion", "positive_synthetic_stop", "unused_before"]
LABELS = {"same_slope": "六键齐全后不足", "fold_embargo": "期末禁入后不足",
          "cross_exclusion": "排除本小时/前小时穿线后不足", "unused_before": "共用供给已占用",
          "missing_support": "母样本因果支持缺失"}
SQL = """SELECT s.stage, MAX(s.stage_label) AS stage_label,
 COUNT(*) AS mother_count, c.all_mothers, c.unmatched_mothers,
 c.matched_mothers, 3 AS required_controls,
 1.0 * COUNT(*) / c.unmatched_mothers AS share_unmatched
FROM main.shortages s CROSS JOIN main.cohort c
GROUP BY s.stage, c.all_mothers, c.unmatched_mothers, c.matched_mothers
ORDER BY MIN(s.stage_order)"""


def shortage_rows(audit, summary):
    """Every unmatched original mother has exactly one ordered first-shortage stage."""
    if len(audit) != summary["mothers"] or audit.event_id.isna().any() or not audit.event_id.is_unique:
        raise ValueError("Full unique original mother population required")
    if audit.match_status.value_counts().to_dict() != summary["old_status_counts"]:
        raise ValueError("Saved support status counts disagree")
    classified = []
    for row in audit.to_dict("records"):
        if row["match_status"] == "matched":
            if row["unused_before_count"] < 3:
                raise ValueError("Matched mother lacks three controls")
            continue
        if row["match_status"] == "missing_causal_matching_support":
            stage = "missing_support"
        elif row["match_status"] == "insufficient_exact_controls":
            values = [row[stage+"_count"] for stage in STAGES]
            if any(pd.isna(v) or v < 0 or int(v) != v for v in values):
                raise ValueError("Search-reached stage counts must be finite nonnegative integers")
            if any(right > left for left, right in zip(values, values[1:])):
                raise ValueError("Ordered counts must not increase")
            stage = next((name for name, value in zip(STAGES, values) if value < 3), None)
            if stage is None:
                raise ValueError("Unmatched row does not have a shortage")
        else:
            raise ValueError("Unknown original support status")
        classified.append({"event_id":row["event_id"],"stage":stage,
            "stage_label":LABELS.get(stage,stage),"stage_order":(STAGES+["missing_support"]).index(stage)})
    # Python validates each ordered row; this SQL really aggregates the resulting
    # maternal classifications, rather than pretending SQL computed the features.
    with sqlite3.connect(":memory:") as db:
        pd.DataFrame(classified,columns=["event_id","stage","stage_label","stage_order"]).to_sql("shortages",db,index=False)
        pd.DataFrame([{"all_mothers":len(audit),"unmatched_mothers":len(classified),
            "matched_mothers":summary["greedy_matched"]}]).to_sql("cohort",db,index=False)
        return pd.read_sql_query(SQL,db).to_dict("records")


def sections(markdown):
    if re.search(r"(?:/Users/|/tmp/|/home/|file://)", markdown):
        raise ValueError("Portable narrative cannot contain machine-local paths")
    lines=markdown.strip().splitlines()
    if not lines or not re.match(r"^# [^#]",lines[0]):
        raise ValueError("Report requires one title")
    title=lines[0][2:].strip()
    groups, current, active=[],[],None
    for line in lines[1:]:
        outside=active is None
        active=_fence(line,active)
        if outside and line.startswith("## "):
            if any(x.strip() for x in current): groups.append(current)
            current=[line]
        else: current.append(line)
    if active is not None: raise ValueError("Unclosed code fence")
    if any(x.strip() for x in current): groups.append(current)
    blocks=[{"id":"title","type":"markdown","body":lines[0],"layout":"full"}]
    count=0
    for i,group in enumerate(groups):
        if not next(x for x in group if x.strip()).startswith("## "):
            raise ValueError("All narrative needs a peer section")
        body,source,chart,active=[],None,False,None
        for line in group:
            outside=active is None
            active=_fence(line,active)
            match=SOURCE_MARKER.match(line) if outside else None
            if match:
                if source: raise ValueError("Duplicate source directive")
                source=match.group(1)
            elif outside and line.strip()==MARKER:
                chart=True; count+=1
            else:
                if outside and MARKER in line: raise ValueError("Standalone marker required")
                if chart and line.strip(): raise ValueError("Chart marker must end its section")
                body.append(line)
        block={"id":f"section_{i}","type":"markdown","body":"\n".join(body).strip(),"layout":"full"}
        if source: block["sourceId"]=source
        blocks.append(block)
        if chart: blocks.append({"id":"shortage_chart","type":"chart","chartId":"shortage","layout":"full"})
    if count!=1: raise ValueError("Exactly one shortage chart directive required")
    return title,blocks


def build_artifact(markdown, summary, audit, *, markdown_path, summary_path, audit_path, generated_at):
    title,blocks=sections(markdown)
    rows=shortage_rows(audit,summary)
    identities={"report":safe_identity(markdown_path),"v10_summary":safe_identity(summary_path),
                "v10_mothers":safe_identity(audit_path),
                "presentation_code":"yoyo/evaluation/hourly_impulse_support_report.py"}
    if len(set(identities.values()))!=len(identities): raise ValueError("Evidence identities must differ")
    if {b["sourceId"] for b in blocks if "sourceId" in b}-set(identities): raise ValueError("Unknown source")
    timestamp=pd.to_datetime(generated_at,utc=True,errors="raise")
    if pd.isna(timestamp): raise ValueError("Finite timestamp required")
    timestamp=timestamp.isoformat()
    sources=[{"id":name,"label":{"report":"V10 · 完整技术报告","v10_summary":"V10 · 保存的容量证书",
        "v10_mothers":"V10 · 全部原始母样本支持账本","presentation_code":"V10 · 顺序计数及报告代码"}[name],"path":path}
        for name,path in identities.items()]
    next(source for source in sources if source["id"]=="v10_mothers")["query"]={
        "engine":"SQLite","language":"sql","sql":SQL,"executed_at":timestamp,
        "tables_used":["main.shortages","main.cohort"],
        "description":"Actual SQLite aggregation in shortage_rows over classifications derived from "+identities["v10_mothers"]+". Python first validates full maternal IDs/status counts and classifies each row at the first frozen stage below three; main.shortages holds event_id,stage,stage_label,stage_order. main.cohort holds original/matched/unmatched denominators. SQL aggregates these classifications; it does not compute trading features or returns.",
        "filters":["All original BTC-USDT-SWAP2023-2024 mothers; only unmatched mothers enter the shortage chart, no outcome filtering",
                   "Missing causal support has its own category, not fabricated zero candidate supply"],
        "metric_definitions":["mother_count counts unmatched maternal IDs once, not unique candidate timestamps or mother-candidate edges",
            "First shortage means first frozen ordered stage count below3; descriptive order, not causal effect of removing a filter",
            "share_unmatched=mother_count/unmatched_mothers; all_mothers includes matched and unmatched requests"]}
    chart={"id":"shortage","title":"未匹配母样本的首个供给不足阶段",
        "subtitle":f"2023–2024 · 全部{len(audit)}母样本中{sum(x['mother_count'] for x in rows)}未匹配 · 顺序描述，非排除规则的因果重要性",
        "type":"bar","intent":"comparison","layout":"full","dataset":"shortage","sourceId":"v10_mothers",
        "question":"匹配缺口最先出现在哪一层？","rationale":"Five ordered discrete stages; exact counts retain all unmatched mothers. Blue marks, labels and ordered stage names distinguish groups without relying on colour.",
        "palette":{"kind":"sequential","name":"blue"},"labels":{"values":"all"},
        "settings":{"sort":"none","categoryLabelPolicy":"wrap"},"maxRows":18,"valueFormat":"number",
        "referenceLines":[{"axis":"y","value":0,"color":"neutral","lineStyle":"solid","label":"0 母样本"}],
        "encodings":{"x":{"field":"stage_label","type":"ordinal","label":"首个低于3控制的阶段 / 缺支持"},
        "y":{"field":"mother_count","type":"quantitative","label":"母样本数","format":"number"},
        "tooltip":[{"field":"mother_count","type":"quantitative","label":"本阶段"},
                   {"field":"all_mothers","type":"quantitative","label":"全部母样本"},
                   {"field":"unmatched_mothers","type":"quantitative","label":"全部未匹配"}]}}
    return {"surface":"report","manifest":{"version":1,"surface":"report","title":title,
        "generatedAt":timestamp,"filters":[],"cards":[],"charts":[chart],"tables":[],"sources":sources,"blocks":blocks},
        "snapshot":{"version":1,"generatedAt":timestamp,"status":"ready","datasets":{"shortage":rows},"accessIssues":[]},"sources":sources}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ["markdown","summary","audit","output"]: p.add_argument("--"+name,required=True)
    a=p.parse_args()
    paths={key:safe_identity(getattr(a,key)) for key in ["markdown","summary","audit","output"]}
    if paths["output"] in [paths[x] for x in ["markdown","summary","audit"]]: raise ValueError("Cannot overwrite evidence")
    artifact=build_artifact((ROOT/paths["markdown"]).read_text(),json.loads((ROOT/paths["summary"]).read_text()),
        pd.read_csv(ROOT/paths["audit"]),markdown_path=paths["markdown"],summary_path=paths["summary"],
        audit_path=paths["audit"],generated_at=datetime.now(timezone.utc).isoformat())
    (ROOT/paths["output"]).write_text(json.dumps(artifact,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"blocks":len(artifact["manifest"]["blocks"]),"charts":1}))


if __name__=="__main__": main()
