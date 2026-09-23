"""Summarize authenticated long-horizon SPIKE V12.8 entry-clock ledgers.

This module only groups already-built replay streams. It does not fetch market
data, rebuild signals, draw controls, or change the frozen execution rules.
Calendar cohorts are assigned by actual Beijing entry time; a monthly/yearly
control pair must also have its actual entry in that same calendar period.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_v128_entry_clock as entry_clock
from yoyo.evaluation import spike_v128_recent as recent
from yoyo.evaluation import spike_v128_retest_entry as retest
from yoyo.evaluation.spike_v112_support_study import _local_transitive_python
from yoyo.evaluation.spike_v8_six_filters import _committed


POLICIES = ("baseline", "retest")
KINDS = ("overall", "4h", "1h")
CALENDAR_KINDS = ("overall", "4h")
PERIODS = ("all", "earlier", "later", "cross")
TRADE_COLUMNS = (
    "trade_key", "arm", "symbol", "timeframe_min", "policy", "status",
    "entry_time", "exit_time", "net_return", "net_r", "gross_return",
)
CONTROL_COLUMNS = (
    "trade_key", "arm", "symbol", "timeframe_min", "control_pool", "matched",
    "control_status", "control_signal_close", "control_exit_time",
    "control_net_return", "control_net_r", "control_censored",
)
FIXED_CLOCK_CANDIDATES = (
    {"candidate_id": "baseline_15_v9_both_4h20", "timeframe_min": 15, "arm": "v9_both", "policy": "baseline", "kind": "4h", "bucket": 20},
    {"candidate_id": "baseline_15_joint_4h20", "timeframe_min": 15, "arm": "joint", "policy": "baseline", "kind": "4h", "bucket": 20},
    {"candidate_id": "baseline_60_v9_both_4h00", "timeframe_min": 60, "arm": "v9_both", "policy": "baseline", "kind": "4h", "bucket": 0},
    {"candidate_id": "baseline_60_joint_4h00", "timeframe_min": 60, "arm": "joint", "policy": "baseline", "kind": "4h", "bucket": 0},
    {"candidate_id": "retest_15_v9_both_4h20", "timeframe_min": 15, "arm": "v9_both", "policy": "retest", "kind": "4h", "bucket": 20},
    {"candidate_id": "retest_15_v9_both_1h23", "timeframe_min": 15, "arm": "v9_both", "policy": "retest", "kind": "1h", "bucket": 23},
)


def digest(path: str | Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(block)
    return hasher.hexdigest()


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def _check_code_hashes(code: dict, label: str) -> None:
    if not isinstance(code, dict) or not code:
        raise ValueError(f"{label} identity lacks code hashes")
    for path, expected in code.items():
        source = Path(path)
        if not source.is_file() or digest(source) != expected:
            raise ValueError(f"{label} source code changed: {path}")


def _check_run(identity: dict, manifest: dict, label: str) -> None:
    if fingerprint(identity) != manifest.get("run_identity"):
        raise ValueError(f"{label} identity hash mismatch")
    if not manifest.get("complete") or manifest.get("errors") or identity.get("subset"):
        raise ValueError(f"{label} must be a complete full run")


def _expected_stream_keys(symbols: list[str], timeframes: list[int]) -> set[str]:
    return {f"{symbol}_{int(minutes)}m" for symbol in symbols for minutes in timeframes}


def authenticate(config_path: str | Path, run_path: str | Path) -> dict:
    """Authenticate the summary config, replay, baseline parent, and all streams."""
    config_path, run_path = Path(config_path), Path(run_path)
    cfg = _read_json(config_path)
    identity = _read_json(run_path / "identity.json")
    manifest = _read_json(run_path / "manifest.json")
    _check_run(identity, manifest, "retest replay")
    if identity.get("config") != cfg or identity.get("config_sha256") != digest(config_path):
        raise ValueError("summary config differs from the replay identity")
    _check_code_hashes(identity.get("code"), "retest replay")

    symbols = list(identity.get("symbols", []))
    timeframes = [int(value) for value in identity.get("timeframes", [])]
    expected = _expected_stream_keys(symbols, timeframes)
    if not symbols or not timeframes or set(manifest.get("receipts", {})) != expected:
        raise ValueError("retest manifest stream inventory mismatch")
    if identity.get("inputs") is None or set(identity["inputs"]) != set(symbols):
        raise ValueError("retest identity input inventory mismatch")

    parent_run = Path(cfg["parent_run"])
    parent_identity_path, parent_manifest_path = parent_run / "identity.json", parent_run / "manifest.json"
    parent_identity, parent_manifest = _read_json(parent_identity_path), _read_json(parent_manifest_path)
    _check_run(parent_identity, parent_manifest, "baseline parent")
    if parent_identity.get("config") != cfg:
        raise ValueError("baseline parent config differs from the summary config")
    _check_code_hashes(parent_identity.get("code"), "baseline parent")
    if (identity.get("parent_identity_sha256") != digest(parent_identity_path) or
            identity.get("parent_manifest_sha256") != digest(parent_manifest_path)):
        raise ValueError("retest identity no longer matches its baseline parent")
    if (parent_identity.get("inputs") != identity["inputs"] or
            parent_identity.get("symbols") != symbols or
            parent_identity.get("timeframes") != timeframes):
        raise ValueError("baseline and retest stream identities differ")
    metadata = identity.get("symbol_meta_source", {})
    if metadata != parent_identity.get("symbol_meta_source"):
        raise ValueError("baseline and retest symbol metadata snapshots differ")
    metadata_path = Path(metadata.get("path", ""))
    if not metadata_path.is_file() or digest(metadata_path) != metadata.get("sha256"):
        raise ValueError("symbol metadata snapshot changed")

    input_manifest_path = Path(identity.get("input_manifest", ""))
    if (str(input_manifest_path) != cfg.get("input_manifest") or
            digest(input_manifest_path) != identity.get("input_manifest_sha256") or
            parent_identity.get("input_manifest_sha256") != identity.get("input_manifest_sha256")):
        raise ValueError("merged input manifest changed or differs from config")
    input_manifest = _read_json(input_manifest_path)
    if not input_manifest.get("complete") or input_manifest.get("failed"):
        raise ValueError("merged input manifest is incomplete")
    input_rows = {row["symbol"]: row for row in input_manifest.get("streams", [])}
    if set(input_rows) != set(symbols):
        raise ValueError("merged input symbol inventory mismatch")
    for symbol in symbols:
        if input_rows[symbol].get("sha256") != identity["inputs"].get(symbol):
            raise ValueError(f"merged input hash differs from replay identity: {symbol}")

    if cfg.get("source_manifest") and input_manifest.get("source_manifest") != cfg["source_manifest"]:
        raise ValueError("merged input manifest points at a different source manifest")
    analysis_start = pd.Timestamp(cfg["analysis_start"])
    split = pd.Timestamp(cfg["analysis_split"])
    end = pd.Timestamp(cfg["end"])
    if not (pd.Timestamp(cfg["start"]) <= analysis_start < split < end):
        raise ValueError("invalid analysis period boundaries")
    if pd.Timestamp(cfg["analysis_split"]) != pd.Timestamp(cfg["split"]):
        raise ValueError("analysis split must match the replay split")
    old_window_start = pd.Timestamp(cfg["old_window_start"])
    if not (analysis_start <= old_window_start < end):
        raise ValueError("old-window cut must lie inside the analysis window")
    if float(cfg["round_trip_cost"]) != 0.002:
        raise ValueError("the fixed 20bp cost contract changed")

    parent_sha_by_key: dict[str, str] = {}
    retest_receipts: dict[str, dict] = {}
    data_by_symbol = input_rows
    trades_parts, controls_parts = [], []
    for symbol in symbols:
        for minutes in timeframes:
            key = f"{symbol}_{minutes}m"
            parent_folder = parent_run / "streams" / key
            retest_folder = run_path / "streams" / key
            parent_receipt = retest.authenticated_parent(parent_folder, parent_identity, parent_manifest, symbol, minutes)
            parent_sha = digest(parent_folder / "receipt.json")
            if parent_manifest["receipts"].get(key) != parent_sha:
                raise ValueError(f"baseline parent receipt hash mismatch: {key}")
            if manifest["receipts"].get(key) != digest(retest_folder / "receipt.json"):
                raise ValueError(f"retest receipt hash mismatch: {key}")
            receipt = retest.validate_receipt(
                retest_folder, manifest["run_identity"], identity["inputs"][symbol],
                symbol, minutes, parent_sha,
            )
            if receipt.get("summary", {}).get("parent_parity") is not True:
                raise ValueError(f"retest stream lacks verified parent parity: {key}")
            if parent_receipt.get("status") != "complete":
                raise ValueError(f"baseline parent stream is incomplete: {key}")
            parent_sha_by_key[key] = parent_sha
            retest_receipts[key] = receipt

            trade_path = retest_folder / "serial_trades.csv.gz"
            control_path = retest_folder / "controls.csv.gz"
            trades_parts.append(_read_required_csv(trade_path, TRADE_COLUMNS))
            controls_parts.append(_read_required_csv(control_path, CONTROL_COLUMNS))

    trades = pd.concat(trades_parts, ignore_index=True) if trades_parts else pd.DataFrame(columns=TRADE_COLUMNS)
    controls = pd.concat(controls_parts, ignore_index=True) if controls_parts else pd.DataFrame(columns=CONTROL_COLUMNS)
    if trades.duplicated(["trade_key", "policy"]).any():
        raise ValueError("duplicate serial trade key/policy across streams")
    if controls.duplicated(["trade_key", "control_pool"]).any():
        raise ValueError("duplicate control key/policy across streams")
    return {
        "cfg": cfg, "identity": identity, "manifest": manifest,
        "parent_identity": parent_identity, "parent_manifest": parent_manifest,
        "input_manifest": input_manifest, "input_rows": data_by_symbol,
        "trades_all": trades, "controls_all": controls,
        "parent_receipt_sha256": parent_sha_by_key,
        "retest_receipts": retest_receipts,
        "symbol_meta_source": metadata,
        "config_sha256": digest(config_path),
        "run_identity_sha256": digest(run_path / "identity.json"),
        "run_manifest_sha256": digest(run_path / "manifest.json"),
        "parent_identity_sha256": digest(parent_identity_path),
        "parent_manifest_sha256": digest(parent_manifest_path),
        "input_manifest_sha256": digest(input_manifest_path),
    }


def _read_required_csv(path: Path, columns: tuple[str, ...]) -> pd.DataFrame:
    header = pd.read_csv(path, nrows=0).columns
    available = [column for column in columns if column in header]
    missing = set(columns) - set(available)
    if missing:
        # Empty worker streams can legitimately retain only their identity header.
        # A non-empty stream missing execution fields is a malformed ledger.
        probe = (pd.read_csv(path, usecols=available, low_memory=False) if available
                 else pd.read_csv(path, low_memory=False))
        if len(probe):
            raise ValueError(f"{path} lacks required columns: {sorted(missing)}")
        for column in missing:
            probe[column] = pd.Series(dtype="object")
        return probe.loc[:, list(columns)]
    return pd.read_csv(path, usecols=list(columns), low_memory=False)


def prepare_ledgers(trades: pd.DataFrame, controls: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Normalize execution and matched-control clocks, then apply analysis dates."""
    if trades.empty:
        trades = pd.DataFrame(columns=TRADE_COLUMNS)
    if controls.empty:
        controls = pd.DataFrame(columns=CONTROL_COLUMNS)
    trades = trades[trades.policy.isin(POLICIES)].copy()
    trades = entry_clock.clock(trades, cfg["timezone"])
    closed = trades[trades.status.eq("closed")]
    if len(closed):
        values = closed[["gross_return", "net_return", "net_r"]].to_numpy(float)
        if not np.isfinite(values).all():
            raise ValueError("closed trade outcome is non-finite")
        np.testing.assert_allclose(closed.gross_return - closed.net_return, float(cfg["round_trip_cost"]), atol=1e-12)
    trades = analysis_window(trades, cfg)

    controls = controls[controls.control_pool.isin(POLICIES)].copy()
    prepared_controls = entry_clock.prepare_controls(controls, cfg["timezone"])
    prepared_controls = analysis_window(prepared_controls, cfg)
    return trades, prepared_controls


