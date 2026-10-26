"""Owner-authorized confirmed entry, pin stop and Monday keypoint targets.

Reuse frozen admitted events/scores and exact five target prices from the
original order ledger, including mirrored and scaled-control geometry.
Only targets change; same pin stops, entry fills, BE policies and costs.
A fixed100USDT planned loss is an illustrative linear research unit, not an
account or live sizing instruction. Rank2025 mean budget-normalized PnL per
resolved opportunity, including pre-entry skips as zero; check2026 unchanged.
"""
from concurrent.futures import ProcessPoolExecutor,as_completed
import argparse
import json
from pathlib import Path
import subprocess
import time
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_wick_r_study import dump,stamp
from yoyo.evaluation.monday_confirmed_r_study import EXCLUDED
from yoyo.evaluation.monday_confirmed_keypoints_engine import TARGETS,planned_loss

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-monday-5m-confirmed-keypoints-20261026-v1'
SRC=ROOT/'experiments/active/exp-monday-5m-confirmed-score-20261026-v1/symbols'
ORDERS=ROOT/'experiments/active/exp-monday-limit-reclaim-20261024-v1/symbols'
CODE=['yoyo/evaluation/monday_confirmed_keypoints_study.py','yoyo/evaluation/monday_confirmed_keypoints_engine.py','yoyo/evaluation/monday_wick_r_engine.py']
KEYS=['event_id','population','control_id']


def target_matrix(events,orders):
    """Exact frozen target prices; no rederived tick precision or new controls."""
    o=orders[orders.reclaim_minutes.eq(5)].copy()
    assert not o.duplicated(KEYS+['exit_mode']).any()
    key=pd.MultiIndex.from_frame(events[KEYS]);counts=o.groupby(KEYS).size().reindex(key)
    assert counts.eq(5).all(),'Every admitted event needs its original five target orders'
    prices=o.pivot(index=KEYS,columns='exit_mode',values='target_price').reindex(key)
    return prices[TARGETS].to_numpy(float)


def audit_accounting(t):
    c=t[t.closed]
    np.testing.assert_allclose(c.net_bp,c.side*(c.exit_price-c.entry)/c.entry*10000-c.fee_bp,atol=1e-7)
    np.testing.assert_allclose(c.budget_r,c.net_bp/10000*c.entry/c.planned_loss_unit,atol=1e-7)
    np.testing.assert_allclose(c.pnl_usdt,c.budget_r*c.budget_usdt,atol=1e-7)
    np.testing.assert_allclose(c.fee_bp,5+c.exit_price/c.entry*np.where(c.reason.eq(4),2,5),atol=1e-7)
    assert t.loc[~t.entered,'quantity'].eq(0).all()
    assert t.loc[~t.entered,'pnl_usdt'].eq(0).all()
    assert (c.exit_i>=c.entry_i).all()


def process(symbol):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.evaluation.monday_confirmed_keypoints_engine import execute
    import vectorbt
    cfg=json.loads((EXP/'config.json').read_text());job=json.loads((ORDERS/symbol/'identity.json').read_text())['job']
    dest=EXP/'symbols'/symbol;dest.mkdir(parents=True,exist_ok=True)
    identity=dict(inputs=[stamp(p) for p in job['paths']],events=stamp(SRC/symbol/'events.parquet'),
        targets=stamp(ORDERS/symbol/'orders.csv.gz'),code=[stamp(ROOT/p) for p in CODE],config=cfg,engine=vectorbt.__version__)
    if (dest/'done.json').exists():
        assert json.loads((dest/'identity.json').read_text())==identity,'Frozen input or code changed'
        return json.loads((dest/'done.json').read_text())
    start=time.monotonic();events=pd.read_parquet(SRC/symbol/'events.parquet')
    if len(events):
        orders=pd.read_csv(ORDERS/symbol/'orders.csv.gz',usecols=KEYS+['reclaim_minutes','exit_mode','target_price'])
        targets=target_matrix(events,orders);frame,quality=load_job(job)
        t,fills=execute(frame,events,targets,cfg['maker_fee'],cfg['taker_fee'],cfg['market_slippage'],job['execution_minutes'],cfg['illustrative_risk_budget_usdt'])
        audit_accounting(t)
        assert len(t)==10*len(events) and len(fills)==2*int(t.entered.sum())
    else:t,fills=pd.DataFrame(),pd.DataFrame()
    t.to_parquet(dest/'trades.parquet',index=False);fills.to_parquet(dest/'fills.parquet',index=False)
    done=dict(symbol=symbol,events=len(events),opportunity_columns=len(t),executed_columns=int(t.entered.sum()) if len(t) else 0,
        skipped_columns=int((~t.entered).sum()) if len(t) else 0,fills=len(fills),seconds=time.monotonic()-start)
    dump(dest/'identity.json',identity);dump(dest/'done.json',done);return done


