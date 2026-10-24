"""Analysis boundaries must not import post-cutoff outcomes into earlier cohorts."""
import pandas as pd
from yoyo.evaluation.monday_ma6_analysis import attempted_pairs


def test_submission_before_cutoff_and_fill_after_is_not_earlier_expectancy():
    ids=['s','c0','c1','c2']
    orders=pd.DataFrame(dict(trade_id=ids,asset_group=['ALTCOINS']*4,
        submitted_at=['2022-12-31 23:55:00+00:00']*4,
        population=['signal','random','random','random'],pair_id=['pair']*4,status=['filled']*4))
    trades=pd.DataFrame(dict(parent_id=ids,policy=['monday_full']*4,return_r=[3.,1.,1.,1.],
        submitted_at=pd.to_datetime(['2022-12-31 23:55:00+00:00']*4,utc=True),
        exit_interval_end=pd.to_datetime(['2023-01-01 01:00:00+00:00']*4,utc=True),
        closed=[True]*4,cross_cut=[False]*4))
    earlier=attempted_pairs(orders,trades,'monday_full','ALTCOINS','earlier')
    all_=attempted_pairs(orders,trades,'monday_full','ALTCOINS','all')
    assert earlier['complete_opportunity_pairs']==0
    assert all_['complete_opportunity_pairs']==1
    assert all_['attempt_paired_excess_r']==2
