"""Assemble an isolated ETC 5m research snapshot using OKX caches, archives and confirmed REST candles."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode
import importlib.util
import json
import sys
import time

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from src.data import fetch_okx

spec = importlib.util.spec_from_file_location("okx_archive_reader", HERE.parent / "exp-alt-month-first-week-five-minute-observation-20261111-v2/archive_windows.py")
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)
CACHE = ROOT / "data/research/etc-month-first-week-all-touches-20261111"
CACHE.mkdir(parents=True, exist_ok=True)
SYMBOL = "ETC-USDT-SWAP"
STEP = 300_000
LISTING = 1573557408000
END = pd.Timestamp("2026-11-11T08:10:00Z").value // 1_000_000


def pull_window(pair):
    start, end = pair
    url = fetch_okx.API + "?" + urlencode(dict(instId=SYMBOL, bar="5m", before=start-1, after=end, limit=300))
    for attempt in range(5):
        payload = fetch_okx._request(url)
        if payload.get("code") == "0":
            break
        time.sleep(attempt+1)
    else:
        raise RuntimeError(str(payload))
    raw = payload["data"]
    rows = np.array([[float(v) for v in row[:5]] for row in raw if row[8] == "1"], dtype=float).reshape(-1,5)
    if len(rows) and (rows[:,0].min()<start or rows[:,0].max()>=end):
        raise ValueError((start,end,rows[:,0].min(),rows[:,0].max()))
    if len(rows) == 300 and len(rows) * STEP < end - start:
        raise ValueError("window too wide for API")
    return rows


def load_month(label):
    path = CACHE / (label + ".npz")
    if path.is_file():
        return
    bj = pd.Timestamp(label+"-01",tz="Asia/Shanghai")
    start = max(int(bj.timestamp()*1000), LISTING // STEP * STEP)
    end = min(int((bj + pd.offsets.MonthBegin(1)).timestamp()*1000), END)
    old, origin = archive.get_archive("ETC",label,False)
    if old is None and end != END:
        raw = fetch_okx._request_archive(fetch_okx.archive_url("ETC_USDT_SWAP",bj))
        if raw is not None:
            try:
                old = archive.read_zip(raw,"ETC",label)
                origin = "OKX_monthly_zip"
            except ValueError as exc:
                # Some OKX archives include unconfirmed rows. Discard the archive
                # and use only confirmed REST data for the entire month.
                origin = f"discarded archive: {exc}"
    baseline = np.empty((0,5)) if old is None else old[(old[:,0]>=start)&(old[:,0]<end),:5]
    want = np.arange(start,end,STEP,dtype=np.int64)
    missing = np.setdiff1d(want,baseline[:,0].astype(np.int64))
    patches = []
    if len(missing):
        for run in np.split(missing,np.flatnonzero(np.diff(missing)!=STEP)+1):
            for sub in range(0,len(run),300):
                times=run[sub:sub+300]
                rows=pull_window((int(times[0]),int(times[-1]+STEP)))
                if len(rows): patches.append(rows)
    result = np.concatenate([baseline,*patches])
    result = result[np.argsort(result[:,0])]
    clock,o,h,l,c = result.T
    assert (np.diff(clock)>0).all() and (clock % STEP == 0).all() and np.isfinite(result).all()
    assert (l>0).all() and (l<=np.minimum(o,c)).all() and (h>=np.maximum(o,c)).all()
    source = dict(month=label,cached_or_archive=origin,archive_rows=len(baseline),rest_rows=sum(map(len,patches)),missing=len(want)-len(result))
    with path.with_suffix(".tmp").open("wb") as handle:
        np.savez_compressed(handle,ohlc=result)
    path.with_suffix(".tmp").replace(path)
    path.with_suffix(".json").write_text(json.dumps(source))
    print(label,len(result),source["missing"],flush=True)


if __name__ == "__main__":
    months = [d.strftime("%Y-%m") for d in pd.date_range("2019-11-01","2026-11-01",freq="MS")]
    with ThreadPoolExecutor(max_workers=9) as pool:
        list(pool.map(load_month,months[::-1]))
