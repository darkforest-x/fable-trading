"""Manual home tickets bind a completed NY Monday, independent of weekdays.

Only the server supplies range extrema. Decimal rounding maps lower/upper
1.618 to executable ticks; long/short targets are mirrored inside the range.
"""
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING, ROUND_HALF_UP


def bind(terms, tick, now, *, defaults=False):
    if terms.get('entry_policy') != 'monday_1618':
        return terms, {}
    r = terms.get('monday_range') or {}
    start, end = r.get('ticket_from_ms', r.get('end_ms', now+1)), r.get('ticket_until_ms', r.get('expires_ms', 0))
    if (r.get('state') != 'ready' or r.get('symbol') != terms['symbol'] or not start <= now < end
            or r.get('end_ms', now+1) > now
            or r.get('display_previous') and ('ticket_until_ms' not in r or r.get('expires_ms') != start)):
        raise ValueError('当前显示的周一区间尚未完成或已过期，请刷新后挂单')
    if terms['order_type'] != 'limit':
        raise ValueError('1.618 首页只支持在 1.618 限价挂单')
    if terms.get('range_week') and terms['range_week'] != r['week']:
        raise ValueError('周一区间已切换，请重新预览')
    h, l, step = (Decimal(str(v)) for v in (r['high'], r['low'], tick))
    if not all(v.is_finite() and v > 0 for v in (h, l, step)) or h <= l:
        raise ValueError('周一区间或最小报价单位无效')
    long = terms['side'] == 'long'
    span = h - l
    raw = l - Decimal('.618')*span if long else h + Decimal('.618')*span
    rounded = lambda v, rounding: format((v/step).to_integral_value(rounding=rounding)*step, 'f')
    price = rounded(raw, ROUND_FLOOR if long else ROUND_CEILING)
    if Decimal(price) <= 0:
        raise ValueError('该区间的 1.618 不在有效价格范围内')
    if not defaults and (not terms.get('limit_price') or Decimal(terms['limit_price']) != Decimal(price)):
        raise ValueError('入场价必须为当前区间对应方向的 1.618，请刷新后预览')
    targets = dict(limit_price=price, range_week=r['week'],
                   target_0618=rounded(l+Decimal('.618')*span if long else h-Decimal('.618')*span, ROUND_HALF_UP),
                   target_extreme=rounded(h if long else l, ROUND_HALF_UP))
    return {**terms, 'limit_price': price, 'range_week': r['week']}, targets


def check_quote(quote, side, price, now):
    """A bound paper quote cannot be repurposed as market or another side/price."""
    import json
    risk = quote.get('risk') or json.loads(quote.get('risk_json') or '{}')
    bound = risk.get('monday_bound')
    if bound and (price is None or side != bound['side']
                  or Decimal(str(price)) != Decimal(bound['limit_price'])
                  or now >= bound['expires_ms']):
        raise ValueError('1.618 挂单方向、价格或周一区间已变化，请重新预览')
