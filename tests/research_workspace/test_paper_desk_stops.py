"""Owner-entered protective prices, fresh exit quotes and atomic OCO closure."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
import json
from types import SimpleNamespace

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.research_workspace import paper_desk as book, paper_desk_limits as engine, paper_desk_api as api
from yoyo.research_workspace.api import same_origin


@pytest.fixture
def env(tmp_path, monkeypatch):
    now = [1000000]
    monkeypatch.setattr(book, "clock_ms", lambda: now[0])
    monkeypatch.setattr(engine, "clock_ms", lambda: now[0])
    desk = book.PaperDesk(tmp_path)
    account = desk.create_account("Protected QA", "1000", "account-request-0001")["id"]
    def mark(bid="100", ask=None):
        return dict(symbol="BTC-USDT-SWAP", bid=bid, ask=ask or bid, source_ms=now[0], received_ms=now[0])
    def opening(side="long", tp="110", sl="90"):
        q = desk.save_quote(mark())
        return desk.open(account, side, "2", q["id"], "", "open-request-0001", {}, 10, tp, sl)
    return SimpleNamespace(desk=desk, account=account, limits=engine.LimitOrders(desk), now=now, mark=mark, opening=opening)


@pytest.mark.parametrize("side,reason,bid,ask,price", [
    ("long", "take_profit", "110", "111", "110"),
    ("long", "take_profit", "115", "116", "115"),
    ("long", "stop_loss", "90", "91", "90"),
    ("long", "stop_loss", "85", "86", "85"),
    ("short", "take_profit", "89", "90", "90"),
    ("short", "take_profit", "84", "85", "85"),
    ("short", "stop_loss", "109", "110", "110"),
    ("short", "stop_loss", "114", "115", "115"),
])
def test_four_trigger_directions_preserve_gap_price_fees_and_once_only(env, side, reason, bid, ask, price):
    e = env
    tp, sl = ("110", "90") if side == "long" else ("90", "110")
    opened = e.opening(side, tp, sl)
    e.now[0] += 1
    e.limits.match({"BTC-USDT-SWAP": e.mark(bid, ask)})
    # Repeated ticks, restart, and the other protective leg cannot close again.
    e.limits = engine.LimitOrders(book.PaperDesk(e.desk.path.parent))
    e.now[0] += 1
    e.limits.match({"BTC-USDT-SWAP": e.mark("80" if reason == "take_profit" else "120")})
    exported = e.desk.export(e.account)
    assert len(exported["fills"]) == 2
    close = exported["fills"][1]
    assert close["exit_reason"] == reason
    assert close["quantity"] == "2" and close["price"] == price
    assert D(close["fee"]) == D(price) * 2 * book.FEE
    assert D(close["gross_pnl"]) == (D(price)-100)*2*(1 if side == "long" else -1)
    assert D(exported["account"]["cash"]) == 1000 + D(close["gross_pnl"]) - D(close["fee"]) - D(opened["fill"]["fee"])
    assert exported["positions"][0]["remaining"] == "0"
    evidence = next(q for q in exported["quotes"] if q["id"] == close["quote_id"])
    assert evidence["bid"] == bid and evidence["ask"] == ask
    assert evidence["source_ms"] > opened["position"]["opened_ms"]
    assert exported["schema_version"] == 8
    assert "automatic_stops" not in exported["exclusions"]


@pytest.mark.parametrize("side,tp,sl,bid,ask", [
    ("long", "110", None, "109", "111"),
    ("long", None, "90", "91", "92"),
    ("short", "90", None, "89", "91"),
    ("short", None, "110", "108", "109"),
    ("long", None, None, "80", "120"),
])
def test_unset_or_untouched_exit_price_does_not_close(env, side, tp, sl, bid, ask):
    e = env
    e.opening(side, tp, sl)
    e.now[0] += 1
    e.limits.match({"BTC-USDT-SWAP": e.mark(bid, ask)})
    assert len(e.desk.export(e.account)["fills"]) == 1


@pytest.mark.parametrize("side,tp,sl", [
    ("long", "100", None), ("long", "99", None), ("long", None, "100"), ("long", None, "101"),
    ("short", "100", None), ("short", "101", None), ("short", None, "100"), ("short", None, "99"),
    ("long", "NaN", None), ("long", None, "0"), ("long", "1e13", None), ("long", None, "1e-13"),
])
def test_invalid_prices_rejected_for_preview_market_and_limit_without_fills(env, side, tp, sl):
    e = env
    q = e.desk.save_quote(e.mark())
    with pytest.raises(ValueError):
        e.desk.size_all_in(e.account, side, q["id"], 10, {}, None, tp, sl)
    with pytest.raises(ValueError):
        e.opening(side, tp, sl)
    with pytest.raises(ValueError):
        e.limits.place("open", e.account, "2", "100", q["id"], "limit-request-0001", {}, side, 10, "", tp, sl)
    data = e.desk.export(e.account)
    assert not data["positions"] and not data["orders"] and not data["fills"]
    assert data["account"]["cash"] == "1000"


def test_no_pre_entry_stale_crossed_or_missing_quote_trigger(env):
    e = env
    opened = e.opening()
    e.now[0] += 20000
    mark = e.mark("80", "81")
    invalid = [None, dict(mark, source_ms=opened["position"]["opened_ms"]),
               dict(mark, received_ms=opened["position"]["opened_ms"]),
               dict(mark, source_ms=e.now[0]-15001), dict(mark, bid="90", ask="80"),
               dict(mark, symbol="ETH-USDT-SWAP")]
    for quote in invalid:
        e.limits.match({"BTC-USDT-SWAP": quote} if quote else {})
    assert len(e.desk.export(e.account)["fills"]) == 1
    e.limits.match({"BTC-USDT-SWAP": mark})
    assert e.desk.export(e.account)["fills"][-1]["exit_reason"] == "stop_loss"


@pytest.mark.parametrize("side,tp,sl,limit,trigger", [
    ("long", "105", "85", "95", "106"), ("short", "95", "115", "105", "94"),
])
def test_limit_entry_activates_protection_only_after_actual_fill(env, side, tp, sl, limit, trigger):
    e = env
    q = e.desk.save_quote(e.mark())
    args = ("open", e.account, "2", limit, q["id"], "limit-request-0001", {}, side, 10, "", tp, sl)
    submitted = e.limits.place(*args)
    e.now[0] += 1
    e.limits.match({"BTC-USDT-SWAP": e.mark(trigger)})
    assert not e.desk.export(e.account)["fills"]  # Protective price alone cannot close an unfilled entry.
    e.now[0] += 1
    e.limits.match({"BTC-USDT-SWAP": e.mark(limit)})
    data = e.desk.export(e.account)
    assert len(data["fills"]) == 1
    assert data["positions"][0]["take_profit"] == tp
    assert data["positions"][0]["stop_loss"] == sl
    assert e.limits.place(*args) == submitted
    e.now[0] += 1
    e.limits.match({"BTC-USDT-SWAP": e.mark(trigger)})
    assert e.desk.export(e.account)["fills"][-1]["exit_reason"] == "take_profit"


def test_partial_manual_close_then_protection_cancels_close_limits_and_exits_remainder(env):
    e = env
    p = e.opening()["position"]
    q = e.desk.save_quote(e.mark())
    e.desk.close(p["id"], ".5", q["id"], "close-request-0001")
    pending = e.limits.place("close", p["id"], "1.5", "110", q["id"], "close-limit-0001")["order"]
    e.now[0] += 1
    # Both the TP and the close limit touch; protection takes priority in one transaction.
    e.limits.match({"BTC-USDT-SWAP": e.mark("112", "113")})
    data = e.desk.export(e.account)
    assert len(data["fills"]) == 3
    assert data["fills"][-1]["quantity"] == "1.5"
    assert data["fills"][-1]["price"] == "112"
    assert data["fills"][-1]["exit_reason"] == "take_profit"
    assert data["orders"][0]["id"] == pending["id"]
    assert data["orders"][0]["status"] == "cancelled"
    assert "自动平仓" in data["orders"][0]["reason"]
    assert e.limits.symbols() == []


def test_manual_close_racing_multiple_workers_cannot_oversell(env):
    e = env
    p = e.opening()["position"]
    e.now[0] += 1
    mark = e.mark("80", "81")
    q = e.desk.save_quote(mark)
    def run(i):
        try:
            if i == 0:
                return e.desk.close(p["id"], "2", q["id"], "close-request-0001")
            return e.limits.match({"BTC-USDT-SWAP": mark})
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(run, range(5)))
    data = e.desk.export(e.account)
    assert len(data["fills"]) == 2
    assert data["positions"][0]["remaining"] == "0"
    assert sum(D(f["quantity"]) for f in data["fills"] if f["action"] == "close") == 2


def test_worker_polls_protection_without_any_limit_order_and_recovers_after_missing_quote(env):
    e = env
    e.opening()
    restarted = engine.LimitOrders(book.PaperDesk(e.desk.path.parent))
    calls = []
    quotes = SimpleNamespace(get=lambda symbols: (calls.append(symbols) or {}))
    worker = engine.LimitOrderWorker(restarted, quotes)
    worker.tick()
    assert calls == [["BTC-USDT-SWAP"]]
    assert worker.status()["error"]
    assert len(e.desk.export(e.account)["fills"]) == 1
    e.now[0] += 1
    quotes.get = lambda symbols: {s: e.mark("111") for s in symbols}
    worker.tick()
    assert worker.status()["error"] is None
    assert e.desk.export(e.account)["fills"][-1]["exit_reason"] == "take_profit"


def test_protective_prices_are_part_of_idempotency_and_legacy_receipts_still_replay(env):
    e = env
    result = e.opening()
    qid = result["fill"]["quote_id"]
    e.now[0] += 20000
    assert e.desk.open(e.account, "long", "2", qid, "", "open-request-0001", {}, 10, "110", "90") == result
    with pytest.raises(ValueError, match="请求标识"):
        e.desk.open(e.account, "long", "2", qid, "", "open-request-0001", {}, 10, "111", "90")


def test_v3_book_migrates_without_enabling_unrequested_stops(env):
    e = env
    opened = e.opening(tp=None, sl=None)
    q = e.desk.save_quote(e.mark())
    original = e.limits.place("close", opened["position"]["id"], "1", "110", q["id"], "close-limit-0001")
    with e.desk.connect() as db:
        for table, removed in (("positions", {"take_profit", "stop_loss"}), ("limit_orders", {"take_profit", "stop_loss"}), ("fills", {"exit_reason"})):
            cols = [r[1] for r in db.execute(f"PRAGMA table_info({table})") if r[1] not in removed]
            db.execute(f"CREATE TABLE {table}_v3 AS SELECT " + ",".join(cols) + f" FROM {table}")
            db.execute(f"DROP TABLE {table}")
            db.execute(f"ALTER TABLE {table}_v3 RENAME TO {table}")
        row = db.execute("SELECT payload FROM receipts WHERE request_id='open-request-0001'").fetchone()
        payload = json.loads(row[0]);payload.pop("take_profit");payload.pop("stop_loss")
        db.execute("UPDATE receipts SET payload=? WHERE request_id='open-request-0001'", (json.dumps(payload),))
    migrated = book.PaperDesk(e.desk.path.parent)
    data = migrated.export(e.account)
    assert data["positions"][0]["take_profit"] is None and data["positions"][0]["stop_loss"] is None
    assert data["fills"][0]["exit_reason"] is None
    assert migrated.open(e.account, "long", "2", opened["fill"]["quote_id"], "", "open-request-0001", {}, 10) == opened
    assert engine.LimitOrders(migrated).place("close", opened["position"]["id"], "1", "110", q["id"], "close-limit-0001") == original


@pytest.mark.parametrize("kind", ["open", "limit-open"])
def test_http_prices_are_validated_and_preserved_through_preview_and_commit(tmp_path, kind):
    app = FastAPI();router = APIRouter(prefix="/api/research", dependencies=[Depends(same_origin)])
    api.install(router, app, SimpleNamespace(runtime=tmp_path))
    app.state.paper_desk_quotes = SimpleNamespace(get=lambda symbols: {s: dict(symbol=s,bid="99",ask="100",source_ms=book.clock_ms(),received_ms=book.clock_ms()) for s in symbols})
    app.include_router(router)
    root = "/api/research/paper-desk";headers={"origin":"http://testserver","sec-fetch-site":"same-origin"}
    with TestClient(app) as c:
        a=c.post(root+"/accounts",json=dict(name="QA",initial_cash="1000",request_id="account-request-0001"),headers=headers).json()
        params=dict(account_id=a["id"],symbol="BTC-USDT-SWAP",side="long",leverage=10,take_profit="120",stop_loss="80")
        if kind == "limit-open": params["limit_price"]="95"
        assert c.get(root+"/open-preview",params=dict(params,stop_loss="101")).status_code == 409
        q=c.get(root+"/open-preview",params=params).json()
        assert q["take_profit"]=="120" and q["stop_loss"]=="80"
        payload=dict(quantity=q["quantity"],quote_id=q["id"],side="long",leverage=10,take_profit="120",stop_loss="80",request_id="open-request-0001")
        if kind == "limit-open": payload["limit_price"]="95"
        path=root+f'/accounts/{a["id"]}/{kind}'
        assert c.post(path,json=dict(payload,take_profit="NaN"),headers=headers).status_code==409
        assert c.post(path,json=payload).status_code==403
        result=c.post(path,json=payload,headers=headers)
        assert result.status_code==201,result.text
        entity=result.json()["order" if kind=="limit-open" else "position"]
        assert entity["take_profit"]=="120" and entity["stop_loss"]=="80"
        assert c.post(path,json=payload,headers=headers).json()==result.json()


@pytest.mark.parametrize('side,mark,tp,sl,trigger', [('long','120','130','110','109'),('short','80','70','90','91')])
def test_amend_allows_profit_lock_and_triggers_only_after_amendment(env, side, mark, tp, sl, trigger):
    e=env; p=e.opening(side, None, None)['position']; e.now[0]+=10
    q=e.desk.save_quote(e.mark(mark))
    result=e.desk.update_protection(p['id'],tp,sl,q['id'],'amend-request-0001')
    assert result['protection']['stop_loss']==sl
    stale=e.mark(trigger); stale['source_ms']-=1
    e.limits.match({'BTC-USDT-SWAP':stale})
    assert len(e.desk.export(e.account)['fills'])==1
    e.now[0]+=1
    e.limits.match({'BTC-USDT-SWAP':e.mark(trigger)})
    exported=e.desk.export(e.account)
    assert exported['fills'][-1]['exit_reason']=='stop_loss'
    assert exported['positions'][0]['remaining']=='0'
    assert exported['protection_updates'][0]['quote_id']==q['id']
    assert any(x['id']==q['id'] for x in exported['quotes'])
    # Retrying even after closure returns the original receipt, never revives protection.
    assert e.desk.update_protection(p['id'],tp,sl,q['id'],'amend-request-0001')==result
    with pytest.raises(ValueError,match='已平仓'):
        e.desk.update_protection(p['id'],None,None,None,'amend-request-0002')


def test_amend_then_clear_works_offline_without_touching_position_or_cash(env):
    e=env;p=e.opening()['position'];e.now[0]+=1
    q=e.desk.save_quote(e.mark())
    e.desk.update_protection(p['id'], '120', None, q['id'], 'amend-request-0001')
    e.now[0]+=20000
    cleared=e.desk.update_protection(p['id'],None,None,None,'amend-request-0002')
    assert cleared['protection']['take_profit'] is None and cleared['protection']['stop_loss'] is None
    e.now[0]+=1;e.limits.match({'BTC-USDT-SWAP':e.mark('80')})
    data=e.desk.export(e.account)
    assert len(data['fills'])==1 and data['positions'][0]['remaining']=='2'
    assert D(data['account']['cash'])==D('999.9')
    with pytest.raises(ValueError,match='请求标识'):
        e.desk.update_protection(p['id'],'150',None,q['id'],'amend-request-0002')


@pytest.mark.parametrize('side,tp,sl', [('long','99',None),('long',None,'100'),('short','101',None),('short',None,'100'),('long','NaN',None)])
def test_amend_rejects_invalid_or_already_crossed_prices_without_modification(env,side,tp,sl):
    e=env;p=e.opening(side,None,None)['position'];q=e.desk.save_quote(e.mark())
    with pytest.raises(ValueError):e.desk.update_protection(p['id'],tp,sl,q['id'],'amend-request-0001')
    p=e.desk.export(e.account)['positions'][0]
    assert p['take_profit'] is None and p['stop_loss'] is None


def test_amend_checks_quote_symbol_freshness_and_closed_position(env):
    e=env;p=e.opening()['position'];q=e.desk.save_quote(dict(e.mark(),symbol='ETH-USDT-SWAP'))
    with pytest.raises(ValueError,match='不匹配'):e.desk.update_protection(p['id'],'120',None,q['id'],'amend-request-0001')
    q=e.desk.save_quote(e.mark());e.now[0]+=16000
    with pytest.raises(ValueError,match='过期'):e.desk.update_protection(p['id'],'120',None,q['id'],'amend-request-0001')


def test_percentage_close_uses_remainder_and_reservations_without_silent_resize(env):
    e=env;p=e.opening()['position'];q=e.desk.save_quote(e.mark())
    sized=e.desk.size_close(p['id'],25,q['id'])
    assert D(sized['quantity'])==D('.5')
    e.desk.close(p['id'],sized['quantity'],q['id'],'close-request-0001')
    assert D(e.desk.size_close(p['id'],50,q['id'])['quantity'])==D('.75')
    order=e.limits.place('close',p['id'],'1','110',q['id'],'limit-request-0001')
    with pytest.raises(ValueError,match='平仓挂单'):e.desk.size_close(p['id'],100,q['id'])
    assert D(e.desk.size_close(p['id'],25,q['id'])['quantity'])==D('.375')
    e.desk.update_protection(p['id'],'105','95',q['id'],'amend-request-0001')
    e.now[0]+=1;e.limits.match({'BTC-USDT-SWAP':e.mark('106')})
    data=e.desk.export(e.account)
    assert D(data['fills'][-1]['quantity'])==D('1.5')
    assert data['fills'][-1]['exit_reason']=='take_profit'
    assert data['orders'][0]['status']=='cancelled'
    assert sum(D(f['quantity']) for f in data['fills'] if f['action']=='close')==2


@pytest.mark.parametrize('percentage',[0,101,25.5,True,'50'])
def test_percentage_close_invalid_values(env,percentage):
    e=env;p=e.opening()['position'];q=e.desk.save_quote(e.mark())
    with pytest.raises(ValueError):e.desk.size_close(p['id'],percentage,q['id'])


def test_percentage_full_close_preserves_precision_and_confirmation_rechecks(env):
    e=env;q=e.desk.save_quote(e.mark())
    p=e.desk.open(e.account,'long','0.123456789012345',q['id'],'','open-request-0001',{},10)['position']
    full=e.desk.size_close(p['id'],100,q['id'])
    assert full['quantity']=='0.123456789012345'
    assert e.desk.size_close(p['id'],25,q['id'])['quantity']=='0.030864197253'
    e.desk.close(p['id'],'0.01',q['id'],'close-request-0001')
    with pytest.raises(ValueError,match='超过'):e.desk.close(p['id'],full['quantity'],q['id'],'close-request-0002')


def test_amend_racing_close_never_reopens_or_oversells(env):
    e=env;p=e.opening(tp=None,sl=None)['position'];q=e.desk.save_quote(e.mark())
    def run(i):
        try:
            if i==0:return e.desk.close(p['id'],'2',q['id'],'close-request-0001')
            return e.desk.update_protection(p['id'],'110','90',q['id'],f'amend-request-000{i}')
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(run,range(3)))
    e.now[0]+=1;e.limits.match({'BTC-USDT-SWAP':e.mark('111')})
    data=e.desk.export(e.account)
    assert len(data['fills'])==2 and data['positions'][0]['remaining']=='0'


def test_position_management_http_origin_validation_and_idempotency(tmp_path):
    app=FastAPI();router=APIRouter(prefix='/api/research',dependencies=[Depends(same_origin)])
    api.install(router,app,SimpleNamespace(runtime=tmp_path));app.include_router(router)
    desk=app.state.paper_desk;a=desk.create_account('QA','1000','account-request-0001')
    mark=lambda:dict(symbol='BTC-USDT-SWAP',bid='99',ask='100',source_ms=book.clock_ms(),received_ms=book.clock_ms())
    app.state.paper_desk_quotes=SimpleNamespace(get=lambda symbols:{s:dict(mark(),symbol=s) for s in symbols})
    q=desk.save_quote(mark());p=desk.open(a['id'],'long','2',q['id'],'','open-request-0001',{},10)['position']
    base=f'/api/research/paper-desk/positions/{p["id"]}';headers={'origin':'http://testserver','sec-fetch-site':'same-origin'}
    with TestClient(app) as c:
        preview=c.get(base+'/close-preview',params={'percentage':25}).json()
        assert D(preview['quantity'])==D('.5') and preview['close_percent']==25
        assert c.get(base+'/close-preview',params={'percentage':101}).status_code==422
        payload=dict(take_profit='110',stop_loss='90',quote_id=preview['id'],request_id='amend-request-0001')
        assert c.post(base+'/protection',json=payload).status_code==403
        result=c.post(base+'/protection',json=payload,headers=headers)
        assert result.status_code==200 and result.json()['protection']['take_profit']=='110'
        assert c.post(base+'/protection',json=payload,headers=headers).json()==result.json()

@pytest.mark.parametrize('side,tp,sl,current', [('long','130','90','120'),('short','70','110','80')])
def test_stop_to_entry_preserves_tp_replays_and_triggers_at_entry(env, side, tp, sl, current):
    e=env;p=e.opening(side,tp,sl)['position'];e.now[0]+=1
    q=e.desk.save_quote(e.mark(current))
    result=e.desk.update_protection(p['id'],None,None,q['id'],'entry-stop-request-001',stop_to_entry=True)
    assert result['protection']['stop_loss']=='100'
    assert result['protection']['take_profit']==tp
    assert result['stop_to_entry'] and not result['unchanged']
    exported=e.desk.export(e.account)
    assert exported['protection_updates'][-1]['stop_loss']=='100'
    assert exported['protection_updates'][-1]['take_profit']==tp
    e.now[0]+=1;e.limits.match({'BTC-USDT-SWAP':e.mark('100')})
    exported=e.desk.export(e.account)
    assert exported['fills'][-1]['exit_reason']=='stop_loss'
    assert exported['fills'][-1]['price']=='100'
    assert D(exported['fills'][-1]['gross_pnl'])==0
    assert D(exported['fills'][-1]['fee'])>0
    assert e.desk.update_protection(p['id'],None,None,q['id'],'entry-stop-request-001',stop_to_entry=True)==result

@pytest.mark.parametrize('side,better,current', [('long','105','120'),('short','95','80')])
def test_entry_shortcut_never_loosens_existing_profitable_stop(env,side,better,current):
    e=env;p=e.opening(side,None,None)['position'];e.now[0]+=1;q=e.desk.save_quote(e.mark(current))
    e.desk.update_protection(p['id'],None,better,q['id'],'manual-stop-request-01')
    result=e.desk.update_protection(p['id'],None,None,q['id'],'entry-stop-request-001',stop_to_entry=True)
    assert result['unchanged'] and result['protection']['stop_loss']==better

@pytest.mark.parametrize('side,current', [('long','100'),('long','90'),('short','100'),('short','110')])
def test_entry_shortcut_rejects_immediately_triggering_or_stale_quotes(env,side,current):
    e=env;p=e.opening(side,None,None)['position'];q=e.desk.save_quote(e.mark(current))
    with pytest.raises(ValueError,match='当前可平仓价'):
        e.desk.update_protection(p['id'],None,None,q['id'],'entry-stop-request-001',stop_to_entry=True)
    e.now[0]+=20000
    with pytest.raises(ValueError,match='过期'):
        e.desk.update_protection(p['id'],None,None,q['id'],'entry-stop-request-002',stop_to_entry=True)
    assert e.desk.export(e.account)['positions'][0]['stop_loss'] is None

def test_entry_shortcut_uses_current_merged_entry_not_stale_frontend_price(env):
    e=env;p=e.opening('long',None,'90')['position'];e.now[0]+=1
    add=e.desk.save_quote(e.mark('110'))
    merged=e.desk.open(e.account,'long','2',add['id'],'','add-request-0001',{'BTC-USDT-SWAP':e.mark('110')},10)['position']
    assert D(merged['entry_price'])==D('105')
    e.now[0]+=1;q=e.desk.save_quote(e.mark('120'))
    result=e.desk.update_protection(p['id'],None,None,q['id'],'entry-stop-request-001',stop_to_entry=True)
    assert D(result['protection']['stop_loss'])==D('105')
    with pytest.raises(ValueError,match='不能同时指定'):
        e.desk.update_protection(p['id'],None,'100',q['id'],'entry-stop-request-002',stop_to_entry=True)

def test_entry_shortcut_http_intent_origin_and_replay(tmp_path):
    app=FastAPI();router=APIRouter(prefix='/api/research',dependencies=[Depends(same_origin)])
    api.install(router,app,SimpleNamespace(runtime=tmp_path));app.include_router(router)
    desk=app.state.paper_desk;a=desk.create_account('QA','1000','account-request-0001')
    mark=lambda value:dict(symbol='BTC-USDT-SWAP',bid=value,ask=value,source_ms=book.clock_ms(),received_ms=book.clock_ms())
    q=desk.save_quote(mark('100'));p=desk.open(a['id'],'long','2',q['id'],'','open-request-0001',{},10,'130','90')['position']
    q=desk.save_quote(mark('120'));base=f'/api/research/paper-desk/positions/{p["id"]}/protection'
    payload=dict(stop_to_entry=True,quote_id=q['id'],request_id='entry-stop-request-001')
    with TestClient(app) as c:
        assert c.post(base,json=payload).status_code==403
        headers={'origin':'http://testserver','sec-fetch-site':'same-origin'}
        result=c.post(base,json=payload,headers=headers)
        assert result.status_code==200
        assert result.json()['protection']['stop_loss']=='100'
        assert result.json()['protection']['take_profit']=='130'
        assert c.post(base,json=payload,headers=headers).json()==result.json()
        assert c.post(base,json=dict(payload,stop_to_entry='yes'),headers=headers).status_code==422


def test_stop_only_amend_preserves_current_tp_atomically_and_binds_retry(env):
    e=env;p=e.opening()['position'];q=e.desk.save_quote(e.mark())
    e.desk.update_protection(p['id'],'120','90',q['id'],'other-tab-amend-0001')
    result=e.desk.update_protection(p['id'],None,'95',q['id'],'stop-only-amend-0001',preserve_take_profit=True)
    assert result['protection']['take_profit']=='120'
    assert result['protection']['stop_loss']=='95'
    assert e.desk.update_protection(p['id'],None,'95',q['id'],'stop-only-amend-0001',preserve_take_profit=True)==result
    with pytest.raises(ValueError):e.desk.update_protection(p['id'],None,'95',q['id'],'stop-only-amend-0001')
