#!/usr/bin/env python3
"""Build the ETH perpetual 15m Pine report and executed audit notebook.

Inputs are compact, validated artifacts under
``experiments/active/exp-pine-eth-15m-v1/results``.  The builder never reads
market data or holdout rows and never trains/scores a model.  It creates the
canonical Markdown report required by project policy plus a rerunnable notebook
that exposes the key calculations and decision assertions.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

import nbformat
import pandas as pd
from nbclient import NotebookClient


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-pine-eth-15m-v1"
RESULTS = EXPERIMENT / "results"
NOTEBOOKS = EXPERIMENT / "notebooks"
REPORT = PROJECT / "analysis/p0_pine_eth_15m_v1_20260821.md"


def _fmt(value: Any, digits: int = 2) -> str:
    if value is None or pd.isna(value):
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def markdown_table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    headers = list(headers)
    rendered = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        rendered.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(rendered)


def load_evidence() -> dict[str, Any]:
    validation = json.loads((RESULTS / "validation.json").read_text(encoding="utf-8"))
    if validation["status"] != "pass":
        raise RuntimeError(f"validation must pass before report build: {validation['failed']}")
    return {
        "config": json.loads((EXPERIMENT / "config.json").read_text(encoding="utf-8")),
        "quality": json.loads((RESULTS / "data_quality.json").read_text(encoding="utf-8")),
        "summary": json.loads((RESULTS / "summary.json").read_text(encoding="utf-8")),
        "statistics": json.loads((RESULTS / "statistical_tests.json").read_text(encoding="utf-8")),
        "features": json.loads((RESULTS / "feature_contract.json").read_text(encoding="utf-8")),
        "framework": json.loads((RESULTS / "backtesting_reconciliation.json").read_text(encoding="utf-8")),
        "intrabar": json.loads((RESULTS / "intrabar_3m_reconciliation.json").read_text(encoding="utf-8")),
        "robustness": json.loads((RESULTS / "robustness_checks.json").read_text(encoding="utf-8")),
        "validation": validation,
        "split": pd.read_csv(RESULTS / "split_summary.csv"),
        "matrix": pd.read_csv(RESULTS / "experiment_matrix.csv"),
        "risk": pd.read_csv(RESULTS / "risk_grid.csv"),
        "threshold": pd.read_csv(RESULTS / "threshold_search.csv"),
        "slope": pd.read_csv(RESULTS / "slope_search.csv"),
        "trailing": pd.read_csv(RESULTS / "trailing_search.csv"),
        "feature_search": pd.read_csv(RESULTS / "feature_filter_search.csv"),
        "core_ablation": pd.read_csv(RESULTS / "core_component_ablation.csv"),
        "prequential": pd.read_csv(RESULTS / "prequential_feature_selection.csv"),
        "timeframe": pd.read_csv(RESULTS / "timeframe_rescale_ablation.csv"),
        "cost": pd.read_csv(RESULTS / "cost_sensitivity.csv"),
        "trades": pd.read_csv(RESULTS / "trades.csv", parse_dates=["entry_time", "exit_time"]),
    }


def build_diagnostics(evidence: dict[str, Any]) -> dict[str, Any]:
    trades = evidence["trades"]
    final_name = "final_preholdout_2025_202602"
    diagnostics: dict[str, Any] = {}
    for variant in ("v8_eth_baseline", "v9_locked", "v10_volume_hypothesis"):
        selected = trades.loc[(trades["variant"] == variant) & (trades["split"] == final_name)].copy()
        selected["month"] = selected["entry_time"].dt.strftime("%Y-%m")
        monthly = selected.groupby("month")["project_net_return"].mean() * 10_000.0
        by_side = selected.groupby("direction")["project_net_return"].agg(["size", "mean", "sum"])
        by_side[["mean", "sum"]] *= 10_000.0
        by_exit = selected.groupby("exit_reason")["project_net_return"].agg(["size", "mean", "sum"])
        by_exit[["mean", "sum"]] *= 10_000.0
        diagnostics[variant] = {
            "months": int(len(monthly)),
            "positive_months": int((monthly > 0.0).sum()),
            "median_monthly_net_bp": float(monthly.median()),
            "monthly_net_bp": {str(key): float(value) for key, value in monthly.items()},
            "by_side": by_side.reset_index().round(6).to_dict("records"),
            "by_exit": by_exit.reset_index().round(6).to_dict("records"),
        }
    (RESULTS / "diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return diagnostics


def _variant_row(split: pd.DataFrame, variant: str, period: str) -> pd.Series:
    return split.loc[(split["variant"] == variant) & (split["period"] == period)].iloc[0]


def build_report(evidence: dict[str, Any], diagnostics: dict[str, Any]) -> str:
    config = evidence["config"]
    quality = evidence["quality"]
    summary = evidence["summary"]
    stats = evidence["statistics"]
    framework = evidence["framework"]
    intrabar = evidence["intrabar"]
    robustness = evidence["robustness"]
    validation = evidence["validation"]
    split = evidence["split"]
    risk = evidence["risk"]
    timeframe = evidence["timeframe"]
    threshold = evidence["threshold"].groupby("threshold", as_index=False).first()
    feature_search = evidence["feature_search"].groupby("feature_filter", as_index=False).first()

    core_rows = [
        [
            row["variant"],
            _fmt(int(row["trades"]), 0),
            _fmt(row["minimum_block_net_bp"]),
            _fmt(row["weighted_net_bp"]),
            f"{int(row['positive_blocks'])}/4",
        ]
        for row in robustness["core_component_aggregate"]
    ]
    prequential_rows = [
        [
            row.selected_on_periods,
            row.test_period,
            row.selected_feature,
            _fmt(row.selected_test_net_bp),
            _fmt(row.baseline_test_net_bp),
            _fmt(row.incremental_test_net_bp),
        ]
        for row in evidence["prequential"].itertuples(index=False)
    ]

    periods = ["discovery_2023", "confirmation_2024", "final_preholdout_2025_202602"]
    comparison_rows = []
    for variant, label in (
        ("v8_eth_baseline", "V8 原 ETH 基线"),
        ("v8_plus_slope12", "V8 + EMA200 slope12"),
        ("v9_locked", "V9 锁定候选"),
        ("v10_volume_hypothesis", "V10 成交量假设*"),
    ):
        for period in periods:
            selected = split.loc[(split["variant"] == variant) & (split["period"] == period)]
            if selected.empty:
                continue
            row = selected.iloc[0]
            comparison_rows.append(
                [
                    label,
                    period.replace("final_preholdout_2025_202602", "final 2025–2026-02"),
                    _fmt(int(row["trades"]), 0),
                    _fmt(row["gross_bp_per_trade"]),
                    _fmt(row["project_net_bp_per_trade"]),
                    _fmt(row["monetary_profit_factor"], 3),
                    _fmt(row["return_percent"]),
                    _fmt(row["max_drawdown_15m_percent"]),
                ]
            )

    v9 = summary["v9_final_preholdout"]
    v10 = summary["v10_post_selection_final_preholdout"]
    v9_match = stats["matched_control"]
    v9_flip = stats["week_block_signflip"]
    v9_abs = stats["week_bootstrap_absolute"]
    ranking = stats["oscillator_ranking_permutation"]
    concentration = stats["profit_concentration"]
    v10_stats = stats["v10_post_selection_hypothesis"]

    risk_final = risk.loc[risk["period"] == "final_preholdout_2025_202602"]
    risk_rows = [
        [
            _fmt(row.risk_percent),
            _fmt(row.return_percent),
            _fmt(row.max_drawdown_15m_percent),
            _fmt(row.mean_leverage),
            _fmt(row.max_leverage),
        ]
        for row in risk_final.itertuples(index=False)
    ]

    rescale_rows = [
        [
            row.period,
            _fmt(int(row.trades), 0),
            _fmt(row.project_net_bp_per_trade),
            _fmt(row.return_percent),
            _fmt(row.max_drawdown_15m_percent),
        ]
        for row in timeframe.itertuples(index=False)
    ]

    feature_top = feature_search.sort_values(
        ["min_block_net_bp", "weighted_net_bp"], ascending=False
    ).head(8)
    feature_rows = [
        [
            row.feature_filter,
            _fmt(int(row.trades), 0),
            _fmt(row.min_block_net_bp),
            _fmt(row.weighted_net_bp),
            "是（仅 forward 假设）" if bool(row.development_selected) else "否",
        ]
        for row in feature_top.itertuples(index=False)
    ]

    threshold_rows = [
        [
            _fmt(float(row.threshold), 2),
            _fmt(int(row.trades), 0),
            _fmt(row.min_block_net_bp),
            _fmt(row.weighted_net_bp),
            "锁定" if bool(row.selected) else "—",
        ]
        for row in threshold.itertuples(index=False)
    ]

    cost_official = evidence["cost"].loc[evidence["cost"]["official_cost_row"].astype(bool)]
    cost_break_even = {
        row.variant: row.gross_bp_per_trade for row in cost_official.itertuples(index=False)
    }

    report = f"""# ETHUSDT.P / ETH-USDT-SWAP 15m Pine 定型与回测审计（V1）

