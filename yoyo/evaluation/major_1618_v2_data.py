"""Registered XAU input extension and reuse of unchanged BTC/ETH study caches.

Owner confirmed fixed0.7% stop also for OKX XAU perpetual on2026-10-31.
Only XAU needs new native orders: old BTC/ETH inputs, labels, matched controls
and costs remain frozen. No content hashes, new downloads or service writes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import numpy as np
import pandas as pd

from yoyo.evaluation.btc_eth_repeat_data import ROOT,CACHE,STEP,candidates,dump,EXP as OLD
EXP=ROOT/'experiments/active/exp-btc-eth-xau-1618-models-20261031-v2'


def config():
    return json.loads((EXP/'config.json').read_text())


def read(name):
    return pd.read_csv(EXP/name,float_precision='round_trip')


def frame(symbol):
    p=OLD/f'inputs/{symbol}_5m.csv.gz' if symbol in ['BTC','ETH'] else EXP/'inputs/XAU_5m.csv.gz'
    f=pd.read_csv(p,float_precision='round_trip')
    f.index=pd.to_datetime(f.pop('close_time'),utc=True)
    return f


def prepare():
    cfg=config();out=EXP/'inputs';out.mkdir(exist_ok=True)
    dest=out/'XAU_5m.csv.gz'
    if not dest.exists():
        source=ROOT/'data/crypto/market/exp-xau-monday-recovery-20261026-v1/candles.csv.gz'
        raw=pd.read_csv(source,float_precision='round_trip')
        raw.index=pd.to_datetime(raw.open_time,utc=True)
        raw=raw[['open','high','low','close','volume']].astype(float)
        end=pd.Timestamp(cfg['xau_snapshot_last_close'])-STEP
        with sqlite3.connect(CACHE.as_uri()+'?mode=ro',uri=True) as db:
            rows=[json.loads(r[0]) for r in db.execute(
                'SELECT raw FROM candles WHERE key=? AND t>=? AND t<=? ORDER BY t',
                ('https://www.okx.com|XAU-USDT-SWAP|5m',int((raw.index[-1]-pd.Timedelta(days=1)).timestamp()*1000),int(end.timestamp()*1000)))]
        tail=pd.DataFrame(rows,columns=['ts','open','high','low','close','volume','base','quote','confirm'])
        tail=tail[tail.confirm.astype(str).eq('1')]
        tail.index=pd.to_datetime(tail.ts.astype('int64'),unit='ms',utc=True)
        tail=tail[raw.columns].astype(float)
        common=raw.index.intersection(tail.index)
        np.testing.assert_allclose(raw.loc[common],tail.loc[common],rtol=0,atol=1e-6)
        f=pd.concat([raw,tail[tail.index>raw.index[-1]]])
        assert f.index[-1]==end,'Registered cache does not cover the frozen comparison end'
        assert f.index.is_unique and (np.diff(f.index.asi8)==STEP.value).all()
        assert np.isfinite(f.to_numpy()).all() and (f[['open','high','low','close']]>0).all().all()
        assert (f.high>=f[['open','close']].max(axis=1)).all() and (f.low<=f[['open','close']].min(axis=1)).all()
        f.index+=STEP;f.index.name='close_time';f.to_csv(dest)
        dump(out/'receipt.json',dict(venue='OKX XAU-USDT-SWAP',source=str(source),size=source.stat().st_size,
              mtime=source.stat().st_mtime,overlap_rows=len(common),rows=len(f),first_close=str(f.index[0]),
              last_close=str(f.index[-1]),zero_volume=int(f.volume.eq(0).sum()),
              reused_BTC_ETH=str(OLD),builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    f=frame('XAU')
    events,orders=candidates('XAU',f,cfg=cfg,tick=.1)
    events.to_csv(EXP/'XAU_events.csv.gz',index=False)
    orders.to_csv(EXP/'XAU_entries.csv.gz',index=False)
    dump(EXP/'reuse_receipt.json',dict(BTC_ETH_native='All previous native results reused, no order replay',
                                     source_experiment=str(OLD),XAU_events=len(events),XAU_eligible=int(events.eligible.sum()),XAU_orders=len(orders)))
    print(dict(XAU_rows=len(f),events=len(events),eligible=int(events.eligible.sum()),orders=len(orders)),flush=True)


def native(sensitivity=False):
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy,run_native
    cfg=config();suffix='path' if sensitivity else 'native'
    if (EXP/f'XAU_{suffix}.json').exists():
        print('Reuse',suffix,flush=True);return
    entries=read('XAU_entries.csv.gz')
    if sensitivity:
        primary=read('XAU_native_trades.csv.gz')
        ids=primary.loc[primary.ambiguous.eq(1)|primary.exit_i.eq(primary.entry_i),'trade_id']
        entries=entries[entries.trade_id.isin(ids)]
        if entries.empty:
            dump(EXP/'XAU_path.json',dict(orders=0));return
    f=frame('XAU')
    entries.to_csv(EXP/f'XAU_{suffix}_orders.csv.gz',index=False)
    policy=ExitPolicy(target_from_entry=True,opposite_hour_exit=False,round_trip_cost=0,
                      fill_maker_fee=cfg['maker_fee'],fill_taker_fee=cfg['taker_fee'],
                      intrabar_path='OLHC' if sensitivity else 'OHLC',monotone_segments=True)
    rows,fills,audit=run_native(f,np.full(len(f),np.nan),np.zeros(len(f)),entries,policy,
                               tick=.1,symbol='XAU',entry_order_mode='limit',execution_minutes=5,batch_bars=24000)
    rows.to_csv(EXP/f'XAU_{suffix}_trades.csv.gz',index=False)
    fills.to_csv(EXP/f'XAU_{suffix}_fills.csv.gz',index=False)
    dump(EXP/f'XAU_{suffix}.json',audit)
    print(suffix,dict(orders=len(entries),trades=len(rows),fills=len(fills),ambiguous=int(rows.ambiguous.sum())),flush=True)


def label_data(include_xau=True):
    from yoyo.evaluation.btc_eth_repeat_fit import labels
    old,_=labels()
    parts=[old]
    if include_xau:
        new=read('XAU_native_trades.csv.gz')
        events=read('XAU_events.csv.gz')
        x=new[new.kind.eq('signal')].copy()
        extra=[c for c in events if c not in x or c=='event_id']
        x=x.merge(events[extra],on='event_id',validate='many_to_one')
        x['label']=x.reason.eq(4).astype(int)
        c=new[new.kind.eq('control')].groupby(['event_id','target']).agg(
            random_bp=('net_notional_bp','mean'),random_r=('net_r','mean'),random_n=('trade_id','size')).reset_index()
        x=x.merge(c,on=['event_id','target'],how='left',validate='one_to_one')
        parts.append(x)
    d=pd.concat(parts,ignore_index=True)
    # Net-gain geometry is target-specific; original near features are retained
    # byte-for-value and far heads receive their actual known target distance.
    farther=d.target.ne('near')
    d.loc[farther,'f_reward_r']=(d.side*(d.target_price-d.entry)/d.risk)[farther]
    cfg=config()
    reward=d.side*(d.target_price-d.entry)-cfg['maker_fee']*(d.entry+d.target_price)
    loss=d.risk+cfg['maker_fee']*d.entry+cfg['taker_fee']*d.stop
    d['break_even_p']=loss/(loss+reward)
    d['week_start']=pd.to_datetime(d.week).dt.tz_localize('America/New_York').dt.tz_convert('UTC')
    d['expiry_dt']=pd.to_datetime(d.expiry,utc=True)
    return d


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','native','sensitivity'])
    a=p.parse_args()
    prepare() if a.stage=='prepare' else native(a.stage=='sensitivity')
