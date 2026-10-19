"""Single-channel scope, audit evidence and causal paper-fill regressions."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from yoyo.copier.store.sqlite import Database
from yoyo.copier.discord_channels import apply_exact_watchlist, register_web_channel
from yoyo.copier.monitor_switches import channel_enabled, save_switches
from yoyo.copier.paper.book import PaperBook, iso_ms
from yoyo.copier.api.routes.paper import summary

GUILD = '1004707886657699901'
CH = '1131521990814089276'
OTHER = '1131522121873489961'

@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / 'copier.db')


def test_unknown_r_does_not_hide_known_net_profit(db):
    from yoyo.copier.api.routes.paper import r_stats
    row=dict(id=1,closed_at='2026-10-15',message_id=None,realized_pnl=12.,fees=2.,
             initial_sl=None,entry_px=100.,qty=1.,initial_risk_usdt=None)
    result=r_stats(PaperBook(db),[row],1000)
    assert result['net_win_rate']==1 and result['net_wins']==1
    assert result['unknown_r']==1 and result['sum_r'] is None


def test_exact_scope_cannot_expand_from_a_matching_name_or_toggle(db):
    apply_exact_watchlist(db, GUILD, CH, 'Arthur行情分析')
    assert not register_web_channel(db, OTHER, GUILD, 'Arthur行情分析')
    save_switches(db, channels={OTHER: True})
    assert channel_enabled(db, CH) and not channel_enabled(db, OTHER)


def test_foreign_guild_and_channel_never_reach_parser(db, monkeypatch):
    from yoyo.copier.api.app import create_app
    apply_exact_watchlist(db, GUILD, CH, 'Arthur行情分析')
    monkeypatch.setattr('yoyo.copier.api.routes.messages.ActionRouter', lambda *_: pytest.fail('parser reached'))
    c = TestClient(create_app(db))
    for guild,channel in [(GUILD,OTHER),('999999999999999999',CH)]:
        r = c.post('/api/messages/ingest', json=dict(guild_id=guild,channel_id=channel,source='discord-web',external_id='wrong',content='BTC long'))
        assert r.json()['skipped'] and not db.message_exists('wrong')


def test_revision_is_audited_once_and_never_reexecuted(db, monkeypatch):
    from yoyo.copier.api.app import create_app
    apply_exact_watchlist(db, GUILD, CH, 'Arthur行情分析')
    mid=db.insert_message('source-1', CH, 'Arthur', 'BTC long 100 SL 95')
    monkeypatch.setattr('yoyo.copier.api.routes.messages.ActionRouter', lambda *_: pytest.fail('edit traded'))
    c=TestClient(create_app(db))
    body=dict(guild_id=GUILD,channel_id=CH,source='discord-web',external_id='source-1',content='BTC long 100 SL 97')
    for _ in range(2): assert c.post('/api/messages/ingest',json=body).json()['requires_review']
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE event='message_revision'").fetchone()[0]==1
    assert db.get_message(mid)['content']=='BTC long 100 SL 95'


def place(book, side='long', account=CH, tp=110):
    return book.place(account=account,inst_id='BTC-USDT-SWAP',side=side,ord_type='limit',px=100,qty=2,
                      leverage=5,sl=95 if side=='long' else 105,tp=tp,message_id=None)


def test_limit_filled_in_closed_bar_checks_that_bars_stop(db, monkeypatch):
    monkeypatch.setattr('yoyo.copier.paper.book.now_iso',lambda:'2026-10-14T00:00:00Z')
    book=PaperBook(db);place(book)
    events=book.process_bar('BTC-USDT-SWAP',iso_ms('2026-10-14T00:01:00Z'),102,112,94,101)
    assert [e['kind'] for e in events]==['fill','stop_loss']
    [p]=book.closed_positions()
    assert p['closed_at']=='2026-10-14T00:01:59Z'
    assert book.r_multiple(p)==pytest.approx(-1.039)


@pytest.mark.parametrize('side,o,h,l,c,tp', [('long',102,112,99,103,110),('short',98,101,89,97,90)])
def test_intrabar_limit_does_not_claim_a_target_that_may_precede_entry(db,monkeypatch,side,o,h,l,c,tp):
    monkeypatch.setattr('yoyo.copier.paper.book.now_iso',lambda:'2026-10-14T00:00:00Z')
    book=PaperBook(db);place(book,side=side,tp=tp)
    e=book.process_bar('BTC-USDT-SWAP',iso_ms('2026-10-14T00:01:00Z'),o,h,l,c)
    assert [x['kind'] for x in e]==['fill']
    assert len(book.open_positions())==1


def test_archived_paper_exposure_does_not_keep_matching(db):
    apply_exact_watchlist(db,GUILD,CH,'Arthur行情分析')
    book=PaperBook(db);place(book,account=OTHER)
    assert book.active_inst_ids()==[]
    assert book.process_mark('BTC-USDT-SWAP',90)==[]
    assert len(book.pending_orders(OTHER))==1


def test_partial_exit_and_add_preserve_committed_initial_risk(db):
    book=PaperBook(db);o=place(book);book.fill(o['id'],100)
    p=book.open_positions()[0];book.close(p['id'],50,110,'partial_close')
    o=book.place(account=CH,inst_id='BTC-USDT-SWAP',side='long',ord_type='limit',px=110,qty=2,leverage=5,sl=100,tp=130,message_id=None)
    book.fill(o['id'],110);book.close(p['id'],100,120,'manual_close')
    [p]=book.closed_positions()
    assert p['initial_risk_usdt']==30
    assert book.r_multiple(p)==pytest.approx((50-p['fees'])/30)


def test_summary_scopes_before_aggregation_and_does_not_truncate_at_500(db):
    book=PaperBook(db)
    with db._connect() as conn:
        for i in range(502):
            conn.execute("INSERT INTO paper_positions(account,inst_id,side,qty,open_qty,entry_px,leverage,initial_sl,sl,tp,status,realized_pnl,fees,opened_at,closed_at,initial_risk_usdt) VALUES(?, 'BTC-USDT-SWAP','long',1,0,100,5,95,95,110,'closed',?,0,'2026-01-01T00:00:00Z','2026-01-02T00:00:00Z',5)", (CH if i<501 else OTHER,5 if i<501 else -5))
    value=summary(db=db,_='',channel_id=CH)
    assert value['r_stats_all']['trades']==501 and value['r_stats_all']['sum_r']==501
    assert len(value['closed'])==100 and all(a['channel_id']==CH for a in value['accounts'])


def test_parser_context_uses_message_time_not_backfill_receipt_order(db):
    from yoyo.copier.router.action_router import ActionRouter
    db.insert_message('discord_web_'+CH+'_1559975318989832264',CH,'Arthur','FUTURE SIGNAL')
    db.insert_message('discord_web_'+CH+'_1519920432369897522',CH,'Arthur','PAST SIGNAL')
    mid=db.insert_message('discord_web_'+CH+'_1520468759540666564',CH,'Arthur','current')
    text=ActionRouter(db)._recent_channel_context(mid,CH)
    assert 'FUTURE' not in text and 'PAST' in text
