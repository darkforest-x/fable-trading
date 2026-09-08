"""Synthetic causal notification proof; no market data or model inference."""
from yoyo.monitor import (MODEL_KIND, MODEL_PROTOCOL, MODEL_PROFILE_ID,
                          MODEL_SHA256, MODEL_MAX_WAIT, SIGNAL_KIND, SIGNAL_PROTOCOL,
                          TIMEFRAMES, TV_PROFILE_ID)
from yoyo.monitor.store import Store

CONFIRM = 86_400_000


def model_event(*, close=None, timeframe="1H", side="long", wait=2,
                price=100.5, arrow_price=99.5, near_zero_bars=12,
                original_changes=None, **changes):
    step = TIMEFRAMES[timeframe]
    close = max(CONFIRM, 24 * step) if close is None else close
    endpoint = close - step
    arrow = endpoint - wait * step
    original = dict(protocol=SIGNAL_PROTOCOL, kind=SIGNAL_KIND, symbol="TEST-USDT-SWAP",
                    timeframe=timeframe, side=side, price=arrow_price,
                    bar_open_ms=arrow, bar_close_ms=arrow + step, detected_at_ms=arrow + step + 1,
                    near_zero_bars=near_zero_bars, focus_start_ms=arrow - near_zero_bars * step,
                    md=.2 if side == "long" else -.2, sb=.02 if side == "long" else -.02,
                    previous_md=.05, previous_sb=.08, focus_band=.1, confirmed=True, ready=True,
                    focus_qualified_before=True, tv_marker_visible=True, tv_show_focus=True,
                    tv_show_marks=False, source_kind="release", tv_marker="focus_release",
                    tv_profile=TV_PROFILE_ID, dense=False, htf_allowed=False)
    original.update(original_changes or {})
    core_end = endpoint - max(2, wait + 1) * step
    core_start = core_end - 3 * step
    proof = dict(status="confirmed", protocol=MODEL_PROTOCOL, profile_id=MODEL_PROFILE_ID,
                 model_sha256=MODEL_SHA256, confidence=.65,
                 side=side, detection_id="synthetic-detection", input_pixel_sha256="a" * 64,
                 max_wait_bars=MODEL_MAX_WAIT,
                 core_start_ms=core_start, core_end_ms=core_end,
                 window_start_ms=endpoint - 17 * step, window_end_ms=endpoint,
                 window_len=18, core_length_bars=4,
                 post_bars=(endpoint - core_end) // step, overlap_bars=4, wait_bars=wait,
                 confirmation_close_ms=close, checked_at_ms=close + 1000)
    result = dict(protocol=MODEL_PROTOCOL, kind=MODEL_KIND, symbol=original["symbol"],
                  timeframe=timeframe, side=side, price=price, bar_open_ms=endpoint,
                  bar_close_ms=close, detected_at_ms=close + 1000,
                  near_zero_bars=near_zero_bars, source_event_id=Store.event_id(original),
                  indicator=original, model=proof)
    result.update(changes)
    return result
