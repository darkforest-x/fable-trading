"""Preregistered temporal selection and full evidence for the V9 HTF SMA study.

Reads only receipt-bound replay outputs. Development selection uses closed
trades ending before the split, never later outcomes. Monthly blocks preserve
cross-symbol dependence; sum-R drawdown is an event curve, not account equity.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v9_htf_sma_study import EXP, config
from yoyo.evaluation.spike_v10_4_study import digest
from yoyo.evaluation.spike_v1_v8_be05 import _committed
from yoyo.evaluation import spike_v9_htf_sma_study as study


def assign_period(frame, split):
    """Separate cross-split positions so future exits cannot select a parameter."""
    entered = pd.to_datetime(frame.entry_time, utc=True)
    exited = pd.to_datetime(frame.exit_time, utc=True)
    return np.where(entered >= split, "later", np.where(exited < split, "earlier", "cross_split"))


def metrics(g):
    closed = g.loc[~g.censored].copy()
    r = closed.net_r.astype(float)
    ordered = closed.sort_values(["exit_time", "event_key"])
    curve = np.r_[0., ordered.net_r.to_numpy(float).cumsum()]
    loss = -r[r < 0].sum()
    nloss = -closed.loc[closed.net_return < 0, "net_return"].sum()
    return {"entries": len(g), "closed": len(closed), "censored": int(g.censored.sum()),
            "win_rate": float((r > 0).mean()) if len(r) else np.nan,
            "net_r": float(r.sum()), "gross_r": float(closed.gross_r.sum()),
            "mean_net_r": float(r.mean()) if len(r) else np.nan,
            "net_bp": float(closed.net_return.sum() * 1e4),
            "mean_net_bp": float(closed.net_return.mean() * 1e4) if len(r) else np.nan,
            "pf": float(r[r > 0].sum() / loss) if loss else np.nan,
            "pf_nominal": float(closed.loc[closed.net_return > 0, "net_return"].sum() / nloss) if nloss else np.nan,
            "event_drawdown_r": float((np.maximum.accumulate(curve) - curve).max()),
            "realized_10r": int((r >= 10).sum())}


def select_lengths(summary):
    """Select only actual-scope, development, both-side total netR; prefer0 ties."""
    dev = summary.loc[summary.scope.eq("actual") & summary.period.eq("earlier") & summary.direction.eq("both")]
    return {tf: int(g.sort_values(["net_r", "length"], ascending=[False, True]).iloc[0].length)
            for tf, g in dev.groupby("timeframe")}


def paired_controls(g, split, period):
    valid = (~g.censored & g.matched.fillna(False).astype(bool) & ~g.control_censored.fillna(True).astype(bool))
    if period == "earlier":
        valid &= pd.to_datetime(g.control_exit_time, utc=True) < split
    if period == "later":
        valid &= pd.to_datetime(g.control_entry_time, utc=True) >= split
    c = g.loc[valid]
    return {"random_pairs": len(c), "random_mean_r": float(c.control_net_r.mean()),
            "paired_strategy_mean_r": float(c.net_r.mean()),
            "random_excess_r": float((c.net_r - c.control_net_r).mean())}


def monthly_delta(a, b, period, cfg, field="net_r"):
    """Block bootstrap/sign-flip sums over shared calendar months, not trades."""
    split = pd.Timestamp(cfg["split"])
    lo, hi = (pd.Timestamp(cfg["start"]), split) if period == "earlier" else (split, pd.Timestamp(cfg["end"]))
    months = pd.period_range(lo.tz_localize(None).to_period("M"), (hi - pd.Timedelta(nanoseconds=1)).tz_localize(None).to_period("M"), freq="M").astype(str)
    def totals(g):
        return g.groupby(pd.to_datetime(g.entry_time, utc=True).dt.strftime("%Y-%m"))[field].sum().reindex(months, fill_value=0.)
    d = (totals(b) - totals(a)).to_numpy(float)
    rng = np.random.default_rng(cfg["seed"])
    draws = rng.integers(0, len(d), size=(cfg["bootstrap_reps"], len(d)))
    values = d[draws].sum(axis=1)
    lo95, hi95 = np.quantile(values, [.025, .975])
    if len(d) <= 16:
        bits = np.arange(2**len(d), dtype=np.uint64)[:, None]
        signs = ((bits >> np.arange(len(d), dtype=np.uint64)) & 1).astype(float) * 2 - 1
        p = float(np.mean((signs @ d) >= d.sum() - 1e-12))
    else:
        signs = rng.choice([-1., 1.], size=(cfg["bootstrap_reps"], len(d)))
        p = float((1 + ((signs @ d) >= d.sum() - 1e-12).sum()) / (len(signs) + 1))
    return {"months": len(d), "delta": float(d.sum()), "low95": float(lo95), "high95": float(hi95), "p": p}


def holm(values):
    order = np.argsort(values); out = np.ones(len(values)); prior = 0.
    for rank, i in enumerate(order):
        prior = max(prior, min(1., values[i] * (len(values) - rank)))
        out[i] = prior
    return out


def markdown_table(frame, columns):
    # Avoid optional tabulate dependency.
    def fmt(v):
        if isinstance(v, (float, np.floating)): return "—" if not np.isfinite(v) else f"{v:.3f}"
        return str(v)
    return "| " + " | ".join(columns) + " |\n|" + "|".join(["---"]*len(columns)) + "|\n" + "\n".join("| " + " | ".join(fmt(row[c]) for c in columns) + " |" for _, row in frame.iterrows())


def audit_baselines(identity, trades):
    """Verify every archived gate-off trade against the original V9 adapter."""
    rows = []
    for symbol, item in identity["inputs"].items():
        path = Path(item["path"])
        assert digest(path) == item["sha256"]
        base = study.inc.guarded_5m(path, study.source.START - pd.Timedelta(days=study.source.WARMUP_BARS))
        for tf, minutes, _, _ in identity["config"]["pairs"]:
            facts = study.source.v9_facts(study.v11.bars_for(base, minutes), minutes, item["meta"]["asset"], float(item["meta"]["tick"]))
            prepared = study.build_prepared(facts, symbol, item["meta"], tf, minutes)
            legacy, _, _, _ = study.replay_v9(prepared.context)
            actual = trades.loc[trades.symbol.eq(symbol) & trades.timeframe.eq(tf) & trades.scope.eq("actual") & trades.length.eq(0)]
            fields = [*study.engine.KEY, "censored"]
            pd.testing.assert_frame_equal(actual[fields].reset_index(drop=True), legacy[fields].reset_index(drop=True), check_dtype=False, rtol=1e-9, atol=1e-9)
            rows.append({"symbol":symbol, "timeframe":tf, "trades":len(actual), "parity":True})
        assert digest(path) == item["sha256"]
    return pd.DataFrame(rows)


def run(source):
    if not _committed((Path(__file__), Path("tests/evaluation/test_spike_v9_htf_sma_report.py"))):
        raise ValueError("Commit report builder and tests before statistics")
    cfg = config(); split = pd.Timestamp(cfg["split"]); source = Path(source)
    identity = json.loads((source / "identity.json").read_text())
    complete = json.loads((source / "completion.json").read_text())
    assert len(complete["symbols"]) == 29 and complete["streams"] == 87
    assert complete["identity_hash"] == identity["identity_hash"]
    assert cfg == identity["config"]
    trades, controls, evidence, coverage = [], [], [], []
    for symbol in complete["symbols"]:
        folder = source / "streams" / symbol
        receipt = json.loads((folder / "completion.json").read_text())
        assert receipt["identity_hash"] == complete["identity_hash"]
        for name, sha in receipt["files"].items(): assert digest(folder / name) == sha
        trades.append(pd.read_csv(folder / "trades.csv.gz"))
        controls.append(pd.read_csv(folder / "controls.csv.gz"))
        evidence.append(pd.read_csv(folder / "evidence.csv.gz"))
        coverage.extend(receipt["coverage"])
    t, c, e = [pd.concat(parts, ignore_index=True) for parts in (trades, controls, evidence)]
    assert not c.event_key.duplicated().any()
    t = t.merge(c, on="event_key", how="left", validate="many_to_one")
    assert t.reason.notna().all()
    t["period"] = assign_period(t, split)
    for col in ("entry_time", "exit_time", "signal_bar_open"):
        t[col] = pd.to_datetime(t[col], utc=True)
    out = source / "statistics"; out.mkdir(exist_ok=True)
    audit_baselines(identity, t).to_csv(out / "baseline_parity.csv", index=False)
    t.to_csv(out / "trades_with_controls.csv.gz", index=False, compression={"method":"gzip", "mtime":0})
    pd.DataFrame(coverage).to_csv(out / "coverage.csv", index=False)
    rows = []
    for (scope, tf, length), g in t.groupby(["scope", "timeframe", "length"]):
        for period in ("full", "earlier", "later", "cross_split"):
            q = g if period == "full" else g.loc[g.period.eq(period)]
            for direction, side in (("both", 0), ("long", 1), ("short", -1)):
                z = q if side == 0 else q.loc[q.side.eq(side)]
                rows.append({"scope":scope, "timeframe":tf, "length":int(length), "period":period,
                             "direction":direction, **metrics(z), **paired_controls(z, split, period)})
    summary = pd.DataFrame(rows); summary.to_csv(out / "summary.csv", index=False)
    selected = select_lengths(summary)
    (out / "selection.json").write_text(json.dumps({"selected":selected, "selection":cfg["selection"], "replay_identity":identity["identity_hash"]}, indent=2)+"\n")
    comparisons, attr = [], []
    for tf, length in selected.items():
        for scope in ("actual", "common"):
            for period in ("earlier", "later"):
                q = t.loc[t.timeframe.eq(tf) & t.scope.eq(scope) & t.period.eq(period) & ~t.censored]
                a, b = [q.loc[q.length.eq(n)] for n in (0, length)]
                removed = a.loc[~a.event_key.isin(b.event_key)]
                added = b.loc[~b.event_key.isin(a.event_key)]
                common = a.merge(b, on="event_key", suffixes=("_a", "_b"))
                for name in ("entry_price", "initial_stop", "exit_price", "net_r", "net_return"):
                    np.testing.assert_allclose(common[name+"_a"], common[name+"_b"], equal_nan=True, rtol=1e-9, atol=1e-9)
                np.testing.assert_allclose(b.net_r.sum()-a.net_r.sum(), added.net_r.sum()-removed.net_r.sum(), atol=1e-8)
                tail = a.loc[a.net_r >= 10]
                keep = int(tail.event_key.isin(b.event_key).sum())
                item = {"timeframe":tf,"scope":scope,"period":period,"selected_length":length,
                        "baseline_closed":len(a),"selected_closed":len(b),"baseline_net_r":a.net_r.sum(),"selected_net_r":b.net_r.sum(),
                        "baseline_10r":len(tail),"retained_10r":keep,"retention_10r":keep/len(tail) if len(tail) else 1.}
                for field in ("net_r", "net_return"):
                    item.update({field+"_"+k:v for k,v in monthly_delta(a,b,period,cfg,field).items()})
                comparisons.append(item)
                attr.append({"timeframe":tf,"scope":scope,"period":period,"length":length,
                             "removed":len(removed),"removed_losses":int(removed.net_r.lt(0).sum()),"removed_winners":int(removed.net_r.gt(0).sum()),
                             "saved_loss_r":-removed.loc[removed.net_r.lt(0),"net_r"].sum(),"lost_profit_r":removed.loc[removed.net_r.gt(0),"net_r"].sum(),
                             "added":len(added),"added_net_r":added.net_r.sum(),"net_delta_r":b.net_r.sum()-a.net_r.sum()})
    comp = pd.DataFrame(comparisons)
    primary = comp.loc[comp.scope.eq("actual") & comp.period.eq("later")].copy()
    primary["holm_p"] = holm(primary.net_r_p.to_numpy())
    for i, row in primary.iterrows():
        common = comp.loc[comp.timeframe.eq(row.timeframe)&comp.scope.eq("common")&comp.period.eq("later")].iloc[0]
        passed = (row.selected_length != 0 and row.selected_net_r > 0 and row.net_r_delta > 0 and row.net_return_delta > 0
                  and row.net_r_low95 > 0 and row.holm_p < .01 and row.retention_10r >= .85
                  and common.net_r_delta > 0 and common.net_return_delta > 0)
        primary.loc[i,"verdict"] = "research_pass" if passed else "baseline_selected" if row.selected_length == 0 else "not_accepted"
    comp.to_csv(out/"selected_comparisons.csv",index=False)
    primary.to_csv(out/"verdict.csv",index=False)
    pd.DataFrame(attr).to_csv(out/"attribution.csv",index=False)
    counts=[]
    for (tf,side),g in e.groupby(["timeframe","side"]):
        for length in cfg["lengths"]:
            counts.append({"timeframe":tf,"side":side,"length":length,"candidates":len(g),
                           "unknown":int(g[f"sma_{length}"].isna().sum()),"pass":int(g[f"pass_{length}"].sum()),
                           "known_reject":int((g[f"sma_{length}"].notna()&~g[f"pass_{length}"]).sum()),
                           "common_ready":int(g.common_ready.sum())})
    pd.DataFrame(counts).to_csv(out/"candidate_counts.csv",index=False)
    assetrows=[]
    for (scope,tf,symbol,length),g in t.groupby(["scope","timeframe","symbol","length"]):
        for period in ("earlier","later","full"):
            z=g if period=="full" else g.loc[g.period.eq(period)]
            assetrows.append({"scope":scope,"timeframe":tf,"symbol":symbol,"length":length,"period":period,**metrics(z)})
    pd.DataFrame(assetrows).to_csv(out/"per_asset.csv",index=False)
    # Full/late maxima are explicitly exploratory; selection remains unchanged.
    ranks = summary.loc[summary.scope.eq("actual") & summary.direction.eq("both")].copy()
    ranks["rank_net_r"] = ranks.groupby(["timeframe","period"]).net_r.rank(method="min",ascending=False)
    ranks.to_csv(out/"parameter_ranks.csv",index=False)
    preview = summary.loc[summary.scope.eq("actual") & summary.direction.eq("both") & summary.period.isin(["earlier","later"])].copy()
    preview=preview.loc[[n in (0, selected[tf]) for n,tf in zip(preview.length,preview.timeframe)]]
    report=f'''# V9 上级SMA方向过滤：固定29币参数对照

本轮只比较入场过滤，所有交易来自完整双向串行回放；不是从原账本删单。开发期选值：{selected}。验证结果见下表，不把全历史最高值当成未来最优。

## 数据与规则

- Binance USDT永续既有5m归档，29币，ARY排除、PEPE=1000PEPE；{cfg['start']}至{cfg['end']}，{cfg['split']}时间切分。共87个币种周期流，原始V9而非570笔联合样本。
- 15m→1h、1h→4h、4h→日线；20/30/50/60/90/120/150/200以及无过滤。开发阶段仅最大总净R选每周期一个长度，多空共用；跨切点交易不进入开发评分。
- 比较信号收盘价与本根开盘时已知的最近完整上级SMA；完整连续5m组成上级K，缺失使窗口失效。对应Pine `SMA[1] + lookahead_on`，不是提前看到正在形成的高周期均线。[TradingView官方口径](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)
- actual按各长度实际可用性，common把所有臂共同限制在SMA200已就绪时钟，用于排除长均线因新币预热而少交易的影响。未知与方向拒绝单独计数。
- 原始反向信号仍退出；次根开盘、五根极值加0.2ATR且至少2ATR、收盘2R启动4ATR追踪、往返0.2%均不变。R是初始价格风险单位；无共享资金账户模型。

## 已选参数与基线

{markdown_table(preview, ['timeframe','period','length','closed','win_rate','net_r','mean_net_bp','pf','event_drawdown_r','realized_10r','random_pairs','random_mean_r','random_excess_r'])}

胜率为0–1比例，length=0为不加过滤。随机列使用同币、同方向、同月、同时间段、同ATR/价格桶配对；与全部交易不是同一分母。跨切点/删失控制不冒充已知收益。

## 后段验证

{markdown_table(primary, ['timeframe','selected_length','baseline_closed','selected_closed','baseline_net_r','selected_net_r','net_r_delta','net_r_low95','net_r_high95','holm_p','retention_10r','verdict'])}

按共同自然月聚合后做4000次区块bootstrap，符号置换对三个已选周期作Holm调整。所选门必须同时改善后段总净R与名义收益、达到不确定性门槛、保留至少85%原已实现10R赢家，且共同预热对照不翻转改善方向。否则仅研究，不能替换默认。统计不证明市场因果。

## 完整数据与复现

所有长度/周期/多空/时间段及随机对照在 `run_v1/statistics/summary.csv`；逐币 `per_asset.csv`；全长度排名 `parameter_ranks.csv`；移除亏单/误删赢家/新增重入 `attribution.csv`；未知预热 `candidate_counts.csv`；完整带控制逐笔 `trades_with_controls.csv.gz`。这些是仓内相对路径，根目录为 `{EXP}`。

```bash
cd /Users/zhangzc/fable-trading
.venv/bin/python -m pytest -q tests/evaluation/test_spike_v9_htf_sma.py tests/evaluation/test_spike_v9_htf_sma_report.py tests/evaluation/test_spike_v9_full_replay.py tests/evaluation/test_spike_v9.py
.venv/bin/python -m yoyo.evaluation.spike_v9_htf_sma_study --workers 3
.venv/bin/python -m yoyo.evaluation.spike_v9_htf_sma_report
```

原回放代码提交 `{identity['source_commit']}`，身份 `{identity['identity_hash']}`；每流源/输出SHA和完成收据均核对。全部29币×三个周期与原V9 adapter逐笔核对（baseline_parity.csv）；全部流关闭新门的准入mask与原adapter一致。

## 风险与诚实声明

- 后段已经被历史研究看过，不是新的盲样本外；只承诺此次参数按前段选定。八长度网格的历史最高值有选择偏差。
- 4h／日线长均线可能拒绝新币，common对照与实际覆盖必须一起看；不以少做交易自动认定方向判断变好。
- 原V9图表周期聚合沿用已有partial桶语义，上级新SMA则严格完整；coverage列明partial和缺口。本轮不声称与TV完整逐事件parity；Pine收盘参考和Python次根开盘成交有区别。
- 事件累计R回撤不是账户回撤；没有资金费率、真实滑点、同币跨周期相关仓位或资金容量模型。成本未为改善结果而更改。
- 信号数量、交易数量、已平仓数量分别统计；未平仓与缺口删失不计胜负。AUC、top-decile与单特征分类准确率不适用，无训练模型。
- 原始失败试验和旧版源码保留。没有自动promote、通知或真金操作；本轮不生成HTML。

下一步：只对通过的参数考虑独立小版本TV展示；未通过则保留原版并报告失败，不改验证期阈值继续追分。
'''
    Path("analysis/p1_spike_v9_htf_sma_20260920.md").write_text(report)
    receipt={"source_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),
             "report_builder_sha256":digest(Path(__file__)),"replay_identity":identity['identity_hash'],
             "selected":selected,"trades_rows":len(t),"unique_executed_events":t.event_key.nunique(),"v9_candidates":len(e),
             "files":{p.name:digest(p) for p in out.iterdir() if p.is_file() and p.name!="receipt.json"}}
    (out/"receipt.json").write_text(json.dumps(receipt,indent=2)+"\n")
    print(primary.to_string(index=False))


if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("--source",type=Path,default=EXP/"run_v1")
    run(p.parse_args().source)
