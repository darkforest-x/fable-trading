#!/usr/bin/env python3
"""Build the owner-facing report for the frozen five-model all-universe scan.

This is an observational detector-output report, not a return backtest.  Its
zero hypothesis is therefore about *proposal alignment*: circularly rotate one
model's core endpoints within each same-symbol UTC day while preserving each
model's episode count, direction and within-day spacing.  That makes an
observed same-time overlap interpretable without inventing a trading metric.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.scan_15m_ma_launch_model_compare_all3d import (
    EXPECTED_MODEL_KEYS,
    EXPERIMENT_ID,
    ModelCompareError,
    _pairwise_overlap,
    load_preregistration,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "analysis" / "output" / "ma_launch_model_compare_all3d_20260831_v1"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
DEFAULT_REPORT = ROOT / "analysis" / "p1_15m_ma_launch_five_model_alluniverse_20260831.md"

SHORT_NAMES = {
    "legacy_t3_10k_960": "旧 t-3\n960",
    "legacy_t3_10k_1280": "旧 t-3\n1280",
    "legacy_owner_10k_neg30k_960": "旧 10k+30k\n960",
    "grade_a8k_neg24k_epoch6_960": "A级 8k+24k\ne6 960",
    "grade_a8k_neg24k_full40_1280": "A级 8k+24k\nfull40 1280",
}


def read_json(path: Path) -> dict[str, Any]:
    """Read an evidence receipt or fail with its precise missing path."""

    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    """Return one streaming file identity for report lineage."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def markdown_table(headers: Sequence[str], rows: Iterable[Sequence[object]]) -> str:
    """Render a compact pipe table from values controlled by this builder."""

    def cell(value: object) -> str:
        return str(value).replace("|", "\\|").replace("\n", "<br>")

    output = ["| " + " | ".join(map(cell, headers)) + " |"]
    output.append("| " + " | ".join("---" for _ in headers) + " |")
    output.extend("| " + " | ".join(map(cell, row)) + " |" for row in rows)
    return "\n".join(output)


def load_episodes(out: Path, model_key: str) -> list[dict[str, Any]]:
    """Load one locally retrieved episode ledger and reject missing evidence."""

    path = out / "models" / model_key / "episodes.csv"
    if not path.is_file():
        raise FileNotFoundError(f"missing collected episode ledger: {path}")
    return pd.read_csv(path).to_dict("records")


def _shifted_episode(row: Mapping[str, Any], offset: int) -> dict[str, Any]:
    """Circularly shift only an endpoint within its own 96-bar UTC day.

    This null keeps the number of episodes, their directions, symbols, dates
    and within-day arrangement intact.  It intentionally alters no model
    confidence or image and is never used for detection or model selection.
    """

    timestamp = pd.Timestamp(row["core_end_time"])
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    local_bar = int((timestamp - timestamp.floor("D")) / pd.Timedelta(minutes=15))
    if not 0 <= local_bar < 96:
        raise ModelCompareError(f"core endpoint outside UTC day: {timestamp}")
    shifted = dict(row)
    shifted["core_end_i"] = int(row["core_end_i"]) - local_bar + (local_bar + offset) % 96
    return shifted


def alignment_null(
    left: Sequence[Mapping[str, Any]], right: Sequence[Mapping[str, Any]]
) -> dict[str, float | int]:
    """Compare observed alignment with all 95 non-identity daily rotations."""

    actual = _pairwise_overlap(left, right)
    null_matches: list[int] = []
    null_jaccards: list[float] = []
    for offset in range(1, 96):
        rotated = [_shifted_episode(row, offset) for row in right]
        value = _pairwise_overlap(left, rotated)
        null_matches.append(int(value["time_matched_within_one_bar"]))
        null_jaccards.append(float(value["proposal_jaccard"]))
    observed = int(actual["time_matched_within_one_bar"])
    return {
        "actual_matches": observed,
        "actual_jaccard": float(actual["proposal_jaccard"]),
        "null_mean_matches": float(np.mean(null_matches)),
        "null_max_matches": int(max(null_matches, default=0)),
        "null_mean_jaccard": float(np.mean(null_jaccards)),
        "alignment_p_ge_actual": float((sum(item >= observed for item in null_matches) + 1) / (len(null_matches) + 1)),
    }


