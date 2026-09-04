#!/usr/bin/env python3
"""Build the BTCUSDT.P 15m/5m parameter-optimization research report."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / (
    "experiments/active/"
    "exp-btcusdtp-k1k2-15m-5m-params-preholdout-20260904-v2"
)
RESULTS = EXPERIMENT / "results"
REPORT = PROJECT / "analysis/p1_btcusdtp_k1k2_15m_5m_parameter_optimization_preholdout_20260904.md"
IMAGE_ROOT = (
    "../experiments/active/"
    "exp-btcusdtp-k1k2-15m-5m-params-preholdout-20260904-v2/results"
)


def pct(value: float) -> str:
    return "—" if not np.isfinite(value) else f"{value * 100:.1f}%"


def number(value: float, digits: int = 2, signed: bool = False) -> str:
    if not np.isfinite(value):
        return "—"
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def outcome_diagnostics(events: pd.DataFrame, bar: str) -> dict[str, Any]:
    sl = events[events["outcome"].astype(str).str.startswith("sl")].copy()
    tp = events[events["outcome"].eq("tp")].copy()
    protection = events[events["outcome"].astype(str).str.startswith("protected")]
    early = sl["mfe_r"].lt(0.5)
    middle = sl["mfe_r"].ge(0.5) & sl["mfe_r"].lt(1.5)
    late = sl["mfe_r"].ge(1.5)
    minutes = 15 if bar == "15m" else 5
    return {
        "bar": bar,
        "events": len(events),
        "sl": len(sl),
        "sl_before_05r": int(early.sum()),
        "sl_05_to_15r": int(middle.sum()),
        "sl_after_15r": int(late.sum()),
        "early_sl_share": float(early.mean()) if len(sl) else np.nan,
        "median_sl_bars": float(sl["hold_bars"].median()) if len(sl) else np.nan,
        "median_sl_minutes": float(sl["hold_bars"].median() * minutes) if len(sl) else np.nan,
        "median_fee_to_risk": float(events["fee_to_risk"].median()),
        "protection_armed": int(events["protection_armed"].sum()),
        "protected_exits": len(protection),
        "tp": len(tp),
        "tp_hit_4r_in_horizon": int(tp["horizon_hit_4r"].sum()),
        "tp_hit_5r_in_horizon": int(tp["horizon_hit_5r"].sum()),
        "tp_hit_6r_in_horizon": int(tp["horizon_hit_6r"].sum()),
        "tp_horizon_mfe_median": float(tp["horizon_mfe_r"].median()) if len(tp) else np.nan,
    }


def group_diagnostics(events: pd.DataFrame, column: str) -> pd.DataFrame:
    return (
        events.groupby(column, sort=True)
        .agg(
            events=("setup_id", "size"),
            mean_gross_bp=("gross_return", lambda values: values.mean() * 1e4),
            mean_net_bp=("net_return", lambda values: values.mean() * 1e4),
            win_rate=("net_return", lambda values: values.gt(0.0).mean()),
            median_mfe_r=("mfe_r", "median"),
        )
        .reset_index()
    )


def main() -> None:
    config = json.loads((EXPERIMENT / "config.json").read_text(encoding="utf-8"))
    selection = json.loads((RESULTS / "selection_receipt.json").read_text(encoding="utf-8"))
    original_metrics = pd.read_csv(RESULTS / "validation_metrics.csv")
    corrected_metrics = pd.read_csv(RESULTS / "validation_metrics_corrected.csv")
    correction = pd.read_csv(RESULTS / "validation_artifact_correction.csv")
    ledgers: dict[str, pd.DataFrame] = {}
    diagnostics: list[dict[str, Any]] = []
    directions: list[pd.DataFrame] = []
    gaps: list[pd.DataFrame] = []
    for bar in ("15m", "5m"):
        events = pd.read_csv(
            RESULTS / f"validation_{bar}_selected_trades.csv.gz",
            parse_dates=["entry_time", "exit_time"],
        )
        ledgers[bar] = events
        diagnostics.append(outcome_diagnostics(events, bar))
        direction = group_diagnostics(events, "direction")
        direction.insert(0, "bar", bar)
        directions.append(direction)
        gap = group_diagnostics(events, "gap_bars")
        gap.insert(0, "bar", bar)
        gaps.append(gap)
    diagnostic_frame = pd.DataFrame(diagnostics)
    direction_frame = pd.concat(directions, ignore_index=True)
    gap_frame = pd.concat(gaps, ignore_index=True)
    diagnostic_frame.to_csv(RESULTS / "validation_failure_and_extension_diagnostics.csv", index=False)
    direction_frame.to_csv(RESULTS / "validation_direction_diagnostics.csv", index=False)
    gap_frame.to_csv(RESULTS / "validation_gap_diagnostics.csv", index=False)

    validation_rows: list[list[Any]] = []
    for bar in ("15m", "5m"):
        inherited = original_metrics[
            original_metrics["bar"].eq(bar) & original_metrics["arm"].eq("inherited")
        ].iloc[0]
        selected = corrected_metrics[corrected_metrics["bar"].eq(bar)].iloc[0]
        validation_rows.extend(
            [
                [
                    bar,
                    "Inherited",
                    int(inherited["events"]),
                    number(inherited["mean_gross_bp"], signed=True),
                    number(inherited["mean_net_bp"], signed=True),
                    number(inherited["profit_factor"], 3),
                    pct(inherited["win_rate"]),
                    pct(inherited["equal_risk_1pct_return"]),
                    "—",
                    "—",
                ],
                [
                    bar,
                    "Selected",
                    int(selected["events"]),
                    number(selected["mean_gross_bp"], signed=True),
                    number(selected["mean_net_bp"], signed=True),
                    number(selected["profit_factor"], 3),
                    pct(selected["win_rate"]),
                    pct(selected["equal_risk_1pct_return"]),
                    number(selected["matched_control_excess_bp"], signed=True),
                    number(selected["paired_signflip_p_one_sided"], 3),
                ],
            ]
        )

    parameter_names = [
        "gap_min_bars",
        "gap_max_bars",
        "k1_min_body_ratio",
        "k1_min_range_atr",
        "k1_min_directional_close_location",
        "k1_min_sma40_cross_depth_atr",
        "k2_min_rejection_wick_share",
        "k2_max_body_ratio",
        "k2_min_rejection_close_location",
        "k2_touch_depth_atr_max",
        "oscillator_gate",
        "k1_min_volume_ratio_20",
        "fee_to_risk_max",
    ]
    selected_params = {
        bar: selection["timeframes"][bar]["selected_params"] for bar in ("15m", "5m")
    }
    param_rows = [
        [
            name,
            selected_params["15m"].get(name),
            selected_params["5m"].get(name),
            "changed" if selected_params["15m"].get(name) != selected_params["5m"].get(name) else "same",
        ]
        for name in parameter_names
    ]

    development_rows: list[list[Any]] = []
    for bar in ("15m", "5m"):
        current = selection["timeframes"][bar]
        initial = current["initial_metrics"]
        selected = current["selected_metrics"]
        moves = [
            step["family"]
            for step in current["steps"]
            if step["before"] != step["after"]
        ]
        development_rows.append(
            [
                bar,
                int(initial["events"]),
                number(float(initial["mean_net_bp"]), signed=True),
                number(float(initial["robust_score_bp"]), signed=True),
                int(selected["events"]),
                number(float(selected["mean_net_bp"]), signed=True),
                number(float(selected["robust_score_bp"]), signed=True),
                ", ".join(moves) if moves else "none",
                "yes" if selected["eligible"] else "no",
            ]
        )

    slice_rows: list[list[Any]] = []
    for bar in ("15m", "5m"):
        slices = pd.read_csv(RESULTS / f"validation_{bar}_selected_slices_corrected.csv")
        for row in slices.itertuples(index=False):
            slice_rows.append(
                [
                    bar,
                    row.fold,
                    int(row.events),
                    number(float(row.mean_gross_bp), signed=True),
                    number(float(row.mean_net_bp), signed=True),
                    number(float(row.profit_factor), 3),
                    pct(float(row.win_rate)),
                ]
            )

    failure_rows: list[list[Any]] = []
    extension_rows: list[list[Any]] = []
    for row in diagnostic_frame.itertuples(index=False):
        failure_rows.append(
            [
                row.bar,
                row.sl,
                row.sl_before_05r,
                pct(row.early_sl_share),
                row.sl_05_to_15r,
                row.sl_after_15r,
                number(row.median_sl_bars, 1),
                number(row.median_sl_minutes, 0),
                number(row.median_fee_to_risk, 2),
            ]
        )
        extension_rows.append(
            [
                row.bar,
                row.tp,
                row.tp_hit_4r_in_horizon,
                row.tp_hit_5r_in_horizon,
                row.tp_hit_6r_in_horizon,
                number(row.tp_horizon_mfe_median, 2),
                row.protection_armed,
                row.protected_exits,
            ]
        )

    direction_rows = [
        [
            row.bar,
            "Long" if int(row.direction) == 1 else "Short",
            int(row.events),
            number(row.mean_gross_bp, signed=True),
            number(row.mean_net_bp, signed=True),
            pct(row.win_rate),
        ]
        for row in direction_frame.itertuples(index=False)
    ]
    gap_rows = [
        [
            row.bar,
            int(row.gap_bars),
            f"{int(row.gap_bars) * (15 if row.bar == '15m' else 5)}m",
            int(row.events),
            number(row.mean_gross_bp, signed=True),
            number(row.mean_net_bp, signed=True),
            pct(row.win_rate),
        ]
        for row in gap_frame.itertuples(index=False)
    ]

    trace = pd.read_csv(RESULTS / "development_selection_trace.csv")
    trace_notes: list[str] = []
    for bar in ("15m", "5m"):
        current = trace[trace["bar"].eq(bar)]
        if bar == "15m":
            trace_notes.append(
                "- **15m:** only `K1 range/ATR = 0.80` and `K2 rejection close location = 0.65` crossed the locked +2bp/worst-fold gate. Gap, candle body, wick, touch depth, oscillator colour, volume and fee/risk stayed unchanged."
            )
        else:
            gap = current[current["family"].eq("gap_window")].sort_values(
                "robust_score_bp", ascending=False
            ).iloc[0]
            body = current[current["family"].eq("k2_max_body_ratio")].sort_values(
                "robust_score_bp", ascending=False
            ).iloc[0]
            fee = current[
                current["family"].eq("fee_to_risk_max")
                & current["value_json"].astype(str).eq("2.0")
            ].iloc[0]
            trace_notes.append(
                f"- **5m:** no move was legal. The best gap trace (`{gap.value_json}`) had robust score {gap.robust_score_bp:+.2f}bp but only {int(gap.minimum_fold_events)} events in its thinnest half-year versus 20 required. `K2 body <= {body.value_json}` looked better ({body.robust_score_bp:+.2f}bp) but had only {int(body.minimum_fold_events)} in the thinnest fold. Loosening fee/risk to 2.0 reached eligibility but worsened robust score to {fee.robust_score_bp:+.2f}bp, so it was correctly rejected."
            )

    source = selection["timeframes"]["5m"]["source"]
    correction_rows = [
        [
            row.bar,
            int(row.original_candidate_exclusion_signals),
            int(row.accepted_inherited_exclusion_signals),
            number(row.original_matched_excess_bp, signed=True),
            number(row.corrected_matched_excess_bp, signed=True),
            number(row.corrected_p_one_sided, 3),
        ]
        for row in correction.itertuples(index=False)
    ]

    report = f"""# BTCUSDT.P 15m / 5m K1→K2 独立参数优化（pre-holdout）

