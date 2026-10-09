"""Charts for exp-htf-ma-reversion-20261009-v1 (owner 2026-10-09: 你怎么测的 给我图表我看看).

Reads the committed builder's own line and the local results_v1/trades.csv.gz; times shown in
UTC+8 to match the owner's TradingView. Three figures:

  1_method_one_trade.png   one ETH trade drawn with every rule the backtest applies
  2_eth_six_trades.png     six ETH trades after 2025 (stops, targets, a timeout)
  3_results.png            cumulative net vs matched random entries, and every grid cell's
                           2023-2024 vs 2025+ net R

Run: .venv/bin/python experiments/active/exp-htf-ma-reversion-20261009-v1/plot_charts.py [--out DIR]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from yoyo.evaluation import htf_ma_reversion_v1 as hr  # noqa: E402
from yoyo.evaluation import market_sync_shock as v1  # noqa: E402

EXP = hr.EXP
OWNER = {"minutes": 5, "side": 1, "vix": False, "measure": "pct", "level": 0.05, "stop": "pct3", "target_r": 3}
TZ = "Asia/Shanghai"
UP, DOWN, LINE, TRIG = "#26a69a", "#ef5350", "#9c6ade", "#f39c12"
SIX = ["2025-02-26T18:25:00Z", "2026-02-03T18:00:00Z", "2025-06-20T17:30:00Z",
       "2025-01-19T09:15:00Z", "2025-10-11T20:25:00Z", "2025-10-30T19:55:00Z"]
ONE = "2025-10-10T15:30:00Z"

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "STHeiti", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def owner_cell(t: pd.DataFrame) -> pd.DataFrame:
    m = np.ones(len(t), bool)
    for k, v in OWNER.items():
        m &= (t[k] == v).to_numpy()
    return t.loc[m]


def eth_bars() -> tuple[pd.DataFrame, np.ndarray]:
    raw = v1.read_5m("ETHUSDT", pd.Timestamp("2022-11-01", tz="UTC"), pd.Timestamp("2026-09-23", tz="UTC"))
    fr = hr.chart_frames(raw, 5, 15)
    return fr["chart"], fr["line"]


def candles(ax, bars: pd.DataFrame, width: float = 0.7) -> None:
    x = np.arange(len(bars))
    o, h, l, c = (bars[k].to_numpy() for k in ("open", "high", "low", "close"))
    col = np.where(c >= o, UP, DOWN)
    ax.vlines(x, l, h, color=col, linewidth=0.6, zorder=2)
    ax.bar(x, np.maximum(np.abs(c - o), (h.max() - l.min()) * 0.0008), bottom=np.minimum(o, c), width=width,
           color=col, linewidth=0, zorder=3)


def time_ticks(ax, index: np.ndarray, n: int = 6) -> None:
    pos = np.linspace(0, len(index) - 1, n).astype(int)
    labels = pd.to_datetime(index[pos], unit="ms", utc=True).tz_convert(TZ).strftime("%m-%d %H:%M")
    ax.set_xticks(pos, labels, fontsize=8)


def draw_trade(ax, bars: pd.DataFrame, line: np.ndarray, r: pd.Series, before_h: float, after_h: float,
               detail: bool) -> None:
    """Candles (5m, or 15m when the window is long), the line, trigger, fill, stop/target and exit."""
    t0 = pd.Timestamp(r.time).value // 10**6
    t_exit = t0 + (int(r.bars) - 1) * 300_000
    lo, hi = t0 - int(before_h * 3.6e6), t_exit + int(after_h * 3.6e6)
    sel = (bars.index >= lo) & (bars.index <= hi)
    w, ln = bars.loc[sel], line[sel]
    step = 3 if len(w) > 400 else 1  # long windows: draw 15m candles for legibility
    if step > 1:
        g = np.arange(len(w)) // step
        w = w.groupby(g).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))
        w.index = bars.index[sel][::step][:len(w)]
        ln = ln[::step][:len(w)]
    x = np.arange(len(w))
    candles(ax, w)
    trig = ln * (1 - OWNER["level"])
    ax.step(x, ln, where="mid", color=LINE, lw=1.8, label="V13.1 线 (15m EMA120)", zorder=4)
    ax.step(x, trig, where="mid", color=TRIG, lw=1.2, ls="--", label="离线 5% 触发价", zorder=4)
    if detail:
        ax.step(x, ln * (1 - OWNER["level"] / 2), where="mid", color="#7f8c8d", lw=1, ls=":",
                label="收盘回到这里 = 本次偏离结束", zorder=4)
    xi = int(np.searchsorted(w.index.to_numpy(), t0, side="right") - 1)
    xe = int(np.searchsorted(w.index.to_numpy(), t_exit, side="right") - 1)
    stop, target = r.fill * (1 - 0.03), r.fill * (1 + 0.09)
    ax.hlines(stop, xi, xe, color=DOWN, lw=1.4, ls="-", zorder=5, label="止损 -3%")
    ax.hlines(target, xi, xe, color=UP, lw=1.4, ls="-", zorder=5, label="止盈 3R = +9%")
    ax.scatter([xi], [r.fill], marker="^", s=90, color="#2980b9", edgecolor="white", zorder=6, label="触价成交")
    exit_px = {"stop": stop, "target": target}.get(r.exit_kind, w.close.iloc[xe])
    ax.scatter([xe], [exit_px], marker="X", s=90, color="black", zorder=6, label="出场")
    kind = {"stop": "止损", "target": "止盈", "timeout": "48h 到期平仓"}[r.exit_kind]
    hold = int(r.bars) * 5 / 60
    when = pd.Timestamp(r.time).tz_convert(TZ).strftime("%Y-%m-%d %H:%M")
    ax.set_title(f"{when}  成交 {r.fill:.0f} · {kind} · 持仓 {hold:.1f}h · 净 {100 * r.net_ret:+.2f}%", fontsize=10)
    time_ticks(ax, w.index.to_numpy())
    ax.grid(alpha=0.15)
    ax.set_xlim(-2, len(w) + 1)


def fig_method(bars, line, eth, out: Path) -> None:
    r = eth.loc[eth.time.eq(ONE)].iloc[0]
    fig = plt.figure(figsize=(17, 7.8))
    gs = fig.add_gridspec(1, 2, width_ratios=[3.2, 1])
    ax, side = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])
    draw_trade(ax, bars, line, r, before_h=14, after_h=5, detail=True)
    side.axis("off")
    handles, labels = ax.get_legend_handles_labels()
    side.legend(handles, labels, loc="upper left", fontsize=10, frameon=False)
    rules = ("回测规则（这一笔就是按这些算的）\n\n"
             "1  线 = V13.1 的 15m EMA120，\n    用上一根已收盘 15m 的值\n"
             "2  价格碰到 线 x (1-5%) 立刻成交\n    （挂单），不等收盘\n"
             "3  止损 3%，止盈 3R = +9%，\n    最多拿 48 小时\n"
             "4  同一根 K 线既碰止损又碰止盈，\n    按止损算（保守）\n"
             "5  每笔扣 0.2% 往返成本\n"
             "6  一次偏离只做一单：收盘回到\n    线 x (1-2.5%) 才算这次结束\n"
             "7  对照：同币、同月、波动相近的\n    随机时间入场 20 次，同样止损止盈\n"
             "8  2023-2024 挑参数，2025 以后检验")
    side.text(0.0, 0.52, rules, transform=side.transAxes, fontsize=10.5, va="top", linespacing=1.45,
              bbox=dict(boxstyle="round", fc="white", ec="#bbbbbb"))
    fig.suptitle("ETHUSDT 5m · 一笔回测交易是怎么算的（你给的参数：离线 5%，止损 3%，1:3）", fontsize=13)
    fig.tight_layout()
    fig.savefig(out / "1_method_one_trade.png", dpi=130)
    plt.close(fig)


def fig_six(bars, line, eth, out: Path) -> None:
    fig, axes = plt.subplots(2, 3, figsize=(18, 9.5))
    for ax, when in zip(axes.ravel(), SIX):
        draw_trade(ax, bars, line, eth.loc[eth.time.eq(when)].iloc[0], before_h=6, after_h=2, detail=False)
    axes[0, 0].legend(loc="upper right", fontsize=7.5, framealpha=0.9)
    after = eth.loc[eth.time >= "2025-01-01"]
    counts = after.exit_kind.value_counts()
    fig.suptitle(f"ETH 2025 年以后的 6 笔（同一套参数）· 这段一共 {len(after)} 笔：止损 {counts.get('stop', 0)}，"
                 f"止盈 {counts.get('target', 0)}，到期 {counts.get('timeout', 0)}，平均每笔 {100 * after.net_ret.mean():+.2f}%",
                 fontsize=13)
    fig.tight_layout()
    fig.savefig(out / "2_eth_six_trades.png", dpi=120)
    plt.close(fig)


def fig_results(trades: pd.DataFrame, summary: pd.DataFrame, selection: pd.DataFrame, out: Path) -> None:
    fig = plt.figure(figsize=(18, 10))
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 1.15])
    split = pd.Timestamp("2025-01-01", tz="UTC").tz_convert(TZ)
    o = owner_cell(trades).copy()
    o = o.loc[np.isfinite(o.net_ret) & np.isfinite(o.control_net_ret)].sort_values("time")
    o["t"] = pd.to_datetime(o.time).dt.tz_convert(TZ)
    for k, (ax, name, g) in enumerate(((fig.add_subplot(gs[0, 0]), "ETH", o.loc[o.symbol.eq("ETHUSDT")]),
                                       (fig.add_subplot(gs[0, 1]), "13 个币合计", o))):
        ax.plot(g.t, 100 * g.net_ret.cumsum(), color="#2980b9", lw=1.8, label=f"策略（{len(g)} 笔）")
        ax.plot(g.t, 100 * g.control_net_ret.cumsum(), color="#95a5a6", lw=1.5, label="随机入场对照（同币同月同波动）")
        ax.axvline(split, color="black", ls="--", lw=1)
        ax.axhline(0, color="#bbbbbb", lw=0.8)
        ymax = ax.get_ylim()[1]
        ax.text(split, ymax, "  ← 2023–2024 挑参数 | 2025 以后检验 →", ha="center", va="top", fontsize=9)
        ax.set_title(f"{name} · 5m 做多 离线 5% 止损 3% 1:3 · 累计净收益（每笔 % 相加）", fontsize=11)
        ax.set_ylabel("累计 %")
        ax.legend(loc="lower left", fontsize=9)
        ax.grid(alpha=0.2)
    ax = fig.add_subplot(gs[1, :])
    w = summary.pivot_table(index=hr.CELL, columns="period", values="mean_net_r").reset_index()
    for side, color, name in ((1, "#27ae60", "做多（线下方）"), (-1, "#c0392b", "做空（线上方）")):
        g = w.loc[w.side.eq(side)]
        ax.scatter(g.select, g.check, s=14, alpha=0.45, color=color, label=f"{name} · {len(g)} 组参数")
    sel = selection.rename(columns={"select_mean_net_r": "select", "check_mean_net_r": "check"})
    ax.scatter(sel.select, sel.check, s=120, facecolor="none", edgecolor="black", lw=1.6,
               label="2023–2024 里挑出的最优 8 组（每种 周期×方向×VixFix 一组）")
    ow = w.loc[(w[list(OWNER)] == pd.Series(OWNER)).all(axis=1)]
    ax.scatter(ow.select, ow.check, marker="*", s=320, color="#f1c40f", edgecolor="black", zorder=5,
               label="你给的参数（5m 多 5% 止损3% 1:3）")
    ax.axhline(0, color="black", lw=0.8)
    ax.axvline(0, color="black", lw=0.8)
    lim = [min(w.select.min(), w.check.min()) - 0.05, max(w.select.max(), w.check.max()) + 0.05]
    ax.plot(lim, lim, color="#bbbbbb", ls=":", lw=1)
    ax.text(lim[1], 0.01, "右上角 = 两段都赚", ha="right", va="bottom", fontsize=10, color="#555555")
    ax.set_xlabel("2023–2024 每笔平均净收益（R，1R = 一倍止损）")
    ax.set_ylabel("2025 以后每笔平均净收益（R）")
    both = ((w.select > 0) & (w.check > 0)).sum()
    ax.set_title(f"全部 {len(w)} 组参数（3 种距离 × 3 档 × 3 种止损 × 3 种止盈 × VixFix 开关 × 多空 × 5m/15m）· "
                 f"两段都赚的只有 {both} 组，且都不显著好于随机", fontsize=11)
    ax.legend(loc="lower left", fontsize=9)
    ax.grid(alpha=0.2)
    fig.suptitle("回测结果：2023–2024 看着能赚的，到 2025 以后都没守住", fontsize=14)
    fig.tight_layout()
    fig.savefig(out / "3_results.png", dpi=120)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--out", type=Path, default=EXP / "charts_v1")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    res = EXP / "results_v1"
    trades = pd.read_csv(res / "trades.csv.gz")
    eth = owner_cell(trades.loc[trades.symbol.eq("ETHUSDT")])
    bars, line = eth_bars()
    fig_method(bars, line, eth, args.out)
    fig_six(bars, line, eth, args.out)
    fig_results(trades, pd.read_csv(res / "summary.csv"), pd.read_csv(res / "selection.csv"), args.out)
    print(sorted(p.name for p in args.out.glob("*.png")))


if __name__ == "__main__":
    main()
