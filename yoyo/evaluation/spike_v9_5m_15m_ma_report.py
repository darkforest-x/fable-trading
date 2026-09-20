"""Temporal selection and auditable evidence for five-minute V9 MA admission.

Source: owner 2026-09-20 request and the committed experiment PROJECT_PLAN.
Only receipt-bound ledgers are read. Selection excludes future/cross-split
outcomes; shared calendar-month uncertainty retains cross-asset dependence.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v9_htf_sma_report import (
    assign_period, metrics, paired_controls, monthly_delta, markdown_table,
)
from yoyo.evaluation.spike_v10_4_study import digest
from yoyo.evaluation import spike_v1_v8_be05 as engine

EXP = Path("experiments/active/exp-spike-v9-5m-15m-ma-20260920-v1")
REPORT = Path("analysis/p1_spike_v9_5m_15m_ma_20260920.md")


def select_ma(summary, cfg):
    """Select one shared line on common, pooled, fully closed early trades."""
    dev = summary.loc[summary.scope.eq(cfg["primary_scope"])
                      & summary.universe.eq("all")
                      & summary.direction.eq("both") & summary.period.eq("earlier")].copy()
    if set(dev.ma) != set(cfg["arms"]) or len(dev) != len(cfg["arms"]):
        raise ValueError("Incomplete or duplicated development grid")
    dev["order"] = dev.ma.map({name: i for i, name in enumerate(cfg["arms"])})
    return str(dev.sort_values(["net_r", "order"], ascending=[False, True]).iloc[0].ma)


def summarize(t, cfg):
    """Keep zero-trade groups visible; every return row carries random context."""
    rows = []
    split = pd.Timestamp(cfg["split"])
    universes = {"all": t}
    if t.symbol.eq("ETHUSDT").any():
        universes["ETH"] = t.loc[t.symbol.eq("ETHUSDT")]
    for universe, data in universes.items():
        for scope in ("actual", "common"):
            for ma in cfg["arms"]:
                arm = data.loc[data.scope.eq(scope) & data.ma.eq(ma)]
                for period in ("full", "earlier", "later", "cross_split"):
                    q = arm if period == "full" else arm.loc[arm.period.eq(period)]
                    for direction, side in (("both", 0), ("long", 1), ("short", -1)):
                        z = q if not side else q.loc[q.side.eq(side)]
                        rows.append(dict(universe=universe, scope=scope, ma=ma,
                                         period=period, direction=direction,
                                         **metrics(z), **paired_controls(z, split, period)))
    return pd.DataFrame(rows)


def audit_shared_paths(t, cfg):
    """Admission cannot alter a common event's prices, exit or censor status."""
    fields = [*engine.KEY, "censored", "entry_time", "exit_time", "period"]
    rows = []
    for scope in ("actual", "common"):
        data = t.loc[t.scope.eq(scope)]
        a = data.loc[data.ma.eq("none")].set_index("event_key")
        for ma in cfg["arms"]:
            b = data.loc[data.ma.eq(ma)].set_index("event_key")
            if not a.index.is_unique or not b.index.is_unique:
                raise ValueError("Duplicate executed event within arm")
            keys = a.index.intersection(b.index).sort_values()
            pd.testing.assert_frame_equal(a.loc[keys, fields], b.loc[keys, fields],
                                          check_dtype=False, rtol=1e-9, atol=1e-9)
            rows.append(dict(scope=scope, ma=ma, common_events=len(keys), parity=True))
    return pd.DataFrame(rows)


