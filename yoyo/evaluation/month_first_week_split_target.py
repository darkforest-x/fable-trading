"""Change only the take-profit allocation in the frozen monthly short account.

The first leg reuses the corrected cap4 path. The runner reuses the original
near-edge trades and refines their terminal hour using the same verified minute
data as cap4. Admission sees only current open positions and symbol priority.
"""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import month_first_week_alt_account as baseline
from yoyo.evaluation import month_first_week_alt_be2 as minute

EXP = baseline.EXPERIMENTS / 'exp-alt-month-first-week-split-target-20261110-v1'
CONFIG = json.loads((EXP / 'config.json').read_text())
SLOTS = baseline.CONFIG['slots']
WEIGHT = .10
OHLC = ['open', 'high', 'low', 'close']


def load_parents(quotes):
    cap = baseline.load_events(quotes).rename(columns={
        'exit_bar_ms': 'cap_exit_ms', 'exit_price': 'cap_exit_price', 'fixed_reason': 'cap_reason',
        'exit_phase': 'cap_phase', 'old_net_pct': 'cap_net_pct'})
    trades = pd.read_csv(baseline.PARENT / 'trades.csv.gz', dtype={'cap': str})
    near = trades[trades.cap.eq('near_edge') & trades.side.eq('upper')].copy()
    near['event_id'] = near.trade_id + '|cap4'
    near['near_exit_ms'] = near.exit_time.map(baseline.millis)
    near = near.rename(columns={'target_price': 'near_target', 'exit_reason': 'near_reason',
                                'exit_price': 'near_exit_price'})
    columns = ['event_id', 'near_target', 'near_exit_ms', 'near_exit_price', 'near_reason',
               'stop_price', 'ambiguous_sl_tp', 'entry_fill', 'entry_time']
    parents = cap.merge(near[columns], on='event_id', validate='one_to_one')
    assert len(parents) == len(cap) == len(near)
    assert np.allclose(parents.entry_fill, parents.entry_price, rtol=1e-12, atol=0)
    assert (parents.entry_time.map(baseline.millis) == parents.entry_ms).all()
    parents['cap_target'] = np.maximum(parents.near_target, parents.entry_price * .96)
    source = pd.read_csv(baseline.FIXED / 'refined_minutes.csv.gz', parse_dates=['time','hour'])
    source = source.sort_values('time')
    minutes = {(s,baseline.millis(h)):g[OHLC].to_numpy() for (s,h),g in source.groupby(['symbol','hour'])}
    audit = {'refined_terminal_hours': 0, 'dual_terminal_trades': 0, 'minute_order_unknown': 0,
             'runner_exit_changes': 0}
    for idx, r in enumerate(parents.itertuples(index=False)):
        clock, bars = quotes(r.symbol)
        opening = bars[np.searchsorted(clock,r.near_exit_ms), 0]
        phase = 'open' if (opening <= r.near_target or opening >= r.stop_price or
                           r.near_exit_ms == r.entry_ms + 168 * wallet.HOUR) else 'intrabar'
        parents.loc[idx, 'near_phase'] = phase
        finer = minutes.get((r.symbol,r.near_exit_ms))
        if r.ambiguous_sl_tp:
            audit['dual_terminal_trades'] += 1
            if finer is None:
                raise ValueError(f'Missing minutes for ambiguous near edge: {r.event_id}')
        if finer is None or phase == 'open':
            continue
        audit['refined_terminal_hours'] += 1
        points, candle, is_open = minute.tape(finer, {}, 0., 0.)
        k, fill, reason = minute.fixed_exit(points, is_open, 0, r.stop_price, r.near_target, 'stop')
        if reason == 'timeout':
            raise ValueError(f'Unconfirmed terminal fill: {r.event_id}')
        o,h,l,_ = finer[candle[k]]
        audit['minute_order_unknown'] += int(r.near_target < o < r.stop_price and
                                             h >= r.stop_price and l <= r.near_target)
        audit['runner_exit_changes'] += int(reason != r.near_reason or
                                             not np.isclose(fill,r.near_exit_price,rtol=1e-10,atol=0))
        parents.loc[idx, ['near_exit_price','near_reason']] = [fill, reason]
    assert (parents.near_exit_ms >= parents.cap_exit_ms).all()
    equal = np.isclose(parents.near_target, parents.cap_target, rtol=1e-12, atol=0)
    same = equal | parents.cap_reason.ne('target')
    if (not (parents.loc[same,'near_exit_ms'] == parents.loc[same,'cap_exit_ms']).all() or
        not (parents.loc[same,'near_reason'] == parents.loc[same,'cap_reason']).all() or
        not np.allclose(parents.loc[same,'near_exit_price'],parents.loc[same,'cap_exit_price'],rtol=1e-10,atol=0)):
        raise ValueError('Nested target exits disagree on an identical pre-cap path')
    return parents, audit


