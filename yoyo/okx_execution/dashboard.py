"""Read-only account snapshots for the signal center (Task 59, 2026-10-16).

OKX V5 account/config, balance, positions and pending-order fields follow
https://www.okx.com/docs-v5/en/. Connectivity, worker liveness and execution
receipts are separate facts. Reference signal returns are never account PnL.
No execution-engine imports, database initialization or exchange POSTs here.
Owner enabled remote read-only account display on 2026-10-16, including the
existing login-free public gateway. Credentials and execution remain private.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal, InvalidOperation
import fcntl
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

from fastapi import HTTPException, Request
from yoyo.contracts.signal_universe import public_policy

from .client import APIError, Client
from .config import Config, DEFAULT_RUNTIME, read_private

STATES = frozenset("prepared retry_pending leverage_pending entry_pending cancel_entry_pending protecting active manual_tp_pending exit_needed cancel_stop_pending exit_pending stop_executing amend_pending closed rejected skipped".split())
ORDER_TYPES = ("conditional", "oco", "trigger", "move_order_stop")
REASON_CODES = frozenset({"quote_stale", "checkpoint_unavailable", "read_unavailable", "tier_changed",
                          "signal_expired", "preparation_rejected", "insufficient_margin", "minimum_size",
                          "stop_crossed", "symbol_excluded", "interrupted_before_entry"})
LEGACY_REASONS = {
    "exchange quote is stale": "quote_stale",
    "monitor checkpoint unavailable or stale": "checkpoint_unavailable",
    "latest completed candle is unavailable": "checkpoint_unavailable",
    "signal is outside the existing 30-minute freshness window": "signal_expired",
    "insufficient available allocation for this signal": "insufficient_margin",
    "cross collateral changed during leverage setup": "insufficient_margin",
    "risk-sized quantity is below exchange minimum": "minimum_size",
    "market has passed the initial stop": "stop_crossed",
    "symbol excluded by owner entry universe": "symbol_excluded",
    "interrupted before entry intent; no order sent": "interrupted_before_entry",
}


def number(value):
    try:
        parsed = Decimal(str(value))
        return str(parsed) if parsed.is_finite() and abs(parsed) < Decimal("1e30") else None
    except (InvalidOperation, ValueError):
        return None


def token(value):
    value = str(value or "")
    return value if re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value) else None


def timestamp(value):
    try:
        parsed = int(value)
        return parsed if 0 < parsed < 100_000_000_000_000 else None
    except (ValueError, TypeError):
        return None


def worker_lock(runtime):
    path = runtime / "worker.lock"
    if not path.exists():
        return False
    if path.is_symlink():
        raise ValueError("unsafe lock")
    with path.open("r") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(stream, fcntl.LOCK_UN)
        return False


def trade_row(trade):
    event = trade.get("event") if isinstance(trade.get("event"), dict) else {}
    result = {key: number(trade.get(key)) for key in (
        "size", "entry_price", "exit_price", "stop", "take_profit", "leverage", "planned_risk", "target_risk")}
    result["manual"] = event.get("source") == "owner_manual_yolo"
    result.update({key: token(trade.get(key) or event.get(key)) for key in ("symbol", "timeframe", "side", "exit_reason")})
    result.update({key: timestamp(trade.get(key)) for key in ("created_ms", "entry_ms", "exit_ms", "attempted_ms", "next_retry_ms")})
    result["signal_ms"] = timestamp(event.get("bar_close_ms"))
    # Public account views receive enumerated reasons, never raw exception text.
    code = LEGACY_REASONS.get(trade.get("reason"), trade.get("reason_code"))
    result["reason_code"] = code if code in REASON_CODES else None
    result["quote_age_ms"] = number(trade.get("quote_age_ms"))
    attempts = trade.get("attempts")
    result["attempts"] = attempts if type(attempts) is int and 0 <= attempts <= 100_000 else None
    result["state"] = trade.get("state") if trade.get("state") in STATES else "unknown"
    # Only complete, USDT-denominated exchange receipts support net PnL.
    entry, exit_order = trade.get("entry_order") or {}, trade.get("exit_order") or {}
    values = [number(exit_order.get("pnl")), number(entry.get("fee")), number(exit_order.get("fee"))]
    result["realized_net_usdt"] = None
    if (result["state"] == "closed" and all(v is not None for v in values)
            and entry.get("feeCcy") == exit_order.get("feeCcy") == "USDT"):
        result["realized_net_usdt"] = str(sum(Decimal(v) for v in values))
    return result


def ledger_snapshot(runtime, now_ms):
    """Read a consistent ledger without constructing Store or creating files."""
    try:
        held = worker_lock(runtime)
        path = runtime / "execution.sqlite3"
        meta, rows, counts = {}, [], {}
        if path.exists():
            if path.is_symlink():
                raise ValueError("unsafe ledger")
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.2)) as db:
                db.execute("BEGIN")
                meta = {key: json.loads(value) for key, value in db.execute(
                    "SELECT key,value FROM meta WHERE key IN ('heartbeat_ms','cutoff_ms','errors','worker_error','entries_enabled','accepting_entries')")}
                counts = {state: count for state, count in db.execute(
                    "SELECT json_extract(value,'$.state'),count(*) FROM trades GROUP BY 1")}
                rows = [trade_row(json.loads(value)) for (value,) in db.execute(
                    "SELECT value FROM trades ORDER BY rowid DESC LIMIT 50")]
        heartbeat = timestamp(meta.get("heartbeat_ms"))
        age = max(0, now_ms - heartbeat) if heartbeat else None
        status = "not_started" if not meta.get("cutoff_ms") and not counts else "stopped"
        if held:
            status = "running" if age is not None and age < 60_000 else "unresponsive"
        errors = meta.get("errors") or meta.get("worker_error")
        return {"status": "ready", "worker": {"status": status, "heartbeat_ms": heartbeat,
                "error_present": bool(errors),
                "entries_enabled": meta.get("entries_enabled") if status == "running" else None,
                "accepting_entries": meta.get("accepting_entries") if status == "running" and not errors else None},
                "total": sum(counts.values()), "closed": counts.get("closed", 0),
                "waiting": counts.get("retry_pending", 0),
                "active": sum(v for k, v in counts.items() if k not in {"closed", "rejected", "skipped", "retry_pending"}),
                "items": rows, "display_limit": 50}
    except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
        return {"status": "unavailable", "worker": {"status": "unknown"}, "items": [],
                "total": None, "closed": None, "active": None, "display_limit": 50}


def pending(client, path, id_field, **query):
    """Bound display work; explicitly report truncation rather than false totals."""
    rows, seen, after = [], set(), None
    for _ in range(5):
        page = client.get(path, **query, limit="100", **({"after": after} if after else {}))
        for row in page:
            identity = row.get(id_field)
            if not identity or identity in seen:
                raise ValueError("inconsistent pagination")
            seen.add(identity)
            rows.append(row)
        if len(page) < 100:
            return rows, False
        after = str(page[-1][id_field])
    return rows, True


def order_row(row, kind):
    result = {key: token(row.get(key)) for key in ("instId", "side", "posSide", "ordType", "state")}
    result.update({key: number(row.get(key)) for key in ("sz", "px", "accFillSz", "slTriggerPx", "tpTriggerPx", "triggerPx")})
    result.update(kind=kind, created_ms=timestamp(row.get("cTime")))
    return result


def account_snapshot(client, *, position_limit=50):
    settings = client.get("/api/v5/account/config")
    balance = client.get("/api/v5/account/balance", ccy="USDT")
    if len(settings) != 1 or len(balance) != 1:
        raise ValueError("missing account")
    details = [row for row in balance[0].get("details", []) if row.get("ccy") == "USDT"]
    if len(details) != 1:
        raise ValueError("missing USDT balance")
    cash = {key: number(details[0].get(key)) for key in ("eq", "cashBal", "availBal", "availEq", "frozenBal", "upl", "imr", "mgnRatio")}
    if cash["eq"] is None or cash["availEq"] is None:
        raise ValueError("incomplete balance")
    positions = []
    for row in client.get("/api/v5/account/positions"):
        size = number(row.get("pos"))
        if size is None:
            raise ValueError("invalid position")
        if Decimal(size) == 0:
            continue
        item = {key: token(row.get(key)) for key in ("instId", "posSide", "mgnMode", "ccy")}
        item.update({key: number(row.get(key)) for key in ("pos", "avgPx", "markPx", "upl", "lever", "liqPx", "margin")})
        positions.append(item)
    ordinary, truncated = pending(client, "/api/v5/trade/orders-pending", "ordId")
    algos = []
    for kind in ORDER_TYPES:
        batch, more = pending(client, "/api/v5/trade/orders-algo-pending", "algoId", ordType=kind)
        algos.extend(batch)
        truncated |= more
    perms = set(str(settings[0].get("perm", "")).split(","))
    return {"account_level": token(settings[0].get("acctLv")),
            "position_mode": token(settings[0].get("posMode")), "trade_permission": "trade" in perms,
            "balance": cash, "positions": positions[:position_limit], "position_count": len(positions),
            "orders": [order_row(r, "ordinary") for r in ordinary[:50]],
            "protection_orders": [order_row(r, "algo") for r in algos[:50]],
            "order_count": len(ordinary), "protection_count": len(algos),
            "orders_truncated": truncated, "display_limit": 50}


class Dashboard:
    """Demand-driven, cached GET-only reader independent of the signal pulse."""

    def __init__(self, runtime=DEFAULT_RUNTIME, client_factory=Client, *, snapshot_reader=account_snapshot,
                 refresh_interval_ms=30_000):
        self.runtime, self.client_factory = Path(runtime).absolute(), client_factory
        self.snapshot_reader, self.refresh_interval_ms = snapshot_reader, refresh_interval_ms
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="okx-dashboard")
        self.lock = threading.RLock()
        self.future = None
        self.fingerprint = None
        self.account = None
        self.updated_ms = self.attempt_ms = 0
        self.error = None

    def _refresh(self, config, fingerprint):
        started_ms = int(time.time() * 1000)
        result, error = None, None
        try:
            credentials = read_private(self.runtime / "credentials.json")
            result = self.snapshot_reader(self.client_factory(config, credentials, writes=False))
        except APIError as exc:
            error = "okx_" + exc.code if re.fullmatch(r"\d{1,8}", exc.code) else "account_unavailable"
        except Exception:
            # Raw errors can contain URLs, account identifiers or credentials.
            error = "account_unavailable"
        with self.lock:
            if fingerprint == self.fingerprint:
                self.error = error
                if result is not None:
                    self.account, self.updated_ms = result, started_ms

    def snapshot(self, force=False):
        now_ms = int(time.time() * 1000)
        config, configured, error, fingerprint = None, False, None, None
        try:
            config = Config(**read_private(self.runtime / "config.json"))
            config.validate()
            paths = [self.runtime / name for name in ("config.json", "credentials.json")]
            configured = paths[1].is_file() and not paths[1].is_symlink() and not paths[1].stat().st_mode & 0o077
            fingerprint = tuple((p.stat().st_ino, p.stat().st_mtime_ns, p.stat().st_size) for p in paths) if configured else None
        except FileNotFoundError:
            error = "not_configured"
        except (OSError, ValueError, TypeError):
            error = "configuration_unavailable"
        with self.lock:
            if fingerprint != self.fingerprint:
                self.account, self.updated_ms, self.attempt_ms, self.error = None, 0, 0, None
                self.fingerprint = fingerprint
            busy = self.future is not None and not self.future.done()
            if configured and not error and not busy and now_ms - self.attempt_ms >= (2_000 if force else self.refresh_interval_ms):
                self.attempt_ms = now_ms
                self.future = self.pool.submit(self._refresh, config, fingerprint)
                busy = True
            age = max(0, now_ms - self.updated_ms) if self.updated_ms else None
            status = ("unconfigured" if not configured or error else "error" if self.error
                      else "loading" if self.account is None else "stale" if age > 60_000 else "ready")
            account = {"status": status, "refreshing": busy, "updated_ms": self.updated_ms or None,
                       "age_ms": age, "error": error or self.error, "data": self.account}
        settings = None
        if config and not error:
            settings = {"capital_usdt": config.capital_usdt, "risk_fraction": config.risk_fraction,
                        "nominal_risk_usdt": str(Decimal(config.capital_usdt) * Decimal(config.risk_fraction)),
                        "margin_mode": config.margin_mode, "leverage_policy": config.leverage_policy,
                        "position_mode": config.position_mode, "max_positions": config.max_positions,
                        "aggregate_risk_limit": config.aggregate_risk_limit,
                        "timeframes": ["15m", "30m", "1H", "4H"], "demo": config.demo,
                        "entry_universe": public_policy()}
        ledger = ledger_snapshot(self.runtime, now_ms)
        blockers = []
        if status != "ready":
            blockers.append("account_unverified")
        data = account["data"]
        if data:
            if data["position_mode"] != "long_short_mode":
                blockers.append("position_mode")
            if data["account_level"] != "2":
                blockers.append("account_level")
            if not data["trade_permission"]:
                blockers.append("trade_permission")
        return {"read_only": True, "generated_ms": now_ms, "configured": configured,
                "config": settings, "account": account, "ledger": ledger, "blockers": blockers}

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


def require_same_origin(request):
    """Allow local and remote viewers without adding cross-origin browser access.

    The existing public gateway normalizes Host/Origin to the upstream URL.
    Peer addresses and proxy markers are no longer account-display gates.
    This is an origin check, not authentication: the public link is login-free.
    """
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if ((origin and origin != expected)
            or request.headers.get("sec-fetch-site") not in {None, "none", "same-origin"}):
        raise HTTPException(403, "account_origin_denied")


def install(app, dashboard=None):
    dashboard = dashboard or Dashboard()
    app.state.okx_execution_dashboard = dashboard

    @app.get("/api/okx-execution/status")
    def status(request: Request, refresh: bool = False):
        require_same_origin(request)
        return dashboard.snapshot(force=refresh)
