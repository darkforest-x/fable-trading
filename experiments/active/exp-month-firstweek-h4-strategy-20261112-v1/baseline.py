"""Execute frozen H4 decisions at the next hourly open with NautilusTrader.

Signal fields are limited to identity, symbol, month, side group and decision
time from the previous full-population study. The 24h row is only one identity
per signal. Outcome, horizon completeness and future extremes are never read.
The matched control uses the same contract, direction, active NY range month
and UTC open hour. Randomly sample one of the three closest eligible dates
by the average H1 range / open in the 24 completed hours before that open.
"""
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT), str(HERE.parent / 'exp-month-firstweek-all-replay-20261111-v1')]
from replay import CUTOFF, HOUR, OUT, ROUNDTRIP, WINDOW, read_raw
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
from yoyo.evaluation.month_firstweek_reclaim_strategy import bracket

DEST = OUT / "h4_strategy"
HOLD = 168
MAKER, TAKER = .0002, .0005
SEED = 20261112


def price_grid(values):
    """Find engine precision for the supplied decimal prices, without rounding the orders."""
    for digits in range(15):
        tick = 10.0**-digits
        quotients = values/tick
        if np.max(np.abs(quotients - np.rint(quotients))) <= 1e-5:
            return tick
    raise ValueError(f"Cannot represent prices, range {np.min(values)} to {np.max(values)}")


def match_index(event, raw, index, allowed, volatility):
    clock = raw[:, 0].astype(np.int64)
    _, ready, expiry = WINDOW[event.month]
    by_hour = (clock // HOUR) % 24 == (event.decision_ms // HOUR) % 24
    candidates = np.flatnonzero(by_hour & (clock >= ready) & (clock < expiry) &
                                (clock != event.decision_ms) & allowed)
    if not allowed[index] or not len(candidates):
        return None
    distance = np.abs(volatility[candidates]-volatility[index])
    nearest = candidates[np.argsort(distance, kind="stable")[:3]]
    seed = hashlib.sha256(f"{SEED}/{event.id}".encode()).digest()[:8]
    return int(nearest[int.from_bytes(seed, 'big') % len(nearest)])


def within_trade(bars, side, phase, fill):
    """Mark extremes only along the stated synthetic path up to the closing fill."""
    prices = [bars[0, 1], fill]
    if len(bars) > 1:
        prices.extend([bars[:-1, 2].max(), bars[:-1, 3].min()])
    cols = [1, 3, 2, 4] if side > 0 else [1, 2, 3, 4]
    prices.extend(bars[-1, cols[:phase]])
    signed = side * (np.array(prices) / bars[0, 1] - 1) * 100
    return max(0., float(signed.max())), max(0., float(-signed.min()))


def run_side(raw, requests, side):
    if not requests:
        return []
    indices = np.unique(np.concatenate([np.arange(r['start'],r['end']+1) for r in requests]))
    tape = raw[indices]
    frame = pd.DataFrame(tape[:,1:5], columns=["open","high","low","close"],
                         index=pd.to_datetime(tape[:,0].astype(np.int64)+HOUR,unit="ms",utc=True))
    orders = []
    meta = {}
    for r in requests:
        entry = raw[r['start'],1]
        stop,target = bracket(entry,side)
        meta[r['trade_id']] = r
        orders.append(dict(trade_id=r['trade_id'],entry_i=int(np.searchsorted(indices,r['start'])),
                           end_i=int(np.searchsorted(indices,r['end'])),entry=entry,stop=stop,
                           side=side,risk=side*(entry-stop),target_price=target))
    orders = pd.DataFrame(orders)
    tick = price_grid(np.concatenate([tape[:,1:5].ravel(),orders.stop,orders.target_price]))
    policy = ExitPolicy(trail_arm_r=float('inf'),opposite_hour_exit=False,round_trip_cost=0.,
                        target_from_entry=True, intrabar_path='OLHC' if side>0 else 'OHLC',
                        monotone_segments=True,fill_maker_fee=MAKER,fill_taker_fee=TAKER)
    rows,fills,_ = run_native(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),orders,
                              policy,tick=tick,symbol='HREPLAY',execution_minutes=60,
                              entry_order_mode='market_open')
    result = []
    entry_fees = fills[fills.phase=='entry'].set_index('trade_id').commission
    for row in rows.itertuples():
        r=meta[row.trade_id]
        start=r['start']
        exit_i=int(indices[row.exit_i])
        reason={1:'stop',4:'target',0:'timeout'}[row.reason]
        if reason=='timeout' and r['end']-start+1 < HOLD:
            reason='cutoff_mark'
        phase=int(row.exit_path_phase)
        entry_ms=int(raw[start,0])
        exit_ms=int(raw[exit_i,0])+(HOUR if phase!=0 else 0)
        mfe,mae=within_trade(raw[start:exit_i+1],side,phase,row.exit_price)
        record={k:v for k,v in r.items() if k not in ('start','end')}
        record.update(side='long' if side==1 else 'short',entry_ms=entry_ms,exit_ms=exit_ms,
                      entry_px=row.entry,exit_px=row.exit_price,qty=row.quantity,
                      entry_fee_u=entry_fees[row.trade_id],
                      exit_fee_u=row.framework_commission-entry_fees[row.trade_id],
                      fee_u=row.framework_commission,net_pct=row.net_notional_bp/100,
                      exit_reason=reason,exit_hour=exit_i-start+1,exit_phase=phase,
                      mfe_pct=mfe,mae_pct=mae,both_barriers=row.ambiguous)
        result.append(record)
    return result


