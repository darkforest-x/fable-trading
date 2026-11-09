"""Control the sale at 2x entry price without changing the V1.7 signal, 2R target, or protective stop."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_native_target_fraction as fixed

source = fixed.source
wallet = source.queue.repair.wallet
EXP = source.ROOT / 'exp-cloud-close-doubling-sale-fraction-20261109-v1'


def route(data, quotes, children):
    """Reuse traded child templates for each event; paired even/odd children differ solely on the doubling limit."""
    selected = data['selected'].reset_index(drop=True).copy()
    selected['child'] = selected.slot % 4
    indexed = selected.set_index(['event_id','child'], drop=False, verify_integrity=True)
    key = pd.MultiIndex.from_arrays([selected.event_id, selected.child.map(dict(enumerate(children)))])
    routed = indexed.loc[key].reset_index(drop=True).copy()
    if not np.array_equal(routed[['event_id','entry_ms','entry_price','stop_price','side']].to_numpy(),
                          selected[['event_id','entry_ms','entry_price','stop_price','side']].to_numpy()):
        raise ValueError('An exit template changed native opening geometry or contract')
    routed['slot'] = selected.slot
    changed = (selected.exit_bar_ms.ne(routed.exit_bar_ms) | selected.exit_phase.ne(routed.exit_phase)
               | selected.exit_price.ne(routed.exit_price) | selected.exit_fee_rate.ne(routed.exit_fee_rate))
    affected = selected.loc[changed]
    if not affected.side.eq(1).all():
        raise ValueError('A short child was changed by an absolute upside sale')
    end = data['start']+len(data['actions'])//3*wallet.HOUR
    inputs = wallet.prepare(None,data['actions'].shape[1],data['start'],end,
                            assigned=(routed,data['skips']),quote_loader=quotes)
    if inputs['gap_marks'] != data['gap_marks']:
        raise ValueError('Unobserved hours introduced by routed child paths')
    return inputs, set(affected.event_id), len(affected)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed doubling sale fraction accounts')
    data, market, quotes, parents, caps, multiples = source.account()
    funded_ids = set(caps[caps.gt(0)].index)
    ref = json.loads((fixed.EXP/'summary.json').read_text())['arms']
    cases = {s:ref[s] for s in ['median','median_stress']}
    evidence = {}
    for name,children in [('no_doubling_sale',[1,1,3,3]), ('full_doubling_sale',[0,0,2,2])]:
        routed, affected, n = route(data,quotes,children)
        evidence[name] = dict(changed_parent_templates=len(affected), changed_child_templates=n,
                              changed_funded_parents=len(funded_ids & affected))
        for slip in [0.,.0005]:
            key = name+('_stress' if slip else '')
            def audit(fills):
                actual = fixed.opening_weights(fills)
                if (set(actual.index)!=funded_ids
                        or not np.allclose(actual,[1/3,1/3,1/6,1/6],rtol=0.,atol=1e-8)):
                    raise ValueError('Initial native shares or roster changed')
            result = source.queue.engine.simulate(routed,market,quotes,multiples,slip,EXP,key,
                                                  entry_limits=caps,fill_sink=audit)
            if 'metrics' not in result:
                raise ValueError('Unresolved framework margin or account')
            print(key, result['metrics']['all'], flush=True)
            cases[key] = result
    source.queue.repair.archive.prices.safe_json(EXP/'summary.json',dict(arms=cases, changes=evidence))


if __name__ == '__main__':
    main()
