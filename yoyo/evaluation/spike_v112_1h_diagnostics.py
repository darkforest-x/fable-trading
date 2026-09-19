"""Read-only 1h diagnostics of the already verified V11.2 box-entry ledger.

No signal, parameter, exit or data fetch changes. All MFE/exit cuts are future
outcome labels used only for explanation. Temporal cuts purge crossing exits;
their rows remain a separate cohort. Source-mixture decomposition is arithmetic,
not causal evidence. Parent V9 comparisons condition on a later breakout and
cannot be used as an earlier entry strategy.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v9_full_report import block_statistics
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-v112-1h-diagnostics-20260919-v1")
SOURCE = Path("experiments/active/exp-spike-v112-execution-20260919-v1")
STATS = SOURCE / "statistics/run_v2"
BOOK = Path("experiments/active/exp-spike-v11-box-joint-20260918-v1/statistics/trade_book/逐笔明细_突破spike.csv")
SPLIT = pd.Timestamp("2025-09-10", tz="UTC")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def paired(q, purge_early=False):
    ok = q.matched.copy()
    # The same early-control purge applies inside every segment, not just totals.
    if purge_early:
        ok &= q.control_exit_time.lt(SPLIT)
    return q.loc[ok]


def metrics(q, purge_early=False):
    win, loss = q.loc[q.net_r.gt(0)], q.loc[q.net_r.lt(0)]
    mr, ml = win.net_r.mean(), -loss.net_r.mean()
    bpw, bpl = win.net_bp.mean(), -loss.net_bp.mean()
    c = paired(q, purge_early)
    excess_r, excess_bp = c.net_r - c.control_net_r, c.net_bp - c.control_net_return * 1e4
    # Sign-flips here are descriptive existing-ledger evidence, not a new gate.
    block = block_statistics(excess_r, c.month)
    return {"n": len(q), "symbols": q.symbol.nunique(), "wins": len(win), "losses": len(loss),
            "win_pct": q.net_r.gt(0).mean() * 100, "mean_r": q.net_r.mean(), "median_r": q.net_r.median(),
            "sum_r": q.net_r.sum(), "gross_r": q.gross_r.mean(), "mean_bp": q.net_bp.mean(),
            "median_bp": q.net_bp.median(), "sum_bp": q.net_bp.sum(), "gross_bp": q.gross_bp.mean(),
            "avg_win_r": mr, "avg_loss_r": ml, "payoff_r": mr / ml if ml > 0 else np.nan,
            "breakeven_win_pct_r": ml / (mr + ml) * 100 if mr + ml > 0 else np.nan,
            "pf_r": win.net_r.sum() / -loss.net_r.sum() if len(loss) else np.nan,
            "avg_win_bp": bpw, "avg_loss_bp": bpl, "payoff_bp": bpw / bpl if bpl > 0 else np.nan,
            "pf_bp": win.net_bp.sum() / -loss.net_bp.sum() if len(loss) else np.nan,
            "mean_hold_h": q.hold_h.mean(), "median_hold_h": q.hold_h.median(),
            "median_risk_pct": q.initial_risk_frac.median() * 100,
            "median_delay": q.bars_after_v9.median(), "mfe_ge1": int(q.mfe_r.ge(1).sum()),
            "mfe_ge2": int(q.mfe_r.ge(2).sum()), "loss_mfe_lt1": int(loss.mfe_r.lt(1).sum()),
            "loss_mfe_ge1": int(loss.mfe_r.ge(1).sum()), "loss_mfe_ge2": int(loss.mfe_r.ge(2).sum()),
            "loss_mfe_ge3": int(loss.mfe_r.ge(3).sum()), "loss_mfe_ge5": int(loss.mfe_r.ge(5).sum()),
            "matched_n": len(c), "matched_pct": len(c) / len(q) * 100 if len(q) else 0,
            "paired_actual_r": c.net_r.mean(), "random_r": c.control_net_r.mean(), "excess_r": excess_r.mean(),
            "paired_actual_bp": c.net_bp.mean(), "random_bp": c.control_net_return.mean() * 1e4,
            "excess_bp": excess_bp.mean(), **{f"excess_{k}": v for k, v in block.items()}}


def cuts(table, column):
    rows = []
    for period in ("full", "earlier", "later", "cross_split"):
        part = table if period == "full" else table.loc[table.cohort.eq(period)]
        for value, q in part.groupby(column, observed=True, sort=True):
            rows.append({"period": period, "group": value, **metrics(q, period == "earlier")})
    return pd.DataFrame(rows)


def decomposition(table):
    rows = []
    for column in ("net_r", "net_bp", "won"):
        early = table.loc[table.cohort.eq("earlier")].groupby("source")[column].agg(["size", "mean"])
        late = table.loc[table.cohort.eq("later")].groupby("source")[column].agg(["size", "mean"])
        assert set(early.index) == set(late.index)
        early["weight"], late["weight"] = early["size"] / early["size"].sum(), late["size"] / late["size"].sum()
        within, mixture = 0., 0.
        for source in early.index:
            a, b = early.loc[source], late.loc[source]
            w = .5 * (a.weight + b.weight) * (b["mean"] - a["mean"])
            m = .5 * (a["mean"] + b["mean"]) * (b.weight - a.weight)
            rows.append({"metric": column, "source": source, "earlier_n": a["size"], "later_n": b["size"],
                         "earlier_weight": a.weight, "later_weight": b.weight, "earlier_mean": a["mean"],
                         "later_mean": b["mean"], "within_effect": w, "mixture_effect": m})
            within, mixture = within + w, mixture + m
        difference = table.loc[table.cohort.eq("later"), column].mean() - table.loc[table.cohort.eq("earlier"), column].mean()
        assert np.isclose(difference, within + mixture)
        rows.append({"metric": column, "source": "ALL", "within_effect": within, "mixture_effect": mixture,
                     "total_difference": difference})
    return pd.DataFrame(rows)


def plot_summary(monthly, outcomes, path):
    """Static MD companion: matched monthly means and all-trade outcome counts.

    Two panels, single blue root plus neutrals, direct count labels. Monthly
    strategy/control bars use the same matched denominator. MFE is explicitly
    retrospective; no cumulative account-equity plot is implied.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties, fontManager

    font = "/System/Library/Fonts/STHeiti Light.ttc"
    fontManager.addfont(font)
    plt.rcParams.update({"font.family": FontProperties(fname=font).get_name(), "axes.unicode_minus": False,
                         "font.size": 12, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(2, 1, figsize=(14, 9.5), gridspec_kw={"height_ratios": [1.2, 1]})
    blue, grey, ink = "#426B9B", "#C4C9CF", "#333B44"
    fig.patch.set_facecolor("#FFFFFF")
    x = np.arange(len(monthly)); ax = axes[0]
    ax.bar(x, monthly.paired_actual_r, color=blue, width=.62, label="策略：配对成交")
    ax.plot(x, monthly.random_r, color=ink, linestyle="--", marker="o", markersize=4, label="同币同月同波动随机入场")
    ax.axhline(0, color=ink, linewidth=.9)
    ax.set_xticks(x); ax.set_xticklabels(monthly.month, rotation=45, ha="right", fontsize=10)
    ax.set_ylabel("每笔平均净 R")
    ax.set_title("1h 月度净收益与匹配随机对照", loc="left", fontsize=17, pad=14)
    ax.legend(frameon=False, loc="upper left", fontsize=11)
    ax.grid(axis="y", color="#E6E8EB", linewidth=.7); ax.set_axisbelow(True)
    ax.set_xlim(-.7, len(monthly)-.3)
    ax = axes[1]
    order = ["<0.5", "0.5-1", "1-2", "2-3", "3-5", ">=5"]
    pivot = outcomes.pivot(index="mfe_bucket", columns="won", values="n").reindex(order).fillna(0)
    loss, wins = pivot[0.], pivot[1.]
    y = np.arange(len(order))
    ax.barh(y, loss, color=grey, label="最终亏损", height=.64)
    ax.barh(y, wins, left=loss, color=blue, label="最终盈利", height=.64)
    for k, (a, b) in enumerate(zip(loss, wins)):
        ax.text(a+b+9, k, f"{int(a)} 亏 / {int(b)} 盈", va="center", fontsize=11, color=ink)
    ax.set_yticks(y); ax.set_yticklabels(["不足 0.5R", "0.5–1R", "1–2R", "2–3R", "3–5R", "至少 5R"])
    ax.invert_yaxis(); ax.set_xlim(0, 875); ax.set_xlabel("交易笔数（全部 2,199 笔已平仓）")
    ax.set_title("持仓期间记录的最大浮盈与最终盈亏", loc="left", fontsize=17, pad=14)
    ax.legend(frameon=False, loc="lower right", fontsize=11)
    ax.grid(axis="x", color="#E6E8EB", linewidth=.7); ax.set_axisbelow(True)
    fig.text(.06, .026, "数据：2024年9月–2026年4月，1h 框内首破新开多仓；往返成本0.2%。月图按信号收盘月归属。", fontsize=11, color=ink)
    fig.text(.06, .008, "最大浮盈属于事后路径，不是入场特征；原引擎不更新退出根的浮盈。图中收益不是账户净值。", fontsize=11, color=ink)
    fig.tight_layout(rect=[.015, .045, .99, 1], h_pad=2.4)
    fig.savefig(path, dpi=160, facecolor=fig.get_facecolor()); plt.close(fig)


def main():
    assert _committed((Path(__file__), EXP / "PROJECT_PLAN.md")), "Commit builder and plan before producing statistics."
    manifest = json.loads((SOURCE / "delivery_manifest.json").read_text())
    sources = [STATS / name for name in ("trades.csv.gz", "summary.csv", "attribution.csv", "tail_retention.csv", "gates.csv")]
    for path in sources:
        assert sha(path) == manifest["files"][str(path)]["sha256"]
    t = pd.read_csv(sources[0]).query("timeframe == '1h'").copy()
    assert not t.duplicated(["arm", "trade_key"]).any()
    for column in ("entry_time", "exit_time", "signal_close", "signal_bar_open", "control_exit_time"):
        t[column] = pd.to_datetime(t[column], utc=True, format="mixed")
    assert t.matched.isin([True, False]).all()
    t["cohort"] = np.where(t.signal_close.ge(SPLIT), "later", np.where(t.exit_time.lt(SPLIT), "earlier", "cross_split"))
    t["hold_h"] = (t.exit_time - t.entry_time).dt.total_seconds() / 3600
    b = t.loc[t.arm.eq("baseline") & t.status.eq("closed")].copy()
    assert len(b) == 2199 and t.loc[t.arm.eq("baseline")].shape[0] == 2201
    assert b.cohort.value_counts().to_dict() == {"later": 1077, "earlier": 1074, "cross_split": 48}
    assert np.allclose(b.gross_bp - b.net_bp, 20.)
    book = pd.read_csv(BOOK).query("周期 == '1h'").copy()
    book["signal_bar_open"] = pd.to_datetime(book["突破+spike K(北京)"]).dt.tz_localize("Asia/Shanghai").dt.tz_convert("UTC")
    book = book.rename(columns={"币种": "symbol", "V9净R": "parent_v9_net_r", "V9最大浮盈R": "parent_v9_mfe",
                                "V9进场价": "parent_v9_entry", "联合净R": "book_joint_r"})
    b = b.merge(book[["symbol", "signal_bar_open", "parent_v9_net_r", "parent_v9_mfe", "parent_v9_entry", "book_joint_r"]],
                on=["symbol", "signal_bar_open"], how="left", validate="one_to_one")
    assert np.allclose(b.net_r, b.book_joint_r, rtol=1e-9, atol=1e-9)
    b["entry_premium_pct"] = (b.entry_price / b.parent_v9_entry - 1) * 100
    b["delay_bucket"] = pd.cut(b.bars_after_v9, [-1, 0, 3, 6, 12, 24, 48, np.inf],
                               labels=["0", "1-3", "4-6", "7-12", "13-24", "25-48", ">48"])
    b["risk_bucket"] = pd.cut(b.initial_risk_frac, [0, .01, .02, .04, .08, np.inf],
                              labels=["0-1%", "1-2%", "2-4%", "4-8%", ">8%"])
    b["mfe_bucket"] = pd.cut(b.mfe_r, [-np.inf, .5, 1, 2, 3, 5, np.inf], right=False,
                             labels=["<0.5", "0.5-1", "1-2", "2-3", "3-5", ">=5"])
    b["won"] = b.net_r.gt(0).astype(float)
    overview = pd.DataFrame([{"period": p, **metrics(b if p == "full" else b.loc[b.cohort.eq(p)], p == "earlier")}
                             for p in ("full", "earlier", "later", "cross_split")])
    assert np.isclose(overview.loc[overview.period.ne("full"), "sum_r"].sum(), b.net_r.sum())
    old = pd.read_csv(STATS / "summary.csv").query("timeframe=='1h' and arm=='baseline'").set_index("period")
    for row in overview.loc[overview.period.ne("cross_split")].itertuples():
        assert row.n == old.loc[row.period, "closed"]
        assert np.isclose(row.mean_r, old.loc[row.period, "mean_net_r"])
        assert np.isclose(row.excess_r, old.loc[row.period, "excess_r"])
    by_month = pd.DataFrame([{"month": m, **metrics(q)} for m, q in b.groupby("month")])
    by_symbol = pd.DataFrame([{"symbol": s, "n": len(q), "net_r_sum": q.net_r.sum(), "net_bp_sum": q.net_bp.sum()}
                             for s, q in b.groupby("symbol")])
    outcomes = b.groupby(["mfe_bucket", "won"], observed=True).size().rename("n").reset_index()
    sensitivity = []
    for col in ("net_r", "net_bp"):
        order = b.sort_values(col, ascending=False)
        for count in (0, 1, int(np.ceil(len(b) * .01))):
            remaining = order.iloc[count:]
            sensitivity.append({"rank_metric": col, "removed_top": count, **metrics(remaining),
                                "removed_sum_r": order.iloc[:count].net_r.sum(), "removed_sum_bp": order.iloc[:count].net_bp.sum()})
    quantiles = b[["net_r", "net_bp", "mfe_r", "hold_h", "initial_risk_frac", "bars_after_v9", "entry_premium_pct"]].quantile(
        [0, .01, .1, .25, .5, .75, .9, .95, .99, 1]).rename_axis("quantile").reset_index()
    parent = {"n": len(b), "parent_available": int(b.parent_v9_net_r.notna().sum()),
              "parent_win_joint_loss": int((b.parent_v9_net_r.gt(0) & b.net_r.lt(0)).sum()),
              "parent_loss_joint_win": int((b.parent_v9_net_r.lt(0) & b.net_r.gt(0)).sum()),
              "joint_entry_higher": int(b.entry_premium_pct.gt(1e-8).sum()),
              "median_entry_premium_pct": b.entry_premium_pct.median(), "median_delay": b.bars_after_v9.median(),
              "parent_mean_r_hindsight_selected": b.parent_v9_net_r.mean(), "joint_mean_r": b.net_r.mean(),
              "symbols_positive_total_r": int(by_symbol.net_r_sum.gt(0).sum()), "symbols_total": len(by_symbol)}
    out = EXP / "statistics"; out.mkdir(exist_ok=True)
    tables = {"overview": overview, "monthly": by_month, "by_source": cuts(b, "source"), "by_delay": cuts(b, "delay_bucket"),
              "by_risk": cuts(b, "risk_bucket"), "by_mfe": cuts(b, "mfe_bucket"), "by_exit": cuts(b, "exit_reason"),
              "mfe_outcomes": outcomes, "source_decomposition": decomposition(b), "tail_sensitivity": pd.DataFrame(sensitivity),
              "quantiles": quantiles, "by_symbol": by_symbol, "baseline_1h_ledger": b}
    for name in ("summary", "attribution", "tail_retention", "gates"):
        tables[f"variants_{name}"] = pd.read_csv(STATS / f"{name}.csv").query("timeframe == '1h'")
    for name, table in tables.items():
        table.to_csv(out / f"{name}.csv", index=False)
    plot_summary(by_month, outcomes, out / "one_hour_summary.png")
    (out / "parent_comparison.json").write_text(json.dumps(parent, indent=2) + "\n")
    receipt = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
               "script_sha256": sha(Path(__file__)), "plan_sha256": sha(EXP / "PROJECT_PLAN.md"),
               "inputs": {str(p): sha(p) for p in [*sources, BOOK, SOURCE / "delivery_manifest.json"]},
               "source_run_identity": manifest["run_identity"], "baseline_parity": True,
               "command": ".venv/bin/python -W ignore -m yoyo.evaluation.spike_v112_1h_diagnostics",
               "files": {str(p): sha(p) for p in out.iterdir() if p.name != "receipt.json"}}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(overview[["period", "n", "wins", "win_pct", "mean_r", "mean_bp", "sum_r", "pf_r", "avg_win_r", "avg_loss_r", "breakeven_win_pct_r"]].to_string(index=False))
    print(json.dumps(parent, indent=2))


if __name__ == "__main__":
    main()
