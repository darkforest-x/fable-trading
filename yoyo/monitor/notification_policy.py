"""Owner's separate Telegram and Bark delivery scopes for both V12.8 stages.

Only the immutable arrow bar (OHLC/IMACD at or before close) is used for a
direct start. Model confirmation keeps its original causal proof and cutoff.
Each channel and stage has its own persisted cutover. No historical signal is
authorized by an older raw-arrow policy. Each newly enabled period needs its
own cutover; enabling a daily stream cannot inherit any earlier period’s cutover.
"""
import re

from yoyo.monitor import (DIRECT_POLICY, DIRECT_TIMEFRAMES, FRESH_MS, MODEL_KIND, MODEL_PROTOCOL,
                          MONITORED_TIMEFRAMES, TIMEFRAMES, BARK_TIMEFRAMES, BARK_ARM_KEY)
from yoyo.monitor.joint_notifications import JOINT_POLICY, is_joint_event
from yoyo.monitor.policy import finite, is_model_signal, is_tv_start, is_v1_short_display_signal
from yoyo.monitor.v130_policy import (POLICY as V130_POLICY, SIGNAL_KIND as V130_KIND,
                                      SIGNAL_PROTOCOL as V130_PROTOCOL, is_v130_signal)


def arm_v9_bark(store, activated_ms):
    """Create one forward-only V9 Bark cutover for raw and YOLO-extra stages.

    This is deliberately independent of Telegram and is idempotent.  The
    caller supplies the synchronized exchange clock before scanning a new bar;
    pre-cutover rows remain history and are never enqueued retrospectively.
    """
    key = BARK_ARM_KEY
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
               "timeframes": list(BARK_TIMEFRAMES), "channel": "bark"}
    store.set_meta(key, receipt)
    return receipt


def arm_v9_telegram(store, activated_ms):
    """Create Telegram's own forward-only V12.8 cutover for both signal stages.

    The receipt is distinct from Bark's immutable cutover. Repeated worker
    starts preserve it; the first start records the synchronized exchange
    clock and does not enqueue any existing event.
    """
    key = "notification_policy:v128_telegram_arm"
    existing = store.get_meta(key)
    if existing is not None:
        return existing
    if type(activated_ms) is not int or activated_ms < 0:
        raise ValueError("invalid Telegram activation")
    protocols = (DIRECT_POLICY, MODEL_PROTOCOL)
    # Telegram's channel-level keys are separate from Bark. The dedicated arm
    # receipt makes refreshing these keys a one-time cutover; the notification
    # center adds route-specific cutovers when an owner later re-enables a topic.
    for protocol in protocols:
        store.activate_notification_policy(activated_ms, protocol=protocol, retire_obsolete=False)
        # This unprefixed policy is Telegram's arm; Bark has a separate
        # notification_policy:bark: key. Refresh it once at this new arm so an
        # obsolete Telegram timestamp cannot authorize an older queued event.
        policy_key = "notification_policy:" + protocol
        policy = store.get_meta(policy_key, {})
        policy["activated_ms"] = activated_ms
        store.set_meta(policy_key, policy)
        for timeframe in DIRECT_TIMEFRAMES:
            if store.timeframe_activation(timeframe, protocol=protocol) is None:
                store.activate_timeframe_policy(timeframe, activated_ms, protocol=protocol)
    receipt = {"activated_ms": activated_ms, "protocols": list(protocols),
               "timeframes": list(DIRECT_TIMEFRAMES), "channel": "telegram"}
    store.set_meta(key, receipt)
    return receipt


def activation(store, channel, protocol):
    if channel not in ("telegram", "bark"):
        raise ValueError("unsupported notification channel")
    prefix = "notification_policy:" + ("bark:" if channel == "bark" else "")
    value = store.get_meta(prefix + protocol, {}).get("activated_ms")
    return value if type(value) is int and value >= 0 else None


