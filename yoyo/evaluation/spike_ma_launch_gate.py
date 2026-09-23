"""Causal adapter of training selection morphology to existing SPIKE decisions.

Owner requested this offline comparison on 2026-09-23. Columns consumed are
OHLC, close SMA/EMA20/60/120 and ATR14. The latest read is the existing SPIKE
decision close; each core ends five bars earlier, with twelve pre-core bars.
There is no access to future 3R labels or score-ranked temporal NMS. Features
must be computed causally with ``ma_dense_launch_v1_reference.add_features``.
The two core lengths follow the original miner's stage-1 distance selection
before the chosen core is subjected to stage 2; a failed chosen core cannot
fall back to a different geometry that happened to pass stage 2.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation import ma_dense_launch_v1_reference as rules


def stage1_distance(frame, confirm_i, direction, core_bars, pack):
    """Original 14-feature/4x10 nearest-reference distance, no stage-2 gate."""
    features, sequence, _ = rules.profiles(frame, confirm_i, direction, core_bars)
    p = pack['stage1']
    scales = np.asarray(p['feature_scales'], float)
    return min(float(p['feature_weight']) * np.sqrt(np.mean(((features-np.asarray(f))/scales)**2))
               + float(p['sequence_weight']) * np.sqrt(np.mean((sequence-np.asarray(s))**2))
               for f, s in zip(p['features'], p['sequences']))


def select_stage1(cores, max_distance):
    """Same-endpoint pick_4_5: stage-1 pass, distance <= cap, min(distance, n)."""
    eligible = [c for c in cores if c['stage1'] and np.isfinite(c['distance'])
                and c['distance'] <= max_distance]
    return min(eligible, key=lambda c: (c['distance'], c['core_bars'])) if eligible else None


def evaluate_gate(feature_frame, confirm_i, side, pack, minutes=15):
    """Evaluate at one existing SPIKE close without moving its entry clock.

    Gate support is the available precomputed MA history, as in the frozen
    Pine rules. The entire 12+core+5 local window must have a continuous clock.
    The dataset renderer's 1200-bar warmup is not an additional entry filter.
    """
    if side not in (-1, 1):
        raise ValueError('SPIKE side must be -1 or 1')
    if not 0 <= confirm_i < len(feature_frame):
        raise ValueError('decision index outside feature frame')
    direction = 'LONG' if side == 1 else 'SHORT'
    output = {'ma_known': False, 'ma_density_only': False, 'ma_hard': False,
              'ma_grade_a': False, 'selected_core_bars': None,
              'stage1_distance': None, 'quality_score': None, 'reason': 'unknown',
              'decision_close': (feature_frame.index[confirm_i]+pd.Timedelta(minutes=minutes)).isoformat(),
              'cores': []}
    windows = {}
    for n in (4, 5):
        start = confirm_i-5-n+1-12
        if start < 0:
            continue
        window = feature_frame.iloc[start:confirm_i+1]
        if not isinstance(window.index, pd.DatetimeIndex) or not np.all(np.diff(window.index.asi8)==pd.Timedelta(minutes=minutes).value):
            continue
        local_i = len(window)-1
        decision = rules.evaluate(window, local_i, direction, n)
        if decision is None:
            continue
        output['ma_known'] = True
        output['ma_density_only'] |= decision.metrics['end_spread_atr'] <= rules.STAGE2['max_six_ma_end_bandwidth_atr']
        output['ma_hard'] |= decision.signal
        distance = float(stage1_distance(window, local_i, direction, n, pack)) if decision.stage1 else float('nan')
        output['cores'].append({'core_bars': n, 'stage1': decision.stage1, 'stage2': decision.stage2,
                                'distance': distance, 'metrics': dict(decision.metrics)})
        windows[n] = window
    chosen = select_stage1(output['cores'], float(pack['stage1']['max_distance']))
    if chosen is not None:
        n = chosen['core_bars']
        result = rules.evaluate_full(windows[n], len(windows[n])-1, direction, n, pack)
        output['selected_core_bars'] = n
        output['stage1_distance'] = chosen['distance']
        value = float(result['quality_score'])
        output['quality_score'] = value if np.isfinite(value) else None
        output['ma_grade_a'] = bool(result['grade_a'])
    output['reason'] = ('grade_a' if output['ma_grade_a'] else 'hard_only' if output['ma_hard']
                        else 'shape_rejected' if output['ma_known'] else 'insufficient_or_gapped_support')
    # JSON-compatible booleans and absent distances; retain both core audits.
    for key in ('ma_known', 'ma_density_only', 'ma_hard', 'ma_grade_a'):
        output[key] = bool(output[key])
    for core in output['cores']:
        if not np.isfinite(core['distance']):
            core['distance'] = None
    return output
