"""Describe original ETH15m ICT paths without evaluating a new exit strategy.

Trade windows come from committed original ledgers. ATR uses current/prior OHLC
via the frozen features builder. Only fully held bars before the exit bar enter
intrabar MFE; observed exit fills enter a separately labelled peak lower bound.
An intrabar exit bar gives only an upper bound because its later extrema may be
post-exit. All statistics are retrospective labels, never trading features.
"""
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.spike_fanshen_prefix import read_prefix
from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_recovery_exit import _round_frozen_trail
from yoyo.evaluation.spike_fanshen_study import sha, save

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-spike-v8-ict-winner-peaks-20260915-v1'
LEVELS=[1,2,3,4,5,6,8,10,15,20]
BUILDERS=['yoyo/evaluation/spike_v8_winner_peaks.py','yoyo/data/spike_fanshen_prefix.py',
 'yoyo/data/release_eth_prefix.py','yoyo/evaluation/spike_burst_replay.py',
 'yoyo/evaluation/spike_recovery_exit.py','yoyo/evaluation/spike_fanshen_study.py',
 str((EXP/'config.json').relative_to(ROOT)),str((EXP/'PROJECT_PLAN.md').relative_to(ROOT))]


def profile_trade(frame, row):
    """Use only held bars; report unknown stop-bar order instead of inventing it."""
    entry,exit_i=int(row['entry_i']),int(row['exit_i'])
    side=int(row['side']);price=float(row['entry_price']);risk=float(row['initial_risk'])
    assert not row['censored'] and 0<=entry<=exit_i<len(frame)
    assert frame.index[entry]==pd.Timestamp(row['entry_time'])
    assert frame.index[exit_i]==pd.Timestamp(row['exit_time'])
    assert abs(float(frame.open.iloc[entry])-price)<1e-8
    if row['exit_at_open']:
        assert abs(float(frame.open.iloc[exit_i])-float(row['exit_price']))<1e-8
    bars=frame.iloc[entry:exit_i]
    fav=side*(bars['high' if side==1 else 'low'].to_numpy()-price)/risk
    adv=side*(bars['low' if side==1 else 'high'].to_numpy()-price)/risk
    closes=side*(bars.close.to_numpy()-price)/risk
    legacy=max(0.,float(fav.max())) if len(fav) else 0.
    assert abs(legacy-float(row['mfe_r']))<1e-8,'legacy MFE mismatch'
    gross=float(row['gross_r']);cost=float(row['cost_r']);net=float(row['net_r'])
    assert abs(gross-cost-net)<1e-9
    peak=max(legacy,gross,0.)
    terminal=frame.iloc[exit_i]
    upper=peak if row['exit_at_open'] else max(peak,side*(terminal['high' if side==1 else 'low']-price)/risk)
    peak_j=int(np.argmax(fav)) if len(fav) and legacy>0 and legacy>=gross else None
    peak_at=frame.index[entry+peak_j] if peak_j is not None else frame.index[exit_i if gross>0 else entry]
    trace=[];armed=False;stop=float(row['initial_stop']);running=0.;arm_j=None
    for j,(_,bar) in enumerate(bars.iterrows()):
        if closes[j]>=2 and not armed:arm_j=j;armed=True
        if armed:
            candidate=_round_frozen_trail(bar.close-side*4*bar.atr,side=side,tick=.01)
            stop=max(stop,candidate) if side==1 else min(stop,candidate)
        running=max(running,float(fav[j]))
        trace.append(dict(signal_i=int(row['signal_i']),bar_open=str(bars.index[j]),
            minutes_from_entry=j*15,favorable_r=fav[j],adverse_r=adv[j],close_r=closes[j],
            atr_r=bar.atr/risk,peak_so_far_r=running,protection_next_bar_r=side*(stop-price)/risk,
            trail_armed=armed))
    assert abs(stop-float(row['final_protection']))<1e-7,'original protection mismatch'
    if not row['exit_at_open'] and 'stop' in row['exit_reason']:
        assert abs(stop-float(row['exit_price']))<1e-7
    lower_dd=upper_dd=np.nan
    if arm_j is not None and peak_j is not None and peak_j>arm_j:
        prior_peak=max(0.,float(fav[:arm_j+1].max()));lower_dd=upper_dd=0.
        # Complete bars strictly before the peak bar; the peak bar's low/high
        # may occur after the peak, so it cannot establish pre-peak drawdown.
        for j in range(arm_j+1,peak_j):
            lower_dd=max(lower_dd,prior_peak-adv[j],0.)
            upper_dd=max(upper_dd,max(prior_peak,fav[j])-adv[j],0.)
            prior_peak=max(prior_peak,fav[j])
    r={k:row[k] for k in ['signal_i','entry_i','entry_time','exit_i','exit_time','side','entry_price',
        'initial_stop','initial_risk','exit_price','exit_reason','exit_at_open','gross_r','net_r','cost_r','mfe_r']}
    r.update(winner=net>1e-9,peak_known_gross_r=peak,peak_known_net_r=peak-cost,
        peak_possible_upper_r=upper,peak_exit_bar_uncertain=upper>peak+1e-9,
        peak_bar_open=str(peak_at),peak_is_intrabar=peak_j is not None,
        minutes_to_peak_lower=(peak_at-frame.index[entry]).total_seconds()/60,
        holding_minutes_lower=(frame.index[exit_i]-frame.index[entry]).total_seconds()/60,
        peak_to_exit_minutes_lower=(frame.index[exit_i]-peak_at).total_seconds()/60,
        max_closed_bar_r=max(0.,float(closes.max())) if len(closes) else 0.,
        giveback_r=peak-gross,gross_capture=gross/peak if peak>0 else np.nan,
        net_capture=net/(peak-cost) if peak>cost else np.nan,
        arm_bar_open=str(bars.index[arm_j]) if arm_j is not None else None,
        pre_peak_pullback_known_r=lower_dd,pre_peak_pullback_possible_r=upper_dd,
        peak_bar_close_r=closes[peak_j] if peak_j is not None else np.nan,
        peak_bar_4atr_r=4*bars.atr.iloc[peak_j]/risk if peak_j is not None else np.nan,
        peak_bar_protection_next_r=trace[peak_j]['protection_next_bar_r'] if peak_j is not None else np.nan)
    for level in LEVELS:
        hits=np.flatnonzero(fav>=level)
        r[f'hit{level}_minutes_lower']=int(hits[0])*15 if len(hits) else (r['holding_minutes_lower'] if gross>=level else np.nan)
    return r,trace


