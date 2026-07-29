#!/usr/bin/env python3
"""Build the reader-facing ETH 3m pilot backtest report and executed notebook."""
from __future__ import annotations

import base64
import contextlib
import io
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
OUT = PROJECT / "analysis/output/eth3m_short_pilot_v1_backtest"
REPORT_MD = PROJECT / "analysis/p_eth_3m_short_pilot_v1_backtest.md"
NOTEBOOK = PROJECT / "analysis/notebooks/eth3m_short_pilot_v1_backtest.ipynb"
ARTIFACT = OUT / "report_artifact.json"


def bp(value: float | None) -> float | None:
    return None if value is None else float(value) * 10000.0


def pct(value: float | None) -> float | None:
    return None if value is None else float(value) * 100.0


def fmt(value: float | None, digits: int = 2, signed: bool = False) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def load_inputs() -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame, pd.DataFrame]:
    summary = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    validation = json.loads((OUT / "validation.json").read_text(encoding="utf-8"))
    if validation["status"] != "pass":
        raise RuntimeError(f"validation failed: {validation['failed']}")
    daily = pd.read_csv(OUT / "daily.csv")
    signals = pd.read_csv(OUT / "signals.csv")
    return summary, validation, daily, signals


def write_markdown(summary: dict[str, Any]) -> None:
    strict = summary["replay"]["strict_oos"]
    gap = summary["replay"]["gap_replay"]
    text = f"""# ETH 3m 专用做空模型 pilot v1 — 因果回放报告

日期：2026-07-29

## 一句话结论

**本轮不通过。** 严格时序 OOS 的 774 根盘口中，模型在 772 根上画了 tip 框，原始开火率
{pct(strict['raw_fire_rate']):.2f}%；18 根去重后仍有 {strict['dedup_signals']} 笔，约
{strict['dedup_signals_per_day']:.2f} 笔/有效日。3h 做空扣 20bp 后平均
{bp(strict['net_mean_at_20bp']):+.2f}bp，匹配随机做空为
{bp(strict['matched_control_net_mean_at_20bp']):+.2f}bp，模型选择超额
{bp(strict['paired_excess_mean']):+.2f}bp。当前模型近似“持续开火”，不能进入判断层或 holdout。

## 复现命令

```bash
MPLCONFIGDIR=/private/tmp/mpl-eth3m-backtest PYTHONPATH=. .venv/bin/python \\
  scripts/backtest_eth3m_short_pilot_v1.py --device mps --batch 32 --render-workers 6
PYTHONPATH=. .venv/bin/python scripts/validate_eth3m_short_pilot_backtest.py
PYTHONPATH=. .venv/bin/python scripts/build_eth3m_short_pilot_backtest_report.py
```

## 回放口径

- 模型：`runs/detect/runs/detect/eth3m_short_pilot_v1_mac_cold/weights/best.pt`
- 数据：ETH_USDT_SWAP 3m；holdout 起点 2026-05-04 00:00 UTC。
- 每次只给模型看决策 bar 及以前的 200 根；5,398 个回放窗口与 183 张 train/val 图片的
  K 线像素交集均为 0。
- 严格 OOS：最后一张 train/val 图片之后再经过完整 200 根像素隔离，2026-05-02 06:18
  至 2026-05-03 20:57 UTC，共 774 根；这是主结果。
- 间隙回放：训练日历之间、但与训练图片像素零重叠的 5,398 根；仅作辅助诊断。
- 固定 conf=0.30、tip/tip-1 门、18 根去重；决策 bar 收盘后识别，下一根 open 入场，
  60 根后 close 出场（3h），短收益 `1 - exit_close / entry_open`，扣固定 0.20% 往返成本。
- 随机对照严格匹配 ETH × 同一零重叠 run × ATR 五分位，每信号最多 3 个；不允许降级。

## 结果表

| 回放范围 | 有效 bars | 原始开火 | 开火率 | 去重信号 | 信号/日 | 模型净均值 | 随机净均值 | 匹配超额 | 净胜率 | 净 PF | 块置换 p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 严格 OOS | {strict['eligible_bars']} | {strict['raw_fires']} | {pct(strict['raw_fire_rate']):.2f}% | {strict['dedup_signals']} | {strict['dedup_signals_per_day']:.2f} | {bp(strict['net_mean_at_20bp']):+.2f}bp | {bp(strict['matched_control_net_mean_at_20bp']):+.2f}bp | {bp(strict['paired_excess_mean']):+.2f}bp | {pct(strict['net_win_rate_at_20bp']):.2f}% | {strict['net_profit_factor_at_20bp']:.3f} | {strict['block_signflip_p_one_sided']:.3f} |
| 间隙回放 | {gap['eligible_bars']} | {gap['raw_fires']} | {pct(gap['raw_fire_rate']):.2f}% | {gap['dedup_signals']} | {gap['dedup_signals_per_day']:.2f} | {bp(gap['net_mean_at_20bp']):+.2f}bp | {bp(gap['matched_control_net_mean_at_20bp']):+.2f}bp | {bp(gap['paired_excess_mean']):+.2f}bp | {pct(gap['net_win_rate_at_20bp']):.2f}% | {gap['net_profit_factor_at_20bp']:.3f} | {gap['block_signflip_p_one_sided']:.3f} |

### 静态验证与因果回放的对照

| 阶段 | P | R | mAP50 | mAP50-95 | 因果开火密度 |
|---|---:|---:|---:|---:|---:|
| 静态 val（16 个正例） | 0.729 | 0.675 | 0.735 | 0.443 | 未测 |
| 严格 OOS 逐 bar | N/A | N/A | N/A | N/A | **{pct(strict['raw_fire_rate']):.2f}%** |

静态 val 只证明模型能在同分布的稀疏图片上拟合框，不能证明连续盘口中会稀疏开火。
这不是通过提高 conf 就应立即修的阈值问题：严格 OOS 的原始框全部映射到当前 tip，且大量置信度很高；
在同一回放上扫阈值既违反阈值决策纪律，也会把数据集捷径伪装成修复。

## 解读

1. **检测层没有形成事件选择。** 严格 OOS 每 480 根/有效日中约 479 根开火，18 根去重只是在
   机械地每 54 分钟取一次，并没有把行情筛成少量事件。
2. **任何绝对收益都不能归给模型。** 严格 OOS 模型比同 run、同 ATR 桶随机做空还差
   {abs(bp(strict['paired_excess_mean'])):.2f}bp；间隙回放差
   {abs(bp(gap['paired_excess_mean'])):.2f}bp。块置换没有正向显著性。
3. **最可能的原因是训练目标的空间捷径和序列负样本缺失。** 76 张正例全部把框右缘固定在 tip，
   107 张负例又是离散的 owner-no 图片；静态 val 没有要求模型在一个正例周围的连续窗口中只开一次，
   因而模型学会了“ETH 图右侧放框”，而不是“只在启动时刻放框”。

## 本阶段不适用的项目指标

本轮只验收 YOLO 检测层，因此 AUC、top-decile、单特征基线和 LightGBM 置换检验均不适用；
它们必须等检测层能产生稀疏、可审计的事件池后再计算。方向性收益表已经按项目纪律加入匹配随机对照。

## 风险与诚实声明

1. 严格 OOS 只有 774 根、43 个去重信号和 2 个 run×UTC-day 统计块；它足以判定 99.74% 的
   开火密度失败，但不足以精确估计经济收益。
2. 3h 结果窗口互相重叠，逐信号 t 值偏乐观；报告以 run×UTC-day 符号置换 p 为更保守参考。
3. 间隙回放与训练日历交错，只能证明像素不重叠，不能替代严格时序 OOS。
4. 本轮未读取 2026-05-04 之后数据、未调 conf、未改成本/障碍、未 promote、未写 ACTIVE。

## 下一步选项（需 owner 决策）

1. **建议确认停止 v1，不进入判断层。** 判断层无法把一个 94%–100% 开火的检测器变成可信事件源。
2. **重做 v2 序列数据集。** 每个正事件只保留一个目标时点；增加同一事件前后连续窗口的
   “未形成 / 已经太晚”硬负例，并设置模糊区不训练；再加入预先封存时间块的背景负例。
3. **先决定检测层密度门。** 下一轮训练前由 owner 明确 raw fire/day、事件精度和来得及率上限；
   不在回放结果出来后倒推阈值。
4. **预留新的严格 OOS。** 先封存一整段未用于选图、标注或 early stopping 的连续 3m 时间块，
   v2 只允许一次验收；holdout 仍不动。

## 复核产物

- `analysis/output/eth3m_short_pilot_v1_backtest/summary.json`
- `analysis/output/eth3m_short_pilot_v1_backtest/validation.json`
- `analysis/output/eth3m_short_pilot_v1_backtest/scan_rows.csv`
- `analysis/output/eth3m_short_pilot_v1_backtest/signals.csv`
- `analysis/output/eth3m_short_pilot_v1_backtest/matched_controls.csv`
- `analysis/notebooks/eth3m_short_pilot_v1_backtest.ipynb`
"""
    REPORT_MD.write_text(text, encoding="utf-8")


