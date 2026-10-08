import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from yoyo.copier.ai.schema import IntentResult
from yoyo.copier.paper.book import FEE_RATE, PaperBook
from yoyo.copier.paper.client import PaperClient, PaperExecutor
from yoyo.copier.paper.engine import match_once
from yoyo.copier.paper.market import PublicMarket
from yoyo.copier.risk.engine import RiskEngine
from yoyo.copier.store.sqlite import Database

CH = "1356581750914027590"


class FixedMarket(PublicMarket):
    def __init__(self, prices):
        super().__init__(fetch=lambda *_: None)
        self.prices = prices

    def last(self, inst_id):
        px = self.prices.get(inst_id)
        return (px, "okx") if px else None


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as td:
        database = Database(Path(td) / "t.db")
        database.set_setting("exchange_channel_map", json.dumps({CH: "paper"}))
        database.set_setting("channel_leverage_map", json.dumps({CH: 10}))
        database.set_setting("max_leverage", "20")
        database.set_setting("position_pct", "0.5")
        yield database


def open_signal(db, market, **kw):
    fields = dict(intent="open", should_act=True, confidence=0.9, symbol="BTC", side="long",
                  entry_low=100.0, entry_high=100.0, stop_loss=95.0, take_profit=110.0)
    fields.update(kw)
    msg = db.insert_message("m-" + str(len(db.list_orders())), CH, "kol", "BTC long")
    return PaperExecutor(db, RiskEngine(db), CH, market=market).open_with_sl_tp(IntentResult(**fields), msg, 1)


def test_entry_risks_one_percent_of_paper_equity(db):
    market = FixedMarket({"BTC-USDT-SWAP": 100.0})
    result = open_signal(db, market)  # mark inside the entry range -> market entry
    plan = result["plan"]
    assert result["ok"] and plan["exchange"] == "paper" and plan["ord_type"] == "market"
    # 1R = 1% of 1000 = 10 USDT; stop distance 5 -> 2 units -> 200 USDT notional
    assert plan["sizing"] == "fixed_risk" and plan["risk_usdt"] == pytest.approx(10.0)
    assert plan["notional_usdt"] == pytest.approx(200.0) and not plan["capped_by_leverage"]
    [pos] = PaperBook(db).open_positions(CH)
    assert pos["entry_px"] == 100.0 and pos["sl"] == 95.0 and pos["fees"] == pytest.approx(200 * FEE_RATE)
    [row] = db.list_orders()
    assert row["okx_ord_id"].startswith("paper-") and row["status"] == "live"


def test_tight_stop_is_capped_by_leverage(db):
    plan = open_signal(db, FixedMarket({"BTC-USDT-SWAP": 100.0}), stop_loss=99.99)["plan"]
    # 1% risk would need 100000 notional; 10x on 1000 equity allows 9800
    assert plan["capped_by_leverage"] and plan["notional_usdt"] == pytest.approx(9800.0)
    assert plan["risk_usdt"] < 10.0


def test_signal_without_stop_is_not_opened(db):
    result = open_signal(db, FixedMarket({"BTC-USDT-SWAP": 100.0}), stop_loss=None)
    assert not result["ok"] and result["skipped"] and "止损" in result["error"]
    assert db.list_orders() == [] and PaperBook(db).open_positions(CH) == []


def test_limit_entry_waits_then_fills_when_crossed(db):
    market = FixedMarket({"BTC-USDT-SWAP": 101.0})
    open_signal(db, market, entry_low=100.0, entry_high=100.0)
    book = PaperBook(db)
    assert len(book.pending_orders(CH)) == 1 and not book.open_positions(CH)
    market.prices["BTC-USDT-SWAP"] = 99.9
    match_once(book, market)
    assert not book.pending_orders(CH)
    assert book.open_positions(CH)[0]["entry_px"] == 100.0


def test_stop_loss_closes_at_observed_price_and_reports_r(db):
    market = FixedMarket({"BTC-USDT-SWAP": 100.0})
    open_signal(db, market)
    book = PaperBook(db)
    market.prices["BTC-USDT-SWAP"] = 94.0  # gapped through the 95 stop
    events = match_once(book, market)
    assert events[0]["kind"] == "stop_loss" and events[0]["px"] == 94.0
    [closed] = book.closed_positions()
    assert closed["close_reason"] == "stop_loss"
    # 1.2R loss from the 1-point-wider gap, plus fees
    assert book.r_multiple(closed) == pytest.approx(-(6 * 2 + closed["fees"]) / (5 * 2), rel=1e-6)


def test_take_profit_fills_at_target(db):
    market = FixedMarket({"BTC-USDT-SWAP": 100.0})
    open_signal(db, market)
    market.prices["BTC-USDT-SWAP"] = 111.0
    match_once(PaperBook(db), market)
    [closed] = PaperBook(db).closed_positions()
    assert closed["close_reason"] == "take_profit" and closed["exit_px"] == 110.0


