"""Continue the scored nine-slot Binance cloud account on contract-specific native prices."""
import json
from collections import Counter
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_november_paths as path
from yoyo.evaluation import cloud_close_spot_reclaim as spot
from yoyo.evaluation import cloud_close_fvg_transfer as fvg

admin = spot.admin
trend = admin.study.tenth.trend
EXP = path.EXP.parent/'exp-cloud-close-november-account-20261109-v1'
SCORES = path.EXP.parent/'exp-cloud-close-november-scores-20261109-v1'
CUTOFF = pd.Timestamp('2026-09-22T00:00Z').value//1_000_000


@lru_cache(maxsize=1)
def settlement_marks():
    filename = EXP/'settlement_hours.csv'
    if not filename.is_file():
        return {}
    marks = pd.read_csv(filename,dtype={'symbol':str})
    return {ticker:part.set_index('time').price for ticker,part in marks.groupby('symbol')}


@lru_cache(maxsize=60)
def quote_loader(symbol):
    ticker = symbol+'USDT'
    with np.load(path.history_slot(ticker)/'quotes.npz') as file:
        frames = [pd.DataFrame(file['prices'],columns=path.OHLC,index=file['clock'])]
    for suffix in sorted(path.CACHE.glob(ticker+'__*/extension.npz')):
        with np.load(suffix) as tape:
            frames.append(pd.DataFrame(tape['prices'],columns=path.OHLC,index=tape['clock']))
    bars = pd.concat(frames).sort_index()
    if not bars.index.is_unique:
        raise ValueError(f'Duplicate hourly priced contract periods {ticker}')
    special = settlement_marks().get(symbol)
    if special is not None:
        missing = special.loc[~special.index.isin(bars.index)]
        if len(missing):
            # Only an exchange cash settlement, at its official clock, can have
            # a price without a trading candle. It is never an entry or a key.
            bars = pd.concat([bars,pd.DataFrame({k:missing for k in path.OHLC})]).sort_index()
    return bars.index.to_numpy(dtype='int64'),bars.to_numpy(dtype=float)


def prepare(legs):
    return admin.study.tenth.split.pair_data(legs[legs.tp_r.eq(0)],legs[legs.tp_r.eq(2)],9,
                start=path.venue.START,end=path.END,quote_loader=quote_loader)


def market_inputs():
    periods = pd.read_csv(path.SOURCE/'coverage.csv.gz',dtype={'symbol':str,'contract_period_id':str})
    ranks = Counter()
    frames = []
    known_ids = set()
    for record in periods.itertuples():
        slot = path.CACHE/record.contract_period_id
        suffix = slot/'result.json'
        hist = path.history_slot(record.symbol)
        is_new_life = 'separate_period_no_continuity_claim' in str(record.period_link_status)
        alive_to = int(record.delivery_ms)-path.HOUR//2 if pd.notna(record.delivery_ms) else path.END
        clocks = []
        if not is_new_life:
            with np.load(hist/'quotes.npz') as tape:
                clocks = tape['rank_times']
                cutoff = int(tape['clock'][-1])+path.HOUR if len(tape['clock']) else 0
                clocks = list(clocks[(clocks>=CUTOFF)&(clocks<cutoff)])
        if suffix.is_file() and (slot/'rank_times.npy').is_file():
            clocks.extend(np.load(slot/'rank_times.npy'))
            current = pd.read_csv(slot/'fills.csv.gz',dtype={'symbol':str},keep_default_na=False,float_precision='round_trip')
            current = current[~current.is_control].copy() if len(current) else current
            if len(current):
                current['period'] = record.contract_period_id
                current['end_ms'] = record.delivery_ms
                current['last_h1'] = json.loads(suffix.read_text())['last_added_ms']
                frames.append(current)
            # The eight newly restored contracts have no frozen pre-extension
            # account roster. Keep their complete native pre-boundary fills,
            # replacing any still-open child path with the extended version.
            warm_path = path.SUPPLEMENTAL_SYMBOLS/record.symbol/'fills.csv.gz'
            if warm_path.is_file():
                warm = pd.read_csv(warm_path,dtype={'symbol':str},keep_default_na=False,float_precision='round_trip')
                warm = warm[~warm.is_control].copy() if len(warm) else warm
                if len(warm):
                    replacement = set(current.event_id.astype(str)) if len(current) else set()
                    warm = warm[~warm.event_id.astype(str).isin(replacement)].copy()
                    if len(warm):
                        warm['period'] = record.contract_period_id
                        warm['end_ms'] = record.delivery_ms
                        warm['last_h1'] = json.loads((path.SUPPLEMENTAL_SYMBOLS/record.symbol/'source_receipt.json').read_text())['last_h1_ms']
                        frames.append(warm)
        elif record.complete_h1>0 and not suffix.is_file():
            raise ValueError(f'Missing contract-path receipt {record.contract_period_id}')
        unique = {int(x) for x in clocks if record.onboard_ms<=x<alive_to and CUTOFF<=x<path.END}
        # Listings of the same ticker must not simultaneously supply two members.
        for t in unique:
            if (record.symbol,t) in known_ids:
                raise ValueError(f'Double membership for {record.symbol}')
            known_ids.add((record.symbol,t))
        ranks.update(unique)
    return pd.concat(frames,ignore_index=True),ranks


