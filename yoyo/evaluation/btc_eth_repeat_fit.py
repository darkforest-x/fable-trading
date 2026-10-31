"""Frozen chronological BTC/ETH entry/continuation models, no parameter search.

Only completed NY weeks train/calibrate models. All coins from the same week
stay together; label expiry is purged at split boundaries. C=1 is fixed before
observing outcomes. Per-coin sigmoid calibration uses only the preceding
quarter, with a pooled fallback. Weekly resampling respects clustered touches.
Execution outcomes always come from nautilus_independent, never from this file.
Continuation features use the completed near-touch 5m close and earlier bars;
rows whose farther position has already exited by that close are ineligible.
"""
from __future__ import annotations

import argparse
import pickle
import subprocess
import numpy as np
import pandas as pd
from scipy.special import logit
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from yoyo.evaluation.btc_eth_repeat_data import (
    EXP, FEATURES, config, dump, read_frame, base_features, make_features, bounds)


def read(name):
    return pd.read_csv(EXP/name,float_precision='round_trip')


def labels():
    events=read('events.csv.gz')
    outcomes=pd.concat([read(f'{s}_native_trades.csv.gz') for s in config()['symbols']],ignore_index=True)
    sig=outcomes[outcomes.kind.eq('signal')].copy()
    # Preserve entry/submission clocks from the engine, metadata from the ledger.
    extra=[c for c in events if c not in sig or c=='event_id']
    sig=sig.merge(events[extra],on='event_id',validate='many_to_one')
    sig['label']=sig.reason.eq(4).astype(int)
    control=outcomes[outcomes.kind.eq('control')].groupby(['event_id','target']).agg(
        random_bp=('net_notional_bp','mean'),random_r=('net_r','mean'),random_n=('trade_id','size')).reset_index()
    sig=sig.merge(control,on=['event_id','target'],how='left',validate='one_to_one')
    return sig, outcomes


def weights(data):
    w=1/data.groupby(['symbol','week']).event_id.transform('size').astype(float)
    w=w/w.groupby(data.symbol).transform('sum')
    return (w/w.mean()).to_numpy()


def rolling_predict(data, feature_names, tag):
    data=data.copy()
    data['week_start']=pd.to_datetime(data.week).dt.tz_localize('America/New_York').dt.tz_convert('UTC')
    data['expiry_dt']=pd.to_datetime(data.expiry,utc=True)
    allpred,receipts=[],[]
    model_dir=EXP/'models'
    model_dir.mkdir(exist_ok=True)
    for start in pd.date_range('2025-01-01','2026-10-01',freq='QS',tz='America/New_York'):
        end=start+pd.DateOffset(months=3)
        cal_start=start-pd.DateOffset(months=3)
        train=data[data.complete & (data.expiry_dt<=cal_start)]
        cal=data[data.complete & (data.week_start>=cal_start) & (data.expiry_dt<=start)]
        test=data[(data.week_start>=start)&(data.week_start<end)]
        record=dict(fold=start.strftime('%YQ')+str(start.quarter),tag=tag,
                    train_n=len(train),cal_n=len(cal),test_n=len(test),
                    train_weeks=train.week.nunique(),cal_weeks=cal.week.nunique(),test_weeks=test.week.nunique(),
                    train_positive=float(train.label.mean()) if len(train) else None,
                    cal_positive=float(cal.label.mean()) if len(cal) else None,
                    train_label_expiry_max=str(train.expiry_dt.max()),cal_label_expiry_max=str(cal.expiry_dt.max()))
        if len(train)<50 or train.label.nunique()<2 or len(cal)<20 or cal.label.nunique()<2 or test.empty:
            record['status']='insufficient_history'
            receipts.append(record)
            continue
        assert not set(train.week)&set(cal.week) and not set(train.week)&set(test.week) and not set(cal.week)&set(test.week)
        columns=['f_'+f for f in feature_names]
        model=make_pipeline(SimpleImputer(strategy='median',keep_empty_features=True),StandardScaler(),
                            LogisticRegression(C=1.,max_iter=2000,solver='lbfgs',random_state=config()['seed']))
        model.fit(train[columns].replace([np.inf,-np.inf],np.nan),train.label,
                  logisticregression__sample_weight=weights(train))
        cal_raw=model.predict_proba(cal[columns].replace([np.inf,-np.inf],np.nan))[:,1]
        raw=model.predict_proba(test[columns].replace([np.inf,-np.inf],np.nan))[:,1]
        calibrators={}
        pred=test.copy()
        pred['p']=np.nan
        pred['top_gate']=False
        for symbol in sorted(test.symbol.unique()):
            selected=cal.symbol.eq(symbol).to_numpy()
            fallback=int(selected.sum())<30 or cal.loc[selected,'label'].nunique()<2
            if fallback:
                selected=np.ones(len(cal),dtype=bool)
            calibration=LogisticRegression(C=1.,max_iter=2000)
            x=logit(np.clip(cal_raw[selected],1e-6,1-1e-6)).reshape(-1,1)
            calibration.fit(x,cal.loc[selected,'label'],sample_weight=weights(cal.loc[selected]))
            sel=test.symbol.eq(symbol).to_numpy()
            pp=calibration.predict_proba(logit(np.clip(raw[sel],1e-6,1-1e-6)).reshape(-1,1))[:,1]
            threshold=float(np.quantile(calibration.predict_proba(x)[:,1],.9))
            pred.loc[pred.symbol.eq(symbol),'p']=pp
            pred.loc[pred.symbol.eq(symbol),'top_gate']=pp>=threshold
            calibrators[symbol]=dict(model=calibration,pooled_fallback=fallback,cal_n=int(selected.sum()),top_threshold=threshold)
        pred['fold']=record['fold']
        pred['gate']=pred.p>pred.break_even_p
        allpred.append(pred)
        record.update(status='fitted',calibration={s:{k:v for k,v in c.items() if k!='model'} for s,c in calibrators.items()},
                      coefficients=dict(zip(feature_names,model[-1].coef_[0].tolist())))
        receipts.append(record)
        with (model_dir/f'{tag}_{record["fold"]}.pkl').open('wb') as handle:
            pickle.dump(dict(model=model,calibrators=calibrators,features=columns,receipt=record),handle)
    dump(EXP/f'{tag}_folds.json',receipts)
    return pd.concat(allpred,ignore_index=True) if allpred else pd.DataFrame()


