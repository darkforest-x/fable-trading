#!/usr/bin/env python3
"""Build the exact-short-window L2 audit report and visual review gallery.

This builder is downstream-only. It reads the frozen preregistration, dataset,
training, rendering and verification receipts plus their declared files. It
never trains or scores a model, reads holdout bars, changes a threshold,
promotes, deploys, mutates forward state, sends Telegram, or places orders.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l2-short-window-side-split-v1"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_DIR = ROOT / "analysis" / "output" / "ma_launch_l2_short_window_side_split_v1"
REPORT_PATH = ROOT / "analysis" / "p3_15m_ma_launch_l2_short_window_side_split_20260901.md"
HTML_PATH = ROOT / "analysis" / "html" / f"{REPORT_PATH.stem}.html"
GALLERY_PATH = ROOT / "analysis" / "html" / (
    "p3_15m_ma_launch_l2_short_window_side_split_gallery_20260901.html"
)
OVERVIEW_PATH = OUTPUT_DIR / "short_window_l2_review_overview.png"
METRIC_CHART_PATH = OUTPUT_DIR / "short_window_l2_economic_comparison.png"
REPORT_RECEIPT_PATH = RESULTS_DIR / "report_receipt.json"
OLD_SIDE_TRAINING = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-l2-side-split-v1"
    / "results"
    / "training_receipt.json"
)
OLD_GLOBAL_TRAINING = (
    ROOT
    / "experiments"
    / "active"
    / "exp-15m-ma-launch-l2-global-context-v1"
    / "results"
    / "training_receipt.json"
)


class ReportError(RuntimeError):
    """Fail closed when report evidence or lineage is incomplete."""


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


def pct(value: float | None, digits: int = 2, *, signed: bool = False) -> str:
    if value is None:
        return "—"
    sign = "+" if signed else ""
    return f"{100 * float(value):{sign}.{digits}f}%"


def bp(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{10_000 * float(value):+.{digits}f} bp"


def number(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def bool_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower()
    if not normalized.isin(("true", "false")).all():
        raise ReportError("invalid boolean column in scored validation")
    return normalized.map({"true": True, "false": False}).astype(bool)


def validate_evidence() -> dict[str, Any]:
    prereg_path = EXPERIMENT_DIR / "preregistration.json"
    dataset_path = RESULTS_DIR / "dataset_receipt.json"
    training_path = RESULTS_DIR / "training_receipt.json"
    render_path = RESULTS_DIR / "render_receipt.json"
    verify_path = RESULTS_DIR / "verify_receipt.json"
    prereg = read_json(prereg_path)
    dataset = read_json(dataset_path)
    training = read_json(training_path)
    render = read_json(render_path)
    verify = read_json(verify_path)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise ReportError("preregistration experiment identity drifted")
    if any(payload.get("experiment_id") != EXPERIMENT_ID for payload in (dataset, training, render, verify)):
        raise ReportError("one or more result receipts have the wrong experiment identity")
    if verify.get("passed") is not True or not all(verify.get("checks", {}).values()):
        raise ReportError("verification receipt is not fully green")
    if training.get("primary_gate", {}).get("passed") is not False:
        raise ReportError("this builder is frozen to the observed rejected result")
    require_hash(
        repo_path(dataset["dataset_path"]), dataset["dataset_sha256"], "short-window dataset"
    )
    require_hash(
        repo_path(dataset["matched_controls_path"]),
        dataset["matched_controls_sha256"],
        "matched controls",
    )
    require_hash(
        repo_path(training["scored_validation_path"]),
        training["scored_validation_sha256"],
        "scored validation",
    )
    require_hash(
        repo_path(render["manifest_path"]), render["manifest_sha256"], "render manifest"
    )
    if training.get("holdout_consumed") is not False or training.get("production_eligible") is not False:
        raise ReportError("training receipt crossed a safety boundary")
    for family in ("models", "single_feature_baseline_models"):
        for side, spec in training[family].items():
            require_hash(repo_path(spec["model_path"]), spec["model_sha256"], f"{family} {side}")
            require_hash(
                repo_path(spec["importance_path"]),
                spec["importance_sha256"],
                f"{family} importance {side}",
            )
    return {
        "prereg": prereg,
        "dataset": dataset,
        "training": training,
        "render": render,
        "verify": verify,
        "old_side": read_json(OLD_SIDE_TRAINING),
        "old_global": read_json(OLD_GLOBAL_TRAINING),
        "source_hashes": {
            "preregistration": sha256_file(prereg_path),
            "dataset_receipt": sha256_file(dataset_path),
            "training_receipt": sha256_file(training_path),
            "render_receipt": sha256_file(render_path),
            "verify_receipt": sha256_file(verify_path),
            "old_side_training_receipt": sha256_file(OLD_SIDE_TRAINING),
            "old_global_training_receipt": sha256_file(OLD_GLOBAL_TRAINING),
        },
    }


def make_metric_chart(training: Mapping[str, Any], output: Path) -> dict[str, Any]:
    """Render two signed bar panels with an honest zero baseline."""

    main = training["main"]
    baseline = training["single_feature_l1_confidence_baseline"]
    overall_labels = ["Exact W18/19\ntop 10%", "Exact W18/19\ntune q90", "L1 confidence\ntop 10%", "L1 confidence\ntune q90"]
    overall_values = np.array(
        [
            main["final_validation"]["top_decile"]["net_mean"],
            main["frozen_threshold"]["net_mean"],
            baseline["final_validation"]["top_decile"]["net_mean"],
            baseline["frozen_threshold"]["net_mean"],
        ],
        dtype=float,
    ) * 10_000
    side_labels = ["LONG\ntop 10%", "LONG\ntune q90", "SHORT\ntop 10%", "SHORT\ntune q90"]
    side_values = np.array(
        [
            main["by_side"]["long"]["final_validation"]["top_decile"]["net_mean"],
            main["by_side"]["long"]["frozen_threshold"]["net_mean"],
            main["by_side"]["short"]["final_validation"]["top_decile"]["net_mean"],
            main["by_side"]["short"]["frozen_threshold"]["net_mean"],
        ],
        dtype=float,
    ) * 10_000
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "DejaVu Sans"],
            "axes.edgecolor": "#343A40",
            "axes.labelcolor": "#343A40",
            "xtick.color": "#343A40",
            "ytick.color": "#343A40",
        }
    )
    figure, axes = plt.subplots(1, 2, figsize=(16, 7.5), dpi=120)
    panels: Sequence[tuple[Any, Sequence[str], np.ndarray, Sequence[str], str]] = (
        (
            axes[0],
            overall_labels,
            overall_values,
            ("#2F6B9A", "#2F6B9A", "#B8BEC5", "#B8BEC5"),
            "Overall exact-window model vs L1-confidence baseline",
        ),
        (
            axes[1],
            side_labels,
            side_values,
            ("#D8892B", "#D8892B", "#2F6B9A", "#2F6B9A"),
            "Exact-window model by direction",
        ),
    )
    for axis, labels, values, colors, title in panels:
        positions = np.arange(len(labels))
        bars = axis.bar(positions, values, color=colors, edgecolor="#343A40", linewidth=0.8)
        axis.axhline(0, color="#343A40", linewidth=1.2)
        axis.set_xticks(positions, labels)
        axis.set_ylabel("Mean net return (bp), after 20 bp round trip")
        axis.set_title(title, loc="left", fontsize=13, fontweight="bold")
        axis.grid(axis="y", color="#D9DEE3", linewidth=0.7, alpha=0.8)
        axis.set_axisbelow(True)
        pad = max(4.0, float(np.ptp(values)) * 0.08)
        axis.set_ylim(
            min(0.0, float(values.min())) - 1.55 * pad,
            max(0.0, float(values.max())) + 1.55 * pad,
        )
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
        "15m MA-launch L2: frozen final pre-holdout economics",
        x=0.04,
        ha="left",
        fontsize=17,
        fontweight="bold",
        color="#202428",
    )
    figure.text(
        0.04,
        0.02,
        "673 independent final events; LONG and SHORT use separate tune-q90 thresholds. "
        "Bars are descriptive final-validation results, not production estimates.",
        fontsize=10,
        color="#50565C",
    )
    figure.tight_layout(rect=(0.03, 0.07, 0.99, 0.91))
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, facecolor="white", bbox_inches="tight")
    plt.close(figure)
    image = cv2.imread(str(output), cv2.IMREAD_COLOR)
    if image is None:
        raise ReportError("metric chart could not be decoded after writing")
    return {
        "path": repo_relative(output),
        "sha256": sha256_file(output),
        "width": int(image.shape[1]),
        "height": int(image.shape[0]),
        "chart_contract": {
            "question": "Does exact W18/W19 L2 improve net selection, and how do LONG/SHORT differ?",
            "family": "comparison",
            "variant": "two-panel signed categorical bars with zero reference",
            "unit": "basis points after 20 bp round-trip cost",
            "palette": "two-root cap: blue exact/SHORT, orange LONG, neutral baseline",
        },
    }


def _tile(image: np.ndarray, width: int, height: int) -> np.ndarray:
    return cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)


def make_overview(manifest: pd.DataFrame, output: Path) -> dict[str, Any]:
    """Compose 12 verified native overlays: three per decision/side cell."""

    groups = (
        ("selected", "long", "L2 SELECTED - LONG"),
        ("selected", "short", "L2 SELECTED - SHORT"),
        ("rejected_high_l1", "long", "HIGH-L1 REJECTED - LONG"),
        ("rejected_high_l1", "short", "HIGH-L1 REJECTED - SHORT"),
    )
    tile_width, tile_height, label_height = 480, 278, 38
    title_height, columns = 58, 3
    canvas = np.full(
        (title_height + len(groups) * (label_height + tile_height), tile_width * columns, 3),
        247,
        dtype=np.uint8,
    )
    cv2.putText(
        canvas,
        "Exact W18/W19 inputs used by L2 (native raw boxes; no future bars)",
        (20, 38),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.82,
        (28, 32, 36),
        2,
        cv2.LINE_AA,
    )
    sources: list[dict[str, Any]] = []
    for row_number, (group, side, label) in enumerate(groups):
        subset = manifest[(manifest["group"] == group) & (manifest["side"] == side)].head(columns)
        if len(subset) != columns:
            raise ReportError(f"overview lacks three rows for {group}/{side}")
        top = title_height + row_number * (label_height + tile_height)
        cv2.putText(
            canvas,
            label,
            (18, top + 27),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.68,
            (40, 45, 50),
            2,
            cv2.LINE_AA,
        )
        for column, record in enumerate(subset.to_dict("records")):
            source = repo_path(record["overlay_path"])
            require_hash(source, record["overlay_sha256"], "overview overlay")
            image = cv2.imread(str(source), cv2.IMREAD_COLOR)
            if image is None:
                raise ReportError(f"could not decode overview source: {source}")
            resized = _tile(image, tile_width, tile_height)
            x = column * tile_width
            y = top + label_height
            canvas[y : y + tile_height, x : x + tile_width] = resized
            cv2.rectangle(
                canvas,
                (x, y),
                (x + tile_width - 1, y + tile_height - 1),
                (92, 98, 104),
                1,
            )
            sources.append(
                {
                    "episode_id": str(record["episode_id"]),
                    "group": group,
                    "side": side,
                    "overlay_path": repo_relative(source),
                    "overlay_sha256": record["overlay_sha256"],
                }
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise ReportError(f"could not write overview: {output}")
    return {
        "path": repo_relative(output),
        "sha256": sha256_file(output),
        "width": int(canvas.shape[1]),
        "height": int(canvas.shape[0]),
        "sources": sources,
    }


def make_gallery(manifest: pd.DataFrame, output: Path) -> dict[str, Any]:
    cards: list[str] = []
    for record in manifest.to_dict("records"):
        raw = repo_path(record["raw_path"])
        overlay = repo_path(record["overlay_path"])
        require_hash(raw, record["raw_sha256"], "gallery raw image")
        require_hash(overlay, record["overlay_sha256"], "gallery overlay image")
        overlay_rel = Path(os.path.relpath(overlay, output.parent)).as_posix()
        raw_rel = Path(os.path.relpath(raw, output.parent)).as_posix()
        group = "SELECTED" if record["group"] == "selected" else "HIGH-L1 REJECTED"
        cards.append(
            "<article class='card'>"
            f"<a href='{html.escape(raw_rel)}'><img loading='lazy' "
            f"src='{html.escape(overlay_rel)}' alt='{html.escape(str(record['episode_id']))}'></a>"
            f"<h2>{group} · {html.escape(str(record['side']).upper())} · "
            f"{html.escape(str(record['symbol']))}</h2>"
            f"<p>{html.escape(str(record['available_at']))}<br>"
            f"W{int(record['window_len'])} · L1 {float(record['l1_confidence']):.3f} · "
            f"L2 percentile {float(record['l2_percentile']):.3f} · "
            f"gross return {100 * float(record['realized_ret']):+.3f}%<br>"
            "点击图片打开无框原始输入。</p></article>"
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark"><title>精确短窗 L2：40 张实际输入图</title>
<style>
:root{{color-scheme:light dark}}body{{font:15px/1.5 system-ui,-apple-system,"PingFang SC",sans-serif;margin:0;background:#101214;color:#eef1f3}}
header{{position:sticky;top:0;z-index:2;padding:14px 20px;background:#15191ded;border-bottom:1px solid #3a4148}}
main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(560px,1fr));gap:14px;padding:14px}}
.card{{background:#191e22;border:1px solid #3a4148;border-radius:9px;overflow:hidden}}
img{{display:block;width:100%;height:auto;background:white}}h2{{font-size:16px;margin:10px 12px 4px}}p{{margin:4px 12px 13px;color:#bbc3ca}}
@media(max-width:620px){{main{{grid-template-columns:1fr;padding:8px}}}}
</style></head><body><header><strong>精确短窗 L2：40 张实际输入图</strong> · 红/绿框是冻结 L1 原框 · 无未来 K 线 · 点击看无框原图</header>
<main>{''.join(cards)}</main></body></html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return {"path": repo_relative(output), "sha256": sha256_file(output), "images": len(cards)}


def _importance_rows(training: Mapping[str, Any]) -> str:
    rows: list[str] = []
    for side in ("long", "short"):
        for rank, item in enumerate(training["models"][side]["importance_top10"][:5], 1):
            rows.append(
                f"| {side.upper()} | {rank} | `{item['feature']}` | {float(item['gain']):.6g} |"
            )
    return "\n".join(rows)


def build_markdown(
    evidence: Mapping[str, Any],
    dataset_frame: pd.DataFrame,
    scored: pd.DataFrame,
    controls: pd.DataFrame,
    overview: Mapping[str, Any],
    metric_chart: Mapping[str, Any],
    gallery: Mapping[str, Any],
    *,
    builder_commit: str,
) -> str:
    prereg = evidence["prereg"]
    dataset = evidence["dataset"]
    training = evidence["training"]
    verify = evidence["verify"]
    old_side = evidence["old_side"]
    old_global = evidence["old_global"]
    main = training["main"]
    baseline = training["single_feature_l1_confidence_baseline"]
    gate = training["primary_gate"]
    selected = scored[bool_series(scored["short_window_keep"])].copy()
    matched_ids = set(controls["episode_id"].astype(str))
    selected_covered = selected[selected["episode_id"].astype(str).isin(matched_ids)]
    selected_unmatched = selected[~selected["episode_id"].astype(str).isin(matched_ids)]
    cost = float(prereg["outcome"]["round_trip_cost_fraction"])
    gate_rows = "\n".join(
        f"| `{name}` | {'PASS' if passed else 'FAIL'} |"
        for name, passed in gate.items()
        if name != "passed"
    )
    comparison_rows = "\n".join(
        [
            (
                f"| 本轮精确 W18/W19，多空分开 | {main['final_validation']['n']} | "
                f"{training['models']['long']['best_iteration']} / {training['models']['short']['best_iteration']} | "
                f"{main['frozen_threshold']['n']} | {bp(main['frozen_threshold']['net_mean'])} | "
                f"{bp(main['final_validation']['top_decile']['net_mean'])} | "
                f"{number(main['outcome_permutation_p'])} | {number(main['final_validation']['roc_auc'])} |"
            ),
            (
                f"| 本轮仅 L1 confidence 基线 | {baseline['final_validation']['n']} | "
                f"{training['single_feature_baseline_models']['long']['best_iteration']} / "
                f"{training['single_feature_baseline_models']['short']['best_iteration']} | "
                f"{baseline['frozen_threshold']['n']} | {bp(baseline['frozen_threshold']['net_mean'])} | "
                f"{bp(baseline['final_validation']['top_decile']['net_mean'])} | "
                f"{number(baseline['outcome_permutation_p'])} | {number(baseline['final_validation']['roc_auc'])} |"
            ),
            (
                f"| 旧 168 根上下文，多空分开 | {old_side['aggregate_side_split']['final_validation']['n']} | "
                f"{old_side['sides']['long']['best_iteration']} / {old_side['sides']['short']['best_iteration']} | "
                f"{old_side['aggregate_side_split']['final_validation_frozen_threshold']['n']} | "
                f"{bp(old_side['aggregate_side_split']['final_validation_frozen_threshold']['net_mean'])} | "
                f"{bp(old_side['aggregate_side_split']['final_validation']['top_decile']['net_mean'])} | "
                f"{number(old_side['aggregate_side_split']['outcome_permutation_p'])} | "
                f"{number(old_side['aggregate_side_split']['final_validation']['roc_auc'])} |"
            ),
            (
                f"| 旧 168 根上下文，混合方向 | {old_global['final_validation']['n']} | 2 | "
                f"{old_global['final_validation_frozen_threshold']['n']} | "
                f"{bp(old_global['final_validation_frozen_threshold']['net_mean'])} | "
                f"{bp(old_global['final_validation']['top_decile']['net_mean'])} | "
                f"{number(old_global['outcome_permutation_p'])} | {number(old_global['final_validation']['roc_auc'])} |"
            ),
        ]
    )
    side_rows: list[str] = []
    for side in ("long", "short"):
        arm = main["by_side"][side]
        model = training["models"][side]
        side_frame = scored[scored["side"] == side]
        side_rows.append(
            f"| {side.upper()} | {model['splits']['train']} / {model['splits']['tune']} / "
            f"{model['splits']['final_validation']} | {model['best_iteration']} | "
            f"{side_frame['short_window_score'].nunique()} | {arm['frozen_threshold']['n']} | "
            f"{bp(arm['frozen_threshold']['net_mean'])} | "
            f"{bp(arm['final_validation']['top_decile']['net_mean'])} | "
            f"{number(arm['outcome_permutation_p'])} | {number(arm['final_validation']['roc_auc'])} | "
            f"{number(arm['final_validation']['spearman_score_vs_return'])} |"
        )
    overview_rel = Path(overview["path"]).relative_to("analysis").as_posix()
    metric_rel = Path(metric_chart["path"]).relative_to("analysis").as_posix()
    min_time = pd.to_datetime(dataset_frame["available_at"], utc=True).min()
    max_time = pd.to_datetime(dataset_frame["available_at"], utc=True).max()
    max_exposure = pd.to_datetime(dataset_frame["exposure_end_exclusive"], utc=True).max()
    selected_covered_net = (
        float(selected_covered["realized_ret"].mean()) - cost if len(selected_covered) else None
    )
    selected_unmatched_net = (
        float(selected_unmatched["realized_ret"].mean()) - cost if len(selected_unmatched) else None
    )
    old_report_warning = (
        "旧模型只作方向性背景：它使用不同的 168 根特征、旧 episode/依赖块，最终独立事件仅 242 个，"
        "不能与本轮 673 个事件当作严格单变量胜负。"
    )
    return f"""# 15m 均线密集启动：精确短窗 L2 多空回归审计（2026-09-01）