def compare_selected(t, selected, cfg):
    """Compare the preselected policy with baseline, retaining missed winners."""
    rows, attribution = [], []
    for scope in ("common", "actual"):
        for period in ("earlier", "later"):
            q = t.loc[t.scope.eq(scope) & t.period.eq(period) & ~t.censored]
            a, b = [q.loc[q.ma.eq(ma)] for ma in ("none", selected)]
            removed = a.loc[~a.event_key.isin(b.event_key)]
            added = b.loc[~b.event_key.isin(a.event_key)]
            delta = float(b.net_r.sum() - a.net_r.sum())
            np.testing.assert_allclose(delta, added.net_r.sum() - removed.net_r.sum(), atol=1e-8)
            tail = a.loc[a.net_r.ge(10)]
            kept = int(tail.event_key.isin(b.event_key).sum())
            row = dict(scope=scope, period=period, selected_ma=selected,
                       baseline_closed=len(a), selected_closed=len(b),
                       baseline_net_r=float(a.net_r.sum()), selected_net_r=float(b.net_r.sum()),
                       baseline_10r=len(tail), retained_10r=kept,
                       retention_10r=kept / len(tail) if len(tail) else 1.)
            for field in ("net_r", "net_return"):
                row.update({field + "_" + k: v for k, v in monthly_delta(a, b, period, cfg, field).items()})
            rows.append(row)
            attribution.append(dict(scope=scope, period=period, ma=selected,
                removed=len(removed), removed_losses=int(removed.net_r.lt(0).sum()),
                removed_winners=int(removed.net_r.gt(0).sum()),
                saved_loss_r=float(-removed.loc[removed.net_r.lt(0), "net_r"].sum()),
                lost_profit_r=float(removed.loc[removed.net_r.gt(0), "net_r"].sum()),
                added=len(added), added_net_r=float(added.net_r.sum()), net_delta_r=delta))
    comp = pd.DataFrame(rows)
    primary = comp.loc[comp.scope.eq("common") & comp.period.eq("later")].iloc[0]
    actual = comp.loc[comp.scope.eq("actual") & comp.period.eq("later")].iloc[0]
    passed = (selected != "none" and primary.selected_net_r > 0
              and primary.net_r_delta > 0 and primary.net_return_delta > 0
              and primary.net_r_low95 > 0 and primary.net_r_p < .01
              and primary.retention_10r >= .85
              and actual.net_r_delta > 0 and actual.net_return_delta > 0)
    verdict = "research_pass" if passed else "baseline_selected" if selected == "none" else "not_accepted"
    return comp, pd.DataFrame(attribution), verdict


def load_receipts(source):
    """Verify replay identity, every file digest, and one independent baseline per stream."""
    identity = json.loads((source / "identity.json").read_text())
    completion = json.loads((source / "completion.json").read_text())
    assert identity["identity_hash"] == completion["identity_hash"]
    assert set(completion["symbols"]) == set(identity["inputs"])
    for path, sha in identity["declared"].items():
        assert digest(Path(path)) == sha, path
    trades, controls, evidence, coverage, receipts = [], [], [], [], []
    for symbol in completion["symbols"]:
        folder = source / "streams" / symbol
        receipt = json.loads((folder / "completion.json").read_text())
        assert receipt["identity_hash"] == identity["identity_hash"]
        assert receipt["source_sha256"] == identity["inputs"][symbol]["sha256"]
        assert all(row["real_adapter_trade_parity"] for row in receipt["coverage"])
        for name, sha in receipt["files"].items():
            assert digest(folder / name) == sha, name
        trades.append(pd.read_csv(folder / "trades.csv.gz"))
        controls.append(pd.read_csv(folder / "controls.csv.gz"))
        evidence.append(pd.read_csv(folder / "evidence.csv.gz"))
        coverage.extend(receipt["coverage"])
        receipts.append(receipt)
    t, c, e = [pd.concat(parts, ignore_index=True) for parts in (trades, controls, evidence)]
    assert not c.event_key.duplicated().any()
    t = t.merge(c, on="event_key", how="left", validate="many_to_one")
    assert t.reason.notna().all()
    split = pd.Timestamp(identity["config"]["split"])
    t["period"] = assign_period(t, split)
    for field in ("signal_bar_open", "entry_time", "exit_time"):
        t[field] = pd.to_datetime(t[field], utc=True)
    return identity, t, e, pd.DataFrame(coverage), receipts


