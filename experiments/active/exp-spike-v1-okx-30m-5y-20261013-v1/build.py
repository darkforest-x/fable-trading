"""SPIKE 强劲爆发 V1 on OKX ETHUSDT.P / BTCUSDT.P 30m, 2021-10-13..2026-10-13: ledger, controls, summary.

Owner 2026-10-13: "spike强劲爆发v1 ethusdt.p btcusdt.p 30min级别 5年详细的回测数据跑一下".

Signals are the frozen default (long-only) Pine replay ``yoyo.evaluation.spike_burst_replay`` (SHA pinned
in config). The trade ledger is produced by the executable ledger of the two-year all-market study,
``spike_v1_twoyear_allmarkets._trade_rows`` (next-open entry, the Pine protection line, stop fill at
min(open, stop), 0.2% round trip, no fixed target, open position at the data end = censored), with its
entry window set to this study's [start, end). 30m bars come from complete 1m buckets of OKX's official
monthly archive plus confirmed API tail; incomplete buckets are dropped and V1 runs over the remaining
bar sequence, as TradingView skips bars an exchange did not print.

Matched controls: for each signal, 20 random bars of the same symbol and calendar month whose
ATR/close lies within 0.5-2x the signal bar's, each entered at its next open with the event's initial
stop distance in ATR units. Events and controls then run through one simulator (``trail_exit``: stop
on bar lows, filled at min(open, stop); arm once a close reaches 2R from the actual fill; protection
close - 4 current ATR from the next bar, never lowered). ``excess_r`` compares the event's simulated
result with its controls, so the comparison never mixes the ledger's Pine-reference arming with the
controls' fill-based arming; ``sim_net_r`` vs ``net_r`` is reported to show the two agree.

Parity: the two-year study's OKX ETH/BTC 30m ledger rows in its window are compared trade by trade.

Usage (after fetch.py):
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1-okx-30m-5y-20261013-v1/build.py
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import subprocess
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as v1
from yoyo.evaluation import spike_v1_twoyear_allmarkets as tw
from yoyo.evaluation.spike_burst_replay import SOURCE_SHA256, features, replay

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())
OUT = HERE / "results_v1"
DATA = Path(CFG["data_dir"])
MIN = CFG["timeframe_min"]
STEP_MS = MIN * 60_000
ARM_R, TRAIL_ATR, VOL_BAND = 2.0, 4.0, (0.5, 2.0)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_1m(symbol: str) -> tuple[pd.DataFrame, dict]:
    """Archive + confirmed tail, validated, restricted to opens before config end."""
    archive = Path(CFG["eth_archive"]) if symbol == "ETH_USDT_SWAP" else next(DATA.glob(f"okx_{symbol}_1m_*.csv"))
    tail = next(DATA.glob(f"okx_api_{symbol}_1m_*.csv"))
    parts = [pd.read_csv(p, usecols=["ts", "open", "high", "low", "close", "volume"]) for p in (archive, tail)]
    raw = pd.concat(parts, ignore_index=True)
    end_ms = pd.Timestamp(CFG["end"]).value // 10**6
    raw = raw.loc[raw.ts + 60_000 <= end_ms]
    ok = ((raw[["open", "high", "low", "close"]] > 0).all(axis=1) & (raw.volume >= 0)
          & (raw.high >= raw[["open", "close", "low"]].max(axis=1)) & (raw.low <= raw[["open", "close", "high"]].min(axis=1)))
    dropped = int((~ok).sum())
    raw = raw.loc[ok].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return raw, {"archive": str(archive), "archive_sha256": sha(archive), "tail": str(tail), "tail_sha256": sha(tail),
                 "rows_1m": len(raw), "invalid_1m_dropped": dropped,
                 "first_1m": pd.Timestamp(int(raw.ts.iloc[0]), unit="ms", tz="UTC").isoformat(),
                 "last_1m": pd.Timestamp(int(raw.ts.iloc[-1]), unit="ms", tz="UTC").isoformat()}


def bars_30m(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Complete 30m buckets on the epoch grid; incomplete or absent buckets are counted and skipped."""
    bucket = raw.ts.to_numpy() // STEP_MS * STEP_MS
    g = raw.assign(bucket=bucket).groupby("bucket", sort=True)
    bars = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                 volume=("volume", "sum"), n=("ts", "size"))
    grid = np.arange(bars.index.min(), bars.index.max() + STEP_MS, STEP_MS)
    incomplete = int((bars.n < MIN).sum())
    bars = bars.loc[bars.n == MIN].drop(columns="n")
    bars.index = pd.to_datetime(bars.index, unit="ms", utc=True)
    return bars, {"bars_30m": len(bars), "grid_buckets": len(grid), "incomplete_buckets_dropped": incomplete,
                  "absent_buckets": int(len(grid) - len(bars) - incomplete),
                  "first_bar": bars.index[0].isoformat(), "last_bar": bars.index[-1].isoformat()}