def no_overlap(data, selected=None):
    """Admission-only selection of independent native results; no fill simulation."""
    if selected is not None:
        data=data[selected].copy()
    keep=[]
    for _,coin in data.groupby('symbol'):
        busy=-1
        for idx,row in coin.sort_values(['submission_i','trade_id']).iterrows():
            if int(row.submission_i)>=busy:
                keep.append(idx)
                busy=int(row.exit_i)
    return data.loc[keep].sort_values(['exit_time','symbol'])


def week_interval(data,column):
    if data.empty:
        return [None,None]
    # Resample weeks, retaining all within-week observations and both assets.
    g=data.groupby('week')[column].agg(['sum','count'])
    rng=np.random.default_rng(config()['seed'])
    ix=rng.integers(0,len(g),size=(2000,len(g)))
    values=g['sum'].to_numpy()[ix].sum(axis=1)/g['count'].to_numpy()[ix].sum(axis=1)
    return np.quantile(values,[.025,.975]).tolist()


def paired_stats(data,column='net_notional_bp',control='random_bp'):
    matched=data.dropna(subset=[control]).copy()
    if matched.empty:
        return dict(matched_n=0,random_bp=None,excess_bp=None,week_p=None,excess_ci95=[None,None])
    matched['delta']=matched[column]-matched[control]
    weekly=matched.groupby('week').delta.sum().to_numpy()
    rng=np.random.default_rng(config()['seed'])
    null=(rng.choice([-1.,1.],size=(10000,len(weekly)))*weekly).sum(axis=1)
    p=(1+int((null>=weekly.sum()).sum()))/10001
    return dict(matched_n=len(matched),random_bp=float(matched[control].mean()),
                matched_signal_bp=float(matched[column].mean()),excess_bp=float(matched.delta.mean()),
                week_p=p,excess_ci95=week_interval(matched,'delta'))


def metrics(data):
    if data.empty:
        return dict(n=0)
    ordered=data.sort_values(['exit_time','symbol'])
    curve=np.r_[0.,ordered.net_r.cumsum().to_numpy()]
    mean_gross=float((data.gross_r*data.risk/data.entry*10000).mean())
    pos=float(data.loc[data.net_r>0,'net_r'].sum())
    neg=-float(data.loc[data.net_r<0,'net_r'].sum())
    out=dict(n=len(data),weeks=data.week.nunique(),target_rate=float(data.label.mean()),
             net_win_rate=float((data.net_notional_bp>0).mean()),gross_bp=mean_gross,
             net_bp=float(data.net_notional_bp.mean()),net_bp_ci95=week_interval(data,'net_notional_bp'),
             net_r=float(data.net_r.sum()),closed_trade_drawdown_r=float((np.maximum.accumulate(curve)-curve).max()),
             profit_factor=pos/neg if neg else None,time_exit_n=int(data.reason.eq(0).sum()),
             adverse_path_n=int(data.ambiguous.sum()),**paired_stats(data))
    if 'p' in data:
        out.update(auc=float(roc_auc_score(data.label,data.p)) if data.label.nunique()==2 else None,
                   brier=float(brier_score_loss(data.label,data.p)))
    return out


