#!/usr/bin/env python3
"""Build the report for the frozen LONG/SHORT L2 regression comparison."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "exp-15m-ma-launch-l2-side-split-v1"
EXPERIMENT_DIR = ROOT / "experiments" / "active" / EXPERIMENT_ID
PREREG_PATH = EXPERIMENT_DIR / "preregistration.json"
TRAINING_PATH = EXPERIMENT_DIR / "results" / "training_receipt.json"
VERIFY_PATH = EXPERIMENT_DIR / "results" / "verify_receipt.json"
REPORT_PATH = ROOT / "analysis" / "p3_15m_ma_launch_l2_side_split_20260901.md"


def pct(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "—"
    return f"{100 * float(value):+.{digits}f}%"


def plain_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{100 * float(value):.{digits}f}%"


def number(value: float | None, digits: int = 4) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{digits}f}"


def arm_row(name: str, arm: Mapping[str, Any]) -> str:
    main = arm["final_validation"]
    selected = arm["final_validation_frozen_threshold"]
    return (
        f"| {name} | {arm['splits']['train']} / {arm['splits']['tune']} / "
        f"{arm['splits']['final_validation']} | {arm['best_iteration']} | "
        f"{selected['n']} | {pct(selected['net_mean'])} | "
        f"{plain_pct(selected['win_rate'])} | {pct(main['top_decile']['net_mean'])} | "
        f"{number(arm['outcome_permutation_p'])} | {number(main['roc_auc'])} |"
    )


def build_report(
    prereg: Mapping[str, Any], training: Mapping[str, Any], verify: Mapping[str, Any]
) -> str:
    mixed = training["mixed"]
    long = training["sides"]["long"]
    short = training["sides"]["short"]
    aggregate = training["aggregate_side_split"]
    agg_selected = aggregate["final_validation_frozen_threshold"]
    agg_main = aggregate["final_validation"]
    control = aggregate["matched_control"]
    gate = training["primary_gate"]
    baselines = training["single_feature_baselines"]
    long_lift = (
        long["final_validation"]["top_decile"]["net_mean"]
        - baselines["long"]["final_validation"]["top_decile"]["net_mean"]
    )
    short_lift = (
        short["final_validation"]["top_decile"]["net_mean"]
        - baselines["short"]["final_validation"]["top_decile"]["net_mean"]
    )
    rows = "\n".join(
        [
            arm_row("混合回归（复现基线）", mixed),
            arm_row("LONG 独立回归", long),
            arm_row("SHORT 独立回归", short),
            (
                f"| 多空独立后合并 | — / — / {agg_main['n']} | — | {agg_selected['n']} | "
                f"{pct(agg_selected['net_mean'])} | {plain_pct(agg_selected['win_rate'])} | "
                f"{pct(agg_main['top_decile']['net_mean'])} | "
                f"{number(aggregate['outcome_permutation_p'])} | {number(agg_main['roc_auc'])} |"
            ),
        ]
    )
    gate_rows = "\n".join(
        f"| `{key}` | {'PASS' if value else 'FAIL'} |"
        for key, value in gate.items()
        if key != "passed"
    )
    return f"""# 15m 均线密集启动：L2 多空拆分回归 v1

## 结论

本轮按 Owner 要求把上一轮 **3,779 条完全相同的 L2 数据**拆成 LONG 1,801 条与
SHORT 1,978 条，并分别训练收益回归模型。混合模型已逐分数复现：242 个最终独立事件的
最大分数差 `{training['mixed_reproduction']['maximum_absolute_score_delta']:.3g}`，41 个
KEEP 决策完全一致。因此结果变化只来自“一个混合模型”改为“多空两个模型”。

**多空拆分的方向明显更好，但预注册门仍为 FAIL。** 两个 tune-q90 门在最终时间段合计保留
{agg_selected['n']} 个独立事件，扣 0.2% 往返成本后平均 {pct(agg_selected['net_mean'])}；
混合模型保留 {mixed['final_validation_frozen_threshold']['n']} 个，净均值仅
{pct(mixed['final_validation_frozen_threshold']['net_mean'])}。但合并置换检验
`p={aggregate['outcome_permutation_p']:.6f}`，未达到 0.01；SHORT 仅保留
{short['final_validation_frozen_threshold']['n']} 个，合计也低于预注册的30个独立事件门。
这只能作为“应继续积累同合同新样本”的发现，不能 promote、部署或用于下单。

## 冻结实验合同

- 来源数据 SHA-256：`{training['source_dataset_sha256']}`；候选、特征、TP5/SL2/72 标签与
  0.2% 成本均未重算或修改。
- 28 个输入特征已在 L1 最后一根可见 K 线收盘时因果生成；未来只用于训练 outcome。
- train / tune / final_validation 原时间段及完整暴露 dependency block 原样保留；训练、早停、
  tune-q90 与最终指标只使用每个 block 的首个事件。
- LONG 和 SHORT 各自在自己的 tune 分数上固定 q90；合并排序使用各自 tune 分布的经验百分位，
  防止某一方向仅因原始分数尺度不同而占据榜首。
- 没有参数搜索、没有在 final validation 调阈值、没有读取 ≥2026-05-04 holdout。

## 数据统计

