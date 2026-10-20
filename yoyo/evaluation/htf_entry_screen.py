"""Higher-timeframe entry screen: fixed-horizon directional markouts.

Source: owner request 2026-10-20 ("换4H/日线级别帮我筛一下哪些入场有方向收益"),
after the Monday-range reversal entry was shown to carry ~0 fixed-horizon
directional return and its "beats random" evidence came from a control that
inherited a not-yet-known direction
(docs/learnings/same-week-random-controls-inherit-a-direction-not-yet-known.md).
Exit work is only worth doing on an entry whose plain markout pays the cost
several times over, so this module measures that first and nothing else.

Decisions:
- Input: OKX USDT-swap 15m archives (data/kline_deep, 2022-01..2026-07),
  resampled to complete UTC 4H / 1D bins only (16 / 96 source bars).
- Every rule emits a side for bar t from data up to and including bar t's
  close. Entry is bar t's close; markout = side * (close[t+h] / close[t] - 1).
  No stops, no funding, close fills: markouts are not executable PnL.
- Events are de-overlapped per (coin, timeframe, rule, side) at the primary
  horizon, so one move counts once per coin.
- Baselines never inherit a direction from a future setup: "drift" is the
  same coin, same side, every bar of the same period half; "quarter" is the
  same coin, same side, every bar of the same calendar quarter.
- Discovery 2022-01..2024-03, confirmation 2024-04..2026-07. Events whose
  primary window crosses the split are purged.
- Inference: calendar-month block bootstrap pooled across coins (crypto
  co-moves inside a month, so coins are not independent samples).
- The 13 rules, horizons and gates are frozen in the experiment config before
  the first run; failures are reported, not tuned.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

FREQ = {"4h": "4h", "1d": "1D"}
SOURCE_BARS = {"4h": 16, "1d": 96}
PERIOD = {"4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
HORIZONS = {"4h": (6, 18, 42), "1d": (3, 7, 14, 30)}
PRIMARY = {"4h": 18, "1d": 7}
WARMUP = 200
SPLIT = pd.Timestamp("2024-04-01", tz="UTC")
COST_BP = 20.0


def load_bars(path: Path, tf: str) -> pd.DataFrame:
    """Complete UTC bins only; index is the bin start, entry is at bin close."""
    raw = pd.read_csv(path, usecols=["ts", "open", "high", "low", "close", "volume"])
    raw = raw.drop_duplicates("ts").sort_values("ts")
    raw.index = pd.to_datetime(raw.pop("ts"), unit="ms", utc=True)
    g = raw.resample(FREQ[tf], label="left", closed="left", origin="epoch")
    bars = pd.DataFrame({
        "open": g.open.first(), "high": g.high.max(), "low": g.low.min(),
        "close": g.close.last(), "volume": g.volume.sum(), "n": g.close.count(),
    })
    return bars[bars.n == SOURCE_BARS[tf]].drop(columns="n")


def _fresh(cond: pd.Series) -> pd.Series:
    cond = cond.fillna(False).astype(bool)
    return cond & ~cond.shift(1, fill_value=False)


def _side(long: pd.Series, short: pd.Series) -> np.ndarray:
    return long.fillna(False).astype(np.int8).values - short.fillna(False).astype(np.int8).values


def _first_in_week(cond: pd.Series, week: pd.Series) -> pd.Series:
    cond = cond.fillna(False).astype(bool)
    return cond & (cond.astype(int).groupby(week).cumsum() == 1)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df.close.shift(1)
    tr = pd.concat([df.high - df.low, (df.high - prev).abs(), (df.low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rule_dc20(df):
    """Donchian 20: close vs max(high)/min(low) of the 20 bars before t; fresh cross."""
    return _side(_fresh(df.close > df.high.shift(1).rolling(20).max()),
                 _fresh(df.close < df.low.shift(1).rolling(20).min()))


def rule_dc55(df):
    """Donchian 55: close vs max(high)/min(low) of the 55 bars before t; fresh cross."""
    return _side(_fresh(df.close > df.high.shift(1).rolling(55).max()),
                 _fresh(df.close < df.low.shift(1).rolling(55).min()))


def rule_mom20(df):
    """State: sign of close[t] / close[t-20] - 1 (time-series momentum, always in market)."""
    r = df.close / df.close.shift(20) - 1
    return _side(r > 0, r < 0)


def rule_ema20_50(df):
    """Fresh cross of EMA20 over/under EMA50 of close (spans 20/50, bars <= t)."""
    e20 = df.close.ewm(span=20, adjust=False).mean()
    e50 = df.close.ewm(span=50, adjust=False).mean()
    return _side(_fresh(e20 > e50), _fresh(e20 < e50))


def rule_sma200(df):
    """Fresh cross of close over/under SMA200 of close (200 bars <= t)."""
    s = df.close.rolling(200).mean()
    return _side(_fresh(df.close > s), _fresh(df.close < s))


def rule_rsi2(df):
    """Connors reversal: Wilder RSI(2) of close fresh < 10 -> long, fresh > 90 -> short."""
    d = df.close.diff()
    au = d.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    ad = (-d.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    rsi = 100 - 100 / (1 + au / ad)
    return _side(_fresh(rsi < 10), _fresh(rsi > 90))


def rule_shock(df):
    """Continuation after |bar return| > 2.5 x std of the 20 prior bar returns."""
    ret = df.close.pct_change()
    z = ret / ret.shift(1).rolling(20).std()
    return _side(z > 2.5, z < -2.5)


def rule_bb_squeeze(df):
    """Bollinger(20,2) break of a band within 5 bars after width hit its 120-bar 10th pct."""
    mid = df.close.rolling(20).mean()
    sd = df.close.rolling(20).std(ddof=0)
    up, lo = mid + 2 * sd, mid - 2 * sd
    width = (up - lo) / mid
    squeezed = (width <= width.rolling(120).quantile(0.1)).astype(float)
    recent = squeezed.shift(1).rolling(5).max() > 0
    return _side(_fresh(df.close > up) & recent, _fresh(df.close < lo) & recent)


def rule_ma_dense(df):
    """MA-compression launch: |EMA20-EMA60|/ATR14 at its 120-bar 10th pct within 5 bars, then fresh close beyond both."""
    e20 = df.close.ewm(span=20, adjust=False).mean()
    e60 = df.close.ewm(span=60, adjust=False).mean()
    spread = (e20 - e60).abs() / _atr(df)
    dense = (spread <= spread.rolling(120).quantile(0.1)).astype(float)
    recent = dense.shift(1).rolling(5).max() > 0
    hi, lo = np.maximum(e20, e60), np.minimum(e20, e60)
    return _side(_fresh(df.close > hi) & recent, _fresh(df.close < lo) & recent)


def _week(df) -> pd.Series:
    return pd.Series(df.index.floor("D") - pd.to_timedelta(df.index.weekday, unit="D"), index=df.index)


def _bars_per_day(df) -> int:
    return 6 if (df.index[1] - df.index[0]) <= pd.Timedelta(hours=4) else 1


def _monday(df):
    week = _week(df)
    mon = df.index.weekday == 0
    stats = df[mon].groupby(week[mon]).agg(hi=("high", "max"), lo=("low", "min"), n=("close", "count"))
    stats = stats[stats.n == _bars_per_day(df)]
    hi = week.map(stats.hi)
    lo = week.map(stats.lo)
    after = pd.Series(~mon, index=df.index)
    return week, hi, lo, after


def rule_monday_break(df):
    """First close of the week (Tue..Sun) above complete Monday high -> long / below Monday low -> short."""
    week, hi, lo, after = _monday(df)
    return _side(_first_in_week((df.close > hi) & after, week),
                 _first_in_week((df.close < lo) & after, week))


def rule_monday_reclaim(df):
    """'翻身': after a Tue..Sun close below Monday low, first later close back above it -> long (mirror short)."""
    week, hi, lo, after = _monday(df)
    below = ((df.close < lo) & after).astype(int).groupby(week).cummax().groupby(week).shift(1, fill_value=0) > 0
    above = ((df.close > hi) & after).astype(int).groupby(week).cummax().groupby(week).shift(1, fill_value=0) > 0
    return _side(_first_in_week(below & (df.close > lo) & after, week),
                 _first_in_week(above & (df.close < hi) & after, week))


def rule_prev_week_break(df):
    """First close of the week above the previous complete week's high -> long / below its low -> short."""
    week = _week(df)
    wk = df.groupby(week).agg(hi=("high", "max"), lo=("low", "min"), n=("close", "count"))
    wk = wk[wk.n == 7 * _bars_per_day(df)]
    prev_hi = week.map(pd.Series(wk.hi.values, index=wk.index + pd.Timedelta(days=7)))
    prev_lo = week.map(pd.Series(wk.lo.values, index=wk.index + pd.Timedelta(days=7)))
    return _side(_first_in_week(df.close > prev_hi, week), _first_in_week(df.close < prev_lo, week))


