"""Replay causally delayed realignment requests under one maternal clock.

Only request timestamps determine the remaining duration. Mother K1 stop and
ATR columns are passed unchanged to the existing L3 simulator; future raw5 bars
remain outcome labels, never inputs to waiting/selection. The original mother's
72h deadline is not restarted after waiting. Every request is independently
replayed, not compounded into a portfolio. Terminal observed nonentries are
zero; missing/invalid-price paths remain unknown and reserve maternal occupancy.

The integer-minute duration avoids floating-hour truncation in pandas 2.3.3:
https://pandas.pydata.org/pandas-docs/version/2.3.3/reference/api/pandas.Timedelta.html
The optional L3 max_minutes contract wins over inherited max_hours explicitly.
"""
from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from yoyo.evaluation.hourly_impulse_k2_research import episode_ledger
from yoyo.layers.l3_backtest.hourly_impulse import simulate_events


REQUIRED_ENTRY_COLUMNS = (
    "event_id", "fold", "decision_time", "mother_decision_time", "mother_deadline",
    "direction", "initial_stop", "signal_atr",
)
_TIME_COLUMNS = ("decision_time", "mother_decision_time", "mother_deadline")
_FIVE_MINUTES = pd.Timedelta(minutes=5)
_MOTHER_HORIZON = pd.Timedelta(hours=72)
_TRADE_FLOAT_COLUMNS = (
    "entry_price", "exit_price", "gross_return", "net_return", "net_r",
    "risk_pct", "risk_atr", "hold_minutes", "partial_fraction",
    "exit_remaining_fraction", "partial_exit_price", "realised_partial_gross_return",
    "marked_gross_return", "marked_net_return", "max_favourable_r",
    "max_adverse_r", "bars_to_first_positive", "transition_initial_side",
)
_TRADE_TIME_COLUMNS = (
    "entry_time", "exit_time", "partial_exit_time", "transition_initial_open_time",
    "transition_armed_at", "transition_first_armed_at",
    "transition_trigger_previous_open_time", "transition_trigger_open_time",
    "transition_trigger_available_at",
)
_TRADE_TEXT_COLUMNS = (
    "outcome", "transition_initial_state", "transition_initial_reason",
    "transition_last_reset_reason",
)


def _empty_trades(entries: pd.DataFrame) -> pd.DataFrame:
    """Keep a usable outcome schema without asking the study for any data."""
    result = entries.iloc[:0].copy()
    for name in REQUIRED_ENTRY_COLUMNS:
        if name not in result:
            result[name] = pd.Series(dtype=object)
    for name in _TIME_COLUMNS + _TRADE_TIME_COLUMNS:
        result[name] = pd.Series(dtype="datetime64[ns, UTC]")
    for name in _TRADE_FLOAT_COLUMNS:
        result[name] = pd.Series(dtype=float)
    for name in _TRADE_TEXT_COLUMNS:
        result[name] = pd.Series(dtype=object)
    for name in ("closed", "funding_modelled"):
        result[name] = pd.Series(dtype=bool)
    result["transition_reset_count"] = pd.Series(dtype=int)
    return result


def _frozen_policy(study: Any, policy: Mapping[str, Any]) -> dict:
    selected = {
        "exit_mode": "transition_colour", "management_minutes": 5,
        "confirmations": 1, "cost_fraction": 0.002, "max_hours": 72,
        **study.config["execution"], **policy,
    }
    if selected["exit_mode"] != "transition_colour" or selected["management_minutes"] != 5:
        raise ValueError("Realignment replay requires native 5m transition_colour")
    if isinstance(selected["confirmations"], (bool, np.bool_)) or selected["confirmations"] != 1:
        raise ValueError("Realignment replay requires one confirmation")
    if selected["cost_fraction"] != 0.002:
        raise ValueError("Realignment replay preserves the frozen 20bp cost")
    if selected["max_hours"] != 72 or "max_minutes" in selected:
        raise ValueError("The maternal 72h horizon is fixed; remaining minutes are computed per request")
    if selected.get("ma_kind", "SMA") != "SMA" or selected.get("ma_length", 40) != 40:
        raise ValueError("Realignment replay preserves native SMA40 management")
    return selected


