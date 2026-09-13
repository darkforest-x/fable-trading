"""Focused causality checks for the fixed monthly asset-exclusion study."""
from __future__ import annotations

import pandas as pd

from yoyo.evaluation.spike_asset_exclusion_study import (
    _metrics,
    attach_lists,
    load_ledger,
    monthly_lists,
    primary_rank_p,
    random_asset_lists,
    scopes,
)


def _row(asset: str, confirmed: str, exited: str, net_r: float, *, source: str = "v8", side: int = 1) -> dict[str, object]:
    return {
        "system_source": source,
        "strategy": "v1" if source.startswith("v1") else "v8",
        "stream_key": f"binance_60m_{asset}",
        "asset": asset,
        "timeframe_min": 60,
        "side": side,
        "signal_confirm_time": pd.Timestamp(confirmed),
        "exit_time": pd.Timestamp(exited),
        "net_r": net_r,
        "net_return": net_r / 100,
        "mfe_r": max(net_r, 0.0),
        "closed": True,
        "scoring_closed": True,
    }


def _rankable_v8() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for asset, value in (("A", -4.0), ("B", -3.0), ("C", -2.0), ("D", 1.0), ("E", 2.0)):
        for number in range(10):
            rows.append(_row(asset, f"2024-09-{10 + number:02d} 00:00:00+00:00", f"2024-09-{11 + number:02d} 00:00:00+00:00", value))
    # This post-October-boundary A loss must remain in November's ranking even
    # though A is excluded by October's counterfactual admission list.
    rows.append(_row("A", "2024-10-05 00:00:00+00:00", "2024-10-06 00:00:00+00:00", -5.0))
    return pd.DataFrame(rows)


def test_bottom_fraction_is_taken_before_negative_condition_and_shadow_history_stays_full() -> None:
    listings = monthly_lists(_rankable_v8(), "v8_both")
    october = listings.loc[listings.decision_time.eq(pd.Timestamp("2024-10-01T00:00:00Z"))]
    assert october.bottom_pool_n.iloc[0] == 1  # ceil(20% of all five eligible assets)
    assert october.loc[october.asset.eq("A"), "causal_excluded"].item()
    assert not october.loc[october.asset.eq("B"), "causal_excluded"].item()
    random = random_asset_lists(listings, "v8_both")
    october_draws = random.loc[random.decision_time.eq(pd.Timestamp("2024-10-01T00:00:00Z"))]
    assert october_draws.groupby("random_seed").size().eq(october.causal_excluded.sum()).all()
    assert october_draws.random_seed.nunique() == 100
    november_a = listings.loc[
        listings.decision_time.eq(pd.Timestamp("2024-11-01T00:00:00Z")) & listings.asset.eq("A")
    ].iloc[0]
    assert november_a.history_trades == 11


def test_month_boundary_uses_confirmation_not_next_open() -> None:
    cohort = _rankable_v8()
    lists = monthly_lists(cohort, "v8_both")
    boundary = pd.DataFrame([
        _row("A", "2024-09-30 23:00:00+00:00", "2024-10-02 00:00:00+00:00", -1.0),
        _row("A", "2024-10-01 00:00:00+00:00", "2024-10-02 00:00:00+00:00", -1.0),
    ])
    attached = attach_lists(boundary, lists)
    assert attached.causal_excluded.tolist() == [False, True]


def test_positive_bottom_pool_is_not_replaced_by_an_extra_random_deletion() -> None:
    positive = _rankable_v8()
    for asset, value in (("A", 0.1), ("B", 1.0), ("C", 2.0), ("D", 3.0), ("E", 4.0)):
        positive.loc[positive.asset.eq(asset), "net_r"] = value
    lists = monthly_lists(positive, "v8_both")
    october = lists.loc[lists.decision_time.eq(pd.Timestamp("2024-10-01T00:00:00Z"))]
    assert october.bottom_pool_n.iloc[0] == 1
    assert october.causal_excluded.sum() == 0
    assert random_asset_lists(lists, "v8_both").empty


def test_scopes_preserve_v1_long_only_and_v8_both() -> None:
    frame = pd.DataFrame([
        _row("A", "2025-01-01 00:00:00+00:00", "2025-01-02 00:00:00+00:00", 1.0, source="v1_common_execution_long"),
        _row("B", "2025-01-01 00:00:00+00:00", "2025-01-02 00:00:00+00:00", 1.0, side=1),
        _row("C", "2025-01-01 00:00:00+00:00", "2025-01-02 00:00:00+00:00", -1.0, side=-1),
    ])
    result = scopes(frame)
    assert len(result["v1_long"]) == 1
    assert len(result["v8_long"]) == 1
    assert len(result["v8_both"]) == 2


def test_loader_keeps_open_rows_out_of_history_without_rejecting_them(tmp_path) -> None:
    closed = _row("A", "2025-01-01 00:00:00+00:00", "2025-01-02 00:00:00+00:00", 1.0)
    open_row = _row("A", "2025-01-03 00:00:00+00:00", "2025-01-03 00:00:00+00:00", 0.0)
    open_row.update({"closed": False, "exit_time": None, "net_r": None, "net_return": None, "mfe_r": None})
    path = tmp_path / "ledger.csv"
    pd.DataFrame([closed, open_row]).drop(columns="strategy").to_csv(path, index=False)
    loaded = load_ledger(path)
    assert len(loaded) == 2
    assert loaded.known_closed_outcome.tolist() == [True, False]


def test_metrics_report_both_r_and_return_pf_and_primary_rank_p_uses_100_draws() -> None:
    metrics = _metrics(pd.DataFrame({"net_r": [2.0, -1.0], "net_return": [0.01, -0.02]}))
    assert metrics["profit_factor_net_r"] == 2.0
    assert metrics["profit_factor_net_return"] == 0.5
    random = pd.DataFrame({
        "scope": ["v8_both"] * 100,
        "level": ["period"] * 100,
        "period": ["validation"] * 100,
        "policy": ["random_equal_asset_count"] * 100,
        "net_r_sum": [11.0] + [9.0] * 99,
    })
    causal = pd.DataFrame({
        "scope": ["v8_both"], "level": ["period"], "period": ["validation"],
        "policy": ["causal_bottom20_negative"], "net_r_sum": [10.0],
    })
    p = primary_rank_p(pd.concat([random, causal], ignore_index=True))
    assert p.one_tailed_rank_p.item() == 2 / 101
