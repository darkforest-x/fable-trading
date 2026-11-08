"""Place only the protective stop at the actual V1.7 signal wick, retaining the 2R target price."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds

EXP = wallet.ROOT/'experiments/active/exp-cloud-close-key-extreme-stop-20261109-v1'


def price(paths):
    """Use low/high of the 12H key closed at entry and cash orders priced from the observed H1 open."""
    metadata = pd.read_csv(wallet.SOURCE/'signals.csv', usecols=['symbol','week','was_top20','signal_low','signal_high'],
                           dtype={'symbol':str}, keep_default_na=False, float_precision='round_trip')
    metadata.index = pd.RangeIndex(1,len(metadata)+1)
    config = json.loads((wallet.EXP/'paths_config.json').read_text())
    inputs = paths[paths.tp_r.eq(0)].copy()
    inputs = inputs[~inputs.is_control | inputs.parent_id.isin(inputs[~inputs.is_control].parent_id)]
    parent = inputs[~inputs.is_control].set_index('parent_id')
    inputs['parent_entry'] = inputs.parent_id.map(parent.entry_price)
    parts = []
    for symbol, group in inputs.groupby('symbol'):
        keys = metadata.loc[group.source_row_number]
        if not keys.symbol.eq(symbol).all():
            raise ValueError(f'{symbol}: signal key mapping mismatch')
        p12,p1,_ = engine._choose_input(symbol,bool(keys.was_top20.iloc[0]),config)
        bars = engine._load_bars(p12,'12h')
        q, clock, _ = engine._prepare_symbol(bars,engine._load_bars(p1,'1h'))
        original_ms = group.parent_id.map(parent.entry_ms)
        known = bars.reindex(original_ms-engine.STEP_12H)
        for column in ['low','high']:
            if not np.allclose(known[column],keys['signal_'+column],rtol=1e-10,atol=1e-14):
                raise ValueError(f'{symbol}: saved wick differs from the just-closed native 12H OHLC')
        extreme = np.where(group.side.eq(1),known.low,known.high)
        stop = np.where(group.is_control,group.entry_price*(extreme/group.parent_entry),extreme)
        start = clock.searchsorted(group.entry_ms)
        if not np.array_equal(clock[start],group.entry_ms):
            raise ValueError(f'{symbol}: missing entry clock')
        end = [engine._safe_path_end(i,s,q,clock)[0] for i,s in zip(start,group.side)]
        jobs = group.assign(week=keys.week.to_numpy(),entry_i=start,end_i=end,stop_price=stop,
                            risk_distance=(group.entry_price-group.stop_price).abs())
        rows,_ = engine._simulate_symbol(jobs.to_dict('records'),q,clock,(0.,2.),8)
        outcome = pd.DataFrame(rows)
        order = group.set_index('event_id').entry_order
        outcome['entry_order'] = outcome.event_id.map(order)
        cols = ['event_id','parent_id','symbol','is_control','source_row_number','side','entry_ms',
                'entry_price','entry_order','stop_price','target_price','tp_r','exit_bar_ms','exit_phase',
                'exit_reason','exit_price','exit_fee_rate','same_bar_ambiguous']
        parts.append(outcome[cols])
    result = pd.concat(parts,ignore_index=True)
    if len(result)!=2*len(inputs) or result.duplicated(['event_id','tp_r']).any():
        raise ValueError('Incomplete or duplicate structural-stop paths')
    return result


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Frozen structural-stop account already exists')
    base = thirds.okx_paths()
    repair.check_parent_sets(base)
    file = EXP/'paths.csv.gz'
    if file.exists():
        paths = pd.read_csv(file,dtype={'symbol':str},keep_default_na=False,float_precision='round_trip')
    else:
        paths = price(base)
        pending = file.with_suffix('.pending')
        paths.to_csv(pending,index=False,compression='gzip')
        pending.replace(file)
    real = paths[~paths.is_control]
    control = paths[paths.is_control]
    if len(real)!=2*11780 or len(control)!=2*10816:
        raise ValueError('Eligibility no longer matches the frozen opportunities')
    first = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    rows = first[first.venue.eq('OKX') & first.scenario.eq('actual')].assign(policy='original').to_dict('records')
    metrics, positions, equity, audit = second.funded(real)
    positions.to_csv(EXP/'funded.csv.gz')
    equity.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
    rows.extend(dict(venue='OKX',policy='wick',scenario='actual',period=p,**m) for p,m in metrics.items())
    dev, full = second.meets(metrics['dev']),second.meets(metrics['all'])
    if dev:
        for name,events,slip in [('matched_actual',real[real.parent_id.isin(control.parent_id)],0.),
                                 ('control',control,0.),('stress',real,.0005)]:
            values = second.funded(events,slip)[0]
            rows.extend(dict(venue='OKX',policy='wick',scenario=name,period=p,**m) for p,m in values.items())
    data = pd.DataFrame(rows)
    data.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(dict(dev_pass=dev,full_pass=full,account=audit),
                                               indent=2,allow_nan=False)+'\n')
    print(data[data.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
