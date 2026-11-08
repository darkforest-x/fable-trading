"""Research one causal trailing stop added to precomputed V1.7 runner fills.

Only at the next hour's open may the prior closed H1 high or low tighten the
order. Native runner exits bound each opportunity's lifetime. The callback
can consult its original adverse market fill only on the hour of that fill.
"""
import json
import numpy as np
import pandas as pd
import vectorbt as vbt
from numba import njit
from vectorbt.portfolio import nb
from vectorbt.portfolio.enums import NoOrder
from yoyo.evaluation import cloud_close_reward_account as w

EXP = w.ROOT / 'experiments/active/exp-cloud-close-trail-20261109-v1'
WIDTHS = (1,2,3,4)


@njit(cache=True)
def trail_order(c, bars, times, start, end, sides, risk, widths, exit_px, phase,
                peaks, why, exit_stage):
    col = c.from_col
    s = sides[col]
    pos = start[col]+c.i
    if pos > end[col]:
        return -1, NoOrder
    op,hi,lo,cl = bars[pos]
    entry = bars[start[col],0]
    if c.i==0 and c.call_idx==0:
        peaks[col] = entry
        return col,nb.order_nb(size=s,price=entry,fees=w.TAKER,raise_reject=True)
    if c.call_idx!=(1 if c.i==0 else 0) or c.last_position[col]==0:
        return -1,NoOrder
    at_end = pos==end[col]
    px = np.nan
    if at_end and phase[col]==1:
        px=exit_px[col]
        exit_stage[col]=1
    else:
        if c.i:
            prev = bars[pos-1]
            peaks[col]=max(peaks[col],prev[1]) if s>0 else min(peaks[col],prev[2])
        armed = c.i>0 and (peaks[col]-entry)*s>=2*risk[col] and risk[col]>0
        level = peaks[col]-s*widths[col]*risk[col]
        adverse = lo if s>0 else hi
        if armed and (op-level)*s<=0:
            px=op
            why[col]=1
            exit_stage[col]=1
        elif armed and (adverse-level)*s<=0 and (
                not at_end or phase[col]==3 or (level-exit_px[col])*s>0):
            px=level
            why[col]=1
            exit_stage[col]=2
        elif at_end:
            px=exit_px[col]
            exit_stage[col]=phase[col]
    if not np.isfinite(px):
        return -1,NoOrder
    return col,nb.order_nb(size=-c.last_position[col],price=px,fees=w.TAKER,raise_reject=True)


def symbol_paths(group):
    clock,bars = w.quotes(group.symbol.iloc[0])
    bars=np.ascontiguousarray(bars)
    starts=clock.searchsorted(group.entry_ms)
    ends=clock.searchsorted(group.exit_bar_ms)
    if not np.array_equal(clock[starts],group.entry_ms) or not np.array_equal(clock[ends],group.exit_bar_ms):
        raise ValueError('No original quote for runner fill')
    # Sorting by observed length only packs all independent columns more tightly.
    group=group.assign(first=starts,last=ends,span=ends-starts+1).sort_values('span')
    results=[]
    for offset in range(0,len(group),12):
        batch=group.iloc[offset:offset+12]
        events=batch.loc[batch.index.repeat(len(WIDTHS))].reset_index(drop=True)
        start=events['first'].to_numpy()
        end=events['last'].to_numpy()
        n=len(events)
        ind=np.minimum(start[None,:]+np.arange(batch.span.max())[:,None],end[None,:])
        marked=np.ascontiguousarray(bars[ind,3])
        risk=np.abs(events.entry_price-events.stop_price).to_numpy(dtype=float)
        phase=events.exit_phase.map({'open':1,'intrabar':2,'close':3}).to_numpy(dtype=np.int8)
        why=np.zeros(n,dtype=np.int8)
        stage=why.copy()
        widths=np.tile(WIDTHS,len(batch))
        pf=vbt.Portfolio.from_order_func(marked,trail_order,bars,clock,start,end,
            events.side.to_numpy(),risk,widths,events.exit_price.to_numpy(),phase,
            np.empty(n),why,stage,flexible=True,cash_sharing=False,
            init_cash=np.maximum(10.,10*events.entry_price.to_numpy()),max_orders=n*2,fill_pos_record=False)
        orders=pf.orders.records.sort_values(['col','id'])
        opened=orders.iloc[::2]
        closed=orders.iloc[1::2]
        if len(orders)!=2*n or not np.array_equal(closed.col,np.arange(n)) or not opened.idx.eq(0).all():
            raise ValueError('Incomplete VectorBT pair')
        if not np.allclose(opened.price,events.entry_price,rtol=2e-10,atol=1e-15):
            raise ValueError('Runner entry mismatch')
        events['giveback_r']=widths
        events['exit_price']=closed.price.to_numpy()
        events['exit_phase']=pd.Series(stage).map({1:'open',2:'intrabar',3:'close'}).to_numpy()
        events['exit_bar_ms']=clock[start+closed.idx.to_numpy()]
        events.loc[why==1,'exit_reason']='trailing_stop'
        events.loc[why==1,'gap_flag']=False
        events['exit_fee_rate']=w.TAKER
        results.append(events.drop(columns=['first','last','span']))
    return pd.concat(results,ignore_index=True)


def main():
    output=EXP/'trailing_paths.csv.gz'
    if not output.is_file():
        paths=w.read_paths()
        runner=paths[paths.tp_r.eq(0)]
        all_paths=pd.concat([symbol_paths(g) for _,g in runner.groupby('symbol')],ignore_index=True)
        all_paths.to_csv(output,index=False)
    else:
        all_paths=pd.read_csv(output,keep_default_na=False,float_precision='round_trip')
    base=pd.read_csv(w.EXP/'account_metrics.csv')
    rows=base[base.tp_r.eq(0)].drop(columns='tp_r').assign(giveback_r=0).to_dict('records')
    samples={}
    for width in WIDTHS:
        events=all_paths[all_paths.giveback_r.eq(width)&~all_paths.is_control]
        metrics,admission,trades,eq=w.evaluate(events)
        samples[width]=(trades,eq)
        rows.extend(dict(giveback_r=width,period=p,**v) for p,v in metrics.items())
    result=pd.DataFrame(rows)
    result.to_csv(EXP/'account_metrics.csv',index=False)
    dev=result[result.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(w.qualifies,axis=1)&dev.trades.ge(100)]
    pick=int(eligible.giveback_r.iloc[0]) if len(eligible) else 0
    best=int(dev.giveback_r.iloc[0])
    checks={}
    for width in sorted({best,pick}-{0}):
        trades,eq=samples[width]
        trades.to_csv(EXP/f'funded_{width}.csv.gz',index=False)
        eq.to_csv(EXP/f'hourly_{width}.csv.gz',index_label='hour_end_utc')
        sample=all_paths[all_paths.giveback_r.eq(width)]
        real,null=sample[~sample.is_control],sample[sample.is_control]
        checks[width]={}
        for name,events,slip in [('control',null,0.),('paired_signals',real[real.parent_id.isin(null.parent_id)],0.),
                                 ('slippage_5bp',real,.0005)]:
            checks[width][name]=w.evaluate(events,slip=slip)[0]
    summary=dict(pick=pick,best_dev_growth=best,eligible=eligible.giveback_r.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(result[result.period.eq('all')].to_string(index=False),flush=True)


if __name__=='__main__':
    main()
