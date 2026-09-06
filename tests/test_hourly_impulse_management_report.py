"""Standalone V8 report tests using synthetic saved deltas, never prices."""
import copy
import json
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import hourly_impulse_management_report as report


def deltas(values=None):
    values = [-.02, -.01, -.005, -.002, -.0005, -1e-10, 0., 1e-13,
              1e-10, .0005, .002, .005, .01, .5, np.nan] if values is None else values
    return pd.DataFrame({"event_id": [f"event_{i:03d}" for i in range(len(values))],
                         "mother_decision_time": pd.date_range("2023-01-01", periods=len(values), freq="h", tz="UTC"),
                         "before": 0., "after": values, "difference": values})


def summary(delta):
    values = delta.difference
    return {"status": "synthetic_no_strategy_run", "effects": {"case_delta": {
        "total_pairs": len(values), "n": int(values.notna().sum()),
        "unknown_pairs": int(values.isna().sum()),
        "improved": int(values.gt(1e-12).sum()), "worsened": int(values.lt(-1e-12).sum()),
        "unchanged": int(values.abs().le(1e-12).sum()),
        "mean_bp": float(values.mean()*10000) if values.notna().any() else None}}}


def markdown():
    return """# V8 独立技术报告

## 技术摘要
<!-- SOURCE: v8_summary -->

保留完整答案，不能只留下图。

## 定义

先解释配对分母、20 bp 成本以及每笔而非账户收益。

## 比较
<!-- SOURCE: v8_summary -->

| 管理 | 均值 |
| --- | --- |
| 5m | -2 |
| 15m | -3 |

## 配对证据
<!-- SOURCE: v8_summary -->

正值为15m减5m。所有原始请求保留，未知不能填零。

<!-- V8_DISTRIBUTION -->

## 失败机制

### 子分组

- 保留第一条。
- 保留第二条。

## 不确定性

重复开发期不是独立验证。完整历史见[历史报告](analysis/html/history.html)。

## 下一步与后续问题

不得根据这张图重新挑参数。

## 复现

```python
## This is code, not a peer narrative heading.
print("<!-- V8_DISTRIBUTION -->")
```
"""


def build(md=None, delta=None, facts=None, **paths):
    delta = deltas() if delta is None else delta
    return report.build_artifact(markdown() if md is None else md,
                                  summary(delta) if facts is None else facts, delta,
                                  markdown_path=paths.get("markdown_path", "analysis/v8.md"),
                                  summary_path=paths.get("summary_path", "results/diagnostics/report_facts.json"),
                                  case_delta_path=paths.get("case_delta_path", "results/case_delta.csv"),
                                  generated_at="2026-09-06T00:00:00Z", fixture=True)


def test_all_signed_bins_zero_atom_unknown_and_untrimmed_extreme_values():
    source = deltas()
    before = source.copy(deep=True)
    rows = report.distribution_rows(source)
    assert len(rows) == 12
    assert [row["bin_id"] for row in rows] == list(range(12))
    assert sum(row["bin_count"] for row in rows) == len(source)
    assert all(row["count_sum"] == row["total_requests"] == len(source) for row in rows)
    assert all(row["finite_requests"] == 14 and row["unknown_requests"] == 1 for row in rows)
    assert rows[0]["minimum_change_bp"] == -200
    assert rows[10]["maximum_change_bp"] == 5000  # No percentile clipping.
    assert rows[5]["bin_count"] == 2  # Tolerance class keeps original values.
    assert rows[5]["summed_request_change_bp"] == pytest.approx(1e-9)
    assert rows[11]["bin_count"] == 1 and rows[11]["mean_change_bp"] is None
    assert sum(row["summed_request_change_bp"] or 0 for row in rows) == pytest.approx(source.difference.sum()*10000)
    pd.testing.assert_frame_equal(source, before)
    json.dumps(rows, allow_nan=False)


def test_exact_signed_numeric_boundary_membership():
    rows = report.distribution_rows(deltas([-.010001, -.01, -.005, -.002, -.0005,
                                           -1e-12, 1e-12, .0004999, .0005, .002, .005, .01]))
    assert [row["bin_count"] for row in rows] == [1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 0]
    assert rows[4]["bin_upper_bp"] == -1e-8 and rows[6]["bin_lower_bp"] == 1e-8


@pytest.mark.parametrize("values", [[], [np.nan]*3, [0.]*4, [.01]*4, [-.01]*4])
def test_empty_unknown_constant_and_one_sided_data_keep_full_count_contract(values):
    rows = report.distribution_rows(deltas(values))
    assert len(rows) == 12 and sum(row["bin_count"] for row in rows) == len(values)
    assert all(row["count_sum"] == len(values) for row in rows)
    json.dumps(rows, allow_nan=False)


