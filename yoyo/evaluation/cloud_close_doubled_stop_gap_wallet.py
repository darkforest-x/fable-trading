"""Reprice genuine post-doubling fixed stops at their first traded minute and count actual exiting units."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_doubled_stop_minutes as minute

stops = minute.stops
source = stops.source
EXP = source.ROOT/'exp-cloud-close-doubled-stop-gap-wallet-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed reconciled minute stop account')
    info = pd.read_csv(minute.EXP/'minute_stop_receipts.csv.gz',index_col='event_id')
    timing = pd.read_csv(stops.EXP/'stop_activations.csv.gz')
    fixed = timing[timing.changed & timing['mode'].eq('fixed')].set_index('event_id').copy()
    if not (info.status.eq('observed').all() and set(fixed.index)==set(info.index)
            and info.gap_bps.ge(-1e-8).all() and info.exit_phase.eq('intrabar').all()):
        raise ValueError('Incomplete or ambiguous source coverage')
    changed = info[~np.isclose(info.exit_price,info.gap_fill_price,rtol=0.,atol=1e-12)]
    if not (changed.first_trade_minute_ms.gt(changed.exit_bar_ms).all()
            and changed.exit_bar_ms.lt(changed.original_exit_ms).all()):
        raise ValueError('A new gap competes with same-hour native stop; resolve with minute chronology first')
    fixed['exit_price'] = info.gap_fill_price
    # All checked exits happened intrabar. Preserve the original parent/H1 cash decision order.
    data,market,quotes,parents,caps,ratios = source.account()
    runner_data,_,_ = stops.doubling.route(data,quotes,[1,1,3,3])
    model,_ = stops.apply_stops(runner_data,quotes,fixed)
    results = {}
    bases = json.loads((stops.EXP/'summary.json').read_text())['arms']
    reference = {s:bases[s]['metrics'] for s in ['fixed','fixed_stress']}
    for slip in [0.,.0005]:
        key = 'gap_checked'+('_stress' if slip else '')
        actuals = {}
        def save_orders(fills):
            weights = stops.doubling.fixed.opening_weights(fills)
            if not np.allclose(weights,[1/3,1/3,1/6,1/6],atol=1e-8,rtol=0.) or set(weights.index)!=set(caps[caps.gt(0)].index):
                raise ValueError('The original funded entry units or roster differ')
            candidates = model['selected'].loc[model['selected'].exit_reason.eq('confirmed_double_candle_stop'),
                                               ['event_id','slot','exit_bar_ms']]
            sold = fills.loc[fills.net_units.lt(0) & fills.stage.eq(3)].merge(candidates,
                left_on=['underlying_event_id','col','hour'],right_on=['event_id','slot','exit_bar_ms'],
                how='inner',validate='one_to_one')
            expected = sold.event_id.map(info.gap_fill_price)*(1.-slip)
            sold['exit_usdt'] = (sold.underlying_cash+sold.fees).abs()
            if not np.allclose(sold.exit_usdt, -sold.net_units*expected,rtol=1e-8,atol=1e-8):
                raise ValueError('Native gap-checked fill cash differs from traded quantity and price')
            by = sold.groupby('event_id').agg(quantity=('net_units','sum'),exit_usdt=('exit_usdt','sum'),fees=('fees','sum'))
            if set(by.index)!=set(info.index):
                raise ValueError('A checked native stop did not actually trade')
            by = by.join(info[['symbol','first_trade_minute_ms','trigger_minute_turnover']])
            by['quantity'] *= -1
            by['turnover_share'] = by.exit_usdt/by.trigger_minute_turnover
            by.to_csv(EXP/f'minute_fills_{key}.csv.gz')
            actuals.update(checked_stops=len(by),max_turnover_share=by.turnover_share.max(),
                over_entire_minute=int(by.turnover_share.gt(1).sum()))
        out = source.queue.engine.simulate(model,market,quotes,ratios,slip,EXP,key,
                                           entry_limits=caps,fill_sink=save_orders)
        if 'metrics' not in out or not actuals:
            raise ValueError('Unresolved gap-adjusted native account')
        results[key] = dict(**out,**actuals)
        print(key,out['metrics']['all'],actuals,flush=True)
    source.queue.repair.archive.prices.safe_json(EXP/'summary.json',dict(arms=results,hourly_reference=reference,
        actual_minutes=len(info),changed_gap_prices=len(changed)))


if __name__ == '__main__':
    main()
