"""Six-MA knot breakout v1: the 2026-10-08 ETH 1m example written as rules and scanned.

Owner 2026-10-11: after a bar-by-bar read of ETHUSDT.P 1m on 2026-10-08 23:01-23:09 Beijing
(the six SPIKE V13.1 MAs SMA/EMA 20/60/120 knotted, a high-volume upside fake-out, then a short
break that fanned out), "把上面几条特征写成规则去扫历史数据". Each feature of that read becomes
one causal condition, evaluated at the decision bar's close:

  knot         mean (max MA - min MA) / ATR14 over the 12 bars before <= 0.7 and >= 10 pairwise MA
               order flips over those bars (example at the 23:02 break: 0.67 ATR, 10 flips)
  break        the bar closes beyond all six MAs in the trade direction, the bar before did not
  close        the close lies in the outer 20% of the bar's range (example 0.00)
  volume       volume / median of the 20 bars before >= 1.5 (example 1.57)
  fakeout      one of the 12 bars before closed beyond all six MAs on the other side with a volume
               ratio >= 3 (example 22:57 at 5.0x and 22:59 at 12.3x)
  htf          the last completed higher-timeframe bar has its six MAs fully stacked in the trade
               direction, both 20s beyond both 60s beyond both 120s (example: the 5m 22:55 bar)
  lower_highs  this bar and the two before each made a lower high (higher low for longs)
  confirm      the decision waits for the first bar, from the break up to 4 bars after it, where
               EMA20 is the outermost of the six and every close since the break stayed beyond all
               six (example 23:05)

The thresholds come from a single owner example and are round numbers at its values; nothing
was tuned on the scanned data, which ends before the example. Variants change one thing from
primary = knot + break: the knot thresholds (SPIKE V13.1's 3.0 ATR / 2 flips, or 1.0 / 6), one
added condition each, or the confirm timing. ``replica`` stacks every condition and is read
descriptively only.

Exits follow exp-owner-squeeze-breakout-20261008-v1, the owner's own system on the same six MAs
("止损在均线外, 盈亏比 1:3 或 1:5"): entry at the next open, stop beyond all six MAs at the
decision bar plus 0.2 ATR, fixed 3R or 5R target, 192-bar cap, 0.2% round-trip cost, 20 matched
random entries per event (same symbol, timeframe and month, ATR / price within 0.5-2x, same stop
distance in ATR units, targets and cost), 5 per event for the variants.

Data: 1m is Binance USD-M monthly archives for six majors (the owner's example is 1m and no
multi-year 1m universe exists locally); 5m/15m/30m/1H reuse the frozen 5m series and the monthly
top-100 universe plus BTC/ETH of the squeeze study. Higher timeframe: 1m->5m, 5m->15m, 15m->1H,
30m->2H, 1H->4H, read only once that bar has closed.
"""
from __future__ import annotations

import argparse
import json
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
from yoyo.evaluation import squeeze_breakout_v1 as sb

EXP = Path("experiments/active/exp-ma6-knot-breakout-20261011-v1")
CONFIG = EXP / "config.json"
SERIES_1M = Path("data/research/ma6_knot_1m_20261011/series")
EXAMPLE_OKX = Path("data/research/spike_v129_eth5m_20261010/okx_api_ETH_USDT_SWAP_1m_20260922T1400_20261010T1000.csv")
EXAMPLE_BREAK = pd.Timestamp("2026-10-08T15:02:00Z")  # 23:02 Beijing
MAJORS_1M = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT")
HTF = {1: 5, 5: 15, 15: 60, 30: 120, 60: 240}
MA_PERIODS = (20, 60, 120)
DENSE_LEN = 12
KNOTS = {"tight": (0.7, 10), "v131": (3.0, 2), "loose": (1.0, 6)}
CLOSE_FRAC, MIN_RV, FAKE_RV, FAKE_LEN, LH_RUN, CONFIRM_LEN = 0.2, 1.5, 3.0, 12, 3, 4
ADDED = ("close", "volume", "fakeout", "htf", "lower_highs")
VARIANTS = ("primary", "knot_v131", "knot_loose", *(f"plus_{a}" for a in ADDED), "confirm", "replica")
CHUNK = 8000  # simulated rows per batch, bounds memory on 1m


