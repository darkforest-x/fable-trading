"""Offline ETH stop-sensitivity ledger for frozen SPIKE V1 and V6.

This is research-only.  It reads the receipt-backed OKX 30-minute ETH swap
file from the 2026-09-11 V1 experiment and never calls an exchange, monitor,
model, or order API.  V1 signals come from the byte-pinned original replay.
V6 uses the pinned closed-bar structure oracle with a generic-timeframe
translation of the V6 Pine's retained V4 provenance state.  The translation is
kept here because the existing PEPE helper is deliberately 1H-only.

All execution candidates enter the next observed open.  A fixed 20bp round
trip cost is deducted.  A stop that gaps through fills at that open; otherwise
the previously-close-known stop is checked before high/MFE and a ratchet made
at close applies only from the next bar.  Net R always divides by actual
next-open-to-initial-stop risk.  No funding, spread, impact, leverage or
portfolio allocation is modelled.
"""
from __future__ import annotations

import hashlib, json, math, sys
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from yoyo.evaluation.spike_burst_replay import features, replay
from yoyo.evaluation.spike_burst_progressive import progressive_fields
from yoyo.evaluation.spike_burst_v6_structure import detect as v6_detect

EXP = Path(__file__).resolve().parent
OUT = EXP / "results"
SOURCE = ROOT / "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/data/normalized/okx/ETH-USDT-SWAP_30m.csv.gz"
OLD_LEDGER = ROOT / "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1/results/covered_trade_ledger.csv.gz"
START, END = pd.Timestamp("2024-09-10T00:00Z"), pd.Timestamp("2026-09-10T00:00Z")
DEV_END = pd.Timestamp("2025-09-10T00:00Z")
TICK, COST = 0.01, 0.002
INITIAL_FLOORS, TRAIL_ATRS = (1.5, 2.0, 2.5, 3.0), (3.0, 4.0, 5.0)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_source() -> pd.DataFrame:
    frame = pd.read_csv(SOURCE, index_col=0, parse_dates=True)
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True))
    return frame


