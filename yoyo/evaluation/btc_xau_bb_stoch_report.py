"""Render the BTC/XAU timeframe comparison; reads ledgers, never the OHLCV."""
from __future__ import annotations

import json
import subprocess
from collections import Counter

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_xau_bb_stoch_study import EXP
from yoyo.evaluation.eth_bb_stoch_study import ROOT

DATASET_NAMES = {"btc_5m": "BTC 5m", "btc_1m": "BTC 1m", "xau_1m": "XAU 1m"}
ARM_NAMES = {"v2_baseline": "原版（保本价=入场价）", "be_cost": "参照：保本含 0.2% 成本",
             "be_cost_rsi_line": "＋RSI 线区域门"}
EXIT_NAMES = {"initial_stop": "初始止损", "initial_stop_gap": "跳空初始止损",
              "break_even": "平半后保本", "break_even_gap": "跳空/同开盘保本退出",
              "break_even_marketable": "平半时新保护价已被越过",
              "opposite_signal_close": "完整反向信号退出", "boundary_censor": "末端未平仓",
              "data_gap_censor": "数据缺口截断", "target_unavailable_censor": "目标不可用截断"}


def number(value, digits=2, signed=False):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "N/A"
    return format(value, f"{'+' if signed else ''}.{digits}f")


def table(headers, rows):
    return ("| " + " | ".join(headers) + " |\n| " + " | ".join(["---"] * len(headers)) + " |\n"
            + "\n".join("| " + " | ".join(map(str, row)) + " |" for row in rows))


