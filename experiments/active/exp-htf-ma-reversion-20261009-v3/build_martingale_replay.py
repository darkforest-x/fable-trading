"""Trade-by-trade replay of the v3 first-year best cell with a doubling stake (owner 2026-10-10).

Owner: "用v3加上倍投 1000u在不爆仓的情况下能变多少" then "那怎么不赚钱？不可能 交易回放给我看".
Cell: 5m long, trigger = completed 15m EMA120 - 4 x 15m Wilder ATR14, Vix Fix on, stop 3% of the
fill, target 5R, 48h cap, one entry per excursion, 0.2% round trip (frozen v3 ledger, OKX ETH).

Stake rules are exactly experiments/active/exp-spike-v129-eth5m-20261010-v1/martingale_overlay.py:
risk per trade = base x 2^min(losses in a row, cap); a net loss (stop or a losing 48h exit)
doubles it, a net win resets it; P&L = risk x net R; ruin when the next risk exceeds equity.
Scheme shown: base 50U, cap 3 (the hindsight best of the overlay grid), started 2024-10-09 (A)
and 2025-10-09 (B), against fixed 50U and fixed 10U. The trades of this cell never overlap, so
the sequential replay must reproduce the overlay's final equity (checked).

Charts redraw each trade from the same OKX 5m series with the builder's own line/ATR functions
(htf_ma_reversion_v1.chart_frames): 15m candles for readability, the line and trigger at 5m.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-htf-ma-reversion-20261009-v3/build_martingale_replay.py
Output (local, untracked): experiments/active/exp-htf-ma-reversion-20261009-v3/replay_v1/index.html
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from yoyo.evaluation import htf_ma_reversion_v1 as hv  # noqa: E402

EXP = Path("experiments/active/exp-htf-ma-reversion-20261009-v3")
OUT = EXP / "replay_v1"
CELL = dict(minutes=5, side=1, vix=True, measure="atr", level=4.0, stop="pct3", target_r=5, reentry=0)
START_A, START_B = pd.Timestamp("2024-10-09T00:00:00Z"), pd.Timestamp("2025-10-09T00:00:00Z")
BASE, CAP, EQUITY0 = 50.0, 3, 1000.0
OVERLAY = {"A": 3407.1, "B": 200.0}
BJ = "Asia/Shanghai"
UP, DOWN = "#089981", "#F23645"
KIND = {"stop": "止损", "target": "止盈", "timeout": "48h 到期"}
plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "PingFang HK", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def trades() -> pd.DataFrame:
    t = pd.read_csv(EXP / "results" / "trades.csv.gz")
    for key, value in CELL.items():
        t = t.loc[t[key].astype(str) == str(value)]
    t = t.copy()
    t["entry"] = pd.to_datetime(t.time, utc=True)
    t["exit"] = t.entry + pd.to_timedelta(t.bars * t.minutes, unit="m")
    t["exit_price"] = t.fill * (1 + t.side * t.gross_ret)
    t["stop_price"] = t.fill * (1 - t.side * t.risk_frac)
    t["target_price"] = t.fill * (1 + t.side * t.risk_frac * t.target_r)
    t = t.sort_values("entry").reset_index(drop=True)
    if (t.entry.iloc[1:].to_numpy() < t.exit.iloc[:-1].to_numpy()).any():
        raise ValueError("overlapping trades: the sequential replay would differ from the overlay")
    return t


def replay(t: pd.DataFrame, start: pd.Timestamp, base: float, cap: int | None) -> tuple[pd.DataFrame, str | None]:
    equity, streak, rows = EQUITY0, 0, []
    for k, row in t.loc[t.entry >= start].iterrows():
        mult = 1 if cap == 0 else 2 ** (streak if cap is None else min(streak, cap))
        risk = base * mult
        if risk > equity:
            frame = pd.DataFrame(rows)
            frame.attrs["needed"] = risk
            return frame, row.entry.tz_convert(BJ).strftime("%Y-%m-%d %H:%M")
        pnl = risk * row.net_r
        rows.append({"k": k, "streak_before": streak, "mult": mult, "risk": risk, "notional": risk / row.risk_frac,
                     "equity_before": equity, "pnl": pnl, "equity_after": equity + pnl})
        equity += pnl
        streak = 0 if row.net_r > 0 else streak + 1
    return pd.DataFrame(rows), None


def chart(row, k: int, frames: dict, ledger_row: dict | None, path: Path) -> None:
    chart5 = frames["chart"]
    idx = pd.to_datetime(chart5.index, unit="ms", utc=True)
    a, b = row.entry - pd.Timedelta(hours=12), row.exit + pd.Timedelta(hours=4)
    m = (idx >= a) & (idx <= b)
    bars = chart5.loc[m].copy()
    bars.index = idx[m]
    line = pd.Series(frames["line"][m], index=idx[m])
    at = line.index.get_indexer([row.entry.floor("5min")])[0]
    if at < 0 or not math.isclose(float(line.iloc[at]), float(row.line), rel_tol=1e-9):
        raise ValueError(f"redrawn line differs from the ledger at #{k}")
    trig = line - 4.0 * pd.Series(frames["atr"][m], index=idx[m])
    c15 = bars.resample("15min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    x = lambda ts: (pd.DatetimeIndex(ts).tz_convert(BJ).tz_localize(None))
    fig, ax = plt.subplots(figsize=(11, 5), dpi=100)
    up = (c15.close >= c15.open).to_numpy()
    xs = mdates_num(x(c15.index))
    w = 15 / 1440 * .7
    for color, mk in ((UP, up), (DOWN, ~up)):
        ax.vlines(xs[mk], c15.low[mk], c15.high[mk], color=color, lw=.8)
        ax.bar(xs[mk], (c15.close - c15.open).abs()[mk].clip(lower=.01), w, bottom=c15[["open", "close"]].min(axis=1)[mk],
               color=color, lw=0)
    ax.step(mdates_num(x(line.index)), line, where="post", color="#C18CFA", lw=2, label="15m EMA120（高周期线）")
    ax.step(mdates_num(x(trig.index)), trig, where="post", color="#f59e0b", lw=1.1, ls="--", label="触发线 = 线 − 4×ATR")
    e0, e1 = mdates_num(x([row.entry]))[0], mdates_num(x([row.exit]))[0]
    ax.hlines(row.stop_price, e0, e1, color=DOWN, lw=1.2)
    ax.text(e1, row.stop_price, f" 止损 {row.stop_price:.2f}", color=DOWN, fontsize=8, va="center")
    ax.add_patch(plt.Rectangle((e0, row.stop_price), e1 - e0, row.fill - row.stop_price, color=DOWN, alpha=.12, lw=0))
    color = UP if row.net_r > 0 else DOWN
    ax.scatter([e0], [row.fill], marker="^", s=80, color=UP, zorder=5)
    ax.text(e0, row.fill, f"进场 {row.fill:.2f}  ", ha="right", va="center", fontsize=8.5,
            bbox={"facecolor": "white", "alpha": .8, "edgecolor": "none", "pad": 1})
    ax.scatter([e1], [row.exit_price], marker="x", s=70, color=color, zorder=5)
    ax.text(e1, row.exit_price, f"  {KIND[row.exit_kind]} {row.exit_price:.2f}  {row.net_r:+.2f}R", va="center", fontsize=8.5,
            color=color, fontweight="bold", bbox={"facecolor": "white", "alpha": .8, "edgecolor": "none", "pad": 1})
    lo = min(c15.low.min(), row.stop_price, trig.min())
    hi = max(c15.high.max(), line.max())
    if row.target_price <= hi * 1.002:
        ax.hlines(row.target_price, e0, e1, color=UP, lw=1, ls=":")
        hi = max(hi, row.target_price)
    else:
        ax.text(e0, hi, f" 止盈 {row.target_price:.2f}（+15%）在图外", fontsize=8, color=UP, va="top")
    pad = (hi - lo) * .05
    ax.set_ylim(lo - pad, hi + pad)
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%m-%d %H:%M"))
    ax.yaxis.tick_right()
    ax.grid(alpha=.18)
    ax.legend(loc="lower left", fontsize=8, frameon=False)
    ax.spines[["top", "left"]].set_visible(False)
    stake = "" if ledger_row is None else f"  ·  A 方案下注风险 {ledger_row['risk']:.0f}U → {ledger_row['pnl']:+.0f}U，账户 {ledger_row['equity_after']:.0f}U"
    ax.set_title(f"#{k}  {row.entry.tz_convert(BJ):%Y-%m-%d %H:%M}  做多 · {KIND[row.exit_kind]} · {row.net_r:+.2f}R{stake}",
                 loc="left", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, format="jpg", pil_kwargs={"quality": 82, "optimize": True})
    plt.close(fig)


def mdates_num(index) -> np.ndarray:
    return matplotlib.dates.date2num(pd.DatetimeIndex(index).to_pydatetime())


def money(v: float) -> str:
    return f"{v:,.0f}U"


def build() -> Path:
    cfg = json.loads((EXP / "config.json").read_text())
    t = trades()
    a, ruin_a = replay(t, START_A, BASE, CAP)
    b, ruin_b = replay(t, START_B, BASE, CAP)
    fixed50, _ = replay(t, START_A, BASE, 0)
    fixed10, _ = replay(t, START_A, 10.0, 0)
    for name, led, ruin in (("A", a, ruin_a), ("B", b, ruin_b)):
        final = led.equity_after.iloc[-1]
        if abs(final - OVERLAY[name]) > .1:
            raise ValueError(f"{name}: {final} differs from the overlay result {OVERLAY[name]}")
    raw = hv.read_5m("ETHUSDT", pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"]), cfg["series_dir"])
    frames = hv.chart_frames(raw, 5, 15)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "charts").mkdir(exist_ok=True)

    fig, ax = plt.subplots(figsize=(11, 4.4), dpi=110)
    for led, label, color, ls in ((a, "A：倍投 50U 起、最多翻 3 次，2024-10 开始", DOWN, "-"),
                                  (b, "B：同样倍投，2025-10 开始", "#7c3aed", "-"),
                                  (fixed50, "固定每笔 50U 风险", "#1f2937", "--"), (fixed10, "固定每笔 10U 风险", "#6b7280", ":")):
        times = pd.DatetimeIndex(t.loc[led.k, "exit"]).tz_convert(BJ).tz_localize(None)
        ax.plot(times, led.equity_after, color=color, lw=1.5, ls=ls, label=label)
    ax.axhline(EQUITY0, color="#9ca3af", lw=.8)
    ax.axvline(START_B.tz_convert(BJ).tz_localize(None), color="#9ca3af", ls="--", lw=1)
    ax.set_ylabel("账户（U）")
    ax.legend(fontsize=8.5, frameon=False, loc="upper left")
    ax.grid(alpha=.25)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(OUT / "equity.png")
    plt.close(fig)

    by_k = a.set_index("k").to_dict("index")
    cards, rows_a = [], []
    for k, row in t.iterrows():
        n = k + 1
        chart(row, n, frames, by_k.get(k), OUT / "charts" / f"r{n:03d}.jpg")
        year = "y1" if row.entry < START_B else "y2"
        cards.append(f'<figure class="card {year} {row.exit_kind}" id="r{n}"><img loading="lazy" src="charts/r{n:03d}.jpg" alt="#{n}"></figure>')
    def ledger_rows(led: pd.DataFrame) -> list[str]:
        out = []
        for _, z in led.iterrows():
            row = t.loc[int(z.k)]
            n = int(z.k) + 1
            c = "pos" if z.pnl > 0 else "neg"
            out.append(f"<tr><td><a href='#r{n}'>#{n}</a></td><td>{row.entry.tz_convert(BJ):%Y-%m-%d %H:%M}</td><td>{row.line:.2f}</td>"
                       f"<td>{row.fill:.2f}</td><td>{row.stop_price:.2f}</td><td>{KIND[row.exit_kind]}</td>"
                       f"<td>{row.exit.tz_convert(BJ):%m-%d %H:%M}</td><td>{row.exit_price:.2f}</td><td class='{c}'>{row.net_r:+.2f}</td>"
                       f"<td>{int(z.streak_before)}</td><td>×{int(z.mult)}</td><td>{z.risk:.0f}</td><td>{z.notional:,.0f}</td>"
                       f"<td class='{c}'>{z.pnl:+,.0f}</td><td>{z.equity_after:,.0f}</td></tr>")
        return out
    head = ("<tr><th>#</th><th>进场（北京）</th><th>高周期线</th><th>进场价</th><th>止损价</th><th>离场方式</th><th>离场时间</th>"
            "<th>离场价</th><th>R</th><th>之前连亏</th><th>倍数</th><th>这笔风险 U</th><th>仓位 U</th><th>盈亏 U</th><th>账户 U</th></tr>")
    tab = lambda rows: f'<div class="tw"><table><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table></div>'
    a_y2 = a.loc[(t.loc[a.k, "entry"] >= START_B).to_numpy()]
    eq_at_b = a.loc[(t.loc[a.k, "entry"] < START_B).to_numpy()].equity_after.iloc[-1]
    css = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1f2937;--mute:#6b7280;--line:#e5e7eb;--pos:#089981;--neg:#e11d48}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,"PingFang SC","Hiragino Sans GB",sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px 80px}h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:32px 0 8px;padding-top:8px;border-top:1px solid var(--line)}
.sub{color:var(--mute);margin:0 0 12px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:12px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.kpi b{display:block;font-size:22px}.kpi span{color:var(--mute);font-size:12px}
.tw{overflow:auto;max-height:640px;background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}table{border-collapse:collapse;width:100%;font-size:12.5px;white-space:nowrap}
th,td{padding:5px 9px;border-bottom:1px solid var(--line);text-align:left}th{background:#fafaf9;position:sticky;top:0}.pos{color:var(--pos);font-weight:600}.neg{color:var(--neg);font-weight:600}
img.wide{width:100%;background:#fff;border:1px solid var(--line);border-radius:10px}.note{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:10px 14px;margin:10px 0}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.filters button{border:1px solid var(--line);background:var(--card);border-radius:999px;padding:5px 12px;cursor:pointer}
.filters button.on{background:var(--ink);color:#fff}.card{margin:0 0 14px;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}.card img{width:100%;display:block}
ol,ul{padding-left:20px}
"""
    js = """document.querySelectorAll('.filters button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.filters button').forEach(x=>x.classList.remove('on'));b.classList.add('on');const f=b.dataset.f;document.querySelectorAll('.card').forEach(c=>{c.style.display=(f==='all'||c.classList.contains(f))?'':'none'})});"""
    worst = a_y2.sort_values("equity_after").iloc[0]
    body = f"""<main>
<h1>v3 + 倍投 · 交易回放（OKX ETHUSDT.P 5m）</h1>
<p class="sub">v3 第一年最优：5m 只做多 · 价格跌到「已收盘 15m EMA120 − 4×15m ATR14」挂单买入（Vix Fix 亮才买）· 止损 3% · 止盈 5R（+15%）· 最多拿 48 小时 · 每段下跌只做一次 · 往返成本 0.2%</p>
<div class="note"><b>倍投规则</b>：本金 1000U；每笔先定风险（打到止损亏多少），仓位 = 风险 ÷ 3%。基础风险 50U；扣费后亏一笔，下一笔风险翻倍；赚一笔，回到 50U；最多翻 3 次（400U）。账户剩的钱不够下一笔的风险就算爆仓、停止。每笔盈亏 = 风险 × R。</div>
<div class="kpis">
<div class="kpi"><span>A：2024-10-09 开始</span><b class="pos">{money(a.equity_after.iloc[-1])}</b><span>{len(a)} 笔 · 最低 {money(a.equity_after.min())}</span></div>
<div class="kpi"><span>B：2025-10-09 开始</span><b class="neg">{'爆仓 ' + ruin_b if ruin_b else money(b.equity_after.iloc[-1])}</b><span>{len(b)} 笔后剩 {money(b.equity_after.iloc[-1])}</span></div>
<div class="kpi"><span>固定每笔 50U（不倍投）</span><b>{money(fixed50.equity_after.iloc[-1])}</b><span>2024-10 开始，{len(fixed50)} 笔</span></div>
<div class="kpi"><span>固定每笔 10U（不倍投）</span><b>{money(fixed10.equity_after.iloc[-1])}</b><span>2024-10 开始</span></div>
</div>
<img class="wide" src="equity.png" alt="账户曲线">
<p class="sub">A 能活下来，是因为第一年（ETH 2442 → 4519）先把账户做到了 {money(eq_at_b)}；第二年开始的 7 连止损它扛住了，账户最低回到 {money(worst.equity_after)}。B 从 1000U 开始直接撞上同一段连亏。</p>

<h2>B：2025-10-09 开始，怎么爆仓的（逐笔）</h2>
{tab(ledger_rows(b))}
<p class="sub">下一笔需要的风险是 {money(b.attrs.get("needed", float("nan")))}，账户只剩 {money(b.equity_after.iloc[-1])}，按规则停止（{ruin_b}）。</p>

<h2>A：2024-10-09 开始，全部 {len(a)} 笔</h2>
{tab(ledger_rows(a))}

<h2>每一笔的 K 线回放（{len(t)} 笔）</h2>
<p class="sub">15 分钟 K 线；紫线 = 已收盘 15m EMA120；橙色虚线 = 触发线（线 − 4×ATR，跌到这里挂单买）；三角 = 进场；叉 = 离场；红框 = 进场到 3% 止损。标题里是 A 方案这笔下了多少风险、盈亏多少。</p>
<div class="filters"><button class="on" data-f="all">全部</button><button data-f="y1">第一年</button><button data-f="y2">第二年</button><button data-f="stop">止损</button><button data-f="timeout">48h 到期</button><button data-f="target">止盈</button></div>
{''.join(cards)}
<h2>说明</h2>
<ul><li>交易本身来自 v3 冻结账本（OKX 官方 1m 归档聚合的 5m），倍投只改每笔下注，入场出场不变。A、B 的最终结果与之前的倍投模拟逐分一致。</li>
<li>这组参数是按第一年收益从 7992 组里挑出来的第一名；倍投方式（50U、最多翻 3 次）也是看完两年结果后挑的，都属于事后最优。</li>
<li>R 已扣 0.2% 往返成本。图上的线用回测同一套函数重算。</li></ul>
</main>"""
    page = f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v3 倍投回放</title><style>{css}</style></head><body>{body}<script>{js}</script></body></html>'
    (OUT / "index.html").write_text(page)
    return OUT / "index.html"


if __name__ == "__main__":
    print(build().resolve())