## 技术结论：局部形态输入没有学到稳定的未来收益排序

本轮把 L2 严格改成与 L1 相同的 **18/19 根、1280×742 原始输入**，只使用图里可见的 OHLC、
SMA/EMA 20/60/120、当前原始检测框与当前 confidence，并为 LONG、SHORT 分别训练收益回归。
结果是 **预注册门 FAIL，不可用于过滤、部署或下单**：673 个最终独立事件的 top 10% 扣 0.2%
往返成本后平均 {bp(main['final_validation']['top_decile']['net_mean'])}，单尾置换
`p={main['outcome_permutation_p']:.6f}`，AUC `{main['final_validation']['roc_auc']:.4f}`，
Spearman `{main['final_validation']['spearman_score_vs_return']:.4f}`。

最重要的分化是：SHORT 的 tune-q90 组 15 个事件净均值
{bp(main['by_side']['short']['frozen_threshold']['net_mean'])}，但 LONG 的 46 个事件为
{bp(main['by_side']['long']['frozen_threshold']['net_mean'])}。这个方向差异是**探索性结果**；它是在本次
final validation 上看到的，不能现在删除 LONG、保留 SHORT 后再把同一段数据称为独立验证。

## 经济结果图：总体不显著，LONG 与 SHORT 方向相反

