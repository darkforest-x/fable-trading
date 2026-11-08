"""Protect a fixed half-R profit after a completed H1 favorable move without capping upside."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_leadership as leader
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_trail as overlay


EXP=wallet.ROOT/'experiments/active/exp-cloud-close-lock-profit-20261109-v1'


def main():
    cfg=json.loads((EXP/'config.json').read_text())
    base=wallet.read_paths()
    base=base[base.tp_r.eq(0)].copy()
    base=base[pd.MultiIndex.from_frame(base[['entry_ms','symbol']]).isin(leader.leadership().dropna().index)].copy()
    base=base[~base.is_control|base.parent_id.isin(base[~base.is_control].parent_id)].copy()
    src=pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    src.index=pd.RangeIndex(1,len(src)+1)
    base['entry_order']=base.source_row_number.map(src.prior_atr_pct).fillna(np.inf)
    base['arm_at_r']=0.
    limits=cfg['threshold_r']
    file=EXP/'profit_lock_paths.csv.gz'
    if not file.exists():
        variants=pd.concat([overlay.symbol_paths(g,limits,cfg['locked_r']) for _,g in base.groupby('symbol')],ignore_index=True)
        temp=file.with_suffix('.pending')
        variants.to_csv(temp,index=False,compression='gzip')
        temp.replace(file)
    else:
        variants=pd.read_csv(file,dtype={'symbol':str},keep_default_na=False,float_precision='round_trip')
    if len(variants)!=len(limits)*len(base) or variants.duplicated(['event_id','arm_at_r']).any():
        raise ValueError('Incomplete or duplicate profit-lock paths')
    paths=pd.concat([base,variants],ignore_index=True)
    rows,curves=[],{}
    for t in [0,*limits]:
        real=paths[~paths.is_control&paths.arm_at_r.eq(t)]
        m,meta,tr,eq=wallet.evaluate(real,9)
        curves[t]=tr,eq
        rows.extend(dict(arm_at_r=t,period=p,**v) for p,v in m.items())
        gc.collect()
    frame=pd.DataFrame(rows)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(wallet.qualifies,axis=1)&dev.trades.ge(100)]
    pick=float(eligible.arm_at_r.iloc[0]) if len(eligible) else 0.
    best=float(dev.arm_at_r.iloc[0])
    checks={}
    for t in sorted({pick,best}):
        tr,eq=curves[t]
        tr.to_csv(EXP/f'funded_{t}.csv.gz',index=False)
        eq.to_csv(EXP/f'hourly_{t}.csv.gz',index_label='hour_end_utc')
        part=paths[paths.arm_at_r.eq(t)]
        real,ctl=part[~part.is_control],part[part.is_control]
        checks[t]={}
        for name,events,slip in [('paired_signals',real[real.parent_id.isin(ctl.parent_id)],0.),
                                 ('control',ctl,0.),('slippage_5bp',real,.0005)]:
            checks[t][name]=wallet.evaluate(events,9,slip=slip)[0]
            gc.collect()
    result=dict(pick=pick,best_dev_growth=best,eligible=eligible.arm_at_r.tolist(),checks=checks)
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
