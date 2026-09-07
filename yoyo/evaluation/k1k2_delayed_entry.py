"""V39 one-hour confirm-before-entry adapter over the unchanged V37 exit engine.

This is a newly declared RESEARCH entry policy, not an expiry inferred from V38
or a claim of an optimal lifetime. Original hourly request E has allowed entry
boundaries E,E+5m,...,E+55m, strictly before E+1h. V38's pure audit therefore uses
cutoff E+55m; known opposition at its last boundary becomes a policy no-fill at
E+1h without inspecting the excluded E+1h OPEN or its preceding bar. The original
fixed K2 stop and signal ATR never move. Controls use their own original stops.

Sources: yoyo.data.k1k2_pending_entry.audit_pending_entries (complete native5
SMA40(HL2), wait-OHLC stop priority, no recovery across unknown history) and
yoyo.layers.l3_backtest.hourly_impulse.simulate_events (native5 transition_colour,
one confirmation, original-notional .002 roundtrip costs). A delayed fill C uses
integer max_minutes=(E+72h-C)/1min, not fractional hours or end_exclusive as a
substitute deadline. Engine input decision_time=C is temporary; result decision_time
is restored to original E, with entry_time=C and absolute_deadline=E+72h.

Only original request fields are permitted as inputs. No outcomes, arming/MFE,
portfolio, pending-audit fields or economic attrs may influence entry selection.
raw5 and native5 are caller-supplied frames; this module has no file/network I/O.
The audit reads only each visited complete bar and the boundary OPEN. The engine
then uses future raw5 OHLC solely for post-fill outcomes; no saved old exit is used.

Trade net/gross are NaN for non-trades. Request net/gross are zero ONLY for known
stop-invalidated/expired/risk-refused no-fills; unknown entry/path outcomes remain
NaN. Each original request survives, even if no request is eligible. entry_ledger
reorders decisions by actual fills or earliest uncertainty, never by original E.
Pending reservations are off. A first-entry uncertainty conservatively blocks
later fills as an explicitly unknown ledger state, NOT as an invented position.

Pandas 2.3 timestamp and copy semantics:
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.Timestamp.html
https://pandas.pydata.org/pandas-docs/version/2.3/reference/api/pandas.DataFrame.copy.html
"""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Number

import numpy as np
import pandas as pd

from yoyo.data.k1k2_pending_entry import EVENT_COLUMNS, audit_pending_entries
from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


BASE_POLICY = {"management_minutes": 5, "exit_mode": "transition_colour",
               "confirmations": 1, "max_hours": 72, "cost_fraction": 0.002}
FIVE = pd.Timedelta(minutes=5)
HOUR = pd.Timedelta(hours=1)
HORIZON = pd.Timedelta(hours=72)
ADAPTER_COLUMNS = ["original_decision_time", "absolute_deadline", "entry_deadline",
                   "remaining_minutes", "entry_policy_status", "entry_policy_known_at",
                   "entry_policy_reason", "request_uncertainty_at", "request_net",
                   "request_gross", "request_known"]
TRADE_DEFAULTS = dict(entry_time=pd.NaT, entry_price=np.nan, exit_time=pd.NaT,
    exit_price=np.nan, closed=False, outcome=None, gross_return=np.nan,
    net_return=np.nan, net_r=np.nan, risk_pct=np.nan, risk_atr=np.nan,
    hold_minutes=np.nan, partial_fraction=0.0, exit_remaining_fraction=np.nan,
    partial_exit_time=pd.NaT, partial_exit_price=np.nan,
    realised_partial_gross_return=np.nan, marked_gross_return=np.nan,
    marked_net_return=np.nan, max_favourable_r=np.nan, max_adverse_r=np.nan,
    bars_to_first_positive=np.nan, funding_modelled=False)


def _clock(value, name, nullable=False):
    if nullable and (value is None or value is pd.NaT or value is pd.NA
                     or (isinstance(value, float) and np.isnan(value))):
        return pd.NaT
    if isinstance(value, (Number, np.number)):
        raise ValueError(name + " must be an explicit timezone-aware clock")
    try:
        t = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(name + " must be a timestamp") from exc
    if pd.isna(t):
        if nullable:
            return pd.NaT
        raise ValueError(name + " must not be missing")
    if t.tzinfo is None:
        raise ValueError(name + " must have an explicit timezone")
    t = t.tz_convert("UTC")
    if t != t.floor("5min"):
        raise ValueError(name + " must be on the five-minute grid")
    return t


