"""ETH H1 green checks: sixteen-hour maximum run-up of the OKX top-gainer list.

Owner 2026-10-21: at each ETH H1 check, take the coins on OKX's gainer board at
that moment and measure their 16h maximum gain; first only the last 3 months.

Ranking features use completed 1H closes only: the 24h change is
close[i] / close[i-24] - 1 (25 complete bars, positive 24h volume); the
Beijing-midnight variant is close[i] / open[last 16:00 UTC bar before the
decision] - 1. Trailing volatility for matching is the std of hourly log
returns over at most 168 and at least 72 bars ending at bar i. Only labels see
bars i+1..i+16 (entry at open[i+1]). Reuses EXP-147 hourly/outcomes/choose and
pepe_range_exits.signals; no execution engine, classifier or training.
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.eth_h1_alt_selection import OHLC, choose, dump, hourly, outcomes
from yoyo.evaluation.pepe_range_exits import signals

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-eth-h1-gainers-mfe16-20261021-v1"
LOCAL_END = pd.Timestamp("2026-08-14 04:00", tz="UTC")  # last complete local 15m hour
DELISTED = {"ICX_USDT_SWAP", "QNT_USDT_SWAP", "WEN_USDT_SWAP"}


def paths(frame, hours, cost):
    """EXP-147 fixed-hold labels plus first peak hour and drawdown up to the peak.

    The peak bar's low is included because 1H bars do not order high and low.
    """
    out = outcomes(frame, hours, cost)
    high, low = frame.high.to_numpy(float), frame.low.to_numpy(float)
    n = len(frame)
    peak_h = np.full(n, np.nan)
    pre_peak = np.full(n, np.nan)
    good = out.mfe_bp.notna().to_numpy()
    entry = out.entry_price.to_numpy(float)
    for i in np.flatnonzero(good):
        h, lo = high[i+1:i+1+hours], low[i+1:i+1+hours]
        k = int(np.argmax(h))
        peak_h[i] = k + 1
        pre_peak[i] = (lo[:k+1].min() / entry[i] - 1) * 1e4
    out["peak_hour"] = peak_h
    out["mae_before_peak_bp"] = pre_peak
    return out


def rank_inputs(panel, hours=24, vol_min=72, vol_max=168):
    """Per-bar eligibility, 24h change, Beijing-midnight change and trailing vol.

    Inputs are close/open/volume panels indexed by bar OPEN time; every output
    row i uses rows <= i only.
    """
    c, o, v = panel["close"], panel["open"], panel["volume"]
    complete = c.notna().rolling(hours + 1).sum().eq(hours + 1)
    eligible = complete & v.rolling(hours).sum().gt(0)
    r24 = (c / c.shift(hours) - 1).where(eligible)
    decision = c.index + pd.Timedelta(hours=1)
    anchor = (decision - pd.Timedelta(hours=16, microseconds=1)).floor("D") + pd.Timedelta(hours=16)
    pos = c.index.get_indexer(anchor)
    span = np.arange(len(c)) - pos + 1  # bars from anchor through i inclusive
    counts = c.notna().cumsum().to_numpy()
    bjt = pd.DataFrame(np.nan, index=c.index, columns=c.columns)
    for i in np.flatnonzero(pos >= 0):
        a = pos[i]
        full = (counts[i] - (counts[a-1] if a > 0 else 0)) == span[i]
        bjt.iloc[i] = np.where(full & eligible.iloc[i].to_numpy(),
                               c.iloc[i].to_numpy() / o.iloc[a].to_numpy() - 1, np.nan)
    vol = np.log(c).diff().rolling(vol_max, min_periods=vol_min).std()
    return eligible, r24, bjt, vol


def matched_vol(vol_row, members, pool, k):
    """k nearest trailing-vol coins from pool; ties broken alphabetically."""
    candidates = vol_row[pool].dropna().sort_index()
    out = {}
    for s in members:
        if pd.isna(vol_row.get(s)) or candidates.empty:
            out[s] = []
            continue
        d = (candidates - vol_row[s]).abs()
        out[s] = list(d.sort_values(kind="stable").head(k).index)
    return out


def event_stats(values, draws, seed):
    """Event-level paired differences: mean, bootstrap 95% CI, sign-flip p."""
    x = np.asarray(pd.Series(values).dropna(), float)
    if len(x) == 0:
        return dict(n=0, mean=None, ci_low=None, ci_high=None, p=None, positive=0)
    rng = np.random.default_rng(seed)
    boot = x[rng.integers(0, len(x), size=(draws, len(x)))].mean(axis=1)
    flips = rng.choice([-1, 1], size=(draws, len(x)))
    p = (1 + np.sum((flips * x).mean(axis=1) >= x.mean() - 1e-12)) / (draws + 1)
    return dict(n=len(x), mean=float(x.mean()), ci_low=float(np.quantile(boot, .025)),
                ci_high=float(np.quantile(boot, .975)), p=float(p), positive=int((x > 0).sum()))


def load_panel(cfg, folder):
    """Fetched OKX 1H per symbol; cross-check overlapping local 15m cache hours."""
    symbols = (folder / cfg["symbols"]).read_text().split()
    data, audit = {}, []
    for sym in symbols:
        fetched = sorted(glob.glob(str(folder / cfg["h1_dir"] / f"okx_{sym}_1H_*.csv")))
        local = sorted(glob.glob(str(ROOT / cfg["local_15m_dir"] / f"okx_{sym}_15m_*.csv")))
        row = dict(symbol=sym, fetched=fetched[0] if fetched else None,
                   local=local[0] if local else None)
        if len(fetched) > 1 or len(local) > 1:
            raise ValueError(f"{sym}: ambiguous source files")
        old = hourly(pd.read_csv(local[0], usecols=["ts", *OHLC, "volume"]), 15) if local else None
        if fetched:
            new = hourly(pd.read_csv(fetched[0], usecols=["ts", *OHLC, "volume"]), 60)
            row["source"] = "okx_history_candles_1H"
            if old is not None:
                overlap = new.index.intersection(old.index)
                overlap = overlap[overlap < LOCAL_END]
                row["overlap_hours"] = len(overlap)
                if len(overlap):
                    rel = (new.loc[overlap, OHLC] / old.loc[overlap, OHLC] - 1).abs().to_numpy()
                    row["overlap_max_rel_diff"] = float(rel.max())
                    if rel.max() > 1e-9:
                        raise ValueError(f"{sym}: fetched 1H disagrees with local 15m cache")
            frame = new
        elif old is not None:
            row["source"] = "local_15m_cache_only"
            frame = old
        else:
            row["source"] = "missing"
            audit.append(row)
            continue
        row.update(first_open=str(frame.index.min()), last_open=str(frame.index.max()),
                   bars=len(frame))
        data[sym] = frame
        audit.append(row)
    dump(folder / "input_receipt.json", audit)
    return data, audit


def summarise(rows, thresholds):
    """Event-weighted means plus pooled coin-level shares for one arm."""
    done = rows.dropna(subset=["mfe_bp"])
    out = dict(events=int(rows.event.nunique()), completed_events=int(done.event.nunique()),
               coin_obs=len(done))
    if done.empty:
        return out
    ev = done.groupby("event")[["mfe_bp", "mae_bp", "net_bp", "peak_hour",
                                "mae_before_peak_bp"]].mean()
    out.update(
        mean_mfe_bp=float(ev.mfe_bp.mean()), median_event_mfe_bp=float(ev.mfe_bp.median()),
        median_coin_mfe_bp=float(done.mfe_bp.median()),
        mean_mae_bp=float(ev.mae_bp.mean()), mean_net_bp=float(ev.net_bp.mean()),
        coin_net_win_percent=float(done.net_bp.gt(0).mean() * 100),
        mean_peak_hour=float(ev.peak_hour.mean()),
        mean_mae_before_peak_bp=float(ev.mae_before_peak_bp.mean()))
    for t in thresholds:
        out[f"mfe_ge_{t}pct_percent"] = float(done[f"ge{t}"].mean() * 100)
    return out


def run(cfg, folder):
    if (folder / "delivery_receipt.json").exists():
        print("Cached completed study retained.")
        return
    for path in [Path(__file__), folder / "config.json", folder / "PROJECT_PLAN.md"]:
        name = str(path.relative_to(ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + name], cwd=ROOT) != path.read_bytes():
            raise RuntimeError("Commit builder and plan before evaluation")
    data, audit = load_panel(cfg, folder)
    hold, cost = cfg["hold_hours"], cfg["round_trip_cost"]
    start = pd.Timestamp(cfg["start"])
    # Common end: every fetched live series reaches it, so late fetches add no hour.
    end = min(data[r["symbol"]].index.max() for r in audit
              if r["source"] == "okx_history_candles_1H" and r["symbol"] not in DELISTED)
    index = pd.date_range(start - pd.Timedelta(days=9), end, freq="h")
    syms = sorted(data)
    panel = {k: pd.DataFrame({s: data[s][k].reindex(index) for s in syms})
             for k in [*OHLC, "volume"]}
    eligible, r24, bjt, vol = rank_inputs(panel, cfg["rank_hours"], cfg["vol_min_bars"],
                                          cfg["vol_max_bars"])
    lab = {s: paths(pd.DataFrame({k: panel[k][s] for k in [*OHLC, "volume"]}), hold, cost)
           for s in syms}
    fields = ["mfe_bp", "mae_bp", "net_bp", "gross_bp", "peak_hour", "mae_before_peak_bp"]
    L = {f: pd.DataFrame({s: lab[s][f] for s in syms}) for f in fields}
    eth = pd.DataFrame({k: panel[k]["ETH_USDT_SWAP"] for k in [*OHLC, "volume"]})
    decision = index + pd.Timedelta(hours=1)
    sig = signals(eth).eq(1) & eligible["ETH_USDT_SWAP"]
    positions = np.flatnonzero(sig.to_numpy() & (decision >= start))

    # ETH signals and labels must reproduce EXP-147 where both cover the time.
    prior = pd.read_csv(ROOT / cfg["prior_events"])
    prior = prior[prior.arm.eq("ETH")]
    prior_t = pd.to_datetime(prior.decision_time, utc=True)
    prior_end = pd.Timestamp("2026-10-20 15:00", tz="UTC")
    mine = pd.Series(decision[positions])
    a = set(prior_t[(prior_t >= start) & (prior_t <= prior_end)])
    b = set(mine[mine <= prior_end])
    if a != b:
        raise ValueError(f"ETH signals differ from EXP-147: {sorted(a ^ b)}")
    pn = prior.assign(t=prior_t).set_index("t").net_bp
    for i in positions:
        t = decision[i]
        if t in pn.index and pd.notna(pn[t]):
            if not np.isclose(pn[t], L["net_bp"].iloc[i]["ETH_USDT_SWAP"], atol=1e-6):
                raise ValueError(f"ETH label differs from EXP-147 at {t}")

    th = cfg["mfe_thresholds_percent"]

    def basket(i, members):
        """Member means plus the share of members whose MFE reached each threshold."""
        out = {}
        for f in fields:
            x = L[f].iloc[i][members].to_numpy(float) if members else np.array([])
            out[f] = float(np.nanmean(x)) if np.isfinite(x).any() else np.nan
        m = L["mfe_bp"].iloc[i][members].dropna().to_numpy(float) if members else np.array([])
        for t in th:
            out[f"ge{t}"] = float((m >= t * 100).mean()) if len(m) else np.nan
        return out

    # Top-N basket at every hour defines the same-clock random-time control.
    n0 = cfg["primary_n"]
    window = np.flatnonzero(decision >= start)
    hourly_top = {}
    for p in window:
        top = choose(r24.iloc[p], eligible.iloc[p], n0)
        if len(top) == n0:
            hourly_top[p] = basket(p, top)
    hourly_top = pd.DataFrame(hourly_top).T
    signal_set = set(positions)

    picks, arms = [], []
    for e, i in enumerate(positions):
        t = decision[i]
        elig = eligible.iloc[i]
        ranked = choose(r24.iloc[i], elig, int(elig.sum()))
        tops = {f"top{n}": ranked[:n] for n in cfg["top_n"]}
        tops["bjt_top10"] = choose(bjt.iloc[i], elig, n0)
        members = dict(ETH=["ETH_USDT_SWAP"], market=ranked, **tops)
        pool = ranked[max(cfg["top_n"]):]
        match = matched_vol(vol.iloc[i], tops[f"top{n0}"], pool, cfg["vol_matches"])
        for rank, s in enumerate(ranked[:max(cfg["top_n"])], 1):
            vals = {f: L[f].iloc[i][s] for f in fields}
            ctrl = basket(i, match[s]) if match.get(s) else {}
            picks.append(dict(event=e, decision_time=str(t), symbol=s, rank=rank,
                              r24_percent=r24.iloc[i][s] * 100, vol=vol.iloc[i][s],
                              **vals, volmatch=",".join(match.get(s, [])),
                              **{f"volmatch_{f}": ctrl.get(f, np.nan) for f in fields}))
        for arm, m in members.items():
            for s in m:
                arms.append(dict(event=e, decision_time=str(t), arm=arm, symbol=s,
                                 **basket(i, [s])))
        # Volatility-matched arm: each top member's own control basket.
        for s in tops[f"top{n0}"]:
            ctrl = basket(i, match[s]) if match[s] else basket(i, [])
            arms.append(dict(event=e, decision_time=str(t), arm="volmatch10", symbol=s, **ctrl))
        tod, month = t.hour // 6, t.strftime("%Y-%m")
        ok = [p for p in hourly_top.index if p not in signal_set and abs(p - i) > hold
              and decision[p].hour // 6 == tod and decision[p].strftime("%Y-%m") == month]
        ctrl = hourly_top.loc[ok].mean() if ok else pd.Series(basket(i, []))
        arms.append(dict(event=e, decision_time=str(t), arm="random_time_top10",
                         symbol=f"{len(ok)}_hours", **ctrl.to_dict()))
    pk, ar = pd.DataFrame(picks), pd.DataFrame(arms)
    pk.to_csv(folder / "picks.csv", index=False)
    ar.to_csv(folder / "arm_rows.csv", index=False)

    order = ["ETH", "market", "top5", "top10", "top20", "bjt_top10", "volmatch10",
             "random_time_top10"]
    summary = [dict(arm=a, **summarise(ar[ar.arm.eq(a)], th)) for a in order]
    ev = ar.groupby(["event", "arm"])[fields].mean().unstack("arm")
    paired = {}
    board = pk[pk["rank"].le(n0)]
    for f in ["mfe_bp", "net_bp", "mae_bp"]:
        for other in ["market", "random_time_top10", "ETH"]:
            paired[f"top10_minus_{other}_{f}"] = event_stats(
                ev[(f, "top10")] - ev[(f, other)], cfg["bootstrap_draws"], cfg["seed"])
        # Vol matching lacks controls for new listings: difference only matched members.
        m = board.dropna(subset=[f, f"volmatch_{f}"])
        paired[f"top10_minus_volmatch10_{f}"] = dict(
            event_stats((m[f] - m[f"volmatch_{f}"]).groupby(m.event).mean(),
                        cfg["bootstrap_draws"], cfg["seed"]),
            matched_members=len(m), board_members=int(board[f].notna().sum()))
    dump(folder / "summary.json", dict(arms=summary, paired=paired))
    pd.DataFrame(summary).to_csv(folder / "summary.csv", index=False)

    # Human-readable per-event table: the board at that moment and what it did.
    table = []
    for e, i in enumerate(positions):
        t = decision[i]
        sub = pk[pk.event.eq(e) & pk["rank"].le(n0)]
        g = ev.loc[e] if e in ev.index else None
        table.append(dict(
            decision_bjt=str(t.tz_convert("Asia/Shanghai")),
            eth_r24_percent=r24.iloc[i]["ETH_USDT_SWAP"] * 100,
            eth_mfe_percent=g[("mfe_bp", "ETH")] / 100 if g is not None else np.nan,
            top10_mfe_percent=g[("mfe_bp", "top10")] / 100,
            top10_net_percent=g[("net_bp", "top10")] / 100,
            market_mfe_percent=g[("mfe_bp", "market")] / 100,
            volmatch_mfe_percent=g[("mfe_bp", "volmatch10")] / 100,
            random_time_mfe_percent=g[("mfe_bp", "random_time_top10")] / 100,
            board=" ".join(f"{r.symbol.replace('_USDT_SWAP', '')}({r.r24_percent:+.1f}%→"
                           f"{r.mfe_bp/100:+.1f}%)" if pd.notna(r.mfe_bp) else
                           f"{r.symbol.replace('_USDT_SWAP', '')}({r.r24_percent:+.1f}%→删失)"
                           for r in sub.itertuples())))
    pd.DataFrame(table).to_csv(folder / "event_table.csv", index=False)
    dump(folder / "delivery_receipt.json", dict(
        config=cfg, source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                           cwd=ROOT, text=True).strip(),
        symbols_loaded=len(syms), sources=pd.DataFrame(audit).source.value_counts().to_dict(),
        panel=[str(index[0]), str(index[-1])], signal_events=len(positions),
        reproduced_exp147_signals=len(b), random_time_hours=len(hourly_top),
        summary=summary, paired=paired, production_eligible=False, training_eligible=False,
        limitations=[
            "Universe is the 2026-10-21 live crypto list plus three cached delisted names, not a census.",
            "Maximum gain is a path extreme before costs, not an achievable exit.",
            "1H bars do not order high and low; pre-peak drawdown includes the peak bar low.",
            "About three months and a few dozen events: descriptive only, no admission claim.",
            "No funding, spread, depth or slippage beyond the 20bp close-return cost."]))
    print(json.dumps(dict(events=len(positions), symbols=len(syms),
                          random_time_hours=len(hourly_top))))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    args = parser.parse_args()
    run(json.loads(args.config.read_text()), args.config.parent)
