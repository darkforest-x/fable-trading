#!/usr/bin/env python3
"""Build the canonical portable report input for ETH 3m v10 label timing.

This report consumes only the validated outputs of
``analyze_eth3m_v10_yes_no_labels.py``.  It does not query Label Studio, read
post-2026-05-04 bars, train a model, or turn the future-aware timing proxy into
a causal feature.  The generated ``artifact.json`` is rendered by the bundled
Data Analytics portable report builder.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = PROJECT / "analysis/output/eth3m_v10_label_timing"


def source_sql() -> str:
    return """-- Reproduce the headline, timing, and confidence datasets.
-- Run from the repository root with:
-- sqlite3 -header -csv :memory: < analysis/output/eth3m_v10_label_timing/source.sql
WITH raw AS (
  SELECT CAST(readfile(
    'analysis/output/eth3m_v10_label_timing/summary.json'
  ) AS TEXT) AS payload
)
SELECT
  json_extract(payload, '$.task_count') AS task_count,
  json_extract(payload, '$.owner_yes_count') AS owner_yes_count,
  json_extract(payload, '$.owner_no_count') AS owner_no_count,
  json_extract(payload, '$.owner_yes_rate_pct') / 100.0 AS owner_yes_rate,
  json_extract(payload, '$.owner_yes.box_elapsed_min_median') AS box_elapsed_min_median,
  json_extract(payload, '$.owner_yes.consumed_atr_median') AS consumed_atr_median,
  json_extract(payload, '$.owner_yes.share_consumed_exceeds_remaining_pct') / 100.0
    AS consumed_exceeds_remaining_rate
FROM raw;

WITH raw AS (
  SELECT CAST(readfile(
    'analysis/output/eth3m_v10_label_timing/summary.json'
  ) AS TEXT) AS payload
)
SELECT
  json_extract(bin.value, '$.confidence_bucket') AS confidence_bucket,
  json_extract(bin.value, '$.task_count') AS task_count,
  json_extract(bin.value, '$.owner_yes_count') AS owner_yes_count,
  json_extract(bin.value, '$.owner_yes_rate_pct') / 100.0 AS owner_yes_rate
