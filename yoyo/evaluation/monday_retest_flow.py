"""Second tests of the Monday extension. Price entries and exits come from Nautilus."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_flow_divergence as flow
from yoyo.evaluation.monday_flow_divergence import TS, OPEN, HIGH, LOW, CLOSE, VOL, COUNT, BUY, MINUTE

ROOT = flow.ROOT
EXP = ROOT / "experiments/active/exp-monday-retest-cvd-20261108-v1"
CFG = json.loads((EXP / "config.json").read_text())
WIDTHS = {"15m": 15, "1h": 60}
BAD = set(flow.CFG["bad_events"])


def find_retest(bars, first, last, step, width, side, limit, rebound):
    """First 15m/1h closed recovery and first subsequent hit before last, inclusive.

    Bars are continuous inside the original ledger segment. Only a complete UTC
    timeframe close arms the limit. Price extremes after that close find its
    first eligible fill interval. Neither CVD nor subsequent outcomes enter.
    """
    span = width // step
    first_close = first + int((-(int(bars[first, TS]) // MINUTE + step)) % width) // step
    closes = np.arange(first_close, last + 1, span, dtype=int)
    closes = closes[closes - span + 1 >= 0]
    closes = closes[bars[closes, TS] - bars[closes-span+1, TS] == (span-1)*step*MINUTE]
    crossed = closes[side * (bars[closes, CLOSE]-limit) >= rebound]
    if not len(crossed):
        return None, None
    armed = int(crossed[0])
    col = LOW if side == 1 else HIGH
    hits = np.flatnonzero(side*(bars[armed+1:last+1, col]-limit) <= 0)
    return armed, armed + 1 + int(hits[0]) if len(hits) else None


def tolerance_at(bars, hit, step, width):
    """Quarter of 14-bar arithmetic ATR from fully closed UTC timeframe OHLC."""
    start = int(bars[hit, TS]) // (width*MINUTE) * (width*MINUTE)
    j = int(np.searchsorted(bars[:, TS], start))
    count = 15 * width // step
    if j < count or bars[j-count, TS] != start - 15 * width*MINUTE:
        return None
    block = bars[j-count:j]
    if not (np.diff(block[:, TS]) == step*MINUTE).all():
        return None
    shaped = block.reshape(15, width//step, 8)
    hi = shaped[1:, :, HIGH].max(axis=1)
    lo = shaped[1:, :, LOW].min(axis=1)
    close = shaped[:-1, -1, CLOSE]
    return .25 * np.maximum(hi-lo, np.maximum(abs(hi-close), abs(lo-close))).mean()


@lru_cache(maxsize=16)
def fine_day(symbol, day_number):
    day = pd.Timestamp(day_number * 86_400_000, unit="ms", tz="UTC").date()
    return flow.day_bars(symbol, day, False)


def refined_bar(symbol, bar, step):
    """Use a full matching minute breakdown, otherwise retain the original bar."""
    if step == 1:
        return bar[None, :]
    ts = int(bar[TS])
    d = fine_day(symbol, ts // 86_400_000)
    i = np.searchsorted(d[:, TS], ts)
    fine = d[i:i+step]
    if len(fine) != step or not np.array_equal(fine[:, TS], ts + MINUTE*np.arange(step)):
        return bar[None, :]
    observed = [fine[0, OPEN], fine[:, HIGH].max(), fine[:, LOW].min(), fine[-1,CLOSE], fine[:,VOL].sum()]
    if not np.allclose(observed, bar[[OPEN,HIGH,LOW,CLOSE,VOL]], rtol=1e-8, atol=1e-12):
        return bar[None, :]
    return fine


def has_flow(bars):
    return (np.isfinite(bars[:, BUY]) & np.isfinite(bars[:, VOL]) &
            (bars[:, BUY] >= 0) & (bars[:, BUY] <= bars[:, VOL]))


def delta(bars):
    return float((2 * bars[:, BUY] - bars[:, VOL]).sum())


def middle_delta(symbol, bars, step):
    """Between the endpoint bars all delta is known if all input volume is known."""
    known = has_flow(bars)
    total = delta(bars[known])
    for bar in bars[~known]:
        finer = refined_bar(symbol, bar, step)
        if not has_flow(finer).all():
            return None
        total += delta(finer)
    return total


def endpoint(symbol, base, step, side, level, reference):
    fine = refined_bar(symbol, base, step)
    if not has_flow(fine).all():
        return None
    col = LOW if side == 1 else HIGH
    can_trade = (fine[:, COUNT] > 0) | ~np.isfinite(fine[:, COUNT])
    hits = np.flatnonzero(can_trade & (side*(fine[:, col]-level) <= 0))
    if not len(hits):
        return None
    i = hits[0]
    m = fine[i]
    minute_precise = step == 1 or len(fine) == step
    tick = None
    if minute_precise:
        day = pd.Timestamp(int(m[TS]), unit="ms", tz="UTC").date()
        tick, _ = flow.touch_tick(symbol, day, m, side, level)
    if reference:
        tail = delta(fine[i+1:])
        if tick:
            exact = tail + (2*m[BUY]-m[VOL]) - tick[1]
            bounds = (exact, exact)
        else:
            bounds = (tail + m[BUY]-m[VOL], tail + m[BUY])
        return bounds, tail, int(m[TS]), minute_precise, bool(tick)
    prior = delta(fine[:i])
    if tick:
        prices = (tick[0], tick[0])
        bounds = (prior+tick[1], prior+tick[1])
    else:
        prices = (m[OPEN], m[OPEN]) if side*(m[OPEN]-level) <= 0 else (level, m[col])
        bounds = (prior + m[BUY]-m[VOL], prior + m[BUY])
    return bounds, prior, int(m[TS]), minute_precise, bool(tick), sorted(prices)


def classify(symbol, bars, first, armed, hit, step, width, side, level):
    """Compare cumulative base delta from the first probe extreme to the retest.

    Reference uses only first-touch through recovery-close OHLC. Latest-price and
    partial-volume intervals cover unknown order inside the retest subbar.
    The feasible set never treats a post-touch minute close as an observed input.
    """
    col = LOW if side == 1 else HIGH
    segment = bars[first:armed+1]
    ref = first + int(np.argmin(side*segment[:,col]))
    extreme = bars[ref,col]
    tol = tolerance_at(bars, hit, step, width)
    outputs = dict(first_extreme=extreme, first_extreme_bar=int(bars[ref,TS]), tolerance=tol)
    if tol is None:
        return dict(outputs, label="atr_missing")
    before = endpoint(symbol, bars[ref], step, side, extreme, True)
    after = endpoint(symbol, bars[hit], step, side, level, False)
    if before is None or after is None:
        return dict(outputs, label="endpoint_missing")
    mid = middle_delta(symbol, bars[ref+1:hit], step)
    if mid is None:
        return dict(outputs, label="intervening_flow_missing")
    bounds1, tail, ts1, m1, t1 = before
    bounds2, prior, ts2, m2, t2, prices = after
    cvd = [mid + a + b for a, b in zip(bounds1, bounds2)]
    signed_prices = sorted(side*(p-extreme) for p in prices)
    signed_delta = sorted(side*v for v in cvd)
    possibilities = flow.categories(*signed_prices, *signed_delta, tol)
    # CVD at the closed reference subbar vs the last closed pre-retest subbar.
    # Both were already public at the time of the resting limit fill.
    price_proxy = prices[0] if prices[0] == prices[1] else level
    p = side*(price_proxy-extreme)
    d = side*(mid+tail+prior)
    return dict(outputs, label=possibilities[0] if len(possibilities)==1 else "ambiguous",
                possible="|".join(possibilities), asof_label=flow.categories(p,p,d,d,tol)[0],
                cvd_min=cvd[0], cvd_max=cvd[1], reference_subbar_ms=ts1, retest_subbar_ms=ts2,
                both_1m=m1 and m2, ref_tick=t1, retest_tick=t2, cross_min=prices[0], cross_max=prices[1])


def frozen_rows(src):
    cols = ["event_id","week","side","entry","execution_minutes","touch_interval_open",
            "touch_i_hint","monday_high","monday_low","target_price","reclaim_minutes","exit_mode","population"]
    all_rows = pd.read_csv(src / "trades.csv.gz", usecols=cols)
    frozen = all_rows[(all_rows.population=="signal") & (all_rows.reclaim_minutes==60) &
                      (all_rows.exit_mode=="near_monday_edge")].copy()
    if not frozen.event_id.is_unique:
        raise ValueError("Duplicate baseline event")
    return frozen


def symbol_orders(symbol, bars, frozen, step, decimals):
    rows, orders = [], {}
    ms = bars[:,TS].astype(np.int64)
    breaks = np.r_[np.flatnonzero(np.diff(ms) != step*MINUTE), len(bars)-1]
    for r in frozen.itertuples():
        first = int(r.touch_i_hint)
        first_ms = int(pd.Timestamp(r.touch_interval_open).value//1_000_000)
        if first >= len(bars) or ms[first] != first_ms:
            raise ValueError(f"Original bar index/time mismatch for {r.event_id}")
        end = int(breaks[np.searchsorted(breaks,first)])
        next_week = (pd.Timestamp(r.week) + pd.Timedelta(days=7)).tz_localize("America/New_York")
        next_week_ms = next_week.value//1_000_000
        last = min(end, int(np.searchsorted(ms, next_week_ms))-1)
        side, limit = r.side, r.entry
        col = LOW if side == 1 else HIGH
        raw_ok = side*(bars[first,col]-limit) <= 0
        for tf,width in WIDTHS.items():
            out = dict(event=r.event_id, symbol=symbol, week=r.week, side=side, timeframe=tf,
                       first_ms=first_ms, limit=limit, trade_id=None, status="source_bad_event")
            rows.append(out)
            if r.event_id in BAD:
                continue
            if not raw_ok:
                out["status"] = "first_price_mismatch"
                continue
            rebound = CFG["rebound_fraction"]*(r.monday_high-r.monday_low)
            armed, hit = find_retest(bars,first,last,step,width,side,limit,rebound)
            if armed is None or hit is None:
                status = "no_recovery" if armed is None else "no_retest"
                if ms[last]+step*MINUTE < next_week_ms:
                    status += "_right_censored"
                out["status"] = status
                if armed is not None:
                    out["armed_close_ms"] = ms[armed]+step*MINUTE
                continue
            key = f"{r.event_id}:retest:{int(ms[hit])}"
            out.update(status="retested", trade_id=key, armed_close_ms=ms[armed]+step*MINUTE,
                       retest_bar_ms=ms[hit], expected_i=hit)
            out.update(classify(symbol,bars,first,armed,hit,step,width,side,limit))
            if key not in orders or orders[key]["entry_i"] > armed:
                orders[key] = dict(trade_id=key,side=side,entry=limit,entry_i=armed,
                                   expire_i=last,end_i=end,target_price=round(r.target_price,decimals))
    return pd.DataFrame(rows), pd.DataFrame(orders.values())


def process(symbol):
    from yoyo.evaluation.monday_retest_flow_data import load_series
    from yoyo.evaluation.monday_retest_flow_native import run_orders
    src = ROOT/CFG["source"]/symbol
    dest = EXP/"symbols"/symbol
    if (dest/"done.json").exists():
        return json.loads((dest/"done.json").read_text())
    dest.mkdir(parents=True,exist_ok=True)
    job = json.loads((src/"identity.json").read_text())["job"]
    prior = json.loads((src/"done.json").read_text())
    step = job["execution_minutes"]
    bars = load_series(symbol,job,prior)
    cols = ["open","high","low","close"]
    prices = pd.DataFrame(bars[:,OPEN:CLOSE+1], columns=cols, index=pd.to_datetime(bars[:,TS].astype(np.int64),unit="ms",utc=True))
    decimals = max(2,min(9,int(np.ceil(-np.log10(prices.to_numpy().min())))+7))
    events, orders = symbol_orders(symbol,bars,frozen_rows(src),step,decimals)
    if orders.empty:
        trades, audit = pd.DataFrame(), {}
    else:
        trades,audit = run_orders(symbol,prices.round(decimals),orders,step,10.**-decimals)
        if set(trades.trade_id)!=set(orders.trade_id) or not trades.trade_id.is_unique:
            raise ValueError(f"{symbol}: expected retests do not match native fills")
        events = events.merge(trades,on="trade_id",how="left",validate="many_to_one")
        filled = events[events.status=="retested"]
        if not (filled.entry_i==filled.expected_i).all():
            raise ValueError(f"{symbol}: native retest clock mismatch")
        trades.to_csv(dest/"native.csv.gz",index=False)
    events.to_csv(dest/"events.csv.gz",index=False)
    receipt = dict(symbol=symbol,first_touches=len(events)//2, trades=len(trades),
                   opportunities=len(orders), views=int(events.status.eq("retested").sum()) if len(events) else 0,
                   step=step, bars=len(bars), flow_bars=int(has_flow(bars).sum()))
    (dest/"audit.json").write_text(json.dumps(audit,allow_nan=False,default=str))
    temp = dest/"done.tmp"
    temp.write_text(json.dumps(receipt))
    temp.replace(dest/"done.json")
    return receipt


def run(workers):
    src=ROOT/CFG["source"]
    syms=sorted(p.name for p in src.iterdir() if (p/"trades.csv.gz").exists() and p.name!="XAUUSD")
    done={p.parent.name for p in (EXP/"symbols").glob("*/done.json")}
    todo=[s for s in syms if s not in done]
    print(f"{len(todo)} of {len(syms)} symbols pending",flush=True)
    fails = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs={pool.submit(process,s): s for s in todo}
        for i,f in enumerate(as_completed(futs),1):
            try:
                f.result()
            except Exception as error:
                fails[futs[f]] = repr(error)
                print("ERROR",futs[f],repr(error),flush=True)
            if i % 25==0:
                print(f"{len(done)+i}/{len(syms)}; {len(fails)} failed",flush=True)
    if fails:
        raise RuntimeError(f"{len(fails)} failures: {dict(list(fails.items())[:5])}")


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--workers",type=int,default=4)
    parser.add_argument("--summarize",action="store_true")
    args=parser.parse_args()
    if not args.summarize:
        run(args.workers)
    from yoyo.evaluation.monday_retest_flow_summary import write_summary
    write_summary(EXP)
