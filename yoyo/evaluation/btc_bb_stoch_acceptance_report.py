"""Render the one-shot BTC 5m holdout acceptance; reads the ledger only."""
from __future__ import annotations

import json
import subprocess
from collections import Counter

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_bb_stoch_acceptance import EXP
from yoyo.evaluation.eth_bb_stoch_study import ROOT

ARM_NAMES = {"v2_baseline": "原参数 BB200/2.0/3%（图上那版）", "be_cost": "＋保本含 0.2% 成本"}
CHECK_NAMES = {"net_r_positive": "① 扣费后总净 R 为正",
               "excess_mean_net_r_positive": "② 配对随机对照超额为正",
               "signflip_p_below_threshold": "③ 月块单侧 p < 0.01"}
EXIT_NAMES = {"initial_stop": "初始止损", "initial_stop_gap": "跳空初始止损",
              "break_even": "平半后保本", "break_even_gap": "跳空/同开盘保本退出",
              "break_even_marketable": "平半时新保护价已被越过",
              "opposite_signal_close": "完整反向信号退出", "boundary_censor": "末端未平仓"}


def number(value, digits=2, signed=False):
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return "N/A"
    return format(value, f"{'+' if signed else ''}.{digits}f")


def table(headers, rows):
    return ("| " + " | ".join(headers) + " |\n| " + " | ".join(["---"] * len(headers)) + " |\n"
            + "\n".join("| " + " | ".join(map(str, row)) + " |" for row in rows))


