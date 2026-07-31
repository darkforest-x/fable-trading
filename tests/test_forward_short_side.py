"""Short-side forward exit geometry and ledger side contract (P0 2026-07-31)."""
from __future__ import annotations

import types

import numpy as np
import pandas as pd

from src.judgment.forward_scan import (
    _artifact_trade_side,
    resolve_forward_exit,
    resolve_forward_exit_short,
)
from src.judgment.forward_types import SL_MULT, TP_MULT
from src.judgment.frozen import default_config


def _ohlc_frame(
    *,
    entry: float = 100.0,
    atr: float = 1.0,
    path: list[tuple[float, float, float, float]],
) -> pd.DataFrame:
    """signal bar + entry bar + path bars. path rows are (o,h,l,c) after entry."""
    n = 2 + len(path)
    open_time = pd.date_range("2026-07-01", periods=n, freq="15min", tz="UTC")
    opens = [entry, entry] + [p[0] for p in path]
    highs = [entry, entry] + [p[1] for p in path]
    lows = [entry, entry] + [p[2] for p in path]
    closes = [entry, entry] + [p[3] for p in path]
    # atr14 / atr_pct at signal_i=0
    atr14 = [atr] * n
    atr_pct = [atr / entry] * n
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "atr14": atr14,
            "atr_pct": atr_pct,
            "open_time": open_time,
        }
    )


def test_default_config_is_short_side() -> None:
    assert default_config().side == "short"


def test_artifact_side_from_config() -> None:
    art = types.SimpleNamespace(config=types.SimpleNamespace(side="short"))
    assert _artifact_trade_side(art) == "short"
    art2 = types.SimpleNamespace(config=None)
    assert _artifact_trade_side(art2) == "long"


def test_short_tp_on_price_fall() -> None:
    atr = 1.0
    entry = 100.0
    tp = entry - TP_MULT * atr  # 95
    # path: go down to TP
    frame = _ohlc_frame(
        entry=entry,
        atr=atr,
        path=[(99, 99.5, 94.5, 95)],
    )
    # signal_i=0, entry bar=1, first path bar=2 — need enough bars: signal + entry + horizon path
    # resolve starts at entry_i = signal_i+1 = 1, so highs/lows from bar 1...
    # put TP hit on entry bar itself for simplicity: low hits 94.5 on bar index 1
    frame.loc[1, "low"] = 94.5
    frame.loc[1, "high"] = 100.5
    frame.loc[1, "close"] = 95.0
    out = resolve_forward_exit_short(frame, 0)
    assert out is not None
    assert out.status == "closed"
    assert out.outcome == "tp"
    assert out.label == 1
    assert abs(out.realized_ret - (1.0 - tp / entry)) < 1e-9


def test_short_sl_on_rally() -> None:
    atr = 1.0
    entry = 100.0
    sl = entry + SL_MULT * atr  # 102
    frame = _ohlc_frame(entry=entry, atr=atr, path=[(101, 103, 100.5, 102)])
    frame.loc[1, "high"] = 103.0
    frame.loc[1, "low"] = 100.2
    frame.loc[1, "close"] = 102.0
    out = resolve_forward_exit_short(frame, 0)
    assert out is not None
    assert out.outcome in {"sl", "sl_ambiguous"}
    assert out.label == 0
    assert out.realized_ret < 0


def test_short_vs_long_opposite_on_same_path() -> None:
    """Falling path: short TP vs long SL."""
    atr = 1.0
    entry = 100.0
    frame = _ohlc_frame(entry=entry, atr=atr, path=[(99, 99.5, 94.0, 95)])
    frame.loc[1, "low"] = 94.0
    frame.loc[1, "high"] = 100.2
    short = resolve_forward_exit_short(frame, 0)
    long = resolve_forward_exit(frame, 0)
    assert short is not None and long is not None
    assert short.outcome == "tp"
    assert long.outcome == "sl"
    assert short.realized_ret > 0
    assert long.realized_ret < 0
