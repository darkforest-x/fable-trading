"""Fund confirmed V1.7 paths in a single cash-sharing VectorBT account.

The input contains independent framework fills. Only signal time, contract and
existing occupied slots determine admission. Entry orders use the simultaneous
hour OPEN marks of live positions. Each slot holds one real long or short with
segregated short collateral. Marks read the native 1H OHLC across its life.
"""
from __future__ import annotations

import json
import zlib
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-cloud-close-reward-20261109-v1'
SOURCE = ROOT / 'experiments/active/exp-cloud-allmarket-screen-20261108-v1'
TOP = ROOT / 'experiments/active/exp-cloud-top20-peak10r-20261108-v1'
HOUR = 3_600_000
START = pd.Timestamp('2020-01-24', tz='UTC').value // 1_000_000
END = pd.Timestamp('2026-11-08T12:00:00Z').value // 1_000_000
SPLIT = pd.Timestamp('2025-01-01', tz='UTC').value // 1_000_000
TAKER = 0.0005


@lru_cache(maxsize=None)
def quotes(symbol):
    path = SOURCE / 'inputs' / symbol / 'bars1h.csv.gz'
    if not path.is_file():
        path = TOP / 'inputs' / symbol / 'bars1h.csv.gz'
    data = pd.read_csv(path, usecols=['time', 'open', 'high', 'low', 'close'],
                       float_precision='round_trip')
    if not data.time.is_unique or not data.time.is_monotonic_increasing:
        raise ValueError(f'Duplicate or unordered hourly source: {symbol}')
    return data.time.to_numpy(), data[['open', 'high', 'low', 'close']].to_numpy()


def admit(events, slots):
    events = events.copy()
    events['priority'] = [zlib.crc32(s.encode()) for s in events.symbol]
    ranks = ['entry_order'] if 'entry_order' in events else []
    events = events.sort_values(['entry_ms', *ranks, 'priority', 'symbol', 'event_id'])
    busy_until = np.full(slots, -1, dtype=np.int64)
    symbols = np.full(slots, '', dtype=object)
    picks = []
    rejected = {'same_contract': 0, 'all_slots_busy': 0}
    for row in events.itertuples():
        busy = busy_until >= row.entry_ms
        if (busy & (symbols == row.symbol)).any():
            rejected['same_contract'] += 1
            continue
        free = np.flatnonzero(~busy)
        if not len(free):
            rejected['all_slots_busy'] += 1
            continue
        slot = int(free[0])
        symbols[slot] = row.symbol
        busy_until[slot] = row.exit_bar_ms
        picks.append((row.Index, slot))
    selected = pd.DataFrame(picks, columns=['row', 'slot']).set_index('row')
    return events.loc[selected.index].assign(slot=selected.slot), rejected


