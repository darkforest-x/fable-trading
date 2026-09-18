"""Why did the V10.4 trendline engine not draw a line the owner drew by hand?

Source: owner 2026-09-19 「为什么15min级别的趋势线很多都没有画出来 比如这个sol … 这个白线是我手动话的」
and the same question for ETH 15m. The engine (`spike_v10_4.joint_events`, the owner's
V10.4/V11.2 Pine defaults) only draws three-point lines: A, B, C must each be a
ta.pivothigh(12, 8) high, and the A-B line must pass every rule of `_search` and
`_validate`. This module replays those rules on one chosen A/B/C triple and reports
each rule with its measured value, so "missing line" becomes "which rule, by how much".

It also reports, for the whole file, a funnel of every candidate triple through the
same rules (which rule removes the most lines on this symbol/timeframe), and reruns the
engine with named relaxations so the owner can see what it would take to get the line.
Relaxations here are diagnostics only; the Pine defaults stay the owner's decision.

Input is an OKX 15m csv from `src.data.fetch_okx` (ts, open, high, low, close, volume).
Read-only: no signal, exit or parameter default is changed.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation.spike_v10_4 import Line, V104Params, _validate, joint_events, pivots, soft_peak

BEIJING = "Asia/Shanghai"
RELAX = (
    ("默认", {}),
    ("平顶算拐点", {"pivot_ties": "right_inclusive"}),
    ("平顶+三点间隔16根", {"pivot_ties": "right_inclusive", "min_gap": 16}),
    ("平顶+间隔16+贴线0.6ATR", {"pivot_ties": "right_inclusive", "min_gap": 16, "touch": 0.6}),
)


def load_okx(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, usecols=["ts", "open", "high", "low", "close", "volume"])
    index = pd.DatetimeIndex(pd.to_datetime(raw.ts.to_numpy(), unit="ms", utc=True))
    bars = pd.DataFrame(raw[["open", "high", "low", "close", "volume"]].to_numpy(float), index=index,
                        columns=["open", "high", "low", "close", "volume"])
    return bars[~bars.index.duplicated(keep="first")].sort_index()


def engine(facts: dict, params: V104Params, tick: float) -> tuple[object, dict]:
    f = facts["frame"]
    trace: dict = {}
    result = joint_events(f.open, f.high, f.low, f.close, f.atr, can_run=facts["can_run"],
                          confirmed_long=facts["v9_long"], parent_high=facts["parent_high"],
                          parent_low=facts["parent_low"], raw_side=facts["side"], long_alive=facts["long_alive"],
                          momentum=facts["momentum"], current_gate=facts["current_gate"],
                          ref_long_exit=facts["ref_long_exit"], tick=tick, params=params, trace=trace)
    return result, trace


def check_triple(f: pd.DataFrame, ax: int, bx: int, cx: int, tick: float, p: V104Params, track: str) -> list[tuple]:
    """(rule, measured, threshold, ok) for one A/B/C triple, in the engine's order."""
    h, o, c, lo, a = (f[k].to_numpy(float) for k in ("high", "open", "close", "low", "atr"))
    u = h if track == "full" else soft_peak(o, h, c, a, p.wick_cap)
    body = np.maximum(o, c)
    rows = []
    for name, x in (("A", ax), ("B", bx), ("C", cx)):
        strict, _, _ = pivots(u, p.left, p.right, "strict")
        one_sided, _, _ = pivots(u, p.left, p.right, "right_inclusive")
        is_strict = strict[x + p.right] == x if x + p.right < len(u) else False
        is_one = one_sided[x + p.right] == x if x + p.right < len(u) else False
        left_max = float(np.max(u[x - p.left:x]))
        right_max = float(np.max(u[x + 1:x + p.right + 1]))
        rows.append((f"{name} 是拐点高点（左12根、右8根都更低）", f"本根 {u[x]:.6g}，左侧最高 {left_max:.6g}，右侧最高 {right_max:.6g}",
                     "严格高于两侧", bool(is_strict), "平顶（右侧等高）时" + ("会算" if is_one else "也不算")))
    ap, bp, cp = float(u[ax]), float(u[bx]), float(u[cx])
    aa, ba, ca = (max(float(a[x]), tick) for x in (ax, bx, cx))
    item = Line(ax, ap, bx, bp, cx, cp, 0.0, cx + p.right, 0)
    born = cx + p.right
    rows += [
        ("A > B > C（逐级降低）", f"{ap:.6g} > {bp:.6g} > {cp:.6g}", "", ap > bp > cp, ""),
        ("A 到 C 跨度", f"{cx - ax} 根", f">= {p.span} 根", cx - ax >= p.span, ""),
        ("A 到 B 间隔", f"{bx - ax} 根", f">= {p.gap} 根", bx - ax >= p.gap, ""),
        ("B 到 C 间隔", f"{cx - bx} 根", f">= {p.gap} 根", cx - bx >= p.gap, ""),
        ("A 比 B 高出", f"{(ap - bp) / max(aa, ba):.2f} ATR", f">= {p.drop} ATR", ap - bp >= p.drop * max(aa, ba), ""),
        ("C 贴在 A-B 线上", f"偏离 {abs(cp - item.at(cx)) / ca:.2f} ATR", f"<= {p.touch} ATR",
         abs(cp - item.at(cx)) / ca <= p.touch, ""),
        ("A-B 之间回落", f"{(min(ap, bp) - lo[ax + 1:bx].min()) / max(aa, ba):.2f} ATR", f">= {p.pullback} ATR",
         (min(ap, bp) - lo[ax + 1:bx].min()) / max(aa, ba) >= p.pullback, ""),
        ("B-C 之间回落", f"{(min(bp, cp) - lo[bx + 1:cx].min()) / max(ba, ca):.2f} ATR", f">= {p.pullback} ATR",
         (min(bp, cp) - lo[bx + 1:cx].min()) / max(ba, ca) >= p.pullback, ""),
    ]
    if born < len(f):
        reason, spikes = _validate(item, born, h, c, body, a, tick, p)
        text = {0: f"通过（针刺 {spikes} 根）", 1: "有实体越线", 2: "针刺过多/过长", 3: "确认前已被突破"}[reason]
        rows.append(("从 A 到三点确认：实体不越线 / 针刺 <=2 / 未提前突破", text, "", reason == 0, ""))
    return rows


