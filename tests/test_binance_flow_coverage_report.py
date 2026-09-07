"""Synthetic SQLite/report contract smoke; no market data read."""
import sqlite3

from yoyo.evaluation.binance_flow_coverage_report import QUERIES, TITLE


def test_sql_queries_count_zero_and_nonzero_denominators_separately():
    with sqlite3.connect(':memory:') as con:
        con.execute('CREATE TABLE bars (open_time TEXT, month TEXT, volume REAL, quote_volume REAL, trade_count INTEGER, delta_quote_volume REAL, price_direction INTEGER, flow_direction INTEGER)')
        con.execute('CREATE TABLE calendar (month TEXT, expected_bars INTEGER)')
        con.execute("INSERT INTO calendar VALUES ('2024-01',3)")
        con.executemany('INSERT INTO bars VALUES (?,?,?,?,?,?,?,?)',[
            ('2024-01-01T00:00Z','2024-01',10,1000,7,400,-1,1),
            ('2024-01-01T00:05Z','2024-01',0,0,0,0,0,0),
            ('2024-01-01T00:10Z','2024-01',10,1000,7,-400,-1,-1)])
        assert con.execute(QUERIES['monthly']).fetchone()==('2024-01',3,3,0,1,2,1,.5)
        assert con.execute(QUERIES['overview']).fetchone()==(3,3,1,1,2,1,.5)
        assert len(con.execute(QUERIES['zero_bars']).fetchall())==1
    assert TITLE=='Genuine Flow Coverage · V34'
