"""Measure the effect of predeclared, documented retired crypto names on the V1.7 wallet."""
import json
import subprocess
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leadership
from yoyo.evaluation import cloud_close_november_paths as native
from yoyo.evaluation import cloud_close_okx_retired_prices as prices
from yoyo.evaluation import cloud_close_owner_risk as risk

wallet = leadership.wallet
EXP = risk.EXP.parent/'exp-cloud-close-okx-retired-account-20261109-v1'
FIELDS = native.OHLC
GAP_REASONS = ['data_gap_markout','series_tail_close','stale_12h_markout']


def official_keys(symbol,hour_file,end,native_12=None):
    cmd = [str(wallet.ROOT/'.venv/bin/python'),'-m','yoyo.evaluation.cloud_close_okx_retired_keys',
           symbol,str(hour_file),str(end)]
    if native_12:
        cmd.extend(['--native12',str(native_12)])
    return json.loads(subprocess.check_output(cmd,text=True))


def frozen_data():
    coverage = pd.read_csv(wallet.SOURCE/'input_coverage.csv')
    raw = pd.read_csv(wallet.SOURCE/'signals.csv',dtype={'symbol':str})
    history = {}
    for row in coverage.itertuples():
        parent = wallet.TOP if row.cohort=='top20_reused' else wallet.SOURCE
        folder = parent/'inputs'/row.symbol
        frame = pd.read_csv(folder/'bars12h.csv.gz',float_precision='round_trip')
        history[row.symbol] = leadership.trailing_return(frame)
        if row.symbol=='BTC':
            rebuilt = pd.DataFrame(official_keys(row.symbol,folder/'bars1h.csv.gz',wallet.END,folder/'bars12h.csv.gz')).sort_values('entry_ms')
            old = raw[raw.symbol.eq('BTC')].copy()
            old['entry_ms'] = pd.to_datetime(old.confirmed_bjt,utc=True).astype('datetime64[ms, UTC]').astype('int64')
            old = old.sort_values('entry_ms')
            if (not np.array_equal(old[['entry_ms','side']],rebuilt[['entry_ms','side']]) or
                    not np.allclose(old.reference_stop,rebuilt.stop_price) or
                    not np.allclose(old.prior_atr_pct,rebuilt.entry_order)):
                raise ValueError('Original BTC V1.7 source does not match independently rebuilt keys')
    paths = wallet.read_paths()
    paths = paths.loc[~paths.is_control & paths.tp_r.isin([0,2])].copy()
    paths['entry_order'] = paths.source_row_number.map(raw.prior_atr_pct.set_axis(raw.index+1)).fillna(np.inf)
    return paths,history


def retired_symbol(symbol,end):
    inst = symbol+'-USDT-SWAP'
    folder = prices.CACHE/'series'/inst
    if not (folder/'source.json').is_file():
        raise FileNotFoundError(f'No archived H1 source receipt for {inst}')
    hour = pd.read_csv(folder/'bars1h.csv.gz',index_col='time',float_precision='round_trip')
    hour = hour.loc[hour.index<end]
    bars = native.twelve_hour(hour)
    ranks = leadership.trailing_return(bars.reset_index())
    events = official_keys(symbol,folder/'bars1h.csv.gz',end)
    if any(e['key_open_ms'] not in bars.index for e in events):
        raise ValueError(f'Original key outside complete native 12H bars for {symbol}')
    rows = []
    missing = 0
    if events:
        q,clock,_ = native.engine._prepare_symbol(bars,hour)
        eligible = []
        for e in events:
            timestamp = e['entry_ms']
            i = clock.searchsorted(timestamp)
            if i==len(clock) or clock[i]!=timestamp:
                missing += 1
                continue
            entry = float(q[i,0])
            distance = (entry-e['stop_price'])*e['side']
            if distance<=0:
                missing += 1
                continue
            last,reason = native.engine._safe_path_end(i,e['side'],q,clock)
            eligible.append(dict(**e,parent_id=e['event_id'],confirmation_ms=timestamp,source_row_number=0,
                        entry_price=entry,entry_i=i,end_i=last,path_upper_bound_reason=reason,is_control=False,
                        risk_distance=distance,risk_fraction=distance/entry))
        if eligible:
            rows,_ = native.engine._simulate_symbol(eligible,q,clock,(0.,2.),128)
            priority = {e['event_id']:e['entry_order'] for e in eligible}
            for r in rows:
                r['entry_order'] = priority[r['event_id']]
                if r['entry_status']!='executed':
                    raise ValueError(f'Unexpected native entry status for {symbol}')
    meta = dict(symbol=symbol,hours=len(hour),complete_12h=len(bars),raw_signals=len(events),
                observed_90d_clocks=int(ranks.notna().sum()),unpriced_entries=missing)
    return ranks,rows,meta


def screen(events,history):
    wide = pd.DataFrame(history)
    available = wide.notna()
    covered = available.loc[available.sum(axis=1).ge(10)]
    clocks,symbols = np.where(covered.to_numpy())
    key = pd.MultiIndex.from_arrays([covered.index.take(clocks),covered.columns.take(symbols)])
    return events[pd.MultiIndex.from_frame(events[['entry_ms','symbol']]).isin(key)].copy()


