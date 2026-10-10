"""Pattern audit of the SPIKE V12.9 ETH 5m ledgers (owner 2026-10-10: 详细分析规律).

Descriptive, post-hoc analysis of existing closed trades; nothing is selected or
changed. Rules fixed here before any bucketed result was looked at:

* Factors use only information known at the signal close (or at the next-open
  fill for the stop distance). HTF trend values come from completed HTF bars
  only (``confirmed_sma`` at the signal close timestamp).
* Continuous factors are cut at the terciles of OKX ordinary trades signalled
  before the config split (2025-10-10); the same thresholds are applied to the
  OKX last twelve months and to Binance ETHUSDT 2021-09-25..2024-01-31 (the
  exp-spike-manual-system-20260925-v1 baseline ledger, same rules, no overlap).
* A factor is called stable only if high-minus-low (or yes-minus-no, or
  block-minus-rest) mean net R has the same sign in all three samples and a
  Welch |t| >= 1 in that direction in each. 18 factors are tested; under the
  null roughly 2 x 0.16^3 per factor pass, so about one false pass in eight runs.
* Loss streaks are compared with 20000 random orderings of the same outcomes.

Run: .venv/bin/python experiments/active/exp-spike-v129-eth5m-20261010-v1/analyze_patterns.py
Outputs: run_v1/patterns_v1/{factors.csv, stability.csv, streaks.json, anatomy.json}
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v128_recent as p
from yoyo.evaluation import spike_v129_eth5m as s
from yoyo.evaluation.spike_v9_htf_sma import confirmed_sma

OUT = s.EXP / "run_v1" / "patterns_v1"
BINANCE_DATA = Path("data/research/spike_manual_system_20260925/ETHUSDT.csv.gz")
BINANCE_LEDGER = Path("experiments/active/exp-spike-manual-system-20260925-v1/run_v1/streams/ETHUSDT_5m")
BINANCE_END = pd.Timestamp("2024-02-01T00:00:00Z")
CONTINUOUS = ["atr_pct", "risk_pct", "rv", "momentum", "rope_dist", "ema_dist", "ret24", "er7d", "gap_prev_h"]
BINARY = ["h1_aligned", "h4_aligned", "d1_aligned", "prev_loss"]
SEED = 1010131


def htf_trend(bars: pd.DataFrame, when: pd.DatetimeIndex, minutes: int, length: int) -> tuple[pd.Series, pd.Series]:
    """Latest completed HTF SMA and its one-HTF-bar slope, both known at ``when``."""
    clock = pd.DatetimeIndex(sorted(set(when)))
    now = confirmed_sma(bars, clock, minutes, (length,))[f"sma_{length}"]
    before = confirmed_sma(bars, clock - pd.Timedelta(minutes=minutes), minutes, (length,))[f"sma_{length}"]
    slope = pd.Series(now.to_numpy() - before.to_numpy(), index=clock)
    return now.reindex(when), slope.reindex(when)


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
    out["ema_dist"] = side * (close[i] - np.asarray(facts["ma"], float)[i]) / atr[i]
    lag = lambda k: np.where(i >= k, close[np.maximum(i - k, 0)], np.nan)
    out["ret24"] = side * (close[i] / lag(288) - 1) * 100
    path = pd.Series(close).diff().abs().rolling(2016).sum().to_numpy()
    out["er7d"] = np.abs(close[i] - lag(2016)) / path[i]
    admitted = np.flatnonzero(facts["v9"])
    pos = np.searchsorted(admitted, i) - 1
    prev_t = np.where(pos >= 0, f.index.asi8[admitted[np.maximum(pos, 0)]], np.nan)
    out["gap_prev_h"] = (f.index.asi8[i] - prev_t) / 3.6e12
    for name, minutes, length in (("h1", 60, 60), ("h4", 240, 60)):
        _, slope = htf_trend(bars, when, minutes, length)
        out[f"{name}_aligned"] = np.where(np.isfinite(slope.to_numpy()), side * slope.to_numpy() > 0, np.nan)
    daily, _ = htf_trend(bars, when, 1440, 50)
    out["d1_aligned"] = np.where(np.isfinite(daily.to_numpy()), side * (close[i] - daily.to_numpy()) > 0, np.nan)
    order = trades.assign(_t=pd.to_datetime(trades.entry_time, utc=True)).sort_values("_t")
    prev = order.groupby("arm").net_r.shift(1)
    out["prev_loss"] = np.where(prev.reindex(trades.index).notna(), prev.reindex(trades.index) < 0, np.nan)
    return out


def okx_sample() -> pd.DataFrame:
    cfg = s.config()
    data = json.loads((Path(cfg["data_dir"]) / "receipt.json").read_text())
    base = low.load_base([Path(x) for x in data["inputs"]], pd.Timestamp(cfg["end"]))
    bars, _ = low.complete_bars(base, 5)
    facts = low.pine_facts(bars, bars, cfg["asset"], float(cfg["tick"]), 5)
    run = s.EXP / "run_v1"
    trades = pd.read_csv(run / "trades.csv.gz")
    trades = trades.loc[~trades.censored.astype(bool)].reset_index(drop=True)
    ctrl = pd.read_csv(run / "controls.csv.gz").set_index("trade_key")
    trades["excess_bp"] = (trades.net_return - trades.trade_key.map(ctrl.control_net_return)) * 1e4
    trades.loc[~trades.trade_key.map(ctrl.matched).fillna(False).astype(bool), "excess_bp"] = np.nan
    trades["sample"] = np.where(pd.to_datetime(trades.signal_close, utc=True) < pd.Timestamp(cfg["split"]),
                                "okx_earlier", "okx_last12m")
    extra = features(trades, facts, bars)
    return pd.concat([trades.drop(columns=[c for c in extra if c in trades]), extra], axis=1)


def binance_sample() -> pd.DataFrame:
    raw = pd.read_csv(BINANCE_DATA)
    base = raw.set_index(pd.to_datetime(raw.ts, unit="ms", utc=True))[["open", "high", "low", "close", "volume"]]
    bars, _ = p.complete_bars(base, 5)
    meta = p.source.symbol_meta()["ETHUSDT"]
    facts = low.pine_facts(bars, base, meta["asset"], float(meta["tick"]), 5)
    trades = pd.read_csv(BINANCE_LEDGER / "trades.csv.gz")
    trades = trades.loc[(trades.policy == "baseline") & ~trades.censored.astype(bool)
                        & (pd.to_datetime(trades.signal_close, utc=True) < BINANCE_END)].reset_index(drop=True)
    ctrl = pd.read_csv(BINANCE_LEDGER / "controls.csv.gz")
    ctrl = ctrl.loc[ctrl.policy == "baseline"].set_index("trade_key")
    trades["excess_bp"] = trades.trade_key.map(ctrl.excess_bp)
    trades["arm"] = trades.arm.replace({"ordinary": "ordinary", "joint": "joint"})
    trades["sample"] = "binance_2021_2024"
    extra = features(trades, facts, base)
    # The parent ledger already stores rv/momentum; keep one recomputed copy only.
    return pd.concat([trades.drop(columns=[c for c in extra if c in trades]), extra], axis=1)


def bucket_table(data: pd.DataFrame, cuts: dict) -> pd.DataFrame:
    rows = []
    for factor in [*CONTINUOUS, *BINARY, "hour_block", "saturday"]:
        if factor in cuts:
            lo, hi = cuts[factor]
            label = pd.Series(np.select([data[factor] <= lo, data[factor] <= hi], ["low", "mid"], "high"), index=data.index)
            label[data[factor].isna()] = np.nan
        else:
            label = data[factor].map(lambda v: np.nan if pd.isna(v) else str(int(v)) if factor == "hour_block" else str(bool(v)))
        for (arm, sample, level), g in data.assign(level=label).dropna(subset=["level"]).groupby(["arm", "sample", "level"]):
            rows.append({"factor": factor, "arm": arm, "sample": sample, "level": level, "n": len(g),
                         "win_rate": float((g.net_r > 0).mean()), "mean_gross_r": float(g.gross_r.mean()),
                         "mean_net_r": float(g.net_r.mean()), "sum_net_r": float(g.net_r.sum()),
                         "mean_net_bp": float(g.net_return.mean() * 1e4), "mean_excess_bp": float(g.excess_bp.mean())})
    return pd.DataFrame(rows)


def welch(a: pd.Series, b: pd.Series) -> tuple[float, float]:
    a, b = a.dropna(), b.dropna()
    if len(a) < 5 or len(b) < 5:
        return np.nan, np.nan
    diff = a.mean() - b.mean()
    return float(diff), float(diff / np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)))


def stability(data: pd.DataFrame, cuts: dict) -> pd.DataFrame:
    """Same-sign, |t|>=1 in all three samples for ordinary trades."""
    tests = []
    for factor in CONTINUOUS:
        lo, hi = cuts[factor]
        tests.append((factor, "high-low", lambda d, f=factor, lo=lo, hi=hi: (d.net_r[d[f] > hi], d.net_r[d[f] <= lo])))
    for factor in BINARY:
        tests.append((factor, "yes-no", lambda d, f=factor: (d.net_r[d[f] == 1], d.net_r[d[f] == 0])))
    for block in (0, 6, 12, 18):
        tests.append((f"hour_{block:02d}", "block-rest", lambda d, b=block: (d.net_r[d.hour_block == b], d.net_r[d.hour_block != b])))
    tests.append(("saturday", "yes-no", lambda d: (d.net_r[d.saturday], d.net_r[~d.saturday])))
    rows = []
    ordinary = data.loc[data.arm == "ordinary"]
    for name, contrast, split in tests:
        row = {"factor": name, "contrast": contrast}
        signs = []
        for sample in ("okx_earlier", "okx_last12m", "binance_2021_2024"):
            a, b = split(ordinary.loc[ordinary["sample"] == sample])
            diff, t = welch(a, b)
            row[f"{sample}_diff_r"], row[f"{sample}_t"] = diff, t
            signs.append(np.sign(t) if np.isfinite(t) and abs(t) >= 1 else 0)
        row["stable"] = bool(signs[0] != 0 and signs.count(signs[0]) == 3)
        rows.append(row)
    return pd.DataFrame(rows)


def streaks(net_r: np.ndarray, rng: np.random.Generator, perms: int = 20000) -> dict:
    loss = net_r < 0

    def longest(x: np.ndarray) -> int:
        best = run = 0
        for v in x:
            run = run + 1 if v else 0
            best = max(best, run)
        return best

    observed = longest(loss)
    sim = np.array([longest(rng.permutation(loss)) for _ in range(perms)])
    runs = 1 + int((loss[1:] != loss[:-1]).sum())
    n1, n2 = int(loss.sum()), int((~loss).sum())
    mu = 2 * n1 * n2 / (n1 + n2) + 1
    var = 2 * n1 * n2 * (2 * n1 * n2 - n1 - n2) / ((n1 + n2) ** 2 * (n1 + n2 - 1))
    after = {}
    run = 0
    for k in range(1, len(loss)):
        run = run + 1 if loss[k - 1] else 0
        key = "after_win" if run == 0 else f"after_{min(run, 6)}{'+' if run >= 6 else ''}_losses"
        after.setdefault(key, []).append(net_r[k])
    return {"trades": len(loss), "loss_rate": float(loss.mean()), "longest_loss_streak": observed,
            "shuffled_mean": float(sim.mean()), "shuffled_p95": float(np.quantile(sim, .95)),
            "p_shuffled_ge_observed": float((sim >= observed).mean()),
            "runs": runs, "runs_expected": mu, "runs_z": float((runs - mu) / np.sqrt(var)),
            "next_trade": {k: {"n": len(v), "win_rate": float(np.mean(np.asarray(v) > 0)),
                               "mean_net_r": float(np.mean(v))} for k, v in sorted(after.items())}}


def anatomy(okx: pd.DataFrame) -> dict:
    """How OKX ordinary losers die and what the big winners share (descriptive only)."""
    o = okx.loc[okx.arm == "ordinary"].copy()
    o["bars_held"] = o.exit_i - o.entry_i
    stop = o.loc[o.exit_reason == "initial_stop"]
    big = o.loc[o.net_r >= 5]
    rest = o.loc[o.net_r < 5]
    cols = [*CONTINUOUS, *BINARY]
    return {"initial_stop_trades": len(stop),
            "initial_stop_bars_held_quantiles": stop.bars_held.quantile([.25, .5, .75]).to_dict(),
            "initial_stop_within_3_6_12_bars": [float((stop.bars_held <= k).mean()) for k in (3, 6, 12)],
            "initial_stop_mfe_known_ge_half_1r": [float((stop.mfe_known_r >= x).mean()) for x in (.5, 1.0)],
            "losers_with_mfe_ge_1r": int(((o.net_r < 0) & (o.mfe_known_r >= 1)).sum()),
            "losers": int((o.net_r < 0).sum()),
            "big_winner_count": len(big),
            "big_winner_bars_held_median": float(big.bars_held.median()),
            "big_vs_rest_medians": {c: [float(big[c].median()), float(rest[c].median())] for c in cols},
            "big_winner_side_counts": big.side.value_counts().to_dict(),
            "big_winner_hour_blocks": big.hour_block.value_counts().to_dict()}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    okx, binance = okx_sample(), binance_sample()
    data = pd.concat([okx, binance], ignore_index=True)
    early = okx.loc[(okx.arm == "ordinary") & (okx["sample"] == "okx_earlier")]
    cuts = {f: tuple(early[f].quantile([1 / 3, 2 / 3]).tolist()) for f in CONTINUOUS}
    table, stable = bucket_table(data, cuts), stability(data, cuts)
    table.to_csv(OUT / "factors.csv", index=False)
    stable.to_csv(OUT / "stability.csv", index=False)
    rng = np.random.default_rng(SEED)
    streak = {}
    for name, g in (("okx_ordinary", okx.loc[okx.arm == "ordinary"]), ("okx_joint", okx.loc[okx.arm == "joint"]),
                    ("binance_ordinary_2021_2024", binance.loc[binance.arm == "ordinary"])):
        g = g.assign(_t=pd.to_datetime(g.entry_time, utc=True)).sort_values("_t")
        streak[name] = streaks(g.net_r.to_numpy(float), rng)
    p.dump(OUT / "streaks.json", s._clean(streak))
    p.dump(OUT / "anatomy.json", s._clean(anatomy(okx)))
    p.dump(OUT / "receipt.json", {"cuts": cuts, "script_sha256": p.digest(Path(__file__)),
                                  "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                                  "files": {f: p.digest(OUT / f) for f in ("factors.csv", "stability.csv", "streaks.json", "anatomy.json")}})
    print(stable.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
