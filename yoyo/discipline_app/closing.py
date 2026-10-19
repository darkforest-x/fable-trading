"""Size one user-requested reduction from current remaining base quantity.

OKX SWAP quantities are converted to contracts by the live adapter. Never round
up a partial reduction or silently turn an undersized request into a full close.
"""
from .policy import Rejected, dec, positive, floor_step


def close_fraction(value):
    fraction = positive(value, '平仓比例')
    if fraction > 1:
        raise Rejected('平仓比例不能超过 100%')
    return fraction


def close_quantity(remaining, instrument, fraction):
    remaining = positive(remaining, '剩余仓位')
    fraction = close_fraction(fraction)
    if fraction == 1:
        return remaining
    quantity = floor_step(remaining * fraction, positive(instrument['step']))
    minimum = positive(instrument['minimum'])
    if quantity < minimum:
        raise Rejected('本次平仓低于最小数量，请提高比例或选择 100%')
    if remaining - quantity < minimum:
        raise Rejected('平仓后余仓低于最小数量，请降低比例或选择 100%')
    return quantity
