"""The feature group distinction is exact and preserves the original inputs."""
from yoyo.evaluation.eth_btc_ablation import config,feature_columns,model_path
from yoyo.evaluation.eth_btc_ablation_evaluate import top_rows
import pandas as pd


def test_ablation_changes_only_the_declared_two_input_columns():
    base=['f_'+str(i) for i in range(20)]
    state=feature_columns(base,'state_only');returns=feature_columns(base,'return_residual_only')
    assert state[:20]==returns[:20]==base
    assert state[20:]==['g_beta_7d','g_correlation_7d']
    assert returns[20:]==['g_btc_return_4h','g_eth_residual_4h']
    assert set(state[20:]).isdisjoint(returns[20:])
    assert config()['stop_fraction']==.007 and config()['maker_fee']==.0002
    assert model_path('full_context','near','2026Q4')!=model_path('state_only','near','2026Q4')


def test_high_score_cohort_never_compares_different_quarter_score_scales():
    data=pd.DataFrame(dict(fold=['2025Q1']*21+['2025Q2']*10,score=list(range(21))+list(range(100,110))))
    top=top_rows(data)
    assert top.groupby('fold').size().to_dict()=={'2025Q1':3,'2025Q2':1}
    assert set(top.score)=={18,19,20,109}
