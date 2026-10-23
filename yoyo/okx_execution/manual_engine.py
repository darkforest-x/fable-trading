"""Manual YOLO lots use a separate account, ledger, queue and locked worker.

The local queue carries one explicit owner confirmation, not a detector signal.
Attached OCO protects the initial fill; TP is reconciled to actual-fill R once.
Unknown writes are GET-reconciled, never replayed. SPIKE trailing/opposite
logic is intentionally inapplicable to these fixed-exit manual lots.
"""
from __future__ import annotations

from copy import copy
from yoyo.contracts.signal_universe import exclusion_reason
from .client import APIError
from .engine import Engine, TERMINAL
from .manual import manual, prepare, take_profit
from .risk import D
from .manual_inventory import inspect, policy

MANUAL_ENGINE_REVISION = "manual-flat-reconcile-20261017-v1"


class ManualEngine(Engine):
    def __init__(self, *args, queue=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = queue
        self.commands = None
        if queue is not None:
            from .manual_commands import Commands
            self.commands = Commands(queue.path.parent)

    def entry_exclusion(self, event):
        # The owner scoped bottom-20 filtering to SPIKE, not manual MA orders.
        if manual(event) or manual(event.get("event") or {}):
            return exclusion_reason(event["symbol"])
        return super().entry_exclusion(event)

    def tick(self, *, entries=True):
        # Existing position protection runs before discretionary entry work.
        result = self._strategy_tick(entries=entries)
        if self.queue and policy(self.store):
            from .free_manual import REVISION, Tickets, consume
            from .free_management import Management, REVISION as MANAGEMENT_REVISION, consume as manage
            self.store.set_meta('free_management_revision', MANAGEMENT_REVISION)
            manage(self, Management(self.queue.path.parent))
            self.store.set_meta('free_manual_revision', REVISION)
            consume(self, Tickets(self.queue.path.parent), entries=entries and not self.store.meta('errors'))
            self.store.set_meta('heartbeat_ms', self.clock())
        return result

    def _strategy_tick(self, *, entries=True):
        if self.commands is not None:
            from .manual_commands import consume
            self.store.set_meta("management_revision", "owner-management-20261018-v1")
            consume(self, self.commands)
        # A manually confirmed queue can be inspected locally in milliseconds.
        # Do not put 8 remote account reads in front of the next confirmation
        # while there is no owned position or intent to reconcile.
        if self.queue and policy(self.store):
            rows = self.store.trades()
            seen = {row["id"] for row in rows}
            pending = any(event["id"] not in seen for event in self.events()) if entries else False
            if not pending and all(row["state"] in TERMINAL for row in rows):
                self.store.set_meta("heartbeat_ms", self.clock())
                self.store.set_meta("entries_enabled", bool(entries))
                self.store.set_meta("accepting_entries", False)
                self.store.set_meta("errors", [])
                return
        return super().tick(entries=entries)

    def account(self):
        from .manual_client import ManualClient
        if isinstance(self.client, ManualClient):
            with self.client.read_scope([("/api/v5/account/"+p, {}) for p in ("config", "balance", "positions")]) as reads:
                local = copy(self)
                local.client = reads
                account = Engine.account(local)
        else:
            account = super().account()
        rules = policy(self.store)
        if rules and account["uid"] != rules["account_uid"]:
            raise ValueError("manual account policy belongs to a different account")
        return account

    def inspect_inventory(self, positions, **kwargs):
        from .manual_client import ManualClient
        if not isinstance(self.client, ManualClient):
            return inspect(self, positions, **kwargs)
        requests = [("/api/v5/trade/orders-pending", {"limit": "100"})]
        requests += [("/api/v5/trade/orders-algo-pending", dict(ordType=k, limit="100"))
                     for k in ("conditional", "oco", "trigger", "move_order_stop")]
        with self.client.read_scope(requests) as reads:
            local = copy(self)
            local.client = reads
            return inspect(local, positions, **kwargs)

    def owned(self, account):
        if not policy(self.store):
            return super().owned(account)
        account["manual_external_symbols"] = self.inspect_inventory(account["positions"], entry=True)
        return account["manual_external_symbols"]

    def assert_owned_quantity(self, positions=None):
        if not policy(self.store):
            return super().assert_owned_quantity(positions)
        positions = positions if positions is not None else self.account()["positions"]
        self.inspect_inventory(positions)

    def event_valid(self, event):
        if not manual(event):
            return super().event_valid(event)
        now = self.clock()
        return (type(event.get("preview_ms")) is int and type(event.get("expires_ms")) is int
                and event["preview_ms"] <= now < event["expires_ms"]
                and event["expires_ms"]-event["preview_ms"] <= 60_000)

    def events(self):
        if self.queue:
            for event in self.queue.events():
                confirmed = event.get("confirmed_ms")
                if (type(confirmed) is int and event["preview_ms"] <= confirmed < event["expires_ms"]
                        and confirmed <= self.clock()):
                    yield event
        if not policy(self.store):
            yield from super().events()

    def prepare(self, event, account):
        if policy(self.store):
            if not manual(event):
                raise ValueError("this account requires individual manual confirmation")
            external = account.get("manual_external_symbols")
            if external is None:
                external = self.inspect_inventory(account["positions"], entry=True, preparing=event["id"])
            if event["symbol"] in external:
                raise ValueError("symbol has owner-managed positions or orders")
        return prepare(self, event, account) if manual(event) else super().prepare(event, account)

    def preparation_failed(self, event, exc, previous=None):
        if not manual(event):
            return super().preparation_failed(event, exc, previous)
        from .preparation import assert_no_entry_intent
        assert_no_entry_intent(previous or {})
        self.store.save(dict(id=event["id"], event=event, symbol=event["symbol"], side=event["side"],
                             timeframe=event["timeframe"], state="skipped", created_ms=self.clock(),
                             reason=str(exc), reason_code="manual_repreview_required"))

    def enter(self, trade):
        if manual(trade["event"]):
            # Even an internal caller cannot turn a preview into an order.
            saved = self.queue.get(trade["id"]) if self.queue else {}
            if saved.get("queue_state") != "confirmed" or saved.get("event") != trade["event"]:
                raise ValueError("manual order lacks matching owner confirmation")
        if policy(self.store):
            if not manual(trade["event"]):
                raise ValueError("this account requires individual manual confirmation")
            # Recheck before leverage, which is shared across the instrument.
            if trade["symbol"] in self.inspect_inventory(self.account()["positions"], entry=True):
                raise ValueError("symbol has owner-managed positions or orders")
        return super().enter(trade)

    def attachment(self, trade):
        result = super().attachment(trade)
        if manual(trade["event"]):
            result.update(tpTriggerPx=trade["take_profit"], tpOrdPx="-1", tpTriggerPxType="last")
        return result

    def verify_stop(self, trade, algo):
        super().verify_stop(trade, algo)
        if manual(trade["event"]):
            if (algo.get("ordType") != "oco" or algo.get("tpOrdPx") != "-1"
                    or algo.get("tpTriggerPxType") != "last"
                    or D(algo.get("tpTriggerPx")) != D(trade["take_profit"])):
                raise ValueError("attached take-profit ownership/price mismatch")

    def algo(self, trade):
        algo = super().algo(trade)
        if manual(trade.get("event", {})) and (
                algo.get("instId") != trade["symbol"] or algo.get("tdMode") != self.config.margin_mode
                or algo.get("algoClOrdId") != trade["algo_client_id"] or algo.get("posSide") != trade["side"]
                or algo.get("side") != ("sell" if trade["side"] == "long" else "buy")):
            raise ValueError("manual protection receipt belongs to another order")
        return algo

    def protect(self, trade):
        if not manual(trade["event"]):
            return super().protect(trade)
        super().protect(trade)
        if trade["state"] != "active":
            return
        try:
            target = take_profit(trade["entry_price"], trade["initial_stop"], trade["side"], trade["rr"], trade["tick"])
        except ValueError:
            trade.update(state="exit_needed", reason="actual-fill take profit is invalid")
            self.store.save(trade)
            return
        trade["actual_risk"] = str(abs(D(trade["entry_price"])-D(trade["initial_stop"]))*D(trade["ctval"])*D(trade["size"]))
        if D(target) == D(trade["take_profit"]):
            trade.update(tp_basis="actual_fill", protection_checked_ms=self.clock())
            self.store.save(trade)
            return
        self.assert_owned_quantity()
        trade.update(state="manual_tp_pending", tp_target=target, tp_amend_ms=self.clock())
        self.store.save(trade)
        # cxlOnFail=false keeps original protection if amendment is rejected.
        # A transport timeout leaves pending state for GET-only reconciliation.
        try:
            trade["tp_amend_ack"] = self.client.post("/api/v5/trade/amend-algos",
                dict(instId=trade["symbol"], algoId=trade["algo_id"], newTpTriggerPx=target,
                     newTpOrdPx="-1", cxlOnFail=False))
        except APIError:
            trade.update(state="exit_needed", reason="actual-fill take profit could not be installed")
        self.store.save(trade)

    def manage(self, trade):
        if trade["state"] == "manual_owner_stop_pending":
            from .manual_commands import reconcile_stop
            return reconcile_stop(self, trade)
        if not manual(trade.get("event", {})):
            return super().manage(trade)
        if trade["state"] == "exit_needed":
            from .manual_reconciliation import external_flattening
            evidence = external_flattening(self, trade)
            if evidence:
                trade.update(state="closed", exit_reason="external_flattened",
                             external_close=evidence, reconciled_ms=self.clock())
                self.store.save(trade)
                return
        if trade["state"] == "manual_tp_pending":
            algo = self.algo(trade)
            if algo.get("state") == "effective":
                trade.update(state="stop_executing")
            elif algo.get("state") == "live":
                # Never POST an unknown amendment again. Validate both exits
                # before accepting the new target or retaining old protection.
                try:
                    accepted = D(algo.get("tpTriggerPx")) == D(trade["tp_target"])
                    candidate = dict(trade, take_profit=trade["tp_target"]) if accepted else trade
                    self.verify_stop(candidate, algo)
                except ValueError:
                    trade.update(state="exit_needed", reason="owned manual TP/SL no longer valid")
                else:
                    if accepted:
                        trade.update(take_profit=trade["tp_target"], tp_basis="actual_fill", state="active",
                                     stop_receipt=algo, protection_checked_ms=self.clock())
                    elif self.clock()-trade["tp_amend_ms"] < 10_000:
                        return
                    else:
                        trade.update(state="exit_needed", reason="actual-fill take profit remains unconfirmed")
            elif algo.get("state") in ("canceled", "order_failed"):
                trade.update(state="exit_needed", reason="owned manual TP/SL no longer valid")
            else:
                raise ValueError("manual protection state unresolved")
            self.store.save(trade)
            return
        if trade["state"] == "active":
            if trade.get("tp_basis") != "actual_fill":
                return self.protect(trade)
            algo = self.algo(trade)
            if algo.get("state") == "effective":
                trade.update(state="stop_executing")
            else:
                try:
                    self.verify_stop(trade, algo)
                    trade.update(protection_checked_ms=self.clock(), stop_receipt=algo)
                    self.store.save(trade)
                except ValueError:
                    trade.update(state="exit_needed", reason="owned manual TP/SL no longer valid")
                    self.store.save(trade)
                # Fixed exits: never call source.bars/advance for this lot.
                return
            self.store.save(trade)
            return
        return super().manage(trade)

    def reconcile_exit(self, trade, *, stopped=False):
        if manual(trade.get("event", {})) and stopped:
            algo = self.algo(trade)
            actual = algo.get("actualSide")
            if actual not in ("tp", "sl"):
                raise ValueError("manual OCO trigger side unresolved")
            super().reconcile_exit(trade, stopped=True)
            if trade["state"] == "closed":
                trade["exit_reason"] = "exchange_take_profit" if actual == "tp" else "exchange_stop"
                self.store.save(trade)
            return
        return super().reconcile_exit(trade, stopped=stopped)
