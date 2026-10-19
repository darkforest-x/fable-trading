"""Owner-approved manual account coexistence, scoped to whole instruments.

2026-10-17: external positions/orders remain owner-managed; only individually
confirmed future orders belong to this executor. The policy is bound to a UID
in the private ledger. Never adopt outside lots or amend their protection.
OKX hedge positions aggregate per instrument/side, so an externally occupied
instrument cannot receive a new system lot. Concurrent owner edits on a system
instrument fail ownership checks; exchange reads are not an atomic reservation.
"""
from pathlib import Path
from .config import DEFAULT_RUNTIME, DEFAULT_MANUAL_RUNTIME
from .engine import TERMINAL
from .preparation import assert_no_entry_intent
from .risk import D


def policy(store):
    value = store.meta("manual_account_policy")
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"version", "mode", "account_uid"}
            or value["version"] != 1 or value["mode"] != "manual_only_shared"
            or not isinstance(value["account_uid"], str) or not value["account_uid"]):
        raise ValueError("invalid manual account policy")
    return value


def runtime_policy(store, runtime):
    """Known entry runtimes cannot silently change worker roles after metadata loss."""
    rules, path = policy(store), Path(runtime).resolve()
    if path == DEFAULT_MANUAL_RUNTIME.resolve() and rules is None:
        raise ValueError("manual account is not configured")
    if path == DEFAULT_RUNTIME.resolve() and rules is not None:
        raise ValueError("signal runtime cannot consume manual confirmations")
    return rules


def pages(client, path, identity, **query):
    seen, after = set(), None
    while True:
        rows = client.get(path, **query, limit="100", **({"after": after} if after else {}))
        for row in rows:
            key = row.get(identity)
            if not key or key in seen or not row.get("instId"):
                raise ValueError("unresolved pending-order inventory")
            seen.add(key)
            yield row
        if len(rows) < 100:
            return
        after = rows[-1][identity]


def inspect(engine, positions, *, entry=False, preparing=None):
    """Return externally occupied symbols, validating every owned quantity."""
    trades = engine.store.trades()
    expected, symbols = {}, set()
    for row in trades:
        if row["state"] in TERMINAL:
            continue
        if row["state"] == "retry_pending":
            assert_no_entry_intent(row)
            continue
        if row["state"] == "leverage_pending" and (not entry or row["id"] == preparing):
            assert_no_entry_intent(row)
            continue
        if (entry and row["state"] != "active") or row["state"] in {"entry_pending", "cancel_entry_pending"}:
            raise ValueError("unresolved execution intent; reconcile before new entries")
        key = row["symbol"], row["side"]
        symbols.add(row["symbol"])
        expected[key] = expected.get(key, D(0)) + D(row["size"])
    actual, external = {}, set()
    for row in positions:
        if D(row["pos"]) == 0:
            continue
        symbol = row["instId"]
        if symbol not in symbols:
            external.add(symbol)
            continue
        if row.get("mgnMode") != engine.config.margin_mode or row.get("posSide") not in {"long", "short"}:
            raise ValueError("position ownership changed; no discretionary mutation sent")
        key = symbol, row["posSide"]
        actual[key] = actual.get(key, D(0)) + abs(D(row["pos"]))
    if actual != expected:
        raise ValueError("exchange positions do not match owned signal lots")
    for row in pages(engine.client, "/api/v5/trade/orders-pending", "ordId"):
        if row["instId"] in symbols:
            raise ValueError("owner order conflicts with a system-managed instrument")
        external.add(row["instId"])
    known = {t.get("algo_id"): t for t in trades if t["state"] not in TERMINAL and t.get("algo_id")}
    for kind in ("conditional", "oco", "trigger", "move_order_stop"):
        for row in pages(engine.client, "/api/v5/trade/orders-algo-pending", "algoId", ordType=kind):
            owned = known.get(row["algoId"])
            if owned:
                if row["instId"] != owned["symbol"]:
                    raise ValueError("protection identity differs from owned instrument")
                continue
            if row["instId"] in symbols:
                raise ValueError("owner order conflicts with a system-managed instrument")
            external.add(row["instId"])
    return external
