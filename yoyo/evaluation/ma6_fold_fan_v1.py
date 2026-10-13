"""Six-MA fold-and-fan v1: the owner's 2026-10-07 ETH 1H three-bar fold written as a rule and scanned.

Owner 2026-10-13, on ETHUSDT.P 1H (OKX, Beijing) 2026-10-07 07:00-09:00 with the six SPIKE MAs
(SMA/EMA 20/60/120): every line bends at the same bar, a corner rather than a smooth curve
(acute or obtuse); "只需要检测3根k"; of the candidate extra features only "六条线从同一点散开"
was kept (small K1/K2, price leaning on one side, a full-body K3 were not). The rule therefore
reads the MA geometry only, at the decision bar K3 = i with the vertex K2 = i-1:

  converge  six-MA width at K2 / ATR14 at K2 <= 1.0 (example 0.64)
  turn      every slope s_j(K3) = MA_j(K3) - MA_j(K2) has the fold sign and |s_j(K3)| >= 3 |s_j(K2)|
            (example: smallest ratio 5.4, SMA120)
  fan       width at K3 >= 1.3 x width at K2 (example 1.50) and EMA20 is the outermost MA on the
            fold side at K3 (example: EMA20 lowest)
  angle     obtuse when >= 4 of the six K2 slopes already point in the fold direction, acute when
            >= 4 point against it, mixed otherwise (example: 5 of 6 down, obtuse)

Every field uses bars up to K3's close; the trade goes in the fold direction. A fold is a
deterministic transform of K3's close change (EMA: s(K3) - s(K2) = alpha * (dC - s(K2))), so
``dc_atr`` is stored with each event for reading. Thresholds are round numbers at or beyond
the single example's values; the scanned data ends 2026-09-23, before it. Variants change one
thing: turn_2 / turn_1_5 lower the per-line turn ratio to 2 / 1.5, no_fan drops the fan
condition, converge_2 widens converge to 2.0 ATR. The turn variants were added before any
return was computed, after a count on six majors (2024-10..2026-09) found the ratio-3 turn on
all six lines only 4 times in 104k bars (ratio 2: 48, ratio 1.5: 139) - too rare to judge alone.

Exits, controls and statistics follow exp-ma6-knot-breakout-20261011-v1 / the squeeze study:
entry at the next open, stop beyond all six MAs at K3 plus 0.2 ATR, fixed 3R or 5R target,
192-bar cap, 0.2% round-trip cost, 20 matched random entries per event (5 for variants),
month-block sign-flip p on the excess. Data: the frozen Binance USD-M 5m series resampled to
complete 1H bars, monthly top-100 alts plus BTC/ETH.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import warnings
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import ma6_knot_breakout_v1 as kb
from yoyo.evaluation import market_sync_shock as v1
from yoyo.evaluation import squeeze_breakout_v1 as sb

EXP = Path("experiments/active/exp-ma6-fold-fan-1h-20261013-v1")
CONFIG = EXP / "config.json"
EXAMPLE_OKX = Path("data/research/ma6_fold_fan_1h_20261013/okx_api_ETH_USDT_SWAP_1H_20260721T2100_20261013T0300.csv")
EXAMPLE_K3 = pd.Timestamp("2026-10-07T01:00:00Z")  # 09:00 Beijing
MINUTES = 60
CONVERGE, TURN_RATIO, FAN_RATIO, ANGLE_VOTES = 1.0, 3.0, 1.3, 4
VARIANTS = {"primary": (CONVERGE, TURN_RATIO, True), "turn_2": (CONVERGE, 2.0, True), "turn_1_5": (CONVERGE, 1.5, True),
            "no_fan": (CONVERGE, TURN_RATIO, False), "converge_2": (2.0, TURN_RATIO, True)}
MA_COLS = ("s20", "e20", "s60", "e60", "s120", "e120")


def features(bars: pd.DataFrame) -> pd.DataFrame:
    """Causal fields at each bar i (= K3); ``*_k2`` columns are read at i - 1."""
    o, h, l, c, v = (bars[k] for k in ("open", "high", "low", "close", "volume"))
    valid = np.isfinite(bars[["open", "high", "low", "close"]].to_numpy()).all(axis=1)
    seg = pd.Series(valid.astype(int), index=bars.index)
    seg = seg.groupby((~valid).cumsum()).cumsum()
    prev_c = c.shift()
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1).where(valid)
    atr = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().where(valid)
    ma = kb.six_mas(c, valid)[list(MA_COLS)]
    s = ma.diff()
    s_k2 = s.shift()
    rope_hi, rope_lo = ma.max(axis=1, skipna=False), ma.min(axis=1, skipna=False)
    width = rope_hi - rope_lo
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = (s.abs() / s_k2.abs()).min(axis=1, skipna=False)  # weakest turn of the six
    up_now, down_now = (s > 0).all(axis=1), (s < 0).all(axis=1)
    with_up, with_down = (s_k2 > 0).sum(axis=1), (s_k2 < 0).sum(axis=1)
    out = pd.DataFrame({
        "open": o, "high": h, "low": l, "close": c, "atr": atr, "prev_atr": atr.shift(),
        "rope_hi": rope_hi, "rope_lo": rope_lo,
        "width_k2_atr": width.shift() / atr.shift(), "fan_ratio": width / width.shift(),
        "turn_ratio": ratio, "all_up": up_now, "all_down": down_now,
        "e20_front_long": ma.e20 >= rope_hi, "e20_front_short": ma.e20 <= rope_lo,
        "k2_up": with_up, "k2_down": with_down, "dc_atr": (c - prev_c) / atr.shift(), "seg": seg,
        "frozen_share": ((h == l) | (v <= 0)).astype(float).where(valid).shift()
                        .rolling(sb.FROZEN_LEN, min_periods=sb.FROZEN_LEN).mean(),
    }, index=bars.index)
    return out


def angle_of(f: pd.DataFrame, i: np.ndarray, side: np.ndarray) -> np.ndarray:
    """obtuse / acute / mixed from how many of the six K2 slopes point with or against the fold."""
    up, down = f.k2_up.to_numpy()[i], f.k2_down.to_numpy()[i]
    with_, against = np.where(side > 0, up, down), np.where(side > 0, down, up)
    return np.where(with_ >= ANGLE_VOTES, "obtuse", np.where(against >= ANGLE_VOTES, "acute", "mixed"))


def variant_hits(f: pd.DataFrame, allowed: np.ndarray) -> dict[str, list[tuple[int, int]]]:
    """(K3 bar, side) hits per variant, before the cooldown."""
    ready = kb.ready_mask(f).to_numpy() & allowed
    width = f.width_k2_atr.to_numpy()
    fan_ok = (f.fan_ratio >= FAN_RATIO).to_numpy()
    out: dict[str, list[tuple[int, int]]] = {}
    for name, (converge, turn_ratio, need_fan) in VARIANTS.items():
        base = ready & (width <= converge) & (f.turn_ratio >= turn_ratio).to_numpy()
        hits = []
        for side, turn, front in ((1, f.all_up, f.e20_front_long), (-1, f.all_down, f.e20_front_short)):
            m = base & turn.to_numpy()
            if need_fan:
                m &= fan_ok & front.to_numpy()
            hits += [(int(i), side) for i in np.flatnonzero(m)]
        out[name] = hits
    return out


def scan_frame(symbol: str, bars: pd.DataFrame, months: set[str], cfg: dict) -> list[pd.DataFrame]:
    if len(bars) < sb.READY + 50:
        return []
    index = bars.index.to_numpy()
    f = features(bars)
    start_ms = pd.Timestamp(cfg["start"]).value // 10**6
    month = pd.to_datetime(index, unit="ms").to_period("M").astype(str).to_numpy()
    n = len(f)
    allowed = np.isin(month, list(months)) & (index >= start_ms)
    allowed[n - 1:] = False  # entry needs a next bar
    o, h, l, c = (f[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = f.atr.to_numpy(float)
    atr_pct = atr / c
    rope_hi, rope_lo = f.rope_hi.to_numpy(float), f.rope_lo.to_numpy(float)
    ready = kb.ready_mask(f).to_numpy() & (index >= start_ms)
    ready[n - 1:] = False
    pool_rows = {mo: np.flatnonzero(ready & (month == mo)) for mo in months}
    rng = np.random.default_rng(cfg["controls"]["seed"] + zlib.crc32(f"{symbol}|{MINUTES}".encode()))
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
        angle = angle_of(f, ei, sd)
        for target_r in cfg["targets_r"]:
            got = sb.simulate_many(o, h, l, c, ei + 1, sd, stop, target_r, cfg["max_hold_bars"], cfg["round_trip_cost"])
            cg = sb.simulate_many(o, h, l, c, cu + 1, cside, cstop, target_r, cfg["max_hold_bars"], cfg["round_trip_cost"])
            cr, cb = np.full(len(flat), np.nan), np.full(len(flat), np.nan)
            cr[ok], cb[ok] = cg["net_r"], cg["net_ret"]
            cr, cb = cr.reshape(len(ei), n_ctrl), cb.reshape(len(ei), n_ctrl)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # an event whose controls all failed is NaN
                c_r, c_b = np.nanmean(cr, axis=1), np.nanmean(cb, axis=1)
            frames.append(pd.DataFrame({
                "variant": variant, "angle": angle, "symbol": symbol, "minutes": MINUTES, "target_r": target_r,
                "time": pd.to_datetime(index[ei], unit="ms", utc=True).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "month": month[ei], "side": sd, "signal_close": c[ei], "entry": got["entry"], "stop": stop,
                "risk_atr": risk_atr, "risk_frac": got["risk_frac"],
                **{k: f[k].to_numpy()[ei] for k in ("width_k2_atr", "fan_ratio", "turn_ratio", "dc_atr", "k2_up", "k2_down")},
                "exit_kind": got["kind"], "bars": got["bars"], "gross_r": got["gross_r"], "net_r": got["net_r"],
                "net_ret": got["net_ret"], "never_closed_below": got["never_closed_below"], "mae_r": got["mae_r"],
                "control_net_r": c_r, "control_net_ret": c_b, "controls_valid": np.isfinite(cr).sum(axis=1)}))
    return frames


def scan_symbol(args: tuple) -> pd.DataFrame:
    symbol, months, cfg = args
    raw = v1.read_5m(symbol, pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"]))
    frames = scan_frame(symbol, kb.bars_from(raw, 5, MINUTES), set(months), cfg) if len(raw) else []
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def summarize(trades: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Shared summary per variant x angle group (obtuse / acute / mixed / all)."""
    both = pd.concat([trades.assign(angle="all"), trades], ignore_index=True)
    out = sb.summarize(both.assign(variant=both.variant + "|" + both.angle), cfg)
    parts = out.variant.str.split("|", expand=True)
    return out.assign(variant=parts[0], angle=parts[1])