def _episode_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Calculate count, direction and within-model confidence descriptions."""

    confidence = np.asarray([float(row["confidence"]) for row in rows], dtype=float)
    classes = Counter(str(row["class_name"]) for row in rows)
    days = {(str(row["day"])[:10], str(row["symbol"])) for row in rows}
    return {
        "episodes": len(rows),
        "long": int(classes["dense_long"]),
        "short": int(classes["dense_short"]),
        "symbol_days": len(days),
        "confidence_mean": float(np.mean(confidence)) if len(confidence) else float("nan"),
        "confidence_median": float(np.median(confidence)) if len(confidence) else float("nan"),
        "confidence_p10": float(np.quantile(confidence, 0.10)) if len(confidence) else float("nan"),
        "confidence_p90": float(np.quantile(confidence, 0.90)) if len(confidence) else float("nan"),
    }


def plot_overview(
    *,
    summary: pd.DataFrame,
    pairs: pd.DataFrame,
    destination: Path,
) -> None:
    """Render a chart contract: counts, class mix, and episode-set agreement."""

    keys = list(EXPECTED_MODEL_KEYS)
    display = [SHORT_NAMES[key] for key in keys]
    indexed = summary.set_index("model_key").reindex(keys)
    x = np.arange(len(keys))
    fig = plt.figure(figsize=(18, 11), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, width_ratios=(1.1, 1.0), height_ratios=(1, 1))
    ax_classes = fig.add_subplot(grid[0, 0])
    ax_volume = fig.add_subplot(grid[1, 0])
    ax_heat = fig.add_subplot(grid[:, 1])

    ax_classes.bar(x, indexed["long_episodes"], label="LONG", color="#1f9d74")
    ax_classes.bar(
        x,
        indexed["short_episodes"],
        bottom=indexed["long_episodes"],
        label="SHORT",
        color="#df4c5c",
    )
    for idx, total in enumerate(indexed["episodes"]):
        ax_classes.text(idx, total + max(1, max(indexed["episodes"]) * 0.015), str(int(total)), ha="center", fontsize=10)
    ax_classes.set_title("同一冻结快照的 episode 数（非质量评分）")
    ax_classes.set_ylabel("overlap episodes")
    ax_classes.set_xticks(x, display)
    ax_classes.legend(frameon=False, ncol=2)
    ax_classes.spines[["top", "right"]].set_visible(False)

    width = 0.36
    ax_volume.bar(x - width / 2, indexed["raw_candidates"], width=width, label="结构合法候选", color="#5579c6")
    ax_volume.bar(x + width / 2, indexed["five_bar_events"], width=width, label="5-bar 去重事件", color="#9a78c5")
    ax_volume.set_title("候选 → 5-bar 去重事件（各模型自有几何合同）")
    ax_volume.set_ylabel("count")
    ax_volume.set_xticks(x, display)
    ax_volume.legend(frameon=False, fontsize=9)
    ax_volume.spines[["top", "right"]].set_visible(False)

    matrix = np.eye(len(keys), dtype=float)
    pair_index = {
        (str(row["left_model_key"]), str(row["right_model_key"])): float(row["proposal_jaccard"])
        for _, row in pairs.iterrows()
    }
    for left_index, left in enumerate(keys):
        for right_index, right in enumerate(keys):
            if left_index == right_index:
                continue
            key = (left, right) if (left, right) in pair_index else (right, left)
            matrix[left_index, right_index] = pair_index[key]
    image = ax_heat.imshow(matrix, cmap="Blues", vmin=0.0, vmax=1.0)
    ax_heat.set_title("同币同日核心右端±1根的 episode Jaccard")
    ax_heat.set_xticks(range(len(keys)), display, rotation=30, ha="right")
    ax_heat.set_yticks(range(len(keys)), display)
    for i in range(len(keys)):
        for j in range(len(keys)):
            color = "white" if matrix[i, j] > 0.55 else "#17243a"
            ax_heat.text(j, i, f"{matrix[i, j]:.3f}", ha="center", va="center", color=color, fontsize=9)
    fig.colorbar(image, ax=ax_heat, fraction=0.05, pad=0.04, label="Jaccard")
    fig.suptitle("五个冻结 15m MA 启动检测器：近三日全 OKX USDT 永续对照", fontsize=18, fontweight="bold")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=180, facecolor="white")
    plt.close(fig)


def build_report(*, out: Path, results: Path, report: Path) -> dict[str, Any]:
    """Validate post-render evidence, create overview/null artifacts, then write Markdown."""

    prereg = load_preregistration()
    scan = read_json(results / "scan_receipt.json")
    render = read_json(results / "render_receipt.json")
    qa = read_json(results / "qa_receipt.json")
    if scan.get("experiment_id") != EXPERIMENT_ID or render.get("experiment_id") != EXPERIMENT_ID:
        raise ModelCompareError("scan/render experiment identity drifted")
    if qa.get("passed") is not True:
        raise ModelCompareError("refusing report: pixel QA did not pass")
    if set(scan.get("models", {})) != set(EXPECTED_MODEL_KEYS):
        raise ModelCompareError("scan receipt model ordering drifted")
    summary_path = results / "model_summary.csv"
    pairs_path = results / "pairwise_episode_overlap.csv"
    summary = pd.read_csv(summary_path)
    pairs = pd.read_csv(pairs_path)
    if tuple(summary["model_key"]) != EXPECTED_MODEL_KEYS:
        raise ModelCompareError("model summary ordering drifted")
    episodes = {key: load_episodes(out, key) for key in EXPECTED_MODEL_KEYS}
    episode_stats = {key: _episode_summary(rows) for key, rows in episodes.items()}
    total_symbol_days = int(scan["usable_symbol_days"])
    null_rows: list[dict[str, Any]] = []
    for left_index, left in enumerate(EXPECTED_MODEL_KEYS):
        for right in EXPECTED_MODEL_KEYS[left_index + 1 :]:
            observed = _pairwise_overlap(episodes[left], episodes[right])
            null = alignment_null(episodes[left], episodes[right])
            if int(observed["time_matched_within_one_bar"]) != int(null["actual_matches"]):
                raise ModelCompareError("null helper disagrees with pairwise overlap")
            null_rows.append(
                {
                    "left_model_key": left,
                    "right_model_key": right,
                    **observed,
                    **null,
                }
            )
    null_path = results / "pairwise_alignment_null.csv"
    pd.DataFrame(null_rows).to_csv(null_path, index=False)
    overview = results / "overview.png"
    plot_overview(summary=summary, pairs=pairs, destination=overview)

    by_key = {str(spec["key"]): spec for spec in prereg["models"]}
    model_rows: list[list[object]] = []
    for _, row in summary.iterrows():
        key = str(row["model_key"])
        detector = by_key[key]["detector"]
        stats = episode_stats[key]
        model_rows.append(
            [
                by_key[key]["display_name"],
                detector["imgsz"],
                "/".join(map(str, detector["window_lengths"])),
                "/".join(map(str, detector["mapped_core_length_bars_allowed"])),
                "/".join(map(str, detector["mapped_confirmation_bars_allowed"])),
                int(row["raw_candidates"]),
                int(row["five_bar_events"]),
                int(row["episodes"]),
                f"{stats['long']} / {stats['short']}",
                f"{int(row['episodes']) / total_symbol_days * 100:.2f}",
                by_key[key]["holdout_consumption_number_for_this_configuration"],
            ]
        )
    pair_rows = [
        [
            SHORT_NAMES[str(item["left_model_key"])].replace("\n", " "),
            SHORT_NAMES[str(item["right_model_key"])].replace("\n", " "),
            int(item["time_matched_within_one_bar"]),
            int(item["same_direction_matches"]),
            int(item["direction_flip_matches"]),
            f"{float(item['proposal_jaccard']):.3f}",
            f"{float(item['null_mean_matches']):.2f}",
            int(item["null_max_matches"]),
            f"{float(item['alignment_p_ge_actual']):.3f}",
        ]
        for item in null_rows
    ]
    confidence_rows = [
        [
            SHORT_NAMES[key].replace("\n", " "),
            f"{stats['confidence_p10']:.3f}",
            f"{stats['confidence_median']:.3f}",
            f"{stats['confidence_p90']:.3f}",
            f"{stats['confidence_mean']:.3f}",
        ]
        for key, stats in episode_stats.items()
    ]
    chart_rows = [
        [
            key,
            next(item for item in render["models"] if item["model_key"] == key)["documents"],
            next(item for item in render["models"] if item["model_key"] == key)["archive"],
        ]
        for key in EXPECTED_MODEL_KEYS
    ]
    complete_days = ", ".join(pd.Timestamp(value).strftime("%Y-%m-%d") for value in scan["complete_days"])
    report_relative_overview = f"../experiments/active/{EXPERIMENT_ID}/results/overview.png"
    report_text = f"""# 五个 15m 均线密集检测模型：近三天全币种冻结对照（2026-09-01）

