"""Temporal-selection and diagnostic contracts, without network or market fixtures."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.ashare_research import read_through, winner, ranking_metrics, monthly_excess_test


def test_selection_reader_stops_before_future_outcome_rows(tmp_path):
    path=tmp_path/'prices.csv'
    path.write_text('date,code,close,isST,tradestatus\n2021-12-31,sh.600000,10,0,1\n2024-01-01,DO NOT PARSE,not-a-price,broken,broken\n')
    frame=read_through(path,'2021-12-31')
    assert frame.date.tolist()==['2021-12-31']
    assert frame.close.tolist()==[10]


def test_selection_requires_natural_trade_count_and_stable_tiebreak():
    rows=[dict(label='too_few',trades=29,net_return=1,max_drawdown=0),
          dict(label='first',trades=30,net_return=.1,max_drawdown=.2),
          dict(label='second',trades=40,net_return=.1,max_drawdown=.1),
          dict(label='same',trades=40,net_return=.1,max_drawdown=.1)]
    assert winner(rows)['label']=='second'
    with pytest.raises(ValueError,match='30 natural'):
        winner(rows[:1])


def test_fixed_ranking_score_is_not_selected_from_future_returns():
    f=pd.DataFrame(dict(reason=['initial_stop']*100,score=np.arange(100),
                        return_net=-np.arange(100)/1000,return_gross=-np.arange(100)/1000+.002))
    result=ranking_metrics(f)
    assert result['top_decile_net']==pytest.approx(-.0945)
    assert result['all_trades_net']==pytest.approx(-.0495)
    assert result['permutation_p']>.9


def test_zero_monthly_excess_does_not_claim_significance():
    f=pd.DataFrame({'date':pd.date_range('2024-01-01','2025-12-31',freq='B').strftime('%Y-%m-%d')})
    f['equity']=np.linspace(1_000_000,1_100_000,len(f))
    result=monthly_excess_test(f,[f.copy(),f.copy()])
    assert result['months']==24
    assert result['mean_monthly_excess']==0
    assert result['p_one_sided']==1
