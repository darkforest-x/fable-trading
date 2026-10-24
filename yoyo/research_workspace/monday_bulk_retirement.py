"""Retire persisted Monday 1.618 paper campaigns without touching manual trades."""
from __future__ import annotations

from .paper_desk import clock_ms

KIND = "monday_1618"
RETIREMENT_REASON = "全币种 1.618 模拟功能已停用"


def retire_monday_bulk(desk, *, at_ms=None):
    """Disable old campaign configs and cancel their pending orders atomically.

    Config generations advance once when an enabled config or a pending campaign
    order exists. Repeating startup retirement is therefore safe and idempotent.
    Ordinary manual orders, positions, fills, and campaign history are preserved.
    """
    at = clock_ms() if at_ms is None else at_ms
    with desk.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        configs = db.execute(
            "UPDATE monday_bulk_configs "
            "SET enabled=0,version=version+1,generation=generation+1,updated_ms=? "
            "WHERE enabled<>0 OR account_id IN ("
            "SELECT account_id FROM limit_orders WHERE status='pending' AND bulk_kind=?"
            ")",
            (at, KIND),
        ).rowcount
        orders = db.execute(
            "UPDATE limit_orders SET status='cancelled',updated_ms=?,reason=? "
            "WHERE status='pending' AND bulk_kind=?",
            (at, RETIREMENT_REASON, KIND),
        ).rowcount
    return {"configs_retired": configs, "orders_cancelled": orders}