生成日期：2026-09-04  
实验：`{config['experiment_id']}`  
结论：**15m 与 5m 均未通过冻结验证，不可用于实盘。**

## 先说结论

这轮确实把 15m 和 5m 分开调了，不是把 1h 参数简单复制下来。结果很明确：

- **15m 有两项开发期稳定改进**：K1 最低波幅从 `0.95 ATR` 放宽到 `0.80 ATR`，K2 收盘在拒绝影线中的位置从 `0.25` 提高到 `0.65`。冻结验证相对继承版改善 `+4.66bp/笔`，但最终仍为 **-21.12bp/笔**，PF `0.403`。
- **5m 没有参数通过预注册门槛**，所以最终参数保持不变。冻结验证为 **-18.60bp/笔**，PF `0.374`。
- 修正后的匹配随机对照仅显示 15m `+4.39bp`、5m `+2.35bp` 的弱相对优势，`p=0.333/0.359`，既不显著，也远小于固定 20bp 往返成本。
- 不是“再细调一点就行”。15m 毛收益已经是 `-1.12bp/笔`，5m 也只有 `+1.40bp/笔`；即便暂时把成本拿掉，绝对方向优势仍接近零。
- 你之前说盈利单的止盈可能更高，这个观察在低周期同样成立：15m 的 8 个 3R TP 中 8 个在固定 12 小时观察窗内继续到 5R，5m 的 13 个中有 12 个继续到 5R。但这是**退出后的路径诊断**，不能拿来直接改 TP；提高 TP、分批止盈或 runner 必须另开障碍参数实验。

