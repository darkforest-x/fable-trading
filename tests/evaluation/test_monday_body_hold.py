"""Causal body-only confirmations and no post-exit excursion contamination."""
import importlib.util
from pathlib import Path
import unittest
import warnings
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_body_hold import prepare_body, excursions
from yoyo.evaluation import monday_parameter_signals as s
from yoyo.evaluation.monday_vectorbt_engine import execute

CFG=dict(split='2023-01-01T00:00:00Z',seed=20261023,controls_per_signal=3,taker_fee=.0005,maker_fee=.0002)


def fixture():
    p=Path(__file__).with_name('test_monday_1h_wick_hold.py')
    spec=importlib.util.spec_from_file_location('body_fixture',p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m.fixture()[1]


def admitted(f,n=3):
    return s.candidates(prepare_body(s.base_context(f,'BTCUSDT')),n,20,CFG)


class BodyHoldTest(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings('ignore',category=pd.errors.Pandas4Warning)

    def test_wicks_may_cross_but_either_body_edge_may_not(self):
        f=fixture();f.iloc[27*12:30*12,f.columns.get_loc('low')]=92.
        assert len(admitted(f))>0
        assert s.candidates(s.prepare_case(s.base_context(f,'BTCUSDT'),3),3,20,CFG).empty
        # Equality is allowed; changing only the open or only the close below
        # the level fails, even though the wick was already below it.
        for col,offset in [('open',28*12),('close',29*12-1)]:
            g=f.copy();g.iloc[offset,g.columns.get_loc(col)]=93.82
            assert len(admitted(g))>0
            g.iloc[offset,g.columns.get_loc(col)]=93.81
            assert admitted(g).empty

    def test_short_mirror_and_first_touch_consumption(self):
        f=fixture();f.iloc[27*12:30*12,f.columns.get_loc('low')]=92.
        g=f.copy();g.open=210-f.open;g.close=210-f.close;g.high=210-f.low;g.low=210-f.high
        a=admitted(g);assert a[a.population.eq('signal')].side.iloc[0]==-1
        f.iloc[25*12:26*12,:4]=[92.,97.,91.,95.]
        assert admitted(f).empty

    def test_complete_consecutive_bars_and_next_open_no_future(self):
        f=fixture();assert admitted(f.iloc[:30*12]).empty
        a=admitted(f);a=a[a.population.eq('signal')]
        assert pd.Timestamp(a.entry_time.iloc[0])-pd.Timestamp(a.pin_time.iloc[0])==pd.Timedelta(hours=4)
        g=f.copy();g.iloc[30*12+1:,:4]=[900.,1000.,800.,950.]
        b=admitted(g);b=b[b.population.eq('signal')]
        pd.testing.assert_frame_equal(a.reset_index(drop=True),b.reset_index(drop=True))
        assert admitted(f.drop(f.index[28*12])).empty

    def audit(self,bars,side,stop,target):
        f=pd.DataFrame(bars,columns=['open','high','low','close'],index=pd.date_range('2026-01-01',periods=len(bars),freq='5min',tz='UTC'))
        e=pd.DataFrame([dict(trade_key='test',entry_i=0,side=side,entry=100.,stop=stop,target_price=target,risk=abs(stop-100),entry_time=str(f.index[0]))])
        a,_=execute(f,e,'BTCUSDT',CFG)
        b=excursions(f,a)
        legacy=a.copy();legacy.exit_path_segment=np.nan
        c=excursions(f,legacy)
        np.testing.assert_allclose(b[['mfe_r','mae_r']],c[['mfe_r','mae_r']])
        return b.iloc[0]

    def test_short_stop_must_exclude_later_low(self):
        r=self.audit([[100.,112.,70.,80.]],-1,110.,60.)
        assert r.reason==1 and r.mfe_r==0 and r.mae_r==1

    def test_long_stop_includes_earlier_high_but_not_later_close(self):
        r=self.audit([[100.,105.,88.,99.]],1,90.,120.)
        assert r.reason==1 and r.mfe_r==.5 and r.mae_r==1
        assert r.mfe_before_exit_bar_r==0

    def test_open_gap_stop_excludes_exit_bar_high(self):
        r=self.audit([[100.,101.,99.,100.],[85.,130.,80.,120.]],1,90.,140.)
        assert r.exit_at_open and abs(r.mfe_r-.1)<1e-12 and r.mae_r==1.5

    def test_target_excursion_is_capped_at_actual_fill(self):
        r=self.audit([[100.,101.,99.,100.],[85.,115.,60.,110.]],-1,110.,90.)
        assert r.reason==4 and r.mfe_r==1 and abs(r.mae_r-.1)<1e-12

    def test_censored_mark_has_no_false_closed_trade(self):
        r=self.audit([[100.,105.,95.,103.]],1,90.,120.)
        assert not r.closed and r.mfe_r==.5 and r.mae_r==.5


if __name__=='__main__': unittest.main()
