"""Add an inclusive +/-5H H1-arrow gate to frozen V2.37 5m/15m pairs.

Owner2026-10-22: H1 candle position is the center; the matching15m arrow
may be up to five H1 candles before or after it, including the boundary.
Keep the original earliest5m/15m pair within +/-25minute OPEN positions.
Only completed native Stoch arrows are read. Actual entry waits until all
three closes and recomputes the unchanged5bar+0.05percent structural stop.
Original5R/4ATR/H1 exit and20bp remain frozen; reuse identical native paths.
"""
from __future__ import annotations

import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_v237_twoyear import read, close_frame, project_entries, controls, metrics
from yoyo.evaluation.pepe_range_exits import aggregate, signals, prepare, replay, inference
from yoyo.evaluation.range_management_study import holm

EXP=ROOT/'experiments/active/exp-range-v237-triple-resonance-20261022-v1'
CONFIG=EXP/'config.json'
HOUR=3_600_000_000_000
KEYS=['population','entry_i','side','entry','stop','end_i']
OUT=['exit_i','exit_price','reason','peak_r','arm_i','protection','gross_r','fee_r','net_r','net_notional_bp','exit_time']


def match_hour(ev,hour_open,hour_close,hour_side,cutoff,window=5):
    """Earliest same-side completed H1 open within +/-window hours of15m open.

    Pair positions use opens; availability uses closes. Incomplete no-match
    windows are pending rather than rejected. No prices/outcomes are inputs.
    """
    assert window==5 and np.all(np.diff(hour_open)>0)
    rows=[];width=window*HOUR
    for r in ev.to_dict('records'):
        center=int(r['matched_ns']);side=int(r['side'])
        ids=np.flatnonzero((hour_side==side)&(hour_close<=cutoff)&
            (hour_open>=center-width)&(hour_open<=center+width))
        old=int(r['publication_ns'])
        r.update(old_publication_ns=old,window_end_ns=center+width+HOUR)
        if len(ids):
            k=int(ids[0]);publication=max(old,int(hour_close[k]))
            r.update(status='matched',hour_open_ns=int(hour_open[k]),hour_close_ns=int(hour_close[k]),
                publication_ns=publication,extra_wait_minutes=(publication-old)/60_000_000_000,
                retrospective=publication>int(r['original_close_ns']))
        else:
            # Last admissible H1 open is on its native clock, not an arbitrary
            # quarter-hour position. This avoids falsely pending completed windows.
            last_open=((center+width)//HOUR)*HOUR
            r['window_end_ns']=last_open+HOUR
            r['status']='pending' if cutoff<r['window_end_ns'] else 'rejected'
        rows.append(r)
    return pd.DataFrame(rows)


def build(symbol):
    cfg=json.loads(CONFIG.read_text());basecfg=json.loads((ROOT/cfg['source']/'config.json').read_text())
    spec=next(x for x in cfg['symbols'] if x['symbol']==symbol);tick=spec['tick']
    source=ROOT/cfg['source']/symbol;dest=EXP/symbol;dest.mkdir(exist_ok=True)
    if (dest/'preparation.json').exists():return
    frame=close_frame(read(source/'candles.csv.gz'));hour=aggregate(frame,60)
    hs=signals(hour);hi=np.flatnonzero(hs.to_numpy());ht=hour.index.as_unit('ns').asi8
    ev=read(source/'events.csv');ev=ev[ev.lane.eq(5)].copy()
    matched=match_hour(ev,ht[hi]-HOUR,ht[hi],hs.to_numpy()[hi],frame.index[-1].value)
    accepted=matched[matched.status.eq('matched')].copy()
    assert len(matched)==len(ev) and matched.event_id.is_unique
    prefix_checks=[]
    for end in [len(frame)//2,len(frame)-100,len(frame)-3]:
        cut=frame.index[end-1].value
        # Only original5m/15m pairs already knowable in that prefix can enter.
        known=ev[ev.publication_ns<=cut]
        hprefix=aggregate(frame.iloc[:end],60);sp=signals(hprefix);ix=np.flatnonzero(sp.to_numpy())
        times=hprefix.index.as_unit('ns').asi8
        got=match_hour(known,times[ix]-HOUR,times[ix],sp.to_numpy()[ix],cut)
        actual=got[got.status.eq('matched')]
        expected=accepted[accepted.publication_ns<=cut]
        cols=['event_id','hour_open_ns','hour_close_ns','publication_ns']
        pd.testing.assert_frame_equal(actual[cols].reset_index(drop=True).astype({'hour_open_ns':'int64','hour_close_ns':'int64'}),
            expected[cols].reset_index(drop=True).astype({'hour_open_ns':'int64','hour_close_ns':'int64'}))
        prefix_checks.append(dict(end=int(end),matched=len(actual)))
    accepted[['hour_open_ns','hour_close_ns']]=accepted[['hour_open_ns','hour_close_ns']].astype('int64')
    actual=project_entries(frame,accepted,tick,'causal')
    # Matching at the final source close has no subsequent executable interval.
    executable=set(actual.event_id)
    matched.loc[matched.status.eq('matched')&~matched.event_id.isin(executable),'status']='boundary_no_entry'
    allentries=controls(frame,actual,tick,basecfg,cfg['seed']+spec['seed_offset'])
    canonical=allentries.drop_duplicates(KEYS).copy().reset_index(drop=True)
    canonical['pair_id']=np.arange(len(canonical))
    occurrences=allentries.merge(canonical[KEYS+['pair_id']],on=KEYS,validate='many_to_one')
    old=read(source/'candidates.csv')
    mapped=canonical.merge(old[KEYS+['pair_id']].rename(columns={'pair_id':'source_pair_id'}),
        on=KEYS,how='left',validate='one_to_one')
    new=mapped[mapped.source_pair_id.isna()].drop(columns='source_pair_id')
    assert not new.empty
    for name,table in [('hour_matches.csv',matched),('occurrences.csv',occurrences),
        ('candidates.csv',canonical),('cache_map.csv',mapped),('new_candidates.csv',new)]:
        table.to_csv(dest/name,index=False)
    native=json.loads((source/'native.json').read_text())
    native.update(experiment_id=cfg['experiment_id'],
        entry_candidates=str((dest/'new_candidates.csv').relative_to(ROOT)),
        output=str((dest/'native').relative_to(ROOT)))
    dump(dest/'native.json',native)
    receipt=dict(symbol=symbol,source_signals=len(ev),statuses=matched.status.value_counts().to_dict(),
        admitted=len(actual),extra_wait=int(actual.extra_wait_minutes.gt(0).sum()),
        median_extra_wait_minutes=float(actual.extra_wait_minutes.median()),
        max_extra_wait_minutes=float(actual.extra_wait_minutes.max()),
        unique_positions=len(canonical),reused_positions=int(mapped.source_pair_id.notna().sum()),
        new_positions=len(new),prefix_checks=prefix_checks,
        inputs=[file_stamp(source/n) for n in ['candles.csv.gz','events.csv','candidates.csv','results.csv.gz','native/baseline/trades.csv']],
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(dest/'preparation.json',receipt);print(json.dumps(receipt),flush=True)


def assemble(symbol):
    cfg=json.loads(CONFIG.read_text());tick=next(x['tick'] for x in cfg['symbols'] if x['symbol']==symbol)
    source=ROOT/cfg['source']/symbol;dest=EXP/symbol
    mapping=read(dest/'cache_map.csv');source_native=read(source/'native/baseline/trades.csv').set_index('pair_id')
    new=read(dest/'native/baseline/trades.csv').sort_values('pair_id').reset_index(drop=True)
    inputs=read(dest/'new_candidates.csv').sort_values('pair_id').reset_index(drop=True)
    for col in ['pair_id',*KEYS,'risk']:
        np.testing.assert_array_equal(new[col],inputs[col])
    # Reuse the already-established independent C++ oracle for changed paths.
    frame=close_frame(read(source/'candles.csv.gz'));ctx=prepare(frame)
    oracle=replay(ctx,inputs[['entry_i','side','entry','stop','end_i']].to_numpy(float),tick=tick)
    for field,col in [('exit_i',0),('reason',2),('arm_i',4),('steps',7)]:
        np.testing.assert_array_equal(new[field],oracle[:,col])
    for field,col in [('exit_price',1),('protection',5)]:
        np.testing.assert_allclose(new[field],oracle[:,col],atol=tick*1e-7,rtol=0)
    np.testing.assert_allclose(new.peak_r,oracle[:,3],atol=1e-7,rtol=0)
    cached=mapping[mapping.source_pair_id.notna()]
    reuse=source_native.loc[cached.source_pair_id.astype(int)].reset_index()
    for col in KEYS:
        np.testing.assert_array_equal(reuse[col],cached[col])
    reuse['pair_id']=cached.pair_id.to_numpy(int)
    combined=pd.concat([reuse,new],ignore_index=True).sort_values('pair_id')
    assert combined.pair_id.is_unique and set(combined.pair_id)==set(mapping.pair_id)
    np.testing.assert_allclose(combined.net_r,combined.gross_r-.002*combined.entry/combined.risk,atol=1e-7,rtol=0)
    combined.to_csv(dest/'combined_native.csv.gz',index=False)
    occ=read(dest/'occurrences.csv')
    result=occ.merge(combined[['pair_id',*OUT]],on='pair_id',validate='many_to_one')
    result['arm']='triple_causal'
    result.to_csv(dest/'results.csv.gz',index=False)
    dump(dest/'audit.json',dict(new_oracle_paths=len(new),cached_paths=len(reuse),
        all_entry_and_cost_checks=True,all_new_native_oracle_paths_match=True))
    print(symbol,'audited',len(new),'new and',len(reuse),'reused native positions',flush=True)


def score(rows):
    s=metrics(rows)
    closed=rows[rows.reason.ne(0)]
    s.update(mean_r=float(closed.net_r.mean()) if len(closed) else None,
        above3r=int(closed.net_r.gt(3+1e-7).sum()),
        median_risk_percent=float((rows.risk/rows.entry*100).median()) if len(rows) else None,
        median_fee_r=float(rows.fee_r.median()) if len(rows) else None)
    return s


def analyze():
    cfg=json.loads(CONFIG.read_text());rows=[];tests=[];impacts=[];examples=[]
    for spec in cfg['symbols']:
        coin=spec['symbol'];source=ROOT/cfg['source']/coin;dest=EXP/coin
        old=read(source/'results.csv.gz');old=old[old.lane.eq(5)]
        matches=read(dest/'hour_matches.csv');accepted=matches[matches.status.eq('matched')]
        new=read(dest/'results.csv.gz');new['mode']='causal'
        arms={'original_reference':old[old['mode'].eq('reference')],
            'original_causal':old[old['mode'].eq('causal')],
            'filtered_reference':old[old['mode'].eq('reference')&old.event_id.isin(accepted.event_id)],
            'filtered_old_causal':old[old['mode'].eq('causal')&old.event_id.isin(accepted.event_id)],
            'triple_causal':new}
        for name,group in arms.items():
            for period,part in [('all',group),('first_year',group[group.segment.eq(0)]),('later_year',group[group.segment.eq(1)])]:
                s=part[part.population.eq('signal')];r=part[part.population.eq('random')]
                assert len(s)==len(r)
                rows.append(dict(symbol=coin,arm=name,period=period,**score(s),random=score(r)))
        causal=new[new.population.eq('signal')];random=new[new.population.eq('random')]
        paired=causal[['event_id','week','net_r','reason']].merge(
            random[['event_id','net_r','reason']],on='event_id',suffixes=('','_random'),validate='one_to_one')
        paired=paired[paired.reason.ne(0)&paired.reason_random.ne(0)]
        tests.append(dict(symbol=coin,contrast='triple_vs_matched_random',**inference(paired,cfg['seed'])))
        baseline=old[old['mode'].eq('causal')&old.population.eq('signal')].copy()
        comparison=baseline[['event_id','entry_i','side','entry','stop','risk','net_r','reason','week','segment']].merge(
            causal[['event_id','entry_i','entry','stop','risk','net_r','reason','extra_wait_minutes']],
            on='event_id',how='left',suffixes=('_old','_new'),validate='one_to_one').merge(
            matches[['event_id','status']],on='event_id',validate='one_to_one')
        full=comparison[comparison.status.isin(['matched','rejected'])&comparison.reason_old.ne(0)&
            (comparison.reason_new.isna()|comparison.reason_new.ne(0))].copy()
        full['net_r']=full.net_r_new.fillna(0)
        full['net_r_random']=full.net_r_old
        tests.append(dict(symbol=coin,contrast='triple_vs_original_per_opportunity',**inference(full,cfg['seed'])))
        valid=comparison.status.eq('matched')&comparison.reason_old.ne(0)&comparison.reason_new.ne(0)
        admitted=comparison[valid]
        rejected=comparison[comparison.status.eq('rejected')&comparison.reason_old.ne(0)]
        impact=dict(symbol=coin,source_signals=len(baseline),matched=len(causal),
            rejected=len(rejected),pending=int(comparison.status.eq('pending').sum()),
            old_winners_rejected=int(rejected.net_r_old.gt(0).sum()),
            old_3r_rejected=int(rejected.net_r_old.gt(3+1e-7).sum()),
            rejected_old_net_r=float(rejected.net_r_old.sum()),
            actual_extra_wait=int(causal.extra_wait_minutes.gt(0).sum()),
            median_extra_wait=float(causal.extra_wait_minutes.median()),
            max_extra_wait=float(causal.extra_wait_minutes.max()),
            same_event_closed=len(admitted),old_selected_net_r=float(admitted.net_r_old.sum()),
            new_same_event_net_r=float(admitted.net_r_new.sum()),
            old_selected_winners=int(admitted.net_r_old.gt(0).sum()),
            new_same_event_winners=int(admitted.net_r_new.gt(0).sum()),
            mean_entry_move_bp=float((admitted.side*(admitted.entry_new-admitted.entry_old)/admitted.entry_old*10000).mean()),
            median_new_old_risk=float((admitted.risk_new/admitted.risk_old).median()),
            same_entry_events=int(admitted.entry_i_new.eq(admitted.entry_i_old).sum()))
        impacts.append(impact)
        comparison.to_csv(dest/'event_comparison.csv',index=False)
        for r in causal[causal.event_id.eq('5-216356')].to_dict('records') if coin=='BTC' else []:
            examples.append({k:r[k] for k in ['event_id','signal_ns','matched_ns','hour_open_ns',
                'hour_close_ns','publication_ns','entry_time','entry','stop','extra_wait_minutes','reason','net_r']})
        new.to_csv(dest/'results.csv.gz',index=False)
    for r,p in zip(tests,holm([r['p_one_sided'] for r in tests])):r['holm_p']=p
    pd.json_normalize(rows).to_csv(EXP/'comparison.csv',index=False)
    dump(EXP/'summary.json',dict(config=cfg,comparison=rows,impact=impacts,inference=tests,example=examples))
    print(json.dumps([r for r in rows if r['period']=='all'],ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['prepare','assemble','analyze']);p.add_argument('--symbol',choices=['BTC','ETH']);a=p.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generation')
    if a.stage=='analyze':analyze()
    else:
        assert a.symbol
        build(a.symbol) if a.stage=='prepare' else assemble(a.symbol)
