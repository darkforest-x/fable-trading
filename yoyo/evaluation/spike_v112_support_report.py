"""Audit a complete support-gate replay against the frozen original V11 box ledger.

Sources are committed runner receipts, both independent serial ledgers, and the
original published box_any ledger. Outcomes are used only for retrospective
attribution, never to select entries. UTC month bootstrap and matched-random
sign-flip statistics reuse the original reporting implementations.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation.spike_v10_4_increment_report import PARITY, arm_metrics, cat
from yoyo.evaluation.spike_v11_report import diff

EXP = Path("experiments/active/exp-spike-v112-support-20260919-v1")
OLD = Path("experiments/active/exp-spike-v11-box-joint-20260918-v1")
REPORT = Path("analysis/p1_spike_v112_support_20260919.md")
ARMS = ("box_any", "box_support")
PERIODS = ("full", "earlier", "later")


def dates(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["signal_bar_open"] = pd.to_datetime(frame.signal_bar_open, utc=True, format="mixed")
    frame["signal_close"] = frame.signal_bar_open + pd.to_timedelta(frame.timeframe.map({"15m": 15, "1h": 60}), unit="m")
    frame["period"] = np.where(frame.signal_close < study.SPLIT, "earlier", "later")
    frame["month"] = frame.signal_close.dt.strftime("%Y-%m")
    return frame


def compare(left: pd.DataFrame, right: pd.DataFrame, fields: list[str], name: str, *, same_keys: bool = True) -> dict:
    joined = left[["trade_key", *fields]].merge(right[["trade_key", *fields]], on="trade_key", how="outer",
                                               suffixes=("_a", "_b"), indicator=True, validate="one_to_one")
    both = joined.loc[joined._merge == "both"]
    row = {"comparison": name, "left": len(left), "right": len(right), "shared": len(both),
           "only_left": int((joined._merge == "left_only").sum()), "only_right": int((joined._merge == "right_only").sum())}
    for field in fields:
        a, b = both[f"{field}_a"], both[f"{field}_b"]
        if field.endswith("time"):
            ok = pd.to_datetime(a, utc=True, format="mixed").eq(pd.to_datetime(b, utc=True, format="mixed"))
        elif pd.api.types.is_numeric_dtype(a) and not pd.api.types.is_bool_dtype(a):
            ok = np.isclose(a.to_numpy(float), b.to_numpy(float), rtol=1e-10, atol=1e-10, equal_nan=True)
        else:
            ok = a.fillna("<NA>").astype(str).to_numpy() == b.fillna("<NA>").astype(str).to_numpy()
        row[f"diff_{field}"] = int((~ok).sum())
    row["passed"] = all(value == 0 for key, value in row.items() if key.startswith("diff_"))
    if same_keys:
        row["passed"] &= row["only_left"] == row["only_right"] == 0
    return row


def main(run: Path, out: Path) -> None:
    manifest = json.loads((run / "manifest.json").read_text())
    assert manifest["complete"] and manifest["symbols"] == 638 and not manifest["failures"], manifest
    from yoyo.evaluation.spike_v112_support_study import _validate_completion
    identity = json.loads((run / "identity.json").read_text())
    for symbol, sha in identity["inputs"].items():
        _validate_completion(run / "streams" / symbol, manifest["run_identity"], sha)
    t, s, c, d = (cat(str(run / "streams" / "*" / f"{name}.csv.gz"))
                  for name in ("trades", "statuses", "controls", "decisions"))
    t, s, d = dates(t), dates(s), dates(d)
    for col in ("entry_time", "exit_time"):
        t[col] = pd.to_datetime(t[col], utc=True, format="mixed")
    t["hold_hours"] = (t.exit_time - t.entry_time).dt.total_seconds() / 3600
    t = t.merge(c[["arm", "trade_key", "matched", "control_net_r", "control_net_return"]],
                on=["arm", "trade_key"], how="left", validate="one_to_one")
    keys = ["symbol", "timeframe", "signal_i"]
    assert not d.duplicated(keys).any()
    t = t.drop(columns=["source", "box_entry_i", "bars_after_v9"], errors="ignore")
    t = t.merge(d[[*keys, "source", "box_entry_i", "bars_after_v9", "support_pass", "distance_atr"]],
                on=keys, validate="many_to_one")
    assert t.loc[t.arm == "box_support", "support_pass"].all()
    assert d.support_pass.eq(np.isfinite(d.close) & np.isfinite(d.ropeHigh) & d.close.gt(d.ropeHigh)).all()
    a, b = t.loc[t.arm == "box_any"], t.loc[t.arm == "box_support"]
    old = pd.read_csv(OLD / "statistics/run_v1/trades.csv.gz").query("arm == 'box_any'")
    check_fields = [*PARITY, "matched", "control_net_r", "control_net_return", "status"]
    checks = pd.DataFrame([compare(old, a, check_fields, "original_vs_baseline"),
                           compare(a, b, check_fields, "shared_baseline_vs_support", same_keys=False)])
    assert checks.passed.all(), checks.to_dict("records")
    old_status = cat(str(OLD / "results/run_v1/streams/*/statuses.csv.gz")).query("arm == 'box_any'")
    new_status = s.loc[s.arm == "box_any"]
    status_fields = [*keys, "status"]
    assert set(map(tuple, old_status[status_fields].values)) == set(map(tuple, new_status[status_fields].values))
    rows, conservation, attribution = [], [], []
    for tf in ("15m", "1h"):
        dt = d.loc[d.timeframe == tf]
        for arm in ARMS:
            tt, ss = t.loc[(t.timeframe == tf) & (t.arm == arm)], s.loc[(s.timeframe == tf) & (s.arm == arm)]
            expected = dt if arm == "box_any" else dt.loc[dt.support_pass]
            assert set(map(tuple, ss[keys].values)) == set(map(tuple, expected[keys].values))
            counts = ss.status.value_counts().to_dict()
            assert len(tt) == sum(counts.get(k, 0) for k in ("closed", "censored_boundary", "censored_gap"))
            conservation.append({"timeframe": tf, "arm": arm, "original_candidates": len(dt),
                                 "support_rejections": 0 if arm == "box_any" else int((~dt.support_pass).sum()),
                                 "eligible_candidates": len(ss), **counts})
            for period in PERIODS:
                tp = tt if period == "full" else tt.loc[tt.period == period]
                sp = ss if period == "full" else ss.loc[ss.period == period]
                rows.append({"timeframe": tf, "arm": arm, "period": period,
                             **arm_metrics(tp, sp), "matched_closed": int(((tp.status == "closed") & tp.matched).sum())})
        for period in PERIODS:
            aa = a.loc[(a.timeframe == tf) & (a.status == "closed")]
            bb = b.loc[(b.timeframe == tf) & (b.status == "closed")]
            if period != "full":
                aa, bb = aa.loc[aa.period == period], bb.loc[bb.period == period]
            removed, added = aa.loc[~aa.trade_key.isin(bb.trade_key)], bb.loc[~bb.trade_key.isin(aa.trade_key)]
            delta = bb.net_r.sum() - aa.net_r.sum()
            assert np.isclose(delta, added.net_r.sum() - removed.net_r.sum(), atol=1e-8)
            attribution.append({"timeframe": tf, "period": period, "removed": len(removed),
                "removed_gate_fail": int((~removed.support_pass).sum()), "removed_occupancy": int(removed.support_pass.sum()),
                "removed_losers": int(removed.net_r.lt(0).sum()), "removed_winners": int(removed.net_r.gt(0).sum()),
                "removed_ge10r": int(removed.net_r.ge(10).sum()), "avoided_loss_r": -removed.loc[removed.net_r < 0, "net_r"].sum(),
                "forgone_profit_r": removed.loc[removed.net_r > 0, "net_r"].sum(), "added": len(added),
                "added_net_r": added.net_r.sum(), "delta_total_r": delta})
    table = pd.DataFrame(rows)
    differences = pd.DataFrame([diff(t, tf, *ARMS, period) for tf in ("15m", "1h") for period in PERIODS])
    verdicts = []
    for tf in ("15m", "1h"):
        full = table.query("timeframe == @tf and arm == 'box_support' and period == 'full'").iloc[0]
        later = table.query("timeframe == @tf and arm == 'box_support' and period == 'later'").iloc[0]
        gates = {"full_positive": full.mean_net_r > 0, "later_positive": later.mean_net_r > 0,
                 "random_excess_positive": full.excess_vs_random_r > 0, "p_lt_001": full.p_vs_random < .01}
        verdicts.append({"timeframe": tf, **gates, "passed": all(gates.values()),
                         "p_bonferroni_2": min(1., 2 * full.p_vs_random)})
    closed = t.loc[t.status == "closed"]
    source = closed.groupby(["timeframe", "arm", "source", "period"]).net_r.agg(n="size", mean_net_r="mean", total_net_r="sum").reset_index()
    months = closed.groupby(["timeframe", "arm", "month"]).net_r.agg(n="size", mean_net_r="mean", total_net_r="sum").reset_index()
    tables = {"main_table": table, "differences": differences, "reproduction": checks,
              "conservation": pd.DataFrame(conservation), "attribution": pd.DataFrame(attribution),
              "verdict": pd.DataFrame(verdicts), "by_source": source, "monthly": months}
    out.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        frame.to_csv(out / f"{name}.csv", index=False)
    t.to_csv(out / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    d.to_csv(out / "decisions.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    started = json.loads((run / "evaluation_started.json").read_text())
    summary = {"manifest": manifest, "source_commit": started["source_commit"],
               "baseline_ledger_sha256": study.digest(OLD / "statistics/run_v1/trades.csv.gz"),
               "reporter_sha256": study.digest(Path(__file__)),
               **{key: value.to_dict("records") for key, value in tables.items()}}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
    metric_cols = ["timeframe", "arm", "period", "closed", "win_rate", "mean_gross_r", "mean_net_r", "pf", "random_mean_r", "excess_vs_random_r", "p_vs_random"]
    report = ["# SPIKE V11.2 六均线支撑单变量完整回放", "", "日期：2026-09-19。结论以以下实际结果为准；两周期均未完成实盘验证。", "",
        "## 结果", "", table[metric_cols].to_markdown(index=False, floatfmt=".4f"), "",
        "R 是每笔初始止损风险单位；胜率为小数。净收益已扣原设定 0.2% 往返成本。所有均值只用已平仓交易。", "",
        "## 条件与数据", "", "A=原框内首次任一突破，B=A 且当根收盘严格高于 SMA/EMA 20、60、120 的最高值。先消耗原框首个突破，再过滤，拒绝后不能同框重试。其他动量、方向门没有恢复。两臂独立串行回放，允许过滤后释放仓位。", "",
        "638 个 Binance 永续历史档案，5m 聚合，15m←1h 与 1h←4h；收盘信号区间 2024-09-10 至 2026-05-01（右端不含），2025-09-10 切前后段。原始数据全部允许研究，本次保持旧窗口只为复现。候选和截尾如下。", "",
        tables["conservation"].fillna(0).to_markdown(index=False), "",
        "规则回测没有分类正类标签、训练集或 val AUC，也没有连续打分及 top-decile 组合，因此这些指标不适用；后段样本数见 later 行。零假设对照是同币×同月×同时间段×同 ATR/价格波动桶随机入场、同退出同成本。共同事件共享随机种子；随机对照按交易配对，不构成独立串行资金账户。", "",
        "## 改善幅度与可用门", "", differences.to_markdown(index=False, floatfmt=".4f"), "",
        "B−A 的区间为两臂共同 UTC 月份重抽样 2,000 次（seed=91509），每次按各臂自己的交易数重算均值；描述性，不是盲测。随机超额 p 沿用月块符号置换，不是 B−A 的 p。", "",
        tables["verdict"].to_markdown(index=False, floatfmt=".4f"), "",
        "passed 必须全期与后段净均值均为正、全期随机超额为正且原始 p<0.01；同时列出两个周期的 Bonferroni p 参考。任何研究门通过也不会自动 promote。", "",
        "## 误删与仓位变化", "", tables["attribution"].to_markdown(index=False, floatfmt=".4f"), "",
        "removed_gate_fail 是直接支撑拒绝；removed_occupancy 是虽然通过支撑、但被新交易占仓而消失。forgone_profit_r 是误删盈利总和，avoided_loss_r 是少亏的绝对值。added 是完整重放才出现的交易。逐周期、逐段验证：总净 R 差 = 新增交易净 R − 消失交易净 R；共同交易 14 字段和随机对照均不变。", "",
        "## 复现核对", "", checks.to_markdown(index=False), "",
        f"代码提交 `{started['source_commit']}`；run identity `{manifest['run_identity']}`。638 个回执逐个核对输出 SHA，基线成交和状态集合与原 V11.1 框内回测相同。", "",
        "## 复现命令", "", "在仓库根目录执行；需保留原638份5m档案及旧基线账本（哈希见 summary.json 和 identity.json）。", "", "```bash",
        ".venv/bin/python -m pytest tests/evaluation/test_spike_v112_support.py -q",
        f".venv/bin/python -m yoyo.evaluation.spike_v112_support_study --output {run} --workers 8",
        f".venv/bin/python -m yoyo.evaluation.spike_v112_support_report --run {run} --output {out}",
        f".venv/bin/python scripts/md_to_html.py {REPORT} --out-dir analysis/html", "```", "",
        "第一次运行生成输出；再次运行验证同身份回执后复用。改变任一输入/依赖必须使用新输出目录，不能覆盖旧 run 身份。", "",
        "## 风险与诚实声明", "", "这是已看过数据上的逻辑假设检验，不能称为样本外发现。币池采用后来仍挂牌的合约，存活偏差仍在；跨币与行情相关，638 币不等于638个独立样本。日期聚合、暖机和缺口沿用旧实现以保持一致，并非已做 TradingView 逐根认证。次根开盘撮合，没有额外滑点、资金费、盘口容量或合约精度约束；0.2% 是原成本假设。合计 R、事件回撤及跨币同时持仓不是共享资金账户收益。未平仓/断档截尾单单列，不混进已平仓净收益。没有参数搜索或选择最优周期，Pine、监控与实盘配置未改。", "",
        "## 下一步", "", "先根据本轮全期、后段和误删赢家判断此条件是否值得保留；失败也保留完整记录。参数搜索不是本轮任务。若以后改 TP/SL、成本或生产默认配置，仍需 Owner 决策。", ""]
    REPORT.write_text("\n".join(report))
    print(json.dumps({"report": str(REPORT), "rows": len(t), "checks_passed": bool(checks.passed.all()), "verdict": verdicts}, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=EXP / "results/run_v1")
    parser.add_argument("--output", type=Path, default=EXP / "statistics/run_v1")
    args = parser.parse_args()
    main(args.run, args.output)
