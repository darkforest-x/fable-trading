const MARKET_ROOT = `${import.meta.env.VITE_SIGNAL_MODEL_API || ""}/api/market`;

/** Load recent public candles through the local read-only proxy. */
export async function fetchMarketCandles(symbol, { bar = "15m", limit = 300 } = {}) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 15_000);
  try {
    const query = new URLSearchParams({ bar, limit: String(limit) });
    const response = await fetch(`${MARKET_ROOT}/candles/${encodeURIComponent(symbol)}?${query}`, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.detail || `行情接口返回 ${response.status}`);
    }
    return payload;
  } finally {
    window.clearTimeout(timeout);
  }
}

/** Parse one OKX WebSocket `[ts,o,h,l,c,vol,...,confirm]` candle. */
export function parseOkxStreamCandle(row) {
  if (!Array.isArray(row) || row.length < 9) return null;
  const candle = {
    time: Math.floor(Number(row[0]) / 1000),
    open: Number(row[1]),
    high: Number(row[2]),
    low: Number(row[3]),
    close: Number(row[4]),
    volume: Number(row[5]),
    confirmed: String(row[8]) === "1",
  };
  const values = [candle.time, candle.open, candle.high, candle.low, candle.close, candle.volume];
  if (!values.every(Number.isFinite) || candle.time <= 0) return null;
  if (candle.high < Math.max(candle.open, candle.low, candle.close)) return null;
  if (candle.low > Math.min(candle.open, candle.high, candle.close)) return null;
  return candle;
}