生成日期：2026-08-21
实验：`{config['experiment_id']}`
构建提交：`{summary['generated_from_commit']}`
状态：**研究候选；不可生产、不可 forward、未通过 TradingView parity**

## 结论先行

15 分钟已经定死为本轮唯一研究周期：本地数据契约是 **OKX `ETH-USDT-SWAP` 15m**，
TradingView 的 `ETHUSDT.P` 必须再明确具体交易所后做逐笔导出对账，不能把一个显示名当成同一数据源。

当前最诚实的结论不是“已经找到稳定暴利策略”，而是：

1. 原始逻辑在 ETH 单币 final-preholdout 段成本后失败：V8 为
   **{_fmt(summary['baseline_final_preholdout']['project_net_bp_per_trade'])} bp/笔**。
2. 在 2023/2024 四个时间块先选择项目已有的 `slow_slope_12` 方向门，再把 oscillator
   阈值从 `0.2` 锁到 `0.1`，V9 在 2025-01 至 2026-02 得到
   **+{_fmt(v9['project_net_bp_per_trade'])} bp/笔、PF {_fmt(v9['monetary_profit_factor'], 3)}、
   1% 风险资金收益 +{_fmt(v9['return_percent'])}%、15m 收盘最大回撤 {_fmt(v9['max_drawdown_15m_percent'])}%**。
