"""ONE screenshot regression for the auxiliary V12 line family, not Pine parity.

Uses the immutable input from the prior ONE audit. OHLC/ATR features consume
only the current bar and its past. Prefix checks compare independently replayed
truncations. The future shown in the case chart is display-only, never a feature.
"""
from pathlib import Path
import gzip
import hashlib
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.monitor import spike_lines as sl
from yoyo.evaluation.spike_v12_local_touch import auxiliary_touch_events, LocalTouchParams
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path("experiments/active/exp-spike-v12-local-touch-20260920-v1")
SOURCE = Path("experiments/active/exp-spike-v112-one-line-audit-20260920-v1")


def plot_case(f, line):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    from matplotlib.ticker import FuncFormatter
    plt.rcParams.update({"font.size": 11, "font.family": "DejaVu Sans"})
    fig, axes = plt.subplots(2, 1, figsize=(15, 9), gridspec_kw={"height_ratios": [1.2, 1]})
    fig.subplots_adjust(left=.075, right=.975, top=.85, bottom=.13, hspace=.38)
    fig.suptitle("ONE / OKX · 15m · V12 local-touch recognition", x=.075, ha="left", y=.96, fontsize=18)
    fig.text(.075, .91, "2026-09-15–17 · Price in USDT · Beijing time (UTC+8) · Frozen local OHLC replay", color="#52525b")
    blue, orange, grey = "#4679C9", "#BF6D27", "#71717a"
    born, broken = int(line["born_i"]), int(line["break_i"])
    for ax, (left, right), title in zip(axes, ((1040, 1231), (1170, 1223)), ("A/B anchors and local C", "C confirmation and two-close breakout")):
        for i in range(left, right + 1):
            row = f.iloc[i]
            tone = blue if row.close >= row.open else grey
            ax.vlines(i, row.low, row.high, colors=tone, lw=.7)
            ax.add_patch(Rectangle((i-.33, min(row.open, row.close)), .66, max(abs(row.close-row.open), 1e-8), facecolor=tone if row.close >= row.open else "white", edgecolor=tone, lw=.7))
        xx = np.arange(max(left, line["ax"]), right+1)
        yy = line["ap"] + (line["bp"]-line["ap"]) * (xx-line["ax"])/(line["bx"]-line["ax"])
        ax.plot(xx, yy, color="#3f3f46", lw=1.3, label="Frozen A/B line")
        for key, name in (("a", "A"), ("b", "B"), ("c", "C · local")):
            x, y = line[key+"x"], line[key+"p"]
            if left <= x <= right:
                ax.scatter([x], [y], facecolors="white", edgecolors=orange, zorder=5, s=38)
                ax.annotate(name, (x,y), xytext=(0,14), textcoords="offset points", ha="center", color="#3f3f46")
        for event, label, style in ((born, "Known: Sep16 20:15 close", ":"), (broken, "Break: Sep17 02:30 close", "--")):
            ax.axvline(event, color=orange, linestyle=style, lw=1.1)
        ticks = np.arange(left+(4-left%4)%4, right+1, 12 if right-left>100 else 8)
        ax.set_xticks(ticks, [f.index[i].tz_convert("Asia/Shanghai").strftime("%m/%d %H:%M") for i in ticks], fontsize=9)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:.7f}"))
        ax.set_xlim(left-1, right+1); ax.set_title(title, loc="left", fontsize=12, pad=18)
        ax.grid(axis="y", color="#e4e4e7", lw=.6); ax.set_axisbelow(True)
        ax.spines[["top", "right"]].set_visible(False)
    axes[1].text(.02,.93, "C: Sep16 19:30 open → known at 20:15 close\nBreak: Sep17 02:15 bar → confirmed at 02:30 close", transform=axes[1].transAxes, va="top", fontsize=10, color="#3f3f46")
    fig.text(.075,.058,"Vertical dotted = C known; dashed = breakout confirmed. X-axis labels are candle OPEN times.\nAuxiliary-family reference only; no V9 long box at breakout, so this is a standalone break, not a joint entry.", fontsize=10, color="#52525b")
    fig.savefig(EXP/"ONE_15m_v12_case.png", dpi=160, facecolor="white")
    plt.close(fig)


def main():
    paths = (Path(__file__), Path("yoyo/evaluation/spike_v12_local_touch.py"), Path("yoyo/evaluation/pine/spike_burst_v12.pine"), EXP/"PROJECT_PLAN.md")
    assert _committed(paths), "Commit source and plan before market replay"
    payload = json.loads(gzip.decompress((SOURCE/"input.json.gz").read_bytes()))
    f = sl.study.v9_facts(sl.frame_of(payload["15m"]["candles"]), 15, "ONE", payload["tick"])
    frame = f["frame"]
    def replay(n, enabled=True):
        df=frame.iloc[:n]
        return auxiliary_touch_events(df.open,df.high,df.low,df.close,df.atr,can_run=f["can_run"][:n],tick=payload["tick"],params=LocalTouchParams(enabled=enabled))
    result = replay(len(frame))
    target = [x for x in result.trace["lines"] if (x["ax"],x["bx"],x["cx"]) == (1066,1182,1189)]
    assert len(target) == 1, target
    target=target[0]
    assert target["born_i"] == 1191
    def stamp(i, close=False):
        return (frame.index[int(i)] + pd.Timedelta(minutes=15 if close else 0)).tz_convert("Asia/Shanghai").isoformat()
    assert stamp(target["break_i"],True) == "2026-09-17T02:30:00+08:00"
    checks=[]
    for n in (1189,1191,1192,1193,1216,1217,1220,len(frame)):
        short=replay(n)
        np.testing.assert_array_equal(result.born_event[:n],short.born_event)
        np.testing.assert_array_equal(result.break_event[:n],short.break_event)
        assert [e for e in result.events if e["i"]<n] == short.events
        checks.append({"prefix_bars":n,"events_identical":True})
    disabled=replay(len(frame),False)
    assert not disabled.born_event.any() and not disabled.break_event.any()
    old=pd.read_csv(SOURCE/"15m_facts.csv.gz")
    assert len(old)==len(frame)
    assert not bool(old.iloc[target["break_i"]].long_open)
    rows=pd.DataFrame(result.trace["lines"])
    for name in ("ax","bx","cx","born_i","break_i"):
        rows[name+"_bj"]=[stamp(i,name in ("born_i","break_i")) if pd.notna(i) else None for i in rows[name]]
    rows.to_csv(EXP/"auxiliary_lines.csv",index=False)
    pd.DataFrame(result.events).to_csv(EXP/"auxiliary_breaks.csv",index=False)
    (EXP/"prefix_checks.json").write_text(json.dumps(checks,indent=2)+"\n")
    summary={"scope":"auxiliary-family reference; not merged Pine parity", "source_commit":subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(),"source_sha256":hashlib.sha256((SOURCE/"input.json.gz").read_bytes()).hexdigest(),"bars":len(frame),"start":stamp(0),"end":stamp(len(frame)-1),"target":dict(target,born_close_bj=stamp(target["born_i"],True),break_close_bj=stamp(target["break_i"],True)),"target_v9_long_box":False,"standalone_break":True,"supplemental_lines":len(rows),"supplemental_breaks":len(result.events),"prefix_checks":len(checks),"disabled_has_events":False}
    (EXP/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    plot_case(frame,target)
    print(json.dumps(summary,indent=2))


if __name__ == "__main__":
    main()
