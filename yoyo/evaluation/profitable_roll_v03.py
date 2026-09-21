"""Causal, long-only profitable-roll replay with explicit execution limits.

The replay reads only completed OHLC(V) bars through a confirmation close.  A
confirmation, stop update, or add request therefore takes effect at the next
bar's open.  It is a research accounting model, not an exchange simulator:
when no mark frame is supplied price bars proxy marks, and unavailable funding
is deliberately not charged.  Independent price and mark bars cannot order a
same-bar stop and maintenance failure, so such cases are censored as
``ambiguous`` instead of inventing an exchange outcome.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import pandas as pd

EPS = 1e-9


def _column(frame: pd.DataFrame, name: str) -> str:
    matches = [column for column in frame.columns if str(column).lower() == name]
    if len(matches) != 1:
        raise ValueError(f"Expected one {name!r} column")
    return matches[0]


def _validate_frame(frame: pd.DataFrame, *, name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError(f"{name} must be a non-empty pandas DataFrame")
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError(f"{name} index must be timezone-aware UTC timestamps")
    if str(frame.index.tz) not in {"UTC", "UTC+00:00"}:
        raise ValueError(f"{name} index must be UTC")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError(f"{name} index must be unique and increasing")
    columns = [_column(frame, value) for value in ("open", "high", "low", "close")]
    values = frame.loc[:, columns].astype(float)
    if not values.apply(lambda column: column.map(math.isfinite).all()).all():
        raise ValueError(f"{name} OHLC contains a non-finite price")
    opening, high, low, close = columns
    if ((values <= 0).any().any() or (values[high] < values[low]).any() or
            (values[high] < values[opening]).any() or (values[high] < values[close]).any() or
            (values[low] > values[opening]).any() or (values[low] > values[close]).any()):
        raise ValueError(f"{name} contains invalid OHLC")
    return values.rename(columns=dict(zip(columns, ("open", "high", "low", "close"))))


def _normalise_tiers(tiers: Sequence[Mapping[str, Any]]) -> list[dict[str, float]]:
    result = []
    for raw in tiers:
        maximum = raw.get("max_quantity", raw.get("maxSz"))
        leverage = raw.get("max_leverage", raw.get("maxLever"))
        if maximum is None or leverage is None or "mmr" not in raw:
            raise ValueError("Each tier needs max_quantity, mmr, and max_leverage")
        row = {"max_quantity": float(maximum), "mmr": float(raw["mmr"]), "max_leverage": float(leverage)}
        if (not all(math.isfinite(value) for value in row.values()) or row["max_quantity"] <= 0 or
                not 0 <= row["mmr"] < 1 or row["max_leverage"] <= 0):
            raise ValueError("Invalid tier values")
        result.append(row)
    if not result:
        raise ValueError("tiers must not be empty")
    return sorted(result, key=lambda row: row["max_quantity"])


def _tier(quantity: float, tiers: Sequence[Mapping[str, float]]) -> Mapping[str, float]:
    for row in tiers:
        if quantity <= row["max_quantity"] + EPS:
            return row
    raise ValueError("Position exceeds supplied tier schedule")


def _down(quantity: float, step: float) -> float:
    ratio = quantity / step
    tolerance = 4.0 * math.ulp(ratio if ratio else 1.0)
    return max(0.0, math.floor(ratio + tolerance) * step)


def _up(quantity: float, step: float) -> float:
    ratio = quantity / step
    tolerance = 4.0 * math.ulp(ratio if ratio else 1.0)
    return max(0.0, math.ceil(ratio - tolerance) * step)


def _time(value: pd.Timestamp) -> str:
    return value.isoformat()


def replay_profitable_roll(
    frame: pd.DataFrame,
    *,
    entry_i: int,
    initial_stop: float,
    tick: float,
    quantity_step: float,
    min_quantity: float,
    min_notional: float = 0.0,
    max_order_quantity: float | None = None,
    initial_quantity_requested: float,
    tiers: Sequence[Mapping[str, Any]],
    capital: float = 100.0,
    leverage: float = 40.0,
    max_adds: int | None = None,
    fee: float = 0.001,
    mark_frame: pd.DataFrame | None = None,
    funding_rates: Mapping[int, float] | None = None,
    maintenance_guard_add: bool = True,
    bar_minutes: int = 60,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replay a pure-long roll using causal OHLC structures.

    ``frame`` and optional ``mark_frame`` use UTC bar-open indices and Open,
    High, Low, Close columns (case-insensitive; a Volume column is ignored).
    Funding keys are epoch milliseconds and are booked before an add at the
    same timestamp.  ``max_adds`` is 0, a positive cap, or ``None`` for no cap.
    The function returns ``(result, events)`` and never reads market data.
    """
    price = _validate_frame(frame, name="frame")
    mark_mode = "last_price_proxy" if mark_frame is None else "independent_observed_mark"
    mark = price if mark_frame is None else _validate_frame(mark_frame, name="mark_frame")
    if not mark.index.equals(price.index):
        raise ValueError("mark_frame must have exactly the frame index")
    if not isinstance(entry_i, int) or entry_i < 0 or entry_i >= len(price):
        raise ValueError("entry_i must identify an in-range bar")
    if (tick <= 0 or quantity_step <= 0 or min_quantity <= 0 or min_notional < 0 or
            capital <= 0 or leverage <= 0 or fee < 0):
        raise ValueError("tick, quantities, capital, leverage, and fee are invalid")
    if max_order_quantity is not None and max_order_quantity <= 0:
        raise ValueError("max_order_quantity must be positive when supplied")
    if max_adds is not None and (not isinstance(max_adds, int) or max_adds < 0):
        raise ValueError("max_adds must be 0, a non-negative integer, or None")
    if not isinstance(bar_minutes, int) or bar_minutes <= 0:
        raise ValueError("bar_minutes must be a positive integer")
    if initial_stop <= 0:
        raise ValueError("initial_stop must be positive")
    tiers_norm = _normalise_tiers(tiers)
    if any(row["mmr"] + fee >= 1.0 for row in tiers_norm):
        raise ValueError("Each tier must satisfy mmr + fee < 1")
    funding = {int(key): float(value) for key, value in (funding_rates or {}).items()}
    if any(not math.isfinite(value) for value in funding.values()):
        raise ValueError("funding rates must be finite")
    funding_mode = "not_available_not_charged" if funding_rates is None else "provided_historical_settlements"

    index = price.index
    expected_gap = pd.Timedelta(minutes=bar_minutes)
    gaps = index.asi8[entry_i + 1:] - index.asi8[entry_i:-1]
    bad_gaps = (gaps != expected_gap.value).nonzero()[0]
    end_i = entry_i + int(bad_gaps[0]) if len(bad_gaps) else len(index) - 1
    truncated = end_i < len(index) - 1

    entry_price = float(price.iloc[entry_i].open)
    entry_mark = float(mark.iloc[entry_i].open)
    stop = float(initial_stop)
    if stop >= entry_price:
        raise ValueError("initial_stop must be below the initial long entry price")
    requested = _down(float(initial_quantity_requested), quantity_step)
    if requested < min_quantity - EPS:
        requested = 0.0
    initial_clip_reasons: list[str] = []
    if max_order_quantity is not None and requested > max_order_quantity + EPS:
        requested = _down(max_order_quantity, quantity_step)
        initial_clip_reasons.append("max_order_quantity")
    events: list[dict[str, Any]] = []

    def initial_safe(quantity: float) -> tuple[bool, str, float]:
        if quantity < min_quantity - EPS:
            return False, "below_min_quantity", 0.0
        if quantity * entry_price < min_notional - EPS:
            return False, "below_min_notional", 0.0
        try:
            row = _tier(quantity, tiers_norm)
        except ValueError:
            return False, "tier_schedule_limit", 0.0
        entry_fee = quantity * entry_price * fee
        margin = quantity * entry_mark / min(leverage, row["max_leverage"])
        if margin + entry_fee + fee * quantity * entry_mark > capital + EPS:
            return False, "opening_margin_or_fee", margin
        stress = stop - max(entry_price - entry_mark, 0.0) - tick
        if stress <= 0:
            return False, "nonpositive_stress_mark", margin
        equity = capital + quantity * (stress - entry_price) - entry_fee
        required = quantity * stress * (row["mmr"] + fee)
        return equity >= required - EPS, "initial_stress_maintenance", margin

    minimum_initial = _up(max(min_quantity, min_notional / entry_price), quantity_step)
    q = requested
    initial_reason = "accepted"
    if q < minimum_initial - EPS:
        q = 0.0
        initial_reason = "below_min_notional" if min_notional > q * entry_price + EPS else "below_min_quantity"
    else:
        minimum_safe, minimum_reason, _ = initial_safe(minimum_initial)
        if not minimum_safe:
            q = 0.0
            initial_reason = minimum_reason
            initial_clip_reasons.append(minimum_reason)
        else:
            low_units = int(round(minimum_initial / quantity_step))
            high_units = int(math.floor((q + EPS) / quantity_step))
            while low_units < high_units:
                middle = (low_units + high_units + 1) // 2
                safe, _, _ = initial_safe(middle * quantity_step)
                if safe:
                    low_units = middle
                else:
                    high_units = middle - 1
            q = low_units * quantity_step
            if q + EPS < requested:
                _, initial_reason, _ = initial_safe(q + quantity_step)
                initial_clip_reasons.append(initial_reason)
    if q < minimum_initial - EPS:
        result = {
            "status": "rejected_initial", "mark_mode": mark_mode, "funding_mode": funding_mode,
            "initial_quantity_requested": initial_quantity_requested, "initial_quantity": 0.0,
            "initial_rejection_reason": initial_reason, "initial_clip_reasons": initial_clip_reasons,
            "final_quantity": 0.0, "adds_count": 0, "censored": False,
            "fees_paid": 0.0, "funding_paid": 0.0, "final_balance": None, "net_profit": None,
        }
        events.append({"kind": "reject", "time_utc": _time(index[entry_i]), "reason": initial_reason,
                       "requested_quantity": initial_quantity_requested, "quantity": 0.0})
        return result, events
    initial_quantity = q
    _, _, initial_margin = initial_safe(initial_quantity)
    initial_fee = q * entry_price * fee
    cost = q * entry_price
    paid_fees = initial_fee
    paid_funding = 0.0
    adds = 0
    last_fill = entry_price
    prior_committed = q * stop - cost - paid_fees - q * stop * fee
    running_high = float(price.iloc[entry_i].high)
    pullback_low: float | None = None
    pending_add: dict[str, float] | None = None
    last_stop_update_i = entry_i

    def net(price_at_exit: float, *, quantity: float | None = None, total_cost: float | None = None,
            entry_fees: float | None = None, funding_paid: float | None = None) -> float:
        quantity = q if quantity is None else quantity
        total_cost = cost if total_cost is None else total_cost
        entry_fees = paid_fees if entry_fees is None else entry_fees
        funding_paid = paid_funding if funding_paid is None else funding_paid
        return quantity * price_at_exit - total_cost - entry_fees - quantity * price_at_exit * fee - funding_paid

    def maintenance_price(quantity: float | None = None, total_cost: float | None = None,
                          entry_fees: float | None = None, funding_paid: float | None = None) -> float:
        quantity = q if quantity is None else quantity
        total_cost = cost if total_cost is None else total_cost
        entry_fees = paid_fees if entry_fees is None else entry_fees
        funding_paid = paid_funding if funding_paid is None else funding_paid
        tier = _tier(quantity, tiers_norm)
        denominator = quantity * (1.0 - tier["mmr"] - fee)
        return (total_cost + entry_fees + funding_paid - capital) / denominator

    def common(*, event_price: float, quantity_leg: float = 0.0) -> dict[str, Any]:
        tier = _tier(q, tiers_norm)
        mark_open = float(mark.iloc[current_i].open)
        stress = stop - max(float(price.iloc[current_i].open) - mark_open, 0.0) - tick
        equity_stress = capital + q * stress - cost - paid_fees - paid_funding
        required_stress = q * stress * (tier["mmr"] + fee)
        mark_equity = capital + q * event_price - cost - paid_fees - paid_funding
        return {
            "time_utc": _time(index[current_i]), "price": event_price, "quantity": quantity_leg,
            "total_quantity": q, "notional": q * event_price, "common_stop": stop,
            "net_at_stop": net(stop), "entry_fees": paid_fees, "funding_paid": paid_funding,
            "maintenance_ratio": tier["mmr"], "maintenance_price": maintenance_price(),
            "protective_stress_mark": stress, "protective_equity": equity_stress,
            "protective_required": required_stress,
            "protective_maintenance_buffer": equity_stress - required_stress,
            "equity": mark_equity, "equity_after_exit_fee": capital + net(event_price),
        }

    def emit(kind: str, *, event_price: float, quantity_leg: float = 0.0, **extra: Any) -> None:
        events.append({"kind": kind, **common(event_price=event_price, quantity_leg=quantity_leg), **extra})

    def finish(*, status: str, reason: str, exit_i: int | None, exit_price: float | None,
               censored: bool = False, ambiguous: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        hypothetical = None if exit_price is None else capital + net(exit_price)
        if exit_i is not None:
            emit("exit", event_price=exit_price if exit_price is not None else float(price.iloc[exit_i].close),
                 quantity_leg=q, reason=reason, censored=censored, ambiguous=ambiguous)
        result = {
            "status": status, "mark_mode": mark_mode, "funding_mode": funding_mode,
            "capital": capital, "leverage": leverage, "fee_rate_one_way": fee,
            "initial_quantity_requested": initial_quantity_requested, "initial_quantity": initial_quantity,
            "initial_quantity_clip_reason": (initial_reason if initial_quantity + EPS < requested else
                                             (";".join(initial_clip_reasons) if initial_clip_reasons else "accepted")),
            "initial_clip_reasons": initial_clip_reasons,
            "initial_actual_stop_risk": initial_quantity * (entry_price - initial_stop), "entry_i": entry_i,
            "entry_time": _time(index[entry_i]), "entry_price": entry_price,
            "final_quantity": q, "adds_count": adds,
            "rejects_count": sum(event["kind"] == "reject" for event in events),
            "fees_paid": paid_fees, "funding_paid": paid_funding,
            "gross_profit": None if exit_price is None else q * exit_price - cost,
            "net_profit": hypothetical - capital if hypothetical is not None else None,
            "final_balance": None if censored or ambiguous else hypothetical,
            "counterfactual_balance": hypothetical if censored else None,
            "hypothetical_balance_at_stop": hypothetical if ambiguous else None,
            "exit_i": exit_i, "exit_price": None if censored or ambiguous else exit_price,
            "exit_reason": reason, "censored": censored, "ambiguous": ambiguous,
            "exit_requires_splitting": bool(max_order_quantity is not None and q > max_order_quantity + EPS),
        }
        result["gross"] = result["gross_profit"]
        result["net"] = result["net_profit"]
        result["finalbalance"] = result["final_balance"]
        result["counterfactual"] = result["counterfactual_balance"]
        return result, events

    current_i = entry_i
    emit("entry", event_price=entry_price, quantity_leg=q, requested_quantity=initial_quantity_requested,
         initial_margin=initial_margin, initial_risk=q * (entry_price - stop), clipped=q + EPS < requested)

    # An entry is filled at the entry bar open, therefore that completed bar can
    # still reach its protective price.  Funding at the entry timestamp is not
    # charged because the position did not exist before that settlement.
    current_i = entry_i
    entry_liquidation = maintenance_price()
    entry_stop_hit = float(price.iloc[entry_i].low) <= stop + EPS
    entry_maintenance_hit = float(mark.iloc[entry_i].low) <= entry_liquidation + EPS
    if mark_frame is None:
        if entry_stop_hit:
            if stop <= entry_liquidation + EPS:
                return finish(status="ambiguous", reason="entry_bar_maintenance_before_or_with_stop",
                              exit_i=entry_i, exit_price=stop, ambiguous=True)
            return finish(status="complete", reason="entry_bar_stop", exit_i=entry_i, exit_price=stop)
        if entry_maintenance_hit:
            return finish(status="ambiguous", reason="entry_bar_proxy_maintenance_crossing", exit_i=entry_i,
                          exit_price=None, ambiguous=True)
    elif entry_stop_hit and entry_maintenance_hit:
        return finish(status="ambiguous", reason="entry_bar_independent_mark_order_unknown", exit_i=entry_i,
                      exit_price=stop, ambiguous=True)
    elif entry_stop_hit:
        return finish(status="complete", reason="entry_bar_stop", exit_i=entry_i, exit_price=stop)
    elif entry_maintenance_hit:
        return finish(status="ambiguous", reason="entry_bar_observed_mark_maintenance_crossing", exit_i=entry_i,
                      exit_price=None, ambiguous=True)

    for current_i in range(entry_i + 1, end_i + 1):
        row = price.iloc[current_i]
        mark_row = mark.iloc[current_i]
        open_price, high, low, close = map(float, (row.open, row.high, row.low, row.close))
        mark_open, mark_low = float(mark_row.open), float(mark_row.low)
        epoch_ms = int(index[current_i].value // 1_000_000)
        if epoch_ms in funding:
            payment = q * mark_open * funding[epoch_ms]
            paid_funding += payment
            emit("funding", event_price=mark_open, rate=funding[epoch_ms], payment=payment)

        liquidation = maintenance_price()
        opening_stop = open_price <= stop + EPS
        opening_maintenance = mark_open <= liquidation + EPS
        if mark_frame is None:
            if opening_stop:
                if opening_maintenance:
                    return finish(status="ambiguous", reason="gap_stop_and_maintenance_order_unknown",
                                  exit_i=current_i, exit_price=stop, ambiguous=True)
                return finish(status="complete", reason="opening_stop", exit_i=current_i, exit_price=open_price)
            if opening_maintenance:
                return finish(status="ambiguous", reason="proxy_opening_maintenance_crossing", exit_i=current_i,
                              exit_price=None, ambiguous=True)
        else:
            if opening_stop and opening_maintenance:
                return finish(status="ambiguous", reason="independent_mark_stop_order_unknown", exit_i=current_i,
                              exit_price=stop, ambiguous=True)
            if opening_stop:
                return finish(status="complete", reason="opening_stop", exit_i=current_i, exit_price=open_price)
            if opening_maintenance:
                return finish(status="ambiguous", reason="observed_mark_maintenance_crossing", exit_i=current_i,
                              exit_price=None, ambiguous=True)

        if pending_add is not None:
            pending = pending_add
            pending_add = None
            hnet = net(stop)
            retained = max(pending["prior_committed"], 0.5 * hnet, 0.0)
            denominator = open_price - stop + fee * (open_price + stop)
            q_risk = _down(max(0.0, (hnet - retained) / denominator) if denominator > 0 else 0.0, quantity_step)
            if open_price <= last_fill + EPS:
                q_risk = 0.0

            def add_safe(delta: float) -> tuple[bool, str]:
                if delta < min_quantity - EPS:
                    return False, "below_min_quantity"
                if delta * open_price < min_notional - EPS:
                    return False, "below_min_notional"
                if max_order_quantity is not None and delta > max_order_quantity + EPS:
                    return False, "max_order_quantity"
                total = q + delta
                try:
                    tier = _tier(total, tiers_norm)
                except ValueError:
                    return False, "tier_schedule_limit"
                new_cost = cost + delta * open_price
                new_fees = paid_fees + delta * open_price * fee
                account_equity = (capital + q * mark_open - cost - paid_fees - paid_funding +
                                  delta * (mark_open - open_price) - delta * open_price * fee)
                needed_margin = total * mark_open / min(leverage, tier["max_leverage"])
                if account_equity < needed_margin + fee * total * mark_open - EPS:
                    return False, "opening_margin_or_fee"
                if maintenance_guard_add:
                    stress = stop - max(open_price - mark_open, 0.0) - tick
                    if stress <= 0:
                        return False, "nonpositive_stress_mark"
                    equity = capital + total * stress - new_cost - new_fees - paid_funding
                    required = total * stress * (tier["mmr"] + fee)
                    if equity < required - EPS:
                        return False, "protective_maintenance_guard"
                return True, "accepted"

            order_cap = q_risk if max_order_quantity is None else min(q_risk, max_order_quantity)
            minimum_delta = _up(max(min_quantity, min_notional / open_price), quantity_step)
            if order_cap < minimum_delta - EPS:
                delta, ok, reject_reason = 0.0, False, "risk_retention_or_last_fill"
            else:
                low_units = int(round(minimum_delta / quantity_step))
                high_units = int(math.floor((order_cap + EPS) / quantity_step))
                if not add_safe(low_units * quantity_step)[0]:
                    delta, ok, reject_reason = 0.0, False, add_safe(low_units * quantity_step)[1]
                else:
                    while low_units < high_units:
                        middle = (low_units + high_units + 1) // 2
                        if add_safe(middle * quantity_step)[0]:
                            low_units = middle
                        else:
                            high_units = middle - 1
                    delta = low_units * quantity_step
                    ok, reject_reason = add_safe(delta)
            if ok:
                q += delta
                cost += delta * open_price
                leg_fee = delta * open_price * fee
                paid_fees += leg_fee
                last_fill = open_price
                adds += 1
                prior_committed = max(pending["prior_committed"], net(stop))
                emit("add", event_price=open_price, quantity_leg=delta, risk_quantity_cap=q_risk,
                     retained_target=retained, opening_fee=leg_fee, prior_committed=pending["prior_committed"])
            else:
                prior_committed = max(pending["prior_committed"], net(stop))
                emit("reject", event_price=open_price, reason=reject_reason, risk_quantity_cap=q_risk,
                     retained_target=retained)

        # Only now is the full current bar known.  If a pending add filled at
        # this open, both stop and maintenance checks must use the new ledger.
        liquidation = maintenance_price()
        stop_hit = low <= stop + EPS
        maintenance_hit = mark_low <= liquidation + EPS
        if mark_frame is None:
            if stop_hit:
                if stop <= liquidation + EPS:
                    return finish(status="ambiguous", reason="intrabar_maintenance_before_or_with_stop",
                                  exit_i=current_i, exit_price=stop, ambiguous=True)
                return finish(status="complete", reason="intrabar_stop", exit_i=current_i, exit_price=stop)
            if maintenance_hit:
                return finish(status="ambiguous", reason="proxy_maintenance_crossing", exit_i=current_i,
                              exit_price=None, ambiguous=True)
        else:
            if stop_hit and maintenance_hit:
                return finish(status="ambiguous", reason="independent_mark_stop_order_unknown", exit_i=current_i,
                              exit_price=stop, ambiguous=True)
            if stop_hit:
                return finish(status="complete", reason="intrabar_stop", exit_i=current_i, exit_price=stop)
            if maintenance_hit:
                return finish(status="ambiguous", reason="observed_mark_maintenance_crossing", exit_i=current_i,
                              exit_price=None, ambiguous=True)

        old_stop = stop
        if pullback_low is None:
            if close < float(price.iloc[current_i - 1].close) - EPS:
                pullback_low = low
            else:
                running_high = max(running_high, high)
        else:
            pullback_low = min(pullback_low, low)
            if close > running_high + EPS:
                candidate = _down(pullback_low - tick, tick)
                frozen_high = running_high
                pullback_low_value = pullback_low
                pullback_low = None
                running_high = high
                if candidate > stop + EPS and candidate < close - EPS:
                    stop = candidate
                    last_stop_update_i = current_i
                    close_time = _time(index[current_i] + expected_gap)
                    emit("stop_update", event_price=close, prior_stop=old_stop, frozen_high=frozen_high,
                         pullback_low=pullback_low_value, known_at=close_time, effective_at=close_time,
                         effective_i=current_i + 1, time_utc=close_time)
                    can_add = (close >= entry_price + 2.0 * (entry_price - initial_stop) - EPS and
                               close > last_fill + EPS and (max_adds is None or adds < max_adds))
                    if can_add:
                        pending_add = {"prior_committed": prior_committed}
                    else:
                        prior_committed = max(prior_committed, net(stop))
        if stop > old_stop + EPS and pending_add is None:
            prior_committed = max(prior_committed, net(stop))

    if truncated:
        current_i = end_i
        return finish(status="censored", reason="incomplete_time_gap", exit_i=end_i,
                      exit_price=float(price.iloc[end_i].close), censored=True)
    current_i = end_i
    return finish(status="censored", reason="boundary_end_of_frame", exit_i=end_i,
                  exit_price=float(price.iloc[end_i].close), censored=True)


# Names retained for the study runner and older single-function notebooks.
replay_roll = replay_profitable_roll
replay = replay_profitable_roll
