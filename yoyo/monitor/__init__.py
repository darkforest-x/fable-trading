"""Owner-authorized, notification-only IMACD monitor, separate from execution."""

VERSION = "1.1.0"
SIGNAL_PROTOCOL = "imacd-zero-axis-monitor-v2"
SIGNAL_KIND = "zero_breakout"
FRESH_MS = 30 * 60 * 1000
TIMEFRAMES = {"1H": 3600000, "4H": 14400000, "1Dutc": 86400000}
