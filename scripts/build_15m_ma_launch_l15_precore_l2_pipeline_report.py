#!/usr/bin/env python3
"""Build the causal pre-core L1.5 plus side-split L2 audit report.

This is a deterministic downstream builder. It reads only frozen pre-holdout
receipts and their declared artifacts. It does not train, score, tune, read
holdout, promote, deploy, mutate forward state, send Telegram, or trade.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l15-precore-global-shape-l2-side-split-v2"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l15_precore_l2_pipeline_v2"
REPORT_PATH = ROOT / "analysis" / "p3_15m_ma_launch_l15_precore_l2_pipeline_20260901.md"
HTML_PATH = ROOT / "analysis" / "html" / f"{REPORT_PATH.stem}.html"
MARKDOWN_HTML_PATH = ROOT / "analysis" / "html" / f"{REPORT_PATH.stem}_markdown.html"
GALLERY_PATH = ROOT / "analysis" / "html" / (
    "p3_15m_ma_launch_l15_precore_l2_pipeline_gallery_20260901.html"
)
ARTIFACT_PATH = OUTPUT_DIR / "report_artifact.json"
NOTES_PATH = OUTPUT_DIR / "report_notes.json"
SOURCE_DB_PATH = OUTPUT_DIR / "report_evidence.sqlite"
METRIC_CHART_PATH = OUTPUT_DIR / "factorial_economic_comparison.png"
RECEIPT_PATH = RESULTS_DIR / "report_receipt.json"
V1_FAILURE_PATH = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-l15-global-shape-l2-side-split-v1"
    / "results"
    / "failure_analysis.json"
)

SOURCE_SQL = """SELECT 'headline' AS dataset,
       json_object('short_fpr', short_fpr, 'l2_net', l2_net,
                   'pipeline_n', pipeline_n) AS row_json
FROM headline
UNION ALL
SELECT 'economic' AS dataset,
       json_object('arm_order', arm_order, 'arm', arm,
                   'selected_n', selected_n, 'net_mean_bp', net_mean_bp,
                   'permutation_p', permutation_p,
                   'matched_excess_bp', matched_excess_bp, 'gate', gate) AS row_json
FROM economic
UNION ALL
SELECT 'sides' AS dataset,
       json_object('row_order', row_order, 'arm', arm, 'side', side,
                   'selected_n', selected_n, 'net_mean_bp', net_mean_bp,
                   'top_decile_bp', top_decile_bp, 'roc_auc', roc_auc,
                   'permutation_p', permutation_p) AS row_json
FROM sides
UNION ALL
SELECT 'classifiers' AS dataset,
       json_object('side_order', side_order, 'side', side,
                   'final_n', final_n, 'roc_auc', roc_auc,
                   'baseline_auc', baseline_auc, 'precision', precision,
                   'recall', recall, 'false_positive_rate', false_positive_rate,
                   'fpr_limit', fpr_limit,
                   'label_permutation_p', label_permutation_p, 'gate', gate) AS row_json
