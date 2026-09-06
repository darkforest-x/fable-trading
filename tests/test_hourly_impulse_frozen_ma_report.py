"""Synthetic-only V12 native report contracts; no prices or outcome runs."""
from copy import deepcopy
import json

import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_frozen_ma_report import CHART_ID, EXPERIMENT_ID, build_artifact


def fixture():
    delta = pd.DataFrame({"event_id": ["a", "b", "c", "d"],
        "mother_decision_time": pd.date_range("2023-02-01", periods=4, freq="h", tz="UTC"),
        "before": [.01, .01, .01, None], "after": [.011, .008, .01, None],
        "difference": [.001, -.002, 0., None]})
    policy = {"id": "5m_native40", "management_minutes": 5, "ma_kind": "SMA", "ma_length": 40,
              "exit_mode": "transition_colour", "confirmations": 1}
    summary = {"experiment_id": EXPERIMENT_ID, "effects": {"case_delta": {"total_pairs": 4, "n": 3,
        "unknown_pairs": 1, "improved": 1, "worsened": 1, "unchanged": 1, "mean_bp": -10/3}},
        "arms": {"baseline": {"policy": policy}, "candidate": {"policy": {**policy,
            "id": "5m_native40_frozen_ma", "frozen_ma_exit": True}}}}
    md = "# Frozen MA Exit\n\n## Summary\n<!-- SOURCE: v12_summary -->\nAll requests.\n\n## Geometry\n<!-- SOURCE: v12_geometry -->\nNot rematched.\n\n## Distribution\nAll tails retained.\n<!-- V12_DISTRIBUTION -->\n\n## Cases\n<!-- SOURCE: v12_mechanics -->\nExamples.\n\n## Caveat\nNot profit proof.\n"
    kwargs = dict(markdown_path="analysis/v12.md", summary_path="experiments/v12/summary.json",
        case_delta_path="experiments/v12/case_delta.csv", generated_at="2026-09-06T00:00:00Z", fixture=True)
    return md, summary, delta, kwargs


def test_complete_narrative_sources_query_and_population():
    md, summary, delta, kwargs = fixture()
    saved = deepcopy(summary)
    artifact = build_artifact(md, summary, delta, **kwargs)
    assert summary == saved
    blocks = artifact["manifest"]["blocks"]
    assert len(blocks) == 7
    assert [b.get("sourceId") for b in blocks] == [None, "v12_summary", "v12_geometry", None, None, "v12_mechanics", None]
    assert blocks[-1]["body"] == "## Caveat\nNot profit proof."
    assert blocks[2]["body"] == "## Geometry\nNot rematched."
    assert artifact["sources"] == artifact["manifest"]["sources"]
    sources = {r["id"]: r for r in artifact["sources"]}
    assert sources["v12_geometry"]["path"] == "experiments/v12/entry_geometry.csv"
    assert sources["v12_mechanics"]["path"] == "experiments/v12/paired_case_mechanics.csv.gz"
    assert "WITH bins" in sources["v12_case_delta"]["query"]["sql"]
    rows = artifact["snapshot"]["datasets"][CHART_ID]
    assert sum(row["bin_count"] for row in rows) == 4
    assert rows[-1]["bin_count"] == rows[5]["bin_count"] == 1
    assert "V8" not in json.dumps(artifact) and "V11" not in json.dumps(artifact)
    assert "native15m" not in json.dumps(artifact)
    json.dumps(artifact, allow_nan=False)


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_fenced_version_and_source_literals_preserved(fence):
    md, summary, delta, kwargs = fixture()
    literal = f"{fence}text\n<!-- SOURCE: v12_geometry -->\n<!-- V12_DISTRIBUTION -->\n<!-- V11_DISTRIBUTION -->\n<!-- V8_DISTRIBUTION -->\n{fence}"
    artifact = build_artifact(md + "\n## Literal\n" + literal, summary, delta, **kwargs)
    assert artifact["manifest"]["blocks"][-1]["body"] == "## Literal\n" + literal
    assert "sourceId" not in artifact["manifest"]["blocks"][-1]


@pytest.mark.parametrize("change", ["native15", "deadline", "cadence", "integer_flag", "false_flag", "old_id", "experiment", "count", "arithmetic", "population"])
def test_wrong_policy_or_evidence_fails_closed(change):
    md, summary, delta, kwargs = fixture()
    p = summary["arms"]["candidate"]["policy"]
    if change == "native15": p["management_minutes"] = 15
    elif change == "deadline": p["launch_deadline_minutes"] = 60
    elif change == "cadence": p["decision_minutes"] = 15
    elif change == "integer_flag": p["frozen_ma_exit"] = 1
    elif change == "false_flag": p["frozen_ma_exit"] = False
    elif change == "old_id": p["id"] = "5m_native40_launch60"
    elif change == "experiment": summary["experiment_id"] = "exp-btcusdtp-1h-launch-deadline-preholdout-20260906-v11"
    elif change == "count": summary["effects"]["case_delta"]["total_pairs"] = 3
    elif change == "arithmetic": delta.loc[0, "difference"] = .1
    elif change == "population": kwargs["fixture"] = False
    with pytest.raises(ValueError): build_artifact(md, summary, delta, **kwargs)


@pytest.mark.parametrize("replacement", ["", "<!-- V8_DISTRIBUTION -->", "<!-- V11_DISTRIBUTION -->", "<!-- V12_DISTRIBUTION -->\n<!-- V12_DISTRIBUTION -->"])
def test_exact_version_marker_required(replacement):
    md, summary, delta, kwargs = fixture()
    with pytest.raises(ValueError):
        build_artifact(md.replace("<!-- V12_DISTRIBUTION -->", replacement), summary, delta, **kwargs)
