"""Owner-authorized, notification-only IMACD monitor, separate from execution."""

VERSION = "1.0.0"
SIGNAL_PROTOCOL = "imacd-pine-v2.2-default-monitor-v1"
FRESH_MS = 30 * 60 * 1000
TIMEFRAMES = {"1H": 3600000, "4H": 14400000, "1Dutc": 86400000}
