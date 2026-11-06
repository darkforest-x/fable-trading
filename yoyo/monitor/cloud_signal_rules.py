"""Shared eligibility rules for read-only cloud reference plans."""

POSITION_POLICY = 'ema-direction-first-per-symbol-ny-week-v3'


def week_key(item):
    """Return the normalized New York Monday key for a signal's 12H open."""
    stamp = item.get('signal_open_ms')
    if isinstance(stamp, int) and not isinstance(stamp, bool):
        try:
            from .monday_fib import week_window

            return week_window(stamp)['week']
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    range_data = item.get('range')
    range_week = range_data.get('week') if isinstance(range_data, dict) else None
    value = item.get('week') or range_week
    if isinstance(value, str):
        return value
    return None


def first_eligible(items):
    """Keep the earliest eligible signal for each symbol and New York week.

    Ties use confirmation time and immutable id for deterministic projections
    of legacy databases that already contain duplicate or malformed rows.
    """
    selected = {}
    for item in items:
        symbol = item.get('symbol')
        week = week_key(item)
        if not isinstance(symbol, str) or not week:
            continue
        stamp = item.get('signal_open_ms')
        confirmed = item.get('confirmed_ms')
        rank = (stamp if isinstance(stamp, int) and not isinstance(stamp, bool) else
                confirmed if isinstance(confirmed, int) and not isinstance(confirmed, bool) else 0,
                confirmed if isinstance(confirmed, int) and not isinstance(confirmed, bool) else 0,
                str(item.get('id') or ''))
        key = (symbol, week)
        old = selected.get(key)
        if old is None or rank < old[0]:
            selected[key] = (rank, item)
    return [pair[1] for pair in sorted(selected.values(), key=lambda pair: pair[0])]
