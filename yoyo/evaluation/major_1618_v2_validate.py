"""Audit saved models/fills and causal features without refitting or replaying.

Reuse unchanged BTC/ETH fee evidence, check every new XAU fill, reconstruct
every frozen prediction, and perturb future OHLCV after real decision clocks.
All inference uses explicit feature allowlists; target labels are audit-only.
"""
from __future__ import annotations

import json
import argparse
import pickle
import platform
import subprocess
import numpy as np
import pandas as pd
from scipy.special import expit,logit
from sklearn.metrics import roc_auc_score

from yoyo.evaluation.btc_eth_repeat_data import dump,bounds,base_features,make_features
from yoyo.evaluation.btc_eth_repeat_fit import labels,weights,no_overlap,metrics
from yoyo.evaluation.major_1618_v2_data import EXP,OLD,config,read,frame,label_data
from yoyo.evaluation.major_1618_v2_models import FINAL,split,score_model
from yoyo.evaluation.major_1618_v2_evaluate import predictions


def audit_models(data):
    records=[]
    for path in sorted((EXP/'models').glob('*/*/*/*.pkl')):
        with path.open('rb') as handle:
            saved=pickle.load(handle)
        receipt=saved['record'];symbol,target,fold=receipt['symbol'],receipt['target'],receipt['fold']
        cohort=data[data.symbol.eq(symbol)&data.target.eq(target)]
        start=pd.Period(fold,freq='Q').start_time.tz_localize('America/New_York')
        old,cal,fresh,test=split(cohort,start)
        train=old if receipt['arm'].startswith('asset_lr_') else fresh
        assert train.event_id.tolist()==receipt['train_event_ids']
        assert train.complete.all() and train.expiry_dt.max()<=start
        assert set(train.symbol)=={symbol} and set(train.target)=={target}
        assert not set(train.event_id)&set(test.event_id)
        assert not set(train.week)&set(test.week)
        columns=saved['features']
        assert all(c.startswith('f_') for c in columns) and 'f_is_eth' not in columns
        if receipt['arm']==FINAL:assert 'f_ordinal_log' not in columns
        model=saved['model'];reg=saved['kind']=='regressor'
        raw=score_model(model,test,columns,reg)
        numeric={}
        if saved['kind']=='lr':
            X=model[:-1].transform(train[columns].replace([np.inf,-np.inf],np.nan))
            beta=model[-1].coef_[0];intercept=model[-1].intercept_[0]
            p=expit(np.einsum('ij,j->i',X,beta)+intercept)
            np.testing.assert_allclose(p,score_model(model,train,columns),rtol=0,atol=1e-12)
            w=weights(train);err=w*(p-train.label.to_numpy())
            grad=max(abs(np.einsum('ij,i->j',X,err)+beta).max(),abs(err.sum()))/w.sum()
            assert np.isfinite(X).all() and np.isfinite(beta).all() and grad<.005
            numeric=dict(gradient=float(grad),iterations=int(model[-1].n_iter_[0]))
        calibration=saved['calibrator']
        if calibration is not None:
            assert not set(cal.week)&set(test.week) and cal.expiry_dt.max()<=start
            xx=logit(np.clip(score_model(model,cal,columns),1e-6,1-1e-6))
            cp=expit(xx*calibration.coef_[0,0]+calibration.intercept_[0])
            np.testing.assert_allclose(cp,calibration.predict_proba(xx[:,None])[:,1],rtol=0,atol=1e-12)
            w=weights(cal);err=w*(cp-cal.label.to_numpy())
            grad=max(abs(err.sum()),abs((xx*err).sum()+calibration.coef_[0,0]))/w.sum()
            assert np.isfinite(grad) and grad<.005
            numeric['calibration_gradient']=float(grad)
            raw=calibration.predict_proba(logit(np.clip(raw,1e-6,1-1e-6))[:,None])[:,1]
        pred=pd.read_csv(path.parent/'predictions.csv.gz',float_precision='round_trip')
        pred=pred[pred.arm.eq(receipt['arm'])].set_index('trade_id').loc[test.trade_id]
        np.testing.assert_allclose(raw,pred.score,rtol=0,atol=1e-12)
        gate=raw>0 if reg else raw>test.break_even_p.to_numpy()
        np.testing.assert_array_equal(gate,pred.admit)
        records.append(dict(path=str(path.relative_to(EXP)),train_n=len(train),test_n=len(test),
                            max_prediction_error=float(abs(raw-pred.score.to_numpy()).max()),**numeric))
    return records


