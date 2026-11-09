"""Prioritize risk-aligned VIX signals only when concurrent V1.7 applications compete."""
import pandas as pd

from yoyo.evaluation import cloud_close_vix_regime as regimes

queue = regimes.queue
EXP = queue.EXP.parent/'exp-cloud-close-vix-priority-20261109-v1'


def rank_aligned(pool):
    """Use saved as-of VIX directions. Resolve within-group order by the original balanced score then ATR."""
    signals = pd.read_csv(regimes.EXP/'signal_regimes.csv.gz',index_col='event_id').reindex(pool.index)
    if not signals.entry_ms.equals(pool.entry_ms):
        raise ValueError('The VIX coverage no longer matches original opportunity clocks')
    # Add one more than the within-clock maximum score to place opposed orders after aligned ones.
    width = pool.groupby('entry_ms').balanced.transform('max')+1
    out = pool.copy()
    for key,source in [('aligned','direction'),('lagged','stale_direction')]:
        out[key] = pool.balanced + width*(signals.side*signals[source]>0).astype(int)
    return out


if __name__=='__main__':
    queue.main(experiment=EXP,policies=[('aligned','aligned'),('lagged','lagged')],score_builder=rank_aligned)
