"""Owner-fixed gold entry x close-time comparison, one native historical run.

NY Monday anchors read only the completed Monday. Entries are resting limits
at the first observed weekly-side touch. Controls use the same week, side and
14 completed 5m-bar relative-TR bucket, with price geometry scaled to their
submission closes. Features never read past submission; outcome excursions
come from native event callbacks. R is 1% of entry, before fees; this is an
independent trade study, not an account equity simulation or parameter search.
"""
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.monday_limit_reclaim_inputs import weekly_context
from yoyo.evaluation.monday_limit_reclaim import segment_ends
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy
from yoyo.layers.l3_backtest.nautilus_gold_recovery import run_gold_recovery

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-xau-monday-recovery-20261026-v1'
DATA=ROOT/'data/crypto/market/exp-xau-monday-recovery-20261026-v1'
CODE=['yoyo/evaluation/gold_monday_recovery.py','yoyo/layers/l3_backtest/nautilus_gold_recovery.py']


def dump(path,value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False,allow_nan=False,default=str)+'\n')


def prepare(f,cfg):
    """Causal levels and controls; OHLC touch only compresses idle submission."""
    tick=cfg['tick'];ends=segment_ends(f,dict(symbol='XAUUSDT',execution_minutes=5))
    weeks=weekly_context(f,5)
    tr=pd.concat([f.high-f.low,(f.high-f.close.shift()).abs(),(f.low-f.close.shift()).abs()],axis=1).max(axis=1)
    bucket=np.floor(np.log2((tr.rolling(14).mean()/f.close).where(lambda s:s>0))*4).fillna(-999).astype(int).to_numpy()
    rng=np.random.default_rng(cfg['seed']);orders=[];opps=[]
    for w in weeks:
        for mode in ['1618','edge']:
            for side in [1,-1]:
                event=f"XAU:{w['week']}:{mode}:{side}"
                common=dict(event_id=event,week=w['week'],entry_mode=mode,side=side)
                if not w.get('valid'):
                    opps.append(dict(common,status='unobservable_week'));continue
                hi,lo=w['monday_high'],w['monday_low'];width=hi-lo
                raw=(lo-.618*width if side>0 else hi+.618*width) if mode=='1618' else (lo if side>0 else hi)
                entry=(np.floor(raw/tick+1e-7) if side>0 else np.ceil(raw/tick-1e-7))*tick
                near=lo if side>0 else hi;target=hi if side>0 else lo
                a,b=w['monitor_start_i'],w['monitor_end_i_exclusive']
                hit=np.flatnonzero(f.low.to_numpy()[a:b]<=entry if side>0 else f.high.to_numpy()[a:b]>=entry)
                if not len(hit):opps.append(dict(common,status='no_touch'));continue
                touch=a+int(hit[0]);j=touch-1
                if j<0 or side*(f.close.iloc[j]-entry)<tick*(1-1e-7) or ends[touch]<=j:
                    opps.append(dict(common,status='already_marketable_or_missing_submission'));continue
                opps.append(dict(common,status='admitted',touch=str(f.index[touch])))
                pool=np.arange(a,max(a,b-1),dtype=int)
                pool=pool[(bucket[pool]==bucket[j])&(bucket[pool]!=-999)&(ends[pool]>pool)&(pool!=j)]
                chosen=rng.choice(pool,cfg['controls_per_signal'],replace=False) if len(pool)>=cfg['controls_per_signal'] else []
                attempts=[('signal',0,j,entry,near,target)]
                for k,cj in enumerate(chosen,1):
                    ce=f.close.iloc[cj]*entry/f.close.iloc[j]
                    ce=(np.floor(ce/tick+1e-7) if side>0 else np.ceil(ce/tick-1e-7))*tick
                    attempts.append(('random',k,int(cj),ce,ce*near/entry,ce*target/entry))
                for population,k,j,price,n,t in attempts:
                    if side*(f.close.iloc[j]-price)<tick*(1-1e-7):continue
                    end=int(ends[j+1]);expiry=min(b-1,end)
                    if expiry<=j:continue
                    target=float(np.round(t/tick)*tick)
                    stop=float((np.floor(price*(1-side*.01)/tick+1e-7) if side>0 else np.ceil(price*(1-side*.01)/tick-1e-7))*tick)
                    for minutes in cfg['confirmation_minutes']:
                        pair=f'{event}:{minutes}'
                        orders.append(dict(common,trade_id=f'{pair}:c{k}',pair_id=pair,population=population,control_id=k,
                            entry_i=j,expire_i=expiry,end_i=end,entry=price,stop=stop,risk=side*(price-stop),
                            target_price=target,near_edge=float(n),confirmation_minutes=minutes,
                            matched_controls_available=len(chosen),volatility_bucket=int(bucket[j]),
                            censor_reason='source_end' if end==len(f)-1 else 'missing_source_interval'))
    return pd.DataFrame(orders),pd.DataFrame(opps),weeks


