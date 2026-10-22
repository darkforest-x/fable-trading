"""Compare tighter initial stops on frozen causal V2.37 entry events.

Entry features are unchanged. Stop construction uses only the already-known
entry, side and original risk, with outward tick rounding. Native Nautilus
produces fills; the existing tick-aware stop/deadline oracle audits outcomes.
Each variant's own initial risk defines primary1R, per owner's correction.
No TP, breakeven, new price source, entry feature, or live/Pine change.
"""
from __future__ import annotations

import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.range_v237_2h_scaleout import load, initial_stop_expectation
from yoyo.evaluation.range_v237_twoyear import read

EXP=ROOT/'experiments/active/exp-range-v237-smaller-stop-20261022-v1'
CONFIG=EXP/'config.json'
EPS=1e-7


def tighten(base, fraction, tick):
    """Read entry/side/original risk only; no candle or outcome access."""
    if not 0<fraction<=1 or tick<=0:
        raise ValueError('Need positive tick and stop fraction in(0,1]')
    out=base.copy()
    out['original_stop']=base.stop
    out['original_risk']=base.risk
    if fraction<1:
        raw=(out.entry-out.side*out.original_risk*fraction)/tick
        out['stop']=np.where(out.side==1,np.floor(raw+1e-8),np.ceil(raw-1e-8))*tick
        out['risk']=out.side*(out.entry-out.stop)
    assert (out.risk>=tick-tick*1e-7).all()
    assert (out.risk<=out.original_risk+tick*1e-7).all()
    assert (out.risk>=out.original_risk*fraction-tick*1e-7).all()
    assert (out.risk<=out.original_risk*fraction+tick+tick*1e-7).all()
    for col in ['pair_id','entry_i','entry','side','end_i','population']:
        np.testing.assert_array_equal(out[col],base[col])
    return out


def score(g):
    """All120minute exits count as closed, including native reason0."""
    losses=-g.loc[g.net_r<0,'net_r'].sum()
    return dict(n=len(g),net_wins=int(g.net_r.gt(EPS).sum()),win_rate=float(g.net_r.gt(EPS).mean()),
        gross_win_rate=float(g.gross_r.gt(EPS).mean()),net_r=float(g.net_r.sum()),gross_r=float(g.gross_r.sum()),
        mean_net_r=float(g.net_r.mean()),pf=float(g.loc[g.net_r>0,'net_r'].sum()/losses) if losses else None,
        mean_net_bp=float(g.net_notional_bp.mean()),stops=int(g.reason.eq(1).sum()),stop_rate=float(g.reason.eq(1).mean()),
        time_exits=int(g.reason.eq(0).sum()),median_stop_percent=float((g.risk/g.entry*100).median()),
        median_fee_r=float(g.fee_r.median()),baseline_net_winners=int(g.baseline_net_r.gt(EPS).sum()),
        baseline_winners_stopped=int((g.baseline_net_r.gt(EPS)&g.reason.eq(1)).sum()),
        extra_stops=int((g.baseline_reason.ne(1)&g.reason.eq(1)).sum()),
        net_r_change=float((g.net_r-g.baseline_net_r).sum()),
        mean_bp_change=float((g.net_notional_bp-g.baseline_net_bp).mean()),
        original_risk_net_bridge=float(g.net_original_r.sum()),
        earlier_exit_count=int(g.exit_i.lt(g.baseline_exit_i).sum()))


