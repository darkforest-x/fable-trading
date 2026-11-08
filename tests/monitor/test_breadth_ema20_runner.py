from threading import Event

import pytest

from yoyo.monitor import breadth_ema20_runner as runner


@pytest.mark.parametrize("delay,expected", [(2_000, True), (91_000, False)])
def test_runner_rechecks_freshness_after_fetch(tmp_path, monkeypatch, delay, expected):
    close = runner.BAR_MS * 10
    clock = [close + 1_000]
    monkeypatch.setattr(runner, "now_ms", lambda: clock[0])
    stop = Event()
    status = runner.Heartbeat(tmp_path / "forward.json")

    class Feed:
        def warmup(self, progress):
            return {"complete": True, "total": 3, "covered": 3}

        def iter_closed_events(self, start, through, position_symbols):
            assert (start, through, position_symbols) == (close - runner.BAR_MS, close, ["BTCUSDT"])
            clock[0] = through + delay
            yield {"close_ms": through, "is_realtime": True, "market_symbols_expected": 3,
                   "market_symbols_observed": 3, "source_coverage_complete": True, "bar_available": True,
                   "candidates": [{"branch": "ALT", "rule_pass": False, "data_complete": True},
                                  {"branch": "BTC_case", "rule_pass": True, "data_complete": True}]}

    class Account:
        def cursor_ms(self):
            return close - runner.BAR_MS

        def open_symbols(self):
            return ["BTCUSDT"]

        def process_closed_bar(self, event):
            assert [s["branch"] for s in event["candidates"]] == ["BTC_case"]
            assert event["is_realtime"] is expected
            assert event["is_replay"] is (not expected)
            stop.set()
            return {"processed": True}

        def snapshot(self):
            return {"start_equity": 1000}

    runner.run(Account(), Feed(), status, stop)
    assert status.state["last_close_ms"] == close
    assert status.state["phase"] == ("running" if expected else "catching_up")


def test_settlement_millisecond_offsets_and_unpublished_due_rate():
    from yoyo.monitor.breadth_ema20_funding import funding_at
    boundary = 1794124800000
    def read(url):
        if "/fundingInfo" in url:
            return [{"symbol": "ALTUSDT", "fundingIntervalHours": 4}]
        return [{"symbol": "BTCUSDT", "fundingTime": boundary + 1,
                 "fundingRate": "-0.0001", "markPrice": "75939"}]
    actual, coverage = funding_at(["BTCUSDT", "ALTUSDT"], boundary, read)
    assert len(actual) == 1 and actual[0]["time_ms"] == boundary and actual[0]["exchange_ms"] == boundary + 1
    assert coverage["BTCUSDT"]["available"]
    assert coverage["ALTUSDT"]["due"] and not coverage["ALTUSDT"]["available"]
