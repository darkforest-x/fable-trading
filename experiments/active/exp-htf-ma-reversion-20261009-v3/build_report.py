"""Owner-requested HTML view of the Notion #44 HTF-line reversion backtests on OKX ETHUSDT.P.

Owner 2026-10-10: "单独把之前做的均线回归的详细回测给我看一下 最大连续止损多少次". Reads the
frozen OKX ETH ledgers of exp-htf-ma-reversion v3 (touch entry), v4 (confirmation entry)
and v5 (v4 plus the daily-trend arm); no rule is changed. Six cells, fixed here:

* the owner's task as written (5m, 5% from the 15m EMA120, stop 3%, 3R, one entry), long and short;
* the owner's 2026-10-08 TradingView sample (5m long, 5%, 0.6% stop, 5R, up to 3 re-entries);
* the first-year best of v3, v4 and v5 (each selected on 2024-10-09..2025-10-09 by its committed rule).

"Consecutive stops" counts trades in entry order whose exit is the protective stop, without a
target or timeout exit in between (re-entries inside one excursion count as separate trades).
R is net of the 0.2% round trip; % sums one unit of notional per trade, as in the parents.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-htf-ma-reversion-20261009-v3/build_report.py
Output (local, untracked): experiments/active/exp-htf-ma-reversion-20261009-v3/report_v1/index.html
"""
from __future__ import annotations

