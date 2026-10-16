"""Explicitly activated isolated hedge-mode execution with per-signal lots.

API contracts: https://www.okx.com/docs-v5/en/ (Trade and Trading Account).
Each timeframe owns its order and attached SL. Exchange positions aggregate
same-side lots, so exchange quantities must reconcile to this ledger before
new entries. An unknown order/SL/exit result blocks new entries but never
turns a failed account read into 'flat'. No blind POST retry or cancel-all.
"""
from __future__ import annotations

import time

from yoyo.monitor import FRESH_MS
from .client import APIError, TransportError
from .risk import D, amount, plan, advance
from .store import identifier

TERMINAL = {"closed", "rejected", "skipped"}


def one(rows):
    if len(rows) != 1:
        raise ValueError("expected one exchange record")
    return rows[0]


class Engine:
    def __init__(self, config, client, source, store, clock=None):
        self.config, self.client, self.source, self.store = config, client, source, store
        self.clock = clock or (lambda: int(time.time() * 1000))

    def account(self):
        settings = one(self.client.get("/api/v5/account/config"))
        if settings.get("posMode") != "long_short_mode" or settings.get("acctLv") != "2":
            raise ValueError("use futures account mode and long/short position mode; settings are not changed automatically")
        balance = one(self.client.get("/api/v5/account/balance", ccy="USDT"))
        usdt = one([r for r in balance.get("details", []) if r.get("ccy") == "USDT"])
        positions = [r for r in self.client.get("/api/v5/account/positions") if D(r["pos"]) != 0]
        return dict(equity=amount(usdt["eq"]), available=amount(usdt["availBal"]), positions=positions,
                    uid=settings.get("uid"), account_mode=settings["acctLv"], position_mode=settings["posMode"])

    def owned(self, account):
        expected = {}
        for trade in self.store.trades():
            if trade["state"] in TERMINAL:
                continue
            if trade["state"] != "active":
                raise ValueError("unresolved execution intent; reconcile before new entries")
            key = (trade["symbol"], trade["side"])
            expected[key] = expected.get(key, D(0)) + D(trade["size"])
        actual = {}
        for position in account["positions"]:
            if position.get("mgnMode") != "isolated" or position.get("posSide") not in {"long", "short"}:
                raise ValueError("unmanaged account position")
            key = (position["instId"], position["posSide"])
            actual[key] = actual.get(key, D(0)) + abs(D(position["pos"]))
        if actual != expected:
            raise ValueError("exchange positions do not match owned signal lots")
        # Pending ordinary orders must not be invisible collateral/position users.
        if self.client.get("/api/v5/trade/orders-pending"):
            raise ValueError("account has unresolved ordinary orders")
        known = {t.get("algo_id") for t in self.store.trades() if t["state"] == "active"}
        for kind in ("conditional", "oco", "trigger", "move_order_stop"):
            rows = self.client.get("/api/v5/trade/orders-algo-pending", ordType=kind, limit="100")
            # Pagination required even on small balances: there is no position-count cap.
            while rows:
                if any(r.get("algoId") not in known for r in rows):
                    raise ValueError("account has an unmanaged pending algo")
                if len(rows) < 100:
                    break
                rows = self.client.get("/api/v5/trade/orders-algo-pending", ordType=kind,
                                       limit="100", after=rows[-1]["algoId"])
        return actual

    def preflight(self):
        server = int(one(self.client.get("/api/v5/public/time"))["ts"])
        if abs(server - self.clock()) > 5000:
            raise ValueError("local and exchange clocks differ by more than five seconds")
        account = self.account()
        self.owned(account)
        if D(account["available"]) <= 0:
            raise ValueError("no available USDT")
        saved_uid = self.store.meta("account_uid")
        if not account["uid"] or saved_uid not in (None, account["uid"]):
            raise ValueError("execution ledger belongs to a different account")
        return account

    def assert_owned_quantity(self):
        """Do not cancel/close another writer's inventory after an account edit.

        Stop fills may temporarily make this conservative check fail; their
        independent GET reconciliation runs before a later mutation attempt.
        """
        expected = {}
        for row in self.store.trades():
            if row["state"] in TERMINAL or row["state"] == "leverage_pending":
                continue
            if row["state"] in {"entry_pending", "cancel_entry_pending"}:
                raise ValueError("entry fill must reconcile before position mutation")
            key = row["symbol"], row["side"]
            expected[key] = expected.get(key, D(0)) + D(row["size"])
        actual = {}
        for row in self.client.get("/api/v5/account/positions"):
            if D(row["pos"]) == 0:
                continue
            if row.get("mgnMode") != "isolated":
                raise ValueError("unmanaged account position")
            key = row["instId"], row["posSide"]
            actual[key] = actual.get(key, D(0)) + abs(D(row["pos"]))
        if actual != expected:
            raise ValueError("position ownership changed; no discretionary mutation sent")

    def prepare(self, event, account):
        now = self.clock()
        if not 0 <= now - event["bar_close_ms"] <= FRESH_MS:
            raise ValueError("signal is outside the existing 30-minute freshness window")
        self.source.checkpoint(event["symbol"], event["timeframe"], now)
        instrument = one(self.client.get("/api/v5/account/instruments", instType="SWAP", instId=event["symbol"]))
        ticker = one(self.client.get("/api/v5/market/ticker", instId=event["symbol"]))
        if not 0 <= now - int(ticker["ts"]) <= 5000:
            raise ValueError("exchange quote is stale")
        tiers = self.client.get("/api/v5/public/position-tiers", instType="SWAP", tdMode="isolated",
                                instFamily=instrument["instFamily"])
        existing = sum((abs(D(p["pos"])) for p in account["positions"]
                        if p["instId"] == event["symbol"] and p["posSide"] == event["side"]), D(0))
        allocated = sum((D(p["margin"]) for p in account["positions"]), D(0))
        result = plan(event, instrument, ticker, tiers, equity=account["equity"], available=account["available"],
                      allocated_margin=allocated, existing_size=existing, config=self.config)
        return dict(result, id=event["id"], event=event, initial_stop=result["stop"],
                    entry_id=identifier(event["id"], "entry"), algo_client_id=identifier(event["id"], "stop"),
                    created_ms=now, quote_ms=int(ticker["ts"]), state="prepared")

    def enter(self, trade):
        # Persist before even the leverage mutation. No concurrent account writer.
        trade["state"] = "leverage_pending"
        self.store.save(trade)
        self.client.post("/api/v5/account/set-leverage", dict(instId=trade["symbol"],
                         mgnMode="isolated", posSide=trade["side"], lever=trade["leverage"]))
        info = self.client.get("/api/v5/account/leverage-info", instId=trade["symbol"], mgnMode="isolated")
        chosen = one([r for r in info if r.get("posSide") == trade["side"]])
        if D(chosen["lever"]) != D(trade["leverage"]):
            raise ValueError("exchange leverage readback differs")
        if ("event" in trade and not 0 <= self.clock() - trade["event"]["bar_close_ms"] <= FRESH_MS
                or "quote_ms" in trade and not 0 <= self.clock() - trade["quote_ms"] <= 5000):
            trade.update(state="skipped", reason="signal/quote expired during leverage setup")
            self.store.save(trade)
            return
        request = dict(instId=trade["symbol"], tdMode="isolated", posSide=trade["side"],
                       side="buy" if trade["side"] == "long" else "sell", ordType="market",
                       sz=trade["size"], clOrdId=trade["entry_id"],
                       attachAlgoOrds=[dict(attachAlgoClOrdId=trade["algo_client_id"],
                                           slTriggerPx=trade["initial_stop"], slOrdPx="-1", slTriggerPxType="last")])
        trade.update(state="entry_pending", entry_request=request)
        self.store.save(trade)
        try:
            trade["entry_ack"] = one(self.client.post("/api/v5/trade/order", request))
        except APIError as exc:
            trade.update(state="rejected", reason=str(exc))
        self.store.save(trade)

    def order(self, trade, *, entry=False, ord_id=None):
        params = {"instId": trade["symbol"]}
        if ord_id:
            params["ordId"] = ord_id
        else:
            params["clOrdId"] = trade["entry_id" if entry else "exit_id"]
        return one(self.client.get("/api/v5/trade/order", **params))

    def algo(self, trade):
        return one(self.client.get("/api/v5/trade/order-algo", algoClOrdId=trade["algo_client_id"]))

    def verify_stop(self, trade, algo):
        if (algo.get("state") != "live" or algo.get("instId") != trade["symbol"]
                or algo.get("tdMode") != "isolated" or algo.get("algoClOrdId") != trade["algo_client_id"]
                or algo.get("posSide") != trade["side"]
                or algo.get("side") != ("sell" if trade["side"] == "long" else "buy")
                or D(algo["sz"]) != D(trade["size"]) or algo.get("slOrdPx") != "-1"
                or algo.get("slTriggerPxType") != "last" or D(algo["slTriggerPx"]) != D(trade["stop"])):
            raise ValueError("attached stop ownership/size/price mismatch")

    def reconcile_entry(self, trade):
        order = self.order(trade, entry=True)
        if (order.get("instId") != trade["symbol"] or order.get("posSide") != trade["side"]
                or order.get("clOrdId") != trade["entry_id"]
                or order.get("side") != ("buy" if trade["side"] == "long" else "sell")):
            raise ValueError("entry receipt ownership mismatch")
        trade["entry_order"] = order
        state, filled = order["state"], D(order["accFillSz"])
        if state in {"canceled", "mmp_canceled"} and filled == 0:
            trade.update(state="rejected", reason="exchange entry canceled without fill")
        elif state == "filled" and filled == D(trade["size"]):
            trade.update(state="protecting", entry_price=order["avgPx"],
                         entry_ms=int(order.get("fillTime") or order["uTime"]),
                         last_bar_ms=int(order.get("fillTime") or order["uTime"]), trailing=False)
        elif state in {"live", "partially_filled"}:
            # Market orders should finish promptly. Do not resend or abandon partial exposure.
            if self.clock() - trade["created_ms"] > 10_000:
                trade["state"] = "cancel_entry_pending"
                self.store.save(trade)
                self.client.post("/api/v5/trade/cancel-order", dict(instId=trade["symbol"], clOrdId=trade["entry_id"]))
        elif state in {"canceled", "mmp_canceled"} and filled > 0:
            trade.update(size=amount(filled), entry_price=order["avgPx"], state="exit_needed",
                         reason="partial entry: close owned fill")
        else:
            raise ValueError("unexpected entry state")
        self.store.save(trade)

    def protect(self, trade):
        try:
            algo = self.algo(trade)
            if algo.get("state") == "effective":
                trade.update(algo_id=algo["algoId"], state="stop_executing")
                self.store.save(trade)
                return
            self.verify_stop(trade, algo)
            side = 1 if trade["side"] == "long" else -1
            if side * (D(trade["entry_price"]) - D(trade["initial_stop"])) <= 0:
                raise ValueError("actual fill crossed initial stop")
        except (APIError, ValueError):
            # An attached SL can be delayed briefly after the fill; never fabricate protection.
            if self.clock() - trade.get("entry_ms", trade["created_ms"]) < 10_000:
                return
            trade.update(state="exit_needed", reason="attached protection unavailable or invalid")
            self.store.save(trade)
            return
        trade.update(algo_id=algo["algoId"], state="active", stop_receipt=algo)
        self.store.save(trade)

    def exit(self, trade):
        """Cancel only this lot's SL, confirm terminal state, then close its qty.

        Hedge-mode sell+long / buy+short is the venue's close-only instruction;
        reduceOnly is a net-mode field and is intentionally not sent.
        """
        try:
            algo = self.algo(trade)
        except APIError as exc:
            # 51603 is not evidence of no protection unless this is an entry
            # failure which never acquired an acknowledged algo ID.
            if exc.code != "51603" or trade.get("algo_id"):
                raise
            algo = {"state": "canceled"}
        if algo["state"] == "effective":
            trade["state"] = "stop_executing"
            self.store.save(trade)
            return
        self.assert_owned_quantity()
        if algo["state"] == "live":
            trade.update(state="cancel_stop_pending", algo_id=algo["algoId"])
            self.store.save(trade)
            self.client.post("/api/v5/trade/cancel-algos", [dict(instId=trade["symbol"], algoId=algo["algoId"])])
            return
        if algo["state"] not in {"canceled", "order_failed"}:
            raise ValueError("stop cancellation is unresolved")
        trade["exit_id"] = identifier(trade["id"], "exit")
        request = dict(instId=trade["symbol"], tdMode="isolated", posSide=trade["side"],
                       side="sell" if trade["side"] == "long" else "buy", ordType="market",
                       sz=trade["size"], clOrdId=trade["exit_id"])
        trade.update(state="exit_pending", exit_request=request)
        self.store.save(trade)
        trade["exit_ack"] = one(self.client.post("/api/v5/trade/order", request))
        self.store.save(trade)

    def reconcile_exit(self, trade, *, stopped=False):
        if stopped:
            algo = self.algo(trade)
            order_id = algo.get("ordId")
            # OKX also exposes ordIdList; only one full-lot stop order is expected.
            if not order_id:
                ids = algo.get("ordIdList") or []
                if len(ids) != 1:
                    raise ValueError("triggered stop order identity unresolved")
                order_id = ids[0]
            order = self.order(trade, ord_id=order_id)
        else:
            order = self.order(trade)
        if (order.get("instId") != trade["symbol"] or order.get("posSide") != trade["side"]
                or order.get("side") != ("sell" if trade["side"] == "long" else "buy")):
            raise ValueError("exit receipt ownership mismatch")
        trade["exit_order"] = order
        if order["state"] == "filled" and D(order["accFillSz"]) == D(trade["size"]):
            trade.update(state="closed", exit_reason="exchange_stop" if stopped else trade.get("reason", "raw_opposite"),
                         exit_price=order["avgPx"], exit_ms=int(order.get("fillTime") or order["uTime"]))
        elif order["state"] not in {"live", "partially_filled"}:
            raise ValueError("exit incomplete: reconcile residual position manually")
        self.store.save(trade)

    def manage(self, trade):
        state = trade["state"]
        if state == "leverage_pending":
            trade.update(state="skipped", reason="interrupted before entry intent; no order sent")
            self.store.save(trade)
        elif state in {"entry_pending", "cancel_entry_pending"}:
            self.reconcile_entry(trade)
        elif state == "protecting":
            self.protect(trade)
        elif state in {"exit_needed", "cancel_stop_pending"}:
            self.exit(trade)
        elif state in {"exit_pending", "stop_executing"}:
            self.reconcile_exit(trade, stopped=state == "stop_executing")
        elif state == "amend_pending":
            algo = self.algo(trade)
            if algo["state"] == "effective":
                trade["state"] = "stop_executing"
            elif algo["state"] == "live" and D(algo["slTriggerPx"]) == D(trade["amend"]["stop"]):
                trade.update(stop=trade["amend"]["stop"], trailing=trade["amend"]["trailing"],
                             last_bar_ms=trade["amend"]["close_ms"], state="active")
            else:
                raise ValueError("SL amendment unresolved; old exchange protection retained")
            self.store.save(trade)
        elif state == "active":
            algo = self.algo(trade)
            if algo["state"] == "effective":
                trade["state"] = "stop_executing"
                self.store.save(trade)
                return
            try:
                self.verify_stop(trade, algo)
            except ValueError:
                trade.update(state="exit_needed", reason="owned protection no longer valid")
                self.store.save(trade)
                return
            for bar in self.source.bars(trade["symbol"], trade["timeframe"], trade["last_bar_ms"], self.clock()):
                change = advance(trade, bar)
                if change["exit"]:
                    trade.update(state="exit_needed", reason="raw_opposite", exit_signal_ms=bar["close_ms"])
                    self.store.save(trade)
                    return
                if D(change["stop"]) != D(trade["stop"]):
                    self.assert_owned_quantity()
                    trade.update(state="amend_pending", amend=change)
                    self.store.save(trade)
                    self.client.post("/api/v5/trade/amend-algos", dict(instId=trade["symbol"], algoId=trade["algo_id"],
                                     newSlTriggerPx=change["stop"], newSlOrdPx="-1", cxlOnFail=False))
                    return
                trade.update(trailing=change["trailing"], last_bar_ms=bar["close_ms"])
                self.store.save(trade)

    def tick(self, *, entries=True):
        failures = []
        for trade in self.store.trades():
            if trade["state"] in TERMINAL:
                continue
            try:
                self.manage(trade)
            except (APIError, TransportError, ValueError, KeyError) as exc:
                failures.append(dict(id=trade["id"], error=str(exc)))
        # Management continues for other lots during errors. New entries require
        # a complete account reconciliation, not a guessed 'empty' response.
        self.store.set_meta("heartbeat_ms", self.clock())
        self.store.set_meta("errors", failures)
        if failures or not entries:
            return
        account = self.account()
        self.owned(account)
        seen = {t["id"] for t in self.store.trades()}
        for event in self.source.events(self.store.meta("cutoff_ms"), self.clock()):
            if event["id"] in seen:
                continue
            try:
                trade = self.prepare(event, account)
            except (ValueError, APIError) as exc:
                self.store.save(dict(id=event["id"], state="skipped", event=event, reason=str(exc)))
                continue
            self.enter(trade)
            # Reconcile before processing the next signal; this is sequencing,
            # not a simultaneous-position ceiling. All periods remain eligible.
            return