3. 这个正结果仍未过项目的证据门：相对匹配随机对照 +{_fmt(v9_match['mean_excess_bp'])} bp，
   但 UTC 周区块 sign-flip `p={_fmt(v9_flip['p_value'], 4)}`，绝对收益周 bootstrap
   95% CI 为 [{_fmt(v9_abs['ci95_low_bp'])}, {_fmt(v9_abs['ci95_high_bp'])}] bp，均未达到 `p<0.01`。
4. 110 笔里只有 {concentration['positive_trades']} 笔盈利；去掉最大一笔后均值变为
   **{_fmt(concentration['mean_without_top1_bp'])} bp/笔**，正收益高度依赖少数长趋势反转单。
5. V10 的 `vol_ratio_mean8 >= 1` 历史点估计更好（+{_fmt(v10['project_net_bp_per_trade'])} bp/笔、
   回撤 {_fmt(v10['max_drawdown_15m_percent'])}%），但它是在 V9 final 已看过之后才选出的，
   且 sign-flip `p={_fmt(v10_stats['week_block_signflip']['p_value'], 4)}`；只能作为下一轮纸面 forward 假设。

因此，**V9 是最后一个在 final 前锁定的 15m 研究基线；V10 是下一步候选。两者都不能上线。**

![V8/V9/V10 final-preholdout equity](../experiments/active/exp-pine-eth-15m-v1/results/charts/final_equity_v8_vs_v9.png)

## Context & Methods

### 数据与切分

| 项目 | 值 |
|---|---|
| 数据 | OKX `ETH-USDT-SWAP` 15m CSV |
| 行数 | {quality['rows_read']:,} |
| 时间 | {quality['first_bar']} → {quality['last_bar']} |
| 15m 缺口 / 重复 / OHLC 错误 | {quality['non_15m_gaps']} / {quality['duplicate_timestamps']} / {quality['ohlc_body_violations']} |
| Discovery | 2023-01-01 → 2024-01-01 |
| Confirmation | 2024-01-01 → 2025-01-01 |
| Final-preholdout | 2025-01-01 → 2026-03-01（已消耗，今后不再称未见 OOS） |
| Repository holdout | >= 2026-05-04；策略计算读取 0 行、配置评估 0 次；另见下方意外预览披露 |

