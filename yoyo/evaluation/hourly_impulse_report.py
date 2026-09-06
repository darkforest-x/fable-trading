"""Package the authored impulse report into the native analytics artifact schema.

Schema sources: Data Analytics 0.2.10 mcp/server.cjs artifactBlock (line 732),
artifactChart/encodings (lines 583--674), sourceSchema (line 380), and
src/analytics-app-core.md Shared Contract / Portable HTML Packaging. This module
does not render HTML, change report prose, read candles, or rerun a strategy.

The native validator requires actual SQL for a chart source. A stdlib SQLite
query really executes over the supplied saved research-result CSV; provenance
explicitly retains the upstream Python evaluator and CSV. This presentation
selection is not represented as the original strategy computation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path, PurePosixPath
import re
import sqlite3
from typing import Any, Dict, Optional

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_ID = "exp-btcusdtp-1h-impulse-ltf-exit-preholdout-20260906-v1"
EXPERIMENT_PATH = "experiments/active/" + EXPERIMENT_ID
REPORT_PATH = "analysis/p1_btcusdtp_hourly_impulse_ltf_exit_20260906.md"
EXIT_CHART_MARKER = "<!-- EXIT_CHART -->"
SOURCE_MARKER = re.compile(r"^\s*<!--\s*SOURCE:\s*([a-zA-Z0-9_-]+)\s*-->\s*$")
EXIT_LABELS = {
    "15m_first": "15m 首次反色", "15m_two": "15m 连续两根反色",
    "15m_slope": "15m 反色且斜率反转", "15m_half": "15m 反色分批退出",
    "5m_native40": "5m / MA40 首次反色", "5m_clock120": "5m / MA120 首次反色",
    "1h_first": "1h 首次反色", "fixed3R": "固定 3R",
}
EXIT_SQL = """SELECT value AS policy_id,
       CAST(events AS INTEGER) AS n,
       CAST(mean_net_bp AS REAL) AS mean_net_bp,
       CAST(win_rate AS REAL) AS win_rate,
       CAST(profit_factor AS REAL) AS profit_factor
