"""SPIKE V12.9 5m/15m on liquid alts: frozen-rule ledgers and the ETH-selected confirmation entry.

Owner 2026-10-11: "5min优化不了吗 还有同时测试一起其他优质山寨币呢", after
exp-spike-v129-eth-confirm-entry-20261011-v1 selected, on OKX ETH before 2025-10-10, a wait-for-
profit confirmation per timeframe (15m K12_m0.5 passed its checks on small samples, 5m K3_m0.5
did not) without a random control. This study takes those two rules unchanged to other coins
and adds the missing control:

* universe: the 20 alts with the highest median daily quote volume on Binance USD-M in
  2022-11/12 (known before the window; stables, BTC and ETH excluded), plus ETHUSDT as a
  reference outside the pooled verdict; frozen Binance 5m series, 2023-01-01 to 2026-09-23;
* ledgers: the V12.9 ordinary serial ledger per symbol and timeframe through the same code path
  as the ETH replays (V9 admission with the completed 15m EMA120 gate on 5m and the completed
  1h SMA60 gate on 15m, next-open entry, 5-bar extreme + 0.2 ATR stop with a 2 ATR minimum,
  close-2R-armed 4 ATR trail, raw opposite exit, 0.2% round trip), matched controls drawn once
  per trade from the same side x UTC week x period x ATR/close stratum (spike_v128_recent);
* confirmation (as the ETH study): bars counted from the original entry bar; skip when the
  original stop prints or the original trade exits first; confirm at the first close >= m R in
  the trade direction within K bars, enter at the next open, keep the original absolute stop and
  recorded exit; new risk from the new entry. The matched control trade of each signal gets the
  same confirmation, so a gain the waiting step gives any entry is not credited to the signal;
* outcome per signal: net R when entered, 0 when skipped (so means compare money per signal).

Pre-registered verdict (config.json) on the pooled alts, per timeframe, for the ETH-selected
candidate: per-signal net R above immediate entry and above the confirmed controls in both
periods (before / from 2025-01-01), with month-block sign-flip p < 0.05 against the controls in
at least one, and mean net R per entered trade above 0 in both. Other grid cells are descriptive.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as v1
from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v128_recent as p

EXP = Path("experiments/active/exp-spike-v129-alts-confirm-20261011-v1")
CONFIG = EXP / "config.json"
TEST = Path("tests/evaluation/test_spike_v129_alts_confirm.py")
COST = 0.002


def config() -> dict:
    cfg = json.loads(CONFIG.read_text())
    if cfg["experiment_id"] != EXP.name or float(cfg["round_trip_cost"]) != COST:
        raise ValueError("invalid config")
    return cfg


def universe(cfg: dict) -> list[str]:
    """Top alts by median daily quote volume in the ranking window, before the study window."""
    lo, hi = pd.Timestamp(cfg["rank_start"]), pd.Timestamp(cfg["rank_end"])
    rows = []
    for path in sorted(v1.SERIES.glob("*.csv.gz")):
        symbol = path.name.removesuffix(".csv.gz")
        if symbol in v1.STABLE or symbol in ("BTCUSDT", "ETHUSDT"):
            continue
        raw = v1.read_5m(symbol, lo, hi)
        if raw.empty:
            continue
        daily = (raw.close * raw.volume).groupby(raw.ts // 86_400_000).sum()
        rows.append((symbol, float(daily.median())))
    rows.sort(key=lambda r: (-r[1], r[0]))
    return [s for s, _ in rows[:int(cfg["universe_size"])]]


def base5m(symbol: str, cfg: dict) -> pd.DataFrame:
    raw = v1.read_5m(symbol, pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"]))
    return raw.set_index(pd.to_datetime(raw.ts, unit="ms", utc=True))[["open", "high", "low", "close", "volume"]]


def facts_for(base: pd.DataFrame, asset: str, tick: float, minutes: int) -> dict:
    bars, _ = p.complete_bars(base, minutes)
    if minutes == 5:
        return low.pine_facts(bars, base, asset, tick, 5)
    return p.facts_for(bars, base, asset, tick, minutes)


def _confirm(O, H, L, C, side, a, x, e0, stop, exit_px, exit_reason, K, m):
    """(net R, confirmation entry) of the delayed entry, or (nan, nan) when skipped."""
    risk0 = side * (e0 - stop)
    if not np.isfinite(risk0) or risk0 <= 0:
        return np.nan, np.nan
    for k in range(K):
        j = a + k
        if j >= x or j + 1 >= len(O):
            return np.nan, np.nan
        touched = (L[j] <= stop) if side == 1 else (H[j] >= stop)
        if touched:
            return np.nan, np.nan
        if side * (C[j] - e0) >= m * risk0:
            e1 = O[j + 1]
            risk1 = side * (e1 - stop)
            if risk1 <= 0 or x < j + 1 or (x == j + 1 and exit_reason == "opposite_v6_next_open"):
                return np.nan, np.nan
            gross = side * (exit_px / e1 - 1.0)
            return (gross - COST) / (risk1 / e1), e1
    return np.nan, np.nan


def scan(args: tuple) -> dict[str, pd.DataFrame]:
    """Ledger, controls and confirmation outcomes for one symbol on both timeframes."""
    symbol, cfg = args
    meta = p.source.symbol_meta()[symbol]
    asset, tick = str(meta["asset"]), float(meta["tick"])
    base = base5m(symbol, cfg)
    start, end = pd.Timestamp(cfg["start"]), pd.Timestamp(cfg["end"])
    grid = [(K, m) for K in cfg["candidates"]["K_bars"] for m in cfg["candidates"]["m_r"]]
    out_trades, out_signals = [], []
    for minutes in cfg["timeframes"]:
        facts = facts_for(base, asset, tick, minutes)
        frame = facts["frame"]
        identity = {"venue": "binance", "symbol": symbol, "asset": asset, "timeframe_min": minutes}
        key = f"binance:{symbol}:{minutes}m"
        prepared = p.source.prepared_arm(frame, facts["gap"], facts["side"], key, identity, minutes, tick)
        events = []
        for i in np.flatnonzero(facts["v9"]):
            close_time = frame.index[i] + pd.Timedelta(minutes=minutes)
            if close_time >= end:
                continue
            events.append({**identity, "arm": "ordinary", "signal_i": int(i), "side": int(facts["side"][i]),
                           "signal_bar_open": frame.index[i], "signal_close": close_time,
                           "in_window": bool(close_time >= start), "signal_price": float(frame.close.iloc[i]),
                           "signal_atr_pct": float(frame.atr.iloc[i] / frame.close.iloc[i])})
        trades, _ = p.serial(prepared, events, "ordinary", key)
        if not trades:
            continue
        controls = p.matched_controls(prepared, trades, cfg).set_index("trade_key")
        O, H, L, C = prepared.open, prepared.high, prepared.low, prepared.close
        table = pd.DataFrame(trades)
        table = table.loc[~table.censored.astype(bool)].reset_index(drop=True)
        cache: dict[tuple[int, int], dict | None] = {}
        for t in table.itertuples():
            ctrl = controls.loc[t.trade_key]
            crow = None
            if bool(ctrl.matched):
                ck = (int(ctrl.control_sig), int(t.side))
                if ck not in cache:
                    status, res = p.attempt(prepared, *ck)
                    cache[ck] = res if status == "closed" else None
                crow = cache[ck]
            row = {"symbol": symbol, "minutes": minutes, "trade_key": t.trade_key, "side": int(t.side),
                   "signal_close": pd.Timestamp(t.signal_close).isoformat(), "net_r": float(t.net_r),
                   "gross_r": float(t.gross_r), "net_return": float(t.net_return), "risk_frac": float(t.initial_risk_frac),
                   "mfe_known_r": float(t.mfe_known_r), "exit_reason": t.exit_reason,
                   "control_net_r": float(crow["net_r"]) if crow is not None else np.nan}
            for K, m in grid:
                name = f"K{K}_m{m:g}"
                row[f"sig_{name}"] = _confirm(O, H, L, C, int(t.side), int(t.entry_i), int(t.exit_i), float(t.entry_price),
                                              float(t.initial_stop), float(t.exit_price), t.exit_reason, K, m)[0]
                row[f"ctl_{name}"] = (np.nan if crow is None else
                                      _confirm(O, H, L, C, int(t.side), int(crow["entry_i"]), int(crow["exit_i"]),
                                               float(crow["entry_price"]), float(crow["initial_stop"]),
                                               float(crow["exit_price"]), crow["exit_reason"], K, m)[0])
            out_signals.append(row)
        out_trades.append(table.assign(symbol=symbol))
    return {"signals": pd.DataFrame(out_signals),
            "trades": pd.concat(out_trades, ignore_index=True) if out_trades else pd.DataFrame()}


def per_signal(s: pd.DataFrame, name: str) -> pd.DataFrame:
    """Per-signal outcomes with skips as 0R, on signals whose control is available."""
    s = s.loc[np.isfinite(s.control_net_r)].copy()
    s["immediate"] = s.net_r
    s["confirmed"] = s[f"sig_{name}"].fillna(0.0)
    s["control_confirmed"] = s[f"ctl_{name}"].fillna(0.0)
    s["entered"] = np.isfinite(s[f"sig_{name}"])
    s["entered_r"] = s[f"sig_{name}"]
    return s


def summarize(signals: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    split = pd.Timestamp(cfg["split"])
    signals = signals.assign(period=np.where(pd.to_datetime(signals.signal_close) < split, "before_2025", "from_2025"),
                             month=pd.to_datetime(signals.signal_close).dt.strftime("%Y-%m"))
    names = [f"K{K}_m{m:g}" for K in cfg["candidates"]["K_bars"] for m in cfg["candidates"]["m_r"]]
    pooled, per_coin = [], []
    for scope, data in (("alts", signals.loc[signals.symbol != "ETHUSDT"]), ("ETHUSDT", signals.loc[signals.symbol == "ETHUSDT"])):
        for (minutes, period), g in data.groupby(["minutes", "period"]):
            for name in names:
                s = per_signal(g, name)
                d_imm = (s.confirmed - s.immediate).to_numpy()
                d_ctl = (s.confirmed - s.control_confirmed).to_numpy()
                pooled.append({"scope": scope, "minutes": minutes, "period": period, "candidate": name,
                               "signals": len(s), "entries": int(s.entered.sum()), "kept": float(s.entered.mean()),
                               "immediate_per_signal": s.immediate.mean(), "confirmed_per_signal": s.confirmed.mean(),
                               "control_confirmed_per_signal": s.control_confirmed.mean(),
                               "control_immediate_per_signal": s.control_net_r.mean(),
                               "confirmed_per_entry": s.entered_r.mean(), "win_rate_entry": float((s.entered_r > 0).sum() / max(1, s.entered.sum())),
                               "vs_immediate": d_imm.mean(), "p_vs_immediate": v1.month_block_sign_flip(d_imm, s.month.to_numpy(), cfg["stat_seed"], cfg["flips"]),
                               "vs_control": d_ctl.mean(), "p_vs_control": v1.month_block_sign_flip(d_ctl, s.month.to_numpy(), cfg["stat_seed"], cfg["flips"])})
    for (symbol, minutes, period), g in signals.groupby(["symbol", "minutes", "period"]):
        s = per_signal(g, cfg["selected"][str(minutes)])
        per_coin.append({"symbol": symbol, "minutes": minutes, "period": period, "signals": len(g),
                         "win_rate": float((g.net_r > 0).mean()), "sum_net_r": g.net_r.sum(), "mean_net_r": g.net_r.mean(),
                         "mean_gross_r": g.gross_r.mean(), "median_risk_pct": 100 * g.risk_frac.median(),
                         "control_mean_net_r": g.control_net_r.mean(),
                         "confirmed_entries": int(s.entered.sum()), "confirmed_sum_r": s.confirmed.sum(),
                         "confirmed_per_entry": s.entered_r.mean(), "control_confirmed_sum_r": s.control_confirmed.sum()})
    return pd.DataFrame(pooled), pd.DataFrame(per_coin)


def verdict(pooled: pd.DataFrame, cfg: dict) -> list[dict]:
    out = []
    for minutes in cfg["timeframes"]:
        name = cfg["selected"][str(minutes)]
        g = pooled.loc[(pooled.scope == "alts") & (pooled.minutes == minutes) & (pooled.candidate == name)].set_index("period")
        ok = (all(g.loc[p_, "vs_immediate"] > 0 and g.loc[p_, "vs_control"] > 0 and g.loc[p_, "confirmed_per_entry"] > 0
                  for p_ in ("before_2025", "from_2025"))
              and any(g.loc[p_, "p_vs_control"] < 0.05 for p_ in ("before_2025", "from_2025")))
        out.append({"minutes": minutes, "candidate": name, "passed": bool(ok),
                    **{f"{k}_{p_}": float(g.loc[p_, k]) for p_ in g.index
                       for k in ("immediate_per_signal", "confirmed_per_signal", "control_confirmed_per_signal",
                                 "confirmed_per_entry", "vs_control", "p_vs_control", "entries")}})
    return out


def run(output: Path, *, limit: int | None, workers: int) -> None:
    paths = (Path(__file__), CONFIG, TEST)
    if limit is None and not v1._committed(paths):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = config()
    began = time.perf_counter()
    symbols = universe(cfg)
    if symbols != cfg["expected_universe"]:
        raise ValueError(f"universe drift: {symbols}")
    jobs = ["ETHUSDT", *symbols][: (limit + 1) if limit else None]
    parts = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for k, part in enumerate(pool.map(scan, [(s, cfg) for s in jobs], chunksize=1)):
            parts.append(part)
            print(json.dumps({"done": k + 1, "of": len(jobs), "symbol": jobs[k], "signals": len(part["signals"]),
                              "elapsed_s": round(time.perf_counter() - began, 1)}), flush=True)
    signals = pd.concat([x["signals"] for x in parts if len(x["signals"])], ignore_index=True)
    trades = pd.concat([x["trades"] for x in parts if len(x["trades"])], ignore_index=True)
    output.mkdir(parents=True, exist_ok=True)
    signals.to_csv(output / "signals.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    trades.to_csv(output / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    pooled, per_coin = summarize(signals, cfg)
    pooled.to_csv(output / "pooled.csv", index=False)
    per_coin.to_csv(output / "per_coin.csv", index=False)
    result = verdict(pooled, cfg)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (output / "receipt.json").write_text(json.dumps({"experiment_id": cfg["experiment_id"], "source_commit": head,
                                                     "limit": limit, "symbols": jobs, "signals": len(signals),
                                                     "verdict": result, "elapsed_s": round(time.perf_counter() - began, 1),
                                                     "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1))
    print(json.dumps(result, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=EXP / "results_v1")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--universe-only", action="store_true")
    args = parser.parse_args()
    if args.universe_only:
        print(json.dumps(universe(config())))
        return
    run(args.output, limit=args.limit, workers=args.workers)


if __name__ == "__main__":
    main()
