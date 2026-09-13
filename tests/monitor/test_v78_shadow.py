"""Forward V7/V8 shadow tests; all candles and primary state are local."""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from yoyo.monitor import TIMEFRAMES
from yoyo.monitor.store import Store
from yoyo.monitor import v78_shadow
from yoyo.monitor.server import create_app
from yoyo.monitor.v78_shadow_manage import configuration


def _candles(timeframe: str, count: int = 734) -> list[dict]:
    step = TIMEFRAMES[timeframe]
    rows = []
    for i in range(count):
        close = 100 + np.sin(i / 11) * .5
        rows.append({"t": i * step, "o": close - .05, "h": close + .2,
                     "l": close - .2, "c": close, "v": 1000 + i})
    return rows


def _diagnostic(timeframe: str, *, v7: bool = True, v8: bool = True) -> pd.DataFrame:
    step = TIMEFRAMES[timeframe]
    index = pd.to_datetime([0], unit="ms", utc=True)
    values = {
        "open": [100.], "high": [102.], "low": [99.], "close": [101.], "volume": [1000.],
        "atr": [1.], "ropeHigh": [100.], "ropeLow": [98.], "md": [1.], "sb": [.5],
        "pastWidth": [1.], "pastCrosses": [3.], "bar_open_ms": [0], "bar_close_ms": [step],
        "v6_side": [1], "v7": [v7], "v8": [v8], "rope_distance_atr": [1.],
        "v8_reason": ["within_3atr" if v8 else "overheated_gt_3atr"], "bb_ready": [True],
        "ma_ready": [True],
        "prior_squeeze_run3": [True], "bb_width": [.1], "bb_width_p10_prior500": [.2],
        "up": [True], "down": [False], "body_above_six": [True], "body_below_six": [False],
        "input_prefix_sha256": ["a" * 64],
    }
    return pd.DataFrame(values, index=index)


def test_checkpoint_analysis_is_prefix_causal_and_reaches_v7_readiness():
    shorter = _candles("1H")
    longer = shorter + [{"t": len(shorter) * TIMEFRAMES["1H"], "o": 99., "h": 103.,
                          "l": 98., "c": 102., "v": 9999.}]
    left = v78_shadow.analyze_checkpoint(shorter, "1H", tick=.01)
    right = v78_shadow.analyze_checkpoint(longer, "1H", tick=.01).iloc[:-1]
    columns = ["md", "sb", "ropeHigh", "ropeLow", "bb_width", "bb_width_p10_prior500",
               "bb_ready", "prior_squeeze_run3", "v6_side", "v7", "v8", "input_prefix_sha256"]
    pd.testing.assert_frame_equal(left[columns], right[columns])
    assert left.bb_ready.iloc[-1]


