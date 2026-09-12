"""Replay V1, V6 and V7 on the frozen two-year covered-market receipts.

This is a receipt-bound research runner, not an all-market collector.  It reads
only cells already marked ``evaluated`` by the V1 two-year coverage ledger,
keeps the current-catalog denominator beside the covered subset, and never
fetches, fills a gap, or promotes a result.  V1's historical native ledger is
reported as a separately hashed reference.  The common-execution rows use the
V6 next-open 5-bar/2R/4ATR model so V1, V6 and V7 signal selection can be
compared without calling that model V1's native execution.

V7 is V6 admission filtered by a causal BB200 compression background: width is
``4 * population_std(close, 200) / abs(SMA(close, 200))``; the threshold is
the preceding 500 widths' P10.  Every one of the preceding twelve bars must
have a defined threshold, and a complete run of three compressed bars must
fall in that preceding window.  The signal bar is excluded.  Raw, unfiltered
opposite V6 events remain exits even when their own entry is filtered out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_burst_replay import features as v1_features, replay as replay_v1
from yoyo.evaluation.spike_v1_twoyear_allmarkets import END, START, _continuous, _read_utc_source, aggregate
from yoyo.evaluation.spike_v6_bb_squeeze import features as bb_features, recent_compression
from yoyo.evaluation.spike_v6_wvf_study import (
    ExecutionSpec,
    _data_gap,
    simulate_v6_variant,
    summarize_account,
    v6_signals,
)

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / "experiments/active/exp-spike-v7-v1-compare-20260912-v1"
SOURCE_EXP = ROOT / "experiments/active/exp-spike-v1-twoyear-allmarkets-20260911-v1"
SOURCE_DATA = SOURCE_EXP / "data"
SOURCE_RESULTS = SOURCE_EXP / "results"
CONFIG_PATH = EXP / "config.json"
TIMEFRAMES = (30, 60, 240)
VARIANTS = ("v1_common_execution_long", "v6_unfiltered_long", "v7_bb_long",
            "v6_unfiltered_both", "v7_bb_both", "v6_common_ready_long",
            "v6_common_ready_both", "v1_common_ready_long")
RUNNER_SOURCE_FILES = (
    Path(__file__).resolve(),
    ROOT / "yoyo/evaluation/spike_burst_replay.py",
    ROOT / "yoyo/evaluation/spike_v6_wvf_study.py",
    ROOT / "yoyo/evaluation/spike_v6_bb_squeeze.py",
    ROOT / "yoyo/evaluation/spike_v1_twoyear_allmarkets.py",
)
STREAM_REQUIRED_FILES = (
    "signals.csv.gz", "trades.csv.gz", "control_cache.pkl.gz", "control_cache.receipt.json",
    "signal_summary.csv", "trade_summary.csv", "tail_retention.csv", "conflicts.csv", "receipt.csv",
)


def sha256(path: Path) -> str:
    """Return a file digest used to pin every reused artifact."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path: Path, value: object) -> None:
    """Atomically save a small, human-readable receipt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def _json_digest(value: object) -> str:
    """Return a stable digest for a small JSON-compatible identity object."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def run_identity(config: dict) -> dict:
    """Bind a resumable result directory to its config, Pine pin and builders."""
    pine_sha = config.get("pine_sha256")
    if not isinstance(pine_sha, str) or len(pine_sha) != 64:
        raise ValueError("config requires a pinned Pine SHA256")
    identity = {
        "config_sha256": sha256(CONFIG_PATH),
        "pine_sha256": pine_sha,
        "source_code_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in RUNNER_SOURCE_FILES},
    }
    return {**identity, "identity_sha256": _json_digest(identity)}


def ensure_run_identity(output: Path, identity: dict) -> None:
    """Create or validate the immutable identity receipt before a resume."""
    output.mkdir(parents=True, exist_ok=True)
    path = output / "run_identity.json"
    if path.is_file():
        recorded = json.loads(path.read_text())
        if recorded != identity:
            raise ValueError("run identity changed; refusing to resume this output directory")
        return
    # A directory left by a failed preflight has no input manifest or stream
    # material and can safely be retried.  Any actual prior material without
    # an identity receipt is ambiguous and must be inspected rather than
    # silently reclassified as this builder/configuration.
    evidence = (output / "input_manifest.json", output / "progress.jsonl", output / "manifest.json", output / "streams")
    if any(item.exists() for item in evidence):
        raise ValueError("existing output has prior material but no run_identity.json")
    atomic_json(path, identity)