下图所有柱都使用同一最终 pre-holdout 时段和 20 bp 成本。左侧比较精确短窗模型与只用 L1
confidence 的基线；右侧拆开 LONG/SHORT。q90 偶然为正不能覆盖 top-decile、置换检验与随机对照失败。

![精确短窗 L2 经济结果]({metric_rel})

| 模型口径 | final 独立事件 | best iter LONG/SHORT | q90 n | q90净收益 | top-decile净收益 | 置换p | AUC |
|---|---:|---:|---:|---:|---:|---:|---:|
{comparison_rows}

{old_report_warning}

## 实际输入图：框和局部形态没坏，失败的是收益预测

40 张抽样图均从**模型实际使用的原始 1280×742 输入**重新读取并逐文件验哈希；红/绿框是冻结 L1
原框，没有未来 K 线。局部形态看起来合理并不矛盾：L1 的任务是找“像不像均线密集启动”，本轮 L2
的任务却是预测“此后 TP5/SL2/72 的实际收益”。前者成立，不等于后者存在可泛化优势。

![实际入选与高置信拒绝图总览]({overview_rel})

高清逐图浏览：[{gallery['images']} 张实际输入图（点击可切换无框原图）]({Path(gallery['path']).name})。

## LONG 模型几乎退化，SHORT 也没有显著排序能力

