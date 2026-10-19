"""Isolated, GET-only manual account view, including exchange order history.

Reuse signal-center serializers and cache invalidation, never its runtime or
risk configuration. Exchange history is bounded to 100 orders in 7 days;
failure to read history must not erase valid positions and available margin.
"""
from contextlib import closing
from decimal import Decimal
import json
import sqlite3
import time

from .client import Client
from .config import DEFAULT_MANUAL_RUNTIME
from .dashboard import Dashboard, account_snapshot, number, order_row, token, timestamp, trade_row, ORDER_TYPES
from .manual_queue import ReadLedger
from .manual_readiness import entry_status
from .dashboard import worker_lock
from .preview_reads import PooledOpener, PreviewClient, PreviewReads, ReadPacer


def read_requests(runtime):
    """No schema initialization, chmod, queue transition or trade authorization."""
    path = runtime / "manual.sqlite3"
    if not path.exists():
        return []
    if path.is_symlink():
        raise ValueError("unsafe manual queue")
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=.2)) as db:
        return [json.loads(r[0]) for r in db.execute(
            "SELECT value FROM requests WHERE state IN ('confirmed','finished') ORDER BY rowid DESC LIMIT 50")]


def executions(runtime):
    try:
        rows = ReadLedger(runtime).trades() if (runtime / "execution.sqlite3").exists() else []
        trades = {r["id"]: r for r in rows}
        result = []
        for draft in read_requests(runtime):
            event, plan = draft["event"], draft["plan"]
            trade = trades.get(draft["id"])
            row = trade_row(trade or dict(plan, event=event, state="prepared"))
            row.update(id=token(draft["id"]), state=trade["state"] if trade else "queued",
                       confirmed_ms=timestamp(event.get("confirmed_ms")), rr=event.get("rr"),
                       initial_stop=number(event.get("initial_stop")),
                       requested_size=number(plan.get("size")),
                       reference_price=number(plan.get("price")))
            if trade and trade.get("reason") in {"manual preview expired or policy invalid", "signal expired before leverage setup", "signal or quote expired before entry"}:
                row["reason_code"] = "manual_expired"
            elif trade and trade.get("reason_code") == "manual_repreview_required" and not row["reason_code"]:
                row["reason_code"] = "manual_repreview_required"
            if row.get("take_profit") is None:
                row["take_profit"] = number(plan.get("take_profit"))
            row["protection_verified"] = bool(trade and trade["state"] == "active"
                and trade.get("tp_basis") == "actual_fill"
                and 0 <= int(time.time()*1000)-trade.get("protection_checked_ms", 0) < 60_000)
            result.append(row)
        public_rows = [trade_row(r) for r in rows if r.get("event", {}).get("source") == "owner_manual_yolo"]
        closed = [r for r in public_rows if r["state"] == "closed"]
        settled = [r for r in closed if r["realized_net_usdt"] is not None]
        active_items = []
        for trade in rows:
            if trade["state"] in ("closed", "rejected", "skipped"):
                continue
            row = trade_row(trade)
            row.update(id=token(trade["id"]), rr=trade.get("event", {}).get("rr"),
                       protection_verified=bool(trade["state"] == "active"
                           and trade.get("tp_basis") == "actual_fill"
                           and 0 <= int(time.time()*1000)-trade.get("protection_checked_ms", 0) < 60_000))
            active_items.append(row)
        statistics = dict(scope="all_manual_trades", filled=sum(bool(r["entry_price"]) for r in public_rows),
                          closed=len(closed), settled=len(settled), unattributed=len(closed)-len(settled),
                          realized_net_usdt=str(sum((Decimal(r["realized_net_usdt"]) for r in settled), Decimal(0))) if settled else None,
                          wins=sum(Decimal(r["realized_net_usdt"]) > 0 for r in settled))
        return dict(status="ready", items=result, display_limit=50, statistics=statistics, active_items=active_items,
                    owned_lots=[{k: token(r.get(k)) if k != "size" else number(r.get(k))
                                 for k in ("symbol", "side", "state", "size")} for r in rows
                                if r["state"] not in ("closed", "rejected", "skipped")])
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
        return dict(status="unavailable", items=[], display_limit=50)


