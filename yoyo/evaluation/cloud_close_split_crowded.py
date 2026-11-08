"""Split scarce simultaneous 12H entry capacity across up to two contracts per original slot."""
import gc
import json
import zlib
from collections import Counter

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as repaired
from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds

EXP = wallet.ROOT/'experiments/active/exp-cloud-close-split-crowded-20261109-v1'
HALF_LOTS = 18


def admissions(runners, allow_halves):
    """Use only the current 12H batch, original prior ATR and strictly earlier observed exits."""
    order = runners.assign(crc=runners.symbol.map(lambda s:zlib.crc32(s.encode()))).sort_values(
        ['entry_ms','entry_order','crc','symbol','event_id'])
    until = np.full(HALF_LOTS,-1,dtype=np.int64)
    names = np.full(HALF_LOTS,'',dtype=object)
    placed,counts = [],Counter()
    for time,group in order.groupby('entry_ms',sort=False):
        busy = until>=time
        free = np.flatnonzero(~busy)
        available = group[~group.symbol.isin(names[busy])]
        if available.symbol.duplicated().any():
            raise ValueError('Multiple same-contract candidates in one batch')
        n = min(len(available),len(free) if allow_halves else len(free)//2)
        doubled = min(n,len(free)-n) if allow_halves else n
        counts['busy_contract']+=len(group)-len(available)
        counts['no_capacity']+=len(available)-n
        if 2*len(available)>len(free) and n:
            counts['oversubscribed_opening_batches']+=1
        offset=0
        for rank,event in enumerate(available.iloc[:n].itertuples()):
            lots = 2 if rank<doubled else 1
            counts['two_lot_positions' if lots==2 else 'one_lot_positions']+=1
            for slot in free[offset:offset+lots]:
                until[slot], names[slot] = event.exit_bar_ms,event.symbol
                placed.append((event.event_id,int(slot)))
            offset+=lots
    return pd.DataFrame(placed,columns=['event_id','physical']),dict(counts)


def account(venue,pool,allow_halves,slip=0.):
    runner, target = (pool[pool.tp_r.eq(x)].set_index('event_id',drop=False) for x in (0,2))
    picks,counts = admissions(runner.reset_index(drop=True),allow_halves)
    if not runner.index.is_unique or not target.index.is_unique:
        raise ValueError('Ambiguous native paths')
    late,early = runner.loc[picks.event_id].copy(),target.loc[picks.event_id].copy()
    if np.any(early.exit_bar_ms.to_numpy()>late.exit_bar_ms.to_numpy()):
        raise ValueError('Partial outlasts the runner')
    early['slot'],late['slot'] = 2*picks.physical.to_numpy(),2*picks.physical.to_numpy()+1
    terms = {} if venue=='OKX' else dict(start=repaired.venue.START,end=repaired.venue.END,quote_loader=repaired.quotes)
    legs = pd.concat([early,late],ignore_index=True)
    data = wallet.prepare(None,2*HALF_LOTS,assigned=(legs,counts),**terms)
    filled,equity = wallet.account(data,slip=slip,weights=(2/3,1/3))
    g = filled.groupby('event_id',sort=False)
    positions = g.agg(symbol=('symbol','first'),parent_id=('parent_id','first'),side=('side','first'),
                      entry_ms=('entry_ms','first'),exit_bar_ms=('exit_bar_ms','max'),notional=('notional','sum'),
                      cash_pnl=('cash_pnl','sum'))
    positions['net_return']=positions.cash_pnl/positions.notional
    positions['half_lots']=(g.size()//2).astype(int)
    if not positions.half_lots.isin([1,2]).all() or not np.isclose(1000+positions.cash_pnl.sum(),equity.nav.iloc[-1],atol=.005,rtol=1e-8):
        raise ValueError('Lot aggregation failed')
    periods={'all':(None,None),'dev':(None,'2025-01-01T00:00Z'),'2025':('2025-01-01T00:00Z','2026-01-01T00:00Z'),'2026':('2026-01-01T00:00Z',None)}
    metrics={k:wallet.summarize(positions,equity,*bounds) for k,bounds in periods.items()}
    return metrics,positions,equity,counts


def check_parity(venue,real):
    # This adapter has altered position representation, so test it against the frozen account.
    _,neutral,curve,_=account(venue,real,False)
    root=repair.EXP/'thirds' if venue=='OKX' else thirds.EXP
    known=pd.read_csv(root/f'funded_{venue}.csv.gz',index_col='event_id')
    equity=pd.read_csv(root/f'hourly_{venue}.csv.gz',index_col=0)
    if set(known.index)!=set(neutral.index) or not neutral.half_lots.eq(2).all():
        raise ValueError(f'{venue}: neutral half-lot representation changed admissions')
    pnl=(neutral.loc[known.index,'cash_pnl']-known.cash_pnl).abs().max()
    nav=np.max(np.abs(curve.to_numpy()-equity[curve.columns].to_numpy()))
    if pnl>.005 or nav>.005:
        raise ValueError(f'{venue}: neutral account diverged more than half a cent: {pnl} / {nav}')
    return dict(max_pnl_error_usdt=pnl,max_curve_error_usdt=nav)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Crowded allocation is frozen')
    original=pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    rows=original[original.scenario.eq('actual')].assign(policy='original').to_dict('records')
    summary={}
    for venue in ['OKX','Binance']:
        if venue=='OKX':
            all_paths=thirds.okx_paths()
            repair.check_parent_sets(all_paths)
        else:
            all_paths=repaired.assembled(set(repaired.patch.affected_tickers()))
        real=all_paths[~all_paths.is_control]
        ctrl=all_paths[all_paths.is_control & all_paths.parent_id.isin(real.parent_id)]
        parity=check_parity(venue,real)
        vals,pos,eq,counts=account(venue,real,True)
        pos.to_csv(EXP/f'funded_{venue}.csv.gz')
        eq.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
        rows.extend(dict(venue=venue,policy='split_crowded',scenario='actual',period=k,**v) for k,v in vals.items())
        dev=second.meets(vals['dev'])
        summary[venue]=dict(parity=parity,counts=counts,dev_pass=dev,full_pass=second.meets(vals['all']))
        del eq,pos
        gc.collect()
        if dev:
            for lab,p,slip in [('paired',real[real.parent_id.isin(ctrl.parent_id)],0.),('control',ctrl,0.),('stress',real,.0005)]:
                checks=account(venue,p,True,slip)[0]
                rows.extend(dict(venue=venue,policy='split_crowded',scenario=lab,period=k,**v) for k,v in checks.items())
    table=pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
