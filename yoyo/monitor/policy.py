"""Validation for frozen SPIKE V1 raw signals and YOLO-extra records."""
import math
import re

from yoyo.monitor import (SIGNAL_KIND, SIGNAL_PROTOCOL, MODEL_KIND,
                          MODEL_PROTOCOL, MODEL_PROFILE_ID, MODEL_SHA256,
                          MODEL_MAX_WAIT, TIMEFRAMES, MONITORED_TIMEFRAMES)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def is_tv_start(event):
    """Compatibility name: validate a closed, raw, long-only V1 signal."""
    return (event.get("protocol") == SIGNAL_PROTOCOL and event.get("kind") == SIGNAL_KIND
            and event.get("source") == "live" and event.get("confirmation") == "raw"
            and event.get("direction") == "long" and event.get("side") == "long"
            and event.get("confirmed") is True and event.get("is_closed") is True
            and finite(event.get("price")) and event["price"] > 0
            and finite(event.get("risk")) and event["risk"] > 0)


def is_model_signal(event):
    """Validate the frozen confirmation proof, using no future price/label.

    The raw V1 event is immutable. Core/window timestamps and score come from
    a closed endpoint e in p..p+9. Freshness uses e's close downstream;
    checked_at_ms records when local inference completed.
    """
    original, model = event.get("indicator"), event.get("model")
    if not isinstance(original, dict) or not isinstance(model, dict) or not is_tv_start(original):
        return False
    if (event.get("protocol") != MODEL_PROTOCOL or event.get("kind") != MODEL_KIND
            or event.get("source") != "live" or event.get("confirmation") != "yolo"
            or event.get("direction") != "long" or event.get("side") != "long"
            or model.get("status") != "confirmed" or model.get("protocol") != MODEL_PROTOCOL
            or model.get("side") != event.get("side")
            or not isinstance(model.get("detection_id"), str) or not model["detection_id"]
            or not isinstance(model.get("input_pixel_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", model["input_pixel_sha256"])
            or model.get("profile_id") != MODEL_PROFILE_ID or model.get("model_sha256") != MODEL_SHA256
            or event.get("timeframe") not in MONITORED_TIMEFRAMES
            or any(event.get(k) != original.get(k) for k in ("symbol", "timeframe", "timeframe_min", "side", "source_sha256"))
            or not finite(event.get("price")) or event["price"] <= 0
            or not finite(model.get("confidence")) or not .25 <= model["confidence"] <= 1):
        return False
    if (model.get("max_wait_bars") != MODEL_MAX_WAIT or
            not all(type(model.get(k)) is int for k in
                    ("max_wait_bars", "wait_bars", "window_len", "core_length_bars", "post_bars"))):
        return False
    timestamps = [event.get("bar_open_ms"), event.get("bar_close_ms"),
                  original.get("bar_open_ms"), original.get("bar_close_ms"),
                  model.get("window_start_ms"), model.get("window_end_ms"),
                  model.get("core_start_ms"), model.get("core_end_ms"),
                  model.get("confirmation_close_ms"), model.get("checked_at_ms")]
    if not all(type(t) is int and t >= 0 for t in timestamps):
        return False
    step = TIMEFRAMES[event["timeframe"]]
    p, end, a, b = original["bar_open_ms"], event["bar_open_ms"], model["core_start_ms"], model["core_end_ms"]
    return (p % step == 0 and end % step == 0
            and original["bar_close_ms"] == p + step and event["bar_close_ms"] == end + step
            and 0 <= end - p <= MODEL_MAX_WAIT * step and (end - p) % step == 0
            and model.get("wait_bars") == (end - p) // step
            and model.get("window_len") in (18, 19) and model["window_end_ms"] == end
            and model["window_start_ms"] == end - (model["window_len"] - 1) * step
            and model["window_start_ms"] <= a <= b <= end
            and (a - end) % step == 0 and (b - a) % step == 0
            and model.get("core_length_bars") == (b - a) // step + 1
            and model["core_length_bars"] in (4, 5)
            and model.get("post_bars") == (end - b) // step and 2 <= model["post_bars"] <= 9
            and a <= p and a <= b <= end
            and model["confirmation_close_ms"] == event["bar_close_ms"]
            and model["checked_at_ms"] >= event["bar_close_ms"])
