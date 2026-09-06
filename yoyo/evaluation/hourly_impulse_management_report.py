"""Build a standalone native V8 technical report from saved outcome evidence.

This presentation helper does not read prices, select strategy parameters,
rewrite authored prose, render HTML or replace the historical V1-V7 report.
The SQL really runs against saved case_delta in SQLite, while source metadata
keeps the upstream Python evaluator distinct from this count transformation.

Native schema: Data Analytics 0.2.10 mcp/server.cjs artifactChart, artifactBlock
and sourceSchema; src/analytics-app-core.md portable artifact contract. Binned
bar counts use explicit numeric unequal/open-ended intervals, not a probability
density. Single quantitative count series hides the native legend automatically
(ChartRenderer.tsx chartLegendItems); neutral zero-count baseline is supported.
The separate zero-change atom is labelled because categorical X reference lines
are not reliably numeric in the native renderer. No bespoke runtime is added.

SQLite/pandas sources:
https://www.sqlite.org/lang_with.html
https://www.sqlite.org/windowfunctions.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_sql_query.html
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from numbers import Number
import re
import sqlite3

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_report import ROOT, safe_identity


MARKER = "<!-- V8_DISTRIBUTION -->"
CHART_ID = "v8_case_difference_distribution"
SUMMARY_SOURCE_ID = "v8_summary"
DELTA_SOURCE_ID = "v8_case_delta"
REQUIRED_COLUMNS = ["event_id", "mother_decision_time", "before", "after", "difference"]
ZERO_TOLERANCE = 1e-12
SOURCE_MARKER = re.compile(r"^\s*<!--\s*SOURCE:\s*([a-zA-Z0-9_-]+)\s*-->\s*$")
SQL = """WITH bins(bin_id, bin_label, bin_lower_bp, bin_upper_bp) AS (
  VALUES (0, '< -100', NULL, -100),
         (1, '[-100, -50)', -100, -50),
         (2, '[-50, -20)', -50, -20),
         (3, '[-20, -5)', -20, -5),
         (4, '[-5, -1e-8)', -5, -0.00000001),
         (5, '0 · |Δ|<=1e-8', -0.00000001, 0.00000001),
         (6, '(1e-8, 5)', 0.00000001, 5),
         (7, '[5, 20)', 5, 20),
         (8, '[20, 50)', 20, 50),
         (9, '[50, 100)', 50, 100),
         (10, '>= 100', 100, NULL),
         (11, '未知', NULL, NULL)
), classified AS (
  SELECT event_id, difference * 10000 AS change_bp,
         CASE WHEN difference IS NULL THEN 11
              WHEN ABS(difference) <= 0.000000000001 THEN 5
              WHEN difference * 10000 < -100 THEN 0
              WHEN difference * 10000 < -50 THEN 1
              WHEN difference * 10000 < -20 THEN 2
              WHEN difference * 10000 < -5 THEN 3
              WHEN difference < 0 THEN 4
              WHEN difference * 10000 < 5 THEN 6
              WHEN difference * 10000 < 20 THEN 7
              WHEN difference * 10000 < 50 THEN 8
              WHEN difference * 10000 < 100 THEN 9 ELSE 10 END AS bin_id
  FROM main.case_delta
), totals AS (
  SELECT COUNT(*) AS total_requests, COUNT(change_bp) AS finite_requests,
         COUNT(*) - COUNT(change_bp) AS unknown_requests FROM classified
)
SELECT b.bin_id, b.bin_label, b.bin_lower_bp, b.bin_upper_bp,
       COUNT(c.event_id) AS bin_count,
       t.total_requests, t.finite_requests, t.unknown_requests,
       SUM(COUNT(c.event_id)) OVER () AS count_sum,
       MIN(c.change_bp) AS minimum_change_bp,
       MAX(c.change_bp) AS maximum_change_bp,
       AVG(c.change_bp) AS mean_change_bp,
       SUM(c.change_bp) AS summed_request_change_bp
FROM bins AS b CROSS JOIN totals AS t
LEFT JOIN classified AS c ON c.bin_id = b.bin_id
GROUP BY b.bin_id, b.bin_label, b.bin_lower_bp, b.bin_upper_bp,
         t.total_requests, t.finite_requests, t.unknown_requests
