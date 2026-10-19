"""Manual paper money, execution receipts and public-quote failure contracts."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
from types import SimpleNamespace

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from yoyo.research_workspace.api import same_origin
from yoyo.research_workspace import paper_desk as book
from yoyo.research_workspace import paper_desk_api as api

HEADERS = {"origin": "http://testserver", "sec-fetch-site": "same-origin"}


@pytest.fixture
def desk(tmp_path):
    return book.PaperDesk(tmp_path)


def quote(desk, bid="100", ask="100", symbol="BTC-USDT-SWAP"):
    return desk.save_quote(dict(symbol=symbol, bid=bid, ask=ask, source_ms=book.clock_ms(), received_ms=book.clock_ms()))


def create(desk, cash="1000", key="account-request-0001"):
    return desk.create_account("练习账户", cash, key)["id"]


@pytest.mark.parametrize("side,exit_price", [("long", "110"), ("short", "90")])
def test_long_short_partial_exit_fee_equity_and_restart(desk, side, exit_price):
    account = create(desk)
    entry = quote(desk)
    opened = desk.open(account, side, "2", entry["id"], "first practice", "open-request-0001", {})
    position = opened["position"]["id"]
    mark = quote(desk, exit_price, exit_price)
    snap = desk.snapshot({mark["symbol"]: mark})["accounts"][0]
    assert Decimal(snap["balance"]) == Decimal("999.9")
    assert Decimal(snap["equity"]) == Decimal("1019.9")
    assert Decimal(snap["available"]) == Decimal("799.9")
    desk.close(position, ".5", mark["id"], "close-request-0001")
    desk = book.PaperDesk(desk.path.parent)
    data = desk.snapshot({mark["symbol"]: mark})
    assert Decimal(data["positions"][0]["remaining"]) == Decimal("1.5")
    assert Decimal(data["accounts"][0]["reserved"]) == 150
    desk.close(position, "1.5", mark["id"], "close-request-0002")
    exported = desk.export(account)
    expected = Decimal("1020") - Decimal(".1") - Decimal(exit_price) * 2 * Decimal(".0005")
    assert Decimal(exported["account"]["cash"]) == expected
    assert len(exported["fills"]) == 3
    assert len(exported["quotes"]) == 2
    assert exported["positions"][0]["closed_ms"]
    assert exported["production_eligible"] is False


def test_spread_and_roundtrip_cost_are_losses_at_unchanged_market(desk):
    account = create(desk)
    q = quote(desk, "99", "101")
    p = desk.open(account, "long", "1", q["id"], "", "open-request-0001", {})["position"]
    desk.close(p["id"], "1", q["id"], "close-request-0001")
    assert Decimal(desk.export(account)["account"]["cash"]) == Decimal("997.900")


def test_distinct_concurrent_orders_cannot_double_spend(desk):
    account = create(desk, "150")
    quotes = [quote(desk, symbol=s) for s in ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]]
    marks = {q["symbol"]: q for q in quotes}
    def opening(i):
        try:
            return desk.open(account, "long", "1", quotes[i]["id"], "", f"open-request-000{i}", marks)
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(opening, range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(desk.export(account)["fills"]) == 1


def test_duplicate_concurrent_requests_and_overspending_are_atomic(desk):
    account = create(desk, "100.1")
    q = quote(desk)
    def opening(_):
        return desk.open(account, "long", "1", q["id"], "", "open-request-0001", {})
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(opening, range(4)))
    assert len({r["fill"]["id"] for r in results}) == 1
    p = results[0]["position"]
    with pytest.raises(ValueError, match="请求标识"):
        desk.open(account, "short", "1", q["id"], "", "open-request-0001", {})
    other = quote(desk, symbol="ETH-USDT-SWAP")
    with pytest.raises(ValueError, match="资金不足"):
        desk.open(account, "long", "1", other["id"], "", "open-request-0002", {q["symbol"]: q})
    assert len(desk.export(account)["fills"]) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        closed = list(pool.map(lambda _: desk.close(p["id"], "1", q["id"], "close-request-0001"), range(4)))
    assert len({r["fill"]["id"] for r in closed}) == 1
    assert len(desk.export(account)["fills"]) == 2


def test_expired_mismatched_missing_and_overclose_leave_ledger_unchanged(desk, monkeypatch):
    account = create(desk)
    q = quote(desk)
    p = desk.open(account, "long", "1", q["id"], "", "open-request-0001", {})["position"]
    other = quote(desk, symbol="ETH-USDT-SWAP")
    with pytest.raises(ValueError, match="不匹配"):
        desk.close(p["id"], "1", other["id"], "close-request-0001")
    with pytest.raises(ValueError, match="超过"):
        desk.close(p["id"], "2", q["id"], "close-request-0002")
    unavailable = desk.snapshot({})["accounts"][0]
    assert unavailable["equity"] is None and unavailable["available"] is None
    monkeypatch.setattr(book, "clock_ms", lambda: q["received_ms"] + 15001)
    with pytest.raises(ValueError, match="过期"):
        desk.close(p["id"], "1", q["id"], "close-request-0003")
    # An already committed request can be replayed after its quote expires.
    assert desk.open(account, "long", "1", q["id"], "", "open-request-0001", {})["position"]["id"] == p["id"]
    assert len(desk.export(account)["fills"]) == 1


def test_losses_reduce_buying_power_and_accounts_are_isolated(desk):
    account = create(desk)
    other = create(desk, "500", "account-request-0002")
    q = quote(desk)
    p = desk.open(account, "short", "2", q["id"], "", "open-request-0001", {})["position"]
    adverse = quote(desk, "500", "500")
    a = next(a for a in desk.snapshot({q["symbol"]: adverse})["accounts"] if a["id"] == account)
    assert Decimal(a["available"]) == 0
    desk.close(p["id"], "2", adverse["id"], "close-request-0001")
    assert Decimal(desk.export(other)["account"]["cash"]) == 500


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "1e13", "1e-13", "invalid"])
def test_invalid_amounts_are_rejected(desk, value):
    with pytest.raises(ValueError):
        create(desk, value)


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    router = APIRouter(prefix="/api/research", dependencies=[Depends(same_origin)])
    api.install(router, app, SimpleNamespace(runtime=tmp_path))
    class Quotes:
        def get(self, symbols):
            return {s: dict(symbol=s, bid="100", ask="101", source_ms=book.clock_ms(), received_ms=book.clock_ms()) for s in symbols}
    app.state.paper_desk_quotes = Quotes()
    app.include_router(router)
    with TestClient(app) as c:
        yield c, app


@pytest.mark.parametrize('symbol', ['BTC-USDT-SWAP', 'W-USDT-SWAP'])
def test_http_lifecycle_and_origin_input_boundaries(client, symbol):
    c, app = client
    prefix = "/api/research/paper-desk"
    payload = dict(name="Practice", initial_cash="1000", request_id="account-request-0001")
    assert c.post(prefix + "/accounts", json=payload).status_code == 403
    a = c.post(prefix + "/accounts", json=payload, headers=HEADERS).json()
    q = c.get(prefix + "/quote", params={"symbol": symbol}).json()
    assert q['symbol'] == symbol
    preview = c.get(prefix + '/open-preview', params=dict(account_id=a['id'], symbol=symbol,
                    leverage=20, position_multiple=1))
    assert preview.status_code == 200, preview.text
    assert preview.json()['symbol'] == symbol
    order = dict(side="long", quantity="1", quote_id=q["id"], request_id="open-request-0001")
    path = prefix + f'/accounts/{a["id"]}/open'
    assert c.post(path, json=dict(order, fee_rate=0), headers=HEADERS).status_code == 422
    opened = c.post(path, json=order, headers=HEADERS)
    assert opened.status_code == 201, opened.text
    p = opened.json()["position"]
    assert c.get(prefix).json()["accounts"][0]["marks_complete"]
    close = dict(quantity="1", quote_id=q["id"], request_id="close-request-0001")
    result = c.post(prefix + f'/positions/{p["id"]}/close', json=close, headers=HEADERS)
    assert result.status_code == 200
    exported = c.get(prefix + f'/accounts/{a["id"]}/export')
    assert len(exported.json()["fills"]) == 2
    assert "attachment" in exported.headers["content-disposition"]
    assert c.get(prefix + "/quote", params={"symbol": "../../secret"}).status_code == 422


def test_quote_failure_keeps_records_and_retry_receipt_available(client):
    c, app = client
    prefix = "/api/research/paper-desk"
    a = create(app.state.paper_desk)
    q = quote(app.state.paper_desk)
    order = dict(side="long", quantity="1", quote_id=q["id"], request_id="open-request-0001")
    path = prefix + f"/accounts/{a}/open"
    result = c.post(path, json=order, headers=HEADERS).json()
    def down(symbols):
        raise httpx.ConnectError("offline")
    app.state.paper_desk_quotes.get = down
    overview = c.get(prefix).json()
    assert overview["market_error"] and overview["accounts"][0]["equity"] is None
    assert c.get(prefix + "/quote", params={"symbol": "BTC-USDT-SWAP"}).status_code == 503
    assert c.post(path, json=order, headers=HEADERS).json() == result


@pytest.mark.parametrize('symbol', ['BTC-USDT-SWAP', 'W-USDT-SWAP'])
def test_public_data_fails_closed_on_stale_nonfinite_and_crossed_quotes(monkeypatch, symbol):
    at = book.clock_ms()
    def row(**kwargs):
        return dict(instId=symbol, bidPx="100", askPx="101", ts=str(at), **kwargs)
    rows = [row()]
    monkeypatch.setattr(api.PublicQuotes, "fetch", staticmethod(lambda: rows))
    assert api.PublicQuotes().get([symbol])
    for key, bad in [("bidPx", "NaN"), ("bidPx", "102"), ("ts", str(at-20000)), ("ts", str(at+60000))]:
        rows[:] = [dict(row(), **{key: bad})]
        assert api.PublicQuotes().get([symbol]) == {}


@pytest.mark.parametrize("side,exit_price", [("long", "110"), ("short", "90")])
def test_leverage_changes_margin_not_exposure_pnl_or_fees(desk, side, exit_price):
    entry = quote(desk)
    results = []
    for leverage in (1, 10, 100):
        account = create(desk, key=f"account-request-{leverage:04}")
        p = desk.open(account, side, "2", entry["id"], "", f"open-request-{leverage:04}", {}, leverage)["position"]
        data = desk.snapshot({entry["symbol"]: entry})
        a = next(a for a in data["accounts"] if a["id"] == account)
        assert Decimal(a["reserved"]) == Decimal(200) / leverage
        assert Decimal(a["available"]) == Decimal("999.9") - Decimal(200) / leverage
        mark = quote(desk, exit_price, exit_price)
        desk.close(p["id"], ".5", mark["id"], f"close-request-part-{leverage}")
        data = desk.snapshot({mark["symbol"]: mark})
        position = next(p for p in data["positions"] if p["account_id"] == account)
        assert Decimal(position["initial_margin"]) == Decimal(200) / leverage
        assert Decimal(position["reserved_margin"]) == Decimal(150) / leverage
        assert position["leverage"] == leverage
        desk.close(p["id"], "1.5", mark["id"], f"close-request-full-{leverage}")
        exported = desk.export(account)
        assert {f["leverage"] for f in exported["fills"]} == {leverage}
        assert exported["schema_version"] == 8
        results.append((exported["account"]["cash"], [(f["quantity"], f["gross_pnl"], f["fee"]) for f in exported["fills"]]))
        final = next(a for a in desk.snapshot({})["accounts"] if a["id"] == account)
        assert Decimal(final["reserved"]) == 0
    assert results[0] == results[1] == results[2]
    # Both directions make 20 USDT gross on two base units, regardless of margin.
    assert Decimal(results[0][0]) == Decimal(1020) - Decimal(".1") - Decimal(exit_price) * 2 * book.FEE


def test_leveraged_admission_keeps_fees_and_losses_in_buying_power(desk):
    account = create(desk, "10.05")
    q = quote(desk)
    with pytest.raises(ValueError, match="资金不足"):
        desk.open(account, "long", "1", q["id"], "", "open-request-0001", {}, 1)
    p = desk.open(account, "long", "1", q["id"], "", "open-request-0001", {}, 10)["position"]
    assert Decimal(desk.snapshot({q["symbol"]: q})["accounts"][0]["available"]) == 0
    with pytest.raises(ValueError, match="请求标识"):
        desk.open(account, "long", "1", q["id"], "", "open-request-0001", {}, 20)
    adverse = quote(desk, "90", "90")
    a = desk.snapshot({q["symbol"]: adverse})["accounts"][0]
    assert Decimal(a["equity"]) == 0 and Decimal(a["available"]) == 0
    other = quote(desk, symbol="ETH-USDT-SWAP")
    with pytest.raises(ValueError, match="资金不足"):
        desk.open(account, "long", ".01", other["id"], "", "open-request-0002", {q["symbol"]: adverse}, 100)
    assert len(desk.export(account)["fills"]) == 1
    # Manual close remains possible after losses; no automatic liquidation is implied.
    desk.close(p["id"], "1", adverse["id"], "close-request-0001")
    assert Decimal(desk.export(account)["account"]["cash"]) == Decimal("-.045")


def test_mixed_leverage_reserves_sum_and_partial_close_releases_proportionally(desk):
    account = create(desk, "100")
    first = quote(desk)
    second = quote(desk, symbol="ETH-USDT-SWAP")
    marks = {q["symbol"]: q for q in (first, second)}
    p = desk.open(account, "long", "2", first["id"], "", "open-request-0001", marks, 10)["position"]
    desk.open(account, "short", "1", second["id"], "", "open-request-0002", marks, 5)
    a = desk.snapshot(marks)["accounts"][0]
    assert Decimal(a["reserved"]) == 40 and Decimal(a["available"]) == Decimal("59.85")
    desk.close(p["id"], ".5", first["id"], "close-request-0001")
    a = book.PaperDesk(desk.path.parent).snapshot(marks)["accounts"][0]
    assert Decimal(a["reserved"]) == 35 and Decimal(a["available"]) == Decimal("64.825")


@pytest.mark.parametrize("leverage", [0, -1, 126, 2.5, 10.0, True, "10", None])
def test_invalid_leverage_rejected_by_http_and_ledger(client, leverage):
    c, app = client
    account = create(app.state.paper_desk)
    q = quote(app.state.paper_desk)
    order = dict(side="long", quantity="1", quote_id=q["id"], request_id="open-request-0001", leverage=leverage)
    result = c.post(f"/api/research/paper-desk/accounts/{account}/open", json=order, headers=HEADERS)
    assert result.status_code == 422
    with pytest.raises(ValueError, match="杠杆"):
        app.state.paper_desk.open(account, "long", "1", q["id"], "", "open-request-0001", {}, leverage)
    assert app.state.paper_desk.export(account)["fills"] == []


def test_http_accepts_leverage_and_disallows_changing_it_on_close(client):
    c, app = client
    account = create(app.state.paper_desk, "11")
    q = quote(app.state.paper_desk)
    prefix = "/api/research/paper-desk"
    order = dict(side="long", quantity="1", quote_id=q["id"], request_id="open-request-0001", leverage=10)
    result = c.post(f"{prefix}/accounts/{account}/open", json=order, headers=HEADERS)
    assert result.status_code == 201, result.text
    p = result.json()["position"]
    assert p["leverage"] == 10
    assert Decimal(c.get(prefix).json()["accounts"][0]["reserved"]) == 10
    close = dict(quantity="1", quote_id=q["id"], request_id="close-request-0001", leverage=20)
    assert c.post(f'{prefix}/positions/{p["id"]}/close', json=close, headers=HEADERS).status_code == 422


def test_legacy_schema_migrates_as_1x_and_replays_unchanged_receipts(desk):
    account = create(desk)
    q = quote(desk)
    p = desk.open(account, "long", "1", q["id"], "", "open-request-0001", {})["position"]
    desk.close(p["id"], ".25", q["id"], "close-request-0001")
    # Construct the actual v1 table and receipt shapes without relying on DROP COLUMN.
    with desk.connect() as db:
        cols = [r[1] for r in db.execute("PRAGMA table_info(positions)") if r[1] != "leverage"]
        db.execute("CREATE TABLE positions_v1 AS SELECT " + ",".join(cols) + " FROM positions")
        db.execute("DROP TABLE positions")
        db.execute("ALTER TABLE positions_v1 RENAME TO positions")
        for receipt in db.execute("SELECT * FROM receipts").fetchall():
            payload, response = json.loads(receipt["payload"]), json.loads(receipt["response"])
            payload.pop("leverage", None)
            if "position" in response:
                response["position"].pop("leverage", None)
                response["fill"].pop("leverage", None)
            db.execute("UPDATE receipts SET payload=?,response=? WHERE request_id=?", (book.encoded(payload), book.encoded(response), receipt["request_id"]))
        old_receipts = [tuple(r) for r in db.execute("SELECT * FROM receipts ORDER BY request_id")]
    desk = book.PaperDesk(desk.path.parent)
    data = desk.snapshot({q["symbol"]: q})
    assert data["positions"][0]["leverage"] == 1
    assert Decimal(data["accounts"][0]["reserved"]) == 75
    assert desk.open(account, "long", "1", q["id"], "", "open-request-0001", {})["position"]["id"] == p["id"]
    assert desk.close(p["id"], ".25", q["id"], "close-request-0001")["fill"]["leverage"] == 1
    with desk.connect() as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM receipts ORDER BY request_id")] == old_receipts
    assert len(desk.export(account)["fills"]) == 2
    desk.close(p["id"], ".75", q["id"], "close-request-0002")


def test_public_symbol_catalog_filters_deduplicates_caches_and_fails_on_expiry(monkeypatch):
    def row(symbol="BTC-USDT-SWAP", **kwargs):
        return dict(instId=symbol, instType="SWAP", state="live", settleCcy="USDT", ctType="linear", **kwargs)
    valid=row()
    rows=[row("SOL-USDT-SWAP"), row("ETH-USDT-SWAP"), row("W-USDT-SWAP"), valid, valid,
          dict(valid, state="suspend"), dict(valid, instType="FUTURES"),
          dict(valid, instId="BTC-USD-SWAP", settleCcy="BTC", ctType="inverse"),
          dict(valid, instId="FAKE-USDT-SWAP", state="preopen"), row("-USDT-SWAP")]
    now=[0]
    fetched=[]
    monkeypatch.setattr(api.time, "monotonic", lambda:now[0])
    monkeypatch.setattr(api.PublicSymbols, "fetch", staticmethod(lambda:(fetched.append(True) or rows)))
    catalog=api.PublicSymbols()
    assert [r["symbol"] for r in catalog.get()] == ["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP","W-USDT-SWAP"]
    assert catalog.get()[0]["base"] == "BTC"
    now[0]=299
    assert len(catalog.get())==4 and len(fetched)==1
    now[0]=300
    rows[:]=[dict(valid, state="suspend")]
    with pytest.raises(ValueError, match="暂无可用"):
        catalog.get()
    rows[:]=[None]
    with pytest.raises(ValueError, match="字段结构"):
        catalog.get()


def test_symbol_endpoint_returns_catalog_or_visible_503_without_changing_book(client, monkeypatch):
    c, app=client
    expected=[dict(symbol="BTC-USDT-SWAP",base="BTC")]
    monkeypatch.setattr(app.state.paper_desk_symbols, "get", lambda:expected)
    response=c.get('/api/research/paper-desk/symbols')
    assert response.status_code==200 and response.json()==dict(symbols=expected)
    def down():
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(app.state.paper_desk_symbols, "get", down)
    response=c.get('/api/research/paper-desk/symbols')
    assert response.status_code==503 and "重试" in response.json()["detail"]
    assert app.state.paper_desk.snapshot({})["accounts"]==[]