def analysis_window(frame: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Use actual execution time, inclusive at analysis_start and exclusive at end."""
    if frame.empty:
        return frame.copy()
    if "entry_time" not in frame:
        raise ValueError("analysis rows need actual entry_time")
    entry_time = pd.to_datetime(frame.entry_time, utc=True)
    return frame.loc[(entry_time >= pd.Timestamp(cfg["analysis_start"])) &
                     (entry_time < pd.Timestamp(cfg["end"]))].copy()


def add_calendar_columns(frame: pd.DataFrame, timezone: str) -> pd.DataFrame:
    out = frame.copy()
    if out.empty:
        out["entry_month"] = pd.Series(dtype="object")
        out["entry_year"] = pd.Series(dtype="object")
        return out
    local = pd.to_datetime(out.entry_time, utc=True).dt.tz_convert(timezone)
    out["entry_month"] = local.dt.strftime("%Y-%m")
    out["entry_year"] = local.dt.strftime("%Y")
    return out


def calendar_labels(start: str | pd.Timestamp, end: str | pd.Timestamp, timezone: str, kind: str) -> list[str]:
    """Return local calendar labels touched by a right-open UTC range."""
    if kind not in ("month", "year"):
        raise ValueError(kind)
    local_start = pd.Timestamp(start).tz_convert(timezone)
    local_end = pd.Timestamp(end).tz_convert(timezone)
    if kind == "month":
        first = local_start.to_period("M").start_time
        last_included = (local_end - pd.Timedelta(nanoseconds=1)).to_period("M").start_time
        return [p.strftime("%Y-%m") for p in pd.period_range(first, last_included, freq="M")]
    first = local_start.to_period("Y").start_time
    last_included = (local_end - pd.Timedelta(nanoseconds=1)).to_period("Y").start_time
    return [p.strftime("%Y") for p in pd.period_range(first, last_included, freq="Y")]


def calendar_bounds(label: str, kind: str, timezone: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return local calendar start and the next period's start."""
    if kind == "month":
        year, month = (int(piece) for piece in label.split("-"))
        naive_start = pd.Timestamp(year=year, month=month, day=1)
        naive_end = naive_start + pd.offsets.MonthBegin(1)
    elif kind == "year":
        naive_start = pd.Timestamp(year=int(label), month=1, day=1)
        naive_end = naive_start + pd.DateOffset(years=1)
    else:
        raise ValueError(kind)
    return naive_start.tz_localize(timezone), naive_end.tz_localize(timezone)


def calendar_period_counts(
    frame: pd.DataFrame,
    kind: str,
    label: str,
    timezone: str,
    data_end: str | pd.Timestamp,
    analysis_start: str | pd.Timestamp,
) -> dict[str, Any]:
    """Count actual period-end knowledge separately from eventual exits."""
    start, end = calendar_bounds(label, kind, timezone)
    period_frame = frame[frame[f"entry_{kind}"] == label]
    closed = period_frame.status.eq("closed")
    exits = pd.to_datetime(period_frame.exit_time, utc=True).dt.tz_convert(timezone)
    cross = closed & exits.ge(end)
    known = closed & exits.lt(end)
    data_end_local = pd.Timestamp(data_end).tz_convert(timezone)
    n = len(period_frame)
    analysis_start_local = pd.Timestamp(analysis_start).tz_convert(timezone)
    start_observed = bool(start >= analysis_start_local)
    end_observed = bool(end <= data_end_local)
    return {
        "period_start_bjt": start.isoformat(),
        "period_end_bjt": end.isoformat(),
        "calendar_period_start_observed": start_observed,
        "calendar_period_end_observed": end_observed,
        "calendar_period_complete_in_analysis_window": bool(start_observed and end_observed),
        "cross_period_exit": int(cross.sum()),
        "period_end_known_closed": int(known.sum()),
        "period_end_known_closed_rate": float(known.sum() / n) if n else math.nan,
        "period_end_outcome_scope": "eventual exits may occur after entry period; incomplete current period is right-censored",
    }


def _source_span(symbol_rows: dict[str, dict], start_utc: pd.Timestamp, end_utc: pd.Timestamp, symbols: set[str] | None = None) -> int:
    start_ms, end_ms = start_utc.value // 10**6, end_utc.value // 10**6
    rows = symbol_rows.items() if symbols is None else ((s, symbol_rows[s]) for s in symbols if s in symbol_rows)
    return sum(
        int(row.get("first_ms", end_ms) < end_ms and int(row.get("last_ms", -1)) + 300_000 > start_ms)
        for _, row in rows
    )


def fixed_incumbent_symbols(input_rows: dict[str, dict], analysis_start: str | pd.Timestamp) -> set[str]:
    """Choose symbols whose merged source starts before the frozen analysis cut."""
    start_ms = pd.Timestamp(analysis_start).value // 10**6
    return {symbol for symbol, row in input_rows.items() if int(row.get("first_ms", start_ms)) < start_ms}


def _entry_bucket(frame: pd.DataFrame, kind: str) -> pd.Series:
    return entry_clock.bucket(frame, kind)


def _row_metrics(
    target: pd.DataFrame,
    random: pd.DataFrame,
    kind: str,
    period: str,
    split: pd.Timestamp,
    cfg: dict,
    meta: dict,
    *,
    test: bool = False,
) -> dict:
    pairs = entry_clock.pair_rows(target, random, kind, period, split)
    described = entry_clock.describe(entry_clock.cohort(target, period, split), cfg)
    compared = entry_clock.comparator(pairs, cfg, test=test)
    row = meta | described | compared
    row["random_coverage"] = len(pairs) / row["closed"] if row.get("closed", 0) else math.nan
    row["test_scope"] = "same-clock matched random, entry-week block inference" if test else "descriptive same-clock matched random"
    return row


def build_bucket_table(trades: pd.DataFrame, controls: pd.DataFrame, cfg: dict, input_rows: dict[str, dict]) -> pd.DataFrame:
    """Create overall/hour buckets and frozen split periods; test 48 4h cells."""
    split = pd.Timestamp(cfg["analysis_split"])
    analysis_start, end = pd.Timestamp(cfg["analysis_start"]), pd.Timestamp(cfg["end"])
    rows: list[dict] = []
    combinations = [(int(m), a, p) for m in cfg["timeframes"] for a in cfg["arms"] for p in cfg["policies"]]
    for minutes, arm, policy in combinations:
        target = trades[(trades.timeframe_min == minutes) & trades.arm.eq(arm) & trades.policy.eq(policy)]
        random = controls[(controls.timeframe_min == minutes) & controls.arm.eq(arm) & controls.policy.eq(policy)]
        for kind in KINDS:
            buckets = [0] if kind == "overall" else list(range(0, 24, 4)) if kind == "4h" else list(range(24))
            pair_by_period = {period: entry_clock.pair_rows(target, random, kind, period, split) for period in PERIODS}
            target_bucket = _entry_bucket(target, kind)
            for bucket in buckets:
                subset = target[target_bucket == bucket]
                earlier_closed = int((entry_clock.cohort(subset, "earlier", split).status == "closed").sum())
                later_closed = int((entry_clock.cohort(subset, "later", split).status == "closed").sum())
                for period in PERIODS:
                    pairs = pair_by_period[period]
                    pairs = pairs[pairs.bucket == bucket]
                    cohort = entry_clock.cohort(subset, period, split)
                    row = {"timeframe_min": minutes, "arm": arm, "policy": policy,
                           "kind": kind, "bucket": bucket, "period": period,
                           "analysis_start": analysis_start, "analysis_split": split,
                           "analysis_end_exclusive": end,
                           "earlier_closed": earlier_closed, "later_closed": later_closed}
                    row |= entry_clock.describe(cohort, cfg)
                    row |= entry_clock.comparator(pairs, cfg, test=(kind == "4h" and period == "all"))
                    row["random_coverage"] = len(pairs) / row["closed"] if row["closed"] else math.nan
                    row["source_available_symbols"] = _source_count_for_window(input_rows, period, analysis_start, split, end)
                    row["source_universe_symbols"] = len(input_rows)
                    row["source_coverage_is_span_only"] = True
                    row["test_scope"] = "same-clock matched random, entry-week block inference" if kind == "4h" and period == "all" else "descriptive same-clock matched random"
                    rows.append(row)
    table = pd.DataFrame(rows)
    primary = table.kind.eq("4h") & table.period.eq("all")
    expected = int(len(cfg["timeframes"]) * len(cfg["arms"]) * len(cfg["policies"]) * 6)
    if int(primary.sum()) != expected or expected != int(cfg["primary_test_slots"]):
        raise ValueError(f"unexpected primary family size: {int(primary.sum())} != {cfg['primary_test_slots']}")
    if "p_one_sided" not in table:
        table["p_one_sided"] = np.nan
    table.loc[primary, "holm_p_48_slots"] = entry_clock.holm(table.loc[primary, "p_one_sided"])
    return table


def _source_count_for_window(input_rows: dict[str, dict], period: str, start: pd.Timestamp, split: pd.Timestamp, end: pd.Timestamp) -> int:
    if period == "all":
        left, right = start, end
    elif period in ("earlier", "cross"):
        left, right = start, split
    elif period == "later":
        left, right = split, end
    else:
        raise ValueError(period)
    return _source_span(input_rows, left, right)


def build_winners(table: pd.DataFrame, trades: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Reuse the prior clock winner and early-selected/later-display rules."""
    winners, selected = [], []
    groups = table[table.kind.isin(("4h", "1h"))]
    for (minutes, arm, policy, kind), group in groups.groupby(["timeframe_min", "arm", "policy", "kind"], sort=True):
        meta = {"timeframe_min": int(minutes), "arm": arm, "policy": policy, "kind": kind}
        for metric in entry_clock.METRICS:
            full_winner = entry_clock.choose(group[group.period.eq("all")], metric, cfg)
            if full_winner is None:
                winners.append(meta | {"metric": metric, "eligible": False})
            else:
                sample = trades[(trades.timeframe_min == minutes) & trades.arm.eq(arm) & trades.policy.eq(policy)]
                sample = sample[_entry_bucket(sample, kind) == full_winner.bucket]
                winners.append(full_winner.to_dict() | {"metric": metric, "eligible": True,
                    "selection_scope": "full analysis history; retrospective diagnostic, not a validated gate"} |
                    entry_clock.sensitivity(sample))

            earlier = entry_clock.choose(group[group.period.eq("earlier")], metric, cfg, earlier=True)
            row = meta | {"metric": metric, "eligible": earlier is not None,
                          "selection_scope": "earlier cohort; post-split is retrospective, not an untouched validation set"}
            if earlier is not None:
                later = group[(group.period == "later") & (group.bucket == earlier.bucket)].iloc[0]
                later_overall = table[(table.timeframe_min == minutes) & table.arm.eq(arm) & table.policy.eq(policy) &
                                      table.kind.eq("overall") & table.period.eq("later")].iloc[0]
                row["bucket"] = earlier.bucket
                for prefix, item in (("earlier", earlier), ("later", later), ("later_overall", later_overall)):
                    for name in ("closed", *entry_clock.METRICS, "matched", "random_mean_net_bp", "mean_excess_bp"):
                        row[f"{prefix}_{name}"] = item.get(name, math.nan)
            selected.append(row)
    return pd.DataFrame(winners), pd.DataFrame(selected)


def _add_period_column(frame: pd.DataFrame, timezone: str) -> pd.DataFrame:
    out = add_calendar_columns(frame, timezone)
    return out


def _calendar_pairs(target: pd.DataFrame, random: pd.DataFrame, kind: str, label: str) -> pd.DataFrame:
    """Pair only after both sides satisfy the same BJT month/year and clock bucket."""
    column = "entry_month" if label.count("-") == 1 else "entry_year"
    targets = target[target[column].astype(str).eq(str(label))]
    controls = random[random[column].astype(str).eq(str(label))]
    split = pd.Timestamp("1970-01-01T00:00:00Z")
    return entry_clock.pair_rows(targets, controls, kind, "all", split)


def build_calendar_table(
    trades: pd.DataFrame,
    controls: pd.DataFrame,
    cfg: dict,
    input_rows: dict[str, dict],
) -> pd.DataFrame:
    """Summarize BJT entry months/years with same-period clock-matched controls."""
    timezone = cfg["timezone"]
    start, end = pd.Timestamp(cfg["analysis_start"]), pd.Timestamp(cfg["end"])
    trades = _add_period_column(trades, timezone)
    controls = _add_period_column(controls, timezone)
    rows: list[dict] = []
    combos = [(int(m), a, p) for m in cfg["timeframes"] for a in cfg["arms"] for p in cfg["policies"]]
    for period_kind in ("month", "year"):
        labels = calendar_labels(start, end, timezone, period_kind)
        column = "entry_month" if period_kind == "month" else "entry_year"
        for minutes, arm, policy in combos:
            target_group = trades[(trades.timeframe_min == minutes) & trades.arm.eq(arm) & trades.policy.eq(policy)]
            random_group = controls[(controls.timeframe_min == minutes) & controls.arm.eq(arm) & controls.policy.eq(policy)]
            for label in labels:
                period_target = target_group[target_group[column].eq(label)]
                period_random = random_group[random_group[column].eq(label)]
                for kind in CALENDAR_KINDS:
                    buckets = [0] if kind == "overall" else list(range(0, 24, 4))
                    target_bucket = _entry_bucket(period_target, kind)
                    for bucket in buckets:
                        target = period_target[target_bucket == bucket]
                        random_bucket = _entry_bucket(period_random, kind)
                        random = period_random[random_bucket == bucket]
                        pairs = _calendar_pairs(target, random, kind, label)
                        row = {"period_kind": period_kind, "period": label,
                               "timeframe_min": minutes, "arm": arm, "policy": policy,
                               "kind": kind, "bucket": bucket,
                               "analysis_start": start, "analysis_end_exclusive": end,
                               "entry_period_controls_same_period": True}
                        row |= entry_clock.describe(target, cfg)
                        row |= entry_clock.comparator(pairs, cfg, test=False)
                        row["random_coverage"] = len(pairs) / row["closed"] if row["closed"] else math.nan
                        period_start, period_end = calendar_bounds(label, period_kind, timezone)
                        span_count = _source_span(input_rows, period_start.tz_convert("UTC"), period_end.tz_convert("UTC"))
                        row["source_available_symbols"] = span_count
                        row["source_universe_symbols"] = len(input_rows)
                        row["source_coverage_is_span_only"] = True
                        row |= calendar_period_counts(target, period_kind, label, timezone, end, start)
                        rows.append(row)
    return pd.DataFrame(rows)


def build_fixed_incumbent_summary(trades: pd.DataFrame, controls: pd.DataFrame, cfg: dict, input_rows: dict[str, dict]) -> pd.DataFrame:
    """Sensitivity on the pre-analysis-start source universe, including first-month removal."""
    fixed = fixed_incumbent_symbols(input_rows, cfg["analysis_start"])
    timezone = cfg["timezone"]
    trades = _add_period_column(trades[trades.symbol.isin(fixed)], timezone)
    controls = _add_period_column(controls[controls.symbol.isin(fixed)], timezone)
    first_month = pd.Timestamp(cfg["analysis_start"]).tz_convert(timezone).strftime("%Y-%m")
    scopes = (
        ("all_fixed_incumbents", trades, controls),
        ("excluding_first_analysis_month", trades[trades.entry_month.ne(first_month)], controls[controls.entry_month.ne(first_month)]),
    )
    split = pd.Timestamp(cfg["analysis_split"])
    start, end = pd.Timestamp(cfg["analysis_start"]), pd.Timestamp(cfg["end"])
    rows = []
    for scope, scope_trades, scope_controls in scopes:
        for minutes in cfg["timeframes"]:
            for arm in cfg["arms"]:
                for policy in cfg["policies"]:
                    target = scope_trades[(scope_trades.timeframe_min == minutes) & scope_trades.arm.eq(arm) & scope_trades.policy.eq(policy)]
                    random = scope_controls[(scope_controls.timeframe_min == minutes) & scope_controls.arm.eq(arm) & scope_controls.policy.eq(policy)]
                    for kind in ("overall", "4h"):
                        buckets = [0] if kind == "overall" else list(range(0, 24, 4))
                        bucket_values = _entry_bucket(target, kind)
                        for bucket in buckets:
                            subset = target[bucket_values == bucket]
                            for period in PERIODS:
                                row = _row_metrics(subset, random, kind, period, split, cfg,
                                    {"scope": scope, "timeframe_min": int(minutes), "arm": arm, "policy": policy,
                                     "kind": kind, "bucket": bucket, "period": period,
                                     "analysis_start": start, "analysis_split": split, "analysis_end_exclusive": end},
                                    test=False)
                                row["fixed_incumbent_symbols"] = len(fixed)
                                row["run_universe_symbols"] = len(input_rows)
                                row["fixed_universe_coverage"] = len(fixed) / len(input_rows) if input_rows else math.nan
                                row["fixed_universe_rule"] = "merged source first_ms < analysis_start; not a point-in-time universe"
                                row["first_analysis_month_removed"] = scope != "all_fixed_incumbents"
                                rows.append(row)
    return pd.DataFrame(rows)


def _candidate_period_rows(
    target: pd.DataFrame,
    random: pd.DataFrame,
    candidate: dict,
    cfg: dict,
    period_kind: str,
    period_label: str,
    split: pd.Timestamp,
) -> dict:
    kind = candidate["kind"]
    bucket = candidate["bucket"]
    target = target[_entry_bucket(target, kind) == bucket]
    random = random[_entry_bucket(random, kind) == bucket]
    if period_kind == "window":
        period = period_label
        pairs = entry_clock.pair_rows(target, random, kind, period, split)
        sample = entry_clock.cohort(target, period, split)
    else:
        target = _add_period_column(target, cfg["timezone"])
        random = _add_period_column(random, cfg["timezone"])
        pairs = _calendar_pairs(target, random, kind, period_label)
        col = "entry_month" if period_kind == "month" else "entry_year"
        sample = target[target[col].eq(period_label)]
    metrics = entry_clock.describe(sample, cfg)
    compared = entry_clock.comparator(pairs, cfg, test=False)
    return metrics | compared | {"random_coverage": len(pairs) / metrics["closed"] if metrics["closed"] else math.nan}


def build_historical_incumbents(trades: pd.DataFrame, controls: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Recheck frozen historical clock choices without selecting new winners."""
    timezone, old_start = cfg["timezone"], pd.Timestamp(cfg["old_window_start"])
    start, end = pd.Timestamp(cfg["analysis_start"]), pd.Timestamp(cfg["end"])
    trades = _add_period_column(trades, timezone)
    controls = _add_period_column(controls, timezone)
    rows: list[dict] = []
    for candidate in FIXED_CLOCK_CANDIDATES:
        target = trades[(trades.timeframe_min == candidate["timeframe_min"]) &
                        trades.arm.eq(candidate["arm"]) & trades.policy.eq(candidate["policy"])]
        random = controls[(controls.timeframe_min == candidate["timeframe_min"]) &
                          controls.arm.eq(candidate["arm"]) & controls.policy.eq(candidate["policy"])]
        for period in ("all", "earlier", "later", "cross"):
            values = _candidate_period_rows(target, random, candidate, cfg, "window", period,
                                            old_start if period != "all" else pd.Timestamp(cfg["analysis_split"]))
            rows.append(candidate | values | {"period_kind": "old_window_cut", "period": {
                "all": "all", "earlier": "earlier_history_mature", "later": "old_window", "cross": "crossing_old_window_cut"}[period],
                "selection_source": "fixed pre-expansion clock observation", "validated": False,
                "analysis_start": start, "old_window_start": old_start, "analysis_end_exclusive": end})
        for period_kind in ("year", "month"):
            for label in calendar_labels(start, end, timezone, period_kind):
                values = _candidate_period_rows(target, random, candidate, cfg, period_kind, label, old_start)
                candidate_target = target[_entry_bucket(target, candidate["kind"]) == candidate["bucket"]]
                values |= calendar_period_counts(candidate_target, period_kind, label, timezone, end, start)
                rows.append(candidate | values | {"period_kind": period_kind, "period": label,
                    "selection_source": "fixed pre-expansion clock observation", "validated": False,
                    "analysis_start": start, "old_window_start": old_start, "analysis_end_exclusive": end})
    return pd.DataFrame(rows)


def build_symbol_coverage(input_rows: dict[str, dict], analysis_start: pd.Timestamp) -> pd.DataFrame:
    fixed = fixed_incumbent_symbols(input_rows, analysis_start)
    rows = []
    for symbol, row in sorted(input_rows.items()):
        rows.append({"symbol": symbol,
            "first_source_utc": pd.to_datetime(int(row["first_ms"]), unit="ms", utc=True),
            "last_source_utc": pd.to_datetime(int(row["last_ms"]), unit="ms", utc=True),
            "source_gap_count": int(row.get("gap_count", 0)),
            "fixed_incumbent_before_analysis_start": symbol in fixed,
            "coverage_rule": "span metadata only; does not prove every bar was present or ready"})
    return pd.DataFrame(rows)


def declared_sources(config_path: str | Path) -> dict[str, str]:
    paths = tuple(dict.fromkeys((
        Path(__file__), Path(config_path), Path("tests/evaluation/test_spike_v128_long_clock.py"),
        *_local_transitive_python((Path(__file__),)),
    )))
    if not _committed(paths):
        raise ValueError("commit the summary module, tests, config, and source dependencies before summarizing")
    return {str(path): digest(path) for path in paths}


def _write_summary(auth: dict, output: Path) -> dict:
    cfg = auth["cfg"]
    trades, controls = prepare_ledgers(auth["trades_all"], auth["controls_all"], cfg)
    input_rows = auth["input_rows"]
    buckets = build_bucket_table(trades, controls, cfg, input_rows)
    winners, selected = build_winners(buckets, trades, cfg)
    calendars = build_calendar_table(trades, controls, cfg, input_rows)
    fixed = build_fixed_incumbent_summary(trades, controls, cfg, input_rows)
    historical = build_historical_incumbents(trades, controls, cfg)
    coverage = build_symbol_coverage(input_rows, pd.Timestamp(cfg["analysis_start"]))
    tables = {
        "buckets.csv": buckets,
        "winners.csv": winners,
        "earlier_selected_later.csv": selected,
        "calendar_periods.csv": calendars,
        "fixed_incumbent_summary.csv": fixed,
        "historical_incumbents.csv": historical,
        "symbol_coverage.csv": coverage,
    }
    output.mkdir(parents=True, exist_ok=False)
    for name, frame in tables.items():
        frame.to_csv(output / name, index=False)
    receipts = {
        "config_sha256": auth["config_sha256"],
        "replay_identity_sha256": auth["run_identity_sha256"],
        "replay_manifest_sha256": auth["run_manifest_sha256"],
        "parent_identity_sha256": auth["parent_identity_sha256"],
        "parent_manifest_sha256": auth["parent_manifest_sha256"],
        "input_manifest_sha256": auth["input_manifest_sha256"],
        "symbol_metadata_snapshot": auth["symbol_meta_source"],
        "upstream_source_manifest": auth["input_manifest"].get("source_manifest"),
        "source_data_files_rehashed": False,
        "input_hashes_checked_against_manifest": True,
        "parent_stream_receipts": auth["parent_receipt_sha256"],
        "retest_stream_receipts": {key: auth["manifest"]["receipts"][key]
                                    for key in sorted(auth["manifest"]["receipts"])},
    }
    receipt = {
        "status": "complete",
        "config": cfg,
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "run_identity": auth["manifest"]["run_identity"],
        "parent_run": cfg["parent_run"],
        "source_hashes": auth["source_hashes"],
        "authenticated_inputs": receipts,
        "stream_count": len(auth["manifest"]["receipts"]),
        "trade_rows_in_replay": len(auth["trades_all"]),
        "trade_rows_in_analysis": len(trades),
        "closed_analysis_trades": int(trades.status.eq("closed").sum()),
        "censored_analysis_trades": int(trades.status.ne("closed").sum()),
        "control_rows_in_replay": len(auth["controls_all"]),
        "control_rows_in_analysis": len(controls),
        "fixed_incumbent_symbols": sorted(fixed_incumbent_symbols(input_rows, cfg["analysis_start"])),
        "warnings": [
            "Calendar performance is assigned by actual Beijing entry time; calendar controls are restricted to the same month/year and clock bucket.",
            "Monthly/yearly net outcomes include later exits; period_end_known_closed separately counts outcomes closed before that entry period ended.",
            "The final partial calendar period is marked incomplete and may be right-censored.",
            "Historical clock winners are fixed candidates; no winner is validated or promoted by this summary.",
            "Earlier-selected/later-displayed results are retrospective time cohorts, not an untouched validation set.",
            "Source availability counts use first/last span metadata and do not assert continuous ready bars or a point-in-time universe.",
        ],
        "files": {name: digest(output / name) for name in sorted(tables)},
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    return receipt


def build(config_path: str | Path, run_path: str | Path, output_path: str | Path) -> dict:
    """Authenticate a completed replay and atomically write compact CSV summaries."""
    output = Path(output_path)
    if output.exists():
        raise ValueError("summary output exists; preserve prior results and choose a new path")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f".{output.name}.staging")
    if staging.exists():
        raise ValueError("incomplete staging summary exists; preserve it for inspection")
    sources = declared_sources(config_path)
    auth = authenticate(config_path, run_path)
    auth["source_hashes"] = sources
    try:
        receipt = _write_summary(auth, staging)
        staging.replace(output)
    except Exception:
        # A failed staging directory is retained as evidence, never silently reused.
        raise
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True, help="completed retest replay directory")
    parser.add_argument("--output", type=Path, required=True, help="new summary output directory")
    args = parser.parse_args()
    result = build(args.config, args.run, args.output)
    print(json.dumps({"output": str(args.output), "files": result["files"],
                      "trade_rows_in_analysis": result["trade_rows_in_analysis"]}, ensure_ascii=False))
