"""Prepare real 15m V6 inputs and reference immutable higher-timeframe caches.

Read-only old caches are joined to an explicitly separate recent public-OKX
receipt. Overlapping OHLCV must agree; no 30m bar is split to invent 15m data.
The OKX REST 'volume' column is contract count. V6 uses within-stream volume
ratios, not cross-asset absolute volume. Quote volume is unavailable and unused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v6_wvf_study import _data_gap, v6_signals


ASSETS = ("BTC", "ETH", "SOL", "XRP", "DOGE", "PEPE", "SOPH", "USELESS")
END = pd.Timestamp("2026-09-10T00:00:00Z")


def load(path):
    frame = pd.read_csv(path)
    frame.index = pd.to_datetime(frame.ts, unit="ms", utc=True)
    if not frame.index.equals(pd.DatetimeIndex(pd.to_datetime(frame.open_time, utc=True))):
        raise ValueError(f"timestamp columns disagree: {path}")
    frame = frame[["open", "high", "low", "close", "volume"]].sort_index()
    if frame.index.has_duplicates:
        raise ValueError(f"duplicate candles: {path}")
    if not np.isfinite(frame.to_numpy()).all() or (frame.volume < 0).any():
        raise ValueError(f"nonfinite or negative-volume bars: {path}")
    if ((frame.low <= 0) | (frame.high < frame[["open", "close"]].max(axis=1))
            | (frame.low > frame[["open", "close"]].min(axis=1))).any():
        raise ValueError(f"invalid OHLC: {path}")
    return frame


def build(old_root, recent_root, previous, output):
    output.mkdir(parents=True, exist_ok=False)
    prior = json.loads((previous / "input_manifest.json").read_text())
    ticks = {item["symbol"]: item["tick"] for item in prior["inputs"]}
    streams, receipts = [], []
    for symbol in ASSETS:
        pattern = f"okx_{symbol}_USDT_SWAP_15m_*.csv"
        old, recent = list(old_root.glob(pattern)), list(recent_root.glob(pattern))
        if len(old) != 1 or len(recent) != 1:
            raise ValueError(f"need exactly one old/recent source for {symbol}: {old}, {recent}")
        a, b = load(old[0]), load(recent[0])
        overlap = a.index.intersection(b.index)
        if not len(overlap):
            raise ValueError(f"no overlap for seam verification: {symbol}")
        for col in a:
            if not np.allclose(a.loc[overlap, col], b.loc[overlap, col], rtol=1e-9, atol=1e-12):
                raise ValueError(f"overlap mismatch {symbol} {col}; do not silently overwrite")
        joined = pd.concat([a, b.loc[~b.index.isin(a.index)]]).sort_index()
        joined = joined.loc[joined.index < END].copy()
        if joined.index.max() + pd.Timedelta(minutes=15) != END:
            raise ValueError(f"15m does not reach frozen end: {symbol}")
        joined.to_csv(output / f"{symbol}_15m.csv.gz", index_label="time", compression="gzip")
        bars = features(joined)
        bars.attrs["minutes"] = 15
        signals = v6_signals(bars, 15)
        gap = _data_gap(bars, 15)
        cache_path = output / f"{symbol}_15m_features.pkl.gz"
        pd.to_pickle({"bars": bars, "signals": signals, "data_gap": gap}, cache_path, compression="gzip")
        for minutes in (15, 30, 60, 240):
            cache = cache_path if minutes == 15 else previous / f"{symbol}_{minutes}m_features.pkl.gz"
            streams.append({"symbol": symbol, "timeframe_min": minutes,
                            "feature_cache": str(cache.resolve()), "tick": ticks[symbol],
                            "sha256": hashlib.sha256(cache.read_bytes()).hexdigest()})
        receipts.append({"symbol": symbol, "rows": len(joined), "start": str(joined.index.min()),
                         "end_open": str(joined.index.max()), "gap_count": int(gap.sum()),
                         "overlap_verified_rows": len(overlap), "volume_unit": "OKX contracts",
                         "inputs": [{"path": str(p.resolve()), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in (old[0], recent[0])]})
        print(f"prepared {symbol} 15m: {len(joined)} bars, {int(gap.sum())} gaps", flush=True)
    manifest = {"streams": streams, "15m_source_receipts": receipts,
                "builder_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "higher_period_source": str(previous.resolve()),
                "15m_coverage_note": "Existing June-2025 onward history plus recent gap-fill; shorter development history than 30m+; common full validation year.",
                "end_exclusive": str(END)}
    (output / "input_manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for key in ("old-root", "recent-root", "previous", "output"):
        parser.add_argument("--"+key, required=True, type=Path)
    args = parser.parse_args()
    build(args.old_root, args.recent_root, args.previous, args.output)
