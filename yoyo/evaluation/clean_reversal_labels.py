"""Closed-bar features and separate future-path labels for owner-defined reversals.

Owner2026-11-01: clean means sustained reverse progress with small drawdowns.
Reuse frozen OKX top20 and BTC candles. Features read open/high/low/close at
indices i-96..i only; windows1/4/16/20/96,TR14/96,Stoch5/3/3. No future episode
length or eventual reentry enters feature_frame. Future29bars are used only by
path_label. TR is explicitly an arithmetic mean,avoiding existing Wilder seeds.
All original gallery rows retain lineage; automatic candidates are not gold.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.btc_1618_early_marks import ROOT, STEP, COLS, EXP as BTC
from yoyo.evaluation.top20_1618_rank import EXP as ALTS
from yoyo.evaluation.spike_fanshen_exit import compute_signals

EXP=ROOT/'experiments/active/exp-clean-reversal-score-20261101-v1'
FEATURES=['is_long','ordinal','hours_since_monday_end','monday_width_tr','overshoot_tr','close_inside_tr','rejection_wick','body_direction','range_tr','approach_1','approach_4','approach_16','approach_96','efficiency_4','efficiency_16','acceleration_4','ma20_distance','ma20_slope16','tr_expansion','range16_tr','range96_tr','level_occupancy16','stoch_extreme','stoch_turn']


def config():return json.loads((EXP/'config.json').read_text())
def dump(path,obj):path.write_text(json.dumps(obj,indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def feature_frame(bars, events):
    """Read only closed OHLC through each signal;96-bar lookback,never backfill."""
    c=bars.close;change=c.diff()
    tr=pd.concat([bars.high-bars.low,(bars.high-c.shift()).abs(),(bars.low-c.shift()).abs()],axis=1).max(axis=1)
    scale=tr.rolling(14,min_periods=14).mean()
    slow=tr.rolling(96,min_periods=96).mean()
    ma=c.rolling(20,min_periods=20).mean()
    sig=compute_signals(bars)
    rows=[];excluded=[]
    for e in events.to_dict('records'):
        t=pd.Timestamp(e['signal_open']).tz_convert('UTC');i=bars.index.get_loc(t)
        if i<96 or not (np.diff(bars.index[i-96:i+1].asi8)==STEP.value).all():
            excluded.append(dict(event_id=e['event_id'],reason='incomplete_feature_lookback'));continue
        a=float(scale.iloc[i]);direction=1 if e['side']=='lower' else -1
        if not np.isfinite(a) or a<=0:
            excluded.append(dict(event_id=e['event_id'],reason='nonpositive_scale'));continue
        b=bars.iloc[i];span=max(float(b.high-b.low),1e-30);level=e['level']
        week=pd.Timestamp(e['week']).tz_localize('America/New_York')
        ready=(pd.Timestamp(e['week'])+pd.Timedelta(days=1)).tz_localize('America/New_York')
        assert t>=ready
        lower_wick=min(b.open,b.close)-b.low;upper_wick=b.high-max(b.open,b.close)
        vals=dict(is_long=float(direction==1),ordinal=float(e['ordinal']),hours_since_monday_end=float((t-ready)/pd.Timedelta(hours=1)),monday_width_tr=(e['monday_high']-e['monday_low'])/a,overshoot_tr=((level-b.low) if direction==1 else (b.high-level))/a,close_inside_tr=direction*(b.close-level)/a,rejection_wick=(lower_wick if direction==1 else upper_wick)/span,body_direction=direction*(b.close-b.open)/span,range_tr=span/a)
        for n in [1,4,16,96]:vals[f'approach_{n}']=-direction*(c.iloc[i]-c.iloc[i-n])/a
        for n in [4,16]:
            den=float(change.iloc[i-n+1:i+1].abs().sum())
            vals[f'efficiency_{n}']=-direction*float(c.iloc[i]-c.iloc[i-n])/den if den else 0.
        vals.update(acceleration_4=-direction*(c.iloc[i]-2*c.iloc[i-4]+c.iloc[i-8])/a,ma20_distance=-direction*(c.iloc[i]-ma.iloc[i])/a,ma20_slope16=-direction*(ma.iloc[i]-ma.iloc[i-16])/a,tr_expansion=a/slow.iloc[i],range16_tr=(bars.high.iloc[i-15:i+1].max()-bars.low.iloc[i-15:i+1].min())/a,range96_tr=(bars.high.iloc[i-95:i+1].max()-bars.low.iloc[i-95:i+1].min())/a,level_occupancy16=float(((bars.low.iloc[i-15:i+1]<=level)&(bars.high.iloc[i-15:i+1]>=level)).mean()),stoch_extreme=(100-sig.k.iloc[i]) if direction==1 else sig.k.iloc[i],stoch_turn=direction*(sig.k.iloc[i]-sig.d.iloc[i]))
        assert np.isfinite(list(vals.values())).all()
        rows.append(dict(event_id=e['event_id'],**{f'feature_{k}':float(vals[k]) for k in FEATURES},tr_mean14=a,signal_close_price=float(b.close),feature_last_close=str(t+STEP)))
    return pd.DataFrame(rows),excluded


def path_label(bars,i,direction,scale,horizon=29):
    """Use only29futureOHLC bars as label;missing timestamps invalidate label."""
    future=bars.iloc[i+1:i+1+horizon]
    if len(future)!=horizon or not (np.diff(bars.index[i:i+1+horizon].asi8)==STEP.value).all():return None
    ref=float(bars.close.iloc[i]);q=np.r_[0.,direction*(future.close.to_numpy()-ref)/scale]
    net=float(q[-1]);progress=max(net,0.);travel=float(np.abs(np.diff(q)).sum())
    efficiency=progress/travel if travel else 0.
    drawdown=float((np.maximum.accumulate(q)-q).max())
    adverse=max(0.,float((ref-future.low.min())/scale if direction==1 else (future.high.max()-ref)/scale))
    quality=100.*(progress/(1.+progress))*efficiency/(1.+drawdown+adverse)
    return dict(quality=quality,net_progress_tr=net,efficiency=efficiency,max_close_drawdown_tr=drawdown,adverse_wick_tr=adverse,positive_time_fraction=float((q[1:]>0).mean()),label_end=str(future.index[-1]+STEP))


def source_data():
    """Yield existing closed candles and full causal first-five signal ledger."""
    rank=json.loads((ALTS/'ranking.json').read_text())
    for symbol,folder in [('BTC',BTC)]+[(r['symbol'],ALTS/r['symbol']) for r in rank['scan']]:
        bars=pd.read_csv(folder/'bars.csv.gz',index_col=0,parse_dates=True,float_precision='round_trip')
        marks=pd.read_csv(folder/'all_outside_marks.csv',float_precision='round_trip')
        marks=marks[marks.basis.eq('wick')&marks.visible_v242&marks.ordinal.le(5)].copy()
        marks['symbol']=symbol
        marks['episode_id']=marks.episode_id.map(lambda x:x if x.startswith(symbol+'-USDT-SWAP|') else symbol+'-USDT-SWAP|'+x)
        marks['event_id']=symbol+'|'+marks.signal_open+'|'+marks.side
        marks['in_original_gallery']=marks.episode_bars.le(5)&marks.duration_known
        yield symbol,bars,marks,folder


def build():
    all_features=[];all_outcomes=[];all_events=[];excluded=[];source=[]
    for symbol,bars,events,folder in source_data():
        if events.empty:continue
        feat,miss=feature_frame(bars,events);excluded.extend(miss)
        by_id=events.set_index('event_id');labels=[]
        for f in feat.to_dict('records'):
            e=by_id.loc[f['event_id']];i=bars.index.get_loc(pd.Timestamp(e.signal_open).tz_convert('UTC'))
            label=path_label(bars,i,1 if e.side=='lower' else -1,f['tr_mean14'],config()['horizon_bars'])
            if label is None:excluded.append(dict(event_id=f['event_id'],reason='incomplete_future_horizon'));continue
            labels.append(dict(event_id=f['event_id'],**label))
        all_features.append(feat);all_outcomes.extend(labels)
        all_events.append(events[['event_id','episode_id','symbol','week','side','signal_open','signal_close','level','ordinal','episode_bars','duration_known','in_original_gallery']])
        p=folder/'bars.csv.gz';source.append(dict(symbol=symbol,path=str(p),bytes=p.stat().st_size,mtime_ns=p.stat().st_mtime_ns,bars=len(bars),candidates=len(events),feature_rows=len(feat),observed_labels=len(labels)))
        print(symbol,'candidates',len(events),'labels',len(labels),flush=True)
    features=pd.concat(all_features,ignore_index=True);outcomes=pd.DataFrame(all_outcomes);events=pd.concat(all_events,ignore_index=True)
    assert events.event_id.is_unique and len(events)==2256
    assert events.in_original_gallery.sum()==644
    events.to_csv(EXP/'events.csv',index=False);features.to_csv(EXP/'features.csv',index=False);outcomes.to_csv(EXP/'outcomes.csv',index=False)
    dump(EXP/'input_receipt.json',dict(sources=source,events=len(events),original_gallery=int(events.in_original_gallery.sum()),features=len(features),labels=len(outcomes),excluded=excluded,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))


if __name__=='__main__':build()
