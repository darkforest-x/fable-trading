"""Owner-authorized, notification-only IMACD monitor, separate from execution."""

VERSION = "1.5.0"
SIGNAL_PROTOCOL = "imacd-tv-visible-start-monitor-v3"
SIGNAL_KIND = "tv_start"
TV_PROFILE_ID = "imacd-v2.2-focus12-band0.10-marks-off"
FRESH_MS = 30 * 60 * 1000
TIMEFRAMES = {"15m": 900000, "1H": 3600000, "4H": 14400000, "1Dutc": 86400000}
HIGHER_TIMEFRAME = {"15m": "1H", "1H": "4H", "4H": "1Dutc"}
MONITORED_TIMEFRAMES = tuple(HIGHER_TIMEFRAME)
TV_INTERVALS = {"15m": "15", "1H": "60", "4H": "240"}