def summarize(df):
    """Winners are an outcome-conditioned diagnostic cohort, not a new edge."""
    winners=df.loc[df.winner];q=winners.peak_known_gross_r.quantile([.1,.25,.5,.75,.9])
    return dict(n=len(df),wins=len(winners),losses=int((df.net_r < -1e-9).sum()),net_r=float(df.net_r.sum()),
        winner_peak_mean=float(winners.peak_known_gross_r.mean()),winner_peak_min=float(winners.peak_known_gross_r.min()),
        winner_peak_max=float(winners.peak_known_gross_r.max()),**{f'winner_peak_p{int(k*100)}':float(v) for k,v in q.items()},
        winner_gross_mean=float(winners.gross_r.mean()),winner_net_mean=float(winners.net_r.mean()),
        winner_net_median=float(winners.net_r.median()),winner_giveback_mean=float(winners.giveback_r.mean()),
        winner_giveback_median=float(winners.giveback_r.median()),winner_giveback_max=float(winners.giveback_r.max()),
        winner_gross_capture_weighted=float(winners.gross_r.sum()/winners.peak_known_gross_r.sum()),
        winner_net_capture_weighted=float(winners.net_r.sum()/winners.peak_known_net_r.sum()),
        winner_net_total=float(winners.net_r.sum()),winner_top1_net=float(winners.nlargest(1,'net_r').net_r.sum()),
        winner_top3_net=float(winners.nlargest(3,'net_r').net_r.sum()),
        winner_hold_median_minutes=float(winners.holding_minutes_lower.median()),
        winner_peak_time_median_minutes=float(winners.minutes_to_peak_lower.median()),
        uncertain_peak_n=int(df.peak_exit_bar_uncertain.sum()),uncertain_winner_peak_n=int(winners.peak_exit_bar_uncertain.sum()))


def threshold_table(df,window):
    rows=[]
    for basis in ['gross','net']:
        peaks=df['peak_known_'+basis+'_r'];upper=df.peak_possible_upper_r-(df.cost_r if basis=='net' else 0)
        for k,level in enumerate(LEVELS):
            hit=df.loc[peaks>=level];next_level=LEVELS[k+1] if k+1<len(LEVELS) else np.nan
            rows.append(dict(window=window,basis=basis,level=level,reached=len(hit),possible_only=int(((upper>=level)&(peaks<level)).sum()),
                wins=int(hit.winner.sum()),losses=int((hit.net_r < -1e-9).sum()),win_fraction=float(hit.winner.mean()) if len(hit) else np.nan,
                mean_final_net_r=float(hit.net_r.mean()),median_final_net_r=float(hit.net_r.median()),
                mean_giveback_r=float(hit.giveback_r.mean()),next_level=next_level,
                reached_next=int((peaks.loc[hit.index]>=next_level).sum()) if pd.notna(next_level) else np.nan,
                next_fraction=float((peaks.loc[hit.index]>=next_level).mean()) if len(hit) and pd.notna(next_level) else np.nan))
    return rows


