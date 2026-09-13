"""Read-only causal correlation risk accounting for frozen SPIKE event leaders.

The formal cross-venue ledger defines leaders and frozen outcomes.  This module
uses only receipt-verified 30-minute cache bars that closed strictly before a
frozen entry clock to decide the correlation gate.  It never uses a trade's
later return, exit result, or price path to choose an admission.  Frozen trade
outcomes are joined only after that decision for descriptive accounting.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


EXP = Path("experiments/active/exp-spike-correlation-risk-20260913-v1")
FORMAL = Path("experiments/active/exp-spike-cross-venue-events-20260913-v1/results")
REPLAY = Path("experiments/active/exp-spike-v7-v1-compare-20260912-v1/results/replay_two_year_20260912_v3")
ARMS = ("v7", "v8")
POLICIES = (
    "event_leaders_baseline",
    "event_leaders_max_open_5",
    "event_leaders_max_open_5_corr_30d",
)
FORMAL_COLUMNS = [
    "source_event_id", "event_id", "arm", "period", "asset", "side", "timeframe_min",
    "stream_key", "signal_bar_open", "availability_time", "is_event_leader", "executed",
    "trade_id", "censored", "net_r", "net_return",
]
TRADE_COLUMNS = [
    "signal_bar_open", "side", "variant", "entry_time", "exit_time", "entry_price",
    "initial_risk", "net_r", "net_return", "censored", "exit_reason",
]
STREAM_METADATA_COLUMNS = ("venue", "symbol", "asset", "minutes", "segment", "source_sha256")
RESULT_FILES = (
    "summary.csv", "candidate_audit.csv.gz", "correlation_coverage.csv",
    "no_op_policy_comparison.csv", "return_source_selection.csv", "source_rule.json",
)


@dataclass(frozen=True)
class ReturnSource:
    """A deterministic 30-minute cached close-return source for one asset."""

    asset: str
    venue: str
    stream_key: str
    returns: pd.Series


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_sha(value: object) -> str:
    return _sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _bool(values: pd.Series, column: str) -> pd.Series:
    """Parse booleans without silently treating an unknown receipt value as false."""
    if values.dtype == bool:
        return values.astype(bool)
    normalized = values.astype(str).str.lower()
    valid = values.isna() | normalized.isin({"true", "false"})
    if not valid.all():
        raise ValueError(f"nonboolean values in {column}")
    return normalized.eq("true")


def _utc(values: pd.Series, column: str, *, nullable: bool = False) -> pd.Series:
    parsed = pd.to_datetime(values, utc=True, errors="coerce")
    if not nullable and parsed.isna().any():
        raise ValueError(f"{column} must contain finite UTC timestamps")
    return parsed


def _read_config() -> tuple[dict[str, object], Path]:
    path = EXP / "config.json"
    return json.loads(path.read_text()), path


def inventory_caches(streams: Path, config: dict[str, object]) -> dict[str, dict[str, object]]:
    """Verify all authenticated cache receipts and return their stable identity.

    Cache bytes and source-selection metadata are verified before a pickle is
    loaded.  Separate fixed aggregates bind the complete 3,531-stream universe
    so a same-count cache or metadata replacement cannot change the panel.
    """
    inventory: list[dict[str, str]] = []
    metadata_inventory: list[dict[str, str]] = []
    metadata: dict[str, dict[str, object]] = {}
    for folder in sorted(path for path in streams.iterdir() if path.is_dir()):
        receipt_path = folder / "control_cache.receipt.json"
        cache_path = folder / "control_cache.pkl.gz"
        metadata_path = folder / "receipt.csv"
        if not receipt_path.is_file() or not cache_path.is_file() or not metadata_path.is_file():
            raise ValueError(f"stream lacks cache receipt, cache, or metadata: {folder.name}")
        receipt_bytes = receipt_path.read_bytes()
        receipt = json.loads(receipt_bytes)
        if receipt.get("key") != folder.name:
            raise ValueError(f"cache receipt key mismatch: {folder.name}")
        cache_sha = _sha256(cache_path)
        if receipt.get("cache_sha256") != cache_sha:
            raise ValueError(f"cache bytes disagree with receipt: {folder.name}")
        entry = {
            "stream_key": folder.name,
            "receipt_sha256": _sha256_bytes(receipt_bytes),
            "cache_sha256": cache_sha,
        }
        inventory.append(entry)
        metadata_bytes = metadata_path.read_bytes()
        stream_metadata = pd.read_csv(io.BytesIO(metadata_bytes), usecols=STREAM_METADATA_COLUMNS)
        if len(stream_metadata) != 1:
            raise ValueError(f"stream metadata must have one row: {folder.name}")
        row = stream_metadata.iloc[0]
        if any(pd.isna(row[column]) for column in STREAM_METADATA_COLUMNS):
            raise ValueError(f"stream metadata has null identity: {folder.name}")
        venue, asset, symbol = str(row.venue), str(row.asset), str(row.symbol)
        minutes, segment = int(row.minutes), int(row.segment)
        if minutes <= 0 or segment < 0 or not asset or not symbol:
            raise ValueError(f"stream metadata has invalid identity: {folder.name}")
        if not folder.name.startswith(f"{venue}_{minutes}m_"):
            raise ValueError(f"stream metadata disagrees with stream key: {folder.name}")
        if str(row.source_sha256) != receipt.get("source_sha256"):
            raise ValueError(f"stream metadata source identity disagrees with cache receipt: {folder.name}")
        metadata_entry = {"stream_key": folder.name, "receipt_csv_sha256": _sha256_bytes(metadata_bytes)}
        metadata_inventory.append(metadata_entry)
        metadata[folder.name] = {
            **receipt, "folder": folder, **entry, **metadata_entry,
            "venue": venue, "asset": asset, "symbol": symbol, "minutes": minutes, "segment": segment,
        }
    expected = int(config["expected_streams"])
    if len(inventory) != expected:
        raise ValueError(f"expected {expected} cache streams, found {len(inventory)}")
    aggregate = _canonical_sha(inventory)
    if aggregate != config["cache_receipt_aggregate_sha256"]:
        raise ValueError("cache receipt inventory differs from preregistration")
    metadata_aggregate = _canonical_sha(metadata_inventory)
    if metadata_aggregate != config["stream_metadata_aggregate_sha256"]:
        raise ValueError("stream metadata inventory differs from preregistration")
    return metadata


def load_formal_leaders(path: Path, config: dict[str, object]) -> pd.DataFrame:
    """Load formal event leaders and validate the declared availability split."""
    if _sha256(path) != config["formal_event_members_sha256"]:
        raise ValueError("formal event ledger changed")
    frame = pd.read_csv(path, usecols=FORMAL_COLUMNS)
    missing = set(FORMAL_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError("formal event ledger misses columns: " + ", ".join(sorted(missing)))
    frame["signal_bar_open"] = _utc(frame.signal_bar_open, "signal_bar_open")
    frame["availability_time"] = _utc(frame.availability_time, "availability_time")
    frame["side"] = pd.to_numeric(frame.side, errors="raise").astype(int)
    frame["timeframe_min"] = pd.to_numeric(frame.timeframe_min, errors="raise").astype(int)
    for column in ("is_event_leader", "executed", "censored"):
        frame[column] = _bool(frame[column], column)
    for column in ("net_r", "net_return"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if not frame.arm.isin(ARMS).all() or not frame.side.isin((-1, 1)).all():
        raise ValueError("formal event ledger has unsupported arm or side")
    if frame.source_event_id.duplicated().any():
        raise ValueError("formal source event identities must be unique")
    leaders = frame.loc[frame.is_event_leader].copy()
    split = pd.Timestamp(config["development_end_availability_exclusive"])
    expected_period = np.where(leaders.availability_time.lt(split), "development", "validation")
    if not leaders.period.astype(str).eq(expected_period).all():
        raise ValueError("formal period is not partitioned by the availability clock")
    leaders["candidate_key"] = leaders.arm.astype(str) + ":" + leaders.source_event_id.astype(str)
    return leaders


def attach_trade_clocks(leaders: pd.DataFrame, streams: Path) -> tuple[pd.DataFrame, str, int]:
    """Attach exact V3 clocks without allowing V3 outcomes to redefine formal ones."""
    targets = leaders.loc[leaders.executed, ["stream_key", "signal_bar_open", "side"]].drop_duplicates()
    wanted: dict[str, set[tuple[pd.Timestamp, int]]] = {
        key: set(zip(group.signal_bar_open, group.side)) for key, group in targets.groupby("stream_key")
    }
    rows: list[pd.DataFrame] = []
    receipts: list[dict[str, str]] = []
    for stream_key in sorted(wanted):
        path = streams / stream_key / "trades.csv.gz"
        if not path.is_file():
            raise ValueError(f"formal leader stream lacks V3 trades receipt: {stream_key}")
        payload = path.read_bytes()
        receipts.append({"stream_key": stream_key, "trades_sha256": _sha256_bytes(payload)})
        trades = pd.read_csv(path, usecols=TRADE_COLUMNS, compression="gzip")
        if trades.empty:
            continue
        trades = trades.loc[trades.variant.eq("v7_bb_both")].copy()
        trades["signal_bar_open"] = _utc(trades.signal_bar_open, "V3 trade signal_bar_open")
        trades["entry_time"] = _utc(trades.entry_time, "V3 trade entry_time")
        trades["exit_time"] = _utc(trades.exit_time, "V3 trade exit_time")
        trades["side"] = pd.to_numeric(trades.side, errors="raise").astype(int)
        trades["censored"] = _bool(trades.censored, "V3 trade censored")
        for column in ("entry_price", "initial_risk", "net_r", "net_return"):
            trades[column] = pd.to_numeric(trades[column], errors="coerce")
        trades = trades.loc[trades.apply(lambda row: (row.signal_bar_open, row.side) in wanted[stream_key], axis=1)].copy()
        if trades.duplicated(["signal_bar_open", "side"]).any():
            raise ValueError(f"ambiguous V3 V7 trade clock: {stream_key}")
        trades["stream_key"] = stream_key
        rows.append(trades.drop(columns=["variant"]))
    clocks = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["stream_key", *TRADE_COLUMNS])
    joined = leaders.merge(
        clocks,
        on=["stream_key", "signal_bar_open", "side"],
        how="left",
        validate="many_to_one",
        suffixes=("", "_cached"),
        indicator=True,
    )
    joined["trade_clock_available"] = joined._merge.eq("both")
    joined.drop(columns=["_merge"], inplace=True)
    matched = joined.executed & joined.trade_clock_available
    if matched.any():
        compare = joined.loc[matched]
        finite_clock = np.isfinite(compare[["entry_price", "initial_risk"]]).all(axis=1)
        realized = compare.loc[~compare.censored]
        finite_outcome = np.isfinite(realized[["net_r_cached", "net_return_cached"]]).all(axis=1)
        if not finite_clock.all() or not finite_outcome.all() or not compare.entry_price.gt(0).all() or not compare.initial_risk.gt(0).all():
            raise ValueError("matched V3 trade has invalid frozen price/risk/outcome")
        if (compare.entry_time < compare.availability_time).any() or (compare.exit_time < compare.entry_time).any():
            raise ValueError("matched V3 trade clock violates causal order")
        if not np.allclose(realized.net_r, realized.net_r_cached, rtol=0, atol=1e-10, equal_nan=False):
            raise ValueError("V3 net_r does not preserve formal frozen outcome")
        if not np.allclose(realized.net_return, realized.net_return_cached, rtol=0, atol=1e-12, equal_nan=False):
            raise ValueError("V3 net_return does not preserve formal frozen outcome")
        if not compare.censored.eq(compare.censored_cached).all():
            raise ValueError("V3 censored status does not preserve formal frozen outcome")
    joined["admission_clock_available"] = joined.executed & joined.trade_clock_available
    joined["input_unavailable_reason"] = np.where(
        ~joined.executed,
        "not_executed_in_formal_frozen_trade",
        np.where(~joined.trade_clock_available, "frozen_trade_clock_unavailable", None),
    )
    return joined, _canonical_sha(receipts), len(receipts)


def _cache_stream_rows(cache_inventory: dict[str, dict[str, object]], timeframe_min: int) -> pd.DataFrame:
    """Return only metadata already bound before source selection."""
    rows = []
    for stream_key, receipt in cache_inventory.items():
        if int(receipt["minutes"]) == timeframe_min:
            rows.append({"stream_key": stream_key, "venue": str(receipt["venue"]), "asset": str(receipt["asset"])})
    return pd.DataFrame(rows, columns=["stream_key", "venue", "asset"])


def build_return_sources(
    leaders: pd.DataFrame, cache_inventory: dict[str, dict[str, object]], config: dict[str, object],
) -> tuple[dict[str, ReturnSource], pd.DataFrame]:
    """Choose and materialize each asset's deterministic causal return source."""
    timeframe = int(config["source_timeframe_min"])
    precedence = [str(value) for value in config["venue_precedence"]]
    rank = {venue: index for index, venue in enumerate(precedence)}
    candidates = _cache_stream_rows(cache_inventory, timeframe)
    if not candidates.empty:
        candidates = candidates.loc[candidates.venue.isin(rank)].copy()
        candidates["venue_rank"] = candidates.venue.map(rank)
        candidates.sort_values(["asset", "venue_rank", "stream_key"], inplace=True, kind="mergesort")
        candidates = candidates.drop_duplicates("asset", keep="first")
    chosen = {row.asset: row for row in candidates.itertuples(index=False)}
    sources: dict[str, ReturnSource] = {}
    selection_rows: list[dict[str, object]] = []
    for asset in sorted(leaders.asset.astype(str).unique()):
        selected = chosen.get(asset)
        if selected is None:
            selection_rows.append({"asset": asset, "source_status": "no_30m_cache", "venue": None, "stream_key": None,
                                   "cache_sha256": None, "return_rows": 0})
            continue
        receipt = cache_inventory[selected.stream_key]
        cache = pd.read_pickle(Path(receipt["folder"]) / "control_cache.pkl.gz", compression="gzip")
        bars = cache.get("bars") if isinstance(cache, dict) else None
        gap = cache.get("data_gap") if isinstance(cache, dict) else None
        if not isinstance(bars, pd.DataFrame) or "close" not in bars:
            raise ValueError(f"selected cache lacks close bars: {selected.stream_key}")
        index = pd.to_datetime(bars.index, utc=True, errors="coerce")
        if index.isna().any() or index.has_duplicates or not index.is_monotonic_increasing:
            raise ValueError(f"selected cache has invalid bar clock: {selected.stream_key}")
        close = pd.to_numeric(bars["close"], errors="coerce").astype(float)
        returns = close.pct_change(fill_method=None)
        if isinstance(gap, pd.Series):
            gap_values = _bool(gap.reindex(bars.index), "cache data_gap")
            returns.loc[gap_values.to_numpy()] = np.nan
        returns.index = index + pd.Timedelta(minutes=timeframe)
        returns = returns.replace([np.inf, -np.inf], np.nan)
        sources[asset] = ReturnSource(asset, str(selected.venue), str(selected.stream_key), returns)
        selection_rows.append({"asset": asset, "source_status": "selected", "venue": selected.venue,
                               "stream_key": selected.stream_key, "cache_sha256": receipt["cache_sha256"],
                               "return_rows": int(returns.notna().sum())})
    return sources, pd.DataFrame(selection_rows)


