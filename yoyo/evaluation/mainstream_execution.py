"""Pure accounting adapter for the frozen eight-coin IMACD comparison.

The owner-requested experiment uses long-only decisions on completed 1H/4H
bars. This adapter inherits ``altcoin_accounting`` next-open fills, a stop at
entry minus two decision-bar ATR, and 10 bp of entry notional on each side.
Only ``baseline_3r`` has a fixed 3R target; all other arms exit next open after
md loses its direction. The explicit 36,500-day limit removes the previous
experiment's 30-day exit within this study's bounded historical folds. Fold
end positions are marked at the final close and remain censored. This is not
the Pine episode's additional original-price-zone termination rule.

Matching uses each actual decision CLOSE's UTC month and ATR/close rank in
the 240 observations ending at that decision, split into five equal rank
intervals. Random controls are long md states from the same symbol/timeframe
call, excluding every release and every arm's actual decision. It never uses
outcomes, release-quality filters, later volatility, or another symbol.
The same decision mapping is reused by all arms, even when multiple release
anchors wait until the same decision. Those candidates retain separate IDs.

``relative_volume``, ``near_zero_bars`` and ``momentum10`` are copied from
already-causal input features at decision and anchor; this module does not
construct them. Waiting price change compares the two next-open prices and
is descriptive accounting, never an input to candidate selection. The caller
owns closed-bar validation, causal features, holdout authorization and splits.

Each instrument has one fixed-quantity sleeve. Entry notional equals its
pre-entry equity; this does not maintain a realtime notional/equity 1x cap.
No execution, file IO, model, network, notification or mutable service state
is imported or changed here.
"""

from __future__ import annotations

from collections.abc import Sequence
from numbers import Integral

import numpy as np
import pandas as pd

from yoyo.evaluation.altcoin_accounting import compound_portfolio, evaluate_events


MAX_HOLD_DAYS = 36_500
CONTEXT_COLUMNS = ("relative_volume", "near_zero_bars", "momentum10")
METADATA_COLUMNS = (
    "symbol", "minutes", "fold", "arm", "anchor_i", "decision_i",
    "anchor_time", "anchor_decision_time", "decision_bar_open_time", "month",
    "volbin", "wait_bars", "wait_price_change_bp", "control_indexes",
    "control_event_ids", "requested_control_count", "control_count",
    "control_mean_net_bp", "excess_bp", "portfolio_selected",
    *CONTEXT_COLUMNS, *("anchor_" + name for name in CONTEXT_COLUMNS),
)
CONTROL_METADATA_COLUMNS = (
    "symbol", "minutes", "fold", "control_signal_i", "matched_decision_i",
    "month", "volbin", "matched_month", "matched_volbin", "matched_arms",
)


def _exit_rule(arm: str) -> str:
    return "fixed3r" if arm == "baseline_3r" else "md"


def _matching(features, bars, decision_indexes, *, first_i, last_i, minutes, seed):
    """Create an outcome-blind map, with no control reused across decisions."""
    decision_closes = bars.index + pd.Timedelta(minutes=minutes)
    months = decision_closes.strftime("%Y-%m").to_numpy()
    atr = features.atr.to_numpy(dtype=float)
    ratio = pd.Series(atr / bars.close.to_numpy(dtype=float), index=bars.index)
    buckets = np.ceil(ratio.rolling(240, min_periods=240).rank(method="average", pct=True) * 5)
    buckets = buckets.clip(1, 5).fillna(-1).to_numpy(dtype=int)
    md = features.md.to_numpy(dtype=float)
    release = features.release_side.to_numpy(dtype=float)
    pool = {}
    decisions = set(decision_indexes)
    for i in range(first_i, last_i):
        if (i in decisions or release[i] != 0 or not np.isfinite(md[i]) or md[i] <= 0
                or not np.isfinite(atr[i]) or atr[i] <= 0 or buckets[i] < 1):
            continue
        pool.setdefault((months[i], buckets[i]), []).append(i)
    rng = np.random.default_rng(seed)
    for values in pool.values():
        rng.shuffle(values)
    mapping = {}
    for i in sorted(decisions):
        available = pool.get((months[i], buckets[i]), []) if first_i <= i < last_i else []
        mapping[i] = [available.pop() for _ in range(min(3, len(available)))]
    return mapping, months, buckets


