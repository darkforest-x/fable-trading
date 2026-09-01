#!/usr/bin/env python3
"""Build the owner-facing L2 global-context report, overview and gallery.

This builder is downstream-only: it reads frozen receipts, the scored final
pre-holdout ledger and chart manifest.  It never imports a detector, trains or
scores a model, reads holdout bars, changes thresholds, promotes, deploys,
mutates forward state, sends Telegram, or places orders.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import cv2
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l2-global-context-v1"
DEFAULT_PREREG = ROOT / "experiments" / "active" / EXPERIMENT_ID / "preregistration.json"
DEFAULT_RESULTS = ROOT / "experiments" / "active" / EXPERIMENT_ID / "results"
DEFAULT_OUT = ROOT / "analysis" / "output" / "ma_launch_l2_global_context_v1"
DEFAULT_REPORT = ROOT / "analysis" / "p3_15m_ma_launch_l2_global_context_20260901.md"
DEFAULT_HTML_DIR = ROOT / "analysis" / "html"


class ReportError(RuntimeError):
    """Fail closed when a required receipt or chart identity is missing."""


def read_json(path: Path) -> dict[str, Any]:
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


def require_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise ReportError(f"missing {label}: {path}")
    actual = sha256_file(path)
    if actual != expected:
        raise ReportError(f"{label} hash drifted: {actual} != {expected}")


def pct(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{100 * float(value):.{digits}f}%"


def bp(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{10_000 * float(value):+.{digits}f} bp"


def number(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{float(value):.{digits}f}"


def phase_commit_lineage(
    snapshot: Mapping[str, Any],
    scan: Mapping[str, Any],
    dataset: Mapping[str, Any],
    training: Mapping[str, Any],
) -> dict[str, str]:
    """Return and validate the committed identity of every experiment phase."""

    commits = {
        "snapshot": str(snapshot.get("source_commit", "")),
        "scan": str(scan.get("source_commit", "")),
        "dataset": str(dataset.get("source_commit", "")),
        "training": str(training.get("source_commit", "")),
    }
    invalid = {
        phase: commit
        for phase, commit in commits.items()
        if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit)
    }
    if invalid:
        raise ReportError(f"invalid phase source commit identities: {invalid}")
    return commits


def make_overview(manifest: pd.DataFrame, output: Path) -> dict[str, Any]:
    """Compose up to six views per decision group without altering sources.

    A failed frozen threshold can legitimately leave fewer than six KEEP rows
    (or, in the opposite extreme, fewer than six REJECT rows).  The report is
    evidence for that outcome, so sparse groups must shrink the overview rather
    than make the failure impossible to deliver.
    """

    kept = manifest[manifest["group"] == "kept"].head(6)
    rejected = manifest[manifest["group"] == "rejected_high_l1"].head(6)
    selected = pd.concat([kept, rejected], ignore_index=True)
    if selected.empty:
        raise ReportError("overview requires at least one delivered chart")
    tile_w, tile_h = 640, 417
    header_h = 58
    columns = min(3, len(selected))
    tile_rows = (len(selected) + columns - 1) // columns
    canvas = np.full(
        (header_h + tile_h * tile_rows, tile_w * columns, 3),
        245,
        dtype=np.uint8,
    )
    summary = f"KEEP {len(kept)} | REJECT {len(rejected)}"
    cv2.putText(
        canvas,
        f"L2 global-context decision views: {summary}",
        (22, 38),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.85,
        (25, 25, 25),
        2,
        cv2.LINE_AA,
    )
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(selected.to_dict("records")):
        path = repo_path(row["chart_path"])
        require_hash(path, str(row["chart_png_sha256"]), "source chart")
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            raise ReportError(f"could not decode chart: {path}")
        tile = cv2.resize(image, (tile_w, tile_h), interpolation=cv2.INTER_AREA)
        y, x = header_h + (index // columns) * tile_h, (index % columns) * tile_w
        canvas[y : y + tile_h, x : x + tile_w] = tile
        cv2.rectangle(canvas, (x, y), (x + tile_w - 1, y + tile_h - 1), (90, 90, 90), 1)
        rows.append(
            {
                "episode_id": str(row["episode_id"]),
                "group": str(row["group"]),
                "source_chart": str(row["chart_path"]),
                "source_sha256": str(row["chart_png_sha256"]),
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), canvas, [cv2.IMWRITE_PNG_COMPRESSION, 3]):
        raise ReportError(f"could not write overview: {output}")
    return {
        "path": output.resolve().relative_to(ROOT.resolve()).as_posix(),
        "sha256": sha256_file(output),
        "width": int(canvas.shape[1]),
        "height": int(canvas.shape[0]),
        "sources": rows,
    }


def make_gallery(manifest: pd.DataFrame, output: Path, *, title: str) -> dict[str, Any]:
    """Write one uncompressed-folder browser for every high-resolution chart."""

    cards: list[str] = []
    for row in manifest.to_dict("records"):
        source = repo_path(row["chart_path"])
        require_hash(source, str(row["chart_png_sha256"]), "gallery chart")
        relative = Path(os.path.relpath(source, output.parent)).as_posix()
        state = "KEEP" if bool(row["l2_keep"]) else "REJECT"
        cards.append(
            "<article class='card'>"
            f"<a href='{html.escape(relative)}'><img loading='lazy' src='{html.escape(relative)}' "
            f"alt='{html.escape(str(row['episode_id']))}'></a>"
            f"<h2>{state} · {html.escape(str(row['symbol']))} · "
            f"{html.escape(str(row['side']).upper())}</h2>"
            f"<p>{html.escape(str(row['available_at']))}<br>"
            f"L1={float(row['l1_confidence']):.3f} · L2={float(row['l2_score']):.6f} · "
            f"gate={float(row['l2_threshold']):.6f}</p>"
            "</article>"
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font:15px/1.45 system-ui,-apple-system,"PingFang SC",sans-serif;margin:0;background:#111;color:#eee}}
header{{position:sticky;top:0;z-index:2;padding:14px 20px;background:#171717eF;border-bottom:1px solid #444}}
main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(560px,1fr));gap:14px;padding:14px}}
.card{{background:#1d1d1d;border:1px solid #3b3b3b;border-radius:8px;overflow:hidden}}
img{{display:block;width:100%;height:auto;background:white}}
h2{{font-size:16px;margin:10px 12px 4px}}p{{margin:4px 12px 12px;color:#bbb}}
</style></head><body><header><strong>{html.escape(title)}</strong> · {len(cards)} 张 · 点击看原图</header>
<main>{''.join(cards)}</main></body></html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    return {"path": output.relative_to(ROOT).as_posix(), "sha256": sha256_file(output), "charts": len(cards)}


def build_markdown(
    prereg: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    scan: Mapping[str, Any],
    dataset: Mapping[str, Any],
    training: Mapping[str, Any],
    qa: Mapping[str, Any],
    overview: Mapping[str, Any],
    gallery: Mapping[str, Any],
) -> str:
    gate = training["primary_gate"]
    validation = training["final_validation"]
    selected = training["final_validation_frozen_threshold"]
    baseline = training["single_feature_baseline"]
    matched = training["matched_control"]
    runtime = training["runtime"]
    commits = phase_commit_lineage(snapshot, scan, dataset, training)
    verdict = "通过研究门" if gate["passed"] else "未通过研究门"
    overview_rel = Path(overview["path"]).relative_to("analysis").as_posix()
    control_rows = [row for row in matched["assignments"] if int(row.get("n", 0)) > 0]
    control_mean = (
        float(np.mean([row["control_net_mean"] for row in control_rows])) if control_rows else None
    )
    control_diff = matched["mean_event_minus_control"]
    split = dataset["split_counts"]
    split_blocks = dataset["split_dependency_block_counts"]
    importance = training["feature_importance_top10"]
    importance_table = "\n".join(
        f"| {index} | {row['feature']} | {float(row['gain']):.1f} |"
        for index, row in enumerate(importance, 1)
    )
    gate_table = "\n".join(
        f"| {name} | {'PASS' if value else 'FAIL'} |"
        for name, value in gate.items()
        if name != "passed"
    )
    return f"""# 15m 均线密集启动：L2 全局上下文判断层 v1

