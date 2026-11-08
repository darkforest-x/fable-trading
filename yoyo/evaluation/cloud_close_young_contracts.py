"""Fund V1.7 opportunities conditional on the contract's archived listing age.

Only executed native paths are sampled. Listing age is measured at the actual
12H-confirmation/H1-open entry time using the exchange's archived contract
listing field. No 90-day price-history or cross-sectional-rank screen is used.
"""
from __future__ import annotations

import argparse
import gc
import json
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_daily_patch as patch
from yoyo.evaluation import cloud_close_binance_gap_repair as repaired
from yoyo.evaluation import cloud_close_binance_transfer as binance
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_scaleout as split
from yoyo.evaluation import cloud_close_slots as capacity


ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-young-contracts-20261109-v1"
OKX_PATHS = wallet.EXP / "paths.csv.gz"
OKX_ATR = wallet.SOURCE / "signals.csv"
OKX_LISTINGS = ROOT / "experiments/active/exp-ma-morphology-top20-20260922-v1/inputs/raw/instruments.json"
BINANCE_LISTINGS = ROOT / "data/kline_preholdout_binance_um15m/exchange_info.json"
PATCHED_WORK = repaired.WORK

SLOTS = 9
TARGET_FRACTION = 2.0 / 3.0
RUNNER_FRACTION = 1.0 / 3.0
AGE_LIMIT_MS = 90 * 24 * 60 * 60 * 1000
EXTRA_SLIP = 0.0005
PATH_TP = (0.0, 2.0)
VALID_CLASSES = ("young_lt_90d", "age_ge_90d")
PERIODS = {
    "all": (None, None),
    "dev": (None, "2025-01-01T00:00:00Z"),
    "2025": ("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    "2026": ("2026-01-01T00:00:00Z", None),
}


def _read_okx_listing_metadata() -> tuple[dict[str, int], dict[str, str], dict]:
    payload = json.loads(OKX_LISTINGS.read_text())
    if payload.get("code") != "0" or not isinstance(payload.get("data"), list):
        raise ValueError(f"Invalid archived OKX instruments payload: {OKX_LISTINGS}")
    times: dict[str, int] = {}
    states: dict[str, str] = {}
    duplicate_ids: list[str] = []
    for row in payload["data"]:
        if row.get("instType") != "SWAP" or row.get("settleCcy") != "USDT":
            continue
        inst_id = str(row.get("instId", ""))
        ticker = inst_id.split("-")[0]
        raw_time = str(row.get("listTime", ""))
        if not ticker or not raw_time.isdigit() or int(raw_time) <= 0:
            continue
        if ticker in times:
            duplicate_ids.append(ticker)
            continue
        times[ticker] = int(raw_time)
        states[ticker] = str(row.get("state", ""))
    if duplicate_ids:
        raise ValueError(f"Ambiguous OKX USDT swap base tickers: {sorted(set(duplicate_ids))}")
    stat = OKX_LISTINGS.stat()
    metadata = {
        "path": str(OKX_LISTINGS.relative_to(ROOT)),
        "archive_mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "archive_payload_server_time": payload.get("ts"),
        "source_field": "data[].listTime (milliseconds UTC)",
        "instrument_count_usdt_swaps_with_list_time": len(times),
        "state_counts": dict(Counter(states.values())),
        "snapshot_scope": "archived OKX instruments snapshot; delisted rows are not guaranteed present",
    }
    return times, states, metadata


def _read_binance_listing_metadata() -> tuple[dict[str, int], dict[str, str], dict]:
    payload = json.loads(BINANCE_LISTINGS.read_text())
    rows = payload.get("symbols")
    if not isinstance(rows, list) or not payload.get("serverTime"):
        raise ValueError(f"Invalid archived Binance exchangeInfo payload: {BINANCE_LISTINGS}")
    times: dict[str, int] = {}
    statuses: dict[str, str] = {}
    duplicates: list[str] = []
    for row in rows:
        if (row.get("contractType") != "PERPETUAL" or row.get("quoteAsset") != "USDT"
                or row.get("marginAsset") != "USDT"):
            continue
        symbol = str(row.get("symbol", ""))
        raw_time = row.get("onboardDate")
        try:
            listed = int(raw_time)
        except (TypeError, ValueError):
            continue
        if not symbol or listed <= 0:
            continue
        if symbol in times:
            duplicates.append(symbol)
            continue
        times[symbol] = listed
        statuses[symbol] = str(row.get("status", ""))
    if duplicates:
        raise ValueError(f"Ambiguous Binance USDT perpetual symbols: {sorted(set(duplicates))}")
    metadata = {
        "path": str(BINANCE_LISTINGS.relative_to(ROOT)),
        "snapshot_server_time_utc": pd.to_datetime(int(payload["serverTime"]), unit="ms", utc=True).isoformat(),
        "source_field": "symbols[].onboardDate (milliseconds UTC)",
        "contract_filter": "contractType=PERPETUAL, quoteAsset=USDT, marginAsset=USDT",
        "instrument_count_with_onboard_date": len(times),
        "status_counts": dict(Counter(statuses.values())),
        "snapshot_scope": "archived exchangeInfo snapshot; already-delisted rows are not guaranteed present",
    }
    return times, statuses, metadata


def _validate_event_paths(paths: pd.DataFrame, venue: str) -> None:
    if paths.empty:
        raise ValueError(f"{venue}: no executed paths")
    if paths.duplicated(["event_id", "tp_r"]).any():
        raise ValueError(f"{venue}: duplicate event/tp path key")
    grouped = paths.groupby("event_id", sort=False)
    if not grouped.size().eq(2).all():
        raise ValueError(f"{venue}: every event must have its 0R runner and 2R target path")
    if not grouped.tp_r.apply(lambda x: set(x.astype(float)) == set(PATH_TP)).all():
        raise ValueError(f"{venue}: incomplete fixed 0R/2R exit pair")
    for col in ("parent_id", "symbol", "side", "entry_ms", "is_control"):
        if not grouped[col].nunique(dropna=False).eq(1).all():
            raise ValueError(f"{venue}: event has inconsistent {col}")


def _attach_listing_age(paths: pd.DataFrame, listing_times: dict[str, int], statuses: dict[str, str],
                        venue: str, status_name: str = "listing_status") -> tuple[pd.DataFrame, pd.DataFrame]:
    _validate_event_paths(paths, venue)
    events = paths.drop_duplicates("event_id").copy()
    events["entry_ms"] = pd.to_numeric(events.entry_ms, errors="raise").astype("int64")
    events["listing_time_ms"] = events.symbol.map(listing_times)
    events[status_name] = events.symbol.map(statuses)
    events["listing_age_ms"] = events.entry_ms - events.listing_time_ms
    events["listing_age_days"] = events.listing_age_ms / 86_400_000.0
    events["listing_class"] = np.select(
        [events.listing_time_ms.isna(), events.listing_age_ms.lt(0), events.listing_age_ms.lt(AGE_LIMIT_MS)],
        ["missing_listing_time", "entry_before_list_time", "young_lt_90d"],
        default="age_ge_90d",
    )
    events["listing_valid"] = events.listing_class.isin(VALID_CLASSES)
    age_cols = ["listing_time_ms", status_name, "listing_age_ms", "listing_age_days", "listing_class", "listing_valid"]
    paths = paths.merge(events.set_index("event_id")[age_cols], on="event_id", how="left", validate="many_to_one")
    return paths, events


def read_okx_paths() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    paths = wallet.read_paths()
    paths = paths[paths.tp_r.isin(PATH_TP)].copy()
    source = pd.read_csv(OKX_ATR, usecols=["prior_atr_pct"])
    source.index = pd.RangeIndex(1, len(source) + 1)
    paths["source_row_number"] = pd.to_numeric(paths.source_row_number, errors="raise").astype("int64")
    paths["entry_order"] = paths.source_row_number.map(source.prior_atr_pct).fillna(np.inf)

    actual = paths[~paths.is_control].drop_duplicates("event_id")
    expected_rows = np.arange(1, len(source) + 1, dtype=np.int64)
    if (len(actual) != len(source) or not np.array_equal(np.sort(actual.source_row_number.to_numpy()), expected_rows)
            or not actual.parent_id.eq("signal-row-" + actual.source_row_number.astype(str).str.zfill(5)).all()
            or not actual.event_id.eq(actual.parent_id + ":signal").all()):
        raise ValueError("OKX raw actual paths no longer reconcile to the 12,707 source signal rows")
    controls = paths[paths.is_control].drop_duplicates("event_id")
    if not controls.event_id.eq(controls.parent_id + ":control").all():
        raise ValueError("OKX control event keys differ from their original parent identities")
    if not np.isfinite(paths.entry_order.to_numpy(dtype=float)).all():
        raise ValueError("OKX ATR priority contains a non-finite value")

    times, states, metadata = _read_okx_listing_metadata()
    paths, events = _attach_listing_age(paths, times, states, "OKX")
    metadata["raw_actual_events"] = int(len(actual))
    metadata["source_signal_rows"] = int(len(source))
    metadata["prior_atr_priority"] = "signals.csv prior_atr_pct keyed by one-based source_row_number; ascending as in V1.7 account"
    return paths, events, metadata


def _read_binance_raw_paths() -> tuple[pd.DataFrame, dict]:
    files = sorted(binance.WORK.glob("*/fills.csv.gz"))
    if len(files) != 632:
        raise ValueError(f"Expected 632 original Binance pre-eligibility work files, found {len(files)}")
    affected = set(patch.affected_tickers())
    if len(affected) != 43:
        raise ValueError(f"Frozen repair cohort changed: expected 43, found {len(affected)}")
    pieces = []
    patched_tickers = set()
    for original in files:
        ticker = original.parent.name
        source = original
        source_label = "transfer_work_raw"
        if ticker in affected:
            source = PATCHED_WORK / ticker / "fills.csv.gz"
            if not source.is_file():
                raise FileNotFoundError(f"Missing repaired raw path file for {ticker}: {source}")
            source_label = "feb2022_gap_repaired_raw"
            patched_tickers.add(ticker)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="The 'float_precision' argument is deprecated.*")
            frame = pd.read_csv(source, dtype={"symbol": str}, float_precision="round_trip")
        if frame.empty:
            continue
        if not set(binance.PATH_COLUMNS).issubset(frame.columns):
            raise ValueError(f"Unexpected Binance raw path schema in {source}")
        if frame.symbol.astype(str).ne(ticker[:-4] if ticker.endswith("USDT") else ticker).any():
            raise ValueError(f"Binance path symbol differs from quote-cache ticker {ticker}")
        frame["path_source"] = source_label
        pieces.append(frame)
    if patched_tickers != affected:
        raise ValueError(f"Repaired path coverage incomplete: {len(patched_tickers)} of {len(affected)}")
    paths = pd.concat(pieces, ignore_index=True)
    paths = paths[paths.tp_r.isin(PATH_TP)].copy()
    _validate_event_paths(paths, "Binance")
    meta = {
        "raw_work_files": len(files),
        "repaired_contract_files": len(patched_tickers),
        "raw_preeligibility_paths": True,
        "path_source_events": dict(Counter(paths.drop_duplicates("event_id").path_source)),
        "prior_90d_rank_flag_false_actual_events": int(
            paths[~paths.is_control].drop_duplicates("event_id").eligible_at_own_time.eq(False).sum()
        ),
        "eligible_at_own_time_used_as_filter": False,
    }
    return paths, meta


