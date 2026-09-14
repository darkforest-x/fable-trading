"""Synthetic study-boundary and grid checks; no historical inputs."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation.spike_eth3m_recovery_study import bounds, cash_grid, exit_grid


def test_declared_grid_size():
    exits = exit_grid(dict(take_profit_r=[None, 1, 2, 3, 4], trigger_r=[.5, 1, 1.5, 2]))
    assert len(exits) * len(cash_grid()) == 855


def test_window_uses_confirmation_and_never_includes_exclusive_end():
    index = pd.date_range('2024-12-31T23:54:00Z', periods=5, freq='3min')
    frame = pd.DataFrame({'close': np.arange(5)}, index=index)
    ctx = dict(frame=frame, raw=frame.copy(), mask=pd.Series(True, index=index))
    cfg = dict(periods={'test': ['2025-01-01T00:00:00Z', '2025-01-01T00:06:00Z']})
    visible, _, indices = bounds(ctx, cfg, 'test')
    assert visible.index.max() == index[3]
    assert indices.tolist() == [1, 2]
    cfg['periods']['test'][1] = '2026-05-04T00:03:00Z'
    with pytest.raises(ValueError):
        bounds(ctx, cfg, 'test')
