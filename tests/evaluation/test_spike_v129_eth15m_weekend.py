"""Beijing-weekend definition of the 15m weekend-filter test."""
import pandas as pd

from yoyo.evaluation import spike_v129_eth15m_weekend as study


def test_beijing_weekend_boundaries():
    utc = pd.to_datetime(["2026-10-09T15:59:00Z",   # Fri 23:59 Beijing
                          "2026-10-09T16:00:00Z",   # Sat 00:00 Beijing
                          "2026-10-11T15:45:00Z",   # Sun 23:45 Beijing
                          "2026-10-11T16:00:00Z"])  # Mon 00:00 Beijing
    assert study.is_weekend(utc).tolist() == [False, True, True, False]


def test_frozen_scope():
    cfg = study.config()
    assert cfg["production_eligible"] is False and "Post-hoc" in cfg["hypothesis_origin"]
    assert pd.Timestamp(cfg["okx_start"]) < pd.Timestamp(cfg["okx_split"]) < pd.Timestamp(cfg["okx_end"])
    assert pd.Timestamp(cfg["binance_start"]) < pd.Timestamp(cfg["binance_split"]) < pd.Timestamp(cfg["binance_end"])
