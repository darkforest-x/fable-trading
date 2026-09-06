"""V12 saved-ledger notebook with honest stdlib-only execution fallback.

Only the pinned summary and three allowlisted saved CSVs are read. Generated
cells independently reconcile paired returns,20bp cost, frozen-boundary clocks
and initial case/control geometry; they import no strategy and read no raw
prices. Structure follows nbformat4.5 minimum fields and unique cell IDs:
https://nbformat.readthedocs.io/en/latest/format_description.html
The reused validator compiles every code cell, not the full nbformat schema.
--check captures actual plain-Python top-down outputs, not Jupyter execution.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re

from yoyo.evaluation.hourly_impulse_support_notebook import GAP, _cell, validate_notebook


EXPERIMENT_ID = "exp-btcusdtp-1h-frozen-ma-exit-preholdout-20260906-v12"
RESULTS_RELATIVE = "experiments/active/" + EXPERIMENT_ID + "/results"
DEFAULT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_FILES = ("case_delta.csv", "paired_case_mechanics.csv.gz", "entry_geometry.csv")


def build_notebook(summary_sha256: str) -> dict:
    """Create a compilable, unexecuted notebook without evidence/file reads."""
    if not isinstance(summary_sha256, str) or not re.fullmatch("[a-f0-9]{64}", summary_sha256):
        raise ValueError("A pinned summary SHA256 is required")
    cells = [
        _cell("markdown", "tldr", """
        # V12 · 冻结K1均线退出的保存账本复核

        ## tl;dr
        未执行前不提供已验证收益结论。运行后独立核对全部251个请求的D差值、
        两臂20bp成本、冻结边界触发，以及251病例/462控制的事前几何与154/97匹配分母。
        这是内部一致性复核，不是新回测或独立盈利验证。
        """),
        _cell("markdown", "context", """
        ## Context & Methods
        两臂同用原生5m SMA40(HL2)真实翻色退出。新版仅增加：首次实际持仓5m收盘
        严格逆于信号小时冻结MA，下一真实5m open退出。等号不退；初始open逆侧不删入场；
        原止损、翻色、总时限优先。没有V11启动期限或V9慢采样。

        ### Key Assumptions
        只检查保存成交/触发记录，不用原始OHLC验证“首次”或盘中顺序。
        控制使用自身信号小时MA，病例risk/ATR转移不变；边界距离没有重新匹配。
        固定g=方向×(入场−MA)/初始R五类只作事前诊断，不据此筛选盈利桶。
        2023–2024已重复研究；154/251匹配覆盖不能达到90%门。
        D是全251原始请求的配对净收益变化，不是账户复利。此处不重算控制收益、I或推断p。
        """),
        _cell("markdown", "data", """
        ## Data
        仅从固定实验目录读取summary.json、case_delta.csv、paired_case_mechanics.csv.gz、
        entry_geometry.csv。固定summary SHA并校验三份CSV的output_hashes；不扫描其他文件。
        哈希证明所读字节的身份，不证明原始行情/特征/成交路径正确。
        在仓库中运行，或设置NOTEBOOK_REPOSITORY_ROOT；只需要Python标准库。
        """),
        _cell("code", "setup", f"""
        import csv, gzip, hashlib, io, json, math
        from collections import Counter
        from datetime import datetime, timedelta, timezone
        from pathlib import Path

        RESULTS_RELATIVE = {RESULTS_RELATIVE!r}
        SUMMARY_SHA256 = {summary_sha256!r}
        EVIDENCE_FILES = {EVIDENCE_FILES!r}
        EXPECTED_REQUESTS, COST, ZERO_TOLERANCE = 251, .002, 1e-12
        def require(condition, message):
            if not condition: raise ValueError(message)
        def number(value):
            if value in (None, ""): return None
            result = float(value)
            require(math.isfinite(result), "Nonfinite saved number")
            return result
        def close_enough(a, b):
            return a is not None and b is not None and math.isclose(a,b,rel_tol=0,abs_tol=1e-12)
        def same_number(a, b):
            return (a is None and b is None) or close_enough(a,b)
        def flag(value):
            require(value in ("True","False","true","false"), "Invalid saved Boolean")
            return value.lower() == "true"
        def stamp(value):
            result = datetime.fromisoformat(value.replace("Z","+00:00"))
            require(result.tzinfo is not None, "Timestamp lacks timezone")
            return result.astimezone(timezone.utc)
        def grid(value):
            return value.second == value.microsecond == 0 and value.minute % 5 == 0
        def need_columns(rows, columns):
            require(rows and all(set(columns).issubset(row) for row in rows), "Missing required saved columns")
        hint = globals().get("NOTEBOOK_REPOSITORY_ROOT")
        roots = [Path(hint)] if hint is not None else [Path.cwd(), *Path.cwd().parents]
        repository_root = next((p.resolve() for p in roots if (p/RESULTS_RELATIVE/"summary.json").is_file()),None)
        require(repository_root is not None, "Run from repository or set NOTEBOOK_REPOSITORY_ROOT")
        results_directory = (repository_root/RESULTS_RELATIVE).resolve()
        require(results_directory.is_relative_to(repository_root), "Evidence directory escaped repository")
        def evidence_path(name):
            require(name in ("summary.json", *EVIDENCE_FILES), "File outside evidence allowlist")
            path = (results_directory/name).resolve()
            require(path.parent == results_directory, "Evidence symlink escaped directory")
            return path
        print("Saved evidence only:",RESULTS_RELATIVE)
        """),
        _cell("markdown", "load-title", "### 1. 固定来源、版本与唯一退出变量"),
        _cell("code", "load", f"""
        payload = evidence_path("summary.json").read_bytes()
        require(hashlib.sha256(payload).hexdigest() == SUMMARY_SHA256, "Pinned summary hash mismatch")
        def reject_constant(value): raise ValueError("Nonfinite JSON constant: "+value)
        summary = json.loads(payload,parse_constant=reject_constant)
        require(summary["experiment_id"] == {EXPERIMENT_ID!r}, "Wrong V12 experiment")
        for key in ("holdout_consumed","audit_prices_loaded","production_eligible","training_eligible"):
            require(summary[key] is False, "Unexpected holdout/production flag: "+key)
        require(summary["status"] == "diagnostic_only_no_candidate_acceptance", "Unexpected acceptance claim")
        require(summary["all_financial_gates_pass"] is False, "Inherited support prevents acceptance")
        require(close_enough(summary["known_coverage_ceiling"],154/251), "Known coverage ceiling changed")
        base = {{"id":"5m_native40","management_minutes":5,"ma_kind":"SMA","ma_length":40,
                "exit_mode":"transition_colour","confirmations":1}}
        policies = {{"baseline":base,"candidate":{{**base,"id":"5m_native40_frozen_ma","frozen_ma_exit":True}}}}
        require(set(summary["arms"]) == set(policies), "Wrong arms")
        for name, expected in policies.items():
            policy = summary["arms"][name]["policy"]
            require(policy == expected, "V12 policy is not frozen-MA-only")
            require(all(not isinstance(v,bool) for k,v in policy.items() if k != "frozen_ma_exit"), "Boolean numeric policy")
            if name == "candidate": require(policy["frozen_ma_exit"] is True, "Opt-in is not boolean True")
        tables = {{}}
        for name in EVIDENCE_FILES:
            data = evidence_path(name).read_bytes()
            require(hashlib.sha256(data).hexdigest() == summary["output_hashes"][name], "CSV hash mismatch: "+name)
            text = gzip.decompress(data).decode("utf-8") if name.endswith(".gz") else data.decode("utf-8")
            reader = csv.DictReader(io.StringIO(text))
            require(reader.fieldnames and len(set(reader.fieldnames)) == len(reader.fieldnames), "Missing/duplicate CSV columns")
            rows = list(reader)
            require(all(None not in r and all(v is not None for v in r.values()) for r in rows), "Malformed CSV row")
            tables[name] = rows
        cases, mechanics, geometry = (tables[n] for n in EVIDENCE_FILES)
        print("Pinned summary plus",len(tables),"saved CSV hashes verified")
        """),
        _cell("markdown", "population-title", "### 2. 保留全部251个请求与配对差值"),
        _cell("code", "population", """
        need_columns(cases,["event_id","mother_decision_time","before","after","difference"])
        need_columns(mechanics,["event_id","net_return_before","net_return_after","difference"])
        case_by_id = {r["event_id"]:r for r in cases}
        detail_by_id = {r["event_id"]:r for r in mechanics}
        require(len(cases) == len(case_by_id) == len(mechanics) == len(detail_by_id) == EXPECTED_REQUESTS, "Lost/duplicated original cases")
        require(all(case_by_id) and set(case_by_id) == set(detail_by_id), "Paired identities differ")
        times = [stamp(r["mother_decision_time"]) for r in cases]
        require(len(set(times)) == EXPECTED_REQUESTS, "Duplicate case timestamps")
        require(all(datetime(2023,1,1,tzinfo=timezone.utc)<=t<datetime(2025,1,1,tzinfo=timezone.utc) for t in times), "Outside development")
        differences, before_values, after_values = [], [], []
        for event_id, row in case_by_id.items():
            old,new,difference = (number(row[k]) for k in ("before","after","difference"))
            detail = detail_by_id[event_id]
            require(same_number(old,number(detail["net_return_before"])) and same_number(new,number(detail["net_return_after"])), "Case vs mechanics return mismatch")
            known = old is not None and new is not None
            require((difference is not None) == known, "Unknown pair silently classified")
            if known:
                require(close_enough(difference,new-old) and close_enough(difference,number(detail["difference"])), "Wrong saved difference")
                differences.append(difference)
            else: require(number(detail["difference"]) is None, "Unknown mechanics difference")
            if old is not None: before_values.append(old)
            if new is not None: after_values.append(new)
        observed = {"total_pairs":EXPECTED_REQUESTS,"n":len(differences),"unknown_pairs":EXPECTED_REQUESTS-len(differences),
            "improved":sum(v>ZERO_TOLERANCE for v in differences),"worsened":sum(v < -ZERO_TOLERANCE for v in differences),
            "unchanged":sum(abs(v)<=ZERO_TOLERANCE for v in differences)}
        effect = summary["effects"]["case_delta"]
        for key,value in observed.items(): require(type(effect[key]) is int and effect[key] == value, "Summary count mismatch: "+key)
        mean_delta_bp = math.fsum(differences)/len(differences)*10000 if differences else None
        require(same_number(mean_delta_bp,effect["mean_bp"]) or (mean_delta_bp is not None and math.isclose(mean_delta_bp,effect["mean_bp"],abs_tol=1e-8,rel_tol=1e-10)), "Summary mean/bp units mismatch")
        print("All-case D counts:",observed,"; mean bp:",mean_delta_bp)
        """),
        _cell("markdown", "geometry-title", "### 3. 独立复算病例/控制几何、154/97分母与风险转移"),
        _cell("code", "geometry", """
        columns = ["population","event_id","parent_event_id","matched_case","signal_time","decision_time","direction",
            "ma","signal_close","signal_atr","initial_stop","entry_open","entry_distance_atr","entry_side",
            "previous_hour_close_distance_atr","previous_hour_close_side","initial_R","entry_distance_r","geometry_bin"]
        need_columns(geometry,columns)
        require(len(geometry) == 713 and {r["population"] for r in geometry} == {"case","control"}, "Wrong geometry populations")
        case_geometry = {r["event_id"]:r for r in geometry if r["population"] == "case"}
        control_geometry = [r for r in geometry if r["population"] == "control"]
        require(len(case_geometry)==251 and set(case_geometry)==set(case_by_id) and len(control_geometry)==462, "Geometry lost cases/controls")
        require(len({r["event_id"] for r in geometry})==713 and all(r["event_id"] for r in geometry), "Duplicate geometry IDs")
        matched_ids = {k for k,r in case_geometry.items() if flag(r["matched_case"])}
        require(len(matched_ids)==154 and len(case_geometry)-len(matched_ids)==97, "Wrong154/97 case support")
        parents = Counter(r["parent_event_id"] for r in control_geometry)
        require(set(parents)==matched_ids and set(parents.values())=={3}, "Each matched case requires three fixed controls")
        require(len({stamp(r["decision_time"]) for r in control_geometry})==462, "Control time reused")
        bins = ("negative","zero","inside","equal_stop","beyond_stop")
        for row in geometry:
            side,ma,close,atr,stop,entry = (number(row[k]) for k in ("direction","ma","signal_close","signal_atr","initial_stop","entry_open"))
            require(side in (-1,1) and min(ma,close,atr,stop,entry)>0, "Invalid geometry inputs")
            signal,decision = stamp(row["signal_time"]),stamp(row["decision_time"])
            require(grid(signal) and signal.minute==0 and decision==signal+timedelta(hours=1), "Geometry MA unavailable at entry")
            require(datetime(2023,1,1,tzinfo=timezone.utc)<=decision<datetime(2025,1,1,tzinfo=timezone.utc), "Geometry outside development")
            risk = side*(entry-stop)
            require(risk>0 and close_enough(risk,number(row["initial_R"])), "Invalid frozen initial risk")
            entry_distance,close_distance = side*(entry-ma),side*(close-ma)
            for key,value in (("entry_distance_atr",entry_distance/atr),("previous_hour_close_distance_atr",close_distance/atr),("entry_distance_r",entry_distance/risk)):
                require(close_enough(number(row[key]),value), "Wrong geometry arithmetic: "+key)
            sign = lambda x: 1 if x>0 else -1 if x<0 else 0
            require(number(row["entry_side"])==sign(entry_distance) and number(row["previous_hour_close_side"])==sign(close_distance), "Wrong geometry side")
            g=entry_distance/risk
            expected="negative" if g<0 else "zero" if g==0 else "inside" if g<1 else "equal_stop" if g==1 else "beyond_stop"
            require(row["geometry_bin"]==expected,"Wrong fixed geometry bin")
            if row["population"]=="case":
                require(row["parent_event_id"]=="" and decision==stamp(case_by_id[row["event_id"]]["mother_decision_time"]), "Wrong case geometry parent/time")
            else:
                require(flag(row["matched_case"]), "Control not attached to original support")
                parent=case_geometry[row["parent_event_id"]]
                require(side==number(parent["direction"]), "Control direction changed")
                require(math.isclose(risk/atr,number(parent["initial_R"])/number(parent["signal_atr"]),rel_tol=1e-9,abs_tol=1e-12), "Control risk/ATR not transferred")
        groups={"all_cases":list(case_geometry.values()),"matched_cases":[r for k,r in case_geometry.items() if k in matched_ids],
            "unmatched_cases":[r for k,r in case_geometry.items() if k not in matched_ids],"controls":control_geometry}
        geometry_verified={}
        for name,rows in groups.items():
            counts=Counter(r["geometry_bin"] for r in rows)
            geometry_verified[name]={"n":len(rows),"geometry_bins":{b:counts[b] for b in bins}}
        require(geometry_verified==summary["entry_geometry"], "Geometry summary mismatch")
        print("All frozen pre-outcome geometry populations:",geometry_verified)
        print("Own-MA boundary distances are not matched; no row was selected or removed here.")
        """),
        _cell("markdown", "results", "## Results\n\n### 4. 独立核对成交、20bp成本和冻结边界触发时钟"),
        _cell("code", "economics", """
        fixed=["entry_time","entry_price","initial_stop","direction","ma","signal_time","decision_time","signal_atr"]
        economics=["closed","gross_return","net_return","exit_price","exit_time","hold_minutes","outcome"]
        diagnostics=["frozen_ma_enabled","frozen_ma_boundary","frozen_ma_available_at","frozen_ma_entry_distance_atr",
            "frozen_ma_trigger_open_time","frozen_ma_trigger_available_at","frozen_ma_trigger_close","frozen_ma_completed_close_count","frozen_ma_status"]
        need_columns(mechanics,[k+s for k in fixed+economics for s in ("_before","_after")]+diagnostics)
        require(not any(k.startswith("frozen_ma_") and k.endswith(("_before","_after")) for k in mechanics[0]), "Candidate-only diagnostics were incorrectly suffixed")
        cost_checks,frozen_count,trigger_count=0,0,0
        for event_id,row in detail_by_id.items():
            entry_time=stamp(row["entry_time_before"])
            geo=case_geometry[event_id]
            for field in fixed:
                old,new=row[field+"_before"],row[field+"_after"]
                if field.endswith("time"):
                    require(stamp(old)==stamp(new),"Fixed timestamp changed")
                else: require(close_enough(number(old),number(new)),"Fixed entry/risk/MA changed")
            require(entry_time==stamp(case_by_id[event_id]["mother_decision_time"])==stamp(geo["decision_time"]), "Entry time changed")
            for trade_field,geometry_field in (("entry_price","entry_open"),("initial_stop","initial_stop"),("direction","direction"),("ma","ma"),("signal_atr","signal_atr")):
                require(close_enough(number(row[trade_field+"_before"]),number(geo[geometry_field])),"Trade vs geometry mismatch")
            require(flag(row["frozen_ma_enabled"]),"Missing enabled flag")
            boundary=number(row["frozen_ma_boundary"])
            require(close_enough(boundary,number(row["ma_before"])),"Boundary is not frozen own MA")
            require(stamp(row["frozen_ma_available_at"])==stamp(row["signal_time_before"])+timedelta(hours=1)==entry_time, "Boundary availability changed")
            require(close_enough(number(row["frozen_ma_entry_distance_atr"]),number(geo["entry_distance_atr"])), "Diagnostic boundary distance mismatch")
            count=number(row["frozen_ma_completed_close_count"])
            require(count is not None and count>=0 and count.is_integer(),"Invalid completed-close count")
            for suffix in ("before","after"):
                gross,net=number(row["gross_return_"+suffix]),number(row["net_return_"+suffix])
                if not flag(row["closed_"+suffix]):
                    require(gross is None and net is None,"Censored path classified as completed")
                    continue
                side,entry,exit_price=(number(row[k+"_"+suffix]) for k in ("direction","entry_price","exit_price"))
                require(side in (-1,1) and entry>0 and exit_price>0,"Invalid saved fill")
                require(close_enough(gross,side*(exit_price/entry-1)),"Gross return differs from saved fills")
                require(close_enough(gross-net,COST),"Roundtrip cost is not20bp")
                exit_time=stamp(row["exit_time_"+suffix]);hold=exit_time-entry_time
                require(grid(exit_time) and timedelta(0)<=hold<=timedelta(hours=72),"Exit outside fixed5m/72h clock")
                require(close_enough(hold.total_seconds()/60,number(row["hold_minutes_"+suffix])),"Hold minutes mismatch")
                cost_checks+=1
            trigger_values=[row[k] for k in ("frozen_ma_trigger_open_time","frozen_ma_trigger_available_at","frozen_ma_trigger_close")]
            require(all(v=="" for v in trigger_values) or all(v!="" for v in trigger_values),"Partial trigger record")
            has_trigger=all(v!="" for v in trigger_values)
            if has_trigger:
                trigger_count+=1
                trigger,available=stamp(trigger_values[0]),stamp(trigger_values[1])
                trigger_close=number(trigger_values[2]);side=number(row["direction_before"])
                require(trigger>=entry_time and grid(trigger) and available==trigger+timedelta(minutes=5),"Trigger was not a completed held5m bar")
                require(side*(trigger_close-boundary)<0,"Trigger close not strictly wrong-side")
                require(count==(available-entry_time).total_seconds()/300,"Completed-close count differs from trigger clock")
                if flag(row["closed_after"]):require(stamp(row["exit_time_after"])==available,"Latched trigger not executed at next open")
            frozen=row["outcome_after"]=="frozen_ma_exit"
            if frozen:
                frozen_count+=1
                require(has_trigger and flag(row["closed_before"]) and flag(row["closed_after"]),"Frozen exit requires observed paired trigger")
                require(row["frozen_ma_status"]=="structure_exit","Frozen exit missing structure status")
                require(stamp(row["exit_time_after"])==available<stamp(row["exit_time_before"]),"Frozen exit must strictly precede original exit")
                require(number(row["hold_minutes_after"])>=5,"Pre-entry/seed cannot trigger")
            elif flag(row["closed_before"]) and flag(row["closed_after"]):
                require(row["frozen_ma_status"]=="prior_exit","Retained exit status mismatch")
                require(stamp(row["exit_time_before"])==stamp(row["exit_time_after"]) and close_enough(number(row["net_return_before"]),number(row["net_return_after"])),"Non-frozen path changed")
            elif not flag(row["closed_after"]):require(row["frozen_ma_status"]=="unknown_source","Unknown path lost source status")
        require(frozen_count==summary["mechanics"]["frozen_ma_exits"],"Frozen exit count mismatch")
        old_mean_bp=math.fsum(before_values)/len(before_values)*10000 if before_values else None
        new_mean_bp=math.fsum(after_values)/len(after_values)*10000 if after_values else None
        verified={**observed,"mean_delta_bp":mean_delta_bp,"baseline_mean_bp":old_mean_bp,"candidate_mean_bp":new_mean_bp,
            "frozen_ma_exits":frozen_count,"recorded_triggers":trigger_count,"closed_cost_checks":cost_checks,
            "geometry_cases":len(case_geometry),"geometry_controls":len(control_geometry),"matched_cases":len(matched_ids),"unmatched_cases":251-len(matched_ids)}
        print("Verified saved-fill economics (20bp roundtrip):",verified)
        print("Only saved metadata was checked: no raw price replay, control returns, inference or strategy run.")
        """),
        _cell("markdown", "takeaways", """
        ## Takeaways
        复核保存收益/成本/时钟/几何，不等于验证原始路径中的第一次触发，或独立验证盈利。
        控制边界的初始相对位置不同，不能把I直接归因为纯K1形态优势；本笔记本不重算I或p。
        正D仍可能是少亏；不删未匹配97例、不按结果换几何桶、不自动部署。

        ### Execution gap
        """ + GAP + """

        完整Jupyter验证需在已具备Jupyter/nbformat/nbclient/ipykernel的隔离环境运行：
        `python -m jupyter nbconvert --execute --to notebook --inplace path/to/frozen_ma_audit.ipynb`。
        不安装依赖，不把普通Python执行冒充Jupyter或完整schema验证。
        """),
    ]
    result={"nbformat":4,"nbformat_minor":5,"metadata":{
        "kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},
        "language_info":{"name":"python"},"fable_validation":{
            "execution_engine":"not_executed","jupyter_kernel_executed":False,"full_nbformat_schema_validated":False,
            "gap":GAP,"summary_sha256":summary_sha256,"evidence_files":list(EVIDENCE_FILES)}},"cells":cells}
    validate_notebook(result)
    result["metadata"]["fable_validation"].update(minimum_structure_validated=True,code_compilation_validated=True)
    return result


def execute_notebook(notebook: dict, repository_root: Path) -> dict:
    """Run generated code top-down in plain Python and capture actual output."""
    result=deepcopy(notebook)
    validate_notebook(result)
    namespace={"__name__":"__main__","NOTEBOOK_REPOSITORY_ROOT":str(Path(repository_root).resolve())}
    count=0
    for cell in result["cells"]:
        if cell["cell_type"]!="code": continue
        count+=1
        stdout,stderr=io.StringIO(),io.StringIO()
        with redirect_stdout(stdout),redirect_stderr(stderr):
            exec(compile("".join(cell["source"]),"<notebook:"+cell["id"]+">","exec"),namespace)
        cell["execution_count"]=count
        cell["outputs"]=[{"output_type":"stream","name":name,"text":stream.getvalue()}
            for name,stream in (("stdout",stdout),("stderr",stderr)) if stream.getvalue()]
    verified=namespace["verified"]
    result["metadata"]["fable_validation"].update(execution_engine="plain_python_top_down",executed_code_cells=count,verified=verified)
    result["cells"][0]=_cell("markdown","tldr",f"""
        # V12 · 冻结K1均线退出的保存账本复核

        ## tl;dr
        普通Python逐格检查完成：全部{verified['total_pairs']}请求，{verified['n']}差值已知、{verified['unknown_pairs']}未知；
        改善{verified['improved']}、恶化{verified['worsened']}、未变{verified['unchanged']}。
        原版净均值{verified['baseline_mean_bp']}bp，新版{verified['candidate_mean_bp']}bp，D均值{verified['mean_delta_bp']}bp。
        冻结边界退出{verified['frozen_ma_exits']}次、记录触发{verified['recorded_triggers']}次，成本核对{verified['closed_cost_checks']}条。
        几何保留病例{verified['geometry_cases']}、控制{verified['geometry_controls']}；匹配{verified['matched_cases']}、未匹配{verified['unmatched_cases']}。
        这些是保存账本的内部核对，不是新行情回测或独立盈利确认。Jupyter与完整schema验证仍未完成。
        """)
    validate_notebook(result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=DEFAULT_ROOT)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--check",action="store_true",help="Plain Python top-down, not Jupyter execution")
    args=parser.parse_args();root=args.root.resolve()
    directory=(root/RESULTS_RELATIVE).resolve();summary=(directory/"summary.json").resolve()
    if not directory.is_relative_to(root) or summary.parent!=directory:raise ValueError("Summary escaped fixed evidence directory")
    output=args.output.resolve()
    if output.suffix!=".ipynb" or output.exists():raise ValueError("Use a new .ipynb output; preserve evidence")
    notebook=build_notebook(hashlib.sha256(summary.read_bytes()).hexdigest())
    if args.check:notebook=execute_notebook(notebook,root)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(notebook,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"output":str(output),"cells":len(notebook["cells"]),"validation":notebook["metadata"]["fable_validation"]},ensure_ascii=False))


if __name__=="__main__":
    main()
