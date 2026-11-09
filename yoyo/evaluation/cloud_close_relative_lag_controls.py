"""Replace current relative strength with earlier, disjoint 30-day observations on the same funded book."""
import pandas as pd

from yoyo.evaluation import cloud_close_cvc_regime as corrected

EXP = corrected.EXP.parent/'exp-cloud-close-relative-lag-controls-20261109-v1'


def weights(parents):
    _,src = corrected.repair.checked_quotes()
    regime = corrected.study.season.direction(src)
    days = 24*corrected.repair.wallet.HOUR
    out = {}
    for lag in [30,60,90]:
        observed = (parents.entry_ms-days*lag).map(regime)
        if observed.isna().any():
            raise ValueError(f'Incomplete earlier 30-day ETH/BTC window at lag {lag}')
        out[f'lag_{lag}d'] = 1.5-.5*parents.side*observed
    return out


if __name__=='__main__':
    corrected.engine.run(EXP,weights,account_loader=corrected.account,reference_dir=corrected.repair.EXP/'account')
