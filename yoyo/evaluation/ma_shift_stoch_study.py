"""Frozen monthly ETH MA Shift/Stoch study with same-day random controls.

Source contract: exp-ma-shift-stoch-eth-month-20260915-v1/PROJECT_PLAN.md.
The control volatility is SMA20 true range divided by current close; only
current/prior high/low/close are used. No score fitting or parameter search.
"""
from __future__ import annotations
import hashlib
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.ma_shift_stoch import run_backtest, replay_one, BAR

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'experiments/active/exp-ma-shift-stoch-eth-month-20260915-v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, obj):
    def clean(x):
        if isinstance(x, dict): return {str(k): clean(v) for k, v in x.items()}
        if isinstance(x, (list, tuple)): return [clean(v) for v in x]
        if isinstance(x, np.generic): return clean(x.item())
        if isinstance(x, float) and not np.isfinite(x): return None
        if isinstance(x, pd.Timestamp): return x.isoformat()
        return x
    Path(path).write_text(json.dumps(clean(obj), ensure_ascii=False, indent=2, default=str)+'\n')


def identity():
    paths = [Path(__file__), ROOT/'yoyo/evaluation/ma_shift_stoch.py',
             ROOT/'yoyo/evaluation/spike_fanshen_exit.py', EXP/'config.json', EXP/'PROJECT_PLAN.md']
    receipt = {}
    for path in paths:
        rel = str(path.relative_to(ROOT))
        if subprocess.check_output(['git', 'show', 'HEAD:'+rel], cwd=ROOT) != path.read_bytes():
            raise ValueError('uncommitted builder: '+rel)
        receipt[rel] = sha(path)
    return receipt


def metrics(trades):
    if trades.empty:
        return dict(n=0, net_win_rate=None, gross_win_rate=None, gross_mean_bp=None,
                    net_mean_bp=None, net_sum_pct=0, pf=None, longest_loss=0)
    net = trades.net_return.to_numpy(float); gross = trades.gross_return.to_numpy(float)
    wins = net[net > 0].sum(); losses = -net[net < 0].sum()
    longest = current = 0
    for value in net:
        current = current+1 if value < 0 else 0
        longest = max(longest, current)
    duration = (pd.to_datetime(trades.exit_time, utc=True)-pd.to_datetime(trades.entry_time, utc=True)).dt.total_seconds()/60
    return dict(n=len(net), net_win_rate=float((net>0).mean()), gross_win_rate=float((gross>0).mean()),
                gross_mean_bp=float(gross.mean()*1e4), net_mean_bp=float(net.mean()*1e4),
                gross_sum_pct=float(gross.sum()*100), net_sum_pct=float(net.sum()*100),
                pf=float(wins/losses) if losses else None, longest_loss=longest,
                median_hold_minutes=float(duration.median()), max_hold_minutes=float(duration.max()),
                max_trade_net_pct=float(net.max()*100), min_trade_net_pct=float(net.min()*100))


def controls(frame, signals, targets, cfg):
    close_clock = frame.index + BAR
    prev = frame.close.shift(1)
    tr = pd.concat([frame.high-frame.low, (frame.high-prev).abs(), (frame.low-prev).abs()], axis=1).max(axis=1)
    vol = tr.rolling(20, min_periods=20).mean()/frame.close
    buckets = np.searchsorted(cfg['volatility_bins'], vol.to_numpy(), side='left')
    day = close_clock.strftime('%Y-%m-%d')
    start,end = pd.Timestamp(cfg['start_utc']),pd.Timestamp(cfg['end_utc'])
    allowed = (close_clock>=start)&(close_clock<end)&np.isfinite(vol.to_numpy())
    allowed[-1] = False
    rows=[]
    for target in targets.itertuples(index=False):
        i = int(frame.index.get_loc(pd.Timestamp(target.entry_signal_time)-BAR))
        side = int(target.side)
        choices = np.flatnonzero(allowed & (day==day[i]) & (buckets==buckets[i]) & (np.arange(len(frame))!=i))
        event=f'{pd.Timestamp(target.entry_time).isoformat()}|{side}'
        row=dict(target_trade_id=int(target.trade_id), event=event, day=day[i], vol_bin=int(buckets[i]),
                 side=side, signal_i=i, target_net_return=float(target.net_return),
                 target_gross_return=float(target.gross_return), choices=len(choices), matched=False)
        if len(choices):
            pick=int(hashlib.sha256(f'{cfg["control_seed"]}|{event}'.encode()).hexdigest(),16)%len(choices)
            chosen=int(choices[pick])
            result=replay_one(frame, signals, chosen, side, 'full_opposite', end)
            if len(result.trade)>1: raise AssertionError('control contains multiple trades')
            row.update(control_signal_i=chosen, control_entry_time=close_clock[chosen], reason='censored')
            if len(result.trade)==1:
                r=result.trade.iloc[0]
                row.update(matched=True, reason='matched', control_net_return=float(r.net_return),
                           control_gross_return=float(r.gross_return), control_exit_time=r.exit_time,
                           excess_net_return=float(target.net_return-r.net_return))
        else: row['reason']='empty_stratum'
        rows.append(row)
    return pd.DataFrame(rows)


