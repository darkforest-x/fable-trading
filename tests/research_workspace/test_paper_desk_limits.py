"""All-available sizing, durable reservations and post-submission limit fills."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from types import SimpleNamespace
import threading

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from yoyo.research_workspace import paper_desk as book, paper_desk_limits as limit_module
from yoyo.research_workspace import paper_desk_api as api
from yoyo.research_workspace.api import same_origin

D = Decimal
HEADERS = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    now = [1000000]
    monkeypatch.setattr(book, "clock_ms", lambda: now[0])
    monkeypatch.setattr(limit_module, "clock_ms", lambda: now[0])
    desk = book.PaperDesk(tmp_path)
    account = desk.create_account("Practice", "1000", "account-request-0001")["id"]
    def mark(bid="100", ask=None, symbol="BTC-USDT-SWAP"):
        return dict(symbol=symbol, bid=bid, ask=ask or bid, source_ms=now[0], received_ms=now[0])
    def quote(*args, **kwargs):
        return desk.save_quote(mark(*args, **kwargs))
    return SimpleNamespace(desk=desk, account=account, limits=limit_module.LimitOrders(desk), now=now, mark=mark, quote=quote)


def place(e, side="long", qty="1", px="100", key="limit-request-0001", symbol="BTC-USDT-SWAP", leverage=1, marks=None):
    q = e.quote("99", "101", symbol=symbol)
    return e.limits.place("open", e.account, qty, px, q["id"], key, marks or {}, side, leverage)["order"]


@pytest.mark.parametrize("leverage", book.ENTRY_LEVERAGES)
@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("limit", [None, "123.456789"])
def test_all_in_reserves_fees_and_rounds_down_without_overspending(env, leverage, side, limit):
    e = env
    q = e.quote("99", "101")
    preview = e.desk.size_all_in(e.account, side, q["id"], leverage, {}, limit)
    price = D(preview["execution_price"])
    qty = D(preview["quantity"])
    rate = D(preview["fee_rate"])
    cost = qty * price / leverage + qty * price * rate
    assert cost <= 1000
    assert 1000 - cost <= D("1e-12") * price * (D(1)/leverage + rate)
    assert e.desk.export(e.account)["fills"] == []
    if limit:
        order = e.limits.place("open", e.account, preview["quantity"], limit, q["id"], "limit-request-0001", {}, side, leverage)
        snap = e.desk.snapshot({})["accounts"][0]
        if rate == book.MAKER_FEE:
            assert D(snap["order_reserved"]) == cost
            assert D(snap["balance"]) == 1000
            e.limits.cancel(order["order"]["id"], "cancel-request-0001")
            assert D(e.desk.snapshot({})["accounts"][0]["available"]) == 1000
        else:
            assert order['order']['status'] == 'filled'
            assert D(snap['balance']) == 1000 - D(preview['fee'])
    else:
        result = e.desk.open(e.account, side, preview["quantity"], q["id"], "", "open-request-0001", {}, leverage)
        assert D(result["position"]["quantity"]) == qty
        assert D(e.desk.export(e.account)["account"]["cash"]) == 1000 - D(preview["fee"])


@pytest.mark.parametrize("leverage", [0, 3, 25, 125, True])
def test_all_in_rejects_unlisted_leverage(env, leverage):
    with pytest.raises(ValueError, match="档位"):
        env.desk.size_all_in(env.account, "long", env.quote()["id"], leverage, {})


def test_all_in_respects_existing_reservations_and_revalidates_at_confirmation(env):
    e = env
    q = e.quote()
    preview = e.desk.size_all_in(e.account, "long", q["id"], 10, {})
    other = place(e, qty="2", symbol="ETH-USDT-SWAP")
    with pytest.raises(ValueError, match="资金不足"):
        e.desk.open(e.account, "long", preview["quantity"], q["id"], "", "open-request-0001", {}, 10)
    resized = e.desk.size_all_in(e.account, "long", q["id"], 10, {})
    assert D(resized["budget"]) == D("799.96")
    e.limits.cancel(other["id"], "cancel-request-0001")
    assert not e.desk.export(e.account)["fills"]


@pytest.mark.parametrize("action,side", [("open", "long"), ("open", "short"), ("close", "long"), ("close", "short")])
def test_touch_uses_correct_book_side_and_conservative_limit_price(env, action, side):
    e = env
    q = e.quote("99", "101")
    target = e.account
    if action == "close":
        target = e.desk.open(e.account, side, "2", q["id"], "", "open-request-0001", {}, 10)["position"]["id"]
    o = e.limits.place(action, target, "1", "100", q["id"], "limit-request-0001", {}, side, 10)["order"]
    assert e.limits.match({q["symbol"]: q}) == []  # Resting orders require a later snapshot.
    e.now[0] += 1
    assert e.limits.match({q["symbol"]: e.mark("99", "101")}) == []
    buy = (action == "open") == (side == "long")
    touched = e.mark("99" if buy else "101")
    assert e.limits.match({q["symbol"]: touched}) == [o["id"]]
    assert e.limits.match({q["symbol"]: touched}) == []
    exported = e.desk.export(e.account)
    filled = next(f for f in exported["fills"] if f["order_id"] == o["id"])
    assert D(filled["price"]) == 100
    assert D(filled["fee"]) == D(".02")
    assert exported["orders"][0]["trigger_quote_id"] == filled["quote_id"]
    with pytest.raises(ValueError, match="已经成交"):
        e.limits.cancel(o["id"], "cancel-request-0001")


def test_stale_missing_and_pre_submission_timestamps_never_fill(env):
    e = env
    o = place(e)
    e.now[0] += 20000
    fresh = e.mark("99")
    for mark in ({}, {fresh["symbol"]: dict(fresh, source_ms=o["created_ms"])},
                 {fresh["symbol"]: dict(fresh, received_ms=o["created_ms"])},
                 {fresh["symbol"]: dict(fresh, source_ms=e.now[0]-15001)}):
        assert e.limits.match(mark) == []
    assert not e.desk.export(e.account)["fills"]
    assert e.limits.match({fresh["symbol"]: fresh}) == [o["id"]]


def test_reservation_cancel_replay_and_conflicting_requests_are_atomic(env):
    e = env
    q = e.quote("99", "101")
    args = ("open", e.account, "9", "100", q["id"], "limit-request-0001", {}, "long", 1)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: e.limits.place(*args), range(4)))
    assert len({r["order"]["id"] for r in results}) == 1
    assert D(e.desk.snapshot({})["accounts"][0]["available"]) == D("99.82")
    with pytest.raises(ValueError, match="资金不足"):
        place(e, qty="1", symbol="ETH-USDT-SWAP", key="limit-request-0002")
    with pytest.raises(ValueError, match="请求标识"):
        e.limits.place("open", e.account, "8", "100", q["id"], "limit-request-0001", {}, "long", 1)
    o = results[0]["order"]
    cancelled = e.limits.cancel(o["id"], "cancel-request-0001")
    assert e.limits.cancel(o["id"], "cancel-request-0001") == cancelled
    assert D(e.desk.snapshot({})["accounts"][0]["available"]) == 1000
    e.now[0] += 1
    assert e.limits.match({q["symbol"]: e.mark("99")}) == []
    assert e.desk.export(e.account)["quotes"]  # Cancelled orders retain their submission evidence.


def test_close_orders_lock_only_the_requested_quantity(env):
    e = env
    q = e.quote()
    p = e.desk.open(e.account, "long", "2", q["id"], "", "open-request-0001", {})["position"]
    order = e.limits.place("close", p["id"], "1.5", "110", q["id"], "limit-request-0001")["order"]
    with pytest.raises(ValueError, match="可用持仓"):
        e.desk.close(p["id"], "1", q["id"], "close-request-0001")
    e.desk.close(p["id"], ".5", q["id"], "close-request-0001")
    snap = e.desk.snapshot({q["symbol"]: q})["positions"][0]
    assert D(snap["available_to_close"]) == 0
    e.limits.cancel(order["id"], "cancel-request-0001")
    e.desk.close(p["id"], "1.5", q["id"], "close-request-0002")
    assert D(e.desk.export(e.account)["positions"][0]["remaining"]) == 0


def test_missing_marks_wait_then_adverse_loss_rejects_without_debit(env):
    e = env
    q = e.quote(symbol="ETH-USDT-SWAP")
    e.desk.open(e.account, "long", "9", q["id"], "", "open-request-0001", {})
    o = place(e, qty=".9", marks={q["symbol"]: q})
    before = e.desk.export(e.account)["account"]["cash"]
    e.now[0] += 1
    btc = e.mark("99")
    assert e.limits.match({btc["symbol"]: btc}) == []
    assert e.desk.export(e.account)["orders"][0]["status"] == "pending"
    eth = e.mark("80", symbol=q["symbol"])
    assert e.limits.match({btc["symbol"]: btc, q["symbol"]: eth}) == []
    exported = e.desk.export(e.account)
    assert exported["orders"][0]["status"] == "rejected"
    assert exported["account"]["cash"] == before
    assert len(exported["fills"]) == 1
    assert D(e.desk.snapshot({q["symbol"]: eth})["accounts"][0]["order_reserved"]) == 0


def test_matching_cancellation_race_and_restart_cannot_double_fill(env):
    e = env
    o = place(e)
    e.now[0] += 1
    restarted = limit_module.LimitOrders(book.PaperDesk(e.desk.path.parent))
    def race(i):
        try:
            return restarted.cancel(o["id"], "cancel-request-0001") if i == 0 else restarted.match({"BTC-USDT-SWAP": e.mark("99")})
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(race, range(5)))
    exported = e.desk.export(e.account)
    status = exported["orders"][0]["status"]
    assert status in ("filled", "cancelled")
    assert len(exported["fills"]) == (1 if status == "filled" else 0)


def test_worker_runs_without_browser_and_restarts_from_persistent_orders(env):
    e = env
    place(e)
    e.now[0] += 1
    fetched = threading.Event()
    class Quotes:
        def get(self, symbols):
            fetched.set()
            return {s: e.mark("99", symbol=s) for s in symbols}
    worker = limit_module.LimitOrderWorker(limit_module.LimitOrders(book.PaperDesk(e.desk.path.parent)), Quotes())
    worker.start()
    assert fetched.wait(1)
    worker.close()
    assert not worker.status()["running"]
    assert len(e.desk.export(e.account)["fills"]) == 1


def test_http_preview_limit_cancel_and_origin_boundaries(tmp_path):
    app = FastAPI()
    router = APIRouter(prefix="/api/research", dependencies=[Depends(same_origin)])
    api.install(router, app, SimpleNamespace(runtime=tmp_path))
    class Quotes:
        def get(self, symbols):
            return {s: dict(symbol=s, bid="100", ask="101", source_ms=book.clock_ms(), received_ms=book.clock_ms()) for s in symbols}
    app.state.paper_desk_quotes = Quotes()
    app.include_router(router)
    root = "/api/research/paper-desk"
    with TestClient(app) as c:
        a = c.post(root+"/accounts", json=dict(name="QA", initial_cash="1000", request_id="account-request-0001"), headers=HEADERS).json()
        params = dict(account_id=a["id"], symbol="BTC-USDT-SWAP", side="short", leverage=30, limit_price="105")
        q = c.get(root+"/open-preview", params=params)
        assert q.status_code == 200, q.text
        q = q.json()
        assert c.get(root).json()["fills"] == []
        assert c.get(root+"/open-preview", params=dict(params, leverage=3)).status_code == 409
        payload = dict(side="short", leverage=30, quantity=q["quantity"], limit_price="105", quote_id=q["id"], request_id="limit-request-0001")
        path = root+f'/accounts/{a["id"]}/limit-open'
        assert c.post(path, json=payload).status_code == 403
        result = c.post(path, json=payload, headers=HEADERS)
        assert result.status_code == 201, result.text
        assert c.post(path, json=payload, headers=HEADERS).json() == result.json()
        order = result.json()["order"]
        cancel = root+f'/orders/{order["id"]}/cancel'
        assert c.post(cancel, json=dict(request_id="cancel-request-0001"), headers=HEADERS).json()["order"]["status"] == "cancelled"
        assert D(c.get(root).json()["accounts"][0]["available"]) == 1000


def test_equity_multiple_reserves_only_margin_and_fee_leaving_room_for_another_order(env):
    e = env
    orders = []
    for index, symbol in enumerate(["BTC-USDT-SWAP", "ETH-USDT-SWAP"]):
        q = e.quote("99", "101", symbol=symbol)
        sizing = e.desk.size_by_equity(e.account, "long", q["id"], 100, 10, {}, "100")
        assert sizing["sizing"] == "equity_multiple"
        assert sizing["position_multiple"] == 10
        assert D(sizing["sizing_equity"]) == 1000
        assert D(sizing["notional"]) == 10000
        assert D(sizing["quantity"]) == 100
        assert D(sizing["margin"]) == 100
        assert D(sizing["fee"]) == 2
        assert D(sizing["available_after"]) == 898 - 102 * index
        result = e.limits.place("open", e.account, sizing["quantity"], "100", q["id"],
                                f"equity-limit-{index:08d}", {}, "long", 100)
        orders.append(result["order"])
        assert D(e.desk.snapshot({})["accounts"][0]["available"]) == 898 - 102 * index
    e.limits.cancel(orders[0]["id"], "equity-cancel-0001")
    assert D(e.desk.snapshot({})["accounts"][0]["available"]) == 898
    e.now[0] += 1
    e.limits.match({"ETH-USDT-SWAP": e.mark(symbol="ETH-USDT-SWAP")})
    snap = e.desk.snapshot({"ETH-USDT-SWAP": e.mark(symbol="ETH-USDT-SWAP")})
    assert D(snap["accounts"][0]["balance"]) == 998
    assert D(snap["accounts"][0]["reserved"]) == 100
    assert D(snap["accounts"][0]["available"]) == 898
    assert snap["positions"][0]["leverage"] == 100
    assert D(snap["positions"][0]["quantity"]) == 100


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("limit", [None, "123.456789"])
def test_margin_leverage_changes_collateral_but_not_equity_sized_exposure(env, side, limit):
    e = env
    q = e.quote("99", "101")
    low = e.desk.size_by_equity(e.account, side, q["id"], 50, 10, {}, limit)
    high = e.desk.size_by_equity(e.account, side, q["id"], 100, 10, {}, limit)
    half = e.desk.size_by_equity(e.account, side, q["id"], 100, 5, {}, limit)
    px = D(limit or ("101" if side == "long" else "99"))
    assert low["quantity"] == high["quantity"]
    assert low["fee"] == high["fee"]
    assert D(low["margin"]) == 2 * D(high["margin"])
    assert 0 <= D(10000) - D(high["notional"]) < D("1e-12") * px
    assert abs(D(high["quantity"]) - 2 * D(half["quantity"])) <= D("1e-12")


@pytest.mark.parametrize("multiple", [0, 3, 125, True, 2.5])
def test_equity_sizing_rejects_invalid_position_multiple(env, multiple):
    with pytest.raises(ValueError, match="仓位倍数"):
        env.desk.size_by_equity(env.account, "long", env.quote()["id"], 100, multiple, {})


def test_equity_sizing_rejects_insufficient_collateral_without_shrinking_and_rechecks_on_submit(env):
    e = env
    q = e.quote()
    with pytest.raises(ValueError, match="资金不足"):
        e.desk.size_by_equity(e.account, "long", q["id"], 10, 10, {})
    sizing = e.desk.size_by_equity(e.account, "long", q["id"], 100, 10, {})
    place(e, qty="9", symbol="ETH-USDT-SWAP", leverage=1)
    with pytest.raises(ValueError, match="资金不足"):
        e.desk.size_by_equity(e.account, "long", q["id"], 100, 10, {})
    with pytest.raises(ValueError, match="资金不足"):
        e.desk.open(e.account, "long", sizing["quantity"], q["id"], "", "equity-open-0001", {}, 100)
    assert e.desk.export(e.account)["fills"] == []


def test_equity_sizing_requires_fresh_marks_and_uses_current_equity(env):
    e = env
    first = e.quote(symbol="ETH-USDT-SWAP")
    e.desk.open(e.account, "long", "1", first["id"], "", "equity-existing-0001", {}, 100)
    q = e.quote()
    with pytest.raises(ValueError, match="行情不完整"):
        e.desk.size_by_equity(e.account, "long", q["id"], 100, 10, {})
    mark = e.mark("90", symbol="ETH-USDT-SWAP")
    sizing = e.desk.size_by_equity(e.account, "long", q["id"], 100, 10, {mark["symbol"]: mark})
    assert D(sizing["sizing_equity"]) == D("989.95")
    assert D(sizing["notional"]) == D("9899.5")


def test_http_equity_sizing_freezes_quantity_and_retains_contract_leverage(tmp_path):
    app = FastAPI()
    router = APIRouter(prefix="/api/research", dependencies=[Depends(same_origin)])
    api.install(router, app, SimpleNamespace(runtime=tmp_path))
    class Quotes:
        def get(self, symbols):
            return {s: dict(symbol=s, bid="100", ask="100", source_ms=book.clock_ms(), received_ms=book.clock_ms()) for s in symbols}
    app.state.paper_desk_quotes = Quotes()
    app.include_router(router)
    root = "/api/research/paper-desk"
    with TestClient(app) as c:
        a = c.post(root+"/accounts", json=dict(name="QA", initial_cash="1000", request_id="equity-account-0001"), headers=HEADERS).json()
        params = dict(account_id=a["id"], symbol="BTC-USDT-SWAP", side="long", leverage=100,
                      position_multiple=10, limit_price="100", take_profit="110", stop_loss="90")
        response = c.get(root+"/open-preview", params=params)
        assert response.status_code == 200, response.text
        q = response.json()
        assert D(q["notional"]) == 10000
        assert D(q["available_after"]) == 895
        assert c.get(root+"/open-preview", params=dict(params, position_multiple=3)).status_code == 409
        assert c.get(root+"/open-preview", params=dict(params, position_multiple="2.5")).status_code == 422
        payload = dict(side="long", leverage=100, quantity=q["quantity"], limit_price="100", quote_id=q["id"],
                       take_profit="110", stop_loss="90", request_id="equity-limit-0001")
        path = root+f'/accounts/{a["id"]}/limit-open'
        result = c.post(path, json=payload, headers=HEADERS)
        assert result.status_code == 201, result.text
        assert c.post(path, json=payload, headers=HEADERS).json() == result.json()
        assert result.json()["order"]["leverage"] == 100
        assert D(c.get(root).json()["accounts"][0]["available"]) == 895
