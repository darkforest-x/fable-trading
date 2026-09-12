"""Focused contracts for descriptive SPIKE coin and break-even reporting."""
from __future__ import annotations

import pandas as pd

from yoyo.evaluation.spike_coin_be_report import choose_cases, event_metrics, numeric_cap, same_entry_pairs


def test_mixed_notional_cap_never_treats_stress_label_as_one_x() -> None:
    assert numeric_cap("1.0") == 1.0
    assert numeric_cap(1) == 1.0
    assert numeric_cap("uncapped_stress") is None


def test_event_metrics_preserve_zero_denominator_as_missing() -> None:
    result = event_metrics(pd.DataFrame([dict(closed=0, wins=0, positive_return_sum=0., negative_return_sum=0., net_r_sum=0.)]))
    assert result.net_win_rate.isna().all() and result.net_pf.isna().all() and result.mean_net_r.isna().all()


def test_pairing_requires_same_frozen_entry_and_labels_loss_reduction_without_false_net_be() -> None:
    shared = dict(stream_key="s", venue="okx", symbol="X-USDT-SWAP", asset="X", timeframe_min=60, cohort="v6_both", signal_bar_open="2025-01-01T00:00:00Z", entry_time="2025-01-01T01:00:00Z", side=1, net_return=0., mfe_r=4., exit_time="x", exit_price=1., exit_reason="x", censored=False)
    rows = [dict(shared, trade_id="base", policy="baseline", net_r=-1.), dict(shared, trade_id="be", policy="be1_price", net_r=-.02)]
    paired = same_entry_pairs(pd.DataFrame(rows))
    row = paired.loc[paired.comparison_policy.eq("be1_price")].iloc[0]
    assert row.same_entry and row.be_reduced_loss and not row.be_rescued_to_net_nonloss and not row.be_cut_trend
    cases = choose_cases(paired, limit=4)
    assert len(cases) >= 1 and cases.case_kind.eq("be_reduced_loss").any()


def test_case_selection_uses_pepe_contract_alias_without_erasing_original_asset() -> None:
    base = dict(stream_key="s", venue="binance", symbol="1000PEPEUSDT", asset="1000PEPE", timeframe_min=60,
                cohort="v6_both", signal_bar_open="2025-10-01T00:00:00Z", entry_time="2025-10-01T01:00:00Z", side=1,
                net_return=0., mfe_r=11., exit_time="2025-10-02T01:00:00Z", exit_price=1., exit_reason="x", censored=False)
    pairs = same_entry_pairs(pd.DataFrame([dict(base, trade_id="b", policy="baseline", net_r=11.), dict(base, trade_id="p", policy="be1_price", net_r=10.)]))
    cases = choose_cases(pairs)
    assert "1000PEPE" in set(cases.asset) and "PEPE" in set(cases.asset_group)
