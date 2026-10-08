"""Market-wide sync shock v3: do stronger shocks continue more? (owner 2026-10-08: "继续做 v3 分层吧").

v2 (exp-market-sync-shock-20261008-v2) found that long after a 1H up shock, held 12-24h, is
positive across its threshold grid, and that the strictest grid cell (z3/v3/b0.75) looked
best. A grid cell mixes three thresholds; v3 asks about each one separately, inside one
fixed event set, so the comparison is between events and not between overlapping sets.

Single variable: conditioning. Events, outcomes and paired controls are v2's own rows for
its loosest config (z>=2, volume x2, breadth >=60%, single sync bar, 24h cooldown) on 30m
and 1H. Each event gets a tercile of one strength feature at a time:

  strength  min(|btc_z|, |eth_z|) at the event bar
  volume    min(btc_vr, eth_vr) at the event bar (recomputed here with v1.causal_features on
            the same BTC-indexed bars, because v2 did not store it; btc_z/eth_z recomputed the
            same way must match v2's events.csv or the run stops)
  breadth   share of the month's top-50 alts moving with the shock (v2's events.csv)

Tercile edges come from events before ``select_before`` only and are applied unchanged to
later events. Per stratum: net after 0.2%, paired excess over v2's same-month same-BTC-
volatility-tercile controls, month-block sign-flip. Per feature: top-minus-bottom excess
in each period, with a label-permutation p-value inside that period.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import time
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import market_sync_shock as v1

EXP = Path("experiments/active/exp-market-sync-shock-20261008-v3")
CONFIG = EXP / "config.json"
FEATURES = ("strength", "volume", "breadth")
BIN_NAMES = ("low", "mid", "high")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def leader_features(raw: dict[str, pd.DataFrame], minutes: int, lookback: int) -> pd.DataFrame:
    """z and volume ratio for BTC and ETH on BTC's complete bars, as v1.Panel builds them."""
    btc = v1.resample(raw["BTCUSDT"], minutes)
    eth = v1.resample(raw["ETHUSDT"], minutes).reindex(btc.index)
    fb, fe = v1.causal_features(btc, lookback), v1.causal_features(eth, lookback)
    out = pd.DataFrame({"btc_z": fb.z, "eth_z": fe.z, "btc_vr": fb.vr, "eth_vr": fe.vr}, index=btc.index)
    out.index = pd.to_datetime(out.index, unit="ms", utc=True)
    return out


def attach_features(events: pd.DataFrame, feats: dict[int, pd.DataFrame], tol: float = 1e-6) -> pd.DataFrame:
    """Add volume ratios; stop if the recomputed z differs from v2's stored z."""
    parts = []
    for minutes, g in events.groupby("minutes"):
        f = feats[minutes].reindex(pd.to_datetime(g.time))
        for col in ("btc_z", "eth_z"):
            gap = np.nanmax(np.abs(f[col].to_numpy() - g[col].to_numpy()))
            if not np.isfinite(gap) or gap > tol:
                raise ValueError(f"{minutes}m {col} differs from v2 events.csv by {gap}")
        parts.append(g.assign(btc_vr=f.btc_vr.to_numpy(), eth_vr=f.eth_vr.to_numpy()))
    out = pd.concat(parts).sort_index()
    return out.assign(strength=np.minimum(out.btc_z.abs(), out.eth_z.abs()),
                      volume=np.minimum(out.btc_vr, out.eth_vr))


def frozen_terciles(values: np.ndarray, select: np.ndarray) -> tuple[np.ndarray, list[float]]:
    """Bins 0/1/2 from edges fitted on the selection rows only."""
    edges = list(np.quantile(values[select], [1 / 3, 2 / 3]))
    return np.searchsorted(edges, values, side="right"), edges


def permutation_p(excess: np.ndarray, bins: np.ndarray, rng: np.random.Generator, reps: int) -> float:
    """Two-sided p of mean(excess | high) - mean(excess | low) under shuffled bin labels."""
    keep = np.isfinite(excess) & np.isin(bins, (0, 2))
    x, b = excess[keep], bins[keep]
    if (b == 0).sum() < 3 or (b == 2).sum() < 3:
        return math.nan
    observed = x[b == 2].mean() - x[b == 0].mean()
    hits = 0
    for _ in range(reps):
        s = rng.permutation(b)
        hits += abs(x[s == 2].mean() - x[s == 0].mean()) >= abs(observed) - 1e-15
    return (hits + 1) / (reps + 1)


def stats(g: pd.DataFrame, seed: int, flips: int) -> dict:
    g = g.loc[np.isfinite(g.net) & np.isfinite(g.control_net)]
    if g.empty:
        return {"events": 0}
    diff = (g.net - g.control_net).to_numpy()
    sd = diff.std(ddof=1) if len(diff) > 2 else math.nan
    return {"events": len(g), "months": g.month.nunique(), "mean_net_bp": 1e4 * g.net.mean(),
            "median_net_bp": 1e4 * g.net.median(), "win_rate": float((g.net > 0).mean()),
            "control_net_bp": 1e4 * g.control_net.mean(), "excess_bp": 1e4 * diff.mean(),
            "t_excess": float(diff.mean() / (sd / math.sqrt(len(diff)))) if sd and sd > 0 else math.nan,
            "sign_flip_p": v1.month_block_sign_flip(diff, g.month.to_numpy(), seed, flips)}


