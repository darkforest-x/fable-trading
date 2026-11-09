import pytest

from yoyo.monitor.cloud_inverse_account import (
    HOUR_MS,
    STEP_12H_MS,
    CloudInversePaper,
    TAKER_FEE,
)


def event(event_id="event-1", *, boundary=12 * HOUR_MS, symbol="ETH-USDT-SWAP", side=1,
          stop=90.0, detected=None):
    return {
        "id": event_id,
        "event_id": event_id,
        "symbol": symbol,
        "side": side,
        "signal_open_ms": boundary - STEP_12H_MS,
        "confirmed_ms": boundary,
        "source_detected_ms": boundary + 2_000 if detected is None else detected,
        "historical": False,
        "stop_price": stop,
    }


def open_one(account, candidate=None, *, now=None, quote_ts=None, bid=99.9, ask=100.0):
    candidate = candidate or event()
    boundary = candidate["confirmed_ms"]
    now = boundary + 6_000 if now is None else now
    quote_ts = boundary + 4_000 if quote_ts is None else quote_ts
    assert account.queue_source_event(candidate, first_observed_ms=now)
    quote = {"ts_ms": quote_ts, "bid": bid, "ask": ask}
    result = account.attempt_entry(
        candidate,
        quote,
        rankable_count=20,
        relative_30d=-0.05,
        prior_atr_pct=2.0,
        entry_order=0,
        now_ms=now,
        max_signal_delay_ms=15 * 60_000,
        max_quote_age_ms=15_000,
    )
    return result, quote


def test_empty_account_is_idempotent_and_first_slot_reserves_fee(tmp_path):
    boundary = 12 * HOUR_MS
    account = CloudInversePaper(tmp_path, clock=lambda: boundary - 10_000)
    assert account.equity() == 1_000.0
    assert account.open_positions() == []

    candidate = event(boundary=boundary)
    result, _ = open_one(account, candidate)
    assert result["status"] == "opened"
    row = account.open_positions()[0]
    assert row["initial_notional_usdt"] == pytest.approx(166.5417603464)
    assert row["entry_fee"] == pytest.approx(row["initial_notional_usdt"] * TAKER_FEE)
    assert row["entry_ms"] == boundary + 6_000
    assert row["quote_ts_ms"] == boundary + 4_000
    assert row["entry_ms"] > row["quote_ts_ms"]

    assert not account.queue_source_event(candidate, first_observed_ms=boundary + 7_000)
    restarted = CloudInversePaper(tmp_path, clock=lambda: boundary + 8_000)
    assert len(restarted.open_positions()) == 1
    assert restarted.snapshot()["account"]["start_equity"] == 1_000.0
    assert restarted.snapshot()["account"]["source_event_counts"] == {"opened": 1}


def test_public_quote_protects_first_hour_and_keeps_observed_clock(tmp_path):
    boundary = 12 * HOUR_MS
    account = CloudInversePaper(tmp_path, clock=lambda: boundary - 10_000)
    candidate = event(boundary=boundary, stop=90.0)
    open_one(account, candidate, bid=99.9, ask=100.0)

    observed = boundary + 70_000
    quote = {"ts_ms": observed - 1_000, "bid": 89.0, "ask": 89.1}
    result = account.process_public_quote(candidate["symbol"], quote, observed_ms=observed)
    assert result == {"processed": 1, "fills": 4}
    snapshot = account.snapshot()
    closed = snapshot["recent_trades"][0]
    assert closed["status"] == "closed"
    assert closed["exit_reason"] == "stop_public_quote"
    assert closed["first_protection_quote_ms"] == observed - 1_000
    assert closed["first_protection_observed_ms"] == observed
    assert closed["first_protection_latency_ms"] == observed - closed["entry_ms"]
    assert all(fill["bar_open_ms"] is None for fill in snapshot["recent_fills"] if fill["kind"] == "stop_public_quote")


