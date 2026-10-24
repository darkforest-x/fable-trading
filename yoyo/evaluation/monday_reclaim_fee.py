"""One-factor fee-protected reclaim comparison against immutable native ledgers.

Entry plans and random controls are reused, not regenerated. Trades which never
reached reclaim cannot be affected and retain their saved native fills. Only
reclaimed trades need new Nautilus execution. A tighter stop cannot exit later
than the original entry-price stop with the same target, so the union of their
original submission-to-exit intervals is an exact replay envelope. Every source
bar inside each interval is retained; no price is interpolated. The original
exit is only a computation bound, never an admission or protection feature.
Unexpected unresolved or later exits fail validation, not silently get scored.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_limit_reclaim as baseline
from yoyo.evaluation import monday_limit_reclaim_analysis as analysis
from yoyo.evaluation.monday_limit_reclaim_validate import read
from yoyo.evaluation.monday_ma6_retest import finite
from yoyo.evaluation.monday_partial_snapshot import describe, moments
from yoyo.evaluation.range_framework import ROOT, dump, file_stamp

EXP = ROOT/'experiments/active/exp-monday-reclaim-fee-be-20261024-v1'
CODE = [
    'yoyo/evaluation/monday_reclaim_fee.py',
    'yoyo/layers/l3_backtest/nautilus_reclaim_fee.py',
    *baseline.CODE,
]
INDEX_COLUMNS = ['entry_i','exit_i','end_i','expire_i',
                 'be_trigger_i','be_effective_i','reclaim_i',
                 'arm_i','fee_be_effective_i']


def replay_envelope(frame, orders, outcomes):
    """Keep complete active intervals, with exact original-index provenance.