def manual_snapshot(client):
    reads = PreviewReads(client, lambda: int(time.time()*1000))
    try:
        for path in ("/api/v5/account/config", "/api/v5/account/balance", "/api/v5/account/positions"):
            reads.submit(path)
        reads.submit("/api/v5/trade/orders-pending", limit="100")
        for kind in ORDER_TYPES:
            reads.submit("/api/v5/trade/orders-algo-pending", ordType=kind, limit="100")
        history = reads.submit("/api/v5/trade/orders-history", instType="SWAP", limit="100")
        result = account_snapshot(reads, position_limit=None)
        receipt = reads.get("/api/v5/account/balance")[0]
        usdt = next(r for r in receipt["details"] if r["ccy"] == "USDT")
        total, eq, usd = (number(v) for v in (receipt.get("totalEq"), usdt.get("eq"), usdt.get("eqUsd")))
        result["total_equity_usdt"] = (str(Decimal(total)*Decimal(eq)/Decimal(usd))
            if total is not None and eq is not None and usd is not None and Decimal(usd) > 0 else None)
        try:
            raw = history.result()
            items = []
            for r in raw:
                row = order_row(r, "ordinary")
                row.update({k:number(r.get(k)) for k in ("avgPx", "pnl", "fee")})
                row.update(feeCcy=token(r.get("feeCcy")), order_id=token(r.get("ordId")),
                           updated_ms=timestamp(r.get("uTime")))
                items.append(row)
            items.sort(key=lambda r:r.get("updated_ms") or r.get("created_ms") or 0, reverse=True)
            result["history"] = dict(status="ready", items=items, days=7, display_limit=100,
                                     truncated=len(raw) >= 100)
        except Exception:
            result["history"] = dict(status="unavailable", items=[], days=7, display_limit=100)
        return result
    finally:
        reads.close()


class ManualDashboard(Dashboard):
    def __init__(self, runtime=DEFAULT_MANUAL_RUNTIME, client_factory=None):
        self.opener, self.pacer = PooledOpener(), ReadPacer()
        def factory(config, credentials, *, writes):
            assert writes is False
            return PreviewClient(Client(config, credentials, writes=False), self.opener, self.pacer)
        super().__init__(runtime, client_factory or factory, snapshot_reader=manual_snapshot, refresh_interval_ms=10_000)

    def snapshot(self, force=False, *, summary=False, workspace=False):
        result = super().snapshot(force)
        try:
            result["entry_status"] = entry_status(ReadLedger(self.runtime), int(time.time()*1000), worker_lock(self.runtime))
        except (OSError, sqlite3.Error, ValueError, TypeError):
            result["entry_status"] = dict(status="blocked", code="unavailable", title="执行状态暂不可用",
                                        message="请稍后重试，当前不会提交订单。", issues=[])
        if summary:
            # A display projection only: preserve source freshness and entry
            # readiness; never cache or reuse this as an execution quote.
            account = result["account"]
            data = account.get("data")
            return dict(account_scope="yolo_manual", view="summary", entry_status=result["entry_status"],
                        account={**account, "data": None if data is None else
                                 {"total_equity_usdt": data.get("total_equity_usdt")}})
        if workspace:
            # One read model for the results desk. It includes every current
            # position and active intent, but no exchange history or raw ledger.
            account, activity = result["account"], executions(self.runtime)
            data = account.get("data")
            activity = {**activity, "items": activity["items"][:5], "display_limit": 5}
            return dict(account_scope="yolo_manual", view="workspace", entry_status=result["entry_status"],
                        executions=activity, account={**account, "data": None if data is None else
                            {key: data.get(key) for key in ("total_equity_usdt", "balance", "positions",
                                "position_count", "order_count", "protection_count")}})
        result.update(account_scope="yolo_manual", label="均线密集专用账户",
            config=dict(risk_fraction="0.03", risk_basis="total_equity", order_type="market",
                        margin_mode="cross", leverage_policy="maximum_valid_tier", confirmation_required=True,
                        stop_policy="ma_envelope_5m", rr_options=[3, 5]),
            executions=executions(self.runtime))
        return result

    def close(self):
        # Wait for in-flight GETs before closing the shared connection pool.
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.opener.close()
