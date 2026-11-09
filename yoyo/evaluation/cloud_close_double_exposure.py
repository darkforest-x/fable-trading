"""Apply isolated linear exposure to the four-child, hourly-trimmed V1.7 account."""
import gc
import json

import numpy as np
import pandas as pd
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder

from yoyo.evaluation import cloud_close_double_limit as parent
from yoyo.evaluation import cloud_close_tenr_exposure as study

wallet = study.wallet
WEIGHTS = np.array([1/3,1/3,1/6,1/6])
EXP = parent.EXP.parent/'exp-cloud-close-double-exposure-20261109-v1'


def original_children():
    rows,quotes,_ = parent.paths('OKX')
    cached = pd.read_csv(parent.EXP/'doublings_OKX.csv').drop_duplicates('event_id')
    limits = []
    original = rows[rows.tp_r.eq(0)].set_index('event_id')
    for r in cached.itertuples():
        clock,ohlc = quotes(r.symbol)
        at = clock.searchsorted(r.limit_ms)
        stop = original.loc[r.event_id,'stop_price']
        if clock[at]!=r.limit_ms or not np.isclose(r.price,2*original.loc[r.event_id,'entry_price']):
            raise ValueError('Frozen doubling limit or clock changed')
        opening = ohlc[at,0]>=r.price
        limits.append(dict(event_id=r.event_id,exit_bar_ms=r.limit_ms,exit_reason='take_profit_open' if opening else 'take_profit',
                      exit_phase='open' if opening else 'intrabar',exit_price=r.price,
                      exit_fee_rate=study.leverage.MAKER,same_bar_ambiguous=bool(ohlc[at,2]<=stop)))
    data,changed = parent.assemble(rows,pd.DataFrame(limits),'OKX',quotes)
    if len(changed)!=len(pd.read_csv(parent.EXP/'doublings_OKX.csv')):
        raise ValueError('Frozen doubling-price child executions changed')
    return data