import html
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path("experiments/active")
OUT = ROOT / "exp-htf-ma-reversion-20261009-v3" / "report_v1"
SPLIT = pd.Timestamp("2025-10-09T00:00:00Z")
BJ = "Asia/Shanghai"
UP, DOWN = "#089981", "#F23645"
KIND = {"stop": "止损", "target": "止盈", "timeout": "48h 到期"}
CELLS = [
    ("原始设定 · 做多", "5m 多，离 15m EMA120 下方 5% 触线进，止损 3%，止盈 3R，每段只进一次",
     "exp-htf-ma-reversion-20261009-v3/results", dict(minutes=5, side=1, vix=False, measure="pct", level=0.05, stop="pct3", target_r=3, reentry=0)),
    ("原始设定 · 做空", "5m 空，离 15m EMA120 上方 5% 触线进，止损 3%，止盈 3R，每段只进一次",
     "exp-htf-ma-reversion-20261009-v3/results", dict(minutes=5, side=-1, vix=False, measure="pct", level=0.05, stop="pct3", target_r=3, reentry=0)),
    ("截图样例", "5m 多，离线 5% 触线进，止损 0.6%，止盈 5R，止损后同一段最多再进 3 次（你 10-08 的 TradingView 框）",
     "exp-htf-ma-reversion-20261009-v3/results", dict(minutes=5, side=1, vix=False, measure="pct", level=0.05, stop="pct0.6", target_r=5, reentry=3)),
    ("v3 第一年最优（触线进）", "5m 多，离线 4 倍 15m ATR 触线进，止损 3%，止盈 5R，开 Vix Fix，每段一次",
     "exp-htf-ma-reversion-20261009-v3/results", dict(minutes=5, side=1, vix=True, measure="atr", level=4.0, stop="pct3", target_r=5, reentry=0)),
    ("v4 第一年最优（等确认进）", "15m 空，离 1h SMA60 上方 4 倍 1h ATR，收盘收回触发价再进，止损在极值外 0.1%，止盈 5R",
     "exp-htf-ma-reversion-20261009-v4/results_eth", dict(minutes=15, side=-1, vix=False, measure="atr", level=4.0, stop="swing0.1", target_r=5, reentry=0, conf="reclaim")),
    ("v5 加日线趋势后最优", "同 v4 第一年最优，只在日线方向一致时做（上一根日线收盘在 SMA50 下方才做空）",
     "exp-htf-ma-reversion-20261010-v5/results_eth", dict(minutes=15, side=-1, vix=False, measure="atr", level=4.0, stop="swing0.1", target_r=5, reentry=0, conf="reclaim", trend="with")),
]
plt.rcParams["font.sans-serif"] = ["Hiragino Sans GB", "PingFang HK", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


_CACHE: dict[str, pd.DataFrame] = {}


def load(folder: str, cell: dict) -> pd.DataFrame:
    if folder not in _CACHE:
        _CACHE[folder] = pd.read_csv(ROOT / folder / "trades.csv.gz")
    t = _CACHE[folder]
    for key, value in cell.items():
        t = t.loc[t[key].astype(str) == str(value)]
    if t.empty:
        raise ValueError(f"cell not found: {cell}")
    t = t.copy()
    t["entry"] = pd.to_datetime(t.time, utc=True)
    t["exit"] = t.entry + pd.to_timedelta(t.bars * t.minutes, unit="m")
    return t.sort_values("entry").reset_index(drop=True)


def runs(flags: np.ndarray, t: pd.DataFrame) -> tuple[int, str, float, list[int]]:
    best, start, cur, where, total, lengths = 0, 0, 0, "", 0.0, []
    for k, f in enumerate(flags):
        if f:
            start = k if cur == 0 else start
            cur += 1
            if cur > best:
                best = cur
                g = t.iloc[start:k + 1]
                where = f"{g.entry.iloc[0].tz_convert(BJ):%Y-%m-%d} → {g.entry.iloc[-1].tz_convert(BJ):%Y-%m-%d}"
                total = float(g.net_r.sum())
        else:
            if cur:
                lengths.append(cur)
            cur = 0
    if cur:
        lengths.append(cur)
    return best, where, total, lengths


def stats(t: pd.DataFrame) -> dict:
    r = t.net_r
    if not len(r):
        return {"n": 0}
    curve = np.r_[0.0, r.cumsum().to_numpy()]
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    stop_best, stop_where, stop_sum, stop_runs = runs((t.exit_kind == "stop").to_numpy(), t)
    loss_best, loss_where, loss_sum, _ = runs((r < 0).to_numpy(), t)
    return {"n": len(r), "win": float((r > 0).mean()), "target": float((t.exit_kind == "target").mean()),
            "stop": float((t.exit_kind == "stop").mean()), "timeout": float((t.exit_kind == "timeout").mean()),
            "sum_pct": float(t.net_ret.sum() * 100), "sum_r": float(r.sum()), "mean_r": float(r.mean()),
            "pf": float(wins / losses) if losses else math.inf, "dd_r": float((np.maximum.accumulate(curve) - curve).max()),
            "ctrl": float(t.control_net_r.mean()), "risk": float(t.risk_frac.median() * 100),
            "hold": float((t.bars * t.minutes).median() / 60), "stop_best": stop_best, "stop_where": stop_where,
            "stop_sum": stop_sum, "stop_ge5": sum(x >= 5 for x in stop_runs), "stop_ge10": sum(x >= 10 for x in stop_runs),
            "loss_best": loss_best, "loss_where": loss_where, "loss_sum": loss_sum}


def fmt(v, d=2, signed=True):
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "–"
    return f"{v:+.{d}f}" if signed else f"{v:.{d}f}"


def cls(v):
    return "pos" if v > 0 else "neg" if v < 0 else ""


def table(head, rows):
    h = "".join(f"<th>{html.escape(x)}</th>" for x in head)
    b = "".join("<tr>" + "".join(f'<td class="{c[1]}">{c[0]}</td>' if isinstance(c, tuple) else f"<td>{c}</td>" for c in r) + "</tr>"
                for r in rows)
    return f'<div class="tw"><table><thead><tr>{h}</tr></thead><tbody>{b}</tbody></table></div>'


def equity(cells: list[tuple[str, pd.DataFrame]], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(11, 4.6), dpi=110)
    colors = ["#1f2937", "#6b7280", DOWN, "#4679C9", "#AD7B29", UP]
    for (name, t), color in zip(cells, colors):
        x = pd.DatetimeIndex(t.exit).tz_convert(BJ).tz_localize(None)
        ax.plot(x, t.sort_values("exit").net_r.cumsum().to_numpy(), color=color, lw=1.4, label=name)
    ax.axvline(SPLIT.tz_convert(BJ).tz_localize(None), color="#9ca3af", ls="--", lw=1)
    ax.text(SPLIT.tz_convert(BJ).tz_localize(None), ax.get_ylim()[1], " 第二年", va="top", fontsize=9, color="#6b7280")
    ax.axhline(0, color="#9ca3af", lw=.8)
    ax.set_ylabel("累计 R（扣 0.2% 成本）")
    ax.legend(fontsize=8.5, frameon=False, ncol=3, loc="lower left")
    ax.grid(alpha=.25)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, format="png")
    plt.close(fig)