def read_binance_paths() -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    paths, raw_meta = _read_binance_raw_paths()
    listing_times, statuses, metadata = _read_binance_listing_metadata()
    # Transfer paths store the base ticker (e.g. BTC); exchangeInfo uses the
    # exact perpetual instrument symbol (BTCUSDT). Join only that exact suffix.
    times_by_ticker = {symbol[:-4]: value for symbol, value in listing_times.items() if symbol.endswith("USDT")}
    status_by_ticker = {symbol[:-4]: value for symbol, value in statuses.items() if symbol.endswith("USDT")}
    paths, events = _attach_listing_age(paths, times_by_ticker, status_by_ticker, "Binance", "contract_status")
    path_symbols = set(events.symbol.astype(str) + "USDT")
    missing = sorted(path_symbols - set(listing_times))
    metadata["path_symbol_count"] = len(set(events.symbol))
    metadata["missing_path_symbols"] = missing
    metadata["path_symbol_status_counts"] = dict(Counter(
        events[["symbol", "contract_status"]].drop_duplicates("symbol")["contract_status"]
        .fillna("missing").astype(str)
    ))
    metadata["path_event_status_counts"] = dict(Counter(
        events["contract_status"].fillna("missing").astype(str)
    ))
    raw_meta["raw_actual_events"] = int(events.loc[~events.is_control].shape[0])
    raw_meta["raw_control_events"] = int(events.loc[events.is_control].shape[0])
    raw_meta["archived_exchange_info_mapping_missing_symbols"] = missing
    return paths, events, {"listing_metadata": metadata, "raw_paths": raw_meta}


