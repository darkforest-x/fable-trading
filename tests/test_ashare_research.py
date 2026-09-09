"""Temporal-selection and diagnostic contracts, without network or market fixtures."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.ashare_research import read_through, winner, ranking_metrics, monthly_excess_test, load_frames, buy_hold


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


def test_excluded_source_is_never_parsed_even_when_csv_exists(tmp_path):
    import json
    (tmp_path/'daily').mkdir()
    (tmp_path/'universe.json').write_text(json.dumps({'codes':['sh.600001','sh.600002']}))
    (tmp_path/'exclusions.json').write_text(json.dumps({'codes':{'sh.600002':'unreconciled corporate action'}}))
    (tmp_path/'daily'/'sh.600001.csv').write_text('date,code,close,isST,tradestatus\n2021-12-31,sh.600001,10,0,1\n')
    (tmp_path/'daily'/'sh.600002.csv').write_text('invalid source must not be parsed')
    frames, missing=load_frames(tmp_path,'2021-12-31')
    assert list(frames)==['sh.600001']
    assert missing==['sh.600002']


def test_missing_frozen_universe_slots_stay_cash_in_benchmark():
    from yoyo.evaluation.ashare_imacd import Costs
    class ZeroCosts(Costs):
        def fee(self,*args):
            return 0.
    costs=ZeroCosts(commission=0,slippage=0)
    raw={'sh.600001':pd.DataFrame(dict(date=['2024-01-02','2024-01-03'],
        open=[10.,20.],close=[10.,20.],raw_open=[10.,20.],raw_preclose=[10.,10.],
        tradestatus=['1','1'],volume=[10000,10000],isST=['0','0'],board=['main_sh','main_sh']))}
    full,_=buy_hold(raw,'2024-01-02','2024-01-03',costs,universe_size=1)
    half,_=buy_hold(raw,'2024-01-02','2024-01-03',costs,universe_size=2)
    # One unavailable stock's original half is not redistributed to the survivor.
    assert half['net_return']==pytest.approx(.499)
    assert half['net_return'] < full['net_return']*.501
