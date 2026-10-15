"""V13.1 15m observation-time paper adapter and entry diagnostics.

Inputs: immutable signal stop and clocks, closed OHLCV through evaluation time,
and the existing causal V12.8 ATR/raw-opposite builder. No performance projection
is used. Only entry timing changes; the frozen 2R/4ATR/20bp kernel is retained.
Reference next-open prices are diagnostics read from exact candles, never fills.
"""
from __future__ import annotations

from .paper_source import evaluate_trade as frozen_trade, _rejected

STEP = 900_000
CATEGORIES = {"1": "crypto", "3": "stocks", "4": "commodities", "5": "forex", "6": "bonds"}


def instrument_snapshot():
    """Freeze public OKX classification once at registration, not in the worker.

    Source: https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments
    Unknown categories and later listings stay unknown, never guessed by ticker.
    Classification is diagnostic and cannot exclude a signal from admission.
    """
    from yoyo.monitor.okx import OKX, MarketError
    from .paper_store import clock_ms

    try:
        rows = OKX().instruments()
    except MarketError as exc:
        raise ValueError(f"OKX 资产分类读取失败，请稍后重试：{exc}") from exc
    instruments = {
        row["instId"]: {"inst_category": row.get("instCategory", ""),
                        "asset_class": CATEGORIES.get(row.get("instCategory"), "unknown")}
        for row in rows if row["instId"].endswith("-USDT-SWAP")
    }
    if not instruments:
        raise ValueError("OKX 资产分类未返回 USDT 永续，未创建运行")
    return {"observed_ms": clock_ms(), "source": "https://www.okx.com/api/v5/public/instruments?instType=SWAP",
            "policy": "registration_snapshot_new_listings_unknown", "instruments": instruments}


def evaluate_trade(candles, timeframe, symbol, tick, decision, now_ms):
    """Require a precommitted future open and report signed entry deterioration."""
    if timeframe != "15m":
        return _rejected("unsupported_v131_paper_timeframe", decision, None)
    observed, scheduled = decision.get("observed_ms"), decision.get("scheduled_entry_ms")
    close = decision.get("signal_close_ms")
    if (type(observed) is not int or type(scheduled) is not int or type(close) is not int
            or not close <= observed <= now_ms or scheduled != (observed // STEP + 1) * STEP):
        return _rejected("entry_not_precommitted_after_observation", decision, None)
    result = frozen_trade(candles, timeframe, symbol, tick, decision, now_ms)
    reference = next((bar for bar in candles if bar["t"] == close and close + STEP <= now_ms), None)
    reference_price = float(reference["o"]) if reference else None
    entry = result.get("entry_price")
    side = 1 if decision.get("side") == "long" else -1
    risk = result.get("initial_risk")
    result.update(
        asset_class=decision.get("payload", {}).get("asset_class", "unknown"),
        reference_entry_price=reference_price,
        reference_entry_ms=close if reference else None,
        observation_delay_ms=observed - close,
        entry_delay_ms=scheduled - close,
        # Positive is a worse price for either direction; this is not spread/slippage.
        entry_adverse_bp=side * (entry / reference_price - 1) * 10000 if entry and reference_price else None,
        cost_r=.002 * entry / risk if entry and risk else None,
        comparison_policy="exact_reference_open_vs_observed_future_open_not_orderbook_slippage",
    )
    return result
