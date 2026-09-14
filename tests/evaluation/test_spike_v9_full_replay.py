"""Synthetic equivalence and matched-control checks for the fixed V9 runner."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_exit_policy_study as base
from yoyo.evaluation import spike_v1_v8_be05 as engine
from yoyo.evaluation.spike_v9 import replay_v9, v9_admissions
from yoyo.evaluation.spike_v8_six_filters import assert_baseline_parity
from yoyo.evaluation.spike_v9_full_replay import prepare_v9, random_controls, serial_pair, check_completed


def fixture(start="2025-01-04", asset="ETH", seed=4):
    index = pd.date_range(start, periods=100, freq="h", tz="UTC")
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, .7, len(index)))
    bars = pd.DataFrame({"open": np.r_[100, close[:-1]], "close": close, "atr": 1., "rv": 2.,
                         "s20": 100., "e20": 100., "md": 1., "sb": 0., "ready": True,
                         "ropeHigh": close, "ropeLow": close}, index=index)
    bars["high"] = bars[["open", "close"]].max(axis=1) + .3
    bars["low"] = bars[["open", "close"]].min(axis=1) - .3
    signals = pd.DataFrame({"long_signal": False, "short_signal": False}, index=index)
    for j, i in enumerate(range(10, 95, 7)):
        signals.loc[index[i], "long_signal" if j % 2 == 0 else "short_signal"] = True
        bars.loc[index[i], "rv"] = 51. if j % 3 == 0 else 50.
    selected = np.flatnonzero(signals.any(axis=1))
    ledger = pd.DataFrame({"signal_bar_open": index[selected], "signal_i": selected})
    cache = {"bars": bars, "signals": signals, "v1_signals": signals.copy(),
             "bb": pd.DataFrame({"v7_ready": True, "prior_squeeze_run3": True}, index=index),
             "bb_ready": pd.Series(True, index=index), "data_gap": pd.Series(False, index=index), "tick": .01}
    return base.StreamContext(Path("."), "synthetic", {}, cache, ledger, 60,
                             {"asset": asset, "symbol": "ETHUSDC", "venue": "synthetic", "timeframe_min": 60})


@pytest.mark.parametrize("seed", range(6))
def test_fast_serial_matches_released_v9_adapter(seed):
    context = fixture(seed=seed)
    outputs, _, _ = serial_pair(context)
    original, _, _, _ = replay_v9(context)
    assert_baseline_parity(outputs["v9"][0], original)


@pytest.mark.parametrize("asset", ["ETH", "USDC", None])
def test_fast_admission_and_risk_match_released_v9(asset):
    context = fixture(asset=asset)
    prepared = engine.prepare_arm(context, arm="v8")
    modified, rows = prepare_v9(prepared)
    expected = v9_admissions(context)
    assert np.array_equal(modified.allowed, expected.v9.to_numpy(bool))
    assert np.array_equal(prepared.raw_side, modified.raw_side)
    for row in rows.itertuples(index=False):
        ref = expected.loc[row.signal_bar_open]
        assert row.v9_bundle_reasons == ref.v9_bundle_reasons
        assert row.reference_initial_risk == pytest.approx(ref.reference_initial_risk, nan_ok=True)
        assert row.reference_cost_r == pytest.approx(ref.reference_cost_r, nan_ok=True)


def test_future_changes_leave_admission_and_reference_values_unchanged():
    context = fixture()
    _, before = prepare_v9(engine.prepare_arm(context, arm="v8"))
    bars = context.cache["bars"]
    bars.iloc[70:, bars.columns.get_indexer(["close", "rv"])] = [200., 500.]
    _, after = prepare_v9(engine.prepare_arm(context, arm="v8"))
    pd.testing.assert_frame_equal(before.loc[before.local_i < 70], after.loc[after.local_i < 70])


def test_random_controls_are_reproducible_matched_and_use_parent_fixed_exit():
    context = fixture(start="2025-01-06")
    outputs, _, prepared = serial_pair(context)
    targets = pd.concat([outputs[arm][0] for arm in ("v8", "v9")], ignore_index=True)
    before = random_controls(prepared, targets)
    pd.testing.assert_frame_equal(before, random_controls(prepared, targets))
    matched = before.loc[before.matched]
    assert len(matched) > 0
    for row in matched.itertuples(index=False):
        assert row.control_signal_time != row.signal_bar_open
        assert row.control_signal_time.strftime("%Y-%m") == row.signal_bar_open.strftime("%Y-%m")
        i = int(prepared.frame.index.get_loc(row.control_signal_time))
        made = base._initial_position_fast(prepared.frame.index, prepared.open, prepared.high, prepared.low,
                                          prepared.close, prepared.atr, prepared.gap, i, row.side, prepared.spec)
        made["initial_risk_frac"] = made["initial_risk"] / made["entry_price"]
        ref = engine.replay_fixed_entry(context, pd.Series(made), arm="v8", enable_be=False, prepared=prepared)
        assert ref["net_r"] == row.control_net_r
        assert ref["exit_time"] == row.control_exit_time


def test_hash_receipt_rejects_tampered_output(tmp_path):
    import hashlib
    import json
    p = tmp_path / "file.csv"
    p.write_text("original")
    (tmp_path / "completion.json").write_text(json.dumps({"status": "complete", "run_identity": "fixed",
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()}}))
    assert check_completed(tmp_path, "fixed")["status"] == "complete"
    p.write_text("changed")
    with pytest.raises(ValueError, match="hash drift"):
        check_completed(tmp_path, "fixed")