def _policy(policy):
    if not isinstance(policy, Mapping) or set(policy) != set(BASE_POLICY):
        raise ValueError("policy must be exactly the five frozen V37 transition keys")
    for key, expected in BASE_POLICY.items():
        value = policy[key]
        if isinstance(value, (bool, np.bool_)) or value != expected:
            raise ValueError("different V37 exit policy: " + key)
        if key != "exit_mode" and not isinstance(value, (int, float, np.integer, np.floating)):
            raise ValueError("policy numbers must be numeric and nonboolean")
    return dict(BASE_POLICY)


def _economic_name(name):
    key = str(name).lower()
    return (key in set(TRADE_DEFAULTS) | set(ADAPTER_COLUMNS) | set(EVENT_COLUMNS)
            | {"audit_cutoff", "pnl", "mfe", "mae", "winner", "loser", "returns",
               "net", "gross", "performance", "failure_class", "flow_pass"}
            or key.startswith(("transition_", "portfolio_", "partial_", "failed_",
                               "launch_", "marked_", "request_", "entry_policy_")))


def _requests(requests):
    required = {"event_id", "decision_time", "direction", "initial_stop", "signal_atr"}
    if not isinstance(requests, pd.DataFrame) or not requests.columns.is_unique or not required.issubset(requests):
        raise ValueError("original requests require unique columns and original identity/risk fields")
    if any(_economic_name(name) for name in requests.columns) or any(_economic_name(name) for name in requests.attrs):
        raise ValueError("outcome, pending or economic metadata is not an entry input")
    clocks = [_clock(value, "decision_time") for value in requests.decision_time]
    if any(t != t.floor("h") for t in clocks):
        raise ValueError("original K2 decisions must be UTC hour boundaries")
    return pd.DatetimeIndex(clocks)


