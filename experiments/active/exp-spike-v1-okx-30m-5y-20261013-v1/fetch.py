"""OKX 1m data for the SPIKE V1 30m five-year replay (BTC archive, BTC and ETH confirmed tails).

ETH reuses the verified archive of exp-spike-v1210-eth15m-5y-20261012-v1 (config eth_archive, SHA pinned);
BTC's official monthly 1m ZIPs are fetched with the same helper and the same cached, retrying downloader
as that study's fetch.py (its module docstring explains why). Tails are confirmed public history-candles
1m from the archive's exclusive end to config end, via spike_lowtf_v1_v126.fetch_recent. Receipts land
in config data_dir (not in git).

Usage:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1-okx-30m-5y-20261013-v1/fetch.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd

from src.data import fetch_okx
from src.data.fetch_okx import archive_months, fetch_archive_symbol
from yoyo.evaluation import spike_lowtf_v1_v126 as low

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())
_spec = importlib.util.spec_from_file_location(
    "v1210_fetch", HERE.parent / "exp-spike-v1210-eth15m-5y-20261012-v1" / "fetch.py")
v1210_fetch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v1210_fetch)


def main():
    out = Path(CFG["data_dir"])
    out.mkdir(parents=True, exist_ok=True)
    eth = Path(CFG["eth_archive"])
    if hashlib.sha256(eth.read_bytes()).hexdigest() != CFG["eth_archive_sha256"]:
        raise SystemExit("ETH archive bytes differ from the pinned SHA")
    v1210_fetch.CFG = {**v1210_fetch.CFG, "data_dir": str(out)}  # its ZIP cache follows this study's data_dir
    fetch_okx._request_archive = v1210_fetch.cached_request
    first, last = CFG["archive_months"]
    months = archive_months(first, last, max_exclusive=CFG["archive_max_exclusive"])
    archive = fetch_archive_symbol("BTC_USDT_SWAP", months=months, output_dir=out,
                                   max_exclusive=CFG["archive_max_exclusive"], bar="1m")
    print("archive", archive["status"], archive.get("rows"), archive.get("first_time"), archive.get("last_time"),
          "missing", archive["months_missing"], flush=True)
    if archive["status"] != "complete" or archive["months_missing"]:
        raise SystemExit("archive incomplete")
    for symbol in CFG["symbols"]:
        tail = low.fetch_recent(symbol, pd.Timestamp(CFG["archive_max_exclusive"]), pd.Timestamp(CFG["end"]), out)
        print("tail", symbol, tail["rows"], "missing minutes", tail["missing_minutes"], tail["path"], flush=True)


if __name__ == "__main__":
    main()
