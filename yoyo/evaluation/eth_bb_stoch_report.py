"""Render the frozen BB/Stoch replay ledger; never reads source OHLCV."""
from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.eth_bb_stoch_study import EXP, ROOT, compare, save, sha


def number(value, digits=2, signed=False):
    if value is None: return "N/A"
    return format(value, f"{'+' if signed else ''}.{digits}f")


def table(headers, rows):
    return "| " + " | ".join(headers) + " |\n| " + " | ".join(["---"]*len(headers)) + " |\n" + "\n".join("| " + " | ".join(map(str, row)) + " |" for row in rows)


def performance_row(name, group):
    s, c = group["stats"], group["control"]
    return [name, s["natural"], number(s["gross_r"], signed=True), number(s["net_r"], signed=True),
            number(100*s["win_rate"]) + "%" if s["win_rate"] is not None else "N/A",
            number(s["profit_factor"]), number(s["max_realized_drawdown_r"]),
            c["paired_n"], number(c["random_mean_net_r"], 3, True), number(c["excess_mean_net_r"], 3, True)]


def main():
    result = json.loads((EXP / "results.json").read_text())
    cfg, source = result["config"], result["data"]
    primary, sensitive = result["paths"]["tv"], result["paths"]["adverse_first"]
    s, c = primary["all"]["stats"], primary["all"]["control"]
    rows = json.loads((EXP / "tv_trades.json").read_text())
    controls = json.loads((EXP / "tv_controls.json").read_text())
    closed = [r for r in rows if not r["censored"]]
    partial = [r for r in closed if r["partial"]]
    be = [r for r in closed if r["exit_reason"] in ("break_even", "break_even_gap")]
    cash_wins = sum(r["net_pnl"] for r in closed if r["net_pnl"] > 0)
    cash_losses = -sum(r["net_pnl"] for r in closed if r["net_pnl"] < 0)
    cash_pf = cash_wins / cash_losses if cash_losses else None
    header = ["样本", "完整交易", "毛 R", "净 R", "净胜率", "PF（R）", "已实现回撤 R", "配对数", "随机均值 R/笔", "配对超额 R/笔"]
    main_table = table(header, [performance_row("v2 · TV OHLC 路径", primary["all"]),
                                performance_row("v2 · 先走逆向路径", sensitive["all"]),
                                ["v1 · 指标稿（未回测）"]+["N/A"]*9])
    partitions = table(header, [performance_row(label, primary[key]) for key, label in
        [("first_half", "时间前半"), ("second_half", "时间后半"), ("long", "多头"), ("short", "空头")] ] +
        [performance_row(key, primary[key]) for key in primary if key[:4].isdigit()])
    reasons = Counter(r["exit_reason"] for r in closed)
    names = {"initial_stop": "初始止损", "initial_stop_gap": "跳空初始止损", "break_even": "平半后保本", "break_even_gap": "跳空/同开盘保本退出", "break_even_marketable": "上移保护价已被越过", "opposite_signal_close": "完整反向信号退出"}
    exit_table = table(["最终退出原因", "笔数", "其中已平半"],
        [[names.get(reason, reason), n, sum(r["partial"] for r in closed if r["exit_reason"] == reason)] for reason, n in reasons.items()])
    direction = "亏损" if s["net_r"] < 0 else "盈利"
    evidence = "没有证明优于匹配随机入场" if c["p"] is None or c["p"] >= .01 or c["excess_mean_net_r"] <= 0 else "在这段样本的月度对照检验中出现正超额，但仍不是独立样本外验证"
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10.5, 3.9), layout="constrained")
    for mode, label, color in [("tv", "TV OHLC path", "#007f73"), ("adverse_first", "Adverse excursion first", "#b25730")]:
        path_rows = json.loads((EXP / f"{mode}_trades.json").read_text())
        natural = [r for r in path_rows if not r["censored"]]
        times = [pd.Timestamp(source["first_open"])] + [pd.Timestamp(r["exit_time"])+pd.Timedelta(minutes=5) for r in natural]
        equity = np.r_[0., np.cumsum([r["net_r"] for r in natural])]
        ax.step(times, equity, where="post", label=label, color=color, lw=1.6, alpha=.88)
    ax.axhline(0, color="#9c9c9c", lw=.8)
    ax.set(title="ETH BB x Stoch v2 | realized net R", ylabel="Cumulative net R", xlabel="UTC exit bar close (intrabar exits positioned at bar end)")
    ax.grid(alpha=.16); ax.legend(frameon=False, fontsize=9)
    chart = ROOT / "analysis/html/eth_bb_stoch_backtest_20260916_equity.png"
    fig.savefig(chart, dpi=170); plt.close(fig)
    tests_path = EXP / "validation.json"
    validation = json.loads(tests_path.read_text()) if tests_path.exists() else {"status": "See validation artifacts"}
    text = f"""# ETH 5m · BB × Stoch v2 回测

## 结论

**这版在本地可用历史上净{direction}：{s['natural']}笔完整交易，净{number(s['net_r'], signed=True)}R，胜率{number(100*s['win_rate'])}%，按R计算PF {number(s['profit_factor'])}。** {evidence}。这次测试没有调整任何策略参数，V1门禁未加入。

按每笔完整仓位固定1 ETH归一化，毛盈亏{number(s['gross_pnl'], signed=True)} USDT，实际成交手续费{number(s['fees'])} USDT，净盈亏{number(s['net_pnl'], signed=True)} USDT，现金PF {number(cash_pf)}。这是仓位归一化数字，不能当作账户收益百分比。

## 数据与执行口径

- 来源：OKX ETH-USDT-SWAP原生5分钟普通K线，本地既有CSV。共{source['rows']}根，UTC开盘起点{source['first_open']}，最后完整收盘{source['last_close']}。北京时间比UTC晚8小时。
- BB预热后最早可用信号收盘：{result['valid_signal_close_start']}。原始组合信号{result['raw_signals']}个，可用信号{result['valid_signals']}个；持仓期间同方向信号被忽略，实际入场{s['total_entries']}次，末端未完整退出{s['censored']}笔。
- BB200/2总体标准差；原Stoch5/3/3严格20/80箭头。整根含影线严格位于带外才入场，踩线不算；信号收盘确认、下一根开盘成交，初始止损3%。
- 多头上轨、空头下轨触碰即平50%，实际成交后尾仓止损移至开仓价；完整反向组合收盘平尾仓，允许下一根反手。动态BB触价由前199根收盘预先求解，每根更新，避免用收盘后的BB倒填该根影线。
- 默认手续费每边0.1%，按实际成交数量扣费；没有额外滑点、资金费率。固定1 ETH现金换算不等于TradingView交易所合约乘数下的1合约。
- **图上R不加权**，仍表示尾仓价格距离/初始3%风险。**经济回测R按实际50%成交份额计算**，否则会把半仓的收益算成整仓。本报告所有收益R均为经济R，逐笔CSV另有tail_price_r保留图上口径。

## 收益、回撤与对照

{main_table}

PF=盈利交易净R之和/亏损交易净R绝对值之和。净胜率在扣手续费后计算。回撤是完整交易结算后的累计净R回撤，**不含持仓中的浮动回撤**；固定1 ETH对应的最大已实现现金回撤为{number(s['max_realized_drawdown_usdt'])} USDT。

![累计已实现净R](eth_bb_stoch_backtest_20260916_equity.png)

前版v1只有指标实现，没有已冻结的经济结果；此处保留N/A，未补跑不同参数制造版本对比。两条路径仅是预先冻结的成交顺序敏感性检查，不能挑较好者作为正式成绩。

## 分段与方向

{partitions}

时间切点固定为数据起点至研究终点的中点：{result['midpoint']}。交易按信号确认时刻归组，跨月/跨切点持仓持续运行，不在分界强平。前后半只是同一固定规则的时间稳定性诊断，没有训练集或调参选择；月度表也不是独立重复试验。

## 平半、保本与反向退出

{exit_table}

完整交易中{len(partial)}笔触轨平半，占{number(100*len(partial)/len(closed))}%；{len(be)}笔尾仓在保本价退出。另外{s['marketable_be_approximations']}笔触轨时已经亏损，移到入场价的保护单已经被当前价格越过，不能算成以入场价保本离场。保本只指尾仓价格回到开仓价，整笔是否盈利还取决于前半仓利润与手续费。最大连续净亏损{s['max_loss_streak']}笔，平均盈利交易{number(s['avg_win_r'], signed=True)}R，平均亏损交易{number(s['avg_loss_r'], signed=True)}R，平均持仓约{number(s['mean_hold_hours'])}小时（按覆盖K线根数估计）。

## 匹配随机检验

每笔实际入场提前抽取5个同ETH、同方向、同UTC月、同因果波动桶的随机入场。波动桶使用14根平均真实波幅/收盘价，相对之前120个值的三分位；不使用后续走势挑样本。随机入场使用同一平半、保本、反向信号、3%止损和费用规则。

共抽取{c['controls_drawn']}个对照，其中{c['controls_censored']}个边界未完成，**没有补抽**。完整配对{c['paired_n']}笔，未完整配对{c['unpaired_n']}笔。配对实单均值{number(c['paired_actual_mean_net_r'], 4, True)}R/笔，对照{number(c['random_mean_net_r'], 4, True)}R/笔，超额{number(c['excess_mean_net_r'], 4, True)}R/笔。按{c['months']}个UTC月整体翻转差值符号，单侧p={number(c['p'], 4)}。该检验依赖月块差值的符号对称性；月份少，显著性分辨率有限。

全部预抽对照保留末端盯市的辅助超额为{number(c['all_draw_boundary_mark_excess'], 4, True)}R/笔；这是未完成仓位按最后收盘估值且只扣已发生费用的诊断值，不是假装已经清仓。对照事件可重叠，不构成可直接执行的单仓资金曲线。表中随机均值与超额仅在完整配对子集上计算，因此可能不等于全表净R/笔减去随机均值。

## 风险与诚实声明

- 这是对冻结Pine规则的Python离线回放，**没有声称与TradingView策略测试器逐笔成交完全一致**。原生编译/合成预览已在上轮验证；这轮实际行情收益来自本地回放账本。
- 主路径用TradingView文档描述的近端极值优先OHLC路径；开盘距高低相等时固定先低。对照路径多单先低、空单先高。5分钟OHLC不能恢复真实逐笔先后；本轮未用1分钟或Bar Magnifier。主路径记录{s['ambiguous_bar_count']}根可能牵涉止盈与保护价先后的持仓K线（不是交易数）。[TradingView策略文档](https://www.tradingview.com/pine-script-docs/concepts/strategies/)
- 主策略{s['entries_open_beyond_target']}笔入场开盘已越过止盈价；{s['same_open_be_approximations']}笔同开盘处理已触发的保本保护，{s['marketable_be_approximations']}笔盘中平半时新保护价已被越过。此时按触发事件观察价立即处理作近似，无法由5分钟数据证明原生订单的下一tick成交价。随机对照中入场已越目标价{sum(r['entry_open_beyond_target'] for r in controls)}笔，此类情况同样保留，因此超额也受该近似影响。动态对侧BB可能移到亏损侧，脚本没有“只有盈利才平半”的额外条件；保本保护也不保证此时能回到入场价成交。
- 未完成仓位{s['censored']}笔，其末端盯市净R为{number(s['boundary_mark_net_r'], signed=True)}R，不计入完整交易收益。数据缺口/重复均为0；不补K线、不换源。受限价格解析{source['restricted_price_rows_parsed']}行，**本配置holdout消耗0次**。
- 本次不涉及模型训练或连续预测分数，val AUC、top-decile排序收益与单特征排序基线不适用；不能编造。替代零假设为同环境随机入场的费用后收益对照。历史数据可能已用于其他研究，不能宣传为未接触的独立样本外测试。

## 校验与复现

源码冻结提交：`{result['source_commit']}`。Pine SHA256：`{cfg['pine_sha256']}`。只读前缀SHA256：`{source['prefix_sha256']}`。研究程序在任何价格读取前核对HEAD源码、配置、计划和Pine字节一致，receipt保存全部文件哈希。验证记录：`{json.dumps(validation, ensure_ascii=False)}`。

```bash
cd /Users/zhangzc/fable-trading
python3 -m pytest tests/evaluation/test_bb_stoch_replay.py tests/evaluation/test_eth_bb_stoch_study.py tests/test_spike_fanshen_exit.py -q
python3 -m yoyo.evaluation.eth_bb_stoch_study
python3 -m yoyo.evaluation.eth_bb_stoch_report
python3 scripts/md_to_html.py analysis/p1_eth_bb_stoch_backtest_20260916.md --out-dir analysis/html
```

需保留原路径的本地CSV；数据未入git。当前源码必须与HEAD中冻结文件一致，修改后会拒绝读取价格。正式逐笔与全部随机对照保存于实验目录JSON/CSV。

## 下一步

先看本次收益和退出构成，决定是否继续研究。V1门禁、其他止损或BB/Stoch参数属于新版本，需Owner指定后单独冻结；本轮未自动尝试。若要与TradingView逐笔核对或扩展历史范围，应作为下一步独立验收；任何holdout使用仍须明确授权。
"""
    report = ROOT / "analysis/p1_eth_bb_stoch_backtest_20260916.md"
    report.write_text(text)
    subprocess.run(["python3", "scripts/md_to_html.py", str(report), "--out-dir", "analysis/html"], cwd=ROOT, check=True)
    print(ROOT / "analysis/html" / report.with_suffix(".html").name, flush=True)


if __name__ == "__main__":
    main()
