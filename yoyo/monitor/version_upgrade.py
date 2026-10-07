"""Record a forward-only V12.8 cutover without deleting historical observations.

The owner requested latest Pine rules on 2026-09-23. Event identities and
notification policies change together; old receipts and payloads remain
immutable. This module never sends a notification or touches execution state.
"""
from __future__ import annotations

from yoyo.monitor import BARK_ARM_KEY, SIGNAL_PROTOCOL, MODEL_PROTOCOL, STRATEGY_VERSION
from yoyo.monitor.store import encode

UPGRADE_KEY = "migration:spike-v128-upgrade-v1"


def record_upgrade(store, synchronized_ms: int) -> dict:
    """Retire only obsolete pending work and preserve the first cutover."""
    if type(synchronized_ms) is not int or synchronized_ms < 0:
        raise ValueError("invalid upgrade clock")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT payload FROM meta WHERE key=?", (UPGRADE_KEY,)).fetchone()
        if row:
            import json
            return json.loads(row[0])
        arm = store.get_meta(BARK_ARM_KEY)
        if not isinstance(arm, dict) or type(arm.get("activated_ms")) is not int:
            raise RuntimeError("v128_notification_cutover_required")
        for table in ("outbox", "bark_outbox"):
            db.execute(f"UPDATE {table} SET status='retired',error='superseded_by_v128',updated_ms=? "
                       "WHERE status='pending' AND event_id IN (SELECT id FROM events "
                       "WHERE json_extract(payload,'$.protocol') NOT IN (?,?))",
                       (synchronized_ms, SIGNAL_PROTOCOL, MODEL_PROTOCOL))
        db.execute("UPDATE model_candidates SET status='expired' WHERE status='pending' AND id IN "
                   "(SELECT id FROM events WHERE json_extract(payload,'$.protocol')!=?)", (SIGNAL_PROTOCOL,))
        receipt = {"activated_ms": arm["activated_ms"], "recorded_ms": synchronized_ms,
                   "protocol": SIGNAL_PROTOCOL, "strategy_version": STRATEGY_VERSION,
                   "history_preserved": True, "historical_notifications": False}
        db.execute("INSERT INTO meta VALUES (?,?)", (UPGRADE_KEY, encode(receipt)))
    return receipt
