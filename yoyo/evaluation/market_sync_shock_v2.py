"""Market-wide sync shock v2 (owner pushback 2026-10-08: "不可能 肯定是哪里逻辑不对 再试试").

v1 (exp-market-sync-shock-20261007-v1) found no tradable average follow-through. Its
arithmetic was re-checked by hand before this study (the 2026-08-19 1H event and 40
random trades match raw data exactly), so v2 revisits design choices v1 made. Each arm
changes one thing against the v1 baseline and is read separately:

  A  direction: up shocks (long) and down shocks (short) are selected and read on their
     own. v1 pooled them; in v1 the 30m/1H up shocks continued in both periods while the
     5m/15m down shocks flipped sign between periods.
  B  holding time in wall clock (1h/4h/12h/24h) for every detection timeframe; v1 held at
     most 12 bars, i.e. one hour after a 5m detection.
  C  exit: a trend exit (initial stop 2ATR from entry, trailing 3ATR from the best close,
     24h cap, close-based) instead of a fixed hold.
  D  event: a cluster of at least two sync bars on the same side within the last three
     bars (owner: "几根成交量放大的k"), firing on the second bar; v1 used one bar.

Unchanged from v1: frozen 638-symbol Binance 5m data, previous-month alt universe, causal
z / volume-ratio / breadth features (bars <= t only), next-open entry, 0.2% round trip,
same-month same-BTC-volatility-tercile random controls with the same side and exit, and
month-block sign-flip; selection on events before ``select_before``, one read after.
Events are independent per timeframe and config: after an event nothing new fires for 24h.

The fixed-hold alt basket is v1's per-alt buy-and-hold from the next open. The trend exit
needs one price path, so its basket is an equal-weight index of the month-universe alts,
rebalanced every bar (gap and open-to-close legs averaged over alts valid on both bars).
Tail metric (descriptive): share of events whose path reaches +3% in the trade direction
at any close within 24h, next to the same share for the controls.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as v1

EXP = Path("experiments/active/exp-market-sync-shock-20261008-v2")
CONFIG = EXP / "config.json"
TAIL = 0.03


def resample_hl(raw: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """v1.resample plus high/low; complete valid buckets only."""
    step = minutes * 60_000
    bucket = raw.ts.to_numpy() // step * step
    g = raw.assign(bucket=bucket).groupby("bucket", sort=True)
    bars = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                 volume=("volume", "sum"), n=("ts", "size"))
    return bars.loc[bars.n == minutes // 5].drop(columns="n")


def atr14(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Wilder ATR(14) at each bar from that bar and earlier."""
    prev = np.roll(close, 1)
    prev[0] = np.nan
    tr = np.nanmax(np.vstack([high - low, np.abs(high - prev), np.abs(low - prev)]), axis=0)
    return pd.Series(tr).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().to_numpy()


def sync_hits(btc: pd.DataFrame, eth: pd.DataFrame, breadth: np.ndarray, *, z_min: float, v_min: float,
              b_min: float) -> np.ndarray:
    """Per-bar side (+1/-1) where v1's sync condition holds, else 0."""
    side = np.sign(btc.ret.to_numpy())
    ok = ((side == np.sign(eth.ret.to_numpy())) & (side != 0)
          & (np.abs(btc.z.to_numpy()) >= z_min) & (np.abs(eth.z.to_numpy()) >= z_min)
          & (btc.vr.to_numpy() >= v_min) & (eth.vr.to_numpy() >= v_min)
          & np.isfinite(breadth) & (breadth >= b_min))
    return np.where(ok, side, 0).astype(int)


def events_from_hits(hits: np.ndarray, kind: str, cooldown: int) -> pd.DataFrame:
    """sync: any hit bar. cluster: a hit bar with a same-side hit in the two bars before it."""
    if kind == "sync":
        fire = hits != 0
    elif kind == "cluster":
        prev1, prev2 = np.roll(hits, 1), np.roll(hits, 2)
        prev1[:1], prev2[:2] = 0, 0
        fire = (hits != 0) & ((prev1 == hits) | (prev2 == hits))
    else:
        raise ValueError(kind)
    rows, block_until = [], -1
    for i in np.flatnonzero(fire):
        if i <= block_until:
            continue
        rows.append((int(i), int(hits[i])))
        block_until = i + cooldown
    return pd.DataFrame(rows, columns=["i", "side"])


