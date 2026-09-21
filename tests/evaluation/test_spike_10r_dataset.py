from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_10r_dataset as subject


def _candidate_rows():
    stamp = pd.Timestamp("2025-01-01T00:00:00Z")
    return pd.DataFrame({"event_key": ["k:100:1", "k:200:1"], "stream_key": ["k", "k"],
                         "signal_i": [100, 200], "local_i": [10, 20], "signal_bar_open": [stamp, stamp + pd.Timedelta(hours=1)],
                         "available_at": [stamp + pd.Timedelta(hours=1), stamp + pd.Timedelta(hours=2)], "side": [1, 1]})


def test_candidate_table_keeps_every_v9_long_even_when_a_feature_would_be_unknown(monkeypatch):
    index = pd.date_range("2025-01-01", periods=5, freq="h", tz="UTC")
    decisions = pd.DataFrame({"local_i": [0, 1, 2], "signal_i": [10, 11, 12], "signal_bar_open": index[:3],
                              "side": [1, 1, -1], "v9": [True, False, True], "stream_key": ["k"] * 3})
    prepared = SimpleNamespace(context=SimpleNamespace(minutes=60), gap=np.zeros(5, bool))
    monkeypatch.setattr(subject.source_replay, "prepare_v9", lambda value: (value, decisions))
    _, rows = subject._candidate_table(prepared)
    assert rows.signal_i.tolist() == [10]
    assert rows.event_key.tolist() == ["k:10:1"]


def test_independent_outcomes_retain_invalid_candidate_and_translate_original_ordinal(monkeypatch):
    candidates = _candidate_rows()
    index = pd.date_range("2025-01-01", periods=40, freq="h", tz="UTC")
    prepared = SimpleNamespace(frame=pd.DataFrame(index=index), open=np.zeros(40), high=np.zeros(40), low=np.zeros(40),
                               close=np.zeros(40), atr=np.ones(40), gap=np.zeros(40, bool), spec=object())
    context = SimpleNamespace(key="k")
    seen = []

    def initial(*args):
        i = args[7]
        if i == 20:
            return None
        return {"entry_time": index[i + 1], "entry_price": 100., "initial_stop": 98., "initial_risk": 2.,
                "signal_bar_open": index[i], "side": 1}

    def replay(_context, row, **_kwargs):
        seen.append((int(row.signal_i), int(row.entry_i)))
        return {"signal_i": row.signal_i, "entry_i": row.entry_i, "entry_time": row.entry_time, "side": 1,
                "entry_price": 100., "initial_stop": 98., "initial_risk": 2., "initial_risk_frac": .02,
                "exit_i": 105, "exit_time": index[15], "exit_price": 122., "exit_reason": "trailing_stop",
                "gross_return": .22, "net_return": .218, "gross_r": 11., "net_r": 10.9, "mfe_r": 12.,
                "censored": False, "exit_time_precision": "within_bar"}

    monkeypatch.setattr(subject.base, "_initial_position_fast", initial)
    monkeypatch.setattr(subject.engine, "replay_fixed_entry", replay)
    result = subject._outcomes(context, prepared, candidates)
    assert seen == [(100, 101)]
    assert result.valid_entry.tolist() == [True, False]
    assert bool(result.censored.iloc[1]) is True
    assert pd.isna(result.label_gt10.iloc[1])
    assert bool(result.label_gt10.iloc[0]) is True


def test_common_serial_parity_covers_censored_rows_and_rejects_missing_event():
    outcomes = pd.DataFrame({"signal_i": [100], "valid_entry": [True], "entry_i": [101], "side": [1], "censored": [True],
                             "entry_price": [100.], "initial_stop": [98.], "initial_risk": [2.], "exit_i": [105],
                             "exit_price": [np.nan], "gross_return": [np.nan], "net_return": [np.nan], "gross_r": [np.nan],
                             "net_r": [np.nan], "mfe_r": [3.], "signal_bar_open": [pd.Timestamp("2025-01-01", tz="UTC")],
                             "entry_time": [pd.Timestamp("2025-01-01 01:00", tz="UTC")], "exit_time": [pd.Timestamp("2025-01-02", tz="UTC")],
                             "exit_reason": ["boundary_mark"]})
    archived = outcomes.drop(columns="valid_entry").copy()
    subject._assert_common_event_parity(outcomes, archived)
    with pytest.raises(AssertionError, match="omitted"):
        subject._assert_common_event_parity(outcomes.iloc[:0], archived)
