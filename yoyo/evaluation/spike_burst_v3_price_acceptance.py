"""Research F: one next-close price check over the unchanged original V3 stream.

Source: exp-spike-v3-price-acceptance-20260910-v1/PROJECT_PLAN.md. Original
V3 supplies its full-condition rising edge, accepted-candidate twelve-bar
cooldown and child ages zero through three without any feedback from F.
Those source fields use current ready/close/SMA20/EMA20, prior twelve highs,
prior twelve density observations, three-bar advance/volume and current
MD/SB/ZLEMA direction, as documented in spike_burst_early_warning. F adds only
the next complete hourly close versus that candidate's frozen prior12 high.
It adds no low, volume, ATR, ready, moving-average or higher-timeframe gate.

At candidate t the status is pending. At continuous t+1, close strictly above
the frozen high accepts; equality/below rejects. Missing hours or unavailable
OHLC make adjudication unknown. Missing volume does not affect this check.
An accepted parent is public only at t+1 close, with that close as its price;
the next possible economic entry is t+2 open, handled by the study executor.
Original children seen at t/t+1 publish with acceptance; children first seen
at t+2/t+3 publish on their real close. Rejected/unknown parents never revive.
No reference holding, entry, stop, return, future outcome or label is used.

detect() is a causal per-bar snapshot, including pending at a sample's end.
candidates() is a separate end-of-sample adjudication registry, NOT a feature
table: it may classify a final pending record as unknown, with no observed
decision_time. Neither API backdates an accepted or confirmed signal. Here
parent_i is the original candidate identity, NOT the public early index;
economic callers must use acceptance_i for the F parent and publication_i
for the event clock. Original-child and publication clocks are independent.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation import spike_burst_early_warning as v3

HOUR = v3.HOUR
SOURCE_COLUMNS = ("early", "confirmed", "parent_i", "frozen_parent_high",
                  "confirm_age", "candidate_edge", "cooldown_blocked")
FLOAT_COLUMNS = ("parent_i", "candidate_i", "candidate_close", "frozen_parent_high",
    "acceptance_i", "acceptance_close", "original_child_i", "original_child_close",
    "child_publication_i", "child_publication_close", "publication_i",
    "publication_close", "decision_i", "decision_close", "delay_bars",
    "confirm_age", "publication_delay_bars")
TIME_COLUMNS = ("candidate_time", "acceptance_time", "original_child_time",
    "child_publication_time", "publication_time", "decision_time", "expected_decision_time")
TEXT_COLUMNS = ("candidate_status", "reason")
BOOL_COLUMNS = ("early", "confirmed", "candidate_edge", "cooldown_blocked")
REGISTRY_COLUMNS = ("candidate_i", "candidate_time", "candidate_close", "frozen_parent_high",
    "candidate_status", "reason", "decision_i", "decision_time", "decision_close",
    "expected_decision_time", "delay_bars", "acceptance_i", "acceptance_time",
    "acceptance_close", "original_child_i", "original_child_time", "original_child_close",
    "child_publication_i", "child_publication_time", "child_publication_close",
    "publication_i", "publication_time", "publication_close", "publication_delay_bars",
    "asof_time", "registry_only")


def _empty_record():
    return {**dict.fromkeys(FLOAT_COLUMNS, np.nan),
            **dict.fromkeys(TIME_COLUMNS, pd.NaT),
            **dict.fromkeys(TEXT_COLUMNS, "")}


def _complete_ohlc(row):
    """Missing/broken price data is unknown, never a successful rejection."""
    values = np.asarray([row.open, row.high, row.low, row.close], dtype=float)
    return bool(np.isfinite(values).all() and (values > 0).all()
                and row.low <= min(row.open, row.close)
                and row.high >= max(row.open, row.close))


def detect(frame):
    """Return prefix-stable F events and unchanged prefixed original V3 fields.

    F early/confirmed occur only at their public close. candidate_i and
    parent_i retain original identity even when original V3's child window
    has expired. Metadata describes the most recently observed candidate;
    only early/confirmed identify public events. publication_* fields are
    populated only on these public bars. original_child_* become known only
    when V3 actually observes that child, even for a rejected candidate.
    """
    source = v3.detect(frame)
    if not source.index.equals(frame.index):
        raise ValueError("Original V3 stream must align exactly with source bars")
    record = _empty_record()
    rows = []
    for i, (bar, original) in enumerate(zip(frame.itertuples(), source.itertuples())):
        clock = frame.index[i] + HOUR
        early = confirmed = False
        if bool(original.early):
            # Original V3's twelve-bar clock and gap reset prevent a new
            # candidate from overlapping an undecided continuous t+1.
            record = _empty_record()
            record.update(parent_i=float(i), candidate_i=float(i), candidate_time=clock,
                candidate_close=float(bar.close), frozen_parent_high=float(original.frozen_parent_high),
                expected_decision_time=clock + HOUR, candidate_status="pending",
                reason="awaiting_next_close")
        if bool(original.confirmed):
            if pd.isna(record["candidate_i"]) or float(original.parent_i) != record["candidate_i"]:
                raise ValueError("Original child does not belong to its observed candidate")
            if pd.notna(record["original_child_i"]):
                raise ValueError("Original V3 emitted more than one child for a candidate")
            record.update(original_child_i=float(i), original_child_time=clock,
                          original_child_close=float(bar.close))
        if record["candidate_status"] == "pending" and i > record["candidate_i"]:
            parent = int(record["candidate_i"])
            record.update(decision_i=float(i), decision_time=clock, decision_close=float(bar.close))
            if i != parent + 1 or frame.index[i] - frame.index[parent] != HOUR:
                record.update(candidate_status="unknown", reason="missing_next_hour")
            elif not _complete_ohlc(bar):
                record.update(candidate_status="unknown", reason="invalid_next_ohlc")
            elif bar.close > record["frozen_parent_high"]:
                early = True
                record.update(candidate_status="accepted", reason="close_above_frozen_high",
                    acceptance_i=float(i), acceptance_time=clock, acceptance_close=float(bar.close),
                    delay_bars=1.)
            else:
                record.update(candidate_status="rejected", reason="close_at_or_below_frozen_high",
                              delay_bars=1.)
        if (record["candidate_status"] == "accepted"
                and pd.notna(record["original_child_i"])
                and pd.isna(record["child_publication_i"])):
            confirmed = True
            record.update(child_publication_i=float(i), child_publication_time=clock,
                          child_publication_close=float(bar.close))
        row = dict(record, early=early, confirmed=confirmed,
            candidate_edge=bool(original.candidate_edge),
            cooldown_blocked=bool(original.cooldown_blocked))
        if early or confirmed:
            row.update(publication_i=float(i), publication_time=clock, publication_close=float(bar.close))
        if confirmed:
            row.update(confirm_age=record["original_child_i"] - record["candidate_i"],
                       publication_delay_bars=float(i) - record["original_child_i"])
        rows.append(row)
    state = pd.DataFrame(rows, index=frame.index)
    # Explicit dtypes keep a prefix with no events identical to a longer
    # history containing its first event (especially all-NaT UTC columns).
    for col in FLOAT_COLUMNS:
        state[col] = state[col].astype(float)
    for col in TIME_COLUMNS:
        state[col] = pd.to_datetime(state[col], utc=True)
    for col in BOOL_COLUMNS:
        state[col] = state[col].astype(bool)
    for col in TEXT_COLUMNS:
        state[col] = state[col].astype(object)
    original = source.rename(columns={col: "v3_" + col for col in SOURCE_COLUMNS})
    return original.join(state)


def candidates(frame, state=None):
    """End-of-sample registry; never use its final status as a t-time feature.

    Pass an existing detect() state to avoid rerunning original V3. Records
    include all source candidates, regardless of any study date boundary.
    Last-bar pending is registry-only unknown with no observed adjudication;
    actual gap/invalid-bar unknown has its observed decision_time. Acceptance
    and child/publication metadata are retained on distinct clocks.
    """
    if state is None:
        state = detect(frame)
    if not state.index.equals(frame.index):
        raise ValueError("Candidate registry requires exactly aligned source and state")
    indices = np.flatnonzero(state.v3_early.to_numpy())
    records = []
    for k, parent in enumerate(indices):
        end = int(indices[k + 1]) if k + 1 < len(indices) else len(state)
        observed = state.iloc[int(parent):end]
        last = observed.iloc[-1]
        row = {col: last[col] for col in REGISTRY_COLUMNS
               if col not in ("asof_time", "registry_only")}
        public = observed.loc[observed.early | observed.confirmed]
        if len(public):
            for col in ("publication_i", "publication_time", "publication_close", "publication_delay_bars"):
                row[col] = public.iloc[-1][col]
        row.update(asof_time=state.index[-1] + HOUR, registry_only=True)
        if row["candidate_status"] == "pending":
            row.update(candidate_status="unknown", reason="no_next_bar_at_sample_end")
        records.append(row)
    registry = pd.DataFrame(records, columns=REGISTRY_COLUMNS)
    for col in set(TIME_COLUMNS) & set(REGISTRY_COLUMNS) | {"asof_time"}:
        registry[col] = pd.to_datetime(registry[col], utc=True)
    for col in set(FLOAT_COLUMNS) & set(REGISTRY_COLUMNS):
        registry[col] = registry[col].astype(float)
    registry["registry_only"] = registry.registry_only.astype(bool)
    return registry