## 技术结论

已在**同一份冻结的 OKX USDT 永续快照**上运行五个讨论过的 YOLO 权重。范围是 {complete_days} UTC 三个完整日的全部可用 current-live crypto USDT-SWAP：**{scan['usable_unique_symbols']} 个币、{total_symbol_days} 个币日、每币 {read_json(results / 'fetch_receipt.json')['required_rows_per_usable_symbol']} 根连续且已确认的 15m K 线**。五个模型的源 OHLCV 文件逐字节相同；差异只来自各权重及其自己历史训练支持的窗口/核心/确认合同。

这是一份**形态提案输出对照**，不是收益回测，也没有宣称谁“最好”。总信号数、置信度和静态 mAP 都不能跨模型直接排优劣：旧 t-3、旧 10k+3万负样本、Grade-A 两臂的窗口与确认长度本来不同。最有用的可比信息是：同一币、同一天、核心右端相差不超过一根 15m K 时，它们是否还指向同一个 episode，是否方向一致。

## 范围、模型与输出

{markdown_table(['模型', '原生 imgsz', '窗口 W', '核心根数', '确认根数', '合法候选', '5-bar 事件', 'episode', 'LONG / SHORT', '每100币日 episode', '本配置 holdout 使用'], model_rows)}

固定条件：`conf=0.25`、NMS IoU `0.70`、原模型的 normalized `cx/cy/w/h` 框保留、每个模型内部按同币同日重叠区间合并 episode、每张复盘图只有其代表原框。没有根据结果删信号、移动框或改变阈值。

