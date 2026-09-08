"""V3 gate/coverage/clock tampering on the existing synthetic ledger fixture."""
import numpy as np
import pandas as pd
import pytest
from test_imacd_formation_verify import ledgers as base_ledgers, alter
from yoyo.evaluation.imacd_launch_verify import verify_saved


@pytest.fixture
def ledgers(tmp_path):
    data,results=base_ledgers.__wrapped__(tmp_path)
    names={'P02':'S01','Q01':'H00','Q02':'H01'}
    for path in [*data.glob('*.csv.gz'),*results.glob('*.csv')]:
        f=pd.read_csv(path).rename(columns=names)
        if 'policy' in f:f['policy']=f.policy.replace(names)
        if path.name=='events.csv.gz':
            f['box_breakout']=f.S01
            f['prior_box_high']=101.;f['prior_box_low']=99.
            f['signal_close_price']=np.where(f.S01,100+f.side*2,100)
            f['breakout_score']=np.where(f.S01,1.,-1.)
            f['htf_known']=f.H00
            f['htf_index']=np.where(f.H00,340,-1)
            f['htf_md']=np.where(f.H00,np.where(f.H01,f.side,-f.side),np.nan)
            f['htf_atr']=np.where(f.H00,1.,np.nan)
            h=f.minutes.map({15:60,60:240,240:1440})*60000
            stamps=pd.to_datetime(f.signal_open_time,utc=True).astype('int64')//1000000
            f['htf_close_ms']=np.where(f.H00,(stamps//h)*h,np.nan)
            f['htf_score']=f.side*f.htf_md/f.htf_atr
        if path.name=='summary.csv':
            f['incremental_p_holm_validation']=np.where(
                f.fold.eq('validation')&f.policy.isin(['S01','H01']),
                f.minutes.map({15:.06,60:.08,240:.08}),np.nan)
        f.to_csv(path,index=False)
    return data,results


def test_valid_independent_gate_ledgers_pass(ledgers):
    a=verify_saved(*ledgers)
    assert a['passed'],a['failed_checks']


@pytest.mark.parametrize('column,value,check',[
    ('S01',False,'s01_is_strict_prior12_box_break'),
    ('H00',False,'h00_is_known_coverage_only'),
    ('H01',False,'h01_is_known_same_md_direction'),
    ('htf_index',339,'known_higher_has_340_bar_independent_warmup'),
    ('htf_close_ms',9999999999999.,'known_higher_is_expected_close_at_local_open'),
    ('htf_score',99.,'higher_score_is_directional_normalized_md'),
])
def test_tampered_context_rejected(ledgers,column,value,check):
    def mutate(f):f.loc[0,column]=value
    alter(ledgers,'events',mutate)
    assert check in verify_saved(*ledgers)['failed_checks']
