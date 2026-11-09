"""Exit a current relative leader after a 12H close violates the original entry candle low."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_rotation_market_filter as market

source=market.source
EXP=market.EXP.parent/'exp-cloud-close-rotation-entry-low-20261109-v1'


def signals(loaded):
    """Frozen target, twelve completed native H1 lows before original entry, subsequent completed 12H last close."""
    base=pd.read_csv(market.EXP/'decisions.csv.gz',index_col=0).market_bull
    prices={}
    for symbol in source.SYMBOLS:
        # Use full historical inputs for the first launch candle, including H1s before the account start.
        stamps,bars=source.spread.wallet.quotes(symbol)
        table=pd.DataFrame(bars[:,[2,3]],index=stamps+source.spread.wallet.HOUR,columns=['low','close'])
        table['entry_low']=table.low.rolling(12).min().where(table.index.to_series().diff(11).eq(11*source.spread.wallet.HOUR))
        prices[symbol]=table
    choices=pd.DataFrame({'original':base,'confirmed':base,'late':base})
    clocks=[]
    for _,episode in base.groupby(base.ne(base.shift()).cumsum()):
        asset=int(episode.iat[0])
        if asset<0:
            continue
        symbol=source.SYMBOLS[asset]
        marks=prices[symbol].reindex(episode.index)
        threshold=marks.entry_low.iat[0]
        if not np.isfinite(threshold) or marks.close.isna().any():
            raise ValueError('Missing completed H1 input in a funded leader episode')
        hit=np.flatnonzero(marks.close.iloc[1:].to_numpy()<threshold)
        if not len(hit):
            continue
        i=int(hit[0])+1
        clocks.append(dict(symbol=symbol,entry_ms=int(episode.index[0]),cut_ms=int(episode.index[i]),
                           entry_low=float(threshold),decision_close=float(marks.close.iat[i])))
        choices.loc[episode.index[i:],'confirmed']=-1
        choices.loc[episode.index[i+1:],'late']=-1
    return choices,pd.DataFrame(clocks)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed entry-candle invalidation test')
    loaded,_=source.clocks_and_prices()
    choices,clocks=signals(loaded)
    clocks.to_csv(EXP/'cuts.csv.gz',index=False)
    choices.to_csv(EXP/'decisions.csv.gz')
    cases={}
    for arm in ['confirmed','late']:
        for slip in [0.,.0005]:
            name=arm+('_stress' if slip else '')
            cases[name]=source.rotation.run(loaded,choices,arm,slip,symbols=source.SYMBOLS,directory=EXP)
            print(name,cases[name]['metrics']['all'],flush=True)
    source.rotation.source._write_json(EXP/'summary.json',dict(arms=cases,episodes_with_cut=len(clocks)))


if __name__=='__main__':
    main()
