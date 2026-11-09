"""Refine both fee-only and stressed near-boundary ETH/BTC contrarian sizing accounts."""
import gc
import json

from yoyo.evaluation import cloud_close_alt_season_sizing as choice
from yoyo.evaluation import cloud_close_double_minute_risk as minute

engine = choice.engine
EXP = choice.EXP.parent/'exp-cloud-close-alt-season-minutes-20261109-v1'


def main(directory=EXP,account_directory=choice.EXP,loader=engine.account,*,scope='Point-in-time ETH/BTC sized V1.7 portfolio with CVC gap close, no historical funding or exchange mark coverage.'):
    if (directory/'summary.json').exists():
        raise FileExistsError('Completed pre-signal ETH/BTC risk refinement')
    data,market,quotes = loader()
    parents = market[-1].set_index('event_id')
    weights = choice.weights(parents)['contrarian_quarter']
    outcomes = json.loads((account_directory/'summary.json').read_text())['arms']
    out = {}
    for arm,slip in [('contrarian_quarter',0.),('contrarian_quarter_stress',.0005)]:
        dest = directory/arm
        dest.mkdir(exist_ok=True)
        if not (dest/'summary.json').exists():
            eq,capture = minute.snapshots(quotes,dest,account_directory,label=arm)
            if not (dest/'post_trim_units.csv.gz').exists():
                engine.simulate(data,market,quotes,weights,slip,dest,arm,snapshot=capture)
            minute.refine(dest,eq,quotes,outcomes[arm]['metrics']['all'],market_slip=slip,
                   scope=scope)
            gc.collect()
        out[arm] = json.loads((dest/'summary.json').read_text())
    engine.source.correction.archive.prices.safe_json(directory/'summary.json',dict(arms=out))


if __name__=='__main__':
    main()