| 方向 | 独立 train/tune/final | best iter | final 唯一分数 | q90 n | q90净收益 | top-decile净收益 | 置换p | AUC | Spearman |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(side_rows)}

LONG 在 480 个训练独立块、238 个特征下早停于第 1 棵树，442 个 final 事件只有
{scored[scored['side'] == 'long']['short_window_score'].nunique()} 个不同分数；46 个 q90 入选事件全部落在
同一个最高分并列组。这不是精细排序，而是低分辨率分桶。SHORT 早停于第 4 棵树，分数更细，但
置换 `p={main['by_side']['short']['outcome_permutation_p']:.4f}`、AUC
`{main['by_side']['short']['final_validation']['roc_auc']:.4f}`，仍不能证明稳定优势。

当前配置的有效样本/特征比例也很紧：LONG 480/238，SHORT 647/238。LightGBM 的早停行为与
final 上负 Spearman 一致，说明这批“局部可见像素坐标特征”对未来收益信号弱，而不是模型还没多跑几轮。

## 严格随机对照揭示 q90 正收益由缺配样本驱动

随机对照固定同币、同月、同 UTC 8 小时时段、同因果 ATR 五分位、同方向、同 TP/SL/期限/成本；
每个 assignment 内控制点至少相隔 72 根，并且只有凑齐 8/8 assignments 的事件才进入对照账本。
673 个最终独立事件中，354 个能完整匹配，319 个诚实缺样。

