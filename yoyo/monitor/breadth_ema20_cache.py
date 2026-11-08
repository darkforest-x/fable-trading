"""Repair gaps in the shared closed-candle cache from bounded public kline requests."""
import pandas as pd


def ensure_closed_window(path, symbol, interval, start_ms, end_ms, *, fetch, read, write):
    """Return cached bars plus verified exchange rows in [start_ms,end_ms).

    Both bounds are UTC candle opens. The caller passes only fully closed
    intervals after listing. An exchange hole remains a hole in the result.
    """
    step = {"1h": 3_600_000, "5m": 300_000}[interval]
    if start_ms % step or end_ms % step or start_ms > end_ms:
        raise ValueError("Invalid closed candle window")
    cached = read(path, interval_ms=step)
    have = set(cached.open_ms)
    missing = [ts for ts in range(start_ms, end_ms, step) if ts not in have]
    if not missing:
        return cached
    fetched = []
    i = 0
    while i < len(missing):
        j = i + 1
        while j < len(missing) and j - i < 499 and missing[j] == missing[j - 1] + step:
            j += 1
        batch = fetch(symbol, interval, start_ms=missing[i], end_ms=missing[j - 1], limit=j - i)
        if not batch.empty:
            fetched.append(batch[batch.open_ms.isin(missing[i:j])])
        i = j
    if fetched:
        repair = pd.concat(fetched, ignore_index=True)
        write(path, repair)
        return pd.concat([cached, repair], ignore_index=True).drop_duplicates("open_ms", keep="last").sort_values("open_ms")
    return cached
