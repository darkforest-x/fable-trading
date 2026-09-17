"""Render the BTC 5m grid search; reads the frozen summaries, never the OHLCV."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.eth_bb_stoch_study import ROOT

EXP = ROOT / "experiments/active/exp-btc-bb-stoch-optimization-20260916-v1"
NAMES = {"bb200_m2_sl3": "原参数 BB200 / 2.0 / 3%",
         "bb400_m3_sl4": "稳健候选 BB400 / 3.0 / 4%",
         "bb300_m2.5_sl3": "峰值 BB300 / 2.5 / 3%"}


def number(value, digits=2, signed=False):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "N/A"
    return format(value, f"{'+' if signed else ''}.{digits}f")


def table(headers, rows):
    return ("| " + " | ".join(headers) + " |\n| " + " | ".join(["---"] * len(headers)) + " |\n"
            + "\n".join("| " + " | ".join(map(str, row)) + " |" for row in rows))


def worst_mode(arm):
    return min(arm["modes"].values(), key=lambda v: v["equity"]["net_liquidation_mark_usdt"])


def arm_row(label, arm, controls=True):
    v = worst_mode(arm)
    s, e, c = v["stats"], v["equity"], v.get("control", {})
    # The grid stage draws no controls, so its p is a placeholder, not a test.
    p_cell = number(c.get("p"), 3) if controls else "未抽对照"
    return [label, s["natural"], number(e["net_liquidation_mark_usdt"]),
            number(s.get("mean_net_r"), 4, True), number(s.get("gross_r"), 2, True),
            number(s.get("profit_factor"), 3),
            number(100 * s["win_rate"]) + "%" if s.get("win_rate") is not None else "N/A",
            number(e["close_mtm_drawdown_usdt"]), s.get("max_loss_streak"), p_cell]


def main() -> None:
    cfg = json.loads((EXP / "config.json").read_text())
    dev = json.loads((EXP / "development_summary.json").read_text())
    rec = json.loads((EXP / "recheck_summary.json").read_text())
    selection = json.loads((EXP / "selection.json").read_text())
    local = json.loads((EXP / "local_artifacts.json").read_text())
    header = ["配置", "完整交易", "净清算 USDT", "每笔净 R", "毛 R", "PF", "净胜率",
              "收盘盯市回撤 USDT", "最大连亏", "月块 p"]
    dev_table = table(header, [arm_row(NAMES[k], dev[k], controls=False) for k in NAMES])
    rec_table = table(header, [arm_row(NAMES[k], rec[k]) for k in NAMES])
    marks = {k: min(v["equity"]["net_liquidation_mark_usdt"] for v in a["modes"].values())
             for k, a in dev.items()}
    positive = sorted([k for k, v in marks.items() if v > 0], key=lambda k: -marks[k])
    grid_table = table(["开发段较差路径净清算为正的组合", "净清算 USDT", "BB 长度", "倍数", "止损"],
                       [[k, number(marks[k]), dev[k]["params"]["bb_length"],
                         dev[k]["params"]["bb_mult"], f"{dev[k]['params']['stop_fraction']*100:g}%"]
                        for k in positive])
    base_rec = worst_mode(rec["bb200_m2_sl3"])["stats"]
    robust_rec = worst_mode(rec[selection["primary"]])["stats"]
    survived = robust_rec.get("mean_net_r", 0) > base_rec.get("mean_net_r", 0)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), layout="constrained")
    for ax, (stage, summary, label) in zip(axes, [("development", dev, "开发段 2024-01 → 2025-09"),
                                                   ("recheck", rec, "复查段 2025-09 → 2026-04")]):
        keys = [k for k in NAMES if k in summary]
        values = [min(v["equity"]["net_liquidation_mark_usdt"] for v in summary[k]["modes"].values()) for k in keys]
        colors = ["#6b6f76" if v < 0 else "#007f73" for v in values]
        ax.bar(range(len(keys)), values, color=colors)
        ax.axhline(0, color="#4a4a4a", lw=.9)
        ax.set_xticks(range(len(keys)))
        ax.set_xticklabels(["BB200/2.0/3%", "BB400/3.0/4%", "BB300/2.5/3%"], fontsize=8)
        ax.set_title(f"{stage}  worst-path net liquidation (USDT)", fontsize=10)
        ax.grid(alpha=.16, axis="y")
    fig.suptitle("BTC 5m grid: development winners vs the untouched later window", fontsize=11)
    fig.savefig(ROOT / "analysis/html/btc_bb_stoch_optimization_20260916.png", dpi=170)
    plt.close(fig)

    text = f"""# BTC 5m · BB × Stoch 315 组参数搜索

## 结论

**调参在开发段把这条规则从亏变成赚，然后在复查段全部崩掉，而且比不调参更差。**

开发段（2024-01 → 2025-09）按事前固定规则选出稳健候选 **BB400 / 倍数3.0 / 止损4%**
（131 笔，每笔 {number(worst_mode(dev['bb400_m3_sl4'])['stats']['mean_net_r'], 4, True)}R，
净清算 {number(marks['bb400_m3_sl4'])} USDT），诊断用峰值为 **BB300 / 2.5 / 3%**。
复查段（2025-09 → 2026-04，选择提交之后才读）两者分别变成每笔
{number(robust_rec['mean_net_r'], 4, True)}R 和
{number(worst_mode(rec['bb300_m2.5_sl3'])['stats']['mean_net_r'], 4, True)}R；
**同期未调参的原参数是 {number(base_rec['mean_net_r'], 4, True)}R。**
{'稳健候选在复查段仍优于原参数。' if survived else '两个入选者在复查段都输给了原参数——搜索不但没找到更好的参数，还找到了更差的。'}

