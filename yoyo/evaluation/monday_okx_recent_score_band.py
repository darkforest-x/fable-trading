"""Descriptive contemporaneous reference for the recent high-score OKX cohort.

Read completed score, direction, and first post-publication minute close.
Only the next six hours of 1m OHLC are used for outcome marks, without stops.
"""
from concurrent.futures import ThreadPoolExecutor
import json

import pandas as pd

from yoyo.evaluation import monday_okx_recent_touch_trend as study


def movement(row):
    base = row.candle_ms // (5*study.MIN) * (5*study.MIN)
    entry_ms = (row.scored_ms + study.MIN-1)//study.MIN*study.MIN
    end = entry_ms + 360*study.MIN
    if end > study.CUTOFF:
        raise ValueError("Incomplete six hours")
    bars = study.minute_path(row, base, end)
    ref = float(bars[bars.open_time == entry_ms-study.MIN].close.item())
    after = bars[bars.open_time >= entry_ms]
    assert len(after) == 360
    peak = after.high.max() if row.side==1 else after.low.min()
    last = after.close.iloc[-1] / ref
    return dict(id=row.id, symbol=row.symbol, score=row.score, side=row.side, entry_ms=entry_ms,
                entry=ref, mfe_pct=max(0., 100*row.side*(peak/ref-1)),
                six_hour_net_pct=100*row.side*(last-1)-.05*(1+last)-.2)


def main():
    with study.source.read_db("monday-reclaim-paper.sqlite3") as db:
        rows = pd.read_sql_query(
            """SELECT id,symbol,side,score,candle_ms,scored_ms FROM signals
               WHERE week='2026-11-09' AND score_basis='touch_close' AND status='scored'
               AND score>0.4 AND score<=0.5 AND scored_ms<=? ORDER BY scored_ms, symbol""",
            db, params=(study.CUTOFF,))
    cat_file = study.OUT/"reference_instruments.json"
    if not cat_file.exists():
        instruments = study.source.fetch("public/instruments", instType="SWAP")
        ids = set(rows.symbol)
        record = {r["instId"]:{c:r[c] for c in ("instCategory","ruleType")} for r in instruments if r["instId"] in ids}
        assert set(record)==ids
        cat_file.write_text(json.dumps(record,indent=2,sort_keys=True)+"\n")
    record = json.loads(cat_file.read_text())
    keys = {s for s,r in record.items() if r["instCategory"]=="1" and r["ruleType"]=="normal"}
    crypto = rows[rows.symbol.isin(keys-{"BTC-USDT-SWAP","ETH-USDT-SWAP"})]
    with ThreadPoolExecutor(max_workers=2) as pool:
        ref = pd.DataFrame(pool.map(movement, crypto.itertuples()))
    ref.to_csv(study.OUT/"reference_score_band.csv",index=False)
    print(ref.to_string(index=False))


if __name__=="__main__":
    main()
