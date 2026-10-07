"""Independent notification policy for the V13.1 retest monitor (15m/30m/1H/4H).

The protocol is deliberately separate from V12.8 raw starts and YOLO
confirmations.  Only a closed, forward retest confirmation may be stored for
this stream or delivered through its own Telegram/Bark cutovers.  The stream
identity (protocol, kind, policy, topic) is unchanged from V13.0 so existing
subscriptions keep applying; V13.1 is carried by VERSION and each period has its
own forward cutover (owner 2026-10-07: signal center becomes V13.1, history cleared).
"""
from __future__ import annotations

import math
import re

from yoyo.monitor import TIMEFRAMES as STEPS

SIGNAL_PROTOCOL = "spike-burst-v130-retest-monitor-v1"
SIGNAL_KIND = "spike_burst_v130_retest"
POLICY = "spike-burst-v130-retest-notifications-v1"
VERSION = "spike-v13.1-retest-monitor-20261007-v1"
TOPIC = "spike_v130"
TIMEFRAMES = ("15m", "30m", "1H", "4H")
MAX_WAIT_BARS = 24


def trail_atr(timeframe: str) -> float:
    """Close-trail multiple of spike_burst_v13_1.pine: 2ATR on 1h charts, 4ATR otherwise."""
    if timeframe not in TIMEFRAMES:
        raise ValueError("unsupported V13.1 timeframe")
    return 2.0 if timeframe == "1H" else 4.0


_SYMBOL = re.compile(r"[A-Z0-9]+-[A-Z0-9]+-SWAP\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _finite_positive(value: object) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value > 0)


def is_v130_signal(event: object) -> bool:
    """Validate the immutable live V13 confirmation contract.

    The anchor, breakout and retest timestamps establish causal order only.
    Notification age and activation are evaluated against ``bar_close_ms``,
    the final confirmation close, so an older anchor cannot make a fresh
    confirmation ineligible.
    """
    if not isinstance(event, dict):
        return False
    side = event.get("side")
    timeframe = event.get("timeframe")
    step = STEPS.get(timeframe) if timeframe in TIMEFRAMES else None
    if (step is None or event.get("protocol") != SIGNAL_PROTOCOL or event.get("kind") != SIGNAL_KIND
            or event.get("strategy_version") != VERSION
            or event.get("timeframe_min") != step // 60_000 or side not in ("long", "short")
            or event.get("direction") != side or event.get("source") != "live"
            or event.get("confirmation") != "retest" or event.get("v130_admitted") is not True
            or event.get("confirmed") is not True or event.get("is_closed") is not True
            or event.get("is_trade") is not False
            or event.get("entry_reference") != "next_open_reference_not_fill"
            or event.get("candidate_source") != "ordinary_both"):
        return False
    symbol = event.get("symbol")
    if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
        return False
    digest = event.get("source_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        return False
    stamps = tuple(event.get(key) for key in (
        "anchor_close_ms", "breakout_close_ms", "retest_close_ms", "bar_close_ms"))
    if (any(type(value) is not int or value < 0 or value % step for value in stamps)
            or not stamps[0] < stamps[1] < stamps[2] < stamps[3]):
        return False
    if (type(event.get("bar_open_ms")) is not int
            or event["bar_open_ms"] != stamps[3] - step):
        return False
    if (type(event.get("detected_at_ms")) is not int
            or event["detected_at_ms"] < stamps[3]):
        return False
    wait_bars = event.get("wait_bars")
    elapsed_bars = (stamps[3] - stamps[0]) // step
    if (type(wait_bars) is not int or not 0 <= wait_bars <= MAX_WAIT_BARS
            or wait_bars != elapsed_bars):
        return False
    price = event.get("price")
    reference_price = event.get("reference_price")
    risk = event.get("risk")
    stop = event.get("initial_stop")
    if not all(_finite_positive(value) for value in (price, reference_price, risk, stop)):
        return False
    if reference_price != price:
        return False
    if side == "long" and not stop < price:
        return False
    if side == "short" and not stop > price:
        return False
    if not math.isclose(risk, abs(price - stop), rel_tol=1e-9, abs_tol=1e-12):
        return False
    return True


def arm_v130(store, exchange_ms: int) -> dict:
    """Persist independent channel cutovers and each period boundary once.

    Repeated calls are restart-safe: each existing cutover is preserved and
    no existing event or outbox is scanned or backfilled here.
    """
    if type(exchange_ms) is not int or exchange_ms < 0:
        raise ValueError("invalid_v130_activation")
    telegram = store.activate_notification_policy(
        exchange_ms, protocol=POLICY, kind=SIGNAL_KIND, retire_obsolete=False)
    bark = store.activate_bark_policy(
        exchange_ms, protocol=POLICY, kind=SIGNAL_KIND, retire_obsolete=False)
    periods = {tf: store.activate_timeframe_policy(tf, exchange_ms, protocol=POLICY) for tf in TIMEFRAMES}
    return {"protocol": SIGNAL_PROTOCOL, "policy": POLICY, "timeframes": list(TIMEFRAMES),
            "telegram_activated_ms": telegram, "bark_activated_ms": bark,
            "timeframe_activated_ms": periods}
