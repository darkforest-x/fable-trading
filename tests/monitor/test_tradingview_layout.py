"""Exercise pure AppleScript URL handlers, without starting apps or reading UI.

The concrete saved layout and regional host must survive the desktop redirect;
only the validated request query may replace its existing symbol/interval.
"""
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="AppleScript is macOS-only")
SCRIPT = Path(__file__).resolve().parents[2] / "yoyo/monitor/tradingview.applescript"


@pytest.fixture(scope="module")
def handlers(tmp_path_factory):
    directory = tmp_path_factory.mktemp("chart-layout")
    compiled = directory / "bridge.scpt"
    subprocess.run(["osacompile", "-o", str(compiled), str(SCRIPT)], check=True, capture_output=True)
    driver = directory / "driver.applescript"
    driver.write_text('''on run argv
    set bridge to load script (POSIX file (item 1 of argv))
    set layoutBase to bridge's savedChartBase(item 2 of argv)
    if layoutBase is missing value then return "NONE"
    if (count of argv) is 2 then return layoutBase
    return bridge's bindToLayout(item 3 of argv, layoutBase)
end run
''')
    def invoke(active, request=None):
        arguments = ["osascript", str(driver), str(compiled), active]
        if request is not None:
            arguments.append(request)
        return subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=5).stdout.strip()
    return invoke


@pytest.mark.parametrize("active,expected", [
    ("https://cn.tradingview.com/chart/Ab12Cd34/?symbol=OLD&interval=30", "https://cn.tradingview.com/chart/Ab12Cd34/"),
    ("https://www.tradingview.com/chart/QwEr1234/", "https://www.tradingview.com/chart/QwEr1234/"),
    ("https://tradingview.com/chart/aBc123#section", "https://tradingview.com/chart/aBc123/"),
    ("https://www.tradingview.com/chart/", "NONE"),
    ("https://cn.tradingview.com.evil.test/chart/Ab12Cd34/", "NONE"),
    ("file:///Applications/TradingView.app/window/index.html", "NONE"),
    ("http://cn.tradingview.com/chart/Ab12Cd34/", "NONE"),
    ("https://cn.tradingview.com/chart/../../elsewhere", "NONE"),
    ("https://cn.tradingview.com/chart/" + "a" * 65 + "/", "NONE"),
])
def test_only_concrete_tradingview_layouts(handlers, active, expected):
    assert handlers(active) == expected


@pytest.mark.parametrize("symbol,interval", [("BEATUSDT.P", "15"), ("ETHUSDT.P", "60"), ("BTCUSD.P", "240")])
def test_preserve_exact_request_query_after_layout_binding(handlers, symbol, interval):
    query = f"symbol=OKX%3A{symbol}&interval={interval}"
    assert handlers("https://cn.tradingview.com/chart/Ab12Cd34/?symbol=OLD&interval=30",
                    "https://www.tradingview.com/chart/?" + query) == "https://cn.tradingview.com/chart/Ab12Cd34/?" + query
