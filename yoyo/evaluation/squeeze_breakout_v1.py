"""Owner squeeze breakout v1: the five-condition system of Notion task #40, scanned and backtested.

Owner 2026-10-08 (task tracker #40 交易系统生成): "1 形成bb密集 2 有跟大阳线或者大阴线 看涨看跌
吞没突破密集区 3 同时这块区域形成我们想要的6条均线的密集 4 伴随成交量 5 止损就在均线下面
盈亏比1:5 或者1:3", then "扫一下吧 几个周期都试试". The six MAs are SMA/EMA 20/60/120 and the
example chart is SPIKE V13.1.

SPIKE V9 already combines a dense six-MA rope, V7 BB compression, a volume ratio and a solid
breakout candle, and its replay lost after cost in the later year with matched excess p 0.19.
This study therefore reuses SPIKE's frozen definitions wherever the owner's words match one
and changes what the owner specified differently: the decision is the close of an engulfing
candle that breaks the dense zone, the stop sits below all six MAs, and the target is a fixed
3R or 5R (SPIKE trails after 2R). Every feature uses the signal bar and earlier only:

  bb        V7/V9 compression: BB SMA200 +/- 2 population stdev, width / basis; a bar is
            compressed when width <= linear P10 of the previous 500 widths; the signal needs
            3 consecutive compressed bars inside the 12 bars before it
  dense     SPIKE rope: mean (max MA - min MA) / ATR over the 12 bars before <= 3.0 and >= 2
            pairwise MA order flips over those bars
  big_body  body >= 1.0 ATR of the previous bar
  engulf    the body covers the previous body
  volume    volume / median of the 20 bars before >= 1.5
  always    candle in the trade direction, body / range >= 0.55, close beyond all six MAs and
            beyond the 12-bar extreme before it; 12-bar cooldown; 700 contiguous ready bars;
            at most 10% zero-range or zero-volume bars among the 50 before (frozen
            pre-listing / suspension prices pass every squeeze test trivially)

Entry is the next open; the stop is min(MAs) - 0.2 ATR for longs (mirrored for shorts); exits
read bar highs and lows, stop first when both levels print in one bar, gaps fill at the open,
and a trade still open after 192 bars closes at that close. Each event gets 20 random entries
of the same symbol, timeframe and month, with ATR / price within 0.5-2x the event's, the same
direction, the same stop distance in ATR units and the same targets and cost. Drop-one variants of the five owner conditions are read
with 5 controls each at 3R.
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
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as v1
from yoyo.evaluation import market_sync_shock_v2 as v2

EXP = Path("experiments/active/exp-owner-squeeze-breakout-20261008-v1")
CONFIG = EXP / "config.json"
MA_PERIODS = (20, 60, 120)
BB_LEN, BB_MULT, BB_HISTORY, BB_PCT, BB_RUN, BB_WINDOW = 200, 2.0, 500, 0.10, 3, 12
DENSE_LEN, DENSE_WIDTH, DENSE_FLIPS = 12, 3.0, 2
MIN_BODY_ATR, MIN_BODY_FRAC, BREAK_LOOKBACK = 1.0, 0.55, 12
RV_LEN, MIN_RV = 20, 1.5
STOP_BUFFER, READY, COOLDOWN, MAX_HOLD = 0.2, 700, 12, 192
VOL_BAND = (0.5, 2.0)  # control ATR/price within this multiple of the event's
FROZEN_LEN, MAX_FROZEN = 50, 0.10  # data quality: zero-range or zero-volume bars before a bar
CONDITIONS = ("bb", "dense", "big_body", "engulf", "volume")
LEADERS = ("BTCUSDT", "ETHUSDT")


def full_bars(raw5: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Complete-bucket OHLCV on a gap-free grid of bar opens (missing buckets are NaN rows)."""
    bars = v2.resample_hl(raw5, minutes)
    step = minutes * 60_000
    if bars.empty:
        return bars
    return bars.reindex(np.arange(bars.index.min(), bars.index.max() + step, step))


def recent_run(compressed: pd.Series, run: int = BB_RUN, window: int = BB_WINDOW) -> pd.Series:
    """True when ``run`` consecutive compressed bars lie inside the ``window`` bars before each bar."""
    c = compressed.fillna(False).astype(bool)
    ends = c.copy()
    for k in range(1, run):
        ends &= c.shift(k, fill_value=False)
    span = window - run + 1  # a run ending at t-1 .. t-(window-run+1) stays inside the window
    return ends.shift(1, fill_value=False).astype(int).rolling(span, min_periods=1).max().astype(bool)


