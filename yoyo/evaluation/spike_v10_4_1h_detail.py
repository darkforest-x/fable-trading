"""Descriptive breakdown of the V10.4 1h joint-long ledger (owner asked for detail).

Source: owner 2026-09-18 「针对1h级别的回测数据结果做详细分析」 after the
six-timeframe report. Reads only the committed run's ledgers
(`statistics/run_v1/trades.csv.gz`, `controls.csv.gz`) plus BTCUSDT 5m closes
from the same archive for a month-by-month market column. Nothing is
re-simulated, no rule or parameter is changed, and every cut below is a
description of trades that already happened. Cuts that were not in the
pre-registered plan are labelled post-hoc in the report and are not evidence.

Holdout: the ledgers end at 2026-05-01; nothing here reads later data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation.spike_v9_full_report import block_statistics

BUCKETS = [(-np.inf, -1.0, "≤ −1R（整笔止损）"), (-1.0, 0.0, "−1R ~ 0"), (0.0, 1.0, "0 ~ 1R"),
           (1.0, 3.0, "1 ~ 3R"), (3.0, 10.0, "3 ~ 10R"), (10.0, np.inf, "≥ 10R")]


def load(stats: Path, timeframe: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    trades = pd.read_csv(stats / "trades.csv.gz")
    controls = pd.read_csv(stats / "controls.csv.gz")
    for column in ("signal_bar_open", "entry_time", "exit_time", "signal_close"):
        trades[column] = pd.to_datetime(trades[column], utc=True, format="mixed")
    controls["control_exit_time"] = pd.to_datetime(controls.control_exit_time, utc=True, format="mixed")
    trades = trades.loc[(trades.timeframe == timeframe) & ~trades.censored.astype(bool)].copy()
    trades["hold_bars"] = trades.exit_i - trades.entry_i
    trades["win"] = trades.net_r > 0
    trades = trades.merge(controls[["trade_key", "matched", "control_net_r", "control_exit_time"]],
                          on="trade_key", how="left", validate="one_to_one")
    return trades.loc[trades.arm == "joint"].copy(), trades.loc[trades.arm == "v9_long"].copy(), controls


def core(part: pd.DataFrame) -> dict:
    r = part.net_r
    wins, losses = r[r > 0], r[r <= 0]
    pair = part.loc[part.matched.fillna(False).astype(bool)] if "matched" in part else part.iloc[0:0]
    delta = pair.net_r - pair.control_net_r
    stats = block_statistics(delta, pair.month) if len(pair) > 1 else {"p_month_signflip": np.nan}
    return {"trades": len(part), "win_rate": r.gt(0).mean() if len(r) else np.nan,
            "mean_r": r.mean() if len(r) else np.nan, "total_r": r.sum(),
            "avg_win_r": wins.mean() if len(wins) else np.nan, "avg_loss_r": losses.mean() if len(losses) else np.nan,
            "pf": wins.sum() / -losses.sum() if losses.sum() < 0 else np.nan,
            "random_mean_r": pair.control_net_r.mean() if len(pair) else np.nan,
            "excess_r": delta.mean() if len(pair) else np.nan, "p": stats["p_month_signflip"]}


def btc_monthly() -> pd.Series:
    base = study.load_5m(study.series_files()["BTCUSDT"], pd.Timestamp("2024-08-01T00:00:00Z"))
    close = base.close.resample("MS").last()
    return (close.pct_change() * 100).rename("btc_month_pct")


def streaks(r: pd.Series) -> int:
    longest = run = 0
    for value in r:
        run = run + 1 if value <= 0 else 0
        longest = max(longest, run)
    return longest


def concurrency(part: pd.DataFrame) -> pd.Series:
    events = pd.concat([pd.Series(1, index=part.entry_time), pd.Series(-1, index=part.exit_time)]).sort_index(kind="stable")
    return events.groupby(level=0).sum().cumsum()


def tables(joint: pd.DataFrame, v9: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    out["headline"] = pd.DataFrame([{"arm": "joint", **core(joint)}, {"arm": "v9_long", **core(v9)}])
    out["period"] = pd.DataFrame([{"period": p, **core(joint.loc[joint.period == p])} for p in ("earlier", "later")])
    monthly = pd.DataFrame([{"month": m, **core(g)} for m, g in joint.groupby("month")])
    btc = btc_monthly()
    monthly["btc_month_pct"] = [btc.get(pd.Timestamp(m + "-01", tz="UTC"), np.nan) for m in monthly.month]
    out["monthly"] = monthly
    bucket_rows = []
    for lo, hi, label in BUCKETS:
        part = joint.loc[(joint.net_r > lo) & (joint.net_r <= hi)]
        bucket_rows.append({"bucket": label, "trades": len(part), "share": len(part) / len(joint),
                            "total_r": part.net_r.sum(), "median_hold_bars": part.hold_bars.median()})
    out["distribution"] = pd.DataFrame(bucket_rows)
    exit_rows = []
    for reason, part in joint.groupby("exit_reason"):
        exit_rows.append({"exit_reason": reason, "trades": len(part), "share": len(part) / len(joint),
                          "mean_r": part.net_r.mean(), "total_r": part.net_r.sum(),
                          "median_hold_bars": part.hold_bars.median(), "median_mfe_r": part.mfe_r.median()})
    out["exits"] = pd.DataFrame(exit_rows)
    stopped = joint.loc[joint.exit_reason.str.startswith("initial_stop")]
    out["giveback"] = pd.DataFrame([{"reached": f"≥{k}R", "all_trades": int(joint.mfe_r.ge(k).sum()),
                                     "share_all": joint.mfe_r.ge(k).mean(),
                                     "then_initial_stop": int(stopped.mfe_r.ge(k).sum()),
                                     "share_of_initial_stops": stopped.mfe_r.ge(k).mean()}
                                    for k in (0.5, 1.0, 1.5, 2.0, 3.0)])
    out["order"] = pd.DataFrame([{"order": o, **core(g)} for o, g in joint.groupby("joint_order")])
    out["order_period"] = pd.DataFrame([{"order": o, "period": p, **core(g)}
                                        for (o, p), g in joint.groupby(["joint_order", "period"])])
    out["line_source"] = pd.DataFrame([{"line_source": s, **core(g)} for s, g in joint.groupby("line_source")])
    risk = joint.assign(risk_bucket=pd.qcut(joint.initial_risk_frac, 4, labels=["最窄25%", "25-50%", "50-75%", "最宽25%"]))
    out["risk"] = pd.DataFrame([{"stop_distance": str(b), "range_pct": f"{g.initial_risk_frac.min()*100:.1f}–{g.initial_risk_frac.max()*100:.1f}%",
                                 "cost_r_median": (0.002 / g.initial_risk_frac).median(), **core(g)}
                                for b, g in risk.groupby("risk_bucket", observed=True)])
    hold = joint.assign(hold=pd.cut(joint.hold_bars, [0, 3, 12, 48, 168, 10_000],
                                    labels=["≤3h", "4–12h", "13–48h", "2–7天", ">7天"], include_lowest=True))
    out["holding"] = pd.DataFrame([{"holding": str(b), **core(g)} for b, g in hold.groupby("hold", observed=True)])
    by_symbol = joint.groupby("symbol").net_r.agg(trades="size", total_r="sum", mean_r="mean").sort_values("total_r")
    out["symbols_top"] = by_symbol.tail(10).iloc[::-1].reset_index()
    out["symbols_bottom"] = by_symbol.head(10).reset_index()
    out["symbols_summary"] = pd.DataFrame([{"symbols": len(by_symbol), "profitable": int((by_symbol.total_r > 0).sum()),
                                            "one_trade_only": int((by_symbol.trades == 1).sum()),
                                            "top10_total_r": by_symbol.total_r.nlargest(10).sum(),
                                            "rest_total_r": by_symbol.total_r.sum() - by_symbol.total_r.nlargest(10).sum()}])
    ordered = joint.sort_values("exit_time")
    open_count = concurrency(joint)
    out["path"] = pd.DataFrame([{"longest_losing_streak": streaks(ordered.net_r),
                                 "max_drawdown_r": float((np.maximum.accumulate(np.r_[0, ordered.net_r.cumsum()])[1:]
                                                          - ordered.net_r.cumsum()).max()),
                                 "max_concurrent_positions": int(open_count.max()),
                                 "median_concurrent_positions": float(open_count.median()),
                                 "median_hold_bars": joint.hold_bars.median(),
                                 "median_stop_pct": joint.initial_risk_frac.median() * 100,
                                 "median_cost_r": (0.002 / joint.initial_risk_frac).median(),
                                 "gross_mean_r": joint.gross_r.mean(), "net_mean_r": joint.net_r.mean()}])
    trimmed = joint.loc[~joint.net_r.isin(joint.net_r.nlargest(max(1, len(joint) // 100)))]
    out["tail"] = pd.DataFrame([{"cut": "全部", **core(joint)}, {"cut": "去掉最好的 1%（15 笔）", **core(trimmed)},
                                {"cut": "去掉 ≥10R 的单", **core(joint.loc[joint.net_r < 10])}])
    return out


def figures(joint: pd.DataFrame, v9: pd.DataFrame, monthly: pd.DataFrame, out: Path) -> None:
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))
    ax = axes[0, 0]
    j = joint.sort_values("exit_time")
    ax.plot(j.exit_time, j.net_r.cumsum(), color="#AD7B29", linewidth=1.6, label=f"突破+spike（{len(j)} 笔）")
    c = joint.loc[joint.matched.fillna(False).astype(bool)].sort_values("control_exit_time")
    ax.plot(c.control_exit_time, c.control_net_r.cumsum(), color="#8a8f98", linewidth=1.2, label="匹配随机做多（同币同月同波动）")
    v = v9.sort_values("exit_time")
    ax.plot(v.exit_time, v.net_r.cumsum() * len(j) / len(v), color="#4679C9", linewidth=1.0, linestyle="--",
            label=f"全部 V9 多头（按笔数缩放到同规模）")
    ax.axvline(study.SPLIT, color="#222", linewidth=0.8, linestyle=":")
    ax.axhline(0, color="#bbb", linewidth=0.6)
    ax.set_title("累计净 R（按出场时间）", fontsize=10)
    ax.legend(fontsize=8)
    ax = axes[0, 1]
    x = np.arange(len(monthly))
    ax.bar(x - 0.2, monthly.mean_r, width=0.4, color="#AD7B29", label="突破+spike 每笔 R")
    ax.bar(x + 0.2, monthly.random_mean_r, width=0.4, color="#8a8f98", label="随机做多 每笔 R")
    ax.set_xticks(x)
    ax.set_xticklabels([m[2:] for m in monthly.month], rotation=90, fontsize=7)
    ax.axhline(0, color="#bbb", linewidth=0.6)
    ax.set_title("逐月每笔净 R：信号 vs 随机（随机≈当月做多环境）", fontsize=10)
    ax.legend(fontsize=8)
    ax = axes[1, 0]
    clipped = joint.net_r.clip(upper=10)
    ax.hist(clipped, bins=np.arange(-1.6, 10.4, 0.25), color="#AD7B29")
    ax.set_title("每笔净 R 分布（>10R 堆在最右一格）", fontsize=10)
    ax.set_xlabel("净 R", fontsize=8)
    ax = axes[1, 1]
    stopped = joint.exit_reason.str.startswith("initial_stop")
    ax.scatter(joint.loc[~stopped, "mfe_r"].clip(upper=20), joint.loc[~stopped, "net_r"].clip(upper=20), s=6,
               color="#008F82", alpha=0.5, label="追踪/反向出场")
    ax.scatter(joint.loc[stopped, "mfe_r"].clip(upper=20), joint.loc[stopped, "net_r"], s=6, color="#D34B66",
               alpha=0.5, label="初始止损出场")
    ax.axvline(2, color="#222", linewidth=0.8, linestyle=":")
    ax.set_xlabel("持仓期间最大浮盈 MFE（R，>20 截断）", fontsize=8)
    ax.set_ylabel("最终净 R", fontsize=8)
    ax.set_title("最大浮盈 vs 最终结果（虚线=2R，追踪启动线）", fontsize=10)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=110)


def main(stats: Path, out: Path, timeframe: str) -> None:
    joint, v9, _ = load(stats, timeframe)
    out.mkdir(parents=True, exist_ok=True)
    result = tables(joint, v9)
    for name, frame in result.items():
        frame.to_csv(out / f"{name}.csv", index=False)
    figures(joint, v9, result["monthly"], out / "overview.png")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    for name, frame in result.items():
        print(f"\n== {name} ==")
        print(frame.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    (out / "receipt.json").write_text(json.dumps({"timeframe": timeframe, "joint_trades": len(joint),
                                                  "v9_long_trades": len(v9), "source": str(stats)}, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeframe", default="1h")
    args = parser.parse_args()
    main(args.stats, args.out, args.timeframe)
