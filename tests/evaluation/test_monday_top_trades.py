"""Protect return-basis and distinct-entry semantics in the review selector."""
import pandas as pd
from yoyo.evaluation.monday_top_trades import normalize, retain_best, ROOT


def test_return_ranking_deduplicates_entries_without_substituting_r():
    f=pd.DataFrame([
        dict(entry_key='a',net_bp=100,net_r=9,exit_interval_end='2020-01-02',source_key='x',config_id='n1',trade_id='a'),
        dict(entry_key='a',net_bp=150,net_r=1,exit_interval_end='2020-01-03',source_key='x',config_id='n2',trade_id='a'),
        dict(entry_key='b',net_bp=200,net_r=.5,exit_interval_end='2020-01-02',source_key='x',config_id='n1',trade_id='b')])
    result=retain_best(f,'net_bp')
    assert result.entry_key.tolist()==['b','a']
    assert result.config_id.tolist()==['n1','n2']


def test_timed_hindsight_bounds_and_random_rows_never_enter_ranking():
    f=pd.DataFrame([dict(symbol='BTCUSDT',population=p,scenario=s,parent_id='p',horizon=5,
        entry=100.,side=1,entry_time='2020-01-01 00:00:00+00:00',exit_time='2020-01-01 00:05:00+00:00',
        entry_interval_end='2020-01-01 00:05:00+00:00',exit_interval_end='2020-01-01 00:10:00+00:00',
        exit_price=102.,gross_bp=200.,net_bp=193.) for p,s in [('signal','grid'),('signal','upper'),('random','grid')]])
    result=normalize(f,'timed',ROOT/'example.csv',100)
    assert len(result)==1 and result.source_csv_record.iloc[0]==101
    assert result.net_pct.iloc[0]==1.93


def test_streaming_topk_equals_full_pool_after_cross_chunk_duplicates():
    rows=[]
    for i in range(30):
        rows.append(dict(entry_key=str(i%11),net_bp=i,exit_interval_end='2020-01-02',
            source_key='x',config_id='x',trade_id=str(i)))
    f=pd.DataFrame(rows);current=pd.DataFrame()
    for start in range(0,30,7):current=retain_best(pd.concat([current,f.iloc[start:start+7]],ignore_index=True),'net_bp',5)
    full=retain_best(f,'net_bp',5)
    assert current.trade_id.tolist()==full.trade_id.tolist()
