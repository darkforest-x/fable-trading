"""V35 report from hash-verified alignment outputs, without market or P&L reads.

Report-only additional V4 projection: event_id,parent_event_id,direction,fold
from the already hash-locked control_mothers file. This audits control coverage,
not selection. SQL aggregates every original mother/status and window. Native
report packaging follows the installed Data Analytics canonical artifact reader.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sqlite3
import subprocess

import pandas as pd

from yoyo.evaluation.k1k2_genuine_flow_audit import HERE, REL, ROOT, sha, write_json

REPORT = "analysis/p1_btcusdtp_k1k2_genuine_flow_alignment_v35_20260907.md"
TITLE = "K1/K2 Flow Clocks · V35"
QUERIES = {
    "case_status": """SELECT status, COUNT(*) AS mothers,
        (SELECT COUNT(*) FROM case_statuses) AS total_mothers,
        1.0*COUNT(*)/(SELECT COUNT(*) FROM case_statuses) AS share
        FROM case_statuses GROUP BY status ORDER BY mothers DESC, status""",
    "fold_counts": """SELECT m.fold, COUNT(*) AS mothers,
        SUM(s.status='request_emitted') AS requests,
        SUM(s.status='expired_no_k2') AS expired,
        SUM(s.status='invalidated_wrong_close') AS wrong_close,
        SUM(s.status='invalidated_ma_colour') AS wrong_colour
        FROM case_mothers m JOIN case_statuses s USING(event_id)
        GROUP BY m.fold ORDER BY m.fold""",
    "alignment": """SELECT cohort,window_kind,status,COUNT(*) AS windows,
        SUM(flow_defined) AS defined, SUM(available_bars) AS source_bar_occurrences,
        SUM(zero_volume_bars) AS zero_bar_occurrences
        FROM features GROUP BY cohort,window_kind,status ORDER BY cohort,window_kind,status""",
    "control_coverage": """WITH coverage AS (SELECT parent_event_id, COUNT(*) AS n FROM parents GROUP BY parent_event_id)
        SELECT m.fold,COUNT(*) AS mothers, SUM(COALESCE(c.n,0)=3) AS mothers_with_three_controls,
        SUM(COALESCE(c.n,0)=0) AS mothers_without_controls,
        SUM(s.status='request_emitted') AS requests,
        SUM(s.status='request_emitted' AND COALESCE(c.n,0)=3) AS requests_with_three_original_controls
        FROM case_mothers m JOIN case_statuses s USING(event_id)
        LEFT JOIN coverage c ON m.event_id=c.parent_event_id GROUP BY m.fold ORDER BY m.fold""",
}


def guard():
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source = "yoyo/evaluation/k1k2_genuine_flow_report.py"
    if subprocess.check_output(["git", "show", commit+":"+source], cwd=ROOT) != (ROOT/source).read_bytes():
        raise ValueError("Commit report builder first")
    summary = json.loads((HERE/"summary.json").read_text())
    config = json.loads((HERE/"config.json").read_text())
    if sha(HERE/"config.json") != summary["config_sha256"]:
        raise ValueError("Config drift")
    for name, record in summary["files"].items():
        if sha(ROOT/name) != record["sha256"]:
            raise ValueError("Alignment output drift")
    control = config["rosters"]["control"]["mothers"]
    if sha(ROOT/control) != config["input_hashes"][control]:
        raise ValueError("Original control file drift")
    return commit, summary, config


def prepare():
    commit, summary, config = guard()
    output = ROOT/config["output_dir"]
    frames = {name: pd.read_csv(output/(name+".csv")) for name in ["case_mothers", "case_statuses"]}
    frames["features"] = pd.read_csv(output/"historical_features.csv", usecols=["cohort", "window_kind",
        "status", "flow_defined", "available_bars", "zero_volume_bars"])
    parents = pd.read_csv(ROOT/config["rosters"]["control"]["mothers"],
                          usecols=["event_id", "parent_event_id", "direction", "fold"])
    cases = frames["case_mothers"].set_index("event_id")
    if parents.event_id.duplicated().any() or not parents.parent_event_id.isin(cases.index).all():
        raise ValueError("Foreign or repeated control")
    if not parents.groupby("parent_event_id").size().eq(3).all():
        raise ValueError("Unexpected partial original control groups")
    for col in ["direction", "fold"]:
        if not parents[col].eq(parents.parent_event_id.map(cases[col])).all():
            raise ValueError("Control direction/fold transfer changed")
    frames["parents"] = parents
    with sqlite3.connect(":memory:") as con:
        for name, frame in frames.items():
            frame.to_sql(name, con, index=False)
        data = {key: pd.read_sql_query(query, con).to_dict("records") for key, query in QUERIES.items()}
    assert sum(r["mothers"] for r in data["case_status"]) == summary["cohorts"]["case"]["mothers"]
    assert sum(r["windows"] for r in data["alignment"]) == summary["windows"]
    for r in data["control_coverage"]:
        assert r["mothers_with_three_controls"] + r["mothers_without_controls"] == r["mothers"]
    if guard() != (commit, summary, config):
        raise ValueError("Input changed during report audit")
    result = dict(data=data, queries=QUERIES, source_commit=commit,
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(), summary_sha256=sha(HERE/"summary.json"),
        original_control_projection=["event_id", "parent_event_id", "direction", "fold"],
        parents_file=config["rosters"]["control"]["mothers"],
        parents_sha256=sha(ROOT/config["rosters"]["control"]["mothers"]), economic_labels_read=False)
    write_json(HERE/"report_data.json", result)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def package():
    commit, summary, _ = guard()
    saved = json.loads((HERE/"report_data.json").read_text())
    if saved["summary_sha256"] != sha(HERE/"summary.json"):
        raise ValueError("Report data drift")
    if subprocess.check_output(["git", "show", commit+":"+REPORT], cwd=ROOT) != (ROOT/REPORT).read_bytes():
        raise ValueError("Commit narrative before packaging")
    sections = [s.strip() for s in re.split(r"(?m)(?=^## )", (ROOT/REPORT).read_text().strip())]
    if not sections[0].startswith("# "+TITLE) or len(sections) < 7:
        raise ValueError("Report reading path incomplete")
    sources = [dict(id="report", label="V35 · 对齐结论、定义与风险", path=REPORT),
        dict(id="summary", label="V35 · 原始对齐及八季度回放", path=str(REL/"summary.json")),
        dict(id="config", label="V35 · 冻结输入与无收益投影", path=str(REL/"config.json"))]
    for name, query in QUERIES.items():
        sources.append(dict(id=name, label="V35 · 原事件名单与窗口复算", path=str(REL/"report_data.json"),
            query=dict(sql=query, engine="sqlite", language="sql", executed_at=saved["generated_at"],
                tables_used=["main.case_mothers", "main.case_statuses", "main.features", "main.parents"],
                description="全部原母群和终态的身份/时钟投影；仅检查状态与原对照覆盖，不读收益", filters=[
                    "V4 fixed original hourly cohort and original assigned controls; development 2023-2024 only; no outcome selection"],
                metric_definitions={"share": "terminal-status mother count / all original case mothers; not win rate",
                    "requests": "request_emitted statuses, not filled trades",
                    "source_bar_occurrences": "available 5m bar occurrences across overlapping windows; not distinct bars"})))
    chart = dict(id="case_status", type="bar", title="原 K1 母信号等待结果",
        description="2023–2024 · 251 个固定母信号；分类是形态终态，不是盈亏", showDescription=True,
        dataset="case_status", sourceId="case_status", palette=dict(kind="categorical", name="blueGold"),
        encodings=dict(x=dict(field="label", type="nominal", label="等待结果"),
            y=dict(field="mothers", type="quantitative", label="母信号数"),
            tooltip=[dict(field="total_mothers",type="quantitative",label="原母群总数"),
                     dict(field="share",type="quantitative",label="占比（0–1）")]))
    labels = {"expired_no_k2": "等待超时", "invalidated_wrong_close": "回到错误一侧",
              "request_emitted": "有效 K2 请求", "invalidated_ma_colour": "均线颜色失效"}
    chart_rows = [dict(r, label=labels[r["status"]]) for r in saved["data"]["case_status"]]
    blocks = []
    for i, section in enumerate(sections):
        source_id = "case_status" if section.startswith("## 不是缺数据") else None
        blocks.append(dict(id="section_"+str(i), type="markdown", layout="full", body=section,
                           **({"sourceId":source_id} if source_id else {})))
        if section.startswith("## 不是缺数据"):
            blocks.append(dict(id="case_status_chart", type="chart", layout="full", chartId="case_status"))
    if sum(b["type"] == "chart" for b in blocks) != 1:
        raise ValueError("Missing chart and adjacent interpretation")
    stamp = pd.Timestamp.now(tz="UTC").isoformat()
    artifact = dict(surface="report", manifest=dict(version=1,surface="report",title=TITLE,
        generatedAt=stamp,sources=sources,blocks=blocks,charts=[chart],cards=[],tables=[],filters=[]),
        snapshot=dict(version=1,generatedAt=stamp,status="ready",datasets=dict(case_status=chart_rows)),sources=sources)
    write_json(HERE/"artifact.json",artifact)
    write_json(HERE/"artifact_build_receipt.json",dict(source_commit=commit,report_sha256=sha(ROOT/REPORT),
        report_data_sha256=sha(HERE/"report_data.json"),summary_sha256=sha(HERE/"summary.json"),
        sections=len(sections), charts=1, generated_at=stamp))
    notebook(saved, summary)
    print(json.dumps(dict(sections=len(sections), charts=1, rows=len(chart_rows))))


def notebook(saved, summary):
    def md(text): return dict(cell_type="markdown",metadata={},source=text.splitlines(keepends=True))
    def code(text): return dict(cell_type="code",metadata={},source=text.splitlines(keepends=True),execution_count=None,outputs=[])
    cells = [md("## tl;dr\n1,001 个历史窗口完整；251 个 K1 中55个有效 K2 请求。未检验盈利。"),
        md("## Context & Methods\n仅复核本轮已保存的聚合/对照身份数据，不读新价格或收益。\n### Key Assumptions\nUTC半开窗口；理论可用不等于收到。无Jupyter依赖，用stdlib顺序执行并记录输出，不冒充kernel验收。"),
        code("from pathlib import Path\nimport json, hashlib\nroot=Path.cwd()\nwhile not (root/'yoyo').exists() and root != root.parent:\n    root=root.parent\n"
            f"here=root/{str(REL)!r}\nsaved=json.loads((here/'report_data.json').read_text())\n"
            f"assert hashlib.sha256((here/'report_data.json').read_bytes()).hexdigest()=={sha(HERE/'report_data.json')!r}\n"
            "print('report data hash verified')\n"),
        md("## Data\n检查全部状态类别和4个半年分段，禁止只挑有效K2或赚钱窗口。"),
        code("data=saved['data']\nassert sum(r['mothers'] for r in data['case_status'])==251\n"
            "assert sum(r['requests'] for r in data['fold_counts'])==55\nprint(json.dumps(data['fold_counts'],ensure_ascii=False))\n"),
        md("## Results\n独立核对窗口数与对照母群覆盖；计次不是唯一K线数。"),
        code("assert sum(r['windows'] for r in data['alignment'])==1001\n"
            "assert all(r['defined']==r['windows'] for r in data['alignment'])\n"
            "c=data['control_coverage']\nassert sum(r['mothers_with_three_controls'] for r in c)*3==462\n"
            "print(json.dumps(c,ensure_ascii=False))\n"),
        md("## Takeaways\n对齐通过不是盈利通过；完整母群、原对照缺失和到达日志缺口必须进入后续验证设计。")]
    namespace={}
    for i, cell in enumerate((c for c in cells if c['cell_type']=='code'),1):
        capture=io.StringIO()
        with contextlib.redirect_stdout(capture): exec(compile(''.join(cell['source']),f'V35 notebook cell{i}','exec'),namespace)
        cell['execution_count']=i
        cell['outputs']=[dict(output_type='stream',name='stdout',text=capture.getvalue().splitlines(keepends=True))]
    write_json(HERE/'verification.ipynb',dict(cells=cells,metadata=dict(kernelspec=dict(display_name='Python 3',language='python',name='python3'),
        language_info=dict(name='python',version='3.9.6'),validation='stdlib sequential execution; no Jupyter kernel'),nbformat=4,nbformat_minor=4))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare","package"])
    prepare() if parser.parse_args().phase=="prepare" else package()