FROM raw
JOIN json_each(raw.payload, '$.confidence_buckets') AS bin;
"""


def _timing_segment_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    timing = summary["owner_yes_timing_proxy"]
    return [
        {
            "segment": "剩余不少于已走",
            "n": timing["remaining_not_less_than_consumed"]["n"],
            "share_of_owner_yes": timing["remaining_not_less_than_consumed"][
                "share_of_owner_yes_pct"
            ]
            / 100,
            "median_consumed_atr": timing["remaining_not_less_than_consumed"][
                "consumed_atr_median"
            ],
            "median_remaining_atr": timing["remaining_not_less_than_consumed"][
                "remaining_drop_atr_median"
            ],
            "future_3h_close_down_rate": timing["remaining_not_less_than_consumed"][
                "future_3h_close_down_rate_pct"
            ]
            / 100,
            "median_future_rebound": timing["remaining_not_less_than_consumed"][
                "future_rebound_pct_median"
            ]
            / 100,
        },
        {
            "segment": "已走多于剩余",
            "n": timing["consumed_exceeds_remaining"]["n"],
            "share_of_owner_yes": timing["consumed_exceeds_remaining"][
                "share_of_owner_yes_pct"
            ]
            / 100,
            "median_consumed_atr": timing["consumed_exceeds_remaining"][
                "consumed_atr_median"
            ],
            "median_remaining_atr": timing["consumed_exceeds_remaining"][
                "remaining_drop_atr_median"
            ],
            "future_3h_close_down_rate": timing["consumed_exceeds_remaining"][
                "future_3h_close_down_rate_pct"
            ]
            / 100,
            "median_future_rebound": timing["consumed_exceeds_remaining"][
                "future_rebound_pct_median"
            ]
            / 100,
        },
    ]


def build_artifact(summary: dict[str, Any], detail: pd.DataFrame) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    yes = detail[detail["owner_is_target"] == 1]
    no = detail[detail["owner_is_target"] == 0]
    timing_rows = _timing_segment_rows(summary)

    label_rows: list[dict[str, Any]] = []
    for label, part in (("是（形态成立）", yes), ("不是", no)):
        label_rows.append(
            {
                "owner_label": label,
                "n": int(len(part)),
                "share": float(len(part) / len(detail)),
                "median_box_elapsed_min": float(part["box_elapsed_min"].median()),
                "median_consumed_atr": float(part["consumed_atr"].median()),
                "median_remaining_atr": float(part["remaining_drop_atr"].median()),
                "future_3h_close_down_rate": float((part["outcome_return_3h"] < 0).mean()),
            }
        )

    timing_outcome_rows: list[dict[str, Any]] = []
    yes = yes.copy()
    yes["timing_segment"] = (
        yes["consumed_drop_abs"] > yes["remaining_drop_abs"]
    ).map({False: "剩余≥已走", True: "已走>剩余"})
    yes["future_state"] = (yes["outcome_return_3h"] < 0).map(
        {True: "3h收跌", False: "3h未收跌"}
    )
    order = [
        ("剩余≥已走", "3h收跌"),
        ("剩余≥已走", "3h未收跌"),
        ("已走>剩余", "3h收跌"),
        ("已走>剩余", "3h未收跌"),
    ]
    for timing, future_state in order:
        count = int(
            ((yes["timing_segment"] == timing) & (yes["future_state"] == future_state)).sum()
        )
        timing_outcome_rows.append(
            {
                "segment": f"{timing} · {future_state}",
                "task_count": count,
                "share_of_owner_yes": count / len(yes),
                "timing_proxy": timing,
                "future_state": future_state,
            }
        )

    confidence_rows = [
        {
            "confidence_bucket": row["confidence_bucket"],
            "task_count": row["task_count"],
            "owner_yes_count": row["owner_yes_count"],
            "owner_yes_rate": row["owner_yes_rate_pct"] / 100,
            "median_consumed_atr": row["median_consumed_atr"],
        }
        for row in summary["confidence_buckets"]
    ]

    source = {
        "id": "owner_label_timing",
        "label": "ETH 3m v10 200 张 owner 标注与时序诊断",
        "path": "analysis/output/eth3m_v10_label_timing/source.sql",
        "query": {
            "engine": "sqlite",
            "language": "sql",
            "id": "eth3m-v10-owner-label-timing-20260729",
            "description": "从项目 53 的 200 条 owner 是/不是标签、冻结 manifest 与严格裁到 holdout 前的 ETH 3m OHLC 复算框宽、已走跌幅和未来剩余跌幅。",
            "executed_at": generated_at,
            "tables_used": [
                "analysis/output/eth3m_v10_label_timing/project_53_owner_choices.csv",
                "datasets/eth_3m_v10_prebox200/manifest.csv",
                "data/kline_fetched/okx_ETH_USDT_SWAP_3m_57705.csv",
            ],
            "filters": [
                "200/200 tasks have exactly one owner is_target choice",
                "candidate and future-end timestamps are strictly before 2026-05-04 00:00 UTC",
                "v10 box right edge maps to the causal tip bar for all 200 tasks",
            ],
            "metric_definitions": [
                "owner yes rate = owner answered 是 / 200; it means shape approval, not entry-timing approval",
                "box elapsed minutes = (mapped last box bar - mapped first box bar) × 3",
                "consumed ATR = max(0, highest close inside box - signal close) / causal ATR14 at signal",
                "remaining ATR = max future 3h decline from signal close / causal ATR14 at signal",
                "timing proxy = consumed drop exceeds maximum remaining 3h drop; this is a future-aware sensitivity cut, not a causal feature or accepted threshold",
            ],
        },
    }
    code_source = {
        "id": "timing_code",
        "label": "几何、ATR 与 holdout-safe 复算代码",
        "path": "scripts/analyze_eth3m_v10_yes_no_labels.py",
        "query": {
            "engine": "source-code",
            "language": "python",
            "description": "解析 Label Studio is_target，使用实际 ChartTransform 映射 box，并在物理裁掉 holdout 后计算因果 ATR 与未来结果诊断。",
            "tables_used": [
                "scripts/analyze_eth3m_v10_yes_no_labels.py",
                "scripts/build_eth_3m_dual_view_calibration.py",
                "src/detection/render.py",
            ],
        },
    }

    headline = [
        {
            "owner_yes_rate": summary["owner_yes_rate_pct"] / 100,
            "median_box_elapsed_min": summary["owner_yes"]["box_elapsed_min_median"],
            "median_consumed_atr": summary["owner_yes"]["consumed_atr_median"],
            "late_proxy_rate": summary["owner_yes"][
                "share_consumed_exceeds_remaining_pct"
            ]
            / 100,
        }
    ]

    manifest: dict[str, Any] = {
        "version": 1,
        "surface": "report",
        "title": "ETH 3m v10 标注后的迟到诊断",
        "description": "200 张 owner 是/不是标注显示：部分形态确实标准，但 v10 的确认点系统性偏晚，检测形态与入场时机必须拆开。",
        "generatedAt": generated_at,
        "cards": [
            {
                "id": "shape_yes",
                "description": "93/200 张被 owner 判定为红框形态成立；它不是入场可用率。",
                "dataset": "headline",
                "sourceId": "owner_label_timing",
                "metrics": [{"label": "形态判是率", "field": "owner_yes_rate", "format": "percent"}],
            },
            {
                "id": "box_span",
                "description": "owner 判是样本的红框横向中位跨度。",
                "dataset": "headline",
                "sourceId": "owner_label_timing",
                "metrics": [
                    {
                        "label": "红框中位跨度",
                        "field": "median_box_elapsed_min",
                        "format": "number",
                        "unit": "分钟",
                    }
                ],
            },
            {
                "id": "consumed_atr",
                "description": "从框内最高收盘到信号收盘，owner 判是样本已走掉的 3m ATR 中位数。",
                "dataset": "headline",
                "sourceId": "owner_label_timing",
                "metrics": [
                    {
                        "label": "信号前已走",
                        "field": "median_consumed_atr",
                        "format": "number",
                        "unit": " ATR",
                    }
                ],
            },
            {
                "id": "late_proxy",
                "description": "owner 判是中，信号前已走跌幅大于随后 3h 最大剩余跌幅的占比；仅作敏感性诊断。",
                "dataset": "headline",
                "sourceId": "owner_label_timing",
                "metrics": [{"label": "迟到风险代理", "field": "late_proxy_rate", "format": "percent"}],
            },
        ],
        "charts": [
            {
                "id": "timing_outcome_chart",
                "title": "93 张形态成立样本的时序与 3h 收盘拆分",
                "subtitle": "‘已走>剩余’使用未来最大下探作诊断，不能作为实盘特征或最终阈值",
                "showDescription": True,
                "intent": "comparison",
                "question": "形态成立后，信号前已走幅度与未来是否仍收跌如何共同分布？",
                "rationale": "四个互斥类别直接显示 93 张 owner-positive 的组成，避免把形态精度误当入场精度。",
                "comparisonContext": {
                    "grain": "owner 判是任务",
                    "unit": "张",
                    "denominator": "93 张形态成立样本",
                },
                "type": "horizontalBar",
                "dataset": "timing_outcome",
                "sourceId": "owner_label_timing",
                "encodings": {
                    "x": {"field": "segment", "type": "ordinal", "label": "时序 × 结果"},
                    "y": {"field": "task_count", "type": "quantitative", "label": "任务数"},
                    "tooltip": [
                        {"field": "share_of_owner_yes", "type": "quantitative", "label": "占形态成立", "format": "percent"},
                        {"field": "timing_proxy", "type": "nominal", "label": "时序代理"},
                        {"field": "future_state", "type": "nominal", "label": "3h 收盘"},
                    ],
                },
                "valueFormat": "number",
                "unit": "张",
                "layout": "full",
                "labels": {"values": "all"},
                "palette": {"kind": "sequential", "name": "orange"},
                "settings": {"sort": "custom", "showValues": True},
                "surface": {"surface": "card", "viewMode": "both"},
            },
            {
                "id": "confidence_shape_chart",
                "title": "v10 置信度分箱与 owner 判是率",
                "subtitle": "200 张；每箱 n=26–94，判是率没有稳定单调上升",
                "showDescription": True,
                "intent": "comparison",
                "question": "提高 v10 置信度能否单独解决形态误框？",
                "rationale": "四个有序置信度箱用柱图比较 owner 判是率，并在 tooltip 保留样本量。",
                "comparisonContext": {
                    "grain": "Label Studio 任务",
                    "unit": "比例",
                    "denominator": "每个置信度箱的任务数",
                },
                "type": "bar",
                "dataset": "confidence",
                "sourceId": "owner_label_timing",
                "encodings": {
                    "x": {"field": "confidence_bucket", "type": "ordinal", "label": "v10 置信度"},
                    "y": {"field": "owner_yes_rate", "type": "quantitative", "label": "owner 判是率", "format": "percent"},
                    "tooltip": [
                        {"field": "task_count", "type": "quantitative", "label": "任务数"},
                        {"field": "owner_yes_count", "type": "quantitative", "label": "判是数"},
                        {"field": "median_consumed_atr", "type": "quantitative", "label": "已走 ATR 中位数"},
                    ],
                },
                "valueFormat": "percent",
                "layout": "full",
                "labels": {"values": "all"},
                "palette": {"kind": "sequential", "name": "blue"},
                "settings": {"sort": "custom", "showValues": True},
                "surface": {"surface": "card", "viewMode": "both"},
            },
        ],
        "tables": [
            {
                "id": "label_table",
                "title": "owner 是/不是样本对照",
                "subtitle": "形态标签、框跨度、信号前已走与未来 3h 最大剩余幅度",
                "dataset": "label_groups",
                "sourceId": "owner_label_timing",
                "density": "spacious",
                "defaultSort": {"field": "n", "direction": "desc"},
                "columns": [
                    {"field": "owner_label", "label": "owner 标签", "type": "text"},
                    {"field": "n", "label": "任务数", "format": "number"},
                    {"field": "share", "label": "占 200 张", "format": "percent"},
                    {"field": "median_box_elapsed_min", "label": "框跨度中位(分钟)", "format": "number"},
                    {"field": "median_consumed_atr", "label": "信号前已走(ATR)", "format": "number"},
                    {"field": "median_remaining_atr", "label": "未来剩余(ATR)", "format": "number"},
                    {"field": "future_3h_close_down_rate", "label": "3h 收跌率", "format": "percent"},
                ],
            },
            {
                "id": "timing_table",
                "title": "93 张形态成立样本的时序代理对照",
                "subtitle": "代理只用于发现问题；最终入场是否来得及仍需单独人工标签",
                "dataset": "timing_segments",
                "sourceId": "owner_label_timing",
                "density": "spacious",
                "defaultSort": {"field": "n", "direction": "desc"},
                "columns": [
                    {"field": "segment", "label": "时序代理", "type": "text"},
                    {"field": "n", "label": "任务数", "format": "number"},
                    {"field": "share_of_owner_yes", "label": "占判是样本", "format": "percent"},
                    {"field": "median_consumed_atr", "label": "已走中位(ATR)", "format": "number"},
                    {"field": "median_remaining_atr", "label": "剩余中位(ATR)", "format": "number"},
                    {"field": "future_3h_close_down_rate", "label": "3h 收跌率", "format": "percent"},
                    {"field": "median_future_rebound", "label": "未来反抽中位", "format": "percent"},
                ],
            },
        ],
        "sources": [source, code_source],
        "blocks": [
            {"id": "title", "type": "markdown", "body": "# ETH 3m v10 标注后的迟到诊断"},
            {
                "id": "executive_summary",
                "type": "markdown",
                "sourceId": "owner_label_timing",
                "body": "## Executive Summary\n\n- **你看到的问题成立，而且是目标语义问题。** 200 张全部标完：93 张‘是’、107 张‘不是’，形态判是率 46.5%。但‘是’只说明红框位置像你要的做空形态，不代表框右缘还能作为入场点。\n- **形态成立的框普遍已经走了一段。** 93 张判是样本的框横向中位跨度 36 分钟；到信号端时，从框内最高收盘到当前收盘中位已经下跌 4.47 个 3m ATR。全部 93 张在信号端都位于六条均线下方。\n- **至少有一组明显的迟到风险样本。** 41/93（44.1%）在信号前已走跌幅大于随后 3h 的最大剩余跌幅；这组未来 3h 最终收跌率仅 41.5%，另一组为 86.5%。这只是未来感知的诊断代理，不是最终入场阈值。\n- **不能把这些框简单改成负例，也不能只裁最宽的框。** 框宽与已走 ATR 的 Spearman 只有 0.008；正确做法是保留 93 张形态正例，再给‘入场时机’增加一个独立、仍然只有是/不是的审核。",
            },
            {"id": "headline_metrics", "type": "metric-strip", "cardIds": ["shape_yes", "box_span", "consumed_atr", "late_proxy"]},
            {
                "id": "definitions",
                "type": "markdown",
                "body": "## 先把两个问题分开\n\n**形态成立**：你在当前界面按‘是’，表示红框覆盖的是想要的做空形态。\n\n**入场来得及**：模型在框右缘开火时，后面是否仍有足够空间、且反抽风险是否可接受。这一问目前没有人工标签，所以不能从现有‘是/不是’里直接知道哪 41 张就是你说的迟到框。\n\n报告中的‘已走>剩余’只是一条敏感性代理：比较信号前已走跌幅与未来 3h 最大剩余跌幅，用来验证问题存在，不会进入实盘特征。",
            },
            {
                "id": "owner_results",
                "type": "markdown",
                "sourceId": "owner_label_timing",
                "body": "## 你的标注确认 v10 有一部分能认出形态\n\n93/200 张被判为‘是’，明显高于随机猜测式的零质量，但仍有 107 张误框。高置信度箱的判是率从 25.0% 到 54.3% 有改善，却不是稳定单调，因此调高 conf 只能过滤一部分误框，不能修复时机。",
            },
            {"id": "label_table_block", "type": "table", "tableId": "label_table"},
            {
                "id": "late_evidence",
                "type": "markdown",
                "sourceId": "owner_label_timing",
                "body": "## 红框正确，不等于红框右缘可以入场\n\n93 张判是样本中，红框中位包含 13 根 3m K 线，即首尾相隔 36 分钟；第一次收盘落到六条均线下方后，到 v10 开火又过了中位 33 分钟。所有 200 个框的右缘都映射到因果窗口最后一根，因此这里测到的不是 HTML 偏移，而是 v10 把‘已经确认的下破段’整体当成目标。\n\n下面把 93 张按‘已走跌幅是否超过未来 3h 最大剩余跌幅’和未来 3h 最终是否收跌拆开。关键不是给 41 张直接判死刑，而是证明形态层与时机层混在一个标签里会产生错误训练目标。",
            },
            {"id": "timing_outcome_chart_block", "type": "chart", "chartId": "timing_outcome_chart", "layout": "full"},
            {"id": "timing_table_block", "type": "table", "tableId": "timing_table"},
            {
                "id": "confidence_text",
                "type": "markdown",
                "sourceId": "owner_label_timing",
                "body": "## 只调置信度解决不了迟到\n\n置信度与 owner 判是的 Spearman 只有 0.174；而框横向跨度与信号前已走 ATR 的相关几乎为零（0.008）。也就是说，既不能把高 conf 当成‘来得及’，也不能只删掉最宽的框。时机必须有自己的监督信号。",
            },
            {"id": "confidence_chart_block", "type": "chart", "chartId": "confidence_shape_chart", "layout": "full"},
            {
                "id": "recommendation",
                "type": "markdown",
                "body": "## 推荐下一步：保留形态正例，另做一条入场线\n\n1. **保留当前 93 张‘是’作为形态正例。** 迟到但形态正确的样本不能改成 detector 负例，否则模型会把真正的密集启动结构一起忘掉。\n2. **红框只表示形态核心，不再把右边缘当入场。** 我会在每张判是图上自动提出一条更早的竖线；你仍然只回答一个问题：‘这个入场点来得及吗？是 / 不是’。\n3. **只给 93 张做时机复核，不让你重标 200 张。** 第一轮先做 30 张校准，确认竖线口径后再扩到其余 63 张。\n4. **训练时物理隔离未来。** 人工审核图可以看未来 3h；最终训练图只截到候选竖线，模型特征只能使用该线及之前的数据。\n5. **模型拆成两层。** 原生 ETH 3m 检测器负责找‘密集形态核心’，因果时机层负责判断‘现在能否入场’；最终开火必须两层同时通过。",
            },
            {
                "id": "questions",
                "type": "markdown",
                "body": "## 现在需要你决定的只有一件事\n\n是否按这个口径先生成 **30 张‘更早入场竖线’校准图**：只使用你已经标‘是’的样本，界面仍然只有‘是 / 不是’两个选择？你确认这 30 张的入场线口径后，我再批量做剩余 63 张。",
            },
            {
                "id": "caveats",
                "type": "markdown",
                "sourceId": "owner_label_timing",
                "body": "## Caveats and assumptions\n\n- 当前 owner 标签只回答形态，不回答入场时机；41/93 是未来感知的迟到风险代理，不能冒充人工真值。\n- ‘已走 ATR’以框内最高收盘到信号收盘计算；改用框左缘收盘时，判是样本的中位跌幅仍为 0.82%，结论方向不变。\n- 200 张按相隔超过 1 小时去重后只有 104 个事件，因此任务级比例不能当 200 个独立行情事件的置信区间。\n- 所有候选和未来结束时间均严格早于 2026-05-04；本次没有读取或消耗 holdout。\n- 未来 3h 只用于人工判断和诊断，不能进入 detector 或时机层的输入特征。",
            },
        ],
    }

    snapshot = {
        "version": 1,
        "generatedAt": generated_at,
        "status": "ready",
        "datasets": {
            "headline": headline,
            "label_groups": label_rows,
            "timing_segments": timing_rows,
            "timing_outcome": timing_outcome_rows,
            "confidence": confidence_rows,
        },
    }
    return {
        "surface": "report",
        "manifest": manifest,
        "snapshot": snapshot,
        "sources": [source, code_source],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    args = parser.parse_args()
    summary = json.loads((args.input / "summary.json").read_text(encoding="utf-8"))
    detail = pd.read_csv(args.input / "task_timing_metrics.csv")
    artifact = build_artifact(summary, detail)
    (args.input / "source.sql").write_text(source_sql(), encoding="utf-8")
    (args.input / "artifact.json").write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    notes = {
        "audience": "product stakeholders",
        "delivery_mode": "html",
        "chart_map": [
            {
                "section": "红框正确，不等于红框右缘可以入场",
                "question": "93 张形态成立样本如何按时序代理与 3h 收盘结果分布？",
                "family": "comparison",
                "type": "horizontalBar",
                "fields": ["segment", "task_count"],
                "palette": "single-root orange",
                "claim": "时序代理将形态正例拆成性质明显不同的两组。",
            },
            {
                "section": "只调置信度解决不了迟到",
                "question": "v10 conf 能否单调排序 owner shape precision？",
                "family": "comparison",
                "type": "bar",
                "fields": ["confidence_bucket", "owner_yes_rate"],
                "palette": "single-root blue",
                "claim": "置信度只能弱过滤，不是入场时机分数。",
            },
        ],
        "validation": summary["data_quality"],
        "omissions": [
            "No task is declared human-confirmed late because the current UI never asked that question.",
            "No economic PnL or barrier test is reported because barrier/cost settings require owner approval and are outside this diagnosis.",
        ],
    }
    (args.input / "report_notes.json").write_text(
        json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(args.input / "artifact.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
