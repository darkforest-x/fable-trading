"""Frozen BTC RSI1h/six-MA5m experiment statistics and reproducible artifacts.

See the experiment PROJECT_PLAN.md for owner decisions. All model-free
comparisons are exploratory. Random entries match asset, side, calendar
month, causal volatility quintile, risk percentage and fixed 3R barriers;
they are an entry-timing null, not a serial executable portfolio. Returns
subtract 20bp of entry notional; funding is not supplied by this study.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.btc_rsi_sixma import prepare, simulate, trace_trade

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT/'experiments/active/exp-btc-rsi1h-sixma5m-20260920-v1'
START = pd.Timestamp('2023-09-19T21:00:00Z')
SPLIT = pd.Timestamp('2025-09-19T21:00:00Z')
END = pd.Timestamp('2026-09-19T21:00:00Z')
SEED = 20260920


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value, (list,tuple,np.ndarray)):
        return [clean(v) for v in value]
    if isinstance(value, (pd.Timestamp,Path)):
        return str(value)
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def dump(path, value):
    path.write_text(json.dumps(clean(value), indent=2, ensure_ascii=False, allow_nan=False))


def describe(tr):
    if len(tr) == 0:
        return {'n':0}
    net = tr.net_r.to_numpy(float)
    bp = tr.net_return.to_numpy(float)*10000
    curve = np.r_[0,net.cumsum()]
    losing = longest = 0
    for loss in net < 0:
        losing = losing+1 if loss else 0
        longest = max(longest,losing)
    wins, losses = net[net>0].sum(), -net[net<0].sum()
    monthly=tr.assign(month=tr.entry_time.dt.strftime('%Y-%m')).groupby('month').net_r.agg(['sum','count']).to_numpy(float)
    rng=np.random.default_rng(SEED)
    sampled=monthly[rng.integers(0,len(monthly),(10000,len(monthly)))].sum(axis=1)
    mean_ci=np.quantile(sampled[:,0]/sampled[:,1],[.025,.975])
    return dict(n=len(tr), win_rate=float((net>0).mean()), gross_win_rate=float((tr.gross_r>0).mean()),
                gross_r_sum=float(tr.gross_r.sum()), net_r_sum=float(net.sum()),
                net_r_mean=float(net.mean()), gross_bp_mean=float(tr.gross_return.mean()*10000),
                mean_net_r_month_bootstrap_95=mean_ci.tolist(),
                net_bp_mean=float(bp.mean()), net_bp_sum=float(bp.sum()),
                profit_factor_r=float(wins/losses) if losses else None,
                profit_factor_bp=float(bp[bp>0].sum()/-bp[bp<0].sum()) if (bp<0).any() else None,
                max_closed_drawdown_r=float((np.maximum.accumulate(curve)-curve).max()),
                longest_net_loss_streak=int(longest), risk_pct_median=float(tr.risk_pct.median()),
                risk_pct_p10=float(tr.risk_pct.quantile(.1)), risk_pct_p90=float(tr.risk_pct.quantile(.9)),
                cost_r_mean=float((.002/tr.risk_pct).mean()), holding_hours_median=float(tr.holding_bars.median()/12),
                holding_hours_p95=float(tr.holding_bars.quantile(.95)/12),
                wait_minutes_median=float(tr.wait_bars.median()*5),
                dual_touch=int(tr.dual_touch.sum()),
                optimistic_dual_touch_net_r_sum=float(net.sum()+4*tr.dual_touch.sum()),
                break_even_roundtrip_cost_bp=float(tr.gross_return.mean()*10000),
                min_trade_net_r=float(net.min()), max_trade_net_r=float(net.max()))


def phase_masks(tr):
    return {'all': np.ones(len(tr),bool),
            'early_closed': (tr.entry_time < SPLIT) & (tr.exit_time < SPLIT),
            'late': tr.entry_time >= SPLIT,
            'cross_split': (tr.entry_time < SPLIT) & (tr.exit_time >= SPLIT),
            'long':tr.side==1, 'short':tr.side==-1}


def controls(ctx, tr, arm, out):
    rng = np.random.default_rng(SEED)
    frame = ctx['frame']
    ix = frame.index
    months = ix.strftime('%Y-%m').to_numpy()
    # Each entry sees volatility at its preceding closed 5m bar only.
    buckets = np.r_[-1,np.asarray(ctx['vol_bucket'],int)[:-1]]
    scope = (ix >= START) & (ix < END) & (buckets >= 0)
    pools = {}
    for month in np.unique(months[scope]):
        for bucket in range(5):
            pools[(month,bucket)] = np.flatnonzero(scope & (months==month) & (buckets==bucket))
    rows = []
    for row in tr.itertuples():
        ei = int(row.entry_i)
        pool = pools.get((months[ei],int(buckets[ei])),np.array([],dtype=int))
        pool = pool[pool != ei]
        if row.entry_time < SPLIT:
            pool = pool[ix[pool] < SPLIT]
            control_end = int(ix.searchsorted(SPLIT)) if row.exit_time < SPLIT else len(frame)
        else:
            pool = pool[ix[pool] >= SPLIT]
            control_end = len(frame)
        chosen = rng.choice(pool, size=min(100,len(pool)), replace=False)
        for ci in chosen:
            entry = float(frame.open.iloc[ci])
            stop = entry*(1-int(row.side)*float(row.risk_pct))
            r = trace_trade(ctx,int(ci),int(row.side),stop,control_end)
            if r['reason'] == 'open':
                # Keep the outcome of every preselected control, including
                # censored positions; never resample based on a future exit.
                net_return = int(row.side)*(float(frame.close.iloc[control_end-1])-entry)/entry-.002
                r = dict(r, net_return=net_return, net_r=net_return/float(row.risk_pct))
            rows.append(dict(parent_trade_id=row.trade_id, control_entry_i=int(ci),
                month=months[ei], vol_bucket=int(buckets[ei]), side=int(row.side),
                parent_entry_time=row.entry_time, parent_exit_time=row.exit_time,
                control_entry_time=ix[ci], risk_pct=float(row.risk_pct),
                actual_net_r=float(row.net_r), actual_net_return=float(row.net_return),
                control_net_r=float(r['net_r']),control_net_return=float(r['net_return']),
                control_exit_i=r['exit_i'],control_reason=r['reason']))
    result = pd.DataFrame(rows)
    result.to_csv(out/f'{arm}_controls.csv.gz',index=False,compression='gzip')
    return result


def control_summary(c, seed=SEED):
    if len(c)==0:
        return {'matched_trades':0}
    pairs = c.groupby('parent_trade_id',sort=False).agg(
        month=('month','first'), actual_r=('actual_net_r','first'), control_r=('control_net_r','mean'),
        actual_ret=('actual_net_return','first'), control_ret=('control_net_return','mean'),
        count=('control_net_r','size'))
    pairs['diff_r'] = pairs.actual_r-pairs.control_r
    pairs['diff_ret'] = pairs.actual_ret-pairs.control_ret
    blocks = pairs.groupby('month').agg(r=('diff_r','sum'),ret=('diff_ret','sum'),n=('diff_r','size'))
    values = blocks[['r','ret','n']].to_numpy(float)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0,len(values),(10000,len(values)))
    bs = values[draws].sum(axis=1)
    bootstrap_r,bootstrap_bp = bs[:,0]/bs[:,2],bs[:,1]/bs[:,2]*10000
    signs = rng.choice([-1,1],(20000,len(values)))
    # Explicit reduction avoids spurious floating-point matmul warnings from
    # this Mac's BLAS backend; inputs and outputs must remain finite.
    null_r = (signs*values[None,:,0]).sum(axis=1)/values[:,2].sum()
    if not np.isfinite(null_r).all():
        raise ValueError('non-finite permutation null')
    observed = float(pairs.diff_r.mean())
    return dict(matched_trades=len(pairs), controls=len(c), minimum_matches=int(pairs['count'].min()),
                months=len(blocks), actual_net_r_mean=float(pairs.actual_r.mean()),
                control_net_r_mean=float(pairs.control_r.mean()), excess_r_mean=observed,
                control_net_bp_mean=float(pairs.control_ret.mean()*10000),
                excess_bp_mean=float(pairs.diff_ret.mean()*10000),
                excess_r_month_bootstrap_95=np.quantile(bootstrap_r,[.025,.975]),
                excess_bp_month_bootstrap_95=np.quantile(bootstrap_bp,[.025,.975]),
                month_sign_permutation_p_greater=float((1+(null_r>=observed).sum())/(1+len(null_r))),
                terminal_control_count=int(c.control_reason.isin(['open','end_open','terminal','data_end']).sum()),
                method='paired timing null; matched month/side/causal-vol quintile/risk%; 100 controls max; 10000 month bootstrap; 20000 month sign permutations')


def grouped_rows(tr,c,arm):
    result=[]
    groups=[('phase',key,mask) for key,mask in phase_masks(tr).items()]
    for freq,fmt in [('year','%Y'),('month','%Y-%m')]:
        labels=tr.entry_time.dt.strftime(fmt)
        groups += [(freq,key,labels==key) for key in labels.unique()]
    quarters=tr.entry_time.dt.year.astype(str)+'Q'+tr.entry_time.dt.quarter.astype(str)
    groups += [('quarter',key,quarters==key) for key in quarters.unique()]
    wait=pd.cut(tr.wait_bars*5,[-1,5,30,60,180,np.inf],labels=['<=5m','5-30m','30-60m','1-3h','>3h'])
    risk=pd.cut(tr.risk_pct,[0,.002,.005,.01,np.inf],labels=['<=0.2%','0.2-0.5%','0.5-1%','>1%'])
    for name,labels in [('wait',wait),('risk',risk)]:
        groups += [(name,str(key),labels==key) for key in labels.dropna().unique()]
    for group,label,mask in groups:
        subset=tr.loc[mask]
        cc=c.loc[c.parent_trade_id.isin(subset.trade_id)] if len(c) else c
        row=dict(arm=arm,group=group,label=label,**describe(subset))
        if len(cc):
            means=cc.groupby('parent_trade_id').agg(cr=('control_net_r','mean'),cb=('control_net_return','mean'))
            row.update(control_net_r_mean=float(means.cr.mean()),control_net_bp_mean=float(means.cb.mean()*10000),
                       matched_n=len(means),excess_net_r_mean=float(subset.net_r.mean()-means.cr.mean()))
        result.append(row)
    return result


def mark_curve(ctx,result):
    """Closed-five-minute marked cumulative R with fixed equal initial risk.

    This is additive R, not an account simulation. Deduct the full modeled
    round-trip charge at entry; candles that exit use actual modeled fills.
    Intrabar equity extremes remain unknown from five-minute OHLC alone.
    """
    frame=ctx['frame'];begin=int(frame.index.searchsorted(START))
    values=np.zeros(len(frame),float);realised=0.;cursor=begin
    holdings=pd.concat([result['trades'],result['open_positions']],ignore_index=True).sort_values('entry_i')
    for row in holdings.itertuples():
        ei=int(row.entry_i)
        values[cursor:ei]=realised
        closed=pd.notna(row.exit_i)
        end=int(row.exit_i) if closed else len(frame)
        marked=int(row.side)*(frame.close.iloc[ei:end].to_numpy()-row.entry_price)/row.entry_price-.002
        values[ei:end]=realised+marked/row.risk_pct
        if closed:
            realised+=row.net_r
            values[end]=realised
            cursor=end+1
        else:cursor=len(frame)
    values[cursor:]=realised
    curve=pd.DataFrame({'close_time':frame.index[begin:]+pd.Timedelta(minutes=5),'marked_net_r':values[begin:]})
    peak=np.maximum.accumulate(np.r_[0.,curve.marked_net_r.to_numpy()])[1:]
    curve['drawdown_r']=peak-curve.marked_net_r
    return curve


def plot_results(results,out):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(3,1,figsize=(12,11),layout='constrained')
    for arm,res in results.items():
        tr=res['trades']
        axes[0].plot(tr.exit_time,tr.net_r.cumsum(),label=arm,lw=1.5)
    axes[0].set(title='BTC perpetual | cumulative closed-trade net R (20bp cost)',ylabel='Net R')
    axes[0].axhline(0,color='gray',lw=.6);axes[0].legend();axes[0].axvline(SPLIT,color='gray',ls='--')
    tr=results['sixma']['trades']
    month=tr.groupby(tr.entry_time.dt.strftime('%Y-%m')).net_r.sum()
    axes[1].bar(month.index,month.values,color=np.where(month.values>=0,'#228b77','#c34c52'))
    axes[1].tick_params(axis='x',rotation=65,labelsize=7)
    axes[1].set(title='Six-MA confirmation | net R by entry month',ylabel='Net R')
    axes[2].scatter(tr.risk_pct*100,.002/tr.risk_pct,s=12,alpha=.5)
    axes[2].set(xlabel='Initial stop distance (%)',ylabel='Round-trip cost (R)',title='Cost burden from fixed hourly stop')
    for ax in axes:ax.grid(alpha=.15)
    fig.savefig(out/'overview.png',dpi=160);plt.close(fig)


def run(out):
    if out.exists():
        raise ValueError('immutable run output already exists')
    builders=['yoyo/evaluation/btc_rsi_sixma.py','yoyo/evaluation/btc_rsi_sixma_study.py',
              'yoyo/evaluation/parabolic_rsi_sar.py','yoyo/evaluation/spike_v6_bb_squeeze.py',
              'yoyo/data/btc_rsi_research_source.py']
    for rel in builders:
        tracked=subprocess.check_output(['git','show','HEAD:'+rel],cwd=ROOT)
        if tracked != (ROOT/rel).read_bytes():
            raise ValueError(f'commit builder before scoring: {rel}')
    data=EXP/'data/okx_btc_usdt_swap_5m.csv.gz'
    frame=pd.read_csv(data,index_col='open_time',parse_dates=True)
    frame.index=pd.to_datetime(frame.index,utc=True)
    ctx=prepare(frame)
    out.mkdir(parents=True)
    results={};summaries={};grouped=[]
    for arm in ['direct','sixma']:
        result=simulate(ctx,START,END,arm=arm)
        opened=result['open_positions']
        if len(opened):
            opened=opened.copy()
            opened['terminal_mark_time']=END
            opened['terminal_mark_price']=float(frame.close.iloc[-1])
            opened['marked_net_return']=opened.side*(opened.terminal_mark_price-opened.entry_price)/opened.entry_price-.002
            opened['marked_net_r']=opened.marked_net_return/opened.risk_pct
            result['open_positions']=opened
        results[arm]=result
        for key,value in result.items():
            if isinstance(value,pd.DataFrame):value.to_csv(out/f'{arm}_{key}.csv',index=False)
        tr=result['trades']
        for col in ['entry_time','exit_time']:
            tr[col]=pd.to_datetime(tr[col],utc=True)
        c=controls(ctx,tr,arm,out)
        curve=mark_curve(ctx,result)
        curve.to_csv(out/f'{arm}_marked_curve.csv.gz',index=False,compression='gzip')
        stats={}
        for label,mask in phase_masks(tr).items():
            subset=tr.loc[mask]
            stats[label]=dict(**describe(subset),control=control_summary(c.loc[c.parent_trade_id.isin(subset.trade_id)]))
        evaluation_events=result['events'].loc[result['events'].status!='outside_window']
        summaries[arm]=dict(stats=stats,events=len(evaluation_events),
            events_with_warmup=len(result['events']),
            event_status_counts=result['events'].status.value_counts().to_dict(),open_positions=len(result['open_positions']),
            max_5m_close_marked_drawdown_r=float(curve.drawdown_r.max()),terminal_marked_net_r=float(curve.marked_net_r.iloc[-1]))
        grouped += grouped_rows(tr,c,arm)
        print(arm,json.dumps(clean(stats['all']),ensure_ascii=False),flush=True)
    pd.DataFrame(grouped).to_csv(out/'grouped_metrics.csv',index=False)
    hourly=ctx['hourly'].copy()
    hourly['rsi']=ctx['rsi'];hourly['sar']=ctx['sar'];hourly['diamond_side']=ctx['diamond_side']
    hourly.to_csv(out/'hourly_indicators.csv.gz',compression='gzip')
    plot_results(results,out)
    summary=dict(start=START,end_exclusive=END,split=SPLIT,seed=SEED,bars=len(frame),
        evaluation_bars=int(((frame.index>=START)&(frame.index<END)).sum()),
        data_sha256=sha(data),builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        builder_sha256={rel:sha(ROOT/rel) for rel in builders},
        generated_at=pd.Timestamp.now(tz='UTC'),arms=summaries,
        limitations=['No historical funding charge series','No native TradingView event export parity',
                     'Historical exploratory temporal recheck, not blind validation',
                     'Same-5m dual barriers conservatively stop-first',
                     'Fixed risk R sums are not compounded account returns',
                     'Random timing controls preserve risk distance, not hourly stop geometry'])
    dump(out/'summary.json',summary)
    dump(out/'manifest.json',{str(p.relative_to(out)):dict(sha256=sha(p),bytes=p.stat().st_size)
                              for p in sorted(out.iterdir()) if p.is_file()})


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=EXP/'results/run_v1')
    run(p.parse_args().output)
