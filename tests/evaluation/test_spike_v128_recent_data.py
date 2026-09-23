"""Prevent duplicate revisions and partial endpoint candles entering the audit."""
import pandas as pd
import pytest
from yoyo.evaluation.spike_v128_recent_data import merge_rows


def sample():
    return pd.DataFrame({'ts': [0, 300_000, 600_000], 'open': 10., 'high': 11.,
                         'low': 9., 'close': 10., 'volume': 1.})


def test_identical_overlap_deduplicated_and_cutoff_excludes_unfinished_bar():
    result = merge_rows([sample(), sample()], pd.Timestamp(0, tz='UTC'), pd.Timestamp(750_000, unit='ms', tz='UTC'))
    assert result.ts.tolist() == [0, 300_000]


def test_conflicting_overlap_fails_even_with_other_identical_duplicates():
    changed = sample(); changed.loc[0, 'close'] = 10.5
    with pytest.raises(ValueError, match='conflicting'):
        merge_rows([sample(), changed], pd.Timestamp(0, tz='UTC'), pd.Timestamp(900_000, unit='ms', tz='UTC'))
