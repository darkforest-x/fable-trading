"""Validate report-only SQL on synthetic rows, without market data or replay."""
import sqlite3

import pandas as pd
import pytest

from yoyo.evaluation.owner_k1k2_transition_report import QUERIES, package


def test_package_rejects_uncontrolled_output_before_reading_files():
    with pytest.raises(ValueError, match="named, one-shot"):
        package("../other.json")


def test_fold_and_mechanism_queries_preserve_population_and_null_arming():
    rows = [
        dict(cohort="case", arm="state", fold="2023H1", closed=True,
             net_return=-.002, gross_return=0., hold_minutes=5,
             outcome="colour", transition_first_armed_at=None),
        dict(cohort="case", arm="transition", fold="2023H1", closed=True,
             net_return=-.004, gross_return=-.002, hold_minutes=10,
             outcome="stop", transition_first_armed_at=None),
        dict(cohort="control", arm="transition", fold="2023H1", closed=True,
             net_return=.003, gross_return=.005, hold_minutes=15,
             outcome="transition_colour", transition_first_armed_at="2023-01-01"),
    ]
    with sqlite3.connect(":memory:") as con:
        pd.DataFrame(rows).to_sql("trades", con, index=False)
        folds = pd.read_sql_query(QUERIES["folds"], con)
        mechanisms = pd.read_sql_query(QUERIES["mechanisms"], con)
    assert folds.trades.sum() == 3
    assert mechanisms.trades.sum() == 2
    assert set(mechanisms.arming) == {"never_armed", "armed"}
    assert mechanisms.set_index("cohort").loc["case", "net_bp"] == -40.


def test_pair_query_preserves_known_pair_denominator():
    rows = [dict(fold="2023H1", complete_pair=True, state_excess_net=-.001,
                 transition_excess_net=.002, delta_excess_net=.003)]
    with sqlite3.connect(":memory:") as con:
        pd.DataFrame(rows).to_sql("pairs", con, index=False)
        pairs = pd.read_sql_query(QUERIES["pairs"], con)
    assert pairs.pairs.sum() == pairs.known_pairs.sum() == 1
    assert pairs.delta_excess_bp.iloc[0] == 30.
