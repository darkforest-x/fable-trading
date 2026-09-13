from __future__ import annotations

import numpy as np
import pandas as pd

from yoyo.evaluation.spike_v1_v8_asset_ranking import (
    NORMALIZED_COLUMNS,
    SPLIT,
    _risk_fields,
    build_rankings,
    ranking_views,
    score_flags,
    summarize_groups,
)


def _ledger_row(**override: object) -> dict[str, object]:
    row: dict[str, object] = {
        "system_source": "v8", "stream_key": "okx_60m_x", "venue": "okx", "symbol": "AAA-USDT-SWAP",
        "asset": "AAA", "timeframe_min": 60, "side": 1,
        "signal_bar_open": "2025-01-01T00:00:00Z", "signal_confirm_time": "2025-01-01T01:00:00Z",
        "entry_time": "2025-01-01T01:00:00Z", "exit_time": "2025-01-02T01:00:00Z", "exit_reason": "trail",
        "net_r": 1.0, "net_return": 0.05, "mfe_r": 2.0, "censored": False, "closed": True,
        "executed": True, "period": "development", "scoring_closed": True,
        "risk_fraction_at_entry": 0.04, "risk_fraction_source": "frozen_trade_initial_risk_frac", "cost_r": 0.05,
    }
    row.update(override)
    return row


def test_confirmation_clock_purges_cross_split_but_retains_full_closed() -> None:
    frame = pd.DataFrame([
        {"signal_confirm_time": SPLIT - pd.Timedelta(hours=1), "exit_time": SPLIT + pd.Timedelta(hours=1), "closed": True},
        {"signal_confirm_time": SPLIT, "exit_time": SPLIT + pd.Timedelta(hours=1), "closed": True},
    ])
    scored = score_flags(frame)
    assert scored.period.tolist() == ["development", "validation"]
    assert scored.scoring_closed.tolist() == [False, True]


def test_risk_fallback_is_labeled_and_zero_r_stays_unknown() -> None:
    raw = pd.DataFrame({"initial_risk_frac": [np.nan, np.nan, 0.04], "net_r": [2.0, 0.0, 1.0], "net_return": [0.10, 0.0, 0.03]})
    got = _risk_fields(raw)
    assert got.risk_fraction_at_entry.iloc[0] == 0.05
    assert got.risk_fraction_source.iloc[0] == "derived_net_return_div_net_r"
    assert pd.isna(got.risk_fraction_at_entry.iloc[1])
    assert got.risk_fraction_source.iloc[1] == "missing"
    assert got.cost_r.iloc[2] == 0.05


def test_v8_long_view_is_not_v8_both() -> None:
    ledger = pd.DataFrame([_ledger_row(side=1), _ledger_row(side=-1, asset="BBB")])
    views = ranking_views(ledger)
    assert len(views[("v8_long", "full_closed")]) == 1
    assert len(views[("v8_both", "full_closed")]) == 2


def test_pf_r_and_nominal_return_are_separate_and_concentration_is_reported() -> None:
    frame = pd.DataFrame([_ledger_row(net_r=10.0, net_return=0.1), _ledger_row(net_r=-1.0, net_return=-0.2)])
    summary = summarize_groups(frame, ["asset"]).iloc[0]
    assert summary.pf_net_r == 10.0
    assert summary.pf_net_return == 0.5
    assert summary.realized_ge10r == 1
    assert summary.top1_positive_r_share == 1.0


def test_main_ranking_uses_fixed_minimum_and_low_sample_is_separate() -> None:
    many = [_ledger_row(asset="AAA", stream_key=f"s{i}", signal_bar_open=f"2025-01-{(i % 28) + 1:02d}T00:00:00Z") for i in range(30)]
    few = [_ledger_row(asset="BBB", stream_key="b", signal_bar_open="2025-02-01T00:00:00Z")]
    results = build_rankings(pd.DataFrame(many + few), min_closed=30)
    main = results["asset_ranking_main_min30"]
    low = results["asset_ranking_low_sample"]
    assert "AAA" in set(main.asset)
    assert "BBB" in set(low.asset)
    assert set(NORMALIZED_COLUMNS).issuperset({"system_source", "cost_r", "scoring_closed"})
