"""Apply the conditional early exit to archived live-wick account allocations."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import live_ema_cloud_filter as accounts
from yoyo.evaluation.range_framework import ROOT


def run_accounts(fills, config_path, workers=5):
    """Change net bp, realization time and through-exit adverse movement on slow positions only.

    Original candidate order/size, 3x liquidation, occupancy and tie handling are
    owned by the saved allocator. Each unchanged replay must reproduce its manifest.
    """
    cfg = json.loads((ROOT/config_path).read_text())
    cfg['accounts'] = {k:v for k,v in cfg['accounts'].items() if k.startswith('live-wick')}
    if len(cfg['accounts']) != 4 or len(fills) != 66 or fills.event.duplicated().any():
        raise ValueError('Expected four live-wick variants and 66 distinct checkpoint positions')
    early = fills[fills.slow].set_index('event')
    if not np.isfinite(early[['early_net_bp','early_mae_pct']]).all().all() or early.early_mae_pct.lt(0).any():
        raise ValueError('Invalid early exit net or adverse excursion')
    result = {}
    for (name, start), (spec, x, archive) in accounts.candidates(cfg, workers).items():
        if not np.array_equal(x.entry_ns, x.entry_t.dt.as_unit('ns').astype('int64')):
            raise ValueError('Account and checkpoint clocks differ')
        baseline = accounts.replay(x, spec, start)
        for observed, saved in [('end','end'),('max_drawdown_pct','max_drawdown_pct'),
                                ('taken','trades'),('win_pct','win_pct'),('cash_added','cash_added')]:
            if not np.isclose(baseline[observed], archive[saved], atol=1e-6, rtol=0):
                raise ValueError(f'{name} {start}: original {observed} differs from saved account')
        mask = x.event.isin(early.index)
        selected = early.loc[x.loc[mask, 'event']].reset_index(drop=True)
        due = selected.due.dt.as_unit('ns').astype('int64').to_numpy()
        if not ((x.loc[mask,'entry_ns'].to_numpy() < due) & (due < x.loc[mask,'exit_ns'].to_numpy())).all():
            raise ValueError('Changed exit is outside the original open holding period')
        alt = x.copy()
        alt.loc[mask,'exit_ns'] = due
        alt.loc[mask,'exit_t'] = pd.to_datetime(due, utc=True)
        alt.loc[mask,'net'] = selected.early_net_bp.to_numpy()
        alt.loc[mask,'mae_pct'] = selected.early_mae_pct.to_numpy()
        action = accounts.replay(alt, spec, start)
        result[f'{name}|{start}'] = dict(candidates=len(x), changed=int(mask.sum()),
                                        same_time_other_entries=int(np.isin(due, x.entry_ns).sum()),
                                        baseline=baseline, early=action,
                                        end_delta=action['end']-baseline['end'])
    return result
