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

import logging
from pathlib import Path
import re
import subprocess
import sys
import threading
from urllib.parse import urlencode

INTERVALS = {"15m": "15", "1H": "60", "4H": "240"}
SCRIPT = Path(__file__).with_suffix(".applescript")
_OPEN_LOCK = threading.Lock()
LOG = logging.getLogger("spike.tradingview")
_STAGES = frozenset({"activate", "accessibility", "window_ready", "menu_button",
                     "menu_items", "dispatch", "clipboard", "layout"})
_REASONS = frozenset({"SPIKE_LAYOUT_UNAVAILABLE", "SPIKE_CLIPBOARD_CHANGED",
                     "SPIKE_DEADLINE", "SPIKE_MENU_UNAVAILABLE", "SPIKE_AX_UNAVAILABLE",
                     "SPIKE_INVALID_URL"})
_STAGE_ERROR = re.compile(
    r"SPIKE_STAGE=([a-z_]+);SPIKE_CODE=(-?\d{1,6});SPIKE_REASON=([A-Z_]+)(?![A-Z0-9_])"
)
_LEGACY_CODE = re.compile(r"(?<![\w-])(-1743|-25211|-1712|-1719|-1728)\)?\s*$")


def _failure_details(stderr: str) -> tuple[str, int | None, str]:
    """Extract only bounded codes and allowlisted metadata, never UI text.

    The AppleScript stage contract distinguishes stale AX window references
    (-1719/-1728) from actual macOS permission denials (-1743/-25211).
    Legacy OS codes remain recognized during an in-place script update.
    """
    match = _STAGE_ERROR.search(stderr)
    if match:
        stage, code, reason = match.groups()
        return (stage if stage in _STAGES else "unknown", int(code),
                reason if reason in _REASONS else "unknown")
    code = _LEGACY_CODE.search(stderr)
    reason = next((marker for marker in _REASONS
                   if re.search(rf"\b{marker}\b", stderr)), "unknown")
    return "unknown", int(code.group(1)) if code else None, reason


def _failure_message(stage: str, code: int | None, reason: str) -> str:
    """Translate trusted diagnostics into fixed, actionable Chinese messages."""
    if code in {-1743, -25211}:
        return "macOS 尚未允许操作 TradingView。请在系统设置 → 隐私与安全性中，允许弹窗所示程序的辅助功能／自动化，再重试。"
    if reason == "SPIKE_LAYOUT_UNAVAILABLE":
        return "TradingView 图表布局配置不可用。请先保存并配置要使用的图表布局，再重试。"
    if reason == "SPIKE_CLIPBOARD_CHANGED":
        return "剪贴板内容已被其他操作更改，本次未继续打开图表。请重新点击卡片重试。"
    if code == -1712 or reason == "SPIKE_DEADLINE":
        return "调用 TradingView 超时，尚无法确认本次打开结果。请先查看应用当前图表，稍后重试。"
    if code in {-1719, -1728}:
        return "TradingView 界面尚未就绪或窗口已发生变化。请等待应用加载完成、关闭遮挡弹窗后重试。"
    if reason == "SPIKE_MENU_UNAVAILABLE" or stage in {"menu_button", "menu_items"}:
        return "无法读取 TradingView 的打开链接菜单。请关闭应用内弹窗、确认主窗口可操作后重试。"
    if stage == "layout":
        return "TradingView 图表布局配置不可用。请先保存并配置要使用的图表布局，再重试。"
    if stage == "clipboard":
        return "无法读取或临时设置剪贴板，本次未继续打开图表。请检查剪贴板是否可用后重试。"
    if reason == "SPIKE_AX_UNAVAILABLE" or stage in {"activate", "accessibility", "window_ready", "dispatch"}:
        return "TradingView 界面尚未就绪或窗口已发生变化。请等待应用加载完成、关闭遮挡弹窗后重试。"
    return "未能打开 TradingView 图表。请确认 Mac 已解锁、TradingView 已安装并登录，再重试；也可使用网页版入口。"


def _report_failure(stage: str, code: int | None, reason: str) -> DesktopOpenError:
    # No raw stderr/stdout, exception text, clipboard or chart identity in logs.
    LOG.warning("TradingView open failed: stage=%s code=%s reason=%s", stage,
                code if code is not None else "unknown", reason)
    return DesktopOpenError(_failure_message(stage, code, reason))


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
            raise _report_failure(*_failure_details(result.stderr))
        return {"requested": True, "symbol": symbol, "timeframe": timeframe}
    except subprocess.TimeoutExpired:
        raise _report_failure("dispatch", -1712, "SPIKE_DEADLINE") from None
    except OSError:
        raise _report_failure("activate", None, "unknown") from None
    finally:
        _OPEN_LOCK.release()
