"""HTF MA reversion v1: fade far excursions from SPIKE V13.1's higher-timeframe line (Notion task #44).

Owner 2026-10-09 (task tracker #44 高周期均线回归策略): on the 5m chart, when price runs far from
SPIKE V13.1's higher-timeframe line, enter toward the line as soon as the distance is reached (no
bar close), targets 1:2 / 1:3 / 1:5; then 15m against the 1h line; also try CM_Williams_Vix_Fix.
Follow-up the same day: no 1h chart; stops 1.5% / 3% "or your own"; both sides; the example was
ETH 2026-10-09 01:20 UTC+8, line 2556, low 2405.

The line is V13.1's own direction line (f_m15Ema120 on 5m, f_h1Sma60 on 15m): a chart bar inside
HTF bucket B reads the value of completed HTF bar B-1, so the trigger is known at the bar open and
constant inside it. Three distance measures set a trigger price:

  pct   line * (1 -/+ d)
  atr   line -/+ k * Wilder ATR14 of the same completed HTF bar
  pctl  line * (1 + q-quantile of low/line - 1) for longs (1-q of high/line - 1 for shorts) over
        the completed chart bars of the previous 30 days

A long fills at min(open, trigger) on the first 5m bar whose low reaches the trigger (a resting
limit); shorts are mirrored. One entry per excursion: an excursion starts at the first touch and
ends at the first chart close back within half the trigger distance of the line; long and short
are separate books and an excursion that starts while that book holds a trade is skipped. The
Vix Fix variant needs the indicator on at the fill price (Pine's realtime highest(close, 22)
includes the live price); shorts use an unconfirmed mirror. Stops: 1.5%, 3%, or one third of the
fill's distance to the line (the 3R target is then the line). Exits read 5m highs/lows: on the
entry bar the stop counts if its low reaches it and the target only if the bar closes beyond it;
afterwards gaps fill at the open, stop first on ties, 48h cap, 0.2% round trip. Each trade gets
20 random open entries of the same symbol, chart timeframe and month with HTF ATR / price within
0.5-2x, the same side, stop fraction, target and cost.

v2 (owner sample 2026-10-09: ETH long box entry 2411, stop 2396, target 2491 - a 0.6% stop and
about 5R) reuses this builder with its own config: stops 'pctX' are X% of the fill, and a
config 'reentry' list adds arms that, after a stop, re-enter at the next touch inside the same
excursion (at most N times; the excursion is still defined by price alone). Arm 0 is the v1
path unchanged. v3 (owner: ETHUSDT.P, last two years, find the best parameters) adds a config
'series_dir' so the run reads OKX ETH bars built by its own experiment.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
import warnings
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as v1
from yoyo.evaluation import market_sync_shock_v2 as v2

EXP = Path("experiments/active/exp-htf-ma-reversion-20261009-v1")
CONFIG = EXP / "config.json"
PATH_MIN = 5
EMA_LEN, EMA_READY, SMA_LEN = 120, 1200, 60
VIX_PD, VIX_BBL, VIX_MULT, VIX_LB, VIX_PH = 22, 20, 2.0, 50, 0.85
VOL_BAND = (0.5, 2.0)
STOPS = ("pct1.5", "pct3", "line3")
CHUNK = 2000


def gap_free(bars: pd.DataFrame, minutes: int, first: int | None = None, last: int | None = None) -> pd.DataFrame:
    """Bars on a gap-free grid of opens (ms); missing buckets are NaN rows."""
    step = minutes * 60_000
    lo = bars.index.min() if first is None else first
    hi = bars.index.max() if last is None else last
    return bars.reindex(np.arange(lo, hi + step, step))


def run_position(valid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Run id and 1-based position inside each contiguous run of valid bars (0 when invalid)."""
    run_id = np.cumsum(~valid)
    pos = pd.Series(valid.astype(int)).groupby(run_id).cumsum().to_numpy()
    return run_id, pos


