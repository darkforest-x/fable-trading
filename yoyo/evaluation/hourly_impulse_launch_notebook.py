"""Saved-ledger-only V11 notebook; no raw prices or strategy imports.

Use nbformat4.5's minimum cell structure and the existing stdlib validator:
https://nbformat.readthedocs.io/en/latest/format_description.html
The optional plain-Python top-down execution captures real stream outputs,
but is neither Jupyter-kernel execution nor full nbformat schema validation.
No notebook packages are available in the contracted runtime; install nothing.
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


EXPERIMENT_ID = "exp-btcusdtp-1h-launch-deadline-preholdout-20260906-v11"
RESULTS_RELATIVE = "experiments/active/" + EXPERIMENT_ID + "/results"
DEFAULT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_FILES = ("case_delta.csv", "paired_case_mechanics.csv.gz")


def build_notebook(summary_sha256: str) -> dict:
    """Create an unexecuted companion without reading any source files."""
    if not isinstance(summary_sha256, str) or not re.fullmatch("[a-f0-9]{64}", summary_sha256):
        raise ValueError("A pinned summary SHA256 is required")
    cells = [
        _cell("markdown", "tldr", """
        # V11 · 60分钟启动期限的保存账本复核

        ## tl;dr
        逐格运行后，本笔记本独立核对全部251个原始1h K1请求的配对差值、
        两臂20bp成本及已保存成交账本的内部一致性。未执行时不提供已验证结果。
        固定匹配覆盖只有154/251，不能把本轮机制诊断宣布为已达盈利目标。
        """),
        _cell("markdown", "context", """
        ## Context & Methods
        原版与新版本都用原生5m SMA40(HL2)真实同向→反向变色退出。
        新版仅增加：入场后E+5至E+60的完整5m收盘，任一次推进达到初始0.5R，
        永久取消启动期限；否则尚持有的仓位在E+60实际open退出。硬止损及原翻色优先。
        逐请求差值D=新净收益−原净收益，分母是全部251，而不是事后受影响或盈利子集。

        ### Key Assumptions
        只复核保存结果，不重建原始价格、均线颜色或盘中路径；哈希证明输入身份，
        不证明回测时序正确。2023–2024已重复研究，不是独立验证。
        20bp是固定往返成本模型，不含额外资金费、成交深度或真实滑点验证。
        独立请求平均及相加不是账户复利；单仓及154组匹配推断仍以报告和原账本为准。
        """),
        _cell("markdown", "data", """
        ## Data
        仅读固定实验目录的summary.json、case_delta.csv和paired_case_mechanics.csv.gz。
        summary SHA固定在下一格，两份CSV需与summary.output_hashes一致。
        不读取原始OHLCV、控制交易或holdout；不扫描其他文件或导入研究/交易执行器。
        从仓库内打开，或设置NOTEBOOK_REPOSITORY_ROOT；不需要第三方Python库。
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
            if not condition:
                raise ValueError(message)
        def close_enough(left, right):
            return math.isclose(left, right, rel_tol=0, abs_tol=1e-12)
        def number(value):
            if value in (None, ""):
                return None
            result = float(value)
            require(math.isfinite(result), "Nonfinite saved number")
            return result
        def flag(value):
            require(value in ("True", "False", "true", "false"), "Invalid saved Boolean")
            return value.lower() == "true"
        def stamp(value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            require(parsed.tzinfo is not None, "Timestamp lacks timezone")
            return parsed.astimezone(timezone.utc)
        hint = globals().get("NOTEBOOK_REPOSITORY_ROOT")
        roots = [Path(hint)] if hint is not None else [Path.cwd(), *Path.cwd().parents]
        repository_root = next((p.resolve() for p in roots if (p / RESULTS_RELATIVE / "summary.json").is_file()), None)
        require(repository_root is not None, "Run from repository or set NOTEBOOK_REPOSITORY_ROOT")
        results_directory = (repository_root / RESULTS_RELATIVE).resolve()
        require(results_directory.is_relative_to(repository_root), "Evidence directory escaped repository")
        def evidence_path(name):
            require(name in ("summary.json", *EVIDENCE_FILES), "File outside evidence allowlist")
            path = (results_directory / name).resolve()
            require(path.parent == results_directory, "Evidence symlink escaped directory")
            return path
        print("Saved evidence only:", RESULTS_RELATIVE)
        """),
        _cell("markdown", "load-title", "### 1. 固定来源与实验规格"),
        _cell("code", "load", f"""
        payload = evidence_path("summary.json").read_bytes()
        require(hashlib.sha256(payload).hexdigest() == SUMMARY_SHA256, "Pinned summary hash mismatch")
        def reject_constant(value):
            raise ValueError("Nonfinite JSON constant: " + value)
        summary = json.loads(payload, parse_constant=reject_constant)
        require(summary["experiment_id"] == {EXPERIMENT_ID!r}, "Wrong experiment")
        for key in ("holdout_consumed", "audit_prices_loaded", "production_eligible", "training_eligible"):
            require(summary[key] is False, "Unexpected holdout/production flag: " + key)
        require(summary["status"] == "diagnostic_only_no_candidate_acceptance", "Unexpected acceptance claim")
        require(summary["all_financial_gates_pass"] is False, "Inherited support prevents acceptance")
        require(close_enough(summary["known_coverage_ceiling"], 154/251), "Known matching ceiling changed")
        base_policy = {{"management_minutes": 5, "ma_kind": "SMA", "ma_length": 40,
                       "exit_mode": "transition_colour", "confirmations": 1}}
        for arm, extra in (("baseline", {{}}), ("candidate", {{"launch_deadline_minutes": 60, "launch_progress_r": .5}})):
            policy = {{k:v for k,v in summary["arms"][arm]["policy"].items() if k != "id"}}
            require(policy == {{**base_policy, **extra}} and not any(isinstance(v, bool) for v in policy.values()), "Wrong exit policy")
        tables = {{}}
        for name in EVIDENCE_FILES:
            data = evidence_path(name).read_bytes()
            require(hashlib.sha256(data).hexdigest() == summary["output_hashes"][name], "CSV hash mismatch: " + name)
            text = gzip.decompress(data).decode("utf-8") if name.endswith(".gz") else data.decode("utf-8")
            reader = csv.DictReader(io.StringIO(text))
            require(reader.fieldnames and len(set(reader.fieldnames)) == len(reader.fieldnames), "Missing/duplicate CSV columns")
            rows = list(reader)
            require(all(None not in r and all(v is not None for v in r.values()) for r in rows), "Malformed CSV row")
            tables[name] = rows
        cases, mechanics = tables["case_delta.csv"], tables["paired_case_mechanics.csv.gz"]
        print("SHA256 checked: pinned summary and", len(tables), "saved ledgers")
        """),
        _cell("markdown", "population-title", "### 2. 核对全部251个请求与配对差值"),
        _cell("code", "population", """
        case_by_id = {r["event_id"]: r for r in cases}
        mechanism_by_id = {r["event_id"]: r for r in mechanics}
        require(len(cases) == len(case_by_id) == len(mechanics) == len(mechanism_by_id) == EXPECTED_REQUESTS, "Lost/duplicated original requests")
        require(all(case_by_id) and set(case_by_id) == set(mechanism_by_id), "Pair identities differ")
        times = [stamp(r["mother_decision_time"]) for r in cases]
        require(len(set(times)) == EXPECTED_REQUESTS, "Duplicate request times")
        require(all(datetime(2023,1,1,tzinfo=timezone.utc) <= t < datetime(2025,1,1,tzinfo=timezone.utc) for t in times), "Outside development")
        differences, before_values, after_values = [], [], []
        for event_id, row in case_by_id.items():
            old, new, difference = (number(row[k]) for k in ("before", "after", "difference"))
            detail = mechanism_by_id[event_id]
            require(old == number(detail["net_return_before"]) and new == number(detail["net_return_after"]), "Case vs mechanics returns differ")
            known = old is not None and new is not None
            require((difference is not None) == known, "Unknown pair silently classified")
            if known:
                require(close_enough(difference, new-old), "Wrong fractional return difference")
                require(close_enough(difference, number(detail["difference"])), "Mechanics difference mismatch")
                differences.append(difference)
            else:
                require(number(detail["difference"]) is None, "Unknown mechanics difference")
            if old is not None: before_values.append(old)
            if new is not None: after_values.append(new)
        effect = summary["effects"]["case_delta"]
        observed = {"total_pairs": EXPECTED_REQUESTS, "n": len(differences),
            "unknown_pairs": EXPECTED_REQUESTS-len(differences),
            "improved": sum(v > ZERO_TOLERANCE for v in differences),
            "worsened": sum(v < -ZERO_TOLERANCE for v in differences),
            "unchanged": sum(abs(v) <= ZERO_TOLERANCE for v in differences)}
        for key, value in observed.items():
            require(type(effect[key]) is int and effect[key] == value, "Summary count mismatch: " + key)
        mean_delta_bp = math.fsum(differences)/len(differences)*10000 if differences else None
        require((mean_delta_bp is None and effect["mean_bp"] is None) or (mean_delta_bp is not None and math.isclose(mean_delta_bp,effect["mean_bp"],abs_tol=1e-8,rel_tol=1e-10)), "Summary mean or bp units mismatch")
        print("All-request paired counts:", observed)
        print("D mean (bp):", mean_delta_bp)
        """),
        _cell("markdown", "results", "## Results\n\n### 3. 独立复算成交收益、20bp成本和期限时钟"),
        _cell("code", "economics", """
        cost_checks, timeout_count = 0, 0
        for event_id, row in mechanism_by_id.items():
            entry_time = stamp(row["entry_time_before"])
            require(entry_time == stamp(row["entry_time_after"]) == stamp(case_by_id[event_id]["mother_decision_time"]), "Entry time changed")
            for field in ("entry_price", "initial_stop", "direction"):
                require(number(row[field+"_before"]) == number(row[field+"_after"]), "Frozen entry/risk changed")
            for suffix in ("before", "after"):
                closed = flag(row["closed_"+suffix])
                gross, net = number(row["gross_return_"+suffix]), number(row["net_return_"+suffix])
                if not closed:
                    require(gross is None and net is None, "Censored path treated as completed")
                    continue
                require(gross is not None and net is not None, "Closed path lost return")
                entry, exit_price = number(row["entry_price_"+suffix]), number(row["exit_price_"+suffix])
                side, stop = number(row["direction_"+suffix]), number(row["initial_stop_"+suffix])
                require(entry > 0 and side in (-1,1) and side*(entry-stop)>0, "Invalid initial risk")
                require(close_enough(gross, side*(exit_price-entry)/entry), "Gross return differs from saved fills")
                require(close_enough(gross-net, COST), "Roundtrip cost is not20bp")
                hold = stamp(row["exit_time_"+suffix])-entry_time
                require(timedelta(0) <= hold <= timedelta(hours=72), "Exit outside fixed72h horizon")
                require(close_enough(hold.total_seconds()/60, number(row["hold_minutes_"+suffix])), "Saved hold minutes mismatch")
                cost_checks += 1
            timeout = row["outcome_after"] == "launch_timeout_exit"
            if timeout:
                timeout_count += 1
                require(flag(row["closed_after"]), "Timeout exit must be observed")
                require(stamp(row["exit_time_after"]) == entry_time+timedelta(minutes=60), "Timeout not at60min")
                if flag(row["closed_before"]):
                    require(stamp(row["exit_time_before"]) > stamp(row["exit_time_after"]), "Timeout did not shorten original path")
            elif flag(row["closed_before"]) and flag(row["closed_after"]):
                require(stamp(row["exit_time_before"]) == stamp(row["exit_time_after"]) and close_enough(number(row["net_return_before"]),number(row["net_return_after"])), "Non-timeout path changed")
        require(timeout_count == summary["mechanics"]["timeout_exits"], "Timeout count mismatch")
        old_mean_bp = math.fsum(before_values)/len(before_values)*10000 if before_values else None
        new_mean_bp = math.fsum(after_values)/len(after_values)*10000 if after_values else None
        verified = {**observed, "mean_delta_bp": mean_delta_bp, "baseline_mean_bp": old_mean_bp,
            "candidate_mean_bp": new_mean_bp, "timeout_exits": timeout_count, "closed_cost_checks": cost_checks}
        print("Verified saved-fill economics (20bp roundtrip cost):", verified)
        print("No raw prices, control outcomes, inference, strategy or solver were run.")
        """),
        _cell("markdown", "takeaways", """
        ## Takeaways
        这是固定历史路径上的退出政策配对复核，不是随机实盘实验、独立盈利验证或参数最优证明。
        未执行前不解释数字方向；执行后的首节只显示实际复核均值和计数。
        匹配支持上限154/251=61.35%来自V10；本笔记本没有重新核对控制收益或置换推断。
        正差值也不意味着新版本净盈利，更不意味着可以自动部署。

        ### Execution gap
        """ + GAP + """

        如需完整Jupyter验证，在已具备nbformat、nbclient、ipykernel和Jupyter的隔离环境运行：
        `python -m jupyter nbconvert --execute --to notebook --inplace path/to/launch_audit.ipynb`。
        本构建器不安装依赖，不将普通Python逐格执行冒充Jupyter内核验证。
        """),
    ]
    result = {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python"}, "fable_validation": {
            "execution_engine": "not_executed", "jupyter_kernel_executed": False,
            "full_nbformat_schema_validated": False, "gap": GAP,
            "summary_sha256": summary_sha256, "evidence_files": list(EVIDENCE_FILES)}}, "cells": cells}
    validate_notebook(result)
    result["metadata"]["fable_validation"].update(minimum_structure_validated=True, code_compilation_validated=True)
    return result


def execute_notebook(notebook: dict, repository_root: Path) -> dict:
    """Run generated cells in plain Python, capture real stdout, preserve input."""
    result = deepcopy(notebook)
    validate_notebook(result)
    namespace = {"__name__": "__main__", "NOTEBOOK_REPOSITORY_ROOT": str(Path(repository_root).resolve())}
    count = 0
    for cell in result["cells"]:
        if cell["cell_type"] != "code":
            continue
        count += 1
        stdout, stderr = io.StringIO(), io.StringIO()
        source = "".join(cell["source"])
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exec(compile(source, "<notebook:" + cell["id"] + ">", "exec"), namespace)
        cell["execution_count"] = count
        cell["outputs"] = [{"output_type": "stream", "name": name, "text": stream.getvalue()}
            for name, stream in (("stdout", stdout), ("stderr", stderr)) if stream.getvalue()]
    verified = namespace["verified"]
    result["metadata"]["fable_validation"].update(execution_engine="plain_python_top_down",
        executed_code_cells=count, verified=verified)
    result["cells"][0] = _cell("markdown", "tldr", f"""
        # V11 · 60分钟启动期限的保存账本复核

        ## tl;dr
        普通Python逐格检查成功：保留全部{verified['total_pairs']}个请求，其中{verified['n']}个配对差值已知、
        {verified['unknown_pairs']}个未知。改善{verified['improved']}、恶化{verified['worsened']}、未变{verified['unchanged']}。
        原版净均值{verified['baseline_mean_bp']}bp，新版{verified['candidate_mean_bp']}bp，配对D均值{verified['mean_delta_bp']}bp。
        {verified['timeout_exits']}个启动期限退出；已完成{verified['closed_cost_checks']}条保存成交的20bp成本与收益核对。
        这些是保存账本的内部复核；未重跑原始行情，未验证控制推断，盈利目标尚未获验收。
        Jupyter内核执行与完整schema validation仍未完成，具体缺口见末节。
        """)
    validate_notebook(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Plain Python top-down checks, not Jupyter execution")
    args = parser.parse_args()
    root = args.root.resolve()
    directory = (root / RESULTS_RELATIVE).resolve()
    summary = (directory / "summary.json").resolve()
    if not directory.is_relative_to(root) or summary.parent != directory:
        raise ValueError("Summary escaped fixed evidence directory")
    output = args.output.resolve()
    if output.suffix != ".ipynb" or output.exists():
        raise ValueError("Use a new .ipynb output; preserve existing evidence")
    notebook = build_notebook(hashlib.sha256(summary.read_bytes()).hexdigest())
    if args.check:
        notebook = execute_notebook(notebook, root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(notebook, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(output), "cells": len(notebook["cells"]),
        "validation": notebook["metadata"]["fable_validation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
