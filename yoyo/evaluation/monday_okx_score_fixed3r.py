"""Evaluate recorded OKX Monday scores with one 3% stop and fixed R targets.

Entry uses only score time, side and the first minute close after publication.
Subsequent continuous 1m trade OHLC are outcomes. Local cache preparation can
end on any chunk covering the adverse barrier; the framework decides all fills.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import sqlite3

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_reclaimed_wick_okx as source
from yoyo.evaluation import monday_reclaimed_wick_target_win as target_engine
from yoyo.evaluation.monday_wick_r_engine import first_hit

OUT = source.ROOT / "experiments/active/exp-monday-okx-score-fixed3r-20261115-v1"
CFG = json.loads((OUT / "config.json").read_text())
CACHE = source.ROOT / "data/research/monday-okx-score-fixed3r-20261115"
MIN = source.MIN
CUTOFF = pd.Timestamp(CFG["price_cutoff_utc"]).value // 1_000_000
COLS = ["open_time", "open", "high", "low", "close"]
RISK = CFG["stop_fraction"]


def cohort():
    path = OUT / "signals.csv"
    if path.exists():
        return pd.read_csv(path)
    with source.read_db("monday-reclaim-paper.sqlite3") as db:
        db.row_factory = sqlite3.Row
        source_until = pd.Timestamp(CFG["signal_ledger_cutoff_utc"]).value // 1_000_000
        rows = pd.read_sql_query(
            """SELECT id,symbol,week,side,score,level,candle_ms,scored_ms FROM signals
               WHERE status='scored' AND score_basis='touch_close' AND score>0.5
                 AND scored_ms<=? ORDER BY candle_ms,symbol""", db, params=(source_until,))
    catalog = json.loads((source.EXP / "universe.json").read_text())
    for symbol in set(rows.symbol) - catalog.keys():
        catalog[symbol] = source.fetch("public/instruments", instType="SWAP", instId=symbol)[0]
    rows["category"] = rows.symbol.map(lambda sym: int(catalog[sym]["instCategory"]))
    rows["tick"] = rows.symbol.map(lambda sym: float(catalog[sym]["tickSz"]))
    assert len(rows) == 67 and not rows.duplicated(["week", "symbol", "side"]).any()
    assert rows.category.value_counts().to_dict() == {1: 38, 3: 29}
    rows.to_csv(path, index=False)
    return rows


def barrier(entry, side, multiple, tick):
    # Both the stop (toward entry) and the target (away) round up for longs.
    price = Decimal(str(entry)) * (1 + Decimal(str(RISK)) * Decimal(str(side * multiple)))
    step = Decimal(str(tick))
    rounding = ROUND_CEILING if side > 0 else ROUND_FLOOR
    return float((price / step).to_integral_value(rounding=rounding) * step)


def cached_minutes(row, start, end):
    chunk = CACHE / f"{row.symbol}-{start}-{end}.csv.gz"
    if chunk.exists():
        data = pd.read_csv(chunk)
    else:
        frames = []
        touch = int(row.candle_ms) // (5*MIN) * (5*MIN)
        old = [source.CACHE / f"{row.symbol}-{touch+5*MIN}-1m.csv.gz"]
        previous = source.ROOT / "data/research/monday-okx-recent-touch-trend-20261115"
        old.extend(previous.glob(f"{row.symbol}-{touch}-*.csv.gz"))
        for path in old:
            if path.is_file():
                part = pd.read_csv(path, usecols=COLS)
                frames.append(part[(part.open_time >= start) & (part.open_time < end)])
        known = pd.concat(frames).drop_duplicates("open_time") if frames else pd.DataFrame(columns=COLS)
        stamps = np.arange(start, end, MIN)
        missing = np.setdiff1d(stamps, known.open_time.to_numpy(dtype="int64"))
        pieces = [known] if len(known) else []
        for run in np.split(missing, np.flatnonzero(np.diff(missing) != MIN)+1):
            if len(run):
                raw = source.candles(row.symbol, "1m", int(run[0]), int(run[-1])+MIN, CUTOFF)
                pieces.append(pd.DataFrame([v[:5] for v in raw], columns=COLS))
        data = pd.concat(pieces, ignore_index=True).astype({k: "float64" for k in COLS[1:]})
        data["open_time"] = data.open_time.astype("int64")
        data = data.sort_values("open_time")
        assert np.array_equal(data.open_time, stamps)
        CACHE.mkdir(parents=True, exist_ok=True)
        tmp = chunk.with_suffix(".tmp.gz")
        data.to_csv(tmp, index=False)
        tmp.replace(chunk)
    if not np.array_equal(data.open_time, np.arange(start, end, MIN)):
        raise ValueError(f"Non-contiguous minute history: {row.symbol} {start}")
    return data


def price_path(row):
    entry_ms = int((row.scored_ms + MIN - 1) // MIN * MIN)
    start = entry_ms - MIN
    parts = []
    while start < CUTOFF:
        end = min(start + (600 if not parts else 1800) * MIN, CUTOFF)
        part = cached_minutes(row, start, end)
        if not parts:
            entry = float(part.close.iloc[0])
            stop = barrier(entry, row.side, -1, row.tick)
            post = part.iloc[1:]
        else:
            post = part
        parts.append(part)
        # Determine data coverage only. Overshoot by up to one chunk; keep its
        # OHLC intact, even when both barriers are touched on the same minute.
        if (row.side == 1 and post.low.min() <= stop) or (row.side == -1 and post.high.max() >= stop):
            break
        start = end
    return pd.concat(parts, ignore_index=True)


def evaluate(row, frame):
    arrays = [np.ascontiguousarray(frame[k].to_numpy(float)[:, None]) for k in COLS[1:]]
    n = len(frame)
    work = (np.empty((n,1)), np.empty((n,1), dtype=np.int64), np.empty(1, dtype=np.int64))
    entry = frame.close.iloc[0]
    start = int(frame.open_time.iloc[0]) + MIN
    side = int(row.side)
    stop = barrier(entry, side, -1, row.tick)
    ticks = {}
    trades = []
    detail = {}
    for multiple in (0, *CFG["targets_R"]):
        target = barrier(entry, side, multiple, row.tick) if multiple else np.nan
        i, price, why, ambiguous = first_hit(*arrays, 1, n-1, entry, stop, target, side, *work)
        if why == 0:
            i, price = n-1, frame.close.iloc[-1]
            assert frame.open_time.iloc[i]+MIN == CUTOFF
        end = int(frame.open_time.iloc[i])+MIN
        ticks[multiple] = (why, end)
        trades.append((str(row.id), row.symbol, "signal", multiple*RISK*100, side, entry, stop,
                       start, end, price, why, ambiguous, why != 0))
        if multiple:
            continue
        best_field = "high" if side == 1 else "low"
        favors = side*(frame[best_field].iloc[1:i+1].to_numpy(dtype=float, copy=True)/entry-1)/RISK
        upper = max(0., np.max(favors))
        if why == 1:
            opening_r = side*(frame.open.iloc[i]/entry-1)/RISK
            favors[-1] = opening_r
            if side*(frame.open.iloc[i]-stop) <= 0:
                upper = max(0., np.max(favors))
        peak = max(0., np.max(favors))
        peak_minute = int(frame.open_time.iloc[1+np.argmax(favors)]) if peak > 0 else start
        detail = dict(id=row.id, week=row.week, symbol=row.symbol, category=row.category, side=side,
                      score=row.score, scored_ms=row.scored_ms, entry_ms=start, entry=entry, stop=stop,
                      mfe_R=peak, mfe_upper_R=upper, peak_minute_ms=peak_minute,
                      stopped=bool(why), end_ms=end, stop_fill_or_mark=price,
                      hours_observed=(end-start)/3600000)
        for h in (6,24,72):
            observed = why or len(favors)>=60*h
            detail[f"mfe_{h}h_R"] = max(0., np.max(favors[:60*h])) if observed else np.nan
    for multiple, (why, end) in ticks.items():
        if multiple:
            assert (why == 4) == (detail["mfe_R"] + 1e-9 >= abs(barrier(entry, side, multiple, row.tick)/entry-1)/RISK)
    return detail, trades


def main():
    signals = cohort()
    assert CFG["costs"] == target_engine.CFG["costs"]
    evaluated = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(price_path, row): row for row in signals.itertuples()}
        for i, job in enumerate(as_completed(jobs), 1):
            row = jobs[job]
            evaluated.append(evaluate(row, job.result()))
            print(f"{i}/{len(jobs)} {row.symbol}", flush=True)
    detail = pd.DataFrame([d for d,_ in evaluated]).sort_values(["entry_ms","symbol"])
    fills = target_engine.account_fills([t for _, batch in evaluated for t in batch])
    fills["target_R"] = (fills.target_pct/(100*RISK)).round(7)
    fills["single"] = False
    for _, group in fills.groupby("target_R"):
        selected = target_engine.select_single(group)
        fills.loc[selected.index,"single"] = True
    fills = fills.sort_values(["target_R","entry_ms","symbol"])
    assert fills.groupby("target_R").size().eq(67).all()
    zero = fills[fills.target_R==0].set_index(fills[fills.target_R==0].event_id.astype(int))
    gross = detail.side*(detail.stop_fill_or_mark/detail.entry-1)
    detail["net_stop_or_mark_R"] = (gross - CFG["costs"]["taker"]*(1+detail.stop_fill_or_mark/detail.entry)
                                    - 2*CFG["costs"]["market_slip_bp"]/10000)/RISK
    stopped = detail[detail.stopped]
    assert np.allclose(stopped.net_stop_or_mark_R, zero.loc[stopped.id,"net_pct"]/(100*RISK))
    detail.to_csv(OUT / "events.csv", index=False)
    fills.to_csv(OUT / "targets.csv", index=False)
    metrics = []
    for multiple, group in fills.groupby("target_R"):
        for scope, selected in [("all",group), ("single",group[group.single])]:
            won, lost = (selected.reason == 4).sum(), (selected.reason == 1).sum()
            metrics.append(dict(target_R=multiple, scope=scope, signals=len(selected), hit=won, stopped=lost,
                 open=len(selected)-won-lost, ambiguous=selected.ambiguous.sum(),
                 mean_net_settled_R=selected.net_pct.mean()/(RISK*100),
                 net_win_loss_ratio=-selected.loc[selected.reason==4,"net_pct"].mean()/selected.loc[selected.reason==1,"net_pct"].mean()))
    pd.DataFrame(metrics).to_csv(OUT / "metrics.csv", index=False)


if __name__ == "__main__":
    main()
