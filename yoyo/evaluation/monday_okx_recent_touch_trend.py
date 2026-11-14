"""Recent actual score publication, observable paths and a single trailing exit.

Signal attributes use the touch 5m OHLC only, entry uses the first post-score
1m close. Trail updates use completed subsequent exchange-aligned 5m closes.
Forward OHLC and touch-to-peak measurements are evaluation labels only.
"""
import gzip
import json
from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

from yoyo.evaluation import monday_reclaimed_wick_okx as source
from yoyo.layers.l3_backtest.nautilus_stop_schedule import run_schedule

OUT = source.ROOT / "experiments/active/exp-monday-okx-recent-touch-trend-20261115-v1"
CACHE = source.ROOT / "data/research/monday-okx-recent-touch-trend-20261115"
CFG = json.loads((OUT / "config.json").read_text())
CUTOFF = int(pd.Timestamp(CFG["cutoff_utc"]).timestamp() * 1000)
MIN = source.MIN


def minute_path(row, begin, end):
    pieces = []
    old_path = source.CACHE / f"{row.symbol}-{begin+5*MIN}-1m.csv.gz"
    if old_path.exists():
        pieces.append(pd.read_csv(old_path).query("open_time >= @begin and open_time < @end"))
    next_ms = begin if not pieces or pieces[0].empty else int(pieces[0].open_time.max()) + MIN
    path = CACHE / f"{row.symbol}-{begin}-{end}.csv.gz"
    if path.exists():
        return pd.read_csv(path)
    if next_ms < end:
        raw = source.candles(row.symbol, "1m", next_ms, end, CUTOFF)
        pieces.append(pd.DataFrame([v[:6] for v in raw], columns=["open_time","open","high","low","close","volume"]))
    joined = pd.concat(pieces, ignore_index=True).astype(float)
    joined["open_time"] = joined.open_time.astype("int64")
    if not np.array_equal(joined.open_time, np.arange(begin, end, MIN)):
        raise ValueError(f"Incomplete local/OKX minute path {row.symbol}")
    CACHE.mkdir(parents=True, exist_ok=True)
    joined.to_csv(path, index=False)
    return joined


def stop_exits(frame, signal, fixed, entry, horizon, tick):
    """The engine matches a fixed stop plus a schedule of known, improving levels."""
    clock = frame.index
    keys = np.flatnonzero((clock.minute % 5 == 0) & (np.arange(len(clock)) > 0))
    marks = frame.close.to_numpy()[keys]
    side = int(signal.side)
    best = np.maximum.accumulate(marks) if side == 1 else np.minimum.accumulate(marks)
    levels = np.where(side*(best/entry-1) >= .02-1e-12, best*(1-side*.02), np.nan)
    schedules = {}
    trades = []
    for name in ("fixed", "trail"):
        idx = np.r_[0,keys] if name == "trail" else np.array([0])
        prices = np.r_[np.nan,levels] if name == "trail" else np.array([np.nan])
        schedules[name] = (idx, prices, np.full(len(idx), clock[-1].value + 60_000_000_000))
        trades.append(dict(trade_id=f"{signal.id}-{name}", entry_i=0, entry=entry, side=side, stop=fixed, schedule=name))
    res, _ = run_schedule(frame, pd.DataFrame(trades), schedules, tick, symbol=signal.symbol.split("-")[0], execution_minutes=1)
    output = {}
    for result in res.itertuples():
        name = result.trade_id.split("-")[-1]
        idx = int(result.exit_i)
        ended = result.exit_reason != "data_end" or len(frame)-1 == horizon*60
        pre = frame.iloc[1:idx] if result.exit_reason != "data_end" else frame.iloc[1:idx+1]
        best_known = max(0., (side*(pre["high" if side==1 else "low"]/entry-1)*100).max()) if len(pre) else 0.
        if result.exit_reason != "data_end":
            best_known = max(best_known, side*(frame.open.iloc[idx]/entry-1)*100)
        output[name] = dict(net_pct=result.net_bp/100-.2, exit_time=clock[idx].isoformat(),
                            exit_reason=result.exit_reason if result.exit_reason!="data_end" else ("time" if ended else "cutoff_open"),
                            settled=ended, mfe_before_exit_pct=best_known, stop_updates=result.stop_updates)
    return output


