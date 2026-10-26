"""Conditional entered-and-closed matched pairs for compact owner readout.

Supplementary to the predeclared per-opportunity comparison (which includes
zero-PnL skips). Both signal and random here actually entered and closed;
unmatched/unfinished outcomes are not replaced. No new price execution.
"""
import json
import pandas as pd
from yoyo.evaluation.monday_confirmed_keypoints_study import EXP,EXCLUDED


def main():
    cols=['event_id','population','side','symbol','score','entry_ns','exit_ns','closed','target_name','be_mode','budget_r']
    frames=[]
    for p in sorted((EXP/'symbols').glob('*/trades.parquet')):
        if json.loads((p.parent/'done.json').read_text())['opportunity_columns']:
            f=pd.read_parquet(p,columns=cols)
            frames.append(f[f.side.eq(1)&~f.symbol.isin(EXCLUDED)&f.closed])
    t=pd.concat(frames,ignore_index=True);rows=[]
    for period in ['all','check2026']:
        z=t if period=='all' else t[t.entry_ns.ge(pd.Timestamp('2026-01-01',tz='UTC').value)]
        for (name,be),g in z.groupby(['target_name','be_mode']):
            sig=g[g.population.eq('signal')&g.score.ge(.22621664169752762)]
            cc=g[g.population.eq('random')].groupby('event_id').budget_r.agg(['mean','count'])
            pair=sig.merge(cc,left_on='event_id',right_index=True)
            rows.append(dict(period=period,target_name=name,be_mode=be,matched_n=len(pair),
                matched_signal_usdt_per100risk=100*pair.budget_r.mean(),matched_random_usdt_per100risk=100*pair['mean'].mean(),
                excess_usdt_per100risk=100*(pair.budget_r-pair['mean']).mean(),full3_n=int(pair['count'].eq(3).sum())))
    out=pd.DataFrame(rows);out.to_csv(EXP/'entered_closed_pairs.csv',index=False)
    print(out[(out.period=='all')&(out.be_mode=='fixed_stop')].to_string(index=False))


if __name__=='__main__':main()
