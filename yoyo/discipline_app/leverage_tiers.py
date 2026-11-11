"""Size an OKX cross-margin swap entry within the published contract tiers."""
from .policy import Rejected, dec, floor_step, positive


def fit_order(client, instrument, *, side, held, quantity, entry, available,
              unit_cost, fixed_leverage=None, reviewed_margin=None):
    """Return (base quantity, leverage), maximizing quantity within the ticket.

    OKX tiers are quoted in contracts. In hedge mode the larger side sets the
    tier, including outstanding opening orders. A tier can have a fractional
    maximum leverage. Query once for this preview so all tiers share a snapshot.
    """
    family = instrument['symbol'].removesuffix('-SWAP')
    contract = positive(instrument['contract_value'])
    step = positive(instrument['step'])
    instrument_cap = positive(instrument['max_leverage'])
    rows = client.get('/api/v5/public/position-tiers', instType='SWAP',
                      tdMode='cross', instFamily=family)
    same = dec(held.get(side, 0))
    opposite = dec(held.get('short' if side == 'long' else 'long', 0))
    candidates = []
    try:
        for row in rows:
            if row.get('instFamily') != family:
                continue
            lower = dec(row['minSz']) * contract
            upper = positive(row['maxSz']) * contract
            cap = min(positive(row['maxLever']), instrument_cap)
            lever = cap if fixed_leverage is None else positive(fixed_leverage)
            if lever > cap or opposite > upper:
                continue
            affordable = available / (entry / lever + unit_cost)
            size = min(quantity, upper - same, affordable)
            if reviewed_margin is not None:
                size = min(size, reviewed_margin * lever / entry)
            size = floor_step(size, step)
            if size > 0 and lower <= max(opposite, same + size) <= upper:
                candidates.append((size, lever))
    except (KeyError, TypeError, ValueError):
        raise Rejected('OKX 仓位档位数据异常，请重新预览') from None
    if not candidates:
        raise Rejected('当前杠杆档位和可用保证金无法支持本次开仓')
    return max(candidates)
