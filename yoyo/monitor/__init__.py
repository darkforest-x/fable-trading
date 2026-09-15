"""Owner-authorized, notification-only SPIKE V9 monitor."""

VERSION = "1.15.0"
SIGNAL_PROTOCOL = "spike-burst-v9-monitor-v1"
SIGNAL_KIND = "spike_burst_v9"
SHORT_SIGNAL_PROTOCOL = "spike-burst-v1-short-monitor-v1"
# Legacy offline V1 short records retain their own identity; V9 uses the
# current SIGNAL_PROTOCOL for both directions.
SHORT_SIGNAL_KIND = "spike_burst_v1"
SHORT_DISPLAY_CUTOVER_KEY = "display_policy:spike-v1-short"
MODEL_PROTOCOL = "spike-burst-v9-yolo-confirmation-v1"
MODEL_KIND = "yolo_confirmed"
DIRECT_POLICY = "spike-burst-v9-raw-notifications-v1"
BARK_ARM_KEY = "notification_policy:v9_bark_arm"
V9_RESET_KEY = "migration:spike-v9-reset-v1"
BARK_TIMEFRAMES = ("15m", "30m", "1H", "4H")
DIRECT_TIMEFRAMES = BARK_TIMEFRAMES
MODEL_MAX_WAIT = 9
MODEL_PROFILE_ID = "owner-grade-v1-w18w19-post2to9-wait9"
MODEL_SHA256 = "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"
TV_PROFILE_ID = "spike-burst-v9-both-directions"
TV_SHORT_PROFILE_ID = "spike-burst-v1-pine-short-setting"
FRESH_MS = 30 * 60 * 1000
TIMEFRAMES = {"15m": 900000, "30m": 1800000, "1H": 3600000, "4H": 14400000}
MONITORED_TIMEFRAMES = tuple(TIMEFRAMES)
HIGHER_TIMEFRAME = {tf: tf for tf in TIMEFRAMES}
TV_INTERVALS = {"15m": "15", "30m": "30", "1H": "60", "4H": "240"}
TIMEFRAME_OFFSETS = {}


def candle_open_ms(timestamp, timeframe):
    period = TIMEFRAMES[timeframe]
    return timestamp // period * period


def timeframe_label(timeframe):
    return timeframe
