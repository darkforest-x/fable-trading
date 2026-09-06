"""V18 saved-only two-bar failed-exit notebook with pinned stdlib verifiers.

Both arms' six ledgers plus three paired differences are allowlisted. The
verifier API reconstructs accounting and per-arm serial occupancy from saved
rows, never raw OHLCV or a strategy import. No inference is recomputed.
The notebook reuses that verifier; it is not another independent oracle.
Minimum-format compilation and plain-Python execution are not Jupyter or
full nbformat-schema validation. No actual artifacts are built on import.
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
from yoyo.evaluation.hourly_impulse_failed_confirm_report import BASE_POLICY,CANDIDATE_POLICY,EXPERIMENT_ID


RESULTS_RELATIVE="experiments/active/"+EXPERIMENT_ID+"/results"
DEFAULT_ROOT=Path(__file__).resolve().parents[2]
TABLE_FILES={"case_trades":"case_trades.csv.gz","control_trades":"control_trades.csv.gz",
    "case_episodes":"case_episodes.csv.gz","control_episodes":"control_episodes.csv.gz",
    "matched":"matched.csv","single_pending":"single_pending.csv.gz"}
DELTA_NAMES=("case_delta","excess_delta","serial_delta")
EVIDENCE_FILES=tuple(f"{arm}/{name}" for arm in ("baseline","candidate") for name in TABLE_FILES.values())+tuple(n+".csv" for n in DELTA_NAMES)
VERIFIER_FILES=("scripts/verify_hourly_impulse_failed_confirm_v18.py",
                "scripts/verify_hourly_impulse_failed_launch_v17.py",
                "scripts/verify_hourly_impulse_dual_partial_v16.py")


def build_notebook(summary_sha256,verifier_hashes):
    """Scaffold unexecuted saved-only cells with exact evidence/source pins."""
    valid=lambda value:isinstance(value,str) and re.fullmatch("[a-f0-9]{64}",value)
    if not valid(summary_sha256) or set(verifier_hashes)!=set(VERIFIER_FILES) or not all(valid(v) for v in verifier_hashes.values()):
        raise ValueError("Summary and all three fixed V18/V17/V16 verifier SHA256 pins required")
    cells=[
        _cell("markdown","tldr","""
        # V18 · 失败退出二次确认的保存账本复核

        ## tl;dr
        未执行前不写收益结论。核对原251病例、462控制、154组三对照及97未匹配机会；
        D/I/单仓差值保留原分母与未知。只使用保存账本，不进行新回测或原始价格读取。
        同一验证器的可复现调用不是第二份独立盈利证据。
        """),
        _cell("markdown","context","""
        ## Context & Methods
        原1h大实体/吞没穿SMA40直接入场、K1极值硬止损、原72小时保持不变。
        两臂都保留盈利半仓：快5分钟真实翻色、最新已完成慢15分钟仍同向且实际open毛收益>20bp时，
        兑现一次原始仓位50%；余仓按原生15分钟SMA40(HL2)真实翻色退出。
        基准V17在尚未partial的同一快翻色/慢同向事件，当前open未超过20bp时立即全平。
        候选只延迟这一失败全平：第一edge创建pending，下一根连续完成、同segment的原生5分钟
        仍反色才可确认；再用最新已完成慢15分钟和当时实际open复核同向及毛收益<=20bp。
        任一复核失败取消并消费该edge；先重新同向再fresh flip才可重建pending。
        第二根open转盈利只取消，不可没有新edge就凭空half；第一根盈利半仓绝不延迟。
        等于20bp属于失败条件，精确报价会计把扣20bp后收益记零，不计作微小赢家。
        不加新入场门；这不是限定前几分钟的启动检测，较晚回吐同样可能触发。

        ### Key Assumptions
        713个原始入口不变。等待可以保留后来恢复，也可恶化亏损；没有收益改善保证。
        未创建pending的路径要求所有旧字段不变。每臂按自己的退出时间重新核算串行占用，
        不能沿用基准的入选掩码或宣称最终路径一律相同。
        source未知、硬止损、原慢周期全平和72小时期限保持高优先级；确认成交不读取当前bar未来HLC。
        failed_launch触发字段记录第一真实edge，failed_confirm成交字段记录第二观察；不能把确认算新翻色。
        完成整仓的20bp成本按partial/remainder权重分摊，不把两次部分退出当成两笔整仓成本。
        确认全平在20bp成本下净收益非正；pending仅来自V17原先净非正的即时全平路径。
        因此V17原净赢家的逐事件路径不变，不会因本开关变亏；原亏损可救回，也可亏得更深。
        这不等于组合占用不变，也不代表相对V16旧赢家从未被失败退出截断。
        30bp压力测试仅重新计费，不能把冻结20bp的触发门改成30bp。
        已兑现partial但剩余仓位未知时，全单仍未知，不用局部利润补成完整收益。
        """),
        _cell("markdown","data","""
        ## Data
        固定summary以及V18、V17、V16三份验证器的SHA；两臂各六表及三个delta共十五份CSV逐一验证output_hashes。
        仅调用verify_tables(tables, summary)纯表函数，不调用整仓CLI/main，不加载raw或其他实验结果。
        从仓库运行或设置NOTEBOOK_REPOSITORY_ROOT；代码格只使用Python标准库。
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
        _cell("markdown","load-title","### 1. 固定来源与唯一失败退出二次确认开关"),
        _cell("code","load",f"""
        payload=evidence_path("summary.json").read_bytes()
        require(digest(payload)==SUMMARY_SHA256,"Pinned summary hash mismatch")
        def reject_constant(value):raise ValueError("Nonfinite JSON: "+value)
        summary=json.loads(payload,parse_constant=reject_constant)
        require(summary["experiment_id"]=={EXPERIMENT_ID!r},"Wrong V18 experiment")
        require(summary["status"]=="diagnostic_only_no_candidate_acceptance","Unexpected acceptance claim")
        for flag in ("holdout_consumed","audit_prices_loaded","training_eligible","production_eligible","all_financial_gates_pass"):
            require(summary[flag] is False,"Unexpected safety/eligibility flag: "+flag)
        require(abs(summary["known_coverage_ceiling"]-154/251)<1e-12,"Original matching support changed")
        expected_policies={{"baseline":{BASE_POLICY!r},"candidate":{CANDIDATE_POLICY!r}}}
        require(set(summary["arms"])==set(expected_policies),"Wrong arms")
        for arm,policy in expected_policies.items():
            require(json.dumps(summary["arms"][arm]["policy"],sort_keys=True)==json.dumps(policy,sort_keys=True),"Failed confirmation policies changed")
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
        print("Pinned summary,",len(loaded),"saved CSVs and three verifier sources verified")
        """),
        _cell("markdown","results","## Results\n\n### 2. 检验pending生命周期、确认成交及各臂串行配对差值"),
        _cell("code","verify","""
        spec=importlib.util.spec_from_file_location("_v18_notebook_saved_verifier",verifier_path(VERIFIER_FILES[0]))
        verifier=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        validation=verifier.verify_tables(tables,summary)
        require(isinstance(validation,dict) and validation.get("status","passed")=="passed","Failed validation receipt")
        require(validation.get("counts")=={"cases":251,"controls":462,"matched":154,"unmatched":97},"Verifier did not retain full population")
        require(set(validation.get("effects",{}))==set(DELTA_NAMES),"Verifier omitted a paired effect")
        require(isinstance(validation.get("accounting"),dict),"Verifier omitted accounting")
        for field,value in (("original_cost_fraction",.002),("partial_fraction",.5),("serial_recomputed",True)):
            require(validation["accounting"].get(field)==value,"Verifier accounting contract changed: "+field)
        require(validation["accounting"].get("serial_recomputed") is True,"Serial recomputation must be explicit")
        fills=validation["accounting"].get("failed_launch_exits")
        limits={"baseline/case":251,"baseline/control":462,"candidate/case":251,"candidate/control":462}
        require(isinstance(fills,dict) and set(fills)==set(limits),"Verifier omitted full-exit counts")
        for population,limit in limits.items():
            require(type(fills[population]) is int and 0<=fills[population]<=limit,"Invalid failed full-exit count")
        lifecycle=validation["accounting"].get("confirmation_lifecycle")
        require(isinstance(lifecycle,dict) and set(lifecycle)==set(limits),"Verifier omitted pending lifecycle")
        for population,counts in lifecycle.items():
            require(isinstance(counts,dict) and set(counts)=={"created","cancelled","terminated","confirmed"},"Incomplete lifecycle counts")
            require(all(type(v) is int and v>=0 for v in counts.values()),"Invalid lifecycle counts")
            require(counts["created"]==counts["cancelled"]+counts["terminated"]+counts["confirmed"],"Unresolved pending lifecycle")
            if population.startswith("baseline/"):
                require(all(v==0 for v in counts.values()),"Baseline cannot carry confirmation lifecycle")
            else:
                require(counts["confirmed"]==fills[population],"Confirmation fill counts disagree")
        scope_fields=("raw_replay","inferential_p_recomputed","sma_recomputed","unlogged_edges_excluded_independently")
        require(all(validation.get(field) is False for field in scope_fields),"Verifier scope overclaim or missing limitation")
        require(isinstance(validation.get("limitation"),str) and validation["limitation"],"Verifier omitted scope limitation")
        scope={field:validation[field] for field in (*scope_fields,"limitation")}
        verified={"counts":validation["counts"],"effects":validation["effects"],
            "accounting":validation["accounting"],"scope":scope,
            "baseline_mean_net_bp":summary["arms"]["baseline"]["metrics"]["mean_net_bp"],
            "candidate_mean_net_bp":summary["arms"]["candidate"]["metrics"]["mean_net_bp"],
            "baseline_events":summary["arms"]["baseline"]["metrics"]["events"],
            "candidate_events":summary["arms"]["candidate"]["metrics"]["events"],
            "raw_price_replay":False,"inferential_p_recomputed":False,"verifier_reused_not_independent":True}
        print("Verified saved ledgers:",json.dumps(verified,ensure_ascii=False,allow_nan=False))
        print("Same pinned verifier reused. No raw-price or inferential-p recomputation here.")
        """),
        _cell("markdown","takeaways","""
        ## Takeaways
        D保留全部251机会；I保留原154组三对照支持及97个未匹配机会，不事后重配。
        未知不能补零；已有partial不代表最终仓位已知。事件收益和不是复利账户收益。
        改善也可能只是少亏；加权账本一致性不等于策略有正期望或真实成交保证。
        同一批反复使用的2023–2024数据和61.35%匹配覆盖不能提供独立盈利确认，不自动部署。

        ### Execution gap
        """+GAP+"""

        原始K线、SMA颜色、首次合格事件及其路径真实性未在本notebook重建；推断p值也未重算。
        完整Jupyter验证需在已有依赖的隔离环境运行
        `python -m jupyter nbconvert --execute --to notebook --inplace path/to/failed_confirm_audit.ipynb`。
        本轮不安装依赖；三格普通Python不是Jupyter内核或完整schema验证。
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
    """Capture actual top-down Python cell outputs, not Jupyter-kernel receipts."""
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
        # V18 · 失败退出二次确认的保存账本复核

        ## tl;dr
        三格普通Python复核251病例、462控制、原154组三对照和97未匹配机会。
        V17即时失败全平基准完成交易{facts['baseline_events']}笔、净均值{facts['baseline_mean_net_bp']}bp；
        失败退出二次确认候选完成交易{facts['candidate_events']}笔、净均值{facts['candidate_mean_net_bp']}bp。
        候选确认全平：病例{facts['accounting']['failed_launch_exits']['candidate/case']}笔，
        控制{facts['accounting']['failed_launch_exits']['candidate/control']}笔。
        病例pending创建{facts['accounting']['confirmation_lifecycle']['candidate/case']['created']}次、
        取消{facts['accounting']['confirmation_lifecycle']['candidate/case']['cancelled']}次、
        优先终止{facts['accounting']['confirmation_lifecycle']['candidate/case']['terminated']}次。
        D已知{effects['case_delta']['n']}/{effects['case_delta']['total_pairs']}，均值{effects['case_delta']['mean_bp']}bp；
        I已知{effects['excess_delta']['n']}/{effects['excess_delta']['total_pairs']}，均值{effects['excess_delta']['mean_bp']}bp。
        未知不补零，D使用相同已知配对；各臂串行占用已按各自退出重新核算。
        等待可能救回或恶化V17原亏损；V17原净赢家逐事件路径不变，组合占用仍须重算。
        不把确认记为新edge；相对V16的历史赢家仍可能受失败退出影响。
        本结论只说明保存账本核对；均值变化不等于盈利确认。
        复用同一验证器，未重做原始路径或p值；Jupyter与完整schema仍未验证。
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
