"""Charts for exp-htf-ma-reversion-20261009-v4 (confirmed entries, stop under the swing extreme).

  1_sample_night.png  the owner's 2026-10-08/09 OKX ETH night: the owner's box next to v4's trades
                      (5m long, 5% from the line, prev_bar confirmation, 1:5, re-entry) drawn as
                      TradingView-style position boxes
  2_results.png       ETH two years: cumulative net of the first year's best cell and of the
                      owner-like cell against matched random entries; every cell's two periods
                      for the ETH run and the 13-symbol run

Times in UTC+8. Reads results_eth / results_market and the v2 OKX tail for the night.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-htf-ma-reversion-20261009-v4/plot_charts.py
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from yoyo.evaluation import htf_ma_reversion_v1 as hr  # noqa: E402

EXP = Path("experiments/active/exp-htf-ma-reversion-20261009-v4")
V2_PLOT = Path("experiments/active/exp-htf-ma-reversion-20261009-v2/plot_charts.py")
TZ = "Asia/Shanghai"
NIGHT = {"minutes": 5, "side": 1, "measure": "pct", "level": 0.05, "target_r": 5, "conf": "prev_bar", "reentry": 3}
OWNER_LIKE = NIGHT

spec = importlib.util.spec_from_file_location("v2_plot", V2_PLOT)
v2p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v2p)  # candles, position_box, okx_eth, SAMPLE, rcParams


def pick(frame: pd.DataFrame, spec_: dict) -> pd.DataFrame:
    m = np.ones(len(frame), bool)
    for k, v in spec_.items():
        m &= (frame[k] == v).to_numpy()
    return frame.loc[m]


def describe(s: dict) -> str:
    side = "做多" if s["side"] == 1 else "做空"
    dist = f"离线 {100 * s['level']:g}%" if s["measure"] == "pct" else (
        f"离线 {s['level']:g} 倍 ATR" if s["measure"] == "atr" else f"30 天 {100 * s['level']:g}% 分位")
    conf = "收回触发价" if s["conf"] == "reclaim" else "收过前高/前低"
    return f"{s['minutes']}m {side} {dist} 等{conf} 1:{s['target_r']}" + ("，可再进" if s["reentry"] else "")


def fig_night(trades: pd.DataFrame, out: Path) -> None:
    fr = v2p.okx_eth()
    path = fr["path"]
    pidx = path.index.to_numpy()
    lo, hi = pd.Timestamp("2026-10-08T14:00:00Z").value // 10**6, pd.Timestamp("2026-10-09T13:55:00Z").value // 10**6
    sel = (pidx >= lo) & (pidx <= hi)
    w = path.loc[sel]
    wi = w.index.to_numpy()
    line_w = fr["line"][np.arange(len(path)) // fr["per"]][sel]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
    for ax in axes:
        v2p.candles(ax, *(w[k].to_numpy(float) for k in ("open", "high", "low", "close")))
        ax.step(np.arange(len(w)), line_w, where="mid", color=v2p.LINE, lw=1.5, label="V13.1 线 (15m EMA120)")
        ax.step(np.arange(len(w)), line_w * 0.95, where="mid", color=v2p.TRIG, lw=1.0, ls="--", label="离线 5%")
        v2p.time_ticks(ax, wi)
        ax.grid(alpha=0.15)
    s = v2p.SAMPLE
    xi = int(np.searchsorted(wi, pd.Timestamp(s["entry_utc"]).value // 10**6))
    xe = next(i for i in range(xi + 1, len(w)) if w.high.iloc[i] >= s["target"])
    v2p.position_box(axes[0], xi, xe, 1, s["fill"], s["stop"], s["target"], "target", "10:50 止盈，+3.3%（未扣成本）")
    axes[0].set_title(f"你的样例：{s['fill']:.0f} 入场，止损 {s['stop']:.0f}，止盈 {s['target']:.0f}", fontsize=8.5)
    night = pick(trades, NIGHT)
    night = night.loc[night.time >= "2026-10-08T14:00:00Z"].sort_values("time")
    rows = []
    for k, r in enumerate(night.itertuples()):
        t0 = pd.Timestamp(r.time).value // 10**6
        a = int(np.searchsorted(wi, t0))
        b = min(a + int(r.bars) - 1, len(w) - 1)
        stop = r.extreme * (1 - 0.001)
        risk = r.fill - stop
        v2p.position_box(axes[1], a, b, 1, r.fill, stop, r.fill + 5 * risk, r.exit_kind, labels=(k == 0))
        axes[1].annotate(f"{k + 1}", (a, r.fill + 5 * risk), xytext=(0, 2), textcoords="offset points", fontsize=7,
                         fontweight="bold")
        when = pd.Timestamp(r.time).tz_convert(TZ).strftime("%H:%M")
        rows.append(f"{k + 1}  {when} 开 {r.fill:.0f} 止损 {stop:.0f} → {'止损' if r.exit_kind == 'stop' else '止盈'} "
                    f"{100 * r.net_ret:+.1f}%")
    rows.append(f"合计（已扣 0.2% 成本）{100 * night.net_ret.sum():+.1f}%")
    axes[1].text(0.98, 0.97, "\n".join(rows), transform=axes[1].transAxes, ha="right", va="top", fontsize=7.5,
                 linespacing=1.5, bbox=dict(boxstyle="round", fc="white", ec="#bbbbbb"))
    axes[1].set_title("v4：离线 5% 后等收盘高于前一根最高点再进，止损放低点下方，1:5，止损后可再进", fontsize=8.5)
    axes[0].legend(loc="upper right", fontsize=6.5)
    axes[0].set_ylim(2375, 2600)
    fig.suptitle("ETHUSDT.P (OKX) 5m · 2026-10-08/09 · 你画的那笔 vs v4 规则", fontsize=10.5)
    fig.tight_layout()
    fig.savefig(out / "1_sample_night.png", dpi=100)
    plt.close(fig)


def fig_results(eth: pd.DataFrame, market_summary: pd.DataFrame, out: Path) -> None:
    res = EXP / "results_eth"
    summary = pd.read_csv(res / "summary.csv")
    cell = hr.cell_keys(summary)
    top = pd.read_csv(res / "top10.csv")
    best = {k: top.iloc[0][k] for k in cell}
    split = pd.Timestamp("2025-10-09", tz="UTC").tz_convert(TZ)
    fig, (ax, bx, cx) = plt.subplots(1, 3, figsize=(15, 4.8), gridspec_kw={"width_ratios": [1.3, 1, 1]})
    for spec_, color, name in ((best, "#2980b9", "第一年最优：" + describe(best)),
                               (OWNER_LIKE, "#e67e22", "你的打法：" + describe(OWNER_LIKE))):
        g = pick(eth, {k: spec_[k] for k in NIGHT}).sort_values("time")
        t = pd.to_datetime(g.time).dt.tz_convert(TZ)
        ax.plot(t, 100 * g.net_ret.cumsum(), color=color, lw=1.6, label=f"{name}（{len(g)} 笔）")
        if spec_ is best:
            ax.plot(t, 100 * g.control_net_ret.cumsum(), color="#95a5a6", lw=1.2, label="同一批时间的随机入场对照")
    ax.axvline(split, color="black", ls="--", lw=1)
    ax.axhline(0, color="#bbbbbb", lw=0.8)
    ax.text(0.02, 0.97, "第一年：挑参数", transform=ax.transAxes, fontsize=8, va="top")
    ax.text(0.98, 0.97, "第二年：检验", transform=ax.transAxes, fontsize=8, va="top", ha="right")
    ax.set_title("ETHUSDT.P 两年累计净收益（每笔 % 相加，已扣 0.2% 成本）", fontsize=9)
    ax.legend(loc="lower left", fontsize=6.5)
    ax.grid(alpha=0.2)
    for axis, frame, title in ((bx, summary, "ETH 两年 · 第一年 vs 第二年"),
                               (cx, market_summary, "13 个币 · 2025 前 vs 2025 后")):
        s = frame.assign(total=frame.mean_net_pct * frame.trades)
        wv = s.pivot_table(index=hr.cell_keys(frame), columns="period", values="total").dropna()
        axis.scatter(wv.select, wv.check, s=6, alpha=0.4, color="#7f8c8d")
        axis.axhline(0, color="black", lw=0.8)
        axis.axvline(0, color="black", lw=0.8)
        both = int(((wv.select > 0) & (wv.check > 0)).sum())
        axis.set_title(f"{title} · 两段都赚 {both} / {len(wv)} 组", fontsize=9)
        axis.set_xlabel("前一段合计净收益 %")
        axis.set_ylabel("后一段合计净收益 %")
        axis.grid(alpha=0.2)
    fig.suptitle("v4（等止跌再进、止损放低点外）：仍然没有两段都站得住的参数", fontsize=11)
    fig.tight_layout()
    fig.savefig(out / "2_results.png", dpi=100)
    plt.close(fig)


def main() -> None:
    out = EXP / "charts"
    out.mkdir(parents=True, exist_ok=True)
    eth = pd.read_csv(EXP / "results_eth" / "trades.csv.gz")
    eth = eth.loc[np.isfinite(eth.net_ret) & np.isfinite(eth.control_net_ret)]
    fig_night(eth, out)
    fig_results(eth, pd.read_csv(EXP / "results_market" / "summary.csv"), out)
    print(sorted(p.name for p in out.glob("*.png")))


if __name__ == "__main__":
    main()
