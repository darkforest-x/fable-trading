"""Trade-by-trade book and TradingView-style sample charts for the 突破+spike box rule.

Source: owner 2026-09-19 「你的回测结果我怎么看不懂？每一笔的详细记录有吗？给我抽样50张截图看看
要和我给你的tradingview截图类似」. Reads the committed box-rule run
(`exp-spike-v11-box-joint-20260918-v1`, arm box_any: a break seen while the chart's V9
long box is open, first break per box, 15m<-1h and 1h<-4h) and, per trade, rebuilds
from the same 5m archive:
  * the V9 trade that opened the box, entered at its own next open (the V9 box);
  * the 突破+spike trade the backtest actually took (next open after the joint bar),
    recomputed and asserted equal to the ledger;
  * the trendline that broke (own-timeframe line from the engine trace, or the
    higher-timeframe line), with A/B/C and its confirmation bar.
Every trade goes to a Chinese-column book (xlsx + csv); 50 trades drawn at random
(seed 91509, 25 per timeframe, not chosen by outcome) are rendered as charts.
Read-only apart from the outputs; no signal, exit or parameter changes.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v10_4_study as study
from yoyo.evaluation import spike_v10_4_increment as inc
from yoyo.evaluation import spike_v11_study as v11
from yoyo.evaluation.spike_v10_4 import V104Params, joint_events

LEDGER = Path("experiments/active/exp-spike-v11-box-joint-20260918-v1/statistics/run_v1/trades.csv.gz")
PAIRS = {"15m": (15, "1h", 60), "1h": (60, "4h", 240)}
SEED, PER_TF = 91509, 25
EXTEND = 48
REASON = {"initial_stop": "初始止损", "initial_stop_gap": "初始止损（跳空）", "trailing_stop": "追踪止损",
          "trailing_stop_gap": "追踪止损（跳空）", "opposite_v6_next_open": "V9空头确认平仓",
          "boundary_mark": "数据结束仍持仓", "data_gap_censored": "数据断档"}
BG, FG, GRID = "#0b0e14", "#c9ccd3", "#1b2029"
UP, DOWN = "#4c8dff", "#b25cff"
BEIJING = "Asia/Shanghai"


def bj(ts) -> str:
    return pd.Timestamp(ts).tz_convert(BEIJING).strftime("%Y-%m-%d %H:%M")


def rebuild(symbol: str, timeframe: str, base: pd.DataFrame, meta: dict) -> dict:
    minutes, htf_name, htf_minutes = PAIRS[timeframe]
    tick, asset = float(meta["tick"]), meta["asset"]
    params = V104Params()
    bars = v11.bars_for(base, minutes)
    facts = study.v9_facts(bars, minutes, asset, tick)
    frame = facts["frame"]
    H, _ = v11.htf_inputs(frame.index, minutes, v11.bars_for(base, htf_minutes), htf_minutes, tick, params)
    trace: dict = {}
    joint_events(frame.open, frame.high, frame.low, frame.close, frame.atr, can_run=facts["can_run"],
                 confirmed_long=facts["v9_long"], parent_high=facts["parent_high"], parent_low=facts["parent_low"],
                 raw_side=facts["side"], long_alive=facts["long_alive"], momentum=facts["momentum"],
                 current_gate=facts["current_gate"], ref_long_exit=facts["ref_long_exit"], tick=tick, params=params,
                 trace=trace)
    identity = {"venue": "binance_um", "symbol": symbol, "asset": asset, "timeframe": timeframe,
                "timeframe_min": minutes}
    prepared = study.prepared_arm(frame, facts["gap"], facts["side"], f"binance_um:{symbol}:{timeframe}", identity,
                                  minutes, tick)
    lines = {ln["uid"]: ln for ln in trace["lines"]}
    return {"frame": frame, "H": H, "trace": trace, "lines": lines, "prepared": prepared, "htf": htf_name,
            "minutes": minutes}


def line_of(ctx: dict, i: int, source: str) -> dict:
    """The line that broke on the joint bar, as bar positions/prices on this chart."""
    frame = ctx["frame"]
    minutes_index = v11.minutes_of(frame.index)
    if source in ("htf", "both"):
        H = ctx["H"]
        to_x = lambda t: float(np.interp(t, minutes_index, np.arange(len(frame))))  # noqa: E731
        return {"kind": f"上级 {ctx['htf']}", "ax": to_x(H["ax_t"][i]), "ap": H["ap"][i], "bx": to_x(H["bx_t"][i]),
                "bp": H["bp"][i], "cx": to_x(H["cx_t"][i]), "cp": H["cp"][i], "born_x": to_x(H["born_t"][i]),
                "break_x": to_x(H["break_t"][i]),
                "a_time": pd.Timestamp(H["ax_t"][i] * 60, unit="s", tz="UTC"),
                "b_time": pd.Timestamp(H["bx_t"][i] * 60, unit="s", tz="UTC"),
                "c_time": pd.Timestamp(H["cx_t"][i] * 60, unit="s", tz="UTC"),
                "born_time": pd.Timestamp(H["born_t"][i] * 60, unit="s", tz="UTC")}
    uid = int(ctx["trace"]["break_winner_uid"][i])
    ln = ctx["lines"][uid]
    idx = frame.index
    return {"kind": "本周期", "ax": ln["ax"], "ap": ln["ap"], "bx": ln["bx"], "bp": ln["bp"], "cx": ln["cx"],
            "cp": ln["cp"], "born_x": ln["born_i"], "break_x": i, "a_time": idx[ln["ax"]], "b_time": idx[ln["bx"]],
            "c_time": idx[ln["cx"]], "born_time": idx[ln["born_i"]] + pd.Timedelta(minutes=ctx["minutes"])}


def book_row(trade, v9_time, v9: dict | None, v9_status: str, jt: dict, line: dict, figure_no) -> dict:
    def r(d, k):
        return None if d is None else d.get(k)
    return {
        "图号": figure_no, "周期": trade.timeframe, "币种": trade.symbol, "前后段": "后段" if trade.period == "later" else "前段",
        "V9信号K(北京)": bj(v9_time),
        "V9进场价": r(v9, "entry_price"), "V9止损": r(v9, "initial_stop"),
        "V9出场(北京)": bj(v9["exit_time"]) if v9 else None, "V9出场价": r(v9, "exit_price"),
        "V9出场原因": REASON.get(r(v9, "exit_reason"), r(v9, "exit_reason")) if v9 else v9_status,
        "V9净R": r(v9, "net_r"), "V9最大浮盈R": r(v9, "mfe_r"),
        "突破来源": line["kind"], "突破+spike K(北京)": bj(trade.signal_bar_open), "距V9信号根数": int(trade.bars_after_v9),
        "联合进场价": jt["entry_price"], "联合止损": jt["initial_stop"], "联合出场(北京)": bj(jt["exit_time"]),
        "联合出场价": jt["exit_price"], "联合出场原因": REASON.get(jt["exit_reason"], jt["exit_reason"]),
        "联合净R": jt["net_r"], "联合净收益bp": None if pd.isna(jt["net_return"]) else jt["net_return"] * 1e4,
        "联合最大浮盈R": jt["mfe_r"],
        "趋势线A(北京)": bj(line["a_time"]), "A价": line["ap"], "B(北京)": bj(line["b_time"]), "B价": line["bp"],
        "C(北京)": bj(line["c_time"]), "C价": line["cp"], "三点确认(北京)": bj(line["born_time"]),
    }


def draw(path: Path, number: int, trade, ctx: dict, v9: dict | None, jt: dict, line: dict) -> None:
    plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    frame = ctx["frame"]
    n = len(frame)
    i = int(trade.signal_i)
    s = int(trade.box_entry_i)
    exits = [int(jt["exit_i"])] + ([int(v9["exit_i"])] if v9 and v9.get("exit_i") is not None else [])
    lo = int(max(0, min(math.floor(line["ax"]), s) - 15))
    lo = max(lo, i - 420)
    hi = int(min(n - 1, max(max(exits), i + EXTEND) + 12))
    hi = min(hi, lo + 760)
    part = frame.iloc[lo:hi + 1]
    x = np.arange(lo, hi + 1)
    fig = plt.figure(figsize=(16, 9), facecolor=BG)
    ax = fig.add_axes([0.05, 0.30, 0.86, 0.62], facecolor=BG)
    sub = fig.add_axes([0.05, 0.07, 0.86, 0.20], facecolor=BG, sharex=ax)
    for a in (ax, sub):
        a.tick_params(colors=FG, labelsize=8)
        for side in ("top", "right", "left", "bottom"):
            a.spines[side].set_color(GRID)
        a.grid(color=GRID, linewidth=0.5)
        a.yaxis.tick_right()
    up = (part.close >= part.open).to_numpy()
    ax.vlines(x, part.low, part.high, color=np.where(up, UP, DOWN), linewidth=0.6)
    ax.bar(x, (part.close - part.open).abs().clip(lower=part.close * 1e-5), bottom=np.minimum(part.open, part.close),
           width=0.7, color=np.where(up, UP, DOWN), linewidth=0)
    for col, color, lw in (("s20", "#1db9a0", .9), ("e20", "#127a6c", .9), ("s60", "#4679C9", .9),
                           ("e60", "#2f5796", .9), ("s120", "#9aa0a6", .8), ("e120", "#5f6368", .8)):
        ax.plot(x, part[col], color=color, linewidth=lw)
    # V9 box
    if v9 and v9.get("exit_i") is not None:
        e0, e1 = int(v9["entry_i"]), int(v9["exit_i"])
        entry, stop, risk = float(v9["entry_price"]), float(v9["initial_stop"]), float(v9["initial_risk"])
        peak = max(3.0, float(v9["mfe_r"] or 0))
        ax.add_patch(plt.Rectangle((e0, stop), e1 - e0, entry - stop, facecolor="#D34B66", alpha=.18, edgecolor="#D34B66", linewidth=.8))
        ax.add_patch(plt.Rectangle((e0, entry), e1 - e0, peak * risk, facecolor="#008F82", alpha=.13, edgecolor="#008F82", linewidth=.8))
        mfe = float(v9["mfe_r"] or 0)
        for k in (1, 2, 3):
            ax.hlines(entry + k * risk, e0, e1, color="#008F82", linestyle="--", linewidth=.6, alpha=.7)
            if abs(k - mfe) > .3:
                ax.text(e1 + 1, entry + k * risk, f"{k}R · {entry + k * risk:.6g}", color="#1db9a0", fontsize=7, va="center")
        ax.text(e1 + 1, stop, f"sl · {stop:.6g}", color="#ff6b88", fontsize=8, va="center")
        ax.text(e1 + 1, entry, f"{entry:.6g}", color=FG, fontsize=8, va="center")
        ax.hlines(entry + mfe * risk, e0, e1, color="#26d07c", linewidth=.8, alpha=.8)
        ax.text(e1 + 1, entry + mfe * risk, f"峰值 {mfe:.2f}R · {entry + mfe * risk:.6g}", color="#26d07c", fontsize=8,
                va="center", fontweight="bold")
        ax.plot([e0], [entry], marker="^", color="#26d07c", markersize=9)
        if v9.get("exit_price") is not None:
            ax.plot([e1], [v9["exit_price"]], marker="x", color="#ffffff", markersize=9, mew=2)
    # the line that broke
    end_x = i + EXTEND
    slope = (line["bp"] - line["ap"]) / (line["bx"] - line["ax"])
    x0 = max(line["ax"], lo)
    color = "#ffffff" if line["kind"] == "本周期" else "#9DB7FF"
    ax.plot([x0, end_x], [line["ap"] + slope * (x0 - line["ax"]), line["ap"] + slope * (end_x - line["ax"])],
            color=color, linewidth=1.6)
    for label, lx, ly, when in (("A", line["ax"], line["ap"], line["a_time"]), ("B", line["bx"], line["bp"], line["b_time"]),
                                ("C", line["cx"], line["cp"], line["c_time"])):
        if lo <= lx <= hi:
            ax.annotate(label, (lx, ly), textcoords="offset points", xytext=(0, 8), ha="center", color=color, fontsize=10)
        elif lx < lo:
            ax.annotate(f"← {label} 在图外左侧：{bj(when)} · {ly:.6g}", (lo, line["ap"] + slope * (lo - line["ax"])),
                        textcoords="offset points", xytext=(4, -16 - 14 * "ABC".index(label)), color=color, fontsize=8)
    for cx_ in (line["ax"], line["break_x"]):
        if lo <= cx_ <= hi:
            ax.plot([cx_], [line["ap"] + slope * (cx_ - line["ax"])], marker="o", markersize=11, markerfacecolor="none",
                    markeredgecolor="#4679C9", mew=1.8)
    if lo <= line["born_x"] <= hi:
        ax.annotate("三点确认", (line["born_x"], line["ap"] + slope * (line["born_x"] - line["ax"])), textcoords="offset points",
                    xytext=(0, 22), ha="center", color=color, fontsize=7, alpha=.85,
                    arrowprops=dict(arrowstyle="-", color=color, alpha=.5, linewidth=.6))
    # 突破+spike label and the trade the backtest took
    tag = "突破+spike" + ("（上级突破）" if line["kind"] != "本周期" else "") + f"\n{frame.close.iloc[i]:.6g}"
    ybot = frame.low.iloc[max(lo, i - 5):i + 6].min()
    ax.annotate(tag, (i, ybot), textcoords="offset points", xytext=(0, -38), ha="center", color="white", fontsize=10,
                fontweight="bold", bbox=dict(boxstyle="round,pad=0.35", fc="#AD7B29", ec="none"))
    ax.axvline(i, color="#AD7B29", linewidth=.8, alpha=.6)
    j0, j1 = int(jt["entry_i"]), int(jt["exit_i"])
    ax.hlines(jt["initial_stop"], j0, j1, color="#ff9f43", linestyle=":", linewidth=1.2)
    ax.plot([j0], [jt["entry_price"]], marker="^", color="#ff9f43", markersize=10)
    ax.plot([j1], [jt["exit_price"]], marker="X", color="#ff9f43", markersize=10)
    ax.set_xlim(lo, hi + 25)
    ymin = min(part.low.min(), (v9 or {}).get("initial_stop") or np.inf, jt["initial_stop"])
    ymax = part.high.max()
    if v9 and v9.get("initial_risk"):
        ymax = max(ymax, float(v9["entry_price"]) + max(3.0, float(v9["mfe_r"] or 0)) * float(v9["initial_risk"]))
    pad = (ymax - ymin) * .06
    ax.set_ylim(ymin - pad * 2.5, ymax + pad)
    # IMACD
    sub.axhline(0, color="#555", linewidth=.8)
    sub.plot(x, part.md, color="#4679C9", linewidth=1.4)
    sub.plot(x, part.sb, color="#CB882A", linewidth=1.4)
    sub.set_ylabel("IMACD", color=FG, fontsize=8)
    ticks = np.linspace(lo, hi, 8).astype(int)
    sub.set_xticks(ticks)
    sub.set_xticklabels([frame.index[t].tz_convert(BEIJING).strftime("%m-%d %H:%M") for t in ticks])
    plt.setp(ax.get_xticklabels(), visible=False)
    v9_text = (f"V9 从信号进场：{float(v9['net_r']):+.2f}R（{REASON.get(v9['exit_reason'], v9['exit_reason'])}）"
               if v9 and v9.get("net_r") is not None and not pd.isna(v9.get("net_r")) else "V9 从信号进场：未平仓/无效")
    jt_text = f"等突破+spike 再进场：{float(jt['net_r']):+.2f}R（{REASON.get(jt['exit_reason'], jt['exit_reason'])}）"
    fig.text(0.05, 0.955, f"#{number:02d}  {trade.symbol} · {trade.timeframe} · Binance 永续 · 突破来源：{line['kind']}"
             f" · V9 信号后第 {int(trade.bars_after_v9)} 根出现突破", color="white", fontsize=13, fontweight="bold")
    fig.text(0.05, 0.93, f"{v9_text}    ｜    {jt_text}    ｜    突破+spike 时间 {bj(trade.signal_bar_open)}（北京）",
             color=FG, fontsize=10.5)
    fig.text(0.05, 0.015, "绿▲/白×=V9 单进出场（绿红框=V9 盈亏框，虚线=1R/2R/3R）  橙▲/橙X=等突破+spike 再进场的进出场（橙点线=它的止损）"
             "  白线=本周期趋势线  浅蓝线=上级趋势线  蓝圈=A点与突破点", color="#8a8f98", fontsize=8.5)
    fig.savefig(path, dpi=90, facecolor=BG)
    plt.close(fig)


def work(args) -> list[dict]:
    symbol, path, meta, trades, sample, out = args
    earliest = study.START - pd.Timedelta(minutes=study.WARMUP_BARS * max(study.TIMEFRAMES.values()))
    base = inc.guarded_5m(Path(path), earliest)
    rows = []
    for timeframe, group in trades.groupby("timeframe"):
        ctx = rebuild(symbol, timeframe, base, meta)
        for trade in group.itertuples(index=False):
            i, s = int(trade.signal_i), int(trade.box_entry_i)
            status, jt = inc.attempt(ctx["prepared"], i)
            if jt is None or int(jt["exit_i"]) != int(trade.exit_i) or not np.isclose(
                    float(jt["entry_price"]), float(trade.entry_price)):
                raise AssertionError(f"joint trade differs from the ledger: {trade.trade_key}")
            v9_status, v9 = inc.attempt(ctx["prepared"], s)
            line = line_of(ctx, i, trade.source)
            number = sample.get(trade.trade_key)
            rows.append(book_row(trade, ctx["frame"].index[s], v9, v9_status, jt, line, number))
            if number is not None:
                draw(Path(out) / f"sample_{number:02d}.png", number, trade, ctx, v9, jt, line)
    return rows


def main(out: Path, workers: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    t = pd.read_csv(LEDGER)
    t = t.loc[t.arm == "box_any"].copy()
    t["signal_bar_open"] = pd.to_datetime(t.signal_bar_open, utc=True, format="mixed")
    rng = np.random.default_rng(SEED)
    picks = []
    for tf in ("15m", "1h"):
        pool = t.loc[(t.timeframe == tf) & (t.status == "closed")].sort_values(["signal_bar_open", "symbol"])
        picks += pool.iloc[np.sort(rng.choice(len(pool), size=PER_TF, replace=False))].trade_key.tolist()
    order = t.set_index("trade_key").loc[picks].sort_values(["timeframe", "signal_bar_open"]).index.tolist()
    sample = {key: n for n, key in enumerate(order, 1)}
    files, meta = study.series_files(), study.symbol_meta()
    jobs = [(sym, str(files[sym]), meta[sym], g, {k: v for k, v in sample.items() if k in set(g.trade_key)}, str(out))
            for sym, g in t.groupby("symbol")]
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(work, j) for j in jobs]):
            rows += future.result()
    book = pd.DataFrame(rows).sort_values(["周期", "突破+spike K(北京)", "币种"])
    book.to_csv(out / "逐笔明细_突破spike.csv", index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(out / "逐笔明细_突破spike.xlsx") as xl:
        book.to_excel(xl, sheet_name="全部", index=False)
        book.loc[book["图号"].notna()].sort_values("图号").to_excel(xl, sheet_name="抽样50张", index=False)
    (out / "sample_keys.json").write_text(json.dumps({"seed": SEED, "per_timeframe": PER_TF, "sample": sample},
                                                     ensure_ascii=False, indent=2) + "\n")
    print(len(book), "rows;", int(book["图号"].notna().sum()), "figures")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    main(args.out, args.workers)
