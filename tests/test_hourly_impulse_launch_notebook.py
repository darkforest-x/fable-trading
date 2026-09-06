"""Synthetic251-row saved-ledger fixtures; no archive, strategy or kernel."""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import io
import json

import pytest

from yoyo.evaluation.hourly_impulse_launch_notebook import (
    EVIDENCE_FILES, EXPERIMENT_ID, RESULTS_RELATIVE, build_notebook, execute_notebook, validate_notebook,
)


def synthetic_evidence(tmp_path, mutation=None):
    cases, mechanics = [], []
    for index in range(251):
        entry = datetime(2023, 2, 1, tzinfo=timezone.utc) + timedelta(hours=index)
        unknown = index == 3
        before = None if unknown else .008
        after = None if unknown else {0: .004, 1: .012, 2: -.006}.get(index, .008)
        difference = None if unknown else after-before
        cases.append({"event_id": str(index), "mother_decision_time": entry.isoformat(),
            "before": before, "after": after, "difference": difference})
        row = {"event_id": str(index), "difference": difference}
        for suffix, net in (("before", before), ("after", after)):
            hold = 60 if index < 3 and suffix == "after" else 120
            gross = None if net is None else net + .002
            row.update({"entry_time_"+suffix: entry.isoformat(), "entry_price_"+suffix: 100,
                "initial_stop_"+suffix: 90, "direction_"+suffix: 1, "closed_"+suffix: not unknown,
                "gross_return_"+suffix: gross, "net_return_"+suffix: net,
                "exit_price_"+suffix: None if unknown else 100*(1+gross),
                "exit_time_"+suffix: None if unknown else (entry+timedelta(minutes=hold)).isoformat(),
                "hold_minutes_"+suffix: None if unknown else hold,
                "outcome_"+suffix: "censored" if unknown else ("launch_timeout_exit" if index < 3 and suffix == "after" else "transition_colour_exit")})
        mechanics.append(row)
    policy = {"management_minutes": 5, "ma_kind": "SMA", "ma_length": 40,
              "exit_mode": "transition_colour", "confirmations": 1}
    differences = [r["difference"] for r in cases if r["difference"] is not None]
    summary = {"experiment_id": EXPERIMENT_ID, "status": "diagnostic_only_no_candidate_acceptance",
        "holdout_consumed": False, "audit_prices_loaded": False, "production_eligible": False,
        "training_eligible": False, "all_financial_gates_pass": False, "known_coverage_ceiling": 154/251,
        "arms": {"baseline": {"policy": policy}, "candidate": {"policy": {**policy,
            "launch_deadline_minutes": 60, "launch_progress_r": .5}}},
        "effects": {"case_delta": {"total_pairs": 251, "n": 250, "unknown_pairs": 1, "improved": 1,
            "worsened": 2, "unchanged": 247, "mean_bp": sum(differences)/len(differences)*1e4}},
        "mechanics": {"timeout_exits": 3}, "output_hashes": {}}
    if mutation:
        mutation(cases, mechanics, summary)
    directory = tmp_path / RESULTS_RELATIVE
    directory.mkdir(parents=True)
    for name, rows in zip(EVIDENCE_FILES, (cases, mechanics)):
        text = io.StringIO()
        writer = csv.DictWriter(text, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        payload = text.getvalue().encode()
        if name.endswith(".gz"):
            payload = gzip.compress(payload, mtime=0)
        (directory / name).write_bytes(payload)
        summary["output_hashes"][name] = hashlib.sha256(payload).hexdigest()
    payload = json.dumps(summary, allow_nan=False).encode()
    (directory / "summary.json").write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_compiles_minimum_structure_without_reading_or_execution():
    notebook = build_notebook("a"*64)
    validate_notebook(notebook)
    metadata = notebook["metadata"]["fable_validation"]
    assert metadata["execution_engine"] == "not_executed"
    assert not metadata["jupyter_kernel_executed"] and not metadata["full_nbformat_schema_validated"]
    assert sum(cell["cell_type"] == "code" for cell in notebook["cells"]) == 4
    assert all(cell["outputs"] == [] for cell in notebook["cells"] if cell["cell_type"] == "code")


def test_plain_python_outputs_real_counts_unknowns_costs_and_honest_gaps(tmp_path):
    notebook = build_notebook(synthetic_evidence(tmp_path))
    saved = deepcopy(notebook)
    result = execute_notebook(notebook, tmp_path)
    assert notebook == saved
    status = result["metadata"]["fable_validation"]
    assert status["execution_engine"] == "plain_python_top_down"
    assert status["executed_code_cells"] == 4
    assert status["verified"]["total_pairs"] == 251
    assert status["verified"]["unknown_pairs"] == 1
    assert status["verified"]["closed_cost_checks"] == 500
    assert status["verified"]["timeout_exits"] == 3
    assert not status["jupyter_kernel_executed"] and not status["full_nbformat_schema_validated"]
    assert "全部251" in "".join(result["cells"][0]["source"])
    assert any("20bp" in output["text"] for cell in result["cells"] if cell["cell_type"] == "code" for output in cell["outputs"])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("mutation", [
    lambda c,m,s: c[0].update(difference=.123),
    lambda c,m,s: m[0].update(gross_return_before=.5),
    lambda c,m,s: m[0].update(exit_price_before=999),
    lambda c,m,s: m[0].update(hold_minutes_after=65),
    lambda c,m,s: m[0].update(initial_stop_after=80),
    lambda c,m,s: m[4].update(net_return_after=.7),
    lambda c,m,s: s["effects"]["case_delta"].update(mean_bp=7),
    lambda c,m,s: s["effects"]["case_delta"].update(unknown_pairs=0),
    lambda c,m,s: s.update(all_financial_gates_pass=True),
    lambda c,m,s: s["arms"]["candidate"]["policy"].update(launch_progress_r=.75),
    lambda c,m,s: c[1].update(event_id=c[0]["event_id"]),
])
def test_corrupt_internally_rehashed_evidence_fails_closed(tmp_path, mutation):
    notebook = build_notebook(synthetic_evidence(tmp_path, mutation))
    with pytest.raises(ValueError):
        execute_notebook(notebook, tmp_path)


def test_source_bytes_must_match_pinned_summary(tmp_path):
    digest = synthetic_evidence(tmp_path)
    directory = tmp_path / RESULTS_RELATIVE
    with (directory / "case_delta.csv").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="CSV hash mismatch"):
        execute_notebook(build_notebook(digest), tmp_path)


def test_summary_bytes_pinned_and_symlink_escape_rejected(tmp_path):
    digest = synthetic_evidence(tmp_path)
    with pytest.raises(ValueError, match="Pinned summary hash"):
        execute_notebook(build_notebook("b"*64), tmp_path)
    directory = tmp_path / RESULTS_RELATIVE
    outside = tmp_path / "saved.csv"
    evidence = directory / "case_delta.csv"
    evidence.rename(outside)
    evidence.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink escaped"):
        execute_notebook(build_notebook(digest), tmp_path)


@pytest.mark.parametrize("value", [None, "", "g"*64, "a"*63])
def test_hash_required(value):
    with pytest.raises(ValueError, match="SHA256"):
        build_notebook(value)
