"""Contract tests for the V10.4 joint 1h forward test (pipeline and TP exits)."""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import spike_v104_forward as fwd
from yoyo.evaluation import spike_v10_4_study as study

RUN_V1 = Path("experiments/active/exp-spike-v10-4-joint-multitf-20260918-v1/statistics/run_v1/trades.csv.gz")
SYMBOL = "DEEPUSDT"


def _stream():
    path = study.series_files().get(SYMBOL)
    if path is None or not RUN_V1.exists():
        pytest.skip("frozen Binance archive or run_v1 ledger absent")
    since = study.START - pd.Timedelta(minutes=1500 * 60)
    base = fwd.load_5m(path, None, since, study.DATA_END)
    bars, _ = study.aggregate(base.loc[base.index >= since.floor("60min")], 60)
    return bars, study.symbol_meta()[SYMBOL]


@pytest.fixture(scope="module")
def stream():
    return _stream()


def test_pipeline_copy_reproduces_original_1h_joint_ledger(stream):
    bars, meta = stream
    out = fwd.run_stream(SYMBOL, bars, meta, study.START, study.DATA_END, {"original": None}, fwd.config())
    got = out["trades"].sort_values("signal_i").reset_index(drop=True)
    old = pd.read_csv(RUN_V1)
    old = old[(old.symbol == SYMBOL) & (old.timeframe == "1h") & (old.arm == "joint")].sort_values("signal_i").reset_index(drop=True)
    assert len(got) == len(old) >= 5
    for col in ("signal_i", "entry_i", "exit_i", "exit_reason"):
        assert got[col].tolist() == old[col].tolist(), col
    np.testing.assert_allclose(got.exit_price.astype(float), old.exit_price.astype(float))
    np.testing.assert_allclose(got.net_r.astype(float), old.net_r.astype(float), equal_nan=True)


def test_no_target_matches_published_engine_for_every_candidate(stream):
    bars, meta = stream
    facts, joint, window = fwd.joint_candidates(bars, meta, study.START, study.DATA_END)
    prepared = study.prepared_arm(facts["frame"], facts["gap"], facts["side"], "k", {"symbol": SYMBOL}, 60, float(meta["tick"]))
    for i in np.flatnonzero(window & (facts["v9_long"] | joint.joint_event))[:40]:
        a = fwd.attempt(prepared, int(i), None)
        from yoyo.evaluation import spike_v10_4_increment as execution
        b = execution.attempt(prepared, int(i))
        assert a[0] == b[0]
        if a[1] is not None:
            for k in ("exit_i", "exit_reason", "exit_price", "net_return", "censored"):
                assert (a[1][k] == b[1][k]) or (isinstance(a[1][k], float) and math.isnan(a[1][k]) and math.isnan(b[1][k])), k


def test_target_fills_at_price_and_same_bar_touch_counts_as_stop(stream):
    bars, meta = stream
    facts, joint, window = fwd.joint_candidates(bars, meta, study.START, study.DATA_END)
    prepared = study.prepared_arm(facts["frame"], facts["gap"], facts["side"], "k", {"symbol": SYMBOL}, 60, float(meta["tick"]))
    i = int(np.flatnonzero(joint.joint_event & window)[0])
    status, res = fwd.attempt(prepared, i, 0.3)
    assert status == "closed"
    tick = float(meta["tick"])
    target = math.ceil((res["entry_price"] + .3 * res["initial_risk"]) / tick - 1e-9) * tick
    if res["exit_reason"].startswith("take_profit"):
        assert math.isclose(res["exit_price"], target) and res["net_return"] == pytest.approx(target / res["entry_price"] - 1.002)
    # Force the entry bar to touch both the stop and the target: the stop must win.
    j = int(res["entry_i"])
    high, low = prepared.high.copy(), prepared.low.copy()
    high[j], low[j] = target * 2, res["initial_stop"] / 2
    forced = replace(prepared, high=high, low=low)
    _, both = fwd.attempt(forced, i, 0.3)
    assert both["exit_reason"].startswith("initial_stop") and both["exit_i"] == j


def test_wilson_interval():
    lo, hi = fwd.wilson(8, 10)
    assert round(lo, 3) == 0.490 and round(hi, 3) == 0.943
    lo, hi = fwd.wilson(10, 10)
    assert round(lo, 3) == 0.722 and hi == pytest.approx(1.0)
