"""Three-view preparation must preserve the original decision and event split."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pytest

from yoyo.datasets.ma_morphology_threeview import render_views, validate_event


def sample():
    n = 1300
    time = pd.date_range('2025-04-01', periods=n, freq='15min', tz='UTC')
    price = 100 + np.arange(n) * .01 + np.sin(np.arange(n) / 10) * .2
    frame = pd.DataFrame({'open_time': time, 'open': price, 'high': price + .1,
                          'low': price - .1, 'close': price + .01, 'volume': 100.})
    event = {'event_id': 'event', 'bar_minutes': 15, 'direction': 'LONG', 'core_bars': 5,
             'core_start_time': time[1220].isoformat(), 'core_end_time': time[1224].isoformat(),
             'split': 'train', 'profit': {'retained': True, 'decision_close_time_utc': time[1230].isoformat(),
                                        'label_window_end_utc': time[1278].isoformat()}}
    plan = {'splits': {'train_end_exclusive': '2026-01-01T00:00:00Z',
                      'validation_end_exclusive': '2026-05-01T00:00:00Z',
                      'test_end_exclusive': '2026-09-21T16:00:00Z'}}
    return frame, event, plan


def test_future_mutation_preserves_all_views_and_boxes():
    frame, event, plan = sample()
    validate_event(event, plan)
    original = render_views(frame, event)
    changed = frame.copy()
    changed.loc[1230:, ['open', 'high', 'low', 'close']] *= 10
    altered = render_views(changed, event)
    assert set(original) == {'P7', 'P9', 'P11'}
    for variant in original:
        assert original[variant]['png'] == altered[variant]['png']
        assert original[variant]['label'] == altered[variant]['label']
        assert original[variant]['visible']['window_end_i'] == 1229
    assert len({v['box']['cx_norm'] for v in original.values()}) == 3


def test_shorter_history_is_not_allowed_to_silently_change_ma_warmup():
    frame, event, _ = sample()
    with pytest.raises(ValueError, match='warmup'):
        render_views(frame.iloc[10:].reset_index(drop=True), event)


def test_known_gap_is_rejected_but_future_gap_is_irrelevant():
    frame, event, _ = sample()
    render_views(frame.drop(index=1240).reset_index(drop=True), event)
    with pytest.raises(ValueError, match='gap'):
        render_views(frame.drop(index=1219).reset_index(drop=True), event)


def test_split_membership_and_original_decision_are_not_rewritten():
    _, event, plan = sample()
    wrong = deepcopy(event)
    wrong['split'] = 'val'
    with pytest.raises(ValueError, match='split'):
        validate_event(wrong, plan)
    wrong = deepcopy(event)
    wrong['profit']['decision_close_time_utc'] = '2025-04-13T19:15:00Z'
    with pytest.raises(ValueError, match='confirmation'):
        validate_event(wrong, plan)
