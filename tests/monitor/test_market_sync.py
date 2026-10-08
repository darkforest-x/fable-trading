"""The live sync-shock observer must compute what exp-market-sync-shock-20261008-v2 computed."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import market_sync_shock as v1
from yoyo.evaluation import market_sync_shock_v2 as v2
from yoyo.monitor import market_sync as live

HOUR = 3_600_000
START = 1_785_542_400_000  # 2026-08-01T00:00Z


def synthetic(n: int = 420, alts: int = 14, seed: int = 7):
    rng = np.random.default_rng(seed)
    index = [START + i * HOUR for i in range(n)]
    common = rng.normal(0, 0.004, n)
    common[[150, 260, 261, 330]] += [0.05, 0.04, 0.03, -0.05]  # a few market-wide shocks
    series = {}
    for k, symbol in enumerate(["BTCUSDT", "ETHUSDT"] + [f"A{i}USDT" for i in range(alts)]):
        ret = common * (1 + 0.3 * k / alts) + rng.normal(0, 0.002, n)
        opens = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
        closes = opens * (1 + ret)
        vols = rng.lognormal(3, 0.3, n) * (1 + 6 * (np.abs(common) > 0.02))
        rows = [(float(o), float(c), float(v)) for o, c, v in zip(opens, closes, vols)]
        if k == 3:
            rows[200] = None  # one missing alt bar
        series[symbol] = rows
    universe = {"2026-08": [f"A{i}USDT" for i in range(alts)]}
    return index, series, universe


def frame(rows):
    return pd.DataFrame([r if r else (math.nan, math.nan, math.nan) for r in rows], columns=["open", "close", "volume"])


def test_leader_features_match_the_study():
    index, series, _ = synthetic()
    for symbol in live.LEADERS:
        ours = pd.DataFrame(live.leader_features(series[symbol]))
        theirs = v1.causal_features(frame(series[symbol]), live.LOOKBACK)
        for col in ("ret", "z", "vr"):
            np.testing.assert_allclose(ours[col].to_numpy(float), theirs[col].to_numpy(float), rtol=1e-9, equal_nan=True)


def test_hits_and_events_match_the_study():
    index, series, universe = synthetic()
    btc, eth = live.leader_features(series["BTCUSDT"]), live.leader_features(series["ETHUSDT"])
    alt_rows = {s: r for s, r in series.items() if s not in live.LEADERS}
    up, down, _ = live.breadth(index, alt_rows, universe)
    ret = np.array([[r[1] / r[0] - 1 if r else np.nan for r in alt_rows[s]] for s in sorted(alt_rows)]).T
    valid = np.isfinite(ret).sum(axis=1)
    np.testing.assert_allclose(up, np.where(valid >= 10, (ret > 0).sum(axis=1) / valid, np.nan), equal_nan=True)
    side = np.sign([b["ret"] for b in btc])
    ref_breadth = np.where(side > 0, up, np.where(side < 0, down, np.nan))
    fb = v1.causal_features(frame(series["BTCUSDT"]), 96)
    fe = v1.causal_features(frame(series["ETHUSDT"]), 96)
    seen = 0
    for cfg in live.GRID:
        ours = live.sync_hits(btc, eth, up, down, **cfg)
        theirs = v2.sync_hits(fb, fe, ref_breadth, **cfg)
        assert ours == theirs.tolist()
        for kind in live.KINDS:
            expected = v2.events_from_hits(theirs, kind, 24)
            got = live.events_from_hits(ours, kind, 24)
            assert got == list(zip(expected.i.tolist(), expected.side.tolist()))
            seen += len(got)
    assert seen > 0


def test_outcomes_match_trade_returns():
    index, series, universe = synthetic()
    members = universe["2026-08"]
    symbols = sorted(series)
    opens = np.array([[r[0] if r else np.nan for r in series[s]] for s in symbols]).T
    closes = np.array([[r[1] if r else np.nan for r in series[s]] for s in symbols]).T
    mask = np.array([[s in members for s in symbols]])
    for i, side in ((150, 1), (330, -1)):
        for h in (1, 4, 12, 24):
            got = live.outcome(series, members, i, side, h)
            btc = v1.trade_returns(opens[:, [symbols.index("BTCUSDT")]], closes[:, [symbols.index("BTCUSDT")]], np.array([i]), h)[0, 0]
            alts = v1.trade_returns(opens, closes, np.array([i]), h, mask)[0]
            assert got["done"] and got["btc"] == pytest.approx(round((side * btc - 0.002) * 1e4, 1))
            assert got["alts"] == pytest.approx(round((side * alts - 0.002) * 1e4, 1))
    running = live.outcome(series, members, len(index) - 3, 1, 12)
    assert running["started"] and not running["done"]


def test_rank_month_uses_the_previous_month_and_skips_leaders_and_stables():
    july = 1_782_864_000_000  # 2026-07-01T00:00Z
    day = 86_400_000
    daily = {
        "AAAUSDT": [[july + d * day, 0, 0, 0, 0, 0, 0, 100.0] for d in range(31)],
        "BBBUSDT": [[july + d * day, 0, 0, 0, 0, 0, 0, 300.0] for d in range(31)],
        "USDCUSDT": [[july + d * day, 0, 0, 0, 0, 0, 0, 9e9] for d in range(31)],
        "CCCUSDT": [[july + 40 * day, 0, 0, 0, 0, 0, 0, 9e9]],  # August only: not ranked for August
    }
    assert live.rank_month(daily, "2026-08", size=2) == ["BBBUSDT", "AAAUSDT"]
    assert live.previous_month("2026-01") == "2025-12"
    assert live.month_bounds("2026-12") == (1_796_083_200_000, 1_798_761_600_000)


class FakeBinance:
    """Serves synthetic closed klines; the last one is still forming."""

    def __init__(self, index, series):
        self.index, self.series, self.requests = index, series, 0
        self.now = index[-1] + HOUR + 1

    def server_ms(self):
        return self.now

    def coins(self):
        return sorted(self.series)

    def klines(self, symbol, interval, start_ms, end_ms, limit=1000):
        self.requests += 1
        step = 30 * 60_000 if interval == "30m" else HOUR if interval == "1h" else 86_400_000
        if interval == "1d":
            return [[START - 31 * 86_400_000 + d * 86_400_000, 0, 0, 0, 0, 0, START - 30 * 86_400_000 + d * 86_400_000 - 1,
                     float(len(symbol))] for d in range(31)]
        rows = []
        for t, row in zip(self.index, self.series.get(symbol, [])):
            if interval == "30m" or row is None or not (start_ms <= t <= end_ms):
                continue
            rows.append([t, row[0], max(row), min(row[:2]), row[1], row[2], t + step - 1])
        return rows[:limit]


def test_observer_cycle_and_api(tmp_path, monkeypatch):
    index, series, _ = synthetic()
    monkeypatch.setattr(live, "BACKFILL_START_MS", START)
    monkeypatch.setattr(live, "OBSERVE_START_MS", START)
    observer = live.Observer(tmp_path, client=FakeBinance(index, series))
    snap = observer.cycle()
    assert snap["universe"]["2026-08"]["size"] == 14
    assert snap["latest"]["60"][-1]["open_ms"] == index[-1]
    assert any(e["minutes"] == 60 for e in snap["events"])
    assert observer.snapshot()["status"]["state"] == "ok"
    reopened = live.Observer(tmp_path, client=FakeBinance(index, series))
    assert reopened.snapshot()["snapshot"]["generated_ms"] == snap["generated_ms"]

    from fastapi.testclient import TestClient
    from yoyo.monitor.server import create_app

    client = TestClient(create_app(runtime=tmp_path, start_monitor=False))
    assert client.get("/api/market-sync").json()["status"]["state"] == "starting"
    meta = client.get("/api/market-sync/history").json()
    assert "60|sync|z3.0_v3.0_b0.75" in meta["group_keys"]
    rows = client.get("/api/market-sync/history", params={"group": "60|sync|z3.0_v3.0_b0.75"}).json()["rows"]
    assert sum(1 for r in rows if r[1] == 1) == 126  # v2 headline sample
    assert client.get("/api/market-sync/history", params={"group": "nope"}).status_code == 404
    ids = [r["experiment_id"] for r in client.get("/api/market-sync/iterations").json()["items"]]
    assert ids[:2] == ["exp-market-sync-shock-20261007-v1", "exp-market-sync-shock-20261008-v2"]


def test_environment_matches_the_v4_study_and_vetoes_use_frozen_edges():
    from yoyo.evaluation import market_sync_shock_v4 as v4

    rng = np.random.default_rng(3)
    t0 = START - 40 * 24 * HOUR
    n = 900 * 12
    ts = t0 + np.arange(n) * 300_000
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    raw = pd.DataFrame({"ts": ts, "open": close, "high": close, "low": close, "close": close, "volume": 1.0})
    raw = raw.loc[~((raw.ts >= t0 + 300 * HOUR) & (raw.ts < t0 + 301 * HOUR))]  # one missing hour
    env = v4.btc_hourly(raw.reset_index(drop=True))
    closes = {int(ms): float(c) for ms, c in env.close.items() if np.isfinite(c)}
    for asof in (t0 + 760 * HOUR, t0 + 850 * HOUR, t0 + 899 * HOUR):
        ours = live.environment(closes, asof)
        for key in ("trend30", "trend7", "vol30"):
            theirs = env.at[asof, key]
            assert (math.isnan(ours[key]) and np.isnan(theirs)) or ours[key] == pytest.approx(theirs, rel=1e-9)
    assert live.vetoes(60, 1, {"trend30": 0.14, "vol30": 0.001}) == ["rally"]
    assert live.vetoes(60, 1, {"trend30": 0.10, "vol30": 0.009}) == []
    assert live.vetoes(30, -1, {"trend30": 0.30, "vol30": 0.0055}) == ["high_vol"]
    assert live.vetoes(60, -1, {"trend30": math.nan, "vol30": math.nan}) == []
