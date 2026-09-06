"""Build V13 saved-evidence audit cells, with honest plain-Python execution.

The generated notebook uses only stdlib and ten fixed saved CSVs plus pinned
summary. No raw prices, strategy imports, inference or parameter search.
Minimum nbformat4.5/cell-ID validation and compilation reuse the existing
scaffold; --check is not Jupyter-kernel or full nbformat-schema validation.
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
from yoyo.evaluation.hourly_impulse_prior_colour_report import BASE_POLICY, CANDIDATE_POLICY, GATE_CONTRACT, EXPERIMENT_ID


RESULTS_RELATIVE = "experiments/active/"+EXPERIMENT_ID+"/results"
DEFAULT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_FILES = ("context_gates.csv", "case_delta.csv", *(
    f"{arm}/{population}_{kind}.csv.gz" for arm in ("baseline","candidate")
    for population in ("case","control") for kind in ("episodes","trades")))


def build_notebook(summary_sha256: str) -> dict:
    """Create compilable unexecuted cells without reading any evidence."""
    if not isinstance(summary_sha256,str) or not re.fullmatch("[a-f0-9]{64}",summary_sha256):
        raise ValueError("Pinned summary SHA256 required")
    cells = [
        _cell("markdown","tldr","""
        # V13 · 事前4小时颜色门的保存账本复核

        ## tl;dr
        未执行前不提供已验证数值。逐格核对251病例、462自身门控对照、全机会D，
        区分实际交易、已知放弃为零和未知；不把仅剩交易的均值当成全机会收益。
        这是保存证据内部复核，不是新回测、独立原始价格重放或盈利验证。
        """),
        _cell("markdown","context","""
        ## Context & Methods
        唯一新增入场门：K1开盘时最近已完成4h的HL2在SMA40同向一侧；等号为+1。
        入场、K1止损、原生5m真实翻色和72小时保持原版。对照使用自己的4h信息。

        ### Key Assumptions
        已知反向：不入场、机会收益0、费用0。未知：不伪造交易，机会收益NaN，
        单仓诊断保守占用72小时并不代表真实仓位。实际完成交易才扣20bp往返成本。
        40根完整4h与原始缺口的真实性不能用保存字段独立证明；此处仅检查字段时钟、
        侧别、门控会计、实际成交保存表及其一致性。不重算原始SMA、匹配I或推断p。
        2023–2024重复开发；原154/251匹配支持不足90%，不授权部署或宣称最优。
        """),
        _cell("markdown","data","""
        ## Data
        固定目录只读summary、context_gates、case_delta、两臂完整case/control母账本
        与各自实际成交表。十个CSV全部校验summary中的SHA，不扫描其他资料。
        哈希证明字节身份，不证明行情或策略正确。设置NOTEBOOK_REPOSITORY_ROOT或从仓库运行。
        """),
        _cell("code","setup",f"""
        import csv, gzip, hashlib, io, json, math
        from collections import Counter
        from datetime import datetime, timedelta, timezone
        from pathlib import Path
        RESULTS_RELATIVE={RESULTS_RELATIVE!r}
        EVIDENCE_FILES={EVIDENCE_FILES!r}
        SUMMARY_SHA256={summary_sha256!r}
        COST, ZERO = .002, 1e-12
        def require(ok,message):
            if not ok: raise ValueError(message)
        def number(value):
            if value in (None,""): return None
            result=float(value)
            require(math.isfinite(result),"Nonfinite saved number")
            return result
        def near(a,b,tolerance=1e-12):
            return a is not None and b is not None and math.isclose(a,b,rel_tol=0,abs_tol=tolerance)
        def same(a,b): return (a is None and b is None) or near(a,b)
        def flag(value):
            require(value in ("True","False","true","false"),"Invalid Boolean")
            return value.lower()=="true"
        def stamp(value):
            result=datetime.fromisoformat(value.replace("Z","+00:00"))
            require(result.tzinfo is not None,"Timezone required")
            return result.astimezone(timezone.utc)
        def mean(values): return math.fsum(values)/len(values) if values else None
        def assert_old_value(key,a,b):
            if a==b: return
            require(a!="" and b!="","Lost old field: "+key)
            if key.endswith(("_time","_at","_deadline","_until","_bar_open")):
                require(stamp(a)==stamp(b),"Changed old timestamp: "+key)
            else:
                require(near(number(a),number(b)),"Changed old value: "+key)
        hint=globals().get("NOTEBOOK_REPOSITORY_ROOT")
        roots=[Path(hint)] if hint is not None else [Path.cwd(),*Path.cwd().parents]
        root=next((p.resolve() for p in roots if (p/RESULTS_RELATIVE/"summary.json").is_file()),None)
        require(root is not None,"Run from repository or set NOTEBOOK_REPOSITORY_ROOT")
        directory=(root/RESULTS_RELATIVE).resolve()
        require(directory.is_relative_to(root),"Evidence directory escaped repository")
        def evidence_path(name):
            require(name in ("summary.json",*EVIDENCE_FILES),"Evidence not allowlisted")
            path=(directory/name).resolve()
            require(path==directory/name,"Evidence symlink escaped fixed path")
            return path
        print("Saved evidence only:",RESULTS_RELATIVE)
        """),
        _cell("markdown","load-title","### 1. 核对来源与唯一入场变量"),
        _cell("code","load",f"""
        payload=evidence_path("summary.json").read_bytes()
        require(hashlib.sha256(payload).hexdigest()==SUMMARY_SHA256,"Pinned summary hash mismatch")
        def reject_constant(value): raise ValueError("Nonfinite JSON: "+value)
        summary=json.loads(payload,parse_constant=reject_constant)
        require(summary["experiment_id"]=={EXPERIMENT_ID!r},"Wrong V13 experiment")
        for key in ("holdout_consumed","audit_prices_loaded","production_eligible","training_eligible","all_financial_gates_pass"):
            require(summary[key] is False,"Unexpected safety/acceptance flag: "+key)
        require(summary["status"]=="diagnostic_only_no_candidate_acceptance","Wrong acceptance status")
        require(near(summary["known_coverage_ceiling"],154/251),"Inherited coverage changed")
        expected_policies={{"baseline":{BASE_POLICY!r},"candidate":{CANDIDATE_POLICY!r}}}
        require(set(summary["arms"])==set(expected_policies),"Unexpected arms")
        for arm,expected in expected_policies.items():
            require(json.dumps(summary["arms"][arm]["policy"],sort_keys=True)==json.dumps(expected,sort_keys=True),"Wrong policy")
        require(json.dumps(summary["gate_contract"],sort_keys=True)==json.dumps({GATE_CONTRACT!r},sort_keys=True),"Wrong gate contract")
        tables,headers={{}},{{}}
        for name in EVIDENCE_FILES:
            data=evidence_path(name).read_bytes()
            require(hashlib.sha256(data).hexdigest()==summary["output_hashes"][name],"CSV hash mismatch: "+name)
            text=gzip.decompress(data).decode() if name.endswith(".gz") else data.decode()
            reader=csv.DictReader(io.StringIO(text))
            require(reader.fieldnames and len(set(reader.fieldnames))==len(reader.fieldnames),"Invalid CSV header")
            rows=list(reader)
            require(all(None not in row and all(v is not None for v in row.values()) for row in rows),"Malformed CSV")
            tables[name],headers[name]=rows,reader.fieldnames
        def indexed(name,required):
            require(set(required).issubset(headers[name]),"Missing columns: "+name)
            rows=tables[name]; out={{r["event_id"]:r for r in rows}}
            require(len(rows)==len(out) and all(out),"Duplicate/empty event ID: "+name)
            return out
        print("Pinned summary and",len(tables),"saved CSVs verified")
        """),
        _cell("markdown","gates-title","### 2. 保留713个机会并核对事前时钟与三态门"),
        _cell("code","gates","""
        gate_columns=["prior_colour_bar_open","prior_colour_available_at","prior_colour_ma","prior_colour_hl2",
            "prior_colour_side","prior_colour_known","prior_colour_reason","prior_colour_count",
            "prior_colour_gate_state","prior_colour_raw_segment_id"]
        context=indexed("context_gates.csv",["event_id","population","signal_time","direction",*gate_columns])
        require(len(context)==713,"Lost original contexts")
        by_population={p:{k:r for k,r in context.items() if r["population"]==p} for p in ("case","control")}
        require(len(by_population["case"])==251 and len(by_population["control"])==462,"Wrong case/control populations")
        gate_counts={}
        for population,rows in by_population.items():
            for event_id,row in rows.items():
                signal=stamp(row["signal_time"]);direction=number(row["direction"])
                require(signal.minute==signal.second==signal.microsecond==0 and direction in (-1,1),"Invalid K1 open/direction")
                known=flag(row["prior_colour_known"]); state=row["prior_colour_gate_state"]
                require(state in ("accepted","abstain","unknown") and known==(state!="unknown"),"Unknown/abstain conflation")
                if known:
                    available,bar=stamp(row["prior_colour_available_at"]),stamp(row["prior_colour_bar_open"])
                    require(bar.hour%4==0 and bar.minute==bar.second==bar.microsecond==0,"Not native UTC4h")
                    require(available==bar+timedelta(hours=4) and timedelta(0)<=signal-available<timedelta(hours=4),"Unavailable/stale context at K1 OPEN")
                    require(number(row["prior_colour_count"])>=40 and number(row["prior_colour_raw_segment_id"])>=0,"Insufficient source support")
                    ma,hl2=number(row["prior_colour_ma"]),number(row["prior_colour_hl2"])
                    require(ma is not None and hl2 is not None and min(ma,hl2)>0,"Invalid known colour")
                    side=1 if hl2>=ma else -1
                    require(number(row["prior_colour_side"])==side and row["prior_colour_reason"]=="known","Wrong MA-side colour")
                    require(state==("accepted" if side==direction else "abstain"),"Gate not based on own direction")
                else:
                    require(number(row["prior_colour_side"]) is None and row["prior_colour_reason"]!="known","Unknown silently assigned a colour")
            counts=Counter(r["prior_colour_gate_state"] for r in rows.values())
            gate_counts[population]={"total":len(rows),**{s:counts[s] for s in ("accepted","abstain","unknown")}}
        require(gate_counts==summary["arms"]["candidate"]["gate_counts"],"Gate summary counts differ")
        print("Full opportunity gate counts:",gate_counts)
        """),
        _cell("markdown","accounting-title","## Results\n\n### 3. 独立重算交易成本、放弃零收益与未知"),
        _cell("code","accounting","""
        populations={}; cost_checks=0; accepted_checks=0
        episodes_required=["event_id","mother_decision_time","mother_deadline","signal_time","direction","observed",
            "executed","completed_trade","episode_net_return","status","episode_status","entry_time","exit_time","terminal_time","occupied_until"]
        trades_required=["event_id","closed","entry_price","exit_price","gross_return","net_return","direction"]
        for population,n in (("case",251),("control",462)):
            old=indexed(f"baseline/{population}_episodes.csv.gz",episodes_required)
            new=indexed(f"candidate/{population}_episodes.csv.gz",[*episodes_required,*gate_columns,"policy_fee_fraction"])
            old_trades=indexed(f"baseline/{population}_trades.csv.gz",trades_required)
            new_trades=indexed(f"candidate/{population}_trades.csv.gz",trades_required)
            require(len(old)==n and set(old)==set(new)==set(old_trades)==set(by_population[population]),"Original population lost")
            accepted={k for k,r in by_population[population].items() if r["prior_colour_gate_state"]=="accepted"}
            require(set(new_trades)==accepted,"Candidate trades contain non-accepted or lost accepted requests")
            for event_id,before in old.items():
                after=new[event_id];gate=by_population[population][event_id];state=gate["prior_colour_gate_state"]
                decision=stamp(before["mother_decision_time"]);deadline=stamp(before["mother_deadline"])
                require(decision==stamp(gate["signal_time"])+timedelta(hours=1) and deadline==decision+timedelta(hours=72),"Changed original mother clock")
                require(datetime(2023,1,1,tzinfo=timezone.utc)<=decision<datetime(2025,1,1,tzinfo=timezone.utc),"Outside development")
                for key in ("mother_decision_time","mother_deadline","signal_time","direction"):
                    assert_old_value(key,before[key],after[key])
                for key in gate_columns: assert_old_value(key,gate[key],after[key])
                require(flag(before["completed_trade"]) and flag(before["observed"]) and flag(before["executed"]),"Frozen baseline not actual completed trade")
                require(near(number(before["episode_net_return"]),number(old_trades[event_id]["net_return"])),"Baseline episode/trade mismatch")
                if state=="accepted":
                    for key,value in before.items():
                        require(key in after,"Lost old episode field")
                        assert_old_value(key,value,after[key])
                    for key,value in old_trades[event_id].items():
                        require(key in new_trades[event_id],"Lost accepted trade field")
                        assert_old_value(key,value,new_trades[event_id][key])
                    require(near(number(after["policy_fee_fraction"]),COST),"Accepted fee changed")
                    accepted_checks+=1
                else:
                    require(after["status"]==after["episode_status"]=="prior_colour_"+state,"Wrong non-entry status")
                    require(not flag(after["executed"]) and not flag(after["completed_trade"]),"Non-entry falsely executed")
                    require(after["entry_time"]==after["exit_time"]=="","Non-entry has execution timestamps")
                    require(stamp(after["terminal_time"])==decision,"Non-entry terminal shifted")
                    if state=="abstain":
                        require(flag(after["observed"]) and number(after["episode_net_return"])==0 and number(after["policy_fee_fraction"])==0,"Abstention must be known zero/no fee")
                        require(stamp(after["occupied_until"])==decision,"Abstention occupies a position")
                    else:
                        require(not flag(after["observed"]) and number(after["episode_net_return"]) is None and number(after["policy_fee_fraction"]) is None,"Unknown converted to zero")
                        require(stamp(after["occupied_until"])==deadline,"Unknown reservation not72h")
            for trades in (old_trades,new_trades):
                for row in trades.values():
                    entry,exit_price,side=(number(row[k]) for k in ("entry_price","exit_price","direction"))
                    require(flag(row["closed"]) and min(entry,exit_price)>0 and side in (-1,1),"Invalid actual closed trade")
                    gross=side*(exit_price-entry)/entry
                    require(near(gross,number(row["gross_return"])) and near(gross-COST,number(row["net_return"])),"Wrong actual trade economics/20bp cost")
                    cost_checks+=1
            populations[population]=(old,new,old_trades,new_trades)
        control_old=populations["control"][0]
        parents=Counter(r["parent_event_id"] for r in control_old.values())
        require(len(parents)==154 and set(parents.values())=={3} and set(parents).issubset(populations["case"][0]),"Original154 fixed triples changed")
        require(len({stamp(r["mother_decision_time"]) for r in control_old.values()})==462,"Control time reused")
        print("713 gate-policy requests verified; actual trade cost checks:",cost_checks,"; accepted full-field checks:",accepted_checks)
        print("Matched154/unmatched97 retained; reserved unknowns are not real positions.")
        """),
        _cell("markdown","effects-title","### 4. 区分全机会D、实际交易均值与事后取舍"),
        _cell("code","effects","""
        cases=indexed("case_delta.csv",["event_id","mother_decision_time","before","after","difference"])
        old,new,old_trades,new_trades=populations["case"]
        require(set(cases)==set(old) and len(cases)==251,"D lost original opportunities")
        differences=[]
        for event_id,row in cases.items():
            before,after=number(old[event_id]["episode_net_return"]),number(new[event_id]["episode_net_return"])
            require(stamp(row["mother_decision_time"])==stamp(old[event_id]["mother_decision_time"]),"D time changed")
            require(same(number(row["before"]),before) and same(number(row["after"]),after),"D inconsistent with opportunity ledgers")
            expected=after-before if before is not None and after is not None else None
            require(same(number(row["difference"]),expected),"Unknown D lost or wrong subtraction")
            if expected is not None:differences.append(expected)
        observed={"total_pairs":251,"n":len(differences),"unknown_pairs":251-len(differences),
            "improved":sum(v>ZERO for v in differences),"worsened":sum(v < -ZERO for v in differences),
            "unchanged":sum(abs(v)<=ZERO for v in differences)}
        effect=summary["effects"]["case_delta"]
        for key,value in observed.items(): require(type(effect[key]) is int and effect[key]==value,"Wrong D count: "+key)
        delta_mean=mean(differences);delta_bp=None if delta_mean is None else delta_mean*10000
        require(same(delta_bp,effect["mean_bp"]) or near(delta_bp,effect["mean_bp"],1e-8),"D mean/unit mismatch")
        candidate_known=[number(r["episode_net_return"]) for r in new.values() if flag(r["observed"])]
        opportunity=mean(candidate_known);opportunity_bp=None if opportunity is None else opportunity*10000
        selected=mean([number(r["net_return"]) for r in new_trades.values()]); selected_bp=None if selected is None else selected*10000
        require(same(opportunity_bp,summary["arms"]["candidate"]["net_effect"]["mean_bp"]) or near(opportunity_bp,summary["arms"]["candidate"]["net_effect"]["mean_bp"],1e-8),"Opportunity mean confused with selected mean")
        require(summary["arms"]["candidate"]["metrics"]["events"]==len(new_trades),"Selected denominator changed")
        if selected_bp is not None: require(near(selected_bp,summary["arms"]["candidate"]["metrics"]["mean_net_bp"],1e-8),"Selected mean mismatch")
        for population,key in (("case","mechanics"),("control","control_mechanics")):
            baseline,candidate,_,_=populations[population]
            blocked=[k for k,r in candidate.items() if r["prior_colour_gate_state"]=="abstain"]
            losses=[number(baseline[k]["episode_net_return"]) for k in blocked if number(baseline[k]["episode_net_return"])<0]
            winners=[number(baseline[k]["episode_net_return"]) for k in blocked if number(baseline[k]["episode_net_return"])>0]
            info=summary[key]
            for field,value in gate_counts[population].items(): require(info[field]==value,"Mechanism denominator changed")
            require(info["avoided_net_losers"]==len(losses) and info["missed_net_winners"]==len(winners),"Wrong retrospective tradeoffs")
            require(near(info["avoided_loss_total_bp"],-math.fsum(losses)*10000,1e-8) and near(info["missed_winner_total_bp"],math.fsum(winners)*10000,1e-8),"Wrong saved opportunity-cost sum")
        verified={**observed,"mean_delta_bp":delta_bp,"candidate_opportunity_mean_bp":opportunity_bp,
            "candidate_selected_mean_bp":selected_bp,"selected_cases":len(new_trades),"gate_counts":gate_counts,
            "actual_cost_checks":cost_checks,"accepted_field_checks":accepted_checks,"matched_cases":154,"unmatched_cases":97}
        print("Verified full-opportunity D and selected-trade denominators:",verified)
        print("Avoided losers and missed winners are retrospective costs, not predictive gate features.")
        """),
        _cell("markdown","takeaways","""
        ## Takeaways
        避免亏单必须连同错过赢家计入全部机会D，不能只看留下交易的胜率/收益。
        未知不是零；同一条4h规则在控制自己的信息上判断，不转移病例结果。
        这是保存记录核对；不重算原始价格、SMA、首次翻色、I/p或账户容量，不自动上线。

        ### Execution gap
        """+GAP+"""

        如需完整Jupyter验证，在已有依赖的隔离环境运行
        `python -m jupyter nbconvert --execute --to notebook --inplace path/to/prior_colour_audit.ipynb`。
        本轮不安装依赖；普通Python逐格运行不冒充Jupyter内核或完整schema验证。
        """),
    ]
    result={"nbformat":4,"nbformat_minor":5,"metadata":{
        "kernelspec":{"display_name":"Python 3","language":"python","name":"python3"},"language_info":{"name":"python"},
        "fable_validation":{"execution_engine":"not_executed","jupyter_kernel_executed":False,
        "full_nbformat_schema_validated":False,"gap":GAP,"summary_sha256":summary_sha256,"evidence_files":list(EVIDENCE_FILES)}},"cells":cells}
    validate_notebook(result)
    result["metadata"]["fable_validation"].update(minimum_structure_validated=True,code_compilation_validated=True)
    return result


def execute_notebook(notebook,repository_root):
    """Execute saved-only cells top-down in plain Python, capturing actual output."""
    result=deepcopy(notebook);validate_notebook(result)
    namespace={"__name__":"__main__","NOTEBOOK_REPOSITORY_ROOT":str(Path(repository_root).resolve())}
    count=0
    for cell in result["cells"]:
        if cell["cell_type"]!="code":continue
        count+=1;stdout,stderr=io.StringIO(),io.StringIO()
        with redirect_stdout(stdout),redirect_stderr(stderr):
            exec(compile("".join(cell["source"]),"<notebook:"+cell["id"]+">","exec"),namespace)
        cell["execution_count"]=count
        cell["outputs"]=[{"output_type":"stream","name":n,"text":s.getvalue()} for n,s in (("stdout",stdout),("stderr",stderr)) if s.getvalue()]
    facts=namespace["verified"]
    result["metadata"]["fable_validation"].update(execution_engine="plain_python_top_down",executed_code_cells=count,verified=facts)
    result["cells"][0]=_cell("markdown","tldr",f"""
        # V13 · 事前4小时颜色门的保存账本复核

        ## tl;dr
        普通Python逐格核对全部251机会：已知D {facts['n']}、未知{facts['unknown_pairs']}，
        改善{facts['improved']}、恶化{facts['worsened']}、不变{facts['unchanged']}；D均值{facts['mean_delta_bp']}bp。
        候选实际成交{facts['selected_cases']}笔，成交均值{facts['candidate_selected_mean_bp']}bp；
        全部已知机会均值{facts['candidate_opportunity_mean_bp']}bp，放弃为零、未知不补零。
        实际成本核对{facts['actual_cost_checks']}条；完整保留251病例/462对照及154/97匹配分母。
        这是保存账本一致性，不是新回测或盈利确认；Jupyter和完整schema验证未完成。
        """)
    validate_notebook(result)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=DEFAULT_ROOT);parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--check",action="store_true",help="Plain Python top-down; not Jupyter")
    args=parser.parse_args();root=args.root.resolve();directory=(root/RESULTS_RELATIVE).resolve()
    summary=(directory/"summary.json").resolve();output=args.output.resolve()
    if not directory.is_relative_to(root) or summary.parent!=directory:raise ValueError("Summary escaped fixed directory")
    if output.suffix!=".ipynb" or output.exists():raise ValueError("Use new notebook output; preserve evidence")
    result=build_notebook(hashlib.sha256(summary.read_bytes()).hexdigest())
    if args.check:result=execute_notebook(result,root)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+"\n")
    print(json.dumps({"output":str(output),"validation":result["metadata"]["fable_validation"]},ensure_ascii=False))


if __name__=="__main__":main()
