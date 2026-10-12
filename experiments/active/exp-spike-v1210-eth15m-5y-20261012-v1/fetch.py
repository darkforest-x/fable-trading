"""Five years of OKX ETH-USDT-SWAP 1m data for the V12.10 ETH 15m replay.

Archive: official OKX monthly 1m candlestick ZIPs (UTC+8 calendar months) from config archive_months,
fetched, validated and published by src.data.fetch_okx.fetch_archive_symbol with bar="1m" (the same
function that produced the parent study's archive input). Tail: confirmed public history-candles 1m
from the archive's exclusive end to config end, via spike_lowtf_v1_v126.fetch_recent (confirm == "1"
rows only, receipt pins the SHA-256). Both receipts land in config data_dir (not in git).

The helper's own downloader keeps nothing between attempts and does not retry http.client.IncompleteRead
or Python 3.9 socket.timeout, so one cut-off ZIP restarted all 61 months. ``cached_request`` replaces it
for this run only: each ZIP is cached under data_dir/zips once fully read, transport errors are retried,
and a real 404 still returns None. Parsing, validation and publishing remain the helper's.

Usage:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1210-eth15m-5y-20261012-v1/fetch.py
"""
from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
import socket
import time
import urllib.error
import urllib.request

import pandas as pd

from src.data import fetch_okx
from src.data.fetch_okx import archive_months, fetch_archive_symbol
from yoyo.evaluation import spike_lowtf_v1_v126 as low

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())


def cached_request(url: str):
    """Fully read ZIP bytes for one archive URL, cached on disk; None only for HTTP 404."""
    name = url.split("/")[-1].split("?")[0]
    cache = Path(CFG["data_dir"]) / "zips" / name
    if cache.exists():
        return cache.read_bytes()
    for attempt in range(8):
        try:
            request = urllib.request.Request(url, headers=fetch_okx.REQUEST_HEADERS)
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = response.read()
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".part")
            tmp.write_bytes(payload)
            tmp.replace(cache)
            print("downloaded", name, len(payload), hashlib.sha256(payload).hexdigest()[:12], flush=True)
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            error = exc
        except (urllib.error.URLError, http.client.IncompleteRead, socket.timeout, ConnectionError, TimeoutError) as exc:
            error = exc
        print("retry", name, type(error).__name__, flush=True)
        time.sleep(min(60, 2 ** attempt))
    raise fetch_okx.ArchiveFetchError(f"archive download failed after retries: {url}")


def main():
    fetch_okx._request_archive = cached_request  # this run only; see module docstring
    out = Path(CFG["data_dir"])
    first, last = CFG["archive_months"]
    months = archive_months(first, last, max_exclusive=CFG["archive_max_exclusive"])
    archive = fetch_archive_symbol(CFG["symbol"], months=months, output_dir=out,
                                   max_exclusive=CFG["archive_max_exclusive"], bar="1m")
    print("archive", archive["status"], archive.get("rows"), archive.get("first_time"), archive.get("last_time"),
          "missing", archive["months_missing"])
    if archive["status"] != "complete" or archive["months_missing"]:
        raise SystemExit("archive incomplete")
    tail = low.fetch_recent(CFG["symbol"], pd.Timestamp(CFG["archive_max_exclusive"]), pd.Timestamp(CFG["end"]), out)
    print("tail", tail["rows"], "missing minutes", tail["missing_minutes"], tail["path"])


if __name__ == "__main__":
    main()
