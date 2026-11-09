"""Permute the volume preference within each decision clock while keeping each real order cap."""
import numpy as np

from yoyo.evaluation import cloud_close_liquid_queue as queue

EXP = queue.EXP.parent/'exp-cloud-close-queue-placebos-20261109-v1'
SEEDS = tuple(range(20261109,20261128))


def permuted_ranks(pool):
    """Use only contemporaneous turnover ranks, independently permuted at each closed 12H decision."""
    ordered = pool.sort_index()
    groups = tuple(ordered.groupby('entry_ms',sort=True).indices.values())
    actual = ordered.liquid.to_numpy()
    for seed in SEEDS:
        generator = np.random.default_rng(seed)
        shuffled = actual.copy()
        for indexes in groups:
            if len(indexes)>1:
                shuffled[indexes] = generator.permutation(actual[indexes])
        ordered[str(seed)] = (ordered.quiet.to_numpy()+shuffled)/2
    return ordered


if __name__=='__main__':
    queue.main(experiment=EXP,policies=[(str(s),str(s)) for s in SEEDS],score_builder=permuted_ranks,
               fill_cases=[('fees',0.)],compact=True)
