"""Compare every saved booklet with entry at the completed touch5m close.

Source cohorts, score-based targets, protection offsets, sizing, starts and tie
seeds are frozen to the displayed ledger. Reuse saved native close fills. Only
the missing 5m BE0 and BE0 parent/child groups run in Nautilus; parent orders in
the latter are necessary native dependencies, not a second baseline replay.
Admission uses only the original Monday geometry and the completed touch close.
No new fit, parameter search, price download or production change is performed.
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
from yoyo.evaluation.monday_limit_paper_accounts import committed

A = ROOT/'experiments/active'
EXP = A/'exp-strategy-booklet-close-compare-20261029-v1'
BOOK = A/'exp-strategy-booklet-paths-20261027-v1'
PAPER = A/'exp-monday-limit-paper-accounts-20261028-v1'
BE0 = A/'exp-monday-limit-close-reclaim-20261028-v1'
KEEP = ['trade_id','side','entry','stop','target_price','exit_price','reason',
        'entry_time','exit_time','entry_i','exit_i','reclaim_status',
        'net_notional_bp','framework_commission','quantity','gross_r','net_r']


def cohorts():
    """Preserve source row order, scores and the separate 5m baseline start."""
    from yoyo.evaluation.strategy_booklet_accounting import load_baselines
    tables=load_baselines()
    starts=json.loads((BOOK/'manifest.json').read_text())['accounts']
    return {k:t[pd.to_datetime(t.entry_t,utc=True)>=pd.Timestamp(starts[k]['start'],tz='UTC')].reset_index(drop=True)
            for k,t in tables.items()}


def construct(p, original, tick, tables, symbol):
    """Only touch-close OHLC and frozen score/levels decide order admission."""
    sources = original[original.population.eq('signal')].set_index('trade_id')
    orders, decisions = [], []
    for kind, track, minutes, offset in [('60','60',60,17.), ('5','5',5,0.),
                                        ('v3','v3',60,17.), ('v3lock','v3lock',60,50.),
                                        ('v3be0','v3',60,0.)]:
        table = tables[track]
        table = table[table.symbol.eq(symbol)]
        for r in table.itertuples():
            mode = 'near_monday_edge' if track in ('60','5') else ('opposite_1618' if r.score >= .6 else 'range_0.618')
            source_id = f'{r.event}:m{minutes}:{mode}:c0'
            src = sources.loc[source_id].to_dict()
            k, side = int(src['touch_i_hint']), int(src['side'])
            close, level, target = float(p.close.iloc[k]), float(src['entry']), float(src['target_price'])
            src.update(trade_id=f'{r.event}:{kind}', event=r.event, kind=kind,
                       entry_i=k, reclaim_ref=level, entry=close, risk=close,
                       be_offset_bp=offset, order_mode='market', parent=None,
                       score=float(r.score), source_id=source_id)
            src['stop'] = float(stops(pd.DataFrame([dict(src,entry=level)]),
                                     'far' if kind in ('60','5') else 'P',30,tick)[0])
            status = 'admitted'
            if np.isfinite(target) and side*(target-close) < tick*(1-1e-7):
                status = 'past_target_at_close'
            elif k >= int(src['end_i']):
                status = 'source_ended_at_close'
            elif kind != '5' and side*(close-src['stop']) <= 0:
                status = 'past_backstop_at_close'
            decisions.append(dict(event=r.event,kind=kind,score=r.score,status=status))
            if status != 'admitted':
                continue
            # The original 5m booklet has no initial stop; its placeholder is
            # ignored by the separate reclaim_backstop=False native policy.
            src['cache_family'] = 'ci' if side*(close-level) >= tick*(1-1e-7) else 'cb'
            orders.append(src)
            if kind == 'v3be0' and r.score >= .6:
                raw = level*(1-side*.05)
                add = (np.floor(raw/tick+1e-7) if side == 1 else np.ceil(raw/tick-1e-7))*tick
                if side*(close-add) >= tick*(1-1e-7) and side*(add-src['stop']) > 0:
                    orders.append(dict(src,trade_id=src['trade_id']+'_add',kind='add0',
                                       parent=src['trade_id'],entry=add,risk=add,order_mode='limit'))
    return pd.DataFrame(orders),pd.DataFrame(decisions)


def cached_rows(x, symbol, path, tick):
    """Reuse only unchanged semantic families; check every intended price/index."""
    if x.empty:
        return pd.DataFrame(columns=KEEP)
    paper = pd.read_csv(PAPER/path/'symbols'/symbol/'trades.csv.gz').set_index('trade_id') if x.kind.ne('v3be0').any() else None
    be0 = pd.read_csv(BE0/path/'symbols'/symbol/'trades.csv.gz').set_index('trade_id') if x.kind.eq('v3be0').any() else None
    rows = []
    for r in x.to_dict('records'):
        fam = r['cache_family']
        if r['kind'] == 'v3be0':
            code = 'P30' if fam == 'ci' else 'B30'
            old = be0.loc[r['source_id']+':v_'+code].to_dict()
        else:
            name = 'v2' if r['kind'] == '60' else r['kind']
            old = paper.loc[r['source_id']+f':v_{fam}_{name}'].to_dict()
        for field in ('side','entry','entry_i','stop','target_price'):
            np.testing.assert_allclose(old[field],r[field],atol=tick*1e-6,rtol=1e-12,equal_nan=True)
        rows.append(dict(old,trade_id=r['trade_id']))
    return pd.DataFrame(rows)


def process(symbol, config):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native
    cfg = json.loads(Path(config).read_text()); out = Path(config).parent/'symbols'/symbol
    if (out/'done.json').exists():
        return json.loads((out/'done.json').read_text())
    started=time.perf_counter(); out.mkdir(parents=True,exist_ok=True)
    origin=CODEX/'symbols'/symbol
    job=json.loads((origin/'identity.json').read_text())['job']
    if job['group'] != 'crypto_5m':
        raise ValueError('Unexpected source group')
    f,_=load_job(job); prior=json.loads((origin/'done.json').read_text())
    if (len(f),str(f.index[0]),str(f.index[-1])) != (prior['rows'],prior['first'],prior['last']):
        raise ValueError('Source changed')
    cols=['open','high','low','close']
    decimals=max(2,min(9,int(np.ceil(-np.log10(f[cols].to_numpy().min())))+7))
    tick=10.**(-decimals); p=f[cols].round(decimals); p.index=p.index.as_unit('ns')
    original=pd.read_csv(origin/'orders.csv.gz')
    x,decisions=construct(p,original,tick,cohorts(),symbol)
    decisions.to_csv(out/'decisions.csv.gz',index=False)
    x.to_csv(out/'orders.csv.gz',index=False)
    if x.empty:
        done=dict(symbol=symbol,orders=0,paths={});dump(out/'done.json',done);return done
    frame=p.copy();frame.index+=pd.Timedelta(minutes=5)
    # A child needs its actual parent in the native engine. Run only those
    # joint BE0 groups; standalone parents keep their existing native fills.
    dependencies=set(x.loc[x.kind.eq('add0'),'parent'])
    new5=x[x.kind.eq('5')]
    newadd=x[x.kind.eq('add0')|x.trade_id.isin(dependencies)]
    reused=x[~x.trade_id.isin(set(new5.trade_id)|set(newadd.trade_id))]
    paths={}
    for path in ('ohlc','olhc'):
        dest=out/path;dest.mkdir(exist_ok=True)
        if (dest/'audit.json').exists():
            paths[path]=json.loads((dest/'audit.json').read_text());continue
        old=cached_rows(reused,symbol,path,tick)
        pieces=[old]; receipts=[]
        for group,runtime,backstop in [('five',new5,False),('add',newadd,True)]:
            if runtime.empty:
                continue
            policy=ExitPolicy(trail_arm_r=1e12,opposite_hour_exit=False,round_trip_cost=0,
                target_from_entry=True,intrabar_path=path.upper(),reclaim_next_bar=True,
                breakeven_includes_cost=False,fill_maker_fee=cfg['maker_fee'],
                fill_taker_fee=cfg['taker_fee'],monotone_segments=True,reclaim_backstop=backstop)
            rows,fills,audit=run_native(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),
                runtime.copy(),policy,tick=tick,symbol=symbol.removesuffix('USDT'),
                entry_order_mode='limit',batch_bars=288,skip_idle_bars=True)
            rows.to_csv(dest/f'{group}-native-trades.csv.gz',index=False)
            fills.to_csv(dest/f'{group}-native-fills.csv.gz',index=False)
            dump(dest/f'{group}-native-audit.json',audit)
            if group == 'add':
                expected=cached_rows(runtime[runtime.kind.eq('v3be0')],symbol,path,tick).set_index('trade_id')
                actual=rows.set_index('trade_id').reindex(expected.index)
                for field in ('entry','exit_price','entry_i','exit_i','reason','net_notional_bp'):
                    np.testing.assert_allclose(actual[field],expected[field],atol=1e-8,rtol=1e-10)
            pieces.append(rows)
            receipts.append(dict(group=group,orders=len(runtime),positions=audit['positions'],fills=audit['fills']))
        combined=pd.concat([q.reindex(columns=KEEP) for q in pieces if len(q)],ignore_index=True)
        combined=combined.merge(x[['trade_id','event','kind','order_mode','score']],on='trade_id',validate='one_to_one')
        combined['entry_t']=[str(frame.index[int(i)]) for i in combined.entry_i]
        combined['exit_t']=combined.exit_time
        combined['symbol']=symbol
        h,lo=p.high.to_numpy(),p.low.to_numpy();mae=[]
        for r in combined.itertuples():
            a=int(r.entry_i)+(r.order_mode=='market');b=max(a+1,int(r.exit_i))
            worst=lo[a:b].min() if r.side==1 else h[a:b].max()
            mae.append(max(0.,-r.side*(worst-r.entry)/r.entry*100))
        combined['mae_pct']=mae
        combined['net']=combined.net_notional_bp-np.where(combined.order_mode.eq('market'),10.,0.)-np.where(combined.reason.eq(4),0.,10.)
        combined.to_csv(dest/'trades.csv.gz',index=False)
        receipt=dict(reused=len(old),new_groups=receipts,parent_parity_checks=len(dependencies),positions=len(combined))
        dump(dest/'audit.json',receipt);paths[path]=receipt
    done=dict(symbol=symbol,orders=len(x),paths=paths,seconds=time.perf_counter()-started,
              sources=[file_stamp(ROOT/Path(v)) for v in job['paths']])
    dump(out/'done.json',done);return done


def run(config, workers, symbol=None):
    committed('yoyo/evaluation/strategy_booklet_close_compare.py','yoyo/evaluation/strategy_booklet_accounting.py',
              'yoyo/layers/l3_backtest/nautilus_independent.py',str(config.relative_to(ROOT)))
    tables=cohorts()
    symbols=sorted(set(pd.concat([tables[k] for k in ('60','5','v3')]).symbol))
    if symbol:
        symbols=[symbol]
    with ProcessPoolExecutor(workers) as pool:
        jobs={pool.submit(process,s,str(config)):s for s in symbols}
        for i,task in enumerate(as_completed(jobs),1):
            try:
                result=task.result()
            except Exception as error:
                result=dict(symbol=jobs[task],error=repr(error));d=config.parent/'symbols'/jobs[task]
                d.mkdir(parents=True,exist_ok=True);dump(d/'error.json',result)
            print(json.dumps(dict(progress=i,total=len(symbols),**result)),flush=True)


def summarize(config):
    from yoyo.evaluation.strategy_booklet_accounting import account, validate_baselines
    baselines=cohorts(); verification=validate_baselines()
    dirs=sorted((config.parent/'symbols').iterdir())
    expected=set(pd.concat([baselines[k] for k in ('60','5','v3')]).symbol)
    assert set(d.name for d in dirs if (d/'done.json').exists())==expected
    tables={p:pd.concat([pd.read_csv(d/p/'trades.csv.gz') for d in dirs if (d/p/'trades.csv.gz').exists()],ignore_index=True).set_index('trade_id') for p in ('ohlc','olhc')}
    o,l=tables['ohlc'],tables['olhc']
    # Preserve the historical per-row worse-path convention, including its
    # OHLC add-fill universe. Path-only children are separately counted.
    l=l.reindex(o.index);w=l.net.lt(o.net)&l.net.notna()
    worse=o.copy();worse.loc[w]=l.loc[w]
    worse['chosen_path']=np.where(w,'olhc','ohlc');worse=worse.reset_index()
    worse.to_csv(config.parent/'native-close-projection.csv.gz',index=False)
    result={};decisions=pd.concat([pd.read_csv(d/'decisions.csv.gz') for d in dirs],ignore_index=True)
    mapping={'60':['60'],'5':['5'],'v3':['v3'],'v3lock':['v3lock'],
             'v3add':['v3be0','add0'],'v3add20':['v3be0','add0'],'allin3':['v3be0']}
    for track,kinds in mapping.items():
        base=baselines[track];new=worse[worse.kind.isin(kinds)].copy()
        base_events=set(base.event);new=new[new.event.isin(base_events)]
        # Keep native source-symbol/variant/event ordering from the original
        # display, instead of inadvertently drawing a different seeded tie.
        ranks={e:i for i,e in enumerate(dict.fromkeys(base.event))}
        new['source_rank']=new.event.map(ranks)
        if track in ('v3add','v3add20'):
            new['source_rank']=[int(base.index[(base.event==r.event)&(base.variant==('v3_add5' if r.kind=='add0' else 'v3_be0'))][0])
                               if ((base.event==r.event)&(base.variant==('v3_add5' if r.kind=='add0' else 'v3_be0'))).any()
                               else ranks[r.event]+.5 for r in new.itertuples()]
        new=new.sort_values('source_rank',kind='stable').reset_index(drop=True)
        old_metrics,old_ledger=account(track,base);new_metrics,new_ledger=account(track,new)
        old_ledger.to_csv(config.parent/f'{track}-baseline-ledger.csv.gz',index=False)
        new_ledger.to_csv(config.parent/f'{track}-close-ledger.csv.gz',index=False)
        primary='v3be0' if track in ('v3add','v3add20','allin3') else kinds[0]
        admission=decisions[decisions.kind.eq(primary)&decisions.event.isin(base_events)]
        result[track]=dict(baseline=old_metrics,close_now=new_metrics,
                          admission={str(k):int(v) for k,v in admission.status.value_counts().items()})
    result['spike']=json.loads((A/'exp-spike-booklet-close-compare-20261029-v1/summary.json').read_text())
    dump(config.parent/'summary.json',dict(config=json.loads(config.read_text()),strategies=result,
        validation=dict(original_ledgers=verification,symbols=len(dirs),
            path_only_adds=dict(ohlc_only=len(set(tables['ohlc'].index)-set(tables['olhc'].index)),
                               olhc_only=len(set(tables['olhc'].index)-set(tables['ohlc'].index)))),
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        training_eligible=False,production_eligible=False))
    for track,v in result.items():
        if track!='spike':
            print(track,json.dumps(dict(baseline=v['baseline'],close=v['close_now'])),flush=True)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--config',type=Path,default=EXP/'config.json')
    ap.add_argument('--workers',type=int,default=4);ap.add_argument('--symbol');ap.add_argument('--summary',action='store_true')
    args=ap.parse_args();config=args.config.resolve()
    summarize(config) if args.summary else run(config,args.workers,args.symbol)
