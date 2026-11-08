"""Reinvest only fee-net profit left after reserving the original runner stop risk.

This builder reuses the frozen parent paths, the existing profit-confirm-entry
opportunities, and the cash-sharing VectorBT adapter in
``cloud_close_reinvest_runner``. Its only execution change is the per-add-on
quantity cap derived at the order callback from the realized 2R partial and
the still-open original runner's fixed stop risk.
"""
from __future__ import annotations

import argparse
import gc
import json
from pathlib import Path

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_eligibility_repair as repair
from yoyo.evaluation import cloud_close_profit_confirm_entry as confirmation
from yoyo.evaluation import cloud_close_reinvest_runner as adapter
from yoyo.evaluation import cloud_close_reward_account as wallet
from yoyo.evaluation import cloud_close_thirds as thirds


EXP = wallet.ROOT / 'experiments/active/exp-cloud-close-profit-budget-add-20261109-v1'
BASELINE = repair.EXP / 'thirds' / 'funded_OKX.csv.gz'
HOURLY_BASELINE = repair.EXP / 'thirds' / 'hourly_OKX.csv.gz'
OPPORTUNITIES = confirmation.EXP / 'opportunities.csv.gz'


def _one_event_smoke(paths: pd.DataFrame, opportunities: pd.DataFrame,
                     candidate: pd.Series, slip: float) -> dict:
    event_id = str(candidate.event_id)
    event_paths = paths[paths.event_id.eq(event_id)].copy()
    selected, _ = adapter._selected_originals(event_paths, 9)
    if len(selected) != 1:
        raise ValueError(f'Mini event did not select exactly one original: {event_id}')
    start = int(selected.entry_ms.iloc[0])
    end = int(selected.exit_bar_ms.iloc[0]) + wallet.HOUR
    event_opportunities = opportunities[opportunities.event_id.eq(event_id)].copy()
    if len(event_opportunities) != 1:
        raise ValueError(f'Mini event does not have exactly one opportunity: {event_id}')

    baseline_data = adapter.prepare(selected, event_opportunities.iloc[0:0], 9,
                                    start=start, end=end, reinvest=False)
    base_positions, _, _, base_meta = adapter.account(baseline_data, slip=slip)
    policy_data = adapter.prepare(selected, event_opportunities, 9,
                                  start=start, end=end, reinvest=True,
                                  profit_only_budget=True)
    positions, addons, _, meta = adapter.account(policy_data, slip=slip)
    if len(addons) != 1:
        raise ValueError(f'Profit-budget mini did not fund one add-on: {event_id}')
    addon = addons.iloc[0]
    if addon.side not in (-1, 1) or int(addon.side) != int(candidate.side):
        raise ValueError('Mini add-on direction differs from its source event')
    if addon.target_notional_cap < addon.notional - 1e-6:
        raise ValueError('Mini add-on exceeded the original target-leg notional')
    if addon.available_cash_before < addon.notional - 1e-6:
        raise ValueError('Mini add-on exceeded its order-time free cash')
    if addon.stop_risk_used_usdt > addon.profit_budget_usdt + 1e-6:
        raise ValueError('Mini add-on stop loss exceeded its net-profit budget')
    if addon.profit_budget_usdt <= 0. or addon.addon_unit_stop_loss <= 0.:
        raise ValueError('Mini add-on has no valid positive risk budget')
    if addon.entry_fee <= 0. or addon.exit_fee <= 0.:
        raise ValueError('Mini add-on did not charge both execution fees')
    if not np.isclose(positions.base_cash_pnl.iloc[0], base_positions.cash_pnl.iloc[0],
                      rtol=1e-10, atol=1e-6):
        raise ValueError('Profit-budget policy changed the original position cash flow')
    if not np.isclose(meta['cash_from_orders'], meta['cash_from_position_ledger'],
                      rtol=1e-10, atol=1e-6):
        raise ValueError('Mini VectorBT cash ledger does not reconcile')
    return dict(event_id=event_id, side=int(addon.side), slip=float(slip),
                target_net_profit_usdt=float(addon.target_net_profit_usdt),
                runner_stop_loss_usdt=float(addon.runner_stop_loss_usdt),
                available_profit_budget_usdt=float(addon.profit_budget_usdt),
                addon_unit_stop_loss_usdt=float(addon.addon_unit_stop_loss),
                max_quantity_from_profit=float(addon.max_quantity_from_profit),
                actual_quantity=float(addon.quantity),
                actual_stop_risk_used_usdt=float(addon.stop_risk_used_usdt),
                unused_profit_budget_usdt=float(addon.stop_risk_budget_unused_usdt),
                target_notional_cap=float(addon.target_notional_cap),
                order_time_free_cash=float(addon.available_cash_before),
                actual_addon_notional=float(addon.notional),
                actual_addon_entry_fee=float(addon.entry_fee),
                actual_addon_exit_fee=float(addon.exit_fee),
                addon_cash_pnl=float(addon.cash_pnl),
                parent_cash_pnl=float(positions.cash_pnl.iloc[0]),
                original_cash_pnl_without_addon=float(base_positions.cash_pnl.iloc[0]),
                account_cash_reconciliation=float(meta['cash_from_orders']-
                                                  meta['cash_from_position_ledger']),
                final_nav=float(meta['final_nav']),
                original_funded=meta['original_funded'], addon_funded=meta['reinvest_funded'],
                baseline_final_nav=float(base_meta['final_nav']))


