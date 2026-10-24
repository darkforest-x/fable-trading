"""Behavioral tests for the paper-only Monday 1.618 bulk order campaign."""
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from yoyo.monitor.monday_fib import week_window
from yoyo.research_workspace import paper_desk as book
from yoyo.research_workspace import paper_desk_limits as limit_module
from yoyo.research_workspace import paper_desk_risk as risk_module
from yoyo.research_workspace import monday_bulk as bulk_module


D = Decimal


def settings(**changes):
    value = dict(notional_usdt="200", leverage=100, direction="both", mode="weekly",
                 take_profit="near", stop_loss_percent=None)
    value.update(changes)
    return value


def instrument(symbol):
    return dict(instId=symbol, ctVal="1", lotSz="1", tickSz="0.1", lever="125",
                minSz="1", maxLmtSz="1000000", maxMktSz="1000000")


def row(symbol, now, *, price="95", stale=False, state="ready", touches=None,
        bid="94.9", ask="95.1", high="100", low="90"):
    risk = dict(contract_size="1", fetched_ms=now,
                tiers=[dict(maxSz="100000000", maxLever="125", mmr="0.005")],
                instrument=instrument(symbol))
    quote = dict(symbol=symbol, bid=bid, ask=ask, source_ms=now, received_ms=now,
                 mark_price=price, mark_source_ms=now, risk=risk)
    spread = D(high) - D(low)
    upper = D(high) + D("0.618") * spread
    lower = D(low) - D("0.618") * spread
    fib = dict(symbol=symbol, state=state, stale=stale, high=high, low=low,
               upper=str(upper), lower=str(lower), price=price,
               touches=touches or {})
    return fib, quote


@pytest.fixture
def env(tmp_path, monkeypatch):
    now = [1_800_000_000_000]
    for module in (book, limit_module, risk_module, bulk_module):
        monkeypatch.setattr(module, "clock_ms", lambda: now[0])
    symbols = ["BTC-USDT-SWAP"]
    fib_rows, quotes = {}, {}
    for symbol in symbols:
        fib_rows[symbol], quotes[symbol] = row(symbol, now[0])
    snapshot = dict(now_ms=now[0], week=week_window(now[0])["week"], monday_complete=True,
                    stream=dict(connected=True), items=list(fib_rows.values()))
    desk = book.PaperDesk(tmp_path)
    account = desk.create_account("Bulk test", "100000", "bulk-account-request-0001")["id"]
    limits = limit_module.LimitOrders(desk)
    source = SimpleNamespace(now=now, symbols=symbols, fib_rows=fib_rows, quotes=quotes,
                             snapshot=snapshot, desk=desk, account=account, limits=limits)

    def get_snapshot():
        source.snapshot["now_ms"] = source.now[0]
        source.snapshot["week"] = week_window(source.now[0])["week"]
        source.snapshot["items"] = list(source.fib_rows.values())
        source.snapshot["monday_complete"] = True
        return source.snapshot

    def get_quotes(wanted):
        return {symbol: source.quotes[symbol] for symbol in wanted if symbol in source.quotes}

    def get_catalog():
        return [dict(symbol=s, base=s.removesuffix("-USDT-SWAP")) for s in source.symbols]

    def service():
        return bulk_module.MondayBulk(desk, limits, get_snapshot, get_quotes, get_catalog)

    source.service = service
    return source


def db_orders(e):
    with e.desk.connect() as db:
        return [dict(r) for r in db.execute(
            "SELECT * FROM limit_orders WHERE account_id=? ORDER BY created_ms,id", (e.account,))]


def configure_enabled(e, service=None, **settings_changes):
    service = service or e.service()
    return service.configure(e.account, settings(**settings_changes), True,
                             "monday-bulk-config-request-0001", 0)


def test_new_account_starts_disabled_and_tick_creates_nothing(env):
    service = env.service()

    state = service.status(env.account)
    assert state["enabled"] is False
    assert state["version"] == 0
    assert state.get("settings") is None
    service.tick()
    assert db_orders(env) == []