def run_symbol(symbol):
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    cfg=json.loads(CONFIG.read_text())
    _,_,frame,base,occ,stamps=load(symbol)
    source=ROOT/cfg['source']
    assert cfg['hold_minutes']==120 and cfg['round_trip_cost']==.002
    assert (base.end_i==base.entry_i+24).all()
    tick=next(s['tick'] for s in cfg['symbols'] if s['symbol']==symbol)
    dest=EXP/symbol;dest.mkdir(exist_ok=True)
    baseline=read(source/symbol/'hold/trades.csv.gz').sort_values('pair_id').reset_index(drop=True)
    original=base.sort_values('pair_id').reset_index(drop=True)
    for col in ['pair_id','entry_i','end_i','side','entry','stop','risk']:
        np.testing.assert_array_equal(baseline[col],original[col])
    bars=frame.to_numpy();zeros=np.zeros(len(frame));records=[];audits=[]
    policy=ExitPolicy(target_from_entry=True,opposite_hour_exit=False,round_trip_cost=cfg['round_trip_cost'])
    for fraction in cfg['fractions']:
        name=f'stop{round(fraction*100)}';folder=dest/name;folder.mkdir(exist_ok=True)
        entries=tighten(original,fraction,tick)
        identity=dict(fraction=fraction,inputs=stamps+[file_stamp(CONFIG),file_stamp(source/symbol/'hold/trades.csv.gz')],
            code=[file_stamp(__file__),file_stamp(ROOT/'yoyo/layers/l3_backtest/nautilus_independent.py')])
        if fraction==1:
            result=baseline.copy();audit=dict(cached_native=True,positions=len(result),new_positions=0,new_fills=0)
        elif (folder/'receipt.json').exists():
            receipt=json.loads((folder/'receipt.json').read_text());assert receipt['identity']==identity
            result=read(folder/'trades.csv.gz');audit=receipt['audit']
        else:
            # Keep raw framework fills even if an independent audit later fails.
            if (folder/'native.csv.gz').exists():
                result=read(folder/'native.csv.gz');fills=read(folder/'fills.csv.gz')
                audit=dict(recovered_native=True,positions=len(result),fills=len(fills))
            else:
                result,fills,audit=run_native(frame,zeros,zeros,entries,policy,tick=tick,symbol=symbol)
                result.to_csv(folder/'native.csv.gz',index=False);fills.to_csv(folder/'fills.csv.gz',index=False)
            audit.update(new_positions=len(result),new_fills=len(fills))
        result=result.sort_values('pair_id').reset_index(drop=True)
        for col in ['pair_id','entry_i','end_i','side','entry','stop','risk']:
            np.testing.assert_array_equal(result[col],entries[col])
        expected=initial_stop_expectation(bars,entries,tick)
        np.testing.assert_array_equal(result.exit_i,expected[:,0]);np.testing.assert_array_equal(result.reason,expected[:,2])
        np.testing.assert_allclose(result.exit_price,expected[:,1],atol=tick*1e-7,rtol=0)
        np.testing.assert_allclose(result.net_r,result.gross_r-cfg['round_trip_cost']*result.entry/result.risk,atol=EPS,rtol=0)
        assert (result.exit_i<=baseline.exit_i).all()
        assert not (result.net_r.gt(EPS)&baseline.net_r.le(EPS)).any(), 'Tighter adverse-only stop cannot create a new winner'
        result.to_csv(folder/'trades.csv.gz',index=False)
        if fraction!=1:dump(folder/'receipt.json',dict(identity=identity,audit=audit,all_exit_audits_passed=True))
        # Occurrences own lane metadata; canonical dedup rows can retain an
        # unrelated lane/reference label, and are used only as economic paths.
        cols=['pair_id','risk','stop','exit_i','exit_price','reason','gross_r','fee_r','net_r','net_notional_bp']
        projection=result[cols].copy()
        projection['original_risk']=original.risk
        projection['baseline_net_r']=baseline.net_r
        projection['baseline_net_bp']=baseline.net_notional_bp
        projection['baseline_reason']=baseline.reason
        projection['baseline_exit_i']=baseline.exit_i
        projection['net_original_r']=result.net_r*result.risk/original.risk
        projection['fraction']=fraction;projection['arm']=name
        rows=occ.drop(columns=['risk','stop','end_i']).merge(projection,on='pair_id',validate='many_to_one')
        assert len(rows)==len(occ)
        records.append(rows);audits.append(dict(arm=name,**audit))
        print(symbol,name,'positions',len(result),'new',audit.get('new_positions',0),flush=True)
    ledger=pd.concat(records,ignore_index=True);ledger.to_csv(dest/'results.csv.gz',index=False)
    dump(dest/'completion.json',dict(symbol=symbol,occurrences=len(occ),causal_signal_counts=occ[occ.population=='signal'].groupby('lane').size().to_dict(),
        checks=audits,native_oracle_all_parity=True,all_entries_preserved=True,primary_r='each variant own risk',
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=ROOT).strip()))


