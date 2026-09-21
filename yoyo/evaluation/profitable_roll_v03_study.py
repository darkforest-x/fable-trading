"""V0.3 management benchmark on frozen entries, plus separate observed WIF case.

Entry timestamps/stops and matched controls come from the committed previous
study. No new entry feature or retrospective winner filter enters the pool.
Only completed hourly structures feed add decisions. The cross-asset execution
profile is an explicitly synthetic maintenance stress, NOT exchange-realistic
leverage, mark-price or funding history. Every event has its own100U account.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd

from yoyo.evaluation import winner_pyramiding_study as old
from yoyo.evaluation.profitable_roll_v03 import replay_roll

EXP=Path('experiments/active/exp-profitable-roll-v03-20260921-v1')
PRIOR=old.EXP/'run_v1'
META=Path('data/kline_preholdout_binance_um5m/exchange_info.json')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def read_prior(symbol):
    """Select one prior arm only to deduplicate entry facts, never its winners."""
    folder=PRIOR/'streams'/symbol
    manifest=json.loads((PRIOR/'manifest.json').read_text())
    if digest(folder/'completion.json')!=manifest['files'][f'streams/{symbol}/completion.json']:
        raise ValueError('Prior completion receipt changed')
    receipt=json.loads((folder/'completion.json').read_text())
    path=folder/'trades.csv.gz'
    if digest(path)!=receipt['files'][path.name]:raise ValueError('Prior trade ledger changed')
    try:table=pd.read_csv(path)
    except pd.errors.EmptyDataError:return pd.DataFrame(),digest(path)
    rows=table[table.arm.eq('none')].copy()
    if rows.duplicated(['scope','event_key']).any():raise ValueError('Duplicate frozen entry')
    return rows,digest(path)


def quantity_rules(symbol,metadata):
    """Archived public order filters; not proof of historical effective dates."""
    info=next(s for s in metadata['symbols'] if s['symbol']==symbol)
    filters={r['filterType']:r for r in info['filters']}
    lot=filters.get('MARKET_LOT_SIZE',filters['LOT_SIZE'])
    fallback=filters['LOT_SIZE'];step=float(lot['stepSize']) or float(fallback['stepSize'])
    minimum=float(lot['minQty']) or float(fallback['minQty'])
    notional=filters.get('MIN_NOTIONAL',filters.get('NOTIONAL',{}))
    return {'quantity_step':step,'min_quantity':minimum,
        'max_order_quantity':float(lot['maxQty']),
        'min_notional':float(notional.get('notional',notional.get('minNotional',0)))}


def select_examples(tables,count):
    """Explicitly retrospective OLD no-add winners, frozen before new outcomes."""
    full=pd.concat([t for t in tables if len(t)],ignore_index=True)
    eligible=full[full.scope.eq('actual') & ~full.censored.astype(bool) & full.symbol.ne('WIFUSDT')]
    ranked=eligible.sort_values(['net_r','event_key'],ascending=[False,True]).drop_duplicates('symbol').head(count)
    return ranked[['event_key','symbol','period','entry_time','entry_price','initial_stop','exit_time','exit_price','net_r','net_usd']].to_dict('records')


def replay_arms(frame,entry_i,stop,tick,requested,tiers,cfg,rules,**extra):
    results=[];events=[];prefixes={}
    for arm,cap in cfg['arms'].items():
        result,detail=replay_roll(frame,entry_i=entry_i,initial_stop=stop,tick=tick,
            initial_quantity_requested=requested,capital=cfg['capital'],leverage=cfg['leverage'],
            max_adds=cap,fee=cfg['fee_each_side'],tiers=tiers,
            maintenance_guard_add=arm!='continuous_unprotected',**rules,**extra)
        results.append({'arm':arm,**result});events.extend({'arm':arm,**e} for e in detail)
        prefixes[arm]=[(e.get('time_utc'),e.get('price'),e.get('quantity')) for e in detail if e['kind']=='add'][:2]
    if prefixes['two']!=prefixes['continuous']:raise AssertionError('Capped and unlimited firsttwo fills differ')
    return results,events


def one(args):
    symbol,item,cfg,metadata,output=args
    path=Path(item['path'])
    if digest(path)!=item['sha256']:raise ValueError('Frozen OHLC changed')
    raw=old.source.inc.guarded_5m(path,old.source.source.START-pd.Timedelta(days=old.source.source.WARMUP_BARS))
    frame=old.source.v11.bars_for(raw,60)
    counts=raw.resample('1h').size().reindex(frame.index).fillna(0)
    frame=frame.loc[counts.eq(12),['open','high','low','close','volume']]
    prior,prior_sha=read_prior(symbol);rules=quantity_rules(symbol,metadata)
    tiers=[{'max_quantity':cfg['synthetic_max_quantity'],'mmr':cfg['synthetic_maintenance_rate'],'max_leverage':cfg['leverage']}]
    answers=[];details=[]
    for row in prior.itertuples(index=False):
        end=pd.Timestamp(cfg['split'] if row.period=='earlier' else cfg['end'])
        f=frame.loc[frame.index<end];entry=pd.Timestamp(row.entry_time)
        i=int(f.index.get_indexer([entry])[0])
        if i<0 or not np.isclose(f.iloc[i].open,row.entry_price,rtol=0,atol=float(item['meta']['tick'])*1e-5):
            raise ValueError(f'Frozen entry price/time changed:{symbol}:{entry}')
        requested=cfg['initial_requested_gross_risk']/(float(row.entry_price)-float(row.initial_stop))
        result,events=replay_arms(f,i,float(row.initial_stop),float(item['meta']['tick']),requested,tiers,cfg,rules)
        identity={'symbol':symbol,'period':row.period,'scope':row.scope,'event_key':row.event_key,
            'month':row.month,'vol_bin':int(row.vol_bin),'entry_time':entry.isoformat(),
            'entry_price':float(row.entry_price),'initial_stop':float(row.initial_stop),
            'old_none_net_r':float(row.net_r),'old_none_net_usd':float(row.net_usd)}
        answers.extend({**identity,**r} for r in result)
        details.extend({**identity,**e} for e in events)
    if digest(path)!=item['sha256'] or read_prior(symbol)[1]!=prior_sha:raise ValueError('Source mutated during replay')
    folder=Path(output)/'streams'/symbol;folder.mkdir(parents=True,exist_ok=False)
    for name,records in [('trades',answers),('events',details)]:
        pd.DataFrame(records).to_csv(folder/f'{name}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    receipt={'symbol':symbol,'source_sha256':item['sha256'],'old_trades_sha256':prior_sha,
        'old_entry_count':len(prior),'new_result_count':len(answers),'quantity_rules':rules,
        'removed_partial_hours':int(counts.ne(12).sum()),'files':{p.name:digest(p) for p in folder.iterdir()}}
    (folder/'completion.json').write_text(json.dumps(receipt,indent=2)+'\n')
    return receipt


def complete(table):
    return table.status.eq('complete') & ~table.censored.astype(bool) & table.final_balance.notna()


def summaries(output,cfg):
    output=Path(output);parts=[]
    for path in sorted((output/'streams').glob('*/trades.csv.gz')):
        try:parts.append(pd.read_csv(path))
        except pd.errors.EmptyDataError:pass
    t=pd.concat(parts,ignore_index=True);t['closed']=complete(t)
    t['net_usd']=t.final_balance-cfg['capital']
    rows=[]
    for (period,scope,arm),group in t.groupby(['period','scope','arm']):
        closed=group[group.closed];wins=closed.net_usd.gt(0)
        rows.append({'period':period,'scope':scope,'arm':arm,'entries':len(group),'complete':len(closed),
            'censored':int(group.censored.sum()),'ambiguous':int(group.status.eq('ambiguous').sum()),
            'other_unfilled':int(group.status.eq('rejected_initial').sum()),
            'net_profit_sum_independent_accounts':float(closed.net_usd.sum()),
            'mean_final_balance':float(closed.final_balance.mean()) if len(closed) else None,
            'median_final_balance':float(closed.final_balance.median()) if len(closed) else None,
            'win_rate':float(wins.mean()) if len(closed) else None,
            'pf':float(closed.loc[wins,'net_usd'].sum()/-closed.loc[~wins,'net_usd'].sum()) if (closed.net_usd<0).any() else None,
            'max_adds':int(group.adds_count.max()),'total_adds':int(group.adds_count.sum()),
            'initial_clipped':int((group.initial_quantity<group.initial_quantity_requested-1e-7).sum())})
    pd.DataFrame(rows).to_csv(output/'summary.csv',index=False)
    actual=t[t.scope.eq('actual')];random=t[t.scope.eq('random')]
    paired=actual.merge(random,on=['event_key','symbol','period','month','arm'],suffixes=('_actual','_random'),validate='one_to_one')
    if not paired.vol_bin_actual.eq(paired.vol_bin_random).all():raise AssertionError('Matched volatility buckets changed')
    paired['common_complete']=paired.closed_actual & paired.closed_random
    paired['delta_usd']=np.where(paired.common_complete,paired.net_usd_actual-paired.net_usd_random,np.nan)
    paired[['event_key','symbol','period','month','arm','common_complete','net_usd_actual','net_usd_random','delta_usd']].to_csv(output/'matched_controls.csv',index=False)
    comparisons=[];statistics={}
    for period in ['earlier','later']:
        a=actual[actual.period.eq(period)]
        base=a[a.arm.eq('continuous')]
        for control in ['none','two','continuous_unprotected']:
            pair=base.merge(a[a.arm.eq(control)],on=['event_key','month'],suffixes=('_target','_control'),validate='one_to_one')
            valid=pair.closed_target & pair.closed_control
            p=pd.DataFrame({'event_key':pair.event_key,'month':pair.month,'period':period,'comparison':'continuous-'+control,
                'common_complete':valid,'delta_usd':np.where(valid,pair.net_usd_target-pair.net_usd_control,np.nan)})
            comparisons.append(p)
        p=paired[paired.period.eq(period)&paired.arm.eq('continuous')]
        comparisons.append(pd.DataFrame({'event_key':p.event_key,'month':p.month,'period':period,'comparison':'continuous-random',
            'common_complete':p.common_complete,'delta_usd':p.delta_usd}))
    comparisons=pd.concat(comparisons,ignore_index=True);comparisons.to_csv(output/'paired_comparisons.csv',index=False)
    for (period,name),part in comparisons.groupby(['period','comparison']):
        part=part[part.common_complete].rename(columns={'delta_usd':'delta_r'})
        result=old.block_inference(part,seed=cfg['seed'],reps=cfg['bootstrap_reps'])
        result['unit']='USDT per independent100U case;legacy helper field mean_delta is USD,not R'
        statistics[period+'|'+name]=result
    planned=['later|continuous-two','later|continuous-random']
    ranked=sorted(planned,key=lambda key:statistics[key]['p_one_sided'] if statistics[key]['p_one_sided'] is not None else 1)
    running=0.
    for rank,key in enumerate(ranked):
        p=statistics[key]['p_one_sided']
        if p is not None:running=max(running,min(1.,p*(len(ranked)-rank)))
        statistics[key]['planned_holm_p']=None if p is None else running
    (output/'statistics.json').write_text(json.dumps(statistics,indent=2,allow_nan=False)+'\n')
    percoin=actual.groupby(['symbol','period','arm']).apply(lambda g:pd.Series({
        'entries':len(g),'complete':int(g.closed.sum()),'net_profit_sum_independent_accounts':g.loc[g.closed,'net_usd'].sum(),
        'win_rate':g.loc[g.closed,'net_usd'].gt(0).mean(),'mean_final_balance':g.loc[g.closed,'final_balance'].mean(),
        'max_adds':g.adds_count.max()}),include_groups=False).reset_index()
    percoin.to_csv(output/'per_coin.csv',index=False)
    chosen=json.loads((output/'selected_examples.json').read_text())
    examples=actual[actual.event_key.isin([x['event_key'] for x in chosen])].copy()
    examples=examples.merge(paired[['event_key','arm','common_complete','net_usd_random','delta_usd']],on=['event_key','arm'],validate='one_to_one')
    examples.to_csv(output/'selected_example_results.csv',index=False)
    return rows


def source_receipt():
    from yoyo.evaluation.spike_v1_v8_be05 import _committed
    from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
    paths=[Path(__file__).resolve(),Path('yoyo/evaluation/profitable_roll_v03.py'),
        Path('yoyo/evaluation/profitable_roll_v03_cli.py'),Path('yoyo/evaluation/profitable_roll_v03_audit.py'),
        Path('tests/evaluation/test_profitable_roll_v03.py'),Path('tests/evaluation/test_profitable_roll_v03_study.py'),
        Path('tests/evaluation/test_profitable_roll_v03_invariants.py'),EXP/'config.json',EXP/'PROJECT_PLAN.md',EXP/'README.md']
    paths=sorted(set([p.resolve() for p in paths]+list(_local_transitive_python((Path(__file__).resolve(),)))))
    if not _committed(tuple(paths)):raise ValueError('Commit unchanged builder/tests/config/protocol before outcomes')
    return {'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'declared':{str(p):digest(p) for p in paths}}


def finish_manifest(output,receipt):
    for p,h in receipt['declared'].items():
        if digest(p)!=h:raise ValueError('Source changed during execution')
    manifest={**receipt,'generated_at':pd.Timestamp.now(tz='UTC').isoformat(),
        'files':{str(p.relative_to(output)):digest(p) for p in output.rglob('*') if p.is_file()}}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')


def run_cross(output,workers):
    receipt=source_receipt();cfg=json.loads((EXP/'config.json').read_text())
    prior=json.loads((PRIOR/'identity.json').read_text());metadata=json.loads(META.read_text())
    manifest=json.loads((PRIOR/'manifest.json').read_text())
    if digest(PRIOR/'identity.json')!=manifest['files']['identity.json']:raise ValueError('Prior identity changed')
    if sorted(prior['inputs'])!=sorted(prior['expected_symbols']):raise ValueError('Incomplete prior pool')
    if digest(META)!=prior['metadata_sha256']:raise ValueError('Archived order metadata changed')
    tables=[read_prior(s)[0] for s in prior['expected_symbols']]
    frozen=pd.concat(tables,ignore_index=True)
    if len(frozen)!=902 or frozen.scope.value_counts().to_dict()!={'actual':451,'random':451}:
        raise AssertionError('Frozen 451 actual / 451 random entry population changed')
    chosen=select_examples(tables,cfg['winner_examples'])
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    identity={**receipt,'config':cfg,'inputs':prior['inputs'],'metadata_sha256':digest(META),
        'prior_identity_sha256':digest(PRIOR/'identity.json'),'source_mode':'frozen29_binance_synthetic_execution',
        'historical_mark_funding_tiers_available':False}
    (output/'identity.json').write_text(json.dumps(identity,indent=2)+'\n')
    (output/'selected_examples.json').write_text(json.dumps(chosen,indent=2)+'\n')
    completions=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures=[pool.submit(one,(s,item,cfg,metadata,str(output))) for s,item in prior['inputs'].items()]
        for f in as_completed(futures):
            r=f.result();completions.append(r);print(json.dumps({'done':len(completions),'total':len(futures),'symbol':r['symbol'],'entries':r['old_entry_count']}),flush=True)
    if sorted(r['symbol'] for r in completions)!=sorted(prior['expected_symbols']):raise AssertionError('Incomplete symbol pool')
    summary=summaries(output,cfg);finish_manifest(output,receipt)
    print(json.dumps(summary,indent=2))


def run_wif(payload,output):
    from yoyo.evaluation.wif_screenshot_case import ENTRY_MS,END_MS
    receipt=source_receipt();cfg=json.loads((EXP/'config.json').read_text())
    def frame(raw):
        rows=sorted({int(r[0]):[int(r[0]),*map(float,r[1:5])] for r in raw if ENTRY_MS<=int(r[0])<END_MS}.values())
        f=pd.DataFrame(rows,columns=['time','open','high','low','close']);f.index=pd.to_datetime(f.pop('time'),unit='ms',utc=True)
        expected=pd.date_range(pd.Timestamp(ENTRY_MS,unit='ms',tz='UTC'),periods=126,freq='1h')
        if not f.index.equals(expected):raise ValueError('Incomplete WIF source')
        return f
    price,mark=frame(payload['price']),frame(payload['mark'])
    if price.iloc[0].open!=.1402 or price.high.max()!=.2296:raise ValueError('Wrong WIF anchors')
    rates={int(r['fundingTime']):float(r['realizedRate']) for r in payload['funding'] if ENTRY_MS<int(r['fundingTime'])<END_MS}
    expected=set(range(((ENTRY_MS//14400000)+1)*14400000,END_MS,14400000))
    if set(rates)!=expected:raise ValueError('Incomplete WIF funding')
    tiers=[{'max_quantity':float(r['maxSz']),'mmr':float(r['mmr']),'max_leverage':float(r['maxLever'])} for r in payload['tiers']]
    instrument=payload['instrument']
    if instrument['instId']!='WIF-USDT-SWAP' or float(instrument['ctVal'])*float(instrument['ctMult'])!=1:
        raise ValueError('WIF contract-to-coin conversion changed')
    rules={'quantity_step':float(instrument['lotSz']),'min_quantity':float(instrument['minSz']),
        'min_notional':0.,'max_order_quantity':float(instrument['maxMktSz'])}
    result,events=replay_arms(price,0,.1362,float(instrument['tickSz']),25000,tiers,cfg,rules,mark_frame=mark,funding_rates=rates)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    (output/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    pd.DataFrame(events).to_csv(output/'events.csv',index=False)
    (output/'source_receipt.json').write_text(json.dumps({'source':'OKX_public','raw_candles_persisted_locally':False,
        'payload_sha256':canonical_sha(payload),'historical_risk_tiers_verified':False,
        'historical_instrument_rules_verified':False,'current_quantity_rules':rules,
        'current_tick':float(instrument['tickSz']),'price_count':126,'mark_count':126,'funding_count':31},indent=2)+'\n')
    finish_manifest(output,receipt);print(json.dumps(result,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['cross','wif-stdin'],default='cross')
    parser.add_argument('--output',type=Path);parser.add_argument('--workers',type=int,default=3);args=parser.parse_args()
    output=args.output or EXP/('run_v1' if args.mode=='cross' else 'wif_v1')
    if args.mode=='cross':run_cross(output,args.workers)
    else:run_wif(json.load(sys.stdin),output)