def rule_vol_spike(df):
    """Continuation of bar direction when volume > 3 x median volume of the 20 prior bars."""
    spike = df.volume > 3 * df.volume.shift(1).rolling(20).median()
    return _side(spike & (df.close > df.open), spike & (df.close < df.open))


RULES = {
    "dc20": rule_dc20, "dc55": rule_dc55, "mom20": rule_mom20, "ema20_50": rule_ema20_50,
    "sma200": rule_sma200, "rsi2": rule_rsi2, "shock": rule_shock, "bb_squeeze": rule_bb_squeeze,
    "ma_dense": rule_ma_dense, "monday_break": rule_monday_break, "monday_reclaim": rule_monday_reclaim,
    "prev_week_break": rule_prev_week_break, "vol_spike": rule_vol_spike,
}


def forward(df: pd.DataFrame, tf: str, h: int) -> np.ndarray:
    """Long markout in bp from close[t] to close[t+h]; NaN when the window has a gap."""
    c = df.close.values
    t = df.index.values
    out = np.full(len(c), np.nan)
    if len(c) > h:
        ok = (t[h:] - t[:-h]) == np.timedelta64(h * PERIOD[tf])
        out[:-h][ok] = (c[h:][ok] / c[:-h][ok] - 1) * 1e4
    return out


