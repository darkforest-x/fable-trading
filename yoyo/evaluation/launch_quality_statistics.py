"""Pure selection and inference for the frozen 1H launch-quality study.

Source: exp-launch-quality-20260910-v1/PROJECT_PLAN.md. Selection reads only
closed-bar relative_volume (volume[t]/median(volume[t-20:t])), tr_expansion
(TR[t]/ATR[t-1]), validity, identity and the decision-close UTC week. The two
thresholds are independent; no feature combination, fitting or outcome-based
ranking is performed. Weekly count-matched thinning is an offline null, not
an online schedule: a week's eventual candidate count is known retrospectively.

Inference alone reads future-labelled excess_bp, the event's net return minus
its frozen valid-control mean. Asset/week means are averaged within each asset,
then asset means receive equal weight and independent sign flips. This avoids
counting repeat arrows or cross-venue copies as independent evidence; remaining
cross-asset market dependence and historical threshold selection still apply.
"""
from __future__ import annotations

from numbers import Integral

import numpy as np
import pandas as pd

from yoyo.evaluation.altseason_research import holm


DEFAULT_SEEDS = tuple(range(20260910, 20260929))
IDENTITY_COLUMNS = ("event_id", "valid", "venue", "asset", "decision_time")
GROUP_COLUMNS = ("venue", "asset", "week")


def _require_columns(frame, columns):
    if not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique:
        raise ValueError("A DataFrame with unique columns is required")
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError("Missing columns: " + ", ".join(sorted(missing)))


def _identities(frame):
    """Copy only pre-outcome identity columns; future columns are never read."""
    _require_columns(frame, IDENTITY_COLUMNS)
    rows = frame.loc[:, IDENTITY_COLUMNS].copy()
    for column in ("event_id", "venue", "asset"):
        if rows[column].isna().any() or not rows[column].map(
                lambda value: isinstance(value, str) and bool(value.strip())).all():
            raise ValueError("Nonempty string identity required: " + column)
    if rows.event_id.duplicated().any():
        raise ValueError("Duplicate event_id")
    if not rows.valid.map(lambda value: isinstance(value, (bool, np.bool_))).all():
        raise ValueError("Explicit boolean validity required")
    if rows.empty:
        rows["decision_time"] = pd.Series(dtype="datetime64[ns, UTC]")
    else:
        times = pd.DatetimeIndex(rows.decision_time)
        if times.tz is None or times.hasnans:
            raise ValueError("Timezone-aware decision closes are required")
        rows["decision_time"] = times.tz_convert("UTC")
    day = rows.decision_time.dt.normalize()
    rows["week"] = day - pd.to_timedelta(day.dt.weekday, unit="D")
    return rows.sort_values("event_id").reset_index(drop=True)


def filter_variants(actual):
    """Return independent copies for baseline, finite RV>=4 and finite TR>=3.

    Invalid/nonfillable candidates remain visible when their feature passes;
    allocation and outcome summaries must handle their explicit valid flag.
    The original arm and all columns are preserved without modification.
    """
    _require_columns(actual, ("relative_volume", "tr_expansion"))
    volume = pd.to_numeric(actual.relative_volume, errors="coerce")
    expansion = pd.to_numeric(actual.tr_expansion, errors="coerce")
    return {
        "baseline": actual.copy(),
        "volume4": actual.loc[np.isfinite(volume) & volume.ge(4)].copy(),
        "expansion3": actual.loc[np.isfinite(expansion) & expansion.ge(3)].copy(),
    }