本轮 q90 共 {len(selected)} 个事件，其中只有 {len(selected_covered)} 个有完整对照：

- 全部 q90：净均值 {bp(main['frozen_threshold']['net_mean'])}；
- 有完整对照的 {len(selected_covered)} 个：净均值 {bp(selected_covered_net)}；
- 无完整对照的 {len(selected_unmatched)} 个：净均值 {bp(selected_unmatched_net)}；
- 8 组已配对样本的事件减随机对照平均为
  {bp(main['matched_control']['mean_event_minus_control'])}，并非 8/8 为正。

因此“q90 总体略正”不能当作成功：正数主要来自无法按预注册标准配对的事件，
`matched_controls_cover_every_selected_event=false` 与 `beats_matched_controls_every_assignment=false` 都是实质失败。

## 数据范围与指标定义

| 项目 | 数值 |
|---|---:|
| 冻结 L1 原始框 | {dataset['candidate_rows']:,} |
| 分方向重聚类 episode | {dataset['side_homogeneous_episodes']:,}（LONG {dataset['side_episode_counts']['long']:,} / SHORT {dataset['side_episode_counts']['short']:,}） |
| 完整标签行 | {dataset['rows_out']:,}（不可用 outcome {dataset['reject_reasons'].get('outcome_unavailable', 0):,}） |
| 原图逐像素校验 | {dataset['pixel_parity']['passed']:,} / {dataset['pixel_parity']['checked']:,}，失败 {dataset['pixel_parity']['failed']} |
| W18 / W19 | {dataset['window_counts']['18.0']:,} / {dataset['window_counts']['19.0']:,} |
| 依赖块 | {dataset['dependency_blocks']:,} |
| train / tune / final 全量行 | {dataset['split_counts']['train']:,} / {dataset['split_counts']['tune']:,} / {dataset['split_counts']['final_validation']:,} |
| final 独立块 | {main['final_validation']['n']:,}（LONG {training['models']['long']['splits']['final_validation']:,} / SHORT {training['models']['short']['splits']['final_validation']:,}） |
| 决策时间范围 | {min_time.isoformat()} → {max_time.isoformat()} |
| 最大标签暴露末端 | {max_exposure.isoformat()}（holdout 从 {prereg['source']['holdout_start']} 开始） |
| 模型输入 | {training['feature_count']} 个精确短窗可见坐标特征 |
| 标签 | TP {prereg['outcome']['tp_atr_multiple']} ATR / SL {prereg['outcome']['sl_atr_multiple']} ATR / {prereg['outcome']['horizon_bars']} 根 / next-open |
| 成本 | {pct(prereg['outcome']['round_trip_cost_fraction'])} 往返 |
| holdout / 网络读取 | 0 / 0 |