def smoke() -> dict:
    """Run one actual long and short candidate, plus a 5bp cost replay for each."""
    paths, opportunities, _ = adapter.read_inputs()
    base, _ = adapter._selected_originals(paths, 9)
    candidates, counts = adapter._validate_reinvestment_candidates(base, opportunities)
    if len(candidates) != 381:
        raise ValueError(f'Frozen candidate count changed: {len(candidates)}')
    results = []
    for side in (1, -1):
        side_candidates = (candidates[candidates.side.eq(side)]
                           .sort_values(['entry_ms', 'entry_order', 'symbol', 'event_id']))
        if side_candidates.empty:
            raise ValueError(f'No existing profit-confirm candidate for side={side}')
        # Attempt a small number only if an event's fee-net budget is zero. The
        # selected candidate is recorded so a skip cannot silently stand in as
        # a successful budget-protection check.
        funded = None
        for candidate in side_candidates.head(8).itertuples(index=False):
            row = pd.Series(candidate._asdict())
            try:
                actual = _one_event_smoke(paths, opportunities, row, 0.)
            except ValueError as exc:
                if 'did not fund one add-on' in str(exc):
                    continue
                raise
            stressed = _one_event_smoke(paths, opportunities, row, .0005)
            funded = dict(actual=actual, stress_5bp=stressed)
            break
        if funded is None:
            raise ValueError(f'No funded profit-budget mini among first eight side={side} candidates')
        results.append(funded)
    return dict(adapter='existing VectorBT cash-sharing reinvest adapter, optional profit-only hook',
                candidates=len(candidates), candidate_counts=counts,
                long_and_short_budget_smoke=results)


def _risk_summary(positions: pd.DataFrame, reentries: pd.DataFrame) -> dict:
    status = positions.reinvest_status.value_counts(dropna=False).to_dict()
    if not len(reentries):
        return dict(status_counts=status, funded_addons=0, target_net_profit_usdt=0.,
                    runner_stop_loss_reserved_usdt=0., available_profit_budget_usdt=0.,
                    realized_stop_risk_used_usdt=0., max_budget_overrun_usdt=0.)
    overrun = (reentries.stop_risk_used_usdt - reentries.profit_budget_usdt).clip(lower=0.)
    return dict(status_counts=status, funded_addons=len(reentries),
                target_net_profit_usdt=float(reentries.target_net_profit_usdt.sum()),
                runner_stop_loss_reserved_usdt=float(reentries.runner_stop_loss_usdt.sum()),
                available_profit_budget_usdt=float(reentries.profit_budget_usdt.sum()),
                realized_stop_risk_used_usdt=float(reentries.stop_risk_used_usdt.sum()),
                max_budget_overrun_usdt=float(overrun.max()))


