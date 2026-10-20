"""Independent minute-data parity oracle for the V2.2 Pine reference frames.

Source: owner-confirmed 2026-10-20 rules; registered Task 62 OKX ETH 1m files.
Uses only completed OHLC: Stoch(5,3,3) on 1H/15m, Wilder ATR(14) on 15m,
and latest five completed 5m lows/highs at entry. No future pivots. This is
engineering parity evidence, not an executable strategy or profitability study.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
INPUTS = [
    "data/crypto/market/kline_lowtf_20260922/okx_ETH_USDT_SWAP_1m_1402560.csv",
    "data/crypto/market/kline_lowtf_20260922/okx_api_ETH_USDT_SWAP_1m_20260831T1600_20260922T1400.csv",
    "data/crypto/market/research-spike_v128_bb_slope_20260924/okx_api_ETH_USDT_SWAP_1m_20260922T1359_20260923T1600.csv",
    "experiments/active/exp-eth5m-stoch-confirm-exits-20261019-v1/inputs/okx_ETH_USDT_SWAP_1m_38899.csv",
]


def load_minutes() -> pd.DataFrame:
    """Read registered inputs without downloading or modifying historical data."""
    pieces = []
    start = pd.Timestamp("2026-06-01", tz="UTC").value // 1_000_000
    for name in INPUTS:
        for frame in pd.read_csv(ROOT / name, usecols=["ts", "open", "high", "low", "close"], chunksize=200_000):
            pieces.append(frame[frame.ts >= start])
    data = pd.concat(pieces).drop_duplicates("ts", keep="last").sort_values("ts")
    data.index = pd.to_datetime(data.pop("ts"), unit="ms", utc=True)
    return data


def aggregate(minutes: pd.DataFrame, width: int) -> pd.DataFrame:
    """Index by close time; reject incomplete source candles."""
    rule = f"{width}min"
    frame = minutes.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    frame = frame[minutes.close.resample(rule).count() == width]
    frame.index += pd.Timedelta(minutes=width)
    return frame


def signals(frame: pd.DataFrame) -> pd.Series:
    low, high = frame.low.rolling(5).min(), frame.high.rolling(5).max()
    k = (100 * (frame.close - low) / (high - low)).rolling(3).mean()
    d = k.rolling(3).mean()
    up = (k > d) & (k.shift() <= d.shift()) & (k < 20) & (d < 20)
    down = (k < d) & (k.shift() >= d.shift()) & (k > 80) & (d > 80)
    return up.astype(int) - down.astype(int)


def atr(frame: pd.DataFrame, length: int = 14) -> pd.Series:
    tr = pd.concat([frame.high - frame.low, (frame.high - frame.close.shift()).abs(), (frame.low - frame.close.shift()).abs()], axis=1).max(axis=1)
    values = [float("nan")] * len(frame)
    values[length - 1] = tr.iloc[:length].mean()
    for i in range(length, len(values)):
        values[i] = (values[i - 1] * (length - 1) + tr.iloc[i]) / length
    return pd.Series(values, index=frame.index)


def round_stop(side: int, extreme: float, buffer: float, tick: float = .01) -> float:
    raw = extreme - side * buffer
    return (math.floor(raw / tick + 1e-8) if side == 1 else math.ceil(raw / tick - 1e-8)) * tick


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
                candidate = round_stop(self.side, c, 4 * atr15)
                self.protection = max(self.protection, candidate) if self.side == 1 else min(self.protection, candidate)


def replay(minutes: pd.DataFrame, source_start: int) -> dict:
    five, quarter, hour = [aggregate(minutes, width) for width in (5, 15, 60)]
    hs, qs, qa = signals(hour), signals(quarter), atr(quarter)
    lows, highs = five.low.rolling(5).min(), five.high.rolling(5).max()
    side, observed, consumed = 0, 0, False
    trades: list[Trade] = []
    for stamp, row in five.iterrows():
        end = stamp.value // 1_000_000
        start = end - 300_000
        if start < source_start:
            continue
        h_side = int(hs.get(stamp, 0))
        if h_side and h_side != side:
            side, observed, consumed = h_side, end, False
        a15 = float(qa.loc[stamp]) if stamp in qa.index else None
        for trade in trades:
            trade.advance(start, end, row.open, row.high, row.low, row.close, a15)
        if side and not consumed and int(qs.get(stamp, 0)) == side and end > observed:
            consumed = True
            initial = round_stop(side, float(lows.loc[stamp] if side == 1 else highs.loc[stamp]), 1)
            if initial > 0 and side * (row.close - initial) >= .01:
                trades.append(Trade(end, observed, side, float(row.close), initial, initial))
    return {"source_start": source_start, "through": int(five.index[-1].value // 1_000_000), "count": len(trades), "watch": [side, observed, consumed], "trades": [asdict(t) for t in trades]}


def compare_native(result: dict, directory: Path) -> dict:
    """Compare UI-captured Pine rows and all-record weighted signatures."""
    by_time = {trade["entered"]: trade for trade in result["trades"]}
    native = {}
    for path in directory.glob("monday_range_v2_2_native*.txt"):
        for row in re.findall(r"TX_ROW (\d[^\n]+)", path.read_text()):
            fields = row.split(",")
            if len(fields) == 13:
                native[int(fields[0])] = fields
    columns = ["entered", "observed", "side", "entry", "initial_stop", "protection", "peak_r", "current_r", "armed_at", "ended", "exit_price", "reason"]
    errors = []
    for at, fields in native.items():
        for index, key in enumerate(columns):
            if abs(float(fields[index]) - by_time[at][key]) > 1e-7:
                errors.append([at, key, fields[index], by_time[at][key]])
    checks = []
    names = [("1m", 10), ("1h", 10), ("15m", 10), ("capacity20_start", 20), ("4h", 20), ("1w", 20)]
    for suffix, count in names:
        path = directory / f"monday_range_v2_2_native_{suffix}.txt"
        match = re.search(r"TX_NATIVE_CHECKS_PASS count=(\d+).*timeSig=([\d,.-]+) priceSig=([\d,.-]+) exitSig=([\d,.-]+) sideSig=([\d,.-]+)", path.read_text())
        assert match, path
        records = result["trades"][-count:]
        expected = [
            sum(i * (t["entered"] + t["observed"]) / 60000 for i, t in enumerate(records, 1)),
            sum(i * (t["entry"] + 3 * t["initial_stop"]) / .01 for i, t in enumerate(records, 1)),
            sum(i * (t["ended"] / 60000 + (t["exit_price"] or 0) / .01 + t["reason"]) for i, t in enumerate(records, 1)),
            sum(i * t["side"] for i, t in enumerate(records, 1)),
        ]
        actual = [float(value.replace(",", "")) for value in match.groups()[1:]]
        assert all(abs(a - e) < .01 for a, e in zip(actual, expected)), path
        checks.append(path.name)
    assert not errors, errors
    return {"visible_native_rows_compared": len(native), "fields_per_row": len(columns), "mismatches": errors, "weighted_signature_matches": checks, "note": "Weighted signatures supplement visible row comparison; they are not full ledger byte equality."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-start", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native-dir", type=Path)
    args = parser.parse_args()
    minutes = load_minutes()
    result = replay(minutes, args.source_start)
    if args.native_dir:
        result["native_parity"] = compare_native(result, args.native_dir)
    result["inputs"] = INPUTS
    result["minute_rows"] = len(minutes)
    result["minute_gaps"] = int((minutes.index.to_series().diff().dropna() != pd.Timedelta(minutes=1)).sum())
    result["scope"] = "Engineering signal/price/path parity; gross R, no account orders or performance inference."
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("count", "source_start", "through", "minute_rows", "minute_gaps")}))


if __name__ == "__main__":
    main()
