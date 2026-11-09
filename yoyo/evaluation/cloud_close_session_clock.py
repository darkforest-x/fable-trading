"""Test the 12H session boundary while rebuilding the whole weekly signal and its cloud from native H1."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_transfer as stats
from yoyo.evaluation import cloud_close_breakout_branch as branch
from yoyo.evaluation import cloud_close_pair_branch as spread

EXP=spread.EXP.parent/'exp-cloud-close-session-clock-20261109-v1'
H=spread.wallet.HOUR
STEP=12*H


def bars_for(hourly,offset):
    """UTC-anchored twelve consecutive H1 OHLC records; a missing clock invalidates the complete block."""
    base=((hourly.index-offset)//STEP)*STEP+offset
    grouped=hourly.groupby(base)
    bar=grouped.agg({'open':'first','high':'max','low':'min','close':'last'})
    bar=bar[grouped.size().eq(12)]
    bar.index.name='time'
    return bar


def candidates(symbol,offset,*,anchor_weekday=0,quote_loader=None,start=spread.wallet.START,end=spread.wallet.END):
    ts,px=(quote_loader or spread.wallet.quotes)(symbol)
    valid=ts<end
    hourly=pd.DataFrame(px[valid],index=ts[valid],columns=['open','high','low','close']).rename_axis('time')
    if hourly.index.has_duplicates or not hourly.index.is_monotonic_increasing or (hourly.index%H).any():
        raise ValueError('Native H1 series is not an aligned clock')
    bars=bars_for(hourly,offset)
    q,clocks,prepared=branch.engine._prepare_symbol(bars,hourly)
    feature=prepared['features']
    calendar=branch.reclaim._anchors(hourly,bars,symbol,anchor_weekday=anchor_weekday)
    events=[]
    observed=[]
    seen=set()
    for i,key in enumerate(bars.index):
        if key+STEP<start or key+STEP>=end:
            continue
        match=branch.reclaim._signal_at(i,symbol,bars,feature,calendar,end,
                                        utc_offset_ms=offset,anchor_weekday=anchor_weekday)
        if match is None or match['week'] in seen:
            continue
        week=match['week']
        seen.add(week)
        eid=f'{symbol}-{key}-{offset//H}'
        if anchor_weekday:
            eid+=f'-weekday{anchor_weekday}'
        observed.append((eid,symbol,key+STEP,week,match['side']))
        if match['side']<0:
            continue
        event=dict(event_id=eid,parent_id=eid,symbol=symbol,week=week,side=1,source_row_number=i,is_control=False,
                   key_open_ms=key,confirmation_ms=key+STEP,stop_price=match['signal_reference_stop'],entry_i=None,
                   entry_order=feature['prior_atr_pct'][i])
        branch._bind_exact_entry(event,q,clocks,start,end)
        if event['entry_i'] is None or not np.isfinite(event['entry_order']):
            continue
        event['end_i'],event['path_upper_bound_reason']=branch.engine._safe_path_end(event['entry_i'],1,q,clocks)
        events.append(event)
    raw,_=branch.engine._simulate_symbol(events,q,clocks,(0.,),64)
    paths=pd.DataFrame(raw)
    if len(paths):
        paths=paths[paths.entry_status.eq('executed')].copy()
        paths['entry_order']=paths.event_id.map({r['event_id']:r['entry_order'] for r in events})
    return paths,observed


def main(*,directory=EXP,settings=(('utc_00_12',0,0),('utc_06_18',6*H,0))):
    if (directory/'summary.json').exists():
        raise FileExistsError('Completed alternate session clock pilot')
    result={}
    for label,offset,weekday in settings:
        events=[]
        keys=[]
        for symbol in spread.SYMBOLS:
            paths,observed=candidates(symbol,offset,anchor_weekday=weekday)
            if len(paths):
                events.append(paths)
            keys.extend(observed)
        table=pd.concat(events,ignore_index=True)
        if not ((table.entry_ms-offset)%STEP==0).all() or not table.confirmation_ms.equals(table.entry_ms):
            raise ValueError('Retimed account uses an unconfirmed open')
        found=pd.DataFrame(keys,columns=['event_id','symbol','decision_ms','week','side'])
        found.to_csv(directory/f'keys_{label}.csv.gz',index=False)
        table.to_csv(directory/f'paths_{label}.csv.gz',index=False)
        data=spread.wallet.prepare(table,3)
        if data['gap_marks']:
            raise ValueError('Held native hour missing')
        for slip in [0.,.0005]:
            key=label+('_stress' if slip else '')
            funded,equity=spread.wallet.account(data,slip=slip)
            funded.to_csv(directory/f'funded_{key}.csv.gz',index=False)
            equity.to_csv(directory/f'hourly_{key}.csv.gz',index_label='hour_end_utc')
            if not np.isclose(1000+funded.cash_pnl.sum(),equity.nav.iat[-1],atol=1e-5):
                raise ValueError('Clock wallet fails realized cash reconciliation')
            result[key]=dict(metrics=stats.summarize(funded,equity),weekly_keys=len(found),long_paths=len(table))
            print(key,result[key]['metrics']['all'],flush=True)
    branch._write_json(directory/'summary.json',result)


if __name__=='__main__':
    main()
