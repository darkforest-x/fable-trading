"""One fixed completed-close trailing rule on the frozen population.

Signal identity, entry, initial stop, 72h horizon, unchanged exits and funding
convention come from the original study. Only past completed 5m closes set a
new effective stop. VectorBT first_hit matches orders, including opening gaps;
the archived original exit is an upper time bound because stops only tighten.
"""
from concurrent.futures import ThreadPoolExecutor
import json

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.evaluation import monday_reclaimed_wick_target_win as base
from yoyo.evaluation.monday_reclaimed_wick_mfe import ROOT, EXP as SOURCE, OCT_EXP, OHLC
from yoyo.evaluation.monday_wick_r_engine import first_hit

OUT = ROOT / "experiments/active/exp-monday-reclaimed-wick-trailing-20261115-v1"
ARM = .02
DISTANCE = .02


def earlier_exits(job):
    symbol, rows, paths, last_open = job
    bars = base.source_windows(rows,paths,last_open)
    arrays = [bars[k].to_numpy(dtype=float,copy=True)[:,None] for k in OHLC]
    n = len(bars)
    work = (np.empty((n,1)),np.empty((n,1),dtype=np.int64),np.empty(1,dtype=np.int64))
    out = []
    for r in rows.itertuples():
        start = bars.index.get_loc(r.open_ms)
        end = start + r.exit_i_source - r.entry_i_source
        if bars.index[end]+base.STEP != r.exit_ms or abs(arrays[3][start,0]-r.entry)>r.tick*.51:
            raise ValueError(f"Source window does not match frozen trade {r.event_id}")
        if np.any(np.diff(bars.index[start:end+1]) != base.STEP):
            raise ValueError(f"Gap in frozen trade {r.event_id}")
        closes = arrays[3][start+1:end,0]
        if not len(closes):
            continue
        best = np.maximum.accumulate(closes) if r.side==1 else np.minimum.accumulate(closes)
        candidates = best*(1-r.side*DISTANCE)/r.tick
        ticks = np.floor(candidates+1e-7) if r.side==1 else np.ceil(candidates-1e-7)
        levels = ticks*r.tick
        armed = (r.side*(best/r.entry-1)>=ARM-1e-12) & (r.side*(levels-r.stop)>r.tick*.1)
        changes = np.flatnonzero(armed & np.r_[True,(~armed[:-1]) | (ticks[1:]!=ticks[:-1])])
        # This schedule was derived from closes only. The native matcher sees
        # each installed level through the next change-candle, inclusive.
        for j, offset in enumerate(changes):
            installed = start+1+offset
            until = start+1+changes[j+1] if j+1<len(changes) else end
            hit,price,kind,_ = first_hit(*arrays,installed+1,until,arrays[3][installed,0],
                                         levels[offset],np.nan,r.side,*work)
            if kind:
                out.append(dict(event_id=r.event_id, entry=r.entry, side=r.side, exit=price,
                                exit_ms=int(bars.index[hit])+base.STEP, trigger=float(levels[offset])))
                break
    return out


def main():
    from pathlib import Path
    ledger = pd.read_csv(SOURCE/"trades.csv.gz")
    ledger["open_ms"] = base.epoch_ms(ledger.entry_open_time)
    ledger["exit_ms"] = base.epoch_ms(ledger.framework_exit_time)
    # Reusing impossible-to-arm outcomes cannot alter an entry or admission.
    upper = ledger[["mfe_pre_stop_pct","mfe_ohlc_favorable_first_pct","mfe_olhc_favorable_first_pct"]].max(axis=1)
    needed = ledger[upper>=100*ARM-1e-9]
    stamps = json.loads((SOURCE/"input_manifest.json").read_text())["source_stamps"]
    coverage = pd.read_csv(SOURCE/"bar_coverage.csv").set_index("symbol")
    jobs=[]
    for symbol,group in needed.groupby("symbol"):
        paths=[Path(p["path"]) for p in stamps[symbol]["bar_paths"]]
        last=pd.Timestamp(coverage.loc[symbol,"last_open_time"])
        if last>=pd.Timestamp("2026-09-24",tz="UTC"):
            paths.append(OCT_EXP/"symbols"/symbol/"new_bars.csv.gz")
        jobs.append((symbol,group,paths,last.value//1_000_000))
    improved=[]
    with ThreadPoolExecutor(max_workers=5) as pool:
        for i,found in enumerate(pool.map(earlier_exits,jobs),1):
            improved.extend(found)
            if i%60==0: print(i,"/",len(jobs),"symbols",flush=True)
    changed=pd.DataFrame(improved)
    prices=np.vstack([changed.entry,changed.exit])
    pf=vbt.Portfolio.from_orders(prices,size=np.vstack([changed.side,-changed.side]),price=prices,
         fees=.0005,init_cash=prices.max(axis=0)*4,direction="both",cash_sharing=False,raise_reject=True,allow_partial=False)
    rr=pf.trades.records.sort_values("col")
    assert len(rr)==len(changed) and rr.status.eq(1).all()
    changed["net_pct"]=rr.pnl.to_numpy()/changed.entry*100-.2
    orig=base.frozen_baseline(ledger).set_index("event_id")
    trail=orig.copy()
    trail["source"]="trail"
    revised=changed.set_index("event_id")
    for col in ("exit_ms","exit","net_pct"):
        trail.loc[revised.index,col]=revised[col]
    trail.loc[revised.index,"complete"]=True
    trail.loc[revised.index,"reason"]=9
    assert (trail.exit_ms<=orig.exit_ms).all()
    trades=pd.concat([trail,orig]).reset_index()
    trades.to_csv(OUT/"events.csv.gz",index=False)
    full,summaries=[],[]
    for name,start,end in (("2020-2024","2019","2025"),("2025","2025","2026"),("2026","2026","2027"),("2025+","2025","2027")):
        a,b=[pd.Timestamp(t,tz="UTC").value//1000000 for t in (start,end)]
        for src,g in trades[trades.entry_ms.between(a,b,inclusive="left")].groupby("source"):
            subset=base.select_single(g)
            full.append(subset.assign(period=name))
            summaries.append(dict(period=name,source=src,**base.stats(subset)))
    pd.concat(full).to_csv(OUT/"single_trades.csv.gz",index=False)
    pd.DataFrame(summaries).to_csv(OUT/"metrics.csv",index=False)
    (OUT/"audit.json").write_text(json.dumps(dict(total=len(orig),at_or_above_arm_excursion=len(needed),
                  native_trailing_exits=len(changed),unchanged_fills=len(orig)-len(changed),vectorbt=vbt.__version__),indent=2)+"\n")


if __name__=="__main__":
    main()
