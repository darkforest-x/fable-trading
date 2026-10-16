"""Causal, display-only leader discovery for Notion tasks 50 and 53.

Research rule v1: hourly benchmark close above the preceding 24 closes starts
an episode. Its first lower close freezes the first wave; a close above that
wave's high confirms recovery. A close below the pre-breakout close, the first
lower close after recovery, or 120 elapsed bars ends the episode. These are
explicit new observation definitions, not validated trading thresholds.

Inputs are confirmed OKX checkpoint t/o/h/l/c/v through the shared cutoff.
First-wave and pullback windows come exclusively from the benchmark prefix.
24h returns and prior 168h highs use only that prefix; volume compares a wave
with its preceding equal-length window in contracts, never quote turnover.
No future label, order, execution parameter or notification is read or written.
"""
from __future__ import annotations

import hashlib
import json
import math

HOUR = 3_600_000
VERSION = "altcoin-leaders-1h-v1"
LOOKBACK = 24
MAX_EPISODE_BARS = 120
BENCHMARKS = ("ETH-USDT-SWAP", "BTC-USDT-SWAP")
STABLES = {"USDT", "USDC", "DAI", "USDE", "USDD", "FDUSD", "TUSD", "PYUSD", "USD1", "USDS", "FRAX"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def crypto_instrument(row):
    """Require explicit current OKX classification; unknown is not crypto.

    Source: https://www.okx.com/docs-v5/en/#public-data-rest-api-get-instruments
    """
    symbol = row.get("instId", "")
    return (symbol.endswith("-USDT-SWAP") and row.get("instType") == "SWAP"
            and row.get("state") == "live" and row.get("instCategory") == "1"
            and row.get("ruleType") == "normal" and row.get("ctType") == "linear"
            and row.get("settleCcy") == "USDT" and symbol.split("-")[0] not in STABLES)


def closed_rows(raw, asof):
    """Validate closed candles; ignore later bars before examining their values."""
    if not isinstance(raw, list):
        raise ValueError("missing_checkpoint")
    rows = []
    for row in raw:
        if not isinstance(row, dict) or type(row.get("t")) is not int:
            raise ValueError("invalid_candle")
        if row["t"] + HOUR > asof:
            continue
        if row["t"] % HOUR:
            raise ValueError("unaligned_candle")
        values = [row.get(k) for k in ("o", "h", "l", "c", "v")]
        if not all(type(x) in (int, float) and math.isfinite(x) for x in values):
            raise ValueError("invalid_candle")
        o, h, lo, c, v = values
        if min(o, h, lo, c) <= 0 or v < 0 or h < max(o, lo, c) or lo > min(o, h, c):
            raise ValueError("invalid_candle")
        if rows and row["t"] <= rows[-1]["t"]:
            raise ValueError("unordered_candles")
        rows.append(row)
    return rows


def contiguous_tail(rows):
    start = 0
    for i in range(1, len(rows)):
        if rows[i]["t"] != rows[i - 1]["t"] + HOUR:
            start = i
    return rows[start:]


def detect_episode(rows, benchmark):
    """Stream the prefix once. Confirmations are never backdated to extrema."""
    rows = contiguous_tail(rows)
    episode = None
    for i in range(LOOKBACK, len(rows)):
        bar, prev = rows[i], rows[i - 1]
        close_ms = bar["t"] + HOUR
        if episode:
            expired = close_ms - episode["launch_ms"] >= MAX_EPISODE_BARS * HOUR
            ended = (bar["c"] <= episode["start_price"] or expired
                     or (episode["phase"] == "recovery" and bar["c"] < prev["c"]))
            if ended:
                episode = None
            elif episode["phase"] == "launch":
                if bar["c"] < prev["c"]:
                    episode.update(phase="pullback", wave_end_ms=prev["t"] + HOUR,
                                   pullback_confirmed_ms=close_ms)
                else:
                    episode["wave_high"] = max(episode["wave_high"], bar["c"])
            elif episode["phase"] == "pullback" and bar["c"] > episode["wave_high"]:
                episode.update(phase="recovery", recovery_confirmed_ms=close_ms)
        if episode is None and bar["c"] > max(r["c"] for r in rows[i - LOOKBACK:i]):
            episode = dict(id=f"{VERSION}:{benchmark}:{close_ms}", benchmark=benchmark,
                           phase="launch", start_ms=prev["t"] + HOUR, start_price=prev["c"],
                           launch_ms=close_ms, wave_high=bar["c"], wave_end_ms=None,
                           pullback_confirmed_ms=None, recovery_confirmed_ms=None)
    return episode


def exact_window(rows, start_close, end_close):
    selected = [r for r in rows if start_close <= r["t"] + HOUR <= end_close]
    count = (end_close - start_close) // HOUR + 1
    if (len(selected) != count or not selected or selected[0]["t"] + HOUR != start_close
            or selected[-1]["t"] + HOUR != end_close
            or any(b["t"] - a["t"] != HOUR for a, b in zip(selected, selected[1:]))):
        raise ValueError("incomplete_common_window")
    return selected


def candidate(symbol, rows, benchmark_rows, episode, asof):
    """Calculate same-window evidence; no weighted buy score is produced."""
    r24 = exact_window(rows, asof - 24 * HOUR, asof)
    b24 = exact_window(benchmark_rows, asof - 24 * HOUR, asof)
    price = r24[-1]["c"]
    ret24 = price / r24[0]["c"] - 1
    result = dict(symbol=symbol, quality="ok", price=price, return_24h=ret24,
                  rs_24h=ret24 - (b24[-1]["c"] / b24[0]["c"] - 1),
                  leadership="discovery", action="wait_market", tier=None,
                  wave_return=None, wave_rs=None, episode_rs=None, giveback=None,
                  drawdown=None, recovered_ms=None, recovery_bars=None,
                  holding_recovery=None, volume_ratio=None, room_to_prior_7d_high=None,
                  prior_7d_high=None, structure_holds=None)
    prior = [r for r in rows if asof - 168 * HOUR <= r["t"] + HOUR < asof]
    if len(prior) == 168 and len(contiguous_tail(prior)) == 168:
        result["prior_7d_high"] = max(r["h"] for r in prior)
        result["room_to_prior_7d_high"] = result["prior_7d_high"] / price - 1
    if not episode:
        return result
    window = exact_window(rows, episode["start_ms"], asof)
    bw = exact_window(benchmark_rows, episode["start_ms"], asof)
    result["episode_rs"] = price / window[0]["c"] - bw[-1]["c"] / bw[0]["c"]
    result["structure_holds"] = price > window[0]["c"]
    if episode["phase"] == "launch":
        result.update(leadership="wave_observing", action="wait_pullback")
        return result
    wave = exact_window(rows, episode["start_ms"], episode["wave_end_ms"])
    benchmark_wave = exact_window(benchmark_rows, episode["start_ms"], episode["wave_end_ms"])
    pullback = [r for r in window if r["t"] + HOUR > episode["wave_end_ms"]]
    p0, h1 = wave[0]["c"], max(r["c"] for r in wave)
    wr = wave[-1]["c"] / p0 - 1
    result.update(wave_return=wr, wave_rs=wr - (benchmark_wave[-1]["c"] / benchmark_wave[0]["c"] - 1))
    n = len(wave) - 1
    try:
        earlier = exact_window(rows, episode["start_ms"] - (n - 1) * HOUR, episode["start_ms"])
        denom = sum(r["v"] for r in earlier)
        result["volume_ratio"] = sum(r["v"] for r in wave[1:]) / denom if denom > 0 else None
    except ValueError:
        pass
    peak, dd = h1, 0.0
    for bar in pullback:
        peak = max(peak, bar["c"])
        dd = max(dd, 1 - bar["c"] / peak)
        if result["recovered_ms"] is None and bar["c"] > h1:
            result["recovered_ms"] = bar["t"] + HOUR
            result["recovery_bars"] = (bar["t"] + HOUR - episode["wave_end_ms"]) // HOUR
    result.update(drawdown=dd, giveback=(h1 - min(r["c"] for r in pullback)) / (h1 - p0) if h1 > p0 else None,
                  holding_recovery=price > h1, wave_start_price=p0, wave_high=h1)
    if wr <= 0 or result["wave_rs"] < 0:
        result.update(leadership="not_participating", action="observe_only")
    elif not result["structure_holds"]:
        result.update(leadership="downgraded", action="structure_lost")
    else:
        result.update(leadership="candidate", action="review_entry" if price > h1 else "wait_recovery")
    return result


def rank_fronts(items):
    """Pareto fronts preserve genuine ties; ticker order inside a front is cosmetic."""
    pool = [r for r in items if r["leadership"] == "candidate"]
    def axes(r):
        return (r["episode_rs"], -r["giveback"], -r["drawdown"],
                -r["recovery_bars"] if r["recovery_bars"] is not None else -math.inf)
    tier = 1
    while pool:
        front = []
        for row in pool:
            a = axes(row)
            if not any(all(x >= y for x, y in zip(axes(other), a))
                       and any(x > y for x, y in zip(axes(other), a)) for other in pool):
                front.append(row)
        for row in front:
            row["tier"] = tier
        ids = {r["symbol"] for r in front}
        pool = [r for r in pool if r["symbol"] not in ids]
        tier += 1


def build_snapshot(raw_series, symbols, benchmark, asof, observed_ms, episode_record=None):
    """Pure snapshot builder. Missing instruments remain in the denominator."""
    benchmark_rows = closed_rows(raw_series.get(benchmark), asof)
    exact_window(benchmark_rows, asof - 24 * HOUR, asof)
    episode = detect_episode(benchmark_rows, benchmark)
    if episode_record and episode and episode_record["id"] == episode["id"]:
        episode.update(first_observed_ms=episode_record["first_observed_ms"],
                       frozen_symbols=episode_record["frozen_symbols"],
                       universe_version=episode_record["universe_version"])
    elif episode:
        frozen = sorted(s for s in symbols if s not in BENCHMARKS)
        episode.update(first_observed_ms=observed_ms, frozen_symbols=frozen,
                       universe_version=digest(frozen))
    current = set(symbols) - set(BENCHMARKS)
    frozen = set(episode["frozen_symbols"]) if episode else current
    items = []
    for symbol in sorted(current | frozen):
        try:
            if symbol not in current:
                raise ValueError("no_longer_in_live_universe")
            rows = closed_rows(raw_series.get(symbol), asof)
            row = candidate(symbol, rows, benchmark_rows, episode, asof)
            if symbol not in frozen:
                row.update(leadership="late_listing", action="next_episode", tier=None)
        except ValueError as exc:
            row = dict(symbol=symbol, quality=str(exc), leadership="unknown", action="data_missing", tier=None)
        items.append(row)
    rank_fronts(items)
    items.sort(key=lambda r: (r["tier"] or 10000, -(r.get("rs_24h") or 0) if not r["tier"] else 0, r["symbol"]))
    return dict(rule_version=VERSION, venue="OKX", timeframe="1H", benchmark=benchmark,
                asof_ms=asof, observed_ms=observed_ms, episode=episode, items=items,
                universe_version=episode["universe_version"] if episode else digest(sorted(current)),
                coverage=dict(total=len(items), comparable=sum(r["quality"] == "ok" for r in items),
                              candidates=sum(r["tier"] is not None for r in items)),
                policy=dict(breakout_closes=LOOKBACK, maximum_episode_hours=MAX_EPISODE_BARS,
                            rank_axes=["episode_rs", "giveback", "drawdown", "recovery_bars"]),
                execution_ready=False, trading_validated=False)
