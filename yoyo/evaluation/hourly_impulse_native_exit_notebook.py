"""V15 saved-ledger notebook through a pinned stdlib verifier, not raw replay.

Fifteen allowlisted CSVs preserve both arms' six ledgers and all three paired
effect tables. The native-context/management-source audit is deliberately not
duplicated here. The V15 verifier and its V12/V11 stdlib helpers are pinned.
Reusing that verifier is not a second independent implementation. Three plain
Python cells do not constitute Jupyter or full nbformat-schema validation.
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
from yoyo.evaluation.hourly_impulse_native_exit_report import BASE_POLICY,CANDIDATE_POLICY,EXPERIMENT_ID


RESULTS_RELATIVE="experiments/active/"+EXPERIMENT_ID+"/results"
DEFAULT_ROOT=Path(__file__).resolve().parents[2]
TABLE_FILES={"case_trades":"case_trades.csv.gz","control_trades":"control_trades.csv.gz",
    "case_episodes":"case_episodes.csv.gz","control_episodes":"control_episodes.csv.gz",
    "matched":"matched.csv","single_pending":"single_pending.csv.gz"}
DELTA_NAMES=("case_delta","excess_delta","serial_delta")
EVIDENCE_FILES=tuple(f"{arm}/{name}" for arm in ("baseline","candidate") for name in TABLE_FILES.values())+tuple(n+".csv" for n in DELTA_NAMES)
VERIFIER_FILES=("scripts/verify_hourly_impulse_native_exit_v15.py",
    "scripts/verify_hourly_impulse_frozen_ma_v12.py","scripts/verify_hourly_impulse_launch_v11.py")


def build_notebook(summary_sha256,verifier_hashes):
    """Produce minimum-format, unexecuted audit cells without evidence reads."""
    valid=lambda value:isinstance(value,str) and re.fullmatch("[a-f0-9]{64}",value)
    if not valid(summary_sha256) or set(verifier_hashes)!=set(VERIFIER_FILES) or not all(valid(v) for v in verifier_hashes.values()):
        raise ValueError("Summary and all three fixed verifier SHA256 pins required")
    cells=[
        _cell("markdown","tldr","""
        # V15 · 原生15分钟与5分钟退出的保存账本复核

        ## tl;dr
        未执行前不提供已验证收益数值。逐格核对原251病例、462控制、154组三对照以及
        全机会D、原支持上的I和单仓差值；不删除未知，也不只统计盈利交易。
        这是同一固定验证器的可复现使用，不是第二次独立验证或新行情回放。
        """),
        _cell("markdown","context","""
        ## Context & Methods
        原1h大实体/吞没穿SMA40的直接入场保持不变，不加4h或前20小时入场门。
        比较原生5m SMA40(HL2)真实同向→反向退出与原生15m同规则；K1极值硬止损、
        原72小时、已完成交易20bp往返假设不变。原生规格同时改变聚合、初始可用颜色
        和均线记忆（3小时20分钟→10小时），不能解释为只改变检查频率。

        ### Key Assumptions
        本notebook只复核保存的成交/母/匹配/单仓/差值账本及其相互一致性。
        不重复native_entry_context与原V5上下文联结，不重算SMA、首次翻色或原始5m路径；
        原生上下文是否通过正式验证应查对应验证收据，不由这份notebook替代。
        不重算推断p；反复研究的2023–2024及154/251固定匹配支持不能提供独立盈利确认。
        """),
        _cell("markdown","data","""
        ## Data
        固定summary SHA，读取两臂各六表和三个delta，共十五份保存CSV；逐一校验output_hashes。
        V15验证器及V12/V11公共stdlib辅助脚本同时固定SHA，仅调用verify_tables纯表函数，
        不调用整仓verify/main，不加载历史原始价格或其他实验结果。
        从仓库运行或设置NOTEBOOK_REPOSITORY_ROOT；执行代码格只需Python标准库。
        """),
        _cell("code","setup",f"""
        import csv, gzip, hashlib, importlib.util, io, json
        from pathlib import Path
        RESULTS_RELATIVE={RESULTS_RELATIVE!r}
        EVIDENCE_FILES={EVIDENCE_FILES!r}
        TABLE_FILES={TABLE_FILES!r}
        DELTA_NAMES={DELTA_NAMES!r}
        VERIFIER_FILES={VERIFIER_FILES!r}
        SUMMARY_SHA256={summary_sha256!r}
        VERIFIER_HASHES={verifier_hashes!r}
        def require(ok,message):
            if not ok:raise ValueError(message)
        def digest(data):return hashlib.sha256(data).hexdigest()
        hint=globals().get("NOTEBOOK_REPOSITORY_ROOT")
        roots=[Path(hint)] if hint is not None else [Path.cwd(),*Path.cwd().parents]
        root=next((p.resolve() for p in roots if (p/RESULTS_RELATIVE/"summary.json").is_file()),None)
        require(root is not None,"Run from repository or set NOTEBOOK_REPOSITORY_ROOT")
        directory=(root/RESULTS_RELATIVE).resolve()
        require(directory.is_relative_to(root),"Evidence escaped repository")
        def evidence_path(name):
            require(name in ("summary.json",*EVIDENCE_FILES),"Evidence not allowlisted")
            path=(directory/name).resolve()
            require(path==directory/name,"Evidence symlink changed fixed identity")
            return path
        def verifier_path(name):
            require(name in VERIFIER_FILES,"Verifier not allowlisted")
            path=(root/name).resolve()
            require(path==root/name,"Verifier symlink changed identity")
            return path
        print("Saved-ledger evidence only:",RESULTS_RELATIVE)
        """),
        _cell("markdown","load-title","### 1. 固定来源和两臂唯一管理差异"),
        _cell("code","load",f"""
        payload=evidence_path("summary.json").read_bytes()
        require(digest(payload)==SUMMARY_SHA256,"Pinned summary hash mismatch")
        def reject_constant(value):raise ValueError("Nonfinite JSON: "+value)
        summary=json.loads(payload,parse_constant=reject_constant)
        require(summary["experiment_id"]=={EXPERIMENT_ID!r},"Wrong V15 experiment")
        require(summary["status"]=="diagnostic_only_no_candidate_acceptance","Unexpected acceptance claim")
        for flag in ("holdout_consumed","audit_prices_loaded","training_eligible","production_eligible","all_financial_gates_pass"):
            require(summary[flag] is False,"Unexpected safety/eligibility flag: "+flag)
        require(abs(summary["known_coverage_ceiling"]-154/251)<1e-12,"Original matching support changed")
        expected_policies={{"baseline":{BASE_POLICY!r},"candidate":{CANDIDATE_POLICY!r}}}
        require(set(summary["arms"])==set(expected_policies),"Wrong arms")
        for arm,policy in expected_policies.items():
            require(json.dumps(summary["arms"][arm]["policy"],sort_keys=True)==json.dumps(policy,sort_keys=True),"Native management policies changed")
        loaded={{}}
        for name in EVIDENCE_FILES:
            data=evidence_path(name).read_bytes()
            require(digest(data)==summary["output_hashes"][name],"CSV hash mismatch: "+name)
            text=gzip.decompress(data).decode() if name.endswith(".gz") else data.decode()
            reader=csv.DictReader(io.StringIO(text))
            require(reader.fieldnames and len(reader.fieldnames)==len(set(reader.fieldnames)),"Invalid CSV headers")
            rows=list(reader)
            require(all(None not in r and all(v is not None for v in r.values()) for r in rows),"Malformed CSV")
            loaded[name]=rows
        for name in VERIFIER_FILES:
            require(digest(verifier_path(name).read_bytes())==VERIFIER_HASHES[name],"Verifier dependency hash mismatch: "+name)
        tables={{arm:{{key:loaded[arm+"/"+file] for key,file in TABLE_FILES.items()}} for arm in ("baseline","candidate")}}
        tables.update({{key:loaded[key+".csv"] for key in DELTA_NAMES}})
        print("Pinned summary,",len(loaded),"saved CSVs and all three verifier modules verified")
        """),
        _cell("markdown","results","## Results\n\n### 2. 复用固定纯表验证器，保留完整配对分母"),
        _cell("code","verify","""
        spec=importlib.util.spec_from_file_location("_v15_notebook_saved_verifier",verifier_path(VERIFIER_FILES[0]))
        verifier=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        validation=verifier.verify_tables(tables,summary["arms"],summary["effects"])
        require(isinstance(validation,dict) and validation.get("status","passed")=="passed","Failed validation receipt")
        require(validation.get("counts")=={"cases":251,"controls":462,"matched":154,"unmatched":97},"Verifier did not retain full population")
        require(set(validation.get("effects",{}))==set(DELTA_NAMES),"Verifier omitted a paired effect")
        verified={"counts":validation["counts"],"effects":validation["effects"],
            "baseline_mean_net_bp":summary["arms"]["baseline"]["metrics"]["mean_net_bp"],
            "candidate_mean_net_bp":summary["arms"]["candidate"]["metrics"]["mean_net_bp"],
            "baseline_events":summary["arms"]["baseline"]["metrics"]["events"],
            "candidate_events":summary["arms"]["candidate"]["metrics"]["events"],
            "raw_price_replay":False,"native_context_reverified":False,"inferential_p_recomputed":False,
            "verifier_reused_not_independent":True}
        print("Verified saved financial ledgers:",json.dumps(verified,ensure_ascii=False,allow_nan=False))
        print("Same pinned verifier reused. No native context, raw price or inferential-p recomputation here.")
        """),
        _cell("markdown","takeaways","""
        ## Takeaways
        D必须保留全部251机会，未知不补零；I只能使用原固定可配对支持，不能删除97个未匹配母事件。
        单仓需按各臂真实保存退出重算占用，不能只比较保留下来的赢家。
        正的收益变化也可能只是少亏；财务账本一致性不是策略有正期望或实盘成交保证。
        不自动部署，也不把原生15分钟比较说成纯采样时钟实验。

        ### Execution gap
        """+GAP+"""

        完整Jupyter验证应在已有依赖的隔离环境运行
        `python -m jupyter nbconvert --execute --to notebook --inplace path/to/native_exit_audit.ipynb`。
        本轮不安装依赖；三格普通Python不是Jupyter内核或完整schema验证。
        """),
    ]
    result={"nbformat":4,"nbformat_minor":5,"metadata":{
        "kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},"language_info":{"name":"python"},
        "fable_validation":{"execution_engine":"not_executed","jupyter_kernel_executed":False,"full_nbformat_schema_validated":False,
            "native_context_reverified":False,"verifier_reused_not_independent":True,"gap":GAP,
            "summary_sha256":summary_sha256,"evidence_files":list(EVIDENCE_FILES),"verifier_hashes":deepcopy(verifier_hashes)}},"cells":cells}
    validate_notebook(result)
    result["metadata"]["fable_validation"].update(minimum_structure_validated=True,code_compilation_validated=True)
    return result


def execute_notebook(notebook,repository_root):
    """Run the three saved-only cells in plain Python and record actual outputs."""
    result=deepcopy(notebook);validate_notebook(result)
    namespace={"__name__":"__main__","NOTEBOOK_REPOSITORY_ROOT":str(Path(repository_root).resolve())};count=0
    for cell in result["cells"]:
        if cell["cell_type"]!="code":continue
        count+=1;out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):exec(compile("".join(cell["source"]),"<notebook:"+cell["id"]+">","exec"),namespace)
        cell["execution_count"]=count
        cell["outputs"]=[{"output_type":"stream","name":name,"text":stream.getvalue()} for name,stream in (("stdout",out),("stderr",err)) if stream.getvalue()]
    facts=namespace["verified"];effects=facts["effects"]
    result["metadata"]["fable_validation"].update(execution_engine="plain_python_top_down",executed_code_cells=count,verified=facts)
    result["cells"][0]=_cell("markdown","tldr",f"""
        # V15 · 原生15分钟与5分钟退出的保存账本复核

        ## tl;dr
        三格普通Python完成保存账本核对：251病例、462控制、原154组三对照。
        原5m完成交易{facts['baseline_events']}笔、净均值{facts['baseline_mean_net_bp']}bp；
        原生15m完成交易{facts['candidate_events']}笔、净均值{facts['candidate_mean_net_bp']}bp。
        D已知{effects['case_delta']['n']}/{effects['case_delta']['total_pairs']}，均值{effects['case_delta']['mean_bp']}bp；
        I已知{effects['excess_delta']['n']}/{effects['excess_delta']['total_pairs']}，均值{effects['excess_delta']['mean_bp']}bp。
        未知没有补零；D在相同已知配对上核对，不用不同完成集合的均值直接相减。
        均值比较不等于盈利确认，原生规格也不是纯时钟差异。
        复用同一验证器，未重做原生上下文、原始价格或p值；Jupyter与完整schema仍未验证。
        """)
    validate_notebook(result);return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--root",type=Path,default=DEFAULT_ROOT)
    parser.add_argument("--output",type=Path,required=True);parser.add_argument("--check",action="store_true")
    args=parser.parse_args();root=args.root.resolve();output=args.output.resolve()
    directory=(root/RESULTS_RELATIVE).resolve();summary=(directory/"summary.json").resolve()
    if not directory.is_relative_to(root) or summary.parent!=directory:raise ValueError("Summary escaped fixed path")
    if output.suffix!=".ipynb" or output.exists():raise ValueError("Use new notebook output; preserve evidence")
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
