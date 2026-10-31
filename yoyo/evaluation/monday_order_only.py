"""Replace only primary score admission with completed pre-touch EMA order>=3.

Owner 2026-10-31 keeps all eight 20261028 paper strategies' score-based targets,
sizing and add eligibility. Features are frozen l2_order_score from completed
15m bars before the touch and causal score from the preceding completed5m bar.
No feature is recomputed, no model fitted, no candle downloaded. Exact native
signal caches are reused; missing orders and original matched controls execute
once per declared path in Nautilus. Existing capital allocators consume fills.
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

from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.monday_limit_backstop import CODEX, stops
from yoyo.evaluation.monday_limit_be_scalein_compare import BAD_EVENTS, clean
from yoyo.evaluation.monday_limit_paper_accounts import committed
from yoyo.evaluation.v3_causal_close_entry import read_csv, execute
from yoyo.evaluation.spike_close_execution import paired

A = ROOT/'experiments/active'
EXP = A/'exp-monday-order-only-20261031-v1'
TRACKS = ('v2_60', 'v2_5', 'v3', 'v3add', 'v3add20', 'v3lock', 'spike', 'allin3')
KEEP = ['trade_id','side','entry','stop','target_price','exit_price','reason',
        'entry_time','exit_time','entry_i','exit_i','net_notional_bp',
        'framework_commission','quantity','gross_r','net_r','reclaim_due_ns']


def population(cfg):
    """Frozen completed-bar EMA feature and pre-touch score, keyed by event."""
    f = read_csv(ROOT/cfg['features'], usecols=['event','l2_order_score']).set_index('event')
    s = read_csv(ROOT/cfg['scores']).set_index('event').causal.rename('score')
    x = f.join(s)
    return x[~x.index.isin(BAD_EVENTS)]


def construct(original, pop, close, tick, cfg):
    """Change primary admission only; retain target, add and stop semantics."""
    e = original[original.event_id.isin(pop.index) & original.reclaim_minutes.isin([5,60])].copy()
    e['event'] = e.event_id
    e['score'] = e.event.map(pop.score)
    e['order_score'] = e.event.map(pop.l2_order_score)
    e['original_id'] = e.trade_id
    near = e.exit_mode.eq('near_monday_edge')
    m60 = e.reclaim_minutes.eq(60)
    v3mode = np.where(e.score >= cfg['far_score'], 'opposite_1618', 'range_0.618')
    v3 = m60 & e.score.notna() & e.exit_mode.eq(v3mode)
    opp = m60 & e.exit_mode.eq('opposite_1618')
    parts = []
    for rule, mask, kind, value, offset, cached in [
        ('v2',near,'far',0,cfg['breakeven_bp'],'v2'),
        ('p30',v3|opp,'P',30,cfg['breakeven_bp'],'v3'),
        ('lock',v3,'P',30,cfg['lock_bp'],'v3lock'),
        ('spike',m60&near,'P',18,cfg['breakeven_bp'],'spike'),
    ]:
        x = e[mask].copy()
        x['rule'] = rule
        x['stop'] = stops(x,kind,value,tick)
        x['reclaim_ref'] = x.entry
        x['be_offset_bp'] = offset
        x['risk'] = x.entry
        x['order_mode'] = 'limit'
        x['parent'] = None
        x['trade_id'] = x.original_id+':order_'+rule
        x['cached_id'] = x.original_id+':v_rest_'+cached
        parts.append(x)
        if rule == 'p30':
            par = x[x.exit_mode.eq('opposite_1618') & x.score.ge(cfg['far_score'])]
            leg = par.copy()
            raw = leg.entry*(1-leg.side*cfg['add_pct']/100)
            leg['entry'] = np.where(leg.side>0,np.floor(raw/tick+1e-7)*tick,np.ceil(raw/tick-1e-7)*tick)
            leg['risk'] = leg.entry
            leg['parent'] = par.trade_id
            leg['trade_id'] = par.trade_id+'_add'
            leg['cached_id'] = par.cached_id+'_add'
            leg['rule'] = 'add'
            px = close[leg.entry_i.astype(int).to_numpy()]
            leg = leg[(leg.side*(leg.entry-leg.stop)>0) & (leg.side*(px-leg.entry)>=tick*(1-1e-7))]
            parts.append(leg)
    return pd.concat(parts,ignore_index=True)


def cache_match(row, order, tick):
    return all(np.isclose(row[k],order[k],rtol=1e-12,atol=tick*1e-6,equal_nan=True)
               for k in ('entry','stop','target_price','side'))


def baseline_projection(paper, symbol, f):
    """Original loader's order, worse path and MAE, without a second data read."""
    tabs = {}
    for path in ('ohlc','olhc'):
        d = paper/path/'symbols'/symbol
        if not d.exists():
            return pd.DataFrame(), {}
        assert (d/'done.json').exists() and not (d/'error.json').exists()
        x = read_csv(d/'trades.csv.gz')
        x = x[x.variant.str.startswith('rest')].copy()
        x['net'] = x.net_notional_bp-np.where(x.reason.eq(4),0.,10.)
        tabs[path] = x.set_index('trade_id')
    o = tabs['ohlc']; l = tabs['olhc'].reindex(o.index)
    take = l.net.lt(o.net)&l.net.notna()
    x = o.copy(); x.loc[take] = l.loc[take]
    # Original summary preserves OHLC entry fields even when OLHC exit is worse.
    for col in ('entry_i','entry','side','variant'):
        x[col] = o[col]
    x['entry_time'] = (f.index[x.entry_i.astype(int)]+pd.Timedelta(minutes=5)).astype(str)
    x['mae_pct'] = adverse(f,x)
    x = x.reset_index()
    p = x.trade_id.str.split(':')
    x['event'] = p.str[:3].str.join(':')
    x['mode'] = p.str[4]
    x['minutes'] = p.str[3]
    x['control_id'] = 0
    return x[~x.event.isin(BAD_EVENTS)], tabs