This optimization is valid only for this fixed, monotone stop tightening. It
must not be reused for wider stops, changed entries/targets, or price-dependent
features requiring omitted history. Features here are NaN ATR / disabled hour.
"""
    ends = outcomes.set_index('trade_id').exit_i
    if not orders.trade_id.is_unique or set(orders.trade_id) != set(ends.index):
        raise ValueError('Replay envelope requires exactly the affected filled orders')
    delta = np.zeros(len(frame)+1, dtype=np.int32)
    end = orders.trade_id.map(ends).to_numpy(int)
    start = orders.entry_i.to_numpy(int)
    if (end <= start).any():
        raise ValueError('Invalid native interval')
    np.add.at(delta, start, 1)
    np.add.at(delta, end+1, -1)
    original = np.flatnonzero(np.cumsum(delta[:-1]) > 0)
    reverse = np.full(len(frame), -1, dtype=np.int64)
    reverse[original] = np.arange(len(original))
    result = orders.copy()
    # Actual fill is unchanged; no need to keep an idle order alive after its
    # proven latest possible exit. Native matching must reproduce that fill.
    result['end_i'] = end
    result['expire_i'] = np.minimum(result.expire_i, end)
    for column in INDEX_COLUMNS:
        if column in result:
            v = result[column].to_numpy(int)
            good = v >= 0
            if (reverse[v[good]] < 0).any():
                raise ValueError(f'Index outside retained interval: {column}')
            result.loc[good,column] = reverse[v[good]]
    return frame.iloc[original].copy(), result, original


def restore_indices(rows, fills, original):
    rows, fills = rows.copy(), fills.copy()
    for column in INDEX_COLUMNS:
        if column in rows:
            good = rows[column].notna() & rows[column].ge(0)
            rows.loc[good,column] = original[rows.loc[good,column].astype(int)]
    if len(rows):
        rows['steps'] = rows.exit_i-rows.entry_i
    if len(fills):
        fills['bar_i'] = original[fills.bar_i.to_numpy(int)]
    return rows, fills


def validate_pair(old, new, fills, audit):
    """Reconcile actual fills and enforce the monotone replay-bound contract."""
    a, b = old.set_index('trade_id').sort_index(), new.set_index('trade_id').sort_index()
    pd.testing.assert_index_equal(a.index,b.index)
    for col in ['entry_i','entry','quantity','reclaim_minutes','exit_mode','population']:
        pd.testing.assert_series_equal(a[col],b[col],check_dtype=False,check_names=False)
    assert (b.exit_i <= a.exit_i).all(), 'Tighter stop exited later than baseline'
    assert (~a.closed | b.closed).all(), 'Replay bound censored an originally closed trade'
    unaffected = ~a.reclaim_status.eq('reclaimed')
    np.testing.assert_array_equal(a.loc[unaffected,'return_bp'],b.loc[unaffected,'return_bp'])
    assert len(fills)==2*len(b) and fills.groupby('trade_id').size().eq(2).all()
    opening=fills[fills.phase.eq('entry')].set_index('trade_id').reindex(b.index)
    closing=fills[~fills.phase.eq('entry')].set_index('trade_id').reindex(b.index)
    np.testing.assert_allclose(b.entry,opening.price,rtol=1e-12,atol=1e-12)
    np.testing.assert_allclose(b.exit_price,closing.price,rtol=1e-12,atol=1e-12)
    expected=fills.price*fills.quantity*np.where(fills.phase.isin(['entry','target']),.0002,.0005)
    np.testing.assert_allclose(fills.commission,expected,atol=5.1e-9,rtol=1e-10)
    fees=fills.groupby('trade_id').commission.sum().reindex(b.index)/b.quantity/b.entry*10000
    np.testing.assert_allclose(b.fee_bp,fees,atol=1e-8)
    np.testing.assert_allclose(b.gross_bp,b.side*(b.exit_price-b.entry)/b.entry*10000,atol=1e-8)
    np.testing.assert_allclose(b.return_bp,b.gross_bp-b.fee_bp,atol=1e-8)
    assert set(b.reason).issubset({0,4,5,11})
    active=b.fee_be_effective_i.fillna(-1).ge(0)
    assert (b.loc[active,'fee_be_effective_i'] >= b.loc[active,'reclaim_i']).all()
    assert b.loc[active,'reclaim_status'].eq('reclaimed').all()
    assert audit['baseline_replays']==0
    return dict(passed=True,positions=len(b),fills=len(fills),
                reused_unchanged=int(unaffected.sum()),new_native_positions=int((~unaffected).sum()))


def process(symbol):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy
    from yoyo.layers.l3_backtest.nautilus_reclaim_fee import run_native_fee_reclaim
    cfg=json.loads((EXP/'config.json').read_text())
    source=baseline.EXP/'symbols'/symbol
    dest=EXP/'symbols'/symbol
    if (dest/'done.json').exists():
        return json.loads((dest/'done.json').read_text())
    if symbol=='XAUUSD':
        raise ValueError('Unknown gold costs cannot support fee-neutral protection')
    saved=json.loads((source/'identity.json').read_text())
    job=saved['job']; old_done=json.loads((source/'done.json').read_text())
    stamps={name:file_stamp(source/name) for name in ['done.json','identity.json','orders.csv.gz','trades.csv.gz','fills.csv.gz','audit.json']}
    current=[file_stamp(Path(p)) for p in job['paths']]
    if current!=saved['inputs']:
        raise RuntimeError('Historical source changed; do not silently compare different data')
    dest.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()
    old=read(source/'trades.csv.gz'); orders=read(source/'orders.csv.gz'); oldfills=read(source/'fills.csv.gz')
    affected=old[old.reclaim_status.eq('reclaimed')] if len(old) else old
    audit=dict(baseline_replays=0,source=str(source.relative_to(ROOT)),source_bars=old_done['rows'],
               positions=len(old),orders=len(orders),original_opportunities=old_done['opportunities'],
               reused_native_positions=len(old)-len(affected),replayed_positions=len(affected),
               compression='Full union of saved submission-to-baseline-exit intervals; tighter stops only',
               gold_excluded=True)
    new=old.copy(); fills=oldfills.copy()
    new['fee_be_effective_i']=-1;new['fee_be_deferred_closes']=0
    new['fee_variant_source']='reused_no_reclaim'
    if len(affected):
        f,quality=load_job(job)
        if len(f)!=old_done['rows'] or str(f.index[0])!=old_done['first'] or str(f.index[-1])!=old_done['last']:
            raise RuntimeError('Baseline source coverage mismatch')
        tick=json.loads((source/'audit.json').read_text())['protection_tick']
        frame=f[baseline.OHLC].round(max(0,int(round(-np.log10(tick)))))
        frame.index=frame.index+pd.Timedelta(minutes=job['execution_minutes'])
        selected=orders[orders.trade_id.isin(affected.trade_id)].copy()
        frame,selected,original=replay_envelope(frame,selected,affected)
        np.save(dest/'replay-source-indices.npy',original)
        policy=ExitPolicy(trail_arm_r=1e12,opposite_hour_exit=False,round_trip_cost=0,
            target_from_entry=True,intrabar_path=cfg['intrabar_path'],reclaim_next_bar=True,
            breakeven_includes_cost=False,fill_maker_fee=cfg['maker_fee'],fill_taker_fee=cfg['taker_fee'])
        rows,ff,native=run_native_fee_reclaim(frame,np.full(len(frame),np.nan),np.zeros(len(frame)),selected,policy,
            tick=tick,symbol=symbol.removesuffix('USDT'),execution_minutes=job['execution_minutes'],
            entry_order_mode='limit',batch_bars=cfg['streaming_batch_bars'])
        rows,ff=restore_indices(rows,ff,original)
        if set(rows.trade_id)!=set(affected.trade_id):
            raise RuntimeError('Original native entries were not reproduced')
        rows['closed']=rows.reason.ne(0);rows['gross_bp']=rows.gross_r*10000
        rows['fee_bp']=rows.fee_r*10000;rows['net_bp']=rows.net_notional_bp
        rows['return_bp']=rows.net_bp;rows['basis']='net'
        rows['entry_interval_open']=f.index[rows.entry_i.astype(int)].astype(str).to_numpy()
        rows['exit_interval_open']=f.index[rows.exit_i.astype(int)].astype(str).to_numpy()
        rows['holding_minutes_upper']=(pd.to_datetime(rows.framework_exit_time,utc=True)-pd.to_datetime(rows.entry_interval_open,utc=True)).dt.total_seconds()/60
        rows['replay_bound_i']=rows.end_i
        rows['end_i']=rows.trade_id.map(orders.set_index('trade_id').end_i)
        rows['expire_i']=rows.trade_id.map(orders.set_index('trade_id').expire_i)
        rows=baseline.label_candles(f,rows,job['execution_minutes'])
        rows=rows.drop(columns=['gross_r','fee_r','net_r','peak_r','net_notional_bp'],errors='ignore').rename(columns={'risk':'notional_normalizer'})
        rows['fee_variant_source']='native_fee_replay'
        new=pd.concat([new[~new.trade_id.isin(affected.trade_id)],rows],ignore_index=True)
        fills=pd.concat([oldfills[~oldfills.trade_id.isin(affected.trade_id)],ff],ignore_index=True)
        audit.update(replay_bars=len(frame),native=native)
    validation=validate_pair(old,new,fills,audit) if len(old) else dict(passed=True,positions=0,fills=0)
    for name,stamp in stamps.items():
        if file_stamp(source/name)!=stamp:raise RuntimeError('Baseline changed during comparison')
    new.to_csv(dest/'trades.csv.gz',index=False);fills.to_csv(dest/'fills.csv.gz',index=False)
    dump(dest/'audit.json',finite(audit));dump(dest/'validation.json',finite(validation))
    dump(dest/'identity.json',finite(dict(config=cfg,baseline_files=stamps,inputs=current,
        code=[file_stamp(ROOT/p) for p in CODE],job=job)))
    done=dict(old_done,positions=len(new),fills=len(fills),new_native_positions=len(affected),
              replay_bars=audit.get('replay_bars',0),seconds=time.perf_counter()-started)
    dump(dest/'done.json',finite(done))
    return done


def summarize():
    totals=defaultdict(lambda:defaultdict(float));coverage=[]
    for marker in sorted((EXP/'symbols').glob('*/done.json')):
        symbol=marker.parent.name;done=json.loads(marker.read_text());coverage.append(done)
        a=analysis.prepare_trades(read(baseline.EXP/'symbols'/symbol/'trades.csv.gz'))
        b=analysis.prepare_trades(read(marker.parent/'trades.csv.gz'))
        if a.empty:continue
        for variant,t in [('entry_price',a),('fee_inclusive',b)]:
            matches=analysis.build_strict_matches(t)
            for period in ['all','pre2023','post2023']:
                g=analysis.period_rows(t,period)
                for (minutes,target),cell in g.groupby(['reclaim_minutes','exit_mode']):
                    signal=cell[cell.population.eq('signal')];closed=signal[signal.closed]
                    pair=matches[matches.reclaim_minutes.eq(minutes)&matches.exit_mode.eq(target)]
                    if period!='all':pair=pair[pair.entry_period.eq(period)&~pair.cross_cut]
                    for group in [symbol.removesuffix('USDT') if symbol in ['BTCUSDT','ETHUSDT'] else 'ALTCOINS','CRYPTO_ALL']:
                        key=(variant,group,period,int(minutes),target);m=totals[key]
                        m['symbols']+=1;m['filled']+=len(signal);m['censored']+=int((~signal.closed).sum())
                        for k,v in moments(closed.return_bp).items():m[k]+=v
                        m['tp_n']+=int(closed.reason.eq(4).sum());m['stop_n']+=int(closed.reason.eq(5).sum())
                        if variant=='fee_inclusive':
                            m['fee_be_activated']+=int(signal.fee_be_effective_i.fillna(-1).ge(0).sum())
                            m['fee_be_stop_n']+=int((closed.reason.eq(5)&closed.fee_be_effective_i.fillna(-1).ge(0)).sum())
                        m['matched_n']+=len(pair)
                        m['matched_signal_sum']+=pair.signal_return_bp.sum();m['matched_random_sum']+=pair.control_mean_return_bp.sum()
        both=a[['trade_id','return_bp','reason','closed','cross_cut']].merge(
            b[['trade_id','return_bp','reason','closed','cross_cut','population','entry_period','reclaim_minutes','exit_mode']],
            on='trade_id',suffixes=('_old','_new'),validate='one_to_one')
        both=both[both.population.eq('signal')&both.closed_old&both.closed_new]
        for period in ['all','pre2023','post2023']:
            sel=both if period=='all' else both[both.entry_period.eq(period)&~both.cross_cut_old&~both.cross_cut_new]
            for (minutes,target),cell in sel.groupby(['reclaim_minutes','exit_mode']):
                for group in [symbol.removesuffix('USDT') if symbol in ['BTCUSDT','ETHUSDT'] else 'ALTCOINS','CRYPTO_ALL']:
                    m=totals[('fee_inclusive',group,period,int(minutes),target)]
                    m['same_closed_n']+=len(cell)
                    m['delta_sum']+=(cell.return_bp_new-cell.return_bp_old).sum()
                    m['old_tp_now_stop']+=int((cell.reason_old.eq(4)&cell.reason_new.eq(5)).sum())
    rows=[]
    for (variant,group,period,minutes,target),m in sorted(totals.items()):
        s=describe(m);matched=m['matched_n'];n=s['n']
        rows.append(dict(variant=variant,group=group,period=period,confirm_minutes=minutes,target=target,
            symbols=int(m['symbols']),closed_n=n,censored_n=int(m['censored']),win_rate=s['win_rate'],
            mean_net_bp=s['mean'],payoff=s['payoff'],profit_factor=s['profit_factor'],
            tp_rate=m['tp_n']/n if n else None,fee_be_stop_n=int(m['fee_be_stop_n']),
            fee_be_activated=int(m['fee_be_activated']),old_tp_now_stop=int(m['old_tp_now_stop']),
            same_closed_n=int(m['same_closed_n']),paired_delta_bp=m['delta_sum']/m['same_closed_n'] if m['same_closed_n'] else None,
            matched_n=int(matched),matched_signal_bp=m['matched_signal_sum']/matched if matched else None,
            matched_random_bp=m['matched_random_sum']/matched if matched else None))
    pd.DataFrame(rows).to_csv(EXP/'metrics.csv',index=False)
    dump(EXP/'summary.json',finite(dict(completed_assets=len(coverage),expected_assets=632,
        status='complete' if len(coverage)==632 else 'partial',assets=coverage,metrics=rows,
        gold='Excluded from fee variant because costs are unknown; original gross results unchanged',
        win_definition='Exact positive net return; outward price rounding may count fee-BE exits as tiny wins. TP rate separately reported.',
        return_basis='Independent unlevered entry-notional return in bp; no initial R, no funded portfolio',
        random_scope='Same frozen complete1+3 matches within each variant; conditional subset',
        paired_scope='Paired policy difference requires same trade closed in both; split views exclude crossing either boundary',
        baseline_replays=0)))


def process_identity(pid):
    return subprocess.run(['ps','-p',str(pid),'-o','lstart=','-o','command='],capture_output=True,text=True).stdout.strip()


def main():
    p=argparse.ArgumentParser();p.add_argument('--symbols',nargs='*');p.add_argument('--workers',type=int,default=2)
    p.add_argument('--wait-pids',type=int,nargs='*',default=[]);p.add_argument('--analyze-only',action='store_true')
    args=p.parse_args();EXP.mkdir(parents=True,exist_ok=True)
    with (EXP/'native.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=dict(started_at=datetime.now(timezone.utc).isoformat(),pid=__import__('os').getpid(),status='preflight')
        def save():dump(EXP/'state.json',finite(state))
        try:
            if args.analyze_only:summarize();return
            for path in CODE+[str((EXP/'config.json').relative_to(ROOT))]:
                if subprocess.run(['git','diff','--quiet','HEAD','--',path],cwd=ROOT).returncode or subprocess.run(['git','ls-files','--error-unmatch',path],cwd=ROOT,capture_output=True).returncode:
                    raise RuntimeError(f'Commit source before execution: {path}')
            cfg=json.loads((EXP/'config.json').read_text())
            if not cfg['run_enabled']:raise RuntimeError('Run is not enabled')
            waits={pid:process_identity(pid) for pid in args.wait_pids}
            if any(v and 'yoyo.evaluation.monday_' not in v for v in waits.values()):
                raise RuntimeError('Refusing to wait on unrelated process')
            state.update(status='waiting_for_original_pools',waits=waits);save()
            while any(identity and process_identity(pid)==identity for pid,identity in waits.items()):time.sleep(30)
            symbols=args.symbols or sorted(p.parent.name for p in (baseline.EXP/'symbols').glob('*/done.json') if p.parent.name!='XAUUSD')
            if args.symbols is None and len(symbols)!=632:raise RuntimeError('Original crypto cohort incomplete')
            symbols=[s for s in symbols if not (EXP/'symbols'/s/'done.json').exists()]
            symbols.sort(key=lambda s:(s not in ['BTCUSDT','ETHUSDT'],s))
            state.update(status='native_running',pending=len(symbols),workers=args.workers);save()
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                jobs={pool.submit(process,s):s for s in symbols}
                for future in as_completed(jobs):
                    result=future.result();print(json.dumps(result),flush=True)
                    state.update(last_symbol=result['symbol'],completed_assets=len(list((EXP/'symbols').glob('*/done.json'))));save()
            summarize();state.update(status='complete' if len(list((EXP/'symbols').glob('*/done.json')))==632 else 'partial_complete');save()
        except BaseException as e:
            state.update(status='failed',error=repr(e));save();raise


if __name__=='__main__':main()
