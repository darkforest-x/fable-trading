"""OKX 24h gainer-board fresh entries: exit methods, then coin selection.

Owner 2026-10-21, after EXP-148 (board run-up is a board property, holding 16h
loses): research how to exit gainer-board coins and which ones to choose.

Event: a coin is in the 24h-change Top10 at a completed 1H close and was in the
Top10 at none of the previous 24 closes. Entry at the next open. Features use
bars <= the decision bar only (24h/1h returns, 24h-high pullback, 24h volume
versus the prior 7 daily averages, listing age from the instrument listTime,
prior 7d return, market median and BTC 24h change, 24h USDT turnover from
contracts x current ctVal x close, Top10 hours in the prior 168). Exits only
see bars after entry; the board exit reads later ranks, which are themselves
causal at each close. TP/SL resolution reuses yoyo.contracts.outcomes.
Panel loading and ranking reuse EXP-148 (eth_h1_gainers_mfe16).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.contracts.outcomes import OutcomeContractError, resolve_barrier_outcome
from yoyo.evaluation.eth_h1_alt_selection import OHLC, dump
from yoyo.evaluation.eth_h1_gainers_mfe16 import DELISTED, load_panel, matched_vol, rank_inputs
from yoyo.evaluation.imacd_startup_research import inference

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-gainer-board-exit-select-20261021-v1"
FEATURES = ["rank", "r24", "r1", "pullback24", "relvol", "age_h", "r7d_prior",
            "mkt_r24", "btc_r24", "turnover24", "board7d"]


def exit_specs(cfg):
    """The fixed exit grid; order is also the dev-selection tie-break."""
    specs = [dict(name=f"time{h}", family="time", hours=h) for h in cfg["time_exits"]]
    specs += [dict(name=f"tp{tp}_sl{sl}", family="tpsl", tp=tp, sl=sl, hours=cfg["tpsl_horizon"])
              for tp in cfg["tp_percent"] for sl in cfg["sl_percent"]]
    specs += [dict(name=f"trail{t}", family="trail", trail=t, hours=cfg["max_hold"])
              for t in cfg["trail_percent"]]
    specs += [dict(name=f"offboard{n}", family="board", n=n, hours=cfg["max_hold"])
              for n in cfg["board_exit_n"]]
    return specs


def ranks(r24):
    """Descending 24h rank among eligible coins; ties by symbol (columns sorted)."""
    return r24[sorted(r24.columns)].rank(axis=1, ascending=False, method="first")


def fresh_events(rank, n, hours):
    """In Top-n now and in none of the previous `hours` closes."""
    on = rank.le(n)
    before = on.astype(float).shift(1).rolling(hours, min_periods=hours).max()
    return on & before.eq(0)


def features(panel, r24, rank, listing_ms, ctval, i, s):
    """Decision-time facts for coin s at bar i; every window ends at bar i."""
    c, h, v = panel["close"][s], panel["high"][s], panel["volume"][s]
    ci = c.iloc[i]
    vol24 = v.iloc[i-23:i+1].sum()
    prior = v.iloc[i-191:i-23]
    relvol = vol24 / (prior.sum() / 7) if prior.notna().sum() >= 168 and prior.sum() > 0 else np.nan
    t = panel["close"].index[i] + pd.Timedelta(hours=1)
    age = (t.value // 10**6 - listing_ms[s]) / 3.6e6 if s in listing_ms else np.nan
    row = r24.iloc[i]
    return dict(
        rank=rank.iloc[i][s], r24=row[s], r1=ci / c.iloc[i-1] - 1,
        pullback24=ci / h.iloc[i-23:i+1].max() - 1, relvol=relvol, age_h=age,
        r7d_prior=c.iloc[i-24] / c.iloc[i-192] - 1, mkt_r24=row.median(),
        btc_r24=row.get("BTC_USDT_SWAP", np.nan),
        turnover24=(v.iloc[i-23:i+1] * c.iloc[i-23:i+1]).sum() * ctval[s] if s in ctval else np.nan,
        board7d=float(rank[s].iloc[i-168:i].le(10).sum()))


def path_exit(sym, i, side, spec, rank_col, cost):
    """One exit on one coin entered at open[i+1]; NaN outcome when censored.

    `sym` holds numpy o/h/l/c and the DataFrame for the contract resolver.
    Trailing stops use only extremes of earlier bars; a gap through the stop
    fills at the open. The board exit leaves at the open after the first close
    whose rank is outside Top-n (or unranked).
    """
    o, h, l, c = sym["o"], sym["h"], sym["l"], sym["c"]
    e, H = i + 1, spec["hours"]
    nan = dict(gross=np.nan, net=np.nan, held=np.nan, ambiguous=False, upper=np.nan)
    if e + H > len(c) or not np.isfinite(o[e]):
        return nan
    window = np.r_[o[e:e+H], h[e:e+H], l[e:e+H], c[e:e+H]]
    if not np.isfinite(window).all():
        return nan
    entry, sign = o[e], 1 if side == "long" else -1

    def ret(px):
        return px / entry - 1 if side == "long" else 1 - px / entry

    amb, upper = False, None
    if spec["family"] == "time":
        px, held = c[e+H-1], H
    elif spec["family"] == "tpsl":
        try:
            r = resolve_barrier_outcome(
                sym["frame"], side=side, entry_i=e, entry_price=entry, atr=entry * .01,
                tp_atr_mult=spec["tp"], sl_atr_mult=spec["sl"], horizon_bars=H,
                same_bar_policy="conservative_sl", gap_policy="barrier_price",
                return_convention="linear_long" if side == "long" else "linear_short",
                allow_partial=False)
        except OutcomeContractError:
            return nan
        g = r.gross_ret
        amb = r.outcome == "sl_ambiguous"
        upper = spec["tp"] / 100 if amb else g
        return dict(gross=g, net=g - cost, held=r.exit_offset, ambiguous=amb, upper=upper - cost)
    elif spec["family"] == "trail":
        k_frac = spec["trail"] / 100
        best = entry
        px, held = c[e+H-1], H
        for k in range(e, e + H):
            stop = best * (1 - k_frac) if side == "long" else best * (1 + k_frac)
            if sign * (o[k] - stop) <= 0:
                px, held = o[k], k - e + 1
                break
            if (side == "long" and l[k] <= stop) or (side == "short" and h[k] >= stop):
                px, held = stop, k - e + 1
                break
            best = max(best, h[k]) if side == "long" else min(best, l[k])
    else:
        px, held = c[e+H-1], H
        for k in range(e, e + H - 1):
            r = rank_col[k]
            if not np.isfinite(r) or r > spec["n"]:
                px, held = o[k+1], k - e + 1
                break
    g = ret(px)
    return dict(gross=g, net=g - cost, held=held, ambiguous=False, upper=g - cost)


def quiet_hours(rank_col, eligible_col, i, window, draws, rng):
    """Same-coin earlier off-board hours whose 48h path ends before the rank window."""
    lo, hi = i - window[0], i - window[1]
    pool = [p for p in range(max(lo, 0), hi + 1)
            if eligible_col[p] and np.isfinite(rank_col[p]) and rank_col[p] > 10]
    if not pool:
        return []
    return sorted(rng.choice(pool, min(draws, len(pool)), replace=False).tolist())


def summary(df, value="net"):
    """Event-weighted bp stats with UTC-day clustered inference."""
    d = df.dropna(subset=[value])
    if d.empty:
        return dict(n=0)
    days = pd.to_datetime(d.decision_time, utc=True).dt.strftime("%Y-%m-%d")
    out = dict(n=len(d), days=int(days.nunique()), mean_bp=float(d[value].mean() * 1e4),
               median_bp=float(d[value].median() * 1e4), win_percent=float(d[value].gt(0).mean() * 100),
               mean_held_h=float(d.held.mean()), ambiguous=int(d.ambiguous.sum()),
               upper_mean_bp=float(d.upper.mean() * 1e4),
               test_zero=inference(d[value] * 1e4, days))
    for ctrl in ["volmatch", "quiet"]:
        x = d.dropna(subset=[f"{ctrl}_net"])
        dd = pd.to_datetime(x.decision_time, utc=True).dt.strftime("%Y-%m-%d")
        out[f"vs_{ctrl}"] = dict(inference((x.net - x[f"{ctrl}_net"]) * 1e4, dd),
                                 n=len(x), control_mean_bp=float(x[f"{ctrl}_net"].mean() * 1e4))
    return out


def passes(dev, val, p):
    try:
        return bool(dev["mean_bp"] > 0 and val["mean_bp"] > 0 and val["test_zero"]["p"] < p
                    and val["vs_volmatch"]["excess_bp"] > 0 and val["vs_volmatch"]["p"] < p
                    and val["vs_quiet"]["excess_bp"] > 0 and val["vs_quiet"]["p"] < p)
    except (KeyError, TypeError):
        return False


def run(cfg, folder):
    if (folder / "delivery_receipt.json").exists():
        print("Cached completed study retained.")
        return
    for path in [Path(__file__), folder / "config.json", folder / "PROJECT_PLAN.md"]:
        name = str(path.relative_to(ROOT))
        if subprocess.check_output(["git", "show", "HEAD:" + name], cwd=ROOT) != path.read_bytes():
            raise RuntimeError("Commit builder and plan before evaluation")
    data, audit = load_panel(cfg, folder)
    for s in cfg["exclude"]:
        data.pop(s, None)
    end = min(data[r["symbol"]].index.max() for r in audit
              if r["source"] == "okx_history_candles_1H" and r["symbol"] not in DELISTED
              and r["symbol"] in data)
    start = pd.Timestamp(cfg["start"])
    index = pd.date_range(start - pd.Timedelta(days=9), end, freq="h")
    syms = sorted(data)
    panel = {k: pd.DataFrame({s: data[s][k].reindex(index) for s in syms}) for k in [*OHLC, "volume"]}
    eligible, r24, _, vol = rank_inputs(panel, cfg["rank_hours"], cfg["vol_min_bars"], cfg["vol_max_bars"])
    rank = ranks(r24)
    inst = json.loads((folder / cfg["instruments"]).read_text())["data"]
    meta = {x["instId"].replace("-", "_"): x for x in inst}
    listing = {s: int(meta[s]["listTime"]) for s in syms if s in meta and meta[s].get("listTime")}
    ctval = {s: float(meta[s]["ctVal"]) * float(meta[s].get("ctMult") or 1) for s in syms if s in meta}
    decision = index + pd.Timedelta(hours=1)
    fresh = fresh_events(rank, cfg["board_n"], cfg["fresh_hours"])
    window = (decision >= start) & (decision <= pd.Timestamp(cfg["last_decision"]))
    ii, jj = np.nonzero(fresh.to_numpy() & window[:, None])
    cols = list(rank.columns)
    sym = {s: dict(o=panel["open"][s].to_numpy(float), h=panel["high"][s].to_numpy(float),
                   l=panel["low"][s].to_numpy(float), c=panel["close"][s].to_numpy(float),
                   frame=pd.DataFrame({k: panel[k][s].to_numpy(float) for k in OHLC}))
           for s in syms}
    rank_np = {s: rank[s].to_numpy(float) for s in cols}
    elig_np = {s: eligible[s].to_numpy(bool) for s in cols}
    rng = np.random.default_rng(cfg["seed"])
    dev_cut, val_from = pd.Timestamp(cfg["dev_before"]), pd.Timestamp(cfg["val_from"])
    events = []
    for e_id, (i, j) in enumerate(sorted(zip(ii, jj))):
        s = cols[j]
        t = decision[i]
        pool = list(rank.iloc[i][rank.iloc[i] > 20].dropna().index)
        match = matched_vol(vol.iloc[i], [s], pool, cfg["vol_matches"])[s]
        quiet = quiet_hours(rank_np[s], elig_np[s], i, cfg["quiet_window"], cfg["quiet_draws"], rng)
        period = "dev" if t < dev_cut else ("val" if t >= val_from else "gap")
        events.append(dict(event=e_id, i=int(i), symbol=s, decision_time=str(t), period=period,
                           volmatch=",".join(match), quiet=",".join(map(str, quiet)),
                           **features(panel, r24, rank, listing, ctval, i, s)))
    ev = pd.DataFrame(events)
    ev.to_csv(folder / "events.csv", index=False)

    specs = exit_specs(cfg)
    rows = []
    for side in ["long", "short"]:
        for spec in specs:
            for r in ev.itertuples():
                x = path_exit(sym[r.symbol], r.i, side, spec, rank_np[r.symbol], cfg["round_trip_cost"])
                ctrl = {}
                for name, members in [("volmatch", [(m, r.i) for m in r.volmatch.split(",") if m]),
                                      ("quiet", [(r.symbol, int(p)) for p in r.quiet.split(",") if p])]:
                    vals = [path_exit(sym[m], p, side, spec, rank_np[m], cfg["round_trip_cost"])["net"]
                            for m, p in members]
                    vals = [v for v in vals if np.isfinite(v)]
                    ctrl[f"{name}_net"] = float(np.mean(vals)) if vals else np.nan
                rows.append(dict(side=side, cell=spec["name"], family=spec["family"],
                                 event=r.event, decision_time=r.decision_time, period=r.period,
                                 **x, **ctrl))
    ex = pd.DataFrame(rows)
    ex.to_csv(folder / "event_exits.csv.gz", index=False)

    # Stage A: dev-only exit choice per side, then every cell in both periods.
    cells, choice = [], {}
    for side in ["long", "short"]:
        dev_means = {}
        for spec in specs:
            g = ex[ex.side.eq(side) & ex.cell.eq(spec["name"])]
            stats = {p: summary(g[g.period.eq(p)]) for p in ["dev", "val"]}
            if stats["dev"].get("n", 0) >= cfg["min_dev_events_exit"]:
                dev_means[spec["name"]] = stats["dev"]["mean_bp"]
            cells.append(dict(side=side, cell=spec["name"], family=spec["family"], **{
                f"{p}_{k}": v for p in stats for k, v in stats[p].items()}))
        best = max(dev_means, key=lambda k: (dev_means[k], -[s["name"] for s in specs].index(k)))
        choice[side] = best
    cdf = pd.json_normalize(cells)
    cdf.to_csv(folder / "exit_cells.csv", index=False)
    spearman = {side: float(cdf[cdf.side.eq(side)][["dev_mean_bp", "val_mean_bp"]]
                            .corr(method="spearman").iloc[0, 1]) for side in ["long", "short"]}

    # Stage B: selection under each side's dev-chosen exit (and time16 for continuity).
    devmask = ev.period.eq("dev")
    sel_rows, sel_choice = [], {}
    for side in ["long", "short"]:
        for exit_name in dict.fromkeys([choice[side], "time16"]):
            g = ex[ex.side.eq(side) & ex.cell.eq(exit_name)].merge(ev[["event", *FEATURES]], on="event")
            dev_best = None
            for f in FEATURES:
                if f == "rank":
                    bucket = pd.cut(g[f], [0, 3, 6, 10], labels=["1-3", "4-6", "7-10"]).astype(str)
                    edges = [3, 6]
                else:
                    q = ev.loc[devmask, f].quantile([1 / 3, 2 / 3]).to_numpy()
                    edges = q.tolist()
                    bucket = pd.Series(np.where(g[f].isna(), "nan", np.where(
                        g[f] <= q[0], "low", np.where(g[f] <= q[1], "mid", "high"))), index=g.index)
                for b in sorted(bucket.unique()):
                    if b == "nan":
                        continue
                    sub = g[bucket.eq(b)]
                    stats = {p: summary(sub[sub.period.eq(p)]) for p in ["dev", "val"]}
                    sel_rows.append(dict(side=side, exit=exit_name, feature=f, bucket=b,
                                         edges=json.dumps([round(x, 6) for x in edges]), **{
                                             f"{p}_{k}": v for p in stats for k, v in stats[p].items()}))
                    n_dev = stats["dev"].get("n", 0)
                    if exit_name == choice[side] and n_dev >= cfg["min_dev_events_select"]:
                        key = (stats["dev"]["mean_bp"], f, b)
                        if dev_best is None or key[0] > dev_best[0]:
                            dev_best = key
            if exit_name == choice[side]:
                sel_choice[side] = dict(feature=dev_best[1], bucket=dev_best[2], dev_mean_bp=dev_best[0])
    sdf = pd.json_normalize(sel_rows)
    sdf.to_csv(folder / "select_cells.csv", index=False)

    verdict = {}
    for side in ["long", "short"]:
        a = cdf[cdf.side.eq(side) & cdf.cell.eq(choice[side])].iloc[0]
        b = sdf[sdf.side.eq(side) & sdf.exit.eq(choice[side]) & sdf.feature.eq(sel_choice[side]["feature"])
                & sdf.bucket.eq(sel_choice[side]["bucket"])].iloc[0]
        g = ex[ex.side.eq(side) & ex.cell.eq(choice[side])]
        dev_a, val_a = summary(g[g.period.eq("dev")]), summary(g[g.period.eq("val")])
        sub = g.merge(ev[["event", *FEATURES]], on="event")
        f, bk = sel_choice[side]["feature"], sel_choice[side]["bucket"]
        edges = json.loads(b.edges)
        if f == "rank":
            m = pd.cut(sub[f], [0, 3, 6, 10], labels=["1-3", "4-6", "7-10"]).astype(str).eq(bk)
        else:
            m = pd.Series(np.where(sub[f].isna(), "nan", np.where(sub[f] <= edges[0], "low", np.where(
                sub[f] <= edges[1], "mid", "high"))), index=sub.index).eq(bk)
        dev_b, val_b = summary(sub[m & sub.period.eq("dev")]), summary(sub[m & sub.period.eq("val")])
        verdict[side] = dict(exit=choice[side], exit_pass=passes(dev_a, val_a, cfg["pass_p"]),
                             exit_dev=dev_a, exit_val=val_a, select=sel_choice[side],
                             select_pass=passes(dev_b, val_b, cfg["pass_p"]),
                             select_dev=dev_b, select_val=val_b)
    dump(folder / "verdict.json", dict(choice=choice, select=sel_choice, spearman=spearman,
                                       verdict=verdict))
    dump(folder / "delivery_receipt.json", dict(
        config=cfg, source_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                                           text=True).strip(),
        reused=["eth_h1_gainers_mfe16.load_panel/rank_inputs/matched_vol",
                "contracts.outcomes.resolve_barrier_outcome", "imacd_startup_research.inference"],
        symbols=len(syms), panel=[str(index[0]), str(index[-1])], events=len(ev),
        events_by_period=ev.period.value_counts().to_dict(), exit_cells=len(specs),
        choice=choice, select=sel_choice, spearman=spearman,
        passes={s: [verdict[s]["exit_pass"], verdict[s]["select_pass"]] for s in verdict},
        production_eligible=False, training_eligible=False,
        limitations=[
            "About three months, two ~6-week halves; independent overlapping events, not an account.",
            "Universe is the 2026-10-21 live crypto list plus three cached delisted names, not a census.",
            "1H bars: same-bar TP/SL resolved stop-first; upper bound reported separately.",
            "No funding, spread, depth or slippage beyond 20bp; ctVal is the current snapshot.",
            "Board exits applied literally to controls (off-board controls leave after one bar)."]))
    print(json.dumps(dict(events=len(ev), choice=choice, select=sel_choice)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=EXP / "config.json")
    args = parser.parse_args()
    run(json.loads(args.config.read_text()), args.config.parent)