def test_liquidation_caps_the_loss_at_the_margin(db):
    market = FixedMarket({"BTC-USDT-SWAP": 100.0})
    open_signal(db, market, stop_loss=50.0)  # stop beyond liquidation: qty 10/50 = 0.2
    book = PaperBook(db)
    market.prices["BTC-USDT-SWAP"] = 80.0  # 10x long: liquidation at 90
    match_once(book, market)
    [closed] = book.closed_positions()
    assert closed["close_reason"] == "liquidation" and closed["exit_px"] == pytest.approx(90.0)
    assert closed["realized_pnl"] == pytest.approx(-2.0)  # notional 20 / 10x = the whole margin


def test_management_messages_act_on_the_paper_position(db):
    market = FixedMarket({"BTC-USDT-SWAP": 100.0})
    open_signal(db, market)
    client = PaperClient(db, CH, market=market)
    assert client.update_stop_loss("BTC-USDT-SWAP", 100.0)["ok"]
    assert client.get_open_triggers()[0]["slTriggerPx"] == "100.0"
    market.prices["BTC-USDT-SWAP"] = 104.0
    half = client.close_position("BTC-USDT-SWAP", 50)
    assert half["ok"] and half["closed_size"] == pytest.approx(1.0)
    assert float(client.get_positions()[0]["pos"]) == pytest.approx(1.0)
    assert client.close_position("ETH-USDT-SWAP")["skipped"]
    assert client.close_position("BTC-USDT-SWAP")["ok"]
    assert client.get_positions() == []


def test_accounts_are_isolated_per_channel(db):
    market = FixedMarket({"BTC-USDT-SWAP": 100.0})
    open_signal(db, market)
    other = PaperClient(db, "1226095564073205780", market=market)
    assert other.get_positions() == []
    assert other.get_balance_usdt() == pytest.approx(1000.0)


def test_unlisted_contract_is_reported_not_faked(db):
    result = open_signal(db, FixedMarket({}))
    assert not result["ok"] and "公开行情" in result["error"]


def test_router_paper_route_trades_while_global_dry_run_stays_on(db, monkeypatch):
    from yoyo.copier.router.action_router import ActionRouter

    async def silent(*args, **kwargs):
        return {"ok": True}

    for name in ("notify_trade_pipeline", "notify_signal_detected", "notify_trade_update"):
        monkeypatch.setattr(f"yoyo.copier.router.action_router.{name}", silent)
    monkeypatch.setattr("yoyo.copier.paper.client._MARKET", FixedMarket({"BTC-USDT-SWAP": 78100.0}))
    db.set_setting("dry_run", "true")
    db.set_setting("max_entry_deviation_pct", "0")
    text = "飞扬合约策略\n具体产品：BTC\n进行方向：做空\n进场点位：78000-78300\n止损点位：80000\n止盈点位：74631"
    msg = db.insert_message("paper-1", CH, "比特币飞扬", text)
    asyncio.run(ActionRouter(db).process_message(msg, text, "比特币飞扬"))
    assert db.get_message(msg)["status"] == "executed"
    [order] = db.list_orders()
    assert order["okx_ord_id"].startswith("paper-")
    assert PaperBook(db).open_positions(CH)[0]["side"] == "short"


class BarMarket(FixedMarket):
    def __init__(self, prices, bars):
        super().__init__(prices)
        self.bars = bars

    def candles_1m(self, inst_id, venue, limit=5, now_ms=None):
        return list(self.bars.get(inst_id, []))


def _opened_bar(db):
    from yoyo.copier.paper.book import first_full_bar_ms
    return first_full_bar_ms(PaperBook(db).open_positions(CH)[0]["opened_at"])


def test_wick_through_the_stop_between_polls_is_caught_by_the_1m_bar(db):
    market = BarMarket({"BTC-USDT-SWAP": 100.0}, {})
    open_signal(db, market)
    start = _opened_bar(db)
    market.bars["BTC-USDT-SWAP"] = [(start, 100.0, 101.0, 94.5, 99.0)]  # wick to 94.5, back to 99
    match_once(PaperBook(db), market)
    [closed] = PaperBook(db).closed_positions()
    assert closed["close_reason"] == "stop_loss" and closed["exit_px"] == 95.0


def test_stop_and_target_in_the_same_bar_resolve_as_the_stop(db):
    market = BarMarket({"BTC-USDT-SWAP": 100.0}, {})
    open_signal(db, market)
    market.bars["BTC-USDT-SWAP"] = [(_opened_bar(db), 100.0, 111.0, 94.0, 100.0)]
    match_once(PaperBook(db), market)
    assert PaperBook(db).closed_positions()[0]["close_reason"] == "stop_loss"