def build() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    loaded = [(name, desc, load(folder, cell)) for name, desc, folder, cell in CELLS]
    equity([(n, t) for n, _, t in loaded], OUT / "equity.png")
    head = ["参数组", "笔数", "胜率", "止盈占比", "止损占比", "合计 %", "合计 R", "每笔 R", "盈亏比", "最大回撤", "最长连续止损",
            "最长连续亏损", "随机入场/笔 R"]
    overview, years, sections = [], [], []
    for i, (name, desc, t) in enumerate(loaded):
        m = stats(t)
        overview.append([f'<a href="#c{i}">{html.escape(name)}</a>', m["n"], f"{m['win'] * 100:.0f}%",
                         f"{m['target'] * 100:.0f}%", f"{m['stop'] * 100:.0f}%", (fmt(m["sum_pct"], 1), cls(m["sum_pct"])),
                         (fmt(m["sum_r"], 1), cls(m["sum_r"])), (fmt(m["mean_r"]), cls(m["mean_r"])), fmt(m["pf"], 2, False),
                         f"{m['dd_r']:.1f}R", (f"<b>{m['stop_best']} 次</b>", "neg"), f"{m['loss_best']} 次", fmt(m["ctrl"])])
        for label, g in (("第一年 2024-10 → 2025-10", t.loc[t.entry < SPLIT]), ("第二年 2025-10 → 2026-10", t.loc[t.entry >= SPLIT])):
            y = stats(g)
            if y["n"]:
                years.append([html.escape(name), label, y["n"], f"{y['win'] * 100:.0f}%", (fmt(y["sum_pct"], 1), cls(y["sum_pct"])),
                              (fmt(y["sum_r"], 1), cls(y["sum_r"])), (fmt(y["mean_r"]), cls(y["mean_r"])), f"{y['stop_best']} 次",
                              f"{y['dd_r']:.1f}R"])
        month = t.assign(m=pd.DatetimeIndex(t.entry).tz_convert(BJ).strftime("%Y-%m")).groupby("m").agg(
            n=("net_r", "size"), r=("net_r", "sum"), pct=("net_ret", lambda x: x.sum() * 100),
            w=("net_r", lambda x: (x > 0).mean()))
        mrows = [[mm, int(v.n), f"{v.w * 100:.0f}%", (fmt(v.pct, 1), cls(v.pct)), (fmt(v.r, 1), cls(v.r))] for mm, v in month.iterrows()]
        trows = []
        for k, row in enumerate(t.itertuples(), 1):
            trows.append([k, f"{row.entry.tz_convert(BJ):%Y-%m-%d %H:%M}", "多" if row.side == 1 else "空", f"{row.line:.2f}",
                          f"{row.fill:.2f}", f"{row.dev_pct:+.2f}%", f"{row.risk_frac * 100:.2f}%", KIND.get(row.exit_kind, row.exit_kind),
                          f"{row.bars * row.minutes / 60:.1f}h", (fmt(row.net_ret * 100), cls(row.net_ret)), (fmt(row.net_r), cls(row.net_r))])
        sections.append(f"""
<h2 id="c{i}">{html.escape(name)}</h2>
<p class="sub">{html.escape(desc)}</p>
<div class="kpis">
<div class="kpi"><span>最长连续止损</span><b class="neg">{m['stop_best']} 次</b><span>{m['stop_where']} · 合计 {m['stop_sum']:+.1f}R</span></div>
<div class="kpi"><span>最长连续亏损（含到期亏损）</span><b>{m['loss_best']} 次</b><span>{m['loss_where']} · 合计 {m['loss_sum']:+.1f}R</span></div>
<div class="kpi"><span>连续止损 ≥5 次 / ≥10 次</span><b>{m['stop_ge5']} / {m['stop_ge10']} 段</b><span>止损中位宽度 {m['risk']:.2f}% · 持仓中位 {m['hold']:.1f}h</span></div>
<div class="kpi"><span>两年合计</span><b class="{cls(m['sum_r'])}">{m['sum_r']:+.1f}R</b><span>{m['sum_pct']:+.1f}% · {m['n']} 笔 · 胜率 {m['win'] * 100:.0f}%</span></div>
</div>
<details><summary>按月（{len(mrows)} 个月）</summary>{table(["月份", "笔数", "胜率", "合计 %", "合计 R"], mrows)}</details>
<details><summary>全部 {len(trows)} 笔明细</summary>{table(["#", "进场（北京）", "方向", "高周期线", "成交价", "离线", "止损宽度", "离场", "持仓", "净 %", "净 R"], trows)}</details>""")
    css = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1f2937;--mute:#6b7280;--line:#e5e7eb;--pos:#089981;--neg:#e11d48}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 -apple-system,"PingFang SC","Hiragino Sans GB",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 80px}h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:34px 0 6px;padding-top:8px;border-top:1px solid var(--line)}
