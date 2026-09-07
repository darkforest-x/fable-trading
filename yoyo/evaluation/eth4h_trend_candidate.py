"""Preregistered conservative ETH4h candidate chain, no optimized thresholds.

Reuses only the previous fixed-hash loader and matched-event inference protocol.
Source data end April2026. All development/replication/accounting windows and
single-variable stages are committed before this runner evaluates any returns.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation.pine_allin_eth4h_replay import (
    ROOT, clean, controls, digest, inference, load_source, save_json, table, trade_stats,
)
from yoyo.layers.l3_backtest.eth4h_trend_candidate import CHAIN, CandidateReplay
from yoyo.layers.l3_backtest.pine_allin_eth4h import Policy, Replay

EXP = ROOT / "experiments/active/exp-eth4h-trend-candidate-20260907-v1"
OUT = EXP / "results"
END = pd.Timestamp("2026-05-01T00:00Z")
WINDOWS = (
    ("development", "2021-01-01", "2025-01-01"),
    ("exposed_replication", "2025-01-01", "2026-05-01"),
    ("continuous", "2021-01-01", "2026-05-01"),
)


def close_boundary(out, frame, end, fee):
    """Explicit administrative last-close exit, including exit commission."""
    pos = out["open_position"]
    if pos is None:
        return out
    price = float(frame.close.iloc[end - 1])
    cost = pos["qty"] * price * fee
    gross = pos["direction"] * pos["qty"] * (price - pos["entry_price"])
    net = gross - pos["entry_fee"] - cost
    notional = pos["qty"] * pos["entry_price"]
    row = {**pos, "exit_i": end - 1, "exit_time": frame.open_time.iloc[end - 1] + pd.Timedelta(hours=4),
           "exit_price": price, "exit_reason": "research_end", "gross_pnl": gross,
           "net_pnl": net, "gross_return": gross / notional, "net_return": net / notional,
           "fees": pos["entry_fee"] + cost, "holding_bars": end - 1 - pos["entry_i"]}
    out["trades"] = pd.concat([out["trades"], pd.DataFrame([row])], ignore_index=True)
    out["cash"] += gross - cost
    out["fees"] += cost
    out["final_equity"] = out["cash"]
    e = out["equity"]
    e.loc[e.index[-1], ["equity", "cash", "position"]] = [out["cash"], out["cash"], 0]
    peak = max(500., float(e.equity.max()))
    close_dd = float((1 - e.equity / e.equity.cummax().clip(lower=500)).max())
    out["max_drawdown_close"] = max(out["max_drawdown_close"], close_dd)
    out["max_drawdown_path"] = max(out["max_drawdown_path"], (peak - out["cash"]) / peak)
    out["open_position"] = None
    return out


class BoundaryReplay(CandidateReplay):
    def run(self, start, end, *, injected=None):
        result = super().run(start, end, injected=injected)
        return close_boundary(result, self.frame, end, self.policy.fee)


def verify_original_defaults(frame):
    """Regression against frozen preceding ledgers, no new strategy selection."""
    old = ROOT / "experiments/active/exp-pine-allin-eth4h-20260907-v1/results"
    start = int(frame.open_time.searchsorted(pd.Timestamp("2021-01-01T00:00Z")))
    out = Replay(frame, Policy("original_cost20", fee=.001)).run(start, len(frame))
    golden = pd.read_csv(old / "continuous_2021_2026apr_original_cost20_trades.csv", float_precision="round_trip")
    for field in ("signal_i", "entry_i", "exit_i", "qty", "entry_price", "exit_price", "net_pnl"):
        assert np.array_equal(golden[field].to_numpy(), out["trades"][field].to_numpy()), field
    return {"passed": True, "trades": len(golden), "comparison": "exact array equality", "fields": 7}


def write_report(payload):
    allrows = payload["summary"]
    final = [x for x in allrows if x["arm"] == CHAIN[-1].name]
    cols = [("arm", "阶段"), ("return_pct", "账户净收益%"), ("dd_pct", "模拟回撤%"), ("trades", "笔数"),
            ("win_rate_pct", "净胜率%"), ("pf_currency", "资金PF"), ("net_bp", "每笔净bp"),
            ("matched_case_bp", "匹配病例净bp"), ("random_control_bp", "随机净bp"), ("excess_bp", "配对超额bp")]
    finalcols = [("window", "窗口"), *cols[1:]]
    screen = "通过预先设定的经济初筛" if payload["screen"]["passed"] else "未通过预先设定的经济初筛"
    qa_path = OUT / 'independent_validation.json'
    verification = ''
    if qa_path.exists():
        qa = json.loads(qa_path.read_text())
        verification = '\n## 逐笔核验与收益集中度\n\n'
        verification += f"独立核对已保存账本，{sum(qa['checks'].values())}/{len(qa['checks'])}项通过；未重跑信号，也不等于原生撮合验证。\n\n"
        verification += table(qa['concentration'], [('window','窗口'),('positive_trades','净盈利笔数'),('trades','总笔数'),('cagr_pct','年化%'),('largest5_positive_share_pct','最大5笔占正利润%'),('net_bp_excluding_largest3','剔除最大3笔后净bp')])
        verification += '\n\n剔除大盈利单仅诊断集中度，不是可以提前执行的策略；短区间年化不是预期收益。\n\n'
        verification += table(qa['annual'], [('year','年份'),('return_pct','当年账户收益%'),('end_equity','年末权益USDT')])
        verification += '\n\n2026仅至4月。跨年持仓按市值计入年份；每个独立窗口边界平仓会改变后续状态，因此不能机械拼接收益。\n\n'
    pine = EXP / 'eth4h_trend_r1.pine'
    (ROOT / 'analysis/html/eth4h_trend_r1.pine').write_bytes(pine.read_bytes())
    text = f"""# ETH 4H Trend R1：保守仓位候选策略