def features(bars: pd.DataFrame) -> pd.DataFrame:
    """Causal fields at each bar; ``before`` fields use only earlier bars."""
    o, h, l, c, v = (bars[k] for k in ("open", "high", "low", "close", "volume"))
    valid = np.isfinite(bars[["open", "high", "low", "close"]].to_numpy()).all(axis=1)
    seg = pd.Series(valid.astype(int), index=bars.index)
    seg = seg.groupby((~valid).cumsum()).cumsum()  # contiguous valid bars so far
    prev_c = c.shift()
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1).where(valid)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().where(valid)
    mas = {}
    for n in MA_PERIODS:
        mas[f"s{n}"] = c.rolling(n, min_periods=n).mean()
        mas[f"e{n}"] = c.ewm(span=n, adjust=False, min_periods=n).mean().where(valid)
    ma = pd.DataFrame(mas)
    rope_hi, rope_lo = ma.max(axis=1, skipna=False), ma.min(axis=1, skipna=False)
    width = (rope_hi - rope_lo) / atr
    flips = pd.Series(0.0, index=bars.index)
    for a, b in combinations(ma.columns, 2):
        d = ma[a] - ma[b]
        flips += (((d > 0) & (d.shift() <= 0)) | ((d < 0) & (d.shift() >= 0))).astype(float)
    basis = c.rolling(BB_LEN, min_periods=BB_LEN).mean()
    sd = c.rolling(BB_LEN, min_periods=BB_LEN).std(ddof=0)
    bw = (2 * BB_MULT * sd) / basis.abs()
    p10 = bw.shift().rolling(BB_HISTORY, min_periods=BB_HISTORY).quantile(BB_PCT, interpolation="linear")
    return pd.DataFrame({
        "open": o, "high": h, "low": l, "close": c, "atr": atr, "prev_atr": atr.shift(),
        "rope_hi": rope_hi, "rope_lo": rope_lo,
        "past_width": width.shift().rolling(DENSE_LEN, min_periods=DENSE_LEN).mean(),
        "past_flips": flips.shift().rolling(DENSE_LEN, min_periods=DENSE_LEN).sum(),
        "bb_recent": recent_run(bw <= p10),
        "rv": v / v.shift().rolling(RV_LEN, min_periods=RV_LEN).median(),
        "prior_high": h.shift().rolling(BREAK_LOOKBACK, min_periods=BREAK_LOOKBACK).max(),
        "prior_low": l.shift().rolling(BREAK_LOOKBACK, min_periods=BREAK_LOOKBACK).min(),
        "prev_open": o.shift(), "prev_close": prev_c, "seg": seg,
        "frozen_share": ((h == l) | (v <= 0)).astype(float).where(valid).shift()
                        .rolling(FROZEN_LEN, min_periods=FROZEN_LEN).mean(),
    }, index=bars.index)


def conditions(f: pd.DataFrame, side: int) -> tuple[pd.Series, dict[str, pd.Series]]:
    """Base mask (always required) and the five owner conditions for one direction."""
    o, c, h, l = f.open, f.close, f.high, f.low
    body = (c - o).abs()
    top, bottom = np.maximum(f.prev_open, f.prev_close), np.minimum(f.prev_open, f.prev_close)
    if side == 1:
        direction, engulf = c > o, (c >= top) & (o <= bottom)
        breakout = (c > f.rope_hi) & (c > f.prior_high)
    else:
        direction, engulf = c < o, (c <= bottom) & (o >= top)
        breakout = (c < f.rope_lo) & (c < f.prior_low)
    ready = (f.seg >= READY) & (f.atr > 0) & (f.prev_atr > 0) & (f.frozen_share <= MAX_FROZEN)
    base = ready & direction & (body >= MIN_BODY_FRAC * (h - l)) & breakout
    conds = {"bb": f.bb_recent.astype(bool),
             "dense": (f.past_width <= DENSE_WIDTH) & (f.past_flips >= DENSE_FLIPS),
             "big_body": body >= MIN_BODY_ATR * f.prev_atr,
             "engulf": engulf,
             "volume": f.rv >= MIN_RV}
    return base.fillna(False), {k: v.fillna(False) for k, v in conds.items()}


def with_cooldown(hits: list[tuple[int, int]], cooldown: int = COOLDOWN) -> list[tuple[int, int]]:
    """Keep (bar, side) hits in time order with nothing new for ``cooldown`` bars after one."""
    out, block = [], -1
    for i, s in sorted(hits):
        if i <= block:
            continue
        out.append((i, s))
        block = i + cooldown
    return out


