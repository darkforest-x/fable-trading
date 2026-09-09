"""Open a chart on the Windows client, never on the Mac scanner.

TradingView Desktop 3.4.1 forwards the final HTTPS argv to openForwardedUrl
on Windows (both startup and second-instance). The installed Appx package is
resolved on each request so updates do not leave a stale executable path.
This uses the same concrete chart layout as the Mac bridge and does not touch
clipboard, keyboard, TradingView settings, signal rules, or order execution.
See docs/ops/SPIKE_WINDOWS_CLIENT.md for the verified version and sources.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import threading

from yoyo.monitor.tradingview import DesktopOpenError, chart_url

SCRIPT = Path(__file__).with_suffix(".ps1")
_OPEN_LOCK = threading.Lock()
_LAYOUT = re.compile(r"https://(?:cn\.|www\.)?tradingview\.com/chart/[A-Za-z0-9]{1,64}/?")


def bound_chart_url(symbol: str, timeframe: str, layout_path: Path) -> str:
    """Bind validated chart identity to an owner-configured saved layout."""
    url = chart_url(symbol, timeframe)
    try:
        layout = layout_path.read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeError):
        raise DesktopOpenError("Windows 图表布局配置缺失或编码无效，请重新安装 Spike 本机入口。") from None
    if not _LAYOUT.fullmatch(layout):
        raise DesktopOpenError("Windows 图表布局配置无效，请重新配置已保存的 TradingView 布局。")
    return layout.rstrip("/") + "/?" + url.split("?", 1)[1]


def open_chart(symbol: str, timeframe: str) -> dict:
    if sys.platform != "win32":
        raise DesktopOpenError("此入口需要在 3060 的 Windows 登录桌面中运行。")
    runtime = Path(os.environ["LOCALAPPDATA"]) / "Spike"
    url = bound_chart_url(symbol, timeframe, runtime / "tradingview-layout.txt")
    if not _OPEN_LOCK.acquire(blocking=False):
        raise DesktopOpenError("上一张图还在打开，请稍后再试。", 409)
    try:
        # No shell interpolation: the URL is one argv and contains only the
        # validated layout/identity above. No executable code comes from HTTP.
        result = subprocess.run(
            [str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"),
             "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), url],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode or result.stdout.strip() != "requested":
            if "SPIKE_INTERACTIVE_SESSION_REQUIRED" in result.stderr:
                raise DesktopOpenError("请登录并解锁 3060 的 Windows 桌面，再点击卡片。")
            if "SPIKE_TV_NOT_INSTALLED" in result.stderr:
                raise DesktopOpenError("3060 当前用户未安装 TradingView 桌面版。")
            raise DesktopOpenError("Windows TradingView 未接受打开请求，请先打开应用并登录，再重试。")
        return {"requested": True, "symbol": symbol, "timeframe": timeframe, "target": "windows"}
    except subprocess.TimeoutExpired:
        raise DesktopOpenError("Windows TradingView 打开请求超时；请先查看应用，再重试。") from None
    except OSError:
        raise DesktopOpenError("Windows 图表桥接程序不可用，请检查 Spike 本机入口。") from None
    finally:
        _OPEN_LOCK.release()
