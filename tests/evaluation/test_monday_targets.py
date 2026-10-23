"""Target interpretation, common controls, and native execution consequences."""
import unittest
import warnings
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_target_study import far_candidates
from yoyo.evaluation.monday_vectorbt_engine import execute
from yoyo.evaluation.monday_body_hold import excursions

CFG=dict(taker_fee=.0005,maker_fee=.0002)


class MondayTargetsTest(unittest.TestCase):
    def setUp(self):warnings.filterwarnings('ignore',category=pd.errors.Pandas4Warning)

    def candidates(self,side):
        entry,stop,target=(100.,90.,110.) if side==1 else (140.,150.,130.)
        rows=[]
        for k,scale in enumerate([1.,2.]):
            rows.append(dict(config_id='body-n3',confirmations=3,pair_id='pair',trade_id=f't{k}',trade_key=f'old{k}',
                population='signal' if k==0 else 'random',symbol='TEST',side=side,entry=entry*scale,
                stop=stop*scale,target_price=target*scale,risk=10.*scale,entry_i=k,
                entry_time=str(pd.Timestamp('2026-10-20T12:00Z')+pd.Timedelta(hours=k)),week='2026-10-19'))
        return pd.DataFrame(rows)

    def test_long_short_far_anchors_and_scaled_controls(self):
        anchors=pd.DataFrame({'high':[130.],'low':[110.]},index=['2026-10-19'])
        for side in (1,-1):
            near=self.candidates(side);far=far_candidates(near,anchors,5)
            target=130. if side==1 else 110.
            np.testing.assert_allclose(far.target_price,[target,target*2])
            pd.testing.assert_frame_equal(near[['entry','stop','risk','entry_time','pair_id']],far[['entry','stop','risk','entry_time','pair_id']])
            assert (far.trade_key!=near.trade_key).all()

    def test_missing_or_wrong_anchor_fails_closed(self):
        near=self.candidates(1)
        for anchors in [pd.DataFrame({'high':[130.],'low':[110.]},index=['2026-10-12']),
                        pd.DataFrame({'high':[130.],'low':[109.]},index=['2026-10-19'])]:
            with self.assertRaises(AssertionError):far_candidates(near,anchors,5)

    def run_pair(self,bars,side=1):
        f=pd.DataFrame(bars,columns=['open','high','low','close'],index=pd.date_range('2026-01-01',periods=len(bars),freq='5min',tz='UTC'))
        rows=[dict(trade_key=mode,entry_i=0,entry=100.,side=side,stop=90. if side==1 else 110.,
            target_price=100.+side*distance,risk=10.,entry_time=str(f.index[0])) for mode,distance in [('near',10.),('far',30.)]]
        result,fills=execute(f,pd.DataFrame(rows),'BTCUSDT',CFG)
        assert len(fills)==4
        return excursions(f,result).set_index('trade_key')

    def test_near_target_then_far_stop_is_preserved(self):
        r=self.run_pair([[100.,112.,99.,108.],[108.,115.,89.,95.]])
        assert r.loc['near','reason']==4 and r.loc['far','reason']==1
        assert r.loc['near','mfe_r']==1 and r.loc['far','mfe_r']==1.5
        assert r.loc['near','exit_original_i']==0 and r.loc['far','exit_original_i']==1

    def test_far_target_can_extend_profit_with_its_own_fee(self):
        r=self.run_pair([[100.,112.,99.,108.],[108.,132.,105.,128.]])
        assert r.reason.eq(4).all()
        np.testing.assert_allclose(r.loc['far','net_r'],3-(100*.0005+130*.0002)/10)
        assert r.loc['far','mfe_r']==3

    def test_near_stop_must_not_change_for_far_target(self):
        r=self.run_pair([[100.,105.,88.,100.]])
        assert r.reason.eq(1).all()
        assert r.net_r.nunique()==1 and r.exit_original_i.nunique()==1

    def test_far_censor_is_not_counted_as_a_win(self):
        r=self.run_pair([[100.,112.,99.,108.]])
        assert r.loc['near','closed'] and not r.loc['far','closed']

    def test_short_near_target_can_turn_into_far_stop(self):
        r=self.run_pair([[100.,101.,88.,92.],[92.,111.,85.,105.]],side=-1)
        assert r.loc['near','reason']==4 and r.loc['far','reason']==1
        assert r.loc['far','mfe_r']==1.2  # Second-bar low occurs after the stop.


if __name__=='__main__':unittest.main()
