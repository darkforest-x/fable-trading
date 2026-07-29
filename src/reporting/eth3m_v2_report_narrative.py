"""Markdown and artifact assembly for the ETH 3m v2a audit report."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from src.reporting.eth3m_v2_report_data import (
    DATASET,
    OUT,
    PROJECT,
    REPORT_MD,
    _check_rows,
    _read_json,
    _sources,
    _split_rows,
)

def build_artifact(
    meta: dict[str, Any], validation: dict[str, Any], receipt: dict[str, Any]
) -> dict[str, Any]:
    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )
    manifest = pd.read_csv(DATASET / "manifest.csv")
    weak = pd.read_csv(DATASET / "weak_or_review_manifest.csv")
    v1 = _read_json(PROJECT / "analysis/output/eth3m_short_pilot_v1_backtest/summary.json")
    strict = v1["replay"]["strict_oos"]
    totals = meta["totals"]
    split = meta["split_audit"]
    label_rows = [
        {
            "label": "short_start",
            "images": int((manifest["target"] == 1).sum()),
            "independent_events": totals["independent_positive_events"],
            "evidence": "固定 30 图的 owner 批量确认；当前 tip",
            "training": "是（诊断 pilot）",
        },
        {
            "label": "no_start",
            "images": int((manifest["target"] == 0).sum()),
            "independent_events": int(
                manifest.loc[manifest["target"] == 0, "event_id"].nunique()
            ),
            "evidence": "Label Studio Project 53 owner-no；当前 tip",
            "training": "是（诊断 pilot）",
        },
        {
            "label": "未定",
            "images": int(len(weak)),
            "independent_events": int(weak["positive_event_id"].dropna().nunique()),
            "evidence": "T-1/T+1/T+2/T+3/原 v10；没有逐时点人工结论",
            "training": "否；仅待复核",
        },
    ]
    contract_rows = [
        {
            "dimension": "训练问题",
            "v1": "在整图中检测固定右缘框",
            "v2": "判断当前 causal tip 是/不是 short_start",
        },
        {
            "dimension": "正例证据",
            "v1": "框几何 + owner 形态判断",
            "v2": "owner 看过固定 30 张未来辅助图后批量确认当前 T 来得及",
        },
        {
            "dimension": "相邻时点",
            "v1": "未建模",
            "v2": "全部无标签待复核；不从扫描门推导寿命",
        },
        {
            "dimension": "训练/验证隔离",
            "v1": "锚点分开但曾有 22-bar 输入重叠",
            "v2": f"事件成组；{split['anchor_embargo_bars']}-bar embargo（要求≥{split['required_anchor_embargo_bars']}）",
        },
        {
            "dimension": "连续盘口",
            "v1": f"严格 OOS raw fire {strict['raw_fire_rate']:.2%}",
            "v2": f"预封存 {totals['sealed_smoke_bars']:,} bars，无标签，只允许训练后测密度",
        },
    ]
    sources = _sources()
    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": "ETH 3m short-start pilot v2 数据集审计",
            "description": "只保留 owner 明确确认的当前-tip标签；相邻规则候选退出训练。",
            "generatedAt": generated,
            "cards": [
                {
                    "id": "training_images",
                    "description": "train + val 中有明确 owner 证据的图片总数。",
                    "dataset": "headline",
                    "sourceId": "v2_build",
                    "metrics": [
                        {"label": "训练图片", "field": "training_images", "format": "number"}
                    ],
                },
                {
                    "id": "positive_events",
                    "description": "正例按重叠 3h 区间归并后的独立事件数；不是图片数。",
                    "dataset": "headline",
                    "sourceId": "v2_build",
                    "metrics": [
                        {"label": "独立正事件", "field": "positive_events", "format": "number"}
                    ],
                },
                {
                    "id": "manual_negatives",
                    "description": "Project 53 中 owner 明确判不是的当前-tip样本。",
                    "dataset": "headline",
                    "sourceId": "v2_build",
                    "metrics": [
                        {"label": "人工负例", "field": "manual_negatives", "format": "number"}
                    ],
                },
                {
                    "id": "embargo",
                    "description": "train 最后锚点至 val 第一锚点；硬门覆盖 200-bar 输入 + 60-bar人工未来窗。",
                    "dataset": "headline",
                    "sourceId": "v2_build",
                    "metrics": [
                        {"label": "时间隔离", "field": "embargo_bars", "format": "number", "unit": " bars"}
                    ],
                },
            ],
            "charts": [
                {
                    "id": "label_evidence_chart",
                    "title": "明确标签与被隔离候选的数量",
                    "subtitle": "只有 owner 明确证据进入 train/val；150 条相邻/原时点保持无标签",
                    "showDescription": True,
                    "intent": "comparison",
                    "question": "本轮有多少样本拥有训练标签，多少候选因证据不足被隔离？",
                    "rationale": "三类数量并列能直接显示正例有效样本稀少，以及语义复核实际移出了多少规则标签。",
                    "comparisonContext": {
                        "grain": "渲染后的 causal-tip 图片",
                        "unit": "张",
                        "denominator": "本轮训练与待复核图片",
                    },
                    "type": "horizontalBar",
                    "dataset": "labels",
                    "sourceId": "v2_build",
                    "encodings": {
                        "x": {"field": "label", "type": "ordinal", "label": "证据角色"},
                        "y": {"field": "images", "type": "quantitative", "label": "图片数"},
                        "tooltip": [
                            {"field": "evidence", "type": "nominal", "label": "证据"},
                            {"field": "training", "type": "nominal", "label": "进入训练"},
                            {"field": "independent_events", "type": "quantitative", "label": "事件数"},
                        ],
                    },
                    "valueFormat": "number",
                    "unit": "张",
                    "layout": "full",
                    "labels": {"values": "all"},
                    "palette": {"kind": "categorical", "name": "category10"},
                    "settings": {"sort": "custom", "showValues": True},
                    "surface": {"surface": "card", "viewMode": "both"},
                }
            ],
            "tables": [
                {
                    "id": "split_table",
                    "title": "事件级时间切分",
                    "subtitle": "图片数不是独立样本数；正事件始终留在同一 split。",
                    "dataset": "split",
                    "sourceId": "v2_build",
                    "density": "spacious",
                    "columns": [
                        {"field": "split", "label": "Split", "type": "text"},
                        {"field": "images", "label": "图片", "format": "number"},
                        {"field": "short_start", "label": "是", "format": "number"},
                        {"field": "no_start", "label": "不是", "format": "number"},
                        {"field": "positive_events", "label": "独立正事件", "format": "number"},
                        {"field": "global_events", "label": "全局事件组", "format": "number"},
                    ],
                },
                {
                    "id": "label_table",
                    "title": "标签证据白名单",
                    "subtitle": "只有前两行进入 train/val；规则候选没有 target。",
                    "dataset": "labels",
                    "sourceId": "v2_build",
                    "density": "spacious",
                    "columns": [
                        {"field": "label", "label": "标签", "type": "text"},
                        {"field": "images", "label": "候选数", "format": "number"},
                        {"field": "independent_events", "label": "事件数", "format": "number"},
                        {"field": "evidence", "label": "证据", "type": "text"},
                        {"field": "training", "label": "进训练", "type": "text"},
                    ],
                },
                {
                    "id": "contract_table",
                    "title": "v1 → v2：这是目标重置，不是调参",
                    "subtitle": "v2 尚未训练，因此不能比较模型精度或收益。",
                    "dataset": "contract",
                    "sourceId": "v1_v2_contract",
                    "density": "spacious",
                    "columns": [
                        {"field": "dimension", "label": "维度", "type": "text"},
                        {"field": "v1", "label": "v1", "type": "text"},
                        {"field": "v2", "label": "v2", "type": "text"},
                    ],
                },
                {
                    "id": "validation_table",
                    "title": "独立验证器检查",
                    "subtitle": "这些门证明构建与隔离正确，不证明模型可盈利。",
                    "dataset": "checks",
                    "sourceId": "v2_validation",
                    "density": "compact",
                    "columns": [
                        {"field": "check", "label": "检查项", "type": "text"},
                        {"field": "result", "label": "结果", "type": "text"},
                    ],
                },
            ],
            "sources": sources,
            "blocks": [
                {"id": "title", "type": "markdown", "body": "# ETH 3m short-start pilot v2 数据集审计"},
                {
                    "id": "technical_summary",
                    "type": "markdown",
                    "body": "## Executive Summary\n\n**v2 数据集已按保守口径重构，但只定位为诊断 pilot。** 训练问题从‘画一个固定右缘框’改成‘当前因果图的最后一根是否是可接受的 ETH 3m 做空启动’。train/val 只允许两类明确证据：你批量确认来得及的固定 30 个当前 T，以及 Project 53 中你判‘不是’的 107 个当前 tip。相邻时点不再由程序猜标签。"
                },
                {"id": "headline", "type": "metric-strip", "cardIds": ["training_images", "positive_events", "manual_negatives", "embargo"]},
                {
                    "id": "why_reset",
                    "type": "markdown",
                    "sourceId": "v1_backtest",
                    "body": f"## Key finding: v1 的高静态 mAP 没有转成事件选择\n\nv1 在严格 OOS 的 {strict['eligible_bars']} 根连续盘口中开火 {strict['raw_fires']} 次，raw fire rate 为 {strict['raw_fire_rate']:.2%}。这证明固定右缘检测框和离散静态验证没有约束模型在连续盘口中稀疏开火。v2 因而重置监督目标，而不是继续调 conf。"
                },
                {"id": "contract_block", "type": "table", "tableId": "contract_table"},
                {
                    "id": "label_scope",
                    "type": "markdown",
                    "sourceId": "owner_receipt",
                    "body": f"## Scope and label definition\n\n`short_start` 只表示：在 200 根全部已收盘的 causal 图上，当前最右端 T 是你认可且‘来得及’的做空启动时点。30 张证据来自固定校准包的批量确认，原话为“{receipt['owner_exact_words']}”；回执绑定了 HTML、manifest 与每张图 SHA256，但它仍不是 30 条逐行 Label Studio 标注。`no_start` 来自 Project 53 的 owner-no 当前 tip。"
                },
                {"id": "label_table_block", "type": "table", "tableId": "label_table"},
                {"id": "label_chart_block", "type": "chart", "chartId": "label_evidence_chart", "layout": "full"},
                {
                    "id": "semantic_correction",
                    "type": "markdown",
                    "body": "## 已纠正的语义错误\n\n生产扫描允许查看 tip/tip-1/tip-2，是检测框离盘口的定位容差，不是信号寿命。初稿曾错误地把 T+1/T+2 自动标正、T+3 自动标负；反方复核后已全部移出 train/val。现在 T-1/T+1/T+2/T+3/原 v10 只存在于 `weak_or_review_manifest.csv`，没有 target。"
                },
                {"id": "split_block", "type": "table", "tableId": "split_table"},
                {
                    "id": "method",
                    "type": "markdown",
                    "sourceId": "v2_build",
                    "body": f"## Method\n\n每张图只渲染决策 T 及以前 {meta['causal_window_bars']} 根 ETH 3m K 线与六条均线；不载入 future outcome 列。train/val 按重叠人工未来区间合并为事件组并顺序切分。实际锚点 embargo 为 {split['anchor_embargo_bars']} bars，高于 200 根输入 + 60 根人工未来窗所需的 {split['required_anchor_embargo_bars']} bars。另封存 {totals['sealed_smoke_bars']:,} 根连续开发期盘口，只允许训练后测开火行为，绝不自动转成负例。"
                },
                {"id": "validation_block", "type": "table", "tableId": "validation_table"},
                {
                    "id": "external_reports",
                    "type": "markdown",
                    "body": "## 对你提供的两份研究报告怎么用\n\n本轮采用其中可直接审计的工程建议：图像统一、因果裁切、时间方向增强禁用、事件级时间切分。没有采用 ARIMA 合成、旋转框、改损失函数或报告中的高收益/高准确率数字，因为它们不能修复当前最核心的标签证据问题，且文中的 `[cite]` 还不是本仓库可复核的实验材料。"
                },
                {
                    "id": "metrics_na",
                    "type": "markdown",
                    "body": "## Performance metrics: not yet applicable\n\n本轮没有训练模型，所以 val AUC、事件精度、raw-fire/day、top-decile 净收益、置换 p 值和匹配随机对照均为 N/A。把数据集完整性检查当成模型验收会再次产生循环论证。"
                },
                {
                    "id": "limitations",
                    "type": "markdown",
                    "sourceId": "v2_build",
                    "body": f"## Limitations and robustness\n\n- 真正有效样本量是 {totals['independent_positive_events']} 个独立正事件，不是图片总数；只够做诊断性可学习性实验。\n- 正例均由 v10 owner-yes 池中的‘第一次收盘跌破六条 MA’提案产生，负例来自 v10 owner-no；模型可能学习来源/规则捷径。\n- 普通连续盘口没有人工 true-tip 标签；封存 smoke 只能测开火密度，不能自动当训练负例。\n- 数据集不是 formal gold，不能 promote，不能据此触碰 holdout 或 ACTIVE。"
                },
                {
                    "id": "incident",
                    "type": "markdown",
                    "body": "## Holdout incident\n\n本次 v2 构建只读取严格 pre-holdout 前缀。并行审计期间曾有助手误读另一个 1m 文件的表头与 3 行 2026-07-15 数据；这些行未用于任何统计或样本，但按‘看一眼就是消耗’已在 HANDOFF 保守登记为全局 holdout 第 12 次误耗。"
                },
                {
                    "id": "next",
                    "type": "markdown",
                    "body": "## Recommended next steps\n\n1. 先由 owner 决定是否启动**诊断性 classification pilot**；这不是正式模型训练授权。\n2. 开训前冻结三个不从结果倒推的门：连续盘口 raw-fire 密度、事件级人工精度、T 时点来得及率。\n3. 若 pilot 显示可学习，再单独扩充普通 true-tip 负例和‘形态对但已经太晚’负例；相邻时点必须逐图二选一或另获寿命规则授权。\n4. holdout 继续封存；训练、early stopping 和 smoke 诊断都只用开发期。"
                },
            ],
        },
        "snapshot": {
            "version": 1,
            "generatedAt": generated,
            "status": "ready",
            "datasets": {
                "headline": [
                    {
                        "training_images": totals["total"],
                        "positive_events": totals["independent_positive_events"],
                        "manual_negatives": int((manifest["target"] == 0).sum()),
                        "embargo_bars": split["anchor_embargo_bars"],
                    }
                ],
                "split": _split_rows(meta),
                "labels": label_rows,
                "contract": contract_rows,
                "checks": _check_rows(validation),
            },
        },
        "sources": sources,
    }
    return artifact


def build_markdown(
    meta: dict[str, Any], validation: dict[str, Any], receipt: dict[str, Any]
) -> str:
    totals = meta["totals"]
    split = meta["split_audit"]
    weak = pd.read_csv(DATASET / "weak_or_review_manifest.csv")
    rows = _split_rows(meta)
    split_lines = "\n".join(
        f"| {row['split']} | {row['images']} | {row['short_start']} | {row['no_start']} | {row['positive_events']} | {row['global_events']} |"
        for row in rows
    )
    failed: list[str] = [] if validation["status"] in {"pass", "passed"} else [
        "see validation.json"
    ]
    return f"""# ETH 3m short-start pilot v2 数据集审计

