"""Versioned read-only trade references; never change signal/notification identity.

Fib references retain the touched-side 1.618 entry. Read-only history can also
project the NY Monday range boundary. Both use fixed-percentage stops from the
selected entry: 1.5% for BTC/ETH and 3% for all other symbols.
"""
import math
from .cloud_signal_rules import week_key

STEP = 43_200_000
POLICY = 'touched-side-1618-entry-symbol-fixed-stop-v5'
MONDAY_POLICY = 'monday-boundary-entry-symbol-fixed-stop-v1'
RANGE_MODES = ('fib', 'monday')
BTC_ETH_STOP_PCT = 0.015
OTHER_STOP_PCT = 0.03


def normalize_range_mode(value='fib'):
    """Validate the historical reference range selected for reconstruction."""
    if value not in RANGE_MODES:
        raise ValueError('badrangemode')
    return value


def policy_for(range_mode='fib'):
    return MONDAY_POLICY if normalize_range_mode(range_mode) == 'monday' else POLICY


def stop_pct_for(symbol):
    """Return stop fraction; only exact BTC and ETH bases use 1.5%."""
    base = symbol.partition('-')[0].upper() if isinstance(symbol, str) else ''
    return BTC_ETH_STOP_PCT if base in ('BTC', 'ETH') else OTHER_STOP_PCT


def fixed_stop(symbol, side, entry):
    """Return ``(stop, stop_pct)`` using a fixed distance from reference entry.

    Long stops sit below entry and short stops above it. ``stop_pct`` is a
    fraction (0.015 or 0.03), so callers can format it as a percentage.
    """
    stop_pct = stop_pct_for(symbol)
    if (side not in (-1, 1) or not isinstance(entry, (int, float))
            or not math.isfinite(entry) or entry <= 0):
        return None, stop_pct
    stop = entry * (1 - side * stop_pct)
    return (stop if math.isfinite(stop) and stop > 0 else None), stop_pct


def stop_basis_for(symbol, range_mode='fib'):
    stop_pct = stop_pct_for(symbol)
    tier = 'BTC/ETH' if stop_pct == BTC_ETH_STOP_PCT else '其他币种'
    basis = '周一边界入场' if normalize_range_mode(range_mode) == 'monday' else '1.618 参考入场'
    return f'{tier} 以{basis}为基准固定止损 {stop_pct * 100:g}%（多单下方 / 空单上方）'


def atr_points(rows, seed=None):
    """Causal 12H ATR; resume saved recurrence and reset on a missing bar."""
    seed = seed or {}
    previous, close = seed.get('time'), seed.get('close')
    count, total, atr = seed.get('count', 0), seed.get('sum', 0.), seed.get('atr14')
    result = []
    for row in rows:
        if previous is None or row['time'] != previous + STEP:
            close, count, total, atr = None, 0, 0., None
        tr = row['high'] - row['low']
        if close is not None:
            tr = max(tr, abs(row['high']-close), abs(row['low']-close))
        count += 1
        if count <= 14:
            total += tr
            atr = total / 14 if count == 14 else None
        else:
            atr += (tr-atr) / 14
        previous, close = row['time'], row['close']
        result.append(dict(time=previous, close=close, count=count, sum=total, atr14=atr))
    return result


def reference(item, atr=None, range_mode=None):
    """Project a mode-specific boundary entry and symbol-tiered fixed stop.

    The original stored entry/stop/RR remain visible in ``original_reference``.
    Missing or mismatched range data invalidates the projected reference; the
    confirmation close is retained separately and is never used as entry.
    """
    if range_mode is None:
        range_mode = item.get('range_mode', 'fib')
    range_mode = normalize_range_mode(range_mode)
    reference_policy = policy_for(range_mode)
    original = item.get('original_reference')
    if not isinstance(original, dict):
        original = dict(entry=item.get('entry'), stop=item.get('stop'),
                        target=item.get('target'), planned_rr=item.get('planned_rr'),
                        basis='stored-reference')
    else:
        original = dict(original)
    bar = item.get('signal_bar') or {}
    side = item.get('side')
    range_data = item.get('range')
    if not isinstance(range_data, dict):
        range_data = {}
    expected_week = week_key(item)
    range_matches = (range_data.get('state') == 'ready'
                     and range_data.get('symbol') == item.get('symbol')
                     and range_data.get('week') == expected_week)
    if range_mode == 'monday':
        entry_key, target_key = ('low', 'high') if side == 1 else ('high', 'low')
    else:
        entry_key, target_key = ('lower', 'upper') if side == 1 else ('upper', 'lower')
    entry = range_data.get(entry_key) if side in (-1, 1) and range_matches else None
    target = range_data.get(target_key) if side in (-1, 1) and range_matches else None
    stop, stop_pct = fixed_stop(item.get('symbol'), side, entry)
    confirmation_close = item.get('confirmation_close', bar.get('close'))
    if confirmation_close is None:
        # Legacy rows without signal_bar stored the confirmation close as entry.
        confirmation_close = original.get('entry')
    entry_basis = ('触及侧周一边界（12H 收盘确认后展示）' if range_mode == 'monday'
                   else '触及侧 1.618（12H 收盘确认后展示）')
    values = dict(item, range_mode=range_mode, entry=entry, stop=stop, target=target,
                  confirmation_close=confirmation_close,
                  original_reference=original, reference_policy=reference_policy,
                  entry_basis=entry_basis,
                  stop_basis=stop_basis_for(item.get('symbol'), range_mode),
                  stop_pct=stop_pct, planned_rr=None, atr14=atr)
    numeric = all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0
                  for x in (entry, stop, target))
    risk = (entry-stop) * side if numeric and side in (-1, 1) else None
    reward = (target-entry) * side if numeric and side in (-1, 1) else None
    valid = (range_matches and numeric and risk is not None and reward is not None
             and math.isfinite(risk) and math.isfinite(reward) and risk > 0 and reward > 0)
    values.update(planned_rr=reward/risk if valid else None,
                  plan_state='ready' if valid else 'invalid',
                  plan_reason='' if valid else (('缺少有效的本周周一区间' if range_mode == 'monday'
                                                else '缺少有效的本周 1.618 区间') if not range_matches
                                                else '固定比例止损或目标位置无效'))
    return values
