"""Owner-stop safeguards using synthetic receipts and mocked child processes."""
import hashlib
import json

import pytest

from yoyo.evaluation import spike_ashare_background as background
from yoyo.evaluation.spike_ashare_data import collect, save


def test_owner_stop_refuses_collection_before_any_provider_access(tmp_path, monkeypatch):
    save(tmp_path/'config.json', {})
    save(tmp_path/'results/owner_stop.json', {'owner_instruction': 'enough data'})
    def forbidden(*args):
        raise AssertionError('must not query provider after owner stop')
    monkeypatch.setattr('yoyo.evaluation.spike_ashare_data.freeze', forbidden)
    with pytest.raises(ValueError, match='owner stopped collection'):
        collect(tmp_path/'config.json', tmp_path/'data')


def test_owner_stop_closeout_never_starts_collection_or_duplicate_evaluator(tmp_path, monkeypatch):
    out, data = tmp_path/'results', tmp_path/'data'
    save(data/'collection_records.json', [])
    save(out/'owner_stop.json', dict(processes={'evaluator': 123},
        source_records_sha256=hashlib.sha256((data/'collection_records.json').read_bytes()).hexdigest()))
    monkeypatch.setattr(background, 'EXP', tmp_path)
    monkeypatch.setattr(background, 'ROOT', tmp_path)
    monkeypatch.setattr(background, 'evaluator_alive', lambda pid: False)
    def forbidden(*args, **kwargs):
        raise AssertionError('must not start a parallel follow evaluator')
    monkeypatch.setattr(background.subprocess, 'Popen', forbidden)
    commands = []
    def child(command, **kwargs):
        commands.append(command)
        if command[3] == 'yoyo.evaluation.spike_ashare_delivery':
            save(out/'summary.json', dict(status='incomplete', covered_symbols=0, failed_symbols=0))
            save(out/'delivery_receipt.json', {})
    monkeypatch.setattr(background.subprocess, 'run', child)
    background.run()
    assert [c[3] for c in commands] == ['yoyo.evaluation.spike_ashare_study', 'yoyo.evaluation.spike_ashare_delivery']
    assert json.loads((out/'background_status.json').read_text())['owner_stopped'] is True
