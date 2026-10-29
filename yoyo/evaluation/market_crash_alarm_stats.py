"""Timing and uncertainty helpers for the six-major crash alarm study.

This module intentionally contains only deterministic matching/statistics helpers. Row
positions must refer to the shared, uncompressed one-minute grid: a missing-data gap must
remain a gap in the row numbers so the 15-minute alarm cooldown is measured in elapsed
minutes rather than in the number of retained observations.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np


def _positions(values: Iterable[int], name: str) -> np.ndarray:
    """Return validated non-negative integer minute-grid positions."""
    raw = np.asarray(list(values) if not isinstance(values, np.ndarray) else values)
    if raw.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if raw.size == 0:
        return np.empty(0, dtype=np.int64)
    try:
        numeric = raw.astype(float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain integer positions") from exc
    if not np.isfinite(numeric).all() or (numeric < 0).any() or not np.equal(numeric, np.floor(numeric)).all():
        raise ValueError(f"{name} must contain non-negative integer positions")
    return numeric.astype(np.int64)


def _seconds(value: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite and non-negative") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def match_one_to_one(
    alarm_positions: Iterable[int],
    event_positions: Iterable[int],
    latency_seconds: float = 1,
    min_lead: float = 60,
    max_lead: float = 300,
) -> dict[str, np.ndarray]:
    """Match the earliest timely alarm to each event, at most once.

    Each event position is the open of its first severe-threshold-crossing minute. An alarm
    at row ``a`` is available at ``(a + 1) * 60 + latency_seconds``; event ``e`` is timed at
    ``e * 60``. Thus a signal from the immediately preceding bar arrives one second after
    the event open at the default latency and does not qualify for a 60-second minimum lead.

    Events are processed by event time (then original input order). For each event, the
    earliest still-unmatched alarm whose lead is in the inclusive requested interval is
    assigned. Returned indices refer to the original input arrays, not their sorted order.
    """
    alarms = _positions(alarm_positions, "alarm_positions")
    events = _positions(event_positions, "event_positions")
    latency = _seconds(latency_seconds, "latency_seconds")
    lead_min = _seconds(min_lead, "min_lead")
    lead_max = _seconds(max_lead, "max_lead")
    if lead_min > lead_max:
        raise ValueError("min_lead must be <= max_lead")

    event_for_alarm = np.full(len(alarms), -1, dtype=np.int64)
    alarm_for_event = np.full(len(events), -1, dtype=np.int64)
    lead_for_event = np.full(len(events), np.nan, dtype=float)
    if not len(alarms) or not len(events):
        return {
            "event_for_alarm": event_for_alarm,
            "alarm_for_event": alarm_for_event,
            "lead_for_event": lead_for_event,
        }

    arrival = (alarms.astype(float) + 1.0) * 60.0 + latency
    alarm_order = np.lexsort((np.arange(len(alarms)), arrival))
    ordered_arrival = arrival[alarm_order]
    used = np.zeros(len(alarms), dtype=bool)
    event_order = np.lexsort((np.arange(len(events)), events))

    for event_index in event_order:
        event_time = float(events[event_index]) * 60.0
        # Inclusive lead bounds mean arrival lies in [event-max_lead, event-min_lead].
        left = int(np.searchsorted(ordered_arrival, event_time - lead_max, side="left"))
        right = int(np.searchsorted(ordered_arrival, event_time - lead_min, side="right"))
        for ordered_index in range(left, right):
            alarm_index = int(alarm_order[ordered_index])
            if used[alarm_index]:
                continue
            lead = event_time - arrival[alarm_index]
            used[alarm_index] = True
            event_for_alarm[alarm_index] = int(event_index)
            alarm_for_event[event_index] = alarm_index
            lead_for_event[event_index] = lead
            break

    return {
        "event_for_alarm": event_for_alarm,
        "alarm_for_event": alarm_for_event,
        "lead_for_event": lead_for_event,
    }


def _cell_key(value: Any) -> Any:
    """Normalize NumPy scalars while requiring a hashable, pre-encoded cell."""
    if isinstance(value, np.generic):
        value = value.item()
    try:
        hash(value)
    except TypeError as exc:
        raise ValueError("each cell value must be hashable") from exc
    return value


def _valid_null_sample(
    sample: np.ndarray,
    cell_by_position: dict[int, Any],
    target_counts: Mapping[Any, int],
    cooldown_minutes: int,
) -> bool:
    if len(sample) != sum(target_counts.values()) or len(np.unique(sample)) != len(sample):
        return False
    if any(int(position) not in cell_by_position for position in sample):
        return False
    observed: dict[Any, int] = defaultdict(int)
    for position in sample:
        observed[cell_by_position[int(position)]] += 1
    if dict(observed) != dict(target_counts):
        return False
    ordered = np.sort(sample)
    return cooldown_minutes <= 0 or len(ordered) < 2 or bool(np.all(np.diff(ordered) >= cooldown_minutes))


def _cooldown_ok(positions: np.ndarray, cooldown_minutes: int) -> bool:
    ordered = np.sort(positions)
    return cooldown_minutes <= 0 or len(ordered) < 2 or bool(np.all(np.diff(ordered) >= cooldown_minutes))


def _draw_cell_counts(
    rng: np.random.Generator,
    candidate_groups: Mapping[Any, np.ndarray],
    target_counts: Mapping[Any, int],
    cooldown_minutes: int,
    max_restarts: int,
) -> tuple[np.ndarray | None, int]:
    """Draw one exact cell-count schedule, rejecting cooldown violations without relaxing."""
    cells = list(target_counts)
    for attempt in range(1, max_restarts + 2):
        selected: list[int] = []
        order = rng.permutation(len(cells)) if cells else np.empty(0, dtype=int)
        for order_index in order:
            cell = cells[int(order_index)]
            count = int(target_counts[cell])
            if count:
                choices = rng.choice(candidate_groups[cell], size=count, replace=False)
                selected.extend(int(position) for position in choices)
        sample = np.asarray(selected, dtype=np.int64)
        if not len(sample):
            return sample, attempt
        if _cooldown_ok(sample, cooldown_minutes):
            return np.sort(sample), attempt
    return None, max_restarts + 1


def _draw_null_alarm_positions(
    rng: np.random.Generator,
    candidate_groups: Mapping[Any, np.ndarray],
    target_counts: Mapping[Any, int],
    cooldown_minutes: int,
    max_restarts: int,
) -> np.ndarray | None:
    """Single-block wrapper retained for focused sampler checks."""
    sample, _attempts = _draw_cell_counts(rng, candidate_groups, target_counts,
                                          cooldown_minutes, max_restarts)
    return sample


def _stable_key(value: Any) -> tuple[str, str]:
    return type(value).__name__, repr(value)


def _draw_blocked_null_positions(
    rng: np.random.Generator,
    candidate_groups_by_block: Mapping[Any, Mapping[Any, np.ndarray]],
    target_counts_by_block: Mapping[Any, Mapping[Any, int]],
    cooldown_minutes: int,
    max_restarts: int,
) -> tuple[np.ndarray | None, dict[str, Any]]:
    """Rejection-sample exact per-cell schedules independently by block, then jointly.

    Each block draw is uniform over its exact per-cell samples conditional on within-block
    cooldown. The combined draw is accepted only if adjacent rows across block boundaries
    also satisfy cooldown. Retrying the whole set after a boundary conflict is rejection
    sampling from the joint constrained space; it is not a greedy placement approximation.
    """
    blocks = sorted(target_counts_by_block, key=_stable_key)
    meta: dict[str, Any] = {
        "outer_attempts": 0,
        "block_draw_attempts": 0,
        "max_block_draw_attempts": 0,
        "failure_reason": None,
    }
    for outer_attempt in range(1, max_restarts + 2):
        meta["outer_attempts"] = outer_attempt
        selected: list[int] = []
        for block in blocks:
            block_sample, block_attempts = _draw_cell_counts(
                rng,
                candidate_groups_by_block[block],
                target_counts_by_block[block],
                cooldown_minutes,
                max_restarts,
            )
            meta["block_draw_attempts"] += block_attempts
            meta["max_block_draw_attempts"] = max(meta["max_block_draw_attempts"], block_attempts)
            if block_sample is None:
                meta["failure_reason"] = "within_block_cooldown"
                return None, meta
            selected.extend(int(position) for position in block_sample)

        sample = np.sort(np.asarray(selected, dtype=np.int64))
        if _cooldown_ok(sample, cooldown_minutes):
            return sample, meta

    meta["failure_reason"] = "cross_week_cooldown"
    return None, meta


def _valid_blocked_null_sample(
    sample: np.ndarray,
    cell_by_position: Mapping[int, Any],
    block_by_position: Mapping[int, Any],
    target_counts_by_block: Mapping[Any, Mapping[Any, int]],
    cooldown_minutes: int,
) -> bool:
    if len(np.unique(sample)) != len(sample):
        return False
    if any(int(position) not in cell_by_position or int(position) not in block_by_position for position in sample):
        return False
    expected_total = sum(sum(counts.values()) for counts in target_counts_by_block.values())
    if len(sample) != expected_total or not _cooldown_ok(sample, cooldown_minutes):
        return False
    for block, target_counts in target_counts_by_block.items():
        block_sample = np.asarray([position for position in sample if block_by_position[int(position)] == block],
                                  dtype=np.int64)
        if not _valid_null_sample(block_sample, cell_by_position, target_counts, cooldown_minutes):
            return False
    return True


def matched_time_null(
    eligible_positions: Iterable[int],
    cells: Sequence[Any],
    real_alarm_positions: Iterable[int],
    event_positions: Iterable[int],
    *,
    latency_seconds: float = 1,
    min_lead: float = 60,
    max_lead: float = 300,
    cooldown_minutes: int = 15,
    block_ids: Sequence[Any] | None = None,
    repeats: int = 499,
    seed: int = 20261029,
    max_restarts: int = 10,
) -> dict[str, Any]:
    """Compare observed event hits with exact-count conditional random alarm times.

    ``cells`` aligns one-to-one with ``eligible_positions`` and contains cells encoded using
    development-only volatility cutoffs, UTC week, and UTC hour. Optional ``block_ids`` also
    aligns with eligible positions and should identify UTC weeks. Each replicate independently
    rejection-samples exact per-cell counts inside each block, then checks cooldown across the
    combined sample. Within-block schedules receive one initial draw plus at most
    ``max_restarts`` retries; a cross-block conflict re-draws all blocks up to the same limit.
    This is exact conditional rejection sampling, not greedy placement. Failed replicates are
    recorded and no stratum or cooldown constraint is relaxed. Omitting ``block_ids`` treats
    the evaluation interval as one block.

    ``null_hits`` contains hit counts for successful replicates only. Null recall moments and
    the add-one upper-tail p-value use those same successful replicates. ``constraints_verified``
    is true only when at least one replicate succeeded and every retained replicate was checked
    against exact cell counts, block membership, eligible membership, uniqueness, and both
    within-block and cross-block cooldown. ``attempts`` gives per-replicate outer and block draw
    counts plus a failure reason; ``failure_reasons`` aggregates failed replicate reasons.
    """
    eligible = _positions(eligible_positions, "eligible_positions")
    alarms = _positions(real_alarm_positions, "real_alarm_positions")
    events = _positions(event_positions, "event_positions")
    latency = _seconds(latency_seconds, "latency_seconds")
    lead_min = _seconds(min_lead, "min_lead")
    lead_max = _seconds(max_lead, "max_lead")
    if lead_min > lead_max:
        raise ValueError("min_lead must be <= max_lead")
    if isinstance(cooldown_minutes, bool) or int(cooldown_minutes) != cooldown_minutes or cooldown_minutes < 0:
        raise ValueError("cooldown_minutes must be a non-negative integer")
    if isinstance(repeats, bool) or int(repeats) != repeats or repeats < 0:
        raise ValueError("repeats must be a non-negative integer")
    if isinstance(max_restarts, bool) or int(max_restarts) != max_restarts or max_restarts < 0:
        raise ValueError("max_restarts must be a non-negative integer")
    cooldown = int(cooldown_minutes)
    repeat_count = int(repeats)
    restart_count = int(max_restarts)

    cell_values = list(cells)
    if len(cell_values) != len(eligible):
        raise ValueError("cells length must equal eligible_positions length")
    block_values = [0] * len(eligible) if block_ids is None else list(block_ids)
    if len(block_values) != len(eligible):
        raise ValueError("block_ids length must equal eligible_positions length")
    if len(np.unique(eligible)) != len(eligible):
        raise ValueError("eligible_positions must be unique")
    if len(np.unique(alarms)) != len(alarms):
        raise ValueError("real_alarm_positions must be unique")

    cell_by_position: dict[int, Any] = {}
    block_by_position: dict[int, Any] = {}
    candidate_lists: dict[Any, dict[Any, list[int]]] = defaultdict(lambda: defaultdict(list))
    for position, cell, block in zip(eligible, cell_values, block_values):
        cell_key = _cell_key(cell)
        block_key = _cell_key(block)
        cell_by_position[int(position)] = cell_key
        block_by_position[int(position)] = block_key
        candidate_lists[block_key][cell_key].append(int(position))
    target_counts_by_block: dict[Any, dict[Any, int]] = defaultdict(lambda: defaultdict(int))
    for alarm in alarms:
        if int(alarm) not in cell_by_position:
            raise ValueError("every real alarm position must be eligible")
        position = int(alarm)
        target_counts_by_block[block_by_position[position]][cell_by_position[position]] += 1
    candidate_groups_by_block = {
        block: {cell: np.asarray(positions, dtype=np.int64) for cell, positions in groups.items()}
        for block, groups in candidate_lists.items()
    }
    expected_counts_by_block = {
        block: dict(counts) for block, counts in target_counts_by_block.items()
    }

    observed_match = match_one_to_one(alarms, events, latency, lead_min, lead_max)
    observed_hits = int(np.count_nonzero(observed_match["alarm_for_event"] >= 0))
    rng = np.random.default_rng(seed)
    null_hits: list[int] = []
    all_constraints_ok = True
    attempts: list[dict[str, Any]] = []
    failure_reasons = {"within_block_cooldown": 0, "cross_week_cooldown": 0, "verification_failed": 0}

    for _ in range(repeat_count):
        sample, draw_meta = _draw_blocked_null_positions(
            rng, candidate_groups_by_block, expected_counts_by_block, cooldown, restart_count
        )
        attempts.append(draw_meta)
        if sample is None:
            failure_reasons[draw_meta["failure_reason"]] += 1
            continue
        if not _valid_blocked_null_sample(sample, cell_by_position, block_by_position,
                                          expected_counts_by_block, cooldown):
            all_constraints_ok = False
            failure_reasons["verification_failed"] += 1
            draw_meta["failure_reason"] = "verification_failed"
            continue
        null_match = match_one_to_one(sample, events, latency, lead_min, lead_max)
        null_hits.append(int(np.count_nonzero(null_match["alarm_for_event"] >= 0)))

    successful = len(null_hits)
    failed = repeat_count - successful
    if successful and len(events):
        recalls = np.asarray(null_hits, dtype=float) / float(len(events))
        null_recall_mean = float(recalls.mean())
        null_recall_ci95 = tuple(float(x) for x in np.quantile(recalls, [0.025, 0.975]))
        p_ge_observed = float((1 + np.count_nonzero(np.asarray(null_hits) >= observed_hits)) / (successful + 1))
    else:
        null_recall_mean = math.nan
        null_recall_ci95 = (math.nan, math.nan)
        p_ge_observed = math.nan

    return {
        "observed_hits": observed_hits,
        "success_replicates": successful,
        "failed_replicates": failed,
        "null_hits": null_hits,
        "null_recall_mean": null_recall_mean,
        "null_recall_ci95": null_recall_ci95,
        "p_ge_observed": p_ge_observed,
        "constraints_verified": bool(successful and all_constraints_ok),
        "attempts": attempts,
        "failure_reasons": failure_reasons,
    }


def _records(week_records: Any) -> list[dict[str, Any]]:
    """Convert a DataFrame-like object or iterable of mappings into records."""
    if hasattr(week_records, "to_dict"):
        try:
            rows = week_records.to_dict(orient="records")
        except TypeError:
            rows = week_records.to_dict("records")
    else:
        rows = list(week_records)
    if any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("week_records must contain mapping-like rows")
    return [dict(row) for row in rows]


def paired_week_bootstrap(
    week_records: Any,
    *,
    repeats: int = 5000,
    seed: int = 20261029,
) -> dict[str, Any]:
    """Paired bootstrap of UTC-week blocks for recall and non-timely-alert rates.

    Duplicate rows for a week are summed first, then whole weeks are sampled with replacement.
    Both detectors always use the same sampled week indices. Point estimates are ratios of
    summed paired differences to total events or exposure days, respectively. The recall
    estimate and interval are NaN when the resampled/observed denominator has no events.
    """
    if isinstance(repeats, bool) or int(repeats) != repeats or repeats < 0:
        raise ValueError("repeats must be a non-negative integer")
    repeat_count = int(repeats)
    required = ("week", "event_n", "btc_hits", "six_hits", "btc_false", "six_false", "exposure_days")
    aggregated: dict[Any, dict[str, float]] = {}
    for row in _records(week_records):
        missing = [key for key in required if key not in row]
        if missing:
            raise ValueError(f"week_records missing columns: {', '.join(missing)}")
        week = row["week"]
        if week is None:
            raise ValueError("week must be present")
        try:
            values = {key: float(row[key]) for key in required[1:]}
        except (TypeError, ValueError) as exc:
            raise ValueError("week count and exposure columns must be numeric") from exc
        if not all(math.isfinite(value) and value >= 0 for value in values.values()):
            raise ValueError("week counts and exposure days must be finite and non-negative")
        if values["btc_hits"] > values["event_n"] or values["six_hits"] > values["event_n"]:
            raise ValueError("weekly hits cannot exceed the weekly event count")
        target = aggregated.setdefault(week, {key: 0.0 for key in required[1:]})
        for key, value in values.items():
            target[key] += value

    columns = {key: np.asarray([aggregated[week][key] for week in aggregated], dtype=float)
               for key in required[1:]}
    n_weeks = len(aggregated)

    def estimates(indices: np.ndarray) -> tuple[float, float]:
        if not len(indices):
            return math.nan, math.nan
        event_total = float(columns["event_n"][indices].sum())
        recall_diff = (float((columns["six_hits"][indices] - columns["btc_hits"][indices]).sum()) / event_total
                       if event_total > 0 else math.nan)
        exposure_total = float(columns["exposure_days"][indices].sum())
        false_rate_diff = (
            float((columns["six_false"][indices] - columns["btc_false"][indices]).sum()) / exposure_total
            if exposure_total > 0 else math.nan
        )
        return recall_diff, false_rate_diff

    point_recall, point_false = estimates(np.arange(n_weeks, dtype=int))
    rng = np.random.default_rng(seed)
    recall_replicates = np.full(repeat_count, np.nan, dtype=float)
    false_replicates = np.full(repeat_count, np.nan, dtype=float)
    for replicate in range(repeat_count):
        indices = rng.integers(0, n_weeks, size=n_weeks) if n_weeks else np.empty(0, dtype=int)
        recall_replicates[replicate], false_replicates[replicate] = estimates(indices)

    def interval(values: np.ndarray) -> tuple[float, float]:
        finite = values[np.isfinite(values)]
        if not len(finite):
            return math.nan, math.nan
        return tuple(float(x) for x in np.quantile(finite, [0.025, 0.975]))

    return {
        "weeks": n_weeks,
        "bootstrap_repeats": repeat_count,
        "recall_diff": point_recall,
        "recall_diff_ci95": interval(recall_replicates),
        "valid_recall_replicates": int(np.isfinite(recall_replicates).sum()),
        "false_rate_diff": point_false,
        "false_rate_diff_ci95": interval(false_replicates),
        "valid_false_rate_replicates": int(np.isfinite(false_replicates).sum()),
    }
