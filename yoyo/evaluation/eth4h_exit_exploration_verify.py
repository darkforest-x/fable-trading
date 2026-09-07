"""Independent saved-ledger and causal gate audit of the frozen R2 study.

Reads no new outcome window and does not rerun strategy selection. Reconciles
fills, costs, risk, admissibility and matching, plus fixed-identity BE deltas.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.eth4h_exit_exploration import OUT, ROOT, report
from yoyo.evaluation.pine_allin_eth4h_replay import digest, inference, load_source, save_json


def read(name,dates=()):
    return pd.read_csv(OUT/name,float_precision='round_trip',parse_dates=list(dates))


def main():
    target=OUT/'independent_validation.json'
    if target.exists():raise RuntimeError('Do not overwrite audit evidence')
    frozen=subprocess.check_output(['git','show','HEAD:yoyo/evaluation/eth4h_exit_exploration_verify.py'])
    assert hashlib.sha256(frozen).hexdigest()==digest(Path(__file__))
    payload=json.loads((OUT/'summary.json').read_text());frame,_=load_source()
    checks={};concentration=[];slope=frame.slow_ma.diff().to_numpy()
    for row in payload['summary']:
        stem=f"{row['window']}_{row['arm']}"
        t=read(stem+'_trades.csv',['signal_time','entry_time','exit_time']);e=read(stem+'_equity.csv',['time']);c=read(stem+'_controls.csv')
        fees=.001*t.qty*(t.entry_price+t.exit_price)
        pnl=t.direction*t.qty*(t.exit_price-t.entry_price)-fees
        checks[stem+'_fees']=bool(np.allclose(t.fees,fees,rtol=1e-12))
        checks[stem+'_cash_identity']=bool(np.allclose(t.net_pnl,pnl,rtol=1e-12))
        checks[stem+'_final_equity']=bool(np.isclose(500+pnl.sum(),row['final_equity'],rtol=1e-12))
        checks[stem+'_bar_equity_final']=bool(np.isclose(e.equity.iloc[-1],row['final_equity'],rtol=1e-12))
        checks[stem+'_drawdown']=bool(np.isclose((1-e.equity/e.equity.cummax().clip(lower=500)).max()*100,row['close_dd_pct']))
        sig,ent=t.signal_i.to_numpy(int),t.entry_i.to_numpy(int)
        ep=frame.open.iloc[ent].to_numpy();sc=frame.close.iloc[sig].to_numpy()
        dist=np.minimum(4*frame.atr.iloc[sig].to_numpy(),sc*.03)
        marks=e.set_index('time').equity.reindex(t.entry_time).to_numpy()
        checks[stem+'_entry_sizing']=bool(np.allclose(t.qty,marks*np.minimum(1.,.005/(dist/sc))/sc,rtol=1e-12))
        checks[stem+'_initial_stop']=bool(np.allclose(t.initial_stop,sc-t.direction.to_numpy()*dist,rtol=1e-12))
        checks[stem+'_next_open']=bool(np.array_equal(ent,sig+1) and np.allclose(ep,t.entry_price,rtol=1e-12))
        checks[stem+'_date_bounds']=bool(t.exit_time.max()<=pd.Timestamp('2026-05-01',tz='UTC'))
        gate=row['arm'].endswith('_slope') or (row['arm']=='SMA_comparator' and payload['selected_policy']['slope_gate'])
        checks[stem+'_causal_flat_gate']=bool(not gate or (slope[sig]*t.direction.to_numpy()>0).all())
        a,b=c.case_signal_i.to_numpy(int),c.control_signal_i.to_numpy(int)
        checks[stem+'_control_strata']=bool(np.array_equal(frame.open_time.iloc[a].dt.strftime('%Y-%m'),frame.open_time.iloc[b].dt.strftime('%Y-%m')) and np.array_equal(frame.hk_hour.iloc[a]//6,frame.hk_hour.iloc[b]//6) and np.array_equal(frame.vol_bin.iloc[a],frame.vol_bin.iloc[b]) and (np.abs(a-b)>12).all())
        checks[stem+'_control_gate']=bool(not gate or (slope[b]*c.direction.to_numpy()>0).all())
        cr=c.direction*(c.control_exit_price/c.control_entry_price-1)-.001*(1+c.control_exit_price/c.control_entry_price)
        checks[stem+'_control_cash']=bool(np.allclose(cr,c.control_net_return,atol=1e-12))
        means=c.groupby('trade_id').control_net_return.mean()
        checks[stem+'_matched_means']=bool(np.allclose(t.loc[t.matched,'control_mean'],means.reindex(t.index[t.matched]),atol=1e-12))
        ranking=inference(t)
        checks[stem+'_ranking_precision']=all(ranking[k]==row['ranking'][k] for k in ['ranking_permutation_p','matched_month_cluster_signflip_p'])
        if row['arm']==payload['selected_policy']['name']:
            years=(e.time.iloc[-1]-(e.time.iloc[0]-pd.Timedelta(hours=4))).total_seconds()/86400/365.25
            concentration.append({'window':row['window'],'trades':len(t),'net_winners':int((t.net_pnl>0).sum()),
                                  'cagr_pct':((row['final_equity']/500)**(1/years)-1)*100,
                                  'largest5_positive_share_pct':row['largest_5_share_of_positive_pnl_pct'],
                                  'matched_coverage_pct':row['coverage_pct'],
                                  'net_bp_excluding_largest3':t.net_return.sort_values(ascending=False).iloc[3:].mean()*1e4})
    ca=read('common_cohort_R1_BE_on_trades.csv',['signal_time']);cb=read('common_cohort_BE_off_trades.csv',['signal_time'])
    checks['common_event_identity']=bool(np.array_equal(ca.signal_i,cb.signal_i) and np.array_equal(ca.entry_price,cb.entry_price) and np.array_equal(ca.qty,cb.qty))
    checks['common_event_difference']=bool(np.isclose((cb.net_return-ca.net_return).mean()*1e4,payload['cohort']['be_off_minus_on_net_bp']))
    deltas=[]
    for window in ['development','exposed_replication','continuous']:
        a=read(f'{window}_R1_BE_on_trades.csv').set_index('signal_i')
        b=read(f"{window}_{payload['selected_policy']['name']}_trades.csv").set_index('signal_i')
        shared=a.index.intersection(b.index);removed=a.index.difference(b.index);added=b.index.difference(a.index)
        checks[window+'_shared_entry_exit_unchanged']=bool(np.array_equal(a.loc[shared,'exit_i'],b.loc[shared,'exit_i']) and np.allclose(a.loc[shared,'net_return'],b.loc[shared,'net_return'],atol=1e-12))
        pieces={'window':window,'shared_trades':len(shared),'removed_trades':len(removed),'added_trades':len(added),
                'removed_net_pnl':float(a.loc[removed,'net_pnl'].sum()),'added_net_pnl':float(b.loc[added,'net_pnl'].sum()),
                'shared_position_sizing_delta':float((b.loc[shared,'net_pnl']-a.loc[shared,'net_pnl']).sum()),
                'total_net_pnl_delta':float(b.net_pnl.sum()-a.net_pnl.sum())}
        checks[window+'_change_attribution']=bool(np.isclose(-pieces['removed_net_pnl']+pieces['added_net_pnl']+pieces['shared_position_sizing_delta'],pieces['total_net_pnl_delta']))
        deltas.append(pieces)
    qa={'passed':all(checks.values()),'checks':checks,'concentration':concentration,'filter_attribution':deltas,
        'market_replay_rerun':False,'native_parity':False,'verification_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'limitation':'Accounting verification does not independently establish intrabar or native Pine execution parity.'}
    save_json(target,qa)
    assert qa['passed'],[k for k,v in checks.items() if not v]
    report(payload)
    print(json.dumps({'checks':len(checks),'passed':qa['passed'],'concentration':concentration,'attribution':deltas},indent=2))


if __name__=='__main__':main()
