"""V26 full-clock opportunity accounting, without labels or executions.

All UTC decision hours in each frozen halfyear [start,end-72h) and BOTH
directions remain. Shape uses only own OHLC/RMA14/previous-body features.
Reference availability never shrinks the old arm. Known-false shape makes
entry false even if reference is unknown; for qualified shapes missing active
inputs remain unknown. Mean/ATR difference and overlap are descriptive only.
No fitted threshold, random sample, price reader or future outcome is used.

https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.merge.html
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.data.hourly_impulse import ENTRY_COLUMNS, make_entries
from yoyo.evaluation.hourly_impulse_structure_event_support import DEFAULT_FOLDS, _time

PARAMS = {"shape": "large_or_engulf", "body_ratio_min": .65, "range_atr_min": 1,
          "engulf_range_atr_min": .65, "close_location_min": .7, "min_volume_ratio": 0,
          "max_cross_count": 999, "min_efficiency": 0, "require_ma_slope": False,
          "require_breakout20": False, "min_extension_atr": 0, "max_extension_atr": 99, "side": "both"}


def opportunity_grid(folds=DEFAULT_FOLDS):
    records = []
    seen = set()
    for fold, start, end in folds:
        if fold in seen:
            raise ValueError("Duplicate fold")
        seen.add(fold)
        start, end = _time(start), _time(end) - pd.Timedelta(hours=72)
        if start >= end:
            raise ValueError("Empty fold after embargo")
        for decision in pd.date_range(start, end, freq="h", inclusive="left"):
            signal = decision - pd.Timedelta(hours=1)
            for direction, suffix in ((1, "L"), (-1, "S")):
                records.append({"event_id": signal.isoformat()+"_"+suffix, "signal_time": signal,
                                "decision_time": decision, "direction": direction, "fold": fold,
                                "month": decision.strftime("%Y-%m")})
    result = pd.DataFrame(records)
    if result.empty or not result.event_id.is_unique:
        raise ValueError("Empty/overlapping opportunity grid")
    return result


def accepted_entries(featured, folds=DEFAULT_FOLDS):
    entries = make_entries(featured, PARAMS)
    parts = []
    for fold, start, end in folds:
        mask = entries.decision_time.ge(_time(start)) & entries.decision_time.lt(_time(end)-pd.Timedelta(hours=72))
        parts.append(entries.loc[mask].assign(fold=fold))
    return pd.concat(parts, ignore_index=True)


def assert_original_parity(entries, original):
    """Every saved original entry column, not merely total251, must reproduce."""
    columns = ENTRY_COLUMNS + ["fold"]
    for frame in (entries, original):
        if not set(columns).issubset(frame) or not frame.event_id.is_unique or frame.event_id.isna().any():
            raise ValueError("Invalid original entry schema/identities")
    a, b = [f[columns].sort_values("event_id").reset_index(drop=True).copy() for f in (entries, original)]
    for f in (a, b):
        for column in ("signal_time", "decision_time"):
            f[column] = f[column].map(_time)
    pd.testing.assert_frame_equal(a, b, check_dtype=False, rtol=1e-12, atol=1e-12)


def shape_state(row, direction):
    if row is None:
        return "unknown", "missing_signal_hour"
    location = row.long_close_location if direction == 1 else row.short_close_location
    if not all(np.isfinite(v) for v in (row.atr, row.body_ratio, row.range_atr, location)) or row.atr <= 0:
        return "unknown", "shape_normalization_unavailable"
    engulf = row.bullish_engulf if direction == 1 else row.bearish_engulf
    qualified = ((row.body_ratio >= .65 and row.range_atr >= 1) or (engulf and row.range_atr >= .65)) and location >= .7
    return ("qualified", "shape_qualified") if qualified else ("not_qualified", "shape_rejected")


def _state(row, shape, shape_reason, event_id, accepted, direction):
    if shape == "unknown":
        return "unknown", shape_reason
    if shape == "not_qualified":
        return "abstain", "shape_rejected"
    if not row.reference_known:
        return "unknown", row.reference_reason
    extension = direction * (row.close-row.ma)/row.atr
    if not np.isfinite(row.cross_count24) or not np.isfinite(extension):
        return "unknown", "active_entry_feature_unavailable"
    return ("accepted", "entry_accepted") if event_id in accepted else ("abstain", "reference_or_active_gate_rejected")


def build_support(sma, vwma, sma_entries, vwma_entries, folds=DEFAULT_FOLDS):
    common = ["open_time", "open", "high", "low", "close", "volume", "segment_id", "hl2", "atr",
              "body_ratio", "range_atr", "long_close_location", "short_close_location", "volume_ratio",
              "bullish_engulf", "bearish_engulf", "efficiency24", "prior_high20", "prior_low20", "prior_range_median20"]
    pd.testing.assert_frame_equal(sma[common], vwma[common], check_dtype=False, rtol=0, atol=0)
    grid = opportunity_grid(folds)
    lookup = {arm: {r.open_time: r for r in frame.itertuples()} for arm, frame in (("sma", sma), ("vwma", vwma))}
    if len(lookup["sma"]) != len(sma) or len(lookup["vwma"]) != len(vwma):
        raise ValueError("Duplicate source hours")
    accepted = {"sma": set(sma_entries.event_id), "vwma": set(vwma_entries.event_id)}
    if not all(ids.issubset(grid.event_id) for ids in accepted.values()):
        raise ValueError("Entry outside full opportunity grid")
    rows = []
    for request in grid.to_dict("records"):
        a, b = [lookup[k].get(request["signal_time"]) for k in ("sma", "vwma")]
        shape, reason = shape_state(a, request["direction"])
        record = dict(request, shape_status=shape, shape_reason=reason)
        for arm, row in (("sma", a), ("vwma", b)):
            state, why = _state(row, shape, reason, request["event_id"], accepted[arm], request["direction"])
            record[arm+"_state"], record[arm+"_reason"] = state, why
            for field in ("ma", "ma_side", "ma_slope_atr", "cross_count24", "reference_known", "reference_reason"):
                record[arm+"_"+field] = getattr(row, field) if row is not None else None
        x, y = record["sma_state"], record["vwma_state"]
        record["overlap"] = ("any_unknown" if "unknown" in (x, y) else "both_accepted" if x==y=="accepted"
                             else "sma_only" if x=="accepted" else "vwma_only" if y=="accepted" else "both_abstain")
        record["reference_delta_atr"] = ((b.ma-a.ma)/a.atr if a is not None and b is not None and a.atr > 0 else np.nan)
        rows.append(record)
    ledger = pd.DataFrame(rows)
    for arm in accepted:
        if set(ledger.loc[ledger[arm+"_state"].eq("accepted"), "event_id"]) != accepted[arm]:
            raise ValueError("Three-valued ledger drift from frozen entry selector")
    counts = []
    months = sorted(grid.month.unique())
    for population, frame in (("all_clock_directions", ledger), ("shape_qualified", ledger.loc[ledger.shape_status.eq("qualified")])):
        for dimension, keys in (("all", ["all"]), ("fold", [f[0] for f in folds]), ("month", months), ("direction", [1, -1])):
            for key in keys:
                part = frame if dimension == "all" else frame.loc[frame[dimension].eq(key)]
                for arm in ("sma", "vwma"):
                    state = part[arm+"_state"]
                    counts.append({"population": population, "dimension": dimension, "key": str(key), "arm": arm,
                                   "total": len(part), **{s: int(state.eq(s).sum()) for s in ("accepted", "abstain", "unknown")}})
    support = {}
    for arm in ("sma", "vwma"):
        own = ledger.loc[ledger[arm+"_state"].eq("accepted")]
        values = {"events": len(own), "minimum_per_fold": min(int(own.fold.eq(f[0]).sum()) for f in folds),
                  "active_months": own.month.nunique(), "minimum_months_per_fold": min(own.loc[own.fold.eq(f[0]), "month"].nunique() for f in folds)}
        gates = {k: values[k] >= v for k, v in {"events":80,"minimum_per_fold":12,"active_months":12,"minimum_months_per_fold":3}.items()}
        support[arm] = {"values":values,"gates":gates,"pass":all(gates.values())}
    different = accepted["sma"] != accepted["vwma"]
    summary = {"population":len(ledger), "shape_qualified":int(ledger.shape_status.eq("qualified").sum()),
               "shape_unknown":int(ledger.shape_status.eq("unknown").sum()), "support":support,
               "overlap":{k:int(ledger.overlap.eq(k).sum()) for k in ("both_accepted","sma_only","vwma_only","both_abstain","any_unknown")},
               "accepted_identity_sets_differ":different,
               "status":"no_entry_change" if not different else "support_pass_requires_new_background" if support["vwma"]["pass"] else "insufficient_support_no_outcomes",
               "outcomes_read_or_computed":False, "economic_acceptance":False, "random_controls_assigned":False}
    return {"opportunities":ledger,"shape_opportunities":ledger.loc[ledger.shape_status.eq("qualified")].copy(),
            "counts":pd.DataFrame(counts),"sma_entries":sma_entries,"vwma_entries":vwma_entries}, summary