def example_check() -> dict:
    """Does primary fire on the owner's example (OKX ETH 1H, K3 = 2026-10-07 09:00 Beijing)?"""
    if not EXAMPLE_OKX.exists():
        return {"status": "missing", "path": str(EXAMPLE_OKX)}
    raw = pd.read_csv(EXAMPLE_OKX, usecols=["ts", "open", "high", "low", "close", "volume"])
    bars = kb.bars_from(raw, MINUTES, MINUTES)
    f = features(bars)
    allowed = np.ones(len(f), bool)
    allowed[-1] = False
    index = bars.index.to_numpy()
    i = int(np.flatnonzero(index == EXAMPLE_K3.value // 10**6)[0])
    hits = {v: [(j - i, s) for j, s in got if abs(j - i) <= 2] for v, got in variant_hits(f, allowed).items()}
    row = f.iloc[i]
    return {"status": "ok", "k3_bj": "2026-10-07 09:00", "hits_within_2_bars": hits,
            "angle": str(angle_of(f, np.array([i]), np.array([-1]))[0]),
            "width_k2_atr": round(float(row.width_k2_atr), 3), "turn_ratio": round(float(row.turn_ratio), 2),
            "fan_ratio": round(float(row.fan_ratio), 3), "dc_atr": round(float(row.dc_atr), 2)}


def run(output: Path, *, limit: int | None = None, workers: int = 6) -> None:
    sources = (Path(__file__), CONFIG, Path("tests/evaluation/test_ma6_fold_fan_v1.py"), Path(kb.__file__),
               Path(sb.__file__), Path(v1.__file__))
    if limit is None and not v1._committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(CONFIG.read_text())
    began = time.perf_counter()
    check = example_check()
    print(json.dumps({"example_check": check}, ensure_ascii=False), flush=True)
    start, end = pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"])
    symbols = sorted(p.name.removesuffix(".csv.gz") for p in v1.SERIES.glob("*.csv.gz"))
    universe = v1.monthly_universe(symbols, start, end, 100)
    by_symbol: dict[str, list[str]] = {}
    for month, names in universe.items():
        for s in list(names) + list(sb.LEADERS):
            by_symbol.setdefault(s, []).append(month)
    jobs = [(s, m, cfg) for s, m in sorted(by_symbol.items())]
    if limit:
        jobs = [j for j in jobs if j[0] in sb.LEADERS] + [j for j in jobs if j[0] not in sb.LEADERS][:limit]
    print(json.dumps({"symbols": len(jobs), "universe_s": round(time.perf_counter() - began, 1)}), flush=True)
    parts = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for k, part in enumerate(pool.map(scan_symbol, jobs, chunksize=1)):
            parts.append(part)
            if (k + 1) % 25 == 0:
                print(json.dumps({"done": k + 1, "of": len(jobs), "elapsed_s": round(time.perf_counter() - began, 1)}), flush=True)
    trades = pd.concat([p for p in parts if len(p)], ignore_index=True)
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    summary = summarize(trades, cfg)
    summary.to_csv(output / "summary.csv", index=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (output / "receipt.json").write_text(json.dumps({
        "experiment_id": cfg["experiment_id"], "source_commit": head, "limit": limit, "symbols": len(jobs),
        "trade_rows": len(trades),
        "primary_events": int(trades.loc[trades.variant.eq("primary") & trades.target_r.eq(cfg["targets_r"][0])].shape[0]),
        "example_check": check, "elapsed_s": round(time.perf_counter() - began, 1),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1, ensure_ascii=False))
    view = summary.loc[summary.variant.eq("primary")]
    cols = ["angle", "side", "target_r", "period", "events", "win_rate", "mean_net_r", "mean_gross_r", "cost_r",
            "control_net_r", "excess_r", "p_excess", "median_risk_pct"]
    print(view[cols].round(3).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=EXP / "results_v1")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--example-only", action="store_true")
    args = parser.parse_args()
    if args.example_only:
        print(json.dumps(example_check(), indent=1, ensure_ascii=False))
        return
    run(args.output, limit=args.limit, workers=args.workers)


if __name__ == "__main__":
    main()