h3{font-size:15px;margin:18px 0 8px}.sub{color:var(--mute);margin:0 0 12px}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:12px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.kpi b{display:block;font-size:22px}.kpi span{color:var(--mute);font-size:12px}
.tw{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left}th{background:#fafaf9;position:sticky;top:0}.pos{color:var(--pos);font-weight:600}.neg{color:var(--neg);font-weight:600}
img.wide{width:100%;background:#fff;border:1px solid var(--line);border-radius:10px}details{margin:8px 0}summary{cursor:pointer;color:#374151;font-weight:600;padding:6px 0}
.note{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:10px 14px;margin:10px 0}ul{padding-left:20px}
"""
    body = f"""<main>
<h1>#44 高周期均线回归 · OKX ETHUSDT.P 回测</h1>
<p class="sub">2024-10-09 至 2026-10-08（两年），前一年用来挑参数，后一年检验 · 5m 图看已收盘 15m EMA120，15m 图看已收盘 1h SMA60（SPIKE V13.1 的高周期线）· 离线到阈值反向进场 · 止盈 2R/3R/5R · 48 小时未出结果按市价离场 · 往返成本 0.2%</p>
<div class="note">结论：六组参数两年都没有稳定赚钱。三组「第一年最好」的参数第二年全部转亏（v3 +21.9R → −15.2R，v4 +11.9R → −27.4R，v5 +14.3R → −19.5R）。你任务里的原始设定，做多、做空两年都亏。截图样例（止损 0.6%、止盈 5R、可再进）胜率只有 17%，<b>最长连续止损 23 次</b>（2024-12-10 到 2025-01-09，合计 −30.7R），两年合计 −80.5R。v4 和 v5 按固定本金加总是正的（约 +30%），但按固定风险（R）是负的：赚钱的主要是少数止损很宽的单子。</div>
<h2>六组参数总览（两年合计）</h2>
{table(head, overview)}
<img class="wide" src="equity.png" alt="资金曲线">
<h3>分年</h3>
{table(["参数组", "年份", "笔数", "胜率", "合计 %", "合计 R", "每笔 R", "最长连续止损", "最大回撤"], years)}
{''.join(sections)}
<h2>说明</h2>
<ul>
<li>数据：OKX 官方 1m 归档聚合成 5m，加上 2026-10-09 拉取的 5m 尾段，无缺口。</li>
<li>「连续止损」按进场顺序数，中间出现一次止盈或到期离场就断开；止损后再进的每一次都算一笔。</li>
<li>合计 % 是每笔按相同本金简单相加；R 是每笔按相同风险（止损打到亏 1R）相加。两者符号不同时（v4、v5），说明盈利集中在止损很宽、仓位相对小的单子上；如果按固定风险下单，实际是亏的。止损越窄，0.2% 成本占的 R 越多。</li>
<li>随机入场：每笔配 20 个同月、同波动、同止损宽度和止盈的随机入场，扣同样成本。</li>
<li>v6（只做恐慌急跌后的反弹）已跑完，但结果还没分析，本页没有包含。</li>
</ul></main>"""
    page = f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>#44 均线回归回测</title><style>{css}</style></head><body>{body}</body></html>'
    path = OUT / "index.html"
    path.write_text(page)
    return path


if __name__ == "__main__":
    print(build().resolve())
