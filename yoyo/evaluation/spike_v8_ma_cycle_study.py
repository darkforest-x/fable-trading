"""Causal six-MA cycle states joined to the frozen V8 same-entry ledger.

This diagnostic never rebuilds V6/V8 masks or changes execution. Bar features use
only completed current/past cache values. Future milestones are recorded only in
a separate availability/cost table and never relabel an earlier V8 entry.
"""
from __future__ import annotations
import argparse, hashlib, json, math, subprocess
from pathlib import Path
import numpy as np
import pandas as pd
from yoyo.evaluation.spike_exit_policy_study import load_verified_stream

EXP=Path('experiments/active/exp-spike-v8-ma-cycle-20260913-v1'); CONFIG=EXP/'config.json'; PLAN=EXP/'PROJECT_PLAN.md'; HOLDOUT=EXP/'holdout_receipt.json'
MA=('s20','e20','s60','e60','s120','e120')
def sha256(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def _clean(paths):
    root=Path.cwd().resolve()
    for p in paths:
        try:r=p.resolve().relative_to(root)
        except ValueError:return False
        if subprocess.run(['git','cat-file','-e',f'HEAD:{r}'],capture_output=True).returncode or subprocess.run(['git','diff','--quiet','HEAD','--',str(r)]).returncode:return False
    return True

def cycle_features(bars:pd.DataFrame,gap:pd.Series,minutes:int)->pd.DataFrame:
    """Build causal per-bar fields from OHLC/ATR/s20,e20,s60,e60,s120,e120 only."""
    if not bars.index.equals(gap.index): raise ValueError('unaligned cycle inputs')
    x=bars.loc[:,['open','high','low','close','atr',*MA]].apply(pd.to_numeric,errors='coerce'); n=len(x); a=x.to_numpy(float)
    cadence=np.ones(n,bool); cadence[1:]=np.diff(bars.index.asi8)==pd.Timedelta(minutes=minutes).value
    valid=np.isfinite(a).all(1)&(a[:,3]>0)&(a[:,4]>0)&(a[:,1]>=a[:,2])&(a[:,1]>=np.maximum(a[:,0],a[:,3]))&(a[:,2]<=np.minimum(a[:,0],a[:,3]))&cadence&~gap.to_numpy(bool); lines=a[:,5:]
    center=np.column_stack(((lines[:,0]+lines[:,1])/2,(lines[:,2]+lines[:,3])/2,(lines[:,4]+lines[:,5])/2))
    segment=np.cumsum(~valid); slope=np.full_like(center,np.nan); same3=np.zeros(n,bool); same3[3:]=segment[3:]==segment[:-3]; slope[same3]=center[same3]-center[np.flatnonzero(same3)-3]
    long_order=np.zeros(n,int); short_order=np.zeros(n,int)
    for l,r in ((0,1),(0,2),(1,2)):
        for li in (2*l,2*l+1):
            for ri in (2*r,2*r+1):
                long_order += (lines[:,li]>lines[:,ri]); short_order += (lines[:,li]<lines[:,ri])
    width=lines.max(1)-lines.min(1); frac=width/a[:,3]; ratio=a[:,4]/a[:,3]
    sf=pd.Series(frac,index=bars.index).where(valid); q=sf.groupby(segment).transform(lambda z:z.shift(1).rolling(256,min_periods=256).quantile(.20))
    rs=pd.Series(ratio,index=bars.index).where(valid); qs=[rs.groupby(segment).transform(lambda z:z.shift(1).rolling(256,min_periods=256).quantile(v)) for v in (.2,.4,.6,.8)]
    compact=(valid & (frac<=q.to_numpy(float))); bucket=np.full(n,-1,int)
    qq=np.column_stack([z.to_numpy(float) for z in qs]); have=np.isfinite(qq).all(1)&np.isfinite(ratio)
    bucket[have]=(ratio[have,None]>qq[have]).sum(1)
    prior_hi=pd.Series(a[:,1],index=bars.index).where(valid).groupby(segment).transform(lambda z:z.shift(1).rolling(12,min_periods=12).max()).to_numpy(float)
    prior_lo=pd.Series(a[:,2],index=bars.index).where(valid).groupby(segment).transform(lambda z:z.shift(1).rolling(12,min_periods=12).min()).to_numpy(float)
    outside_long=(a[:,0]>lines.max(1))&(a[:,3]>lines.max(1)); outside_short=(a[:,0]<lines.min(1))&(a[:,3]<lines.min(1))
    breakout_long=a[:,3]>prior_hi; breakout_short=a[:,3]<prior_lo
    out={k:np.empty(n,object) for k in ('state','transition')}; out['state'][:]='unknown'; out['transition'][:]=''
    for k in ('state_direction','state_age','episode_id','episode_age','launch_i','expansion_i','last_compact_i','order_run','slope_votes','causal_volatility_bucket'):
        out[k]=np.full(n,-1,int)
    for k in ('episode_atr','width_episode_atr','launch_range_low','launch_range_high','running_max_width') : out[k]=np.full(n,np.nan)
    state='unknown'; direction=0; state_age=-1; eid=-1; next_eid=0; epstart=-1; epatr=np.nan; launch=-1; expansion=-1; lastcompact=-1; rmax=np.nan; recon=0; crun=0; launch_low=np.nan; launch_high=np.nan
    prevstate=None
    for i in range(n):
        if not valid[i]:
            state='unknown'; direction=0; eid=-1; epstart=-1; epatr=np.nan; launch=-1; expansion=-1; lastcompact=-1; rmax=np.nan; recon=crun=0; launch_low=launch_high=np.nan; state_age=0; prevstate='unknown'
        else:
            consumed=False; trans=''
            if state=='launch':
                if (direction==1 and a[i,3]<=launch_high) or (direction==-1 and a[i,3]>=launch_low):
                    state='awaiting_compression'; direction=0; eid=-1; epstart=-1; epatr=np.nan; launch=-1; expansion=-1; lastcompact=-1; rmax=np.nan; recon=crun=0; launch_low=launch_high=np.nan; trans='failed_launch'; consumed=True
                else:
                    d=direction; gaps=(d*(center[i,0]-center[i,1])>0 and d*(center[i,1]-center[i,2])>0 and d*(center[i,0]-center[i,1])>d*(center[i-3,0]-center[i-3,1]) and d*(center[i,1]-center[i,2])>d*(center[i-3,1]-center[i-3,2])) if i>=3 and np.isfinite(center[i-3]).all() else False
                    score=long_order[i] if d==1 else short_order[i]
                    if score>=9 and gaps: state='expansion'; expansion=i; rmax=max(rmax,width[i]); trans='expansion'; consumed=True
            elif state=='expansion':
                rmax=max(rmax,width[i]); unanimous=(np.all(slope[i]>0) if direction==1 else np.all(slope[i]<0))
                recon = recon+1 if width[i] < .70*rmax and not unanimous else 0
                if recon>=3: state='reconsolidation'; direction=0; crun=0; launch_low=launch_high=np.nan; trans='reconsolidation'; consumed=True
            if state=='armed_consolidation' and not consumed:
                if i-lastcompact>12:
                    state='awaiting_compression'; direction=0; eid=-1; epstart=-1; epatr=np.nan; launch=expansion=-1; launch_low=launch_high=np.nan; rmax=np.nan; crun=0; trans='expired'; consumed=True
                else:
                    d=1 if np.all(slope[i]>0) else (-1 if np.all(slope[i]<0) else 0)
                    body=outside_long[i] if d==1 else (outside_short[i] if d==-1 else False)
                    br=breakout_long[i] if d==1 else (breakout_short[i] if d==-1 else False)
                    if d and body and br:
                        state='launch'; direction=d; launch=i; launch_low=prior_lo[i]; launch_high=prior_hi[i]; rmax=width[i]; trans='launch'; consumed=True
            if not consumed and state in ('unknown','awaiting_compression','reconsolidation'):
                crun=crun+1 if compact[i] else 0
                if crun>=3:
                    eid=next_eid; next_eid+=1; epstart=i; epatr=a[i,4]; lastcompact=i; launch=expansion=-1; launch_low=launch_high=np.nan; rmax=width[i]; direction=0; state='armed_consolidation'; trans='armed'; crun=0
            elif state=='armed_consolidation' and compact[i]: lastcompact=i
            if state==prevstate: state_age+=1
            else: state_age=0
            prevstate=state; out['transition'][i]=trans
        d=direction; score=long_order[i] if d==1 else (short_order[i] if d==-1 else 0)
        if d and i>0 and ((d==1 and long_order[i]==12) or (d==-1 and short_order[i]==12)):
            out['order_run'][i]=(out['order_run'][i-1]+1 if i else 1)
        else: out['order_run'][i]=0
        out['state'][i]=state; out['state_direction'][i]=d; out['state_age'][i]=state_age; out['episode_id'][i]=eid; out['episode_age'][i]=i-epstart if eid>=0 else -1
        out['episode_atr'][i]=epatr; out['width_episode_atr'][i]=width[i]/epatr if np.isfinite(epatr) and epatr>0 else np.nan; out['launch_i'][i]=launch; out['expansion_i'][i]=expansion; out['last_compact_i'][i]=lastcompact; out['running_max_width'][i]=rmax; out['launch_range_low'][i]=launch_low; out['launch_range_high'][i]=launch_high; out['slope_votes'][i]=int((slope[i]>0).sum())-int((slope[i]<0).sum()); out['causal_volatility_bucket'][i]=bucket[i]
    result=pd.DataFrame(out,index=bars.index); result['width_absolute']=width; result['width_close']=frac; result['width_atr']=width/a[:,4]; result['order_long']=long_order; result['order_short']=short_order; result['body_outside_long']=outside_long; result['body_outside_short']=outside_short; result['breakout_long']=breakout_long; result['breakout_short']=breakout_short; result['compression_threshold']=q; result['compression_qualified']=compact
    return result

def _event_state(events, context, feat):
    e=events.copy(); times=pd.to_datetime(e.signal_bar_open,utc=True); idx=feat.index.get_indexer(times)
    if (idx<0).any(): raise ValueError(f'event absent from cache {context.key}')
    f=feat.iloc[idx].reset_index(drop=True); e=e.reset_index(drop=True)
    for c in f.columns:e[c]=f[c].to_numpy()
    e['state_direction_match']=e.state_direction.eq(e.side); e['same_direction_launch']=e.state.eq('launch')&e.state_direction_match; e['same_direction_expansion']=e.state.eq('expansion')&e.state_direction_match
    e['state_age_bucket']=pd.cut(e.state_age,[-np.inf,2,6,12,np.inf],labels=['0-2','3-6','7-12','>12']).astype(str); e['calendar_month']=pd.to_datetime(e.entry_time,utc=True).dt.strftime('%Y-%m')
    return e

def _attach_trade_details(events, replay):
    """Left-join execution references; unexecuted V8 events deliberately remain."""
    key=str(events.stream_key.iloc[0]); t=pd.read_csv(replay/'streams'/f'{key}.trades.csv.gz')
    t=t.loc[t.arm.eq('v8'),['trade_id','entry_price','initial_stop','initial_risk']]
    if not t.trade_id.is_unique: raise ValueError(f'non-unique frozen V8 trade id: {key}')
    out=events.copy(); out['entry_price']=np.nan; out['initial_stop']=np.nan; out['initial_risk']=np.nan
    executed=out.trade_id.notna()
    if not out.loc[executed,'trade_id'].is_unique: raise ValueError(f'non-unique executed event id: {key}')
    detail=t.set_index('trade_id')
    for column in ('entry_price','initial_stop','initial_risk'):
        out.loc[executed,column]=out.loc[executed,'trade_id'].map(detail[column])
    return out

def _process(events, feat):
    """Emit independent same-episode expansion and order12 availability rows."""
    rows=[]
    key=str(events.stream_key.iloc[0]); idx=feat.index.get_indexer(pd.to_datetime(events.signal_bar_open,utc=True)); bars=feat.attrs['bars']
    for ev,i in zip(events.itertuples(index=False),idx):
        for target in ('expansion','order12'):
            ep=int(feat.episode_id.iloc[i]); side=int(ev.side); end=min(len(feat)-1,i+48); status='no_active_episode_at_confirmation' if ep<0 else 'no_milestone_48'; hit=-1; kind=''
            score=lambda j: int(feat.order_long.iloc[j] if side==1 else feat.order_short.iloc[j])
            is_target=lambda j: (feat.state.iloc[j]=='expansion') if target=='expansion' else (score(j)==12)
            ended=False
            if ep>=0 and int(feat.state_direction.iloc[i])==side and is_target(i): hit=i; kind=target; status='already_at_confirmation'
            elif ep>=0:
                for j in range(i+1,end+1):
                    if int(feat.episode_id.iloc[j])!=ep: ended=True; break
                    if int(feat.state_direction.iloc[j])==side and is_target(j): hit=j; kind=target; status='milestone'; break
            if hit<0 and ep>=0 and ended: status='episode_ended'
            elif hit<0 and ep>=0 and end==len(feat)-1: status='right_censored_end'
            confirm=feat.index[hit]+pd.Timedelta(minutes=int(ev.timeframe_min)) if hit>=0 else pd.NaT
            has_reference=bool(pd.notna(ev.entry_price) and pd.notna(ev.initial_stop) and pd.notna(ev.initial_risk) and float(ev.initial_risk)>0)
            exit_before=bool(hit>=0 and has_reference and pd.notna(ev.exit_time) and pd.Timestamp(ev.exit_time)<=confirm); entry_i=i+1; stop_before=False
            if hit>=0 and entry_i<=hit and pd.notna(ev.initial_stop):
                h=bars.high.iloc[entry_i:hit+1].to_numpy(float); l=bars.low.iloc[entry_i:hit+1].to_numpy(float); stop_before=bool((l<=ev.initial_stop).any() if side==1 else (h>=ev.initial_stop).any())
            nxt=hit+1
            next_ok=hit>=0 and nxt<len(feat) and feat.index[nxt]==confirm and not bool(feat.attrs['gap'].iloc[nxt])
            cost=(side*(float(bars.open.iloc[nxt])-float(ev.entry_price))/float(ev.initial_risk)) if next_ok and pd.notna(ev.initial_risk) else math.nan
            valid=bool(has_reference and hit>=0 and next_ok and not exit_before and not stop_before)
            rows.append({'trade_id':ev.trade_id,'stream_key':key,'signal_bar_open':ev.signal_bar_open,'period':ev.period,'side':side,'milestone_target':target,'episode_id_at_confirmation':ep,'status':status,'milestone_kind':kind,'milestone_bar_open':feat.index[hit] if hit>=0 else pd.NaT,'delay_bars':hit-i if hit>=0 else np.nan,'has_original_trade_reference':has_reference,'original_exit_before_milestone':exit_before,'original_stop_hit_before_milestone':stop_before,'next_open_available':next_ok,'confirmation_cost_r':cost,'valid_for_confirmation_diagnostic':valid})
    return pd.DataFrame(rows)

def _summary(events):
    rows=[]; closed=events.loc[events.scoring_closed].copy()
    for name,mask in [('baseline_all',pd.Series(True,index=closed.index)),('same_direction_launch',closed.same_direction_launch),('non_launch',~closed.same_direction_launch),('same_direction_expansion',closed.same_direction_expansion),('non_expansion',~closed.same_direction_expansion)]:
      for keys,p in closed.loc[mask].groupby(['period','timeframe_min','side'],dropna=False):
        wins=p.net_r.gt(0); gain=p.loc[wins,'net_r'].sum(); loss=-p.loc[p.net_r<0,'net_r'].sum(); base10=closed.loc[(closed.period==keys[0])&(closed.timeframe_min==keys[1])&(closed.side==keys[2]),'net_r'].ge(10).sum()
        rows.append({'period':keys[0],'timeframe_min':keys[1],'side':keys[2],'comparison':name,'n':len(p),'win_rate':wins.mean(),'profit_factor':gain/loss if loss else math.inf,'mean_r':p.net_r.mean(),'net_r':p.net_r.sum(),'realized_ge_10r':int(p.net_r.ge(10).sum()),'original_10r_retention':int(p.net_r.ge(10).sum())/base10 if base10 else np.nan,'loss_p05_r':p.net_r.quantile(.05)})
    return pd.DataFrame(rows)

def _state_summary(events, monthly=False):
    """Describe every observed state; only scoring_closed rows carry P/L fields."""
    e=events.loc[events.scoring_closed].copy(); keys=['period','timeframe_min','side','state','state_direction_match']+(['calendar_month'] if monthly else []); rows=[]
    for values,p in e.groupby(keys,dropna=False):
        gain=p.loc[p.net_r>0,'net_r'].sum(); loss=-p.loc[p.net_r<0,'net_r'].sum()
        rows.append(dict(zip(keys,values))|{'n':len(p),'win_rate':p.net_r.gt(0).mean(),'profit_factor':gain/loss if loss else math.inf,'mean_r':p.net_r.mean(),'net_r':p.net_r.sum(),'realized_ge_10r':int(p.net_r.ge(10).sum()),'loss_p05_r':p.net_r.quantile(.05)})
    return pd.DataFrame(rows)

def _pairs(events, flag):
    e=events.loc[events.scoring_closed].copy(); e['target']=e[flag]; out=[]
    for _,p in e.groupby(['stream_key','period','calendar_month','side','causal_volatility_bucket'],dropna=False):
        targets=p.loc[p.target].sort_values(['signal_confirm_time','trade_id']); controls=p.loc[~p.target].copy()
        for t in targets.itertuples(index=False):
            if t.causal_volatility_bucket < 0: out.append({'comparison':flag,'target_trade_id':t.trade_id,'matched':False,'missing_reason':'insufficient_causal_volatility_history'}); continue
            if controls.empty: out.append({'comparison':flag,'target_trade_id':t.trade_id,'matched':False,'missing_reason':'no_non_target_in_exact_match_block'}); continue
            q=controls.assign(_d=(pd.to_datetime(controls.signal_confirm_time,utc=True)-pd.Timestamp(t.signal_confirm_time)).abs()).sort_values(['_d','trade_id']).iloc[0]
            out.append({'comparison':flag,'target_trade_id':t.trade_id,'control_trade_id':q.trade_id,'matched':True,'period':t.period,'timeframe_min':t.timeframe_min,'side':t.side,'causal_volatility_bucket':t.causal_volatility_bucket,'target_signal_confirm_time':t.signal_confirm_time,'control_signal_confirm_time':q.signal_confirm_time,'target_net_r':t.net_r,'control_net_r':q.net_r,'difference_r':t.net_r-q.net_r})
            controls=controls.loc[controls.trade_id.ne(q.trade_id)]
    return pd.DataFrame(out)

def run(output:Path,official=False):
    cfg=json.loads(CONFIG.read_text()); entry=Path(cfg['entry_process']); manifest=entry/'manifest.json'; evidence=entry/'same_entry_evidence.csv.gz'; raw=Path(cfg['raw']); replay=Path(cfg['v8_replay'])
    raw_manifest=raw/'manifest.json'; replay_manifest=replay/'manifest.json'
    if sha256(manifest)!=cfg['entry_process_manifest_sha256'] or sha256(evidence)!=cfg['entry_process_evidence_sha256']:raise ValueError('frozen entry-process input changed')
    if sha256(raw_manifest)!=cfg['raw_manifest_sha256'] or sha256(replay_manifest)!=cfg['v8_replay_manifest_sha256']:raise ValueError('frozen cache or V8 replay manifest changed')
    dependency=Path(__file__).with_name('spike_exit_policy_study.py')
    if official and (not _clean((Path(__file__),CONFIG,PLAN,HOLDOUT,dependency)) or EXP not in output.parents):raise ValueError('official requires committed clean source and experiment output')
    events=pd.read_csv(evidence); 
    if len(events)!=cfg['expected_v8_signals'] or int(events.scoring_closed.sum())!=cfg['expected_scoring_closed']:raise ValueError('frozen event universe changed')
    parts=[]; process_parts=[]; comp_windows=[]
    folders=sorted(p for p in (raw/'streams').iterdir() if (p/'completion.json').is_file())
    if len(folders)!=cfg['expected_streams']:raise ValueError('stream count changed')
    for no,folder in enumerate(folders,1):
        c=load_verified_stream(folder); f=cycle_features(c.cache['bars'],c.cache['data_gap'],c.minutes); f.attrs['bars']=c.cache['bars']; f.attrs['gap']=c.cache['data_gap']; subset=events.loc[events.stream_key.eq(c.key)]
        if len(subset):
            stream_events=_attach_trade_details(_event_state(subset,c,f),replay); parts.append(stream_events); process_parts.append(_process(stream_events,f))
        if c.identity['symbol']=='COMP-USDT-SWAP' and c.minutes==60:
            w=f.loc['2026-07-22T00:00:00Z':'2026-08-04T00:00:00Z'].copy(); w.insert(0,'stream_key',c.key); comp_windows.append(w.reset_index(names='bar_open'))
        if no%100==0: print(json.dumps({'streams':no,'target':len(folders)}),flush=True)
    state=pd.concat(parts,ignore_index=True); process=pd.concat(process_parts,ignore_index=True); summary=_summary(state); state_summary=_state_summary(state); monthly_state_summary=_state_summary(state,monthly=True); pairs=pd.concat([_pairs(state,'same_direction_launch'),_pairs(state,'same_direction_expansion')],ignore_index=True)
    output.mkdir(parents=True,exist_ok=False); state.to_csv(output/'event_states.csv.gz',index=False,compression={'method':'gzip','mtime':0}); process.to_csv(output/'process_availability.csv.gz',index=False,compression={'method':'gzip','mtime':0}); summary.to_csv(output/'outcome_summary.csv',index=False); state_summary.to_csv(output/'outcome_state_summary.csv',index=False); monthly_state_summary.to_csv(output/'monthly_state_summary.csv',index=False); pairs.to_csv(output/'same_stream_month_side_volatility_pairs.csv',index=False)
    existing=pd.read_csv(entry/'existing_random_controls.csv'); labels=state[['stream_key','signal_bar_open','side','period','state','state_direction_match','same_direction_launch','same_direction_expansion']].copy(); labels.signal_bar_open=pd.to_datetime(labels.signal_bar_open,utc=True); existing.signal_bar_open=pd.to_datetime(existing.signal_bar_open,utc=True); existing=existing.merge(labels,on=['stream_key','signal_bar_open','side','period'],how='left',validate='many_to_one'); existing.to_csv(output/'existing_random_controls_reused.csv',index=False)
    pd.concat(comp_windows,ignore_index=True).to_csv(output/'comp_1h_cycle_window.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    identity={str(p):sha256(p) for p in (Path(__file__),CONFIG,PLAN,HOLDOUT,dependency,manifest,evidence,raw_manifest,replay_manifest)}; (output/'manifest.json').write_text(json.dumps({'complete':True,'official':official,'source':identity,'events':len(state),'scoring_closed':int(state.scoring_closed.sum()),'streams_scanned':len(folders),'process':'same-episode future availability/cost only; no earnings strata','random_control_limitation':'frozen existing controls lack control exit clock'},indent=2)); (output/'receipt.json').write_text(json.dumps({p.name:sha256(p) for p in output.iterdir() if p.is_file()},indent=2))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--official',action='store_true');a=p.parse_args();run(a.output,a.official)
