"""Use a closed doubling candle's fixed protective stop to budget one reinvestment."""
import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from numba import njit
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_deferred_fill as deferred
from yoyo.evaluation import cloud_close_doubled_stop_gap_wallet as gap
from yoyo.evaluation import cloud_close_second_window_capacity as capacity

source = gap.source
base = deferred.base
engine = deferred.engine
FEE = base.wallet.TAKER
EXP = source.ROOT / 'exp-cloud-close-locked-profit-add-20261109-v1'


def prepare():
    data, market, quotes, parents, caps, ratios = source.account()
    timing = pd.read_csv(gap.stops.EXP / 'stop_activations.csv.gz')
    panel = timing[timing['mode'].eq('fixed')].set_index('event_id', verify_integrity=True)
    panel = panel.join(parents[['symbol', 'entry_ms', 'entry_price', 'slot', 'side']])
    panel = panel[panel.index.map(caps).to_numpy() > 0].copy()
    changed = panel[panel.changed].copy()
    stops = pd.read_csv(gap.minute.EXP / 'minute_stop_receipts.csv.gz', index_col='event_id')
    if set(changed.index) != set(stops.index) or not stops.status.eq('observed').all():
        raise ValueError('Incomplete minute-priced protective stops')
    changed['exit_price'] = stops.gap_fill_price
    routed, _, _ = gap.stops.doubling.route(data, quotes, [1,1,3,3])
    protected, _ = gap.stops.apply_stops(routed, quotes, changed)
    panel['column'] = panel.slot // 4
    panel['row'] = (panel.activation_ms - data['start']) // source.H
    if panel.duplicated(['row','column']).any() or not panel.side.eq(1).all():
        raise ValueError('Ambiguous add schedule')
    def observe(p):
        clock, px = quotes(p.symbol)
        i = clock.searchsorted(p.activation_ms)
        if (clock[i] != p.activation_ms or len(clock[max(0,i-12):i]) != 12
                or not np.all(np.diff(clock[i-12:i+1]) == source.H)
                or p.activation_ms % source.STEP or clock[i-12] < p.entry_ms
                or px[i-1,3] < 2*p.entry_price
                or not np.isclose(px[i-12:i,2].min(), p.activation_low)):
            raise ValueError('Confirmation uses a missing or incomplete 12H bar')
        v = capacity._capacity_window(p.symbol, int(p.activation_ms), p.Index, quotes)
        return dict(event_id=p.Index, next_open=px[i,0], **v)
    with ThreadPoolExecutor(max_workers=3) as pool:
        windows = pd.DataFrame(pool.map(observe, panel.itertuples())).set_index('event_id')
    panel = panel.join(windows.drop(columns='symbol'), validate='one_to_one')
    panel.to_csv(EXP / 'opportunities.csv.gz')
    return protected, market, panel, parents, caps, ratios, quotes


@njit(cache=True)
def prior_parent_cash(c, first, opening_index):
    """Executed synthetic collateral debits and credits since this parent's native opening."""
    cash = 0.
    latest = np.max(c.last_oidx[first:first+4])
    for idx in range(latest, -1, -1):
        fill = c.order_records[idx]
        if fill['idx'] < opening_index:
            break
        if first <= fill['col'] < first+4:
            sign = -1. if fill['side'] == 0 else 1.
            cash += sign * fill['size'] * fill['price'] - fill['fees']
    return cash


@njit(cache=True)
def order(c, nav, actions, prices, fees, reserve, weights, unit_weights, trim, native,
          side, multiple, slip, limits, decisions, spec, planned, audit):
    hour, phase = c.i//4, c.i%4
    slot, leg = c.col//4, c.col%4
    k = decisions[hour,slot]
    if phase != 2 or k < 0 or trim[slot] > 1e-10:
        return base.trade(c,nav,actions,prices,fees,reserve,weights,unit_weights,trim,
                          native,side,multiple,slip,limits)
    first = slot*4
    if leg == 0:
        planned[first:first+4] = 0.
        shares = c.last_position[first:first+4]
        held = shares.sum()
        open_notional = native[hour,slot]
        stop_notional, exposure, entry_stage, liquidity_cap = spec[k]
        if held <= 0. or open_notional <= stop_notional or liquidity_cap <= 0.:
            return NoOrder
        add_notional = open_notional*(1.+slip)
        stop_after_slip = stop_notional*(1.-slip)
        add_price = 1.-exposure+add_notional
        liquidation_proceeds = 1.-exposure+stop_after_slip*(1.-FEE)
        add_cost = add_price+add_notional*FEE
        protected = prior_parent_cash(c,first,int(entry_stage)) + held*liquidation_proceeds
        loss_per_share = add_cost-liquidation_proceeds
        concentration = max(0., .2*nav-held*open_notional)
        spare_cash = c.free_cash_now/(1.+deferred.empty_slots(c))
        max_shares = min(protected/loss_per_share, concentration/add_notional,
                         spare_cash/add_cost, liquidity_cap/add_notional)
        audit[k,0] = protected
        audit[k,1] = loss_per_share
        audit[k,2] = concentration
        audit[k,3] = spare_cash
        audit[k,4] = add_price
        audit[k,5] = add_notional*FEE/add_price
        if max_shares*add_notional > 1.e-6:
            planned[first:first+4] = shares/held*max_shares*(1.-1.e-10)
    if planned[c.col] <= 0.:
        return NoOrder
    return base.wallet.nb.order_nb(size=planned[c.col], price=audit[k,4], fees=audit[k,5],
                                    lock_cash=True, allow_partial=False, raise_reject=True)


