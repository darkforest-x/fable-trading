"""V2.31 versus V2.30 returns with unchanged native exits and cached paths.

Only admissions changed. Join frozen signal identities before inspecting any
outcome. Reuse every identical independent signal/control path; replay two
BTC rematches and their same-time, risk-adjusted controls through existing
Nautilus and exit adapters. Control week/half/ATR14 bucket must still match.
All fees, policy parameters, initial-risk normalization and study bounds stay
fixed. Display pivot extremes are never treated as executable exit prices.
"""
from pathlib import Path
import json
import math
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, run
from yoyo.evaluation.range_v230_exit_compare import run_symbol
from yoyo.evaluation.range_v221_weighting import weighting
from yoyo.evaluation.range_management_study import holm
from yoyo.evaluation.pepe_range_exits import aggregate, inference, pairs
from yoyo.evaluation.monday_range_v2_2_reference import atr

SOURCE=ROOT/'experiments/active/exp-range-v230-pivot-20261021-v1'
EXITS=ROOT/'experiments/active/exp-range-v230-exits-20261021-v1'
ADMISSIONS=ROOT/'experiments/active/exp-range-v231-midpoint-20261021-v1'
DEST=ROOT/'experiments/active/exp-range-v231-returns-20261021-v1'
FIELDS=['entry_i','side','entry','stop','end_i','segment','week','bucket']


def read(path):
    return pd.read_csv(path,float_precision='round_trip')


def select_cached(old,new):
    """Identity match before returns; never select from winners or MFE."""
    lookup={(int(r.signal_i),int(r.side)):i for i,r in old.iterrows()}
    mapped=new.copy()
    mapped['pair_id']=[lookup[(int(r.signal_i),int(r.side))] for r in new.itertuples()]
    assert mapped.pair_id.is_unique
    fields=['touch_i','entry_i','entry','stop','key','level']
    changed=[int(r.pair_id) for r in mapped.itertuples()
             if any(getattr(r,k)!=old.iloc[r.pair_id][k] for k in fields)]
    return mapped,changed


def prepare():
    cfg=json.loads((SOURCE/'config.json').read_text())
    metadata={}; all_candidates={}
    for spec in cfg['symbols']:
        symbol=spec['symbol']
        old=read(SOURCE/symbol/'signal_matches.csv')
        new=read(ADMISSIONS/f'{symbol}_signal_matches.csv')
        mapped,changed=select_cached(old,new)
        assert len(new)==({'BTC':75,'ETH':69}[symbol])
        base=read(SOURCE/symbol/'candidates.csv')
        chosen=base[base.pair_id.isin(mapped.pair_id)].copy()
        native=json.loads((SOURCE/symbol/'native.json').read_text())
        frame=read(ROOT/native['data'])
        frame.index=pd.DatetimeIndex(pd.to_datetime(frame.pop('ts'),unit='ms',utc=True)+pd.Timedelta(minutes=5)).as_unit('ns')
        q=aggregate(frame,15)
        buckets=np.digitize(atr(q,14).to_numpy()/q.close.to_numpy(),cfg['volatility_edges'])
        quarter_week=(q.index-pd.to_timedelta(q.index.dayofweek,unit='D')).normalize()
        half=(q.index>=pd.Timestamp(cfg['midpoint'])).astype(int)
        for row in mapped.itertuples():
            s=chosen.index[(chosen.pair_id==row.pair_id)&(chosen.population=='signal')][0]
            r=chosen.index[(chosen.pair_id==row.pair_id)&(chosen.population=='random')][0]
            if row.pair_id not in changed:
                assert chosen.loc[s,'entry']==row.entry and chosen.loc[s,'stop']==row.stop
                assert pd.Timestamp(chosen.loc[s,'entry_time'])==pd.Timestamp(row.entry_time)
                continue
            qi=int(row.entry_i)
            ci=int(q.index.get_indexer([frame.index[int(chosen.loc[r,'entry_i'])]])[0])
            oi=int(q.index.get_indexer([frame.index[int(chosen.loc[s,'entry_i'])]])[0])
            assert ci>=0 and oi>=0
            assert quarter_week[qi]==quarter_week[ci]==quarter_week[oi]
            assert buckets[qi]==buckets[ci]==chosen.loc[r,'bucket']
            assert half[qi]==half[ci]==chosen.loc[r,'segment']
            assert q.close.iloc[qi]==row.entry
            chosen.loc[s,['entry_i','entry','stop','entry_time']]=[
                int(frame.index.get_indexer([q.index[qi]])[0]),row.entry,row.stop,q.index[qi].isoformat()]
            fraction=row.side*(row.entry-row.stop)/row.entry
            raw=chosen.loc[r,'entry']*(1-row.side*fraction)/spec['tick']
            chosen.loc[r,'stop']=(math.floor(raw+1e-8) if row.side==1 else math.ceil(raw-1e-8))*spec['tick']
        folder=DEST/symbol; folder.mkdir(parents=True,exist_ok=True)
        mapped.to_csv(folder/'signal_matches.csv',index=False)
        chosen.to_csv(folder/'candidates.csv',index=False)
        if changed:
            delta=DEST/'delta'/symbol;delta.mkdir(parents=True,exist_ok=True)
            chosen[chosen.pair_id.isin(changed)].to_csv(delta/'candidates.csv',index=False)
            native.update(experiment_id=DEST.name,entry_candidates=str((delta/'candidates.csv').relative_to(ROOT)),
                          output=str((delta/'native').relative_to(ROOT)))
            dump(delta/'native.json',native)
        metadata[symbol]=dict(kept=mapped.pair_id.tolist(),changed=changed,
            unchanged=len(mapped)-len(changed),removed=len(old)-len(mapped),
            inputs=[file_stamp(SOURCE/symbol/'signal_matches.csv'),file_stamp(ADMISSIONS/f'{symbol}_signal_matches.csv')])
        all_candidates[symbol]=chosen
    dump(DEST/'preparation.json',metadata)
    return cfg,metadata,all_candidates