def read_1m(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Archive 1m bars in [start, end) with the same validity screen as the 5m reader."""
    path = next(SERIES_1M.glob(f"binance_um_{symbol}_1m_*.csv"))
    raw = pd.read_csv(path, usecols=["ts", "open", "high", "low", "close", "volume"])
    raw = raw.loc[(raw.ts >= start.value // 10**6) & (raw.ts + 60_000 <= end.value // 10**6)]
    ok = ((raw[["open", "high", "low", "close"]] > 0).all(axis=1) & (raw.volume >= 0)
          & (raw.high >= raw[["open", "close", "low"]].max(axis=1)) & (raw.low <= raw[["open", "close", "high"]].min(axis=1)))
    return raw.loc[ok].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def bars_from(raw: pd.DataFrame, base: int, minutes: int) -> pd.DataFrame:
    """Complete-bucket OHLCV on a gap-free grid of bar opens; incomplete buckets are NaN rows."""
    step = minutes * 60_000
    bucket = raw.ts.to_numpy() // step * step
    g = raw.assign(bucket=bucket).groupby("bucket", sort=True)
    bars = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                 volume=("volume", "sum"), n=("ts", "size"))
    bars = bars.loc[bars.n == minutes // base].drop(columns="n")
    if bars.empty:
        return bars
    return bars.reindex(np.arange(bars.index.min(), bars.index.max() + step, step))


def six_mas(c: pd.Series, valid: np.ndarray) -> pd.DataFrame:
    out = {}
    for n in MA_PERIODS:
        out[f"s{n}"] = c.rolling(n, min_periods=n).mean()
        out[f"e{n}"] = c.ewm(span=n, adjust=False, min_periods=n).mean().where(valid)
    return pd.DataFrame(out)


def stacks(ma: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Full long / short stack: both 20s beyond both 60s beyond both 120s."""
    lo20, hi20 = ma[["s20", "e20"]].min(axis=1, skipna=False), ma[["s20", "e20"]].max(axis=1, skipna=False)
    lo60, hi60 = ma[["s60", "e60"]].min(axis=1, skipna=False), ma[["s60", "e60"]].max(axis=1, skipna=False)
    lo120, hi120 = ma[["s120", "e120"]].min(axis=1, skipna=False), ma[["s120", "e120"]].max(axis=1, skipna=False)
    return (lo20 > hi60) & (lo60 > hi120), (hi20 < lo60) & (hi60 < lo120)


def htf_stack(index: np.ndarray, base: int, htf: pd.DataFrame, htf_minutes: int) -> tuple[np.ndarray, np.ndarray]:
    """Per base bar, the stack of the last higher-timeframe bar that closed by the base bar's close."""
    n = len(index)
    if htf.empty:
        return np.zeros(n, bool), np.zeros(n, bool)
    valid = np.isfinite(htf[["open", "high", "low", "close"]].to_numpy()).all(axis=1)
    up, down = stacks(six_mas(htf.close, valid))
    closes = htf.index.to_numpy() + htf_minutes * 60_000
    k = np.searchsorted(closes, index + base * 60_000, side="right") - 1
    ok = k >= 0
    kk = np.where(ok, k, 0)
    return ok & up.to_numpy()[kk], ok & down.to_numpy()[kk]


def features(bars: pd.DataFrame, htf_up: np.ndarray, htf_down: np.ndarray) -> pd.DataFrame:
    """Causal fields at each bar; ``past_*`` and the fake-out use only bars before."""
    o, h, l, c, v = (bars[k] for k in ("open", "high", "low", "close", "volume"))
    valid = np.isfinite(bars[["open", "high", "low", "close"]].to_numpy()).all(axis=1)
    seg = pd.Series(valid.astype(int), index=bars.index)
    seg = seg.groupby((~valid).cumsum()).cumsum()
    prev_c = c.shift()
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1).where(valid)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().where(valid)
    ma = six_mas(c, valid)
    rope_hi, rope_lo = ma.max(axis=1, skipna=False), ma.min(axis=1, skipna=False)
    flips = pd.Series(0.0, index=bars.index)
    for a, b in combinations(ma.columns, 2):
        d = ma[a] - ma[b]
        flips += (((d > 0) & (d.shift() <= 0)) | ((d < 0) & (d.shift() >= 0))).astype(float)
    rv = v / v.shift().rolling(20, min_periods=20).median()
    above, below = (c > rope_hi), (c < rope_lo)
    span = h - l
    pos = ((c - l) / span).where(span > 0)
    lower_high, higher_low = (h < h.shift()).astype(float), (l > l.shift()).astype(float)
    return pd.DataFrame({
        "open": o, "high": h, "low": l, "close": c, "atr": atr, "prev_atr": atr.shift(),
        "rope_hi": rope_hi, "rope_lo": rope_lo, "e20": ma.e20,
        "past_width": ((rope_hi - rope_lo) / atr).shift().rolling(DENSE_LEN, min_periods=DENSE_LEN).mean(),
        "past_flips": flips.shift().rolling(DENSE_LEN, min_periods=DENSE_LEN).sum(),
        "rv": rv, "close_pos": pos, "above": above, "below": below,
        "break_long": above & ~above.shift(fill_value=False) & rope_lo.shift().notna(),
        "break_short": below & ~below.shift(fill_value=False) & rope_hi.shift().notna(),
        "fake_for_short": (above & (rv >= FAKE_RV)).astype(float).shift().rolling(FAKE_LEN, min_periods=1).max() > 0,
        "fake_for_long": (below & (rv >= FAKE_RV)).astype(float).shift().rolling(FAKE_LEN, min_periods=1).max() > 0,
        "lh_run": lower_high.rolling(LH_RUN, min_periods=LH_RUN).sum() >= LH_RUN,
        "hl_run": higher_low.rolling(LH_RUN, min_periods=LH_RUN).sum() >= LH_RUN,
        "htf_up": htf_up, "htf_down": htf_down, "seg": seg,
        "frozen_share": ((h == l) | (v <= 0)).astype(float).where(valid).shift()
                        .rolling(sb.FROZEN_LEN, min_periods=sb.FROZEN_LEN).mean(),
    }, index=bars.index)