def adverse(f, rows):
    """Post-fill MAE used by the unchanged historical account proxy, not admission."""
    h, lo = f.high.to_numpy(), f.low.to_numpy()
    out = []
    for r in rows.itertuples():
        a,b = int(r.entry_i),int(r.exit_i)
        worst = lo[a:max(a+1,b)].min() if r.side==1 else h[a:max(a+1,b)].max()
        out.append(max(0.,-r.side*(worst-r.entry)/r.entry*100))
    return out


def process(symbol, config):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    cfg = json.loads(Path(config).read_text()); root = Path(config).parent
    out = root/'symbols'/symbol; out.mkdir(parents=True,exist_ok=True)
    if (out/'done.json').exists():
        return json.loads((out/'done.json').read_text())
    started = time.perf_counter(); origin = CODEX/'symbols'/symbol
    job = json.loads((origin/'identity.json').read_text())['job']
    assert job['group']=='crypto_5m'
    f,_ = load_job(job); prior = json.loads((origin/'done.json').read_text())
    assert (len(f),str(f.index[0]),str(f.index[-1]))==(prior['rows'],prior['first'],prior['last'])
    tick = json.loads((origin/'audit.json').read_text())['protection_tick']
    p = f[['open','high','low','close']].round(int(round(-np.log10(tick))))
    p.index = p.index.as_unit('ns'); frame = p.copy(); frame.index += pd.Timedelta(minutes=5)
    base,tabs = baseline_projection(ROOT/cfg['baseline'],symbol,f)
    base.to_csv(out/'baseline.csv.gz',index=False)
    pop = population(cfg); selected = pop[pop.index.str.startswith(symbol+':') & pop.l2_order_score.ge(cfg['min_order'])]
    if selected.empty:
        result = dict(symbol=symbol,events=0,baseline=len(base),paths={},seconds=time.perf_counter()-started)
        dump(out/'done.json',result);return result
    original = read_csv(origin/'orders.csv.gz')
    x = construct(original,selected,p.close.to_numpy(),tick,cfg)
    fills = read_csv(origin/'trades.csv.gz',usecols=['trade_id','entry_i']).set_index('trade_id')
    x['native_touch_i'] = x.original_id.map(fills.entry_i)
    core = x[x.control_id.eq(0)&x.rule.eq('spike')].set_index('event')
    assert set(core.index)==set(selected.index)
    x['cohort_time'] = x.event.map(pd.Series((p.index[core.native_touch_i.astype(int)]+pd.Timedelta(minutes=5)).astype(str),index=core.index))
    x.to_csv(out/'orders.csv.gz',index=False)
    receipts = {}
    for path in ('ohlc','olhc'):
        d = out/path;d.mkdir(exist_ok=True)
        if (d/'done.json').exists():
            receipts[path]=json.loads((d/'done.json').read_text());continue
        cache = tabs.get(path,pd.DataFrame())
        reused, reused_ids, known_empty, mismatches = [],set(),set(),[]
        for r in x[x.control_id.eq(0)].to_dict('records'):
            key=r['cached_id']
            if key in cache.index:
                row=cache.loc[key].to_dict()
                if not cache_match(row,r,tick):
                    mismatches.append(dict(trade_id=r['trade_id'],cached_id=key));continue
                if r['rule']!='add':assert int(row['entry_i'])==int(r['native_touch_i'])
                row.update(trade_id=r['trade_id'],cache_source=str(ROOT/cfg['baseline']/path/'symbols'/symbol/'trades.csv.gz'))
                reused.append(row);reused_ids.add(r['trade_id'])
            elif r['rule']=='add' and r['cached_id'].removesuffix('_add') in cache.index:
                # The original high-score parent generated the same add order;
                # complete native cache with no child fill means no entry.
                par=cache.loc[r['cached_id'].removesuffix('_add')]
                expected=x.set_index('trade_id').loc[r['parent']]
                if cache_match(par,expected,tick):known_empty.add(r['trade_id'])
        control_path=A/'exp-v3-causal-close-entry-20261030-v1'/'symbols'/symbol/path/'trades.csv.gz'
        if control_path.exists():
            controls=read_csv(control_path).set_index('trade_id')
            for r in x[x.control_id.gt(0)&x.rule.eq('p30')].to_dict('records'):
                key=r['original_id']+':entry_rest'
                if key not in controls.index:continue
                row=controls.loc[key].to_dict()
                if not cache_match(row,r,tick):continue
                assert int(row['entry_i'])==int(r['native_touch_i'])
                row.update(trade_id=r['trade_id'],cache_source=str(control_path))
                reused.append(row);reused_ids.add(r['trade_id'])
        runtime=x[~x.trade_id.isin(reused_ids|known_empty)&x.native_touch_i.notna()].copy()
        dependencies=set(runtime.parent.dropna())
        deps=x[x.trade_id.isin(dependencies-set(runtime.trade_id))]
        runtime=pd.concat([runtime,deps],ignore_index=True)
        reused=[r for r in reused if r['trade_id'] not in dependencies]
        if (d/'native-audit.json').exists():
            # A completed stage remains authoritative after discovering extra
            # external caches. Never overwrite its submitted-order receipt.
            old_runtime=read_csv(d/'native-orders.csv.gz')
            assert set(runtime.trade_id)<=set(old_runtime.trade_id)
            current=x.set_index('trade_id')
            for r in old_runtime.to_dict('records'):
                assert cache_match(r,current.loc[r['trade_id']],tick)
            runtime=old_runtime
            reused=[r for r in reused if r['trade_id'] not in set(runtime.trade_id)]
            rows=read_csv(d/'native-trades.csv.gz');audit=json.loads((d/'native-audit.json').read_text())
        else:
            runtime.to_csv(d/'native-orders.csv.gz',index=False)
            rows,native_fills,audit=execute(frame,runtime,path.upper(),tick,symbol,cfg)
            rows.to_csv(d/'native-trades.csv.gz',index=False)
            native_fills.to_csv(d/'native-fills.csv.gz',index=False)
            dump(d/'native-audit.json',audit)
        rows=pd.concat([pd.DataFrame(reused),rows],ignore_index=True)
        assert rows.trade_id.is_unique
        rows['entry_time']=(p.index[rows.entry_i.astype(int)]+pd.Timedelta(minutes=5)).astype(str)
        rows['net']=rows.net_notional_bp-np.where(rows.reason.eq(4),0.,cfg['non_target_slippage_bp'])
        rows['mae_pct']=adverse(p,rows)
        rows.reindex(columns=KEEP+['net','mae_pct','cache_source']).to_csv(d/'trades.csv.gz',index=False)
        receipt=dict(reused=len(reused),known_empty_children=len(known_empty),cache_mismatches=mismatches,
                     new_orders=len(runtime),new_positions=audit['positions'],new_fills=audit['fills'],
                     dependent_parents=len(deps),positions=len(rows),engine=audit.get('engine'),version=audit.get('version'))
        dump(d/'done.json',receipt);receipts[path]=receipt
    result=dict(symbol=symbol,events=len(selected),orders=len(x),baseline=len(base),paths=receipts,
                sources=[file_stamp(v) for v in job['paths']],seconds=time.perf_counter()-started)
    dump(out/'done.json',result);return result