def test_shadow_store_has_no_notification_or_execution_tables(tmp_path):
    store = v78_shadow.ShadowStore(tmp_path / "shadow.sqlite3")
    event = dict(protocol=v78_shadow.PROTOCOL, symbol="TEST-USDT-SWAP", timeframe="1H", side=1,
                 bar_open_ms=0, bar_close_ms=TIMEFRAMES["1H"], detected_ms=10,
                 v8_admitted=True, rope_distance_atr=1.2, input_prefix_sha256="a" * 64,
                 config_sha256="b" * 64)
    event_id, inserted = store.insert_event(event)
    assert inserted and len(event_id) == 24
    assert store.insert_event(event) == (event_id, False)
    with sqlite3.connect(store.path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not ({"outbox", "bark_outbox", "orders", "model_candidates"} & tables)


def test_scanner_starts_at_activation_and_never_backfills_old_signal(tmp_path, monkeypatch):
    primary = Store(tmp_path / v78_shadow.PRIMARY_DATABASE)
    primary.save_candle_checkpoint("TEST-USDT-SWAP", "1H", _candles("1H", 1))
    primary.upsert_market({"symbol": "TEST-USDT-SWAP", "timeframe": "1H", "tick_size": ".01"})
    monkeypatch.setattr(v78_shadow, "analyze_checkpoint", lambda *args, **kwargs: _diagnostic("1H"))

    scanner = v78_shadow.V78ShadowScanner(tmp_path, clock=lambda: 10_000_000)
    receipt = scanner.run_once()
    assert receipt["processed"] == 1
    assert scanner.shadow.list_events() == []
    assert scanner.shadow.status()["market_snapshots"] == 0
    activation = scanner.shadow.get_meta("activation")
    assert activation["activated_ms"] == 10_000_000


def test_activation_clock_itself_is_not_a_forward_event(tmp_path, monkeypatch):
    primary = Store(tmp_path / v78_shadow.PRIMARY_DATABASE)
    primary.save_candle_checkpoint("TEST-USDT-SWAP", "1H", _candles("1H", 1))
    primary.upsert_market({"symbol": "TEST-USDT-SWAP", "timeframe": "1H", "tick_size": ".01"})
    monkeypatch.setattr(v78_shadow, "analyze_checkpoint", lambda *args, **kwargs: _diagnostic("1H"))

    scanner = v78_shadow.V78ShadowScanner(tmp_path, clock=lambda: TIMEFRAMES["1H"])
    receipt = scanner.run_once()
    assert receipt["events_inserted"] == 0
    assert scanner.shadow.list_events() == []
    assert scanner.shadow.status()["market_snapshots"] == 0


def test_first_forward_event_does_not_write_pre_activation_context(tmp_path, monkeypatch):
    step = TIMEFRAMES["1H"]
    primary = Store(tmp_path / v78_shadow.PRIMARY_DATABASE)
    primary.save_candle_checkpoint("TEST-USDT-SWAP", "1H", _candles("1H", 2))
    primary.upsert_market({"symbol": "TEST-USDT-SWAP", "timeframe": "1H", "tick_size": ".01"})
    before = _diagnostic("1H", v7=False, v8=False)
    after = _diagnostic("1H").copy()
    after.index = pd.to_datetime([step], unit="ms", utc=True)
    after["bar_open_ms"] = step
    after["bar_close_ms"] = 2 * step
    after["input_prefix_sha256"] = "b" * 64
    diagnostic = pd.concat([before, after])
    monkeypatch.setattr(v78_shadow, "analyze_checkpoint", lambda *args, **kwargs: diagnostic)

    scanner = v78_shadow.V78ShadowScanner(tmp_path, clock=lambda: step)
    receipt = scanner.run_once()
    assert receipt["events_inserted"] == 1
    with sqlite3.connect(scanner.shadow.path) as database:
        closes = [row[0] for row in database.execute("SELECT bar_close_ms FROM shadow_bars")]
    assert closes == [2 * step]
    assert all(close_ms > step for close_ms in closes)


def test_scanner_inserts_fresh_event_once_and_captures_path_without_delivery(tmp_path, monkeypatch):
    primary = Store(tmp_path / v78_shadow.PRIMARY_DATABASE)
    primary.save_candle_checkpoint("TEST-USDT-SWAP", "1H", _candles("1H", 1))
    primary.upsert_market({"symbol": "TEST-USDT-SWAP", "timeframe": "1H", "tick_size": ".01"})
    monkeypatch.setattr(v78_shadow, "analyze_checkpoint", lambda *args, **kwargs: _diagnostic("1H"))

    scanner = v78_shadow.V78ShadowScanner(tmp_path, clock=lambda: 1)
    first, second = scanner.run_once(), scanner.run_once()
    assert first["events_inserted"] == 1 and second["events_inserted"] == 0
    assert second["unchanged"] == 1
    event = scanner.shadow.list_events()[0]
    assert event["notification_eligible"] is False
    assert event["execution_eligible"] is False
    assert event["causal_cutoff_ms"] == TIMEFRAMES["1H"]
    status = scanner.shadow.status()
    assert status["events"] == status["v8_admitted"] == status["path_bars"] == 1


def test_existing_protocol_fails_closed_when_contract_hash_changes(tmp_path):
    scanner = v78_shadow.V78ShadowScanner(tmp_path, clock=lambda: 1)
    scanner._activation()
    scanner.fixed = dict(scanner.fixed, config_sha256="f" * 64)
    with pytest.raises(RuntimeError, match="shadow_contract_changed"):
        scanner.run_once()


def test_shadow_api_is_empty_before_start_and_read_only_after_start(tmp_path):
    client = TestClient(create_app(runtime=tmp_path, start_monitor=False))
    assert client.get("/api/shadow/status").json()["configured"] is False
    store = v78_shadow.ShadowStore(tmp_path / v78_shadow.SHADOW_DATABASE)
    event = dict(protocol=v78_shadow.PROTOCOL, symbol="TEST-USDT-SWAP", timeframe="1H", side=-1,
                 bar_open_ms=0, bar_close_ms=TIMEFRAMES["1H"], detected_ms=10,
                 v8_admitted=False, rope_distance_atr=3.2, input_prefix_sha256="a" * 64,
                 config_sha256="b" * 64)
    store.insert_event(event)
    response = client.get("/api/shadow/events?timeframe=1H&side=-1")
    assert response.status_code == 200
    assert response.json()["items"][0]["symbol"] == "TEST-USDT-SWAP"
    assert response.json()["notification_eligible"] is False
    assert client.get("/api/shadow/events?side=0").status_code == 400


def test_market_snapshot_uses_closed_previous_bar_without_claiming_four_hour_as_sixty_minutes(tmp_path):
    store = v78_shadow.ShadowStore(tmp_path / "shadow.sqlite3")
    step = TIMEFRAMES["4H"]
    for index, above in enumerate((False, True)):
        close_ms = (index + 1) * step
        for symbol in ("A-USDT-SWAP", "B-USDT-SWAP"):
            store.upsert_cell(symbol, "4H", index + 1, "ready", {
                "bar_open_ms": close_ms - step, "bar_close_ms": close_ms,
                "ma_ready": True, "up": above, "down": not above,
                "body_above_six": above, "body_below_six": not above,
                "v7": above, "v8": above,
            })
        snapshot = store.record_market_snapshot("4H", expected=2, after_ms=0)
    assert snapshot is not None
    assert snapshot["joint_up_delta_60m"] is None
    assert snapshot["joint_up_delta_previous_bar"] == pytest.approx(1.0)


def test_shadow_launch_job_is_background_and_does_not_keep_mac_awake():
    config = configuration()
    assert config["ProcessType"] == "Background"
    assert config["ProgramArguments"][:3] == ["/usr/bin/nice", "-n", "10"]
    assert "caffeinate" not in config["ProgramArguments"]
    assert v78_shadow.POLL_SECONDS >= 300
