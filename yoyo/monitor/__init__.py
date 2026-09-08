"""Owner-authorized, notification-only IMACD monitor, separate from execution."""

VERSION = "1.6.0"
SIGNAL_PROTOCOL = "imacd-tv-visible-start-monitor-v3"
SIGNAL_KIND = "tv_start"
MODEL_PROTOCOL = "imacd-yolo-confirmation-monitor-v1"
MODEL_KIND = "yolo_confirmed"
MODEL_PROFILE_ID = "owner-grade-v1-w18w19-post2to9-wait9"
MODEL_SHA256 = "862705b999594355c1133640acc540f4de19b561889e89d9e050ddad5c6db838"
MODEL_MAX_WAIT = 9
TV_PROFILE_ID = "imacd-v2.2-focus12-band0.10-marks-off"
FRESH_MS = 30 * 60 * 1000
TIMEFRAMES = {"15m": 900000, "1H": 3600000, "4H": 14400000, "1Dutc": 86400000}
HIGHER_TIMEFRAME = {"15m": "1H", "1H": "4H", "4H": "1Dutc"}
MONITORED_TIMEFRAMES = tuple(HIGHER_TIMEFRAME)
TV_INTERVALS = {"15m": "15", "1H": "60", "4H": "240"}