![冻结验证收益与等风险权益]({IMAGE_ROOT}/validation_summary.png)

## 当前完整交易规则

1. 指标状态：`SMA40(HL2)`、Pine/Wilder `ATR14`、MA Shift 的 K 线颜色；所有状态只用当前已完成 K 线及之前数据。
2. K1：方向实体必须贯穿 SMA40；K1 的 MA Shift 颜色必须与方向一致；实体占比、波幅/ATR、方向收盘位置和穿越深度达到下表阈值。
3. K1→K2：间隔 2–8 根；中间每一根收盘都不能回到 SMA40 错误侧，MA Shift 颜色也必须连续在方向侧。
4. K2：必须真实用拒绝影线触碰 SMA40，SMA40 不得进入实体；K2 收盘必须回到方向侧；影线占比、实体上限、拒绝收盘位置和触线深度达到下表阈值。
5. 入场：K2 完成后的下一根开盘；多头止损为 K2 最低点，空头止损为 K2 最高点。
6. 经济门：`0.2% / 初始风险百分比 <= fee_to_risk_max`；初始风险必须在 `0.15–2.50 ATR`。
7. 出场：固定 3R；最长持有 12 小时（15m=48 根，5m=144 根）；同根 TP/SL 冲突按止损优先。
8. 保护：完成 K 线收盘达到 1.5R 后，从下一根起把止损移到覆盖 0.2% 往返成本的位置。
9. 去重：全局冷却 6 小时（15m=24 根，5m=72 根），同方向同一 K1 不重复使用。

