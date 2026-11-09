"""Vary only short-side leverage on the same confirmed historical cash roster."""
import gc
import json

from yoyo.evaluation import cloud_close_directional_exposure as directional

EXP = directional.EXP.parent/'exp-cloud-close-short-exposure-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed short-side exposure comparison')
    rows,quotes,_ = directional.historical.inputs()
    parent = directional.base.parent
    data,_ = parent.assemble(rows,directional.repair.old.changed_limits(rows,quotes),'OKX',quotes)
    market = directional.base.underlying(data,quotes)
    results = {}
    for ratio in (2.,3.):
        for slip in [0.,.0005]:
            key = f'short_{ratio:g}'+('_stress' if slip else '')
            results[key] = directional.simulate(data,market,quotes,1.5,ratio,slip,directory=EXP)
            print(key,results[key].get('metrics',{}).get('all'),results[key]['valid'],flush=True)
            gc.collect()
    reference = json.loads((directional.EXP/'summary.json').read_text())['arms']['long_1.5']
    directional.repair.archive.prices.safe_json(EXP/'summary.json',dict(reference=reference,arms=results,long_multiple=1.5))


if __name__=='__main__':
    main()
