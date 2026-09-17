"""Split every trendline crossing into "price rose" and "line came down".

`docs/learnings/a-descending-trendline-break-is-a-crossing-not-a-breakout.md`
established that the owner's signal is a crossing of a moving threshold, and a
crossing has two causes that mean opposite things: price climbed through a line
that stayed put, or the line descended into price that never moved. The V10
gate cannot tell them apart, so this audit measures how much of the pool's
admitted crossings are of each kind.

Descriptive only. It changes no arm, selects nothing, and is not a gate: a
crossing-type filter would be a new entry rule and therefore a new experiment
with its own single variable (CLAUDE.md rule 4).

The decomposition is exact. Between the bar a line was born and the bar it was
crossed, the gap between close and line closed by

    (close_break - line_break) - (close_born - line_born)
      = (close_break - close_born) + (line_born - line_break)
      =  price_move                +  line_drop

so the two terms sum to the whole move and can be read as shares. Both are
reported in ATR units at the crossing bar, because a raw currency amount is not
comparable across 3,531 streams.

Reads only the pre-holdout prefix: no bar opening at or after 2026-05-04.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v10_full_replay import CUT, truncate
from yoyo.evaluation.trendline_break import trendline_breaks


def crossings(key: str) -> pd.DataFrame:
    """One row per close-confirmed crossing in one stream, both directions."""
    context = base.load_verified_stream(engine.RAW / "streams" / key)
    context, kept, _ = truncate(context, CUT)
    rows: list[dict] = []
    if kept == 0:
        return pd.DataFrame(rows)
    frame = context.cache["bars"]
    gap = context.cache["data_gap"].reindex(frame.index).fillna(True).to_numpy(bool)
    delta = frame.index.to_series().diff().ne(pd.Timedelta(minutes=context.minutes)).to_numpy()
    if len(delta):
        delta[0] = False
    gap = gap | delta
    arrays = tuple(frame[name].to_numpy(float) for name in ("high", "low", "close", "atr"))
    close, atr = arrays[2], arrays[3]
    for side, direction in ((1, 1), (-1, -1)):
        result = trendline_breaks(*arrays, gap, float(context.cache["tick"]), direction=direction)
        births = np.array([line["born_i"] for line in result.anchors], dtype=np.int64)
        for break_i in np.flatnonzero(result.break_event):
            earlier = births[births <= break_i]
            if not len(earlier):
                continue
            born_i = int(earlier[-1])
            line_born = result.line_price[born_i]
            line_break = result.line_price[break_i]
            if not (np.isfinite(line_born) and np.isfinite(line_break)):
                continue
            unit = atr[break_i]
            if not np.isfinite(unit) or unit <= 0:
                continue
            # side=1: the line falls and price rises, so both terms are signed
            # so that a positive value means "this side closed the gap".
            price_move = side * (close[break_i] - close[born_i])
            line_drop = side * (line_born - line_break)
            rows.append({"stream_key": key, "timeframe_min": context.minutes, "side": side,
                         "break_i": int(break_i), "born_i": born_i, "bars_held": int(break_i - born_i),
                         "price_move_atr": float(price_move / unit), "line_drop_atr": float(line_drop / unit),
                         "led_by": "price" if price_move > line_drop else "line"})
    return pd.DataFrame(rows)


def run(destination: Path, workers: int = 6, limit: int | None = None):
    keys = sorted(p.name for p in (engine.RAW / "streams").iterdir() if (p / "completion.json").is_file())
    keys = keys[:limit] if limit is not None else keys
    frames = []
    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        tasks = {pool.submit(crossings, key): key for key in keys}
        for number, future in enumerate(as_completed(tasks), 1):
            frames.append(future.result())
            if number % 500 == 0 or number == len(keys):
                print(json.dumps({"completed": number, "target": len(keys),
                                  "elapsed_seconds": round(time.perf_counter() - start, 1)}), flush=True)
    table = pd.concat(frames, ignore_index=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(destination, index=False, compression={"method": "gzip", "mtime": 0})
    shares = table.groupby(["timeframe_min", "side", "led_by"]).size().unstack(fill_value=0)
    shares["line_share"] = shares.get("line", 0) / shares.sum(axis=1)
    print(shares.to_string())
    print(table.groupby("led_by")[["price_move_atr", "line_drop_atr", "bars_held"]].median().to_string())
    return table


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run(args.out, workers=args.workers, limit=args.limit)
