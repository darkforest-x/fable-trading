from __future__ import annotations

from yoyo.copier.ai.schema import IntentResult


PRICE_FIELDS = ("entry_low", "entry_high", "stop_loss", "take_profit")


def normalize_decimal_shorthand_to_mark(
    result: IntentResult,
    mark: float,
    *,
    max_deviation: float,
) -> float | None:
    """Repair shorthand prices against live mark, e.g. BTC 65.4k or DOGE .08775."""
    if mark <= 0:
        return None
    entry_prices = [price for price in (result.entry_low, result.entry_high) if price and price > 0]
    if not entry_prices:
        return None

    entry = sum(entry_prices) / len(entry_prices)
    base_deviation = _deviation(entry, mark)
    allowed_deviation = max(float(max_deviation or 0), 0.2)
    if base_deviation <= allowed_deviation:
        return None

    for factor in (1000, 100, 10, 0.1, 0.01, 0.001):
        scaled_deviation = _deviation(entry * factor, mark)
        if scaled_deviation <= allowed_deviation and scaled_deviation < base_deviation:
            _scale_prices(result, mark, factor, allowed_deviation)
            note = f"price_scale_adjusted:{factor:g}"
            result.entry_note = f"{result.entry_note}; {note}" if result.entry_note else note
            return factor
    return None


def _scale_prices(result: IntentResult, mark: float, factor: float, allowed_deviation: float) -> None:
    for field in PRICE_FIELDS:
        value = getattr(result, field)
        if not value or value <= 0:
            continue
        scaled = float(value) * factor
        if field.startswith("entry_") or _deviation(scaled, mark) < _deviation(float(value), mark):
            setattr(result, field, _clean_float(scaled))


def _deviation(value: float, mark: float) -> float:
    if mark <= 0:
        return 0.0
    return abs(float(value) - float(mark)) / float(mark)


def _clean_float(value: float) -> float:
    return float(f"{value:.12g}")
