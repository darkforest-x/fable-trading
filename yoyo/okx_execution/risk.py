"""Exact-decimal, linear-USDT contract sizing, without portfolio caps.

Owner-selected 1R is 2% of min(50 USDT, current USDT equity), excluding costs.
OKX documents linear notional as sz * ctVal * price (not ctMult again):
https://www.okx.com/docs-v5/#order-book-trading-trade-post-place-order
Venue maximum leverage minimizes posted margin, not risk. Liquidation may
precede the strategy stop; neither 1R nor 50 USDT is a guaranteed loss bound.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_CEILING


def D(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not an amount")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("invalid exchange amount") from None
    if not result.is_finite():
        raise ValueError("nonfinite amount")
    return result


def amount(value) -> str:
    return format(D(value), "f")


def quantize(value, step, *, up=False):
    value, step = D(value), D(step)
    if step <= 0:
        raise ValueError("invalid precision")
    return (value / step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * step


def plan(event, instrument, ticker, tiers, *, equity, available, allocated_margin, existing_size, config):
    """Use current quote and frozen signal stop; never reference performance."""
    if (instrument.get("instId") != event["symbol"] or instrument.get("instType") != "SWAP"
            or instrument.get("ctType") != "linear" or instrument.get("settleCcy") != "USDT"
            or instrument.get("state") != "live"):
        raise ValueError("instrument is not an available live linear USDT swap")
    long = event["side"] == "long"
    price = D(ticker["askPx" if long else "bidPx"])
    tick, ctval, lot, minimum = [D(instrument[k]) for k in ("tickSz", "ctVal", "lotSz", "minSz")]
    if min(price, tick, ctval, lot, minimum) <= 0:
        raise ValueError("invalid exchange specification")
    stop = quantize(event["initial_stop"], tick, up=not long)
    distance = (price - stop) if long else (stop - price)
    if stop <= 0 or distance <= 0:
        raise ValueError("market has passed the initial stop")
    capital = max(D(0), min(D(config.capital_usdt), D(equity)))
    target = capital * D(config.risk_fraction)
    size = quantize(target / (distance * ctval), lot)
    if size < minimum:
        raise ValueError("risk-sized quantity is below exchange minimum")
    if size > D(instrument["maxMktSz"]):
        raise ValueError("risk-sized quantity exceeds exchange market-order limit")
    total = size + D(existing_size)
    matching = [r for r in tiers if D(r["minSz"]) <= total <= D(r["maxSz"])]
    if not matching:
        raise ValueError("exchange position tier is unavailable")
    leverage = min(D(instrument["lever"]), *(D(r["maxLever"]) for r in matching))
    if leverage <= 0:
        raise ValueError("invalid exchange leverage")
    notional = size * ctval * price
    margin = notional / leverage
    remaining = max(D(0), min(D(available), capital - D(allocated_margin)))
    if margin > remaining:
        raise ValueError("insufficient available allocation for this signal")
    return dict(symbol=event["symbol"], timeframe=event["timeframe"], side=event["side"],
                size=amount(size), price=amount(price), stop=amount(stop), ctval=amount(ctval),
                tick=amount(tick), leverage=amount(leverage), margin=amount(margin),
                planned_risk=amount(size * ctval * distance), target_risk=amount(target),
                notional=amount(notional), risk_excludes_fees_slippage_and_funding=True,
                liquidation_can_precede_stop=True)


def advance(position, bar):
    """Closed-bar-only 2R arming, 2ATR (1H) / 4ATR (others) monotone trail.

    Reads current close/ATR/raw_side from the canonical causal prefix. Does not
    infer fills from candle highs/lows. Reference entry is replaced by actual
    exchange fill; raw opposite requests an exit at the next available market.
    """
    from yoyo.monitor.v130_policy import trail_atr
    side = 1 if position["side"] == "long" else -1
    entry, initial = D(position["entry_price"]), D(position["initial_stop"])
    risk = D(side) * (entry - initial)
    if risk <= 0:
        raise ValueError("actual fill is beyond the initial stop")
    armed = position.get("trailing", False) or D(side) * (D(bar["close"]) - entry) >= 2 * risk
    stop = D(position["stop"])
    if armed:
        atr = D(bar["atr"])
        if atr <= 0:
            raise ValueError("ATR unavailable")
        proposal = quantize(D(bar["close"]) - side * D(trail_atr(position["timeframe"])) * atr,
                            position["tick"], up=side == -1)
        stop = max(stop, proposal) if side == 1 else min(stop, proposal)
    return dict(stop=amount(stop), trailing=bool(armed), close_ms=bar["close_ms"],
                exit=int(bar["raw_side"]) == -side)
