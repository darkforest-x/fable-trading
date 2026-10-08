"""Exit rules of exp-market-sync-shock-20261008-v5."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import market_sync_shock_v2 as v2
from yoyo.evaluation import market_sync_shock_v5 as v5


def test_config_rules_match_the_builder():
    cfg = json.loads(v5.CONFIG.read_text())
    assert tuple(cfg["exits"]) == v5.EXITS
    assert cfg["baseline"] == "hold_12h"
    assert cfg["round_trip_cost"] == 0.002


@pytest.mark.parametrize("side", [1, -1])
def test_trail3_is_v2_trend_exit(side):
    rng = np.random.default_rng(5)
    for _ in range(200):
        closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 24)))
        atr = float(rng.uniform(0.3, 2.0))
        ours, _ = v5.stop_exit(100.0, closes, atr, side)
        theirs, _ = v2.trend_exit(100.0, closes, atr, side)
        assert ours == pytest.approx(theirs, abs=1e-15)


def test_breakeven_arms_after_one_atr_and_uses_earlier_closes():
    closes = np.array([100.5, 101.2, 100.6, 99.9, 103.0])
    got, j = v5.stop_exit(100.0, closes, 1.0, 1, trail=None, breakeven=1.0)
    assert (j, got) == (3, pytest.approx(-0.001))
    got, j = v5.stop_exit(100.0, closes, 1.0, 1, trail=None)
    assert (j, got) == (4, pytest.approx(0.03))
    short, j = v5.stop_exit(100.0, 200.0 - closes, 1.0, -1, trail=None, breakeven=1.0)
    assert (j, short) == (3, pytest.approx(-0.001))


def path(per_hour: int, head: list[float], fill: float) -> np.ndarray:
    c = np.full(48 * per_hour, fill)
    c[: len(head)] = head
    return c


def test_scale_out_takes_half_at_target_and_trails_the_rest():
    c = path(1, [101.0, 102.5, 104.0, 100.4], 100.4)
    got = v5.exit_returns(100.0, c, 1.0, 1, per_hour=1)
    assert got["trail3_24h"] == pytest.approx(0.004)  # best 104, level 101, out at 100.4
    assert got["tp2_trail3_24h"] == pytest.approx(0.5 * 0.025 + 0.5 * 0.004)
    flat = v5.exit_returns(100.0, path(1, [101.0, 99.0, 97.5], 97.5), 1.0, 1, per_hour=1)
    assert flat["tp2_trail3_24h"] == flat["trail3_24h"] == pytest.approx(-0.025)  # target never reached


def test_holds_and_split_read_wall_clock_closes():
    c = 100 + np.arange(1, 97, dtype=float)  # 30m bars: 96 closes = 48h
    got = v5.exit_returns(100.0, c, 50.0, -1, per_hour=2)
    assert got["hold_12h"] == pytest.approx(-(c[23] / 100 - 1))
    assert got["hold_24h"] == pytest.approx(-(c[47] / 100 - 1))
    assert got["split_4h_24h"] == pytest.approx(-0.5 * (c[7] / 100 - 1) - 0.5 * (c[47] / 100 - 1))
    assert got["trail3_48h"] == pytest.approx(-(c[95] / 100 - 1))  # wide ATR: no stop, 48h cap
    assert set(got) == set(v5.EXITS)


def test_simulate_needs_a_complete_48h_path():
    n = 120
    series = {"open": np.full(n, 100.0), "close": np.full(n, 100.0), "atr": np.full(n, 1.0)}
    series["close"][60] = np.nan
    got = v5.simulate(series, np.array([5, 30, 70, 80]), np.array([1, 1, -1, 1]), per_hour=1)
    for rule in v5.EXITS:
        assert np.isfinite(got[rule]).tolist() == [True, False, True, False]


def test_summary_pairs_each_rule_with_the_baseline_on_the_same_events():
    rows = []
    for k in range(40):
        time = (pd.Timestamp("2024-06-01", tz="UTC") + pd.Timedelta(days=k)).isoformat()
        for rule in v5.EXITS:
            net = 0.001 * k + (0.002 if rule == "hold_24h" else 0.0)
            rows.append({"minutes": 60, "instrument": "eth", "exit": rule, "time": time,
                         "month": time[:7], "side": 1, "net": net, "control_net": 0.0})
    trades = pd.DataFrame(rows)
    trades.loc[(trades.exit == "be_24h") & (trades.time == rows[0]["time"]), "net"] = np.nan
    cfg = {"select_before": "2025-01-01T00:00:00Z", "stat_seed": 1, "flips": 200, "baseline": "hold_12h"}
    s = v5.summarize(trades, cfg).set_index("exit")
    assert (s.events == 39).all()  # the event missing one rule leaves every rule
    assert s.at["hold_24h", "vs_base_bp"] == pytest.approx(20.0)
    assert s.at["trail3_24h", "vs_base_bp"] == pytest.approx(0.0)
    assert np.isnan(s.at["hold_12h", "vs_base_bp"])