{markdown_table(['Parameter', '15m', '5m', 'Relation'], param_rows)}

## 实验设计与数据

- 唯一底层源：OKX 官方月度 1m 归档聚合出的完整 5m K 线；SHA256 `{source['source_sha256']}`。
- 物理范围：`{source['first_time']}` 至 `{source['last_time']}`；15m 由连续 3 根 5m 因果聚合。
- 开发期：2023-01-01 至 2024-12-31，四个半年折；一次按预注册顺序的 coordinate pass。
- 验证期：2025-01-01 至 2026-02-28 16:00 UTC；参数收据先提交，再首次打开验证。
- 仓库 holdout 从 2026-05-04 开始；干净 v2 的源物理截止更早，**holdout 读取 0 行**。
- 固定成本：20bp；资金费率与额外滑点未计。
- 匹配对照：同月份 × UTC 六小时块 × 月内 ATR 五分位，复制方向、风险 ATR、持有期和退出规则，每笔 3 个对照，不放宽 strata。

## 开发期选择结果

{markdown_table(['TF', 'Initial n', 'Initial net bp', 'Initial robust', 'Final n', 'Final net bp', 'Final robust', 'Moved families', 'Eligible'], development_rows)}

{chr(10).join(trace_notes)}

开发期每一个坐标的完整轨迹如下。图中横轴是配置里锁定的网格顺序，不是连续变量拟合。

