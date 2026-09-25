"""Five-year SPIKE manual-decision study using the committed V12.6/V12.8 port.

Source: owner's 2026-09-25 request for BTC/ETH 5m/15m/1h, every trade,
entry selection and whether to exit at 4R. No model fitting or live changes.
The two single-variable treatments are frozen before construction in config:
completed-HTF SMA60 slope admission, and a resting 4R exit. Ordinary both-side
signals and long-only joint signals remain separate, overlapping ledgers.
Features read current/prior chart OHLCV; HTF features end before chart OPEN.
Actual entries use the next open; a Pine confirmation-close R is not a fill.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_recent as p
from yoyo.evaluation import spike_lowtf_v1_v126 as low
from yoyo.evaluation import spike_v128_recent_report as stats
from yoyo.evaluation.spike_v128_long_replay import merge_history
from yoyo.evaluation.spike_v104_forward import fetch_rest
from yoyo.evaluation.spike_v9_htf_sma import confirmed_sma
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed

EXP = Path('experiments/active/exp-spike-manual-system-20260925-v1')
CONFIG = EXP / 'config.json'
HTF = {5: 15, 15: 60, 60: 240}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def sources():
    from yoyo.evaluation import spike_manual_four_r
    paths = tuple(dict.fromkeys([CONFIG, Path(__file__), Path(spike_manual_four_r.__file__),
        p.PINE, p.PINE_V128, Path('tests/evaluation/test_spike_manual_system_study.py'),
        Path('tests/evaluation/test_spike_manual_four_r.py'),
        *_local_transitive_python((Path(__file__), Path(spike_manual_four_r.__file__)))]))
    if not _committed(paths):
        raise ValueError('Commit every builder, test, config and imported source before construction')
    return {str(x): p.digest(x) for x in paths}


def prepare_data(cfg):
    """Verify the previous same-venue archive, append only official closed 5m rows."""
    code = sources()
    manifest = json.loads(Path(cfg['input_manifest']).read_text())
    if not manifest['complete'] or manifest['failed']:
        raise ValueError('Previous input incomplete')
    root = Path(cfg['data_dir']); root.mkdir(parents=True, exist_ok=True)
    receipts = []
    for row in manifest['streams']:
        symbol = row['symbol']
        if symbol not in cfg['symbols']:
            continue
        receipt = root / f'{symbol}.json'
        if receipt.exists():
            saved = json.loads(receipt.read_text())
            if saved['config_hash'] != p.digest(CONFIG) or p.digest(Path(saved['path'])) != saved['sha256']:
                raise ValueError('Existing research data changed')
            receipts.append(saved); continue
        old_path = Path(row['path'])
        if p.digest(old_path) != row['sha256']:
            raise ValueError('Old archive hash changed')
        old = pd.read_csv(old_path)
        start = pd.Timestamp(int(old.ts.iloc[-1]), unit='ms', tz='UTC')
        tail = fetch_rest(symbol, start, pd.Timestamp(cfg['end']))
        if tail is None or tail.empty:
            raise ValueError(f'Official same-source tail unavailable: {symbol}')
        tail_path = root / f'{symbol}_tail.csv'
        tail.to_csv(tail_path, index=False)
        merged, overlap = merge_history(old, tail, cfg)
        expected_last = pd.Timestamp(cfg['end']).value // 10**6 - 300000
        if int(merged.ts.iloc[-1]) != expected_last:
            raise ValueError('Tail does not reach requested closed endpoint')
        if not merged.ts.diff().dropna().eq(300000).all():
            raise ValueError('Missing 5m bars; no synthetic fill permitted')
        bars = merged[['open','high','low','close','volume']]
        if not (np.isfinite(bars).all().all() and bars.low.gt(0).all() and bars.volume.ge(0).all()
                and bars.high.ge(bars[['open','close','low']].max(axis=1)).all()
                and bars.low.le(bars[['open','close','high']].min(axis=1)).all()):
            raise ValueError('Invalid market OHLCV')
        path = root / f'{symbol}.csv.gz'
        merged.to_csv(path, index=False, compression={'method':'gzip','mtime':0})
        saved = {'symbol':symbol,'path':str(path),'sha256':p.digest(path),'rows':len(merged),
            'first_ms':int(merged.ts.iloc[0]),'last_ms':int(merged.ts.iloc[-1]),'gap_count':0,
            'overlap_rows':overlap,'config_hash':p.digest(CONFIG),'code':code,
            'inputs':{str(old_path):row['sha256'],str(tail_path):p.digest(tail_path)},
            'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
        p.dump(receipt,saved); receipts.append(saved)
        print(json.dumps({'data':symbol,'rows':len(merged),'gaps':0}),flush=True)
    if {r['symbol'] for r in receipts} != set(cfg['symbols']):
        raise ValueError('Missing required symbol')
    p.dump(root/'manifest.json',{'streams':receipts,'config_hash':p.digest(CONFIG)})


def higher_features(base, chart_index, minutes):
    """Read latest two completed HTF SMA60 values, never the developing candle."""
    htf = HTF[minutes]
    current = confirmed_sma(base, chart_index, htf, (60,))
    prior = confirmed_sma(base, chart_index-pd.Timedelta(minutes=htf), htf, (60,))
    result = pd.DataFrame(index=chart_index)
    result['htf_sma60'] = current.sma_60.to_numpy()
    result['htf_slope'] = current.sma_60.to_numpy()-prior.sma_60.to_numpy()
    result['htf_close_time'] = current.htf_close_time
    known = current.htf_close_time.dropna()
    if not (known <= known.index).all():
        raise ValueError('Future higher timeframe value')
    return result


def choose_serial(candidates, outcomes, policy, frame_length):
    """Replay occupancy independently for each admission/exit policy, warmup included."""
    trades, statuses, flat_from, blocker = [], [], -1, None
    for event in candidates:
        i, side = int(event['signal_i']), int(event['side'])
        status, result = outcomes[(i, side, 'take_4r' if policy == 'take_4r' else 'baseline')]
        common = dict(event, policy=policy)
        if policy == 'htf_slope' and not event['htf_slope_aligned']:
            if event['in_window']: statuses.append(dict(common,status='htf_slope_rejected',blocking_trade=None))
            continue
        if i < flat_from:
            if event['in_window']: statuses.append(dict(common,status='skipped_in_position',blocking_trade=blocker))
            continue
        if event['in_window']: statuses.append(dict(common,status=status,blocking_trade=None))
        if result is None:
            continue
        key = f"{event['symbol']}:{event['timeframe_min']}:{event['arm']}:{policy}:{event['signal_close']}"
        row = dict(common, **{k:result.get(k) for k in p.source.TRADE_KEEP if k not in common})
        row.update({k:v for k,v in result.items() if k.startswith(('mfe_','close_peak','stop_bar','four_r','first_'))})
        row.update(trade_key=key,status=status)
        if event['in_window']: trades.append(row)
        blocker = key
        flat_from = frame_length+1 if status=='censored_boundary' else int(result['exit_i'])
    return trades, statuses


def controls(prepared, trades, hf, cfg, evaluate):
    """One paired random entry from same symbol/side/week/fold/causal ATR bucket.

    The slope treatment's controls also satisfy its slope gate. The random
    controls are entry-quality comparators, not an investable random portfolio.
    """
    frame = prepared.frame; minutes = prepared.context.minutes
    clock = frame.index + pd.Timedelta(minutes=minutes)
    week = np.asarray(clock.strftime('%G-W%V'))
    fold = np.where(clock < pd.Timestamp(cfg['split']),'earlier','later')
    vb = np.searchsorted(np.asarray(cfg['vol_bins']),prepared.atr/prepared.close,side='left')
    valid = (frame.ready.to_numpy(bool) & ~prepared.gap & np.isfinite(prepared.atr)
        & (prepared.atr>0) & (clock>=pd.Timestamp(cfg['start'])) & (clock<pd.Timestamp(cfg['end'])))
    pools, records = {}, []
    for tr in trades:
        i, side, policy = int(tr['signal_i']),int(tr['side']),tr['policy']
        key = (side,policy,week[i],fold[i],int(vb[i]))
        if key not in pools:
            mask = valid & (week==week[i]) & (fold==fold[i]) & (vb==vb[i])
            if policy=='htf_slope': mask &= side*hf.htf_slope.to_numpy()>0
            pools[key]=np.flatnonzero(mask)
        possible=pools[key][pools[key]!=i]
        picked=None if not len(possible) else int(possible[int(hashlib.sha256(f"{cfg['control_seed']}|{tr['trade_key']}".encode()).hexdigest(),16)%len(possible)])
        status,result=('empty_stratum',None) if picked is None else evaluate(picked,side,'take_4r' if policy=='take_4r' else 'baseline')
        match=result is not None and status=='closed' and not tr['censored']
        records.append({'trade_key':tr['trade_key'],'symbol':tr['symbol'],'timeframe_min':minutes,
            'arm':tr['arm'],'policy':policy,'side':side,'utc_week':week[i],'matched':match,
            'status':status,'control_signal_close':None if picked is None else clock[picked],
            'control_exit_time':None if result is None else result['exit_time'],
            'target_net_bp':10000*tr['net_return'],'control_net_bp':None if not match else 10000*result['net_return'],
            'control_net_r':None if not match else result['net_r'],
            'excess_bp':None if not match else 10000*(tr['net_return']-result['net_return'])})
    return pd.DataFrame(records)


def worker(task):
    from yoyo.evaluation.spike_manual_four_r import replay_four_r, four_r_checkpoints
    symbol, minutes, cfg, data, rid = task
    folder=EXP/'run_v1'/'streams'/f'{symbol}_{minutes}m';folder.mkdir(parents=True,exist_ok=True)
    receipt=folder/'receipt.json'
    if receipt.exists():
        old=json.loads(receipt.read_text())
        if old['run_identity']!=rid: raise ValueError('Run identity changed')
        for name,sha in old['files'].items():
            if p.digest(folder/name)!=sha: raise ValueError('Output hash drift')
        return old
    began=time.monotonic()
    if p.digest(Path(data['path']))!=data['sha256']: raise ValueError('Data hash drift')
    raw=pd.read_csv(data['path']);base=raw.set_index(pd.to_datetime(raw.ts,unit='ms',utc=True))[['open','high','low','close','volume']]
    bars,partial=p.complete_bars(base,minutes)
    meta=p.source.symbol_meta()[symbol];tick=float(meta['tick'])
    facts=low.pine_facts(bars,base,meta['asset'],tick,5) if minutes==5 else p.facts_for(bars,base,meta['asset'],tick,minutes)
    frame=facts['frame'];hf=higher_features(base,frame.index,minutes)
    # Inherited line geometry is diagnostic and creates the separate joint arm.
    local=p.line_events(frame.open,frame.high,frame.low,frame.close,frame.atr,can_run=facts['can_run'],gap=facts['gap'],tick=tick,
        confirmed_long=facts['v9_long'],parent_high=facts['parent_high'],parent_low=facts['parent_low'],
        raw_side=facts['side'],long_alive=facts['long_alive'],ref_long_exit=facts['ref_long_exit'])
    higher,_=p.complete_bars(base,HTF[minutes]);hframe=p.features(higher);hg=p._data_gap(hframe,HTF[minutes]).to_numpy(bool)
    hl=p.line_events(hframe.open,hframe.high,hframe.low,hframe.close,hframe.atr,can_run=~hg&hframe.atr.gt(0).to_numpy(),gap=hg,tick=tick,htf=True)
    mapped=p._map_htf(hframe,frame,hl.winner_events,minutes,HTF[minutes])
    joints=p.pair_events(frame.close.to_numpy(),bar_times=frame.index.asi8//60000000000,chart_breaks=local.events,
        htf_breaks=mapped,box_id=facts['box']['box_entry'],confirmed_long=facts['v9_long'],gap=facts['gap'])
    identity={'venue':cfg['venue'],'symbol':symbol,'asset':meta['asset'],'timeframe_min':minutes}
    prepared=p.source.prepared_arm(frame,facts['gap'],facts['side'],f'{symbol}_{minutes}',identity,minutes,tick)
    cache={}
    def evaluate(i,side,policy):
        key=(int(i),int(side),policy)
        if key in cache:return cache[key]
        if policy=='baseline':
            status,result=p.attempt(prepared,i,side)
        else:
            status,original=evaluate(i,side,'baseline')
            if original is None:result=None
            else:
                result=replay_four_r(prepared,pd.Series(original))
                status='closed' if not result['censored'] else 'censored_boundary' if result['exit_reason']=='boundary_mark' else 'censored_gap'
        cache[key]=(status,result)
        return cache[key]
    family={'ordinary':[(int(i),int(facts['side'][i])) for i in np.flatnonzero(facts['v9'])],
            'joint':[(int(j['joint_i']),1) for j in joints]}
    all_trades,all_status,all_events,paired=[],[],[],[]
    for arm,points in family.items():
        events=[]
        for i,side in points:
            clock=frame.index[i]+pd.Timedelta(minutes=minutes)
            if clock>=pd.Timestamp(cfg['end']):continue
            event=dict(identity,arm=arm,signal_i=i,side=side,signal_bar_open=frame.index[i],signal_close=clock,
                in_window=clock>=pd.Timestamp(cfg['start']),htf_sma60=float(hf.htf_sma60.iloc[i]),
                htf_slope=float(hf.htf_slope.iloc[i]),htf_slope_aligned=bool(side*hf.htf_slope.iloc[i]>0),
                htf_close_time=hf.htf_close_time.iloc[i],signal_atr_pct=float(frame.atr.iloc[i]/frame.close.iloc[i]),
                rv=float(frame.rv.iloc[i]),momentum=float(side*(frame.md.iloc[i]-frame.sb.iloc[i])/frame.atr.iloc[i]),
                signal_price=float(frame.close.iloc[i]))
            events.append(event)
            evaluate(i,side,'baseline');evaluate(i,side,'take_4r')
        all_events.extend(e for e in events if e['in_window'])
        for policy in cfg['policies']:
            trades,statuses=choose_serial(events,cache,policy,len(frame))
            if policy=='baseline':
                oracle,_=p.serial(prepared,events,arm,f'{symbol}_{minutes}')
                fields=['signal_i','entry_price','initial_stop','exit_i','exit_price','censored']
                pd.testing.assert_frame_equal(pd.DataFrame(trades)[fields].reset_index(drop=True),pd.DataFrame(oracle)[fields].reset_index(drop=True),check_dtype=False)
                for row in trades:
                    original=evaluate(row['signal_i'],row['side'],'baseline')[1]
                    check=four_r_checkpoints(prepared,original)
                    row.update(check)
                    alt=evaluate(row['signal_i'],row['side'],'take_4r')[1]
                    paired.append(dict(trade_key=row['trade_key'],symbol=symbol,timeframe_min=minutes,arm=arm,
                        entry_time=row['entry_time'],exit_time=row['exit_time'],censored=row['censored'],
                        baseline_net_r=row['net_r'],take4_net_r=alt['net_r'],take4_exit_time=alt['exit_time'],take4_censored=alt['censored'],
                        baseline_net_bp=10000*row['net_return'],take4_net_bp=10000*alt['net_return'],
                        mfe_known_r=row['mfe_known_r'],**check))
            all_trades.extend(trades);all_status.extend(statuses)
    tt=pd.DataFrame(all_trades);cc=controls(prepared,all_trades,hf,cfg,evaluate)
    closed=tt.loc[~tt.censored]
    np.testing.assert_allclose(closed.gross_return-closed.net_return,.002,atol=1e-12)
    np.testing.assert_allclose(closed.net_r,closed.net_return/closed.initial_risk_frac,atol=1e-10)
    if tt.trade_key.duplicated().any():raise ValueError('Duplicate trade identity')
    tt['fee_r']=.002/tt.initial_risk_frac
    tt['entry_context']=np.where(tt.htf_slope_aligned,'上级SMA60斜率同向','上级SMA60斜率不同向或未知')
    tt['review_label']=np.select([tt.censored,tt.mfe_known_r.ge(4)&tt.net_r.lt(0),tt.mfe_known_r.ge(4)&tt.net_r.lt(4-tt.fee_r),tt.net_r.ge(4-tt.fee_r),tt.net_r.gt(0)],
        ['截止未结束','已知浮盈达4R后净亏','已知浮盈达4R但兑现较低','原退出保留4R及以上','盈利但未兑现4R'],default='亏损')
    tt['entry_time_beijing']=pd.to_datetime(tt.entry_time,utc=True).dt.tz_convert('Asia/Shanghai')
    tt['exit_time_beijing']=pd.to_datetime(tt.exit_time,utc=True).dt.tz_convert('Asia/Shanghai')
    tables={'trades.csv.gz':tt,'controls.csv.gz':cc,'candidates.csv.gz':pd.DataFrame(all_events),
            'statuses.csv.gz':pd.DataFrame(all_status),'paired_four_r.csv.gz':pd.DataFrame(paired)}
    for name,table in tables.items():table.to_csv(folder/name,index=False,compression={'method':'gzip','mtime':0})
    result=dict(symbol=symbol,minutes=minutes,run_identity=rid,rows=len(frame),partial=partial,gaps=int(facts['gap'].sum()),
        first=str(frame.index[0]),last=str(frame.index[-1]),candidates=len(all_events),trades=len(tt),
        files={name:p.digest(folder/name) for name in tables},pivot_ties=local.trace['pivot_tie_counts'],
        baseline_parent_parity=True,elapsed_seconds=time.monotonic()-began)
    p.dump(receipt,result);return result


def replay(cfg,workers):
    code=sources();manifest=json.loads((Path(cfg['data_dir'])/'manifest.json').read_text())
    identity={'config':cfg,'code':code,'data':manifest,
        'symbol_meta_source':{'path':str(p.source.EXCHANGE_INFO),'sha256':p.digest(p.source.EXCHANGE_INFO)},
        'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()}
    rid=fingerprint(identity);out=EXP/'run_v1';out.mkdir(parents=True,exist_ok=True)
    ip=out/'identity.json'
    if ip.exists() and json.loads(ip.read_text())!=identity:raise ValueError('Do not overwrite a different run')
    p.dump(ip,identity);done=[];errors=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(worker,(r['symbol'],m,cfg,r,rid)):f"{r['symbol']}_{m}" for r in manifest['streams'] for m in cfg['timeframes']}
        for future in as_completed(futures):
            try:
                result=future.result();done.append(result)
                print(json.dumps({k:result[k] for k in ('symbol','minutes','trades','elapsed_seconds')}),flush=True)
            except Exception as exc:
                errors.append({'stream':futures[future],'error':repr(exc)});print(json.dumps(errors[-1]),flush=True)
    p.dump(out/'manifest.json',{'complete':len(done)==6 and not errors,'run_identity':rid,'receipts':done,'errors':errors})
    if errors:raise RuntimeError(str(errors))


def fold_of(entry,exit_,split):
    """Purge trades whose known outcome crosses the chronological cut."""
    return np.where(entry>=split,'later',np.where(exit_<split,'earlier','cross_split'))


def holm(values):
    """Adjust a predeclared family without dropping missing hypotheses."""
    raw=np.asarray(values,float);out=np.full(len(raw),np.nan)
    indices=np.flatnonzero(np.isfinite(raw));order=indices[np.argsort(raw[indices])]
    if len(order):out[order]=np.minimum(1,np.maximum.accumulate(raw[order]*(len(raw)-np.arange(len(order)))))
    return out


def summarize(cfg):
    from scipy.stats import rankdata
    root=EXP/'run_v1';manifest=json.loads((root/'manifest.json').read_text())
    if not manifest['complete']:raise ValueError('Incomplete replay')
    combined={}
    for name in ['trades','controls','candidates','statuses','paired_four_r']:
        tables=[]
        for r in manifest['receipts']:
            path=root/'streams'/f"{r['symbol']}_{r['minutes']}m"/f'{name}.csv.gz'
            if p.digest(path)!=r['files'][path.name]:raise ValueError('Changed stream output')
            tables.append(pd.read_csv(path))
        combined[name]=pd.concat(tables,ignore_index=True)
    out=EXP/'summary_v1';out.mkdir(parents=True,exist_ok=True)
    trades=stats.enrich(combined['trades'],pd.Timestamp(cfg['split']))
    trades['fold']=fold_of(trades.entry_time,trades.exit_time,pd.Timestamp(cfg['split']))
    cc=combined['controls'];cc['control_exit_time']=pd.to_datetime(cc.control_exit_time,utc=True)
    trades=trades.merge(cc[['trade_key','matched','control_net_bp','control_net_r','excess_bp','control_exit_time','utc_week']],on='trade_key',validate='one_to_one')
    records=[];rank_rows=[]
    for keys,group in trades.groupby(['symbol','timeframe_min','arm','policy']):
        symbol,minutes,arm,policy=keys
        for fold in ['all','earlier','later']:
            g=group if fold=='all' else group[group.fold.eq(fold)]
            closed=g.loc[~g.censored].copy()
            row=dict(symbol=symbol,timeframe_min=int(minutes),arm=arm,policy=policy,fold=fold,censored=int(g.censored.sum()),**stats.metrics(closed))
            matched=closed.loc[closed.matched].copy()
            if fold=='earlier':matched=matched.loc[matched.control_exit_time<pd.Timestamp(cfg['split'])]
            if len(matched):
                inf=stats.block_inference(matched.excess_bp.to_numpy(),matched.utc_week.to_numpy(),seed=cfg['stat_seed'])
                row.update(random_mean_net_bp=float(matched.control_net_bp.mean()),target_matched_net_bp=float(matched.net_bp.mean()),matched_n=len(matched),**{'excess_'+k:v for k,v in inf.items()})
            records.append(row)
        earlier=group.loc[~group.censored & group.fold.eq('earlier')]
        later=group.loc[~group.censored & group.fold.eq('later')]
        # A predeclared single feature baseline, not a trained model or an optimized score.
        score=group.side*group.htf_slope/group.signal_price
        cut=float(score.loc[earlier.index].quantile(.9)) if len(earlier) else np.nan
        validation=later.loc[np.isfinite(score.loc[later.index])]
        y=validation.net_r.gt(0).to_numpy();s=score.loc[validation.index].to_numpy()
        auc=float((rankdata(s)[y].sum()-y.sum()*(y.sum()+1)/2)/(y.sum()*(len(y)-y.sum()))) if y.any() and (~y).any() else np.nan
        top=later.loc[score.loc[later.index]>=cut]
        rank_rows.append(dict(symbol=symbol,timeframe_min=int(minutes),arm=arm,policy=policy,validation_n=len(validation),auc=auc,early_q90=cut,
            later_top_n=len(top),later_top_gross_bp=float(top.gross_bp.mean()),later_top_net_bp=float(top.net_bp.mean()),later_top_win_rate=float(top.net_r.gt(0).mean()),
            later_top_matched_random_bp=float(top.loc[top.matched,'control_net_bp'].mean())))
    metric_table=pd.DataFrame(records)
    later_mask=metric_table.fold.eq('later')
    metric_table.loc[later_mask,'later_excess_p_holm_36']=holm(metric_table.loc[later_mask,'excess_p_one_sided'])
    metric_table.to_csv(out/'metrics.csv',index=False)
    pd.DataFrame(rank_rows).to_csv(out/'single_feature_baseline.csv',index=False)
    yearly=[]
    for keys,g in trades.loc[~trades.censored].groupby(['symbol','timeframe_min','arm','policy',trades.entry_time.dt.year]):
        yearly.append(dict(zip(['symbol','timeframe_min','arm','policy','year'],keys),**stats.metrics(g)))
    pd.DataFrame(yearly).to_csv(out/'yearly.csv',index=False)
    side_rows=[]
    for keys,g in trades.loc[~trades.censored].groupby(['symbol','timeframe_min','arm','policy','side','fold']):
        side_rows.append(dict(zip(['symbol','timeframe_min','arm','policy','side','fold'],keys),**stats.metrics(g),
            matched_random_net_bp=float(g.loc[g.matched,'control_net_bp'].mean())))
    pd.DataFrame(side_rows).to_csv(out/'side_metrics.csv',index=False)
    paired=combined['paired_four_r'];paired['entry_time']=pd.to_datetime(paired.entry_time,utc=True);paired['exit_time']=pd.to_datetime(paired.exit_time,utc=True)
    paired['fold']=fold_of(paired.entry_time,paired.exit_time,pd.Timestamp(cfg['split']))
    pr=[]
    for keys,g in paired.loc[~paired.censored & ~paired.take4_censored].groupby(['symbol','timeframe_min','arm','fold']):
        blocks=g.entry_time.dt.strftime('%G-W%V').to_numpy();delta=g.take4_net_bp-g.baseline_net_bp
        inf=stats.block_inference(delta.to_numpy(),blocks,seed=cfg['stat_seed'])
        known=g.loc[g.mfe_known_r>=4]
        pr.append(dict(zip(['symbol','timeframe_min','arm','fold'],keys),pairs=len(g),baseline_mean_net_r=float(g.baseline_net_r.mean()),
            four_mean_net_r=float(g.take4_net_r.mean()),known_touch4=len(known),touch4_then_net_loss=int(known.baseline_net_r.lt(0).sum()),
            baseline_net5=int(g.baseline_net_r.ge(5).sum()),baseline_net10=int(g.baseline_net_r.ge(10).sum()),**inf))
    paired_table=pd.DataFrame(pr)
    later_mask=paired_table.fold.eq('later')
    paired_table.loc[later_mask,'later_delta_p_holm_12']=holm(paired_table.loc[later_mask,'p_one_sided'])
    paired_table.to_csv(out/'four_r_comparison.csv',index=False)
    for name,table in {'all_trades':trades,'all_statuses':combined['statuses'],'all_candidates':combined['candidates'],'all_controls':cc,'paired_four_r':paired}.items():
        table.to_csv(out/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    # One readable row per baseline trade; deterministic factual labels, not invented visual reviews.
    fields=['symbol','timeframe_min','arm','entry_time_beijing','side','entry_price','initial_stop','initial_risk','fee_r','entry_context',
            'exit_time_beijing','exit_price','exit_reason','net_r','mfe_known_r','mfe_upper_r','close_peak_r','review_label','trade_key']
    trades.loc[trades.policy.eq('baseline'),fields].to_csv(out/'逐笔复盘.csv',index=False,encoding='utf-8-sig')
    files={str(x):p.digest(x) for x in out.iterdir() if x.is_file() and x.name!='receipt.json'}
    p.dump(out/'receipt.json',{'config':cfg,'run_manifest_sha256':p.digest(root/'manifest.json'),'files':files,
        'training_eligible':False,'production_eligible':False,'baseline_trade_rows':int(trades.policy.eq('baseline').sum()),
        'no_portfolio_claim':True,'manual_visual_review_of_every_chart':False})
    print(json.dumps({'summary':str(out),'trade_rows':len(trades),'baseline_rows':int(trades.policy.eq('baseline').sum())}),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['data','replay','summary']);parser.add_argument('--workers',type=int,default=3)
    args=parser.parse_args();cfg=json.loads(CONFIG.read_text())
    if cfg['round_trip_cost']!=.002 or cfg['timeframes']!=[5,15,60]:raise ValueError('Frozen protocol changed')
    if args.stage=='data':prepare_data(cfg)
    elif args.stage=='replay':replay(cfg,args.workers)
    else:summarize(cfg)


if __name__=='__main__':main()