![五模型输出数量与一致性总览]({report_relative_overview})

上图左侧的数是输出密度，**不是 precision 或盈利能力**；右侧 Jaccard 才是在定义一致的同币同日核心时点上，两个模型看到同一形态的比例。

## 逐对 episode 身份一致性

{markdown_table(['左模型', '右模型', '时点重合', '同向', '反向', 'Jaccard', '旋转零假设均值', '零假设最大', 'p(零假设≥实际)'], pair_rows)}

零假设不是收益或价格结果：对每一个右侧模型，在每个币的每个 UTC 日内把核心右端做 1–95 根的循环位移，保留它的 episode 数、方向、置信度、同日间距和币种分布，再重新计算同一时点的重合。表中的 `p` 是 95 种非零位移中“重合数至少与实际一样多”的比例（加一校正）。它只回答“两个模型的相同时间提案是否超过偶然错位”，不回答形态是否盈利或标注是否 Gold。

## 置信度：仅供每个模型内部排序

{markdown_table(['模型', 'P10', '中位数', 'P90', '均值'], confidence_rows)}

这些数不能横向理解为“哪个模型更自信/更准确”。YOLO 分数没有跨 checkpoint 的统一校准；例如较低的分数可能只是训练数据、分辨率或负样本构成不同。

## 高清全景图

以下是模型实际输出的每个 overlap episode 的一图一框、1920×1400 全景文件；右下角内嵌了送入模型的精确 1280×742 像素输入。所有图按模型分别打包，未做人工抽删。

{markdown_table(['模型键', '图数', '完整 ZIP'], chart_rows)}

对应的逐图 manifest 在同一模型目录下；可按 `episode_id` 追溯到 `accepted_candidates.csv`、`episodes.csv` 和原始四坐标预测框。