def _metrics_rows(label: str, metrics: dict) -> list[dict]:
    return [dict(policy=label, period=period, **values)
            for period, values in metrics.items()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', action='store_true', help='Run bounded long/short mini checks only')
    args = parser.parse_args()
    if args.smoke:
        print(json.dumps(smoke(), indent=2, allow_nan=False))
        return

    paths, opportunities, frozen = adapter.read_inputs()
    no_add_metrics, no_add_positions, _, no_add_equity, no_add_meta, no_add_selected = adapter.simulate(
        paths, opportunities.iloc[0:0], reinvest=False)
    if set(no_add_selected.event_id) != set(frozen.event_id):
        raise ValueError('Zero-add original event set differs from the frozen funded ledger')
    baseline_check = adapter._baseline_reconciliation(no_add_positions, frozen)
    hourly_check = adapter._hourly_reconciliation(no_add_equity, HOURLY_BASELINE)
    if (baseline_check['max_abs_cash_pnl_diff'] > 1e-6
            or baseline_check['max_abs_notional_diff'] > 1e-6
            or any(baseline_check['identity_mismatches'].values())
            or max(hourly_check['max_abs_by_field'].values()) > 1e-6):
        raise ValueError(f'Zero-add baseline reconciliation failed: {baseline_check} {hourly_check}')
    rows = _metrics_rows('zero_add_replay', no_add_metrics)
    del no_add_positions, no_add_equity, no_add_selected
    gc.collect()

    metrics, positions, reentries, equity, meta, selected = adapter.simulate(
        paths, opportunities, profit_only_budget=True)
    if set(selected.event_id) != set(frozen.event_id):
        raise ValueError('Profit-budget account changed the frozen original event set')
    rows.extend(_metrics_rows('profit_only_reinvested', metrics))
    dev = metrics['dev']
    dev_pass = bool(wallet.qualifies(dev) and dev['payoff_usdt'] >= 2.5 and dev['trades'] >= 100)
    checks = {}
    if dev_pass:
        all_paths, all_opportunities, _ = adapter.read_inputs()
        control_paths = thirds.okx_paths()
        control_paths = control_paths[control_paths.is_control & control_paths.tp_r.isin([0., 2.])
                                      & control_paths.parent_id.isin(all_paths.parent_id)].copy()
        control_parent_ids = set(control_paths.loc[control_paths.tp_r.eq(0.), 'parent_id'])
        actual_paths = all_paths[all_paths.parent_id.isin(control_parent_ids)]
        raw_opps = pd.read_csv(OPPORTUNITIES, float_precision='round_trip',
                               dtype={'symbol': str}, keep_default_na=False)
        raw_opps = raw_opps[raw_opps.is_control].copy()
        for label, event_paths, event_opps, slip in (
                ('paired_actual', actual_paths, all_opportunities, 0.),
                ('matched_control', control_paths, raw_opps, 0.),
                ('stress_5bp', all_paths, all_opportunities, .0005)):
            values, check_positions, addon_subset, _, check_meta, _ = adapter.simulate(
                event_paths, event_opps, slip=slip, profit_only_budget=True)
            checks[label] = dict(metrics=values, account=check_meta,
                                 addon_metrics=adapter._reentry_trade_metrics(addon_subset),
                                 earned_profit_risk=_risk_summary(check_positions, addon_subset))
            rows.extend(_metrics_rows(label, values))
            gc.collect()

    EXP.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(EXP / 'account_metrics.csv', index=False)
    positions.to_csv(EXP / 'funded.csv.gz', index=False)
    reentries.to_csv(EXP / 'reinvested.csv.gz', index=False)
    equity.to_csv(EXP / 'hourly.csv.gz', index_label='hour_end_utc')
    summary = dict(experiment_id='exp-cloud-close-profit-budget-add-20261109-v1',
                   baseline_reconciliation=baseline_check,
                   baseline_hourly_reconciliation=hourly_check,
                   zero_add_account=no_add_meta,
                   original_event_set_matches_frozen=len(selected) == len(frozen),
                   development_pass=dev_pass, parent_level_metrics=metrics,
                   addon_leg_metrics=adapter._reentry_trade_metrics(reentries),
                   earned_profit_risk=_risk_summary(positions, reentries),
                   account=meta, checks=checks)
    (EXP / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n')
    print(pd.DataFrame(rows)[lambda frame: frame.period.eq('all')].to_string(index=False), flush=True)


if __name__ == '__main__':
    main()
