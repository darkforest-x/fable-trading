"""Render the frozen RSI-zone comparison; reads the ledger, never the OHLCV."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.bb_stoch_rsi_study import EXP
from yoyo.evaluation.eth_bb_stoch_study import ROOT

ARM_NAMES = {"unfiltered": "v2 原版（无 RSI 过滤）", "rsi_entry": "v3 主口径（RSI 只过滤入场）",
             "rsi_both": "敏感性（RSI 同时过滤反向退出）"}
EXIT_NAMES = {"initial_stop": "初始止损", "initial_stop_gap": "跳空初始止损",
              "break_even": "平半后保本", "break_even_gap": "跳空/同开盘保本退出",
              "break_even_marketable": "上移保护价已被越过", "take_profit": "触轨平半",
              "opposite_signal_close": "完整反向信号退出", "boundary_censor": "末端未平仓"}


def number(value, digits=2, signed=False):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "N/A"
    return format(value, f"{'+' if signed else ''}.{digits}f")


def table(headers, rows):
    return ("| " + " | ".join(headers) + " |\n| " + " | ".join(["---"] * len(headers)) + " |\n"
            + "\n".join("| " + " | ".join(map(str, row)) + " |" for row in rows))


def performance_row(name, group):
    s, c = group["stats"], group["control"]
    return [name, s["natural"], number(s["gross_r"], signed=True), number(s["net_r"], signed=True),
            number(100 * s["win_rate"]) + "%" if s["win_rate"] is not None else "N/A",
            number(s["profit_factor"]), number(s["max_realized_drawdown_r"]),
            c["paired_n"], number(c["random_mean_net_r"], 3, True),
            number(c["excess_mean_net_r"], 3, True), number(c["p"], 4)]


def main() -> None:
    result = json.loads((EXP / "results.json").read_text())
    cfg, source, counts = result["config"], result["data"], result["counts"]
    primary, base = cfg["primary_arm"], "unfiltered"
    path = cfg["primary_path"]
    filtered = result["arms"][primary][path]["all"]
    original = result["arms"][base][path]["all"]
    fs, os_ = filtered["stats"], original["stats"]
    fc = filtered["control"]
    events = result["event_level"]
    rows = json.loads((EXP / f"{primary}_{path}_trades.json").read_text())
    closed = [r for r in rows if not r["censored"]]
    header = ["组", "完整交易", "毛 R", "净 R", "净胜率", "PF（R）", "已实现回撤 R",
              "配对数", "随机均值 R/笔", "配对超额 R/笔", "月块 p"]
    arm_table = table(header, [performance_row(ARM_NAMES[arm] + "·TV路径", result["arms"][arm][path]["all"])
                               for arm in cfg["arms"]]
                      + [performance_row(ARM_NAMES[arm] + "·先走逆向", result["arms"][arm]["adverse_first"]["all"])
                         for arm in cfg["arms"]])
    partitions = table(header, [performance_row(label, result["arms"][primary][path][key]) for key, label in
                                [("first_half", "时间前半"), ("second_half", "时间后半"),
                                 ("long", "多头"), ("short", "空头")]]
                       + [performance_row(key, result["arms"][primary][path][key])
                          for key in result["arms"][primary][path] if key[:4].isdigit()])
    gate_table = table(["候选", "可用信号", "RSI 放行", "放行率", "信号根 RSI 中位数"],
                       [["全部", counts["admissible"], counts["gate_passed"],
                         number(100 * counts["gate_passed"] / counts["admissible"]) + "%", "—"]]
                       + [[label, counts[key]["admissible"], counts[key]["gate_passed"],
                           number(100 * counts[key]["gate_passed"] / counts[key]["admissible"]) + "%"
                           if counts[key]["admissible"] else "N/A",
                           number(counts[key]["median_rsi"])]
                          for key, label in [("long", "做多候选（需 RSI<30）"), ("short", "做空候选（需 RSI>70）")]])
    event_table = table(["事件级独立回放", "笔数", "净 R 合计", "净 R/笔", "净胜率"],
                        [["RSI 放行", events["passed_n"], number(events["passed_net_r"], signed=True),
                          number(events["passed_mean_net_r"], 4, True),
                          number(100 * events["passed_win_rate"]) + "%" if events["passed_win_rate"] is not None else "N/A"],
                         ["RSI 拦截", events["blocked_n"], number(events["blocked_net_r"], signed=True),
                          number(events["blocked_mean_net_r"], 4, True),
                          number(100 * events["blocked_win_rate"]) + "%" if events["blocked_win_rate"] is not None else "N/A"]])
    reasons = Counter(r["exit_reason"] for r in closed)
    exit_table = table(["最终退出原因", "笔数", "其中已平半"],
                       [[EXIT_NAMES.get(reason, reason), n,
                         sum(r["partial"] for r in closed if r["exit_reason"] == reason)]
                        for reason, n in reasons.items()])
    delta_r = fs["net_r"] - os_["net_r"]
    better = "更少亏" if delta_r > 0 and fs["net_r"] < 0 else "更赚" if delta_r > 0 else "更差"
    verdict = ("过滤后仍为净亏损" if fs["net_r"] < 0 else "过滤后转为净盈利")
    evidence = ("没有证明优于匹配随机入场" if fc["p"] is None or fc["p"] >= .01
                or (fc["excess_mean_net_r"] or 0) <= 0
                else "在本段月块对照检验中出现正超额，但仍不是独立样本外验证")
    label_evidence = ("放行组与拦截组的差异在标签置换零假设下不显著" if events["p"] is None or events["p"] >= .05
                      else "放行组在标签置换零假设下优于拦截组（p<0.05），但这是探索性历史区间的单次检验")
    receipt_path = EXP / "tv_save_receipt.json"
    tv_line = (json.loads(receipt_path.read_text())["report_line"] if receipt_path.exists() else
               "**Pine v3 尚未保存到 TradingView**：本机没有已登录的 TradingView 浏览器会话（Chrome 扩展未连接，"
               "内置浏览器无 cookie），保存需要 Owner 自己登录或连上扩展。源码已在仓库，可直接粘贴到 Pine 编辑器。")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10.5, 3.9), layout="constrained")
    for arm, color in zip(cfg["arms"], ["#6b6f76", "#007f73", "#b25730"]):
        arm_rows = json.loads((EXP / f"{arm}_{path}_trades.json").read_text())
        natural = [r for r in arm_rows if not r["censored"]]
        times = [pd.Timestamp(source["first_open"])] + [pd.Timestamp(r["exit_time"]) + pd.Timedelta(minutes=5)
                                                        for r in natural]
        ax.step(times, np.r_[0., np.cumsum([r["net_r"] for r in natural])], where="post",
                label=f"{arm} (n={len(natural)})", color=color, lw=1.6, alpha=.88)
    ax.axhline(0, color="#9c9c9c", lw=.8)
    ax.set(title="ETH BB x Stoch v3 | realized net R by arm (TV OHLC path)", ylabel="Cumulative net R",
           xlabel="UTC exit bar close (intrabar exits positioned at bar end)")
    ax.grid(alpha=.16)
    ax.legend(frameon=False, fontsize=9)
    fig.savefig(ROOT / "analysis/html/eth_bb_stoch_rsi_filter_20260916_equity.png", dpi=170)
    plt.close(fig)

    validation = json.loads((EXP / "validation.json").read_text()) if (EXP / "validation.json").exists() else {}
    text = f"""# ETH 5m · BB × Stoch 加 Parabolic RSI 区域过滤

