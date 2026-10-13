"""Bit浪浪 live picks v1: did the alts he named in real time beat the alts he could have named?

Source: his weekly small-group livestreams (2023-10-28 .. 2024-06-01, re-uploaded on YouTube) and
fan Q&A clips, transcribed locally for the Bit浪浪 handbook v2 (outside this public repo). In most
sessions he ends with "下周重点关注" / "财富密码": a short list of alts he expects to lead. Those
lists are copied into the experiment config verbatim (with the transcript timestamp) before any
return is computed. The handbook's video says the system's edge is "选龙头"; the earlier backtest
(exp-bitlanglang-mainwave-20261011-v1) could only proxy that with volume ranks. Here the selection
is his own, made in real time, so there is no look-ahead in the choice itself.

Rule (one variable: who chose the coin):
  entry     the first 5m open at or after 16:00 UTC on the session date (00:00 Beijing the next
            day; every session ended before that)
  return    close of the last 5m bar before entry + H days, divided by the entry open, minus 1;
            H in config.horizons_days. No costs (no trade is simulated, only the selection)
  universe  the month's top-N alts by previous-month median daily quote volume (same rule as
            market_sync_shock.monthly_universe; BTC, ETH and stables excluded), restricted to
            symbols with both prices; a pick outside the universe is still scored against it
  per pick  excess = pick return - universe median; pct = share of universe members with a lower
            return (0.5 = a coin drawn at random)
  control   10,000 draws: replace every pick with a random universe member of the same session
            (seeded); p = share of draws whose mean excess >= the picks' mean excess. Sessions are
            also averaged first (picks within a session share the same week) as a second view.
Picks missing from the frozen series (not listed on Binance USD-M then) are reported, not scored.
Summaries are repeated for focus picks only and for picks whose coin name is not inferred (uncertain=false).

Momentum control (added after the first run, v1b; `--momentum`): his picks were mostly coins that had
already run. For each session, rank universe members by their trailing 30-day return up to the entry
time; report where the picks sit in that ranking, the forward excess of the session's top-k trailing
movers (k = number of scored picks), and a p-value for the picks against random draws from the
session's top trailing-return quintile (same k).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.market_sync_shock import LEADERS, SERIES, STABLE, monthly_universe, read_5m

DAY_MS = 86_400_000


def price_path(symbol: str, entry: pd.Timestamp, horizons: list[int]) -> dict | None:
    """Entry open and the close at each horizon; None when the series does not cover entry."""
    path = SERIES / f"{symbol}.csv.gz"
    if not path.exists():
        return None
    raw = read_5m(symbol, entry - pd.Timedelta(days=1), entry + pd.Timedelta(days=max(horizons) + 1))
    after = raw.loc[raw.ts >= entry.value // 10**6]
    if after.empty or after.ts.iloc[0] - entry.value // 10**6 > 3_600_000:
        return None
    out = {"entry_ts": int(after.ts.iloc[0]), "entry": float(after.open.iloc[0])}
    for h in horizons:
        cut = after.loc[after.ts + 300_000 <= entry.value // 10**6 + h * DAY_MS]
        full = not cut.empty and entry.value // 10**6 + h * DAY_MS - (cut.ts.iloc[-1] + 300_000) <= 3_600_000
        out[f"ret_{h}d"] = float(cut.close.iloc[-1] / out["entry"] - 1) if full else None
    return out


def _subset_p(sub: pd.DataFrame, pools: dict, col: str, cfg: dict) -> float | None:
    """Same-session random-draw p-value for a subset of picks (own seed so the main draws stay unchanged)."""
    if sub.empty:
        return None
    rng = np.random.default_rng(cfg["seed"] + 7)
    obs = float(sub[col].mean())
    counts = sub.groupby("session").size()
    draws = np.array([np.concatenate([rng.choice(pools[sid][0], size=int(n), replace=False) - pools[sid][1]
                                      for sid, n in counts.items()]).mean() for _ in range(cfg["draws"])])
    return float((draws >= obs).mean())


def score(cfg: dict) -> dict:
    horizons = cfg["horizons_days"]
    symbols = sorted(p.name.removesuffix(".csv.gz") for p in SERIES.glob("*.csv.gz"))
    dates = sorted({s["date"] for s in cfg["sessions"]})
    start = pd.Timestamp(dates[0], tz="UTC") - pd.DateOffset(months=1)
    end = pd.Timestamp(dates[-1], tz="UTC") + pd.Timedelta(days=max(horizons) + 2)
    universe = monthly_universe(symbols, start, end, cfg["universe_size"])
    rows, missing, uni_rows = [], [], []
    for s in cfg["sessions"]:
        entry = pd.Timestamp(s["date"], tz="UTC") + pd.Timedelta(hours=cfg["entry_utc_hour"])
        members = [m for m in universe.get(entry.strftime("%Y-%m"), []) if m not in LEADERS and m not in STABLE]
        paths = {m: price_path(m, entry, horizons) for m in members}
        paths = {m: p for m, p in paths.items() if p is not None}
        for m, p in paths.items():
            uni_rows.append({"session": s["id"], "symbol": m, **{k: v for k, v in p.items() if k.startswith("ret_")}})
        for pick in s["picks"]:
            sym = pick["symbol"]
            p = paths.get(sym) or price_path(sym, entry, horizons)
            if p is None:
                missing.append({"session": s["id"], "symbol": sym})
                continue
            row = {"session": s["id"], "date": s["date"], "video": s["video"], "t": pick.get("t"), "symbol": sym,
                   "rank": pick.get("rank", "focus"), "uncertain": bool(pick.get("uncertain")), "in_universe": sym in paths,
                   "entry": p["entry"]}
            for h in horizons:
                r = p[f"ret_{h}d"]
                peers = np.array([q[f"ret_{h}d"] for m, q in paths.items() if m != sym and q[f"ret_{h}d"] is not None])
                row[f"ret_{h}d"] = r
                row[f"uni_median_{h}d"] = float(np.median(peers)) if len(peers) and r is not None else None
                row[f"excess_{h}d"] = r - row[f"uni_median_{h}d"] if row[f"uni_median_{h}d"] is not None else None
                row[f"pct_{h}d"] = float((peers < r).mean()) if len(peers) and r is not None else None
            rows.append(row)
    picks = pd.DataFrame(rows)
    uni = pd.DataFrame(uni_rows)
    rng = np.random.default_rng(cfg["seed"])
    summary = {}
    for h in horizons:
        col = f"excess_{h}d"
        ok = picks.dropna(subset=[col])
        if ok.empty:
            continue
        obs = float(ok[col].mean())
        sess_obs = float(ok.groupby("session")[col].mean().mean())
        # Null: each pick replaced by a random member of its own session's universe.
        pools = {sid: (g[f"ret_{h}d"].dropna().to_numpy(), float(g[f"ret_{h}d"].dropna().median()))
                 for sid, g in uni.groupby("session")}
        draws = np.zeros(cfg["draws"])
        sess_draws = np.zeros(cfg["draws"])
        counts = ok.groupby("session").size()
        for i in range(cfg["draws"]):
            per = []
            for sid, n in counts.items():
                pool, med = pools[sid]
                per.append(rng.choice(pool, size=int(n), replace=False) - med)
            draws[i] = np.concatenate(per).mean()
            sess_draws[i] = np.mean([x.mean() for x in per])
        summary[f"{h}d"] = {
            "picks": int(len(ok)), "sessions": int(ok.session.nunique()),
            "mean_ret": float(ok[f"ret_{h}d"].mean()), "median_ret": float(ok[f"ret_{h}d"].median()),
            "mean_universe_median": float(ok[f"uni_median_{h}d"].mean()),
            "mean_excess": obs, "median_excess": float(ok[col].median()),
            "beat_median_share": float((ok[col] > 0).mean()), "mean_pct": float(ok[f"pct_{h}d"].mean()),
            "p_mean_excess": float((draws >= obs).mean()), "session_mean_excess": sess_obs,
            "p_session_mean_excess": float((sess_draws >= sess_obs).mean()),
            "focus_only_mean_excess": float(ok.loc[ok["rank"] == "focus", col].mean()) if (ok["rank"] == "focus").any() else None,
            "focus_only_beat_share": float((ok.loc[ok["rank"] == "focus", col] > 0).mean()) if (ok["rank"] == "focus").any() else None,
            "certain_only_mean_excess": float(ok.loc[~ok["uncertain"], col].mean()) if (~ok["uncertain"]).any() else None,
            "certain_only_p_mean_excess": _subset_p(ok.loc[~ok["uncertain"]], pools, col, cfg),
        }
    return {"summary": summary, "picks": rows, "missing": missing}


def trailing_return(symbol: str, entry: pd.Timestamp, days: int = 30) -> float | None:
    raw = read_5m(symbol, entry - pd.Timedelta(days=days + 1), entry)
    if raw.empty:
        return None
    start = raw.loc[raw.ts >= (entry - pd.Timedelta(days=days)).value // 10**6]
    if start.empty or start.ts.iloc[0] - (entry - pd.Timedelta(days=days)).value // 10**6 > 3_600_000:
        return None
    return float(raw.close.iloc[-1] / start.open.iloc[0] - 1)


def momentum(cfg: dict, picks: pd.DataFrame) -> dict:
    """Post-hoc momentum control: do the session's strongest trailing movers do as well as his picks?"""
    horizons = cfg["horizons_days"]
    symbols = sorted(p.name.removesuffix(".csv.gz") for p in SERIES.glob("*.csv.gz"))
    dates = sorted({s["date"] for s in cfg["sessions"]})
    universe = monthly_universe(symbols, pd.Timestamp(dates[0], tz="UTC") - pd.DateOffset(months=1),
                                pd.Timestamp(dates[-1], tz="UTC") + pd.Timedelta(days=max(horizons) + 2), cfg["universe_size"])
    rng = np.random.default_rng(cfg["seed"] + 11)
    rows, pick_ranks = [], []
    for s in cfg["sessions"]:
        entry = pd.Timestamp(s["date"], tz="UTC") + pd.Timedelta(hours=cfg["entry_utc_hour"])
        members = [m for m in universe.get(entry.strftime("%Y-%m"), []) if m not in LEADERS and m not in STABLE]
        recs = []
        for m in members:
            fwd = price_path(m, entry, horizons)
            tr = trailing_return(m, entry)
            if fwd is not None and tr is not None:
                recs.append({"symbol": m, "trail": tr, **{f"ret_{h}d": fwd[f"ret_{h}d"] for h in horizons}})
        u = pd.DataFrame(recs)
        mine = picks.loc[picks.session == s["id"]]
        for _, p in mine.iterrows():
            tr = trailing_return(p.symbol, entry)
            pick_ranks.append({"session": s["id"], "symbol": p.symbol, "trail": tr,
                               "trail_pct": float((u.trail < tr).mean()) if tr is not None else None})
        k = len(mine)
        top = u.sort_values("trail", ascending=False).head(k)
        quint = u.loc[u.trail >= u.trail.quantile(0.8)]
        row = {"session": s["id"], "k": k}
        for h in horizons:
            med = float(u[f"ret_{h}d"].median())
            row[f"top_excess_{h}d"] = float(top[f"ret_{h}d"].mean() - med)
            row[f"pick_excess_{h}d"] = float(mine[f"excess_{h}d"].mean())
            row[f"quint_pool_{h}d"] = (quint[f"ret_{h}d"].dropna().to_numpy() - med).tolist()
        rows.append(row)
    out = {"pick_trailing_pct_mean": float(np.nanmean([r["trail_pct"] for r in pick_ranks if r["trail_pct"] is not None])),
           "pick_trailing_pct_median": float(np.nanmedian([r["trail_pct"] for r in pick_ranks if r["trail_pct"] is not None])),
           "pick_ranks": pick_ranks}
    for h in horizons:
        n = sum(r["k"] for r in rows)
        top_mean = sum(r[f"top_excess_{h}d"] * r["k"] for r in rows) / n
        pick_mean = sum(r[f"pick_excess_{h}d"] * r["k"] for r in rows) / n
        draws = np.array([np.concatenate([rng.choice(r[f"quint_pool_{h}d"], size=r["k"], replace=len(r[f"quint_pool_{h}d"]) < r["k"])
                                          for r in rows]).mean() for _ in range(cfg["draws"])])
        out[f"{h}d"] = {"picks_mean_excess": pick_mean, "top_trailing_k_mean_excess": top_mean,
                        "top_quintile_random_mean_excess": float(draws.mean()), "p_picks_vs_top_quintile": float((draws >= pick_mean).mean())}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--momentum", action="store_true", help="post-hoc v1b control; reads picks.csv from --out")
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    if a.momentum:
        mom = momentum(cfg, pd.read_csv(Path(a.out) / "picks.csv"))
        (Path(a.out) / "momentum_v1b.json").write_text(json.dumps(mom, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in mom.items() if k != "pick_ranks"}, indent=2))
        return
    res = score(cfg)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(res["picks"]).to_csv(out / "picks.csv", index=False)
    (out / "summary.json").write_text(json.dumps({"experiment_id": cfg["experiment_id"], "summary": res["summary"],
                                                   "missing": res["missing"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res["summary"], ensure_ascii=False, indent=2))
    if res["missing"]:
        print("missing:", res["missing"])


if __name__ == "__main__":
    main()