def funnel(f: pd.DataFrame, can_run, tick: float, p: V104Params) -> list[tuple[str, int]]:
    """Every (A, B, C) the search could see, pushed through the rules in order."""
    h, o, c, lo, a = (f[k].to_numpy(float) for k in ("high", "open", "close", "low", "atr"))
    body = np.maximum(o, c)
    can = np.asarray(can_run, dtype=bool)
    steps = ["时间上 A<B<C 的全部组合", "A>B>C", f"A-C 跨度>={p.span}", f"两两间隔>={p.gap}", f"A-B 落差>={p.drop}ATR",
             f"C 贴线<={p.touch}ATR", "当前收盘仍在线下", f"两段回落>={p.pullback}ATR", "实体不越线/针刺/未提前突破"]
    total = dict.fromkeys(steps, 0)
    for u in (h, soft_peak(o, h, c, a, p.wick_cap)):
        of, _, _ = pivots(u, p.left, p.right, p.pivot_ties)
        xs: list[int] = []; ps: list[float] = []; ats: list[float] = []
        for i in range(len(f)):
            if not can[i]:
                xs, ps, ats = [], [], []
                continue
            px = i - p.right
            if of[i] < 0 or px < 0 or not np.isfinite(a[px]):
                continue
            xs.append(px); ps.append(float(u[px])); ats.append(max(float(a[px]), tick))
            while xs and (i - xs[0] > p.lookback or len(xs) > p.pivots_cap):
                xs.pop(0); ps.pop(0); ats.pop(0)
            if len(xs) < 3:
                continue
            cx, cp, ca = xs[-1], ps[-1], ats[-1]
            for ai in range(len(xs) - 2):
                for bi in range(ai + 1, len(xs) - 1):
                    ax, ap, aa, bx, bp, ba = xs[ai], ps[ai], ats[ai], xs[bi], ps[bi], ats[bi]
                    total[steps[0]] += 1
                    if not ap > bp > cp:
                        continue
                    total[steps[1]] += 1
                    if cx - ax < p.span:
                        continue
                    total[steps[2]] += 1
                    if bx - ax < p.gap or cx - bx < p.gap:
                        continue
                    total[steps[3]] += 1
                    if ap - bp < p.drop * max(aa, ba):
                        continue
                    total[steps[4]] += 1
                    item = Line(ax, ap, bx, bp, cx, cp, 0.0, i, 0)
                    if abs(cp - item.at(cx)) / ca > p.touch:
                        continue
                    total[steps[5]] += 1
                    if not (item.at(i) > 0 and c[i] <= item.at(i)):
                        continue
                    total[steps[6]] += 1
                    b_atr = max(float(a[bx]), tick)
                    if ((min(ap, bp) - lo[ax + 1:bx].min()) / max(aa, b_atr) < p.pullback
                            or (min(bp, cp) - lo[bx + 1:cx].min()) / max(b_atr, ca) < p.pullback):
                        continue
                    total[steps[7]] += 1
                    if _validate(item, i, h, c, body, a, tick, p)[0] == 0:
                        total[steps[8]] += 1
    return [(s, total[s]) for s in steps]


