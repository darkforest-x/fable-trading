"""Mechanics of the v3 strength strata: frozen terciles, permutation contrast, feature guard."""
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import market_sync_shock_v3 as m


def test_tercile_edges_come_from_selection_rows_only():
    values = np.array([1, 2, 3, 4, 5, 6, 100, 200, 300.0])
    select = np.array([True] * 6 + [False] * 3)
    bins, edges = m.frozen_terciles(values, select)
    assert edges == pytest.approx([2 + 2 / 3, 4 + 1 / 3])
    assert bins.tolist() == [0, 0, 1, 1, 2, 2, 2, 2, 2]  # later extremes fall in the frozen top bin


def test_permutation_contrast_separates_signal_from_noise():
    rng = np.random.default_rng(0)
    bins = np.repeat([0, 1, 2], 30)
    shifted = rng.normal(0, 1, 90) + np.where(bins == 2, 2.0, 0.0)
    assert m.permutation_p(shifted, bins, np.random.default_rng(1), 2000) < 0.01
    assert m.permutation_p(rng.normal(0, 1, 90), bins, np.random.default_rng(2), 2000) > 0.05
    assert np.isnan(m.permutation_p(np.ones(4), np.array([0, 0, 2, 1]), np.random.default_rng(3), 10))


def test_feature_attachment_refuses_a_different_z():
    t = pd.Timestamp("2024-01-01", tz="UTC")
    feats = {60: pd.DataFrame({"btc_z": [3.0], "eth_z": [-4.0], "btc_vr": [2.5], "eth_vr": [5.0]}, index=[t])}
    ev = pd.DataFrame({"minutes": [60], "time": [t.isoformat()], "side": [1], "btc_z": [3.0], "eth_z": [-4.0]})
    out = m.attach_features(ev, feats)
    assert out.strength.iloc[0] == 3.0 and out.volume.iloc[0] == 2.5
    with pytest.raises(ValueError):
        m.attach_features(ev.assign(btc_z=[3.1]), feats)
