"""Download HistData.com XAUUSD 1-minute ASCII bars into data/histdata/XAUUSD/.

HistData M1 ASCII rows: YYYYMMDD HHMMSS;open;high;low;close;volume. The site says EST without DST,
but for XAUUSD the stamps behave as New York wall-clock time (see backtest.load_15m). Full years come as one zip; the current year is per month.
Flow: GET the year/month page to obtain the hidden form token, then POST /get.php.
Chosen because Dukascopy throttles to ~15s/file through this machine's proxy and
OKX XAU-USDT-SWAP only lists from 2025, while the strategy author backtested 2011-2026.
"""
import re, sys, time, pathlib, urllib.request, urllib.parse, http.cookiejar

OUT = pathlib.Path(__file__).resolve().parents[3] / "data" / "histdata" / "XAUUSD"
BASE = "http://www.histdata.com"
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
opener.addheaders = [("User-Agent", "Mozilla/5.0")]


def fetch(period: str) -> None:
    out = OUT / f"HISTDATA_COM_ASCII_XAUUSD_M1_{period.replace('/', '')}.zip"
    if out.exists() and out.stat().st_size > 1000:
        print("skip", out.name, flush=True)
        return
    page = f"{BASE}/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/xauusd/{period}"
    html = opener.open(page, timeout=60).read().decode("utf-8", "replace")
    form = dict(re.findall(r'<input type="hidden" name="(\w+)" id="\w+" value="([^"]*)"', html))
    req = urllib.request.Request(f"{BASE}/get.php", data=urllib.parse.urlencode(form).encode(),
                                 headers={"Referer": page})
    data = opener.open(req, timeout=300).read()
    if data[:2] != b"PK":
        raise RuntimeError(f"{period}: not a zip ({len(data)} bytes)")
    OUT.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print("ok", out.name, len(data), flush=True)


if __name__ == "__main__":
    for p in sys.argv[1:]:
        for attempt in range(5):
            try:
                fetch(p)
                break
            except Exception as e:  # truncated transfers through the proxy happen; retry
                print("retry", p, attempt, repr(e)[:120], flush=True)
                time.sleep(5 * (attempt + 1))
        time.sleep(2)
