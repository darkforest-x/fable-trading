"""Synthetic saved-support receipts only; no archive, strategy or solver reads."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path

import pytest

from yoyo.evaluation import hourly_impulse_support_notebook as notebook


def save_csv(path, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    path.write_bytes(gzip.compress(stream.getvalue().encode(), mtime=0))


def synthetic_evidence(tmp_path):
    """251 artificial IDs; 154 disjoint triplets, 94 sparse, three unknown."""
    directory = tmp_path / notebook.RESULTS_RELATIVE
    directory.mkdir(parents=True)
    folds = [("2023H1", datetime(2023, 1, 1, tzinfo=timezone.utc)),
             ("2023H2", datetime(2023, 7, 1, tzinfo=timezone.utc)),
             ("2024H1", datetime(2024, 1, 1, tzinfo=timezone.utc)),
             ("2024H2", datetime(2024, 7, 1, tzinfo=timezone.utc))]
    mothers, edges, allocation, cursor = [], [], [], {}
    for index in range(251):
        mother_id = "synthetic_mother_" + str(index)
        fold, beginning = folds[index % 4]
        time = beginning + timedelta(hours=index // 4)
        unknown = index >= 248
        degree = 3 if index < 154 else index % 3 if not unknown else 0
        status = "matched" if index < 154 else "missing_causal_matching_support" if unknown else "insufficient_exact_controls"
        row = {"event_id": mother_id, "decision_time": time.isoformat(), "fold": fold,
               "match_status": status, "reconstructed_status": status,
               "mother_search_reached": "False" if unknown else "True",
               "preallocation_available": "" if unknown else degree,
               "available_before_greedy": "" if unknown else degree,
               "used_before_count": "" if unknown else 0,
               "selected_count": 3 if index < 154 else 0,
               "assigned_controls": 3 if index < 154 else 0,
               "same_slope_count": 3, "hourly_support_count": 3,
               "cross_exclusion_count": degree, "actual_mother_exclusion_count": degree,
               "positive_synthetic_stop_count": "" if unknown else degree,
               "unused_before_count": "" if unknown else degree}
        for stage in notebook.STAGE_NAMES:
            if stage + "_count" not in row:
                row[stage + "_count"] = 3
        mothers.append(row)
        bucket = time.hour // 6
        for _ in range(degree):
            current = cursor.get((fold, bucket), 0)
            cursor[(fold, bucket)] = current + 1
            candidate = beginning + timedelta(days=10 + current // 6, hours=bucket * 6 + current % 6)
            edge = {"event_id": mother_id, "candidate_id": candidate.isoformat(),
                    "candidate_time": candidate.isoformat(), "fold": fold}
            edges.append(edge)
            if index < 154:
                allocation.append({key: edge[key] for key in ("event_id", "candidate_id", "fold")})
    frames = dict(zip(notebook.EVIDENCE_FILES, (mothers, edges, allocation)))
    for name, rows in frames.items():
        save_csv(directory / name, rows)
    summary = {"experiment_id": notebook.EXPERIMENT_ID, "mothers": 251,
               "maximum_matched": 154, "greedy_matched": 154, "greedy_controls": 462,
               "matching_edges": len(edges), "maximum_coverage": 154 / 251,
               "required_complete_mothers": 226, "coverage_gate_attainable": False,
               "allocation_recoverable": 0, "historical_full_parity": True,
               "original_assignment_feasible": True,
               "capacity": {"connected_component_upper_bound": 154, "optimal": True, "matched_mothers": 154},
               "old_status_counts": {"matched": 154, "insufficient_exact_controls": 94, "missing_causal_matching_support": 3},
               "output_hashes": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in frames}}
    for flag in ("outcomes_read_or_computed", "profitability_test", "holdout_consumed", "training_eligible", "production_eligible"):
        summary[flag] = False
    (directory / "summary.json").write_text(json.dumps(summary))
    return directory, summary, frames


def prepared(directory):
    return notebook.build_notebook(hashlib.sha256((directory / "summary.json").read_bytes()).hexdigest())


def refresh(directory, summary, frames):
    for name, rows in frames.items():
        save_csv(directory / name, rows)
        summary["output_hashes"][name] = hashlib.sha256((directory / name).read_bytes()).hexdigest()
    (directory / "summary.json").write_text(json.dumps(summary))
    return prepared(directory)


def test_minimum_structure_unique_ids_compile_and_explicit_execution_gap():
    n = notebook.build_notebook("a" * 64)
    notebook.validate_notebook(n)
    assert n["nbformat"] == 4 and n["nbformat_minor"] == 5
    assert len({cell["id"] for cell in n["cells"]}) == len(n["cells"])
    all_text = "\n".join("".join(cell["source"]) for cell in n["cells"])
    for heading in ("## tl;dr", "## Context & Methods", "## Data", "## Results", "## Takeaways"):
        assert heading in all_text
    assert not n["metadata"]["fable_validation"]["jupyter_kernel_executed"]
    assert not n["metadata"]["fable_validation"]["full_nbformat_schema_validated"]
    assert all(cell["outputs"] == [] and cell["execution_count"] is None for cell in n["cells"] if cell["cell_type"] == "code")


def test_plain_python_exec_produces_real_outputs_and_preserves_input(tmp_path, monkeypatch):
    directory, _, _ = synthetic_evidence(tmp_path)
    n = prepared(directory)
    before = deepcopy(n)
    original_read_bytes = Path.read_bytes
    reads = []
    def audited_read(path):
        reads.append(path.name)
        assert path.name in ("summary.json", *notebook.EVIDENCE_FILES)
        return original_read_bytes(path)
    monkeypatch.setattr(Path, "read_bytes", audited_read)
    result = notebook.execute_notebook(n, tmp_path)
    assert n == before
    assert reads == ["summary.json", *notebook.EVIDENCE_FILES]
    codes = [cell for cell in result["cells"] if cell["cell_type"] == "code"]
    assert [cell["execution_count"] for cell in codes] == list(range(1, len(codes) + 1))
    output = "\n".join(o["text"] for cell in codes for o in cell["outputs"])
    assert "independently proved upper bound: 154" in output
    assert "154 mothers / 462 unique controls" in output
    assert "61.3546" in output and "Profitability was NOT tested" in output
    assert "'cross_exclusion': 94" in output
    assert "154母" in "".join(result["cells"][0]["source"])
    validation = result["metadata"]["fable_validation"]
    assert validation["execution_engine"] == "plain_python_top_down"
    assert validation["executed_code_cells"] == len(codes)
    assert not validation["jupyter_kernel_executed"] and not validation["full_nbformat_schema_validated"]


@pytest.mark.parametrize("change", ["summary_hash", "csv_hash", "lost_mother", "duplicate_mother", "forbidden_edge",
    "reuse", "partial_group", "wrong_time", "wrong_fold", "wrong_degree", "unknown_as_zero",
    "wrong_bound", "wrong_target", "wrong_status", "future_profit_flag"])
def test_tampering_or_inconsistent_saved_evidence_fails(tmp_path, change):
    directory, summary, frames = synthetic_evidence(tmp_path)
    before = prepared(directory)
    mothers, edges, allocation = (frames[name] for name in notebook.EVIDENCE_FILES)
    if change == "summary_hash":
        (directory / "summary.json").write_text((directory / "summary.json").read_text() + " ")
    elif change == "csv_hash":
        (directory / notebook.EVIDENCE_FILES[0]).write_bytes(b"changed")
    else:
        if change == "lost_mother": mothers.pop()
        elif change == "duplicate_mother": mothers[-1]["event_id"] = mothers[0]["event_id"]
        elif change == "forbidden_edge": allocation[0]["candidate_id"] = "2023-01-27T00:00:00+00:00"
        elif change == "reuse": allocation.append(dict(allocation[0]))
        elif change == "partial_group": allocation.pop()
        elif change == "wrong_time": edges[0]["candidate_time"] = "2023-01-27T00:00:00+00:00"
        elif change == "wrong_fold": edges[0]["fold"] = "2023H2"
        elif change == "wrong_degree": mothers[0]["preallocation_available"] = 4
        elif change == "unknown_as_zero": mothers[-1]["preallocation_available"] = 0
        elif change == "wrong_bound": summary["capacity"]["connected_component_upper_bound"] = 155
        elif change == "wrong_target": summary["required_complete_mothers"] = 154
        elif change == "wrong_status": summary["old_status_counts"]["matched"] = 155
        elif change == "future_profit_flag": summary["profitability_test"] = True
        before = refresh(directory, summary, frames)
    with pytest.raises(ValueError):
        notebook.execute_notebook(before, tmp_path)


@pytest.mark.parametrize("change", ["duplicate_id", "bad_id", "empty_id", "long_id", "bad_source", "syntax", "count", "outputs", "version"])
def test_notebook_field_validation_rejects_invalid_structure(change):
    n = notebook.build_notebook("b" * 64)
    code = next(cell for cell in n["cells"] if cell["cell_type"] == "code")
    if change == "duplicate_id": n["cells"][1]["id"] = n["cells"][0]["id"]
    elif change == "bad_id": code["id"] = "has space"
    elif change == "empty_id": code["id"] = ""
    elif change == "long_id": code["id"] = "x" * 65
    elif change == "bad_source": code["source"] = [None]
    elif change == "syntax": code["source"] = "if !!!"
    elif change == "count": code["execution_count"] = True
    elif change == "outputs": code["outputs"] = [{"output_type": "stream", "name": "invented", "text": "wrong"}]
    else: n["nbformat_minor"] = 4
    with pytest.raises((ValueError, SyntaxError)):
        notebook.validate_notebook(n)


def test_cli_checked_notebook_uses_new_output_and_will_not_overwrite(tmp_path, monkeypatch):
    synthetic_evidence(tmp_path)
    output = tmp_path / "companion.ipynb"
    monkeypatch.setattr("sys.argv", ["notebook", "--root", str(tmp_path), "--output", str(output), "--check"])
    notebook.main()
    saved = output.read_bytes()
    n = json.loads(saved)
    assert n["metadata"]["fable_validation"]["execution_engine"] == "plain_python_top_down"
    with pytest.raises(ValueError, match="new .ipynb"):
        notebook.main()
    assert output.read_bytes() == saved


def test_missing_evidence_fails_without_creating_output(tmp_path, monkeypatch):
    output = tmp_path / "never.ipynb"
    monkeypatch.setattr("sys.argv", ["notebook", "--root", str(tmp_path), "--output", str(output), "--check"])
    with pytest.raises(FileNotFoundError): notebook.main()
    assert not output.exists()


def test_generated_code_imports_only_standard_library_and_never_research_or_solver():
    import ast
    n = notebook.build_notebook("c" * 64)
    imports = []
    for cell in n["cells"]:
        if cell["cell_type"] == "code":
            for node in ast.walk(ast.parse("".join(cell["source"]))):
                if isinstance(node, ast.Import): imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom): imports.append(node.module)
    assert set(imports) <= {"csv", "gzip", "hashlib", "io", "json", "math", "collections", "datetime", "pathlib"}
