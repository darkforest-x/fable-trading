"""V29 report SQL diagnostics against full synthetic context/trace schemas.

No market data or labels. Cover both gate axes and preserve warmup/missing-hour
unknowns. Contradictory duplicate hour feature names expose join ambiguity and
ensure the query uses the frozen request context rather than a new calculation.
"""
import importlib.util
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

PATH = Path(__file__).resolve().parents[1] / 'experiments/active/exp-btcusdtp-1h-classifier-support-preholdout-20260907-v29/build_report.py'
SPEC = importlib.util.spec_from_file_location('classifier_report', PATH)
REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REPORT)


@pytest.mark.parametrize('direction', [1, -1])
def test_full_trace_schema_and_mutually_exclusive_rejection_reasons(direction):
    rows, hours = [], []
    # Event order: accepted, distance-only failure, slope-only failure,
    # both fail, warmup unknown, exact-hour missing unknown. The short
    # passing slope is exactly flat to test the source's asymmetric branch.
    for i, (slope, distance, known) in enumerate([
            (True, True, True), (True, False, True),
            (False, True, True), (False, False, True),
            (False, False, False), (False, False, False)]):
        clock = f'2024-01-01 {i:02d}:00:00+00:00'
        previous = (99 if slope else 101) if direction == 1 else (100 if slope else 99)
        # Strict band boundaries must fail the distance condition.
        close = 100 + direction * (3 if distance else 2)
        rows.append(dict(event_id=str(i), signal_time=clock.replace(' ', 'T'), direction=direction,
            classifier_known=known, classifier_center=100 if known else None,
            classifier_previous_center=previous if known else None, classifier_step=2 if known else None))
        if i < 5:
            hours.append(dict(open_time=clock, close=close, classifier_center=-999,
                classifier_previous_center=999, classifier_step=999, classifier_known=True))
    with sqlite3.connect(':memory:') as con:
        pd.DataFrame(rows).to_sql('cases', con, index=False)
        pd.DataFrame(rows).to_sql('controls', con, index=False)
        pd.DataFrame(hours).to_sql('hours', con, index=False)
        actual = pd.read_sql_query(REPORT.QUERIES['failures'], con).to_dict('records')
    for population in ('case', 'control'):
        reasons = {r['reason']: r['events'] for r in actual if r['population'] == population}
        assert reasons == dict(accepted=1, distance_only=1, slope_only=1, slope_and_distance=1, unknown=2)
        assert sum(reasons.values()) == len(rows)