def main() -> None:
    result = json.loads((EXP / "results.json").read_text())
    cfg, source, counts = result["config"], result["data"], result["counts"]
    path = cfg["primary_path"]
    arms = {a: result["arms"][a][path]["all"] for a in cfg["arms"]}
    primary = arms["v2_baseline"]["stats"]
    accepted = result["accepted"]
    header = ["组", "完整交易", "净 R", "每笔净 R", "毛 R", "净胜率", "PF", "回撤 R",
              "最大连亏", "配对超额 R/笔", "月块 p"]
    arm_table = table(header, [[ARM_NAMES[a], arms[a]["stats"]["natural"],
                                number(arms[a]["stats"]["net_r"], 2, True),
                                number(arms[a]["stats"]["mean_net_r"], 4, True),
                                number(arms[a]["stats"]["gross_r"], 2, True),
                                number(100 * arms[a]["stats"]["win_rate"]) + "%"
                                if arms[a]["stats"]["win_rate"] is not None else "N/A",
                                number(arms[a]["stats"]["profit_factor"], 3),
                                number(arms[a]["stats"]["max_realized_drawdown_r"]),
                                arms[a]["stats"]["max_loss_streak"],
                                number(arms[a]["control"]["excess_mean_net_r"], 4, True),
                                number(arms[a]["control"]["p"], 4)] for a in cfg["arms"]]
                      + [[ARM_NAMES[a] + "·先走逆向", result["arms"][a]["adverse_first"]["all"]["stats"]["natural"],
                          number(result["arms"][a]["adverse_first"]["all"]["stats"]["net_r"], 2, True),
                          number(result["arms"][a]["adverse_first"]["all"]["stats"]["mean_net_r"], 4, True),
                          number(result["arms"][a]["adverse_first"]["all"]["stats"]["gross_r"], 2, True),
                          "—", "—", "—", "—", "—", "—"] for a in cfg["arms"]])
    verdict_rows = []
    for arm, v in result["verdicts"].items():
        for key, label in CHECK_NAMES.items():
            verdict_rows.append([ARM_NAMES[arm], label, "✅ 满足" if v["checks"][key] else "❌ 不满足",
                                 {"net_r_positive": number(v["net_r"], 2, True),
                                  "excess_mean_net_r_positive": number(v["excess_mean_net_r"], 4, True),
                                  "signflip_p_below_threshold": number(v["p"], 4)}[key]])
    verdict_table = table(["组", "标准", "判定", "实际值"], verdict_rows)
    fee_rows = []
    for arm in cfg["arms"]:
        curve = result["fee_curves"][arm]
        star = curve[-1]["value"]
        fee_rows.append([ARM_NAMES[arm]] + [number(p["net_r"], 2, True) for p in curve[:-1]]
                        + [number(1e4 * star, 2, True) + " bp" if star is not None else "N/A"])
    fee_table = table(["组"] + [f"每边 {int(1e4*r)}bp" for r in cfg["fee_sensitivity_rates"]]
                      + ["盈亏平衡费率 f*"], fee_rows)
    rows = [r for r in json.loads((EXP / f"v2_baseline_{path}_trades.json").read_text()) if not r["censored"]]
    reasons = Counter(r["exit_reason"] for r in rows)
    exit_table = table(["最终退出原因", "笔数", "净 R 合计"],
                       [[EXIT_NAMES.get(k, k), n, number(sum(r["net_r"] for r in rows if r["exit_reason"] == k), 2, True)]
                        for k, n in reasons.most_common()])
    validation = json.loads((EXP / "validation.json").read_text()) if (EXP / "validation.json").exists() else {}

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10.5, 3.8), layout="constrained")
    for arm, color in zip(cfg["arms"], ["#6b6f76", "#007f73"]):
        trades = [r for r in json.loads((EXP / f"{arm}_{path}_trades.json").read_text()) if not r["censored"]]
        times = [pd.Timestamp(cfg["holdout_authorization"]["evaluation_start"])] + \
                [pd.Timestamp(r["exit_time"]) for r in trades]
        ax.step(times, np.r_[0., np.cumsum([r["net_r"] for r in trades])], where="post",
                label=f"{arm} (n={len(trades)})", color=color, lw=1.5)
    ax.axhline(0, color="#9c9c9c", lw=.8)
    ax.set(title="BTC 5m holdout acceptance 2026-05-04 .. 2026-08-31 | realized net R",
           ylabel="Cumulative net R", xlabel="UTC exit bar close")
    ax.grid(alpha=.16)
    ax.legend(frameon=False, fontsize=9)
    fig.savefig(ROOT / "analysis/html/btc_bb_stoch_holdout_acceptance_20260917.png", dpi=170)
    plt.close(fig)

    text = f"""# BTC 5m · BB × Stoch 最终验收（holdout）

## 判定：{'**通过**' if accepted else '**未通过**'}

**这是该配置第 1 次消耗 holdout，也是本仓第 2 次记录在案的 holdout 读取**
（`docs/HOLDOUT_LEDGER.md` 第 2 条，在读取任何字节之前登记并提交）。

计分区间 **2026-05-04T00:00Z → 2026-08-31T16:00Z**（约 3.9 个月），
实际读入 holdout K 线 {counts['holdout_rows_read']:,} 根，计分信号 {counts['scored_signals']} 个
（多 {counts['scored_long']} / 空 {counts['scored_short']}）。
原参数组 {primary['natural']} 笔完整交易，净 {number(primary['net_r'], 2, True)}R，
每笔 {number(primary['mean_net_r'], 4, True)}R，毛 {number(primary['gross_r'], 2, True)}R，
PF {number(primary['profit_factor'], 3)}。

{verdict_table}

三条标准在读数据之前就写死在 `config.json` 与计划里，由代码逐条判定，
**任何一条不满足即为未通过**。{'本次判定为通过——但通过验收不等于可以上实盘，真金操作仍需 Owner 另行逐次授权。' if accepted else '本次未通过，本配置即被否决；后续任何 BTC 5m BB×Stoch 参数实验都必须重新取得 holdout 授权。'}

## 结果

{arm_table}

![holdout 区间累计净 R](btc_bb_stoch_holdout_acceptance_20260917.png)

## 成本敏感性

{fee_table}

费用不改变止损、止盈与成交价，这几档是从同一条价格路径精确重算的。
f* = Σ毛盈亏 / Σ成交名义，即使总净额归零所需的每边费率。

## 退出构成（原参数组）

{exit_table}

## 事前预期与实际的对照

计划里写明的预期是**未通过**，理由是前段（2023-12 → 2026-04）该规则毛 +4.98R/697 笔、
f* 仅 +0.51 bp/边，而 OKX 最低 maker 2 bp。
本次实际 f* 为 {number(1e4 * (result['fee_curves']['v2_baseline'][-1]['value'] or 0), 2, True)} bp/边。
把预期写在前面是为了两件事：不让"果然没过"被说成早有洞见，也不让万一通过被当成可以直接下单。

## 风险与诚实声明

- **这次消耗不可退回。** 同一配置不得再跑第二次，也不得事后换标准、换窗口或换参数重评。
  程序在 `results.json` 已存在时直接拒绝运行。
- 参评的只有原参数与 Owner 已批准的保本含成本变体。**网格搜出的 BB400/3.0/4%、BB300/2.5/3%
  没有参评**——它们已在 2025-09→2026-04 复查段被否决（每笔 −0.4080R / −0.2843R）。
- Python 离线回放，**不声称与 TradingView 策略测试器逐笔成交一致**；5m OHLC 无法还原逐笔先后。
  两条成交路径都跑，主口径为 TV 近端极值优先。
- {counts['scored_signals']} 个信号、约 3.9 个月仍是单一品种的一段行情；**即使通过也只是通过这一次验收**，
  不构成对未来收益的保证。
- 无排序模型，val AUC 与 top-decile 排序收益不适用，不编造；替代零假设为匹配随机入场。
- 费用只含每边 0.1%，不含资金费率、额外滑点与冲击成本；固定 1 BTC 归一化不是账户收益率。
- 本轮不训练、不 promote、不改仓、不动真金。

## 校验与复现

源码冻结提交 `{result['source_commit']}`；数据 SHA256 `{source['sha256'][:16]}…`，
窗口 {source['window_start']} → {source['window_end']}，计分自 {source['evaluation_start']}。
验证记录：`{json.dumps(validation, ensure_ascii=False)}`。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/evaluation/test_btc_bb_stoch_acceptance.py -q
.venv/bin/python -m src.data.fetch_okx --symbols BTC_USDT_SWAP --bar 5m \\
  --archive-monthly-start 2026-03 --archive-monthly-end 2026-08 \\
  --archive-max-exclusive 2026-09-01T00:00:00Z --out-dir data/kline_holdout_btc5m
.venv/bin/python -m yoyo.evaluation.btc_bb_stoch_acceptance      # 已跑过，会拒绝重跑
.venv/bin/python -m yoyo.evaluation.btc_bb_stoch_acceptance_report
```
"""
    report = ROOT / "analysis/p1_btc_bb_stoch_holdout_acceptance_20260917.md"
    report.write_text(text)
    subprocess.run(["python3", "scripts/md_to_html.py", str(report), "--out-dir", "analysis/html"],
                   cwd=ROOT, check=True)
    print(ROOT / "analysis/html" / report.with_suffix(".html").name, flush=True)


if __name__ == "__main__":
    main()
