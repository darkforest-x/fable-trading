"""Native Binance 1m turnover limits for the frozen September 1x/2x account.

Capacity is measured from the 60 complete native USD-M 1m quote-volume bars
ending immediately before each funded parent entry. The entry minute and its
full H1 are used only to verify execution-clock and source-price parity; none
of their future volume enters the capacity calculation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from functools import lru_cache
import gc
import json
from pathlib import Path
import shutil
from urllib.parse import quote
from zipfile import BadZipFile

import numpy as np
import pandas as pd

from yoyo.data.binance_um_vision_fetch import BASE as VISION_BASE, fetch
from yoyo.evaluation import cloud_close_binance_relative_minutes as minute_risk
from yoyo.evaluation import cloud_close_binance_relative_size as relative_size
from yoyo.evaluation import cloud_close_verified_exposure as engine


ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-cloud-close-binance-entry-capacity-20261109-v1"
CACHE = ROOT / "data/research/cloud_close_binance_entry_capacity"
BASELINE = minute_risk.EXP
BASELINE_FUNDED = BASELINE / "funded_contrarian_half.csv.gz"
BASELINE_HOURLY = BASELINE / "hourly_contrarian_half.csv.gz"
HOUR_MS = 3_600_000
MINUTE_MS = 60_000
DAY_MS = 86_400_000
ENTRY_PERIODS = {
    "all": (None, None),
    "dev": (None, "2025-01-01T00:00Z"),
    "2025": ("2025-01-01T00:00Z", "2026-01-01T00:00Z"),
    "2026": ("2026-01-01T00:00Z", None),
}
FEE_TAKER = 0.0005
FEE_MAKER = 0.0002
MARKET_SLIP = 0.0005
FIELDS = ["open", "high", "low", "close"]
CSV_COLUMNS = ["time", *FIELDS, "base_volume", "quote_volume"]
REUSE_KLINES = minute_risk.CACHE / "klines"


def frozen_baseline() -> pd.DataFrame:
    if not BASELINE_FUNDED.is_file() or not BASELINE_HOURLY.is_file():
        raise FileNotFoundError("Frozen 1,586-parent Binance contrarian-half baseline is missing")
    frame = pd.read_csv(BASELINE_FUNDED, float_precision="round_trip", dtype={"symbol": str})
    required = {"event_id", "parent_id", "symbol", "side", "entry_ms", "exit_bar_ms",
                "notional", "cash_pnl", "exposure"}
    if not required.issubset(frame.columns):
        raise ValueError("Frozen Binance baseline fields changed")
    if (len(frame) != 1586 or frame.event_id.duplicated().any() or
            frame.parent_id.duplicated().any() or set(frame.exposure.unique()) != {1.0, 2.0}):
        raise ValueError("Expected 1,586 unique funded parents at frozen 1x/2x exposure")
    return frame


def _source_context():
    """Load the same native Binance paths, exits, and prices as the prior 1x/2x study."""
    archive = relative_size.archive
    archive_summary = json.loads((archive.EXP / "summary.json").read_text())
    admin_summary = json.loads((relative_size.bundle.admin.study.EXP / "summary.json").read_text())
    additions = set(archive_summary["archived_additions"])
    additions.update(admin_summary["new_archive_cohorts"]["priced"])
    return archive, additions


def _check_frozen_account(rows: pd.DataFrame, quotes) -> tuple[dict, pd.DataFrame, pd.Series]:
    """Rebuild exact native parents and confirm 1x/2x sizing matches frozen receipts."""
    baseline = frozen_baseline().set_index("event_id").sort_index()
    data, market = relative_size.make_account(rows, quotes)
    parents = market[-1].set_index("event_id").sort_index()
    if set(parents.index) != set(baseline.index) or not parents.index.is_unique:
        raise ValueError("Prepared native Binance parent roster differs from 1,586 frozen receipts")
    for field in ("parent_id", "symbol", "side", "entry_ms", "exit_bar_ms"):
        left, right = parents[field].sort_index(), baseline[field].sort_index()
        same = (np.array_equal(left.to_numpy(), right.to_numpy()) if field in {"parent_id", "symbol"}
                else np.array_equal(left.to_numpy(dtype=float), right.to_numpy(dtype=float)))
        if not same:
            raise ValueError(f"Frozen Binance parent {field} or timing changed")
    # Reconstruct the previously selected causal ETH/BTC relative-size rule;
    # the frozen account's realized 1x/2x per-parent mapping must match it.
    state = relative_size.relative(quotes)
    balance = parents.entry_ms.map(state)
    if balance.isna().any():
        raise ValueError("Missing frozen ETH/BTC 30-day sizing state on the funded roster")
    expected = 1.5 - 0.5 * balance.mul(parents.side)
    actual = baseline.exposure.reindex(parents.index)
    if not np.allclose(expected.to_numpy(), actual.to_numpy(), rtol=0, atol=1.e-12):
        raise ValueError("Frozen 1x/2x exposure mapping differs from the original contrarian-half rule")
    if not parents.entry_price.gt(0).all() or not (parents.entry_ms % HOUR_MS == 0).all():
        raise ValueError("Parent entry price or UTC H1 opening clock is invalid")
    for symbol, group in parents.groupby("symbol", sort=False):
        clock, ohlc = quotes(str(symbol))
        indices = clock.searchsorted(group.entry_ms.to_numpy(dtype="int64"))
        if ((indices >= len(clock)).any() or not np.array_equal(clock[indices], group.entry_ms.to_numpy(dtype="int64")) or
                not np.allclose(ohlc[indices, 0], group.entry_price.to_numpy(dtype=float), rtol=1e-8, atol=1e-12)):
            raise ValueError(f"Frozen entry price does not match Binance native H1 open for {symbol}")
    return data, market, actual


def _utc_day(ms: int) -> str:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").strftime("%Y-%m-%d")


def required_daily_jobs(parents: pd.DataFrame) -> list[tuple[str, str]]:
    """Daily files covering prior 60m, the exact entry minute, and entry H1 audit."""
    jobs: set[tuple[str, str]] = set()
    for row in parents.itertuples():
        entry = int(row.entry_ms)
        if entry % HOUR_MS:
            raise ValueError(f"Non-H1 entry clock in frozen Binance path: {row.event_id}")
        first = entry - HOUR_MS
        end = entry + HOUR_MS  # exclusive; audit uses the entire H1 after its opening
        day = (first // DAY_MS) * DAY_MS
        while day < end:
            jobs.add((str(row.symbol) + "USDT", _utc_day(day)))
            day += DAY_MS
    return sorted(jobs)


def _daily_path(symbol: str, day: str) -> tuple[Path, str]:
    name = f"{symbol}-1m-{day}.zip"
    # Binance's public symbol set contains UTF-8 contract names. Encode URL
    # path components but use that same encoded basename in the local cache.
    archive_name = quote(name, safe="")
    return CACHE / "klines" / symbol / archive_name, archive_name


def _fetch_daily(job: tuple[str, str]) -> dict:
    symbol, day = job
    path, name = _daily_path(symbol, day)
    encoded_symbol = quote(symbol, safe="")
    url = f"{VISION_BASE}/daily/klines/{encoded_symbol}/1m/{name}"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size:
        return dict(symbol=symbol, day=day, status="cached", url=url,
                    path=str(path.relative_to(ROOT)), size_bytes=path.stat().st_size)
    decoded_name = f"{symbol}-1m-{day}.zip"
    reusable = REUSE_KLINES / symbol / decoded_name
    if reusable.is_file() and reusable.stat().st_size:
        temporary = path.with_suffix(".copying")
        shutil.copyfile(reusable, temporary)
        temporary.replace(path)
        return dict(symbol=symbol, day=day, status="reused_local_official_zip", url=url,
                    path=str(path.relative_to(ROOT)), reused_from=str(reusable.relative_to(ROOT)),
                    size_bytes=path.stat().st_size)
    _kind, _symbol, status = fetch(CACHE, "klines", symbol, url)
    return dict(symbol=symbol, day=day, status=status, url=url,
                path=str(path.relative_to(ROOT)) if path.is_file() else None,
                size_bytes=path.stat().st_size if path.is_file() else 0)


def acquire_daily(jobs: list[tuple[str, str]], workers: int = 4) -> pd.DataFrame:
    if workers < 1 or workers > 4:
        raise ValueError("Use one to four concurrent official archive requests")
    CACHE.mkdir(parents=True, exist_ok=True)
    receipt = CACHE / "daily_archive_receipt.csv"
    previous = {}
    if receipt.is_file():
        prior = pd.read_csv(receipt, dtype={"symbol": str, "day": str})
        previous = {(r.symbol, r.day): r._asdict() for r in prior.itertuples(index=False)}
    results = {key: previous[key] for key in jobs if key in previous and
               previous[key].get("status") in {"cached", "fetched", "reused_local_official_zip", "missing"}}
    pending = [job for job in jobs if job not in results]
    temp = receipt.with_suffix(".tmp")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {pool.submit(_fetch_daily, job): job for job in pending}
        done = 0
        for future in as_completed(future_map):
            job = future_map[future]
            results[job] = future.result()
            done += 1
            if done % 100 == 0 or done == len(pending):
                pd.DataFrame(results.values()).sort_values(["symbol", "day"]).to_csv(temp, index=False)
                temp.replace(receipt)
                print(f"Archived Binance 1m days {done}/{len(pending)} new; total {len(results)}/{len(jobs)}", flush=True)
    pd.DataFrame(results.values()).sort_values(["symbol", "day"]).to_csv(temp, index=False)
    temp.replace(receipt)
    out = pd.DataFrame(results.values())
    if len(out) != len(jobs) or out.duplicated(["symbol", "day"]).any():
        raise ValueError("Official daily archive acquisition receipt is incomplete")
    if out.status.eq("failed").any():
        raise IOError(f"Some Binance 1m daily archives failed after bounded retries: {out[out.status.eq('failed')].head().to_dict('records')}")
    return out


@lru_cache(maxsize=32)
def read_daily(symbol: str, day: str) -> pd.DataFrame | None:
    """Read official USD-M 1m OHLC, base volume and quote volume without unit guesses."""
    path, _ = _daily_path(symbol, day)
    if not path.is_file() or not path.stat().st_size:
        return None
    return _parse_daily(path)


def _parse_daily(path: Path) -> pd.DataFrame | None:
    """Parse one official S3 daily ZIP at its native millisecond timestamps."""
    try:
        frame = pd.read_csv(path, header=None, compression="zip", usecols=[0, 1, 2, 3, 4, 5, 7],
                            names=CSV_COLUMNS, dtype=str)
    except (BadZipFile, OSError, ValueError, pd.errors.ParserError):
        return None
    if frame.empty:
        return None
    if pd.isna(pd.to_numeric(frame.time.iloc[0], errors="coerce")):
        frame = frame.iloc[1:].copy()
    numeric = frame.apply(pd.to_numeric, errors="coerce")
    if numeric.empty or numeric.isna().any().any():
        return None
    numeric = numeric.set_index("time").sort_index()
    numeric.index = numeric.index.astype("int64")
    if numeric.index.duplicated().any() or (numeric.index % MINUTE_MS != 0).any():
        return None
    values = numeric[CSV_COLUMNS[1:]].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or (numeric[FIELDS] <= 0).any().any() or
            (numeric.base_volume < 0).any() or (numeric.quote_volume < 0).any() or
            (numeric.low > numeric[["open", "close"]].min(axis=1)).any() or
            (numeric.high < numeric[["open", "close"]].max(axis=1)).any()):
        return None
    return numeric


def _valid_turnover(frame: pd.DataFrame) -> bool:
    base = frame.base_volume.to_numpy(dtype=float)
    quote = frame.quote_volume.to_numpy(dtype=float)
    if (base < 0).any() or (quote < 0).any():
        return False
    zero = base == 0
    if (quote[zero] != 0).any() or ((quote[~zero] <= 0).any() if (~zero).any() else False):
        return False
    vwap = np.divide(quote, base, out=np.zeros_like(quote), where=~zero)
    return not (((vwap < frame.low.to_numpy() * (1 - 1e-5)) |
                 (vwap > frame.high.to_numpy() * (1 + 1e-5))) & ~zero).any()


def _combine_days(symbol: str, first_ms: int, end_ms: int) -> tuple[pd.DataFrame | None, list[str]]:
    days, frames = [], []
    day_ms = (first_ms // DAY_MS) * DAY_MS
    while day_ms < end_ms:
        day = _utc_day(day_ms)
        days.append(day)
        frame = read_daily(symbol, day)
        if frame is not None:
            frames.append(frame)
        day_ms += DAY_MS
    if len(frames) != len(days):
        return None, days
    result = pd.concat(frames).sort_index()
    if result.index.duplicated().any():
        return None, days
    return result, days


def observe(row, quotes) -> dict:
    """Produce one causal 60-minute volume feature and a separate entry/H1 audit."""
    event, symbol, entry = str(row.event_id), str(row.symbol) + "USDT", int(row.entry_ms)
    first, end = entry - HOUR_MS, entry + HOUR_MS
    data, days = _combine_days(symbol, first, end)
    record = dict(event_id=event, symbol=str(row.symbol), side=int(row.side), entry_ms=entry,
                  window_start_ms=first, window_end_exclusive_ms=entry, entry_h1_end_exclusive_ms=end,
                  required_minutes=60, prior_minutes=0, zero_prior_minutes=None,
                  source_days=";".join(days), status=None, prior_median_quote_usdt=None,
                  prior_mean_quote_usdt=None, entry_quote_volume_usdt=None,
                  entry_base_volume=None, entry_open=None, h1_ohlc_match=False,
                  baseline_entry_price=float(row.entry_price))
    if data is None:
        expected_paths = [_daily_path(symbol, day)[0] for day in days]
        if any(not p.exists() for p in expected_paths):
            record["status"] = "missing_archive"
        else:
            record["status"] = "invalid_or_overlapping_archive"
        return record
    prior_clock = entry - MINUTE_MS * np.arange(60, 0, -1, dtype=np.int64)
    prior = data.reindex(prior_clock)
    record["prior_minutes"] = int(prior.open.notna().sum())
    if prior.isna().any().any():
        record["status"] = "incomplete_prior_60m"
        return record
    if not _valid_turnover(prior):
        record["status"] = "invalid_prior_turnover"
        return record
    entry_bar = data.reindex([entry])
    if entry_bar.isna().any().any():
        record["status"] = "entry_minute_missing"
        return record
    entry_hour_clock = entry + MINUTE_MS * np.arange(60, dtype=np.int64)
    entry_hour = data.reindex(entry_hour_clock)
    if entry_hour.isna().any().any():
        record["status"] = "incomplete_entry_h1_for_validation"
        return record
    if not _valid_turnover(entry_hour):
        record["status"] = "invalid_entry_hour_turnover"
        return record
    aggregate = np.array([entry_hour.open.iat[0], entry_hour.high.max(),
                          entry_hour.low.min(), entry_hour.close.iat[-1]], dtype=float)
    clock, ohlc = quotes(str(row.symbol))
    i = int(clock.searchsorted(entry))
    if i == len(clock) or int(clock[i]) != entry:
        record["status"] = "native_h1_entry_missing"
        return record
    if not np.allclose(aggregate, ohlc[i, :4], rtol=1e-8, atol=1e-12):
        record["status"] = "entry_h1_ohlc_conflict"
        return record
    if not np.isclose(float(entry_bar.open.iat[0]), float(row.entry_price), rtol=1e-8, atol=1e-12):
        record["status"] = "entry_minute_open_conflict"
        return record
    median = float(prior.quote_volume.median())
    record.update(prior_median_quote_usdt=median,
                  prior_mean_quote_usdt=float(prior.quote_volume.mean()),
                  zero_prior_minutes=int(prior.quote_volume.eq(0).sum()),
                  entry_quote_volume_usdt=float(entry_bar.quote_volume.iat[0]),
                  entry_base_volume=float(entry_bar.base_volume.iat[0]),
                  entry_open=float(entry_bar.open.iat[0]), h1_ohlc_match=True)
    record["status"] = "zero_prior_median" if median <= 0 else "observed"
    return record


def collect_observations(parents: pd.DataFrame, quotes, workers: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    jobs = required_daily_jobs(parents)
    receipt = acquire_daily(jobs, workers=workers)
    if receipt.status.eq("missing").any():
        print(f"Binance official S3 daily archives unavailable for {int(receipt.status.eq('missing').sum())} required symbol-days; affected parents will remain unknown.", flush=True)
    records = []
    for n, row in enumerate(parents.reset_index().itertuples(index=False), 1):
        records.append(observe(row, quotes))
        if n % 100 == 0 or n == len(parents):
            print(f"Validated native Binance prior-volume windows {n}/{len(parents)}", flush=True)
    return pd.DataFrame(records), receipt


def _funded_metrics(funds: pd.DataFrame, curve: pd.DataFrame) -> dict:
    wallet = relative_size.base.wallet
    return {name: wallet.summarize(funds, curve, *limits)
            for name, limits in ENTRY_PERIODS.items()}


def _snapshot_reconcile(frozen: pd.DataFrame, frozen_curve: pd.DataFrame):
    def check(_pf, _data, funds, curve, _multiples, slip):
        if slip != 0:
            raise ValueError("Only zero-slip baseline should use frozen reconciliation")
        left, right = funds.sort_index(), frozen.sort_index()
        if not left.index.equals(right.index):
            raise ValueError("Native VectorBT no-cap parent roster differs from the frozen baseline")
        for field in ("cash_pnl", "notional", "net_return", "exposure"):
            if not np.allclose(left[field], right[field], rtol=1e-9, atol=1e-5):
                raise ValueError(f"Native VectorBT baseline {field} differs from frozen account")
        if not curve.index.equals(frozen_curve.index) or not curve.columns.equals(frozen_curve.columns):
            raise ValueError("Native VectorBT no-cap calendar or NAV stages differ from frozen baseline")
        if not np.allclose(curve, frozen_curve, rtol=1e-9, atol=.005):
            raise ValueError("Native VectorBT no-cap hourly account differs from frozen baseline")
    return check


def _enrich_receipt(path: Path, observed: pd.DataFrame, cap: pd.Series) -> pd.DataFrame:
    funds = pd.read_csv(path, index_col="event_id", float_precision="round_trip")
    meta = observed.set_index("event_id")
    for col in ("status", "prior_median_quote_usdt", "prior_mean_quote_usdt", "zero_prior_minutes",
                "entry_quote_volume_usdt", "h1_ohlc_match"):
        funds[col] = funds.index.map(meta[col])
    funds["entry_limit_usdt"] = cap.reindex(funds.index)
    funds["notional_to_prior_median"] = funds.notional / funds.prior_median_quote_usdt
    funds.to_csv(path, index_label="event_id")
    return funds


def _arm_summary(funds: pd.DataFrame, observed: pd.DataFrame, cap: pd.Series, *, fraction,
                 metrics: dict, margin: dict, baseline_replay: bool = False) -> dict:
    expected = int(cap.gt(0).sum())
    finite_cap = cap.reindex(funds.index).to_numpy(dtype=float) if len(funds) else np.array([])
    actual_notional = funds.notional.to_numpy(dtype=float) if len(funds) else np.array([])
    finite_pair = np.isfinite(finite_cap)
    out = dict(funded_parents=len(funds), eligible_positive_capacity_windows=expected,
               excluded_or_unknown=len(cap)-expected,
               entry_capacity_fraction=fraction, metrics=metrics, margin=margin,
               fills_at_limit=int(np.isclose(actual_notional[finite_pair], finite_cap[finite_pair],
                                             rtol=1e-8, atol=1e-6).sum()),
               maximum_notional_over_limit=(float((actual_notional[finite_pair]-finite_cap[finite_pair]).max())
                                            if finite_pair.any() else None),
               baseline_replay_reconciled=baseline_replay)
    if len(funds):
        out["median_funded_notional_to_prior_median"] = float(funds.notional_to_prior_median.median()) if "notional_to_prior_median" in funds else None
    return out


def run(workers: int = 4) -> dict:
    if (EXP / "summary.json").exists():
        raise FileExistsError("Completed Binance entry-capacity study exists")
    EXP.mkdir(parents=True, exist_ok=True)
    archive, additions = _source_context()
    with archive.market_quotes(additions):
        rows, loader, _ = relative_size.bundle.paths("Binance")
        quotes = lru_cache(maxsize=None)(loader)
        data, market, exposure = _check_frozen_account(rows, quotes)
        parents = market[-1].set_index("event_id").sort_index()
        frozen = frozen_baseline().set_index("event_id").sort_index()
        observations, source_receipt = collect_observations(parents, quotes, workers)
        if observations.event_id.duplicated().any() or set(observations.event_id) != set(parents.index):
            raise ValueError("Minute source panel does not cover every frozen funded parent")
        observations.to_csv(EXP / "entry_capacity.csv.gz", index=False)
        source_receipt.to_csv(EXP / "daily_archive_sources.csv", index=False)
        observed = observations.set_index("event_id").reindex(parents.index)
        positive = observed.status.eq("observed") & observed.prior_median_quote_usdt.gt(0)
        if positive.isna().any():
            raise ValueError("Entry capacity source status is missing")
        medians = observed.prior_median_quote_usdt.where(positive, 0.).fillna(0.).astype(float)
        if (medians < 0).any() or not np.isfinite(medians.to_numpy()).all():
            raise ValueError("Invalid Binance prior 60-minute median quote volume")
        status_counts = observations.status.value_counts(dropna=False).to_dict()
        if int(positive.sum()) + int((~positive).sum()) != len(parents):
            raise ValueError("Coverage denominator does not reconcile to frozen parents")

        baseline_funds = frozen.copy()
        baseline_curve = pd.read_csv(BASELINE_HOURLY, index_col=0, parse_dates=True)
        replay = engine.simulate(data, market, quotes, exposure, 0., EXP, "baseline_replay",
                                 inspect_flag=minute_risk.inspect,
                                 snapshot=_snapshot_reconcile(baseline_funds, baseline_curve))
        if replay.get("unresolved"):
            raise ValueError("Frozen 1x/2x Binance baseline no longer passes native margin checks")
        baseline_metrics = _funded_metrics(baseline_funds, baseline_curve)
        baseline_files = {}
        for name, path in (("funded", BASELINE_FUNDED), ("hourly", BASELINE_HOURLY)):
            target = EXP / ("funded_unrestricted.csv.gz" if name == "funded" else "hourly_unrestricted.csv.gz")
            shutil.copy2(path, target)
            baseline_files[name] = str(target.relative_to(ROOT))
        frozen_stress_funds = pd.read_csv(BASELINE / "funded_contrarian_half_stress.csv.gz",
                                          index_col="event_id", float_precision="round_trip")
        frozen_stress_curve = pd.read_csv(BASELINE / "hourly_contrarian_half_stress.csv.gz",
                                          index_col=0, parse_dates=True)
        shutil.copy2(BASELINE / "funded_contrarian_half_stress.csv.gz", EXP / "funded_unrestricted_stress.csv.gz")
        shutil.copy2(BASELINE / "hourly_contrarian_half_stress.csv.gz", EXP / "hourly_unrestricted_stress.csv.gz")

        records = []
        for p, values in baseline_metrics.items():
            records.append(dict(arm="unrestricted", period=p, **values))
        arms = {
            "unrestricted": dict(metrics=baseline_metrics, funded_parents=len(baseline_funds),
                                 source="frozen original native 1x/2x account; replayed through VectorBT and cash/hourly reconciled",
                                 files=baseline_files, margin=replay.get("margin")),
            "unrestricted_stress": dict(metrics=_funded_metrics(frozen_stress_funds, frozen_stress_curve),
                                        funded_parents=len(frozen_stress_funds),
                                        source="frozen original +5bp market-fill stress; reused without re-running the baseline"),
        }

        specs = [
            ("same_coverage_unrestricted", None, np.where(positive.to_numpy(), np.inf, 0.)),
            ("same_coverage_unrestricted_stress", MARKET_SLIP,
             np.where(positive.to_numpy(), np.inf, 0.)),
            ("cap_10pct", 0., medians * 0.10),
            ("cap_10pct_stress", MARKET_SLIP, medians * 0.10),
            ("cap_1pct", 0., medians * 0.01),
            ("cap_1pct_stress", MARKET_SLIP, medians * 0.01),
        ]
        for name, slip, values in specs:
            # In the paired same-coverage control, uncapped positive-volume
            # entries are replayed; zero, missing, or invalid histories get 0.
            cap = pd.Series(values, index=parents.index, dtype=float)
            case = name
            audit_slip = float(slip or 0.)
            result = engine.simulate(data, market, quotes, exposure, audit_slip, EXP, case,
                                     inspect_flag=minute_risk.inspect, entry_limits=cap)
            if result.get("unresolved"):
                raise ValueError(f"Binance native margin flags unresolved in {name}")
            path = EXP / f"funded_{case}.csv.gz"
            funds = _enrich_receipt(path, observations, cap)
            curve = pd.read_csv(EXP / f"hourly_{case}.csv.gz", index_col=0, parse_dates=True)
            if int(funds.index.nunique()) != int(cap.gt(0).sum()):
                raise ValueError(f"Funded rows differ from positive pre-entry coverage in {name}")
            over = funds.notional - funds.entry_limit_usdt
            if over.gt(1.e-6).any():
                raise ValueError(f"Native actual entry notional exceeded its volume cap in {name}")
            metric = _funded_metrics(funds, curve)
            records.extend(dict(arm=case, period=p, **row) for p, row in metric.items())
            arms[case] = _arm_summary(funds, observations, cap,
                                      fraction=(0.10 if "10pct" in name else 0.01 if "1pct" in name else None),
                                      metrics=metric, margin=result.get("margin", {}))
            arms[case]["coverage_status_counts"] = status_counts
            arms[case]["positive_prior_volume_parents"] = int(positive.sum())
            arms[case]["parents_at_or_above_cap"] = int(np.isclose(funds.notional, funds.entry_limit_usdt,
                                                                    rtol=1e-8, atol=1e-6).sum())
            print(case, metric["all"], "funded", len(funds), "of", len(parents), flush=True)
            del funds, curve, result
            gc.collect()

        # Ensure both surviving input and account cash reconcile at every arm.
        for case in ["same_coverage_unrestricted", "same_coverage_unrestricted_stress",
                     "cap_10pct", "cap_10pct_stress", "cap_1pct", "cap_1pct_stress"]:
            funds = pd.read_csv(EXP / f"funded_{case}.csv.gz", index_col="event_id", float_precision="round_trip")
            curve = pd.read_csv(EXP / f"hourly_{case}.csv.gz", index_col=0, parse_dates=True)
            if not np.isclose(1000 + funds.cash_pnl.sum(), curve.nav.iat[-1], rtol=1e-9, atol=.01):
                raise ValueError(f"Funded cash and final NAV do not reconcile in {case}")
        pd.DataFrame(records).to_csv(EXP / "account_metrics.csv", index=False)
        dates = pd.to_datetime(observations.entry_ms, unit="ms", utc=True)
        counts_by_year = observations.assign(entry_year=dates.dt.year).groupby("entry_year").status.value_counts().unstack(fill_value=0)
        yearly_status = {
            str(year): {str(status): int(count) for status, count in values.items()}
            for year, values in counts_by_year.to_dict(orient="index").items()
        }
        summary = dict(
            scope="Frozen Binance USD-M native 1x/2x September account; 1,586 V1.7 funded parents; original admission and exit paths; no November two-leg/funding-adjusted account.",
            source=dict(provider="Binance public data.binance.vision", endpoint="/data/futures/um/daily/klines/<symbol>/1m/<symbol>-1m-YYYY-MM-DD.zip",
                        minute_volume="column 7 quote volume in USDT; column 5 base volume", source_jobs=len(source_receipt),
                        source_status={str(k): int(v) for k, v in source_receipt.status.value_counts().to_dict().items()},
                        source_receipt=str((CACHE / "daily_archive_receipt.csv").relative_to(ROOT)),
                        events=len(observations), event_status_counts={str(k): int(v) for k, v in status_counts.items()},
                        yearly_status_counts=yearly_status,
                        observed_positive_prior60=int(positive.sum()), excluded_or_unknown=int((~positive).sum()),
                        h1_ohlc_matches=int(observations.h1_ohlc_match.sum()), entry_open_matches=int(observations.status.isin(["observed", "zero_prior_median"]).sum()),
                        zero_quote_entry_minutes=int(observations.entry_quote_volume_usdt.fillna(-1).eq(0).sum()),
                        zero_median_prior_windows=int(observations.status.eq("zero_prior_median").sum())),
            baseline=dict(parents=len(baseline_funds), exposure_counts={str(float(k)): int(v) for k, v in baseline_funds.exposure.value_counts().sort_index().to_dict().items()},
                          start_utc=pd.to_datetime(baseline_funds.entry_ms.min(), unit="ms", utc=True).isoformat(),
                          last_entry_utc=pd.to_datetime(baseline_funds.entry_ms.max(), unit="ms", utc=True).isoformat(),
                          last_exit_utc=pd.to_datetime(baseline_funds.exit_bar_ms.max(), unit="ms", utc=True).isoformat(),
                          cash_reconciled=True, native_vectorbt_replay_matches_frozen=True,
                          metrics=baseline_metrics),
            limits=dict(cap_basis="fraction times median quote volume from exactly the preceding 60 complete Binance USD-M 1m bars; actual funded parent entry notional is capped at this value.",
                        arms={"10%": "0.10 * prior60 median quote volume", "1%": "0.01 * prior60 median quote volume sensitivity"},
                        same_coverage_control="Uncapped account restricted to parents with a complete, valid 60m window and strictly positive median; no later replacements.",
                        unknown="Missing archives, incomplete windows, price conflicts, or invalid volumes receive zero entry limit in constrained arms and remain separately counted.",
                        execution="Existing 9-slot shared-cash VectorBT account; frozen entry order/slot and exact child paths; market limits use native actual notional; 5bp extra adverse market slippage stress.",
                        fees="Frozen model maker 2bp / taker 5bp. Binance historical VIP-specific fee tier is not asserted.",
                        funding="Not included.",
                        limitations=["Minute quote-volume median is a turnover proxy, not visible order-book depth or guaranteed fill capacity.",
                                     "No minimum quantity, lot size, queue position, spread, nonlinear market impact, or Binance funding is modeled.",
                                     "A complete entry H1 is checked against the frozen H1 source; those post-entry minutes are not inputs to the cap."]),
            arms=arms)
        (EXP / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False, default=_json_default) + "\n")
        print(json.dumps({k: v.get("metrics", {}).get("all") for k, v in arms.items()}, indent=2, default=_json_default), flush=True)
        return summary


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if pd.isna(value):
        return None
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Not JSON serializable: {type(value).__name__}")


def preflight() -> dict:
    """Check frozen denominator, 1x/2x roster, source parsing, and cap arithmetic."""
    baseline = frozen_baseline()
    sample_files = sorted(REUSE_KLINES.glob("*/*.zip"))
    if not sample_files:
        raise FileNotFoundError("No already-fetched Binance native 1m ZIP available for parser preflight")
    sample = sample_files[0]
    symbol, day = sample.parent.name, sample.name.removesuffix(".zip").split("-1m-")[-1]
    frame = _parse_daily(sample)
    if frame is None or frame.empty or not _valid_turnover(frame.head(min(60, len(frame)))):
        raise ValueError("Cached official Binance daily 1m OHLCV parser failed")
    volumes = pd.Series([100.0 + i for i in range(60)])
    limit = float(volumes.median() * .10)
    if not np.isclose(limit, 12.95):
        raise ValueError("Pre-entry median * 10% capacity arithmetic changed")
    archive, additions = _source_context()
    with archive.market_quotes(additions):
        rows, loader, _ = relative_size.bundle.paths("Binance")
        quotes = lru_cache(maxsize=None)(loader)
        data, market, exposure = _check_frozen_account(rows, quotes)
        prepared_parents = market[-1]
        check = dict(prepared_parent_count=len(prepared_parents), exposures=exposure.value_counts().sort_index().to_dict(),
                     account_action_shape=list(data["actions"].shape),
                     native_entry_open_parity="all 1,586 parents")
        del data, market, rows, quotes
        gc.collect()
    result = dict(parents=len(baseline), exposures={str(float(k)): int(v) for k, v in baseline.exposure.value_counts().sort_index().to_dict().items()},
                  sample_archive=str(sample.relative_to(ROOT)), sample_rows=len(frame),
                  sample_first_minute_ms=int(frame.index[0]), sample_quote_volume=float(frame.quote_volume.iloc[0]),
                  cap_formula_example=limit, source_account_check=check,
                  status="preflight_ok_no_full_archive_fetch_or_account_run")
    print(json.dumps(result, indent=2, default=_json_default), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.preflight == args.run:
        parser.error("Choose exactly one of --preflight or --run")
    if args.preflight:
        preflight()
    else:
        run(workers=args.workers)


if __name__ == "__main__":
    main()
