"""Serial, causal low-timeframe replay for the frozen SPIKE V8 admission rule.

This study deliberately does not alter V8.  It reuses the exact V6 raw-event
feed, V7 prior BB200 squeeze predicate, and V8 signed rope-distance <= 3 ATR
admission from the frozen research implementation.  Features consume only the
signal bar and earlier OHLCV.  Future bars are read solely by the unchanged
next-open/stop/trailing evaluation engine.

The daily-leaderboard cohort is intentionally causal: UTC day D is ranked only
after D is closed and its members may first enter on D+1.  A separate post-hoc
coverage table uses same-day final winners only to diagnose missed moves; it is
never included in the trading-return cohort.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import features
from yoyo.evaluation.spike_v6_wvf_study import ExecutionSpec, _data_gap
from yoyo.evaluation.spike_v6_wvf_study_post import matched_random_controls
from yoyo.evaluation.spike_v7_fast import simulate_v6_variant, v6_signals, v7_diagnostics


ROOT = Path(__file__).resolve().parents[2]
BINANCE_SERIES = ROOT / "data/kline_preholdout_binance_um5m/series"
BINANCE_INFO = ROOT / "data/kline_preholdout_binance_um5m/exchange_info.json"
BINANCE_ADMITTED = ROOT / "data/kline_preholdout_binance_um5m/admitted_symbols.json"
OKX_BTC_3M = ROOT / "data/kline_fetched/okx_BTC_USDT_SWAP_3m_57705.csv"
OKX_ETH_3M = ROOT / "data/kline_deep/okx_ETH_USDT_SWAP_3m_525599.csv"
CSV_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "open_time"]
ROUND_TRIP_COST = 0.002
V8_DISTANCE_ATR = 3.0
LEADERBOARD_START = pd.Timestamp("2026-03-01T00:00:00Z")
LEADERBOARD_END = pd.Timestamp("2026-05-01T00:00:00Z")
HOLDOUT_START = pd.Timestamp("2026-05-04T00:00:00Z")
CONFIG = ROOT / "experiments/active/exp-spike-v8-lowtf-20260913-v1/config.json"
PRIMARY_ARTIFACTS = (
    "primary_signals.csv.gz",
    "primary_closed_trades.csv.gz",
    "primary_matched_controls.csv.gz",
    "primary_matched_control_summary.csv",
)


@dataclass(frozen=True)
class Stream:
    name: str
    symbol: str
    venue: str
    minutes: int
    path: Path
    tick: float
    development_start: pd.Timestamp
    split: pd.Timestamp
    end: pd.Timestamp


def _utc(value: str | pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(value, tz="UTC") if not isinstance(value, pd.Timestamp) else value.tz_convert("UTC")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reuse_primary(source: Path, out: Path, config: dict[str, object]) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Reuse hash-pinned 3m/5m results without rereading holdout-era OHLCV."""
    receipt = config.get("reusable_primary")
    if not isinstance(receipt, dict):
        raise ValueError("config has no reusable_primary receipt")
    expected_path = (ROOT / str(receipt["path"])).resolve()
    if source.resolve() != expected_path:
        raise ValueError(f"reuse-primary must be the pinned source: {expected_path}")
    expected_hashes = receipt.get("sha256")
    if not isinstance(expected_hashes, dict):
        raise ValueError("reusable_primary has no sha256 map")
    for name in (*PRIMARY_ARTIFACTS, "summary.csv", "manifest.json"):
        path = source / name
        expected = expected_hashes.get(name)
        if not path.is_file() or not isinstance(expected, str) or _sha256(path) != expected:
            raise ValueError(f"reusable primary artifact mismatch: {path}")
    for name in PRIMARY_ARTIFACTS:
        shutil.copy2(source / name, out / name)
    summary = pd.read_csv(source / "summary.csv")
    primary = summary.loc[summary["stream"].notna()].copy()
    if len(primary) != 8 or set(primary.minutes.astype(int)) != {3, 5}:
        raise ValueError("pinned primary summary does not contain the expected eight fold rows")
    provenance = {
        "mode": "hash_pinned_reuse_without_ohlcv_read",
        "source": str(source),
        "formal_holdout_exposure_number": int(receipt["formal_holdout_exposure_number"]),
        "sha256": {name: _sha256(source / name) for name in (*PRIMARY_ARTIFACTS, "summary.csv", "manifest.json")},
    }
    return primary.to_dict("records"), provenance