def trend_exit(entry: float, closes: np.ndarray, atr: float, side: int) -> tuple[float, float]:
    """Close-based exit: stop 2ATR from entry, trail 3ATR from the best close, path capped by caller.

    Returns (gross return in the trade direction, max favourable close return).
    The stop that applies on bar j was set by closes before j.
    """
    if not (np.isfinite(entry) and entry > 0 and np.isfinite(atr) and atr > 0) or not len(closes):
        return math.nan, math.nan
    c = np.asarray(closes, float)
    missing = ~np.isfinite(c)
    if missing.any():  # a data gap ends the path; the trade is marked at the last good close
        c = c[: np.argmax(missing)]
        if not len(c):
            return math.nan, math.nan
    signed = side * c
    best = np.maximum.accumulate(np.concatenate([[side * entry], signed]))[:-1]
    stop = np.maximum(side * entry - 2 * atr, best - 3 * atr)
    hit = np.flatnonzero(signed <= stop)
    exit_price = c[hit[0]] if len(hit) else c[-1]
    mfe = float(np.max(signed / (side * entry) - 1.0)) if side == 1 else float(np.max(1.0 - c / entry))
    return side * (exit_price / entry - 1.0), mfe


class Panel2(v1.Panel):
    """v1 panel plus high/low/ATR for the leaders and a rebalanced alt index."""

    def __init__(self, raw, minutes, lookback, universe):
        super().__init__(raw, minutes, lookback, universe)
        index = self.index
        self.lead = {}
        for s in v1.LEADERS:
            bars = resample_hl(raw[s], minutes).reindex(index)
            self.lead[s] = {"open": bars.open.to_numpy(), "close": bars.close.to_numpy(),
                            "atr": atr14(bars.high.to_numpy(), bars.low.to_numpy(), bars.close.to_numpy())}
        prev_close = np.vstack([np.full((1, self.close.shape[1]), np.nan), self.close[:-1]])
        both = self.alt_mask & np.isfinite(self.open) & np.isfinite(self.close) & np.isfinite(prev_close)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            gap = np.nanmean(np.where(both, self.open / prev_close - 1.0, np.nan), axis=1)
            oc = np.nanmean(np.where(both, self.close / self.open - 1.0, np.nan), axis=1)
        gap, oc = np.nan_to_num(gap), np.nan_to_num(oc)
        idx_close = np.cumprod((1 + gap) * (1 + oc))
        idx_open = np.concatenate([[1.0], idx_close[:-1]]) * (1 + gap)
        tr = np.abs(np.diff(np.concatenate([[1.0], idx_close])))
        self.lead["ALTS"] = {"open": idx_open, "close": idx_close,
                             "atr": pd.Series(tr).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().to_numpy()}

    def trend(self, name: str, i: np.ndarray, side: np.ndarray, cap: int) -> tuple[np.ndarray, np.ndarray]:
        s = self.lead[name]
        n = len(self.index)
        gross, mfe = np.full(len(i), np.nan), np.full(len(i), np.nan)
        for k, (e, sd) in enumerate(zip(i, side)):
            if e + 1 >= n:
                continue
            path = s["close"][e + 1: min(n, e + 1 + cap)]
            gross[k], mfe[k] = trend_exit(s["open"][e + 1], path, s["atr"][e], int(sd))
        return gross, mfe


def outcomes(panel: Panel2, i: np.ndarray, side: np.ndarray, holds: dict[str, int], cap: int) -> dict:
    out = {}
    for label, h in holds.items():
        got = panel.outcome(i, side, h)
        for name in ("btc", "eth", "alts"):
            out[(label, name)] = (got[name], None)
    for name, key in (("btc", "BTCUSDT"), ("eth", "ETHUSDT"), ("alts", "ALTS")):
        out[("trend", name)] = panel.trend(key, i, side, cap)
    return out


