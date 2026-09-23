"""Guard expanded-history inputs and parity of the diagnostic-free replay."""
import numpy as np
import pandas as pd
import pytest
from yoyo.evaluation import spike_v128_long_replay as study
from yoyo.evaluation import spike_v128_recent as parent


def rows(times):
    return pd.DataFrame({'ts': times, 'open': 100., 'high': 101., 'low': 99., 'close': 100., 'volume': 10.})


def test_merge_overlap_keeps_original_and_does_not_fill_gaps():
    cfg = {'warmup_start': '1970-01-01T00:05Z', 'end': '1970-01-01T00:30Z'}
    a = rows([0, 300000, 600000]); b = rows([600000, 1200000, 1500000, 1800000])
    b.loc[0, 'close'] += 1e-13
    result, overlap = study.merge_history(a, b, cfg)
    assert result.ts.tolist() == [300000, 600000, 1200000, 1500000]
    assert result.loc[result.ts == 600000, 'close'].iloc[0] == 100.
    assert overlap == 1


def test_merge_conflicting_overlap_fails_closed():
    a = rows([0, 300000]); b = rows([300000, 600000]); b.loc[0, 'volume'] = 20.
    with pytest.raises(ValueError, match='overlap conflict'):
        study.merge_history(a, b, {'warmup_start': '1970-01-01Z', 'end': '1970-01-02Z'})


def test_merge_duplicate_or_nonfinite_source_fails():
    cfg = {'warmup_start': '1970-01-01', 'end': '1970-01-02'}
    with pytest.raises(ValueError, match='duplicate'):
        study.merge_history(rows([0, 0]), rows([300000]), cfg)
    bad = rows([0]); bad.loc[0, 'high'] = np.nan
    with pytest.raises(ValueError, match='non-finite'):
        study.merge_history(bad, rows([300000]), cfg)


@pytest.mark.parametrize('minutes', [15, 60])
def test_omitting_hint_diagnostics_preserves_all_core_ledgers(minutes, monkeypatch):
    n = 18000; rng = np.random.default_rng(42)
    close = 100 * np.exp(np.cumsum(rng.normal(0, .002, n)))
    opens = np.r_[close[0], close[:-1]]
    base = pd.DataFrame({'open': opens, 'high': np.maximum(opens, close) * 1.001,
                         'low': np.minimum(opens, close) * .999, 'close': close,
                         'volume': rng.uniform(80, 200, n)},
                        index=pd.date_range('2022-11-01', periods=n, freq='5min', tz='UTC'))
    cfg = {'warmup_start': '2022-11-01T00:00Z', 'start': '2022-11-15T00:00Z',
           'split': '2022-12-01T00:00Z', 'end': '2023-01-01T00:00Z',
           'control_seed': 923128, 'vol_bins': [.005, .01, .02, .05, .1], 'round_trip_cost': .002}
    # Inject a small deterministic event tape; this test targets replay/serialization
    # parity, while detector formulas retain their existing reference tests.
    original_facts = parent.facts_for
    def fixture_facts(bars, source, asset, tick, timeframe):
        facts = original_facts(bars, source, asset, tick, timeframe)
        events = np.zeros(len(bars), dtype=bool)
        events[np.arange(700, len(bars), 500)] = True
        facts['v9'] = events
        facts['v9_long'] = events
        facts['side'] = np.where(events, 1, facts['side'])
        return facts
    monkeypatch.setattr(parent, 'facts_for', fixture_facts)
    monkeypatch.setattr(study, 'facts_for', fixture_facts)
    expected, _ = parent.run_stream(base, 'BTCUSDT', {'asset': 'BTC', 'tick': .1}, minutes, cfg)
    actual, summary = study.ledger_stream(base, 'BTCUSDT', {'asset': 'BTC', 'tick': .1}, minutes, cfg)
    assert len(actual['decisions']) > 0
    for name in ('decisions', 'trades', 'statuses', 'controls'):
        pd.testing.assert_frame_equal(actual[name], expected[name])
    assert actual['frames'].empty and actual['hints'].empty
    assert summary['hint_diagnostics_omitted']
