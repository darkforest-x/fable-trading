"""Prior-day trend at the frozen OKX Monday first touches.

Trend uses closes of the last completed UTC 4H bar at the touch 5m open and
the bar six periods earlier. Magnitude uses the most recent 14 complete 4H
true ranges. Touch-bar close determines recovery state at the decision. Only
the direction label uses the 5m close exactly 4h after that decision. Existing
12h native fills are joined separately without computing any new executions.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("experiments/active/exp-monday-pa-prior24h-20261108-v1")
BAR = pd.Timedelta(minutes=5)


def four_hour_context(five):
    grouped = five.resample("4h", label="right", closed="left")
    bars = grouped.agg({"high": "max", "low": "min", "close": "last"}).where(grouped.size().eq(48), axis=0)
    previous = bars.close.shift()
    true_range = pd.concat([bars.high-bars.low, (bars.high-previous).abs(),
                            (bars.low-previous).abs()], axis=1).max(axis=1).where(previous.notna())
    bars["atr"] = true_range.rolling(14).mean()
    bars["day_ago"] = bars.close.shift(6)
    return bars


def event_data(events, cfg, symbol):
    source = Path("experiments/active") / cfg["archive"] / symbol
    f = pd.read_csv(Path("data/crypto/market") / cfg["archive"] / symbol / "candles.csv.gz",
                    usecols=["open_time", "high", "low", "close"], parse_dates=["open_time"]).set_index("open_time")
    if not f.index.to_series().diff().iloc[1:].eq(BAR).all():
        raise ValueError("Discontinuous 5m archive")
    bars = four_hour_context(f)
    pos = pd.read_csv(source / "positions.csv", parse_dates=["entry_time"])
    pos = pos[pos.population.eq("signal")].set_index("event_id", verify_integrity=True)
    pairs = pd.read_csv(source / "matched_pairs.csv").set_index("event_id", verify_integrity=True)
    results = []
    for e in events.itertuples():
        t = e.touch_open
        side = 1 if e.side == "lower" else -1
        b = bars.loc[t.floor("4h")]
        now = t + BAR
        end = now + pd.Timedelta(hours=4)
        expires = pd.Timestamp(e.week, tz="America/New_York") + pd.DateOffset(days=7)
        row = dict(symbol=symbol, week=e.week, side=e.side, touch_open=t, decision=now, label_at=end,
                   context_close_at=b.name, context_close=b.close, prior_close=b.day_ago, atr=b.atr,
                   close_inside=bool(side*(e.touch_close-e.level)>0), touch_close=e.touch_close,
                   period="2026" if now >= pd.Timestamp(cfg["split"]) else "2024-25")
        results.append(row)
        if pd.isna(b.day_ago) or pd.isna(b.close) or b.close == b.day_ago:
            row["status"] = "missing_or_flat_trend"
            continue
        move = side*(b.close-b.day_ago)
        row.update(aligned=bool(move>0), delta_over_atr=move/b.atr, atr_pct=100*b.atr/b.close)
        key = f"{symbol}:{e.week}:{side}"
        # The native engine stamps this bar at its close, while the archive indexes opens.
        if key in pos.index and f.index[pos.at[key,"entry_i"]] == t:
            if pos.at[key,"entry_time"] != now:
                raise ValueError(f"Native entry event clock mismatch for {key}")
            row.update(native_r=pos.at[key,"total_mark_r"], native_closed=bool(pos.at[key,"closed"]))
            if key in pairs.index:
                row["paired_excess_r"] = row["native_r"] - pairs.at[key,"random_r"]
        if end > expires or now < pd.Timestamp(cfg["split"]) <= end:
            row["status"] = "censored_4h"
            continue
        future = f.at[end-BAR,"close"]
        row.update(status="eligible", later_close=future, inward=bool(side*(future-e.touch_close)>0))
    return pd.DataFrame(results)


def compare(data, cfg):
    counts = pd.crosstab(data.week, [data.aligned, data.inward]).reindex(
        columns=pd.MultiIndex.from_product([[False, True],[False,True]]), fill_value=0).to_numpy()
    counts = counts.reshape(-1,2,2)
    totals = counts.sum(axis=0)
    result = {key: {"inward": int(b), "n": int(a+b), "rate": float(b/(a+b)) if a+b else None}
              for key,(a,b) in zip(["opposed","aligned"],totals)}
    result["difference_pp"] = result["ci95_pp"] = None
    if (totals.sum(axis=1)>0).all():
        result["difference_pp"] = 100*(result["aligned"]["rate"]-result["opposed"]["rate"])
    if len(counts) and (np.sum(counts.sum(axis=2)>0,axis=0)>=2).all():
        ix = np.random.default_rng(cfg["seed"]).integers(len(counts), size=(cfg["draws"],len(counts)))
        sampled = counts[ix].sum(axis=1)
        sampled = sampled[(sampled.sum(axis=2)>0).all(axis=1)]
        diff = np.diff(sampled[:,:,1]/sampled.sum(axis=2), axis=1).ravel()
        result["ci95_pp"] = np.percentile(100*diff,[2.5,97.5]).tolist()
    return result


def adjusted(df, cfg):
    eligible = df.dropna(subset=["atr_pct"]).copy()
    eligible["high_vol"] = eligible.atr_pct.ge(cfg["atr_pct_split"])
    diff, weights, n = [], [], 0
    for _, cell in eligible.groupby(["symbol","side","period","close_inside","high_vol"]):
        a, b = cell[cell.aligned.eq(True)], cell[cell.aligned.eq(False)]
        if len(a) and len(b):
            weights.append(len(a)*len(b)/len(cell))
            diff.append(a.inward.mean()-b.inward.mean())
            n += len(cell)
    return dict(n=n, cells=len(weights), diff_pp=float(100*np.average(diff,weights=weights)) if weights else None)


def archived_fills(df):
    result = {}
    for aligned, g in df.dropna(subset=["native_r"]).groupby("aligned"):
        pairs = g.paired_excess_r.dropna()
        result["aligned" if aligned else "opposed"] = dict(n=len(g), closed=int(g.native_closed.sum()),
            mean_marked_r=float(g.native_r.mean()), matched=len(pairs),
            mean_paired_excess_r=float(pairs.mean()) if len(pairs) else None)
    return result


def main():
    cfg = json.loads((OUT/"config.json").read_text())
    src = pd.read_csv(cfg["source_events"],parse_dates=["touch_open"]).dropna(subset=["touch_open"])
    events = pd.concat([event_data(g, cfg, s) for s,g in src.groupby("symbol")],ignore_index=True)
    assert len(events)==len(src) and not events.duplicated(["symbol","week","side"]).any()
    valid = events[events.status.eq("eligible")]
    summary = dict(raw=len(events), counts=events.status.value_counts().to_dict(), periods={})
    for period in ["all","2024-25","2026"]:
        df = valid if period=="all" else valid[valid.period.eq(period)]
        native = events if period=="all" else events[events.period.eq(period)]
        groups = {"both":df, "upper":df[df.side.eq("upper")], "lower":df[df.side.eq("lower")]}
        for symbol in ["BTC","ETH"]:
            groups[symbol] = df[df.symbol.eq(symbol)]
            groups[symbol+"_lower"] = df[df.symbol.eq(symbol)&df.side.eq("lower")]
        summary["periods"][period] = dict(direction={k:compare(v,cfg) for k,v in groups.items()},
            adjusted_lower=adjusted(groups["lower"],cfg), adjusted_both=adjusted(df,cfg),
            native_lower=archived_fills(native[native.side.eq("lower")]), native_both=archived_fills(native))
    events.to_csv(OUT/"events.csv.gz",index=False)
    (OUT/"summary.json").write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n")


if __name__=="__main__":
    main()