def _listing_counts(events: pd.DataFrame) -> dict:
    result = {}
    for label, group in (("actual", events[~events.is_control]), ("control", events[events.is_control])):
        counts = group.listing_class.value_counts().to_dict()
        result[label] = {
            "events": int(len(group)),
            "by_listing_class": {str(k): int(v) for k, v in counts.items()},
            "by_year_and_class": {
                f"{year}:{age_class}": int(n)
                for (year, age_class), n in group.assign(entry_year=pd.to_datetime(group.entry_ms, unit="ms", utc=True).dt.year)
                .groupby(["entry_year", "listing_class"], dropna=False).size().items()
            },
        }
    return result


def _cohort_samples(paths: pd.DataFrame, cohort: str) -> tuple[dict[str, pd.DataFrame], dict]:
    if cohort == "all_age":
        pool = paths[paths.listing_class.isin(VALID_CLASSES)].copy()
    elif cohort == "young_lt_90d":
        pool = paths[paths.listing_class.eq("young_lt_90d")].copy()
    else:
        raise ValueError(f"Unknown listing-age cohort: {cohort}")
    real = pool[~pool.is_control].copy()
    controls_before_match = pool[pool.is_control].copy()
    control = controls_before_match[controls_before_match.parent_id.isin(real.parent_id)].copy()
    paired_real = real[real.parent_id.isin(control.parent_id)].copy()
    samples = {
        "actual": real,
        "paired_actual": paired_real,
        "matched_control": control,
        "extra_slippage_5bp": real,
    }
    counts = {
        "actual_events": int(real.event_id.nunique()),
        "control_events_after_own_age_filter": int(controls_before_match.event_id.nunique()),
        "matched_control_events": int(control.event_id.nunique()),
        "paired_actual_events": int(paired_real.event_id.nunique()),
    }
    return samples, counts


