"""First 12H wick through a known monthly rail, reclaimed at that 12H close.
Decision: the next completed 1H closes strictly further inward than the 12H
close. Execute at the following H1 open. No wick-length or future result filter.
Control: all other first reclaims measured at the same one-hour decision lag.
Reference: the previous study's seeded market-time matcher. Both use the same
fill-relative 5% stop, rail-relative 20% target, 168h horizon and fees as H4.
"""
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
H4 = HERE.parent / "exp-month-firstweek-h4-rail-target-20261112-v1"
V1 = HERE.parent / "exp-month-firstweek-h4-strategy-20261112-v1"
REPLAY = HERE.parent / "exp-month-firstweek-all-replay-20261111-v1"
sys.path[:0] = [str(H4), str(V1), str(REPLAY)]
from replay import CUTOFF, HOUR, OUT, WINDOW, read_raw
from compare_fills import intervals, bootstrap

DEST = OUT / "h1_launch"
LABEL = {"short":"上轨首小时启动", "long":"下轨首小时启动"}


def decision(prefix, event):
    """Only 13 closed bars [UTC open_ms, O, H, L, C] including the touching 12H."""
    start = event.touch_ms // (12 * HOUR) * (12 * HOUR)
    if len(prefix) != 13 or not np.array_equal(prefix[:,0], start + np.arange(13)*HOUR):
        return "incomplete_prefix"
    touch = prefix[(event.touch_ms-start)//HOUR]
    if not touch[3] <= event.rail <= touch[2]:
        raise ValueError(f"Frozen rail is outside the recorded first touch for {event.id}")
    sign = 1 if event.side == "long" else -1
    if sign * (prefix[11,4] - event.rail) <= 0:
        return "no_12h_reclaim"
    return "signal" if sign * (prefix[12,4] - prefix[11,4]) > 0 else "stalled"


def extract():
    # The matcher is shared with the frozen native baseline. Import only in its venv.
    from baseline import match_index

    ids = pd.read_csv(OUT / "group_first_watch.csv.gz", float_precision="round_trip",
                      usecols=["id","symbol","month","side","touch_ms","rail"])
    assert ids.id.is_unique and not ids.duplicated(["symbol","month","side"]).any()
    watches, orders = [], []
    for symbol, group in ids.groupby("symbol"):
        bars = read_raw(symbol)
        bars = bars[bars[:,0] + HOUR <= CUTOFF]
        clock = bars[:,0].astype(np.int64)
        vol = pd.Series((bars[:,2]-bars[:,3])/bars[:,1]).rolling(24).mean().shift().to_numpy()
        allowed = np.zeros(len(bars),dtype=bool)
        allowed[24:] = clock[24:] - clock[:-24] == 24 * HOUR
        for event in group.itertuples(index=False):
            start = event.touch_ms // (12*HOUR) * (12*HOUR)
            when = start + 13*HOUR
            a, b = clock.searchsorted([start, when])
            prefix = bars[a:b]
            watch = dict(pair_id=event.id, symbol=symbol, side=event.side, source_month=event.month,
                         touch_ms=event.touch_ms, decision_ms=when, rail=event.rail)
            watches.append(watch)
            if when > CUTOFF or when >= WINDOW[event.month][2]:
                watch["status"] = "decision_after_cutoff_or_expiry"
                continue
            status = decision(prefix, event)
            watch["status"] = status
            if status not in ("signal","stalled"):
                continue
            watch.update(reclaim_close=prefix[11,4], first_hour_close=prefix[12,4])
            if b == len(bars) or clock[b] != when:
                watch["execution"] = "missing_next_open"
                continue
            picks = [(status, b)]
            if status == "signal":
                request = SimpleNamespace(month=event.month, id=event.id, decision_ms=when)
                ref = match_index(request,bars,b,allowed,vol)
                watch["has_random"] = ref is not None
                if ref is not None:
                    picks.append(("random",ref))
            sign = 1 if event.side == "long" else -1
            target = event.rail * (1+sign*0.2)
            for sample, idx in picks:
                entry = bars[idx,1]
                eligible = sign*(target-entry)>0
                if sample == status:
                    watch["execution"] = "entered" if eligible else "target_at_or_behind_open"
                orders.append(dict(pair_id=event.id, trade_id=f"{sample}/{event.id}",
                                   sample=sample, symbol=symbol, side=event.side,
                                   group=LABEL[event.side] if sample!='stalled' else '首小时未启动',
                                   source_month=event.month, rail=event.rail, target_px=target,
                                   entry_ms=clock[idx], entry_px=entry, qty=max(1,int(1000/entry)),
                                   pre24_range_pct=vol[idx]*100, eligible=eligible))
    DEST.mkdir(parents=True,exist_ok=True)
    pd.DataFrame(watches).to_csv(DEST/"decisions.csv",index=False)
    pd.DataFrame(orders).to_csv(DEST/"opportunities.csv",index=False)
    print(pd.DataFrame(watches).groupby(["status","execution"],dropna=False).size().to_string(),flush=True)


def trade():
    from rail_target import HOLD, exit_for_window, excursions, portfolio_ledger
    requests = pd.read_csv(DEST/"opportunities.csv",float_precision="round_trip")
    rows = []
    for symbol, group in requests[requests.eligible].groupby("symbol"):
        raw = read_raw(symbol)
        raw = raw[raw[:,0]+HOUR <= CUTOFF]
        for r in group.itertuples(index=False):
            start = raw[:,0].searchsorted(r.entry_ms)
            window = raw[start:start+HOLD]
            assert window[0,0]==r.entry_ms and window[0,1]==r.entry_px
            gaps = np.flatnonzero(np.diff(window[:,0])!=HOUR)
            if len(gaps):
                window = window[:gaps[0]+1]
            sign = 1 if r.side=="long" else -1
            i, phase, reason, fill = exit_for_window(window,sign,r.entry_px,r.target_px)
            mfe, mae = excursions(window,i,phase,sign,r.entry_px,fill)
            stop = r.entry_px*(1-sign*.05)
            double = window[i,2]>=max(stop,r.target_px) and window[i,3]<=min(stop,r.target_px)
            record = dict(r._asdict(),exit_px=fill,exit_hour=i+1,exit_phase=phase,exit_reason=reason,
                          exit_ms=int(window[i,0]+(HOUR if phase else 0)),mfe_pct=mfe,mae_pct=mae,
                          both_barriers=int(double))
            rows.append(record)
    frame = portfolio_ledger(pd.DataFrame(rows).sort_values(["entry_ms","symbol","trade_id"]))
    assert frame.trade_id.is_unique and len(frame)==int(requests.eligible.sum())
    for name, data in [("fills",frame[frame['sample']!="stalled"]),
                       ("stalled_control",frame[frame['sample']=="stalled"])]:
        tmp = DEST / f"{name}.tmp"
        data.to_csv(tmp,index=False,float_format="%.17g")
        tmp.replace(DEST/f"{name}.csv")
    summary(frame)
    photos(frame)


def summary(frame):
    closed = frame[frame.exit_reason!='cutoff_mark']
    records = []
    for period, chunk in intervals(closed):
        for side in ["all","short","long"]:
            subset = chunk if side=="all" else chunk[chunk.side==side]
            signal = subset[subset['sample']=="signal"]
            ref = subset[subset['sample']=="random"]
            pairs = signal.merge(ref,on="pair_id",suffixes=("_s","_r"),validate="one_to_one")
            diff = pairs.net_pct_s-pairs.net_pct_r
            bounds = bootstrap(diff,pairs.source_month_s)
            for sample, data in subset.groupby("sample"):
                net = data.net_pct
                row = dict(period=period,side=side,sample=sample,settled=len(data),win_pct=100*net.gt(0).mean(),
                           mean_net_pct=net.mean(),pf=net[net>0].sum()/(-net[net<0].sum()),
                           target=data.exit_reason.eq("target").sum(),stop=data.exit_reason.eq("stop").sum(),
                           timeout=data.exit_reason.eq("timeout").sum(),median_mfe_pct=data.mfe_pct.median(),
                           median_mae_pct=data.mae_pct.median(), end_date=data.exit_ms.max())
                if sample=="signal":
                    row.update(pairs=len(pairs),matched_signal_pct=pairs.net_pct_s.mean(),
                               matched_random_pct=pairs.net_pct_r.mean(),delta_pp=diff.mean(),
                               month_ci_low=bounds[0],month_ci_high=bounds[1])
                records.append(row)
    table = pd.DataFrame(records)
    table.to_csv(HERE/"trade_summary.csv",index=False)
    print(table[(table.period=="全期")].round(3).to_string(index=False),flush=True)


def photos(frame):
    reference = pd.read_csv(H4/"photo_coverage.csv")[["symbol","month","side","image","pair_id","result"]]
    ref = reference.rename(columns={"result":"h4_status"})
    decisions = pd.read_csv(DEST/"decisions.csv")[["pair_id","status","execution"]]
    joined = ref.merge(decisions,how="left",on="pair_id",validate="one_to_one")
    joined = joined.merge(frame[frame['sample']=="signal"][["pair_id","net_pct","exit_reason","entry_ms","mfe_pct","mae_pct"]],
                          how="left",on="pair_id",validate="one_to_one")
    joined.to_csv(HERE/"photo_coverage.csv",index=False)
    print(joined[["symbol","h4_status","status","execution","net_pct"]].to_string(index=False))


if __name__ == "__main__":
    {"signals":extract,"trades":trade}[sys.argv[1]]()
