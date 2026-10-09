"""HTF MA reversion v4: wait for a turn after the far touch, stop under the swing extreme (Notion #44).

Owner 2026-10-09 开始, after v1-v3 were rejected and the recommendation was to change the entry: the
owner's sample long (OKX ETHUSDT.P 5m, entry about 2411 near the 2405 low, stop 2396, target 2491) did
not buy the touch - it waited until the drop stopped and put the stop just under the low. v1-v3
bought the touch and lost to excursions that ran 10-31% past the line (ETH, two years). v4 changes
only the entry and the stop; the line, the distance measures and the excursion are v1's:

  excursion  first touch of the trigger; ends at a chart close back within half the trigger
             distance of the line (hr.excursions)
  confirm    'reclaim': a chart-bar close back beyond the trigger (long: close > trigger)
             'prev_bar': a chart-bar close beyond the previous chart bar's high (long; short:
             below its low)
             at or after the touching chart bar and no later than the excursion's last bar
  entry      the next chart bar's open
  stop       the excursion's extreme so far (long: lowest low from the touch through the
             confirming bar) minus 0.1% of it; a fill at or beyond the stop waits for the next
             confirmation
  targets    2R / 3R / 5R, 48h cap, v1 exits for an open entry (stop first on ties, gaps at the open)
  re-entry   arm 0: one trade per excursion; arm 3: after a stop, the next confirmation inside the
             same excursion (extreme recomputed from the touch), at most 3 more
  book       one open trade per side; a confirmation whose entry bar is not after the last exit is
             skipped and the excursion stays eligible for a later one

Controls (20 random open entries, same symbol, chart and month, HTF ATR / price within 0.5-2x, same
side, stop fraction, target and cost), the 0.2% round trip, the split and the summaries are v1's
(htf_ma_reversion_v1). A confirmation reads the touching and confirming chart bars and earlier path
bars only; the entry is the next bar's open. The Vix Fix gate is dropped (no effect in v1-v3).

v5 (owner 2026-10-10 嗯 to "A: 大级别趋势过滤") reuses this builder with a config 'trend' block: the
state is the last completed UTC daily close against its SMA (daily_trend, +1 above / -1 below); arm
'with' keeps confirmations whose entry bar has state == side (longs in uptrends, shorts in
downtrends), arm 'against' the opposite; controls are drawn only from bars with the trade's state.
Without the block the scan is v4's, trade for trade.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import zlib
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import htf_ma_reversion_v1 as hr
from yoyo.evaluation import market_sync_shock as v1
from yoyo.evaluation import market_sync_shock_v2 as v2

EXP = Path("experiments/active/exp-htf-ma-reversion-20261009-v4")


def confirmations(kind: str, side: int, close: np.ndarray, high: np.ndarray, low: np.ndarray,
                  trig: np.ndarray) -> np.ndarray:
    """Chart bars whose close confirms a turn back toward the line."""
    with np.errstate(invalid="ignore"):
        if kind == "reclaim":
            ok = side * (close - trig) > 0
        elif kind == "prev_bar":
            prev = np.r_[np.nan, (high if side > 0 else low)[:-1]]
            ok = side * (close - prev) > 0
        else:
            raise ValueError(kind)
    return ok & np.isfinite(trig)


def chain_confirmed(exc: list[tuple[int, int]], conf_idx: np.ndarray, side: int, bars: tuple, per: int,
                    buffer: float, target_r: float, cap: int, cost: float, max_re: int) -> list[tuple]:
    """Confirmed entries per excursion in time order: (touch bar, entry bar, fill, attempt, extreme, outcome)."""
    o, h, l, c = bars
    out, last_exit = [], -1
    for p0, j_end in exc:
        j_from, attempt = p0 // per, 0
        while attempt <= max_re:
            k = np.searchsorted(conf_idx, max(j_from, last_exit // per))
            if k >= len(conf_idx) or conf_idx[k] > j_end:
                break
            j = int(conf_idx[k])
            e = (j + 1) * per
            if e >= len(o) or not np.isfinite(o[e]):
                break
            ext = float(np.nanmin(l[p0:e]) if side > 0 else np.nanmax(h[p0:e]))
            stop, fill = ext * (1 - side * buffer), float(o[e])
            risk = side * (fill - stop)
            if risk <= 0:  # opened beyond the stop: wait for the next confirmation
                j_from = j + 1
                continue
            sim = hr.simulate(o, h, l, c, np.array([e]), np.array([fill]), np.array([side]), np.array([stop]),
                              np.array([fill + side * target_r * risk]), np.array([False]), cap, cost)
            if not sim["valid"][0]:
                break
            out.append((p0, e, fill, attempt, ext, {k2: v[0] for k2, v in sim.items()}))
            last_exit = int(sim["exit_i"][0])
            if sim["kind"][0] != "stop":
                break
            attempt += 1
            j_from = j + 1
    return out


def daily_trend(raw: pd.DataFrame, path_index: np.ndarray, sma_days: int) -> np.ndarray:
    """+1 / -1 / 0 per path bar: the last completed UTC day's close against the SMA of daily closes.

    Inputs: complete UTC daily buckets of the 5m series (all 288 bars present); a path bar on day D reads
    day D-1 only, so the state is known at its open. Missing days or an unready SMA give 0.
    """
    day_ms = 86_400_000
    daily = v2.resample_hl(raw, 1440)
    if daily.empty:
        return np.zeros(len(path_index), int)
    grid = np.arange(daily.index.min(), daily.index.max() + day_ms, day_ms)
    close = daily.close.reindex(grid)
    sma = close.rolling(sma_days, min_periods=sma_days).mean()
    state = np.sign((close - sma).to_numpy())
    state = np.where(np.isfinite(state), state, 0).astype(int)
    k = (path_index // day_ms * day_ms - day_ms - grid[0]) // day_ms
    out = np.zeros(len(path_index), int)
    ok = (k >= 0) & (k < len(grid))
    out[ok] = state[k[ok]]
    return out


def scan_symbol(args: tuple) -> pd.DataFrame:
    """Confirmed-entry trades with matched controls for one symbol on both charts and the grid."""
    symbol, cfg = args
    raw = hr.read_5m(symbol, pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"]), cfg.get("series_dir"))
    if raw.empty:
        return pd.DataFrame()
    start_ms = pd.Timestamp(cfg["start"]).value // 10**6
    cap, cost, n_ctrl, buffer = cfg["max_hold_bars"], cfg["round_trip_cost"], cfg["controls"]["n"], cfg["stop_buffer"]
    frames = []
    for chart_key, spec in cfg["charts"].items():
        minutes = int(chart_key)
        fr = hr.chart_frames(raw, minutes, spec["htf_minutes"])
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
        trend = cfg.get("trend")
        arms = trend["arms"] if trend else [None]
        if trend:
            tstate = daily_trend(raw, pidx, trend["sma_days"])
            pools_by_state = {s_: {mo: np.flatnonzero(pool_ok & (month == mo) & (tstate == s_))
                                   for mo in np.unique(month[pool_ok])} for s_ in (1, -1)}
        window = int(cfg["distance"]["pctl"]["window_days"] * 1440 // minutes)
        levels = {"pct": cfg["distance"]["pct"]["levels"], "atr": cfg["distance"]["atr"]["levels"][chart_key],
                  "pctl": cfg["distance"]["pctl"]["levels"]}
        for measure, lv in levels.items():
            for level in lv:
                trig = hr.triggers(measure, level, line, atr, cl, ch, window)
                for side in (1, -1):
                    tg = trig[side]
                    dist = side * (line - tg)
                    with np.errstate(invalid="ignore"):
                        back = ~(np.isfinite(tg) & np.isfinite(cc)) | (side * (cc - (line - side * dist / 2)) >= 0)
                        tp = tg[chart_of]
                        touch = tradable & ((l <= tp) if side > 0 else (h >= tp))
                    exc = hr.excursions(touch, back, chart_of, per)
                    if not exc:
                        continue
                    for kind in cfg["confirm"]:
                        conf_all = np.flatnonzero(confirmations(kind, side, cc, ch, cl, tg))
                        for arm in arms:
                            if arm is None:
                                conf_idx, arm_pools = conf_all, pools
                            else:
                                need = side if arm == "with" else -side
                                conf_idx = conf_all[tstate[np.minimum((conf_all + 1) * per, len(o) - 1)] == need]
                                arm_pools = pools_by_state[need]
                            for target_r in cfg["targets_r"]:
                                for max_re in cfg["reentry"]:
                                    got = chain_confirmed(exc, conf_idx, side, bars, per, buffer, target_r, cap, cost,
                                                          max_re)
                                    if not got:
                                        continue
                                    p0s, es, fills, att, ext = (np.array([g[i] for g in got]) for i in range(5))
                                    sim = {k: np.array([g[5][k] for g in got]) for k in got[0][5]}
                                    key = f"{symbol}|{minutes}|{measure}|{level}|{side}|{kind}|{target_r}|re{max_re}"
                                    key += f"|{arm}" if arm else ""
                                    rng = np.random.default_rng(cfg["controls"]["seed"] + zlib.crc32(key.encode()))
                                    ctrl = hr.draw_controls(es, arm_pools, month, atr_pct, rng, n_ctrl)
                                    c_net, c_ok = hr.control_outcome(bars, ctrl, sim["risk_frac"], side, target_r, cap,
                                                                     cost)
                                    cell = {"symbol": symbol, "minutes": minutes, "side": side, "vix": False,
                                            "measure": measure, "level": level, "stop": f"swing{100 * buffer:g}",
                                            "target_r": target_r}
                                    extra = {"conf": kind, "reentry": max_re, "attempt": att, "extreme": ext}
                                    if arm:
                                        extra["trend"] = arm
                                    frames.append(hr.trade_frame(cell, pidx, month, line_p, atr_pct, p0s, es, fills,
                                                                 sim, c_net, c_ok, extra))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def run(config: Path, *, symbols: list[str] | None = None, workers: int = 8) -> None:
    sources = (Path(__file__), config, Path(hr.__file__), Path("tests/evaluation/test_htf_ma_reversion_v4.py"),
               Path(v1.__file__), Path(v2.__file__))
    if symbols is None and not v1._committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(config.read_text())
    output = config.parent / cfg["output_dir"]
    began = time.perf_counter()
    jobs = symbols or cfg["symbols"]
    parts = []
    with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        for k, part in enumerate(pool.map(scan_symbol, [(s, cfg) for s in jobs], chunksize=1)):
            parts.append(part)
            print(json.dumps({"done": k + 1, "symbol": jobs[k], "rows": len(part),
                              "elapsed_s": round(time.perf_counter() - began, 1)}), flush=True)
    trades = pd.concat([p for p in parts if len(p)], ignore_index=True)
    summary = hr.summarize(trades, cfg)
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    summary.to_csv(output / "summary.csv", index=False)
    chosen = hr.select_cells(summary)
    chosen.to_csv(output / "selection.csv", index=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (output / "receipt.json").write_text(json.dumps({
        "experiment_id": cfg["experiment_id"], "config": str(config), "source_commit": head, "symbols": jobs,
        "trade_rows": len(trades), "elapsed_s": round(time.perf_counter() - began, 1),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1))
    pd.set_option("display.width", 250)
    print(chosen.round(3).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    run(args.config, symbols=args.symbols, workers=args.workers)


if __name__ == "__main__":
    main()
