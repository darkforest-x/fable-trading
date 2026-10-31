"""Summarize a frozen harmonic gate using existing native fills and allocators.

No OHLC execution or model fitting occurs here. Original baseline account
results are reused, and filtered capital paths call the same historical
allocators. A harmonic candidate is not a probability model; score diagnostics
below describe the unchanged original score, including its known lookahead.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_harmonic import EXP, baseline
from yoyo.evaluation.monday_order_only import TRACKS, accounts, select_track, worse_paths
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp
from yoyo.evaluation.monday_limit_be_scalein_compare import clean
from yoyo.evaluation.v3_causal_close_entry import read_csv
from yoyo.evaluation.spike_close_execution import paired

PERIODS={'all':('2020-01-01',None),'2025':('2025-01-01','2026-01-01'),'2026':('2026-01-01',None)}


def valid_geometry(pattern, definitions):
    """Recheck actual XABC prices: forming() can join inconsistent A anchors.

    This validates the package's own frozen ratio table, without widening it
    or searching additional patterns. It uses no outcome or touch-bar data.
    """
    from pyharmonics import constants as c
    X,A,B,C=map(float,pattern['prices'][:4]);name=pattern['name']
    if A==X or A==B:return False
    ratios=((c.XAB,abs(A-B)/abs(A-X)),(c.ABC,abs(B-C)/abs(A-B)))
    return all(definitions[stage][name][c.MIN]<=r<=definitions[stage][name][c.MAX] for stage,r in ratios)


def validate_candidates(config):
    """Preserve raw library candidates; emit stricter verified admission rows."""
    from pyharmonics import constants as c, utils
    cfg=json.loads(config.read_text());root=config.parent
    defs=utils.get_pattern_definition(cfg['harmonic']['fib_tolerance'],c.MATRIX_PATTERNS)
    gates=read_csv(root/'gates.csv.gz');counts={};valid=[];rejected=[]
    for path in sorted((root/'scan').glob('*/patterns.jsonl.gz')):
        with gzip.open(path,'rt') as handle:
            for line in handle:
                p=json.loads(line)
                if valid_geometry(p,defs):
                    valid.append(p);counts.setdefault(p['event'],[0,0])
                    counts[p['event']][0]+=1;counts[p['event']][1]+=int(p['confluence'])
                else:rejected.append(p)
    gates['raw_admitted']=gates.admitted
    gates['raw_candidate_count']=gates.candidate_count
    gates['candidate_count']=gates.event.map(lambda e:counts.get(e,[0,0])[0])
    gates['confluent_count']=gates.event.map(lambda e:counts.get(e,[0,0])[1])
    gates['admitted']=gates.observable & gates.confluent_count.gt(0)
    assert not (gates.admitted & ~gates.raw_admitted).any()
    gates.to_csv(ROOT/cfg['admission_events'],index=False)
    for name,rows in [('patterns-validated',valid),('patterns-invalid-library-join',rejected)]:
        with gzip.open(root/(name+'.jsonl.gz'),'wt') as handle:
            for row in rows:handle.write(json.dumps(row)+'\n')
    out=dict(raw_patterns=len(valid)+len(rejected),valid_patterns=len(valid),
        rejected_inconsistent_xabc=len(rejected),raw_admitted=int(gates.raw_admitted.sum()),
        admitted=int(gates.admitted.sum()),removed_events=int((gates.raw_admitted & ~gates.admitted).sum()),
        correction='Before looking at returns, reject forming() joins whose actual XABC prices violate the same library ratio table. Raw candidates preserved; no rescan or parameter change.',
        no_outcome_columns=True,no_touch_candle=True)
    dump(root/'harmonic-validation.json',out);print(json.dumps(out))


def period(table,begin,end):
    t=table[table.cohort_time.ge(begin)]
    return t[t.cohort_time.lt(end)] if end else t


def stats(t):
    if t.empty:return dict(n=0,events=0,mean_net_bp=None,mean_gross_bp=None,win_pct=None,pf=None)
    gross=t.side*(t.exit_price/t.entry-1)*1e4
    wins=t.net[t.net>0].sum();loss=-t.net[t.net<0].sum()
    return dict(n=len(t),events=t.event.nunique(),wins=int(t.net.gt(1).sum()),win_pct=float(100*t.net.gt(1).mean()),
                positive_net_pct=float(100*t.net.gt(0).mean()),mean_net_bp=float(t.net.mean()),
                mean_gross_bp=float(gross.mean()),mean_fee_bp=float((gross-t.net_notional_bp).mean()),
                mean_slippage_bp=float((t.net_notional_bp-t.net).mean()),sum_net_bp=float(t.net.sum()),
                pf=float(wins/loss) if loss>0 else None,boundary=int(t.reason.eq(0).sum()))


def score_diagnostics(t):
    """The same frozen score, not a fitted harmonic classifier; per-period only."""
    t=t[~t.variant.str.endswith('_add')].drop_duplicates('event')
    t=t[t.score.notna()]
    y=t.net.gt(0);n1=int(y.sum());n0=len(t)-n1
    auc=(t.score.rank(method='average')[y].sum()-n1*(n1+1)/2)/(n1*n0) if n1*n0 else None
    n=max(1,int(np.ceil(len(t)*.1)))
    top=t.sort_values(['score','event'],ascending=[False,True]).head(n)
    return dict(auc=None if auc is None else float(auc),n=len(t),top_decile=stats(top),
                interpretation='Unchanged original fill-bar score; no model fit and no harmonic score optimization.')


def filter_difference(t,gates,seed):
    """Week cluster bootstrap retained-minus-rejected event returns.

    Children are first averaged with their parent at equal notional per event.
    This is a descriptive selection contrast, not a matched-time causal effect
    or a test of the nonlinear funded-account equity difference.
    """
    e=t.groupby('event',sort=True).net.mean().to_frame()
    e['keep']=e.index.map(gates.admitted).fillna(False).astype(bool)
    e['week']=e.index.str.split(':').str[1]
    if not e.keep.any() or e.keep.all():return dict(available=False)
    e['kept_sum']=np.where(e.keep,e.net,0.);e['other_sum']=np.where(~e.keep,e.net,0.)
    e['kept_n']=e.keep.astype(int);e['other_n']=(~e.keep).astype(int)
    w=e.groupby('week')[['kept_sum','other_sum','kept_n','other_n']].sum()
    a=w.to_numpy();rng=np.random.default_rng(seed)
    sample=rng.integers(0,len(a),size=(4000,len(a)))
    s=a[sample].sum(axis=1);ok=(s[:,2]>0)&(s[:,3]>0)
    boot=s[ok,0]/s[ok,2]-s[ok,1]/s[ok,3]
    kept=e[e.keep];byweek=kept.groupby('week').net.sum().sort_values(ascending=False)
    trimmed=kept[~kept.week.isin(byweek.head(3).index)]
    return dict(available=True,kept=len(kept),rejected=int((~e.keep).sum()),weeks=len(a),
        kept_mean_bp=float(kept.net.mean()),rejected_mean_bp=float(e.loc[~e.keep,'net'].mean()),
        difference_bp=float(kept.net.mean()-e.loc[~e.keep,'net'].mean()),
        week_bootstrap_difference_ci95_bp=[float(v) for v in np.quantile(boot,[.025,.975])],
        bootstrap_probability_not_positive=float((boot<=0).mean()),
        without_best3_weeks_n=len(trimmed),without_best3_weeks_mean_bp=float(trimmed.net.mean()) if len(trimmed) else None,
        best3_weeks=byweek.head(3).to_dict())


def collect_controls(config):
    cfg=json.loads(config.read_text());root=config.parent
    gates=read_csv(ROOT/cfg['admission_events']);syms=sorted(set(gates.loc[gates.admitted,'symbol']))
    paths={'ohlc':[],'olhc':[]};receipts=[];sources=[]
    for s in syms:
        d=root/'symbols'/s
        if not (d/'done.json').exists():raise RuntimeError(f'Missing native control completion: {s}')
        receipt=json.loads((d/'done.json').read_text());receipts.append(receipt)
        orders=read_csv(d/'orders.csv.gz')
        meta=orders[['trade_id','cached_id','event','score','order_score','rule','control_id','cohort_time','exit_mode','reclaim_minutes','parent']]
        for path in paths:
            rows=read_csv(d/path/'trades.csv.gz')
            joined=meta.merge(rows,on='trade_id',how='left',validate='one_to_one')
            joined['filled']=joined.net.notna();joined['net']=joined.net.fillna(0.)
            joined['mode']=joined.exit_mode;joined['minutes']='m'+joined.reclaim_minutes.astype(int).astype(str)
            paths[path].append(joined)
            sources.append(file_stamp(d/path/'trades.csv.gz'))
    if not syms:return pd.DataFrame(),dict(symbols=0,new_positions=0)
    o,l=(pd.concat(paths[p],ignore_index=True).set_index('trade_id') for p in ('ohlc','olhc'))
    w=worse_paths(o,l).reset_index()
    b=baseline(cfg).set_index('trade_id');signals=w[w.control_id.eq(0)&w.filled]
    # Every eligible signal is an unchanged native baseline position.
    assert set(signals.cached_id)<=set(b.index)
    expected=b.loc[signals.cached_id]
    maxerr=float(np.max(np.abs(expected.net.to_numpy()-signals.net.to_numpy()))) if len(signals) else 0.
    assert maxerr<1e-7,maxerr
    for col in ('side','entry','stop','target_price','exit_price'):
        assert np.allclose(expected[col],signals[col],equal_nan=True,rtol=1e-10,atol=1e-10),col
    w.to_csv(root/'matched-controls.csv.gz',index=False)
    totals={k:sum(r['paths'][p].get(k,0) for r in receipts for p in r['paths'])
            for k in ('reused','new_orders','new_positions','new_fills','dependent_parents','known_empty_children','known_empty_prior_orders')}
    totals.update(symbols=len(syms),signal_rows_checked=len(signals),signal_net_max_error_bp=maxerr,sources=sources)
    dump(root/'controls-validation.json',totals)
    return w,totals


def validate_outputs(config):
    """Audit saved clocks, source stamps and native fills without replaying them."""
    from yoyo.evaluation.monday_harmonic import events, STEP, interval_step
    from yoyo.evaluation.monday_order_only import population, prior_studies, same_order
    from yoyo.evaluation.monday_limit_backstop import CODEX
    cfg=json.loads(config.read_text());root=config.parent
    gates=read_csv(ROOT/cfg['admission_events']).set_index('event')
    expected=events(cfg).set_index('event')
    assert gates.index.is_unique and set(gates.index)==set(expected.index)
    assert (pd.to_datetime(gates.cutoff,utc=True)==pd.to_datetime(expected.loc[gates.index].cutoff,utc=True)).all()
    seen=gates[gates.observable]
    step=interval_step(cfg['harmonic'])
    boundary=pd.to_datetime(seen.cutoff,utc=True).dt.floor(step)
    assert (pd.to_datetime(seen.last_input_close,utc=True)==boundary).all()
    if step>STEP:
        assert (pd.to_datetime(seen.invalidation_last_close,utc=True)==pd.to_datetime(seen.cutoff,utc=True)).all()
    stamps={};prefix_checks=0;patterns=0
    for path in (root/'scan').glob('*/done.json'):
        r=json.loads(path.read_text())
        for s in r['sources']:stamps[s['path']]=s
        for c in r['causality_checks']:
            assert c['prefix_equal'] and c['future_mutation_equal'];prefix_checks+=1
    for path,saved in stamps.items():assert file_stamp(path)==saved,path
    with gzip.open(root/'patterns-validated.jsonl.gz','rt') as handle:
        for line in handle:
            p=json.loads(line);cut=pd.Timestamp(p['cutoff'])
            assert all(pd.Timestamp(v)+step<=cut for v in p['points'])
            assert pd.Timestamp(p['xabc_confirmed_at'])<=cut
            if p['confluence']:
                assert p['prz_low']<=gates.loc[p['event'],'entry']<=p['prz_high']
            patterns+=1
    pop=population(cfg);priors=prior_studies(cfg)
    score_rows=positions=clocks=parent_reruns=native_signal_orders=0;worst=0.;mismatches=[]
    syms=sorted(gates.loc[gates.admitted,'symbol'].unique())
    for symbol in syms:
        d=root/'symbols'/symbol;saved=read_csv(d/'orders.csv.gz')
        np.testing.assert_allclose(saved.score,saved.event.map(pop.score),equal_nan=True)
        assert set(saved.event)<=set(gates.index[gates.admitted]);score_rows+=len(saved)
        tick=json.loads((CODEX/'symbols'/symbol/'audit.json').read_text())['protection_tick']
        for path in ('ohlc','olhc'):
            p=d/path;r=json.loads((p/'done.json').read_text());mismatches.extend(r['cache_mismatches'])
            audit=json.loads((p/'native-audit.json').read_text())
            orders=read_csv(p/'native-orders.csv.gz').set_index('trade_id')
            native_signal_orders+=int(orders.control_id.eq(0).sum())
            for prior in priors:
                source=prior/'symbols'/symbol
                if not (source/'orders.csv.gz').exists():continue
                old=read_csv(source/'orders.csv.gz').set_index('trade_id');parents=set(orders.parent.dropna())
                for tid,order in orders.iterrows():
                    if tid in old.index and same_order(old.loc[tid],order,tick):
                        assert tid in parents,('avoidable_rerun',tid);parent_reruns+=1
            if not audit['fills']:continue
            rows=read_csv(p/'native-trades.csv.gz').set_index('trade_id');fills=read_csv(p/'native-fills.csv.gz')
            assert len(fills)==audit['fills']==2*len(rows)
            for tid,g in fills.groupby('trade_id'):
                row=rows.loc[tid];order=orders.loc[tid]
                en=g[g.phase.eq('entry')].iloc[0];ex=g[~g.phase.eq('entry')].iloc[0]
                assert en.quantity==ex.quantity and en.bar_i==row.entry_i and ex.bar_i==row.exit_i
                if order.rule!='add':
                    assert order.entry_i<row.entry_i<=order.expire_i and row.entry_i==order.native_touch_i
                    opened=pd.Timestamp(row.entry_time)-STEP
                    due=opened.floor(f'{int(order.reclaim_minutes)}min')+pd.Timedelta(minutes=2*int(order.reclaim_minutes))
                    assert int(row.reclaim_due_ns)==due.value;clocks+=1
                for fill in (en,ex):
                    fee=cfg['maker_fee'] if fill.phase in ('entry','target') else cfg['taker_fee']
                    np.testing.assert_allclose(fill.commission,fill.price*fill.quantity*fee,rtol=1e-7,atol=1e-8)
                net=row.side*(ex.price/en.price-1)*1e4-g.commission.sum()/(en.price*en.quantity)*1e4
                worst=max(worst,abs(net-row.net_notional_bp));assert abs(net-row.net_notional_bp)<1e-6
                positions+=1
    assert not mismatches,mismatches[:3]
    assert native_signal_orders==0,native_signal_orders
    result=dict(events=len(gates),observable=len(seen),source_files_unchanged=len(stamps),
        prefix_and_future_mutation_checks=prefix_checks,validated_patterns=patterns,
        score_metadata_rows_checked=score_rows,new_native_positions_checked=positions,
        confirmation_clocks_checked=clocks,max_fee_net_error_bp=worst,
        exact_cached_orders_rerun_only_as_dependencies=parent_reruns,cache_mismatches=0,
        native_signal_orders=native_signal_orders,
        interval=cfg['harmonic']['interval'],complete_higher_bars_only=True,
        touch_bar_excluded=True,no_execution_replay=True)
    dump(root/'validation.json',result);print(json.dumps(result))


def summarize(config):
    cfg=json.loads(config.read_text());root=config.parent
    gates=read_csv(ROOT/cfg['admission_events']).set_index('event')
    b=baseline(cfg);old=read_csv(ROOT/cfg['baseline_comparison']/'headline.csv')
    controls,validation=collect_controls(config)
    prior=json.loads((ROOT/cfg['baseline_comparison']/'summary.json').read_text())
    headline=[];details={};annual=[]
    for track in TRACKS:
        table=select_track(b,track,cfg,True)
        assert set(table.event)<=set(gates.index)
        table['observable']=table.event.map(gates.observable).astype(bool)
        table['admitted']=table.event.map(gates.admitted).astype(bool)
        source=select_track(controls,track,cfg,False) if len(controls) else controls
        details[track]={}
        for label,(begin,end) in PERIODS.items():
            t=period(table,begin,end);selected=t[t.admitted].copy()
            ref=old[(old.strategy==track)&(old.arm=='baseline')&(old.period==label)].iloc[0]
            st=stats(t)
            assert len(t)==int(ref.n) and np.isclose(st['mean_net_bp'],ref.mean_net_bp)
            # All original baseline capital summaries are already complete.
            base={k:ref[k] for k in ref.index if k.startswith('account_')}
            same=t[t.observable]
            for arm,rows in [('baseline',t),('harmonic',selected)]:
                metrics=stats(rows)
                if arm=='baseline':acc=base;seeds=None
                else:
                    s,ledger,seeds=accounts(track,rows,cfg,begin)
                    acc={'account_'+k:v for k,v in s.items()}
                    ledger.to_csv(root/f'{track}-{label}-ledger.csv.gz',index=False)
                matched={}
                if arm=='harmonic' and len(source):
                    q=period(source,begin,end).groupby(['event','control_id'],as_index=False).net.mean()
                    matched=paired(q,np.random.default_rng(cfg['seed']))
                row=dict(strategy=track,arm=arm,period=label,**metrics,**acc,
                    retention_pct=float(100*len(rows)/len(t)) if len(t) else None,
                    matched_n=matched.get('pairs'),matched_signal_bp=matched.get('signal_bp'),
                    matched_random_bp=matched.get('random_bp'),matched_week_p=matched.get('week_sign_flip_p'))
                headline.append(row)
                details[track][arm+'_'+label]=dict(metrics=row,matched_random=matched,account_seeds=seeds,
                    unchanged_score_diagnostics=score_diagnostics(rows))
            details[track]['difference_'+label]=filter_difference(same,gates,cfg['seed'])
            details[track]['observable_baseline_'+label]=stats(same)
            details[track]['single_feature_ema3_'+label]=stats(t[t.order_score.ge(3)])
            if len(same)!=len(t):
                common,_,_=accounts(track,same,cfg,begin)
                details[track]['observable_baseline_'+label]['account']=common
        for (year,keep),rows in table.assign(year=table.cohort_time.str[:4]).groupby(['year','admitted']):
            annual.append(dict(strategy=track,year=year,harmonic=bool(keep),**stats(rows)))
    out=dict(config=cfg,scan=json.loads((root/'scan.json').read_text()),harmonic_validation=json.loads((root/'harmonic-validation.json').read_text()),native_counts={k:v for k,v in validation.items() if k!='sources'},
        baseline_exact=True,strategies=details,limitations=[
            'Original fill-bar score lookahead is deliberately preserved; only harmonic information is pre-touch.',
            f"Single frozen {cfg['harmonic']['interval']}/1000bar/10spacing/3percent Pyharmonics configuration; no outcome-tuned search.",
            'Historical selection study; no unseen forward proof. Same-size harmonic gating is not randomized.',
            'Native fills plus unchanged realized-equity/MAE capital overlays; no exchange margin, funding or mark-to-market model.',
            'Matched random-time controls are conditional on selected signal geometry and original score; unfilled attempts remain zero.',
            'No new probability model: AUC and top-decile diagnostics describe the unchanged original score only.'
        ],training_eligible=False,production_eligible=False)
    dump(root/'summary.json',clean(out));pd.DataFrame(headline).to_csv(root/'headline.csv',index=False)
    pd.DataFrame(annual).to_csv(root/'annual.csv',index=False)
    print(json.dumps(clean(dict(scan=out['scan'],native_counts=out['native_counts'],headline=headline))))


def monthly(config):
    from yoyo.evaluation.monday_limit_langlang import path_run
    cfg=json.loads(config.read_text());root=config.parent
    py=json.loads((ROOT/'experiments/active/exp-monday-limit-pyramid-20261027-v1/compare.json').read_text())
    gates=read_csv(ROOT/cfg['admission_events']).set_index('event');table=select_track(baseline(cfg),'allin3',cfg,True)
    table=table[table.event.map(gates.admitted).fillna(False)].copy()
    table['entry_t']=pd.to_datetime(table.entry_time,utc=True).astype('datetime64[ns, UTC]')
    table['entry_ns']=table.entry_t.astype('int64')
    table['exit_ns']=pd.to_datetime(table.exit_time,utc=True).astype('datetime64[ns, UTC]').astype('int64')
    table['entry']=table.entry_ns;table['exit']=table.exit_ns;rows=[]
    for k,start in enumerate(pd.date_range(py['first_start'],py['last_start'],freq='MS',tz='UTC')):
        t=table[table.entry_t>=start].reset_index(drop=True)
        for r in range(py['ties']):
            seed=py['seed']+1000*k+r;h=path_run(t,'allin',3,seed)
            rows.append(dict(start=str(start),seed=seed,
                **{f'd{q}':h[q]['days'] if h[q] else None for q in (10,100)},
                **{f'b{q}':h[q]['blowups'] if h[q] else None for q in (10,100)}))
    d=pd.DataFrame(rows)
    harmonic={f'x{q}':dict(reached_pct=float(100*d[f'd{q}'].notna().mean()),
        days_median=float(d[f'd{q}'].dropna().median()) if d[f'd{q}'].notna().any() else None,
        blowups_before_median=float(d[f'b{q}'].dropna().median()) if d[f'b{q}'].notna().any() else None) for q in (10,100)}
    old=json.loads((ROOT/cfg['baseline_comparison']/'allin_monthly.json').read_text())
    assert len(rows)==old['trials_per_arm']
    dump(root/'allin_monthly.json',dict(baseline=old['arms']['baseline'],harmonic=harmonic,trials_per_arm=len(rows),
        trials=rows,baseline_reused=True,accounting='Original monthly starts and external replenishments; goal reach is not ROI.'))


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['validate','audit','summary','monthly'])
    ap.add_argument('--config',type=Path,default=EXP/'config.json');args=ap.parse_args()
    if args.action=='summary':summarize(args.config.resolve())
    elif args.action=='monthly':monthly(args.config.resolve())
    elif args.action=='audit':validate_outputs(args.config.resolve())
    else:validate_candidates(args.config.resolve())