本轮目标是开发扣费后盈利、回撤可控的策略。最终候选固定为 S5，没有按收益挑中间版本。
**结果：{screen}。** 这不是未来盈利保证，原生 TradingView 对齐及全新前向验证尚未完成。

## 可交付策略

[Pine 源码](eth4h_trend_r1.pine)。
普通 BINANCE:ETHUSDT.P、4 小时；初始500USDT；每边手续费0.1%，不另加滑点或资金费。
默认仅研究截至2026-04-30，Pine 中有日期上限以保护 holdout。没有创建实盘警报或连接交易账户。

- 入场：原有 SMA10/60 交叉 + EMA100 方向 + 振荡器确认，收盘确认、下一根开盘。
- 仓位：按权益0.5%的价格止损风险算数量，最多1倍。手续费、跳空可能使实际亏损大于计划风险。
- 止损：原4ATR与3%取小，随入场单一起提交；保本门槛仍为1.5%/0.1%，未修改。
- 同向信号仅收紧止损；多空止损各自独立；冷却先更新；反向信号只平仓，等待后续新信号再入场。
- 20%是研究验收目标，未把在20%处强制清仓当成保证，也没有靠低仓位掩盖负的单位期望。

## 最终候选 S5

{table(final, finalcols)}

窗口：development=2021–2024；exposed_replication=2025–2026年4月；continuous=2021–2026年4月。
各窗口都从空仓500USDT启动，边界强制按末根收盘平仓并计费用。后段数据已经看过，不能叫真正未见样本外。

## 单变量变化链：连续窗口

{table([x for x in allrows if x['window']=='continuous'], cols)}

S0：首根保护+固定1倍；S1：只改0.5%风险仓位；S2：只改止损仅收紧；S3：只改止损状态隔离；
S4：只改冷却更新时间；S5：只改反向信号只平仓。SMA comparator：S5只换成均线交叉入场，其余一样。
所有参数和链条在结果前冻结；不能将最后版本失败改写成“最佳中间版本成功”。

此次S1相对S0的回撤下降来自仓位缩小；单位净收益没有提高。S2–S4在这批数据没有改变收益，
合成测试验证了保护行为，但不能把代码修复算成额外交易优势。S5改变交易集合后收益小幅增加。

{verification}

## 开发期与已暴露后段的完整记录

{table([x for x in allrows if x['window']=='development'], cols)}

