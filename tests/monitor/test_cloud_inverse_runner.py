"""The independent paper job must keep exits running and fail closed on stale marks."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from yoyo.monitor.cloud_inverse_account import HOUR_MS
from yoyo.monitor.cloud_inverse_runner import cycle, next_eligible_close


def test_overdue_position_suppresses_entries_and_terminates_expired_candidates():
    now = 1000 * HOUR_MS + 180_000
    feed, account = MagicMock(), MagicMock()
    feed.clock.return_value = now
    feed.health.return_value = {"clock_synchronized_ms":now, "source_db":"healthy"}
    feed.market_state = {}
    account.open_positions.return_value = [{"symbol":"TEST-USDT-SWAP", "last_mark_ms":now - HOUR_MS}]
    account.queued_boundaries.return_value = [now - HOUR_MS]
    account.queued_candidates.return_value = [{"event_id":"expired"}]
    cycle(feed, account)
    feed.advance_open_positions.assert_called_once_with(account, now)
    feed.process_queued.assert_not_called()
    account.mark_missed.assert_called_once_with("expired", "admission_unavailable_at_deadline", now_ms=now)
    assert account.snapshot.call_args.kwargs["phase"] == "degraded"


def test_ny_monday_does_not_admit_partial_utc_signal_candle():
    def utc_ms(value):
        return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()*1000)
    assert next_eligible_close(utc_ms("2026-11-09T18:00")) == utc_ms("2026-11-11T00:00")
    assert next_eligible_close(utc_ms("2026-11-11T00:00")) == utc_ms("2026-11-11T12:00")
