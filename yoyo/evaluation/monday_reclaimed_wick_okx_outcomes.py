"""Excursions from a completed 5m close until the original native stop or cutoff.

Post-entry 1m OHLC is used only for outcomes. MFE bounds cover both possible
orders on the stop minute. Giveback counts require a *later* minute, or an
observable adverse close after the minute's favourable extreme.
"""
import argparse
import json
import numpy as np
import pandas as pd

from yoyo.evaluation.monday_reclaimed_wick_okx import EXP, ROOT
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native


def simulate(row, cfg, post_score=False, arm_at_two=False):
    bars = pd.read_csv(ROOT/row.path)
    bars.index = pd.to_datetime(bars.open_time, unit="ms", utc=True) + pd.Timedelta(minutes=1)
    frame = bars[["open", "high", "low", "close", "volume"]]
    start = frame.index.get_loc(pd.to_datetime(row.entry_ms, unit="ms", utc=True))
    assert start == 4 and abs(frame.close.iloc[start] - row.entry) < row.tick/100
    if post_score:
        # The first observable full minute-close at or after the archived score.
        decision = pd.to_datetime(row.scored_ms, unit="ms", utc=True).ceil("min")
        after = frame.index.get_loc(decision)
        pending = frame.iloc[start+1:after+1]
        invalid = (pending.low <= row.stop).any() if row.side == 1 else (pending.high >= row.stop).any()
        if invalid:
            return []
        start = after
        row = row._replace(entry=float(frame.close.iloc[start]), entry_ms=int(decision.timestamp()*1000))
        frame = frame.iloc[:min(len(frame), start + 1 + 6*60)]
    risk = row.side*(row.entry-row.stop)
    order = dict(trade_id=str(row.id), side=int(row.side), entry=row.entry, stop=row.stop,
                 risk=risk, entry_i=start, end_i=len(frame)-1)
    fee = cfg["taker_fee_bps_each"]/10000
    slip = cfg["slippage_bps_each"]/10000
    be = (2*fee+2*slip)/(1-row.side*fee)*100
    if arm_at_two:
        if not post_score:
            raise ValueError("The exploratory stop update uses only the post-score entry")
        # Adapt the native scheduled stop at the first completed minute close
        # that can place it, after a favourable excursion of at least 2%.
        price = frame.high if row.side == 1 else frame.low
        best = (row.side*(price.iloc[start+1:-1]/row.entry-1)).cummax()
        ticks = row.entry*(1+row.side*be/100)/row.tick
        be_price = (np.ceil(ticks) if row.side == 1 else np.floor(ticks))*row.tick
        placeable = row.side*(frame.close.loc[best.index]-be_price) >= row.tick*(1-1e-7)
        hits = best.index[(best >= 0.02) & placeable]
        if len(hits):
            order.update(stop_update_i=frame.index.get_loc(hits[0]), stop_update=be_price)

    policy = ExitPolicy(trail_arm_r=1e12, opposite_hour_exit=False, round_trip_cost=0,
                        fill_maker_fee=0.0002, fill_taker_fee=fee)
    trades, _, audit = run_native(frame, np.full(len(frame), np.nan), np.zeros(len(frame)),
                                 pd.DataFrame([order]), policy, tick=row.tick,
                                 symbol=row.symbol.split("-")[0], execution_minutes=1)
    assert len(trades) == 1 and audit["positions"] == 1
    trade = trades.iloc[0]
    stop_hit = int(trade.reason) == 1
    adjusted_stop = int(trade.reason) == 9
    if arm_at_two:
        assert stop_hit or adjusted_stop or int(trade.exit_i) == len(frame)-1
        return [dict(id=int(row.id), symbol=row.symbol, entry_ms=int(row.entry_ms),
                     native_exit_reason=int(trade.reason), net_pct=trade.net_notional_bp/100-200*slip,
                     update_scheduled="stop_update_i" in order,
                     observed_minutes=int(trade.exit_i)-start)]
    assert stop_hit or int(trade.exit_i) == len(frame)-1
    # Commission uses actual leg notionals; slip assumes 10bp of entry notional per market leg.
    rows = []
    for h in ([1,6] if post_score else cfg["horizons_hours"] + [0]):
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


def run(post_score=False, arm_at_two=False):
    cfg = json.loads((EXP / "config.json").read_text())
    signals = pd.read_csv(EXP / "selection.csv")
    eligible = signals[signals.status.eq("selected")]
    rows = [record for row in eligible.itertuples() for record in simulate(row,cfg,post_score,arm_at_two)]
    df = pd.DataFrame(rows)
    if arm_at_two:
        base = pd.read_csv(EXP/"post-score-outcomes.csv")
        base = base.loc[base.horizon_h.eq(6), ["id","mark_or_exit_net_pct"]]
        joined = df.merge(base, on="id", validate="one_to_one", how="outer", indicator=True)
        assert joined["_merge"].eq("both").all()
        joined["difference_pct"] = joined.net_pct - joined.mark_or_exit_net_pct
        joined.drop(columns="_merge").to_csv(EXP/"post-score-be2-comparison.csv", index=False)
        print(joined.groupby("native_exit_reason")[["net_pct","mark_or_exit_net_pct","difference_pct"]].agg(["count","mean"]))
        return
    prefix = "post-score-" if post_score else ""
    df.to_csv(EXP/(prefix+"outcomes.csv"), index=False)
    summary = []
    for h in ([1,6] if post_score else cfg["horizons_hours"]+[0]):
        g = df[df.horizon_h.eq(h) & df.full_clock]
        d = dict(horizon_h=h, n=len(g), stopped=int(g.stopped.sum()), mfe_median=float(g.mfe_pct.median()),
                 risk_median=float(g.risk_pct.median()), net_mean=float(g.mark_or_exit_net_pct.mean()))
        for k in (2,5,10):
            d[f"hit{k}"] = int(g[f"hit{k}"].sum())
            d[f"back{k}"] = int(g[f"back{k}"].sum())
            d[f"back{k}_stopped"] = int((g[f"back{k}"] & g.stopped).sum())
        summary.append(d)
    (EXP/(prefix+"summary.json")).write_text(json.dumps(summary,indent=2)+"\n")
    print(pd.DataFrame(summary).to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--post-score", action="store_true")
    parser.add_argument("--arm-at-two", action="store_true")
    args = parser.parse_args()
    run(args.post_score,args.arm_at_two)
