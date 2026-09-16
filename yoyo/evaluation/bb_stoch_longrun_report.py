"""Render the frozen 28-month diagnosis; reads the ledger, never the OHLCV."""
from __future__ import annotations

import json
import subprocess
from collections import Counter

import numpy as np
import pandas as pd

from yoyo.evaluation.bb_stoch_longrun_study import EXP
from yoyo.evaluation.eth_bb_stoch_study import ROOT

ARM_NAMES = {
    "v2_baseline": "v2 原版（保本价=入场价）",
    "be_cost": "参照：保本含 0.2% 成本",
    "be_cost_rsi_line": "＋RSI 线区域门",
    "be_cost_sar_same_bar": "＋◈ 同根",
    "be_cost_sar_w20": "＋◈ 最近 20 根内",
    "be_cost_sar_direction": "＋SAR 方向一致",
    "be_cost_no_partial": "＋取消平半（单仓跑到底）",
}
GATE_NAMES = {"rsi_line": "RSI 线区域", "sar_strong_same_bar": "◈ 同根",
              "sar_strong_window": "◈ 最近 20 根内", "sar_direction": "SAR 方向一致"}
EXIT_NAMES = {"initial_stop": "初始止损", "initial_stop_gap": "跳空初始止损",
              "break_even": "平半后保本", "break_even_gap": "跳空/同开盘保本退出",
              "break_even_marketable": "平半时新保护价已被越过", "take_profit": "触轨平半",
              "opposite_signal_close": "完整反向信号退出", "boundary_censor": "末端未平仓"}


def number(value, digits=2, signed=False):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "N/A"
    return format(value, f"{'+' if signed else ''}.{digits}f")


def table(headers, rows):
    return ("| " + " | ".join(headers) + " |\n| " + " | ".join(["---"] * len(headers)) + " |\n"
            + "\n".join("| " + " | ".join(map(str, row)) + " |" for row in rows))


def row_for(name, group):
    s, c = group["stats"], group["control"]
    return [name, s["natural"], number(s["net_r"], signed=True),
            number(s["mean_net_r"], 4, True) if s["mean_net_r"] is not None else "N/A",
            number(100 * s["win_rate"]) + "%" if s["win_rate"] is not None else "N/A",
            number(s["profit_factor"]), number(s["max_realized_drawdown_r"]), s["max_loss_streak"],
            c["paired_n"], number(c["excess_mean_net_r"], 4, True), number(c["p"], 3)]


