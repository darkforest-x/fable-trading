"""Allow a funded V1.7 re-entry after a previous funded first key has fully stopped out."""
import json
import zlib
from collections import Counter

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as split
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds

EXP = wallet.ROOT/'experiments/active/exp-cloud-close-stopped-reentry-20261109-v1'
SLOTS = 9


def admit(paths):
    runner = paths[paths.tp_r.eq(0)].copy()
    target = paths[paths.tp_r.eq(2)].set_index('event_id',drop=False)
    if (not target.index.is_unique or runner.event_id.duplicated().any()
            or set(target.index)!=set(runner.event_id)):
        raise ValueError('Each opportunity needs two uniquely identified legs')
    runner['crc'] = runner.symbol.map(lambda s:zlib.crc32(s.encode()))
    ordered = runner.sort_values(['entry_ms','entry_order','crc','symbol','event_id'])
    funded_primary = {}
    until, symbols = np.full(SLOTS,-1,dtype=np.int64), np.full(SLOTS,'',dtype=object)
    picks, counts = [], Counter()
    for r in ordered.itertuples():
        if r.is_second:
            original = funded_primary.get(r.parent_id)
            if original is None:
                counts['original_never_funded']+=1
                continue
            if original.exit_bar_ms >= r.entry_ms:
                counts['original_still_open_at_entry_phase']+=1
                continue
            # At this point the original runner (and therefore its partial) has closed.
            if (original.exit_reason not in ('stop','stop_gap_open')
                    or target.at[original.event_id,'exit_reason'] not in ('stop','stop_gap_open')):
                counts['original_did_not_stop_both_legs']+=1
                continue
        busy = until>=r.entry_ms
        if (busy & (symbols==r.symbol)).any():
            counts['symbol_busy']+=1
            continue
        free = np.flatnonzero(~busy)
        if not len(free):
            counts['slots_busy']+=1
            continue
        if not r.is_second:
            funded_primary[r.parent_id]=r
        i = free[0]
        until[i],symbols[i] = r.exit_bar_ms,r.symbol
        picks.append((r.Index,i))
        counts['secondary_funded' if r.is_second else 'primary_funded']+=1
    selected = pd.DataFrame(picks,columns=['idx','slot']).set_index('idx')
    last = runner.loc[selected.index].copy()
    first = target.loc[last.event_id].copy()
    if np.any(first.exit_bar_ms.to_numpy()>last.exit_bar_ms.to_numpy()):
        raise ValueError('Partial exited later than runner')
    first['slot'],last['slot'] = 2*selected.slot.to_numpy(),2*selected.slot.to_numpy()+1
    legs = pd.concat([first,last],ignore_index=True)
    return wallet.prepare(None,SLOTS*2,assigned=(legs,dict(counts))),dict(counts)


def fund(primary, extra, slip=0.):
    rows = pd.concat([primary.assign(is_second=False),extra.assign(is_second=True)],ignore_index=True)
    data, counts = admit(rows)
    metrics,trades,equity = split.simulate(data,2/3,slip)
    if not np.isclose(1000+trades.cash_pnl.sum(),equity.nav.iloc[-1],rtol=1e-8,atol=.005):
        raise ValueError('Cash failed to reconcile with closed positions')
    trades['is_second'] = trades.index.isin(extra.event_id)
    if trades.is_second.sum()!=counts.get('secondary_funded',0):
        raise ValueError('An admitted secondary position was not funded')
    return metrics,trades,equity,counts


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Already evaluated')
    base = thirds.okx_paths()
    repair.check_parent_sets(base)
    pri = base[~base.is_control]
    secondary = pd.read_csv(second.EXP/'paths.csv.gz',dtype={'symbol':str},keep_default_na=False,float_precision='round_trip')
    extra, ctl = second.split_events(secondary)
    # Verify the no-extra admission decisions directly, without re-running the frozen account.
    old = pd.read_csv(repair.EXP/'thirds'/'funded_OKX.csv.gz')
    frozen,_ = wallet.admit(pri[pri.tp_r.eq(0)],SLOTS)
    if set(frozen.event_id)!=set(old.event_id):
        raise ValueError('Primary candidates differ from the frozen funded comparator')
    original = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    reference = original[original.venue.eq('OKX') & original.scenario.eq('actual')]
    records = reference.assign(policy='primary_only').to_dict('records')
    metrics, trades, equity, counts = fund(pri,extra)
    trades.to_csv(EXP/'funded.csv.gz')
    equity.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
    records.extend(dict(venue='OKX',policy='stopped_reentry',scenario='actual',period=p,**m) for p,m in metrics.items())
    dev,full = second.meets(metrics['dev']),second.meets(metrics['all'])
    checks = {}
    if dev:
        for label,p,slip in [('paired_real',extra[extra.parent_id.isin(ctl.parent_id)],0.),
                             ('paired_control',ctl,0.),('stress',extra,.0005)]:
            results,_,_,checks[label] = fund(pri,p,slip)
            records.extend(dict(venue='OKX',policy='stopped_reentry',scenario=label,period=k,**m) for k,m in results.items())
    table = pd.DataFrame(records)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    ledger = trades[trades.is_second]
    summary = dict(dev_pass=dev,full_pass=full,affected_primary_count=len(set(old.event_id)^set(trades[~trades.is_second].index)),
                   secondary_closed_cash_pnl=float(ledger.cash_pnl.sum()),secondary_wins=int(ledger.cash_pnl.gt(0).sum()),
                   account=counts,checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
