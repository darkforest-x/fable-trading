"""Replace only primary score admission with completed pre-touch EMA order>=3.

Owner 2026-10-31 keeps all eight 20261028 paper strategies' score-based targets,
sizing and add eligibility. Features are frozen l2_order_score from completed
15m bars before the touch. The declared score column is causal (the original
study) or stored (the owner's original touch-bar backtest comparison).
No feature is recomputed, no model fitted, no candle downloaded. Exact native
signal caches are reused; missing orders and original matched controls execute
once per declared path in Nautilus. Existing capital allocators consume fills.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
from pathlib import Path
import shutil
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
    """Read frozen EMA and the explicitly declared score clock; never substitute."""
    f = read_csv(ROOT/cfg['features'], usecols=['event','l2_order_score']).set_index('event')
    column = cfg.get('score_column', 'causal')
    if column not in ('causal', 'stored'):
        raise ValueError(f'Unsupported frozen score column: {column}')
    expected_arm = 'leaky_rest_touchbar' if column == 'stored' else 'A_rest_causal'
    if cfg.get('baseline_arm', expected_arm) != expected_arm:
        raise ValueError('Baseline and treatment must use the same score clock')
    s = read_csv(ROOT/cfg['scores']).set_index('event')[column].rename('score')
    x = f.join(s)
    return x[~x.index.isin(BAD_EVENTS)]


def admitted_population(cfg):
    """Reuse native execution for a frozen external event filter, if supplied.

    This hook never changes scores or execution. Its CSV must contain unique
    event IDs and an explicit boolean admitted flag, prepared before this run.
    Without the opt-in key, the original EMA-only experiment is unchanged.
    """
    pop = population(cfg)
    if cfg.get('admission_events'):
        gates = read_csv(ROOT/cfg['admission_events'], usecols=['event', 'admitted'])
        if not gates.event.is_unique or not gates.admitted.isin([True, False]).all():
            raise ValueError('Invalid external event admission ledger')
        return pop.loc[pop.index.intersection(gates.loc[gates.admitted, 'event'], sort=False)]
    return pop[pop.l2_order_score.ge(cfg['min_order'])]


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
        if cfg.get('preserve_original_admission', False):
            if rule == 'v2':
                x = x[x.score.ge(cfg['v2_threshold'])]
            elif rule == 'spike':
                x = x[x.score.ge(cfg['far_score']) & x.order_score.ge(cfg['min_order'])]
            else:
                modes = np.where(x.score.ge(cfg['far_score']), 'opposite_1618', 'range_0.618')
                x = x[x.score.ge(cfg['v3_cut']) & x.exit_mode.eq(modes)]
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


def same_order(row, order, tick):
    """Cache identity includes timing, protection and the native parent link."""
    if not cache_match(row, order, tick):
        return False
    for key in ('entry_i', 'expire_i', 'end_i', 'reclaim_minutes', 'be_offset_bp',
                'reclaim_ref', 'native_touch_i'):
        if not np.isclose(row[key], order[key], rtol=1e-12, atol=1e-12, equal_nan=True):
            return False
    return all((pd.isna(row[key]) and pd.isna(order[key])) or row[key] == order[key]
               for key in ('parent', 'order_mode'))


def prior_study(cfg):
    """A score-clock change may reuse fills only under unchanged execution rules."""
    if not cfg.get('reuse_study'):
        return None
    root = ROOT/cfg['reuse_study']
    old = json.loads((root/'config.json').read_text())
    for key in ('baseline', 'features', 'scores', 'min_order', 'maker_fee', 'taker_fee',
                'non_target_slippage_bp', 'breakeven_bp', 'lock_bp', 'far_score', 'add_pct'):
        if cfg[key] != old[key]:
            raise ValueError(f'Prior study execution mismatch: {key}')
    return root


def reuse_unchanged_symbol(symbol, cfg, out, selected):
    """Score values can change sizing without changing any native order.

    Reuse only if score availability and the target/add band are unchanged for
    every admitted event. Frame bytes are not read; registered sizes and times
    must still match. New score metadata feeds the unchanged account allocators.
    """
    prior = prior_study(cfg)
    if prior is None:
        return None
    d = prior/'symbols'/symbol
    if not (d/'done.json').exists() or (d/'error.json').exists():
        return None
    receipt = json.loads((d/'done.json').read_text())
    for stamp in receipt.get('sources', []):
        if file_stamp(Path(stamp['path'])) != stamp:
            raise ValueError(f'Cached source changed: {stamp["path"]}')
    if len(selected) != receipt['events']:
        return None
    if len(selected):
        orders = read_csv(d/'orders.csv.gz')
        old = orders[orders.control_id.eq(0)&orders.rule.eq('spike')].set_index('event').score
        new = selected.score.reindex(old.index)
        if set(old.index) != set(selected.index) or not old.isna().equals(new.isna()) \
                or not old.ge(cfg['far_score']).equals(new.ge(cfg['far_score'])):
            return None
        orders['score'] = orders.event.map(selected.score)
        orders.to_csv(out/'orders.csv.gz', index=False)
    shutil.copyfile(d/'baseline.csv.gz', out/'baseline.csv.gz')
    paths = {}
    for path in receipt['paths']:
        dest = out/path; dest.mkdir(exist_ok=True)
        shutil.copyfile(d/path/'trades.csv.gz', dest/'trades.csv.gz')
        count = receipt['paths'][path]['positions']
        p = dict(reused=count, known_empty_children=0,
                 known_empty_prior_orders=len(orders)-count, cache_mismatches=[],
                 new_orders=0, new_positions=0, new_fills=0, dependent_parents=0,
                 positions=count, engine='nautilus_trader', version='1.231.0',
                 source=str(d/path/'trades.csv.gz'))
        dump(dest/'done.json', p); paths[path] = p
    result = dict(receipt, paths=paths, reused_complete_symbol=True,
                  reuse_source=str(d), seconds=0.)
    dump(out/'done.json', result)
    return result


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
    pop = admitted_population(cfg)
    selected = pop[pop.index.str.startswith(symbol+':')]
    reused_symbol = reuse_unchanged_symbol(symbol, cfg, out, selected)
    if reused_symbol is not None:
        return reused_symbol
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
    if selected.empty:
        result = dict(symbol=symbol,events=0,baseline=len(base),paths={},seconds=time.perf_counter()-started)
        dump(out/'done.json',result);return result
    original = read_csv(origin/'orders.csv.gz')
    x = construct(original,selected,p.close.to_numpy(),tick,cfg)
    fills = read_csv(origin/'trades.csv.gz',usecols=['trade_id','entry_i']).set_index('trade_id')
    x['native_touch_i'] = x.original_id.map(fills.entry_i)
    core_rule = 'v2' if cfg.get('preserve_original_admission', False) else 'spike'
    core = x[x.control_id.eq(0)&x.rule.eq(core_rule)&x.reclaim_minutes.eq(60)].set_index('event')
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
        prior = prior_study(cfg)
        prior_empty = set()
        if prior is not None and (prior/'symbols'/symbol/'orders.csv.gz').exists():
            source = prior/'symbols'/symbol
            old_orders = read_csv(source/'orders.csv.gz').set_index('trade_id')
            old_rows = read_csv(source/path/'trades.csv.gz').set_index('trade_id')
            assert (source/path/'done.json').exists() and not (source/'error.json').exists()
            for r in x.to_dict('records'):
                key = r['trade_id']
                if key not in old_orders.index or not same_order(old_orders.loc[key], r, tick):
                    continue
                if key not in old_rows.index:
                    prior_empty.add(key)
                    continue
                row = old_rows.loc[key].to_dict()
                row.update(trade_id=key, cache_source=str(source/path/'trades.csv.gz'))
                reused.append(row); reused_ids.add(key)
            known_empty.update(prior_empty)
        for r in x[x.control_id.eq(0)].to_dict('records'):
            if r['trade_id'] in reused_ids|known_empty:
                continue
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
                if r['trade_id'] in reused_ids|known_empty:
                    continue
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
        receipt=dict(reused=len(reused),known_empty_children=len(known_empty-prior_empty),
                     known_empty_prior_orders=len(prior_empty),cache_mismatches=mismatches,
                     new_orders=len(runtime),new_positions=audit['positions'],new_fills=audit['fills'],
                     dependent_parents=len(deps),positions=len(rows),engine=audit.get('engine'),version=audit.get('version'))
        dump(d/'done.json',receipt);receipts[path]=receipt
    result=dict(symbol=symbol,events=len(selected),orders=len(x),baseline=len(base),paths=receipts,
                sources=[file_stamp(v) for v in job['paths']],seconds=time.perf_counter()-started)
    dump(out/'done.json',result);return result


def symbols(cfg):
    pop=admitted_population(cfg)
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
        raw_equity=float(ledger.equity.iloc[-1])
        multiple=result.get('final_multiple',raw_equity/1000 if cash==0 else None)
        equity=1000*multiple if 'final_multiple' in result else raw_equity
        dd=float(result.get('max_drawdown_pct',100*(1-ledger.equity/ledger.equity.cummax()).max()))
        rows.append(dict(seed_index=seed_i,funded=len(funded),win_pct=float(100*(funded.pnl>0).mean()) if len(funded) else 0.,
                         equity=equity,raw_ending_equity=raw_equity,external_cash=cash,net_pnl=float(ledger.pnl.sum()),max_drawdown_pct=dd,
                         final_multiple=multiple if cash==0 else None))
        if seed_i==0:ledger0=ledger
    d=pd.DataFrame(rows)
    summary={c:float(d[c].median()) if d[c].notna().any() else None for c in d if c!='seed_index'}
    if d.external_cash.gt(0).any():summary['final_multiple']=None
    return summary,ledger0,rows


def worse_paths(o,l):
    """Keep the original OHLC child universe; compare only dual-filled children."""
    l=l.reindex(o.index)
    comparable=o.rule.ne('add')|(o.filled&l.filled)
    choose=l.net.lt(o.net)&comparable
    w=o.copy();w.loc[choose]=l.loc[choose]
    w['chosen_path']=np.where(choose,'olhc','ohlc')
    return w


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
    w=worse_paths(o,l)
    only_l=o.rule.eq('add')&~o.filled&l.filled
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
                ref=prior[track+'|'+cfg.get('baseline_arm','A_rest_causal')]['all']
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
                    ref=prior[track+'|'+cfg.get('baseline_arm','A_rest_causal')]['all' if period=='all' else 'y'+period]['replay']
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
                olhc_only_children=int(only_l.sum())),baseline_counts_match_all_eight=True,
                saved_account_metrics_match_seven_non_allin=True,
                allin_account_scope='Additional fixed full/annual starts with original allocator and replenishments; not the old monthly goal-rate summary.',strategies=details,
                reused_complete_symbols=sum(bool(r.get('reused_complete_symbol')) for r in receipts),
                native_counts={key:sum(r['paths'][p].get(key,0) for r in receipts for p in r['paths']) for key in ('reused','known_empty_children','known_empty_prior_orders','new_orders','new_positions','new_fills')},
                source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),training_eligible=False,production_eligible=False)
    dump(root/'summary.json',clean(result));pd.DataFrame(headline).to_csv(root/'headline.csv',index=False)
    print(json.dumps(clean(dict(coverage=result['coverage'],native_counts=result['native_counts'],headline=headline))),flush=True)


def validate(config):
    cfg=json.loads(config.read_text());root=config.parent;count=0;worst=0.;clocks=0
    prior_control_duplicates=0;required_control_parents=0
    prior = prior_study(cfg); prior_parent_dependencies = 0; score_rows = 0
    pop = population(cfg)
    for symbol in symbols(cfg):
        d=root/'symbols'/symbol
        if (d/'orders.csv.gz').exists():
            saved = read_csv(d/'orders.csv.gz')
            np.testing.assert_allclose(saved.score, saved.event.map(pop.score), equal_nan=True)
            score_rows += len(saved)
        for path in ('ohlc','olhc'):
            p=d/path
            if not (p/'native-audit.json').exists():continue
            audit=json.loads((p/'native-audit.json').read_text())
            if not audit['fills']:continue
            rows=read_csv(p/'native-trades.csv.gz').set_index('trade_id')
            orders=read_csv(p/'native-orders.csv.gz').set_index('trade_id')
            if prior is not None:
                source = prior/'symbols'/symbol
                old_orders = read_csv(source/'orders.csv.gz').set_index('trade_id')
                cached = read_csv(source/path/'trades.csv.gz').set_index('trade_id')
                tick = json.loads((CODEX/'symbols'/symbol/'audit.json').read_text())['protection_tick']
                parents = set(orders.parent.dropna())
                for tid, order in orders.iterrows():
                    if tid in old_orders.index and same_order(old_orders.loc[tid], order, tick):
                        assert tid in parents, ('avoidable_prior_order_rerun', tid)
                        prior_parent_dependencies += int(tid in cached.index)
            older=A/'exp-v3-causal-close-entry-20261030-v1'/'symbols'/symbol/path/'trades.csv.gz'
            if older.exists():
                cached=read_csv(older).set_index('trade_id')
                parents=set(orders.parent.dropna())
                for tid,r in orders[orders.rule.eq('p30')&orders.control_id.gt(0)].iterrows():
                    key=r.original_id+':entry_rest'
                    if key not in cached.index or tid not in rows.index:continue
                    old=cached.loc[key]
                    if not all(np.isclose(old[k],r[k],rtol=1e-12,atol=1e-12) for k in ('entry','stop','target_price')):continue
                    if tid in parents:required_control_parents+=1
                    else:prior_control_duplicates+=1
            fills=read_csv(p/'native-fills.csv.gz')
            assert len(fills)==audit['fills']==2*len(rows)
            for tid,g in fills.groupby('trade_id'):
                r=rows.loc[tid];order=orders.loc[tid]
                en=g[g.phase=='entry'].iloc[0];ex=g[g.phase!='entry'].iloc[0]
                assert en.quantity==ex.quantity and en.bar_i==r.entry_i and ex.bar_i==r.exit_i
                if order.rule!='add':
                    assert order.entry_i<r.entry_i<=order.expire_i and r.entry_i==order.native_touch_i
                    opened=pd.Timestamp(r.entry_time)-pd.Timedelta(minutes=5)
                    due=opened.floor(f'{int(order.reclaim_minutes)}min')+pd.Timedelta(minutes=2*int(order.reclaim_minutes))
                    assert int(r.reclaim_due_ns)==due.value
                    clocks+=1
                for fill in (en,ex):
                    fee=cfg['maker_fee'] if fill.phase in ('entry','target') else cfg['taker_fee']
                    np.testing.assert_allclose(fill.commission,fill.price*fill.quantity*fee,rtol=1e-7,atol=1e-8)
                expected=r.side*(ex.price/en.price-1)*1e4-g.commission.sum()/(en.price*en.quantity)*1e4
                worst=max(worst,abs(expected-r.net_notional_bp));assert abs(expected-r.net_notional_bp)<1e-6
                count+=1
    o,l,w=[read_csv(root/(path+'.csv.gz')).set_index('trade_id') for path in ('ohlc','olhc','worse')]
    l=l.reindex(o.index);w=w.reindex(o.index)
    one_path_child=o.rule.eq('add')&~(o.filled&l.filled)
    np.testing.assert_allclose(w.net,np.where(one_path_child,o.net,np.minimum(o.net,l.net)))
    for path in (o,l):
        assert path.loc[path.control_id.eq(0)&path.rule.ne('add'),'filled'].all()
        assert path.loc[~path.filled,'net'].eq(0).all()
    base=read_csv(root/'baseline.csv.gz');common_counts={}
    for track in TRACKS:
        a=select_track(base,track,cfg,True)
        b=select_track(w.reset_index(),track,cfg)
        b=b[b.control_id.eq(0)&b.filled]
        a=a.assign(is_add=a.variant.str.endswith('_add'))
        b=b.assign(is_add=b.rule.eq('add'))
        common=a.merge(b,on=['event','is_add'],suffixes=('_old','_new'),validate='one_to_one')
        for field in ('entry_i','exit_i','entry','exit_price','net','reason'):
            np.testing.assert_allclose(common[field+'_old'],common[field+'_new'],atol=1e-8,rtol=1e-10)
        common_counts[track]=len(common)
    result=dict(new_positions_checked=count,max_fee_net_error_bp=worst,confirmation_clocks_checked=clocks,
                unchanged_common_fills=common_counts,original_no_fill_controls_zero=True,
                score_column=cfg.get('score_column','causal'),score_metadata_rows_checked=score_rows,
                unchanged_prior_orders_rerun_only_as_parent_dependencies=prior_parent_dependencies,
                earlier_control_cache_coverage_gap_positions=prior_control_duplicates,
                native_parent_dependencies_with_old_standalone_controls=required_control_parents,
                no_download=True,no_content_hash=True,no_model_fit=True)
    dump(root/'validation.json',result);print(json.dumps(result))


def diagnostics(config):
    """Read-only boundary and best-week attribution; never replay an account."""
    cfg=json.loads(config.read_text());root=config.parent
    w=read_csv(root/'worse.csv.gz');out={}
    for track in TRACKS:
        t=select_track(w,track,cfg);t=t[t.control_id.eq(0)&t.filled]
        for period,begin in [('all','2020-01-01'),('2026','2026-01-01')]:
            s=t[t.cohort_time.ge(begin)];nb=s[~s.reason.eq(0)]
            weeks=s.event.str.split(':').str[1]
            weekly=s.groupby(weeks).net.sum().sort_values(ascending=False)
            ledger=read_csv(root/f'{track}-order_only-{period}-seed0-ledger.csv.gz')
            boundary=ledger.notional.gt(0)&pd.to_numeric(ledger.reason,errors='coerce').eq(0)
            out[track+'|'+period]=dict(n=len(s),mean_bp=float(s.net.mean()),boundary=len(s)-len(nb),
                without_boundary_mean_bp=float(nb.net.mean()),best_week=str(weekly.index[0]),
                mean_without_best_week_bp=float(s[weeks!=weekly.index[0]].net.mean()),
                funded_boundary_pnl=float(ledger.loc[boundary,'pnl'].sum()),seed0_net_pnl=float(ledger.pnl.sum()))
    dump(root/'concentration.json',out)
    summary=json.loads((root/'summary.json').read_text())
    summary.pop('baseline_counts_and_accounts_match',None)
    summary.update(baseline_counts_match_all_eight=True,saved_account_metrics_match_seven_non_allin=True,
                   allin_account_scope='Additional fixed full/annual starts with original allocator and replenishments; not the old monthly goal-rate summary.')
    dump(root/'summary.json',summary)
    print(json.dumps(out))


def monthly(config):
    """Reuse the original all-in monthly starts and seeds on saved native fills."""
    from yoyo.evaluation.monday_limit_langlang import path_run
    cfg=json.loads(config.read_text()); root=config.parent
    py=json.loads((A/'exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    arms={}; details=[]
    for arm,filename in [('baseline','baseline.csv.gz'),('order_only','worse.csv.gz')]:
        table=select_track(read_csv(root/filename),'allin3',cfg,arm=='baseline')
        table=table[table.control_id.eq(0)&table.filled].copy()
        table['entry_t']=pd.to_datetime(table.entry_time,utc=True).astype('datetime64[ns, UTC]')
        table['exit_t']=pd.to_datetime(table.exit_time,utc=True).astype('datetime64[ns, UTC]')
        table['entry_ns']=table.entry_t.astype('int64'); table['exit_ns']=table.exit_t.astype('int64')
        table['entry']=table.entry_ns;table['exit']=table.exit_ns
        rows=[]
        for k,start in enumerate(pd.date_range(py['first_start'],py['last_start'],freq='MS',tz='UTC')):
            t=table[table.entry_t>=start].reset_index(drop=True)
            for r in range(py['ties']):
                seed=py['seed']+1000*k+r
                h=path_run(t,'allin',3,seed)
                rows.append(dict(arm=arm,start=str(start),seed=seed,
                    **{f'd{q}':h[q]['days'] if h[q] else np.nan for q in (10,100)},
                    **{f'b{q}':h[q]['blowups'] if h[q] else np.nan for q in (10,100)}))
        d=pd.DataFrame(rows);details.extend(rows)
        arms[arm]={f'x{q}':dict(reached_pct=float(100*d[f'd{q}'].notna().mean()),
                    days_median=float(d[f'd{q}'].median()) if d[f'd{q}'].notna().any() else None,
                    blowups_before_median=float(d[f'b{q}'].median()) if d[f'b{q}'].notna().any() else None)
                   for q in (10,100)}
    prior=json.loads((ROOT/cfg['baseline']/'summary.json').read_text())['results']['plans']
    ref=prior['allin3|'+cfg.get('baseline_arm','A_rest_causal')]['allin3x_monthly_starts']
    for goal,values in ref.items():
        for key,value in values.items():
            if value is None: assert arms['baseline'][goal][key] is None
            else: assert np.isclose(value,arms['baseline'][goal][key]),(goal,key,value,arms)
    result=dict(config=py,arms=arms,trials_per_arm=len(rows),baseline_matches_original=True,
                accounting='Original all-in allocator including replenishment after blowups; goal reach rates are not investment returns.',
                native_fills_rerun=False,trials=details)
    dump(root/'allin_monthly.json',clean(result))
    summary=json.loads((root/'summary.json').read_text())
    summary['allin_original_monthly_starts']=dict(arms=arms,trials_per_arm=len(rows),baseline_matches_original=True)
    dump(root/'summary.json',summary)
    print(json.dumps(dict(arms=arms,trials_per_arm=len(rows))))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['run','summary','validate','diagnostics','monthly'])
    ap.add_argument('--config',type=Path,default=EXP/'config.json');ap.add_argument('--workers',type=int,default=4)
    args=ap.parse_args();config=args.config.resolve()
    if args.action=='run':run(config,args.workers)
    elif args.action=='summary':summarize(config)
    elif args.action=='validate':validate(config)
    elif args.action=='diagnostics':diagnostics(config)
    else:monthly(config)