def ready_mask(f: pd.DataFrame) -> pd.Series:
    return (f.seg >= sb.READY) & (f.atr > 0) & (f.prev_atr > 0) & (f.frozen_share <= sb.MAX_FROZEN)


def conditions(f: pd.DataFrame, side: int) -> dict[str, np.ndarray]:
    """Boolean arrays for one direction: the break, each knot setting and each added condition."""
    long = side == 1
    out = {"break": (ready_mask(f) & (f.break_long if long else f.break_short)).to_numpy()}
    for name, (width, nflips) in KNOTS.items():
        out[f"knot_{name}"] = ((f.past_width <= width) & (f.past_flips >= nflips)).to_numpy()
    out["close"] = ((f.close_pos >= 1 - CLOSE_FRAC) if long else (f.close_pos <= CLOSE_FRAC)).to_numpy()
    out["volume"] = (f.rv >= MIN_RV).to_numpy()
    out["fakeout"] = (f.fake_for_long if long else f.fake_for_short).to_numpy()
    out["htf"] = (f.htf_up if long else f.htf_down).to_numpy()
    out["lower_highs"] = (f.hl_run if long else f.lh_run).to_numpy()
    return {k: np.nan_to_num(np.asarray(v, float)).astype(bool) for k, v in out.items()}


def confirm_bars(f: pd.DataFrame, hits: np.ndarray, side: int) -> np.ndarray:
    """For each break bar, the first bar from it to CONFIRM_LEN after where EMA20 is outermost and
    every close since the break stayed beyond all six MAs; -1 when that never happens."""
    beyond = (f.above if side == 1 else f.below).to_numpy()
    outer = (f.e20 >= f.rope_hi) if side == 1 else (f.e20 <= f.rope_lo)
    outer = outer.to_numpy() & ready_mask(f).to_numpy()
    n, out = len(f), np.full(len(hits), -1)
    for k, i in enumerate(hits):
        for j in range(i, min(i + CONFIRM_LEN + 1, n)):
            if not beyond[j]:
                break
            if outer[j]:
                out[k] = j
                break
    return out


