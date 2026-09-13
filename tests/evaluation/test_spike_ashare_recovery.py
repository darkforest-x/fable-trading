"""Synthetic coverage-repair checks; no historical prices or scores are read."""
import json

import pandas as pd
import pytest

from yoyo.evaluation.spike_ashare_data import save
from yoyo.evaluation.spike_ashare_recovery import SINGLETON_ERROR, recover_singleton_coverage
from yoyo.evaluation.spike_ashare_study import identity, sha


@pytest.fixture
def experiment(tmp_path):
    data, out = tmp_path/'data', tmp_path/'results'
    (data/'daily').mkdir(parents=True)
    out.mkdir()
    dates = pd.bdate_range('2024-01-01', periods=5).strftime('%Y-%m-%d')
    daily = pd.DataFrame(dict(code='sh.synthetic', date=dates, open=10., high=11.,
                             low=9., close=10., volume=100., factor=1., tradestatus=1))
    source = data/'daily/sh.synthetic.csv'
    daily.to_csv(source, index=False)
    pd.DataFrame(dict(calendar_date=dates, is_trading_day=1)).to_csv(data/'calendar.csv', index=False)
    save(tmp_path/'config.json', dict(start=dates[0], end=dates[-1],
                                    versions=['v1', 'v8'], timeframes=['1D', '1W']))
    save(data/'collection_records.json', [dict(code='sh.synthetic', daily_sha256=sha(source))])
    save(data/'collection_progress.json', dict(status='complete'))
    save(out/'evaluation_errors.json', {'sh.synthetic': SINGLETON_ERROR})
    save(out/'evaluation_identity.json', identity(tmp_path/'config.json'))
    save(out/'background_status.json', dict(status='running', phase='final_reconciliation'))
    save(out/'summary.json', dict(status='incomplete'))
    return tmp_path


def test_recovers_only_unready_coverage_and_retains_failure_evidence(experiment, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('coverage repair must not evaluate indicators or outcomes')
    monkeypatch.setattr('yoyo.evaluation.spike_ashare_engine.build_signals', forbidden)
    monkeypatch.setattr('yoyo.evaluation.spike_ashare_engine.replay', forbidden)
    assert recover_singleton_coverage(experiment)
    out = experiment/'results'
    receipt = json.loads((out/'streams/sh.synthetic.json').read_text())
    assert len(receipt['coverage']) == 4
    assert {row['cycle_bars'] for row in receipt['coverage']} == {1, 5}
    assert all(row['ready_bars'] == row['signals'] == 0 for row in receipt['coverage'])
    assert all(row['status'] == 'warmup_insufficient' for row in receipt['coverage'])
    assert pd.read_csv(out/'streams/sh.synthetic.trades.csv.gz').empty
    audit = json.loads((out/'warmup_recovery.json').read_text())
    assert audit['sh.synthetic']['original_error'] == SINGLETON_ERROR
    assert audit['sh.synthetic']['receipt_sha256'] == sha(out/'streams/sh.synthetic.json')
    assert json.loads((out/'evaluation_errors.json').read_text()) == {}
    assert not recover_singleton_coverage(experiment)


def test_refuses_active_writer(experiment):
    save(experiment/'results/background_status.json', dict(status='running', phase='collecting'))
    with pytest.raises(ValueError, match='finished'):
        recover_singleton_coverage(experiment)


def test_refuses_changed_source(experiment):
    source = experiment/'data/daily/sh.synthetic.csv'
    source.write_text(source.read_text()+'\n')
    with pytest.raises(ValueError, match='source differs'):
        recover_singleton_coverage(experiment)


def test_refuses_changed_frozen_identity(experiment):
    save(experiment/'results/evaluation_identity.json', {})
    with pytest.raises(ValueError, match='identity changed'):
        recover_singleton_coverage(experiment)


def test_unrelated_failure_stays_isolated(experiment):
    save(experiment/'results/evaluation_errors.json', {'sh.synthetic': 'ValueError: source gap'})
    assert not recover_singleton_coverage(experiment)
    assert not (experiment/'results/streams/sh.synthetic.json').exists()


def test_can_resume_after_receipt_was_written_before_error_was_cleared(experiment):
    assert recover_singleton_coverage(experiment)
    path = experiment/'results/streams/sh.synthetic.json'
    previous = sha(path)
    save(experiment/'results/evaluation_errors.json', {'sh.synthetic': SINGLETON_ERROR})
    assert recover_singleton_coverage(experiment)
    assert sha(path) == previous


def test_does_not_accept_a_long_history_with_same_error_message(experiment):
    source = experiment/'data/daily/sh.synthetic.csv'
    daily = pd.read_csv(source)
    extra = daily.iloc[-1:].copy()
    extra['date'] = '2024-01-08'
    pd.concat([daily, extra]).to_csv(source, index=False)
    save(experiment/'data/collection_records.json', [dict(code='sh.synthetic', daily_sha256=sha(source))])
    assert not recover_singleton_coverage(experiment)
    assert not (experiment/'results/streams/sh.synthetic.json').exists()
