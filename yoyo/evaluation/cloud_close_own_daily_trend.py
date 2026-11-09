"""Screen the original funded cloud account with each coin's already known daily regime."""
import gc
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_waves_wallet as repair

old = repair.old
archive = repair.archive
wallet = repair.wallet
EXP = repair.EXP.parent/'exp-cloud-close-own-daily-trend-20261109-v1'
DAY = 24*wallet.HOUR


def daily_state(bars):
    """Use two contiguous UTC 12H closes per calendar day; the last close is available next midnight."""
    key = bars.index.to_numpy()
    daily = pd.Series(bars.close.to_numpy(),index=key//DAY*DAY).groupby(level=0).agg(['last','size'])
    closed = daily.loc[daily['size'].eq(2),'last']
    segment = closed.index.to_series().diff().ne(DAY).cumsum()
    fast = closed.groupby(segment).transform(lambda s:s.ewm(span=20,adjust=False,min_periods=50).mean())
    slow = closed.groupby(segment).transform(lambda s:s.ewm(span=50,adjust=False,min_periods=50).mean())
    return pd.Series(np.sign(fast-slow).to_numpy(),index=closed.index+DAY)


def source_book():
    src = repair.market.sources()
    src['WAVES'] = (repair.WAVES,None,src['WAVES'][2])
    original,returns = archive.frozen_data()
    base_symbols = set(returns)
    registry = pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['key_open_bjt','confirmed_bjt'])
    registry.index += 1
    keys_ms = pd.to_datetime(registry.key_open_bjt,utc=True).astype('datetime64[ms, UTC]').astype('int64')
    confirms = pd.to_datetime(registry.confirmed_bjt,utc=True).astype('datetime64[ms, UTC]').astype('int64')
    if not (keys_ms+12*wallet.HOUR).eq(confirms).all():
        raise ValueError('Original logged keys do not match their 12H confirmations')
    original['key_open_ms'] = original.source_row_number.map(keys_ms)
    if original.key_open_ms.isna().any():
        raise ValueError('Original 291-name key clock absent')
    retired = pd.read_csv(old.BASE/'retired_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    waves = pd.read_csv(repair.EXP/'waves_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    all_paths = pd.concat([original,retired[retired.symbol.ne('WAVES')],waves],ignore_index=True)
    keys = all_paths.loc[all_paths.tp_r.eq(0),['event_id','symbol','key_open_ms']].copy()
    states = {}
    for sym,group in keys.groupby('symbol'):
        _,bars = repair.market.frames(src[sym])
        if sym not in base_symbols:
            returns[sym] = archive.leadership.trailing_return(bars.reset_index())
        when = group.key_open_ms//DAY*DAY
        states.update(zip(group.event_id,daily_state(bars).reindex(when).to_numpy()))
    for sym in set(src)-set(returns):
        _,bars = repair.market.frames(src[sym])
        returns[sym] = archive.leadership.trailing_return(bars.reset_index())
    pool = archive.screen(all_paths,returns)
    rows,_ = archive.risk.hourly.tested.expiry.time_in_force(pool)
    rows['prior_daily_direction'] = rows.event_id.map(states)
    if rows.prior_daily_direction.isna().any():
        raise ValueError('A 90-day price-qualified event lacks its prior completed daily cloud')
    expected = pd.read_csv(repair.EXP/'funded_1.5x.csv.gz',usecols=['event_id'])
    admitted,_ = wallet.admit(rows[rows.tp_r.eq(0)],9)
    if len(rows)!=2*12437 or set(admitted.event_id)!=set(expected.event_id):
        raise ValueError('Rebuilt 360-name pool does not reproduce the corrected cash roster')

    @lru_cache(maxsize=None)
    def quotes(symbol):
        f,_,end = src[symbol]
        h = pd.read_csv(f,float_precision='round_trip')
        h = h[h.time.lt(end)]
        return h.time.to_numpy(),h[archive.FIELDS].to_numpy()

    return rows,quotes,src


def save_execution_tape(tape,directory):
    """Save the operative native wallet inputs after any amendments to its child exits."""
    fields = ['event_id','parent_id','source_row_number','symbol','side','tp_r','is_control',
              'entry_ms','exit_bar_ms','entry_price','exit_price','stop_price','exit_fee_rate',
              'exit_reason','exit_phase','same_bar_ambiguous','gap_flag','entry_status',
              'entry_order','key_open_ms']
    tape[fields].to_csv(directory/'selected_paths.csv.gz',index=False)


def run_wallet(plans,quotes,src,directory):
    directory.mkdir(parents=True,exist_ok=True)
    roster,_ = wallet.admit(plans[plans.tp_r.eq(0)],9)
    tape = plans[plans.event_id.isin(roster.event_id)].copy()
    delivery = json.loads((old.DELIVERY/'summary.json').read_text())
    info = json.loads((old.DELIVERY/'config.json').read_text())['deliveries']
    uncertain = []
    for idx,r in tape[tape.exit_reason.eq('series_tail_close') & tape.exit_bar_ms.lt(wallet.END-wallet.HOUR)].iterrows():
        known = info.get(r.symbol)
        if known and r.exit_bar_ms+wallet.HOUR==pd.Timestamp(known['delivery_utc']).value//1_000_000:
            tape.loc[idx,['exit_price','exit_reason','exit_fee_rate']] = [
                delivery['official_price_hour'][r.symbol]['close'],'delisting_settlement',0.]
        else:
            uncertain.append({'event_id':r.event_id,'symbol':r.symbol,'exit_ms':int(r.exit_bar_ms)})
    save_execution_tape(tape,directory)
    limits = old.changed_limits(tape,quotes)
    data,trigger = old.parent.assemble(tape,limits,'OKX',quotes)
    native = old.exposure.underlying(data,quotes)
    cases = {}
    for name,slip in [('fee_only',0.),('extra_5bp',.0005)]:
        cases[name] = old.exposure.simulate(data,native,1.5,slip,directory=directory,quote_loader=quotes)
        print(directory.name,name,cases[name].get('metrics',{}).get('all'),flush=True)
        gc.collect()
    return dict(cases=cases,eligible_parents=len(plans)//2,funded=len(roster),new_or_uncertain_tail_exits=uncertain,
                gap_events=sorted(tape.loc[tape.gap_flag | tape.exit_reason.isin(['data_gap_markout','stale_12h_markout']),'event_id'].unique()),
                doubling_fills=len(trigger))


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed own daily-trend filter study')
    rows,quotes,src = source_book()
    rows.loc[rows.tp_r.eq(0),['event_id','symbol','entry_ms','side','prior_daily_direction']].to_csv(
        EXP/'opportunities.csv.gz',index=False)
    aligned = rows.side.mul(rows.prior_daily_direction)
    reference = json.loads((repair.EXP/'summary.json').read_text())['cases']['fixed']['metrics']
    result = {}
    for name,mask in [('aligned',aligned.gt(0)),('opposed',aligned.lt(0))]:
        result[name] = run_wallet(rows[mask],quotes,src,EXP/name)
    archive.prices.safe_json(EXP/'summary.json',dict(reference=reference,unclassified_parents=int((aligned.isna()|aligned.eq(0)).sum()//2),arms=result))


if __name__=='__main__':
    main()
