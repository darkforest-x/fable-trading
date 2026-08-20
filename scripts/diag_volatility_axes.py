"""Owner hypothesis 2026-08-18: "the more volatile the coin, the better the detector works,
especially coins on the current movers leaderboard."

Splits that claim into two axes that both get called "volatility" and tests each against the
matched random-short control from `diag_matched_base_rate.py`:

  AXIS 1  coin-level volatility  -- how volatile the SYMBOL habitually is (cross-sectional)
  AXIS 2  bar-level expansion    -- atr_pct_ratio96, this bar's ATR vs its own trailing
                                    96-bar mean (time-series, causal: rolling(96).mean())

Two reporting units are used because P2-M (analysis/p2m_readonly_mechanism_audit_20260803.md)
proved TP/SL land at exactly +5.000/-2.000 ATR, so raw bp returns are mechanically amplified
by signal-bar ATR. Scale-free units -- ATR-normalized return and win rate -- are the verdict;
bp is reported only for continuity with earlier reports.

Axis 1 is measured causally: symbols are ranked by the PRIOR month's median atr_pct, which is
the only leaderboard a live system could actually have used. A same-window ranking is also
shown to expose how much of the apparent effect is symbol-selection look-ahead.

Read-only. Window 2025-11-04..2026-05-03 is entirely inside train; holdout rows read = 0.
No model is trained, changed, promoted, or used for orders.

Inputs : data/judgment_yolo_owner_side_short_100_6m.csv        (25,602 detector fires)
         analysis/output/base_rate_random_short_atr.csv         (39,692 random shorts)
Output : analysis/output/volatility_axes_20260818/report.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("analysis/output/volatility_axes_20260818")
POOL = "data/judgment_yolo_owner_side_short_100_6m.csv"
BASE = "analysis/output/base_rate_random_short_atr.csv"
COST_BP = 10.0  # owner-controlled taker round trip, unchanged here


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    pool = pd.read_csv(POOL)
    base = pd.read_csv(BASE)
    pool["t"] = pd.to_datetime(pool["signal_time"], utc=True)
    base["t"] = pd.to_datetime(base["t"], utc=True)
    for d in (pool, base):
        d["m"] = d["t"].dt.strftime("%Y-%m")
        d["atr_norm"] = d["realized_ret"] / d["atr_pct"]
        d["win"] = (d["realized_ret"] > 0).astype(float)
    # identical bucket edges to diag_matched_base_rate.py so cells stay comparable
    edges = np.quantile(pool["atr_pct"], [0, 0.2, 0.4, 0.6, 0.8, 1.0])
    edges[0], edges[-1] = -np.inf, np.inf
    for d in (pool, base):
        d["aq"] = pd.cut(d["atr_pct"], edges, labels=False, include_lowest=True)
    return pool, base


def matched(pool: pd.DataFrame, base: pd.DataFrame, cells: list[str], min_n: int) -> pd.DataFrame:
    k = base.groupby(cells).agg(
        rnd_n=("realized_ret", "count"),
        rnd_bp=("realized_ret", "mean"),
        rnd_atr=("atr_norm", "mean"),
        rnd_win=("win", "mean"),
    ).reset_index()
    k = k[k["rnd_n"] >= min_n]
    p = pool.merge(k, on=cells, how="inner").copy()
    p["ex_bp"] = p["realized_ret"] - p["rnd_bp"]
    p["ex_atr"] = p["atr_norm"] - p["rnd_atr"]
    p["ex_win"] = p["win"] - p["rnd_win"]
    return p


def prior_month_leaderboard(base: pd.DataFrame, min_bars: int = 15) -> pd.DataFrame:
    """Rank each symbol by the PREVIOUS month's median atr_pct -- causal, live-reproducible."""
    mv = base.groupby(["symbol", "m"])["atr_pct"].agg(["median", "count"]).reset_index()
    mv = mv[mv["count"] >= min_bars]
    months = sorted(base["m"].unique())
    nxt = {months[i - 1]: months[i] for i in range(1, len(months))}
    mv["target_m"] = mv["m"].map(nxt)
    lb = mv.dropna(subset=["target_m"])[["symbol", "target_m", "median"]]
    lb = lb.rename(columns={"median": "prior_vol"})
    lb["rank_pct"] = lb.groupby("target_m")["prior_vol"].rank(pct=True)
    return lb


