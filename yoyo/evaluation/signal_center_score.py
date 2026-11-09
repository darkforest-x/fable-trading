"""Signal-center cloud signals on 12H/4H/1H/15m: causal features, matched random
controls and one NautilusTrader execution (exp-signal-center-score-20261106-v1).

Owner 2026-11-05/06: "针对我们信号中心的信号（12h的，4h,1h,15min）再做模型做评分" and
approved the plan "按方案开训". The live signal center only has 12H; 4H/1H/15m use
the same rule on that timeframe (owner-approved interpretation).

Signal rule (copied from yoyo/monitor/cloud_signals.signal, parity-tested): a
complete UTC-aligned bar lying wholly after the complete NY Monday and before the
week expires; EMA20/50 of that timeframe's closes (alpha 2/(n+1), first-close
seed, restart on a missing bar, >=50 contiguous bars); long if EMA20>EMA50 and
low < lower1618 < close < upper1618, short mirrored. First signal per symbol x NY
week x timeframe (signal-center eligibility). Monday range from complete 5m bars.

Executable label (owner-approved): market entry at the signal bar close (taker),
fixed stop at the touched 1.618 * (1 -/+ 1.5% BTC/ETH, 3% others) as in
cloud_signal_plans.fixed_stop, exit at the first of that stop and the intrabar
EMA20/50 cross of the signal timeframe (cross price from the prior closed bar's
EMA state, as cloud-signal-chart.js planExcursion). No target. Executed on 5m
bars by yoyo.layers.l3_backtest.nautilus_stop_schedule; maker 2bp / taker 5bp.

Features use only the signal bar and earlier data (columns and windows listed in
FEATURES). Matched random controls: same symbol, timeframe, NY week window and
side, EMA aligned with the side, not a raw signal bar, same quarter-octave
log2(ATR14/close) bucket; up to three per signal, seeded by crc32 of the key; the
signal's close-to-stop distance is reused. Controls are never chosen by outcome.

Data: frozen Monday-limit cohort (632 Binance UM crypto jobs, 2020-01..2026-09-23)
via monday_limit_reclaim_inputs.jobs/load_job; six 1m assets aggregated to complete
5m bars. Run with the isolated Nautilus interpreter:
  $HOME/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.signal_center_score \
      --config experiments/active/exp-signal-center-score-20261106-v1/config.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import zlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
NY = "America/New_York"
TFS = {"12H": 720, "4H": 240, "1H": 60, "15m": 15}
A20, A50 = 2 / 21, 2 / 51
NS = 10**9
CODE = [Path(__file__), ROOT / "yoyo/layers/l3_backtest/nautilus_stop_schedule.py"]

# Feature -> definition (all at the signal bar close; s = side +1/-1).
FEATURES = {
    "side": "s",
    "pen_atr": "beyond-1618 wick depth / ATR14: long (lower-low), short (high-upper)",
    "pen_pct": "same depth / close",
    "reclaim_atr": "close back inside from the touched 1618 / ATR14",
    "close_pos": "close position across lower..upper measured from the touched side",
    "wick_ratio": "touched-side wick / bar range",
    "body_atr": "s*(close-open)/ATR14",
    "range_atr": "(high-low)/ATR14",
    "atr_pct": "ATR14/close (Wilder, contiguous bars)",
    "ema_spread_atr": "s*(EMA20-EMA50)/ATR14",
    "ema20_slope_atr": "s*(EMA20 - EMA20 five bars earlier)/ATR14",
    "close_ema20_atr": "s*(close-EMA20)/ATR14",
    "monday_width_pct": "(Monday high-low)/close",
    "stop_dist_pct": "s*(close-stop)/close",
    "rr_close": "s*(opposite 1618 - close) / s*(close-stop)",
    "log_vol_ratio": "log(bar volume / median volume of the 20 prior bars)",
    "hours_since_monday": "hours from NY Monday end to the bar close",
    "dow_ny": "NY weekday of the bar close",
    "prior_touches": "earlier bars this week whose wick passed the touched 1618",
    "ret_24h": "s*log(close / 5m close 24h earlier)",
    "ret_7d": "s*log(close / 5m close 7d earlier)",
    "log_qv_7d": "log(mean daily close*volume over the prior 7 days of 5m bars)",
    "age_days": "days since the symbol's first bar in the frozen source",
    "btc_ret_24h": "s*BTC 24h log return at the same close",
    "btc_side": "s*sign(BTC EMA20-EMA50) on the same timeframe at the same close",
    "n_same_bar": "cohort raw signals with the same timeframe, close and side (added at model stage)",
}


def stop_pct(symbol):
    return 0.015 if symbol.removesuffix("USDT") in ("BTC", "ETH") else 0.03


def five_minute(job):
    """Load one job as complete 5m OHLCV bars indexed by bar OPEN time (UTC ns)."""
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    f, _ = load_job(job)
    f.index = f.index.as_unit("ns")
    if int(job["execution_minutes"]) == 1:
        g = f.resample("5min", origin="epoch", label="left", closed="left").agg(
            open=("open", "first"), high=("high", "max"), low=("low", "min"),
            close=("close", "last"), volume=("volume", "sum"), n=("close", "size"))
        f = g[g.n == 5].drop(columns="n")
        f.index = f.index.as_unit("ns")
    return f[["open", "high", "low", "close", "volume"]]


def monday_ranges(f, source_minutes=5):
    ny = f.index.tz_convert(NY)
    monday = ny.normalize() - pd.to_timedelta(ny.weekday, unit="D")
    is_mon = ny.weekday == 0
    g = f[is_mon].groupby(monday[is_mon])
    r = g.agg(high=("high", "max"), low=("low", "min"), n=("close", "size"))
    r = r[(r.n == 1440 // source_minutes) & (r.high > r.low)].copy()
    width = r.high - r.low
    r["upper"], r["lower"] = r.high + .618 * width, r.low - .618 * width
    r["end"] = (r.index + pd.Timedelta(days=1)).tz_convert("UTC").as_unit("ns")
    r["expires"] = pd.DatetimeIndex([pd.Timestamp((x + pd.Timedelta(days=7)).date(), tz=NY) for x in r.index]
                                    ).tz_convert("UTC").as_unit("ns")
    return r


def tf_bars(f, minutes, source_minutes=5):
    """Complete UTC-aligned bars with EMA20/50, Wilder ATR14 and next-bar cross price."""
    fields = dict(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                  close=("close", "last"), n=("close", "size"))
    if "volume" in f:
        fields["volume"] = ("volume", "sum")
    b = f.resample(f"{minutes}min", origin="epoch", label="left", closed="left").agg(**fields)
    b = b[b.n == minutes // source_minutes].drop(columns="n").copy()
    b.index = b.index.as_unit("ns")
    step = minutes * 60 * NS
    t = b.index.asi8
    seg = np.r_[0, np.cumsum(np.diff(t) != step)] if len(t) else np.array([], int)
    b["seg"] = seg
    c = b.close
    b["e20"] = c.groupby(seg).transform(lambda s: s.ewm(alpha=A20, adjust=False).mean())
    b["e50"] = c.groupby(seg).transform(lambda s: s.ewm(alpha=A50, adjust=False).mean())
    b["count"] = b.groupby("seg").cumcount() + 1
    h, lo, cl = b.high.to_numpy(), b.low.to_numpy(), b.close.to_numpy()
    atr = np.full(len(b), np.nan)
    prev_c, total, cur, cnt, last_seg = None, 0., np.nan, 0, -1
    for i in range(len(b)):
        if seg[i] != last_seg:
            prev_c, total, cur, cnt, last_seg = None, 0., np.nan, 0, seg[i]
        tr = h[i] - lo[i]
        if prev_c is not None:
            tr = max(tr, abs(h[i] - prev_c), abs(lo[i] - prev_c))
        cnt += 1
        if cnt <= 14:
            total += tr
            cur = total / 14 if cnt == 14 else np.nan
        else:
            cur += (tr - cur) / 14
        atr[i] = cur
        prev_c = cl[i]
    b["atr"] = atr
    b["cross_next"] = ((1 - A50) * b.e50 - (1 - A20) * b.e20) / (A20 - A50)
    b["close_ns"] = t + step
    return b


def _ns(values):
    return pd.DatetimeIndex(values).as_unit("ns").asi8


def _asof(close_ns, values, at_ns, max_lag_ns):
    pos = np.searchsorted(close_ns, at_ns, side="right") - 1
    out = np.full(len(at_ns), np.nan)
    ok = (pos >= 0)
    ok[ok] &= (at_ns[ok] - close_ns[pos[ok]]) <= max_lag_ns
    out[ok] = values[pos[ok]]
    return out


def btc_context(cfg):
    """BTC 24h log return per 5m close and per-timeframe EMA side per bar close."""
    from yoyo.evaluation.monday_limit_reclaim_inputs import jobs
    job = next(j for j in jobs(cfg["source_config"]) if j["symbol"] == "BTCUSDT")
    f = five_minute(job)
    close_ns = f.index.asi8 + 300 * NS
    c = f.close.to_numpy()
    prev = _asof(close_ns, c, close_ns - 86400 * NS, 3600 * NS)
    out = dict(close_ns=close_ns, ret24=np.log(c / prev))
    for tf, m in TFS.items():
        b = tf_bars(f, m)
        ok = b["count"].to_numpy() >= 50
        out[f"side_{tf}_ns"] = b.close_ns.to_numpy()[ok]
        out[f"side_{tf}"] = np.sign((b.e20 - b.e50).to_numpy()[ok])
    return out


def features_for(sig, b, f5, rng_week, ctx, first_ns, tf):
    s = sig.side.to_numpy()
    atr = sig.atr.to_numpy()
    lo, hi, op, cl = (sig[k].to_numpy() for k in ("low", "high", "open", "close"))
    lower, upper = sig.lower.to_numpy(), sig.upper.to_numpy()
    touched = np.where(s > 0, lower, upper)
    far = np.where(s > 0, upper, lower)
    pen = np.where(s > 0, lower - lo, hi - upper)
    X = pd.DataFrame(index=sig.index)
    X["side"] = s
    X["pen_atr"], X["pen_pct"] = pen / atr, pen / cl
    X["reclaim_atr"] = s * (cl - touched) / atr
    X["close_pos"] = s * (cl - touched) / (upper - lower)
    rng = hi - lo
    X["wick_ratio"] = np.where(s > 0, np.minimum(op, cl) - lo, hi - np.maximum(op, cl)) / rng
    X["body_atr"], X["range_atr"], X["atr_pct"] = s * (cl - op) / atr, rng / atr, atr / cl
    X["ema_spread_atr"] = s * (sig.e20 - sig.e50).to_numpy() / atr
    e20_lag = b.e20.shift(5).where(b.seg.shift(5) == b.seg)
    X["ema20_slope_atr"] = s * (sig.e20.to_numpy() - e20_lag.reindex(sig.index).to_numpy()) / atr
    X["close_ema20_atr"] = s * (cl - sig.e20.to_numpy()) / atr
    X["monday_width_pct"] = (sig.mon_high - sig.mon_low).to_numpy() / cl
    X["stop_dist_pct"] = s * (cl - sig.stop.to_numpy()) / cl
    X["rr_close"] = s * (far - cl) / (s * (cl - sig.stop.to_numpy()))
    med = b.volume.shift(1).rolling(20, min_periods=20).median().where(b["count"] > 20)
    X["log_vol_ratio"] = np.log(sig.volume.to_numpy() / med.reindex(sig.index).to_numpy())
    cns = sig.close_ns.to_numpy()
    X["hours_since_monday"] = (cns - _ns(sig.end)) / 3600 / NS
    X["dow_ny"] = pd.DatetimeIndex(cns).tz_localize("UTC").tz_convert(NY).weekday
    X["prior_touches"] = sig.prior_touches.to_numpy()
    c5_ns = f5.index.asi8 + 300 * NS
    c5 = f5.close.to_numpy()
    X["ret_24h"] = s * np.log(cl / _asof(c5_ns, c5, cns - 86400 * NS, 3600 * NS))
    X["ret_7d"] = s * np.log(cl / _asof(c5_ns, c5, cns - 7 * 86400 * NS, 3600 * NS))
    qv = pd.Series(c5 * f5.volume.to_numpy(), index=pd.DatetimeIndex(c5_ns).tz_localize("UTC"))
    qv7 = qv.rolling("7D").sum().to_numpy()
    X["log_qv_7d"] = np.log(np.maximum(_asof(c5_ns, qv7, cns, 0), 1e-12) / 7)
    X["age_days"] = (cns - first_ns) / 86400 / NS
    X["btc_ret_24h"] = s * _asof(ctx["close_ns"], ctx["ret24"], cns, 0)
    X["btc_side"] = s * _asof(ctx[f"side_{tf}_ns"], ctx[f"side_{tf}"], cns, 0)
    return X


_CTX = None


def _ctx(path):
    global _CTX
    if _CTX is None:
        z = np.load(path)
        _CTX = {k: z[k] for k in z.files}
    return _CTX


def build_inputs(f, sym, ctx, *, key_timeframes=None, source_minutes=5, price_decimals=None):
    """Signals, causal features, matched controls and stop schedules for one symbol.

    ``f`` is complete 5m OHLCV indexed by bar open. Reads no trade outcome.
    Returns (f_rounded, tick, decimals, close_ns5, signals, raw, entries, schedules).
    With key_timeframes only the key columns for those timeframes are needed;
    skip model features and sampling of model-training controls. Key-only extraction also
    accepts complete aligned 15m or hourly OHLC when the timeframe is divisible
    by that interval. For extensions, price_decimals can be frozen before the
    first new key. The model feature path still requires native five-minute volume.
    """
    if source_minutes not in (5, 15, 60) or (source_minutes != 5 and key_timeframes is None):
        raise ValueError("Coarser intervals support only closed-key extraction")
    if key_timeframes and any(TFS[tf] % source_minutes for tf in key_timeframes):
        raise ValueError("Key timeframe must contain an integer number of source intervals")
    f = f.copy()
    if price_decimals is None:
        decimals = max(2, min(9, int(np.ceil(-np.log10(f[["open", "high", "low", "close"]].to_numpy().min()))) + 7))
    elif isinstance(price_decimals, int) and 2 <= price_decimals <= 9:
        decimals = price_decimals
    else:
        raise ValueError("Invalid frozen input precision")
    tick = 10.0 ** (-decimals)
    f[["open", "high", "low", "close"]] = f[["open", "high", "low", "close"]].round(decimals)
    close_ns5 = f.index.asi8 + source_minutes * 60 * NS
    pos_of = pd.Index(close_ns5)
    mr = monday_ranges(f, source_minutes)
    pct = stop_pct(sym)
    sig_parts, raw_parts, entries, schedules = [], [], [], {}
    timeframes = TFS.items() if key_timeframes is None else [(tf, TFS[tf]) for tf in key_timeframes]
    for tf, m in timeframes:
        b = tf_bars(f, m, source_minutes)
        if len(b) < 60:
            continue
        step = m * 60 * NS
        pos = pos_of.get_indexer(b.close_ns.to_numpy())
        if (pos < 0).any():
            raise RuntimeError("complete bar close missing from 5m frame")
        schedules[tf] = (pos.astype(np.int64), b.cross_next.to_numpy(), b.close_ns.to_numpy() + step)
        bny = b.index.tz_convert(NY)
        b["wk"] = bny.normalize() - pd.to_timedelta(bny.weekday, unit="D")
        j = b.join(mr[["high", "low", "upper", "lower", "end", "expires"]].rename(
            columns={"high": "mon_high", "low": "mon_low"}), on="wk", how="inner")
        t = j.index.asi8
        ok = ((j["count"] >= 50) & (t >= _ns(j.end))
              & (t + step <= _ns(j.expires))).to_numpy()
        lo, hi, c = j.low.to_numpy(), j.high.to_numpy(), j.close.to_numpy()
        up, dn = j.upper.to_numpy(), j.lower.to_numpy()
        bull, bear = (j.e20 > j.e50).to_numpy(), (j.e20 < j.e50).to_numpy()
        inside = (dn < c) & (c < up)
        long_ = ok & bull & (lo < dn) & inside
        short = ok & bear & (up < hi) & inside
        side = np.where(long_, 1, np.where(short, -1, 0))
        j["side"] = side
        j["pos"] = pos_of.get_indexer(j.close_ns.to_numpy())
        # touches of each side earlier in the same week (wick beyond, any close)
        j["t_lo"] = (ok & (lo < dn)).astype(int)
        j["t_hi"] = (ok & (hi > up)).astype(int)
        g = j.groupby("wk")
        j["prior_lo"] = g.t_lo.cumsum() - j.t_lo
        j["prior_hi"] = g.t_hi.cumsum() - j.t_hi
        raw = j[side != 0]
        raw_parts.append(pd.DataFrame(dict(tf=tf, close_ns=raw.close_ns.to_numpy(), side=raw.side.to_numpy())))
        first = raw.groupby("wk").head(1).copy()
        first = first[first.pos < len(f) - 2] if source_minutes == 5 else first[first.close_ns <= close_ns5[-1] - 600 * NS]
        if first.empty:
            continue
        s = first.side.to_numpy()
        touched = np.where(s > 0, first.lower, first.upper)
        raw_stop = touched * (1 - s * pct)
        first["stop"] = np.where(s > 0, np.floor(raw_stop / tick + 1e-7), np.ceil(raw_stop / tick - 1e-7)) * tick
        first["prior_touches"] = np.where(s > 0, first.prior_lo, first.prior_hi)
        first["bucket"] = np.floor(np.log2(first.atr / first.close) * 4)
        first["signal_id"] = [f"{tf}|{sym}|{int(x)}|{int(v)}" for x, v in zip(first.close_ns, first.side)]
        meta = first[["signal_id", "close_ns", "side", "close", "stop", "pos", "bucket"]].copy()
        meta["tf"], meta["week"] = tf, [str(w.date()) for w in first.wk]
        if key_timeframes is not None:
            sig_parts.append(meta)
            continue
        X = features_for(first, b, f, mr, ctx, close_ns5[0] - 300 * NS, tf)
        sig_parts.append(pd.concat([meta, X.drop(columns="side")], axis=1))
        for r in meta.itertuples():
            entries.append(dict(trade_id=r.signal_id, entry_i=int(r.pos), side=int(r.side), entry=float(r.close),
                                stop=float(r.stop), schedule=tf, kind="signal", parent=r.signal_id))
        # matched random controls
        bucket_all = np.floor(np.log2(j.atr.to_numpy() / c) * 4)
        cand = ok & (side == 0) & np.isfinite(bucket_all) & (j.pos.to_numpy() < len(f) - 2)
        wk_arr = pd.Index(j.wk)
        for r in first.itertuples():
            sd = int(r.side)
            aligned = bull if sd > 0 else bear
            mask = cand & aligned & (wk_arr == r.wk) & (bucket_all == r.bucket)
            idx = np.flatnonzero(mask)
            if not len(idx):
                continue
            seed = zlib.crc32(f"{sym}|{tf}|{r.signal_id}".encode())
            pick = np.sort(np.random.default_rng(seed).choice(idx, size=min(3, len(idx)), replace=False))
            d = sd * (r.close - r.stop) / r.close
            for k, q in enumerate(pick):
                e = float(c[q])
                raw_cs = e * (1 - sd * d)
                cs = (math.floor(raw_cs / tick + 1e-7) if sd > 0 else math.ceil(raw_cs / tick - 1e-7)) * tick
                if not sd * (e - cs) > tick * 0.5:
                    continue
                entries.append(dict(trade_id=f"{r.signal_id}|c{k}", entry_i=int(j.pos.iloc[q]), side=sd,
                                    entry=e, stop=float(round(cs, decimals)), schedule=tf, kind="control",
                                    parent=r.signal_id))
    raw_all = pd.concat(raw_parts) if raw_parts else pd.DataFrame(columns=["tf", "close_ns", "side"])
    sigs = pd.concat(sig_parts) if sig_parts else pd.DataFrame()
    return f, tick, decimals, close_ns5, sigs, raw_all, entries, schedules


def process(job, cfg, out_dir):
    from yoyo.layers.l3_backtest.nautilus_stop_schedule import run_schedule
    started = time.perf_counter()
    sym = job["symbol"]
    dest = Path(out_dir) / "symbols" / sym
    if (dest / "done.json").exists():
        return sym, "cached"
    dest.mkdir(parents=True, exist_ok=True)
    ctx = _ctx(Path(out_dir) / "btc_context.npz")
    f, tick, decimals, close_ns5, sigs, raw_all, entries, schedules = build_inputs(five_minute(job), sym, ctx)
    raw_all.to_csv(dest / "raw.csv.gz", index=False)
    audit = dict(symbol=sym, bars_5m=len(f), first=str(f.index[0]), last=str(f.index[-1]), tick=tick)
    if entries:
        ent = pd.DataFrame(entries)
        ent["stop"] = ent.stop.round(decimals)
        frame = f[["open", "high", "low", "close"]].copy()
        frame.index = pd.DatetimeIndex(close_ns5).tz_localize("UTC")
        trades, run_audit = run_schedule(frame, ent, schedules, tick, symbol=sym.removesuffix("USDT")[:12] or "X",
                                         taker=cfg["taker_fee"], maker=cfg["maker_fee"])
        trades = trades.merge(ent[["trade_id", "kind", "parent", "schedule"]], on="trade_id")
        trades["entry_ns"] = close_ns5[trades.entry_i.to_numpy()]
        trades["exit_ns"] = close_ns5[trades.exit_i.to_numpy()]
        trades["symbol"] = sym
        trades.to_csv(dest / "trades.csv.gz", index=False)
        sigs["symbol"] = sym
        sigs.to_csv(dest / "signals.csv.gz", index=False)
        audit.update(run_audit, signals=int((ent.kind == "signal").sum()), controls=int((ent.kind == "control").sum()))
    audit["seconds_total"] = round(time.perf_counter() - started, 2)
    audit["code"] = [dict(path=str(p.relative_to(ROOT)), bytes=p.stat().st_size, mtime_ns=p.stat().st_mtime_ns)
                     for p in CODE]
    (dest / "done.json").write_text(json.dumps(audit, indent=1))
    return sym, audit.get("signals", 0)


def _worker(args):
    job, cfg, out = args
    try:
        return process(job, cfg, out)
    except Exception as exc:  # recorded, never silently skipped
        return job["symbol"], f"ERROR {type(exc).__name__}: {exc}"


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--symbols", nargs="*")
    p.add_argument("--workers", type=int, default=8)
    a = p.parse_args(argv)
    cfg = json.loads(Path(a.config).read_text())
    out = ROOT / cfg["output_dir"]
    out.mkdir(parents=True, exist_ok=True)
    from yoyo.evaluation.monday_limit_reclaim_inputs import jobs
    js = [j for j in jobs(cfg["source_config"]) if j["group"] != "spot_gold"]
    if a.symbols:
        js = [j for j in js if j["symbol"] in a.symbols]
    ctx_path = out / "btc_context.npz"
    if not ctx_path.exists():
        np.savez_compressed(ctx_path, **btc_context(cfg))
    errors = []
    with ProcessPoolExecutor(a.workers) as ex:
        futs = [ex.submit(_worker, (j, cfg, str(out))) for j in js]
        for n, fu in enumerate(as_completed(futs), 1):
            sym, res = fu.result()
            if isinstance(res, str) and res.startswith("ERROR"):
                errors.append(dict(symbol=sym, error=res))
            if n % 25 == 0 or n == len(futs):
                print(f"{n}/{len(futs)} done, errors {len(errors)}", flush=True)
    (out / "run_errors.json").write_text(json.dumps(errors, indent=1))
    print("errors", len(errors), errors[:5])


if __name__ == "__main__":
    sys.exit(main())