所有特征只用信号 bar `t` 及以前数据；订单在 `t+1` 开盘成交。止损/标签才读取未来。
正式成本固定为单边 0.10%，即往返 20 bp；没有滑点、资金费、强平和共享保证金模拟。

### V9 信号与执行契约

- `hl2` 的 SMA10/SMA60 金叉/死叉；close 需位于 SMA60 与 EMA100 的同方向一侧；
- 原振荡器：`hl2-SMA40 → trailing 200-bar p99 → ratio → 10-bar change → HMA10`；
- oscillator 方向阈值 `±0.1`，并要求继续上升/下降；
- 项目判断特征 `EMA200.pct_change(12)` 必须与方向一致；
- HK 21:00–23:00 禁入、周日禁入、ATR% 0.1–10%；
- 初始止损 `min(4×Pine ATR14, signal close 的 3%)`，按 0.01 tick 舍入并锚定实际 fill；
- 完成 bar 达到 +1.5% 后，下一 bar 起锁 +0.1%；trailing 关闭；
- opposite signal 单次反转，legacy 03:00/周四仓位倍增关闭；
- 仓位默认每笔止损风险 1%，13x 只是上限，final 实际最大杠杆 {_fmt(v9['max_leverage'])}x。

### 10m 改到 15m 到底改变了什么

保留 10/60 根后，墙钟窗口由约 100/600 分钟变为 **150/900 分钟**，策略变慢 50%。
我们把“按原 10m 墙钟长度换算到 15m”（约 7/40、EMA67、osc 27/133/7/7）作为一个概念变量，
ATR14 和止损完全不动。结果：

{markdown_table(['开发块', '笔数', '净 bp/笔', '资金收益 %', '15m DD %'], rescale_rows)}

四块有两块净期望为负，所以拒绝等时长搬运；15m 版本应视为一个新的、较慢的固定策略。

### 核心逻辑嵌套消融（只读 2023/2024）

下面每一步只增加一个信号组件，执行、止损、仓位、冷却和 20 bp 成本完全不变：

{markdown_table(['信号阶段', '总笔数', '最差半年 bp', '加权 bp', '正半年'], core_rows)}

![Nested core component ablation](../experiments/active/exp-pine-eth-15m-v1/results/charts/core_component_ablation.png)

单纯 SMA10/60 crossover 和再加 EMA100 的四个开发半年全部成本后为负；`EMA200 slope12`
是第一个产生正加权期望的组件，但仍有一个半年为负。振荡器方向继续降噪，最终 `±0.1`
门才让四块都为正。因此当前核心应理解为**趋势方向一致后的稀疏交叉触发**，而不是已经验证的
“均线密集形态”。这也解释了为什么把严格 ribbon density 强塞进去反而不稳定。

## Results

### 主版本对照

{markdown_table(['版本', '时期', '笔数', '毛 bp/笔', '净 bp/笔', 'PF', '收益 %', '15m DD %'], comparison_rows)}

`*` V10 是 post-selection 历史诊断，不是独立 OOS。

### 匹配随机、排序检验与尾部依赖

| 检验 | V9 结果 | 判定 |
|---|---:|---|
| 策略净收益 | +{_fmt(v9_match['mean_candidate_net_bp'])} bp/笔 | 点估计为正 |
| 匹配随机净收益 | {_fmt(v9_match['mean_control_net_bp'])} bp/笔 | 同月×HK 6h×ATR 五分位×方向×持有期 |
| 策略 - 对照 | +{_fmt(v9_match['mean_excess_bp'])} bp/笔 | 正，但不显著 |
| UTC 周 sign-flip | p={_fmt(v9_flip['p_value'], 4)} | **未过 p<0.01** |
| 绝对收益周 bootstrap | [{_fmt(v9_abs['ci95_low_bp'])}, {_fmt(v9_abs['ci95_high_bp'])}] bp | 跨 0 |
| oscillator top-decile | +{_fmt(ranking['top_decile_net_bp'])} bp/笔，p={_fmt(ranking['p_value'], 4)} | 排序不显著 |
| oscillator AUC（净正收益） | {_fmt(ranking['auc_net_positive'], 4)} | 仅参考，不是成功标准 |
| 盈利笔数 | {concentration['positive_trades']}/{concentration['trades']} | 低胜率趋势策略 |
| 最大一笔占净收益 | {_fmt(concentration['top1_share_of_net'] * 100)}% | 去掉后均值转负 |
| 正月份 | {concentration['positive_months']}/{concentration['months_with_trades']} | 月中位数 {_fmt(concentration['median_monthly_net_bp'])} bp/笔 |

