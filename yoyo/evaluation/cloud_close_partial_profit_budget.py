"""Spend a fraction of banked target profit on a confirmed V1.7 runner add-on."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_profit_budget_add as earned
from yoyo.evaluation import cloud_close_reinvest_runner as adapter
from yoyo.evaluation import cloud_close_second_reclaim as second

EXP = earned.EXP.parent/'exp-cloud-close-partial-profit-budget-20261109-v1'
PERIODS = {'all':(None,None),'dev':(None,'2025-01-01T00:00Z'),
           '2025':('2025-01-01T00:00Z','2026-01-01T00:00Z'),'2026':('2026-01-01T00:00Z',None)}


def evaluate(paths,opportunities,fraction,slip=0.):
    _,parents,adds,eq,meta,selected = adapter.simulate(paths,opportunities,profit_only_budget=True,
                                                       profit_risk_fraction=fraction,slip=slip)
    adjusted = parents.copy()
    flat = adjusted.cash_pnl.abs().le(1e-8)
    adjusted.loc[flat,['cash_pnl','net_return']] = 0.
    amounts = fraction*(adds.target_net_profit_usdt-adds.runner_stop_loss_usdt).clip(lower=0)
    if (not np.allclose(amounts,adds.profit_budget_usdt,atol=1e-8) or
        (adds.stop_risk_used_usdt-amounts).gt(1e-8).any() or len(parents)!=len(selected)):
        raise ValueError('The economic parent roster or the independently checked risk constraint differs')
    metrics = {p:adapter.wallet.summarize(adjusted,eq,*bounds) for p,bounds in PERIODS.items()}
    return metrics,parents,adds,eq,dict(funded_parents=len(parents),addons=len(adds),flat=int(flat.sum()),
                                       add_cash_pnl=float(adds.cash_pnl.sum()),ending_cash=float(eq.nav.iloc[-1]))


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Frozen fractional profit budget already evaluated')
    paths,opps,frozen = adapter.read_inputs()
    old = pd.read_csv(earned.EXP/'account_metrics.csv')
    original = old[old.policy.eq('zero_add_replay')].assign(fraction=0.,scenario='actual')
    rows = original.to_dict('records')
    counts = {}
    for fraction in [.125,.25,.5]:
        m,parents,adds,eq,meta = evaluate(paths,opps,fraction)
        if set(parents.event_id)!=set(frozen.event_id):
            raise ValueError('Original funded entry identities changed')
        rows.extend(dict(fraction=fraction,scenario='actual',period=p,**v) for p,v in m.items())
        counts[str(fraction)] = meta
        parents.to_csv(EXP/f'funded_{fraction}.csv.gz',index=False)
        adds.to_csv(EXP/f'addons_{fraction}.csv.gz',index=False)
        eq.to_csv(EXP/f'hourly_{fraction}.csv.gz',index_label='hour_end_utc')
        del eq,parents,adds
        gc.collect()
    # Use only pre-2025 evidence. The 100% risk endpoint already failed its dev drawdown test.
    dev = [r for r in rows if r['period']=='dev' and second.meets(r)]
    pick = float(max(dev,key=lambda r:r['final_usdt'])['fraction']) if dev else None
    if pick:
        ctl = adapter.thirds.okx_paths()
        ctl = ctl[ctl.is_control & ctl.parent_id.isin(paths.parent_id)]
        ctl_opps = pd.read_csv(adapter.OPPORTUNITIES,float_precision='round_trip',dtype={'symbol':str},keep_default_na=False)
        for name,pool,choices,slip in [('paired',paths[paths.parent_id.isin(ctl.parent_id)],opps,0.),
                                       ('control',ctl,ctl_opps[ctl_opps.is_control],0.),('stress',paths,opps,.0005)]:
            m,*_ = evaluate(pool,choices,pick,slip)
            rows.extend(dict(fraction=pick,scenario=name,period=p,**v) for p,v in m.items())
    full_endpoint = old[old.policy.eq('profit_only_reinvested')].copy()
    if len(full_endpoint):
        # The full-budget endpoint is stored in its own study; its true flat trades are
        # restated from the raw ledger here regardless of when this builder is run.
        base = pd.read_csv(earned.EXP/'funded.csv.gz')
        base.loc[base.cash_pnl.abs().le(1e-8),['cash_pnl','net_return']]=0.
        eq = pd.read_csv(earned.EXP/'hourly.csv.gz',index_col='hour_end_utc',parse_dates=True)
        rows.extend(dict(fraction=1.,scenario='actual',period=p,**adapter.wallet.summarize(base,eq,*bounds)) for p,bounds in PERIODS.items())
    df = pd.DataFrame(rows)
    df.to_csv(EXP/'account_metrics.csv',index=False)
    (EXP/'summary.json').write_text(json.dumps(dict(counts=counts,dev_pick=pick,
        full_pass_at_dev_pick=(True if pick==0. else second.meets(df[df.fraction.eq(pick)&df.period.eq('all')&df.scenario.eq('actual')].iloc[0])) if pick is not None else False),
        indent=2,allow_nan=False)+'\n')
    print(df[df.period.eq('all')][['fraction','scenario','final_usdt','win_pct','payoff_usdt','dd_bound_pct']].to_string(index=False))


if __name__=='__main__':
    main()
