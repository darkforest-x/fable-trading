"""Test whether the slower doubled-winner exit can keep its return at lower initial exposure."""
import gc

import pandas as pd

from yoyo.evaluation import cloud_close_doubled_runner as runner

source = runner.source
old = runner.parent.old
EXP = runner.EXP.parent/'exp-cloud-close-doubled-risk-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed lower exposure study for daily runners')
    tape = pd.read_csv(runner.EXP/'account/selected_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    _,quotes,_ = source.inputs()
    data,_ = old.parent.assemble(tape,old.changed_limits(tape,quotes),'OKX',quotes)
    market = old.exposure.underlying(data,quotes)
    arms = {}
    for multiple in [1.,1.25]:
        for slip in [0.,.0005]:
            key = f'{multiple:g}'+('_stress' if slip else '')
            arms[key] = old.exposure.simulate(data,market,multiple,slip,quote_loader=quotes,directory=EXP)
            print(key,arms[key].get('metrics',{}).get('all'),flush=True)
            gc.collect()
    source.correction.archive.prices.safe_json(EXP/'summary.json',dict(arms=arms))


if __name__=='__main__':
    main()
