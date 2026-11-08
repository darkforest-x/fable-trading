"""Replay a frozen V1.7 Binance comparison with missing February 2022 sessions restored."""
import gc
import json
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_daily_patch as patch
from yoyo.evaluation import cloud_close_binance_transfer as venue
from yoyo.evaluation import cloud_close_lower_agreement as agreement
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as scaleout
from yoyo.evaluation import signal_center_score as source


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-binance-gap-repair-20261109-v1'
WORK = wallet.ROOT / 'data/research/cloud_close_binance_feb2022_replay'
FROM = pd.Timestamp('2022-02-26',tz='UTC')
UNTIL = pd.Timestamp('2022-03-01',tz='UTC')


def rebuild(job):
    ticker = job['symbol']
    folder = WORK/ticker
    receipt = folder/'patch_receipt.json'
    if receipt.is_file():
        return json.loads(receipt.read_text())
    folder.mkdir(exist_ok=True,parents=True)
    prior = source.five_minute(job)
    daily = patch.patch_bars(ticker)
    if not daily.index.equals(pd.date_range(FROM,UNTIL,freq='5min',inclusive='left').as_unit('ns')):
        raise ValueError(f'{ticker}: incomplete official 5m patch clock')
    keep = (prior.index<FROM) | (prior.index>=UNTIL)
    overlap = prior[~keep]
    if len(overlap) and not np.allclose(overlap[venue.FIELDS+['volume']],daily.loc[overlap.index,venue.FIELDS+['volume']],rtol=1e-10):
        raise ValueError(f'{ticker}: old and replacement source overlap differs')
    restored = pd.concat([prior[keep],daily[prior.columns]]).sort_index()
    if not restored.index.is_unique:
        raise ValueError(f'{ticker}: duplicate merged candles')
    keys = source.build_inputs(restored,ticker,None,key_timeframes=['12H','4H'])[4].assign(symbol=ticker)
    previous = pd.read_csv(venue.ARCHIVE/'results/symbols'/ticker/'signals.csv.gz',dtype={'symbol':str})
    previous = previous[previous.tf.isin(['12H','4H'])]
    def past(frame):
        return frame[frame.close_ns.lt(FROM.value)].set_index('signal_id').sort_index()
    old, new = past(previous), past(keys)
    if not new.index.equals(old.index) or not np.array_equal(new[['side','close_ns']],old[['side','close_ns']]) or not np.allclose(new.stop,old.stop,rtol=1e-11,atol=1e-14):
        raise ValueError(f'{ticker}: unaffected pre-repair keys differ')
    keys.to_csv(folder/'keys.csv.gz',index=False)
    times = restored.index.as_unit('ms').asi8
    market = restored[(times>=venue.START)&(times<venue.END)]
    hourly, twelve = venue.aggregate(market,1), venue.aggregate(market,12)
    meta = venue.price_frames(ticker,folder,hourly,twelve,keys)
    added = set(keys.signal_id)-set(previous.signal_id)
    removed = set(previous.signal_id)-set(keys.signal_id)
    summary = dict(**meta, prior_verified_keys=len(old), overlapping_5m=len(overlap),
                   restored_5m=len(daily)-len(overlap), added_keys=len(added), removed_keys=len(removed))
    temp = receipt.with_suffix('.pending')
    temp.write_text(json.dumps(summary))
    temp.replace(receipt)
    return summary


@lru_cache(maxsize=180)
def quotes(symbol):
    file = WORK/(symbol+'USDT')/'quotes.npz'
    if file.exists():
        with np.load(file) as data:
            return data['clock'],data['prices']
    return venue.quotes(symbol)