def test_enabled_both_posts_exact_passive_levels_and_dedupes_across_restart(env):
    service = env.service()
    configure_enabled(env, service)

    service.tick()
    orders = db_orders(env)
    assert len(orders) == 2
    by_side = {order["side"]: order for order in orders}
    assert set(by_side) == {"long", "short"}
    assert by_side["long"]["status"] == by_side["short"]["status"] == "pending"
    assert D(by_side["long"]["limit_price"]) == D("83.8")
    assert D(by_side["short"]["limit_price"]) == D("106.2")
    # near TP is the corresponding Monday boundary; no default stop is invented.
    assert D(by_side["long"]["take_profit"]) == D("90")
    assert D(by_side["short"]["take_profit"]) == D("100")
    assert by_side["long"]["stop_loss"] is None
    assert by_side["short"]["stop_loss"] is None
    assert by_side["long"]["bulk_level"] != by_side["long"]["limit_price"]

    service.tick()
    restarted = env.service()
    restarted.tick()
    assert len(db_orders(env)) == 2


def test_far_targets_are_opposite_monday_boundaries_and_stop_requires_explicit_percent(env):
    service = env.service()
    configure_enabled(env, service, take_profit="far", stop_loss_percent="5")

    service.tick()
    by_side = {order["side"]: order for order in db_orders(env)}
    assert D(by_side["short"]["take_profit"]) == D("90")
    assert D(by_side["long"]["take_profit"]) == D("100")
    assert D(by_side["short"]["stop_loss"]) == D("111.5")
    assert D(by_side["long"]["stop_loss"]) == D("79.6")


@pytest.mark.parametrize("direction,mutation", [
    ("both", "stale"),
    ("short", "upper_touched"),
    ("long", "lower_touched"),
    ("long", "missing_price"),
    ("short", "marketable"),
    ("long", "marketable"),
])
def test_stale_touched_or_marketable_candidates_are_never_created(env, direction, mutation):
    symbol = env.symbols[0]
    fib, quote = env.fib_rows[symbol], env.quotes[symbol]
    if mutation == "stale":
        fib["stale"] = True
    elif mutation == "upper_touched":
        fib["touches"] = {"upper": dict(source="live", observed_ms=env.now[0])}
    elif mutation == "lower_touched":
        fib["touches"] = {"lower": dict(source="live", observed_ms=env.now[0])}
    elif mutation == "missing_price":
        fib["price"] = None
    elif mutation == "marketable" and direction == "short":
        quote["bid"] = "106.2"
        quote["ask"] = "106.3"
    elif mutation == "marketable" and direction == "long":
        quote["ask"] = "83.8"
        quote["bid"] = "83.7"
    service = env.service()
    configure_enabled(env, service, direction=direction)

    if mutation == "missing_price":
        preview = service.preview(env.account, settings(direction=direction))
        candidate = next(item for item in preview["items"] if item["side"] == direction)
        assert candidate["status"] == "skipped"
        assert "无效" in candidate["reason"]

    service.tick()
    assert db_orders(env) == []


def test_preview_reports_available_funds_and_aggregate_entry_costs(env):
    service = env.service()
    preview = service.preview(env.account, settings())

    eligible = [item for item in preview["items"] if item["status"] == "eligible"]
    expected_margin = sum((D(item["estimated_margin"]) for item in eligible), D(0))
    expected_fees = sum((D(item["quantity"]) * D(item["entry"]) * book.MAKER_FEE
                         for item in eligible), D(0))
    available = D(preview["available"])

    assert len(eligible) == 2
    assert available == D("100000")
    assert D(preview["estimated_margin"]) == expected_margin
    assert D(preview["estimated_entry_fees"]) == expected_fees
    assert D(preview["available_after"]) == available - expected_margin - expected_fees