def permutation_rank(data):
    """Permutation of weekly outcomes within coin/year; rows aren't independent."""
    if data.empty:
        return None
    d=data.copy()
    d['year']=d.week.str[:4]
    w=d.groupby(['symbol','year','week']).agg(p=('p','mean'),y=('label','mean')).reset_index()
    p=w.p-w.groupby(['symbol','year']).p.transform('mean')
    y=w.y-w.groupby(['symbol','year']).y.transform('mean')
    observed=float(np.dot(p,y))
    rng=np.random.default_rng(config()['seed'])
    groups=list(w.groupby(['symbol','year']).indices.values())
    count=0
    for _ in range(10000):
        perm=y.to_numpy().copy()
        for idx in groups:
            perm[idx]=rng.permutation(perm[idx])
        count+=float(np.dot(p,perm))>=observed
    return float((count+1)/10001)


def entry():
    sig,_=labels()
    near=sig[sig.target.eq('near')].copy()
    cfg=config()
    reward=near.side*(near.target_price-near.entry)-cfg['maker_fee']*(near.entry+near.target_price)
    loss=near.risk+cfg['maker_fee']*near.entry+cfg['taker_fee']*near.stop
    near['break_even_p']=loss/(loss+reward)
    pred=rolling_predict(near,FEATURES,'entry')
    single=rolling_predict(near,['is_eth','side','reward_r'],'single_reward')
    pred.to_csv(EXP/'entry_predictions.csv.gz',index=False)
    single.to_csv(EXP/'single_predictions.csv.gz',index=False)
    complete=near[near.complete]
    oos=pred[pred.complete]
    single=single[single.complete]
    rows=[]
    selections=[]
    for period,data in [('all_complete',complete),('rolling_test',oos)]:
        arms={'first_only':no_overlap(data,data.ordinal.eq(1)), 'all_repeats':no_overlap(data)}
        if period=='rolling_test':
            arms.update(score_gate=no_overlap(data,data.gate),
                        calibration_top_decile=no_overlap(data,data.top_gate),
                        single_reward_gate=no_overlap(single,single.gate))
        for arm,chosen in arms.items():
            chosen=chosen.copy()
            chosen['analysis_period'],chosen['arm']=period,arm
            selections.append(chosen)
            for symbol in ['ALL','BTC','ETH']:
                coin=chosen if symbol=='ALL' else chosen[chosen.symbol.eq(symbol)]
                rows.append(dict(period=period,arm=arm,symbol=symbol,side='both',**metrics(coin)))
                if symbol!='ALL':
                    for side in [-1,1]:
                        rows.append(dict(period=period,arm=arm,symbol=symbol,side='upper_short' if side==-1 else 'lower_long',
                                         **metrics(coin[coin.side.eq(side)])))
    pd.DataFrame(rows).to_csv(EXP/'entry_metrics.csv',index=False)
    pd.concat(selections).to_csv(EXP/'selected_trades.csv.gz',index=False)
    # Diagnostic top decile is explicitly retrospective, distinct from the deployable gate.
    diagnostics=[]
    for symbol in ['BTC','ETH']:
        coin=oos[oos.symbol.eq(symbol)]
        top=coin.nlargest(max(1,int(np.ceil(len(coin)*.1))),'p')
        diagnostics.append(dict(symbol=symbol,all_predictions=metrics(coin),
                                retrospective_top_decile=metrics(top),
                                week_rank_permutation_p=permutation_rank(coin)))
    dump(EXP/'entry_diagnostics.json',diagnostics)
    dump(EXP/'entry_summary.json',dict(rows=rows,diagnostics=diagnostics,
                                     current_week=pred.loc[~pred.complete,['event_id','symbol','week','ordinal','side','p','break_even_p','gate','reason','net_notional_bp']].to_dict('records')))
    print(pd.DataFrame(rows).query("period=='rolling_test' and side=='both'")
          [['arm','symbol','n','net_bp','net_r','week_p']].to_string(index=False),flush=True)


