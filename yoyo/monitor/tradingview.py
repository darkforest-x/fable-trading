"""Owner-authorized Mac chart navigation, separate from trading and alerts.

TradingView Desktop does not apply chart query parameters through its registered
login URL scheme. The bridge binds each validated request to the owner's saved
layout and uses Desktop's "Open link from clipboard" entry point. It reports
success only after the chart canvas exposes the requested symbol and interval.
No signal rule, model, notification, or order execution is involved.
"""
from __future__ import annotations

import logging
from pathlib import Path
import re
import subprocess
import sys
import threading
from urllib.parse import urlencode

from yoyo.monitor import TV_INTERVALS

INTERVALS = TV_INTERVALS
SCRIPT = Path(__file__).with_suffix(".applescript")
_OPEN_LOCK = threading.Lock()
LOG = logging.getLogger("spike.tradingview")
_STAGES = frozenset({"arguments", "activate", "accessibility", "window_ready",
                     "verify_chart", "dispatch", "clipboard", "layout",
                     "menu_button", "menu_items"})
_REASONS = frozenset({"SPIKE_LAYOUT_UNAVAILABLE", "SPIKE_CLIPBOARD_CHANGED",
                     "SPIKE_DEADLINE", "SPIKE_MENU_UNAVAILABLE", "SPIKE_AX_UNAVAILABLE",
                     "SPIKE_CHART_MISMATCH", "SPIKE_INVALID_URL"})
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
    if reason == "SPIKE_CHART_MISMATCH" or stage == "verify_chart":
        return "TradingView 已接收链接，但主图未加载到目标合约和周期。请关闭应用内弹窗后重试。"
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


_TIMEFRAME_ALIASES = {
    "5": "5m", "5m": "5m",
    "15": "15m", "15m": "15m",
    "30": "30m", "30m": "30m",
    "60": "1H", "1h": "1H", "1H": "1H",
    "240": "4H", "4h": "4H", "4H": "4H",
    "1440": "1Dutc", "1d": "1Dutc", "1D": "1Dutc", "1Dutc": "1Dutc", "D": "1Dutc",
}
_ALL_INTERVALS = {**INTERVALS, "5m": "5", "1Dutc": "1D"}


def normalize_timeframe(timeframe: str) -> str:
    """Normalize API/UI aliases without widening the allowed TV intervals."""
    normalized = _TIMEFRAME_ALIASES.get(timeframe)
    if normalized is None:
        raise DesktopOpenError("合约或周期无效；支持 OKX 永续的 5m、15m、30m、1H、4H、日线。", 400)
    return normalized


def normalize_symbol(symbol: str) -> tuple[str, str, str]:
    """Return canonical OKX id, base and quote from card or TV identities."""
    value = symbol.strip().upper() if isinstance(symbol, str) else ""
    if value.startswith("OKX:"):
        value = value[4:]
    if value.endswith(".P"):
        value = value[:-2]
    match = re.fullmatch(r"([A-Z0-9]{1,30})-(USDT|USDC|USD)-SWAP", value)
    if match is None:
        match = re.fullmatch(r"([A-Z0-9]{1,30})(USDT|USDC|USD)", value)
    if match is None:
        raise DesktopOpenError("合约或周期无效；支持 OKX 永续的 5m、15m、30m、1H、4H、日线。", 400)
    base, quote = match.groups()
    return f"{base}-{quote}-SWAP", base, quote


def chart_url(symbol: str, timeframe: str) -> str:
    """Accept the bounded identities emitted by current and historical cards."""
    _, base, quote = normalize_symbol(symbol)
    normalized_timeframe = normalize_timeframe(timeframe)
    query = urlencode({"symbol": f"OKX:{base}{quote}.P", "interval": _ALL_INTERVALS[normalized_timeframe]})
    return "https://www.tradingview.com/chart/?" + query


def open_chart(symbol: str, timeframe: str) -> dict:
    url = chart_url(symbol, timeframe)
    canonical_symbol, base, quote = normalize_symbol(symbol)
    canonical_timeframe = normalize_timeframe(timeframe)
    if sys.platform != "darwin":
        raise DesktopOpenError("一键打开需要在安装 TradingView 的 Mac 上运行。")
    if not _OPEN_LOCK.acquire(blocking=False):
        raise DesktopOpenError("上一张图还在打开，请稍后再试。", 409)
    try:
        result = subprocess.run(["/usr/bin/osascript", str(SCRIPT), url,
                                 f"OKX:{base}{quote}.P", _ALL_INTERVALS[canonical_timeframe]],
                                capture_output=True, text=True, timeout=58, check=False)
        if result.returncode or result.stdout.strip() != "loaded":
            # Never return raw AppleScript output or clipboard contents to the API.
            raise _report_failure(*_failure_details(result.stderr))
        return {"requested": True, "verified": True,
                "symbol": canonical_symbol, "timeframe": canonical_timeframe}
    except subprocess.TimeoutExpired:
        raise _report_failure("dispatch", -1712, "SPIKE_DEADLINE") from None
    except OSError:
        raise _report_failure("activate", None, "unknown") from None
    finally:
        _OPEN_LOCK.release()
