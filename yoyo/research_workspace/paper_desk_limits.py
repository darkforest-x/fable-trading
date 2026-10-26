"""Persistent local limit orders for the manual paper desk (Notion task 54).

Marketable limits execute immediately at public bid/ask as takers. Resting limits
require a subsequent fresh snapshot and execute at their limit as makers. A buy
touches when ask <= limit; a sell when bid >= limit. Queue depth is not modelled.
Reservations, cancellation and fills share the ledger's SQLite write transaction.
Optional entry-attached TP/SL triggers close remaining exposure at observed
exit bid/ask, cancelling sibling close limits in the same transaction. The
independent worker never participates in the forward scan or real execution.
"""
from __future__ import annotations

import logging
import json
import threading
import uuid

from .paper_desk import (clock_ms, fresh, leverage_value, number, positive, optional_price, stop_prices,
                         limit_fee, MAKER_FEE, TAKER_FEE)

logger = logging.getLogger(__name__)


class LimitOrders:
    def __init__(self, desk):
        self.desk = desk

    def symbols(self):
        with self.desk.connect() as db:
            orders = {r[0] for r in db.execute("SELECT symbol FROM limit_orders WHERE status='pending'")}
            protected = {r["symbol"] for r in db.execute(
                "SELECT symbol,remaining FROM positions WHERE CAST(remaining AS NUMERIC)>0")
                if number(r["remaining"]) > 0}
            return sorted(orders | protected)

    def place(self, action, target_id, quantity, limit_price, quote_id, request_id,
              marks=None, side=None, leverage=1, note="", take_profit=None, stop_loss=None,
              *, bulk_identity=None):
        qty, limit = positive(quantity), positive(limit_price)
        if action not in {"open", "close"}:
            raise ValueError("挂单操作无效")
        payload = dict(action="limit_" + action, target_id=target_id, quantity=str(qty),
                       limit_price=str(limit), quote_id=quote_id)
        if action == "open":
            leverage = leverage_value(leverage)
            if side not in {"long", "short"}:
                raise ValueError("方向无效")
            payload.update(side=side, leverage=leverage, note=note,
                           take_profit=optional_price(take_profit), stop_loss=optional_price(stop_loss))
        if bulk_identity is not None:
            payload["bulk_identity"] = dict(bulk_identity)
        with self.desk.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self.desk.replay(db, request_id, payload)
            if previous:
                return previous
            at = clock_ms()
            quote = self.desk.row(db, "quotes", quote_id)
            from yoyo.contracts.monday_order import check_quote
            if action == 'open':
                check_quote(quote, side, limit, at)
            if not fresh(quote, at):
                raise ValueError("报价已过期，请重新获取后确认挂单")
            if bulk_identity is not None:
                if action != "open":
                    raise ValueError("周一批量功能只允许开仓限价委托")
                self._check_bulk_identity(db, target_id, quote, bulk_identity, at)
                existing = db.execute(
                    "SELECT * FROM limit_orders WHERE account_id=? AND bulk_kind='monday_1618' "
                    "AND bulk_week=? AND symbol=? AND side=?",
                    (target_id, bulk_identity["week"], quote["symbol"], side)).fetchone()
                if existing:
                    return self.desk.receipt(db, request_id, payload, dict(order=dict(existing)))
            position = None
            stops = dict(take_profit=None, stop_loss=None)
            if action == "open":
                rate = limit_fee(side, quote, limit, action)
                if json.loads(quote.get('risk_json') or '{}').get('monday_bound') and rate != MAKER_FEE:
                    raise ValueError('1.618 已触及盘口，请重新预览挂单')
                if bulk_identity is not None and rate != MAKER_FEE:
                    raise ValueError("1.618 限价已触及盘口，拒绝转为市价成交")
                px = number(quote['ask'] if side == 'long' else quote['bid']) if rate == TAKER_FEE else limit
                stops = stop_prices(side, px, take_profit, stop_loss)
                account = self.desk.check_open(db, target_id, quote["symbol"], qty, px, leverage, marks or {}, at,
                                               fee_rate=rate, entry_quote=quote,
                                               bulk_authorized=bulk_identity is not None, side=side)
            else:
                position = self.desk.row(db, "positions", target_id)
                if position["symbol"] != quote["symbol"]:
                    raise ValueError("报价与持仓不匹配")
                self.desk.check_close(db, position, qty)
                account = self.desk.row(db, "accounts", position["account_id"])
                side, leverage = position["side"], position["leverage"]
                rate = limit_fee(side, quote, limit, action)
            if bulk_identity is None and len(self.desk.pending_orders(db, account["id"])) >= 50:
                raise ValueError("每个账户最多同时保留 50 笔挂单")
            order = dict(id=uuid.uuid4().hex, account_id=account["id"], action=action,
                         symbol=quote["symbol"], side=side, quantity=str(qty), limit_price=str(limit),
                         leverage=leverage, note=note, position_id=position["id"] if position else None,
                         status="pending", created_ms=at, updated_ms=at, quote_id=quote_id,
                         trigger_quote_id=None, fill_id=None, reason="等待提交后的新鲜报价触价", fee_rate=str(rate),
                         bulk_kind="monday_1618" if bulk_identity is not None else None,
                         bulk_week=bulk_identity.get("week") if bulk_identity is not None else None,
                         bulk_generation=bulk_identity.get("generation") if bulk_identity is not None else None,
                         bulk_expires_ms=bulk_identity.get("expires_ms") if bulk_identity is not None else None,
                         bulk_level=bulk_identity.get("level") if bulk_identity is not None else None, **stops)
            db.execute("INSERT INTO limit_orders(id,account_id,action,symbol,side,quantity,limit_price,leverage,note,position_id,"
                       "status,created_ms,updated_ms,quote_id,trigger_quote_id,fill_id,reason,take_profit,stop_loss,fee_rate,"
                       "bulk_kind,bulk_week,bulk_generation,bulk_expires_ms,bulk_level) "
                       "VALUES(:id,:account_id,:action,:symbol,:side,:quantity,:limit_price,"
                       ":leverage,:note,:position_id,:status,:created_ms,:updated_ms,:quote_id,:trigger_quote_id,:fill_id,:reason,:take_profit,:stop_loss,:fee_rate,"
                       ":bulk_kind,:bulk_week,:bulk_generation,:bulk_expires_ms,:bulk_level)", order)
            if rate == TAKER_FEE:
                if action == 'open':
                    result = self.desk.open_fill(db, account['id'], side, qty, quote, note, at, marks or {}, leverage,
                                                exclude_order=order['id'], take_profit=take_profit, stop_loss=stop_loss)
                else:
                    result = self.desk.close_fill(db, position, qty, quote, at, exclude_order=order['id'])
                order.update(status='filled', reason='委托价已穿过盘口，按买卖报价立即成交（Taker）',
                             trigger_quote_id=quote_id, position_id=result['position']['id'], fill_id=result['fill']['id'])
                db.execute('UPDATE limit_orders SET status=:status,reason=:reason,trigger_quote_id=:trigger_quote_id,'
                           'position_id=:position_id,fill_id=:fill_id WHERE id=:id', order)
            return self.desk.receipt(db, request_id, payload, dict(order=order))

    @staticmethod
    def _check_bulk_identity(db, account_id, quote, identity, at):
        required = {"week", "generation", "expires_ms", "level"}
        if set(identity) != required or type(identity.get("generation")) is not int or type(identity.get("expires_ms")) is not int:
            raise ValueError("批量委托身份无效")
        if identity["expires_ms"] <= at:
            raise ValueError("本周 1.618 委托已过期")
        from yoyo.monitor.monday_fib import week_window
        window = week_window(at)
        if (identity['week'] != window['week'] or identity['expires_ms'] != window['expires']
                or at < window['end']):
            raise ValueError('周一批量委托的周区间无效')
        config = db.execute("SELECT * FROM monday_bulk_configs WHERE account_id=?", (account_id,)).fetchone()
        if not config or not config["enabled"] or config["generation"] != identity["generation"]:
            raise ValueError("周一批量设置已停用或已更新")
        if config["armed_week"] and config["armed_week"] != identity["week"]:
            raise ValueError("一次性设置不属于当前周")
        try:
            tag = json.loads(quote.get("risk_json") or "{}").get("monday_bulk")
        except (TypeError, ValueError):
            tag = None
        expected = dict(account_id=account_id, week=identity["week"], generation=identity["generation"])
        if tag != expected:
            raise ValueError("报价与周一批量账户或代次不匹配")

    def cancel(self, order_id, request_id):
        payload = dict(action="cancel_limit", order_id=order_id)
        with self.desk.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = self.desk.replay(db, request_id, payload)
            if previous:
                return previous
            order = self.desk.row(db, "limit_orders", order_id)
            if order["status"] == "filled":
                raise ValueError("挂单已经成交，无法撤销，请查看持仓")
            if order["status"] == "pending":
                order.update(status="cancelled", updated_ms=clock_ms(), reason="已手动撤销")
                db.execute("UPDATE limit_orders SET status=:status,updated_ms=:updated_ms,reason=:reason WHERE id=:id", order)
            return self.desk.receipt(db, request_id, payload, dict(order=order))

    def match(self, marks):
        """Use only post-submission snapshots; missing/stale observations never imply a touch."""
        matched = []
        with self.desk.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # This gate runs before any fill processing. A restart, stop request or
            # week rollover can never turn an old bulk order into a paper fill.
            at = clock_ms()
            expired = db.execute("SELECT * FROM limit_orders WHERE status='pending' AND bulk_kind='monday_1618'").fetchall()
            for pending in expired:
                config = db.execute("SELECT enabled,generation,armed_week FROM monday_bulk_configs WHERE account_id=?",
                                    (pending["account_id"],)).fetchone()
                stale = (pending["bulk_expires_ms"] is None or pending["bulk_expires_ms"] <= at or
                         not config or not config["enabled"] or config["generation"] != pending["bulk_generation"] or
                         (config["armed_week"] and config["armed_week"] != pending["bulk_week"]))
                if stale:
                    reason = "周一批量设置已停用、更新或过期"
                    db.execute("UPDATE limit_orders SET status='cancelled',updated_ms=?,reason=? WHERE id=? AND status='pending'",
                               (at, reason, pending["id"]))
            from .paper_desk_risk import liquidate_accounts
            liquidate_accounts(self.desk, db, marks, clock_ms())
            # Protective exits own the remaining position before pending close
            # limits can consume it. Both decisions use this same write lock.
            self.match_stops(db, marks)
            orders = [dict(r) for r in db.execute("SELECT * FROM limit_orders WHERE status='pending' ORDER BY created_ms,id")]
            for order in orders:
                at = clock_ms()
                if order.get('bulk_kind') == 'monday_1618':
                    # Recheck time immediately before this order can consume a mark.
                    current = db.execute("SELECT enabled,generation,armed_week FROM monday_bulk_configs WHERE account_id=?",
                                         (order['account_id'],)).fetchone()
                    if (order.get('bulk_expires_ms') is None or order['bulk_expires_ms'] <= at or not current
                            or not current['enabled'] or current['generation'] != order['bulk_generation']
                            or (current['armed_week'] and current['armed_week'] != order['bulk_week'])):
                        db.execute("UPDATE limit_orders SET status='cancelled',updated_ms=?,reason=? WHERE id=? AND status='pending'",
                                   (at, "周一批量设置已停用、更新或过期", order['id']))
                        continue
                mark = marks.get(order["symbol"])
                if (not mark or not fresh(mark, at) or mark["source_ms"] <= order["created_ms"]
                        or mark["received_ms"] <= order["created_ms"]):
                    continue
                bid, ask = positive(mark["bid"]), positive(mark["ask"])
                if mark["symbol"] != order["symbol"] or bid > ask:
                    continue
                buy = (order["action"] == "open") == (order["side"] == "long")
                limit = number(order["limit_price"])
                if (buy and ask > limit) or (not buy and bid < limit):
                    continue
                if order["action"] == "open":
                    account = self.desk.row(db, "accounts", order["account_id"])
                    positions = [dict(r) for r in db.execute("SELECT * FROM positions WHERE account_id=? AND CAST(remaining AS NUMERIC)>0", (order["account_id"],))]
                    if not self.desk.totals(account, positions, marks, at)["marks_complete"]:
                        db.execute("UPDATE limit_orders SET reason=? WHERE id=?", ("触价，等待其他持仓的新鲜行情核算资金", order["id"]))
                        continue
                from .paper_desk import quick_quote
                original = self.desk.row(db, 'quotes', order['quote_id'])
                if quick_quote(original):
                    mark = dict(mark, risk={**mark.get('risk', {}), 'monday_quick': True, 'monday_order_type':'limit'})
                bulk_identity = None
                if order.get('bulk_kind') == 'monday_1618':
                    bulk_identity = dict(week=order['bulk_week'], generation=order['bulk_generation'],
                                         expires_ms=order['bulk_expires_ms'], level=order.get('bulk_level'))
                    tag = dict(account_id=order['account_id'], week=order['bulk_week'],
                               generation=order['bulk_generation'])
                    mark = dict(mark, risk={**mark.get('risk', {}), 'monday_bulk': tag,
                                            'monday_order_type': 'limit'})
                quote = self.desk.record_quote(db, mark)
                order["trigger_quote_id"] = quote["id"]
                try:
                    if order["action"] == "open":
                        result = self.desk.open_fill(db, order["account_id"], order["side"], number(order["quantity"]),
                                                     quote, order["note"], at, marks, order["leverage"], limit, order["id"],
                                                     order["take_profit"], order["stop_loss"], fee_rate=number(order.get('fee_rate') or MAKER_FEE),
                                                     bulk_identity=bulk_identity)
                    else:
                        position = self.desk.row(db, "positions", order["position_id"])
                        result = self.desk.close_fill(db, position, number(order["quantity"]), quote, at, limit, order["id"], fee_rate=number(order.get('fee_rate') or MAKER_FEE))
                except ValueError as error:
                    order.update(status="rejected", reason=str(error), updated_ms=at)
                else:
                    order.update(status="filled", reason="后续新鲜买卖报价触价，按委托价模拟全额成交", updated_ms=at,
                                 position_id=result["position"]["id"], fill_id=result["fill"]["id"])
                    matched.append(order["id"])
                db.execute("UPDATE limit_orders SET status=:status,reason=:reason,updated_ms=:updated_ms,"
                           "trigger_quote_id=:trigger_quote_id,position_id=:position_id,fill_id=:fill_id WHERE id=:id", order)
        return matched

    def match_stops(self, db, marks):
        """Close remaining exposure at observed exit bid/ask, never at an ideal trigger price.

        Only quotes observed after entry or the last amendment are eligible. Price gaps are retained.
        Protective exits cancel sibling close limits atomically; a completed
        position makes both protective legs inactive, including after restart.
        """
        positions = [dict(r) for r in db.execute(
            "SELECT * FROM positions WHERE CAST(remaining AS NUMERIC)>0 AND (take_profit IS NOT NULL OR stop_loss IS NOT NULL) ORDER BY opened_ms,id")]
        for p in positions:
            if number(p["remaining"]) <= 0:
                continue
            at = clock_ms()
            mark = marks.get(p["symbol"])
            active_after = max(p["opened_ms"], p.get("protection_updated_ms") or 0, p.get('exposure_updated_ms') or 0)
            if (not mark or not fresh(mark, at) or mark["source_ms"] <= active_after
                    or mark["received_ms"] <= active_after or mark["symbol"] != p["symbol"]):
                continue
            bid, ask = positive(mark["bid"]), positive(mark["ask"])
            if bid > ask:
                continue
            px = bid if p["side"] == "long" else ask
            sign = 1 if p["side"] == "long" else -1
            reason = None
            if p["stop_loss"] is not None and (px - number(p["stop_loss"])) * sign <= 0:
                reason = "stop_loss"
            elif p["take_profit"] is not None and (px - number(p["take_profit"])) * sign >= 0:
                reason = "take_profit"
            if reason is None:
                continue
            quote = self.desk.record_quote(db, mark)
            label = "止损" if reason == "stop_loss" else "止盈"
            db.execute("UPDATE limit_orders SET status='cancelled',updated_ms=?,reason=? "
                       "WHERE position_id=? AND action='close' AND status='pending'",
                       (at, label + "自动平仓，已撤销同仓位平仓挂单", p["id"]))
            self.desk.close_fill(db, p, number(p["remaining"]), quote, at, exit_reason=reason)


