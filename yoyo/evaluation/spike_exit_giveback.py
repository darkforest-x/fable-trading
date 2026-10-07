"""SPIKE V12.8 / V13.0 exit study: how much open profit the incumbent exit gives back.

Owner, 2026-10-07: "spike 策略止盈有非常大的问题……主要是盈利回吐太多" -- for both the
V12.8 ordinary start (``baseline`` policy) and the V13.0 retest (``retest`` policy).

Single variable: the exit rule. Every original entry, side, entry price and initial
stop is frozen from the registered long replay
``exp-spike-v128-entry-clock-long-20260923-v1/retest_v1`` (v9_both arm, closed,
uncensored, in-window). The incumbent exit is: stop tested intrabar (gap fills at the
open); after a close at >= 2R a 4ATR trail from the close ratchets the stop, effective
next bar; a raw opposite V6 signal exits at the next open.

Every candidate only *tightens* protection or scales out earlier, never loosens. So a
candidate either acts before the recorded incumbent exit bar, or the trade leaves
exactly as recorded. That lets the study replay only entry..exit bars and reuse the
recorded opposite-signal exits without re-deriving V6. ``baseline`` re-derives the
incumbent stop path and is checked against the recorded exits (parity table).

Matched random control: each target's matched control from the same replay (same
symbol x UTC week x volatility bin), rebuilt with the same initial-stop function and
replayed under the same candidates. Costs: 0.1% per side on each fill (0.2% round
trip), as in the parent. Selection uses exits before 2025-01-01 only; later exits
are reported, not tuned on.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import _smma
from yoyo.evaluation.spike_v126_htf_recheck import complete_bars
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec
from yoyo.evaluation.spike_v7_fast import _initial_position_fast

EXP = Path("experiments/active/exp-spike-exit-giveback-20261007-v1")
CONFIG = EXP / "config.json"
SOURCE = Path("experiments/active/exp-spike-v128-entry-clock-long-20260923-v1")
SERIES = Path("data/research/spike_v128_entry_clock_long_20260923/series")
SIDE_COST = 0.001
INCUMBENT_ARM_R, INCUMBENT_TRAIL_ATR = 2.0, 4.0

# name -> (description, single change relative to the incumbent)
POLICIES = {
    "incumbent": "2R close arms a 4ATR close trail; opposite V6 next open",
    "trail3": "trail distance 3ATR instead of 4ATR",
    "trail2": "trail distance 2ATR instead of 4ATR",
    "arm1": "trail arms at a 1R close instead of 2R",
    "be1": "after a 1R close the stop moves to entry (price breakeven)",
    "lock1": "after a 2R close peak the stop locks peak-1R (gives back at most 1R of close profit)",
    "half2": "after the first 2R close, 50% exits at the next open; the rest keeps the incumbent",
}
VERSIONS = {"baseline": "V12.8 普通启动", "retest": "V13.0 回踩再突破"}


def _tick_round(value: float, side: int, tick: float) -> float:
    return math.floor(value / tick) * tick if side == 1 else math.ceil(value / tick) * tick


def _tighter(a: float, b: float, side: int) -> float:
    return max(a, b) if side == 1 else min(a, b)


def simulate(policy: str, *, side: int, entry: float, stop0: float, tick: float,
             o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, atr: np.ndarray,
             e: int, x: int, exit_price: float, exit_reason: str) -> dict:
    """Replay bars e..x (incumbent exit bar x) under one policy. Returns fills and diagnostics.

    The stop in force during bar j comes from closes up to j-1 (updates take effect next
    bar), matching the incumbent engine's order: test stop, then apply close updates.
    """
    risk = side * (entry - stop0)
    inc = pol = stop0
    inc_armed = pol_armed = be_done = half_pending = half_done = False
    peak_close_r = 0.0
    remaining, fills, parity = 1.0, [], "ok"
    opposite = exit_reason.startswith("opposite")
    for j in range(e, x + 1):
        if half_pending:
            fills.append((0.5, float(o[j]), "half2"))
            remaining, half_pending = 0.5, False
        if j == x and opposite:
            tight = _tighter(inc, pol, side)
            gapped = o[j] <= tight if side == 1 else o[j] >= tight
            fills.append((remaining, float(o[j]) if gapped else float(exit_price), "incumbent_opposite"))
            remaining = 0.0
            break
        tight = _tighter(inc, pol, side)
        breached = l[j] <= tight if side == 1 else h[j] >= tight
        if breached:
            price = min(o[j], tight) if side == 1 else max(o[j], tight)
            policy_only = tight != inc
            if j < x and not policy_only:
                parity = "incumbent_stop_before_recorded_exit"
            if j == x and not policy_only:
                if abs(price - exit_price) > max(tick, 1e-12) * 1.01:
                    parity = "exit_price_mismatch"
                price = float(exit_price)
            fills.append((remaining, float(price), "policy_stop" if policy_only else "incumbent_stop"))
            remaining = 0.0
            break
        if j == x:
            parity = "recorded_stop_not_reproduced"
            fills.append((remaining, float(exit_price), "incumbent_recorded"))
            remaining = 0.0
            break
        r_close = side * (c[j] - entry) / risk
        peak_close_r = max(peak_close_r, r_close)
        a = atr[j]
        inc_armed = inc_armed or r_close >= INCUMBENT_ARM_R
        if inc_armed and math.isfinite(a) and a > 0:
            inc = _tighter(inc, _tick_round(c[j] - side * INCUMBENT_TRAIL_ATR * a, side, tick), side)
        if policy in ("trail3", "trail2") and inc_armed and math.isfinite(a) and a > 0:
            dist = 3.0 if policy == "trail3" else 2.0
            pol = _tighter(pol, _tick_round(c[j] - side * dist * a, side, tick), side)
        elif policy == "arm1":
            pol_armed = pol_armed or r_close >= 1.0
            if pol_armed and math.isfinite(a) and a > 0:
                pol = _tighter(pol, _tick_round(c[j] - side * INCUMBENT_TRAIL_ATR * a, side, tick), side)
        elif policy == "be1" and not be_done and r_close >= 1.0:
            be_done = True
            pol = _tighter(pol, entry, side)
        elif policy == "lock1" and peak_close_r >= 2.0:
            pol = _tighter(pol, _tick_round(entry + side * (peak_close_r - 1.0) * risk, side, tick), side)
        elif policy == "half2" and not half_done and r_close >= 2.0:
            half_done = half_pending = True
    gross = sum(q * side * (p / entry - 1.0) for q, p, _ in fills)
    net = gross - SIDE_COST - SIDE_COST * sum(q for q, _, _ in fills)
    last = fills[-1][2] if fills else "none"
    return {"net_return": net, "gross_return": gross, "net_r": net / (risk / entry),
            "gross_r": gross / (risk / entry), "peak_close_r": peak_close_r, "exit_kind": last,
            "parity": parity, "acted": any(k in ("policy_stop", "half2") for _, _, k in fills)}


def _bars(symbol: str, minutes: int, cfg: dict) -> pd.DataFrame:
    raw = pd.read_csv(SERIES / f"{symbol}.csv.gz", usecols=["ts", "open", "high", "low", "close", "volume"])
    index = pd.DatetimeIndex(pd.to_datetime(raw.ts.to_numpy(), unit="ms", utc=True))
    base = pd.DataFrame(raw[["open", "high", "low", "close", "volume"]].to_numpy(float), index=index,
                        columns=["open", "high", "low", "close", "volume"])
    base = base.loc[(base.index >= pd.Timestamp(cfg["warmup_start"])) & (base.index + pd.Timedelta(minutes=5) <= pd.Timestamp(cfg["end"]))]
    bars, _ = complete_bars(base, minutes)
    prev = bars.close.shift()
    tr = pd.concat((bars.high - bars.low, (bars.high - prev).abs(), (bars.low - prev).abs()), axis=1).max(axis=1)
    bars = bars.assign(atr=_smma(tr, 14))
    # Missing buckets break the bar clock; the parent marks them as data gaps.
    step = pd.Timedelta(minutes=minutes)
    bars["gap"] = bars.index.to_series().diff().ne(step).to_numpy() & (np.arange(len(bars)) > 0)
    return bars


def _read(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (pd.errors.EmptyDataError, FileNotFoundError):
        return pd.DataFrame()


def run_stream(args: tuple) -> list[dict]:
    symbol, minutes, cfg = args
    stream = SOURCE / "retest_v1" / "streams" / f"{symbol}_{minutes}m"
    trades = _read(stream / "serial_trades.csv.gz")
    controls = _read(stream / "controls.csv.gz")
    if trades.empty:
        return []
    trades = trades[(trades.arm == "v9_both") & trades.policy.isin(list(VERSIONS)) & trades.in_window.astype(bool)
                    & ~trades.censored.astype(bool) & (trades.status == "closed")]
    if trades.empty:
        return []
    bars = _bars(symbol, minutes, cfg)
    o, h, l, c, atr = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close", "atr"))
    gaps = bars.gap.to_numpy(bool)
    loc = {t: i for i, t in enumerate(bars.index)}
    rows = []

    def replay(cohort, version, key, side, entry, stop, tick, entry_time, exit_time, exit_price, exit_reason):
        e, x = loc.get(pd.Timestamp(entry_time)), loc.get(pd.Timestamp(exit_time))
        if e is None or x is None or x < e or gaps[e:x + 1].any():
            rows.append({"cohort": cohort, "version": version, "trade_key": key, "policy": "-", "parity": "unaligned"})
            return
        for policy in POLICIES:
            out = simulate(policy, side=side, entry=entry, stop0=stop, tick=tick, o=o, h=h, l=l, c=c, atr=atr,
                           e=e, x=x, exit_price=exit_price, exit_reason=str(exit_reason))
            rows.append({"cohort": cohort, "version": version, "trade_key": key, "symbol": symbol, "timeframe_min": minutes,
                         "side": side, "entry_time": entry_time, "exit_time": exit_time, "policy": policy, **out})

    for t in trades.itertuples():
        # The CSV stop is a decimal print of the engine's floor(raw/tick)*tick float; put it
        # back on the same grid or an exact-touch low compares differently (DOGE parity).
        grid_stop = round(float(t.initial_stop) / float(t.tick)) * float(t.tick)
        replay("signal", t.policy, t.trade_key, int(t.side), float(t.entry_price), grid_stop, float(t.tick),
               t.entry_time, t.exit_time, float(t.exit_price), t.exit_reason)
    if not controls.empty and "control_pool" in controls:
        tick = float(trades.tick.iloc[0])
        spec = ExecutionSpec(tick=tick)
        version_of = dict(zip(trades.trade_key, trades.policy))
        for k in controls[controls.matched.astype(bool) & controls.trade_key.isin(version_of)].itertuples():
            version = version_of[k.trade_key]
            # Each target is matched within its own pool: V13 controls follow V13 semantics.
            if k.control_pool != version or not isinstance(k.control_signal_close, str):
                continue
            # Control rows carry frame ordinals; the replay frame is rebuilt from the same
            # warmup start, so ordinals align (checked against target entry_i in tests).
            anchor = int(k.control_anchor_i) if math.isfinite(float(k.control_anchor_i)) else None
            if anchor is None or anchor >= len(o) or not math.isfinite(float(k.control_net_r)):
                continue
            pos = _initial_position_fast(bars.index, o, h, l, c, atr, gaps, anchor, int(k.side), spec)
            if pos is None:
                continue
            stop, entry_i = float(pos["initial_stop"]), anchor + 1
            if version == "retest":
                # Retest: enter at the open after the confirmation close, keep the anchor's
                # absolute stop, recompute R from the new entry.
                # control_decision_time repeats the anchor close; the confirmation bar is
                # control_confirmation_i.
                if not math.isfinite(float(k.control_confirmation_i)):
                    continue
                conf = int(k.control_confirmation_i)
                if conf + 1 >= len(o):
                    continue
                entry_i = conf + 1
            entry = float(o[entry_i])
            if int(k.side) * (entry - stop) <= 0:
                continue
            frac = (int(k.side) * (entry - stop)) / entry
            exit_price = entry * (1 + int(k.side) * (float(k.control_net_r) * frac + 2 * SIDE_COST))
            replay("control", version, k.trade_key, int(k.side), entry, stop, tick, bars.index[entry_i],
                   k.control_exit_time, exit_price, k.control_exit_reason)
    return rows


def _max_drawdown(r: pd.Series) -> float:
    curve = r.cumsum()
    return float((curve.cummax() - curve).max()) if len(curve) else 0.0


KEY = ["cohort", "version", "timeframe_min", "trade_key", "entry_time"]


def reproduced(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep only trades whose incumbent replay reproduces the recorded exit exactly."""
    frame = frame[frame.policy != "-"]
    ok = frame.loc[(frame.policy == "incumbent") & (frame.parity == "ok"), KEY].drop_duplicates()
    return frame.merge(ok, on=KEY, how="inner")


