#!/usr/bin/env python3
"""Export the frozen safe BTCUSDT.P 15m series for Freqtrade 2026.8.

The input is the same physically pre-holdout OKX 5m archive used by the native
experiment. Complete UTC-aligned 3x5m groups are aggregated before export. No
row at or after the repository holdout is available in the source.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.optimize_btcusdtp_k1k2_intraday_preholdout import (
    json_value,
    load_featured,
    sha256_file,
    write_json,
)
from scripts.research_btcusdtp_k1k2_15m_two_stage_k2 import (
    BAR,
    EXPERIMENT,
    RESULTS,
    load_config,
)


PROJECT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT / "data/freqtrade_btcusdtp_15m_two_stage_k2_20260904_v1/okx"
OUTPUT = DATA_ROOT / "futures/BTC_USDT_USDT-15m-futures.json"
RECEIPT = RESULTS / "freqtrade_data_receipt.json"


def main() -> None:
    config = load_config()
    frame, quality = load_featured(config, BAR)
    rows = [
        [
            int(stamp.value // 1_000_000),
            float(open_),
            float(high),
            float(low),
            float(close),
            float(volume),
        ]
        for stamp, open_, high, low, close, volume in frame[
            ["open_time", "open", "high", "low", "close", "volume"]
        ].itertuples(index=False, name=None)
    ]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "experiment_id": config["experiment_id"],
        "freqtrade_version": config["freqtrade"]["version"],
        "freqtrade_image": config["freqtrade"]["image"],
        "output": str(OUTPUT.relative_to(PROJECT)),
        "output_sha256": sha256_file(OUTPUT),
        "rows": len(rows),
        "first_time": frame["open_time"].iloc[0],
        "last_time": frame["open_time"].iloc[-1],
        "source": quality,
        "holdout_rows_read": 0,
    }
    write_json(RECEIPT, json_value(receipt))
    print(json.dumps(json_value(receipt), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