def htf_line(htf: pd.DataFrame, kind: str) -> tuple[np.ndarray, np.ndarray]:
    """V13.1 line and Wilder ATR14 at each completed HTF bar of a gap-free frame (NaN until ready)."""
    c, h, l = (htf[k].to_numpy(float) for k in ("close", "high", "low"))
    valid = np.isfinite(htf[["open", "high", "low", "close"]].to_numpy(float)).all(axis=1)
    run_id, pos = run_position(valid)
    close = pd.Series(np.where(valid, c, np.nan))
    if kind == "ema120":  # alpha 2/121, first-close seed per run, 1200 contiguous bars
        line = close.groupby(run_id).transform(lambda x: x.ewm(span=EMA_LEN, adjust=False).mean()).to_numpy()
        line[pos < EMA_READY] = np.nan
    elif kind == "sma60":
        line = close.rolling(SMA_LEN, min_periods=SMA_LEN).mean().to_numpy()
        line[pos < SMA_LEN] = np.nan
    else:
        raise ValueError(kind)
    prev = np.r_[np.nan, c[:-1]]
    with np.errstate(invalid="ignore"):
        tr = np.fmax(h - l, np.fmax(np.abs(h - prev), np.abs(l - prev)))
    tr[~valid] = np.nan
    atr = pd.Series(tr).groupby(run_id).transform(
        lambda x: x.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()).to_numpy()
    atr[~valid] = np.nan
    return line, atr


