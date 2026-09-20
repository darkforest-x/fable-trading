"""Freeze two OKX perpetual 5m research sources, separate from live caches.

Public source: OKX market/history-candles with confirm=1. Existing local seed
snapshots are read-only and hash bound; duplicate OHLCV must agree exactly.
Missing intervals alone are fetched, with raw response receipts and no price
interpolation or venue substitution. Commit this builder/config before use.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-four-hour-range-20260920-v1"
URL = "https://www.okx.com/api/v5/market/history-candles"
COLS = ["open", "high", "low", "close", "volume"]
BAR = pd.Timedelta(minutes=5)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def merge(pieces):
    """Fail on any conflicting duplicate, including downloaded overlap."""
    if not pieces:
        return pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz="UTC"))
    frame = pd.concat(pieces).sort_index()
    duplicates = frame.loc[frame.index.duplicated(keep=False)]
    if duplicates.groupby(level=0)[COLS].nunique().gt(1).any().any():
        raise ValueError("conflicting duplicate OHLCV source rows")
    return frame.loc[~frame.index.duplicated(keep="last")]


def acquire(config_path):
    config_path = Path(config_path).resolve()
    builders = [Path(__file__).resolve(), config_path]
    identities = {}
    for path in builders:
        rel = str(path.relative_to(ROOT))
        if subprocess.check_output(["git", "show", f"HEAD:{rel}"], cwd=ROOT) != path.read_bytes():
            raise ValueError(f"uncommitted source builder/config: {rel}")
        identities[rel] = sha(path)
    cfg = json.loads(config_path.read_text())
    start, end = pd.Timestamp(cfg["source_start"]), pd.Timestamp(cfg["source_end"])
    grid = pd.date_range(start, end, freq="5min", inclusive="left")
    session = requests.Session()
    all_receipts = {}
    for symbol, seeds in cfg["seeds"].items():
        out = EXP / "data" / symbol
        raw = out / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        target, receipt_path = out / "bars.csv.gz", out / "source_receipt.json"
        if target.exists() or receipt_path.exists():
            if target.exists() and receipt_path.exists():
                prior = json.loads(receipt_path.read_text())
                if (prior["builders"] == identities and prior["sha256"] == sha(target)
                        and prior["start"] == str(start) and prior["end_exclusive"] == str(end)):
                    all_receipts[symbol] = prior
                    print(f"{symbol}: verified existing frozen source", flush=True)
                    continue
            raise FileExistsError(f"incomplete or changed frozen output: {out}")
        pieces, used_seeds = [], []
        for spec in seeds:
            path = ROOT / spec["path"]
            if not path.exists():
                continue  # Fresh checkout can fetch the identical fixed grid.
            if sha(path) != spec["sha256"]:
                raise ValueError(f"seed hash changed: {path}")
            d = pd.read_csv(path)
            d.index = (pd.to_datetime(d.ts, unit="ms", utc=True) if "ts" in d
                       else pd.to_datetime(d.open_time, utc=True))
            d = d.loc[(d.index >= start) & (d.index < end), COLS]
            pieces.append(d)
            used_seeds.append(dict(**spec, selected_rows=len(d)))
        frame = merge(pieces)
        missing = grid.difference(frame.index)
        intervals = []
        for stamp in missing:
            if not intervals or stamp != intervals[-1][1]:
                intervals.append([stamp, stamp + BAR])
            else:
                intervals[-1][1] += BAR
        print(f"{symbol}: seed={len(frame)} missing={len(missing)} intervals={len(intervals)}", flush=True)
        pages = []
        for low, high in intervals:
            cursor, bound = int(high.timestamp() * 1000), int(low.timestamp() * 1000)
            while cursor > bound:
                path = raw / f"{cursor}.json"
                params = dict(instId=symbol, bar="5m", after=cursor, limit=300)
                if path.exists():
                    payload = json.loads(path.read_text())
                else:
                    for attempt in range(5):
                        try:
                            response = session.get(URL, params=params, timeout=30)
                            response.raise_for_status()
                            payload = response.json()
                            if payload.get("code") != "0":
                                raise ValueError(str(payload))
                            break
                        except (requests.RequestException, ValueError):
                            if attempt == 4:
                                raise
                            time.sleep(1 + attempt)
                    path.write_text(json.dumps(payload, separators=(",", ":")))
                    time.sleep(.15)
                rows = payload.get("data", [])
                if payload.get("code") != "0" or not rows or min(int(r[0]) for r in rows) >= cursor:
                    raise ValueError(f"invalid/no older source response at {cursor}")
                selected = [r for r in rows if bound <= int(r[0]) < cursor]
                if any(len(r) < 9 or r[8] != "1" for r in selected):
                    raise ValueError("unconfirmed/malformed native source candle")
                d = pd.DataFrame([[float(v) for v in r[1:6]] for r in selected], columns=COLS,
                    index=pd.to_datetime([int(r[0]) for r in selected], unit="ms", utc=True))
                pieces.append(d)
                pages.append(dict(path=str(path.relative_to(ROOT)), sha256=sha(path), params=params, rows=len(selected)))
                cursor = min(int(r[0]) for r in rows)
                if len(pages) % 10 == 0:
                    print(f"{symbol}: pages={len(pages)} oldest={pd.Timestamp(cursor, unit='ms', tz='UTC')}", flush=True)
        frame = merge(pieces)
        if not frame.index.equals(grid):
            raise ValueError(f"source grid mismatch: {symbol}, missing={len(grid.difference(frame.index))}")
        if (not np.isfinite(frame.to_numpy()).all() or (frame[COLS[:4]] <= 0).any().any()
                or (frame.volume < 0).any() or (frame.low > frame[['open', 'close']].min(axis=1)).any()
                or (frame.high < frame[['open', 'close']].max(axis=1)).any()):
            raise ValueError("invalid OHLCV geometry")
        frame.index.name = "open_time"
        frame.to_csv(target, compression=dict(method="gzip", mtime=0))
        receipt = dict(symbol=symbol, start=str(start), end_exclusive=str(end), rows=len(frame),
            missing=0, duplicate_conflicts=0, initial_missing=len(missing), seeds=used_seeds,
            pages=pages, source=URL, output=str(target.relative_to(ROOT)), sha256=sha(target),
            builders=identities, git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            generated_at=pd.Timestamp.now(tz="UTC").isoformat())
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        all_receipts[symbol] = receipt
        print(f"{symbol}: frozen={len(frame)} sha256={receipt['sha256']}", flush=True)
    return all_receipts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=EXP / "source_config.json")
    acquire(parser.parse_args().config)
