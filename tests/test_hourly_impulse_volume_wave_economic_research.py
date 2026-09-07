"""Synthetic V32 clock checks; no actual saved outcome table is accessed."""
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_volume_wave_economic_research import preflight


def frame():
    return pd.DataFrame(dict(decision_time=["2023-02-01T04:00:00Z"],
        endpoint_time=["2023-02-01T08:00:00Z"], horizon_hours=[4]))


def test_native_hour_development_clock():
    result = preflight(frame())
    assert result["decision_time"]["first"] == "2023-02-01 04:00:00+00:00"


@pytest.mark.parametrize("column,value", [
    ("decision_time", "2023-02-01 04:00:00"),
    ("decision_time", "2023-02-01T04:15:00Z"),
    ("endpoint_time", "2025-01-01T08:00:00Z"),
    ("decision_time", "2022-12-31T04:00:00Z"),
    ("endpoint_time", "2023-02-01T09:00:00Z"),
    ("horizon_hours", 5), ("decision_time", 1675224000),
])
def test_reject_scope_clock_or_unregistered_horizon(column, value):
    data = frame()
    data.loc[0, column] = value
    with pytest.raises(ValueError):
        preflight(data)

from copy import deepcopy
from yoyo.evaluation.hourly_impulse_volume_wave_economic_research import V31, validate_support_receipts


def receipts():
    sources = [{"path": "synthetic-source.py", "sha256": "source-sha"}]
    states = {"case": {"accepted": 100, "abstain": 148, "unknown": 3},
              "control": {"accepted": 401, "abstain": 343, "unknown": 0}}
    inputs = {str(V31/"results/support_frozen.json"): "freeze-sha"}
    outputs = {kind+"_context.csv.gz": kind+"-sha" for kind in states}
    inputs.update({str(V31/"results"/p): sha for p, sha in outputs.items()})
    config = {"inputs": inputs, "population": {k+"_states": v for k,v in states.items()}}
    started = {"sources": sources, "builder_commit": "synthetic-commit", "at": "2026-09-07T01:00:00Z"}
    frozen = dict(started, at="2026-09-07T01:01:00Z", output_hashes=outputs,
        outcomes_read_or_computed=False, raw5_read=False, holdout_consumed=False)
    # The historical V31 summary deliberately has no sources field.
    support = dict(support_pass=True, status="support_pass_requires_separate_outcome_preregistration",
        outcomes_read_or_computed=False, economic_acceptance=False, support_frozen_sha256="freeze-sha",
        builder_commit="synthetic-commit", generated_at="2026-09-07T01:02:00Z",
        accepted_case_control_states=dict(total=300, accepted=158, abstain=142, unknown=0, known=300),
        population=states, output_hashes=outputs)
    audit = dict(status="passed", scalar_replay=True, contexts=995, economic_outcomes_read=False,
        raw5_read=False, holdout_consumed=False, at="2026-09-07T01:03:00Z",
        input_and_output_hashes={**inputs, "synthetic-source.py":"source-sha"},
        direction_symmetry_control={k: dict(accepted=v["accepted"], direction_reversed_accepted=v["abstain"],
            flat=0, known=v["accepted"]+v["abstain"]) for k,v in states.items()})
    return audit, support, frozen, started, config


def test_actual_v31_schema_has_no_summary_sources():
    args = receipts()
    assert "sources" not in args[1]
    validate_support_receipts(*args)


@pytest.mark.parametrize("which,field,value", [
    (0,"economic_outcomes_read",True), (1,"support_pass",False),
    (1,"generated_at","2026-09-07T00:00:00Z"), (2,"builder_commit","wrong"),
    (0,"input_and_output_hashes",{}), (1,"support_frozen_sha256","wrong"),
    (1,"accepted_case_control_states",{}), (0,"scalar_replay",False),
])
def test_frozen_provenance_fails_closed(which, field, value):
    args = deepcopy(receipts())
    args[which][field] = value
    with pytest.raises(ValueError):
        validate_support_receipts(*args)