![V9 monthly expectancy](../experiments/active/exp-pine-eth-15m-v1/results/charts/v9_monthly_net_bp.png)

### 参数选择与单变量实验

oscillator 阈值只在 2023/2024 搜索；`0.1` 与 `0.15` 形成稳定平台，选择 `0.1` 是因为样本更多、
加权期望更高，不是因为看了 final。

{markdown_table(['阈值', '示例块笔数', '最差半年 bp', '四块加权 bp', '状态'], threshold_rows)}

![Threshold robustness](../experiments/active/exp-pine-eth-15m-v1/results/charts/oscillator_threshold_robustness.png)

项目 28 个因果特征逐个用自然阈值筛选后的开发段前八名如下；没有训练 LR/LightGBM：

{markdown_table(['单特征门', '示例块笔数', '最差半年 bp', '四块加权 bp', '选择'], feature_rows)}

增量 prequential 检查每次只用已经结束的半年选 gate，再测紧接着的下半年：

{markdown_table(['选择所用历史块', '下一测试块', '选中 gate', 'gate bp', 'V9 bp', '增量 bp'], prequential_rows)}

三次都选中 `vol_ratio_mean8 >= 1`，增量三次为正，测试块加权期望为
{_fmt(robustness['prequential_feature_replay']['selected_weighted_test_net_bp'])} bp，
同期 V9 为 {_fmt(robustness['prequential_feature_replay']['baseline_weighted_test_net_bp'])} bp。
这是 V10 值得 paper A/B 的最好证据，但只有三个测试块，精确 sign-flip 最低也只能到
`p={_fmt(robustness['prequential_feature_replay']['increment_exact_signflip']['p_value'], 4)}`；
对 18 个 gate 的搜索做共同区块 max-stat 校正后为
`p={_fmt(robustness['selection_adjusted_feature_test']['selection_adjusted_p_value'], 4)}`。

所以 `vol_ratio_mean8 >= 1` 的 V10 虽改善历史点估计和回撤，但 final 已污染、尾部更集中，
且多重选择校正未过，不能冒充验证成功。

止损/退出结论：

- 保留 break-even：关闭后单位期望有些块更高，但交易更少、资金 PF/收益不一致，证据不足以替换；
- 拒绝 trailing：开发段搜索的所有 2.5–10% 激活、1–5% 距离组合，最差半年都为负；
- `close only` 反转模式最差半年 +22.32 bp，低于 V9 的 +41.49 bp，拒绝；
- 初始 `4×ATR / 3% cap`、ATR 下限和 20 bp 成本未调参。

### 仓位风险与回撤

仓位只改变资金路径，不改变 +{_fmt(v9['project_net_bp_per_trade'])} bp/笔的单位期望：

{markdown_table(['每笔风险 %', '资金收益 %', '15m DD %', '均值杠杆', '最大杠杆'], risk_rows)}

本轮把 **1%** 作为默认研究风险：0.5% 更稳但收益低；2% 的历史回撤已达 {_fmt(risk_final.loc[risk_final.risk_percent.eq(2.0), 'max_drawdown_15m_percent'].iloc[0])}%。
正式 20 bp 成本下 V9 毛期望 {_fmt(cost_break_even['v9_locked'])} bp/笔，意味着总成本接近该值时点估计归零；
资金费和真实滑点未建模，不能忽略。

### 独立回测框架复核

本机 `Backtesting.py {framework['framework_version']}` 的独立回放得到：