@contextlib.contextmanager
def entry_window(start: pd.Timestamp, end: pd.Timestamp):
    """_trade_rows reads its module's START/END; set them to this study's window for the call only."""
    saved = tw.START, tw.END
    tw.START, tw.END = start, end
    try:
        yield
    finally:
        tw.START, tw.END = saved


def trail_exit(o, h, l, c, atr, entry_i: int, stop0: float, cost: float) -> dict:
    """One long position from the open of ``entry_i``; see the module docstring for the rule."""
    entry = o[entry_i]
    risk = entry - stop0
    if not (np.isfinite(entry) and np.isfinite(stop0) and risk > 0):
        return {"sim_net_r": np.nan, "sim_exit_i": -1, "sim_reason": "invalid"}
    prot, armed = stop0, False
    for j in range(entry_i, len(o)):
        if l[j] <= prot:
            px = min(o[j], prot)
            net = px / entry - 1 - cost
            return {"sim_net_r": net / (risk / entry), "sim_exit_i": j, "sim_reason": "stop"}
        if not armed and c[j] >= entry + ARM_R * risk:
            armed = True
        if armed and np.isfinite(atr[j]):
            prot = max(prot, c[j] - TRAIL_ATR * atr[j])
    return {"sim_net_r": np.nan, "sim_exit_i": -1, "sim_reason": "censored"}


def run_symbol(symbol: str) -> dict:
    meta = CFG["symbols"][symbol]
    raw, data_receipt = load_1m(symbol)
    bars, bar_receipt = bars_30m(raw)
    start, end = pd.Timestamp(CFG["start"]), pd.Timestamp(CFG["end"])
    item = {"venue": CFG["venue"], "symbol": symbol.replace("_", "-"), "asset": meta["asset"], "tick": meta["tick"]}
    with entry_window(start, end):
        ledger = pd.DataFrame(tw._trade_rows(item, bars, MIN))
    f = features(bars)
    rp = replay(f, meta["tick"])
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    atr = f.atr.to_numpy(float)
    atr_pct = atr / c
    pos = bars.index.get_indexer(pd.to_datetime(ledger.signal_bar_open, utc=True))
    ledger["initial_stop"] = rp.initial_stop.to_numpy(float)[pos]
    implied = ledger.entry_price * (1 - ledger.risk_fraction_at_entry)
    ok = np.isfinite(implied)
    assert np.allclose(implied[ok], ledger.initial_stop[ok], rtol=1e-12, atol=1e-9), "ledger stop != replay stop"
    ledger["signal_atr"] = atr[pos]
    ledger["risk_atr"] = (ledger.entry_price - ledger.initial_stop) / ledger.signal_atr
    ledger["gross_r"] = ledger.gross_return / ledger.risk_fraction_at_entry
    month = bars.index.strftime("%Y-%m").to_numpy()
    in_window = (bars.index >= start) & (bars.index < end)
    ready = np.isfinite(atr) & in_window
    ready[len(bars) - 1:] = False
    rng = np.random.default_rng(CFG["controls"]["seed"] + zlib.crc32(symbol.encode()))
    n_ctrl, cost = CFG["controls"]["n"], CFG["round_trip_cost"]
    sims, ctrl_rows = [], []
    for k, p in enumerate(pos):
        ev = trail_exit(o, h, l, c, atr, p + 1, float(ledger.initial_stop.iloc[k]), cost)
        pool = np.flatnonzero(ready & (month == month[p]))
        pool = pool[(pool != p) & (atr_pct[pool] >= VOL_BAND[0] * atr_pct[p]) & (atr_pct[pool] <= VOL_BAND[1] * atr_pct[p])]
        picks = rng.choice(pool, size=n_ctrl) if len(pool) else np.empty(0, int)
        vals = []
        for q in picks:
            stop_q = o[q + 1] - float(ledger.risk_atr.iloc[k]) * atr[q]
            got = trail_exit(o, h, l, c, atr, q + 1, stop_q, cost)
            vals.append(got["sim_net_r"])
            ctrl_rows.append({"event_id": ledger.event_id.iloc[k], "control_signal_bar_open": bars.index[q].isoformat(),
                              "control_net_r": got["sim_net_r"], "control_exit": got["sim_reason"]})
        vals = np.asarray(vals, float)
        sims.append({"sim_net_r": ev["sim_net_r"], "sim_reason": ev["sim_reason"],
                     "control_net_r": float(np.nanmean(vals)) if np.isfinite(vals).any() else np.nan,
                     "controls_valid": int(np.isfinite(vals).sum())})
    ledger = pd.concat([ledger, pd.DataFrame(sims)], axis=1)
    ledger["excess_r"] = ledger.sim_net_r - ledger.control_net_r
    ledger["mfe_r"] = ledger.mfe_return / ledger.risk_fraction_at_entry
    # protective stops split by whether the Pine protection had been raised above the initial stop
    ap = rp.active_protection.to_numpy(float)
    exit_pos = bars.index.get_indexer(pd.to_datetime(ledger.exit_time, utc=True) - pd.Timedelta(minutes=MIN))
    raised = [j >= 0 and np.isfinite(ap[j]) and ap[j] > s + 1e-12 for j, s in zip(exit_pos, ledger.initial_stop)]
    ledger["exit_kind"] = np.where(ledger.exit_reason.eq("protective_stop"),
                                   np.where(raised, "trailing_stop", "initial_stop"), ledger.exit_reason)
    bars = bars.assign(rope_high=f.ropeHigh.to_numpy(float), rope_low=f.ropeLow.to_numpy(float), protection=ap)
    return {"symbol": symbol, "ledger": ledger, "controls": pd.DataFrame(ctrl_rows), "bars": bars,
            "receipt": {**data_receipt, **bar_receipt, "signals_total": int(rp.burst.sum()),
                        "signals_in_window": int(rp.burst[(rp.index >= start) & (rp.index < end)].sum())}}