def simulate(job):
    symbol, events=job
    raw=read_raw(symbol)
    raw=raw[raw[:,0]+HOUR<=CUTOFF]
    clock=raw[:,0].astype(np.int64)
    vol=pd.Series((raw[:,2]-raw[:,3])/raw[:,1]).rolling(24).mean().shift().to_numpy()
    allowed=np.zeros(len(raw),bool)
    allowed[24:]=(clock[24:]-clock[:-24])==24*HOUR
    requests={1:[],-1:[]}
    skips=[]
    for ev in events.itertuples():
        side=-1 if ev.group=='上轨守住' else 1
        ix=int(np.searchsorted(clock,ev.decision_ms))
        if ix>=len(clock) or clock[ix]!=ev.decision_ms:
            skips.append((ev.id,'signal','missing_open'));continue
        control=match_index(ev,raw,ix,allowed,vol)
        for sample,start in [('signal',ix),('random',control)]:
            if start is None:
                skips.append((ev.id,sample,'unmatched'));continue
            expected=clock[start]+np.arange(min(HOLD,len(raw)-start))*HOUR
            gaps=np.flatnonzero(clock[start:start+HOLD]!=expected)
            n=int(gaps[0]) if len(gaps) else len(expected)
            assert n>0
            requests[side].append(dict(start=start,end=start+n-1,sample=sample,symbol=symbol,
                pair_id=ev.id,trade_id=f"H{ev.Index}{'S' if sample=='signal' else 'R'}",
                group=ev.group,source_month=ev.month,pre24_range_pct=vol[start]*100))
    results=run_side(raw,requests[1],1)+run_side(raw,requests[-1],-1)
    return results,skips


def load_events():
    path = DEST/'signals.csv'
    if not path.exists():
        from signals import extract
        extract().to_csv(path,index=False)
    events=pd.read_csv(path,**ROUNDTRIP)
    assert len(events)==2338 and events.id.is_unique
    return events


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    cache=DEST/'baseline_parts'
    cache.mkdir(exist_ok=True)
    jobs=list(load_events().groupby('symbol',sort=True))
    pending=[job for job in jobs if not (cache/(job[0]+'.json')).exists()]
    with ProcessPoolExecutor(max_workers=5) as pool:
        from concurrent.futures import as_completed
        futures={pool.submit(simulate,job):job[0] for job in pending}
        for index,future in enumerate(as_completed(futures),1):
            symbol=futures[future]
            try:
                data=future.result()
            except Exception as error:
                for other in futures:
                    other.cancel()
                raise RuntimeError(f"Native replay failed for {symbol}") from error
            tmp=cache/(symbol+'.tmp')
            tmp.write_text(json.dumps(data,ensure_ascii=False))
            tmp.replace(cache/(symbol+'.json'))
            if index%100==0:
                print(f"Native replay {index}/{len(pending)} contract batches",flush=True)
    all_rows=[];missing=[]
    for symbol,_ in jobs:
        rows,skips=json.loads((cache/(symbol+'.json')).read_text())
        all_rows.extend(rows);missing.extend(skips)
    frame=pd.DataFrame(all_rows).sort_values(['entry_ms','symbol','trade_id'])
    assert frame.trade_id.is_unique
    frame.to_csv(DEST/'fills.csv',index=False)
    pd.DataFrame(missing,columns=['pair_id','sample','reason']).to_csv(DEST/'skips.csv',index=False)
    print(frame.groupby(['sample','group','exit_reason']).size().to_string(),flush=True)


if __name__=='__main__':
    main()