- 110/110 入场时间一致，110/110 出场时间一致；
- 最大入/出价格误差分别 {_fmt(framework['max_entry_price_error'], 10)} / {_fmt(framework['max_exit_price_error'], 10)}；
- 最大单位收益误差 {_fmt(framework['max_unit_return_error_bp'], 10)} bp；
- 框架收益 +{_fmt(framework['framework_return_percent'])}%、最大回撤 {_fmt(framework['framework_max_drawdown_percent'])}%，
  与自定义引擎 +{_fmt(v9['return_percent'])}% / {_fmt(v9['max_drawdown_15m_percent'])}% 接近。

这通过了**独立 Python 框架 reconciliation**，但不是 TradingView broker-emulator parity。
Pine 仍需在同一交易所 15m 图表编译并导出逐笔 ledger。

同一 OKX 合约的 3m 有序路径又做了一层执行敏感性复核：

- 2025-01 至 2026-02 共 {intrabar['parent_bar_reconstruction']['joined_15m_bars']:,} 根 15m 母 K，
  每根都恰好由五根 3m K 构成，OHLC 最大误差为 0；
- V9 的 {intrabar['canonical_trade_count']} 笔中，
  {intrabar['same_15m_exit_parent_count']}/{intrabar['canonical_trade_count']} 出场母 K 一致，
  {intrabar['exact_exit_price_count']}/{intrabar['canonical_trade_count']} 出场价一致；
- {intrabar['stop_trade_count']} 笔止损没有发现 15m 聚合隐藏的 3m 跳空劣化，
  单笔净收益最大差 {_fmt(intrabar['maximum_absolute_net_return_delta_bp'], 10)} bp。

这只证明**本地 OKX 同源 15m 执行没有被聚合路径高估**；Pine 设置仍为
`use_bar_magnifier=false`，也没有替代 TradingView venue-specific parity。

## 项目判断层（用户称 LR）能否接入

可以接，但当前不能把旧模型直接拿来判定：

1. 仓库生产判断层实际是 LightGBM；LogisticRegression 只是 `ma_spread_pct` 单特征 baseline。
2. 当前冻结 v10 是短侧 YOLO-v10 候选池、72-bar barrier 标签、legacy feature semantics；Pine 是双向交叉候选和反转/BE 退出，分布与目标都不同。
3. `models/active_bundle.json` 缺失，生产协议 fail-closed；当前真实生产模型数是 0。
4. P0/P1 `training_eligible=false`，因此本轮没有新训练、没有加载旧模型做伪 OOS 打分。

本轮已经输出每个 V9 final 信号 bar 的 28 个因果特征、side-aligned 语义和 label end：
`results/pine_l2_feature_rows.csv`，但每行都标记 `training_eligible=false`。

未来获得训练许可后，正确链路是：

```text
Pine confirmed close(t)
  → 28 causal L2 features available_at close(t)
  → Pine-specific LR baseline + LightGBM ranker
  → calibration-only q90 threshold
  → entry at open(t+1)
```

标签必须用同一套 Pine 入场、反转、止损、BE 和 20 bp 成本；按时间 walk-forward，按 label end purge，
并同时报告 top-decile 净收益、p<0.01、匹配随机对照。不能把旧 YOLO L2 分数当成 Pine 已验证 gate。

## 风险与诚实声明

- **Final 已消耗。** 2025-01 至 2026-02 已用于 V9 单次终测；V10 是其后的 post-selection 假设。
- **统计未过门。** V9/V10 的区块 p 值都远高于 0.01，CI 跨 0，不能说收益已稳定。
- **特征搜索未过多重校正。** `vol_ratio_mean8 >= 1` 的四块增量都为正，但 18-gate max-stat `p={_fmt(robustness['selection_adjusted_feature_test']['selection_adjusted_p_value'], 4)}`；V10 仍只是 paper-forward 假设。
- **收益高度集中。** V9 去掉最大赢家后转负；V10 集中更严重。
- **数据源未 parity。** OKX cache 不能证明等于任意 TradingView `ETHUSDT.P`。
- **成本不完整。** 正式扣 20 bp，但滑点/资金费/强平/最小下单量未进入资金曲线。
- **没有真实密集门。** 原始核心是 SMA10/60 单点 crossover，不是项目 Local Signal V2；严格 EMA ribbon density gate 在开发段不稳定。
- **没有训练或生产动作。** 所有策略计算的 bounded loader 都读取 0 行 holdout；未 train、promote、deploy、改 ACTIVE、写 forward_log 或操作真金账户。
- **意外 holdout 预览已披露。** 在写 3m bounded loader 前，一次 shell `tail` 意外显示了原始文件末尾两行（均在 repository holdout）；它们没有进入 Python、没有被评分、没有参与任何配置选择或收益评估。按本仓“看一眼也要记录”的纪律，此事故不能写成“从未看见”，后续已用前缀加载器和不读取整文件的前缀哈希封死。
- **Docker 首次拉镜像依赖外部 Docker Hub。** 主机两套引擎已复核；容器结果单独记录，不拿失败拉取冒充代码失败。