def run(source, statistics_name="statistics_v1"):
    required = (Path(__file__), Path("tests/evaluation/test_spike_v9_5m_15m_ma_report.py"))
    if not engine._committed(required):
        raise ValueError("Commit report builder and tests before producing statistics")
    source = Path(source)
    identity, t, e, coverage, receipts = load_receipts(source)
    cfg = identity["config"]
    assert cfg == json.loads((EXP / "config.json").read_text())
    out = source / statistics_name
    out.mkdir()  # Never overwrite a prior result.
    shared = audit_shared_paths(t, cfg)
    summary = summarize(t, cfg)
    selected = select_ma(summary, cfg)
    comp, attribution, verdict = compare_selected(t, selected, cfg)
    counts = []
    for (symbol, side), group in e.groupby(["symbol", "side"]):
        for ma in cfg["arms"]:
            if ma == "none":
                known = pd.Series(True, index=group.index)
                passed = known
            else:
                family = "sma" if ma.startswith("sma") else "ema"
                length = int(ma[len(family):])
                known = group[f"{family}_{length}"].notna()
                # Recompute the strict gate from its persisted values.
                value = group[f"{family}_{length}"]
                passed = known & ((group.signal_close > value) if side == 1 else (group.signal_close < value))
            counts.append(dict(symbol=symbol, side=side, ma=ma, candidates=len(group),
                               unknown=int((~known).sum()), passed=int(passed.sum()),
                               known_reject=int((known & ~passed).sum()), common_ready=int(group.common_ready.sum())))
    candidate_counts = pd.DataFrame(counts)
    tables = {"summary": summary, "shared_path_audit": shared, "coverage": coverage,
              "selected_comparisons": comp, "attribution": attribution,
              "candidate_counts": candidate_counts}
    asset_rows = []
    for (scope, symbol, ma), group in t.groupby(["scope", "symbol", "ma"]):
        for period in ("earlier", "later", "full"):
            q = group if period == "full" else group.loc[group.period.eq(period)]
            for direction, side in (("both", 0), ("long", 1), ("short", -1)):
                z = q if not side else q.loc[q.side.eq(side)]
                asset_rows.append(dict(scope=scope, symbol=symbol, ma=ma, period=period,
                    direction=direction, **metrics(z), **paired_controls(z, pd.Timestamp(cfg["split"]), period)))
    tables["per_asset"] = pd.DataFrame(asset_rows)
    tables["control_status"] = t.groupby(["scope", "ma", "period", "reason", "censored"], dropna=False).size().rename("trades").reset_index()
    for name, table in tables.items():
        table.to_csv(out / f"{name}.csv", index=False)
    t.to_csv(out / "trades_with_controls.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    selection = dict(selected=selected, verdict=verdict, selection=cfg["selection"], replay_identity=identity["identity_hash"])
    (out / "selection.json").write_text(json.dumps(selection, indent=2) + "\n")
    view = summary.loc[summary.scope.eq("common") & summary.universe.eq("all")
                       & summary.period.isin(["earlier", "later"]) & summary.direction.eq("both")]
    sides = summary.loc[summary.scope.eq("common") & summary.universe.eq("all")
                        & summary.period.eq("later") & summary.direction.ne("both")]
    eth = summary.loc[summary.scope.eq("common") & summary.universe.eq("ETH")
                      & summary.period.isin(["earlier", "later"]) & summary.direction.eq("both")]
    actual = summary.loc[summary.scope.eq("actual") & summary.universe.eq("all")
                         & summary.period.eq("later") & summary.direction.eq("both")]
    baseline = view.loc[view.ma.eq("none") & view.period.eq("later")].iloc[0]
    chosen = view.loc[view.ma.eq(selected) & view.period.eq("later")].iloc[0]
    primary = comp.loc[comp.scope.eq("common") & comp.period.eq("later")].iloc[0]
    honest_verdict = {"research_pass": "达到预注册研究门槛，尚非生产准入", "not_accepted": "未通过预注册验证，不能推荐为实盘最优", "baseline_selected": "开发期选择不加过滤，六条线未胜过基线"}[verdict]
    count = len(identity["inputs"])
    report = f'''# 5分钟SPIKE开多空：15分钟六条均线方向过滤（2026-09-20）

结论：按前段开发期累计净R选出的共用规则是 **{selected}**；**{honest_verdict}**。后段原基线{baseline.closed:.0f}笔、{baseline.net_r:.2f}R，所选规则{chosen.closed:.0f}笔、{chosen.net_r:.2f}R；差值{primary.net_r_delta:.2f}R，月块95%区间[{primary.net_r_low95:.2f}, {primary.net_r_high95:.2f}]，单侧符号置换p={primary.net_r_p:.6f}。这些是等初始风险的事件合计，不是账户收益率。

## 固定方案与数据

- {count}币，来源为原29币Binance USDT永续5m固定档案，PEPE对应1000PEPE；ETH单列。开始{cfg['start']}，截止{cfg['end']}不含，切点{cfg['split']}。后段曾被旧研究查看，不能称新的盲样本外。8月OKX截图不参与本轮选线或验证。
- 比较SMA/EMA20、60、120与无过滤。5m确认收盘高于已知15m线才允许多头，低于才允许空头，等于/未知拒绝。只改变入场方向许可，所有臂完整双向串行回放。
- 上级15m必须由3根完整有效5m组成，仅使用5m开盘时已完成的上级值；未使用正在形成的15m均线。见[TradingView官方口径](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)。SMA需要N根，EMA第一观测播种、递推alpha=2/(N+1)，需10N连续有效上级根，缺口重置；不宣称完整TV缺口语义parity。
- 主比较common让所有臂共享六条线全部就绪的时钟；actual使用各线自己的预热时钟并保留原无过滤V9。前段只选一条多空共用线，跨切点交易和删失不参与选参；后段/全期/ETH最高值不反向改选。
- 次根开盘、五根极值+0.2ATR且最少2ATR风险、收盘2R激活4ATR追踪、0.2%往返成本均不变。反向原始信号始终退出，即使它被新线拒绝入场。

## 六条线对照：开发与验证

{markdown_table(view, ['ma','period','closed','censored','win_rate','gross_r','net_r','mean_net_bp','pf','event_drawdown_r','random_pairs','random_mean_r','random_excess_r'])}

胜率为0到1。R与名义bp是不同权重的汇总；不能把累计R换写成账户百分比。随机列为同币、同方向、同月、同时间段、同ATR/价格桶的同障碍同成本入场，random_pairs是实际有效配对分母，不保证等于交易数。随机均值/超额以R计；这些描述量不是因果证明。

## 多头与空头分开：后段

{markdown_table(sides, ['ma','direction','closed','win_rate','net_r','mean_net_bp','pf','random_pairs','random_mean_r','random_excess_r'])}

这些是每条共用线的完整双向账本分组，不代表将多头最优与空头最优拼接后的新策略。方向间或币间的赢家不能替代预先选定的主检验。

## ETH单独核查

{markdown_table(eth, ['ma','period','closed','win_rate','net_r','mean_net_bp','random_pairs','random_mean_r','random_excess_r'])}

ETH属于原样本池的子组，没有独立选参资格；本表也不是OKX8月那笔交易的回测。

## 覆盖敏感性：各线实际就绪时钟

{markdown_table(actual, ['ma','closed','net_r','mean_net_bp','random_pairs','random_mean_r','random_excess_r'])}

主结论的可接受性要求actual方向不反转。完整每币覆盖/缺口、未知过滤和共同时钟候选数见coverage.csv与candidate_counts.csv。

## 选择后的比较与归因

{markdown_table(comp, ['scope','period','selected_ma','baseline_closed','selected_closed','baseline_net_r','selected_net_r','net_r_delta','net_r_low95','net_r_high95','net_r_p','net_return_delta','retention_10r'])}

{markdown_table(attribution, ['scope','period','removed','removed_losses','removed_winners','saved_loss_r','lost_profit_r','added','added_net_r','net_delta_r'])}

过滤既可能删除亏损，也可能误删趋势赢家并释放新的交易机会。共同事件的整个成交/退出/成本/删失轨迹经过逐项一致性核对，归因满足新增净R减去移除净R等于两臂差值。上述归因只针对前段选出的规则。

## 统计门与风险与诚实声明

- 全币共用自然月块，4000次bootstrap，后段8个月精确符号置换，最小可能p=1/256（非零差异月更少时更粗）。本轮只有一个前段选定的 pooled 主检验；其余网格/方向/ETH是探索性展示。区间不是未来收益保证。
- {cfg['acceptance']}
- AUC、top-decile毛净收益与模型排序置换不适用：本轮没有分类器或排序分数。单特征线、无过滤基线、匹配随机以及共享月块零假设直接检验本轮问题，不编造这些指标。
- 未平仓/缺口删失和跨切点单独保留。手续费是固定20bp假设，未计资金费与额外真实滑点；事件曲线回撤不是共享账户回撤，不含仓位相关性与容量。
- 固定币池有历史选择偏差，Binance结果不等于OKX执行。六条线之外没搜索斜率、组合或其他长度。通过代码检查也不代表已经完成TV原生逐笔parity。
- 没有改Pine、生产默认、ACTIVE、成本、止损或账户操作；未生成HTML。结论不能自动promote。

## 完整产物与复现

候选{len(e)}条、独立执行事件{t.event_key.nunique()}个；完整臂账本、成交腿、匹配随机、候选线值与每币收据在`{source}`。统计表在`{out}`，涵盖所有臂、全期/前段/后段/跨切点、多空、逐币及控制状态。源代码先提交后运行：`{identity['source_commit']}`；回放身份`{identity['identity_hash']}`。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/evaluation/test_spike_v9_5m_15m_ma.py tests/evaluation/test_spike_v9_5m_15m_ma_report.py tests/evaluation/test_spike_v9_htf_sma.py
.venv/bin/python -m yoyo.evaluation.spike_v9_5m_15m_ma --workers 3 --output {EXP}/reproduce_run
.venv/bin/python -m yoyo.evaluation.spike_v9_5m_15m_ma_report --source {EXP}/reproduce_run
```

下一步选项：若未过门，保留为研究结果；若过门，可由Owner决定是否进入独立前向观察。更换过滤定义、方向分别选线或交付新版Pine都另立版本，不能用后段结果追选。
'''
    REPORT.write_text(report)
    files = {p.name: digest(p) for p in out.iterdir() if p.is_file()}
    receipt = dict(source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                   replay_identity=identity["identity_hash"], selected=selected, verdict=verdict,
                   report_path=str(REPORT), report_sha256=digest(REPORT), files=files,
                   input_stream_receipts=len(receipts), candidates=len(e), unique_events=int(t.event_key.nunique()))
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(selection, indent=2))
    print(view[["ma", "period", "closed", "net_r", "mean_net_bp"]].to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=EXP / "run_v1")
    parser.add_argument("--statistics-name", default="statistics_v1")
    args = parser.parse_args()
    run(args.source, args.statistics_name)