def channel_enabled(store, channel):
    return any(activation(store, channel, p) is not None
               for p in (MODEL_PROTOCOL, DIRECT_POLICY, JOINT_POLICY, V130_POLICY))


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


def model_candidate_error(store, event, now, channel):
    """Check whether a raw V12.8 event may enter the independent YOLO stage.

    Candidate work follows the confirmation topic's channel, timeframe and
    subscription cutovers. It must not depend on whether the raw-start topic
    is enabled for that same channel.
    """
    if channel not in ("telegram", "bark"):
        raise ValueError("unsupported notification channel")
    if not is_direct_start(event):
        return "not_model_confirmed_signal"
    timeframe = event["timeframe"]
    if channel == "bark" and timeframe not in BARK_TIMEFRAMES:
        return "bark_timeframe_muted_by_owner"
    close = event["bar_close_ms"]
    since = activation(store, channel, MODEL_PROTOCOL)
    if since is None or close <= since:
        return "before_bark_activation" if channel == "bark" else "before_notification_policy_activation"
    timeframe_since = store.timeframe_activation(timeframe, protocol=MODEL_PROTOCOL)
    if timeframe_since is None or close <= timeframe_since:
        return "before_timeframe_activation"
    if not 0 <= now - close <= FRESH_MS:
        return "signal_expired"
    # Project only the immutable raw close needed by the shared route gate.
    # This is a policy check, not a fabricated or journaled model event.
    route_event = {"protocol": MODEL_PROTOCOL, "kind": MODEL_KIND, "bar_close_ms": close,
                   "indicator": {"bar_close_ms": close}}
    return _routing_error(store, route_event, now, channel)


def delivery_error(store, event, now, channel):
    """Return a stable rejection code, or None for a currently deliverable leg."""
    if is_joint_event(event):
        protocol = JOINT_POLICY
        closes = (event["bar_close_ms"],)
        detected = event["detected_at_ms"]
        if not (closes[0] <= detected <= now and now - closes[0] <= FRESH_MS and now - detected <= FRESH_MS):
            return "signal_expired"
        since = activation(store, channel, protocol)
        if since is None or closes[0] <= since:
            return "before_bark_activation" if channel == "bark" else "before_notification_policy_activation"
        # Joint streams have per-channel timeframes.  Old V9 stores retain
        # their existing shared-timeframe interface and never take this path.
        timeframe_since = store.timeframe_activation(event["timeframe"], protocol=protocol, channel=channel)
        if timeframe_since is None or closes[0] <= timeframe_since:
            return "before_timeframe_activation"
        return _routing_error(store, event, now, channel)
    if event.get("protocol") == V130_PROTOCOL or event.get("kind") == V130_KIND:
        if channel not in ("telegram", "bark"):
            raise ValueError("unsupported notification channel")
        if not is_v130_signal(event):
            return "not_v130_retest_signal"
        close = event["bar_close_ms"]
        detected = event["detected_at_ms"]
        # Causality, freshness and channel cutovers are anchored only to the
        # final closed retest confirmation.  The anchor may be hours older.
        if not close <= detected <= now or not 0 <= now - close <= FRESH_MS:
            return "signal_expired"
        since = activation(store, channel, V130_POLICY)
        if since is None or close <= since:
            return "before_notification_policy_activation"
        timeframe_since = store.timeframe_activation(event["timeframe"], protocol=V130_POLICY)
        if timeframe_since is None or close <= timeframe_since:
            return "before_timeframe_activation"
        return _routing_error(store, event, now, channel)
    # Recheck at the sender boundary: a durable queue may predate withdrawal.
    if is_v1_short_display_signal(event):
        return "short_display_only"
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
    return _routing_error(store, event, now, channel)


def _routing_error(store, event, now, channel):
    """Apply the shared topic subscription and its channel-specific cutover."""
    from yoyo.monitor.notification_center import routing_error

    return routing_error(store, event, now, channel)


# Retain the import name for older offline fixtures; policy identity is V12.8.
arm_v1_bark = arm_v9_bark
