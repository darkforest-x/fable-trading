"""v4 environment features must be measured before the shock bar opens."""
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest

from yoyo.evaluation import market_sync_shock_v4 as m

T0 = pd.Timestamp("2024-01-01", tz="UTC").value // 1_000_000
FIVE = 300_000


def raw_5m(hours: int, drift: float = 0.0, drop_hour: int | None = None) -> pd.DataFrame:
    ts = T0 + np.arange(hours * 12) * FIVE
    close = 100 * np.exp(drift * np.arange(len(ts)))
    df = pd.DataFrame({"ts": ts, "open": close, "high": close, "low": close, "close": close, "volume": 1.0})
    if drop_hour is not None:
        df = df.loc[~((df.ts >= T0 + drop_hour * 3_600_000) & (df.ts < T0 + (drop_hour + 1) * 3_600_000))]
    return df.reset_index(drop=True)


def test_hourly_features_are_indexed_by_close_time_and_span_wall_clock_hours():
    env = m.btc_hourly(raw_5m(800, drift=1e-4))
    first_close = T0 + 3_600_000
    assert env.index[0] == first_close
    t = first_close + 750 * 3_600_000
    expected = np.exp(1e-4 * 12 * 720) - 1  # 720 hours of 12 five-minute steps
    assert env.at[t, "trend30"] == pytest.approx(expected, rel=1e-9)
    assert env.at[t, "vol30"] == pytest.approx(0.0, abs=1e-12)


def test_missing_hour_stays_missing_instead_of_shifting_the_window():
    env = m.btc_hourly(raw_5m(800, drift=1e-4, drop_hour=100))
    assert np.isnan(env.at[T0 + 101 * 3_600_000, "close"])
    t = T0 + 790 * 3_600_000
    assert env.at[t, "trend30"] == pytest.approx(np.exp(1e-4 * 12 * 720) - 1, rel=1e-9)


def test_environment_is_read_at_or_before_the_event_open():
    env = pd.DataFrame({f: [1.0, 2.0, 3.0] for f in m.FEATURES},
                       index=[T0 + h * 3_600_000 for h in (10, 11, 12)])
    ev = pd.DataFrame({"time": [pd.Timestamp(T0 + 11 * 3_600_000, unit="ms", tz="UTC").isoformat(),
                                pd.Timestamp(T0 + 11 * 3_600_000 + 1_800_000, unit="ms", tz="UTC").isoformat()],
                       "minutes": [60, 30], "side": [1, 1]})
    out = m.attach_environment(ev, env)
    # 1H bar opening 11:00 and 30m bar opening 11:30 both see the 11:00 close, never 12:00
    assert out.trend30.tolist() == [2.0, 2.0] and out.env_close_ms.tolist() == [T0 + 11 * 3_600_000] * 2
