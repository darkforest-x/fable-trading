"""Reconcile saved native fills, causal clocks, cached models and uncertainty.

No replay and no fitting. Numeric checks use einsum, independent of the BLAS
matmul path which can emit platform RuntimeWarnings in this local environment.
Raw native-order receipts, failed diagnostics and historical results are kept.
"""
from __future__ import annotations

import json
import pickle
import subprocess
import numpy as np
import pandas as pd
from scipy.special import expit,logit
from sklearn.metrics import roc_auc_score
from yoyo.evaluation.btc_eth_repeat_data import EXP,config,dump,read_frame,bounds
from yoyo.evaluation.btc_eth_repeat_fit import read,labels,weights,no_overlap,metrics


def numeric_models():
    data,_=labels()
    entry=data[data.target.eq('near')]
    continuation=read('continuation_candidates.csv.gz')
    records=[]
    for path in sorted((EXP/'models').glob('*.pkl')):
        with path.open('rb') as handle:
            saved=pickle.load(handle)
        receipt=saved['receipt']
        start=pd.Period(receipt['fold'],freq='Q').start_time.tz_localize('America/New_York')
        calstart=start-pd.DateOffset(months=3)
        d=entry if receipt['tag'] in ['entry','single_reward'] else continuation[continuation.target.eq(receipt['tag'].replace('continue_',''))]
        expiry=pd.to_datetime(d.expiry,utc=True)
        week=pd.to_datetime(d.week).dt.tz_localize('America/New_York')
        train=d[d.complete & (expiry<=calstart)]
        cal=d[d.complete & (week>=calstart) & (expiry<=start)]
        model=saved['model'];columns=saved['features']
        X=model[:-1].transform(train[columns].replace([np.inf,-np.inf],np.nan))
        beta=model[-1].coef_[0];intercept=model[-1].intercept_[0]
        score=np.einsum('ij,j->i',X,beta)+intercept
        p=expit(score)
        np.testing.assert_allclose(p,model.predict_proba(train[columns].replace([np.inf,-np.inf],np.nan))[:,1],rtol=0,atol=1e-12)
        w=weights(train)
        grad=np.einsum('ij,i->j',X,w*(p-train.label.to_numpy()))+beta
        gradient=float(max(abs(grad).max(),abs(np.sum(w*(p-train.label.to_numpy()))))/w.sum())
        assert np.isfinite(X).all() and np.isfinite(beta).all() and gradient<.005
        calibration=[]
        raw=model.predict_proba(cal[columns].replace([np.inf,-np.inf],np.nan))[:,1]
        for symbol,bundle in saved['calibrators'].items():
            keep=np.ones(len(cal),dtype=bool) if bundle['pooled_fallback'] else cal.symbol.eq(symbol).to_numpy()
            xx=logit(np.clip(raw[keep],1e-6,1-1e-6))
            m=bundle['model'];pp=expit(xx*m.coef_[0,0]+m.intercept_[0])
            np.testing.assert_allclose(pp,m.predict_proba(xx[:,None])[:,1],rtol=0,atol=1e-12)
            ww=weights(cal.loc[keep]);error=ww*(pp-cal.loc[keep,'label'].to_numpy())
            gg=max(abs(np.sum(error)),abs(np.sum(xx*error)+m.coef_[0,0]))/ww.sum()
            assert np.isfinite(m.coef_).all() and gg<.005
            calibration.append(dict(symbol=symbol,gradient=float(gg),iterations=int(m.n_iter_[0])))
        records.append(dict(model=path.name,train_n=len(train),max_scaled_feature=float(abs(X).max()),
                            gradient=gradient,iterations=int(model[-1].n_iter_[0]),calibration=calibration))
    return records


