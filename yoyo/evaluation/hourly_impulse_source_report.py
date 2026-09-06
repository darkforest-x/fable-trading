"""Add one native V7 monthly matched-cohort chart to the existing report.

Presentation only: read saved request outcomes, never raw prices or strategies.
Both plotted means use the identical requests with three assigned controls and
finite paired outcomes. All requests supply coverage denominators only. The
fixed 2023-2024 UTC calendar retains empty months as NULL, never zero/interpolated.

Native contract: Data Analytics 0.2.10 mcp/server.cjs artifactChart/encodings,
src/analytics-app/charting/chart-contract.ts and chart-app-helpers.tsx. The native
long-form adapter assigns its first two series blue/orange and supports a
lineStyle field; it does not retain custom per-series colors. No extra renderer.
SQL below actually aggregates the saved Python evaluator ledgers in SQLite;
it does not claim to be the SQL that generated the original backtest outcomes.

Library sources (pandas 2.3.3, repository constraints-ci.txt):
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.to_sql.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.read_sql_query.html
https://www.sqlite.org/lang_with.html
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_report import ROOT, safe_identity


MARKER = "<!-- V7_MONTHLY -->"
CHART_ID = "v7_monthly_matched_returns"
SOURCE_ID = "v7_monthly"
CASES_SOURCE_ID = "v7_monthly_cases"
BLOCK_ID = "v7_monthly_chart"
CASE_SERIES = "配对案例"
CONTROL_SERIES = "三控制均值"
PAIR_COLUMNS = ["event_id", "mother_decision_time", "event_net_return",
                "assigned_controls", "control_mean_return", "excess"]
CASE_COLUMNS = ["event_id", "mother_decision_time", "episode_net_return"]
SQL = """WITH RECURSIVE calendar(month_start) AS (
  SELECT '2023-01-01'
  UNION ALL
  SELECT DATE(month_start, '+1 month') FROM calendar
  WHERE month_start < '2024-12-01'
), eligible AS (
  SELECT p.event_id, SUBSTR(p.mother_decision_time, 1, 7) AS month,
         p.event_net_return, p.control_mean_return, p.excess
  FROM main.matched_request_outcomes AS p
  JOIN main.case_request_outcomes AS c ON c.event_id = p.event_id
  WHERE p.assigned_controls = 3
    AND p.event_net_return IS NOT NULL
    AND p.control_mean_return IS NOT NULL AND p.excess IS NOT NULL
    AND p.mother_decision_time >= '2023-01-01'
    AND p.mother_decision_time < '2025-01-01'
), paired_months AS (
  SELECT month, COUNT(*) AS matched_requests,
         AVG(event_net_return) * 10000 AS case_netbp,
         AVG(control_mean_return) * 10000 AS control_netbp,
         AVG(excess) * 10000 AS excessbp
  FROM eligible GROUP BY month
), all_months AS (
  SELECT SUBSTR(mother_decision_time, 1, 7) AS month,
         COUNT(*) AS all_requests
  FROM main.case_request_outcomes
  WHERE mother_decision_time >= '2023-01-01'
    AND mother_decision_time < '2025-01-01'
  GROUP BY month
), monthly AS (
  SELECT SUBSTR(calendar.month_start, 1, 7) AS month,
         COALESCE(p.matched_requests, 0) AS matched_requests,
         COALESCE(a.all_requests, 0) AS all_requests,
         COALESCE(p.matched_requests, 0) * 1.0 /
           NULLIF(COALESCE(a.all_requests, 0), 0) AS coverage,
         p.case_netbp, p.control_netbp, p.excessbp
  FROM calendar
  LEFT JOIN paired_months AS p ON p.month = SUBSTR(calendar.month_start, 1, 7)
  LEFT JOIN all_months AS a ON a.month = SUBSTR(calendar.month_start, 1, 7)
)
SELECT month, '配对案例' AS series, case_netbp AS netbp,
       matched_requests, all_requests, coverage, excessbp, 'solid' AS line_style
