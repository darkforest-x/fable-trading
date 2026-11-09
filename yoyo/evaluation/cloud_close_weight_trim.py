"""Trim single-contract exposure above a fixed NAV weight on completed twelve-hour bars."""
import gc
import json

import numpy as np
import pandas as pd
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_score_priority as scores
from yoyo.evaluation import cloud_close_target_expiry as expiry

admin = scores.admin
w = admin.archive.native.wallet
EXP = scores.EXP.parent / 'exp-cloud-close-weight-trim-20261109-v1'


@njit(cache=True)
def segment(c, eligible, limit, target, fraction, nlegs):
    phase = c.i % 4
    if phase == 0:
        # State and marks here still belong to the prior completed H1 close.
        previous_nav = nb.get_group_value_ctx_nb(c)
        for col in range(c.from_col, c.to_col, nlegs):
            target[col//nlegs] = -1.
            if c.i > 0 and eligible[c.i//4, col//nlegs]:
                previous_price = c.close[c.i-1, col+nlegs-1]
                quantity = np.abs(c.last_position[col:col+nlegs]).sum()
                if quantity * previous_price > previous_nav * limit:
                    target[col//nlegs] = previous_nav * limit / previous_price
    if phase == 2:
        # Native exits at the open have already occurred; trim only actual remaining units.
        for col in range(c.from_col, c.to_col, nlegs):
            goal = target[col//nlegs]
            quantity = np.abs(c.last_position[col:col+nlegs]).sum()
            fraction[col//nlegs] = 1. - goal / quantity if 0. <= goal < quantity else 0.
    return w.pre_segment(c)


@njit(cache=True)
def order(c, nav, actions, prices, fees, exposure, weights, reserve, slot_weights, fraction):
    if c.i % 4 == 2:
        ratio = fraction[c.col//len(weights)]
        if c.position_now == 0. or ratio <= 1.e-10:
            return NoOrder
        return nb.order_nb(size=-c.position_now*ratio, price=c.close[c.i,c.col], fees=w.TAKER,
                           lock_cash=True, allow_partial=False, raise_reject=True)
    return w.account_order(c, nav, actions, prices, fees, 0., exposure, weights, reserve, 0, slot_weights)


def trim_account(data, quote_loader, exposure, limit, leg_weights=(2/3,1/3)):
    """Require twelve consecutive prior H1 observations and the exact next open for each trim."""
    base = data['actions']
    hours, columns = base.shape[0]//3, base.shape[1]
    nlegs = len(leg_weights)
    if min(leg_weights)<=0 or not np.isclose(sum(leg_weights),1.) or columns % nlegs:
        raise ValueError('Invalid funded child reservations')
    eligible = np.zeros((hours, columns//nlegs), dtype=bool)
    runners = data['selected'].loc[data['selected'].slot.mod(nlegs).eq(nlegs-1)]
    if not runners.tp_r.eq(0).all() or runners.event_id.duplicated().any():
        raise ValueError('Last child must be the original cloud runner')
    for r in runners.itertuples():
        clock = quote_loader(r.symbol)[0]
        i,j = clock.searchsorted([r.entry_ms, r.exit_bar_ms+w.HOUR])
        times = clock[i:j]
        present = times[12:][(times[12:]-times[:-12] == 12*w.HOUR) & (times[12:] % (12*w.HOUR) == 0)]
        eligible[(present-data['start'])//w.HOUR,r.slot//nlegs] = True
    def expand(field):
        return data[field].reshape(hours,3,columns)[:,[0,1,1,2],:].reshape(-1,columns).copy()
    marks, prices, fees = [expand(field) for field in ('marks','prices','fees')]
    actions = expand('actions')
    actions[2::4] = 0
    fraction = np.zeros(columns//nlegs)
    pf = w.vbt.Portfolio.from_order_func(marks, order, actions, prices, fees, exposure,
        np.asarray(leg_weights), np.zeros(1), np.ones((1,1)), fraction,
        pre_segment_func_nb=segment, pre_segment_args=(eligible,limit,np.full(columns//nlegs,-1.),fraction,nlegs),
        cash_sharing=True, group_by=True, init_cash=1000., fill_pos_record=False,
        max_orders=int(np.count_nonzero(actions)+nlegs*eligible.sum()+1))
    fills = pf.orders.records.copy()
    fills['action'] = np.where(fills.idx.mod(4).eq(2), 3, actions[fills.idx, fills.col])
    fills['hour'] = data['start'] + fills.idx.floordiv(4)*w.HOUR
    for slot, children in data['selected'].groupby('slot'):
        children = children.sort_values('entry_ms')
        ids = fills.index[fills.col.eq(slot)]
        stamps = fills.loc[ids, 'hour'].to_numpy()
        ix = children.entry_ms.searchsorted(stamps, side='right')-1
        if (ix < 0).any() or (stamps > children.exit_bar_ms.to_numpy()[ix]).any():
            raise ValueError('Fill outside its original child holding interval')
        fills.loc[ids, 'event_id'] = children.event_id.to_numpy()[ix]
    opening = fills.action.abs().eq(1)
    value = fills['size'] * fills.price
    fills['notional'] = value.where(opening, 0.)
    fills['cash_pnl'] = np.where(fills.side.eq(0), -value, value)-fills.fees
    fills['net_qty'] = fills['size'].where(fills.side.eq(0), -fills['size'])
    child = fills.groupby(['event_id','col']).agg(residual=('net_qty','sum'), activity=('size','sum'))
    if len(child) != len(data['selected']) or (child.residual.abs() > child.activity*1e-9+1e-12).any():
        raise ValueError('Child position did not open and close the same quantity')
    parents = runners.set_index('event_id')[['parent_id','symbol','side','entry_ms','exit_bar_ms']].copy()
    parents = parents.join(fills.groupby('event_id')[['cash_pnl','notional']].sum(), validate='one_to_one')
    parents['net_return'] = parents.cash_pnl / parents.notional
    nav = np.asarray(pf.value()).reshape(-1,4)
    if not np.isclose(nav[-1,-1], 1000+parents.cash_pnl.sum(), rtol=1e-8, atol=.005):
        raise ValueError('Terminal marked wealth does not equal actual net cash')
    qty = np.asarray(pf.assets())[2::4]
    low, high = data['low'], data['high']
    lower = np.minimum(nav.min(axis=1),nav[:,2]+(qty*np.where(qty>=0,low,high)).sum(axis=1))
    upper = np.maximum(nav.max(axis=1),nav[:,2]+(qty*np.where(qty>=0,high,low)).sum(axis=1))
    index = pd.to_datetime(data['start'] + (1+np.arange(hours))*w.HOUR, unit='ms', utc=True)
    equity = pd.DataFrame(dict(nav=nav[:,3], open_nav=nav[:,0], open_exit_nav=nav[:,1],
                               post_trim_nav=nav[:,2], lower_nav=lower, upper_nav=upper), index=index)
    return summarize(parents,equity), parents, equity, fills[fills.action.eq(3)]


def summarize(parents,equity):
    metrics = admin.study.tenth.trend.rotation._period_metrics(parents,equity)
    # Include all four executable stages in the observed drawdown.
    index = equity.index
    nav = equity[['open_nav','open_exit_nav','post_trim_nav','nav']].to_numpy()
    for key,(lo,hi) in admin.study.tenth.trend.rotation.PERIODS.items():
        dates = (index > pd.Timestamp(lo)) if lo else np.ones(len(nav), dtype=bool)
        if hi:
            dates &= index <= pd.Timestamp(hi)
        start = np.flatnonzero(dates)[0]
        points = np.r_[nav[start-1,-1] if start else 1000.,nav[dates].ravel()]
        metrics[key]['dd_mark_pct'] = 100*float(1-(points/np.maximum.accumulate(points)).min())
    return metrics


def binance_inputs(extra):
    archive = admin.archive
    frozen = pd.read_csv(scores.EXP / 'funded.csv.gz').set_index('event_id')
    paths = archive.pool(extra)
    _, selected = admin.study.tenth.trend.inputs('Binance', paths[paths.event_id.isin(frozen.index)])
    spec = json.loads((scores.EXP / 'config.json').read_text())['additional_settlements']
    chosen = admin.adjusted_legs(selected, extra, spec)
    minute = pd.read_csv(scores.EXP / 'settlement_index_1m.csv')
    marks = minute.groupby('symbol').close.mean()
    chosen.loc[chosen.delivery,'exit_price'] = (chosen.loc[chosen.delivery,'symbol']+'USDT').map(marks)
    ranks = pd.read_csv(scores.EXP/'candidate_order.csv.gz',usecols=['event_id','new_rank']).set_index('event_id')
    if chosen.exit_price.isna().any() or chosen.delivery_ambiguous.any():
        raise ValueError('Missing documented settlement marks for the scored roster')
    chosen['entry_order'] = chosen.event_id.map(ranks.new_rank)
    return archive.native.prepare(chosen), frozen


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed weight-trim experiment')
    cap = json.loads((EXP/'config.json').read_text())['weight_cap']
    extra = set(json.loads((admin.archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    summary = {}
    for venue in ['Binance','OKX']:
        with admin.archive.market_quotes(extra):
            if venue == 'Binance':
                data, frozen = binance_inputs(extra)
                loader, exposure = admin.archive.native.quotes, .9
            else:
                paths, _ = expiry.time_in_force(expiry.trend.thirds.okx_paths())
                real = paths[~paths.is_control]
                data = admin.study.tenth.split.pair_data(real[real.tp_r.eq(0)],real[real.tp_r.eq(2)],9)
                frozen = pd.read_csv(expiry.EXP/'funded_OKX.csv.gz').set_index('event_id')
                loader, exposure = w.quotes, 1.
            metrics, parents, equity, trims = trim_account(data, loader, exposure, cap)
        if set(parents.index) != set(frozen.index):
            raise ValueError('Trimming changed the signal roster')
        parents.to_csv(EXP/f'funded_{venue}.csv.gz')
        equity.to_csv(EXP/f'hourly_{venue}.csv.gz',index_label='hour_end_utc')
        trims.to_csv(EXP/f'trims_{venue}.csv.gz',index=False)
        first = trims.hour.min() if len(trims) else np.inf
        initial = parents.index[parents.exit_bar_ms.lt(first)]
        if not np.allclose(parents.loc[initial,'cash_pnl'],frozen.loc[initial,'cash_pnl'],atol=1e-5):
            raise ValueError('Original closed trades changed before the first trim')
        summary[venue] = dict(metrics=metrics, trim_fills=len(trims), trimmed_events=trims.event_id.nunique(),
                              original_final_usdt=1000+frozen.cash_pnl.sum(), pre_trim_closed_parents=len(initial))
        del data, parents, equity, trims
        gc.collect()
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


if __name__ == '__main__':
    main()