`top-decile` 是按各方向 tune 分布经验百分位合并后最高 10%；`q90` 是 LONG、SHORT 各自在 tune
分数上固定第 90 百分位阈值，再原样应用到 final。置换检验固定模型分数、随机打乱 final 收益
10,000 次，检验真实 top-decile 毛收益是否显著更高。AUC 只把 TP-first 当诊断标签，不是收益回归成功门。

## 模型与时间纪律

- 原候选账本 SHA-256：`{dataset['candidate_ledger_sha256']}`；没有重扫 L1，也没有改 confidence/NMS。
- LONG/SHORT 按 `symbol + side` 重新聚类，19 个旧混方向 episode 不再混成一条训练记录。
- 决策时刻是完整 W18/W19 最后一根收盘；标签未来仅用于监督，完整 72 根 exposure 在调用 labeler
  **之前**检查，超过 2026-05-04 会 fail closed。
- 训练、tune、final 按时间切分，保留 60 小时 purge；直接或传递重叠的同币事件只取依赖块首条。
- 六条可见 SMA/EMA 20/60/120 本身会因定义而总结窗口前 close；这是 L1 图里真实可见状态，已在
  prereg 明示。没有额外的 48/96/168 根原始 K、volume、symbol、EMA200、旧 global-context 特征或
  后续 episode 最大 confidence 进入模型。