FROM classifiers
ORDER BY dataset, row_json;"""


class ReportError(RuntimeError):
    """Raised when report evidence or lineage is incomplete."""


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReportError(f"missing JSON evidence: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_path(value: object) -> Path:
    path = (ROOT / str(value).replace("\\", "/")).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ReportError(f"path escapes repository: {value}") from exc
    return path


def repo_relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def require_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise ReportError(f"missing {label}: {path}")
    observed = sha256_file(path)
    if observed != str(expected):
        raise ReportError(f"{label} hash drifted: {observed} != {expected}")


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def bp(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{10_000 * float(value):+.{digits}f} bp"


def pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{100 * float(value):.{digits}f}%"


def validate_evidence() -> dict[str, Any]:
    prereg = read_json(EXPERIMENT_DIR / "preregistration.json")
    receipts = {
        name: read_json(RESULTS_DIR / name)
        for name in (
            "l15_dataset_receipt.json",
            "l15_training_receipt.json",
            "candidate_l15_receipt.json",
            "l2_training_receipt.json",
            "render_receipt.json",
            "verify_receipt.json",
        )
    }
    v1_failure = read_json(V1_FAILURE_PATH)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise ReportError("preregistration experiment identity drifted")
    if any(item.get("experiment_id") != EXPERIMENT_ID for item in receipts.values()):
        raise ReportError("one or more receipts have the wrong experiment identity")
    verify = receipts["verify_receipt.json"]
    if verify.get("passed") is not True or not all(verify.get("checks", {}).values()):
        raise ReportError("verification receipt is not fully green")
    l15 = receipts["l15_training_receipt.json"]
    l2 = receipts["l2_training_receipt.json"]
    if l15.get("gate_passed") is not False:
        raise ReportError("builder is frozen to the observed L1.5 gate failure")
    if any(arm["gate"].get("passed") is not False for arm in l2["arms"].values()):
        raise ReportError("builder is frozen to the observed L2 arm failures")
    if any(item.get("holdout_consumed") is not False for item in receipts.values()):
        raise ReportError("receipt crossed the holdout boundary")
    dataset_receipt = receipts["l15_dataset_receipt.json"]
    candidate_receipt = receipts["candidate_l15_receipt.json"]
    render_receipt = receipts["render_receipt.json"]
    require_hash(
        repo_path(dataset_receipt["dataset_path"]),
        dataset_receipt["dataset_sha256"],
        "L1.5 dataset",
    )
    require_hash(
        repo_path(candidate_receipt["path"]),
        candidate_receipt["sha256"],
        "candidate scores",
    )
    require_hash(
        repo_path(l2["pipeline_scored_path"]),
        l2["pipeline_scored_sha256"],
        "pipeline validation scores",
    )
    require_hash(
        repo_path(render_receipt["manifest_path"]),
        render_receipt["manifest_sha256"],
        "review manifest",
    )
    require_hash(
        repo_path(render_receipt["overview_path"]),
        render_receipt["overview_sha256"],
        "review overview",
    )
    for side, arm in l15["arms"].items():
        require_hash(repo_path(arm["model_path"]), arm["model_sha256"], f"L1.5 {side}")
        require_hash(
            repo_path(arm["baseline_path"]), arm["baseline_sha256"], f"L1.5 baseline {side}"
        )
    for arm_name, arm in l2["arms"].items():
        for side, model in arm["models"].items():
            require_hash(
                repo_path(model["model_path"]), model["model_sha256"], f"{arm_name} {side}"
            )
    return {"prereg": prereg, "receipts": receipts, "v1_failure": v1_failure}


def economic_rows(l2: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "arm_order": 1,
            "arm": "L1 候选池",
            "selected_n": int(l2["l1_only"]["rank"]["n"]),
            "net_mean_bp": 10_000 * float(l2["l1_only"]["rank"]["pool_net_mean"]),
            "permutation_p": None,
            "matched_excess_bp": None,
            "gate": "基线",
        },
        {
            "arm_order": 2,
            "arm": "L1 + L1.5",
            "selected_n": int(l2["l15_only"]["frozen_filter"]["n"]),
            "net_mean_bp": 10_000 * float(l2["l15_only"]["frozen_filter"]["net_mean"]),
            "permutation_p": None,
            "matched_excess_bp": 10_000
            * float(l2["l15_only"]["matched_control"]["mean_event_minus_control"]),
            "gate": "FAIL",
        },
        {
            "arm_order": 3,
            "arm": "L1 + L2 q90",
            "selected_n": int(l2["arms"]["l2_only"]["metrics"]["frozen_q90"]["n"]),
            "net_mean_bp": 10_000
            * float(l2["arms"]["l2_only"]["metrics"]["frozen_q90"]["net_mean"]),
            "permutation_p": float(l2["arms"]["l2_only"]["metrics"]["permutation_p"]),
            "matched_excess_bp": 10_000
            * float(
                l2["arms"]["l2_only"]["metrics"]["matched_control"][
                    "mean_event_minus_control"
                ]
            ),
            "gate": "FAIL",
        },
        {
            "arm_order": 4,
            "arm": "L1 + L1.5 + L2 q90",
            "selected_n": int(l2["arms"]["l15_l2"]["metrics"]["frozen_q90"]["n"]),
            "net_mean_bp": 10_000
            * float(l2["arms"]["l15_l2"]["metrics"]["frozen_q90"]["net_mean"]),
            "permutation_p": float(l2["arms"]["l15_l2"]["metrics"]["permutation_p"]),
            "matched_excess_bp": 10_000
            * float(
                l2["arms"]["l15_l2"]["metrics"]["matched_control"][
                    "mean_event_minus_control"
                ]
            ),
            "gate": "FAIL",
        },
    ]


def side_rows(l2: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for arm_name, label in (("l2_only", "L2 q90"), ("l15_l2", "L1.5 + L2 q90")):
        for side in ("long", "short"):
            metrics = l2["arms"][arm_name]["metrics"]["by_side"][side]
            rows.append(
                {
                    "row_order": len(rows) + 1,
                    "arm": label,
                    "side": side.upper(),
                    "selected_n": int(metrics["frozen_q90"]["n"]),
                    "net_mean_bp": 10_000 * float(metrics["frozen_q90"]["net_mean"]),
                    "top_decile_bp": 10_000 * float(metrics["rank"]["top_decile"]["net_mean"]),
                    "roc_auc": float(metrics["rank"]["roc_auc"]),
                    "permutation_p": float(metrics["permutation_p"]),
                }
            )
    return rows


def l15_rows(l15: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order, side in enumerate(("long", "short"), 1):
        arm = l15["arms"][side]
        metric = arm["final_metrics"]
        rows.append(
            {
                "side_order": order,
                "side": side.upper(),
                "final_n": int(metric["n"]),
                "roc_auc": float(metric["roc_auc"]),
                "baseline_auc": float(arm["single_spread_baseline"]["roc_auc"]),
                "precision": float(metric["precision"]),
                "recall": float(metric["recall"]),
                "false_positive_rate": float(metric["false_positive_rate"]),
                "fpr_limit": 0.12,
                "label_permutation_p": float(metric["permutation_p"]),
                "gate": "PASS" if arm["gate"]["passed"] else "FAIL",
            }
        )
    return rows


def make_metric_chart(
    economic: list[dict[str, Any]], sides: list[dict[str, Any]], output: Path
) -> dict[str, Any]:
    """Render signed categorical bars with explicit zero references."""

    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial Unicode MS", "Arial", "DejaVu Sans"],
            "axes.edgecolor": "#343A40",
            "axes.labelcolor": "#343A40",
            "xtick.color": "#343A40",
            "ytick.color": "#343A40",
        }
    )
    figure, axes = plt.subplots(1, 2, figsize=(16, 7.5), dpi=120)
    panels = (
        (
            axes[0],
            [row["arm"] for row in economic],
            np.array([row["net_mean_bp"] for row in economic]),
            ["#B8BEC5", "#D8892B", "#2F6B9A", "#6C7A3D"],
            "Four-arm selected-set net return",
        ),
        (
            axes[1],
            [f"{row['arm']}\n{row['side']}" for row in sides],
            np.array([row["net_mean_bp"] for row in sides]),
            ["#D8892B" if row["side"] == "LONG" else "#2F6B9A" for row in sides],
            "Frozen q90 net return by direction",
        ),
    )
    for axis, labels, values, colors, title in panels:
        positions = np.arange(len(labels))
        bars = axis.bar(positions, values, color=colors, edgecolor="#343A40", linewidth=0.8)
        axis.axhline(0, color="#343A40", linewidth=1.2)
        axis.set_xticks(positions, labels, fontsize=9)
        axis.set_ylabel("Mean net return (bp), after 20 bp round trip")
        axis.set_title(title, loc="left", fontsize=13, fontweight="bold")
        axis.grid(axis="y", color="#D9DEE3", linewidth=0.7, alpha=0.8)
        axis.set_axisbelow(True)
        pad = max(7.0, float(np.ptp(values)) * 0.06)
        axis.set_ylim(min(0.0, float(values.min())) - 1.7 * pad, max(0.0, float(values.max())) + 1.7 * pad)
        for bar, value in zip(bars, values):
            axis.text(
                bar.get_x() + bar.get_width() / 2,
                value + (pad if value >= 0 else -pad),
                f"{value:+.1f}",
                ha="center",
                va="bottom" if value >= 0 else "top",
                fontsize=10,
                fontweight="bold",
                color="#202428",
            )
    figure.suptitle(
        "15m MA launch: causal L1.5 + side-split L2 factorial",
        x=0.04,
        ha="left",
        fontsize=17,
        fontweight="bold",
        color="#202428",
    )
    figure.text(
        0.04,
        0.02,
        "Pre-holdout retrospective development period; fixed TP5/SL2/72 and 20 bp cost. "
        "Positive bars do not override sample-size, side or permutation gates.",
        fontsize=10,
        color="#50565C",
    )
    figure.tight_layout(rect=(0.03, 0.07, 0.99, 0.91))
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, facecolor="white", bbox_inches="tight")
    plt.close(figure)
    image = cv2.imread(str(output), cv2.IMREAD_COLOR)
    if image is None:
        raise ReportError("metric chart could not be decoded")
    return {
        "path": repo_relative(output),
        "sha256": sha256_file(output),
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
    }


def make_gallery(manifest: Mapping[str, Any], output: Path) -> dict[str, Any]:
    cards: list[str] = []
    for record in manifest["records"]:
        image_path = repo_path(record["path"])
        require_hash(image_path, record["sha256"], "gallery review image")
        image_rel = Path(os.path.relpath(image_path, output.parent)).as_posix()
        state = "PIPELINE KEEP" if record["state"] == "keep" else "L1.5 REJECT"
        cards.append(
            "<article class='card'>"
            f"<a href='{html.escape(image_rel)}'><img loading='lazy' src='{html.escape(image_rel)}' "
            f"alt='{html.escape(str(record['episode_id']))}'></a>"
            f"<h2>{int(record['order']):02d} · {state}</h2>"
            f"<p>{html.escape(str(record['episode_id']))}<br>点击图片打开 1920×1250 高清原图。</p>"
            "</article>"
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>因果 L1.5 + L2：38 张全局审核图</title>
<style>
:root{{color-scheme:light dark}}body{{font:15px/1.5 system-ui,-apple-system,"PingFang SC",sans-serif;margin:0;background:#101214;color:#eef1f3}}
header{{position:sticky;top:0;z-index:2;padding:14px 20px;background:#15191ded;border-bottom:1px solid #3a4148}}
main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(560px,1fr));gap:14px;padding:14px}}
.card{{background:#191e22;border:1px solid #3a4148;border-radius:9px;overflow:hidden}}
img{{display:block;width:100%;height:auto;background:white}}h2{{font-size:16px;margin:10px 12px 4px}}p{{margin:4px 12px 13px;color:#bbc3ca;overflow-wrap:anywhere}}
@media(max-width:620px){{main{{grid-template-columns:1fr;padding:8px}}}}
</style></head><body><header><strong>因果 L1.5 + L2：38 张全局审核图</strong> · 128 根已收盘 K · 冻结 L1 原框 · 无未来结果</header>
<main>{''.join(cards)}</main></body></html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return {"path": repo_relative(output), "sha256": sha256_file(output), "images": len(cards)}


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    header = "| " + " | ".join(label for _, label in columns) + " |"
    divider = "|" + "|".join("---" for _ in columns) + "|"
    body = ["| " + " | ".join(str(row[key]) for key, _ in columns) + " |" for row in rows]
    return "\n".join([header, divider, *body])


def build_markdown(
    evidence: Mapping[str, Any],
    economic: list[dict[str, Any]],
    sides: list[dict[str, Any]],
    classifiers: list[dict[str, Any]],
    metric_chart: Mapping[str, Any],
    gallery: Mapping[str, Any],
) -> str:
    prereg = evidence["prereg"]
    receipts = evidence["receipts"]
    dataset = receipts["l15_dataset_receipt.json"]
    l15 = receipts["l15_training_receipt.json"]
    candidate = receipts["candidate_l15_receipt.json"]
    l2 = receipts["l2_training_receipt.json"]
    render = receipts["render_receipt.json"]
    verify = receipts["verify_receipt.json"]
    l2_only = l2["arms"]["l2_only"]["metrics"]
    pipeline = l2["arms"]["l15_l2"]["metrics"]
    chart_rel = Path(metric_chart["path"]).relative_to("analysis").as_posix()
    overview_rel = Path(render["overview_path"]).relative_to("analysis").as_posix()
    econ_md = []
    for row in economic:
        econ_md.append(
            {
                "arm": row["arm"],
                "n": row["selected_n"],
                "net": f"{row['net_mean_bp']:+.1f} bp",
                "p": "—" if row["permutation_p"] is None else f"{row['permutation_p']:.4f}",
                "control": "—" if row["matched_excess_bp"] is None else f"{row['matched_excess_bp']:+.1f} bp",
                "gate": row["gate"],
            }
        )
    side_md = [
        {
            "arm": row["arm"],
            "side": row["side"],
            "n": row["selected_n"],
            "net": f"{row['net_mean_bp']:+.1f} bp",
            "top": f"{row['top_decile_bp']:+.1f} bp",
            "auc": f"{row['roc_auc']:.4f}",
            "p": f"{row['permutation_p']:.4f}",
        }
        for row in sides
    ]
    class_md = [
        {
            "side": row["side"],
            "n": row["final_n"],
            "auc": f"{row['roc_auc']:.4f}",
            "baseline": f"{row['baseline_auc']:.4f}",
            "precision": pct(row["precision"]),
            "recall": pct(row["recall"]),
            "fpr": pct(row["false_positive_rate"]),
            "gate": row["gate"],
        }
        for row in classifiers
    ]
    return f"""# 15m 均线密集启动：因果 L1.5 + 多空 L2 全链路审计（2026-09-01）

