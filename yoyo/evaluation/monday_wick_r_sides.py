"""Directional and year breakdowns of saved native wick-R signal outcomes.

Pure aggregation: does not change parameters, signals, prices, or fills.
Directional optima are descriptive; no direction is silently removed.
"""
import numpy as np
import pandas as pd
from yoyo.evaluation.monday_wick_r_study import EXP,dump


def run():
    cols=['side','asset_group','stop_pct','target_r','entry_ns','exit_ns','closed','net_bp','net_after_slip_bp','reason']
    frames=[pd.read_parquet(p,columns=cols,filters=[('population','==','signal')]) for p in sorted((EXP/'symbols').glob('*/trades.parquet'))]
    t=pd.concat(frames,ignore_index=True);t=t[t.asset_group.eq('ALTCOINS')]
    t['stop_label']=t.stop_pct.fillna(-1)
    y25=pd.Timestamp('2025-01-01',tz='UTC').value;y26=pd.Timestamp('2026-01-01',tz='UTC').value
    rows=[]
    for period,mask in [('all',np.ones(len(t),bool)),('select2025',(t.entry_ns>=y25)&(t.entry_ns<y26)&(t.exit_ns<y26)),('check2026',t.entry_ns>=y26)]:
        for (side,sl,tp),g in t[mask].groupby(['side','stop_label','target_r']):
            c=g[g.closed]
            rows.append(dict(period=period,side=int(side),stop_pct=sl if sl>=0 else np.nan,target_r=int(tp),
                n=len(g),closed_n=len(c),target_n=int(c.reason.isin([4,14]).sum()),
                mean_bp=float(c.net_after_slip_bp.mean()),mean_fee_only_bp=float(c.net_bp.mean())))
    m=pd.DataFrame(rows);m.to_csv(EXP/'side-metrics.csv',index=False)
    best=[]
    for side in [1,-1]:
        z=m[m.side.eq(side)]
        for period in ['all','select2025']:
            b=z[z.period.eq(period)].sort_values('mean_bp',ascending=False).iloc[0]
            same=z.target_r.eq(b.target_r)&z.stop_pct.fillna(-1).eq(-1 if pd.isna(b.stop_pct) else b.stop_pct)
            best.append(dict(side=side,selected_period=period,selection=b.to_dict(),same_parameter_periods=z[same].to_dict('records')))
    dump(EXP/'side-summary.json',best)
    print(best)


if __name__=='__main__':run()