def draw(path: Path, title: str, f: pd.DataFrame, ax_: int, bx: int, cx: int, rows: list[tuple],
         found: dict | None, lo: int, hi: int, through: str = "ab", notes: tuple = ()) -> None:
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    bg, fg, grid = "#0b0e14", "#c9ccd3", "#1b2029"
    part = f.iloc[lo:hi + 1]
    x = np.arange(lo, hi + 1)
    fig = plt.figure(figsize=(16, 9), facecolor=bg)
    ax = fig.add_axes([0.04, 0.08, 0.60, 0.82], facecolor=bg)
    ax.tick_params(colors=fg, labelsize=8)
    for side in ax.spines.values():
        side.set_color(grid)
    ax.grid(color=grid, linewidth=.5)
    ax.yaxis.tick_right()
    up = (part.close >= part.open).to_numpy()
    colors = np.where(up, "#4c8dff", "#b25cff")
    ax.vlines(x, part.low, part.high, color=colors, linewidth=.6)
    ax.bar(x, (part.close - part.open).abs().clip(lower=part.close * 1e-5), bottom=np.minimum(part.open, part.close),
           width=.7, color=colors, linewidth=0)
    h = f.high.to_numpy()
    second = bx if through == "ab" else cx
    slope = (h[second] - h[ax_]) / (second - ax_)
    end = hi
    ax.plot([ax_, end], [h[ax_], h[ax_] + slope * (end - ax_)], color="white", linewidth=1.4)
    for when, text in notes:
        k = int(f.index.get_loc(pd.Timestamp(when, tz=BEIJING).tz_convert("UTC")))
        ax.annotate(text, (k, h[k]), textcoords="offset points", xytext=(18, 26), color="#ff9f43", fontsize=9,
                    arrowprops=dict(arrowstyle="-", color="#ff9f43"))
    for name, px in (("A", ax_), ("B", bx), ("C", cx)):
        ax.annotate(name, (px, h[px]), textcoords="offset points", xytext=(0, 9), ha="center", color="white", fontsize=11)
        ax.plot([px], [h[px]], marker="o", markersize=10, markerfacecolor="none", markeredgecolor="#4679C9", mew=1.6)
    for k in range(ax_ - 2, ax_ + 3):
        if k != ax_ and 0 <= k < len(h) and h[k] == h[ax_]:
            ax.annotate(f"与 A 等高 {h[k]:.6g}", (k, h[k]), textcoords="offset points", xytext=(12, 14), color="#ff9f43",
                        fontsize=9, arrowprops=dict(arrowstyle="-", color="#ff9f43"))
    if found is not None:
        ax.axvline(found["born_i"] + 1, color="#1db9a0", linewidth=.8, linestyle="--")
        ax.text(found["born_i"] + 1, ax.get_ylim()[1], " 放宽后三点确认", color="#1db9a0", fontsize=8, va="top")
        if found.get("broke") is not None:
            b = found["broke"]
            ax.axvline(b, color="#AD7B29", linewidth=.9)
            ax.text(b, ax.get_ylim()[0], " 放宽后突破", color="#AD7B29", fontsize=9, va="bottom")
    ticks = np.linspace(lo, hi, 8).astype(int)
    ax.set_xticks(ticks)
    ax.set_xticklabels([f.index[t].tz_convert(BEIJING).strftime("%m-%d %H:%M") for t in ticks])
    ax.set_xlim(lo - 2, hi + 4)
    fig.text(0.04, 0.945, title, color="white", fontsize=14, fontweight="bold")
    y = 0.90
    fig.text(0.66, y, "程序的规则，逐条量这条线（时间=北京）", color="white", fontsize=11, fontweight="bold")
    for rule, measured, need, ok, note in rows:
        y -= 0.043
        fig.text(0.66, y, ("✓ " if ok else "✗ ") + rule, color="#26d07c" if ok else "#ff6b88", fontsize=9.5)
        fig.text(0.675, y - 0.018, f"{measured}  {need}  {note}".strip(), color=fg, fontsize=8)
    fig.savefig(path, dpi=90, facecolor=bg)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", type=Path, required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--asset", required=True)
    ap.add_argument("--tick", type=float, required=True)
    ap.add_argument("--a", required=True, help="A bar open, Beijing time, e.g. '2026-09-15 04:30'")
    ap.add_argument("--b", required=True)
    ap.add_argument("--c", required=True)
    ap.add_argument("--view-from", required=True)
    ap.add_argument("--view-to", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--through", choices=("ab", "ac"), default="ab",
                    help="draw the hand line through A-B (engine geometry) or A-C (owner drew through A and C)")
    ap.add_argument("--note", action="append", default=[], help="'Beijing time|text' marker, repeatable")
    args = ap.parse_args()
    bars = load_okx(args.csv)
    facts = study.v9_facts(bars, 15, args.asset, args.tick)
    f = facts["frame"]
    at = lambda s: int(f.index.get_loc(pd.Timestamp(s, tz=BEIJING).tz_convert("UTC")))  # noqa: E731
    bj = lambda i: f.index[i].tz_convert(BEIJING).strftime("%m-%d %H:%M")  # noqa: E731
    ax_, bx, cx = at(args.a), at(args.b), at(args.c)
    lo, hi = at(args.view_from), at(args.view_to)
    base = V104Params()
    print(f"{args.symbol} 15m OKX {len(f)} bars {bj(0)} .. {bj(len(f) - 1)} (Beijing)")
    print("pivot highs in view (full wick):",
          [(bj(int(x)), float(f.high.iloc[int(x)])) for x in pivots(f.high.to_numpy(), base.left, base.right)[0]
           if lo <= x <= hi])
    rows = check_triple(f, ax_, bx, cx, args.tick, base, "full")
    for r in rows:
        print(("  OK  " if r[3] else "  FAIL") + f" {r[0]}: {r[1]} {r[2]} {r[4]}")
    print("funnel (default rules, whole file):")
    for step, count in funnel(f, facts["can_run"], args.tick, base):
        print(f"  {step:28s} {count:>8,d}")
    found = None
    for name, change in RELAX:
        result, trace = engine(facts, replace(base, **change), args.tick)
        events = pd.DataFrame(trace["line_events"])
        match = [ln for ln in trace["lines"] if (ln["ax"], ln["bx"], ln["cx"]) == (ax_, bx, cx)]
        text = "没有这条线"
        if match:
            ln = match[0]
            e = events.loc[events.uid == ln["uid"]] if len(events) else events
            broke = [int(r.i) for r in e.itertuples() if r.event == "broke"]
            text = f"出现：三点确认 {bj(ln['born_i'] + 1)}" + (f"，突破 {bj(broke[0])}" if broke else "")
            if found is None:
                found = {"born_i": ln["born_i"], "broke": broke[0] if broke else None, "name": name}
        print(f"  {name:24s} 45天入池 {result.store_codes[1]:>3d} 条 | 你的线：{text}")
    title = f"{args.symbol} 15m · 你画的线为什么程序没画"
    if found is not None:
        title += f"（要同时放宽到「{found['name']}」才出现）"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    notes = tuple(tuple(n.split("|", 1)) for n in args.note)
    draw(args.out, title, f, ax_, bx, cx, rows, found, lo, hi, args.through, notes)
    print("figure", args.out)


if __name__ == "__main__":
    main()
