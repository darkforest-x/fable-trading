"""V7 native monthly report checks on synthetic saved outcomes, never prices."""
import copy
import json
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_source_report as report


def ledgers():
    pairs = pd.DataFrame({
        "event_id": ["jan_a", "jan_b", "jan_unmatched", "feb_unknown", "dec_a"],
        "mother_decision_time": ["2023-01-02T00:00:00Z", "2023-01-20T23:00:00Z",
                                 "2023-01-30T00:00:00Z", "2023-02-03T00:00:00Z",
                                 "2024-12-31T23:00:00Z"],
        "event_net_return": [.001, -.003, .9, np.nan, -.004],
        "assigned_controls": [3, 3, 0, 3, 3],
        "control_mean_return": [.002, -.004, np.nan, .005, -.006],
        "excess": [-.001, .001, np.nan, np.nan, .002],
    })
    cases = pairs[["event_id", "mother_decision_time", "event_net_return"]].rename(
        columns={"event_net_return": "episode_net_return"})
    return pairs, cases


def artifact():
    return {
        "surface": "report",
        "manifest": {
            "title": "Original report", "generatedAt": "2026-09-06T00:00:00Z",
            "blocks": [{"id": "old_markdown", "type": "markdown", "body": "# Original report"},
                       {"id": "old_chart_block_a", "type": "chart", "chartId": "old_a", "layout": "full"},
                       {"id": "old_chart_block_b", "type": "chart", "chartId": "old_b", "layout": "full"},
                       {"id": "v7", "type": "markdown", "body": "## Monthly\n\n" + report.MARKER},
                       {"id": "risk", "type": "markdown", "body": "Original caveat."}],
            "charts": [{"id": "old_a", "dataset": "old_a", "title": "Old A"},
                       {"id": "old_b", "dataset": "old_b", "title": "Old B"}],
            "sources": [{"id": "old", "path": "results/old.csv"}],
        },
        "snapshot": {"datasets": {"old_a": [{"x": 1, "y": -.25}], "old_b": [{"x": "b", "y": 2}]},
                     "status": "fixture"},
        "sources": [{"id": "old", "path": "results/old.csv"}],
    }


def monthly(pairs=None, cases=None):
    if pairs is None:
        pairs, cases = ledgers()
    return report.monthly_rows(pairs, cases)


def test_complete_24_month_two_series_calendar_and_same_cohort_means():
    pairs, cases = ledgers()
    originals = copy.deepcopy((pairs, cases))
    rows = monthly(pairs, cases)
    assert len(rows) == 48
    assert [row["month"] for row in rows[::2]] == pd.date_range("2023-01-01", periods=24, freq="MS").strftime("%Y-%m").tolist()
    assert [row["series"] for row in rows] == [report.CASE_SERIES, report.CONTROL_SERIES] * 24
    assert set(rows[0]) == {"month", "series", "netbp", "matched_requests", "all_requests", "coverage", "excessbp", "line_style"}
    assert rows[0]["netbp"] == pytest.approx(-10)
    assert rows[1]["netbp"] == pytest.approx(-10)
    assert rows[0]["matched_requests"] == rows[1]["matched_requests"] == 2
    assert rows[0]["all_requests"] == 3  # Unmatched +90% is not in either mean.
    assert rows[0]["coverage"] == pytest.approx(2 / 3)
    assert rows[0]["excessbp"] == pytest.approx(0)
    assert rows[-2]["netbp"] == pytest.approx(-40)
    assert rows[-1]["netbp"] == pytest.approx(-60)
    assert rows[-1]["excessbp"] == pytest.approx(20)
    assert sum(row["matched_requests"] for row in rows[::2]) == 3
    assert sum(row["all_requests"] for row in rows[::2]) == 5
    pd.testing.assert_frame_equal(pairs, originals[0])
    pd.testing.assert_frame_equal(cases, originals[1])


def test_unknown_and_empty_months_stay_null_never_zero_or_interpolated():
    rows = monthly()
    # February has one request but an unknown case: no pair, not a flat return.
    for row in rows[2:4]:
        assert row["matched_requests"] == 0
        assert row["all_requests"] == 1
        assert row["coverage"] == 0
        assert row["netbp"] is row["excessbp"] is None
    # March is genuinely empty; neither its return nor 0/0 coverage is zero.
    for row in rows[4:6]:
        assert row["all_requests"] == row["matched_requests"] == 0
        assert row["coverage"] is row["netbp"] is row["excessbp"] is None
    json.dumps(rows, allow_nan=False)


