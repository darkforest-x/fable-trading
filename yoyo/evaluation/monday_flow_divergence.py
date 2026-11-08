"""Classify CVD at first Monday 1.618 fills using archived Binance aggressor volume."""
from __future__ import annotations

import argparse
import gzip
import json
import subprocess
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-monday-1618-cvd-divergence-20261108-v1"
CFG = json.loads((EXP / "config.json").read_text())
MINUTE = 60_000
TS, OPEN, HIGH, LOW, CLOSE, VOL, COUNT, BUY = range(8)
EMPTY = np.empty((0, 8))
TFS = {"15m": 15, "1h": 60}


def read_minutes(stream):
    """Binance USD-M open time, OHLC, base VOL, count and taker-buy base volume."""
    header = stream.readline().split(b",")[0]
    stream.seek(0)
    arr = pd.read_csv(stream, header=None, skiprows=int(not header.isdigit()),
                      usecols=[0, 1, 2, 3, 4, 5, 8, 9], dtype="float64",
                      float_precision="round_trip").to_numpy()
    if len(arr) and arr[0, TS] > 1e14:
        arr[:, TS] //= 1000
    return arr


@lru_cache(maxsize=3)
def major_month(symbol, month):
    base = ROOT / CFG["major_months"] / symbol
    path = base / f"{symbol}-1m-{month}.zip"
    if not path.is_file():
        return EMPTY
    with zipfile.ZipFile(path) as z, z.open(z.namelist()[0]) as f:
        return read_minutes(f)


@lru_cache(maxsize=6)
def day_bars(symbol, day, major):
    if major:
        arr = major_month(symbol, day.isoformat()[:7])
        start = (day - date(1970, 1, 1)).days * 86_400_000
        return arr[np.searchsorted(arr[:, TS], start):np.searchsorted(arr[:, TS], start + 86_400_000)]
    folder = ROOT / CFG["days"] / symbol
    for suffix, opener in [(".csv.gz", gzip.open), (".csv", open)]:
        path = folder / (day.isoformat() + suffix)
        if path.is_file():
            with opener(path, "rb") as f:
                return read_minutes(f)
    return EMPTY


def touch_tick(symbol, day, minute, side, level):
    """Use ordered prints through first cross; reconcile the whole minute only for data QA."""
    path = ROOT / CFG["ticks"] / symbol / (day.isoformat() + ".csv.gz")
    if not path.is_file():
        return None, "no_tick_file"
    trades = pd.read_csv(path, float_precision="round_trip")
    start = int(minute[TS])
    minute_trades = trades[(trades.ts >= start) & (trades.ts < start + MINUTE)]
    if minute_trades.empty:
        return None, "no_tick_minute"
    price = minute_trades.price.to_numpy()
    qty = minute_trades.qty.to_numpy()
    maker = minute_trades.buyer_maker
    if not (maker.isin([True, False]).all() and minute_trades.ts.is_monotonic_increasing
            and np.isfinite(qty).all() and (qty > 0).all() and np.isfinite(price).all()):
        return None, "invalid_tick_data"
    buys = ~maker.to_numpy(dtype=bool)
    observed = [price[0], price.max(), price.min(), price[-1], qty.sum(), qty[buys].sum()]
    reference = minute[[OPEN, HIGH, LOW, CLOSE, VOL, BUY]]
    if not np.allclose(observed, reference, rtol=1e-8, atol=1e-10):
        return None, "tick_minute_mismatch"
    crossing = np.flatnonzero(side * (price - level) <= 0)
    if not len(crossing):
        return None, "no_tick_cross"
    end = crossing[0]
    delta = np.where(buys[:end+1], qty[:end+1], -qty[:end+1]).sum()
    return (price[end], delta, int(minute_trades.ts.iloc[end])), "matched"


def categories(price_lo, price_hi, cvd_lo, cvd_hi, tolerance):
    """Possible labels over side-normalized price and CVD differences from the pivot."""
    labels = []
    can_break = price_lo < 0
    can_retest = price_hi >= 0 and price_lo <= tolerance
    if can_break and cvd_hi > 0:
        labels.append("regular")
    if can_retest and cvd_lo < 0:
        labels.append("absorption")
    if ((can_break and cvd_lo <= 0) or (can_retest and cvd_hi >= 0)
            or price_hi > tolerance):
        labels.append("none")
    if not labels:
        raise ValueError("Empty feasible category set")
    return labels


