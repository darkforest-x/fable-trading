"""Write the already-frozen ETH source into Freqtrade's offline futures layout.

The futures and mark OHLC files intentionally contain the same frozen OKX trade
bars because no independent historical mark-price series is present in this
experiment.  Funding is a zero-rate mechanical placeholder solely so Freqtrade
can exercise short execution; results exclude funding and must not be presented
as a funded perpetual PnL result.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from freqtrade.data.history.datahandlers import get_datahandler
from freqtrade.enums import CandleType
from freqtrade.exchange import timeframe_to_minutes
from build_v6_bridge import aggregate, read_source

EXP = Path(__file__).resolve().parent
PAIR = "ETH/USDT:USDT"


def main() -> None:
    handler = get_datahandler(EXP / "data", "feather")
    raw = read_source()
    for minutes, tf in ((30, "30m"), (60, "1h"), (240, "4h")):
        frame = aggregate(raw, minutes).reset_index(names="date")
        ohlcv = frame[["date", "open", "high", "low", "close", "volume"]]
        handler.ohlcv_store(PAIR, tf, ohlcv, CandleType.FUTURES)
        # Required by Freqtrade's futures data loader; documented in receipt.
        handler.ohlcv_store(PAIR, tf, ohlcv, CandleType.MARK)
    funding_dates = pd.date_range(raw.index.min().floor("1h"), raw.index.max().ceil("1h"), freq="1h", tz="UTC")
    funding = pd.DataFrame({"date": funding_dates, "funding_rate": 0.0})
    handler.ohlcv_store(PAIR, "1h", funding, CandleType.FUNDING_RATE)


if __name__ == "__main__":
    main()