def metrics(g):
    c=g[g.reason.ne(0)];counts=c.exit_kind.value_counts()
    missed=c.returned_to_range.astype(bool)&c.exit_kind.ne('far_target')
    return dict(entered=len(g),closed=len(c),unresolved=int(g.reason.eq(0).sum()),
        net_r_sum=float(c.net_fixed_r.sum()),gross_r_sum=float(c.gross_fixed_r.sum()),
        mean_net_r=float(c.net_fixed_r.mean()) if len(c) else None,
        far_target=int(counts.get('far_target',0)),backstop=int(counts.get('backstop',0)),
        fee_stop=int(counts.get('fee_stop',0)),fee_recovery=int(counts.get('fee_recovery',0)),
        breakeven=int(counts.get('fee_stop',0)+counts.get('fee_recovery',0)),
        ever_underwater=int(g.mae_fixed_r.lt(-1e-7).sum()),
        underwater_at_least_half_r=int(g.mae_fixed_r.le(-.5).sum()),
        worst_mae_r=float(g.mae_fixed_r.min()),median_mae_r=float(g.mae_fixed_r.median()),
        returned_without_tp=int(missed.sum()),returned_without_tp_be=int((missed&c.exit_kind.isin(['fee_stop','fee_recovery'])).sum()),
        returned_without_tp_backstop=int((missed&c.exit_kind.eq('backstop')).sum()),
        unresolved_underwater=int((g.reason.eq(0)&g.gross_fixed_r.lt(0)).sum()),
        unresolved_mark_net_r=float(g.loc[g.reason.eq(0),'net_fixed_r'].sum()),
        fee_band_cases=int(g.decision_status.eq('fee_band_wait').sum()),ambiguous_exit_bars=int(g.ambiguous.sum()))