日期：2026-07-29

## 一句话结论

**v2 已按 owner 明确证据重构并通过独立结构验证，但只够做诊断 pilot。** train/val 共有
{totals['total']} 张：30 张 owner 批量确认的当前 T 正例、107 张 Label Studio owner-no 当前 tip
负例；正例按重叠 3h 区间只有 {totals['independent_positive_events']} 个独立事件。相邻时点规则候选
{len(weak)} 条全部无 target、退出训练。

## 为什么从框检测改成当前-tip二分类

- 用户实际问题是“现在是不是可入场的做空启动”，不是“整张图哪里存在某个对象”。
- v1 的 107 张 owner-no 中有 69 张历史区域含已知正形态；把它们写成 YOLO 整图空标签会产生
  矛盾监督。
- v1 严格 OOS 连续盘口 raw fire 99.74%，说明静态框 mAP 没有约束事件密度。
- 因此 v2 是有 owner 授权记录的目标重置，不是与 v1 可直接比较的单变量调参。

## 标签合同

| 训练角色 | 数量 | 证据 | 是否进 train/val |
|---|---:|---|---|
| `short_start` | 30 | 固定 calibration30 当前 T；owner 批量确认“{receipt['owner_exact_words']}” | 是 |
| `no_start` | 107 | Project 53 Label Studio owner-no 当前 tip | 是 |
| T-1/T+1/T+2/T+3/原 v10 | {len(weak)} | 没有逐时点人工结论 | 否；仅待复核 |

