"""Owner-requested HTML view of the SPIKE V12.9 OKX ETH 15m backtest (2026-10-10).

Owner: "v12.9 15min ethusdt.p详细回测呢" after the 5m view. Adapted copy of
experiments/active/exp-spike-v129-eth5m-20261010-v1/build_report.py: same tables, weekday
and worst-period sections and TradingView-style trade charts, ordinary signals only (the
owner dropped 突破+spike). Differences: 15m bars, the purple line is the completed 1h SMA60
(the 15m direction gate), every closed trade is charted (164), streaks are computed here
(shuffle test, same method as the 5m pattern audit), down-legs use an 8R zigzag, and the
5m-only pattern audit / exit test sections are absent. Binance context is the
manual-system ETHUSDT_15m baseline ledger 2021-09..2024-01. No rule is changed.

Owner follow-up "15m去掉周末再回测一下": --run/--out/--suffix/--compare render the same page for the
no-weekend ledger of exp-spike-v129-eth15m-weekend-20261010-v1 with a before/after table on top.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v129-eth15m-20261010-v1/build_report.py
     [--run DIR --out DIR --suffix TEXT --compare comparison.csv]
Output (local, untracked): experiments/active/exp-spike-v129-eth15m-20261010-v1/report_v1/
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from yoyo.evaluation import spike_lowtf_v1_v126 as low  # noqa: E402
from yoyo.evaluation import spike_v128_recent as p  # noqa: E402
from yoyo.evaluation import spike_v129_eth15m as fifteen  # noqa: E402

EXP = fifteen.EXP
RUN = EXP / "run_v1"
OUT = EXP / "report_v1"
CHARTS = OUT / "charts"
BINANCE_LEDGER = Path("experiments/active/exp-spike-manual-system-20260925-v1/run_v1/streams/ETHUSDT_15m")
RECENT_DAYS = 183
BJ = "Asia/Shanghai"
UP, DOWN = "#089981", "#F23645"
MA_STYLE = {"s20": ("#008F82", .62), "e20": ("#008F82", .42), "s60": ("#4679C9", .60), "e60": ("#4679C9", .40),
            "s120": ("#787B86", .52), "e120": ("#787B86", .34)}
REASON = {"initial_stop": "初始止损", "trailing_stop": "跟踪止损", "opposite_v6_next_open": "反向信号离场"}
ARM = {"ordinary": "普通", "joint": "突破+spike"}
plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "PingFang HK", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


# ----------------------------------------------------------------------------- data

def load() -> dict:
    cfg = fifteen.config()
    base5m = fifteen.okx_base5m(cfg)
    bars, _ = p.complete_bars(base5m, 15)
    facts = p.facts_for(bars, base5m, cfg["asset"], float(cfg["tick"]), 15)
    frame = facts["frame"].copy()
    frame["dirline"] = facts["h1"].sma_60.to_numpy(float)
    mid = frame.close.rolling(200).mean()
    sd = frame.close.rolling(200).std(ddof=0)
    frame["bb_up"], frame["bb_dn"] = mid + 2 * sd, mid - 2 * sd
    trades = pd.read_csv(RUN / "trades.csv.gz")
    for col in ("signal_close", "entry_time", "exit_time"):
        trades[col] = pd.to_datetime(trades[col], utc=True)
    ctrl = pd.read_csv(RUN / "controls.csv.gz").set_index("trade_key")
    trades["control_net_r"] = trades.trade_key.map(ctrl.control_net_r)
    trades["matched"] = trades.trade_key.map(ctrl.matched).fillna(False).astype(bool)
    trades = trades.loc[~trades.censored.astype(bool) & (trades.arm == "ordinary")].copy()
    trades["net_bp"] = trades.net_return * 1e4
    trades["fee_r"] = .002 / trades.initial_risk_frac
    trades["hold_h"] = (trades.exit_time - trades.entry_time).dt.total_seconds() / 3600
    receipt = json.loads((RUN / "receipt.json").read_text())
    h1 = frame.close.resample("1h").last().dropna()
    width = 4 * frame.close.rolling(200).std(ddof=0) / frame.close.rolling(200).mean()
    threshold = width.shift().rolling(500).quantile(.10)
    known = threshold.notna()
    frame["bb_compressed"] = (width <= threshold).where(known)
    return {"cfg": cfg, "frame": frame, "h1": h1, "trades": trades, "receipt": receipt,
            "yearly": pd.read_csv(RUN / "yearly.csv"), "monthly": pd.read_csv(RUN / "monthly.csv"),
            "tick": float(cfg["tick"])}


# ------------------------------------------------------------------------- metrics

def stats(g: pd.DataFrame) -> dict:
    g = g.sort_values("exit_time")
    r = g.net_r
    if not len(r):
        return {"n": 0}
    curve = r.cumsum().to_numpy()
    dd = float((np.maximum.accumulate(np.r_[0, curve]) - np.r_[0, curve]).max())
    run = best = 0
    for v in r < 0:
        run = run + 1 if v else 0
        best = max(best, run)
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    m = g.loc[g.matched]
    return {"n": len(r), "win": float((r > 0).mean()), "gross": float(g.gross_r.mean()), "fee": float(g.fee_r.mean()),
            "mean": float(r.mean()), "sum": float(r.sum()), "pf": float(wins / losses) if losses else math.inf,
            "avg_win": float(r[r > 0].mean()) if (r > 0).any() else math.nan,
            "avg_loss": float(r[r < 0].mean()) if (r < 0).any() else math.nan,
            "best": float(r.max()), "dd": dd, "streak": best, "ge5": int((r >= 5).sum()),
            "touch5": int((g.mfe_known_r >= 5).sum()), "hold": float(g.hold_h.median()),
            "risk": float(g.initial_risk_frac.median() * 100), "ctrl": float(m.control_net_r.mean()) if len(m) else math.nan,
            "sum_wo5": float(r.sum() - r.nlargest(5).sum())}


GROUPS = (("普通·全部", lambda t: t.arm == "ordinary"), ("普通·多", lambda t: (t.arm == "ordinary") & (t.side == 1)),
          ("普通·空", lambda t: (t.arm == "ordinary") & (t.side == -1)))


# --------------------------------------------------------------------------- charts

def _bj(index) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(index).tz_convert(BJ).tz_localize(None)


def equity_chart(trades: pd.DataFrame, recent_start: pd.Timestamp, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 4.4), dpi=110)
    colors = {"普通·全部": "#1f2937", "普通·多": UP, "普通·空": DOWN, "突破+spike": "#AD7B29"}
    for name, pick in GROUPS:
        g = trades.loc[pick(trades)].sort_values("exit_time")
        ax.plot(_bj(g.exit_time), g.net_r.cumsum(), color=colors[name], lw=1.6 if name == "普通·全部" else 1.1, label=name)
    g = trades.loc[trades.arm == "ordinary"].sort_values("exit_time")
    ax.plot(_bj(g.exit_time), g.gross_r.cumsum(), color="#1f2937", lw=1, ls="--", alpha=.55, label="普通·全部（扣费前）")
    ax.axvspan(_bj([recent_start])[0], _bj(g.exit_time).max(), color="#fde68a", alpha=.25, lw=0)
    ax.text(_bj([recent_start])[0], ax.get_ylim()[1], " 最近半年", va="top", fontsize=9, color="#92400e")
    ax.axhline(0, color="#9ca3af", lw=.8)
    ax.set_ylabel("累计 R")
    ax.legend(loc="lower left", fontsize=8.5, ncol=5, frameon=False)
    ax.grid(alpha=.25)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, format="png")
    plt.close(fig)


def monthly_chart(trades: pd.DataFrame, path: Path) -> None:
    t = trades.assign(month=_bj(trades.signal_close).strftime("%Y-%m"))
    months = sorted(t.month.unique())[-12:]
    o = t.loc[t.arm == "ordinary"].groupby("month").net_r.sum().reindex(months, fill_value=0)
    x = np.arange(len(months))
    fig, ax = plt.subplots(figsize=(11, 3.4), dpi=110)
    ax.bar(x, o, .6, color=[UP if v > 0 else DOWN for v in o])
    for xi, v in zip(x, o):
        ax.text(xi, v, f"{v:+.1f}", ha="center", va="bottom" if v >= 0 else "top", fontsize=8)
    ax.set_xticks(x, [m[2:] for m in months], fontsize=8.5)
    ax.axhline(0, color="#9ca3af", lw=.8)
    ax.set_ylabel("当月合计 R")
    ax.grid(axis="y", alpha=.25)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, format="png")
    plt.close(fig)


def trail_path(row, o, h, l, c, atr, tick) -> np.ndarray:
    """Protection in force during each bar entry..exit under the incumbent rule."""
    side, entry = int(row.side), float(row.entry_price)
    stop = round(float(row.initial_stop) / tick) * tick
    risk = side * (entry - stop)
    prot, armed, out = stop, False, []
    for j in range(int(row.entry_i), int(row.exit_i) + 1):
        out.append(prot)
        armed = armed or side * (c[j] - entry) / risk >= 2.0
        if armed and atr[j] > 0:
            raw = c[j] - side * 4.0 * atr[j]
            cand = math.floor(raw / tick) * tick if side == 1 else math.ceil(raw / tick) * tick
            prot = max(prot, cand) if side == 1 else min(prot, cand)
    return np.asarray(out)


def trade_chart(row, frame: pd.DataFrame, tick: float, arms: str, number: int, path: Path) -> dict:
    o, h, l, c, atr = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close", "atr"))
    e, x, sig = int(row.entry_i), int(row.exit_i), int(row.signal_i)
    a, b = max(0, sig - 72), min(len(frame) - 1, x + 24)
    idx = np.arange(a, b + 1)
    side, entry, stop = int(row.side), float(row.entry_price), float(row.initial_stop)
    risk = abs(entry - stop)
    prot = trail_path(row, o, h, l, c, atr, tick)
    if row.exit_reason == "trailing_stop" and abs(prot[-1] - float(row.exit_price)) > tick * 1.01 \
            and not (side * (o[x] - prot[-1]) < 0):
        raise ValueError(f"trail path does not end at recorded exit: {row.trade_key}")
    fig = plt.figure(figsize=(11, 5.6), dpi=105)
    ax = fig.add_axes([.06, .25, .86, .66])
    av = fig.add_axes([.06, .08, .86, .15], sharex=ax)
    pos = idx - a
    up = c[idx] >= o[idx]
    for color, mask in ((UP, up), (DOWN, ~up)):
        ax.vlines(pos[mask], l[idx][mask], h[idx][mask], color=color, lw=.8)
        ax.bar(pos[mask], np.maximum(np.abs(c[idx] - o[idx])[mask], tick), .62,
               bottom=np.minimum(o[idx], c[idx])[mask], color=color, lw=0)
        av.bar(pos[mask], frame.volume.to_numpy(float)[idx][mask], .62, color=color, alpha=.45, lw=0)
    for col, (color, alpha) in MA_STYLE.items():
        ax.plot(pos, frame[col].to_numpy(float)[idx], color=color, alpha=alpha, lw=1)
    ax.plot(pos, frame.bb_up.to_numpy(float)[idx], color="#7D8490", alpha=.3, lw=.8)
    ax.plot(pos, frame.bb_dn.to_numpy(float)[idx], color="#7D8490", alpha=.3, lw=.8)
    ax.step(pos, frame.dirline.to_numpy(float)[idx], where="post", color="#C18CFA", lw=2, label="1h SMA60")
    e0, x0 = e - a, x - a
    mfe = float(row.mfe_known_r) if math.isfinite(row.mfe_known_r) else 0.0
    peak = entry + side * max(mfe, 0) * risk
    xp = float(row.exit_price)
    lo = min(l[idx].min(), stop, peak, xp)
    hi = max(h[idx].max(), stop, peak, xp)
    pad = (hi - lo) * .06
    near = (hi - lo) * .03
    box = {"facecolor": "white", "alpha": .82, "edgecolor": "none", "pad": 1.2}
    ax.add_patch(plt.Rectangle((e0 - .4, min(entry, stop)), x0 - e0 + .8, risk, color=DOWN, alpha=.16, lw=0))
    if mfe > 0:
        ax.add_patch(plt.Rectangle((e0 - .4, min(entry, peak)), x0 - e0 + .8, abs(peak - entry), color=UP, alpha=.13, lw=0))
    ax.hlines(stop, e0 - .4, x0 + .4, color=DOWN, lw=1)
    ax.hlines(entry, e0 - .4, x0 + .4, color="#374151", lw=1)
    last = entry
    for k in (1, 2, 3, 5, 10, 20):
        y = entry + side * k * risk
        if k > max(3, mfe + .5) or abs(y - last) < near or not (lo - pad <= y <= hi + pad):
            continue
        ax.hlines(y, e0, x0, color=UP, lw=.7, ls=(0, (4, 4)), alpha=.55)
        ax.text(x0 + .6, y, f"{k}R", fontsize=7, color=UP, va="center", alpha=.85)
        last = y
    armed = prot != prot[0]
    if armed.any():
        pp = np.where(armed, prot, np.nan)
        ax.step(np.arange(e0, x0 + 1), pp, where="post", color="#0f766e", lw=1.4)
    color = UP if row.net_r > 0 else DOWN
    ax.hlines(xp, e0, x0 + 3, color=color, lw=1.3, ls="--")
    if abs(xp - stop) < near:
        ax.text(x0 + 3.5, xp, f"止损离场 {xp:.2f}  {row.net_r:+.2f}R", fontsize=8.5, va="center", color=DOWN,
                fontweight="bold", bbox=box)
    else:
        ax.text(x0 + 3.5, xp, f"离场 {row.net_r:+.2f}R", fontsize=8.5, va="center", color=color, fontweight="bold", bbox=box)
        ax.text(x0 + .6, stop, f"止损 {stop:.2f}", fontsize=8, color=DOWN, va="center", bbox=box)
    ax.text(e0 - 1, entry, f"进场 {entry:.2f}", fontsize=8, color="#374151", va="center", ha="right", bbox=box)
    if mfe > 0 and abs(peak - entry) >= near:
        ax.text(e0 + 1, peak, f"峰值 {mfe:.1f}R", fontsize=8, color=UP, va="bottom" if side == 1 else "top", bbox=box)
    marker_y = l[sig] - .4 * atr[sig] if side == 1 else h[sig] + .4 * atr[sig]
    ax.scatter([sig - a], [marker_y], marker="^" if side == 1 else "v", s=70, color=UP if side == 1 else DOWN, zorder=5)
    ax.set_ylim(lo - pad, hi + pad)
    ax.set_xlim(-1, len(idx) + 9)
    ax.yaxis.tick_right()
    av.yaxis.set_visible(False)
    ticks = np.linspace(0, len(idx) - 1, 7).astype(int)
    av.set_xticks(ticks, [_bj([frame.index[a + t]])[0].strftime("%m-%d %H:%M") for t in ticks], fontsize=8)
    plt.setp(ax.get_xticklabels(), visible=False)
    for axis in (ax, av):
        axis.grid(alpha=.18)
        axis.spines[["top", "left"]].set_visible(False)
    when = _bj([row.signal_close])[0].strftime("%Y-%m-%d %H:%M")
    ax.set_title(f"#{number}  {when}  {'做多' if side == 1 else '做空'}  ·  {REASON.get(row.exit_reason, row.exit_reason)}"
                 f"  ·  净 {row.net_r:+.2f}R", loc="left", fontsize=10.5)
    fig.savefig(path, format="jpg", pil_kwargs={"quality": 82, "optimize": True})
    plt.close(fig)
    return {"bars": len(idx)}


# ---------------------------------------------------------------------- worst periods

def down_legs(t: pd.DataFrame, threshold: float = 15.0) -> list[tuple[int, int]]:
    """Zigzag pivots of cumulative net R (positions in the exit-ordered ledger, 0 = start)."""
    vals = np.r_[0.0, t.net_r.cumsum().to_numpy()]
    piv, mode, hi, lo = [0], None, 0, 0
    for k in range(1, len(vals)):
        if mode in (None, "up"):
            hi = k if vals[k] > vals[hi] else hi
            if vals[hi] - vals[k] >= threshold:
                if piv[-1] != hi:
                    piv.append(hi)
                mode, lo = "down", k
        if mode == "down":
            lo = k if vals[k] < vals[lo] else lo
            if vals[k] - vals[lo] >= threshold:
                piv.append(lo)
                mode, hi = "up", k
    piv.append(lo if mode == "down" else hi)
    return [(a, b) for a, b in zip(piv, piv[1:]) if vals[b] < vals[a]]


def eth_move(h1: pd.Series, a, b) -> tuple[float, float, float, float]:
    p = h1.loc[a:b]
    return float(p.iloc[0]), float(p.iloc[-1]), float((p.iloc[-1] / p.iloc[0] - 1) * 100), float((p.max() / p.min() - 1) * 100)


def worst_window(t: pd.DataFrame, days: int) -> tuple[int, int, float]:
    times = t.exit_time.to_numpy()
    best = None
    for i in range(len(t)):
        j = int(np.searchsorted(times, times[i] + np.timedelta64(days, "D"), side="right")) - 1
        total = float(t.net_r.iloc[i:j + 1].sum())
        if best is None or total < best[2]:
            best = (i, j, total)
    return best


def worst_chart(t: pd.DataFrame, h1: pd.Series, legs, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 4.6), dpi=110)
    times = _bj(t.exit_time)
    eq = t.net_r.cumsum().to_numpy()
    ax.plot(times, eq, color="#1f2937", lw=1.6, label="累计 R（普通信号）")
    start = times[0] - pd.Timedelta(minutes=15)
    stamp = lambda k: start if k == 0 else times[k - 1]
    level = lambda k: 0.0 if k == 0 else eq[k - 1]
    for n, (a, b) in enumerate(sorted(legs, key=lambda ab: level(ab[1]) - level(ab[0]))[:3], 1):
        ax.axvspan(stamp(a), stamp(b), color=DOWN, alpha=.10, lw=0)
        ax.text(stamp(a), level(a), f" {n}", fontsize=9, color=DOWN, va="bottom", fontweight="bold")
    ax.axhline(0, color="#9ca3af", lw=.8)
    ax.set_ylabel("累计 R")
    ax.grid(alpha=.25)
    px = ax.twinx()
    px.plot(_bj(h1.index), h1.to_numpy(), color="#C18CFA", lw=.9, alpha=.8, label="ETH 价格（右轴）")
    px.set_ylabel("ETH")
    lines = ax.get_legend_handles_labels()[0] + px.get_legend_handles_labels()[0]
    ax.legend(lines, [x.get_label() for x in lines], loc="lower left", fontsize=8.5, frameon=False)
    for axis in (ax, px):
        axis.spines[["top"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, format="png")
    plt.close(fig)


# ----------------------------------------------------------------------------- html

def fmt(v, digits=2, signed=True, pct=False):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    if pct:
        return f"{v * 100:.1f}%"
    return f"{v:+.{digits}f}" if signed else f"{v:.{digits}f}"


def cls(v):
    return "pos" if isinstance(v, (int, float)) and math.isfinite(v) and v > 0 else (
        "neg" if isinstance(v, (int, float)) and math.isfinite(v) and v < 0 else "")


def table(headers, rows, num_from=1):
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = []
    for r in rows:
        cells = []
        for k, v in enumerate(r):
            if isinstance(v, tuple):
                text, klass = v
                cells.append(f'<td class="{klass}">{text}</td>')
            else:
                cells.append(f'<td{" class=num" if k >= num_from else ""}>{v}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def stat_rows(trades):
    rows = []
    for name, pick in GROUPS:
        m = stats(trades.loc[pick(trades)])
        if not m["n"]:
            continue
        rows.append([name, m["n"], fmt(m["win"], pct=True), fmt(m["gross"]), fmt(-m["fee"]), (fmt(m["mean"]), cls(m["mean"])),
                     (fmt(m["sum"], 1), cls(m["sum"])), fmt(m["pf"], 2, False), f'{fmt(m["avg_win"])} / {fmt(m["avg_loss"])}',
                     f'{m["dd"]:.1f}R', m["streak"], f'{m["ge5"]} / {m["touch5"]}', fmt(m["ctrl"]),
                     (fmt(m["sum_wo5"], 1), cls(m["sum_wo5"]))])
    return rows


STAT_HEAD = ["分组", "笔数", "胜率", "扣费前/笔", "手续费/笔", "扣费后/笔", "合计 R", "盈亏比", "平均赢/亏", "最大回撤",
             "最长连亏", "≥5R 拿住/到过", "随机入场/笔", "去掉最好5笔"]


SUFFIX = ""
COMPARE: Path | None = None
BINANCE_TRADES: Path | None = None


def binance_trades() -> pd.DataFrame:
    """Binance ETH 15m context ledger: the manual-system baseline, or the variant's own ledger."""
    if BINANCE_TRADES is None:
        return pd.read_csv(BINANCE_LEDGER / "trades.csv.gz")
    return pd.read_csv(BINANCE_TRADES).assign(policy="baseline")
