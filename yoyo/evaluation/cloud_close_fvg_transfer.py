"""Apply the first-touch 12H FVG exclusion unchanged to Binance perpetual candles."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_fvg_exclusion as study
from yoyo.evaluation import cloud_close_spot_reclaim as spot
from yoyo.evaluation import cloud_close_november_paths as prices

admin = spot.admin
EXP = admin.EXP.parent/'exp-cloud-close-fvg-transfer-20261109-v1'


def classify(paths):
    items = []
    for symbol,group in paths[(~paths.is_control)&paths.tp_r.eq(0)].groupby('symbol'):
        clock,data = admin.archive.native.quotes(symbol)
        hours = pd.DataFrame(data,index=pd.Index(clock,name='time'),columns=prices.OHLC)
        zones = study.prior_zones(prices.twelve_hour(hours))
        ticker = symbol+'USDT'
        tick = 10.**-max(2,min(9,int(np.ceil(-np.log10(hours.low.min())))+7))
        pct = prices.keys.stop_pct(ticker)
        for row in group.itertuples():
            key = row.entry_ms-study.pilot.STEP
            week = study.pilot.week_window(key)
            start,end = np.searchsorted(clock,[week['start'],week['end']])
            monday = hours.iloc[start:end]
            tag = 'incomplete_monday'
            if len(monday)==24:
                high,low = monday.high.max(),monday.low.min()
                line = low-.618*(high-low) if row.side==1 else high+.618*(high-low)
                if abs(float(row.stop_price)-line*(1-row.side*pct))>1.6*tick+1.e-12:
                    raise ValueError(f'Frozen 1.618 stop disagrees with native candles for {row.event_id}')
                ref = SimpleNamespace(side=row.side,week=week['week'],key_open_bjt=pd.Timestamp(key,unit='ms',tz='UTC'),
                                      reference_entry=line,reference_stop=row.stop_price)
                tag = study.touch_tag(ref,hours,zones)
            items.append(dict(parent_id=row.parent_id,symbol=symbol,entry_ms=row.entry_ms,tag=tag))
    return pd.DataFrame(items).set_index('parent_id',verify_integrity=True)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed native FVG transfer')
    extra = set(json.loads((admin.archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    spec,marks = spot.rank.market_spec(EXP)
    for source_dir in (spot.vwap.EXP,spot.timing.EXP):
        spec.update(json.loads((source_dir/'config.json').read_text())['additional_settlements'])
        marks.update(pd.read_csv(source_dir/'settlement_index_1m.csv').groupby('symbol')[['low','close','high']].mean().to_dict('index'))
    with admin.archive.market_quotes(extra):
        all_paths = admin.archive.pool(extra)
        _,rotated = admin.study.tenth.trend.inputs('Binance',all_paths)
        rows = admin.adjusted_legs(rotated,extra,spec)
        file = EXP/'decisions.csv.gz'
        if file.is_file():
            tags = pd.read_csv(file,dtype={'symbol':str}).set_index('parent_id')
        else:
            tags = classify(rows)
            tags.to_csv(file)
        if set(tags.index)!=set(rows.loc[~rows.is_control,'parent_id']):
            raise ValueError('Native FVG signals differ from the frozen original roster')
        results = {}
        for label,values in [('covered',['overlap','outside']),('exclude_overlap',['outside'])]:
            accepted = rows[rows.parent_id.isin(tags.index[tags.tag.isin(values)])]
            actual = accepted[~accepted.is_control]
            scenarios = [('actual',actual,0.)]
            while scenarios:
                mode,pool,slip = scenarios.pop(0)
                paid = admin.admitted(pool)
                deliveries = paid[paid.delivery]
                deliveries.to_csv(EXP/f'deliveries_{label}_{mode}.csv',index=False)
                metrics,parents,equity = admin.fund(paid.copy(),marks,'proxy',slip=slip)
                results[f'{label}_{mode}'] = dict(eligible=len(pool)//2,metrics=metrics)
                parents.to_csv(EXP/f'funded_{label}_{mode}.csv.gz')
                if label=='exclude_overlap' and mode=='actual' and study.trend.second.meets(metrics['dev']) and study.trend.second.meets(metrics['all']) and metrics['dev']['final_usdt']>results['covered_actual']['metrics']['dev']['final_usdt']:
                    ctls = accepted[accepted.is_control & accepted.parent_id.isin(actual.parent_id)]
                    paired = actual[actual.parent_id.isin(ctls.parent_id)]
                    scenarios.extend([('paired',paired,0.),('later',ctls,0.),('stress',actual,.0005)])
    summary = dict(tags=tags.tag.value_counts().to_dict(),results=results)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()
