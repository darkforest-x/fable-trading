"""Opt-in fee-aware reclaim stops on the shared Nautilus execution engine.

The baseline adapter intentionally installs an exact-entry stop after reclaim.
This module keeps that behavior as the first protection, then moves the live
stop to a maker-entry/taker-exit fee-neutral price after a completed execution
bar close leaves at least one tick of room. The strategy subclass is bound to
a private copy of ``run_native``'s globals so running baseline jobs keep their
original strategy binding.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from functools import wraps
from types import FunctionType

from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.objects import Money

from yoyo.layers.l3_backtest import nautilus_independent as _native


def _decimal(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _commission_neutral_stop(entry, side, quantity, entry_commission, taker_fee, tick):
    """Return an outward-rounded fee-neutral stop using the actual entry fee.

    ``Money`` rounds commissions to the quote currency precision. After the
    algebraic price is rounded to a tick, an extra outward tick step is applied
    only if that currency rounding would leave a small nominal loss.
    """
    entry = _decimal(entry)
    quantity = _decimal(quantity)
    entry_commission = _decimal(entry_commission)
    taker_fee = _decimal(taker_fee)
    tick = _decimal(tick)
    side = int(side)
    if side not in (-1, 1) or entry <= 0 or quantity <= 0 or tick <= 0:
        raise ValueError("Fee breakeven requires positive entry, quantity and tick")
    if not 0 <= taker_fee < 1:
        raise ValueError("Taker fee must be in [0, 1)")

    # Long: (B-E)Q - C_entry - BQt = 0.
    # Short: (E-B)Q - C_entry - BQt = 0.
    raw = (entry + Decimal(side) * entry_commission / quantity) / (
        Decimal(1) - Decimal(side) * taker_fee
    )
    rounding = ROUND_CEILING if side > 0 else ROUND_FLOOR
    units = int((raw / tick).to_integral_value(rounding=rounding))

    def net_at(stop):
        exit_commission = Money(stop * quantity * taker_fee, USDT).as_decimal()
        return Decimal(side) * quantity * (stop - entry) - entry_commission - exit_commission

    stop = tick * units
    if stop <= 0:
        raise ValueError("Fee breakeven stop is not a positive price")
    # The algebraic candidate is fee-neutral before Money's currency rounding.
    # That rounding can add at most one currency quantum to the exit charge.
    # Jump enough outward ticks to cover it, then verify with Money itself.
    gain_per_tick = quantity * (Decimal(1) - Decimal(side) * taker_fee) * tick
    quantum = Decimal(1).scaleb(-USDT.precision)
    while net_at(stop) < 0:
        deficit = -net_at(stop)
        steps = int(((deficit + quantum) / gain_per_tick).to_integral_value(
            rounding=ROUND_CEILING
        ))
        steps = max(1, steps)
        units += side * steps
        stop = tick * units
        if stop <= 0:
            raise ValueError("Fee breakeven stop is not a positive price")
    return stop


class FeeReclaimStrategy(_native.IndependentCloseStrategy):
    """Apply fee breakeven only to still-open successful reclaim positions."""

    def on_order_filled(self, event):
        key = str(event.client_order_id)
        state, phase, _ = self.order_map[key]
        is_entry = phase == "entry" and self.policy.reclaim_next_bar
        if is_entry:
            # These Nautilus values are Decimal-backed Money/Quantity/Price.
            # Capture them before the shared callback converts its fill row to
            # floats, so fee computation uses the actual charged commission.
            entry_commission = event.commission.as_decimal()
            quantity = event.last_qty.as_decimal()
            entry = event.last_px.as_decimal()
        super().on_order_filled(event)
        if not is_entry:
            return

        side = int(state.source["side"])
        fee_stop = _commission_neutral_stop(
            entry=entry,
            side=side,
            quantity=quantity,
            entry_commission=entry_commission,
            taker_fee=Decimal(str(self.policy.fill_taker_fee)),
            tick=Decimal(str(self.tick)),
        )
        state.fee_be_price_exact = fee_stop
        state.source.update(
            entry_commission=float(entry_commission),
            fee_be_price=float(fee_stop),
            fee_be_effective_i=-1,
            fee_be_deferred_closes=0,
        )

    def on_bar(self, bar):
        super().on_bar(bar)
        # Reclaim policies require an explicit OHLC path. Native matching has
        # already processed this parent bar; only phase 3 is its completed
        # close. An amendment here can first execute on a later bar.
        i, phase = self.point_phases[bar.ts_event]
        if phase != 3:
            return
        close = Decimal(str(float(self.prices[i, 3])))
        tick = Decimal(str(self.tick))
        for state in list(self.active.values()):
            source = state.source
            if (state.reclaim_status != "reclaimed"
                    or int(source.get("fee_be_effective_i", -1)) >= 0
                    or i >= int(source["end_i"])
                    or state.stop_order is None
                    or state.stop_order.is_closed):
                continue

            side = int(source["side"])
            fee_stop = state.fee_be_price_exact
            room = close - fee_stop if side > 0 else fee_stop - close
            if room >= tick:
                # No other management policy is compatible with reclaim mode.
                # Keep the existing stop reason and record this ratchet in its
                # own fields so legacy be_* diagnostics retain their meaning.
                if side * (float(fee_stop) - state.stop) <= self.eps:
                    source["fee_be_deferred_closes"] += 1
                    continue
                state.stop = float(fee_stop)
                state.stop_kind = 1
                source["fee_be_effective_i"] = i
                self.modify_order(
                    state.stop_order,
                    trigger_price=self.instrument.make_price(state.stop),
                )
            else:
                source["fee_be_deferred_closes"] += 1


_run_native_globals = dict(_native.run_native.__globals__)
_run_native_globals["IndependentCloseStrategy"] = FeeReclaimStrategy
_run_native_fee_reclaim = FunctionType(
    _native.run_native.__code__,
    _run_native_globals,
    _native.run_native.__name__,
    _native.run_native.__defaults__,
    _native.run_native.__closure__,
)
_run_native_fee_reclaim.__kwdefaults__ = (
    None if _native.run_native.__kwdefaults__ is None
    else dict(_native.run_native.__kwdefaults__)
)
_run_native_fee_reclaim.__annotations__ = dict(_native.run_native.__annotations__)


@wraps(_native.run_native)
def run_native_fee_reclaim(*args, **kwargs):
    """Run a fee-aware reclaim comparison on the existing native engine.

    This opt-in entry point accepts the ``run_native`` arguments and requires
    a fee-configured limit-entry reclaim policy. The shared adapter and its
    globals remain unchanged.
    """
    policy = kwargs.get("policy", args[4] if len(args) > 4 else None)
    if policy is None or not policy.reclaim_next_bar:
        raise ValueError("Fee reclaim adapter requires reclaim_next_bar=True")
    if policy.fill_maker_fee is None or policy.fill_taker_fee is None:
        raise ValueError("Fee reclaim adapter requires actual maker and taker rates")
    if kwargs.get("entry_order_mode", "market") != "limit":
        raise ValueError("Fee reclaim adapter requires limit entries")
    rows, fills, audit = _run_native_fee_reclaim(*args, **kwargs)
    audit.update(
        fee_reclaim_breakeven=(
            "actual entry commission plus configured taker stop fee; outward tick; "
            "activate only when at least one tick behind a completed execution close"
        ),
        fee_reclaim_entry_commission="native entry fill commission",
        fee_reclaim_stop_fee="configured taker fee, rounded by native Money",
    )
    return rows, fills, audit
