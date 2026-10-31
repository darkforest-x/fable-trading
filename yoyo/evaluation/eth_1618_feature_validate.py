"""Causality, frozen-model reconstruction and native-accounting evidence.

Reuse predictions and fills; no retraining or OHLC replay. Real-history
truncation and future mutation verify new feature clocks. Fixed-policy
alternate native paths are sensitivity only, not another model search.
"""
import json
import pickle
import platform
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_1618_features import EXP, PRIOR, ROOT, config, read, dump, event_features
from yoyo.evaluation.eth_1618_feature_models import training_rows, training_weights
from yoyo.evaluation.btc_eth_repeat_data import read_frame
from yoyo.evaluation.btc_eth_repeat_fit import metrics


def main():
    cfg=config(); data=read('data.csv.gz'); pred=read('predictions.csv.gz'); selected=read('selected_trades.csv.gz')
    receipts=json.loads((EXP/'model_receipts.json').read_text()); checks=[]
    for r in receipts:
        start=pd.Period(r['fold'],freq='Q').start_time.tz_localize('America/New_York')
        train=training_rows(data[data.target.eq(r['target'])],start,r['arm'])
        part=pred[pred.target.eq(r['target'])&pred.fold.eq(r['fold'])&pred.arm.eq(r['arm'])]
        with (EXP/'models'/r['target']/r['fold']/f"{r['arm']}.pkl").open('rb') as h: bundle=pickle.load(h)
        assert train.event_id.tolist()==bundle['record']['train_event_ids']
        assert not set(train.week)&set(part.week)
        assert pd.to_datetime(train.expiry_dt,utc=True).max()<=start
        weights=training_weights(train,start,r['arm'])
        assert abs(weights.sum()-r['weight_sum'])<1e-9
        # Future outcomes cannot affect the actual training subset or weights.
        poisoned=data[data.target.eq(r['target'])].copy()
        future=pd.to_datetime(poisoned.expiry_dt,utc=True)>start
        poisoned.loc[future,'net_r']=1e12
        pd.testing.assert_frame_equal(train,training_rows(poisoned,start,r['arm']))
        scores=bundle['model'].booster_.predict(part[bundle['features']].replace([np.inf,-np.inf],np.nan))
        err=float(np.max(abs(scores-part.score)))
        assert err<1e-12 and np.array_equal(scores>0,part.admit)
        checks.append(dict(target=r['target'],fold=r['fold'],arm=r['arm'],score_max_error=err,
                           train_n=len(train),train_weeks=int(train.week.nunique())))
    eth,btc=read_frame('ETH'),read_frame('BTC')
    events=data[data.target.eq('near')].drop_duplicates('event_id').sort_values('submission_i')
    sample=events.iloc[np.linspace(0,len(events)-1,8,dtype=int)]
    causality=[]
    for _,r in sample.iterrows():
        e=events[events.event_id.eq(r.event_id)]; i=int(r.submission_i)
        expected=e[cfg['feature_groups']['add_regime']+cfg['feature_groups']['add_approach']+cfg['feature_groups']['add_btc_context']]
        a,b=eth.iloc[:i+1],btc.iloc[:i+1]
        truncated=event_features(e,a,b)
        np.testing.assert_allclose(expected,truncated,rtol=0,atol=1e-12,equal_nan=True)
        a,b=eth.copy(),btc.copy(); a.iloc[i+1:]*=7; b.iloc[i+1:]*=.13
        mutated=event_features(e,a,b)
        np.testing.assert_allclose(expected,mutated,rtol=0,atol=1e-12,equal_nan=True)
        causality.append(dict(event_id=r.event_id,submission_time=r.submission_time,
                             truncated_equal=True,future_mutated_equal=True))
    native_dir=ROOT/'experiments/active/exp-btc-eth-repeat-1618-20261031-v1'
    native=pd.read_csv(native_dir/'ETH_native_trades.csv.gz',float_precision='round_trip').set_index('trade_id')
    assert selected.complete.all() and not selected.week.eq('2026-10-26').any()
    for (target,arm),g in selected.groupby(['target','arm']):
        assert g.trade_id.is_unique
        ordered=g.sort_values(['submission_i','trade_id'])
        assert (ordered.submission_i.iloc[1:].to_numpy()>=ordered.exit_i.iloc[:-1].to_numpy()).all()
        ref=native.loc[g.trade_id]
        for col in ['entry','entry_i','exit_price','exit_i','risk','fee_r','net_r']:
            np.testing.assert_allclose(g[col],ref[col],rtol=0,atol=1e-12)
        np.testing.assert_allclose(g.vbt_net_r,ref.net_r,rtol=0,atol=1e-8)
        if arm in cfg['arms']:
            pp=pred[pred.target.eq(target)&pred.arm.eq(arm)].set_index('trade_id').loc[g.trade_id]
            assert pp.admit.all()
            np.testing.assert_allclose(g.score,pp.score,rtol=0,atol=1e-12)
    previous=pd.read_csv(PRIOR/'selected_trades.csv.gz',float_precision='round_trip')
    parity=[]
    for oldname,newname in [('fresh_tree_net_noordinal','original_model'),('single_reward','single_reward')]:
        for target in cfg['targets']:
            a=previous[previous.symbol.eq('ETH')&previous.target.eq(target)&previous.arm.eq(oldname)]
            b=selected[selected.target.eq(target)&selected.arm.eq(newname)]
            assert set(a.trade_id)==set(b.trade_id)
            assert abs(a.net_r.sum()-b.net_r.sum())<1e-9
            parity.append(dict(arm=newname,target=target,n=len(b),net_r=float(b.net_r.sum())))
    oldmetrics=pd.read_csv(PRIOR/'metrics.csv')
    for target in cfg['targets']:
        old=oldmetrics[oldmetrics.symbol.eq('ETH')&oldmetrics.target.eq(target)&oldmetrics.arm.eq('fresh_tree_net_noordinal')].iloc[0]
        unscored=selected[selected.target.eq(target)&selected.arm.eq('unscored')]
        assert len(unscored)==int(old.baseline_n)
        assert abs(unscored.net_r.sum()-old.baseline_net_r)<1e-8
        parity.append(dict(arm='unscored',target=target,n=len(unscored),net_r=float(unscored.net_r.sum())))
    alt=pd.read_csv(native_dir/'ETH_path_sensitivity_trades.csv.gz',float_precision='round_trip').set_index('trade_id')
    assert (native.loc[alt.index].exit_i==alt.exit_i).all()
    paths=[]
    for (target,arm),g in selected.groupby(['target','arm']):
        other=g.copy().set_index('trade_id',drop=False);other.index.name=None
        ids=other.index.intersection(alt.index)
        cols=['net_r','net_notional_bp','gross_r','fee_r','reason']
        other.loc[ids,cols]=alt.loc[ids,cols];other['label']=other.reason.eq(4).astype(int)
        paths.append(dict(target=target,arm=arm,primary_net_r=float(g.net_r.sum()),alternate=metrics(other)))
    dump(EXP/'validation.json',dict(model_checks=checks,causality=causality,baseline_parity=parity,
        nonoverlap=True,original_native_fields_match=True,frozen_policy_path_sensitivity=paths,
        new_native_positions=0,OHLC_replays=0,source_future_mutation='Future labels poisoned for every training selection; eight real event feature windows truncated and future candles mutated.',
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    import lightgbm,sklearn
    dump(EXP/'reproduction.json',dict(runtime=dict(python=platform.python_version(),numpy=np.__version__,
        pandas=pd.__version__,lightgbm=lightgbm.__version__,sklearn=sklearn.__version__,vectorbt='1.2.0 isolated'),
        commands=['.venv/bin/python -m yoyo.evaluation.eth_1618_features',
                  '.venv/bin/python -m yoyo.evaluation.eth_1618_feature_models',
                  '/Users/zhangzc/.local/bin/vectorbt-python -m yoyo.evaluation.eth_1618_feature_portfolios',
                  '.venv/bin/python -m yoyo.evaluation.eth_1618_feature_evaluate',
                  '.venv/bin/python -m yoyo.evaluation.eth_1618_feature_validate'],
        source_native=str(native_dir),baseline=str(PRIOR),model_artifacts='Local models/target/fold/arm.pkl; no weights committed to git'))
    print(dict(models=len(checks),max_score_error=max(r['score_max_error'] for r in checks),
               causal_events=len(causality),selected_rows=len(selected),baseline_parity=parity),flush=True)


if __name__ == '__main__':
    main()
