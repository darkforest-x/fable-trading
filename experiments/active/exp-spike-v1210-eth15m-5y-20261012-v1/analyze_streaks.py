"""Why V12.10 ETH 15m has long runs of initial stops (owner question 2026-10-12, 「为什么有很多次连续的止损」).

Descriptive diagnostics on results_v1 from build.py; no rule is changed or selected here.

1. Are the runs abnormal? Longest losing run and run count versus (a) the same results in random order
   (10,000 shuffles: clustering test) and (b) independent trades at the observed win rate.
   Conditional loss rate after a loss versus after a win.
2. The streaks themselves: every run of >= 6 consecutive losses with dates, sides, ETH move and regime.
3. Causal factors at the signal bar (bars <= signal bar only), fixed before looking at results:
   - er96 / er384: Kaufman efficiency ratio over the prior 24h / 4 days (low = choppy, high = trending);
   - atr_rank: ATR(14)/close percentile within the prior 30 days (volatility level);
   - vidya_age: bars since the VIDYA trend last flipped; vidya_dist: side x (close - VIDYA line) / ATR;
   - trend8d: side x (close - SMA(800)) / ATR, about an 8-day trend agreement;
   - bar_range: signal bar range / ATR; risk_atr: initial risk / ATR;
   - prev_loss: whether the previous closed trade lost.
   Each factor is split into terciles on the full sample; a factor counts as an explanation only if the
   direction holds in both periods (before / from 2024-02-01). With ~10 factors some splits will look
   large by chance, so nothing here is a validated filter.
4. Loser anatomy: how fast initial stops are hit and how far losers ran first (MFE), and whether the
   matched random entries in the same weeks lost too (market regime versus signal quality).

Usage (after build.py):
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1210-eth15m-5y-20261012-v1/analyze_streaks.py
Output: results_v1/streaks.json and printed tables.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RES = HERE / "results_v1"
CFG = json.loads((HERE / "config.json").read_text())
SPLIT = pd.Timestamp(CFG["split"])
RNG = np.random.default_rng(int(CFG["stat_seed"]) + 7)
SHUFFLES = 10_000


def rma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[n - 1] = np.nanmean(x[:n])
    for i in range(n, len(x)):
        out[i] = out[i - 1] + (x[i] - out[i - 1]) / n
    return out


def longest(loss: np.ndarray) -> int:
    best = run = 0
    for v in loss:
        run = run + 1 if v else 0
        best = max(best, run)
    return best


def runs(loss: np.ndarray) -> int:
    return int(1 + (loss[1:] != loss[:-1]).sum())


def bars_features() -> pd.DataFrame:
    b = pd.read_csv(RES / "bars_15m.csv.gz")
    c, h, l = b.close.to_numpy(), b.high.to_numpy(), b.low.to_numpy()
    pc = np.roll(c, 1)
    pc[0] = c[0]
    atr = rma(np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))), 14)
    step = np.abs(np.diff(c, prepend=c[0]))
    feats = {"ts": b.ts.to_numpy(), "close": c, "atr": atr, "range": h - l, "vidya_line": b.vidya_line.to_numpy(),
             "vidya_up": b.vidya_up.to_numpy(bool)}
    for n in (96, 384):
        move = np.abs(c - np.roll(c, n))
        path = pd.Series(step).rolling(n).sum().to_numpy()
        er = np.where(path > 0, move / path, np.nan)
        er[:n] = np.nan
        feats[f"er{n}"] = er
    atrp = pd.Series(atr / c)
    feats["atr_rank"] = atrp.rolling(2880, min_periods=500).rank(pct=True).to_numpy()
    feats["sma800"] = pd.Series(c).rolling(800).mean().to_numpy()
    up = feats["vidya_up"]
    flip_idx = np.where(np.r_[True, up[1:] != up[:-1]], np.arange(len(up)), 0)
    feats["vidya_age"] = np.arange(len(up)) - np.maximum.accumulate(flip_idx)
    return pd.DataFrame(feats)


def bucket_table(df: pd.DataFrame, col: str) -> dict:
    q = df[col].quantile([1 / 3, 2 / 3]).to_numpy()
    lab = np.where(df[col] <= q[0], "低", np.where(df[col] <= q[1], "中", "高"))
    out = {"cuts": [round(float(x), 3) for x in q], "rows": []}
    for name in ("低", "中", "高"):
        g = df[lab == name]
        row = {"bucket": name, "trades": int(len(g)), "win_rate": round(float((g.net_r > 0).mean() * 100), 1),
               "avg_r": round(float(g.net_r.mean()), 3), "loss_rate": round(float((g.net_r <= 0).mean() * 100), 1)}
        for per, m in (("before", g.signal_close < SPLIT), ("after", g.signal_close >= SPLIT)):
            row[per] = {"trades": int(m.sum()), "win_rate": round(float((g.net_r[m] > 0).mean() * 100), 1) if m.any() else None,
                        "avg_r": round(float(g.net_r[m].mean()), 3) if m.any() else None}
        out["rows"].append(row)
    lo, hi = out["rows"][0], out["rows"][2]
    out["consistent"] = bool(lo["before"]["avg_r"] is not None and hi["before"]["avg_r"] is not None
                             and np.sign(hi["before"]["avg_r"] - lo["before"]["avg_r"]) == np.sign(hi["after"]["avg_r"] - lo["after"]["avg_r"]))
    return out


def main():
    t = pd.read_csv(RES / "okx_vidya_gate_trades.csv.gz")
    t = t.loc[~t.censored.astype(bool)].copy()
    for col in ("signal_close", "signal_bar_open", "entry_time", "exit_time"):
        t[col] = pd.to_datetime(t[col], utc=True)
    t = t.sort_values(["exit_time", "trade_key"]).reset_index(drop=True)
    ctl = pd.read_csv(RES / "okx_vidya_gate_controls.csv.gz").set_index("trade_key").control_net_r
    t["control_r"] = t.trade_key.map(ctl)
    loss = (t.net_r <= 0).to_numpy()
    q = loss.mean()

    # 1) abnormal or expected?
    obs_long, obs_runs = longest(loss), runs(loss)
    sh_long, sh_runs = np.empty(SHUFFLES), np.empty(SHUFFLES)
    bern_long = np.empty(SHUFFLES)
    for k in range(SHUFFLES):
        perm = RNG.permutation(loss)
        sh_long[k], sh_runs[k] = longest(perm), runs(perm)
        bern_long[k] = longest(RNG.random(len(loss)) < q)
    after_loss = loss[1:][loss[:-1]].mean()
    after_win = loss[1:][~loss[:-1]].mean()
    abnormal = {"trades": int(len(loss)), "loss_rate": round(float(q * 100), 1), "longest_losing_run": obs_long,
                "shuffle_longest_median": float(np.median(sh_long)), "shuffle_p_longest_ge_obs": round(float((sh_long >= obs_long).mean()), 4),
                "independent_longest_median": float(np.median(bern_long)), "independent_p_ge_obs": round(float((bern_long >= obs_long).mean()), 4),
                "independent_p_ge10": round(float((bern_long >= 10).mean()), 4),
                "runs_observed": obs_runs, "runs_shuffle_mean": round(float(sh_runs.mean()), 1),
                "clustering_p_fewer_runs": round(float((sh_runs <= obs_runs).mean()), 4),
                "loss_rate_after_loss": round(float(after_loss * 100), 1), "loss_rate_after_win": round(float(after_win * 100), 1)}
    print("1) streaks vs chance", json.dumps(abnormal, ensure_ascii=False), flush=True)

    # features at the signal bar
    f = bars_features()
    idx = np.searchsorted(f.ts.to_numpy(), (t.signal_bar_open.astype("int64") // 1_000_000).to_numpy())
    fs = f.iloc[idx].reset_index(drop=True)
    side = t.side.to_numpy()
    atr = fs.atr.to_numpy()
    t["er96"], t["er384"], t["atr_rank"], t["vidya_age"] = fs.er96.to_numpy(), fs.er384.to_numpy(), fs.atr_rank.to_numpy(), fs.vidya_age.to_numpy()
    t["vidya_dist"] = side * (fs.close.to_numpy() - fs.vidya_line.to_numpy()) / atr
    t["trend8d"] = side * (fs.close.to_numpy() - fs.sma800.to_numpy()) / atr
    t["bar_range"] = fs.range.to_numpy() / atr
    t["risk_atr"] = (t.initial_risk.to_numpy() / atr)
    t["hold_h"] = (t.exit_time - t.entry_time).dt.total_seconds() / 3600
    t["prev_loss"] = np.r_[np.nan, loss[:-1].astype(float)]

    # 2) the streaks
    streaks, i = [], 0
    while i < len(loss):
        if loss[i]:
            j = i
            while j + 1 < len(loss) and loss[j + 1]:
                j += 1
            if j - i + 1 >= 6:
                g = t.iloc[i:j + 1]
                p0 = float(g.entry_price.iloc[0]); p1 = float(g.exit_price.iloc[-1])
                streaks.append({"n": int(j - i + 1), "from": str(g.entry_time.iloc[0])[:16], "to": str(g.exit_time.iloc[-1])[:16],
                                "days": round((g.exit_time.iloc[-1] - g.entry_time.iloc[0]).total_seconds() / 86400, 1),
                                "sum_r": round(float(g.net_r.sum()), 1), "longs": int((g.side == 1).sum()), "shorts": int((g.side == -1).sum()),
                                "eth_move_pct": round((p1 / p0 - 1) * 100, 1), "median_er96": round(float(g.er96.median()), 3),
                                "median_hold_h": round(float(g.hold_h.median()), 1), "initial_stops": int((g.exit_reason == "initial_stop").sum()),
                                "control_avg_r": round(float(g.control_r.mean()), 2)})
            i = j + 1
        else:
            i += 1
    print("2) runs >= 6:", flush=True)
    for s in streaks:
        print("  ", s)

    # 3) factors
    factors = ["er96", "er384", "atr_rank", "vidya_age", "vidya_dist", "trend8d", "bar_range", "risk_atr"]
    tables = {c: bucket_table(t.dropna(subset=[c]), c) for c in factors}
    print("3) factor terciles (win rate / avg R; before | after):", flush=True)
    for c, tb in tables.items():
        print(f"   {c:10s} cuts {tb['cuts']} consistent={tb['consistent']} :: " + " | ".join(
            f"{r['bucket']} n{r['trades']} wr{r['win_rate']} {r['avg_r']:+.2f} ({r['before']['avg_r']:+.2f}/{r['after']['avg_r']:+.2f})" for r in tb["rows"]))
    pl = {k: {"trades": int(len(g)), "win_rate": round(float((g.net_r > 0).mean() * 100), 1), "avg_r": round(float(g.net_r.mean()), 3)}
          for k, g in (("after_loss", t[t.prev_loss == 1]), ("after_win", t[t.prev_loss == 0]))}

    # 4) loser anatomy
    stops = t[t.exit_reason == "initial_stop"]
    losers = t[t.net_r <= 0]
    anatomy = {"initial_stops": int(len(stops)), "stops_within_1h": int((stops.hold_h <= 1).sum()), "stops_within_4h": int((stops.hold_h <= 4).sum()),
               "stops_median_hold_h": round(float(stops.hold_h.median()), 2),
               "stops_mfe_lt_0_5": int((stops.mfe_known_r < 0.5).sum()), "stops_mfe_ge_1": int((stops.mfe_known_r >= 1).sum()),
               "losers_control_avg_r": round(float(losers.control_r.mean()), 3), "winners_control_avg_r": round(float(t[t.net_r > 0].control_r.mean()), 3),
               "streak_trades_control_avg_r": round(float(np.mean([s["control_avg_r"] for s in streaks])), 3) if streaks else None,
               "stop_risk_pct_median": round(float(stops.initial_risk_frac.median() * 100), 2),
               "stop_risk_atr_median": round(float(stops.risk_atr.median()), 2)}
    print("4) anatomy", json.dumps(anatomy, ensure_ascii=False), "\n   prev", pl, flush=True)

    # year x regime view
    t["year"] = t.signal_close.dt.year
    t["chop"] = np.where(t.er96 <= tables["er96"]["cuts"][0], "chop", np.where(t.er96 <= tables["er96"]["cuts"][1], "mid", "trend"))
    yr = t.groupby("year").agg(trades=("net_r", "size"), win=("net_r", lambda r: round(float((r > 0).mean() * 100), 1)),
                               sum_r=("net_r", lambda r: round(float(r.sum()), 1)), er96=("er96", lambda x: round(float(x.median()), 3)),
                               atr_rank=("atr_rank", lambda x: round(float(x.median()), 2)), stops=("exit_reason", lambda x: int((x == "initial_stop").sum()))).reset_index()
    print("5) by year\n", yr.to_string(index=False), flush=True)
    out = {"abnormal": abnormal, "streaks": streaks, "factors": tables, "after_prev": pl, "anatomy": anatomy,
           "by_year": yr.to_dict("records")}
    (RES / "streaks.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float))
    print("wrote", RES / "streaks.json")


if __name__ == "__main__":
    main()