def md_cell(cell_id: str, source: str) -> dict[str, Any]:
    return {"cell_type": "markdown", "id": cell_id, "metadata": {}, "source": source}


def code_cell(cell_id: str, source: str) -> dict[str, Any]:
    return {
        "cell_type": "code",
        "execution_count": None,
        "id": cell_id,
        "metadata": {},
        "outputs": [],
        "source": source,
    }


def execute_notebook_cells(cells: list[dict[str, Any]]) -> None:
    """Execute code cells top-to-bottom without requiring Jupyter packages."""
    namespace: dict[str, Any] = {"__name__": "__notebook__"}
    count = 0
    for cell in cells:
        if cell["cell_type"] != "code":
            continue
        count += 1
        output = io.StringIO()
        plt.close("all")
        with contextlib.redirect_stdout(output):
            exec(compile(cell["source"], f"notebook-cell-{count}", "exec"), namespace)
        outputs: list[dict[str, Any]] = []
        text = output.getvalue()
        if text:
            outputs.append({"name": "stdout", "output_type": "stream", "text": text})
        for fig_num in plt.get_fignums():
            fig = plt.figure(fig_num)
            image = io.BytesIO()
            fig.savefig(image, format="png", bbox_inches="tight", dpi=130)
            outputs.append(
                {
                    "data": {
                        "image/png": base64.b64encode(image.getvalue()).decode("ascii"),
                        "text/plain": f"<Figure {fig_num}>",
                    },
                    "metadata": {},
                    "output_type": "display_data",
                }
            )
        plt.close("all")
        cell["execution_count"] = count
        cell["outputs"] = outputs


