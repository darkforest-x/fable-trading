"""Fund the three frozen ten-major native timing cases with uniform 5bp market impact."""
import json
import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_breakout_fixed_delay as delay

branch = delay.branch
wallet = branch.wallet
EXP = delay.EXP.parent / 'exp-cloud-close-breakout-cost-20261109-v1'


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed ten-coin breakout cost study')
    original = pd.read_csv(branch.EXP/'paths.csv.gz',float_precision='round_trip')
    later = pd.read_csv(delay.EXP/'paths.csv.gz',float_precision='round_trip')
    original = original[~original.is_control & original.status.eq('executed')]
    late = later[later.status.eq('executed')]
    if set(late.parent_id)!=set(original[original.arm.eq('breakout')].parent_id):
        raise ValueError('Delayed catalogue has different eligible parents')
    cases = {}
    for label,events in [('breakout_immediate',original[original.arm.eq('breakout')]),
                         ('breakout_delayed_12h',late),('reclaim_comparator',original[original.arm.eq('reclaim')])]:
        data = wallet.prepare(events,9)
        trades,curve = wallet.account(data,slip=.0005,exposure=1.)
        ref = (delay.EXP/'funded.csv.gz' if label=='breakout_delayed_12h' else
               branch.EXP/'funded'/f"funded_{events.arm.iat[0]}_actual.csv.gz")
        funded = pd.read_csv(ref,usecols=['parent_id'])
        if set(trades.parent_id)!=set(funded.parent_id) or not np.isclose(curve.nav.iat[-1],1000+trades.cash_pnl.sum(),atol=.005):
            raise ValueError(f'Slippage test membership or cash diverged for {label}')
        trades.to_csv(EXP/f'funded_{label}.csv.gz',index=False)
        curve.to_csv(EXP/f'hourly_{label}.csv.gz',index_label='hour_end_utc')
        cases[label] = {p:wallet.summarize(trades,curve,*b) for p,b in branch.PERIODS.items()}
    branch._write_json(EXP/'summary.json',dict(cases=cases,config=json.loads((EXP/'config.json').read_text())))
    print({k:round(v['all']['final_usdt'],2) for k,v in cases.items()})


if __name__=='__main__':
    main()