## 下一步选项（需 owner 决策的已标出）

1. **可立即做：** 在明确的 TradingView venue 上粘贴 V9 Pine，导出 trade list；对 signal/entry/exit/fee/equity 逐笔对账。未过不得 forward。
2. **可立即做：** V9 与 V10 只做 paper-only 新鲜前向 A/B，各积累至少 100 笔；V10 不追认旧 final 为 OOS。
3. **需 owner 在 P0/P1 通过后批准：** 建 Pine-specific LR baseline / LightGBM judgment dataset 与训练；当前硬门禁止。
4. **需 owner 单独批准：** 任何 TP/SL 倍数、ATR 下限、20 bp 成本或 holdout 评估；本轮都没动。
5. **不建议：** 用 2% 风险或 legacy 日历倍增掩盖不显著的单位 alpha。

## 复现命令

```bash
git checkout {summary['generated_from_commit']}
PYTHONPATH=. .venv/bin/python scripts/research_pine_eth_15m.py
PYTHONPATH=. .venv/bin/python scripts/analyze_pine_eth_15m_robustness.py
PYTHONPATH=. python3 scripts/reconcile_pine_eth_15m_backtesting.py
PYTHONPATH=. python3 scripts/reconcile_pine_eth_15m_intrabar.py
PYTHONPATH=. .venv/bin/python scripts/validate_pine_eth_15m.py
PYTHONPATH=. /tmp/fable-pine-eval-venv/bin/python scripts/build_pine_eth_15m_report.py
PYTHONPATH=. .venv/bin/python scripts/md_to_html.py \\
  analysis/p0_pine_eth_15m_v1_20260821.md --out-dir analysis/html
PYTHONPATH=. .venv/bin/python -m pytest -q \\
  tests/test_pine_allin_v7_backtest.py tests/test_research_pine_eth_15m.py tests/test_reconcile_pine_eth_15m_intrabar.py tests/test_analyze_pine_eth_15m_robustness.py
```

Docker：

```bash
docker build -t fable-pine-eth15m-v1 \\
  experiments/active/exp-pine-eth-15m-v1/docker
docker run --rm --network none -v "$PWD:/workspace:ro" \\
  -v "$PWD/experiments/active/exp-pine-eth-15m-v1/results-docker:/output" \\
  fable-pine-eth15m-v1
```