def simulate_realign_requests(study: Any, entries: pd.DataFrame, policy: Mapping[str, Any]) -> pd.DataFrame:
    """Replay each request at mother + 0..480 integer minutes on the 5m grid.

    ``study`` exposes raw, folds, config['execution'], and featured(5,'SMA',40).
    Mother decisions must be completed hourly boundaries. Actual entry clocks
    may be hourly K2 closes or subsequent native 5m closes. The authoritative
    wait is derived from UTC timestamps, not floating wait_hours. Optional
    wait_minutes/wait_hours diagnostics are checked, never used to move clocks.
    Every fold's end_exclusive is forwarded unchanged to L3. No IDs are dropped.
    """
    selected = _frozen_policy(study, policy)
    if entries.empty:
        return _empty_trades(entries)
    missing = set(REQUIRED_ENTRY_COLUMNS) - set(entries.columns)
    if missing:
        raise ValueError("Realignment entries missing columns: {}".format(sorted(missing)))
    requests = entries.copy()
    if requests["event_id"].isna().any() or requests["event_id"].duplicated().any():
        raise ValueError("Request identities must be known and unique")
    for name in _TIME_COLUMNS:
        requests[name] = pd.to_datetime(requests[name], utc=True)
        if requests[name].isna().any():
            raise ValueError("Request clocks must be finite")
    delays = []
    for row in requests.itertuples(index=False):
        if row.mother_decision_time.value % pd.Timedelta(hours=1).value:
            raise ValueError("Mother decisions must be completed hourly boundaries")
        delta = row.decision_time - row.mother_decision_time
        if delta.value < 0 or delta.value > pd.Timedelta(hours=8).value or delta.value % _FIVE_MINUTES.value:
            raise ValueError("Entry delay must be 0..480 minutes on the exact 5m grid")
        if row.mother_deadline != row.mother_decision_time + _MOTHER_HORIZON:
            raise ValueError("Every request must preserve the mother's absolute 72h horizon")
        delays.append(int(delta.value // pd.Timedelta(minutes=1).value))
    delay_values = np.asarray(delays, dtype=int)
    for name, expected in (("wait_minutes", delay_values), ("wait_hours", delay_values / 60.0)):
        if name in requests:
            supplied = pd.to_numeric(requests[name], errors="coerce").to_numpy(dtype=float)
            if not np.isfinite(supplied).all() or not np.isclose(supplied, expected, rtol=0, atol=1e-12).all():
                raise ValueError("{} contradicts the authoritative request clocks".format(name))

    folds = {}
    for name, start, end in study.folds:
        start, end = pd.to_datetime(start, utc=True), pd.to_datetime(end, utc=True)
        if name in folds or pd.isna(start) or pd.isna(end) or start >= end:
            raise ValueError("Study folds must have unique names and valid increasing boundaries")
        folds[name] = (start, end)
    if requests["fold"].isna().any() or not set(requests["fold"]).issubset(folds):
        raise ValueError("Every request must belong to a known study fold")
    for name, (start, end) in folds.items():
        part = requests.loc[requests["fold"].eq(name)]
        if not (part["mother_decision_time"].ge(start) & part["mother_decision_time"].lt(end) & part["decision_time"].lt(end)).all():
            raise ValueError("Request clocks must lie inside their declared fold")

    management = study.featured(5, "SMA", 40)
    pieces = []
    for name, (_, end) in folds.items():
        fold_mask = requests["fold"].eq(name).to_numpy()
        for delay in np.unique(delay_values[fold_mask]):
            part = requests.loc[fold_mask & (delay_values == delay)].copy()
            remaining_minutes = 72 * 60 - int(delay)
            if not (part["decision_time"] + pd.Timedelta(minutes=remaining_minutes)).eq(part["mother_deadline"]).all():
                raise ValueError("Remaining duration drifted from the maternal deadline")
            pieces.append(simulate_events(
                study.raw, management, part,
                {**selected, "max_minutes": remaining_minutes}, end_exclusive=end,
            ))
    trades = pd.concat(pieces, ignore_index=True)
    if trades["event_id"].duplicated().any() or set(trades["event_id"]) != set(requests["event_id"]):
        raise ValueError("Every emitted request must have exactly one execution result")
    # Stable request order makes paired comparison independent of fold/group order.
    return trades.set_index("event_id").loc[requests["event_id"]].reset_index()


def realign_episode_ledger(mothers: pd.DataFrame, statuses: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Add one observed expiry without changing the historical episode helper.

    The translation occurs on a local copy, and the exact realignment status is
    restored afterwards. Duplicate/foreign/missing execution results use the
    existing fail-closed identity checks. Unknown outcomes stay NaN and occupy
    their full maternal horizon; invalid risk remains a known rejected-entry 0.
    """
    if mothers.empty and statuses.empty and trades.empty:
        mothers = mothers.copy()
        statuses = statuses.copy()
        for name in ("event_id", "fold"):
            if name not in mothers:
                mothers[name] = pd.Series(dtype=object)
        for name in ("mother_decision_time", "mother_deadline", "terminal_time"):
            if name not in statuses:
                statuses[name] = pd.Series(dtype="datetime64[ns, UTC]")
    translated = statuses.copy()
    if "status" not in translated:
        if len(translated):
            raise ValueError("Every mother needs a terminal status")
        translated["status"] = pd.Series(dtype=object)
    if "event_id" not in translated and translated.empty:
        translated["event_id"] = pd.Series(dtype=object)
    alignment_expiry = translated["status"].eq("expired_no_alignment")
    expiry_ids = set(translated.loc[alignment_expiry, "event_id"])
    translated.loc[alignment_expiry, "status"] = "expired_no_k2"
    result = episode_ledger(mothers, translated, trades)
    restore = result["event_id"].isin(expiry_ids)
    result.loc[restore, "status"] = "expired_no_alignment"
    result.loc[restore, "episode_status"] = "expired_no_alignment"
    return result
