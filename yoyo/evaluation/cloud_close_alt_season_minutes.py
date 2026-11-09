"""Refine both fee-only and stressed near-boundary ETH/BTC contrarian sizing accounts."""
import gc
import json

from yoyo.evaluation import cloud_close_alt_season_sizing as choice
from yoyo.evaluation import cloud_close_double_minute_risk as minute

engine = choice.engine
EXP = choice.EXP.parent/'exp-cloud-close-alt-season-minutes-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed pre-signal ETH/BTC risk refinement')
    data,market,quotes = engine.account()
    parents = market[-1].set_index('event_id')
    weights = choice.weights(parents)['contrarian_quarter']
    outcomes = json.loads((choice.EXP/'summary.json').read_text())['arms']
    out = {}
    for arm,slip in [('contrarian_quarter',0.),('contrarian_quarter_stress',.0005)]:
        dest = EXP/arm
        dest.mkdir(exist_ok=True)
        if not (dest/'summary.json').exists():
            eq,capture = minute.snapshots(quotes,dest,choice.EXP,label=arm)
            if not (dest/'post_trim_units.csv.gz').exists():
                engine.simulate(data,market,quotes,weights,slip,dest,arm,snapshot=capture)
            minute.refine(dest,eq,quotes,outcomes[arm]['metrics']['all'],market_slip=slip,
                   scope='Point-in-time ETH/BTC sized V1.7 portfolio with CVC gap close, no historical funding or exchange mark coverage.')
            gc.collect()
        out[arm] = json.loads((dest/'summary.json').read_text())
    engine.source.correction.archive.prices.safe_json(EXP/'summary.json',dict(arms=out))


if __name__=='__main__':
    main()