## 结论

**加上「只在超卖做多、超买做空」的 RSI 门后，同一段历史里{verdict}：{fs['natural']}笔完整交易，
净{number(fs['net_r'], signed=True)}R，胜率{number(100 * fs['win_rate'])}%，PF {number(fs['profit_factor'])}；
未过滤的 v2 是{os_['natural']}笔、净{number(os_['net_r'], signed=True)}R、PF {number(os_['profit_factor'])}。**
总亏损少了 {number(abs(delta_r))}R，方向上{better}。

**但少亏主要来自少做单，不是每笔变好：**每笔净收益 {number(os_['mean_net_r'], 4, True)}R →
{number(fs['mean_net_r'], 4, True)}R，几乎没动（{number(fs['mean_net_r'] - os_['mean_net_r'], 4, True)}R/笔），
入场数 {os_['natural']} → {fs['natural']} 笔。同时最大连续亏损从 {os_['max_loss_streak']} 笔变成
{fs['max_loss_streak']} 笔，**变差了**。{evidence}。本轮没有调任何参数，V1 门禁未加入。

{tv_line}

## 这次到底改了什么

| 组 | 入场 | 反向/退出 |
|---|---|---|
| `unfiltered` | 原 v2 组合信号 | 原 v2 信号 |
| `rsi_entry`（主，= 保存的 Pine v3） | 组合信号 ∩ RSI 区域 | **原 v2 信号，未过滤** |
| `rsi_both`（事前冻结的敏感性） | 组合信号 ∩ RSI 区域 | 同样被过滤 |

