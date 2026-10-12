"""Five years of OKX ETH-USDT-SWAP 1m data for the V12.10 ETH 15m replay.

Archive: official OKX monthly 1m candlestick ZIPs (UTC+8 calendar months) from config archive_months,
fetched, validated and published by src.data.fetch_okx.fetch_archive_symbol with bar="1m" (the same
function that produced the parent study's archive input). Tail: confirmed public history-candles 1m
from the archive's exclusive end to config end, via spike_lowtf_v1_v126.fetch_recent (confirm == "1"
rows only, receipt pins the SHA-256). Both receipts land in config data_dir (not in git).

Usage:
  PYTHONPATH=. .venv/bin/python experiments/active/exp-spike-v1210-eth15m-5y-20261012-v1/fetch.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.data.fetch_okx import archive_months, fetch_archive_symbol
from yoyo.evaluation import spike_lowtf_v1_v126 as low

HERE = Path(__file__).resolve().parent
CFG = json.loads((HERE / "config.json").read_text())


def main():
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