def prepare(events, slots, start=START, end=END, *, assigned=None, quote_loader=None):
    if quote_loader is None:
        quote_loader = quotes
    selected, skips = admit(events, slots) if assigned is None else assigned
    count = (end - start) // HOUR
    # Stages in every hour: openings, exits observed at open, remaining exits at end.
    shape = (count, 3, slots)
    marks = np.ones(shape)
    delta_hi = np.zeros((count, slots))
    delta_lo = np.zeros_like(delta_hi)
    actions = np.zeros(shape, dtype=np.int8)
    prices = np.ones(shape)
    fees = np.zeros(shape)
    gap_marks = 0
    for r in selected.itertuples():
        clock, hourly = quote_loader(r.symbol)
        first = (int(r.entry_ms) - start) // HOUR
        last = (int(r.exit_bar_ms) - start) // HOUR
        if not 0 <= first <= last < count:
            raise ValueError(f'Fill outside grid: {r.event_id}')
        a, b = clock.searchsorted([r.entry_ms, r.exit_bar_ms + HOUR])
        times = clock[a:b]
        loc = (times - start) // HOUR - first
        if not len(loc) or loc[0] != 0 or loc[-1] != last-first:
            raise ValueError(f'Fill with no observed OHLC: {r.event_id}')
        if len(loc) == last-first+1:
            bars = hourly[a:b]
        else:
            gap_marks += last-first+1-len(loc)
            bars = np.empty((last-first+1, 4))
            # Only an earlier observed close can mark an absent hour.
            prev = np.searchsorted(loc, np.arange(len(bars)), side='right')-1
            bars[:] = hourly[a:b, 3][prev, None]
            bars[loc] = hourly[a:b]
        slot = r.slot
        section = slice(first, last+1)
        op, hi, lo, cl = bars.T
        if not np.isclose(op[0], r.entry_price, rtol=1e-9, atol=1e-14):
            raise ValueError(f'Entry differs from executable open: {r.event_id}')
        marks[section, 0, slot] = op
        marks[section, 1, slot] = op
        marks[section, 2, slot] = cl
        delta_hi[section, slot] = hi - op
        delta_lo[section, slot] = lo - op
        # A filled limit caps the favorable move up to exit. A triggered market
        # stop caps the adverse move at its modeled fill (no within-hour gaps).
        if r.exit_phase == 'intrabar':
            if r.exit_fee_rate < TAKER:
                cap = delta_hi if r.side > 0 else delta_lo
            else:
                cap = delta_lo if r.side > 0 else delta_hi
            cap[last, slot] = r.exit_price - op[-1]
        phase = 1 if r.exit_phase == 'open' else 2
        actions[first, 0, slot] = r.side
        prices[first, 0, slot] = r.entry_price
        fees[first, 0, slot] = TAKER
        actions[last, phase, slot] = 2
        prices[last, phase, slot] = r.exit_price
        fees[last, phase, slot] = r.exit_fee_rate
    return dict(selected=selected, skips=skips, gap_marks=gap_marks,
                marks=marks.reshape(-1, slots), prices=prices.reshape(-1, slots),
                fees=fees.reshape(-1, slots), actions=actions.reshape(-1, slots),
                high=delta_hi, low=delta_lo, start=start)


@njit(cache=True)
def pre_segment(c):
    for col in range(c.from_col, c.to_col):
        c.last_val_price[col] = c.close[c.i, col]
    return (nb.get_group_value_ctx_nb(c),)


@njit(cache=True)
def pre_segment_with_drawdown(c, peak, power):
    nav = pre_segment(c)[0]
    peak[0] = max(peak[0],nav)
    # Current open and past marked account stages alone inform the next order.
    # Sizing never changes existing positions or the reported portfolio value.
    remaining = max(0.,1. - (1. - nav/peak[0])/.35)
    return (nav * max(.1,remaining**power),)


