"""Bank half of remaining long units at the first native doubling of the entry price."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_fvg_priority_funding as priority
from yoyo.evaluation import cloud_close_november_paths as price

admin = priority.admin
spot = priority.spot
wallet = spot.hourly.tested.w
EXP = admin.EXP.parent/'exp-cloud-close-double-limit-20261109-v1'
PHASE = {'open':0,'intrabar':1,'close':2}


def paths(venue):
    if venue=='Binance':
        f = pd.read_csv(priority.EXP/'child_windows.csv.gz',dtype={'symbol':str},float_precision='round_trip')
        return f,admin.archive.native.quotes,.9
    original = spot.hourly.tested.expiry.trend.thirds.okx_paths()
    rows,_ = spot.hourly.tested.expiry.time_in_force(original[~original.is_control])
    roster = pd.read_csv(spot.hourly.EXP/'funded_OKX.csv.gz',usecols=['event_id'])
    part = rows[rows.event_id.isin(roster.event_id)]
    return part,wallet.quotes,1.


def new_exits(children,loader):
    # Each parent keeps a piece of the original runner until its original exit,
    # so neither physical slot occupancy nor same-contract admission changes.
    runners = children[children.tp_r.eq(0)&children.side.eq(1)]
    records = []
    for symbol,group in runners.groupby('symbol'):
        clock,ohlc = loader(symbol)
        events = []
        for row in group.itertuples():
            left,right = np.searchsorted(clock,[row.entry_ms,row.exit_bar_ms+price.HOUR])
            if clock[left]!=row.entry_ms or clock[right-1]!=row.exit_bar_ms:
                raise ValueError('Unobservable funded entry/exit at doubling-price review')
            if ohlc[left:right,1].max()<2*row.entry_price:
                continue
            events.append((row,left))
        if not events:
            continue
        hours = pd.DataFrame(ohlc,columns=price.OHLC,index=pd.Index(clock,name='time'))
        q,times,_ = price.engine._prepare_symbol(price.twelve_hour(hours),hours)
        for row,left in events:
            risk = float(row.entry_price-row.stop_price)
            end,reason = price.engine._safe_path_end(left,1,q,times)
            event = dict(event_id=row.event_id,parent_id=row.parent_id,symbol=symbol,source_row_number=0,week='',
                         is_control=False,side=1,entry_ms=row.entry_ms,entry_price=row.entry_price,stop_price=row.stop_price,
                         risk_distance=risk,entry_i=left,end_i=end,path_upper_bound_reason=reason)
            fills,_ = price.engine._simulate_symbol([event],q,times,(row.entry_price/risk,),1)
            new = fills[0]
            if abs(new['target_price']-2*row.entry_price)>1.e-11*row.entry_price:
                raise ValueError('Native target did not equal two times the actual entry')
            if new['exit_reason'].startswith('take_profit'):
                records.append(new)
            elif row.exit_reason!='delisting_settlement' and (new['exit_bar_ms']!=row.exit_bar_ms or
                    not np.isclose(new['exit_price'],row.exit_price,rtol=1.e-9,atol=1.e-12)):
                raise ValueError(f'Untouched limit changed the underlying fixed exit for {row.event_id}')
    return pd.DataFrame(records)


def assemble(children,new,venue,loader,*,parent_slots=9):
    original,skips = wallet.admit(children[children.tp_r.eq(0)],parent_slots)
    known = original.set_index('event_id').slot
    results = []
    records = []
    by_id = new.set_index('event_id') if len(new) else pd.DataFrame()
    for n,(level,extra) in enumerate([(2,True),(2,False),(0,True),(0,False)]):
        rows = children[children.tp_r.eq(level)].copy().reset_index(drop=True)
        if set(known.index)!=set(rows.event_id):
            raise ValueError('Physical native roster changed')
        rows['slot'] = rows.event_id.map(known)*4+n
        if extra:
            for idx in rows.index[rows.event_id.isin(by_id.index)]:
                r = rows.loc[idx]
                adjusted = by_id.loc[r.event_id]
                timing = (int(adjusted.exit_bar_ms),PHASE[adjusted.exit_phase])
                native = (int(r.exit_bar_ms),PHASE[r.exit_phase])
                if timing>native:
                    continue
                if timing==native:
                    # With two profit limits on one long child the nearer one
                    # executes first. Adverse native protection wins a simultaneous tie.
                    if not r.exit_reason.startswith('take_profit') or r.exit_price<=adjusted.exit_price:
                        continue
                for field in ['exit_bar_ms','exit_phase','exit_reason','exit_price','exit_fee_rate','same_bar_ambiguous']:
                    rows.at[idx,field] = adjusted[field]
                records.append(dict(event_id=r.event_id,original_child=level,symbol=r.symbol,
                                    entry_ms=r.entry_ms,limit_ms=int(adjusted.exit_bar_ms),price=adjusted.exit_price))
        results.append(rows)
    calendar = {} if venue=='OKX' else dict(start=price.venue.START,end=price.venue.END)
    return wallet.prepare(None,parent_slots*4,assigned=(pd.concat(results,ignore_index=True),skips),
                          quote_loader=loader,**calendar),records


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed absolute doubling-price study')
    extra = set(json.loads((admin.archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    source = pd.read_csv(priority.EXP/'child_windows.csv.gz',nrows=0)
    if 'stop_price' not in source:
        raise ValueError('Unpriced original stop in the frozen extended funding source')
    out = {}
    with admin.archive.market_quotes(extra):
        for venue in ('Binance','OKX'):
            children,loader,exposure = paths(venue)
            doubled = new_exits(children,loader)
            data,changes = assemble(children,doubled,venue,loader)
            metrics,pos,curve,trims = spot.hourly.tested.trim_account(data,loader,exposure,.2,
                    (1/3,1/3,1/6,1/6),review_hours=1)
            if not np.isclose(1000+pos.cash_pnl.sum(),curve.nav.iloc[-1],rtol=1.e-8,atol=.001):
                raise ValueError('Summed actual child fills and native account do not reconcile')
            base = (json.loads((priority.priority.EXP/'summary.json').read_text())['ranked'] if venue=='Binance'
                    else json.loads((spot.hourly.EXP/'summary.json').read_text())['OKX']['metrics'])
            pos.to_csv(EXP/f'funded_{venue}.csv.gz')
            curve.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
            pd.DataFrame(changes).to_csv(EXP/f'doublings_{venue}.csv',index=False)
            out[venue] = dict(reference=base,metrics=metrics,changed_parents=len(set(r['event_id'] for r in changes)),
                              changed_child_exits=len(changes),trimmed_parents=trims.event_id.nunique())
            del data,children
            gc.collect()
    (EXP/'summary.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')
    print(json.dumps(out,indent=2),flush=True)


if __name__=='__main__':
    main()