def random_thinning(base, filtered, seeds=DEFAULT_SEEDS):
    """Return seed->event-id lists and exact venue/asset/week count diagnostics.

    Sampling is without replacement within each valid-candidate group. Each
    seeded RNG sees stable group ordering and stable event_id ordering, never
    row order, feature scores, exit fields or any other outcome. Filtered rows
    must be a metadata-consistent subset of base. Groups with 0<kept<base are
    replaceable; groups retaining every row or no row cannot discriminate.

    ``selections`` contains sorted ids for each seed; ``groups`` records all
    valid baseline groups, including target_count=0. Summary denominators are
    explicit so numerous rejected groups cannot inflate replaceability.
    """
    baseline, target = _identities(base), _identities(filtered)
    missing = set(target.event_id) - set(baseline.event_id)
    if missing:
        raise ValueError("Filtered ids are not a subset of baseline")
    joined = target.set_index("event_id").join(
        baseline.set_index("event_id"), rsuffix="_base", how="left")
    for column in ("valid", "venue", "asset", "decision_time"):
        if not joined[column].eq(joined[column + "_base"]).all():
            raise ValueError("Filtered metadata differs from baseline: " + column)
    baseline = baseline.loc[baseline.valid]
    target = target.loc[target.valid]
    seeds = tuple(seeds)
    if (any(isinstance(seed, (bool, np.bool_)) or not isinstance(seed, Integral)
            or seed < 0 for seed in seeds) or len(set(seeds)) != len(seeds)):
        raise ValueError("Distinct nonnegative integer seeds required")
    target_counts = target.groupby(list(GROUP_COLUMNS), observed=True).size()
    groups, pools = [], []
    for key, rows in baseline.groupby(list(GROUP_COLUMNS), sort=True, observed=True):
        count = int(target_counts.get(key, 0))
        population = sorted(rows.event_id.tolist())
        if count > len(population):
            raise ValueError("Filtered group is larger than baseline")
        replaceable = 0 < count < len(population)
        groups.append(dict(zip(GROUP_COLUMNS, key), baseline_count=len(population),
                           target_count=count, replaceable=replaceable))
        pools.append((population, count))
    group_frame = pd.DataFrame(groups, columns=[*GROUP_COLUMNS, "baseline_count",
                                               "target_count", "replaceable"])
    selections = {}
    for seed in seeds:
        rng = np.random.default_rng(int(seed))
        ids = []
        for population, count in pools:
            if count == len(population):
                ids.extend(population)
            elif count:
                positions = rng.choice(len(population), size=count, replace=False)
                ids.extend(population[int(i)] for i in positions)
        selections[int(seed)] = sorted(ids)
    selected_groups = sum(row["target_count"] > 0 for row in groups)
    replaceable_groups = sum(row["replaceable"] for row in groups)
    replaceable_candidates = sum(row["target_count"] for row in groups if row["replaceable"])
    summary = dict(baseline_valid_candidates=len(baseline), filtered_valid_candidates=len(target),
                   baseline_groups=len(groups), selected_groups=selected_groups,
                   replaceable_groups=replaceable_groups,
                   replaceable_selected_group_fraction=(replaceable_groups / selected_groups
                                                        if selected_groups else np.nan),
                   replaceable_target_candidates=replaceable_candidates,
                   replaceable_target_candidate_fraction=(replaceable_candidates / len(target)
                                                          if len(target) else np.nan),
                   seeds=len(seeds), grouping="venue x asset x decision-close UTC Monday week",
                   future_outcomes_used_for_selection=False,
                   interpretation="Retrospective count-matched null; not an executable weekly schedule")
    return dict(selections=selections, groups=group_frame, summary=summary)


def paired_event_statistics(events, permutations=10000, seed=20260910):
    """One-sided positive paired-excess sign test using equal asset weights.

    Call on one period/scope/variant. valid=False or nonfinite excess rows do
    not contribute. Missing controls remain missing, not zero. event_id must
    identify unique original candidates. Below five assets p is unavailable.
    Gross/net event averages are intentionally not mixed into this test.
    """
    _require_columns(events, (*IDENTITY_COLUMNS, "excess_bp"))
    identities = _identities(events)
    if (isinstance(permutations, (bool, np.bool_)) or not isinstance(permutations, Integral)
            or permutations < 1):
        raise ValueError("A positive integer permutation count is required")
    if isinstance(seed, (bool, np.bool_)) or not isinstance(seed, Integral) or seed < 0:
        raise ValueError("A nonnegative integer permutation seed is required")
    outcomes = pd.to_numeric(events.set_index("event_id").excess_bp, errors="coerce")
    identities["excess_bp"] = identities.event_id.map(outcomes)
    selected = identities.loc[identities.valid & np.isfinite(identities.excess_bp)]
    asset_weeks = selected.groupby(["asset", "week"], sort=True, observed=True).excess_bp.mean()
    units = asset_weeks.groupby(level="asset", sort=True).mean().to_numpy(dtype=float)
    observed = float(units.mean()) if len(units) else np.nan
    p_value = np.nan
    if len(units) >= 5:
        rng = np.random.default_rng(int(seed))
        exceedances = 0
        for offset in range(0, int(permutations), 500):
            size = min(500, int(permutations) - offset)
            null = (rng.choice([-1., 1.], size=(size, len(units))) * units).mean(axis=1)
            exceedances += int(np.count_nonzero(null >= observed))
        p_value = (1 + exceedances) / (int(permutations) + 1)
    valid_count = int(identities.valid.sum())
    return dict(events=len(events), valid_events=valid_count, matched_events=len(selected),
                matched_fraction=len(selected) / valid_count if valid_count else np.nan,
                asset_weeks=len(asset_weeks), permutation_assets=len(units),
                asset_balanced_excess_bp=observed,
                event_mean_excess_bp=float(selected.excess_bp.mean()) if len(selected) else np.nan,
                permutation_p=p_value, permutations=int(permutations), seed=int(seed),
                alternative="positive asset-equal mean paired excess", minimum_assets=5)


def holm_adjust(values):
    """Holm adjustment across the predeclared family; preserve unavailable p."""
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or np.isinf(values).any() or ((values < 0) | (values > 1)).any():
        raise ValueError("P values must be one-dimensional in [0,1] or NaN")
    return holm(values)
