"""Data loaders and tabular source helpers for the ETH 3m v2a report."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[2]
DATASET = PROJECT / "datasets/eth_3m_short_pilot_v2"
OUT = PROJECT / "analysis/output/eth3m_short_pilot_v2_dataset"
REPORT_MD = PROJECT / "analysis/p_eth_3m_short_pilot_v2_dataset.md"

def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _split_rows(meta: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for split in ("train", "val"):
        item = meta["counts"][split]
        rows.append(
            {
                "split": split,
                "images": item["total"],
                "short_start": item["short_start"],
                "no_start": item["no_start"],
                "global_events": item["global_events"],
                "positive_events": item["positive_events"],
            }
        )
    return rows


def _check_rows(validation: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "check": key,
            "result": "PASS" if (value is True or value == 0) else str(value),
        }
        for key, value in validation["checks"].items()
    ]


def _sources() -> list[dict[str, Any]]:
    return [
        {
            "id": "v2_build",
            "label": "ETH 3m v2 构建元数据与 manifest",
            "path": "datasets/eth_3m_short_pilot_v2/build_meta.json",
            "query": {
                "engine": "artifact",
                "language": "json/csv",
                "description": "由冻结的 owner 证据构建当前 causal tip 二分类图片，并记录事件、切分与标签来源。",
                "sql": "WITH train_labels AS (\n  SELECT split, class_name, target, event_id, positive_event_id, sample_kind\n  FROM read_csv_auto('datasets/eth_3m_short_pilot_v2/manifest.csv', header=true)\n), weak AS (\n  SELECT COUNT(*) AS weak_rows\n  FROM read_csv_auto('datasets/eth_3m_short_pilot_v2/weak_or_review_manifest.csv', header=true)\n)\nSELECT split, class_name, COUNT(*) AS images, COUNT(DISTINCT event_id) AS global_events,\n       COUNT(DISTINCT CASE WHEN target = 1 THEN positive_event_id END) AS positive_events,\n       (SELECT weak_rows FROM weak) AS weak_rows\nFROM train_labels\nGROUP BY split, class_name\nORDER BY split, class_name;",
                "tables_used": [
                    "datasets/eth_3m_short_pilot_v2/build_meta.json",
                    "datasets/eth_3m_short_pilot_v2/manifest.csv",
                    "datasets/eth_3m_short_pilot_v2/event_manifest.csv",
                    "datasets/eth_3m_short_pilot_v2/weak_or_review_manifest.csv",
                ],
                "filters": [
                    "manifest.csv contains only confirmed_current_tip and owner_no_tip_negative",
                    "weak_or_review rows have blank target and are excluded from train/val",
                ],
                "metric_definitions": [
                    "training images = all rows in manifest.csv",
                    "independent positive events = distinct nonblank positive_event_id among target=1",
                    "manual negatives = rows whose sample_kind is owner_no_tip_negative",
                    "embargo bars comes from build_meta.split_audit and is independently recomputed by validation",
                ],
            },
        },
        {
            "id": "v2_validation",
            "label": "独立数据集验证回执",
            "path": "analysis/output/eth3m_short_pilot_v2_dataset/validation.json",
            "query": {
                "engine": "source-code",
                "language": "python",
                "description": "独立检查文件、标签白名单、哈希、因果窗、事件切分、embargo、receipt 与 holdout 边界。",
                "sql": "SELECT * FROM read_json_auto('analysis/output/eth3m_short_pilot_v2_dataset/validation.json');",
                "tables_used": [
                    "scripts/validate_eth3m_short_pilot_dataset_v2.py",
                    "analysis/output/eth3m_short_pilot_v2_dataset/validation.json",
                ],
                "filters": ["validation.status = 'passed' before report generation"],
                "metric_definitions": ["boolean checks must be true; error-count checks must equal zero"],
            },
        },
        {
            "id": "owner_receipt",
            "label": "30 张时机图批量确认回执",
            "path": "datasets/eth_3m_short_pilot_v2/owner_confirmation_receipt.json",
            "query": {
                "engine": "artifact",
                "language": "json",
                "description": "绑定固定 calibration manifest、移动端 HTML、30 张 review/causal 图片 SHA256 与 owner 原话；明确不是逐行 Label Studio 标注。",
                "sql": "SELECT confirmation_scope, not_row_level_label_studio, owner_exact_words, confirmed_current_tip_image_count\nFROM read_json_auto('datasets/eth_3m_short_pilot_v2/owner_confirmation_receipt.json');",
                "tables_used": [
                    "datasets/eth_3m_short_pilot_v2/owner_confirmation_receipt.json"
                ],
                "filters": ["confirmed_current_tip_image_count = 30", "all referenced SHA256 values revalidated"],
                "metric_definitions": ["owner evidence is one batch chat confirmation bound to the fixed 30-image pack"],
            },
        },
        {
            "id": "v1_backtest",
            "label": "ETH 3m pilot v1 因果回放失败结果",
            "path": "analysis/output/eth3m_short_pilot_v1_backtest/summary.json",
            "query": {
                "engine": "artifact",
                "language": "json",
                "description": "v1 严格 OOS 连续回放，用于说明为何必须重置标签目标；不是 v2 性能结果。",
                "sql": "SELECT replay.strict_oos.eligible_bars AS eligible_bars,\n       replay.strict_oos.raw_fires AS raw_fires,\n       replay.strict_oos.raw_fire_rate AS raw_fire_rate\nFROM read_json_auto('analysis/output/eth3m_short_pilot_v1_backtest/summary.json');",
                "tables_used": [
                    "analysis/output/eth3m_short_pilot_v1_backtest/summary.json"
                ],
                "filters": ["strict_oos only"],
                "metric_definitions": ["raw fire rate = raw fires / eligible causal replay bars"],
            },
        },
        {
            "id": "v1_v2_contract",
            "label": "v1 回放与 v2 数据合同对照",
            "path": "analysis/p_eth_3m_short_pilot_v2_dataset.md",
            "query": {
                "engine": "duckdb",
                "language": "sql",
                "description": "把 v1 严格 OOS 行为与 v2 的标签、切分和 smoke 元数据放到同一审计行；文本维度由该证据解释。",
                "sql": "WITH v1 AS (\n  SELECT replay.strict_oos.raw_fire_rate AS v1_raw_fire_rate\n  FROM read_json_auto('analysis/output/eth3m_short_pilot_v1_backtest/summary.json')\n), v2 AS (\n  SELECT totals.total AS v2_images, totals.independent_positive_events AS v2_positive_events,\n         totals.sealed_smoke_bars AS v2_smoke_bars, split_audit.anchor_embargo_bars AS v2_embargo_bars\n  FROM read_json_auto('datasets/eth_3m_short_pilot_v2/build_meta.json')\n)\nSELECT * FROM v1 CROSS JOIN v2;",
                "tables_used": [
                    "analysis/output/eth3m_short_pilot_v1_backtest/summary.json",
                    "datasets/eth_3m_short_pilot_v2/build_meta.json",
                ],
                "filters": ["v1 strict_oos only", "v2 current frozen build"],
                "metric_definitions": ["v1 and v2 are contract comparisons, not model-performance comparisons"],
            },
        },
    ]
