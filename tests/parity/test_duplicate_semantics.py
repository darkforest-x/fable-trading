"""One semantic, several implementations: which agree, and where they do not.

The consolidation task book asks for a single canonical implementation per
semantic. Getting there by rewriting everything at once is not available during
a task whose first rule is to change nothing that runs, and for two of these the
duplication is deliberate. So this file does the part that is both safe and
load-bearing: it states which implementations must agree and proves it on every
run, and it pins the one place they demonstrably do not, with its magnitude.

A divergence that is measured and pinned is a known quantity. The same
divergence undocumented is a number that changes when someone tidies an import.

Inventory and decisions: docs/consolidation/DUPLICATE_SEMANTICS.md
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def bars() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    n = 300
    ts = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + np.abs(rng.normal(0, 0.3, n))
    low = close - np.abs(rng.normal(0, 0.3, n))
    open_ = close + rng.normal(0, 0.2, n)
    high = np.maximum.reduce([high, open_, close])
    low = np.minimum.reduce([low, open_, close])
    return pd.DataFrame(
        {
            "open_time": ts, "timestamp": ts, "open": open_, "high": high,
            "low": low, "close": close, "volume": rng.uniform(1, 100, n),
        }
    )


# -- SHA-256: seven implementations, one answer required -------------------

def test_every_file_hash_helper_returns_the_same_digest():
    """Seven copies of one function. They agree; this keeps it that way.

    Chunk sizes and return types differ between them, which is exactly the sort
    of difference that stays invisible until one of them is edited.
    """
    from yoyo.artifacts.lineage import digest_file
    from yoyo.contracts.protocol import file_sha256 as protocol_hash
    from yoyo.datasets.gold_render import sha256_file as gold_render_hash
    from yoyo.datasets.legacy_gold_migration.io import sha256_file as legacy_hash
    from yoyo.layers.l1_detection.onset.common.hashing import file_sha256 as onset_hash
    from yoyo.layers.l2_judgment.frozen import file_sha256 as frozen_hash

    with tempfile.TemporaryDirectory() as tmp:
        sample = Path(tmp) / "sample.bin"
        sample.write_bytes(os.urandom(3_000_000))  # larger than any chunk size in use
        digests = {
            "yoyo.artifacts.lineage.digest_file": digest_file(sample).sha256,
            "yoyo.contracts.protocol.file_sha256": protocol_hash(sample),
            "yoyo.datasets.gold_render.sha256_file": gold_render_hash(sample),
            "yoyo.datasets.legacy_gold_migration.io.sha256_file": legacy_hash(sample),
            "yoyo.layers.l1_detection.onset.common.hashing.file_sha256": onset_hash(sample),
            "yoyo.layers.l2_judgment.frozen.file_sha256": frozen_hash(sample),
        }
    assert len(set(digests.values())) == 1, f"file hash helpers disagree: {digests}"


# -- MA / EMA: three implementations, identical values ---------------------

@pytest.mark.parametrize(
    ("baseline_col", "detection_col"),
    [("sma_20", "sma20"), ("ema_20", "ema20"), ("sma_60", "sma60"),
     ("ema_60", "ema60"), ("sma_120", "sma120"), ("ema_120", "ema120")],
)
def test_the_numeric_baseline_and_the_detector_compute_the_same_moving_averages(
    bars, baseline_col, detection_col
):
    """Identical to the last bit, under two naming conventions.

    yoyo/layers/l1_detection/data.py feeds the renderer whose exact pixels the
    detector is bound to; the numeric baseline is yoyo-eth's byte-identical
    research code. They were written independently and agree exactly -- the
    underscore in the column name is the entire difference, and it is why
    nobody noticed they were the same function twice.
    """
    from yoyo.layers.l1_detection import data as detection
    from yoyo.layers.l1_detection.numeric_baseline import indicators as baseline

    left = baseline.add_indicators(bars.copy())[baseline_col]
    right = detection.add_mas(bars.copy())[detection_col]
    difference = (left - right).abs().max()
    assert difference == 0.0, f"{baseline_col} vs {detection_col} differ by {difference}"


# -- ATR: two implementations, a warmup divergence that decays -------------

#: Measured, not assumed. See docs/consolidation/DUPLICATE_SEMANTICS.md.
ATR_WARMUP_DIVERGENCE_AT_BAR_14 = 0.109
ATR_DIVERGENCE_AFTER_BAR_100 = 2e-4


def test_the_two_atrs_diverge_only_in_warmup_and_the_gap_decays(bars):
    """They disagree, the disagreement is a seeding choice, and it washes out.

    yoyo/layers/l1_detection/numeric_baseline sets the first TR to NaN -- bar 0
    has no previous close -- and masks the first 14 bars, because 14 TRs are
    needed before an ATR14 means anything. yoyo/data/indicators.py seeds its
    EWM on bar 0's high-low and emits a value from the first bar.

    This matters more than a warmup detail usually would: ATR sets the TP/SL
    barrier distances (-5 / +2 ATR), so an ATR that is wrong early is a barrier
    that is wrong early. It is pinned rather than fixed because changing either
    one moves every published number that used it; which one is correct is an
    owner decision, recorded in docs/consolidation/DUPLICATE_SEMANTICS.md.
    """
    from yoyo.data import indicators as data_indicators
    from yoyo.layers.l1_detection.numeric_baseline import indicators as baseline

    strict = baseline.add_indicators(bars.copy())["atr_14"]
    lenient = data_indicators.add_indicators(bars.copy())["atr14"]
    gap = (strict - lenient).abs()

    # the strict one refuses to answer before it has 14 true ranges
    assert strict.iloc[:14].isna().all()
    assert lenient.iloc[:14].notna().all()

    assert gap.iloc[14] == pytest.approx(ATR_WARMUP_DIVERGENCE_AT_BAR_14, abs=1e-3)
    assert gap.iloc[100:].max() < ATR_DIVERGENCE_AFTER_BAR_100
    assert gap.iloc[200:].max() < 1e-6, "the divergence should be exhausted by bar 200"


# -- already canonical: assert there is still only one --------------------

def test_barrier_outcomes_have_exactly_one_implementation():
    """The exit simulator is the one semantic that was already unified."""
    from yoyo.contracts import outcomes

    assert hasattr(outcomes, "resolve_barrier_outcome")
    assert outcomes.SAME_BAR_POLICIES == ("conservative_sl",), (
        "the same-bar TP/SL tie-break is an owner decision; a second policy "
        "appearing here means two answers to one question"
    )


def test_the_holdout_boundary_has_exactly_one_canonical_definition():
    from yoyo.contracts.holdout import HOLDOUT_START, HOLDOUT_START_ISO

    assert HOLDOUT_START.isoformat() == HOLDOUT_START_ISO
