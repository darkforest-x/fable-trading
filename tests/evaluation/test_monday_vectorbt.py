"""Behavioral fixtures for causal grids and framework execution boundaries."""
import importlib.util
from pathlib import Path
import unittest
import warnings
import numpy as np
import pandas as pd
warnings.filterwarnings('ignore', category=pd.errors.Pandas4Warning)
from yoyo.evaluation import monday_parameter_signals as s
from yoyo.evaluation.monday_vectorbt_engine import execute

CFG={'split':'2023-01-01T00:00:00Z','seed':20261023,'controls_per_signal':3,
     'first_touch_only':True,'taker_fee':.0005,'maker_fee':.0002}


def fixture_module(filename):
    spec=importlib.util.spec_from_file_location(filename,Path(__file__).with_name(filename+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class MondayVectorBTTest(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings('ignore', category=pd.errors.Pandas4Warning)

    def test_baseline_and_parameter_entry_timing(self):
        for case,name in [(2,'test_monday_15m_volume_rejection'),(3,'test_monday_1h_wick_hold'),(4,'test_monday_4h_close_hold')]:
            source=fixture_module(name);_,f=source.fixture()
            f.index=f.index.as_unit('ns')
            if 'volume' not in f:f['volume']=10.
            ctx=s.prepare_case(s.base_context(f,'BTCUSDT'),case)
            actual=s.candidates(ctx,3,20,CFG)
            expected,_=source.m.candidates(f,'BTCUSDT',CFG)
            columns=['trade_id','entry_i','entry','stop','target_price','side']
            pd.testing.assert_frame_equal(actual[columns].reset_index(drop=True),expected[columns].reset_index(drop=True))
            for n in (1,2,4,5):
                generated=s.candidates(ctx,n,20,CFG)
                if len(generated):
                    a=generated[generated.population.eq('signal')]
                    assert (pd.to_datetime(a.entry_time,utc=True)-pd.to_datetime(a.pin_time,utc=True)==ctx['step']*(n+1)).all()

    def test_volume_and_confirmation_are_independent(self):
        source=fixture_module('test_monday_15m_volume_rejection');h,f=source.fixture()
        # A volume spike 25 bars before the pin only excludes the30/40 windows.
        f.iloc[(98-25)*3:(98-24)*3,f.columns.get_loc('volume')]=100.
        ctx=s.prepare_case(s.base_context(f,'BTCUSDT'),2)
        assert len(s.candidates(ctx,3,20,CFG))>0
        assert s.candidates(ctx,3,30,CFG).empty
        assert s.candidates(ctx,3,40,CFG).empty

    def test_no_future_confirmation_information(self):
        source=fixture_module('test_monday_4h_close_hold');_,f=source.fixture()
        cut=10*48
        assert s.candidates(s.prepare_case(s.base_context(f.iloc[:cut],'BTCUSDT'),4),1,20,CFG).empty
        complete=s.candidates(s.prepare_case(s.base_context(f.iloc[:cut+1],'BTCUSDT'),4),1,20,CFG)
        assert len(complete)>0
        changed=f.copy();changed.iloc[cut+1:,:4]=[500.,600.,400.,550.]
        before=s.candidates(s.prepare_case(s.base_context(f,'BTCUSDT'),4),1,20,CFG)
        after=s.candidates(s.prepare_case(s.base_context(changed,'BTCUSDT'),4),1,20,CFG)
        cols=['entry_time','entry','stop','target_price']
        pd.testing.assert_frame_equal(before[before.population.eq('signal')][cols].reset_index(drop=True),after[after.population.eq('signal')][cols].reset_index(drop=True))

    def test_same_candle_two_sides_gap_stop_and_fees(self):
        f=pd.DataFrame([[100.,112.,88.,101.],[100.,102.,98.,101.],[85.,87.,84.,86.]],
                       columns=['open','high','low','close'],index=pd.date_range('2026-01-01',periods=3,freq='5min',tz='UTC'))
        rows=[]
        for key,i,side,stop,target in [('long',0,1,90.,110.),('short',0,-1,110.,90.),('gap',1,1,90.,120.),('censor',1,1,50.,150.)]:
            rows.append(dict(trade_key=key,entry_i=i,entry=100.,side=side,stop=stop,target_price=target,risk=side*(100-stop),entry_time=str(f.index[i])))
        result,fills=execute(f,pd.DataFrame(rows),'BTCUSDT',CFG)
        result=result.set_index('trade_key')
        assert result.loc['long','reason']==4 and result.loc['long','exit_price']==110.
        assert result.loc['short','reason']==1 and result.loc['short','exit_price']==110.
        assert result.loc['gap','reason']==1 and result.loc['gap','exit_price']==85.
        assert not result.loc['censor','closed']
        np.testing.assert_allclose(result.loc['long','net_r'],1-(100*.0005+110*.0002)/10)
        np.testing.assert_allclose(result.loc['short','net_r'],-1-(100*.0005+110*.0005)/10)
        assert len(fills)==8
        assert result.loc['long','stop_first_net_r']<0

    def test_missing_crypto_interval_censors_before_gap(self):
        f=pd.DataFrame([[100.,101.,99.,100.],[100.,130.,80.,100.]],columns=['open','high','low','close'],
                       index=pd.to_datetime(['2026-01-01T00:00Z','2026-01-01T00:10Z']))
        e=pd.DataFrame([dict(trade_key='gap',entry_i=0,entry=100.,side=1,stop=90.,target_price=110.,risk=10.,entry_time=str(f.index[0]))])
        a,_=execute(f,e,'BTCUSDT',CFG)
        assert not a.closed.iloc[0] and a.exit_original_i.iloc[0]==0

    def test_exact_barrier_touch_survives_percentage_roundtrip(self):
        f=pd.DataFrame([[100.,110.,99.,105.]],columns=['open','high','low','close'],
                       index=pd.date_range('2026-01-01',periods=1,freq='5min',tz='UTC'))
        e=pd.DataFrame([dict(trade_key='exact',entry_i=0,entry=100.,side=1,stop=90.,target_price=110.,risk=10.,entry_time=str(f.index[0]))])
        a,_=execute(f,e,'BTCUSDT',CFG)
        assert a.closed.iloc[0] and a.exit_price.iloc[0]==110.


if __name__=='__main__':unittest.main()
