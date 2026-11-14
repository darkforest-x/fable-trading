"""Single-position fixed-target screen on the frozen reclaimed-wick cohort.

Only the recorded signal and completed signal close are used for entry. High,
low, open and close of subsequent bars are passed to VectorBT's first-hit
kernel. For controls the volatility bucket uses the last 12 completed 5m bars.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import zlib

import numpy as np
import pandas as pd
import vectorbt as vbt

from yoyo.data.breadth_chart_sources import read_plain_csv_windows, read_streaming_csv_windows
from yoyo.evaluation.monday_reclaimed_wick_mfe import EXP as SOURCE, OCT_EXP, OHLC, ROOT
from yoyo.evaluation.monday_wick_r_engine import first_hit

OUT = ROOT / "experiments/active/exp-monday-reclaimed-wick-target-win-20261115-v1"
CFG = json.loads((OUT / "config.json").read_text())
STEP = 300_000
HORIZON = 864
TARGETS = CFG["gross_target_pct"]


def epoch_ms(series):
    return pd.to_datetime(series, utc=True).astype("datetime64[ns, UTC]").astype("int64") // 1_000_000


def source_windows(events, paths, last_open):
    intervals = []
    for t in sorted(events.open_ms):
        block = (t + STEP) // (72 * STEP) * (72 * STEP)
        left, right = block - 13 * STEP, min(last_open + STEP, block + (72 + HORIZON) * STEP)
        if intervals and left <= intervals[-1][1]:
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], right))
        else:
            intervals.append((left, right))
    parts = []
    for path in paths:
        reader = read_streaming_csv_windows if path.suffix == ".gz" else read_plain_csv_windows
        part = reader(path, intervals)
        if len(part):
            parts.append(part)
    bars = pd.concat(parts).drop_duplicates().sort_values("ts").set_index("ts")
    if bars.index.has_duplicates:
        # The historical October extension used pandas default CSV precision.
        for _, group in bars[bars.index.duplicated(keep=False)].groupby(level=0):
            if not np.allclose(group[OHLC], group[OHLC].iloc[0], rtol=2e-14, atol=0):
                raise ValueError("conflicting OHLC across sources")
        bars = bars[~bars.index.duplicated()]
    return bars[OHLC]


def control_index(event, times, i, buckets, known):
    block = (times[i] + STEP) // (72 * STEP)
    eligible = ((times + STEP) // (72 * STEP) == block) & (buckets == buckets[i])
    eligible &= np.abs(times - times[i]) >= 6 * STEP
    candidates = np.flatnonzero(eligible & ~known & np.isfinite(buckets))
    if len(candidates):
        rng = np.random.default_rng(zlib.crc32(event.event_id.encode()))
        return int(rng.choice(candidates))
    return None


def symbol_events(args):
    symbol, events, paths, last_open = args
    bars = source_windows(events, paths, last_open)
    times = bars.index.to_numpy(dtype=np.int64)
    contiguous = pd.Series(times).diff(11).eq(11 * STEP).to_numpy()
    ranges = (bars.high.rolling(12).max() - bars.low.rolling(12).min()) / bars.close
    buckets = np.floor(np.log2(ranges.where(ranges > 0) * 100)).where(contiguous).to_numpy()
    known = np.isin(times, events.open_ms)
    boundaries = np.flatnonzero(np.diff(times) != STEP)
    arrays = [np.ascontiguousarray(bars[name].to_numpy()[:, None]) for name in OHLC]
    n = len(bars)
    workspace = (np.empty((n, 1)), np.empty((n, 1), dtype=np.int64), np.empty(1, dtype=np.int64))
    rows = []
    for event in events.itertuples():
        i = int(bars.index.get_loc(event.open_ms))
        if abs(bars.close.iloc[i] - event.entry) > event.tick * 0.51:
            raise ValueError(f"Frozen entry mismatch: {event.event_id}")
        control = control_index(event, times, i, buckets, known)
        opportunities = [("signal", i, event.entry, event.stop)]
        if control is not None:
            ref = float(bars.close.iloc[control])
            opportunities.append(("random", control, ref, ref * (event.stop / event.entry)))
        for source, start, entry, stop in opportunities:
            j = int(np.searchsorted(boundaries, start))
            limit = int(boundaries[j]) if j < len(boundaries) else n - 1
            end = min(start + HORIZON, limit)
            if source == "signal" and end - start != event.end_i_source - event.entry_i_source:
                raise ValueError(f"Frozen clock mismatch: {event.event_id}")
            if end <= start:
                continue
            for target_pct in (TARGETS if source == "signal" else [0, *TARGETS]):
                target = np.nan
                if target_pct:
                    ticks = entry * (1 + event.side * target_pct / 100) / event.tick
                    outward = np.ceil(ticks - 1e-6) if event.side == 1 else np.floor(ticks + 1e-6)
                    target = outward * event.tick
                x, price, why, amb = first_hit(*arrays, start + 1, end, entry, stop,
                                               target, event.side, *workspace)
                if why == 0:
                    x, price = end, bars.close.iloc[end]
                rows.append((event.event_id, symbol, source, target_pct, event.side, entry, stop,
                             times[start] + STEP, times[x] + STEP, price, why, amb,
                             why != 0 or end - start == HORIZON))
    return rows


def account_fills(rows):
    cols = ["event_id", "symbol", "source", "target_pct", "side", "entry", "stop", "entry_ms",
            "exit_ms", "exit", "reason", "ambiguous", "complete"]
    out = pd.DataFrame(rows, columns=cols)
    fees = CFG["costs"]
    prices = np.vstack([out.entry, out.exit])
    size = np.vstack([out.side, -out.side])
    rates = np.vstack([np.full(len(out), fees["taker"]), np.where(out.reason == 4, fees["maker"], fees["taker"])])
    pf = vbt.Portfolio.from_orders(prices, size=size, price=prices, fees=rates, init_cash=prices.max(axis=0)*4,
                                   direction="both", cash_sharing=False, allow_partial=False, raise_reject=True)
    trades = pf.trades.records.sort_values("col")
    assert len(trades) == len(out) and (trades.status == 1).all()
    out["fee_pct"] = (trades.entry_fees.to_numpy() + trades.exit_fees.to_numpy()) / out.entry * 100
    out["slip_pct"] = fees["market_slip_bp"] / 100 * (1 + (out.reason != 4).astype(int))
    out["net_pct"] = trades.pnl.to_numpy() / out.entry * 100 - out.slip_pct
    out.loc[~out.complete, "net_pct"] = np.nan
    return out


def frozen_baseline(ledger):
    return pd.DataFrame(dict(event_id=ledger.event_id, symbol=ledger.symbol, source="signal", target_pct=0,
                             side=ledger.side, entry=ledger.entry, stop=ledger.stop, entry_ms=epoch_ms(ledger.entry_time),
                             exit_ms=epoch_ms(ledger.framework_exit_time), exit=ledger.exit_price,
                             reason=ledger.native_reason, ambiguous=0, complete=ledger.actual_outcome_complete,
                             net_pct=(ledger.native_net_after_slippage_bp / 100).where(ledger.actual_outcome_complete)))


def select_single(group):
    selected = []
    busy_until = -1
    for row in group.sort_values(["entry_ms", "symbol", "event_id"]).itertuples():
        if row.entry_ms > busy_until:
            selected.append(row.Index)
            busy_until = row.exit_ms
    return group.loc[selected]


def stats(group):
    complete = group[group.complete]
    ret = complete.net_pct.to_numpy()
    won = ret > 0
    dates = pd.DatetimeIndex(pd.to_datetime(complete.entry_ms, unit="ms", utc=True))
    weeks = dates.tz_convert("America/New_York").tz_localize(None).to_period("W-SUN")
    blocks = pd.DataFrame(dict(wins=won, n=1, net=ret)).groupby(weeks).sum().to_numpy()
    draws = np.random.default_rng(152026).integers(len(blocks), size=(5000, len(blocks)))
    counts = blocks[draws].sum(axis=1)
    win_ci = np.percentile(100 * counts[:,0] / counts[:,1], [2.5, 97.5])
    equity = np.r_[1., np.cumprod(1 + ret / 100)]
    return dict(n=len(ret), wins=won.sum(), win_pct=100*won.mean(), mean_pct=ret.mean(),
                win_lo=win_ci[0], win_hi=win_ci[1], mean_win_pct=ret[won].mean(), mean_loss_pct=ret[~won].mean(),
                profit_factor=ret[won].sum()/-ret[~won].sum(), max_closed_dd_pct=100*(1-equity/np.maximum.accumulate(equity)).max(),
                ambiguous=complete.ambiguous.sum(), censored=len(group)-len(ret), weekly_clusters=len(blocks))


def summarize(rows):
    windows = {"2025_selection": ("2025-01-01", "2025-12-29"), "2026_evaluation": ("2026-01-01", "2026-11-01"),
               "2025_plus": ("2025-01-01", "2026-11-01"), "before_2025": ("2019-01-01", "2024-12-29")}
    schedules, summary = [], []
    for name, (start, end) in windows.items():
        left, right = [pd.Timestamp(t, tz="UTC").value // 1_000_000 for t in (start, end)]
        window = rows[rows.entry_ms.between(left, right, inclusive="left")]
        for (source, target), group in window.groupby(["source","target_pct"]):
            single = select_single(group)
            schedules.append(single.assign(period=name))
            summary.append(dict(period=name, source=source, target_pct=target, **stats(single)))
    pd.concat(schedules).to_csv(OUT / "single_trades.csv.gz", index=False)
    metrics = pd.DataFrame(summary)
    metrics.to_csv(OUT / "metrics.csv", index=False)
    fitting = metrics[(metrics.period == "2025_selection") & (metrics.source == "signal") & (metrics.target_pct > 0)]
    passed = fitting[(fitting.win_pct >= 50) & (fitting.mean_pct > 0)].sort_values("mean_pct", ascending=False)
    (OUT / "selection.json").write_text(json.dumps(dict(selection_period="2025_selection",
           qualified_targets_pct=passed.target_pct.tolist(),
           chosen_target_pct=float(passed.target_pct.iloc[0]) if len(passed) else None,
           vectorbt=vbt.__version__, signal_events=rows[rows.source=="signal"].event_id.nunique(),
           matched_events=rows[rows.source=="random"].event_id.nunique()), indent=2)+"\n")


def main():
    ledger = pd.read_csv(SOURCE / "trades.csv.gz")
    assert ledger.event_id.is_unique and (ledger.score > .5).all()
    ledger["open_ms"] = epoch_ms(ledger.entry_open_time)
    stamps = json.loads((SOURCE / "input_manifest.json").read_text())["source_stamps"]
    coverage = pd.read_csv(SOURCE / "bar_coverage.csv").set_index("symbol")
    jobs = []
    for symbol, group in ledger.groupby("symbol"):
        paths = [Path(p["path"]) for p in stamps[symbol]["bar_paths"]]
        last = pd.Timestamp(coverage.loc[symbol, "last_open_time"])
        if last >= pd.Timestamp("2026-09-24", tz="UTC"):
            paths.append(OCT_EXP / "symbols" / symbol / "new_bars.csv.gz")
        jobs.append((symbol, group, paths, last.value // 1_000_000))
    rows = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for i, batch in enumerate(pool.map(symbol_events, jobs)):
            rows.extend(batch)
            if (i+1)%50 == 0:
                print(f"{i+1}/{len(jobs)} symbols", flush=True)
    fills = account_fills(rows)
    assert fills[fills.source == "signal"].groupby("target_pct").size().eq(len(ledger)).all()
    combined = pd.concat([fills, frozen_baseline(ledger)], ignore_index=True)
    combined.to_csv(OUT / "all_events.csv.gz", index=False)
    summarize(combined)


if __name__ == "__main__":
    main()
