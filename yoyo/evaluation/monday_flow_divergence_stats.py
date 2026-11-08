"""Summaries and plots for Monday 1.618 flow-divergence comparisons.

Input is one row per event. Main estimates use exact regular, absorption and
none labels. Feasible sets produce separate conservative bounds; unknown rows
are never imputed as none.
"""
from __future__ import annotations

from pathlib import Path
import zlib

import numpy as np
import pandas as pd


LABELS = ("regular", "absorption", "none")
COMPARISONS = {
    "regular_vs_none": (frozenset(("regular",)), frozenset(("none",))),
    "absorption_vs_none": (frozenset(("absorption",)), frozenset(("none",))),
    "any_vs_none": (frozenset(("regular", "absorption")), frozenset(("none",))),
}
BOOTSTRAP_REPS = 5000
BOOTSTRAP_SEED = 20261108
_REQUIRED = ("event", "week", "symbol", "side", "net_bp", "label_15m", "label_1h")


def _missing(value) -> bool:
    try:
        result = pd.isna(value)
        return bool(result) if np.ndim(result) == 0 else False
    except (TypeError, ValueError):
        return False


def _label(value) -> str:
    if _missing(value):
        return "<missing>"
    return str(value).strip().lower() or "<missing>"


def _candidate_set(possible, label_value) -> frozenset[str]:
    """Parse a feasible set; fall back only to an unambiguous exact label."""
    if not _missing(possible):
        collection = isinstance(possible, (list, tuple, set, frozenset))
        if collection:
            parts = possible
        else:
            raw = str(possible).strip()
            parts = raw.split("|") if raw else ()
        candidates = frozenset(
            str(part).strip().lower() for part in parts
            if str(part).strip().lower() in LABELS
        )
        nonempty = len(possible) > 0 if collection else bool(str(possible).strip())
        if candidates or nonempty:
            # A malformed non-empty feasible set remains unknown. Do not infer
            # a label from the display column when it contradicts this field.
            return candidates
    normalized = _label(label_value)
    return frozenset((normalized,)) if normalized in LABELS else frozenset()


def _prepare(events: pd.DataFrame, label_cols: dict[str, str] | None = None,
             possible_cols: dict[str, str] | None = None) -> pd.DataFrame:
    label_cols = label_cols or {"15m": "label_15m", "1h": "label_1h"}
    possible_cols = possible_cols or {"15m": "possible_15m", "1h": "possible_1h"}
    missing = [name for name in _REQUIRED if name not in events.columns]
    if missing:
        raise ValueError(f"events missing required columns: {', '.join(missing)}")

    out = events.copy()
    out["_net"] = pd.to_numeric(out["net_bp"], errors="coerce")
    parsed_week = pd.to_datetime(out["week"], errors="coerce")
    out["_week"] = parsed_week.dt.strftime("%Y-%m-%d").where(parsed_week.notna(), None)
    out["_year"] = parsed_week.dt.year
    side = pd.to_numeric(out["side"], errors="coerce")
    out["_side"] = side.where(side.isin((1, -1)))
    for period, col in label_cols.items():
        out[f"_label_{period}"] = out[col].map(_label)
        possible_col = possible_cols.get(period)
        if possible_col and possible_col in out.columns:
            out[f"_possible_{period}"] = [
                _candidate_set(p, label) for p, label in zip(out[possible_col], out[col])
            ]
        else:
            out[f"_possible_{period}"] = [
                _candidate_set(None, label) for label in out[col]
            ]
    return out


def _cohort(frame: pd.DataFrame, labels: frozenset[str]) -> dict:
    selected = frame[frame["_label"].isin(labels)]
    values = selected.loc[selected["_net"].notna(), "_net"]
    total = int(len(values))
    wins = int((values > 0).sum())
    return {
        "label_events": int(len(selected)),
        "wins": wins,
        "total": total,
        "rate": float(wins / total) if total else None,
        "mean_net_bp": float(values.mean()) if total else None,
        "net_bp_missing": int(selected["_net"].isna().sum()),
    }


