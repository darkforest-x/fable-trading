"""Enter only on a later fully closed 12H bar confirming a profitable V1.7 shadow runner."""
import json
from collections import Counter

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_lock_profit as lock
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine

EXP = wallet.ROOT/'experiments/active/exp-cloud-close-profit-confirm-entry-20261109-v1'


def confirmed_events(group, ranks):
    """Use the first completed 12H close at +2R, its pre-key ATR14 and only previously closed H1 highs/lows."""
    symbol = group.symbol.iloc[0]
    file = wallet.SOURCE/'inputs'/symbol/'bars12h.csv.gz'
    if not file.is_file():
        file = wallet.TOP/'inputs'/symbol/'bars12h.csv.gz'
    bars = engine._load_bars(file,'12h').reset_index()
    prior_atr = pd.DataFrame(engine.atr_points(bars.to_dict('records'))).atr14.shift(1)
    priority = (100*prior_atr/bars.close.shift(1)).to_numpy()
    closes = bars.close.to_numpy()
    boundaries = bars.time.to_numpy() + engine.STEP_12H
    clock, hourly = wallet.quotes(symbol)
    admitted, counts = [],Counter()
    for row in group.to_dict('records'):
        original = int(row['entry_ms'])
        last = int(row['exit_bar_ms'])
        side,entry = row['side'],row['entry_price']
        distance = abs(entry-row['stop_price'])
        native_open_exit = row['exit_phase']=='open'
        # Right endpoint includes an intrabar exit later in the prospective entry
        # hour. It excludes only a native exit known at that hour's open.
        lo = boundaries.searchsorted(original,side='right')
        hi = boundaries.searchsorted(last,side='left' if native_open_exit else 'right')
        hits = np.flatnonzero(side*(closes[lo:hi]-entry)>=2*distance) if distance>0 else []
        if not len(hits):
            counts['tail_without_confirm' if row['exit_reason']=='series_tail_close' else 'no_confirm_before_exit']+=1
            continue
        candidate = lo+hits[0]
        boundary = boundaries[candidate]
        start_i,current = clock.searchsorted([original,boundary])
        if current>=len(clock) or clock[current]!=boundary or start_i>=current or clock[start_i]!=original:
            counts['missing_entry_open']+=1
            continue
        if (boundary,symbol) not in ranks or not np.isfinite(priority[candidate]):
            counts['unavailable_rank_or_atr']+=1
            continue
        extreme = hourly[start_i:current,1].max() if side==1 else hourly[start_i:current,2].min()
        if side*(extreme-entry)<2*distance:
            counts['lock_not_yet_active_in_hourly_path']+=1
            continue
        stop = entry+side*.5*distance
        fill = hourly[current,0]
        if side*(fill-stop)<=0:
            counts['open_at_or_beyond_locked_stop']+=1
            continue
        row.update(shadow_entry_ms=original,shadow_entry_price=entry,shadow_stop_price=row['stop_price'],shadow_risk_distance=distance,
                   entry_ms=boundary,entry_price=fill,stop_price=stop,entry_order=priority[candidate],
                   risk_distance=side*(fill-stop),risk_fraction=side*(fill-stop)/fill,
                   key_open_ms=boundary-engine.STEP_12H,confirmation_close=closes[candidate],confirmation_ms=boundary)
        admitted.append(row)
        counts['accepted']+=1
    return admitted,counts


def main():
    pool = pd.read_csv(lock.EXP/'profit_lock_paths.csv.gz',dtype={'symbol':str},keep_default_na=False,
                       float_precision='round_trip')
    pool = pool[pool.arm_at_r.eq(2.)]
    ranks = set(leader.leadership().index)
    admitted,counts = [],Counter()
    for _,group in pool.groupby('symbol'):
        selected,detail = confirmed_events(group,ranks)
        admitted.extend(selected)
        counts.update(detail)
    if sum(counts.values())!=len(pool):
        raise ValueError('Shadow opportunities not fully accounted for')
    paths = pd.DataFrame(admitted)[['event_id','parent_id','source_row_number','symbol','side','tp_r','is_control',
        'entry_ms','entry_price','stop_price','entry_order','risk_distance','risk_fraction','key_open_ms',
        'confirmation_close','confirmation_ms','exit_bar_ms','exit_phase','exit_reason','exit_price',
        'exit_fee_rate','shadow_entry_ms','shadow_entry_price','shadow_stop_price','shadow_risk_distance']]
    paths.to_csv(EXP/'opportunities.csv.gz',index=False)
    real = paths[~paths.is_control]
    ctl = paths[paths.is_control & paths.parent_id.isin(real.parent_id)].copy()
    new_entry = real.set_index('parent_id').entry_ms
    ctl = ctl[ctl.entry_ms.gt(ctl.parent_id.map(new_entry))]
    base = pd.read_csv(lock.EXP/'account_metrics.csv')
    rows = base[base.arm_at_r.eq(2.)].drop(columns='arm_at_r').assign(policy='original',scenario='actual').to_dict('records')
    metrics, audit, trades, equity = wallet.evaluate(real,9)
    trades.to_csv(EXP/'funded.csv.gz',index=False)
    equity.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
    rows.extend(dict(policy='confirmed_2r',scenario='actual',period=p,**m) for p,m in metrics.items())
    dev = metrics['dev']
    dev_ok = bool(wallet.qualifies(dev) and dev['payoff_usdt']>=2.5 and dev['trades']>=100)
    if dev_ok:
        paired = real[real.parent_id.isin(ctl.parent_id)]
        for name,events,slip in [('matched_actual',paired,0.),('matched_control',ctl,0.),('stress',real,.0005)]:
            values = wallet.evaluate(events,9,slip=slip)[0]
            rows.extend(dict(policy='confirmed_2r',scenario=name,period=p,**m) for p,m in values.items())
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    summary = dict(eligible_real=len(real),paired=len(ctl),opportunity_status=dict(counts),
                   dev_pass=dev_ok,account=audit)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