def main() -> None:
    cfg = json.loads((EXP / "config.json").read_text())
    loaded = {k: json.loads((EXP / k / "results.json").read_text()) for k in cfg["datasets"]
              if (EXP / k / "results.json").exists()}
    ref = cfg["reference_arm"]
    header = ["市场/周期", "组", "完整交易", "净 R", "每笔净 R", "净胜率", "PF",
              "回撤 R", "最大连亏", "0 费率净 R", "f* (bp/边)", "配对超额 R/笔", "月块 p"]
    rows = []
    for key, res in loaded.items():
        for arm in cfg["arms"]:
            s = res["arms"][arm]["all"]["stats"]
            c = res["arms"][arm]["all"]["control"]
            curve = res["fee_curves"][arm]
            star = curve[-1]["value"]
            rows.append([DATASET_NAMES[key], ARM_NAMES[arm], s["natural"],
                         number(s["net_r"], signed=True),
                         number(s["mean_net_r"], 4, True) if s["mean_net_r"] is not None else "N/A",
                         number(100 * s["win_rate"]) + "%" if s["win_rate"] is not None else "N/A",
                         number(s["profit_factor"]), number(s["max_realized_drawdown_r"]),
                         s["max_loss_streak"], number(curve[0]["net_r"], 2, True),
                         number(1e4 * star, 2, True) if star is not None else "N/A",
                         number(c["excess_mean_net_r"], 4, True), number(c["p"], 3)])
    main_table = table(header, rows)
    data_table = table(["市场/周期", "K 线根数", "覆盖区间", "缺口根", "组合信号", "其中多/空", "RSI 门放行"],
                       [[DATASET_NAMES[k], f"{r['counts']['bars']:,}",
                         f"{r['data']['first_open'][:16]} → {r['data']['last_close'][:16]}",
                         r["counts"]["gap_bars"], r["counts"]["admissible"],
                         f"{r['counts']['admissible_long']}/{r['counts']['admissible_short']}",
                         r["counts"]["rsi_line_passed"]] for k, r in loaded.items()])
    exit_rows = []
    for key, res in loaded.items():
        trades = [t for t in json.loads((EXP / key / f"{ref}_trades.json").read_text()) if not t["censored"]]
        counter = Counter(t["exit_reason"] for t in trades)
        for reason, n in counter.most_common():
            exit_rows.append([DATASET_NAMES[key], EXIT_NAMES.get(reason, reason), n,
                              number(sum(t["net_r"] for t in trades if t["exit_reason"] == reason), 2, True)])
    exit_table = table(["市场/周期", "最终退出原因", "笔数", "净 R 合计"], exit_rows)
    worst = min(loaded, key=lambda k: loaded[k]["arms"][ref]["all"]["stats"]["mean_net_r"] or 0)
    any_positive = any(loaded[k]["arms"][a]["all"]["stats"]["net_r"] > 0 for k in loaded for a in cfg["arms"])
    verdict = "有组在本样本上为正，见表" if any_positive else "没有任何一组做到费用后为正"

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, len(loaded), figsize=(4.6 * len(loaded), 3.6), layout="constrained", squeeze=False)
    for ax, (key, res) in zip(axes[0], loaded.items()):
        for arm, color in zip(cfg["arms"], ["#6b6f76", "#007f73", "#b25730"]):
            trades = [t for t in json.loads((EXP / key / f"{arm}_trades.json").read_text()) if not t["censored"]]
            times = [pd.Timestamp(res["data"]["first_open"])] + [pd.Timestamp(t["exit_time"]) for t in trades]
            ax.step(times, np.r_[0., np.cumsum([t["net_r"] for t in trades])], where="post",
                    label=f"{arm} ({len(trades)})", color=color, lw=1.3, alpha=.9)
        ax.axhline(0, color="#9c9c9c", lw=.8)
        ax.set_title(DATASET_NAMES[key], fontsize=10)
        ax.grid(alpha=.16)
        ax.tick_params(labelsize=7)
        ax.legend(frameon=False, fontsize=7)
    axes[0][0].set_ylabel("Cumulative net R")
    fig.suptitle("BB x Stoch, ETH parameters unchanged, on other markets and bar durations", fontsize=11)
    fig.savefig(ROOT / "analysis/html/btc_xau_bb_stoch_timeframes_20260916_equity.png", dpi=170)
    plt.close(fig)

    validation = json.loads((EXP / "validation.json").read_text()) if (EXP / "validation.json").exists() else {}
    ref_stats = {k: loaded[k]["arms"][ref]["all"]["stats"] for k in loaded}
    text = f"""# BB × Stoch 换市场换周期：BTC 5m/1m、XAU 1m

## 结论

**规则一个字没改，直接搬到 BTC 5m、BTC 1m、XAU 1m——{verdict}。**
参照组（保本含 0.2% 成本）每笔净 R：""" + "；".join(
        f"{DATASET_NAMES[k]} {number(ref_stats[k]['mean_net_r'], 4, True)}R（{ref_stats[k]['natural']} 笔）"
        for k in loaded) + f"""。
最差的是 {DATASET_NAMES[worst]}。**所有组合的盈亏平衡费率 f\\* 见主表**——f\\* 为负即表示
把手续费降到 0 仍然亏，成本不是病因。

这是 ETH 那轮结论的外部检验：ETH 5m 上 28 个月毛收益为负，换市场换周期后
{'结论没有改变' if not any_positive else '出现了例外，见表'}。

## 数据

{data_table}

全部经冻结读取器读到 {cfg['end']} 之前，**holdout 消耗 0**；档案下载时另加
`--archive-max-exclusive {cfg['archive_max_exclusive']}` 硬边界。
XAU-USDT-SWAP 在 OKX 上市较晚，可用历史只有 3 个多月，**笔数少，只作描述不宣称稳健**。
缺口根数按各自周期判定（1m 序列若按 5m 默认判缺口会把每根都当缺口，本轮显式声明周期）。

## 结果

{main_table}

**跨市场跨周期不能比总净 R**（笔数和单位风险口径不同），只看**每笔净 R**、
**0 费率那一列**和 **f\\***。0 费率仍为负 = 毛收益为负 = 规则本身负期望。

![三个组合的累计净 R](btc_xau_bb_stoch_timeframes_20260916_equity.png)

## 退出构成（参照组）

{exit_table}

## 这轮没有做的事

- **没有为新市场调参**。BB200、Stoch5/3/3、3% 止损、0.1% 费率全部是 ETH 5m 的值。
  1m 上 3% 是极宽的止损、BB200 只覆盖 200 分钟——这是照搬的后果，本轮如实测量。
  **不能据此说"参数没调好所以不算"**，也不能顺手调；要调是另一轮，需 Owner 指定并单独冻结。
- 没有做事件级置换（ETH 那轮已证四个门无信息，本轮只带 RSI 线门作交叉检验）。
- 没有触碰 holdout，没有训练、promote、改仓或任何真金操作。

## 风险与诚实声明

- Python 离线回放，**不声称与 TradingView 策略测试器逐笔成交一致**；OHLC 无法还原逐笔先后。
- 三段历史此前都没被本策略用过，**本轮看过即已花掉**；它们不是 holdout，也不构成独立验收。
- 无排序模型，AUC / top-decile 不适用，不编造；替代零假设为匹配随机入场。
- 费用只含每边 0.1%（敏感性另列），不含资金费率、额外滑点与冲击成本；
  固定 1 单位归一化不等于交易所合约乘数下的账户收益率。XAU 与 BTC 的合约面值不同，
  现金列不可跨市场相加。

## 校验与复现

验证记录：`{json.dumps(validation, ensure_ascii=False)}`。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/evaluation/test_btc_xau_bb_stoch_study.py \\
  tests/evaluation/test_bb_stoch_longrun_study.py tests/evaluation/test_bb_stoch_parameter_replay.py -q
for s in BTC_USDT_SWAP XAU_USDT_SWAP; do
  .venv/bin/python -m src.data.fetch_okx --symbols $s --bar 1m --archive-monthly-start 2024-01 \\
    --archive-monthly-end 2026-04 --archive-max-exclusive 2026-05-04T00:00:00Z --out-dir data/kline_deep_1m
done
.venv/bin/python -m src.data.fetch_okx --symbols BTC_USDT_SWAP --bar 5m --archive-monthly-start 2024-01 \\
  --archive-monthly-end 2026-04 --archive-max-exclusive 2026-05-04T00:00:00Z --out-dir data/kline_deep_5m
for d in btc_5m btc_1m xau_1m; do .venv/bin/python -m yoyo.evaluation.btc_xau_bb_stoch_study --dataset $d & done; wait
.venv/bin/python -m yoyo.evaluation.btc_xau_bb_stoch_report
```

## 下一步（需 Owner 决定）

1. ETH 5m、BTC 5m/1m、XAU 1m 都跑完了。若要继续，问题不在市场也不在周期，在入场规则本身。
2. 若要为某个市场重新选参（BB 长度、止损、周期），那是参数搜索，需 Owner 指定范围并单独冻结；
   本轮已消费的这三段历史届时不能再算未接触数据。
"""
    report = ROOT / "analysis/p1_btc_xau_bb_stoch_timeframes_20260916.md"
    report.write_text(text)
    subprocess.run(["python3", "scripts/md_to_html.py", str(report), "--out-dir", "analysis/html"],
                   cwd=ROOT, check=True)
    print(ROOT / "analysis/html" / report.with_suffix(".html").name, flush=True)


if __name__ == "__main__":
    main()
