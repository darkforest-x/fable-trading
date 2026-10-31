"""Validate unchanged ETH inputs,48new models,48old models and cached outcomes.

No model refit or native replay. Feature causality inherits the completed
source audit, since this study only selects existing columns. Reconstruct
predictions, label clocks, train IDs, weights, admission, control selections
and fixed-policy native alternative paths. No pooled cross-fold score rank.
"""
import json
import pickle
import platform
import subprocess
import numpy as np
import pandas as pd
from yoyo.evaluation.eth_btc_ablation import EXP,SOURCE,ORIGINAL,ROOT,config,read,dump,model_path,feature_columns
from yoyo.evaluation.eth_1618_feature_models import training_rows,training_weights
from yoyo.evaluation.btc_eth_repeat_fit import metrics
from yoyo.evaluation.eth_btc_ablation_evaluate import top_rows


def main():
    cfg=config();source=read('data.csv.gz',SOURCE);pred=read('predictions.csv.gz');selected=read('selected_trades.csv.gz')
    oldcfg=json.loads((SOURCE/'config.json').read_text());checks=[]
    for (target,fold,arm),part in pred.groupby(['target','fold','arm']):
        start=pd.Period(fold,freq='Q').start_time.tz_localize('America/New_York')
        train=training_rows(source[source.target.eq(target)],start,'original_model')
        with model_path(arm,target,fold).open('rb') as h:bundle=pickle.load(h)
        assert set(train.event_id)==set(bundle['record']['train_event_ids'])
        assert not set(train.week)&set(part.week)
        assert pd.to_datetime(train.expiry_dt,utc=True).max()<=start
        w=training_weights(train,start,'original_model')
        if arm in cfg['arms']:
            assert bundle['record']['params']==oldcfg['lightgbm']
            assert abs(w.sum()-bundle['record']['weight_sum'])<1e-9
            with model_path('original_model',target,fold).open('rb') as h:base=pickle.load(h)
            assert bundle['features']==feature_columns(base['features'],arm)
        # Outcome poisoning cannot change training data or weights.
        damaged=source[source.target.eq(target)].copy()
        damaged.loc[pd.to_datetime(damaged.expiry_dt,utc=True)>start,'net_r']=1e12
        pd.testing.assert_frame_equal(train,training_rows(damaged,start,'original_model'))
        x=part[bundle['features']].replace([np.inf,-np.inf],np.nan)
        scores=bundle['model'].booster_.predict(x)
        error=float(abs(scores-part.score).max());assert error<1e-12
        assert np.array_equal(scores>0,part.admit)
        checks.append(dict(target=target,fold=fold,arm=arm,score_error=error,train_n=len(train),train_weeks=int(train.week.nunique())))
    oldpred=read('predictions.csv.gz',SOURCE)
    reuse_checks=[]
    for oldarm,newarm in [('original_model','original_model'),('add_btc_context','full_context')]:
        a=oldpred[oldpred.arm.eq(oldarm)].set_index('trade_id')
        b=pred[pred.arm.eq(newarm)].set_index('trade_id').loc[a.index]
        np.testing.assert_allclose(a.score,b.score,rtol=0,atol=0)
        reuse_checks.append(dict(arm=newarm,rows=len(b),score_exact=True))
    original_selected=read('selected_trades.csv.gz',SOURCE)
    for oldarm,newarm in [('original_model','original_model'),('add_btc_context','full_context'),('unscored','unscored'),('single_reward','single_reward')]:
        a=original_selected[original_selected.arm.eq(oldarm)]
        b=selected[selected.arm.eq(newarm)]
        assert set(a.trade_id)==set(b.trade_id)
        assert abs(a.net_r.sum()-b.net_r.sum())<1e-9
    native_dir=ROOT/'experiments/active/exp-btc-eth-repeat-1618-20261031-v1'
    native=read('ETH_native_trades.csv.gz',native_dir).set_index('trade_id')
    assert selected.complete.all() and not selected.week.eq('2026-10-26').any()
    for (target,arm),g in selected.groupby(['target','arm']):
        assert g.trade_id.is_unique
        ordered=g.sort_values(['submission_i','trade_id'])
        assert (ordered.submission_i.iloc[1:].to_numpy()>=ordered.exit_i.iloc[:-1].to_numpy()).all()
        original=native.loc[g.trade_id]
        for col in ['entry_i','exit_i','entry','exit_price','risk','fee_r','net_r']:
            np.testing.assert_allclose(g[col],original[col],rtol=0,atol=1e-12)
        np.testing.assert_allclose(g.vbt_net_r,original.net_r,rtol=0,atol=1e-8)
        if arm in cfg['arms']:
            score=pred[pred.target.eq(target)&pred.arm.eq(arm)].set_index('trade_id').loc[g.trade_id]
            assert score.admit.all()
            np.testing.assert_allclose(score.score,g.score,rtol=0,atol=1e-12)
    previous_diag=json.loads((SOURCE/'diagnostics.json').read_text())
    tail_parity=[]
    for oldarm,newarm in [('original_model','original_model'),('add_btc_context','full_context')]:
        previous=next(r for r in previous_diag if r['target']=='near' and r['arm']==oldarm)['diagnostics']['retrospective_top_decile']
        g=pred[pred.complete&pred.target.eq('near')&pred.arm.eq(newarm)]
        top=top_rows(g);actual=metrics(top)
        for col in ['n','net_r','net_bp','random_bp']:
            assert abs(actual[col]-previous[col])<1e-10
        tail_parity.append(dict(arm=newarm,n=len(top),net_r=actual['net_r'],mean_net_bp=actual['net_bp']))
    alt=read('ETH_path_sensitivity_trades.csv.gz',native_dir).set_index('trade_id')
    assert (native.loc[alt.index].exit_i==alt.exit_i).all()
    paths=[]
    for (target,arm),g in selected.groupby(['target','arm']):
        alternative=g.copy().set_index('trade_id',drop=False);alternative.index.name=None
        ids=alternative.index.intersection(alt.index);cols=['net_r','net_notional_bp','gross_r','fee_r','reason']
        alternative.loc[ids,cols]=alt.loc[ids,cols];alternative['label']=alternative.reason.eq(4).astype(int)
        paths.append(dict(target=target,arm=arm,primary_net_r=float(g.net_r.sum()),alternate=metrics(alternative)))
    inherited=json.loads((SOURCE/'validation.json').read_text())['causality']
    assert len(inherited)==8 and all(r['future_mutated_equal'] and r['truncated_equal'] for r in inherited)
    dump(EXP/'validation.json',dict(model_checks=checks,control_prediction_parity=reuse_checks,
        control_selection_parity=True,source_feature_causality_reused=inherited,nonoverlap=True,
        native_fields_exact=True,tail_parity=tail_parity,frozen_policy_path_sensitivity=paths,
        new_native_positions=0,OHLC_replays=0,builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    import lightgbm,sklearn
    dump(EXP/'reproduction.json',dict(runtime=dict(python=platform.python_version(),numpy=np.__version__,pandas=pd.__version__,
        lightgbm=lightgbm.__version__,sklearn=sklearn.__version__,vectorbt='1.2.0 isolated',shap='0.52.0 isolated Python3.12'),
        commands=['.venv/bin/python -m yoyo.evaluation.eth_btc_ablation fit',
          '/Users/zhangzc/.local/bin/vectorbt-python -m yoyo.evaluation.eth_btc_ablation portfolios',
          '.venv/bin/python -m yoyo.evaluation.eth_btc_ablation_evaluate',
          '.venv/bin/python -m yoyo.evaluation.eth_btc_tail_explain export',
          '/Users/zhangzc/.local/share/fable-trading/venvs/shap-052/bin/python -m yoyo.evaluation.eth_btc_tail_explain verify',
          '.venv/bin/python -m yoyo.evaluation.eth_btc_tail_explain summarize',
          '.venv/bin/python -m yoyo.evaluation.eth_btc_ablation_validate'],
        source_data=str(SOURCE/'data.csv.gz'),native_source=str(native_dir),original_models=str(ORIGINAL/'models/ETH'),
        full_context_models=str(SOURCE/'models'),local_models='models/target/fold/arm.pkl',local_explanations='shap/fold/arm/*_explanation.pkl'))
    print(dict(models=len(checks),score_max_error=max(r['score_error'] for r in checks),selected_rows=len(selected),tail_parity=tail_parity),flush=True)


if __name__=='__main__':main()