ORDER BY b.bin_id"""


def _fence(line: str, active):
    match = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
    if not match:
        return active
    mark, suffix = match.groups()
    if active is None:
        return mark[0], len(mark)
    if mark[0] == active[0] and len(mark) >= active[1] and not suffix.strip():
        return None
    return active


def markdown_sections(markdown: str) -> tuple[str, list[dict]]:
    """Retain every peer section and literal fenced content; consume directives.

    One standalone V8 marker is mandatory outside code fences, at the end of
    its narrative section. SOURCE directives are author assertions, never
    inferred from numbers or inherited from the historical report. Unbound
    mixed-source/prose-only blocks and the title get no invented provenance.
    """
    if re.search(r"(?:/Users/|/home/|/tmp/|file://)", markdown):
        raise ValueError("portable report prose contains a machine-local path")
    lines = markdown.strip().splitlines()
    if not lines or not re.match(r"^# [^#]", lines[0]):
        raise ValueError("report must start with one # title")
    title = lines[0][2:].strip()
    blocks = [{"id": "report_title", "type": "markdown", "body": lines[0], "layout": "full"}]
    sections, section, fence = [], [], None
    for line in lines[1:]:
        outside = fence is None
        fence = _fence(line, fence)
        if outside and line.startswith("## "):
            if any(value.strip() for value in section):
                sections.append(section)
            section = [line]
        else:
            section.append(line)
    if fence is not None:
        raise ValueError("report code fence is not closed")
    if any(value.strip() for value in section):
        sections.append(section)
    marker_count = 0
    for index, section in enumerate(sections, 1):
        if not next((line for line in section if line.strip()), "").startswith("## "):
            raise ValueError("all narrative after the title needs peer ## sections")
        body, source_id, chart_here, fence = [], None, False, None
        for line in section:
            outside = fence is None
            fence = _fence(line, fence)
            directive = SOURCE_MARKER.match(line) if outside else None
            if directive:
                if source_id is not None:
                    raise ValueError("only one SOURCE directive per peer section")
                source_id = directive.group(1)
            elif outside and line.strip() == MARKER:
                marker_count += 1
                chart_here = True
            else:
                if outside and MARKER in line:
                    raise ValueError("V8 marker must be a standalone line")
                if chart_here and line.strip():
                    raise ValueError("V8 marker must be last in its narrative section")
                body.append(line)
        block = {"id": f"section_{index:02d}", "type": "markdown",
                 "body": "\n".join(body).strip(), "layout": "full"}
        if source_id is not None:
            block["sourceId"] = source_id
        blocks.append(block)
        if chart_here:
            blocks.append({"id": "v8_distribution_chart", "type": "chart",
                           "chartId": CHART_ID, "layout": "full"})
    if marker_count != 1:
        raise ValueError("exactly one V8_DISTRIBUTION marker is required")
    return title, blocks


def _reviewed_delta(delta: pd.DataFrame) -> pd.DataFrame:
    if not set(REQUIRED_COLUMNS).issubset(delta) or delta.columns.duplicated().any():
        raise ValueError("case_delta has missing or duplicate columns")
    result = delta[REQUIRED_COLUMNS].copy()
    ids = result["event_id"]
    if (ids.isna().any() or ids.duplicated().any()
            or ids.map(lambda value: not isinstance(value, str) or not value.strip()).any()):
        raise ValueError("each original case needs a unique nonempty event_id")
    timestamps = result["mother_decision_time"]
    if len(timestamps) and (timestamps.isna().any() or timestamps.map(lambda value: isinstance(value, Number)).any()):
        raise ValueError("case times must be explicit nonnull datetimes, not numeric epochs")
    times = pd.to_datetime(timestamps, utc=True, format="mixed", errors="raise")
    if times.isna().any() or times.duplicated().any() or not (times.ge("2023-01-01") & times.lt("2025-01-01")).all():
        raise ValueError("case times must be unique and in development 2023-2024 UTC")
    result["mother_decision_time"] = times.map(lambda value: value.isoformat())
    for name in ("before", "after", "difference"):
        result[name] = pd.to_numeric(result[name], errors="raise").astype(float)
        if np.isinf(result[name]).any():
            raise ValueError("infinite outcomes are invalid, not missing or tail-clipped")
    with np.errstate(over="ignore", invalid="ignore"):
        expected = result["after"] - result["before"]
        bp_values = result["difference"] * 10000
    if np.isinf(expected).any() or np.isinf(bp_values).any():
        raise ValueError("nonfinite arithmetic cannot be plotted as finite evidence")
    if not np.allclose(result["difference"], expected, rtol=0, atol=1e-12, equal_nan=True):
        raise ValueError("difference must equal 15m after minus 5m before on each case")
    return result


def distribution_rows(case_delta: pd.DataFrame) -> list[dict]:
    """Execute the declared count SQL, preserving all finite/unknown case rows."""
    source = _reviewed_delta(case_delta)
    with sqlite3.connect(":memory:") as database:
        source.to_sql("case_delta", database, index=False)
        aggregated = pd.read_sql_query(SQL, database)
    rows = []
    for record in aggregated.to_dict("records"):
        row = {key: None if pd.isna(value) else value for key, value in record.items()}
        for key in ("bin_id", "bin_count", "total_requests", "finite_requests", "unknown_requests", "count_sum"):
            row[key] = int(row[key])
        rows.append(row)
    if sum(row["bin_count"] for row in rows) != len(source) or any(row["count_sum"] != len(source) for row in rows):
        raise AssertionError("all original requests must occur in exactly one distribution bin")
    return rows


def _reconcile_summary(summary: dict, source: pd.DataFrame) -> None:
    """Check the supplied fact source describes this exact full paired cohort."""
    try:
        effect = summary["effects"]["case_delta"]
    except (KeyError, TypeError):
        raise ValueError("summary needs effects.case_delta from the reviewed V8 run")
    delta = source["difference"]
    expected = {"total_pairs": len(source), "n": int(delta.notna().sum()),
                "unknown_pairs": int(delta.isna().sum()),
                "improved": int(delta.gt(ZERO_TOLERANCE).sum()),
                "worsened": int(delta.lt(-ZERO_TOLERANCE).sum()),
                "unchanged": int(delta.abs().le(ZERO_TOLERANCE).sum())}
    for key, value in expected.items():
        if effect.get(key) != value or isinstance(effect.get(key), bool):
            raise ValueError(f"summary case_delta.{key} does not match the full case ledger")
    expected_mean = float(delta.mean() * 10000) if expected["n"] else None
    actual_mean = effect.get("mean_bp")
    if expected_mean is None:
        valid_mean = actual_mean is None
    else:
        valid_mean = (isinstance(actual_mean, Number) and not isinstance(actual_mean, bool)
                      and np.isfinite(actual_mean)
                      and np.isclose(actual_mean, expected_mean, rtol=1e-10, atol=1e-8))
    if not valid_mean:
        raise ValueError("summary case_delta.mean_bp does not match the full case ledger")


def build_artifact(markdown: str, summary: dict, case_delta: pd.DataFrame, *,
                   markdown_path: str, summary_path: str, case_delta_path: str,
                   generated_at: str, fixture: bool = False) -> dict:
    """Build one complete independent report without file or price reads."""
    title, blocks = markdown_sections(markdown)
    paths = {"report": safe_identity(markdown_path), SUMMARY_SOURCE_ID: safe_identity(summary_path),
             DELTA_SOURCE_ID: safe_identity(case_delta_path),
             "research_code": "yoyo/evaluation/hourly_impulse_management_research.py",
             "presentation_code": "yoyo/evaluation/hourly_impulse_management_report.py"}
    if len(set(paths.values())) != len(paths):
        raise ValueError("distinct evidence sources require distinct identities")
    unknown = {block["sourceId"] for block in blocks if "sourceId" in block} - set(paths)
    if unknown:
        raise ValueError(f"unknown V8 SOURCE identities: {sorted(unknown)}")
    reviewed = _reviewed_delta(case_delta)
    _reconcile_summary(summary, reviewed)
    rows = distribution_rows(reviewed)
    timestamp = pd.to_datetime(generated_at, utc=True, errors="raise")
    if pd.isna(timestamp):
        raise ValueError("generated_at must be a finite timestamp")
    timestamp = timestamp.isoformat()
    sources = [{"id": source_id, "label": label, "path": paths[source_id]} for source_id, label in [
        ("report", "V8 · 独立技术报告原文"),
        (SUMMARY_SOURCE_ID, "OKX archive · V8 保存的结果与诊断事实"),
        (DELTA_SOURCE_ID, "OKX archive · V8 全部案例配对变化"),
        ("research_code", "V8 · 上游 Python 评估器"),
        ("presentation_code", "V8 · 分布聚合与原生报告构建器"),
    ]]
    next(source for source in sources if source["id"] == DELTA_SOURCE_ID)["query"] = {
        "engine": "SQLite", "language": "sql", "sql": SQL, "executed_at": timestamp,
        "tables_used": ["main.case_delta"],
        "description": f"Actual count query over {paths[DELTA_SOURCE_ID]}, loaded as main.case_delta after identity/time/arithmetic validation. Source columns: event_id, mother_decision_time, before, after, difference. Upstream Python evaluator {paths['research_code']} produced paired outcomes; this presentation query does not rerun prices, a strategy or inference. The reviewed JSON fact source is {paths[SUMMARY_SOURCE_ID]}.",
        "filters": ["BTC-USDT-SWAP, reused development 2023-01-01 <= mother_decision_time UTC < 2025-01-01", "Every original case request, not matched-only cases or winner-only paths; no trimming or sampling", "One unique event_id and exact decision time per source request; +/-infinity rejected, NULL differences retained in the unknown bin"],
        "metric_definitions": ["difference=after-before, where after is native15m and before is native5m paired episode net return; chart bp=difference*10000", "bin_count counts requests, not probability density; numeric intervals have unequal widths and open-ended tails", "Zero atom follows frozen comparison tolerance abs(difference)<=1e-12 in fractional-return units (1e-8 bp); original nonzero values are preserved in sums and means", "total_requests=all source rows; finite_requests=nonnull differences; unknown_requests=null differences; count_sum=sum(bin_count) must equal total_requests", "Each bin retains observed minimum, maximum, mean and summed request change in bp. Sum of independent request returns is not compounded account P/L. Both arms retain the same upstream 20bp cost assumption"],
    }
    count, unknown_count = len(reviewed), int(reviewed["difference"].isna().sum())
    chart = {"id": CHART_ID, "title": "15m 与 5m 管理的逐请求收益变化分布",
             "subtitle": f"2023–2024 开发期 · 全部 {count} 请求（未知 {unknown_count}）· 15m−5m 净收益变化，bp · 分箱计数，非密度；零类为 |Δ|≤1e−8 bp",
             "showDescription": True, "type": "bar", "intent": "distribution", "layout": "full",
             "dataset": CHART_ID, "sourceId": DELTA_SOURCE_ID,
             "question": "管理规格变化的改善与恶化分布在多少原始请求上？",
             "rationale": "Explicit signed numeric bins preserve both tails, the zero atom and unknowns for every original request. Unequal widths mean counts, not density. One blue measure needs no legend; count labels and signed interval labels identify meaning.",
             "palette": {"kind": "sequential", "name": "blue"}, "labels": {"values": "all"},
             "settings": {"sort": "none", "categoryLabelPolicy": "wrap"},
             "referenceLines": [{"axis": "y", "value": 0, "color": "neutral", "lineStyle": "solid", "label": "0 请求"}],
             "maxRows": 12, "valueFormat": "number",
             "encodings": {"x": {"field": "bin_label", "type": "ordinal", "label": "净收益变化区间（bp；15m−5m）"},
                           "y": {"field": "bin_count", "type": "quantitative", "label": "原始请求数", "format": "number"},
                           "tooltip": [{"field": "bin_count", "type": "quantitative", "label": "区间请求数"},
                                       {"field": "total_requests", "type": "quantitative", "label": "全部请求"},
                                       {"field": "unknown_requests", "type": "quantitative", "label": "未知请求"},
                                       {"field": "count_sum", "type": "quantitative", "label": "全部分箱计数和"},
                                       {"field": "mean_change_bp", "type": "quantitative", "label": "区间变化均值", "unit": "bp"},
                                       {"field": "minimum_change_bp", "type": "quantitative", "label": "区间实际最小值", "unit": "bp"},
                                       {"field": "maximum_change_bp", "type": "quantitative", "label": "区间实际最大值", "unit": "bp"}]}}
    return {"surface": "report",
            "manifest": {"version": 1, "surface": "report", "title": title, "generatedAt": timestamp,
                         "filters": [], "cards": [], "charts": [chart], "tables": [], "sources": sources, "blocks": blocks},
            "snapshot": {"version": 1, "generatedAt": timestamp, "status": "fixture" if fixture else "ready",
                         "datasets": {CHART_ID: rows}, "accessIssues": []}, "sources": sources}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("markdown", "summary", "case-delta", "output"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    paths = {name: safe_identity(getattr(args, name)) for name in ("markdown", "summary", "case_delta", "output")}
    if paths["output"] in {paths["markdown"], paths["summary"], paths["case_delta"]}:
        raise ValueError("output must not overwrite authored report or evidence")
    artifact = build_artifact((ROOT/paths["markdown"]).read_text(),
                              json.loads((ROOT/paths["summary"]).read_text()),
                              pd.read_csv(ROOT/paths["case_delta"]),
                              markdown_path=paths["markdown"], summary_path=paths["summary"],
                              case_delta_path=paths["case_delta"], generated_at=datetime.now(timezone.utc).isoformat())
    output = ROOT/paths["output"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"surface": "report", "blocks": len(artifact["manifest"]["blocks"]),
                      "charts": 1, "requests": artifact["snapshot"]["datasets"][CHART_ID][0]["total_requests"]}))


if __name__ == "__main__":
    main()