def _account(venue: str, events: pd.DataFrame, slip: float) -> tuple[dict, pd.DataFrame, pd.DataFrame, dict]:
    if events.empty:
        raise ValueError(f"{venue}: empty account scenario")
    if venue == "OKX":
        metrics, positions, equity = capacity.run(events, SLOTS, TARGET_FRACTION, slip)
        gaps = 0
    elif venue == "Binance":
        prepared = repaired.prepare(events)
        gaps = int(prepared["gap_marks"])
        metrics, positions, equity = split.simulate(prepared, TARGET_FRACTION, slip)
    else:
        raise ValueError(venue)
    final_from_cash = 1000.0 + float(positions.cash_pnl.sum())
    final_from_marks = float(equity.nav.iloc[-1])
    if not np.isclose(final_from_cash, final_from_marks, rtol=1e-8, atol=0.01):
        raise ValueError(f"{venue} cash/NAV does not reconcile: {final_from_cash} vs {final_from_marks}")
    funded = int(len(positions))
    request_events = int(events.event_id.nunique())
    if funded > request_events:
        raise ValueError(f"{venue}: funded more positions than candidate events")
    return metrics, positions, equity, {
        "candidate_events": request_events,
        "funded_positions": funded,
        "unfunded_or_rejected_events": request_events - funded,
        "unquoted_position_hours": gaps,
        "final_cash_reconciled_usdt": final_from_cash,
        "final_marked_nav_usdt": final_from_marks,
    }


def _rank_membership() -> Counter:
    """Return the existing own-time rankable-contract counts by 12H close."""
    files = sorted(binance.WORK.glob("*/quotes.npz"))
    if len(files) != 632:
        raise ValueError(f"Expected 632 Binance quote caches for key reconciliation, found {len(files)}")
    affected = set(patch.affected_tickers())
    membership: Counter = Counter()
    seen = set()
    for original in files:
        ticker = original.parent.name
        if ticker in seen:
            raise ValueError(f"Duplicate Binance ticker quote cache: {ticker}")
        seen.add(ticker)
        source = PATCHED_WORK / ticker / "quotes.npz" if ticker in affected else original
        with np.load(source, allow_pickle=False) as archive:
            if "rank_times" not in archive.files:
                raise ValueError(f"Missing rank_times in {source}")
            times = np.asarray(archive["rank_times"], dtype=np.int64)
        if len(times) > 1 and np.any(np.diff(times) <= 0):
            raise ValueError(f"Unordered rank-time cache: {source}")
        membership.update(times.tolist())
    if seen.intersection(affected) != affected:
        raise ValueError("Rank membership misses one or more repaired Binance contracts")
    return membership