## 完整性、复现与 QA

- 一次网络读取只发生在快照阶段；之后 5 个模型扫描、高清渲染、QA 全部为离线读取。
- Fetch receipt SHA-256：`{sha256_file(results / 'fetch_receipt.json')}`；扫描 receipt SHA-256：`{sha256_file(results / 'scan_receipt.json')}`。
- 像素 QA：{qa['exact_model_inputs']} 个实际模型输入、{qa['exact_pixel_rerenders']} 张全景重渲、{qa['exact_png_hash_matches']} 个 PNG 哈希都通过；全局独立 PNG 为 {qa['unique_chart_pngs']}。
- 无模型训练、微调、阈值/权重变更、ACTIVE/frozen 变更、promote、部署、forward 写入、Telegram 发送或下单。

复现顺序（市场读取已经冻结，后两步完全离线）：

```bash
cd {ROOT}

OUT=analysis/output/ma_launch_model_compare_all3d_20260831_v1
RESULTS=experiments/active/{EXPERIMENT_ID}/results

# 复核高清图，不会重新抓取或再跑模型。
PYTHONPATH=. .venv/bin/python scripts/scan_15m_ma_launch_model_compare_all3d.py \\
  --verify --out "$OUT" --results "$RESULTS"

# 重新生成图表、零假设和 Markdown；再转成可直接打开的 HTML。
PYTHONPATH=. .venv/bin/python scripts/build_15m_ma_launch_model_compare_all3d_report.py \\
  --out "$OUT" --results "$RESULTS"
python3 scripts/md_to_html.py analysis/p1_15m_ma_launch_five_model_alluniverse_20260831.md \\
  --out-dir analysis/html
```

## 风险与诚实声明

- 所有币种是扫描时 `state=live` 的当前 universe，因此含有幸存者偏差；它不是历史时点可交易的 universe。
- 这些 detector 保留了各自历史的 post-core 确认要求；它们是 completed-history 形态检索，不能冒充 tip/tip-1/tip-2 实盘信号。
- 本轮不含收益、胜率、AUC、top-decile、匹配随机入场或置换收益指标。那些对纯 detection parity 报告不适用，不能用“候选数更多”代替。
- 横向窗口合同不一致是有意保留的历史事实，不是本轮调优。因此本报告不从数量、置信度或静态 val mAP 推出模型排名。
- 本轮的唯一新近三日 holdout 使用已按每个 checkpoint 配置登记；它不是 final acceptance，也不能再被用来调阈值或挑权重。

## 下一步（需 Owner 决策）

若要从五个中选出要继续投入的一个，建议先确定一个**未读的新时间段或独立 Owner Gold 集**，并预注册单一裁决标准（形态级 precision/recall、跨分辨率稳定性或因果 tip 任务中的一个），而不是回看这一轮哪一个“看起来更多/更像”。当前这份对照只负责把全部输出、原框、高清图和一致性证据固定下来。
"""
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(report_text, encoding="utf-8")
    receipt = {
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report": report.relative_to(ROOT).as_posix(),
        "report_sha256": sha256_file(report),
        "overview": overview.relative_to(ROOT).as_posix(),
        "overview_sha256": sha256_file(overview),
        "alignment_null_csv": null_path.relative_to(ROOT).as_posix(),
        "alignment_null_sha256": sha256_file(null_path),
        "scan_receipt_sha256": sha256_file(results / "scan_receipt.json"),
        "render_receipt_sha256": sha256_file(results / "render_receipt.json"),
        "qa_receipt_sha256": sha256_file(results / "qa_receipt.json"),
        "economic_backtest": False,
        "training_or_tuning": False,
        "threshold_or_weight_changed": False,
        "active_or_frozen_changed": False,
        "promoted": False,
        "deployed": False,
        "forward_state_changed": False,
        "orders_placed": False,
        "telegram_sent": False,
        "training_eligible": False,
        "production_eligible": False,
    }
    receipt_path = results / "report_build_receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    """Parse explicit output locations and generate one report once QA exists."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    receipt = build_report(out=args.out, results=args.results, report=args.report)
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