def _weekly_sufficient(frame: pd.DataFrame) -> dict:
    """Aggregate outcome sufficient statistics once per NY week and exact label."""
    base = frame[frame["_week"].notna()]
    week_keys = sorted(base["_week"].unique())
    if not week_keys:
        return {
            "weeks": [],
            "by_label": {},
            "excluded_missing_week_events": int(frame["_week"].isna().sum()),
        }
    known = base[base["_net"].notna()].copy()
    known["_wins"] = (known["_net"] > 0).astype(np.int64)
    known["_n"] = 1
    known["_bp_sum"] = known["_net"]
    grouped = known.groupby(["_week", "_label"], sort=False)[["_wins", "_n", "_bp_sum"]].sum()
    by_label = {}
    for label in LABELS:
        try:
            values = grouped.xs(label, level="_label")
            values = values.reindex(week_keys, fill_value=0)
            by_label[label] = values[["_wins", "_n", "_bp_sum"]].to_numpy(dtype=float)
        except KeyError:
            by_label[label] = np.zeros((len(week_keys), 3), dtype=float)
    return {
        "weeks": week_keys,
        "by_label": by_label,
        "excluded_missing_week_events": int(frame["_week"].isna().sum()),
    }


def _bootstrap_ci(weekly: dict, left_labels: frozenset[str],
                  right_labels: frozenset[str], seed_key: str) -> dict:
    """Paired NY-week cluster percentile bootstrap from pre-aggregated sums."""
    week_keys = weekly["weeks"]
    if not week_keys:
        return {
            "method": "NY-week cluster percentile bootstrap",
            "reps": BOOTSTRAP_REPS,
            "valid_reps": 0,
            "weeks": 0,
            "excluded_missing_week_events": weekly["excluded_missing_week_events"],
            "win_rate_diff_95ci": None,
            "mean_net_bp_diff_95ci": None,
        }

    def arm_sums(labels):
        arrays = [weekly["by_label"][label] for label in labels if label in weekly["by_label"]]
        return np.sum(arrays, axis=0) if arrays else np.zeros((len(week_keys), 3), dtype=float)

    left = arm_sums(left_labels)
    right = arm_sums(right_labels)
    sums = np.column_stack((left, right))
    crc = zlib.crc32(seed_key.encode("utf-8"))
    rng = np.random.default_rng((BOOTSTRAP_SEED + crc) % (2**32))
    sampled_counts = rng.multinomial(
        len(week_keys), np.full(len(week_keys), 1 / len(week_keys)), size=BOOTSTRAP_REPS
    )
    totals = np.einsum("rw,wk->rk", sampled_counts, sums, optimize=False)
    left_rate = np.divide(totals[:, 0], totals[:, 1], out=np.full(BOOTSTRAP_REPS, np.nan), where=totals[:, 1] > 0)
    right_rate = np.divide(totals[:, 3], totals[:, 4], out=np.full(BOOTSTRAP_REPS, np.nan), where=totals[:, 4] > 0)
    left_mean = np.divide(totals[:, 2], totals[:, 1], out=np.full(BOOTSTRAP_REPS, np.nan), where=totals[:, 1] > 0)
    right_mean = np.divide(totals[:, 5], totals[:, 4], out=np.full(BOOTSTRAP_REPS, np.nan), where=totals[:, 4] > 0)

    rate_diff = left_rate - right_rate
    mean_diff = left_mean - right_mean

    def interval(values):
        clean = values[np.isfinite(values)]
        if not len(clean):
            return None
        return [float(v) for v in np.quantile(clean, (0.025, 0.975))]

    return {
        "method": "NY-week cluster percentile bootstrap",
        "reps": BOOTSTRAP_REPS,
        "valid_reps": int(np.isfinite(rate_diff).sum()),
        "mean_valid_reps": int(np.isfinite(mean_diff).sum()),
        "weeks": len(week_keys),
        "excluded_missing_week_events": weekly["excluded_missing_week_events"],
        "win_rate_diff_95ci": interval(rate_diff),
        "mean_net_bp_diff_95ci": interval(mean_diff),
    }


def _period_summary(frame: pd.DataFrame, period: str, label_col: str,
                    possible_col: str, seed_prefix: str) -> dict:
    data = frame[["_week", "_net", label_col, possible_col]].copy()
    data = data.rename(columns={label_col: "_label"})
    counts = data["_label"].value_counts(dropna=False)
    cohorts = {name: _cohort(data, frozenset((name,))) for name in LABELS}
    cohorts["any"] = _cohort(data, frozenset(("regular", "absorption")))
    weekly = _weekly_sufficient(data)
    comparisons = {}
    for name, (left_labels, right_labels) in COMPARISONS.items():
        left = _cohort(data, left_labels)
        right = _cohort(data, right_labels)
        comparisons[name] = {
            "left": left,
            "right": right,
            "win_rate_diff": (left["rate"] - right["rate"])
                if left["rate"] is not None and right["rate"] is not None else None,
            "mean_net_bp_diff": (left["mean_net_bp"] - right["mean_net_bp"])
                if left["mean_net_bp"] is not None and right["mean_net_bp"] is not None else None,
            "bootstrap": _bootstrap_ci(weekly, left_labels, right_labels,
                                       f"{seed_prefix}/{period}/{name}"),
        }
    feasible = data[possible_col].map(bool)
    return {
        "events": int(len(data)),
        "exact_label_counts": {str(k): int(v) for k, v in counts.items()},
        "unknown_label_events": int((~data["_label"].isin(LABELS)).sum()),
        "feasible_set_missing_or_unusable": int((~feasible).sum()),
        "feasible_set_events": int(feasible.sum()),
        "cohorts": cohorts,
        "comparisons": comparisons,
    }