- LightGBM 固定 CPU、单线程、deterministic 与全部 seed；没有参数搜索，阈值只来自 tune。

## 特征贡献不是因果解释

| 方向 | 排名 | 特征 | gain |
|---|---:|---|---:|
{_importance_rows(training)}

这些 gain 只描述少数树如何切分当前训练数据。尤其 LONG 只有 1 棵树，不能把
`t03_ema120_y` 或框横坐标解释成稳定交易因子；SHAP 或更复杂解释也无法把一个未通过验证的模型变成有效模型。

## 预注册门

| 门 | 结果 |
|---|---:|
{gate_rows}

总判定：**FAIL**。校验回执 `passed={str(bool(verify['passed'])).lower()}`，共
{len(verify['checks'])} 项全部为真；失败来自模型证据，不是文件、标签或渲染校验坏掉。

## 为什么“图看起来不错”与“模型失败”可以同时成立

1. **L1 与 L2 的真值不同。** 这 3,827 个 episode 本来就是 L1 提出的局部形态，抽出来看大多像目标并不意外；L2 学的是后来赚不赚钱。
2. **同一小窗缺少全局阶段信息。** 18/19 根足够描述红框附近，却看不到更早趋势是否已经走完、上方阻力、波动所处阶段等。旧 168 根模型也没过门，说明“加长上下文”本身同样不是答案。
3. **收益噪声大于可见形态差异。** TP5/SL2/72 会受到候选之后市场 beta、跨币共振和波动路径影响；视觉上近似的框可以走出相反结果。
4. **当前表征维度过高、样本有限。** 238 维对 480/647 个训练独立块，导致 LONG 第 1 轮就停止并产生大量分数并列。
5. **完成形态不等于新鲜入场。** L1 已看核心后确认 K；本轮可用时间仍是完整检测窗右端，不能冒充 tip/tip-1/tip-2 实盘信号。

## 限制、稳健性与诚实声明

- final validation 已被本配置消耗一次用于裁决；不能在这里删 LONG、降维、改阈值或换参数后再次宣称独立验证。
- 54 币来自现存深历史文件，仍有 cohort/生存者偏差；匹配随机对照缓解但不能消除。
- 354/673 的严格控制覆盖不足，且入选组仅 32/61 完整覆盖；报告将其判为失败，不做乐观外推。
- 两张完全相同的像素图对应不同方向框，但它们被纳入同一跨方向 dependency block，未重复当独立证据。
- 本轮没有读取 ≥2026-05-04 holdout，没有改 TP/SL/期限/成本，没有 promote、部署、改 ACTIVE/frozen/forward、发 Telegram 或下单。
- 本机仓库级环境门另报告 FastAPI/OpenCV/PyYAML 与最新锁文件不一致；本轮训练所冻结的 LightGBM/NumPy/pandas/scikit-learn/SciPy 版本全部吻合。该环境差异没有被静默修依赖。

