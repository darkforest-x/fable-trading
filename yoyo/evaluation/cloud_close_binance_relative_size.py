"""Transfer the ETH/BTC conditional sizing to the previously priced Binance historical account."""
import gc
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_verified_exposure as engine

base = engine.base
bundle = base.parent
archive = bundle.admin.archive
EXP = engine.EXP.parent/'exp-cloud-close-binance-relative-size-20261109-v1'


def make_account(rows,quotes):
    saved = pd.read_csv(bundle.EXP/'doublings_Binance.csv')
    runners = rows[rows.tp_r.eq(0)].set_index('event_id')
    limits = []
    for r in saved.drop_duplicates('event_id').itertuples():
        clock,x = quotes(r.symbol)
        ix = clock.searchsorted(r.limit_ms)
        if clock[ix]!=r.limit_ms or not np.isclose(2*runners.at[r.event_id,'entry_price'],r.price):
            raise ValueError('Missing or altered published Binance doubling fill')
        early = x[ix,0]>=r.price
        limits.append(dict(event_id=r.event_id,exit_bar_ms=r.limit_ms,exit_phase='open' if early else 'intrabar',
                      exit_reason='take_profit_open' if early else 'take_profit',exit_fee_rate=base.wallet.TAKER*.4,
                      exit_price=r.price,same_bar_ambiguous=False))
    data,actually = bundle.assemble(rows,pd.DataFrame(limits),'Binance',quotes)
    if len(actually)!=len(saved):
        raise ValueError('Binance child outcomes differ from their historical receipt')
    return data,base.underlying(data,quotes)


def relative(quotes):
    prices = {}
    for sym in ['BTC','ETH']:
        ts,ohlc = quotes(sym)
        h = pd.DataFrame(ohlc[:,:4],index=pd.Index(ts,name='time'),columns=['open','high','low','close'])
        bars = bundle.price.twelve_hour(h)
        prices[sym] = bars.close
    ratio = prices['ETH'].div(prices['BTC']).dropna()
    state = np.sign(ratio/ratio.shift(60)-1).where(ratio.index.to_series().diff(60).eq(60*12*base.wallet.HOUR))
    state.index += 12*base.wallet.HOUR
    return state


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed matched Binance relative-size transfer')
    extra = set(json.loads((archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((bundle.admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    with archive.market_quotes(extra):
        rows,loader,_ = bundle.paths('Binance')
        quotes = lru_cache(maxsize=None)(loader)
        data,market = make_account(rows,quotes)
        parents = market[-1].set_index('event_id')
        balance = parents.entry_ms.map(relative(quotes))
        if balance.isna().any():
            raise ValueError('Missing native ETH or BTC 30-day window on the Binance funded book')
        sign = balance*parents.side
        result = {}
        for name,k in [('same_size',0.),('contrarian_quarter',.25),('contrarian_half',.5),('momentum_half',-.5)]:
            weights = 1.5-k*sign
            for slip in [0.,.0005]:
                key = name+('_stress' if slip else '')
                result[key] = engine.simulate(data,market,quotes,weights,slip,EXP,key,inspect_flag=None)
                print(key,result[key].get('metrics',{}).get('all'),flush=True)
                gc.collect()
    engine.source.correction.archive.prices.safe_json(EXP/'summary.json',dict(arms=result,native_funded=len(parents),
            caveat='Original Binance scored, retired-inclusive portfolio through September 2026, different history and admission from OKX. H1 risk flags require Binance-specific evidence. Funding not included.'))


if __name__=='__main__':
    main()