def _scope_summary(frame: pd.DataFrame, seed_prefix: str,
                   labels: dict[str, str] | None = None,
                   possibles: dict[str, str] | None = None) -> dict:
    labels = labels or {"15m": "_label_15m", "1h": "_label_1h"}
    possibles = possibles or {"15m": "_possible_15m", "1h": "_possible_1h"}
    periods = {
        p: _period_summary(frame, p, labels[p], possibles[p], seed_prefix)
        for p in labels
    }
    return {"events": int(len(frame)), "periods": periods}


def _bounds_for_arm(data: pd.DataFrame, arm: frozenset[str]) -> dict:
    """Sharp marginal bounds for one cohort under the supplied feasible sets."""
    known_outcomes = data[data["_net"].notna() & data["_possible"].map(bool)]
    state = []
    for candidates, raw_value in known_outcomes[["_possible", "_net"]].itertuples(index=False, name=None):
        member = bool(candidates & arm)
        mandatory = member and candidates.issubset(arm)
        optional = member and not mandatory
        if not member:
            continue
        value = float(raw_value)
        outcome = "win" if value > 0 else "loss" if value < 0 else "flat"
        state.append((mandatory, optional, value, outcome))
    fixed = [x for x in state if x[0]]
    optional = [x for x in state if x[1]]

    def rate_extreme(maximize: bool) -> float | None:
        fixed_wins = sum(x[3] == "win" for x in fixed)
        fixed_n = len(fixed)
        opt_wins = sum(x[3] == "win" for x in optional)
        opt_nonwins = len(optional) - opt_wins
        if maximize:
            if opt_wins:
                return 1.0 if fixed_n == 0 else (fixed_wins + opt_wins) / (fixed_n + opt_wins)
            return fixed_wins / fixed_n if fixed_n else (0.0 if opt_nonwins else None)
        if opt_nonwins:
            return fixed_wins / (fixed_n + opt_nonwins) if fixed_n else 0.0
        return fixed_wins / fixed_n if fixed_n else (1.0 if opt_wins else None)

    def mean_extreme(maximize: bool) -> float | None:
        fixed_values = [x[2] for x in fixed]
        option_values = sorted((x[2] for x in optional), reverse=maximize)
        candidates = [float(np.mean(fixed_values))] if fixed_values else []
        running = sum(fixed_values)
        count = len(fixed_values)
        for value in option_values:
            running += value
            count += 1
            candidates.append(running / count)
        if not candidates:
            return None
        return float(max(candidates) if maximize else min(candidates))

    fixed_wins = sum(x[3] == "win" for x in fixed)
    fixed_losses = sum(x[3] == "loss" for x in fixed)
    fixed_flats = sum(x[3] == "flat" for x in fixed)
    optional_wins = sum(x[3] == "win" for x in optional)
    optional_losses = sum(x[3] == "loss" for x in optional)
    optional_flats = sum(x[3] == "flat" for x in optional)
    feasible_rows = data[data["_possible"].map(bool)]
    candidate_members = feasible_rows["_possible"].map(lambda s: bool(s & arm))
    mandatory_members = feasible_rows["_possible"].map(lambda s: bool(s) and s.issubset(arm))
    label_candidate_n = int(candidate_members.sum())
    label_mandatory_n = int((candidate_members & mandatory_members).sum())
    return {
        "possible_label_events": label_candidate_n,
        "minimum_label_denominator": label_mandatory_n,
        "maximum_label_denominator": label_candidate_n,
        "possible_outcome_denominator": len(state),
        "minimum_denominator": len(fixed),
        "maximum_denominator": len(state),
        "outcome_denominator_scope": "numeric net_bp events; ties remain in total",
        "mandatory_outcomes": {"n": len(fixed), "wins": fixed_wins, "losses": fixed_losses, "flat": fixed_flats},
        "optional_outcomes": {"n": len(optional), "wins": optional_wins, "losses": optional_losses, "flat": optional_flats},
        "win_rate_bounds": [rate_extreme(False), rate_extreme(True)],
        "mean_net_bp_bounds": [mean_extreme(False), mean_extreme(True)],
    }


