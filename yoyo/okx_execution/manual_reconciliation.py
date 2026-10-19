"""Reconcile externally flattened manual lots using GET receipts only.

An owner can add to and flatten a hedge position outside this executor. A
canceled OCO plus a missing position must never cause another close order.
Require two flat account reads, no pending orders, a terminal owned OCO and a
completed reduce-only close since our entry. Mixed-lot exit economics remain
unknown. OKX V5 Trade / order history: https://www.okx.com/docs-v5/en/.
"""
from .engine import TERMINAL
from .manual_inventory import pages, policy
from .risk import D


def external_flattening(engine, trade):
    """Return evidence for one unambiguous flat instrument, otherwise no change."""
    if (not policy(engine.store) or trade.get("state") != "exit_needed"
            or not trade.get("entry_price") or not trade.get("algo_id")
            or trade.get("exit_id") or trade.get("exit_request")):
        return None
    siblings = [r for r in engine.store.trades()
                if r["symbol"] == trade["symbol"] and r["state"] not in TERMINAL]
    if (not siblings or trade["id"] not in {r["id"] for r in siblings}
            or any(r.get("state") != "exit_needed" or r.get("side") != trade["side"]
                   or not r.get("entry_price") or not r.get("algo_id")
                   or r.get("exit_id") or r.get("exit_request")
                   or int(r.get("entry_ms") or 0) <= 0 for r in siblings)
            or len({r["algo_id"] for r in siblings}) != len(siblings)):
        return None

    def flat_account():
        account = engine.account()
        if account["uid"] != engine.store.meta("account_uid"):
            raise ValueError("execution account changed")
        return not any(p["instId"] == trade["symbol"] and D(p["pos"]) != 0
                       for p in account["positions"])

    if not flat_account():
        return None
    protections = {r["id"]: engine.algo(r) for r in siblings}
    if any(a.get("state") not in ("canceled", "order_failed") for a in protections.values()):
        return None
    algo = protections[trade["id"]]
    queries = [("/api/v5/trade/orders-pending", "ordId", {})]
    queries += [("/api/v5/trade/orders-algo-pending", "algoId", {"ordType": kind})
                for kind in ("conditional", "oco", "trigger", "move_order_stop")]
    for path, identity, query in queries:
        if any(r["instId"] == trade["symbol"] for r in pages(engine.client, path, identity, **query)):
            return None
    # Bounded history is sufficient only when it contains positive close proof.
    history = engine.client.get("/api/v5/trade/orders-history", instType="SWAP",
                                instId=trade["symbol"], limit="100")
    closing_side = "sell" if trade["side"] == "long" else "buy"
    entry_ms = max(int(r["entry_ms"]) for r in siblings)
    owned_size = sum((D(r["size"]) for r in siblings), D(0))
    if entry_ms <= 0:
        return None
    closes = [r for r in history if r.get("instId") == trade["symbol"]
              and r.get("posSide") == trade["side"] and r.get("side") == closing_side
              and r.get("state") == "filled" and r.get("reduceOnly") in (True, "true")
              and r.get("ordId") and int(r.get("cTime") or 0) >= entry_ms
              and int(r.get("uTime") or 0) >= entry_ms
              and D(r.get("accFillSz") or 0) >= owned_size]
    if not closes or not flat_account():
        return None
    # Never copy a whole-account close's PnL/price into a smaller owned lot.
    return dict(checked_ms=engine.clock(), instrument_flat=True,
                protection=algo, sibling_ids=[r["id"] for r in siblings], owned_size=str(owned_size),
                close_orders=closes, economics="unattributed_external_close")
