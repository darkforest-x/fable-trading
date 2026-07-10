from __future__ import annotations

import importlib
from pathlib import Path

import pandas as pd

from src.judgment import candidates
from src.judgment import candidates_v206
from src.judgment.features import FEATURE_COLUMNS, add_features
from src.judgment.frozen import default_config


def _price_frame(rows: int = 320) -> pd.DataFrame:
    close = pd.Series([100.0 + index * 0.01 for index in range(rows)])
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-01-01", periods=rows, freq="15min", tz="UTC"),
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 1000.0,
        }
    )


def test_judgment_indicators_use_only_sma_ema_20_60_120() -> None:
    enriched = candidates.add_indicators(_price_frame())

    assert candidates.MA_PERIODS == (20, 60, 120)
    assert {"sma20", "ema20", "sma60", "ema60", "sma120", "ema120"} <= set(enriched.columns)
    assert {"ema8", "ema13", "ema21", "ema34", "ema55", "ema144", "ema200"}.isdisjoint(enriched.columns)


def test_judgment_features_name_the_20_60_120_anchors() -> None:
    featured = add_features(candidates.add_indicators(_price_frame()))

    assert "close_vs_ema60" in FEATURE_COLUMNS
    assert "close_vs_ema120" in FEATURE_COLUMNS
    assert "close_vs_ema55" not in FEATURE_COLUMNS
    assert "close_vs_ema200" not in FEATURE_COLUMNS
    assert {"close_vs_ema60", "close_vs_ema120"} <= set(featured.columns)


def test_default_frozen_config_uses_ma206_dataset(tmp_path: Path) -> None:
    config = default_config(tmp_path)

    assert config.name == "tp5_sl2_swap_ma206"
    assert config.dataset_path == tmp_path / "data" / "ma206" / "swap_tp5_sl2_ma206.csv"


def test_ma206_research_entrypoints_import() -> None:
    assert importlib.import_module("scripts.swap_h1h9_stack") is not None
    assert importlib.import_module("scripts.v3_portfolio_sim") is not None


def test_v206_compatibility_scan_uses_expanded_mode(monkeypatch) -> None:
    received: dict[str, object] = {}

    def fake_scan(frame: pd.DataFrame, *, horizon_bars: int, mode: str) -> list[int]:
        received.update(horizon_bars=horizon_bars, mode=mode, rows=len(frame))
        return [42]

    monkeypatch.setattr(candidates_v206, "_scan_candidates", fake_scan)

    assert candidates_v206.scan_candidates(_price_frame(10), horizon_bars=96) == [42]
    assert received == {"horizon_bars": 96, "mode": "expanded", "rows": 10}
