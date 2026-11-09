"""Check that the historical signal builder's whole-file rounding had no effect on entry-time inputs."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_binance_winner_entry_features as source
from yoyo.evaluation import cloud_binance_winner_entry_contrast as study
from yoyo.evaluation import signal_center_score as original


def precision(low):
    return int(np.clip(np.ceil(-np.log10(low))+7,2,9))


def audit(item,jobs):
    symbol, rows = item
    ticker = symbol+"USDT"
    direct = rows.signal_feature_source_status.eq("frozen_signal_center_table")
    uncertain = []
    results = []
    if direct.any():
        path = study.ROOT/f"exp-cloud-close-binance-transfer-20261109-v1/work/{ticker}/quotes.npz"
        with np.load(path) as quotes:
            clock = quotes["clock"]
            lows = np.minimum.accumulate(quotes["prices"][:,2])
        tick = json.loads((source.SIGNAL_TABLES/ticker/"done.json").read_text())["tick"]
        digits = int(round(-np.log10(tick)))
        for r in rows[direct].itertuples():
            last = np.searchsorted(clock,r.entry_ms)-1
            # Only H1 candles already completed at the signal close participate.
            if last>=0 and clock[last]+3600000<=r.entry_ms and precision(lows[last])>=digits:
                results.append(dict(event_id=r.Index,method="precision_already_observable",digits=digits))
            else:
                uncertain.append((r,digits))
    for r in rows[~direct].itertuples():
        uncertain.append((r,None))
    if not uncertain:
        return results
    raw = original.five_minute(jobs[symbol]) if symbol in jobs else source._load_archived_15m(symbol)
    times = raw.index.asi8//1000000
    values = raw[source.OHLC].to_numpy()
    end_digits = precision(np.min(values))
    hour_size = 300000 if symbol in jobs else 900000
    groups = {}
    for r, recorded in uncertain:
        n = np.searchsorted(times+hour_size,r.entry_ms,side="right")
        causal = precision(np.min(values[:n]))
        digits = end_digits if recorded is None else recorded
        if not n or times[n-1]+hour_size!=r.entry_ms or digits!=end_digits:
            raise ValueError(f"Unexpected original file or decision clock: {r.Index}")
        groups.setdefault((causal,digits),[]).append((r.Index,n))
    for (causal,digits),events in groups.items():
        # If the arrays for the longest prefix coincide then so do all shorter prefixes.
        n = max(x[1] for x in events)
        consistent = causal==digits or np.array_equal(np.round(values[:n],causal),np.round(values[:n],digits))
        method = "prefix_rounding_identical" if consistent else "requires_causal_recalculation"
        for event,stop in events:
            status = method
            if not consistent and np.array_equal(np.round(values[:stop],causal),np.round(values[:stop],digits)):
                status = "prefix_rounding_identical"
            results.append(dict(event_id=event,method=status,digits=digits,asof_digits=causal))
    return results


def main():
    inp = pd.read_csv(study.EXP/"entry_rows.csv.gz",dtype={"symbol":str}).set_index("event_id")
    jobs = source._job_map()
    with ThreadPoolExecutor(max_workers=3) as pool:
        portions = list(pool.map(lambda x:audit(x,jobs),inp.groupby("symbol",sort=False)))
    receipt = pd.DataFrame([r for group in portions for r in group]).set_index("event_id")
    if not receipt.index.is_unique or set(receipt.index)!=set(inp.index):
        raise ValueError("Price precision audit omitted or duplicated an entry")
    receipt.to_csv(study.EXP/"price_precision.csv.gz")
    print(receipt.method.value_counts().to_dict(),flush=True)


if __name__=="__main__":
    main()