def period_of(entry_close: pd.DatetimeIndex, window_end: pd.DatetimeIndex) -> np.ndarray:
    disc = (entry_close < SPLIT) & (window_end <= SPLIT)
    conf = entry_close >= SPLIT
    return np.where(disc, "disc", np.where(conf, "conf", "purged"))


def deoverlap(pos: np.ndarray, h: int) -> np.ndarray:
    keep, last = [], -10**9
    for p in pos:
        if p >= last + h:
            keep.append(p)
            last = p
    return np.asarray(keep, dtype=int)


def coin_events(coin: str, df: pd.DataFrame, tf: str) -> tuple[list[pd.DataFrame], pd.DataFrame]:
    hp = PRIMARY[tf]
    fwd = {h: forward(df, tf, h) for h in HORIZONS[tf]}
    close_t = df.index + PERIOD[tf]
    period = period_of(close_t, close_t + hp * PERIOD[tf])
    quarter = close_t.to_period("Q").astype(str)
    valid = np.isfinite(fwd[hp]) & (np.arange(len(df)) >= WARMUP)

    base = pd.DataFrame({"period": period, "quarter": quarter, **{f"f{h}": fwd[h] for h in HORIZONS[tf]}})[valid]
    base_period = base.groupby("period")[[f"f{h}" for h in HORIZONS[tf]]].mean()
    base_quarter = base.groupby("quarter")[[f"f{h}" for h in HORIZONS[tf]]].mean()

    frames = []
    for name, fn in RULES.items():
        side_all = fn(df)
        for side in (1, -1):
            pos = deoverlap(np.flatnonzero((side_all == side) & valid), hp)
            if len(pos) == 0:
                continue
            ev = pd.DataFrame({
                "coin": coin, "tf": tf, "rule": name, "side": side,
                "entry_close": close_t[pos], "period": period[pos], "quarter": quarter[pos],
            })
            for h in HORIZONS[tf]:
                m = side * fwd[h][pos]
                ev[f"m{h}"] = m
                ev[f"xd{h}"] = m - side * base_period[f"f{h}"].reindex(period[pos]).values
                ev[f"xq{h}"] = m - side * base_quarter[f"f{h}"].reindex(quarter[pos]).values
            frames.append(ev)
    drift = base_period.assign(coin=coin, tf=tf).reset_index()
    return frames, drift