def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    """Validate ordinary closed OHLCV bars and retain a UTC open-clock index."""
    time_name = "open_time" if "open_time" in raw else "time"
    if time_name not in raw:
        raise ValueError("source has no open-time clock")
    frame = raw.copy()
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop(time_name), utc=True))
    needed = ["open", "high", "low", "close", "volume"]
    if set(needed) - set(frame):
        raise ValueError("source lacks OHLCV")
    frame = frame.loc[:, needed].sort_index().astype(float)
    if frame.index.has_duplicates or not frame.index.is_monotonic_increasing:
        raise ValueError("source clock is not unique and sorted")
    return frame


def read_ohlcv(path: Path) -> pd.DataFrame:
    """Read a normal CSV source used for BTC/ETH serial studies."""
    return _normalise(pd.read_csv(path))


def read_binance_tail(path: Path, lines: int) -> pd.DataFrame:
    """Read only a late pre-holdout tail for causal daily ranking.

    Every Binance series is chronologically sorted.  Keeping a 70+ day tail
    provides the February warm-up plus the March/April cohort without scanning
    the 11 GB archive or manufacturing missing data.
    """
    completed = subprocess.run(["tail", "-n", str(lines), str(path)], check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    text = completed.stdout
    if not text.strip():
        return pd.DataFrame(columns=CSV_COLUMNS)
    first = text.splitlines()[0]
    raw = pd.read_csv(io.StringIO(text), header=0 if first.startswith("ts,") else None,
                      names=None if first.startswith("ts,") else CSV_COLUMNS)
    return _normalise(raw)


def binance_ticks() -> dict[str, float]:
    info = json.loads(BINANCE_INFO.read_text())
    ticks: dict[str, float] = {}
    for item in info["symbols"]:
        price = next((f["tickSize"] for f in item.get("filters", []) if f.get("filterType") == "PRICE_FILTER"), None)
        if price is not None and float(price) > 0:
            ticks[str(item["symbol"])] = float(price)
    return ticks


def v8_mask(frame: pd.DataFrame, minutes: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Return frozen V6 raw signals, V7 diagnostics, and exact V8 admissions."""
    frame = features(frame)
    frame.attrs["minutes"] = minutes
    gaps = _data_gap(frame, minutes)
    raw = v6_signals(frame, minutes)
    bb = v7_diagnostics(frame, data_gap=gaps)
    long = raw.long_signal.fillna(False).astype(bool)
    short = raw.short_signal.fillna(False).astype(bool)
    if (long & short).any():
        raise ValueError("ambiguous raw V6 side")
    side = pd.Series(np.where(long, 1, np.where(short, -1, 0)), index=frame.index)
    baseline = (long | short) & bb.v7_ready.fillna(False).astype(bool) & bb.prior_squeeze_run3.fillna(False).astype(bool)
    edge = pd.Series(np.where(side.eq(1), frame.ropeHigh, frame.ropeLow), index=frame.index)
    distance = side * (frame.close - edge) / frame.atr.where(frame.atr.gt(0))
    return raw, bb, (baseline & distance.le(V8_DISTANCE_ATR).fillna(False)).astype(bool)


def _fold_mask(index: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp, minutes: int) -> pd.Series:
    """Allow only confirmed signals whose close belongs to [start, end)."""
    confirmation = index + pd.Timedelta(minutes=minutes)
    return pd.Series((confirmation >= start) & (confirmation < end), index=index)


def _run(frame: pd.DataFrame, raw: pd.DataFrame, admission: pd.Series, *, tick: float,
         minutes: int, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the reference execution through an exclusive bar-end boundary."""
    visible = frame.loc[frame.index < end].copy()
    visible.attrs["minutes"] = minutes
    raw_visible = raw.reindex(visible.index)
    allowed = admission.reindex(visible.index, fill_value=False)
    return simulate_v6_variant(visible, raw_visible, admission=allowed, variant="v8",
                               data_gap=_data_gap(visible, minutes), spec=ExecutionSpec(tick=tick))


def _signal_rows(index: pd.DatetimeIndex, raw: pd.DataFrame, admission: pd.Series, *, symbol: str,
                 venue: str, minutes: int, arm: str) -> pd.DataFrame:
    side = np.where(raw.long_signal.fillna(False), 1, np.where(raw.short_signal.fillna(False), -1, 0))
    included = admission.fillna(False).to_numpy(bool)
    rows = pd.DataFrame({"signal_bar_open": index[included], "side": side[included]})
    rows["signal_confirm_time"] = rows.signal_bar_open + pd.Timedelta(minutes=minutes)
    rows["symbol"], rows["venue"], rows["minutes"], rows["arm"] = symbol, venue, minutes, arm
    return rows.loc[rows.side.ne(0)].reset_index(drop=True)


def _finished(trades: pd.DataFrame, *, symbol: str, venue: str, minutes: int, arm: str) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    out = trades.loc[~trades.censored.astype(bool)].copy()
    out["symbol"], out["venue"], out["minutes"], out["arm"] = symbol, venue, minutes, arm
    out["signal_confirm_time"] = pd.to_datetime(out.signal_bar_open, utc=True) + pd.Timedelta(minutes=minutes)
    return out.reset_index(drop=True)


def stream_account_stats(trades: pd.DataFrame) -> pd.DataFrame:
    """Return independent serial-account metrics; never merge concurrent coins."""
    columns = ["symbol", "venue", "minutes", "arm", "trades", "account_return", "max_drawdown"]
    if trades.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for keys, group in trades.groupby(["symbol", "venue", "minutes", "arm"], sort=True):
        returns = group.sort_values("entry_time").net_return.astype(float)
        equity = pd.concat([pd.Series([1.0]), (1.0 + returns).cumprod()], ignore_index=True)
        drawdown = float((1.0 - equity / equity.cummax()).max())
        rows.append(dict(zip(["symbol", "venue", "minutes", "arm"], keys), trades=int(len(group)),
                         account_return=float(equity.iloc[-1] - 1.0), max_drawdown=drawdown))
    return pd.DataFrame(rows)


def metrics(signals: pd.DataFrame, trades: pd.DataFrame, accounts: pd.DataFrame, *, label: str) -> dict[str, object]:
    """Event statistics plus independent-account summaries for one research arm."""
    gains = trades.net_return.clip(lower=0).sum() if len(trades) else 0.0
    losses = -trades.net_return.clip(upper=0).sum() if len(trades) else 0.0
    return {
        "label": label,
        "signals": int(len(signals)),
        "long_signals": int(signals.side.eq(1).sum()) if len(signals) else 0,
        "short_signals": int(signals.side.eq(-1).sum()) if len(signals) else 0,
        "closed_trades": int(len(trades)),
        "win_rate": float(trades.net_return.gt(0).mean()) if len(trades) else math.nan,
        "profit_factor": float(gains / losses) if losses else math.nan,
        "net_r_sum": float(trades.net_r.sum()) if len(trades) else 0.0,
        "net_r_mean": float(trades.net_r.mean()) if len(trades) else math.nan,
        "gross_return_sum": float(trades.gross_return.sum()) if len(trades) else 0.0,
        "net_return_sum": float(trades.net_return.sum()) if len(trades) else 0.0,
        "cost_return_sum": float(len(trades) * ROUND_TRIP_COST),
        "realized_10r": int(trades.net_r.ge(10).sum()) if len(trades) else 0,
        "realized_10r_rate": float(trades.net_r.ge(10).mean()) if len(trades) else math.nan,
        "mfe_10r": int(trades.mfe_r.ge(10).sum()) if len(trades) else 0,
        "mfe_10r_rate": float(trades.mfe_r.ge(10).mean()) if len(trades) else math.nan,
        "streams": int(len(accounts)),
        "mean_independent_account_return": float(accounts.account_return.mean()) if len(accounts) else math.nan,
        "median_independent_account_return": float(accounts.account_return.median()) if len(accounts) else math.nan,
        "mean_closed_trade_drawdown": float(accounts.max_drawdown.mean()) if len(accounts) else math.nan,
        "worst_closed_trade_drawdown": float(accounts.max_drawdown.max()) if len(accounts) else math.nan,
    }


def _causal_leaderboards(tail_lines: int) -> tuple[pd.DataFrame, dict[pd.Timestamp, set[str]], dict[pd.Timestamp, set[str]], dict[str, Path]]:
    """Build previous-day membership and same-day diagnostic top tens from closed bars."""
    admitted = {str(row["symbol"]) for row in json.loads(BINANCE_ADMITTED.read_text()) if row.get("status") == "TRADING"}
    rows: list[pd.DataFrame] = []
    paths: dict[str, Path] = {}
    earliest = LEADERBOARD_START - pd.Timedelta(days=2)
    for path in sorted(BINANCE_SERIES.glob("binance_um_*_5m_*.csv")):
        symbol = path.name.removeprefix("binance_um_").split("_5m_")[0]
        if symbol not in admitted:
            continue
        frame = read_binance_tail(path, tail_lines)
        frame = frame.loc[(frame.index >= earliest) & (frame.index < LEADERBOARD_END)]
        if frame.empty:
            continue
        day = frame.index.floor("D")
        daily = frame.assign(day=day).groupby("day", sort=True).agg(
            open=("open", "first"), close=("close", "last"), bars=("close", "size"))
        daily = daily.loc[daily.bars.eq(288)].copy()
        daily["return"] = daily.close / daily.open - 1.0
        daily["symbol"] = symbol
        rows.append(daily.reset_index())
        paths[symbol] = path
    daily_all = pd.concat(rows, ignore_index=True)
    rankable = daily_all.loc[(daily_all.day >= LEADERBOARD_START - pd.Timedelta(days=1)) &
                             (daily_all.day < LEADERBOARD_END)].copy()
    rankable["rank"] = rankable.groupby("day")["return"].rank(method="first", ascending=False)
    top = rankable.loc[rankable["rank"].le(10)].copy()
    prior_members: dict[pd.Timestamp, set[str]] = {}
    posthoc: dict[pd.Timestamp, set[str]] = {}
    for day, group in top.groupby("day"):
        posthoc[day] = set(group.symbol)
        next_day = day + pd.Timedelta(days=1)
        if LEADERBOARD_START <= next_day < LEADERBOARD_END:
            prior_members[next_day] = set(group.symbol)
    return top, prior_members, posthoc, paths


def _membership_mask(index: pd.DatetimeIndex, membership: dict[pd.Timestamp, set[str]], symbol: str,
                     minutes: int) -> pd.Series:
    """Admit D+1 members only after the first D+1 bar has closed.

    The D leaderboard is knowable at D+1 00:00 UTC.  A 23:55--00:00 signal
    cannot be ranked and filled at that same 00:00 open without execution
    latency.  Requiring confirmation strictly after midnight makes the first
    eligible 5m signal the 00:00--00:05 bar, whose next-open fill is causal.
    """
    confirmation = index + pd.Timedelta(minutes=minutes)
    confirmation_day = confirmation.floor("D")
    rank_available = confirmation > confirmation_day
    member = np.fromiter(
        (symbol in membership.get(day, set()) for day in confirmation_day),
        dtype=bool,
        count=len(confirmation_day),
    )
    return pd.Series(rank_available & member, index=index)


def posthoc_coverage(top: pd.DataFrame, signal_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Diagnose same-day Top10 recall without treating it as a tradable cohort."""
    rows = []
    for event in top.loc[(top.day >= LEADERBOARD_START) & (top.day < LEADERBOARD_END)].to_dict("records"):
        final_return = float(event["return"])
        symbol = str(event["symbol"])
        signal = signal_frames.get(symbol, pd.DataFrame())
        direction = 1 if final_return >= 0 else -1
        day_start, day_end = event["day"], event["day"] + pd.Timedelta(days=1)
        same = signal.loc[(signal.signal_confirm_time >= day_start) & (signal.signal_confirm_time < day_end) &
                          signal.side.eq(direction)].sort_values("signal_confirm_time")
        first = same.iloc[0] if len(same) else None
        cumulative = math.nan if first is None else float(first.signal_close / event["open"] - 1.0)
        remaining = math.nan if first is None else float(event["close"] / first.signal_close - 1.0)
        before_80 = bool(first is not None and abs(cumulative) <= 0.8 * abs(final_return))
        rows.append({"day": event["day"], "symbol": symbol, "rank": int(event["rank"]),
                     "day_return": final_return, "direction": direction,
                     "v8_same_direction_signal": first is not None,
                     "first_signal_confirm_time": pd.NaT if first is None else first.signal_confirm_time,
                     "cumulative_return_at_signal": cumulative, "remaining_return": remaining,
                     "signal_before_80pct_of_final_move": before_80})
    return pd.DataFrame(rows)


def _controls_for_stream(frame: pd.DataFrame, raw: pd.DataFrame, trades: pd.DataFrame, *, tick: float,
                         start: pd.Timestamp, end: pd.Timestamp, symbol: str, seeds: Iterable[int]) -> pd.DataFrame:
    """Run same-symbol/month/prior-volatility controls for one fixed validation arm."""
    if trades.empty:
        return pd.DataFrame()
    cache = {"bars": frame, "signals": raw, "data_gap": _data_gap(frame, int(frame.attrs["minutes"]))}
    matches, _ = matched_random_controls(cache, trades, tick=tick, fold_start=start, fold_end=end, seeds=seeds)
    matches["symbol"] = symbol
    return matches


def sign_flip_p(matches: pd.DataFrame, *, draws: int = 20000) -> dict[str, float]:
    """Average seed replicas, then sign-flip symbol×UTC-month matched clusters."""
    usable = matches.loc[matches.matched.astype(bool) & matches.net_r_difference.notna()].copy()
    if usable.empty:
        return {"clusters": 0, "delta_net_r": math.nan, "p_two_sided": math.nan, "match_rate": math.nan}
    usable["target_time"] = pd.to_datetime(usable.target_time, utc=True)
    event_mean = usable.groupby(["symbol", "target_time"], as_index=False).net_r_difference.mean()
    event_mean["utc_month"] = event_mean.target_time.dt.strftime("%Y-%m")
    by_cluster = event_mean.groupby(["symbol", "utc_month"]).net_r_difference.mean().to_numpy(float)
    observed = float(by_cluster.mean())
    rng = np.random.default_rng(20260913)
    signs = rng.choice(np.array([-1.0, 1.0]), size=(draws, len(by_cluster)))
    null = (signs * by_cluster).mean(axis=1)
    p = float((np.abs(null) >= abs(observed)).mean())
    return {"clusters": int(len(by_cluster)), "delta_net_r": observed, "p_two_sided": p,
            "match_rate": float(usable.shape[0] / matches.shape[0]) if len(matches) else math.nan}


def run_primary(stream: Stream) -> tuple[list[dict[str, object]], pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Evaluate one BTC/ETH stream in chronological development/validation folds."""
    source = read_ohlcv(stream.path)
    frame = features(source)
    frame.attrs["minutes"] = stream.minutes
    raw, _, admission = v8_mask(source, stream.minutes)
    all_signals, all_trades, accounts_rows, trade_rows, signal_rows = [], [], [], [], []
    folds = [("development", stream.development_start, stream.split), ("validation", stream.split, stream.end)]
    for fold, start, end in folds:
        allowed = admission & _fold_mask(frame.index, start, end, stream.minutes)
        ledger, trades = _run(frame, raw, allowed, tick=stream.tick, minutes=stream.minutes, end=end)
        signals = _signal_rows(frame.index, raw, allowed, symbol=stream.symbol, venue=stream.venue,
                               minutes=stream.minutes, arm="v8")
        done = _finished(trades, symbol=stream.symbol, venue=stream.venue, minutes=stream.minutes, arm="v8")
        accounts = stream_account_stats(done)
        all_signals.append(signals.assign(fold=fold))
        all_trades.append(done.assign(fold=fold))
        accounts_rows.append(accounts.assign(fold=fold))
        signal_rows.append(signals.assign(fold=fold)); trade_rows.append(done.assign(fold=fold))
    signal_table, trade_table, account_table = (pd.concat(all_signals, ignore_index=True),
                                                 pd.concat(all_trades, ignore_index=True),
                                                 pd.concat(accounts_rows, ignore_index=True))
    output = [metrics(signal_table.loc[signal_table.fold.eq(fold)], trade_table.loc[trade_table.fold.eq(fold)],
                      account_table.loc[account_table.fold.eq(fold)], label=f"{stream.name}:{fold}") | {"fold": fold,
                      "stream": stream.name, "minutes": stream.minutes, "venue": stream.venue}
              for fold, _, _ in folds]
    return output, signal_table, trade_table, account_table, frame


def run_leaderboard(tail_lines: int, out: Path) -> tuple[list[dict[str, object]], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run causal prior-day Top10 and a nontradable same-day coverage audit."""
    top, membership, posthoc, paths = _causal_leaderboards(tail_lines)
    ticks = binance_ticks()
    selected = set().union(*membership.values()) if membership else set()
    selected |= set().union(*posthoc.values()) if posthoc else set()
    all_signal_frames: dict[str, pd.DataFrame] = {}
    cohort_signals: list[pd.DataFrame] = []
    cohort_trades: list[pd.DataFrame] = []
    base_signals: list[pd.DataFrame] = []
    base_trades: list[pd.DataFrame] = []
    controls: list[pd.DataFrame] = []
    for ordinal, symbol in enumerate(sorted(selected), start=1):
        path, tick = paths.get(symbol), ticks.get(symbol)
        if path is None or tick is None:
            continue
        source = read_binance_tail(path, tail_lines)
        source = source.loc[source.index < LEADERBOARD_END]
        if len(source) < 900:
            continue
        frame = features(source)
        frame.attrs["minutes"] = 5
        raw, _, admission = v8_mask(source, 5)
        period = _fold_mask(frame.index, LEADERBOARD_START, LEADERBOARD_END, 5)
        member = _membership_mask(frame.index, membership, symbol, 5)
        signal_frame = _signal_rows(frame.index, raw, admission, symbol=symbol, venue="binance", minutes=5, arm="v8")
        signal_frame["signal_close"] = frame.close.reindex(pd.DatetimeIndex(signal_frame.signal_bar_open)).to_numpy(float)
        all_signal_frames[symbol] = signal_frame
        for arm, allowed, sig_sink, trade_sink in (("same_symbols_all_days", admission & period, base_signals, base_trades),
                                                    ("prior_day_top10", admission & period & member, cohort_signals, cohort_trades)):
            _, trade = _run(frame, raw, allowed, tick=tick, minutes=5, end=LEADERBOARD_END)
            signal = _signal_rows(frame.index, raw, allowed, symbol=symbol, venue="binance", minutes=5, arm=arm)
            done = _finished(trade, symbol=symbol, venue="binance", minutes=5, arm=arm)
            sig_sink.append(signal); trade_sink.append(done)
            if arm == "prior_day_top10" and len(done):
                control = _controls_for_stream(frame, raw, done, tick=tick, start=LEADERBOARD_START,
                                               end=LEADERBOARD_END, symbol=symbol, seeds=range(5))
                if len(control):
                    controls.append(control)
        if ordinal % 20 == 0:
            print(f"leaderboard processed {ordinal}/{len(selected)} symbols", flush=True)
    base_signal = pd.concat(base_signals, ignore_index=True) if base_signals else pd.DataFrame()
    base_trade = pd.concat(base_trades, ignore_index=True) if base_trades else pd.DataFrame()
    cohort_signal = pd.concat(cohort_signals, ignore_index=True) if cohort_signals else pd.DataFrame()
    cohort_trade = pd.concat(cohort_trades, ignore_index=True) if cohort_trades else pd.DataFrame()
    base_accounts, cohort_accounts = stream_account_stats(base_trade), stream_account_stats(cohort_trade)
    coverage = posthoc_coverage(top, all_signal_frames)
    out.mkdir(parents=True, exist_ok=True)
    top.to_csv(out / "daily_top10_posthoc_source.csv.gz", index=False, compression="gzip")
    coverage.to_csv(out / "posthoc_top10_coverage.csv", index=False)
    cohort_signal.to_csv(out / "leaderboard_prior_day_signals.csv.gz", index=False, compression="gzip")
    cohort_trade.to_csv(out / "leaderboard_prior_day_closed_trades.csv.gz", index=False, compression="gzip")
    base_trade.to_csv(out / "leaderboard_same_symbols_all_day_closed_trades.csv.gz", index=False, compression="gzip")
    matched = pd.concat(controls, ignore_index=True) if controls else pd.DataFrame()
    matched.to_csv(out / "leaderboard_matched_controls.csv.gz", index=False, compression="gzip")
    summary = [metrics(base_signal, base_trade, base_accounts, label="leaderboard:same_symbols_all_days") | {"fold": "preholdout"},
               metrics(cohort_signal, cohort_trade, cohort_accounts, label="leaderboard:prior_day_top10") | {"fold": "preholdout"}]
    control_summary = sign_flip_p(matched) if len(matched) else {"clusters": 0, "delta_net_r": math.nan, "p_two_sided": math.nan, "match_rate": math.nan}
    pd.DataFrame([control_summary]).to_csv(out / "leaderboard_matched_control_summary.csv", index=False)
    return summary, coverage, pd.DataFrame([control_summary]), top


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tail-lines", type=int, default=24000)
    parser.add_argument("--reuse-primary", type=Path,
                        help="Hash-pinned completed primary run; avoids another 3m holdout read")
    parser.add_argument("--allow-holdout", action="store_true",
                        help="Owner-authorized fresh 3m holdout read; forbidden with --reuse-primary")
    parser.add_argument("--holdout-exposure-number", type=int,
                        help="Required receipt number for a fresh owner-authorized holdout read")
    args = parser.parse_args()
    if args.tail_lines < 20000:
        raise ValueError("tail-lines must retain at least 70 days for ranking and V7 warm-up")
    if args.reuse_primary is not None and (args.allow_holdout or args.holdout_exposure_number is not None):
        raise ValueError("reuse-primary cannot be combined with fresh holdout authorization")
    if args.reuse_primary is None:
        if not args.allow_holdout or args.holdout_exposure_number is None:
            raise ValueError("fresh primary replay reads holdout-era 3m data; pass both --allow-holdout and --holdout-exposure-number after owner authorization")
        config = json.loads(CONFIG.read_text())
        expected_number = int(config["holdout_era"]["next_formal_exposure_number"])
        if args.holdout_exposure_number != expected_number:
            raise ValueError(f"next recorded formal holdout exposure number is {expected_number}")
    else:
        config = json.loads(CONFIG.read_text())
    out = args.out
    out.mkdir(parents=True, exist_ok=False)
    streams = [
        Stream("BTCUSDT_5m_binance", "BTCUSDT", "binance", 5,
               BINANCE_SERIES / "binance_um_BTCUSDT_5m_665856.csv", 0.1,
               _utc("2020-01-01"), _utc("2024-01-01"), _utc("2026-05-01")),
        Stream("ETHUSDT_5m_binance", "ETHUSDT", "binance", 5,
               BINANCE_SERIES / "binance_um_ETHUSDT_5m_665856.csv", 0.01,
               _utc("2020-01-01"), _utc("2024-01-01"), _utc("2026-05-01")),
        Stream("BTC_USDT_SWAP_3m_okx", "BTC-USDT-SWAP", "okx", 3, OKX_BTC_3M, 0.1,
               _utc("2026-03-13T12:15:00"), HOLDOUT_START, _utc("2026-07-11T17:30:00")),
        Stream("ETH_USDT_SWAP_3m_okx", "ETH-USDT-SWAP", "okx", 3, OKX_ETH_3M, 0.01,
               _utc("2023-07-31T11:12:00"), HOLDOUT_START, _utc("2026-07-30T11:09:00")),
    ]
    if args.reuse_primary is not None:
        summary_rows, primary_provenance = _reuse_primary(args.reuse_primary, out, config)
    else:
        summary_rows = []
        all_signals: list[pd.DataFrame] = []
        all_trades: list[pd.DataFrame] = []
        control_rows: list[pd.DataFrame] = []
        for stream in streams:
            print(f"running {stream.name}", flush=True)
            rows, signals, trades, _, frame = run_primary(stream)
            summary_rows.extend(rows)
            all_signals.append(signals); all_trades.append(trades)
            validation = trades.loc[trades.fold.eq("validation")]
            raw_source = read_ohlcv(stream.path)
            raw, _, _ = v8_mask(raw_source, stream.minutes)
            if len(validation):
                matches = _controls_for_stream(frame, raw, validation, tick=stream.tick, start=stream.split,
                                               end=stream.end, symbol=stream.symbol, seeds=range(5))
                if len(matches):
                    matches["stream"] = stream.name
                    control_rows.append(matches)
        signals = pd.concat(all_signals, ignore_index=True)
        trades = pd.concat(all_trades, ignore_index=True)
        signals.to_csv(out / "primary_signals.csv.gz", index=False, compression="gzip")
        trades.to_csv(out / "primary_closed_trades.csv.gz", index=False, compression="gzip")
        primary_controls = pd.concat(control_rows, ignore_index=True) if control_rows else pd.DataFrame()
        primary_controls.to_csv(out / "primary_matched_controls.csv.gz", index=False, compression="gzip")
        primary_control_rows = []
        if len(primary_controls):
            for stream, group in primary_controls.groupby("stream"):
                primary_control_rows.append({"stream": stream} | sign_flip_p(group))
        pd.DataFrame(primary_control_rows).to_csv(out / "primary_matched_control_summary.csv", index=False)
        primary_provenance = {
            "mode": "fresh_owner_authorized_holdout_read",
            "formal_holdout_exposure_number": int(args.holdout_exposure_number),
        }
    lb_rows, coverage, lb_control, top = run_leaderboard(args.tail_lines, out)
    summary_rows.extend(lb_rows)
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(out / "summary.csv", index=False)
    coverage_summary = pd.DataFrame([{
        "posthoc_top10_day_symbol_rows": int(len(coverage)),
        "same_direction_signal_recall": float(coverage.v8_same_direction_signal.mean()) if len(coverage) else math.nan,
        "early_before_80pct_recall": float(coverage.signal_before_80pct_of_final_move.mean()) if len(coverage) else math.nan,
        "median_remaining_return_after_first_signal": float(coverage.loc[coverage.v8_same_direction_signal, "remaining_return"].median()) if coverage.v8_same_direction_signal.any() else math.nan,
    }])
    coverage_summary.to_csv(out / "posthoc_top10_coverage_summary.csv", index=False)
    manifest = {
        "rule": "V6 raw + V7 prior_squeeze_run3 + signed rope distance <= 3 ATR",
        "round_trip_cost": ROUND_TRIP_COST,
        "leaderboard_membership": "UTC D final return Top10 may first enter D+1",
        "posthoc_diagnostic": "same-day final Top10 coverage only; not a trading cohort",
        "holdout_era_exposure": (
            f"formal primary source exposure #{primary_provenance['formal_holdout_exposure_number']}; "
            + ("this run reused pinned artifacts and did not reread 3m OHLCV"
               if primary_provenance["mode"] == "hash_pinned_reuse_without_ohlcv_read"
               else "this run performed the explicitly authorized 3m OHLCV read")
        ),
        "primary_provenance": primary_provenance,
        "streams": [stream.__dict__ | {"path": str(stream.path)} for stream in streams],
        "tail_lines": args.tail_lines,
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n")


if __name__ == "__main__":
    main()