def test_empty_inputs_still_supply_full_calendar():
    pairs, cases = ledgers()
    rows = monthly(pairs.iloc[:0], cases.iloc[:0])
    assert len(rows) == 48
    assert all(row["netbp"] is None and row["coverage"] is None for row in rows)
    assert all(row["all_requests"] == 0 for row in rows)


def test_utc_boundary_assignment_and_input_order_do_not_change_months():
    pairs, cases = ledgers()
    pairs.loc[0, "mother_decision_time"] = "2023-02-01T07:00:00+08:00"
    cases.loc[0, "mother_decision_time"] = "2023-01-31T23:00:00Z"
    rows = monthly(pairs.sample(frac=1, random_state=7), cases.iloc[::-1])
    assert rows[0]["matched_requests"] == 2
    assert rows[0]["all_requests"] == 3
    assert rows[2]["all_requests"] == 1


def test_saved_sql_is_the_query_that_reproduces_the_48_rows():
    pairs, cases = ledgers()
    expected = pd.DataFrame(monthly(pairs, cases))
    with sqlite3.connect(":memory:") as database:
        pairs.to_sql("matched_request_outcomes", database, index=False)
        cases.to_sql("case_request_outcomes", database, index=False)
        actual = pd.read_sql_query(report.SQL, database)
    pd.testing.assert_frame_equal(actual, expected)


@pytest.mark.parametrize("ledger_name", ["pairs", "cases"])
@pytest.mark.parametrize("bad_id", [None, "", "  ", "jan_b"])
def test_duplicate_or_null_case_identity_fails_closed(ledger_name, bad_id):
    pairs, cases = ledgers()
    target = pairs if ledger_name == "pairs" else cases
    target.loc[0, "event_id"] = bad_id
    with pytest.raises(ValueError, match="event_id|nonnull"):
        monthly(pairs, cases)


@pytest.mark.parametrize("column,value", [
    ("mother_decision_time", None), ("mother_decision_time", 1672531200),
    ("mother_decision_time", "2025-01-01T00:00:00Z"),
    ("mother_decision_time", "2022-12-31T23:59:59Z"),
    ("assigned_controls", 2), ("assigned_controls", np.nan),
    ("event_net_return", np.inf), ("control_mean_return", -np.inf),
    ("excess", .3),
])
def test_invalid_times_counts_and_outcomes_are_rejected(column, value):
    pairs, cases = ledgers()
    pairs.loc[0, column] = value
    with pytest.raises(ValueError):
        monthly(pairs, cases)


@pytest.mark.parametrize("change", ["missing_pair", "missing_case", "different_time", "different_return", "invented_unmatched_control"])
def test_source_ledgers_must_reconcile_before_any_aggregation(change):
    pairs, cases = ledgers()
    if change == "missing_pair":
        pairs = pairs.iloc[1:]
    elif change == "missing_case":
        cases = cases.iloc[1:]
    elif change == "different_time":
        cases.loc[0, "mother_decision_time"] = "2023-01-10T00:00:00Z"
    elif change == "different_return":
        cases.loc[0, "episode_net_return"] = .2
    else:
        pairs.loc[2, ["control_mean_return", "excess"]] = [0., .9]
    with pytest.raises(ValueError):
        monthly(pairs, cases)


def test_artifact_preserves_both_old_charts_blocks_and_data_byte_for_byte():
    previous = artifact()
    original = copy.deepcopy(previous)
    revised = report.add_monthly(previous, *ledgers(), "results/pairs.csv", "results/cases.csv.gz")
    assert previous == original
    for key in ("blocks", "charts"):
        for index in range(3 if key == "blocks" else 2):
            assert json.dumps(revised["manifest"][key][index], ensure_ascii=False).encode() == json.dumps(original["manifest"][key][index], ensure_ascii=False).encode()
    for key in ("old_a", "old_b"):
        assert json.dumps(revised["snapshot"]["datasets"][key]).encode() == json.dumps(original["snapshot"]["datasets"][key]).encode()
    assert revised["manifest"]["blocks"][-1] == original["manifest"]["blocks"][-1]
    assert revised["manifest"]["blocks"][4] == {"id": report.BLOCK_ID, "type": "chart", "chartId": report.CHART_ID, "layout": "full"}
    assert report.MARKER not in json.dumps(revised)
    assert len(revised["manifest"]["charts"]) == 3
    assert len(revised["snapshot"]["datasets"][report.CHART_ID]) == 48
    json.dumps(revised, allow_nan=False)