检测扫描的 `tip/tip-1/tip-2` 是框定位容差，**不是**信号寿命。v2 初稿曾把它误转成
T/T+1/T+2 正、T+3 负；反方复核后已纠正，不能训练那一版。

批量确认回执绑定 calibration manifest、移动端 HTML、30 张 review 图和 30 张 causal 图 SHA256；
它仍诚实标记为聊天整批确认，不冒充 30 条逐行 Label Studio 标注。

## 数据统计

| Split | 图片 | 是 | 不是 | 独立正事件 | 全局事件组 |
|---|---:|---:|---:|---:|---:|
{split_lines}

- 200 根 3m 因果输入，图像最后一根就是决策 T。
- train/val 按事件顺序切分；实际 embargo {split['anchor_embargo_bars']} bars，硬门
  {split['required_anchor_embargo_bars']} bars（200 输入 + 60 人工未来窗）。
- 连续开发期 smoke {totals['sealed_smoke_bars']:,} bars，保持无标签，绝不自动转负例。

## 独立验证

- 状态：`{validation['status']}`；失败项：{failed or '无'}。
- 图片文件、尺寸、class/path/target、SHA256、事件跨 split、输入因果窗、标签未来窗、holdout 边界、
  weak 标签为空和 owner receipt 哈希全部由独立验证器复算。