## 结论

本轮结论是：**{verdict}**。L1 继续只负责在 18/19 根局部图里提出候选；L2 在 L1 最后一根可见 K 线收盘后，读取最多 168 根历史形成的 28 个因果特征，再用调参段固定的 q90 分数门过滤。最终验证段没有参与训练、早停或阈值选择。

这不是生产启用结论。当前 L1 是完成形态检测器，已经看过核心后的 2–9 根确认 K；而且 L1 的 `best.pt` 由 2025-12 至 2026-05 的 chronological val 选择。虽然本轮剔除了所有与 L1 train/val 图片相交的候选窗口，系统级结果仍应解释为“冻结 L1 条件下的 L2 时间外排序”，不是完整滚动训练仿真。真正生产确认仍需新的前向新鲜样本。

![L2 保留与淘汰全局图总览]({overview_rel})

高清单图浏览：[{gallery['charts']} 张 L2 KEEP / REJECT 全局图]({Path(gallery['path']).name})。

## 五模型候选血缘与本轮输入

本轮不是把五个 checkpoint 的框直接混在一起。上游冻结对照是 `{prereg['five_model_lineage']['comparison_experiment']}`；其预注册、汇总和模型表均以 SHA-256 固定。本轮选择其中 `{prereg['five_model_lineage']['selected_l1_key']}` 作为唯一 L1 输入，因为它对应当前 Owner Grade-A 正负样本几何与原生 1280 合同。

