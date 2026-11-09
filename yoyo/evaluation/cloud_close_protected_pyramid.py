"""Reinvest the profit protected at a ratcheting stop into a profitable cloud runner."""
import json

import numpy as np
import pandas as pd
from numba import njit
from vectorbt.portfolio.enums import NoOrder, OrderStatus

from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_peak_retention as source
from yoyo.evaluation import cloud_close_second_reclaim as screens

wallet = source.wallet
EXP = source.EXP.parent / 'exp-cloud-close-protected-pyramid-20261109-v1'
HOUR = wallet.HOUR
FEE = wallet.TAKER


def setup(pool):
    """Set add thresholds using H1 highs/lows strictly before each eligible 12H open."""
    data = wallet.prepare(pool, 9)
    hours, slots = data['high'].shape
    shape = (hours, 4, slots)
    actions = np.zeros(shape, dtype='int8')
    old = data['actions'].reshape(hours, 3, slots)
    actions[:,0], actions[:,1], actions[:,3] = old[:,0], old[:,1], old[:,2]
    marks = data['marks'].reshape(hours,3,slots)[:,[0,1,1,2],:].copy()
    prices = data['prices'].reshape(hours,3,slots)[:,[0,1,1,2],:].copy()
    levels = np.zeros((hours,slots))
    prior = np.zeros_like(levels)
    for row in data['selected'].itertuples():
        clock, bars = wallet.quotes(row.symbol)
        i, end = np.searchsorted(clock, [row.entry_ms,row.exit_bar_ms])
        current = clock[i:end+1]
        if current[0] != row.entry_ms or current[-1] != row.exit_bar_ms:
            raise ValueError('Missing native quoted clock')
        index = np.arange(1,len(current))
        # An add requires a fully observed price path since the initial order.
        index = index[(current[index] - row.entry_ms == index*HOUR) & (current[index] % (12*HOUR) == 0)]
        if row.exit_phase == 'open':
            index = index[current[index] < row.exit_bar_ms]
        if not len(index):
            continue
        extreme = bars[i:end+1, 1 if row.side==1 else 2]
        move = np.maximum.accumulate(row.side * (extreme - row.entry_price))[index-1]
        risk = abs(row.entry_price - row.stop_price)
        armed = (move >= 2*risk) & (risk > 0)
        index, move = index[armed], move[armed]
        take = (current[index] - data['start']) // HOUR
        levels[take,row.slot] = row.entry_price + row.side * np.maximum(.5*risk, .25*move)
        prior[take,row.slot] = bars[i+index-1,3]
        actions[take,2,row.slot] = 3
        prices[take,2,row.slot] = bars[i+index,0]
    return data, marks.reshape(-1,slots), prices.reshape(-1,slots), actions.reshape(-1,slots), levels, prior


@njit(cache=True)
def order(c, nav, actions, prices, levels, prior, state, slip):
    action = actions[c.i,c.col]
    if not action:
        return NoOrder
    price = prices[c.i,c.col]
    if action == 2:
        return wallet.nb.order_nb(size=-c.position_now,price=price,fees=FEE,slippage=slip,
                                  lock_cash=True,allow_partial=False,raise_reject=True)
    flat = np.sum(c.last_position[c.from_col:c.to_col] == 0.)
    if action != 3:
        budget = min(nav/c.group_len, c.free_cash_now/max(1,flat))
        if budget < 1.e-8 or c.position_now != 0.:
            raise ValueError('Original reservation cannot be funded')
        size = action*budget*(1.-1.e-10)/(price*(1.+slip)*(1.+FEE))
    else:
        if c.position_now == 0.:
            return NoOrder
        side = np.sign(c.position_now)
        row = c.i//4
        level = levels[row,c.col]
        original_qty, paid, entry_fees, last_fill, last_level = state[c.col]
        entry = price*(1.+side*slip)
        stop_fill = level*(1.-side*slip)
        if (side*(prior[row,c.col]-last_fill) <= 0. or side*(entry-last_fill) <= 0.
                or side*(level-last_level) <= 0. or side*(price-level) <= 0.):
            return NoOrder
        qty = abs(c.position_now)
        protected = side*(qty*stop_fill-paid)-entry_fees-qty*stop_fill*FEE
        risk_per_unit = side*(entry-stop_fill) + (entry+stop_fill)*FEE
        if protected <= 0. or risk_per_unit <= 0.:
            return NoOrder
        collateral = c.free_cash_now/((1.+flat)*price*(1.+slip)*(1.+FEE))
        increase = min(protected/risk_per_unit, collateral, original_qty)*(1.-1.e-10)
        if increase <= 0. or increase*price < 1.e-8:
            return NoOrder
        size = side*increase
    return wallet.nb.order_nb(size=size,price=price,fees=FEE,slippage=slip,
                              lock_cash=True,allow_partial=False,raise_reject=True)