def trailing_returns(source: ReturnSource, entry_time: pd.Timestamp, config: dict[str, object]) -> np.ndarray | None:
    """Return exactly the strict-before-entry 30-calendar-day return window."""
    observations = int(config["lookback_observations"])
    frequency = pd.Timedelta(minutes=int(config["source_timeframe_min"]))
    end = pd.Timestamp(entry_time) - frequency
    start = pd.Timestamp(entry_time) - pd.Timedelta(days=int(config["lookback_calendar_days"]))
    clock = pd.date_range(start=start, end=end, freq=frequency, tz="UTC")
    if len(clock) != observations:
        raise AssertionError("configured calendar window does not have the declared observation count")
    values = source.returns.reindex(clock).to_numpy(dtype=float)
    return values if np.isfinite(values).all() else None


def _pearson(left: np.ndarray, right: np.ndarray) -> float | None:
    """Compute Pearson correlation, failing closed for a non-informative series."""
    left_centered, right_centered = left - left.mean(), right - right.mean()
    denominator = math.sqrt(float(np.dot(left_centered, left_centered) * np.dot(right_centered, right_centered)))
    if not math.isfinite(denominator) or denominator == 0:
        return None
    value = float(np.dot(left_centered, right_centered) / denominator)
    return value if math.isfinite(value) else None