LABEL = {"okx": "OKX ETH 15m（2024-02 → 2026-10）", "binance": "币安 ETH 15m（2021-09 → 2026-09）"}
WINDOW = {"full": "全区间", "before_split": "前段", "after_split": "后段"}
ARM_LABEL = {"baseline": "原规则", "no_weekend": "去掉周末", "no_weekend_1216": "去掉周末 + 12–16 点"}
RULE_TEXT = "规则：北京时间周六、周日出现的信号不开新仓；已有持仓照常离场；单子重新按顺序回放（原来被周末单挡住的工作日信号会放出来）。随机对照只从工作日抽。"


def significance(p_value: float) -> str:
    """Wording against the project bar (p < 0.01) rather than a bare 'significant'."""
    if p_value < .01:
        return f"达到项目 p<0.01 的标准（p={p_value:.3f}）"
    if p_value < .05:
        return f"有一定统计强度，但没达到项目 p<0.01 的标准（p={p_value:.2f}）"
    return f"统计上不显著（p={p_value:.2f}）"


def compare_section() -> str:
    if COMPARE is None:
        return ""
    c = pd.read_csv(COMPARE)
    rows = []
    for (venue, window), g in c.groupby(["venue", "window"], sort=False):
        for r in g.itertuples():
            last = r.arm == g.arm.iloc[-1]
            rows.append([LABEL[venue], WINDOW[window], f"<b>{ARM_LABEL[r.arm]}</b>" if last else ARM_LABEL[r.arm], r.closed,
                         fmt(r.win_rate, pct=True), (fmt(r.mean_net_r), cls(r.mean_net_r)), (fmt(r.sum_net_r, 1), cls(r.sum_net_r)),
                         (fmt(r.sum_without_top5, 1), cls(r.sum_without_top5)), fmt(r.pf, 2, False), f"{r.max_drawdown_r:.1f}R",
                         r.max_losses_in_row, r.max_stops_in_row, fmt(r.long_sum_r, 1), fmt(r.short_sum_r, 1),
                         f"{fmt(r.excess_bp, 1)}（p={r.excess_p:.2f}）"])
    return ('<h2 id="cmp">过滤前后对比</h2>'
            f'<p class="sub">{RULE_TEXT}OKX 前段 = 2025-10-10 以前，后段 = 之后；币安前段 = 2024-02-01 以前。</p>'
            + table(["数据", "区间", "版本", "笔数", "胜率", "每笔 R", "合计 R", "去掉最好5笔", "盈亏比", "最大回撤", "最长连亏",
                     "最长连续止损", "多单 R", "空单 R", "比随机多 bp/笔"], rows, 3)
            + '<div class="note">注意：这些过滤条件都是在这几份数据里事后看出来的，所以这里的改善都在样本内，不能当成已经验证的规则；OKX 和币安后段是同一段行情；'
              '真正的检验只能看以后的新数据。</div>')