def run(workers=2):
    for p in CODE+[str((EXP/'config.json').relative_to(ROOT))]:
        if subprocess.run(['git','diff','--quiet','HEAD','--',p],cwd=ROOT).returncode or subprocess.run(
            ['git','ls-files','--error-unmatch',p],cwd=ROOT,capture_output=True).returncode:raise RuntimeError('Commit builder before execution')
    syms=[p.parent.name for p in sorted(SRC.glob('*/events.parquet'))]
    dump(EXP/'run_manifest.json',dict(symbols=syms,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    failures=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        fut={pool.submit(process,s):s for s in syms}
        for i,f in enumerate(as_completed(fut),1):
            try:print(json.dumps(dict(progress=f'{i}/{len(syms)}',**f.result())),flush=True)
            except Exception as exc:
                d=dict(symbol=fut[f],error=repr(exc));failures.append(d);dump(EXP/'symbols'/fut[f]/'error.json',d);print(json.dumps(d),flush=True)
    dump(EXP/'execution.json',dict(expected=len(syms),failures=failures,complete=not failures))
    if failures:raise RuntimeError(str(failures))


def metrics(g):
    c=g[g.closed];res=g[g.resolved];entered=g[g.entered];v=c.budget_r
    return dict(n=len(g),entered_n=len(entered),closed_n=len(c),skipped_n=int((~g.entered).sum()),censored_n=int((g.entered&~g.closed).sum()),
        mean_bp=float(c.net_bp.mean()),mean_r=float(c.net_r.mean()),mean_budget_r=float(v.mean()),
        opportunity_budget_r=float(res.budget_r.mean()),sum_budget_r=float(v.sum()),
        win_pct=float(100*(v>1e-8).mean()),risk_pf=float(v[v>0].sum()/-v[v<0].sum()) if (v<0).any() else np.nan,
        tp_n=int(c.reason.eq(4).sum()),sl_n=int(c.reason.eq(1).sum()),be_n=int(c.reason.eq(5).sum()),be_armed_n=int(g.be_armed.sum()),
        ambiguous_n=int(g.ambiguous.sum()),median_notional_per100risk=float(entered.notional.median()),
        median_geometric_stop_pct=float(entered.risk_pct.median()),median_planned_loss_pct=float(entered.planned_loss_pct.median()),
        median_target_r=float(entered.target_r.median()),median_hold_min=float(((c.exit_ns-c.entry_ns)/60_000_000_000).median()),
        p99_hold_min=float(((c.exit_ns-c.entry_ns)/60_000_000_000).quantile(.99)),
        sl_mean_budget_r=float(c.loc[c.reason.eq(1),'budget_r'].mean()))


def table(t,scope):
    rows=[];cut=pd.Timestamp('2026-01-01',tz='UTC').value;train=pd.Timestamp('2025-01-01',tz='UTC').value
    directions=[('long',[1]),('short',[-1]),('combined',[1,-1])] if scope=='keypoints' else [('long',[1])]
    for direction,sides in directions:
        z=t[t.side.isin(sides)&~t.symbol.isin(EXCLUDED)]
        for period in ['all','pre2025','select2025','check2026']:
            mask={'all':np.ones(len(z),bool),'pre2025':(z.entry_ns<train)&(z.exit_ns<train),
                'select2025':(z.entry_ns>=train)&(z.entry_ns<cut)&(z.exit_ns<cut),'check2026':z.entry_ns>=cut}[period]
            pp=z[mask]
            for filt in ['all','score_gate']:
                sig=pp[pp.population.eq('signal')]
                if filt=='score_gate':sig=sig[sig.score.ge(.22621664169752762)]
                for (name,be),sg in sig.groupby(['target_name','be_mode']):
                    m=metrics(sg)
                    ctrl=pp[pp.population.eq('random')&pp.event_id.isin(sg.event_id)&pp.target_name.eq(name)&pp.be_mode.eq(be)&pp.resolved]
                    cc=ctrl.groupby('event_id').budget_r.agg(['mean','count'])
                    pair=sg[sg.resolved].merge(cc,left_on='event_id',right_index=True);full=pair[pair['count']==3]
                    m.update(scope=scope,direction=direction,period=period,filter=filt,target_name=name,be_mode=be,
                        matched_n=len(pair),matched_signal_budget_r=float(pair.budget_r.mean()),matched_random_budget_r=float(pair['mean'].mean()),
                        excess_budget_r=float((pair.budget_r-pair['mean']).mean()),full3_n=len(full),
                        full3_excess_budget_r=float((full.budget_r-full['mean']).mean()))
                    rows.append(m)
    return pd.DataFrame(rows)


def read_cached_r():
    """Linear same-risk reweighting of unchanged native baseline fills, no replay."""
    cols=['event_id','symbol','week','side','population','entry_ns','exit_ns','closed','target_r','be_mode','score',
        'entry','stop','risk','risk_pct','net_bp','net_r','reason','be_armed','ambiguous']
    frames=[]
    for p in sorted(SRC.glob('*/trades.parquet')):
        if not json.loads((p.parent/'done.json').read_text())['columns']:continue
        f=pd.read_parquet(p,columns=cols);f=f[f.side.eq(1)&~f.symbol.isin(EXCLUDED)];frames.append(f)
    t=pd.concat(frames,ignore_index=True);t['target_name']='R_'+t.target_r.astype(str)
    t['planned_loss_unit']=planned_loss(t.entry,t.stop,t.side)
    t['planned_loss_pct']=100*t.planned_loss_unit/t.entry
    t['budget_r']=t.net_bp/10000*t.entry/t.planned_loss_unit;t['notional']=100*t.entry/t.planned_loss_unit
    t['entered']=True;t['resolved']=t.closed
    return t


def summarize():
    cols=['event_id','symbol','week','side','population','entry_ns','exit_ns','closed','resolved','entered','target_name','be_mode','score',
        'target_r','risk_pct','planned_loss_pct','notional','net_bp','net_r','budget_r','reason','be_armed','ambiguous']
    frames=[];done=[]
    for p in sorted((EXP/'symbols').glob('*/done.json')):
        d=json.loads(p.read_text());done.append(d)
        if d['opportunity_columns']:frames.append(pd.read_parquet(p.parent/'trades.parquet',columns=cols))
    t=pd.concat(frames,ignore_index=True);res=table(t,'keypoints');base=table(read_cached_r(),'fixed_r_cache')
    res.to_csv(EXP/'metrics.csv',index=False);base.to_csv(EXP/'baseline_fixed_r.csv',index=False)
    choices=[]
    for (scope,direction,filt),g in pd.concat([res,base]).groupby(['scope','direction','filter']):
        selected=g[g.period.eq('select2025')].sort_values(['opportunity_budget_r','target_name'],ascending=[False,True]).iloc[0]
        full=g[g.period.eq('all')].sort_values(['opportunity_budget_r','target_name'],ascending=[False,True]).iloc[0]
        same=g.target_name.eq(selected.target_name)&g.be_mode.eq(selected.be_mode)
        choices.append(dict(scope=scope,direction=direction,filter=filt,selected2025=selected.to_dict(),evaluation=g[same].to_dict('records'),full_best_descriptive=full.to_dict()))
    # Block bootstrap on selected2025 keypoint parameters, never reselect2026.
    checks=[];cut=pd.Timestamp('2026-01-01',tz='UTC').value
    for choice in choices:
        if choice['scope']!='keypoints' or choice['direction']!='long':continue
        sel=choice['selected2025'];filt=choice['filter']
        z=t[t.side.eq(1)&~t.symbol.isin(EXCLUDED)&t.target_name.eq(sel['target_name'])&t.be_mode.eq(sel['be_mode'])&t.entry_ns.ge(cut)&t.resolved]
        sg=z[z.population.eq('signal')]
        if filt=='score_gate':sg=sg[sg.score.ge(.22621664169752762)]
        cc=z[z.population.eq('random')].groupby('event_id').budget_r.agg(['mean','count']);pair=sg.merge(cc,left_on='event_id',right_index=True);pair['delta']=pair.budget_r-pair['mean']
        rr=dict(filter=filt,target_name=sel['target_name'],be_mode=sel['be_mode'],n=len(sg),matched_n=len(pair))
        for label,frame,col in [('absolute',sg,'budget_r'),('matched_excess',pair,'delta')]:
            w=frame.groupby('week')[col].agg(['sum','count'])
            if len(w):
                rng=np.random.default_rng(20261026);idx=rng.integers(len(w),size=(3000,len(w)))
                boot=w['sum'].to_numpy()[idx].sum(1)/w['count'].to_numpy()[idx].sum(1)
                rr[label]=dict(weeks=len(w),mean=float(frame[col].mean()),ci95=np.quantile(boot,[.025,.975]).tolist())
        checks.append(rr)
    dump(EXP/'summary.json',dict(choices=choices,bootstrap2026=checks,symbols=len(done),events=sum(d['events'] for d in done),
        opportunity_columns=len(t),executed_columns=sum(d['executed_columns'] for d in done),fills=sum(d['fills'] for d in done),
        primary='Fixed planned100USDT loss per independent entry, including known fees and10bp stop slippage. Rank2025 mean budgetR per resolved opportunity; pre-entry skipped targets count0, censored remain missing.',
        limitations='Not a cash account; no funding, margin, liquidation, capital constraints or depth capacity. Historical scores approximate live venue; early years overlap original model fit. Targets already passed or without fee space skipped causally.',
        production_eligible=False,training_eligible=False))
    print(json.dumps([c for c in choices if c['direction']=='long' and c['filter']=='score_gate'],indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['run','summarize']);p.add_argument('--workers',type=int,default=2);a=p.parse_args()
    run(a.workers) if a.action=='run' else summarize()