RSI 取 ChartPrime 源码里的 `ta.rsi(close, {cfg['rsi_length']})` 曲线，和策略同图 5m；
严格 `<{number(cfg['lower'], 0)}` 才允许做多、严格 `>{number(cfg['upper'], 0)}` 才允许做空，
等于阈值不放行，在组合信号那根 5m 收盘同根判定。**不使用该指标的 SAR 翻转/强信号菱形**，
那是另一个事件。阈值与周期是公开源码默认值；本机没有可用的 TradingView 会话，
**没有核实 Owner 图上保存的实时参数**，若不是 14/30/70 则本报告数字需按其参数重跑。

主口径刻意只过滤入场：如果连反向退出也要等 RSI 极值，过滤就会把亏损单留在场内，
这在 `rsi_both` 那一行看得见，它是敏感性披露，不是可选成绩。

## 过滤掉了什么

{gate_table}

`unfiltered` 与上一轮冻结账本逐笔一致（{json.dumps(result.get('baseline_parity', {}), ensure_ascii=False)}），
这是回归校验不是新评分。BB 带外 + Stoch 极区的信号本身已经是"极端位置"，
再叠加 RSI 极值后，同向确认的样本数量大幅下降，这是本轮样本量变小的直接原因。

## 收益、回撤与匹配随机对照

{arm_table}

PF = 盈利交易净 R 之和 / 亏损交易净 R 绝对值之和；净胜率扣手续费后计算；回撤是完整交易结算后的
累计净 R 回撤，**不含持仓浮动回撤**。两条路径只是预先冻结的成交顺序敏感性，不得挑较好者当成绩。

**这段样本里两条路径结果逐笔相同**（上一轮 v2 冻结账本也是如此）：主口径只有
{fs['ambiguous_bar_count']} 根持仓 K 线同时够到止盈与保护价，而这几根里先后顺序没有改变结果。
所以这条敏感性在本样本上没有分辨力，不能当作"路径无关"已被证明。

![按组的累计已实现净 R](eth_bb_stoch_rsi_filter_20260916_equity.png)

## 事件级：过滤到底有没有选出更好的信号

序贯账本里删掉一些入场会改变后续机会，净差不能全部算在"被删掉的那些单"头上。
因此对每个可用原始信号独立回放一笔 1 ETH（忽略占仓），按 RSI 放行/拦截分组：

{event_table}

放行组减拦截组的净 R/笔差为 {number(events.get('mean_difference'), 4, True)}，
在事件集合内做 {events.get('permutations', 0)} 次种子化标签置换，单侧 p = {number(events.get('p'), 4)}。
{label_evidence}。这只检验"RSI 区域标签是否含信息"，不是收益证明；事件可重叠，
不构成可执行的单仓账户。共 {events['events']} 个事件，其中 {events['censored']} 笔在末端未平仓已排除。

## 主口径的分段与退出构成

{partitions}

时间切点固定为数据起点到研究终点的中点：{result['midpoint']}。前后半、月度只是同一固定规则的
稳定性诊断，不是独立重复试验，也没有用来选参数。

{exit_table}

完整交易中 {sum(r['partial'] for r in closed)} 笔触轨平半；最大连续净亏损 {fs['max_loss_streak']} 笔，
平均盈利 {number(fs['avg_win_r'], signed=True)}R、平均亏损 {number(fs['avg_loss_r'], signed=True)}R，
平均持仓约 {number(fs['mean_hold_hours'])} 小时（按覆盖 K 线根数估计）。
固定 1 ETH 归一化下，主口径毛盈亏 {number(fs['gross_pnl'], signed=True)} USDT、
手续费 {number(fs['fees'])} USDT、净 {number(fs['net_pnl'], signed=True)} USDT；
这是仓位归一化数字，不是账户收益率。

## 匹配随机对照口径

