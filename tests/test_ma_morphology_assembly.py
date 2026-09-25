"""Assembly preserves chronology, visible-only labels and conservative rejects."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.datasets.ma_morphology_assembly import event_split, overlaps, screen_negative
from yoyo.datasets.fifteen_minute_launch_candidates import add_candidate_features
from yoyo.datasets.ma_launch_owner_yolo_dataset import negative_feature_masks

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = json.loads((ROOT / 'experiments/active/exp-15m-ma-launch-owner-grade-a8000-neg24000-v1/preregistration.json').read_text())
PARENT = json.loads((ROOT / 'experiments/active/exp-ma-morphology-negatives-20260922-v3/plan.json').read_text())


def example():
    t = pd.date_range('2025-04-01', periods=1300, freq='15min', tz='UTC')
    close = 100 + .01 * np.sin(np.arange(1300))
    frame = pd.DataFrame({'open_time': t, 'open': close-.002,
                          'high': close+.1, 'low': close-.1,
                          'close': close, 'volume': 100.})
    row = {'core_start_time': t[1211].isoformat(), 'core_end_time': t[1215].isoformat(),
           'core_bars': 5, 'negative_kind': 'hard', 'split': 'train'}
    metrics = negative_feature_masks(add_candidate_features(frame), core_len=5, prereg=PROTOCOL)
    fields = {'ma_envelope_atr': 'ma_envelope_atr', 'ma_spread_end_atr': 'ma_spread_end_atr',
              'max_body_atr': 'max_body_atr', 'candle_envelope_atr': 'candle_envelope_atr',
              'minimum_close_to_ma_atr': 'minimum_close_to_ma_atr',
              'abs_close_progress_atr_core_plus_2': 'close2',
              'abs_close_progress_atr_core_plus_3': 'close3',
              'abs_close_progress_atr_core_plus_5': 'close5',
              'two_sided_excursion_atr_core_plus_1_to_5': 'excursion'}
    row.update({old: float(metrics[key][1215]) for old, key in fields.items()})
    return frame, row


def test_visible_sideways_hard_negative_and_future_mutation():
    frame, row = example()
    result, support = screen_negative(frame, row, PROTOCOL)
    assert result['accepted']
    assert len(support) == 1221
    frame.loc[1221:, ['open', 'high', 'low', 'close']] *= 20
    changed, changed_support = screen_negative(frame, row, PROTOCOL)
    assert result == changed
    pd.testing.assert_frame_equal(support, changed_support)


def test_visible_gap_and_legacy_metric_change_are_rejected():
    frame, row = example()
    result, _ = screen_negative(frame.drop(index=1210), row, PROTOCOL)
    assert not result['accepted']
    row['ma_envelope_atr'] += 1
    result, _ = screen_negative(frame, row, PROTOCOL)
    assert 'legacy_feature_identity_drift' in result['reasons']


def test_visible_launch_is_not_an_empty_label():
    frame, row = example()
    # A large move in the rightmost visible bar must disqualify a background.
    frame.loc[1220, ['open', 'high', 'low', 'close']] += 3
    result, _ = screen_negative(frame, row, PROTOCOL)
    assert not result['accepted']
    assert 'other_visible_dense_motion' in result['reasons']


def test_old_validation_cannot_be_recycled_into_train():
    _, row = example()
    row['split'] = 'val'
    assert event_split(row, PARENT) is None
    row.update(core_start_time='2026-02-01T00:00:00Z', core_end_time='2026-02-01T01:00:00Z')
    assert event_split(row, PARENT) == 'val'
    row.update(core_start_time='2025-12-31T23:00:00Z', core_end_time='2026-01-01T00:00:00Z')
    assert event_split(row, PARENT) is None


def test_protection_includes_exact_boundary_contact():
    assert overlaps(10, 20, [(20, 30)])
    assert overlaps(30, 40, [(20, 30)])
    assert not overlaps(31, 40, [(20, 30)])
