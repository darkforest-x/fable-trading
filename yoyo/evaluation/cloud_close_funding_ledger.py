"""Attribute Binance USD-M funding cashflows to the frozen efficient-trend fills.

This is a fill-level cashflow sensitivity only. It does not replay or compound
the account NAV. Funding events are sourced from Binance's realized
``fundingRate`` history; settlement mark prices use the response ``markPrice``
when present, otherwise the official Binance mark-price H1 open is explicitly
tagged as a proxy. Each leg retains its actual VectorBT-selected size, side,
entry clock, exit clock, exit phase and TP identity.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.data.altseason_funding import FundingClient, collect
from yoyo.evaluation import cloud_close_efficiency_binance_bounds as efficient
from yoyo.evaluation import monday_breadth_opt_funding as archive_funding


ROOT = efficient.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-funding-ledger-20261109-v1"
CONFIG_PATH = EXP / "config.json"
SOURCE_EXP = ROOT / "experiments/active/exp-cloud-close-efficient-trend-20261109-v1"
SOURCE_CONFIG = SOURCE_EXP / "binance_refinement_config.json"
BASE_DATA = archive_funding.ROOT
CACHE = ROOT / "data/research/cloud_close_funding_ledger_20261109"
HOUR_MS = 3_600_000
FIVE_MIN_MS = 300_000
ENTRY_BOUNDARY_TOLERANCE_MS = 1_000
API_QUERY_ALIGNMENT_PAD_MS = 1_000
INTERVAL_HOURS = (1, 4, 8)
FUNDING_RATE_LIMIT = 1_000
API_MIN_INTERVAL_SECONDS = 0.8
UTC = "UTC"


def _assert_committed() -> None:
    for path in (Path(__file__).resolve(), CONFIG_PATH.resolve()):
        relative = str(path.relative_to(ROOT))
        subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT).returncode:
            raise RuntimeError("Commit the funding-ledger builder/config before producing scope outputs")


def _source_legs() -> tuple[pd.DataFrame, dict]:
    source_config = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    legs, audit = efficient._accounting_legs(source_config)
    required = {
        "event_id", "parent_id", "symbol", "side", "tp_r", "entry_ms", "exit_bar_ms",
        "exit_phase", "entry_price", "exit_price", "signed_size", "notional", "exit_reason",
    }
    missing = required - set(legs.columns)
    if missing:
        raise ValueError(f"Frozen Binance fill legs are missing fields: {sorted(missing)}")
    if legs.duplicated(["event_id", "tp_r"]).any() or not legs.groupby("event_id").size().eq(2).all():
        raise ValueError("Expected exactly two actual funding legs per frozen funded position")
    if legs["is_control"].astype(bool).any():
        raise ValueError("Funding ledger scope accepts funded actual positions only")
    by_event_notional = (legs.assign(child_notional=legs.signed_size.abs() * legs.entry_price)
                         .groupby("event_id", sort=False)
                         .agg(child_notional=("child_notional", "sum"), parent_notional=("notional", "first")))
    if not np.allclose(by_event_notional.child_notional, by_event_notional.parent_notional,
                       rtol=1e-9, atol=1e-6):
        raise ValueError("Child quantity × entry price does not reconcile once to the funded parent notional")
    return legs.copy(), audit


def _validate_config(config: dict) -> None:
    if config["venue"] != "Binance" or int(config["funding_source"]["limit"]) != FUNDING_RATE_LIMIT:
        raise ValueError("Funding-ledger configuration differs from its frozen Binance source contract")
    if int(config["funding_source"]["api_alignment_overfetch_ms"]) != API_QUERY_ALIGNMENT_PAD_MS:
        raise ValueError("Funding API alignment padding differs from the frozen query contract")
    if int(config["cashflow"]["entry_exit_boundary_tolerance_ms"]) != ENTRY_BOUNDARY_TOLERANCE_MS:
        raise ValueError("Funding boundary tolerance differs from the frozen attribution logic")
    if set(config["scopes"]) != {"ada", "all"}:
        raise ValueError("Funding-ledger config must register the ADA and original-account scopes")


def preflight(scope: str = "ada", config_path: Path = CONFIG_PATH) -> dict:
    """Read-only actual-entry preflight for frozen funded and decision tables."""
    config = json.loads(config_path.read_text(encoding="utf-8"))
    _validate_config(config)
    funded = pd.read_csv(efficient.FUNDED, dtype={"event_id": str, "parent_id": str, "symbol": str})
    decisions = pd.read_csv(efficient.DECISIONS, dtype={"event_id": str, "parent_id": str, "symbol": str})
    if funded.event_id.duplicated().any() or decisions.event_id.duplicated().any():
        raise ValueError("Frozen source account files contain duplicate event IDs")
    if len(funded) != int(config["source_account"]["positions"]):
        raise ValueError("Frozen funded position count differs from preregistered source account")
    actual_decisions = decisions.loc[~decisions.is_control.astype(bool)]
    funded_decisions = actual_decisions.loc[actual_decisions.event_id.astype(str).isin(funded.event_id.astype(str))]
    if set(funded_decisions.event_id.astype(str)) != set(funded.event_id.astype(str)):
        raise ValueError("Every frozen funded ID must join exactly one actual decision")
    if scope == "ada":
        focal = config["scopes"]["ada"]
        funded_row = funded.loc[funded.event_id.astype(str).eq(focal["event_id"])]
        decision_row = funded_decisions.loc[funded_decisions.event_id.astype(str).eq(focal["event_id"])]
        if len(funded_row) != 1 or len(decision_row) != 1:
            raise ValueError("ADA focal event did not join one-to-one across frozen funded/decision files")
        row, decision = funded_row.iloc[0], decision_row.iloc[0]
        checks = {
            "parent_id": str(row.parent_id) == focal["event_id"],
            "symbol": row.symbol == focal["symbol"],
            "side": int(row.side) == 1,
            "entry_ms": int(row.entry_ms) == _epoch_ms(focal["entry_utc"]),
            "exit_bar_ms": int(row.exit_bar_ms) == _epoch_ms(focal["exit_bar_utc"]),
            "decision_actual": not bool(decision.is_control),
            "full_runner_selection": decision.choice == "full_runner",
            "notional": bool(np.isclose(float(row.notional), float(focal["expected_funded_notional_usdt"]), atol=1e-6)),
        }
        if not all(checks.values()):
            raise ValueError(f"ADA frozen-entry preflight mismatch: {checks}")
        output = {"scope": scope, "positions": 1, "actual_child_decision": str(decision.choice),
                  "event_id": focal["event_id"], "entry_utc": focal["entry_utc"],
                  "exit_bar_utc": focal["exit_bar_utc"], "funded_notional_usdt": float(row.notional),
                  "symbol_month_windows": len(pd.period_range(
                      pd.Timestamp(focal["entry_utc"]).strftime("%Y-%m"),
                      pd.Timestamp(focal["exit_bar_utc"]).strftime("%Y-%m"), freq="M")),
                  "checks": checks, "api_requests_started": False}
    elif scope == "all":
        output = {"scope": scope, "positions": int(len(funded)),
                  "child_decisions": int(len(actual_decisions)),
                  "symbols": int(funded.symbol.nunique()), "api_requests_started": False}
    else:
        raise ValueError(f"Unsupported funding-ledger scope: {scope}")
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return output


def _select_scope(legs: pd.DataFrame, scope: str, config: dict) -> pd.DataFrame:
    if scope == "all":
        return legs.copy()
    if scope != "ada":
        raise ValueError(f"Unsupported scope: {scope}")
    focal = config["scopes"]["ada"]
    selected = legs.loc[
        legs.symbol.eq(focal["symbol"])
        & legs.entry_ms.eq(int(pd.Timestamp(focal["entry_utc"]).value // 1_000_000))
    ].copy()
    expected_event = focal["event_id"]
    if set(selected.event_id.astype(str)) != {expected_event} or set(selected.tp_r.astype(float)) != {0.0, 2.0}:
        raise ValueError("The frozen ADA focal position did not resolve to its two expected child legs")
    if not selected.exit_bar_ms.eq(int(pd.Timestamp(focal["exit_bar_utc"]).value // 1_000_000)).all():
        raise ValueError("ADA focal exit clock differs from the preregistered native ledger timestamp")
    total_qty = selected.signed_size.abs().sum()
    if not np.isclose(total_qty, float(focal["expected_total_abs_quantity"]), rtol=1e-9, atol=1e-8):
        raise ValueError("ADA focal quantity differs from the expected two-leg native position")
    return selected


def _epoch_ms(value: object) -> int:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(UTC)
    else:
        stamp = stamp.tz_convert(UTC)
    return int(stamp.value // 1_000_000)


def _leg_funding_window(entry_ms: int, exit_bar_ms: int, exit_phase: str) -> tuple[int, int, int]:
    """Return search start, exclusive search end, and phase-specific exit bound."""
    if exit_phase == "open":
        exit_bound = int(exit_bar_ms)
    elif exit_phase in ("close", "intrabar"):
        exit_bound = int(exit_bar_ms) + HOUR_MS
    else:
        raise ValueError(f"Unknown native exit phase: {exit_phase}")
    start = int(entry_ms) - ENTRY_BOUNDARY_TOLERANCE_MS
    # collect() requires second-aligned API windows. Fetch one aligned second
    # past the inclusive attribution tolerance, then trim those extra rows below.
    end_exclusive = exit_bound + ENTRY_BOUNDARY_TOLERANCE_MS + API_QUERY_ALIGNMENT_PAD_MS
    return start, end_exclusive, exit_bound


def _month_windows(legs: pd.DataFrame) -> dict[tuple[str, str], dict]:
    """Construct one bounded query interval for each actually held symbol-month."""
    windows: dict[tuple[str, str], dict] = {}
    for row in legs.itertuples(index=False):
        entry = int(row.entry_ms)
        exit_bar = int(row.exit_bar_ms)
        query_start, query_end, _ = _leg_funding_window(entry, exit_bar, str(row.exit_phase))
        first_month = pd.Timestamp(query_start, unit="ms", tz=UTC).strftime("%Y-%m")
        last_month = pd.Timestamp(query_end - 1, unit="ms", tz=UTC).strftime("%Y-%m")
        for period in pd.period_range(first_month, last_month, freq="M"):
            month = str(period)
            month_start = _epoch_ms(f"{month}-01T00:00:00Z")
            next_month = str(period + 1)
            month_end = _epoch_ms(f"{next_month}-01T00:00:00Z")
            start = max(month_start, query_start)
            end = min(month_end, query_end)
            if start >= end:
                continue
            key = (str(row.symbol), month)
            current = windows.setdefault(key, {"start_ms": start, "end_ms": end,
                                               "position_ids": set(), "exit_phases": set()})
            current["start_ms"] = min(current["start_ms"], start)
            current["end_ms"] = max(current["end_ms"], end)
            current["position_ids"].add(str(row.event_id))
            current["exit_phases"].add(str(row.exit_phase))
    for info in windows.values():
        if info["start_ms"] % 1_000 or info["end_ms"] % 1_000:
            raise ValueError("Funding API windows must remain second-aligned")
    return windows


def _normalized_sources(symbol: str, month: str, start_ms: int, end_ms: int) -> tuple[pd.DataFrame, list[dict]]:
    """Read existing exact API-normalized local history and its range receipts."""
    records, audits = [], []
    candidates = [
        (BASE_DATA / "funding_months" / month / "normalized/binance" / f"{symbol}USDT_funding.csv.gz",
         "monday_breadth_funding_months"),
        (BASE_DATA / "funding_history/normalized/binance" / f"{symbol}USDT_funding.csv.gz",
         "monday_breadth_funding_history"),
        (BASE_DATA / "funding_2026/normalized/binance" / f"{symbol}USDT_funding.csv.gz",
         "monday_breadth_funding_2026"),
    ]
    month_start = _epoch_ms(f"{month}-01T00:00:00Z")
    month_end = _epoch_ms(f"{(pd.Period(month, freq='M') + 1)}-01T00:00:00Z")
    for path, source in candidates:
        if not path.is_file():
            continue
        frame = pd.read_csv(path, keep_default_na=True)
        needed = {"funding_time", "realized_rate", "mark_price"}
        if needed - set(frame.columns):
            raise ValueError(f"Unexpected normalized funding schema: {path}")
        frame["funding_time"] = pd.to_numeric(frame.funding_time, errors="raise").astype("int64")
        frame = frame.loc[(frame.funding_time >= month_start) & (frame.funding_time < month_end)]
        manifest_path = path.with_suffix(".manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        spec = manifest.get("request_spec", {})
        covered = bool(
            manifest.get("api_range_traversed")
            and spec.get("venue") == "binance"
            and spec.get("symbol") == f"{symbol}USDT"
            and int(spec.get("start_ms", 2**63 - 1)) <= start_ms
            and int(spec.get("end_ms", -1)) >= end_ms
            and int(manifest.get("missing_actual_rate_rows", 1)) == 0
        )
        audits.append({
            "path": str(path.relative_to(ROOT)),
            "manifest_path": str(manifest_path.relative_to(ROOT)) if manifest_path.is_file() else None,
            "month_rows": int(len(frame)), "manifest_range_covers_query": covered,
            "missing_mark_price_rows": int(frame.mark_price.isna().sum()),
        })
        for row in frame.itertuples(index=False):
            records.append({
                "funding_time_ms": int(row.funding_time),
                "funding_rate": float(row.realized_rate) if pd.notna(row.realized_rate) else np.nan,
                "mark_price": float(row.mark_price) if pd.notna(row.mark_price) else np.nan,
                "rate_source": source,
                "mark_source": "binance_fundingRate_markPrice" if pd.notna(row.mark_price) else "missing",
                "source_complete": covered,
            })
    return pd.DataFrame(records, columns=[
        "funding_time_ms", "funding_rate", "mark_price", "rate_source", "mark_source", "source_complete",
    ]), audits


def _rate_archive_path(symbol: str, month: str) -> Path | None:
    archive_name = f"{symbol}USDT-fundingRate-{month}.zip"
    shared = ROOT / "experiments/active/exp-monday-1618-flow-20261023-v1/inputs/funding" / f"{symbol}USDT" / archive_name
    local = BASE_DATA / "funding_archives/funding" / f"{symbol}USDT" / archive_name
    if shared.is_file():
        return shared
    if local.is_file():
        return local
    return None


def _mark_archive_path(symbol: str, month: str) -> Path:
    return BASE_DATA / "funding_archives/mark_1h" / f"{symbol}USDT" / f"{symbol}USDT-1h-{month}.zip"


def _archive_rate_rows(symbol: str, month: str, mark_history: pd.DataFrame) -> pd.DataFrame:
    path = _rate_archive_path(symbol, month)
    if path is None:
        return pd.DataFrame()
    with zipfile.ZipFile(path) as archive:
        csvs = [member for member in archive.namelist() if member.lower().endswith(".csv")]
        if len(csvs) != 1:
            raise ValueError(f"Unexpected funding archive members: {path}")
        with archive.open(csvs[0]) as handle:
            frame = pd.read_csv(handle)
    if not {"calc_time", "last_funding_rate"}.issubset(frame.columns):
        raise ValueError(f"Unexpected Binance monthly funding schema: {path}")
    frame["funding_time_ms"] = pd.to_numeric(frame.calc_time, errors="raise").astype("int64")
    frame["funding_rate"] = pd.to_numeric(frame.last_funding_rate, errors="coerce")
    if mark_history is not None and not mark_history.empty:
        mark_index_ms = mark_history.index.as_unit("ms").asi8
        proxy = pd.Series(pd.to_numeric(mark_history.mark_price, errors="coerce").to_numpy(dtype=float),
                          index=mark_index_ms)
        five_min_clock = (frame.funding_time_ms.to_numpy(dtype=np.int64) // FIVE_MIN_MS) * FIVE_MIN_MS
        frame["mark_price"] = proxy.reindex(five_min_clock).to_numpy(dtype=float)
    else:
        frame["mark_price"] = np.nan
    frame["rate_source"] = "binance_vision_monthly_fundingRate"
    frame["mark_source"] = np.where(frame.mark_price.notna(), "binance_vision_mark_1h_open_proxy", "missing")
    frame["source_complete"] = True
    return frame[["funding_time_ms", "funding_rate", "mark_price", "rate_source", "mark_source", "source_complete"]]


def _api_rows(client: FundingClient, symbol: str, month: str, start_ms: int, end_ms: int) -> tuple[pd.DataFrame, dict]:
    key_root = CACHE / "funding_api" / f"{symbol}USDT" / month
    previous_root = client.root
    client.root = key_root
    try:
        manifest = collect(client, f"{symbol}USDT", start_ms, end_ms)
        frame = pd.read_csv(manifest["path"], keep_default_na=True)
    finally:
        client.root = previous_root
    if frame.empty:
        frame = pd.DataFrame(columns=["funding_time", "realized_rate", "mark_price"])
    frame["funding_time_ms"] = pd.to_numeric(frame.funding_time, errors="raise").astype("int64")
    frame["funding_rate"] = pd.to_numeric(frame.realized_rate, errors="coerce")
    frame["mark_price"] = pd.to_numeric(frame.mark_price, errors="coerce")
    frame["rate_source"] = "binance_public_fundingRate_api"
    frame["mark_source"] = np.where(frame.mark_price.notna(), "binance_fundingRate_markPrice", "missing")
    frame["source_complete"] = bool(manifest.get("api_range_traversed"))
    return frame[["funding_time_ms", "funding_rate", "mark_price", "rate_source", "mark_source", "source_complete"]], manifest


def _merge_history(frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=["funding_time_ms", "funding_rate", "mark_price", "rate_source", "mark_source", "source_complete"])
    merged = pd.concat([frame for frame in frames if frame is not None and not frame.empty], ignore_index=True)
    if merged.empty:
        return merged
    merged["funding_time_ms"] = pd.to_numeric(merged.funding_time_ms, errors="raise").astype("int64")
    output = []
    for timestamp, group in merged.groupby("funding_time_ms", sort=True):
        rates = group.funding_rate.dropna().to_numpy(dtype=float)
        if len(rates) > 1 and not np.allclose(rates, rates[0], rtol=0, atol=1e-12):
            raise ValueError(f"Conflicting Binance funding rates at {timestamp}: {rates.tolist()}")
        exact_marks = group.loc[group.mark_source.eq("binance_fundingRate_markPrice") & group.mark_price.notna()]
        proxy_marks = group.loc[group.mark_source.eq("binance_vision_mark_1h_open_proxy") & group.mark_price.notna()]
        if not exact_marks.empty:
            chosen = exact_marks.iloc[0]
            mark, mark_source = float(chosen.mark_price), "binance_fundingRate_markPrice"
        elif not proxy_marks.empty:
            chosen = proxy_marks.iloc[0]
            mark, mark_source = float(chosen.mark_price), "binance_vision_mark_1h_open_proxy"
        else:
            chosen = group.loc[group.funding_rate.notna()].iloc[0] if group.funding_rate.notna().any() else group.iloc[0]
            mark, mark_source = np.nan, "missing"
        rate_source = str(chosen.rate_source)
        output.append({
            "funding_time_ms": int(timestamp),
            "funding_rate": float(rates[0]) if len(rates) else np.nan,
            "mark_price": mark,
            "rate_source": rate_source,
            "mark_source": mark_source,
            "source_complete": bool(group.source_complete.astype(bool).any()),
        })
    return pd.DataFrame(output)


def _interval_profile(frame: pd.DataFrame) -> dict:
    if len(frame) < 2:
        return {"settlements": int(len(frame)), "intervals": 0, "hours_1": 0, "hours_4": 0,
                "hours_8": 0, "other_hours": 0, "observed_interval_hours": []}
    times = np.sort(frame.funding_time_ms.to_numpy(dtype=np.int64))
    delta_hours = np.diff(times) / 3_600_000.0
    counts = {h: int(np.isclose(delta_hours, h, atol=0.01).sum()) for h in INTERVAL_HOURS}
    other = int(len(delta_hours) - sum(counts.values()))
    observed = sorted(set(float(round(value, 3)) for value in delta_hours))
    return {"settlements": int(len(frame)), "intervals": int(len(delta_hours)),
            "hours_1": counts[1], "hours_4": counts[4], "hours_8": counts[8],
            "other_hours": other, "observed_interval_hours": observed}


def _window_history(
    symbol: str,
    month: str,
    window: dict,
    client: FundingClient,
    *,
    allow_api: bool,
) -> tuple[pd.DataFrame, dict]:
    start_ms, end_ms = int(window["start_ms"]), int(window["end_ms"])
    normalized, normalized_audits = _normalized_sources(symbol, month, start_ms, end_ms)
    normalized_cover = bool(normalized_audits) and any(x["manifest_range_covers_query"] for x in normalized_audits)
    api_request = None
    monthly_history = pd.DataFrame()
    need_monthly = normalized.empty or normalized.mark_price.isna().any()
    if need_monthly:
        monthly_history = archive_funding.archive_month(symbol + "USDT", month, local_only=True)

    local_archive_path = _rate_archive_path(symbol, month)
    archive_complete = local_archive_path is not None
    archive_rows = _archive_rate_rows(symbol, month, monthly_history) if archive_complete else pd.DataFrame()

    exact_local_cover = (normalized_cover and normalized.funding_rate.notna().all()
                         and normalized.mark_price.notna().all())
    if exact_local_cover:
        selected_base = normalized
        base_source = "existing_normalized_binance_funding_api"
    elif archive_complete and not archive_rows.empty:
        selected_base = archive_rows
        base_source = "existing_binance_vision_monthly_fundingRate"
    else:
        selected_base = pd.DataFrame()
        base_source = "none"

    should_query_api = allow_api and not exact_local_cover
    if should_query_api:
        try:
            api, api_request = _api_rows(client, symbol, month, start_ms, end_ms)
            if not api.empty:
                selected_base = _merge_history([selected_base, api])
                base_source = "binance_public_fundingRate_api"
            elif selected_base.empty:
                base_source = "api_range_empty"
            else:
                base_source = base_source + "_api_empty_fallback"
        except Exception as exc:  # Preserve the local fallback and expose the failed request.
            api_request = {"error": f"{type(exc).__name__}: {str(exc)[:500]}",
                           "symbol": symbol + "USDT", "month": month,
                           "start_ms": start_ms, "end_ms": end_ms}

    if not selected_base.empty and selected_base.mark_price.isna().any():
        # archive_month reuses local official inputs and fetches only the required
        # rate/mark month plus daily mark hours missing from that monthly archive.
        current_proxy = monthly_history.mark_price.notna().sum() if not monthly_history.empty else 0
        if current_proxy == 0 or archive_complete:
            monthly_history = archive_funding.archive_month(symbol + "USDT", month, local_only=False)
        local_archive_path = _rate_archive_path(symbol, month)
        archive_complete = local_archive_path is not None
        if archive_complete:
            archive_rows = _archive_rate_rows(symbol, month, monthly_history)

    if selected_base.empty and archive_complete:
        selected_base = archive_rows
        base_source = "binance_vision_monthly_fundingRate"
    elif not selected_base.empty and not archive_rows.empty:
        selected_base = _merge_history([selected_base, archive_rows])

    selected_base = selected_base.copy()
    if not selected_base.empty:
        selected_base = selected_base.loc[
            (selected_base.funding_time_ms >= start_ms) & (selected_base.funding_time_ms < end_ms)
        ].copy()
        selected_base = selected_base.sort_values("funding_time_ms").drop_duplicates("funding_time_ms")
    profile = _interval_profile(selected_base) if not selected_base.empty else _interval_profile(pd.DataFrame(columns=["funding_time_ms"]))
    missing_rate_rows = int(selected_base.funding_rate.isna().sum()) if not selected_base.empty else 0
    missing_mark_rows = int(selected_base.mark_price.isna().sum()) if not selected_base.empty else 0
    range_complete = bool(
        exact_local_cover or archive_complete
        or (api_request is not None and api_request.get("api_range_traversed", False))
    )
    audit = {
        "symbol": symbol + "USDT", "month": month, "query_start_ms": start_ms, "query_end_exclusive_ms": end_ms,
        "position_ids": sorted(window["position_ids"]), "exit_phases": sorted(window["exit_phases"]),
        "source": base_source,
        "normalized_sources": normalized_audits, "monthly_rate_archive": str(local_archive_path.relative_to(ROOT)) if local_archive_path and local_archive_path.is_relative_to(ROOT) else str(local_archive_path) if local_archive_path else None,
        "monthly_mark_1h_archive": str(_mark_archive_path(symbol, month).relative_to(ROOT)) if _mark_archive_path(symbol, month).is_relative_to(ROOT) else str(_mark_archive_path(symbol, month)),
        "monthly_mark_1h_archive_exists": _mark_archive_path(symbol, month).is_file(),
        "monthly_mark_1h_archive_bytes": _mark_archive_path(symbol, month).stat().st_size if _mark_archive_path(symbol, month).is_file() else None,
        "monthly_mark_proxy_rows_available": int(monthly_history.mark_price.notna().sum()) if not monthly_history.empty else 0,
        "funding_settlement_rows_in_query_window": int(len(selected_base)), "missing_rate_rows": missing_rate_rows,
        "missing_mark_rows": missing_mark_rows, "range_complete": range_complete,
        "api_request_manifest": api_request,
        "interval_profile": profile,
        "proxy_policy": "official Binance markPriceKlines 1h open, joined at the fundingTime hour; explicit proxy flag",
    }
    return selected_base, audit


def _funding_attribution(legs: pd.DataFrame, histories: dict[tuple[str, str], pd.DataFrame],
                         month_audits: dict[tuple[str, str], dict]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    detail: list[dict] = []
    position_rows: list[dict] = []
    for leg in legs.itertuples(index=False):
        start_ms, exit_ms = int(leg.entry_ms), int(leg.exit_bar_ms)
        query_start_ms, query_end_ms, exit_bound_ms = _leg_funding_window(
            start_ms, exit_ms, str(leg.exit_phase),
        )
        months = [str(m) for m in pd.period_range(
            pd.Timestamp(query_start_ms, unit="ms", tz=UTC).strftime("%Y-%m"),
            pd.Timestamp(query_end_ms - 1, unit="ms", tz=UTC).strftime("%Y-%m"), freq="M")]
        missing_months = []
        event_frames = []
        for month in months:
            key = (str(leg.symbol), month)
            audit = month_audits.get(key)
            if audit is None or not audit["range_complete"]:
                missing_months.append(month)
            history = histories.get(key, pd.DataFrame())
            if history.empty:
                continue
            hit = history.loc[
                (history.funding_time_ms >= query_start_ms)
                & (history.funding_time_ms <= exit_bound_ms + ENTRY_BOUNDARY_TOLERANCE_MS)
            ].copy()
            if not hit.empty:
                event_frames.append(hit)
        settlement_rows = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
        if not settlement_rows.empty:
            settlement_rows = settlement_rows.drop_duplicates("funding_time_ms").sort_values("funding_time_ms")
        deterministic = 0.0
        boundary_low = 0.0
        boundary_high = 0.0
        unknown_mark = 0
        known_events = 0
        boundary_events = 0
        proxy_events = 0
        if not settlement_rows.empty:
            for event in settlement_rows.itertuples(index=False):
                funding_time = int(event.funding_time_ms)
                has_rate = pd.notna(event.funding_rate)
                has_mark = pd.notna(event.mark_price) and float(event.mark_price) > 0
                entry_boundary = abs(funding_time - start_ms) <= ENTRY_BOUNDARY_TOLERANCE_MS
                phase = str(leg.exit_phase)
                if phase == "open":
                    exit_boundary = abs(funding_time - exit_ms) <= ENTRY_BOUNDARY_TOLERANCE_MS
                elif phase == "close":
                    exit_boundary = abs(funding_time - exit_bound_ms) <= ENTRY_BOUNDARY_TOLERANCE_MS
                else:
                    # Exit occurs at an unknown instant in this H1 bar. A settlement
                    # anywhere from its open through close may precede or follow it.
                    exit_boundary = (exit_ms - ENTRY_BOUNDARY_TOLERANCE_MS
                                     <= funding_time <= exit_bound_ms + ENTRY_BOUNDARY_TOLERANCE_MS)
                boundary = entry_boundary or exit_boundary
                cashflow = (-float(leg.signed_size) * float(event.mark_price) * float(event.funding_rate)
                            if has_rate and has_mark else np.nan)
                if not np.isfinite(cashflow):
                    unknown_mark += 1
                else:
                    known_events += 1
                    if event.mark_source == "binance_vision_mark_1h_open_proxy":
                        proxy_events += 1
                    if boundary:
                        boundary_events += 1
                        boundary_low += min(0.0, cashflow)
                        boundary_high += max(0.0, cashflow)
                    else:
                        deterministic += cashflow
                detail.append({
                    "event_id": str(leg.event_id), "parent_id": str(leg.parent_id), "symbol": str(leg.symbol),
                    "tp_r": float(leg.tp_r), "side": int(leg.side), "signed_quantity": float(leg.signed_size),
                    "quantity_abs": abs(float(leg.signed_size)), "entry_ms": start_ms, "exit_bar_ms": exit_ms,
                    "exit_phase": str(leg.exit_phase), "exit_reason": str(leg.exit_reason),
                    "funding_time_ms": funding_time,
                    "funding_time_utc": pd.Timestamp(funding_time, unit="ms", tz=UTC).isoformat(),
                    "funding_rate": float(event.funding_rate) if has_rate else np.nan,
                    "mark_price": float(event.mark_price) if has_mark else np.nan,
                    "rate_source": str(event.rate_source), "mark_source": str(event.mark_source),
                    "funding_cashflow_usdt": cashflow,
                    "cashflow_low_usdt": min(0.0, cashflow) if boundary and np.isfinite(cashflow) else cashflow,
                    "cashflow_high_usdt": max(0.0, cashflow) if boundary and np.isfinite(cashflow) else cashflow,
                    "boundary_ambiguous": bool(boundary),
                    "boundary_reason": ("entry_and_exit_settlement_window" if entry_boundary and exit_boundary
                                        else "entry_settlement_coincidence" if entry_boundary
                                        else "intrabar_exit_hour_order_unknown" if phase == "intrabar" and exit_boundary
                                        else "exit_settlement_coincidence" if exit_boundary else "interior"),
                    "cashflow_known": bool(np.isfinite(cashflow)),
                })
        complete = not missing_months and unknown_mark == 0
        known_total = deterministic + boundary_low
        known_upper = deterministic + boundary_high
        position_rows.append({
            "event_id": str(leg.event_id), "parent_id": str(leg.parent_id), "symbol": str(leg.symbol),
            "tp_r": float(leg.tp_r), "side": int(leg.side), "signed_quantity": float(leg.signed_size),
            "quantity_abs": abs(float(leg.signed_size)),
            "notional_usdt": abs(float(leg.signed_size)) * float(leg.entry_price),
            "entry_ms": start_ms, "entry_time_utc": pd.Timestamp(start_ms, unit="ms", tz=UTC).isoformat(),
            "exit_bar_ms": exit_ms, "exit_bar_open_utc": pd.Timestamp(exit_ms, unit="ms", tz=UTC).isoformat(),
            "exit_window_end_ms": exit_bound_ms,
            "exit_window_end_utc": pd.Timestamp(exit_bound_ms, unit="ms", tz=UTC).isoformat(),
            "exit_phase": str(leg.exit_phase), "exit_reason": str(leg.exit_reason),
            "funding_months": "|".join(months), "missing_months": "|".join(missing_months),
            "settlement_count": int(len(settlement_rows)), "known_settlement_count": known_events,
            "unknown_mark_or_rate_count": unknown_mark, "boundary_settlement_count": boundary_events,
            "proxy_mark_count": proxy_events, "known_cashflow_usdt": deterministic,
            "known_cashflow_lower_usdt": known_total, "known_cashflow_upper_usdt": known_upper,
            "funding_cashflow_lower_usdt": known_total if complete else np.nan,
            "funding_cashflow_upper_usdt": known_upper if complete else np.nan,
            "funding_complete_for_observed_source": complete,
            "interpretation": "Funding cashflow only; no account NAV or reinvestment replay",
        })
    pos = pd.DataFrame(position_rows)
    detail_frame = pd.DataFrame(detail)
    events = []
    for event_id, group in pos.groupby("event_id", sort=False):
        row = group.iloc[0]
        complete = bool(group.funding_complete_for_observed_source.all())
        events.append({
            "event_id": event_id, "parent_id": row.parent_id, "symbol": row.symbol,
            "side": int(row.side), "entry_ms": int(row.entry_ms), "exit_bar_ms": int(row.exit_bar_ms),
            "exit_phase": row.exit_phase, "exit_window_end_ms": int(row.exit_window_end_ms),
            "child_legs": int(len(group)),
            "funded_notional_usdt": float(group.notional_usdt.sum()),
            "settlement_count": int(group.settlement_count.sum()),
            "known_cashflow_usdt": float(group.known_cashflow_usdt.sum()),
            "known_cashflow_lower_usdt": float(group.known_cashflow_lower_usdt.sum()),
            "known_cashflow_upper_usdt": float(group.known_cashflow_upper_usdt.sum()),
            "funding_cashflow_lower_usdt": float(group.funding_cashflow_lower_usdt.sum()) if complete else np.nan,
            "funding_cashflow_upper_usdt": float(group.funding_cashflow_upper_usdt.sum()) if complete else np.nan,
            "unknown_child_legs": int((~group.funding_complete_for_observed_source).sum()),
            "boundary_settlement_count": int(group.boundary_settlement_count.sum()),
            "proxy_mark_count": int(group.proxy_mark_count.sum()),
            "funding_complete_for_observed_source": complete,
        })
    return detail_frame, pos, pd.DataFrame(events)


def _schedule_table(histories: dict[tuple[str, str], pd.DataFrame], month_audits: dict[tuple[str, str], dict]) -> pd.DataFrame:
    rows = []
    for key in sorted(set(histories) | set(month_audits)):
        frame = histories.get(key, pd.DataFrame())
        profile = _interval_profile(frame) if not frame.empty else _interval_profile(pd.DataFrame(columns=["funding_time_ms"]))
        audit = month_audits.get(key, {})
        rows.append({"symbol": key[0] + "USDT", "month": key[1], **profile,
                     "source": audit.get("source"), "range_complete": audit.get("range_complete", False),
                     "missing_mark_rows": audit.get("missing_mark_rows", 0),
                     "query_start_ms": audit.get("query_start_ms"), "query_end_exclusive_ms": audit.get("query_end_exclusive_ms")})
    return pd.DataFrame(rows)


def _symbol_summary(position_rows: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for symbol, group in position_rows.groupby("symbol", sort=True):
        complete = bool(group.funding_complete_for_observed_source.all())
        rows.append({
            "symbol": symbol, "funded_events": int(group.event_id.nunique()), "child_legs": int(len(group)),
            "funded_notional_usdt_sum": float(group.notional_usdt.sum()),
            "signed_quantity_abs_sum": float(group.quantity_abs.sum()),
            "settlement_rows": int(group.settlement_count.sum()),
            "boundary_settlements": int(group.boundary_settlement_count.sum()),
            "proxy_mark_rows": int(group.proxy_mark_count.sum()),
            "unknown_rows_or_months": int((~group.funding_complete_for_observed_source).sum()),
            "known_cashflow_usdt": float(group.known_cashflow_usdt.sum()),
            "funding_cashflow_lower_usdt": float(group.funding_cashflow_lower_usdt.sum()) if complete else np.nan,
            "funding_cashflow_upper_usdt": float(group.funding_cashflow_upper_usdt.sum()) if complete else np.nan,
            "fully_covered": complete,
        })
    return pd.DataFrame(rows)


def _write_scope(scope: str, config: dict, legs: pd.DataFrame, source_audit: dict, *,
                 experiment_dir: Path = EXP, source_experiment: str | None = None,
                 source_strategy: str | None = None) -> dict:
    output_dir = experiment_dir / scope
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "funding_audit.json"
    if audit_path.exists():
        raise FileExistsError(f"Funding scope output already exists: {audit_path}")
    windows = _month_windows(legs)
    client = FundingClient("binance", CACHE / "funding_api")
    histories: dict[tuple[str, str], pd.DataFrame] = {}
    month_audits: dict[tuple[str, str], dict] = {}
    for i, (key, window) in enumerate(sorted(windows.items()), 1):
        symbol, month = key
        frame, audit = _window_history(symbol, month, window, client, allow_api=True)
        histories[key] = frame
        month_audits[key] = audit
        if i % 100 == 0:
            print(f"Funding sources {scope}: {i}/{len(windows)} symbol-month windows", flush=True)
    detail, positions, events = _funding_attribution(legs, histories, month_audits)
    schedule = _schedule_table(histories, month_audits)
    symbols = _symbol_summary(positions)
    detail_path = output_dir / "settlement_flows.csv.gz"
    positions_path = output_dir / "leg_funding.csv.gz"
    events_path = output_dir / "position_funding.csv.gz"
    symbols_path = output_dir / "symbol_funding.csv"
    schedule_path = output_dir / "funding_schedule.csv"
    month_audit_path = output_dir / "symbol_month_audit.json"
    detail.to_csv(detail_path, index=False, compression={"method": "gzip", "mtime": 0})
    positions.to_csv(positions_path, index=False, compression={"method": "gzip", "mtime": 0})
    events.to_csv(events_path, index=False, compression={"method": "gzip", "mtime": 0})
    symbols.to_csv(symbols_path, index=False)
    schedule.to_csv(schedule_path, index=False)
    month_audit = [month_audits[k] for k in sorted(month_audits)]
    tmp_audit = month_audit_path.with_suffix(".tmp")
    tmp_audit.write_text(json.dumps(month_audit, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp_audit.replace(month_audit_path)
    data_complete = bool(positions.funding_complete_for_observed_source.all()) if len(positions) else False
    total = float(events.known_cashflow_usdt.sum()) if len(events) else 0.0
    lower = float(events.funding_cashflow_lower_usdt.sum()) if data_complete else None
    upper = float(events.funding_cashflow_upper_usdt.sum()) if data_complete else None
    api_calls = [a.get("api_request_manifest") for a in month_audit if isinstance(a.get("api_request_manifest"), dict)]
    request_pages = sum(len(x.get("pages", [])) for x in api_calls)
    errors = [a for a in month_audit if isinstance(a.get("api_request_manifest"), dict) and a["api_request_manifest"].get("error")]
    missing_months = [a for a in month_audit if not a.get("range_complete")]
    output = {
        "id": config["id"], "scope": scope, "venue": "Binance USD-M",
        "source_experiment": source_experiment or config["source_account"]["experiment"],
        "source_strategy": source_strategy or "trend_efficiency actual funded positions; frozen leg paths and quantities",
        "source_reconciliation": source_audit,
        "cashflow_only_not_nav_replay": True,
        "funding_cashflow_sign": "-signed_quantity * funding_rate * mark_price; positive rate charges longs and credits shorts",
        "range_boundary_policy": "Entry and open-exit ±1s coincidences are bounded by include/exclude; close exits use bar-close time, and intrabar exits bound settlements across the full H1 exit bar.",
        "mark_price_policy": "Use fundingRate.markPrice if present; else official Binance markPriceKlines 1h open as a proxy; missing rates or prices remain unknown, never zero-filled.",
        "schedule_policy": "Use observed fundingTime rows; no fixed 1h/4h/8h schedule is assumed. Interval diagnostics are reported per symbol-month.",
        "source_legs": int(len(legs)), "funded_positions": int(legs.event_id.nunique()),
        "symbols": int(legs.symbol.nunique()), "held_symbol_month_windows": int(len(windows)),
        "funding_settlement_rows": int(len(detail)), "position_rows": int(len(events)),
        "covered_position_legs": int(positions.funding_complete_for_observed_source.sum()),
        "incomplete_position_legs": int((~positions.funding_complete_for_observed_source).sum()),
        "unknown_month_windows": int(len(missing_months)), "api_queries": len(api_calls),
        "api_pages": int(request_pages), "api_query_errors": len(errors),
        "known_observed_cashflow_usdt": total,
        "funding_cashflow_lower_usdt": lower, "funding_cashflow_upper_usdt": upper,
        "account_nav_recomputed": False,
        "outputs": {"settlement_flows": str(detail_path.relative_to(ROOT)),
                    "leg_funding": str(positions_path.relative_to(ROOT)),
                    "position_funding": str(events_path.relative_to(ROOT)),
                    "symbol_funding": str(symbols_path.relative_to(ROOT)),
                    "funding_schedule": str(schedule_path.relative_to(ROOT)),
                    "symbol_month_audit": str(month_audit_path.relative_to(ROOT))},
        "unresolved_months": [{"symbol": a["symbol"], "month": a["month"],
                               "api_error": (a.get("api_request_manifest") or {}).get("error"),
                               "source": a.get("source")} for a in missing_months],
        "ada_focal_position": config["scopes"]["ada"] if scope == "ada" else None,
    }
    tmp = audit_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(audit_path)
    print(json.dumps({"scope": scope, "positions": output["funded_positions"],
                      "legs": output["source_legs"], "symbol_months": len(windows),
                      "settlements": len(detail), "complete_legs": output["covered_position_legs"],
                      "unknown_legs": output["incomplete_position_legs"],
                      "funding_lower_usdt": lower, "funding_upper_usdt": upper,
                      "audit": str(audit_path.relative_to(ROOT))}, indent=2, allow_nan=False), flush=True)
    return output


def _cashflow_interval(signed_quantity: float, rate: float, mark: float, boundary: bool) -> tuple[float, float]:
    cash = -signed_quantity * rate * mark
    return (min(0.0, cash), max(0.0, cash)) if boundary else (cash, cash)


def mini_check() -> None:
    """Check long/short signs, coincidence bounds, and the actual ADA frozen path join."""
    long_lo, long_hi = _cashflow_interval(2.0, 0.001, 100.0, False)
    short_lo, short_hi = _cashflow_interval(-2.0, 0.001, 100.0, False)
    entry_lo, entry_hi = _cashflow_interval(2.0, 0.001, 100.0, True)
    if (long_lo, long_hi) != (-0.2, -0.2) or (short_lo, short_hi) != (0.2, 0.2):
        raise AssertionError("Funding cashflow sign is wrong for long/short positions")
    if (entry_lo, entry_hi) != (-0.2, 0.0):
        raise AssertionError("Funding coincidence must preserve adverse include/exclude bounds")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    legs, _ = _source_legs()
    ada = _select_scope(legs, "ada", config)
    if len(ada) != 2 or not ada.exit_phase.eq("intrabar").all():
        raise AssertionError("Focal ADA actual VectorBT leg identity/phase did not match")
    if not np.isclose(ada.signed_size.abs().sum(), 931.238492, atol=1e-5):
        raise AssertionError("Focal ADA child quantities changed")
    child_notional = float((ada.signed_size.abs() * ada.entry_price).sum())
    if not np.isclose(child_notional, float(config["scopes"]["ada"]["expected_funded_notional_usdt"]), atol=1e-6):
        raise AssertionError("Child-leg notional must aggregate once to the funded parent notional")
    ada_windows = _month_windows(ada)
    if len(ada_windows) != 6:
        raise AssertionError("Focal ADA holding should resolve to six actual funding month windows")
    first_month = ada_windows[("ADA", "2020-11")]
    last_month = ada_windows[("ADA", "2021-04")]
    if (first_month["start_ms"] != int(ada.entry_ms.iloc[0]) - ENTRY_BOUNDARY_TOLERANCE_MS
            or last_month["end_ms"] != int(ada.exit_bar_ms.iloc[0]) + HOUR_MS
            + ENTRY_BOUNDARY_TOLERANCE_MS + API_QUERY_ALIGNMENT_PAD_MS):
        raise AssertionError("Funding queries must include the full ±1s settlement boundary range")
    intervals = pd.DataFrame([
        {"signed_size": 2.0, "rate": 0.001, "mark": 100.0, "entry": 1_000, "exit": 3_000},
        {"signed_size": -2.0, "rate": 0.001, "mark": 100.0, "entry": 1_000, "exit": 3_000},
    ])
    vals = [_cashflow_interval(r.signed_size, r.rate, r.mark, False) for r in intervals.itertuples()]
    if sum(v[0] for v in vals) != 0.0 or sum(v[1] for v in vals) != 0.0:
        raise AssertionError("Offsetting simultaneous long/short cashflows should cancel")
    entry_ms = _epoch_ms("2024-01-01T00:00:00Z")
    exit_ms = entry_ms + 16 * HOUR_MS
    synthetic_leg = pd.DataFrame([{
        "event_id": "mini", "parent_id": "mini-parent", "symbol": "ADA", "tp_r": 0.0,
        "side": 1, "signed_size": 2.0, "notional": 100.0, "entry_price": 50.0, "entry_ms": entry_ms,
        "exit_bar_ms": exit_ms, "exit_phase": "open", "exit_reason": "forced_open",
    }])
    timestamps = [entry_ms, entry_ms + 8 * HOUR_MS, exit_ms]
    synthetic_history = pd.DataFrame({
        "funding_time_ms": timestamps, "funding_rate": [0.001, 0.001, 0.001],
        "mark_price": [100.0, 100.0, 100.0],
        "rate_source": ["mini"] * 3,
        "mark_source": ["binance_vision_mark_1h_open_proxy"] * 3,
        "source_complete": [True] * 3,
    })
    key = ("ADA", "2024-01")
    audit = {key: {"range_complete": True}, ("ADA", "2023-12"): {"range_complete": True}}
    detail, pos, event_rows = _funding_attribution(synthetic_leg, {key: synthetic_history}, audit)
    expected = pos.iloc[0]
    if (len(detail) != 3 or expected.settlement_count != 3 or expected.boundary_settlement_count != 2
            or not np.isclose(expected.funding_cashflow_lower_usdt, -0.6)
            or not np.isclose(expected.funding_cashflow_upper_usdt, -0.2)
            or not np.isclose(event_rows.iloc[0].funding_cashflow_lower_usdt, -0.6)):
        raise AssertionError("Settlement-boundary cashflow bounds do not match the synthetic event path")
    missing_history = synthetic_history.copy()
    missing_history.loc[1, ["mark_price", "mark_source"]] = [np.nan, "missing"]
    _, missing_pos, _ = _funding_attribution(synthetic_leg, {key: missing_history}, audit)
    if bool(missing_pos.iloc[0].funding_complete_for_observed_source) or pd.notna(missing_pos.iloc[0].funding_cashflow_lower_usdt):
        raise AssertionError("Missing settlement marks must remain unknown rather than become zero")

    jan_entry = _epoch_ms("2024-01-31T22:00:00Z")
    jan_exit_bar = _epoch_ms("2024-01-31T23:00:00Z")
    cross_close_leg = pd.DataFrame([{
        "event_id": "month-close", "parent_id": "month-close", "symbol": "ADA", "tp_r": 0.0,
        "side": 1, "signed_size": 2.0, "notional": 100.0, "entry_price": 50.0, "entry_ms": jan_entry,
        "exit_bar_ms": jan_exit_bar, "exit_phase": "close", "exit_reason": "ema_flip",
    }])
    close_windows = _month_windows(cross_close_leg)
    if set(close_windows) != {("ADA", "2024-01"), ("ADA", "2024-02")}:
        raise AssertionError("Close-phase exit must enumerate the month containing the actual H1 close")
    feb_settlement = _epoch_ms("2024-02-01T00:00:00Z") + 5
    feb_history = pd.DataFrame([{
        "funding_time_ms": feb_settlement, "funding_rate": 0.001, "mark_price": 100.0,
        "rate_source": "mini", "mark_source": "binance_fundingRate_markPrice", "source_complete": True,
    }, {
        # In the second-aligned API fetch envelope, but beyond the actual ±1s
        # close tolerance; attribution must discard it.
        "funding_time_ms": feb_settlement + 1_500, "funding_rate": 0.001, "mark_price": 100.0,
        "rate_source": "mini", "mark_source": "binance_fundingRate_markPrice", "source_complete": True,
    }])
    _, close_pos, _ = _funding_attribution(
        cross_close_leg, {("ADA", "2024-01"): pd.DataFrame(), ("ADA", "2024-02"): feb_history},
        {key: {"range_complete": True} for key in close_windows},
    )
    if (close_pos.iloc[0].settlement_count != 1 or close_pos.iloc[0].boundary_settlement_count != 1
            or not np.isclose(close_pos.iloc[0].funding_cashflow_lower_usdt, -0.2)):
        raise AssertionError("Close-phase month-boundary settlement was not included conservatively")

    intrabar_leg = cross_close_leg.assign(exit_phase="intrabar")
    intrabar_event = jan_exit_bar + 2_000
    intrabar_history = pd.DataFrame([{
        "funding_time_ms": intrabar_event, "funding_rate": 0.001, "mark_price": 100.0,
        "rate_source": "mini", "mark_source": "binance_fundingRate_markPrice", "source_complete": True,
    }, {
        "funding_time_ms": feb_settlement + 1_500, "funding_rate": 0.001, "mark_price": 100.0,
        "rate_source": "mini", "mark_source": "binance_fundingRate_markPrice", "source_complete": True,
    }])
    _, intrabar_pos, _ = _funding_attribution(
        intrabar_leg, {("ADA", "2024-01"): intrabar_history, ("ADA", "2024-02"): feb_history},
        {key: {"range_complete": True} for key in _month_windows(intrabar_leg)},
    )
    if (intrabar_pos.iloc[0].settlement_count != 2 or intrabar_pos.iloc[0].boundary_settlement_count != 2
            or not np.isclose(intrabar_pos.iloc[0].funding_cashflow_lower_usdt, -0.4)):
        raise AssertionError("Intrabar exit must bound settlements throughout its H1 bar")
    print("Binance efficient-trend funding-ledger mini-check: PASS")


def run(scope: str, config_path: Path = CONFIG_PATH) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    _validate_config(config)
    _assert_committed()
    full_legs, source_audit = _source_legs()
    legs = _select_scope(full_legs, scope, config)
    return _write_scope(scope, config, legs, source_audit)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=("ada", "all"), default="all")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--mini-check", action="store_true")
    parser.add_argument("--preflight", action="store_true", help="Read frozen funded/decision files without API requests")
    args = parser.parse_args()
    if args.mini_check:
        mini_check()
        return
    if args.preflight:
        preflight(args.scope, args.config.resolve())
        return
    run(args.scope, args.config.resolve())


if __name__ == "__main__":
    main()
