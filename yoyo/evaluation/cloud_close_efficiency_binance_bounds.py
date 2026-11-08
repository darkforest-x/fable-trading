"""Resolve the 35% drawdown boundary for the frozen Binance efficient-trend account.

Only the Binance ledger and native Binance bars are read. The hourly envelope
algorithm is reused from ``cloud_close_drawdown_bounds.hour_envelope``; its H1
quote callback is temporarily bound to the corrected Binance quote loader.
Candidate windows are derived from the complete H1 lower/upper series, then
only active contracts in those windows are read at 5m. Official daily UM 1m
archives are requested only if the complete 5m refinement still straddles 35%.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import subprocess
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_gap_repair as binance
from yoyo.evaluation import cloud_close_drawdown_bounds as dd
from yoyo.evaluation import cloud_close_exit_rotation as rotation
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation.monday_limit_reclaim_inputs import jobs as source_jobs
from yoyo.evaluation.signal_center_score import five_minute


ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-efficient-trend-20261109-v1"
CONFIG_PATH = EXP / "binance_refinement_config.json"
OUTPUT_PATH = EXP / "binance_refinement.json"
CACHE = ROOT / "data/research/cloud_close_efficiency_binance_dd"
HOUR = wallet.HOUR
FIVE_MIN = 300_000
ONE_MIN = 60_000
SPLIT = pd.Timestamp("2025-01-01T00:00:00Z")
INITIAL_CASH = 1000.0
LEG_FRACTIONS = {0.0: 1.0 / 3.0, 2.0: 2.0 / 3.0}
OHLC = ["open", "high", "low", "close"]
H1_EQUITY = EXP / "hourly_Binance.csv.gz"
FUNDED = EXP / "funded_Binance.csv.gz"
DECISIONS = EXP / "decisions_Binance.csv.gz"
INPUT_ARCHIVE_AUDIT = ROOT / "data/crypto/market/kline_preholdout_binance_um5m/archive_fetch_summary.json"


def _assert_committed() -> None:
    source_rel = str(Path(__file__).resolve().relative_to(ROOT))
    config_rel = str(CONFIG_PATH.resolve().relative_to(ROOT))
    for path in (source_rel, config_rel):
        subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    changed = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", source_rel, config_rel],
                             cwd=ROOT).returncode
    if changed:
        raise RuntimeError("Commit the Binance DD builder/config before producing the frozen refinement")


def _read_equity() -> pd.DataFrame:
    equity = pd.read_csv(H1_EQUITY, parse_dates=["hour_end_utc"])
    equity = equity.set_index("hour_end_utc").sort_index()
    equity.index = pd.DatetimeIndex(equity.index).tz_convert("UTC")
    required = {"nav", "open_nav", "open_exit_nav", "lower_nav", "upper_nav"}
    if (required - set(equity.columns) or equity.empty or not equity.index.is_unique
            or not equity.index.is_monotonic_increasing or not np.isfinite(equity[list(required)]).all().all()):
        raise ValueError("Frozen Binance hourly NAV file is incomplete or invalid")
    if not np.isclose(equity.index.to_series().diff().dropna().dt.total_seconds(), 3600.0).all():
        raise ValueError("Frozen Binance H1 equity timeline is not continuous")
    return equity


def _periods(equity: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {
        "full": equity,
        "development": equity.loc[equity.index <= SPLIT],
    }


def _upper_drawdown_series(equity: pd.DataFrame) -> np.ndarray:
    troughs = np.r_[INITIAL_CASH, equity.lower_nav.to_numpy(dtype=np.float64)]
    peaks = np.maximum.accumulate(np.r_[INITIAL_CASH, equity.upper_nav.to_numpy(dtype=np.float64)])
    return 100.0 * (1.0 - troughs / peaks)


def _max_drawdown_upper(equity: pd.DataFrame) -> float:
    return dd.max_drawdown(equity)


def _observed_drawdown_lower(equity: pd.DataFrame) -> float:
    points = np.r_[
        INITIAL_CASH,
        equity[["open_nav", "open_exit_nav", "nav"]].to_numpy(dtype=np.float64).ravel(),
    ]
    peaks = np.maximum.accumulate(points)
    return float(100.0 * (1.0 - np.min(points / peaks)))


def _entry_fee_by_hour(legs: pd.DataFrame) -> pd.Series:
    if legs.empty:
        return pd.Series(dtype=np.float64, index=pd.DatetimeIndex([], tz="UTC"))
    entry_clock = pd.to_datetime(legs.entry_ms.to_numpy(dtype=np.int64) + HOUR,
                                 unit="ms", utc=True)
    return (legs.signed_size.abs() * legs.entry_price * wallet.TAKER).groupby(entry_clock).sum()


def _fee_adjusted_envelope(equity: pd.DataFrame, legs: pd.DataFrame,
                           fine_hours: list[pd.Timestamp] | tuple[pd.Timestamp, ...] = ()) -> tuple[pd.DataFrame, pd.Series]:
    """Apply conservative fee bounds to every still-coarse H1 mark.

    A non-open exit fee can be paid at any point during its H1 bar, so subtract
    it from that coarse hour's lower NAV. At every entry hour, include the NAV
    just before paying the taker entry fee in the upper envelope. Refined hours
    are excluded from the lower-side subtraction because their native intrabar
    settlement path explicitly includes the relevant exit fee.
    """
    adjusted = equity.copy()
    late_exits = legs.loc[~legs.exit_phase.eq("open")]
    if not late_exits.empty:
        exit_clock = pd.to_datetime(late_exits.exit_bar_ms.to_numpy(dtype=np.int64) + HOUR,
                                    unit="ms", utc=True)
        exit_fee = (late_exits.signed_size.abs() * late_exits.exit_price
                    * late_exits.exit_fee_rate).groupby(exit_clock).sum()
        fine_index = pd.DatetimeIndex(fine_hours).tz_convert("UTC") if len(fine_hours) else pd.DatetimeIndex([], tz="UTC")
        coarse_index = adjusted.index.difference(fine_index)
        adjusted.loc[coarse_index, "lower_nav"] -= exit_fee.reindex(coarse_index, fill_value=0.0)
    entry_fee = _entry_fee_by_hour(legs)
    pre_entry_nav = adjusted.open_nav + entry_fee.reindex(adjusted.index, fill_value=0.0)
    adjusted["upper_nav"] = np.maximum(adjusted.upper_nav, pre_entry_nav)
    return adjusted, pre_entry_nav


def critical_windows(equity: pd.DataFrame, threshold_pct: float = 35.0) -> list[dict]:
    """List every H1 peak/trough pair whose conservative bound exceeds threshold."""
    dd_upper = _upper_drawdown_series(equity)
    upper = np.r_[INITIAL_CASH, equity.upper_nav.to_numpy(dtype=np.float64)]
    rows = []
    for trough_i in np.flatnonzero(dd_upper[1:] > threshold_pct) + 1:
        peak_i = int(np.argmax(upper[:trough_i + 1]))
        peak_end = None if peak_i == 0 else equity.index[peak_i - 1]
        trough_end = equity.index[trough_i - 1]
        rows.append({
            "peak_hour_end_utc": peak_end.isoformat() if peak_end is not None else None,
            "trough_hour_end_utc": trough_end.isoformat(),
            "h1_upper_dd_at_trough_pct": float(dd_upper[trough_i]),
            "h1_trough_lower_usdt": float(equity.lower_nav.iloc[trough_i - 1]),
            "h1_prior_upper_peak_usdt": float(upper[peak_i]),
        })
    return rows


def _active_symbols(legs: pd.DataFrame, hour_open_ms: int) -> list[str]:
    active = legs.loc[
        (legs.entry_ms <= hour_open_ms) & (legs.exit_bar_ms >= hour_open_ms)
    ]
    active = active.loc[~(active.exit_bar_ms.eq(hour_open_ms) & active.exit_phase.eq("open"))]
    return sorted(active.symbol.astype(str).unique())


def _accounting_legs(config: dict) -> tuple[pd.DataFrame, dict]:
    funded = pd.read_csv(FUNDED, dtype={"event_id": str, "parent_id": str, "symbol": str})
    decisions = pd.read_csv(DECISIONS, dtype={"event_id": str, "parent_id": str, "symbol": str})
    if funded.event_id.duplicated().any() or decisions.event_id.duplicated().any():
        raise ValueError("Binance funded ledger or frozen decision file has duplicate events")
    if not decisions.choice.isin(["partial_2_3", "full_runner"]).all():
        raise ValueError("Binance frozen decisions contain an unknown exit arm")
    funded_ids = set(funded.event_id.astype(str))
    funded_decisions = decisions.loc[
        ~decisions.is_control.astype(bool) & decisions.event_id.astype(str).isin(funded_ids)
    ].copy()
    if set(funded_decisions.event_id.astype(str)) != funded_ids:
        raise ValueError("Every funded Binance event must have exactly one frozen decision")

    paths = binance.assembled(set(binance.patch.affected_tickers()))
    paths = paths.loc[paths.event_id.astype(str).isin(funded_ids)].copy()
    if (paths.is_control.astype(bool).any() or paths.duplicated(["event_id", "tp_r"]).any()
            or not paths.groupby("event_id").size().eq(2).all()):
        raise ValueError("Funded Binance event paths are not one native runner and one 2R leg each")
    if set(paths.event_id.astype(str)) != funded_ids:
        raise ValueError("Corrected Binance native path coverage differs from funded IDs")

    selected = rotation._rotated_paths(paths, funded_decisions)
    legs = selected.merge(funded[["event_id", "parent_id", "symbol", "side", "entry_ms",
                                  "exit_bar_ms", "cash_pnl", "notional"]],
                          on="event_id", how="left", suffixes=("", "_funded"),
                          validate="many_to_one")
    if legs["cash_pnl"].isna().any() or len(legs) != 2 * len(funded):
        raise ValueError("Selected exit legs failed to join the funded Binance cash ledger")
    maker_exit = legs.exit_fee_rate.to_numpy(dtype=float) < wallet.TAKER
    target_exit = legs.exit_reason.astype(str).str.startswith("take_profit").to_numpy(dtype=bool)
    if not np.array_equal(maker_exit, target_exit):
        raise ValueError("Selected Binance exit fee class differs from its native trigger type")
    for column in ("symbol", "parent_id", "entry_ms", "side"):
        right = f"{column}_funded"
        if right in legs and not legs[column].astype(str).equals(legs[right].astype(str)):
            raise ValueError(f"Funded and native Binance legs differ in {column}")
    if not legs.groupby("event_id").exit_bar_ms.max().sort_index().equals(
            funded.set_index("event_id").exit_bar_ms.sort_index()):
        raise ValueError("Native runner exit clock differs from the funded Binance position ledger")
    selected = funded_decisions.set_index("event_id").choice
    full_ids = set(selected.index[selected.eq("full_runner")].astype(str))
    runner = legs.loc[legs.tp_r.eq(0)].set_index("event_id")
    target = legs.loc[legs.tp_r.eq(2)].set_index("event_id")
    if not full_ids.issubset(set(runner.index)):
        raise ValueError("Full-runner selection has no runner path")
    exact_runner_fields = ["exit_bar_ms", "exit_price", "exit_phase", "exit_fee_rate", "exit_reason"]
    for column in exact_runner_fields:
        if not runner.loc[list(full_ids), column].equals(target.loc[list(full_ids), column]):
            raise ValueError(f"Full-runner replacement changed native {column}")

    legs["fraction"] = legs.tp_r.map(LEG_FRACTIONS)
    if legs.fraction.isna().any():
        raise ValueError("Only the native 2R and runner weights may be funded")
    legs["signed_size"] = (legs.notional / legs.entry_price * legs.fraction * legs.side)
    legs["reconstructed_cash_pnl"] = (
        legs.side * (legs.exit_price - legs.entry_price) * legs.signed_size.abs()
        - legs.entry_price * legs.signed_size.abs() * wallet.TAKER
        - legs.exit_price * legs.signed_size.abs() * legs.exit_fee_rate
    )
    reconstructed = legs.groupby("event_id").reconstructed_cash_pnl.sum().sort_index()
    reference_pnl = funded.set_index("event_id").cash_pnl.sort_index()
    errors = (reconstructed - reference_pnl).abs()
    tolerance = float(config["accounting"]["cash_reconciliation_tolerance_usdt"])
    if errors.max() > tolerance:
        raise ValueError(f"Native Binance exit legs do not reconcile to funded cash (max {errors.max():.9g})")
    if not np.isclose(INITIAL_CASH + funded.cash_pnl.sum(),
                      pd.read_csv(H1_EQUITY).nav.iloc[-1], rtol=1e-9, atol=tolerance):
        raise ValueError("Funded Binance net PnL does not reconcile to terminal H1 NAV")
    audit = {
        "funded_positions": int(len(funded)),
        "native_exit_legs": int(len(legs)),
        "selected_full_runner_positions": int(len(full_ids)),
        "selected_partial_positions": int(len(funded) - len(full_ids)),
        "cash_start_plus_pnl_usdt": float(INITIAL_CASH + funded.cash_pnl.sum()),
        "h1_terminal_nav_usdt": float(pd.read_csv(H1_EQUITY).nav.iloc[-1]),
        "max_per_event_cash_reconciliation_error_usdt": float(errors.max()),
        "exit_path_fee_classes_preserved": True,
        "original_entry_order_prices_and_quantities_preserved": True,
    }
    return legs, audit


def _expected_bar_times(hour_open_ms: int, step_ms: int) -> np.ndarray:
    return hour_open_ms + np.arange(HOUR // step_ms, dtype=np.int64) * step_ms


def _bar_rows_for_hours(config: dict, legs: pd.DataFrame,
                        hour_ends: list[pd.Timestamp], step_ms: int) -> tuple[pd.DataFrame, dict]:
    active_by_hour = {}
    for end in hour_ends:
        hour_open_ms = int(end.value // 1_000_000 - HOUR)
        active_by_hour[hour_open_ms] = _active_symbols(legs, hour_open_ms)
    needed = {}
    for hour_ms, symbols in active_by_hour.items():
        for symbol in symbols:
            needed.setdefault(symbol, []).append(hour_ms)
    if not needed:
        raise ValueError("No Binance positions are active in the candidate peak/trough windows")

    cache_name = "5m_quotes.csv.gz" if step_ms == FIVE_MIN else "1m_quotes.csv.gz"
    cache_path = CACHE / cache_name
    wanted_keys = {(symbol, hour_ms) for symbol, stamps in needed.items() for hour_ms in stamps}
    if cache_path.is_file():
        cached = pd.read_csv(cache_path, dtype={"symbol": str})
        if not cached.duplicated(["symbol", "time"]).any():
            present = set(zip(cached.symbol.astype(str),
                              (cached.time.astype(np.int64) // HOUR) * HOUR))
            if wanted_keys.issubset(present):
                audit_path = CACHE / ("5m_source_audit.json" if step_ms == FIVE_MIN else "1m_source_audit.json")
                audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.is_file() else {}
                audit.update(cache=str(cache_path.relative_to(ROOT)), reused=True)
                return cached.loc[
                    [((str(s), int((int(t) // HOUR) * HOUR)) in wanted_keys)
                     for s, t in zip(cached.symbol, cached.time)]
                ].copy(), audit

    CACHE.mkdir(parents=True, exist_ok=True)
    if step_ms == FIVE_MIN and not config["resolution"].get("official_daily_primary", False):
        jobs_by_symbol = {job["symbol"].removesuffix("USDT"): job
                          for job in source_jobs() if job["group"] == "crypto_5m"}
        source_manifest = json.loads(INPUT_ARCHIVE_AUDIT.read_text(encoding="utf-8"))
        if source_manifest.get("provider") != "Binance official data.binance.vision USD-M monthly klines":
            raise ValueError("Expected the frozen Binance official monthly 5m source manifest")
        source_name = "monday_limit_reclaim_inputs.jobs -> signal_center_score.five_minute"
        records = []
        source_records = []
        for symbol, hours in sorted(needed.items()):
            job = jobs_by_symbol.get(symbol)
            if job is None:
                raise ValueError(f"No Binance-native 5m archive job for {symbol}")
            bars = five_minute(job)
            if bars.index.has_duplicates or not bars.index.is_monotonic_increasing:
                raise ValueError(f"{symbol}: source 5m clock is duplicated or unordered")
            clock = bars.index.as_unit("ms").asi8
            for hour_ms in sorted(set(hours)):
                expected = _expected_bar_times(hour_ms, FIVE_MIN)
                left = int(np.searchsorted(clock, hour_ms))
                right = int(np.searchsorted(clock, hour_ms + HOUR))
                times = clock[left:right]
                if not np.array_equal(times, expected):
                    fine, fallback_audit = _official_daily_bars(symbol, hour_ms, "5m", FIVE_MIN)
                    if not np.array_equal(fine.time.to_numpy(dtype=np.int64), expected):
                        raise ValueError(f"{symbol}: incomplete local and official 5m bars in H1 window {hour_ms}")
                    bar_times = fine.time.to_numpy(dtype=np.int64)
                    bar_values = fine[OHLC].to_numpy(dtype=float)
                    source_records.append({"symbol": symbol, "fallback_hour_open_ms": int(hour_ms),
                                           "official_daily_archive": fallback_audit})
                else:
                    bar_times = times
                    bar_values = bars.iloc[left:right][OHLC].to_numpy(dtype=float)
                for timestamp, values in zip(bar_times, bar_values):
                    records.append(dict(symbol=symbol, time=int(timestamp),
                                        **dict(zip(OHLC, map(float, values)))))
            source_records.append({
                "symbol": symbol,
                "paths": [{"path": p, "exists": Path(p).is_file(),
                           "size_bytes": Path(p).stat().st_size if Path(p).is_file() else None}
                          for p in job["paths"]],
                "requested_hour_windows": sorted(set(map(int, hours))),
                "source_rows": int(len(bars)),
                "source_first_utc": str(bars.index[0]) if len(bars) else None,
                "source_last_utc": str(bars.index[-1]) if len(bars) else None,
            })
            del bars
            gc.collect()
        output = pd.DataFrame(records, columns=["symbol", "time", *OHLC])
        audit = {"cache": str(cache_path.relative_to(ROOT)), "reused": False,
                 "resolution": "5m", "source": source_name,
                 "official_archive_provider": source_manifest["provider"],
                 "contracts": source_records, "rows": int(len(output))}
    elif step_ms in (ONE_MIN, FIVE_MIN):
        output, audit = _official_rows(needed, cache_path, step_ms)
    else:
        raise ValueError(f"Unsupported subhour bar size: {step_ms}")

    if output.duplicated(["symbol", "time"]).any():
        raise ValueError("Cached native Binance subhour rows have duplicate keys")
    output = output.sort_values(["symbol", "time"]).reset_index(drop=True)
    temp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    output.to_csv(temp, index=False, compression="gzip")
    temp.replace(cache_path)
    audit["rows"] = int(len(output))
    audit["cache_size_bytes"] = int(cache_path.stat().st_size)
    audit_path = CACHE / ("5m_source_audit.json" if step_ms == FIVE_MIN else "1m_source_audit.json")
    audit_path.write_text(json.dumps(audit, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return output, audit


def _download_official(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "cloud-close-binance-dd/1.0"})
    temp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as output:
        output.write(response.read())
    temp.replace(dest)


def _official_daily_bars(symbol: str, hour_ms: int, interval: str,
                         step_ms: int) -> tuple[pd.DataFrame, dict]:
    day = pd.to_datetime(hour_ms, unit="ms", utc=True).strftime("%Y-%m-%d")
    name = f"{symbol}USDT-{interval}-{day}.zip"
    url = ("https://data.binance.vision/data/futures/um/daily/klines/"
           f"{symbol}USDT/{interval}/{name}")
    path = CACHE / f"official_{interval}" / f"{symbol}USDT" / name
    if not path.is_file():
        _download_official(url, path)
    check_path = path.with_suffix(path.suffix + ".CHECKSUM")
    if not check_path.is_file():
        _download_official(url + ".CHECKSUM", check_path)
    expected_hash = check_path.read_text(encoding="utf-8").strip().split()[0].lower()
    actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(f"Official Binance archive checksum mismatch: {path}")
    with zipfile.ZipFile(path) as archive:
        members = [member for member in archive.namelist() if member.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"Unexpected official daily archive contents: {path}")
        with archive.open(members[0]) as handle:
            frame = pd.read_csv(handle, header=None, usecols=[0, 1, 2, 3, 4],
                                names=["time", *OHLC])
    if str(frame.time.iloc[0]).lower() in {"open_time", "open time"}:
        frame = frame.iloc[1:].copy()
    frame["time"] = pd.to_numeric(frame.time, errors="raise").astype("int64")
    frame[OHLC] = frame[OHLC].astype(float)
    frame = frame.loc[(frame.time >= hour_ms) & (frame.time < hour_ms + HOUR)].copy()
    expected = _expected_bar_times(hour_ms, step_ms)
    if not np.array_equal(frame.time.to_numpy(dtype=np.int64), expected):
        raise ValueError(f"Official Binance {interval} archive lacks a full hour: {symbol} {day}")
    return frame.reset_index(drop=True), {
        "symbol": symbol, "date_utc": day, "interval": interval,
        "archive_url": url, "archive_path": str(path.relative_to(ROOT)),
        "sha256": actual_hash, "hour_open_ms": int(hour_ms), "rows": int(len(frame)),
    }


def _official_rows(needed: dict[str, list[int]], cache_path: Path, step_ms: int) -> tuple[pd.DataFrame, dict]:
    """Use Binance USD-M daily archives for the selected contracts and hours."""
    interval = "1m" if step_ms == ONE_MIN else "5m"
    records, archive_records = [], []
    for symbol, hour_stamps in sorted(needed.items()):
        for hour_ms in sorted(set(hour_stamps)):
            frame, audit = _official_daily_bars(symbol, hour_ms, interval, step_ms)
            records.extend(frame.assign(symbol=symbol).to_dict("records"))
            archive_records.append(audit)
    return pd.DataFrame(records, columns=["time", *OHLC, "symbol"])[["symbol", "time", *OHLC]], {
        "resolution": interval, "source": "Binance official USD-M daily klines",
        "contracts_and_archives": archive_records, "rows": int(len(records)),
        "cache": str(cache_path.relative_to(ROOT)), "reused": False,
    }


@contextmanager
def _binance_native_h1_quotes():
    """Bind dd.hour_envelope to the corrected Binance H1 source for this call only."""
    old_quotes = wallet.quotes
    wallet.quotes = binance.quotes
    try:
        yield
    finally:
        wallet.quotes = old_quotes


def _refine_hours(equity: pd.DataFrame, legs: pd.DataFrame, hours: list[pd.Timestamp],
                  step_ms: int, subbars: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    refined = equity.copy()
    rows = []
    entry_fee = _entry_fee_by_hour(legs)
    with _binance_native_h1_quotes():
        for hour_end in sorted(set(hours)):
            stamp = hour_end - pd.Timedelta(hours=1)
            native = binance.quotes.cache_info() if hasattr(binance.quotes, "cache_info") else None
            rec = dd.hour_envelope(stamp, refined.loc[hour_end], legs, subbars, step_ms)
            active = rec["active_symbols"].split("|") if rec["active_symbols"] else []
            observed = rec["observed_symbols"].split("|") if rec["observed_symbols"] else []
            if active != observed:
                raise ValueError(f"Incomplete Binance {step_ms}ms coverage at {hour_end}: {active} vs {observed}")
            pre_entry_fee_upper = (float(refined.loc[hour_end, "open_nav"])
                                   + float(entry_fee.get(hour_end, 0.0)))
            final_upper = max(float(rec["refined_upper"]), pre_entry_fee_upper)
            refined.loc[hour_end, ["lower_nav", "upper_nav"]] = [
                rec["refined_lower"], final_upper,
            ]
            rows.append({"hour_end_utc": hour_end.isoformat(), "step_ms": step_ms,
                         "active_symbols": active, "observed_symbols": observed,
                         "refined_lower_usdt": float(rec["refined_lower"]),
                         "refined_upper_usdt": float(rec["refined_upper"]),
                         "previous_lower_usdt": float(rec["previous_lower"]),
                         "previous_upper_usdt": float(rec["previous_upper"]),
                         "min_observed_subbar_close_nav_usdt": rec["min_observed_close"],
                         "max_observed_subbar_close_nav_usdt": rec["max_observed_close"],
                         "pre_entry_fee_upper_bound_usdt": pre_entry_fee_upper,
                         "final_upper_nav_usdt": final_upper,
                         "h1_nav_self_check": float(refined.loc[hour_end, "nav"]),
                         "native_quote_cache_info": str(native) if native is not None else None})
    return refined, rows


def _period_bounds(equity: pd.DataFrame, baseline_h1: pd.DataFrame,
                   fee_h1: pd.DataFrame | None = None) -> dict:
    lower = _observed_drawdown_lower(equity)
    upper = _max_drawdown_upper(equity)
    baseline_upper = _max_drawdown_upper(baseline_h1)
    fee_upper = _max_drawdown_upper(fee_h1 if fee_h1 is not None else baseline_h1)
    if lower > upper + 1e-7:
        raise ValueError("Observed Binance drawdown lower bound exceeds the refined conservative upper bound")
    return {
        "observed_lower_pct": lower,
        "refined_upper_pct": upper,
        "original_h1_upper_pct": baseline_upper,
        "fee_adjusted_h1_upper_pct": fee_upper,
        "interval_straddles_35pct": bool(lower <= 35.0 <= upper),
        "passes_35pct_upper_bound": bool(upper <= 35.0),
        "fails_35pct_lower_bound": bool(lower > 35.0),
    }


def run(config_path: Path = CONFIG_PATH) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["venue"] != "Binance" or config["drawdown_threshold_pct"] != 35.0:
        raise ValueError("This builder is frozen to the Binance 35% DD review")
    if OUTPUT_PATH.exists():
        raise FileExistsError(f"Refinement output already exists: {OUTPUT_PATH}")
    _assert_committed()
    equity = _read_equity()
    baseline_metrics = pd.read_csv(EXP / "account_metrics.csv")
    baseline_rows = baseline_metrics.loc[
        baseline_metrics.venue.eq("Binance")
        & baseline_metrics.policy.eq("trend_efficiency")
        & baseline_metrics.scenario.eq("actual")
        & baseline_metrics.period.isin(["all", "dev"])
    ].set_index("period")
    if set(baseline_rows.index) != {"all", "dev"}:
        raise ValueError("Frozen Binance efficient-trend full/development metrics are missing")
    if not np.isclose(float(equity.nav.iloc[-1]), float(baseline_rows.loc["all", "final_usdt"]), atol=1e-6):
        raise ValueError("Binance H1 NAV terminal value differs from its frozen account metrics")

    period_curves = _periods(equity)
    for name, curve in period_curves.items():
        metric_period = "all" if name == "full" else "dev"
        if not np.isclose(curve.nav.iloc[-1], baseline_rows.loc[metric_period, "final_usdt"],
                          rtol=1e-10, atol=1e-6):
            raise ValueError(f"Binance {name} H1 terminal NAV differs from frozen account metrics")
        if not np.isclose(_observed_drawdown_lower(curve),
                          baseline_rows.loc[metric_period, "dd_mark_pct"], rtol=1e-9, atol=1e-6):
            raise ValueError(f"Binance {name} observed H1 marks differ from frozen drawdown metrics")
        if not np.isclose(_max_drawdown_upper(curve),
                          baseline_rows.loc[metric_period, "dd_bound_pct"], rtol=1e-9, atol=1e-6):
            raise ValueError(f"Binance {name} H1 upper envelope differs from frozen drawdown metrics")

    # Reconstruct the unchanged filled legs before window selection so the coarse
    # H1 envelope includes every possible fee-bound crossing of the 35% gate.
    legs, accounting_audit = _accounting_legs(config)
    candidates_raw = {name: critical_windows(curve, 35.0) for name, curve in period_curves.items()}
    candidate_scan = {name: _fee_adjusted_envelope(curve, legs)[0]
                      for name, curve in period_curves.items()}
    candidates = {name: critical_windows(curve, 35.0) for name, curve in candidate_scan.items()}
    for name, expected in config["expected_h1_critical_windows"].items():
        observed = candidates_raw[name]
        expected_keys = {(item["peak_hour_end_utc"], item["trough_hour_end_utc"]) for item in expected}
        observed_keys = {(item["peak_hour_end_utc"], item["trough_hour_end_utc"]) for item in observed}
        if expected_keys != observed_keys:
            raise ValueError(f"{name} H1 DD candidate windows changed: {observed_keys}")
    for name, expected in config["expected_fee_adjusted_h1_critical_windows"].items():
        observed = candidates[name]
        expected_keys = {(item["peak_hour_end_utc"], item["trough_hour_end_utc"]) for item in expected}
        observed_keys = {(item["peak_hour_end_utc"], item["trough_hour_end_utc"]) for item in observed}
        if expected_keys != observed_keys:
            raise ValueError(f"{name} fee-adjusted H1 DD candidate windows changed: {observed_keys}")
    unique_ends = sorted({pd.Timestamp(item[key]) for rows in candidates.values() for item in rows
                          for key in ("peak_hour_end_utc", "trough_hour_end_utc")
                          if item[key] is not None})
    if not unique_ends:
        raise ValueError("H1 drawdown did not contain any candidate windows above 35%")

    # Fine bars replace the coarse exit-fee subtraction within their own hours;
    # the entry-fee upper mark remains in place and is retained by _refine_hours.
    refinement_base_full = _fee_adjusted_envelope(equity, legs, fine_hours=unique_ends)[0]
    bars_5m, source_audit_5m = _bar_rows_for_hours(config, legs, unique_ends, FIVE_MIN)
    equity_5m, rows_5m = _refine_hours(
        refinement_base_full, legs, unique_ends, FIVE_MIN, bars_5m,
    )
    result_by_period_5m = {
        name: _period_bounds(equity_5m.loc[curve.index], curve,
                             fee_h1=candidate_scan[name])
        for name, curve in period_curves.items()
    }
    unresolved_after_5m = any(v["interval_straddles_35pct"] for v in result_by_period_5m.values())
    resolution = "5m"
    final_equity = equity_5m
    final_rows = rows_5m
    source_audit_1m = None
    result_by_period = result_by_period_5m

    if unresolved_after_5m:
        bars_1m, source_audit_1m = _bar_rows_for_hours(config, legs, unique_ends, ONE_MIN)
        final_equity, final_rows = _refine_hours(
            refinement_base_full, legs, unique_ends, ONE_MIN, bars_1m,
        )
        result_by_period = {
            name: _period_bounds(final_equity.loc[curve.index], curve,
                                 fee_h1=candidate_scan[name])
            for name, curve in period_curves.items()
        }
        resolution = "1m"

    output = {
        "id": config["id"],
        "venue": "Binance",
        "source_account": "exp-cloud-close-efficient-trend-20261109-v1, scenario=actual, policy=trend_efficiency",
        "source_h1_file": str(H1_EQUITY.relative_to(ROOT)),
        "source_funded_file": str(FUNDED.relative_to(ROOT)),
        "source_decisions_file": str(DECISIONS.relative_to(ROOT)),
        "resolution_used": resolution,
        "threshold_drawdown_pct": 35.0,
        "periods": {
            "full": result_by_period["full"],
            "development": result_by_period["development"],
        },
        "baseline_h1_metrics": {
            "full": {"final_usdt": float(baseline_rows.loc["all", "final_usdt"]),
                     "dd_mark_pct": float(baseline_rows.loc["all", "dd_mark_pct"]),
                     "dd_bound_pct": float(baseline_rows.loc["all", "dd_bound_pct"]),
                     "trades": int(baseline_rows.loc["all", "trades"])},
            "development": {"final_usdt": float(baseline_rows.loc["dev", "final_usdt"]),
                            "dd_mark_pct": float(baseline_rows.loc["dev", "dd_mark_pct"]),
                            "dd_bound_pct": float(baseline_rows.loc["dev", "dd_bound_pct"]),
                            "trades": int(baseline_rows.loc["dev", "trades"])},
        },
        "critical_windows_by_period": candidates,
        "raw_h1_critical_windows_by_period": candidates_raw,
        "fee_adjusted_h1_critical_windows_by_period": candidates,
        "refined_windows": final_rows,
        "five_minute_refinement": {
            "period_bounds": result_by_period_5m,
            "windows": rows_5m,
            "source_audit": source_audit_5m,
        },
        "one_minute_refinement": ({
            "period_bounds": result_by_period,
            "windows": final_rows,
            "source_audit": source_audit_1m,
        } if source_audit_1m is not None else None),
        "accounting_reconciliation": accounting_audit,
        "native_binance_only": True,
        "no_repricing_or_order_changes": True,
        "status": ("pass" if all(v["passes_35pct_upper_bound"] for v in result_by_period.values())
                   else "fail" if any(v["fails_35pct_lower_bound"] for v in result_by_period.values())
                   else "unresolved_35pct_interval"),
    }
    temp = OUTPUT_PATH.with_suffix(OUTPUT_PATH.suffix + ".tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")
    temp.replace(OUTPUT_PATH)
    print(json.dumps({"output": str(OUTPUT_PATH.relative_to(ROOT)), "status": output["status"],
                      "periods": output["periods"], "resolution_used": resolution},
                     indent=2, allow_nan=False), flush=True)
    return output


def mini_check() -> None:
    """Check all-candidate discovery and reuse of the envelope with Binance-native H1 quotes."""
    index = pd.date_range("2024-12-31T21:00:00Z", periods=5, freq="h")
    synthetic = pd.DataFrame({
        "nav": [1000, 1200, 800, 1250, 780],
        "open_nav": [1000, 1200, 800, 1250, 780],
        "open_exit_nav": [1000, 1200, 800, 1250, 780],
        "lower_nav": [1000, 1180, 760, 1230, 770],
        "upper_nav": [1000, 1210, 810, 1260, 790],
    }, index=index)
    found = critical_windows(synthetic, 35.0)
    if len(found) != 2:
        raise AssertionError("Candidate discovery must retain every >35% peak/trough window")

    fee_index = pd.date_range("2025-01-01T00:00:00Z", periods=2, freq="h")
    fee_hour_ms = int(fee_index[0].value // 1_000_000)
    fee_equity = pd.DataFrame({
        "nav": [100.0, 100.0], "open_nav": [100.0, 95.0],
        "open_exit_nav": [100.0, 95.0], "lower_nav": [90.0, 90.0],
        "upper_nav": [100.0, 94.0],
    }, index=fee_index)
    fee_legs = pd.DataFrame([
        {"entry_ms": fee_hour_ms - 2 * HOUR, "exit_bar_ms": fee_hour_ms - HOUR, "exit_phase": "close",
         "signed_size": 2.0, "exit_price": 100.0, "exit_fee_rate": 0.001,
         "entry_price": 100.0},
        {"entry_ms": fee_hour_ms, "exit_bar_ms": fee_hour_ms, "exit_phase": "open",
         "signed_size": 1.0, "exit_price": 100.0, "exit_fee_rate": wallet.TAKER,
         "entry_price": 100.0},
    ])
    fee_all, _ = _fee_adjusted_envelope(fee_equity, fee_legs)
    fee_fine, _ = _fee_adjusted_envelope(fee_equity, fee_legs, fine_hours=[fee_index[0]])
    if not np.isclose(fee_all.lower_nav.iloc[0], 89.8) or not np.isclose(fee_fine.lower_nav.iloc[0], 90.0):
        raise AssertionError("Coarse/fine exit-fee lower bounds are not applied exactly once")
    if not np.isclose(fee_all.upper_nav.iloc[1], 95.05):
        raise AssertionError("Entry-fee prepayment upper bound is missing")

    hour_ms = pd.Timestamp("2025-01-01T00:00:00Z").value // 1_000_000
    quote_clock = np.array([hour_ms], dtype=np.int64)
    native_h1 = np.array([[100.0, 104.0, 98.0, 102.0]])
    fake_legs = pd.DataFrame([{
        "event_id": "mini", "symbol": "ETH", "side": 1,
        "entry_ms": hour_ms, "entry_price": 100.0, "exit_bar_ms": hour_ms + HOUR,
        "exit_phase": "close", "exit_price": 102.0, "exit_fee_rate": wallet.TAKER,
        "exit_reason": "ema_flip", "stop_price": 95.0, "signed_size": 1.0,
    }])
    opens = np.array([100, 101, 100, 99, 100, 101, 102, 103, 102, 101, 102, 103.0])
    highs = np.array([101, 102, 101, 100, 102, 103, 104, 104, 103, 102, 103, 103.0])
    lows = np.array([99, 100, 98, 98.5, 99, 100, 101, 102, 101, 100, 101, 101.0])
    closes = np.r_[opens[1:], 102.0]
    subbars = pd.DataFrame({
        "symbol": "ETH", "time": hour_ms + np.arange(12, dtype=np.int64) * FIVE_MIN,
        "open": opens, "high": highs, "low": lows, "close": closes,
    })
    old = pd.Series({"open_nav": 999.95, "open_exit_nav": 999.95, "nav": 1001.95,
                     "lower_nav": 990.0, "upper_nav": 1010.0})
    hour_end = pd.Timestamp(hour_ms + HOUR, unit="ms", tz="UTC")
    mini_equity = pd.DataFrame([old.to_dict()], index=pd.DatetimeIndex([hour_end]))
    previous = binance.quotes
    try:
        binance.quotes = lambda symbol: (quote_clock, native_h1)
        mini_refined, rows = _refine_hours(mini_equity, fake_legs, [hour_end], FIVE_MIN, subbars)
    finally:
        binance.quotes = previous
    result = rows[0]
    if result["observed_symbols"] != ["ETH"] or result["active_symbols"] != ["ETH"]:
        raise AssertionError("Mini envelope must use complete Binance-native synthetic 5m bars")
    refined_upper = float(mini_refined.upper_nav.iloc[0])
    if (result["min_observed_subbar_close_nav_usdt"] is None
            or not 1000.0 <= refined_upper < 1010.0):
        raise AssertionError("Synchronized 5m marks did not tighten the broad H1 envelope")
    print("Binance efficient-trend DD mini-check: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--mini-check", action="store_true")
    args = parser.parse_args()
    if args.mini_check:
        mini_check()
        return
    run(args.config.resolve())


if __name__ == "__main__":
    main()
