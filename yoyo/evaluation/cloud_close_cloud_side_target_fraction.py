"""Assign the sale fraction at the original sealed 12H signal, without filtering any entries."""
import hashlib
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_confirmation_cloud_position as cloud
from yoyo.evaluation import cloud_close_native_target_fraction as fixed

source = fixed.source
EXP = source.ROOT / 'exp-cloud-close-cloud-side-target-fraction-20261109-v1'


def masks(parents, caps):
    known = pd.read_csv(cloud.EXP/'known_at_confirmation.csv.gz',index_col='event_id').reindex(parents.index)
    if (not known.entry_ms.equals(parents.entry_ms) or not known.side.equals(parents.side)
            or known.cloud_position.isna().any()):
        raise ValueError('A sealed cloud feature is missing or refers to another clock or side')
    plan = known[['entry_ms','side','cloud_position']].copy()
    plan['positive_cap'] = caps.gt(0)
    plan['beyond'] = known.cloud_position.eq('beyond_both')
    plan['hash_control'] = False
    rank = pd.Series({eid:hashlib.sha256(('1109|'+eid).encode()).hexdigest() for eid in plan.index})
    for _,group in plan.groupby(['entry_ms','side','positive_cap']):
        n = int(group.beyond.sum())
        ids = rank.loc[group.index].sort_values().index[:n]
        plan.loc[ids,'hash_control'] = True
    return plan


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed original cloud position sale comparison')
    data, market, quotes, parents, caps, ratios = source.account()
    plan = masks(parents,caps)
    plan.to_csv(EXP/'entry_policy.csv.gz',index_label='event_id')
    cases = {}
    for name in ['beyond','hash_control']:
        fractions = pd.Series(np.where(plan[name],1/3,2/3),index=plan.index)
        expected_ids = set(parents.index[caps.gt(0)])
        for slip in (0.,.0005):
            key = name+('_stress' if slip else '')
            def verify(fills):
                actual = fixed.opening_weights(fills)
                f = fractions.loc[actual.index]
                expected = np.column_stack((f/2,f/2,(1-f)/2,(1-f)/2))
                if set(actual.index)!=expected_ids or not np.allclose(actual,expected,rtol=0.,atol=1e-8):
                    raise ValueError('Conditional native allocations or parent roster differ')
            result = source.queue.engine.simulate(data,market,quotes,ratios,slip,EXP,key,
                                entry_limits=caps,fill_sink=verify,entry_target_fractions=fractions)
            if 'metrics' not in result:
                raise ValueError('Unresolved conditional sale account')
            print(key, result['metrics']['all'], flush=True)
            cases[key] = result
    funded = plan[plan.positive_cap]
    baselines = json.loads((fixed.EXP/'summary.json').read_text())['arms']
    source.queue.repair.archive.prices.safe_json(EXP/'summary.json',dict(arms=cases,
        funded=len(funded), changed_fraction=int(funded.beyond.sum()),
        different_control_assignments=int(funded.beyond.ne(funded.hash_control).sum()),
        uniform_two_thirds=baselines['median']['metrics'],uniform_one_third=baselines['sell_one_third']['metrics']))


if __name__ == '__main__':
    main()
