"""Add to a funded native V1.7 reclaim on a subsequent independent 12H breakout."""
import json
import zlib

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_breakout_branch as branch
from yoyo.evaluation import cloud_close_weak_rally_branch as market

wallet = branch.wallet
EXP = branch.EXP.parent/'exp-cloud-close-confirmed-winner-add-20261109-v1'


def confirmation_marks(events):
    """Join a breakout to that symbol's native OHLC close at its exact 12H key."""
    keys = events[events.arm.eq('breakout')]
    src = market.sources()
    marks = {}
    for sym,group in keys.groupby('symbol'):
        _,bars = market.frames(src[sym])
        px = bars.close.reindex(group.key_open_ms.to_numpy())
        if px.isna().any():
            raise ValueError(f'Missing confirmed breakout close for {sym}')
        marks.update(zip(group.event_id,px.to_numpy()))
    return marks


def admission(events,marks,allow_add):
    ordered = events.copy()
    ordered['is_breakout'] = ordered.arm.eq('breakout')
    ordered['crc'] = ordered.symbol.map(lambda s:zlib.crc32(s.encode()))
    ordered = ordered.sort_values(['entry_ms','is_breakout','crc','symbol','event_id'])
    held = [None]*9
    admitted,used = [],set()
    skips = {}
    for r in ordered.itertuples():
        stamp = int(r.entry_ms)
        held = [h if h is not None and h.exit_bar_ms>=stamp else None for h in held]
        same = [h for h in held if h is not None and h.symbol==r.symbol]
        reason = None
        parent = r.event_id
        if not r.is_breakout:
            if same:
                reason = 'contract_occupied'
        elif not allow_add:
            continue
        else:
            base = next((h for h in same if not h.is_breakout and h.side==r.side and h.entry_ms<stamp and
                        (h.exit_bar_ms>stamp or h.exit_phase!='open')),None)
            if base is None:
                reason = 'no_live_original_same_direction'
            elif base.event_id in used:
                reason = 'already_added'
            else:
                px = marks[r.event_id]
                pnl_per_unit = r.side*(px-base.entry_price)-wallet.TAKER*(px+base.entry_price)
                if pnl_per_unit<=0:
                    reason = 'original_not_profitable_at_signal_close'
                parent = base.event_id
        if reason is None and None not in held:
            reason = 'all_slots_busy'
        if reason:
            skips[reason] = skips.get(reason,0)+1
            continue
        slot = held.index(None)
        held[slot] = r
        admitted.append((r.Index,slot,parent))
        if r.is_breakout:
            used.add(parent)
    idx = pd.DataFrame(admitted,columns=['row','slot','campaign']).set_index('row')
    return ordered.loc[idx.index].assign(slot=idx.slot,campaign=idx.campaign),skips


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed confirmed-winner account')
    cfg = json.loads((branch.HISTORICAL_EXP/'config.json').read_text())
    f = branch.HISTORICAL_EXP/cfg['outputs']['paths_csv_gz']
    raw = pd.read_csv(f,dtype={'symbol':str},float_precision='round_trip')
    events = raw[~raw.is_control & raw.status.eq('executed')].copy()
    marks = confirmation_marks(events)
    quotes = branch._quote_loader(cfg)
    original,_ = admission(events,marks,False)
    frozen = pd.read_csv(branch.HISTORICAL_EXP/'funded/funded_reclaim_actual.csv.gz',usecols=['event_id','slot'])
    compare = original[['event_id','slot']].sort_values('event_id').reset_index(drop=True)
    if not compare.equals(frozen.sort_values('event_id').reset_index(drop=True)):
        raise ValueError('Reference slot-by-slot reclaim admissions failed to reproduce')
    selected,skips = admission(events,marks,True)
    data = wallet.prepare(None,9,assigned=(selected,skips),quote_loader=quotes)
    result = {}
    for name,slip in [('actual',0.),('extra_5bp',.0005)]:
        funds,eq = wallet.account(data,slip=slip)
        if not np.isclose(1000+funds.cash_pnl.sum(),eq.nav.iat[-1],rtol=1e-9,atol=.005):
            raise ValueError('Winner-add cash did not reconcile')
        parents = funds.groupby('campaign').agg(entry_ms=('entry_ms','min'),exit_bar_ms=('exit_bar_ms','max'),
                                                  cash_pnl=('cash_pnl','sum'),notional=('notional','sum'))
        parents['net_return'] = parents.cash_pnl/parents.notional
        funded_adds = funds[funds.is_breakout]
        if (not funded_adds.campaign.is_unique or not set(funded_adds.campaign).issubset(set(funds.loc[~funds.is_breakout,'event_id']))):
            raise ValueError('Funded add has no funded original or exceeds the addition cap')
        result[name] = dict(parent_metrics={p:wallet.summarize(parents,eq,*bounds) for p,bounds in branch.PERIODS.items()},
            leg_metrics=wallet.summarize(funds,eq),funded_adds=len(funded_adds),addon_cash_pnl=funded_adds.cash_pnl.sum(),
            unquoted_position_hours=data['gap_marks'])
        funds.to_csv(EXP/f'funded_{name}.csv.gz',index=False)
        eq.to_csv(EXP/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
        print(name,result[name]['parent_metrics']['all'],flush=True)
    baseline = json.loads((branch.HISTORICAL_EXP/'summary.json').read_text())['accounts']['reclaim']['actual']['metrics']
    branch._write_json(EXP/'summary.json',dict(baseline=baseline,cases=result,admission_skips=skips))


if __name__=='__main__':
    main()
