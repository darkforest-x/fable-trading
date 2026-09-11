"""Owner's separate display and Bark delivery scopes for both signal stages.

Only the immutable arrow bar (OHLC/IMACD at or before close) is used for a
direct start. Model confirmation keeps its original causal proof and cutoff.
Each channel and stage has its own persisted cutover. No historical signal is
authorized by an older raw-arrow policy. Each newly enabled period needs its
own cutover; enabling a daily stream cannot inherit any earlier period’s cutover.
"""
import re

from yoyo.monitor import (DIRECT_POLICY, DIRECT_TIMEFRAMES, FRESH_MS, MODEL_PROTOCOL,
                          MONITORED_TIMEFRAMES, TIMEFRAMES, BARK_TIMEFRAMES)
from yoyo.monitor.policy import finite, is_model_signal, is_tv_start


def arm_v1_bark(store, activated_ms):
    """Create one forward-only V1 Bark cutover for raw and YOLO-extra stages.

    This is deliberately independent of Telegram and is idempotent.  The
    caller supplies the synchronized exchange clock before scanning a new bar;
    pre-cutover rows remain history and are never enqueued retrospectively.
    """
    key = "notification_policy:v1_bark_arm"
    existing = store.get_meta(key)
    # A later owner-authorized timeframe needs its own fresh boundary. Keep
    # the original arm receipt immutable: the history UI relies on that date.
    if existing is not None and all(store.timeframe_activation(tf, protocol=p) is not None
                                    for p in (DIRECT_POLICY, MODEL_PROTOCOL) for tf in BARK_TIMEFRAMES):
        return existing
    for protocol in (DIRECT_POLICY, MODEL_PROTOCOL):
        store.activate_bark_policy(activated_ms, protocol=protocol, retire_obsolete=False)
        for timeframe in BARK_TIMEFRAMES:
            if store.timeframe_activation(timeframe, protocol=protocol) is None:
                store.activate_timeframe_policy(timeframe, activated_ms, protocol=protocol)
    if existing is not None:
        return existing
    receipt = {"activated_ms": int(activated_ms), "protocols": [DIRECT_POLICY, MODEL_PROTOCOL],
               "timeframes": list(BARK_TIMEFRAMES), "telegram": "disabled"}
    store.set_meta(key, receipt)
    return receipt


def activation(store, channel, protocol):
    if channel not in ("telegram", "bark"):
        raise ValueError("unsupported notification channel")
    prefix = "notification_policy:" + ("bark:" if channel == "bark" else "")
    value = store.get_meta(prefix + protocol, {}).get("activated_ms")
    return value if type(value) is int and value >= 0 else None


def channel_enabled(store, channel):
    return any(activation(store, channel, p) is not None for p in (MODEL_PROTOCOL, DIRECT_POLICY))


def is_direct_start(event):
    """Validate the original arrow, its aligned closed bar and setup prefix."""
    if (not is_tv_start(event) or event.get("timeframe") not in DIRECT_TIMEFRAMES
            or not isinstance(event.get("symbol"), str)
            or not re.fullmatch(r"[A-Z0-9]+-[A-Z0-9]+-SWAP", event["symbol"])
            or not finite(event.get("price")) or event["price"] <= 0):
        return False
    start, end = (event.get(k) for k in ("bar_open_ms", "bar_close_ms"))
    if not all(type(t) is int and t >= 0 for t in (start, end)):
        return False
    step = TIMEFRAMES[event["timeframe"]]
    return start % step == 0 and end == start + step


def delivery_error(store, event, now, channel):
    """Return a stable rejection code, or None for a currently deliverable leg."""
    # Recheck at the sender boundary: a durable queue may predate withdrawal.
    if event.get("timeframe") not in MONITORED_TIMEFRAMES:
        return "timeframe_disabled_by_owner"
    if channel == "bark" and event.get("timeframe") not in BARK_TIMEFRAMES:
        return "bark_timeframe_muted_by_owner"
    if is_model_signal(event):
        protocol = MODEL_PROTOCOL
        closes = (event["bar_close_ms"], event["indicator"]["bar_close_ms"])
    elif is_direct_start(event) and activation(store, channel, DIRECT_POLICY) is not None:
        protocol = DIRECT_POLICY
        closes = (event["bar_close_ms"],)
    else:
        return "not_model_confirmed_signal"
    since = activation(store, channel, protocol)
    if since is None or min(closes) <= since:
        return "before_bark_activation" if channel == "bark" else "before_notification_policy_activation"
    timeframe_since = store.timeframe_activation(event["timeframe"], protocol=protocol)
    if timeframe_since is None or min(closes) <= timeframe_since:
        return "before_timeframe_activation"
    if not 0 <= now - event["bar_close_ms"] <= FRESH_MS:
        return "signal_expired"
    return None