def _tick(value: object) -> float:
    tick = float(value)
    if not math.isfinite(tick) or tick <= 0:
        raise ValueError("covered stream has no positive finite tick")
    return tick


def _catalog_tick_map(catalog: pd.DataFrame) -> dict[tuple[str, str], float]:
    """Return usable ticks; covered cells still fail closed when one is absent.

    The historical current catalog retains unrelated eligible rows with blank
    ticks.  They are outside this frozen coverage subset and must not prevent
    inspection of a separate evaluated receipt.  A selected row with no tick
    remains an error at the point it is selected below.
    """
    ticks = {}
    for row in catalog.itertuples():
        if not bool(row.eligible):
            continue
        try:
            ticks[(str(row.venue), str(row.symbol))] = _tick(row.tick)
        except (TypeError, ValueError):
            continue
    return ticks


def v7_diagnostics(bars: pd.DataFrame, *, data_gap: pd.Series) -> pd.DataFrame:
    """Calculate the V7 BB background using only this bar and its past.

    ``threshold_ready_prior12`` deliberately requires readiness at every bar
    t-12 through t-1.  Since each threshold itself uses widths t-500 through
    t-1, the first eligible signal is bar 712 of an uninterrupted segment.
    """
    diagnostic = bb_features(bars, data_gap=data_gap)
    threshold_ready = diagnostic.bb_width_p10_prior500.notna()
    diagnostic["threshold_ready_prior12"] = (
        threshold_ready.shift(1).rolling(12, min_periods=12).min().eq(1)
    )
    diagnostic["prior_squeeze_run3"] = recent_compression(diagnostic.bb_compressed)
    diagnostic["v7_ready"] = diagnostic.threshold_ready_prior12.astype(bool)
    return diagnostic


def v7_admission(signals: pd.DataFrame, diagnostic: pd.DataFrame) -> pd.Series:
    """Return a side-neutral V7 admission mask for raw V6 events only."""
    if not signals.index.equals(diagnostic.index):
        raise ValueError("signals and BB diagnostic clocks must agree")
    if not {"long_signal", "short_signal"}.issubset(signals):
        raise ValueError("V6 long_signal and short_signal columns are required")
    raw = signals.long_signal.fillna(False).astype(bool) | signals.short_signal.fillna(False).astype(bool)
    return (raw & diagnostic.v7_ready.astype(bool) & diagnostic.prior_squeeze_run3.astype(bool)).astype(bool)


def v1_common_signals(bars: pd.DataFrame, tick: float) -> pd.DataFrame:
    """Map frozen V1 long entries into the shared-execution signal contract."""
    native = replay_v1(bars, tick)
    return pd.DataFrame({"long_signal": native.burst.astype(bool), "short_signal": False}, index=bars.index)


def v1_common_with_v6_exit_feed(v1: pd.DataFrame, raw_v6_short: pd.Series) -> tuple[pd.DataFrame, pd.Series]:
    """Install the raw V6 long-exit feed and expose same-bar V1 conflicts.

    The shared simulator forbids a bar from carrying two sides.  More
    importantly, a raw V6 short must close an existing common-execution long,
    rather than also opening a fresh V1 long on that same confirmation.  The
    archived native V1 ledger does not use this adapter and remains unchanged.
    """
    if not v1.index.equals(raw_v6_short.index):
        raise ValueError("V1 and raw V6 short clocks must agree")
    out = v1.copy()
    native_long = out.long_signal.fillna(False).astype(bool)
    exit_feed = raw_v6_short.fillna(False).astype(bool)
    conflicts = native_long & exit_feed
    out["native_v1_long_event"] = native_long
    out["raw_v6_short_exit_feed"] = exit_feed
    out["suppressed_v1_entry_conflict"] = conflicts
    out["long_signal"] = native_long & ~exit_feed
    out["short_signal"] = exit_feed
    return out, conflicts


