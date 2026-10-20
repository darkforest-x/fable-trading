"""Engineering replay for V2.7 armed-only opposite-hour exits.

Source: V2.5 reference and registered native PEPE 5m/15m/1H OHLC. At each
completed 5m bar use only its OHLC, trailing 15m ATR14, and a same-close 1H signal.
Old protection is evaluated before arming/ratchet and opposite-hour close exit.
No account return, live orders, data download, or profitability inference.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
import pandas as pd
from yoyo.evaluation.monday_range_v2_5_reference import Trade as PriorTrade, replay as prior_replay
from yoyo.evaluation.monday_range_v2_2_reference import signals, atr, round_stop
from yoyo.evaluation.monday_range_entry_diagnostic import load


class Trade(PriorTrade):
    def advance(self, start, end, o, h, l, c, atr15, hour_side=0):
        super().advance(start, end, o, h, l, c, atr15)
        if not self.ended and self.armed_at and hour_side == -self.side and end >= self.armed_at and end > self.entered:
            self.ended = end
            self.exit_price = float(c)
            self.reason = 3
            self.current_r = self.side * (c - self.entry) / (self.side * (self.entry - self.initial_stop))


def replay() -> dict:
    five, quarter, hour = load("5m", 5), load("15m", 15), load("1H", 60)
    hs, qs, qa = signals(hour), signals(quarter), atr(quarter)
    lows, highs = five.low.rolling(5).min(), five.high.rolling(5).max()
    prefix = hs.loc[hs.index < five.index[0]]
    prefix = prefix[prefix != 0]
    side = int(prefix.iloc[-1]) if len(prefix) else 0
    observed = prefix.index[-1].value // 1_000_000 if len(prefix) else 0
    trades: list[Trade] = []
    max_active = 0
    for stamp, row in five.iterrows():
        end = int(stamp.value // 1_000_000)
        start = end - 300_000
        h_side = int(hs.get(stamp, 0))
        if h_side:
            side, observed = h_side, end
        a15 = float(qa.loc[stamp]) if stamp in qa.index else None
        for trade in trades:
            trade.advance(start, end, row.open, row.high, row.low, row.close, a15, h_side)
        if side and int(qs.get(stamp, 0)) == side and end > observed:
            extreme = float(lows.loc[stamp] if side == 1 else highs.loc[stamp])
            if math.isfinite(extreme):
                initial = round_stop(side, extreme, row.close * .0005, 1e-9)
                if initial > 0 and side * (row.close - initial) / 1e-9 >= 1 - 1e-8:
                    trades.append(Trade(end, observed, side, float(row.close), initial, initial))
        max_active = max(max_active, sum(t.ended == 0 for t in trades))
    begin = pd.Timestamp("2026-10-16", tz="Asia/Shanghai").value // 1_000_000
    finish = pd.Timestamp("2026-10-18", tz="Asia/Shanghai").value // 1_000_000
    audit = [asdict(t) for t in trades if begin <= t.entered < finish]
    target_at = pd.Timestamp("2026-10-16 13:30", tz="Asia/Shanghai").value // 1_000_000
    target = next(t for t in audit if t["entered"] == target_at)
    assert abs((target["entry"] - .000004134) / 1e-9) < 1e-6
    assert abs((target["initial_stop"] - .000004160) / 1e-9) < 1e-6
    grouped = pd.DataFrame([asdict(t) for t in trades]).groupby("observed").size()
    assert grouped.max() > 1
    return dict(source="registered PEPE native OKX OHLC", count=len(trades),
                max_simultaneous=max_active, maximum_entries_per_h1_observation=int(grouped.max()),
                audit=audit, trades=[asdict(t) for t in trades],
                through=int(five.index[-1].value // 1_000_000),
                scope="Engineering parity only; independent references, no account return.")


if __name__ == "__main__":
    result = replay()
    old_rows = {t["entered"]: t for t in prior_replay()["trades"]}
    result["changed_exits"] = [dict(entered=t["entered"], prior=old_rows[t["entered"]], current=t)
                               for t in result["trades"] if t["reason"] == 3]
    out = Path("output/pine/monday_range_v2_7_reference.json")
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"entries": result["count"], "changed": result["changed_exits"]}, indent=2))