{table([x for x in allrows if x['window']=='exposed_replication'], cols)}

## 统计与样本

{table(payload['ranking'], [('window','窗口'),('n','已平仓样本'),('auc_abs_osc_vs_net_positive','AUC'),('top_decile_gross_bp','前10%毛bp'),('top_decile_net_bp','前10%净bp'),('top_decile_control_bp','随机净bp'),('top_decile_paired_excess_bp','配对超额bp'),('ranking_permutation_p','排序p'),('matched_month_cluster_signflip_p','月簇配对p')])}

原策略不按振荡器大小选前10%，上表仅诊断，没有训练和调参。正类为净收益>0，正类率见净胜率。
匹配同币、UTC月、香港6小时时间块、过去252根ATR%分位和方向，固定哈希选最多3例，病例前后12根禁入。
对照使用相同规则和独立状态，不复制未来持仓时长；可跨病例重叠，不能把对照合成可交易组合。覆盖率和缺样见summary.json。

## 风险与诚实声明

- 数据：{payload['data']['source_rows']}根15m聚合为{payload['data']['four_hour_rows']}根4h；完整16根、无缺口、固定SHA256。2020预热，不读取2026-05-04起holdout。
- 研发已经受此前回测结果影响；不能把2025年后再次复测冒充独立验证。经济初筛不等于生产验收。
- 只实现并审查了Pine源码，没有运行原生Pine编译器、保证金撮合或逐笔对账；Python以连续数量模拟。
- 没有资金费、额外滑点、交易所强平/最低数量/取整模型；策略收益仍可能被这些成本侵蚀。
- 0.1%保本偏移小于本次名义往返费用，所以小额保本单仍可能净亏；本轮没有擅自更改该参数。
- 最终盈利和失败均保留。保护代码不能代替正的单位收益，也不能补足样本。

## 验收目标及结论

```json
{json.dumps(clean(payload['screen']),ensure_ascii=False,indent=2)}
```

本轮只给出可复核的研究候选，training_eligible=false、production_eligible=false。
下一步应先完成同日期原生逐笔核对。若要改变成本/障碍参数或动用holdout，需要Owner另行明确决定。

## 资金曲线

![最终候选与起点](../experiments/active/exp-eth4h-trend-candidate-20260907-v1/results/equity.png)