每笔实际入场提前抽 5 个同 ETH、同方向、同 UTC 月、同因果波动桶（TR-SMA14/close 相对前 120 个值的
三分位）的随机入场，先冻结抽样再读结果，未平仓也不重抽；随机入场使用同组的平半、保本、反向、
3% 止损与费用规则。主口径共抽 {fc['controls_drawn']} 个对照，其中 {fc['controls_censored']} 个末端未完成，
完整配对 {fc['paired_n']} 笔、未配对 {fc['unpaired_n']} 笔。按 {fc['months']} 个 UTC 月整体翻转差值符号，
单侧 p = {number(fc['p'], 4)}。月份数少，显著性分辨率有限。

## 风险与诚实声明

- 这是对冻结 Pine 规则的 Python 离线回放，**不声称与 TradingView 策略测试器逐笔成交一致**。
  5m OHLC 无法还原真实逐笔先后；主路径用 TradingView 文档的近端极值优先约定，开盘距高低相等时固定先低。
- 样本量：过滤后完整交易仅 {fs['natural']} 笔。**笔数少于 30 时只描述，不宣称稳健**；
  本区间此前已被 v2 回测使用，属探索性历史复用，不是独立样本外验证。
- 本配置 **holdout 消耗 0 次**，读价止于 {cfg['end']}，早于 05-04 holdout 起点。
  数据缺口/重复均为 0，不补 K 线、不换源；受限价格解析 {source['restricted_price_rows_parsed']} 行。
- 主口径 {fs['entries_open_beyond_target']} 笔入场开盘已越过止盈价、{fs['same_open_be_approximations']} 笔同开盘
  处理已触发的保本、{fs['marketable_be_approximations']} 笔盘中平半时新保护价已被越过，均按观察价近似并保留标记。
- 无排序模型，val AUC、top-decile 排序收益与单特征基线不适用，不编造；替代零假设是匹配随机入场
  与事件级标签置换。未完成仓位 {fs['censored']} 笔按末端盯市，不计入完整交易收益。
- 费用只含每边 0.1%，不含资金费率、额外滑点与冲击成本；固定 1 ETH 现金换算不等于交易所合约乘数。
- 本轮不训练、不 promote、不改仓、不动真金，也不产生任何实盘资格。

## 校验与复现

源码冻结提交：`{result['source_commit']}`。Pine v3 SHA256：`{cfg['pine_sha256']}`；
父版 v2：`{cfg['parent_pine_sha256']}`；ChartPrime 源：`{cfg['chartprime_sha256']}`。
只读前缀 SHA256：`{source['prefix_sha256']}`。程序在任何价格读取前核对 HEAD 的 builder、配置、
计划与两个 Pine 字节一致，否则拒绝运行。验证记录：`{json.dumps(validation, ensure_ascii=False)}`。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/evaluation/test_bb_stoch_rsi_study.py tests/evaluation/test_bb_stoch_replay.py \\
  tests/evaluation/test_eth_bb_stoch_study.py tests/test_ma_stoch_rsi_filter.py -q
.venv/bin/python -m yoyo.evaluation.bb_stoch_rsi_study
.venv/bin/python -m yoyo.evaluation.bb_stoch_rsi_report
python3 scripts/md_to_html.py analysis/p1_eth_bb_stoch_rsi_filter_20260916.md --out-dir analysis/html
```

逐笔、对照抽签、事件级账本与摘要保存在 `{EXP.relative_to(ROOT)}/`；数据未入 git。

## 下一步（需 Owner 决定）

1. 核对 Owner 图上 Parabolic RSI 的实际参数；不是 14/30/70 就按其参数重跑，本轮数字作废。
2. 若要改阈值（例如 35/65）或改成"最近 N 根内曾经超卖"，那是新变量，需另行冻结，不在本轮自动尝试。
3. 是否要用该指标的 **SAR 翻转/强信号菱形**作为另一种过滤——与本轮的区域口径不是一回事。
4. 任何 holdout 评估仍需 Owner 明确批准并记账。
"""
    report = ROOT / "analysis/p1_eth_bb_stoch_rsi_filter_20260916.md"
    report.write_text(text)
    subprocess.run(["python3", "scripts/md_to_html.py", str(report), "--out-dir", "analysis/html"],
                   cwd=ROOT, check=True)
    print(ROOT / "analysis/html" / report.with_suffix(".html").name, flush=True)


if __name__ == "__main__":
    main()