def analyze(rows,opps,audit,cfg):
    summary={'market':cfg['instrument'],'data':json.loads((DATA/'receipt.json').read_text()),'config':cfg,
        'opportunities':opps.groupby(['entry_mode','status']).size().reset_index(name='count').to_dict('records'),
        'metrics':[],'matched_controls':[]}
    for (mode,minutes),g in rows[rows.population.eq('signal')].groupby(['entry_mode','confirmation_minutes']):
        for period,sub in [('all',g),('2025',g[g.week<'2026']),('2026',g[g.week>='2026'])]:
            if len(sub):summary['metrics'].append(dict(entry_mode=mode,minutes=int(minutes),period=period,**metrics(sub)))
    closed=rows[rows.reason.ne(0)]
    pairs=[]
    for pair,g in closed.groupby('pair_id'):
        sig=g[g.population.eq('signal')];ctrl=g[g.population.eq('random')]
        if len(sig)==1 and len(ctrl)==cfg['controls_per_signal']:
            r=sig.iloc[0];pairs.append(dict(pair_id=pair,week=r.week,entry_mode=r.entry_mode,minutes=int(r.confirmation_minutes),
                signal_r=float(r.net_fixed_r),random_r=float(ctrl.net_fixed_r.mean())))
    paired=pd.DataFrame(pairs);paired.to_csv(EXP/'matched_pairs.csv',index=False)
    if len(paired):
        rng=np.random.default_rng(cfg['seed'])
        for (mode,minutes),g in paired.groupby(['entry_mode','minutes']):
            x=g.assign(delta=g.signal_r-g.random_r).groupby('week').delta.agg(['sum','count'])
            ix=rng.integers(0,len(x),size=(3000,len(x)))
            boot=x['sum'].to_numpy()[ix].sum(axis=1)/x['count'].to_numpy()[ix].sum(axis=1)
            summary['matched_controls'].append(dict(entry_mode=mode,minutes=int(minutes),pairs=len(g),
                signal_mean_r=float(g.signal_r.mean()),random_mean_r=float(g.random_r.mean()),
                excess_mean_r=float((g.signal_r-g.random_r).mean()),excess_week_bootstrap95=[float(v) for v in np.quantile(boot,[.025,.975])]))
    summary['native']={k:audit[k] for k in ['positions','fills','engine','version','max_simultaneous_positions']}
    dump(EXP/'summary.json',summary)
    cols=['entry_mode','confirmation_minutes','week','side','entry_time','exit_time','entry','exit_price','exit_kind','decision_status','net_fixed_r','mae_fixed_r','mfe_fixed_r','returned_to_range','reason']
    rows[rows.population.eq('signal')][cols].to_csv(EXP/'signal_trades.csv',index=False)
    print(json.dumps(summary,ensure_ascii=False,indent=2))


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    if (EXP/'summary.json').exists():raise RuntimeError('Existing result: reuse instead of replay')
    for p in CODE+[str((EXP/'config.json').relative_to(ROOT))]:
        if subprocess.run(['git','diff','--quiet','HEAD','--',p],cwd=ROOT).returncode or subprocess.run(['git','ls-files','--error-unmatch',p],cwd=ROOT,capture_output=True).returncode:
            raise RuntimeError('Commit builder and config first')
    f=pd.read_csv(DATA/'candles.csv.gz',index_col='open_time',parse_dates=True)
    f.index=pd.DatetimeIndex(f.index).as_unit('ns');f.attrs['group']='crypto_5m'
    e,op,weeks=prepare(f,cfg)
    dump(EXP/'weeks.json',weeks);e.to_csv(EXP/'orders.csv.gz',index=False);op.to_csv(EXP/'opportunities.csv',index=False)
    frame=f[['open','high','low','close']].copy();frame.index+=pd.Timedelta(minutes=5)
    policy=ExitPolicy(trail_arm_r=1e12,opposite_hour_exit=False,round_trip_cost=0,target_from_entry=True,
        intrabar_path='OHLC',monotone_segments=True,fill_maker_fee=cfg['maker_fee'],fill_taker_fee=cfg['taker_fee'])
    rows,fills,audit=run_gold_recovery(frame,e,policy,tick=cfg['tick'],market_tick=.01,symbol='XAU',execution_minutes=5,batch_bars=24000)
    for col in ['net','gross','mae','mfe']:
        rows[col+'_fixed_r']=rows[col+'_r']*rows.risk/(rows.entry*.01)
    rows.to_csv(EXP/'trades.csv.gz',index=False);fills.to_csv(EXP/'fills.csv.gz',index=False);dump(EXP/'audit.json',audit)
    identity=dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        files=[dict(path=p,size=(ROOT/p).stat().st_size,mtime_ns=(ROOT/p).stat().st_mtime_ns) for p in CODE],
        data_receipt=json.loads((DATA/'receipt.json').read_text()),framework='NautilusTrader 1.231.0')
    dump(EXP/'identity.json',identity)
    analyze(rows,op,audit,cfg)


if __name__=='__main__':main()