def write_notebook(summary: dict[str, Any]) -> None:
    strict = summary["replay"]["strict_oos"]
    gap = summary["replay"]["gap_replay"]
    cells = [
        md_cell("title", "# ETH 3m short pilot v1 causal replay"),
        md_cell(
            "tldr",
            "## tl;dr\n\n"
            f"Strict OOS fired on **{strict['raw_fires']}/{strict['eligible_bars']}** bars "
            f"({pct(strict['raw_fire_rate']):.2f}%). After 18-bar dedup, net 3h short return "
            f"was **{bp(strict['net_mean_at_20bp']):+.2f}bp** versus matched random "
            f"**{bp(strict['matched_control_net_mean_at_20bp']):+.2f}bp**. The detector fails "
            "the density gate and should not enter the judgment layer.",
        ),
        md_cell(
            "methods",
            "## Context & Methods\n\n"
            "Decision: whether the dedicated ETH 3m short detector is sparse and selective enough "
            "to justify building a judgment layer. Strict OOS is primary; pixel-disjoint interleaved "
            "gaps are diagnostic only. Entry is next-bar open, exit is the 60th 3m close, cost is 20bp.\n\n"
            "### Key Assumptions\n\n"
            "- No bar at or after 2026-05-04 is read.\n"
            "- Every replay image has zero OHLC-bar overlap with all train/val images.\n"
            "- Matched controls share untouched run and ATR quintile.",
        ),
        code_cell(
            "load",
            "from pathlib import Path\n"
            "import json, pandas as pd, numpy as np, matplotlib.pyplot as plt\n"
            "PROJECT = Path.cwd()\n"
            "OUT = PROJECT / 'analysis/output/eth3m_short_pilot_v1_backtest'\n"
            "summary = json.loads((OUT / 'summary.json').read_text())\n"
            "validation = json.loads((OUT / 'validation.json').read_text())\n"
            "eligible = pd.read_csv(OUT / 'eligible.csv')\n"
            "scan = pd.read_csv(OUT / 'scan_rows.csv')\n"
            "signals = pd.read_csv(OUT / 'signals.csv')\n"
            "controls = pd.read_csv(OUT / 'matched_controls.csv')\n"
            "daily = pd.read_csv(OUT / 'daily.csv')\n"
            "assert validation['status'] == 'pass'\n"
            "print(f\"validation={validation['status']} eligible={len(eligible)} scan={len(scan)} signals={len(signals)} controls={len(controls)}\")",
        ),
        md_cell("data", "## Data\n\nThe checks below verify population, temporal boundaries, and saved-output grain."),
        code_cell(
            "checks",
            "print('strict eligible:', int(eligible.strict_oos.sum()))\n"
            "print('zero-overlap rows:', int((eligible.train_pixel_overlap_bars == 0).sum()))\n"
            "print('raw fire:', int(scan.raw_fire.sum()), '/', len(scan))\n"
            "print('mapped offsets:', (scan.loc[scan.raw_fire, 'bar_i'] - scan.loc[scan.raw_fire, 'mapped_box_bar_i']).value_counts().to_dict())\n"
            "print('failed validation checks:', validation['failed'])",
        ),
        md_cell("results", "## Results\n\nStrict OOS and the wider gap replay point in the same direction."),
        code_cell(
            "table",
            "rows=[]\n"
            "for scope,label in [('strict_oos','Strict OOS'),('gap_replay','Gap replay')]:\n"
            "    r=summary['replay'][scope]\n"
            "    rows.append({'scope':label,'eligible':r['eligible_bars'],'fire_rate_pct':100*r['raw_fire_rate'],"
            "'signals':r['dedup_signals'],'signals_day':r['dedup_signals_per_day'],"
            "'model_net_bp':1e4*r['net_mean_at_20bp'],'control_net_bp':1e4*r['matched_control_net_mean_at_20bp'],"
            "'excess_bp':1e4*r['paired_excess_mean'],'net_win_pct':100*r['net_win_rate_at_20bp']})\n"
            "result_table=pd.DataFrame(rows)\n"
            "print(result_table.round(2).to_string(index=False))",
        ),
        code_cell(
            "daily_plot",
            "fig,ax=plt.subplots(figsize=(9,4))\n"
            "ax.plot(pd.to_datetime(daily.date), daily.raw_fire_rate*100, marker='o', linewidth=1.5)\n"
            "ax.axhline(100,color='#9ca3af',linestyle='--',linewidth=1)\n"
            "ax.set(title='Daily raw fire rate on eligible bars',ylabel='Fire rate (%)',xlabel='UTC date',ylim=(0,105))\n"
            "ax.grid(alpha=.2); fig.autofmt_xdate(); fig.tight_layout()",
        ),
        code_cell(
            "economics_plot",
            "fig,ax=plt.subplots(figsize=(7,4))\n"
            "x=np.arange(len(result_table)); width=.35\n"
            "ax.bar(x-width/2,result_table.model_net_bp,width,label='Model signals',color='#ef4444')\n"
            "ax.bar(x+width/2,result_table.control_net_bp,width,label='Matched random',color='#94a3b8')\n"
            "ax.axhline(0,color='#111827',linewidth=.8)\n"
            "ax.set_xticks(x,result_table.scope); ax.set_ylabel('Mean net 3h short return (bp)')\n"
            "ax.set_title('Model versus matched random shorts'); ax.legend(); ax.grid(axis='y',alpha=.2); fig.tight_layout()",
        ),
        md_cell(
            "takeaways",
            "## Takeaways\n\n"
            f"- Strict OOS fire density is {pct(strict['raw_fire_rate']):.2f}%, so 18-bar dedup is only a mechanical throttle.\n"
            f"- Strict matched excess is {bp(strict['paired_excess_mean']):+.2f}bp; gap matched excess is {bp(gap['paired_excess_mean']):+.2f}bp.\n"
            "- Do not tune confidence on this replay or build the judgment layer. Rebuild v2 with sequence-local hard negatives and reserve a new continuous OOS block first.",
        ),
    ]
    execute_notebook_cells(cells)
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python (.venv)", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.9"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    NOTEBOOK.parent.mkdir(parents=True, exist_ok=True)
    NOTEBOOK.write_text(json.dumps(notebook, ensure_ascii=False, indent=1), encoding="utf-8")


