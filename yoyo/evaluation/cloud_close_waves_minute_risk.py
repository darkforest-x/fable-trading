"""Inspect actual minute prices during drawdown on the repaired WAVES roster."""
import json
from functools import lru_cache

import pandas as pd

from yoyo.evaluation import cloud_close_double_minute_risk as minute
from yoyo.evaluation import cloud_close_waves_wallet as repair

EXP = repair.EXP.parent / 'exp-cloud-close-waves-minute-risk-20261109-v1'


def inputs():
    original,quotes,prior = repair.old.inputs()
    details = json.loads((repair.EXP/'summary.json').read_text())
    funded = pd.read_csv(repair.EXP/'funded_1.5x_stress.csv.gz',usecols=['event_id'])
    waves = pd.read_csv(repair.EXP/'waves_paths.csv.gz',float_precision='round_trip')
    changed,_ = repair.archive.risk.hourly.tested.expiry.time_in_force(waves[waves.event_id.isin(details['added_funded'])])
    tape = pd.concat([original[original.event_id.isin(funded.event_id)],changed[original.columns]],ignore_index=True)
    if set(tape.event_id)!=set(funded.event_id) or not tape.groupby('event_id').size().eq(2).all():
        raise ValueError('Incremental WAVES reconstruction has different funded parents')

    @lru_cache(maxsize=None)
    def load(symbol):
        if symbol!='WAVES':
            return quotes(symbol)
        raw = pd.read_csv(repair.WAVES,float_precision='round_trip')
        return raw.time.to_numpy(),raw[repair.archive.FIELDS].to_numpy()

    return tape,load,prior


if __name__ == '__main__':
    minute.main(EXP,repair.EXP,inputs)
