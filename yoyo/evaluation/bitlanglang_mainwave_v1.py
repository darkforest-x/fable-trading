"""Bit浪浪 main-wave system v1: position ① (box breakout, pullback, 5m turn) with the video's beginner standards.

Source: Speculation Lab, "全网首发！Bit浪浪把956美元做到4000万的交易系统完整版" (YouTube 0HmddXBIZU0,
2026-10-11). The owner asked for a detailed read and a handbook, then 「要」 to the follow-up
backtest proposed in its chapter 12: test the beginner standard of position ① first (H1), then add
the leader filter (H2), then the BTC season veto (H3), one layer at a time, against matched random
entries and against the same 5m confirmation fired at random.

Rules (every daily field uses complete UTC days up to the breakout day; 5m fields use the trigger
bar and earlier):

  box        the 14 complete days before breakout day d: top = max high, bottom = min low; its
             height <= 4.0 daily ATR14 as of d-1 (calibrated on the video's PEOPLE 2024-05-09 and
             INJ 2023-10-19 breakouts, 2.75 and 3.23 ATR)
  breakout   close(d) > top and volume(d) >= 3x the mean volume of d-7..d-1
  pullback   in the 10 days after d, a 5m low <= top x 1.03 (back within 3% of the box top or
             inside the box); the setup dies at the first 5m close below the box bottom
  turn       5m: a low L that is the lowest of the 48 bars before it; the next 3 bars stay >= L;
             the first later bullish bar whose close exceeds the previous bar's high triggers,
             within 48 bars of L and before any lower low; entry at the next 5m open
  stop       L x (1 - 0.75%)
  targets    daily pivot highs (5 days each side, confirmed by day d, within 365 days) above
             entry x 1.005, nearest first; missing levels are filled with round numbers (integer
             multiples of 10^floor(log10(entry))); TP2 >= TP1 x 1.01. Skip the trigger when the
             mean of the two target gains < 3x the stop distance
  exits      half at TP1, then the rest's stop moves to the entry; the rest exits at TP2, at the
             entry, or after 30 days at the close. Stop first when stop and TP1 print in one bar;
             gaps fill at the open; 0.2% round-trip cost on the whole position
  attempts   up to 3 trades per setup, one open position per symbol at a time

H2 adds the leader filter at day d: a new face on the volume board (top 10 alts by quote volume
on d, not top 10 on d-7..d-1), it rose at least as much as BTC over BTC's last up-leg (BTC low in
d-60..d-1 to the following high, BTC leg >= 5%), and on BTC down days (<= -2%) in d-30..d-1 it
fell no more than BTC on average. H3 adds the season veto: BTC close(d) > SMA50 and SMA50 rising
over 5 days. Each trade gets 20 matched random entries (same symbol and month, 5m ATR/price
within 0.5-2x, same stop in ATR units, same target R multiples) and 20 same-confirmation random
entries (5m turn triggers of the same symbol and month in the same ATR band, their own stop,
the trade's target R multiples).
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as v1
from yoyo.evaluation import market_sync_shock_v2 as v2

EXP = Path("experiments/active/exp-bitlanglang-mainwave-20261011-v1")
CONFIG = EXP / "config.json"
DAY_MS, BAR_MS = 86_400_000, 300_000

BOX_DAYS, BOX_MAX_ATR, VOL_DAYS, VOL_MULT = 14, 4.0, 7, 3.0
PULLBACK, SETUP_DAYS, MAX_ATTEMPTS = 0.03, 10, 3
LOW_LOOKBACK, HOLD_BARS, TRIGGER_EXPIRY, STOP_PAD = 48, 3, 48, 0.0075
PIVOT_SIDE, PIVOT_DAYS, TP_MIN_GAP, TP2_MIN_STEP, MIN_PLAN_R = 5, 365, 0.005, 0.01, 3.0
MAX_HOLD = 30 * 288
COST = 0.002
TOP_N, NEW_DAYS, WAVE_DAYS, WAVE_MIN, DOWN_DAYS, BTC_DOWN = 10, 7, 60, 0.05, 30, -0.02
SEASON_SMA, SEASON_SLOPE = 50, 5
N_CONTROLS, VOL_BAND = 20, (0.5, 2.0)
FROZEN_MAX = 0.10
HYPOTHESES = ("H1", "H2", "H3")


# ---------------------------------------------------------------- daily layer

def daily_bars(raw: pd.DataFrame) -> pd.DataFrame:
    """Complete UTC days from 5m bars, with quote volume and the share of frozen 5m bars."""
    bars = v2.resample_hl(raw, 1440)
    day = raw.ts.to_numpy() // DAY_MS * DAY_MS
    extra = raw.assign(day=day, qv=raw.close * raw.volume,
                       frozen=((raw.high == raw.low) | (raw.volume <= 0)).astype(float)).groupby("day")
    bars["qv"] = extra.qv.sum().reindex(bars.index)
    bars["frozen"] = extra.frozen.mean().reindex(bars.index)
    return bars


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    prev = close.shift()
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def daily_features(d: pd.DataFrame) -> pd.DataFrame:
    """Box, breakout and pivot fields per day; box fields use the 14 days before each day only.

    Days must be consecutive for the rolling windows to mean calendar days; gaps break the box.
    """
    full = d.reindex(np.arange(d.index.min(), d.index.max() + DAY_MS, DAY_MS))
    atr = wilder_atr(full.high, full.low, full.close)
    top = full.high.shift().rolling(BOX_DAYS, min_periods=BOX_DAYS).max()
    bottom = full.low.shift().rolling(BOX_DAYS, min_periods=BOX_DAYS).min()
    vol_base = full.volume.shift().rolling(VOL_DAYS, min_periods=VOL_DAYS).mean()
    frozen = full.frozen.shift().rolling(BOX_DAYS, min_periods=BOX_DAYS).mean()
    centered = full.high.rolling(2 * PIVOT_SIDE + 1, center=True, min_periods=2 * PIVOT_SIDE + 1).max()
    f = pd.DataFrame({
        "open": full.open, "high": full.high, "low": full.low, "close": full.close, "volume": full.volume,
        "atr": atr, "top": top, "bottom": bottom, "height_atr": (top - bottom) / atr.shift(),
        "vol_ratio": full.volume / vol_base, "frozen_before": frozen,
        "is_pivot": (full.high == centered) & full.high.notna(),
    }, index=full.index)
    f["breakout"] = ((f.close > f.top) & (f.height_atr <= BOX_MAX_ATR) & (f.vol_ratio >= VOL_MULT)
                     & (f.frozen_before <= FROZEN_MAX)).fillna(False)
    return f


def resistance_levels(f: pd.DataFrame, day: int) -> np.ndarray:
    """Pivot highs confirmed by the end of ``day`` (pivot + 5 days <= day), within 365 days, ascending."""
    lo = day - PIVOT_DAYS * DAY_MS
    hi = day - PIVOT_SIDE * DAY_MS
    sel = f.loc[(f.index >= lo) & (f.index <= hi) & f["is_pivot"].to_numpy()]
    return np.sort(sel.high.to_numpy(float))


def round_levels(entry: float, above: float, k: int = 2) -> list[float]:
    """The next ``k`` integer multiples of 10^floor(log10(entry)) strictly above ``above``."""
    unit = 10 ** math.floor(math.log10(entry))
    first = math.floor(above / unit + 1e-9) + 1
    return [round((first + i) * unit, 12) for i in range(k)]


def targets(levels: np.ndarray, entry: float) -> tuple[float, float]:
    """TP1 = nearest level above entry x 1.005; TP2 = next level >= TP1 x 1.01; round numbers fill gaps."""
    above = [x for x in levels if x > entry * (1 + TP_MIN_GAP)]
    tp1 = above[0] if above else round_levels(entry, entry * (1 + TP_MIN_GAP), 1)[0]
    rest = [x for x in above if x >= tp1 * (1 + TP2_MIN_STEP)]
    tp2 = rest[0] if rest else round_levels(entry, tp1 * (1 + TP2_MIN_STEP) - 1e-12, 1)[0]
    return float(tp1), float(tp2)


# ---------------------------------------------------------------- 5m layer

def next_lower(low: np.ndarray) -> np.ndarray:
    """Index of the first later bar with a strictly lower low (len(low) when none)."""
    n = len(low)
    out = np.full(n, n, dtype=np.int64)
    stack: list[int] = []
    for i in range(n):
        x = low[i]
        while stack and low[stack[-1]] > x:
            out[stack.pop()] = i
        stack.append(i)
    return out


def turn_signals(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray) -> pd.DataFrame:
    """5m turn triggers: (trigger bar, low bar, low). Uses the trigger bar and earlier only."""
    n = len(l)
    if n < LOW_LOOKBACK + HOLD_BARS + 2:
        return pd.DataFrame(columns=["trig", "low_i", "low"])
    prior_min = pd.Series(l).shift().rolling(LOW_LOOKBACK, min_periods=LOW_LOOKBACK).min().to_numpy()
    with np.errstate(invalid="ignore"):
        cand = np.flatnonzero(l < prior_min)
    nl = next_lower(l)
    bull = np.zeros(n, bool)
    bull[1:] = (c[1:] > o[1:]) & (c[1:] > h[:-1])
    nxt = np.full(n + 1, n, dtype=np.int64)  # first bull index >= k
    for k in range(n - 1, -1, -1):
        nxt[k] = k if bull[k] else nxt[k + 1]
    start = cand + HOLD_BARS + 1
    ok = start < n
    cand, start = cand[ok], start[ok]
    trig = nxt[start]
    keep = (nl[cand] > cand + HOLD_BARS) & (trig < nl[cand]) & (trig <= cand + TRIGGER_EXPIRY) & (trig < n - 1)
    return pd.DataFrame({"trig": trig[keep], "low_i": cand[keep], "low": l[cand[keep]]})


def simulate(o, h, l, c, entry_i: int, stop: float, tp1: float, tp2: float, cap: int = MAX_HOLD) -> dict:
    """Half at TP1 then break-even stop for the rest; long only. Arrays are compact 5m bars."""
    entry = o[entry_i]
    end = min(entry_i + cap, len(o))
    O, H, L, C = o[entry_i:end], h[entry_i:end], l[entry_i:end], c[entry_i:end]
    hit_s = (L <= stop) | (O <= stop)
    hit_1 = (H >= tp1) | (O >= tp1)
    ks = int(np.argmax(hit_s)) if hit_s.any() else len(O)
    k1 = int(np.argmax(hit_1)) if hit_1.any() else len(O)
    out = {"entry": entry, "risk_frac": (entry - stop) / entry, "tp1_hit": False, "tp2_hit": False}
    if ks <= k1 and ks < len(O):  # stop first (also when both print in one bar)
        px = min(O[ks], stop)
        gross = px / entry - 1
        out.update(kind="stop", bars=ks + 1, gross=gross)
    elif k1 < len(O):
        px1 = max(O[k1], tp1)
        rest = slice(k1 + 1, len(O))
        hit_b = (L[rest] <= entry) | (O[rest] <= entry)
        hit_2 = (H[rest] >= tp2) | (O[rest] >= tp2)
        kb = int(np.argmax(hit_b)) if hit_b.any() else len(hit_b)
        k2 = int(np.argmax(hit_2)) if hit_2.any() else len(hit_2)
        if kb <= k2 and kb < len(hit_b):
            px2, kind, bars = min(O[k1 + 1 + kb], entry), "tp1_be", k1 + kb + 2
        elif k2 < len(hit_2):
            px2, kind, bars = max(O[k1 + 1 + k2], tp2), "tp2", k1 + k2 + 2
            out["tp2_hit"] = True
        else:
            px2, kind, bars = C[-1], "tp1_timeout", len(O)
        out["tp1_hit"] = True
        out.update(kind=kind, bars=bars, gross=0.5 * (px1 / entry - 1) + 0.5 * (px2 / entry - 1))
    else:
        out.update(kind="timeout", bars=len(O), gross=C[-1] / entry - 1)
    out["open_at_end"] = entry_i + cap > len(o) and out["kind"] in ("timeout", "tp1_timeout")
    out["net"] = out["gross"] - COST
    out["gross_r"] = out["gross"] / out["risk_frac"]
    out["net_r"] = out["net"] / out["risk_frac"]
    return out


# ---------------------------------------------------------------- per symbol

def scan_symbol(args: tuple) -> pd.DataFrame:
    """H1 trades of one symbol with both control sets; leader/season flags are added later."""
    symbol, months, cfg = args
    raw = v1.read_5m(symbol, pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"]))
    if len(raw) < 288 * 40:
        return pd.DataFrame()
    f = daily_features(daily_bars(raw))
    ts = raw.ts.to_numpy()
    o, h, l, c = (raw[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr5 = v2.atr14(h, l, c)
    atr_pct = atr5 / c
    month = pd.to_datetime(ts, unit="ms").to_period("M").astype(str).to_numpy()
    sig = turn_signals(o, h, l, c)
    sig_month = month[sig.trig.to_numpy()] if len(sig) else np.array([], dtype=object)
    start_ms = pd.Timestamp(cfg["start"]).value // 10**6
    months = set(months)
    rng = np.random.default_rng(cfg["controls"]["seed"] + zlib.crc32(symbol.encode()))
    rows, busy_until = [], -1
    for day in f.index[f.breakout.to_numpy()]:
        mo = pd.Timestamp(day, unit="ms").to_period("M").strftime("%Y-%m")
        if day < start_ms or mo not in months:
            continue
        top, bottom = float(f.at[day, "top"]), float(f.at[day, "bottom"])
        w0, w1 = np.searchsorted(ts, [day + DAY_MS, day + (1 + SETUP_DAYS) * DAY_MS])
        if w0 >= w1:
            continue
        below = np.flatnonzero(c[w0:w1] < bottom)
        w_end = w0 + int(below[0]) if len(below) else w1
        touch = np.flatnonzero(l[w0:w_end] <= top * (1 + PULLBACK))
        if not len(touch):
            continue
        t0 = w0 + int(touch[0])
        levels = resistance_levels(f, day)
        cand = sig.loc[(sig.low_i >= t0) & (sig.trig < w_end) & (sig.low <= top * (1 + PULLBACK))]
        attempts = 0
        for s in cand.itertuples(index=False):
            if attempts >= MAX_ATTEMPTS:
                break
            ei = int(s.trig) + 1
            if ei <= busy_until or ei >= len(o):
                continue
            stop = float(s.low) * (1 - STOP_PAD)
            entry = o[ei]
            if entry <= stop:
                continue
            tp1, tp2 = targets(levels, entry)
            risk = entry - stop
            plan_r = ((tp1 - entry) + (tp2 - entry)) / 2 / risk
            if plan_r < MIN_PLAN_R:
                rows.append({"symbol": symbol, "breakout_day": day, "trig": int(s.trig), "skipped": "plan_r",
                             "plan_r": plan_r})
                continue
            got = simulate(o, h, l, c, ei, stop, tp1, tp2)
            attempts += 1
            busy_until = ei + got["bars"] - 1
            r1, r2 = (tp1 - entry) / risk, (tp2 - entry) / risk
            ctrl_a, ctrl_b = matched_controls(o, h, l, c, atr5, atr_pct, month, sig, sig_month, int(s.trig),
                                              risk / atr5[int(s.trig)], r1, r2, rng)
            rows.append({
                "symbol": symbol, "breakout_day": day, "trig": int(s.trig), "skipped": "",
                "time": pd.Timestamp(ts[int(s.trig)], unit="ms", tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
                "month": month[int(s.trig)], "attempt": attempts, "box_top": top, "box_bottom": bottom,
                "height_atr": float(f.at[day, "height_atr"]), "vol_ratio": float(f.at[day, "vol_ratio"]),
                "low": float(s.low), "entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "plan_r": plan_r,
                "r1": r1, "r2": r2, "risk_pct": 100 * got["risk_frac"], "kind": got["kind"], "bars": got["bars"],
                "tp1_hit": got["tp1_hit"], "tp2_hit": got["tp2_hit"], "open_at_end": got["open_at_end"],
                "gross_r": got["gross_r"], "net_r": got["net_r"], "net_ret": got["net"],
                "ctrl_a_net_r": ctrl_a, "ctrl_b_net_r": ctrl_b})
    return pd.DataFrame(rows)


def matched_controls(o, h, l, c, atr5, atr_pct, month, sig, sig_month, e, risk_atr, r1, r2, rng):
    """Mean net R of 20 matched random entries and of 20 same-confirmation random entries."""
    band = (atr_pct >= VOL_BAND[0] * atr_pct[e]) & (atr_pct <= VOL_BAND[1] * atr_pct[e])
    pool = np.flatnonzero((month == month[e]) & band & np.isfinite(atr5))
    pool = pool[(pool != e) & (pool < len(o) - 1)]
    a = []
    if len(pool):
        for k in rng.choice(pool, size=N_CONTROLS):
            ei = k + 1
            risk = risk_atr * atr5[k]
            entry = o[ei]
            if not risk > 0 or entry - risk <= 0:
                continue
            a.append(simulate(o, h, l, c, ei, entry - risk, entry + r1 * risk, entry + r2 * risk)["net_r"])
    b = []
    if len(sig):
        trig = sig.trig.to_numpy()
        ok = (sig_month == month[e]) & (trig != e) & band[trig]
        pick = np.flatnonzero(ok)
        if len(pick):
            for j in rng.choice(pick, size=N_CONTROLS):
                ei = int(trig[j]) + 1
                entry = o[ei]
                stop = float(sig.low.iat[j]) * (1 - STOP_PAD)
                risk = entry - stop
                if risk <= 0:
                    continue
                b.append(simulate(o, h, l, c, ei, stop, entry + r1 * risk, entry + r2 * risk)["net_r"])
    return (float(np.mean(a)) if a else math.nan, float(np.mean(b)) if b else math.nan)


# ---------------------------------------------------------------- cross-section

def read_daily(symbol: str, cfg: dict) -> tuple[str, pd.DataFrame]:
    raw = v1.read_5m(symbol, pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"]))
    if raw.empty:
        return symbol, pd.DataFrame()
    d = daily_bars(raw)
    return symbol, d[["close", "qv"]]


def universe_from_daily(qv: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, size: int) -> dict[str, list[str]]:
    """Same rule as market_sync_shock.monthly_universe: top ``size`` alts by previous-month median daily quote volume."""
    alts = [s for s in qv.columns if s not in v1.LEADERS and s not in v1.STABLE]
    table = qv[alts].copy()
    table.index = pd.to_datetime(table.index, unit="ms")
    medians = table.groupby(table.index.to_period("M")).median()
    out = {}
    for month in pd.period_range(start.tz_convert(None).to_period("M"), end.tz_convert(None).to_period("M"), freq="M"):
        prior = month - 1
        if prior not in medians.index:
            continue
        ranked = medians.loc[prior].dropna().sort_values(ascending=False)
        out[str(month)] = list(ranked.index[:size])
    return out


def leader_flags(trades: pd.DataFrame, close: pd.DataFrame, qv: pd.DataFrame) -> pd.DataFrame:
    """H2 leader conditions and the H3 season flag at each trade's breakout day (data up to that day)."""
    alts = [s for s in qv.columns if s not in v1.LEADERS and s not in v1.STABLE]
    rank = qv[alts].rank(axis=1, ascending=False, method="first")
    top = rank <= TOP_N
    btc = close["BTCUSDT"]
    ret = close.pct_change(fill_method=None)
    sma = btc.rolling(SEASON_SMA, min_periods=SEASON_SMA).mean()
    out = []
    for r in trades[["symbol", "breakout_day"]].drop_duplicates().itertuples(index=False):
        d, s = int(r.breakout_day), r.symbol
        new_face = bool(top.at[d, s]) if d in top.index and s in top.columns else False
        if new_face:
            prev = top.loc[(top.index >= d - NEW_DAYS * DAY_MS) & (top.index < d), s]
            new_face = not bool(prev.any())
        win = btc.loc[(btc.index >= d - WAVE_DAYS * DAY_MS) & (btc.index < d)].dropna()
        wave = False
        if len(win) > 1:
            t0 = win.idxmin()
            t1 = win.loc[t0:].idxmax()
            leg = win[t1] / win[t0] - 1
            c0, c1 = close.at[t0, s], close.at[t1, s]
            wave = bool(leg >= WAVE_MIN and np.isfinite(c0) and np.isfinite(c1) and c1 / c0 - 1 >= leg)
        days = ret.loc[(ret.index >= d - DOWN_DAYS * DAY_MS) & (ret.index < d), ["BTCUSDT", s]].dropna()
        down = days.loc[days.BTCUSDT <= BTC_DOWN]
        resilient = bool(down.empty or (down[s] - down.BTCUSDT).mean() >= 0)
        season = bool(btc.get(d, np.nan) > sma.get(d, np.nan) and sma.get(d, np.nan) > sma.get(d - SEASON_SLOPE * DAY_MS, np.nan))
        out.append({"symbol": s, "breakout_day": d, "new_face": new_face, "wave": wave, "resilient": resilient,
                    "season": season})
    flags = pd.DataFrame(out)
    flags["leader"] = flags.new_face & flags.wave & flags.resilient
    return trades.merge(flags, on=["symbol", "breakout_day"], how="left")


