"""Apply just the frozen confirmed-exit change to the Binance transfer's venue-native prices."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_transfer as venue
from yoyo.evaluation import cloud_close_flip_confirmation as flip
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-binance-confirm-20261109-v1'


def price(group):
    symbol=group.symbol.iloc[0]
    clock,values=venue.quotes(symbol)
    bars=pd.DataFrame(values,index=pd.Index(clock,name='time'),columns=venue.FIELDS)
    groups=bars.groupby(clock//engine.STEP_12H*engine.STEP_12H)
    b12=groups.agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),n=('open','count'))
    b12=b12[b12.n.eq(12)].drop(columns='n').rename_axis('time')
    q,time,ctx=engine._prepare_symbol(b12,bars)
    revised,info=flip._confirmation_q(q,time,ctx['features'],1)
    starts=time.searchsorted(group.entry_ms)
    if not np.array_equal(time[starts],group.entry_ms) or np.any(q[starts,5]*group.side.to_numpy()<=0):
        raise ValueError('Binance source entry confirmation mismatch for '+symbol)
    if not np.allclose(q[starts,0],group.entry_price,rtol=1e-12,atol=1e-15):
        raise ValueError('Binance hourly entry price mismatch for '+symbol)
    events=group.assign(entry_i=starts,risk_distance=(group.entry_price-group.stop_price).abs(),week='',
                        source_row_number=np.arange(len(group)))
    events['end_i']=[engine._safe_path_end(i,s,revised,time)[0] for i,s in zip(starts,events.side)]
    filled,_=engine._simulate_symbol(events.to_dict('records'),revised,time,(0.,2.),8)
    result=pd.DataFrame(filled)
    result['entry_order']=result.event_id.map(group.set_index('event_id').entry_order)
    return result[['symbol','parent_id','event_id','is_control','side','entry_ms','exit_bar_ms','entry_price',
                   'exit_price','exit_phase','exit_reason','exit_fee_rate','tp_r','stop_price','gap_flag',
                   'same_bar_ambiguous','entry_order']]


def fetch(prior):
    checkpoint=EXP/'work'
    checkpoint.mkdir(exist_ok=True)
    chunks=[]
    for k,(symbol,g) in enumerate(prior[prior.tp_r.eq(0)].groupby('symbol'),1):
        path=checkpoint/(symbol+'.csv.gz')
        if path.is_file():
            chunk=pd.read_csv(path,dtype={'symbol':str},keep_default_na=False)
        else:
            chunk=price(g)
            temp=path.with_suffix('.pending')
            chunk.to_csv(temp,index=False,compression='gzip')
            temp.replace(path)
        chunks.append(chunk)
        if k%100==0:print('Priced',k,'Binance confirmed-exit symbols',flush=True)
    frame=pd.concat(chunks,ignore_index=True)
    if len(frame)!=len(prior) or set(frame.event_id)!=set(prior.event_id):
        raise ValueError('Venue comparison changed entry set')
    return frame


def main():
    parent=json.loads((flip.EXP/'summary.json').read_text())['development_pick']
    if parent['pick']!='confirmed_1x12h':
        raise ValueError('Frozen OKX exit changed')
    prior=pd.read_csv(venue.EXP/'fills.csv.gz',dtype={'symbol':str},keep_default_na=False)
    out=EXP/'confirmed_fills.csv.gz'
    if not out.exists():
        confirmed=fetch(prior)
        tmp=out.with_suffix('.pending')
        confirmed.to_csv(tmp,index=False,compression='gzip')
        tmp.replace(out)
    else:
        confirmed=pd.read_csv(out,dtype={'symbol':str},keep_default_na=False)
    real=confirmed[~confirmed.is_control]
    ctl=confirmed[confirmed.is_control]
    rows=[]
    marks={}
    for name,events,slip in [('intrabar',prior[~prior.is_control],0.),('confirmed',real,0.),
                             ('slippage_5bp',real,.0005),
                             ('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),('control',ctl,0.)]:
        (metrics,tr,eq),marks[name]=venue.account(events,True,slip)
        rows.extend(dict(policy=name,period=p,**v) for p,v in metrics.items())
        if name=='confirmed':
            tr.to_csv(EXP/'funded_confirmed.csv.gz')
            eq.to_csv(EXP/'hourly_confirmed.csv.gz',index_label='hour_end_utc')
        gc.collect()
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    result=dict(source_rows=len(prior),priced_real=len(real)//2,available_controls=len(ctl)//2,
                missing_position_leg_hours=marks,all_metrics=frame[frame.period.eq('all')].to_dict('records'))
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