def prepare_continuation():
    sig,_=labels()
    near=sig[sig.target.eq('near') & sig.reason.eq(4)].set_index('event_id')
    events=read('events.csv.gz').set_index('event_id')
    rows=[]
    for symbol in config()['symbols']:
        frame=read_frame(symbol)
        base=base_features(frame)
        orders=read(f'{symbol}_entries.csv.gz')
        orders=orders[orders.kind.eq('signal') & orders.target.eq('near')].set_index('event_id')
        close_orders={}
        far=sig[sig.symbol.eq(symbol) & ~sig.target.eq('near')]
        for _,r in far.iterrows():
            event=r.event_id
            if event not in near.index:
                continue
            decision=int(near.loc[event,'exit_i'])
            if int(r.exit_i)<=decision:
                continue
            e=events.loc[event]
            expiry=pd.Timestamp(e.expiry)
            feat=make_features(frame,base,decision,symbol,int(e.side),e.monday_high,e.monday_low,
                               e.monday_close,e.entry,int(e.ordinal),
                               None if e.previous_i<0 else int(e.previous_i),expiry)
            current=float(frame.close.iloc[decision])
            risk=r.side*(current-r.stop)
            gain=r.side*(r.target_price-current)
            if risk<=0 or gain<=0:
                continue
            feat.update(reward_r=gain/risk,distance_atr=gain/float(base.atr.iloc[decision]))
            feat['holding_hours']=(decision-int(r.entry_i))*5/60
            feat['unrealized_r']=r.side*(current-r.entry)/r.risk
            rr=r.to_dict()
            rr.update({f'f_{k}':v for k,v in feat.items()})
            # Compare continuation with a taker close now; the already-paid
            # entry commission cancels from this incremental decision.
            incremental_win=gain+config()['taker_fee']*current-config()['maker_fee']*r.target_price
            incremental_loss=risk+config()['taker_fee']*r.stop-config()['taker_fee']*current
            rr.update(decision_i=decision,decision_price=current,
                      break_even_p=incremental_loss/(incremental_loss+incremental_win))
            rows.append(rr)
            if event not in close_orders:
                order=orders.loc[event].to_dict()
                order.update(event_id=event,trade_id=f'{event}:close_now',end_i=decision,target_price=np.nan,target='close_now')
                close_orders[event]=order
        pd.DataFrame(close_orders.values()).to_csv(EXP/f'{symbol}_continuation_orders.csv.gz',index=False)
    pd.DataFrame(rows).to_csv(EXP/'continuation_candidates.csv.gz',index=False)
    print('Continuation candidates',len(rows),flush=True)


def continuation():
    data=read('continuation_candidates.csv.gz')
    closed=pd.concat([read(f'{s}_close_now_trades.csv.gz') for s in config()['symbols']],ignore_index=True)
    base=closed[['event_id','net_notional_bp','net_r']].rename(columns={'net_notional_bp':'close_now_bp','net_r':'close_now_r'})
    data=data.merge(base,on='event_id',validate='many_to_one')
    predictions,rows=[],[]
    for target in ['far','opposite1618']:
        cand=data[data.target.eq(target)].copy()
        pred=rolling_predict(cand,FEATURES+['holding_hours','unrealized_r'],'continue_'+target)
        if pred.empty:
            continue
        pred['incremental_bp']=pred.net_notional_bp-pred.close_now_bp
        pred['policy_bp']=np.where(pred.gate,pred.net_notional_bp,pred.close_now_bp)
        pred['policy_incremental_bp']=pred.policy_bp-pred.close_now_bp
        predictions.append(pred)
        for symbol in ['ALL','BTC','ETH']:
            d=pred[pred.complete] if symbol=='ALL' else pred[pred.complete & pred.symbol.eq(symbol)]
            if d.empty:
                continue
            rows.append(dict(target=target,symbol=symbol,n=len(d),weeks=d.week.nunique(),selected=int(d.gate.sum()),
                             auc=float(roc_auc_score(d.label,d.p)) if d.label.nunique()==2 else None,
                             close_now_bp=float(d.close_now_bp.mean()),always_continue_bp=float(d.net_notional_bp.mean()),
                             score_policy_bp=float(d.policy_bp.mean()),
                             always_incremental_bp=float(d.incremental_bp.mean()),
                             score_incremental_bp=float(d.policy_incremental_bp.mean()),
                             paired_close_control=paired_stats(d,'policy_bp','close_now_bp'),
                             random_entry_control=paired_stats(d),
                             week_rank_permutation_p=permutation_rank(d)))
    if predictions:
        pd.concat(predictions).to_csv(EXP/'continuation_predictions.csv.gz',index=False)
    dump(EXP/'continuation_summary.json',rows)
    print(pd.DataFrame(rows).drop(columns=['paired_close_control','random_entry_control'],errors='ignore').to_string(index=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['entry','prepare_continuation','continuation'])
    args=parser.parse_args()
    globals()[args.stage]()