另外四个模型的弱/强标签、原生分辨率、窗口、核心和确认长度、confidence 标定均不同；混池后同一个 L2 threshold 没有统一语义，也会同时改变多个变量。因此五模型结果只提供 checkpoint 血缘与视觉对照，**没有把近三日候选数量、confidence 或 holdout 表现用于选模/调参，其他四臂也没有作为 L2 特征**。

## 数据与时间纪律

| 项目 | 数值 |
|---|---:|
| 冻结币种 | {snapshot['symbols']} |
| 冻结 15m K 线 | {snapshot['rows']:,} |
| 冻结范围 | {snapshot['snapshot_start']} → {snapshot['snapshot_end_exclusive']} |
| L1 原始结构合法框 | {scan['raw_accepted_candidates']:,} |
| 跨午夜重叠 episode | {scan['overlap_episodes']:,} |
| L2 可用 episode | {dataset['rows_out']:,} |
| 完整暴露依赖块 | {dataset['dependency_blocks']:,}（最大块 {dataset['maximum_dependency_block_events']:,} 个 episode） |
| train / tune / final val episode | {split.get('train', 0):,} / {split.get('tune', 0):,} / {split.get('final_validation', 0):,} |
| train / tune / final val 独立块 | {split_blocks.get('train', 0):,} / {split_blocks.get('tune', 0):,} / {split_blocks.get('final_validation', 0):,} |
| matched-control 行 | {dataset['matched_controls']:,}（{matched['usable_assignment_count']} / {matched['required_assignment_count']} 个分配可用） |
| LightGBM / NumPy / pandas | {runtime['packages']['lightgbm']} / {runtime['packages']['numpy']} / {runtime['packages']['pandas']} |
| 确定性训练 | CPU · deterministic=true · force_col_wise=true · num_threads=1 |
| 冻结快照 commit | `{commits['snapshot']}` |
| 远端 L1 扫描 commit | `{commits['scan']}` |
| L2 数据集 commit | `{commits['dataset']}` |
| L2 训练评估 commit | `{commits['training']}` |
| holdout 读取 | 0 |

信号时钟固定为：`window_end_time` 是 L1 最后一根可见 K 的开盘时间；`available_at = window_end_time + 15min`；L2 特征只到该收盘；TP5/SL2/72 标签从 `available_at` 对应的下一根开盘开始。每个事件的完整暴露区间是 `[available_at-42h, available_at+18h)`，train→tune 与 tune→final val 各留 60 小时 purge。直接或传递重叠的同币区间属于同一依赖块；只有每块最早事件进入训练、早停、阈值选择和最终指标，后续事件只用于评分与全局图复盘。

本轮是显式多阶段血缘，不把不同提交伪装成同一个二进制：快照和远端 L1 扫描固定在上表对应 commit；完整暴露隔离、确定性训练、收据守门与报告修复随后落在数据集/训练 commit。远端回执逐项固定 L1 权重、训练 manifest、renderer、L2 feature/label builder 及候选账本 SHA-256；本地阶段重新校验这些哈希后才读取候选。扫描之后的改动不重算、筛选或调节 L1 候选。