def evaluate_book(
    bars: pd.DataFrame,
    features: pd.DataFrame,
    candidates: Sequence[dict],
    *,
    first_i: int,
    last_i: int,
    symbol: str,
    minutes: int,
    fold: str,
    seed: int,
    arms: Sequence[str] | None = None,
) -> dict:
    """Return all candidate/control rows, per-arm close curves and diagnostics.

    A candidate contains ``arm``, original ``anchor_i`` and actual
    ``decision_i`` integer positions. All are long; caller-provided decisions
    must still have positive md. ``first_i``/``last_i`` are inclusive fold
    positions. Invalid event outcomes are retained, counted and excluded from
    portfolio fills and matched means. ``arms`` retains arms with no signals.
    """
    if minutes not in (60, 240):
        raise ValueError("mainstream comparison supports only 1H and 4H")
    if not features.index.equals(bars.index):
        raise ValueError("features must have the identical bars index")
    if not {"atr", "md", "release_side"}.issubset(features.columns):
        raise ValueError("features require atr, md and release_side")
    if bars.attrs.get("period_seconds", minutes * 60) != minutes * 60:
        raise ValueError("bars period_seconds conflicts with minutes")
    # The inherited engine checks schema/grid/fold bounds even for empty arms.
    book_bars = bars.copy(deep=False)
    book_bars.attrs = {**bars.attrs, "period_seconds": minutes * 60}
    empty_events = evaluate_events(book_bars, features, [], [], first_i=first_i,
                                  last_i=last_i, max_hold_days=MAX_HOLD_DAYS)
    requested = list(arms) if arms is not None else []
    if any(not isinstance(arm, str) or not arm for arm in requested) or len(set(requested)) != len(requested):
        raise ValueError("arms must be unique nonempty strings")
    normalized = []
    for sequence, candidate in enumerate(candidates):
        arm = candidate["arm"]
        anchor, decision = candidate["anchor_i"], candidate["decision_i"]
        if not isinstance(arm, str) or not arm:
            raise ValueError("candidate arm must be a nonempty string")
        if any(isinstance(i, (bool, np.bool_)) or not isinstance(i, Integral) for i in (anchor, decision)):
            raise ValueError("candidate anchor_i and decision_i must be integer positions")
        anchor, decision = int(anchor), int(decision)
        if not 0 <= anchor <= decision < len(bars):
            raise ValueError("candidate must have 0 <= anchor_i <= decision_i < bar count")
        if not np.isfinite(features.md.iloc[decision]) or features.md.iloc[decision] <= 0:
            raise ValueError("long candidate md must remain positive at its actual decision")
        if arms is not None and arm not in requested:
            raise ValueError("candidate arm is absent from the explicit arms list")
        if arm not in requested:
            requested.append(arm)
        normalized.append(dict(arm=arm, anchor_i=anchor, decision_i=decision, sequence=sequence))

    mapping, months, buckets = _matching(features, book_bars,
        {row["decision_i"] for row in normalized}, first_i=first_i,
        last_i=last_i, minutes=minutes, seed=seed)
    outcomes, control_rows = {}, []
    prefix = f"{symbol}:{minutes}:{fold}"
    for rule in dict.fromkeys(_exit_rule(arm) for arm in requested):
        rule_candidates = [row for row in normalized if _exit_rule(row["arm"]) == rule]
        decision_indexes = sorted({row["decision_i"] for row in rule_candidates})
        indexes = sorted(set(decision_indexes) | {j for i in decision_indexes for j in mapping[i]})
        evaluated = evaluate_events(book_bars, features, indexes, [1] * len(indexes),
            first_i=first_i, last_i=last_i, exit_rule=rule, stop_atr=2,
            max_hold_days=MAX_HOLD_DAYS)
        outcomes[rule] = {int(row["signal_i"]): row for row in evaluated.to_dict("records")}
        for decision in decision_indexes:
            matched_arms = "|".join(arm for arm in requested if any(
                row["arm"] == arm and row["decision_i"] == decision for row in rule_candidates))
            for control_i in mapping[decision]:
                row = dict(outcomes[rule][control_i])
                row.update(event_id=f"{prefix}:control:{rule}:{control_i}",
                    symbol=symbol, minutes=minutes, fold=fold, control_signal_i=control_i,
                    matched_decision_i=decision, month=months[control_i], volbin=int(buckets[control_i]),
                    matched_month=months[decision], matched_volbin=int(buckets[decision]),
                    matched_arms=matched_arms)
                control_rows.append(row)

    event_rows = []
    step = pd.Timedelta(minutes=minutes)
    for candidate in normalized:
        arm, anchor, decision = candidate["arm"], candidate["anchor_i"], candidate["decision_i"]
        rule = _exit_rule(arm)
        row = dict(outcomes[rule][decision])
        controls = [outcomes[rule][i] for i in mapping[decision]]
        valid_controls = [c for c in controls if c["valid"] and np.isfinite(c["net_bp"])]
        control_mean = float(np.mean([c["net_bp"] for c in valid_controls])) if valid_controls else np.nan
        waiting = ((bars.open.iloc[decision + 1] / bars.open.iloc[anchor + 1] - 1) * 10_000
                   if decision + 1 <= last_i else np.nan)
        row.update(event_id=f"{prefix}:{arm}:{anchor}:{decision}:{candidate['sequence']}",
            symbol=symbol, minutes=minutes, fold=fold, arm=arm, anchor_i=anchor, decision_i=decision,
            anchor_time=bars.index[anchor], anchor_decision_time=bars.index[anchor] + step,
            decision_bar_open_time=bars.index[decision], month=months[decision], volbin=int(buckets[decision]),
            wait_bars=decision-anchor, wait_price_change_bp=float(waiting),
            control_indexes=" ".join(map(str, mapping[decision])),
            control_event_ids="|".join(f"{prefix}:control:{rule}:{i}" for i in mapping[decision]),
            requested_control_count=len(controls), control_count=len(valid_controls),
            control_mean_net_bp=control_mean,
            excess_bp=float(row["net_bp"] - control_mean) if row["valid"] else np.nan,
            portfolio_selected=False)
        for name in CONTEXT_COLUMNS:
            row[name] = features[name].iloc[decision] if name in features else np.nan
            row["anchor_" + name] = features[name].iloc[anchor] if name in features else np.nan
        event_rows.append(row)

    columns = list(dict.fromkeys([*empty_events.columns, *METADATA_COLUMNS]))
    events = pd.DataFrame(event_rows, columns=columns)
    controls = pd.DataFrame(control_rows,
                            columns=list(dict.fromkeys([*empty_events.columns, *CONTROL_METADATA_COLUMNS])))
    curves, diagnostics = {}, []
    for arm in requested:
        arm_events = events.loc[events.arm.eq(arm)].copy()
        equity, selected, detail = compound_portfolio(book_bars, arm_events, first_i, last_i)
        detail.pop("adverse_equity")
        selected_ids = set(selected.event_id)
        events.loc[events.arm.eq(arm), "portfolio_selected"] = arm_events.event_id.isin(selected_ids).to_numpy()
        curves[arm] = equity
        related_controls = controls.loc[controls.exit_rule.eq(_exit_rule(arm))
            & controls.matched_decision_i.isin(arm_events.decision_i)]
        valid = arm_events.valid.eq(True)
        diagnostics.append(dict(symbol=symbol, minutes=minutes, fold=fold, arm=arm,
            exit_rule=_exit_rule(arm), max_hold_days=MAX_HOLD_DAYS,
            candidate_count=len(arm_events), valid_candidate_count=int(valid.sum()),
            invalid_candidate_count=int((~valid).sum()),
            control_count=len(related_controls), invalid_control_count=int(related_controls.valid.eq(False).sum()),
            unmatched_valid_candidate_count=int((valid & arm_events.control_count.eq(0)).sum()),
            capital_contract="fixed quantity; entry notional equals pre-entry sleeve equity; no realtime 1x cap",
            **detail))
    return dict(events=events, controls=controls, curves=curves, diagnostics=diagnostics)
