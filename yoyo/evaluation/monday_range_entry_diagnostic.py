"""Replay V2.4 observation/consumption gates for one PEPE missing-entry query.

Source: owner query on 2026-10-20 and monday_range_v2_4.pine at ad2586953b.
Reads native closed OKX 1H/15m OHLC, with causal Stoch(5,3,3) windows.
The 5m stop check uses only the five completed bars before the target close.
This audits admission, not trade exits, account returns, or profitability.
"""
from pathlib import Path
import json
import math

import pandas as pd

from yoyo.evaluation.monday_range_v2_2_reference import signals


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "experiments/active/exp-pepe-range-entry-diagnostic-20261020-v1"
TARGET = pd.Timestamp("2026-10-16 13:30", tz="Asia/Shanghai")


def load(tf: str, minutes: int) -> pd.DataFrame:
    files = list((RUN / "inputs").glob(f"*_{tf}_*.csv"))
    assert len(files) == 1, files
    frame = pd.read_csv(files[0])
    frame.index = pd.to_datetime(frame.ts, unit="ms", utc=True).dt.tz_convert("Asia/Shanghai")
    assert frame.index.is_unique and frame.index.is_monotonic_increasing
    assert (frame.index.to_series().diff().dropna() == pd.Timedelta(minutes=minutes)).all()
    frame.index += pd.Timedelta(minutes=minutes)
    frame["signal"] = signals(frame)
    return frame


def replay(hours: pd.DataFrame, quarters: pd.DataFrame, rearm_each_hour: bool = False) -> pd.DataFrame:
    side, since, consumed = 0, None, False
    rows = []
    for at, row in quarters.iterrows():
        h_side = int(hours.loc[at, "signal"]) if at in hours.index else 0
        changed = bool(h_side and (h_side != side or rearm_each_hour))
        if changed:
            side, since, consumed = h_side, at, False
        q_side = int(row.signal)
        consumed_before = consumed
        accepted = bool(side and not consumed and q_side == side and at > since)
        if accepted:
            consumed = True
        if h_side or q_side or at == TARGET:
            rows.append(dict(close_at=at.isoformat(), h_side=h_side, q_side=q_side,
                watch_side=side, watch_since=since.isoformat() if since is not None else None,
                rearmed=changed, consumed_before=consumed_before, eligible=accepted,
                close=float(row.close)))
    return pd.DataFrame(rows)


def main() -> None:
    hours, quarters, fives = load("1H", 60), load("15m", 15), load("5m", 5)
    actual = replay(hours, quarters)
    counterfactual = replay(hours, quarters, rearm_each_hour=True)
    actual.to_csv(RUN / "gate_trace.csv", index=False)
    target = actual.loc[actual.close_at == TARGET.isoformat()].iloc[0].to_dict()
    alternative = counterfactual.loc[counterfactual.close_at == TARGET.isoformat()].iloc[0].to_dict()
    first = actual.loc[(actual.watch_since == target["watch_since"]) & actual.eligible].iloc[0].to_dict()
    window = fives.loc[:TARGET].tail(5)
    assert window.index[-1] == TARGET
    entry = float(quarters.loc[TARGET, "close"])
    stop = math.ceil((float(window.high.max()) + entry * .0005) / 1e-9 - 1e-8) * 1e-9
    k = (100 * (quarters.close - quarters.low.rolling(5).min()) /
         (quarters.high.rolling(5).max() - quarters.low.rolling(5).min())).rolling(3).mean()
    d = k.rolling(3).mean()
    assert target["q_side"] == -1 and target["consumed_before"] and not target["eligible"]
    assert target["watch_since"] == "2026-10-06T19:00:00+08:00"
    assert first["close_at"] == "2026-10-08T22:45:00+08:00"
    assert alternative["eligible"] and alternative["watch_since"] == "2026-10-16T03:00:00+08:00"
    assert stop > entry and (stop - entry) / 1e-9 >= 1
    # Native chart at the queried candle: K 81.81, D 85.33, close .000004134.
    assert round(float(k.loc[TARGET]), 2) == 81.81
    assert round(float(d.loc[TARGET]), 2) == 85.33
    assert abs((entry - .000004134) / 1e-9) < 1e-6
    result = dict(symbol="OKX:PEPEUSDT.P", timezone="Asia/Shanghai",
        candle_open="2026-10-16T13:15:00+08:00", target=target,
        first_admission=first, counterfactual_rearm_each_h1=alternative,
        target_k=float(k.loc[TARGET]), target_d=float(d.loc[TARGET]),
        hypothetical_initial_stop=stop, buffer_percent=.05,
        source_version="V2.4 ad2586953b", pine_changed=False,
        native_first_frame=dict(entry=.000004015, stop=.000004048),
        counts=dict(h1=len(hours), m15=len(quarters), m5=len(fives)),
        limitation="Admission diagnosis only; no profit inference or new rule deployed.")
    (RUN / "receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
