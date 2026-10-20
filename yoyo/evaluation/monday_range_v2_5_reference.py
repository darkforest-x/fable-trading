"""Independent native-OHLC replay for V2.5 multi-entry path validation.

Owner 2026-10-20: every closed H1 signal resets observation; every later
same-side 15m signal may enter. Uses registered PEPE OHLC only; Stoch5/3/3,
15m ATR14 and five completed 5m extrema. Stops/ATR never see future candles.
No orders, account simulation, or profitability inference.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import pandas as pd
from yoyo.evaluation.monday_range_v2_2_reference import signals, atr, round_stop
from yoyo.evaluation.monday_range_entry_diagnostic import load

@dataclass
class Trade:
    entered: int
    observed: int
    side: int
    entry: float
    initial_stop: float
    protection: float
    peak_r: float = 0.0
    current_r: float = 0.0
    armed_at: int = 0
    ended: int = 0
    exit_price: float | None = None
    reason: int = 0
    tick: float = 1e-9

    def advance(self, start: int, end: int, o: float, h: float, l: float, c: float, atr15: float | None) -> None:
        """Test old protection before close-based ratchet; entry bar cannot exit."""
        if self.ended or start < self.entered:
            return
        risk = self.side * (self.entry - self.initial_stop)
        stopped = l <= self.protection if self.side == 1 else h >= self.protection
        if stopped:
            gap = o < self.protection if self.side == 1 else o > self.protection
            self.exit_price = min(o, self.protection) if self.side == 1 else max(o, self.protection)
            self.ended = start if gap else end
            self.reason = 2 if self.armed_at else 1
            self.current_r = self.side * (self.exit_price - self.entry) / risk
            return
        self.peak_r = max(self.peak_r, self.side * ((h if self.side == 1 else l) - self.entry) / risk)
        self.current_r = self.side * (c - self.entry) / risk
        if atr15 is not None and not math.isnan(atr15) and atr15 > 0:
            if not self.armed_at and self.current_r >= 5:
                self.armed_at = end
            if self.armed_at:
                candidate = round_stop(self.side, c, 4 * atr15, self.tick)
                self.protection = max(self.protection, candidate) if self.side == 1 else min(self.protection, candidate)


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
            trade.advance(start, end, row.open, row.high, row.low, row.close, a15)
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
    out = Path("output/pine/monday_range_v2_5_reference.json")
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "trades"}, indent=2))