@njit(cache=True)
def account_order(c, nav, actions, prices, fees, market_slip, exposure, weights, reservation,
                  full_slots, slot_weights):
    action = actions[c.i, c.col]
    if action == 0:
        return NoOrder
    fee = fees[c.i, c.col]
    slip = market_slip if fee == TAKER else 0.
    price = prices[c.i, c.col]
    if action == 2:
        if c.position_now == 0.:
            return NoOrder
        return nb.order_nb(size=-c.position_now, price=price, fees=fee,
                           slippage=slip, lock_cash=True, allow_partial=False, raise_reject=True)
    if c.position_now != 0:
        raise ValueError('Position collision')
    # Simultaneous child orders share a single collateral reservation.
    leg = c.col % len(weights)
    if leg == 0:
        capacity = c.group_len // len(weights)
        free_slots = 0
        weight_sum = 0.
        for first in range(c.from_col,c.to_col,len(weights)):
            occupied = False
            for child in range(first,first+len(weights)):
                if c.last_position[child] != 0.:
                    occupied = True
            free_slots += int(not occupied)
            if not occupied and slot_weights.shape[0] > 1:
                weight_sum += slot_weights[c.i//3,first//len(weights)]
        if full_slots:
            # The first leg exits at the partial target. After that only its
            # runner remains, and the released collateral can fund a new entry.
            capacity = full_slots
            occupied_full = 0
            for first in range(c.from_col,c.to_col,len(weights)):
                occupied_full += int(c.last_position[first] != 0.)
            free_slots = min(free_slots,capacity - occupied_full)
            if free_slots < 1:
                raise ValueError('No full position slot available')
        if slot_weights.shape[0] > 1:
            weight = slot_weights[c.i//3,c.col//len(weights)]
            reservation[0] = min(nav * exposure / capacity * weight, c.free_cash_now*weight/weight_sum)
        else:
            reservation[0] = min(nav * exposure / capacity, c.free_cash_now/max(1,free_slots))
    budget = reservation[0] * weights[leg]
    if budget < 1.e-8:
        return NoOrder
    # Leave a rounding margin on the short and long collateral requirements.
    size = action * (budget * (1.-1.e-10) / (price*(1.+slip)*(1.+fee)))
    return nb.order_nb(size=size, price=price, fees=fee, slippage=slip,
                       lock_cash=True, allow_partial=False, raise_reject=True)


def account(data, slip=0., exposure=1., weights=(1.,), full_slots=0, slot_weights=None,
            drawdown_power=0.):
    actions = data['actions']
    weights = np.asarray(weights, dtype=float)
    if np.any(weights<=0) or not np.isclose(weights.sum(), 1) or actions.shape[1]%len(weights):
        raise ValueError('Invalid leg weights')
    if slot_weights is None:
        slot_weights = np.ones((1,1))
    elif (full_slots or slot_weights.shape!=(actions.shape[0]//3,actions.shape[1]//len(weights))
          or np.any(slot_weights<=0) or not np.isfinite(slot_weights).all()):
        raise ValueError('Invalid per-position reservation weights')
    if drawdown_power<0. or not np.isfinite(drawdown_power):
        raise ValueError('Drawdown sizing exponent must be finite and nonnegative')
    segment = pre_segment_with_drawdown if drawdown_power else pre_segment
    segment_args = (np.array([1000.]),drawdown_power) if drawdown_power else ()
    pf = vbt.Portfolio.from_order_func(data['marks'], account_order, actions, data['prices'],
        data['fees'], slip, exposure, weights, np.zeros(1), full_slots, slot_weights,
        pre_segment_func_nb=segment, pre_segment_args=segment_args, cash_sharing=True,
        group_by=True, init_cash=1000., max_orders=int(np.count_nonzero(actions))+1,
        fill_pos_record=False)
    orders = pf.orders.records
    roster = data['selected'].copy()
    start = data['start']
    stage = (roster.entry_ms - start)//HOUR * 3
    exit_stage = (roster.exit_bar_ms - start)//HOUR*3 + np.where(roster.exit_phase.eq('open'), 1, 2)
    keyed = orders.set_index(['idx','col'])
    entries = keyed.reindex(pd.MultiIndex.from_arrays([stage, roster.slot]))
    exits = keyed.reindex(pd.MultiIndex.from_arrays([exit_stage, roster.slot]))
    have = entries['size'].notna().to_numpy()
    if not have.all():
        raise ValueError('A reserved slot was left unfunded')
    if not np.array_equal(have, exits['size'].notna().to_numpy()):
        raise ValueError('Unclosed funded position')
    entries, exits = entries[have], exits[have]
    if not np.allclose(entries['size'], exits['size'], rtol=1e-9, atol=1e-12):
        raise ValueError('Entry and exit quantity mismatch')
    roster = roster[have].copy()
    roster['quantity'] = entries['size'].to_numpy()
    roster['cash_pnl'] = (roster.side * (exits.price.to_numpy()-entries.price.to_numpy()) * roster.quantity
                          -entries.fees.to_numpy()-exits.fees.to_numpy())
    roster['notional'] = roster.quantity*entries.price.to_numpy()
    roster['net_return'] = roster.cash_pnl / roster.notional
    nav = np.asarray(pf.value()).reshape(-1, 3)
    # VectorBT's reconstructed cash flow can accumulate sub-cent cancellation
    # across reused slots with base-unit quantities ranging from <1 to >1e9.
    cash_from_orders = 1000.+float((np.where(orders.side.eq(0),-1.,1.) *
                                    orders['size']*orders.price-orders.fees).sum())
    final_pnl = 1000.+roster.cash_pnl.sum()
    if (not np.isclose(cash_from_orders,final_pnl,rtol=1.e-10,atol=1.e-5)
            or not np.isclose(nav[-1,-1],cash_from_orders,rtol=1.e-10,atol=.005)):
        raise ValueError(f'Final NAV {nav[-1,-1]} versus orders {cash_from_orders} and PnL {final_pnl}')

    qty = np.asarray(pf.assets())[1::3]
    hi, lo = data['high'], data['low']
    favorable = np.where(qty >= 0, hi, lo)
    adverse = np.where(qty >= 0, lo, hi)
    # Synchronized per-asset extremes in the least favorable order give an
    # upper bound on drawdown; hourly OHLC does not reveal exact ordering.
    upper = np.maximum(nav.max(axis=1), nav[:,1]+(qty*favorable).sum(axis=1))
    lower = np.minimum(nav.min(axis=1), nav[:,1]+(qty*adverse).sum(axis=1))
    idx = pd.to_datetime(start+HOUR*(np.arange(len(nav))+1), unit='ms', utc=True)
    equity = pd.DataFrame(dict(nav=nav[:,2], open_nav=nav[:,0], open_exit_nav=nav[:,1],
                               lower_nav=lower, upper_nav=upper),index=idx)
    return roster, equity


def summarize(roster, equity, lo=None, hi=None):
    start, end = equity.index[0]-pd.Timedelta(hours=1), equity.index[-1]
    lo = pd.Timestamp(lo) if lo else start
    hi = pd.Timestamp(hi) if hi else end
    curve = equity[(equity.index>lo)&(equity.index<=hi)]
    earlier = equity[equity.index<=lo]
    initial = 1000. if earlier.empty else earlier.nav.iloc[-1]
    # Entries and exits must both be in the period for trade metrics.
    # An intrabar exit at/after the boundary cannot enter the earlier sample.
    lms, hms = lo.value//1_000_000, hi.value//1_000_000
    fills = roster[(roster.entry_ms>=lms)&(roster.exit_bar_ms<hms)]
    r = fills.net_return
    def payoff(x):
        w, l = x[x>0], x[x<0]
        return float(w.mean()/-l.mean()) if len(w) and len(l) else None
    points = np.r_[initial, curve[['open_nav','open_exit_nav','nav']].to_numpy().ravel()]
    bound = np.r_[initial,curve.lower_nav] / np.maximum.accumulate(np.r_[initial,curve.upper_nav])
    return dict(final_usdt=float(curve.nav.iloc[-1]),multiple=float(curve.nav.iloc[-1]/initial),
                dd_mark_pct=float(100*(1-(points/np.maximum.accumulate(points)).min())),
                dd_bound_pct=float(100*(1-bound.min())),trades=len(fills),
                win_pct=float(100*(r>0).mean()) if len(r) else None, payoff=payoff(r),
                payoff_usdt=payoff(fills.cash_pnl), median_net_pct=float(100*r.median()) if len(r) else None)


def evaluate(events, slots=12, slip=0., exposure=1.):
    data = prepare(events, slots)
    roster, eq = account(data, slip=slip, exposure=exposure)
    periods = {'all':(None,None),'dev':(None,'2025-01-01T00:00Z'),
               '2025':('2025-01-01T00:00Z','2026-01-01T00:00Z'),
               '2026':('2026-01-01T00:00Z',None)}
    metrics = {name:summarize(roster,eq,*limits) for name,limits in periods.items()}
    meta = dict(opportunities=len(events), admitted=len(data['selected']), funded=len(roster),
                unquoted_position_hours=data['gap_marks'], **data['skips'])
    return metrics, meta, roster, eq


def qualifies(m):
    return m['win_pct'] is not None and m['win_pct']>=30 and (m['payoff'] or 0)>=2.5 and m['dd_bound_pct']<=35


def read_paths():
    fields = ['entry_status','symbol','source_row_number','parent_id','event_id','is_control',
              'tp_r','side','entry_ms','exit_bar_ms','exit_phase','entry_price','exit_price',
              'stop_price','exit_reason','exit_fee_rate','same_bar_ambiguous','gap_flag']
    frame = pd.read_csv(EXP/'paths.csv.gz',usecols=fields,float_precision='round_trip',
                        dtype={'symbol':str},keep_default_na=False,na_values={'entry_ms':[''],
                        'exit_bar_ms':[''],'entry_price':[''],'exit_price':[''],'exit_fee_rate':[''],'stop_price':['']})
    frame = frame[frame.entry_status.eq('executed')].copy()
    for col in ('entry_ms','exit_bar_ms'):
        if frame[col].isna().any():
            raise ValueError('Executed trade has no execution timestamp')
        frame[col] = frame[col].astype('int64')
    if frame.duplicated(['event_id','tp_r']).any() or not frame.groupby('event_id').size().eq(7).all():
        raise ValueError('Executed paths must contain all seven prespecified exits')
    if (frame.exit_bar_ms < frame.entry_ms).any() or not frame.exit_phase.isin(['open','intrabar','close']).all():
        raise ValueError('Invalid exit clock')
    if not np.array_equal(frame.exit_fee_rate.lt(TAKER),frame.exit_reason.str.startswith('take_profit')):
        raise ValueError('Fee class differs from actual fill type')
    runner = frame[frame.tp_r.eq(0)].set_index('parent_id')
    control = runner[runner.is_control]
    actual = runner[~runner.is_control]
    if not control.index.is_unique or not actual.index.is_unique or not control.index.isin(actual.index).all():
        raise ValueError('Invalid matched control identity')
    if (control.entry_ms.to_numpy() <= actual.loc[control.index,'entry_ms'].to_numpy()).any():
        raise ValueError('Control inherits direction or stop geometry from a later parent')
    return frame

def main():
    paths = read_paths()
    cfg = json.loads((EXP/'account_config.json').read_text())
    all_rows, metadata, outputs = [], {}, {}
    for r in cfg['tp_r']:
        events = paths[(paths.tp_r==r)&(~paths.is_control)]
        print(f"Evaluating TP {r}R with {len(events)} actual paths",flush=True)
        m, meta, trades, eq = evaluate(events)
        for period, vals in m.items():
            all_rows.append(dict(tp_r=r,period=period,**vals))
        metadata[r] = meta
        outputs[r] = (trades, eq)
    frame = pd.DataFrame(all_rows)
    dev = frame[frame.period=='dev'].sort_values('multiple',ascending=False)
    passing = dev[dev.apply(qualifies,axis=1)]
    winner = int(passing.tp_r.iloc[0]) if len(passing) else 0
    best = int(dev.tp_r.iloc[0])
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    tests = {}
    for r in sorted({0,winner,best}):
        trades, eq = outputs[r]
        trades.to_csv(EXP/f'funded_tp{r}.csv.gz',index=False)
        eq.to_csv(EXP/f'hourly_tp{r}.csv.gz',index_label='hour_end_utc')
        events = paths[paths.tp_r==r]
        ctl = events[events.is_control]
        paired = events[(~events.is_control)&events.parent_id.isin(ctl.parent_id)]
        tests[r] = {}
        for name, pool, slip in [('control',ctl,0.),('paired_signals',paired,0.),('slippage_5bp',events[~events.is_control],.0005)]:
            m, meta, _, _ = evaluate(pool,slip=slip)
            tests[r][name] = dict(metrics=m,admission=meta)
    result = dict(engine=vbt.__version__,development_eligible=passing.tp_r.tolist(),
                  pick=winner,best_dev_growth=best,admission=metadata,checks=tests)
    (EXP/'account_summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period=='all'].to_string(index=False),flush=True)


if __name__ == '__main__':
    main()
