"""Excursions from a completed 5m close until the original native stop or cutoff.

Post-entry 1m OHLC is used only for outcomes. MFE bounds cover both possible
orders on the stop minute. Giveback counts require a *later* minute, or an
observable adverse close after the minute's favourable extreme.
"""
import json
import numpy as np
import pandas as pd

from yoyo.evaluation.monday_reclaimed_wick_okx import EXP, ROOT
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def simulate(row, cfg):
    bars = pd.read_csv(ROOT/row.path)
    bars.index = pd.to_datetime(bars.open_time, unit="ms", utc=True) + pd.Timedelta(minutes=1)
    frame = bars[["open", "high", "low", "close", "volume"]]
    start = frame.index.get_loc(pd.to_datetime(row.entry_ms, unit="ms", utc=True))
    assert start == 4 and abs(frame.close.iloc[start] - row.entry) < row.tick/100
    risk = row.side*(row.entry-row.stop)
    order = dict(trade_id=str(row.id), side=int(row.side), entry=row.entry, stop=row.stop,
                 risk=risk, entry_i=start, end_i=len(frame)-1)
    fee = cfg["taker_fee_bps_each"]/10000
    slip = cfg["slippage_bps_each"]/10000
    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0,
                        fill_maker_fee=0.0002, fill_taker_fee=fee)
    trades, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
                                 pd.DataFrame([order]), policy, tick=row.tick,
                                 symbol=row.symbol.split("-")[0], execution_minutes=1)
    assert len(trades) == 1 and audit["positions"] == 1
    trade = trades.iloc[0]
    stop_hit = int(trade.reason) == 1
    assert stop_hit or int(trade.exit_i) == len(frame)-1
    # Commission uses actual leg notionals; slip assumes 10bp of entry notional per market leg.
    be = (2*fee + 2*slip)/(1-row.side*fee)*100
    rows = []
    for h in cfg["horizons_hours"] + [0]:
        last = min(start+60*h, len(frame)-1) if h else len(frame)-1
        exit_i = int(trade.exit_i)
        stopped = stop_hit and exit_i <= last
        mature = not h or last-start == h*60
        last_held = min(exit_i,last)
        view = frame.iloc[start+1:last_held+1]
        favorable = 100*row.side*((view.high if row.side == 1 else view.low)/row.entry-1)
        adverse = 100*row.side*((view.low if row.side == 1 else view.high)/row.entry-1)
        close_pct = 100*row.side*(view.close/row.entry-1)
        certain = favorable.copy()
        if stopped:
            certain.iloc[-1] = 100*row.side*(view.open.iloc[-1]/row.entry-1)
        mark = trade.exit_price if stopped else view.close.iloc[-1]
        net = 100*(row.side*(mark/row.entry-1)-fee*(1+mark/row.entry)-2*slip)
        answer = dict(id=int(row.id), symbol=row.symbol, score=row.score, side=int(row.side),
                      entry_ms=int(row.entry_ms), horizon_h=h, full_clock=mature,
                      observed_minutes=int(last-start), stopped=stopped, outcome_ms=int(view.index[-1].timestamp()*1000),
                      risk_pct=risk/row.entry*100, mfe_pct=max(0.,certain.max()), mfe_upper_pct=max(0.,favorable.max()),
                      mark_or_exit_net_pct=net, be_move_pct=be)
        for trigger in (2,5,10):
            ix = np.flatnonzero(certain.to_numpy()>=trigger)
            hit = bool(ix.size)
            giveback = False
            if hit:
                j = int(ix[0])
                giveback = (close_pct.iloc[j]<=be or (adverse.iloc[j+1:]<=be).any() or (stopped and j == len(view)-1))
            answer[f"hit{trigger}"] = hit
            answer[f"back{trigger}"] = bool(giveback)
        rows.append(answer)
    return rows


def run():
    cfg = json.loads((EXP / "config.json").read_text())
    signals = pd.read_csv(EXP / "selection.csv")
    eligible = signals[signals.status.eq("selected")]
    rows = [record for row in eligible.itertuples() for record in simulate(row,cfg)]
    df = pd.DataFrame(rows)
    df.to_csv(EXP/"outcomes.csv", index=False)
    summary = []
    for h in cfg["horizons_hours"]+[0]:
        g = df[df.horizon_h.eq(h) & df.full_clock]
        d = dict(horizon_h=h, n=len(g), stopped=int(g.stopped.sum()), mfe_median=float(g.mfe_pct.median()),
                 risk_median=float(g.risk_pct.median()), net_mean=float(g.mark_or_exit_net_pct.mean()))
        for k in (2,5,10):
            d[f"hit{k}"] = int(g[f"hit{k}"].sum())
            d[f"back{k}"] = int(g[f"back{k}"].sum())
            d[f"back{k}_stopped"] = int((g[f"back{k}"] & g.stopped).sum())
        summary.append(d)
    (EXP/"summary.json").write_text(json.dumps(summary,indent=2)+"\n")
    print(pd.DataFrame(summary).to_string(index=False))


if __name__ == "__main__":
    run()
