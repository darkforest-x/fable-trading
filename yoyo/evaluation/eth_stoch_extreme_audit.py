"""Audit causal windows/stop geometry and every positive V4 grid candidate.

This independent diagnostic rereads registered OHLC, directly slices source
windows, and scalar-replays the unique signal/control paths behind all positive
full-history arms. It does not retune or redraw controls after observing returns.
"""
import argparse
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.eth_stoch_confirm_exits import EXP as V1,load_minutes,prepare,trace,Policy
from yoyo.evaluation.eth_stoch_extreme_stop import EXP,REASONS,stops
from yoyo.evaluation.eth_stoch_extreme_analysis import entry_configs,indices,load_arrays,metrics,paired_metrics


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--paths',default='paths_v1');ap.add_argument('--analysis',default='analysis_v1');ap.add_argument('--out',default='audit_v1')
    args=ap.parse_args();out=EXP/args.out;out.mkdir(exist_ok=False)
    paths=EXP/args.paths;analysis=EXP/args.analysis
    cfg=json.loads((EXP/'config.json').read_text());source=json.loads((V1/'config.json').read_text())
    minute,_=load_minutes(source);ctx=prepare(minute);ctx['arrow']=np.zeros_like(ctx['arrow'])
    events=pd.read_csv(paths/'events.csv.gz');anchors=events.anchor_i.to_numpy(int)
    k=ctx['signals'].k.to_numpy();h=ctx['frame'].high.to_numpy();l=ctx['frame'].low.to_numpy()
    for lb in cfg['lookbacks']:
        window=np.stack([k[anchors-j] for j in range(lb+1)],axis=1)
        assert np.allclose(np.nanmin(window,axis=1),events[f'kmin_{lb}'],atol=1e-10,rtol=0)
        assert np.allclose(np.nanmax(window,axis=1),events[f'kmax_{lb}'],atol=1e-10,rtol=0)
    lo=np.stack([l[anchors+j] for j in (-2,-1,0,1)]).min(axis=0)
    hi=np.stack([h[anchors+j] for j in (-2,-1,0,1)]).max(axis=0)
    for buffer in cfg['stop_buffers']:
        expected=np.where(events.side.to_numpy()==1,np.floor((lo-buffer)*100+1e-8),np.ceil((hi+buffer)*100-1e-8))/100
        actual,_=stops(events,buffer)
        assert np.allclose(actual,expected,rtol=0,atol=1e-10)
    grid=pd.read_csv(analysis/'grid_summary.csv.gz')
    positive=grid[(grid.segment=='all')&(grid.net_points>1e-8)]
    policies=[Policy(**p) for p in json.loads((paths/'policies.json').read_text())]
    pindex={p.name:j for j,p in enumerate(policies)}
    configs={c['config_id']:c for c in entry_configs(cfg)}
    selected=set()
    for row in positive.itertuples():
        conf=configs[row.config_id]
        sig,left,right,_=indices(events,conf,cfg['control_seed'])
        for i in np.unique(np.r_[sig,right]): selected.add((conf['buffer'],int(i),pindex[row.policy]))
    checks=0
    for buffer in sorted({x[0] for x in selected}):
        a=load_arrays(paths,buffer,events,.002)
        for _,i,j in sorted(x for x in selected if x[0]==buffer):
            r=events.iloc[i]
            z=trace(ctx,int(r.entry_i),int(r.side),float(r.entry),float(a['stop'][i]),policies[j],end_i=int(r.end_i))
            assert z['exit_i']==int(a['raw']['exit_i'][i,j])
            assert z['reason']==REASONS[int(a['raw']['reason'][i,j])]
            assert abs(z['exit_price']-float(a['raw']['exit_price'][i,j]))<1e-8
            assert abs(z['net_points']-float(a['net'][i,j]))<1e-8
            checks+=1
        print('positive scalar buffer',buffer,'checked',checks,flush=True)
    # Preserve the sparse positive candidates with the most trades and the
    # highest fixed-size profit; these are descriptions, not new selections.
    examples=pd.concat([positive.sort_values('net_points',ascending=False).head(5),
                        positive.sort_values('closed',ascending=False).head(5)]).drop_duplicates(['config_id','policy'])
    examples.to_csv(out/'sparse_positive_candidates.csv',index=False)
    parts=grid.merge(examples[['config_id','policy']],on=['config_id','policy'],validate='many_to_one')
    parts.to_csv(out/'sparse_positive_periods.csv',index=False)
    baseline=grid[(grid.config_id=='e1111')&(grid.segment=='all')].set_index('policy')
    owner=grid[(grid.config_id=='e1180')&(grid.segment=='all')].set_index('policy')
    facts=dict(builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        direct_k_windows=len(events)*len(cfg['lookbacks']),direct_stop_geometries=len(events)*len(cfg['stop_buffers']),
        positive_full_history_arms=len(positive),positive_candidate_unique_paths=checks,
        max_closed_trades_in_any_positive_arm=int(positive.closed.max()),
        primary_original_target_hits=int(baseline.loc['tp20_price_5r'].target_hits),
        primary_owner_target_hits=int(owner.loc['tp20_price_5r'].target_hits),
        primary_large_winner_retention=float(owner.loc['tp20_price_5r'].target_hits/baseline.loc['tp20_price_5r'].target_hits),
        primary_signal_retention=float(owner.loc['tp20_price_5r'].orders/baseline.loc['tp20_price_5r'].orders),
        not_economic_acceptance=True,training_eligible=False,production_eligible=False)
    (out/'receipt.json').write_text(json.dumps(facts,indent=2)+'\n')
    print(json.dumps(facts),flush=True)


if __name__=='__main__':main()