def evaluate(row, catalog):
    begin = int(row.candle_ms)//(5*MIN)*(5*MIN)
    score_ms = int(row.scored_ms)
    start = (score_ms+MIN-1)//MIN*MIN
    end = min(start+72*60*MIN, CUTOFF)
    bars = minute_path(row, begin, end)
    if not np.array_equal(bars.open_time, np.arange(begin,end,MIN)):
        raise ValueError("noncontinuous OHLC")
    bars.index = pd.to_datetime(bars.open_time+MIN, unit="ms", utc=True).astype("datetime64[ns, UTC]")
    post = bars[bars.open_time >= start]
    entry = float(bars.close.loc[pd.Timestamp(start, unit="ms",tz="UTC")])
    side = int(row.side)
    wick = float(bars.low.iloc[:5].min() if side==1 else bars.high.iloc[:5].max())
    pre_entry = bars[(bars.open_time >= begin+5*MIN) & (bars.open_time < start)]
    breach = (pre_entry.low <= wick).any() if side==1 else (pre_entry.high >= wick).any()
    hi, lo = ("high","low") if side==1 else ("low","high")
    close5 = float(bars.close.iloc[4])
    tick = float(catalog["tickSz"])
    rec = dict(id=row.id, symbol=row.symbol, week=row.week, side=side, score=row.score, level=row.level,
               touch_ms=row.candle_ms, score_ms=score_ms, entry_ms=start, entry=entry, wick=wick,
               signal_close=close5, reclaimed=bool(side*(row.level-wick)>0 and side*(close5-row.level)>0),
               delay_seconds=(score_ms-begin-5*MIN)/1000, invalidated_before_entry=bool(breach),
               risk_pct=100*side*(entry-wick)/entry, actual_minutes=len(post))
    movement, exits = [], []
    for h in (6,24,72):
        part=post.iloc[:60*h]
        favorable=side*(part[hi]/entry-1)*100
        adverse=side*(part[lo]/entry-1)*100
        near_hits=part[hi]>=row.target if side==1 else part[hi]<=row.target
        d=dict(id=row.id,h=h,full=len(part)==60*h,mfe_pct=max(0.,favorable.max()),
               mae_pct=max(0.,-adverse.min()),net_close_pct=side*(part.close.iloc[-1]/entry-1)*100-.05*(1+part.close.iloc[-1]/entry)-.2,
               near_monday_edge_hit=near_hits.any(),minutes_to_peak=(favorable.idxmax().value//1_000_000-start)/MIN)
        for pct in (2,5,10):
            d[f"hit_{pct}"]=bool(favorable.max()>=pct)
        movement.append(d)
        if not breach and h in (6,72):
            frame=bars.loc[pd.Timestamp(start,unit="ms",tz="UTC"):part.index[-1]]
            for name, res in stop_exits(frame,row,wick,entry,h,tick).items():
                exits.append(dict(id=row.id, week=row.week,symbol=row.symbol,h=h,rule=name,entry_ms=start,**res))
    return rec,movement,exits


def main():
    cat=json.loads((source.EXP/"universe.json").read_text())
    with source.read_db("monday-reclaim-paper.sqlite3") as db:
        all_rows=pd.read_sql_query("SELECT id,week,symbol,side,level,target,status,score_basis,score,candle_ms,scored_ms FROM signals WHERE week IN (?,?) AND candle_ms < ? ORDER BY candle_ms,symbol", db, params=(*CFG["weeks"], CUTOFF))
        positions=pd.read_sql_query("SELECT p.signal_id,p.track,p.symbol,p.stage,p.taken,p.entry,p.reason,p.fill_ms,p.exit_ms,p.net_slip_bp FROM positions p JOIN signals s ON s.id=p.signal_id WHERE s.week IN (?,?) AND s.scored_ms<=? AND s.score>.5 AND p.taken=1 AND p.track IN (7060,8060)",db,params=(*CFG["weeks"],CUTOFF))
    hits=all_rows[(all_rows.status=="scored") & (all_rows.score_basis=="touch_close") & (all_rows.score>.5) & (all_rows.scored_ms<=CUTOFF)].copy()
    hits["category"]=[cat[s]["instCategory"] for s in hits.symbol]
    hits.to_csv(OUT/"high_scores.csv",index=False)
    positions.to_csv(OUT/"paper_fills.csv",index=False)
    facts,moves,exits=[],[],[]
    crypto=hits[(hits.category=="1") & ~hits.symbol.isin(["BTC-USDT-SWAP","ETH-USDT-SWAP"])]
    for row in crypto.itertuples():
        if cat[row.symbol]["ruleType"]!="normal":
            continue
        fact,move,ex=evaluate(row,cat[row.symbol])
        facts.append(fact); moves.extend(move); exits.extend(ex)
        print(row.symbol, len(ex), flush=True)
    pd.DataFrame(facts).to_csv(OUT/"entries.csv", index=False)
    pd.DataFrame(moves).to_csv(OUT/"movement.csv", index=False)
    results=pd.DataFrame(exits)
    results.to_csv(OUT/"exits.csv", index=False)
    picked=[]
    for (week,h,rule),group in results.groupby(["week","h","rule"]):
        end=-1
        for r in group.sort_values(["entry_ms","symbol"]).itertuples():
            if r.entry_ms>end:
                picked.append(r.Index);end=pd.Timestamp(r.exit_time).value//1_000_000
    results.loc[picked].to_csv(OUT/"one_position.csv", index=False)
    (OUT/"coverage.json").write_text(json.dumps(dict(cutoff=CFG["cutoff_utc"],score_gt_point5=len(hits),sample=len(facts),
          by_week=hits.groupby(["week","category"]).size().to_string(),engine="NautilusTrader 1.231.0"),indent=2)+"\n")


if __name__=="__main__":
    main()
