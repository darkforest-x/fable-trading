from __future__ import annotations

import pandas as pd

import scripts.research_btcusdtp_15m_trend_regime_live_entry as subject


def test_live_pairs_rejects_stale_or_opposed_regime_memory() -> None:
    pairs = pd.DataFrame(
        {
            "signed_fast_slow_spread_atr": [0.2, 0.2, -0.1, 0.0],
            "signed_fast_slope4_atr_per_bar": [0.01, -0.01, 0.02, 0.02],
            "name": ["live", "slope_stale", "spread_opposed", "zero_spread"],
        }
    )

    actual = subject._live_pairs(pairs)

    assert actual["name"].tolist() == ["live"]


def test_pine_v5_enforces_live_direction_and_preserves_sparse_surface() -> None:
    source = (
        subject.EXPERIMENT / "pine/fable_15m_trend_regime_live_v5.pine"
    ).read_text(encoding="utf-8")

    assert '"Fable 15m Live Trend · Episode V5"' in source
    assert "fastSlowSpreadAtr > 0.0 and ema30Slope4AtrPerBar >= 0.0" in source
    assert "fastSlowSpreadAtr < 0.0 and ema30Slope4AtrPerBar <= 0.0" in source
    assert "trendRegime == 1 and currentLongTrendAlive and rawLongPair" in source
    assert "trendRegime == -1 and currentShortTrendAlive and rawShortPair" in source
    assert source.count('plot(showMainMa ? ema30 : na, "EMA30 · main"') == 1
    assert "plot(showRunnerMa" not in source
    assert "box.set_right" not in source
    assert "TP" not in source
    assert "SL" not in source