def main() -> None:
    result = json.loads((EXP / "results.json").read_text())
    cfg, source, counts = result["config"], result["data"], result["counts"]
    path, ref = cfg["primary_path"], cfg["reference_arm"]
    arms = {a: result["arms"][a][path]["all"] for a in cfg["arms"]}
    base, reference = arms["v2_baseline"]["stats"], arms[ref]["stats"]
    events = result["event_level"]
    header = ["组", "完整交易", "净 R", "每笔净 R", "净胜率", "PF", "回撤 R", "最大连亏",
              "配对数", "配对超额 R/笔", "月块 p"]
    arm_table = table(header, [row_for(ARM_NAMES[a], arms[a]) for a in cfg["arms"]]
                      + [row_for(ARM_NAMES[a] + "·先走逆向", result["arms"][a]["adverse_first"]["all"])
                         for a in cfg["arms"] if "adverse_first" in result["arms"][a]])
    gate_table = table(["门", "放行/可用", "放行率", "做多放行", "做空放行"],
                       [[GATE_NAMES[g], f"{counts[g]['passed']}/{counts['admissible']}",
                         number(100 * counts[g]["passed"] / counts["admissible"]) + "%",
                         f"{counts[g]['passed_long']}/{counts['admissible_long']}",
                         f"{counts[g]['passed_short']}/{counts['admissible_short']}"]
                        for g in GATE_NAMES])
    event_table = table(["门", "放行笔数", "放行 R/笔", "拦截笔数", "拦截 R/笔", "差值", "置换 p"],
                        [[GATE_NAMES[g], events[g]["passed_n"], number(events[g]["passed_mean_net_r"], 4, True),
                          events[g]["blocked_n"], number(events[g]["blocked_mean_net_r"], 4, True),
                          number(events[g].get("mean_difference"), 4, True), number(events[g].get("p"), 3)]
                         for g in GATE_NAMES])
    fee_rows = []
    for arm in (("v2_baseline", ref, "be_cost_no_partial")):
        curve = result["fee_curves"][arm]
        cells = [ARM_NAMES[arm]]
        for point in curve[:-1]:
            cells.append(number(point["net_r"], 2, True))
        star = curve[-1]["value"]
        cells.append(number(1e4 * star, 2, True) + " bp" if star is not None else "N/A")
        fee_rows.append(cells)
    fee_table = table(["组"] + [f"每边 {int(1e4 * r)}bp" for r in cfg["fee_sensitivity_rates"]]
                      + ["盈亏平衡费率 f*"], fee_rows)
    split_table = table(header, [row_for(label, result["arms"][ref][path][key]) for key, label in
                                 [("newly_used_window", "本轮新看的 23 个月"),
                                  ("previously_used_window", "此前已看过的窗口"),
                                  ("long", "多头"), ("short", "空头")]]
                        + [row_for(year, result["arms"][ref][path][year])
                           for year in sorted(k for k in result["arms"][ref][path] if k.isdigit())])
    rows = json.loads((EXP / f"{ref}_{path}_trades.json").read_text())
    closed = [r for r in rows if not r["censored"]]
    reasons = Counter(r["exit_reason"] for r in closed)
    exit_table = table(["最终退出原因", "笔数", "其中已平半", "净 R 合计"],
                       [[EXIT_NAMES.get(k, k), n, sum(r["partial"] for r in closed if r["exit_reason"] == k),
                         number(sum(r["net_r"] for r in closed if r["exit_reason"] == k), 2, True)]
                        for k, n in reasons.most_common()])
    delta = reference["net_r"] - base["net_r"]
    star_ref = result["fee_curves"][ref][-1]["value"]
    gross_sign = "仍然是负的" if reference["gross_r"] < 0 else "是正的"
    best_gate = max(GATE_NAMES, key=lambda g: events[g].get("p") is not None and -events[g]["p"])
    verdict = "没有任何一组做到费用后为正" if max(a["stats"]["net_r"] for a in arms.values()) <= 0 else "有组在本样本上为正"

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 4.2), layout="constrained")
    for arm, color in zip(cfg["arms"], ["#6b6f76", "#007f73", "#b25730", "#8a6fbf", "#c99700", "#4a7fb5", "#a3437f"]):
        arm_rows = [r for r in json.loads((EXP / f"{arm}_{path}_trades.json").read_text()) if not r["censored"]]
        times = [pd.Timestamp(source["first_open"])] + [pd.Timestamp(r["exit_time"]) + pd.Timedelta(minutes=5)
                                                        for r in arm_rows]
        ax.step(times, np.r_[0., np.cumsum([r["net_r"] for r in arm_rows])], where="post",
                label=f"{arm} (n={len(arm_rows)})", color=color, lw=1.4, alpha=.9)
    ax.axvline(pd.Timestamp(cfg["previously_used_window_start"]), color="#9c9c9c", ls="--", lw=.9)
    ax.axhline(0, color="#9c9c9c", lw=.8)
    ax.set(title="ETH BB x Stoch | 28 months, realized net R by arm (TV OHLC path)",
           ylabel="Cumulative net R", xlabel="UTC exit bar close; dashed line = start of the previously used window")
    ax.grid(alpha=.16)
    ax.legend(frameon=False, fontsize=8, ncol=2)
    fig.savefig(ROOT / "analysis/html/eth_bb_stoch_longrun_20260916_equity.png", dpi=170)
    plt.close(fig)

    validation = json.loads((EXP / "validation.json").read_text()) if (EXP / "validation.json").exists() else {}
    cross = json.loads((EXP / "source_receipt.json").read_text()).get("cross_check", {})
    text = f"""# ETH 5m · BB × Stoch 28 个月长周期诊断

## 结论

**把窗口从 4.4 个月拉到 28 个月（{counts['bars']:,} 根 5m，2023-12-31 → 2026-04-30），
七个事前冻结的组{verdict}。** 参照组（保本含 0.2% 成本）{reference['natural']} 笔完整交易，
净 {number(reference['net_r'], signed=True)}R，每笔 {number(reference['mean_net_r'], 4, True)}R，
PF {number(reference['profit_factor'])}；原 v2 是 {base['natural']} 笔、
净 {number(base['net_r'], signed=True)}R、每笔 {number(base['mean_net_r'], 4, True)}R。

**最关键的一个数：参照组的毛收益（完全不计手续费）{number(reference['gross_r'], signed=True)}R，{gross_sign}。**
盈亏平衡费率 f* = {number(1e4 * star_ref, 2, True) if star_ref is not None else 'N/A'} bp/边——
{'费率为负意味着把手续费降到 0 也仍然亏，成本不是主因，规则本身是负期望。' if star_ref is not None and star_ref < 0 else '这是使总净额归零所需的费率。'}

## 数据：这 23 个月是一次性支出

- 源：OKX 官方月度 1m 档案聚合 5m，`{cfg['source']}`，{source['rows']:,} 根，
  {source['first_open']} → {source['last_close']}。缺口 {source['gaps']}、重复 {source['duplicates']}。
- 与此前独立抓取的 CSV 在 **{cross.get('overlapping_bars', 0):,} 根重叠上最大 OHLC 差
  {number(cross.get('max_abs_ohlc_difference'), 10)}**，不一致行 {cross.get('rows_differing')}。两个来源互证。
- **holdout 消耗 0**：读价止于 {cfg['end']}，冻结读取器的 `end > 2026-05-01` 硬门未放宽。
- 2024-01 → 2025-12 这 23 个月此前没被本策略族用过；**本轮看过即算用掉**，
  之后再拿它调参它就不再是未接触数据。所以本轮不做任何参数搜索。

## 七个组

{arm_table}

每组只比参照多一个变化；门只过滤新入场，反向退出一律用未过滤信号。
{'两条成交路径在本样本上仍逐笔相同，敏感性零分辨力。' if all(result['arms'][a]['adverse_first']['all']['stats']['net_r'] == result['arms'][a][path]['all']['stats']['net_r'] for a in result['arms'] if 'adverse_first' in result['arms'][a]) else '两条路径存在差异，见表。'}
**不得挑其中较好的一组当成绩**：它们是同一批数据上的并列诊断，不是候选筛选。

## 门到底拦掉了什么

{gate_table}

样本内 ChartPrime 强信号 ◈ 共出现 {counts['strong_diamonds_in_sample']['strong_up']} 次（多）/
{counts['strong_diamonds_in_sample']['strong_dn']} 次（空），但要和 BB 带外＋Stoch 极区撞在同一根上极其罕见。

## 事件级：门有没有选出更好的信号

对参照规格下每个可用信号独立回放一笔（忽略占仓），按四个门各自的标签分组，
9999 次种子化标签置换，单侧检验：

{event_table}

共 {events['events']} 个事件，其中 {events['censored']} 笔末端未平仓已排除。
置换检验只回答"标签有没有信息"，不是收益证明；事件可重叠，不构成可执行账户。

## 成本结构：便宜到什么程度才不亏

{fee_table}

费用不改变止损、止盈与成交价，因此这几档是从同一条冻结价格路径**精确重算**的，不是重跑。
f* = Σ毛盈亏 / Σ成交名义；**f* 为负 = 免手续费也亏**。

## 参照组的分段与退出构成

{split_table}

{exit_table}

其中"平半时新保护价已被越过"是 Owner 本轮批准的保本含成本带来的已知副作用：
多单保本价抬到入场价上方后，触轨价若还没超过入场价+0.2%，尾仓当场就可成交。
这是规则的直接后果，已逐笔计数。**Owner 本轮没有批准"止盈不许挂在亏损侧"，
所以动态止盈漂到入场价另一侧导致的亏损仍然照常发生。**

## 风险与诚实声明

- 这是 Python 离线回放，**不声称与 TradingView 策略测试器逐笔成交一致**；5m OHLC 无法还原逐笔先后。
- 28 个月、{reference['natural']} 笔仍是单一品种单一规则的一次历史观察，**不是独立样本外验证**，
  也不是 holdout。全部七组同时出自这一批数据。
- 无排序模型，val AUC 与 top-decile 排序收益不适用，不编造；替代零假设为匹配随机入场与标签置换。
- 费用只含每边 0.1%（敏感性另列），不含资金费率、额外滑点与冲击成本；
  固定 1 ETH 归一化不等于交易所合约乘数下的账户收益率。
- 参照组末端未平仓 {reference['censored']} 笔按最后收盘盯市，不计入完整交易收益。
- 本轮不训练、不 promote、不改仓、不动真金，也不产生任何实盘资格。

## 校验与复现

源码冻结提交：`{result['source_commit']}`。只读前缀 SHA256：`{source['prefix_sha256']}`。
互证源 SHA256：`{cross.get('other_sha256')}`。验证记录：`{json.dumps(validation, ensure_ascii=False)}`。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/evaluation/test_bb_stoch_longrun_study.py \\
  tests/evaluation/test_bb_stoch_parameter_replay.py tests/evaluation/test_parabolic_rsi_sar.py \\
  tests/evaluation/test_bb_stoch_rsi_study.py tests/evaluation/test_bb_stoch_replay.py -q
.venv/bin/python -m src.data.fetch_okx --symbols ETH_USDT_SWAP --bar 5m \\
  --archive-monthly-start 2024-01 --archive-monthly-end 2026-04 \\
  --archive-max-exclusive 2026-05-04T00:00:00Z --out-dir data/kline_deep_5m
.venv/bin/python -m yoyo.evaluation.bb_stoch_longrun_study
.venv/bin/python -m yoyo.evaluation.bb_stoch_longrun_report
```

## 下一步（需 Owner 决定）

1. 这条线要不要继续。毛收益{'为负说明问题在入场规则本身，不在成本或过滤' if reference['gross_r'] < 0 else '为正，可以谈成本优化'}。
2. 若要改"止盈不许挂在亏损侧"，那是本轮未批准的第二处退出改动，需单独冻结重跑。
3. 任何 holdout（≥2026-05-04）评估仍需逐次授权并在 `docs/HOLDOUT_LEDGER.md` 记账。
"""
    report = ROOT / "analysis/p1_eth_bb_stoch_longrun_20260916.md"
    report.write_text(text)
    subprocess.run(["python3", "scripts/md_to_html.py", str(report), "--out-dir", "analysis/html"],
                   cwd=ROOT, check=True)
    print(ROOT / "analysis/html" / report.with_suffix(".html").name, flush=True)


if __name__ == "__main__":
    main()
