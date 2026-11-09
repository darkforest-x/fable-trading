"""Tilt equal-slot leverage toward or away from the pre-entry ETH/BTC-led direction."""
import pandas as pd

from yoyo.evaluation import cloud_close_alt_season as season
from yoyo.evaluation import cloud_close_verified_exposure as engine

EXP = season.EXP.parent/'exp-cloud-close-alt-season-sizing-20261109-v1'


def weights(parents):
    _,_,src = season.source.inputs()
    regime = parents.entry_ms.map(season.direction(src))
    if regime.isna().any():
        raise ValueError('ETH/BTC chronology missing for an actually funded parent')
    agreement = parents.side*regime
    return {name:1.5-n*agreement for name,n in [('contrarian_quarter',.25),
                                                 ('contrarian_half',.5),('momentum_half',-.5)]}


if __name__=='__main__':
    engine.run(EXP,weights)
