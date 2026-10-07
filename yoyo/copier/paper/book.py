"""Paper ledger: orders, positions and fills in the copier's SQLite database.

Net mode per (account, instrument), like the live OKX/Gate accounts: at most one
open position; an opposite fill closes the old one first. Account equity is
``starting equity + realized PnL - fees``; open positions and resting entries
reserve margin (notional / leverage).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from yoyo.copier.store.models import now_iso
from yoyo.copier.store.sqlite import Database

STARTING_EQUITY = 1000.0
FEE_RATE = 0.001          # per side on notional -> 0.2% round trip
EPSILON = 1e-12

SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL, inst_id TEXT NOT NULL, side TEXT NOT NULL, ord_type TEXT NOT NULL,
    px REAL NOT NULL, qty REAL NOT NULL, leverage INTEGER NOT NULL, sl REAL, tp REAL,
    status TEXT NOT NULL, message_id INTEGER, order_row_id INTEGER, venue TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL, inst_id TEXT NOT NULL, side TEXT NOT NULL,
    qty REAL NOT NULL, open_qty REAL NOT NULL, entry_px REAL NOT NULL, leverage INTEGER NOT NULL,
    initial_sl REAL, sl REAL, tp REAL, status TEXT NOT NULL,
    realized_pnl REAL NOT NULL DEFAULT 0, fees REAL NOT NULL DEFAULT 0, exit_px REAL,
    close_reason TEXT, message_id INTEGER, opened_at TEXT NOT NULL, closed_at TEXT
);
CREATE TABLE IF NOT EXISTS paper_fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT, position_id INTEGER NOT NULL, kind TEXT NOT NULL,
    qty REAL NOT NULL, px REAL NOT NULL, fee REAL NOT NULL, pnl REAL NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_marks (
    inst_id TEXT PRIMARY KEY, px REAL NOT NULL, venue TEXT, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_bar_cursor (
    inst_id TEXT PRIMARY KEY, last_bar_ms INTEGER NOT NULL
);
"""
MINUTE_MS = 60_000


def iso_ms(value: str) -> int:
    """'2026-10-07T05:15:00Z' -> epoch ms (the store writes second-resolution UTC)."""
    from datetime import datetime, timezone
    return int(datetime.strptime(value.rstrip("Z")[:19], "%Y-%m-%dT%H:%M:%S")
               .replace(tzinfo=timezone.utc).timestamp() * 1000)