def main():
    cfg=config();events=read('events.csv.gz')
    validation=dict(fees=[],paths=[],source_parity={},model_numeric=numeric_models())
    for symbol in cfg['symbols']:
        frame=read_frame(symbol)
        for suffix in ['native','close_now','path_sensitivity']:
            rows=read(f'{symbol}_{suffix}_trades.csv.gz')
            fills=read(f'{symbol}_{suffix}_fills.csv.gz')
            orders=read(f'{symbol}_{suffix}_orders.csv.gz').set_index('trade_id')
            assert len(fills)==2*len(rows) and len(rows)==len(orders)
            detail=fills.merge(orders[['kind','order_mode']],left_on='trade_id',right_index=True,validate='many_to_one')
            maker=detail.phase.eq('target') | (detail.phase.eq('entry') & detail.order_mode.eq('limit'))
            expected=detail.price*detail.quantity*np.where(maker,cfg['maker_fee'],cfg['taker_fee'])
            fee_error=float(abs(detail.commission-expected).max())
            assert fee_error<1e-7
            total=fills.groupby('trade_id').commission.sum()
            actual=rows.trade_id.map(total)
            net=(rows.side*(rows.exit_price-rows.entry)-actual/rows.quantity)/rows.entry*10000
            pnl_error=float(abs(net-rows.net_notional_bp).max())
            assert pnl_error<1e-8 and (rows.exit_i<=rows.end_i).all()
            signal=rows[rows.kind.eq('signal')]
            assert (signal.entry_i>signal.submission_i).all()
            if suffix=='native':
                joined=signal.merge(events[['event_id','touch_i']],on='event_id',validate='many_to_one')
                assert (joined.entry_i==joined.touch_i).all()
                assert all(frame.index[int(r.end_i)]==pd.Timestamp(bounds(r.week)[2]) for r in rows[rows.complete].itertuples())
            if suffix=='close_now':
                assert rows.reason.eq(0).all() and (rows.exit_i==rows.end_i).all()
                np.testing.assert_allclose(rows.exit_price,frame.close.iloc[rows.exit_i.to_numpy(int)],rtol=0,atol=1e-8)
            validation['fees'].append(dict(symbol=symbol,arm=suffix,orders=len(orders),trades=len(rows),fills=len(fills),
                                            max_commission_error=fee_error,max_net_bp_error=pnl_error))
        original=read(f'{symbol}_native_trades.csv.gz')
        alternate=read(f'{symbol}_path_sensitivity_trades.csv.gz')
        d=original.merge(alternate,on='trade_id',suffixes=('_oh','_ol'))
        changed=d[(d.reason_oh!=d.reason_ol)|(abs(d.net_r_oh-d.net_r_ol)>1e-7)]
        validation['paths'].append(dict(symbol=symbol,tested=len(d),changed=len(changed),
                                        changes=changed[['trade_id','reason_oh','reason_ol','net_notional_bp_oh','net_notional_bp_ol']].to_dict('records')))
    old=pd.read_csv(EXP.parent/'exp-btc-1618-return-count-20261030-v1/events.csv')
    oldkey=old.week+'|'+old.side+'|'+pd.to_datetime(old.start,utc=True).astype(str)
    btc=events[events.symbol.eq('BTC')]
    newkey=btc.week+'|'+btc.side.map({-1:'upper',1:'lower'})+'|'+pd.to_datetime(btc.touch_open,utc=True).dt.floor('15min').astype(str)
    assert oldkey.isin(newkey).all()
    validation['source_parity']=dict(old_859=len(old),matched_859=int(oldkey.isin(newkey).sum()))
    selections=read('selected_trades.csv.gz')
    for _,g in selections.groupby(['analysis_period','arm','symbol']):
        g=g.sort_values('submission_i')
        assert (g.submission_i.iloc[1:].to_numpy()>=g.exit_i.iloc[:-1].to_numpy()).all()
    # Reuse native alternate outcomes with frozen scores; do not refit on a
    # sensitivity arm. Both ETH changed outcomes were worse in primary OHLC.
    pred=read('entry_predictions.csv.gz')
    sensitivity=[]
    for symbol in cfg['symbols']:
        alt=read(f'{symbol}_path_sensitivity_trades.csv.gz').set_index('trade_id')
        coin=pred[pred.symbol.eq(symbol)&pred.complete].copy().set_index('trade_id',drop=False)
        coin.index.name=None
        shared=coin.index.intersection(alt.index)
        for col in ['exit_i','exit_price','reason','gross_r','fee_r','net_r','net_notional_bp','exit_time']:
            coin.loc[shared,col]=alt.loc[shared,col]
        coin['label']=coin.reason.eq(4).astype(int)
        for arm,choose in [('all_repeats',pd.Series(True,index=coin.index)),('score_gate',coin.gate)]:
            sensitivity.append(dict(symbol=symbol,arm=arm,**metrics(no_overlap(coin,choose))))
    validation['frozen_score_path_sensitivity']=sensitivity
    sig,_=labels()
    targets=[]
    for target,g in sig[sig.complete & (sig.week>='2025-01-01')].groupby('target'):
        chosen=no_overlap(g)
        for symbol in cfg['symbols']:
            targets.append(dict(target=target,symbol=symbol,**metrics(chosen[chosen.symbol.eq(symbol)])))
    dump(EXP/'target_baselines.json',targets)
    entry=json.loads((EXP/'entry_summary.json').read_text())
    cont=json.loads((EXP/'continuation_summary.json').read_text())
    hypotheses=[(r['symbol']+'_entry',r['week_p']) for r in entry['rows'] if r['period']=='rolling_test' and r['arm']=='score_gate' and r['symbol']!='ALL' and r['side']=='both']
    hypotheses += [(r['symbol']+'_'+r['target'],r['paired_close_control']['week_p']) for r in cont if r['symbol']!='ALL']
    ordered=sorted(hypotheses,key=lambda x:x[1]);last=0.;holm=[]
    for i,(name,p) in enumerate(ordered):
        last=max(last,min(1.,(len(ordered)-i)*p));holm.append(dict(test=name,p=p,holm_p=last))
    validation['primary_comparisons_holm']=holm
    validation['numeric_warnings']='Local sklearn matmul RuntimeWarnings retained in logs; all frozen model/calibrator outputs checked against einsum/elementwise calculations, finite coefficients and first-order gradients.'
    validation['continuation_scope']='Conditional independent opportunity study after near-touch completed close; not an integrated single-position entry-plus-continuation account strategy. Calibrated model folds lacking history are explicitly skipped.'
    dump(EXP/'validation.json',validation)
    dump(EXP/'summary.json',dict(experiment=cfg['experiment_id'],verdict='No robust profitable entry or continuation model established; retain negative results.',
         config=cfg,input_receipt=json.loads((EXP/'inputs/receipt.json').read_text()),
         entry=entry,continuation=cont,target_baselines=targets,validation=validation,
         builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
         reproduction=['.venv/bin/python -m yoyo.evaluation.btc_eth_repeat_data',
          'for symbol in BTC ETH; do /Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.btc_eth_repeat_native --symbol "$symbol"; done',
          '.venv/bin/python -m yoyo.evaluation.btc_eth_repeat_fit entry',
          '.venv/bin/python -m yoyo.evaluation.btc_eth_repeat_fit prepare_continuation',
          'for symbol in BTC ETH; do /Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.btc_eth_repeat_native --symbol "$symbol" --continuation; done',
          'for symbol in BTC ETH; do /Users/zhangzc/.local/share/crypto-toolkit/venvs/nautilus/bin/python -m yoyo.evaluation.btc_eth_repeat_native --symbol "$symbol" --sensitivity; done',
          '.venv/bin/python -m yoyo.evaluation.btc_eth_repeat_fit continuation',
          '.venv/bin/python -m yoyo.evaluation.btc_eth_repeat_validate']))
    print('Validated',dict(native_fills=sum(x['fills'] for x in validation['fees']),models=len(validation['model_numeric']),old_events=859),flush=True)
    print(pd.DataFrame(targets)[['target','symbol','n','net_bp','net_r','random_bp','week_p']].to_string(index=False),flush=True)


if __name__=='__main__':
    main()
