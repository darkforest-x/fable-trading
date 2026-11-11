"""Retest the frozen winners under the prior study's stable CRC32 symbol priority.

The adapter sorts on symbol when several entries share a five-minute open.
A one-to-one key prefix changes only that tie break and keeps repeated-symbol
exclusions intact. The signal times, prices and account sizes are frozen.
"""
import json
import zlib
import pandas as pd
from run_research import CACHE, CFG, HERE, account_result


def alternate_priority(symbol):
    key = zlib.crc32(symbol.encode('utf-8'))
    return f'{key:08x}|{symbol}'


def main():
    select = json.loads((HERE/'selected.json').read_text())
    result = []
    for strategy,fraction in sorted({(x['strategy'],x['fraction']) for x in select.values()}):
        p=CACHE/f"{strategy}__signal__{CFG['base_market_slippage_bps']}bp"
        fills,marks=(pd.read_parquet(p/(name+'.parquet')) for name in ('fills','marks'))
        lookup = {s:alternate_priority(s) for s in fills.symbol.unique()}
        if len(set(lookup.values())) != len(lookup):
            raise ValueError('symbol key collision')
        fills['symbol']=fills.symbol.map(lookup)
        marks['symbol']=marks.symbol.map(lookup)
        for period in ('early','late','full'):
            summary=account_result(fills,marks,period,fraction)['summary']
            result.append(dict(summary,strategy=strategy,fraction=fraction,tiebreak='CRC32(symbol), then symbol'))
    pd.DataFrame(result).to_csv(HERE/'simultaneous_entry_order.csv',index=False)


if __name__=='__main__':
    main()
