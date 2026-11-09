"""Reprice the actually truncated ICX/LRC signals and refund the shared historical account."""
import json
from functools import lru_cache
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_own_daily_trend as parent
from yoyo.evaluation import cloud_close_notice_tail_prices as sources
from yoyo.evaluation import cloud_close_okx_retired_delivery as delivery

wallet = parent.wallet
archive = parent.archive
EXP = sources.EXP.parent/'exp-cloud-close-notice-tail-wallet-20261109-v1'


def update_symbol(sym,old_rows,ledger,end):
    folder = sources.EXP/sym
    h = pd.read_csv(folder/'bars1h.csv.gz',index_col='time',float_precision='round_trip')
    bars = pd.read_csv(folder/'bars12h.csv.gz',index_col='time',float_precision='round_trip')
    oldh = pd.read_csv(wallet.SOURCE/'inputs'/sym/'bars1h.csv.gz',usecols=['time'])
    old_end = int(oldh.time.iat[-1]+wallet.HOUR)
    keys = archive.official_keys(sym,folder/'bars1h.csv.gz',end,folder/'bars12h.csv.gz')
    known = ledger[ledger.symbol.eq(sym)].set_index(['stamp','side'])
    new_prefix = {(r['entry_ms'],r['side']) for r in keys if r['entry_ms']<=old_end}
    if set(known.index)!=new_prefix:
        raise ValueError(f'Official signal prefix changed for {sym}: missing={set(known.index)-new_prefix}, extra={new_prefix-set(known.index)}')
    affected = set(old_rows.loc[old_rows.exit_reason.eq('series_tail_close') &
                                old_rows.exit_bar_ms.eq(old_end-wallet.HOUR),'event_id'])
    ranks = archive.leadership.trailing_return(bars.reset_index())
    q,clock,_ = archive.native.engine._prepare_symbol(bars,h)
    events,added = [],[]
    for e in keys:
        key = (e['entry_ms'],e['side'])
        new = key not in known.index
        canonical_id = e['event_id'] if new else known.at[key,'original_id']
        if not new and canonical_id not in affected:
            continue
        if pd.isna(ranks.get(e['entry_ms'],np.nan)) or e['entry_ms']>=end:
            continue
        i = int(clock.searchsorted(e['entry_ms']))
        if i>=len(clock) or clock[i]!=e['entry_ms']:
            raise ValueError(f'Missing repaired H1 entry {sym}')
        px = float(q[i,0]);risk = e['side']*(px-e['stop_price'])
        if risk<=0:
            raise ValueError(f'Invalid repaired entry risk {sym}')
        if not new:
            same = old_rows[old_rows.event_id.eq(canonical_id)]
            if not np.allclose(same.entry_price,px) or not np.allclose(same.stop_price,e['stop_price']):
                raise ValueError('Appended prices changed an earlier original entry or stop')
        last,why = archive.native.engine._safe_path_end(i,e['side'],q,clock)
        e.update(event_id=canonical_id,parent_id=canonical_id,confirmation_ms=e['entry_ms'],source_row_number=0,
                 is_control=False,entry_i=i,entry_price=px,risk_distance=risk,risk_fraction=risk/px,end_i=last,path_upper_bound_reason=why)
        events.append(e)
        if new:
            added.append(canonical_id)
    if not affected.issubset({e['event_id'] for e in events}):
        raise ValueError('Missing replacement for a truncated native path')
    changes,_ = archive.native.engine._simulate_symbol(events,q,clock,(0.,2.),64)
    table = pd.DataFrame(changes)
    priority = {r['event_id']:r['entry_order'] for r in events}
    table['entry_order'] = table.event_id.map(priority)
    if not table.status.eq('executed').all():
        raise ValueError('A repaired native candidate failed execution')
    table,_ = archive.risk.hourly.tested.expiry.time_in_force(table)
    return table,affected,added


