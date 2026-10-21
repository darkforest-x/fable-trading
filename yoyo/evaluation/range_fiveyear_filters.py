"""Apply admitted sweep20 plus MTF-first filters to frozen five-year entries.

Read touch OHLC and preceding20 bars before key pairing; MTF uses only the
paired15m arrow's completed H1 watch and the existing0/15/30minute entry lag.
Reuse original five-year native paths and the annual sweep ledger after
execution-input identity checks. Only previously unseen older rematches run
through Nautilus. Exact wick stops,15m2/2 confirmation-close exits,20bp cost
and matched backgrounds are unchanged; no exit search or Pine modification.
"""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT,dump,file_stamp

CONFIG=ROOT/'experiments/active/exp-range-v231-fiveyear-filters-20261021-v1/config.json'
CODE=['yoyo/evaluation/range_fiveyear_filters.py',
      'yoyo/evaluation/monday_range_sweep_reference.py',
      'yoyo/evaluation/monday_range_v2_31_reference.py',
      'yoyo/evaluation/range_mtf_confluence.py',
      'yoyo/evaluation/range_mtf_confluence_window.py']
FIELDS=['entry_i','side','entry','stop','end_i','segment','week','bucket']


def read(path):
    return pd.read_csv(path,float_precision='round_trip')