def apply_policy(candidates: pd.DataFrame, policy: str, sources: dict[str, ReturnSource], config: dict[str, object]) -> pd.DataFrame:
    """Apply one fixed policy with outcome-free admission and deterministic ties."""
    if policy not in POLICIES:
        raise ValueError(f"unsupported policy: {policy}")
    selected = candidates.copy().reset_index(drop=True)
    count = len(selected)
    accepted = np.zeros(count, dtype=bool)
    reason = np.full(count, "input_unavailable", dtype=object)
    before = np.full(count, np.nan)
    after = np.full(count, np.nan)
    corr_value = np.full(count, np.nan)
    corr_peer = np.full(count, None, dtype=object)
    corr_peers = np.zeros(count, dtype=int)
    history_status = np.full(count, "not_evaluated", dtype=object)
    for row in selected.itertuples():
        if bool(row.admission_clock_available):
            reason[int(row.Index)] = None
        else:
            reason[int(row.Index)] = row.input_unavailable_reason
    import heapq

    windows: dict[tuple[str, int], np.ndarray | None] = {}

    def window(asset: str, at: pd.Timestamp) -> np.ndarray | None:
        key = (asset, pd.Timestamp(at).value)
        if key not in windows:
            source = sources.get(asset)
            windows[key] = None if source is None else trailing_returns(source, at, config)
        return windows[key]

    cap = int(config["max_open_positions"])
    threshold = float(config["correlation_threshold"])
    for arm in ARMS:
        ordered = selected.loc[selected.admission_clock_available & selected.arm.eq(arm)].sort_values(
            ["entry_time", "candidate_key"], kind="mergesort"
        )
        heap: list[tuple[int, str]] = []
        active: dict[str, str] = {}
        for row in ordered.itertuples():
            index = int(row.Index)
            entry = pd.Timestamp(row.entry_time)
            while heap and heap[0][0] <= entry.value:
                _, expired_key = heapq.heappop(heap)
                active.pop(expired_key, None)
            before[index] = len(active)
            if policy != "event_leaders_baseline" and len(active) >= cap:
                reason[index] = "max_open_positions_5"
                after[index] = len(active)
                continue
            if policy == "event_leaders_max_open_5_corr_30d":
                candidate_window = window(str(row.asset), entry)
                if candidate_window is None:
                    reason[index] = "correlation_history_unavailable"
                    history_status[index] = "candidate_history_unavailable"
                    after[index] = len(active)
                    continue
                peers = sorted({asset for asset in active.values() if asset != str(row.asset)})
                corr_peers[index] = len(peers)
                if not peers:
                    history_status[index] = "no_open_different_asset"
                else:
                    values: list[tuple[str, float]] = []
                    for asset in peers:
                        peer_window = window(asset, entry)
                        if peer_window is None:
                            values = []
                            history_status[index] = "peer_history_unavailable"
                            break
                        correlation = _pearson(candidate_window, peer_window)
                        if correlation is None:
                            values = []
                            history_status[index] = "constant_or_invalid_return_series"
                            break
                        values.append((asset, correlation))
                    if not values and history_status[index] != "no_open_different_asset":
                        reason[index] = "correlation_history_unavailable"
                        after[index] = len(active)
                        continue
                    if values:
                        peer, maximum = max(values, key=lambda value: (value[1], value[0]))
                        corr_value[index], corr_peer[index] = maximum, peer
                        history_status[index] = "available"
                        if maximum >= threshold:
                            reason[index] = "correlation_ge_0_80"
                            after[index] = len(active)
                            continue
            accepted[index] = True
            reason[index] = None
            active[str(row.candidate_key)] = str(row.asset)
            heapq.heappush(heap, (pd.Timestamp(row.exit_time).value, str(row.candidate_key)))
            after[index] = len(active)
    selected["policy"] = policy
    selected["accepted"] = accepted
    selected["blocked_reason"] = reason
    selected["open_count_before"] = before
    selected["open_count_after"] = after
    selected["max_trailing_correlation"] = corr_value
    selected["max_correlation_peer_asset"] = corr_peer
    selected["correlation_peer_assets"] = corr_peers
    selected["correlation_history_status"] = history_status
    return selected.sort_values(["entry_time", "candidate_key"], kind="mergesort", na_position="last").reset_index(drop=True)


