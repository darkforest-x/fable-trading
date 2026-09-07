"""Package reviewed V31 support counts into the canonical report artifact.

This is presentation only: no labels, economic calculations, candidate changes
or new prices. Monthly rows are a direct typed projection of frozen counts.
Official artifact schema and shared portable renderer are owned by the local
Data Analytics plugin; this script does not implement HTML/chart rendering.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
EID = "exp-btcusdtp-1h-volume-wave-support-preholdout-20260907-v31"
E = Path("experiments/active")/EID
MD = Path("analysis/p1_btcusdtp_hourly_volume_wave_support_v31_20260907.md")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def monthly_dataset(rows):
    """Select pre-existing monthly counts without regrouping or exclusions."""
    output=[]
    for r in rows:
        if r["dimension"] != "month":
            continue
        assert r["population"] in ("case","control")
        n={k:int(r[k]) for k in ("total","accepted","abstain","unknown","known")}
        assert n["accepted"]+n["abstain"]+n["unknown"]==n["total"]
        assert n["known"]==n["total"]-n["unknown"]
        rate=float(r["accepted_rate"])
        assert abs(rate-n["accepted"]/n["total"]) < 1e-14
        output.append(dict(month=r["key"]+"-01", series="K1入口" if r["population"]=="case" else "原随机控制",
                           population=r["population"], accepted_rate=rate, **n))
    keys={(r["population"],r["month"]) for r in output}
    assert len(keys)==len(output)==48
    assert keys=={(p,f"{y}-{m:02d}-01") for p in ("case","control") for y in (2023,2024) for m in range(1,13)}
    return output


def run(root=ROOT):
    root=Path(root)
    commit=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip()
    for p in (MD,Path("yoyo/evaluation/hourly_impulse_volume_wave_report.py"),
              Path("tests/test_hourly_impulse_volume_wave_report.py")):
        assert hashlib.sha256(subprocess.check_output(["git","show",commit+":"+str(p)],cwd=root)).hexdigest()==digest(root/p)
    f=json.loads((root/E/"results/support_frozen.json").read_text())
    a=json.loads((root/E/"audit.json").read_text())
    assert a["status"]=="passed" and a["economic_outcomes_read"] is False
    for name,h in f["output_hashes"].items():
        assert digest(root/E/"results"/name)==h
    with gzip.open(root/E/"results/counts.csv.gz","rt") as h:
        data=monthly_dataset(list(csv.DictReader(h)))
    text=(root/MD).read_text()
    sections=re.split(r"(?m)(?=^## )",text)
    title=sections[0].strip().removeprefix("# ").strip()
    sources=[
        dict(id="report",label="V31 · 完整支持检查、口径与限制",path=str(MD)),
        dict(id="counts",label="V31 · 冻结全62行支持计数",path=str(E/"results/counts.csv.gz")),
        dict(id="audit",label="V31 · 独立标量审计",path=str(E/"audit.json")),
        dict(id="pine",label="ChartPrime · DeltaPulse 原始源码",path="experiments/active/exp-chartprime-public-confluence-audit-20260906-v1/sources/lfaZVLub.pine")]
    blocks=[]
    for i,section in enumerate(sections):
        block=dict(id=f"section_{i}",type="markdown",layout="full",body=section.strip())
        if i:
            block["sourceId"]="report"
        blocks.append(block)
        if section.startswith("## 24个月"):
            blocks.append(dict(id="monthly_chart",type="chart",chartId="monthly",layout="full"))
    assert len([b for b in blocks if b["type"]=="chart"])==1
    now=datetime.now(timezone.utc).isoformat()
    chart=dict(id="monthly",type="line",title="逐月入口与随机控制通过率",
        description="2023–2024 UTC · accepted/全部原机会，未知保留在分母；不是胜率或收益",
        showDescription=True,dataset="monthly",sourceId="counts",
        palette=dict(kind="categorical",name="blueGold"),
        encodings=dict(x=dict(field="month",type="temporal",label="UTC月份"),
            y=dict(field="accepted_rate",type="quantitative",label="通过率（0–1）"),
            color=dict(field="series",type="nominal",label="组别"),
            tooltip=[dict(field=k,type="quantitative",label=v) for k,v in
                     (("accepted","通过数"),("total","原机会总数"),("unknown","未知数"))]))
    artifact=dict(surface="report",manifest=dict(version=1,surface="report",title=title,generatedAt=now,
        filters=[],cards=[],charts=[chart],tables=[],blocks=blocks,sources=sources),
        snapshot=dict(version=1,status="ready",generatedAt=now,datasets=dict(monthly=data)),sources=sources)
    output=root/E/"artifact.json"
    with output.open("x") as h:
        json.dump(artifact,h,indent=2,ensure_ascii=False,allow_nan=False)
        h.write("\n")
    receipt=dict(builder_commit=commit,at=now,source_md_sha256=digest(root/MD),
        counts_sha256=f["output_hashes"]["counts.csv.gz"],artifact_sha256=digest(output),
        monthly_rows=len(data),sections=len(sections),blocks=len(blocks),outcomes_read=False)
    with (root/E/"report_receipt.json").open("x") as h:
        json.dump(receipt,h,indent=2)
        h.write("\n")
    return receipt


if __name__=="__main__":
    print(json.dumps(run(),indent=2))