def on_chart(chart_index: np.ndarray, htf_index: np.ndarray, values: np.ndarray, htf_ms: int) -> np.ndarray:
    """Value of the completed HTF bar before each chart bar's HTF bucket (NaN when missing)."""
    k = (chart_index // htf_ms * htf_ms - htf_ms - htf_index[0]) // htf_ms
    ok = (k >= 0) & (k < len(htf_index))
    out = np.full(len(chart_index), np.nan)
    out[ok] = values[k[ok]]
    return out


def triggers(measure: str, level: float, line: np.ndarray, atr: np.ndarray, low: np.ndarray,
             high: np.ndarray, window: int) -> dict[int, np.ndarray]:
    """Trigger price per chart bar and side from completed data (NaN where undefined)."""
    if measure == "pct":
        return {1: line * (1 - level), -1: line * (1 + level)}
    if measure == "atr":
        return {1: line - level * atr, -1: line + level * atr}
    if measure != "pctl":
        raise ValueError(measure)
    with np.errstate(invalid="ignore", divide="ignore"):
        dl = pd.Series(low / line - 1).shift().rolling(window, min_periods=window // 2).quantile(level).to_numpy()
        dh = pd.Series(high / line - 1).shift().rolling(window, min_periods=window // 2).quantile(1 - level).to_numpy()
        return {1: np.where(dl < 0, line * (1 + dl), np.nan), -1: np.where(dh > 0, line * (1 + dh), np.nan)}


def excursions(touch: np.ndarray, back: np.ndarray, chart_of: np.ndarray, per: int) -> list[tuple[int, int]]:
    """(first touching path bar, last chart bar) of each excursion; ``back`` marks chart bars that end one."""
    t_idx, b_idx = np.flatnonzero(touch), np.flatnonzero(back)
    n_chart = len(back)
    out, cursor = [], 0
    while True:
        k = np.searchsorted(t_idx, cursor)
        if k >= len(t_idx):
            return out
        p = int(t_idx[k])
        m = np.searchsorted(b_idx, chart_of[p])
        j_end = int(b_idx[m]) if m < len(b_idx) else n_chart - 1
        out.append((p, j_end))
        cursor = (j_end + 1) * per


def vix_on(price, side: int, ext_prev, s_sum, s_sq, s_max):
    """CM_Williams_Vix_Fix signal with the live bar at ``price`` (shorts: unconfirmed mirror)."""
    price = np.asarray(price, float)
    with np.errstate(invalid="ignore", divide="ignore"):
        if side > 0:
            ref = np.fmax(ext_prev, price)
            w = (ref - price) / ref * 100
        else:
            ref = np.fmin(ext_prev, price)
            w = (price - ref) / ref * 100
        mean = (s_sum + w) / VIX_BBL
        sd = np.sqrt(np.maximum((s_sq + w * w) / VIX_BBL - mean * mean, 0.0))
        return (w >= mean + VIX_MULT * sd) | (w >= VIX_PH * np.fmax(s_max, w))


def vix_inputs(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> dict[int, tuple[np.ndarray, ...]]:
    """Per chart bar and side: prior 21-close extreme and prior wvf sums / squares (19) and max (49)."""
    c = pd.Series(close)
    out = {}
    for side in (1, -1):
        ext = c.rolling(VIX_PD, min_periods=VIX_PD).max() if side > 0 else c.rolling(VIX_PD, min_periods=VIX_PD).min()
        wvf = (ext - low) / ext * 100 if side > 0 else (high - ext) / ext * 100
        prev = c.shift().rolling(VIX_PD - 1, min_periods=VIX_PD - 1)
        hist = wvf.shift().rolling(VIX_BBL - 1, min_periods=VIX_BBL - 1)
        out[side] = ((prev.max() if side > 0 else prev.min()).to_numpy(), hist.sum().to_numpy(),
                     (wvf ** 2).shift().rolling(VIX_BBL - 1, min_periods=VIX_BBL - 1).sum().to_numpy(),
                     wvf.shift().rolling(VIX_LB - 1, min_periods=VIX_LB - 1).max().to_numpy())
    return out


def bisect_on(side: int, near: float, far: float, args: tuple, steps: int = 40) -> float:
    """Price closest to ``near`` (towards ``far``) where the Vix Fix turns on; on(far) must hold."""
    good, bad = far, near
    for _ in range(steps):
        mid = 0.5 * (good + bad)
        if bool(vix_on(mid, side, *args)):
            good = mid
        else:
            bad = mid
    return good


def find_entry(p0: int, j_end: int, side: int, trig: np.ndarray, o: np.ndarray, h: np.ndarray, l: np.ndarray,
               chart_of: np.ndarray, per: int, vix: dict | None) -> tuple[int, float] | None:
    """First fill inside one excursion: the touch itself, or the first point where the Vix Fix is on."""
    def first_price(p):
        return float(np.fmin(o[p], trig[p]) if side > 0 else np.fmax(o[p], trig[p]))
    if vix is None:
        return p0, first_price(p0)
    last = min((j_end + 1) * per, len(o)) - 1
    ps = np.arange(p0, last + 1)
    with np.errstate(invalid="ignore"):
        ps = ps[(l[ps] <= trig[ps]) if side > 0 else (h[ps] >= trig[ps])]
    if not len(ps):
        return None
    js = chart_of[ps]
    args = tuple(a[js] for a in vix[side])
    near = np.fmin(o[ps], trig[ps]) if side > 0 else np.fmax(o[ps], trig[ps])
    far = l[ps] if side > 0 else h[ps]
    on_near, on_far = vix_on(near, side, *args), vix_on(far, side, *args)
    hit = np.flatnonzero(on_near | on_far)
    if not len(hit):
        return None
    i = hit[0]
    if on_near[i]:
        return int(ps[i]), float(near[i])
    return int(ps[i]), bisect_on(side, float(near[i]), float(far[i]), tuple(a[i] for a in args))


def simulate(o, h, l, c, start, entry, side, stop, target, intrabar, cap, cost) -> dict:
    """Stop/target exits on path bars from ``start`` (the entry bar), chunked; see module docstring."""
    parts = [_simulate(o, h, l, c, *(x[i:i + CHUNK] for x in (start, entry, side, stop, target, intrabar)), cap, cost)
             for i in range(0, len(start), CHUNK)]
    if not parts:
        return {k: np.empty(0) for k in ("valid", "gross", "net", "risk_frac", "kind", "bars", "exit_i")}
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def _simulate(o, h, l, c, start, entry, side, stop, target, intrabar, cap, cost) -> dict:
    m = len(start)
    k = start[:, None] + np.arange(cap)[None, :]
    inside = k < len(o)
    k = np.minimum(k, len(o) - 1)
    O, H, L, C = (np.where(inside, x[k], np.nan) for x in (o, h, l, c))
    s = side.astype(float)[:, None]
    so, sc = s * O, s * C
    slo, shi = np.where(s > 0, L, -H), np.where(s > 0, H, -L)
    sstop, starget = (side * stop)[:, None], (side * target)[:, None]
    with np.errstate(invalid="ignore"):
        gap_stop, gap_target = so <= sstop, so >= starget
        hit_stop, hit_target = slo <= sstop, shi >= starget
        gap_stop[:, 0] = gap_target[:, 0] = False  # the fill is at or after the entry bar's open
        hit_target[intrabar, 0] = sc[intrabar, 0] >= starget[intrabar, 0]
    hit = gap_stop | gap_target | hit_stop | hit_target
    has = hit.any(axis=1)
    first = np.where(has, hit.argmax(axis=1), cap - 1)
    rows = np.arange(m)
    gs, gt, hs = gap_stop[rows, first], gap_target[rows, first], hit_stop[rows, first]
    sexit = np.where(~has, sc[rows, cap - 1],
                     np.where(gs | gt, so[rows, first], np.where(hs, sstop[:, 0], starget[:, 0])))
    stopped = has & (gs | (~gt & hs))
    upto = np.arange(cap)[None, :] <= first[:, None]
    finite = np.isfinite(O) & np.isfinite(H) & np.isfinite(L) & np.isfinite(C)
    risk_frac = side * (entry - stop) / entry
    with np.errstate(invalid="ignore", divide="ignore"):
        valid = (finite | ~upto).all(axis=1) & np.isfinite(entry) & (risk_frac > 0)
        gross = side * (side * sexit / entry - 1.0)
    nan = np.where(valid, 1.0, np.nan)
    return {"valid": valid, "gross": gross * nan, "net": (gross - cost) * nan, "risk_frac": risk_frac * nan,
            "kind": np.where(~valid, "", np.where(~has, "timeout", np.where(stopped, "stop", "target"))),
            "bars": np.where(valid, first + 1, -1), "exit_i": start + first}


def take_sequential(start_p: np.ndarray, exit_i: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """One open trade per book: an excursion starting at or before the last exit is skipped."""
    keep = np.zeros(len(start_p), bool)
    last = -1
    for i in np.argsort(start_p, kind="stable"):
        if valid[i] and start_p[i] > last:
            keep[i] = True
            last = exit_i[i]
    return keep


def stop_risk(rule: str, side: int, fill: np.ndarray, line_at_fill: np.ndarray) -> np.ndarray:
    """Risk per unit of the fill: 'line3' = a third of the distance to the line, 'pctX' = X% of the fill."""
    if rule == "line3":
        return side * (line_at_fill - fill) / 3
    if rule.startswith("pct"):
        return fill * float(rule[3:]) / 100
    raise ValueError(rule)


def chain_with_reentry(exc: list[tuple[int, int]], first: list, touch_idx: np.ndarray, side: int, tp: np.ndarray,
                       bars: tuple, chart_of: np.ndarray, per: int, vix: dict | None, rule: str, line_p: np.ndarray,
                       target_r: float, cap: int, cost: float, max_re: int) -> list[tuple]:
    """Trades in time order, one open per book; after a stop, re-enter at the next touch of the same excursion.

    Returns (excursion start, entry bar, fill, attempt, outcome dict) per trade; with max_re = 0 this is
    the v1 rule (first entry per excursion, excursions starting at or before the last exit skipped).
    """
    o, h, l, c = bars
    out, last_exit = [], -1
    for (p0, j_end), e in zip(exc, first):
        if e is None or p0 <= last_exit:
            continue
        p, fill = e
        end = min((j_end + 1) * per, len(o)) - 1
        attempt = 0
        while True:
            fa = np.array([fill])
            risk = stop_risk(rule, side, fa, line_p[[p]])
            stop, target = fa - side * risk, fa + side * target_r * risk
            sim = simulate(o, h, l, c, np.array([p]), fa, np.array([side]), stop, target, np.array([True]), cap, cost)
            if not sim["valid"][0]:
                break
            out.append((p0, p, fill, attempt, {k: v[0] for k, v in sim.items()}))
            last_exit = int(sim["exit_i"][0])
            if sim["kind"][0] != "stop" or attempt >= max_re:
                break
            k = np.searchsorted(touch_idx, last_exit, side="right")
            if k >= len(touch_idx) or touch_idx[k] > end:
                break
            nxt = find_entry(int(touch_idx[k]), j_end, side, tp, o, h, l, chart_of, per, vix)
            if nxt is None:
                break
            p, fill = nxt
            attempt += 1
    return out


def draw_controls(ps: np.ndarray, pools: dict, month: np.ndarray, atr_pct: np.ndarray, rng, n_ctrl: int) -> np.ndarray:
    """Random pool bars per trade: same month, HTF ATR / price within VOL_BAND of the trade's (-1 = none)."""
    ctrl = np.full((len(ps), n_ctrl), -1)
    for i, p in enumerate(ps):
        pool = pools.get(month[p], np.empty(0, int))
        ref = atr_pct[p]
        cand = pool[(pool != p) & (atr_pct[pool] >= VOL_BAND[0] * ref) & (atr_pct[pool] <= VOL_BAND[1] * ref)]
        if len(cand):
            ctrl[i] = rng.choice(cand, size=n_ctrl)
    return ctrl


def control_outcome(bars: tuple, ctrl: np.ndarray, risk_frac: np.ndarray, side: int, target_r: float, cap: int,
                    cost: float) -> tuple[np.ndarray, np.ndarray]:
    """Mean net of each trade's random open entries with its stop fraction, target, cap and cost."""
    o, h, l, c = bars
    n_ctrl = ctrl.shape[1]
    flat, ok = ctrl.ravel(), ctrl.ravel() >= 0
    cu = flat[ok]
    cfrac = np.repeat(risk_frac, n_ctrl)[ok]
    ce = o[cu]
    cs = simulate(o, h, l, c, cu, ce, np.full(len(cu), side), ce * (1 - side * cfrac),
                  ce * (1 + side * target_r * cfrac), np.zeros(len(cu), bool), cap, cost)
    cn = np.full(len(flat), np.nan)
    cn[ok] = cs["net"]
    cn = cn.reshape(-1, n_ctrl)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a trade whose controls all failed is NaN
        return np.nanmean(cn, axis=1), np.isfinite(cn).sum(axis=1)


def read_5m(symbol: str, start: pd.Timestamp, end: pd.Timestamp, series_dir: str | None = None) -> pd.DataFrame:
    """v1's frozen Binance reader, or the same row validation on a config's own series directory (v3: OKX ETH)."""
    if series_dir is None:
        return v1.read_5m(symbol, start, end)
    raw = pd.read_csv(Path(series_dir) / f"{symbol}.csv.gz", usecols=["ts", "open", "high", "low", "close", "volume"])
    raw = raw.loc[(raw.ts >= start.value // 10**6) & (raw.ts + 300_000 <= end.value // 10**6)]
    ok = ((raw[["open", "high", "low", "close"]] > 0).all(axis=1) & (raw.volume >= 0)
          & (raw.high >= raw[["open", "close", "low"]].max(axis=1)) & (raw.low <= raw[["open", "close", "high"]].min(axis=1)))
    return raw.loc[ok].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def chart_frames(raw: pd.DataFrame, minutes: int, htf_minutes: int) -> dict | None:
    """Gap-free chart, HTF and 5m path bars over the chart range, plus the HTF line on chart bars."""
    chart = v2.resample_hl(raw, minutes)
    htf = v2.resample_hl(raw, htf_minutes)
    if len(chart) < 2000 or len(htf) < 200:
        return None
    chart = gap_free(chart, minutes)
    htf = gap_free(htf, htf_minutes)
    step = minutes * 60_000
    path = gap_free(raw.set_index("ts")[["open", "high", "low", "close", "volume"]], PATH_MIN,
                    int(chart.index[0]), int(chart.index[-1]) + step - PATH_MIN * 60_000)
    line, atr = htf_line(htf, "ema120" if htf_minutes == 15 else "sma60")
    ci = chart.index.to_numpy()
    hi = htf.index.to_numpy()
    return {"chart": chart, "path": path, "per": minutes // PATH_MIN,
            "line": on_chart(ci, hi, line, htf_minutes * 60_000), "atr": on_chart(ci, hi, atr, htf_minutes * 60_000)}


def scan_symbol(args: tuple) -> pd.DataFrame:
    """Trades with matched controls for one symbol on both charts and the whole grid."""
    symbol, cfg = args
    raw = read_5m(symbol, pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"]), cfg.get("series_dir"))
    if raw.empty:
        return pd.DataFrame()
    start_ms = pd.Timestamp(cfg["start"]).value // 10**6
    cap, cost, n_ctrl = cfg["max_hold_bars"], cfg["round_trip_cost"], cfg["controls"]["n"]
    arms = cfg.get("reentry", [0])
    frames = []
    for chart_key, spec in cfg["charts"].items():
        minutes = int(chart_key)
        fr = chart_frames(raw, minutes, spec["htf_minutes"])
        if fr is None:
            continue
        chart, path, per, line, atr = fr["chart"], fr["path"], fr["per"], fr["line"], fr["atr"]
        cl, ch, cc = (chart[k].to_numpy(float) for k in ("low", "high", "close"))
        o, h, l, c = (path[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        bars = (o, h, l, c)
        pidx = path.index.to_numpy()
        chart_of = np.arange(len(path)) // per
        line_p, atr_pct = line[chart_of], atr[chart_of] / o
        month = pd.to_datetime(pidx, unit="ms").to_period("M").astype(str).to_numpy()
        tradable = pidx >= start_ms
        pool_ok = tradable & np.isfinite(line_p) & np.isfinite(atr_pct) & np.isfinite(o)
        pools = {mo: np.flatnonzero(pool_ok & (month == mo)) for mo in np.unique(month[pool_ok])}
        vix = vix_inputs(ch, cl, cc)
        window = int(cfg["distance"]["pctl"]["window_days"] * 1440 // minutes)
        levels = {"pct": cfg["distance"]["pct"]["levels"], "atr": cfg["distance"]["atr"]["levels"][chart_key],
                  "pctl": cfg["distance"]["pctl"]["levels"]}
        for measure, lv in levels.items():
            for level in lv:
                trig = triggers(measure, level, line, atr, cl, ch, window)
                for side in (1, -1):
                    tg = trig[side]
                    dist = side * (line - tg)
                    with np.errstate(invalid="ignore"):
                        back = ~(np.isfinite(tg) & np.isfinite(cc)) | (side * (cc - (line - side * dist / 2)) >= 0)
                        tp = tg[chart_of]
                        touch = tradable & ((l <= tp) if side > 0 else (h >= tp))
                    exc = excursions(touch, back, chart_of, per)
                    touch_idx = np.flatnonzero(touch)
                    for use_vix in (False, True):
                        firsts = [find_entry(p0, j_end, side, tp, o, h, l, chart_of, per, vix if use_vix else None)
                                  for p0, j_end in exc]
                        got = [(p0,) + e for (p0, _), e in zip(exc, firsts) if e]
                        if not got:
                            continue
                        p0s = np.array([g[0] for g in got])
                        ps = np.array([g[1] for g in got])
                        fill = np.array([g[2] for g in got])
                        n = len(ps)
                        sd = np.full(n, side)
                        lp = line_p[ps]
                        key = f"{symbol}|{minutes}|{measure}|{level}|{side}|{use_vix}"
                        base = {"symbol": symbol, "minutes": minutes, "side": side, "vix": use_vix,
                                "measure": measure, "level": level}
                        if 0 in arms:
                            rng = np.random.default_rng(cfg["controls"]["seed"] + zlib.crc32(key.encode()))
                            ctrl = draw_controls(ps, pools, month, atr_pct, rng, n_ctrl)
                        for stop_rule in cfg["stops"]:
                            risk = stop_risk(stop_rule, side, fill, lp)
                            stop = fill - side * risk
                            for target_r in cfg["targets_r"]:
                                cell = {**base, "stop": stop_rule, "target_r": target_r}
                                if 0 in arms:
                                    target = fill + side * target_r * risk
                                    sim = simulate(o, h, l, c, ps, fill, sd, stop, target, np.ones(n, bool), cap, cost)
                                    keep = take_sequential(p0s, sim["exit_i"], sim["valid"])
                                    if keep.any():
                                        c_net, c_ok = control_outcome(bars, ctrl[keep], sim["risk_frac"][keep], side,
                                                                      target_r, cap, cost)
                                        frames.append(trade_frame(
                                            cell, pidx, month, line_p, atr_pct, p0s[keep], ps[keep], fill[keep],
                                            {k: v[keep] for k, v in sim.items()}, c_net, c_ok,
                                            {"reentry": 0, "attempt": 0} if "reentry" in cfg else {}))
                                for max_re in (a for a in arms if a > 0):
                                    chain = chain_with_reentry(exc, firsts, touch_idx, side, tp, bars, chart_of, per,
                                                               vix if use_vix else None, stop_rule, line_p, target_r,
                                                               cap, cost, max_re)
                                    if not chain:
                                        continue
                                    cp0, cp, cfill, catt = (np.array([r[i] for r in chain]) for i in range(4))
                                    csim = {k: np.array([r[4][k] for r in chain]) for k in chain[0][4]}
                                    ck = f"{key}|{stop_rule}|{target_r}|re{max_re}"
                                    rng = np.random.default_rng(cfg["controls"]["seed"] + zlib.crc32(ck.encode()))
                                    cctrl = draw_controls(cp, pools, month, atr_pct, rng, n_ctrl)
                                    c_net, c_ok = control_outcome(bars, cctrl, csim["risk_frac"], side, target_r, cap, cost)
                                    frames.append(trade_frame(cell, pidx, month, line_p, atr_pct, cp0, cp, cfill, csim,
                                                              c_net, c_ok, {"reentry": max_re, "attempt": catt}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def trade_frame(cell: dict, pidx, month, line_p, atr_pct, p0s, ps, fill, sim: dict, c_net, c_ok, extra: dict) -> pd.DataFrame:
    """One row per kept trade with its outcome and matched-control mean."""
    lp = line_p[ps]
    return pd.DataFrame({
        **cell, **extra,
        "start_time": pd.to_datetime(pidx[p0s], unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time": pd.to_datetime(pidx[ps], unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "month": month[ps], "line": lp, "fill": fill,
        "dev_pct": 100 * (fill / lp - 1), "atr_pct": 100 * atr_pct[ps],
        "risk_frac": sim["risk_frac"], "exit_kind": sim["kind"],
        "bars": sim["bars"], "gross_ret": sim["gross"], "net_ret": sim["net"],
        "net_r": sim["net"] / sim["risk_frac"],
        "control_net_ret": c_net, "control_net_r": c_net / sim["risk_frac"],
        "controls_valid": c_ok})


CELL = ["minutes", "side", "vix", "measure", "level", "stop", "target_r"]


def cell_keys(frame: pd.DataFrame) -> list[str]:
    """v1 cells, plus the re-entry arm (v2+) and the confirmation kind (v4) when the run has them."""
    return CELL + [k for k in ("reentry", "conf") if k in frame.columns]


def summarize(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    t = trades.loc[np.isfinite(trades.net_r) & np.isfinite(trades.control_net_r)].copy()
    t["period"] = np.where(pd.to_datetime(t.time) < pd.Timestamp(cfg["select_before"]), "select", "check")
    t["excess_r"] = t.net_r - t.control_net_r
    t["day"] = t.time.str[:10]
    rows = []
    cell = cell_keys(t)
    for key, g in t.groupby(cell + ["period"], sort=True):
        x = g.excess_r.to_numpy()
        sd = x.std(ddof=1) if len(x) > 2 else math.nan
        best_days = g.groupby("day").net_ret.sum().nlargest(5).index
        rest = g.loc[~g.day.isin(best_days)]
        rows.append({**dict(zip(cell + ["period"], key)), "trades": len(g), "symbols": g.symbol.nunique(),
                     "days": g.day.nunique(), "win_rate": float((g.net_ret > 0).mean()),
                     "target_rate": float((g.exit_kind == "target").mean()),
                     "timeout_rate": float((g.exit_kind == "timeout").mean()),
                     "mean_net_pct": 100 * g.net_ret.mean(), "mean_net_r": g.net_r.mean(),
                     "control_net_r": g.control_net_r.mean(), "excess_r": x.mean(),
                     "t_excess": float(x.mean() / (sd / math.sqrt(len(x)))) if sd and sd > 0 else math.nan,
                     "p_excess": v1.month_block_sign_flip(x, g.month.to_numpy(), cfg["stat_seed"], cfg["flips"]),
                     "net_r_ex_best5_days": rest.net_r.mean() if len(rest) else math.nan,
                     "median_risk_pct": 100 * g.risk_frac.median(), "median_hold_h": g.bars.median() * PATH_MIN / 60,
                     "median_dev_pct": g.dev_pct.median()})
    return pd.DataFrame(rows)


def select_cells(summary: pd.DataFrame, min_trades: int = 30) -> pd.DataFrame:
    """Per chart x side x vix: the select-period cell with the highest excess t, and its check row."""
    sel = summary.loc[summary.period.eq("select") & (summary.trades >= min_trades)]
    cell = cell_keys(summary)
    out = []
    for key, g in sel.groupby(["minutes", "side", "vix"] + cell[len(CELL):]):
        best = g.loc[g.t_excess.idxmax()]
        chk = summary.loc[(summary[cell] == best[cell]).all(axis=1) & summary.period.eq("check")]
        row = {**{k: best[k] for k in cell}, "cells_tried": len(g)}
        for tag, r in (("select", best), ("check", chk.iloc[0] if len(chk) else None)):
            for f in ("trades", "win_rate", "mean_net_pct", "mean_net_r", "control_net_r", "excess_r", "p_excess",
                      "net_r_ex_best5_days"):
                row[f"{tag}_{f}"] = math.nan if r is None else r[f]
        out.append(row)
    return pd.DataFrame(out)


def run(output: Path | None, *, symbols: list[str] | None = None, workers: int = 8, config: Path = CONFIG) -> None:
    sources = (Path(__file__), config, Path("tests/evaluation/test_htf_ma_reversion_v1.py"), Path(v1.__file__),
               Path(v2.__file__))
    if symbols is None and not v1._committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(config.read_text())
    output = output or config.parent / cfg.get("output_dir", "results_v1")
    began = time.perf_counter()
    jobs = symbols or cfg["symbols"]
    parts = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for k, part in enumerate(pool.map(scan_symbol, [(s, cfg) for s in jobs], chunksize=1)):
            parts.append(part)
            print(json.dumps({"done": k + 1, "symbol": jobs[k], "rows": len(part),
                              "elapsed_s": round(time.perf_counter() - began, 1)}), flush=True)
    trades = pd.concat([p for p in parts if len(p)], ignore_index=True)
    summary = summarize(trades, cfg)
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    summary.to_csv(output / "summary.csv", index=False)
    chosen = select_cells(summary)
    chosen.to_csv(output / "selection.csv", index=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (output / "receipt.json").write_text(json.dumps({
        "experiment_id": cfg["experiment_id"], "source_commit": head, "symbols": jobs, "trade_rows": len(trades),
        "elapsed_s": round(time.perf_counter() - began, 1), "generated_at": pd.Timestamp.now(tz="UTC").isoformat()},
        indent=1))
    pd.set_option("display.width", 250)
    print(chosen.round(3).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--config", type=Path, default=CONFIG)
    parser.add_argument("--output", type=Path, help="default: the config's output_dir (v1: results_v1)")
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    run(args.output, symbols=args.symbols, workers=args.workers, config=args.config)


if __name__ == "__main__":
    main()
