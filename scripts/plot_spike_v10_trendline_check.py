"""Render one stream's detected trendlines so the owner can eyeball them on TV.

The replay's numbers cannot show whether the ported indicator draws the lines
the owner's script draws. This does: pick a stream, plot the candles, the lines
the port built, the bars where a close-confirmed break landed, and the V9
confirmations with their break age. Open the same symbol and timeframe in
TradingView with 「主下降趋势线 · 关键高点 V1」 and the lines should coincide.

Reads only the frozen pre-holdout prefix of the pool; no bar opening at or
after 2026-05-04 is loaded.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v10 import break_ages
from yoyo.evaluation.spike_v10_full_replay import CUT, truncate
from yoyo.evaluation.spike_v9_full_replay import prepare_v9
from yoyo.evaluation.trendline_break import trendline_breaks


def render(key: str, bars_shown: int, destination: Path) -> dict:
    context = base.load_verified_stream(engine.RAW / "streams" / key)
    context, kept, dropped = truncate(context, CUT)
    prepared = engine.prepare_arm(context, arm="v8")
    treatment, decisions = prepare_v9(prepared)
    frame, gap, tick = treatment.frame, treatment.gap, float(context.cache["tick"])
    ages = break_ages(frame, gap, tick)
    arrays = tuple(frame[name].to_numpy(float) for name in ("high", "low", "close", "atr"))
    up = trendline_breaks(*arrays, gap, tick, direction=1)
    down = trendline_breaks(*arrays, gap, tick, direction=-1)
    start = max(0, len(frame) - bars_shown)
    x = np.arange(len(frame))
    figure, axis = plt.subplots(figsize=(19, 8.5))
    body = frame.iloc[start:]
    rising = body.close >= body.open
    axis.vlines(x[start:], body.low, body.high, color="#555", linewidth=.6, zorder=1)
    for mask, colour in ((rising, "#008F82"), (~rising, "#D34B66")):
        part = body.loc[mask]
        axis.vlines(x[start:][mask.to_numpy()], part[["open", "close"]].min(axis=1),
                    part[["open", "close"]].max(axis=1), color=colour, linewidth=2.4, zorder=2)
    drawn = {}
    marks = {}
    for name, result, colour, marker in (("resistance", up, "#1f77b4", "^"), ("support", down, "#AD7B29", "v")):
        count = 0
        for line in result.anchors:
            if line["born_i"] < start:
                continue
            # Draw each line only over the span where the replay held it live.
            end = line["born_i"]
            while end + 1 < len(frame) and result.line_active[end + 1]:
                end += 1
            span = np.arange(line["x1"], end + 1)
            slope = (line["y2"] - line["y1"]) / (line["x2"] - line["x1"])
            axis.plot(span, line["y1"] + slope * (span - line["x1"]), color=colour, linewidth=1.1, zorder=3)
            count += 1
        hits = np.flatnonzero(result.break_event)
        hits = hits[hits >= start]
        direction = "upward" if name == "resistance" else "downward"
        axis.scatter(hits, frame.close.to_numpy()[hits], marker=marker, s=70, color=colour, zorder=5,
                     label=f"{direction} break of the {name} line ({len(hits)})")
        drawn[name] = count
        marks[name] = int(len(hits))
    breaks = np.flatnonzero(up.break_event)
    breaks = breaks[breaks >= start]
    shown = decisions.loc[decisions.local_i.ge(start)]
    for label, marker, colour in (("long", "o", "#008F82"), ("short", "o", "#D34B66")):
        side = 1 if label == "long" else -1
        part = shown.loc[shown.side.eq(side) & shown.v9.astype(bool)]
        axis.scatter(part.local_i, frame.close.to_numpy()[part.local_i.to_numpy(int)], marker=marker,
                     s=46, facecolors="none", edgecolors=colour, linewidths=1.6, zorder=6,
                     label=f"V9 {label} confirmation ({len(part)})")
        for row in part.itertuples(index=False):
            age = int(ages.long_break_age.iloc[row.local_i] if side == 1 else ages.short_break_age.iloc[row.local_i])
            axis.annotate("none" if age < 0 else str(age), (row.local_i, frame.close.iloc[row.local_i]),
                          textcoords="offset points", xytext=(0, 11 if side == 1 else -16),
                          ha="center", fontsize=7.5, color=colour)
    ticks = np.linspace(start, len(frame) - 1, 9).astype(int)
    axis.set_xticks(ticks)
    axis.set_xticklabels([frame.index[i].strftime("%Y-%m-%d") for i in ticks], fontsize=8)
    identity = context.identity
    axis.set_title(f"{identity['venue']} {identity['symbol']} {identity['timeframe_min']}m · "
                   f"main trendlines and close-confirmed breaks · last {len(frame)-start} bars before "
                   f"{CUT.date()} · numbers are the break age at each V9 confirmation", fontsize=10)
    axis.legend(loc="upper left", fontsize=8, framealpha=.9)
    axis.grid(alpha=.12)
    figure.tight_layout()
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=125)
    plt.close(figure)
    return {"key": key, "bars_kept": kept, "bars_dropped": dropped, "lines_drawn_in_view": drawn,
            "breaks_in_view": marks, "total_long_breaks": int(up.break_event.sum()),
            "total_short_breaks": int(down.break_event.sum()), "pivot_ties": up.pivot_ties,
            "v9_signals": int(decisions.v9.astype(bool).sum()),
            "first_bar": str(frame.index[0]), "last_bar": str(frame.index[-1])}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="okx_240m_70ef73bf59b6f387")
    parser.add_argument("--bars", type=int, default=420)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(render(args.key, args.bars, args.out))
