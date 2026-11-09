"""Replay the extended Binance cloud account with settlement-time funding.

The parent account owns the frozen event roster and native H1 paths. This
adapter only adds observed Binance USD-M funding settlements to the existing
four-stage VectorBT hourly account; it never synthesizes orders or fills.
"""
from __future__ import annotations

import argparse
import gc
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_funding_ledger as ledger
from yoyo.evaluation import cloud_close_hourly_trim_funding as hourly
from yoyo.evaluation import cloud_close_november_account as november
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.data.altseason_funding import FundingClient

ROOT = wallet.ROOT
EXP = ROOT / "experiments/active/exp-cloud-close-november-funding-20261109-v1"
CONFIG_PATH = EXP / "config.json"
ACCOUNT_EXP = november.EXP
OLD_FUNDING_EXP = ROOT / "experiments/active/exp-cloud-close-hourly-trim-funding-20261109-v1"
OLD_TAPE = OLD_FUNDING_EXP / "funding_tape.csv.gz"
OLD_MONTHS = OLD_FUNDING_EXP / "source_months.csv.gz"
OLD_COVERAGE = OLD_FUNDING_EXP / "source_coverage.json"
HOUR = wallet.HOUR
TAKER = wallet.TAKER
MAKER = 0.0002


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
    temporary.replace(path)


