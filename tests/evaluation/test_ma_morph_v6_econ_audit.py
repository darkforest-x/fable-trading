"""Contract tests for the morphology v6 economic robustness audit."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import ma_morph_v6_econ_audit as audit


def cfg():
    return json.loads(audit.CONFIG.read_text())


def test_top_fraction_uses_ceil_and_event_id_tiebreak():
    f = pd.DataFrame({"score": [.9, .9, .5, .1], "event_id": ["b", "a", "c", "d"]})
    assert audit.top_fraction(f, .25).event_id.tolist() == ["a"]
    assert audit.top_fraction(f, .3).event_id.tolist() == ["a", "b"]


def test_block_stats_sign_flip_detects_consistent_positive_blocks():
    rng = np.random.default_rng(0)
    x = pd.Series([1.0] * 40)
    blocks = pd.Series(np.repeat(np.arange(20), 2))
    mean, lo, hi, p, n = audit.block_stats(x, blocks, rng, 500, 2000)
    assert mean == 1.0 and lo == 1.0 and n == 20 and p < 0.001
    x2 = pd.Series(np.tile([1.0, -1.0], 20))
    _, _, _, p2, _ = audit.block_stats(x2, pd.Series(np.arange(40)), rng, 500, 2000)
    assert p2 > 0.3


@pytest.mark.skipif(not Path(cfg()["manifest"]).exists(), reason="v4 bank manifest absent")
def test_load_reconciles_reported_top_decile():
    c = cfg()
    frame = audit.load(c)
    for split in c["splits"]:
        s = frame[frame.split == split]
        t = audit.top_fraction(s, c["top_fraction"])
        assert set(t.event_id) == set(s[s.top10_reported].event_id)
        assert len(t) == c["reported"][split]["n"]
        assert t.net_bp.mean() == pytest.approx(c["reported"][split]["net_bp"], abs=1e-3)
        assert s.asset.notna().all() and s.decision_at.notna().all()