def first_full_bar_ms(created: str) -> int:
    """Only bars that start after the order/position existed may touch it."""
    return (iso_ms(created) // MINUTE_MS + 1) * MINUTE_MS


def direction(side: str) -> int:
    return 1 if side == "long" else -1


class PaperBook:
    def __init__(self, db: Database) -> None:
        self.db = db
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return self.db._connect()

    @staticmethod
    def _rows(rows) -> list[dict[str, Any]]:
        return [dict(r) for r in rows]

    # ---------- reads ----------

    def starting_equity(self) -> float:
        try:
            return float(self.db.get_setting("paper_starting_equity") or STARTING_EQUITY)
        except (TypeError, ValueError):
            return STARTING_EQUITY

    def mark(self, inst_id: str) -> Optional[float]:
        with self._connect() as conn:
            row = conn.execute("SELECT px FROM paper_marks WHERE inst_id=?", (inst_id,)).fetchone()
        return float(row[0]) if row else None

    def open_positions(self, account: Optional[str] = None, inst_id: Optional[str] = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM paper_positions WHERE status='open'", []
        if account is not None:
            sql, args = sql + " AND account=?", [*args, account]
        if inst_id is not None:
            sql, args = sql + " AND inst_id=?", [*args, inst_id]
        with self._connect() as conn:
            return self._rows(conn.execute(sql + " ORDER BY id", args).fetchall())

    def pending_orders(self, account: Optional[str] = None, inst_id: Optional[str] = None) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM paper_orders WHERE status='pending'", []
        if account is not None:
            sql, args = sql + " AND account=?", [*args, account]
        if inst_id is not None:
            sql, args = sql + " AND inst_id=?", [*args, inst_id]
        with self._connect() as conn:
            return self._rows(conn.execute(sql + " ORDER BY id", args).fetchall())

    def closed_positions(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return self._rows(conn.execute(
                "SELECT * FROM paper_positions WHERE status='closed' ORDER BY closed_at DESC, id DESC LIMIT ?",
                (limit,)).fetchall())

    def accounts(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT DISTINCT account FROM paper_positions UNION "
                                "SELECT DISTINCT account FROM paper_orders").fetchall()
        return sorted(str(r[0]) for r in rows)

    def active_inst_ids(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT inst_id FROM paper_positions WHERE status='open' UNION "
                                "SELECT inst_id FROM paper_orders WHERE status='pending'").fetchall()
        return sorted(str(r[0]) for r in rows)

    def unrealized(self, position: dict[str, Any]) -> float:
        px = self.mark(position["inst_id"])
        if px is None:
            return 0.0
        return (px - position["entry_px"]) * position["open_qty"] * direction(position["side"])

    def balance(self, account: Optional[str]) -> dict[str, float]:
        accounts = [account] if account is not None else (self.accounts() or [])
        start = self.starting_equity() * max(1, len(accounts)) if account is None else self.starting_equity()
        with self._connect() as conn:
            where, args = ("WHERE account=?", [account]) if account is not None else ("", [])
            realized, fees = conn.execute(
                f"SELECT COALESCE(SUM(realized_pnl),0), COALESCE(SUM(fees),0) FROM paper_positions {where}", args).fetchone()
        equity = start + float(realized) - float(fees)
        positions = self.open_positions(account)
        used = sum(p["open_qty"] * p["entry_px"] / max(p["leverage"], 1) for p in positions)
        used += sum(o["qty"] * o["px"] / max(o["leverage"], 1) for o in self.pending_orders(account))
        upl = sum(self.unrealized(p) for p in positions)
        return {"equity": equity, "total_usdt": equity + upl, "available_usdt": max(0.0, equity - used),
                "unrealized_pnl": upl, "used_margin": used, "positions": len(positions)}

    # ---------- writes ----------

    def set_mark(self, inst_id: str, px: float, venue: str = "") -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO paper_marks(inst_id,px,venue,at) VALUES(?,?,?,?) "
                         "ON CONFLICT(inst_id) DO UPDATE SET px=excluded.px, venue=excluded.venue, at=excluded.at",
                         (inst_id, px, venue, now_iso()))

    def place(self, *, account: str, inst_id: str, side: str, ord_type: str, px: float, qty: float,
              leverage: int, sl: Optional[float], tp: Optional[float], message_id: Optional[int],
              venue: str = "") -> dict[str, Any]:
        stamp = now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO paper_orders(account,inst_id,side,ord_type,px,qty,leverage,sl,tp,status,message_id,"
                "venue,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'pending',?,?,?,?)",
                (account, inst_id, side, ord_type, px, qty, leverage, sl, tp, message_id, venue, stamp, stamp))
            order_id = int(cur.lastrowid)
        if ord_type == "market":
            self.fill(order_id, px)
        return self.order(order_id)

    def sizing_by_message(self) -> dict[int, str]:
        """Sizing rule each paper order was opened under; plans before 2026-10-07 carry none."""
        with self._connect() as conn:
            rows = conn.execute("SELECT message_id, response_json FROM orders WHERE okx_ord_id LIKE 'paper-%'").fetchall()
        out: dict[int, str] = {}
        for message_id, raw in rows:
            try:
                plan = (json.loads(raw or "{}") or {}).get("plan") or {}
            except ValueError:
                plan = {}
            out[message_id] = plan.get("sizing") or "legacy_margin"
        return out

    def link_order_row(self, order_id: int, order_row_id: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE paper_orders SET order_row_id=? WHERE id=?", (order_row_id, order_id))

    def order(self, order_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            return dict(conn.execute("SELECT * FROM paper_orders WHERE id=?", (order_id,)).fetchone())

    def _sync_order_row(self, order: dict[str, Any], status: str) -> None:
        """Keep the shared `orders` row (what the workbench lists) in step with the paper order."""
        if not order.get("order_row_id"):
            return
        with self._connect() as conn:
            row = conn.execute("SELECT response_json FROM orders WHERE id=?", (order["order_row_id"],)).fetchone()
            if not row:
                return
            try:
                payload = json.loads(row[0] or "{}")
            except ValueError:
                payload = {}
            payload.setdefault("response", {})["status"] = status
            conn.execute("UPDATE orders SET response_json=?, status=? WHERE id=?",
                         (json.dumps(payload, ensure_ascii=False), "canceled" if status == "canceled" else "live",
                          order["order_row_id"]))

    def fill(self, order_id: int, px: float) -> dict[str, Any]:
        order = self.order(order_id)
        if order["status"] != "pending":
            return order
        stamp = now_iso()
        for existing in self.open_positions(order["account"], order["inst_id"]):
            if existing["side"] != order["side"]:
                self.close(existing["id"], 100.0, px, "reversed")
        same = self.open_positions(order["account"], order["inst_id"])
        fee = px * order["qty"] * FEE_RATE
        with self._connect() as conn:
            if same:
                pos = same[0]
                new_qty = pos["open_qty"] + order["qty"]
                entry = (pos["entry_px"] * pos["open_qty"] + px * order["qty"]) / new_qty
                conn.execute("UPDATE paper_positions SET qty=qty+?, open_qty=?, entry_px=?, fees=fees+?, "
                             "sl=COALESCE(?,sl), tp=COALESCE(?,tp) WHERE id=?",
                             (order["qty"], new_qty, entry, fee, order["sl"], order["tp"], pos["id"]))
                position_id = pos["id"]
            else:
                cur = conn.execute(
                    "INSERT INTO paper_positions(account,inst_id,side,qty,open_qty,entry_px,leverage,initial_sl,sl,tp,"
                    "status,fees,message_id,opened_at) VALUES(?,?,?,?,?,?,?,?,?,?,'open',?,?,?)",
                    (order["account"], order["inst_id"], order["side"], order["qty"], order["qty"], px,
                     order["leverage"], order["sl"], order["sl"], order["tp"], fee, order["message_id"], stamp))
                position_id = int(cur.lastrowid)
            conn.execute("INSERT INTO paper_fills(position_id,kind,qty,px,fee,pnl,at) VALUES(?,?,?,?,?,0,?)",
                         (position_id, "open", order["qty"], px, fee, stamp))
            conn.execute("UPDATE paper_orders SET status='filled', px=?, updated_at=? WHERE id=?", (px, stamp, order_id))
        self._sync_order_row(order, "filled")
        return self.order(order_id)

    def cancel(self, order_id: int) -> None:
        order = self.order(order_id)
        if order["status"] != "pending":
            return
        with self._connect() as conn:
            conn.execute("UPDATE paper_orders SET status='canceled', updated_at=? WHERE id=?", (now_iso(), order_id))
        self._sync_order_row(order, "canceled")

    def close(self, position_id: int, pct: float, px: float, reason: str) -> dict[str, Any]:
        with self._connect() as conn:
            pos = dict(conn.execute("SELECT * FROM paper_positions WHERE id=?", (position_id,)).fetchone())
        if pos["status"] != "open":
            return {"closed_qty": 0.0, "pnl": 0.0}
        pct = min(max(float(pct), 0.0), 100.0)
        qty = pos["open_qty"] if pct >= 100 else pos["open_qty"] * pct / 100
        if qty <= EPSILON:
            return {"closed_qty": 0.0, "pnl": 0.0}
        pnl = (px - pos["entry_px"]) * qty * direction(pos["side"])
        fee = px * qty * FEE_RATE
        remaining = pos["open_qty"] - qty
        done = remaining <= pos["qty"] * 1e-9
        stamp = now_iso()
        closed_before = pos["qty"] - pos["open_qty"]
        exit_px = ((pos["exit_px"] or 0) * closed_before + px * qty) / max(closed_before + qty, EPSILON)
        with self._connect() as conn:
            conn.execute("UPDATE paper_positions SET open_qty=?, realized_pnl=realized_pnl+?, fees=fees+?, exit_px=?, "
                         "status=?, close_reason=?, closed_at=? WHERE id=?",
                         (0.0 if done else remaining, pnl, fee, exit_px, "closed" if done else "open",
                          reason if done else pos["close_reason"], stamp if done else None, position_id))
            conn.execute("INSERT INTO paper_fills(position_id,kind,qty,px,fee,pnl,at) VALUES(?,?,?,?,?,?,?)",
                         (position_id, reason, qty, px, fee, pnl, stamp))
        return {"closed_qty": qty, "pnl": pnl, "fee": fee, "closed": done}

    def set_stop(self, position_id: int, sl: Optional[float] = None, tp: Optional[float] = None) -> None:
        with self._connect() as conn:
            if sl is not None:
                conn.execute("UPDATE paper_positions SET sl=? WHERE id=?", (sl, position_id))
            if tp is not None:
                conn.execute("UPDATE paper_positions SET tp=? WHERE id=?", (tp, position_id))

    # ---------- matching ----------

    @staticmethod
    def liquidation_px(position: dict[str, Any]) -> float:
        """Isolated-style bound: the move that consumes the position's margin (maintenance ignored)."""
        return position["entry_px"] * (1 - direction(position["side"]) / max(position["leverage"], 1))

    def process_mark(self, inst_id: str, px: float, venue: str = "") -> list[dict[str, Any]]:
        """Apply one observed price: fill crossed limit entries, then stops/targets/liquidation."""
        self.set_mark(inst_id, px, venue)
        events = []
        for order in self.pending_orders(inst_id=inst_id):
            crossed = px <= order["px"] if order["side"] == "long" else px >= order["px"]
            if crossed:
                self.fill(order["id"], order["px"])
                events.append({"kind": "fill", "order_id": order["id"], "px": order["px"]})
        for pos in self.open_positions(inst_id=inst_id):
            d = direction(pos["side"])
            liq = self.liquidation_px(pos)
            # The level nearer to entry is reached first on the way down (long) / up (short).
            stops = [(lvl, why) for lvl, why in ((pos["sl"], "stop_loss"), (liq, "liquidation")) if lvl]
            stop = max(stops, key=lambda s: s[0] * d) if stops else None
            if stop and (px - stop[0]) * d <= 0:
                # Stops are market orders: fill at the observed price, never better than the
                # trigger and never worse than liquidation (the margin is the most it can lose).
                if stop[1] == "stop_loss":
                    fill_px = min(px, stop[0]) if d > 0 else max(px, stop[0])
                    fill_px = max(fill_px, liq) if d > 0 else min(fill_px, liq)
                else:
                    fill_px = liq
                self.close(pos["id"], 100.0, fill_px, stop[1])
                events.append({"kind": stop[1], "position_id": pos["id"], "px": fill_px})
            elif pos["tp"] and (px - pos["tp"]) * d >= 0:
                self.close(pos["id"], 100.0, pos["tp"], "take_profit")
                events.append({"kind": "take_profit", "position_id": pos["id"], "px": pos["tp"]})
        return events

    def bar_cursor(self, inst_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT last_bar_ms FROM paper_bar_cursor WHERE inst_id=?", (inst_id,)).fetchone()
        return int(row[0]) if row else 0

    def process_bar(self, inst_id: str, start_ms: int, o: float, h: float, l: float, c: float) -> list[dict[str, Any]]:
        """Apply one closed 1m bar. Within a bar the order of high and low is unknown, so
        a stop and a target touched in the same bar resolve as the stop (conservative)."""
        events = []
        for order in self.pending_orders(inst_id=inst_id):
            if start_ms < first_full_bar_ms(order["created_at"]):
                continue
            px = order["px"]
            if order["side"] == "long" and l <= px:
                fill = min(px, o)  # a limit fills at its price, or at a better gapped open
            elif order["side"] == "short" and h >= px:
                fill = max(px, o)
            else:
                continue
            self.fill(order["id"], fill)
            events.append({"kind": "fill", "order_id": order["id"], "px": fill, "bar_ms": start_ms})
        for pos in self.open_positions(inst_id=inst_id):
            if start_ms < first_full_bar_ms(pos["opened_at"]):
                continue
            d = direction(pos["side"])
            liq = self.liquidation_px(pos)
            stops = [(lvl, why) for lvl, why in ((pos["sl"], "stop_loss"), (liq, "liquidation")) if lvl]
            stop = max(stops, key=lambda s: s[0] * d) if stops else None
            adverse, favourable = (l, h) if d > 0 else (h, l)
            if stop and (adverse - stop[0]) * d <= 0:
                if stop[1] == "stop_loss":
                    # Stop-market: the trigger price, or the open if the bar gapped through it.
                    fill = min(stop[0], o) if d > 0 else max(stop[0], o)
                    fill = max(fill, liq) if d > 0 else min(fill, liq)
                else:
                    fill = liq
                self.close(pos["id"], 100.0, fill, stop[1])
                events.append({"kind": stop[1], "position_id": pos["id"], "px": fill, "bar_ms": start_ms})
            elif pos["tp"] and (favourable - pos["tp"]) * d >= 0:
                fill = max(pos["tp"], o) if d > 0 else min(pos["tp"], o)
                self.close(pos["id"], 100.0, fill, "take_profit")
                events.append({"kind": "take_profit", "position_id": pos["id"], "px": fill, "bar_ms": start_ms})
        with self._connect() as conn:
            conn.execute("INSERT INTO paper_bar_cursor(inst_id,last_bar_ms) VALUES(?,?) ON CONFLICT(inst_id) "
                         "DO UPDATE SET last_bar_ms=MAX(last_bar_ms, excluded.last_bar_ms)", (inst_id, start_ms))
        return events

    # ---------- reporting ----------

    @staticmethod
    def r_multiple(position: dict[str, Any]) -> Optional[float]:
        if not position.get("initial_sl"):
            return None
        risk = abs(position["entry_px"] - position["initial_sl"]) * position["qty"]
        if risk <= EPSILON:
            return None
        return (position["realized_pnl"] - position["fees"]) / risk