def _write_frame(path: Path, frame: pd.DataFrame, **kwargs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    compression = {"method": "gzip", "mtime": 0} if path.suffix == ".gz" else None
    frame.to_csv(temporary, compression=compression, **kwargs)
    temporary.replace(path)


def _bool_column(series: pd.Series, name: str) -> pd.Series:
    if series.dtype == bool:
        return series
    values = series.astype(str).str.strip().str.lower().map(
        {"true": True, "false": False, "1": True, "0": False}
    )
    if values.isna().any():
        raise ValueError(f"Invalid boolean values in {name}")
    return values.astype(bool)


def _validate_config(config: dict) -> None:
    if config.get("id") != EXP.name or config.get("venue") != "Binance USD-M":
        raise ValueError("November funding config identity or venue changed")
    account = config["account"]
    if (int(account["slots"]) != 9
            or not np.isclose(float(account["initial_cash_usdt"]), 1000.0)
            or not np.isclose(float(account["new_order_exposure_fraction"]), 0.9)):
        raise ValueError("The extended nine-slot account sizing contract changed")
    if config["hourly_management"]["review_hours"] != 1 or not np.isclose(
        float(config["hourly_management"]["single_contract_weight_cap"]), 0.2
    ):
        raise ValueError("This adapter requires the frozen hourly 20% contract cap")
    if config["boundary_policy"]["timestamp_resolution"] != "Exact source milliseconds; no timestamp tolerance":
        raise ValueError("Funding boundaries must use exact source milliseconds")
    if config["network_policy"] != (
        "Only official Binance USD-M funding and mark archives or public fundingRate history "
        "for uncovered held windows; no market-price collection in this adapter"
    ):
        raise ValueError("Funding source policy changed")
    if config["funding_source"]["old_source_experiment"] != OLD_FUNDING_EXP.name:
        raise ValueError("The previously covered funding source must remain frozen")
    if config["outputs"]["scope"] != f"experiments/active/{EXP.name}/account/":
        raise ValueError("Extended funding account output must remain isolated")
    expected_inputs = {
        "funded_legs": f"experiments/active/{ACCOUNT_EXP.name}/funded_legs.csv.gz",
        "extension_root": "data/research/cloud_close_november_paths",
        "old_funding_tape": str(OLD_TAPE.relative_to(ROOT)),
        "old_source_months": str(OLD_MONTHS.relative_to(ROOT)),
        "old_source_coverage": str(OLD_COVERAGE.relative_to(ROOT)),
    }
    if any(config["inputs"].get(key) != value for key, value in expected_inputs.items()):
        raise ValueError("Frozen November account/funding input paths changed")


def _assert_committed() -> None:
    for path in (Path(__file__).resolve(), CONFIG_PATH.resolve(), Path(november.__file__).resolve()):
        relative = str(path.relative_to(ROOT))
        subprocess.run(["git", "ls-files", "--error-unmatch", relative], cwd=ROOT,
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if subprocess.run(["git", "diff", "--quiet", "HEAD", "--", relative], cwd=ROOT).returncode:
            raise RuntimeError(f"Commit the November funding adapter and parent account first: {relative}")


def _load_legs(config: dict) -> tuple[pd.DataFrame, dict]:
    path = ROOT / config["inputs"]["funded_legs"]
    if not path.is_file():
        raise FileNotFoundError(f"Parent November funded legs are not available: {path}")
    legs = pd.read_csv(path, dtype={"event_id": str, "parent_id": str, "symbol": str},
                       float_precision="round_trip")
    required = {
        "event_id", "parent_id", "symbol", "side", "tp_r", "entry_ms", "entry_price",
        "entry_order", "exit_bar_ms", "exit_phase", "exit_price", "exit_reason", "exit_fee_rate",
    }
    missing = required - set(legs.columns)
    if missing:
        raise ValueError(f"Parent funded-leg source is missing columns: {sorted(missing)}")
    for col in ("side", "entry_ms", "entry_order", "exit_bar_ms"):
        legs[col] = pd.to_numeric(legs[col], errors="raise").astype("int64")
    for col in ("tp_r", "entry_price", "exit_price", "exit_fee_rate"):
        legs[col] = pd.to_numeric(legs[col], errors="raise").astype(float)
    legs["symbol"] = legs.symbol.astype(str).str.removesuffix("USDT")
    legs["exit_phase"] = legs.exit_phase.astype(str)
    if legs.empty or legs.event_id.isna().any() or legs.parent_id.isna().any():
        raise ValueError("Parent funded-leg source is empty or has missing event/parent IDs")
    if legs.duplicated(["event_id", "tp_r"]).any():
        raise ValueError("Parent funded legs duplicate an event_id/tp_r key")
    if not legs.groupby("event_id").size().eq(2).all() or not legs.groupby("event_id").tp_r.apply(
        lambda values: set(values.astype(float)) == {0.0, 2.0}
    ).all():
        raise ValueError("Every selected parent must retain one runner and one native 2R child")
    stable = ["parent_id", "symbol", "side", "entry_ms", "entry_order"]
    if not legs.groupby("event_id")[stable].nunique(dropna=False).eq(1).all().all():
        raise ValueError("Child legs disagree on parent identity, symbol, side, or native entry priority")
    if not legs.side.isin([-1, 1]).all() or not legs.exit_phase.isin(["open", "intrabar", "close"]).all():
        raise ValueError("Invalid side or native exit phase in parent funded legs")
    if (legs.entry_ms > legs.exit_bar_ms).any() or (legs.exit_bar_ms % HOUR).any():
        raise ValueError("Parent leg clock is not an ordered H1-open timestamp")
    if not np.isfinite(legs[["entry_price", "exit_price", "exit_fee_rate"]]).all().all():
        raise ValueError("Parent funded legs contain non-finite native prices or fees")
    limit_exit = legs.exit_reason.astype(str).eq("take_profit")
    expected_exit_fee = np.where(limit_exit, MAKER, TAKER)
    if not np.allclose(legs.exit_fee_rate.to_numpy(dtype=float), expected_exit_fee, rtol=0, atol=1e-12):
        raise ValueError("Parent native exit fee class differs from maker target versus market/delisting fills")
    if "slot" not in legs:
        raise ValueError("Parent funded legs must include their frozen slot assignment")
    legs["slot"] = pd.to_numeric(legs.slot, errors="raise").astype("int64")
    if not legs.groupby("event_id").slot.nunique().eq(1).all():
        raise ValueError("Child legs disagree on their frozen account slot")
    if legs.slot.isna().any() or not legs.slot.between(0, 8).all():
        raise ValueError("Parent slot assignment is missing or outside the nine-slot account")
    if legs.event_id.nunique() < int(config["account"]["minimum_parent_count"]):
        raise ValueError("Extended wallet is missing part of the frozen original parent roster")
    if "is_control" in legs and _bool_column(legs.is_control, "funded_legs.is_control").any():
        raise ValueError("The November Binance actual-account replay cannot include controls")
    return legs.sort_values(["entry_ms", "entry_order", "symbol", "event_id", "tp_r"]).reset_index(drop=True), {
        "funded_legs_file": str(path.relative_to(ROOT)),
        "positions": int(legs.event_id.nunique()), "child_legs": int(len(legs)),
        "symbols": int(legs.symbol.nunique()), "first_entry_ms": int(legs.entry_ms.min()),
        "last_exit_bar_ms": int(legs.exit_bar_ms.max()),
        "parent_join_key": "event_id", "child_join_key": ["event_id", "tp_r"],
    }


def _prepare_account(legs: pd.DataFrame, config: dict) -> dict:
    data = november.prepare(legs)
    if not isinstance(data, dict) or "selected" not in data:
        raise TypeError("cloud_close_november_account.prepare(legs) must return native scaleout.pair_data")
    selected = data["selected"].copy()
    if selected.duplicated(["event_id", "tp_r"]).any():
        raise ValueError("Native November account preparer duplicated event_id/tp_r")
    source_keys = set(zip(legs.event_id.astype(str), legs.tp_r.astype(float)))
    selected_keys = set(zip(selected.event_id.astype(str), selected.tp_r.astype(float)))
    if source_keys != selected_keys:
        raise ValueError("Native November account preparer changed the frozen funded-leg roster")
    for col in ("symbol", "side", "entry_ms", "entry_order", "exit_bar_ms", "exit_phase",
                "entry_price", "exit_price", "exit_fee_rate"):
        if col not in selected or col not in legs:
            raise ValueError(f"Native account selection/source is missing frozen field {col}")
        left = selected.set_index(["event_id", "tp_r"]).sort_index()[col].astype(str).to_numpy()
        right = legs.set_index(["event_id", "tp_r"]).sort_index()[col].astype(str).to_numpy()
        if not np.array_equal(left, right):
            raise ValueError(f"Native account preparer changed the frozen leg field {col}")
    input_slot = legs.groupby("event_id").slot.first().astype("int64").sort_index()
    native_slot = selected.set_index("event_id").slot.astype("int64")
    if not native_slot.groupby(level=0).apply(lambda values: len(values) == 2 and
                                               set(values.to_numpy(dtype=int)) ==
                                               {2 * int(input_slot.loc[values.index[0]]),
                                                2 * int(input_slot.loc[values.index[0]]) + 1}).all():
        raise ValueError("Native runner/target columns do not map to the frozen nine account slots")
    data["funding_input_audit"] = {
        "native_preparer": "cloud_close_november_account.prepare",
        "native_quote_loader": "cloud_close_november_account.quote_loader",
        "parents_joined_by": "event_id", "children_joined_by": ["event_id", "tp_r"],
        "priority_source": "funded_legs.entry_order and parent frozen slot",
        "exact_child_roster_and_native_path_match": True,
        "positions": int(legs.event_id.nunique()), "child_legs": int(len(legs)),
    }
    return hourly._expand_hourly(
        data, november.quote_loader, float(config["hourly_management"]["single_contract_weight_cap"]),
    )


def _assert_end_bound(data: dict, config: dict) -> int:
    expected = int(pd.Timestamp(config["inputs"]["input_end_exclusive_utc"]).value // 1_000_000)
    actual = int(data["start"] + data["hours"] * HOUR)
    if actual != expected:
        raise ValueError(f"Native hourly account end differs from frozen extension: {actual} != {expected}")
    return expected


def _load_old_sources(config: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    paths = [ROOT / config["inputs"][key] for key in
             ("old_funding_tape", "old_source_months", "old_source_coverage")]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"Previously frozen funding source is missing: {path}")
    tape = pd.read_csv(paths[0], dtype={"symbol": str}, float_precision="round_trip")
    months = pd.read_csv(paths[1], dtype={"symbol": str, "month": str})
    audit = _read_json(paths[2])
    required_tape = {"symbol", "funding_time_ms", "funding_rate", "mark_price", "rate_source", "mark_source", "source_complete"}
    required_months = {"symbol", "month", "query_start_ms", "query_end_exclusive_ms", "range_complete",
                       "missing_rate_rows", "missing_mark_rows"}
    if required_tape - set(tape.columns) or required_months - set(months.columns):
        raise ValueError("Frozen source-builder schema changed")
    tape["funding_time_ms"] = pd.to_numeric(tape.funding_time_ms, errors="raise").astype("int64")
    for col in ("funding_rate", "mark_price"):
        tape[col] = pd.to_numeric(tape[col], errors="coerce")
    tape["source_complete"] = _bool_column(tape.source_complete, "old_funding_tape.source_complete")
    months["query_start_ms"] = pd.to_numeric(months.query_start_ms, errors="raise").astype("int64")
    months["query_end_exclusive_ms"] = pd.to_numeric(months.query_end_exclusive_ms, errors="raise").astype("int64")
    months["range_complete"] = _bool_column(months.range_complete, "old_source_months.range_complete")
    for col in ("missing_rate_rows", "missing_mark_rows"):
        months[col] = pd.to_numeric(months[col], errors="raise").astype("int64")
    months["symbol"] = months.symbol.astype(str).str.removesuffix("USDT")
    tape["symbol"] = tape.symbol.astype(str).str.removesuffix("USDT")
    if not bool(audit.get("complete")) or not months.range_complete.all():
        raise ValueError("Earlier frozen hourly funding source is incomplete; do not treat missing fees as zero")
    if months.duplicated(["symbol", "month"]).any() or tape.duplicated(["symbol", "funding_time_ms"]).any():
        raise ValueError("Earlier funding source has duplicate symbol-month or settlement keys")
    if months.missing_rate_rows.sum() or months.missing_mark_rows.sum() or not tape.source_complete.all():
        raise ValueError("Earlier funding source reports missing rates/marks")
    return tape.sort_values(["symbol", "funding_time_ms"]).reset_index(drop=True), months, audit


def _uncovered_segments(start_ms: int, end_ms: int, old_start: int, old_end: int,
                        old_complete: bool) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return locally covered and missing half-open portions of a needed month."""
    if not old_complete or old_end <= start_ms or old_start >= end_ms:
        return [], [(start_ms, end_ms)]
    covered = [(max(start_ms, old_start), min(end_ms, old_end))]
    gaps = []
    if start_ms < covered[0][0]:
        gaps.append((start_ms, covered[0][0]))
    if covered[0][1] < end_ms:
        gaps.append((covered[0][1], end_ms))
    return [(a, b) for a, b in covered if a < b], [(a, b) for a, b in gaps if a < b]


def _collect_sources(legs: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    old_tape, old_months, old_audit = _load_old_sources(config)
    month_lookup = {(str(row.symbol), str(row.month)): row
                    for row in old_months.itertuples(index=False)}
    old_tape_by_symbol = {str(symbol): frame for symbol, frame in old_tape.groupby("symbol", sort=False)}
    windows = ledger._month_windows(legs)
    client = FundingClient("binance", ledger.CACHE / "funding_api")
    tapes, month_rows = [], []
    old_month_reused = 0
    old_rows_reused = 0
    newly_sourced_windows = 0
    api_pages = 0
    for i, (key, window) in enumerate(sorted(windows.items()), 1):
        symbol, month = key
        start_ms, end_ms = int(window["start_ms"]), int(window["end_ms"])
        previous = month_lookup.get(key)
        if previous is None:
            old_start = old_end = 0
            previous_complete = False
        else:
            old_start = int(previous.query_start_ms)
            old_end = int(previous.query_end_exclusive_ms)
            previous_complete = bool(previous.range_complete) and not int(previous.missing_rate_rows) and not int(previous.missing_mark_rows)
        covered, gaps = _uncovered_segments(start_ms, end_ms, old_start, old_end, previous_complete)
        pieces = []
        if covered:
            old_part = old_tape_by_symbol.get(symbol, pd.DataFrame())
            old_receipt_rows = int(getattr(previous, "funding_settlement_rows_in_query_window", 0))
            if old_part.empty and old_receipt_rows:
                raise ValueError(f"Old month receipt claims coverage but no settlement tape exists for {symbol} {month}")
            stamp = (old_part.funding_time_ms.to_numpy(dtype=np.int64) if not old_part.empty
                     else np.empty(0, dtype=np.int64))
            for left, right in covered:
                piece = old_part.loc[(stamp >= left) & (stamp < right)].copy()
                if len(piece) and (piece.funding_rate.isna().any() or piece.mark_price.isna().any() or not piece.source_complete.all()):
                    raise ValueError(f"Previously covered settlement rows are incomplete for {symbol} {month}")
                pieces.append(piece)
                old_rows_reused += len(piece)
            old_month_reused += 1
        gap_audits = []
        for gap_start, gap_end in gaps:
            if gap_start % 1_000 or gap_end % 1_000:
                raise ValueError(f"New funding source window is not second-aligned: {key} {(gap_start, gap_end)}")
            piece, source_audit = ledger._window_history(
                symbol, month,
                {"start_ms": gap_start, "end_ms": gap_end,
                 "position_ids": set(window["position_ids"]), "exit_phases": set(window["exit_phases"])},
                client, allow_api=False, bulk_archives=True, cache_only=False,
            )
            if not source_audit["range_complete"] or source_audit["missing_rate_rows"] or source_audit["missing_mark_rows"]:
                # Only incomplete archive windows need the public REST request;
                # normalized/cache and official Vision rows have already been tried.
                piece, source_audit = ledger._window_history(
                    symbol, month,
                    {"start_ms": gap_start, "end_ms": gap_end,
                     "position_ids": set(window["position_ids"]), "exit_phases": set(window["exit_phases"])},
                    client, allow_api=True, bulk_archives=True, cache_only=False,
                )
            if not source_audit["range_complete"] or source_audit["missing_rate_rows"] or source_audit["missing_mark_rows"]:
                raise ValueError(
                    f"Official funding/mark sources do not fully cover {symbol} {month} [{gap_start},{gap_end}): "
                    f"{source_audit}"
                )
            if len(piece) and (piece.funding_rate.isna().any() or piece.mark_price.isna().any()
                               or piece.mark_price.le(0).any()):
                raise ValueError(f"New source rows contain missing/nonpositive rates or marks for {symbol} {month}")
            pieces.append(piece)
            gap_audits.append(source_audit)
            newly_sourced_windows += 1
            request = source_audit.get("api_request_manifest") or {}
            api_pages += len(request.get("pages", [])) if isinstance(request, dict) else 0
        merged = ledger._merge_history(pieces) if pieces else pd.DataFrame(
            columns=["funding_time_ms", "funding_rate", "mark_price", "rate_source", "mark_source", "source_complete"]
        )
        merged = merged.loc[(merged.funding_time_ms >= start_ms) & (merged.funding_time_ms < end_ms)].copy()
        if not merged.empty:
            if merged.funding_time_ms.duplicated().any() or merged.funding_rate.isna().any() or merged.mark_price.isna().any():
                raise ValueError(f"Incomplete/conflicting funding rows remain after source merge: {key}")
            if merged.mark_price.le(0).any() or not merged.source_complete.all():
                raise ValueError(f"Invalid mark or incomplete source flag after merge: {key}")
        if sum(b - a for a, b in covered + gaps) != end_ms - start_ms:
            raise AssertionError(f"Old plus new source intervals do not cover the held month window: {key}")
        tapes.append(merged.assign(symbol=symbol))
        audit_parts = [
            {"source": "frozen_hourly_trim_source", "start_ms": a, "end_ms": b,
             "range_complete": True} for a, b in covered
        ] + [
            {"source": item.get("source"), "start_ms": int(item["query_start_ms"]),
             "end_ms": int(item["query_end_exclusive_ms"]), "range_complete": bool(item["range_complete"]),
             "rate_source": item.get("source"), "monthly_rate_archive": item.get("monthly_rate_archive"),
             "monthly_mark_1h_archive": item.get("monthly_mark_1h_archive"),
             "api_request_manifest": item.get("api_request_manifest")}
            for item in gap_audits
        ]
        month_rows.append({
            "symbol": symbol, "month": month, "query_start_ms": start_ms,
            "query_end_exclusive_ms": end_ms, "position_count": len(window["position_ids"]),
            "exit_phases": "|".join(sorted(window["exit_phases"])),
            "range_complete": True, "missing_rate_rows": 0, "missing_mark_rows": 0,
            "funding_settlement_rows": int(len(merged)),
            "funding_settlement_rows_in_query_window": int(len(merged)),
            "reused_old_coverage": bool(covered), "new_source_segments": len(gaps),
            "sources": json.dumps(audit_parts, ensure_ascii=False, separators=(",", ":")),
        })
        if i % 100 == 0:
            print(f"November funding source windows {i}/{len(windows)}", flush=True)
    tape = pd.concat(tapes, ignore_index=True) if tapes else pd.DataFrame()
    months = pd.DataFrame(month_rows)
    if tape.duplicated(["symbol", "funding_time_ms"]).any():
        raise ValueError("Combined funding tape duplicated a symbol/settlement timestamp")
    mark_counts = tape.mark_source.astype(str).value_counts().to_dict()
    source_audit = {
        "complete": bool(months.range_complete.all() and not months.missing_rate_rows.sum()
                          and not months.missing_mark_rows.sum() and tape.source_complete.all()),
        "positions": int(legs.event_id.nunique()), "legs": int(len(legs)),
        "symbols": int(legs.symbol.nunique()), "symbol_months": int(len(months)),
        "market_settlements": int(len(tape)), "old_source_positions": old_audit.get("positions"),
        "old_source_symbol_months": int(len(old_months)), "old_source_rows_reused": int(old_rows_reused),
        "held_months_reusing_old_coverage": int(old_month_reused),
        "new_source_segments": int(newly_sourced_windows), "new_public_api_pages": int(api_pages),
        "mark_source_counts": mark_counts,
        "rate_source_counts": tape.rate_source.astype(str).value_counts().to_dict(),
        "source_extension_end_utc": "2026-11-08T12:00:00Z",
        "source_gaps": [row for row in month_rows if not row["range_complete"]],
        "parent_event_ids": sorted(legs.event_id.astype(str).unique().tolist()),
        "parent_source_file": str((ACCOUNT_EXP / "funded_legs.csv.gz").relative_to(ROOT)),
        "no_missing_fee_or_mark_zero_fill": True,
    }
    if not source_audit["complete"]:
        raise ValueError("New and reused funding source windows are not fully covered")
    return tape.sort_values(["symbol", "funding_time_ms"]).reset_index(drop=True), months, source_audit


def _funding_sources(legs: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    tape_path = EXP / "funding_tape.csv.gz"
    months_path = EXP / "source_months.csv.gz"
    audit_path = EXP / "source_coverage.json"
    if any(path.exists() for path in (tape_path, months_path, audit_path)):
        if not all(path.is_file() for path in (tape_path, months_path, audit_path)):
            raise FileExistsError("Partial funding source receipt exists; do not silently overwrite it")
        tape = pd.read_csv(tape_path, dtype={"symbol": str}, float_precision="round_trip")
        months = pd.read_csv(months_path, dtype={"symbol": str, "month": str})
        audit = _read_json(audit_path)
        if not audit.get("complete") or set(audit.get("parent_event_ids", [])) != set(legs.event_id.astype(str)):
            raise ValueError("Existing source tape does not match the frozen extended parent roster")
        months["range_complete"] = _bool_column(months.range_complete, "source_months.range_complete")
        for col in ("missing_rate_rows", "missing_mark_rows", "query_start_ms", "query_end_exclusive_ms",
                    "funding_settlement_rows_in_query_window"):
            months[col] = pd.to_numeric(months[col], errors="raise").astype("int64")
        if (not months.range_complete.all() or months.missing_rate_rows.sum()
                or months.missing_mark_rows.sum()):
            raise ValueError("Existing extended source month receipt is incomplete")
        expected = ledger._month_windows(legs)
        observed = {(str(r.symbol), str(r.month)): (int(r.query_start_ms), int(r.query_end_exclusive_ms))
                    for r in months.itertuples(index=False)}
        if observed != {key: (int(v["start_ms"]), int(v["end_ms"])) for key, v in expected.items()}:
            raise ValueError("Existing source tape month windows do not match the current frozen child paths")
        tape["funding_time_ms"] = pd.to_numeric(tape.funding_time_ms, errors="raise").astype("int64")
        tape["funding_rate"] = pd.to_numeric(tape.funding_rate, errors="raise").astype(float)
        tape["mark_price"] = pd.to_numeric(tape.mark_price, errors="raise").astype(float)
        tape["source_complete"] = _bool_column(tape.source_complete, "funding_tape.source_complete")
        if not tape.source_complete.all() or tape.duplicated(["symbol", "funding_time_ms"]).any():
            raise ValueError("Existing source tape is incomplete or duplicated")
        return tape, months, audit
    tape, months, audit = _collect_sources(legs, config)
    _write_frame(tape_path, tape, index=False)
    _write_frame(months_path, months, index=False)
    _write_json(audit_path, audit)
    return tape, months, audit


def preflight(config_path: Path = CONFIG_PATH) -> dict:
    config = _read_json(config_path)
    _validate_config(config)
    legs, source = _load_legs(config)
    data = _prepare_account(legs, config)
    end_ms = _assert_end_bound(data, config)
    if len(data["selected"]) != len(legs):
        raise ValueError("Native account expansion changed the funded child leg count")
    result = {
        "vectorbt_version": hourly.vbt.__version__, "source_legs": source,
        "parent_prepare": data["funding_input_audit"],
        "native_hourly_start_ms": int(data["start"]), "native_hourly_rows": int(data["hours"]),
        "native_hourly_end_exclusive_ms": end_ms,
        "native_h1_quote_paths_prepared": True,
        "funding_source_collected": False, "dynamic_funding_account_started": False,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return result


def _write_scenario(output_dir: Path, scenario: str, data: dict, tape: pd.DataFrame,
                    config: dict, market_slip: float) -> dict:
    runtime = json.loads(json.dumps(config))
    runtime["account"]["expected_positions"] = int(data["selected"].event_id.nunique())
    runtime["market_slip"] = float(market_slip)
    child, parent, equity, flows, result = hourly._run_scenario(data, tape, runtime, scenario)
    scenario_dir = output_dir / scenario
    scenario_dir.mkdir(parents=True, exist_ok=True)
    child["funding_scenario"] = scenario
    parent["funding_scenario"] = scenario
    hourly._write_frame(scenario_dir / "funded_legs.csv.gz", child, index=False)
    hourly._write_frame(scenario_dir / "funded_positions.csv.gz", parent.reset_index(names="event_id"), index=False)
    hourly._write_frame(scenario_dir / "hourly_equity.csv.gz", equity, index_label="hour_end_utc")
    hourly._write_frame(scenario_dir / "applied_funding.csv.gz", flows, index=False)
    orders = result["audit"].pop("orders_frame")
    hourly._write_frame(scenario_dir / "orders.csv.gz", orders, index=False)
    hourly._write_frame(scenario_dir / "account_metrics.csv",
                        pd.DataFrame([{"period": period, **metrics}
                                      for period, metrics in result["metrics"].items()]), index=False)
    total_cash = 1000.0 + float(orders.order_cashflow_usdt.sum()) + float(
        flows.loc[flows.applied, "funding_cashflow_usdt"].sum()
    )
    if not np.isclose(total_cash, float(equity.nav.iloc[-1]), rtol=1e-8, atol=0.05):
        raise ValueError("VectorBT order fees, exact funding rows and terminal NAV fail cash reconciliation")
    result["audit"].update({
        "market_slippage_rate": float(market_slip),
        "vectorbt_order_fees_usdt": float(orders.fees.sum()),
        "funding_cashflow_usdt": float(flows.loc[flows.applied, "funding_cashflow_usdt"].sum()),
        "terminal_nav_cash_reconciliation_usdt": float(total_cash),
        "terminal_nav_cash_reconciliation_abs_error_usdt": float(abs(total_cash - equity.nav.iloc[-1])),
        "exact_and_proxy_mark_counts_applied": flows.loc[flows.applied, "mark_source"].astype(str).value_counts().to_dict(),
        "source_months_complete": True,
        "hourly_bounds_and_nav_reconciled_by_existing_vectorbt_adapter": True,
    })
    result["audit"].pop("orders_frame", None)
    return result


def run(config_path: Path = CONFIG_PATH) -> dict:
    _assert_committed()
    config = _read_json(config_path)
    _validate_config(config)
    account_output = ROOT / config["outputs"]["scope"].rstrip("/")
    receipt = account_output / "funding_account_audit.json"
    if receipt.exists():
        raise FileExistsError(f"Extended November account receipt already exists: {receipt}")
    legs, leg_audit = _load_legs(config)
    data = _prepare_account(legs, config)
    _assert_end_bound(data, config)
    tape, months, source_audit = _funding_sources(legs, config)
    data["funding_no_settlement_windows"] = hourly.verified_empty_windows(months)
    account_output.mkdir(parents=True, exist_ok=True)
    scenarios = {}
    for name, slip, subdir in (
        ("include_boundary", 0.0, account_output),
        ("exclude_boundary", 0.0, account_output),
        ("include_boundary", float(config["additional_market_slippage"]), account_output / "market_slip_5bp"),
        ("exclude_boundary", float(config["additional_market_slippage"]), account_output / "market_slip_5bp"),
    ):
        label = f"{'market_slip_5bp/' if slip else ''}{name}"
        scenarios[label] = _write_scenario(subdir, name, data, tape, config, slip)
        gc.collect()
    final = {
        "id": config["id"], "status": "complete", "vectorbt_version": hourly.vbt.__version__,
        "source_account": leg_audit, "funding_source": source_audit,
        "funding_boundaries": ["include_boundary", "exclude_boundary"],
        "additional_market_slippage": float(config["additional_market_slippage"]),
        "scenarios": {key: {"metrics": value["metrics"], "audit": value["audit"]}
                      for key, value in scenarios.items()},
        "full_parent_roster_replayed": True, "all_selected_children_have_native_entry_exit_orders": True,
        "five_bp_stress_repriced_orders_and_funding_account": True,
        "source_months_file": str((EXP / "source_months.csv.gz").relative_to(ROOT)),
        "funding_tape_file": str((EXP / "funding_tape.csv.gz").relative_to(ROOT)),
        "account_nav_reconciled_from_vectorbt_orders_and_cash_settlements": True,
        "no_strategy_registry_or_deployment_changes": True,
    }
    _write_json(receipt, final)
    print(json.dumps({"status": "complete", "positions": leg_audit["positions"],
                      "legs": leg_audit["child_legs"], "funding_symbol_months": len(months),
                      "funding_rows": len(tape), "scenarios": list(scenarios),
                      "audit": str(receipt.relative_to(ROOT))}, ensure_ascii=False, indent=2), flush=True)
    return final


def mini_check() -> dict:
    """Run the accepted VectorBT path fixture and verify a real archived source month."""
    hourly_result = hourly._mini_check()
    month = "2026-10"
    month_start = int(pd.Timestamp(f"{month}-01T00:00:00Z").value // 1_000_000)
    month_end = int((pd.Timestamp(f"{month}-01T00:00:00Z") + pd.offsets.MonthBegin(1)).value // 1_000_000)
    client = FundingClient("binance", ledger.CACHE / "funding_api")
    history, audit = ledger._window_history(
        "1000LUNC", month,
        {"start_ms": month_start, "end_ms": month_end,
         "position_ids": {"local-real-source-mini"}, "exit_phases": {"open"}},
        client, allow_api=False, bulk_archives=True, cache_only=True,
    )
    if (history.empty or not audit["range_complete"] or audit["missing_rate_rows"]
            or audit["missing_mark_rows"] or history.funding_rate.isna().any()
            or history.mark_price.isna().any() or history.mark_price.le(0).any()):
        raise AssertionError(f"Official local Binance 1000LUNC Oct-2026 source/mark mini failed: {audit}")
    cashflow = -float(history.funding_rate.iloc[0]) * float(history.mark_price.iloc[0])
    if not np.isfinite(cashflow):
        raise AssertionError("Actual archived funding event did not produce a finite one-unit cashflow")
    empty_month = pd.DataFrame([{
        "symbol": "MINI", "month": month, "query_start_ms": month_start,
        "query_end_exclusive_ms": month_end, "range_complete": True,
        "missing_rate_rows": 0, "missing_mark_rows": 0,
        "funding_settlement_rows_in_query_window": 0,
    }])
    empty_windows = hourly.verified_empty_windows(empty_month)
    if empty_windows != {"MINI": [(month_start, month_end)]}:
        raise AssertionError("Explicit complete zero-settlement windows are not carried into the VBT scheduler")
    result = {
        "vectorbt_mini": hourly_result,
        "real_source": {"symbol": "1000LUNCUSDT", "month": month, "settlements": int(len(history)),
                        "range_complete": bool(audit["range_complete"]),
                        "rate_source": history.rate_source.astype(str).value_counts().to_dict(),
                        "mark_source": history.mark_source.astype(str).value_counts().to_dict(),
                        "first_funding_rate": float(history.funding_rate.iloc[0]),
                        "first_mark_price": float(history.mark_price.iloc[0]),
                        "one_unit_long_cashflow_usdt": cashflow,
                        "verified_empty_window_fixture": empty_windows,
                        "network_requests_started": False},
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--mini-check", action="store_true")
    group.add_argument("--run", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        preflight()
    elif args.mini_check:
        mini_check()
    else:
        run()


if __name__ == "__main__":
    main()