这项 60 小时/依赖块规则是在扫描期间的代码审计中、**任何 L2 outcome、score 或收益结果生成之前**写入预注册 integrity amendment；它修复原 18 小时 label-only purge 的证据隔离缺口，没有改变 L1 权重/阈值、TP/SL、期限或成本。

LightGBM 4.6.0 的官方参数说明要求 `deterministic=true` 时同时固定 `force_col_wise` 或 `force_row_wise`，以避免潜在数值不稳定。本轮固定 CPU、`force_col_wise=true`、单线程及全部抽样 seed，并把实际 Python/平台/包版本写入训练回执。来源：https://lightgbm.readthedocs.io/en/v4.6.0/Parameters.html#deterministic

## 最终验证结果

| 口径 | n | 毛收益 | 扣 0.2% 净收益 | 胜率 | AUC | Spearman |
|---|---:|---:|---:|---:|---:|---:|
| L1 独立块首个候选 | {validation['n']} | {bp(validation['pool_gross_mean'])} | {bp(validation['pool_net_mean'])} | {pct(validation['positive_rate'])} | {number(validation['roc_auc'])} | {number(validation['spearman_score_vs_return'])} |
| L2 final top-decile | {validation['top_decile']['n']} | {bp(validation['top_decile']['gross_mean'])} | {bp(validation['top_decile']['net_mean'])} | {pct(validation['top_decile']['win_rate'])} | — | — |
| L2 冻结 tune-q90 门 | {selected['n']} | {bp(selected['gross_mean'])} | {bp(selected['net_mean'])} | {pct(selected['win_rate'])} | — | — |
| 单特征 ma_spread baseline top-decile | {baseline['top_decile']['n']} | {bp(baseline['top_decile']['gross_mean'])} | {bp(baseline['top_decile']['net_mean'])} | {pct(baseline['top_decile']['win_rate'])} | {number(baseline['roc_auc'])} | {number(baseline['spearman_score_vs_return'])} |
| 匹配随机对照（L2 选中对应） | ≥{matched['minimum_pairs_per_assignment']} / 分配 | — | {bp(control_mean)} | — | — | — |
| L2 减匹配对照 | — | — | {bp(control_diff)} | — | — | — |

Outcome permutation（固定分数、打乱收益 10,000 次）单尾 `p={training['outcome_permutation_p']:.6f}`。AUC 只作诊断，不进入成功裁决。
匹配对照若缺少任何一个预注册分配（本轮缺失：`{matched['missing_assignments']}`），会直接判门失败，不把“没有配到样本”当成胜出。

## 预注册门

| 门 | 结果 |
|---|---:|
{gate_table}

总判定：**{'PASS' if gate['passed'] else 'FAIL'}**。

## 特征贡献（gain 前 10）

| 排名 | 特征 | gain |
|---:|---|---:|
{importance_table}

## 如何理解数字变化

- 如果 L2 通过，说明“局部框像”与“全局值得做”确实可以分层：L1 保持召回，L2 用历史波动、均线间距/收敛、位置、趋势、量能和近端动量筛掉一部分全局不协调候选。结论只按完整暴露依赖块的首个因果事件计数，不把重叠行情重复算成证据。
- 如果 L2 未通过，不能靠调低阈值把结果救回来；本配置应记录为负结果。下一轮只能预注册一个新变量，例如固定 epoch 的 L1 checkpoint、不同 L2 表征或真正的新鲜前向数据。
- 匹配随机对照与 L1 候选使用相同币、月份、UTC 8 小时时段、因果 ATR 五分位、方向、障碍、期限与成本；每个控制点离任何 L1 episode 至少 72 根 K。

## 风险与诚实声明

- 54 币来自当前存在的深历史文件，存在生存者偏差；所有实验臂和随机对照使用同一 cohort，但这不能消除绝对收益偏差。
- L1 权重拟合数据止于 2025-11-29，候选期从 2026-01-01 开始；不过 `best.pt` 的 epoch 选择看过延续到 2026-05-03 的 L1 chronological val。精确重叠图已隔离，checkpoint-selection hindsight 仍存在。
- L1 是 completed-shape 研究检测器，不是 tip/tip-1/tip-2 信号。L2 的 `available_at` 是完整检测窗右端，而不是红框核心结束时间。
- 本轮没有读取 ≥2026-05-04 holdout，没有调 L1 confidence/NMS/window，没有改 TP/SL/horizon/成本，没有 promote、部署、写 forward、发 Telegram 或下单。
- 静态 pre-holdout PASS 最多允许进入新的前向验证；不能替代 100 笔新鲜前向终审。

