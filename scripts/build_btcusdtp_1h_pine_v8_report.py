#!/usr/bin/env python3
"""Build the owner-facing report for the BTCUSDT.P Pine-v8 1h replay."""
from __future__ import annotations

import hashlib
import json
import platform
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import scipy

PROJECT = Path(__file__).resolve().parents[1]
EXPERIMENT = PROJECT / "experiments/active/exp-btcusdtp-1h-pine-v8-sixmonth-backtest-20260904-v1"
RESULTS = EXPERIMENT / "results"
REPORT = PROJECT / "analysis/p1_btcusdtp_1h_pine_v8_sixmonth_backtest_20260904.md"
PINE = (
    PROJECT
    / "experiments/active/exp-two-key-candle-feature-atlas-v3/pine"
    / "fable_two_key_candle_sma40_retest_v1.pine"
)

FLAG_ZH = {
    "edge_gap_2_7_8": "边缘间距2/7/8",
    "k1_body_below_owner_strict": "K1实体占比<0.65",
    "k1_range_below_owner_strict": "K1振幅<1.25ATR",
    "k1_close_below_owner_strict": "K1方向收盘位置<0.85",
    "k1_volume_below_owner_strict": "K1量比<1.25",
    "k1_ma_colour_misaligned": "K1均线色反向",
    "k2_wick_below_owner_strict": "K2拒绝影线<0.60",
    "k2_body_above_owner_strict": "K2实体占比>0.35",
    "k2_rejection_below_owner_strict": "K2拒绝收盘<0.65",
    "k2_close_back_below_owner_strict": "K2收回均线<0.25ATR",
    "k2_touch_outside_owner_strict": "K2踩线深度不在0.10–1.00ATR",
    "risk_outside_owner_strict": "开盘止损距离不在0.25–2.00ATR",
    "path_close_distance_large": "K2-K1收盘距离>0.75ATR",
    "path_extension_large": "回踩前延伸>1.00ATR",
    "path_wrong_sma40_close": "中间K线收错均线侧",
    "path_ma_colour_not_continuous": "中间均线色不连续",
    "path_volume_relation_outside": "K2/K1量比不在0.50–1.50",
    "oscillator_state_misaligned": "振荡器序列未对齐",
    "market_structure_misaligned": "10/10结构方向未对齐",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "—"
    return str(value).replace("|", "、").replace("\n", " ")


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    headers = list(headers)
    output = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    output.extend("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def _bp(value: float) -> str:
    return f"{value:+.2f}"


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _pf(value: float) -> str:
    return "∞" if np.isinf(value) else f"{value:.3f}"


def _cst(value: Any) -> str:
    return pd.Timestamp(value).tz_convert("Asia/Shanghai").strftime("%Y-%m-%d %H:%M")


def _mechanical_reason(row: pd.Series) -> str:
    hold = int(row["hold_bars"])
    mfe = float(row["mfe_r"])
    mae = float(row["mae_r"])
    if row["outcome"] == "tp":
        if row["path_class"] == "fast_clean_tp":
            return f"第{hold}根触达3R；最大逆向仅{mae:.2f}R，属快速干净推进"
        return f"第{hold}根触达3R；途中MFE {mfe:.2f}R、MAE {mae:.2f}R"
    if row["outcome"] == "timeout":
        return f"12根内TP/SL均未触发，按第12根收盘；MFE {mfe:.2f}R、MAE {mae:.2f}R"
    if row["path_class"] == "immediate_reversal_sl":
        return f"第{hold}根突破K2极值止损；此前最多仅{mfe:.2f}R，属入场后立即反转"
    if row["path_class"] == "giveback_then_sl":
        return f"先走出{mfe:.2f}R，随后全部回吐并于第{hold}根击穿K2极值"
    return f"第{hold}根突破K2极值止损；此前MFE {mfe:.2f}R、MAE {mae:.2f}R"


def _flags(value: Any) -> str:
    if pd.isna(value) or not str(value):
        return "无冻结弱点标签"
    return "；".join(FLAG_ZH.get(item, item) for item in str(value).split("|") if item)


def _trade_rows(frame: pd.DataFrame) -> list[list[Any]]:
    rows: list[list[Any]] = []
    for _, trade in frame.sort_values("entry_i").iterrows():
        rows.append(
            [
                int(trade["trade_number"]),
                _cst(trade["entry_time"]),
                "多" if int(trade["direction"]) > 0 else "空",
                int(trade["gap_bars"]),
                f"{float(trade['stop_distance_atr']):.2f}",
                str(trade["outcome"]).upper(),
                int(trade["hold_bars"]),
                f"{float(trade['mfe_r']):.2f}/{float(trade['mae_r']):.2f}",
                _bp(float(trade["net_return"]) * 10_000.0),
                _mechanical_reason(trade),
                _flags(trade["causal_flags"]),
            ]
        )
    return rows


def main() -> int:
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    validation = json.loads((RESULTS / "validation.json").read_text(encoding="utf-8"))
    if validation.get("status") != "pass":
        raise RuntimeError(f"refusing to report failed validation: {validation.get('failed')}")
    config = json.loads((EXPERIMENT / "config.json").read_text(encoding="utf-8"))
    events = pd.read_csv(
        RESULTS / "trade_ledger.csv",
        parse_dates=["k1_time", "k2_time", "entry_time", "exit_bar_time", "exit_available_at"],
    )
    controls = pd.read_csv(RESULTS / "matched_controls.csv")
    monthly = pd.read_csv(RESULTS / "monthly_summary.csv")
    factors = pd.read_csv(RESULTS / "causal_flag_diagnostics.csv")

    events["cost_r"] = config["execution"]["round_trip_cost_fraction"] / (
        events["risk_price"] / events["entry_price"]
    )
    events["trade_number"] = np.arange(1, len(events) + 1)
    wins = events[events["net_profitable"]].copy()
    losses = events[~events["net_profitable"]].copy()
    matched = summary["matched_candidates"]
    primary = summary["primary_every_signal"]
    no_september = summary["excluding_terminal_partial_month"]
    equal_risk = summary["equal_risk_1pct_sensitivity"]

    side_rows = []
    for side, group in events.groupby("side", sort=False):
        gains = group.loc[group["net_return"].gt(0.0), "net_return"].sum()
        losses_sum = -group.loc[group["net_return"].lt(0.0), "net_return"].sum()
        side_rows.append(
            [
                "多" if side == "long" else "空",
                len(group),
                int(group["net_profitable"].sum()),
                _pct(group["net_profitable"].mean()),
                _bp(group["gross_return"].mean() * 10_000.0),
                _bp(group["net_return"].mean() * 10_000.0),
                _pf(float(gains / losses_sum)),
            ]
        )

    outcome_rows = []
    for outcome, group in events.groupby("outcome", sort=False):
        outcome_rows.append(
            [
                outcome.upper(),
                len(group),
                int(group["net_profitable"].sum()),
                _bp(group["net_return"].mean() * 10_000.0),
                f"{group['return_r'].mean():+.2f}",
                f"{group['hold_bars'].mean():.2f}",
                f"{group['mfe_r'].mean():.2f}",
                f"{group['mae_r'].mean():.2f}",
            ]
        )

    gap_rows = []
    for gap, group in events.groupby("gap_bars", sort=True):
        gap_rows.append(
            [
                int(gap),
                len(group),
                int(group["net_profitable"].sum()),
                _pct(group["net_profitable"].mean()),
                _bp(group["gross_return"].mean() * 10_000.0),
                _bp(group["net_return"].mean() * 10_000.0),
                int(group["outcome"].eq("tp").sum()),
                int(group["outcome"].str.startswith("sl").sum()),
                int(group["outcome"].eq("timeout").sum()),
            ]
        )

    month_rows = []
    for _, row in monthly.iterrows():
        month_rows.append(
            [
                row["month"],
                int(row["n"]),
                f"{int(row['long'])}/{int(row['short'])}",
                f"{int(row['tp'])}/{int(row['sl'])}/{int(row['timeout'])}",
                _pct(float(row["win_rate_net"])),
                _bp(float(row["mean_net_bp"])),
                _bp(float(row["control_mean_net_bp"])) if np.isfinite(row["control_mean_net_bp"]) else "无精确对照",
                _bp(float(row["paired_excess_bp"])) if np.isfinite(row["paired_excess_bp"]) else "—",
            ]
        )

    factor_rows = []
    for _, row in factors.sort_values("mean_net_bp_delta").iterrows():
        factor_rows.append(
            [
                FLAG_ZH.get(row["flag"], row["flag"]),
                f"{int(row['n_with'])}/{int(row['n_without'])}",
                f"{float(row['win_rate_with']) * 100:.1f}%/{float(row['win_rate_without']) * 100:.1f}%",
                f"{float(row['mean_net_bp_with']):+.1f}/{float(row['mean_net_bp_without']):+.1f}",
                f"{float(row['mean_net_bp_delta']):+.1f}",
                f"{float(row['mean_net_permutation_p_two_sided']):.4f}",
                f"{float(row['mean_net_permutation_p_holm']):.4f}",
            ]
        )

    crosscheck_rows = []
    for item in summary["source_crosschecks"]:
        crosscheck_rows.append(
            [
                item["source_path"],
                item["aggregation"],
                item["overlap_rows"],
                item["overlap_first"],
                item["overlap_last"],
                "是" if item["ohlc_exact_match"] else "否",
                item["volume_mismatch_rows"],
            ]
        )

    artifact_paths = [
        EXPERIMENT / "config.json",
        EXPERIMENT / "protocol_amendment_01.json",
        RESULTS / "source_receipt.json",
        RESULTS / "source/okx_BTC_USDT_SWAP_1H.csv.gz",
        RESULTS / "trade_ledger.csv",
        RESULTS / "matched_controls.csv",
        RESULTS / "matched_pairs.csv",
        RESULTS / "monthly_summary.csv",
        RESULTS / "causal_flag_diagnostics.csv",
        RESULTS / "one_position_trades.csv",
        RESULTS / "data_quality.json",
        RESULTS / "summary.json",
        RESULTS / "validation.json",
        RESULTS / "overview.png",
        RESULTS / "reason_diagnostics.png",
        RESULTS / "causal_flag_diagnostics.png",
        PINE,
    ]
    artifact_rows = [
        [str(path.relative_to(PROJECT)), path.stat().st_size, _sha(path)] for path in artifact_paths
    ]

    trade_headers = [
        "#", "入场(CST)", "向", "gap", "风险ATR", "结局", "持有h", "MFE/MAE R",
        "净bp", "机械成功/失败原因", "入场前冻结诊断",
    ]
    report_parts = [
        "# P1：BTCUSDT.P 1h Pine v8 近半年逐笔回测（2026-09-04）",
        """
## 结论

**这套默认参数不具备可确认的稳定盈利能力，结论为 REJECT。** 2026-03-04 00:00 至
2026-09-04 03:00 UTC（右开）共出现 **49 笔**信号，全部有完整 12 小时路径：16 笔触达 3R、
30 笔击穿 K2 极值止损、3 笔超时退出；扣固定 0.2% 往返成本后，成功 19 笔、失败 30 笔，
胜率 **38.78%**。
""".strip(),
        f"""
按每笔等名义计算，毛收益均值 **{primary['mean_gross_bp']:+.2f}bp**，扣费后只剩
**{primary['mean_net_bp']:+.2f}bp/笔**，profit factor **{primary['profit_factor_net']:.3f}**，
49 笔顺序复利 **{_pct(summary['primary_equal_notional_compounded_return'])}**。这个正数没有统计资格：
单侧 sign-flip `p={summary['primary_mean_net_signflip_p_one_sided']:.4f}`，bootstrap 95% CI
为 **[{summary['primary_mean_net_bp_bootstrap_95_ci'][0]:+.2f},
{summary['primary_mean_net_bp_bootstrap_95_ci'][1]:+.2f}]bp/笔**，明显跨零。
""".strip(),
        f"""
最危险的表象是最后两笔：9 月 1 日和 3 日两笔都赢，合计贡献全期净收益的 **1139.1%**；
去掉尚未结束的 9 月，仅前 47 笔就变成 **{no_september['mean_net_bp']:+.2f}bp/笔**、
PF **{no_september['profit_factor_net']:.3f}**。相对 141 笔精确匹配随机入场，前 47 笔候选
虽少亏 **{summary['candidate_minus_control_mean_bp']:+.2f}bp/笔**，但候选自身仍为
{matched['mean_net_bp']:+.2f}bp/笔，而且配对 `p={summary['paired_signflip_p_one_sided']:.4f}`，不能称为形态优势。
""".strip(),
        "![回测总览](../experiments/active/exp-btcusdtp-1h-pine-v8-sixmonth-backtest-20260904-v1/results/overview.png)",
        """
## 冻结合同与 holdout 记录

Owner 明确批准：“批准读取 2026-05-04 之后的价格”。这是当前 Pine v8 默认配置的第 1 次
holdout 使用，不是全项目所有模型的统一次数。读取前已在 git 提交 `1eb5ea4` 冻结以下合同：

- `OKX:BTCUSDT.P` / `BTC-USDT-SWAP`，原生 1H，UTC；
- Pine v8 `Core recall · 2–8`，SMA40(HL2)，多空都做，六根全局 cooldown；
- K2 收盘确认，下一根开盘入场；止损为精确 K2 低点（多）/高点（空）；
- 目标 3R，最多看 12 根；同一根同时打 TP/SL 时保守记 SL；
- 每笔毛收益减 0.2% 往返成本；不含 funding 与额外滑点；
- 三个匹配控制：同月、同 UTC 6 小时时段、同月 ATR14 五分位，复制方向、ATR 风险、3R、12h、成本。

首次运行在结果落盘前因 9 月精确控制池为空而 fail-closed。运行期补丁 `3ed2ef8` 没有放宽匹配，
也没有改任何信号/成交/收益口径：保留主交易；少于 3 个精确控制的候选明确标成 unmatched，
仅不参加配对检验。本次共有 2 笔 unmatched，正是 9 月两笔赢家。
""".strip(),
        """
## 数据质量

官方 OKX `history-candles` 快照从 2025-08-01 00:00 UTC 开始，以提供 1000 根振荡器预热；
最后一根是 2026-09-04 02:00 UTC，合计 9,579 根已确认 1H K 线。零重复、零缺口、零非法 OHLC、
零负成交量。原始快照只保存在实验目录，未覆盖生产缓存。
""".strip(),
        _table(
            ["交叉源", "粒度", "重叠根数", "首根", "末根", "OHLC逐字一致", "成交量差异根数"],
            crosscheck_rows,
        ),
        "15m 聚合源有 1 根成交量相差 5.78 合约，但 9,076 根重叠 OHLC 全部精确一致；信号和出场只依赖 OHLC，未受影响。",
        """
## 总体与多空拆分

“成功”定义为扣 0.2% 后净收益大于 0；不是单纯触达 TP 的别名。本窗 16 笔 TP 与 3 笔超时均为
净正，30 笔 SL 均为净负，且没有同 bar TP/SL 歧义。
""".strip(),
        _table(
            ["方向", "笔数", "成功", "净胜率", "毛bp/笔", "净bp/笔", "净PF"],
            side_rows,
        ),
        _table(
            ["结局", "笔数", "净成功", "净bp/笔", "毛R/笔", "平均持有h", "平均MFE R", "平均MAE R"],
            outcome_rows,
        ),
        f"""
多头 {int(events['side'].eq('long').sum())} 笔与空头 {int(events['side'].eq('short').sum())} 笔都接近零；
没有哪一边单独承担全部问题。中位数是 **{primary['median_net_bp']:+.2f}bp**，显著低于均值，说明均值由少数
大 TP 拉高，而多数交易是小到中等止损。
""".strip(),
        """
## 费用与仓位：为什么毛利看起来可以，实盘口径却不行
""".strip(),
        f"""
零成本、等名义顺序复利为 **{_pct(summary['zero_cost_equal_notional_compounded_return'])}**；加入
49×20bp 往返成本后降为 **{_pct(summary['primary_equal_notional_compounded_return'])}**。K2 极值止损的
价格距离中位数只有 **{events['risk_price'].div(events['entry_price']).median() * 100:.3f}%**，所以 0.2% 成本
中位数相当于 **{events['cost_r'].median():.2f}R**，均值相当于 {events['cost_r'].mean():.2f}R。
信号毛 R 期望是 {primary['mean_return_r']:+.3f}R，扣费后变成 **{primary['mean_net_return_r']:+.3f}R**。
""".strip(),
        f"""
如果按止损距离把每笔仓位调到固定账户风险 1%（事后仓位敏感性，不是预注册主结果），49 笔合计
**{equal_risk['sum_net_return_r']:+.2f}R**，复利 **{_pct(equal_risk['compounded_return'])}**，最大回撤
**{_pct(equal_risk['max_drawdown'])}**。因此“等名义 +0.10%”不能外推成常见的等风险仓位系统盈利。
""".strip(),
        "![失败类型与费用敏感性](../experiments/active/exp-btcusdtp-1h-pine-v8-sixmonth-backtest-20260904-v1/results/reason_diagnostics.png)",
        """
## 成功与失败的路径原因

这里严格区分两类“原因”：

- **机械原因**可以确定：价格何时打到 3R、何时穿过 K2 极值、是否 12 根超时；
- **形态原因**只能叫关联：K1/K2、距离、颜色、路径、振荡器和结构字段都在入场前可见，但 49 笔历史回放不能证明它们导致成败。

19 笔成功中，7 笔是 4 小时内且 MAE≤0.5R 的快速干净 TP，9 笔是较慢 TP，3 笔没有触达 3R、
但第 12 根收盘仍覆盖成本。30 笔失败中，11 笔在前两根几乎没走出 0.5R 就反转止损；8 笔曾经
走出至少 1.5R，却未保护利润、最终全部回吐；另 11 笔为普通止损。TP 平均 4.75 小时，SL 平均
2.47 小时——失败通常比成功更快暴露。
""".strip(),
        """
## K1→K2 距离

距离 2–8 全部按原样报告，没有根据本次结果选择“最佳 gap”。gap=8 三笔全败，但样本只有 3；
gap=2 的均值最高，也只有 11 笔且被大赢家影响。边缘 gap 2/7/8 合并后反而比 3–6 好约 19.4bp，
这与“只收紧到 3–6”并不一致，所以本窗不能支持直接改默认范围。
""".strip(),
        _table(
            ["gap", "n", "成功", "胜率", "毛bp/笔", "净bp/笔", "TP", "SL", "超时"],
            gap_rows,
        ),
        """
## 月份与稳定性

3–8 月六个分段只有 6 月、7 月为正；5 月 5 笔全败。9 月只覆盖 3 天零 6 小时，两笔都赢，不能与
完整月份等权看待，更不能用这两笔宣称策略刚刚“变好了”。
""".strip(),
        _table(
            ["月", "n", "多/空", "TP/SL/超时", "净胜率", "候选净bp", "对照净bp", "候选-对照bp"],
            month_rows,
        ),
        """
## 入场前形态关联：颜色是最明显的线索，但还不是可用过滤器

19 个冻结标签全部一起审计并做 Holm 多重比较校正。最强原始信号是 **K1 均线色反向**：反向组
3/16 成功、均值 -42.97bp；对齐组 16/33 成功、均值 +21.73bp，差 -64.70bp。其次是中间路径有
收盘落到错误 SMA40 一侧，均值差 -53.00bp。它们符合 Owner 对 K 线颜色和均线侧连续性的直觉。

但这两项的收益置换原始 `p` 分别约 0.0156、0.0436；在同时看 19 项后 Holm `p` 变为 0.2966、
0.7843，**没有任何标签通过 0.01**。所以只能把 K1 颜色对齐和中间路径连续性列为下一份全新数据的
预注册候选，不能在本快照上勾选过滤后再把改善当样本外结果。振荡器失配出现在 41/49 笔，几乎没有
区分度；结构未对齐组反而表面更好，说明不能机械叠加所有“看起来正确”的门。
""".strip(),
        _table(
            ["冻结标签（有/无）", "n有/无", "胜率有/无", "净bp有/无", "差值bp", "原始置换p", "Holm p"],
            factor_rows,
        ),
        "![冻结形态标签关联](../experiments/active/exp-btcusdtp-1h-pine-v8-sixmonth-backtest-20260904-v1/results/causal_flag_diagnostics.png)",
        """
## 匹配随机对照与经济门

47 笔有足够精确对照，每笔 3 个，共 141 笔；9 月两笔因部分月份且信号排除半径覆盖可用行而没有
精确对照。没有借用别月、放宽 ATR 桶或缩小禁入区凑数。
""".strip(),
        _table(
            ["口径", "n", "胜率", "毛bp/笔", "净bp/笔", "净PF"],
            [
                ["全部候选", 49, _pct(primary["win_rate_net"]), _bp(primary["mean_gross_bp"]), _bp(primary["mean_net_bp"]), _pf(primary["profit_factor_net"])],
                ["有精确对照的候选", 47, _pct(matched["win_rate_net"]), _bp(matched["mean_gross_bp"]), _bp(matched["mean_net_bp"]), _pf(matched["profit_factor_net"])],
                ["精确匹配随机对照", len(controls), _pct(summary["matched_controls"]["win_rate_net"]), _bp(summary["matched_controls"]["mean_gross_bp"]), _bp(summary["matched_controls"]["mean_net_bp"]), _pf(summary["matched_controls"]["profit_factor_net"])],
            ],
        ),
        _table(
            ["门", "结果", "判定"],
            [
                ["候选扣费均值 > 0", f"{primary['mean_net_bp']:+.2f}bp", "仅数值通过"],
                ["绝对收益 sign-flip p < 0.01", f"p={summary['primary_mean_net_signflip_p_one_sided']:.4f}", "失败"],
                ["候选 - 对照 > 0", f"{summary['candidate_minus_control_mean_bp']:+.2f}bp", "数值通过"],
                ["配对 sign-flip p < 0.01", f"p={summary['paired_signflip_p_one_sided']:.4f}", "失败"],
                ["时间稳定", "3–8 月仅 6、7 月为正；9 月为部分月", "失败"],
                ["等风险 1% 仓位", _pct(equal_risk["compounded_return"]), "失败"],
                ["总判定", "没有稳健、可复现的扣费优势", "REJECT"],
            ],
        ),
        """
## AUC、top-decile 与单特征基线

AUC 与 top-decile 按预注册记为**不适用**：本轮回放的是一个布尔阈值指标，没有预注册按 score 排序
或选择 top-decile 的交易臂；事后拿图上的 `anchor_score` 排序会把这次 holdout 变成调参集。单特征
基线采用 Owner 明确强调、且读取前已冻结的 `K1 MA colour aligned` 二分诊断：对齐组 +21.73bp/笔，
反向组 -42.97bp/笔，但 Holm `p=0.2966`，只能作为下一批数据要验证的假设，不能据此修改当前指标。
""".strip(),
        """
## 逐笔成功明细（19 笔）

`MFE/MAE` 是入场后的最大有利/不利波动，以该笔 K2 止损距离为 1R。最后一列是入场前冻结标签，
只是解释线索；真正决定该笔成功的是倒数第二列记录的价格路径。
""".strip(),
        _table(trade_headers, _trade_rows(wins)),
        """
## 逐笔失败明细（30 笔）
""".strip(),
        _table(trade_headers, _trade_rows(losses)),
        """
## 完整性验证

独立验证为 **PASS（23/23）**：Pine 源码 SHA、官方快照 SHA、零缺口/非法 OHLC、三路重叠 OHLC、
K1/K2 索引、next-open、六根 cooldown、精确 K2 止损、3R、风险区间、49 笔全量重放、逐笔费用、
三个精确匹配控制、unmatched 显式排除、配对差值、信号禁入半径、结果汇总均一致。另对首/中/末三处
信号之后的 OHLCV 做大幅未来扰动，既有信号与入场仍逐字段不变。

自动测试覆盖 3R、SL、同 bar 碰撞保守记 SL、12 根 timeout、尾部 unresolved，以及 holdout/Pine
合同。当前 Pine 源 SHA256 为
`3afa39c8a3bc2d85f329f3fd553b112ef0ca68e5fdc1ff143956b6b5ced09984`。
""".strip(),
        """
## 风险与诚实声明

- 只有 BTC 一个品种、49 笔、一次半年窗；置信区间宽，不能泛化到别的周期或币种。
- 这是按 Pine 源码 SHA 固定的 Python 逐 bar 重放，不是 TradingView Strategy Tester 导出；两者若要做像素/事件逐根 parity，仍需 TradingView 导出事件时间。
- 1H OHLC 不知道同根内部先后顺序；合同保守按 SL 优先。本窗没有出现同 bar TP/SL，因此该假设未改变 49 笔结果。
- 费用固定 0.2%，不含 funding 与额外滑点；真实摩擦更高只会使结果更差，不能使其变好。
- 9 月两笔都是赢家且没有精确控制；报告保留它们的主结果，同时单独给出排除部分末月的敏感性。
- 等风险 1% 是结果后追加的仓位敏感性，不是另一个经预注册验证的策略臂。
- 19 个形态标签在读取前冻结，但与收益的关联仍是探索性；所有多重校正均未通过，禁止据此在同一快照调参。
- 本轮没有训练、promote、部署、ACTIVE/frozen/forward 修改、消息发送或真金下单。
""".strip(),
        """
## 下一步

当前默认参数应保持**研究指标**，不应宣称已获得可实盘的盈利系统。若继续，唯一干净的路径是：

1. 停止在这份 2026-03-04 至 2026-09-04 快照上选参数；它已经是用过一次的 holdout。
2. 把 `K1均线色必须同向` 与 `K1→K2中间收盘不得跨错SMA40侧` 作为下一份未见数据的两个独立候选，一次只验证一个；当前证据只够提出假设。
3. 在任何新收益验证前，先决定成交成本口径和仓位口径。当前最主要的失败不是方向完全随机，而是 0.2% 成本相对 0.30% 中位止损过重。
4. 若 Owner 要改 TP/SL、成本、严格阈值或选 gap，必须视作新配置重新预注册；不能复用本轮 p 值当确认。
""".strip(),
        """
## 逐笔图册

每张图四笔，K1/K2、SMA40、入场、精确止损与 3R 目标均从同一账本绘制。青/橙 K 线按
`HL2 >= SMA40` 着色，与指标的 MA Shift 蜡烛语义一致。
""".strip(),
    ]

    pages = sorted((RESULTS / "trade_pages").glob("trade_audit_page_*.png"))
    for page_index, page in enumerate(pages, start=1):
        start_trade = (page_index - 1) * 4 + 1
        end_trade = min(page_index * 4, len(events))
        relative = "../" + str(page.relative_to(PROJECT))
        report_parts.extend(
            [
                f"### 图册 {page_index}：第 {start_trade}–{end_trade} 笔",
                f"![逐笔图册{page_index}]({relative})",
            ]
        )

    report_parts.extend(
        [
            "## 产物与哈希",
            _table(["文件", "字节", "SHA256"], artifact_rows),
            f"运行环境：Python {platform.python_version()}，numpy {np.__version__}，pandas {pd.__version__}，scipy {scipy.__version__}。",
            """
## 从零复现

```bash
cd /Users/zhangzc/fable-trading

# 使用已冻结的官方快照；若重新拉取，字节哈希可能因交易所历史修订而变化
PYTHONPATH=. python3 scripts/backtest_two_key_candle_pine_v8_btc_1h.py
PYTHONPATH=. python3 scripts/validate_two_key_candle_pine_v8_btc_1h.py
PYTHONPATH=. python3 -m pytest -q tests/test_backtest_two_key_candle_pine_v8_btc_1h.py tests/contracts/test_registries.py

python3 scripts/build_btcusdtp_1h_pine_v8_report.py
python3 scripts/md_to_html.py \\
  analysis/p1_btcusdtp_1h_pine_v8_sixmonth_backtest_20260904.md \\
  --out-dir analysis/html
```
""".strip(),
        ]
    )

    REPORT.write_text("\n\n".join(report_parts) + "\n", encoding="utf-8")
    print(f"wrote {REPORT.relative_to(PROJECT)} ({REPORT.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
