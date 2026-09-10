"""Synthetic frozen-schedule end-to-end audit; no real market files are read."""
import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_burst_noise_study as study


def synthetic_features():
    n = 100
    f = pd.DataFrame(dict(open=100., high=100.1, low=99.9, close=100., volume=100.,
        atr=1., md=-2., sb=-3., pastWidth=4., pastCrosses=0., ropeHigh=100.2,
        ropeLow=99.8, rv=1., expansion=1., ready=True, atr_pct=.01),
        index=pd.date_range('2026-07-10', periods=n, freq='h', tz='UTC'))
    f['history_count'] = np.arange(n)+400
    f['middle'] = 100 + np.arange(n) / 100
    f.loc[f.index[35:51], ['pastWidth', 'pastCrosses']] = [3., 2.]
    for i, close in ((40, 101.), (41, 102.), (42, 103.)):
        f.loc[f.index[i], ['open', 'high', 'low', 'close', 'volume']] = [close-.8, close+.1, close-.9, close, 150.]
    f.loc[f.index[43:], ['open', 'high', 'low', 'close']] = [103., 103.2, 102.8, 103.]
    f.loc[f.index[45], ['open', 'high', 'low', 'close']] = [103., 103.1, 98., 102.]
    for i, close in ((46, 103.), (47, 104.), (48, 105.)):
        f.loc[f.index[i], ['open', 'high', 'low', 'close', 'volume']] = [close-.8, close+.1, close-.9, close, 150.]
    f.loc[f.index[49:], ['open', 'high', 'low', 'close']] = [105., 105.2, 104.8, 105.]
    f['tr'] = pd.concat([f.high-f.low, (f.high-f.close.shift()).abs(),
        (f.low-f.close.shift()).abs()], axis=1).max(axis=1)
    f['recentLow'] = f.low.rolling(5, min_periods=1).min()
    f['recentHigh'] = f.high.rolling(5, min_periods=1).max()
    f.attrs['minutes'] = 60
    return f


@pytest.fixture
def synthetic_pool(monkeypatch, tmp_path):
    f = synthetic_features()
    instrument = 'SYNTH-USDT-SWAP'
    ctx = dict(instrument=instrument, asset='SYNTH', symbol='SYNTH/USDT:USDT', venue='okx', minutes=60)
    job = dict(ctx, tick=.01, features_path='synthetic-no-file', features_sha256='synthetic')
    labels = study.old.label_events(f)
    labels['instrument'] = instrument
    prior = tmp_path/'prior'
    prior.mkdir()
    baseline = study.base.replay(f.join(study.base.progressive_fields(f)), .01)
    indices = np.flatnonzero(baseline.burst.to_numpy())
    frozen = pd.DataFrame([dict(ctx, arm='v2', decision_i=int(i)) for i in indices])
    assert list(indices) == [42, 48]
    study.old.write_csv(prior/'signals.csv.gz', frozen)
    study.old.write_csv(prior/'labels.csv.gz', labels)
    study.old.write_csv(prior/'exposure.csv.gz', pd.DataFrame(dict(
        decision_time=f.index+study.old.HOUR, eligible_bars=1)))
    out = tmp_path/'experiment'/'results'
    monkeypatch.setattr(study, 'OUT', out)
    monkeypatch.setattr(study.auth, 'V2', prior)
    monkeypatch.setattr(study, 'pins', lambda: {'synthetic_builder': 'pinned'})
    monkeypatch.setattr(study.auth, 'authenticated_prior', lambda: ([job], pd.DataFrame(), {}, labels.copy()))
    monkeypatch.setattr(study, 'load_feature', lambda *args: f.copy(deep=True))
    monkeypatch.setattr(study, 'match_controls', lambda frame, all_indices, indices, *args:
        {i: [20] for i in indices})
    return f, out, prior


def test_synthetic_prepare_and_evaluate_freeze_true_replay_and_costs(synthetic_pool):
    f, out, prior = synthetic_pool
    study.prepare()
    prepared = json.loads((out/'prepared_manifest.json').read_text())
    assert prepared['status'] == 'complete'
    assert (out/'labels.csv.gz').read_bytes() == (prior/'labels.csv.gz').read_bytes()
    signals = study.read('signals.csv.gz')
    assert signals.loc[signals.arm.eq('v2'), 'decision_i'].tolist() == [42, 48]
    assert signals.loc[signals.arm.eq('episode'), 'decision_i'].tolist() == [42]
    changes = study.read('changes.csv.gz')
    assert changes.loc[changes.decision_i.eq(48), 'change'].item() == 'removed'
    blocked = study.read('blocked.csv.gz')
    assert 48 in blocked.decision_i.to_list()
    hashes = {a['path']: a['sha256'] for a in prepared['artifacts']}
    study.evaluate()
    for path, digest in hashes.items():
        assert study.old.sha(path) == digest
    result = json.loads((out/'validation_manifest.json').read_text())
    assert result['status'] == 'complete' and result['account_return_not_computed']
    trades = study.read('trade_events.csv.gz')
    assert len(trades) == 3
    assert trades.fee_bp.eq(20).all()
    assert trades.decision_i.add(1).eq(trades.entry_i).all()
    assert trades.loc[trades.decision_i.eq(42), 'entry_price'].eq(f.open.iloc[43]).all()
    full = study.read('summary.csv').query("period == 'full'").set_index('arm')
    assert full.loc['v2', 'signals'] == 2 and full.loc['episode', 'signals'] == 1
    assert full.loc['v2', 'positive_events'] == full.loc['episode', 'positive_events']
    with pytest.raises(ValueError, match='repeat|Repeated|repeated'):
        study.evaluate()
    with pytest.raises(ValueError, match='overwrite'):
        study.prepare()


def test_zero_available_random_controls_is_retained_not_fabricated(synthetic_pool, monkeypatch):
    _, out, _ = synthetic_pool
    monkeypatch.setattr(study, 'match_controls', lambda frame, all_indices, indices, *args:
        {i: [] for i in indices})
    study.prepare()
    assert study.read('controls.csv.gz').empty
    study.evaluate()
    assert study.read('trade_controls.csv.gz').empty
    full = study.read('summary.csv').query("period == 'full'")
    assert full.matched.eq(0).all()
    assert full.mean_excess_bp.isna().all()


def test_mismatched_prepared_config_rejected_before_scoring(synthetic_pool):
    _, out, _ = synthetic_pool
    study.prepare()
    path = out/'prepared_manifest.json'
    receipt = json.loads(path.read_text())
    receipt['config'] = dict(receipt['config'], direction='changed')
    path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError):
        study.evaluate()
    assert not (out/'evaluation_started.json').exists()
