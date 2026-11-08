"""Close a funded V1.7 trade at the open immediately after a complete failed reclaim."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as native
from yoyo.evaluation import cloud_close_slots as capacity


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-reclaim-failure-20261109-v1'


def generate(hours):
    paths = wallet.read_paths()
    rank = leader.leadership().index
    paths = paths[paths.tp_r.eq(0) & pd.MultiIndex.from_frame(paths[['entry_ms','symbol']]).isin(rank)].copy()
    original = paths[~paths.is_control].set_index('parent_id')
    paths = paths[paths.parent_id.isin(original.index)].copy()
    source = pd.read_csv(wallet.SOURCE/'signals.csv', usecols=['prior_atr_pct','reference_entry'],
                         float_precision='round_trip')
    source.index = pd.RangeIndex(1, len(source)+1)
    reference = original.source_row_number.map(source.reference_entry)
    if not ((reference - original.stop_price) * original.side).gt(0.).all():
        raise ValueError('Source touched levels do not lie inside original protective stops')
    paths['reclaim_level'] = paths.entry_price * paths.parent_id.map(reference / original.entry_price)
    paths.loc[~paths.is_control,'reclaim_level'] = paths.loc[~paths.is_control,'parent_id'].map(reference)
    paths['entry_order'] = paths.source_row_number.map(source.prior_atr_pct).fillna(np.inf)
    paths['risk_distance'] = (paths.entry_price-paths.stop_price)*paths.side
    paths['week'] = ''
    pieces = []
    for symbol, group in paths.groupby('symbol'):
        folder = wallet.SOURCE/'inputs'/symbol
        if not (folder/'bars1h.csv.gz').is_file():
            folder = wallet.TOP/'inputs'/symbol
        twelve = native._load_bars(folder/'bars12h.csv.gz','12h')
        hourly = native._load_bars(folder/'bars1h.csv.gz','1h')
        q, clock, _ = native._prepare_symbol(twelve,hourly)
        starts, ends = clock.searchsorted(group.entry_ms), clock.searchsorted(group.exit_bar_ms)
        if not np.array_equal(clock[starts],group.entry_ms) or not np.array_equal(clock[ends],group.exit_bar_ms):
            raise ValueError(f'Original position boundary missing on {symbol}')
        group = group.assign(entry_i=starts,end_i=ends)
        rows = group.sort_values(['entry_i','event_id']).to_dict('records')
        for duration in hours:
            filled, _ = native._simulate_symbol(rows,q,clock,(0.,2.),8,reclaim_hours=duration)
            part = pd.DataFrame(filled)
            part['entry_order'] = part.event_id.map(group.set_index('event_id').entry_order)
            part['hours'] = duration
            pieces.append(part[['hours','parent_id','event_id','is_control','symbol','side','entry_ms',
                                'exit_bar_ms','exit_phase','exit_reason','entry_price','exit_price','exit_fee_rate',
                                'stop_price','tp_r','entry_order']])
    result = pd.concat(pieces,ignore_index=True)
    if len(result) != len(paths)*2*len(hours) or result.duplicated(['hours','event_id','tp_r']).any():
        raise ValueError('Incomplete or duplicate protected paths')
    return result


def main():
    hours = json.loads((EXP/'config.json').read_text())['hours']
    output = EXP/'paths.csv.gz'
    if output.exists():
        paths = pd.read_csv(output, dtype={'symbol':str}, keep_default_na=False, float_precision='round_trip')
    else:
        paths = generate(hours)
        pending = output.with_suffix('.pending')
        paths.to_csv(pending, index=False, compression='gzip')
        pending.replace(output)
    baseline = pd.read_csv(leader.EXP/'account_metrics.csv')
    rows = baseline[baseline.top_fraction.eq(1)].drop(columns='top_fraction').assign(hours=0).to_dict('records')
    curves = {}
    for duration in hours:
        pool = paths[paths.hours.eq(duration) & ~paths.is_control]
        metrics, trades, equity = capacity.run(pool,9,.75)
        rows.extend(dict(hours=duration,period=p,**m) for p,m in metrics.items())
        curves[duration] = trades,equity
        gc.collect()
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    dev = table[table.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible = dev[dev.apply(wallet.qualifies,axis=1) & dev.payoff_usdt.ge(2.5) & dev.trades.ge(100)]
    pick = int(eligible.hours.iloc[0]) if len(eligible) else None
    checks = {}
    if pick in hours:
        trades, equity = curves[pick]
        trades.to_csv(EXP/'funded_dev_pick.csv.gz')
        equity.to_csv(EXP/'hourly_dev_pick.csv.gz',index_label='hour_end_utc')
        chosen = paths[paths.hours.eq(pick)]
        real, control = chosen[~chosen.is_control], chosen[chosen.is_control]
        matched = real[real.parent_id.isin(control.parent_id)]
        for key,pool,slip in [('paired_signals',matched,0.),('control',control,0.),('slippage_5bp',real,.0005)]:
            checks[key] = capacity.run(pool,9,.75,slip)[0]
    counts = paths[~paths.is_control & paths.tp_r.eq(0) & paths.exit_reason.eq('reclaim_failed_at_close')]
    (EXP/'summary.json').write_text(json.dumps(dict(pick=pick, checks=checks, failures=counts.hours.value_counts().to_dict()),
                                                indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
