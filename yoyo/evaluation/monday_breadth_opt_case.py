"""Owner-named market episode. Include the three reference assets and all 68 original constituents."""
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd

from yoyo.evaluation import monday_breadth_opt_exit_study as study
from yoyo.evaluation import monday_breadth_opt_exits as sim


def main():
    output=study.OUT
    output.mkdir(parents=True,exist_ok=True);study.CACHE.mkdir(parents=True,exist_ok=True)
    member=study.member_bridge()
    names=member[(member.minutes==5)&(member.known==study.CASE_ENTRY-pd.Timedelta(minutes=30))].symbol.tolist()
    if len(names)!=68:raise ValueError('September event membership differs')
    jobs=[(s,[study.CASE_ENTRY],None) for s in ['BTCUSDT','ETHUSDT','SOLUSDT',*names]]
    with ProcessPoolExecutor(3) as pool:
        for i,(s,n) in enumerate(pool.map(study.fill_symbol,jobs),1):
            if i%15==0:print(i,s,n,flush=True)
    cases=[]
    for s,_,_ in jobs:
        f=pd.read_parquet(study.CACHE/(s+'.parquet'))
        f=f[f.entry_time.eq(study.CASE_ENTRY)].copy()
        policies=sim.policies(s=='BTCUSDT')
        f['rule']=[policies[i].key for i in f.policy_id]
        f['kind']=[policies[i].kind for i in f.policy_id]
        f['horizon_hours']=[policies[i].hours for i in f.policy_id]
        f['asset']=s
        cases.append(f)
    all_fills=pd.concat(cases,ignore_index=True)
    all_fills.to_parquet(output/'sept16_all_policy_member_fills.parquet',index=False)
    btc=all_fills[all_fills.asset.eq('BTCUSDT')].copy()
    btc['net_10000']=btc.net_points.ge(10000)
    btc['gross_points']=btc.exit_vwap-btc.entry_open
    btc.to_csv(output/'sept16_btc_policies.csv',index=False)
    pooled=all_fills[all_fills.asset.isin(names)].groupby(['policy_id','rule','kind','horizon_hours'])
    alt=pooled.agg(coins=('asset','nunique'),mean_net_pct=('net_pct','mean'),median_net_pct=('net_pct','median'),
        mean_peak_pct=('peak_pct','mean'),win_pct=('net_pct',lambda x:100*np.mean(x>0)),worst=('net_pct','min'),
        mean_exits=('exit_legs','mean'),mean_retained_pct=('net_pct','mean')).reset_index()
    alt['mean_retained_pct']=100*alt.mean_net_pct/alt.mean_peak_pct
    alt.to_csv(output/'sept16_alt_policies.csv',index=False)


if __name__=='__main__':
    main()
