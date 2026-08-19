"""The ported numeric baseline must compute what yoyo-eth computed.

Two checks, because each covers what the other cannot:

  golden values   pinned numbers for MA / EMA / ATR / dispersion / candidate
                  positions on fixed synthetic bars. Self-contained, so it keeps
                  working after yoyo-eth is archived and would catch a change
                  even if the source repository were gone.

  cross-check     the same computation run through ~/yoyo-eth's own package
                  when it is present on this machine, which is the only thing
                  that proves the golden values were not simply generated from
                  the port's own bug.

The golden values are deliberately not derived at test time from the module
under test. A fixture that computes its own expectation cannot fail.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.layers.l1_detection.numeric_baseline import indicators as ind_mod
from yoyo.layers.l1_detection.numeric_baseline import scanner as scanner_mod
from yoyo.layers.l1_detection.numeric_baseline.features import FEATURE_COLUMNS, add_features

REPO = Path(__file__).resolve().parents[2]
SOURCE_REPO = Path.home() / "yoyo-eth"
DECISION = 250
TOLERANCE = 1e-12

#: Computed once from the port and cross-checked below against yoyo-eth itself.
GOLDEN_AT_BAR_250 = {
    "sma_20": 82.0456171464,
    "sma_60": 84.4350315071,
    "sma_120": 85.3220716724,
    "ema_20": 81.9954901542,
    "ema_60": 83.7288242369,
    "ema_120": 85.5405542659,
    "atr_14": 0.8223928962,
    "ma_upper": 85.5405542659,
    "ma_lower": 81.9954901542,
    "cluster_center": 84.0819278720,
    "ma_dispersion_atr": 4.3106696669,
}
GOLDEN_EVENT_POSITIONS = [21, 81, 340, 377]
GOLDEN_FEATURE_COUNT = 27


def _future_mutation_module():
    spec = importlib.util.spec_from_file_location(
        "_fm_helpers", REPO / "tests" / "causality" / "test_future_mutation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_bars() -> pd.DataFrame:
    """The same synthetic series the causality tests use, seed 7, 400 bars."""
    return _future_mutation_module().make_bars(400, seed=7)


def _prepared(df: pd.DataFrame) -> pd.DataFrame:
    return add_features(
        scanner_mod.add_dispersion(ind_mod.add_indicators(df)), compression_threshold=1.5
    )


@pytest.mark.parametrize(("column", "expected"), sorted(GOLDEN_AT_BAR_250.items()))
def test_indicator_matches_its_golden_value(column: str, expected: float):
    value = float(_prepared(make_bars()).loc[DECISION, column])
    assert value == pytest.approx(expected, rel=1e-9), (
        f"{column} at bar {DECISION} is {value!r}, not {expected!r}. The numeric "
        "baseline changed; the published MVP/P02/P03 numbers were produced by the "
        "previous behaviour."
    )


def test_the_scanner_finds_the_same_events():
    events, _ = scanner_mod.scan(
        _prepared(make_bars()), threshold=1.5, min_duration=3, cooldown_bars=8
    )
    assert events["decision_pos"].tolist() == GOLDEN_EVENT_POSITIONS


def test_the_feature_set_is_still_the_documented_27():
    assert len(FEATURE_COLUMNS) == GOLDEN_FEATURE_COUNT
    assert len(set(FEATURE_COLUMNS)) == GOLDEN_FEATURE_COUNT, "duplicate feature names"


@pytest.mark.skipif(
    not (SOURCE_REPO / "src" / "yoyo_eth").is_dir(),
    reason="yoyo-eth is not checked out here; the golden values above still apply",
)
def test_the_port_agrees_with_the_source_repository():
    """The check the golden values cannot make on their own."""
    source_src = str(SOURCE_REPO / "src")
    added = source_src not in sys.path
    if added:
        sys.path.append(source_src)  # append, never prepend: `yoyo` must stay local
    try:
        from yoyo_eth import indicators as src_ind
        from yoyo_eth import scanner as src_scanner
        from yoyo_eth.features import FEATURE_COLUMNS as SRC_FEATURES
        from yoyo_eth.features import add_features as src_add_features

        df = make_bars()
        theirs = src_add_features(
            src_scanner.add_dispersion(src_ind.add_indicators(df)), compression_threshold=1.5
        )
        ours = _prepared(df)

        assert list(SRC_FEATURES) == list(FEATURE_COLUMNS)
        pd.testing.assert_frame_equal(
            theirs.loc[:, list(FEATURE_COLUMNS)],
            ours.loc[:, list(FEATURE_COLUMNS)],
            check_exact=False,
            rtol=TOLERANCE,
        )

        their_events, _ = src_scanner.scan(theirs, 1.5, 3, 8)
        our_events, _ = scanner_mod.scan(ours, 1.5, 3, 8)
        pd.testing.assert_frame_equal(their_events, our_events)
    finally:
        if added:
            sys.path.remove(source_src)


@pytest.mark.skipif(
    not (SOURCE_REPO / "src" / "yoyo_eth").is_dir(),
    reason="yoyo-eth is not checked out here",
)
def test_the_cross_check_did_not_shadow_the_local_yoyo_package():
    """Appending the source path must not change which yoyo is imported."""
    import yoyo

    assert Path(yoyo.__file__).resolve().is_relative_to(REPO)
