"""Refinance the original V1.7 signal pool with/without opposite-side 12H closes after the attribution study."""
import pandas as pd

from yoyo.evaluation import cloud_close_confirmation_cloud_position as locations

original = locations.original
EXP = original.ROOT/'exp-cloud-close-inside-or-better-20261109-v1'


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed original cloud grouping')
    data, market, quotes, parents, caps, multiples = original.account()
    panel = pd.read_csv(locations.EXP/'known_at_confirmation.csv.gz',index_col='event_id')
    panel = panel.reindex(parents.index)
    if panel.cloud_position.isna().any() or not panel.entry_ms.equals(parents.entry_ms):
        raise ValueError('An event lost its original sealed decision or timestamp')
    keep = panel.cloud_position.ne('opposite_both')
    cases = {}
    for arm, selected in [('inside_or_better',keep), ('opposite_only',~keep)]:
        for slip in (0., .0005):
            label = arm+('_stress' if slip else '')
            result = original.queue.engine.simulate(data,market,quotes,multiples,slip,EXP,label,
                                                   entry_limits=caps.where(selected,0.))
            if 'metrics' not in result:
                raise ValueError('Unresolved V1.7 original signal account')
            print(label, result['metrics']['all'], flush=True)
            cases[label] = result
    original.queue.repair.archive.prices.safe_json(EXP/'summary.json', dict(arms=cases,
        selected_after_attribution=True, independent_out_of_sample=False))


if __name__ == '__main__':
    main()