![开发期单变量轨迹]({IMAGE_ROOT}/development_selection_trace.png)

## 冻结验证

{markdown_table(['TF', 'Arm', 'n', 'Gross bp', 'Net bp', 'PF', 'Win', 'Equal-risk return', 'Matched excess bp', 'paired p'], validation_rows)}

成功门要求：净收益 > 0、匹配超额 > 0 且 `p<0.01`、2025H1/H2 都 > 0。两套系统均失败。

### 时间稳定性

{markdown_table(['TF', 'Slice', 'n', 'Gross bp', 'Net bp', 'PF', 'Win'], slice_rows)}

15m 的 2026P1 只有 5 笔，虽然等名义均值为 `+6.40bp`，但样本太小且等风险累计仍为负，不能覆盖两个完整 2025 半年的亏损。5m 三个切片全负。

### 匹配对照修正

冻结交易账本没有变化。初版对照误把冷却前候选当成排除中心；修正后严格按协议只围绕已接受的继承信号排除。

{markdown_table(['TF', 'Old exclusion signals', 'Correct accepted signals', 'Old excess bp', 'Correct excess bp', 'Correct p'], correction_rows)}

## 为什么失败

### 1. 失败首先发生在 K2 后的最初几根，而不是盈利后回吐

{markdown_table(['TF', 'SL', 'SL <0.5R', 'Share', 'SL 0.5–1.5R', 'SL >=1.5R', 'Median stop bars', 'Median stop minutes', 'Median fee/risk R'], failure_rows)}

- 15m 有 `17/36` 个止损在到达 0.5R 前发生，止损单中位只活 2 根（30 分钟）。
- 5m 更明显：`30/49` 个止损在 0.5R 前发生，中位只活 3 根（15 分钟）。
- 这说明低周期主要病因是 **K2 触线后没有真实延续确认 + K2 极值止损处在微观噪声内**。现有 K 线颜色、振荡器颜色、成交量阈值并没有在开发期稳定地解决它。

### 2. 成本相对初始风险太大

15m 的费用中位数相当于 `0.72R`，5m 达到 `0.96R`。也就是说低周期的一次 20bp 往返成本，接近一整个初始风险单位。15m 要净零成本必须低于其毛期望，但毛期望已经为负；5m 的理论净零成本上限只有约 `1.40bp/笔`，与当前 20bp 相差一个数量级。

### 3. TP 单确实有长右尾，但单纯提高 TP 不是完整答案

{markdown_table(['TF', '3R TP', 'Later hit 4R', 'Later hit 5R', 'Later hit 6R', 'TP horizon MFE median R', 'Protection armed', 'Protected exits'], extension_rows)}

右尾是真实的，但当前亏损主要来自大量早期 SL。提高 TP 只放大少数赢家；同时 1.5R 费用保护会在等待 5R 时改变退出分布。因此下一轮若获批准，应比较“固定 5R”与“3R 部分止盈 + runner”，不能只把数字 3 改成 5。

### 4. 方向和距离没有稳定规律

{markdown_table(['TF', 'Side', 'n', 'Gross bp', 'Net bp', 'Win'], direction_rows)}

15m 多头略好，5m 却是空头略好，方向优势没有跨周期一致性；更像市场阶段 beta，而不是结构规则。

{markdown_table(['TF', 'Gap bars', 'Clock gap', 'n', 'Gross bp', 'Net bp', 'Win'], gap_rows)}

15m 验证里 2–3 根和 8 根看起来较好、4–7 根较差；5m 里 4–5 根较好。但每格只有 5–23 笔，而且开发期并未给出同样排序，所以这些只能作为下一次预注册假设，不能回头裁掉验证亏损。

