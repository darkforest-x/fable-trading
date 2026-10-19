"""Owner's 2026-10-17 manual contract: 3% total equity, 5m six-MA envelope SL, 3/5R TP.

Account totalEq is USD; convert through the same receipt's USDT eq/eqUsd.
This includes trading-account equity, not a funding-wallet allocation. Only
closed candles available at preview time are used. A saved detection supplies
context, never authorization. API definitions: https://www.okx.com/docs-v5/en/.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from yoyo.contracts.signal_universe import exclusion_reason
from .engine import one
from .risk import D, amount, plan, quantize, MarketOrderLimitError, InsufficientMarginError
from .store import identifier

SOURCE = "owner_manual_yolo"
PREVIEW_TTL_MS = 60_000


def manual(event):
    return event.get("source") == SOURCE


def take_profit(entry, stop, side, rr, tick):
    if type(rr) is not int or rr not in (3, 5) or side not in ("long", "short"):
        raise ValueError("invalid manual exit policy")
    sign = 1 if side == "long" else -1
    distance = sign * (D(entry) - D(stop))
    if distance <= 0:
        raise ValueError("actual fill crossed initial stop")
    # Round toward entry: never promise more R than the selected target.
    target = quantize(D(entry) + sign * rr * distance, tick, up=side == "short")
    if target <= 0 or sign * (target - D(entry)) <= 0:
        raise ValueError("take profit is outside the valid price range")
    return amount(target)


def reference_prices(*, symbol, timeframe, side, rr, price, stop, tick, candle,
                     stop_source, quote_ms, observed_ms, expires_ms):
    """Display-only prices; no identity, quantity, account or execution token."""
    stop = quantize(stop, tick, up=side == 'short')
    if min(D(price), stop, D(tick)) <= 0 or side not in ('long', 'short') or type(rr) is not int or rr not in (3, 5):
        raise ValueError('invalid manual reference prices')
    try:
        target = take_profit(price, stop, side, rr, tick)
        error = None
    except ValueError as exc:
        target, error = None, str(exc)
    return dict(display_only=True, symbol=symbol, timeframe=timeframe, side=side, rr=rr,
                price=amount(price), initial_stop=amount(stop), take_profit=target,
                stop_candle=candle, stop_source=stop_source, quote_ms=quote_ms,
                observed_ms=observed_ms, expires_ms=expires_ms, price_error=error)


def closed_stop(client, symbol, side, clock):
    """Read high/low of the latest confirmed 5m bar; no developing bar input."""
    rows = client.get("/api/v5/market/candles", instId=symbol, bar="5m", limit="3")
    now = clock()
    opening = now // 300_000 * 300_000 - 300_000
    matches = [r for r in rows if len(r) == 9 and str(r[0]) == str(opening) and r[8] == "1"]
    if len(matches) != 1:
        raise ValueError("latest closed 5m candle unavailable")
    row = matches[0]
    o, h, low, c = (D(row[i]) for i in range(1, 5))
    if min(o, h, low, c) <= 0 or not low <= min(o, c) <= max(o, c) <= h:
        raise ValueError("invalid closed 5m candle")
    return dict(open_ms=opening, close_ms=opening+300_000, high=amount(h), low=amount(low),
                price=amount(low if side == "long" else h), observed_ms=now)


def total_account(client):
    balance = one(client.get("/api/v5/account/balance"))
    usdt = one([r for r in balance.get("details", []) if r.get("ccy") == "USDT"])
    total, eq, eq_usd = D(balance["totalEq"]), D(usdt["eq"]), D(usdt["eqUsd"])
    if min(total, eq, eq_usd) <= 0:
        raise ValueError("total trading-account equity unavailable")
    return dict(total_equity_usdt=amount(total * eq / eq_usd), total_equity_usd=amount(total),
                usdt_usd=amount(eq_usd / eq), available=amount(usdt["availEq"]),
                allocated_margin=amount(usdt["imr"]), usdt_equity=amount(eq))


@dataclass(frozen=True)
class Sizing:
    capital_usdt: str
    risk_fraction: str = "0.03"
    margin_mode: str = "cross"


def prepare(engine, event, account):
    """Reprice within owner-confirmed risk/quantity limits, without new discretion."""
    if exclusion_reason(event["symbol"]) is not None:
        raise ValueError("symbol excluded by owner entry universe")
    if (event.get("side") not in ("long", "short") or type(event.get("rr")) is not int
            or event["rr"] not in (3, 5) or event.get("risk_fraction") != "0.03"
            or not engine.event_valid(event)):
        raise ValueError("manual preview expired or policy invalid")
    if account.get("uid") != event.get("account_uid") or account.get("uid") != engine.store.meta("account_uid"):
        raise ValueError("manual confirmation belongs to a different account")
    equity = total_account(engine.client)
    # A subsequent balance increase never enlarges an approved monetary budget.
    capital = min(D(equity["total_equity_usdt"]), D(event["approved_equity_usdt"]))
    instrument = one(engine.client.get("/api/v5/account/instruments", instType="SWAP", instId=event["symbol"]))
    tiers = engine.client.get("/api/v5/public/position-tiers", instType="SWAP", tdMode="cross",
                              instFamily=instrument["instFamily"])
    requested = engine.clock()
    ticker = one(engine.client.get("/api/v5/market/ticker", instId=event["symbol"]))
    # The preview reader may overlap this GET with inventory checks. Preserve
    # its actual request clock while applying freshness at calculation time.
    requested = getattr(engine.client, "quote_request_ms", requested)
    received = engine.clock()
    # An exchange-clock lead within the preflight tolerance is not old market data. Wait for our
    # clock to reach it, then apply the unchanged [0, 5000] ms freshness gate.
    # Never rewrite timestamps or extend the owner's preview deadline.
    ahead = int(ticker["ts"]) - received
    if 0 < ahead <= 5000:
        time.sleep((ahead + 1) / 1000)
        received = engine.clock()
    if not engine.event_valid(event) or not 0 <= received - int(ticker["ts"]) <= 5000:
        raise ValueError("manual preview or quote expired")
    sizes = {side: sum((abs(D(p["pos"])) for p in account["positions"]
                       if p["instId"] == event["symbol"] and p["posSide"] == side), D(0))
             for side in ("long", "short")}
    limit = None
    if event.get("approved_size") is not None:
        price = D(ticker["askPx" if event["side"] == "long" else "bidPx"])
        if price <= 0 or D(instrument["ctVal"]) <= 0:
            raise ValueError("invalid exchange specification")
        limit = min(D(event["approved_size"]), D(event["approved_margin"])*D(event["approved_leverage"])
                    / (price*D(instrument["ctVal"])))
    try:
        result = plan(event, instrument, ticker, tiers, equity=capital, available=equity["available"],
                      allocated_margin=equity["allocated_margin"], existing_size=sizes[event["side"]],
                      opposite_size=sizes["short" if event["side"] == "long" else "long"],
                      config=Sizing(amount(capital)), size_limit=limit)
    except (MarketOrderLimitError, InsufficientMarginError) as exc:
        # Explain the exact rejected quote, without creating a confirmable draft.
        exc.details['reference'] = reference_prices(
            symbol=event['symbol'], timeframe=event['timeframe'], side=event['side'], rr=event['rr'],
            price=exc.details['price'], stop=exc.details['stop'], tick=instrument['tickSz'],
            candle=event['stop_candle'], stop_source=event['stop_source'], quote_ms=int(ticker['ts']),
            observed_ms=received, expires_ms=event['expires_ms'])
        raise
    # Execution may reduce size as price moves away from the stop, never increase
    # the quantity or margin beyond what the owner reviewed.
    if event.get("approved_size") is not None:
        size = min(D(result["size"]), D(event["approved_size"]))
        result.update(size=amount(size), notional=amount(size*D(result["ctval"])*D(result["price"])),
                      planned_risk=amount(size*D(result["ctval"])*abs(D(result["price"])-D(result["stop"]))))
        result["margin"] = amount(D(result["notional"])/D(result["leverage"]))
        if (D(result["margin"]) > D(event["approved_margin"]) or result["leverage"] != event["approved_leverage"]):
            raise ValueError("manual margin or leverage changed; preview again")
    tp = take_profit(result["price"], result["stop"], event["side"], event["rr"], result["tick"])
    return dict(result, id=event["id"], event=event, initial_stop=result["stop"], take_profit=tp,
                rr=event["rr"], total_equity_usdt=equity["total_equity_usdt"],
                entry_id=identifier(event["id"], "entry"), algo_client_id=identifier(event["id"], "stop"),
                created_ms=received, state="prepared", quote_request_ms=requested,
                quote_received_ms=received, quote_ms=int(ticker["ts"]), quote_age_ms=received-int(ticker["ts"]))
