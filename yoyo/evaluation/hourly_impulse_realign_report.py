"""Add one native V6 distribution chart without altering prior report blocks.

This is report presentation only: saved paired mother returns, not prices.
The explicit ordered-category count plot retains the zero atom and unequal
interval labels rather than implying equal-width histogram density. Native
schema: Data Analytics0.2.10 mcp/server.cjs artifactChart/encodings. SQL actually
runs against the supplied saved paired ledger and preserves all mother rows.
"""
from __future__ import annotations

import argparse
import copy
import json
import sqlite3

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_report import ROOT, safe_identity


MARKER = "<!-- V6_DISTRIBUTION -->"
LABELS = ["< -50", "[-50, -20)", "[-20, -5)", "[-5, 0)", "0 · 不变",
          "(0, 5)", "[5, 20)", "[20, 50)", ">= 50", "未知"]
SQL = """WITH classified AS (
  SELECT CASE WHEN difference IS NULL THEN 9
              WHEN ABS(difference) <= 0.000000000001 THEN 4
              WHEN difference * 10000 < -50 THEN 0
              WHEN difference * 10000 < -20 THEN 1
              WHEN difference * 10000 < -5 THEN 2
              WHEN difference < 0 THEN 3
              WHEN difference * 10000 < 5 THEN 5
              WHEN difference * 10000 < 20 THEN 6
              WHEN difference * 10000 < 50 THEN 7 ELSE 8 END AS bucket,
         difference * 10000 AS change_bp
  FROM main.paired_mothers
)
SELECT bucket, COUNT(*) AS mothers, SUM(change_bp) AS summed_event_change_bp,
       AVG(change_bp) AS mean_change_bp, MIN(change_bp) AS minimum_change_bp,
       MAX(change_bp) AS maximum_change_bp
FROM classified GROUP BY bucket ORDER BY bucket"""


def distribution_rows(pairs: pd.DataFrame) -> list[dict]:
    if not {"event_id", "difference"}.issubset(pairs):
        raise ValueError("Need original mother identities and paired changes")
    if pairs["event_id"].isna().any() or pairs["event_id"].duplicated().any():
        raise ValueError("Every mother must occur exactly once")
    source = pairs[["event_id", "difference"]].copy()
    source["difference"] = pd.to_numeric(source["difference"], errors="raise")
    if np.isinf(source["difference"]).any():
        raise ValueError("Infinite returns cannot be plotted as finite evidence")
    with sqlite3.connect(":memory:") as database:
        source.to_sql("paired_mothers", database, index=False)
        selected = pd.read_sql_query(SQL, database).set_index("bucket")
    rows = []
    for index, label in enumerate(LABELS):
        values = selected.loc[index].to_dict() if index in selected.index else {}
        row = {"bucket": index, "interval_bp": label, "mothers": int(values.get("mothers", 0)),
               "all_mothers": len(pairs)}
        for key in ("summed_event_change_bp", "mean_change_bp", "minimum_change_bp", "maximum_change_bp"):
            value = values.get(key)
            row[key] = float(value) if value is not None and pd.notna(value) else None
        rows.append(row)
    if sum(row["mothers"] for row in rows) != len(pairs):
        raise AssertionError("Distribution must preserve every mother")
    return rows


def add_distribution(artifact: dict, pairs: pd.DataFrame, source_path: str) -> dict:
    result = copy.deepcopy(artifact)
    blocks = result["manifest"]["blocks"]
    locations = [i for i, block in enumerate(blocks) if MARKER in block.get("body", "")]
    if len(locations) != 1:
        raise ValueError("Exactly one adjacent V6 distribution narrative is required")
    if any(chart["id"] == "v6_paired_distribution" for chart in result["manifest"]["charts"]):
        raise ValueError("Do not duplicate an existing V6 chart")
    location = locations[0]
    blocks[location]["body"] = blocks[location]["body"].replace(MARKER, "").strip()
    source = {"id": "v6_distribution", "label": "V6 · 全部原始机会配对变化",
              "path": safe_identity(source_path), "query": {
                  "engine": "SQLite", "language": "sql", "sql": SQL,
                  "executed_at": result["manifest"]["generatedAt"],
                  "tables_used": ["main.paired_mothers"],
                  "description": "Presentation count query over saved paired_changes.csv.gz. Upstream returns are Python outcomes from hourly_impulse_realign_research.py; SQL does not rerun prices or a strategy.",
                  "filters": ["BTC-USDT-SWAP development2023-2024", "All original mothers, including known nonentry zeros and unknowns", "One row per maternal event_id"],
                  "metric_definitions": ["difference=(flat-alignment episode return)-(immediate episode return); bp=difference*10000", "Known nontrade return0; unknown remainsNULL. Absolute difference<=1e-12 counted unchanged, matching the paired audit", "Y is count, not probability density; intervals have unequal widths. Summed event changes are not compounded account P/L"]}}
    chart = {
        "id": "v6_paired_distribution", "title": "入场等待的配对收益变化分布",
        "subtitle": "全部原始机会 · 横轴为新减旧的净收益变化区间（bp）· 纵轴为机会数，不是密度",
        "showDescription": True, "type": "bar", "intent": "distribution",
        "dataset": "v6_paired_distribution", "sourceId": "v6_distribution", "layout": "full",
        "question": "改进是广泛发生，还是多数不变、少数改变相互抵消？",
        "rationale": "Count categories isolate the zero atom and retain unknowns. Unequal interval widths are explicit and never shown as numeric density. Prior report charts remain intact.",
        "palette": {"kind": "sequential", "name": "blue"}, "labels": {"values": "all"},
        "settings": {"sort": "none", "categoryLabelPolicy": "wrap"},
        "valueFormat": "number", "maxRows": 10,
        "encodings": {"x": {"field": "interval_bp", "type": "ordinal", "label": "配对变化区间（bp）"},
                      "y": {"field": "mothers", "type": "quantitative", "label": "原始机会数", "format": "number"},
                      "tooltip": [{"field": "mothers", "type": "quantitative", "label": "机会数"},
                                  {"field": "mean_change_bp", "type": "quantitative", "label": "区间内均值（bp）"},
                                  {"field": "all_mothers", "type": "quantitative", "label": "完整分母"}]}}
    result["manifest"]["sources"].append(source)
    # deepcopy preserves shared lists in the original payload; avoid appending
    # twice when top-level and manifest sources were the same Python object.
    if result["sources"] is not result["manifest"]["sources"]:
        result["sources"].append(copy.deepcopy(source))
    result["manifest"]["charts"].append(chart)
    blocks.insert(location+1, {"id": "v6_distribution_chart", "type": "chart", "chartId": chart["id"], "layout": "full"})
    result["snapshot"]["datasets"][chart["dataset"]] = distribution_rows(pairs)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--pairs", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    source_path = safe_identity(args.pairs)
    artifact = json.loads((ROOT/safe_identity(args.input)).read_text())
    revised = add_distribution(artifact, pd.read_csv(ROOT/source_path), source_path)
    (ROOT/safe_identity(args.output)).write_text(json.dumps(revised, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"blocks": len(revised["manifest"]["blocks"]), "charts": len(revised["manifest"]["charts"]), "all_mothers": len(pd.read_csv(ROOT/source_path))}))


if __name__ == "__main__":
    main()
