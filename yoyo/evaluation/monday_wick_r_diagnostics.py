"""Read-only explanation of frozen wick-R framework results, no new fills.

Separate fee-neutral stops from gaps, activation-time market targets from
resting targets, and ex-post R outliers from equal-notional performance.
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_wick_r_study import EXP,dump


def run():
    summary=json.loads((EXP/'summary.json').read_text())
    metrics=pd.read_csv(EXP/'metrics.csv')
    a=metrics[metrics.cohort.eq('altcoins')]
    finite=a[a.period.eq('all')&a.stop_pct.notna()].sort_values('mean_bp',ascending=False).iloc[0]
    stop=float(finite.stop_pct)
    cols=['event_id','symbol','week','side','population','control_id','entry_ns','exit_ns','closed','stop_pct','target_r',
          'entry_i','touch_end_i','exit_i','entry','exit_price','wick_r','wick_pct','reason','fee_be_armed','confirmed',
          'net_bp','gross_bp','net_after_slip_bp','net_after_slip_r','ambiguous','baseline_net_bp',
          'baseline_after_slip_bp','baseline_closed','baseline_exit_ns','asset_group']
    chunks=[pd.read_parquet(p,columns=cols,filters=[('stop_pct','==',stop)]) for p in sorted((EXP/'symbols').glob('*/trades.parquet'))]
    t=pd.concat(chunks,ignore_index=True);t=t[t.asset_group.eq('ALTCOINS')]
    sig=t[t.population.eq('signal')];details=[]
    for target,g in sig.groupby('target_r'):
        c=g[g.closed];be=c[c.reason.eq(5)];initial=c[c.reason.eq(12)]
        details.append(dict(target_r=int(target),n=len(g),closed=len(c),censored=len(g)-len(c),
            target_n=int(c.reason.isin([4,14]).sum()),target_pct=100*c.reason.isin([4,14]).sum()/len(g),
            resting_target_n=int(c.reason.eq(4).sum()),market_target_at_touch_close_n=int(c.reason.eq(14).sum()),
            target_but_net_loss_n=int((c.reason.isin([4,14])&c.net_bp.le(0)).sum()),
            initial_stop_n=len(initial),initial_stop_in_touch_n=int((initial.exit_i<=initial.touch_end_i).sum()),
            initial_stop_in_confirmation_n=int((initial.exit_i>initial.touch_end_i).sum()),
            failed_confirm_n=int(c.reason.eq(11).sum()),fee_be_armed_n=int(g.fee_be_armed.sum()),
            confirmed_n=int(g.confirmed.sum()),fee_be_stop_n=len(be),
            exact_fee_neutral_n=int(be.net_bp.abs().lt(1e-7).sum()),fee_be_gap_loss_n=int(be.net_bp.lt(-1e-7).sum()),
            entry_stop_fee_loss_n=int(c.reason.eq(6).sum()),mean_fee_only_bp=float(c.net_bp.mean()),
            mean_after_slip_bp=float(c.net_after_slip_bp.mean()),mean_after_slip_r=float(c.net_after_slip_r.mean()),
            median_after_slip_r=float(c.net_after_slip_r.median()),ambiguous_n=int(c.ambiguous.sum())))
    pd.DataFrame(details).to_csv(EXP/'target-outcomes.csv',index=False)
    chosen=sig[sig.target_r.eq(finite.target_r)&sig.closed].copy()
    chosen['net_r']=chosen.net_bp/10000*chosen.entry/chosen.wick_r
    chosen['gross_r']=chosen.gross_bp/10000*chosen.entry/chosen.wick_r
    top=chosen.sort_values('net_after_slip_r',ascending=False)
    top.head(20).to_csv(EXP/'largest-r-diagnostics.csv',index=False)
    total=float(chosen.net_after_slip_r.sum());top_count=max(1,int(np.ceil(len(chosen)*.01)))
    pair=chosen[chosen.baseline_closed]
    diagnostic=dict(finite_full_best=finite.to_dict(),target_table_stop_pct=stop,
        r_distribution=dict(mean=float(chosen.net_after_slip_r.mean()),median=float(chosen.net_after_slip_r.median()),
            quantiles=chosen.net_after_slip_r.quantile([.01,.1,.25,.5,.75,.9,.99,1]).to_dict(),
            largest=float(top.net_after_slip_r.iloc[0]),largest_event=top.event_id.iloc[0],
            top1percent_contribution_to_net_r=float(top.head(top_count).net_after_slip_r.sum()/total),
            mean_without_top1percent=float(top.iloc[top_count:].net_after_slip_r.mean()),
            market_at_touch_close_r_contribution=float(chosen.loc[chosen.reason.eq(14),'net_after_slip_r'].sum()/total)),
        same_closed_baseline=dict(n=len(pair),new_mean_bp=float(pair.net_after_slip_bp.mean()),
            old_mean_bp=float(pair.baseline_after_slip_bp.mean()),paired_delta_bp=float((pair.net_after_slip_bp-pair.baseline_after_slip_bp).mean()),
            unambiguous_new_mean_bp=float(pair.loc[~pair.ambiguous,'net_after_slip_bp'].mean()),
            unambiguous_old_mean_bp=float(pair.loc[~pair.ambiguous,'baseline_after_slip_bp'].mean())))
    # Already-selected 2025 parameters, matched week sign randomization.
    sel=summary['chosen']['altcoins']['selected_on_2025']
    selected=pd.read_parquet(EXP/'selected_2026_signals.parquet')
    s=selected[selected.cohort.eq('altcoins')&selected.closed]
    controls=t[t.population.eq('random')&t.target_r.eq(sel['target_r'])&t.closed]
    controls=controls[controls.entry_ns>=pd.Timestamp('2026-01-01',tz='UTC').value]
    cc=controls.groupby('event_id').net_after_slip_bp.agg(['mean','count'])
    p=s.merge(cc[cc['count']==3],left_on='event_id',right_index=True)
    p['delta']=p.net_after_slip_bp-p['mean'];w=p.groupby('week').delta.sum().to_numpy()
    if len(w) and (sel['stop_pct']==stop):
        rng=np.random.default_rng(20261026);signs=rng.choice([-1,1],size=(10000,len(w)))
        diagnostic['selected_2026_matched_week_sign_p']=(1+int((np.abs(signs@w)>=abs(w.sum())).sum()))/10001
    # Denominator is all first-touch opportunities; body-crossing touches and
    # failed reclaim attempts stay in the population instead of hindsight filtering.
    side=[]
    for direction,g in chosen.groupby('side'):
        side.append(dict(side=int(direction),n=len(g),mean_bp=float(g.net_after_slip_bp.mean()),mean_fee_only_bp=float(g.net_bp.mean())))
    diagnostic['full_best_finite_by_side']=side
    dump(EXP/'diagnostics.json',diagnostic)
    print(json.dumps(diagnostic,ensure_ascii=False))


if __name__=='__main__':run()