## 技术结论：整条链路未通过，不能开启

本轮把用户要求的路径完整做成四组冻结对照：原 L1、L1+全局形态 L1.5、L1+多空分开收益 L2、
L1+L1.5+L2。最终结论是 **全部不可上线**。L1.5 的 LONG 分类通过，但 SHORT 的最终误报率
{pct(l15['arms']['short']['final_metrics']['false_positive_rate'])} 超过预注册 {pct(0.12)} 上限；完整
L1.5+L2 只选出 {pipeline['frozen_q90']['n']} 个独立事件，少于最少 30 个，置换
`p={pipeline['permutation_p']:.4f}`，而且 SHORT 净收益为
{bp(pipeline['by_side']['short']['frozen_q90']['net_mean'])}。

最重要的诊断是：**全局形态分类和未来收益判断不是一回事**。L1.5 能很好地复现现有自动标签，并不
代表这些标签就是 Owner 眼里的完美全局形态；即使局部图看起来标准，也不自动拥有稳定的 TP5/SL2/72
收益排序。

## 四组对照：加层并没有稳定提高收益

左图按四个实际输出集合比较扣 20 bp 后净均值；右图拆开 L2 的 LONG/SHORT。正柱不等于通过：
样本数、两边方向和置换检验必须同时过门。

