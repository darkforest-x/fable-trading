"""Extend frozen V2.31 admissions and confirmed-pivot exits to five OKX years.

Features use completed 15m OHLC, Stoch5/3/3, pre-touch Monday extrema,
five pre-arrow bodies, +/-2 touch bars and the admitted midpoint-side gate.
No sweep, MTF, future-body gate, parameter search or new exit engine.
Reuse native annual paths only after full overlapping OHLC/entry identity
checks. Download only absent archive minutes from the same official OKX API;
verify adjacent overlap and never synthesize missing candles. A bounded,
explicitly listed archive/API revision is retained for admission/control
sensitivity; known archive rows are never silently overwritten.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run

CONFIG = ROOT/'experiments/active/exp-range-v231-fiveyear-20261021-v1/config.json'
CODE = ['yoyo/evaluation/range_fiveyear.py',
        'yoyo/evaluation/monday_range_v2_31_reference.py',
        'yoyo/evaluation/range_v231_midpoint.py',
        'yoyo/evaluation/range_v221_compare.py']
OHLC = ['open', 'high', 'low', 'close']


def merge_minutes(parts):
    """Reject conflicting duplicate timestamps before combining sources."""
    frame = pd.concat(parts, ignore_index=True)
    dup = frame[frame.duplicated('ts', keep=False)]
    if not dup.empty and (dup.groupby('ts')[OHLC].nunique().max(axis=1)>1).any():
        raise ValueError('Conflicting official minute OHLC')
    return frame.drop_duplicates('ts').sort_values('ts').reset_index(drop=True)


def load_minutes(cfg, spec, folder):
    from src.data.fetch_okx import API, _request
    lo, hi = [pd.Timestamp(cfg[k]).value//1_000_000 for k in ('warmup','end')]
    parts = []
    for path in spec['inputs']:
        for f in pd.read_csv(ROOT/path, usecols=['ts',*OHLC],
                             float_precision='round_trip', chunksize=250_000):
            parts.append(f[(f.ts>=lo)&(f.ts<hi)])
    frame = merge_minutes(parts)
    expected = np.arange(lo, hi, 60000, dtype=np.int64)
    missing = np.setdiff1d(expected, frame.ts.to_numpy())
    if len(missing)>100:
        raise ValueError(f'Unexpected large data gap: {len(missing)} minutes')
    original_missing = missing.copy()
    pages, patches, revisions = [], [], []
    while len(missing):
        # The 100-bar page includes both missing bars and known neighbours.
        at = int(missing[-1]); end = at + 10*60000
        url = f'{API}?instId={spec["symbol"]}-USDT-SWAP&bar=1m&after={end}&limit=100'
        target = folder/'inputs'/f'okx_gap_{at}.json'
        target.parent.mkdir(exist_ok=True)
        payload = json.loads(target.read_text()) if target.exists() else _request(url)
        if payload.get('code')!='0':
            raise ValueError(f'OKX gap request failed: {payload.get("msg")}')
        data = payload.get('data',[])
        fetched = pd.DataFrame([[int(r[0]),*[float(x) for x in r[1:5]]]
                                for r in data if len(r)>=9 and str(r[8])=='1'],
                               columns=['ts',*OHLC])
        overlap = fetched.merge(frame,on='ts',suffixes=('_api','_archive'),validate='one_to_one')
        if overlap.empty:
            raise ValueError('Gap page has no known neighbours for verification')
        if not target.exists():
            dump(target,payload)
        changed=np.any(overlap[[k+'_api' for k in OHLC]].to_numpy()!=
                       overlap[[k+'_archive' for k in OHLC]].to_numpy(),axis=1)
        different=overlap[changed].copy()
        if len(different):
            bounds=spec.get('known_revision_window')
            if bounds is None:
                raise ValueError('Unexpected official archive/API conflict')
            low,high=[pd.Timestamp(x).value//1_000_000 for x in bounds]
            if not different.ts.between(low,high,inclusive='left').all():
                raise ValueError('Official revision outside the bounded audit window')
            revisions.append(different)
        patch = fetched[fetched.ts.isin(missing)]
        if patch.empty:
            raise ValueError('Official API cannot recover archive gap')
        patches.append(patch)
        pages.append(dict(url=url,path=str(target.relative_to(ROOT)),
                          recovered=len(patch),matching_neighbours=len(overlap)-len(different),
                          revised_neighbours=len(different)))
        frame = merge_minutes([frame,patch])
        missing = np.setdiff1d(expected,frame.ts.to_numpy())
    np.testing.assert_array_equal(frame.ts,expected)
    values=frame[OHLC]
    if not np.isfinite(values).all().all() or (values<=0).any().any():
        raise ValueError('Invalid minute values')
    if (frame.high<values.max(axis=1)).any() or (frame.low>values.min(axis=1)).any():
        raise ValueError('Invalid minute OHLC range')
    if patches:
        pd.concat(patches).sort_values('ts').to_csv(folder/'inputs/gap_minutes.csv',index=False)
    if revisions:
        pd.concat(revisions).drop_duplicates('ts').sort_values('ts').to_csv(folder/'inputs/overlap_revisions.csv',index=False)
    frame.index=pd.DatetimeIndex(pd.to_datetime(frame.pop('ts'),unit='ms',utc=True)).as_unit('ns')
    return frame,dict(minute_rows=len(frame),missing_archive_minutes=len(original_missing),
                     remaining_missing=0,pages=pages,overlap_revisions=sum(len(x) for x in revisions),
                     sources=[file_stamp(ROOT/p) for p in spec['inputs']])


def prepare(cfg,spec,dest):
    from yoyo.evaluation.btc_range_wick_fanshen import monday_levels
    from yoyo.evaluation.monday_range_v2_2_reference import aggregate as minute_aggregate, signals
    from yoyo.evaluation.pepe_range_exits import aggregate
    from yoyo.evaluation.monday_range_v2_31_reference import select_matches
    from yoyo.evaluation.range_v231_midpoint import exact_stops
    from yoyo.evaluation.range_v221_compare import controls, candidate_rows
    folder=dest/spec['symbol'];folder.mkdir(exist_ok=True)
    identity=dict(config=cfg,spec=spec,sources=[file_stamp(ROOT/p) for p in spec['inputs']],
                  code=[file_stamp(ROOT/p) for p in CODE],
                  annual=[file_stamp(ROOT/cfg['annual_results']/spec['symbol']/p) for p in
                          ('signal_matches.csv','candidates.csv','pivot/trades.csv','pivot/sensitivity.csv')]
                         +[file_stamp(ROOT/cfg['annual_data']/spec['symbol']/'candles.csv.gz')])
    receipt=folder/'preparation.json'
    if receipt.exists():
        saved=json.loads(receipt.read_text())
        if saved['identity']!=identity:
            raise ValueError('Preparation changed; preserve old results')
        return saved
    minutes,quality=load_minutes(cfg,spec,folder)
    five=minute_aggregate(minutes,5)
    alternate=None
    if quality['overlap_revisions']:
        revision=pd.read_csv(folder/'inputs/overlap_revisions.csv',float_precision='round_trip')
        revision.index=pd.to_datetime(revision.ts,unit='ms',utc=True)
        buckets=revision.index.floor('5min').unique()
        affected=minutes[minutes.index.floor('5min').isin(buckets)].copy()
        affected.loc[revision.index,OHLC]=revision[[k+'_api' for k in OHLC]].to_numpy()
        alternate=five.copy();updated=minute_aggregate(affected,5)
        alternate.loc[updated.index,OHLC]=updated[OHLC]
    del minutes
    q=aggregate(five,15)
    assert (five.index.to_series().diff().dropna()==pd.Timedelta(minutes=5)).all()
    grid,names,weeks,_=monday_levels(q,cfg['ratios'],cfg['timezone'])
    arrows=signals(q).to_numpy()
    kwargs=dict(before=5,wait_bars=2,penetration=.1,buffer=.002,
                tick=spec['tick'],timezone=cfg['timezone'],start=cfg['start'])
    selected=select_matches(q,arrows,grid,names,weeks,**kwargs)
    rows=exact_stops(selected,q,spec['tick'],cfg['end'])
    records=[dict(r,pair_id=i) for i,r in enumerate(rows)]
    probes=sorted({r['entry_i']+d for r in records[::max(1,len(records)//8)] for d in (0,1)})
    for stop in probes:
        got=select_matches(q.iloc[:stop],arrows[:stop],grid[:stop],names,weeks[:stop],**kwargs)
        assert got==[r for r in selected if r['entry_i']<stop]
    annual=ROOT/cfg['annual_results']/spec['symbol']
    old=pd.read_csv(annual/'signal_matches.csv',float_precision='round_trip')
    overlap=pd.DataFrame(records)
    overlap=overlap[pd.to_datetime(overlap.entry_time,utc=True)>=pd.Timestamp(cfg['annual_start'])]
    fields=['signal_time','touch_time','entry_time','side','key','level','entry','stop']
    pd.testing.assert_frame_equal(overlap[fields].reset_index(drop=True),old[fields].reset_index(drop=True),
                                  check_dtype=False,rtol=0,atol=1e-10)
    prior_five=pd.read_csv(ROOT/cfg['annual_data']/spec['symbol']/'candles.csv.gz',float_precision='round_trip')
    prior_five.index=pd.DatetimeIndex(pd.to_datetime(prior_five.pop('ts'),unit='ms',utc=True)
                                     +pd.Timedelta(minutes=5)).as_unit('ns')
    pd.testing.assert_frame_equal(five.loc[prior_five.index,OHLC],prior_five[OHLC],check_freq=False)
    offset=int(five.index.get_indexer([prior_five.index[0]])[0])
    old_to_new=dict(zip(old.pair_id.astype(int),overlap.pair_id.astype(int)))
    prior_candidates=pd.read_csv(annual/'candidates.csv',float_precision='round_trip')
    reserved={}; matched={}
    for row in prior_candidates[prior_candidates.population=='random'].itertuples():
        idx=int(q.index.get_indexer([pd.Timestamp(row.entry_time)])[0])
        assert idx>=0
        matched[old_to_new[row.pair_id]]=idx;reserved[idx]=1
    new=[r for r in records if r['pair_id'] not in matched]
    exclusions=arrows.copy()
    exclusions[list(reserved)+[r['entry_i'] for r in records]]=1
    additional,half,week,bucket=controls(q,exclusions,new,cfg,spec)
    matched.update(additional)
    assert len(set(matched.values()))==len(records)
    candidates=candidate_rows(five,q,records,matched,(half,week,bucket),spec['tick'])
    if alternate is not None:
        from yoyo.evaluation.monday_range_v2_2_reference import atr
        aq=aggregate(alternate,15)
        ag,an,aw,_=monday_levels(aq,cfg['ratios'],cfg['timezone'])
        arows=exact_stops(select_matches(aq,signals(aq).to_numpy(),ag,an,aw,**kwargs),aq,spec['tick'],cfg['end'])
        assert arows==rows, 'Official revision changes admitted signals'
        abucket=np.digitize(atr(aq,14).to_numpy()/aq.close.to_numpy(),cfg['volatility_edges'])
        used=[r['entry_i'] for r in records]+list(matched.values())
        np.testing.assert_array_equal(abucket[used],bucket[used])
        quality['revision_admission_and_control_bucket_parity']=True
    # Preserve each cached annual random price/risk exactly, not a rerounded copy.
    for row in prior_candidates.itertuples():
        pid=old_to_new[row.pair_id]
        mask=(candidates.pair_id==pid)&(candidates.population==row.population)
        at=candidates.index[mask][0]
        assert candidates.loc[at,'entry_i']==row.entry_i+offset
        assert candidates.loc[at,'entry']==row.entry
        assert abs(candidates.loc[at,'stop']-row.stop)<spec['tick']*1e-7
        candidates.loc[at,'stop']=row.stop
        qi=int(q.index.get_indexer([pd.Timestamp(row.entry_time)])[0])
        si=int(overlap.loc[overlap.pair_id==pid,'entry_i'].iloc[0])
        assert (half[qi],week[qi],bucket[qi])==(half[si],week[si],bucket[si])
    output=five.copy();output.insert(0,'ts',five.index.asi8//1_000_000-300000)
    output.to_csv(folder/'candles.csv.gz',index=False)
    pd.DataFrame(records).to_csv(folder/'signal_matches.csv',index=False)
    candidates.to_csv(folder/'candidates.csv',index=False)
    delta=candidates[~candidates.pair_id.isin(old_to_new.values())]
    delta.to_csv(folder/'delta_candidates.csv',index=False)
    native=dict(schema=1,experiment_id=cfg['experiment_id'],engine='nautilus',symbol=spec['symbol'],
                tick=spec['tick'],market_tick=spec['tick'],entry_mode='provided',
                data=str((folder/'candles.csv.gz').relative_to(ROOT)),
                entry_candidates=str((folder/'delta_candidates.csv').relative_to(ROOT)),
                stop_window_5m=1,buffer_percent=0,maximum_risk_percent=0,round_trip_cost=cfg['round_trip_cost'],
                policies=[dict(name='pivot',confirmed_pivot_exit=True,opposite_hour_exit=False),
                          dict(name='strict_ties',confirmed_pivot_exit=True,strict_pivot_ties=True,opposite_hour_exit=False)],
                output=str((folder/'native_delta').relative_to(ROOT)))
    dump(folder/'native.json',native)
    saved=dict(identity=identity,quality=quality,signals=len(records),controls=len(records),
               reused_annual_signals=len(old),new_signals=len(new),annual_ohlc_parity=len(prior_five),
               annual_signal_parity=True,causal_prefix_checks=len(probes),offset=offset,
               old_to_new=old_to_new,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(receipt,saved)
    print(json.dumps(dict(symbol=spec['symbol'],signals=len(records),new_signals=len(new),
                          reused_annual_signals=len(old),recovered_minutes=quality['missing_archive_minutes'])),flush=True)
    return saved


def combine(cfg,spec,dest,meta):
    folder=dest/spec['symbol'];annual=ROOT/cfg['annual_results']/spec['symbol']
    candidates=pd.read_csv(folder/'candidates.csv',float_precision='round_trip')
    mapping={int(k):v for k,v in meta['old_to_new'].items()}
    for policy,name in [('pivot','trades.csv'),('strict_ties','sensitivity.csv')]:
        cached=pd.read_csv(annual/'pivot'/name,float_precision='round_trip')
        cached['source_trade_id']=cached.trade_id;cached['source_ledger']=str((annual/'pivot'/name).relative_to(ROOT))
        cached['pair_id']=cached.pair_id.map(mapping)
        for col in ['entry_i','end_i','exit_i','arm_i','be_trigger_i','be_effective_i']:
            cached[col]=np.where(cached[col]>=0,cached[col]+meta['offset'],cached[col])
        cached['trade_id']=cached.population+'-'+cached.pair_id.astype(str)
        metadata=candidates.set_index(['pair_id','population'])
        for col in ['segment','week','bucket']:
            cached[col]=[metadata.loc[(r.pair_id,r.population),col] for r in cached.itertuples()]
        delta=pd.read_csv(folder/'native_delta'/policy/'trades.csv',float_precision='round_trip')
        delta['source_trade_id']=delta.trade_id;delta['source_ledger']=str((folder/'native_delta'/policy/'trades.csv').relative_to(ROOT))
        rows=pd.concat([cached,delta],ignore_index=True).sort_values(['entry_i','trade_id'])
        expected=candidates.set_index(['pair_id','population']).sort_index()
        actual=rows.set_index(['pair_id','population']).sort_index()
        pd.testing.assert_index_equal(expected.index,actual.index)
        for col in ['entry_i','end_i','side','entry','stop','segment','week','bucket']:
            np.testing.assert_allclose(actual[col],expected[col],rtol=0,atol=1e-8)
        np.testing.assert_allclose(rows.gross_r-rows.fee_r,rows.net_r,rtol=0,atol=1e-8)
        np.testing.assert_allclose(rows.fee_r,cfg['round_trip_cost']*rows.entry/rows.risk,rtol=0,atol=1e-7)
        if spec.get('known_revision_window'):
            lo,hi=map(pd.Timestamp,spec['known_revision_window'])
            # Include two future15m bars to cover pivot confirmation influence.
            hit=(pd.to_datetime(rows.entry_time,utc=True)<hi+pd.Timedelta(minutes=30))&(
                pd.to_datetime(rows.exit_time,utc=True)>=lo)
            assert not hit.any(), 'Trade path crosses ambiguous official revision; requires sensitivity replay'
        rows.to_csv(folder/name,index=False)


def key_name(value):
    if value=='0:up': return 'Monday low'
    if value=='0:down': return 'Monday high'
    return value.split(':')[0]


def describe(rows,ids,seed):
    from yoyo.evaluation.pepe_range_exits import inference,pairs
    s=rows[(rows.population=='signal')&rows.pair_id.isin(ids)]
    r=rows[(rows.population=='random')&rows.pair_id.isin(ids)]
    assert len(s)==len(r)==len(ids)
    wins=int((s.net_r>1e-9).sum());n=len(s);p=wins/n if n else 0.
    z=1.959963984540054;den=1+z*z/n if n else 1
    center=(p+z*z/(2*n))/den if n else 0.
    margin=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n))/den if n else 0.
    losses=-float(s.loc[s.net_r<0,'net_r'].sum())
    return dict(n=n,wins=wins,losses=int((s.net_r< -1e-9).sum()),flat=int((s.net_r.abs()<=1e-9).sum()),
                win_rate=p,wilson_95ci=[center-margin,center+margin],
                gross_win_rate=float((s.gross_r>1e-9).mean()),gross_r=float(s.gross_r.sum()),
                fee_r=float(s.fee_r.sum()),net_r=float(s.net_r.sum()),mean_net_r=float(s.net_r.mean()),
                mean_net_bp=float(s.net_notional_bp.mean()),random_net_r=float(r.net_r.sum()),
                random_win_rate=float((r.net_r>1e-9).mean()),random_mean_net_bp=float(r.net_notional_bp.mean()),
                pf=float(s.loc[s.net_r>0,'net_r'].sum()/losses) if losses else None,
                boundary_n=int(s.reason.eq(0).sum()),boundary_net_r=float(s.loc[s.reason==0,'net_r'].sum()),
                net_r_without_best5=float(s.net_r.sum()-s.net_r.nlargest(5).sum()),
                initial_stops=int(s.reason.eq(1).sum()),pivot_exits=int(s.reason.eq(7).sum()),
                **inference(pairs(s,r),seed))


def analyze(cfg,dest):
    summaries=[];allkeys=[];years=[];sides=[];checks=[]
    for spec in cfg['symbols']:
        symbol=spec['symbol'];folder=dest/symbol
        rows=pd.read_csv(folder/'trades.csv',float_precision='round_trip')
        strict=pd.read_csv(folder/'sensitivity.csv',float_precision='round_trip')
        matches=pd.read_csv(folder/'signal_matches.csv',float_precision='round_trip')
        ids=matches.pair_id.tolist()
        result=dict(symbol=symbol,**describe(rows,ids,cfg['seed']))
        result['strict_ties_net_r']=float(strict.loc[strict.population=='signal','net_r'].sum())
        summaries.append(result)
        matches['key_group']=matches.key.map(key_name)
        for key,part in matches.groupby('key_group',sort=False):
            allkeys.append(dict(symbol=symbol,key=key,**describe(rows,part.pair_id.tolist(),cfg['seed'])))
        for side,part in matches.groupby('side'):
            sides.append(dict(symbol=symbol,side=int(side),**describe(rows,part.pair_id.tolist(),cfg['seed'])))
        entries=pd.to_datetime(matches.entry_time,utc=True)
        for year in range(5):
            lo=pd.Timestamp(cfg['start'])+pd.DateOffset(years=year)
            hi=pd.Timestamp(cfg['start'])+pd.DateOffset(years=year+1)
            chosen=matches[(entries>=lo)&(entries<hi)]
            years.append(dict(symbol=symbol,start=lo.isoformat(),end=hi.isoformat(),
                              **describe(rows,chosen.pair_id.tolist(),cfg['seed'])))
        paired=rows[rows.population=='signal'].merge(matches.drop(columns=['entry_i','entry','stop','side','entry_time']),on='pair_id',validate='one_to_one')
        paired.to_csv(folder/'signal_details.csv',index=False)
        checks.append(dict(symbol=symbol,rows=len(rows),unique_trade_ids=bool(rows.trade_id.is_unique),
                           counts_reconcile=sum(x['n'] for x in allkeys if x['symbol']==symbol)==len(matches),
                           native_fee_checks=len(rows)))
    summary=dict(start=cfg['start'],end=cfg['end'],round_trip_cost=cfg['round_trip_cost'],
                 rule='Original V2.31, no sweep or MTF; exact wick stop; first profitable 15m2/2 pivot confirmation-close exit.',
                 overall=summaries,by_key=allkeys,by_year=years,by_side=sides,validation=checks,
                 limitations='Independent R, not account returns. No funding/spread/slippage. Historical exploration; one matched control per signal; Wilson intervals are descriptive and do not adjust for temporal dependence.',
                 reproduction='PYTHONPATH=. /Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.range_fiveyear',
                 training_eligible=False,production_eligible=False)
    dump(dest/'summary.json',summary)
    pd.DataFrame(allkeys).to_csv(dest/'by_key.csv',index=False)
    pd.DataFrame(years).to_csv(dest/'by_year.csv',index=False)
    print(json.dumps(summaries,indent=2),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=CONFIG)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--symbol',choices=['BTC','ETH'])
    args=parser.parse_args();cfg=json.loads(args.config.read_text());dest=args.config.parent
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE,str(args.config)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generation')
    for spec in cfg['symbols']:
        if args.symbol and args.symbol!=spec['symbol']:
            continue
        meta=prepare(cfg,spec,dest)
        if not args.prepare_only:
            run(dest/spec['symbol']/'native.json')
            combine(cfg,spec,dest,meta)
    if not args.prepare_only:
        analyze(cfg,dest)


if __name__=='__main__':
    main()