def _drawdown(closed: pd.DataFrame) -> float:
    if closed.empty:
        return 0.0
    values = closed.sort_values(["exit_time", "candidate_key"], kind="mergesort").net_r.to_numpy(float)
    equity = np.concatenate(([0.0], np.cumsum(values)))
    return float(np.max(np.maximum.accumulate(equity) - equity))


def summarize(audit: pd.DataFrame, config: dict[str, object]) -> pd.DataFrame:
    """Report policy accounting by availability-clock development/validation slices."""
    holdout = pd.Timestamp(config["holdout_start_availability"])
    scopes = {
        "development": audit.period.eq("development"),
        "validation_reused_history": audit.period.eq("validation"),
        "authorized_holdout_use_1": audit.period.eq("validation") & audit.availability_time.ge(holdout),
    }
    rows: list[dict[str, object]] = []
    for policy in POLICIES:
        for arm in ARMS:
            part = audit.loc[audit.policy.eq(policy) & audit.arm.eq(arm)]
            for scope, mask in scopes.items():
                group = part.loc[mask.loc[part.index]]
                admitted = group.loc[group.accepted]
                closed = admitted.loc[~admitted.censored]
                gains = float(closed.net_r.clip(lower=0).sum())
                losses = float(-closed.net_r.clip(upper=0).sum())
                realized_10r = int(closed.net_r.ge(10).sum())
                rows.append({
                    "policy": policy, "arm": arm, "scope": scope,
                    "availability_clock": config["availability_clock"], "candidates": int(len(group)),
                    "formal_executed": int(group.executed.sum()), "trade_clock_available": int(group.trade_clock_available.sum()),
                    "accepted": int(len(admitted)), "blocked": int((~group.accepted).sum()),
                    "blocked_max_open_positions_5": int(group.blocked_reason.eq("max_open_positions_5").sum()),
                    "blocked_correlation_ge_0_80": int(group.blocked_reason.eq("correlation_ge_0_80").sum()),
                    "blocked_correlation_history_unavailable": int(group.blocked_reason.eq("correlation_history_unavailable").sum()),
                    "blocked_frozen_trade_clock_unavailable": int(group.blocked_reason.eq("frozen_trade_clock_unavailable").sum()),
                    "censored_accepted": int(admitted.censored.sum()), "closed": int(len(closed)),
                    "cumulative_net_r": float(closed.net_r.sum()), "mean_net_r": float(closed.net_r.mean()) if len(closed) else math.nan,
                    "mean_net_return": float(closed.net_return.mean()) if len(closed) else math.nan,
                    "realized_10r_count": realized_10r,
                    "realized_10r_rate_of_closed": float(realized_10r / len(closed)) if len(closed) else math.nan,
                    "pf_net_r": gains / losses if losses > 0 else math.nan, "max_drawdown_r": _drawdown(closed),
                    "max_open_after_entry": int(admitted.open_count_after.max()) if len(admitted) else 0,
                })
    summary = pd.DataFrame(rows)
    baseline = summary.loc[summary.policy.eq("event_leaders_baseline")].set_index(["arm", "scope"])["realized_10r_count"]
    summary["realized_10r_retention_vs_baseline"] = [
        float(row.realized_10r_count / baseline[(row.arm, row.scope)]) if baseline[(row.arm, row.scope)] else math.nan
        for row in summary.itertuples()
    ]
    return summary