## 复现

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest tests/test_pine_allin_eth4h.py tests/test_eth4h_trend_candidate.py -q
.venv/bin/python -m yoyo.evaluation.eth4h_trend_candidate
.venv/bin/python scripts/md_to_html.py analysis/p0_eth4h_trend_candidate_20260907.md --out-dir analysis/html
```

前提：已有SHA256固定的只读Binance15m文件，见此前ETH4h回放source receipt。已有结果时拒绝覆盖。
来源：[TradingView策略执行](https://www.tradingview.com/pine-script-docs/v5/concepts/strategies/)、[策略属性](https://www.tradingview.com/support/solutions/43000628599-strategy-properties/)。
"""
    path = ROOT / "analysis/p0_eth4h_trend_candidate_20260907.md"
    path.write_text(text)
    subprocess.run([str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/md_to_html.py"), str(path),
                    "--out-dir", str(ROOT / "analysis/html")], check=True)


def main():
    if OUT.exists():
        raise RuntimeError("Output exists; do not overwrite an experiment")
    config = json.loads((EXP / "config.json").read_text())
    assert config["chain"] == [asdict(p) for p in CHAIN]
    files = [Path(__file__), ROOT / "yoyo/layers/l3_backtest/eth4h_trend_candidate.py",
             ROOT / "yoyo/layers/l3_backtest/pine_allin_eth4h.py", EXP / "config.json", EXP / "eth4h_trend_r1.pine"]
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    import hashlib
    for path in files:
        frozen = subprocess.check_output(["git", "show", f"HEAD:{path.relative_to(ROOT)}"])
        assert hashlib.sha256(frozen).hexdigest() == digest(path), path
    frame, receipt = load_source()
    regression = verify_original_defaults(frame)
    OUT.mkdir(parents=True)
    payload = {"generated_at": datetime.now(timezone.utc), "builder_commit": commit,
               "data": receipt, "code_hashes": {str(p.relative_to(ROOT)):digest(p) for p in files},
               "original_default_regression": regression, "summary": [], "ranking": [],
               "holdout_consumed": False, "training_eligible": False, "production_eligible": False}
    save_json(OUT / "pre_run_receipt.json", payload)
    plotted = {}
    for window, startdate, enddate in WINDOWS:
        start = int(frame.open_time.searchsorted(pd.Timestamp(startdate,tz="UTC")))
        end = int(frame.open_time.searchsorted(pd.Timestamp(enddate,tz="UTC")))
        for policy in (*CHAIN, replace(CHAIN[-1], name="SMA_comparator", cross_only=True)):
            runner = BoundaryReplay(frame, policy)
            out = runner.run(start, end)
            trades, ctrl = controls(frame, runner, out["trades"], start, end)
            stem = f"{window}_{policy.name}"
            for suffix, df in (("trades",trades),("controls",ctrl),("equity",out["equity"]),("events",out["events"])):
                df.to_csv(OUT / f"{stem}_{suffix}.csv",index=False)
            expected = trades.direction * trades.qty * (trades.exit_price - trades.entry_price) - trades.fees
            assert np.allclose(trades.net_pnl,expected)
            assert np.isclose(500 + trades.net_pnl.sum(),out["final_equity"])
            row={"window":window,"arm":policy.name,**trade_stats(trades),
                 "return_pct":(out["final_equity"]/500-1)*100,"dd_pct":out["max_drawdown_path"]*100,
                 "close_dd_pct":out["max_drawdown_close"]*100,"fees":out["fees"],
                 "max_actual_entry_leverage":float(trades.leverage.max()),
                 "nonpositive_equity_seen":out["nonpositive_equity_seen"],
                 "administrative_exits":int(trades.exit_reason.eq("research_end").sum()),"ledger_passed":True}
            payload["summary"].append(row)
            if policy.name==CHAIN[-1].name:
                payload["ranking"].append({"window":window,**inference(trades)})
            if window=="continuous" and policy in (CHAIN[0],CHAIN[-1]):
                plotted[policy.name]=out["equity"]
            print(json.dumps({k:row[k] for k in ("window","arm","return_pct","dd_pct","trades","net_bp","excess_bp")}),flush=True)
    final=[r for r in payload["summary"] if r["arm"]==CHAIN[-1].name]
    screen={}
    for r in final:
        w=r["window"]
        screen[w+"_net_return_positive"]=r["return_pct"]>0
        screen[w+"_paired_excess_positive"]=r["excess_bp"]>0
        screen[w+"_drawdown_within20pct"]=r["dd_pct"]<=20
        screen[w+"_positive_equity"]=not r["nonpositive_equity_seen"]
    payload["screen"]={"passed":all(screen.values()),"criteria":screen,"native_parity":False,
                       "genuinely_unseen_evidence":False,"production_eligible":False}
    fig,axes=plt.subplots(2,1,figsize=(10,6),sharex=True)
    for name,e in plotted.items():
        axes[0].plot(e.time,e.equity,label=name)
        axes[1].plot(e.time,100*(e.equity/e.equity.cummax().clip(lower=500)-1),label=name)
    axes[0].set_title("ETH 4h Trend R1 | fixed research chain | 2021-Apr2026")
    axes[0].set_ylabel("USDT equity");axes[1].set_ylabel("Close drawdown %")
    for ax in axes:ax.grid(alpha=.2);ax.legend()
    fig.tight_layout();fig.savefig(OUT/'equity.png',dpi=150);plt.close(fig)
    save_json(OUT/'summary.json',payload)
    pd.DataFrame(payload['summary']).to_csv(OUT/'summary.csv',index=False)
    write_report(payload)
    print(json.dumps(clean(payload['screen']),indent=2),flush=True)


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['--report-only']:
        write_report(json.loads((OUT / 'summary.json').read_text()))
    elif sys.argv[1:]:
        raise SystemExit('Only --report-only is supported; existing results are never overwritten.')
    else:
        main()