def replay_delayed(raw5, native5, requests, policy, end_exclusive=None):
    """Return (all-original-request results, pure audit events, visited trace).

    Inputs retain the pure audit's event ID/direction/positive stop/ATR contract.
    end_exclusive is a source/fold censor boundary, NOT the strategy deadline; if
    supplied it must permit all entry checks through E+55m. Earlier post-fill
    source cutoffs may censor a position, but never reset its E+72h deadline.
    The caller separately enforces the frozen original four-fold/72h embargo.
    """
    chosen = _policy(policy)
    origins = _requests(requests)
    cutoff = _clock(end_exclusive, "end_exclusive") if end_exclusive is not None else None
    if cutoff is not None and any(t+HOUR-FIVE >= cutoff for t in origins):
        raise ValueError("end_exclusive does not cover the full allowed entry window")
    audit_input = requests.copy(deep=True)
    audit_input["audit_cutoff"] = pd.array(origins+HOUR-FIVE, dtype="datetime64[ns, UTC]")
    audit, trace = audit_pending_entries(audit_input, native5, raw5)
    if list(audit.event_id) != list(requests.event_id):
        raise AssertionError("pending audit changed request identity/order")
    pd.testing.assert_frame_equal(audit[list(requests)].reset_index(drop=True),
                                  requests.reset_index(drop=True), check_exact=True)
    source_records = requests.to_dict("records")
    original_by_id = {row["event_id"]: row for row in source_records}
    evidence = audit.to_dict("records")
    evidence_by_id = {row["event_id"]: row for row in evidence}
    results, selected = {}, []
    for row, item, origin in zip(source_records, evidence, origins):
        event_id, status = row["event_id"], item["status"]
        terminal = _clock(item["status_known_at"], "audit status clock")
        result = dict(TRADE_DEFAULTS)
        result.update(original_decision_time=origin, absolute_deadline=origin+HORIZON,
            entry_deadline=origin+HOUR, remaining_minutes=pd.NA,
            entry_policy_status=status, entry_policy_known_at=terminal,
            entry_policy_reason=item["status_reason"], request_uncertainty_at=pd.NaT,
            request_net=np.nan, request_gross=np.nan, request_known=False)
        if status == "eligible":
            at = _clock(item["eligible_at"], "eligible_at")
            if not origin <= at < origin+HOUR or at != terminal:
                raise AssertionError("first eligible time is outside original decision-hour")
            duration = (origin+HORIZON-at).value
            if duration <= 0 or duration % FIVE.value:
                raise AssertionError("remaining duration must be a positive complete five-minute duration")
            minutes = int(duration // pd.Timedelta(minutes=1).value)
            if at+pd.Timedelta(minutes=minutes) != origin+HORIZON:
                raise AssertionError("absolute original horizon changed")
            result["remaining_minutes"] = minutes
            execution = dict(row)
            execution.update(decision_time=at, original_decision_time=origin,
                             absolute_deadline=origin+HORIZON)
            selected.append((minutes, execution))
        elif status in ("invalidated_open", "invalidated_wait_bar", "pending_at_cutoff"):
            result.update(outcome="no_fill_expired" if status == "pending_at_cutoff" else "no_fill_stop",
                          request_net=0.0, request_gross=0.0, request_known=True)
            if status == "pending_at_cutoff":
                if terminal != origin+HOUR-FIVE:
                    raise AssertionError("expiry requires every allowed entry boundary to be known")
                result.update(entry_policy_status="expired_no_entry", entry_policy_known_at=origin+HOUR,
                              entry_policy_reason="original_decision_hour_expired")
        elif status in ("unknown_raw", "unknown_management"):
            result.update(outcome="entry_policy_unknown", request_uncertainty_at=terminal)
        else:
            raise AssertionError("unexpected audit terminal status")
        results[event_id] = result

    # Group by exact remaining minutes; no per-event mutable global policy or
    # fractional-hour arithmetic, and no fabricated first row when all abstain.
    for minutes in sorted({minutes for minutes, _ in selected}):
        entries = pd.DataFrame([row for left, row in selected if left == minutes])
        arm_policy = dict(chosen, max_minutes=minutes)
        trades = simulate_events(raw5, native5, entries, arm_policy, end_exclusive=cutoff)
        if trades.event_id.isna().any() or trades.event_id.duplicated().any() or set(trades.event_id) != set(entries.event_id):
            raise AssertionError("execution changed original event identity")
        expected = entries.set_index("event_id").sort_index()
        actual = trades.set_index("event_id").sort_index()
        pd.testing.assert_frame_equal(expected, actual[expected.columns], check_dtype=False, check_exact=True)
        for trade in trades.to_dict("records"):
            event_id = trade["event_id"]
            result = results[event_id]
            origin = result["original_decision_time"]
            at = _clock(trade["entry_time"], "actual entry_time")
            if at != _clock(trade["decision_time"], "execution decision_time") or at+pd.Timedelta(minutes=minutes) != origin+HORIZON:
                raise AssertionError("entry/deadline clock drift")
            # Source fields are restored from the untouched request, never from
            # inferred trade outcomes. Only actual entry_time remains delayed.
            for name, value in trade.items():
                if name not in original_by_id[event_id] and name not in ADAPTER_COLUMNS:
                    result[name] = value
            if trade["outcome"] == "entry_invalid_risk":
                result.update(entry_time=pd.NaT, entry_price=np.nan, exit_time=pd.NaT,
                              exit_price=np.nan, closed=False, gross_return=np.nan, net_return=np.nan,
                              request_net=0.0, request_gross=0.0, request_known=True)
            elif str(trade["outcome"]).startswith("entry_"):
                result.update(entry_time=pd.NaT, entry_price=np.nan, exit_time=pd.NaT,
                              exit_price=np.nan, closed=False,
                              request_uncertainty_at=at)
            else:
                known_entry = evidence_by_id[event_id]
                if (trade["transition_initial_state"] != "aligned"
                        or _clock(trade["transition_initial_open_time"], "engine seed")+FIVE != at):
                    raise AssertionError("engine did not initialize from the confirmed aligned seed")
                for observed, audited in [("entry_price", "reference_open"), ("risk_pct", "risk_pct"), ("risk_atr", "risk_atr")]:
                    if not np.isclose(trade[observed], known_entry[audited], rtol=1e-12, atol=1e-12):
                        raise AssertionError("audit/execution entry risk or quote drift")
                if bool(trade["closed"]):
                    if not np.isfinite([trade["gross_return"], trade["net_return"]]).all():
                        raise AssertionError("closed execution must have finite returns")
                    exit_at = _clock(trade["exit_time"], "exit_time")
                    if not at <= exit_at <= origin+HORIZON:
                        raise AssertionError("closed exit passed the original absolute deadline")
                    result.update(request_net=trade["net_return"], request_gross=trade["gross_return"],
                                  request_known=True)
                else:
                    result.update(request_uncertainty_at=_clock(trade["exit_time"], "censor clock", nullable=True))
                    if pd.isna(result["request_uncertainty_at"]):
                        result["request_uncertainty_at"] = at

    output = requests.copy(deep=True)
    added = list(TRADE_DEFAULTS)+ADAPTER_COLUMNS
    added += sorted({name for row in results.values() for name in row}-set(added))
    for name in added:
        if name in requests:
            continue
        values = [results[event_id].get(name, pd.NA) for event_id in requests.event_id]
        if name in {"entry_time", "exit_time", "partial_exit_time", "original_decision_time",
                    "absolute_deadline", "entry_deadline", "entry_policy_known_at", "request_uncertainty_at"}:
            output[name] = pd.array(values, dtype="datetime64[ns, UTC]")
        elif name in {"closed", "request_known"}:
            output[name] = pd.array(values, dtype="boolean")
        elif name == "remaining_minutes":
            output[name] = pd.array(values, dtype="Int64")
        else:
            output[name] = values
    return output, audit, trace


def annotate_immediate(trades):
    """Add common request accounting to baseline trades without changing old fields.

    The caller already replayed the exact V37 immediate policy. Original engine
    entry_* failures may carry entry_time=E despite no fill: the ledger routes
    them by outcome, never treats that placeholder as an executed position.
    """
    required = {"event_id", "decision_time", "entry_time", "exit_time", "closed",
                "outcome", "gross_return", "net_return"}
    if not isinstance(trades, pd.DataFrame) or not trades.columns.is_unique or not required.issubset(trades):
        raise ValueError("immediate annotation needs complete baseline trade columns")
    if trades.event_id.isna().any() or trades.event_id.duplicated().any():
        raise ValueError("baseline identities must be unique and non-null")
    if set(ADAPTER_COLUMNS) & set(trades) or any(str(k).startswith("portfolio_") for k in trades):
        raise ValueError("baseline has already been annotated or portfolio-filtered")
    records = []
    for row in trades.to_dict("records"):
        origin = _clock(row["decision_time"], "original decision_time")
        if origin != origin.floor("h"):
            raise ValueError("baseline original decisions must be hourly")
        if not isinstance(row["closed"], (bool, np.bool_)):
            raise ValueError("baseline closed must be boolean")
        note = dict(original_decision_time=origin, absolute_deadline=origin+HORIZON,
                    entry_deadline=origin+HOUR, remaining_minutes=pd.NA,
                    entry_policy_status="immediate", entry_policy_known_at=origin,
                    entry_policy_reason="original_immediate_entry", request_uncertainty_at=pd.NaT,
                    request_net=np.nan, request_gross=np.nan, request_known=False)
        if row["outcome"] == "entry_invalid_risk":
            note.update(request_net=0.0, request_gross=0.0, request_known=True)
        elif str(row["outcome"]).startswith("entry_"):
            note["request_uncertainty_at"] = origin
        else:
            if _clock(row["entry_time"], "baseline entry_time") != origin:
                raise ValueError("immediate baseline entry_time must equal original E")
            note["remaining_minutes"] = 4320
            if row["closed"]:
                end = _clock(row["exit_time"], "baseline exit_time")
                if not origin <= end <= origin+HORIZON or not np.isfinite([row["gross_return"], row["net_return"]]).all():
                    raise ValueError("invalid closed baseline path")
                note.update(request_net=row["net_return"], request_gross=row["gross_return"], request_known=True)
            else:
                at = _clock(row["exit_time"], "baseline censor clock", nullable=True)
                note["request_uncertainty_at"] = origin if pd.isna(at) else at
        records.append(note)
    output = trades.copy(deep=True)
    for name in ADAPTER_COLUMNS:
        values = [row[name] for row in records]
        if name in {"original_decision_time", "absolute_deadline", "entry_deadline",
                    "entry_policy_known_at", "request_uncertainty_at"}:
            output[name] = pd.array(values, dtype="datetime64[ns, UTC]")
        elif name == "request_known":
            output[name] = pd.array(values, dtype="boolean")
        elif name == "remaining_minutes":
            output[name] = pd.array(values, dtype="Int64")
        else:
            output[name] = values
    return output


def entry_ledger(results):
    """All-row single-position diagnostic: no pending reservation, unknown blocks.

    Known no-fills have no occupancy. An unknown first entry activates a permanent
    conservative uncertainty block at request_uncertainty_at; it is not marked
    portfolio_selected and does not assert a real trade. A selected entered but
    censored position blocks later entries. Events sort by actual entry/uncertainty
    time and event_id; exact known exit==next entry is permitted. The returned row
    order/index/attrs match results, with explicit scheduler diagnostics added.
    """
    required = {"event_id", "entry_time", "exit_time", "closed", "outcome",
                "request_known", "request_uncertainty_at", "entry_policy_known_at"}
    if not isinstance(results, pd.DataFrame) or not results.columns.is_unique or not required.issubset(results):
        raise ValueError("ledger needs complete request accounting and clocks")
    if results.event_id.isna().any() or results.event_id.duplicated().any():
        raise ValueError("ledger identities must be unique and non-null")
    if any(str(name).startswith("portfolio_") for name in results):
        raise ValueError("refuse to reuse an existing portfolio mask")
    rows = results.to_dict("records")
    selected, reasons = [False]*len(rows), [""]*len(rows)
    blockers, blocked_here, event_times = [None]*len(rows), [False]*len(rows), [pd.NaT]*len(rows)
    scheduled = []
    for i, row in enumerate(rows):
        if not isinstance(row["request_known"], (bool, np.bool_)) or not isinstance(row["closed"], (bool, np.bool_)):
            raise ValueError("ledger known/closed must be boolean, not null")
        entry = _clock(row["entry_time"], "entry_time", nullable=True)
        nonentry = str(row["outcome"]).startswith(("entry_", "no_fill_"))
        if pd.notna(entry) and not nonentry:
            event_times[i] = entry
            scheduled.append((entry, row["event_id"], i, "fill"))
        elif row["request_known"]:
            reasons[i] = "known_no_fill"
            event_times[i] = _clock(row["entry_policy_known_at"], "entry_policy_known_at")
        else:
            at = _clock(row["request_uncertainty_at"], "request_uncertainty_at")
            event_times[i] = at
            scheduled.append((at, row["event_id"], i, "unknown"))
    occupied_until = None
    active_id = uncertainty_id = None
    uncertainty_kind = None
    for at, event_id, i, kind in sorted(scheduled):
        row = rows[i]
        if kind == "unknown":
            reasons[i] = "entry_unknown"
            if uncertainty_id is None:
                uncertainty_id, uncertainty_kind = event_id, "uncertain_entry"
                blocked_here[i] = True
            blockers[i] = uncertainty_id
        elif uncertainty_id is not None:
            reasons[i], blockers[i] = uncertainty_kind, uncertainty_id
        elif occupied_until is not None and at < occupied_until:
            reasons[i], blockers[i] = "position_open", active_id
        else:
            selected[i] = True
            if row["closed"]:
                exit_at = _clock(row["exit_time"], "exit_time")
                if exit_at < at:
                    raise ValueError("exit precedes actual entry")
                occupied_until, active_id = exit_at, event_id
            else:
                uncertainty_id, uncertainty_kind = event_id, "uncertain_position"
                blocked_here[i] = True
    output = results.copy(deep=True)
    output["portfolio_selected"] = selected
    output["portfolio_skip_reason"] = reasons
    output["portfolio_blocker_id"] = blockers
    output["portfolio_uncertainty_block"] = blocked_here
    output["portfolio_event_time"] = pd.array(event_times, dtype="datetime64[ns, UTC]")
    return output