def summarize(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    split = pd.Timestamp(cfg["select_before"])
    t = trades.loc[trades.skipped.eq("") & np.isfinite(trades.net_r)].copy()
    t["period"] = np.where(pd.to_datetime(t.time) < split, "before_2025", "from_2025")
    masks = {"H1": t.net_r.notna(), "H2": t.leader.astype(bool),
             "H3": t.leader.astype(bool) & t.season.astype(bool), "H1_season": t.season.astype(bool)}
    rows = []
    for name, m in masks.items():
        for period in ("before_2025", "from_2025", "all"):
            g = t.loc[m & (t.period.eq(period) if period != "all" else True)]
            if g.empty:
                rows.append({"hypothesis": name, "period": period, "trades": 0})
                continue
            setup = g.groupby(["symbol", "breakout_day"]).net_r.sum()
            ea, eb = g.net_r - g.ctrl_a_net_r, g.net_r - g.ctrl_b_net_r
            rows.append({
                "hypothesis": name, "period": period, "trades": len(g), "setups": len(setup),
                "symbols": g.symbol.nunique(), "win_rate": float((g.net_r > 0).mean()),
                "tp1_rate": float(g.tp1_hit.mean()), "tp2_rate": float(g.tp2_hit.mean()),
                "stop_rate": float(g.kind.eq("stop").mean()), "mean_gross_r": g.gross_r.mean(),
                "cost_r": float((COST / (g.risk_pct / 100)).mean()), "mean_net_r": g.net_r.mean(),
                "median_net_r": g.net_r.median(), "total_net_r": g.net_r.sum(), "median_risk_pct": g.risk_pct.median(),
                "median_plan_r": g.plan_r.median(), "setup_mean_net_r": setup.mean(),
                "setup_positive": float((setup > 0).mean()),
                "ctrl_a_net_r": g.ctrl_a_net_r.mean(), "excess_a": ea.mean(),
                "p_a": v1.month_block_sign_flip(ea.to_numpy(), g.month.to_numpy(), cfg["stat_seed"], cfg["flips"]),
                "ctrl_b_net_r": g.ctrl_b_net_r.mean(), "excess_b": eb.mean(),
                "p_b": v1.month_block_sign_flip(eb.to_numpy(), g.month.to_numpy(), cfg["stat_seed"], cfg["flips"])})
    return pd.DataFrame(rows)


def verdict(summary: pd.DataFrame) -> dict:
    """Pre-registered pass: both periods net > 0 and both excesses > 0; pooled p_a and p_b < 0.01."""
    out = {}
    for name in ("H1", "H2", "H3"):
        s = summary.loc[summary.hypothesis.eq(name)].set_index("period")
        try:
            per = [s.at[p, "mean_net_r"] > 0 and s.at[p, "excess_a"] > 0 and s.at[p, "excess_b"] > 0
                   for p in ("before_2025", "from_2025")]
            out[name] = bool(all(per) and s.at["all", "p_a"] < 0.01 and s.at["all", "p_b"] < 0.01)
        except KeyError:
            out[name] = False
    return out


def run(output: Path, *, limit: int | None = None, workers: int = 8) -> None:
    sources = (Path(__file__), CONFIG, Path("tests/evaluation/test_bitlanglang_mainwave_v1.py"),
               Path(v1.__file__), Path(v2.__file__))
    if limit is None and not v1._committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(CONFIG.read_text())
    began = time.perf_counter()
    symbols = sorted(p.name.removesuffix(".csv.gz") for p in v1.SERIES.glob("*.csv.gz"))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        daily = dict(pool.map(read_daily, symbols, [cfg] * len(symbols), chunksize=4))
    close = pd.DataFrame({s: d["close"] for s, d in daily.items() if len(d)}).sort_index()
    qv = pd.DataFrame({s: d["qv"] for s, d in daily.items() if len(d)}).sort_index()
    universe = universe_from_daily(qv, pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"]), cfg["universe_size"])
    by_symbol: dict[str, list[str]] = {}
    for month, names in universe.items():
        for s in names:
            by_symbol.setdefault(s, []).append(month)
    jobs = sorted(by_symbol.items())[:limit] if limit else sorted(by_symbol.items())
    print(json.dumps({"symbols": len(jobs), "daily_s": round(time.perf_counter() - began, 1)}), flush=True)
    parts = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for k, part in enumerate(pool.map(scan_symbol, [(s, m, cfg) for s, m in jobs], chunksize=1)):
            parts.append(part)
            if (k + 1) % 25 == 0:
                print(json.dumps({"done": k + 1, "elapsed_s": round(time.perf_counter() - began, 1)}), flush=True)
    trades = pd.concat([p for p in parts if len(p)], ignore_index=True)
    trades = leader_flags(trades, close, qv)
    summary = summarize(trades, cfg)
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    summary.to_csv(output / "summary.csv", index=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    taken = trades.loc[trades.skipped.eq("")]
    (output / "receipt.json").write_text(json.dumps({
        "experiment_id": cfg["experiment_id"], "source_commit": head, "symbols": len(jobs), "limit": limit,
        "universe_months": len(universe), "trades": int(len(taken)),
        "skipped_plan_r": int(trades.skipped.eq("plan_r").sum()), "verdict": verdict(summary),
        "elapsed_s": round(time.perf_counter() - began, 1),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1))
    cols = ["hypothesis", "period", "trades", "setups", "win_rate", "tp1_rate", "tp2_rate", "mean_gross_r", "cost_r",
            "mean_net_r", "ctrl_a_net_r", "excess_a", "p_a", "ctrl_b_net_r", "excess_b", "p_b", "median_risk_pct"]
    print(summary.reindex(columns=cols).round(3).to_string(index=False))
    print(json.dumps(verdict(summary)))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=EXP / "results_v1")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    run(args.output, limit=args.limit, workers=args.workers)


if __name__ == "__main__":
    main()
