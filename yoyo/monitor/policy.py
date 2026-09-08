"""Notifications follow the owner's visible TradingView marker, not raw md.

Observed in the actual OKX ETH 4H chart on 2026-09-08: showFocus=true,
focusMinBars=12, focusAtrBand=.10, showMarks=false. The visible price label
is the confirmed focusRelease branch in Pine V2.2. Previous md may already
be nonzero; both previous lines must still be inside the qualified frozen
band. Current md must strictly leave it. No future price is used.
"""
import math

from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL, TV_PROFILE_ID


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
