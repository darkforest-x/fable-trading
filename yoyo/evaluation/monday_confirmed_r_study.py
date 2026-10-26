"""Confirmed5m entry with entry-known pin risk and frozen paper-score gate.

Owner2026-10-26 authorized delayed market entry, pin-low risk and score filter.
Signals and matched random fills are reused, not regenerated. Candle polarity
and wick geometry are known before entry. Score uses pre-touch features and
frozen pre2025 coefficients. Controls follow the selected signal identities;
their own pin/confirmation rules apply, without outcome-based replacement.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
import json
from pathlib import Path
import subprocess
import time
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_wick_r_study import dump,stamp
from yoyo.evaluation.monday_confirmed_score import five_bars,hourly_bars,feature_row,score,context_at,touch_context,MODEL,MIN5

ROOT=Path(__file__).resolve().parents[2]
EXP=ROOT/'experiments/active/exp-monday-5m-confirmed-score-20261026-v1'
SRC=ROOT/'experiments/active/exp-monday-5m-wick-r-20261026-v1/symbols'
IDENT=ROOT/'experiments/active/exp-monday-limit-reclaim-20261024-v1/symbols'
CODE=['yoyo/evaluation/monday_confirmed_r_study.py','yoyo/evaluation/monday_confirmed_r_engine.py',
      'yoyo/evaluation/monday_confirmed_score.py','yoyo/evaluation/monday_wick_r_engine.py']
EXCLUDED={'BTCUSDT','ETHUSDT','XAUUSDT','XAGUSDT','PAXGUSDT','XAUTUSDT'}


def prepare(frame,old,job,ctx,spec,slip):
    """Admit only complete opposite-color pin + next5m reclaim; next-open fill.

