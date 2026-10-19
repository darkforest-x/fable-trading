"""Compare quantity allocation on frozen V13.1 trade paths (owner 2026-10-19).

Only entry fraction, retracement depth, entry method and expiry vary. Decisions
read OHLC through the current chart bar; reclaim orders use a closed bar and fill
at the next open. Protection uses prior closes/ATR14 and the original entry/R.
Archived exits only bound the evaluation and identify original opposite exits.
No final MAE/MFE is used to admit an order. Original occupancy is preserved even
with zero initial quantity. Prices, quantities and fees use the original risk
denominator, preventing apparent gains from risk renormalization or leverage.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import _smma
from yoyo.evaluation.spike_v126_htf_recheck import complete_bars
from yoyo.evaluation.spike_v131_excursions import MANIFEST, PARENT, EXTRA, provenance, trace
from yoyo.evaluation.spike_v131_symbol_summary import CONFIG as SUMMARY_CONFIG, load
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec
from yoyo.evaluation.spike_v7_fast import _initial_position_fast

EXP = Path('experiments/active/exp-spike-v131-staged-entry-search-20261019-v1')
CONFIG = EXP / 'config.json'
EXCURSIONS = Path('experiments/active/exp-spike-v131-excursions-20261019-v1/results_v1/trades.csv.gz')


def leg_paths(*, side, entry, stop0, tick, o, h, l, c, atr, e, x, opposite, trail, depths):
    """First eligible fill for each depth, independent of initial quantity/expiry.

    Stop gaps and known opposite exits cancel pending adds before the open.
    Intrabar adds strictly above/below protection can fill before a later stop.
    A trailed stop inside the limit cancels that limit, avoiding post-exit fills.
    """
    risk = side * (entry - stop0)
    assert risk > 0 and x >= e
    raw = entry - side * np.asarray(depths) * risk
    limits = (np.floor(raw / tick) if side == 1 else np.ceil(raw / tick)) * tick
    valid = (limits > 0) & (side * (limits - stop0) > tick * 1e-7)
    n = len(depths)
    prices = {k: np.full(n, np.nan) for k in ['limit', 'touch', 'reclaim']}
    offsets = {k: np.full(n, -1, dtype=int) for k in prices}
    touched = np.zeros(n, dtype=bool)
    pending = np.zeros(n, dtype=bool)
    stop, armed = stop0, False
    for j in range(e, x + 1):
        opened_exit = side * (o[j] - stop) <= 0 or (j == x and opposite)
        if opened_exit:
            exit_price, exit_i = float(o[j]), j
            break
        take = pending & np.isnan(prices['reclaim'])
        prices['reclaim'][take], offsets['reclaim'][take] = o[j], j - e
        pending[:] = False
        active = valid & (side * (limits - stop) > tick * 1e-7)
        adverse = l[j] if side == 1 else h[j]
        for method, penetration in [('limit', tick), ('touch', 0.0)]:
            crossed = side * (adverse - limits) <= -penetration + tick * 1e-7
            take = active & np.isnan(prices[method]) & crossed
            # Opening beyond the limit is price improvement; exact touches need
            # penetration later in the bar in the conservative primary model.
            fill = np.minimum(o[j], limits) if side == 1 else np.maximum(o[j], limits)
            prices[method][take], offsets[method][take] = fill[take], j - e
        stop_hit = l[j] <= stop if side == 1 else h[j] >= stop
        if stop_hit:
            exit_price, exit_i = float(stop), j
            break
        touched |= valid & (side * (adverse - limits) <= tick * 1e-7)
        if side * (c[j] - entry) >= 0:
            pending = touched & np.isnan(prices['reclaim'])
        armed = armed or side * (c[j] - entry) / risk >= 2.0
        if armed and math.isfinite(atr[j]) and atr[j] > 0:
            raw_stop = c[j] - side * trail * atr[j]
            candidate = (math.floor(raw_stop / tick) if side == 1 else math.ceil(raw_stop / tick)) * tick
            stop = max(stop, candidate) if side == 1 else min(stop, candidate)
    else:
        raise ValueError('exit not reproduced')
    assert exit_i == x, 'actual exit bound must have been recovered first'
    base_r = (side * (exit_price - entry) - .002 * entry) / risk
    out = dict(net_r=base_r, actual_exit_price=exit_price)
    for method in prices:
        for k, depth in enumerate(depths):
            prefix = f'{method}_{int(round(depth*100)):02d}'
            price = prices[method][k]
            out[prefix+'_offset'] = int(offsets[method][k])
            out[prefix+'_price'] = None if np.isnan(price) else float(price)
            out[prefix+'_r'] = 0.0 if np.isnan(price) else float((side*(exit_price-price)-.002*price)/risk)
    return out


def worker(args):
    symbol, source, targets, controls, tick, depths = args
    before = provenance(source)
    raw = pd.read_csv(source, usecols=['ts','open','high','low','close','volume'])
    assert raw.ts.is_monotonic_increasing and not raw.ts.duplicated().any()
    base = raw.drop(columns='ts').set_axis(pd.to_datetime(raw.ts.to_numpy(),unit='ms',utc=True))
    base = base[(base.index >= pd.Timestamp('2022-11-01T00:00:00Z')) &
                (base.index + pd.Timedelta(minutes=5) <= pd.Timestamp('2026-09-23T04:00:00Z'))]
    rows = []
    for minutes, group in targets.groupby('timeframe_min'):
        minutes = int(minutes)
        bars, _ = complete_bars(base, minutes)
        prev = bars.close.shift()
        tr = pd.concat([bars.high-bars.low,(bars.high-prev).abs(),(bars.low-prev).abs()],axis=1).max(axis=1)
        bars['atr'] = _smma(tr,14)
        arrays = {k:bars[k].to_numpy(float) for k in ['open','high','low','close','atr']}
        gaps = bars.index.to_series().diff().ne(pd.Timedelta(minutes=minutes)).to_numpy()
        gaps[0] = False
        picked = controls[controls.timeframe_min.eq(minutes)]
        original_controls = None
        if minutes == 60 and len(picked):
            original_controls = pd.read_csv(PARENT/f'{symbol}_{minutes}m'/'controls.csv.gz')
            original_controls = original_controls[original_controls.control_pool.eq('retest') &
                original_controls.matched.eq(True) & original_controls.trade_key.isin(picked.trade_key)].set_index('trade_key')
            assert len(original_controls) == len(picked)
        for cohort, part in [('signal',group),('control',picked)]:
            for row in part.to_dict('records'):
                side = int(row['side'])
                if cohort == 'signal':
                    entry = float(row['entry_price'])
                    stop = round((entry-side*row['initial_risk'])/tick)*tick
                    e = bars.index.get_loc(pd.Timestamp(row['entry_time']))
                    x = bars.index.get_loc(pd.Timestamp(row['actual_exit_time']))
                    opposite = row['actual_exit_kind'] == 'opposite'
                else:
                    old = original_controls.loc[row['trade_key']].to_dict() if minutes == 60 else row
                    anchor = int(old['control_anchor_i'] if minutes in (15,60) else old['control_sig'])
                    pos = _initial_position_fast(bars.index, arrays['open'],arrays['high'],arrays['low'],
                        arrays['close'],arrays['atr'],gaps,anchor,side,ExecutionSpec(tick=tick))
                    assert pos is not None
                    stop = float(pos['initial_stop'])
                    e = int(old['control_confirmation_i'])+1 if minutes in (15,60) else bars.index.get_loc(pd.Timestamp(row['control_entry_time']))
                    entry = float(arrays['open'][e])
                    x = bars.index.get_loc(pd.Timestamp(old['control_exit_time'] if minutes != 15 else old['exit_time']))
                    original_r = float(old['control_net_r'] if minutes != 15 else old['net_r'])
                    risk = side*(entry-stop)
                    original_exit = entry + side*(original_r*risk + .002*entry)
                    if minutes in (15,60):
                        opposite = str(old['control_exit_reason']).startswith('opposite')
                    else:
                        # These archives omit reason. A stop exit away from open
                        # has a unique fill; an open fill is opposite or a gap.
                        opposite = abs(original_exit-arrays['open'][x]) <= max(tick*1e-6,entry*1e-12)
                    recovered = trace(side=side,entry=entry,stop0=stop,tick=tick,e=e,x=x,opposite=opposite,
                        trail=2 if minutes==60 else 4,**{a:arrays[b] for a,b in [('o','open'),('h','high'),('l','low'),('c','close'),('atr','atr')]})
                    if minutes != 60:
                        assert recovered['actual_exit_i'] == x
                        np.testing.assert_allclose(recovered['actual_exit_price'],original_exit,rtol=0,atol=max(tick*1e-6,entry*1e-10))
                    x = recovered['actual_exit_i']
                    opposite = recovered['actual_exit_kind'] == 'opposite'
                assert not gaps[e:x+1].any()
                assert abs(entry-arrays['open'][e]) <= max(tick*1e-6,entry*1e-12)
                result = leg_paths(side=side,entry=entry,stop0=stop,tick=tick,e=e,x=x,opposite=opposite,
                    trail=2 if minutes==60 else 4,depths=depths,
                    **{a:arrays[b] for a,b in [('o','open'),('h','high'),('l','low'),('c','close'),('atr','atr')]})
                expected = row['net_r'] if cohort=='signal' else row['expected_r']
                np.testing.assert_allclose(result['net_r'],expected,atol=1e-8,rtol=0)
                rows.append(dict(cohort=cohort,trade_key=row['trade_key'],symbol=symbol,timeframe_min=minutes,
                    side=side,entry_time=str(bars.index[e]),exit_time=str(bars.index[x]),entry_price=entry,
                    initial_stop=stop,initial_risk=side*(entry-stop),tick=tick)|result)
    assert before == provenance(source)
    return rows,before


def candidates(cfg):
    out = [dict(method='constant',fraction=w,depth=0.0,expiry=0) for w in cfg['entry_fractions']]
    for method in cfg['methods']:
        for depth in cfg['depths_r']:
            for expiry in cfg['expiry_bars']:
                out += [dict(method=method,fraction=w,depth=depth,expiry=expiry) for w in cfg['entry_fractions'] if w<1]
    return [dict(candidate_id=i,**p) for i,p in enumerate(out)]


def candidate_returns(frame, policy, touch=False):
    w = policy['fraction']
    base = frame.net_r.to_numpy(float)
    if policy['method']=='constant':
        return w*base, np.zeros(len(frame),bool)
    method = 'touch' if touch and policy['method']=='limit' else policy['method']
    if method=='reclaim_capped':method='reclaim'
    prefix = f"{method}_{int(round(policy['depth']*100)):02d}"
    offset = frame[prefix+'_offset'].to_numpy(int)
    fill = offset>=0
    if policy['expiry']:
        fill &= offset<policy['expiry']
    leg=frame[prefix+'_r'].to_numpy(float)
    if policy['method']=='reclaim_capped':
        exposure=frame.side.to_numpy()*(frame[prefix+'_price'].to_numpy()-frame.initial_stop.to_numpy())/frame.initial_risk.to_numpy()
        leg=leg/np.maximum(1.0,exposure)
    return w*base + (1-w)*np.where(fill,leg,0.0),fill


def stat(r):
    pos = float(r[r>0].sum()); neg = -float(r[r<0].sum())
    return dict(n=len(r),sum_net_r=float(r.sum()),mean_net_r=float(r.mean()) if len(r) else None,
        win_rate=float((r>0).mean()) if len(r) else None,positive_r=pos,negative_r=neg,
        pf=pos/neg if neg else ('inf' if pos else 'undefined'))


def period_mask(frame,period,cut):
    if period=='early': return (frame.entry_time<cut)&(frame.exit_time<cut)
    if period=='later': return frame.entry_time>=cut
    return pd.Series(True,index=frame.index)


def block_uncertainty(target,control,policy,seed):
    """Weekly block bootstrap for a frozen policy; not search-adjusted evidence."""
    r,_=candidate_returns(target,policy)
    weeks=target.entry_time.dt.strftime('%G-W%V').to_numpy()
    delta=r-target.net_r.to_numpy(float)
    block=pd.DataFrame(dict(week=weeks,delta=delta,n=1)).groupby('week').sum()
    rng=np.random.default_rng(seed)
    if len(block):
        ix=rng.integers(len(block),size=(3000,len(block)))
        means=block.delta.to_numpy()[ix].sum(axis=1)/block.n.to_numpy()[ix].sum(axis=1)
        interval=np.quantile(means,[.025,.975]).tolist()
    else: interval=None
    out=dict(delta_vs_full_mean_r=float(delta.mean()),weekly_bootstrap_95=interval,blocks=len(block),
             inference='Exploratory fixed-policy interval; historical/search reuse, no selection-adjusted significance claim.')
    if len(control):
        paired=target.set_index('trade_key').loc[control.trade_key].reset_index()
        a,_=candidate_returns(paired,policy);b,_=candidate_returns(control,policy)
        difference=a-b
        pblock=pd.DataFrame(dict(week=paired.entry_time.dt.strftime('%G-W%V').to_numpy(),difference=difference,n=1)).groupby('week').sum()
        ix=rng.integers(len(pblock),size=(3000,len(pblock)))
        means=pblock.difference.to_numpy()[ix].sum(axis=1)/pblock.n.to_numpy()[ix].sum(axis=1)
        out.update(matched=len(control),matched_excess_mean_r=float(difference.mean()),
                   matched_weekly_bootstrap_95=np.quantile(means,[.025,.975]).tolist())
    return out


def aggregate(frame,cfg,output):
    frame=frame.copy()
    for c in ['entry_time','exit_time']:frame[c]=pd.to_datetime(frame[c],utc=True)
    targets=frame[frame.cohort.eq('signal')].reset_index(drop=True)
    controls=frame[frame.cohort.eq('control')].reset_index(drop=True)
    assert len(targets)==21547 and len(controls)==3131
    assert not targets.trade_key.duplicated().any() and not controls.trade_key.duplicated().any()
    policies=candidates(cfg);cut=pd.Timestamp(cfg['split'])
    rows=[];selections=[]
    groups=[('all',targets)]+[(f'{tf}m',g) for tf,g in targets.groupby('timeframe_min')]+[(f'{tf}m_side{side}',g) for (tf,side),g in targets.groupby(['timeframe_min','side'])]
    for name,whole in groups:
        group_rows=[];slices={}
        for period in ['full','early','later']:
            t=whole[period_mask(whole,period,cut)].reset_index(drop=True)
            c=controls[controls.trade_key.isin(t.trade_key)&period_mask(controls,period,cut)].reset_index(drop=True)
            pair=t.set_index('trade_key').loc[c.trade_key].reset_index()
            slices[period]=(t,c)
            for p in policies:
                r,fill=candidate_returns(t,p)
                a,_=candidate_returns(pair,p);b,_=candidate_returns(c,p)
                group_rows.append(dict(group=name,period=period,**p,**stat(r),
                    add_fills=int(fill.sum()),add_rate=float(fill.mean()),delta_sum_r=float((r-t.net_r.to_numpy()).sum()),
                    matched=len(c),matched_target_mean_r=float(a.mean()) if len(c) else None,
                    matched_control_mean_r=float(b.mean()) if len(c) else None,
                    matched_excess_mean_r=float((a-b).mean()) if len(c) else None))
        table=pd.DataFrame(group_rows)
        def best(period,mask=None):
            part=table[table.period.eq(period)]
            if mask is not None:part=part[mask(part)]
            return part.sort_values(['sum_net_r','fraction','candidate_id'],ascending=[False,False,True]).iloc[0].to_dict()
        full,early=best('full'),best('early')
        choice=dict(group=name,full_best=full,early_best=early,
            best_half=best('full',lambda a:a.fraction.eq(.5)),
            best_positive_initial=best('full',lambda a:a.fraction.gt(0)),
            early_selected_later=table[table.period.eq('later')&table.candidate_id.eq(early['candidate_id'])].iloc[0].to_dict(),
            full_best_later=table[table.period.eq('later')&table.candidate_id.eq(full['candidate_id'])].iloc[0].to_dict())
        for period,p,label in [('full',full,'full_best'),('later',early,'early_selected_later')]:
            t,c=slices[period]
            choice[label+'_uncertainty']=block_uncertainty(t,c,p,cfg['seed'])
            r,fill=candidate_returns(t,p,touch=True)
            choice[label+'_touch_sensitivity']=stat(r)|dict(add_fills=int(fill.sum()))
        original=table[table.method.eq('constant')&table.fraction.eq(1)].to_dict('records')
        proposal=table[table.method.eq('limit')&table.fraction.eq(.5)&table.depth.eq(.5)&table.expiry.eq(0)].to_dict('records')
        choice.update(original=original,half_at_minus05=proposal)
        if name=='all':
            yearly=[]
            for year,t in whole.groupby(whole.entry_time.dt.year):
                for label,p in [('full_best',full),('early_best',early),('original',dict(method='constant',fraction=1))]:
                    r,_=candidate_returns(t,p)
                    yearly.append(dict(year=int(year),policy=label,**stat(r)))
            choice['yearly']=yearly
        selections.append(choice);rows.extend(group_rows)
    pd.DataFrame(rows).to_csv(output/'search.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    return dict(candidate_count=len(policies),search_rows=len(rows),selections=selections,
        target_count=len(targets),control_count=len(controls),
        baseline_sum_net_r=float(targets.net_r.sum()),
        crossing_targets=int((~period_mask(targets,'early',cut)&~period_mask(targets,'later',cut)).sum()))


def run(output,workers,config=CONFIG):
    assert not output.exists(),'refusing to overwrite results'
    for path in [Path(__file__),config]:
        subprocess.run(['git','ls-files','--error-unmatch',str(path)],check=True,stdout=subprocess.DEVNULL)
        subprocess.run(['git','diff','--exit-code','HEAD','--',str(path)],check=True)
    cfg=json.loads(config.read_text())
    assert cfg['round_trip_cost']==.002
    targets=pd.read_csv(EXCURSIONS)
    _,pairs,audit,sources=load(json.loads(SUMMARY_CONFIG.read_text()))
    c15=pd.read_csv(Path(json.loads(SUMMARY_CONFIG.read_text())['source_15m'])/'matched_controls.csv.gz')
    c15=c15[c15.trade_key.isin(pairs.trade_key)].copy();c15['expected_r']=c15.net_r
    c60=pairs[pairs.timeframe_min.eq(60)][['trade_key','symbol','timeframe_min','side','control_net_r']].rename(columns={'control_net_r':'expected_r'})
    extra=pd.read_csv(EXTRA/'controls.csv.gz');extra=extra[extra.eligible.eq(True)].copy();extra['expected_r']=extra.control_net_r
    controls=pd.concat([c15,c60,extra],ignore_index=True)
    assert len(controls)==3131 and controls.trade_key.isin(targets.trade_key).all()
    manifest=json.loads(MANIFEST.read_text());by_symbol={r['symbol']:r['path'] for r in manifest['streams']}
    meta=json.loads(Path('data/kline_preholdout_binance_um5m/exchange_info.json').read_text())
    ticks={r['symbol']:float(next(f['tickSize'] for f in r['filters'] if f['filterType']=='PRICE_FILTER')) for r in meta['symbols']}
    tasks=[(s,by_symbol[s],g,controls[controls.symbol.eq(s)],ticks[s],cfg['depths_r']) for s,g in targets.groupby('symbol')]
    rows=[];receipts=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(worker,t) for t in tasks]
        for i,f in enumerate(as_completed(futures),1):
            r,p=f.result();rows.extend(r);receipts.append(p)
            if i%50==0 or i==len(tasks):print(json.dumps(dict(done=i,total=len(tasks),rows=len(rows))),flush=True)
    frame=pd.DataFrame(rows).sort_values(['cohort','timeframe_min','symbol','entry_time'])
    output.mkdir(parents=True)
    frame.to_csv(output/'leg_paths.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    print('All baseline paths reconciled; aggregating grid.',flush=True)
    result=aggregate(frame,cfg,output)
    result.update(generated_at=datetime.now(timezone.utc).isoformat(),config=cfg,
        builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        audit=audit|dict(all_target_and_control_baselines_match=True),price_inputs=receipts,
        sources=[provenance(p) for p in sources+[EXCURSIONS,EXTRA/'controls.csv.gz',MANIFEST]],
        limitations=['Frozen opportunity/exit paths, including original 1H occupancy; no new admission after zero entry or expiry.',
          'Arithmetic net R, not account equity; simultaneous cross-symbol positions and capital constraints are not simulated.',
          'Historical universe and tick metadata, 20bp costs, no funding, queue, order latency or capacity simulation.',
          'OHLC penetration is a fill proxy; atomic exit/cancel priority and immediate original-entry-open placement are assumed.',
          'Chronological diagnostic is not unseen data: earlier research already inspected this history.',
          'Full-history grid maximum and fixed-selected confidence intervals do not adjust for model/parameter selection.',
          'No trained classifier or ranking score: AUC and top-decile tests are inapplicable; use same-policy matched random controls.'],
        training_eligible=False,production_eligible=False)
    (output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps(dict(output=str(output),candidates=result['candidate_count'],baseline=result['baseline_sum_net_r'],best=result['selections'][0]['full_best']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=EXP/'results_v1');parser.add_argument('--workers',type=int,default=4);parser.add_argument('--config',type=Path,default=CONFIG)
    args=parser.parse_args();run(args.output,args.workers,args.config)
