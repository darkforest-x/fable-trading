"""History reads reuse ledger projections while retaining corrections and gaps."""
from contextlib import contextmanager
import sqlite3
from yoyo.research_workspace.paper_desk import PaperDesk
from yoyo.research_workspace.paper_desk_analytics import history
from yoyo.research_workspace.paper_history_cache import Equity


def test_quotes_and_month_switch_do_not_rescan_ledger_and_equity_appends_only(tmp_path, monkeypatch):
    desk=PaperDesk(tmp_path);account=desk.create_account('Cache','1000','cache-account-request')['id']
    with desk.connect() as db:
        db.execute('INSERT INTO equity_samples VALUES(?,?,?,?,?)',(account,60000,60000,'1000','1000'))
    history(desk,account)
    statements=[];original=desk.connect
    @contextmanager
    def traced():
        with original() as db:
            db.set_trace_callback(statements.append);yield db
    monkeypatch.setattr(desk,'connect',traced)
    with original() as db:
        db.execute('INSERT INTO quotes(id,symbol,bid,ask,source_ms,received_ms) VALUES(?,?,?,?,?,?)',('q','BTC','1','2',1,1))
    history(desk,account,'2020-01')
    assert not any('FROM fills ' in s or 'FROM positions ' in s or 'FROM equity_samples ' in s for s in statements)
    statements.clear()
    # Separate connection models the still-running background writer.
    with sqlite3.connect(desk.path) as db:
        db.execute('INSERT INTO equity_samples VALUES(?,?,?,?,?)',(account,120000,120000,None,'1000'))
    data=history(desk,account)
    assert data['equity_curve'][-1]['equity'] is None
    assert any('bucket_ms>60000' in s for s in statements)
    assert not any('FROM fills ' in s or 'FROM positions ' in s for s in statements)


def test_updates_deletes_backfills_and_account_changes_invalidate(tmp_path):
    desk=PaperDesk(tmp_path);a=desk.create_account('Cache','1000','cache-account-request')['id']
    with desk.connect() as db:
        db.execute('INSERT INTO equity_samples VALUES(?,?,?,?,?)',(a,120000,120000,'1000','1000'))
    history(desk,a)
    with sqlite3.connect(desk.path) as db:
        db.execute('INSERT INTO equity_samples VALUES(?,?,?,?,?)',(a,60000,60000,None,'1000'))
        db.execute('UPDATE equity_samples SET equity=? WHERE account_id=? AND bucket_ms=?',('900',a,120000))
    data=history(desk,a);assert [p['equity'] for p in data['equity_curve']]==[None,'900']
    with sqlite3.connect(desk.path) as db:
        db.execute('DELETE FROM equity_samples WHERE account_id=?',(a,))
        db.execute('UPDATE accounts SET initial_cash=? WHERE id=?',('2000',a))
    data=history(desk,a);assert data['equity_curve']==[] and data['sampled_since'] is None
    assert data['balance_curve'][0]['balance']=='2000'


def test_equity_projection_stays_bounded_and_retains_extrema_and_missing_points():
    samples=Equity()
    for i in range(50000):
        value=None if i==2 else '-300' if i==30 else '9999' if i==9000 else str(1000+i%17)
        samples.append(dict(bucket_ms=i*60000,observed_ms=i*60000,equity=value,balance='1000'))
    rows=samples.rows()
    assert len(rows)<=1200
    assert rows[0]['observed_ms']==0 and rows[-1]['observed_ms']==49999*60000
    assert {None,'-300','9999'} <= {r['equity'] for r in rows}
