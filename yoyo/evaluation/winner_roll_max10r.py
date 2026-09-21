"""Future-informed capacity labels for a fixed linear margin model, NOT signals.

Inputs are the complete future OHLC path between a frozen entry and its original
exit bar (exclusive). All future lows are intentionally used to size positions;
each candidate high is an oracle exit. There are no features or causal decisions.
The model inherits the preceding roll study's 40x cap, flat 5% maintenance and
0.1% each-side fees. Funding, lot sizes, risk tiers and order depth are unknown.

For fixed strictly increasing buy prices and zero funding, an earlier unit has
both greater terminal profit and greater common-future margin slack than a later
unit. Saturating the earliest feasible quantity therefore dominates replacing it
with later, more expensive units. The remaining-path minimum low determines its
survival cap. Exhaustive time enumeration plus these quantities solves ONLY this
continuous, fixed-rate, increasing-price model. Tests compare independent LPs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def validate_frame(frame: pd.DataFrame) -> np.ndarray:
    """Reject incomplete prices; the study runner separately validates clocks."""
    a = frame[['open', 'high', 'low', 'close']].to_numpy(dtype=float)
    if not len(a) or not np.isfinite(a).all() or (a <= 0).any():
        raise ValueError('OHLC must be finite, positive and nonempty')
    if ((a[:, 2] > a[:, [0, 3]].min(axis=1)) |
            (a[:, 1] < a[:, [0, 3]].max(axis=1))).any():
        raise ValueError('Invalid OHLC geometry')
    return a


def audit_schedule(frame, legs, exit_i, *, capital=100., leverage=40., fee=.001,
                   maintenance_rate=.05, liquidation_fee=.001, buffer=.01):
    """Independently replay every held low and every opening-margin admission."""
    a = validate_frame(frame)
    if not 0 <= exit_i < len(a) or not legs or legs[0]['bar_i'] != 0:
        raise ValueError('Invalid entry or exit')
    indexes = [int(x['bar_i']) for x in legs]
    prices = [float(x['price']) for x in legs]
    if len(legs) > 3 or indexes != sorted(set(indexes)) or indexes[-1] > exit_i:
        raise ValueError('Invalid leg clock')
    if any(p2 <= p1 for p1, p2 in zip(prices, prices[1:])):
        raise ValueError('Adds must increase fill prices')
    by_i = {int(x['bar_i']): x for x in legs}
    quantity, cost, fees = 0., 0., 0.
    min_mm, min_im = float('inf'), float('inf')
    for i in range(exit_i + 1):
        if i in by_i:
            leg = by_i[i]
            p, q = float(leg['price']), float(leg['quantity'])
            if not np.isfinite(q) or q <= 0 or not np.isclose(p, a[i, 0], rtol=1e-12):
                raise ValueError('Invalid fill')
            # No new money is credited on purchase. Opening fees are realized.
            quantity += q
            cost += q * p
            fees += q * p * fee
            equity = capital + quantity * p - cost - fees
            min_im = min(min_im, equity - quantity * p / leverage)
        low = a[i, 2]
        equity = capital + quantity * low - cost - fees
        requirement = quantity * low * (maintenance_rate + liquidation_fee)
        min_mm = min(min_mm, equity - requirement)
    final = capital + quantity * a[exit_i, 1] - cost - fees - quantity * a[exit_i, 1] * fee
    tolerance = 1e-8 * max(capital, abs(final), cost)
    if min_mm < buffer - tolerance or min_im < -tolerance:
        raise AssertionError(f'Margin constraint failed: mm={min_mm}, im={min_im}')
    return {'final_balance': float(final), 'min_maintenance_buffer': float(min_mm),
            'min_initial_margin_buffer': float(min_im),
            'opening_fees': float(fees), 'closing_fee': float(quantity * a[exit_i, 1] * fee)}


def optimize_path(frame, *, capital=100., leverage=40., fee=.001,
                  maintenance_rate=.05, liquidation_fee=.001, buffer=.01):
    """Exhaust every original-bar exit and increasing-price pair of add opens.

    Quantity is continuous. The output is a conditional mathematical optimum,
    not a claim of exchange order feasibility or historical mark-price solvency.
    All held lows, including the exit bar's low, constrain position size.
    """
    raw = validate_frame(frame)
    if not (capital > buffer > 0 and leverage >= 1 and 0 <= fee < 1
            and 0 <= maintenance_rate + liquidation_fee < 1):
        raise ValueError('Invalid linear margin parameters')
    # Normalize prices to avoid tiny-token conditioning. q is then p0 * base qty.
    scale = raw[0, 0]
    a = raw / scale
    op, hi, lo = a[:, 0], a[:, 1], a[:, 2]
    alpha, buy_factor, im = 1-maintenance_rate-liquidation_fee, 1+fee, 1/leverage
    effective_buffer = buffer + capital * 1e-10
    best = [None, None, None]
    scores = [-np.inf] * 3

    def cap(cash, quantity, p, remaining_low):
        im_cap = (cash + (1-im)*p*quantity) / (p*(fee+im))
        denominator = buy_factor*p-alpha*remaining_low
        with np.errstate(divide='ignore', invalid='ignore'):
            mm_cap = np.where(denominator > 0,
                (cash + alpha*remaining_low*quantity-effective_buffer)/denominator, np.inf)
        return np.maximum(0., np.minimum(im_cap, mm_cap))

    def save(arm, value, e, indexes, quantities):
        if value > scores[arm]:
            scores[arm] = float(value)
            best[arm] = (e, indexes, quantities)

    for e in range(len(a)):
        price_out = hi[e]*(1-fee)
        if price_out <= buy_factor*op[0]:
            continue
        lows = np.minimum.accumulate(lo[:e+1][::-1])[::-1]
        q0 = float(cap(capital, 0., op[0], lows[0]))
        cash0 = capital-buy_factor*op[0]*q0
        base_value = cash0+price_out*q0
        save(0, base_value, e, [0], [q0])
        if e == 0:
            continue
        ix = np.arange(1, e+1)
        p = op[ix]
        low = lows[ix]
        q1 = cap(cash0, q0, p, low)
        valid1 = (p > op[0]) & (price_out > buy_factor*p) & (q1 > 0)
        value1 = cash0+price_out*q0 + q1*(price_out-buy_factor*p)
        eligible = np.where(valid1, value1, -np.inf)
        i = int(np.argmax(eligible))
        if np.isfinite(eligible[i]):
            save(1, eligible[i], e, [0, int(ix[i])], [q0, float(q1[i])])
        if e < 2:
            continue
        cash1 = cash0-buy_factor*p*q1
        total1 = q0+q1
        q2 = cap(cash1[:, None], total1[:, None], p[None, :], low[None, :])
        valid2 = (valid1[:, None] & (ix[None, :] > ix[:, None]) &
                  (p[None, :] > p[:, None]) & (price_out > buy_factor*p[None, :]) & (q2 > 0))
        value2 = cash1[:, None] + price_out*total1[:, None] + q2*(price_out-buy_factor*p[None, :])
        eligible2 = np.where(valid2, value2, -np.inf)
        i, j = np.unravel_index(np.argmax(eligible2), eligible2.shape)
        if np.isfinite(eligible2[i, j]):
            save(2, eligible2[i, j], e, [0, int(ix[i]), int(ix[j])],
                 [q0, float(q1[i]), float(q2[i, j])])
    if best[0] is None:
        raise ValueError('No profitable exit in the permitted complete-bar horizon')
    results = {}
    for arm, name in enumerate(('none', 'one', 'two')):
        # Limits mean AT MOST this many adds; never force an inferior extra fill.
        winner = max(range(arm+1), key=lambda j: scores[j])
        e, indexes, quantities = best[winner]
        legs = [{'bar_i': i, 'price': float(raw[i, 0]), 'quantity': float(q/scale)}
                for i, q in zip(indexes, quantities)]
        audit = audit_schedule(frame, legs, e, capital=capital, leverage=leverage, fee=fee,
            maintenance_rate=maintenance_rate, liquidation_fee=liquidation_fee, buffer=buffer)
        if not np.isclose(audit['final_balance'], scores[winner], rtol=1e-10, atol=1e-8):
            raise AssertionError('Optimizer and independent ledger differ')
        results[name] = {'status': 'conditional_optimal', 'exit_i': int(e),
            'exit_price': float(raw[e, 1]), 'legs': legs, 'adds_count': len(legs)-1,
            'quantity_model': 'continuous', 'real_exchange_solvency': 'unknown', **audit}
    return results