## 下一步：保留回归架构，但淘汰本配置

1. **当前两个模型均不启用。** LONG 明确负向；SHORT 的正数是本轮 final 上的事后分方向观察，不能单独 promote。
2. **若继续经济 L2，必须另立预注册。** 单变量优先考虑大幅降维/正则化，而不是在本 final 上调 q90；继续保持多空分开，并用新的未见时间段或前向样本验收。
3. **若目标是修正“局部图好、全局图差”，那是 L1.5 形态质量任务。** 它需要 Owner 的全局好/坏标签，不能拿未来收益自动代替形态真值；经济 L2 回归仍放在其后。
4. **不要现在消耗 holdout。** 当前 pre-holdout 已明确 FAIL，没有理由用最终 holdout 给失败配置补一次机会。

## 仍需回答的问题

- SHORT 的探索性正收益能否在完全新的、预注册的时间段复现？
- 只保留少量、可解释且从 W18/W19 直接计算的特征，是否能避免 LONG 的 1-tree 退化？
- Owner 所说的“全局不对”应具体拆成哪些形态标签，才能构建不依赖未来收益的 L1.5 Gold？

## 复现命令

```bash
git checkout {training['source_commit']}
python3 -m scripts.research_15m_ma_launch_l2_short_window_side_split --build-dataset
python3 -m scripts.research_15m_ma_launch_l2_short_window_side_split --train-evaluate
python3 -m scripts.research_15m_ma_launch_l2_short_window_side_split --render
python3 -m scripts.research_15m_ma_launch_l2_short_window_side_split --verify
git checkout {builder_commit}
python3 scripts/build_15m_ma_launch_l2_short_window_side_split_report.py
python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l2_short_window_side_split_20260901.md --out-dir analysis/html
```
"""


def main() -> int:
    evidence = validate_evidence()
    dataset = evidence["dataset"]
    training = evidence["training"]
    render = evidence["render"]
    dataset_frame = pd.read_csv(repo_path(dataset["dataset_path"]))
    scored = pd.read_csv(repo_path(training["scored_validation_path"]))
    scored = scored[bool_series(scored["dependency_representative"])].copy()
    controls = pd.read_csv(repo_path(dataset["matched_controls_path"]))
    manifest = pd.read_csv(repo_path(render["manifest_path"]))
    overview = make_overview(manifest, OVERVIEW_PATH)
    metric_chart = make_metric_chart(training, METRIC_CHART_PATH)
    gallery = make_gallery(manifest, GALLERY_PATH)
    builder_commit = git_head()
    report = build_markdown(
        evidence,
        dataset_frame,
        scored,
        controls,
        overview,
        metric_chart,
        gallery,
        builder_commit=builder_commit,
    )
    REPORT_PATH.write_text(report, encoding="utf-8")
    subprocess.run(
        [
            "python3",
            "scripts/md_to_html.py",
            repo_relative(REPORT_PATH),
            "--out-dir",
            "analysis/html",
        ],
        cwd=ROOT,
        check=True,
    )
    if not HTML_PATH.is_file():
        raise ReportError("markdown converter did not create the expected HTML")
    html_text = HTML_PATH.read_text(encoding="utf-8")
    required_sections = (
        "技术结论：局部形态输入没有学到稳定的未来收益排序",
        "经济结果图：总体不显著，LONG 与 SHORT 方向相反",
        "严格随机对照揭示 q90 正收益由缺配样本驱动",
        "限制、稳健性与诚实声明",
        "下一步：保留回归架构，但淘汰本配置",
    )
    if any(section not in html_text for section in required_sections):
        raise ReportError("rendered HTML is missing one or more required sections")
    if html_text.count("data:image/png;base64,") < 2:
        raise ReportError("rendered HTML did not embed both report images")
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "source_commit": builder_commit,
        "source_hashes": evidence["source_hashes"],
        "report_path": repo_relative(REPORT_PATH),
        "report_sha256": sha256_file(REPORT_PATH),
        "html_path": repo_relative(HTML_PATH),
        "html_sha256": sha256_file(HTML_PATH),
        "gallery": gallery,
        "overview": overview,
        "metric_chart": metric_chart,
        "html_embedded_png_count": html_text.count("data:image/png;base64,"),
        "required_sections_present": True,
        "holdout_consumed": False,
        "production_eligible": False,
    }
    REPORT_RECEIPT_PATH.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