def run_timeframe(panel: Panel2, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    btc, eth = panel.feat["BTCUSDT"], panel.feat["ETHUSDT"]
    breadth = panel.breadth(np.sign(btc.ret.to_numpy()))
    per_hour = 60 // panel.minutes
    holds = {f"time_{h}h": h * per_hour for h in cfg["hold_hours"]}
    cap = cfg["trend_cap_hours"] * per_hour
    cooldown = cfg["cooldown_hours"] * per_hour
    in_window = panel.times >= pd.Timestamp(cfg["start"])
    rng = np.random.default_rng(cfg["control_seed"] + panel.minutes)
    pool = {(m, t): np.flatnonzero((panel.month == m) & (panel.vol_tercile == t) & in_window)
            for m in np.unique(panel.month) for t in range(3)}
    cost = cfg["round_trip_cost"]
    event_rows, trade_rows = [], []
    for config in cfg["grid"]:
        hits = sync_hits(btc, eth, breadth, z_min=config["z_min"], v_min=config["v_min"], b_min=config["b_min"])
        for kind in cfg["kinds"]:
            ev = events_from_hits(hits, kind, cooldown)
            ev = ev.loc[in_window[ev.i.to_numpy()]] if len(ev) else ev
            if ev.empty:
                continue
            i, side = ev.i.to_numpy(), ev.side.to_numpy().astype(float)
            key = f"z{config['z_min']}_v{config['v_min']}_b{config['b_min']}_{kind}"
            for e, s in zip(i, side):
                event_rows.append({"config": key, "kind": kind, "minutes": panel.minutes,
                                   "time": panel.times[e].isoformat(), "side": int(s),
                                   "btc_z": float(btc.z.iat[e]), "eth_z": float(eth.z.iat[e]),
                                   "breadth": float(breadth[e])})
            ctrl = []
            for e in i:
                cand = pool.get((panel.month[e], panel.vol_tercile[e]), np.empty(0, int))
                cand = cand[cand != e]
                ctrl.append(rng.choice(cand, size=cfg["controls"]) if len(cand) else np.full(cfg["controls"], -1))
            ctrl = np.asarray(ctrl)
            flat, cside = ctrl.ravel(), np.repeat(side, cfg["controls"])
            ok = flat >= 0
            ev_out = outcomes(panel, i, side, holds, cap)
            c_out = outcomes(panel, flat[ok], cside[ok], holds, cap)
            for (exit_label, name), (gross, mfe) in ev_out.items():
                cg = np.full(len(flat), np.nan)
                cg[ok] = c_out[(exit_label, name)][0]
                cmean = np.nanmean(cg.reshape(len(i), -1), axis=1) if np.isfinite(cg).any() else np.full(len(i), np.nan)
                ctail = None
                if mfe is not None:
                    cm = np.full(len(flat), np.nan)
                    cm[ok] = c_out[(exit_label, name)][1]
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        ctail = np.nanmean((cm >= TAIL).astype(float).reshape(len(i), -1), axis=1)
                for k in range(len(i)):
                    trade_rows.append({"config": key, "kind": kind, "minutes": panel.minutes, "exit": exit_label,
                                       "instrument": name, "time": panel.times[i[k]].isoformat(),
                                       "month": panel.month[i[k]], "side": int(side[k]), "gross": gross[k],
                                       "net": gross[k] - cost, "control_net": cmean[k] - cost,
                                       "tail_hit": None if mfe is None else float(mfe[k] >= TAIL) if np.isfinite(mfe[k]) else None,
                                       "control_tail_hit": None if ctail is None else ctail[k]})
    return pd.DataFrame(event_rows), pd.DataFrame(trade_rows)


def summarize(trades: pd.DataFrame, split: pd.Timestamp, seed: int, flips: int) -> pd.DataFrame:
    rows = []
    trades = trades.assign(period=np.where(pd.to_datetime(trades.time) < split, "select", "check"))
    keys = ["period", "config", "kind", "minutes", "exit", "instrument", "side"]
    for key, g in trades.groupby(keys, sort=True):
        g = g.loc[np.isfinite(g.net) & np.isfinite(g.control_net)]
        if g.empty:
            continue
        diff = (g.net - g.control_net).to_numpy()
        sd = diff.std(ddof=1) if len(diff) > 2 else math.nan
        rows.append({**dict(zip(keys, key)), "events": len(g), "months": g.month.nunique(),
                     "mean_net_bp": 1e4 * g.net.mean(), "median_net_bp": 1e4 * g.net.median(),
                     "win_rate": float((g.net > 0).mean()), "control_net_bp": 1e4 * g.control_net.mean(),
                     "paired_diff_bp": 1e4 * diff.mean(),
                     "t_diff": float(diff.mean() / (sd / math.sqrt(len(diff)))) if sd and sd > 0 else math.nan,
                     "sign_flip_p": v1.month_block_sign_flip(diff, g.month.to_numpy(), seed, flips),
                     "tail_share": pd.to_numeric(g["tail_hit"], errors="coerce").mean(),
                     "control_tail_share": pd.to_numeric(g["control_tail_hit"], errors="coerce").mean()})
    return pd.DataFrame(rows)


def select_and_check(summary: pd.DataFrame, min_events: int) -> pd.DataFrame:
    """Per timeframe and side: the alt-basket cell with the largest paired t before the split."""
    alts = summary.loc[summary.instrument.eq("alts")]
    keys = ["config", "kind", "minutes", "exit", "instrument", "side"]
    rows = []
    pick = alts.loc[alts.period.eq("select") & alts.events.ge(min_events) & np.isfinite(alts.t_diff)]
    for (minutes, side), g in pick.groupby(["minutes", "side"]):
        best = g.sort_values("t_diff", ascending=False).iloc[0]
        check = alts.loc[alts.period.eq("check")].merge(best[keys].to_frame().T.astype({"minutes": int, "side": int}), on=keys)
        rows.append({**{f"select_{k}": v for k, v in best.items()},
                     **{f"check_{k}": v for k, v in (check.iloc[0].items() if len(check) else [])}})
    return pd.DataFrame(rows)


def run(output: Path, *, limit: int | None = None) -> None:
    sources = (Path(__file__), CONFIG, Path("tests/evaluation/test_market_sync_shock_v2.py"),
               Path(v1.__file__))
    if limit is None and not v1._committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(CONFIG.read_text())
    start, end = pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"])
    symbols = sorted(p.name.removesuffix(".csv.gz") for p in v1.SERIES.glob("*.csv.gz"))
    if limit:
        symbols = sorted(set(symbols[:limit]) | set(v1.LEADERS))
    began = time.perf_counter()
    universe = v1.monthly_universe(symbols, pd.Timestamp(cfg["start"]), end, cfg["universe_size"])
    needed = sorted(set(v1.LEADERS) | {s for names in universe.values() for s in names})
    raw = {s: v1.read_5m(s, start, end) for s in needed}
    output.mkdir(parents=True, exist_ok=True)
    events, trades = [], []
    for minutes in cfg["timeframes"]:
        panel = Panel2(raw, minutes, cfg["lookback"], universe)
        ev, tr = run_timeframe(panel, cfg)
        events.append(ev); trades.append(tr)
        print(json.dumps({"minutes": minutes, "events": len(ev), "trades": len(tr),
                          "elapsed_s": round(time.perf_counter() - began, 1)}), flush=True)
    events, trades = pd.concat(events, ignore_index=True), pd.concat(trades, ignore_index=True)
    summary = summarize(trades, pd.Timestamp(cfg["select_before"]), cfg["stat_seed"], cfg["flips"])
    events.to_csv(output / "events.csv", index=False)
    trades.to_csv(output / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
    summary.to_csv(output / "summary.csv", index=False)
    select_and_check(summary, cfg["min_events"]).to_csv(output / "selected.csv", index=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (output / "receipt.json").write_text(json.dumps({
        "experiment_id": cfg["experiment_id"], "source_commit": head, "symbols_read": len(needed),
        "events": len(events), "trade_rows": len(trades), "limit": limit,
        "elapsed_s": round(time.perf_counter() - began, 1),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EXP / "results_v2")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run(args.output, limit=args.limit)


if __name__ == "__main__":
    main()