def source_specs() -> dict[str, dict[str, Any]]:
    summary_path = "analysis/output/eth3m_short_pilot_v1_backtest/summary.json"
    daily_path = "analysis/output/eth3m_short_pilot_v1_backtest/daily.csv"
    scope_cte = f"""WITH replay_summary AS (
  SELECT replay.strict_oos AS strict_oos,
         replay.gap_replay AS gap_replay
  FROM read_json_auto('{summary_path}')
), scope_summary AS (
  SELECT '严格 OOS' AS scope, 'strict_oos' AS scope_id,
         strict_oos.eligible_bars AS eligible_bars,
         strict_oos.eligible_bar_days AS eligible_bar_days,
         strict_oos.raw_fires AS raw_fires,
         strict_oos.raw_fire_rate AS raw_fire_rate,
         strict_oos.raw_fires_per_day AS raw_fires_per_day,
         strict_oos.dedup_signals AS dedup_signals,
         strict_oos.dedup_signals_per_day AS signals_per_day,
         strict_oos.gross_mean * 10000 AS model_gross_bp,
         strict_oos.net_mean_at_20bp * 10000 AS model_net_bp,
         strict_oos.matched_control_net_mean_at_20bp * 10000 AS control_net_bp,
         strict_oos.paired_excess_mean * 10000 AS excess_bp,
         strict_oos.net_win_rate_at_20bp AS net_win_rate,
         strict_oos.net_profit_factor_at_20bp AS net_pf,
         strict_oos.block_signflip_p_one_sided AS block_p,
         strict_oos.matched_signal_coverage AS matched_coverage
  FROM replay_summary
  UNION ALL
  SELECT '间隙回放', 'gap_replay', gap_replay.eligible_bars,
         gap_replay.eligible_bar_days, gap_replay.raw_fires,
         gap_replay.raw_fire_rate, gap_replay.raw_fires_per_day,
         gap_replay.dedup_signals, gap_replay.dedup_signals_per_day,
         gap_replay.gross_mean * 10000, gap_replay.net_mean_at_20bp * 10000,
         gap_replay.matched_control_net_mean_at_20bp * 10000,
         gap_replay.paired_excess_mean * 10000,
         gap_replay.net_win_rate_at_20bp,
         gap_replay.net_profit_factor_at_20bp,
         gap_replay.block_signflip_p_one_sided,
         gap_replay.matched_signal_coverage
  FROM replay_summary
)"""
    shared_query = {
        "engine": "duckdb",
        "language": "sql",
        "executed_at": "2026-07-29T15:01:16Z",
        "filters": [
            "ETH_USDT_SWAP 3m only",
            "open_time < 2026-05-04T00:00:00Z",
            "200-bar replay window has zero overlap with every train/val image",
            "conf=0.30, tip-edge=2 bars, min-gap=18 bars",
        ],
        "metric_definitions": [
            "raw_fire_rate = eligible decision bars with any tip-edge box / eligible decision bars",
            "net_ret_3h = 1 - close[t+60] / open[t+1] - 0.002",
            "paired_excess = model signal net return - mean matched-control net return",
        ],
    }
    return {
        "bt_source": {
            "id": "bt_source",
            "label": "ETH 3m causal replay summary",
            "path": summary_path,
            "query": {
                **shared_query,
                "sql": f"{scope_cte}\nSELECT * FROM scope_summary ORDER BY scope_id;",
                "description": "Builds strict-OOS and zero-pixel-overlap replay metrics from the audited replay summary.",
                "tables_used": [summary_path],
            },
        },
        "economics_source": {
            "id": "economics_source",
            "label": "ETH 3m model versus matched-control economics",
            "path": summary_path,
            "query": {
                **shared_query,
                "sql": f"""{scope_cte}
SELECT scope_summary.*,
       series,
       CASE WHEN series = '模型信号' THEN model_net_bp ELSE control_net_bp END AS net_bp
FROM scope_summary
CROSS JOIN (VALUES ('模型信号'), ('匹配随机')) AS comparison(series)
ORDER BY scope_id, series;""",
                "description": "Expands each replay scope into model and exact run/ATR matched-control return rows.",
                "tables_used": [summary_path],
            },
        },
        "daily_source": {
            "id": "daily_source",
            "label": "ETH 3m daily replay density",
            "path": daily_path,
            "query": {
                "engine": "duckdb",
                "language": "sql",
                "sql": f"SELECT * FROM read_csv_auto('{daily_path}', header=true) ORDER BY date;",
                "description": "Loads the audited daily density rollup produced by the causal replay.",
                "executed_at": "2026-07-29T15:01:16Z",
                "filters": [
                    "ETH_USDT_SWAP 3m only",
                    "zero train/val pixel overlap",
                    "open_time < 2026-05-04T00:00:00Z",
                ],
                "metric_definitions": [
                    "raw_fire_rate = raw_fires / eligible_bars for each UTC date",
                ],
                "tables_used": [daily_path],
            },
        },
    }