def no_op_comparison(audit: pd.DataFrame, summary: pd.DataFrame) -> pd.DataFrame:
    """Compare the correlation action to its fixed max-five no-op null policy."""
    cap = summary.loc[summary.policy.eq("event_leaders_max_open_5")].set_index(["arm", "scope"])
    corr = summary.loc[summary.policy.eq("event_leaders_max_open_5_corr_30d")].set_index(["arm", "scope"])
    rows = []
    for key in cap.index:
        left, right = cap.loc[key], corr.loc[key]
        rows.append({"arm": key[0], "scope": key[1], "null_policy": "event_leaders_max_open_5",
                     "action_policy": "event_leaders_max_open_5_corr_30d",
                     "accepted_delta_action_minus_null": int(right.accepted - left.accepted),
                     "closed_delta_action_minus_null": int(right.closed - left.closed),
                     "cumulative_net_r_delta_action_minus_null": float(right.cumulative_net_r - left.cumulative_net_r),
                     "correlation_threshold_rejections": int(right.blocked_correlation_ge_0_80),
                     "history_unavailable_rejections": int(right.blocked_correlation_history_unavailable),
                     "interpretation": "no-op risk-policy comparison only; not an alpha claim"})
    return pd.DataFrame(rows)


def correlation_coverage(audit: pd.DataFrame, config: dict[str, object]) -> pd.DataFrame:
    """Expose whether the fail-closed history requirement was actually usable."""
    holdout = pd.Timestamp(config["holdout_start_availability"])
    scopes = {
        "development": audit.period.eq("development"),
        "validation_reused_history": audit.period.eq("validation"),
        "authorized_holdout_use_1": audit.period.eq("validation") & audit.availability_time.ge(holdout),
    }
    rows = []
    corr = audit.loc[audit.policy.eq("event_leaders_max_open_5_corr_30d")]
    for arm in ARMS:
        for scope, mask in scopes.items():
            group = corr.loc[corr.arm.eq(arm) & mask.loc[corr.index]]
            checked = group.correlation_history_status.ne("not_evaluated")
            available = group.correlation_history_status.isin(["available", "no_open_different_asset"])
            unknown = group.correlation_history_status.isin([
                "candidate_history_unavailable", "peer_history_unavailable", "constant_or_invalid_return_series",
            ])
            rows.append({
                "arm": arm, "scope": scope, "reaching_correlation_history_gate": int(checked.sum()),
                "history_available_or_no_peer": int(available.sum()), "history_unknown": int(unknown.sum()),
                "history_coverage_rate": float(available.sum() / checked.sum()) if checked.any() else math.nan,
                "different_asset_pairs_evaluated": int(group.correlation_history_status.eq("available").sum()),
                "no_open_different_asset": int(group.correlation_history_status.eq("no_open_different_asset").sum()),
                "threshold_rejections": int(group.blocked_reason.eq("correlation_ge_0_80").sum()),
            })
    return pd.DataFrame(rows)