def run():
    cfg=json.loads((EXP/'config.json').read_text());old=ROOT/cfg['previous_experiment']
    for rel in BUILDERS:
        subprocess.run(['git','cat-file','-e','HEAD:'+rel],cwd=ROOT,check=True)
        subprocess.run(['git','diff','--exit-code','HEAD','--',rel],cwd=ROOT,check=True)
    out=EXP/'results';out.mkdir(exist_ok=False)
    receipt=dict(builder_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        builders={rel:sha(ROOT/rel) for rel in BUILDERS},holdout_consumed=False,holdout_consumption_number=0,
        end=cfg['end'],new_exit_policies_evaluated=0)
    save(out/'read_receipt.json',receipt)
    inputs={};ledgers={}
    for window,rel in cfg['ledgers'].items():
        f=old/rel;phase=f.parent;manifest=phase/'manifest.json'
        assert sha(manifest)==cfg['manifests'][str(manifest.relative_to(old))]
        expected=json.loads(manifest.read_text())['files'][f.name];assert sha(f)==expected
        inputs[str(f.relative_to(ROOT))]=expected;df=pd.read_csv(f)
        assert not df.signal_i.duplicated().any() and not df.censored.any()
        assert (pd.to_datetime(df.exit_time,utc=True)<pd.Timestamp(cfg['end'])).all()
        ledgers[window]=df
    bars,prefix=read_prefix(ROOT/cfg['source'],15,cfg['end'])
    assert prefix['prefix_sha256']==cfg['expected_prefix_sha256']
    frame=features(bars);save(out/'input_receipt.json',dict(**prefix,ledgers=inputs))
    cache={};summary=[];thresholds=[];alltraces={};allprofiles={};strata=[]
    for window,df in ledgers.items():
        rows=[]
        for row in df.to_dict('records'):
            key=(int(row['signal_i']),int(row['exit_i']),row['exit_price'])
            if key not in cache:cache[key]=profile_trade(frame,row)
            profile,trace=cache[key];rows.append(profile)
            if window=='available':alltraces[int(row['signal_i'])]=trace
        profiles=pd.DataFrame(rows);allprofiles[window]=profiles
        profiles.to_csv(out/f'{window}_profiles.csv',index=False)
        profiles.loc[profiles.winner].sort_values('peak_known_gross_r',ascending=False).to_csv(out/f'{window}_winners.csv',index=False)
        summary.append(dict(window=window,**summarize(profiles)));thresholds+=threshold_table(profiles,window)
        for direction,group in profiles.groupby('side'):
            if group.winner.any():strata.append(dict(window=window,segment='long' if direction==1 else 'short',**summarize(group)))
        print(window,len(profiles),'wins',int(profiles.winner.sum()),'peakmax',round(profiles.loc[profiles.winner,'peak_known_gross_r'].max(),4),flush=True)
    pd.DataFrame(summary).to_csv(out/'summary.csv',index=False);pd.DataFrame(thresholds).to_csv(out/'thresholds.csv',index=False)
    pd.DataFrame(strata).to_csv(out/'direction_summary.csv',index=False)
    pd.DataFrame([r for rows in alltraces.values() for r in rows]).to_csv(out/'available_held_bar_paths.csv.gz',index=False)
    # Only reuse matching results from the previous experiment, with hash identity.
    baseline=[]
    for phase in ['develop','evaluate']:
        f=old/'results'/phase/'summary.csv';man=json.loads((f.parent/'manifest.json').read_text())
        assert sha(f)==man['files'][f.name]
        d=pd.read_csv(f);baseline.append(d.loc[d.policy=='original'])
    pd.concat(baseline).to_csv(out/'frozen_original_and_random_baselines.csv',index=False)
    save(out/'manifest.json',dict(**receipt,unique_trade_paths=len(cache),files={f.name:sha(f) for f in sorted(out.iterdir()) if f.is_file()}))
    print('COMPLETE unique paths',len(cache),flush=True)


if __name__=='__main__':run()