def test_sql_in_source_really_reproduces_distribution_not_fake_backtest_provenance():
    source = deltas()
    result = build(delta=source)
    declared = next(item for item in result["sources"] if item["id"] == report.DELTA_SOURCE_ID)
    assert declared["query"]["sql"] == report.SQL
    with sqlite3.connect(":memory:") as database:
        source.to_sql("case_delta", database, index=False)
        actual = pd.read_sql_query(declared["query"]["sql"], database)
    pd.testing.assert_frame_equal(actual, pd.DataFrame(result["snapshot"]["datasets"][report.CHART_ID]))
    assert declared["query"]["tables_used"] == ["main.case_delta"]
    assert "does not rerun" in declared["query"]["description"]


@pytest.mark.parametrize("field", ["before", "after", "difference"])
@pytest.mark.parametrize("value", [np.inf, -np.inf])
def test_infinite_values_fail_instead_of_becoming_zero_unknown_or_trimmed(field, value):
    source = deltas()
    source.loc[0, field] = value
    with pytest.raises(ValueError, match="infinite"):
        report.distribution_rows(source)


@pytest.mark.parametrize("change", ["duplicate_id", "null_id", "blank_id", "duplicate_time", "null_time", "numeric_time", "future_time", "old_time", "wrong_delta", "missing_column"])
def test_corrupt_identity_time_and_pair_arithmetic_fail(change):
    source = deltas()
    if change == "duplicate_id":
        source.loc[0, "event_id"] = source.loc[1, "event_id"]
    elif change == "null_id":
        source.loc[0, "event_id"] = None
    elif change == "blank_id":
        source.loc[0, "event_id"] = "  "
    elif change == "duplicate_time":
        source.loc[0, "mother_decision_time"] = source.loc[1, "mother_decision_time"]
    elif change == "null_time":
        source.loc[0, "mother_decision_time"] = pd.NaT
    elif change == "numeric_time":
        source["mother_decision_time"] = range(len(source))
    elif change in {"future_time", "old_time"}:
        source.loc[0, "mother_decision_time"] = pd.Timestamp("2025-01-01" if change == "future_time" else "2022-12-31", tz="UTC")
    elif change == "wrong_delta":
        source.loc[0, "difference"] += .1
    else:
        source = source.drop(columns="before")
    with pytest.raises(ValueError):
        report.distribution_rows(source)


def test_timezone_and_row_order_do_not_change_bins():
    source = deltas()
    expected = report.distribution_rows(source)
    source["mother_decision_time"] = source.mother_decision_time.dt.tz_convert("Asia/Shanghai")
    assert report.distribution_rows(source.iloc[::-1]) == expected


@pytest.mark.parametrize("key", ["n", "total_pairs", "unknown_pairs", "improved", "worsened", "unchanged"])
def test_fact_source_and_full_delta_population_must_reconcile(key):
    source = deltas()
    facts = summary(source)
    facts["effects"]["case_delta"][key] += 1
    with pytest.raises(ValueError, match="summary case_delta"):
        build(delta=source, facts=facts)


@pytest.mark.parametrize("wrong", [None, True, "100", float("nan"), float("inf"), -999])
def test_fact_source_mean_must_match_even_when_all_counts_match(wrong):
    source = deltas()
    facts = summary(source)
    facts["effects"]["case_delta"]["mean_bp"] = wrong
    with pytest.raises(ValueError, match="summary case_delta.mean_bp"):
        build(delta=source, facts=facts)


@pytest.mark.parametrize("values", [[], [np.nan], [0.], [-.1, .1]])
def test_mean_reconciliation_empty_unknown_zero_and_signed(values):
    source = deltas(values)
    build(delta=source)
    if not source.difference.notna().any():
        facts = summary(source)
        facts["effects"]["case_delta"]["mean_bp"] = 0.
        with pytest.raises(ValueError, match="summary case_delta.mean_bp"):
            build(delta=source, facts=facts)


def test_complete_authored_markdown_is_preserved_with_one_chart_per_marker():
    original = markdown()
    result = build()
    blocks = result["manifest"]["blocks"]
    assert result["manifest"]["title"] == "V8 独立技术报告"
    assert blocks[0]["body"] == "# V8 独立技术报告" and "sourceId" not in blocks[0]
    assert len([block for block in blocks if block["type"] == "markdown"]) == 9
    assert len([block for block in blocks if block["type"] == "chart"]) == 1
    reconstructed = "\n\n".join(block["body"] for block in blocks if block["type"] == "markdown")
    for literal in ["保留完整答案", "| 15m | -3 |", "### 子分组", "- 保留第一条。", "- 保留第二条。",
                    "重复开发期不是独立验证", "[历史报告](analysis/html/history.html)",
                    "## This is code, not a peer narrative heading.", 'print("<!-- V8_DISTRIBUTION -->")']:
        assert literal in original and literal in reconstructed
    assert reconstructed.count("<!-- V8_DISTRIBUTION -->") == 1  # Literal code remains unchanged.
    chart_index = next(i for i, block in enumerate(blocks) if block["type"] == "chart")
    assert blocks[chart_index-1]["body"].startswith("## 配对证据")
    assert blocks[chart_index+1]["body"].startswith("## 失败机制")
    assert "sourceId" not in blocks[2]  # Unbound definitions are not auto-sourced.
    assert blocks[1]["sourceId"] == report.SUMMARY_SOURCE_ID


