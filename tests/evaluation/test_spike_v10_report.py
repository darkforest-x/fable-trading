"""Guards for the V10 aggregation: reconciliation, buckets, holdout containment."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation.spike_v10 import ARMS
from yoyo.evaluation.spike_v10_report import REQUIRED, age_profile, attribution, summary_rows
from yoyo.evaluation.spike_v10_full_replay import CUT, replay_stream
from yoyo.evaluation.spike_v9_full_replay import random_controls
from yoyo.evaluation.spike_v9_full_report import event_keys

from tests.evaluation.test_spike_v10_full_replay import trending_fixture


def ledgers(seed=3):
    context = trending_fixture(seed=seed)
    outputs, decisions, treatment = replay_stream(context)
    trades = pd.concat([outputs[arm][0] for arm in ARMS], ignore_index=True)
    controls = random_controls(treatment, trades)
    for table in (trades, decisions, controls):
        for column in ("signal_bar_open", "entry_time", "exit_time", "control_signal_time",
                       "control_entry_time", "control_exit_time"):
            if column in table:
                table[column] = pd.to_datetime(table[column], utc=True)
        table["event_key"] = event_keys(table)
    trades["censored"] = trades.censored.astype(bool)
    return trades, decisions, controls


def test_required_file_set_covers_every_arm():
    assert REQUIRED == {f"{arm}.{kind}.csv.gz" for arm in ARMS for kind in ("trades", "fills")} | {
        "decisions.csv.gz", "controls.csv.gz"}
    assert len(REQUIRED) == 2 * len(ARMS) + 2


def test_attribution_reconciles_with_the_summary_delta():
    trades, decisions, controls = ledgers()
    summary = summary_rows(trades, controls)
    full = summary.loc[summary.period.eq("full")].set_index("arm")
    for arm in ARMS:
        if arm == "v9":
            continue
        detail = attribution(trades, arm)
        delta = full.loc[arm, "total_r"] - full.loc["v9", "total_r"]
        assert detail["serial_delta_r"] == pytest.approx(delta, abs=1e-6), arm
        assert detail["removed_events"] >= 0 and detail["retained_10r"] <= detail["v9_10r"]


def test_attribution_rejects_a_tampered_shared_outcome():
    trades, _, _ = ledgers()
    tampered = trades.copy()
    shared = set(trades.loc[trades.arm.eq("v9"), "event_key"]) & set(trades.loc[trades.arm.eq("v10_a200"), "event_key"])
    assert shared, "fixture must share at least one event between the arms"
    key = sorted(shared)[0]
    target = tampered.index[tampered.arm.eq("v10_a200") & tampered.event_key.eq(key)][0]
    tampered.loc[target, "net_r"] = float(tampered.loc[target, "net_r"]) + 5.0
    with pytest.raises(ValueError, match="shared event"):
        attribution(tampered, "v10_a200")


def test_age_profile_buckets_cover_every_v9_trade():
    trades, decisions, _ = ledgers()
    profile = age_profile(trades, decisions)
    closed = trades.loc[trades.arm.eq("v9") & ~trades.censored]
    assert int(profile.trades.sum()) == len(closed)
    assert profile.net_r.sum() == pytest.approx(float(closed.net_r.sum()), abs=1e-9)
    assert set(profile.bucket.astype(str)) >= {"no_break", "age_0"}


def test_summary_rows_keep_the_registered_arm_order():
    trades, _, controls = ledgers()
    summary = summary_rows(trades, controls)
    order = summary.loc[summary.period.eq("full"), "arm"].tolist()
    assert order == list(ARMS)


def test_nothing_in_the_fixture_reaches_the_holdout_cut():
    trades, _, _ = ledgers()
    assert trades.entry_time.max() < CUT
