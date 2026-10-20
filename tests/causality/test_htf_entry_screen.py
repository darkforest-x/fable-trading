"""Every higher-timeframe screen rule must be blind to bars after t.

A rule whose output at bar t changes when later bars are removed is reading
the future, and its markout would be a label leak, not an entry edge.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.htf_entry_screen import RULES, deoverlap, forward


def _bars(n: int, freq: str, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 0.01, n)) * close
    idx = pd.date_range("2022-01-03", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({
        "open": open_, "high": np.maximum(open_, close) + spread,
        "low": np.minimum(open_, close) - spread, "close": close,
        "volume": rng.lognormal(10, 1, n),
    }, index=idx)


@pytest.mark.parametrize("freq,n", [("4h", 2400), ("1D", 700)])
@pytest.mark.parametrize("name", sorted(RULES))
def test_rule_output_is_prefix_invariant(name, freq, n):
    df = _bars(n, freq)
    full = RULES[name](df)
    assert np.abs(full).sum() > 0, "rule never fires on synthetic data"
    for cut in (n // 3 + 1, n // 2 + 3, n - 17):
        np.testing.assert_array_equal(full[:cut], RULES[name](df.iloc[:cut]))


def test_forward_markout_is_nan_across_a_gap():
    df = _bars(50, "4h").drop(index=_bars(50, "4h").index[20])
    fwd = forward(df, "4h", 3)
    assert np.isnan(fwd[17:20]).all() and np.isfinite(fwd[:17]).all()


def test_deoverlap_keeps_one_event_per_horizon():
    assert deoverlap(np.array([0, 1, 5, 6, 12, 13]), 6).tolist() == [0, 6, 12]
