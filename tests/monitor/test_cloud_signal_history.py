import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from yoyo.data.candle_hub import CandleHub
from yoyo.monitor.chart_periods import candles
from yoyo.monitor.cloud_signal_history import CloudSignalHistory
from yoyo.monitor.cloud_signal_plans import MONDAY_POLICY, POLICY, atr_points, reference
from yoyo.monitor.cloud_signal_rules import POSITION_POLICY
from yoyo.monitor.cloud_signals import STEP, VERSION, ema_points
from yoyo.monitor.monday_fib import week_window


SYMBOL = "EDGE-USDT-SWAP"
WEEK = "2026-08-24"


def monday_window(week=WEEK):
    stamp = int(datetime.fromisoformat(week).replace(
        tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000)
    return week_window(stamp)


class FakeClient:
    def __init__(self, hub, now, page_rows=None):
        self.hub = hub
        self.now = now
        self.page_rows = list(page_rows or [])
        self.calls = []

    def clock(self):
        return self.now

    def get(self, path, params):
        self.calls.append((path, dict(params)))
        return list(self.page_rows)


class FakeLevels:
    def __init__(self, high=48.0, low=40.0):
        self.high = high
        self.low = low
        self.calls = []

    def read(self, symbol, at):
        self.calls.append((symbol, at))
        window = week_window(at)
        width = self.high - self.low
        return dict(symbol=symbol, week=window["week"], state="ready",
                    start_ms=window["start"], end_ms=window["end"],
                    expires_ms=window["expires"], high=self.high, low=self.low,
                    lower=self.low - .618 * width, upper=self.high + .618 * width)


class FakeService:
    def __init__(self, hub, now, levels, stored=None, page_rows=None):
        self.client = FakeClient(hub, now, page_rows)
        self.levels = levels
        self.stored = list(stored or [])
        self.history_calls = 0
        self.record_calls = 0
        self.notification_calls = 0
        self.projection_calls = 0

    def history(self, symbol):
        self.history_calls += 1
        return {"symbol": symbol, "items": [
            dict(reference(item, item.get("atr14")), eligibility_policy=POSITION_POLICY)
            for item in self.stored if item.get("symbol") == symbol
        ]}

    def project(self, item, inputs=None, range_mode=None):
        self.projection_calls += 1
        atr = (inputs or {}).get(item["id"])
        return reference(item, atr, range_mode)

    def record(self, *args, **kwargs):
        self.record_calls += 1
        raise AssertionError("history reconstruction must not record signals")

    def deliver_pending(self):
        self.notification_calls += 1
        raise AssertionError("history reconstruction must not deliver notifications")


def raw_bar(t, open_, high, low, close):
    return [str(t), str(open_), str(high), str(low), str(close), "1", "1", "1", "1"]


def fixture_bars(window=None, *, strict=False, second_candidate=False, cloud_reject=False):
    window = window or monday_window()
    first = ((window["end"] + STEP - 1) // STEP) * STEP
    last = ((window["expires"] - STEP) // STEP) * STEP
    warmup_start = first - 49 * STEP
    raw = []
    for index, t in enumerate(range(warmup_start, last + 1, STEP)):
        close = 40.0 + index * .1
        open_ = close - .05
        high, low = close + .2, close - .2
        if t == first:
            # The strict variant touches the Monday lower boundary exactly.
            lower = 40.0 - .618 * 8.0
            low = lower if strict else 34.0
            open_, high, close = 44.5, 47.0, 45.0
        elif second_candidate and t == first + STEP:
            # This strict reclaim can sit entirely below the EMA cloud.
            open_, high, low, close = ((42.25, 42.5, 34.5, 42.0) if cloud_reject
                                       else (42.5, 43.0, 34.5, 42.0))
        raw.append(raw_bar(t, open_, high, low, close))
    return raw, first, last


def seed_cache(hub, rows):
    key = "|".join((hub.origin, SYMBOL, "12Hutc"))
    with hub.db() as db:
        for raw in rows:
            db.execute("INSERT OR REPLACE INTO candles VALUES (?,?,?)",
                       (key, int(raw[0]), json.dumps(raw)))


def fixture(tmp_path, *, now=None, raw_rows=None, page_rows=None, levels=None, stored=None):
    window = monday_window()
    now = window["expires"] + STEP if now is None else now
    hub = CandleHub(tmp_path)
    levels = levels or FakeLevels()
    service = FakeService(hub, now, levels, stored=stored, page_rows=page_rows)
    if raw_rows is not None:
        seed_cache(hub, raw_rows)
    return CloudSignalHistory(service), service, hub, window


def test_completed_week_reconstructs_wick_plans_without_writes(tmp_path):
    rows, first, _ = fixture_bars(second_candidate=True)
    history, service, hub, _ = fixture(tmp_path, raw_rows=rows)
    before = hub.path.stat().st_size

    result = history.read(SYMBOL, WEEK)

    assert result["state"] == "ready" and result["complete"] is True
    assert result["coverage"]["candidate_bars_considered"] == 11
    assert result["coverage"]["candidate_bars_available"] == 11
    assert [item["signal_open_ms"] for item in result["items"]] == [first]
    ready = result["items"][0]
    assert ready["id"] == f"{VERSION}:{SYMBOL}:{first}:1"
    assert ready["plan_state"] == "ready" and ready["planned_rr"] > 0
    assert all(item["historical"] and item["provenance"] == "reconstruction" for item in result["items"])
    assert all(item["eligibility_policy"] == POSITION_POLICY for item in result["items"])
    assert all(item["reference_policy"] == POLICY for item in result["items"])
    assert service.record_calls == service.notification_calls == 0
    assert service.projection_calls == 1
    assert hub.path.stat().st_size == before

    result["items"][0]["entry"] = -1
    history.read(SYMBOL, WEEK)
    assert service.history_calls == 1 and len(service.levels.calls) == 1
    assert history.read(SYMBOL, WEEK)["items"][0]["entry"] == pytest.approx(40.0 - .618 * 8.0)


def test_monday_mode_reprojects_stored_fib_signal_and_has_independent_cache_key(tmp_path):
    rows, first, _ = fixture_bars()
    fib_history, _, _, _ = fixture(tmp_path / "fib", raw_rows=rows)
    fib_item = fib_history.read(SYMBOL, WEEK)["items"][0]
    history, service, _, _ = fixture(tmp_path / "monday", raw_rows=rows, stored=[fib_item])

    fib = history.read(SYMBOL, WEEK)
    monday = history.read(SYMBOL, WEEK, range_mode="monday")

    assert fib["items"][0]["id"] == f"{VERSION}:{SYMBOL}:{first}:1"
    assert fib["range_mode"] == "fib" and fib["reference_policy"] == POLICY
    assert monday["items"][0]["id"] == f"{VERSION}:{SYMBOL}:{first}:1:monday"
    assert monday["items"][0]["signal_open_ms"] == first
    assert monday["range_mode"] == "monday" and monday["reference_policy"] == MONDAY_POLICY
    assert monday["items"][0]["entry"] == 40 and monday["items"][0]["target"] == 48
    assert monday["items"][0]["stop"] == pytest.approx(40 * .97)
    assert monday["items"][0]["original_reference"]["entry"] != fib_item["entry"]
    assert service.projection_calls == 1
    assert history.read(SYMBOL, WEEK, range_mode="monday")["items"][0]["id"] == monday["items"][0]["id"]
    assert service.projection_calls == 1


def test_history_rejects_unknown_range_mode(tmp_path):
    rows, _, _ = fixture_bars()
    history, _, _, _ = fixture(tmp_path, raw_rows=rows)

    with pytest.raises(ValueError, match="badrangemode"):
        history.read(SYMBOL, WEEK, range_mode="other")


def test_stored_signal_projection_is_authoritative_by_id_and_seeds_ema(tmp_path):
    rows, first, _ = fixture_bars()
    # Reuse a persisted ready EMA at the exact first eligible point. Its
    # deliberately invalid stored plan must survive reconstruction unchanged.
    stored = dict(
        id=f"{VERSION}:{SYMBOL}:{first}:1", version=VERSION, symbol=SYMBOL, side=1,
        timeframe="12H", signal_open_ms=first, confirmed_ms=first + STEP,
        entry=45.0, stop=46.0, target=52.0, planned_rr=None, plan_state="invalid",
        # Legacy persisted rows omitted signal_bar; entry is their confirmed close.
        week=WEEK, ema20=44.0, ema50=43.0, atr14=2.0,
        historical=True, reference_policy=POLICY, eligibility_policy=POSITION_POLICY,
    )
    history, service, _, _ = fixture(tmp_path, raw_rows=rows, stored=[stored])

    result = history.read(SYMBOL, WEEK)

    saved = next(item for item in result["items"] if item["id"] == stored["id"])
    assert saved["entry"] is None
    assert saved["confirmation_close"] == stored["entry"]
    assert saved["original_reference"]["entry"] == stored["entry"]
    assert saved["original_reference"]["stop"] == stored["stop"]
    assert saved["stop"] is None and saved["stop_pct"] == .03
    assert saved["plan_state"] == "invalid" and saved["planned_rr"] is None
    assert result["coverage"]["warmup"] == "stored_signal_ema"
    assert service.projection_calls == 0


def test_rebuilt_history_matches_legacy_stored_projection_without_rewriting_original(tmp_path):
    rows, first, _ = fixture_bars()
    rebuilt_history, _, _, _ = fixture(tmp_path / "rebuilt", raw_rows=rows)
    rebuilt = rebuilt_history.read(SYMBOL, WEEK)["items"][0]

    legacy = dict(rebuilt)
    for key in ("original_reference", "reference_policy", "entry_basis", "stop_basis",
                "stop_pct", "plan_state", "plan_reason", "atr14"):
        legacy.pop(key, None)
    old_stop = legacy["signal_bar"]["low"]
    legacy["stop"] = old_stop
    legacy["planned_rr"] = ((legacy["target"]-legacy["entry"])*legacy["side"] /
                            ((legacy["entry"]-old_stop)*legacy["side"]))
    legacy["reference_policy"] = "touched-side-1618-entry-key-wick-stop-v3"
    stored_history, stored_service, _, _ = fixture(
        tmp_path / "stored", raw_rows=rows, stored=[legacy])

    stored = stored_history.read(SYMBOL, WEEK)["items"][0]

    assert stored["id"] == rebuilt["id"]
    assert stored["signal_open_ms"] == first == rebuilt["signal_open_ms"]
    assert stored["confirmed_ms"] == rebuilt["confirmed_ms"]
    for key in ("entry", "stop", "stop_pct", "target", "planned_rr", "reference_policy"):
        if isinstance(rebuilt[key], float):
            assert stored[key] == pytest.approx(rebuilt[key])
        else:
            assert stored[key] == rebuilt[key]
    assert stored["reference_policy"] == POLICY == "touched-side-1618-entry-symbol-fixed-stop-v5"
    assert stored["original_reference"]["stop"] == old_stop
    assert stored["stop"] == pytest.approx(stored["entry"] * (1 - stored["stop_pct"]))
    assert stored_service.projection_calls == 0
    assert stored_service.record_calls == stored_service.notification_calls == 0


def test_strict_reclaim_equality_is_rejected_but_wrong_side_cloud_wick_is_kept(tmp_path):
    rows, first, _ = fixture_bars(strict=True, second_candidate=True, cloud_reject=True)
    # The first candle only touches the range boundary; the second has a strict
    # reclaim even though the whole candle remains below the EMA cloud.
    history, _, _, _ = fixture(tmp_path, raw_rows=rows)

    result = history.read(SYMBOL, WEEK)

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["signal_open_ms"] == first + STEP
    assert item["stop_pct"] == .03
    assert item["stop"] == pytest.approx(item["entry"] * .97)
    assert item["signal_bar"]["high"] < min(item["ema20"], item["ema50"])


def test_missing_candidate_history_fails_retryably_instead_of_returning_empty(tmp_path):
    rows, _, last = fixture_bars()
    rows = [raw for raw in rows if int(raw[0]) != last]
    history, service, _, _ = fixture(tmp_path, raw_rows=rows, page_rows=[])

    with pytest.raises(RuntimeError, match="history_missing"):
        history.read(SYMBOL, WEEK)

    assert len(service.client.calls) == 1
    assert service.record_calls == service.notification_calls == 0


def test_one_bounded_history_page_fills_missing_warmup_and_partial_weeks_recheck(tmp_path):
    rows, first, last = fixture_bars()
    partial_now = first + STEP
    # Cache all elapsed week candidates, but omit the 50-bar EMA warmup.
    partial_rows = [raw for raw in rows if first <= int(raw[0]) <= first]
    history, service, _, _ = fixture(tmp_path, now=partial_now, raw_rows=partial_rows,
                                     page_rows=[raw for raw in rows if int(raw[0]) <= first])

    first_result = history.read(SYMBOL, WEEK)

    assert first_result["state"] == "ready" and first_result["complete"] is False
    assert len(first_result["items"]) == 1
    assert first_result["coverage"]["source"] == "candle_hub+history_page"
    assert len(service.client.calls) == 1
    path, params = service.client.calls[0]
    assert path == "/api/v5/market/history-candles"
    assert params["bar"] == "12Hutc" and params["limit"] == "300"
    assert int(params["after"]) == first + 1

    # The partial response was not cached; once the next bar closes, refresh
    # sees the newly available row rather than returning the earlier snapshot.
    second_time = first + STEP
    second_raw = next(raw for raw in rows if int(raw[0]) == second_time)
    seed_cache(service.client.hub, [second_raw])
    service.client.now = second_time + STEP
    service.client.page_rows = [raw for raw in rows if int(raw[0]) <= second_time]
    second_result = history.read(SYMBOL, WEEK)

    assert second_result["complete"] is False
    assert len(second_result["items"]) >= len(first_result["items"])
    assert second_result["coverage"]["candidate_bars_considered"] == 2
    assert len(service.client.calls) == 2


def test_full_cached_prefix_controls_ema_and_atr_and_future_bars_do_not(tmp_path):
    rows, first, last = fixture_bars()
    original_start = int(rows[0][0])
    prefix = []
    for index, t in enumerate(range(original_start - 80 * STEP, original_start, STEP)):
        close = 30.0 + index * .12 + (index % 5) * .02
        prefix.append(raw_bar(t, close - .1, close + .45, close - .55, close))
    rows = prefix + rows
    history, _, hub, _ = fixture(tmp_path, raw_rows=rows)

    result = history.read(SYMBOL, WEEK)

    raw_map = {int(raw[0]): raw for raw in rows}
    series = candles(raw_map)
    expected_ema = {point["time"]: point for point in ema_points(series)}[first]
    expected_atr = {point["time"]: point for point in atr_points(series)}[first]
    item = next(value for value in result["items"] if value["signal_open_ms"] == first)
    assert item["ema20"] == pytest.approx(expected_ema["ema20"])
    assert item["ema50"] == pytest.approx(expected_ema["ema50"])
    assert item["atr14"] == pytest.approx(expected_atr["atr14"])
    assert result["coverage"]["warmup_start_ms"] == int(prefix[0][0])
    assert result["coverage"]["warmup_bars"] == len(series)

    # A later closed candle outside the requested week cannot alter its frozen
    # EMA/ATR projection, even after discarding the in-memory week cache.
    future = raw_bar(last + STEP, 98, 102, 90, 100)
    seed_cache(hub, [future])
    history.cache.clear()
    refreshed = history.read(SYMBOL, WEEK)
    refreshed_item = next(value for value in refreshed["items"]
                          if value["signal_open_ms"] == first)
    assert refreshed_item["ema20"] == pytest.approx(item["ema20"])
    assert refreshed_item["ema50"] == pytest.approx(item["ema50"])
    assert refreshed_item["atr14"] == pytest.approx(item["atr14"])
