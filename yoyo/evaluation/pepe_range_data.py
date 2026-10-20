"""Public OKX data acquisition for the owner-authorized V2.8 PEPE exit study.

Reuse registered native 5m/15m/H1 candles for parity; obtain missing history
from the same exchange's monthly archive and public candles API. Validate
instrument, completed status, OHLC and time continuity. No content hashing,
credentials, account endpoints, or production data writes.
"""
from __future__ import annotations

import concurrent.futures
import io
import json
from pathlib import Path
import time
import threading
import urllib.parse
import urllib.request
import zipfile

import pandas as pd
from src.data.fetch_okx import REQUEST_HEADERS

EXP = Path("experiments/active/exp-pepe-range-exits-20261020-v1")
_request_lock = threading.Lock()
_last_request = 0.


def get(url: str) -> bytes:
    global _last_request
    for attempt in range(5):
        try:
            with _request_lock:
                time.sleep(max(0., .13-(time.monotonic()-_last_request)))
                _last_request = time.monotonic()
            req = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=45) as response:
                return response.read()
        except Exception:
            if attempt == 4:
                raise
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def validate(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_values("ts").reset_index(drop=True)
    duplicate = frame[frame.ts.duplicated(False)]
    for _, group in duplicate.groupby("ts"):
        if len(group.drop_duplicates()) != 1:
            raise ValueError("conflicting duplicate candles")
    frame = frame.drop_duplicates("ts").reset_index(drop=True)
    assert (frame[["open", "high", "low", "close"]] > 0).all().all()
    assert (frame.high >= frame[["open", "close", "low"]].max(axis=1)).all()
    assert (frame.low <= frame[["open", "close", "high"]].min(axis=1)).all()
    return frame


def month(label: str) -> dict:
    out = EXP / "inputs" / f"okx_PEPE_USDT_SWAP_5m_{label}.csv"
    meta = out.with_suffix(".json")
    if out.exists() and meta.exists():
        return json.loads(meta.read_text())
    ym = label.replace("-", "")
    url = f"https://static.okx.com/cdn/okex/traderecords/candlesticks/monthly/{ym}/PEPE-USDT-SWAP-candlesticks-{label}.zip?v=999"
    payload = get(url)
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [n for n in archive.namelist() if n.endswith(".csv")]
        assert len(names) == 1
        raw = pd.read_csv(archive.open(names[0]))
    assert set(raw.instrument_name) == {"PEPE-USDT-SWAP"}
    assert set(raw["confirm"].astype(str)) <= {"0", "1"}, (label, raw["confirm"].unique())
    unconfirmed = int((raw["confirm"] == 0).sum())
    raw = raw[raw["confirm"] == 1]
    frame = raw.rename(columns={"open_time": "ts"})[["ts", "open", "high", "low", "close"]]
    frame = validate(frame)
    frame.index = pd.to_datetime(frame.ts, unit="ms", utc=True)
    start = pd.Timestamp(label + "-01", tz="UTC") - pd.Timedelta(hours=8)
    finish = pd.Timestamp(label + "-01", tz="UTC") + pd.offsets.MonthBegin(1) - pd.Timedelta(hours=8)
    assert frame.empty or (frame.index.min() >= start and frame.index.max() < finish), label
    grouped = frame.resample("5min")
    five = grouped.agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    count = grouped.close.count()
    five = five[count == 5]
    five.insert(0, "ts", five.index.asi8 // 1_000_000)
    five.to_csv(out, index=False)
    receipt = dict(path=str(out), source=url, bytes=len(payload), raw_rows=len(frame),
                   rows=len(five), first=int(five.ts.iloc[0]) if len(five) else None,
                   last=int(five.ts.iloc[-1]) if len(five) else None,
                   incomplete_five=int(((count > 0) & (count < 5)).sum()),
                   raw_gaps=int((frame.ts.diff().dropna() != 60000).sum()),
                   unconfirmed_minutes_excluded=unconfirmed)
    meta.write_text(json.dumps(receipt, indent=2) + "\n")
    print(label, receipt["rows"], flush=True)
    return receipt


def recent(start: int, finish: int) -> dict:
    out = EXP / "inputs" / "okx_PEPE_USDT_SWAP_5m_2026-10-api.csv"
    meta = out.with_suffix(".json")
    if out.exists() and meta.exists():
        return json.loads(meta.read_text())
    cursor, rows, urls = finish, [], []
    while cursor > start:
        query = urllib.parse.urlencode(dict(instId="PEPE-USDT-SWAP", bar="5m", limit=100, after=cursor))
        url = "https://www.okx.com/api/v5/market/history-candles?" + query
        result = json.loads(get(url))
        assert result["code"] == "0", result
        page = result["data"]
        if not page:
            raise ValueError("history API ended before requested coverage")
        urls.append(url)
        for row in page:
            if int(row[8]) == 1 and start <= int(row[0]) < finish:
                rows.append([int(row[0]), *map(float, row[1:5])])
        nxt = min(int(row[0]) for row in page)
        assert nxt < cursor
        cursor = nxt
        time.sleep(.12)
    frame = validate(pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close"]))
    frame.to_csv(out, index=False)
    receipt = dict(path=str(out), sources=urls, rows=len(frame),
                   first=int(frame.ts.iloc[0]), last=int(frame.ts.iloc[-1]))
    meta.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def repair(frame: pd.DataFrame) -> tuple[pd.DataFrame, list]:
    """Replace archive gaps with actual completed native OKX 5m observations."""
    ranges = []
    for i in frame.index[frame.ts.diff() > 300000]:
        begin, end = int(frame.ts.iloc[i-1]+300000), int(frame.ts.iloc[i])
        for start in range(begin, end, 7*86400000):
            ranges.append((start, min(start+7*86400000, end)))

    def fetch_range(bounds):
        start, finish = bounds
        path = EXP/"inputs"/f"native_gap_{start}_{finish}.csv"
        expected = (finish-start)//300000
        if path.exists():
            fixed = validate(pd.read_csv(path))
            assert len(fixed) == expected
            return dict(start=start, finish=finish, rows=len(fixed), path=str(path),
                        source="OKX public history-candles, confirmed native 5m")
        cursor, rows = finish, []
        while cursor > start:
            query = urllib.parse.urlencode(dict(instId="PEPE-USDT-SWAP", bar="5m", limit=100, after=cursor))
            url = "https://www.okx.com/api/v5/market/history-candles?" + query
            result = json.loads(get(url))
            assert result["code"] == "0", result
            page = result["data"]
            if not page:
                raise ValueError(f"cannot recover archive gap {start} through {finish}")
            rows += [[int(r[0]), *map(float, r[1:5])] for r in page
                     if int(r[8]) == 1 and start <= int(r[0]) < finish]
            nxt = min(int(r[0]) for r in page)
            assert nxt < cursor
            cursor = nxt
            time.sleep(.12)
        fixed = validate(pd.DataFrame(rows, columns=frame.columns))
        assert len(fixed) == expected, (start, finish, len(fixed), expected)
        fixed.to_csv(path, index=False)
        print("repaired", pd.Timestamp(start, unit="ms", tz="UTC"), len(fixed), flush=True)
        return dict(start=start, finish=finish, rows=len(fixed), path=str(path),
                    source="OKX public history-candles, confirmed native 5m")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fixes = list(pool.map(fetch_range, ranges))
    if fixes:
        frame = validate(pd.concat([frame, *[pd.read_csv(f["path"]) for f in fixes]], ignore_index=True))
    return frame, fixes


def main():
    cfg = json.loads((EXP / "config.json").read_text())
    (EXP / "inputs").mkdir(exist_ok=True)
    labels = pd.period_range(cfg["warmup_archive_start"], cfg["archive_end"], freq="M").astype(str)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        receipts = list(pool.map(month, labels))
    finish = pd.Timestamp(cfg["end"]).value // 1_000_000
    begin = pd.Timestamp("2026-09-30T16:00:00Z").value // 1_000_000
    receipts.append(recent(begin, finish))
    instrument = json.loads(get("https://www.okx.com/api/v5/public/instruments?instType=SWAP&instId=PEPE-USDT-SWAP"))["data"][0]
    assert float(instrument["tickSz"]) == cfg["tick"]
    parts = [pd.read_csv(r["path"]) for r in receipts]
    frame = validate(pd.concat(parts, ignore_index=True))
    frame = frame[frame.ts < finish]
    frame, fixes = repair(frame)
    gaps = frame.loc[frame.ts.diff().ne(300000) & frame.ts.diff().notna(), ["ts"]]
    audit = dict(sources=receipts, gap_repairs=fixes, rows=len(frame), first=int(frame.ts.iloc[0]),
                 last=int(frame.ts.iloc[-1]), gaps=gaps.ts.tolist(),
                 instrument={k: instrument.get(k) for k in ("instId", "tickSz", "listTime", "state")},
                 validation="metadata, actual read, OHLC, time continuity; no content hashes")
    (EXP / "data_receipt.json").write_text(json.dumps(audit, indent=2) + "\n")
    if len(gaps):
        raise ValueError(f"{len(gaps)} source gaps; resolve before replay")
    frame.to_csv(EXP / "inputs" / "okx_pepe_5m.csv.gz", index=False)
    print(json.dumps({k: v for k, v in audit.items() if k != "sources"}, indent=2))


if __name__ == "__main__":
    main()