def test_long_fences_preserve_short_fence_and_peer_heading_literals():
    md = markdown().replace('```python\n## This', '````python\n```\n## This').replace('print("<!-- V8_DISTRIBUTION -->")\n```', 'print("<!-- V8_DISTRIBUTION -->")\n````')
    result = build(md=md)
    code = result["manifest"]["blocks"][-1]["body"]
    assert '````python\n```\n## This' in code
    assert len([block for block in result["manifest"]["blocks"] if block["type"] == "chart"]) == 1


@pytest.mark.parametrize("change", ["missing_marker", "duplicate_marker", "inline_marker", "marker_not_last", "unclosed_fence", "missing_title", "source_unknown", "duplicate_source"])
def test_malformed_report_markers_or_sources_fail_without_slimming_report(change):
    md = markdown()
    if change == "missing_marker":
        md = md.replace("\n<!-- V8_DISTRIBUTION -->\n", "\n")
    elif change == "duplicate_marker":
        md = md.replace("\n<!-- V8_DISTRIBUTION -->\n", "\n<!-- V8_DISTRIBUTION -->\n<!-- V8_DISTRIBUTION -->\n")
    elif change == "inline_marker":
        md = md.replace("\n<!-- V8_DISTRIBUTION -->\n", "\ninline <!-- V8_DISTRIBUTION -->\n")
    elif change == "marker_not_last":
        md = md.replace("\n<!-- V8_DISTRIBUTION -->\n", "\n<!-- V8_DISTRIBUTION -->\nThis cannot move after the chart.\n")
    elif change == "unclosed_fence":
        md = md.rstrip()[:-3]
    elif change == "missing_title":
        md = md.replace("# V8 独立技术报告", "not a title", 1)
    elif change == "source_unknown":
        md = md.replace("SOURCE: v8_summary", "SOURCE: old_v1_report")
    else:
        md = md.replace("<!-- SOURCE: v8_summary -->", "<!-- SOURCE: v8_summary -->\n<!-- SOURCE: v8_case_delta -->", 1)
    with pytest.raises(ValueError):
        build(md=md)


@pytest.mark.parametrize("key", ["markdown_path", "summary_path", "case_delta_path"])
@pytest.mark.parametrize("path", ["/Users/private/report.json", "../report.json", "a/../report.json", "~/.private", "C:\\secret", "data//facts.json"])
def test_all_metadata_identities_are_safe_and_exact(key, path):
    with pytest.raises(ValueError):
        build(**{key: path})


def test_no_inherited_v1_sources_and_single_blue_native_count_chart():
    result = build()
    chart = result["manifest"]["charts"][0]
    assert chart["type"] == "bar" and chart["layout"] == "full"
    assert chart["palette"] == {"kind": "sequential", "name": "blue"}
    assert "color" not in chart["encodings"] and "series" not in chart
    assert chart["referenceLines"][0]["value"] == 0
    assert "分箱计数，非密度" in chart["subtitle"]
    assert "未知 1" in chart["subtitle"]
    assert len(result["manifest"]["charts"]) == 1
    assert set(result["snapshot"]["datasets"]) == {report.CHART_ID}
    assert result["snapshot"]["status"] == "fixture"
    assert not any("v1" in source["path"] for source in result["sources"])
    fact = next(source for source in result["sources"] if source["id"] == report.SUMMARY_SOURCE_ID)
    assert fact["path"] == "results/diagnostics/report_facts.json"
    assert "query" not in fact  # No fabricated SQL for the JSON fact source.
    assert result["manifest"]["sources"] == result["sources"]
    json.dumps(result, allow_nan=False)


def test_cli_reads_only_named_saved_evidence_and_writes_canonical_artifact(tmp_path, monkeypatch):
    source = deltas()
    facts = summary(source)
    (tmp_path/"report.md").write_text(markdown())
    (tmp_path/"facts.json").write_text(json.dumps(facts))
    source.to_csv(tmp_path/"delta.csv", index=False)
    monkeypatch.setattr(report, "ROOT", tmp_path)
    args = ["report", "--markdown", "report.md", "--summary", "facts.json", "--case-delta", "delta.csv", "--output", "new/report/artifact.json"]
    monkeypatch.setattr(sys, "argv", args)
    report.main()
    result = json.loads((tmp_path/"new/report/artifact.json").read_text())
    assert result["surface"] == "report"
    assert result["snapshot"]["status"] == "ready"
    assert result["snapshot"]["datasets"][report.CHART_ID][0]["total_requests"] == len(source)
    args[-1] = "facts.json"
    with pytest.raises(ValueError, match="overwrite"):
        report.main()
    assert json.loads((tmp_path/"facts.json").read_text()) == facts
