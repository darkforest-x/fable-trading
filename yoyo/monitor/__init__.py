"""Owner-authorized, notification-only IMACD monitor, separate from execution."""

VERSION = "1.12.0"
SIGNAL_PROTOCOL = "imacd-tv-visible-start-monitor-v3"
SIGNAL_KIND = "tv_start"
MODEL_PROTOCOL = "imacd-yolo-confirmation-monitor-v1"
MODEL_KIND = "yolo_confirmed"
# Delivery policy identity only: raw event/Pine identity remains SIGNAL_PROTOCOL.
DIRECT_POLICY = "imacd-direct-start-notifications-v1"
# Direct arrows remain visible independently of permission to send Bark.
BARK_TIMEFRAMES = ("1H", "4H", "1Dutc")
DIRECT_TIMEFRAMES = ("5m", "15m", "30m", "1H", "4H", "1Dutc")
MODEL_PROFILE_ID = "owner-grade-v1-w18w19-post2to9-wait9"
MODEL_SHA256 = "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"
MODEL_MAX_WAIT = 9
TV_PROFILE_ID = "imacd-v2.2-focus12-band0.10-marks-off"
FRESH_MS = 30 * 60 * 1000
TIMEFRAMES = {"5m": 300000, "15m": 900000, "30m": 1800000, "1H": 3600000, "2H": 7200000, "4H": 14400000, "1Dutc": 86400000, "1Wutc": 604800000}
HIGHER_TIMEFRAME = {"5m": "1H", "15m": "1H", "30m": "2H", "1H": "4H", "4H": "1Dutc", "1Dutc": "1Wutc"}
MONITORED_TIMEFRAMES = tuple(HIGHER_TIMEFRAME)
TV_INTERVALS = {"5m": "5", "15m": "15", "30m": "30", "1H": "60", "4H": "240", "1Dutc": "1D"}
# Native UTC week opens Monday, four days after the Thursday Unix epoch.
# Daily closes remain UTC midnight (08:00 Beijing), matching the existing HTF.
TIMEFRAME_OFFSETS = {"1Wutc": 4 * 86400000}


def candle_open_ms(timestamp, timeframe):
    """Floor a timestamp onto the native OKX opening grid; no future data."""
    offset = TIMEFRAME_OFFSETS.get(timeframe, 0)
    period = TIMEFRAMES[timeframe]
    return (timestamp - offset) // period * period + offset


def timeframe_label(timeframe):
    return {"1Dutc": "日线", "1Wutc": "周线"}.get(timeframe, timeframe)