def summarize(frame: pd.DataFrame, split: pd.Timestamp) -> pd.DataFrame:
    frame = reproduced(frame).copy()
    frame["exit_time"] = pd.to_datetime(frame.exit_time, utc=True)
    frame["period"] = np.where(frame.exit_time < split, "select_<2025", "check_>=2025")
    base = frame[frame.policy == "incumbent"].set_index(["cohort", "version", "timeframe_min", "trade_key", "entry_time"]).net_r
    out = []
    for keys, g in frame.groupby(["cohort", "version", "timeframe_min", "period", "policy"]):
        g = g.sort_values("exit_time")
        r = g.net_r
        wins, losses = r[r > 0].sum(), -r[r < 0].sum()
        peaked = g[g.peak_close_r >= 2.0]
        gave = (peaked.peak_close_r - peaked.gross_r)
        turned = g[(g.peak_close_r >= 1.0) & (g.net_r <= 0)]
        diff = (g.set_index(["cohort", "version", "timeframe_min", "trade_key", "entry_time"]).net_r
                - base.reindex(g.set_index(["cohort", "version", "timeframe_min", "trade_key", "entry_time"]).index)).dropna()
        out.append(dict(zip(["cohort", "version", "timeframe_min", "period", "policy"], keys)) | {
            "n": int(len(g)), "win_rate": float((r > 0).mean()), "mean_net_r": float(r.mean()),
            "median_net_r": float(r.median()), "sum_net_r": float(r.sum()),
            "profit_factor": float(wins / losses) if losses > 0 else math.inf,
            "max_drawdown_r": _max_drawdown(r), "big_winner_share_5r": float((r >= 5).mean()),
            "reached_2r": int(len(peaked)), "mean_giveback_r_from_2r_peak": float(gave.mean()) if len(gave) else math.nan,
            "reached_1r_closed_flat_or_loss": float(len(turned) / max(1, (g.peak_close_r >= 1.0).sum())),
            "acted_share": float(g.acted.mean()), "delta_mean_vs_incumbent": float(diff.mean()) if len(diff) else math.nan,
            "delta_sum_vs_incumbent": float(diff.sum()) if len(diff) else math.nan,
        })
    return pd.DataFrame(out)