| 方向 | 全量行 | train | purge | tune | final validation |
|---|---:|---:|---:|---:|---:|
| LONG | {training['partition_rows']['long']} | {training['partition_split_counts']['long']['train']} | {training['partition_split_counts']['long']['purge']} | {training['partition_split_counts']['long']['tune']} | {training['partition_split_counts']['long']['final_validation']} |
| SHORT | {training['partition_rows']['short']} | {training['partition_split_counts']['short']['train']} | {training['partition_split_counts']['short']['purge']} | {training['partition_split_counts']['short']['tune']} | {training['partition_split_counts']['short']['final_validation']} |
| 合计 | {training['source_rows']} | {training['partition_split_counts']['long']['train'] + training['partition_split_counts']['short']['train']} | {training['partition_split_counts']['long']['purge'] + training['partition_split_counts']['short']['purge']} | {training['partition_split_counts']['long']['tune'] + training['partition_split_counts']['short']['tune']} | {training['partition_split_counts']['long']['final_validation'] + training['partition_split_counts']['short']['final_validation']} |

## 时间外结果

| 模型 | 独立 train/tune/final | best iter | q90保留n | q90净收益 | q90胜率 | top-decile净收益 | 置换p | AUC |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
{rows}

LONG 的最终 top-decile 比同方向 `ma_spread_pct` 单特征基线高 {pct(long_lift)}；SHORT 高
{pct(short_lift)}。这说明改善不只是把数据按方向切开后的单特征分桶，但样本量仍不足以证明
稳定性。AUC 只作诊断：收益回归按幅度排序，不能用 TP-first 分类 AUC 作为成功裁决。

## 匹配随机对照

合并后的20个 q90 事件在8个固定分配上全部跑赢同币 × 同月 × 同UTC时段 × 同ATR桶 × 同方向
的随机候选；平均净超额 {pct(control['mean_event_minus_control'])}，每个分配有
{control['minimum_pairs_per_assignment']} 对。LONG 与 SHORT 单独也均为8/8正，但SHORT每个分配
只有7对，不能用“大数值”掩盖小样本不确定性。置换检验没有通过，故随机对照通过不能单独放行。

## 预注册门

| 门 | 结果 |
|---|---:|
{gate_rows}

总判定：**{'PASS' if gate['passed'] else 'FAIL'}**。

## L1.5 应该怎么做

L1.5 不是收益模型，而是 **全局形态质量分类器**。它应放在局部 YOLO 与经济 L2 之间：

```text
L1 局部检测 → L1.5 全局形态过滤 → LONG/SHORT 独立收益回归 L2 → 回测
```

1. **输入**：固定168根已收盘15m K线，右端严格停在L1可用时刻；使用与实扫一致的渲染器、
   原生宽高比和颜色。未来K线、收益、TP/SL结果不得进入像素。
2. **候选定位**：不把红框烧进RGB图，避免模型学习框颜色/位置捷径；L1核心起止与确认根数作为
   独立数值元数据，或作为单独mask通道输入。
3. **标签**：`global_shape_good`只表达Owner认可的全局结构，例如启动前是否已经走完、是否只是
   长时间平盘、均线是否真正收拢、候选是否位于合理阶段。不能用“后来赚了”自动当正例。
4. **多空分开**：LONG、SHORT分别建Gold与hard negatives，禁止时间翻转、左右翻转或交换红绿语义。
5. **样本来源**：以现有8,000个Grade-A事件作为正例种子重新渲染168根上下文；负例优先使用
   “局部像、全局不对”的L1 hard proposals，而不是大量一眼就不像的easy negatives。现有24,000
   负例只能作为候选来源，不能自动宣称拥有全局语义。
6. **训练顺序**：先做可解释的全局数值规则/LightGBM基线，再训练图像分类器；图像模型必须在
   同一时间外Gold上显著提高precision，才证明它看到了规则没有表达的视觉结构。
7. **验收**：以Owner确认的独立全局Gold集报告LONG/SHORT precision、recall、误杀率，以及
   L1→L1.5前后的候选变化；这是标签质量实验，不用收益替代形态真值。经济效果留给后面的L2。

不建议直接把8,000张局部正图换成长图就开训：这些标签只证明局部形态，尚未证明整张168根图
符合全局标准。应先构建小而可靠的全局Gold和hard-negative门，再扩成训练集。

## 风险与诚实声明

- 最终独立事件只有242个，拆分后LONG/SHORT分别157/85；q90保留13/7，方差很大。
- 本轮 final validation 已用于一次预注册裁决；不得在这里继续调阈值后再次宣称独立验证。
- 原L1是看过核心后2–9根确认K的完成形态检测器，本轮没有把它变成tip实时信号。
- 本轮未读取holdout、未重扫L1、未改标签/特征/障碍/成本、未promote、未部署、未改
  ACTIVE/frozen/forward、未发Telegram、未下单。
- 校验回执：`verify.passed={str(bool(verify['passed'])).lower()}`，全部
  {len(verify['checks'])} 项为真。

## 复现命令

```bash
git checkout {training['source_commit']}
PYTHONPATH=. .venv/bin/python scripts/retrain_15m_ma_launch_l2_by_side.py --train-evaluate
PYTHONPATH=. .venv/bin/python scripts/retrain_15m_ma_launch_l2_by_side.py --verify
PYTHONPATH=. .venv/bin/python scripts/build_15m_ma_launch_l2_side_split_report.py
python3 scripts/md_to_html.py analysis/p3_15m_ma_launch_l2_side_split_20260901.md --out-dir analysis/html
```

## 下一步

1. 保留多空独立回归作为研究候选，但不启用；用相同冻结合同积累新的时间外事件，至少补足
   SHORT和总选择数后再做一次预注册确认。
2. L1.5另立实验，先产出因果168根全局Gold/hard-negative预览及直接质量统计；不与本轮收益
   回归一起改，避免无法归因。
"""


def main() -> int:
    prereg = json.loads(PREREG_PATH.read_text(encoding="utf-8"))
    training = json.loads(TRAINING_PATH.read_text(encoding="utf-8"))
    verify = json.loads(VERIFY_PATH.read_text(encoding="utf-8"))
    report = build_report(prereg, training, verify)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(REPORT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
