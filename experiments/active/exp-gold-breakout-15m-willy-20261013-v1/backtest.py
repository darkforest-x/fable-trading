"""Replicate "Gold Breakout 15M Strategy [WillyAlgoTrader]" v1.0.0 on XAUUSD 15m and test it against controls.

Source logic (TradingView PUB;42b9e30300f840c7af2787b46c8617cd, read 2026-10-13), defaults only:
- Broker clock fixed GMT+3. Range = high/low of bars opening 00:00-06:59 broker time, reset daily;
  the first loaded day is skipped.
- From the bar whose *next* bar opens in [07:00, 20:00): buy-stop at range high + sell-stop at range low,
  OCA (first fill cancels the other), one trade per broker day.
- SL = 1% from the breakout level, re-anchored at the entry bar close to 1% from the fill price.
  No take-profit. Market close_all on the bar whose next bar opens >= 20:00 (fills next open).
- Size = 1% equity risk / 1% stop -> 1x notional (cap 10x never binds); commission $0.4/order (ignored).
Fills follow TradingView's broker emulator without bar magnifier: stops fill at the level (or the
open on a gap) and the intrabar path is O->H->L->C when the open is nearer the high, else O->L->H->C.

Data: HistData.com XAUUSD M1 (New York wall-clock, verified via NFP/daily break and OKX overlap) -> UTC -> 15m. Only bar i and earlier decide orders
for bar i+1; outcomes use bars after the fill (labels only).

Controls (same days, same 1% SL, same 20:00 exit, same costs):
- fade: same trigger moment and price, opposite side.
- same_moment_random: same trigger moment and price, coin-flip side (N seeds) -> tests direction info.
- random_time: random bar open in the 07:00-19:45 window of the same day, coin-flip side (N seeds)
  -> tests timing + direction. Matched by symbol, day (time block) and therefore volatility regime.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import zipfile

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[3]
RAW = ROOT / "data" / "histdata" / "XAUUSD"
CACHE = RAW / "xauusd_15m_utc.parquet"
OUT = pathlib.Path(__file__).resolve().parent / "results"

BROKER_SHIFT_H = 3      # "GMT+3" fixed
RANGE_FROM, RANGE_TO, CLOSE_HOUR = 0, 7, 20
SL_PCT = 0.01
TICK = 0.01


def load_15m() -> pd.DataFrame:
    if CACHE.exists():
        return pd.read_parquet(CACHE)
    frames = []
    for z in sorted(RAW.glob("HISTDATA_COM_ASCII_XAUUSD_M1_*.zip")):
        with zipfile.ZipFile(z) as zf:
            name = next(n for n in zf.namelist() if n.endswith(".csv"))
            with zf.open(name) as f:
                frames.append(pd.read_csv(f, sep=";", header=None,
                                          names=["ts", "open", "high", "low", "close", "vol"]))
    m1 = pd.concat(frames, ignore_index=True)
    # HistData documents "EST without DST", but the NFP spike sits at 08:30 local in both seasons and the
    # daily break at 17:00 local, so the stamps are New York wall-clock time; DST gaps fall on the weekend.
    local = pd.to_datetime(m1["ts"], format="%Y%m%d %H%M%S")
    m1["ts"] = local.dt.tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC").dt.tz_localize(None)
    m1 = m1.dropna(subset=["ts"]).drop_duplicates("ts").set_index("ts").sort_index()
    bars = m1.resample("15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    bars.to_parquet(CACHE)
    return bars


def rtick(x: float) -> float:
    return round(round(x / TICK) * TICK, 2)


def path_points(o, h, l, c):
    return (o, h, l, c) if (h - o) <= (o - l) else (o, l, h, c)


def first_trigger(o, h, l, c, long_lvl, short_lvl):
    """Return (side, fill_price, seg_index) of the first stop hit along the TV path, or None."""
    if o >= long_lvl:
        return 1, o, 0
    if o <= short_lvl:
        return -1, o, 0
    pts = path_points(o, h, l, c)
    for k in range(3):
        a, b = pts[k], pts[k + 1]
        if b > a and a < long_lvl <= b:
            return 1, long_lvl, k
        if b < a and a > short_lvl >= b:
            return -1, short_lvl, k
    return None


def sl_hit_after(pts, seg, side, fill, sl):
    """Check SL along the remainder of the bar path after a fill inside segment `seg`."""
    rest = [fill] + list(pts[seg + 1:])
    for p in rest[1:]:
        if (side == 1 and p <= sl) or (side == -1 and p >= sl):
            return True
    return False


def prepare(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    bt = df.index + pd.Timedelta(hours=BROKER_SHIFT_H)
    df["hour"] = bt.hour
    df["day"] = bt.year * 10000 + bt.month * 100 + bt.day
    df["next_hour"] = (bt + pd.Timedelta(minutes=15)).hour
    return df


def run(df: pd.DataFrame, policy: str = "breakout", rng: np.random.Generator | None = None) -> pd.DataFrame:
    """Bar loop mirroring the Pine script. policy: breakout | fade | same_moment_random."""
    O, H, L, C = (df[c].to_numpy() for c in ("open", "high", "low", "close"))
    hour, day, nxt = df["hour"].to_numpy(), df["day"].to_numpy(), df["next_hour"].to_numpy()
    ts = df.index
    first_day = day[0]
    r_hi = r_lo = np.nan
    traded_day = -1
    pos = 0
    entry = sl = np.nan
    entry_i = -1
    pending = None          # (long_lvl, short_lvl, slL, slS)
    pending_close = False
    trades = []
    prev_day = None
    pos_prev = 0

    def close_trade(i, px, reason):
        trades.append(dict(entry_time=ts[entry_i], exit_time=ts[i], day=day[entry_i], side=pos,
                           entry=entry, exit=px, reason=reason, same_bar=(entry_i == i)))

    for i in range(len(df)):
        o, h, l, c = O[i], H[i], L[i], C[i]
        closed_same_bar = False
        # ---- fills for orders placed at previous close ----
        if pos != 0 and pending_close:
            close_trade(i, o, "time")
            pos, pending_close = 0, False
        if pos != 0:
            if pos == 1 and (o <= sl or l <= sl):
                close_trade(i, min(o, sl), "sl"); pos = 0
            elif pos == -1 and (o >= sl or h >= sl):
                close_trade(i, max(o, sl), "sl"); pos = 0
        elif pending is not None:
            lv, sv, slL, slS = pending
            trig = first_trigger(o, h, l, c, lv, sv)
            if trig is not None:
                side, fill, seg = trig
                if policy == "fade":
                    side = -side
                elif policy == "same_moment_random":
                    side = 1 if rng.random() < 0.5 else -1
                if policy == "breakout":
                    plan_sl = slL if side == 1 else slS
                else:
                    plan_sl = rtick(fill * (1 - side * SL_PCT))
                pos, entry, entry_i, sl = side, fill, i, plan_sl
                pts = path_points(o, h, l, c)
                if sl_hit_after(pts, seg, side, fill, plan_sl):
                    close_trade(i, plan_sl, "sl")
                    pos, closed_same_bar = 0, True
            pending = None
        # ---- script logic at bar close ----
        if day[i] != prev_day:
            r_hi = r_lo = np.nan
            prev_day = day[i]
        if RANGE_FROM <= hour[i] < RANGE_TO:
            r_hi = h if np.isnan(r_hi) else max(r_hi, h)
            r_lo = l if np.isnan(r_lo) else min(r_lo, l)
        if pos != 0 and pos_prev == 0:
            traded_day = day[i]
            sl = rtick(entry * (1 - pos * SL_PCT))          # re-anchor to fill
        if closed_same_bar:
            traded_day = day[i]
        range_ready = not np.isnan(r_hi) and r_hi > r_lo
        next_in = RANGE_TO <= nxt[i] < CLOSE_HOUR
        if range_ready and next_in and pos == 0 and traded_day != day[i] and day[i] != first_day:
            pending = (r_hi, r_lo, rtick(r_hi * (1 - SL_PCT)), rtick(r_lo * (1 + SL_PCT)))
        else:
            pending = None
        if pos != 0 and (nxt[i] >= CLOSE_HOUR or nxt[i] < RANGE_TO):
            pending_close = True
        pos_prev = pos
    return pd.DataFrame(trades)


def random_time_table(df: pd.DataFrame, days: np.ndarray) -> pd.DataFrame:
    """Outcome of a bar-open entry for every bar 07:00-19:45 of the given days, both sides.

    Same 1% SL (checked on the full entry bar, stop fills at level or open on a gap) and the same
    market exit at the first bar opening >= 20:00. Seeds then draw one bar and one side per day.
    """
    O, H, L = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    hour, day, nxt = df["hour"].to_numpy(), df["day"].to_numpy(), df["next_hour"].to_numpy()
    stop_after = (nxt >= CLOSE_HOUR) | (nxt < RANGE_TO)       # bar after this one is the exit bar
    in_win = (hour >= RANGE_TO) & (hour < CLOSE_HOUR) & np.isin(day, days)
    nxt_stop = np.full(len(df), len(df))
    k = len(df)
    for i in range(len(df) - 1, -1, -1):
        if stop_after[i]:
            k = i
        nxt_stop[i] = k
    rows = []
    for i0 in np.flatnonzero(in_win):
        j = nxt_stop[i0] + 1                                    # exit bar index (market at open)
        if j >= len(df):
            continue
        e = O[i0]
        for side in (1, -1):
            sl = rtick(e * (1 - side * SL_PCT))
            hit = (L[i0:j] <= sl) if side == 1 else (H[i0:j] >= sl)
            if hit.any():
                k = i0 + int(np.argmax(hit))
                px = sl if k == i0 else (min(O[k], sl) if side == 1 else max(O[k], sl))
            else:
                px = O[j]
            rows.append((day[i0], i0, side, side * (px / e - 1) / SL_PCT))
    return pd.DataFrame(rows, columns=["day", "i0", "side", "R"])


def add_returns(t: pd.DataFrame) -> pd.DataFrame:
    t = t.copy()
    t["ret"] = t["side"] * (t["exit"] / t["entry"] - 1)
    t["R"] = t["ret"] / SL_PCT
    return t


def summarize(t: pd.DataFrame, cost_rt: float = 0.0) -> dict:
    r = t["ret"].to_numpy() - cost_rt
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    eq = np.cumprod(1 + r)
    dd = 1 - eq / np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return dict(n=len(r), mean_R=round(r.mean() / SL_PCT, 4), win=round((r > 0).mean(), 4),
                pf=round(wins / losses, 3) if losses else None, total_ret_pct=round((eq[-1] - 1) * 100, 1),
                max_dd_pct=round(dd.max() * 100, 1), sum_R=round(r.sum() / SL_PCT, 1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=2000)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    df = prepare(load_15m())
    costs = {"gross": 0.0, "cfd_4bp": 0.0004, "okx_taker_10bp": 0.0010, "project_20bp": 0.0020}

    bo = add_returns(run(df, "breakout"))
    bo.to_csv(OUT / "trades_breakout.csv", index=False)
    fade = add_returns(run(df, "fade"))
    res = {"data": dict(first=str(df.index[0]), last=str(df.index[-1]), bars=len(df)),
           "breakout": {k: summarize(bo, v) for k, v in costs.items()},
           "fade": {k: summarize(fade, v) for k, v in costs.items()},
           "breakout_by_side": {("long" if s == 1 else "short"): summarize(bo[bo.side == s]) for s in (1, -1)},
           "breakout_exit_reasons": bo["reason"].value_counts().to_dict()}
    bo["year"] = pd.to_datetime(bo["entry_time"]).dt.year
    res["breakout_by_year"] = {int(y): summarize(g) for y, g in bo.groupby("year")}

    rng = np.random.default_rng(20261013)
    obs = bo["R"].mean()
    # same trigger moment/price, coin-flip side: trigger set is side-independent (OCA, one trade/day)
    assert (bo["entry_time"].to_numpy() == fade["entry_time"].to_numpy()).all()
    both = np.stack([bo["R"].to_numpy(), fade["R"].to_numpy()])
    flips = rng.integers(0, 2, size=(args.seeds, len(bo)))
    smr = both[flips, np.arange(len(bo))].mean(axis=1)
    tab = random_time_table(df, bo["day"].unique())
    tab.to_parquet(OUT / "random_time_table.parquet")
    groups = tab.groupby("day").indices
    keys = list(groups)
    rt = []
    for _ in range(args.seeds):
        pick = [groups[d][rng.integers(len(groups[d]))] for d in keys]
        rt.append(tab["R"].to_numpy()[pick].mean())
    res["random_time_long_only_mean_R"] = round(tab[tab.side == 1]["R"].mean(), 4)
    res["random_time_short_only_mean_R"] = round(tab[tab.side == -1]["R"].mean(), 4)
    for name, dist in (("same_moment_random", smr), ("random_time", rt)):
        dist = np.array(dist)
        res[name] = dict(seeds=len(dist), mean_R=round(dist.mean(), 4), p95=round(np.quantile(dist, 0.95), 4),
                         p_value=round((1 + (dist >= obs).sum()) / (1 + len(dist)), 4))
    (OUT / "summary.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