def test_native_chart_has_comparator_legend_supported_strokes_and_complete_sources():
    revised = report.add_monthly(artifact(), *ledgers(), "results/pairs.csv", "results/cases.csv.gz")
    chart = revised["manifest"]["charts"][-1]
    assert chart["type"] == "line" and chart["layout"] == "full"
    assert chart["encodings"]["color"]["field"] == "series"
    assert chart["encodings"]["lineStyle"]["field"] == "line_style"
    assert chart["legend"]["position"] == "bottom"
    assert "3/5" in chart["subtitle"] and "60.0%" in chart["subtitle"]
    assert "非账户累计收益" in chart["subtitle"]
    assert chart["referenceLines"][0]["value"] == 0
    assert [row["line_style"] for row in revised["snapshot"]["datasets"][report.CHART_ID][:2]] == ["solid", "dashed"]
    sources = revised["manifest"]["sources"][-2:]
    assert sources[0]["query"]["sql"] == report.SQL
    assert sources[0]["query"]["tables_used"] == ["main.matched_request_outcomes", "main.case_request_outcomes"]
    assert [source["path"] for source in sources] == ["results/pairs.csv", "results/cases.csv.gz"]
    assert "Python" in sources[0]["query"]["description"]
    assert revised["sources"] == revised["manifest"]["sources"]


def test_shared_source_list_is_not_double_appended():
    previous = artifact()
    previous["sources"] = previous["manifest"]["sources"]
    revised = report.add_monthly(previous, *ledgers(), "results/pairs.csv", "results/cases.csv.gz")
    assert len(revised["sources"]) == 3
    assert revised["sources"] is revised["manifest"]["sources"]


@pytest.mark.parametrize("change", ["none", "two_blocks", "twice_in_block", "chart", "chart_block", "dataset", "source", "top_source"])
def test_missing_or_duplicate_marker_chart_dataset_source_cannot_be_inserted(change):
    previous = artifact()
    if change == "none":
        previous["manifest"]["blocks"][3]["body"] = "No marker"
    elif change == "two_blocks":
        previous["manifest"]["blocks"][0]["body"] += report.MARKER
    elif change == "twice_in_block":
        previous["manifest"]["blocks"][3]["body"] += report.MARKER
    elif change == "chart":
        previous["manifest"]["charts"].append({"id": report.CHART_ID})
    elif change == "chart_block":
        previous["manifest"]["blocks"].append({"chartId": report.CHART_ID})
    elif change == "dataset":
        previous["snapshot"]["datasets"][report.CHART_ID] = []
    elif change == "source":
        previous["manifest"]["sources"].append({"id": report.SOURCE_ID})
    else:
        previous["sources"].append({"id": report.CASES_SOURCE_ID})
    with pytest.raises(ValueError):
        report.add_monthly(previous, *ledgers(), "results/pairs.csv", "results/cases.csv.gz")


@pytest.mark.parametrize("unsafe", ["/Users/private/pairs.csv", "../pairs.csv", "a/../pairs.csv", "~/pairs.csv", "C:\\pairs.csv", "results//pairs.csv"])
@pytest.mark.parametrize("which", ["pairs", "cases"])
def test_both_source_identities_reject_machine_paths_and_traversal(unsafe, which):
    paths = ["results/pairs.csv", "results/cases.csv.gz"]
    paths[0 if which == "pairs" else 1] = unsafe
    with pytest.raises(ValueError):
        report.add_monthly(artifact(), *ledgers(), *paths)


def test_cli_synthetic_roundtrip_and_cannot_overwrite_sources(tmp_path, monkeypatch):
    pairs, cases = ledgers()
    (tmp_path / "input.json").write_text(json.dumps(artifact()))
    pairs.to_csv(tmp_path / "pairs.csv", index=False)
    cases.to_csv(tmp_path / "cases.csv.gz", index=False)
    monkeypatch.setattr(report, "ROOT", tmp_path)
    args = ["helper", "--input", "input.json", "--pairs", "pairs.csv", "--cases", "cases.csv.gz", "--output", "output.json"]
    monkeypatch.setattr(sys, "argv", args)
    report.main()
    result = json.loads((tmp_path / "output.json").read_text())
    assert len(result["snapshot"]["datasets"][report.CHART_ID]) == 48
    args[-1] = "pairs.csv"
    with pytest.raises(ValueError, match="overwrite"):
        report.main()