FROM main.development_search
WHERE stage = 'exit'
ORDER BY mean_net_bp DESC, policy_id ASC"""


def safe_identity(value: str) -> str:
    """Require exact repository-relative identities, never machine paths."""
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or value.startswith("~"):
        raise ValueError("source identity must be a repository-relative path: %r" % value)
    if str(path) != value:
        raise ValueError("source identity must already be normalized: %r" % value)
    return value


def default_sources(report_path: str, search_path: str) -> Dict[str, str]:
    result = EXPERIMENT_PATH + "/results/"
    return {
        "report": report_path,
        "exit_search": search_path,
        "config": EXPERIMENT_PATH + "/config.json",
        "selection": result + "selection.json",
        "audit_summary": result + "audit_summary.json",
        "audit_baseline_trades": result + "audit_baseline_trades.csv.gz",
        "audit_candidate_trades": result + "audit_candidate_trades.csv.gz",
        "diagnoses": "analysis/output/btcusdtp_1h_impulse_ltf_exit_20260906_v1/summary.json",
        "research_code": "yoyo/evaluation/hourly_impulse_research.py",
    }


def split_markdown(markdown: str) -> tuple[str, list[dict]]:
    """Split peer sections outside code fences, preserving all authored prose.

    One optional SOURCE directive assigns a whole section's verified source.
    The author must use it only where every quantitative claim has that source.
    EXIT_CHART must occur exactly once at the end of its narrative section.
    """
    if re.search(r"(?:/Users/|/home/|/tmp/|file://)", markdown):
        raise ValueError("portable report prose contains a machine-local path")
    lines = markdown.strip().splitlines()
    if not lines or not re.match(r"^# [^#]", lines[0]):
        raise ValueError("report must start with one # title")
    title = lines[0][2:].strip()
    blocks = [{"id": "report_title", "type": "markdown", "body": "# " + title, "layout": "full"}]
    sections, current, fence = [], [], None
    for line in lines[1:]:
        boundary = re.match(r"^\s*(`{3,}|~{3,})", line)
        if boundary:
            marker = boundary.group(1)
            if fence is None:
                fence = marker[0]
            elif marker[0] == fence:
                fence = None
        if fence is None and re.match(r"^## ", line):
            if any(part.strip() for part in current):
                sections.append(current)
            current = [line]
        else:
            current.append(line)
    if any(part.strip() for part in current):
        sections.append(current)
    chart_count = 0
    for index, section in enumerate(sections):
        meaningful = [line for line in section if line.strip()]
        if not meaningful or not meaningful[0].startswith("## "):
            raise ValueError("each report section after the title needs a peer ## heading")
        source_id, chart_here, clean_lines, fence = None, False, [], None
        for line in section:
            boundary = re.match(r"^\s*(`{3,}|~{3,})", line)
            if boundary:
                marker = boundary.group(1)
                if fence is None:
                    fence = marker[0]
                elif marker[0] == fence:
                    fence = None
            source = SOURCE_MARKER.match(line) if fence is None else None
            if source:
                if source_id is not None:
                    raise ValueError("only one block-wide SOURCE is allowed per peer section")
                source_id = source.group(1)
            elif fence is None and line.strip() == EXIT_CHART_MARKER:
                chart_here = True
                chart_count += 1
            else:
                if chart_here and line.strip():
                    raise ValueError("EXIT_CHART must be last in its peer narrative section")
                clean_lines.append(line)
        block = {"id": "section_%02d" % (index + 1), "type": "markdown", "body": "\n".join(clean_lines).strip(), "layout": "full"}
        if source_id:
            block["sourceId"] = source_id
        blocks.append(block)
        if chart_here:
            blocks.append({"id": "exit_policy_chart_block", "type": "chart", "chartId": "exit_policy_comparison", "layout": "full"})
    if chart_count != 1:
        raise ValueError("native report requires exactly one EXIT_CHART marker")
    return title, blocks


def reviewed_exit_rows(search: pd.DataFrame) -> list[dict]:
    """Actually execute the declared SQL over reviewed saved development rows."""
    required = {"stage", "value", "events", "mean_net_bp", "win_rate", "profit_factor"}
    if not required.issubset(search.columns):
        raise ValueError("development search is missing %s" % sorted(required - set(search.columns)))
    selected = search.loc[search["stage"].eq("exit"), list(required)].copy()
    if len(selected) != 8 or set(selected["value"]) != set(EXIT_LABELS):
        raise ValueError("expected each of the eight registered exit policies exactly once")
    for field in ("events", "mean_net_bp", "win_rate", "profit_factor"):
        selected[field] = pd.to_numeric(selected[field], errors="raise")
    if not selected["mean_net_bp"].map(math.isfinite).all():
        raise ValueError("all exit comparisons need finite mean_net_bp")
    if not ((selected["events"] >= 1) & (selected["events"] % 1 == 0)).all():
        raise ValueError("each exit comparison needs a positive integer sample count")
    if not selected["win_rate"].between(0, 1).all():
        raise ValueError("win_rate must be a fractional rate")
    if selected["profit_factor"].lt(0).any():
        raise ValueError("profit_factor must be nonnegative")
    with sqlite3.connect(":memory:") as connection:
        selected.to_sql("development_search", connection, index=False)
        cursor = connection.execute(EXIT_SQL)
        columns = [field[0] for field in cursor.description]
        rows = [dict(zip(columns, values)) for values in cursor.fetchall()]
    for rank, row in enumerate(rows, start=1):
        row["policy"] = EXIT_LABELS[row["policy_id"]]
        row["rank"] = rank
        row["period"] = "2023–2024 development"
        row["mean_net_bp_signed"] = "%+.2f bp" % row["mean_net_bp"]
        pf = row["profit_factor"]
        row["profit_factor_label"] = "∞" if pf is not None and math.isinf(pf) else str(pf)
        if pf is not None and not math.isfinite(pf):
            row["profit_factor"] = None
    return rows


def build_artifact(
    markdown: str,
    search: pd.DataFrame,
    *,
    report_path: str = REPORT_PATH,
    search_path: str = EXPERIMENT_PATH + "/results/development_search.csv",
    source_paths: Optional[Dict[str, str]] = None,
    generated_at: str,
    fixture: bool = False,
) -> dict:
    """Build the canonical one-chart report, without reading any source file."""
    title, blocks = split_markdown(markdown)
    rows = reviewed_exit_rows(search)
    paths = default_sources(safe_identity(report_path), safe_identity(search_path))
    paths.update(source_paths or {})
    paths = {key: safe_identity(value) for key, value in paths.items()}
    needed = {"report", "exit_search", "research_code"} | {block["sourceId"] for block in blocks if "sourceId" in block}
    unknown = needed - set(paths)
    if unknown:
        raise ValueError("unknown SOURCE identities: %s; supply --source ID=relative/path" % sorted(unknown))
    timestamp = pd.to_datetime(generated_at, utc=True).isoformat()
    sources = []
    for source_id in sorted(needed):
        source = {"id": source_id, "label": "OKX archive · " + source_id.replace("_", " "), "path": paths[source_id]}
        if source_id == "exit_search":
            source["query"] = {
                "engine": "SQLite", "language": "sql", "sql": EXIT_SQL,
                "description": (
                    "Presentation SELECT actually executed over the saved development_search CSV. "
                    "The upstream strategy calculation is Python in yoyo/evaluation/hourly_impulse_research.py, "
                    "using the OKX BTC-USDT-SWAP archive; SQL does not rerun that backtest. "
                    "One row per registered exit policy; n, win_rate and profit_factor retain review context. "
                    "The shared renderer may override sequential blue with green/red for mixed-sign bars."
                ),
                "executed_at": timestamp,
                "tables_used": ["main.development_search"],
                "filters": ["stage = exit", "BTC-USDT-SWAP", "Development: 2023–2024; transport: 2025–February 2026", "Closed finite outcomes only in upstream metrics; unclosed marks are excluded"],
                "metric_definitions": [
                    "mean_net_bp = arithmetic mean of completed independent-event net_return × 10,000; 20 bp round-trip cost in upstream replay",
                    "n = completed events; win_rate = positive net outcomes / n; profit_factor = sum positive net outcomes / abs(sum negative net outcomes)",
                    "Independent-event returns are not compounded account returns; transport is frozen-rule testing on historically used data",
                ],
            }
        sources.append(source)
    chart = {
        "id": "exit_policy_comparison", "title": "退出方式与平均净收益",
        "subtitle": "2023–2024 开发期 · 已闭合事件 · 扣除 20 bp 往返成本 · 0 为盈亏分界",
        "showDescription": True, "type": "horizontalBar", "intent": "comparison",
        "question": "同一组 1h 入场信号，不同退出方式的平均净收益相差多少？",
        "rationale": "Eight comparable policies; sorted signed bars provide a full-width comparison while preserving sample count, win rate, and PF in the snapshot.",
        "dataset": "exit_policy_results", "sourceId": "exit_search", "layout": "full",
        "valueFormat": "number", "unit": "bp", "maxRows": 8,
        "palette": {"kind": "sequential", "name": "blue"},
        "labels": {"values": "all"}, "settings": {"sort": "none", "categoryLabelPolicy": "wrap"},
        "referenceLines": [{"axis": "x", "value": 0, "color": "neutral", "lineStyle": "solid", "label": "0 bp"}],
        "encodings": {
            "x": {"field": "policy", "type": "nominal", "label": "退出方式"},
            "y": {"field": "mean_net_bp", "type": "quantitative", "label": "平均净收益", "unit": "bp", "format": "number"},
            "tooltip": [
                {"field": "mean_net_bp_signed", "type": "nominal", "label": "平均净收益"},
                {"field": "n", "type": "quantitative", "label": "已闭合笔数", "format": "number"},
                {"field": "win_rate", "type": "quantitative", "label": "胜率", "format": "percent"},
                {"field": "profit_factor", "type": "quantitative", "label": "PF", "format": "number"},
            ],
        },
    }
    return {
        "surface": "report",
        "manifest": {"version": 1, "surface": "report", "title": title, "generatedAt": timestamp,
                     "filters": [], "cards": [], "charts": [chart], "tables": [], "sources": sources, "blocks": blocks},
        "snapshot": {"version": 1, "generatedAt": timestamp, "status": "fixture" if fixture else "ready",
                     "datasets": {"exit_policy_results": rows}, "accessIssues": []},
        "sources": sources,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", default=REPORT_PATH)
    parser.add_argument("--search", default=EXPERIMENT_PATH + "/results/development_search.csv")
    parser.add_argument("--output", required=True)
    parser.add_argument("--source", action="append", default=[], metavar="ID=RELATIVE_PATH")
    parser.add_argument("--generated-at", default=datetime.now(timezone.utc).isoformat())
    args = parser.parse_args()
    report_path, search_path = safe_identity(args.markdown), safe_identity(args.search)
    overrides = {}
    for mapping in args.source:
        key, separator, value = mapping.partition("=")
        if not separator or not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
            parser.error("--source must be ID=relative/path")
        overrides[key] = safe_identity(value)
    artifact = build_artifact(
        (ROOT / report_path).read_text(encoding="utf-8"), pd.read_csv(ROOT / search_path),
        report_path=report_path, search_path=search_path, source_paths=overrides, generated_at=args.generated_at,
    )
    for source in artifact["sources"]:
        if not (ROOT / source["path"]).exists():
            raise FileNotFoundError("declared source does not exist: " + source["path"])
    output = ROOT / safe_identity(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"artifact": output.relative_to(ROOT).as_posix(), "blocks": len(artifact["manifest"]["blocks"]), "chart_rows": 8}, ensure_ascii=False))


if __name__ == "__main__":
    main()
