"""Reduce only the marked concentration of the long runner after its doubling was confirmed."""
import gc

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_doubled_risk as study

runner = study.runner
source = study.source
old = study.old
base = old.exposure
EXP = study.EXP.parent/'exp-cloud-close-doubled-cap-20261109-v1'


def switched_hours(data,previous,parents):
    observed = pd.read_csv(runner.EXP/'native_switches.csv.gz',index_col='event_id')
    mask = np.zeros_like(previous,dtype=bool)
    eligible = parents[parents.event_id.isin(observed.index)]
    for r in eligible.itertuples():
        opening = int(observed.at[r.event_id,'activated_ms'])
        if not r.entry_ms<opening<=r.exit_bar_ms:
            raise ValueError('Concentration cut lies outside its funded existing long')
        l,h = (np.array([opening,r.exit_bar_ms])-data['start'])//runner.wallet.HOUR
        mask[l:h+1,r.slot//4] = True
    return mask,len(eligible)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed doubled winner concentration study')
    parent_dir = runner.EXP/'account'
    tape = pd.read_csv(parent_dir/'selected_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    _,quotes,_ = source.inputs()
    data,_ = old.parent.assemble(tape,old.changed_limits(tape,quotes),'OKX',quotes)
    native,previous,side,parents = base.underlying(data,quotes)
    mask,n_eligible = switched_hours(data,previous,parents)
    out = {}
    for cap in [.2,.1,.05]:
        caps = np.where(mask,cap,.2)
        for slip in ([0.] if cap==.2 else [0.,.0005]):
            model,_ = base.study.leverage._syntheticize(dict(data,actions=data['actions'].copy()),1.5,slip)
            pf = base.build_portfolio(data,model,native,previous,side,1.5,slip,caps=caps)
            funds,curve,trims = base.receipts(pf,data,model,1.5)
            key = f'cap_{cap:g}'+('_stress' if slip else '')
            if cap==.2:
                old_curve = pd.read_csv(parent_dir/'hourly_1.5x.csv.gz',index_col=0)
                old_funds = pd.read_csv(parent_dir/'funded_1.5x.csv.gz',index_col='event_id')
                if not np.allclose(curve[old_curve.columns],old_curve,rtol=1.e-9,atol=.0001) or not np.allclose(
                         funds.loc[old_funds.index,'cash_pnl'],old_funds.cash_pnl,rtol=1.e-9,atol=.0001):
                    raise ValueError('Uniform concentration standard changed the native account')
            else:
                funds.to_csv(EXP/f'funded_{key}.csv.gz')
                curve.to_csv(EXP/f'hourly_{key}.csv.gz',index_label='hour_end_utc')
            out[key] = dict(metrics=base.parent.spot.hourly.tested.summarize(funds,curve),trim_fills=trims)
            print(key,out[key]['metrics']['all'],flush=True)
            del model,pf
            gc.collect()
    source.correction.archive.prices.safe_json(EXP/'summary.json',dict(arms=out,funded_activations=n_eligible,
            maintenance='Unchanged 1.5x per-unit price scenario on the identical original runner roster.'))


if __name__=='__main__':
    main()