def reconcile_binance_eligible_keys(paths: pd.DataFrame) -> dict:
    """Read-only assertion that the reconstructed raw pool reproduces the old 90d arm."""
    membership = _rank_membership()
    enough_contracts = paths.entry_ms.map(membership).fillna(0).ge(10)
    prior_eligible = paths.eligible_at_own_time.astype(bool)
    eligible = paths[prior_eligible & enough_contracts].copy()
    real_parents = set(eligible.loc[~eligible.is_control, "parent_id"])
    expected = eligible[eligible.parent_id.isin(real_parents)]
    expected_keys = set(zip(expected.event_id.astype(str), expected.tp_r.astype(float)))
    del membership, enough_contracts, eligible, expected
    gc.collect()

    frozen = repaired.assembled(set(patch.affected_tickers()))
    actual_keys = set(zip(frozen.event_id.astype(str), frozen.tp_r.astype(float)))
    if expected_keys != actual_keys:
        missing = len(expected_keys - actual_keys)
        extra = len(actual_keys - expected_keys)
        raise ValueError(f"Binance preeligibility reconstruction key mismatch: missing={missing}, extra={extra}")
    return {
        "result": "pass",
        "raw_source_events": int(paths.event_id.nunique()),
        "raw_actual_events_with_prior_rank_flag_false": int(
            paths[~paths.is_control].drop_duplicates("event_id").eligible_at_own_time.eq(False).sum()
        ),
        "raw_control_events_with_prior_rank_flag_false": int(
            paths[paths.is_control].drop_duplicates("event_id").eligible_at_own_time.eq(False).sum()
        ),
        "old_rank_and_coverage_arm_event_path_keys": len(actual_keys),
        "old_arm_key_set_matches_repaired_assembled": True,
        "note": "Eligibility used only for this source-key reconciliation, never in young/all-age cohort selection.",
    }


def _mini_account(venue: str, paths: pd.DataFrame, cohort: str, quote_loader) -> dict:
    events = paths[(~paths.is_control) & paths.listing_class.eq(cohort)].drop_duplicates("event_id")
    if events.empty:
        raise ValueError(f"{venue}: no {cohort} event available for smoke")
    event = events.sort_values(["entry_ms", "event_id"]).iloc[0]
    pair = paths[paths.event_id.eq(event.event_id)].copy()
    start = int(event.entry_ms)
    end = int(event.exit_bar_ms) + wallet.HOUR
    prepared = split.pair_data(
        pair[pair.tp_r.eq(0)], pair[pair.tp_r.eq(2)], SLOTS,
        start=start, end=end, quote_loader=quote_loader,
    )
    legs, equity = wallet.account(prepared, weights=(TARGET_FRACTION, RUNNER_FRACTION))
    if len(legs) != 2 or set(legs.tp_r.astype(float)) != set(PATH_TP):
        raise ValueError(f"{venue}: mini account failed to fund both original exit legs")
    total_notional = float(legs.notional.sum())
    target_weight = float(legs.loc[legs.tp_r.eq(2), "notional"].sum() / total_notional)
    if not np.isclose(target_weight, TARGET_FRACTION, rtol=1e-9, atol=1e-9):
        raise ValueError(f"{venue}: mini account target weight differs: {target_weight}")
    if not np.isclose(1000.0 + legs.cash_pnl.sum(), equity.nav.iloc[-1], rtol=1e-8, atol=0.01):
        raise ValueError(f"{venue}: mini account cash does not reconcile to marked NAV")
    return {
        "venue": venue,
        "cohort": cohort,
        "event_id": str(event.event_id),
        "symbol": str(event.symbol),
        "entry_ms": start,
        "listing_time_ms": int(event.listing_time_ms),
        "listing_age_days": float(event.listing_age_days),
        "side": int(event.side),
        "funded_legs": int(len(legs)),
        "2r_target_notional_fraction": target_weight,
        "cash_pnl_usdt": float(legs.cash_pnl.sum()),
        "final_nav_usdt": float(equity.nav.iloc[-1]),
        "reconciliation": "pass",
    }