def revision_alternative(five,source):
    """Rebuild only affected5m buckets under the other official ETH prices."""
    from yoyo.evaluation.monday_range_v2_2_reference import aggregate as from_minutes
    revisions=read(source/'inputs/overlap_revisions.csv')
    buckets=(revisions.ts//300000*300000).unique()
    lo,hi=int(min(buckets)),int(max(buckets))+300000
    baseline=json.loads((source.parent/'config.json').read_text())
    spec=next(s for s in baseline['symbols'] if s['symbol']=='ETH')
    pieces=[]
    for name in spec['inputs']:
        for frame in pd.read_csv(ROOT/name,usecols=['ts','open','high','low','close'],float_precision='round_trip',chunksize=250_000):
            pieces.append(frame[(frame.ts>=lo)&(frame.ts<hi)])
    pieces.append(read(source/'inputs/gap_minutes.csv'))
    minute=pd.concat(pieces).drop_duplicates('ts').sort_values('ts')
    minute=minute[(minute.ts//300000*300000).isin(buckets)]
    minute.index=pd.DatetimeIndex(pd.to_datetime(minute.pop('ts'),unit='ms',utc=True)).as_unit('ns')
    current=from_minutes(minute,5)
    pd.testing.assert_frame_equal(current,five.loc[current.index],check_freq=False)
    idx=pd.to_datetime(revisions.ts,unit='ms',utc=True)
    minute.loc[idx,['open','high','low','close']]=revisions[[k+'_api' for k in ['open','high','low','close']]].to_numpy()
    alternative=five.copy();updated=from_minutes(minute,5)
    alternative.loc[updated.index]=updated
    return alternative


def prepare(cfg,spec,dest):
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    from yoyo.evaluation.monday_range_v2_2_reference import signals,atr
    from yoyo.evaluation.pepe_range_exits import aggregate
    from yoyo.evaluation.monday_range_v2_31_reference import select_matches
    from yoyo.evaluation.monday_range_sweep_reference import select_sweep_matches
    from yoyo.evaluation.range_v231_midpoint import exact_stops
    from yoyo.evaluation.range_v221_compare import controls,candidate_rows
    symbol=spec['symbol'];folder=dest/symbol;source=ROOT/cfg['source']/symbol
    annual=ROOT/cfg['annual_sweep']/symbol
    identity=dict(config=cfg,spec=spec,code=[file_stamp(ROOT/p) for p in CODE],
        inputs=[file_stamp(source/p) for p in ['candles.csv.gz','signal_matches.csv','candidates.csv','trades.csv','sensitivity.csv']]
              +[file_stamp(annual/p) for p in ['signal_matches.csv','candidates.csv','admission.csv','pivot/trades.csv','pivot/sensitivity.csv']])
    receipt=folder/'preparation.json'
    if receipt.exists():
        old=json.loads(receipt.read_text())
        if old['identity']!=identity:raise ValueError('Changed preparation; preserve previous outputs')
        return old
    five=read(source/'candles.csv.gz')
    five.index=pd.DatetimeIndex(pd.to_datetime(five.pop('ts'),unit='ms',utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
    q=aggregate(five,15)
    grid,names,weeks,_=monday_levels(q,cfg['ratios'],cfg['timezone'])
    arrows=signals(q).to_numpy()
    kwargs=dict(before=5,wait_bars=2,penetration=.1,buffer=.002,tick=spec['tick'],timezone=cfg['timezone'],start=cfg['start'])
    original=exact_stops(select_matches(q,arrows,grid,names,weeks,**kwargs),q,spec['tick'],cfg['end'])
    old=read(source/'signal_matches.csv')
    pd.testing.assert_frame_equal(pd.DataFrame(original)[old.columns.drop('pair_id')],old.drop(columns='pair_id'),check_dtype=False,rtol=0,atol=1e-10)
    selected=select_sweep_matches(q,arrows,grid,names,weeks,window=20,**kwargs)
    records=exact_stops(selected,q,spec['tick'],cfg['end'])
    revision_sweep_parity=None
    if symbol=='ETH':
        aq=aggregate(revision_alternative(five,source),15)
        ag,an,aw,_=monday_levels(aq,cfg['ratios'],cfg['timezone'])
        alternative=exact_stops(select_sweep_matches(aq,signals(aq).to_numpy(),ag,an,aw,window=20,**kwargs),aq,spec['tick'],cfg['end'])
        assert alternative==records
        revision_sweep_parity=True
    lookup={(int(r.signal_i),int(r.side)):int(r.pair_id) for r in old.itertuples()}
    next_id=int(old.pair_id.max())+1
    for row in records:
        k=(row['signal_i'],row['side'])
        if k not in lookup:lookup[k]=next_id;next_id+=1
        row['pair_id']=lookup[k]
    probes=sorted({r['entry_i']+d for r in records[::max(1,len(records)//8)] for d in (0,1)})
    for end in probes:
        got=select_sweep_matches(q.iloc[:end],arrows[:end],grid[:end],names,weeks[:end],window=20,**kwargs)
        assert got==[r for r in selected if r['entry_i']<end]
    mapped=pd.DataFrame(records)
    recent=mapped[pd.to_datetime(mapped.entry_time,utc=True)>=pd.Timestamp(cfg['annual_start'])]
    annual_matches=read(annual/'signal_matches.csv')
    fields=['signal_time','touch_time','entry_time','side','key','level','entry','stop']
    pd.testing.assert_frame_equal(recent[fields].reset_index(drop=True),annual_matches[fields].reset_index(drop=True),check_dtype=False,rtol=0,atol=1e-10)
    annual_to_new=dict(zip(annual_matches.pair_id.astype(int),recent.pair_id.astype(int)))
    annual_base=read(annual/'candidates.csv')
    baseline_base=read(source/'candidates.csv')
    half=(q.index>=pd.Timestamp(cfg['midpoint'])).astype(int)
    week=(q.index-pd.to_timedelta(q.index.dayofweek,unit='D')).normalize().as_unit('ns').asi8
    bucket=np.digitize(atr(q,14).to_numpy()/q.close.to_numpy(),cfg['volatility_edges'])
    excluded=set(np.flatnonzero(arrows))|set(mapped.entry_i)
    matched={};reserved=set();preferred={};fallback=[]
    new_to_annual={v:k for k,v in annual_to_new.items()}
    for row in records:
        pid=row['pair_id'];i=row['entry_i']
        kind='annual' if pid in new_to_annual else 'baseline'
        old_pid=new_to_annual.get(pid,pid)
        source_base=annual_base if kind=='annual' else baseline_base
        pair=source_base[source_base.pair_id==old_pid]
        control=pair[pair.population=='random']
        ci=-1 if control.empty else int(q.index.get_indexer([pd.Timestamp(control.iloc[0].entry_time)])[0])
        valid=ci>=0 and ci not in excluded and ci not in reserved
        valid=valid and (half[ci],week[ci],bucket[ci])==(half[i],week[i],bucket[i])
        if valid:
            matched[pid]=ci;reserved.add(ci);preferred[pid]=(kind,old_pid)
        else:
            if kind=='annual':raise AssertionError('Frozen annual control no longer matches')
            fallback.append(row)
    if fallback:
        excluded_arrows=arrows.copy();excluded_arrows[list(reserved|excluded)]=1
        extra,_,_,_=controls(q,excluded_arrows,fallback,cfg,spec);matched.update(extra)
    assert len(set(matched.values()))==len(records)
    candidates=candidate_rows(five,q,records,matched,(half,week,bucket),spec['tick'])
    reuse=[];replay=[]
    for row in records:
        pid=row['pair_id'];current=candidates[candidates.pair_id==pid].sort_values('population')
        chosen=preferred.get(pid)
        if chosen is None:replay.append(pid);continue
        kind,old_pid=chosen
        previous=(annual_base if kind=='annual' else baseline_base)
        previous=previous[previous.pair_id==old_pid].sort_values('population').copy()
        offset=0
        if kind=='annual':
            offsets=current.entry_i.to_numpy()-previous.entry_i.to_numpy()
            assert len(set(offsets))==1
            offset=int(offsets[0])
        previous['entry_i']+=offset;previous['end_i']+=offset
        for column in ['segment','week','bucket']:previous[column]=current[column].to_numpy()
        same=np.allclose(current[FIELDS],previous[FIELDS],rtol=0,atol=1e-8)
        if same:
            # Keep the exact cached stop float, avoiding an irrelevant reround.
            candidates.loc[current.index,'stop']=previous.stop.to_numpy()
            reuse.append(dict(pair_id=pid,source=kind,source_pair_id=old_pid,offset=offset))
        else:
            if kind=='annual':raise AssertionError('Annual execution identity changed')
            replay.append(pid)
    changed_ids=[]
    before=old.set_index('pair_id')
    for row in records:
        if row['pair_id'] not in before.index or any(row[k]!=before.loc[row['pair_id'],k] for k in ['touch_i','entry_i','key','level','entry','stop']):
            changed_ids.append(row['pair_id'])
    folder.mkdir(parents=True,exist_ok=False)
    mapped.to_csv(folder/'signal_matches.csv',index=False)
    candidates.to_csv(folder/'candidates.csv',index=False)
    candidates[candidates.pair_id.isin(replay)].to_csv(folder/'delta_candidates.csv',index=False)
    native=json.loads((source/'native.json').read_text())
    native.update(experiment_id=cfg['experiment_id'],entry_candidates=str((folder/'delta_candidates.csv').relative_to(ROOT)),output=str((folder/'native_delta').relative_to(ROOT)))
    dump(folder/'native.json',native)
    meta=dict(identity=identity,baseline=len(old),sweep=len(records),reused= reuse,replay_ids=replay,
              rematched_ids=changed_ids,new_control_times=len(fallback),prefix_checks=len(probes),
              revision_sweep_parity=revision_sweep_parity,
              annual_sweep_parity=len(recent),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(receipt,meta)
    print(json.dumps(dict(symbol=symbol,baseline=len(old),sweep=len(records),replayed_pairs=len(replay),reused_pairs=len(reuse),annual_parity=len(recent))),flush=True)
    return meta


def execute(cfg,dest):
    from yoyo.evaluation.range_framework import run
    for spec in cfg['symbols']:
        symbol=spec['symbol'];folder=dest/symbol;meta=prepare(cfg,spec,dest)
        if meta['replay_ids']:run(folder/'native.json')
        candidates=read(folder/'candidates.csv')
        for policy,name in [('pivot','trades.csv'),('strict_ties','sensitivity.csv')]:
            pieces=[]
            for kind in ['baseline','annual']:
                reuse=[r for r in meta['reused'] if r['source']==kind]
                if not reuse:continue
                path=ROOT/cfg['source']/symbol/name if kind=='baseline' else ROOT/cfg['annual_sweep']/symbol/'pivot'/name
                cached=read(path).set_index('pair_id')
                for item in reuse:
                    part=cached.loc[[item['source_pair_id']]].copy().reset_index()
                    part['source_trade_id']=part.trade_id;part['source_ledger']=str(path.relative_to(ROOT))
                    part['pair_id']=item['pair_id']
                    part['trade_id']=part.population+'-'+part.pair_id.astype(str)
                    for col in ['entry_i','end_i','exit_i','arm_i','be_trigger_i','be_effective_i']:
                        part[col]=np.where(part[col]>=0,part[col]+item['offset'],part[col])
                    current=candidates[candidates.pair_id==item['pair_id']].set_index('population')
                    for col in ['segment','week','bucket']:part[col]=part.population.map(current[col])
                    pieces.append(part)
            if meta['replay_ids']:
                path=folder/'native_delta'/policy/'trades.csv';delta=read(path)
                delta['source_trade_id']=delta.trade_id;delta['source_ledger']=str(path.relative_to(ROOT));pieces.append(delta)
            combined=pd.concat(pieces,ignore_index=True).sort_values(['entry_i','trade_id'])
            expected=candidates.set_index(['pair_id','population']).sort_index()
            actual=combined.set_index(['pair_id','population']).sort_index()
            pd.testing.assert_index_equal(expected.index,actual.index)
            np.testing.assert_allclose(expected[FIELDS],actual[FIELDS],rtol=0,atol=1e-8)
            np.testing.assert_allclose(combined.net_r,combined.side*(combined.exit_price-combined.entry)/combined.risk-cfg['round_trip_cost']*combined.entry/combined.risk,rtol=0,atol=1e-7)
            # The source study isolated this official archive/API revision.
            if symbol=='ETH':
                overlap=(pd.to_datetime(combined.entry_time,utc=True)<pd.Timestamp('2022-12-18T11:30Z'))&(pd.to_datetime(combined.exit_time,utc=True)>=pd.Timestamp('2022-12-18T02:00Z'))
                assert not overlap.any()
            combined.to_csv(folder/name,index=False)


def analyze(cfg,dest):
    from yoyo.evaluation.pepe_range_exits import prepare as context
    from yoyo.evaluation.range_mtf_confluence import gate_rows
    from yoyo.evaluation.range_mtf_confluence_window import window_gate
    from yoyo.evaluation.range_fiveyear import describe,key_name
    from yoyo.evaluation.range_management_study import holm
    stats=[];keys=[];checks=[]
    prior=json.loads((ROOT/cfg['source']/'summary.json').read_text())
    for spec in cfg['symbols']:
        symbol=spec['symbol'];folder=dest/symbol;source=ROOT/cfg['source']/symbol
        five=read(source/'candles.csv.gz')
        five.index=pd.DatetimeIndex(pd.to_datetime(five.pop('ts'),unit='ms',utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
        ctx=context(five);matches=read(folder/'signal_matches.csv');base=read(folder/'candidates.csv')
        gates=gate_rows(ctx,matches,base);gates['window_both']=window_gate(gates)
        revision_mtf_parity=None
        if symbol=='ETH':
            alternative=gate_rows(context(revision_alternative(five,source)),matches,base)
            pd.testing.assert_frame_equal(gates[alternative.columns],alternative)
            np.testing.assert_array_equal(gates.window_both,window_gate(alternative))
            revision_mtf_parity=True
        gates.to_csv(folder/'admission.csv',index=False)
        prefixes=0
        for idx in sorted(set(np.linspace(0,len(gates)-1,6,dtype=int))):
            row=gates.iloc[idx];pid=int(row.pair_id);end=int(row.entry_i)+1
            short=gate_rows(context(five.iloc[:end]),matches[matches.pair_id==pid],base[base.pair_id==pid])
            pd.testing.assert_frame_equal(gates.loc[gates.pair_id==pid,short.columns].reset_index(drop=True),short)
            prefixes+=1
        annual=read(ROOT/cfg['annual_sweep']/symbol/'admission.csv')
        recent=gates[pd.to_datetime(gates.entry_time,utc=True)>=pd.Timestamp(cfg['annual_start'])]
        fields=['side','entry_time','arrow_close','key','level','strict_both_now','paired_arrow_was_mtf','window_both']
        pd.testing.assert_frame_equal(recent[fields].reset_index(drop=True),annual[fields].reset_index(drop=True),check_dtype=False,rtol=0,atol=1e-10)
        both=set(gates.loc[gates.window_both,'pair_id'])
        original_matches=read(source/'signal_matches.csv');original=read(source/'trades.csv')
        rows=read(folder/'trades.csv');sensitivity=read(folder/'sensitivity.csv')
        for label,ledger,selection in [('baseline',original,original_matches),('sweep20',rows,matches),('sweep20_mtf',rows,matches[matches.pair_id.isin(both)])]:
            entry=pd.to_datetime(selection.entry_time,utc=True)
            periods=[('all',np.ones(len(selection),bool)),('recent2m',entry>=pd.Timestamp(cfg['recent_start']))]
            periods += [(str(year+1),(entry>=pd.Timestamp(cfg['start'])+pd.DateOffset(years=year))&(entry<pd.Timestamp(cfg['start'])+pd.DateOffset(years=year+1))) for year in range(5)]
            for period,mask in periods:
                ids=selection.loc[mask,'pair_id'].tolist()
                stat=dict(symbol=symbol,variant=label,period=period,**describe(ledger,ids,cfg['seed']))
                if label!='baseline':
                    stat['strict_tie_net_r']=float(sensitivity.loc[(sensitivity.population=='signal')&sensitivity.pair_id.isin(ids),'net_r'].sum())
                stats.append(stat)
                if label=='baseline' and period=='all':
                    old=next(x for x in prior['overall'] if x['symbol']==symbol)
                    for k in ['n','wins','net_r','random_net_r']:np.testing.assert_allclose(stat[k],old[k],rtol=0,atol=1e-8)
        selected=matches[matches.pair_id.isin(both)].copy();selected['key_group']=selected.key.map(key_name)
        for key,part in selected.groupby('key_group',sort=False):
            keys.append(dict(symbol=symbol,key=key,**describe(rows,part.pair_id.tolist(),cfg['seed'])))
        detail=rows[(rows.population=='signal')&rows.pair_id.isin(both)].merge(selected.drop(columns=['entry_i','entry','stop','side','entry_time']),on='pair_id',validate='one_to_one')
        detail.to_csv(folder/'combined_signal_details.csv',index=False)
        rows[rows.pair_id.isin(both)].to_csv(folder/'combined_trades.csv',index=False)
        checks.append(dict(symbol=symbol,sweep_n=len(gates),combined_n=len(both),mtf_prefix_checks=prefixes,
                           revision_mtf_parity=revision_mtf_parity,
                           annual_sweep_n=len(recent),annual_combined_n=int(recent.window_both.sum()),annual_gate_parity=True))
    tests=[r for r in stats if r['variant']=='sweep20_mtf' and r['period']=='all']
    for row,p in zip(tests,holm([r['p_one_sided'] for r in tests])):row['holm_two_coins_p']=p
    dump(dest/'summary.json',dict(config=cfg,rows=stats,by_key=keys,checks=checks,
        limitations='Historical admitted filters; no tuning. Shared15m oscillator means the modules are not independent signals. One matched random control per event, without requiring it to satisfy sweep/MTF. IndependentR, not account return; no funding/slippage. No boundary trades expected.',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    pd.DataFrame(stats).to_csv(dest/'comparison.csv',index=False)
    pd.DataFrame(keys).to_csv(dest/'by_key.csv',index=False)
    print(json.dumps([r for r in stats if r['period']=='all'],indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=CONFIG)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--analyze',action='store_true')
    args=parser.parse_args();cfg=json.loads(args.config.read_text())
    if not args.execute and not args.analyze:parser.error('Choose --execute or --analyze')
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE,str(args.config)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generating outputs')
    if args.execute:execute(cfg,args.config.parent)
    if args.analyze:analyze(cfg,args.config.parent)


if __name__=='__main__':main()
