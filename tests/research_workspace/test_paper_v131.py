"""Forward-entry causality, V13 exit parity, and immutable cohort diagnostics."""
from copy import deepcopy
import hashlib
import json
import time

import numpy as np
import pandas as pd
import pytest

from yoyo.monitor.v130_policy import SIGNAL_KIND, SIGNAL_PROTOCOL, VERSION
from yoyo.monitor.v130_signals import replay
from yoyo.research_workspace.paper_source import MonitorSource, evaluate_trade as frozen_trade
from yoyo.research_workspace.paper_store import PaperStore
from yoyo.research_workspace.paper_v131 import evaluate_trade, instrument_snapshot
from yoyo.research_workspace.strategies import get_plugin
from test_paper_source import _raw_database, _series, _candle

STEP = 900000


def signal():
    close = int(time.time() * 1000) // STEP * STEP - STEP
    return dict(id="retest", symbol="BTC-USDT-SWAP", timeframe="15m", timeframe_min=15,
                kind=SIGNAL_KIND, protocol=SIGNAL_PROTOCOL, strategy_version=VERSION,
                side="long", direction="long", is_closed=True, is_trade=False, confirmed=True,
                source="live", confirmation="retest", v130_admitted=True,
                candidate_source="ordinary_both", entry_reference="next_open_reference_not_fill",
                source_sha256="a" * 64, anchor_close_ms=close - 3 * STEP,
                breakout_close_ms=close - 2 * STEP, retest_close_ms=close - STEP,
                bar_close_ms=close, bar_open_ms=close - STEP, detected_at_ms=close + 1000,
                wait_bars=3, price=100., reference_price=100., initial_stop=90., risk=10.,
                performance={"entry_price": 1., "exit_r": 9999})


