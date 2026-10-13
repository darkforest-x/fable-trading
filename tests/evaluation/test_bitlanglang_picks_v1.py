"""Mechanics of exp-bitlanglang-picks-20261013-v1 (entry timing, horizon closes, config shape)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import bitlanglang_picks_v1 as bp

CONFIG = Path("experiments/active/exp-bitlanglang-picks-20261013-v1/config.json")


def fake_series(start: pd.Timestamp, days: int, slope: float) -> pd.DataFrame:
    ts = np.arange(start.value // 10**6, start.value // 10**6 + days * bp.DAY_MS, 300_000)
    price = 1.0 + slope * np.arange(len(ts))
    return pd.DataFrame({"ts": ts, "open": price, "high": price * 1.001, "low": price * 0.999, "close": price, "volume": 1.0})


def test_entry_is_the_first_bar_at_or_after_the_cutoff_and_horizon_uses_the_last_full_bar(monkeypatch, tmp_path):
    start = pd.Timestamp("2024-01-01", tz="UTC")
    raw = fake_series(start, 70, 0.0001)
    (tmp_path / "AAAUSDT.csv.gz").write_bytes(b"x")
    monkeypatch.setattr(bp, "SERIES", tmp_path)
    monkeypatch.setattr(bp, "read_5m", lambda s, a, b: raw.loc[(raw.ts >= a.value // 10**6) & (raw.ts + 300_000 <= b.value // 10**6)])
    entry = start + pd.Timedelta(days=2, hours=16)
    p = bp.price_path("AAAUSDT", entry, [7])
    assert p["entry_ts"] == entry.value // 10**6
    k0 = int((entry.value // 10**6 - raw.ts.iloc[0]) // 300_000)
    k7 = k0 + 7 * 288 - 1  # last 5m bar that closes at or before entry + 7 days
    assert np.isclose(p["entry"], raw.open.iloc[k0])
    assert np.isclose(p["ret_7d"], raw.close.iloc[k7] / raw.open.iloc[k0] - 1)


def test_missing_series_or_late_listing_is_not_scored(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, "SERIES", tmp_path)
    assert bp.price_path("NOPEUSDT", pd.Timestamp("2024-01-01", tz="UTC"), [7]) is None
    start = pd.Timestamp("2024-01-05", tz="UTC")
    raw = fake_series(start, 20, 0.0)
    (tmp_path / "LATEUSDT.csv.gz").write_bytes(b"x")
    monkeypatch.setattr(bp, "read_5m", lambda s, a, b: raw.loc[(raw.ts >= a.value // 10**6) & (raw.ts + 300_000 <= b.value // 10**6)])
    assert bp.price_path("LATEUSDT", pd.Timestamp("2024-01-01 16:00", tz="UTC"), [7]) is None


def test_config_records_every_pick_with_its_source_before_scoring():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert cfg["entry_utc_hour"] == 16 and cfg["universe_size"] == 100 and cfg["draws"] >= 10_000
    for s in cfg["sessions"]:
        assert s["video"] and s["date"] and s["picks"]
        for p in s["picks"]:
            assert p["symbol"].endswith("USDT") and p["rank"] in {"focus", "backup"}
            assert isinstance(p.get("t"), int)