def assembled(affected):
    old = pd.read_csv(venue.EXP/'fills.csv.gz',dtype={'symbol':str},keep_default_na=False,
                      float_precision='round_trip')
    pieces = [old[~(old.symbol+'USDT').isin(affected)].drop(columns='eligible')]
    keys, ranks = {}, {}
    for original in sorted(venue.WORK.glob('*/quotes.npz')):
        ticker = original.parent.name
        file = WORK/ticker/'quotes.npz' if ticker in affected else original
        with np.load(file) as data:
            for ts in data['rank_times']:
                ranks[ts] = ranks.get(ts,0)+1
        if ticker in affected:
            keys[ticker] = pd.read_csv(file.parent/'keys.csv.gz',dtype={'symbol':str})
            pieces.append(pd.read_csv(file.parent/'fills.csv.gz',dtype={'symbol':str},keep_default_na=False,
                                      float_precision='round_trip'))
    paths = pd.concat(pieces,ignore_index=True)
    if len(keys)!=len(affected) or paths.duplicated(['event_id','tp_r']).any() or not paths.groupby('event_id').size().eq(2).all():
        raise ValueError('Invalid patched trading universe')
    paths['eligible'] = paths.eligible_at_own_time & paths.entry_ms.map(ranks).fillna(0).ge(10)
    paths = paths[paths.eligible]
    paths = paths[paths.parent_id.isin(paths.loc[~paths.is_control,'parent_id'])].copy()
    paths['agree4h'] = agreement.prior_signal_times(paths,['4H'],keys)['4H']
    return paths


def prepare(pool):
    return scaleout.pair_data(pool[pool.tp_r.eq(0)],pool[pool.tp_r.eq(2)],9,start=venue.START,
                              end=venue.END,quote_loader=quotes)


def main():
    affected = set(patch.affected_tickers())
    jobs = [j for j in venue.jobs() if j['symbol'] in affected]
    if len(jobs)!=len(affected) or len(jobs)!=43:
        raise ValueError('Original affected contract set changed')
    with ProcessPoolExecutor(max_workers=2) as executor:
        records = []
        for n,result in enumerate(executor.map(rebuild,jobs),1):
            records.append(result)
            if n%10==0:
                print('Repriced',n,'of',len(jobs),flush=True)
    pd.DataFrame(records).to_csv(EXP/'patch_coverage.csv',index=False)
    paths = assembled(affected)
    rows, gaps = [], {}
    for label, pool in [('all',paths),('agree4h',paths[paths.agree4h])]:
        real = pool[~pool.is_control]
        data = prepare(real)
        for fraction in [.75,.5]:
            name = f'{label}_{fraction}'
            metrics, trades, equity = scaleout.simulate(data,fraction)
            gaps[name] = data['gap_marks']
            rows.extend(dict(policy=name,period=p,**m) for p,m in metrics.items())
            if label=='agree4h' and fraction==.5:
                trades.to_csv(EXP/'funded_agree4h_half.csv.gz')
                equity.to_csv(EXP/'hourly_agree4h_half.csv.gz',index_label='hour_end_utc')
                if not np.isclose(1000+trades.cash_pnl.sum(),equity.nav.iloc[-1],rtol=1e-8):
                    raise ValueError('Restored-capital reconciliation failed')
        del data
        gc.collect()
    real = paths[paths.agree4h & ~paths.is_control]
    ctl = paths[paths.agree4h & paths.is_control & paths.parent_id.isin(real.parent_id)]
    for name, events, slip in [('paired',real[real.parent_id.isin(ctl.parent_id)],0.),
                               ('controls',ctl,0.),('stress',real,.0005)]:
        data = prepare(events)
        metrics, _, _ = scaleout.simulate(data,.5,slip)
        gaps[name] = data['gap_marks']
        rows.extend(dict(policy=name,period=p,**m) for p,m in metrics.items())
        del data
        gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    summary = dict(affected=len(affected), restored_source_rows=sum(r['restored_5m'] for r in records),
                   eligible_real=len(paths[~paths.is_control])//2, agree4h_real=len(real)//2,
                   agree4h_paired=len(ctl)//2, unquoted_leg_hours=gaps)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
