"""One-variable v3 entry study authorized by owner on 2026-10-30.

Freeze pre-touch causal scores, existing 0.5/0.6 target bands, P30/BE17 and
the original touch-derived H1 clock. Compare cached resting fills with entry
at the first touch candle close. Native resting outcomes identify an episode
already completed within that candle; never reopen it at its close.
Reuse original same-symbol/week/side/prior-14bar-TR matched random orders,
including unfilled controls at zero. Only new trades run in Nautilus, once per
declared OHLC/OLHC path. No handwritten execution engine or account overlay.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp, runtime_versions
from yoyo.evaluation.monday_limit_backstop import CODEX, stops
from yoyo.evaluation.allin3_close_hybrid import CACHED, KEEP
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
from yoyo.evaluation.spike_close_execution import stats, paired, week_sign_flip

EXP = ROOT/'experiments/active/exp-v3-causal-close-entry-20261030-v1'
VARIANTS = ('rest', 'close')


def eligible(cfg):
    """Read frozen pre-touch scores; the stored touch-close score is unused."""
    s = pd.read_csv(ROOT/cfg['scores']).set_index('event')
    return s[(s.causal >= cfg['score_cut']) & ~s.index.isin(BAD_EVENTS)].causal


def select_orders(original, scores, cfg):
    e = original[original.reclaim_minutes == 60].copy()
    e['score'] = e.event_id.map(scores)
    modes = np.where(e.score >= cfg['far_score'], 'opposite_1618', 'range_0.618')
    return e[(e.score >= cfg['score_cut']) & (e.exit_mode == modes)].copy()


def close_order(source, baseline, p, tick):
    """Read only the touch candle close and native events through that close.

    The baseline row is used only for its first fill and whether that episode
    terminated at the fill candle. Later baseline exits never affect admission.
    """
    if baseline is None:
        return None, 'no_touch_before_expiry'
    k = int(baseline['entry_i'])
    if int(baseline['exit_i']) == k:
        return None, 'episode_completed_in_touch_bar'
    if k >= source['expire_i'] or k >= source['end_i']:
        return None, 'no_remaining_interval'
    c, side = float(p.close.iloc[k]), source['side']
    if np.isfinite(source['target_price']) and side*(source['target_price']-c) < tick*(1-1e-7):
        return None, 'close_at_or_past_target'
    if side*(c-source['stop']) <= 0:
        return None, 'close_at_or_past_backstop'
    r = dict(source, entry_i=k, entry=c, risk=c, order_mode='market')
    return r, 'admitted'


def checked_cached(cached, key, order, tick, expected_fill):
    if key not in cached.index:
        return None
    row = cached.loc[key].to_dict()
    if isinstance(cached.loc[key], pd.DataFrame):
        raise ValueError('Duplicate cached trade identity')
    for field in ('entry', 'stop', 'target_price'):
        np.testing.assert_allclose(row[field], order[field], atol=tick*1e-6, rtol=1e-12)
    assert int(row['entry_i']) == int(expected_fill)
    row['trade_id'] = order['trade_id']
    return row


def execute(frame, orders, path, tick, symbol, cfg):
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    if orders.empty:
        return pd.DataFrame(columns=KEEP), pd.DataFrame(), dict(positions=0, fills=0, opportunities=[])
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0,
        target_from_entry=True, intrabar_path=path, reclaim_next_bar=True,
        breakeven_includes_cost=False, fill_maker_fee=cfg['maker_fee'],
        fill_taker_fee=cfg['taker_fee'], monotone_segments=True, reclaim_backstop=True)
    return run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)), orders,
        policy, tick=tick, symbol=symbol.removesuffix('USDT'), entry_order_mode='limit',
        execution_minutes=5, batch_bars=288, skip_idle_bars=True)


def process(symbol, config):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    cfg = json.loads(Path(config).read_text()); out = Path(config).parent/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    if (out/'done.json').exists():
        return json.loads((out/'done.json').read_text())
    started = time.perf_counter(); origin = CODEX/'symbols'/symbol
    job = json.loads((origin/'identity.json').read_text())['job']
    assert job['group'] == 'crypto_5m'
    f, _ = load_job(job); prior = json.loads((origin/'done.json').read_text())
    assert (len(f), str(f.index[0]), str(f.index[-1])) == (prior['rows'], prior['first'], prior['last'])
    decimals = max(2, min(9, int(np.ceil(-np.log10(f[['open','high','low','close']].to_numpy().min())))+7))
    tick = 10.**(-decimals); p = f[['open','high','low','close']].round(decimals)
    p.index = p.index.as_unit('ns'); frame = p.copy(); frame.index += pd.Timedelta(minutes=5)
    e = select_orders(pd.read_csv(origin/'orders.csv.gz'), eligible(cfg), cfg)
    old_fills = pd.read_csv(origin/'trades.csv.gz', usecols=['trade_id','entry_i']).set_index('trade_id')
    e['original_id'] = e.trade_id
    e['native_touch_i'] = e.original_id.map(old_fills.entry_i)
    e['reclaim_ref'] = e.entry
    e['stop'] = stops(e, 'P', cfg['backstop_pct'], tick)
    e['be_offset_bp'] = cfg['be_offset_bp']
    e['order_mode'] = 'limit'; e['risk'] = e.entry
    e['event'] = e.event_id
    e['cohort_time'] = (p.index[e.touch_i_hint.where(e.control_id == 0, 0).astype(int)]
                        + pd.Timedelta(minutes=5)).astype(str)
    event_time = e[e.control_id == 0].set_index('event').cohort_time
    e['cohort_time'] = e.event.map(event_time)
    assert e[e.control_id == 0].native_touch_i.notna().all()
    e.to_csv(out/'sources.csv.gz', index=False)
    receipts = {}
    for path in ('OHLC','OLHC'):
        dest = out/path.lower(); dest.mkdir(exist_ok=True)
        if (dest/'audit.json').exists():
            receipts[path] = json.loads((dest/'audit.json').read_text()); continue
        cached = pd.read_csv(CACHED/path.lower()/'symbols'/symbol/'trades.csv.gz').set_index('trade_id')
        rest = e.copy(); rest['trade_id'] = rest.original_id+':entry_rest'
        rest['variant'] = 'rest'
        reused = []
        for r in rest[rest.control_id == 0].to_dict('records'):
            row = checked_cached(cached, r['original_id']+':v_rest_v3', r, tick, r['native_touch_i'])
            if row is None:
                raise ValueError('Missing exact original resting v3 baseline')
            reused.append(row)
        new_rest = rest[(rest.control_id > 0) & rest.native_touch_i.notna()]
        # Cache each native stage independently; a summary failure never replays it.
        def stage(name, orders):
            if (dest/(name+'_audit.json')).exists():
                rows = pd.read_csv(dest/(name+'_trades.csv.gz'))
                audit = json.loads((dest/(name+'_audit.json')).read_text())
                return rows, audit
            orders.to_csv(dest/(name+'_orders.csv.gz'), index=False)
            rows, fills, audit = execute(frame, orders, path, tick, symbol, cfg)
            rows.reindex(columns=KEEP+['reclaim_due_ns']).to_csv(dest/(name+'_trades.csv.gz'), index=False)
            fills.to_csv(dest/(name+'_fills.csv.gz'), index=False)
            pd.DataFrame(audit.get('opportunities',[])).to_csv(dest/(name+'_opportunities.csv.gz'), index=False)
            receipt = dict(orders=len(orders),positions=audit['positions'],fills=audit['fills'])
            dump(dest/(name+'_audit.json'),receipt)
            return rows, receipt
        rows_r, ar = stage('new_rest',new_rest)
        all_rest = pd.concat([rows_r.reindex(columns=KEEP),pd.DataFrame(reused).reindex(columns=KEEP)],ignore_index=True)
        baseline = all_rest.set_index('trade_id')
        # The old first-fill clock is independent of the exits being studied.
        for r in new_rest.itertuples():
            assert r.trade_id in baseline.index
            assert int(baseline.loc[r.trade_id,'entry_i']) == int(r.native_touch_i)
        decisions, close_orders, reused_c = [], [], []
        for r in rest.to_dict('records'):
            old = baseline.loc[r['trade_id']].to_dict() if r['trade_id'] in baseline.index else None
            rr, status = close_order(r,old,p,tick)
            k = int(r['native_touch_i']) if pd.notna(r['native_touch_i']) else None
            common = {key:r[key] for key in ('event','symbol','week','side','score','control_id','cohort_time','exit_mode')}
            common.update(original_id=r['original_id'], native_touch_i=k,
                level=r['reclaim_ref'],target=r['target_price'],backstop=r['stop'],
                close=float(p.close.iloc[k]) if k is not None else None,
                close_position=('inside' if r['side']*(p.close.iloc[k]-r['reclaim_ref']) >= tick*(1-1e-7) else 'outside') if k is not None else 'no_touch')
            decisions.append(dict(common,trade_id=r['trade_id'],variant='rest',status='admitted' if old is not None else 'no_touch_before_expiry'))
            tid=r['original_id']+':entry_close'
            decisions.append(dict(common,trade_id=tid,variant='close',status=status))
            if rr is None:
                continue
            rr.update(trade_id=tid,variant='close',entry_time=str(frame.index[int(rr['entry_i'])]))
            close_orders.append(rr)
            if r['control_id'] == 0:
                fam = 'ci' if common['close_position']=='inside' else 'cb'
                old_c=checked_cached(cached,r['original_id']+f':v_{fam}_v3',rr,tick,k)
                if old_c is not None:
                    reused_c.append(old_c)
        close_df=pd.DataFrame(close_orders,columns=[*rest.columns,'entry_time'])
        cached_ids={r['trade_id'] for r in reused_c}
        new_close=close_df[~close_df.trade_id.isin(cached_ids)]
        rows_c,ac=stage('new_close',new_close)
        all_orders=pd.concat([rest,close_df],ignore_index=True)
        all_orders.to_csv(dest/'orders.csv.gz',index=False)
        d=pd.DataFrame(decisions);d.to_csv(dest/'decisions.csv.gz',index=False)
        ledger=pd.concat([all_rest,rows_c.reindex(columns=KEEP),pd.DataFrame(reused_c).reindex(columns=KEEP)],ignore_index=True)
        ledger=ledger.merge(all_orders[['trade_id','variant','order_mode']],on='trade_id',validate='one_to_one')
        ledger['net']=ledger.net_notional_bp-np.where(ledger.order_mode=='market',cfg['slippage_bp']['market_entry'],0.)-np.where(ledger.reason==4,0.,cfg['slippage_bp']['non_target_exit'])
        ledger.to_csv(dest/'trades.csv.gz',index=False)
        receipt=dict(new_rest=ar,new_close=ac,reused_rest=len(reused),reused_close=len(reused_c),positions=len(ledger))
        dump(dest/'audit.json',receipt);receipts[path]=receipt
    result=dict(symbol=symbol,events=int((e.control_id==0).sum()),controls=int((e.control_id>0).sum()),
        paths=receipts,sources=[file_stamp(v) for v in job['paths']],seconds=time.perf_counter()-started)
    dump(out/'done.json',result)
    return result


def run(config,workers):
    from yoyo.evaluation.monday_limit_paper_accounts import committed
    committed('yoyo/evaluation/v3_causal_close_entry.py','yoyo/layers/l3_backtest/nautilus_independent.py',str(config.relative_to(ROOT)))
    cfg=json.loads(config.read_text()); syms=sorted(set(eligible(cfg).index.str.split(':').str[0]))
    todo=[s for s in syms if not (config.parent/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as pool:
        jobs={pool.submit(process,s,str(config)):s for s in todo}
        for i,fu in enumerate(as_completed(jobs),1):
            try:
                result=fu.result()
                err=config.parent/'symbols'/jobs[fu]/'error.json'
                if err.exists(): err.unlink()
            except Exception as error:
                result=dict(symbol=jobs[fu],error=repr(error))
                dump(config.parent/'symbols'/jobs[fu]/'error.json',result)
            print(json.dumps(dict(progress=i,total=len(todo),**result)),flush=True)


def summarize(config):
    cfg=json.loads(config.read_text());root=config.parent
    selected=eligible(cfg);syms=sorted(set(selected.index.str.split(':').str[0]))
    paths={p:[] for p in ('ohlc','olhc')};receipts=[]
    for symbol in syms:
        d=root/'symbols'/symbol
        if (d/'error.json').exists() or not (d/'done.json').exists(): raise RuntimeError('Incomplete: '+symbol)
        receipts.append(json.loads((d/'done.json').read_text()))
        for path in paths:
            decisions=pd.read_csv(d/path/'decisions.csv.gz')
            trades=pd.read_csv(d/path/'trades.csv.gz').drop(columns=['variant'])
            z=decisions.merge(trades,on='trade_id',how='left',validate='one_to_one',suffixes=('','_fill'))
            z['filled']=z.net.notna();z['net']=z.net.fillna(0.)
            paths[path].append(z)
    paths={p:pd.concat(v,ignore_index=True).set_index('trade_id') for p,v in paths.items()}
    a,b=paths['ohlc'],paths['olhc'];b=b.reindex(a.index)
    assert b.variant.notna().all()
    take=(b.net<a.net)|((b.net==a.net)&b.filled&~a.filled)
    w=a.copy();w.loc[take]=b.loc[take];w['chosen_path']=np.where(take,'olhc','ohlc')
    for name,x in [*paths.items(),('worse_opportunity',w)]:x.to_csv(root/(name+'.csv.gz'))
    periods={'all':lambda x:np.ones(len(x),bool),'2020_2024':lambda x:x.cohort_time<'2025-01-01',
        '2025':lambda x:(x.cohort_time>='2025-01-01')&(x.cohort_time<'2026-01-01'),
        '2026':lambda x:x.cohort_time>='2026-01-01'}
    rng=np.random.default_rng(cfg['seed']);result=dict(symbols=len(syms),events=len(selected),variants={},controls={},score_bands={},paired_entry_difference={},checks={},concentration={})
    for v in VARIANTS:
        x=w[w.variant==v];s=x[x.control_id==0]
        result['variants'][v]={per:stats(s[mask(s)]) for per,mask in periods.items()}
        result['controls'][v]={per:paired(x[mask(x)],rng) for per,mask in periods.items()}
        result['score_bands'][v]={band:{per:dict(signal=stats(g[(g.control_id==0)&mask(g)]),matched_random=paired(g[mask(g)],rng)) for per,mask in periods.items()}
            for band,g in x.groupby(np.where(x.score>=cfg['far_score'],'>=0.6','0.5-0.6'))}
        result['concentration'][v]={}
        for per,mask in periods.items():
            f=s[mask(s)&s.filled];weeks=f.groupby('week').net.sum().sort_values(ascending=False)
            result['concentration'][v][per]=dict(best_week=str(weeks.index[0]) if len(weeks) else None,
                mean_without_best_week_bp=float(f.loc[f.week!=weeks.index[0],'net'].mean()) if len(weeks)>1 else None,
                mean_without_top3_bp=float(f.net.nsmallest(max(0,len(f)-3)).mean()))
    signal=w[w.control_id==0]
    for per,mask in periods.items():
        q=signal[mask(signal)].pivot(index='event',columns='variant',values='net')
        diff=q['close']-q['rest'];weeks=diff.groupby(diff.index.str.split(':').str[1]).agg(['sum','size'])
        samples=rng.integers(0,len(weeks),size=(3000,len(weeks)))
        boot=weeks['sum'].to_numpy()[samples].sum(axis=1)/weeks['size'].to_numpy()[samples].sum(axis=1)
        result['paired_entry_difference'][per]=dict(events=len(diff),mean_close_minus_rest_bp=float(diff.mean()),
            week_p=week_sign_flip(weeks['sum'].to_numpy(),len(diff),rng),ci95_bp=np.quantile(boot,[.025,.975]).tolist())
    result['checks']=dict(statuses={v:signal[signal.variant==v].status.value_counts().to_dict() for v in VARIANTS},
        paths={p:{v:stats(z[(z.variant==v)&(z.control_id==0)]) for v in VARIANTS} for p,z in paths.items()},
        first_cohort=signal.cohort_time.min(),last_cohort=signal.cohort_time.max(),
        expected_controls=len(selected)*3,actual_controls=int(((w.variant=='rest')&(w.control_id>0)).sum()))
    result['native_counts']={key:sum(r['paths'][p][key] for r in receipts for p in ('OHLC','OLHC')) for key in ('reused_rest','reused_close')}
    for key in ('orders','positions','fills'):
        result['native_counts']['new_'+key]=sum(r['paths'][p][stage][key] for r in receipts for p in ('OHLC','OLHC') for stage in ('new_rest','new_close'))
    dump(root/'summary.json',clean(dict(config=cfg,results=result,runtime=runtime_versions(),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),training_eligible=False,production_eligible=False)))
    print(json.dumps(clean(result)),flush=True)


def validate(config):
    cfg=json.loads(config.read_text());root=config.parent;count=0;max_error=0.;clock_checks=0
    for symbol in sorted(set(eligible(cfg).index.str.split(':').str[0])):
        for path in ('ohlc','olhc'):
            d=root/'symbols'/symbol/path
            orders=pd.read_csv(d/'orders.csv.gz').set_index('trade_id')
            for stage in ('new_rest','new_close'):
                audit=json.loads((d/(stage+'_audit.json')).read_text())
                if not audit['fills']:continue
                fills=pd.read_csv(d/(stage+'_fills.csv.gz'));rows=pd.read_csv(d/(stage+'_trades.csv.gz')).set_index('trade_id')
                assert len(fills)==audit['fills'] and len(rows)==audit['positions']
                for tid,g in fills.groupby('trade_id'):
                    row,order=rows.loc[tid],orders.loc[tid]
                    en=g[g.phase=='entry'];ex=g[g.phase!='entry'];assert len(en)==len(ex)==1
                    en,ex=en.iloc[0],ex.iloc[0];assert en.quantity==ex.quantity
                    assert en.bar_i==row.entry_i and ex.bar_i==row.exit_i
                    assert row.entry_i==order.native_touch_i
                    if order.order_mode=='market':assert row.entry_i==order.entry_i
                    else:assert order.entry_i<row.entry_i<=order.expire_i
                    for fill in (en,ex):
                        fee=cfg['maker_fee'] if fill.phase=='target' or (fill.phase=='entry' and order.order_mode=='limit') else cfg['taker_fee']
                        np.testing.assert_allclose(fill.commission,fill.price*fill.quantity*fee,atol=1e-8,rtol=1e-7)
                    expected=row.side*(ex.price/en.price-1)*1e4-g.commission.sum()/(en.price*en.quantity)*1e4
                    max_error=max(max_error,abs(expected-row.net_notional_bp));np.testing.assert_allclose(expected,row.net_notional_bp,atol=1e-6)
                    due=(pd.Timestamp(row.entry_time)-pd.Timedelta(minutes=5)).floor('60min')+pd.Timedelta(minutes=120)
                    assert int(row.reclaim_due_ns)==due.value;clock_checks+=1;count+=1
    a,b,w=[pd.read_csv(root/(p+'.csv.gz')).set_index('trade_id') for p in ('ohlc','olhc','worse_opportunity')]
    np.testing.assert_allclose(w.net,np.minimum(a.net,b.net));assert (w.loc[~w.filled,'net']==0).all()
    assert not w.loc[w.status!='admitted','filled'].any()
    result=dict(new_native_positions_checked=count,fee_net_max_error_bp=max_error,H1_clock_checks=clock_checks,
        opportunity_rows=len(w),no_content_hashing=True,no_download=True,no_account_projection=True)
    dump(root/'validation.json',result);print(json.dumps(result),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['run','summary','validate'])
    ap.add_argument('--config',type=Path,default=EXP/'config.json');ap.add_argument('--workers',type=int,default=4)
    args=ap.parse_args();config=args.config.resolve()
    if args.action=='run':run(config,args.workers)
    elif args.action=='summary':summarize(config)
    else:validate(config)
