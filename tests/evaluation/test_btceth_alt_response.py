"""Event-clock and future-visibility regressions for descriptive5m alignment."""
import unittest
import numpy as np
import pandas as pd
from yoyo.evaluation.btceth_alt_response import levels,event_rows,all_features,OHLCV

class ResponseTests(unittest.TestCase):
    def fixture(self):
        idx=pd.date_range('2026-08-31T00:00:00Z',periods=288*8,freq='5min')
        return pd.DataFrame(dict(open=100.,high=101.,low=99.,close=100.,volume=10.),index=idx)

    def test_new_york_monday_and_missing_anchor(self):
        f=self.fixture();f.loc['2026-08-31T00:00Z','high']=900.
        b=levels(f);t=pd.Timestamp('2026-09-01T04:00Z')
        self.assertAlmostEqual(b.loc[t,'lower'],101.-1.618*2.)
        self.assertTrue(pd.isna(b.loc[t-pd.Timedelta(minutes=5),'lower']))
        changed=f.copy();changed.loc[t:,'high']=1200.
        self.assertEqual(levels(changed).loc[t,'upper'],b.loc[t,'upper'])
        f.loc['2026-08-31T04:00Z','close']=np.nan
        self.assertTrue(pd.isna(levels(f).loc[t,'lower']))

    def test_first_ready_bar_cooldown_and_rearm(self):
        f=self.fixture();t=pd.Timestamp('2026-09-01T04:00Z')
        for minutes in [0,5,15,65]:f.loc[t+pd.Timedelta(minutes=minutes),'low']=96.
        frames={'BTC':f,'ETH':f.copy()};bounds={s:levels(z) for s,z in frames.items()}
        e=event_rows(frames,bounds);g=e[e.leader.eq('BTC')&e.side.eq(1)]
        self.assertEqual(g.time.tolist(),[t,t+pd.Timedelta(minutes=65)])
        self.assertEqual(g.kind.tolist(),['pure_wick_reclaim']*2)

    def test_features_ignore_later_prices(self):
        f=self.fixture();rng=np.random.default_rng(4)
        f['close']=100+np.cumsum(rng.normal(0,.02,len(f)));f['open']=f.close.shift().fillna(100)
        f['low']=f[['open','close']].min(axis=1)-.1;f['high']=f[['open','close']].max(axis=1)+.1
        frames={'BTC':f,'ETH':f*1.1,'SOL':f*1.3};bounds={s:levels(z) for s,z in frames.items()}
        before,_,_=all_features(frames,bounds,'BTC',1);i=700
        changed={s:z.copy() for s,z in frames.items()}
        for z in changed.values():z.iloc[i+1:]=z.iloc[i+1:]*5
        after,_,_=all_features(changed,{s:levels(z) for s,z in changed.items()},'BTC',1)
        cols=[c for c in before if 'after' not in c and 'extreme_offset' not in c and 'extreme_earlier' not in c]
        pd.testing.assert_series_equal(before.iloc[i][cols],after.iloc[i][cols])

if __name__=='__main__':unittest.main()
