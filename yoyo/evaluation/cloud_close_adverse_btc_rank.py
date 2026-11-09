"""Rank concurrent cloud entries by directional resilience on adverse BTC days."""
import gc
import json
import zlib

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_alt_season_delivery as deliveries
from yoyo.evaluation import cloud_close_cvc_regime as reference

repair = reference.repair
wallet = repair.wallet
EXP = reference.EXP.parent/'exp-cloud-close-adverse-btc-rank-20261109-v1'
DAY = 24*wallet.HOUR


def daily_returns(symbol, quotes):
    """Use hourly close on complete UTC days, requiring both adjacent days in full."""
    times, prices = quotes(symbol)
    frame = pd.Series(prices[:, 3], index=times)
    if not frame.index.is_unique or not frame.index.is_monotonic_increasing or (times % wallet.HOUR).any():
        raise ValueError(f'Off-grid or duplicate native prices for {symbol}')
    grouped = frame.groupby(times//DAY).agg(['last', 'size'])
    days = grouped['last'].where(grouped['size'].eq(24))
    days = days.reindex(range(days.index.min(), days.index.max()+1))
    ret = days.div(days.shift()).sub(1)
    ret.index = (ret.index+1)*DAY
    return ret


def score(book, quotes):
    """Previous 30 completed daily coin/BTC close returns, conditional on a 2% adverse BTC move."""
    benchmark = daily_returns('BTC', quotes)
    samples = []
    for sym, group in book.groupby('symbol'):
        ret = daily_returns(sym, quotes).reindex(benchmark.index)
        available = (ret.notna() & benchmark.notna()).rolling(30).sum().eq(30)
        for side, parents in group.groupby('side'):
            adverse = side*benchmark <= -.02
            count = adverse.rolling(30).sum()
            excess = ((ret-benchmark)*side).where(adverse, 0).rolling(30).sum()
            strength = excess.div(count.clip(lower=1)).where(available)
            stamp = parents.entry_ms//DAY*DAY
            samples.append(parents.assign(score=stamp.map(strength), adverse_days=stamp.map(count), daily_close_ms=stamp))
    return pd.concat(samples, ignore_index=True)


def settle(tape, src):
    """Reuse known settlement minutes only at the exact venue delivery time."""
    price_sources = [repair.old.DELIVERY/'settlement_index_minutes.csv', *deliveries.EXP.glob('index_*.csv')]
    cached = pd.concat([pd.read_csv(p) for p in price_sources], ignore_index=True)
    tape = tape.copy()
    tails = tape.exit_reason.eq('series_tail_close') & tape.exit_bar_ms.lt(wallet.END-wallet.HOUR)
    metadata = {}
    cfg = json.loads((repair.old.DELIVERY/'config.json').read_text())
    for symbol, rows in tape.loc[tails].groupby('symbol'):
        end = src[symbol][2]
        if not rows.exit_bar_ms.eq(end-wallet.HOUR).all() or end >= wallet.END:
            raise ValueError(f'Unverified preterminal exit for {symbol}')
        series = cached[(cached.symbol==symbol) & cached.time.between(end-wallet.HOUR, end-1)].drop_duplicates('time').sort_values('time')
        if len(series)!=60:
            path = EXP/f'index_{symbol}.csv'
            if not path.exists():
                series = deliveries.delivery.index_data(dict(cfg,symbols=[symbol]),end,directory=None)
                series.to_csv(path,index=False)
            series = pd.read_csv(path)
        if not np.array_equal(series.time,np.arange(end-wallet.HOUR,end,60000)):
            raise ValueError(f'Incomplete ending minute prices for {symbol}')
        mean = float(series.close.mean())
        tape.loc[rows.index,['exit_price','exit_reason','exit_fee_rate']] = [mean,'delisting_settlement',0.]
        metadata[symbol] = dict(mean_index=mean,legs=len(rows))
    return tape,metadata


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed native adverse BTC ranking')
    rows,quotes,src,_ = repair.pool()
    cols = ['event_id','symbol','side','entry_ms','entry_order']
    parents = rows.loc[rows.tp_r.eq(0),cols].copy()
    expected = set(pd.read_csv(repair.EXP/'account/funded_1.5x.csv.gz',usecols=['event_id']).event_id)
    base,_ = wallet.admit(rows[rows.tp_r.eq(0)],9)
    if set(base.event_id)!=expected or not parents.event_id.is_unique:
        raise ValueError('Original admission no longer reconciles')
    scores = score(parents,quotes)
    scores['clock_hash'] = [zlib.crc32(f'61|{t}|{s}'.encode()) for t,s in zip(scores.entry_ms,scores.symbol)]
    scores['prior_atr'] = scores.entry_order
    scores.to_csv(EXP/'opportunities.csv.gz',index=False)
    state = reference.study.season.direction(src)
    results = {}
    for label in ['prefer_resilient','prefer_fragile','clock_hash_61']:
        frame = scores.copy()
        feature = 'clock_hash' if label=='clock_hash_61' else 'score'
        frame = frame.sort_values(['entry_ms',feature,'prior_atr'],ascending=[True,label!='prefer_resilient',True],kind='stable')
        # Equal scores and equal prior ATR retain the framework's original CRC tie breaker.
        keys = pd.MultiIndex.from_frame(frame[['entry_ms',feature,'prior_atr']])
        frame['entry_order'] = keys.factorize(sort=False)[0]
        ranked = rows.copy()
        ranked['entry_order'] = ranked.event_id.map(frame.set_index('event_id').entry_order)
        roster,skips = wallet.admit(ranked[ranked.tp_r.eq(0)],9)
        tape,delivery = settle(ranked[ranked.event_id.isin(roster.event_id)],src)
        directory = EXP/label
        directory.mkdir(exist_ok=True)
        repair.parent.save_execution_tape(tape,directory)
        # Use current native prices also for newly admitted parents.
        data,_ = repair.old.parent.assemble(tape,repair.old.changed_limits(tape,quotes),'OKX',quotes)
        market = reference.engine.base.underlying(data,quotes)
        positions = market[-1].set_index('event_id')
        ratios = 1.5-.5*positions.side*positions.entry_ms.map(state)
        if ratios.isna().any() or set(positions.index)!=set(roster.event_id):
            raise ValueError('Exposure clock or roster changed')
        cases = {}
        for slip,name in [(0.,'fees'),(.0005,'stress')]:
            cases[name] = reference.engine.simulate(data,market,quotes,ratios,slip,directory,name)
            print(label,name,cases[name].get('metrics',{}).get('all'),flush=True)
            gc.collect()
        results[label] = dict(cases=cases,selected=len(roster),overlap=len(set(roster.event_id)&expected),
                              skips=skips,delivery=delivery,source_gap_events=int(tape[tape.gap_flag].event_id.nunique()),
                              missing_holding_hours=data['gap_marks'])
    repair.archive.prices.safe_json(EXP/'summary.json',dict(arms=results,eligible=len(parents),
             unscored=int(scores.score.isna().sum()),no_adverse_days=int(scores.adverse_days.eq(0).sum()),
             baseline=reference.EXP.name,exclusions=['funding','capacity'],selection='exploratory'))


if __name__=='__main__':
    main()