def verify_adds(fills, panel, audit, ratios, slip):
    """Check actual added units and floor budgets in USDT, then allocate their later native exits."""
    adds = fills[fills.side.eq(0) & fills.stage.eq(2)]
    verified = []
    for event_id, group in adds.groupby('underlying_event_id'):
        p = panel.loc[event_id]
        at = int(p.activation_ms)
        orders = fills[fills.underlying_event_id.eq(event_id)]
        before = orders[(orders.hour < at) | ((orders.hour == at) & (orders.stage < 2))]
        later = orders[(orders.hour > at) | ((orders.hour == at) & (orders.stage > 2))]
        previous_units = before.net_units.sum()
        floor = p.activation_low*(1.-slip)*(1.-FEE)
        locked = before.underlying_cash.sum()+previous_units*floor
        qty = group.net_units.sum()
        amount = group.notional.sum()
        marginal_risk = -group.underlying_cash.sum()-qty*floor
        i = panel.index.get_loc(event_id)
        a = audit[i]
        open_price = p.next_open*(1.+slip)
        frac = qty/(previous_units+qty)
        opening_units = before.groupby('col').net_units.sum().reindex(group.col,fill_value=0.).to_numpy()
        proceeds = later.underlying_cash.sum()*frac
        if (not group.hour.eq(at).all() or later.net_units.gt(0.).any() or not qty > 0
            or not np.allclose(opening_units/previous_units,group.net_units/qty,atol=1e-9)
            or not np.isclose(amount,qty*open_price,rtol=1e-9) or not np.isclose(locked,a[0],atol=1e-6)
            or not np.isclose(marginal_risk,group['size'].sum()*a[1],atol=1e-6)
            or not np.isclose(qty,-later.net_units.sum()*frac,rtol=1e-9)
            or min(locked-marginal_risk,p.second_entry_limit_10pct_usdt-amount,
                   a[2]-amount,a[3]+group.share_cash.sum()) < -1e-6):
            raise ValueError('Added native quantity, costs, exit inventory or risk/cash limits differ')
        verified.append(dict(event_id=event_id,activation_ms=at,symbol=p.symbol,quantity=qty,
            add_usdt=amount,locked_before=locked,added_stop_risk=marginal_risk,headroom=a[2],
            available_collateral=a[3],cap=p.second_entry_limit_10pct_usdt,
            attributed_cash_pnl=group.underlying_cash.sum()+proceeds))
    return pd.DataFrame(verified)


def run(context, slip):
    data, market, panel, parents, caps, ratios, _ = context
    native, previous, side, _ = market
    model, leverage = engine.collateral_model(data,market,ratios,slip)
    cap_grid = np.full_like(native,np.inf)
    cap_grid[(parents.entry_ms-data['start'])//source.H,parents.slot//4] = caps
    decision = np.full(native.shape, -1, dtype=np.int32)
    decision[panel.row,panel.column] = np.arange(len(panel))
    rates = ratios.reindex(panel.index)
    spec = np.column_stack((rates*panel.activation_low/panel.entry_price, rates,
          4*((panel.entry_ms-data['start'])//source.H),panel.second_entry_limit_10pct_usdt.fillna(0.)))
    audit = np.zeros((len(panel),6))
    pf = base.build_portfolio(data,model,native*leverage,previous*leverage,side,1.,slip,entry_limits=cap_grid,
        order_func=order,extra_order_args=(decision,spec,np.zeros(side.shape[1]*4),audit),extra_order_count=4*len(panel))
    actual = dict(data,selected=data['selected'][data['selected'].event_id.map(caps).gt(0)])
    saved = []
    funds, curve, _ = base.receipts(pf,actual,model,ratios,fill_sink=saved.append)
    fills = saved[0]
    initial = fills[fills.stage.eq(0) & fills.side.eq(0)].groupby('underlying_event_id').notional.sum()
    if (set(initial.index)!=set(caps[caps.gt(0)].index) or (initial>caps.loc[initial.index]+1e-6).any()
        or not np.allclose(gap.stops.doubling.fixed.opening_weights(fills[fills.stage.eq(0)]),
                           [1/3,1/3,1/6,1/6],atol=1e-8,rtol=0.)):
        raise ValueError('Initial funded parent/weights/cap differ')
    adds = verify_adds(fills,panel,audit,ratios,slip)
    label = 'stress' if slip else 'fees'
    funds['initial_notional'] = initial
    funds.to_csv(EXP/f'parents_{label}.csv.gz')
    adds.to_csv(EXP/f'adds_{label}.csv',index=False)
    curve.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
    ref = json.loads((gap.EXP/'summary.json').read_text())['arms']['gap_checked'+('_stress' if slip else '')]
    if not ref['margin'] or not all(m['all_flags_resolved'] for m in ref['margin'].values()):
        raise ValueError('Original margin trigger path unresolved')
    results = base.parent.spot.hourly.tested.summarize(funds,curve)
    return dict(metrics=results,reference=ref['metrics'],adds=len(adds),candidate_closes=len(panel),
                positive_adds=int(adds.attributed_cash_pnl.gt(0).sum()),added_notional=adds.add_usdt.sum(),
                added_cash_pnl=adds.attributed_cash_pnl.sum(),concentration_limited=int(np.isclose(adds.add_usdt,adds.headroom).sum()),
                liquidity_limited=int(np.isclose(adds.add_usdt,adds.cap).sum()),floor_limited=int(np.isclose(adds.added_stop_risk,adds.locked_before).sum()))


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed locked-profit add account')
    context = prepare()
    arms = {}
    for name, slip in [('fees',0.), ('stress',.0005)]:
        arms[name] = run(context,slip)
        print(name,arms[name]['metrics']['all'],arms[name]['adds'],flush=True)
    source.queue.repair.archive.prices.safe_json(EXP/'summary.json',dict(arms=arms,
             prior_minute_sources=context[2].capacity_status.value_counts().to_dict()))


if __name__ == '__main__':
    main()
