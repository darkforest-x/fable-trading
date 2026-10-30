"""Owner 2026-10-30: remove only spike's score gate, retaining EMA >= 3.

Reuse frozen completed pre-touch 15m EMA features and original native candidate
orders. Selection arms use stored touch-close, causal pre-touch 5m, or the saved
pre-touch 1m model, all at 0.6, plus no score gate. Unknown scores stay unknown;
report the common score-known universe separately. Preserve original BE0/P18,
near Monday edge, H1, fees, slippage and the displayed fixed-seed allocator.
Only missing native order versions execute, once per OHLC/OLHC path. Original
same-symbol/week/side/prior-volatility matched controls retain unfilled zeros.
No fitting, parameter sweep, new execution engine, or production changes.
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
from yoyo.evaluation.v3_causal_close_entry import read_csv, execute, stats
from yoyo.evaluation.spike_close_execution import paired
from yoyo.evaluation.spike_booklet_close_compare import account

A = ROOT/'experiments/active'
EXP = A/'exp-spike-score-ablation-20261030-v1'
CACHES = [('exp-monday-limit-v3-grid-20261025-v1', 'near_P18'),
          ('exp-monday-limit-strategies-causal-20261027-v1', 'spike')]


def population(cfg):
    """EMA features are completed 15m bars before touch; scores remain separate."""
    f = read_csv(ROOT/cfg['features'], usecols=['event', 'l2_order_score']).set_index('event')
    s = read_csv(ROOT/cfg['scores']).set_index('event')[['stored', 'causal']]
    m = read_csv(ROOT/cfg['scores_1m']).set_index('event')[['causal']].rename(columns={'causal':'minute'})
    out = f.join(s).join(m)
    assert out.index.is_unique
    return out[(out.l2_order_score >= cfg['min_order']) & ~out.index.isin(BAD_EVENTS)]


def masks(s, cfg):
    return {'old_touch_score':s.stored >= cfg['score_cut'],
            'causal_5m':s.causal >= cfg['score_cut'],
            'causal_1m':s.minute >= cfg['score_cut'],
            'no_score_common':s[['stored','causal','minute']].notna().all(axis=1),
            'no_score_all':pd.Series(True,index=s.index)}


def select_orders(original, eligible):
    return original[(original.reclaim_minutes == 60) &
        (original.exit_mode == 'near_monday_edge') & original.event_id.isin(eligible)].copy()


def cache_matches(row, order, tick):
    """Reuse only equal executable price anchors and the original fill index."""
    return (all(np.isclose(float(row[k]), float(order[k]), rtol=1e-12,
                           atol=tick*1e-6) for k in ('entry','stop','target_price'))
            and int(row['entry_i']) == int(order['native_touch_i']))


def process(symbol, config):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    cfg = json.loads(Path(config).read_text()); out = Path(config).parent/'symbols'/symbol
    out.mkdir(parents=True, exist_ok=True)
    if (out/'done.json').exists(): return json.loads((out/'done.json').read_text())
    start = time.perf_counter(); origin = CODEX/'symbols'/symbol
    job = json.loads((origin/'identity.json').read_text())['job']
    assert job['group'] == 'crypto_5m'
    f, _ = load_job(job); prior = json.loads((origin/'done.json').read_text())
    assert (len(f),str(f.index[0]),str(f.index[-1])) == (prior['rows'],prior['first'],prior['last'])
    tick = json.loads((origin/'audit.json').read_text())['protection_tick']
    decimals = int(round(-np.log10(tick)))
    p = f[['open','high','low','close']].round(decimals)
    p.index = p.index.as_unit('ns'); frame = p.copy(); frame.index += pd.Timedelta(minutes=5)
    pop = population(cfg); wanted = pop[pop.index.str.startswith(symbol+':')]
    e = select_orders(read_csv(origin/'orders.csv.gz'),wanted.index)
    e['event'] = e.event_id
    available = set(e.loc[e.control_id == 0,'event'])
    assert available == set(wanted.index), (symbol,sorted(set(wanted.index)-available))
    old = read_csv(origin/'trades.csv.gz',usecols=['trade_id','entry_i']).set_index('trade_id')
    e['original_id'] = e.trade_id
    e['native_touch_i'] = e.original_id.map(old.entry_i)
    assert e.loc[e.control_id == 0,'native_touch_i'].notna().all()
    e['stop'] = stops(e,'P',cfg['backstop_pct'],tick)
    e['reclaim_ref'] = e.entry; e['be_offset_bp'] = cfg['breakeven_offset_bp']
    e['risk'] = e.entry; e['order_mode'] = 'limit'
    e['trade_id'] = e.original_id+':score_ablation'
    touch = e[e.control_id == 0].set_index('event').native_touch_i.astype(int)
    cohort = pd.Series((p.index[touch]+pd.Timedelta(minutes=5)).astype(str),index=touch.index)
    e['cohort_time'] = e.event.map(cohort)
    for col in wanted: e[col] = e.event.map(wanted[col])
    e.to_csv(out/'sources.csv.gz',index=False)
    receipts = {}
    for path in ('OHLC','OLHC'):
        dest = out/path.lower(); dest.mkdir(exist_ok=True)
        if (dest/'done.json').exists():
            receipts[path] = json.loads((dest/'done.json').read_text()); continue
        reused, reused_ids, mismatches = [], set(), []
        for study, variant in CACHES:
            source = A/study/path.lower()/'symbols'/symbol/'trades.csv.gz'
            if not source.exists(): continue
            cache = read_csv(source)
            cache = cache[cache.variant == variant].copy()
            cache['original_id'] = cache.trade_id.str.rsplit(':v_',n=1).str[0]
            cache = cache.set_index('original_id'); assert cache.index.is_unique
            for r in e[(e.control_id == 0) & ~e.trade_id.isin(reused_ids)].to_dict('records'):
                if r['original_id'] not in cache.index: continue
                row = cache.loc[r['original_id']].to_dict()
                if not cache_matches(row,r,tick):
                    mismatches.append(dict(trade_id=r['trade_id'],cache=study)); continue
                row.update(trade_id=r['trade_id'],cache_source=str(source.relative_to(ROOT)))
                reused.append(row); reused_ids.add(r['trade_id'])
        new = e[~e.trade_id.isin(reused_ids) & e.native_touch_i.notna()].copy()
        # Original no-fill controls remain zeros; barrier changes cannot create a touch.
        if (dest/'native-audit.json').exists():
            rows = read_csv(dest/'native-trades.csv.gz')
            audit = json.loads((dest/'native-audit.json').read_text())
        else:
            new.to_csv(dest/'native-orders.csv.gz',index=False)
            rows, fills, audit = execute(frame,new,path,tick,symbol,cfg)
            rows.to_csv(dest/'native-trades.csv.gz',index=False)
            fills.to_csv(dest/'native-fills.csv.gz',index=False)
            dump(dest/'native-audit.json',audit)
        rows = pd.concat([pd.DataFrame(reused),rows],ignore_index=True)
        assert rows.trade_id.is_unique
        assert set(rows.trade_id) == set(e.loc[e.native_touch_i.notna(),'trade_id'])
        filled = e.set_index('trade_id').loc[rows.trade_id]
        np.testing.assert_array_equal(rows.entry_i.to_numpy(),filled.native_touch_i.to_numpy())
        rows['entry_time'] = (p.index[rows.entry_i.astype(int)]+pd.Timedelta(minutes=5)).astype(str)
        rows['net'] = rows.net_notional_bp-np.where(rows.reason == 4,0.,cfg['non_target_slippage_bp'])
        h, low = p.high.to_numpy(),p.low.to_numpy()
        maes = []
        for r in rows.itertuples():
            seg = slice(int(r.entry_i),max(int(r.entry_i)+1,int(r.exit_i)))
            adverse = low[seg].min() if r.side == 1 else h[seg].max()
            maes.append(max(0.,-r.side*(adverse-r.entry)/r.entry*100))
        rows['mae_pct'] = maes
        cols = ['trade_id','entry','stop','target_price','side','exit_price','entry_i','exit_i',
                'entry_time','exit_time','reason','net_notional_bp','net','mae_pct','cache_source']
        rows.reindex(columns=cols).to_csv(dest/'trades.csv.gz',index=False)
        receipt = dict(reused=len(reused),new_orders=len(new),new_positions=audit['positions'],
            new_fills=audit['fills'],unfilled_controls=int(e.native_touch_i.isna().sum()),
            cache_mismatches=mismatches,engine=audit.get('engine'),version=audit.get('version'))
        dump(dest/'done.json',receipt); receipts[path] = receipt
    result = dict(symbol=symbol,events=len(wanted),orders=len(e),paths=receipts,
                  sources=[file_stamp(v) for v in job['paths']],seconds=time.perf_counter()-start)
    dump(out/'done.json',result); return result


def run(config,workers):
    from yoyo.evaluation.monday_limit_paper_accounts import committed
    committed('yoyo/evaluation/spike_score_ablation.py',str(config.relative_to(ROOT)))
    cfg = json.loads(config.read_text()); pop = population(cfg)
    syms = sorted(set(pop.index.str.split(':').str[0]))
    todo = [s for s in syms if not (config.parent/'symbols'/s/'done.json').exists()]
    with ProcessPoolExecutor(workers) as pool:
        jobs = {pool.submit(process,s,str(config)):s for s in todo}
        for i,fu in enumerate(as_completed(jobs),1):
            try:
                r = fu.result(); err = config.parent/'symbols'/jobs[fu]/'error.json'
                if err.exists(): err.unlink()
            except Exception as error:
                r = dict(symbol=jobs[fu],error=repr(error))
                dump(config.parent/'symbols'/jobs[fu]/'error.json',r)
            print(json.dumps(dict(progress=i,total=len(todo),**r)),flush=True)


def summarize(config):
    cfg = json.loads(config.read_text()); root = config.parent; pop = population(cfg)
    parts = {'ohlc':[],'olhc':[]}; receipts = []
    for symbol in sorted(set(pop.index.str.split(':').str[0])):
        d = root/'symbols'/symbol
        assert (d/'done.json').exists() and not (d/'error.json').exists(), symbol
        receipts.append(json.loads((d/'done.json').read_text()))
        sources = read_csv(d/'sources.csv.gz')
        meta = sources[['trade_id','event','control_id','week','cohort_time','stored','causal','minute','l2_order_score']]
        for path in parts:
            x = meta.merge(read_csv(d/path/'trades.csv.gz'),on='trade_id',how='left',validate='one_to_one')
            x['filled'] = x.net.notna(); x['net'] = x.net.fillna(0.)
            parts[path].append(x)
    paths = {p:pd.concat(rows,ignore_index=True).set_index('trade_id') for p,rows in parts.items()}
    o,l = paths['ohlc'],paths['olhc']; l = l.reindex(o.index)
    assert o.event.equals(l.event)
    w = o.copy(); choose = l.net < o.net; w.loc[choose] = l.loc[choose]
    w['chosen_path'] = np.where(choose,'olhc','ohlc')
    for name,x in [*paths.items(),('worse',w)]: x.to_csv(root/(name+'.csv.gz'))
    rng = np.random.default_rng(cfg['seed']); result = dict(arms={},coverage={},native_counts={})
    periods = {'all':lambda g:np.ones(len(g),bool),'2020_2024':lambda g:g.cohort_time<'2025-01-01',
        '2025':lambda g:(g.cohort_time>='2025-01-01')&(g.cohort_time<'2026-01-01'),
        '2026':lambda g:g.cohort_time>='2026-01-01'}
    headline = []
    for arm,mask in masks(pop,cfg).items():
        x = w[w.event.isin(pop.index[mask])]; result['arms'][arm] = {}
        for period,pick in periods.items():
            g = x[pick(x)]; signal = g[g.control_id == 0].copy()
            s = stats(signal); controls = paired(g,rng)
            account_rows = signal[signal.filled].copy()
            account_rows['entry_t'] = account_rows.entry_time
            account_rows['exit_t'] = account_rows.exit_time
            account_rows['symbol'] = account_rows.event.str.split(':').str[0]
            acct,ledger = account(account_rows,cfg)
            ledger.to_csv(root/(arm+'-'+period+'-ledger.csv.gz'),index=False)
            s.update(account=acct,matched_random=controls,
                without_boundary=stats(signal[~signal.reason.eq(0)]))
            result['arms'][arm][period] = s
            headline.append(dict(arm=arm,period=period,opportunities=s['opportunities'],fills=s['fills'],
                independent_win_pct=s['win_pct'],mean_net_bp=s['mean_fill_bp'],mean_gross_bp=s.get('mean_gross_bp'),
                mean_fee_bp=s.get('mean_fee_bp'),mean_slippage_bp=s.get('mean_slippage_bp'),
                funded=acct['taken'],account_win_pct=acct['win_pct'],ending_equity=acct['ending_equity'],
                return_pct=acct['return_pct'],max_drawdown_pct=acct['max_drawdown_pct'],
                matched_n=controls.get('pairs'),matched_signal_bp=controls.get('signal_bp'),
                matched_random_bp=controls.get('random_bp'),matched_week_p=controls.get('week_sign_flip_p')))
    # The original pictured curve must be the exact baseline, not an annual restart or median.
    old = read_csv(A/'exp-spike-booklet-close-compare-20261029-v1/baseline-ledger.csv.gz')
    mine = read_csv(root/'old_touch_score-all-ledger.csv.gz')
    pd.testing.assert_frame_equal(mine,old,check_dtype=False,atol=1e-7,rtol=1e-10)
    signal = w[w.control_id == 0]
    result['coverage'] = dict(events=len(pop),symbols=signal.event.str.split(':').str[0].nunique(),
        common=int(masks(pop,cfg)['no_score_common'].sum()),unknown_stored=int(pop.stored.isna().sum()),
        unknown_minute=int(pop.minute.isna().sum()),original_displayed_ledger_exact=True,
        expected_controls=len(pop)*3,actual_controls=int((w.control_id > 0).sum()),
        first=signal.cohort_time.min(),last=signal.cohort_time.max())
    for key in ('reused','new_orders','new_positions','new_fills'):
        result['native_counts'][key] = sum(r['paths'][p][key] for r in receipts for p in ('OHLC','OLHC'))
    result['path_sensitivity'] = {path:{arm:stats(t[(t.control_id == 0)&t.event.isin(pop.index[mask])])
        for arm,mask in masks(pop,cfg).items()} for path,t in paths.items()}
    dump(root/'summary.json',clean(dict(config=cfg,results=result,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        training_eligible=False,production_eligible=False)))
    pd.DataFrame(headline).to_csv(root/'headline.csv',index=False)
    print(json.dumps(clean(result)),flush=True)


def validate(config):
    cfg = json.loads(config.read_text()); count=0; worst=0.; root=config.parent
    for symbol in sorted(set(population(cfg).index.str.split(':').str[0])):
        for path in ('ohlc','olhc'):
            d=root/'symbols'/symbol/path
            receipt=json.loads((d/'native-audit.json').read_text())
            if not receipt['fills']: continue
            rows=read_csv(d/'native-trades.csv.gz').set_index('trade_id')
            orders=read_csv(d/'native-orders.csv.gz').set_index('trade_id')
            fills=read_csv(d/'native-fills.csv.gz')
            assert len(fills)==2*len(rows)==receipt['fills']
            for tid,g in fills.groupby('trade_id'):
                r,order=rows.loc[tid],orders.loc[tid]
                en=g[g.phase=='entry'].iloc[0]; ex=g[g.phase!='entry'].iloc[0]
                assert en.quantity==ex.quantity and en.bar_i==r.entry_i and ex.bar_i==r.exit_i
                assert order.entry_i<r.entry_i<=order.expire_i and r.entry_i==order.native_touch_i
                for fill in (en,ex):
                    fee=cfg['maker_fee'] if fill.phase in ('entry','target') else cfg['taker_fee']
                    np.testing.assert_allclose(fill.commission,fill.price*fill.quantity*fee,atol=1e-8,rtol=1e-7)
                expected=r.side*(ex.price/en.price-1)*1e4-g.commission.sum()/(en.price*en.quantity)*1e4
                worst=max(worst,abs(expected-r.net_notional_bp))
                np.testing.assert_allclose(expected,r.net_notional_bp,atol=1e-6)
                due=(pd.Timestamp(r.entry_time)-pd.Timedelta(minutes=5)).floor('60min')+pd.Timedelta(minutes=120)
                assert int(r.reclaim_due_ns)==due.value; count+=1
    o,l,w=[read_csv(root/(p+'.csv.gz')).set_index('trade_id') for p in ('ohlc','olhc','worse')]
    np.testing.assert_allclose(w.net,np.minimum(o.net,l.net))
    assert (w.loc[~w.filled,'net']==0).all()
    result=dict(new_positions_checked=count,max_fee_net_error_bp=worst,opportunities=len(w),
                no_download=True,no_content_hash=True,no_new_fit=True)
    dump(root/'validation.json',result); print(json.dumps(result))


if __name__ == '__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['run','summary','validate'])
    ap.add_argument('--config',type=Path,default=EXP/'config.json');ap.add_argument('--workers',type=int,default=4)
    args=ap.parse_args(); config=args.config.resolve()
    if args.action=='run':run(config,args.workers)
    elif args.action=='summary':summarize(config)
    else:validate(config)