![四组链路收益对照]({chart_rel})

{markdown_table(econ_md, [('arm', '配置'), ('n', '入选独立事件'), ('net', '净均值'), ('p', '置换 p'), ('control', '减匹配对照'), ('gate', '裁决')])}

只加 L1.5 后，原 L1 池的净均值从 {bp(l2['l1_only']['rank']['pool_net_mean'])} 变成
{bp(l2['l15_only']['frozen_filter']['net_mean'])}，说明当前全局弱标签过滤器没有保住经济价值。
只加 L2 的 34 个事件看似有 {bp(l2_only['frozen_q90']['net_mean'])}，也完整超过 8 组匹配随机对照，
但 `p={l2_only['permutation_p']:.4f}` 且 LONG 为负，因此仍是未确认的探索性结果。

## 多空必须分开看：正负方向会互相掩盖

{markdown_table(side_md, [('arm', '配置'), ('side', '方向'), ('n', 'q90 n'), ('net', 'q90净均值'), ('top', 'top-decile净均值'), ('auc', '收益正负AUC'), ('p', '置换p')])}

L2-only 的 SHORT 为正、LONG 为负；加上 L1.5 后恰好反过来。这个翻转说明样本选择不稳定，不能在看完
结果后临时宣布“只做空”或“只做多”。若要验证某一方向，必须新预注册并等待新的未见时期。

## L1.5 看起来很准，但它学的是协议弱标签，不是 Owner 全局金标

{markdown_table(class_md, [('side', '方向'), ('n', 'final n'), ('auc', 'L1.5 AUC'), ('baseline', '单一密集度AUC'), ('precision', '精确率'), ('recall', '召回率'), ('fpr', '误报率'), ('gate', '裁决')])}

L1.5 使用 128 根历史，但输入右端严格停在每个样本的 `core_end`：启动确认段可见 K 线为 **0**。
训练单位是一事件一行，3129 个独立事件中 1043 个自动 Grade-A 正例、2086 个同形态 hard negatives；
LONG/SHORT 独立建模。它确实显著优于“只看当前均线宽度”的单特征基线，但标签来自原自动协议，
未经过 Owner 对“全局是否完美”的逐样本确认，因此高 AUC 只能证明模型能复现这套协议。

## 为什么第一版 AUC=1.0 被作废

第一版让 L1.5 看到了核心框之后的确认 K，同时标签本身也用这段启动进度区分正负；单独一个
`aligned_core_to_decision_atr` 就能把 LONG/SHORT 全部分开，AUC 都是 1.0。这是标签构造捷径，
不是全局形态能力。发现后没有继续拿它筛候选或训练 L2，而是先冻结失败证据，再预注册 v2：
物理截断全部 post-core K，并删除两个确认段字段。这个修正解释了为何 v2 的指标回到可信范围。

