"""Check correlation to the live nine-position book before admitting a V1.7 12H order."""
import gc
import json
import zlib
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as binance
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds
from yoyo.evaluation import cloud_close_volatility_expansion as market

wallet = thirds.wallet
EXP = wallet.ROOT/'experiments/active/exp-cloud-close-book-correlation-20261109-v1'
BAR = 12*wallet.HOUR


def observable_returns(venue):
    """Normalize 60 prior log returns using 61 native 12H closes; end before the key candle."""
    @lru_cache(maxsize=None)
    def bars(symbol):
        frame = market.market_bars(venue,symbol)
        return frame.index.to_numpy(),frame.close.to_numpy()
    @lru_cache(maxsize=50000)
    def window(symbol, t):
        clock,close = bars(symbol)
        last = t-2*BAR   # bar opens here, closes at the key open t-12h
        pos = int(clock.searchsorted(last))
        if pos<60 or pos==len(clock) or clock[pos]!=last or clock[pos-60]!=last-60*BAR:
            return None
        values = np.diff(np.log(close[pos-60:pos+1]))
        centered = values-values.mean()
        norm = np.linalg.norm(centered)
        return centered/norm if np.isfinite(norm) and norm>0 else None
    return window


def select(paths, window, mode):
    ordered = paths.loc[paths.tp_r.eq(0)].copy()
    ordered['priority'] = ordered.symbol.map(lambda symbol:zlib.crc32(symbol.encode()))
    ordered = ordered.sort_values(['entry_ms','entry_order','priority','symbol','event_id'])
    active = {}
    picks = []
    skips = dict(same_contract=0,full=0,unknown=0,correlated=0)
    for event in ordered.itertuples():
        active = {slot:p for slot,p in active.items() if p.exit_bar_ms>=event.entry_ms}
        if any(p.symbol==event.symbol for p in active.values()):
            skips['same_contract'] += 1
            continue
        if len(active)==9:
            skips['full'] += 1
            continue
        maximum = np.nan
        if mode!='unchanged':
            source = window(event.symbol,event.entry_ms)
            peers = [window(p.symbol,event.entry_ms) for p in active.values()]
            if source is None or any(x is None for x in peers):
                skips['unknown'] += 1
                continue
            if peers:
                maximum = max(float(np.dot(source,x))*event.side*p.side for x,p in zip(peers,active.values()))
            if mode=='bounded' and maximum>.75:
                skips['correlated'] += 1
                continue
        slot = next(i for i in range(9) if i not in active)
        active[slot] = event
        picks.append((event.event_id,slot,maximum))
    selected = pd.DataFrame(picks,columns=['event_id','slot','max_signed_correlation']).set_index('event_id')
    # Dropping the vetoed applicants must recover these same physical slots under the original adapter.
    check_paths = ordered if mode=='unchanged' else ordered[ordered.event_id.isin(selected.index)]
    fixed,_ = wallet.admit(check_paths,9)
    slots = fixed.set_index('event_id').slot
    if not selected.slot.equals(slots):
        raise ValueError('Correlation admissions disagree with native slot priority')
    return selected,skips


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Frozen correlation factor already evaluated')
    table = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    rows = table[table.scenario.eq('actual')].assign(policy='unfiltered').to_dict('records')
    summary = {}
    for venue in ['OKX','Binance']:
        if venue=='OKX':
            paths = thirds.okx_paths()
            repair.check_parent_sets(paths)
        else:
            paths = binance.assembled(set(binance.patch.affected_tickers()))
        actual = paths[~paths.is_control]
        ctl = paths[paths.is_control & paths.parent_id.isin(actual.parent_id)]
        paired = actual[actual.parent_id.isin(ctl.parent_id)]
        window = observable_returns(venue)
        original,_ = select(actual,window,'unchanged')
        expected = 1569 if venue=='OKX' else 1629
        if len(original)!=expected:
            raise ValueError('Original admission count differs from frozen account')
        summary[venue] = dict(original_positions=len(original))
        for tag,pool,mode in [('coverage',actual,'available'),('actual',actual,'bounded'),
                              ('paired',paired,'bounded'),('control',ctl,'bounded')]:
            selected,counts = select(pool,window,mode)
            eligible = pool[pool.event_id.isin(selected.index)]
            metrics,tr,eq = market.fund(venue,eligible,0.)
            if set(selected.index)!=set(tr.index):
                raise ValueError('Expected and funded event identities differ')
            rows.extend(dict(policy='book_correlation',venue=venue,scenario=tag,period=p,**m) for p,m in metrics.items())
            summary[venue][tag] = dict(skips=counts,funded=len(tr),dev_pass=second.meets(metrics['dev']),full_pass=second.meets(metrics['all']))
            if tag=='actual':
                tr.join(selected).to_csv(EXP/f'funded_{venue}.csv.gz')
                eq.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
                stress = market.fund(venue,eligible,.0005)[0]
                rows.extend(dict(policy='book_correlation',venue=venue,scenario='stress',period=p,**m) for p,m in stress.items())
            del tr,eq
            gc.collect()
    frame = pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')][['venue','scenario','policy','trades','final_usdt','win_pct','payoff_usdt','dd_bound_pct']].to_string(index=False))


if __name__ == '__main__':
    main()