**而且不是成本问题。**复查段稳健候选的毛 R 是
{number(robust_rec.get('gross_r'), 2, True)}（49 笔），**免手续费也亏**；
开发段那点优势一天都没延续下来。

## 开发段（选择依据，2024-01 → 2025-09）

{dev_table}

开发段按事前约定**不抽随机对照**（对照是用来验证入选者的，不参与 315 组排序），
所以这一段没有 p 值可报，表中如实写"未抽对照"而不是填一个占位数字。

## 复查段（一次性评分，2025-09 → 2026-04）

{rec_table}

![开发段 vs 复查段](btc_bb_stoch_optimization_20260916.png)

## 为什么这个结果是可预期的

- 315 组里**只有 {len(positive)} 组**在较差成交路径下净清算为正，占 {number(100*len(positive)/len(dev))}%。
  这个存活率本身就接近"在噪声里挑极值"的量级。
- 赢家全部挤在网格的同一个角落：BB 长度 300–400、倍数 2.5–3.0、止损 3–5%——
  也就是"带子极宽、止损极宽、信号极少"。BB400/3.0 在 20 个月里只有 131 笔，平均 4.6 天一笔。

{grid_table}

- 这是**第二次**看到同一个形态。ETH 那轮（`exp-eth-bb-stoch-optimization-20260916-v1`）
  同样是开发段最优、复查段翻负，见
  `docs/learnings/a-grid-winner-can-depend-on-one-boundary-position.md`。

## 这轮的诚实边界

1. **复查段不是新鲜盲验。** 这 28 个月在 2026-09-16 的冻结回测里已经被整体看过，
   复查段只是已观察数据内部的时间留出。真正未接触的只有 holdout（≥2026-05-04），本轮不碰，
   **holdout 消耗 0**。
2. **多重比较**：315 组 × 2 条成交路径。选择规则事前固定为邻域中位数而非峰值，
   并在 `selection.json` 提交（`{selection['source_commit'][:10]}`）之后才读复查段价格，
   程序核对字节一致才肯运行——这道闸是代码强制的。
3. **不因为失败再搜一轮。**换网格、换 Stoch 参数、换周期都属于新的搜索，需要 Owner 另行指定；
   而且这 28 个月已经被消费，届时不能再算未接触数据。
4. 先验已经说明了这一点：同规则在这段数据上毛收益仅 +4.98R/697 笔、
   盈亏平衡费率 +0.51bp/边，而 OKX 最低 maker 2bp。**要靠调参翻盘需要把毛优势提高一个数量级**，
   而不是把信号数从 523 笔砍到 131 笔。

## 风险与诚实声明

- Python 离线回放，**不声称与 TradingView 策略测试器逐笔成交一致**；OHLC 无法还原逐笔先后。
  两条成交路径都跑，表中取较差者。
- 净清算 USDT 按固定 1 BTC 名义归一化，**不是账户收益率**；BTC 与 ETH 的现金列不可相加。
- 无排序模型，val AUC 与 top-decile 排序收益不适用，不编造；替代零假设为匹配随机入场。
- 本轮不训练、不 promote、不改仓、不动真金，**不产生任何实盘资格**。
  开发段的正收益**不是**可交易结论。

## 校验与复现

网格冻结提交 `{selection['source_commit'][:10]}`；选择在读复查段之前生成
（`selected_without_recheck_price_read: {json.dumps(selection['selected_without_recheck_price_read'])}`）。
逐组账本 {local['files']['development_ledger.jsonl.gz']['lines']} 行、
{local['files']['development_ledger.jsonl.gz']['size_bytes']/1e6:.1f} MB 因体积只在本地保留，
SHA256 `{local['files']['development_ledger.jsonl.gz']['sha256'][:16]}…` 记录在 `local_artifacts.json`。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m yoyo.evaluation.bb_stoch_optimization development \\
  --exp experiments/active/exp-btc-bb-stoch-optimization-20260916-v1
git add experiments/active/exp-btc-bb-stoch-optimization-20260916-v1 && git commit   # 必须先提交
.venv/bin/python -m yoyo.evaluation.bb_stoch_optimization recheck \\
  --exp experiments/active/exp-btc-bb-stoch-optimization-20260916-v1
.venv/bin/python -m yoyo.evaluation.btc_bb_stoch_optimization_report
```

## 下一步（需 Owner 决定）

1. **建议到此为止。** ETH 毛为负、BTC 毛微正但 f\\* 拿不到、XAU 约零、参数搜索两次都在复查段崩掉。
2. 若仍要继续，应该换的是**入场形态本身**，不是这条规则的参数。
3. 任何 holdout 评估仍需逐次授权并在 `docs/HOLDOUT_LEDGER.md` 记账。
"""
    report = ROOT / "analysis/p1_btc_bb_stoch_optimization_20260916.md"
    report.write_text(text)
    subprocess.run(["python3", "scripts/md_to_html.py", str(report), "--out-dir", "analysis/html"],
                   cwd=ROOT, check=True)
    print(ROOT / "analysis/html" / report.with_suffix(".html").name, flush=True)


if __name__ == "__main__":
    main()
