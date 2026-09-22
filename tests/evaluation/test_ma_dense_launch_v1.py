"""Gate parity between the frozen research rules, the Python mirror and the Pine file."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import ma_dense_launch_v1_reference as ref

PINE = Path("yoyo/evaluation/pine/ma_dense_launch_v1.pine")
LEDGER = Path("experiments/active/exp-ma-profit3r-20260922-v1/selection_queue_round_017/selection_ledger.jsonl")
STAGE1_PREREG = Path("experiments/active/exp-15m-ma-launch-owner-autofill10000-v1/preregistration.json")
STAGE2_PREREG = Path("experiments/active/exp-15m-ma-launch-owner-perfect-filter10000-v1/preregistration.json")


def test_mirror_thresholds_match_the_frozen_preregistrations():
    stage1 = json.loads(STAGE1_PREREG.read_text())["morphology_gate"]
    for key, value in ref.STAGE1.items():
        assert stage1[key] == value, key
    stage2 = json.loads(STAGE2_PREREG.read_text())["hard_gates"]
    for key, value in ref.STAGE2.items():
        assert stage2[key] == value, key


def test_pine_file_carries_the_same_numbers():
    text = PINE.read_text()
    assert "//@version=6" in text
    # stage 1 and stage 2 comparisons, in the order the Pine gate lines use them
    for fragment in ("coreEnv <= 1.5", "endSpread <= 1.1", "maxBody <= 1.2", "coreProg >= -0.6",
                     "coreProg <= 1.3", "p2 >= 1.0", "p3 >= 1.25", "p5 >= 1.75", "slope1 >= 0.03",
                     "minCloseToMa <= 1.0", "maxCloseToEnv <= 1.9", "maxBodyToEnv <= 1.5",
                     "endSpread <= 0.95", "coreProg <= 1.0", "slope2 >= 0.02", "ratio <= 0.9",
                     "dec >= 2", "ratio <= 1.15", "flips >= 3", "touchRate >= 0.4", "closeQ75 <= 1.5",
                     "preBodyQ90 <= 1.1", "prePath <= 5.5", "preLast3 <= 1.0", "preFav <= 3.0",
                     "wickQ90 <= 2.0", "coreRev <= 1", "minProg >= 0.0", "posSteps >= 3",
                     "retrace <= 0.75", "postRev <= 1", "maxOppPost <= 0.8"):
        assert fragment in text, fragment
    assert "atr14[3]" in text          # ATR anchored at core end + 2
    assert "int startO = 4 + L" in text and "int endO = 5" in text


def test_pine_and_mirror_declare_the_same_unported_parts():
    text = PINE.read_text().lower()
    assert "similarity" in text and "quality score" in text
    assert "superset" in text and "superset" in ref.__doc__.lower()


def _frame(n: int = 400, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, .05, n))
    frame = pd.DataFrame({"open": np.r_[close[0], close[:-1]], "high": close + .05,
                          "low": close - .05, "close": close})
    return ref.add_features(frame)


def test_quiet_random_walk_produces_no_signal():
    frame = _frame()
    assert not any(d is not None and d.signal
                   for i in range(140, len(frame))
                   for d in (ref.evaluate(frame, i, side, length) for side in ("LONG", "SHORT") for length in (4, 5)))


def test_incomplete_window_returns_none():
    frame = _frame()
    assert ref.evaluate(frame, 10, "LONG", 4) is None           # not enough pre-core history
    assert ref.evaluate(frame, len(frame) + 1, "LONG", 4) is None
    with pytest.raises(ValueError):
        ref.evaluate(frame, 200, "LONG", 6)


def test_pine_rma_matches_wilder_recursion():
    values = np.arange(1.0, 21.0)
    out = ref.pine_rma(values, 14)
    assert np.isnan(out[:13]).all()
    assert out[13] == pytest.approx(values[:14].mean())
    assert out[14] == pytest.approx((out[13] * 13 + values[14]) / 14)


@pytest.mark.skipif(not LEDGER.exists(), reason="frozen candidate ledger absent")
def test_frozen_grade_a_candidates_still_pass_both_gate_sets():
    rows = []
    for line in LEDGER.open():
        row = json.loads(line)
        if Path(row["source_path"]).exists():
            rows.append(row)
        if len(rows) >= 24:
            break
    if not rows:
        pytest.skip("no local source files for the frozen candidates")
    cache: dict[str, pd.DataFrame] = {}
    for row in rows:
        path = row["source_path"]
        if path not in cache:
            cache[path] = ref.add_features(pd.read_csv(path))
        decision = ref.evaluate(cache[path], int(row["source_core_end_i"]) + 5,
                                row["direction"], int(row["core_bars"]))
        assert decision is not None and decision.signal, row["event_id"]