def test_reader_uses_live_retest_contract_readonly_and_ignores_outcomes(tmp_path):
    p = get_plugin("spike-v131-retest")
    good = signal()
    assert p.accepts(good)
    for change in ({"strategy_version": "old"}, {"v130_admitted": False}, {"timeframe": "1H"}):
        assert not p.accepts(dict(good, **change))
    path = tmp_path / "monitor.sqlite3"
    db = _raw_database(path)
    db.execute("INSERT INTO markets VALUES(?,?,?)", (good["symbol"], "15m", '{"tick_size":0.01}'))
    for row in (good, dict(good, id="history", source="history"), dict(good, id="raw", confirmation="raw")):
        db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (row["id"], row["symbol"], "15m",
                   row["kind"], row["side"], row["bar_close_ms"], row["detected_at_ms"], json.dumps(row)))
    db.commit(); db.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    found = MonitorSource(tmp_path).events(p)
    assert [e["id"] for e in found] == ["retest"]
    assert found[0]["initial_stop"] == 90 and found[0]["tick"] == .01
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("short", [False, True])
@pytest.mark.parametrize("scenario", ["stop", "trail", "reverse", "reverse_gap", "stop_gap"])
def test_frozen_15m_exit_prices_and_net_r_match_v131(monkeypatch, short, scenario):
    """Use the canonical signal replay as an independent oracle for exit paths."""
    extras = {
        "stop": [(11.2, 12, 7.9, 9)],
        "trail": [(11.2, 18.2, 10.5, 18), (18, 18.1, 16.9, 17.5)],
        "reverse": [(11.2, 12, 10, 11.8), (10.5, 11, 10, 10.8)],
        "reverse_gap": [(11.2, 12, 10, 11.8), (7, 7.8, 6, 7.1)],
        "stop_gap": [(11.2, 12, 10, 11.8), (7, 7.8, 6, 7.1)],
    }[scenario]
    rows = [(9.5, 10, 9, 9.5), (10, 11, 10, 10.5), (10.5, 10.8, 9.9, 10.2),
            (10.3, 11.5, 10.1, 11.2)] + extras
    if short:
        rows = [(30-o, 30-low, 30-high, 30-c) for o, high, low, c in rows]
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                         index=pd.date_range("2026-09-25", periods=len(rows), freq="15min", tz="UTC"))
    frame["atr"] = .25
    frame.attrs["data_gap"] = np.zeros(len(rows), bool)
    sides = np.zeros(len(rows), int)
    if scenario.startswith("reverse"):
        sides[4] = 1 if short else -1
    side, stop = ("short", 22.) if short else ("long", 8.)
    stamps = [int(t.value // 1000000) for t in frame.index]
    events, _ = replay(frame, sides, [dict(bar_close_ms=stamps[0]+STEP, side=side,
                     initial_stop=stop, source_sha256="a"*64, base_asset="BTC")], tick=.1)
    oracle = events[0]["performance"]
    from yoyo.evaluation import spike_v128_recent
    monkeypatch.setattr(spike_v128_recent, "_generic_facts", lambda *args: {"frame": frame, "side": sides})
    candles = [dict(t=t, o=o, h=h, l=low, c=c, v=1) for t, (o,h,low,c) in zip(stamps, rows)]
    # Compare exits at identical entries, then test the observation timing separately.
    got = frozen_trade(candles, "15m", "BTC-USDT-SWAP", .1,
                       dict(side=side, initial_stop=stop, signal_close_ms=stamps[4], scheduled_entry_ms=stamps[4]),
                       stamps[-1]+STEP)
    assert got["status"] == "closed"
    assert got["entry_price"] == oracle["entry_price"]
    assert got["exit_price"] == pytest.approx(oracle["exit_price"])
    assert got["net_r"] == pytest.approx(oracle["exit_r"])


def intent(side="long"):
    return dict(side=side, initial_stop=90 if side == "long" else 110, signal_close_ms=379*STEP,
                observed_ms=379*STEP+1000, scheduled_entry_ms=380*STEP,
                payload={"asset_class": "crypto", "source_payload": {"performance": {"exit_r": 9999}}})


@pytest.mark.parametrize("side", ["long", "short"])
def test_delayed_fill_uses_future_open_recomputes_risk_and_signed_difference(side):
    rows = _series(count=381)
    rows[379] = _candle(379*STEP, open_=99, high=101, low=98, close=100)
    got = evaluate_trade(rows, "15m", "BTC-USDT-SWAP", .01, intent(side), 381*STEP)
    assert got["entry_price"] == 100 and got["reference_entry_price"] == 99
    assert got["initial_risk"] == 10 and got["cost_r"] == pytest.approx(.02)
    assert got["entry_adverse_bp"] == pytest.approx((1 if side == "long" else -1)*10000/99)
    assert got["entry_delay_ms"] == STEP and got["observation_delay_ms"] == 1000
    changed = intent(side); changed["payload"]["source_payload"]["performance"] = {"entry_price": 99999}
    assert evaluate_trade(rows, "15m", "BTC-USDT-SWAP", .01, changed, 381*STEP) == got


def test_observation_clock_stop_invalidation_and_no_fabricated_prices():
    rows, dec = _series(count=381), intent()
    for change in ({"scheduled_entry_ms":379*STEP}, {"observed_ms":None}):
        assert evaluate_trade(rows,"15m","BTC-USDT-SWAP",.01,dict(dec,**change),381*STEP)["status"] == "rejected"
    got = evaluate_trade(rows, "15m", "BTC-USDT-SWAP", .01, dec, 380*STEP)
    assert got["status"] == "pending" and got["entry_price"] is None
    assert evaluate_trade(rows,"1H","BTC-USDT-SWAP",.01,dec,381*STEP)["status"] == "rejected"
    bad = deepcopy(rows); bad[380] = _candle(380*STEP, open_=89, high=91, low=88, close=90)
    assert evaluate_trade(bad,"15m","BTC-USDT-SWAP",.01,dec,381*STEP)["reason"] == "invalid_initial_risk"
    missing = rows[:380] + [_candle(381*STEP)]
    assert evaluate_trade(missing,"15m","BTC-USDT-SWAP",.01,dec,382*STEP)["status"] == "censored"
    no_ref = rows[:379]+rows[380:]
    assert evaluate_trade(no_ref,"15m","BTC-USDT-SWAP",.01,dec,381*STEP)["reference_entry_price"] is None


def test_classification_is_public_snapshot_unknown_not_symbol_guess(monkeypatch):
    from yoyo.monitor.okx import OKX, MarketError
    monkeypatch.setattr(OKX,"instruments",lambda self: [
        {"instId":"BTC-USDT-SWAP","instCategory":"1"}, {"instId":"SPYX-USDT-SWAP","instCategory":"3"},
        {"instId":"UNKNOWN-USDT-SWAP","instCategory":"99"}])
    items=instrument_snapshot()["instruments"]
    assert [v["asset_class"] for v in items.values()] == ["crypto","stocks","unknown"]
    def fail(self): raise MarketError("unavailable")
    monkeypatch.setattr(OKX,"instruments",fail)
    with pytest.raises(ValueError,match="分类读取失败"): instrument_snapshot()


def test_side_asset_metrics_cover_closed_only_and_unknown_listings(tmp_path):
    store=PaperStore(tmp_path)
    snapshot={"instruments":{"BTC-USDT-SWAP":{"asset_class":"crypto"}, "STOCK-USDT-SWAP":{"asset_class":"stocks"}}}
    r=store.create({"id":"spike-v131-retest","name":"test","version":"1"},
                   {"instrument_snapshot":snapshot},"registration-123456789",at=1)
    assert store.by_request("registration-123456789")["id"]==r["id"]
    for i,(symbol,side,status,net) in enumerate([
        ("BTC-USDT-SWAP","long","closed",-1), ("STOCK-USDT-SWAP","short","closed",2),
        ("NEW-USDT-SWAP","short","open",None)]):
        e=dict(id=str(i),symbol=symbol,side=side,timeframe="15m",bar_close_ms=STEP,
               detected_at_ms=STEP+1,initial_stop=90)
        store.decide(r["id"],e,STEP+100,2*STEP)
        store.update_trade(r["id"],str(i),dict(status=status,net_r=net,entry_adverse_bp=10))
    m=store.run(r["id"])["metrics"]
    assert m["net_r"]==1 and m["closed"]==2 and m["open"]==1
    assert m["by_side"]["short"]["closed"]==1 and m["by_side"]["short"]["net_r"]==2
    assert m["by_asset_class"]["stocks"]["net_r"]==2
    assert m["by_asset_class"]["unknown"]["net_r"] is None
    assert m["entry_comparisons"]==3 and m["mean_entry_adverse_bp"]==10
