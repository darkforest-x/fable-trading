"""Pure accounting helpers for merging same-direction scale-ins.

The helpers deliberately operate on the currently remaining quantity. Closed
quantity and lifecycle realized PnL stay in the caller's ledger history; the
returned baseline starts a new R/TP cycle for the surviving position.
"""
from copy import deepcopy
from decimal import Decimal

from .policy import Rejected, dec, positive


def _unit_cost(entry, policy):
    return positive(entry) * (2 * dec(policy.fee_rate) + dec(policy.slippage_rate))


def remaining_risk(pos, policy):
    """Cash risk of the current remaining size at its current stop.

    Distance remains signed so a stop on the profitable side never creates
    extra risk allowance. Costs are reserved using the active fee/slippage
    assumptions, and an already profitable stop contributes zero risk.
    """
    remaining = positive(pos.get('remaining'), '剩余仓位')
    entry = positive(pos.get('entry'), '入场价格')
    stop = positive(pos.get('stop'), '止损价格')
    direction = pos.get('direction')
    if direction not in (-1, 1):
        raise Rejected('持仓方向无效，暂停加仓')
    signed_distance = (entry - stop) * direction
    total = remaining * (signed_distance + _unit_cost(entry, policy))
    return max(Decimal(0), total)


def merged_position(pos, quantity, entry, entry_fee, policy):
    """Return fresh position-baseline fields after adding ``quantity``.

    Existing realized ``pnl``, identity, lifecycle timestamps and other audit
    fields are intentionally left to the caller. Old entry fees are allocated
    to the current remainder before combining them with the new fill fee.
    """
    add_quantity = positive(quantity, '加仓数量')
    add_entry = positive(entry, '加仓价格')
    add_fee = dec(entry_fee, '加仓手续费')
    if add_fee < 0:
        raise Rejected('加仓手续费不能为负')

    previous_quantity = positive(pos.get('quantity'), '原始仓位')
    previous_remaining = positive(pos.get('remaining'), '剩余仓位')
    if previous_remaining > previous_quantity:
        raise Rejected('持仓余量超过原始数量，暂停加仓')
    old_entry = positive(pos.get('entry'), '原入场价格')
    stop = positive(pos.get('stop'), '止损价格')
    leverage = positive(pos.get('leverage'), '杠杆')
    direction = pos.get('direction')
    if direction not in (-1, 1):
        raise Rejected('持仓方向无效，暂停加仓')

    total_quantity = previous_remaining + add_quantity
    average_entry = (previous_remaining * old_entry + add_quantity * add_entry) / total_quantity
    signed_distance = (average_entry - stop) * direction
    total_risk = total_quantity * (signed_distance + _unit_cost(average_entry, policy))
    if signed_distance <= 0 or total_risk <= 0:
        raise Rejected('合并后止损基线无效，暂停加仓')

    previous_fee = dec(pos.get('entry_fee', '0'), '原入场手续费')
    if previous_fee < 0:
        raise Rejected('原入场手续费无效，暂停加仓')
    allocated_previous_fee = previous_fee * previous_remaining / previous_quantity

    history = deepcopy(pos.get('baseline_history', []))
    history.append({
        key: deepcopy(pos[key])
        for key in ('quantity', 'remaining', 'entry', 'stop', 'original_stop',
                    'risk', 'margin', 'entry_fee', 'breakeven', 'stages', 'current_r')
        if key in pos
    })

    return {
        'entry': str(average_entry),
        'quantity': str(total_quantity),
        'remaining': str(total_quantity),
        'risk': str(total_risk),
        'original_stop': str(stop),
        'stop': str(stop),
        'entry_fee': str(allocated_previous_fee + add_fee),
        'margin': str(total_quantity * average_entry / leverage),
        'baseline_history': history,
        'breakeven': False,
        'stages': [],
    }
