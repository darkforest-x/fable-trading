"""ETH 15m-watch/5m-entry partial-close study on frozen Nautilus candidates.

Only exit size changes: first surviving 5m close >= 1/3/5 initial R realizes
25/50 percent; no stop move on that event. Remaining shares keep the existing
15m ATR14x4/5R management and armed H1 reverse exit. This is not an intrabar
limit target. Input bars, entry candidates, initial risk, matched controls and
20bp cost are reused. Original full-position outcomes are read-only; only
bounded no-partial regression probes are replayed after the adapter extension.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
import time
import numpy as np
import pandas as pd
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, prepared, compare_golden
from yoyo.evaluation.range_management_study import summary, subset, holm
from yoyo.evaluation.pepe_range_exits import inference

DEFAULT=ROOT/'experiments/active/exp-eth-5m-partial-close-20261020-v1/config.json'
CODE=['yoyo/evaluation/range_partial_study.py','yoyo/layers/l3_backtest/nautilus_independent.py',
      'tests/evaluation/test_range_partial_close.py']


def drawdown(values):
    cumulative=np.cumsum(np.r_[0.,np.asarray(values,dtype=float)])
    return float(np.max(np.maximum.accumulate(cumulative)-cumulative))


def cash_events(rows,fills):
    """Normalize actual share fills to original risk/notional, including fees.

    Realized cash only, not marked-to-market equity or a funded account.
    Group simultaneous fills before computing drawdown; ordering among IDs is
    arbitrary and must not create artificial peaks within a shared timestamp.
    """
    merged=fills.merge(rows[['trade_id','segment','population','entry','risk','side','quantity']],
        on='trade_id',suffixes=('_fill','_initial'),validate='many_to_one')
    entering=merged.phase.astype(str)=='entry'
    gross=np.where(entering,0.,merged.side*(merged.price-merged.entry)*merged.quantity_fill)
    net=gross-merged.commission
    merged['cash_r']=net/(merged.quantity_initial*merged.risk)
    merged['cash_bp']=10000*net/(merged.quantity_initial*merged.entry)
    return merged[['trade_id','segment','population','ts_ns','cash_r','cash_bp']]


def close_partial_oracle(rows,closes,trigger,fraction,tick):
    """Audit ONLY: scan closes before cached final exit, never used for admission.

    Runner exit path cannot change when only size changes. Earlier surviving
    closes identify the first executable partial; old stops and final close
    exits have precedence on the final bar. No MFE-to-fill conversion is used.
    """
    output=[]
    for r in rows.itertuples():
        start=int(r.entry_i)+1
        prices=closes[start:int(r.exit_i)]
        hit=np.flatnonzero(r.side*(prices-r.entry)+tick*1e-7 >= trigger*r.risk)
        idx=start+int(hit[0]) if len(hit) else -1
        price=closes[idx] if idx>=0 else np.nan
        q=fraction if idx>=0 else 0.
        gross=(q*r.side*(price-r.entry)/r.risk+(1-q)*r.gross_r) if q else r.gross_r
        output.append((idx,price,q,gross))
    return pd.DataFrame(output,columns=['partial_i','partial_price','partial_weight','gross_r'],index=rows.index)


def run(path):
    started=time.perf_counter()
    cfg=json.loads(path.read_text()); dest=path.parent
    if subprocess.run(['git','diff','--quiet','HEAD','--',*CODE],cwd=ROOT).returncode:
        raise RuntimeError('Commit changed builder before producing results')
    prior=json.loads((ROOT/cfg['baseline_receipt']).read_text())
    native=json.loads((ROOT/cfg['baseline_config']).read_text())
    assert native==prior['identity']['config']
    assert [file_stamp(ROOT/native[k]) for k in ('data','entry_candidates')]==prior['identity']['inputs']
    for spec in cfg['arms']:
        candidate=json.loads((dest/spec['config']).read_text())
        for key in ('data','entry_candidates','symbol','tick','market_tick','stop_window_5m',
                    'buffer_percent','maximum_risk_percent','round_trip_cost'):
            assert candidate[key]==native[key],key
        p=dict(candidate['policies'][0]); p.pop('partial_close_r');p.pop('partial_fraction');p.pop('name')
        assert p=={k:v for k,v in native['policies'][0].items() if k!='name'}
    # Reuse existing prepared bars; metadata remains tied to the original files.
    if not (dest/'.cache').exists():
        shutil.copytree((ROOT/cfg['baseline_config']).parent/'.cache',dest/'.cache')
    ctx,entries,hit=prepared(native,dest/'.cache')
    old=pd.read_csv(ROOT/cfg['baseline_ledger'],float_precision='round_trip')
    probes=np.unique(np.r_[np.linspace(0,len(old)-1,48,dtype=int),np.argmax(old.peak_r),
                           np.argmax(old.net_r),np.argmin(old.net_r)])
    auditpath=dest/'baseline_regression.json'
    if not auditpath.exists():
        from yoyo.layers.l3_backtest.nautilus_independent import run_native,ExitPolicy
        chosen=old.iloc[probes]
        selected=entries[entries.trade_id.isin(chosen.trade_id)]
        rows,_,audit=run_native(ctx['frame'],ctx['atr'],ctx['hour'],selected,ExitPolicy(),
            native['tick'],native['symbol'],native['market_tick'])
        chosen.to_csv(dest/'baseline_probe.csv',index=False)
        parity=compare_golden(rows,dest/'baseline_probe.csv',native['tick'])
        dump(auditpath,dict(audit=audit,parity=parity,full_baseline_replayed=False))
    dump(dest/'preflight.json',dict(feature_cache_hit=hit,inputs=prior['identity']['inputs'],
        baseline_ledger=file_stamp(ROOT/cfg['baseline_ledger']),entries=len(entries),
        baseline_code=prior['source_commit'],new_code=subprocess.check_output(
            ['git','rev-parse','HEAD'],text=True).strip(),no_download=True,no_content_hash=True))
    def one(spec):
        with (dest/(spec['name']+'.log')).open('w') as log:
            subprocess.run([str(ROOT/'scripts/backtest-range'),'--config',str(dest/spec['config'])],
                cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
        receipt=json.loads((dest/'runs'/spec['name']/'receipt.json').read_text())
        result=dict(name=spec['name'],elapsed_seconds=receipt['elapsed_seconds'])
        print(json.dumps(result),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs=list(pool.map(one,cfg['arms']))
    dump(dest/'run_receipt.json',dict(runs=runs,wall_seconds=time.perf_counter()-started))


def analyze(path):
    cfg=json.loads(path.read_text());dest=path.parent
    native=json.loads((ROOT/cfg['baseline_config']).read_text())
    ctx,_,_=prepared(native,dest/'.cache')
    old=pd.read_csv(ROOT/cfg['baseline_ledger'],float_precision='round_trip').set_index('trade_id',drop=False)
    oldfills=pd.read_csv(ROOT/cfg['baseline_fills'],float_precision='round_trip')
    frames={'baseline':old}
    cash={'baseline':cash_events(old.reset_index(drop=True),oldfills)}
    audits=[]
    for spec in cfg['arms']:
        work=dest/'runs'/spec['name']/spec['name']
        new=pd.read_csv(work/'trades.csv',float_precision='round_trip').set_index('trade_id',drop=False)
        assert set(new.index)==set(old.index)
        new=new.loc[old.index]
        for col in ('entry_i','side','end_i','exit_i','reason','arm_i','population','segment','week','bucket'):
            np.testing.assert_array_equal(new[col],old[col],err_msg=col)
        for col in ('entry','stop','risk','exit_price','protection','peak_r','fee_r'):
            np.testing.assert_allclose(new[col],old[col],rtol=0,atol=1e-7,err_msg=col)
        expected=close_partial_oracle(old,ctx['bars'][:,3],spec['trigger_r'],spec['fraction'],native['tick'])
        for col in expected:
            np.testing.assert_allclose(new[col],expected[col],rtol=0,atol=1e-7,equal_nan=True,err_msg=col)
        np.testing.assert_allclose(new.net_r,new.gross_r-new.fee_r,rtol=0,atol=1e-7)
        np.testing.assert_allclose(new.framework_commission,new.quantity*new.entry*native['round_trip_cost'],rtol=0,atol=1e-7)
        fills=pd.read_csv(work/'fills.csv',float_precision='round_trip')
        events=cash_events(new.reset_index(drop=True),fills)
        np.testing.assert_allclose(events.groupby('trade_id').cash_r.sum().loc[new.index],new.net_r,rtol=0,atol=1e-7)
        np.testing.assert_allclose(events.groupby('trade_id').cash_bp.sum().loc[new.index],new.net_notional_bp,rtol=0,atol=1e-7)
        for r in new[new.partial_i>=0].itertuples():
            assert r.entry_i<r.partial_i<r.exit_i
            assert r.partial_weight==spec['fraction']
        frames[spec['name']],cash[spec['name']]=new,events
        audits.append(dict(arm=spec['name'],oracle_paths=len(new),partial_trades=int((new.partial_i>=0).sum()),
                           fills=len(fills),unchanged_runner_exit_rows=len(new),weighted_fee_and_cash_rows=len(new)))
    tables=[]; comparisons=[]
    for name,rows in frames.items():
        for seg in (-1,0,1,2,-2):
            part=subset(rows,seg)
            for pop in ('signal','random'):
                r=part[part.population==pop]; e=subset(cash[name],seg);e=e[e.population==pop]
                grouped=e.groupby('ts_ns')[['cash_r','cash_bp']].sum()
                metric=summary(r)
                metric.update(mean_net_bp=float(r.net_notional_bp.mean()),
                    realized_r_drawdown=drawdown(grouped.cash_r),realized_bp_drawdown=drawdown(grouped.cash_bp),
                    partial_n=int((r.partial_i>=0).sum()) if name!='baseline' else 0)
                tables.append(dict(arm=name,segment=seg,population=pop,**metric))
            s=part[part.population=='signal'];control=part[part.population=='random']
            paired=s[['pair_id','week','net_r']].merge(control[['pair_id','net_r']],on='pair_id',suffixes=('','_random'),validate='one_to_one')
            item=dict(arm=name,segment=seg,n=len(s),paired_n=len(paired),signal_vs_random=inference(paired,cfg['seed']))
            sbp=s[['pair_id','week','net_notional_bp']].rename(columns={'net_notional_bp':'net_r'})
            cbp=control[['pair_id','net_notional_bp']].rename(columns={'net_notional_bp':'net_r_random'})
            item['signal_vs_random_bp']=inference(sbp.merge(cbp,on='pair_id'),cfg['seed'])
            if name!='baseline':
                base=old.loc[s.index];delta=s.net_r-base.net_r
                improvement=s[['week','net_r']].assign(net_r_random=base.net_r)
                effect=paired.copy()
                effect['net_r']=s.net_r.to_numpy()-base.net_r.to_numpy()
                # Align random effects explicitly by the frozen pair ID.
                cd=(control.set_index('pair_id').net_r-old.loc[control.index].set_index('pair_id').net_r)
                effect['net_r_random']=effect.pair_id.map(cd)
                item.update(delta_net_r=float(delta.sum()),delta_mean_net_bp=float((s.net_notional_bp-base.net_notional_bp).mean()),
                    improved=int((delta>1e-7).sum()),worsened=int((delta < -1e-7).sum()),
                    losers_to_winners=int(((base.net_r<=1e-9)&(s.net_r>1e-9)).sum()),
                    winners_to_losses=int(((base.net_r>1e-9)&(s.net_r<=1e-9)).sum()),
                    policy_gain=inference(improvement,cfg['seed']),matched_increment=inference(effect,cfg['seed']))
                for level in (10,20):
                    mask=base.gross_r>=level-1e-7
                    item[f'old_{level}r_n']=int(mask.sum())
                    item[f'still_{level}r_n']=int((mask&(s.gross_r>=level-1e-7)).sum())
                    item[f'old_{level}r_tail_gross']=float(base.loc[mask,'gross_r'].sum())
                    item[f'new_{level}r_tail_gross']=float(s.loc[mask,'gross_r'].sum())
            comparisons.append(item)
    for seg in (-1,0,1,2,-2):
        group=[r for r in comparisons if r['segment']==seg and r['arm']!='baseline']
        for metric in ('policy_gain','matched_increment'):
            for record,p in zip(group,holm([r[metric]['p_one_sided'] for r in group])):
                record[metric]['holm_p_six']=p
    table=pd.DataFrame(tables);table.to_csv(dest/'summary.csv',index=False)
    development=table[(table.segment==0)&(table.population=='signal')]
    chosen=development.sort_values(['mean_net_bp','arm'],ascending=[False,True]).iloc[0].arm
    full=table[(table.segment==-1)&(table.population=='signal')].sort_values(['mean_net_bp','arm'],ascending=[False,True])
    dump(dest/'selection.json',dict(development_selected=chosen,primary='2024 mean net notional bp; baseline included',
        descriptive_full_best=full.iloc[0].arm,later=table[(table.segment==-2)&(table.population=='signal')].to_dict('records'),
        historical_not_unseen=True))
    dump(dest/'comparisons.json',comparisons);dump(dest/'audit.json',audits)
    print(full[['arm','n','partial_n','net_r','mean_net_bp','win_rate','gross_10r','gross_20r','realized_r_drawdown']].to_string(index=False))
    print('2024 selection:',chosen)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=DEFAULT)
    parser.add_argument('--phase',choices=['run','analyze'],required=True)
    args=parser.parse_args();globals()[args.phase](args.config.resolve())

if __name__=='__main__':main()