def _feasible_bounds(frame: pd.DataFrame, period: str, left_arm: frozenset[str],
                     right_arm: frozenset[str]) -> dict:
    data = pd.DataFrame({
        "_net": frame["_net"].to_numpy(),
        "_possible": frame[f"_possible_{period}"].to_numpy(),
    })
    left = _bounds_for_arm(data, left_arm)
    right = _bounds_for_arm(data, right_arm)
    known = data[data["_net"].notna() & data["_possible"].map(bool)]
    overlap = int(sum(bool(s & left_arm) and bool(s & right_arm) for s in known["_possible"]))
    rate_l, rate_u = left["win_rate_bounds"]
    rate_r_l, rate_r_u = right["win_rate_bounds"]
    mean_l, mean_u = left["mean_net_bp_bounds"]
    mean_r_l, mean_r_u = right["mean_net_bp_bounds"]
    # With at least one fixed outcome in each arm, each uncertain event can be
    # assigned to whichever feasible arm gives the more extreme win/loss
    # contribution. This makes the win-rate difference bounds attainable even
    # when one feasible set spans both arms. Mean differences do not share this
    # property because a value can improve both arm means at once.
    rate_sharp = (
        rate_l is not None and rate_r_l is not None
        and (overlap == 0 or (left["minimum_denominator"] > 0 and right["minimum_denominator"] > 0))
    )
    mean_sharp = overlap == 0 and mean_l is not None and mean_r_l is not None
    return {
        "left_arm": sorted(left_arm),
        "right_arm": sorted(right_arm),
        "left": left,
        "right": right,
        "joint_feasible_set_overlap_events": overlap,
        "win_rate_diff_bounds": [
            rate_l - rate_r_u if rate_l is not None and rate_r_u is not None else None,
            rate_u - rate_r_l if rate_u is not None and rate_r_l is not None else None,
        ],
        "mean_net_bp_diff_bounds": [
            mean_l - mean_r_u if mean_l is not None and mean_r_u is not None else None,
            mean_u - mean_r_l if mean_u is not None and mean_r_l is not None else None,
        ],
        "conservative_difference_bound": True,
        "sharp_for_win_rate_difference": bool(rate_sharp),
        "sharp_for_joint_difference": bool(rate_sharp and mean_sharp),
        "sharpness_by_metric": {
            "win_rate_diff": bool(rate_sharp),
            "mean_net_bp_diff": bool(mean_sharp),
        },
        "sharpness_note": "Win-rate difference bounds are sharp when both arms have fixed numeric outcomes, or their feasible memberships do not overlap. Mean-net-bp bounds are guaranteed sharp when feasible memberships do not overlap; with overlap they are conservative outer bounds.",
        "unknown_feasible_set_outcomes_excluded": int((data["_net"].notna() & ~data["_possible"].map(bool)).sum()),
        "missing_net_bp_events_excluded": int((data["_net"].isna() & data["_possible"].map(bool)).sum()),
        "denominator_note": "Bounds use numeric-net_bp events per arm. Denominators range from mandatory to all feasible arm members. Events with no feasible set are excluded, never assigned to none.",
    }


def _summary_core(frame: pd.DataFrame, seed_prefix: str) -> dict:
    all_scope = _scope_summary(frame, f"{seed_prefix}/all")
    years = {}
    for name, mask in (
        ("<=2024", frame["_year"].le(2024)),
        ("2025", frame["_year"].eq(2025)),
        ("2026", frame["_year"].eq(2026)),
    ):
        years[name] = _scope_summary(frame[mask], f"{seed_prefix}/year/{name}")
    sides = {}
    for side, name in ((1, "+1"), (-1, "-1")):
        sides[name] = _scope_summary(frame[frame["_side"].eq(side)], f"{seed_prefix}/side/{name}")
    shared_mask = frame["_label_15m"].isin(LABELS) & frame["_label_1h"].isin(LABELS)
    shared = _scope_summary(frame[shared_mask], f"{seed_prefix}/shared-coverage")
    return {
        "all": all_scope,
        "by_year": years,
        "by_side": sides,
        "shared_coverage": shared,
        "shared_coverage_rule": "both exact period labels are regular, absorption or none; ambiguous/unknown labels are not imputed",
    }


