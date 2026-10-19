"""VIDYA trend-line retest: rule semantics, live/backtest parity, API contract."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from yoyo.monitor import vidya_retest as vr
from yoyo.monitor import vidya_retest_worker as worker


def test_api_metadata_mirrors_engine_parameters():
    from yoyo.monitor.vidya_retest_api import STRATEGY_META

    assert set(STRATEGY_META) == set(vr.STRATEGIES)
    for sid, cfg in vr.STRATEGIES.items():
        p = cfg["params"]
        meta = STRATEGY_META[sid]
        assert meta["label"] == cfg["label"]
        for key in ("length", "delta_threshold", "k_atr", "rr", "exit_mode", "tol_atr"):
            assert meta[key] == getattr(p, key), (sid, key)


def _frame(rows):
    """rows: (open, high, low, close); line/up/delta/atr are injected directly."""
    a = np.array(rows, dtype=float)
    return {"o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3]}


def _run(bars, line, up, delta, params, fill="next_open"):
    n = len(bars["c"])
    ind = {"line": np.asarray(line, float), "up": np.asarray(up, bool), "delta": np.asarray(delta, float),
           "atr14": np.full(n, 1.0)}
    strat = vr.Strategy(params, cost=0.0, fill=fill)
    st = vr.State()
    out = []
    for i in range(1, n):
        out += strat.on_bar(i, bars, ind, st)
    return out, st


P = vr.Params(length=10, delta_threshold=50.0, k_atr=1.0, rr=2.0, exit_mode="fixed", tol_atr=0.0)


def test_green_touch_signals_and_fills_next_open_then_hits_target():
    bars = _frame([(105, 106, 104, 105), (104, 105, 99.5, 102), (102, 103, 101.5, 102.5), (103, 112, 102.8, 111)])
    line, up, delta = [100] * 4, [True] * 4, [60] * 4
    # bar1: low 99.5 <= 100, close 102 >= 100, but red (open 104 > close 102) -> pending; bar2 green confirms.
    events, _ = _run(bars, line, up, delta, P)
    kinds = [(e["kind"], e["index"]) for e in events]
    assert ("signal", 2) in kinds
    entry = next(e for e in events if e["kind"] == "entry")
    assert entry["index"] == 3 and entry["price"] == 103          # next bar's open
    sig = next(e for e in events if e["kind"] == "signal")
    assert sig["touch_index"] == 1 and sig["stop"] == pytest.approx(min(99.5, 101.5) - 1.0)
    exit_ = next(e for e in events if e["kind"] == "exit")
    risk = 103 - 98.5
    assert exit_["reason"] == "target" and exit_["price"] == pytest.approx(103 + 2 * risk)


def test_low_delta_is_skipped_and_previous_touch_is_not_fresh():
    bars = _frame([(105, 106, 104, 105), (100.5, 102, 99.8, 101.5), (101, 102, 99.9, 101.6)])
    events, st = _run(bars, [100] * 3, [True] * 3, [20, 20, 20], P)
    assert [e["reason"] for e in events if e["kind"] == "skip"] == ["delta"]
    assert not any(e["kind"] == "signal" for e in events)
    # bar2 also touches but bar1 was not clear of the line -> no second confirmation
    assert st.order is None and st.position is None


def test_red_touch_then_red_next_bar_cancels_pending():
    bars = _frame([(105, 106, 104, 105), (103, 104, 99.5, 101), (101, 101.5, 100.5, 100.8)])
    events, st = _run(bars, [100] * 3, [True] * 3, [70] * 3, P)
    assert not events and st.pending["long"] is None


def test_short_mirror_and_stop_exit():
    bars = _frame([(95, 96, 94, 95), (97, 100.5, 96, 97.5), (97.4, 97.6, 96.5, 96.8), (96.8, 103, 96, 102)])
    events, _ = _run(bars, [100] * 4, [False] * 4, [-60] * 4, P)
    sig = next(e for e in events if e["kind"] == "signal")
    assert sig["side"] == "short" and sig["touch_index"] == 1
    exit_ = next(e for e in events if e["kind"] == "exit")
    assert exit_["reason"] == "stop" and exit_["price"] == pytest.approx(max(100.5, 97.6) + 1.0)


def test_trend_exit_closes_on_first_bar_after_flip():
    p = vr.Params(exit_mode="trend", tol_atr=0.0, delta_threshold=50.0)
    bars = _frame([(105, 106, 104, 105), (100.5, 102, 99.8, 101.5), (101.6, 104, 101.5, 103.5), (103.5, 104, 102, 102.5)])
    events, _ = _run(bars, [100, 100, 100, np.nan], [True, True, True, False], [60] * 4, p)
    exit_ = next(e for e in events if e["kind"] == "exit")
    assert exit_["reason"] == "trend" and exit_["index"] == 3 and exit_["price"] == 102.5


def test_stale_catch_up_never_opens_a_position():
    bars = _frame([(105, 106, 104, 105), (100.5, 102, 99.8, 101.5), (101.6, 104, 101.5, 103.5)])
    ind = {"line": np.full(3, 100.0), "up": np.ones(3, bool), "delta": np.full(3, 60.0), "atr14": np.ones(3)}
    strat, st = vr.Strategy(P), vr.State()
    events = strat.on_bar(1, bars, ind, st, allow_new=False) + strat.on_bar(2, bars, ind, st, allow_new=False)
    assert [e.get("reason") for e in events if e["kind"] == "skip"] == ["stale"]
    assert st.order is None and st.position is None


def _synthetic(n=1700, seed=7):
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-0.0012, 0.0012, 0.0], size=n // 100 + 1), 100)[:n]
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.004, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(0, 0.003, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    vol = rng.uniform(100, 1000, n)
    t = (1_700_000_000_000 // worker.BAR_MS + np.arange(n)) * worker.BAR_MS
    return pd.DataFrame({"time": pd.to_datetime(t, unit="ms", utc=True), "t": t, "open": open_, "high": high,
                         "low": low, "close": close, "volume": vol})


def test_live_worker_reproduces_offline_engine_bar_for_bar(tmp_path, monkeypatch):
    params = vr.Params(length=10, delta_threshold=0.0, k_atr=1.0, rr=2.0, exit_mode="fixed", tol_atr=0.3)
    monkeypatch.setattr(vr, "STRATEGIES", {"t": {"label": "t", "params": params}})
    df = _synthetic()
    candles = [dict(t=int(r.t), o=r.open, h=r.high, l=r.low, c=r.close, v=r.volume) for r in df.itertuples()]
    book = worker.Book(tmp_path / worker.DATABASE)
    first_live = 1300
    activation = candles[first_live - 1]["t"] + worker.BAR_MS            # activation right after bar 1299 closes
    worker.step_symbol(book, "TEST-USDT-SWAP", candles[:first_live], activation + 30_000, activation)
    for j in range(first_live + 1, len(candles) + 1):                      # one new bar per pass, processed on time
        worker.step_symbol(book, "TEST-USDT-SWAP", candles[:j], candles[j - 1]["t"] + worker.BAR_MS + 60_000, activation)
    import sqlite3
    db = sqlite3.connect(str(tmp_path / worker.DATABASE))
    live = db.execute("SELECT entry_ms, exit_ms, r FROM trades WHERE source='live' ORDER BY entry_ms").fetchall()
    warm = db.execute("SELECT COUNT(*) FROM trades WHERE source='warmup'").fetchone()[0]

    # offline: same arrays, fresh state at the first live bar
    bars = {k: df[c].to_numpy(float) for k, c in (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"))}
    ind = vr.indicator(bars["o"], bars["h"], bars["l"], bars["c"], df["volume"].to_numpy(float), length=10)
    strat, st, offline = vr.Strategy(params), vr.State(), []
    for i in range(first_live, len(df)):
        for ev in strat.on_bar(i, bars, ind, st):
            if ev["kind"] == "exit":
                offline.append((int(df.t[ev["entry_index"]]), int(df.t[ev["index"]]), ev["r"]))
    assert len(offline) >= 3, "fixture must exercise several trades"
    assert [(a, b) for a, b, _ in live] == [(a, b) for a, b, _ in offline]
    assert [round(r, 9) for *_, r in live] == [round(r, 9) for *_, r in offline]
    assert warm >= 1


def test_api_routes_read_the_book(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yoyo.monitor.vidya_retest_api import install

    app = FastAPI()
    install(app, tmp_path)
    client = TestClient(app)
    assert client.get("/api/vidya-retest/overview").status_code == 503      # before the worker created the book
    book = worker.Book(tmp_path / worker.DATABASE)
    book.set_meta("status", {"status": "running", "completed": 2, "total": 2})
    state = {"pending": {"long": None, "short": None}, "order": None,
             "position": {"side": "long", "stop": 95.0, "level": 99.0, "delta": 66.0, "signal_index": 1, "touch_index": 1,
                          "entry": 100.0, "risk": 5.0, "target": 110.0, "entry_index": 2}}
    trade = dict(strategy="tl-2r", source="live", side="long", touch_ms=1, signal_ms=1, entry_ms=2, exit_ms=3,
                 entry=100.0, exit=110.0, stop=95.0, target=110.0, level=99.0, delta=66.0, risk_pct=5.0,
                 reason="target", r=1.96, r_gross=2.0, bars=1)
    market = dict(bar_ms=3, close=102.0, trend="up", line=99.0, delta=66.0, atr=1.0, dist_atr=0.5, near=True, bars=1440)
    book.commit_symbol("ETH-USDT-SWAP", {("tl-2r", "live"): (3, state)}, [], [trade], market, 10)
    data = client.get("/api/vidya-retest/overview?strategy=tl-2r&source=live").json()
    assert data["stats"]["trades"] == 1 and data["stats"]["win_rate"] == 100.0
    assert data["orders_enabled"] is False and data["notifications_enabled"] is False
    pos = data["positions"][0]
    assert pos["status"] == "open" and pos["unrealized_r"] == pytest.approx((2 - 0.002 * 101) / 5, abs=1e-3)
    assert client.get("/api/vidya-retest/trades?outcome=win").json()["total"] == 1
    assert client.get("/api/vidya-retest/market?scope=near").json()["items"][0]["symbol"] == "ETH-USDT-SWAP"
    assert client.get("/api/vidya-retest/overview?strategy=nope").status_code == 400


def test_universe_is_regular_crypto_usdt_perpetuals_only():
    assert worker.tradable({"instId": "ETH-USDT-SWAP", "instCategory": "1", "ruleType": "normal"})
    assert not worker.tradable({"instId": "ARM-USDT-SWAP", "instCategory": "3", "ruleType": "normal"})
    assert not worker.tradable({"instId": "XAU-USDT-SWAP", "instCategory": "4", "ruleType": "normal"})
    assert not worker.tradable({"instId": "NEW-USDT-SWAP", "instCategory": "1", "ruleType": "pre_market"})
    assert not worker.tradable({"instId": "BTC-USD-SWAP", "instCategory": "1", "ruleType": "normal"})


def test_prune_keeps_live_history_and_drops_outside_warmup(tmp_path):
    import sqlite3
    book = worker.Book(tmp_path / worker.DATABASE)
    trade = dict(strategy="tl-2r", side="long", touch_ms=1, signal_ms=1, entry_ms=2, exit_ms=3, entry=1.0, exit=1.1,
                 stop=0.9, target=1.2, level=1.0, delta=60.0, risk_pct=10.0, reason="target", r=0.9, r_gross=1.0, bars=1)
    market = dict(bar_ms=3, close=1.0, trend="up", line=0.9, delta=60.0, atr=0.1, dist_atr=1.0, near=False, bars=1440)
    book.commit_symbol("ARM-USDT-SWAP", {}, [], [dict(trade, source="warmup"), dict(trade, source="live", entry_ms=5)], market, 1)
    book.prune(["ETH-USDT-SWAP"])
    db = sqlite3.connect(str(tmp_path / worker.DATABASE))
    assert db.execute("SELECT source FROM trades").fetchall() == [("live",)]
    assert db.execute("SELECT COUNT(*) FROM market").fetchone()[0] == 0


def test_backtest_route_serves_the_registered_experiment(tmp_path):
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yoyo.monitor import vidya_retest_api as api

    app = FastAPI()
    api.install(app, tmp_path)
    data = TestClient(app).get("/api/vidya-retest/backtest").json()
    results = json.loads((api.EXPERIMENT_DIR / "results.json").read_text())
    assert data["experiment_id"] == results["experiment_id"] == api.EXPERIMENT_DIR.name
    assert data["production_eligible"] is False and data["registry_status"] == "inconclusive"
    for key, block in results["main"].items():
        coin, sid = key.split("|")
        rows = [t for t in data["trades"] if t["coin"] == coin and t["strategy"] == sid]
        assert len(rows) == block["summary"]["trades"], key
        assert round(sum(t["r"] for t in rows), 1) == pytest.approx(block["summary"]["total_r"], abs=0.11), key
    missing = FastAPI()
    api._backtest_cache.clear()
    original = api.EXPERIMENT_DIR
    try:
        api.EXPERIMENT_DIR = tmp_path / "nowhere"
        api.install(missing, tmp_path)
        assert TestClient(missing).get("/api/vidya-retest/backtest").status_code == 404
    finally:
        api.EXPERIMENT_DIR = original
        api._backtest_cache.clear()


def test_history_routes_serve_the_report_configs_and_candle_windows(tmp_path):
    import json
    import numpy as np
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from yoyo.monitor import vidya_retest_api as api

    app = FastAPI()
    api.install(app, tmp_path)
    client = TestClient(app)
    data = client.get("/api/vidya-retest/history").json()
    published = {"A": (653, -0.36), "H": (53, 0.38), "I": (66, 0.62)}
    by_key = {c["config"]: c for c in data["configs"]}
    assert [c["config"] for c in data["configs"]] == list("ABCDEFGHI")
    for key, (n, avg) in published.items():
        assert (by_key[key]["trades"], by_key[key]["avg_R"]) == (n, avg)
        assert sum(1 for t in data["trades"] if t["config"] == key) == n
    assert data["trendline"]["experiment_id"] == api.EXPERIMENT_DIR.name

    # candle windows: synthetic cache, bucket merge keeps extremes and the last trend value
    bar = 900_000
    t = np.arange(100, dtype=np.int64) * bar
    o = np.arange(100, dtype=float)
    cache = dict(t=t, o=o, h=o + 2, l=o - 1, c=o + 1, line10=o - 5, up10=np.ones(100, bool), delta10=np.full(100, 55, np.float32),
                 line14=o - 6, up14=np.zeros(100, bool), delta14=np.full(100, -10, np.float32))
    np.savez(tmp_path / "candles_ETH_15m.npz", **cache)
    win = api.candle_window("15m", 10, 10 * bar, 30 * bar, 50, directory=tmp_path)
    assert win["bucket"] == 1 and len(win["rows"]) == 20 and win["rows"][0][:5] == [10 * bar, 10.0, 12.0, 9.0, 11.0]
    merged = api.candle_window("15m", 10, 0, 100 * bar, 50, directory=tmp_path)
    assert merged["bucket"] == 2 and merged["rows"][1] == [2 * bar, 2.0, 5.0, 1.0, 4.0, -2.0, True, 55.0]
    assert client.get("/api/vidya-retest/history/candles?tf=4h&start_ms=0&end_ms=1").status_code == 400