def prepare(parents, quotes):
    # Each slot belongs to one parent until the second child has closed.
    selected, skips = wallet.admit(parents.assign(exit_bar_ms=parents.near_exit_ms), SLOTS)
    children = []
    for j, name in enumerate(('cap','near')):
        leg = selected.copy()
        leg['leg'] = name
        leg['slot'] = 2 * leg.slot + j
        for dst, suffix in [('exit_bar_ms','exit_ms'), ('exit_price','exit_price'), ('exit_phase','phase')]:
            leg[dst] = leg[name+'_'+suffix]
        leg['exit_fee_rate'] = np.where(leg[name+'_reason'].eq('target'),baseline.MAKER,baseline.TAKER)
        children.append(leg)
    data = wallet.prepare(None, 2*SLOTS, baseline.millis(baseline.CONFIG['start']),
                          baseline.millis(CONFIG['end']), assigned=(pd.concat(children,ignore_index=True),skips),
                          quote_loader=quotes)
    assert not data['gap_marks']
    return data


def shared_account(data, slip):
    legs, eq = wallet.account(data, exposure=WEIGHT*SLOTS, slip=slip, weights=(.5,.5))
    intrabar = legs[legs.exit_phase.eq('intrabar')]
    s = np.where(intrabar.exit_fee_rate.eq(baseline.TAKER),slip,0.)
    expense = intrabar.quantity * intrabar.exit_price * (s + (1+s)*intrabar.exit_fee_rate)
    hours = pd.to_datetime(intrabar.exit_bar_ms+wallet.HOUR,unit='ms',utc=True)
    eq['lower_nav'] -= expense.groupby(hours).sum().reindex(eq.index,fill_value=0.)
    first = legs[legs.leg.eq('cap')].set_index('event_id').copy()
    second = legs[legs.leg.eq('near')].set_index('event_id').loc[first.index]
    assert np.allclose(first.quantity,second.quantity,rtol=1e-12,atol=0)
    if not slip:
        np.testing.assert_allclose(100*first.net_return,first.cap_net_pct,rtol=0,atol=1e-8)
    first['cap_cash_pnl'] = first.cash_pnl
    first['near_cash_pnl'] = second.cash_pnl
    first['cash_pnl'] += second.cash_pnl
    first['notional'] += second.notional
    first['net_return'] = first.cash_pnl / first.notional
    first['exit_bar_ms'] = second.exit_bar_ms
    first['retaining_half_delta_usdt'] = first.near_cash_pnl - first.cap_cash_pnl
    np.testing.assert_allclose(1000 + first.cash_pnl.sum(),eq.nav.iloc[-1],atol=.002)
    return first, eq


def compare(funded, old):
    baseline_only = old.index.difference(funded.index)
    split_only = funded.index.difference(old.index)
    common = funded.index.intersection(old.index)
    diff = funded.retaining_half_delta_usdt
    changed = funded.cap_reason.eq('target') & (funded.near_target < funded.cap_target*(1.-1e-12))
    result = dict(funded=len(funded), common=len(common), missed=len(baseline_only), extra=len(split_only),
                  split_target_reached=int(changed.sum()),
                  runner_more=int(diff.gt(1e-8).sum()), runner_less=int(diff.lt(-1e-8).sum()),
                  added_usdt=diff.clip(lower=0).sum(), given_back_usdt=-diff.clip(upper=0).sum(),
                  runner_delta=diff.sum(), original_only_pnl=old.loc[baseline_only,'cash_pnl'].sum(),
                  extra_at_cap_pnl=2*funded.loc[split_only,'cap_cash_pnl'].sum(),
                  compounding=(2*funded.loc[common,'cap_cash_pnl']-old.loc[common,'cash_pnl']).sum(),
                  runner_stopped=int((changed & funded.near_reason.eq('stop')).sum()),
                  whole_trade_flipped_loss=int((changed & (funded.cap_cash_pnl>0) & (funded.cash_pnl<0)).sum()))
    reconstructed = result['runner_delta'] - result['original_only_pnl'] + result['extra_at_cap_pnl'] + result['compounding']
    np.testing.assert_allclose(reconstructed, funded.cash_pnl.sum()-old.cash_pnl.sum(),atol=1e-7)
    return result


