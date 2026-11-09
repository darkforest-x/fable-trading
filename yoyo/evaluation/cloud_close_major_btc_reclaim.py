"""Apply the frozen ETH/BTC relative reclaim to the four actual fills of a market-neutral round trip."""
import json
import zlib

import pandas as pd

from yoyo.evaluation import cloud_close_eth_btc_reclaim as pair

EXP=pair.EXP.parent/'exp-cloud-close-major-btc-reclaim-20261109-v1'


def main(*,directory=EXP,stop_clock=pair.H):
    if (directory/'summary.json').exists():
        raise FileExistsError('Completed major/BTC reclaim experiment')
    records=[]
    for symbol in pair.spread.SYMBOLS:
        if symbol=='BTC':
            continue
        ratio=pair.observed_ratio(symbol)
        signals,ema=pair.entries(ratio)
        events=pair.paths(signals,ratio,ema,asset=symbol,overlaps=True,stop_clock=stop_clock)
        events['event_id']=symbol+'|'+events.event_id.astype(str)
        events['parent_id']=events.event_id
        records.append(events)
    all_events=pd.concat(records,ignore_index=True)
    parents=all_events[all_events.slot.eq(0)].copy()
    parents['order']=parents.symbol.map(lambda name:zlib.crc32(name.encode()))
    busy=-1
    picked=[]
    for row in parents.sort_values(['entry_ms','order','symbol']).itertuples():
        if row.entry_ms>busy:
            picked.append(row.event_id)
            busy=row.exit_bar_ms
    chosen=all_events[all_events.event_id.isin(picked)].copy()
    chosen.to_csv(directory/'paths.csv.gz',index=False)
    parents[['entry_ms','symbol','side','order','event_id']].to_csv(directory/'confirmed_keys.csv.gz',index=False)
    results={}
    for paired in [True,False]:
        legs=chosen if paired else chosen[chosen.slot.eq(0)]
        bad=legs[legs.margin_buffer.le(0)]
        if len(bad):
            results['pair' if paired else 'asset_half']=dict(unresolved_collateral=bad.event_id.unique().tolist())
            continue
        for slip in [0.,.0005]:
            name,case=pair.fund(chosen,paired,slip,directory=directory,asset_label='asset_half')
            results[name]=case
    out=dict(total_keys=len(parents),funded_pairs=len(picked),funded_by_coin=chosen[chosen.slot.eq(0)].symbol.value_counts().to_dict(),
             cases=results,scope='Nine named surviving liquid altcoins versus BTC; history is not a complete venue census.')
    (directory/'summary.json').write_text(json.dumps(out,indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--confirmed-stop',action='store_true')
    if parser.parse_args().confirmed_stop:
        main(directory=EXP.parent/'exp-cloud-close-major-ratio-stop-20261109-v1',stop_clock=pair.STEP)
    else:
        main()
