"""Render completed V1+ backtest tables into the repository's durable report.

This presentation step never recalculates signals or selects parameter values.
Best/worst rows are explicitly retrospective illustrations, not a sampled win
rate. Economic claims remain attached to independent accounts or event units.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

from yoyo.evaluation.spike_v1_plus_report import EXP, BASELINE, PLUS

ROOT=Path(__file__).resolve().parents[2]


def table(frame, columns, percentages=()):
    f=frame.loc[:,list(columns)].copy()
    for name in f:
        if name == "cohort":
            f[name]=f[name].replace({BASELINE:"V1 双向基线",PLUS:"V1+ 默认"})
        elif name == "period":
            f[name]=f[name].replace({"full":"完整两年","development":"前一年","validation":"后一年"})
        elif name == "side_group":
            f[name]=f[name].replace({"long":"多头","short":"空头","both":"多空合计"})
        elif name in percentages:
            f[name]=f[name].map(lambda x:"—" if pd.isna(x) else f"{x:.2%}")
        elif name in {"entry_price", "exit_price"}:
            f[name]=f[name].map(lambda x:"—" if pd.isna(x) else f"{x:.10g}")
        elif pd.api.types.is_float_dtype(f[name]):
            f[name]=f[name].map(lambda x:"—" if pd.isna(x) else f"{x:.3f}")
    f=f.rename(columns=columns)
    def row(values):
        return "| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in values) + " |"
    return "\n".join([row(f.columns), row(["---"] * len(f.columns)),
                      *[row(values) for values in f.itertuples(index=False, name=None)]])


def deliver(post:Path, engine:Path):
    manifest=json.loads((post/"post_manifest.json").read_text())
    if not manifest.get("complete"): raise ValueError("incomplete post")
    for name,sha in manifest["outputs"].items():
        if hashlib.sha256((post/name).read_bytes()).hexdigest()!=sha: raise ValueError(f"hash mismatch {name}")
    read=lambda name:pd.read_csv(post/name)
    events=read("event_summary.csv"); accounts=read("account_summary.csv")
    signals=read("signal_summary.csv"); pairs=read("same_entry_pairs.csv.gz"); trades=read("trades.csv.gz")
    individual=read("independent_accounts.csv"); controls=read("matched_benchmark_summary.csv")
    concentration=read("concentration.csv")
    ids=trades[["stream_key","timeframe_min"]].drop_duplicates()
    pairs=pairs.merge(ids,on="stream_key",validate="many_to_one")
    tail=[]
    for tf,g in pairs.groupby("timeframe_min"):
        base=g.loc[g.baseline_realized_ge10r.eq(True)]
        tail.append(dict(timeframe_min=tf,baseline_ge10r=len(base),
                         same_entry_present=int(base._merge.eq("both").sum()),
                         same_entry_ge10r=int(base.same_entry_retained_ge10r.eq(True).sum()),
                         new_plus_entries=int(g._merge.eq("right_only").sum()),
                         missing_base_entries=int(g._merge.eq("left_only").sum())))
    named=individual.loc[individual.venue.eq("okx")&individual.timeframe_min.eq(60)&individual.period.eq("validation")&
                         individual.asset.isin(["BTC","ETH","SOL","ZEC","PEPE","WIF","TAO","SOPH","USELESS","BICO","DOGE","SUI"])]
    done=trades.loc[trades.cohort.eq(PLUS)&trades.censored.eq(False)]
    examples=pd.concat([done.nlargest(5,"net_r"),done.nsmallest(5,"net_r")]).drop_duplicates("trade_id")
    report=ROOT/"analysis/p1_spike_v1_plus_backtest_20260912.md"
    report.parent.mkdir(exist_ok=True)
    text=f'''# SPIKE V1+ 完整默认配置：两年回测

本轮真实运行了 V1+ 完整默认配置，并与同一源码关闭加强模块的双向基线比较。
**这是固定配置评估，不是参数寻优，也不是专门的 1R 保本实验。**

完成 {manifest["streams"]:,} 个行情流；两臂共 {manifest["simulated_trades"]:,} 条模拟交易记录，
其中包含同一机会的版本对照，不能视为独立市场机会数。完整方向/周期/账户数据在下表和 CSV。

## 配置与边界

- 两年窗口：2024-09-10 至 2026-09-10 UTC；2025-09-10 分段。两年历史都已研究过，不是盲样本。
- Binance/OKX/Gate 已覆盖池，30m/1H/4H；3,531 是交易所×周期×连续行情段，不是3,531个币。
  上游目录三周期5,043格，原已覆盖3,534格，其中SATS三个tick无效格排除。当前存续目录和覆盖不足有选择偏差。
  每个币只使用实际可得历史；新上市币不足两年，不补造历史，账户窗口长度也可能不同。
- `v1_display_both` 是同源关闭 V1+ 总开关的双向基线，不能改称旧仅多头原版。
  `v1_plus_default_both` 为固定默认加强版：开启联合过热过滤、分段保护；其他可选模块关闭。
- 入场信号收盘确认，下一根实际开盘模拟成交。图表参考R与实际入场R分别保留。
  止损已生效后可以盘中触发；先处理开盘跳空、排定退出/入场，再处理盘中止损。收盘新增保护下一根生效。
- 初始账户10,000、目标初始风险1%、入场名义金额上限1倍权益。各行情流独立账户，**平均账户收益不是全币组合收益**。
- 固定0.2%原入场名义金额往返成本；未模拟资金费率、额外滑点、订单容量及强平。
- 边界持仓估值单独记censored，不计已兑现胜率/净R；账户仍包含估值。持仓延续跨年，账户按日历收盘净值分段。

## 已兑现交易：全期及分年

PF使用逐笔等名义净收益；净R合计不是账户百分比。止损K内先后顺序未知，保守地不计该K有利极值。

{table(events.loc[events.side_group.eq("both")],{"period":"期间","timeframe_min":"分钟","cohort":"版本","entries":"入场","realized":"已平仓","censored":"边界估值","win_rate":"净胜率","event_pf":"PF","net_r_sum":"净R和","mean_net_r":"平均净R","realized_ge10r":"兑现≥10R"},["win_rate"])}

## 独立账户与最大回撤

包含已观察零交易账户。回撤按每根收盘盯市计算；不是只用已平仓余额，也不是盘中最大回撤。
表中是同周期独立账户的均值，逐账户明细另存，不能据此推算统一资金池收益。

{table(accounts,{"period":"期间","timeframe_min":"分钟","accounts":"账户","comparable":"可比较","mean_base_return":"基线平均收益","mean_plus_return":"V1+平均收益","mean_base_drawdown":"基线平均回撤","mean_plus_drawdown":"V1+平均回撤","improved_accounts":"收益改善账户","worsened_accounts":"收益下降账户"},["mean_base_return","mean_plus_return","mean_base_drawdown","mean_plus_drawdown"])}

## 多空分别看（后一年）

多空交易是双向系统中的归因，不是独立运行的两个单向账户。

{table(events.loc[events.period.eq("validation")&events.side_group.ne("both")],{"timeframe_min":"分钟","cohort":"版本","side_group":"方向","realized":"已平仓","win_rate":"净胜率","event_pf":"PF","net_r_sum":"净R和","realized_ge10r":"兑现≥10R"},["win_rate"])}

## 信号数量与大趋势保留

确认信号与实际入场分别统计：无效风险、开盘越过SL或期末未成交，不能算实际开单。

{table(signals.loc[signals.period.eq("full")],{"timeframe_min":"分钟","cohort":"版本","raw_confirmations":"原始确认","visible_signals":"可见信号","accepted_references":"有效参考","overheat_rejected":"过热拒绝"})}

下表用同一行情流、同根信号、同方向精确配对；“同点位存在”不等于仍兑现10R。

{table(pd.DataFrame(tail),{"timeframe_min":"分钟","baseline_ge10r":"基线兑现≥10R","same_entry_present":"加强版保留同次入场","same_entry_ge10r":"加强版仍兑现≥10R","new_plus_entries":"加强版新增入场","missing_base_entries":"原有入场消失"})}

## 点名币种：OKX 1H 后一年

{table(named,{"asset":"币","cohort":"版本","net_return":"账户收益","max_close_drawdown":"收盘最大回撤","valid":"有效"},["net_return","max_close_drawdown"])}

## 利润是否依赖少数大单

以下仅为事后敏感性检查，删除最大盈利行不会重新回放账户。按净R衡量，跨交易所重复行情仍可能重复计数。
币种集中度使用盈利净R之和，PEPE与1000PEPE在该项合并；这不是跨币风险额度建议。

{table(concentration.loc[concentration.period.eq("validation")],{"timeframe_min":"分钟","cohort":"版本","total_net_r":"净R合计","without_top1_net_r":"去最大1笔","without_top5_net_r":"去最大5笔","without_top10_net_r":"去最大10笔","top_asset":"盈利最多币","top_asset_positive_r_share":"占盈利R比例"},["top_asset_positive_r_share"])}

## 匹配随机入场：单独的零假设检查

按固定hash最多选每流/方向/年份/版本2个真实入场，包含边界样本，不按最终利润抽样。
匹配同流、同月、同方向、因果波动分桶的随机时点。实际/随机时点都用相同的纯保护退出，
都不含原始反向参考退出。因此**这一表独立于上面的完整策略收益**，不是账户随机组合。
未解决边界和无效风险匹配明确保留在明细，p值按月份块翻转，仅作探索；不能把跨所重复当独立试验。

{table(controls,{"period":"期间","timeframe_min":"分钟","cohort":"版本","targets":"抽样目标","matched":"成功匹配","month_blocks":"月份块","mean_target_net_r":"目标平均净R","mean_control_net_r":"随机平均净R","equal_month_difference":"等月净R差","exploratory_sign_flip_p":"探索性p"})}

## 成功与失败的逐笔入口

以下是加强版事后最好5笔及最差5笔，用于核查路径，不能估计胜率；跨所可能是同一次行情。

{table(examples,{"venue":"交易所","symbol":"合约","timeframe_min":"分钟","side":"方向","signal_bar_open":"信号K开盘UTC","entry_price":"模拟入场","exit_price":"模拟退出","net_r":"兑现净R","mfe_r":"观察峰值R","exit_reason":"退出原因"})}

## 验证、风险与诚实声明

- 最初技术冒烟发现撮合顺序问题；首个正式尝试80流被停止保留，未用其表现选参数。
  随后的366流尝试因执行器性能优化重新启动；5流×两臂新旧执行器逐列一致，规则未改。
  修复后的正式目录为 `{engine.relative_to(ROOT)}`，不得混入旧尝试。代码/数据/输出SHA绑定。
- Python翻译及合成因果测试不能替代TradingView原生逐bar parity。本轮不宣称完成原生收益一致性验证。
- 不以净R总和代替账户净值；不把事后最高浮盈当可全部兑现；不保证这些历史效果适用于未来。
- 两臂同时改变的是Owner指定的完整加强配置，不能据此归因某个单独模块，也没有确认最优参数。
- 没有训练分类模型，AUC、top-decile预测排序指标不适用。对应的同条件入场零假设另列。
- 无线上规则、Pine、Bark、仓位、订单或模型版本改动，生产与训练资格均为false。

## 明细与复现

- [全部模拟逐笔]({post / "trades.csv.gz"})
- [全部成交与拒绝记录]({post / "fills.csv.gz"})
- [逐币逐周期事件统计]({post / "coin_event_summary.csv"})
- [全部独立账户]({post / "independent_accounts.csv"})
- [同入场逐笔对照]({post / "same_entry_pairs.csv.gz"})
- [完整实验计划]({EXP / "PROJECT_PLAN.md"})
- [运行收据]({engine / "manifest.json"})

```bash
.venv/bin/python -m pytest -q tests/evaluation/test_spike_v1_plus_replay.py tests/evaluation/test_spike_v1_plus_report.py
.venv/bin/python -m yoyo.evaluation.spike_v1_plus_study {engine.relative_to(ROOT)}
.venv/bin/python -m yoyo.evaluation.spike_v1_plus_report {engine.relative_to(ROOT)} {post.relative_to(ROOT)}
.venv/bin/python -m yoyo.evaluation.spike_v1_plus_delivery {post.relative_to(ROOT)} {engine.relative_to(ROOT)}
.venv/bin/python scripts/md_to_html.py analysis/p1_spike_v1_plus_backtest_20260912.md --out-dir analysis/html
```
'''
    report.write_text(text,encoding="utf-8")
    print(report)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("post",type=Path);p.add_argument("engine",type=Path)
    a=p.parse_args();deliver(a.post.resolve(),a.engine.resolve())
