"""The empirical local-archive clock correction is seasonal, not a global shift."""
from yoyo.evaluation.monday_5m_wick_gold_clock import local_index


def test_local_gold_archive_maps_winter_and_summer_separately():
    ix=local_index(['20260105 000000','20260706 000000'])
    assert list(ix.hour)==[5,4]
    assert list(ix.tz_convert('America/New_York').hour)==[0,0]


def test_ambiguous_and_nonexistent_clock_values_stay_detectable():
    ix=local_index(['20260308 023000','20261101 013000'])
    assert ix.isna().all()