def test_stop_cancels_only_bulk_pending_and_preserves_manual_orders_and_positions(env):
    symbol = env.symbols[0]
    quote = env.quotes[symbol]
    manual_quote = env.desk.save_quote(quote)
    manual = env.limits.place("open", env.account, "1", "90", manual_quote["id"],
                              "manual-limit-request-0001", {symbol: quote}, "long", 10)["order"]
    service = env.service()
    configure_enabled(env, service)
    service.tick()
    assert len(db_orders(env)) == 3

    service.configure(env.account, None, False, "monday-bulk-stop-request-0001", 1)
    by_id = {order["id"]: order for order in db_orders(env)}
    assert by_id[manual["id"]]["status"] == "pending"
    assert by_id[manual["id"]].get("bulk_kind") is None
    assert all(o["status"] == "cancelled" for o in by_id.values() if o.get("bulk_kind") == "monday_1618")
    assert service.status(env.account)["enabled"] is False


def test_expired_bulk_order_is_cancelled_before_match_can_fill(env):
    service = env.service()
    configure_enabled(env, service)
    service.tick()
    order = next(o for o in db_orders(env) if o["side"] == "long")
    expired = order["bulk_expires_ms"]
    env.now[0] = expired
    mark = dict(env.quotes[order["symbol"]], bid=order["limit_price"], ask=order["limit_price"],
                source_ms=env.now[0], received_ms=env.now[0], mark_source_ms=env.now[0])

    assert env.limits.match({order["symbol"]: mark}) == []
    current = next(o for o in db_orders(env) if o["id"] == order["id"])
    assert current["status"] == "cancelled"
    assert not env.desk.export(env.account)["fills"]


def test_insufficient_funds_are_reported_and_do_not_create_order(env):
    with env.desk.connect() as db:
        db.execute("UPDATE accounts SET cash='1',initial_cash='1' WHERE id=?", (env.account,))
    service = env.service()
    configure_enabled(env, service, direction="long", leverage=1, notional_usdt="100")

    preview = service.preview(env.account, settings(direction="long", leverage=1, notional_usdt="100"))
    assert preview["counts"]["rejected"] >= 1
    service.tick()
    assert db_orders(env) == []
    status = service.status(env.account)
    assert status["counts"]["rejected"] >= 1


def test_bulk_explicitly_exceeds_manual_50_order_and_30_symbol_limits(env):
    symbols = [f"C{i:03d}-USDT-SWAP" for i in range(51)]
    env.symbols[:] = symbols
    env.fib_rows.clear()
    env.quotes.clear()
    for symbol in symbols:
        env.fib_rows[symbol], env.quotes[symbol] = row(symbol, env.now[0])
    service = env.service()
    configure_enabled(env, service, direction="long", leverage=100, notional_usdt="100")

    service.tick()
    orders = db_orders(env)
    assert len(orders) == 51
    assert sum(order["status"] == "pending" for order in orders) == 51
    assert len({order["symbol"] for order in orders}) == 51


def test_real_maker_touch_creates_fill_at_limit_and_retains_bulk_quote_marker(env):
    service = env.service()
    configure_enabled(env, service, direction="long")
    service.tick()
    order = next(o for o in db_orders(env) if o.get("bulk_kind") == "monday_1618")
    env.now[0] += 1
    trigger = dict(env.quotes[order["symbol"]], bid=order["limit_price"], ask=order["limit_price"],
                   source_ms=env.now[0], received_ms=env.now[0], mark_source_ms=env.now[0])

    assert env.limits.match({order["symbol"]: trigger}) == [order["id"]]
    exported = env.desk.export(env.account)
    fill = next(f for f in exported["fills"] if f["order_id"] == order["id"])
    assert D(fill["price"]) == D(order["limit_price"])
    assert D(fill["fee_rate"]) == book.MAKER_FEE
    with env.desk.connect() as db:
        quote = dict(db.execute("SELECT * FROM quotes WHERE id=?", (fill["quote_id"],)).fetchone())
    assert json.loads(quote["risk_json"])["monday_bulk"] == dict(
        account_id=env.account, week=order["bulk_week"], generation=order["bulk_generation"])


