"""Read public OKX candles without writing scanner state or market caches.

Source: https://www.okx.com/docs-v5/en/#order-book-trading-market-data-get-candlesticks
The newest row may be unconfirmed. SMA uses the trailing 20/60/120 closes;
EMA continues the confirmed SPIKE seed with alpha=2/(length+1), identical to
spike_burst_replay._ema. Each poll starts from the confirmed seed, never the
previous provisional EMA. Observations are not original signal-time evidence.
"""
from __future__ import annotations

import httpx

from .source import SourceError, causal_candles, finite

MARKET_URL = "https://www.okx.com/api/v5/market/candles"


class LiveMarket:
    def __init__(self, transport=None):
        self.transport = transport

    def candles(self, signal):
        try:
            with httpx.Client(timeout=8, follow_redirects=False, transport=self.transport) as client:
                response = client.get(MARKET_URL, params={"instId": signal["symbol"],
                                      "bar": signal["timeframe"], "limit": "300"})
                response.raise_for_status()
                payload = response.json()
            if str(payload.get("code")) != "0" or not isinstance(payload.get("data"), list):
                raise ValueError("invalid market response")
            return payload["data"]
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise SourceError("OKX 实时行情暂不可用，已暂停更新；请稍后刷新") from exc


def live_rows(payload, signal, raw_rows, observed_at_ms):
    """Merge current OHLC with a confirmed 120-bar SPIKE MA seed, in memory."""
    duration = signal["timeframe_min"] * 60_000
    values = payload.get("candles")
    if not isinstance(values, list) or not values or not isinstance(values[-1], dict) or not finite(values[-1].get("t")):
        raise SourceError("SPIKE 均线种子不完整，暂不能更新实时图表")
    seed_end = values[-1]["t"] + duration
    if seed_end > observed_at_ms:
        raise SourceError("SPIKE 已收盘数据时间异常")
    seed = causal_candles(payload, dict(signal, bar_close_ms=seed_end))
    if len(seed) < 120 or any(not finite(seed[-1].get(f"ema{n}")) for n in (20, 60, 120)):
        raise SourceError("SPIKE 均线预热数据不足，暂不能更新实时图表")
    parsed = {}
    for raw in raw_rows:
        try:
            if not isinstance(raw, list) or len(raw) < 9 or str(raw[8]) not in {"0", "1"}:
                raise ValueError("invalid candle")
            t = int(raw[0])
            row = dict(zip(("o", "h", "l", "c"), (float(x) for x in raw[1:5])))
            closed = str(raw[8]) == "1"
            if (t % duration or t > observed_at_ms or t < 0 or
                    (closed and t + duration > observed_at_ms) or
                    not all(finite(v) and v > 0 for v in row.values()) or
                    row["h"] < max(row["o"], row["c"], row["l"]) or
                    row["l"] > min(row["o"], row["c"], row["h"])):
                raise ValueError("invalid prices or time")
            item = dict(row, t=t, is_closed=closed)
            if t in parsed and parsed[t] != item:
                raise ValueError("conflicting candles")
            parsed[t] = item
        except (ValueError, TypeError, OverflowError) as exc:
            raise SourceError("OKX 实时K线格式异常，暂不生成识别输入") from exc
    if not parsed or max(parsed) < seed[-1]["t"] or seed[-1]["t"] not in parsed:
        raise SourceError("OKX 实时数据与 SPIKE 缓存没有连续交集，请稍后刷新")
    for row in seed:
        other = parsed.get(row["t"])
        if other and (not other["is_closed"] or any(other[k] != row[k] for k in ("o", "h", "l", "c"))):
            raise SourceError("OKX 与 SPIKE 已收盘价格不一致，暂不拼接图表")
    merged = [dict(row, is_closed=True) for row in seed]
    for t in sorted(parsed):
        if t <= seed[-1]["t"]:
            continue
        if t != merged[-1]["t"] + duration or not merged[-1]["is_closed"]:
            raise SourceError("实时窗口内有缺失或未确认的历史K线，暂不生成识别输入")
        row = dict(parsed[t])
        closes = [r["c"] for r in merged] + [row["c"]]
        for n in (20, 60, 120):
            row[f"sma{n}"] = sum(closes[-n:]) / n
            alpha = 2 / (n + 1)
            row[f"ema{n}"] = (1 - alpha) * merged[-1][f"ema{n}"] + alpha * row["c"]
        merged.append(row)
    rows = merged[-120:]
    last = rows[-1]
    stale = (last["t"] + duration < observed_at_ms - 30_000)
    return rows, stale