def parity(ledgers: dict[str, pd.DataFrame]) -> dict:
    """Trade-by-trade match against the two-year study's OKX 30m rows in its window."""
    lo, hi = (pd.Timestamp(x) for x in CFG["parity"]["window"])
    ref = pd.read_csv(CFG["parity"]["ledger"])
    ref = ref.loc[ref.venue.eq("okx") & ref.timeframe_min.eq(MIN) & ref.symbol.isin(["ETH-USDT-SWAP", "BTC-USDT-SWAP"])].copy()
    out = {}
    keys = ["signal_bar_open", "entry_time", "entry_price", "exit_time", "exit_price", "exit_reason", "net_r"]
    for symbol, mine in ledgers.items():
        name = symbol.replace("_", "-")
        a = ref.loc[ref.symbol.eq(name)].copy()
        b = mine.loc[(pd.to_datetime(mine.entry_time, utc=True) >= lo) & (pd.to_datetime(mine.entry_time, utc=True) < hi)].copy()
        for df in (a, b):
            for col in ("signal_bar_open", "entry_time", "exit_time"):
                df[col] = pd.to_datetime(df[col], utc=True)
        m = a[keys].merge(b[keys], on="signal_bar_open", how="outer", suffixes=("_ref", "_new"), indicator=True)
        same = m["_merge"].eq("both")
        exact = same.copy()
        for col in keys[1:]:
            x, y = m[f"{col}_ref"], m[f"{col}_new"]
            if col in ("entry_price", "exit_price", "net_r"):
                exact &= np.isclose(x.astype(float), y.astype(float), rtol=1e-9, atol=1e-9, equal_nan=True)
            else:
                exact &= x.astype(str).eq(y.astype(str))
        out[symbol] = {"reference_trades": len(a), "new_trades_in_window": len(b), "same_signal": int(same.sum()),
                       "exact": int(exact.sum()), "only_reference": m.loc[m._merge.eq("left_only"), "signal_bar_open"].astype(str).tolist(),
                       "only_new": m.loc[m._merge.eq("right_only"), "signal_bar_open"].astype(str).tolist(),
                       "differing": m.loc[same & ~exact, ["signal_bar_open", *[f"{k}_{s}" for k in keys[1:] for s in ("ref", "new")]]]
                                     .astype(str).to_dict("records")}
    return out


