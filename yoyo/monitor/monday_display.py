"""Read-only display projection; prior touches never become current signals.

During the forming NY Monday only, read the immediately preceding week's
stored touch timestamps. Keep live prices/current states and an explicit
display week. No scanner, alert, score, or order-context mutation is allowed.
"""
from yoyo.monitor.monday_fib import week_window


def snapshot(service):
    value = service.snapshot(compact=True)
    value = dict(value, display_week=value['week'], display_previous=False)
    if value['monday_complete']:
        return value
    current = week_window(value['now_ms'])
    previous = week_window(current['start'] - 1)
    touches = {}
    for event in service.book.records(previous['week']):
        touches.setdefault(event['symbol'], {})[event['side']] = {
            key: event[key] for key in ('source', 'candle_ms', 'observed_ms')}
    return dict(value, display_week=previous['week'], display_previous=True,
                items=[dict(row, touches=touches.get(row['symbol'], {}))
                       for row in value['items']])
