"""Candidate charts for exp-owner-squeeze-breakout-20261008-v1 (owner 2026-10-08: "扫完把候选图发给我看看").

Draws a seeded random sample of primary events per timeframe, not the best ones, so the owner
sees what the rule actually catches. Each panel shows the six MAs (SMA solid, EMA dashed,
20/60/120), the BB200 +/- 2 band, the 12-bar dense window before the signal, the signal candle,
entry / stop / 3R / 5R levels and the bars after the signal with the 3R exit. Times are Beijing
time (UTC+8) to match the owner's TradingView charts. Viewing aid only; nothing here is a label.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from yoyo.evaluation import market_sync_shock as v1  # noqa: E402
from yoyo.evaluation import squeeze_breakout_v1 as sb  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "STHeiti", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
TF_NAME = {5: "5分", 15: "15分", 30: "30分", 60: "1小时", 240: "4小时"}
MA_COLOR = {20: "#2962ff", 60: "#ff9800", 120: "#9c27b0"}
UP, DOWN = "#26a69a", "#ef5350"
KIND = {"target": "止盈", "stop": "止损", "timeout": "到期平"}


def sample(trades: pd.DataFrame, per_tf: int, seed: int) -> pd.DataFrame:
    p = trades.loc[trades.variant.eq("primary") & np.isfinite(trades.net_r)]
    three = p.loc[p.target_r.eq(3)]
    five = p.loc[p.target_r.eq(5), ["symbol", "minutes", "time", "exit_kind", "net_r"]]
    both = three.merge(five, on=["symbol", "minutes", "time"], suffixes=("", "_5r"), how="left")
    return pd.concat([g.sample(min(per_tf, len(g)), random_state=seed) for _, g in both.groupby("minutes")])


def panel(ax, axv, bars: pd.DataFrame, ev: pd.Series, pre: int = 100, post: int = 70) -> None:
    f = sb.features(bars)
    c = bars.close
    basis, sd = c.rolling(sb.BB_LEN).mean(), c.rolling(sb.BB_LEN).std(ddof=0)
    t_ms = pd.Timestamp(ev.time).value // 10**6
    i = int(np.flatnonzero(bars.index.to_numpy() == t_ms)[0])
    lo, hi = max(0, i - pre), min(len(bars), i + post + 1)
    w = bars.iloc[lo:hi]
    x = np.arange(lo, hi) - i
    o, h, l, cl, v = (w[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
    col = np.where(cl >= o, UP, DOWN)
    ax.vlines(x, l, h, color=col, linewidth=0.7)
    ax.bar(x, np.maximum(np.abs(cl - o), 1e-12), bottom=np.minimum(o, cl), color=col, width=0.7)
    axv.bar(x, v, color=col, width=0.7)
    for n in sb.MA_PERIODS:
        ax.plot(x, c.rolling(n).mean().iloc[lo:hi], color=MA_COLOR[n], linewidth=0.9)
        ax.plot(x, c.ewm(span=n, adjust=False).mean().iloc[lo:hi], color=MA_COLOR[n], linewidth=0.9, linestyle="--")
    for band in (basis + 2 * sd, basis - 2 * sd):
        ax.plot(x, band.iloc[lo:hi], color="#9e9e9e", linewidth=0.8, alpha=0.7)
    ax.axvspan(-sb.DENSE_LEN - 0.5, -0.5, color="#f3e5c8", alpha=0.6, zorder=0)
    ax.axvline(0, color="#424242", linewidth=0.6, linestyle=":")
    side = int(ev.side)
    entry, stop = float(ev.entry), float(ev.stop)
    risk = side * (entry - stop)
    end = min(hi - lo, int(ev.bars) + 1) if ev.bars > 0 else post
    ax.hlines(entry, 1, end, color="#212121", linewidth=1.0, linestyle="--")
    ax.hlines(stop, 1, end, color=DOWN, linewidth=1.2)
    ax.hlines(entry + side * 3 * risk, 1, end, color="#2e7d32", linewidth=1.2)
    ax.hlines(entry + side * 5 * risk, 1, end, color="#1b5e20", linewidth=1.0, linestyle=":")
    if ev.bars > 0 and ev.bars <= post:
        ex = entry * (1 + side * float(ev.gross_r) * float(ev.risk_frac))
        ax.plot([ev.bars], [ex], marker="x", color="#000000", markersize=8)
    ymin, ymax = min(np.nanmin(l), stop, entry), max(np.nanmax(h), stop, entry)
    span = ymax - ymin
    for level in (entry + side * 3 * risk, entry + side * 5 * risk):  # far targets stay off-chart
        if ymin - 0.6 * span <= level <= ymax + 0.6 * span:
            ymin, ymax = min(ymin, level), max(ymax, level)
    pad = 0.03 * (ymax - ymin)
    ax.set_ylim(ymin - pad, ymax + pad)
    bj = pd.Timestamp(ev.time).tz_convert("Asia/Shanghai")
    five = f"；5R {KIND.get(ev.exit_kind_5r, '?')} {ev.net_r_5r:+.1f}R" if pd.notna(ev.get("net_r_5r")) else ""
    ax.set_title(f"{ev.symbol}  {TF_NAME[int(ev.minutes)]}  {'做多' if side > 0 else '做空'}  {bj:%Y-%m-%d %H:%M}\n"
                 f"3R {KIND.get(ev.exit_kind, '?')} {ev.net_r:+.1f}R{five}   止损 {100 * ev.risk_frac:.2f}%  "
                 f"量比 {ev.rv:.1f}×  实体 {ev.body_atr:.1f}ATR  均线宽 {ev.past_width:.1f}ATR", fontsize=9)
    ticks = [t for t in range(x[0], x[-1] + 1) if t % 20 == 0]
    stamps = pd.to_datetime(bars.index.to_numpy()[[t + i for t in ticks]], unit="ms", utc=True).tz_convert("Asia/Shanghai")
    fmt = "%m-%d %H:%M" if ev.minutes < 240 else "%m-%d"
    axv.set_xticks(ticks, [s.strftime(fmt) for s in stamps], fontsize=7)
    ax.set_xticks(ticks, [])
    ax.tick_params(axis="y", labelsize=7)
    axv.tick_params(axis="y", labelsize=6)
    axv.set_yticks([])
    for a in (ax, axv):
        a.set_xlim(x[0] - 1, x[-1] + 1)
        a.grid(alpha=0.15)


def render(results: Path, out: Path, per_tf: int, seed: int) -> list[Path]:
    cfg = json.loads(sb.CONFIG.read_text())
    trades = pd.read_csv(results / "trades.csv.gz")
    picked = sample(trades, per_tf, seed)
    out.mkdir(parents=True, exist_ok=True)
    cache: dict[str, pd.DataFrame] = {}
    files = []
    for minutes, g in picked.groupby("minutes"):
        rows = (len(g) + 1) // 2
        fig = plt.figure(figsize=(16, 5.4 * rows))
        grid = fig.add_gridspec(rows * 5, 2, hspace=0.35, wspace=0.12)  # price 3, volume 1, spacer 1
        for k, (_, ev) in enumerate(g.iterrows()):
            r, cidx = divmod(k, 2)
            ax = fig.add_subplot(grid[r * 5: r * 5 + 3, cidx])
            axv = fig.add_subplot(grid[r * 5 + 3, cidx])
            if ev.symbol not in cache:
                cache[ev.symbol] = v1.read_5m(ev.symbol, pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"]))
            panel(ax, axv, sb.full_bars(cache[ev.symbol], int(minutes)), ev)
        fig.suptitle(f"{TF_NAME[int(minutes)]} · 随机抽 {len(g)} 个候选（不是挑好的）· 米色 = 信号前 12 根密集区 · "
                     "黑虚线入场 / 红止损 / 绿 3R / 深绿点线 5R / × 3R 出场", fontsize=11, y=1.0)
        path = out / f"candidates_{int(minutes)}m.png"
        fig.savefig(path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        files.append(path)
    picked.to_csv(out / "sampled_events.csv", index=False)
    return files


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--results", type=Path, default=sb.EXP / "results_v1")
    parser.add_argument("--out", type=Path, default=sb.EXP / "gallery_v1")
    parser.add_argument("--per-tf", type=int, default=6)
    parser.add_argument("--seed", type=int, default=1008603)
    args = parser.parse_args()
    for path in render(args.results, args.out, args.per_tf, args.seed):
        print(path)


if __name__ == "__main__":
    main()
