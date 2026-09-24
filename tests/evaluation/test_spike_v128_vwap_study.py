"""Behavioral checks for session causality and cached serial entry filtering."""
import json
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v128_vwap_study as study
from yoyo.evaluation.spike_burst_replay import features


def bars(index, price=None, volume=None):
    price = np.full(len(index), 100.) if price is None else np.asarray(price, float)
    return pd.DataFrame({'open': price, 'high': price+1, 'low': price-1, 'close': price,
                         'volume': np.ones(len(index)) if volume is None else volume}, index=index)


def test_volume_weight_is_distinct_from_same_window_time_weight():
    frame = bars(pd.date_range('2024-01-01', periods=2, freq='15min', tz='UTC'), [100, 110], [9, 1])
    result = study.session_features(frame, 15)
    assert result.vwap.iloc[-1] == 101
    assert result.twap.iloc[-1] == 105


def test_equal_volume_matches_twap_and_atr_matches_frozen_engine():
    index = pd.date_range('2024-01-01', periods=400, freq='15min', tz='UTC')
    frame = bars(index, 100+np.sin(np.arange(400)/5))
    result = study.session_features(frame, 15)
    np.testing.assert_allclose(result.vwap, result.twap)
    np.testing.assert_array_equal(result.atr, features(frame).atr)


def test_appending_future_and_changing_future_volume_cannot_change_prefix():
    index = pd.date_range('2024-01-01', periods=200, freq='15min', tz='UTC')
    frame = bars(index, 100+np.arange(200)*.1)
    expected = study.session_features(frame.iloc[:70], 15)
    frame.iloc[70:, frame.columns.get_loc('close')] = 9000
    frame.iloc[70:, frame.columns.get_loc('volume')] = 999999
    actual = study.session_features(frame, 15).iloc[:70]
    pd.testing.assert_frame_equal(actual, expected)


def test_midnight_is_owned_by_open_clock_and_resets_average():
    index = pd.date_range('2024-01-01', periods=97, freq='15min', tz='UTC')
    frame = bars(index, np.r_[np.full(96, 100.), 500.])
    result = study.session_features(frame, 15)
    assert result.vwap.iloc[95] == 100
    assert result.vwap.iloc[96] == 500


def test_gap_or_partial_session_stays_unknown_until_complete_next_day():
    index = pd.date_range('2024-01-01', periods=194, freq='15min', tz='UTC')
    frame = bars(index).drop(index[[0, 98]])
    result = study.session_features(frame, 15)
    assert result.loc['2024-01-01', 'vwap'].isna().all()
    assert result.loc['2024-01-02 00:00', 'vwap'] == 100
    assert result.loc['2024-01-02 00:45':'2024-01-02 23:45', 'vwap'].isna().all()
    assert result.loc['2024-01-03 00:00', 'vwap'] == 100


def test_zero_or_missing_volume_never_creates_a_vwap():
    index = pd.date_range('2024-01-01', periods=20, freq='15min', tz='UTC')
    frame = bars(index, volume=np.zeros(20))
    assert study.session_features(frame, 15).vwap.isna().all()
    frame.volume = 1.
    frame.loc[index[3], 'volume'] = np.nan
    result = study.session_features(frame, 15)
    assert result.vwap.iloc[3:].isna().all()


def test_feature_join_checks_event_clock_as_well_as_index():
    index = pd.date_range('2024-01-01', periods=20, freq='15min', tz='UTC')
    feature = study.session_features(bars(index), 15)
    row = pd.DataFrame({'signal_i': [14], 'signal_close': [index[15]]})
    assert study.attach_features(row, feature, 15).vwap_distance.iloc[0] == 0
    row.signal_close = index[14]
    with pytest.raises(ValueError, match='clocks differ'):
        study.attach_features(row, feature, 15)


def test_threshold_fit_does_not_use_outcomes_or_later_features():
    earlier = pd.DataFrame({'arm': 'v9_both', 'signal_close': pd.Timestamp('2024-01-01', tz='UTC'),
                            'timeframe_min': 15, 'vwap_distance': np.arange(100),
                            'twap_distance': np.arange(100)*2, 'net_return': np.arange(100)})
    later = earlier.copy()
    later.signal_close = pd.Timestamp('2025-02-01', tz='UTC')
    later.vwap_distance = -1e9
    cfg = {'analysis_start': '2023-01-01T00:00:00Z', 'split': '2025-01-01T00:00:00Z', 'timeframes': [15]}
    first = study.fit_thresholds([earlier], cfg)
    earlier.net_return = -999
    earlier['censored'] = True
    assert study.fit_thresholds([earlier, later], cfg) == first
    assert first['15']['vwap']['q50'] == 49.5


def event(i, end, *, passed=True, status='closed'):
    return {'signal_i': i, 'exit_i': end, 'trade_key': str(i), 'status': status,
            'vwap_near': passed, 'vwap_distance': 1. if passed else 9.}


def test_filter_frees_later_candidate_and_preserves_cached_exit():
    rows = [event(1, 10, passed=False), event(3, 5), event(6, 9)]
    base, _ = study.select_serial(rows, 'baseline', 30)
    gated, statuses = study.select_serial(rows, 'vwap_near', 30)
    assert [r['signal_i'] for r in base] == [1]
    assert [r['signal_i'] for r in gated] == [3, 6]
    assert [r['exit_i'] for r in gated] == [5, 9]
    assert statuses[0]['status'] == 'filtered_distance'


def test_common_carry_blocks_until_exit_and_same_close_can_reenter():
    rows = [event(3, 5), event(6, 9)]
    result, statuses = study.select_serial(rows, 'vwap_near', 30, (6, 'old'))
    assert [r['signal_i'] for r in result] == [6]
    assert statuses[0]['blocking_trade'] == 'old'


def test_censor_boundary_keeps_position_but_gap_releases_it():
    for status, count in [('censored_boundary', 1), ('censored_gap', 2)]:
        result, _ = study.select_serial([event(1, 4, status=status), event(5, 9)], 'baseline', 30)
        assert len(result) == count


def test_receipt_cannot_hide_changed_bytes(tmp_path):
    folder = tmp_path/'BTCUSDT_15m'
    folder.mkdir()
    path = folder/'a.csv'
    path.write_text('original')
    receipt = {'run_identity': 'id', 'status': 'complete', 'files': {'a.csv': study.digest(path)}}
    (folder/'receipt.json').write_text(json.dumps(receipt))
    manifest = {'run_identity': 'id', 'receipts': {folder.name: study.digest(folder/'receipt.json')}}
    study.verified_receipt(folder, manifest)
    path.write_text('changed')
    with pytest.raises(ValueError, match='changed stream artifact'):
        study.verified_receipt(folder, manifest)


def test_empty_legacy_csv_gets_headers_without_inventing_observations(tmp_path):
    path = tmp_path/'empty.csv.gz'
    pd.DataFrame().to_csv(path, index=False)
    result = study.read_table(path)
    assert result.empty
    assert result[result.signal_close >= pd.Timestamp('2025-01-01', tz='UTC')].empty
    assert result[result.arm.eq('v9_both')].empty