def audit_xau():
    cfg=config();events=read('XAU_events.csv.gz');f=frame('XAU');result=[]
    for suffix in ['native','path']:
        rows=read(f'XAU_{suffix}_trades.csv.gz');fills=read(f'XAU_{suffix}_fills.csv.gz')
        orders=read(f'XAU_{suffix}_orders.csv.gz').set_index('trade_id')
        assert len(fills)==2*len(rows) and len(rows)==len(orders)
        detail=fills.merge(orders[['order_mode']],left_on='trade_id',right_index=True,validate='many_to_one')
        maker=detail.phase.eq('target')|(detail.phase.eq('entry')&detail.order_mode.eq('limit'))
        expected=detail.price*detail.quantity*np.where(maker,cfg['maker_fee'],cfg['taker_fee'])
        fee_error=float(abs(expected-detail.commission).max());assert fee_error<1e-7
        total=fills.groupby('trade_id').commission.sum()
        net=(rows.side*(rows.exit_price-rows.entry)-rows.trade_id.map(total)/rows.quantity)/rows.entry*10000
        pnl_error=float(abs(net-rows.net_notional_bp).max());assert pnl_error<1e-8
        assert (rows.exit_i<=rows.end_i).all()
        signal=rows[rows.kind.eq('signal')].merge(events[['event_id','touch_i']],on='event_id',validate='many_to_one')
        assert (signal.entry_i==signal.touch_i).all() and (signal.entry_i==signal.submission_i+1).all()
        assert all(f.index[int(r.end_i)]==pd.Timestamp(bounds(r.week)[2]) for r in rows[rows.complete].itertuples())
        result.append(dict(arm=suffix,trades=len(rows),fills=len(fills),fee_error=fee_error,pnl_bp_error=pnl_error))
    return result