def underlying(data,quote_loader=wallet.quotes):
    """Native price per original entry and availability of the previous closed H1 at each review."""
    n = len(data['actions'])//3
    slots = data['actions'].shape[1]//len(WEIGHTS)
    native_open = np.zeros((n,slots))
    previous_close = np.zeros_like(native_open)
    directions = np.zeros((n,slots),dtype='int8')
    runners = data['selected'].loc[data['selected'].slot.mod(4).eq(3)]
    for r in runners.itertuples():
        clock,x = quote_loader(r.symbol)
        a,b = clock.searchsorted([r.entry_ms,r.exit_bar_ms+wallet.HOUR])
        t = clock[a:b]
        h = ((t-data['start'])//wallet.HOUR).astype('int64')
        slot = r.slot//4
        native_open[h,slot] = x[a:b,0]/r.entry_price
        directions[h,slot] = r.side
        adjacent = np.diff(t)==wallet.HOUR
        previous_close[h[1:][adjacent],slot] = x[a:b-1,3][adjacent]/r.entry_price
    return native_open,previous_close,directions,runners


@njit(cache=True)
def mark_and_cap(c,multiple,previous_close,share_target,trim):
    phase = c.i%4
    if phase==0:
        previous_equity = nb.get_group_value_ctx_nb(c)
        for col in range(c.from_col,c.to_col,4):
            slot = col//4
            share_target[slot] = -1.
            notional_per_share = multiple*previous_close[c.i//4,slot]
            shares = c.last_position[col:col+4].sum()
            if notional_per_share>0 and shares*notional_per_share>previous_equity*.2:
                share_target[slot] = previous_equity*.2/notional_per_share
    if phase==2:
        for col in range(c.from_col,c.to_col,4):
            goal = share_target[col//4]
            held = c.last_position[col:col+4].sum()
            trim[col//4] = 1.-goal/held if 0<=goal<held else 0.
    return wallet.pre_segment(c)


@njit(cache=True)
def trade(c,nav,actions,prices,fees,reserve,weights,unit_weights,trim,native_open,directions,multiple,slip):
    if c.i%4==2:
        slot = c.col//4
        if c.position_now<=0. or trim[slot]<=1.e-10:
            return NoOrder
        underlying_ratio = native_open[c.i//4,slot]
        side = directions[c.i//4,slot]
        if underlying_ratio<=0 or side==0:
            raise ValueError('No real underlying quote at trim')
        price = c.close[c.i,c.col]-multiple*underlying_ratio*slip
        fee = wallet.TAKER*multiple*underlying_ratio*(1-side*slip)/price
        return nb.order_nb(size=-c.position_now*trim[slot],price=price,fees=fee,lock_cash=True,
                           allow_partial=False,raise_reject=True)
    return wallet.account_order(c,nav,actions,prices,fees,0.,1.,weights,reserve,0,unit_weights)


def receipts(pf, data, model, multiple):
    fills = pf.orders.records.copy()
    fills['stage'] = fills.idx.mod(4)
    fills['hour'] = data['start']+fills.idx.floordiv(4)*wallet.HOUR
    for slot,group in data['selected'].groupby('slot'):
        group = group.sort_values('entry_ms')
        idx = fills.index[fills.col.eq(slot)]
        times = fills.loc[idx,'hour'].to_numpy()
        ix = group.entry_ms.searchsorted(times,side='right')-1
        if (ix<0).any() or (times>group.exit_bar_ms.to_numpy()[ix]).any():
            raise ValueError('Unassigned native holding at fill')
        for column in ('event_id','side','entry_price','exit_fee_rate'):
            fills.loc[idx,'underlying_'+column] = group[column].to_numpy()[ix]
    sign = fills.underlying_side
    base = fills.underlying_entry_price
    opened = fills.side.eq(0)
    scale = fills['size']*multiple/base
    spot_fill = base*(1+sign*(fills.price-1)/multiple)
    real_notional = scale*spot_fill
    expected_fee = real_notional*np.where(opened|fills.stage.eq(2),wallet.TAKER,fills.underlying_exit_fee_rate)
    if not np.allclose(fills.fees,expected_fee,rtol=1.e-8,atol=1.e-7):
        raise ValueError('Collateral share fee differs from actual underlying fee')
    fills['notional'] = real_notional.where(opened,0.)
    fills['underlying_cash'] = np.where(opened,-sign,sign)*real_notional-fills.fees
    fills['share_cash'] = np.where(opened,-1.,1.)*fills['size']*fills.price-fills.fees
    fills['net_units'] = np.where(opened,1.,-1.)*scale
    balances = fills.groupby(['underlying_event_id','col']).agg(units=('net_units','sum'),volume=('net_units',lambda x:x.abs().sum()))
    if (balances.units.abs()>balances.volume*1.e-9+1.e-10).any() or len(balances)!=len(data['selected']):
        raise ValueError('Some funded underlying native coin units remain unsettled')
    g = fills.groupby('underlying_event_id')[['notional','underlying_cash','share_cash']].sum()
    if not np.allclose(g.underlying_cash,g.share_cash,rtol=1.e-8,atol=.00001):
        raise ValueError('Per-parent native and collateral cash differ')
    runners = data['selected'][data['selected'].slot.mod(4).eq(3)].set_index('event_id')
    positions = runners[['parent_id','side','symbol','entry_ms','exit_bar_ms']].join(g.rename(columns={'share_cash':'cash_pnl'}))
    positions['net_return'] = positions.cash_pnl/positions.notional
    values = np.asarray(pf.value()).reshape(-1,4)
    qty = np.asarray(pf.assets())[2::4]
    low = np.minimum(values.min(axis=1),values[:,2]+(qty*model['low']).sum(axis=1))
    high = np.maximum(values.max(axis=1),values[:,2]+(qty*model['high']).sum(axis=1))
    index = pd.to_datetime(data['start']+(np.arange(len(values))+1)*wallet.HOUR,unit='ms',utc=True)
    curve = pd.DataFrame(dict(nav=values[:,3],open_nav=values[:,0],open_exit_nav=values[:,1],
                              post_trim_nav=values[:,2],lower_nav=low,upper_nav=high),index=index)
    if not np.isclose(1000+positions.cash_pnl.sum(),curve.nav.iat[-1],rtol=1.e-9,atol=.001):
        raise ValueError('Terminal equity differs from underlying trade cash')
    return positions,curve,int(fills.stage.eq(2).sum())


def simulate(data,market,multiple,slip,*,directory=EXP,quote_loader=wallet.quotes):
    native_open,previous_close,directions,runners = market
    audit,coverage = study.leverage._margin_audit(runners.reset_index(drop=True),multiple,slip,quote_loader=quote_loader)
    coverage['affected_contracts'] = sorted(audit.loc[audit.maintenance_boundary_hit,'symbol'].unique())
    if not coverage['valid']:
        return dict(audit=coverage)
    model,_ = study.leverage._syntheticize(dict(data,actions=data['actions'].copy()),multiple,slip)
    hours,cols = len(data['actions'])//3,data['actions'].shape[1]
    def expand(field):
        return model[field].reshape(hours,3,cols)[:,[0,1,1,2],:].reshape(-1,cols).copy()
    actions = expand('actions')
    actions[2::4] = 0
    trim = np.zeros(cols//4)
    portfolio = wallet.vbt.Portfolio.from_order_func(expand('marks'),trade,actions,expand('prices'),expand('fees'),
        np.zeros(1),WEIGHTS,np.ones((1,1)),trim,native_open,directions,multiple,slip,
        pre_segment_func_nb=mark_and_cap,pre_segment_args=(multiple,previous_close,np.full(cols//4,-1.),trim),
        cash_sharing=True,group_by=True,init_cash=1000.,fill_pos_record=False,
        max_orders=int(np.count_nonzero(actions)+4*np.count_nonzero(previous_close)+1))
    positions,equity,trims = receipts(portfolio,data,model,multiple)
    if multiple==1 and not slip and directory==EXP:
        old = pd.read_csv(parent.EXP/'funded_OKX.csv.gz',index_col='event_id').sort_index()
        base = pd.read_csv(parent.EXP/'hourly_OKX.csv.gz',index_col=0)
        ours = positions.sort_index()
        if (not ours.index.equals(old.index) or not np.allclose(ours.cash_pnl,old.cash_pnl,rtol=1.e-7,atol=.0001)
            or not np.allclose(equity[base.columns],base,rtol=1.e-7,atol=.005)):
            raise ValueError('Reconstructed 1x doubled-limit cash or hourly marks differ from frozen account')
    else:
        label = f'{multiple:g}x'+('_stress' if slip else '')
        positions.to_csv(directory/f'funded_{label}.csv.gz')
        equity.to_csv(directory/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
    return dict(metrics=parent.spot.hourly.tested.summarize(positions,equity),audit=coverage,trim_fills=trims)


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed hourly trimmed doubling-limit notional study')
    data = original_children()
    market = underlying(data)
    arms = {}
    for multiple in [1.,1.25,1.5,2.]:
        arms[multiple] = simulate(data,market,multiple,0.)
        print(multiple,arms[multiple].get('metrics',{}).get('all'),arms[multiple]['audit']['maintenance_boundary_hits'],flush=True)
        gc.collect()
    limits = json.loads((study.owner.EXP/'config.json').read_text())['owner_acceptance']
    valid = [m for m,r in arms.items() if 'metrics' in r and study.owner.qualifies(r['metrics']['dev'],limits)]
    pick = max(valid,key=lambda m:arms[m]['metrics']['dev']['final_usdt']) if valid else None
    stress = simulate(data,market,pick,.0005) if pick else None
    (EXP/'summary.json').write_text(json.dumps(dict(cases=arms,development_pick=pick,market_5bp=stress,
        limits=limits,scope='Frozen 291-market cohort; funding, venue-wide historical listing and mark-based historical tiers unresolved.'),
        indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