def test_bars_before_the_position_existed_are_ignored_and_never_replayed(db):
    market = BarMarket({"BTC-USDT-SWAP": 100.0}, {})
    open_signal(db, market)
    start = _opened_bar(db)
    market.bars["BTC-USDT-SWAP"] = [(start - 60_000, 100.0, 100.0, 50.0, 100.0)]  # pre-entry crash
    match_once(PaperBook(db), market)
    assert PaperBook(db).open_positions(CH)
    market.bars["BTC-USDT-SWAP"] = [(start, 100.0, 112.0, 99.0, 100.0)]
    match_once(PaperBook(db), market)
    match_once(PaperBook(db), market)  # same bar again: cursor stops a second close attempt
    [closed] = PaperBook(db).closed_positions()
    assert closed["close_reason"] == "take_profit" and closed["exit_px"] == 110.0


def test_r_stats_report_one_r_and_streaks(db):
    from yoyo.copier.api.routes.paper import r_stats, risk_usdt

    market = FixedMarket({"BTC-USDT-SWAP": 100.0})
    book = PaperBook(db)
    for exit_px in (95.0, 94.0, 110.0):  # loss, loss, win
        market.prices["BTC-USDT-SWAP"] = 100.0
        open_signal(db, market)
        pos = book.open_positions(CH)[0]
        book.close(pos["id"], 100.0, exit_px, "manual_close")
    closed = book.closed_positions()
    first, last = min(closed, key=lambda p: p["id"]), max(closed, key=lambda p: p["id"])
    assert risk_usdt(first) == pytest.approx(10.0, rel=1e-6)  # 1% of 1000
    # 1R tracks realized equity, so it shrinks after the two losses
    assert risk_usdt(last) < risk_usdt(first)
    s = r_stats(book, closed, 1000.0)
    assert s["trades"] == 3 and s["max_losing_streak"] == 2
    assert s["win_rate"] == pytest.approx(1 / 3)
    assert s["buckets"]["≤-1R"] == 2 and s["buckets"]["≥5R"] == 0
    assert s["avg_risk_pct"] == pytest.approx(0.01, rel=0.05)


def test_legacy_margin_trades_keep_their_r_but_leave_the_average_one_r(db):
    from yoyo.copier.api.routes.paper import r_stats

    market = FixedMarket({"BTC-USDT-SWAP": 100.0})
    book = PaperBook(db)
    for exit_px in (95.0, 110.0):
        market.prices["BTC-USDT-SWAP"] = 100.0
        open_signal(db, market)
        pos = book.open_positions(CH)[0]
        book.close(pos["id"], 100.0, exit_px, "manual_close")
    closed = book.closed_positions()
    sizing = book.sizing_by_message()
    assert set(sizing.values()) == {"fixed_risk"}
    legacy = max(closed, key=lambda p: p["id"])
    sizing[legacy["message_id"]] = "legacy_margin"
    with_legacy, without = r_stats(book, closed, 1000.0, sizing), r_stats(book, closed, 1000.0)
    assert with_legacy["trades"] == without["trades"] == 2 and with_legacy["sum_r"] == without["sum_r"]
    assert with_legacy["legacy_trades"] == 1 and without["legacy_trades"] == 0
    oldest = min(closed, key=lambda p: p["id"])
    assert with_legacy["avg_risk_usdt"] == pytest.approx(abs(oldest["entry_px"] - oldest["initial_sl"]) * oldest["qty"])


def test_tracker_percent_of_original_closes_the_rest_after_tp1(db):
    market = FixedMarket({"BTC-USDT-SWAP": 100.0})
    open_signal(db, market)  # 2 units
    client = PaperClient(db, CH, market=market)
    assert client.close_original_pct("BTC-USDT-SWAP", 25)["closed_size"] == pytest.approx(0.5)
    rest = client.close_original_pct("BTC-USDT-SWAP", 75)  # 75% of the original = all that is left
    assert rest["ok"] and rest["closed_size"] == pytest.approx(1.5)
    assert client.get_positions() == []


def test_close_without_position_cancels_the_stale_entry(db, monkeypatch):
    from yoyo.copier.router.action_router import ActionRouter

    async def silent(*args, **kwargs):
        return {"ok": True}

    for name in ("notify_trade_pipeline", "notify_signal_detected", "notify_trade_update"):
        monkeypatch.setattr(f"yoyo.copier.router.action_router.{name}", silent)
    market = FixedMarket({"BTC-USDT-SWAP": 103.0})
    monkeypatch.setattr("yoyo.copier.paper.client._MARKET", market)
    open_signal(db, market, entry_note="limit")  # mark above the range -> resting limit
    assert PaperBook(db).pending_orders(CH, "BTC-USDT-SWAP")
    text = "BTC: Closed BE (100%) • Total R/R: 0.00R"
    msg = db.insert_message("paper-close", CH, "kol", text)
    asyncio.run(ActionRouter(db).process_message(msg, text, "kol"))
    assert PaperBook(db).pending_orders(CH, "BTC-USDT-SWAP") == []
