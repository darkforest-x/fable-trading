"""Report query smoke on tiny synthetic roster identities, no market data."""
import sqlite3

import pandas as pd

from yoyo.evaluation.k1k2_genuine_flow_report import QUERIES


def test_query_denominators_include_unmatched_and_non_requests():
    tables = {
        "case_mothers": pd.DataFrame([dict(event_id="a", fold="2023H1"), dict(event_id="b", fold="2023H1")]),
        "case_statuses": pd.DataFrame([dict(event_id="a", status="request_emitted"), dict(event_id="b", status="expired_no_k2")]),
        "parents": pd.DataFrame([dict(event_id="c"+str(i),parent_event_id="a") for i in range(3)]),
        "features": pd.DataFrame([dict(cohort="case",window_kind="k1",status="complete",flow_defined=True,available_bars=12,zero_volume_bars=0)]),
    }
    with sqlite3.connect(":memory:") as con:
        for name, frame in tables.items(): frame.to_sql(name,con,index=False)
        result = {name:pd.read_sql_query(sql,con).to_dict("records") for name,sql in QUERIES.items()}
    assert sum(r["mothers"] for r in result["case_status"])==2
    assert all(r["share"]==.5 and r["total_mothers"]==2 for r in result["case_status"])
    assert result["fold_counts"][0]["requests"]==1
    assert result["control_coverage"]==[dict(fold="2023H1",mothers=2,mothers_with_three_controls=1,
        mothers_without_controls=1,requests=1,requests_with_three_original_controls=1)]
    assert result["alignment"][0]["source_bar_occurrences"]==12