def build() -> Path:
    d = load()
    frame, trades, tick, cfg = d["frame"], d["trades"], d["tick"], d["cfg"]
    end = pd.Timestamp(cfg["end"])
    recent_start = end - pd.Timedelta(days=RECENT_DAYS)
    OUT.mkdir(parents=True, exist_ok=True)
    CHARTS.mkdir(parents=True, exist_ok=True)
    equity_chart(trades, recent_start, OUT / "equity.png")
    monthly_chart(trades, OUT / "monthly.png")
    recent = trades.copy()

    unique = recent.sort_values("entry_time")
    cards, rows, templates, signal_at = [], [], {}, {}
    for n, row in enumerate(unique.itertuples(), 1):
        name = f"t{n:03d}.jpg"
        trade_chart(row, frame, tick, "", n, CHARTS / name)
        side = "多" if row.side == 1 else "空"
        res = "win" if row.net_r > 0 else "loss"
        tags = f'{"long" if row.side == 1 else "short"} {res}'
        when = _bj([row.signal_close])[0].strftime("%m-%d %H:%M")
        rows.append([f'<a href="#t{n}">#{n}</a>', when, side, f"{row.entry_price:.2f}",
                     f"{row.initial_stop:.2f}", f"{row.initial_risk_frac * 100:.2f}%", _bj([row.exit_time])[0].strftime("%m-%d %H:%M"),
                     f"{row.exit_price:.2f}", REASON.get(row.exit_reason, row.exit_reason), f"{row.hold_h:.1f}h",
                     (fmt(row.net_r), cls(row.net_r)), fmt(row.mfe_known_r, 1, False), fmt(row.control_net_r)])
        templates[n] = (f'<figure class="card {tags}" id="__ID__"><img loading="lazy" src="charts/{name}" alt="#{n}">'
                     f'<figcaption>#{n} · {when} 做{side} · 进场 {row.entry_price:.2f} · '
                     f'止损 {row.initial_stop:.2f}（{row.initial_risk_frac * 100:.2f}%） · {REASON.get(row.exit_reason, row.exit_reason)} '
                     f'{row.exit_price:.2f} · 持仓 {row.hold_h:.1f}h · <b class="{cls(row.net_r)}">净 {row.net_r:+.2f}R</b>'
                     f' · 峰值 {row.mfe_known_r:.1f}R</figcaption></figure>')
        cards.append(templates[n].replace("__ID__", f"t{n}"))
        signal_at[n] = row.signal_close

    two_start = end - pd.Timedelta(days=61)
    two_ns = [n for n, when_ in signal_at.items() if when_ >= two_start]
    two = recent.loc[recent.signal_close >= two_start]
    equity_chart(two, two_start, OUT / "equity_2m.png")
    two_rows = [[f'<a href="#m{n}">#{n}</a>', *rows[n - 1][1:]] for n in two_ns]
    two_cards = [templates[n].replace("__ID__", f"m{n}") for n in two_ns]
    two_stats = stats(two)

    rc = d["receipt"]["summary"]
    windows = []
    for key, label in (("full", "全区间 2024-02 → 10-10"), ("last_12m", "近 12 个月"), ("last_90d", "近 90 天"), ("last_30d", "近 30 天")):
        for g, gl in (("ordinary_all", "普通·全部"), ("ordinary_long", "普通·多"), ("ordinary_short", "普通·空")):
            m = rc[key][g]
            windows.append([label, gl, m["closed"], fmt(m["win_rate"], pct=True), (fmt(m["mean_net_r"]), cls(m["mean_net_r"])),
                            (fmt(m["sum_net_r"], 1), cls(m["sum_net_r"])), fmt(m["control_mean_net_r"]),
                            fmt(m["random_excess"]["mean_excess_bp"], 1), fmt(m["random_excess"]["p_one_sided"], 2, False)])
    yearly = []
    for r in d["yearly"].loc[d["yearly"].group != "joint_long"].itertuples():
        yearly.append([{"ordinary_long": "普通·多", "ordinary_short": "普通·空", "joint_long": "突破+spike"}[r.group], int(r.year),
                       int(r.closed), fmt(r.win_rate, pct=True), (fmt(r.mean_net_r), cls(r.mean_net_r)),
                       (fmt(r.sum_net_r, 1), cls(r.sum_net_r)), fmt(r.pf_r, 2, False), fmt(r.best_net_r, 1)])
    mon = d["monthly"]
    mon = mon.loc[mon.month >= sorted(mon.month.unique())[-12]]
    piv = mon.pivot_table(index="month", columns="group", values=["closed", "sum_net_r", "win_rate"])
    monthly = []
    for mth in piv.index:
        row = [mth]
        for g in ("ordinary_all",):
            n = piv.get(("closed", g), pd.Series()).get(mth, np.nan)
            sr = piv.get(("sum_net_r", g), pd.Series()).get(mth, np.nan)
            wr = piv.get(("win_rate", g), pd.Series()).get(mth, np.nan)
            row += [0 if pd.isna(n) else int(n), fmt(wr, pct=True), (fmt(sr, 1), cls(sr))]
        monthly.append(row)
    o = trades.loc[trades.arm == "ordinary"]
    bins = [(-np.inf, -1, "亏 >1R（满止损+手续费）"), (-1, 0, "−1R 到 0"), (0, 1, "0 到 1R"), (1, 3, "1R 到 3R"),
            (3, 5, "3R 到 5R"), (5, 10, "5R 到 10R"), (10, np.inf, "10R 以上")]
    dist = [[lab, int(((o.net_r >= lo) & (o.net_r < hi)).sum()), f"{((o.net_r >= lo) & (o.net_r < hi)).mean() * 100:.1f}%"]
            for lo, hi, lab in bins]
    reasons = [[REASON.get(k, k), v, f"{v / len(o) * 100:.0f}%"] for k, v in o.exit_reason.value_counts().items()]
    bn = binance_trades()
    bn = bn.loc[(bn.policy == "baseline") & ~bn.censored.astype(bool) & (pd.to_datetime(bn.signal_close, utc=True) < "2024-02-01")]
    cost_rows = []
    for label, g in (("OKX 2024-02 → 2025-10", o.loc[o.signal_close < pd.Timestamp(cfg["split"])]),
                     ("OKX 近 12 个月", o.loc[o.signal_close >= pd.Timestamp(cfg["split"])]),
                     ("币安 2021-09 → 2024-01", bn.loc[bn.arm == "ordinary"])):
        vals = [float((g.gross_r - c / g.initial_risk_frac).mean()) for c in (0, .002, .0015, .001, .0007)]
        cost_rows.append([label, len(g), *[(fmt(v), cls(v)) for v in vals]])
    rng = np.random.default_rng(1010153)

    def shuffle_streak(net_r: np.ndarray) -> list:
        loss = net_r < 0

        def longest(x):
            best = run = 0
            for v in x:
                run = run + 1 if v else 0
                best = max(best, run)
            return best

        sim = np.array([longest(rng.permutation(loss)) for _ in range(20000)])
        obs = longest(loss)
        return [len(loss), obs, f"{sim.mean():.1f}", f"{np.quantile(sim, .95):.0f}", f"{(sim >= obs).mean() * 100:.0f}%"]

    okx_sorted = o.sort_values("entry_time")
    bn_sorted = bn.loc[bn.arm == "ordinary"].sort_values("entry_time")
    streak_rows = [["OKX 普通", *shuffle_streak(okx_sorted.net_r.to_numpy())],
                   ["币安 普通 2021–24", *shuffle_streak(bn_sorted.net_r.to_numpy())]]
    next_rows = []
    run, after = 0, {}
    vals = okx_sorted.net_r.to_numpy()
    for k_ in range(1, len(vals)):
        run = run + 1 if vals[k_ - 1] < 0 else 0
        key = "刚赢一笔" if run == 0 else f"连亏 {min(run, 6)}{'+' if run >= 6 else ''}"
        after.setdefault(key, []).append(vals[k_])
    for key in ["刚赢一笔", *[f"连亏 {i}" for i in range(1, 6)], "连亏 6+"]:
        if key in after:
            v = np.asarray(after[key])
            next_rows.append([key, len(v), fmt(float((v > 0).mean()), pct=True), (fmt(float(v.mean())), cls(float(v.mean())))])
    stop_best = 0
    run = 0
    for v in (okx_sorted.exit_reason == "initial_stop"):
        run = run + 1 if v else 0
        stop_best = max(stop_best, run)
    rec = stats(recent.loc[recent.arm == "ordinary"])
    legs = down_legs(trades.sort_values("exit_time").reset_index(drop=True), threshold=8.0)
    ts = trades.sort_values("exit_time").reset_index(drop=True)
    worst_chart(ts, d["h1"], legs, OUT / "worst.png")
    eqv = np.r_[0.0, ts.net_r.cumsum().to_numpy()]
    stamp = lambda k: ts.exit_time.iloc[0] - pd.Timedelta(minutes=15) if k == 0 else ts.exit_time.iloc[k - 1]
    leg_rows = []
    for n, (a, b) in enumerate(sorted(legs, key=lambda ab: eqv[ab[1]] - eqv[ab[0]]), 1):
        g = ts.iloc[a:b]
        p0, p1, chg, rng = eth_move(d["h1"], stamp(a), stamp(b))
        days = (stamp(b) - stamp(a)).days
        leg_rows.append([n, f"{_bj([stamp(a)])[0]:%Y-%m-%d} → {_bj([stamp(b)])[0]:%Y-%m-%d}", f"{days} 天",
                         (fmt(eqv[b] - eqv[a], 1), "neg"), len(g), fmt((g.net_r > 0).mean(), pct=True),
                         fmt(g.loc[g.side == 1].net_r.sum(), 1), fmt(g.loc[g.side == -1].net_r.sum(), 1),
                         f"{p0:.0f} → {p1:.0f}（{chg:+.1f}%）", f"{rng:.0f}%"])
    win_rows = []
    for days in (30, 90, 180):
        i, j, total = worst_window(ts, days)
        g = ts.iloc[i:j + 1]
        p0, p1, chg, rng = eth_move(d["h1"], ts.exit_time.iloc[i], ts.exit_time.iloc[j])
        win_rows.append([f"最差 {days} 天", f"{_bj([ts.exit_time.iloc[i]])[0]:%Y-%m-%d} → {_bj([ts.exit_time.iloc[j]])[0]:%Y-%m-%d}",
                         (fmt(total, 1), "neg"), len(g), fmt((g.net_r > 0).mean(), pct=True),
                         fmt(g.loc[g.side == 1].net_r.sum(), 1), fmt(g.loc[g.side == -1].net_r.sum(), 1),
                         f"{p0:.0f} → {p1:.0f}（{chg:+.1f}%）", f"{rng:.0f}%"])
    ts["month"] = _bj(ts.exit_time).strftime("%Y-%m")
    ts["quarter"] = _bj(ts.exit_time).to_period("Q").astype(str)
    mm = ts.groupby("month").agg(n=("net_r", "size"), r=("net_r", "sum"), w=("net_r", lambda r: (r > 0).mean()))
    month_rows = [[m, int(v.n), fmt(v.w, pct=True), (fmt(v.r, 1), cls(v.r))] for m, v in mm.sort_values("r").head(6).iterrows()]
    best_rows = [[m, int(v.n), fmt(v.w, pct=True), (fmt(v.r, 1), cls(v.r))] for m, v in mm.sort_values("r", ascending=False).head(4).iterrows()]
    qq = ts.groupby("quarter").agg(n=("net_r", "size"), r=("net_r", "sum"), w=("net_r", lambda r: (r > 0).mean()))
    quarter_rows = [[q, int(v.n), fmt(v.w, pct=True), (fmt(v.r, 1), cls(v.r))] for q, v in qq.iterrows()]
    dd_all = stats(ts)["dd"]
    names = "一二三四五六日"
    wk = trades.assign(wd=_bj(trades.signal_close).dayofweek, blk=(_bj(trades.signal_close).hour // 6) * 6)
    week_rows = []
    for wd, g in wk.groupby("wd"):
        week_rows.append([f"周{names[wd]}", len(g), fmt((g.net_r > 0).mean(), pct=True), fmt(g.gross_r.mean()),
                          (fmt(g.net_r.mean()), cls(g.net_r.mean())), (fmt(g.net_r.sum(), 1), cls(g.net_r.sum())),
                          (fmt(g.loc[g.side == 1].net_r.sum(), 1), cls(g.loc[g.side == 1].net_r.sum())),
                          (fmt(g.loc[g.side == -1].net_r.sum(), 1), cls(g.loc[g.side == -1].net_r.sum())),
                          fmt(g.loc[g.matched].control_net_r.mean())])
    bn_all = binance_trades()
    bn_all = bn_all.loc[(bn_all.policy == "baseline") & (bn_all.arm == "ordinary") & ~bn_all.censored.astype(bool)].copy()
    bn_all["signal_close"] = pd.to_datetime(bn_all.signal_close, utc=True)
    bn_all = bn_all.loc[bn_all.signal_close < pd.Timestamp("2024-02-01T00:00:00Z")]
    split = pd.Timestamp(cfg["split"])
    samples = (("OKX 2024-02 → 2025-10", wk.loc[wk.signal_close < split]), ("OKX 近 12 个月", wk.loc[wk.signal_close >= split]),
               ("币安 2021-09 → 2024-01", bn_all.assign(wd=_bj(bn_all.signal_close).dayofweek)))
    week_cross = []
    for wd in range(7):
        row = [f"周{names[wd]}"]
        for _, g in samples:
            r = g.loc[g.wd == wd].net_r
            row.append((f"{fmt(r.mean())}（{len(r)}）" if len(r) else "–", cls(r.mean()) if len(r) else ""))
        week_cross.append(row)
    weekend_rows = []
    for label, g in samples:
        we = g.loc[g.wd >= 5].net_r
        wdys = g.loc[g.wd < 5].net_r
        t_stat = ((we.mean() - wdys.mean()) / math.sqrt(we.var() / len(we) + wdys.var() / len(wdys))
                  if len(we) > 1 and len(wdys) > 1 else math.nan)
        weekend_rows.append([label, len(we), (fmt(we.mean()), cls(we.mean())), (fmt(we.sum(), 1), cls(we.sum())), len(wdys),
                             (fmt(wdys.mean()), cls(wdys.mean())), (fmt(wdys.sum(), 1), cls(wdys.sum())),
                             fmt(we.mean() - wdys.mean()), f"{t_stat:.2f}"])
    heat = wk.pivot_table(index="wd", columns="blk", values="net_r", aggfunc=["sum", "size"])
    heat_rows = []
    for wd in range(7):
        row = [f"周{names[wd]}"]
        for blk in (0, 6, 12, 18):
            n_ = heat.get(("size", blk), pd.Series()).get(wd, np.nan)
            v = heat.get(("sum", blk), pd.Series()).get(wd, np.nan)
            row.append(("–", "") if pd.isna(n_) else (f"{fmt(v, 1)}（{int(n_)}）", cls(v)))
        heat_rows.append(row)
    bbw = frame.assign(wd=_bj(frame.index).dayofweek).dropna(subset=["bb_compressed"])
    bb_rows = [[f"周{names[wd]}", f"{g.bb_compressed.mean() * 100:.0f}%", f"{g.volume.median():,.0f}",
                f"{((g.high - g.low) / g.close).median() * 1e4:.0f} bp", int((wk.wd == wd).sum())]
               for wd, g in bbw.groupby("wd")]
    worst_day = min(week_rows, key=lambda r: float(r[5][0]))

    css = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1f2937;--mute:#6b7280;--line:#e5e7eb;--pos:#089981;--neg:#e11d48;--acc:#AD7B29}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,"PingFang SC","Hiragino Sans GB",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 80px}h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 10px;padding-top:8px;border-top:1px solid var(--line)}
h3{font-size:15px;margin:18px 0 8px}.sub{color:var(--mute);margin:0 0 14px}nav{position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:5;border-bottom:1px solid var(--line);display:flex;gap:14px;flex-wrap:wrap;font-size:13px}
nav a{color:var(--ink);text-decoration:none}nav a:hover{color:var(--acc)}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin:14px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.kpi b{display:block;font-size:22px}.kpi span{color:var(--mute);font-size:12px}
.tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0 6px}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left}th{background:#fafaf9;font-weight:600;position:sticky;top:0}td.num{font-variant-numeric:tabular-nums}
.pos{color:var(--pos);font-weight:600}.neg{color:var(--neg);font-weight:600}img.wide{width:100%;background:#fff;border:1px solid var(--line);border-radius:10px}
.note{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:10px 14px;margin:10px 0}.filters{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
.filters button{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:5px 12px;cursor:pointer;font-size:13px}.filters button.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.card{margin:0 0 18px;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}.card img{width:100%;display:block}.card figcaption{padding:8px 12px;font-size:13px;color:#374151}
ul{padding-left:20px}li{margin:3px 0}
"""
    js = """
document.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('.filters button').forEach(x=>x.classList.remove('on'));b.classList.add('on');
 const f=b.dataset.f;document.querySelectorAll('.card').forEach(c=>{c.style.display=(f==='all'||c.classList.contains(f))?'':'none'});});
"""
    k = rc["full"]
    body = f"""
<main>
<h1>SPIKE V12.9 · OKX ETHUSDT.P 15m 回测{SUFFIX}</h1>
<p class="sub">回测区间 2024-02-01 至 2026-10-10 18:00（北京时间）· 只做普通信号（多空）· 方向过滤：收盘在上一根已收盘 1h SMA60 上方才做多、下方才做空 · 下一根 15m 开盘价进场 · 止损 = 前 5 根极值外 0.2ATR（至少 2ATR）· 收盘浮盈到 2R 后启动 4ATR 跟踪止损 · 反向信号离场 · 往返成本 0.2% · R = 初始止损距离</p>
<nav>{'<a href="#cmp">过滤前后对比</a>' if COMPARE else ''}<a href="#sum">总览</a><a href="#two">最近两个月</a><a href="#week">按星期</a><a href="#worst">最亏时段</a><a href="#curve">资金曲线</a><a href="#time">按时间</a><a href="#dist">盈亏分布</a><a href="#cost">成本</a><a href="#streak">连亏</a><a href="#recent">全部逐笔</a><a href="#limits">说明</a></nav>
<div class="kpis">
<div class="kpi"><span>普通信号（多空）</span><b>{k['ordinary_all']['closed']} 笔</b><span>胜率 {k['ordinary_all']['win_rate'] * 100:.1f}%</span></div>
<div class="kpi"><span>每笔 / 合计</span><b class="{cls(k['ordinary_all']['mean_net_r'])}">{k['ordinary_all']['mean_net_r']:+.2f}R</b><span>合计 {k['ordinary_all']['sum_net_r']:+.1f}R · 去掉最好 5 笔 {k['ordinary_all']['sum_net_r_without_top5']:+.1f}R</span></div>
<div class="kpi"><span>最大回撤 / 最长连亏</span><b class="neg">{dd_all:.1f}R</b><span>最长连亏 {k['ordinary_all']['max_consecutive_losses']} 笔 · 最长连续止损 {stop_best} 次</span></div>
<div class="kpi"><span>前段 → 近 12 个月</span><b>{rc['earlier']['ordinary_all']['sum_net_r']:+.1f}R → {rc['last_12m']['ordinary_all']['sum_net_r']:+.1f}R</b><span>{rc['earlier']['ordinary_all']['closed']} 笔 → {rc['last_12m']['ordinary_all']['closed']} 笔</span></div>
<div class="kpi"><span>最亏的星期</span><b class="neg">{worst_day[0]} {worst_day[5][0]}R</b><span>{worst_day[1]} 笔 · 每笔 {worst_day[4][0]}R</span></div>
</div>
<div class="note">结论：扣 0.2% 成本后 {k['ordinary_all']['closed']} 笔合计 {k['ordinary_all']['sum_net_r']:+.1f}R（每笔 {k['ordinary_all']['mean_net_r']:+.2f}R），但去掉最好的 5 笔是 {k['ordinary_all']['sum_net_r_without_top5']:+.1f}R；
比同周、同波动的随机入场每笔多 {k['ordinary_all']['random_excess']['mean_excess_bp']:.1f}bp，{significance(k['ordinary_all']['random_excess']['p_one_sided'])}。
前段（2024-02 → 2025-10）{rc['earlier']['ordinary_all']['sum_net_r']:+.1f}R，近 12 个月 {rc['last_12m']['ordinary_all']['sum_net_r']:+.1f}R，后者主要来自做空（{rc['last_12m']['ordinary_short']['sum_net_r']:+.1f}R），同期随机做空每笔 {rc['last_12m']['ordinary_short']['control_mean_net_r']:+.2f}R{'（ETH 在跌，随机做空也赚）' if rc['last_12m']['ordinary_short']['control_mean_net_r'] > 0 else ''}。
多单全区间 {k['ordinary_long']['sum_net_r']:+.1f}R。
15m 止损比 5m 宽（中位 {k['ordinary_all']['median_risk_bp'] / 100:.2f}%），手续费每笔只吃 {k['ordinary_all']['mean_fee_r']:.2f}R，这是它比 5m 好的主要原因。
</div>

{compare_section()}
<h2 id="sum">总览（全区间）</h2>
{table(STAT_HEAD, stat_rows(trades))}
<h3>分时间窗</h3>
{table(["区间", "分组", "笔数", "胜率", "每笔 R", "合计 R", "随机入场/笔 R", "比随机多 bp/笔", "p（单边）"], windows, 2)}

<h2 id="two">最近两个月逐笔（{_bj([two_start])[0]:%Y-%m-%d} 起，{len(two_ns)} 笔）</h2>
{table(STAT_HEAD, stat_rows(two))}
<img class="wide" src="equity_2m.png" alt="最近两个月资金曲线">
<h3>交易列表（编号与「全部逐笔」一致，点编号跳到图）</h3>
{table(["#", "信号（北京）", "方向", "进场", "止损", "止损宽度", "离场（北京）", "离场价", "离场方式", "持仓", "净 R", "峰值 R", "随机对照 R"], two_rows)}
<p class="sub">这两个月：{two_stats['n']} 笔，胜率 {two_stats['win'] * 100:.0f}%，合计 {two_stats['sum']:+.1f}R，最长连亏 {two_stats['streak']} 笔。图例同下文：红框 = 进场到初始止损，绿框 = 进场到峰值，虚线 = 离场价，深绿阶梯线 = 跟踪止损，紫线 = 已收盘 1h SMA60。</p>
{''.join(two_cards)}

<h2 id="week">按星期（北京时间，按信号出现的那天）</h2>
{table(["星期", "笔数", "胜率", "扣费前/笔", "扣费后/笔", "合计 R", "其中多单 R", "其中空单 R", "随机入场/笔"], week_rows)}
<p class="sub">周日只有 00–08 点有信号：V9 规则本身不在 UTC 周日开新单（北京周日 08:00 到周一 08:00），所以周一也只有 08 点以后的信号。</p>
<h3>三段数据分开看（每笔 R，括号里是笔数）</h3>
{table(["星期", "OKX 2024-02 → 2025-10", "OKX 近 12 个月", "币安 2021-09 → 2024-01"], week_cross)}
<h3>周末（北京周六、周日）对比工作日</h3>
{table(["样本", "周末笔数", "周末每笔", "周末合计", "工作日笔数", "工作日每笔", "工作日合计", "差", "t"], weekend_rows)}
<h3>星期 × 时段（合计 R，括号里是笔数）</h3>
{table(["星期", "00–06 点", "06–12 点", "12–18 点", "18–24 点"], heat_rows)}
<h3>各天被判为「压缩」的 K 线比例</h3>
{table(["星期", "被 BB 规则算作压缩的 15m K 线占比", "15m 成交量中位数", "15m 振幅中位数", "信号笔数"], bb_rows)}
<p class="sub">BB 压缩条件拿当前带宽和「此前 500 根 15m（约 5.2 天）」比较。15m 的比较窗口跨过整个周末，周末与工作日的差别会比 5m 小；上表直接给出各天被判为压缩的比例，可与 5m 页面对照。按星期的差别是事后统计，样本小时容易是噪声。</p>

<h2 id="worst">按日期：哪段时间亏损最多</h2>
<img class="wide" src="worst.png" alt="最亏时段">
<p class="sub">黑线 = 普通信号累计 R（按离场时间）；紫线 = ETH 价格（右轴）；红色阴影 = 亏得最多的三段下跌（编号同下表）。下跌段按资金曲线回撤 8R 以上切分。</p>
<h3>资金曲线的下跌段（从局部高点到局部低点）</h3>
{table(["#", "时间（北京）", "长度", "这段亏损", "笔数", "胜率", "其中多单 R", "其中空单 R", "ETH 价格", "ETH 振幅"], leg_rows)}
<h3>滚动窗口里最差的一段</h3>
{table(["窗口", "时间（北京）", "亏损", "笔数", "胜率", "其中多单 R", "其中空单 R", "ETH 价格", "ETH 振幅"], win_rows)}
<h3>最亏的 6 个月 · 最赚的 4 个月（按离场月份）</h3>
{table(["月份", "笔数", "胜率", "合计 R"], month_rows)}
{table(["月份", "笔数", "胜率", "合计 R"], best_rows)}
<h3>按季度</h3>
{table(["季度", "笔数", "胜率", "合计 R"], quarter_rows)}

<h2 id="curve">资金曲线（累计 R，按离场时间）</h2>
<img class="wide" src="equity.png" alt="资金曲线">
<p class="sub">虚线是扣费前。实线和虚线之间的差，就是 0.2% 往返成本累计吃掉的 R。</p>

<h2 id="time">按时间</h2>
<img class="wide" src="monthly.png" alt="月度">
<h3>按年</h3>
{table(["分组", "年份", "笔数", "胜率", "每笔 R", "合计 R", "盈亏比", "最好一笔 R"], yearly, 1)}
<h3>近 12 个月逐月</h3>
{table(["月份", "笔数", "胜率", "合计 R"], monthly)}

<h2 id="dist">盈亏分布（普通信号 {len(o)} 笔）</h2>
{table(["平仓结果", "笔数", "占比"], dist)}
{table(["离场方式", "笔数", "占比"], reasons)}

<h2 id="cost">成本敏感度（普通信号，每笔平均 R）</h2>
{table(["区间", "笔数", "扣费前", "0.20%（项目口径）", "0.15%", "0.10%", "0.07%"], cost_rows)}
<p class="sub">OKX 普通用户吃单单边 0.05%，来回 0.10%（未算滑点）。币安一行是 9-25 那次 ETH 15m 回放（同一套代码，逐笔复现）的 2021-09 到 2024-01 段。</p>

<h2 id="streak">连亏</h2>
{table(["样本", "笔数", "实际最长连亏", "打乱顺序后平均", "打乱后 95% 分位", "打乱后出现同样长连亏的比例"], streak_rows)}
{table(["前面的情况（OKX 普通）", "笔数", "下一笔胜率", "下一笔平均 R"], next_rows)}
<p class="sub">连亏长度和随机打乱后的结果没有区别；连亏之后下一笔的胜率也不会变高，倍投没有依据。</p>

<h2 id="recent">全部逐笔（{len(unique)} 笔）</h2>
<h3>交易列表</h3>
{table(["#", "信号（北京）", "方向", "进场", "止损", "止损宽度", "离场（北京）", "离场价", "离场方式", "持仓", "净 R", "峰值 R", "随机对照 R"], rows)}
<h3>逐笔图</h3>
<p class="sub">红框 = 进场到初始止损；绿框 = 进场到持仓期间的峰值；虚线 = 实际离场价；深绿阶梯线 = 跟踪止损；紫线 = 已收盘 1h SMA60（15m 方向过滤线）；三角 = 信号 K。图中按下一根开盘价成交，所以 R 值和 TradingView 面板（按信号收盘价算）会略有差别。</p>
<div class="filters"><button class="on" data-f="all">全部</button><button data-f="long">做多</button><button data-f="short">做空</button><button data-f="win">盈利</button><button data-f="loss">亏损</button></div>
{''.join(cards)}

<h2 id="limits">说明与局限</h2>
<ul>
<li>数据：OKX 官方 1m 归档 + 已确认的 1m 接口数据（与 5m 回测同一份），聚合成完整 5m/15m K 线，无缺口。同一套代码在币安数据上逐笔复现了 9-25 那次 ETH 15m 的 267 笔。</li>
<li>本页按你的要求去掉了突破+spike（只做多的联合信号），账本本身没改；需要时可以加回来。</li>
<li>两次加仓提示只是显示，回测没有执行；R 简单相加，不复利。</li>
<li>只开一个仓：持仓期间出现的信号跳过；反向信号只平仓、不反手。</li>
<li>5m 页面的规律审计和出场测试没有在 15m 上做。</li>
</ul>
</main>"""
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SPIKE V12.9 ETH 15m 回测{SUFFIX}</title><style>{css}</style></head><body>{body}<script>{js}</script></body></html>"""
    path = OUT / "index.html"
    path.write_text(page)
    return path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SPIKE V12.9 OKX ETH 15m HTML view")
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--suffix", default="")
    parser.add_argument("--compare", type=Path, default=None)
    parser.add_argument("--binance-trades", type=Path, default=None)
    parser.add_argument("--rule-text", default=None)
    args = parser.parse_args()
    if args.rule_text:
        RULE_TEXT = args.rule_text
    RUN, OUT, CHARTS, SUFFIX, COMPARE = args.run, args.out, args.out / "charts", args.suffix, args.compare
    BINANCE_TRADES = args.binance_trades
    print(build().resolve())
