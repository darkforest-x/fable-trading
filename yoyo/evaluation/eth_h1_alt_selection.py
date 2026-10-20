"""ETH confirmed-H1 timing and causal alt selection, fixed sixteen-hour labels.

Features use completed OHLCV through the decision bar: 24h/4h returns, trailing
168h beta/volatility and relative volume, own Stoch checks in the last4 bars.
Only labels use the next open and the following16 complete hourly candles.
Reuses market_sync_shock's fixed-horizon returns and existing clustered
inference, not an additional execution engine. No classifier is trained.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import warnings

import numpy as np
import pandas as pd

from yoyo.evaluation.pepe_range_exits import signals
from yoyo.evaluation.market_sync_shock import trade_returns
from yoyo.evaluation.imacd_startup_research import inference

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-eth-h1-alt-selection-20261020-v1"
OHLC = ["open", "high", "low", "close"]


def dump(path, value):
    def default(x):
        if isinstance(x, (np.integer, np.floating, np.bool_)):
            return x.item()
        return str(x)
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=default) + "\n")


def hourly(raw, minutes):
    """Source ts is OPEN milliseconds. Only complete, consecutive buckets survive."""
    if raw.ts.duplicated().any() or not raw.ts.is_monotonic_increasing:
        raise ValueError("Duplicate or unsorted source timestamps")
    if (raw.ts % (minutes * 60000)).any():
        raise ValueError("Source timestamps off grid")
    a = raw[OHLC + ["volume"]].to_numpy(float)
    if not np.isfinite(a).all() or (a[:, :4] <= 0).any() or (a[:, 4] < 0).any():
        raise ValueError("Nonfinite or nonpositive market data")
    if (raw.high < raw[["open", "close", "low"]].max(axis=1)).any() or (
            raw.low > raw[["open", "close", "high"]].min(axis=1)).any():
        raise ValueError("Invalid OHLC envelope")
    x = raw.copy()
    x.index = pd.to_datetime(x.ts, unit="ms", utc=True)
    g = x.resample("1h")
    out = g.agg({"open": "first", "high": "max", "low": "min",
                 "close": "last", "volume": "sum"})
    return out[g.close.count().eq(60 // minutes)]


def features(frame, eth, lookback=168):
    """No labels: close/high/low/volume windows end at the current completed bar."""
    c = frame.close
    ret = c.pct_change(fill_method=None)
    er = eth.close.pct_change(fill_method=None)
    vol = ret.rolling(lookback).std()
    corr = ret.rolling(lookback).corr(er)
    beta = ret.rolling(lookback).cov(er) / er.rolling(lookback).var()
    r4, r24 = c / c.shift(4) - 1, c / c.shift(24) - 1
    own = signals(frame).eq(1).rolling(4).max().eq(1)
    rv = frame.volume.rolling(4).sum() / (4 * frame.volume.shift(4).rolling(lookback).mean())
    eligible = c.notna().rolling(lookback + 5).sum().eq(lookback + 5) & (
        frame.volume.rolling(lookback).sum() > 0)
    # Volatility ranks use only earlier values, never whole-month quantiles.
    q1 = vol.shift().rolling(720, min_periods=168).quantile(1 / 3)
    q2 = vol.shift().rolling(720, min_periods=168).quantile(2 / 3)
    bucket = (vol > q1).astype(int) + (vol > q2).astype(int)
    bucket = bucket.where(q1.notna(), -1)
    return pd.DataFrame(dict(
        eligible=eligible, vol=vol, bucket=bucket,
        strength24=r24 - (eth.close / eth.close.shift(24) - 1),
        weakness24=-r24, beta168=beta.where(corr >= .3),
        volume_rebound4=(r4 * rv).where(r4 > 0),
        own_bull4=r4.where(own), lowvol168=-vol), index=frame.index)


def outcomes(frame, hours, cost):
    """Enter next open; exit after hours complete bars. Reject any path gap."""
    n = len(frame)
    gross = trade_returns(frame.open.to_numpy(), frame.close.to_numpy(),
                          np.arange(n), hours)
    count = frame.close.notna().rolling(hours).sum().shift(-hours)
    entry = frame.open.shift(-1)
    good = count.eq(hours) & entry.notna()
    gross = pd.Series(gross, index=frame.index).where(good)
    return pd.DataFrame(dict(
        gross_bp=gross * 1e4, net_bp=gross * 1e4 - cost * 1e4,
        mfe_bp=(frame.high.rolling(hours).max().shift(-hours) / entry - 1).where(good) * 1e4,
        mae_bp=(frame.low.rolling(hours).min().shift(-hours) / entry - 1).where(good) * 1e4,
        entry_price=entry, exit_price=frame.close.shift(-hours).where(good)), index=frame.index)


def choose(scores, eligible, k):
    """Stable topK from current facts only; no outcome argument exists."""
    x = scores.where(eligible).dropna().sort_index()
    return list(x.sort_values(ascending=False, kind="stable").head(k).index)


def load_panel(cfg, folder):
    manifest = json.loads((ROOT / cfg["manifest"]).read_text())
    index = pd.date_range(pd.Timestamp(cfg["start"]) - pd.Timedelta(days=45),
                          pd.Timestamp(cfg["end"]) - pd.Timedelta(hours=1), freq="h")
    data, audit = {}, []
    boundary = pd.Timestamp(manifest["end_exclusive"])
    for row in manifest["symbols"]:
        if row["cohort"] != "frozen_pool":
            continue
        symbol = row["symbol"]
        source = Path(row["output_path"])
        raw = pd.read_csv(source, usecols=["ts", *OHLC, "volume"])
        old = hourly(raw, 15)
        tailfiles = list((folder / "tail").glob(f"okx_{symbol}_USDT_SWAP_1H_*.csv"))
        if len(tailfiles) != 1:
            raise ValueError(f"{symbol}: missing or ambiguous completed tail")
        tail = hourly(pd.read_csv(tailfiles[0]), 60)
        overlap = old.index.intersection(tail.index)
        if len(overlap) < 24:
            raise ValueError(f"{symbol}: insufficient source overlap")
        np.testing.assert_allclose(old.loc[overlap, OHLC], tail.loc[overlap, OHLC],
                                   rtol=1e-9, atol=1e-12, err_msg=symbol)
        full = pd.concat([old[old.index < boundary], tail[tail.index >= boundary]])
        if full.index.has_duplicates:
            raise ValueError("Merge duplicated hours")
        data[symbol] = full.reindex(index)
        audit.append(dict(symbol=symbol, source=str(source), tail=str(tailfiles[0]),
                          overlap_hours=len(overlap), first=str(full.index.min()),
                          last_close=str(full.index.max() + pd.Timedelta(hours=1)),
                          observed=int(data[symbol].close.notna().sum())))
    dump(folder / "input_receipt.json", audit)
    return data


def control_series(frame, feat, label, seed):
    """Same coin/month/6h UTC bucket/past-volatility tercile, up to5 random times."""
    n = len(frame)
    times = frame.index + pd.Timedelta(hours=1)
    keys = pd.DataFrame(dict(month=times.strftime("%Y-%m"), tod=times.hour // 6,
                             bucket=feat.bucket.to_numpy(), i=np.arange(n)))
    usable = feat.eligible.to_numpy() & label.net_bp.notna().to_numpy() & (keys.bucket >= 0)
    groups = {key: group.i.to_numpy() for key, group in keys[usable].groupby(["month", "tod", "bucket"])}
    rng = np.random.default_rng(seed)
    result = np.full(n, np.nan)
    # Controls are null outcomes, not a tradable admission decision.
    for row in keys.itertuples():
        if not usable[row.i]:
            continue
        pool = groups.get((row.month, row.tod, row.bucket), np.array([], dtype=int))
        pool = pool[np.abs(pool - row.i) > 16]
        if len(pool):
            sample = rng.choice(pool, min(5, len(pool)), replace=False)
            result[row.i] = label.net_bp.iloc[sample].mean()
    return result


def metrics(rows):
    mature = rows.dropna(subset=["net_bp"])
    months = pd.to_datetime(mature.decision_time, utc=True).dt.strftime("%Y-%m")
    return dict(
        n=len(rows), completed=len(mature), censored=len(rows)-len(mature),
        mean_net_bp=float(mature.net_bp.mean()), median_net_bp=float(mature.net_bp.median()),
        gross_win_percent=float(mature.gross_bp.gt(0).mean()*100),
        net_win_percent=float(mature.net_bp.gt(0).mean()*100),
        mean_mae_bp=float(mature.mae_bp.mean()), worst_mae_bp=float(mature.mae_bp.min()),
        same_time_random_bp=float(mature.basket_bp.mean()),
        same_coin_random_time_bp=float(mature.control_bp.mean()),
        vs_same_time=inference(mature.net_bp-mature.basket_bp, months),
        vs_random_time=inference(mature.net_bp-mature.control_bp, months))


def run(cfg, folder):
    if (folder / "delivery_receipt.json").exists():
        print("Cached completed study retained.")
        return
    for path in [Path(__file__), folder / "config.json", folder / "PROJECT_PLAN.md"]:
        name = str(path.relative_to(ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + name], cwd=ROOT) != path.read_bytes():
            raise RuntimeError("Commit builder and plan before evaluation")
    data = load_panel(cfg, folder)
    index = data["ETH"].index
    cols = sorted(set(data) - {"ETH", "BTC"})
    fs, labels, controls = {}, {}, {}
    for ordinal, symbol in enumerate(sorted(data)):
        fs[symbol] = features(data[symbol], data["ETH"], cfg["lookback_hours"])
        labels[symbol] = outcomes(data[symbol], cfg["hold_hours"], cfg["round_trip_cost"])
        controls[symbol] = control_series(data[symbol], fs[symbol], labels[symbol],
                                          cfg["seed"] + ordinal)
    # Prefix equality proves feature/rank invariance at bounded real timestamps.
    checks = 0
    for symbol in ("ETH", "SOL", "PEPE"):
        for i in np.linspace(1000, len(index)-20, 4, dtype=int):
            prefix = features(data[symbol].iloc[:i+1], data["ETH"].iloc[:i+1])
            pd.testing.assert_series_equal(prefix.iloc[-1], fs[symbol].iloc[i])
            checks += 1
    eligible = pd.DataFrame({s: fs[s].eligible for s in cols})
    signal = signals(data["ETH"]).eq(1) & fs["ETH"].eligible
    decision = index + pd.Timedelta(hours=1)
    positions = np.flatnonzero(signal & (decision >= pd.Timestamp(cfg["start"])))
    events, picks = [], []
    for i in positions:
        t = decision[i]
        allowed = list(eligible.columns[eligible.iloc[i]])
        if len(allowed) < cfg["top_k"]:
            continue
        basket_values = np.array([labels[s].net_bp.iloc[i] for s in allowed])
        basket = float(basket_values.mean())  # NaN preserves missing constituents.
        arms = dict(ETH=["ETH"], all_alts=allowed)
        for rule in cfg["rules"]:
            arms[rule] = choose(pd.Series({s: fs[s][rule].iloc[i] for s in cols}),
                                eligible.iloc[i], cfg["top_k"])
        for arm, members in arms.items():
            denominator = len(members) if arm in ("ETH", "all_alts") else cfg["top_k"]
            sums = {k: 0. for k in ("net_bp", "gross_bp", "mfe_bp", "mae_bp", "control_bp")}
            for s in members:
                value = labels[s].iloc[i].to_dict()
                value["control_bp"] = controls[s][i]
                for key in sums:
                    sums[key] += value[key]/denominator
                picks.append(dict(decision_time=str(t), signal_open=str(index[i]),
                                  exit_time=str(t+pd.Timedelta(hours=cfg["hold_hours"])),
                                  arm=arm, symbol=s, weight=1/denominator,
                                  score=None if arm in ("ETH", "all_alts") else fs[s][arm].iloc[i],
                                  **value))
            events.append(dict(decision_time=str(t), arm=arm, selected=",".join(members),
                               slots=len(members), basket_bp=basket * (
                                   len(members)/denominator if denominator else 0), **sums))
    ev, pk = pd.DataFrame(events), pd.DataFrame(picks)
    ev.to_csv(folder / "events.csv", index=False)
    pk.to_csv(folder / "picks.csv", index=False)
    times = pd.to_datetime(ev.decision_time, utc=True)
    cutoff = pd.Timestamp(cfg["select_before"])
    dev = ev[(times + pd.Timedelta(hours=cfg["hold_hours"]) < cutoff)]
    dev_summary = {r: metrics(dev[dev.arm.eq(r)]) for r in cfg["rules"]}
    winner = max(cfg["rules"], key=lambda r: dev_summary[r]["mean_net_bp"])
    dump(folder / "selection.json", dict(selected=winner, selection=dev_summary,
                                        criterion="2023-2024 mean net basis points only"))
    summary = []
    masks = {
        "selection_2023_2024": times + pd.Timedelta(hours=cfg["hold_hours"]) < cutoff,
        "later_2025_plus": times >= cutoff,
        "2025": times.dt.year.eq(2025), "2026": times.dt.year.eq(2026),
        "october_2026": times.ge("2026-10-01"), "full": times.notna()}
    for period, mask in masks.items():
        for arm in ["ETH", "all_alts", *cfg["rules"]]:
            summary.append(dict(period=period, arm=arm, selected=arm==winner,
                                 **metrics(ev[mask & ev.arm.eq(arm)])))
    dump(folder / "summary.json", summary)
    pd.DataFrame(summary).drop(columns=["vs_same_time", "vs_random_time"]).to_csv(
        folder / "summary.csv", index=False)
    recent = pk[pd.to_datetime(pk.decision_time, utc=True).ge("2026-10-01") &
                pk.arm.eq(winner)].copy()
    recent["decision_bjt"] = pd.to_datetime(recent.decision_time, utc=True).dt.tz_convert("Asia/Shanghai")
    recent.to_csv(folder / "october_picks.csv", index=False)
    dump(folder / "delivery_receipt.json", dict(
        config=cfg, source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        reused_engines=["market_sync_shock.trade_returns", "imacd_startup_research.inference"],
        eligible_pool=cols, prefix_checks=checks, signal_events=len(positions),
        selected_rule=winner, summary=summary, production_eligible=False, training_eligible=False,
        limitations=[
            "Fixed surviving convenience pool, not historical census; no liquidity capacity claim.",
            "Independent overlapping fixed-notional events, not portfolio R or account return.",
            "20bp frozen cost, no funding, spread, latency or extra slippage.",
            "No protective stop; MAE is observed exposure, not a guaranteed bound.",
            "All historical research; chronological rule selection is not blind prospective validation.",
            "Portfolio MAE is the mean of individual adverse extrema, a conservative bound rather than synchronized equity.",
            "Selected missing outcomes stay censored; no hindsight replacement."]))
    print(json.dumps(dict(events=len(positions), selected=winner, prefix_checks=checks)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    args = parser.parse_args()
    run(json.loads(args.config.read_text()), args.config.parent)
