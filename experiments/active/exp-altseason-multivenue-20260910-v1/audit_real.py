"""Reproduce the fixed real-price audit after this audit source is committed.

The initial /tmp review was preliminary and its script was not committed before
inspection. This formal audit retains top5/loss5/random5 per venue and only
checks existing frozen events; it does not optimize, change or rescore signals.
Independent replay uses source OHLCV to verify clocks, fills and held extrema.
"""
from pathlib import Path
import hashlib
import subprocess
import json
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[3]
EXPERIMENT=Path(__file__).resolve().parent
for source in (Path(__file__).resolve(), ROOT/'yoyo/evaluation/altseason_engine.py'):
    saved=subprocess.check_output(['git','show','HEAD:'+str(source.relative_to(ROOT))],cwd=ROOT)
    if saved != source.read_bytes():
        raise ValueError('Commit exact audit and engine source before formal audit: '+str(source))
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
OUT=EXPERIMENT/'results'
frames=[]
input_hashes={}
for venue in ('binance','gate'):
    for path in sorted((OUT/'markets'/venue).glob('*/events.csv.gz')):
        input_hashes[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
        f=pd.read_csv(path)
        if len(f): frames.append(f)
f=pd.concat(frames,ignore_index=True)
f=f.loc[f.valid.eq(True)].copy()
selected=[]
for venue,g in f.groupby('venue',sort=True):
    winners=g.sort_values(['net_bp','event_id'],ascending=[False,True]).drop_duplicates('asset').head(5)
    losers=g.sort_values(['net_bp','event_id'],ascending=[True,True]).drop_duplicates('asset').head(5)
    rng=g.loc[~g.event_id.isin(pd.concat([winners,losers]).event_id)].sample(n=5,random_state=20260910)
    for group,name in ((winners,'top5'),(losers,'loss5'),(rng,'random5')):
        group=group.copy();group['audit_group']=name;selected.append(group)
selected=pd.concat(selected,ignore_index=True)
errors=[];checks=[]
cache={}
for r in selected.to_dict('records'):
    path=Path(r['features_path'])
    if str(path) not in cache:
        input_hashes[str(path.relative_to(ROOT))]=hashlib.sha256(path.read_bytes()).hexdigest()
        cache[str(path)]=pd.read_pickle(path)
    b=cache[str(path)]
    i,j,k=int(r['decision_i']),int(r['entry_i']),int(r['exit_i']);step=pd.Timedelta(minutes=int(r['minutes']))
    def check(name,cond):
        if not cond:errors.append({'event_id':r['event_id'],'name':name,'symbol':r['symbol'],'arm':r['arm']})
    check('contiguous_source', bool((np.diff(b.index.asi8)==step.value).all()))
    check('decision_close_clock',pd.Timestamp(r['decision_time'])==b.index[i]+step)
    check('entry_next_open_index',j==i+1)
    check('entry_next_open_clock',pd.Timestamp(r['entry_time'])==b.index[j])
    entry=float(b.open.iloc[j]); risk=2*float(b.atr.iloc[i]);stop=entry-risk;target=entry+3*risk
    check('source_entry',np.isclose(entry,r['entry_price'],rtol=1e-10,atol=1e-12))
    check('frozen_risk',np.isclose(risk,r['initial_risk'],rtol=1e-10,atol=1e-12))
    check('initial_stop',np.isclose(stop,r['initial_stop'],rtol=1e-10,atol=1e-12))
    rule=r['exit_rule'];ma=rule.split('ratchet_')[-1] if rule.startswith('ratchet_') else None
    level=stop
    if ma and b.close.iloc[i]>b[ma].iloc[i]:level=max(level,b[ma].iloc[i])
    pending=False;mfe=mae=0.
    for at in range(j,len(b)):
        o,h,l,c=map(float,b[['open','high','low','close']].iloc[at]);mode=reason=price=None
        mfe=max(mfe,o/entry-1);mae=max(mae,1-o/entry)
        if o<=stop:price=o;reason='initial_stop_gap';mode='open'
        elif pending:price=o;reason=rule;mode='open'
        elif rule=='fixed3r' and o>=target:price=target;reason='take_profit_gap';mode='open'
        elif l<=stop:price=stop;reason='initial_stop';mode='intrabar_unknown'
        elif rule=='fixed3r' and h>=target:price=target;reason='take_profit';mode='intrabar_unknown'
        else:
            mfe=max(mfe,h/entry-1);mae=max(mae,1-l/entry)
            if at==len(b)-1:price=c;reason='boundary_mark';mode='close'
            elif ma:
                pending=c<level
                if not pending and c>b[ma].iloc[at]:level=max(level,b[ma].iloc[at])
            elif rule=='md':pending=b.md.iloc[at]<=0
        if price is not None:break
    mfe=max(mfe,price/entry-1);mae=max(mae,1-price/entry)
    check('exit_index',at==k)
    check('exit_price',np.isclose(price,r['exit_price'],rtol=1e-10,atol=1e-12))
    check('exit_reason',reason==r['exit_reason'])
    check('exit_timing',mode==r['exit_timing'])
    check('exit_clock',pd.Timestamp(r['exit_time'])==b.index[at]+(pd.Timedelta(0) if mode=='open' else step))
    check('held_mfe',np.isclose(mfe,r['mfe_return'],rtol=1e-10,atol=1e-12))
    check('held_mae',np.isclose(mae,r['mae_return'],rtol=1e-10,atol=1e-12))
    check('net_accounting',np.isclose(price/entry-1-.002,r['net_return'],rtol=1e-10,atol=1e-12))
    check('R_fixed_denominator',np.isclose((price/entry-1-.002)/(risk/entry),r['net_r'],rtol=1e-10,atol=1e-12))
    check('end_cut',b.index[-1]+step<=pd.Timestamp('2026-09-09T00:00Z'))
    checks.append({key:r[key] for key in ('venue','symbol','minutes','arm','event_id','audit_group','decision_time','exit_reason','exit_price','net_bp','mfe_return','mae_return')})
result=dict(generated_at=pd.Timestamp.now(tz='UTC').isoformat(),code_commit=commit,
    audit_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    engine_sha256=hashlib.sha256((ROOT/'yoyo/evaluation/altseason_engine.py').read_bytes()).hexdigest(),
    prior_review='Preliminary /tmp inspection occurred before its audit script was committed; this is the formal committed replay.',
    selection='For each Binance/Gate venue: highest5 distinct assets, lowest5 distinct assets,5 other events sampled with seed20260910; all arms and both periods eligible.',
    candidate_rows_inspected_for_audit_selection=len(f),selected=len(selected),
    venues=selected.groupby('venue').size().to_dict(),minutes=selected.groupby('minutes').size().to_dict(),
    arms=selected.groupby('arm').size().to_dict(),input_sha256=input_hashes,errors=errors,cases=checks,
    limitations='Finite audit sample, not an exhaustive proof. Peak-price MFE is a held-path diagnostic, not a realized fill. Boundary marks remain censored.')
output=OUT/'audit_real.json'
output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(dict(output=str(output),selected=len(selected),failures=len(errors))))
if errors:raise SystemExit(1)