def timeline(bars, hit, width, side, level, price_bounds, prefix_bounds):
    """Use 22 closed timeframe bars' OHLC + 1m taker-buy and volume through first touch.

    The pivot's extreme minute and the touch minute have unknown intra-minute paths.
    Only their possible partial volumes enter the bounds, never their closing CVD
    as an observed fill-time value. All intermediate minutes contribute exact delta.
    """
    now = int(bars[hit, TS])
    frame_start = now - now % (width * MINUTE)
    cut = int(np.searchsorted(bars[:, TS], frame_start))
    start = cut - 22 * width
    if start < 0 or bars[start, TS] != frame_start - 22 * width * MINUTE:
        return {"label": "history_gap"}
    window = bars[start:hit+1]
    if (not np.all(np.diff(window[:, TS]) == MINUTE) or not np.isfinite(window).all()
            or (window[:, BUY] < 0).any() or (window[:, BUY] > window[:, VOL]).any()
            or (window[:, LOW] <= 0).any() or (window[:, HIGH] < window[:, LOW]).any()
            or (window[:, COUNT] < 0).any()):
        return {"label": "invalid_or_gapped_history"}
    full = bars[start:cut].reshape(22, width, 8)
    traded = full[:, :, COUNT] > 0
    if not traded.any(axis=1).all():
        return {"label": "empty_timeframe_bar"}
    lo = np.where(traded, full[:, :, LOW], np.inf).min(axis=1)
    hi = np.where(traded, full[:, :, HIGH], -np.inf).max(axis=1)
    extremes = lo if side == 1 else -hi
    windows = np.lib.stride_tricks.sliding_window_view(extremes, 5)
    pivot_indices = np.flatnonzero((windows[:, 2, None] < windows[:, [0, 1, 3, 4]]).all(axis=1)) + 2
    if not len(pivot_indices):
        return {"label": "no_confirmed_pivot"}
    p = pivot_indices[-1]
    pivot = side * extremes[p]
    endpoint = LOW if side == 1 else HIGH
    minute_in_bar = np.flatnonzero(traded[p] & (full[p, :, endpoint] == pivot))[0]
    ref = start + p * width + minute_in_bar
    completed = bars[ref+1:hit]
    mid_flow = (2 * completed[:, BUY] - completed[:, VOL]).sum()
    # Remaining pivot-minute flow after the first print at its extreme.
    flow_lo = mid_flow - (bars[ref, VOL] - bars[ref, BUY]) + prefix_bounds[0]
    flow_hi = mid_flow + bars[ref, BUY] + prefix_bounds[1]
    signed_flow = sorted((side * flow_lo, side * flow_hi))
    signed_price = sorted(side * (px - pivot) for px in price_bounds)
    prev_close = full[:-1, -1, CLOSE]
    tr = np.maximum(hi[1:] - lo[1:], np.maximum(abs(hi[1:] - prev_close), abs(lo[1:] - prev_close)))
    tolerance = 0.25 * tr[-14:].mean()
    labels = categories(*signed_price, *signed_flow, tolerance)
    # At the moment the limit is hit, both of these minute-close CVD readings are public.
    pre_price, pre_flow = side * (level - pivot), side * mid_flow
    lagged = categories(pre_price, pre_price, pre_flow, pre_flow, tolerance)[0]
    return {"label": labels[0] if len(labels) == 1 else "ambiguous", "possible": "|".join(labels),
            "label_pre": lagged, "pivot_minute_ms": int(bars[ref, TS]), "pivot_price": pivot,
            "cvd_diff_min": flow_lo, "cvd_diff_max": flow_hi, "retest_tolerance": tolerance}