def analyze():
    from yoyo.evaluation.pepe_range_exits import inference
    from yoyo.evaluation.range_management_study import holm
    cfg=json.loads(CONFIG.read_text());stats=[];tests=[];selected=[]
    for spec in cfg['symbols']:
        symbol=spec['symbol'];ledger=read(EXP/symbol/'results.csv.gz')
        for (lane,arm),g in ledger.groupby(['lane','arm'],sort=False):
            for period,subset in [('all',g),('first_year',g[g.segment.eq(0)]),('later_year',g[g.segment.eq(1)])]:
                s=subset[subset.population.eq('signal')];r=subset[subset.population.eq('random')]
                assert len(s)==len(r)
                stats.append(dict(symbol=symbol,lane=int(lane),arm=arm,period=period,
                    fraction=float(g.fraction.iloc[0]),**score(s),random=score(r)))
            s=g[g.population.eq('signal')];r=g[g.population.eq('random')]
            paired=s[['event_id','week','net_r']].merge(r[['event_id','net_r']],on='event_id',suffixes=('','_random'),validate='one_to_one')
            tests.append(dict(symbol=symbol,lane=int(lane),arm=arm,contrast='matched_random',**inference(paired,cfg['seed'])))
            if arm!='stop100':
                paired=s[['week','net_r','baseline_net_r']].rename(columns={'baseline_net_r':'net_r_random'})
                tests.append(dict(symbol=symbol,lane=int(lane),arm=arm,contrast='original_stop',**inference(paired,cfg['seed'])))
        for lane in [5,15]:
            subset=[r for r in stats if r['symbol']==symbol and r['lane']==lane]
            best=max([r for r in subset if r['period']=='first_year'],key=lambda x:x['mean_net_r'])
            selected.append(dict(symbol=symbol,lane=lane,chosen_first_year=best['arm'],first_year=best,
                later_year=next(r for r in subset if r['period']=='later_year' and r['arm']==best['arm']),
                full_history_best=max([r for r in subset if r['period']=='all'],key=lambda x:x['mean_net_r'])))
    assert len(tests)==28
    for r,p in zip(tests,holm([t['p_one_sided'] for t in tests])):r['holm_p']=p
    pd.json_normalize(stats).to_csv(EXP/'comparison.csv',index=False)
    dump(EXP/'summary.json',dict(config=cfg,comparison=stats,selection=selected,inference=tests))
    dump(EXP/'completion.json',dict(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True,cwd=ROOT).strip(),
        coins=[json.loads((EXP/s['symbol']/'completion.json').read_text()) for s in cfg['symbols']],
        files=[file_stamp(p) for p in [CONFIG,EXP/'summary.json',EXP/'comparison.csv',EXP/'BTC/results.csv.gz',EXP/'ETH/results.csv.gz']],
        training_eligible=False,production_eligible=False))
    compact=[{k:r[k] for k in ['symbol','lane','arm','n','win_rate','net_r','gross_r','mean_net_bp','stop_rate','baseline_winners_stopped','net_r_change','median_fee_r']} for r in stats if r['period']=='all']
    print(json.dumps(compact,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['run','analyze']);p.add_argument('--symbol',choices=['BTC','ETH']);a=p.parse_args()
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__,str(CONFIG)],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder/config before generating artifacts')
    if a.stage=='run':assert a.symbol;run_symbol(a.symbol)
    else:analyze()