def symbols(cfg):
    pop=population(cfg);pop=pop[pop.l2_order_score.ge(cfg['min_order'])]
    prior=ROOT/cfg['baseline']/'ohlc/symbols'
    return sorted(set(pop.index.str.split(':').str[0])|{d.name for d in prior.iterdir() if (d/'done.json').exists()})


def run(config,workers):
    committed('yoyo/evaluation/monday_order_only.py',str(config.relative_to(ROOT)))
    cfg=json.loads(config.read_text())
    todo=[s for s in symbols(cfg) if not (config.parent/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as pool:
        jobs={pool.submit(process,s,str(config)):s for s in todo}
        for i,fu in enumerate(as_completed(jobs),1):
            try:
                result=fu.result();err=config.parent/'symbols'/jobs[fu]/'error.json'
                if err.exists():err.unlink()
            except Exception as error:
                result=dict(symbol=jobs[fu],error=repr(error))
                dump(config.parent/'symbols'/jobs[fu]/'error.json',result)
            print(json.dumps(dict(progress=i,total=len(todo),**result)),flush=True)


def select_track(t,track,cfg,baseline=False):
    """Primary filter changes; score target and child gate stay frozen."""
    sc=t.score
    modes=np.where(sc>=cfg['far_score'],'opposite_1618','range_0.618')
    if baseline:
        rules=t.variant.map({'rest_v2':'v2','rest_v3':'p30','rest_v3_add':'add','rest_v3lock':'lock','rest_spike':'spike'})
    else:rules=t.rule
    if track in ('v2_60','v2_5'):
        mask=rules.eq('v2')&t.minutes.eq('m60' if track=='v2_60' else 'm5')
        if baseline:mask&=sc.ge(cfg['v2_threshold'])
    elif track=='spike':
        mask=rules.eq('spike')
        if baseline:mask&=sc.ge(cfg['far_score'])&t.order_score.ge(cfg['min_order'])
    elif track=='allin3':
        mask=rules.eq('p30')&t['mode'].eq('opposite_1618')
        if baseline:mask&=sc.ge(cfg['far_score'])
    else:
        mask=rules.eq('lock' if track=='v3lock' else 'p30')&sc.notna()&t['mode'].eq(modes)
        if baseline:mask&=sc.ge(cfg['v3_cut'])
        if track in ('v3add','v3add20'):
            # The original replay concatenates all parents before all children.
            # Source order affects the seeded same-time allocation priority.
            return pd.concat([t[mask],t[rules.eq('add')&sc.ge(cfg['far_score'])]]).copy()
    return t[mask].copy()


def accounts(track,t,cfg,start):
    """Original seed ensembles; emit funded metrics without generating trades."""
    from yoyo.evaluation.monday_limit_reclaim_optimize import replay_w
    from yoyo.evaluation.monday_limit_return_opt import frac
    from yoyo.evaluation.monday_limit_spike_hw import slot_replay
    from yoyo.evaluation.monday_limit_langlang import path_run
    from yoyo.evaluation.strategy_booklet_paths import Trace
    ro=json.loads((A/'exp-monday-limit-return-opt-20261027-v2/config.json').read_text())
    sh=json.loads((A/'exp-monday-limit-spike-hw-20261026-v1/config.json').read_text())
    py=json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    y=t.copy().reset_index(drop=True)
    for col in ('entry','exit'):
        y[col+'_ns']=pd.to_datetime(y[col+'_time'],utc=True).astype('datetime64[ns, UTC]').astype('int64')
        y[col]=y[col+'_ns']
    sz=cfg['v3_sizing'];k=np.minimum(sz['fraction']*(y.score.to_numpy()-sz['cut'])/sz['mean_excess'],sz['cap'])
    if track.startswith('v2'):k=np.full(len(y),.1)
    n=sh['ties'] if track=='spike' else py['ties'] if track=='allin3' else ro['ties']
    rng=np.random.default_rng(ro['seed']);rows=[];ledger0=None
    for seed_i in range(n):
        trace=Trace(pd.Timestamp(start,tz='UTC').value)
        if track in ('v2_60','v2_5','v3','v3lock','v3add'):
            order=np.lexsort((rng.random(len(y)),y.entry_ns.to_numpy()))
            z=y.iloc[order].reset_index(drop=True)
            result,_=replay_w(z,k[order],1.,trace=trace)
        elif track=='v3add20':result=frac(y,20,3,ro['seed']+seed_i,trace=trace)
        elif track=='spike':result,_=slot_replay(y,sh['sizing']['slots'],sh['sizing']['leverage'],sh['sizing']['liq_pct'],sh['seed']+seed_i,trace=trace)
        else:result=path_run(y,'allin',3,py['seed']+seed_i,trace=trace)
        ledger=pd.DataFrame(trace.rows)
        assert np.allclose(ledger.equity,1000+ledger.pnl.cumsum()+ledger.cashflow.cumsum(),rtol=1e-9,atol=1e-7)
        funded=ledger[ledger.notional>0]
        cash=float(ledger.cashflow.sum())
        equity=float(ledger.equity.iloc[-1])
        dd=float(100*(1-ledger.equity/ledger.equity.cummax()).max())
        rows.append(dict(seed_index=seed_i,funded=len(funded),win_pct=float(100*(funded.pnl>0).mean()) if len(funded) else 0.,
                         equity=equity,external_cash=cash,net_pnl=float(ledger.pnl.sum()),max_drawdown_pct=dd,
                         final_multiple=equity/1000 if cash==0 else None))
        if seed_i==0:ledger0=ledger
    d=pd.DataFrame(rows)
    return {c:float(d[c].median()) if d[c].notna().any() else None for c in d if c!='seed_index'},ledger0,rows


def summarize(config):
    cfg=json.loads(config.read_text());root=config.parent;pop=population(cfg)
    bases=[];paths={'ohlc':[],'olhc':[]};receipts=[]
    for symbol in symbols(cfg):
        d=root/'symbols'/symbol
        assert (d/'done.json').exists() and not (d/'error.json').exists(),symbol
        receipt=json.loads((d/'done.json').read_text());receipts.append(receipt)
        if receipt['baseline']:bases.append(read_csv(d/'baseline.csv.gz'))
        if not receipt['events']:continue
        x=read_csv(d/'orders.csv.gz')
        meta=x[['trade_id','event','score','order_score','rule','control_id','cohort_time','exit_mode','reclaim_minutes','parent']]
        for path in paths:
            z=meta.merge(read_csv(d/path/'trades.csv.gz'),on='trade_id',how='left',validate='one_to_one')
            z['filled']=z.net.notna();z['net']=z.net.fillna(0.)
            z['mode']=z.exit_mode;z['minutes']='m'+z.reclaim_minutes.astype(str)
            paths[path].append(z)
    paths={p:pd.concat(v,ignore_index=True).set_index('trade_id') for p,v in paths.items()}
    o,l=paths['ohlc'],paths['olhc'];l=l.reindex(o.index)
    w=o.copy();choose=l.net.lt(o.net);w.loc[choose]=l.loc[choose]
    # Preserve original OHLC child-fill universe, including zero for OLHC-only.
    only_l=o.rule.eq('add')&~o.filled&l.filled
    w.loc[only_l]=o.loc[only_l]
    w['chosen_path']=np.where(choose&~only_l,'olhc','ohlc')
    for name,t in [*paths.items(),('worse',w)]:t.to_csv(root/(name+'.csv.gz'))
    baseline=pd.concat(bases,ignore_index=True)
    baseline['score']=baseline.event.map(pop.score);baseline['order_score']=baseline.event.map(pop.l2_order_score)
    baseline['filled']=True;baseline['cohort_time']=baseline.entry_time
    baseline.to_csv(root/'baseline.csv.gz',index=False)
    prior=json.loads((ROOT/cfg['baseline']/'summary.json').read_text())['results']['plans']
    headline=[];details={};rng=np.random.default_rng(cfg['seed'])
    periods={'all':('2020-01-01',None),'2025':('2025-01-01','2026-01-01'),'2026':('2026-01-01',None)}
    for track in TRACKS:
        details[track]={}
        for arm,source in [('baseline',baseline),('order_only',w.reset_index())]:
            table=select_track(source,track,cfg,arm=='baseline')
            if arm=='baseline':
                ref=prior[track+'|A_rest_causal']['all']
                assert len(table)==ref['n'],(track,len(table),ref['n'])
                assert np.isclose(100*table.net.gt(1).mean(),ref['win_pct'])
            for period,(begin,end) in periods.items():
                t=table[table.cohort_time.ge(begin)]
                if end:t=t[t.cohort_time.lt(end)]
                sig=t[t.control_id.eq(0)&t.filled].copy()
                acc,ledger,seeds=accounts(track,sig,cfg,begin)
                ledger.to_csv(root/f'{track}-{arm}-{period}-seed0-ledger.csv.gz',index=False)
                # Controls are paired per leg; combine parent/add at equal notional
                # per event before the week-clustered comparison for add strategies.
                control={}
                if arm=='order_only':
                    q=t.groupby(['event','control_id'],as_index=False).net.mean()
                    control=paired(q,rng)
                if arm=='baseline' and track!='allin3':
                    ref=prior[track+'|A_rest_causal']['all' if period=='all' else 'y'+period]['replay']
                    assert np.isclose(acc['final_multiple'],ref['final_median'],rtol=1e-9,atol=1e-9),(track,period,acc,ref)
                    assert np.isclose(acc['max_drawdown_pct'],ref['dd_median'],rtol=1e-9,atol=1e-9),(track,period,acc,ref)
                row=dict(strategy=track,arm=arm,period=period,n=len(sig),events=sig.event.nunique(),wins=int(sig.net.gt(1).sum()),
                         win_pct=float(100*sig.net.gt(1).mean()),mean_net_bp=float(sig.net.mean()),
                         boundary=int(sig.reason.eq(0).sum()),score_missing=int(sig.score.isna().sum()),
                         score_nonpositive_size=int(sig.score.le(cfg['v3_sizing']['cut']).sum()) if track in ('v3','v3lock','v3add') else 0,
                         **{'account_'+k:v for k,v in acc.items()},matched_n=control.get('pairs'),
                         matched_signal_bp=control.get('signal_bp'),matched_random_bp=control.get('random_bp'),matched_week_p=control.get('week_sign_flip_p'))
                headline.append(row);details[track][arm+'_'+period]=dict(metrics=row,account_seeds=seeds,matched_random=control)
    selected=pop[pop.l2_order_score.ge(cfg['min_order'])]
    result=dict(config=cfg,coverage=dict(events=len(selected),missing_score=int(selected.score.isna().sum()),symbols=len(receipts),
                olhc_only_children=int(only_l.sum())),baseline_counts_and_accounts_match=True,strategies=details,
                native_counts={key:sum(r['paths'][p][key] for r in receipts for p in r['paths']) for key in ('reused','known_empty_children','new_orders','new_positions','new_fills')},
                source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),training_eligible=False,production_eligible=False)
    dump(root/'summary.json',clean(result));pd.DataFrame(headline).to_csv(root/'headline.csv',index=False)
    print(json.dumps(clean(dict(coverage=result['coverage'],native_counts=result['native_counts'],headline=headline))),flush=True)


def validate(config):
    cfg=json.loads(config.read_text());root=config.parent;count=0;worst=0.
    for symbol in symbols(cfg):
        d=root/'symbols'/symbol
        for path in ('ohlc','olhc'):
            p=d/path
            if not (p/'native-audit.json').exists():continue
            audit=json.loads((p/'native-audit.json').read_text())
            if not audit['fills']:continue
            rows=read_csv(p/'native-trades.csv.gz').set_index('trade_id')
            fills=read_csv(p/'native-fills.csv.gz')
            assert len(fills)==audit['fills']==2*len(rows)
            for tid,g in fills.groupby('trade_id'):
                r=rows.loc[tid];en=g[g.phase=='entry'].iloc[0];ex=g[g.phase!='entry'].iloc[0]
                assert en.quantity==ex.quantity and en.bar_i==r.entry_i and ex.bar_i==r.exit_i
                for fill in (en,ex):
                    fee=cfg['maker_fee'] if fill.phase in ('entry','target') else cfg['taker_fee']
                    np.testing.assert_allclose(fill.commission,fill.price*fill.quantity*fee,rtol=1e-7,atol=1e-8)
                expected=r.side*(ex.price/en.price-1)*1e4-g.commission.sum()/(en.price*en.quantity)*1e4
                worst=max(worst,abs(expected-r.net_notional_bp));assert abs(expected-r.net_notional_bp)<1e-6
                count+=1
    result=dict(new_positions_checked=count,max_fee_net_error_bp=worst,no_download=True,no_content_hash=True,no_model_fit=True)
    dump(root/'validation.json',result);print(json.dumps(result))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['run','summary','validate'])
    ap.add_argument('--config',type=Path,default=EXP/'config.json');ap.add_argument('--workers',type=int,default=4)
    args=ap.parse_args();config=args.config.resolve()
    if args.action=='run':run(config,args.workers)
    elif args.action=='summary':summarize(config)
    else:validate(config)