## 复现命令

```bash
git checkout {training['source_commit']}
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --freeze-snapshot
bash scripts/run_15m_ma_launch_l2_global_context_on_3060.sh --check --batch-size 32
bash scripts/run_15m_ma_launch_l2_global_context_on_3060.sh --stage --batch-size 32
bash scripts/run_15m_ma_launch_l2_global_context_on_3060.sh --start --batch-size 32
bash scripts/run_15m_ma_launch_l2_global_context_on_3060.sh --status
# 仅在远端 scan.exit=0 且原子终态回执存在后收集候选账本：
bash scripts/run_15m_ma_launch_l2_global_context_on_3060.sh --collect
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --build-dataset
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --train-evaluate
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --render
PYTHONPATH=. .venv/bin/python scripts/research_15m_ma_launch_l2_global_context.py --verify
PYTHONPATH=. .venv/bin/python scripts/build_15m_ma_launch_l2_global_context_report.py
python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l2_global_context_20260901.md --out-dir analysis/html
```

## 下一步选项

1. 若 FAIL：停止本配置，不在 final val 上继续调阈值；另开单变量预注册。
2. 若 PASS：先跑只读前向观察，不自动 promote/deploy；何时消耗 holdout 或接入 tip 路径仍需 Owner 单独批准。
3. 若 Owner 更在意“肉眼全局形态”而非收益排序，可另建全局图分类器；它和本轮 LightGBM 经济判断是不同目标，不能混成一轮。

QA：{qa['charts_checked']} 张高清图逐图重渲染，失败 {len(qa['failures'])}；overview SHA-256 `{overview['sha256']}`；gallery SHA-256 `{gallery['sha256']}`。
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--html-dir", type=Path, default=DEFAULT_HTML_DIR)
    args = parser.parse_args()
    prereg = read_json(args.prereg)
    if prereg.get("experiment_id") != EXPERIMENT_ID:
        raise ReportError("unexpected experiment_id")
    receipts = {
        name: read_json(args.results / f"{name}_receipt.json")
        for name in ("snapshot", "scan", "dataset", "training", "render", "qa")
    }
    if not receipts["qa"].get("passed"):
        raise ReportError("QA receipt is not passing")
    manifest_path = repo_path(receipts["render"]["manifest_path"])
    require_hash(manifest_path, str(receipts["render"]["manifest_sha256"]), "chart manifest")
    manifest = pd.read_csv(manifest_path)
    manifest["l2_keep"] = (
        manifest["l2_keep"].astype(str).str.lower().map({"true": True, "false": False})
    )
    if manifest["l2_keep"].isna().any():
        raise ReportError("chart manifest contains an invalid l2_keep value")
    overview = make_overview(manifest, args.out / "l2_kept_vs_rejected_overview.png")
    gallery_path = args.html_dir / "p3_15m_ma_launch_l2_global_context_gallery_20260901.html"
    gallery = make_gallery(
        manifest,
        gallery_path,
        title="15m MA Launch L2 · Global Context Gallery",
    )
    markdown = build_markdown(
        prereg,
        receipts["snapshot"],
        receipts["scan"],
        receipts["dataset"],
        receipts["training"],
        receipts["qa"],
        overview,
        gallery,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(markdown, encoding="utf-8")
    subprocess.run(
        [
            "python3",
            str(ROOT / "scripts" / "md_to_html.py"),
            str(args.report),
            "--out-dir",
            str(args.html_dir.relative_to(ROOT)),
        ],
        cwd=ROOT,
        check=True,
    )
    html_path = args.html_dir / f"{args.report.stem}.html"
    payload = {
        "protocol": prereg["protocol"],
        "experiment_id": EXPERIMENT_ID,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "report_path": args.report.relative_to(ROOT).as_posix(),
        "report_sha256": sha256_file(args.report),
        "html_path": html_path.relative_to(ROOT).as_posix(),
        "html_sha256": sha256_file(html_path),
        "gallery": gallery,
        "overview": overview,
        "holdout_rows_read": 0,
        "production_eligible": False,
    }
    receipt_path = args.results / "report_receipt.json"
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
