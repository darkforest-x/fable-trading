"""Every trade of v4's ETH first-year best cell, plus 10 TradingView-style charts per year.

Owner 2026-10-09: 每一笔都给我看 图也要给我 跟我给你的图一样 两组分别给10张就好 - about the ETH run's
first-year best (15m short, 4x 1h ATR above the 1h SMA60 line, reclaim confirmation, stop 0.1% above
the excursion high, 5R, one entry per excursion).

  best_trades/trades_all.csv   every trade of the cell, both years, UTC+8 times
  best_trades/y1_NN.png        10 first-year trades, y2_NN.png 10 second-year trades

The 10 per year are evenly spaced through that year's trades in time order (np.linspace over the
trade index), not picked by outcome. Charts mimic the owner's TradingView screenshot: 15m candles,
price axis on the right, the V13.1 1h SMA60 line in purple, a short position box (red = entry to
stop above, green = entry to target below) from entry to exit.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-htf-ma-reversion-20261009-v4/plot_best_trades.py
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

from yoyo.evaluation import htf_ma_reversion_v1 as hr  # noqa: E402

EXP = Path("experiments/active/exp-htf-ma-reversion-20261009-v4")
CELL = {"minutes": 15, "side": -1, "measure": "atr", "level": 4.0, "conf": "reclaim", "target_r": 5, "reentry": 0}
TZ = "Asia/Shanghai"
UP, DOWN, LINE, TRIG = "#26a69a", "#ef5350", "#9c6ade", "#f39c12"
PER_YEAR = 10
KIND = {"stop": "止损", "target": "止盈", "timeout": "48h 到期"}

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "STHeiti", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def local(ms: int) -> pd.Timestamp:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").tz_convert(TZ)


def trade_table(trades: pd.DataFrame, fr: dict, cfg: dict) -> pd.DataFrame:
    """One row per trade with entry, stop, target, exit and outcome (prices from the 5m path)."""
    path = fr["path"]
    pidx = path.index.to_numpy()
    c = path.close.to_numpy(float)
    rows = []
    for k, r in enumerate(trades.sort_values("time").itertuples(), 1):
        e = int(np.searchsorted(pidx, pd.Timestamp(r.time).value // 10**6))
        x = e + int(r.bars) - 1
        stop = r.extreme * (1 + cfg["stop_buffer"])
        target = r.fill - CELL["target_r"] * (stop - r.fill)
        exit_px = {"stop": stop, "target": target}.get(r.exit_kind, c[x])
        rows.append({"序号": k, "年段": "第一年" if r.time < cfg["select_before"] else "第二年",
                     "触发时间": local(pd.Timestamp(r.start_time).value // 10**6).strftime("%Y-%m-%d %H:%M"),
                     "入场时间": local(pidx[e]).strftime("%Y-%m-%d %H:%M"), "入场价": round(r.fill, 2),
                     "离线%": round(r.dev_pct, 2), "前高": round(r.extreme, 2), "止损": round(stop, 2),
                     "止盈": round(target, 2), "止损幅度%": round(100 * r.risk_frac, 2), "出场": KIND[r.exit_kind],
                     "出场时间": local(pidx[x]).strftime("%Y-%m-%d %H:%M"), "出场价": round(exit_px, 2),
                     "持仓小时": round(int(r.bars) * 5 / 60, 1), "净收益%": round(100 * r.net_ret, 2),
                     "净R": round(r.net_r, 2), "随机对照%": round(100 * r.control_net_ret, 2),
                     "_e": e, "_x": x})
    return pd.DataFrame(rows)


def draw(row: pd.Series, fr: dict, cfg: dict, out: Path) -> None:
    chart, line, atr, per = fr["chart"], fr["line"], fr["atr"], fr["per"]
    j_in, j_out = int(row._e) // per, int(row._x) // per
    lo, hi = max(j_in - 64, 0), min(j_out + 24, len(chart) - 1)
    w = chart.iloc[lo:hi + 1]
    o, h, l, c = (w[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    x = np.arange(len(w))
    fig, ax = plt.subplots(figsize=(12, 6.2))
    col = np.where(c >= o, UP, DOWN)
    ax.vlines(x, l, h, color=col, linewidth=0.8, zorder=2)
    ax.bar(x, np.maximum(np.abs(c - o), (np.nanmax(h) - np.nanmin(l)) * 0.001), bottom=np.minimum(o, c), width=0.65,
           color=col, linewidth=0, zorder=3)
    ln = line[lo:hi + 1]
    ax.step(x, ln, where="mid", color=LINE, lw=2.0, zorder=4, label="V13.1 线（1h SMA60）")
    ax.step(x, ln + CELL["level"] * atr[lo:hi + 1], where="mid", color=TRIG, lw=1.0, ls="--", zorder=4,
            label="离线 4 倍 1h ATR（触发价）")
    xi, xe = j_in - lo, max(j_out - lo, j_in - lo + 1)
    fill, stop, target = row["入场价"], row["止损"], row["止盈"]
    ax.add_patch(Rectangle((xi, fill), xe - xi, stop - fill, fc=DOWN, alpha=0.20, ec=DOWN, lw=0.8, zorder=1))
    ax.add_patch(Rectangle((xi, target), xe - xi, fill - target, fc=UP, alpha=0.20, ec=UP, lw=0.8, zorder=1))
    ax.hlines(fill, xi, xe, color="#333333", lw=0.9, zorder=5)
    ax.scatter([xi], [fill], marker="v", s=55, color="#2962ff", edgecolor="white", zorder=6)
    ax.scatter([xe], [row["出场价"]], marker="X", s=55, color="black", zorder=6)
    for y, txt, color in ((stop, f"止损 {stop:.2f}（+{row['止损幅度%']:.2f}%）", "#c0392b"),
                          (target, f"止盈 {target:.2f}（1:5）", "#1e8449"), (fill, f"入场 {fill:.2f}", "#333333")):
        ax.annotate(txt, (xe, y), xytext=(4, 0), textcoords="offset points", va="center", fontsize=8, color=color,
                    zorder=7, bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.8))
    ax.yaxis.tick_right()
    ax.yaxis.set_label_position("right")
    ax.tick_params(labelsize=8)
    pos = np.linspace(0, len(w) - 1, 7).astype(int)
    ax.set_xticks(pos, [local(w.index[p]).strftime("%m-%d %H:%M") for p in pos])
    ax.grid(alpha=0.15)
    ax.set_xlim(-1, len(w) + 6)
    ax.legend(loc="upper left", fontsize=7.5, framealpha=0.9)
    result = f"{row['出场']} · 持仓 {row['持仓小时']:.1f}h · 净 {row['净收益%']:+.2f}%（{row['净R']:+.2f}R）"
    ax.set_title(f"ETHUSDT.P · 15 · OKX    做空 #{row['序号']} {row['年段']} · 入场 {row['入场时间']} · {result}",
                 fontsize=10, loc="left")
    fig.tight_layout()
    fig.savefig(out, dpi=100)
    plt.close(fig)


def main() -> None:
    cfg = json.loads((EXP / "config_eth.json").read_text())
    trades = pd.read_csv(EXP / "results_eth" / "trades.csv.gz")
    m = np.isfinite(trades.net_ret).to_numpy() & np.isfinite(trades.control_net_ret).to_numpy()
    for k, v in CELL.items():
        m &= (trades[k] == v).to_numpy()
    raw = hr.read_5m("ETHUSDT", pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"]), cfg["series_dir"])
    fr = hr.chart_frames(raw, 15, 60)
    table = trade_table(trades.loc[m], fr, cfg)
    out = EXP / "charts" / "best_trades"
    out.mkdir(parents=True, exist_ok=True)
    table.drop(columns=["_e", "_x"]).to_csv(out / "trades_all.csv", index=False, encoding="utf-8-sig")
    for tag, year in (("y1", "第一年"), ("y2", "第二年")):
        part = table.loc[table["年段"].eq(year)].reset_index(drop=True)
        for n, i in enumerate(np.unique(np.linspace(0, len(part) - 1, PER_YEAR).round().astype(int)), 1):
            draw(part.iloc[i], fr, cfg, out / f"{tag}_{n:02d}.png")
    summary = table.groupby("年段").agg(笔数=("序号", "size"), 合计净收益=("净收益%", "sum"),
                                        止损=("出场", lambda s: int((s == "止损").sum())),
                                        止盈=("出场", lambda s: int((s == "止盈").sum())),
                                        到期=("出场", lambda s: int((s == "48h 到期").sum())))
    print(summary.round(1).to_string())


if __name__ == "__main__":
    main()
