"""Owner-requested HTML view of the SPIKE V12.9 OKX ETH 5m backtest (2026-10-10).

Owner: "给我一个html. 我看看详细的回测结果 并且针对最近半年的回测要给我图 就像我notion里那个图片一样".
The Notion reference is a TradingView V12.9 5m screenshot of the 2026-10-08 16:45 short:
red risk box (entry..initial stop), green box down to the peak, dashed exit line with
its R, peak R label, six MAs, purple completed-15m EMA120 step line and R guides.

This builder only reads committed ledgers and receipts (run_v1, patterns_v1, the
exit test results and the Binance parent ledger for cost/streak context); it does
not change any rule. Trade charts redraw each closed trade of the last 183 days
from the same OKX 5m bars; the trailing-stop path is recomputed with the incumbent
rule (2R close arms a 4ATR close trail, effective next bar) and must end at the
recorded exit price for every trailing-stop exit.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v129-eth5m-20261010-v1/build_report.py
Output (local, untracked): experiments/active/exp-spike-v129-eth5m-20261010-v1/report_v1/
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
from yoyo.evaluation import spike_v129_eth5m as s  # noqa: E402

EXP = s.EXP
RUN = EXP / "run_v1"
OUT = EXP / "report_v1"
CHARTS = OUT / "charts"
EXIT_EXP = Path("experiments/active/exp-spike-v129-eth5m-exit-20261010-v1/results_v1")
BINANCE_LEDGER = Path("experiments/active/exp-spike-manual-system-20260925-v1/run_v1/streams/ETHUSDT_5m")
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
    cfg = s.config()
    data = json.loads((Path(cfg["data_dir"]) / "receipt.json").read_text())
    base = low.load_base([Path(x) for x in data["inputs"]], pd.Timestamp(cfg["end"]))
    bars, _ = low.complete_bars(base, 5)
    facts = low.pine_facts(bars, bars, cfg["asset"], float(cfg["tick"]), 5)
    frame = facts["frame"].copy()
    frame["ema120_15m"] = np.asarray(facts["ma"], float)
    mid = frame.close.rolling(200).mean()
    sd = frame.close.rolling(200).std(ddof=0)
    frame["bb_up"], frame["bb_dn"] = mid + 2 * sd, mid - 2 * sd
    trades = pd.read_csv(RUN / "trades.csv.gz")
    for col in ("signal_close", "entry_time", "exit_time"):
        trades[col] = pd.to_datetime(trades[col], utc=True)
    ctrl = pd.read_csv(RUN / "controls.csv.gz").set_index("trade_key")
    trades["control_net_r"] = trades.trade_key.map(ctrl.control_net_r)
    trades["matched"] = trades.trade_key.map(ctrl.matched).fillna(False).astype(bool)
    trades = trades.loc[~trades.censored.astype(bool)].copy()
    trades["net_bp"] = trades.net_return * 1e4
    trades["fee_r"] = .002 / trades.initial_risk_frac
    trades["hold_h"] = (trades.exit_time - trades.entry_time).dt.total_seconds() / 3600
    receipt = json.loads((RUN / "receipt.json").read_text())
    return {"cfg": cfg, "frame": frame, "trades": trades, "receipt": receipt,
            "yearly": pd.read_csv(RUN / "yearly.csv"), "monthly": pd.read_csv(RUN / "monthly.csv"),
            "streaks": json.loads((RUN / "patterns_v1" / "streaks.json").read_text()),
            "stability": pd.read_csv(RUN / "patterns_v1" / "stability.csv"),
            "exit": pd.read_csv(EXIT_EXP / "summary.csv"), "tick": float(cfg["tick"])}


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
          ("普通·空", lambda t: (t.arm == "ordinary") & (t.side == -1)), ("突破+spike", lambda t: t.arm == "joint"))


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
    j = t.loc[t.arm == "joint"].groupby("month").net_r.sum().reindex(months, fill_value=0)
    x = np.arange(len(months))
    fig, ax = plt.subplots(figsize=(11, 3.4), dpi=110)
    ax.bar(x - .2, o, .4, color=[UP if v > 0 else DOWN for v in o], label="普通（多空）")
    ax.bar(x + .2, j, .4, color=[UP if v > 0 else DOWN for v in j], alpha=.45, hatch="//", edgecolor="#555", lw=0, label="突破+spike")
    ax.set_xticks(x, [m[2:] for m in months], fontsize=8.5)
    ax.axhline(0, color="#9ca3af", lw=.8)
    ax.set_ylabel("当月合计 R")
    ax.legend(fontsize=8.5, frameon=False)
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
    ax.step(pos, frame.ema120_15m.to_numpy(float)[idx], where="post", color="#C18CFA", lw=2, label="15m EMA120")
    e0, x0 = e - a, x - a
    mfe = float(row.mfe_known_r) if math.isfinite(row.mfe_known_r) else 0.0
    peak = entry + side * max(mfe, 0) * risk
    ax.add_patch(plt.Rectangle((e0 - .4, min(entry, stop)), x0 - e0 + .8, risk, color=DOWN, alpha=.16, lw=0))
    if mfe > 0:
        ax.add_patch(plt.Rectangle((e0 - .4, min(entry, peak)), x0 - e0 + .8, abs(peak - entry), color=UP, alpha=.13, lw=0))
    ax.hlines(stop, e0 - .4, x0 + .4, color=DOWN, lw=1)
    ax.hlines(entry, e0 - .4, x0 + .4, color="#374151", lw=1)
    for k in (1, 2, 3, 5, 10):
        if k <= max(3, mfe + .5):
            y = entry + side * k * risk
            ax.hlines(y, e0, x0, color=UP, lw=.7, ls=(0, (4, 4)), alpha=.55)
            ax.text(x0 + .6, y, f"{k}R", fontsize=7, color=UP, va="center", alpha=.8)
    armed = prot != prot[0]
    if armed.any():
        pp = np.where(armed, prot, np.nan)
        ax.step(np.arange(e0, x0 + 1), pp, where="post", color="#0f766e", lw=1.4)
    xp = float(row.exit_price)
    ax.hlines(xp, e0, x0 + 3, color=UP if row.net_r > 0 else DOWN, lw=1.3, ls="--")
    ax.text(x0 + 3.5, xp, f"离场 {row.net_r:+.2f}R", fontsize=8.5, va="center",
            color=UP if row.net_r > 0 else DOWN, fontweight="bold")
    ax.text(x0 + .6, stop, f"止损 {stop:.2f}", fontsize=8, color=DOWN, va="center")
    ax.text(e0 - 1, entry, f"进场 {entry:.2f} ", fontsize=8, color="#374151", va="center", ha="right")
    if mfe > 0:
        ax.text(e0 + 1, peak, f"峰值 {mfe:.1f}R", fontsize=8, color=UP, va="bottom" if side == 1 else "top")
    marker_y = l[sig] - .4 * atr[sig] if side == 1 else h[sig] + .4 * atr[sig]
    ax.scatter([sig - a], [marker_y], marker="^" if side == 1 else "v", s=70, color=UP if side == 1 else DOWN, zorder=5)
    lo = min(l[idx].min(), stop, peak, xp)
    hi = max(h[idx].max(), stop, peak, xp)
    pad = (hi - lo) * .06
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
    ax.set_title(f"#{number}  {when}  {'做多' if side == 1 else '做空'} · {arms}  ·  {REASON.get(row.exit_reason, row.exit_reason)}"
                 f"  ·  净 {row.net_r:+.2f}R", loc="left", fontsize=10.5)
    fig.savefig(path, format="jpg", pil_kwargs={"quality": 82, "optimize": True})
    plt.close(fig)
    return {"bars": len(idx)}


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


def build() -> Path:
    d = load()
    frame, trades, tick, cfg = d["frame"], d["trades"], d["tick"], d["cfg"]
    end = pd.Timestamp(cfg["end"])
    recent_start = end - pd.Timedelta(days=RECENT_DAYS)
    OUT.mkdir(parents=True, exist_ok=True)
    CHARTS.mkdir(parents=True, exist_ok=True)
    equity_chart(trades, recent_start, OUT / "equity.png")
    monthly_chart(trades, OUT / "monthly.png")
    recent = trades.loc[trades.signal_close >= recent_start].copy()
    equity_chart(recent, recent_start, OUT / "equity_recent.png")

    # One chart per distinct position (ordinary and joint share entries when they coincide).
    recent["pos_key"] = recent.entry_i.astype(str) + ":" + recent.side.astype(str)
    arms = recent.groupby("pos_key").arm.agg(lambda a: " + ".join(ARM[x] for x in sorted(set(a), reverse=True)))
    unique = recent.sort_values(["entry_time", "arm"], ascending=[True, False]).drop_duplicates("pos_key")
    cards, rows = [], []
    for n, row in enumerate(unique.itertuples(), 1):
        name = f"t{n:03d}.jpg"
        trade_chart(row, frame, tick, arms[row.pos_key], n, CHARTS / name)
        side = "多" if row.side == 1 else "空"
        res = "win" if row.net_r > 0 else "loss"
        tags = f'{"long" if row.side == 1 else "short"} {res} {"joint" if "突破" in arms[row.pos_key] else ""}'
        when = _bj([row.signal_close])[0].strftime("%m-%d %H:%M")
        rows.append([f'<a href="#t{n}">#{n}</a>', when, side, arms[row.pos_key], f"{row.entry_price:.2f}",
                     f"{row.initial_stop:.2f}", f"{row.initial_risk_frac * 100:.2f}%", _bj([row.exit_time])[0].strftime("%m-%d %H:%M"),
                     f"{row.exit_price:.2f}", REASON.get(row.exit_reason, row.exit_reason), f"{row.hold_h:.1f}h",
                     (fmt(row.net_r), cls(row.net_r)), fmt(row.mfe_known_r, 1, False), fmt(row.control_net_r)])
        cards.append(f'<figure class="card {tags}" id="t{n}"><img loading="lazy" src="charts/{name}" alt="#{n}">'
                     f'<figcaption>#{n} · {when} 做{side} · {html.escape(arms[row.pos_key])} · 进场 {row.entry_price:.2f} · '
                     f'止损 {row.initial_stop:.2f}（{row.initial_risk_frac * 100:.2f}%） · {REASON.get(row.exit_reason, row.exit_reason)} '
                     f'{row.exit_price:.2f} · 持仓 {row.hold_h:.1f}h · <b class="{cls(row.net_r)}">净 {row.net_r:+.2f}R</b>'
                     f' · 峰值 {row.mfe_known_r:.1f}R</figcaption></figure>')

    rc = d["receipt"]["summary"]
    windows = []
    for key, label in (("full", "全区间 2024-02 → 10-10"), ("last_12m", "近 12 个月"), ("last_90d", "近 90 天"), ("last_30d", "近 30 天")):
        for g, gl in (("ordinary_all", "普通·全部"), ("joint_long", "突破+spike")):
            m = rc[key][g]
            windows.append([label, gl, m["closed"], fmt(m["win_rate"], pct=True), (fmt(m["mean_net_r"]), cls(m["mean_net_r"])),
                            (fmt(m["sum_net_r"], 1), cls(m["sum_net_r"])), fmt(m["control_mean_net_r"]),
                            fmt(m["random_excess"]["mean_excess_bp"], 1), fmt(m["random_excess"]["p_one_sided"], 2, False)])
    yearly = []
    for r in d["yearly"].itertuples():
        yearly.append([{"ordinary_long": "普通·多", "ordinary_short": "普通·空", "joint_long": "突破+spike"}[r.group], int(r.year),
                       int(r.closed), fmt(r.win_rate, pct=True), (fmt(r.mean_net_r), cls(r.mean_net_r)),
                       (fmt(r.sum_net_r, 1), cls(r.sum_net_r)), fmt(r.pf_r, 2, False), fmt(r.best_net_r, 1)])
    mon = d["monthly"]
    mon = mon.loc[mon.month >= sorted(mon.month.unique())[-12]]
    piv = mon.pivot_table(index="month", columns="group", values=["closed", "sum_net_r", "win_rate"])
    monthly = []
    for mth in piv.index:
        row = [mth]
        for g in ("ordinary_all", "joint_long"):
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
    bn = pd.read_csv(BINANCE_LEDGER / "trades.csv.gz")
    bn = bn.loc[(bn.policy == "baseline") & ~bn.censored.astype(bool) & (pd.to_datetime(bn.signal_close, utc=True) < "2024-02-01")]
    cost_rows = []
    for label, g in (("OKX 2024-02 → 2025-10", o.loc[o.signal_close < pd.Timestamp(cfg["split"])]),
                     ("OKX 近 12 个月", o.loc[o.signal_close >= pd.Timestamp(cfg["split"])]),
                     ("币安 2021-09 → 2024-01", bn.loc[bn.arm == "ordinary"])):
        vals = [float((g.gross_r - c / g.initial_risk_frac).mean()) for c in (0, .002, .0015, .001, .0007)]
        cost_rows.append([label, len(g), *[(fmt(v), cls(v)) for v in vals]])
    st = d["streaks"]
    streak_rows = [[lab, st[k]["trades"], st[k]["longest_loss_streak"], f'{st[k]["shuffled_mean"]:.1f}',
                    f'{st[k]["shuffled_p95"]:.0f}', f'{st[k]["p_shuffled_ge_observed"] * 100:.0f}%']
                   for k, lab in (("okx_ordinary", "OKX 普通"), ("okx_joint", "OKX 突破+spike"), ("binance_ordinary_2021_2024", "币安 普通 2021–24"))]
    nxt = st["okx_ordinary"]["next_trade"]
    next_rows = [[k.replace("after_win", "刚赢一笔").replace("after_", "连亏 ").replace("_losses", ""), v["n"],
                  fmt(v["win_rate"], pct=True), (fmt(v["mean_net_r"]), cls(v["mean_net_r"]))] for k, v in nxt.items()]
    names = {"atr_pct": "5m ATR%", "risk_pct": "止损宽度", "rv": "放量倍数", "momentum": "动量", "rope_dist": "离六线距离",
             "ema_dist": "离15m EMA120", "ret24": "前24小时同向涨跌", "er7d": "7日趋势效率", "gap_prev_h": "离上个信号小时数",
             "h1_aligned": "顺1h趋势", "h4_aligned": "顺4h趋势", "d1_aligned": "顺日线趋势", "prev_loss": "上一笔亏损",
             "hour_00": "北京 00–06点", "hour_06": "北京 06–12点", "hour_12": "北京 12–18点", "hour_18": "北京 18–24点", "saturday": "周六"}
    stab = [[names.get(r.factor, r.factor), (fmt(r.okx_earlier_diff_r), cls(r.okx_earlier_diff_r)),
             (fmt(r.okx_last12m_diff_r), cls(r.okx_last12m_diff_r)), (fmt(r.binance_2021_2024_diff_r), cls(r.binance_2021_2024_diff_r)),
             "是" if r.stable else "否"] for r in d["stability"].itertuples()]
    ex = d["exit"]
    ex = ex.loc[(ex.arm == "ordinary") & (ex.cohort == "signal")]
    pol = {"incumbent": "现规则：2R 启动，跟踪 4ATR", "trail3": "跟踪 3ATR", "trail2": "跟踪 2ATR", "arm1": "1R 就启动跟踪",
           "be1": "到 1R 移保本", "lock1": "到 2R 后锁峰值−1R", "half2": "到 2R 先平一半"}
    exit_rows = []
    for p_, lab in pol.items():
        g = ex.loc[ex.policy == p_].set_index("sample")
        exit_rows.append([lab] + [(fmt(g.loc[sm, "mean_net_r"]), cls(g.loc[sm, "mean_net_r"])) for sm in ("okx_select", "okx_check", "binance_check")]
                         + [" / ".join(str(int(g.loc[sm, "n_ge_5r"])) for sm in ("okx_select", "okx_check", "binance_check"))])
    rec = stats(recent.loc[recent.arm == "ordinary"])
    rj = stats(recent.loc[recent.arm == "joint"])

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
<h1>SPIKE V12.9 · OKX ETHUSDT.P 5m 回测</h1>
<p class="sub">回测区间 2024-02-01 至 2026-10-10 18:00（北京时间）· 下一根 5m 开盘价进场 · 止损 = 前 5 根极值外 0.2ATR（至少 2ATR）· 收盘浮盈到 2R 后启动 4ATR 跟踪止损 · 反向信号离场 · 往返成本 0.2% · R = 初始止损距离</p>
<nav><a href="#sum">总览</a><a href="#curve">资金曲线</a><a href="#time">按时间</a><a href="#dist">盈亏分布</a><a href="#cost">成本</a><a href="#streak">连亏</a><a href="#pattern">规律审计</a><a href="#exit">出场测试</a><a href="#recent">最近半年逐笔</a><a href="#limits">说明</a></nav>
<div class="kpis">
<div class="kpi"><span>普通信号（多空）</span><b>{k['ordinary_all']['closed']} 笔</b><span>胜率 {k['ordinary_all']['win_rate'] * 100:.1f}%</span></div>
<div class="kpi"><span>普通 · 每笔 / 合计</span><b class="neg">{k['ordinary_all']['mean_net_r']:+.2f}R</b><span>合计 {k['ordinary_all']['sum_net_r']:+.1f}R</span></div>
<div class="kpi"><span>突破+spike（只做多）</span><b class="neg">{k['joint_long']['mean_net_r']:+.2f}R</b><span>{k['joint_long']['closed']} 笔 · 合计 {k['joint_long']['sum_net_r']:+.1f}R</span></div>
<div class="kpi"><span>最近半年 · 普通</span><b class="{cls(rec['sum'])}">{rec['sum']:+.1f}R</b><span>{rec['n']} 笔 · 每笔 {rec['mean']:+.2f}R</span></div>
<div class="kpi"><span>最近半年 · 突破+spike</span><b class="{cls(rj['sum'])}">{rj['sum']:+.1f}R</b><span>{rj['n']} 笔 · 每笔 {rj['mean']:+.2f}R</span></div>
</div>
<div class="note">结论：扣 0.2% 成本后整体亏损。多头和突破+spike 平均不比随机入场强。空头在 OKX 上好于随机，但在币安 2021–24 年没有复现。少数大单（2026-08-19 做多 +29.5R、2026-01-31 做空 +18.2R 等）决定了大部分盈利；去掉最好的 5 笔，结果明显变差。</div>

<h2 id="sum">总览（全区间）</h2>
{table(STAT_HEAD, stat_rows(trades))}
<h3>分时间窗</h3>
{table(["区间", "分组", "笔数", "胜率", "每笔 R", "合计 R", "随机入场/笔 R", "比随机多 bp/笔", "p（单边）"], windows, 2)}

<h2 id="curve">资金曲线（累计 R，按离场时间）</h2>
<img class="wide" src="equity.png" alt="资金曲线">
<p class="sub">虚线是扣费前。实线和虚线之间的差，就是 0.2% 往返成本累计吃掉的 R。</p>

<h2 id="time">按时间</h2>
<img class="wide" src="monthly.png" alt="月度">
<h3>按年</h3>
{table(["分组", "年份", "笔数", "胜率", "每笔 R", "合计 R", "盈亏比", "最好一笔 R"], yearly, 1)}
<h3>近 12 个月逐月</h3>
{table(["月份", "普通 笔数", "普通 胜率", "普通 合计R", "联合 笔数", "联合 胜率", "联合 合计R"], monthly)}

<h2 id="dist">盈亏分布（普通信号 {len(o)} 笔）</h2>
{table(["平仓结果", "笔数", "占比"], dist)}
{table(["离场方式", "笔数", "占比"], reasons)}

<h2 id="cost">成本敏感度（普通信号，每笔平均 R）</h2>
{table(["区间", "笔数", "扣费前", "0.20%（项目口径）", "0.15%", "0.10%", "0.07%"], cost_rows)}
<p class="sub">OKX 普通用户吃单单边 0.05%，来回 0.10%（未算滑点）。三段里有两段，扣费前就接近 0 或为负。</p>

<h2 id="streak">连亏</h2>
{table(["样本", "笔数", "实际最长连亏", "打乱顺序后平均", "打乱后 95% 分位", "打乱后出现同样长连亏的比例"], streak_rows)}
{table(["前面的情况（OKX 普通）", "笔数", "下一笔胜率", "下一笔平均 R"], next_rows)}
<p class="sub">连亏长度和随机打乱后的结果没有区别；连亏之后下一笔的胜率也不会变高，倍投没有依据。</p>

<h2 id="pattern">规律审计（18 个入场时可知的条件）</h2>
<p class="sub">数值 = 高组减低组（或「是」减「否」、该时段减其余）的每笔 R 差。只有三段同向、而且每段 |t|≥1，才算稳定。</p>
{table(["条件", "OKX 前段", "OKX 近 12 月", "币安 2021–24", "稳定"], stab)}

<h2 id="exit">出场单变量测试（普通信号，每笔 R）</h2>
{table(["出场规则", "OKX 前段（挑选）", "OKX 近 12 月（验证）", "币安 2021–24（验证）", "≥5R 笔数（三段）"], exit_rows)}
<p class="sub">挑出的「1R 就启动跟踪」在 OKX 近 12 个月每笔少 0.15R，两段验证合起来 p=0.87，没有通过。收紧出场会砍掉大单，对随机入场的帮助反而更大。</p>

<h2 id="recent">最近半年逐笔（{_bj([recent_start])[0]:%Y-%m-%d} 起，{len(unique)} 笔持仓）</h2>
{table(STAT_HEAD, stat_rows(recent))}
<img class="wide" src="equity_recent.png" alt="最近半年资金曲线">
<h3>交易列表</h3>
{table(["#", "信号（北京）", "方向", "类型", "进场", "止损", "止损宽度", "离场（北京）", "离场价", "离场方式", "持仓", "净 R", "峰值 R", "随机对照 R"], rows)}
<h3>逐笔图</h3>
<p class="sub">红框 = 进场到初始止损；绿框 = 进场到持仓期间的峰值；虚线 = 实际离场价；深绿阶梯线 = 跟踪止损；紫线 = 已收盘 15m EMA120；三角 = 信号 K。图中按下一根开盘价成交，所以 R 值和 TradingView 面板（按信号收盘价算）会略有差别。</p>
<div class="filters"><button class="on" data-f="all">全部</button><button data-f="long">做多</button><button data-f="short">做空</button><button data-f="joint">含突破+spike</button><button data-f="win">盈利</button><button data-f="loss">亏损</button></div>
{''.join(cards)}

<h2 id="limits">说明与局限</h2>
<ul>
<li>数据：OKX 官方 1m 归档 + 已确认的 1m 接口数据，聚合成完整 5m/15m K 线，无缺口。</li>
<li>突破+spike 的 241 笔与 9-22 那次 OKX 回放逐笔一致。趋势线高点识别没有和 TradingView 逐根核对。</li>
<li>两次加仓提示只是显示，回测没有执行；R 简单相加，不复利。</li>
<li>只开一个仓：持仓期间出现的信号跳过（135 个）；反向信号只平仓、不反手。</li>
<li>规律审计和出场测试都是对已有交易的事后分析，所有门槛在看结果前提交。</li>
</ul>
</main>"""
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SPIKE V12.9 ETH 5m 回测</title><style>{css}</style></head><body>{body}<script>{js}</script></body></html>"""
    path = OUT / "index.html"
    path.write_text(page)
    return path


if __name__ == "__main__":
    print(build().resolve())
