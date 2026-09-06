"""Synthetic V5 orchestration checks; never open prices or actual outcomes."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.hourly_impulse_transition_research import (
    PARITY_COLUMNS, assert_parity, state_diagnostics, verify_config, adjust_contrasts, COHORTS,
)
from yoyo.evaluation.hourly_impulse_diagnostics import classify_trades


def frame():
    row = {key: 1.0 for key in PARITY_COLUMNS}
    row.update(event_id="a", entry_time=pd.Timestamp("2024-01-01", tz="UTC"),
               exit_time=pd.Timestamp("2024-01-01T00:05Z"), closed=True,
               outcome="colour_exit", net_return=-.001, gross_return=.001,
               hold_minutes=5, max_favourable_r=.4, max_adverse_r=-.1,
               ltf_entry_state="opposite")
    return pd.DataFrame([row])


def test_exit_label_only_can_differ_in_transition_parity():
    a = frame()
    b = a.assign(outcome="transition_colour_exit")
    assert_parity(a, b, transition=True)
    with pytest.raises(AssertionError):
        assert_parity(a, b)
    with pytest.raises(AssertionError):
        assert_parity(a, b.assign(net_return=.1), transition=True)


def test_state_diagnostics_preserve_unknown_and_nonclosed():
    a = frame()
    b = a.assign(event_id="b", ltf_entry_state="unknown", closed=False, net_return=np.nan)
    table, info = state_diagnostics(pd.concat([a, b]))
    assert info["state_counts"] == {"opposite": 1, "unknown": 1}
    indexed = table.set_index("state")
    assert indexed.loc["opposite", "fee_flips"] == 1
    assert indexed.loc["opposite", "exit_5min"] == 1
    assert indexed.loc["unknown", "requests"] == 1
    assert indexed.loc["unknown", "closed"] == 0


def test_transition_exit_is_in_colour_loss_family():
    a = classify_trades(frame().assign(outcome="transition_colour_exit"))
    assert bool(a.iloc[0]["colour_exit"])
    assert a.iloc[0]["primary_loss_reason"] == "cost_flip"


def test_config_rejects_added_entry_filter_or_mode():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root/"experiments/active/exp-btcusdtp-1h-colour-transition-preholdout-20260906-v5/config.json").read_text())
    base = json.loads((root/config["base_config"]).read_text())
    verify_config(config, base)
    with pytest.raises(RuntimeError):
        verify_config({**config, "exit_modes": config["exit_modes"]+["fixed_3r"]}, base)


@pytest.mark.parametrize("missing", [None, np.nan, np.inf])
def test_holm_missing_is_one_and_never_changes_finite_rank(missing):
    pairs = {COHORTS[0]: {"month_cluster_p": missing}, COHORTS[1]: {"month_cluster_p": .04}}
    adjust_contrasts(pairs)
    assert pairs[COHORTS[0]]["holm_two_p"] == 1
    assert pairs[COHORTS[1]]["holm_two_p"] == .08