def test_hourly_cap_targets_previous_close_quantity_but_fills_at_open(tmp_path):
    boundary = 12 * HOUR_MS
    account = CloudInversePaper(tmp_path, clock=lambda: boundary - 10_000)
    candidate = event(boundary=boundary, stop=50.0)
    open_one(account, candidate)
    position = account.open_positions()[0]

    # Fixture a large prior close mark to cross the cap while keeping the H1
    # open materially different. The target quantity must use 200, not 150.
    with account.db() as db:
        db.execute("UPDATE positions SET last_mark=200,last_mark_ms=? WHERE position_id=?",
                   (boundary, position["position_id"]))
    prior_nav = account.equity()
    qty_before = float(position["remaining_qty"])
    expected_remaining = min(qty_before, prior_nav * 0.20 / 200.0)
    bar_open = (position["entry_ms"] // HOUR_MS + 1) * HOUR_MS
    result = account.process_hour_bars(
        bar_open,
        {candidate["symbol"]: {"open": 150.0, "high": 151.0, "low": 60.0, "close": 120.0}},
        observed_ms=bar_open + HOUR_MS + 5_000,
    )
    assert result["fills"] == 4
    fills = [fill for fill in account.snapshot()["recent_fills"] if fill["kind"] == "hourly_cap_trim"]
    assert len(fills) == 4
    assert {fill["price"] for fill in fills} == {150.0}
    assert sum(fill["qty"] for fill in fills) == pytest.approx(qty_before - expected_remaining)
    assert account.open_positions()[0]["remaining_qty"] == pytest.approx(expected_remaining)

    # Replaying the completed H1 cannot produce a second set of fills.
    replay = account.process_hour_bars(
        bar_open,
        {candidate["symbol"]: {"open": 150.0, "high": 151.0, "low": 60.0, "close": 120.0}},
        observed_ms=bar_open + HOUR_MS + 6_000,
    )
    assert replay["fills"] == 0


def test_first_two_thirds_take_nearer_2r_before_long_double(tmp_path):
    boundary = 12 * HOUR_MS
    account = CloudInversePaper(tmp_path, clock=lambda: boundary - 10_000)
    candidate = event(boundary=boundary, stop=80.0)  # 2R=140, 2x=200
    open_one(account, candidate)
    position = account.open_positions()[0]
    bar_open = (position["entry_ms"] // HOUR_MS + 1) * HOUR_MS

    result = account.process_hour_bars(
        bar_open,
        {candidate["symbol"]: {"open": 100.0, "high": 150.0, "low": 99.0, "close": 145.0}},
        observed_ms=bar_open + HOUR_MS + 1_000,
    )
    assert result["fills"] == 2
    fills = account.snapshot()["recent_fills"]
    take_profits = [fill for fill in fills if fill["kind"].startswith("take_profit")]
    assert len(take_profits) == 2
    assert sum(fill["qty"] for fill in take_profits) == pytest.approx(position["initial_qty"] * 2 / 3)
    assert {fill["price"] for fill in take_profits} == {140.0}
    with account.db() as db:
        remaining_legs = {row["leg_no"]: row["remaining_qty"] for row in db.execute(
            "SELECT leg_no,remaining_qty FROM legs WHERE position_id=?", (position["position_id"],)
        )}
    assert remaining_legs[2] > 0 and remaining_legs[3] > 0


def test_missing_entry_quote_is_terminal_missed_and_visible(tmp_path):
    boundary = 12 * HOUR_MS
    account = CloudInversePaper(tmp_path, clock=lambda: boundary - 10_000)
    candidate = event(boundary=boundary)
    assert account.queue_source_event(candidate, first_observed_ms=boundary + 2_000)
    result = account.attempt_entry(
        candidate, None, rankable_count=20, relative_30d=-0.01, prior_atr_pct=2.0,
        entry_order=0, now_ms=boundary + 3_000,
        max_signal_delay_ms=15 * 60_000, max_quote_age_ms=15_000,
    )
    assert result["status"] == "missed"
    decision = account.snapshot()["recent_decisions"][0]
    assert decision["status"] == "missed"
    assert decision["reason"] == "missing_or_invalid_public_quote"
    assert account.open_positions() == []