def main(directory=EXP, *, reuse_first=False):
    if (directory/'summary.json').is_file():
        raise FileExistsError(f'Completed retired-market sensitivity in {directory}')
    cfg = json.loads((directory/'config.json').read_text())
    ends = {s:pd.Timestamp(n['delivery_from_utc']).value//1_000_000 for n in cfg['notices'] for s in n['symbols']}
    original,history = frozen_data()
    before = screen(original,history)
    if len(before)!=2*11780 or set(before.event_id)!=set(pd.read_csv(
            risk.hourly.tested.expiry.trend.repair.agreement.EXP/'event_screen.csv.gz').query(
            'not is_control and eligible_90d').event_id):
        raise ValueError('Original 90-day eligible pool changed')
    records,coverage = [],[]
    if reuse_first:
        cached = pd.read_csv(EXP/'retired_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
        cached_summary = pd.read_csv(EXP/'source_coverage.csv').set_index('symbol',drop=False)
        first_cfg = json.loads((EXP/'config.json').read_text())
        limits = {s:pd.Timestamp(n['delivery_from_utc']).value//1_000_000 for n in first_cfg['notices'] for s in n['symbols']}
        if any(ends.get(s)!=t for s,t in limits.items()):
            raise ValueError('An archived contract delivery changed in the expanded notice roster')
    for symbol,end in sorted(ends.items()):
        if symbol in history:
            raise ValueError(f'{symbol} already in original roster')
        if reuse_first and symbol in cached_summary.index:
            f = prices.CACHE/'series'/(symbol+'-USDT-SWAP')/'bars1h.csv.gz'
            hours = pd.read_csv(f,index_col='time',float_precision='round_trip')
            rank = leadership.trailing_return(native.twelve_hour(hours[hours.index<end]).reset_index())
            rows = cached[cached.symbol.eq(symbol)].to_dict('records')
            meta = cached_summary.loc[symbol].to_dict()
        else:
            rank,rows,meta = retired_symbol(symbol,end)
        history[symbol] = rank
        records.extend(rows)
        coverage.append(meta)
    added = pd.DataFrame(records)
    expanded = screen(pd.concat([original,added],ignore_index=True),history)
    rows,_ = risk.hourly.tested.expiry.time_in_force(expanded)
    pd.DataFrame(coverage).to_csv(directory/'source_coverage.csv',index=False)
    added.to_csv(directory/'retired_paths.csv.gz',index=False)

    @lru_cache(maxsize=None)
    def quotes(symbol):
        if symbol not in ends:
            return wallet.quotes(symbol)
        f = prices.CACHE/'series'/(symbol+'-USDT-SWAP')/'bars1h.csv.gz'
        x = pd.read_csv(f,float_precision='round_trip')
        return x.time.to_numpy(),x[FIELDS].to_numpy()

    data = risk.split.pair_data(rows[rows.tp_r.eq(0)],rows[rows.tp_r.eq(2)],9,quote_loader=quotes)
    selected = data['selected']
    archived = selected[selected.symbol.isin(ends)].copy()
    archived.to_csv(directory/'funded_retired_legs.csv.gz',index=False)
    unresolved = archived[archived.exit_reason.isin(GAP_REASONS)|archived.gap_flag]
    original_ids = set(pd.read_csv(risk.hourly.EXP/'funded_OKX.csv.gz').event_id)
    held = set(selected.event_id)
    cases = {}
    for name,slip in [('fee_only',0.),('market_slippage_5bp',.0005)]:
        if slip:
            m,pos,eq,_ = risk.checked._trim_account_with_slippage(data,quotes,1.,.2,review_hours=1,market_slip=slip)
        else:
            m,pos,eq,_ = risk.hourly.tested.trim_account(data,quotes,1.,.2,review_hours=1)
        if not np.isclose(1000+pos.cash_pnl.sum(),eq.nav.iat[-1],rtol=1.e-8,atol=.001):
            raise ValueError('Historical wallet cash/closed positions mismatch')
        pos.to_csv(directory/f'funded_{name}.csv.gz')
        eq.to_csv(directory/f'hourly_{name}.csv.gz',index_label='hour_end_utc')
        cases[name] = m
    summary = dict(added_names=sorted(ends),eligible_before=len(before)//2,eligible_after=len(rows)//2,
            eligible_new_contracts=rows.loc[rows.symbol.isin(ends),'event_id'].nunique(),
            eligible_additional_original=rows.loc[~rows.symbol.isin(ends),'event_id'].nunique()-len(before)//2,
            funded_original=len(original_ids),funded_expanded=len(held),funded_entries_added=len(held-original_ids),
            funded_entries_displaced=len(original_ids-held),retired_funded=archived.event_id.nunique(),
            retired_selected_source_boundary_legs=len(unresolved),retired_selected_source_boundary_events=
            sorted(unresolved.event_id.unique()),metrics=cases,
            scope=f'{len(ends)} documented delistings added with the confirmed native H1 sources available. This is a roster sensitivity; earlier months and other retired instruments remain incomplete. Fees included, funding pending.')
    prices.safe_json(directory/'summary.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='metrics'},indent=2),flush=True)


if __name__=='__main__':
    import argparse
    cli = argparse.ArgumentParser()
    cli.add_argument('--directory-census',action='store_true')
    if cli.parse_args().directory_census:
        main(EXP.parent/'exp-cloud-close-okx-directory-account-20261109-v1',reuse_first=True)
    else:
        main()