Features use only pre-touch OHLCV, completed Monday and prior touches. Entry
uses next open; pin stop is known before this open. Candle gaps retain the
source-ledger censoring boundary. No final trade outcome enters admission.
"""
    five=five_bars(frame,job['execution_minutes']);hour=hourly_bars(five)
    fi=five.index.as_unit('ns').asi8;ts=frame.index.as_unit('ns').asi8
    records=[]
    for oldrow in old.to_dict('records'):
        r=oldrow.copy();side=r['side'];touch=int(r['entry_ns'])//MIN5*MIN5
        k=int(np.searchsorted(fi,touch));entry_i=int(r['confirm_i'])+1
        r.update(original_entry=r['entry'],touch_ns=touch,original_entry_i=r['entry_i'],
                 score=np.nan,score_error='',admission='accepted')
        if (k+1>=len(five) or fi[k]!=touch or fi[k+1]!=touch+MIN5 or
            entry_i>=len(frame) or entry_i>r['end_i'] or ts[entry_i]!=touch+2*MIN5):
            r['admission']='incomplete_or_boundary'
        else:
            pin=five.iloc[k];confirm=five.iloc[k+1]
            polarity=side*(pin.close-pin.open)<0
            wick=(pin.low<min(pin.open,pin.close)) if side==1 else (pin.high>max(pin.open,pin.close))
            if not polarity or not wick:r['admission']='not_opposite_color_wick'
            elif side*(confirm.close-r['original_entry'])<=0:r['admission']='failed_confirmation'
            else:
                stop=float(pin.low if side==1 else pin.high);quote=float(frame.open.iloc[entry_i])
                if side*(quote-stop)<=0:r['admission']='entry_open_beyond_pin_stop'
                else:
                    entry=quote*(1+side*slip)
                    r.update(entry_i=entry_i,entry_ns=int(ts[entry_i]),entry_quote=quote,entry=entry,
                             stop=stop,risk=side*(entry-stop),risk_pct=100*side*(entry-stop)/entry)
                    if r['population']=='signal':
                        end=(pd.Timestamp(r['week'],tz='America/New_York')+pd.DateOffset(days=1)).tz_convert('UTC').value
                        opp,cluster=context_at(ctx,r['symbol'],r['week'],side,touch)
                        try:
                            feat=feature_row(five,hour,side,r['monday_high'],r['monday_low'],r['level'],end,touch,opp,cluster)
                            r['score']=score(feat,spec)
                            r.update({'feature_'+key:value for key,value in feat.items()})
                        except ValueError as exc:r['score_error']=str(exc)
        records.append(r)
    audit=pd.DataFrame(records)
    admitted=audit[audit.admission.eq('accepted')].copy().reset_index(drop=True)
    return admitted,audit


def process(symbol):
    from yoyo.evaluation.monday_limit_reclaim_inputs import load_job
    from yoyo.evaluation.monday_confirmed_r_engine import execute
    import vectorbt
    cfg=json.loads((EXP/'config.json').read_text());spec=json.loads(MODEL.read_text())
    job=json.loads((IDENT/symbol/'identity.json').read_text())['job']
    dest=EXP/'symbols'/symbol;dest.mkdir(parents=True,exist_ok=True)
    identity=dict(inputs=[stamp(p) for p in job['paths']],source=stamp(SRC/symbol/'events.parquet'),
        context=stamp(EXP/'touch_context.parquet'),model=stamp(MODEL),code=[stamp(ROOT/p) for p in CODE],config=cfg,engine=vectorbt.__version__)
    if (dest/'done.json').exists():
        assert json.loads((dest/'identity.json').read_text())==identity,'Inputs or builder changed'
        return json.loads((dest/'done.json').read_text())
    begin=time.monotonic();old=pd.read_parquet(SRC/symbol/'events.parquet')
    frame,quality=load_job(job);ctx=pd.read_parquet(EXP/'touch_context.parquet')
    events,audit=prepare(frame,old,job,ctx,spec,cfg['market_slippage'])
    out,fills=execute(frame,events,cfg['target_r'],cfg['maker_fee'],cfg['taker_fee'],cfg['market_slippage'],job['execution_minutes'])
    if len(out):
        assert len(out)==len(events)*12 and len(fills)==2*len(out)
        assert (out.exit_i>=out.entry_i).all()
        np.testing.assert_allclose(out.loc[out.reason.eq(4),'gross_r'],out.loc[out.reason.eq(4),'target_r'],atol=1e-7)
    audit.to_parquet(dest/'admission.parquet',index=False);events.to_parquet(dest/'events.parquet',index=False)
    out.to_parquet(dest/'trades.parquet',index=False);fills.to_parquet(dest/'fills.parquet',index=False)
    done=dict(symbol=symbol,source_rows=len(frame),first=str(frame.index[0]),last=str(frame.index[-1]),
        source_n=len(old),source_signals=int(old.population.eq('signal').sum()),events=len(events),
        signals=int(events.population.eq('signal').sum()),columns=len(out),fills=len(fills),
        admission=audit.admission.value_counts().to_dict(),seconds=time.monotonic()-begin)
    dump(dest/'identity.json',identity);dump(dest/'done.json',done);return done


def run(symbols=None,workers=2):
    for p in CODE+[str((EXP/'config.json').relative_to(ROOT))]:
        if subprocess.run(['git','diff','--quiet','HEAD','--',p],cwd=ROOT).returncode or subprocess.run(
            ['git','ls-files','--error-unmatch',p],cwd=ROOT,capture_output=True).returncode:
            raise RuntimeError('Commit builder before history execution')
    paths=sorted(SRC.glob('*/events.parquet'))
    if not (EXP/'touch_context.parquet').exists():
        ctx=touch_context(pd.concat([pd.read_parquet(p,columns=['population','event_id','symbol','week','side','entry_ns']) for p in paths],ignore_index=True))
        ctx.to_parquet(EXP/'touch_context.parquet',index=False)
    syms=[p.parent.name for p in paths if not symbols or p.parent.name in symbols]
    dump(EXP/'run_manifest.json',dict(symbols=syms,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    failures=[]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures={pool.submit(process,s):s for s in syms}
        for i,f in enumerate(as_completed(futures),1):
            s=futures[f]
            try:print(json.dumps(dict(progress=f'{i}/{len(syms)}',**f.result())),flush=True)
            except Exception as exc:
                err=dict(symbol=s,error=repr(exc));failures.append(err);dump(EXP/'symbols'/s/'error.json',err);print(json.dumps(err),flush=True)
    dump(EXP/'execution.json',dict(expected=len(syms),failures=failures,complete=not failures))
    if failures:raise RuntimeError(str(failures))


def metrics(g):
    c=g[g.closed];v=c.net_bp
    return dict(n=len(g),closed_n=len(c),censored_n=len(g)-len(c),mean_bp=float(v.mean()),sum_bp=float(v.sum()),
        mean_r=float(c.net_r.mean()),median_r=float(c.net_r.median()),sum_r=float(c.net_r.sum()),
        mean_fee_bp=float(c.fee_bp.mean()),mean_entry_slip_bp=float(c.entry_slip_bp.mean()),mean_exit_slip_bp=float(c.exit_slip_bp.mean()),
        win_pct=float(100*(v>1e-8).mean()),pf=float(v[v>0].sum()/-v[v<0].sum()) if (v<0).any() else np.nan,
        tp_n=int(c.reason.eq(4).sum()),initial_sl_n=int(c.reason.eq(1).sum()),be_n=int(c.reason.eq(5).sum()),
        be_armed_n=int(g.be_armed.sum()),ambiguous_n=int(g.ambiguous.sum()))


def summarize():
    frames=[];admissions=[]
    for p in sorted((EXP/'symbols').glob('*/done.json')):
        a=pd.read_parquet(p.parent/'admission.parquet');admissions.append(a)
        if json.loads(p.read_text())['columns']:frames.append(pd.read_parquet(p.parent/'trades.parquet'))
    t=pd.concat(frames,ignore_index=True);a=pd.concat(admissions,ignore_index=True)
    gate=json.loads(MODEL.read_text())['top40_threshold'];cut=pd.Timestamp('2026-01-01',tz='UTC').value;train=pd.Timestamp('2025-01-01',tz='UTC').value
    rows=[]
    for direction,sides in [('long',[1]),('short',[-1]),('combined',[1,-1])]:
        z=t[t.side.isin(sides)&~t.symbol.isin(EXCLUDED)].copy()
        for period in ['all','pre2025','select2025','check2026']:
            mask={'all':np.ones(len(z),bool),'pre2025':(z.entry_ns<train)&(z.exit_ns<train),
                'select2025':(z.entry_ns>=train)&(z.entry_ns<cut)&(z.exit_ns<cut),'check2026':z.entry_ns>=cut}[period]
            pp=z[mask]
            for filt in ['all','scorable','score_gate']:
                sig=pp[pp.population.eq('signal')]
                if filt=='scorable':sig=sig[sig.score.notna()]
                elif filt=='score_gate':sig=sig[sig.score.ge(gate)]
                for (tp,be),sg in sig.groupby(['target_r','be_mode']):
                    m=metrics(sg);ctrl=pp[pp.population.eq('random')&pp.event_id.isin(sg.event_id)&pp.target_r.eq(tp)&pp.be_mode.eq(be)&pp.closed]
                    cc=ctrl.groupby('event_id').net_bp.agg(['mean','count'])
                    pair=sg[sg.closed].merge(cc,left_on='event_id',right_index=True)
                    full=pair[pair['count']==3]
                    m.update(direction=direction,period=period,filter=filt,target_r=int(tp),be_mode=be,
                        matched_n=len(pair),matched_signal_bp=float(pair.net_bp.mean()),matched_random_bp=float(pair['mean'].mean()),
                        excess_bp=float((pair.net_bp-pair['mean']).mean()),full3_n=len(full),full3_excess_bp=float((full.net_bp-full['mean']).mean()))
                    rows.append(m)
    res=pd.DataFrame(rows);res.to_csv(EXP/'metrics.csv',index=False)
    choices=[]
    for (direction,filt),g in res.groupby(['direction','filter']):
        select=g[g.period.eq('select2025')].sort_values(['mean_bp','target_r'],ascending=[False,True]).iloc[0]
        full=g[g.period.eq('all')].sort_values(['mean_bp','target_r'],ascending=[False,True]).iloc[0]
        chosen=g[(g.target_r==select.target_r)&(g.be_mode==select.be_mode)]
        choices.append(dict(direction=direction,filter=filt,selected2025=select.to_dict(),evaluation=chosen.to_dict('records'),full_best_descriptive=full.to_dict()))
    sg=a[a.population.eq('signal')&~a.symbol.isin(EXCLUDED)]
    diagnostics=dict(source_signals=len(sg),admission=sg.admission.value_counts().to_dict(),
        accepted=int(sg.admission.eq('accepted').sum()),scored=int(sg.score.notna().sum()),score_gate_n=int(sg.score.ge(gate).sum()),
        score_errors=sg.loc[sg.score_error.ne(''),'score_error'].value_counts().to_dict(),
        risk_pct=sg.loc[sg.admission.eq('accepted'),'risk_pct'].describe(percentiles=[.01,.1,.5,.9,.99]).to_dict())
    dump(EXP/'summary.json',dict(choices=choices,diagnostics=diagnostics,positions=len(t),fills=2*len(t),
        scoring='Frozen current5m paper coefficients, pre-touch windows. Historical Binance volume approximation, not an exact OKX deployment replay.',
        controls='Original symbol/week/side/volatility matched random timestamps, own pin and confirmation; score-filter selects signal IDs and their controls. Conditional matched subset only.',
        units='Independent equal-notional per-trade net return and entry-known geometric R; no funded account. Fee-inclusive stop does not offset slippage or gaps.',
        training_eligible=False,production_eligible=False))
    print(json.dumps(diagnostics),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('action',choices=['run','summarize']);p.add_argument('--symbols',nargs='*');p.add_argument('--workers',type=int,default=2);a=p.parse_args()
    run(a.symbols,a.workers) if a.action=='run' else summarize()
