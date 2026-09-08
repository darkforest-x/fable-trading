"""Owner-authorized Mac chart navigation, separate from monitoring and trading.

TradingView Desktop 3.4.0 on this Mac registers a login URL scheme but has no
chart deep-link handler. Its documented Mac entry point is Open link from
clipboard: https://www.tradingview.com/support/solutions/43000708221-how-in-app-link-handling-works-in-tradingview-desktop/
Owner explicitly authorized this AppleScript bridge on 2026-09-08. It targets
only that menu, receives a constructed OKX URL as argv (never executable source),
and attempts to restore the previous clipboard unless the user has copied
something else. Ordinary script errors clean up; an OS/process crash cannot
guarantee clipboard restoration.
No signal rule, model, notification or order execution is involved.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
import threading
from urllib.parse import urlencode

INTERVALS = {"15m": "15", "1H": "60", "4H": "240"}
SCRIPT = Path(__file__).with_suffix(".applescript")
_OPEN_LOCK = threading.Lock()


class DesktopOpenError(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


def chart_url(symbol: str, timeframe: str) -> str:
    """Only accept an OKX swap identity and one of the monitored timeframes."""
    match = re.fullmatch(r"([A-Z0-9]{1,30})-([A-Z0-9]{2,10})-SWAP", symbol)
    if not match or timeframe not in INTERVALS:
        raise DesktopOpenError("合约或周期无效；支持 OKX 永续的 15m、1H、4H。", 400)
    query = urlencode({"symbol": f"OKX:{match[1]}{match[2]}.P", "interval": INTERVALS[timeframe]})
    return "https://www.tradingview.com/chart/?" + query


def open_chart(symbol: str, timeframe: str) -> dict:
    url = chart_url(symbol, timeframe)
    if sys.platform != "darwin":
        raise DesktopOpenError("一键打开需要在安装 TradingView 的 Mac 上运行。")
    if not _OPEN_LOCK.acquire(blocking=False):
        raise DesktopOpenError("上一张图还在打开，请稍后再试。", 409)
    try:
        result = subprocess.run(["/usr/bin/osascript", str(SCRIPT), url],
                                capture_output=True, text=True, timeout=25, check=False)
        if result.returncode or result.stdout.strip() != "requested":
            # Never return raw AppleScript output or clipboard contents to the API.
            permission_error = any(code in result.stderr for code in ("-1743", "-1719", "-25211", "SPIKE_PERMISSION"))
            if permission_error:
                raise DesktopOpenError("macOS 尚未允许操作 TradingView。请在系统设置 → 隐私与安全性中，允许弹窗所示程序的辅助功能／自动化，再重试。")
            if "-1712" in result.stderr:
                raise DesktopOpenError("macOS 自动化调用超时。请检查系统设置 → 隐私与安全性 → 自动化中 caffeinate → System Events 已打开，并允许辅助功能，再重试。")
            raise DesktopOpenError("未能打开 TradingView 图表。请确认 Mac 已解锁、TradingView 已安装并登录，再重试；也可使用网页版入口。")
        return {"requested": True, "symbol": symbol, "timeframe": timeframe}
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DesktopOpenError("TradingView 打开超时或不可用，请查看 Mac 上的应用和权限提示后重试。") from error
    finally:
        _OPEN_LOCK.release()