def control_candidates(pool: np.ndarray, atr_pct: np.ndarray, e: int, band: tuple[float, float] = VOL_BAND) -> np.ndarray:
    """Pool bars other than ``e`` whose ATR / price lies within ``band`` times the event's.

    Matching the stop in ATR units alone breaks on frozen-price bars (delisting months), where
    ATR is near zero and cost / risk explodes; the band keeps controls in the event's regime.
    """
    ref = atr_pct[e]
    keep = (pool != e) & (atr_pct[pool] >= band[0] * ref) & (atr_pct[pool] <= band[1] * ref)
    return pool[keep]


def simulate_many(o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, entry_i: np.ndarray,
                  side: np.ndarray, stop: np.ndarray, target_r: float, cap: int, cost: float) -> dict:
    """Fixed stop / R-target exits from the open of ``entry_i``; arrays are bar series."""
    m = len(entry_i)
    pad = np.full(cap, np.nan)
    o, h, l, c = (np.concatenate([np.asarray(x, float), pad]) for x in (o, h, l, c))
    k = entry_i[:, None] + np.arange(cap)[None, :]
    O, H, L, C = o[k], h[k], l[k], c[k]
    s = side.astype(float)[:, None]
    entry = o[entry_i]
    risk = side * (entry - stop)
    target = entry + side * target_r * risk
    so, sc = s * O, s * C
    slo, shi = np.where(s > 0, L, -H), np.where(s > 0, H, -L)
    sstop, starget = (side * stop)[:, None], (side * target)[:, None]
    with np.errstate(invalid="ignore"):
        gap_stop, gap_target = so <= sstop, so >= starget
        hit_stop, hit_target = slo <= sstop, shi >= starget
    hit = hit_stop | hit_target | gap_stop | gap_target
    has = hit.any(axis=1)
    first = np.where(has, hit.argmax(axis=1), cap - 1)
    rows = np.arange(m)
    gs, gt, hs = gap_stop[rows, first], gap_target[rows, first], hit_stop[rows, first]
    sexit = np.where(~has, sc[rows, cap - 1],
                     np.where(gs | gt, so[rows, first], np.where(hs, sstop[:, 0], starget[:, 0])))
    stopped = has & (gs | (~gt & hs))
    upto = np.arange(cap)[None, :] <= first[:, None]
    finite = np.isfinite(O) & np.isfinite(H) & np.isfinite(L) & np.isfinite(C)
    with np.errstate(invalid="ignore"):
        valid = (finite | ~upto).all(axis=1) & np.isfinite(entry) & (risk > 0)
        exit_price = side * sexit
        gross = side * (exit_price / entry - 1.0)
        risk_frac = risk / entry
        net = gross - cost
        lowest_close = np.where(upto, sc, np.inf).min(axis=1)
        worst = np.where(upto, slo, np.inf).min(axis=1)
    nan = np.where(valid, 1.0, np.nan)
    return {"valid": valid, "entry": entry * nan, "risk_frac": risk_frac * nan, "gross_ret": gross * nan,
            "net_ret": net * nan, "net_r": net / risk_frac * nan, "gross_r": gross / risk_frac * nan,
            "kind": np.where(~valid, "", np.where(~has, "timeout", np.where(stopped, "stop", "target"))),
            "bars": np.where(valid, first + 1, -1),
            "never_closed_below": np.where(valid, lowest_close >= side * entry, False),
            "mae_r": (worst - side * entry) / risk * nan}