def smoke() -> dict:
    okx_paths, okx_events, okx_meta = read_okx_paths()
    bin_paths, bin_events, bin_meta = read_binance_paths()
    key_check = reconcile_binance_eligible_keys(bin_paths)
    minis = []
    for venue, paths, loader in (
        ("OKX", okx_paths, wallet.quotes),
        ("Binance", bin_paths, repaired.quotes),
    ):
        for age_class in VALID_CLASSES:
            minis.append(_mini_account(venue, paths, age_class, loader))
    return {
        "read_only": True,
        "binance_old_arm_key_check": key_check,
        "listing_counts": {
            "OKX": _listing_counts(okx_events),
            "Binance": _listing_counts(bin_events),
        },
        "metadata": {"OKX": okx_meta, "Binance": bin_meta},
        "mini_accounts": minis,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="Read-only metadata/key and tiny account checks; writes nothing")
    args = parser.parse_args()
    if args.smoke:
        print(json.dumps(smoke(), indent=2, allow_nan=False))
        return
    if (EXP / "summary.json").exists():
        raise FileExistsError(f"Frozen result exists; refusing to overwrite {EXP}")

    paths_by_venue = {}
    events_by_venue = {}
    source_meta = {}
    if not OKX_PATHS.is_file() or not OKX_ATR.is_file():
        raise FileNotFoundError("Frozen OKX source paths or prior ATR source is missing")
    paths_by_venue["OKX"], events_by_venue["OKX"], source_meta["OKX"] = read_okx_paths()
    paths_by_venue["Binance"], events_by_venue["Binance"], source_meta["Binance"] = read_binance_paths()
    binance_key_check = reconcile_binance_eligible_keys(paths_by_venue["Binance"])

    EXP.mkdir(parents=True, exist_ok=True)
    metrics_rows = []
    coverage = {}
    for venue in ("OKX", "Binance"):
        paths = paths_by_venue[venue]
        events = events_by_venue[venue]
        coverage[venue] = _listing_counts(events)
        for cohort in ("all_age", "young_lt_90d"):
            samples, counts = _cohort_samples(paths, cohort)
            coverage[venue][cohort] = counts
            for scenario, pool in samples.items():
                slip = EXTRA_SLIP if scenario == "extra_slippage_5bp" else 0.0
                print(f"Funding {venue} {cohort} {scenario}: {pool.event_id.nunique()} events", flush=True)
                result, positions, equity, account_meta = _account(venue, pool, slip)
                for period, values in result.items():
                    metrics_rows.append(dict(venue=venue, cohort=cohort, scenario=scenario,
                                             period=period, **values))
                coverage[venue].setdefault("accounts", {})[f"{cohort}:{scenario}"] = account_meta
                if scenario == "actual":
                    suffix = "all_age" if cohort == "all_age" else "young_lt_90d"
                    positions.to_csv(EXP / f"funded_{venue}_{suffix}.csv.gz", index_label="event_id")
                    equity.to_csv(EXP / f"hourly_{venue}_{suffix}.csv.gz", index_label="hour_end_utc")
                del positions, equity
                gc.collect()
    metrics = pd.DataFrame(metrics_rows)
    metrics.to_csv(EXP / "account_metrics.csv", index=False)
    audit_events = pd.concat(
        [events_by_venue["OKX"].assign(venue="OKX"), events_by_venue["Binance"].assign(venue="Binance")],
        ignore_index=True,
    )
    audit_events.to_csv(EXP / "listing_audit.csv.gz", index=False)
    summary = {
        "experiment_id": EXP.name,
        "factor": "Actual exchange listing age at entry, 0 <= age < 90 days; no other strategy change.",
        "account": {"starting_usdt": 1000, "slots": SLOTS, "target_fraction": TARGET_FRACTION,
                    "runner_fraction": RUNNER_FRACTION, "extra_market_slippage_per_fill": EXTRA_SLIP},
        "candidate_paths": source_meta,
        "listing_counts": coverage,
        "binance_preeligibility_key_reconciliation": binance_key_check,
        "metadata_limitations": [
            "OKX instrument archive is a dated current-instrument snapshot and does not guarantee delisted records; all-age is limited to symbols with a mapped listTime and nonnegative entry age.",
            "OKX four path tickers lack a mapped USDT swap listTime; pre-listTime ZEC events are excluded as listing/relisting ambiguity. No first-candle proxy is used.",
            "Binance exchangeInfo is a 2026-08-28 snapshot; already-delisted symbols may be absent. Its 632 path symbols all have a USDT perpetual onboardDate in this archive.",
            "Funding, exchange depth/capacity, tick/lot rounding and subsequent delisting behavior are not modeled.",
        ],
        "periods": list(PERIODS),
        "account_metrics": "account_metrics.csv",
        "listing_audit": "listing_audit.csv.gz",
    }
    (EXP / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
    print(metrics[metrics.period.eq("all")].to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
