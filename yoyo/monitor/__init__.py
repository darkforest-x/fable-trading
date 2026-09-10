"""Owner-authorized, notification-only SPIKE Burst V1 monitor."""

VERSION = "1.13.0"
SIGNAL_PROTOCOL = "spike-burst-v1-monitor-v1"
SIGNAL_KIND = "spike_burst_v1"
MODEL_PROTOCOL = "spike-burst-v1-yolo-confirmation-v1"
MODEL_KIND = "yolo_confirmed"
DIRECT_POLICY = "spike-burst-v1-raw-notifications-v1"
BARK_TIMEFRAMES = ("30m", "1H", "4H")
DIRECT_TIMEFRAMES = BARK_TIMEFRAMES
MODEL_MAX_WAIT = 9
MODEL_PROFILE_ID = "owner-grade-v1-w18w19-post2to9-wait9"
MODEL_SHA256 = "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"
TV_PROFILE_ID = "spike-burst-v1-default-long-only"
FRESH_MS = 30 * 60 * 1000
TIMEFRAMES = {"30m": 1800000, "1H": 3600000, "4H": 14400000}
MONITORED_TIMEFRAMES = tuple(TIMEFRAMES)
HIGHER_TIMEFRAME = {tf: tf for tf in TIMEFRAMES}
TV_INTERVALS = {"30m": "30", "1H": "60", "4H": "240"}
TIMEFRAME_OFFSETS = {}


def candle_open_ms(timestamp, timeframe):
    period = TIMEFRAMES[timeframe]
    return timestamp // period * period


def timeframe_label(timeframe):
    return timeframe
