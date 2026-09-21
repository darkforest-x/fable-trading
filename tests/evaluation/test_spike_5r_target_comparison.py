"""A target-only comparison must preserve every non-model path."""
import pandas as pd
import pytest

from yoyo.evaluation import spike_5r_target_comparison as c
from yoyo.evaluation.spike_5r_target_comparison import assert_control_parity


def test_model_paths_can_change_but_control_paths_cannot():
    old = pd.DataFrame(dict(rule=['original_all', 'risk_top5', 'logistic_top5'],
                            event_key=['a', 'b', 'c'], net_r=[6., 10., 8.]))
    new = old.copy()
    new.loc[2, 'event_key'] = 'new_model_entry'
    new.loc[2, 'net_r'] = -1.
    assert_control_parity(old, new.iloc[::-1])
    new.loc[1, 'net_r'] = 10.001
    with pytest.raises(AssertionError):
        assert_control_parity(old, new)


def test_lost_original_entry_is_detected_even_if_mean_is_same():
    old = pd.DataFrame(dict(rule=['original_all', 'original_all'],
                            event_key=['a', 'b'], net_r=[6., 6.]))
    with pytest.raises(AssertionError):
        assert_control_parity(old, old.iloc[:1])


def test_serial_rejects_changed_adjacent_receipt_before_accepting_trade_hashes(tmp_path, monkeypatch):
    prediction = tmp_path/'prediction_v1'
    evaluation = tmp_path/'evaluation_v1'
    prediction.mkdir()
    evaluation.mkdir()
    pr = prediction/'prediction_receipt.json'
    er = evaluation/'evaluation_receipt.json'
    pr.write_text('{}')
    er.write_text('{}')
    serial = dict(complete=True, streams=3531, baseline_parity=True,
                  candidate_economic_parity=True, prediction_receipt_sha256=c.s.digest(pr),
                  evaluation_receipt_sha256=c.s.digest(er))
    monkeypatch.setattr(c.new, 'verify_output', lambda *args: serial)
    pr.write_text('{"replacement": true}')
    with pytest.raises(ValueError, match='receipt drift'):
        c.load_serial(tmp_path/'serial_v1')


@pytest.mark.parametrize('field', ['complete', 'baseline_parity', 'candidate_economic_parity', 'streams'])
def test_serial_requires_recorded_completion_and_parity(tmp_path, monkeypatch, field):
    serial = dict(complete=True, streams=3531, baseline_parity=True, candidate_economic_parity=True)
    serial[field] = False
    monkeypatch.setattr(c.new, 'verify_output', lambda *args: serial)
    with pytest.raises(ValueError, match='not certified'):
        c.load_serial(tmp_path/'serial_v1')
