"""Use ETH/BTC relative performance, observable before entry, to pick the direction of the V1.7 pool."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_notice_tail_risk as source

parent = source.correction.parent
wallet = source.correction.wallet
EXP = source.EXP.parent/'exp-cloud-close-alt-season-20261109-v1'
STEP = 12*wallet.HOUR


def direction(src):
    """Thirty-day change of native ETH close divided by BTC close; require 61 adjacent complete 12H bars."""
    bars = {}
    for s in ['ETH','BTC']:
        _,b = parent.repair.market.frames(src[s])
        bars[s] = b.close
    ratio = bars['ETH'].div(bars['BTC']).dropna()
    periods = ratio.index.to_series().diff(60).eq(60*STEP)
    state = np.sign(ratio/ratio.shift(60)-1).where(periods)
    state.index += STEP
    return state


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed ETH/BTC relative-strength admission')
    rows,src,_,_ = source.correction.pool()
    _,quotes,_ = source.inputs()
    regime = rows.entry_ms.map(direction(src))
    if regime.isna().any():
        raise ValueError('Original alt admission lacks the closed and aligned ETH/BTC window')
    relative = rows.side*regime
    result = {}
    for name,mask in [('following',relative.gt(0)),('opposite',relative.lt(0))]:
        result[name] = parent.run_wallet(rows[mask],quotes,src,EXP/name)
        gc.collect()
    reference = json.loads((source.correction.EXP/'summary.json').read_text())['cases']['fee_only']['metrics']['all']
    source.correction.archive.prices.safe_json(EXP/'summary.json',dict(arms=result,reference=reference,
                                 neutral_eligible=int(regime.eq(0).sum()//2)))


if __name__=='__main__':
    main()