def stats(r: np.ndarray) -> dict:
    r = np.asarray(r, float)
    r = r[np.isfinite(r)]
    if not len(r):
        return {"trades": 0}
    cum = np.cumsum(r)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    streak = best = 0
    for v in r:
        streak = streak + 1 if v < 0 else 0
        best = max(best, streak)
    eq = np.cumprod(1 + CFG["equity_risk_per_trade"] * r)
    epk = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    return {"trades": int(len(r)), "win_rate": round(float((r > 0).mean() * 100), 1), "avg_r": round(float(r.mean()), 3),
            "sum_r": round(float(r.sum()), 2), "pf": round(float(pos / neg), 2) if neg > 0 else None,
            "max_dd_r": round(float((cum - peak).min()), 2), "max_losing_streak": int(best),
            "avg_win": round(float(r[r > 0].mean()), 2) if (r > 0).any() else None,
            "avg_loss": round(float(r[r <= 0].mean()), 2) if (r <= 0).any() else None,
            "equity_1pct_ret": round(float((eq[-1] - 1) * 100), 1), "equity_1pct_max_dd": round(float((eq / epk - 1).min() * 100), 1)}


def block(df: pd.DataFrame) -> dict:
    df = df.loc[~df.censored.astype(bool)].sort_values(["exit_time", "entry_time"])
    out = stats(df.net_r.to_numpy())
    ok = np.isfinite(df.excess_r.to_numpy(float))
    x = df.excess_r.to_numpy(float)[ok]
    out.update({"sim_avg_r": round(float(np.nanmean(df.sim_net_r)), 3) if len(df) else None,
                "control_avg_r": round(float(df.control_net_r[ok].mean()), 3) if ok.any() else None,
                "excess_r": round(float(x.mean()), 3) if ok.any() else None,
                "p_excess": v1.month_block_sign_flip(x, pd.to_datetime(df.signal_bar_open, utc=True).dt.strftime("%Y-%m").to_numpy()[ok],
                                                     CFG["stat_seed"], CFG["flips"]) if ok.any() else None,
                "cost_r": round(float((df.gross_r - df.net_r).mean()), 3) if len(df) else None,
                "median_risk_pct": round(float(df.risk_fraction_at_entry.median() * 100), 2) if len(df) else None,
                "median_hold_h": round(float(df.holding_minutes.median() / 60), 1) if len(df) else None})
    return out


def main():
    sources = [Path(__file__), HERE / "config.json"]
    if not v1._committed(sources):
        raise SystemExit("commit build.py and config.json before generating results")
    OUT.mkdir(exist_ok=True)
    split = pd.Timestamp(CFG["split"])
    cases, ledgers = {}, {}
    for symbol in CFG["symbols"]:
        res = run_symbol(symbol)
        led = res["ledger"]
        led["period"] = np.where(pd.to_datetime(led.signal_close_time, utc=True) < split, "before", "after")
        tag = symbol.split("_")[0].lower()
        led.to_csv(OUT / f"{tag}_trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        res["controls"].to_csv(OUT / f"{tag}_controls.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        b = res["bars"].reset_index(names="t")
        b.insert(0, "ts", b.t.astype("int64") // 10**6)
        b.drop(columns="t").to_csv(OUT / f"{tag}_bars_30m.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        ledgers[symbol] = led
        closed = led.loc[~led.censored.astype(bool)]
        cases[symbol] = {"receipt": res["receipt"], "full": block(led), "before": block(led[led.period == "before"]),
                         "after": block(led[led.period == "after"]), "censored_open": int(led.censored.astype(bool).sum()),
                         "exit_reasons": closed.exit_reason.value_counts().to_dict(),
                         "sim_vs_ledger_max_abs_diff_r": round(float((closed.sim_net_r - closed.net_r).abs().max()), 4) if len(closed) else None}
        print(symbol, json.dumps(cases[symbol]["full"], ensure_ascii=False), flush=True)
    both = pd.concat(ledgers.values(), ignore_index=True)
    combined = {"full": block(both), "before": block(both[both.period == "before"]), "after": block(both[both.period == "after"])}
    summary = {"generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
               "git_head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
               "replay_source_sha256": SOURCE_SHA256, "config": CFG, "parity": parity(ledgers), "cases": cases, "combined": combined}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
    print("combined", json.dumps(combined, ensure_ascii=False))
    print("parity", json.dumps(summary["parity"], ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
