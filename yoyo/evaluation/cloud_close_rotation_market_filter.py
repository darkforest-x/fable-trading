"""Gate an existing relative cloud leader portfolio with the BTC/USD completed 12H cloud."""
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_relative_cloud_leaders as source

EXP=source.EXP.parent/'exp-cloud-close-rotation-market-filter-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed relative leader market regime test')
    loaded,closes=source.clocks_and_prices()
    saved=pd.read_csv(source.EXP/'decisions.csv.gz',index_col='earliest_entry_ms')
    trend=source.spread.trend(closes.BTC.dropna())
    aligned=trend.reindex(saved.index)
    delayed=pd.Series(trend.reindex(saved.index-30*24*source.spread.wallet.HOUR).to_numpy(),index=saved.index)
    coverage=aligned.notna() & delayed.notna()
    signals=pd.DataFrame(index=saved.index)
    masks={'common':coverage,'market_bull':coverage & aligned.gt(0),
           'market_bear':coverage & aligned.lt(0),'stale_bull':coverage & delayed.gt(0)}
    cases={}
    for name,mask in masks.items():
        signals[name]=saved.leader.where(mask,-1).astype(int)
        for slip in [0.,.0005]:
            key=name+('_stress' if slip else '')
            cases[key]=source.rotation.run(loaded,signals,name,slip,symbols=source.SYMBOLS,directory=EXP)
            print(key,cases[key]['metrics']['all'],flush=True)
    signals.assign(btc_cloud=aligned,btc_cloud_30d_earlier=delayed).to_csv(EXP/'decisions.csv.gz')
    source.rotation.source._write_json(EXP/'summary.json',dict(arms=cases,
        common_decisions=int(coverage.sum()),active_leader_decisions=int((coverage & saved.leader.ge(0)).sum())))


if __name__=='__main__':
    main()