def main():
    quotes = baseline.market_loader()
    parents, audit = load_parents(quotes)
    parents.to_csv(EXP / 'exit_paths.csv.gz', index=False)
    rows, effects = [], []
    for name, is_control in [('signal',False),('random',True)]:
        pool = parents[parents.is_control.eq(is_control)]
        data = prepare(pool,quotes)
        for slip_bp in (0,5):
            tag = f'{name}_10_{slip_bp}bp'
            old = pd.read_csv(baseline.EXP / f'{tag}_fills.csv.gz').set_index('event_id')
            funded, equity = shared_account(data, slip_bp/10000)
            funded.to_csv(EXP / f'{tag}_positions.csv.gz',index_label='event_id')
            equity.to_csv(EXP / f'{tag}_equity.csv.gz',index_label='hour_end_utc')
            opportunity = pool[['event_id','symbol','entry_ms']].copy()
            opportunity['baseline_pnl'] = opportunity.event_id.map(old.cash_pnl)
            opportunity['split_pnl'] = opportunity.event_id.map(funded.cash_pnl)
            opportunity.to_csv(EXP / f'{tag}_opportunities.csv.gz',index=False)
            for period, lo, hi in [('all',None,None),('early',None,CONFIG['split']),('later',CONFIG['split'],None)]:
                metrics = wallet.summarize(funded,equity,lo,hi)
                rows.append(dict(pool=name,slip_bp=slip_bp,period=period,**metrics))
                lms = baseline.millis(lo or baseline.CONFIG['start'])
                hms = baseline.millis(hi or CONFIG['end'])
                current = funded[funded.entry_ms.between(lms,hms,inclusive='left')]
                original = old[old.entry_ms.between(lms,hms,inclusive='left')]
                # No real or control parent straddles the chosen 2025 cut.
                assert current.exit_bar_ms.lt(hms).all() and original.exit_bar_ms.lt(hms).all()
                effects.append(dict(pool=name,slip_bp=slip_bp,period=period,**compare(current,original)))
    pd.DataFrame(rows).to_csv(EXP/'metrics.csv',index=False)
    pd.DataFrame(effects).to_csv(EXP/'attribution.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(dict(order_audit=audit,metrics=rows,attribution=effects),
                                               indent=2,allow_nan=False)+'\n')
    print('orders',audit,'actual split',rows[0],flush=True)


def export_excursions():
    from yoyo.evaluation.month_first_week_split_excursion import measure

    positions = pd.read_csv(EXP/'signal_10_0bp_positions.csv.gz')
    paths = measure(positions, baseline.market_loader())
    detail = positions.merge(paths, on='event_id', validate='1:1')
    if len(detail) != len(positions):
        raise ValueError('Missing marked excursion bounds for funded positions')
    detail['entry_utc'] = pd.to_datetime(detail.entry_ms, unit='ms', utc=True)
    detail['last_exit_hour_utc'] = pd.to_datetime(detail.near_exit_ms, unit='ms', utc=True)
    columns = {'event_id':'交易编号','symbol':'币种','entry_utc':'进场时间UTC',
               'last_exit_hour_utc':'余仓退出小时UTC','entry_price':'进场价',
               'cap_exit_price':'首半仓平仓价','near_exit_price':'余仓平仓价',
               'cap_cash_pnl':'首半仓净盈亏U','near_cash_pnl':'余仓净盈亏U',
               'cash_pnl':'整笔净盈亏U','retaining_half_delta_usdt':'等量余仓相比首目标多赚U',
               'gross_mfe_pct_lower_bound':'持仓毛浮盈峰值下界%',
               'gross_mfe_pct_upper_bound':'持仓毛浮盈峰值上界%',
               'gross_max_float_loss_pct_lower_bound':'持仓毛浮亏幅度下界%',
               'gross_max_float_loss_pct_upper_bound':'持仓毛浮亏幅度上界%'}
    detail[list(columns)].rename(columns=columns).to_csv(EXP/'分批止盈逐笔复盘.csv',
                                                         index=False,encoding='utf-8-sig',float_format='%.9g')


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--excursions-only', action='store_true')
    if not parser.parse_args().excursions_only:
        main()
    export_excursions()