def aggregate(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if minutes == 30:
        return frame.copy()
    g = frame.resample(f"{minutes}min", origin="epoch", closed="left", label="left")
    out = g.agg({"open":"first", "high":"max", "low":"min", "close":"last", "volume":"sum", "quote_volume":"sum"})
    return out.loc[g.size().eq(minutes // 30)]


def generic_v4(frame: pd.DataFrame, side: int) -> pd.DataFrame:
    """Exact V6 Pine retained legacy state, using the frame's actual step."""
    p = progressive_fields(frame)
    dense = frame.ready.eq(True) & frame.pastWidth.le(3.0) & frame.pastCrosses.ge(2.0)
    recent_dense = dense.astype(float).shift().rolling(12, min_periods=12).sum().gt(0)
    high = frame.high.shift().rolling(12, min_periods=12).max()
    low = frame.low.shift().rolling(12, min_periods=12).min()
    fast_high, fast_low = frame[["s20", "e20"]].max(axis=1), frame[["s20", "e20"]].min(axis=1)
    early = frame.ready.eq(True) & (frame.close.gt(high) if side == 1 else frame.close.lt(low)) & (frame.close.gt(fast_high) if side == 1 else frame.close.lt(fast_low))
    quality = (frame.ready.eq(True) & recent_dense & (p.prog_advance.ge(1.5) if side == 1 else p.prog_advance.le(-1.5))
               & p.prog_volume_ratio.ge(1.5) & (frame.md.ge(frame.sb) if side == 1 else frame.md.le(frame.sb))
               & (frame.middle.gt(frame.middle.shift()) if side == 1 else frame.middle.lt(frame.middle.shift())))
    previous = False; last = parent = None; parent_high = parent_low = np.nan; parent_done = False; rows=[]
    for i in range(len(frame)):
        edge = bool(early.iloc[i] and not previous); accepted = edge and (last is None or i-last >= 12)
        if accepted:
            last=parent=i; parent_high=float(high.iloc[i]); parent_low=float(low.iloc[i]); parent_done=False
        age = None if parent is None else i-parent
        if age is not None and age > 3:
            parent=None; parent_high=parent_low=np.nan; parent_done=False; age=None
        confirms = bool(parent is not None and not parent_done and 0 <= age <= 3 and quality.iloc[i]
                        and (frame.close.iloc[i] > parent_high if side == 1 else frame.close.iloc[i] < parent_low))
        if confirms: parent_done=True
        rows.append(dict(legacy_confirmed=confirms, legacy_parent_high=parent_high if confirms else np.nan,
                         legacy_parent_low=parent_low if confirms else np.nan))
        previous=bool(early.iloc[i])
    return pd.DataFrame(rows,index=frame.index)


def v6_signals(frame: pd.DataFrame, side: int) -> pd.Series:
    legacy=generic_v4(frame,side); p=progressive_fields(frame)
    supplied=frame[["open","high","low","close","md","sb","atr","ropeHigh","ropeLow","ready"]].copy()
    supplied["legacy_confirmed"]=legacy.legacy_confirmed.astype(bool)
    supplied["legacy_parent_high"]=legacy.legacy_parent_high; supplied["legacy_parent_low"]=legacy.legacy_parent_low
    supplied["advance3"]=p.prog_advance; supplied["volume_ratio3"]=p.prog_volume_ratio
    supplied["data_gap"]=False; supplied["confirmed"]=True
    return v6_detect(supplied,side=side).confirmed


def simulate(bars: pd.DataFrame, signal_i: int, side: int, floor_atr: float=2.0, trail_atr: float=4.0) -> dict:
    """One causal next-open event; all post-entry data only moves protection up."""
    i=signal_i; step=bars.index[1]-bars.index[0]; close=float(bars.close.iloc[i]); atr=float(bars.atr.iloc[i])
    extreme=float((bars.low if side==1 else bars.high).iloc[max(0,i-4):i+1].min() if side==1 else (bars.high.iloc[max(0,i-4):i+1].max()))
    raw=min(extreme-.2*atr,close-floor_atr*atr) if side==1 else max(extreme+.2*atr,close+floor_atr*atr)
    stop=(math.floor(raw/TICK)*TICK if side==1 else math.ceil(raw/TICK)*TICK)
    entry=float(bars.open.iloc[i+1]); risk=side*(entry-stop)
    row=dict(signal_bar_open=bars.index[i], signal_close_time=bars.index[i]+step, side=side, entry_time=bars.index[i+1], entry_price=entry,
             initial_stop=stop, risk_fraction_at_entry=risk/entry if risk>0 else np.nan, floor_atr=floor_atr, trail_atr=trail_atr)
    if not np.isfinite(risk) or risk<=0:
        return dict(row, valid=False, exit_reason="entry_at_or_through_stop", censored=False)
    prot=stop; armed=False; peak=0.; initial_hit=trail_hit=0
    for j in range(i+1,len(bars)):
        b=bars.iloc[j]; o,h,l,c=map(float,(b.open,b.high,b.low,b.close))
        touched = l<=prot if side==1 else h>=prot
        if touched:
            exit_price=min(o,prot) if side==1 else max(o,prot)
            reason=("trailing_stop" if (prot>stop if side==1 else prot<stop) else "initial_stop") + ("_gap" if (o<=prot if side==1 else o>=prot) else "")
            initial_hit += reason.startswith("initial"); trail_hit += reason.startswith("trailing")
            exit_time=bars.index[j] if reason.endswith("_gap") and j==i+1 else bars.index[j]+step
            break
        peak=max(peak,side*(h-entry)/risk if side==1 else side*(l-entry)/risk)
        armed=armed or side*(c-entry)/risk>=2.0
        if j==len(bars)-1:
            exit_price=c; reason="boundary_mark"; exit_time=bars.index[j]+step; break
        a=float(b.atr)
        if armed and np.isfinite(a) and a>0:
            raw=c-side*trail_atr*a; candidate=math.floor(raw/TICK)*TICK if side==1 else math.ceil(raw/TICK)*TICK
            prot=max(prot,candidate) if side==1 else min(prot,candidate)
    net=side*(exit_price/entry-1)-COST
    follow=bars.iloc[j+1:min(len(bars),j+21)]
    post=np.nan
    if len(follow)==20:
        post=(float(follow.high.max())/entry-1 if side==1 else entry/float(follow.low.min())-1)
    return dict(row,valid=True,exit_time=exit_time,exit_price=exit_price,exit_reason=reason,censored=reason=="boundary_mark",net_return=net,net_r=net/(risk/entry),peak_r=peak,trail_armed=armed,post_stop_20bar_mfe_return=post,initial_stop_hit=initial_hit, trailing_stop_hit=trail_hit)


def stop_plan(bars: pd.DataFrame, signal_i: int, side: int=1, floor_atr: float=2.0, trail_atr: float=4.0) -> list[dict]:
    """Pre-close-known V1 stops for the Freqtrade baseline bridge.

    The returned stop for a bar is the protection that was already operative at
    that bar's open.  This explicit one-bar lag prevents Freqtrade's callback
    (which sees a candle high/low) from moving a stop at the close and testing
    it against the same candle's opposite extreme.
    """
    i=signal_i; entry_time=bars.index[i+1]; close=float(bars.close.iloc[i]); atr=float(bars.atr.iloc[i])
    extreme=float(bars.low.iloc[max(0,i-4):i+1].min())
    stop=math.floor(min(extreme-.2*atr,close-floor_atr*atr)/TICK)*TICK; entry=float(bars.open.iloc[i+1]); risk=entry-stop
    if risk<=0: return []
    protection=stop; armed=False; out=[]
    for j in range(i+1,len(bars)):
        b=bars.iloc[j]; o,h,l,c=map(float,(b.open,b.high,b.low,b.close))
        out.append(dict(entry_time=entry_time,current_time=bars.index[j],active_stop=protection))
        if l<=protection: break
        armed=armed or (c-entry)/risk>=2.0
        if armed and j<len(bars)-1 and np.isfinite(float(b.atr)) and float(b.atr)>0:
            protection=max(protection,math.floor((c-trail_atr*float(b.atr))/TICK)*TICK)
    return out


def rows_for(strategy: str, minutes: int, signals: Iterable[tuple[int,int]], bars: pd.DataFrame, floor: float, trail: float) -> pd.DataFrame:
    rows=[]
    for i,side in signals:
        if i+1>=len(bars) or not (START<=bars.index[i+1]<END): continue
        r=simulate(bars,i,side,floor,trail); r.update(strategy=strategy,timeframe_min=minutes)
        rows.append(r)
    return pd.DataFrame(rows)


def summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows=[]
    for keys,g in frame.groupby(["strategy","variant","split","timeframe_min","side"],dropna=False):
        r=g.loc[~g.censored.astype(bool)].copy(); v=r.net_r.astype(float); prof=v[v>0].sum(); loss=-v[v<0].sum(); curve=np.r_[0.,v.to_numpy().cumsum()]; dd=float((curve-np.maximum.accumulate(curve)).min())
        rows.append(dict(zip(["strategy","variant","split","timeframe_min","side"],keys),n=len(r),wins=int((v>0).sum()),win_rate=(v>0).mean() if len(v) else np.nan,net_r=v.sum(),pf=prof/loss if loss else np.nan,event_sequence_drawdown_r=dd,initial_stop_pct=r.initial_stop_hit.mean() if len(r) else np.nan,trailing_stop_pct=r.trailing_stop_hit.mean() if len(r) else np.nan,post_initial_stop_20bar_mfe=r.loc[r.initial_stop_hit.eq(1),"post_stop_20bar_mfe_return"].mean()))
    return pd.DataFrame(rows)


def main() -> None:
    OUT.mkdir(parents=True,exist_ok=True)
    source=read_source(); allrows=[]; manifest={"source":str(SOURCE),"source_sha256":sha(SOURCE),"start":str(START),"end":str(END),"tick":TICK,"cost":COST,"v6_pine_sha256":sha(ROOT/'yoyo/evaluation/pine/spike_burst_v6.pine'),"v6_oracle_sha256":sha(ROOT/'yoyo/evaluation/spike_burst_v6_structure.py')}
    for minutes in (30,60,240):
        bars=aggregate(source,minutes); f=features(bars); default=replay(f,TICK)
        v1=[(int(i),1) for i in np.flatnonzero(default.burst.to_numpy())]
        v6long=[(int(i),1) for i in np.flatnonzero(v6_signals(f,1).to_numpy())]
        v6short=[(int(i),-1) for i in np.flatnonzero(v6_signals(f,-1).to_numpy())]
        for strategy,signals in (("v1",v1),("v6_long",v6long),("v6_both",sorted(v6long+v6short))):
            base=rows_for(strategy,minutes,signals,f,2.,4.); base["variant"]="baseline"; allrows.append(base)
            for floor in INITIAL_FLOORS:
                z=rows_for(strategy,minutes,signals,f,floor,4.); z["variant"]=f"initial_floor_{floor:.1f}"; allrows.append(z)
            for trail in TRAIL_ATRS:
                z=rows_for(strategy,minutes,signals,f,2.,trail); z["variant"]=f"trail_atr_{trail:.1f}"; allrows.append(z)
    ledger=pd.concat(allrows,ignore_index=True); ledger["split"]=np.where(pd.to_datetime(ledger.entry_time,utc=True)<DEV_END,"development","later_validation")
    ledger.to_csv(OUT/'eth_stop_sensitivity_ledger.csv.gz',index=False,compression={"method":"gzip","mtime":0})
    summary(ledger).to_csv(OUT/'eth_stop_sensitivity_summary.csv',index=False)
    # strict V1 baseline parity to the existing frozen executable ledger.
    old=pd.read_csv(OLD_LEDGER); old=old[(old.venue=='okx')&(old.symbol=='ETH-USDT-SWAP')&(old.timeframe_min.isin([30,60,240]))].copy()
    new=ledger[(ledger.strategy=='v1')&(ledger.variant=='baseline')].copy(); new['event_id']=[hashlib.sha256(f"okx|ETH-USDT-SWAP|{int(r.timeframe_min)}|{pd.Timestamp(r.signal_bar_open).isoformat()}".encode()).hexdigest() for r in new.itertuples()]
    m=new.merge(old[['event_id','entry_time','entry_price','exit_time','exit_price','net_r','net_return']],on='event_id',suffixes=('_new','_old'),how='outer',indicator=True)
    same=m._merge.eq('both') & np.isclose(m.entry_price_new,m.entry_price_old,equal_nan=True) & np.isclose(m.exit_price_new,m.exit_price_old,equal_nan=True) & np.isclose(m.net_r_new,m.net_r_old,equal_nan=True)
    receipt=dict(manifest,generated_at=pd.Timestamp.now(tz='UTC').isoformat(),v1_old_rows=len(old),v1_new_rows=len(new),v1_exact_matches=int(same.sum()),v1_parity_pass=bool(len(m)==len(old)==len(new) and same.all()), grids={"initial_floor_atr":INITIAL_FLOORS,"trail_atr":TRAIL_ATRS}, development_end=str(DEV_END), position_model="independent frozen default signal events; no allocation/account MDD")
    # Framework bridge inputs: the V1 input signal bars and each next-open
    # active stop.  They are derived after the frozen replay, never from a
    # shortened Freqtrade startup window.
    ft=EXP/'freqtrade'; (ft/'plans').mkdir(parents=True,exist_ok=True)
    for minutes in (30,60,240):
        bars=features(aggregate(source,minutes)); signals=np.flatnonzero(replay(bars,TICK).burst.to_numpy())
        sigrows=[]
        plans={"baseline": []} | {f"initial_{x:.1f}": [] for x in INITIAL_FLOORS} | {f"trail_{x:.1f}": [] for x in TRAIL_ATRS}
        for i in signals:
            if i+1<len(bars) and START<=bars.index[i+1]<END:
                sigrows.append(dict(signal_bar_open=bars.index[i],entry_time=bars.index[i+1],side='long'))
                plans["baseline"].extend(stop_plan(bars,int(i)))
                for x in INITIAL_FLOORS: plans[f"initial_{x:.1f}"].extend(stop_plan(bars,int(i),floor_atr=x))
                for x in TRAIL_ATRS: plans[f"trail_{x:.1f}"].extend(stop_plan(bars,int(i),trail_atr=x))
        pd.DataFrame(sigrows).to_csv(ft/'plans'/f'v1_signals_{minutes}m.csv',index=False)
        for name, rows in plans.items(): pd.DataFrame(rows).to_csv(ft/'plans'/f'v1_stops_{minutes}m_{name}.csv',index=False)
    (OUT/'manifest.json').write_text(json.dumps(receipt,indent=2,default=str),encoding='utf8')
    if not receipt['v1_parity_pass']: raise SystemExit('V1 baseline mismatch; do not optimize')


if __name__ == '__main__': main()