def control_stats(rows,cfg):
    if rows.empty or not rows.matched.any(): return dict(matched=0)
    pairs=rows[rows.matched].copy(); d=pairs.groupby('day').excess_net_return.sum().to_numpy(float)
    n=len(pairs); obs=float(d.sum()/n)
    rng=np.random.default_rng(cfg['control_seed']+1)
    null=(rng.choice([-1.,1.],size=(cfg['permutations'],len(d)))*d).sum(axis=1)/n
    return dict(matched=n, unmatched=len(rows)-n, utc_day_blocks=len(d),
                unique_controls=int(pairs[['control_signal_i','side']].drop_duplicates().shape[0]),
                target_mean_bp=float(pairs.target_net_return.mean()*1e4),
                control_mean_bp=float(pairs.control_net_return.mean()*1e4),
                excess_mean_bp=obs*1e4,
                p_greater=float((1+(null>=obs).sum())/(1+len(null))),
                p_two_sided=float((1+(np.abs(null)>=abs(obs)).sum())/(1+len(null))),
                test='UTC-day block sign-flip of paired net excess; descriptive symmetry null, not randomized causal proof')


def main():
    cfg=json.loads((EXP/'config.json').read_text()); code=identity(); out=EXP/'results'
    out.mkdir(exist_ok=True)
    if (out/'summary.json').exists(): raise ValueError('frozen result exists; do not overwrite')
    source=json.loads((EXP/'sources/summary.json').read_text()); path=EXP/'sources/5m.csv'
    if sha(path)!=source['timeframes']['5m']['csv_sha256']: raise ValueError('source SHA mismatch')
    dump(out/'evaluation_started.json',dict(started_at=pd.Timestamp.now(tz='UTC'), exact_configuration_holdout_consumption=1,
         prior_exposures_preserved=True, source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
         code=code, source_sha256=sha(path), config_sha256=sha(EXP/'config.json')))
    raw=pd.read_csv(path)
    frame=raw[['open','high','low','close','volume']].copy()
    frame.index=pd.DatetimeIndex(pd.to_datetime(raw.ts,unit='ms',utc=True)); frame.index.name='open_time'
    start,end=pd.Timestamp(cfg['start_utc']),pd.Timestamp(cfg['end_utc'])
    if frame.index[-1]+BAR!=end: raise ValueError('source end differs')
    summary=dict(config=cfg,source={k:v for k,v in source['timeframes']['5m'].items() if k!='pages'},arms={})
    for arm,ma in [('ma_stoch',True),('stoch_only',False)]:
        r=run_backtest(frame,start,end,use_ma_filter=ma)
        for name in ['trades','fills','open_positions','equity_curve']:
            getattr(r,name).to_csv(out/f'{arm}_{name}.csv',index=False)
        if ma: r.signals.to_csv(out/'signals.csv')
        s=r.signals; scope=(frame.index+BAR>=start)&(frame.index+BAR<end)
        candidates=int(((s.entry_long|s.entry_short) if ma else (s.arrow_long|s.arrow_short))[scope].sum())
        curve=r.equity_curve.equity.to_numpy(float)
        row=dict(**metrics(r.trades), engine_stats=r.stats, entries=len(r.trades)+len(r.open_positions),
                 candidates=candidates, open_count=len(r.open_positions), final_equity=float(curve[-1]),
                 mtm_return_pct=float((curve[-1]/1000-1)*100),
                 mtm_max_drawdown_pct=float(-(curve/np.maximum.accumulate(curve)-1).min()*100))
        c=controls(frame,r.signals,r.trades,cfg); c.to_csv(out/f'{arm}_controls.csv',index=False)
        row['control']=control_stats(c,cfg)
        row['sides']={label:metrics(r.trades[r.trades.side==side]) for label,side in [('long',1),('short',-1)]}
        middle=start+(end-start)/2
        row['entry_time_halves']={label:metrics(r.trades[mask]) for label,mask in [
            ('earlier',pd.to_datetime(r.trades.entry_time,utc=True)<middle),
            ('later',pd.to_datetime(r.trades.entry_time,utc=True)>=middle)]}
        summary['arms'][arm]=row
        print(arm,json.dumps(row,ensure_ascii=False,default=str),flush=True)
    dump(out/'summary.json',summary)

if __name__=='__main__': main()