@njit(cache=True)
def record(c, nav, actions, levels, state):
    fill = c.order_result
    if fill.status != OrderStatus.Filled:
        return
    action = actions[c.i,c.col]
    if action == 2:
        state[c.col,:] = 0.
        return
    if action == 3:
        state[c.col,4] = levels[c.i//4,c.col]
    else:
        state[c.col,0] = fill.size
        # The first addition must be protected on the profitable side of entry.
        state[c.col,4] = fill.price
    state[c.col,1] += fill.size*fill.price
    state[c.col,2] += fill.fees
    state[c.col,3] = fill.price


def simulate(pool, slip=0.):
    data, marks, prices, actions, levels, prior = setup(pool)
    state = np.zeros((marks.shape[1],5))
    pf = wallet.vbt.Portfolio.from_order_func(marks, order, actions, prices, levels, prior, state, slip,
        pre_segment_func_nb=wallet.pre_segment,post_order_func_nb=record,post_order_args=(actions,levels,state),
        cash_sharing=True,group_by=True,init_cash=1000.,max_orders=int(np.count_nonzero(actions))+1,fill_pos_record=False)
    fills = pf.orders.records.copy()
    fills['action'] = actions[fills.idx,fills.col]
    fills['stamp'] = data['start'] + (fills.idx//4)*HOUR
    roster = data['selected'][['event_id','parent_id','side','symbol','entry_ms','exit_bar_ms','slot']].copy()
    for slot, rows in roster.groupby('slot'):
        rows = rows.sort_values('entry_ms')
        idx = fills.index[fills.col.eq(slot)]
        when = fills.loc[idx,'stamp'].to_numpy()
        ix = np.searchsorted(rows.entry_ms,when,side='right')-1
        if (ix<0).any() or (when > rows.exit_bar_ms.to_numpy()[ix]).any():
            raise ValueError('Order outside its active protected position')
        fills.loc[idx,'event_id'] = rows.event_id.to_numpy()[ix]
    turnover = fills['size']*fills.price
    fills['cashflow'] = np.where(fills.side.eq(0),-turnover,turnover)-fills.fees
    fills['entry_notional'] = turnover.where(fills.action.ne(2),0.)
    fills['entry_quantity'] = fills['size'].where(fills.action.ne(2),0.)
    fills['exit_quantity'] = fills['size'].where(fills.action.eq(2),0.)
    grouped = fills.groupby('event_id')
    stats = grouped.agg(cash_pnl=('cashflow','sum'),notional=('entry_notional','sum'),
                        entry_qty=('entry_quantity','sum'),exit_qty=('exit_quantity','sum'))
    positions = roster.set_index('event_id').join(stats,validate='one_to_one')
    positions['net_return'] = positions.cash_pnl/positions.notional
    if not np.allclose(positions.entry_qty,positions.exit_qty,rtol=1.e-9,atol=1.e-12):
        raise ValueError('An open/add total did not reconcile to its native close')
    nav = np.asarray(pf.value()).reshape(-1,4)
    if not np.isclose(nav[-1,-1],1000+positions.cash_pnl.sum(),atol=.01,rtol=1.e-9):
        raise ValueError('Portfolio does not reconcile to paid orders')
    qty = np.asarray(pf.assets())[2::4]
    hi,lo = data['high'],data['low']
    lower = np.minimum(nav.min(axis=1),nav[:,2]+(qty*np.where(qty>=0,lo,hi)).sum(axis=1))
    upper = np.maximum(nav.max(axis=1),nav[:,2]+(qty*np.where(qty>=0,hi,lo)).sum(axis=1))
    index = pd.to_datetime(data['start']+(1+np.arange(len(nav)))*HOUR,unit='ms',utc=True)
    eq = pd.DataFrame(dict(nav=nav[:,3],open_nav=nav[:,0],open_exit_nav=nav[:,1],post_add_nav=nav[:,2],
                           lower_nav=lower,upper_nav=upper),index=index)
    periods = {'all':(None,None),'dev':(None,'2025-01-01T00:00Z'),'2025':('2025-01-01T00:00Z','2026-01-01T00:00Z'),'2026':('2026-01-01T00:00Z',None)}
    metrics = {p:wallet.summarize(positions,eq,*dates) for p,dates in periods.items()}
    return metrics,positions,eq,fills[fills.action.eq(3)]


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Protected pyramid already evaluated')
    paths = pd.read_csv(source.EXP/'paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    paths = paths[paths.peak_retained.eq(.25)]
    repair.check_parent_sets(paths)
    real = paths[~paths.is_control]
    metrics,pos,eq,adds = simulate(real)
    rows = [dict(scenario='actual',period=p,**m) for p,m in metrics.items()]
    reference = pd.read_csv(source.EXP/'account_metrics.csv')
    ref = reference[reference.peak_retained.eq(.25) & reference.period.eq('all')].iloc[0]
    if len(pos)!=ref.trades:
        raise ValueError('Add-on policy displaced the original admission roster')
    pos.to_csv(EXP/'funded.csv.gz'); eq.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
    adds.to_csv(EXP/'add_orders.csv.gz',index=False)
    dev_pass = screens.meets(metrics['dev'])
    if dev_pass:
        controls = paths[paths.is_control & paths.parent_id.isin(real.parent_id)]
        paired = real[real.parent_id.isin(controls.parent_id)]
        for name,cohort,slip in [('paired',paired,0.),('control',controls,0.),('stress',real,.0005)]:
            more = simulate(cohort,slip)[0]
            rows.extend(dict(scenario=name,period=p,**m) for p,m in more.items())
    pd.DataFrame(rows).to_csv(EXP/'account_metrics.csv',index=False)
    summary=dict(full=metrics['all'],dev=metrics['dev'],dev_pass=dev_pass,full_pass=screens.meets(metrics['all']),
                 baseline_usdt=float(ref.final_usdt),add_orders=len(adds),parents_with_adds=int(adds.event_id.nunique()))
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__=='__main__':
    main()
