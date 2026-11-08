"""Freeze the previous screen's exact eligible identities and chart boundaries.

Causal order terms use the signal close, fixed 1.618 and stop, plus the first
future 12H bar's EMA-cross boundary calculated with the just-confirmed EMAs.
Later OHLC and exit fields are replay/evaluation data only.
"""
from pathlib import Path
import json

import pandas as pd

from yoyo.evaluation.cloud_allmarket_census import EXP as SOURCE, STEP, read_bars


EXP = SOURCE.parent / 'exp-cloud-strong-retest-20261108-v1'
CONFIG = json.loads((EXP/'config.json').read_text())
MINUTE = 60_000


def cross_price(fast, slow):
    a, b = 2/21, 2/51
    return ((1-b)*slow - (1-a)*fast)/(a-b)


def candidates():
    signals = pd.read_csv(SOURCE/'signals.csv',float_precision='round_trip')
    years = signals.confirmed_bjt.str[:4].isin(['2025','2026'])
    keep = signals.close_distance_r.isna() | (signals.close_distance_r >= CONFIG['reclaim_threshold_r'])
    frame = signals[~signals.was_top20 & years & keep].copy()
    frame['confirm_ms'] = pd.to_datetime(frame.confirmed_bjt,utc=True).astype('int64')//1_000_000
    frame['case_id'] = frame.symbol + ':' + frame.confirm_ms.astype(str)
    frame['year'] = frame.confirmed_bjt.str[:4].astype(int)
    frame['chart_exit_ms'] = pd.to_datetime(frame.path_end_bar_bjt,utc=True).astype('int64').where(frame.path_end_bar_bjt.notna()).floordiv(1_000_000).astype('Int64')
    frame['chart_exit_price'] = frame.reference_entry + (frame.reference_entry-frame.reference_stop)*frame.exit_reference_r
    frame['first_cloud_cross'] = cross_price(frame.ema20,frame.ema50)
    assert frame.case_id.is_unique and frame.year.value_counts().to_dict()=={2025:1695,2026:2390}
    return frame


def main():
    cases = candidates()
    chunks=[]
    for symbol,g in cases.groupby('symbol',sort=False):
        bars=read_bars(SOURCE/'inputs'/symbol)
        b=bars[['open','high','low','close']].rename(columns=lambda c:'first_'+c)
        joined=g.join(b,on='confirm_ms')
        has_bar=joined.first_open.notna()
        touched=(joined.side.eq(1)&joined.first_low.le(joined.reference_entry)) | (joined.side.eq(-1)&joined.first_high.ge(joined.reference_entry))
        joined['first_bar_touches']=touched.where(has_bar).astype('boolean')
        chunks.append(joined)
    cases=pd.concat(chunks).sort_values(['confirm_ms','symbol'])
    keep=['case_id','symbol','week','confirmed_bjt','confirm_ms','year','side','reference_entry','confirmation_close','reference_stop',
          'first_cloud_cross','first_open','first_high','first_low','first_close','first_bar_touches','chart_exit_ms','chart_exit_price',
          'exit_reason','peak_lower_r','peak_upper_r','confirmation_basis_lower_r','confirmation_basis_upper_r','status','close_distance_r']
    cases[keep].to_csv(EXP/'cases.csv',index=False)
    print(cases.groupby(['year','first_bar_touches'],dropna=False).size())

if __name__=='__main__':
    main()
