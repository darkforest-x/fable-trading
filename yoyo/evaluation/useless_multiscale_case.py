"""Auditable, descriptive USELESS launch case; never a live signal pipeline.

Features use closed OHLCV through each row. 15m aggregation emits only complete
UTC-aligned groups. As-of joins compare candle CLOSE with decision time, so an
unfinished 4H candle cannot enter a 1H decision. The Notion Momentum 1.0 formula
is SMA50 deviation divided by its trailing 50-bar absolute maximum (current bar
included); it is not a volume feature. Future-path labels are kept in separate
columns/files and never used to tune thresholds or select successful cases.

Sources: yoyo.data.altcoin_features; OKX public market/history-candles (confirm=1);
owner-linked Notion Momentum 1.0. HTTP caches retain raw replies and SHA256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from yoyo.data.altcoin_features import MA_COLUMNS, build_altcoin_features

DEFAULT_SOURCE = Path("data/altcoin_trends_20260909_v1/history_full_verified/owner_illustration/USELESS_USDT_SWAP_15m.csv")
DEFAULT_OUT = Path("analysis/output/useless_multiscale_20260909")
INTERVALS = {"3m": 3, "5m": 5, "15m": 15, "30m": 30, "1H": 60, "4H": 240}
TZ = "Asia/Shanghai"


def read_bars(path: Path) -> pd.DataFrame:
    bars = pd.read_csv(path)
    bars.index = pd.to_datetime(bars["open_time"], utc=True)
    bars.index.name = "open_time"
    return bars.drop(columns=["open_time"]).sort_index()


def aggregate_complete(bars: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregate gap-free 15m inputs; drop incomplete leading/trailing groups."""
    if minutes % 15 or minutes < 15:
        raise ValueError("aggregation must be a multiple of 15 minutes")
    if not bars.index.is_unique or not bars.index.is_monotonic_increasing:
        raise ValueError("input times must be unique and increasing")
    if not (bars.index.to_series().diff().dropna() == pd.Timedelta(minutes=15)).all():
        raise ValueError("15m source has a gap")
    agg = bars.resample(f"{minutes}min", origin="epoch").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    counts = bars.close.resample(f"{minutes}min", origin="epoch").count()
    return agg.loc[counts.eq(minutes // 15)]


def features(bars: pd.DataFrame) -> pd.DataFrame:
    """IMACD plus SMA50 oscillator, prior20 volume, prior12 MA formation."""
    out = bars.loc[:, ["open", "high", "low", "close", "volume"]].join(build_altcoin_features(bars))
    out["volume_mean20_prior"] = bars.volume.shift(1).rolling(20).mean()
    out["volume_median20_prior"] = bars.volume.shift(1).rolling(20).median()
    out["relative_volume_mean20"] = bars.volume / out.volume_mean20_prior.replace(0, np.nan)
    out["relative_volume_median20"] = bars.volume / out.volume_median20_prior.replace(0, np.nan)
    deviation = bars.close - bars.close.rolling(50).mean()
    denominator = deviation.abs().rolling(50, min_periods=1).max().replace(0, np.nan)
    out["momentum10"] = deviation / denominator * 100
    out["momentum10_high_count6"] = out.momentum10.ge(90).rolling(6, min_periods=1).sum()
    out["momentum10_high_zone"] = out.momentum10.ge(90)
    out["momentum10_strong6"] = out.momentum10_high_count6.ge(3)
    last = None
    events = np.zeros(len(out), dtype=bool)
    for i, strong in enumerate(out.momentum10_strong6):
        if strong and (last is None or i-last > 1):
            events[i], last = True, i
    out["momentum10_label_event"] = events
    out["momentum10_corrected_alert_event"] = events
    # Exact original final alert expression is evaluated after lastOscUpBar is
    # assigned on a label event; all candidate events therefore become false.
    out["momentum10_original_final_alert"] = False
    out["golden_cross"] = out.md.gt(out.sb) & out.md.shift(1).le(out.sb.shift(1))
    out["dead_cross"] = out.md.lt(out.sb) & out.md.shift(1).ge(out.sb.shift(1))
    out["close_above_all6"] = bars.close.gt(out.loc[:, MA_COLUMNS].max(axis=1))
    out["bar_return_pct"] = (bars.close / bars.open - 1) * 100
    out["above6_count"] = out.loc[:, MA_COLUMNS].lt(bars.close, axis=0).sum(axis=1)
    out["price_rope_gap_atr"] = (bars.close - out.rope_high) / out.atr
    out["rope_width_atr"] = (out.rope_high - out.rope_low) / out.atr
    out["rope_centroid"] = out.loc[:, MA_COLUMNS].mean(axis=1)
    out["prior_flatness12_atr"] = (out.rope_centroid.shift(1) - out.rope_centroid.shift(13)).abs() / out.atr.shift(1)
    out["pine_structure_stop_v27"] = bars.low.rolling(5).min() - 0.2 * out.atr
    out["pine_initial_stop_v27"] = np.minimum(out.pine_structure_stop_v27, bars.close - out.atr)
    out["pine_initial_stop_v27_unrounded"] = out.pine_initial_stop_v27
    # Historical OHLC supplied here has 1e-5 price precision. This is an explicit
    # replay tick assumption, not a claim about today's instrument metadata.
    out["pine_initial_stop_v27"] = np.floor(out.pine_initial_stop_v27 / 0.00001) * 0.00001
    out["legacy_signal_bar_stop"] = bars.low
    out["research_stop_2atr"] = bars.close - 2 * out.atr
    return out


def asof_closed(frame: pd.DataFrame, minutes: int, decision: pd.Timestamp) -> pd.Series:
    eligible = frame.index + pd.Timedelta(minutes=minutes) <= decision
    if not eligible.any():
        raise ValueError("no closed candle before decision")
    row = frame.loc[eligible].iloc[-1].copy()
    row["open_time"] = frame.loc[eligible].index[-1].isoformat()
    row["close_time"] = (frame.loc[eligible].index[-1] + pd.Timedelta(minutes=minutes)).isoformat()
    row["open_beijing"] = frame.loc[eligible].index[-1].tz_convert(TZ).isoformat()
    row["close_beijing"] = (frame.loc[eligible].index[-1] + pd.Timedelta(minutes=minutes)).tz_convert(TZ).isoformat()
    return row


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        value = value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is pd.NA or (isinstance(value, float) and not np.isfinite(value)):
        return None
    return value


def fetch_native(bar: str, start: str, end: str, destination: Path) -> dict:
    """Page older confirmed OKX candles at <=1.8req/s; persist raw source receipts."""
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000)
    minutes = INTERVALS[bar]
    destination.mkdir(parents=True, exist_ok=True)
    raw_dir = destination / f"raw_{bar}"
    raw_dir.mkdir(exist_ok=True)
    cursor, accumulated, receipts, page = end_ms, {}, [], 0
    while cursor > start_ms:
        url = "https://www.okx.com/api/v5/market/history-candles?" + urlencode(
            {"instId": "USELESS-USDT-SWAP", "bar": bar, "after": cursor, "limit": 100})
        started = time.monotonic()
        request = Request(url, headers={"User-Agent": "SpikeHistoricalResearch/1.0"})
        with urlopen(request, timeout=30) as response:
            raw = response.read()
        parsed = json.loads(raw)
        if parsed.get("code") != "0" or not parsed.get("data"):
            raise RuntimeError(f"OKX empty/error page {bar} {cursor}: {parsed.get('code')}")
        raw_path = raw_dir / f"page_{page:04d}.json"
        raw_path.write_bytes(raw)
        rows = parsed["data"]
        receipts.append({"url": url, "path": str(raw_path), "sha256": hashlib.sha256(raw).hexdigest(),
                         "rows": len(rows), "fetched_at": pd.Timestamp.now(tz="UTC").isoformat()})
        for row in rows:
            ts = int(row[0])
            if start_ms <= ts < end_ms:
                if str(row[8]) != "1":
                    raise ValueError("unconfirmed historical candle")
                if ts in accumulated and accumulated[ts] != row:
                    raise ValueError("inconsistent duplicate source candle")
                accumulated[ts] = row
        next_cursor = min(int(row[0]) for row in rows)
        if next_cursor >= cursor:
            raise RuntimeError("OKX pagination stalled")
        cursor, page = next_cursor, page + 1
        time.sleep(max(0, 0.56 - (time.monotonic() - started)))
    frame = pd.DataFrame([accumulated[t] for t in sorted(accumulated)],
                         columns=["ts", "open", "high", "low", "close", "volume", "volCcy", "volCcyQuote", "confirm"])
    expected = np.arange(start_ms, end_ms, minutes * 60_000)
    if not np.array_equal(frame.ts.astype("int64").to_numpy(), expected):
        raise ValueError(f"{bar}: missing or misaligned candles ({len(frame)} vs {len(expected)})")
    frame["open_time"] = pd.to_datetime(frame.ts.astype("int64"), unit="ms", utc=True)
    path = destination / f"USELESS_{bar}.csv"
    frame.to_csv(path, index=False)
    receipt = {"bar": bar, "rows": len(frame), "start": start, "end_exclusive": end,
               "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "raw_pages": receipts,
               "all_confirmed": True, "gap_count": 0, "conflicting_duplicates": 0}
    (destination / f"USELESS_{bar}_receipt.json").write_text(json.dumps(receipt, indent=2))
    return receipt


def audit_paths(frame: pd.DataFrame, entry_open: pd.Timestamp, minutes=60) -> dict:
    """Illustrative post-close path, with initial-stop and MA-exit timing explicit."""
    signal = frame.loc[entry_open]
    entry = float(signal.close)
    future = frame.loc[frame.index > entry_open]
    result = {"entry_reference": entry, "entry_open": entry_open, "entry_close": entry_open + pd.Timedelta(minutes=minutes),
              "last_close": float(future.close.iloc[-1]), "last_time": future.index[-1],
              "highest_high": float(future.high.max()), "highest_time": future.high.idxmax(),
              "highest_close": float(future.close.max()), "lowest_after_entry": float(future.low.min()),
              "maximum_favorable_pct": float((future.high.max()/entry-1)*100),
              "last_return_pct": float((future.close.iloc[-1]/entry-1)*100)}
    for key in ("pine_initial_stop_v27", "legacy_signal_bar_stop", "research_stop_2atr", "release_zone_low"):
        stop = float(signal[key])
        hits = future.loc[future.low.le(stop)]
        first = None if hits.empty else hits.index[0]
        live = future if first is None else future.loc[future.index < first]
        peak = entry if live.empty else max(entry, float(live.high.max()))
        result[key] = {"stop": stop, "risk_pct": (entry-stop)/entry*100, "first_touch_open": first,
                       "peak_before_stop_R": (peak-entry)/(entry-stop) if entry>stop else None}
    for ma in MA_COLUMNS:
        hits = future.loc[future.close.lt(future[ma])]
        if hits.empty:
            result[f"first_close_below_{ma}"] = None
        else:
            when = hits.index[0]
            next_rows = future.loc[future.index > when]
            result[f"first_close_below_{ma}"] = {"open_time": when, "close": float(hits.iloc[0].close),
                        "signal_close_time": when+pd.Timedelta(minutes=minutes),
                        "next_open_reference": None if next_rows.empty else float(next_rows.iloc[0].open),
                        "close_return_pct": (float(hits.iloc[0].close)/entry-1)*100}
    prior_trail = float(signal.sma20) if signal.close > signal.sma20 else np.nan
    trail_breaches = []
    episode_end = None
    for when, row in future.iterrows():
        if np.isfinite(prior_trail) and row.close < prior_trail:
            trail_breaches.append({"open_time": when, "close": float(row.close), "prior_protection": prior_trail})
        if row.close > row.sma20:
            prior_trail = max(prior_trail, float(row.sma20)) if np.isfinite(prior_trail) else float(row.sma20)
        if episode_end is None:
            reason = ("new_release_replaces_tracker" if row.release_side else
                      "md_returned_to_zero_or_negative" if row.md <= 0 else
                      "close_below_frozen_zone_low" if row.close < signal.release_zone_low else None)
            if reason:
                episode_end = {"open_time": when, "reason": reason, "close": float(row.close),
                               "close_return_pct": (float(row.close)/entry-1)*100}
    # Pine gives structure termination/new-release replacement priority over
    # protection status. The terminating bar and subsequent episodes therefore
    # cannot contribute to this episode's protection-breach count.
    episode_breaches = (trail_breaches if episode_end is None else
                        [item for item in trail_breaches if item["open_time"] < episode_end["open_time"]])
    result["sma20_ratchet_first_breach"] = episode_breaches[0] if episode_breaches else None
    result["sma20_ratchet_breach_bars"] = len(episode_breaches)
    result["all_followup_breach_bars"] = len(trail_breaches)
    result["all_followup_breach_scope"] = "SMA20 ratchet replay through the entire supplied follow-up, including after the original episode ended"
    result["pine_episode_end_ignoring_initial_stop"] = episode_end
    result["pine_tick_assumption"] = 0.00001
    result["path_is_not_trade_pnl"] = True
    return result


def run_analysis(source: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    base = read_bars(source)
    frames = {name: features(aggregate_complete(base, minutes))
              for name, minutes in INTERVALS.items() if minutes >= 15}
    for name in ("3m", "5m"):
        path = out / "data" / f"USELESS_{name}.csv"
        if path.exists():
            frames[name] = features(read_bars(path))
    decisions = {"08h_Beijing": pd.Timestamp("2026-08-31T08:00:00+08:00"),
                 "09h_Beijing": pd.Timestamp("2026-08-31T09:00:00+08:00")}
    report = {"source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "source_rows": len(base), "asof": {}, "events": {}, "snapshots": {}, "paths": {}}
    for label, decision in decisions.items():
        report["asof"][label] = {name: asof_closed(frame, INTERVALS[name], decision).to_dict()
                                    for name, frame in frames.items()}
    event_start, event_end = pd.Timestamp("2026-08-28T00:00Z"), pd.Timestamp("2026-09-03T00:00Z")
    for name, frame in frames.items():
        global_view = frame.loc[(frame.index >= pd.Timestamp("2026-08-25T00:00Z"))].copy()
        global_view["close_time"] = global_view.index + pd.Timedelta(minutes=INTERVALS[name])
        global_view["open_beijing"] = global_view.index.tz_convert(TZ)
        global_view.to_csv(out / f"features_{name}.csv", index_label="open_time")
        selected = frame.loc[(frame.index >= event_start) & (frame.index < event_end)].copy()
        selected["close_time"] = selected.index + pd.Timedelta(minutes=INTERVALS[name])
        selected["open_beijing"] = selected.index.tz_convert(TZ)
        events = selected.loc[selected.release_side.ne(0) | selected.golden_cross | selected.dead_cross]
        report["events"][name] = [{"open_time": t, **row.to_dict()} for t, row in events.iterrows()]
        if name == "1H":
            all_events = frame.loc[(frame.index >= pd.Timestamp("2026-08-01T00:00Z")) & frame.release_side.ne(0)].copy()
            # These reference levels were derived for longs only. The export
            # contains both release directions; never present a long-side low
            # or downward ATR offset as a valid short stop. Global chart feature
            # names stay stable and are not used as short execution levels.
            long_stop_columns = ["pine_structure_stop_v27", "pine_initial_stop_v27",
                                 "pine_initial_stop_v27_unrounded", "legacy_signal_bar_stop",
                                 "research_stop_2atr"]
            all_events.loc[all_events.release_side.lt(0), long_stop_columns] = np.nan
            # Labels are attached only to this separate descriptive event export.
            for horizon in (6, 24, 72):
                all_events[f"future_close_return_{horizon}h_pct"] = ((frame.close.shift(-horizon)/frame.close-1)*100).reindex(all_events.index)
                all_events[f"future_high_{horizon}h_pct"] = ((frame.high.shift(-1)[::-1].rolling(horizon).max()[::-1]/frame.close-1)*100).reindex(all_events.index)
                all_events[f"future_low_{horizon}h_pct"] = ((frame.low.shift(-1)[::-1].rolling(horizon).min()[::-1]/frame.close-1)*100).reindex(all_events.index)
            all_events.to_csv(out / "all_1H_releases_with_separate_future_labels.csv", index_label="open_time")
            for t in ("2026-08-30T23:00Z", "2026-08-31T00:00Z"):
                report["snapshots"][t] = frame.loc[pd.Timestamp(t)].to_dict()
            for t in frame.index[(frame.index >= pd.Timestamp("2026-08-30T20:00Z")) &
                                 (frame.index <= pd.Timestamp("2026-08-31T04:00Z")) & frame.release_side.eq(1)]:
                report["paths"][t.isoformat()] = audit_paths(frame, t)
    for name in ("15m", "1H", "4H"):
        native_path = out / "data" / f"USELESS_{name}.csv"
        if native_path.exists():
            native = read_bars(native_path)
            merged = native.join(frames[name][["open", "high", "low", "close", "volume"]], rsuffix="_aggregate", how="inner")
            report.setdefault("native_crosscheck", {})[name] = {"rows": len(merged),
                "max_abs_difference": {c: float((merged[c]-merged[c+"_aggregate"]).abs().max())
                                       for c in ("open", "high", "low", "close", "volume")}}
    (out / "case_summary.json").write_text(json.dumps(clean(report), indent=2, ensure_ascii=False))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "analyze"))
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    if args.action == "fetch":
        for bar in ("3m", "5m", "15m", "1H", "4H"):
            receipt = fetch_native(bar, "2026-08-27T00:00:00Z", "2026-08-31T08:00:00Z", args.out / "data")
            print(json.dumps({k: receipt[k] for k in ("bar", "rows", "sha256")}), flush=True)
    else:
        result = run_analysis(args.source, args.out)
        print(json.dumps({"source_rows": result["source_rows"], "output": str(args.out / "case_summary.json")}), flush=True)


if __name__ == "__main__":
    main()