- 相关测试与复现命令见下节。

## 复现命令

```bash
MPLCONFIGDIR=/private/tmp/mpl-eth3m-v2 PYTHONPATH=. .venv/bin/python \\
  scripts/build_eth3m_short_pilot_dataset_v2.py --out datasets/eth_3m_short_pilot_v2
PYTHONPATH=. .venv/bin/python scripts/validate_eth3m_short_pilot_dataset_v2.py
PYTHONPATH=. .venv/bin/pytest -q \\
  tests/test_build_eth3m_short_pilot_dataset_v2.py \\
  tests/test_build_eth3m_short_pilot_dataset.py \\
  tests/test_build_eth3m_entry_timing_calibration30.py \\
  tests/test_analyze_eth3m_v10_yes_no_labels.py
PYTHONPATH=. .venv/bin/python scripts/build_eth3m_short_pilot_v2_dataset_report.py
```

## 你提供的两份研究报告如何进入本轮

采用了可直接审计的工程建议：因果裁切、统一渲染、禁用破坏时序的翻转/mosaic、事件级时间切分。
没有采用 ARIMA 合成、旋转框、改 IoU 损失或报告中的高准确率/高收益数字：这些建议不能修复当前
标签证据不足，而且 `[cite]` 还不是本仓库可复核的实验材料。

