"""Market-wide synchronized shock study (owner Notion task 2026-10-07, "整个市场的异动").

Owner question: when BTC and ETH lead a sudden same-direction move on expanded
volume and most alts move with them in the same bar (owner reference: the
2026-08-19 22:00-23:00 Beijing broad rally), is there a regularity in what
follows, and on which timeframe is it strongest?

Inputs: the frozen 638-symbol Binance USD-M 5m series of
exp-spike-v128-entry-clock-long-20260923-v1 (2022-11-01..2026-09-23, delisted
symbols included). 5m is the finest timeframe available for the whole market;
3m would need 1m data for every symbol, which is not on disk.

Universe: per calendar month, the top ``universe_size`` alts by median daily
quote volume over the previous calendar month. The ranking window closes before
the month it ranks (docs/learnings/symbol-ranking-window-must-end-before-the-trading-window.md).

Features at bar t read bars <= t only (columns: open, close, volume):
  ret_t    = close_t / open_t - 1
  z_t      = ret_t / RMS(ret_{t-L..t-1})          (L = ``lookback`` bars)
  vr_t     = volume_t / median(volume_{t-L..t-1})
  breadth  = share of month-universe alts with a valid bar t whose ret_t has the
             shock sign
  engulf   = BTC body_t covers body_{t-1} and the colours differ (descriptive)
Event: BTC and ETH same sign, both |z| >= z_min and vr >= v_min; "sync" adds
breadth >= b_min, "lead" requires breadth < 0.5 (alts have not followed yet).
After an event the next ``cooldown`` bars cannot start another one, so events
on one timeframe never overlap within the longest horizon.

Outcome (label, future allowed): enter at open_{t+1} in the shock direction,
exit at close_{t+h}; instruments BTC, ETH and an equal-weight basket of the
month-universe alts with a finite entry open and exit close; net of the project
0.2% round-trip cost. Reversal is the negated gross minus the same cost.

Controls: per event, ``controls`` random bars from the same calendar month and
the same tercile of BTC trailing volatility (RMS of the previous L BTC returns,
terciles within the month), same direction, same horizon and cost. Paired diff
= event net - mean control net. Month-block sign-flip p on the paired diffs.
Selection uses events before ``select_before`` only; later events are reported.
Per timeframe the selected cell is the (config, kind, horizon, direction) of the
alt basket with the largest paired-diff t on selection events (>= ``min_events``);
the same cell is then read on the check period. Everything else is descriptive.
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

EXP = Path("experiments/active/exp-market-sync-shock-20261007-v1")
CONFIG = EXP / "config.json"
SERIES = Path("data/research/spike_v128_entry_clock_long_20260923/series")
LEADERS = ("BTCUSDT", "ETHUSDT")
STABLE = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "USDPUSDT", "DAIUSDT", "EURUSDT", "AEURUSDT",
          "USDEUSDT", "XUSDUSDT", "BFUSDUSDT", "USD1USDT", "RLUSDUSDT", "PAXGUSDT", "XAUTUSDT"}


def read_5m(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    raw = pd.read_csv(SERIES / f"{symbol}.csv.gz", usecols=["ts", "open", "high", "low", "close", "volume"])
    raw = raw.loc[(raw.ts >= start.value // 10**6) & (raw.ts + 300_000 <= end.value // 10**6)]
    ok = ((raw[["open", "high", "low", "close"]] > 0).all(axis=1) & (raw.volume >= 0)
          & (raw.high >= raw[["open", "close", "low"]].max(axis=1)) & (raw.low <= raw[["open", "close", "high"]].min(axis=1)))
    return raw.loc[ok].drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def resample(raw: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Complete bars only: every 5m slot of a bucket must be present and valid."""
    step = minutes * 60_000
    bucket = raw.ts.to_numpy() // step * step
    g = raw.assign(bucket=bucket).groupby("bucket", sort=True)
    bars = g.agg(open=("open", "first"), close=("close", "last"), volume=("volume", "sum"), n=("ts", "size"))
    bars = bars.loc[bars.n == minutes // 5].drop(columns="n")
    return bars


def monthly_universe(symbols: list[str], start: pd.Timestamp, end: pd.Timestamp, size: int) -> dict[str, list[str]]:
    """Top-``size`` alts for month M from median daily quote volume in month M-1."""
    daily = {}
    for symbol in symbols:
        if symbol in LEADERS or symbol in STABLE:
            continue
        raw = read_5m(symbol, start - pd.DateOffset(months=1), end)
        if raw.empty:
            continue
        day = pd.to_datetime(raw.ts.to_numpy() // 86_400_000 * 86_400_000, unit="ms")
        daily[symbol] = (raw.close * raw.volume).groupby(day).sum()
    table = pd.DataFrame(daily)
    medians = table.groupby(table.index.to_period("M")).median()
    out = {}
    for month in pd.period_range(start.tz_convert(None).to_period("M"), end.tz_convert(None).to_period("M"), freq="M"):
        prior = month - 1
        if prior not in medians.index:
            continue
        ranked = medians.loc[prior].dropna().sort_values(ascending=False)
        out[str(month)] = list(ranked.index[:size])
    return out


def causal_features(bars: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """z and volume ratio from the previous ``lookback`` bars only (shift(1) before rolling)."""
    ret = bars.close / bars.open - 1.0
    rms = np.sqrt((ret ** 2).shift(1).rolling(lookback, min_periods=lookback).mean())
    vol_med = bars.volume.shift(1).rolling(lookback, min_periods=lookback).median()
    return pd.DataFrame({"ret": ret, "z": ret / rms, "vr": bars.volume / vol_med, "rms": rms}, index=bars.index)


def detect(btc: pd.DataFrame, eth: pd.DataFrame, breadth: pd.Series, *, z_min: float, v_min: float,
           b_min: float, kind: str, cooldown: int) -> pd.DataFrame:
    """Return event rows (bar position, direction). Inputs share one bar index."""
    side = np.sign(btc.ret.to_numpy())
    same = side == np.sign(eth.ret.to_numpy())
    strong = (np.abs(btc.z.to_numpy()) >= z_min) & (np.abs(eth.z.to_numpy()) >= z_min)
    volume = (btc.vr.to_numpy() >= v_min) & (eth.vr.to_numpy() >= v_min)
    b = breadth.to_numpy()
    if kind == "sync":
        wide = b >= b_min
    elif kind == "lead":
        wide = b < 0.5
    else:
        raise ValueError(kind)
    hit = same & (side != 0) & strong & volume & wide & np.isfinite(b)
    rows, block_until = [], -1
    for i in np.flatnonzero(hit):
        if i <= block_until:
            continue
        rows.append((int(i), int(side[i])))
        block_until = i + cooldown
    return pd.DataFrame(rows, columns=["i", "side"])


def engulf(bars: pd.DataFrame) -> np.ndarray:
    o, c = bars.open.to_numpy(), bars.close.to_numpy()
    po, pc = np.roll(o, 1), np.roll(c, 1)
    out = (np.sign(c - o) != np.sign(pc - po)) & (np.maximum(o, c) >= np.maximum(po, pc)) & (np.minimum(o, c) <= np.minimum(po, pc))
    out[0] = False
    return out


def trade_returns(opens: np.ndarray, closes: np.ndarray, i: np.ndarray, h: int, cols_mask: np.ndarray | None = None) -> np.ndarray:
    """Gross long return from open[i+1] to close[i+h]; NaN when the window leaves the data.

    ``opens``/``closes`` are (bars, symbols); a column must be finite at both ends.
    With ``cols_mask`` (events x symbols) the result is the equal-weight mean over allowed columns.
    """
    n = opens.shape[0]
    ok = i + h < n
    out = np.full((len(i),) + opens.shape[1:], np.nan)
    a, b = i[ok] + 1, i[ok] + h
    out[ok] = closes[b] / opens[a] - 1.0
    if cols_mask is None:
        return out
    out = np.where(cols_mask, out, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # an event with no valid alt is NaN, not an error
        return np.nanmean(out, axis=1)


def month_block_sign_flip(diff: np.ndarray, months: np.ndarray, seed: int, flips: int) -> float:
    keep = np.isfinite(diff)
    diff, months = diff[keep], months[keep]
    if not len(diff):
        return math.nan
    labels, inverse = np.unique(months, return_inverse=True)
    sums = np.bincount(inverse, weights=diff, minlength=len(labels))
    observed = abs(sums.sum())
    rng = np.random.default_rng(seed)
    signs = rng.choice((-1.0, 1.0), size=(flips, len(labels)))
    return float((np.abs((signs * sums).sum(axis=1)) >= observed - 1e-12).mean())


def _committed(paths) -> bool:
    out = subprocess.run(["git", "status", "--porcelain", "--", *map(str, paths)], capture_output=True, text=True)
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", *map(str, paths)], capture_output=True, text=True)
    return out.returncode == 0 and not out.stdout.strip() and tracked.returncode == 0


class Panel:
    """Aligned bar matrices for one timeframe, indexed on BTC's complete bars."""

    def __init__(self, raw: dict[str, pd.DataFrame], minutes: int, lookback: int, universe: dict[str, list[str]]):
        btc = resample(raw["BTCUSDT"], minutes)
        index = btc.index.to_numpy()
        self.minutes, self.index = minutes, index
        self.times = pd.to_datetime(index, unit="ms", utc=True)
        self.symbols = sorted(raw)
        k = len(self.symbols)
        self.open = np.full((len(index), k), np.nan, dtype=np.float32)
        self.close = np.full_like(self.open, np.nan)
        self.ret = np.full_like(self.open, np.nan)
        self.feat = {}
        for j, symbol in enumerate(self.symbols):
            bars = resample(raw[symbol], minutes).reindex(index)
            self.open[:, j], self.close[:, j] = bars.open.to_numpy(), bars.close.to_numpy()
            self.ret[:, j] = (bars.close / bars.open - 1.0).to_numpy()
            if symbol in LEADERS:
                self.feat[symbol] = causal_features(bars, lookback)
        months = self.times.tz_convert(None).to_period("M").astype(str).to_numpy()
        self.month = months
        col = {s: j for j, s in enumerate(self.symbols)}
        self.alt_mask = np.zeros((len(index), k), dtype=bool)
        for month in np.unique(months):
            rows = months == month
            cols = [col[s] for s in universe.get(month, []) if s in col]
            if cols:
                self.alt_mask[np.ix_(rows, cols)] = True
        sign_up = np.where(self.alt_mask, np.sign(self.ret), np.nan)
        valid = self.alt_mask & np.isfinite(self.ret)
        n_valid = valid.sum(axis=1)
        self.breadth_up = np.where(n_valid >= 10, (sign_up > 0).sum(axis=1) / np.maximum(n_valid, 1), np.nan)
        self.breadth_down = np.where(n_valid >= 10, (sign_up < 0).sum(axis=1) / np.maximum(n_valid, 1), np.nan)
        rms = self.feat["BTCUSDT"].rms.to_numpy()
        self.vol_tercile = np.full(len(index), -1, dtype=int)
        for month in np.unique(months):
            rows = np.flatnonzero((months == month) & np.isfinite(rms))
            if len(rows) < 30:
                continue
            q = np.quantile(rms[rows], [1 / 3, 2 / 3])
            self.vol_tercile[rows] = np.searchsorted(q, rms[rows], side="right")
        self.leader_col = {s: col[s] for s in LEADERS}
        self.engulf = engulf(pd.DataFrame({"open": self.open[:, col["BTCUSDT"]], "close": self.close[:, col["BTCUSDT"]]}))

    def breadth(self, side: np.ndarray) -> np.ndarray:
        return np.where(side > 0, self.breadth_up, np.where(side < 0, self.breadth_down, np.nan))

    def outcome(self, i: np.ndarray, side: np.ndarray, h: int) -> dict[str, np.ndarray]:
        out = {}
        for name, s in (("btc", "BTCUSDT"), ("eth", "ETHUSDT")):
            j = self.leader_col[s]
            out[name] = side * trade_returns(self.open[:, [j]], self.close[:, [j]], i, h)[:, 0]
        n = len(self.index)
        ok = i + h < n
        mask = np.zeros((len(i), len(self.symbols)), dtype=bool)
        mask[ok] = self.alt_mask[i[ok]]
        out["alts"] = side * trade_returns(self.open, self.close, i, h, mask)
        return out


def run_timeframe(panel: Panel, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Events x configs x horizons with paired controls."""
    btc, eth = panel.feat["BTCUSDT"], panel.feat["ETHUSDT"]
    side_all = np.sign(btc.ret.to_numpy())
    breadth = pd.Series(panel.breadth(side_all), index=btc.index)
    start = pd.Timestamp(cfg["start"])
    in_window = panel.times >= start
    rng = np.random.default_rng(cfg["control_seed"] + panel.minutes)
    pool = {}
    for month in np.unique(panel.month):
        for t in range(3):
            rows = np.flatnonzero((panel.month == month) & (panel.vol_tercile == t) & in_window)
            pool[(month, t)] = rows
    horizons = cfg["horizons"]
    cost = cfg["round_trip_cost"]
    event_rows, trade_rows = [], []
    cells = [(c, kind) for kind in cfg["kinds"] for c in cfg["grid"]
             if kind == "sync" or c["b_min"] == cfg["grid"][0]["b_min"]]
    for config, kind in cells:
        events = detect(btc, eth, breadth, z_min=config["z_min"], v_min=config["v_min"], b_min=config["b_min"],
                        kind=kind, cooldown=cfg["cooldown"])
        events = events.loc[in_window[events.i.to_numpy()]] if len(events) else events
        if events.empty:
            continue
        i = events.i.to_numpy()
        side = events.side.to_numpy().astype(float)
        key = f"z{config['z_min']}_v{config['v_min']}_" + (f"b{config['b_min']}_sync" if kind == "sync" else "lead")
        for e, s in zip(i, side):
            event_rows.append({"config": key, "kind": kind, "minutes": panel.minutes, "time": panel.times[e].isoformat(),
                               "side": int(s), "btc_z": float(btc.z.iat[e]), "eth_z": float(eth.z.iat[e]),
                               "btc_vr": float(btc.vr.iat[e]), "eth_vr": float(eth.vr.iat[e]),
                               "breadth": float(breadth.iat[e]), "btc_engulf": bool(panel.engulf[e])})
        controls = []
        for e in i:
            cand = pool.get((panel.month[e], panel.vol_tercile[e]), np.empty(0, dtype=int))
            cand = cand[cand != e]
            controls.append(rng.choice(cand, size=cfg["controls"], replace=True) if len(cand) else np.full(cfg["controls"], -1))
        controls = np.asarray(controls)
        for h in horizons:
            got = panel.outcome(i, side, h)
            flat = controls.ravel()
            okc = flat >= 0
            csides = np.repeat(side, cfg["controls"])
            cont = {name: np.full(len(flat), np.nan) for name in got}
            if okc.any():
                cgot = panel.outcome(flat[okc], csides[okc], h)
                for name in got:
                    cont[name][okc] = cgot[name]
            for name, gross in got.items():
                cmean = np.nanmean(cont[name].reshape(len(i), -1), axis=1) if len(i) else np.empty(0)
                for k in range(len(i)):
                    trade_rows.append({"config": key, "kind": kind, "minutes": panel.minutes, "h": h, "instrument": name,
                                       "time": panel.times[i[k]].isoformat(), "month": panel.month[i[k]],
                                       "side": int(side[k]), "engulf": bool(panel.engulf[i[k]]),
                                       "gross": gross[k], "net": gross[k] - cost, "rev_net": -gross[k] - cost,
                                       "control_net": cmean[k] - cost, "control_rev_net": -cmean[k] - cost})
    return pd.DataFrame(event_rows), pd.DataFrame(trade_rows)


def summarize(trades: pd.DataFrame, split: pd.Timestamp, seed: int, flips: int) -> pd.DataFrame:
    rows = []
    trades = trades.assign(period=np.where(pd.to_datetime(trades.time) < split, "select", "check"))
    for (period, config, kind, minutes, h, instrument), g in trades.groupby(
            ["period", "config", "kind", "minutes", "h", "instrument"], sort=True):
        g = g.loc[np.isfinite(g.net) & np.isfinite(g.control_net)]
        if g.empty:
            continue
        for direction, col, ccol in (("continue", "net", "control_net"), ("reverse", "rev_net", "control_rev_net")):
            diff = (g[col] - g[ccol]).to_numpy()
            rows.append({"period": period, "config": config, "kind": kind, "minutes": minutes, "h": h,
                         "instrument": instrument, "direction": direction, "events": len(g),
                         "months": g.month.nunique(), "mean_net_bp": 1e4 * g[col].mean(),
                         "median_net_bp": 1e4 * g[col].median(), "win_rate": float((g[col] > 0).mean()),
                         "control_net_bp": 1e4 * g[ccol].mean(), "paired_diff_bp": 1e4 * diff.mean(),
                         "t_diff": float(diff.mean() / (diff.std(ddof=1) / math.sqrt(len(diff)))) if len(diff) > 2 and diff.std(ddof=1) > 0 else math.nan,
                         "sign_flip_p": month_block_sign_flip(diff, g.month.to_numpy(), seed, flips)})
    return pd.DataFrame(rows)


def select_and_check(summary: pd.DataFrame, min_events: int) -> pd.DataFrame:
    """Pick one alt-basket cell per timeframe on the selection period; read it on the check period."""
    alts = summary.loc[summary.instrument.eq("alts")]
    pick = alts.loc[alts.period.eq("select") & alts.events.ge(min_events) & np.isfinite(alts.t_diff)]
    keys = ["config", "kind", "minutes", "h", "instrument", "direction"]
    rows = []
    for minutes, g in pick.groupby("minutes"):
        best = g.sort_values("t_diff", ascending=False).iloc[0]
        check = alts.loc[alts.period.eq("check")].merge(best[keys].to_frame().T, on=keys)
        rows.append({**{f"select_{k}": v for k, v in best.items()},
                     **{f"check_{k}": v for k, v in (check.iloc[0].items() if len(check) else [])}})
    return pd.DataFrame(rows)


def run(output: Path, *, limit: int | None = None) -> None:
    sources = (Path(__file__), CONFIG, Path("tests/evaluation/test_market_sync_shock.py"))
    if limit is None and not _committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(CONFIG.read_text())
    start, end = pd.Timestamp(cfg["warmup_start"]), pd.Timestamp(cfg["end"])
    symbols = sorted(p.name.removesuffix(".csv.gz") for p in SERIES.glob("*.csv.gz"))
    if limit:
        symbols = sorted(set(symbols[:limit]) | set(LEADERS))
    began = time.perf_counter()
    universe = monthly_universe(symbols, pd.Timestamp(cfg["start"]), end, cfg["universe_size"])
    needed = sorted(set(LEADERS) | {s for names in universe.values() for s in names})
    raw = {s: read_5m(s, start, end) for s in needed}
    output.mkdir(parents=True, exist_ok=True)
    (output / "universe.json").write_text(json.dumps(universe, indent=1))
    events, trades = [], []
    for minutes in cfg["timeframes"]:
        panel = Panel(raw, minutes, cfg["lookback"], universe)
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
    receipt = {"experiment_id": cfg["experiment_id"], "source_commit": head, "symbols_read": len(needed),
               "universe_months": len(universe), "events": len(events), "trade_rows": len(trades),
               "limit": limit, "elapsed_s": round(time.perf_counter() - began, 1),
               "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EXP / "results_v1")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    run(args.output, limit=args.limit)


if __name__ == "__main__":
    main()
