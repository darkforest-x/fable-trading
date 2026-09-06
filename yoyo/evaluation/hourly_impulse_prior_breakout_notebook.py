"""V14 saved-support companion; reuse the pinned stdlib verifier transparently.

Generated cells read four fixed support CSVs and summary, then invoke only
verify_tables from the pinned V14 verifier. Its V11 stdlib helper is pinned as
well. This is a reproducible use of the SAME verifier, not a second independent
validation. No raw archive or economic outcomes. Three plain-Python cells are
not Jupyter execution or full nbformat-schema validation; no packages installed.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr,redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re

from yoyo.evaluation.hourly_impulse_support_notebook import GAP,_cell,validate_notebook


EXPERIMENT_ID="exp-btcusdtp-1h-prior20-breakout-preholdout-20260906-v14"
RESULTS_RELATIVE="experiments/active/"+EXPERIMENT_ID+"/results"
DEFAULT_ROOT=Path(__file__).resolve().parents[2]
EVIDENCE_FILES=("entry_context.csv","counts.csv","matched_support.csv","prior_hourly_rows.csv")
VERIFIER_FILES=("scripts/verify_hourly_impulse_prior_breakout_v14.py","scripts/verify_hourly_impulse_launch_v11.py")


def build_notebook(summary_sha256,verifier_hashes):
    """Create a minimum-format notebook without reading data or generating claims."""
    def is_sha(value):return isinstance(value,str) and re.fullmatch("[a-f0-9]{64}",value)
    if not is_sha(summary_sha256) or set(verifier_hashes)!=set(VERIFIER_FILES) or not all(is_sha(v) for v in verifier_hashes.values()):
        raise ValueError("Summary and both exact verifier SHA256 pins required")
    cells=[
        _cell("markdown","tldr","""
        # V14 · 前20小时突破支持度复核

        ## tl;dr
        未执行前不提供已验证计数。本轮只核原251病例、462控制在固定前20小时突破规则下
        的支持度与原154组对照，不计算收益或决定部署。样本门通过也需要另立收益回放。
        """),
        _cell("markdown","context","""
        ## Context & Methods
        多头K1收盘严格高于自身前20根完整小时高点，空头严格低于前20小时低点。
        K1自身不得混入前20根极值；等于边界为不满足，缺小时或断档保留未知。
        原母事件及控制完整保留，不用后来涨跌、最大浮盈或成交结果挑选支持。

        ### Key Assumptions
        保存窗口只包含入场前已完成信息。验证器重算保存小时的极值、时钟和支持计数，
        不能独立证明原始5m到小时聚合正确。下面复用同一已固定stdlib验证器，
        不是第二个独立实现，不执行它的整仓扫描或金融审查入口。
        2023–2024为重复开发期；支持数不是盈利能力、统计功效或新鲜独立证据。
        """),
        _cell("markdown","data","""
        ## Data
        只读取固定summary和四份保存CSV：entry_context、counts、matched_support、prior_hourly_rows。
        summary字节SHA固定，四份CSV需与其output_hashes一致。两个验证脚本均固定SHA，
        禁止执行未固定的替代脚本。未读取原始归档，不读取V5/V13等历史收益表。
        从仓库运行，或设置NOTEBOOK_REPOSITORY_ROOT。代码格只需Python标准库。
        """),
        _cell("code","setup",f"""
        import csv, hashlib, importlib.util, io, json, re
        from pathlib import Path
        RESULTS_RELATIVE={RESULTS_RELATIVE!r}
        EVIDENCE_FILES={EVIDENCE_FILES!r}
        VERIFIER_FILES={VERIFIER_FILES!r}
        SUMMARY_SHA256={summary_sha256!r}
        VERIFIER_HASHES={verifier_hashes!r}
        def require(condition,message):
            if not condition:raise ValueError(message)
        def digest(data):return hashlib.sha256(data).hexdigest()
        hint=globals().get("NOTEBOOK_REPOSITORY_ROOT")
        roots=[Path(hint)] if hint is not None else [Path.cwd(),*Path.cwd().parents]
        root=next((p.resolve() for p in roots if (p/RESULTS_RELATIVE/"summary.json").is_file()),None)
        require(root is not None,"Run from repository or set NOTEBOOK_REPOSITORY_ROOT")
        directory=(root/RESULTS_RELATIVE).resolve()
        require(directory.is_relative_to(root),"Evidence directory escaped repository")
        def evidence_path(name):
            require(name in ("summary.json",*EVIDENCE_FILES),"File not in support allowlist")
            path=(directory/name).resolve()
            require(path==directory/name,"Support evidence symlink escaped fixed identity")
            return path
        def verifier_path(name):
            require(name in VERIFIER_FILES,"Verifier not allowlisted")
            path=(root/name).resolve()
            require(path==root/name,"Verifier symlink changed identity")
            return path
        print("Support-only saved inputs:",RESULTS_RELATIVE)
        """),
        _cell("markdown","load-title","### 1. 固定摘要、四份CSV和验证器依赖"),
        _cell("code","load",f"""
        payload=evidence_path("summary.json").read_bytes()
        require(digest(payload)==SUMMARY_SHA256,"Pinned summary hash mismatch")
        def reject_constant(value):raise ValueError("Nonfinite JSON: "+value)
        summary=json.loads(payload,parse_constant=reject_constant)
        require(summary["experiment_id"]=={EXPERIMENT_ID!r},"Wrong experiment")
        require(summary["status"] in ("insufficient_support_no_outcomes","support_pass_requires_separate_replay"),"Not support-only")
        for flag in ("outcomes_read_or_computed","profitability_test","holdout_consumed","training_eligible","production_eligible"):
            require(summary[flag] is False,"Unexpected financial/production claim: "+flag)
        require(type(summary["outcome_replays"]) is int and summary["outcome_replays"]==0,"Outcome replay is not support evidence")
        forbidden=re.compile(r"(^|_)(pnl|returns?|mfe|mae|outcome|closed|exit|profit|loss|fee)($|_)",re.I)
        tables={{}}
        for name in EVIDENCE_FILES:
            data=evidence_path(name).read_bytes()
            require(digest(data)==summary["output_hashes"][name],"CSV hash mismatch: "+name)
            reader=csv.DictReader(io.StringIO(data.decode("utf-8")))
            columns=reader.fieldnames
            require(columns and len(columns)==len(set(columns)),"Missing or duplicate CSV header")
            require(not any(forbidden.search(c) or c.startswith("max_favourable") for c in columns),"Economic columns forbidden in support notebook")
            rows=list(reader)
            require(all(None not in r and all(v is not None for v in r.values()) for r in rows),"Malformed support CSV")
            tables[name]=rows
        for name in VERIFIER_FILES:
            require(digest(verifier_path(name).read_bytes())==VERIFIER_HASHES[name],"Verifier dependency hash mismatch: "+name)
        print("Pinned summary, four saved CSVs and both verifier modules verified")
        """),
        _cell("markdown","results","## Results\n\n### 2. 复用固定验证器重算支持度，不运行收益回放"),
        _cell("code","verify","""
        spec=importlib.util.spec_from_file_location("_v14_notebook_saved_verifier",verifier_path(VERIFIER_FILES[0]))
        verifier=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        validation=verifier.verify_tables(tables["entry_context.csv"],tables["prior_hourly_rows.csv"],
            tables["counts.csv"],tables["matched_support.csv"],summary)
        require(isinstance(validation,dict) and validation.get("status")=="passed","Verifier must return a passed validation receipt")
        verified={"status":summary["status"],"population":summary["population"],
            "support_values":summary["support_values"],"support_gates":summary["support_gates"],
            "support_pass":summary["support_pass"],"matching":summary["matching"],"outcome_replays":0}
        print("Verified saved support:",json.dumps(verified,ensure_ascii=False,allow_nan=False))
        print("SAME pinned stdlib verifier reused; not independent raw aggregation or financial validation.")
        """),
        _cell("markdown","takeaways","""
        ## Takeaways
        支持不足不能通过看收益后缩小窗口、放松匹配或选择盈利子集来补足。
        即使支持门通过，也只是允许另立回放；这里没有获利、手续费、止盈止损结果或生产资格。
        保留未知和原154/251匹配覆盖限制。保存小时窗口复核不等于独立原始行情重建。

        ### Execution gap
        """+GAP+"""

        完整Jupyter验证需在已有依赖的隔离环境运行
        `python -m jupyter nbconvert --execute --to notebook --inplace path/to/prior_breakout_support.ipynb`。
        本轮不安装依赖；三格普通Python执行不冒充Jupyter或完整schema验证。
        """),
    ]
    result={"nbformat":4,"nbformat_minor":5,"metadata":{
        "kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},"language_info":{"name":"python"},
        "fable_validation":{"execution_engine":"not_executed","jupyter_kernel_executed":False,"full_nbformat_schema_validated":False,
            "verifier_reused_not_independent":True,"gap":GAP,"summary_sha256":summary_sha256,
            "evidence_files":list(EVIDENCE_FILES),"verifier_hashes":deepcopy(verifier_hashes)}},"cells":cells}
    validate_notebook(result)
    result["metadata"]["fable_validation"].update(minimum_structure_validated=True,code_compilation_validated=True)
    return result


def execute_notebook(notebook,repository_root):
    """Capture actual three-cell stdlib execution; never call verifier.main/verify."""
    result=deepcopy(notebook);validate_notebook(result)
    namespace={"__name__":"__main__","NOTEBOOK_REPOSITORY_ROOT":str(Path(repository_root).resolve())};count=0
    for cell in result["cells"]:
        if cell["cell_type"]!="code":continue
        count+=1;out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):exec(compile("".join(cell["source"]),"<notebook:"+cell["id"]+">","exec"),namespace)
        cell["execution_count"]=count
        cell["outputs"]=[{"output_type":"stream","name":name,"text":stream.getvalue()} for name,stream in (("stdout",out),("stderr",err)) if stream.getvalue()]
    verified=namespace["verified"]
    result["metadata"]["fable_validation"].update(execution_engine="plain_python_top_down",executed_code_cells=count,verified=verified)
    case=verified["population"]["case"];control=verified["population"]["control"]
    result["cells"][0]=_cell("markdown","tldr",f"""
        # V14 · 前20小时突破支持度复核

        ## tl;dr
        三格普通Python执行完成：原病例{case['total']}个，符合{case['accepted']}、不满足{case['abstain']}、未知{case['unknown']}；
        原控制{control['total']}个，符合{control['accepted']}、不满足{control['abstain']}、未知{control['unknown']}。
        支持状态：{verified['status']}。没有收益回放或盈利结论。
        复用同一固定验证器，不是第二独立验证；Jupyter与完整schema验证未完成。
        """)
    validate_notebook(result);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--root",type=Path,default=DEFAULT_ROOT)
    parser.add_argument("--output",type=Path,required=True);parser.add_argument("--check",action="store_true")
    args=parser.parse_args();root=args.root.resolve();output=args.output.resolve()
    directory=(root/RESULTS_RELATIVE).resolve();summary=(directory/"summary.json").resolve()
    if not directory.is_relative_to(root) or summary.parent!=directory:raise ValueError("Summary escaped fixed path")
    if output.suffix!=".ipynb" or output.exists():raise ValueError("Use a new notebook output; preserve evidence")
    hashes={}
    for name in VERIFIER_FILES:
        path=(root/name).resolve()
        if path!=root/name:raise ValueError("Verifier symlink changed identity")
        hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    result=build_notebook(hashlib.sha256(summary.read_bytes()).hexdigest(),hashes)
    if args.check:result=execute_notebook(result,root)
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"output":str(output),"validation":result["metadata"]["fable_validation"]},ensure_ascii=False))


if __name__=="__main__":main()