class LimitOrderWorker:
    """Poll pending orders or protected positions, independent of browser lifetime."""
    interval_seconds = 5

    def __init__(self, limits, quotes):
        self.limits, self.quotes = limits, quotes
        self.stop_event = threading.Event()
        self.thread = None
        self.last_check_ms = None
        self.last_error = None

    def tick(self):
        symbols = self.limits.symbols()
        marks = {}
        if symbols:
            requested = sorted(set(symbols) | set(self.limits.desk.symbols()))
            marks = self.quotes.get(requested)
            self.limits.match(marks)
            missing = {s for s in requested if s not in marks or not fresh(marks[s], clock_ms())}
            self.last_error = "部分合约暂无新鲜行情，挂单和止盈止损等待报价" if missing else None
            if not self.limits.desk.risk_complete(marks):
                self.last_error = '标记价或维持保证金档位暂不可用，强平检查暂停'
        else:
            self.last_error = None
        self.last_check_ms = clock_ms()
        from .paper_desk_analytics import sample_equity
        sample_equity(self.limits.desk, marks)

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Manual paper limit order polling failed")
                self.last_error = "自动检查失败，等待重试；挂单和止盈止损暂未触发"
            self.stop_event.wait(self.interval_seconds)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, name="manual-paper-limit-orders", daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=12)

    def status(self):
        return dict(running=bool(self.thread and self.thread.is_alive()), interval_seconds=self.interval_seconds,
                    last_check_ms=self.last_check_ms, error=self.last_error)
