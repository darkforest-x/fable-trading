"""Keep simultaneous entries' original lottery order when filtering already-filled signal paths."""
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation.live_capital41_volume import EXP, ROOT, accounts, dump


def preserve_order(frame, mask, seed):
    """Feed survivors to the original allocator so its within-time sort retains each event's original priority."""
    source = frame.loc[mask].reset_index(drop=True)
    ranks = np.random.default_rng(seed).random(len(frame))[mask]
    new_ranks = np.random.default_rng(seed).random(len(source))
    order = np.arange(len(source))
    changed = 0
    for indices in source.groupby('entry_ns', sort=False).indices.values():
        if len(indices) > 1:
            by_original = indices[np.argsort(ranks[indices])]
            by_allocator = indices[np.argsort(new_ranks[indices])]
            changed += int(not np.array_equal(by_original, by_allocator))
            order[by_allocator] = by_original
    aligned = source.iloc[order].reset_index(drop=True)
    want = source.iloc[np.lexsort((ranks, source.entry_ns))].event.to_list()
    actual = aligned.iloc[np.lexsort((new_ranks, aligned.entry_ns))].event.to_list()
    if actual != want:
        raise ValueError('Filtered tie order mismatch')
    return aligned, changed


def run():
    accounts.cr.committed(str(Path(__file__).resolve().relative_to(ROOT)))
    cfg = json.loads((EXP/'config.json').read_text())
    parent = json.loads((ROOT/cfg['accounts']).read_text())
    previous = json.loads((EXP/'summary.json').read_text())['results']
    tags = pd.read_csv(EXP/'tags.csv.gz', parse_dates=['decision'])
    results = {}
    for (name, start), (spec, x, _) in accounts.candidates(parent, 5).items():
        key = f'{name}|{start}'
        x['decision'] = pd.to_datetime(x.decision_ns, unit='ns', utc=True)
        x = x.merge(tags[['event','decision','ratio']], on=['event','decision'], how='left', validate='one_to_one')
        if x.ratio.isna().any():
            raise ValueError('Missing tags for priority check')
        keep = x.ratio.ge(1.2).to_numpy()
        aligned, changed = preserve_order(x, keep, spec['seed'])
        # The narrow check is reused with the same account seeds, original fills and selected IDs.
        stable = accounts.replay(aligned, spec, start)
        results[key] = dict(changed_tie_groups=changed, stable=stable,
                            original_base=previous[key]['base'], original_selected=previous[key]['selected'])
        print(key, changed, stable['trading_pnl'], flush=True)
    dump(EXP/'tie_sensitivity.json', dict(results=results, method=__doc__, source_commit=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], text=True).strip()))


if __name__ == '__main__':
    run()