def scan_symbol(args: tuple) -> pd.DataFrame:
    """Events and trades (with matched controls) for one symbol on every timeframe."""
    symbol, months, cfg = args
    raw = v1.read_5m(symbol, pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"]))
    if raw.empty:
        return pd.DataFrame()
    start_ms = pd.Timestamp(cfg["start"]).value // 10**6
    months = set(months)
    variants = {"primary": CONDITIONS, **{f"no_{c}": tuple(x for x in CONDITIONS if x != c) for c in CONDITIONS}}
    frames = []
    for minutes in cfg["timeframes"]:
        bars = full_bars(raw, minutes)
        if len(bars) < READY + 50:
            continue
        f = features(bars)
        index = bars.index.to_numpy()
        month = pd.to_datetime(index, unit="ms").to_period("M").astype(str).to_numpy()
        allowed = np.isin(month, list(months)) & (index >= start_ms)
        n = len(f)
        allowed[n - 1:] = False  # entry needs a next bar
        o, h, l, c = (f[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        atr = f.atr.to_numpy(float)
        atr_pct = atr / c
        rope = {1: f.rope_lo.to_numpy(float), -1: f.rope_hi.to_numpy(float)}
        masks = {s: conditions(f, s) for s in (1, -1)}
        ready = ((f.seg >= READY) & (f.atr > 0) & (f.frozen_share <= MAX_FROZEN)).to_numpy() & (index >= start_ms)
        ready[n - 1:] = False  # a control also needs a next-bar entry
        pool_rows = {mo: np.flatnonzero(ready & (month == mo)) for mo in months}
        rng = np.random.default_rng(cfg["controls"]["seed"] + zlib.crc32(f"{symbol}|{minutes}".encode()))
        for variant, keep in variants.items():
            hits = []
            for s in (1, -1):
                base, conds = masks[s]
                m = base.to_numpy() & allowed
                for name in keep:
                    m &= conds[name].to_numpy()
                hits += [(int(i), s) for i in np.flatnonzero(m)]
            events = with_cooldown(hits)
            if not events:
                continue
            ei = np.array([i for i, _ in events])
            sd = np.array([s for _, s in events])
            stop = np.where(sd > 0, rope[1][ei] - STOP_BUFFER * atr[ei], rope[-1][ei] + STOP_BUFFER * atr[ei])
            risk_atr = sd * (o[ei + 1] - stop) / atr[ei]
            n_ctrl = cfg["controls"]["n"] if variant == "primary" else cfg["ablation"]["controls"]
            ctrl = np.full((len(ei), n_ctrl), -1)
            for k, e in enumerate(ei):
                cand = control_candidates(pool_rows.get(month[e], np.empty(0, int)), atr_pct, e)
                if len(cand):
                    ctrl[k] = rng.choice(cand, size=n_ctrl)
            flat, ok = ctrl.ravel(), ctrl.ravel() >= 0
            cside = np.repeat(sd, n_ctrl)[ok]
            cu = flat[ok]
            cstop = o[cu + 1] - cside * np.repeat(risk_atr, n_ctrl)[ok] * atr[cu]
            targets = cfg["targets_r"] if variant == "primary" else [cfg["ablation"]["target_r"]]
            for target_r in targets:
                got = simulate_many(o, h, l, c, ei + 1, sd, stop, target_r, cfg["max_hold_bars"], cfg["round_trip_cost"])
                cg = simulate_many(o, h, l, c, cu + 1, cside, cstop, target_r, cfg["max_hold_bars"], cfg["round_trip_cost"])
                cr, cb = np.full(len(flat), np.nan), np.full(len(flat), np.nan)
                cr[ok], cb[ok] = cg["net_r"], cg["net_ret"]
                cr, cb = cr.reshape(len(ei), n_ctrl), cb.reshape(len(ei), n_ctrl)
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)  # an event whose controls all failed is NaN
                    c_r, c_b = np.nanmean(cr, axis=1), np.nanmean(cb, axis=1)
                frames.append(pd.DataFrame({
                    "variant": variant, "symbol": symbol, "minutes": minutes, "target_r": target_r,
                    "time": pd.to_datetime(index[ei], unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "month": month[ei], "side": sd, "signal_close": c[ei], "entry": got["entry"],
                    "stop": stop, "risk_atr": risk_atr, "risk_frac": got["risk_frac"],
                    "past_width": f.past_width.to_numpy()[ei], "rv": f.rv.to_numpy()[ei],
                    "body_atr": np.abs(c[ei] - o[ei]) / f.prev_atr.to_numpy()[ei],
                    "exit_kind": got["kind"], "bars": got["bars"], "gross_r": got["gross_r"], "net_r": got["net_r"],
                    "net_ret": got["net_ret"], "never_closed_below": got["never_closed_below"], "mae_r": got["mae_r"],
                    "control_net_r": c_r, "control_net_ret": c_b, "controls_valid": np.isfinite(cr).sum(axis=1)}))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def summarize(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    split = pd.Timestamp(cfg["select_before"])
    t = trades.loc[np.isfinite(trades.net_r) & np.isfinite(trades.control_net_r)].copy()
    t["period"] = np.where(pd.to_datetime(t.time) < split, "select", "check")
    t["excess_r"] = t.net_r - t.control_net_r
    rows = []
    keys = ["variant", "minutes", "side", "target_r", "period"]
    for key, g in t.groupby(keys, sort=True):
        x = g.excess_r.to_numpy()
        sd = x.std(ddof=1) if len(x) > 2 else math.nan
        rows.append({**dict(zip(keys, key)), "events": len(g), "symbols": g.symbol.nunique(), "months": g.month.nunique(),
                     "win_rate": float((g.net_r > 0).mean()), "target_rate": float((g.exit_kind == "target").mean()),
                     "mean_net_r": g.net_r.mean(), "median_net_r": g.net_r.median(), "mean_gross_r": g.gross_r.mean(),
                     "mean_net_bp": 1e4 * g.net_ret.mean(), "median_risk_pct": 100 * g.risk_frac.median(),
                     "cost_r": float((cfg["round_trip_cost"] / g.risk_frac).mean()),
                     "control_net_r": g.control_net_r.mean(), "excess_r": x.mean(),
                     "t_excess": float(x.mean() / (sd / math.sqrt(len(x)))) if sd and sd > 0 else math.nan,
                     "p_excess": v1.month_block_sign_flip(x, g.month.to_numpy(), cfg["stat_seed"], cfg["flips"]),
                     "never_closed_below": float(g.never_closed_below.mean())})
    return pd.DataFrame(rows)


def risk_buckets(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    t = trades.loc[trades.variant.eq("primary") & np.isfinite(trades.net_r) & np.isfinite(trades.control_net_r)].copy()
    t["period"] = np.where(pd.to_datetime(t.time) < pd.Timestamp(cfg["select_before"]), "select", "check")
    t["risk_bucket"] = pd.cut(100 * t.risk_frac, [0, 0.5, 1, 2, 4, np.inf], right=False).astype(str)
    g = t.groupby(["minutes", "target_r", "period", "risk_bucket"])
    return g.agg(events=("net_r", "size"), mean_net_r=("net_r", "mean"), mean_gross_r=("gross_r", "mean"),
                 control_net_r=("control_net_r", "mean"), win_rate=("net_r", lambda s: float((s > 0).mean()))).reset_index()


def run(output: Path, *, limit: int | None = None, workers: int = 8) -> None:
    sources = (Path(__file__), CONFIG, Path("tests/evaluation/test_squeeze_breakout_v1.py"), Path(v1.__file__), Path(v2.__file__))
    if limit is None and not v1._committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(CONFIG.read_text())
    began = time.perf_counter()
    symbols = sorted(p.name.removesuffix(".csv.gz") for p in v1.SERIES.glob("*.csv.gz"))
    universe = v1.monthly_universe(symbols, pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"]), 100)
    by_symbol: dict[str, list[str]] = {}
    for month, names in universe.items():
        for s in list(names) + list(LEADERS):
            by_symbol.setdefault(s, []).append(month)
    jobs = sorted(by_symbol.items())
    if limit:
        jobs = [j for j in jobs if j[0] in LEADERS] + [j for j in jobs if j[0] not in LEADERS][:limit]
    print(json.dumps({"symbols": len(jobs), "universe_s": round(time.perf_counter() - began, 1)}), flush=True)
    parts = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for k, part in enumerate(pool.map(scan_symbol, [(s, m, cfg) for s, m in jobs], chunksize=1)):
            parts.append(part)
            if (k + 1) % 25 == 0:
                print(json.dumps({"done": k + 1, "elapsed_s": round(time.perf_counter() - began, 1)}), flush=True)
    trades = pd.concat([p for p in parts if len(p)], ignore_index=True)
    summary = summarize(trades, cfg)
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    summary.to_csv(output / "summary.csv", index=False)
    risk_buckets(trades, cfg).to_csv(output / "risk_buckets.csv", index=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (output / "receipt.json").write_text(json.dumps({
        "experiment_id": cfg["experiment_id"], "source_commit": head, "symbols": len(jobs), "limit": limit,
        "universe_months": len(universe), "trade_rows": len(trades),
        "primary_events": int(trades.loc[trades.variant.eq("primary") & trades.target_r.eq(cfg["targets_r"][0])].shape[0]),
        "elapsed_s": round(time.perf_counter() - began, 1),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1))
    view = summary.loc[summary.variant.eq("primary")]
    cols = ["minutes", "side", "target_r", "period", "events", "win_rate", "mean_net_r", "mean_gross_r", "cost_r",
            "control_net_r", "excess_r", "p_excess", "median_risk_pct", "never_closed_below"]
    print(view[cols].round(3).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=EXP / "results_v1")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    run(args.output, limit=args.limit, workers=args.workers)


if __name__ == "__main__":
    main()
