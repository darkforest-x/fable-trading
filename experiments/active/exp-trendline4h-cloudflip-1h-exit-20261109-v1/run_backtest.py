"""Owner 2026-11-09 trendline/cloud longs on OKX 1H bars, executed by NautilusTrader once.

Arm A (main): trades.csv from build_trades.py (4H break + 4H cloud flip within
10 bars; matched cloud-flip-only controls). Arm B (variant 2): first 1H close
across each live 4H line while the 1H cloud is bullish; matched random
bullish-1H-cloud controls. Both arms: market entry at the signal close, resting
stop-market, market exit at the first 1H close with EMA20 < EMA50 (1H), taker
5bp / maker 2bp actual fills. Definitions frozen in config.json.

Run with /Users/zhangzc/.local/bin/nautilus-python from the repo root.
Causality: 1H EMAs use only closes up to each bar; the variant-2 line level is
known from its 4H birth close; entries/stops use bars up to the entry close.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from yoyo.layers.l3_backtest.nautilus_independent import ExitPolicy, run_native  # noqa: E402

CFG = json.loads((HERE / "config.json").read_text())
H1DIR = ROOT / "data/crypto/market/okx_swap_1h_windows_20261109"
H1 = 3600_000
WARM = 300
POLICY = ExitPolicy(opposite_hour_exit=True, hour_exit_from_entry=True, round_trip_cost=0.,
                    fill_maker_fee=CFG["costs"]["maker_fee"], fill_taker_fee=CFG["costs"]["taker_fee"])


def ema(c: np.ndarray, n: int) -> np.ndarray:
    out = np.empty_like(c)
    a = 2 / (n + 1)
    out[0] = c[0]
    for i in range(1, len(c)):
        out[i] = out[i - 1] + a * (c[i] - out[i - 1])
    return out


def load_1h(symbol: str):
    path = H1DIR / f"okx_{symbol}_1H.csv"
    raw = pd.read_csv(path, dtype=str)
    dec = max(len(v.split(".")[1]) if "." in v else 0 for col in ("open", "high", "low", "close") for v in raw[col])
    df = raw.astype({"ts": "int64", "open": float, "high": float, "low": float, "close": float})
    ts = df.ts.to_numpy(np.int64)
    brk = np.flatnonzero(np.diff(ts) != H1) + 1
    block = np.zeros(len(df), np.int64)
    block[brk] = 1
    block = np.cumsum(block)
    start = np.zeros(len(df), np.int64)
    hour = np.zeros(len(df))
    for b in np.unique(block):
        idx = np.flatnonzero(block == b)
        c = df.close.to_numpy()[idx]
        hour[idx] = np.where(ema(c, 20) > ema(c, 50), 1., -1.)
        start[idx] = idx[0]
    df["close_ms"] = ts + H1
    df["block"], df["block_start"], df["hour"] = block, start, hour
    return df, 10.0 ** -dec


def variant2(rng: np.random.Generator) -> tuple[pd.DataFrame, dict]:
    lines = pd.read_csv(HERE / "lines.csv")
    rows, stats = [], dict(lines=len(lines), crossed=0, cloud_bear_skip=0, no_cross=0, cold=0)
    for sym, g in lines.groupby("symbol"):
        df, _ = load_1h(sym)
        o, c, lo = df.ts.to_numpy(np.int64), df.close.to_numpy(), df.low.to_numpy()
        close_ms, hour, bstart, block = (df[k].to_numpy() for k in ("close_ms", "hour", "block_start", "block"))
        sigs = []
        for r in g.itertuples():
            lvl = r.b_y + (r.b_y - r.a_y) * (o - r.b_t) / (r.b_t - r.a_t)
            elig = np.flatnonzero((o >= r.born_close) & (close_ms <= r.end_close))
            hit = None
            for j in elig:
                if j == 0 or block[j - 1] != block[j]:
                    continue
                if c[j] > lvl[j] and c[j - 1] <= lvl[j - 1]:
                    hit = j
                    break
            if hit is None:
                stats["no_cross"] += 1
                continue
            stats["crossed"] += 1
            if hour[hit] < 0:
                stats["cloud_bear_skip"] += 1
                continue
            if hit - bstart[hit] < WARM:
                stats["cold"] += 1
                continue
            sigs.append(hit)
            rows.append(dict(kind="signal", symbol=sym, j=hit, group=f"{sym}:{r.born}", close_ms=int(close_ms[hit]),
                             entry=float(c[hit]), stop=float(lo[hit - 10:hit + 1].min())))
        taken = np.zeros(len(df), bool)
        for j in sigs:
            taken[max(0, j - 10):j + 11] = True
        for k, j in enumerate(sigs):
            cand = np.flatnonzero((np.abs(np.arange(len(df)) - j) <= 720) & (hour > 0) & ~taken
                                  & (np.arange(len(df)) - bstart >= WARM) & (block == block[j]))
            if len(cand):
                for b in rng.choice(cand, size=min(2, len(cand)), replace=False):
                    b = int(b)
                    taken[b] = True
                    rows.append(dict(kind="control", symbol=sym, j=b, group=f"{sym}:sig{k}", close_ms=int(close_ms[b]),
                                     entry=float(c[b]), stop=float(lo[b - 10:b + 1].min())))
    return pd.DataFrame(rows), stats


def execute(arm: str, trades: pd.DataFrame) -> pd.DataFrame:
    out, skipped = [], dict(no_bar=0, price_mismatch=0, zero_risk=0, cold=0)
    for sym, g in trades.groupby("symbol"):
        df, tick = load_1h(sym)
        pos = {int(t): i for i, t in enumerate(df.close_ms)}
        ents = []
        for r in g.itertuples():
            i = pos.get(int(r.close_ms))
            if i is None:
                skipped["no_bar"] += 1
                continue
            if abs(df.close.iat[i] - r.entry) > tick * 1e-3:
                skipped["price_mismatch"] += 1
                continue
            if i - df.block_start.iat[i] < WARM:
                skipped["cold"] += 1
                continue
            if i + 1 >= len(df) or df.block.iat[i + 1] != df.block.iat[i]:
                skipped["no_bar"] += 1  # entry on the last bar of its data block
                continue
            if r.entry - r.stop < tick * (1 - 1e-7):
                skipped["zero_risk"] += 1
                continue
            ents.append(dict(trade_id=r.trade_id, i=i, block=int(df.block.iat[i]), entry=float(df.close.iat[i]),
                             stop=float(r.stop)))
        for b, eg in pd.DataFrame(ents).groupby("block") if ents else []:
            idx = np.flatnonzero(df.block.to_numpy() == b)
            lo, hi = idx[0], idx[-1]
            frame = df.iloc[lo:hi + 1]
            frame = frame.set_index(pd.to_datetime(frame.close_ms, unit="ms", utc=True))[["open", "high", "low", "close"]]
            e = pd.DataFrame(dict(trade_id=eg.trade_id, entry_i=eg.i - lo, side=1, entry=eg.entry, stop=eg.stop,
                                  risk=eg.entry - eg.stop, end_i=hi - lo))
            rows, _, _ = run_native(frame, np.full(len(frame), np.nan), df.hour.to_numpy()[lo:hi + 1], e, POLICY,
                                    tick=tick, symbol="TKN", execution_minutes=60)
            rows["censored"] = rows.reason.eq(0)
            rows["hold_h"] = rows.exit_i - rows.entry_i
            out.append(rows)
    res = pd.concat(out, ignore_index=True)
    res["arm"] = arm
    print(arm, "skipped", skipped, flush=True)
    return res


def main() -> int:
    rng = np.random.default_rng(CFG["control"]["seed"])
    a = pd.read_csv(HERE / "trades.csv").rename(columns={"entry_close_ms": "close_ms"})
    b, stats = variant2(rng)
    b.insert(0, "trade_id", [f"v2{k[0]}{i:05d}" for i, k in enumerate(b.kind)])
    b.to_csv(HERE / "trades_v2.csv", index=False)
    print("variant2 build", stats, b.kind.value_counts().to_dict(), flush=True)
    res = pd.concat([execute("A_4h_break_cloudflip", a), execute("B_1h_cross_1h_cloud", b)], ignore_index=True)
    meta = pd.concat([a[["trade_id", "kind", "symbol", "group", "close_ms"]], b[["trade_id", "kind", "symbol", "group", "close_ms"]]])
    res = res.merge(meta, on="trade_id")
    res.to_csv(HERE / "results.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