def _same_price_action(frame: pd.DataFrame) -> dict:
    output = {}
    common = {"touch_price_min", "touch_price_max"}
    for period in ("15m", "1h"):
        pivot_col = f"pivot_price_{period}"
        if not common.issubset(frame.columns) or pivot_col not in frame.columns:
            continue
        low = pd.to_numeric(frame["touch_price_min"], errors="coerce")
        high = pd.to_numeric(frame["touch_price_max"], errors="coerce")
        pivot = pd.to_numeric(frame[pivot_col], errors="coerce")
        mask = ((frame["_side"].eq(1) & high.lt(pivot)) |
                (frame["_side"].eq(-1) & low.gt(pivot)))
        subset = frame[mask & low.notna() & high.notna() & pivot.notna()]
        data = subset[["_week", "_net", f"_label_{period}"]].rename(columns={f"_label_{period}": "_label"})
        left = _cohort(data, frozenset(("regular",)))
        right = _cohort(data, frozenset(("none",)))
        comp = {
            "left": left,
            "right": right,
            "win_rate_diff": (left["rate"] - right["rate"])
                if left["rate"] is not None and right["rate"] is not None else None,
            "bootstrap": _bootstrap_ci(_weekly_sufficient(data), frozenset(("regular",)), frozenset(("none",)),
                                       f"same-price/{period}/regular-vs-none"),
        }
        output[period] = {
            "events": int(len(subset)),
            "rule": "side +1: touch_price_max < pivot_price; side -1: touch_price_min > pivot_price",
            "regular_vs_none": comp,
        }
    return output


def summarize(events: pd.DataFrame) -> dict:
    """Summarize event-level labels and outcomes without changing labels.

    A win is numeric net_bp > 0. NY-week clusters are resampled as whole blocks
    for the percentile intervals.
    """
    if not isinstance(events, pd.DataFrame):
        raise TypeError("events must be a pandas DataFrame")
    frame = _prepare(events)
    result = _summary_core(frame, "main")

    label_counts = {}
    for period in ("15m", "1h"):
        counts = frame[f"_label_{period}"].value_counts(dropna=False)
        candidates = frame[f"_possible_{period}"]
        label_counts[period] = {
            "label_counts": {str(k): int(v) for k, v in counts.items()},
            "unknown_label_count": int((~frame[f"_label_{period}"].isin(LABELS)).sum()),
            "empty_feasible_set_count": int((~candidates.map(bool)).sum()),
            "candidate_set_count": int(candidates.map(bool).sum()),
        }

    bounds = {}
    for period in ("15m", "1h"):
        bounds[period] = {
            name: _feasible_bounds(frame, period, arms[0], arms[1])
            for name, arms in {
                "regular_vs_none": COMPARISONS["regular_vs_none"],
                "any_vs_none": COMPARISONS["any_vs_none"],
            }.items()
        }

    support = {}
    pre_cols = {p: f"label_pre_{p}" for p in ("15m", "1h") if f"label_pre_{p}" in events.columns}
    if pre_cols:
        pre_frame = _prepare(
            events, label_cols=pre_cols,
            possible_cols={p: f"possible_pre_{p}" for p in pre_cols},
        )
        pre_labels = {p: f"_label_{p}" for p in pre_cols}
        pre_possible = {p: f"_possible_{p}" for p in pre_cols}
        pre_periods = {
            p: _period_summary(pre_frame, p, pre_labels[p], pre_possible[p], f"pre/{p}")
            for p in pre_cols
        }
        pre_shared = None
        if set(pre_cols) == {"15m", "1h"}:
            mask = pre_frame["_label_15m"].isin(LABELS) & pre_frame["_label_1h"].isin(LABELS)
            pre_shared = _scope_summary(
                pre_frame[mask], "pre/shared-coverage",
                labels=pre_labels, possibles=pre_possible,
            )
        support["asof_pre"] = {
            "available_periods": list(pre_cols),
            "periods": pre_periods,
            "shared_coverage": pre_shared,
            "unknown_label_counts": {
                p: int((~pre_frame[f"_label_{p}"].isin(LABELS)).sum()) for p in pre_cols
            },
        }

    if "tick_exact" in events.columns:
        raw_tick = events["tick_exact"]
        truth = {"true", "1", "1.0", "yes", "y", "exact"}
        false = {"false", "0", "0.0", "no", "n", "inexact"}
        parsed = []
        unknown_tick = 0
        for value in raw_tick:
            if _missing(value):
                parsed.append(None)
                unknown_tick += 1
            elif isinstance(value, (bool, np.bool_)):
                parsed.append(bool(value))
            else:
                val = str(value).strip().lower()
                if val in truth:
                    parsed.append(True)
                elif val in false:
                    parsed.append(False)
                else:
                    parsed.append(None)
                    unknown_tick += 1
        tick_mask = pd.Series(parsed, index=frame.index).eq(True)
        tick_frame = frame[tick_mask]
        support["tick_exact"] = {
            "exact_events": int(len(tick_frame)),
            "not_exact_events": int(sum(v is False for v in parsed)),
            "unknown_tick_exact_count": int(unknown_tick),
            "summary": _summary_core(tick_frame, "tick-exact"),
        }

    result.update({
        "meta": {
            "events": int(len(events)),
            "unique_event_ids": int(events["event"].nunique(dropna=True)),
            "duplicate_event_id_rows": int(events["event"].duplicated(keep=False).sum()),
            "numeric_net_bp_events": int(frame["_net"].notna().sum()),
            "missing_or_non_numeric_net_bp_events": int(frame["_net"].isna().sum()),
            "events_with_invalid_or_missing_week": int(frame["_week"].isna().sum()),
            "events_with_unknown_side": int(frame["_side"].isna().sum()),
            "win_definition": "numeric net_bp > 0; zero remains in total but is neither win nor loss",
            "week_definition": "input week normalized to YYYY-MM-DD and used as the NY-week cluster key",
            "bootstrap": {
                "method": "NY-week cluster percentile",
                "reps": BOOTSTRAP_REPS,
                "seed_base": BOOTSTRAP_SEED,
                "ci": "95%",
            },
        },
        "label_quality": label_counts,
        "full_period_feasible_bounds": bounds,
        "same_price_action_check": _same_price_action(frame),
        "supporting_versions": support,
    })
    return result


