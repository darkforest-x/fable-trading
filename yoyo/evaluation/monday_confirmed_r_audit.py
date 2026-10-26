"""Reconcile cached native fills and concentration in the exact scored-alt cohort."""
import json
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_confirmed_r_study import EXP,EXCLUDED,dump


def main():
    cols=['event_id','symbol','population','target_r','be_mode','score','side','entry','entry_quote','stop','risk',
        'risk_pct','entry_ns','exit_ns','exit_price','exit_quote','net_bp','net_r','gross_r','fee_bp','closed','reason']
    frames=[];n=0;done=[]
    for p in sorted(EXP.glob('symbols/*/trades.parquet')):
        d=json.loads((p.parent/'done.json').read_text());done.append(d)
        if not d['columns']:continue
        t=pd.read_parquet(p,columns=cols);c=t[t.closed]
        np.testing.assert_allclose(c.net_bp,c.side*(c.exit_price-c.entry)/c.entry*10000-c.fee_bp,atol=1e-7)
        np.testing.assert_allclose(c.net_r,c.net_bp/10000*c.entry/c.risk,atol=1e-7)
        np.testing.assert_allclose(c.fee_bp,5+c.exit_price/c.entry*np.where(c.reason.eq(4),2,5),atol=1e-7)
        assert c.reason.isin([1,4,5]).all() and t.risk.gt(0).all()
        n+=len(t)
        frames.append(t[t.population.eq('signal')&~t.symbol.isin(EXCLUDED)&t.side.eq(1)&t.score.ge(.22621664169752762)&t.target_r.eq(7)&t.be_mode.eq('fixed_stop')])
    f=pd.concat(frames,ignore_index=True);c=f[f.closed].copy();c['year']=pd.to_datetime(c.entry_ns,utc=True).dt.year
    q=c.net_bp.quantile(.99)
    audit=dict(native_position_columns=n,native_fill_records=2*n,symbols=len(done),admitted_events=sum(d['events'] for d in done),
        signal_events=sum(d['signals'] for d in done),accounting_checks='Every closed native position reconciles PnL, R and both fill fee types',behavioral_tests=10,
        concentration_cohort='Scored eligible-alt long7R fixed-stop; exact primary cohort, excluding BTC/ETH/metals',concentration_closed_n=len(c),
        top1percent_share_net=float(c.loc[c.net_bp>=q,'net_bp'].sum()/c.net_bp.sum()),without_top1percent_mean_bp=float(c.loc[c.net_bp<q,'net_bp'].mean()),
        source_commit='758bbc5a83',chronology='Tests and builder committed before history run; one fill execution; diagnostics use cached results',
        annual_by_entry_year=c.groupby('year').agg(n=('net_bp','size'),mean_bp=('net_bp','mean'),mean_r=('net_r','mean')).to_dict('index'))
    dump(EXP/'validation.json',audit);c.nlargest(20,'net_bp').to_csv(EXP/'top_long7r.csv',index=False)
    print(json.dumps(audit,indent=2))


if __name__=='__main__':main()
