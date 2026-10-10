"""Frozen scope of the V12.9 OKX ETH 15m replay and its 15m exit semantics."""
import re

import pandas as pd

from yoyo.evaluation import spike_v129_eth15m as study
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec


def test_15m_keeps_four_atr_trail_and_h1_gate():
    source = study.five.PINE_V129.read_text()
    assert re.search(r"^float trailAtr = timeframe.isminutes and timeframe.multiplier == 60 \? 2.0 : 4.0$", source, re.M)
    assert "useH1Sma60 = input.bool(true" in source
    assert ExecutionSpec().trail_atr == 4.0


def test_frozen_scope():
    cfg = study.config()
    assert cfg["timeframe_min"] == 15 and cfg["venue"] == "okx" and cfg["production_eligible"] is False
    assert pd.Timestamp(cfg["start"]) < pd.Timestamp(cfg["split"]) < pd.Timestamp(cfg["end"])
