"""Event grouping and future-context bounds for Owner manual annotation."""
from copy import deepcopy

import pandas as pd
import pytest

from yoyo.datasets import grade_a_manual_pack as pack


def sample(start='2025-01-01T00:00:00Z', variant=1, pre=5, post=9):
    begin = pd.Timestamp(start)
    return dict(sample_kind='positive', event_id='event', split='train',
                dataset_sample_id=f'sample-{variant}', source_path='data/test.csv',
                window_start_time=begin.isoformat(), window_end_time=(begin+17*pack.BAR).isoformat(),
                window_start_i=0, window_end_i=17, window_bars=18,
                variant_index=variant, pre_bars=pre, post_bars=post)


def frame_for(row, count=58):
    return pd.DataFrame({'open_time': pd.date_range(row['window_start_time'], periods=count, freq='15min')})


def test_event_grouping_retains_variants_and_chronological_split():
    first, second = sample(), sample(variant=2, pre=6, post=8)
    groups = pack.group_events([second, first])
    assert len(groups) == 1
    assert pack.representative(next(iter(groups.values()))) == first
    negative = deepcopy(first)
    negative.update(sample_kind='negative', negative_event_id='event', dataset_sample_id='negative')
    assert len(pack.group_events([first, negative])) == 2
    second['split'] = 'val'
    with pytest.raises(ValueError, match='crosses source or split'):
        pack.group_events([first, second])


def test_grouping_rejects_duplicate_images_and_holdout_inputs():
    with pytest.raises(ValueError, match='duplicate'):
        pack.group_events([sample(), sample()])
    with pytest.raises(ValueError, match='pre-holdout'):
        pack.group_events([sample('2026-05-04T00:00:00Z')])


def test_future_includes_exactly_forty_additional_candles():
    row = sample()
    window, meta = pack.bounded_future_window(frame_for(row, 80), row)
    assert len(window) == 58
    assert meta['actual_future_bars'] == 40 and meta['missing_future_reason'] is None
    assert pd.Timestamp(meta['review_end_bar_open']) - pd.Timestamp(row['window_end_time']) == 40*pack.BAR


def test_future_stops_before_holdout_even_if_dataframe_has_later_rows():
    row = sample('2026-05-03T19:15:00Z')  # input ends at 23:30, one more completed bar is safe
    window, meta = pack.bounded_future_window(frame_for(row, 58), row)
    assert meta['actual_future_bars'] == 1
    assert meta['missing_future_reason'] == 'holdout_boundary'
    assert window.iloc[-1]['open_time']+pack.BAR == pack.HOLDOUT_START
    assert pack.source_read_end([row]) == pack.HOLDOUT_START


def test_future_gap_and_source_end_are_reported_without_padding():
    row = sample()
    frame = frame_for(row)
    frame.loc[21:, 'open_time'] += pack.BAR
    _, meta = pack.bounded_future_window(frame, row)
    assert meta['actual_future_bars'] == 3 and meta['missing_future_reason'] == 'source_gap'
    _, meta = pack.bounded_future_window(frame_for(row, 20), row)
    assert meta['actual_future_bars'] == 2 and meta['missing_future_reason'] == 'source_end'


def test_original_gap_is_an_error_and_frozen_output_cannot_change(tmp_path):
    row = sample()
    frame = frame_for(row)
    frame.loc[5, 'open_time'] += pack.BAR
    with pytest.raises(ValueError, match='incomplete'):
        pack.bounded_future_window(frame, row)
    path = tmp_path/'frozen.json'
    pack.frozen_write(path, b'one')
    pack.frozen_write(path, b'one')
    with pytest.raises(ValueError, match='differs'):
        pack.frozen_write(path, b'two')
    assert path.read_bytes() == b'one'
