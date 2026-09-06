"""Native report distribution checks on synthetic paired mothers only."""
import copy

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_realign_report import distribution_rows, add_distribution, MARKER


def test_distribution_preserves_unknown_zero_negative_and_positive_mothers():
    values = [-.01, -.005, -.002, -.0005, -1e-8, 0., 1e-8, .0005, .002, .005, np.nan]
    pairs = pd.DataFrame({"event_id": range(len(values)), "difference": values})
    rows = distribution_rows(pairs)
    assert sum(row["mothers"] for row in rows) == 11
    assert rows[3]["mothers"] == 2
    assert rows[4]["mothers"] == 1
    assert rows[9]["mothers"] == 1
    assert rows[9]["mean_change_bp"] is None
    assert sum(row["summed_event_change_bp"] or 0 for row in rows) == pytest.approx(np.nansum(values)*1e4)


def test_full_prior_report_is_preserved_and_chart_inserted_at_marker():
    artifact = {"manifest": {"generatedAt": "2024-01-01T00:00:00Z", "charts": [{"id": "old"}], "sources": [],
                              "blocks": [{"id": "first", "body": "unchanged"}, {"id": "v6", "body": "explanation\n"+MARKER}, {"id": "last", "body": "old caveat"}]},
                "snapshot": {"datasets": {"old": [{"x": 1}]}}, "sources": []}
    original = copy.deepcopy(artifact)
    revised = add_distribution(artifact, pd.DataFrame({"event_id": ["a"], "difference": [0]}), "results/pairs.csv")
    assert artifact == original
    assert revised["manifest"]["blocks"][0] == original["manifest"]["blocks"][0]
    assert revised["manifest"]["blocks"][-1] == original["manifest"]["blocks"][-1]
    assert revised["manifest"]["charts"][0] == {"id": "old"}
    assert revised["snapshot"]["datasets"]["old"] == [{"x": 1}]
    assert revised["manifest"]["blocks"][2]["chartId"] == "v6_paired_distribution"
    with pytest.raises(ValueError):
        add_distribution(revised, pd.DataFrame({"event_id": ["a"], "difference": [0]}), "results/pairs.csv")


@pytest.mark.parametrize("values,ids", [([np.inf], ["a"]), ([0, 1], ["a", "a"])])
def test_distribution_rejects_bad_evidence(values, ids):
    with pytest.raises(ValueError):
        distribution_rows(pd.DataFrame({"event_id": ids, "difference": values}))