## 参数问题还是逻辑问题？

结论偏向 **逻辑问题为主、参数问题为辅**：

1. 15m 的 K2 强收盘阈值确实减少了弱拒绝，验证改善约 4.7bp/笔，说明形态参数有信息；但改善后毛期望仍负。
2. 5m 的多数参数变化要么样本不足，要么更差；现有 K1/K2 两根结构在 5m 上不足以抵抗噪声。
3. 下一条最有价值的入场逻辑实验是增加一个**因果确认条件**，例如 K2 后下一根不能重新穿回均线，再下一开盘入场；代价是更晚、更少的入场。它不是本轮参数微调，必须单独预注册。
4. K2 极值外加 ATR 缓冲、TP/runner 和更低真实成交成本都属于止损/障碍/成本假设，按项目纪律需要 owner 明确批准后才能测试。

## 风险与诚实声明

- v1 预检误信了旧 15m 文件的“物理安全”记录；加载时间戳后发现它已覆盖 holdout，信号和收益尚未计算即 fail-closed。该读取被诚实记录为 v1 未授权的配置特定 holdout 触碰 #1，v1 完全废弃。
- v2 改用物理截止 2026-02-28 的官方归档源，holdout 读取 0 行。上一份 1h 报告里“旧 15m 文件物理截止 2026-02-28”的说法也因此需要单独勘误。
- 第一遍开发选择器曾错误允许“不够样本的 incumbent”免除 +2bp 改进条件；验证尚未打开即发现。无效运行完整保存在 `results/invalid_run01/`，修正代码提交后才重跑并封存选择。
- 原验证切片的 `2026P1` 标签与对照排除中心有报告层 bug；修复器逐笔确认交易账本完全不变后只重算报告和 controls。
- 结果没有模型分数，所以 AUC、top-decile 与单特征模型基线不适用；这里用继承规则、时间折和匹配随机入场作为严格零假设对照。
- 未计资金费率和 20bp 以外滑点，真实结果只可能更差；没有训练、promote、ACTIVE/frozen/forward 变更、部署、消息或订单。

## 复现

```bash
python3 -m pytest tests/test_fetch_okx_archives.py tests/test_optimize_btcusdtp_k1k2_intraday_preholdout.py -q
python3 -m src.data.fetch_okx --symbols BTC_USDT_SWAP --bar 5m \\
  --archive-monthly-start 2022-12 --archive-monthly-end 2026-02 \\
  --archive-max-exclusive 2026-03-01T00:00:00Z \\
  --out-dir data/kline_preholdout_okx_5m --workers 1
python3 -m scripts.optimize_btcusdtp_k1k2_intraday_preholdout --phase development
# commit results/selection_receipt.json before the next command
python3 -m scripts.optimize_btcusdtp_k1k2_intraday_preholdout --phase validation
python3 -m scripts.repair_btcusdtp_k1k2_intraday_validation_artifacts
python3 -m scripts.build_btcusdtp_k1k2_intraday_parameter_report
python3 scripts/md_to_html.py {REPORT.relative_to(PROJECT)} --out-dir analysis/html
```

## 下一步选项（需要 owner 决策）

1. **入场确认实验（推荐）**：固定本轮所有障碍与成本，只增加 K2 后确认条款，分别在 15m/5m 开新开发实验。
2. **退出右尾实验**：批准改变障碍参数后，固定信号比较 3R、5R、3R 部分止盈 + runner。
3. **止损缓冲实验**：批准改变止损后，单变量比较 K2 极值与 K2 极值 ± 0.1/0.2 ATR；必须同时报告风险放大与仓位缩小。
4. **执行成本实验**：只有拿到真实 maker/taker 与滑点数据才重估成本；不得为了让回测转正直接把 20bp 改小。
"""
    REPORT.write_text(report, encoding="utf-8")
    print(REPORT)


if __name__ == "__main__":
    main()
