"""Fund nine 75/25 parent positions using the existing isolated linear collateral-share adapter."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_exposure as leverage
from yoyo.evaluation import cloud_close_owner_risk as owner
from yoyo.evaluation import cloud_close_partial_r as baseline

wallet = baseline.wallet
EXP = owner.EXP.parent/'exp-cloud-close-tenr-exposure-20261109-v1'
PERIODS = {'all':(None,None),'dev':(None,'2025-01-01T00:00Z'),
           '2025':('2025-01-01T00:00Z','2026-01-01T00:00Z'),'2026':('2026-01-01T00:00Z',None)}


def prepare():
    old = pd.read_csv(baseline.EXP/'funded_10.csv.gz',usecols=['event_id'])
    paths = wallet.read_paths()
    rows = paths.loc[paths.event_id.isin(old.event_id)&paths.tp_r.isin([0,10])].copy()
    if len(rows)!=len(old)*2 or rows.is_control.any():
        raise ValueError('Frozen 10R portfolio is missing an actual target or runner')
    source = pd.read_csv(wallet.SOURCE/'signals.csv',usecols=['prior_atr_pct'])
    rows['entry_order'] = rows.source_row_number.map(source.prior_atr_pct.set_axis(source.index+1))
    return baseline.split.pair_data(rows[rows.tp_r.eq(0)],rows[rows.tp_r.eq(10)],9)


def simulate(data,multiple,slip):
    audit,receipt = leverage._margin_audit(data['selected'].reset_index(drop=True),multiple,slip)
    hit = audit[audit.maintenance_boundary_hit]
    receipt['affected_names'] = sorted(hit.symbol.unique())
    if len(hit):
        return dict(audit=receipt)
    # The transformation overwrites the entry action signs. Keep the source data
    # intact for other multiples, then retain underlying direction for the receipts.
    model,_ = leverage._syntheticize(dict(data,actions=data['actions'].copy()),multiple,slip)
    model['selected'] = data['selected'].assign(side=1)
    fills,equity = wallet.account(model,weights=(.75,.25))
    fills['side'] = data['selected'].side.to_numpy()
    qty = fills.quantity*multiple/fills.entry_price
    entry = fills.entry_price*(1+fills.side*slip)
    exit_px = fills.exit_price*(1-fills.side*np.where(fills.exit_fee_rate.ge(wallet.TAKER),slip,0.))
    fills['notional'] = qty*entry
    economics = qty*(fills.side*(exit_px-entry) - entry*wallet.TAKER-exit_px*fills.exit_fee_rate)
    if not np.allclose(fills.cash_pnl,economics,rtol=1.e-8,atol=.00001):
        raise ValueError('Linear collateral fills differ from actual underlying quantity, side or fee cashflows')
    parents = fills.groupby('event_id',sort=False).agg(parent_id=('parent_id','first'),
              symbol=('symbol','first'),side=('side','first'),entry_ms=('entry_ms','first'),
              exit_bar_ms=('exit_bar_ms','max'),cash_pnl=('cash_pnl','sum'),notional=('notional','sum'))
    parents['net_return'] = parents.cash_pnl/parents.notional
    partial = fills[fills.tp_r.eq(10)].set_index('event_id').loc[parents.index]
    if not np.allclose(partial.notional/parents.notional,.75) or not np.isclose(
            1000+parents.cash_pnl.sum(),equity.nav.iat[-1],rtol=1.e-9,atol=.001):
        raise ValueError('Joint reservation or actual final cash does not reconcile')
    if multiple==1 and not slip:
        original = pd.read_csv(baseline.EXP/'funded_10.csv.gz',index_col='event_id').sort_index()
        old_eq = pd.read_csv(baseline.EXP/'hourly_10.csv.gz',index_col=0)
        ours = parents.sort_index()
        if (not ours.index.equals(original.index) or not np.allclose(ours.cash_pnl,original.cash_pnl,
                rtol=1.e-7,atol=.0001) or not np.allclose(equity[old_eq.columns],old_eq,rtol=1.e-7,atol=.005)):
            raise ValueError('1x split synthetic account does not reproduce original hourly marks and per-parent cash')
    else:
        label = f'{multiple:g}x'+('_stress' if slip else '')
        parents.to_csv(EXP/f'funded_{label}.csv.gz')
        equity.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
    metrics = {p:wallet.summarize(parents,equity,*bounds) for p,bounds in PERIODS.items()}
    return dict(metrics=metrics,audit=receipt)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed isolated-margin 10R study')
    limits = json.loads((owner.EXP/'config.json').read_text())['owner_acceptance']
    data = prepare()
    out = {}
    for multiple in [1.,1.25,1.5,2.]:
        out[multiple] = simulate(data,multiple,0.)
        print(multiple,out[multiple].get('metrics',{}).get('all'),out[multiple]['audit']['maintenance_boundary_hits'],flush=True)
        gc.collect()
    eligible = [m for m in out if 'metrics' in out[m] and owner.qualifies(out[m]['metrics']['dev'],limits)]
    pick = max(eligible,key=lambda m:out[m]['metrics']['dev']['final_usdt']) if eligible else None
    stress = simulate(data,pick,.0005) if pick is not None else None
    (EXP/'summary.json').write_text(json.dumps(dict(cases=out,development_pick=pick,market_5bp=stress,
            limits=limits,funding='omitted; venue-specific historical mark margin tiers also unresolved'),
            indent=2,allow_nan=False)+'\n')


if __name__=='__main__':
    main()