def pool():
    rows,base_quote,src = parent.source_book()
    ledger = pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['confirmed_bjt','symbol','side'])
    ledger['stamp'] = pd.to_datetime(ledger.confirmed_bjt,utc=True).astype('datetime64[ms, UTC]').astype('int64')
    ledger['original_id'] = [f'signal-row-{i:05d}:signal' for i in range(1,len(ledger)+1)]
    meta = json.loads((sources.EXP/'config.json').read_text())['symbols']
    changed,added = set(),[]
    chunks = []
    for sym,v in meta.items():
        end = pd.Timestamp(v['end']).value//1_000_000
        patched,affected,new = update_symbol(sym,rows[rows.symbol.eq(sym)],ledger,end)
        changed.update(affected);added.extend(new);chunks.append(patched)
        src[sym] = (sources.EXP/sym/'bars1h.csv.gz',sources.EXP/sym/'bars12h.csv.gz',end)
    extras = pd.concat(chunks,ignore_index=True)
    # Every confirmed time in the extra intervals already has at least ten fully
    # observed other peers; adding these prices cannot admit extra non-target names.
    prior_peers = {}
    for sym in ['BTC','ETH','SOL','BNB','XRP','DOGE','ADA','LINK','LTC','AVAX','TRX','DOT']:
        _,bars = parent.repair.market.frames(src[sym])
        prior_peers[sym] = archive.leadership.trailing_return(bars.reset_index()).notna()
    counts = pd.DataFrame(prior_peers).fillna(False).sum(axis=1)
    for v in json.loads((sources.EXP/'summary.json').read_text())['markets'].values():
        start = pd.Timestamp(v['start_utc']).value//1_000_000
        end = pd.Timestamp(v['delivery']).value//1_000_000
        grid = np.arange(((start+12*wallet.HOUR-1)//(12*wallet.HOUR))*12*wallet.HOUR,end,12*wallet.HOUR)
        if counts.reindex(grid).fillna(0).lt(10).any():
            raise ValueError('Updated source could change collateral peer availability')
    return pd.concat([rows[~rows.event_id.isin(changed)],extras],ignore_index=True),src,changed,added


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed corrected two-delisting wallet')
    rows,src,changed,added = pool()
    roster,_ = wallet.admit(rows[rows.tp_r.eq(0)],9)
    occupied = rows[rows.event_id.isin(roster.event_id)]
    extras = occupied[occupied.symbol.isin(['LRC','ICX']) & occupied.exit_reason.eq('series_tail_close')].copy()
    settlement_inputs = []
    for sym,part in extras.groupby('symbol'):
        end = src[sym][2]
        if not part.exit_bar_ms.eq(end-wallet.HOUR).all():
            raise ValueError(f'{sym} liquidation at an unverified time')
        cfg = json.loads((parent.old.DELIVERY/'config.json').read_text())
        minute = delivery.index_data(dict(cfg,symbols=[sym]),end,directory=None)
        settlement_inputs.append(minute)
        mask = rows.event_id.isin(part.event_id)&rows.exit_reason.eq('series_tail_close')
        rows.loc[mask,['exit_reason','exit_price','exit_fee_rate']] = ['delisting_settlement',float(minute.close.mean()),0.]
    if settlement_inputs:
        pd.concat(settlement_inputs,ignore_index=True).to_csv(EXP/'delivery_minutes.csv',index=False)
    @lru_cache(maxsize=None)
    def quotes(symbol):
        path,_,end = src[symbol]
        h = pd.read_csv(path,float_precision='round_trip')
        h = h[h.time.lt(end)]
        return h.time.to_numpy(),h[archive.FIELDS].to_numpy()
    result = parent.run_wallet(rows,quotes,src,EXP/'repaired')
    old_ids = set(pd.read_csv(parent.repair.EXP/'funded_1.5x.csv.gz',usecols=['event_id']).event_id)
    held = set(roster.event_id)
    result.update(repriced_original=sorted(changed),new_opportunities=added,new_funded=sorted(held-old_ids),
                  displaced_funded=sorted(old_ids-held),total_eligible=len(rows)//2,
                  newly_settled_symbols=sorted(extras.symbol.unique()),remaining_funding='omitted')
    archive.prices.safe_json(EXP/'summary.json',result)


if __name__=='__main__':
    main()
