"""Cached risk-sizing, concentration and overlapping-exposure diagnostics.

No price matching or account replay. Concurrency uses original entry opens
and conservative recorded exit-bar closes; unfinished positions remain open
through their last observed boundary. These are independent planned exposures,
not an affordable leverage, margin requirement or funded equity trajectory.
"""
import json
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_confirmed_keypoints_study import EXP,SRC,EXCLUDED,dump


def overlap(g):
    g=g[g.entered]
    if not len(g):return {}
    entries=pd.DataFrame(dict(ts=g.entry_ns,value=g.notional,count=1))
    exits=pd.DataFrame(dict(ts=g.exit_ns,value=-g.notional,count=-1))
    cumulative=pd.concat([entries,exits]).groupby('ts')[['value','count']].sum().sort_index().cumsum()
    return dict(max_concurrent_n=int(cumulative['count'].max()),max_concurrent_notional_per100risk=float(cumulative.value.max()),
        max_total_initial_risk_budget=float(100*cumulative['count'].max()))


def main():
    summary=json.loads((EXP/'summary.json').read_text());allrows=[]
    cols=['event_id','symbol','week','population','side','score','entry','entry_ns','exit_ns','entered','closed','resolved','reason',
        'target_name','target_price','target_r','be_mode','risk_pct','planned_loss_pct','notional','quantity','budget_r','net_bp','net_r',
        'skip_reason','planned_loss_unit','stop','exit_quote','exit_price','boundary_mark_budget_r']
    for p in sorted((EXP/'symbols').glob('*/trades.parquet')):
        if json.loads((p.parent/'done.json').read_text())['opportunity_columns']:
            f=pd.read_parquet(p,columns=cols)
            allrows.append(f[f.population.eq('signal')&f.side.eq(1)&~f.symbol.isin(EXCLUDED)&f.score.ge(.22621664169752762)])
    t=pd.concat(allrows,ignore_index=True);rows=[]
    for (name,be),g in t.groupby(['target_name','be_mode']):
        c=g[g.closed];e=g[g.entered];b=c[c.reason.eq(5)]
        pre_slip=(b.side*(b.exit_quote-b.entry)-.0005*(b.entry+b.exit_quote))/b.entry*10000
        row=dict(target_name=name,be_mode=be,n=len(g),entered_n=len(e),closed_n=len(c),
            skipped_n=int((~g.entered).sum()),censored_n=int((g.entered&~g.closed).sum()),
            skip_reasons=g.loc[~g.entered,'skip_reason'].value_counts().to_dict(),
            planned_loss_pct=e.planned_loss_pct.describe(percentiles=[.01,.1,.5,.9,.99]).to_dict(),
            notional_per100risk=e.notional.describe(percentiles=[.01,.1,.5,.9,.99]).to_dict(),
            target_r=e.target_r.describe(percentiles=[.01,.1,.5,.9,.99]).to_dict(),
            sl_gap_over_budget_n=int((c.reason.eq(1)&c.budget_r.lt(-1-1e-7)).sum()),
            max_pnl_per100=float(100*c.budget_r.max()),min_pnl_per100=float(100*c.budget_r.min()),
            be_n=len(b),be_fee_neutral_before_slip_n=int(pre_slip.abs().le(1e-7).sum()),be_gap_loss_n=int(pre_slip.lt(-1e-7).sum()),
            be_mean_pnl_per100=float(100*b.budget_r.mean()),censored_mean_budget_mark=float(g.boundary_mark_budget_r.mean()))
        row.update(overlap(e))
        if len(c):
            q=c.budget_r.quantile(.99);total=c.budget_r.sum()
            row.update(top1pct_share_net=float(c.loc[c.budget_r.ge(q),'budget_r'].sum()/total) if abs(total)>1e-12 else None,
                without_top1pct_mean_budget_r=float(c.loc[c.budget_r.lt(q),'budget_r'].mean()))
        rows.append(row)
    chosen=next(c for c in summary['choices'] if c['scope']=='keypoints' and c['direction']=='long' and c['filter']=='score_gate')
    pick=chosen['selected2025'];selected=t[t.target_name.eq(pick['target_name'])&t.be_mode.eq(pick['be_mode'])]
    c=selected[selected.closed].copy();c['year']=pd.to_datetime(c.entry_ns,utc=True).dt.year
    annual=c.groupby('year').agg(n=('budget_r','size'),mean_budget_r=('budget_r','mean'),mean_bp=('net_bp','mean')).to_dict('index')
    c.nlargest(20,'budget_r').to_csv(EXP/'selected_top20.csv',index=False)
    dump(EXP/'diagnostics.json',dict(scored_long_targets=rows,selected2025=pick,annual_by_entry_year=annual,
        same_risk_note='Same entry and pin stop means all targets use exactly the same quantity whenever entered.100USDT planned risk is a scalable illustration; ordinary fees/slippage included, gaps not bounded.',
        concurrency_note='Independent overlapping positions, not a funded account. Bar-close timestamps conservatively bound observed exit times.'))
    done=[json.loads(p.read_text()) for p in (EXP/'symbols').glob('*/done.json')]
    dump(EXP/'validation.json',dict(symbols=len(done),events=sum(x['events'] for x in done),opportunity_columns=sum(x['opportunity_columns'] for x in done),
        executed_columns=sum(x['executed_columns'] for x in done),fills=sum(x['fills'] for x in done),behavioral_tests=8,
        checks='All new closed native positions reconciled fee types, PnL, geometricR, budgetR. Skips have no orders; fixed-R baseline never replayed.',
        builder_commit='ddf687dd06',training_eligible=False,production_eligible=False))
    print(json.dumps(dict(selected=pick['target_name'],be=pick['be_mode'],annual=annual),indent=2))


if __name__=='__main__':main()
