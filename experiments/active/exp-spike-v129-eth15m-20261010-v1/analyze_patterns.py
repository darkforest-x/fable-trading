"""Pattern audit of the SPIKE V12.9 ETH 15m ledgers (owner 2026-10-11: 亏损的单子有什么特点).

The 15m twin of exp-spike-v129-eth5m-20261010-v1/analyze_patterns.py, with the same
rules fixed before any bucketed result was looked at:

* Factors use only information known at the signal close (or at the next-open
  fill for the stop distance); HTF values come from completed HTF bars only.
  Lags are the same clock time as on 5m: ret24 = 96 bars, er7d = 672 bars.
  ``ema_dist`` is the distance to the 15m direction line (completed 1h SMA60),
  the 15m counterpart of the 5m chart's 15m EMA120.
* Continuous factors are cut at the terciles of OKX ordinary trades signalled
  before the config split (2025-10-10); the same thresholds apply to the OKX
  last twelve months and to Binance ETHUSDT 15m 2021-09..2024-01 (the
  exp-spike-manual-system-20260925-v1 baseline ordinary ledger, no overlap).
* A factor is stable only if the high-minus-low (yes-minus-no, block-minus-rest)
  mean net R has the same sign with Welch |t| >= 1 in all three samples.
* Loss streaks are compared with 20000 random orderings of the same outcomes.

Run: PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v129-eth15m-20261010-v1/analyze_patterns.py
Outputs: run_v1/patterns_v1/{factors.csv, stability.csv, streaks.json, anatomy.json, receipt.json}
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as p
from yoyo.evaluation import spike_v129_eth15m as s
from yoyo.evaluation import spike_v129_eth5m as s5

FIVE = Path("experiments/active/exp-spike-v129-eth5m-20261010-v1/analyze_patterns.py")
_spec = importlib.util.spec_from_file_location("five_audit", FIVE)
five = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(five)

OUT = s.EXP / "run_v1" / "patterns_v1"
BINANCE_LEDGER = Path("experiments/active/exp-spike-manual-system-20260925-v1/run_v1/streams/ETHUSDT_15m")
BINANCE_END = pd.Timestamp("2024-02-01T00:00:00Z")
MINUTES, DAY_BARS, WEEK_BARS = 15, 96, 672
SEED = 1011151


def features(trades: pd.DataFrame, facts: dict, bars: pd.DataFrame) -> pd.DataFrame:
    """Signal-time factors for each trade row (``signal_i`` indexes ``facts['frame']``)."""
    f = facts["frame"]
    i = trades.signal_i.to_numpy(int)
    side = trades.side.to_numpy(int)
    close, atr = f.close.to_numpy(float), f.atr.to_numpy(float)
    if not np.allclose(close[i], trades.signal_price.to_numpy(float)):
        raise ValueError("ledger signal_i does not index this frame")
    edge = np.where(side == 1, f.ropeHigh.to_numpy(float)[i], f.ropeLow.to_numpy(float)[i])
    when = pd.DatetimeIndex(pd.to_datetime(trades.signal_close, utc=True))
    out = pd.DataFrame(index=trades.index)
    local = when.tz_convert("Asia/Shanghai")
    out["hour_block"] = (local.hour // 6) * 6
    out["saturday"] = local.dayofweek == 5
    out["atr_pct"] = atr[i] / close[i] * 100
    out["risk_pct"] = trades.initial_risk_frac.to_numpy(float) * 100
    out["rv"] = f.rv.to_numpy(float)[i]
    out["momentum"] = side * (f.md.to_numpy(float)[i] - f.sb.to_numpy(float)[i]) / atr[i]
    out["rope_dist"] = side * (close[i] - edge) / atr[i]
    out["ema_dist"] = side * (close[i] - facts["h1"].sma_60.to_numpy(float)[i]) / atr[i]
    lag = lambda k: np.where(i >= k, close[np.maximum(i - k, 0)], np.nan)
    out["ret24"] = side * (close[i] / lag(DAY_BARS) - 1) * 100
    path = pd.Series(close).diff().abs().rolling(WEEK_BARS).sum().to_numpy()
    out["er7d"] = np.abs(close[i] - lag(WEEK_BARS)) / path[i]
    admitted = np.flatnonzero(facts["v9"])
    pos = np.searchsorted(admitted, i) - 1
    prev_t = np.where(pos >= 0, f.index.asi8[admitted[np.maximum(pos, 0)]], np.nan)
    out["gap_prev_h"] = (f.index.asi8[i] - prev_t) / 3.6e12
    for name, minutes, length in (("h1", 60, 60), ("h4", 240, 60)):
        _, slope = five.htf_trend(bars, when, minutes, length)
        out[f"{name}_aligned"] = np.where(np.isfinite(slope.to_numpy()), side * slope.to_numpy() > 0, np.nan)
    daily, _ = five.htf_trend(bars, when, 1440, 50)
    out["d1_aligned"] = np.where(np.isfinite(daily.to_numpy()), side * (close[i] - daily.to_numpy()) > 0, np.nan)
    order = trades.assign(_t=pd.to_datetime(trades.entry_time, utc=True)).sort_values("_t")
    prev = order.groupby("arm").net_r.shift(1)
    out["prev_loss"] = np.where(prev.reindex(trades.index).notna(), prev.reindex(trades.index) < 0, np.nan)
    return out


def okx_sample(cfg: dict) -> pd.DataFrame:
    base5m = s.okx_base5m(cfg)
    bars, partial = p.complete_bars(base5m, MINUTES)
    if partial:
        raise ValueError("partial 15m buckets")
    facts = p.facts_for(bars, base5m, cfg["asset"], float(cfg["tick"]), MINUTES)
    run = s.EXP / "run_v1"
    trades = pd.read_csv(run / "trades.csv.gz")
    trades = trades.loc[~trades.censored.astype(bool)].reset_index(drop=True)
    ctrl = pd.read_csv(run / "controls.csv.gz").set_index("trade_key")
    trades["excess_bp"] = (trades.net_return - trades.trade_key.map(ctrl.control_net_return)) * 1e4
    trades.loc[~trades.trade_key.map(ctrl.matched).fillna(False).astype(bool), "excess_bp"] = np.nan
    trades["sample"] = np.where(pd.to_datetime(trades.signal_close, utc=True) < pd.Timestamp(cfg["split"]),
                                "okx_earlier", "okx_last12m")
    extra = features(trades, facts, base5m)
    return pd.concat([trades.drop(columns=[c for c in extra if c in trades]), extra], axis=1)


def binance_sample(cfg: dict) -> pd.DataFrame:
    base5m = s.binance_base5m(cfg)
    bars, _ = p.complete_bars(base5m, MINUTES)
    meta = p.source.symbol_meta()["ETHUSDT"]
    facts = p.facts_for(bars, base5m, meta["asset"], float(meta["tick"]), MINUTES)
    trades = pd.read_csv(BINANCE_LEDGER / "trades.csv.gz")
    trades = trades.loc[(trades.policy == "baseline") & (trades.arm == "ordinary") & ~trades.censored.astype(bool)
                        & (pd.to_datetime(trades.signal_close, utc=True) < BINANCE_END)].reset_index(drop=True)
    ctrl = pd.read_csv(BINANCE_LEDGER / "controls.csv.gz")
    ctrl = ctrl.loc[ctrl.policy == "baseline"].set_index("trade_key")
    trades["excess_bp"] = trades.trade_key.map(ctrl.excess_bp)
    trades["sample"] = "binance_2021_2024"
    extra = features(trades, facts, base5m)
    return pd.concat([trades.drop(columns=[c for c in extra if c in trades]), extra], axis=1)


def main() -> None:
    cfg = s.config()
    OUT.mkdir(parents=True, exist_ok=True)
    okx, binance = okx_sample(cfg), binance_sample(cfg)
    data = pd.concat([okx, binance], ignore_index=True)
    early = okx.loc[(okx.arm == "ordinary") & (okx["sample"] == "okx_earlier")]
    cuts = {f: tuple(early[f].quantile([1 / 3, 2 / 3]).tolist()) for f in five.CONTINUOUS}
    table, stable = five.bucket_table(data, cuts), five.stability(data, cuts)
    table.to_csv(OUT / "factors.csv", index=False)
    stable.to_csv(OUT / "stability.csv", index=False)
    rng = np.random.default_rng(SEED)
    streak = {}
    for name, g in (("okx_ordinary", okx.loc[okx.arm == "ordinary"]), ("binance_ordinary_2021_2024", binance)):
        g = g.assign(_t=pd.to_datetime(g.entry_time, utc=True)).sort_values("_t")
        streak[name] = five.streaks(g.net_r.to_numpy(float), rng)
    p.dump(OUT / "streaks.json", s5._clean(streak))
    p.dump(OUT / "anatomy.json", s5._clean(five.anatomy(okx)))
    p.dump(OUT / "receipt.json", {"cuts": cuts, "script_sha256": p.digest(Path(__file__)),
                                  "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                                  "samples": {k: int(v) for k, v in data.loc[data.arm == "ordinary"]["sample"].value_counts().items()},
                                  "files": {f: p.digest(OUT / f) for f in ("factors.csv", "stability.csv", "streaks.json", "anatomy.json")}})
    print(json.dumps({k: int(v) for k, v in data["sample"].value_counts().items()}))
    print(stable.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