FROM monthly
UNION ALL
SELECT month, '三控制均值' AS series, control_netbp AS netbp,
       matched_requests, all_requests, coverage, excessbp, 'dashed' AS line_style
FROM monthly
ORDER BY month, line_style DESC"""


def _ledger(frame: pd.DataFrame, columns: list[str], name: str) -> pd.DataFrame:
    """Validate complete saved identities and normalize timestamps to UTC text."""
    if not set(columns).issubset(frame.columns) or frame.columns.duplicated().any():
        raise ValueError(f"{name}: missing or duplicate columns")
    result = frame[columns].copy()
    identifiers = result["event_id"]
    if identifiers.isna().any() or identifiers.duplicated().any():
        raise ValueError(f"{name}: every case event_id must be nonnull and unique")
    if identifiers.map(lambda value: not isinstance(value, str) or not value.strip()).any():
        raise ValueError(f"{name}: event_id must be a nonempty string")
    raw_time = result["mother_decision_time"]
    if raw_time.isna().any() or raw_time.map(lambda value: isinstance(value, (int, float, np.number))).any():
        raise ValueError(f"{name}: missing or numeric decision time is not allowed")
    times = pd.to_datetime(raw_time, utc=True, format="mixed", errors="raise")
    if times.isna().any() or not (times.ge("2023-01-01") & times.lt("2025-01-01")).all():
        raise ValueError(f"{name}: decision times must fall in development 2023-2024 UTC")
    result["mother_decision_time"] = times.map(lambda value: value.isoformat())
    for column in columns:
        if column in {"event_id", "mother_decision_time"}:
            continue
        result[column] = pd.to_numeric(result[column], errors="raise").astype(float)
        if np.isinf(result[column]).any():
            raise ValueError(f"{name}: infinite outcomes/counts are not evidence")
    return result


def monthly_rows(pairs: pd.DataFrame, cases: pd.DataFrame) -> list[dict]:
    """Return exactly 48 SQL-produced month/series rows, preserving empty NULLs.

    Inputs are complete ledgers, including unmatched or unknown requests. Each
    must contain one row per identical original event_id, at identical UTC
    mother_decision_time. Paired event returns must equal case episode returns.
    Unknown outcomes are excluded from both means, not recoded as zero; their
    requests still count in all_requests. No rows outside 2023-2024 are accepted.
    """
    selected = _ledger(pairs, PAIR_COLUMNS, "pairs")
    complete = _ledger(cases, CASE_COLUMNS, "cases")
    if set(selected["event_id"]) != set(complete["event_id"]):
        raise ValueError("Both complete ledgers must preserve every original case")
    matched = selected.set_index("event_id")
    original = complete.set_index("event_id").reindex(matched.index)
    if not matched["mother_decision_time"].equals(original["mother_decision_time"]):
        raise ValueError("Paired case decision times do not match original cases")
    if not np.allclose(matched["event_net_return"], original["episode_net_return"],
                       rtol=0, atol=1e-12, equal_nan=True):
        raise ValueError("Paired case returns do not match original case outcomes")
    if not selected["assigned_controls"].isin([0, 3]).all():
        raise ValueError("Assignment must be all three controls or zero")
    unmatched = selected["assigned_controls"].eq(0)
    if selected.loc[unmatched, ["control_mean_return", "excess"]].notna().any().any():
        raise ValueError("Unmatched cases cannot contain control means or excess")
    expected = selected["event_net_return"] - selected["control_mean_return"]
    if not np.allclose(selected["excess"], expected, rtol=0, atol=1e-12, equal_nan=True):
        raise ValueError("Paired excess must equal case minus its control mean")
    # Only scalar saved fields are copied to these local, fixed-identity tables.
    with sqlite3.connect(":memory:") as database:
        selected.to_sql("matched_request_outcomes", database, index=False)
        complete.to_sql("case_request_outcomes", database, index=False)
        aggregated = pd.read_sql_query(SQL, database)
    rows = []
    for values in aggregated.to_dict("records"):
        row = {key: None if pd.isna(value) else value for key, value in values.items()}
        for key in ("matched_requests", "all_requests"):
            row[key] = int(row[key])
        rows.append(row)
    if len(rows) != 48 or sum(row["all_requests"] for row in rows[::2]) != len(complete):
        raise AssertionError("The complete 24-month paired calendar must preserve the denominator")
    return rows


def add_monthly(artifact: dict, pairs: pd.DataFrame, cases: pd.DataFrame,
                pairs_path: str, cases_path: str) -> dict:
    """Deep-copy a native artifact and insert one chart at its sole V7 marker."""
    pairs_path, cases_path = safe_identity(pairs_path), safe_identity(cases_path)
    if pairs_path == cases_path:
        raise ValueError("Pair and case ledgers must have distinct source identities")
    result = copy.deepcopy(artifact)
    manifest, snapshot = result["manifest"], result["snapshot"]
    blocks = manifest["blocks"]
    occurrences = sum(block.get("body", "").count(MARKER) for block in blocks)
    if occurrences != 1:
        raise ValueError("Exactly one V7_MONTHLY narrative marker is required")
    if (any(chart["id"] == CHART_ID for chart in manifest["charts"])
            or any(block.get("id") == BLOCK_ID or block.get("chartId") == CHART_ID for block in blocks)
            or CHART_ID in snapshot["datasets"]
            or any(source.get("id") in {SOURCE_ID, CASES_SOURCE_ID}
                   for source in manifest["sources"] + result["sources"])):
        raise ValueError("Do not duplicate an existing V7 monthly chart or source")
    rows = monthly_rows(pairs, cases)
    case_rows = [row for row in rows if row["series"] == CASE_SERIES]
    matched_count = sum(row["matched_requests"] for row in case_rows)
    all_count = sum(row["all_requests"] for row in case_rows)
    coverage = f"{matched_count / all_count:.1%}" if all_count else "无请求"
    source = {"id": SOURCE_ID, "label": "OKX archive · V7 同请求月度配对收益",
              "path": pairs_path, "query": {
                  "engine": "SQLite", "language": "sql", "sql": SQL,
                  "executed_at": pd.Timestamp.now(tz="UTC").isoformat(),
                  "tables_used": ["main.matched_request_outcomes", "main.case_request_outcomes"],
                  "description": f"Actual monthly aggregation over saved ledgers: main.matched_request_outcomes <- {pairs_path}; main.case_request_outcomes <- {cases_path}. Input timestamps normalized to UTC before loading. Upstream outcomes were generated by yoyo/evaluation/hourly_impulse_source_research.py in Python; this SQL does not rerun prices or the strategy.",
                  "filters": ["BTC-USDT-SWAP development: 2023-01-01 <= mother_decision_time UTC < 2025-01-01", "One row per original request in both complete ledgers; duplicate, missing identity and mismatched original outcomes rejected", "Both plotted means: assigned_controls=3 and finite case/control/excess outcomes; unknown outcomes are not zero", "Full UTC monthly calendar; empty means remain NULL without interpolation"],
                  "metric_definitions": ["netbp: arithmetic mean of eligible case event_net_return * 10000, or arithmetic mean of each identical case's three-control control_mean_return * 10000", "matched_requests: identical eligible original requests for both lines; all_requests: every original case request that month, not controls", "coverage=matched_requests/all_requests; NULL when all_requests=0; excessbp=mean(case minus three-control mean)*10000 on the identical requests", "Per-request net returns, including frozen execution costs, not cumulative/compounded account returns. Monthly means are not averaged to produce the overall request mean."]}}
    case_source = {"id": CASES_SOURCE_ID, "label": "OKX archive · V7 全部请求覆盖率分母",
                   "path": cases_path}
    chart = {"id": CHART_ID, "title": "2023–2024 月度配对请求净收益",
             "subtitle": f"UTC 月 · 同一批 {matched_count}/{all_count} 请求（覆盖率 {coverage}）· 每笔均值 bp，非账户累计收益；无样本月留空",
             "showDescription": True, "type": "line", "intent": "trend",
             "dataset": CHART_ID, "sourceId": SOURCE_ID, "layout": "full",
             "question": "相同配对请求的策略和对照月度净收益如何随月份变化？",
             "rationale": "A fixed 24-month calendar compares temporal variation on identical requests. Native blue/orange series, a visible legend and solid/dashed strokes distinguish the two means without adding a runtime.",
             "comparisonContext": {"grain": "UTC month of original mother_decision_time",
                                   "denominator": "Identical eligible requests with three controls for both means; all requests for coverage only",
                                   "unit": "bp", "normalization": "Arithmetic mean per request, never account accumulation"},
             "palette": {"kind": "categorical"},
             "legend": {"position": "bottom", "sort": "spec", "title": "同请求配对"},
             "labels": {"values": "none"},
             "settings": {"sort": "none", "showPoints": "always"},
             "referenceLines": [{"axis": "y", "value": 0, "color": "neutral", "lineStyle": "solid", "label": "0 bp"}],
             "valueFormat": "number", "unit": "bp", "maxRows": 48,
             "encodings": {"x": {"field": "month", "type": "temporal", "label": "UTC 月"},
                           "y": {"field": "netbp", "type": "quantitative", "label": "每请求平均净收益", "unit": "bp", "format": "number"},
                           "color": {"field": "series", "type": "nominal", "label": "同请求配对"},
                           "lineStyle": {"field": "line_style", "type": "nominal"},
                           "tooltip": [{"field": "matched_requests", "type": "quantitative", "label": "配齐请求数"},
                                       {"field": "all_requests", "type": "quantitative", "label": "全部请求数"},
                                       {"field": "coverage", "type": "quantitative", "label": "配对覆盖率", "format": "percent"},
                                       {"field": "excessbp", "type": "quantitative", "label": "同请求超额均值", "unit": "bp"}]}}
    for new_source in (source, case_source):
        manifest["sources"].append(new_source)
        if result["sources"] is not manifest["sources"]:
            result["sources"].append(copy.deepcopy(new_source))
    location = next(i for i, block in enumerate(blocks) if MARKER in block.get("body", ""))
    blocks[location]["body"] = blocks[location]["body"].replace(MARKER, "").strip()
    blocks.insert(location + 1, {"id": BLOCK_ID, "type": "chart", "chartId": CHART_ID, "layout": "full"})
    manifest["charts"].append(chart)
    snapshot["datasets"][CHART_ID] = rows
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("input", "pairs", "cases", "output"):
        parser.add_argument(f"--{name}", required=True)
    args = parser.parse_args()
    paths = {name: safe_identity(getattr(args, name)) for name in ("input", "pairs", "cases", "output")}
    if paths["output"] in {paths["input"], paths["pairs"], paths["cases"]}:
        raise ValueError("Output must not overwrite the input artifact or saved ledgers")
    artifact = json.loads((ROOT / paths["input"]).read_text())
    revised = add_monthly(artifact, pd.read_csv(ROOT / paths["pairs"]),
                          pd.read_csv(ROOT / paths["cases"]), paths["pairs"], paths["cases"])
    (ROOT / paths["output"]).write_text(json.dumps(revised, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"blocks": len(revised["manifest"]["blocks"]),
                      "charts": len(revised["manifest"]["charts"]),
                      "monthly_rows": len(revised["snapshot"]["datasets"][CHART_ID])}))


if __name__ == "__main__":
    main()
