"""Notifications follow the owner's visible TradingView marker, not raw md.

Observed in the actual OKX ETH 4H chart on 2026-09-08: showFocus=true,
focusMinBars=12, focusAtrBand=.10, showMarks=false. The visible price label
is the confirmed focusRelease branch in Pine V2.2. Previous md may already
be nonzero; both previous lines must still be inside the qualified frozen
band. Current md must strictly leave it. No future price is used.
"""
import math
import re

from yoyo.monitor import (SIGNAL_KIND, SIGNAL_PROTOCOL, TV_PROFILE_ID, MODEL_KIND,
                          MODEL_PROTOCOL, MODEL_PROFILE_ID, MODEL_SHA256,
                          MODEL_MAX_WAIT, TIMEFRAMES, MONITORED_TIMEFRAMES)


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def is_tv_start(event):
    md, previous, previous_sb = event.get("md"), event.get("previous_md"), event.get("previous_sb")
    band, count = event.get("focus_band"), event.get("near_zero_bars")
    return (event.get("protocol") == SIGNAL_PROTOCOL
            and event.get("kind") == SIGNAL_KIND
            and event.get("source_kind") == "release"
            and event.get("tv_marker") == "focus_release"
            and event.get("tv_profile") == TV_PROFILE_ID
            and event.get("confirmed") is True and event.get("ready") is True
            and event.get("focus_qualified_before") is True
            and event.get("tv_marker_visible") is True
            and event.get("tv_show_focus") is True and event.get("tv_show_marks") is False
            and finite(band) and band >= 0
            and finite(md) and abs(md) > band
            and finite(previous) and finite(previous_sb)
            and max(abs(previous), abs(previous_sb)) <= band
            and type(count) is int and count >= 12
            and event.get("side") == ("long" if md > 0 else "short"))


def is_model_signal(event):
    """Validate the frozen confirmation proof, using no future price/label.

    Original IMACD fields come from arrow p. Core/window timestamps and score
    come from closed endpoint e in p..p+9. Freshness uses e's close downstream;
    checked_at_ms records when the local worker actually performed inference.
    """
    original, model = event.get("indicator"), event.get("model")
    if not isinstance(original, dict) or not isinstance(model, dict) or not is_tv_start(original):
        return False
    if (event.get("protocol") != MODEL_PROTOCOL or event.get("kind") != MODEL_KIND
            or model.get("status") != "confirmed" or model.get("protocol") != MODEL_PROTOCOL
            or model.get("side") != event.get("side")
            or not isinstance(model.get("detection_id"), str) or not model["detection_id"]
            or not isinstance(model.get("input_pixel_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", model["input_pixel_sha256"])
            or model.get("profile_id") != MODEL_PROFILE_ID or model.get("model_sha256") != MODEL_SHA256
            or event.get("timeframe") not in MONITORED_TIMEFRAMES
            or any(event.get(k) != original.get(k) for k in ("symbol", "timeframe", "side", "near_zero_bars"))
            or not finite(event.get("price")) or event["price"] <= 0
            or not finite(model.get("confidence")) or not .25 <= model["confidence"] <= 1):
        return False
    if (model.get("max_wait_bars") != MODEL_MAX_WAIT or
            not all(type(model.get(k)) is int for k in
                    ("max_wait_bars", "wait_bars", "window_len", "core_length_bars", "post_bars", "overlap_bars"))):
        return False
    timestamps = [event.get("bar_open_ms"), event.get("bar_close_ms"),
                  original.get("bar_open_ms"), original.get("bar_close_ms"), original.get("focus_start_ms"),
                  model.get("window_start_ms"), model.get("window_end_ms"),
                  model.get("core_start_ms"), model.get("core_end_ms"),
                  model.get("confirmation_close_ms"), model.get("checked_at_ms")]
    if not all(type(t) is int and t >= 0 for t in timestamps):
        return False
    step = TIMEFRAMES[event["timeframe"]]
    p, end, a, b = original["bar_open_ms"], event["bar_open_ms"], model["core_start_ms"], model["core_end_ms"]
    setup = original["focus_start_ms"]
    return (p % step == 0 and end % step == 0 and setup % step == 0
            and original["bar_close_ms"] == p + step and event["bar_close_ms"] == end + step
            and setup == p - original["near_zero_bars"] * step
            and 0 <= end - p <= MODEL_MAX_WAIT * step and (end - p) % step == 0
            and model.get("wait_bars") == (end - p) // step
            and model.get("window_len") in (18, 19) and model["window_end_ms"] == end
            and model["window_start_ms"] == end - (model["window_len"] - 1) * step
            and model["window_start_ms"] <= a <= b <= end
            and (a - end) % step == 0 and (b - a) % step == 0
            and model.get("core_length_bars") == (b - a) // step + 1
            and model["core_length_bars"] in (4, 5)
            and model.get("post_bars") == (end - b) // step and 2 <= model["post_bars"] <= 9
            and min(b, p) >= max(a, setup)
            and model.get("overlap_bars") == (min(b, p) - max(a, setup)) // step + 1
            and model["confirmation_close_ms"] == event["bar_close_ms"]
            and model["checked_at_ms"] >= event["bar_close_ms"])