def result_file_inventory(output: Path) -> list[dict[str, object]]:
    """Hash every materialized result that the run manifest promises to bind."""
    entries = []
    for name in RESULT_FILES:
        path = output / name
        if not path.is_file():
            raise ValueError(f"required result file is absent: {name}")
        payload = path.read_bytes()
        entries.append({"relative_path": name, "bytes": len(payload), "sha256": _sha256_bytes(payload)})
    return entries


def verify_result_outputs(manifest_path: Path) -> None:
    """Fail closed if a result bound by a completed run manifest changed later."""
    receipt = json.loads(manifest_path.read_text())
    expected = receipt.get("result_files")
    if not isinstance(expected, list):
        raise ValueError("run manifest lacks result file inventory")
    if [entry.get("relative_path") for entry in expected] != list(RESULT_FILES):
        raise ValueError("run manifest result file set is invalid")
    aggregate = _canonical_sha(expected)
    if aggregate != receipt.get("result_file_aggregate_sha256"):
        raise ValueError("run manifest result file aggregate is invalid")
    actual = result_file_inventory(manifest_path.parent)
    if actual != expected:
        raise ValueError("result file identity differs from completed run manifest")


def run(output: Path, *, formal_members: Path = FORMAL / "event_members.csv.gz", replay: Path = REPLAY) -> dict[str, object]:
    """Materialize receipt-bound causal correlation-risk accounting."""
    config, config_path = _read_config()
    cross_manifest = FORMAL / "run_manifest.json"
    replay_manifest = replay / "manifest.json"
    if _sha256(cross_manifest) != config["formal_cross_venue_manifest_sha256"]:
        raise ValueError("formal cross-venue manifest changed")
    if _sha256(replay_manifest) != config["frozen_replay_manifest_sha256"]:
        raise ValueError("frozen replay manifest changed")
    manifest = json.loads(replay_manifest.read_text())
    if int(manifest.get("completed_streams", -1)) != int(config["expected_streams"]):
        raise ValueError("frozen replay is not complete")
    streams = replay / "streams"
    cache_inventory = inventory_caches(streams, config)
    leaders = load_formal_leaders(formal_members, config)
    candidates, trade_clock_aggregate, trade_clock_receipts = attach_trade_clocks(leaders, streams)
    sources, selection = build_return_sources(candidates, cache_inventory, config)
    audits = [apply_policy(candidates, policy, sources, config) for policy in POLICIES]
    audit = pd.concat(audits, ignore_index=True)
    summary = summarize(audit, config)
    comparison = no_op_comparison(audit, summary)
    coverage = correlation_coverage(audit, config)
    output.mkdir(parents=True, exist_ok=True)
    selection.to_csv(output / "return_source_selection.csv", index=False)
    summary.to_csv(output / "summary.csv", index=False)
    comparison.to_csv(output / "no_op_policy_comparison.csv", index=False)
    coverage.to_csv(output / "correlation_coverage.csv", index=False)
    columns = [
        "policy", "candidate_key", "source_event_id", "event_id", "arm", "period", "asset", "side", "timeframe_min",
        "stream_key", "signal_bar_open", "availability_time", "entry_time", "exit_time", "trade_id", "executed", "censored",
        "trade_clock_available", "accepted", "blocked_reason", "open_count_before", "open_count_after",
        "max_trailing_correlation", "max_correlation_peer_asset", "correlation_peer_assets", "correlation_history_status",
        "entry_price", "initial_risk", "net_return", "net_r", "exit_reason",
    ]
    audit.loc[:, columns].to_csv(output / "candidate_audit.csv.gz", index=False, compression="gzip")
    source_rule = {
        "source_timeframe_min": config["source_timeframe_min"], "venue_precedence": config["venue_precedence"],
        "tie_break": config["source_tie_break"], "return_definition": config["return_definition"],
        "availability_clock": config["availability_clock"], "lookback_calendar_days": config["lookback_calendar_days"],
        "lookback_observations": config["lookback_observations"], "unknown_history_policy": config["unknown_history_policy"],
        "selected_assets": int(selection.source_status.eq("selected").sum()),
        "assets_without_30m_source": int(selection.source_status.eq("no_30m_cache").sum()),
    }
    (output / "source_rule.json").write_text(json.dumps(source_rule, indent=2) + "\n")
    result_files = result_file_inventory(output)
    receipt = {
        "experiment_id": config["experiment_id"], "config_sha256": _sha256(config_path),
        "code_sha256": _sha256(Path(__file__)), "formal_event_members_sha256": _sha256(formal_members),
        "formal_cross_venue_manifest_sha256": _sha256(cross_manifest), "frozen_replay_manifest_sha256": _sha256(replay_manifest),
        "cache_receipt_aggregate_sha256": config["cache_receipt_aggregate_sha256"], "cache_streams_verified": len(cache_inventory),
        "stream_metadata_aggregate_sha256": config["stream_metadata_aggregate_sha256"],
        "trade_clock_receipt_aggregate_sha256": trade_clock_aggregate, "trade_clock_receipts": trade_clock_receipts,
        "event_leaders": int(len(candidates)), "formal_executed_leaders": int(candidates.executed.sum()),
        "trade_clock_available_leaders": int(candidates.trade_clock_available.sum()), "audit_rows": int(len(audit)),
        "return_sources": int(len(sources)), "owner_authorization": config["owner_authorization"],
        "holdout_use": config["holdout_use"], "research_only": True, "nonblind_reused_history": True,
        "no_alpha_claim": True,
        "result_files": result_files, "result_file_aggregate_sha256": _canonical_sha(result_files),
    }
    manifest_path = output / "run_manifest.json"
    manifest_path.write_text(json.dumps(receipt, indent=2) + "\n")
    verify_result_outputs(manifest_path)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=EXP / "results")
    parser.add_argument("--formal-members", type=Path, default=FORMAL / "event_members.csv.gz")
    parser.add_argument("--replay", type=Path, default=REPLAY)
    args = parser.parse_args()
    print(json.dumps(run(args.output, formal_members=args.formal_members, replay=args.replay), indent=2))