## 38 张实际全局图：像素一致，但肉眼也能看到边界不稳定

下面的联系表来自模型实际候选，展示 128 根已收盘 K 和冻结 L1 原框，不画未来结果。38/38 张高清图
全部通过输入哈希与坐标重投影检查，像素一致失败为 0。

![L1.5 保留与拒绝全局图总览]({overview_rel})

逐张高清查看：[{gallery['images']} 张全局审核图]({Path(gallery['path']).name})。

总览同时说明当前弱标签的局限：部分保留图的框已经包含明显释放，部分被拒绝图肉眼仍像有效启动。
这不是渲染偏移，而是“自动 Grade-A/自动 hard negative”没有等价于 Owner 的全局好坏判断。

## 数据、时间与模型合同

| 项目 | 冻结值 |
|---|---:|
| L1.5 独立事件 | {dataset['events']:,}（正 {prereg['l15']['positive_events']:,} / hard负 {prereg['l15']['hard_negative_events']:,}） |
| L1.5 train / tune / final | 2,050 / 614 / 465 |
| L1.5 上下文 | 128 根，右端=core_end，post-core=0 |
| L1.5 特征 | {len(dataset['feature_columns'])} 个因果全局形态特征 |
| 候选账本 | {candidate['rows']:,} 行；最终独立事件 242 |
| L2 特征 | {len(l2['feature_columns'])} 个预先选定的因果特征，多空分开 |
| 结果标签 | next-open；TP {prereg['outcome']['tp_atr_multiple']} ATR / SL {prereg['outcome']['sl_atr_multiple']} ATR / {prereg['outcome']['horizon_bars']} 根 |
| 往返成本 | {pct(prereg['outcome']['round_trip_cost_fraction'])} |
| 最大暴露末端 | {candidate['max_exposure_end_exclusive']} |
| holdout 起点 | {prereg['inputs']['holdout_start']} |
| holdout 消耗 | 0 |
| 渲染像素一致失败 | {render['pixel_parity_failures']} |
| 机械验证 | {sum(bool(value) for value in verify['checks'].values())}/{len(verify['checks'])} PASS |

所有切分按时间完成，没有随机切分；同一依赖事件只贡献一个 representative。收益评估继续使用原冻结
TP5/SL2/72、next-open、同根先判 SL、20 bp 成本和 8 组同币同时间块同波动桶随机对照。

## 风险与诚实声明

- 本次没有读取 `>=2026-05-04` holdout，没有 promote、部署、修改 ACTIVE/frozen、写 forward、发 Telegram 或下单。
- 经济 final 已在此前实验中使用过，因此这只是回顾式开发诊断，不是新的确认集。
- L1.5 的标签仍是协议弱标签；没有 Owner 全局金标时，不能宣称它真正懂“完美形态”。
- L2-only SHORT 的正结果是看完 final 后发现的，不得原地改成 SHORT-only 成功版本。
- 完整链路只有 18 个入选事件，任何漂亮均值都非常脆弱。
- 匹配随机对照只控制已定义的同币、时间块与波动桶，不能替代新时期前向确认。

## 正确路径：下一轮只解决标签，不再继续堆模型

1. **冻结本轮为失败基线。** 不调现有阈值，不把某个正方向单独挑出来上线。
2. **先建立真正的全局形态监督。** 从 L1.5 高分保留、低分拒绝和两者分歧区各抽样，直接标
   `global_shape_good`；LONG/SHORT 分开。若不做这一步，后续只能继续拟合自动协议。
3. **L1.5 只做形态，不看未来收益。** 仍严格截止 core_end；正例是 Owner 认可的全局图，hard negative
   是“局部框像，但全局不对”的图。
4. **L2 继续预测实际收益。** 只在 L1.5 合格候选上训练，LONG/SHORT 分开，并用全新未见时期验收。
5. **只有形态门和经济门都通过，才申请一次 holdout。** 本轮没有资格消耗 holdout。

## 复现命令

```bash
PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l15_precore_l2_pipeline --build-l15-dataset
PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l15_precore_l2_pipeline --train-l15
PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l15_precore_l2_pipeline --apply-l15
PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l15_precore_l2_pipeline --train-l2
PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l15_precore_l2_pipeline --render
PYTHONPATH=. .venv/bin/python -m scripts.research_15m_ma_launch_l15_precore_l2_pipeline --verify
PYTHONPATH=. .venv/bin/python scripts/build_15m_ma_launch_l15_precore_l2_pipeline_report.py
PYTHONPATH=. .venv/bin/python scripts/md_to_html.py analysis/p3_15m_ma_launch_l15_precore_l2_pipeline_20260901.md --out-dir analysis/html
```

## 仍需回答的问题

- Owner 是否愿意把“全局形态好/不好”作为独立监督问题确认一小批校准样本？如果明确不要任何人工标签，
  下一轮只能做无监督聚类/分歧检索，不能诚实称为 Owner 完美形态分类器。
