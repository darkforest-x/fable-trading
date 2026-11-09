"""Test exclusion of same-side 1.618/FVG confluence at original 12H confirmations."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_direction_history as account
from yoyo.evaluation import cloud_fvg_pilot as pilot

trend = account.tenth.trend
wallet = trend.thirds.wallet
EXP = wallet.EXP.parent/'exp-cloud-close-fvg-exclusion-20261109-v1'


def prior_zones(bars):
    states = {}
    for _,segment in bars.groupby(bars.index.to_series().diff().ne(pilot.STEP).cumsum()):
        states.update(pilot.snapshots(segment))
    return states


def touch_tag(row,hours,states):
    key = pd.Timestamp(row.key_open_bjt).value//1_000_000
    start = pilot.week_window(key)['end']
    count = (key+pilot.STEP-start)//pilot.HOUR
    left,end = np.searchsorted(hours.index,[start,key+pilot.STEP])
    if end-left!=count or count<=0:
        return 'missing_hours'
    window = hours.iloc[left:end]
    hits = window.low.le(row.reference_entry) if row.side==1 else window.high.ge(row.reference_entry)
    if not hits.any():
        return 'no_hourly_touch'
    if hits[hits].index[0]//pilot.STEP*pilot.STEP not in states:
        return 'missing_12h'
    return pilot.first_touch(row,hours,states)['tag']


def decisions():
    cols = ['symbol','week','side','key_open_bjt','confirmed_bjt','reference_entry','reference_stop']
    source = pd.read_csv(wallet.SOURCE/'signals.csv',usecols=cols,dtype={'symbol':str},keep_default_na=False)
    source.index = pd.RangeIndex(1,len(source)+1,name='source_row_number')
    rows = []
    for symbol,part in source.groupby('symbol'):
        bars = trend.market.market_bars('OKX',symbol)
        states = prior_zones(bars)
        time,prices = wallet.quotes(symbol)
        hours = pd.DataFrame(prices,index=time,columns=['open','high','low','close'])
        for row in part.itertuples():
            tag = touch_tag(row,hours,states)
            rows.append(dict(source_row_number=row.Index,symbol=symbol,week=row.week,tag=tag))
    result = pd.DataFrame(rows).set_index('source_row_number',verify_integrity=True)
    sample = pd.read_csv(pilot.EXP/'signals.csv',usecols=['symbol','week','tag'])
    old = sample.merge(result,on=['symbol','week'],suffixes=('_old','_new'),validate='one_to_one')
    if len(old)!=len(sample) or not old.tag_old.equals(old.tag_new):
        raise ValueError('Widened market source failed original six-symbol pilot parity')
    result.to_csv(EXP/'decisions.csv.gz')
    return result


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Closed FVG exclusion study')
    feature = decisions()
    paths = trend.thirds.okx_paths()
    trend.repair.check_parent_sets(paths)
    _,paths = trend.inputs('OKX',paths)
    paths['fvg_tag'] = paths.source_row_number.map(feature.tag)
    if paths.fvg_tag.isna().any():
        raise ValueError('Missing parent key at a real or later matched entry')
    known = paths[paths.fvg_tag.isin(['overlap','outside'])]
    results = {}
    for name,eligible in [('all_symbols',known),('outside_pilot',known[~known.symbol.isin(pilot.CONFIG['symbols'])])]:
        record = {}
        for label,pool in [('covered',eligible),('exclude_overlap',eligible[eligible.fvg_tag.eq('outside')])]:
            real = pool[~pool.is_control]
            metrics,pos,eq = account.simulate(real,0.)
            record[label] = dict(eligible=len(real)//2,metrics=metrics)
            pos.to_csv(EXP/f'funded_{name}_{label}.csv.gz')
            if label=='exclude_overlap' and name=='all_symbols' and trend.second.meets(metrics['dev']) and trend.second.meets(metrics['all']) and metrics['dev']['final_usdt']>record['covered']['metrics']['dev']['final_usdt']:
                ctl = pool[pool.is_control & pool.parent_id.isin(real.parent_id)]
                paired = real[real.parent_id.isin(ctl.parent_id)]
                for scenario,sample,slip in [('paired',paired,0.),('later',ctl,0.),('stress',real,.0005)]:
                    record[scenario] = dict(eligible=len(sample)//2,metrics=account.simulate(sample,slip)[0])
        results[name] = record
    summary = dict(covered_by_tag=feature.tag.value_counts().to_dict(),results=results,
                   pilot_parity=len(pd.read_csv(pilot.EXP/'signals.csv')))
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()
