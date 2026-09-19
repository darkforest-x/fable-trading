"""Extend the frozen OKX BTC source to 90 days of pre-evaluation context.

Read only the prior official-OKX snapshot and the earlier local OKX archive.
Fill absent five-minute bars from the same public history-candles API, never
interpolate. The previous 30-day-plus-three-year snapshot remains identical.
The larger prefix stabilizes both recursive RSI/SAR and consecutive strong
diamond counts; it is chosen before evaluating any new strategy outcomes.
No production cache or trading endpoint is written.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import requests

from yoyo.data.btc_rsi_research_source import COLS, URL, sha

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-btc-rsi-fifth-color-20260920-v1"
PARENT = ROOT / "experiments/active/exp-btc-rsi1h-sixma5m-20260920-v1/data/okx_btc_usdt_swap_5m.csv.gz"
SEED = ROOT / "data/kline_preholdout_okx_5m/okx_BTC_USDT_SWAP_5m_341567.csv"
EXPECTED_PARENT_SHA = "076e9d1c74b9912a8d3421216fa6a10c3b19544fbdf93e90050dc92d2bf560ae"


def acquire():
    rel = str(Path(__file__).resolve().relative_to(ROOT))
    if subprocess.check_output(["git", "show", "HEAD:" + rel], cwd=ROOT) != Path(__file__).read_bytes():
        raise ValueError("commit source builder before running")
    assert sha(PARENT) == EXPECTED_PARENT_SHA
    out = EXP / "data"
    target = out / "okx_btc_usdt_swap_5m_90d.csv.gz"
    if target.exists():
        receipt = json.loads((out / "source_receipt.json").read_text())
        if sha(target) != receipt["sha256"]:
            raise ValueError("existing frozen source SHA mismatch")
        print("verified existing immutable source; no write")
        return
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    base = pd.read_csv(PARENT, index_col="open_time", parse_dates=True)
    base.index = pd.to_datetime(base.index, utc=True)
    start = pd.Timestamp("2023-09-19T21:00Z") - pd.Timedelta(days=90)
    end = pd.Timestamp("2026-09-19T21:00Z")
    if SEED.exists():
        old = pd.read_csv(SEED)
        old.index = pd.to_datetime(old.ts, unit="ms", utc=True)
        prefix = old.loc[(old.index >= start) & (old.index < base.index[0]), COLS]
    else:
        prefix = pd.DataFrame(columns=COLS, index=pd.DatetimeIndex([], tz="UTC"))
    if not prefix.index.is_unique:
        raise ValueError("duplicate prefix source rows")
    frame = pd.concat([prefix, base]).sort_index()
    grid = pd.date_range(start, end, freq="5min", inclusive="left")
    missing = grid.difference(frame.index)
    receipts = []
    pieces = [frame]
    intervals = []
    for stamp in missing:
        if not intervals or stamp != intervals[-1][1]:
            intervals.append([stamp, stamp + pd.Timedelta(minutes=5)])
        else:
            intervals[-1][1] += pd.Timedelta(minutes=5)
    for low, high in intervals:
        cursor = int(high.timestamp() * 1000)
        bound = int(low.timestamp() * 1000)
        while cursor > bound:
            params = dict(instId="BTC-USDT-SWAP", bar="5m", after=cursor, limit=300)
            response = requests.get(URL, params=params, timeout=30)
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != "0":
                raise ValueError(payload)
            all_rows = payload.get("data", [])
            if not all_rows or min(int(row[0]) for row in all_rows) >= cursor:
                raise ValueError("history pagination did not advance")
            rows = [row for row in all_rows if bound <= int(row[0]) < cursor]
            if any(row[8] != "1" for row in rows):
                raise ValueError("unconfirmed same-source row")
            path = raw / f"page_before_{cursor}.json"
            path.write_text(json.dumps(payload, separators=(",", ":")))
            if rows:
                pieces.append(pd.DataFrame([[float(v) for v in row[1:6]] for row in rows], columns=COLS,
                    index=pd.to_datetime([int(row[0]) for row in rows], unit="ms", utc=True)))
            receipts.append(dict(params=params, selected_rows=len(rows), path=str(path.relative_to(ROOT)), sha256=sha(path)))
            cursor = min(int(row[0]) for row in all_rows)
    frame = pd.concat(pieces).sort_index()
    if not frame.index.equals(grid) or not np.isfinite(frame[COLS].to_numpy()).all():
        raise ValueError("source grid/nonfinite mismatch")
    if (frame[COLS[:4]] <= 0).any().any() or (frame.volume < 0).any() or (frame.low > frame[["open", "close"]].min(axis=1)).any() or (frame.high < frame[["open", "close"]].max(axis=1)).any():
        raise ValueError("invalid OHLCV")
    pd.testing.assert_frame_equal(frame.loc[base.index, COLS], base[COLS], check_names=False, check_freq=False)
    frame.index.name = "open_time"
    frame.to_csv(target, compression=dict(method="gzip", mtime=0))
    receipt = dict(source_url=URL, symbol="BTC-USDT-SWAP", start=str(start), end_exclusive=str(end),
        rows=len(frame), expected_rows=len(grid), missing_after=0, initial_missing=len(missing),
        prior_source=str(PARENT.relative_to(ROOT)), prior_sha256=sha(PARENT), prior_rows_identical=len(base),
        prefix_source=str(SEED.relative_to(ROOT)) if SEED.exists() else None,
        prefix_sha256=sha(SEED) if SEED.exists() else None, raw_pages=receipts,
        output=str(target.relative_to(ROOT)), sha256=sha(target),
        builder_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        generated_at=pd.Timestamp.now(tz="UTC").isoformat())
    (out / "source_receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    acquire()