验证器结果：**{validation['counts']['checks']}/{validation['counts']['checks']} checks pass**。
"""
    return report


def make_notebook(evidence: dict[str, Any]) -> nbformat.NotebookNode:
    cells = [
        nbformat.v4.new_markdown_cell(
            "# ETH perpetual 15m Pine V1 audit\n\n"
            "**TL;DR:** V9 improves the consumed final-preholdout ETH result to +30.22 bp/trade "
            "after the frozen 20 bp cost, but week-block p=0.17 and the mean turns negative after "
            "removing the largest winner. V10 is only a post-selection forward hypothesis."
        ),
        nbformat.v4.new_code_cell(
            "from pathlib import Path\n"
            "import json, pandas as pd\n"
            "ROOT=Path.cwd(); EXP=ROOT/'experiments/active/exp-pine-eth-15m-v1'; R=EXP/'results'\n"
            "validation=json.loads((R/'validation.json').read_text())\n"
            "summary=json.loads((R/'summary.json').read_text())\n"
            "stats=json.loads((R/'statistical_tests.json').read_text())\n"
            "intrabar=json.loads((R/'intrabar_3m_reconciliation.json').read_text())\n"
            "robustness=json.loads((R/'robustness_checks.json').read_text())\n"
            "split=pd.read_csv(R/'split_summary.csv')\n"
            "risk=pd.read_csv(R/'risk_grid.csv')\n"
            "assert validation['status']=='pass' and summary['holdout_consumed'] is False\n"
            "print(f\"{validation['counts']['checks']} checks pass; holdout consumed: 0\")"
        ),
        nbformat.v4.new_markdown_cell(
            "## Context & Methods\n\nSignal close at t, entry at open(t+1); time splits only; "
            "20 bp round-trip cost; exact ETH-month × HK 6h × ATR-quintile controls with unique starts; "
            "week-clustered inference."
        ),
        nbformat.v4.new_code_cell(
            "cols=['variant','period','trades','project_net_bp_per_trade','monetary_profit_factor',"
            "'return_percent','max_drawdown_15m_percent']\n"
            "split.loc[split.variant.isin(['v8_eth_baseline','v9_locked','v10_volume_hypothesis']),cols].round(4)"
        ),
        nbformat.v4.new_code_cell(
            "pd.DataFrame({\n"
            " 'metric':['V9 candidate bp','matched control bp','excess bp','week signflip p','absolute CI low','absolute CI high'],\n"
            " 'value':[stats['matched_control']['mean_candidate_net_bp'],stats['matched_control']['mean_control_net_bp'],"
            "stats['matched_control']['mean_excess_bp'],stats['week_block_signflip']['p_value'],"
            "stats['week_bootstrap_absolute']['ci95_low_bp'],stats['week_bootstrap_absolute']['ci95_high_bp']]})"
        ),
        nbformat.v4.new_code_cell(
            "stats['profit_concentration']"
        ),
        nbformat.v4.new_code_cell(
            "risk.loc[risk.period.eq('final_preholdout_2025_202602'),"
            " ['risk_percent','return_percent','max_drawdown_15m_percent','mean_leverage','max_leverage']].round(4)"
        ),
        nbformat.v4.new_markdown_cell(
            "## Decision assertions\n\nThe notebook deliberately asserts the failures as well as arithmetic validity."
        ),
        nbformat.v4.new_code_cell(
            "v9=summary['v9_final_preholdout']\n"
            "assert v9['project_net_bp_per_trade'] > 0\n"
            "assert stats['week_block_signflip']['p_value'] >= 0.01\n"
            "assert stats['week_bootstrap_absolute']['ci95_low_bp'] < 0\n"
            "assert stats['profit_concentration']['mean_without_top1_bp'] < 0\n"
            "assert intrabar['data_quality']['holdout_rows_read'] == 0\n"
            "assert intrabar['same_15m_exit_parent_count'] == 110\n"
            "assert intrabar['exact_exit_price_count'] == 110\n"
            "assert robustness['final_preholdout_rows_read'] == 0\n"
            "assert robustness['selection_adjusted_feature_test']['selection_adjusted_p_value'] >= 0.01\n"
            "assert summary['tradingview_parity_passed'] is False\n"
            "print('Point estimate positive; robustness and parity gates correctly remain failed.')"
        ),
    ]
    return nbformat.v4.new_notebook(
        cells=cells,
        metadata={
            "kernelspec": {
                "display_name": "Fable Pine Eval",
                "language": "python",
                "name": "fable-pine-eval",
            },
            "language_info": {"name": "python", "version": "3.9"},
        },
    )


def main() -> None:
    evidence = load_evidence()
    diagnostics = build_diagnostics(evidence)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(build_report(evidence, diagnostics), encoding="utf-8")

    NOTEBOOKS.mkdir(parents=True, exist_ok=True)
    notebook = make_notebook(evidence)
    client = NotebookClient(
        notebook,
        timeout=600,
        kernel_name="fable-pine-eval",
        resources={"metadata": {"path": str(PROJECT)}},
        record_timing=False,
    )
    executed = client.execute()
    notebook_path = NOTEBOOKS / "pine_eth_15m_v1_audit.ipynb"
    nbformat.write(executed, notebook_path)
    print(f"wrote {RESULTS / 'diagnostics.json'}")
    print(f"wrote {REPORT}")
    print(f"wrote {notebook_path}")


if __name__ == "__main__":
    main()
