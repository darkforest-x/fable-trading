"""Reapply the frozen 2026 12H booster on genuine volume and closed bars without retraining."""
import argparse
import io
import json
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from yoyo.evaluation import signal_center_score as keys
from yoyo.evaluation.monday_limit_reclaim_inputs import jobs

ROOT = keys.ROOT
EXP = ROOT/'experiments/active/exp-cloud-close-november-scores-20261109-v1'
MODEL = EXP.parent/'exp-signal-center-score-20261106-v1/results'
SOURCE = ROOT/'data/research/cloud_close_november_prices'
WORK = ROOT/'data/research/cloud_close_november_scores'
CFG = json.loads((EXP/'config.json').read_text())
FROM,VERIFY,UNTIL = [pd.Timestamp(CFG[k]).value//1_000_000 for k in ('prefix_volume_from','verify_from','verify_until')]
HOUR = 3_600_000
FIVE = 300_000
FIELDS = ['open','high','low','close']
METRICS = ['volume','close_volume_sum_5m']


def tail_file(path,lo,hi):
    """Seek sorted native epoch-ms CSV by line, then decode only the requested date interval."""
    path = Path(path)
    fields = ['ts','close','volume']
    if path.suffix=='.gz':
        frame = pd.read_csv(path,usecols=fields)
        return frame[(frame.ts>=lo)&(frame.ts<hi)]
    with path.open('rb') as f:
        header = f.readline()
        if header.split(b',',1)[0]!=b'ts':
            raise ValueError(f'Unexpected native CSV clock {path}')
        base = f.tell()
        f.seek(0,2)
        left,right = base,f.tell()
        while right-left>32768:
            middle = (left+right)//2
            f.seek(middle)
            f.readline()
            offset = f.tell()
            line = f.readline()
            if not line or int(line.split(b',',1)[0])>=lo:
                right = middle
            else:
                left = offset
        f.seek(left)
        lines = [header]
        for line in f:
            stamp = int(line.split(b',',1)[0])
            if stamp>=hi:
                break
            if stamp>=lo:
                lines.append(line)
    return pd.read_csv(io.BytesIO(b''.join(lines)),usecols=fields)


def volume_prefix(job,decimals):
    frames = [tail_file(path,FROM,UNTIL) for path in job['paths']]
    rows = pd.concat([f for f in frames if len(f)],ignore_index=True) if any(len(f) for f in frames) else frames[0]
    duplicates = rows[rows.ts.duplicated(keep=False)]
    if len(duplicates) and duplicates.groupby('ts')[['close','volume']].nunique().gt(1).any().any():
        raise ValueError(f"Frozen volume source overlap differs for {job['symbol']}")
    rows = rows.drop_duplicates('ts').sort_values('ts').set_index('ts')
    if job['execution_minutes']==1:
        group = rows.groupby(rows.index//FIVE)
        rows = group.agg(close=('close','last'),volume=('volume','sum'))[group.size().eq(5)]
        rows.index *= FIVE
    if (rows.index%FIVE).any():
        raise ValueError(f"Invalid native five-minute source for {job['symbol']}")
    rows['close_volume_sum_5m'] = rows.close.round(decimals)*rows.volume
    group = rows.groupby(rows.index//HOUR)
    result = group[METRICS].sum()[group.size().eq(12)]
    result.index *= HOUR
    return result


def price_history(ticker):
    settings = [
        ROOT/'data/research/cloud_close_binance_feb2022_replay',
        EXP.parent/'exp-cloud-close-binance-transfer-20261109-v1/work']
    for base in settings:
        path = base/ticker/'quotes.npz'
        if path.is_file():
            with np.load(path) as data:
                return pd.DataFrame(data['prices'],index=pd.Index(data['clock'],name='time'),columns=FIELDS)
    raise FileNotFoundError(ticker)


def make_frame(job,file):
    ticker = job['symbol']
    past = price_history(ticker)
    receipt = json.loads((MODEL/'symbols'/ticker/'done.json').read_text())
    origin_ns = pd.Timestamp(receipt['first']).value
    # Freeze rounding at the old boundary; the original published precision is also recorded.
    decimals = max(2,min(9,int(np.ceil(-np.log10(past.low.min())))+7))
    prefix = volume_prefix(job,decimals)
    frame = past.join(prefix)
    if file:
        ext = pd.read_csv(file,usecols=['open_time_ms',*FIELDS,*METRICS]).set_index('open_time_ms')
        if not ext.index.is_unique or not ext.index.is_monotonic_increasing:
            raise ValueError(f'Invalid extended source for {ticker}')
        same = ext.index.intersection(frame.index)
        if not np.allclose(ext.loc[same,FIELDS],frame.loc[same,FIELDS],rtol=2.e-11,atol=1.e-13):
            raise ValueError(f'Frozen prediction price source has conflicting OHLC on {ticker}')
        frame.loc[same,METRICS] = ext.loc[same,METRICS].values
        frame = pd.concat([frame,ext[ext.index>past.index[-1]]])
    frame.index = pd.to_datetime(frame.index,unit='ms',utc=True).as_unit('ns')
    return frame,decimals,origin_ns


def btc_ctx(frame):
    close_ns = frame.index.asi8+HOUR*1_000_000
    close = frame.close.to_numpy()
    prior = keys._asof(close_ns,close,close_ns-86400*keys.NS,3600*keys.NS)
    bars = keys.tf_bars(frame,720,60)
    usable = bars['count'].ge(50)
    return dict(close_ns=close_ns,ret24=np.log(close/prior),side_12H_ns=bars.loc[usable,'close_ns'].to_numpy(),
                side_12H=np.sign(bars.loc[usable,'e20']-bars.loc[usable,'e50']).to_numpy())


@lru_cache(maxsize=1)
def market_ctx(path):
    with np.load(path) as data:
        return {name:data[name] for name in data.files}


def extract(args):
    job,file,ctx_file = args
    ticker = job['symbol']
    frame,decimals,first_ns = make_frame(job,file)
    if not len(frame):
        raise ValueError(f'No frozen native intervals for {ticker}')
    sig = raw = pd.DataFrame()
    if frame.index[-1].value >= VERIFY*1_000_000:
        sig,raw = keys.build_inputs(frame,ticker,market_ctx(ctx_file),key_timeframes=['12H'],source_minutes=60,
                                    price_decimals=decimals,feature_start_ns=first_ns)[4:6]
        sig = sig[sig.close_ns.ge(VERIFY*1_000_000)].copy() if len(sig) else sig
        raw = raw[raw.close_ns.ge(VERIFY*1_000_000)].copy()
        if len(sig):
            # The model's prior twenty 12H volume observations plus the key, and
            # its seven-day turnover, must come from complete native intervals.
            valid = frame[METRICS].notna().all(axis=1)
            continuous = frame.index.to_series().diff(251).eq(pd.Timedelta(hours=251))
            history = (valid.rolling(252,min_periods=252).sum().eq(252)&continuous)
            history.index = frame.index.asi8+HOUR*1_000_000
            sig['supported'] = sig.close_ns.map(history).fillna(False)
    return ticker,sig,raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers',type=int,default=2)
    args = parser.parse_args()
    roster = [j for j in jobs() if j['group']!='spot_gold']
    if len(roster)!=632 or not (SOURCE/'manifest.json').exists():
        raise ValueError('Incomplete native market collection or model universe')
    src = pd.read_csv(SOURCE/'coverage.csv.gz',dtype={'symbol':str}).fillna('')
    files = {}
    for row in src.itertuples():
        path = SOURCE/'series'/(row.contract_period_id+'.csv.gz')
        if path.is_file() and 'separate_period_no_continuity_claim' not in row.period_link_status:
            if row.symbol in files:
                raise ValueError(f'Duplicate original contract identity {row.symbol}')
            files[row.symbol] = path
    btc = next(j for j in roster if j['symbol']=='BTCUSDT')
    frame,_,_ = make_frame(btc,files['BTCUSDT'])
    WORK.mkdir(exist_ok=True,parents=True)
    ctx_file = WORK/'btc_native_ctx.npz'
    np.savez(ctx_file,**btc_ctx(frame))
    predictions,raw = [],[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for n,(_,sig,activity) in enumerate(pool.map(extract,[(j,files.get(j['symbol']),ctx_file) for j in roster]),1):
            if len(sig):
                predictions.append(sig)
            if len(activity):
                raw.append(activity)
            if n%80==0:
                print(f'Built native features for {n}/{len(roster)} prior score contracts',flush=True)
    rows = pd.concat(predictions,ignore_index=True)
    breadth = pd.concat(raw).groupby(['tf','close_ns','side']).size().rename('n_same_bar')
    rows = rows.join(breadth,on=['tf','close_ns','side']).set_index('signal_id',verify_integrity=True)
    booster = lgb.Booster(model_file=str(ROOT/CFG['booster']))
    if list(keys.FEATURES)!=booster.feature_name() or rows.n_same_bar.isna().any():
        raise ValueError('Frozen predictor or contemporaneous 632-contract signal count changed')
    ready = rows.supported & rows.btc_ret_24h.notna() & rows.btc_side.notna()
    rows['score'] = np.nan
    rows.loc[ready,'score'] = booster.predict(rows.loc[ready,booster.feature_name()],num_threads=1)
    ref = pd.read_csv(MODEL/'scores_oos.csv.gz',usecols=['signal_id','tf','score'])
    ref = ref[ref.tf.eq('12H')].set_index('signal_id',verify_integrity=True)
    test = rows[rows.close_ns.lt(UNTIL*1_000_000)&ready].join(ref.score.rename('published'),how='inner')
    if len(test)==0:
        raise ValueError('No reconstructed probabilities to compare before the extension')
    diff = (test.score-test.published).abs()
    test[['score','published','close_ns','n_same_bar']].assign(abs_difference=diff).to_csv(EXP/'score_parity.csv.gz')
    rows[['score','close_ns','side','n_same_bar','supported']].to_csv(EXP/'scores.csv.gz')
    summary = dict(common_prefix=len(test),equal_probabilities=int(diff.le(1.e-10).sum()),
                   max_abs=float(diff.max()),post_overlap_keys=int(rows.close_ns.ge(UNTIL*1_000_000).sum()),
                   unsupported_post_overlap=int((rows.close_ns.ge(UNTIL*1_000_000)&~ready).sum()),
                   same_calendar_booster=True)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()
