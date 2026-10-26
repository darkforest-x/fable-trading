"""Audit cached confirmed-entry outcomes; no second execution or parameter fit.

Week-cluster bootstrap assesses the preselected2025 candidate on2026 and its
matched random control subset. Fixed-risk normalized means are supplementary
and explicitly distinct from equal-notional return and funded equity.
"""
import json
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_confirmed_r_study import EXP,EXCLUDED,metrics,dump


def main():
    summary=json.loads((EXP/'summary.json').read_text());m=pd.read_csv(EXP/'metrics.csv')
    cols=['event_id','symbol','week','side','population','entry_ns','exit_ns','closed','target_r','be_mode','score',
        'reason','be_armed','ambiguous','net_bp','net_r','gross_r','entry','entry_quote','stop','risk','risk_pct',
        'exit_quote','exit_price','fee_bp','entry_slip_bp','exit_slip_bp','net_ex_slip_cost_bp','boundary_mark_bp']
    t=pd.concat([pd.read_parquet(p,columns=cols) for p in sorted((EXP/'symbols').glob('*/trades.parquet'))
        if json.loads((p.parent/'done.json').read_text())['columns']],ignore_index=True)
    t=t[~t.symbol.isin(EXCLUDED)]
    gate=.22621664169752762;cut=pd.Timestamp('2026-01-01',tz='UTC').value
    checks=[]
    for choice in summary['choices']:
        d=choice['direction'];filt=choice['filter'];sel=choice['selected2025']
        if filt=='scorable':continue
        mask=t.side.eq(1) if d=='long' else t.side.eq(-1) if d=='short' else np.ones(len(t),bool)
        z=t[mask&t.target_r.eq(sel['target_r'])&t.be_mode.eq(sel['be_mode'])&(t.entry_ns>=cut)&t.closed]
        sg=z[z.population.eq('signal')]
        if filt=='score_gate':sg=sg[sg.score.ge(gate)]
        cc=z[z.population.eq('random')].groupby('event_id').net_bp.agg(['mean','count'])
        pair=sg.merge(cc,left_on='event_id',right_index=True)
        pair['delta']=pair.net_bp-pair['mean']
        result=dict(direction=d,filter=filt,target_r=sel['target_r'],be_mode=sel['be_mode'],n=len(sg),matched_n=len(pair))
        for label,frame,col in [('absolute',sg,'net_bp'),('matched_excess',pair,'delta')]:
            weeks=frame.groupby('week')[col].agg(['sum','count']);n=len(weeks)
            if n:
                rng=np.random.default_rng(20261026);idx=rng.integers(n,size=(3000,n))
                boot=weeks['sum'].to_numpy()[idx].sum(1)/weeks['count'].to_numpy()[idx].sum(1)
                result[label]=dict(weeks=n,mean_bp=float(frame[col].mean()),ci95_bp=np.quantile(boot,[.025,.975]).tolist(),
                    bootstrap_sign_probability_two_sided=float(2*min(((boot<=0).sum()+1)/3001,((boot>=0).sum()+1)/3001)))
        checks.append(result)
    risk_choices=[]
    for (d,filt),g in m[m['filter'].isin(['all','score_gate'])].groupby(['direction','filter']):
        sel=g[g.period.eq('select2025')].sort_values('mean_r',ascending=False).iloc[0]
        risk_choices.append(dict(direction=d,filter=filt,selected_by_2025_mean_r=sel.to_dict(),
            evaluation=g[g.target_r.eq(sel.target_r)&g.be_mode.eq(sel.be_mode)].to_dict('records')))
    out=[]
    for d,side in [('long',1),('short',-1)]:
        z=t[t.population.eq('signal')&t.side.eq(side)&t.score.ge(gate)]
        for (tp,be),g in z.groupby(['target_r','be_mode']):
            c=g[g.closed];b=c[c.reason.eq(5)];sl=c[c.reason.eq(1)]
            fee_pre_slip=b.net_bp+b.exit_slip_bp
            wins=c[c.net_bp>0].groupby('symbol').net_bp.sum().sort_values(ascending=False)
            out.append(dict(direction=d,target_r=int(tp),be_mode=be,**metrics(g),
                mean_risk_pct=float(g.risk_pct.mean()),median_risk_pct=float(g.risk_pct.median()),
                median_hold_minutes=float(((c.exit_ns-c.entry_ns)/60_000_000_000).median()),
                p99_hold_minutes=float(((c.exit_ns-c.entry_ns)/60_000_000_000).quantile(.99)),
                max_net_r=float(c.net_r.max()),min_net_r=float(c.net_r.min()),
                mean_initial_stop_r=float(sl.net_r.mean()),be_fee_neutral_n=int(fee_pre_slip.abs().le(1e-7).sum()),
                be_gap_loss_n=int(fee_pre_slip.lt(-1e-7).sum()),be_mean_net_bp=float(b.net_bp.mean()),
                censored_mark_mean_bp=float(g.boundary_mark_bp.mean()),
                top5_symbols_share_positive=float(wins.head(5).sum()/wins.sum()) if len(wins) else None))
    pd.DataFrame(out).to_csv(EXP/'target_outcomes.csv',index=False)
    dump(EXP/'diagnostics.json',dict(bootstrap_2026=checks,supplementary_risk_choices=risk_choices,
        note='MeanR selection is supplementary, not the predeclared equal-notional primary objective. Bootstrap is exploratory with overlapping trades and previously exposed history; not a deployment test.'))
    print(json.dumps(checks,indent=2))


if __name__=='__main__':main()
