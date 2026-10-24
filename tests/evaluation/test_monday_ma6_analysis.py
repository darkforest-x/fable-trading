"""Analysis boundaries must not import post-cutoff outcomes into earlier cohorts."""
import pandas as pd
from yoyo.evaluation.monday_ma6_analysis import attempted_pairs,common_exit_cohort,annotate_split_prices,POLICIES


def test_split_exit_price_is_two_leg_mean_without_a_fake_single_target():
    t=pd.DataFrame(dict(parent_id=['x']*3,policy=POLICIES,
        target_price=[110.,120.,120.],exit_price=[110.,98.,98.],return_r=[5.,-1.,2.]))
    out=annotate_split_prices(t);h=out[out.policy.eq('half_each')].iloc[0]
    assert h.exit_price==104.
    assert pd.isna(h.target_price)
    assert h.last_leg_exit_price==98.
    assert out.return_r.equals(t.return_r)


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


def test_exit_comparison_drops_all_siblings_of_an_unresolved_far_leg():
    rows=[]
    for parent in ['resolved','unresolved']:
        for mode in POLICIES:
            rows.append(dict(parent_id=parent,policy=mode,population='signal',asset_group='BTC',
                period='later',cross_cut=False,closed=parent=='resolved' or mode=='monday_full',
                return_r={'monday_full':1.,'opposite_1618_full':3.,'half_each':2.}[mode],
                return_pct=.5,fee_r=.03,holding_hours=5.))
    result=common_exit_cohort(pd.DataFrame(rows),'BTC','all')
    assert all(r['common_closed_n']==1 for r in result.values())
    assert result['half_each']['common_mean_r']==2.