def variant_hits(f: pd.DataFrame, allowed: np.ndarray) -> dict[str, list[tuple[int, int]]]:
    """(decision bar, side) hits per variant, before the cooldown."""
    out: dict[str, list[tuple[int, int]]] = {v: [] for v in VARIANTS}
    for side in (1, -1):
        c = conditions(f, side)
        core = c["break"] & allowed
        primary = core & c["knot_tight"]
        masks = {"primary": primary, "knot_v131": core & c["knot_v131"], "knot_loose": core & c["knot_loose"]}
        for a in ADDED:
            masks[f"plus_{a}"] = primary & c[a]
        replica = primary.copy()
        for a in ADDED:
            replica &= c[a]
        masks["replica"] = replica
        for name, m in masks.items():
            out[name] += [(int(i), side) for i in np.flatnonzero(m)]
        js = confirm_bars(f, np.flatnonzero(primary), side)
        out["confirm"] += [(int(j), side) for j in js if j >= 0 and allowed[j]]
    return out


def simulate_chunked(o, h, l, c, entry_i, side, stop, target_r, cap, cost) -> dict:
    parts = [sb.simulate_many(o, h, l, c, entry_i[a:a + CHUNK], side[a:a + CHUNK], stop[a:a + CHUNK], target_r, cap, cost)
             for a in range(0, len(entry_i), CHUNK)]
    return {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}