def process_symbol(symbol):
    src = ROOT / CFG["native"] / symbol
    group = json.loads((src / "identity.json").read_text())["job"]["group"]
    if group not in {"crypto_1m", "crypto_5m"}:
        return []
    major = group == "crypto_1m"
    columns = ["event_id", "week", "side", "entry", "touch_interval_open", "execution_minutes",
               "entry_interval_open", "reclaim_minutes", "exit_mode", "population", "closed", "reason", "net_bp"]
    trades = pd.read_csv(src / "trades.csv.gz", usecols=columns)
    sample = trades[(trades.population == "signal") & (trades.exit_mode == "near_monday_edge")
                    & (trades.reclaim_minutes == 60)].sort_values("touch_interval_open")
    out = []
    for r in sample.itertuples():
        stamp = pd.Timestamp(r.touch_interval_open)
        day = stamp.date()
        row = {"event": r.event_id, "symbol": symbol, "side": int(r.side), "week": r.week,
               "touch_interval_open": r.touch_interval_open, "entry_limit": r.entry, "exit_reason": int(r.reason),
               "net_bp": r.net_bp if r.closed and r.reason != 0 else np.nan, "tick_exact": False}
        out.append(row)
        status = "source_bad_event" if r.event_id in CFG["bad_events"] else None
        if pd.Timestamp(r.entry_interval_open) != stamp:
            status = "different_fill_interval"
        if not status:
            bars = np.concatenate([day_bars(symbol, day - timedelta(days=1), major), day_bars(symbol, day, major)])
            ms = stamp.value // 1_000_000
            i = np.searchsorted(bars[:, TS], ms)
            probe = bars[i:i+r.execution_minutes]
            if len(probe) != r.execution_minutes or not np.array_equal(probe[:, TS], ms + MINUTE * np.arange(r.execution_minutes)):
                status = "missing_touch_interval"
            else:
                extremum = LOW if r.side == 1 else HIGH
                crossings = np.flatnonzero((probe[:, COUNT] > 0) & (r.side * (probe[:, extremum] - r.entry) <= 0))
                if not len(crossings):
                    status = "no_matching_cross"
        if status:
            row.update(label_15m=status, label_1h=status)
            continue
        hit = i + crossings[0]
        m = bars[hit]
        tick, row["tick_status"] = touch_tick(symbol, day, m, r.side, r.entry)
        row["touch_minute_ms"] = int(m[TS])
        if tick is None:
            prices = (m[OPEN], m[OPEN]) if r.side * (m[OPEN] - r.entry) <= 0 else (m[extremum], r.entry)
            prefix = (m[BUY] - m[VOL], m[BUY])
        else:
            px, delta, row["touch_tick_ms"] = tick
            row["tick_exact"] = True
            prices, prefix = (px, px), (delta, delta)
        row["touch_price_min"], row["touch_price_max"] = sorted(prices)
        for suffix, width in TFS.items():
            values = timeline(bars, hit, width, r.side, r.entry, prices, prefix)
            row.update({f"{key}_{suffix}": value for key, value in values.items()})
    return out


def build(workers):
    base = ROOT / CFG["native"]
    symbols = sorted(p.name for p in base.iterdir() if (p / "trades.csv.gz").is_file() and p.name != "XAUUSD")
    # Start long monthly-archive jobs early, alongside local touch-day reads.
    symbols.sort(key=lambda s: (s not in {"BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","DOGEUSDT","XRPUSDT"}, s))
    rows = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        from concurrent.futures import as_completed
        futures = [pool.submit(process_symbol, s) for s in symbols]
        for i, future in enumerate(as_completed(futures), 1):
            rows.extend(future.result())
            if i % 100 == 0:
                print(f"{i}/{len(symbols)} symbols, {len(rows)} events", flush=True)
    frame = pd.DataFrame(rows).sort_values(["touch_interval_open", "symbol", "side"])
    if not frame.event.is_unique or not frame[["label_15m", "label_1h"]].notna().all().all():
        raise ValueError("Incomplete or duplicated event keys")
    return frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--summarize", action="store_true")
    args = parser.parse_args()
    events_file = EXP / "events.csv.gz"
    if not args.summarize:
        events = build(args.workers)
        tmp = events_file.with_suffix(".tmp")
        events.to_csv(tmp, index=False, compression="gzip")
        tmp.replace(events_file)
    else:
        events = pd.read_csv(events_file)
    from yoyo.evaluation.monday_flow_divergence_stats import summarize, plot
    summary = summarize(events)
    summary["builder_commit"] = subprocess.check_output(["git", "log", "-1", "--format=%H", "--", __file__], cwd=ROOT, text=True).strip()
    summary["first_touch_utc"] = events.touch_interval_open.min()
    summary["last_touch_utc"] = events.touch_interval_open.max()
    (EXP / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    plot(summary, EXP / "win_rates.png")


if __name__ == "__main__":
    main()