def analyse(events: pd.DataFrame, trades: pd.DataFrame, cfg: dict,
            features: tuple[str, ...] = FEATURES) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    split = pd.Timestamp(cfg["select_before"])
    events = events.assign(period=np.where(pd.to_datetime(events.time) < split, "select", "check"))
    edges_rows, binned = [], []
    for (minutes, side), g in events.groupby(["minutes", "side"]):
        g = g.copy()
        select = (g.period == "select").to_numpy()
        for feature in features:
            g[f"{feature}_bin"], edges = frozen_terciles(g[feature].to_numpy(), select)
            edges_rows.append({"minutes": minutes, "side": side, "feature": feature,
                               "edge_low_mid": edges[0], "edge_mid_high": edges[1]})
        binned.append(g)
    events = pd.concat(binned).sort_values(["minutes", "time"]).reset_index(drop=True)
    key = ["minutes", "time", "side"]
    joined = trades.merge(events[key + ["period"] + [f"{f}_bin" for f in features] + list(features)], on=key)
    rng = np.random.default_rng(cfg["stat_seed"])
    strata, contrasts = [], []
    for (minutes, side, exit_, inst), g in joined.groupby(["minutes", "side", "exit", "instrument"]):
        for feature in features:
            for period in ("select", "check"):
                p = g.loc[g.period == period]
                for b, name in enumerate(BIN_NAMES):
                    strata.append({"minutes": minutes, "side": side, "exit": exit_, "instrument": inst,
                                   "feature": feature, "bin": name, "period": period,
                                   **stats(p.loc[p[f"{feature}_bin"] == b], cfg["stat_seed"], cfg["flips"])})
                excess = (p.net - p.control_net).to_numpy()
                bins = p[f"{feature}_bin"].to_numpy()
                hi, lo = excess[(bins == 2) & np.isfinite(excess)], excess[(bins == 0) & np.isfinite(excess)]
                contrasts.append({"minutes": minutes, "side": side, "exit": exit_, "instrument": inst,
                                  "feature": feature, "period": period, "n_high": len(hi), "n_low": len(lo),
                                  "high_minus_low_bp": 1e4 * (hi.mean() - lo.mean()) if len(hi) and len(lo) else math.nan,
                                  "perm_p": permutation_p(excess, bins, rng, cfg["permutations"])})
    return events, pd.DataFrame(strata), pd.DataFrame(contrasts), pd.DataFrame(edges_rows)


def run(output: Path) -> None:
    sources = (Path(__file__), CONFIG, Path("tests/evaluation/test_market_sync_shock_v3.py"), Path(v1.__file__))
    if not v1._committed(sources):
        raise ValueError("commit builder, tests and config before generating results")
    cfg = json.loads(CONFIG.read_text())
    src = Path(cfg["source_results"])
    v2_cfg = json.loads((src.parent / "config.json").read_text())
    began = time.perf_counter()
    sel = cfg["event_set"]
    events = pd.read_csv(src / "events.csv")
    events = events.loc[events.config.eq(sel["config"]) & events.minutes.isin(sel["timeframes"])]
    trades = pd.read_csv(src / "trades.csv.gz")
    trades = trades.loc[trades.config.eq(sel["config"]) & trades.minutes.isin(sel["timeframes"])
                        & trades.exit.isin(cfg["exits"]) & trades.instrument.isin(cfg["instruments"])]
    start, end = pd.Timestamp(v2_cfg["warmup_start"]), pd.Timestamp(v2_cfg["end"])
    raw = {s: v1.read_5m(s, start, end) for s in v1.LEADERS}
    feats = {m: leader_features(raw, m, v2_cfg["lookback"]) for m in sel["timeframes"]}
    events = attach_features(events, feats)
    events, strata, contrasts, edges = analyse(events, trades, cfg)
    output.mkdir(parents=True, exist_ok=True)
    events.to_csv(output / "events_v3.csv", index=False)
    strata.to_csv(output / "strata.csv", index=False)
    contrasts.to_csv(output / "contrasts.csv", index=False)
    edges.to_csv(output / "edges.csv", index=False)
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (output / "receipt.json").write_text(json.dumps({
        "experiment_id": cfg["experiment_id"], "source_commit": head,
        "v2_receipt": json.loads((src / "receipt.json").read_text()),
        "v2_events_sha256": sha256(src / "events.csv"), "v2_trades_sha256": sha256(src / "trades.csv.gz"),
        "events": len(events), "strata_rows": len(strata), "elapsed_s": round(time.perf_counter() - began, 1),
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat()}, indent=1))
    pc = cfg["primary"]
    view = contrasts.loc[(contrasts.minutes == pc["minutes"]) & (contrasts.side == pc["side"])
                         & (contrasts.exit == pc["exit"]) & (contrasts.instrument == pc["instrument"])]
    print(view.to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--output", type=Path, default=EXP / "results_v3")
    run(parser.parse_args().output)


if __name__ == "__main__":
    main()
