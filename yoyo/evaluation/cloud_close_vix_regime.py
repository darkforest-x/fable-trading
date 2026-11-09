"""Gate closed V1.7 entries by the previously published direction of Cboe's VIX."""

import json

import numpy as np
import pandas as pd
import requests

from yoyo.evaluation import cloud_close_liquid_queue as queue

EXP = queue.EXP.parent/'exp-cloud-close-vix-regime-20261109-v1'
URL = 'https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv'
CACHE = queue.repair.wallet.ROOT/'data/research/cloud_close_vix_regime/VIX_History_20261109.csv'
DAY = 24 * queue.repair.wallet.HOUR


def daily_regime(path):
    """Official VIX CLOSE vs five sessions before, eligible from UTC midnight two calendar dates later."""
    daily = pd.read_csv(path,usecols=['DATE','CLOSE'])
    daily['date'] = pd.to_datetime(daily.DATE,format='%m/%d/%Y',utc=True)
    if daily.date.duplicated().any() or not daily.date.is_monotonic_increasing or not daily.CLOSE.gt(0).all():
        raise ValueError('Invalid daily VIX source')
    daily['available_ms'] = daily.date.map(lambda d:d.value//1_000_000)+2*DAY
    daily['direction'] = np.sign(daily.CLOSE-daily.CLOSE.shift(5))
    daily['stale_direction'] = daily.direction.shift(20)
    return daily


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed VIX regime experiment')
    if not CACHE.exists():
        response = requests.get(URL,timeout=(8,35))
        response.raise_for_status()
        CACHE.parent.mkdir(exist_ok=True,parents=True)
        path = CACHE.with_suffix('.tmp')
        path.write_bytes(response.content)
        daily_regime(path)
        path.replace(CACHE)
    daily = daily_regime(CACHE)
    rows,_,_,_=queue.repair.pool()
    events=rows.loc[rows.tp_r.eq(0),['event_id','symbol','entry_ms','side']].sort_values('entry_ms')
    features=pd.merge_asof(events,daily[['available_ms','date','CLOSE','direction','stale_direction']],
                           left_on='entry_ms',right_on='available_ms',direction='backward',tolerance=4*DAY)
    covered=features[['direction','stale_direction']].notna().all(axis=1)
    features=features[covered].set_index('event_id')
    if not (features.entry_ms>=features.available_ms).all():
        raise ValueError('Unpublished VIX data entered a signal')
    features.to_csv(EXP/'signal_regimes.csv.gz')
    # The risk-aligned rule favors longs when the independently observed US equity volatility index has fallen.
    product=features.side*features.direction
    allowed=dict(covered=features.index,aligned=features.index[product<0],opposed=features.index[product>0],
                 lagged=features.index[features.side*features.stale_direction<0])
    fully_covered = len(features)==len(events) and set(features.index)==set(events.event_id)
    if fully_covered:
        del allowed['covered']
    queue.main(experiment=EXP,policies=[(s,'balanced') for s in allowed],eligible_by_arm=allowed)
    if fully_covered:
        path=EXP/'summary.json'
        result=json.loads(path.read_text())
        prior=json.loads((queue.EXP/'summary.json').read_text())['arms']['balanced']
        source_roster=set(pd.read_csv(queue.EXP/'balanced/selected_paths.csv.gz',usecols=['event_id']).event_id)
        result['arms']['covered']=dict(prior,eligible_count=result['liquid_eligible'])
        result['covered_account_source']=str((queue.EXP/'balanced').relative_to(queue.repair.wallet.ROOT))
        for label in allowed:
            actual=set(pd.read_csv(EXP/label/'selected_paths.csv.gz',usecols=['event_id']).event_id)
            result['arms'][label]['overlap']=len(actual & source_roster)
        queue.repair.archive.prices.safe_json(path,result)


if __name__=='__main__':
    main()