def new_eligible(legs,earlier,ranks):
    base = legs[legs.tp_r.eq(0)].copy()
    prior = base.event_id.isin(earlier)
    alive = base.end_ms.isna() | base.entry_ms.lt(base.end_ms-path.HOUR//2)
    eligible = alive & base.eligible_at_own_time & (prior|base.entry_ms.map(ranks).fillna(0).ge(10))
    valid = legs[legs.event_id.isin(base.loc[eligible,'event_id'])].copy()
    options = []
    for symbol,part in valid[valid.tp_r.eq(0)].groupby('symbol'):
        t,q = quote_loader(symbol)
        bars = pd.DataFrame(q,columns=path.OHLC,index=pd.Index(t,name='time'))
        eff = trend.efficiency(path.twelve_hour(bars)).reindex(part.entry_ms).to_numpy()*part.side
        options.append(pd.DataFrame(dict(event_id=part.event_id,choice=np.where(eff>.5,'full_runner','partial_2_3'))))
    return trend.rotation._rotated_paths(valid,pd.concat(options,ignore_index=True))


def extend_deliveries(rows):
    rows = rows.copy()
    rows['delivery'] = False
    rows['delivery_ambiguous'] = False
    rows['delivery_gap'] = False
    expired = rows.end_ms.notna()&rows.end_ms.le(path.END)
    last = rows.last_h1+path.HOUR
    tail = expired & rows.exit_reason.eq('series_tail_close') & rows.exit_bar_ms.eq(rows.last_h1)
    if (tail & rows.end_ms.lt(last)).any():
        raise ValueError('An extended terminal bar crosses the exchange contract lifetime')
    rows.loc[tail,'delivery_gap'] = (rows.end_ms[tail]-last[tail]).ge(path.HOUR)
    if (tail & rows.end_ms.mod(path.HOUR).ne(0)).any():
        raise ValueError('Intrahour delivery requires finer native execution evidence')
    rows.loc[tail,'delivery'] = True
    rows.loc[tail,'exit_reason'] = 'delisting_settlement'
    rows.loc[tail,'exit_fee_rate'] = path.venue.wallet.TAKER
    rows.loc[tail,'exit_phase'] = 'open'
    rows.loc[tail,'exit_bar_ms'] = rows.end_ms[tail].astype('int64')
    rows.loc[tail,'exit_price'] = np.nan
    return rows


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed November wallet')
    source = json.loads((path.EXP/'summary.json').read_text())
    if source['parity_differences'] or source['old_interior_missing']:
        raise ValueError('Historical overlap requires reconciliation before joining the extended account')
    score_meta = json.loads((SCORES/'summary.json').read_text())
    if score_meta['equal_probabilities']!=score_meta['common_prefix']:
        raise ValueError('Historical out-of-time score overlap requires reconciliation')
    native = pd.read_csv(spot.rank.scores.EXP/'candidate_order.csv.gz',usecols=['event_id','entry_ms','score']).set_index('event_id')
    later = pd.read_csv(SCORES/'scores.csv.gz',usecols=['signal_id','score']).set_index('signal_id')
    spec,marks = spot.rank.market_spec(EXP)
    for source_dir in (spot.vwap.EXP,spot.timing.EXP,fvg.EXP):
        spec.update(json.loads((source_dir/'config.json').read_text())['additional_settlements'])
        marks.update(pd.read_csv(source_dir/'settlement_index_1m.csv').groupby('symbol')[['low','close','high']].mean().to_dict('index'))
    extra = set(json.loads((admin.archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    orig = admin.archive.pool(extra)
    with admin.archive.market_quotes(extra):
        _,old = trend.inputs('Binance',orig[~orig.is_control])
    old = admin.adjusted_legs(old,extra,spec)
    if set(old.event_id)!=set(native.index):
        raise ValueError('Old eligible signal roster changed')
    frozen_old_ids = set(old.event_id)
    raw,ranks = market_inputs()
    # Raw pre-eligibility files also carry old events that the frozen account
    # had ruled out. Never retrospectively admit one of those.
    periods = pd.read_csv(path.EXP/'periods.csv.gz').set_index('period')
    historical = raw.entry_ms.lt(raw.period.map(periods.frozen_cutoff_ms))
    supplemental_periods = set(periods.index[periods.supplemental_new_history.astype(bool)])
    new_source_history = raw.period.isin(supplemental_periods)
    raw = raw[~historical | raw.event_id.isin(frozen_old_ids) | new_source_history]
    amended = extend_deliveries(new_eligible(raw,frozen_old_ids,ranks))
    carries = frozen_old_ids & set(amended.event_id)
    if (old.event_id.isin(carries) & old.delivery).any():
        raise ValueError('New history would re-open a documented old delisting')
    candidates = pd.concat([old[~old.event_id.isin(carries)],amended],ignore_index=True)
    if candidates.duplicated(['event_id','tp_r']).any() or not candidates.groupby('event_id').size().eq(2).all():
        raise ValueError('Incomplete native extended path pair')
    forecasts = pd.concat([native.score,later.score[~later.index.isin(native.index)]])
    ranked,order = spot.rank.scores.rerank(candidates,forecasts)
    order.to_csv(EXP/'candidate_order.csv.gz',index=False)
    chosen = admin.admitted(ranked)
    forced = chosen[chosen.delivery]
    forced[['event_id','symbol','exit_bar_ms','tp_r']].to_csv(EXP/'selected_deliveries.csv',index=False)
    if forced.delivery_gap.eq(True).any():
        raise ValueError('Funded positions held across undocumented missing pre-delivery prices')
    paid = admin.resolve_settlements(chosen.copy(),marks,'proxy')
    settlement = paid[paid.delivery & paid.exit_bar_ms.ge(CUTOFF)][['symbol','exit_bar_ms','exit_price']].drop_duplicates()
    settlement.rename(columns={'exit_bar_ms':'time','exit_price':'price'}).to_csv(EXP/'settlement_hours.csv',index=False)
    quote_loader.cache_clear()
    settlement_marks.cache_clear()
    paid.to_csv(EXP/'funded_legs.csv.gz',index=False)
    metrics,pos,eq,trims = spot.hourly.tested.trim_account(prepare(paid),quote_loader,.9,.2,review_hours=1)
    original = pd.read_csv(spot.hourly.EXP/'hourly_Binance.csv.gz',index_col=0,parse_dates=True)
    same = original[original.index<=pd.Timestamp(CUTOFF,unit='ms',tz='UTC')]
    divergence = (eq.loc[same.index,same.columns]-same).abs().max().max()
    if divergence>1.e-5 or not np.isclose(1000+pos.cash_pnl.sum(),eq.nav.iloc[-1],rtol=1.e-8,atol=.001):
        raise ValueError(f'Unchanged pre-extension cash and marks must reconcile: {divergence}')
    pos.to_csv(EXP/'funded.csv.gz')
    eq.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
    trims.to_csv(EXP/'trims.csv.gz',index=False)
    new = ~order.event_id.isin(native.index)
    supplemental_symbols = ['DATAIP','GRAM','GRVT','DOS','牛来','MARSCOIN','PONS','哈基米']
    source_receipts = {str(r['symbol']):r for r in json.loads((path.SUPPLEMENTAL_CACHE/'source_receipt.json').read_text())}
    raw_actual = raw[(~raw.is_control)&raw.tp_r.eq(0)]
    amended_actual = amended[(~amended.is_control)&amended.tp_r.eq(0)]
    chosen_actual = chosen[(~chosen.is_control)&chosen.tp_r.eq(0)]
    funded_ids = set(pos.index.astype(str))
    supplemental_audit = []
    for base_symbol in supplemental_symbols:
        ticker = base_symbol+'USDT'
        receipt = source_receipts[ticker]
        part = raw_actual[raw_actual.symbol.eq(base_symbol)]
        eligible_age = part.eligible_at_own_time.astype(bool)
        cross_section = part.entry_ms.map(ranks).fillna(0).ge(10)
        eligible_ids = set(part.loc[eligible_age & cross_section,'event_id'].astype(str))
        supplemental_audit.append(dict(
            symbol=ticker,onboard_ms=int(receipt['onboard_ms']),mature_at_ms=int(receipt['mature_at_ms']),
            age_days_at_account_end=round((path.END-int(receipt['onboard_ms']))/(24*path.HOUR),6),
            source_status=receipt['state'],official_h1_rows=int(receipt['history_bars']),
            raw_native_actual_signals=int(part.event_id.nunique()),
            ever_90d_rank_eligible=int(part.loc[eligible_age,'event_id'].nunique()),
            cross_section_eligible_signals=int(len(eligible_ids)),
            eligible_paths_after_lifecycle_filter=int(amended_actual.loc[amended_actual.symbol.eq(base_symbol),'event_id'].nunique()),
            selected_actual_signals=int(chosen_actual.loc[chosen_actual.symbol.eq(base_symbol),'event_id'].nunique()),
            funded_actual_signals=int(len(eligible_ids & funded_ids)),
            ninety_day_status_at_end=('matured' if int(receipt['mature_at_ms']) <= path.END else 'not_yet_90d'),
        ))
    summary = dict(metrics=metrics,earlier_roster=len(native),new_eligible=int(new.sum()),
                   new_frozen_scores=int(order.loc[new,'score'].count()),previously_open_eligible=len(carries),
                   previously_open_funded=len(carries&set(pos.index)),prefix_equity_max_abs=float(divergence),
                   new_funded=len(set(pos.index)-set(native.index)),supplemental_8=supplemental_audit,
                   supplemental_unscored=True,fee_only=True)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()
