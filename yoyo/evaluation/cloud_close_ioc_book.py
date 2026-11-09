"""Measure fillable 12H close orders at a 5bp price limit on the already reconstructed native books."""
import gzip
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_book_sample as sample
from yoyo.evaluation import cloud_close_prior_minute_liquidity as prior

native = sample.native
EXP = native.EXP.parent/'exp-cloud-close-ioc-book-20261109-v1'


def execution(book,side,qty,face,close):
    """Only displayed opposing levels currently inside the known 12H-close limit can fill."""
    limit = close*(1+side*.0005)
    levels = sorted(book['asks'].items()) if side>0 else sorted(book['bids'].items(),reverse=True)
    visible = 0.
    cash = 0.
    filled = 0.
    for px,contracts in levels:
        if side*(px-limit)>0:
            break
        size = contracts*face
        visible += size
        take = min(size,max(0.,qty-filled))
        filled += take
        cash += take*px
    if filled>qty*(1+1e-10) or (filled and side*(cash/filled-limit)>1e-12*close):
        raise ValueError('IOC fill exceeds requested size or the protective limit')
    return dict(fill_fraction=min(1.,filled/qty),filled_notional=cash,at_close_notional=filled*close,
                visible_at_close=visible*close,limit=limit,average_price=cash/filled if filled else None)


def read(row,kind,quotes,volume):
    sym = row['symbol']
    when = row['entry_ms']
    ts,x = quotes(sym)
    i=ts.searchsorted(when-native.audit.minute.HOUR)
    if ts[i]!=when-native.audit.minute.HOUR:
        raise ValueError('Missing prior closed 12H signal price')
    close = float(x[i,3])
    qty = row['entry_notional']/close
    face = row['contract_face']
    ctx = volume.loc[row['event_id']]
    prior_cap = min(float(ctx.median_usdt)*.1,row['entry_notional']) if ctx.status=='observed' else np.nan
    file = (sample.EXP/f'{sym}_{when}_events.jsonl.gz' if kind=='ordinary' else native.EXP/f'{sym}_events.jsonl.gz')
    book={}
    first=None
    with gzip.open(file,'rt') as f:
        for line in f:
            m=json.loads(line)
            t=int(m['ts'])
            if t>when+1000:
                break
            native.apply_message(book,m)
            if t>=when and first is None:
                first=execution(book,row['observations']['first']['side'],qty,face,close)
    side = row['observations']['first']['side']
    one = execution(book,side,qty,face,close)
    out=[]
    for stage,result in [('first',first),('one_second',one)]:
        out.append(dict(event_id=row['event_id'],symbol=sym,side=side,sample=kind,stage=stage,
                   decision_close=close,desired=row['entry_notional'],past_cap_at_close=prior_cap,**result))
    return out


def run():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed measured native IOC quantities')
    quotes,_=native.audit.source.repair.checked_quotes()
    v=pd.read_csv(prior.EXP/'prior_liquidity.csv.gz',index_col='event_id')
    ordinary=[json.loads(p.read_text()) for p in sample.EXP.glob('case_*.json')]
    extreme=list(json.loads((native.EXP/'summary.json').read_text())['cases'].values())
    out=[]
    for category,records in [('ordinary',ordinary),('extreme',extreme)]:
        for r in records:
            if r.get('status','observed')=='observed':
                out.extend(read(r,category,quotes,v))
    f=pd.DataFrame(out)
    f.to_csv(EXP/'fills.csv',index=False)
    cases={}
    for (kind,stage),g in f.groupby(['sample','stage']):
        both=g[g.past_cap_at_close.notna()]
        cases[kind+'_'+stage] = dict(priced=len(g),full=int(g.fill_fraction.ge(1-1e-9).sum()),
               partial=int((g.fill_fraction.gt(0)&g.fill_fraction.lt(1-1e-9)).sum()),no_fill=int(g.fill_fraction.eq(0).sum()),
               median_fraction=float(g.fill_fraction.median()),desired_amount_fraction=float(g.at_close_notional.sum()/g.desired.sum()),
               both_sources=len(both),more_than_past_cap=int((both.at_close_notional>both.past_cap_at_close+1e-7).sum()),
               less_than_past_cap=int((both.at_close_notional<both.past_cap_at_close-1e-7).sum()))
    native.audit.source.repair.archive.prices.safe_json(EXP/'summary.json',dict(ordinary_requested=len(ordinary),
          extremes_requested=len(extreme),cases=cases,scope='Observed sample quantities with no account projection.'))


if __name__=='__main__':
    run()