## 本阶段不适用指标

尚未训练，所以 val AUC、事件精度、raw-fire/day、top-decile 扣成本收益、置换 p、匹配随机对照
全部为 N/A。数据构建验证通过不等于模型验收通过。

## 风险与诚实声明

1. 有效正样本只有 {totals['independent_positive_events']} 个独立事件，样本非常少，只能回答
   “有没有可学习信号”，不能宣称模型成熟。
2. 正例来自 v10 owner-yes 后的第一次六 MA 下破提案，负例来自 v10 owner-no，存在来源/规则捷径；
   后续必须用连续 smoke 与简单规则基线揭穿捷径。
3. 普通连续盘口还没有人工 true-tip 负例；smoke 无标签，因此只能测密度，不能算精度或进训练。
4. v2 不是 formal gold，不可 promote，不可切 ACTIVE，不可据此触碰 holdout。
5. 并行审计助手曾误读另一个 1m 文件的表头和 3 行 2026-07-15 holdout 数据；未用于本数据集，
   但已按纪律保守登记为全局 holdout 第 12 次误耗。

## 下一步（需 owner 决策）

是否启动**诊断性 classification pilot**。若启动，训练前先冻结连续盘口 raw-fire 密度、事件级人工
精度、T 时点来得及率三个验收门；不允许训练后看结果再倒推阈值。正式扩集则需要新标普通 true-tip
负例和“形态正确但已经太晚”的负例。
"""
