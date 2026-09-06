"""Build a saved-evidence-only V10 companion notebook, with honest fallback QA.

No nbformat/nbclient/ipykernel dependency is available in the contracted runtime.
The notebook follows nbformat4.5's documented minimum JSON fields and cell IDs:
https://nbformat.readthedocs.io/en/latest/format_description.html
We check those fields and compile code, but do NOT claim full schema validation.
Optional --check runs generated cells in one plain Python namespace and records
actual stream outputs; it is NOT Jupyter-kernel execution. No strategy or solver
is imported. Only the pinned summary and three saved V10 support CSVs are read.
Source/price hashes in upstream receipts are identity claims, not reread here.
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
import sys
import textwrap


EXPERIMENT_ID = "exp-btcusdtp-1h-matching-support-preholdout-20260906-v10"
RESULTS_RELATIVE = "experiments/active/" + EXPERIMENT_ID + "/results"
DEFAULT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_FILES = ("mother_audit.csv.gz", "eligible_edges.csv.gz", "maximum_allocation.csv.gz")
STAGE_NAMES = ("same_month", "same_utc6h", "same_vol_bucket", "same_5m_colour", "same_hourly_colour",
               "same_slope", "fold_embargo", "vol_support", "atr_support", "entry_open_support",
               "entry_continuity_support", "five_minute_support", "hourly_support", "cross_exclusion",
               "actual_mother_exclusion", "positive_synthetic_stop", "unused_before")
GAP = ("Plain Python top-down execution is not Jupyter-kernel execution. "
       "Minimum nbformat4.5 structure and code compilation are checked; full nbformat schema validation is not run. "
       "nbformat, nbclient and ipykernel are unavailable; no dependencies were installed.")


def _cell(kind, identifier, source):
    cell = {"cell_type": kind, "id": identifier, "metadata": {},
            "source": textwrap.dedent(source).strip().splitlines(keepends=True)}
    if kind == "code":
        cell.update(execution_count=None, outputs=[])
    return cell


def build_notebook(summary_sha256: str) -> dict:
    """Create an unexecuted notebook; this function reads no evidence files."""
    if not isinstance(summary_sha256, str) or not re.fullmatch("[a-f0-9]{64}", summary_sha256):
        raise ValueError("A pinned summary SHA256 is required")
    cells = [
        _cell("markdown", "tldr", """
        # V10 · 原1h入口的严格匹配支持复核

        ## tl;dr
        这不是收益回测。逐格执行后，结果会独立核对全部母事件、完整三配对、
        候选时间不复用，以及图的容量上界。未执行的笔记本不提供已验证结果。
        本轮只解释固定匹配设计的支持度；owner的盈利目标尚未完成。
        """),
        _cell("markdown", "context", """
        ## Context & Methods
        固定V10审计：251个原始1h K1母事件，每母三个控制，90%需要226母。
        原匹配键、风险转移、当前/前一小时cross排除及严格foldEnd−72h边界不变。
        本笔记本不重建这些特征，也不调用策略、MILP或任何交易执行器。

        ### Key Assumptions
        保存图是上游已核对的合法事前边；此处验证其哈希和内部一致性，
        不以再次读取原始行情证明边的因果性。全月分配是离线支持审计，不是实时分配。
        其他最优分配可能选择不同母事件；一份解未选某母，不证明该母永远无法匹配。
        同一母的阶段计数是有固定顺序的描述，不是删除各规则的因果效应。
        """),
        _cell("markdown", "data", """
        ## Data
        只读下列固定目录中的summary.json及三个保存的审计CSV；不扫描其他文件，
        不读取价格、收益、MFE、K2成败或策略输出。summary的SHA固定在下一格，
        CSV字节必须与summary.output_hashes一致。上游价格哈希只作为来源身份，不重算。
        从仓库任意子目录打开；也可先设置NOTEBOOK_REPOSITORY_ROOT为仓库路径。
        """),
        _cell("code", "setup", f"""
        import csv, gzip, hashlib, io, json, math
        from collections import Counter, defaultdict, deque
        from datetime import datetime, timezone
        from pathlib import Path

        RESULTS_RELATIVE = {RESULTS_RELATIVE!r}
        SUMMARY_SHA256 = {summary_sha256!r}
        EVIDENCE_FILES = {EVIDENCE_FILES!r}
        COUNT_PER_MOTHER = 3
        EXPECTED_MOTHERS = 251
        REQUIRED_COMPLETE = 226
        def require(condition, message):
            if not condition:
                raise ValueError(message)
        hint = globals().get("NOTEBOOK_REPOSITORY_ROOT")
        roots = [Path(hint)] if hint is not None else [Path.cwd(), *Path.cwd().parents]
        repository_root = next((p.resolve() for p in roots if (p / RESULTS_RELATIVE / "summary.json").is_file()), None)
        require(repository_root is not None, "Run from the repository or set NOTEBOOK_REPOSITORY_ROOT")
        results_directory = (repository_root / RESULTS_RELATIVE).resolve()
        require(results_directory.is_relative_to(repository_root), "Evidence directory escaped repository")
        def evidence_path(name):
            require(name in ("summary.json", *EVIDENCE_FILES), "File is not in the evidence allowlist")
            path = (results_directory / name).resolve()
            require(path.parent == results_directory, "Evidence symlink escaped its directory")
            return path
        print("Saved support evidence:", RESULTS_RELATIVE)
        """),
        _cell("markdown", "load-heading", "### 1. 固定来源并读取保存账本"),
        _cell("code", "load", """
        summary_bytes = evidence_path("summary.json").read_bytes()
        require(hashlib.sha256(summary_bytes).hexdigest() == SUMMARY_SHA256, "Pinned summary hash mismatch")
        def reject_json_constant(value):
            raise ValueError("Nonfinite JSON number: " + value)
        summary = json.loads(summary_bytes, parse_constant=reject_json_constant)
        require(summary["experiment_id"] == "exp-btcusdtp-1h-matching-support-preholdout-20260906-v10", "Wrong experiment")
        for flag in ("outcomes_read_or_computed", "profitability_test", "holdout_consumed", "training_eligible", "production_eligible"):
            require(summary[flag] is False, "Unexpected outcome/production flag: " + flag)
        require(summary["historical_full_parity"] is True and summary["original_assignment_feasible"] is True, "Missing upstream parity claim")
        tables = {}
        for name in EVIDENCE_FILES:
            payload = evidence_path(name).read_bytes()
            require(hashlib.sha256(payload).hexdigest() == summary["output_hashes"][name], "CSV hash mismatch: " + name)
            reader = csv.DictReader(io.StringIO(gzip.decompress(payload).decode("utf-8")))
            require(reader.fieldnames is not None and len(set(reader.fieldnames)) == len(reader.fieldnames), "Duplicate/missing CSV headers")
            rows = list(reader)
            require(all(None not in r and all(v is not None for v in r.values()) for r in rows), "Malformed CSV row")
            tables[name] = rows
        mothers = tables["mother_audit.csv.gz"]
        edges = tables["eligible_edges.csv.gz"]
        allocation = tables["maximum_allocation.csv.gz"]
        print("SHA256 verified: summary plus", len(tables), "saved CSVs")
        print("Rows:", {name: len(rows) for name, rows in tables.items()})
        """),
        _cell("markdown", "population-heading", "### 2. 保留全部母事件并核对图、时钟与分配"),
        _cell("code", "population", """
        def integer(value, allow_missing=False):
            if allow_missing and value == "":
                return None
            number = float(value)
            require(math.isfinite(number) and number >= 0 and number.is_integer(), "Invalid nonnegative integer")
            return int(number)
        def stamp(value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            require(parsed.tzinfo is not None, "Timestamp must have timezone")
            return parsed.astimezone(timezone.utc)
        mother_by_id = {r["event_id"]: r for r in mothers}
        require(len(mothers) == len(mother_by_id) == summary["mothers"] == EXPECTED_MOTHERS, "Lost/duplicated mother IDs")
        require(all(mother_by_id), "Empty mother ID")
        fold_ends = {"2023H1": "2023-07-01", "2023H2": "2024-01-01", "2024H1": "2024-07-01", "2024H2": "2025-01-01"}
        fold_starts = {"2023H1": "2023-01-01", "2023H2": "2023-07-01", "2024H1": "2024-01-01", "2024H2": "2024-07-01"}
        from datetime import timedelta
        def valid_fold_time(value, fold):
            require(fold in fold_ends, "Unknown fold")
            time = stamp(value)
            start = stamp(fold_starts[fold] + "T00:00:00+00:00")
            end = stamp(fold_ends[fold] + "T00:00:00+00:00") - timedelta(hours=72)
            require(start <= time < end and time.minute == time.second == time.microsecond == 0, "Decision outside exact hourly fold/embargo")
            return time
        for row in mothers:
            valid_fold_time(row["decision_time"], row["fold"])
        edge_pairs = set()
        neighbours = {mother: set() for mother in mother_by_id}
        candidate_owners = defaultdict(set)
        candidate_folds = defaultdict(set)
        for row in edges:
            mother, candidate = row["event_id"], row["candidate_id"]
            require(mother in mother_by_id and candidate, "Orphan/empty graph ID")
            require(row["fold"] == mother_by_id[mother]["fold"], "Edge fold mismatch")
            time = valid_fold_time(candidate, row["fold"])
            require(time.isoformat() == candidate and stamp(row["candidate_time"]) == time, "Candidate identity/time mismatch")
            mother_time = stamp(mother_by_id[mother]["decision_time"])
            require((time.year, time.month, time.hour // 6) == (mother_time.year, mother_time.month, mother_time.hour // 6), "Different month/time bucket")
            pair = (mother, candidate)
            require(pair not in edge_pairs, "Duplicate admissible edge")
            edge_pairs.add(pair)
            neighbours[mother].add(candidate)
            candidate_owners[candidate].add(mother)
            candidate_folds[candidate].add(row["fold"])
        require(all(len(folds) == 1 for folds in candidate_folds.values()), "Cross-fold control reuse")
        require(len(edges) == summary["matching_edges"], "Edge count mismatch")
        selected_pairs = [(r["event_id"], r["candidate_id"]) for r in allocation]
        require(len(selected_pairs) == len(set(selected_pairs)), "Duplicate allocation edge")
        require(set(selected_pairs) <= edge_pairs, "Allocated forbidden edge")
        require(len({candidate for _, candidate in selected_pairs}) == len(allocation), "Control timestamp reused")
        selected_counts = Counter(mother for mother, _ in selected_pairs)
        require(all(n == COUNT_PER_MOTHER for n in selected_counts.values()), "Partial control groups")
        require(all(r["fold"] == mother_by_id[r["event_id"]]["fold"] for r in allocation), "Allocation fold mismatch")
        matched_count = len(selected_counts)
        require(matched_count == summary["maximum_matched"], "Allocation and summary disagree")
        require(len(allocation) == COUNT_PER_MOTHER * matched_count, "Wrong group size")
        print("All", len(mothers), "mother IDs retained; complete allocation:", matched_count, "mothers /", len(allocation), "unique controls")
        """),
        _cell("markdown", "results", """
        ## Results
        下列输出来自保存账本的独立复算。缺失支持保留为未知可用数，不能伪装成零供给。
        阶段分解使用报告相同的全部17个阶段，从same_month到unused_before，按固定次序
        取首次低于3的位置；只有原可用数≥3、被先前分配消耗至不足3，才归于unused_before。
        它不能与原始供给不足混为一谈；不合并fold_embargo与其他支持缺失。
        """),
        _cell("code", "decomposition", """
        status_counts = Counter(r["match_status"] for r in mothers)
        require(dict(status_counts) == summary["old_status_counts"], "Historical status counts mismatch")
        decomposition = Counter()
        checkpoints = [(stage + "_count", stage) for stage in __STAGE_NAMES__]
        for row in mothers:
            require(row["match_status"] == row["reconstructed_status"], "Reconstructed status mismatch")
            before = integer(row["preallocation_available"], allow_missing=True)
            available = integer(row["available_before_greedy"], allow_missing=True)
            degree = len(neighbours[row["event_id"]])
            if row["mother_search_reached"] == "False":
                require(before is None and available is None and degree == 0, "Missing support misrepresented as zero/known supply")
                require(integer(row["selected_count"]) == integer(row["assigned_controls"]) == 0, "Unknown mother cannot have assigned controls")
                require(row["match_status"] != "matched", "Unknown mother cannot be matched")
                decomposition["mother_missing_support"] += 1
                continue
            require(row["mother_search_reached"] == "True", "Unknown search flag")
            require(before == degree and available is not None, "Preallocation count does not equal graph degree")
            require(integer(row["used_before_count"]) + available == before, "Greedy consumption arithmetic mismatch")
            values = [integer(row[column]) for column, _ in checkpoints]
            require(all(a >= b for a, b in zip(values, values[1:])), "Nonmonotone ordered stage counts")
            require(values[-2:] == [before, available], "Stage availability mismatch")
            expected_selected = COUNT_PER_MOTHER if available >= COUNT_PER_MOTHER else 0
            require(integer(row["selected_count"]) == integer(row["assigned_controls"]) == expected_selected, "Greedy partial/reserved controls")
            require((row["match_status"] == "matched") == bool(expected_selected), "Greedy status inconsistent with availability")
            reason = next((label for (_, label), value in zip(checkpoints, values) if value < COUNT_PER_MOTHER), "matched")
            decomposition[reason] += 1
        require(sum(decomposition.values()) == EXPECTED_MOTHERS, "Decomposition lost mothers")
        require(status_counts["matched"] == summary["greedy_matched"], "Greedy count mismatch")
        require(summary["greedy_controls"] == COUNT_PER_MOTHER * status_counts["matched"], "Historical group count mismatch")
        print("Historical statuses:", dict(sorted(status_counts.items())))
        print("Ordered support decomposition:", dict(sorted(decomposition.items())))
        """.replace("__STAGE_NAMES__", repr(STAGE_NAMES))),
        _cell("markdown", "bound-heading", "### 3. 不调用求解器，重算连通分量容量上界"),
        _cell("code", "capacity-proof", """
        visited = set()
        components = []
        for initial in sorted(mother_by_id):
            if initial in visited:
                continue
            pending, component_mothers, component_candidates = deque([initial]), set(), set()
            while pending:
                mother = pending.popleft()
                if mother in visited:
                    continue
                visited.add(mother)
                component_mothers.add(mother)
                for candidate in neighbours[mother]:
                    if candidate not in component_candidates:
                        component_candidates.add(candidate)
                        pending.extend(candidate_owners[candidate] - visited)
            upper = min(len(component_mothers), len(component_candidates) // COUNT_PER_MOTHER)
            components.append((len(component_mothers), len(component_candidates), upper))
        require(visited == set(mother_by_id), "BFS omitted isolated mothers")
        upper_bound = sum(upper for _, _, upper in components)
        require(upper_bound == summary["capacity"]["connected_component_upper_bound"], "Saved component bound mismatch")
        require(matched_count <= upper_bound, "Feasible solution exceeds graph upper bound")
        require(matched_count == upper_bound, "This graph bound is not tight; independent maximum NOT certified")
        require(summary["capacity"]["optimal"] is True and summary["capacity"]["matched_mothers"] == matched_count, "Upstream certificate mismatch")
        require(summary["required_complete_mothers"] == REQUIRED_COMPLETE, "Coverage target changed")
        require(math.isclose(summary["maximum_coverage"], matched_count / EXPECTED_MOTHERS, rel_tol=0, abs_tol=1e-12), "Coverage denominator changed")
        require(summary["coverage_gate_attainable"] is (matched_count >= REQUIRED_COMPLETE), "Coverage gate contradiction")
        require(summary["allocation_recoverable"] == matched_count - status_counts["matched"], "Recoverable allocation mismatch")
        print("Connected components:", len(components), "; independently proved upper bound:", upper_bound)
        print("Feasible allocation reaches bound:", matched_count, "; maximum coverage:", round(100 * matched_count / EXPECTED_MOTHERS, 4), "%")
        print("90% requires", REQUIRED_COMPLETE, "; additional complete mothers available by reallocation:", matched_count - status_counts["matched"])
        print("Profitability was NOT tested; owner profit goal remains unachieved.")
        """),
        _cell("markdown", "takeaways", """
        ## Takeaways
        只有上一格全部核验成功，才由“合法完整分配下界=连通分量上界”独立证明固定图最大容量。
        这不是再次运行MILP。此证明只属于保存图，不扩张到新月份、放宽后的匹配规则或收益表现。
        支持不足不能通过降低90%门、减少控制或把可支持子集冒充251母全体来解决。
        即使支持充分也不等于赚钱；本笔记本没有计算任何收益，owner盈利目标仍未完成。

        ### Execution gap
        生成器的`--check`仅在一个普通Python命名空间从上到下执行并捕获真实stdout/stderr，
        **不是Jupyter kernel execution，也没有完成完整nbformat schema validation**。
        已检查最小4.5结构、合法唯一cell IDs与全部code compile。缺少nbformat、nbclient、ipykernel，未安装依赖。
        在另一个已配好这些依赖的环境，可另行运行：

        ```bash
        python -m jupyter nbconvert --execute --to notebook --inplace PATH_TO_NOTEBOOK.ipynb
        ```

        完整schema验证还需`nbformat.validate(nbformat.read(path, as_version=4))`。
        这两步尚未执行；不要把本次普通Python检查转述为Jupyter或浏览器验证通过。
        """),
    ]
    notebook = {"nbformat": 4, "nbformat_minor": 5, "cells": cells,
                "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
                             "language_info": {"name": "python", "version": sys.version.split()[0]},
                             "fable_validation": {"execution_engine": "not_executed", "jupyter_kernel_executed": False,
                                 "full_nbformat_schema_validated": False, "gap": GAP,
                                 "summary_sha256": summary_sha256, "evidence_files": list(EVIDENCE_FILES)}}}
    validate_notebook(notebook)
    notebook["metadata"]["fable_validation"].update(code_compilation_validated=True,
                                                   minimum_structure_validated=True)
    return notebook


def validate_notebook(notebook: dict) -> None:
    """Validate the generated minimum structure and compile; not full nbformat."""
    if (type(notebook.get("nbformat")) is not int or type(notebook.get("nbformat_minor")) is not int
            or notebook["nbformat"] != 4 or notebook["nbformat_minor"] != 5 or not isinstance(notebook.get("metadata"), dict)):
        raise ValueError("Expected nbformat4.5 with metadata")
    if not isinstance(notebook.get("cells"), list) or not notebook["cells"]:
        raise ValueError("Notebook needs cells")
    seen = set()
    for cell in notebook["cells"]:
        identifier = cell.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier) or identifier in seen:
            raise ValueError("Unique valid nbformat4.5 cell IDs required")
        seen.add(identifier)
        if not isinstance(cell.get("metadata"), dict):
            raise ValueError("Cell metadata required")
        source = cell.get("source")
        if isinstance(source, list) and all(isinstance(line, str) for line in source):
            source = "".join(source)
        if not isinstance(source, str):
            raise ValueError("Source must be text or text lines")
        if cell.get("cell_type") == "code":
            if "execution_count" not in cell:
                raise ValueError("Code execution_count field required, including before execution")
            compile(source, "<notebook:" + identifier + ">", "exec")
            count = cell.get("execution_count")
            if count is not None and (type(count) is not int or count < 1):
                raise ValueError("Invalid execution count")
            if not isinstance(cell.get("outputs"), list):
                raise ValueError("Code outputs required")
            for output in cell["outputs"]:
                if output.get("output_type") != "stream" or output.get("name") not in ("stdout", "stderr") or not isinstance(output.get("text"), str):
                    raise ValueError("Only captured text stream outputs are generated")
        elif cell.get("cell_type") != "markdown" or "outputs" in cell or "execution_count" in cell:
            raise ValueError("Only markdown and code cells are generated")
    json.dumps(notebook, allow_nan=False)


def execute_notebook(notebook: dict, repository_root: Path) -> dict:
    """Execute generated code cells top-down in plain Python; preserve input."""
    result = deepcopy(notebook)
    validate_notebook(result)
    namespace = {"__name__": "__main__", "NOTEBOOK_REPOSITORY_ROOT": str(Path(repository_root).resolve())}
    count = 0
    for cell in result["cells"]:
        if cell["cell_type"] != "code":
            continue
        count += 1
        stdout, stderr = io.StringIO(), io.StringIO()
        source = "".join(cell["source"]) if isinstance(cell["source"], list) else cell["source"]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exec(compile(source, "<notebook:" + cell["id"] + ">", "exec"), namespace)
        cell["execution_count"] = count
        cell["outputs"] = [{"output_type": "stream", "name": name, "text": stream.getvalue()}
                           for name, stream in (("stdout", stdout), ("stderr", stderr)) if stream.getvalue()]
    result["metadata"]["fable_validation"].update(execution_engine="plain_python_top_down",
        executed_code_cells=count, code_compilation_validated=True, minimum_structure_validated=True)
    verified = {"mothers": namespace["EXPECTED_MOTHERS"], "complete_mothers": namespace["matched_count"],
                "component_upper_bound": namespace["upper_bound"],
                "required_complete_mothers": namespace["REQUIRED_COMPLETE"],
                "recoverable_by_reallocation": namespace["matched_count"] - namespace["status_counts"]["matched"]}
    result["metadata"]["fable_validation"]["verified_support"] = verified
    result["cells"][0] = _cell("markdown", "tldr", f"""
        # V10 · 原1h入口的严格匹配支持复核

        ## tl;dr
        普通Python逐格检查成功：全部{verified['mothers']}母保留；完整三配对{verified['complete_mothers']}母
        （{100 * verified['complete_mothers'] / verified['mothers']:.2f}%），独立图上界同为{verified['component_upper_bound']}。
        重新分配可恢复{verified['recoverable_by_reallocation']}母；90%需要{verified['required_complete_mothers']}母。
        这些数字来自下面的保存账本复核，不是收益回测。owner盈利目标尚未完成。
        Jupyter内核执行及完整nbformat schema validation未完成，具体缺口见末节。
        """)
    validate_notebook(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Execute generated code cells in plain Python, not a Jupyter kernel")
    args = parser.parse_args()
    root = args.root.resolve()
    summary = (root / RESULTS_RELATIVE / "summary.json").resolve()
    if not summary.is_relative_to(root) or summary.parent != (root / RESULTS_RELATIVE).resolve():
        raise ValueError("Summary escaped fixed evidence directory")
    output = args.output.resolve()
    if output.suffix != ".ipynb" or output.exists():
        raise ValueError("Use a new .ipynb output; do not overwrite evidence")
    notebook = build_notebook(hashlib.sha256(summary.read_bytes()).hexdigest())
    if args.check:
        notebook = execute_notebook(notebook, root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(notebook, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(output), "cells": len(notebook["cells"]),
                      "validation": notebook["metadata"]["fable_validation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