def write_artifact(summary: dict[str, Any], daily: pd.DataFrame) -> None:
    scopes: list[dict[str, Any]] = []
    economics: list[dict[str, Any]] = []
    for scope, label in (("strict_oos", "严格 OOS"), ("gap_replay", "间隙回放")):
        r = summary["replay"][scope]
        row = {
            "scope": label,
            "scope_id": scope,
            "eligible_bars": r["eligible_bars"],
            "eligible_bar_days": r["eligible_bar_days"],
            "raw_fires": r["raw_fires"],
            "raw_fire_rate": r["raw_fire_rate"],
            "raw_fires_per_day": r["raw_fires_per_day"],
            "dedup_signals": r["dedup_signals"],
            "signals_per_day": r["dedup_signals_per_day"],
            "model_gross_bp": bp(r["gross_mean"]),
            "model_net_bp": bp(r["net_mean_at_20bp"]),
            "control_net_bp": bp(r["matched_control_net_mean_at_20bp"]),
            "excess_bp": bp(r["paired_excess_mean"]),
            "net_win_rate": r["net_win_rate_at_20bp"],
            "net_pf": r["net_profit_factor_at_20bp"],
            "block_p": r["block_signflip_p_one_sided"],
            "matched_coverage": r["matched_signal_coverage"],
        }
        scopes.append(row)
        economics.extend(
            [
                {**row, "series": "模型信号", "net_bp": row["model_net_bp"]},
                {**row, "series": "匹配随机", "net_bp": row["control_net_bp"]},
            ]
        )
    strict = summary["replay"]["strict_oos"]
    gap = summary["replay"]["gap_replay"]
    sources = source_specs()
    artifact = {
        "surface": "report",
        "manifest": {
            "version": 1,
            "surface": "report",
            "title": "ETH 3m 专用做空模型：开发期回测",
            "description": "严格 OOS 与训练像素零重叠间隙回放的因果验收。",
            "generatedAt": summary["generated_at"],
            "cards": [
                {
                    "id": "strict_fire",
                    "description": "训练结束后严格 OOS 中，产生 tip 框的 eligible bars 占比。",
                    "dataset": "scope_summary",
                    "sourceId": "bt_source",
                    "filter": {"scope_id": "strict_oos"},
                    "metrics": [{"label": "严格 OOS 原始开火率", "field": "raw_fire_rate", "format": "percent"}],
                },
                {
                    "id": "strict_density",
                    "description": "18 根最小间隔后，每个有效 24 小时的信号数。",
                    "dataset": "scope_summary",
                    "sourceId": "bt_source",
                    "filter": {"scope_id": "strict_oos"},
                    "metrics": [{"label": "去重信号/有效日", "field": "signals_per_day", "format": "number"}],
                },
                {
                    "id": "strict_net",
                    "description": "下一根 open 入场、3h close 出场并扣 20bp 的平均做空收益。",
                    "dataset": "scope_summary",
                    "sourceId": "bt_source",
                    "filter": {"scope_id": "strict_oos"},
                    "metrics": [{"label": "模型净收益均值（bp）", "field": "model_net_bp", "format": "number", "signed": True}],
                },
                {
                    "id": "strict_excess",
                    "description": "模型信号相对同 run、同 ATR 桶随机做空的信号加权差。",
                    "dataset": "scope_summary",
                    "sourceId": "bt_source",
                    "filter": {"scope_id": "strict_oos"},
                    "metrics": [{"label": "匹配超额（bp）", "field": "excess_bp", "format": "number", "signed": True}],
                },
            ],
            "charts": [
                {
                    "id": "fire_density",
                    "title": "原始开火率",
                    "subtitle": "严格 OOS 与训练像素零重叠的间隙回放。",
                    "type": "bar",
                    "dataset": "scope_summary",
                    "sourceId": "bt_source",
                    "valueFormat": "percent",
                    "encodings": {
                        "x": {"field": "scope", "type": "nominal", "label": "回放范围"},
                        "y": {"field": "raw_fire_rate", "type": "quantitative", "label": "开火率", "format": "percent"},
                        "tooltip": [
                            {"field": "eligible_bars", "type": "quantitative", "label": "有效 bars"},
                            {"field": "raw_fires", "type": "quantitative", "label": "原始开火"},
                            {"field": "dedup_signals", "type": "quantitative", "label": "去重信号"},
                        ],
                    },
                },
                {
                    "id": "economics",
                    "title": "3h 做空净收益对照",
                    "subtitle": "均扣固定 20bp；随机对照匹配同 run 与 ATR 五分位。",
                    "type": "bar",
                    "dataset": "economics",
                    "sourceId": "economics_source",
                    "valueFormat": "number",
                    "unit": "bp",
                    "encodings": {
                        "x": {"field": "scope", "type": "nominal", "label": "回放范围"},
                        "y": {"field": "net_bp", "type": "quantitative", "label": "平均净收益", "unit": "bp"},
                        "color": {"field": "series", "type": "nominal", "label": "样本"},
                        "tooltip": [
                            {"field": "dedup_signals", "type": "quantitative", "label": "模型信号数"},
                            {"field": "excess_bp", "type": "quantitative", "label": "匹配超额", "unit": "bp"},
                        ],
                    },
                },
                {
                    "id": "daily_fire",
                    "title": "每日原始开火率",
                    "subtitle": "只统计与训练图片像素零重叠的 eligible bars。",
                    "type": "line",
                    "dataset": "daily",
                    "sourceId": "daily_source",
                    "valueFormat": "percent",
                    "encodings": {
                        "x": {"field": "date", "type": "temporal", "label": "UTC 日期"},
                        "y": {"field": "raw_fire_rate", "type": "quantitative", "label": "开火率", "format": "percent"},
                        "tooltip": [
                            {"field": "eligible_bars", "type": "quantitative", "label": "有效 bars"},
                            {"field": "gap_replay_signals", "type": "quantitative", "label": "去重信号"},
                        ],
                    },
                },
            ],
            "tables": [
                {
                    "id": "scope_table",
                    "title": "回放结果明细",
                    "subtitle": "严格 OOS 为主结果；间隙回放只作辅助诊断。",
                    "dataset": "scope_summary",
                    "sourceId": "bt_source",
                    "defaultSort": {"field": "raw_fire_rate", "direction": "desc"},
                    "layout": "full",
                    "columns": [
                        {"field": "scope", "label": "范围", "type": "text"},
                        {"field": "eligible_bars", "label": "有效 bars", "format": "number"},
                        {"field": "raw_fire_rate", "label": "开火率", "format": "percent"},
                        {"field": "dedup_signals", "label": "去重信号", "format": "number"},
                        {"field": "signals_per_day", "label": "信号/日", "format": "number"},
                        {"field": "model_net_bp", "label": "模型净均值(bp)", "format": "number", "movement": True},
                        {"field": "control_net_bp", "label": "随机净均值(bp)", "format": "number", "movement": True},
                        {"field": "excess_bp", "label": "匹配超额(bp)", "format": "number", "movement": True},
                        {"field": "net_win_rate", "label": "净胜率", "format": "percent"},
                    ],
                }
            ],
            "sources": list(sources.values()),
            "blocks": [
                {"id": "title", "type": "markdown", "body": "# ETH 3m 专用做空模型：开发期回测"},
                {
                    "id": "executive",
                    "type": "markdown",
                    "sourceId": "bt_source",
                    "body": "## Executive Summary\n\n"
                    f"- **本轮不通过。** 严格 OOS 在 {strict['raw_fires']}/{strict['eligible_bars']} 根盘口开火，原始开火率 **{pct(strict['raw_fire_rate']):.2f}%**。\n"
                    f"- **去重并没有创造选择力。** 18 根去重后仍有 {strict['dedup_signals']} 笔、{strict['dedup_signals_per_day']:.2f} 笔/有效日。\n"
                    f"- **经济结果同样失败。** 扣 20bp 后模型平均 **{bp(strict['net_mean_at_20bp']):+.2f}bp**，匹配随机为 **{bp(strict['matched_control_net_mean_at_20bp']):+.2f}bp**，模型超额 **{bp(strict['paired_excess_mean']):+.2f}bp**。\n"
                    "- **下一步不是调 conf。** 应停止 v1，先重做带连续硬负例的 v2 数据集并预留新的严格 OOS。",
                },
                {"id": "metrics", "type": "metric-strip", "cardIds": ["strict_fire", "strict_density", "strict_net", "strict_excess"]},
                {
                    "id": "density_finding",
                    "type": "markdown",
                    "sourceId": "bt_source",
                    "body": "## 检测层没有形成稀疏事件\n\n"
                    "严格 OOS 每 480 根/有效日约有 479 根开火。18 根间隔只是在机械限流，不能把近似恒真的检测结果变成事件选择。",
                },
                {"id": "density_chart", "type": "chart", "chartId": "fire_density"},
                {
                    "id": "economics_finding",
                    "type": "markdown",
                    "sourceId": "economics_source",
                    "body": "## 收益没有超过匹配随机做空\n\n"
                    f"严格 OOS 模型比随机差 {abs(bp(strict['paired_excess_mean'])):.2f}bp；间隙回放差 {abs(bp(gap['paired_excess_mean'])):.2f}bp。任何绝对涨跌都不能归因给模型。",
                },
                {"id": "economics_chart", "type": "chart", "chartId": "economics"},
                {"id": "scope_table_block", "type": "table", "tableId": "scope_table", "layout": "full"},
                {
                    "id": "daily_finding",
                    "type": "markdown",
                    "sourceId": "daily_source",
                    "body": "## 过密不是单日偶发\n\n多数 eligible 日期的开火率接近 100%；少数较低日期也仍不足以形成可用的稀疏事件流。",
                },
                {"id": "daily_chart", "type": "chart", "chartId": "daily_fire"},
                {
                    "id": "next_steps",
                    "type": "markdown",
                    "body": "## 建议下一步\n\n"
                    "1. **停止 v1，不进入判断层或 holdout。**\n"
                    "2. **先封存新的连续严格 OOS。** v2 训练、选图和 early stopping 都不得读取。\n"
                    "3. **重做序列数据集。** 每事件只保留一个正时点，并加入同事件前后连续窗口的‘未形成/已经太晚’硬负例；模糊区不训练。\n"
                    "4. **训练前由 owner 明确密度门。** 不在看到回放后倒推 conf。",
                },
                {
                    "id": "questions",
                    "type": "markdown",
                    "body": "## 仍需回答的问题\n\n"
                    "- owner 接受的原始开火/日和去重事件/日上限是多少？\n"
                    "- ‘已经太晚’窗口是否直接作为负例，还是单独做 timing 类别？\n"
                    "- v2 需要补多少跨行情背景负例，才能在训练前锁定新的 OOS？",
                },
                {
                    "id": "caveats",
                    "type": "markdown",
                    "sourceId": "bt_source",
                    "body": "## 口径、限制与诚实声明\n\n"
                    "- 严格 OOS 只有 774 根、43 个去重信号和 2 个 run×UTC-day 块；足以判定密度失败，不足以精确估计收益。\n"
                    "- 3h 结果重叠，逐信号 t 值偏乐观；报告使用更保守的块符号置换作参考。\n"
                    "- 间隙回放只保证训练像素零重叠，不能替代严格时序 OOS。\n"
                    "- 本轮未读取 holdout、未调阈值、未改成本/障碍、未 promote。",
                },
            ],
        },
        "snapshot": {
            "version": 1,
            "generatedAt": summary["generated_at"],
            "status": "ready",
            "datasets": {
                "scope_summary": scopes,
                "economics": economics,
                # pandas' ``to_dict`` preserves floating NaN values even after
                # replacing missing entries with None on a float column.  The
                # JSON round-trip emits strict ``null`` values instead, keeping
                # the portable report artifact valid for non-Python parsers.
                "daily": json.loads(daily.to_json(orient="records")),
            },
        },
        "sources": list(sources.values()),
    }
    ARTIFACT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    summary, _validation, daily, _signals = load_inputs()
    write_markdown(summary)
    write_notebook(summary)
    write_artifact(summary, daily)
    print(REPORT_MD)
    print(NOTEBOOK)
    print(ARTIFACT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