def combine(prior,delta,kept,changed,base):
    """Reuse only exact execution inputs and assert both populations survive."""
    out=prior[prior.pair_id.isin(kept)&~prior.pair_id.isin(changed)].copy()
    if changed:
        assert set(delta.pair_id)==set(changed)
        out=pd.concat([out,delta],ignore_index=True)
    assert len(out)==len(base)
    assert not out.duplicated(['pair_id','population']).any()
    actual=out.set_index(['pair_id','population']).sort_index()
    expected=base.set_index(['pair_id','population']).sort_index()
    pd.testing.assert_index_equal(actual.index,expected.index)
    np.testing.assert_allclose(actual[FIELDS],expected[FIELDS],rtol=0,atol=1e-8)
    return out


def describe(rows,symbol,arm,version,group,seed):
    s=rows[rows.population=='signal'];r=rows[rows.population=='random']
    ordered=s.sort_values(['exit_i','entry_i']).net_r.cumsum().to_numpy()
    drawdown=float((np.maximum.accumulate(np.r_[0,ordered])[1:]-ordered).max())
    losses=-s.loc[s.net_r<0,'net_r'].sum()
    weighted=weighting(rows,seed)
    return dict(symbol=symbol,arm=arm,version=version,group=group,
        **weighted,win_rate=float((s.net_r>1e-9).mean()),gross_win_rate=float((s.gross_r>1e-9).mean()),
        gross_r=float(s.gross_r.sum()),fee_r=float(s.fee_r.sum()),mean_net_r=float(s.net_r.mean()),
        pf=float(s.loc[s.net_r>0,'net_r'].sum()/losses) if losses else None,
        realized_r_drawdown=drawdown,
        **inference(pairs(s,r),seed))