def scan_frame(symbol: str, minutes: int, bars: pd.DataFrame, htf: pd.DataFrame, months: set[str], cfg: dict) -> list[pd.DataFrame]:
    if len(bars) < sb.READY + 50:
        return []
    index = bars.index.to_numpy()
    up, down = htf_stack(index, minutes, htf, HTF[minutes])
    f = features(bars, up, down)
    start_ms = pd.Timestamp(cfg["start"]).value // 10**6
    month = pd.to_datetime(index, unit="ms").to_period("M").astype(str).to_numpy()
    n = len(f)
    allowed = np.isin(month, list(months)) & (index >= start_ms)
    allowed[n - 1:] = False  # entry needs a next bar
    o, h, l, c = (f[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = f.atr.to_numpy(float)
    atr_pct = atr / c
    rope_hi, rope_lo = f.rope_hi.to_numpy(float), f.rope_lo.to_numpy(float)
    ready = ready_mask(f).to_numpy() & (index >= start_ms)
    ready[n - 1:] = False
    pool_rows = {mo: np.flatnonzero(ready & (month == mo)) for mo in months}
    rng = np.random.default_rng(cfg["controls"]["seed"] + zlib.crc32(f"{symbol}|{minutes}".encode()))
    flags = {s: conditions(f, s) for s in (1, -1)}
    frames = []
    for variant, hits in variant_hits(f, allowed).items():
        events = sb.with_cooldown(hits, cfg["cooldown_bars"])
        if not events:
            continue
        ei = np.array([i for i, _ in events])
        sd = np.array([s for _, s in events])
        stop = np.where(sd > 0, rope_lo[ei] - sb.STOP_BUFFER * atr[ei], rope_hi[ei] + sb.STOP_BUFFER * atr[ei])
        risk_atr = sd * (o[ei + 1] - stop) / atr[ei]
        n_ctrl = cfg["controls"]["n"] if variant == "primary" else cfg["controls"]["n_variants"]
        ctrl = np.full((len(ei), n_ctrl), -1)
        for k, e in enumerate(ei):
            cand = sb.control_candidates(pool_rows.get(month[e], np.empty(0, int)), atr_pct, e)
            if len(cand):
                ctrl[k] = rng.choice(cand, size=n_ctrl)
        flat, ok = ctrl.ravel(), ctrl.ravel() >= 0
        cside = np.repeat(sd, n_ctrl)[ok]
        cu = flat[ok]
        cstop = o[cu + 1] - cside * np.repeat(risk_atr, n_ctrl)[ok] * atr[cu]
        extra = {a: np.where(sd > 0, flags[1][a][ei], flags[-1][a][ei]) for a in ADDED}
        for target_r in cfg["targets_r"]:
            got = simulate_chunked(o, h, l, c, ei + 1, sd, stop, target_r, cfg["max_hold_bars"], cfg["round_trip_cost"])
            cg = simulate_chunked(o, h, l, c, cu + 1, cside, cstop, target_r, cfg["max_hold_bars"], cfg["round_trip_cost"])
            cr, cb = np.full(len(flat), np.nan), np.full(len(flat), np.nan)
            cr[ok], cb[ok] = cg["net_r"], cg["net_ret"]
            cr, cb = cr.reshape(len(ei), n_ctrl), cb.reshape(len(ei), n_ctrl)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # an event whose controls all failed is NaN
                c_r, c_b = np.nanmean(cr, axis=1), np.nanmean(cb, axis=1)
            frames.append(pd.DataFrame({
                "variant": variant, "symbol": symbol, "minutes": minutes, "target_r": target_r,
                "time": pd.to_datetime(index[ei], unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "month": month[ei], "side": sd, "signal_close": c[ei], "entry": got["entry"], "stop": stop,
                "risk_atr": risk_atr, "risk_frac": got["risk_frac"], "past_width": f.past_width.to_numpy()[ei],
                "past_flips": f.past_flips.to_numpy()[ei], "rv": f.rv.to_numpy()[ei], "close_pos": f.close_pos.to_numpy()[ei],
                **{f"has_{a}": extra[a] for a in ADDED},
                "exit_kind": got["kind"], "bars": got["bars"], "gross_r": got["gross_r"], "net_r": got["net_r"],
                "net_ret": got["net_ret"], "mae_r": got["mae_r"],
                "control_net_r": c_r, "control_net_ret": c_b, "controls_valid": np.isfinite(cr).sum(axis=1)}))
    return frames


def scan_symbol(args: tuple) -> pd.DataFrame:
    """Events and trades (with matched controls) for one symbol on its source's timeframes."""
    symbol, months, cfg, source = args
    lo, hi = pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"])
    months = set(months)
    frames: list[pd.DataFrame] = []
    if source == "1m":
        raw = read_1m(symbol, lo, hi)
        if len(raw):
            frames += scan_frame(symbol, 1, bars_from(raw, 1, 1), bars_from(raw, 1, HTF[1]), months, cfg)
    else:
        raw = v1.read_5m(symbol, lo, hi)
        for minutes in cfg["timeframes_5m_source"]:
            if len(raw):
                frames += scan_frame(symbol, minutes, bars_from(raw, 5, minutes), bars_from(raw, 5, HTF[minutes]), months, cfg)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def example_check() -> dict:
    """Which variants fire on the owner's example (OKX ETH 1m, 2026-10-08 23:02 Beijing break)."""
    if not EXAMPLE_OKX.exists():
        return {"status": "missing", "path": str(EXAMPLE_OKX)}
    raw = pd.read_csv(EXAMPLE_OKX, usecols=["ts", "open", "high", "low", "close", "volume"])
    bars = bars_from(raw, 1, 1)
    index = bars.index.to_numpy()
    up, down = htf_stack(index, 1, bars_from(raw, 1, 5), 5)
    f = features(bars, up, down)
    allowed = np.ones(len(f), bool)
    allowed[-1] = False
    i = int(np.flatnonzero(index == EXAMPLE_BREAK.value // 10**6)[0])
    near = {}
    for variant, hits in variant_hits(f, allowed).items():
        got = [(j - i, s) for j, s in hits if abs(j - i) <= CONFIRM_LEN and s == -1]
        near[variant] = [pd.Timestamp(int(index[i + d]), unit="ms", tz="UTC").tz_convert("Asia/Shanghai").strftime("%H:%M")
                         for d, _ in got]
    row = f.iloc[i]
    return {"status": "ok", "break_bar_bj": "2026-10-08 23:02", "short_hits_within_4_bars": near,
            "past_width": round(float(row.past_width), 3), "past_flips": int(row.past_flips),
            "rv": round(float(row.rv), 3), "close_pos": round(float(row.close_pos), 3)}


def run(output: Path, *, limit: int | None = None, workers: int = 6, workers_1m: int = 3) -> None:
    sources = (Path(__file__), CONFIG, Path("tests/evaluation/test_ma6_knot_breakout_v1.py"), Path(sb.__file__), Path(v1.__file__))
    if limit is None and not v1._committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(CONFIG.read_text())
    began = time.perf_counter()
    check = example_check()
    print(json.dumps({"example_check": check}, ensure_ascii=False), flush=True)
    start, end = pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"])
    all_months = [str(m) for m in pd.period_range(start.tz_convert(None).to_period("M"),
                                                  (end - pd.Timedelta(minutes=1)).tz_convert(None).to_period("M"), freq="M")]
    jobs_1m = [(s, all_months, cfg, "1m") for s in MAJORS_1M]
    symbols = sorted(p.name.removesuffix(".csv.gz") for p in v1.SERIES.glob("*.csv.gz"))
    universe = v1.monthly_universe(symbols, start, pd.Timestamp(cfg["end_5m_source"]), 100)
    by_symbol: dict[str, list[str]] = {}
    for month, names in universe.items():
        for s in list(names) + list(sb.LEADERS):
            by_symbol.setdefault(s, []).append(month)
    jobs_5m = [(s, m, cfg, "5m") for s, m in sorted(by_symbol.items())]
    if limit:
        jobs_1m = [j for j in jobs_1m if j[0] == "ETHUSDT"]
        jobs_5m = [j for j in jobs_5m if j[0] in sb.LEADERS] + [j for j in jobs_5m if j[0] not in sb.LEADERS][:limit]
    print(json.dumps({"symbols_1m": len(jobs_1m), "symbols_5m": len(jobs_5m),
                      "universe_s": round(time.perf_counter() - began, 1)}), flush=True)
    parts = []
    for jobs, w in ((jobs_1m, workers_1m), (jobs_5m, workers)):
        with ProcessPoolExecutor(max_workers=w) as pool:
            for k, part in enumerate(pool.map(scan_symbol, jobs, chunksize=1)):
                parts.append(part)
                if (k + 1) % 25 == 0 or jobs is jobs_1m:
                    print(json.dumps({"done": k + 1, "of": len(jobs), "elapsed_s": round(time.perf_counter() - began, 1)}), flush=True)
    trades = pd.concat([p for p in parts if len(p)], ignore_index=True)
    summary = sb.summarize(trades, cfg)
    eth = sb.summarize(trades.loc[trades.symbol.eq("ETHUSDT") & trades.minutes.eq(1)], cfg)
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    summary.to_csv(output / "summary.csv", index=False)
    eth.to_csv(output / "summary_eth_1m.csv", index=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (output / "receipt.json").write_text(json.dumps({
        "experiment_id": cfg["experiment_id"], "source_commit": head, "limit": limit,
        "symbols_1m": len(jobs_1m), "symbols_5m_source": len(jobs_5m), "trade_rows": len(trades),
        "primary_events": int(trades.loc[trades.variant.eq("primary") & trades.target_r.eq(cfg["targets_r"][0])].shape[0]),
        "example_check": check, "elapsed_s": round(time.perf_counter() - began, 1),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1, ensure_ascii=False))
    view = summary.loc[summary.variant.eq("primary")]
    cols = ["minutes", "side", "target_r", "period", "events", "win_rate", "mean_net_r", "mean_gross_r", "cost_r",
            "control_net_r", "excess_r", "p_excess", "median_risk_pct"]
    print(view[cols].round(3).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=EXP / "results_v1")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--workers-1m", type=int, default=3)
    parser.add_argument("--example-only", action="store_true")
    args = parser.parse_args()
    if args.example_only:
        print(json.dumps(example_check(), indent=1, ensure_ascii=False))
        return
    run(args.output, limit=args.limit, workers=args.workers, workers_1m=args.workers_1m)


if __name__ == "__main__":
    main()
