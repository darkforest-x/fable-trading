"""Measure the partial target from the nearer observable V1.7 protective boundary at entry."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_reward_paths as engine
from yoyo.evaluation import cloud_close_second_reclaim as second
from yoyo.evaluation import cloud_close_thirds as thirds

EXP = wallet.ROOT/'experiments/active/exp-cloud-close-binding-risk-target-20261109-v1'


def reroute(base):
    """At the exact H1 entry read only q[:,0] and the prior-confirmed 12H EMA flip level q[:,4]."""
    ref = pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['week','was_top20'])
    ref.index = pd.RangeIndex(1,len(ref)+1)
    config = json.loads((wallet.EXP/'paths_config.json').read_text())
    paths = base.copy()
    audit = []
    for symbol,g in paths[paths.tp_r.eq(2)].groupby('symbol'):
        source = ref.loc[g.source_row_number]
        p12,p1,_ = engine._choose_input(symbol,bool(source.was_top20.iloc[0]),config)
        q,times,_ = engine._prepare_symbol(engine._load_bars(p12,'12h'),engine._load_bars(p1,'1h'))
        indices = times.searchsorted(g.entry_ms)
        if not np.array_equal(times[indices],g.entry_ms) or not np.allclose(q[indices,0],g.entry_price,rtol=1e-12,atol=1e-14):
            raise ValueError('Native entry and quote ledger differ')
        protection = g.side*(g.entry_price-g.stop_price)
        flip = q[indices,4]
        distance = g.side*(g.entry_price-flip)
        active = (flip>0) & np.isfinite(flip) & distance.gt(0) & distance.lt(protection)
        audit.append(pd.DataFrame({'event_id':g.event_id,'symbol':symbol,'is_control':g.is_control,'side':g.side,
                                  'entry_price':g.entry_price,'fixed_stop':g.stop_price,'ema_flip':flip,
                                  'changed':active,'frozen_target_risk':protection.abs(),'ema_distance':distance}))
        jobs = g.assign(week=source.week.to_numpy(),entry_i=indices,risk_distance=distance).loc[active].copy()
        if jobs.empty:
            continue
        jobs['end_i'] = [engine._safe_path_end(i,s,q,times)[0] for i,s in zip(jobs.entry_i,jobs.side)]
        rows,_ = engine._simulate_symbol(jobs.to_dict('records'),q,times,(2.,),8)
        result = pd.DataFrame(rows).set_index('event_id').loc[jobs.event_id]
        if not np.allclose(result.target_price,jobs.entry_price.to_numpy()+2*jobs.side.to_numpy()*jobs.risk_distance.to_numpy()):
            raise ValueError('Repriced native limit differs from the specified initial 2R')
        fields = ['exit_price','exit_bar_ms','exit_phase','exit_reason','exit_fee_rate','same_bar_ambiguous','gap_flag']
        paths.loc[jobs.index,fields] = result[fields].to_numpy()
    return paths,pd.concat(audit,ignore_index=True)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Binding-risk comparison already exists')
    base = thirds.okx_paths()
    repair.check_parent_sets(base)
    base = base[~base.is_control | base.parent_id.isin(base[~base.is_control].parent_id)]
    paths,audit = reroute(base)
    audit.to_csv(EXP/'geometry.csv.gz',index=False)
    paths[paths.tp_r.eq(2)&paths.event_id.isin(audit.loc[audit.changed,'event_id'])].to_csv(EXP/'changed_limits.csv.gz',index=False)
    real, controls = paths[~paths.is_control],paths[paths.is_control]
    old = pd.read_csv(repair.EXP/'thirds'/'account_metrics.csv')
    baseline = old[old.venue.eq('OKX') & old.scenario.eq('actual')]
    rows = baseline.assign(policy='fixed_stop_risk').to_dict('records')
    m,trades,equity,meta = second.funded(real)
    trades.to_csv(EXP/'funded.csv.gz')
    equity.to_csv(EXP/'hourly.csv.gz',index_label='hour_end_utc')
    rows.extend(dict(venue='OKX',policy='nearer_protection',scenario='actual',period=p,**z) for p,z in m.items())
    dev,full = second.meets(m['dev']),second.meets(m['all'])
    if dev:
        for name,group,slip in [('matched',real[real.parent_id.isin(controls.parent_id)],0.),('control',controls,0.),('stress',real,.0005)]:
            checks = second.funded(group,slip)[0]
            rows.extend(dict(venue='OKX',policy='nearer_protection',scenario=name,period=p,**z) for p,z in checks.items())
    table = pd.DataFrame(rows)
    table.to_csv(EXP/'account_metrics.csv',index=False)
    summary = dict(changed_real=int((audit.changed & ~audit.is_control).sum()),
                   changed_controls=int((audit.changed & audit.is_control).sum()),dev_pass=dev,full_pass=full,
                   transfer=bool(dev and full and m['all']['final_usdt']>baseline.loc[baseline.period.eq('all'),'final_usdt'].item()),account=meta)
    (EXP/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(table[table.period.eq('all')].to_string(index=False))


if __name__=='__main__':
    main()
