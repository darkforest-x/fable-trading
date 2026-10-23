"""Compile and exercise the real Windows URL parser without launching any UI."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
SOURCE = Path(__file__).resolve().parents[2] / "yoyo/monitor/windows_chart_link.cs"
LINK = "spike-tradingview://open?symbol=BTC-USDT-SWAP&timeframe=15m"
LAYOUT = "https://cn.tradingview.com/chart/Example123/"


@unittest.skipUnless(sys.platform == "win32", "Windows .NET Framework contract")
class WindowsChartLinkContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="spike-link-contract-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.exe = Path(cls.temporary.name) / "parse.exe"
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        if not compiler.exists():
            compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework/v4.0.30319/csc.exe"
        subprocess.run([str(compiler), "/nologo", "/target:exe", "/codepage:65001",
                        "/reference:System.Windows.Forms.dll", "/reference:System.Web.Extensions.dll",
                        "/out:" + str(cls.exe), str(SOURCE)], check=True, capture_output=True)

    def parse(self, link=LINK, layout=LAYOUT):
        return subprocess.run([str(self.exe), "--validate", link, layout], capture_output=True, text=True, timeout=5)

    def test_exact_contract_and_period_survive(self):
        for period, interval in [("5m", "5"), ("15m", "15"), ("30m", "30"), ("1H", "60"), ("4H", "240"), ("1Dutc", "1D")]:
            for symbol, ticker in [("BTC-USDT-SWAP", "BTCUSDT.P"), ("1000PEPE-USDT-SWAP", "1000PEPEUSDT.P"), ("ETH-USDC-SWAP", "ETHUSDC.P")]:
                with self.subTest(symbol=symbol, period=period):
                    response = self.parse(LINK.replace("BTC-USDT-SWAP", symbol).replace("15m", period))
                    self.assertEqual(response.returncode, 0)
                    self.assertEqual(json.loads(response.stdout), {"Symbol": symbol, "Timeframe": period,
                        "ChartUrl": LAYOUT + "?symbol=OKX%3A" + ticker + "&interval=" + interval})

    def test_browser_normalized_root_slash_preserves_requested_chart(self):
        # Captured from the failed Windows handler's actual command line.
        link = "spike-tradingview://open/?symbol=BZ-USDT-SWAP&timeframe=15m"
        response = self.parse(link)
        self.assertEqual(response.returncode, 0)
        self.assertEqual(json.loads(response.stdout), {"Symbol": "BZ-USDT-SWAP", "Timeframe": "15m",
            "ChartUrl": LAYOUT + "?symbol=OKX%3ABZUSDT.P&interval=15"})
        for period in ["5m", "15m", "30m", "1H", "4H", "1Dutc"]:
            with self.subTest(period=period):
                normalized = self.parse(link.replace("15m", period))
                original = self.parse(link.replace("open/?", "open?").replace("15m", period))
                self.assertEqual(normalized.returncode, 0)
                self.assertEqual(original.returncode, 0)
                self.assertEqual(normalized.stdout, original.stdout)

    def test_invalid_links_are_rejected_before_dispatch(self):
        for link in [LINK.replace("spike-tradingview:", "https:"), LINK.replace("//open?", "//evil?"),
            LINK.replace("//open?", "//user@open?"), LINK.replace("//open?", "//open:80?"),
            LINK.replace("//open?", "//open/path?"), LINK.replace("//open?", "//open//?"),
            LINK.replace("//open?", "//open/%2F?"), LINK.replace("//open?", "//open/../?"),
            LINK + "#fragment", LINK + "&symbol=ETH-USDT-SWAP",
            LINK + "&url=https://evil.test", LINK.replace("15m", "1m"), LINK.replace("BTC", "BTC%0A"),
            LINK + '\" & calc.exe', LINK + "\n", LINK.replace("BTC", "A" * 31), "--other"]:
            with self.subTest(link=link):
                self.assertEqual(self.parse(link).returncode, 2)

    def test_caller_cannot_choose_an_external_destination(self):
        for layout in ["https://evil.test/chart/", "http://www.tradingview.com/chart/",
            "https://tradingview.com.evil.test/chart/", "https://www.tradingview.com/chart/../",
            LAYOUT + "?symbol=OLD", LAYOUT + "\n", 'C:\\Windows\\System32\\calc.exe']:
            with self.subTest(layout=layout):
                self.assertEqual(self.parse(layout=layout).returncode, 2)

    def test_new_install_can_use_the_same_chart_base_as_telegram(self):
        response = self.parse(layout="https://www.tradingview.com/chart/")
        self.assertEqual(json.loads(response.stdout)["ChartUrl"], "https://www.tradingview.com/chart/?symbol=OKX%3ABTCUSDT.P&interval=15")


if __name__ == "__main__":
    unittest.main(verbosity=2)