- SHORT 的 L2-only 正值能否在全新 pre-holdout 或获批 holdout 上复现？当前 final 已被观察，不能再作答案。
"""


def source_spec(generated_at: str) -> dict[str, Any]:
    return {
        "id": "pipeline_receipts",
        "label": "Causal pre-core L1.5 and side-split L2 frozen receipts",
        "path": repo_relative(SOURCE_DB_PATH),
        "query": {
            "engine": "sqlite",
            "language": "sql",
            "executed_at": generated_at,
            "description": "Reads the deterministic SQLite snapshot built from the frozen preregistration and terminal receipts.",
            "tables_used": [
                "report_evidence.headline",
                "report_evidence.economic",
                "report_evidence.sides",
                "report_evidence.classifiers",
            ],
            "filters": [
                "all outcome exposure ends before 2026-05-04T00:00:00Z",
                "dependency representatives only for economic final metrics",
                "L1.5 inputs physically end at core_end with zero post-core bars",
                "LONG and SHORT models and tune-q90 thresholds remain separate",
            ],
            "metric_definitions": [
                "net mean = mean realized gross return - 0.002 round-trip cost",
                "L1.5 false-positive rate = accepted hard negatives / all hard negatives",
                "L2 frozen q90 = score threshold fitted separately by side on tune only",
                "matched excess = selected-event net return - same assignment matched-control net return",
                "permutation p = preregistered event/block-level permutation test reported by the terminal receipt",
            ],
            "sql": SOURCE_SQL,
        },
    }


def write_source_database(
    headline: list[dict[str, Any]],
    economic: list[dict[str, Any]],
    sides: list[dict[str, Any]],
    classifiers: list[dict[str, Any]],
) -> None:
    """Materialize and execute the exact source query exposed by the report."""

    if SOURCE_DB_PATH.exists():
        SOURCE_DB_PATH.unlink()
    with sqlite3.connect(SOURCE_DB_PATH) as connection:
        pd.DataFrame(headline).to_sql("headline", connection, index=False)
        pd.DataFrame(economic).to_sql("economic", connection, index=False)
        pd.DataFrame(sides).to_sql("sides", connection, index=False)
        pd.DataFrame(classifiers).to_sql("classifiers", connection, index=False)
        rows = connection.execute(SOURCE_SQL).fetchall()
    expected = len(headline) + len(economic) + len(sides) + len(classifiers)
    if len(rows) != expected:
        raise ReportError(f"source SQL returned {len(rows)} rows, expected {expected}")


def build_artifact(
    evidence: Mapping[str, Any],
    economic: list[dict[str, Any]],
    sides: list[dict[str, Any]],
    classifiers: list[dict[str, Any]],
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    l2 = evidence["receipts"]["l2_training_receipt.json"]
    l15 = evidence["receipts"]["l15_training_receipt.json"]
    headline = [
        {
            "short_fpr": float(l15["arms"]["short"]["final_metrics"]["false_positive_rate"]),
            "l2_net": float(l2["arms"]["l2_only"]["metrics"]["frozen_q90"]["net_mean"]),
            "pipeline_n": int(l2["arms"]["l15_l2"]["metrics"]["frozen_q90"]["n"]),
        }
    ]
    write_source_database(headline, economic, sides, classifiers)
    source = source_spec(generated_at)
    manifest = {
        "version": 1,
        "surface": "report",
        "title": "15m 均线密集启动：因果 L1.5 + 多空 L2 全链路审计",
        "description": "四组冻结对照显示：形态弱标签可分类，但全链路经济门未通过，不能开启。",
        "generatedAt": generated_at,
        "cards": [
            {
                "id": "short_fpr",
                "description": "SHORT L1.5 最终误报率；预注册上限为 12%。",
                "dataset": "headline",
                "sourceId": "pipeline_receipts",
                "metrics": [{"label": "SHORT L1.5 误报率", "field": "short_fpr", "format": "percent"}],
            },
            {
                "id": "l2_net",
                "description": "L2-only tune-q90 选中 34 个事件后的平均净收益；未通过置换与双边门。",
                "dataset": "headline",
                "sourceId": "pipeline_receipts",
                "metrics": [{"label": "L2-only q90 净均值", "field": "l2_net", "format": "percent"}],
            },
            {
                "id": "pipeline_n",
                "description": "完整 L1.5+L2 最终入选事件数；预注册最低为 30。",
                "dataset": "headline",
                "sourceId": "pipeline_receipts",
                "metrics": [{"label": "完整链路入选", "field": "pipeline_n", "format": "number", "unit": "个"}],
            },
        ],
        "charts": [
            {
                "id": "factorial_chart",
                "title": "四组冻结配置的入选集合净收益",
                "subtitle": "最终 pre-holdout 开发段；扣 20 bp 往返成本，正值不覆盖样本量与显著性门",
                "showDescription": True,
                "intent": "comparison",
                "question": "L1.5 和 L2 单独或联合加入后，入选集合净收益如何变化？",
                "rationale": "四个预注册 arm 是离散类别，排序柱图直接显示方向与幅度，并保留样本量和 p 值。",
                "comparisonContext": {"grain": "配置的最终入选集合", "unit": "bp", "denominator": "每组独立事件"},
                "type": "horizontalBar",
                "dataset": "economic",
                "sourceId": "pipeline_receipts",
                "encodings": {
                    "x": {"field": "arm", "type": "ordinal", "label": "配置"},
                    "y": {"field": "net_mean_bp", "type": "quantitative", "label": "净均值（bp）"},
                    "tooltip": [
                        {"field": "selected_n", "type": "quantitative", "label": "独立事件"},
                        {"field": "permutation_p", "type": "quantitative", "label": "置换 p"},
                        {"field": "matched_excess_bp", "type": "quantitative", "label": "减匹配对照（bp）"},
                        {"field": "gate", "type": "nominal", "label": "裁决"},
                    ],
                },
                "valueFormat": "number",
                "unit": "bp",
                "layout": "full",
                "labels": {"values": "all"},
                "palette": {"kind": "sequential", "name": "blue"},
                "settings": {"sort": "custom", "showValues": True},
                "surface": {"surface": "card", "viewMode": "both"},
            }
        ],
        "tables": [
            {
                "id": "classifier_table",
                "title": "L1.5 最终分类指标",
                "subtitle": "协议弱标签；误报门 12%，LONG/SHORT 分开",
                "dataset": "classifiers",
                "sourceId": "pipeline_receipts",
                "density": "spacious",
                "defaultSort": {"field": "side", "direction": "asc"},
                "columns": [
                    {"field": "side", "label": "方向", "type": "text"},
                    {"field": "final_n", "label": "n", "format": "number"},
                    {"field": "roc_auc", "label": "AUC", "format": "number"},
                    {"field": "baseline_auc", "label": "单特征AUC", "format": "number"},
                    {"field": "precision", "label": "精确率", "format": "percent"},
                    {"field": "recall", "label": "召回率", "format": "percent"},
                    {"field": "false_positive_rate", "label": "误报率", "format": "percent"},
                    {"field": "gate", "label": "裁决", "type": "text"},
                ],
            },
            {
                "id": "side_table",
                "title": "L2 冻结 q90 多空结果",
                "subtitle": "固定 TP5/SL2/72、next-open 与 20 bp 成本",
                "dataset": "sides",
                "sourceId": "pipeline_receipts",
                "density": "spacious",
                "defaultSort": {"field": "net_mean_bp", "direction": "desc"},
                "columns": [
                    {"field": "arm", "label": "配置", "type": "text"},
                    {"field": "side", "label": "方向", "type": "text"},
                    {"field": "selected_n", "label": "n", "format": "number"},
                    {"field": "net_mean_bp", "label": "q90净均值(bp)", "format": "number", "role": "movement"},
                    {"field": "top_decile_bp", "label": "top10%净均值(bp)", "format": "number", "role": "movement"},
                    {"field": "roc_auc", "label": "AUC", "format": "number"},
                    {"field": "permutation_p", "label": "置换p", "format": "number"},
                ],
            },
        ],
        "sources": [source],
        "blocks": [
            {"id": "title", "type": "markdown", "body": "# 15m 均线密集启动：因果 L1.5 + 多空 L2 全链路审计"},
            {
                "id": "summary",
                "type": "markdown",
                "sourceId": "pipeline_receipts",
                "body": (
                    "## 技术结论：整条链路未通过，不能开启\n\n"
                    f"- **L1.5 不是整体合格。** LONG 误报率 {100*l15['arms']['long']['final_metrics']['false_positive_rate']:.1f}%，"
                    f"SHORT 为 {100*l15['arms']['short']['final_metrics']['false_positive_rate']:.1f}%，后者超过 12% 预注册上限。\n"
                    f"- **完整链路样本不足且不显著。** L1.5+L2 只留 {l2['arms']['l15_l2']['metrics']['frozen_q90']['n']} 个，"
                    f"净均值 {100*l2['arms']['l15_l2']['metrics']['frozen_q90']['net_mean']:.3f}%，"
                    f"置换 p={l2['arms']['l15_l2']['metrics']['permutation_p']:.3f}。\n"
                    "- **不能把高形态 AUC 当收益证据。** L1.5 学的是协议弱标签；L2 的方向结果在不同过滤下翻转。\n"
                    "- **本轮裁决是 REJECT。** 未读 holdout，未 promote、部署或写入实盘状态。"
                ),
            },
            {"id": "headline", "type": "metric-strip", "cardIds": ["short_fpr", "l2_net", "pipeline_n"]},
            {
                "id": "factorial_text",
                "type": "markdown",
                "sourceId": "pipeline_receipts",
                "body": "## 四组对照：加层没有产生稳定提升\n\nL1.5 单独过滤后净均值转负；L2-only 虽然均值为正，但 p=0.192 且 LONG 为负；完整链路只有 18 个事件。柱图展示的是实际入选集合，不能脱离样本量和门禁单看正负。",
            },
            {"id": "factorial_chart_block", "type": "chart", "chartId": "factorial_chart", "layout": "full"},
            {
                "id": "classifier_text",
                "type": "markdown",
                "sourceId": "pipeline_receipts",
                "body": "## L1.5 只能证明复现弱标签\n\n输入使用 128 根历史并严格截止 core_end，确认段可见 K 为 0；一事件一行，LONG/SHORT 分开。分类明显优于单一均线密集度，但正负来自自动协议而非 Owner 全局金标，因此不能把 AUC 解释成‘完美形态准确率’。",
            },
            {"id": "classifier_table_block", "type": "table", "tableId": "classifier_table"},
            {
                "id": "side_text",
                "type": "markdown",
                "sourceId": "pipeline_receipts",
                "body": "## 多空结果翻转说明排序不稳定\n\nL2-only 的 SHORT 为正、LONG 为负；加 L1.5 后方向翻转。看完 final 后再选择方向会产生新的过拟合，必须重新预注册并用未见时期验证。",
            },
            {"id": "side_table_block", "type": "table", "tableId": "side_table"},
            {
                "id": "scope",
                "type": "markdown",
                "body": "## 范围、方法与可视证据\n\nL1.5 数据为 3,129 个独立事件（1,043 正、2,086 hard 负）；经济 final 为 242 个依赖去重事件。结果固定 TP5/SL2/72、next-open、同根先 SL、20 bp 成本及 8 组匹配随机对照。38 张 128-bar 高清图全部通过像素和框坐标校验；逐图画廊与总览随报告交付。",
            },
            {
                "id": "shortcut",
                "type": "markdown",
                "body": "## 第一版 AUC=1.0 是标签捷径，已作废\n\n第一版同时让标签和特征使用核心后的确认推进，单一距离特征即可完美还原标签。v2 在训练前预注册为物理截断全部 post-core K；这个失败不会被包装成成功结果。",
            },
            {
                "id": "limitations",
                "type": "markdown",
                "sourceId": "pipeline_receipts",
                "body": "## 限制与稳健性\n\n- 经济 final 已用于此前开发，只能作回顾诊断。\n- L1.5 是协议弱标签，不是 Owner 全局金标。\n- 完整链路 n=18，均值极不稳定。\n- 未读取 holdout；配置不具 production eligibility。",
            },
            {
                "id": "next",
                "type": "markdown",
                "body": "## 下一步：先修监督目标，再谈收益层\n\n1. 冻结本轮为失败基线，不调阈值。\n2. LONG/SHORT 分开建立真正的 `global_shape_good` 标签，重点覆盖 L1.5 保留/拒绝分歧样本。\n3. L1.5 永远截止 core_end，只判断形态；L2 才预测实际收益。\n4. 在新的未见时期同时通过形态门、经济门和随机对照后，才申请一次 holdout。",
            },
            {
                "id": "questions",
                "type": "markdown",
                "body": "## 仍待确认的问题\n\n如果完全不做 Owner 全局形态校准，后续只能诚实称为协议分类或无监督聚类，不能称为‘完美形态判断’。L2-only SHORT 的探索性正值也必须由新时期单独复验。",
            },
        ],
    }
    snapshot = {
        "version": 1,
        "generatedAt": generated_at,
        "status": "ready",
        "datasets": {
            "headline": headline,
            "economic": economic,
            "sides": sides,
            "classifiers": classifiers,
        },
    }
    return {"surface": "report", "manifest": manifest, "snapshot": snapshot, "sources": [source]}


def build_outputs() -> dict[str, Any]:
    evidence = validate_evidence()
    l2 = evidence["receipts"]["l2_training_receipt.json"]
    l15 = evidence["receipts"]["l15_training_receipt.json"]
    economic = economic_rows(l2)
    sides = side_rows(l2)
    classifiers = l15_rows(l15)
    metric_chart = make_metric_chart(economic, sides, METRIC_CHART_PATH)
    review_manifest = read_json(repo_path(evidence["receipts"]["render_receipt.json"]["manifest_path"]))
    gallery = make_gallery(review_manifest, GALLERY_PATH)
    markdown = build_markdown(evidence, economic, sides, classifiers, metric_chart, gallery)
    REPORT_PATH.write_text(markdown, encoding="utf-8")
    artifact = build_artifact(evidence, economic, sides, classifiers)
    ARTIFACT_PATH.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    notes = {
        "audience": "technical",
        "delivery_mode": "html",
        "report_spine": {
            "question": "Can causal global morphology filtering and side-split return ranking fix globally poor L1 detections?",
            "answer": "No. The full chain fails morphology FPR, sample-size, side-consistency and permutation gates.",
            "comparison": "Frozen four-arm L1/L1.5/L2 factorial on the same pre-holdout candidate snapshot.",
        },
        "chart_map": [
            {
                "section": "四组对照",
                "question": "How does selected-set net return differ across the four frozen arms?",
                "family": "comparison",
                "type": "horizontalBar in portable artifact; two-panel signed bars in Markdown archive",
                "fields": ["arm", "net_mean_bp", "selected_n", "permutation_p"],
                "palette": "hard two-root cap plus neutral",
                "claim": "No arm passes all preregistered gates despite positive means in two small selections.",
            },
            {
                "section": "全局图审核",
                "question": "Do actual 128-bar inputs explain the disagreement between weak-label classification and economics?",
                "family": "small-multiple contact sheet",
                "type": "static review overview plus 38-image gallery",
                "fields": ["state", "side", "raw L1 box", "L1.5 score"],
                "palette": "semantic frozen L1 red/green boxes",
                "claim": "Rendering is parity-clean, while weak-label visual semantics remain imperfect.",
            },
        ],
        "omissions": [
            "No holdout score because the owner did not authorize a holdout read for this configuration.",
            "No production recommendation because every primary gate failed.",
            "The portable artifact links rather than embeds the 38 large PNGs; the adjacent gallery is the visual-review surface.",
        ],
    }
    NOTES_PATH.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "markdown": repo_relative(REPORT_PATH),
        "artifact": repo_relative(ARTIFACT_PATH),
        "gallery": gallery,
        "metric_chart": metric_chart,
    }


def finalize_receipt() -> dict[str, Any]:
    evidence = validate_evidence()
    required = [
        REPORT_PATH,
        HTML_PATH,
        MARKDOWN_HTML_PATH,
        GALLERY_PATH,
        ARTIFACT_PATH,
        NOTES_PATH,
        SOURCE_DB_PATH,
        METRIC_CHART_PATH,
    ]
    for path in required:
        if not path.is_file():
            raise ReportError(f"cannot finalize; missing report artifact: {path}")
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "builder_commit": git_head(),
        "artifacts": {
            repo_relative(path): {"sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in required
        },
        "source_receipts": {
            name: sha256_file(RESULTS_DIR / name)
            for name in evidence["receipts"]
        },
        "report_audience": "technical",
        "delivery_mode": "html",
        "portable_html_present": True,
        "markdown_html_present": True,
        "review_images": int(evidence["receipts"]["render_receipt.json"]["charts"]),
        "holdout_consumed": False,
        "production_eligible": False,
    }
    RECEIPT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    payload = finalize_receipt() if args.finalize else build_outputs()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