def main():
    if (DEST/'summary.json').exists():
        print('Completed comparison exists; reuse '+str(DEST/'summary.json'));return
    if subprocess.run(['git','diff','--quiet','HEAD','--',__file__],cwd=ROOT).returncode:
        raise RuntimeError('Commit builder before generating results')
    DEST.mkdir(exist_ok=True)
    cfg,meta,bases=prepare()
    approved=json.loads((EXITS/'config.json').read_text())
    delta_cfg=dict(approved,experiment_id=DEST.name,source=str((DEST/'delta').relative_to(ROOT)))
    for spec in cfg['symbols']:
        symbol=spec['symbol']; changed=meta[symbol]['changed']
        if changed:
            run(DEST/'delta'/symbol/'native.json')
            run_symbol(delta_cfg,dict(spec,expected_signals=len(changed)),DEST/'delta/exits')
    results=[]; attribution=[]; checks=[]
    for spec in cfg['symbols']:
        symbol=spec['symbol']; m=meta[symbol]
        for arm in ['pivot',*[a['name'] for a in approved['arms']]]:
            source=SOURCE/symbol/'native/pivot' if arm=='pivot' else EXITS/symbol/arm
            patch=DEST/'delta'/symbol/'native/pivot' if arm=='pivot' else DEST/'delta/exits'/symbol/arm
            prior=read(source/'trades.csv')
            delta=read(patch/'trades.csv') if m['changed'] else None
            current=combine(prior,delta,m['kept'],m['changed'],bases[symbol])
            folder=DEST/symbol/arm;folder.mkdir(exist_ok=True)
            current.to_csv(folder/'trades.csv',index=False)
            if arm=='pivot':
                lower_prior=read(SOURCE/symbol/'native/strict_ties/trades.csv')
                lower_delta=read(DEST/'delta'/symbol/'native/strict_ties/trades.csv') if m['changed'] else None
            else:
                lower_prior=read(source/'stop_first.csv')
                lower_delta=read(patch/'stop_first.csv') if m['changed'] else None
            lower=combine(lower_prior,lower_delta,m['kept'],m['changed'],bases[symbol])
            lower.to_csv(folder/'sensitivity.csv',index=False)
            for version,rows in [('v230',prior),('v231',current)]:
                for group,part in [('all',rows),('first_half',rows[rows.segment==0]),('second_half',rows[rows.segment==1])]:
                    result=describe(part,symbol,arm,version,group,cfg['seed'])
                    if group=='all':
                        low=lower if version=='v231' else lower_prior
                        result['sensitivity_net_r']=float(low[low.population=='signal'].net_r.sum())
                        result['boundary_n']=int((part.loc[part.population=='signal','reason']==0).sum()) if arm=='pivot' else int((part.loc[part.population=='signal','boundary_weight']>0).sum())
                    results.append(result)
            old_s=prior[prior.population=='signal'];new_s=current[current.population=='signal']
            removed=old_s[~old_s.pair_id.isin(m['kept'])]
            old_changed=old_s[old_s.pair_id.isin(m['changed'])];new_changed=new_s[new_s.pair_id.isin(m['changed'])]
            attribution.append(dict(symbol=symbol,arm=arm,removed_n=len(removed),removed_net_r=float(removed.net_r.sum()),
                rematched_old_net_r=float(old_changed.net_r.sum()),rematched_new_net_r=float(new_changed.net_r.sum())))
            np.testing.assert_allclose(new_s.net_r.sum(),old_s.net_r.sum()-removed.net_r.sum()-old_changed.net_r.sum()+new_changed.net_r.sum(),atol=1e-8,rtol=0)
            checks.append(dict(symbol=symbol,arm=arm,rows=len(current),reused_rows=2*m['unchanged'],replayed_rows=2*len(m['changed'])))
    # Correct the requested policy family; rankings remain descriptive only.
    for symbol in ['BTC','ETH']:
        selected=[r for r in results if r['symbol']==symbol and r['version']=='v231' and r['group']=='all']
        for r,p,b in zip(selected,holm([r['p_one_sided'] for r in selected]),holm([r['bp_excess_p'] for r in selected])):
            r['holm_p_over_arms']=float(p);r['bp_holm_p_over_arms']=float(b)
    dump(DEST/'summary.json',dict(start=cfg['start'],end=cfg['end'],cost=cfg['round_trip_cost'],rows=results,
        attribution=attribution,checks=checks,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        reproduction='python -m yoyo.evaluation.range_v231_returns',
        limitations='Independent equal-risk R and equal-notional bp; no funding, slippage, capacity or account compounding. Historical policy comparison, no new optimization or promotion.'))
    print(json.dumps([r for r in results if r['arm']=='pivot' and r['group']=='all'],indent=2),flush=True)


if __name__=='__main__':
    main()