def plot(summary: dict, path) -> Path:
    """Plot full-period regular, absorption and none win rates for both periods."""
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    rows = []
    for period in ("15m", "1h"):
        p = summary.get("all", {}).get("periods", {}).get(period, {})
        cohorts = p.get("cohorts", {})
        for key, label in (
            ("regular", "Regular"),
            ("absorption", "Absorption candidate"),
            ("none", "None"),
        ):
            cohort = cohorts.get(key, {})
            rows.append((period, label, cohort.get("rate"), cohort.get("total", 0)))

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10.0, 4.8), constrained_layout=True)
    x = np.array([0.0, 2.4])
    width = 0.46
    series = (
        ("Regular", -0.55, "regular"),
        ("Absorption candidate", 0, "absorption"),
        ("None", 0.55, "none"),
    )
    visible_series = [
        (label, offset, key) for label, offset, key in series
        if any(r[3] > 0 for r in rows if r[1] == label)
    ]
    for label, offset, _ in visible_series:
        values = [next((r[2] for r in rows if r[0] == period and r[1] == label), None)
                  for period in ("15m", "1h")]
        ns = [next((r[3] for r in rows if r[0] == period and r[1] == label), 0)
              for period in ("15m", "1h")]
        heights = [np.nan if value is None else value for value in values]
        bars = ax.bar(x + offset, heights, width, label=label)
        for bar, value, n in zip(bars, values, ns):
            if value is not None:
                ax.annotate(f"{value:.2%} / n={n:,}",
                            (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                            xytext=(0, 4), textcoords="offset points", ha="center",
                            va="bottom", fontsize=7)
            elif n == 0:
                ax.annotate("n=0", (bar.get_x() + bar.get_width() / 2, 0),
                            xytext=(0, 3), textcoords="offset points", ha="center",
                            va="bottom", fontsize=8)
    ax.set_xticks(x, ("15m", "1h"))
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_ylabel("Win rate")
    rates = [r[2] for r in rows if r[2] is not None]
    max_rate = max(rates, default=0.0)
    y_top = min(1.08, max(0.10, np.ceil((max_rate + 0.05) / 0.05) * 0.05))
    ax.set_ylim(0, y_top)
    ax.set_title("Monday 1.618 flow divergence: win rate")
    ax.legend(frameon=False)
    ax.grid(axis="y", alpha=0.25)
    fig.savefig(output, dpi=160)
    plt.close(fig)
    return output