def month_bootstrap(values: np.ndarray, months: np.ndarray, rng, draws: int = 2000) -> tuple[float, float]:
    ok = np.isfinite(values)
    if ok.sum() < 2:
        return np.nan, np.nan
    s = pd.DataFrame({"m": months[ok], "v": values[ok]}).groupby("m").v.agg(["sum", "count"])
    sums, cnts = s["sum"].values, s["count"].values
    idx = rng.integers(0, len(sums), (draws, len(sums)))
    means = sums[idx].sum(1) / cnts[idx].sum(1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def summarize(events: pd.DataFrame, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for (tf, rule, side, period), g in events[events.period != "purged"].groupby(["tf", "rule", "side", "period"]):
        hp = PRIMARY[tf]
        months = g.entry_close.dt.strftime("%Y-%m").values
        m, xd, xq = g[f"m{hp}"].values, g[f"xd{hp}"].values, g[f"xq{hp}"].values
        net_lo, net_hi = month_bootstrap(m - COST_BP, months, rng)
        xd_lo, xd_hi = month_bootstrap(xd, months, rng)
        per_coin = g.groupby("coin")[f"xd{hp}"].agg(["mean", "count"])
        per_coin = per_coin[per_coin["count"] >= 5]
        row = {
            "tf": tf, "rule": rule, "side": side, "period": period, "horizon_bars": hp,
            "n": len(g), "coins": g.coin.nunique(), "months": len(set(months)),
            "mean_bp": np.nanmean(m), "median_bp": np.nanmedian(m), "net_bp": np.nanmean(m) - COST_BP,
            "net_ci_lo": net_lo, "net_ci_hi": net_hi, "net_win": float(np.mean(m > COST_BP)),
            "x_drift_bp": np.nanmean(xd), "x_drift_ci_lo": xd_lo, "x_drift_ci_hi": xd_hi,
            "x_quarter_bp": np.nanmean(xq),
            "coin_breadth": float((per_coin["mean"] > 0).mean()) if len(per_coin) else np.nan,
        }
        for h in HORIZONS[tf]:
            row[f"mean_bp_h{h}"] = np.nanmean(g[f"m{h}"].values)
            row[f"x_drift_bp_h{h}"] = np.nanmean(g[f"xd{h}"].values)
        rows.append(row)
    return pd.DataFrame(rows)


def gate(summary: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    g = cfg["gates"]
    s = summary.copy()
    s["pass"] = (
        (s.n >= g["min_events"]) & (s.mean_bp >= g["min_mean_bp"])
        & (s.net_ci_lo > 0) & (s.x_drift_ci_lo > 0)
    )
    d = s[s.period == "disc"].set_index(["tf", "rule", "side"])
    c = s[s.period == "conf"].set_index(["tf", "rule", "side"])
    out = d.join(c, lsuffix="_disc", rsuffix="_conf", how="outer")
    out["verdict"] = np.select(
        [out.pass_disc.fillna(False) & out.pass_conf.fillna(False),
         out.pass_disc.fillna(False),
         out.pass_conf.fillna(False)],
        ["both_pass", "disc_only", "conf_only"], default="fail")
    return out.reset_index()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    cfg = json.loads(Path(args.config).read_text())
    out = Path(cfg["output"])
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    frames, drifts, inputs = [], [], []
    for path in sorted(Path(cfg["data_dir"]).glob(cfg["glob"])):
        coin = path.name.split("_")[1]
        for tf in cfg["timeframes"]:
            df = load_bars(path, tf)
            if len(df) < WARMUP + 2 * PRIMARY[tf]:
                continue
            ev, drift = coin_events(coin, df, tf)
            frames += ev
            drifts.append(drift)
            inputs.append({"coin": coin, "tf": tf, "file": str(path), "bars": len(df),
                           "first": str(df.index[0]), "last": str(df.index[-1])})
    events = pd.concat(frames, ignore_index=True)
    events.to_csv(out / "events.csv.gz", index=False)
    pd.concat(drifts, ignore_index=True).to_csv(out / "coin_drift.csv", index=False)
    pd.DataFrame(inputs).to_csv(out / "inputs.csv", index=False)
    summary = summarize(events, cfg["seed"])
    summary.to_csv(out / "summary.csv", index=False)
    verdict = gate(summary, cfg)
    verdict.to_csv(out / "verdict.csv", index=False)
    print(json.dumps({"events": len(events), "coins": events.coin.nunique(),
                      "cells": len(verdict), "seconds": round(time.time() - t0, 1),
                      "verdicts": verdict.verdict.value_counts().to_dict()}))


if __name__ == "__main__":
    main()