def sign_flip_p(diff: np.ndarray, seed: int, flips: int = 10000) -> float:
    """Two-sided paired test of mean(policy - incumbent) per trade."""
    diff = diff[np.isfinite(diff) & (diff != 0)]
    if not len(diff):
        return 1.0
    rng = np.random.default_rng(seed)
    observed = abs(diff.mean())
    signs = rng.choice((-1.0, 1.0), size=(flips, len(diff)))
    return float((np.abs((signs * diff).mean(axis=1)) >= observed - 1e-12).mean())


def run(output: Path, *, workers: int, limit: int | None = None) -> None:
    cfg = json.loads(CONFIG.read_text())
    symbols = sorted(p.name.rsplit("_", 1)[0] for p in (SOURCE / "retest_v1" / "streams").iterdir() if p.is_dir())
    symbols = sorted(set(symbols))[:limit] if limit else sorted(set(symbols))
    jobs = [(s, m, cfg) for s in symbols for m in cfg["timeframes"]]
    started, rows = time.perf_counter(), []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(run_stream, job) for job in jobs]):
            rows.extend(future.result())
    frame = pd.DataFrame(rows)
    output.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    split = pd.Timestamp(cfg["select_before"])
    summary = summarize(frame, split)
    tests = []
    for (cohort, version, tf, period), g in reproduced(frame).assign(
            period=lambda d: np.where(pd.to_datetime(d.exit_time, utc=True) < split, "select_<2025", "check_>=2025")
    ).groupby(["cohort", "version", "timeframe_min", "period"]):
        piv = g.pivot_table(index=["trade_key", "entry_time"], columns="policy", values="net_r")
        for policy in POLICIES:
            if policy == "incumbent" or policy not in piv:
                continue
            d = (piv[policy] - piv["incumbent"]).to_numpy(float)
            tests.append({"cohort": cohort, "version": version, "timeframe_min": tf, "period": period, "policy": policy,
                          "sign_flip_p": sign_flip_p(d, cfg["stat_seed"])})
    summary = summary.merge(pd.DataFrame(tests), how="left", on=["cohort", "version", "timeframe_min", "period", "policy"])
    summary.to_csv(output / "summary.csv", index=False)
    parity = frame[(frame.policy == "incumbent")].groupby(["cohort", "parity"]).size().to_dict()
    parity_all = frame[frame.policy == "-"].groupby("cohort").size().to_dict()
    receipt = {"experiment_id": cfg["experiment_id"], "symbols": len(symbols), "jobs": len(jobs),
               "rows": int(len(frame)), "parity": {f"{k[0]}:{k[1]}": int(v) for k, v in parity.items()},
               "unaligned": {k: int(v) for k, v in parity_all.items()},
               "wall_seconds": round(time.perf_counter() - started, 1),
               "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (output / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=EXP / "results_v1")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=None, help="first N symbols (smoke run)")
    args = parser.parse_args()
    run(args.output, workers=args.workers, limit=args.limit)


if __name__ == "__main__":
    main()