def _source_maps() -> tuple[dict[tuple[str, str], dict], dict[tuple[str, str, int], dict]]:
    market, gate = {}, {}
    for path in (SOURCE_DATA / "market_receipts").glob("*/*.json"):
        item = json.loads(path.read_text())
        market[(str(item["venue"]), str(item["symbol"]))] = item
    for path in (SOURCE_DATA / "gate_timeframe_receipts").glob("*.json"):
        item = json.loads(path.read_text())
        gate[("gate", str(item["symbol"]), int(item["minutes"]))] = item
    return market, gate


def _coverage_receipt(venue: str, symbol: str, minutes: int) -> dict:
    """Load V1's per-cell source hash; a current file hash alone is not proof."""
    path = SOURCE_RESULTS / "covered_ledgers" / venue / f"{symbol}_{minutes}m.csv.receipt.json"
    if not path.is_file():
        raise FileNotFoundError(f"missing V1 coverage receipt: {path}")
    receipt = json.loads(path.read_text())
    expected = receipt.get("source_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError(f"V1 coverage receipt lacks source SHA256: {path}")
    return {"path": str(path.resolve()), "sha256": sha256(path), "source_sha256": expected}


def evaluation_window(index: pd.DatetimeIndex, minutes: int) -> pd.Series:
    """Mark confirmations in [START, END), using a bar's close as its clock.

    Features may consume authenticated history before ``START``.  Admissions
    may not: a signal observed before the frozen start must not create a
    carried position.  The final close at ``END`` is likewise unavailable, so
    a signal confirming there is excluded and any open trade is censored.
    """
    step = pd.Timedelta(minutes=minutes)
    return pd.Series((index + step >= START) & (index + step < END), index=index)


def covered_streams(*, limit: int | None = None, frozen: dict[str, dict] | None = None) -> Iterable[dict]:
    """Yield only receipt-pinned V1-evaluated cells for 30m/1H/4H.

    Binance and OKX have verified 30m receipts and are aggregated locally into
    complete UTC bars.  Gate's receipt is native to each timeframe, so it is
    never re-aggregated as though it were a 30m source.
    """
    coverage_path = SOURCE_RESULTS / "coverage_limited.csv"
    catalog_path = SOURCE_DATA / "catalog.json"
    if not coverage_path.is_file() or not catalog_path.is_file():
        raise FileNotFoundError("frozen V1 two-year coverage artifacts are required")
    coverage = pd.read_csv(coverage_path)
    coverage = coverage.loc[coverage.status.eq("evaluated") & coverage.timeframe_min.isin(TIMEFRAMES)].copy()
    catalog = pd.read_json(catalog_path)
    tick_map = _catalog_tick_map(catalog)
    market, gate = _source_maps()
    emitted = 0
    for row in coverage.sort_values(["venue", "symbol", "timeframe_min"]).itertuples(index=False):
        venue, symbol, minutes = str(row.venue), str(row.symbol), int(row.timeframe_min)
        tick = tick_map.get((venue, symbol))
        if tick is None:
            raise ValueError(f"evaluated cell lacks catalog tick: {venue}:{symbol}")
        if venue == "gate":
            receipt = gate.get((venue, symbol, minutes))
            if receipt is None or receipt.get("status") not in ("complete", "partial"):
                raise ValueError(f"evaluated Gate cell lacks usable receipt: {symbol}/{minutes}")
            path = Path(receipt["path"])
            source = _read_utc_source(path)
        else:
            receipt = market.get((venue, symbol))
            if receipt is None or receipt.get("status") != "complete":
                raise ValueError(f"evaluated cell lacks complete receipt: {venue}:{symbol}")
            path = Path(receipt["path"])
            source = aggregate(_read_utc_source(path), minutes)
        if not path.is_file():
            raise FileNotFoundError(path)
        source_sha = sha256(path)
        coverage_receipt = _coverage_receipt(venue, symbol, minutes)
        if source_sha != coverage_receipt["source_sha256"]:
            raise ValueError(f"frozen V1 source hash drift: {venue}:{symbol}:{minutes}")
        for segment_i, segment in enumerate(_continuous(source, minutes)):
            if len(segment) < 341:
                continue
            segment.attrs["minutes"] = minutes
            item = dict(venue=venue, symbol=symbol, asset=str(row.asset), minutes=minutes, tick=tick,
                        source_path=str(path.resolve()), source_sha256=source_sha, segment=segment_i, bars=segment,
                        coverage_receipt=coverage_receipt)
            key = stream_key(item)
            if frozen is not None:
                expected = frozen.get(key)
                actual = {name: item[name] for name in (
                    "source_path", "source_sha256", "minutes", "segment",
                )}
                actual.update({
                    "coverage_receipt_path": coverage_receipt["path"],
                    "coverage_receipt_sha256": coverage_receipt["sha256"],
                    "coverage_receipt_source_sha256": coverage_receipt["source_sha256"],
                })
                if expected != actual:
                    raise ValueError(f"frozen input manifest drift: {key}")
            yield item
            emitted += 1
            if limit is not None and emitted >= limit:
                return


def stream_key(stream: dict) -> str:
    """Stable path-safe identity for one covered source segment."""
    digest = hashlib.sha256(f"{stream['venue']}|{stream['symbol']}|{stream['minutes']}|{stream['segment']}".encode()).hexdigest()[:16]
    return f"{stream['venue']}_{stream['minutes']}m_{digest}"


def _variant_admissions(v1: pd.DataFrame, v6: pd.DataFrame, v7: pd.Series,
                        common_ready: pd.Series) -> dict[str, tuple[pd.DataFrame, pd.Series]]:
    """Build explicit long-only and bidirectional admission variants."""
    v1_long = v1.long_signal.astype(bool)
    v6_raw = v6.long_signal.astype(bool) | v6.short_signal.astype(bool)
    return {
        "v1_common_execution_long": (v1, v1_long),
        "v6_unfiltered_long": (v6, v6.long_signal.astype(bool)),
        "v7_bb_long": (v6, v7 & v6.long_signal.astype(bool)),
        "v6_unfiltered_both": (v6, v6_raw),
        "v7_bb_both": (v6, v7),
        "v6_common_ready_long": (v6, v6.long_signal.astype(bool) & common_ready),
        "v6_common_ready_both": (v6, v6_raw & common_ready),
        "v1_common_ready_long": (v1, v1_long & common_ready),
    }


def replay_stream(stream: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    """Replay all eight common-execution variants for one continuous stream."""
    source = stream["bars"].copy()
    featured = v1_features(source)
    featured.attrs["minutes"] = int(stream["minutes"])
    gap = _data_gap(featured, int(stream["minutes"]))
    v6 = v6_signals(featured, int(stream["minutes"]))
    diagnostic = v7_diagnostics(featured, data_gap=gap)
    v7 = v7_admission(v6, diagnostic)
    v1 = v1_common_signals(featured, float(stream["tick"]))
    # Long-only common execution shares the same raw V6 short exit feed.  It
    # does not turn those shorts into V1 entries; their role is labelled below.
    v1, v1_exit_conflicts = v1_common_with_v6_exit_feed(v1, v6.short_signal)
    # Indicators see the whole authenticated prefix.  Selection sees only the
    # frozen close clock; this prevents a pre-window signal from opening a
    # position carried into the evaluation window and censors at END below.
    in_window = evaluation_window(featured.index, int(stream["minutes"]))
    featured = featured.loc[featured.index < END].copy()
    featured.attrs["minutes"] = int(stream["minutes"])
    gap, v6, v1, v7, diagnostic, in_window, v1_exit_conflicts = (
        value.loc[featured.index].copy()
        for value in (gap, v6, v1, v7, diagnostic, in_window, v1_exit_conflicts)
    )
    v6.loc[~in_window, ["long_signal", "short_signal"]] = False
    v1.loc[~in_window, ["long_signal", "short_signal"]] = False
    v7 &= in_window
    common_ready = diagnostic.v7_ready.astype(bool) & in_window
    conflict_clock = featured.index[(in_window & v1_exit_conflicts).to_numpy()]
    conflicts = pd.DataFrame({
        "signal_bar_open": conflict_clock,
        "signal_confirm_time": conflict_clock + pd.Timedelta(minutes=int(stream["minutes"])),
        "native_v1_long_event": True,
        "raw_v6_short_exit_feed": True,
        "suppressed_v1_entry_conflict": True,
        "affected_variants": "v1_common_execution_long|v1_common_ready_long",
    })
    if len(conflicts):
        conflicts["venue"], conflicts["symbol"], conflicts["asset"] = stream["venue"], stream["symbol"], stream["asset"]
        conflicts["timeframe_min"], conflicts["segment"] = int(stream["minutes"]), int(stream["segment"])
        conflicts["source_sha256"] = stream["source_sha256"]
    cache_start = START - pd.Timedelta(minutes=int(stream["minutes"]) * 120)
    cache = {"bars": featured.loc[(featured.index >= cache_start) & (featured.index < END)].copy(),
             "signals": v6.loc[(v6.index >= cache_start) & (v6.index < END)].copy(),
             "v1_signals": v1.loc[(v1.index >= cache_start) & (v1.index < END)].copy(),
             "data_gap": gap.loc[(gap.index >= cache_start) & (gap.index < END)].copy(),
             "bb_ready": diagnostic.v7_ready.loc[(diagnostic.index >= cache_start) & (diagnostic.index < END)].copy(),
             "tick": float(stream["tick"])}
    signal_tables, trade_tables = [], []
    for variant, (signals, admission) in _variant_admissions(v1, v6, v7, common_ready).items():
        ledger, trades = simulate_v6_variant(
            featured, signals, admission=admission, variant=variant, data_gap=gap,
            spec=ExecutionSpec(tick=float(stream["tick"])),
        )
        ledger["signal_role"] = "entry_candidate"
        if variant.startswith("v1_"):
            ledger.loc[ledger.side.eq(-1), "signal_role"] = "raw_v6_short_exit_feed"
        for table in (ledger, trades):
            if len(table):
                table["venue"], table["symbol"], table["asset"] = stream["venue"], stream["symbol"], stream["asset"]
                table["timeframe_min"], table["segment"] = int(stream["minutes"]), int(stream["segment"])
                table["source_sha256"] = stream["source_sha256"]
        signal_tables.append(ledger)
        trade_tables.append(trades)
    stream_receipt = pd.DataFrame([{
        **{key: stream[key] for key in ("venue", "symbol", "asset", "minutes", "segment", "source_path", "source_sha256")},
        "coverage_receipt_path": stream["coverage_receipt"]["path"],
        "coverage_receipt_sha256": stream["coverage_receipt"]["sha256"],
        "coverage_receipt_source_sha256": stream["coverage_receipt"]["source_sha256"],
        "tick": float(stream["tick"]), "window_start": START.isoformat(), "window_end_exclusive": END.isoformat(),
        "cache_start": cache_start.isoformat(), "bars": len(featured), "v6_raw_events": int((v6.long_signal | v6.short_signal).sum()),
        "v1_long_raw_v6_short_conflicts": int(len(conflicts)),
        "v7_admitted_events": int(v7.sum()), "v7_first_ready_bar": int(diagnostic.v7_ready.to_numpy().argmax()) if diagnostic.v7_ready.any() else None,
    }])
    return (pd.concat(signal_tables, ignore_index=True), pd.concat(trade_tables, ignore_index=True),
            conflicts, stream_receipt, cache)


def _stream_summaries(stream: dict, signals: pd.DataFrame, trades: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Summarize one stream only; never compound unrelated coins into an account."""
    identity = {key: stream[key] for key in ("venue", "symbol", "asset", "minutes", "segment")}
    signal_rows, trade_rows, retention = [], [], []
    for (variant, side), part in signals.groupby(["variant", "side"], dropna=False):
        entries = part.loc[part.signal_role.eq("entry_candidate")]
        signal_rows.append({**identity, "variant": variant, "side": side, "entry_candidates": len(entries),
                            "entry_admitted": int(entries.admitted_for_entry.astype(bool).sum()),
                            "raw_v6_short_exit_feed": int(part.signal_role.eq("raw_v6_short_exit_feed").sum())})
    for (variant, side), part in trades.groupby(["variant", "side"], dropna=False):
        summary = summarize_account(part)
        done = part.loc[~part.censored.astype(bool)]
        # These closed-balance fields are valid only for this one stream's
        # sequential simulator; callers must not combine them across streams.
        trade_rows.append({**identity, "variant": variant, "side": side, **summary,
                           "censored": int(part.censored.astype(bool).sum()),
                           "realized_net_r_ge_10": int(done.net_r.ge(10).sum()),
                           "realized_mfe_r_ge_10": int(done.mfe_r.ge(10).sum())})
    keys = ["side", "signal_bar_open"]
    for base, filtered in (("v6_unfiltered_long", "v7_bb_long"),
                           ("v6_unfiltered_both", "v7_bb_both")):
        base_entries = signals.loc[(signals.variant == base) & signals.signal_role.eq("entry_candidate"),
                                   keys + ["admitted_for_entry"]]
        base_trades = trades.loc[(trades.variant == base) & ~trades.censored.astype(bool), keys + ["net_r"]]
        accepted = signals.loc[signals.variant.eq(filtered), keys + ["admitted_for_entry"]].rename(
            columns={"admitted_for_entry": "v7_admitted"})
        entry_joined = base_entries.merge(accepted, on=keys, how="left")
        joined = base_trades.merge(accepted, on=keys, how="left")
        baseline_admitted = entry_joined.admitted_for_entry.astype(bool)
        same_admitted = baseline_admitted & entry_joined.v7_admitted.fillna(False).astype(bool)
        retention.append({**identity, "baseline": base, "filtered": filtered,
                          "baseline_entry_admitted": int(baseline_admitted.sum()),
                          "same_entry_v7_admitted": int(same_admitted.sum()),
                          "same_entry_v7_admission_rate": (float(same_admitted.sum() / baseline_admitted.sum())
                                                             if baseline_admitted.any() else math.nan),
                          "baseline_realized_trades": int(len(joined)),
                          "baseline_realized_net_r_ge_10": int(joined.net_r.ge(10).sum()),
                          "same_realized_trade_v7_admitted": int((joined.net_r.ge(10) & joined.v7_admitted.fillna(False)).sum())})
    return pd.DataFrame(signal_rows), pd.DataFrame(trade_rows), pd.DataFrame(retention)


def _native_v1_reference(output: Path) -> dict:
    """Copy no native data; pin and summarize the archived V1 ledger instead."""
    path = SOURCE_RESULTS / "covered_trade_ledger.csv.gz"
    if not path.is_file():
        raise FileNotFoundError(path)
    ledger = pd.read_csv(path)
    ledger = ledger.loc[ledger.timeframe_min.isin(TIMEFRAMES)].copy()
    realized = ledger.loc[~ledger.censored.astype(bool)].copy()
    summary = realized.groupby(["venue", "timeframe_min"], dropna=False).agg(
        realized_rows=("event_id", "size"), realized_net_return_sum=("net_return", "sum")
    ).reset_index()
    censored = ledger.groupby(["venue", "timeframe_min"], dropna=False).censored.sum().rename("censored_rows").reset_index()
    summary = summary.merge(censored, on=["venue", "timeframe_min"], how="outer")
    summary.to_csv(output / "historical_v1_native_covered_summary.csv", index=False)
    return {"path": str(path.resolve()), "sha256": sha256(path), "method": "archived_v1_native_covered_ledger",
            "rows_30m_1h_4h": int(len(ledger))}


def _validate_frozen_inputs(config: dict) -> None:
    """Fail closed on all pre-registered inputs and the three-period coverage."""
    inputs = config.get("frozen_inputs")
    if not isinstance(inputs, dict) or len(inputs) != 5:
        raise ValueError("config requires exactly five frozen input receipts")
    for label, item in inputs.items():
        if not isinstance(item, dict):
            raise ValueError(f"invalid frozen input receipt: {label}")
        path, expected = Path(str(item.get("path", ""))), item.get("sha256")
        if not path.is_file() or not isinstance(expected, str) or sha256(path) != expected:
            raise ValueError(f"frozen input hash drift: {label}")
    coverage = pd.read_csv(SOURCE_RESULTS / "coverage_limited.csv")
    selected = coverage.loc[coverage.timeframe_min.isin(TIMEFRAMES)]
    expected = config.get("expected_coverage")
    if not isinstance(expected, dict) or len(selected) != expected.get("catalog_cells") or int(selected.status.eq("evaluated").sum()) != expected.get("evaluated_cells"):
        raise ValueError("frozen three-period coverage denominator/evaluated count drift")


def _input_manifest(*, expected_catalog: int, expected_evaluated: int) -> dict:
    """Freeze every V1 receipt-bound source identity before new outcomes run."""
    streams = []
    for stream in covered_streams():
        streams.append({"key": stream_key(stream), **{name: stream[name] for name in
                        ("source_path", "source_sha256", "minutes", "segment")},
                        "coverage_receipt_path": stream["coverage_receipt"]["path"],
                        "coverage_receipt_sha256": stream["coverage_receipt"]["sha256"],
                        "coverage_receipt_source_sha256": stream["coverage_receipt"]["source_sha256"]})
    if len(streams) != expected_evaluated:
        raise ValueError(f"receipt-bound continuous stream count {len(streams)} != frozen evaluated count {expected_evaluated}")
    return {"coverage_path": str((SOURCE_RESULTS / "coverage_limited.csv").resolve()),
            "coverage_sha256": sha256(SOURCE_RESULTS / "coverage_limited.csv"), "streams": streams,
            "catalog_cells": expected_catalog, "evaluated_cells": expected_evaluated}


def _completed(streams_root: Path, identity_sha256: str) -> set[str]:
    """Return only fully materialized streams for this exact run identity.

    ``progress.jsonl`` is an append-only audit trail, not a completion proof:
    a crash can append it before a file reaches stable storage.  A completion
    receipt and every expected sibling file are required before a stream may
    be skipped on resume.
    """
    completed = set()
    if streams_root.is_dir():
        for completion in streams_root.glob("*/completion.json"):
            if completion.parent.name.startswith("."):
                continue
            item = json.loads(completion.read_text())
            required = all((completion.parent / filename).is_file() for filename in STREAM_REQUIRED_FILES)
            if (item.get("status") == "complete" and isinstance(item.get("key"), str)
                    and completion.parent.name == item["key"]
                    and item.get("run_identity_sha256") == identity_sha256 and required):
                completed.add(item["key"])
    return completed


def _append_progress(path: Path, payload: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")
        handle.flush()


def run(output: Path, *, max_streams: int | None = None) -> None:
    """Resumably materialize receipt-pinned per-stream comparison artifacts."""
    if not CONFIG_PATH.is_file():
        raise FileNotFoundError(CONFIG_PATH)
    config = json.loads(CONFIG_PATH.read_text())
    if config.get("variants") != ["historical_v1_native", *VARIANTS]:
        raise ValueError("pre-registered variant order changed")
    _validate_frozen_inputs(config)
    expected_coverage = config["expected_coverage"]
    identity = run_identity(config)
    if output.exists() and not output.is_dir():
        raise FileExistsError(output)
    ensure_run_identity(output, identity)
    input_path = output / "input_manifest.json"
    if input_path.exists():
        inputs = json.loads(input_path.read_text())
        if inputs.get("coverage_sha256") != sha256(SOURCE_RESULTS / "coverage_limited.csv"):
            raise ValueError("coverage source changed after input manifest freeze")
    else:
        output.mkdir(parents=True, exist_ok=True)
        inputs = _input_manifest(expected_catalog=int(expected_coverage["catalog_cells"]),
                                 expected_evaluated=int(expected_coverage["evaluated_cells"]))
        atomic_json(input_path, inputs)
    if (inputs.get("catalog_cells"), inputs.get("evaluated_cells")) != (
            expected_coverage["catalog_cells"], expected_coverage["evaluated_cells"]):
        raise ValueError("existing input manifest does not match frozen coverage counts")
    frozen = {row["key"]: {key: row[key] for key in (
                  "source_path", "source_sha256", "minutes", "segment",
                  "coverage_receipt_path", "coverage_receipt_sha256", "coverage_receipt_source_sha256",
              )}
              for row in inputs["streams"]}
    progress = output / "progress.jsonl"
    streams_root = output / "streams"
    completed, processed = _completed(streams_root, identity["identity_sha256"]), 0
    for stream in covered_streams(frozen=frozen):
        key = stream_key(stream)
        if key in completed:
            continue
        signals, trades, conflicts, receipt, cache = replay_stream(stream)
        streams_root.mkdir(parents=True, exist_ok=True)
        folder = streams_root / key
        if folder.exists():
            raise FileExistsError(f"incomplete stream directory requires inspection: {folder}")
        staging = streams_root / f".{key}.staging"
        if staging.exists():
            raise FileExistsError(f"stale stream staging directory requires inspection: {staging}")
        staging.mkdir()
        signals.to_csv(staging / "signals.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        trades.to_csv(staging / "trades.csv.gz", index=False, compression={"method": "gzip", "mtime": 0})
        # Keep the exact causal context needed by the fixed one-seed control
        # postprocessor: 120 pre-start bars, all in-window bars and raw V6.
        cache_path = staging / "control_cache.pkl.gz"
        pd.to_pickle(cache, cache_path, compression={"method": "gzip", "mtime": 0})
        cache_receipt = {
            "key": key, "cache_file": "control_cache.pkl.gz", "cache_sha256": sha256(cache_path),
            "run_identity_sha256": identity["identity_sha256"],
            "tick": float(stream["tick"]), "cache_start": receipt.loc[0, "cache_start"],
            "window_start": START.isoformat(), "window_end_exclusive": END.isoformat(),
            "source_sha256": stream["source_sha256"],
            "coverage_receipt_sha256": stream["coverage_receipt"]["sha256"],
        }
        atomic_json(staging / "control_cache.receipt.json", cache_receipt)
        receipt["control_cache_file"] = cache_receipt["cache_file"]
        receipt["control_cache_sha256"] = cache_receipt["cache_sha256"]
        signal_summary, trade_summary, retention = _stream_summaries(stream, signals, trades)
        signal_summary.to_csv(staging / "signal_summary.csv", index=False)
        trade_summary.to_csv(staging / "trade_summary.csv", index=False)
        retention.to_csv(staging / "tail_retention.csv", index=False)
        conflicts.to_csv(staging / "conflicts.csv", index=False)
        receipt.to_csv(staging / "receipt.csv", index=False)
        atomic_json(staging / "completion.json", {"key": key, "status": "complete",
                                                    "source_sha256": stream["source_sha256"],
                                                    "cache_sha256": cache_receipt["cache_sha256"],
                                                    "run_identity_sha256": identity["identity_sha256"]})
        staging.replace(folder)
        _append_progress(progress, {"key": key, "source_sha256": stream["source_sha256"],
                                    "run_identity_sha256": identity["identity_sha256"],
                                    "completed_at": pd.Timestamp.now(tz="UTC").isoformat()})
        processed += 1
        if max_streams is not None and processed >= max_streams:
            break
    native = _native_v1_reference(output)
    manifest = {
        "status": "complete" if len(_completed(streams_root, identity["identity_sha256"])) == len(inputs["streams"]) else "partial",
        "config_path": str(CONFIG_PATH.resolve()), "config_sha256": identity["config_sha256"],
        "run_identity": identity,
        "source_coverage_path": str((SOURCE_RESULTS / "coverage_limited.csv").resolve()),
        "source_coverage_sha256": sha256(SOURCE_RESULTS / "coverage_limited.csv"),
        "window": {"start": START.isoformat(), "end_exclusive": END.isoformat(), "timeframes": list(TIMEFRAMES)},
        "covered_streams_frozen": int(len(inputs["streams"])),
        "completed_streams": len(_completed(streams_root, identity["identity_sha256"])),
        "max_streams": max_streams,
        "historical_v1_native": native,
        "source_code_sha256": identity["source_code_sha256"],
        "limitations": "Covered current-catalog subset only; no fetch, gap fill, cross-stream portfolio, funding, impact, or matched-control conclusion.",
    }
    atomic_json(output / "manifest.json", manifest)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new empty result directory")
    parser.add_argument("--max-streams", type=int, help="bounded smoke run only; never a full-coverage claim")
    args = parser.parse_args()
    if args.max_streams is not None and args.max_streams <= 0:
        parser.error("--max-streams must be positive")
    run(args.output, max_streams=args.max_streams)


if __name__ == "__main__":
    main()