def test_existing_same_direction_manual_position_is_not_merged_by_bulk_fill(env):
    symbol = env.symbols[0]
    quote = env.desk.save_quote(env.quotes[symbol])
    position = env.desk.open(env.account, "long", "1", quote["id"], "manual",
                             "manual-position-request-0001", {symbol: env.quotes[symbol]}, 10)["position"]
    original = (position["id"], position["quantity"], position["entry_price"])
    service = env.service()
    configure_enabled(env, service)

    service.tick()
    orders = [o for o in db_orders(env) if o.get("bulk_kind") == "monday_1618"]
    assert len(orders) == 1
    assert orders[0]["side"] == "short"
    current = next(p for p in env.desk.export(env.account)["positions"] if p["id"] == position["id"])
    assert (current["id"], current["quantity"], current["entry_price"]) == original


@pytest.mark.parametrize("mode", ["once", "weekly"])
def test_week_rollover_disables_once_and_refreshes_weekly(env, mode):
    service = env.service()
    configure_enabled(env, service, mode=mode, direction="long")
    service.tick()
    first = next(o for o in db_orders(env) if o.get("bulk_kind") == "monday_1618")
    next_week = first["bulk_expires_ms"]
    next_window = week_window(next_week)
    # Weekly mode waits for the following New York Monday to finish before it arms.
    env.now[0] = next_window["end"]
    env.fib_rows.clear()
    env.quotes.clear()
    for symbol in env.symbols:
        env.fib_rows[symbol], env.quotes[symbol] = row(symbol, env.now[0])

    service.tick()
    orders = [o for o in db_orders(env) if o.get("bulk_kind") == "monday_1618"]
    state = service.status(env.account)
    if mode == "once":
        assert state["enabled"] is False
        assert all(o["status"] == "cancelled" for o in orders)
        assert len(orders) == 1
    else:
        assert state["enabled"] is True
        assert any(o["status"] == "cancelled" for o in orders)
        assert any(o["status"] == "pending" and o["bulk_week"] == next_window["week"]
                   for o in orders)


def test_stop_generation_change_between_preview_and_place_rejects_stale_tick(env, monkeypatch):
    service = env.service()
    configure_enabled(env, service, direction="long")
    original_place = service.limits.place
    stopped = [False]

    def stop_before_place(*args, **kwargs):
        if kwargs.get("bulk_identity") and not stopped[0]:
            stopped[0] = True
            service.configure(env.account, None, False, "monday-bulk-race-stop-0001", 1)
        return original_place(*args, **kwargs)

    monkeypatch.setattr(service.limits, "place", stop_before_place)
    service.tick()

    assert stopped[0]
    assert service.status(env.account)["enabled"] is False
    assert not [o for o in db_orders(env) if o.get("bulk_kind") == "monday_1618"]


@pytest.mark.parametrize("identity_change", ["expired", "previous_week"])
def test_limit_place_rejects_expired_or_noncurrent_week_identity(env, identity_change):
    service = env.service()
    configure_enabled(env, service, direction="long")
    state = service.status(env.account)
    window = week_window(env.now[0])
    week = window["week"]
    expiry = window["expires"]
    if identity_change == "expired":
        expiry = env.now[0] - 1
    else:
        week = "2027-01-04"
    symbol = env.symbols[0]
    quote_value = dict(env.quotes[symbol], risk={**env.quotes[symbol]["risk"], "monday_bulk":
                       dict(account_id=env.account, week=week, generation=state["generation"])})
    saved = env.desk.save_quote(quote_value)
    identity = dict(week=week, generation=state["generation"], expires_ms=expiry, level="83.82")

    with pytest.raises(ValueError, match="过期|周区间无效"):
        env.limits.place("open", env.account, "1", "83.8", saved["id"],
                         "monday-bulk-invalid-identity-0001", env.quotes, "long", 100,
                         bulk_identity=identity)
    assert not [o for o in db_orders(env) if o.get("bulk_kind") == "monday_1618"]
