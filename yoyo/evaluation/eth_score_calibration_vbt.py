"""VectorBT admission and accounting of unchanged cached ETH native fills.

The compressed timeline has native entries, native exits, and submissions.
A slot is reserved at submission, including the interval until the next5m
entry. At a shared bar index entry precedes exit, exit precedes submission;
this matches the source rule allowing new submission at the previous exit
bar close. Prices and fee types come only from completed native records.
There is no OHLC scan or custom fill/PNL engine. VectorBT processes orders,
positions and costs for independent parameter columns. One dollar initial
risk per trade expresses PNL in R, never as a funded account percentage.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder,OrderStatus

from yoyo.evaluation.eth_score_calibration_data import EXP,config,read,dump,rule_mask


@njit(cache=True)
def order_func(c,event_ids,event_types,admit,entry,exit_price,side,risk,exit_fee,active):
    k=event_ids[c.i];kind=event_types[c.i]
    if kind==2:
        if active[c.col]<0 and admit[k,c.col]:active[c.col]=k
        return NoOrder
    if active[c.col]!=k:return NoOrder
    if kind==0:
        return nb.order_nb(size=side[k]/risk[k],price=entry[k],fees=.0002,allow_partial=False,raise_reject=True)
    if c.position_now!=0:
        return nb.order_nb(size=-c.position_now,price=exit_price[k],fees=exit_fee[k],allow_partial=False,raise_reject=True)
    return NoOrder


@njit(cache=True)
def post_order(c,event_ids,event_types,active,accepted):
    if c.order_result.status!=OrderStatus.Filled:return
    k=event_ids[c.i]
    if event_types[c.i]==0:accepted[k,c.col]=True
    else:active[c.col]=-1


def cached_portfolios(data,masks,names):
    """One independent VectorBT column per admission policy, native fills reused."""
    d=data.sort_values(['submission_i','trade_id']).reset_index(drop=True)
    aligned=pd.DataFrame(masks,index=data.trade_id,columns=names).loc[d.trade_id].to_numpy(bool)
    assert d.trade_id.is_unique and (d.entry_i>d.submission_i).all()
    assert (d.exit_i>=d.entry_i).all()
    n=len(d);k=len(names)
    timeline=[]
    for i,r in enumerate(d.itertuples()):
        timeline.extend([(int(r.entry_i),0,i),(int(r.exit_i),1,i),(int(r.submission_i),2,i)])
    timeline.sort()
    ev=np.asarray(timeline,dtype=np.int64)
    ids,types=ev[:,2],ev[:,1]
    marks=np.where(types==1,d.exit_price.to_numpy()[ids],d.entry.to_numpy()[ids])
    close=pd.DataFrame(np.repeat(marks[:,None],k,axis=1),columns=names)
    active=np.full(k,-1,np.int64);accepted=np.zeros((n,k),bool)
    pf=vbt.Portfolio.from_order_func(close,order_func,ids,types,aligned,
        d.entry.to_numpy(float),d.exit_price.to_numpy(float),d.side.to_numpy(float),d.risk.to_numpy(float),
        np.where(d.reason.eq(4),.0002,.0005),active,
        post_order_func_nb=post_order,post_order_args=(ids,types,active,accepted),
        init_cash=1e9,cash_sharing=False,group_by=False,max_orders=2*n*k)
    assert (active==-1).all()
    records=pd.DataFrame(pf.trades.records)
    out={};max_error=0.
    for col,name in enumerate(names):
        trades=records[records.col.eq(col)]
        assert trades.status.eq(1).all()
        selected=d.loc[accepted[:,col]].copy()
        mapping={int(ids[int(r.entry_idx)]):float(r.pnl) for r in trades.itertuples()}
        assert set(mapping)==set(np.flatnonzero(accepted[:,col]))
        selected['vbt_net_r']=[mapping[i] for i in selected.index]
        if len(selected):
            error=float(abs(selected.vbt_net_r-selected.net_r).max())
            assert error<1e-8;max_error=max(max_error,error)
        selected['arm']=name;out[name]=selected
    return out,dict(candidates=n,columns=k,event_rows=len(ev),orders=len(pf.orders.records),
                    selected=int(accepted.sum()),max_native_net_r_error=max_error,engine=vbt.__version__)


def choose_rule(scores):
    """Validation only: zero-trade wins when no eligible positive-net rule exists."""
    best=scores[0]
    assert best['name']=='none' and best['net_r']==0.
    for row in scores[1:]:
        if row['eligible'] and row['net_r']>best['net_r']+1e-12:best=row
    return best


def main():
    cfg=config();data=read('scores.csv.gz')
    choices=json.loads((EXP/'frozen_candidates.json').read_text())
    selected=[];decisions=[];audits=[];current=[];cal_selections=[]
    for spec in choices:
        target,fold=spec['target'],spec['fold']
        cohort=data[data.target.eq(target)&data.fold.eq(fold)]
        cal=cohort[cohort.role.eq('calibration')]
        tests=cohort[cohort.role.eq('test')]
        cal_names=[r['name'] for r in spec['rules']]
        masks=np.column_stack([rule_mask(cal,r) for r in spec['rules']])
        cal_results,audit=cached_portfolios(cal,masks,cal_names)
        audits.append(dict(target=target,fold=fold,stage='calibration',**audit))
        candidates=[]
        for rule in spec['rules']:
            g=cal_results[rule['name']];weeks=int(g.week.nunique())
            valid=rule['name']=='none' or (len(g)>=cfg['selection_min_trades'] and weeks>=cfg['selection_min_weeks'])
            candidates.append(dict(rule,name=rule['name'],n=len(g),weeks=weeks,eligible=valid,
                                   net_r=float(g.vbt_net_r.sum()),mean_net_bp=float(g.net_notional_bp.mean()) if len(g) else None,
                                   random_bp=float(g.random_bp.mean()) if len(g) else None))
            cal_selections.append(g)
        winner=choose_rule(candidates)
        decision=dict(target=target,fold=fold,adequate=spec['adequate'],chosen=winner,candidates=candidates,
                      bias_delta_r=spec['bias_delta_r'],cal_n=len(cal),cal_weeks=int(cal.week.nunique()))
        decisions.append(decision)
        names=['fresh_raw_zero','lagged_raw_zero','unscored','single_reward','bias_calibrated_zero','past_selected_threshold']
        def gates(g):
            return np.column_stack([g.fresh_score>0,g.score>0,np.ones(len(g),bool),g.single_reward_admit,
                (g.score+spec['bias_delta_r']>0)&spec['adequate'],rule_mask(g,winner)])
        actual=tests[tests.complete]
        result,audit=cached_portfolios(actual,gates(actual),names)
        audits.append(dict(target=target,fold=fold,stage='test',**audit))
        selected.extend(result.values())
        now=tests[~tests.complete].copy()
        if len(now):
            masks=gates(now)
            for i,name in enumerate(names):
                part=now[['trade_id','event_id','symbol','target','fold','week','side','ordinal','touch_open','score','fresh_score']].copy()
                part['arm']=name;part['admit']=masks[:,i];part['bias_delta_r']=spec['bias_delta_r']
                current.append(part)
        print(target,fold,'chosen',winner['name'],'calR',round(winner['net_r'],3),'test_n',len(result['past_selected_threshold']),flush=True)
    pd.concat(selected,ignore_index=True).to_csv(EXP/'selected_trades.csv.gz',index=False)
    pd.concat(cal_selections,ignore_index=True).to_csv(EXP/'calibration_selections.csv.gz',index=False)
    pd.concat(current,ignore_index=True).to_csv(EXP/'current_week.csv',index=False)
    dump(EXP/'decisions.json',decisions);dump(EXP/'engine_audit.json',audits)
    dump(EXP/'engine_receipt.json',dict(vectorbt=vbt.__version__,new_native_positions=0,price_replays=0,
         source='Original native fill prices, clocks and fee types; VectorBT performs admission and fixed-risk accounting.',
         builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))


if __name__=='__main__':main()