def table(df: pd.DataFrame, col: str, name: str) -> pd.DataFrame:
    rows = []
    for tv, g in df.groupby(col, observed=True):
        n = len(g)
        b, sb = g["ex_bp"].mean(), g["ex_bp"].std() / np.sqrt(n)
        a, sa = g["ex_atr"].mean(), g["ex_atr"].std() / np.sqrt(n)
        w, sw = g["ex_win"].mean(), g["ex_win"].std() / np.sqrt(n)
        rows.append({
            name: tv, "n": n,
            "det_bp": g["realized_ret"].mean() * 1e4, "rnd_bp": g["rnd_bp"].mean() * 1e4,
            "excess_bp": b * 1e4, "t_bp": b / sb, "net_bp": b * 1e4 - COST_BP,
            "det_ATRu": g["atr_norm"].mean(), "rnd_ATRu": g["rnd_atr"].mean(),
            "excess_ATRu": a, "t_atr": a / sa,
            "det_win_pct": g["win"].mean() * 100, "rnd_win_pct": g["rnd_win"].mean() * 100,
            "excess_win_pp": w * 100, "t_win": w / sw,
        })
    return pd.DataFrame(rows)



def contrast(a: pd.Series, b: pd.Series, rng: np.random.Generator, iters: int = 20000) -> dict:
    """Two-group difference in mean excess with a label-permutation p-value."""
    obs = a.mean() - b.mean()
    pooled = np.concatenate([a.to_numpy(), b.to_numpy()])
    na = len(a)
    hits = 0
    for _ in range(iters):
        rng.shuffle(pooled)
        if abs(pooled[:na].mean() - pooled[na:].mean()) >= abs(obs):
            hits += 1
    return {"diff": float(obs), "p_perm": (hits + 1) / (iters + 1), "n_a": int(na), "n_b": int(len(b))}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pool, base = load()
    assert pool["t"].max() < pd.Timestamp("2026-05-04", tz="UTC"), "holdout leak"

    res: dict[str, object] = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "pool_rows": int(len(pool)), "pool_symbols": int(pool["symbol"].nunique()),
        "base_rows": int(len(base)),
        "window": [str(pool["t"].min()), str(pool["t"].max())],
        "holdout_rows_read": 0, "cost_bp": COST_BP,
    }

    p = matched(pool, base, ["m", "aq"], 30)
    m, se = p["ex_bp"].mean(), p["ex_bp"].std() / np.sqrt(len(p))
    res["pooled"] = {"n": int(len(p)), "excess_bp": m * 1e4, "t": m / se}

    # AXIS 2 -- expansion at the signal bar
    p["cq"] = pd.qcut(p["atr_pct_ratio96"], 5, labels=False)
    a2 = table(p, "cq", "cq")
    a2["ratio96_med"] = [p.loc[p.cq == q, "atr_pct_ratio96"].median() for q in a2["cq"]]
    a2["abs_atr_pct_med"] = [p.loc[p.cq == q, "atr_pct"].median() * 100 for q in a2["cq"]]
    res["axis2_expansion"] = a2.to_dict("records")

    # AXIS 1 -- coin volatility, causal prior-month leaderboard
    lb = prior_month_leaderboard(base)
    c = p.merge(lb, left_on=["symbol", "m"], right_on=["symbol", "target_m"], how="inner").copy()
    c["lbq"] = pd.cut(c["rank_pct"], [0, .2, .4, .6, .8, 1.0], labels=False, include_lowest=True)
    a1 = table(c, "lbq", "lb_tier")
    a1["prior_atr_pct_med"] = [c.loc[c.lbq == q, "prior_vol"].median() * 100 for q in a1["lb_tier"]]
    res["axis1_coin_vol_causal"] = a1.to_dict("records")
    res["axis1_causal_coverage"] = len(c) / len(p)

    c["grp"] = np.where(c["rank_pct"] > 0.9, "top10pct_movers", "rest")
    res["axis1_leaderboard_top_decile"] = table(c, "grp", "group").to_dict("records")

    per_month = []
    for mth, g in c.groupby("m"):
        top, rest = g[g.rank_pct > 0.9], g[g.rank_pct <= 0.9]
        per_month.append({"month": mth, "top_n": len(top), "rest_n": len(rest),
                          "top_excess_bp": top["ex_bp"].mean() * 1e4,
                          "rest_excess_bp": rest["ex_bp"].mean() * 1e4,
                          "top_excess_win_pp": top["ex_win"].mean() * 100,
                          "rest_excess_win_pp": rest["ex_win"].mean() * 100})
    res["axis1_top_decile_by_month"] = per_month

    # look-ahead contrast: rank symbols by the SAME window they are traded in
    sv = base.groupby("symbol")["atr_pct"].median()
    p2 = p.copy(); p2["symvol"] = p2["symbol"].map(sv)
    qs = np.quantile(sv.dropna(), [0, .2, .4, .6, .8, 1.0]); qs[0], qs[-1] = -np.inf, np.inf
    p2["svq"] = pd.cut(p2["symvol"], qs, labels=False, include_lowest=True)
    res["axis1_coin_vol_lookahead"] = table(p2.dropna(subset=["symvol"]), "svq", "sv_tier").to_dict("records")

    # interaction + the momentum confound behind axis 2
    c["comp"] = np.where(c["cq"] <= 1, "compressed", np.where(c["cq"] >= 3, "expanded", "mid"))
    res["interaction"] = table(c, ["grp", "comp"], "cell").assign(
        cell=lambda d: d["cell"].astype(str)).to_dict("records")
    p["rq"] = pd.qcut(p["ret_12"], 5, labels=False)
    res["axis2_by_recent_return"] = (
        p.pivot_table(index="rq", columns="cq", values="ex_bp", aggfunc=lambda x: x.mean() * 1e4)
        .round(2).to_dict()
    )
    res["axis2_recent_return_n"] = p.pivot_table(index="rq", columns="cq", values="ex_bp",
                                                 aggfunc="count").to_dict()

    rng = np.random.default_rng(20260818)
    top, rest = c[c.rank_pct > 0.9], c[c.rank_pct <= 0.9]
    res["contrasts"] = {
        "axis1_top10_minus_rest_win_pp": contrast(top["ex_win"] * 100, rest["ex_win"] * 100, rng),
        "axis1_top10_minus_rest_ATRu": contrast(top["ex_atr"], rest["ex_atr"], rng),
        "axis1_tier4_minus_tier2_win_pp": contrast(c.loc[c.lbq == 4, "ex_win"] * 100,
                                                   c.loc[c.lbq == 2, "ex_win"] * 100, rng),
        "axis2_cq4_minus_cq0_win_pp": contrast(p.loc[p.cq == 4, "ex_win"] * 100,
                                               p.loc[p.cq == 0, "ex_win"] * 100, rng),
        "axis2_cq4_minus_cq0_ATRu": contrast(p.loc[p.cq == 4, "ex_atr"],
                                             p.loc[p.cq == 0, "ex_atr"], rng),
    }

    (OUT / "report.json").write_text(json.dumps(res, indent=2, default=str))
    print(json.dumps({k: v for k, v in res.items()
                      if k in ("pool_rows", "pooled", "axis1_causal_coverage")}, indent=2, default=str))
    print("\n--- contrasts (label permutation) ---")
    print(json.dumps(res["contrasts"], indent=2))
    for key in ("axis2_expansion", "axis1_coin_vol_causal", "axis1_leaderboard_top_decile"):
        print(f"\n--- {key} ---")
        print(pd.DataFrame(res[key]).round(3).to_string(index=False))


if __name__ == "__main__":
    main()
