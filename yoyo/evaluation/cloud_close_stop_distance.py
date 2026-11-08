"""Only the fixed stop varies; both original entry and 2R limit stay fixed."""
import json
import numpy as np
import pandas as pd
from yoyo.evaluation import cloud_close_priority as priority
from yoyo.evaluation import cloud_close_slots as capacity
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_reward_account as w

EXP=w.ROOT/'experiments/active/exp-cloud-close-stop-distance-20261109-v1'
FACTORS=(0.,.25,.5,2.,3.,5.)


def prepare_paths(output):
    src=pd.read_csv(w.SOURCE/'signals.csv',float_precision='round_trip')
    src.index=pd.RangeIndex(1,len(src)+1)
    path_cfg=json.loads((w.EXP/'paths_config.json').read_text())
    frozen=w.read_paths()
    runner=frozen[frozen.tp_r.eq(0)].copy()
    actual=runner[~runner.is_control].set_index('parent_id')
    runner['original_parent_entry']=runner.parent_id.map(actual.entry_price)
    results=[]
    for j,(symbol,g) in enumerate(runner.groupby('symbol')):
        ids=g.source_row_number
        spec=src.loc[ids]
        twelve,hourly,_=engine._choose_input(symbol,bool(spec.was_top20.iloc[0]),path_cfg)
        bars=engine._load_bars(hourly,'1h')
        q,clock,_=engine._prepare_symbol(engine._load_bars(twelve,'12h'),bars)
        g=g.assign(week=spec.week.to_numpy(),entry_i=clock.searchsorted(g.entry_ms),
                   reference=spec.reference_entry.to_numpy(),beyond=spec.stop_pct.to_numpy())
        g['end_i']=[engine._safe_path_end(i,s,q,clock)[0] for i,s in zip(g.entry_i,g.side)]
        g['risk_distance']=(g.entry_price-g.stop_price).abs()  # Original absolute target geometry.
        for factor in FACTORS:
            pstop=g.reference*(1-g.side*factor*g.beyond)
            fraction=g.side*(g.original_parent_entry-pstop)/g.original_parent_entry
            stop=np.where(g.is_control,g.entry_price*(1-g.side*fraction),pstop)
            jobs=g.assign(stop_price=stop)
            rows,_=engine._simulate_symbol(jobs.to_dict('records'),q,clock,(0.,2.),8)
            r=pd.DataFrame(rows)
            r['stop_factor']=factor
            results.append(r[[*frozen.columns,'stop_factor']])
        if (j+1)%50==0:print('Priced',j+1,'contracts',flush=True)
    out=pd.concat(results,ignore_index=True)
    pending=output.with_suffix('.tmp')
    out.to_csv(pending,index=False,compression='gzip')
    pending.replace(output)


def main():
    out=EXP/'modified_fills.csv.gz'
    if not out.is_file():prepare_paths(out)
    paths=pd.read_csv(out,keep_default_na=False,float_precision='round_trip')
    cfg=json.loads((priority.EXP/'summary.json').read_text())
    fraction,slots,rule=cfg['parent_fraction'],cfg['slots'],cfg['pick']
    if rule!='crc':
        src=pd.read_csv(w.SOURCE/'signals.csv')
        src.index=pd.RangeIndex(1,len(src)+1)
        feature,sign=priority.POLICIES[rule]
        paths['entry_order']=(paths.source_row_number.map(src[feature])*sign).fillna(np.inf)
    base=pd.read_csv(priority.EXP/'account_metrics.csv')
    results=base[base.policy.eq(rule)].drop(columns='policy').assign(stop_factor=1).to_dict('records')
    samples={}
    for f in FACTORS:
        real=paths[paths.stop_factor.eq(f)&~paths.is_control]
        m,trades,eq=capacity.run(real,slots,fraction)
        results.extend(dict(stop_factor=f,period=p,**v) for p,v in m.items())
        samples[f]=trades,eq
    frame=pd.DataFrame(results)
    frame.to_csv(EXP/'account_metrics.csv',index=False)
    dev=frame[frame.period.eq('dev')].sort_values('multiple',ascending=False)
    eligible=dev[dev.apply(w.qualifies,axis=1)&dev.trades.ge(100)]
    pick=float(eligible.stop_factor.iloc[0]) if len(eligible) else 1.
    best=float(dev.stop_factor.iloc[0])
    checks={}
    for f in sorted({pick,best}-{1.}):
        trades,eq=samples[f]
        trades.to_csv(EXP/f'funded_{f}.csv.gz')
        eq.to_csv(EXP/f'hourly_{f}.csv.gz',index_label='hour_end_utc')
        part=paths[paths.stop_factor.eq(f)]
        real,null=part[~part.is_control],part[part.is_control]
        checks[f]={}
        for lab,pool,slip in [('control',null,0.),('paired_signals',real[real.parent_id.isin(null.parent_id)],0.),
                              ('slippage_5bp',real,.0005)]:
            checks[f][lab]=capacity.run(pool,slots,fraction,slip)[0]
    res=dict(fraction=fraction,slots=slots,priority=rule,eligible=eligible.stop_factor.tolist(),
             pick=pick,best_dev_growth=best,checks=checks)
    (EXP/'summary.json').write_text(json.dumps(res,indent=2,allow_nan=False)+'\n')
    print(frame[frame.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
