"""Beijing 12:00-16:00 boundary and arm definitions of the 15m hours filter."""
import pandas as pd

from yoyo.evaluation import spike_v129_eth15m_hours as study


def test_hour_block_boundaries():
    utc = pd.to_datetime(["2026-10-08T03:45:00Z",   # Thu 11:45 Beijing
                          "2026-10-08T04:00:00Z",   # Thu 12:00 Beijing
                          "2026-10-08T07:45:00Z",   # Thu 15:45 Beijing
                          "2026-10-08T08:00:00Z"])  # Thu 16:00 Beijing
    assert study.blocked_hours(utc).tolist() == [False, True, True, False]
    assert study.ARMS["no_weekend_1216"](utc).tolist() == [False, True, True, False]
    sat = pd.to_datetime(["2026-10-10T02:00:00Z"])  # Sat 10:00 Beijing
    assert study.ARMS["no_weekend"](sat).tolist() == [True] and study.ARMS["baseline"](sat).tolist() == [False]


def test_frozen_scope():
    cfg = study.config()
    assert cfg["production_eligible"] is False and "Post-hoc" in cfg["hypothesis_origin"]
