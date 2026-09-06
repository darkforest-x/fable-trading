"""Pure synthetic V9 report metadata and complete-cohort contract."""
import copy
import json

import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_cadence_report import CHART_ID, build_artifact


def fixture():
    delta = pd.DataFrame({"event_id": ["a", "b", "c"],
        "mother_decision_time": pd.date_range("2023-02-01", periods=3, freq="h", tz="UTC"),
        "before": [.01, .01, .01], "after": [.011, .008, .01], "difference": [.001, -.002, 0.]})
    summary = {"effects": {"case_delta": {"total_pairs": 3, "n": 3, "unknown_pairs": 0,
        "improved": 1, "worsened": 1, "unchanged": 1, "mean_bp": -10/3}}}
    summary["arms"] = [{"policy": {"management_minutes": 5, "ma_kind": "SMA", "ma_length": 40,
        "exit_mode": "transition_colour", "confirmations": 1, "decision_minutes": cadence}}
        for cadence in (5, 15)]
    md = "# Exit Cadence\n\n## Summary\n<!-- SOURCE: v9_summary -->\nThree original requests.\n\n## Distribution\nAll tails retained.\n<!-- V9_DISTRIBUTION -->\n\n## Caveat\nNot profit.\n"
    kwargs = dict(markdown_path="analysis/v9.md", summary_path="experiments/v9/summary.json",
        case_delta_path="experiments/v9/case_delta.csv", generated_at="2026-09-06T00:00:00Z", fixture=True)
    return md, summary, delta, kwargs


def test_full_structure_narrative_and_source_identity():
    md, summary, delta, kwargs = fixture()
    saved = copy.deepcopy(summary)
    artifact = build_artifact(md, summary, delta, **kwargs)
    assert summary == saved
    assert len(artifact["manifest"]["blocks"]) == 5
    assert artifact["manifest"]["blocks"][1]["body"] == "## Summary\nThree original requests."
    assert artifact["manifest"]["blocks"][-1]["body"] == "## Caveat\nNot profit."
    assert artifact["sources"] == artifact["manifest"]["sources"]
    ids = {row["id"] for row in artifact["sources"]}
    assert {"v9_summary", "v9_case_delta"} <= ids
    assert not any("v8" in row["id"] for row in artifact["sources"])
    chart = artifact["manifest"]["charts"][0]
    assert chart["dataset"] == CHART_ID
    assert sum(row["bin_count"] for row in artifact["snapshot"]["datasets"][CHART_ID]) == 3
    query = next(row["query"] for row in artifact["sources"] if row["id"] == "v9_case_delta")
    assert "native15m" not in json.dumps(query)
    assert "SAME native5m" in query["description"]
    json.dumps(artifact, allow_nan=False)


def test_bad_arithmetic_and_counts_rejected():
    md, summary, delta, kwargs = fixture()
    delta.loc[0, "difference"] = .1
    with pytest.raises(ValueError):
        build_artifact(md, summary, delta, **kwargs)
    md, summary, delta, kwargs = fixture()
    summary["effects"]["case_delta"]["total_pairs"] = 2
    with pytest.raises(ValueError):
        build_artifact(md, summary, delta, **kwargs)


def test_native15_evidence_cannot_be_relabelled_as_pure_cadence():
    md, summary, delta, kwargs = fixture()
    summary["arms"][1]["policy"]["management_minutes"] = 15
    with pytest.raises(ValueError, match="same native5m"):
        build_artifact(md, summary, delta, **kwargs)


@pytest.mark.parametrize("replacement", ["", "<!-- V8_DISTRIBUTION -->", "<!-- V9_DISTRIBUTION -->\n<!-- V9_DISTRIBUTION -->"])
def test_directive_required(replacement):
    md, summary, delta, kwargs = fixture()
    with pytest.raises(ValueError):
        build_artifact(md.replace("<!-- V9_DISTRIBUTION -->", replacement), summary, delta, **kwargs)