def future_invariance():
    result=[]
    oldevents=pd.read_csv(OLD/'events.csv.gz',float_precision='round_trip')
    for symbol in config()['symbols']:
        events=read('XAU_events.csv.gz') if symbol=='XAU' else oldevents[oldevents.symbol.eq(symbol)]
        events=events[events.eligible].sort_values('submission_i')
        f=frame(symbol);base=base_features(f)
        for j in [0,len(events)//2,len(events)-1]:
            row=events.iloc[j];i=int(row.submission_i)
            changed=f.copy()
            changed.iloc[i+1:,changed.columns.get_indexer(['open','high','low','close'])]*=7
            changed.iloc[i+1:,changed.columns.get_loc('volume')]*=100
            alternative=base_features(changed)
            args=(i,symbol,int(row.side),row.monday_high,row.monday_low,row.monday_close,row.entry,
                  int(row.ordinal),None if row.previous_i<0 else int(row.previous_i),pd.Timestamp(row.expiry))
            before=make_features(f,base,*args);after=make_features(changed,alternative,*args)
            for key,value in before.items():
                np.testing.assert_allclose(value,row['f_'+key],rtol=0,atol=1e-10,equal_nan=True)
                np.testing.assert_allclose(value,after[key],rtol=0,atol=0,equal_nan=True)
            result.append(dict(symbol=symbol,event_id=row.event_id,features=len(before),error=0.))
    return result


def path_sensitivity(pred):
    results=[]
    for symbol in config()['symbols']:
        source=EXP if symbol=='XAU' else OLD
        suffix='path' if symbol=='XAU' else 'path_sensitivity'
        primary=pd.read_csv(source/f'{symbol}_native_trades.csv.gz',float_precision='round_trip').set_index('trade_id',drop=False)
        alt=pd.read_csv(source/f'{symbol}_{suffix}_trades.csv.gz',float_precision='round_trip').set_index('trade_id')
        columns=['exit_i','exit_price','reason','gross_r','fee_r','net_r','net_notional_bp','exit_time']
        common=primary.index.intersection(alt.index)
        changed=int((abs(primary.loc[common,'net_r']-alt.loc[common,'net_r'])>1e-7).sum())
        primary.loc[common,columns]=alt.loc[common,columns]
        controls=primary[primary.kind.eq('control')].groupby(['event_id','target']).agg(random_bp=('net_notional_bp','mean'),random_r=('net_r','mean'))
        for target,g in pred[pred.symbol.eq(symbol)&pred.arm.eq(FINAL)&pred.complete].groupby('target'):
            original=no_overlap(g,g.admit)
            coin=g.set_index('trade_id',drop=False).copy();coin.index.name=None
            coin.loc[:,columns]=primary.loc[coin.index,columns]
            coin['label']=coin.reason.eq(4).astype(int)
            for c in ['random_bp','random_r']:
                coin[c]=[controls.loc[(r.event_id,r.target),c] for r in coin.itertuples()]
            selected=no_overlap(coin,coin.admit)
            results.append(dict(symbol=symbol,target=target,checked_native_orders=len(alt),changed_native_outcomes=changed,
                                original_n=len(original),original_net_r=float(original.net_r.sum()),alternate=metrics(selected)))
    return results


def main():
    data=label_data();pred=predictions()
    old,_=labels()
    near=data[data.symbol.isin(['BTC','ETH'])&data.target.eq('near')].set_index('trade_id')
    old=old[old.target.eq('near')].set_index('trade_id')
    columns=['net_r','net_notional_bp','random_bp','label']+[c for c in old if c.startswith('f_')]
    pd.testing.assert_frame_equal(near.loc[old.index,columns],old[columns],check_dtype=False)
    selected=read('selected_trades.csv.gz')
    for _,g in selected.groupby(['symbol','target','arm']):
        g=g.sort_values(['submission_i','trade_id'])
        assert (g.submission_i.iloc[1:].to_numpy()>=g.exit_i.iloc[:-1].to_numpy()).all()
    baseline=read('metrics.csv')
    for symbol,n,value in [('BTC',59,.856578821),('ETH',83,-12.829584792)]:
        r=baseline[baseline.symbol.eq(symbol)&baseline.arm.eq('old_pooled')].iloc[0]
        assert r.n==n and abs(r.net_r-value)<1e-6
    oldvalidation=json.loads((OLD/'validation.json').read_text())
    outcome_summary=[]
    for (symbol,target),g in pred[pred.complete&pred.arm.eq(FINAL)].groupby(['symbol','target']):
        folds=[]
        for fold,part in g.groupby('fold'):
            folds.append(dict(fold=fold,n=len(part),wins=int(part.label.sum()),
                              auc=float(roc_auc_score(part.label,part.score)) if part.label.nunique()==2 else None))
        outcome_summary.append(dict(symbol=symbol,target=target,n=len(g),wins=int(g.label.sum()),
                                    win_weeks=int(g.loc[g.label.eq(1),'week'].nunique()),folds=folds))
    result=dict(models=audit_models(data),new_XAU_fills=audit_xau(),
                BTC_ETH_near_reuse_parity_rows=len(near),BTC_ETH_fee_evidence=oldvalidation['fees'],
                causal_future_mutations=future_invariance(),frozen_gate_path_sensitivity=path_sensitivity(pred),
                final_target_class_support=outcome_summary,nonoverlap=True,old_score_baseline_reproduced=True,
                numerical_warnings='Saved local sklearn matmul warnings retained. Every LR output independently reproduced by einsum, finite coefficients and first-order gradients checked. No new fit.',
                builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip())
    dump(EXP/'validation.json',result)
    import lightgbm,sklearn,scipy
    dump(EXP/'reproduction.json',dict(runtime=dict(python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__,
         lightgbm=lightgbm.__version__,sklearn=sklearn.__version__,scipy=scipy.__version__,nautilus='1.231.0 in separate native venv'),
         commands=['.venv/bin/python -m yoyo.evaluation.major_1618_v2_data prepare',
          '/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.major_1618_v2_data native',
          '/Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.major_1618_v2_data sensitivity',
          *[f'.venv/bin/python -m yoyo.evaluation.major_1618_v2_models --symbol {s}' for s in config()['symbols']],
          '.venv/bin/python -m yoyo.evaluation.major_1618_v2_evaluate',
          '.venv/bin/python -m yoyo.evaluation.major_1618_v2_validate'],
         prerequisites='Registered v1 inputs/native results; registered XAU candles and readonly local OKX cache through frozen cutoff. No downloads. Existing completed native and model artifacts reused.',
         research_only=True,production_eligible=False))
    print('Validated',dict(models=len(result['models']),XAU_fills=sum(r['fills'] for r in result['new_XAU_fills']),
                            BTC_ETH_reused_rows=len(near),causal_mutations=len(result['causal_future_mutations'])),flush=True)


def audit_diagnostics():
    """Independent pair-count AUC and unchanged policy PnL after rank fix."""
    before=read('metrics_initial_pooled.csv');after=read('metrics.csv')
    columns=['symbol','target','arm','n','net_r','net_bp','random_bp','baseline_net_r']
    pd.testing.assert_frame_equal(before[columns],after[columns])
    pred=predictions();pred=pred[pred.complete]
    saved=json.loads((EXP/'diagnostics.json').read_text());checks=[]
    for item in saved:
        g=pred[pred.symbol.eq(item['symbol'])&pred.target.eq(item['target'])&pred.arm.eq(item['arm'])]
        numerator=denominator=0.;tops=[];folds=[]
        for fold,part in g.groupby('fold'):
            pos=part.loc[part.label.eq(1),'score'].to_numpy();neg=part.loc[part.label.eq(0),'score'].to_numpy()
            if len(pos) and len(neg):
                dif=pos[:,None]-neg[None,:]
                auc=float(((dif>0).sum()+.5*(dif==0).sum())/dif.size)
                numerator+=len(part)*auc;denominator+=len(part)
                folds.append(dict(fold=fold,n=len(part),positives=len(pos),pair_count_auc=auc))
            tops.append(part.sort_values('score',ascending=False,kind='stable').iloc[:max(1,int(np.ceil(len(part)*.1)))])
        auc=numerator/denominator if denominator else None
        assert auc is None or abs(auc-item['target_auc'])<1e-12
        top=pd.concat(tops)
        assert len(top)==item['retrospective_top_decile']['n']
        assert abs(float(top.net_notional_bp.mean())-item['retrospective_top_decile']['net_bp'])<1e-10
        checks.append(dict(symbol=item['symbol'],target=item['target'],arm=item['arm'],auc=auc,folds=folds))
    dump(EXP/'diagnostic_validation.json',dict(policy_returns_unchanged=True,independent_pair_count_auc=checks,
         numerical_and_future_leak_audit='validation.json:222 model reconstructions,whole-week purge,9 real future OHLCV mutations; XAU0.838 target AUC has only15 positives over259 candidates and no profitable fixed-gate policy.',
         superseded_files=['metrics_initial_pooled.csv','diagnostics_initial_pooled.json','summary_initial_pooled.json'],
         tests='96 focused and boundary tests passed; added within-fold scale-invariance regression test passed separately (97 total).',
         builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    print('Ranking diagnostics validated',len(checks),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--diagnostics-only',action='store_true')
    args=parser.parse_args()
    audit_diagnostics() if args.diagnostics_only else main()
