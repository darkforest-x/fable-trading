"""Owner's 2026-09-08 notification contract: first confirmed departure from zero.

Uses only previous/current IMACD values and the preceding exact-zero run.
MA density, near-zero ATR zones, signal-line crosses and HTF are annotations.
The independent envelope check also prevents legacy queued events from being
sent after a software update. No price outcome or future candle is read.
"""
import math

from yoyo.monitor import SIGNAL_KIND, SIGNAL_PROTOCOL


def is_zero_breakout(event):
    md, previous = event.get("md"), event.get("previous_md")
    count = event.get("zero_bars")
    return (event.get("protocol") == SIGNAL_PROTOCOL
            and event.get("kind") == SIGNAL_KIND
            and event.get("confirmed") is True
            and type(md) in (int, float) and math.isfinite(md) and md != 0
            and type(previous) in (int, float) and previous == 0
            and type(count) is int and count >= 1
            and event.get("side") == ("long" if md > 0 else "short"))
