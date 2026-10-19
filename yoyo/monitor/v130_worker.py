"""Observe V13.1 from the existing scanner prefix of each period, without another fetch loop."""
from yoyo.monitor.v130_signals import from_parent, SIGNAL_PROTOCOL
from yoyo.monitor.v130_policy import arm_v130
from yoyo.monitor.notification_policy import delivery_error
from yoyo.contracts.signal_universe import exclusion_reason, VERSION as UNIVERSE_VERSION


def observe(store, parent_result, context, *, symbol, tick, clock, timeframe='15m', allow_new=True):
    now = int(clock())
    arm_v130(store, now)
    result = from_parent(parent_result, context, tick=tick, timeframe=timeframe)
    # Each protocol owns its forward boundary. No historical recomputation is
    # journaled or pushed at startup, even when it is within 30 minutes.
    since = store.get_meta('notification_policy:spike-burst-v130-retest-notifications-v1', {}).get('activated_ms')
    events = []
    for original in result['events']:
        if type(since) is not int or original['bar_close_ms'] <= since:
            continue
        event = dict(original, symbol=symbol, venue='okx', detected_at_ms=int(clock()))
        admitted = allow_new and exclusion_reason(symbol, timeframe=timeframe, side=event['side']) is None
        if not admitted and not store.has_event(event):
            continue
        event_now = int(clock())
        inserted = store.upsert_event(event,
            notify=admitted and delivery_error(store, event, event_now, 'telegram') is None,
            bark_notify=admitted and delivery_error(store, event, event_now, 'bark') is None)
        if not inserted:
            store.update_event_payload(store.event_id(event), {'performance': event['performance']})
        events.append(event)
    result['events'] = events
    result['state'].update(symbol=symbol, last_observed_ms=int(clock()), entry_universe_version=UNIVERSE_VERSION)
    store.set_meta('v130:last_observation', result['state'])
    return result
