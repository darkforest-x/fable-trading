"""Price protective stops below closed 12H candles after a genuine post-entry 2x close."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_doubling_sale_fraction as doubling
from yoyo.evaluation import cloud_close_trail as trail

source = doubling.source
wallet = doubling.wallet
EXP = source.ROOT/'exp-cloud-close-doubled-candle-low-20261109-v1'
PHASE = {'open':1,'intrabar':2,'close':3}
MODES = ('fixed','ratchet')


def stop_path(r, clock, prices):
    """First completed twelve native H1 closes at >=2x; fixed or rising low of completed 12H candles."""
    first, last = clock.searchsorted([r.entry_ms,r.exit_bar_ms+source.H])
    t = clock[first:last]
    x = np.ascontiguousarray(prices[first:last])
    if not len(t) or t[0]!=r.entry_ms or t[-1]!=r.exit_bar_ms or not np.all(np.diff(t)==source.H):
        raise ValueError('Missing native H1 in the original long runner path')
    if len(t)<=12 or x[:-1,1].max()<2*r.entry_price:
        return []
    if t[0] % source.STEP or not np.isclose(x[0,0],r.entry_price):
        raise ValueError('Unexpected original 12H clock or open')
    blocks = (len(x)-1)//12
    if not blocks:
        return []
    complete = x[:12*blocks].reshape(blocks,12,4)
    hits = np.flatnonzero(complete[:,-1,3]>=2*r.entry_price)
    if not len(hits):
        return []
    first_hit = int(hits[0])
    active = 12*(first_hit+1)
    if active==len(t)-1 and r.exit_phase=='open':
        return []
    lows = complete[first_hit:,:,2].min(axis=1)
    ratchet = np.maximum.accumulate(lows)
    levels = np.full((len(t),2),np.nan)
    levels[active:,0] = lows[0]
    levels[active:,1] = ratchet[(np.arange(active,len(t))-active)//12]
    marks = np.repeat(x[:,3,None],2,axis=1)
    why = np.zeros(2,dtype=np.int8)
    phase = np.zeros_like(why)
    pf = trail.vbt.Portfolio.from_order_func(marks,trail.trail_order,x,t,np.zeros(2,dtype=np.int64),
        np.full(2,len(t)-1),np.ones(2,dtype=np.int8),np.full(2,abs(r.entry_price-r.stop_price)),
        np.ones(2),np.full(2,r.exit_price),np.full(2,PHASE[r.exit_phase],dtype=np.int8),
        np.empty(2),why,phase,0.,False,levels,flexible=True,cash_sharing=False,
        init_cash=max(10.,10*r.entry_price),max_orders=4)
    orders = pf.orders.records.sort_values(['col','id'])
    if (len(orders)!=4 or not np.allclose(orders.iloc[::2].price,r.entry_price)
            or not np.allclose(orders['size'],1.)):
        raise ValueError('Native protective fills failed entry/quantity reconciliation')
    rows = []
    for j,(_,order) in enumerate(orders.iloc[1::2].iterrows()):
        i = int(order.idx)
        if why[j] and (i<active or not np.isclose(order.price,min(x[i,0],levels[i,j]))):
            raise ValueError('Protective stop filled early or above executable level')
        if not why[j] and (t[i]!=r.exit_bar_ms or not np.isclose(order.price,r.exit_price)):
            raise ValueError('An untriggered protective stop changed the original fill')
        rows.append(dict(event_id=r.event_id, mode=MODES[j], activation_ms=t[active],activation_low=lows[0],
            changed=bool(why[j]),exit_bar_ms=t[i],exit_phase={1:'open',2:'intrabar',3:'close'}[phase[j]],
            exit_price=order.price, original_exit_ms=r.exit_bar_ms, original_exit_price=r.exit_price))
    return rows


def apply_stops(data, quotes, changes):
    selected = data['selected'].copy().reset_index(drop=True)
    changed = 0
    for idx,r in selected.loc[selected.event_id.isin(changes.index)].iterrows():
        stop = changes.loc[r.event_id]
        if (r.exit_bar_ms,PHASE[r.exit_phase]) < (stop.exit_bar_ms,PHASE[stop.exit_phase]):
            continue
        if (r.exit_bar_ms,PHASE[r.exit_phase]) == (stop.exit_bar_ms,PHASE[stop.exit_phase]):
            if r.exit_reason.startswith('take_profit'):
                raise ValueError('The original 2R limit remained active after a completed doubling confirmation')
            if r.exit_phase=='open' or (r.exit_phase=='intrabar' and r.exit_price>=stop.exit_price):
                continue
        selected.loc[idx,['exit_bar_ms','exit_phase','exit_price','exit_fee_rate','exit_reason']] = [
            stop.exit_bar_ms,stop.exit_phase,stop.exit_price,wallet.TAKER,'confirmed_double_candle_stop']
        changed += 1
    end = data['start']+len(data['actions'])//3*wallet.HOUR
    out = wallet.prepare(None,data['actions'].shape[1],data['start'],end,
                         assigned=(selected,data['skips']),quote_loader=quotes)
    if out['gap_marks']!=0:
        raise ValueError('Missing held H1 in candle-stop account')
    return out,changed


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed doubled 12H candle runner study')
    data,market,quotes,parents,caps,ratios = source.account()
    runner_data,_,_ = doubling.route(data,quotes,[1,1,3,3])
    findings = []
    for r in parents.reset_index().itertuples():
        if r.side==1:
            findings.extend(stop_path(r,*quotes(r.symbol)))
    events = pd.DataFrame(findings)
    events.to_csv(EXP/'stop_activations.csv.gz',index=False)
    reference = json.loads((doubling.EXP/'summary.json').read_text())['arms']
    results = {'no_extra_stop':reference['no_doubling_sale'], 'no_extra_stop_stress':reference['no_doubling_sale_stress']}
    counts = {}
    for mode in MODES:
        updates = events[events['mode'].eq(mode)&events.changed].set_index('event_id',verify_integrity=True)
        inputs,n = apply_stops(runner_data,quotes,updates)
        counts[mode] = dict(parents_with_new_stop=len(updates),funded_with_new_stop=int(caps.reindex(updates.index).gt(0).sum()),
                            changed_children=n)
        for slip in [0.,.0005]:
            key = mode+('_stress' if slip else '')
            result = source.queue.engine.simulate(inputs,market,quotes,ratios,slip,EXP,key,entry_limits=caps)
            if 'metrics' not in result or result['metrics']['all']['trades']!=int(caps.gt(0).sum()):
                raise ValueError('Unresolved candle-stop account')
            print(key,result['metrics']['all'],flush=True)
            results[key] = result
    source.queue.repair.archive.prices.safe_json(EXP/'summary.json',dict(arms=results,counts=counts,
        confirmed_parent_activations=events.event_id.nunique(),funded_activations=int(caps.loc[events.event_id.unique()].gt(0).sum())))


if __name__ == '__main__':
    main()
