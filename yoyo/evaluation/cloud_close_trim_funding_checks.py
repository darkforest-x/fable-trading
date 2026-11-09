"""Check the funded hourly-cap wallet against observed orders, rates and its fee-only prefix."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_hourly_trim_funding as engine

EXP = engine.EXP
ROOT_ACCOUNT = EXP/'account'


def zero_rate_parity():
    cfg = engine._read_json(engine.CONFIG_PATH)
    kids, tape, _, _ = engine._load_source_inputs(cfg)
    groups = kids.groupby('event_id').agg(entry=('entry_ms','first'),exit=('exit_bar_ms','max')).sort_values('entry')
    flat = groups.entry.shift(-1).gt(groups.exit.cummax()+engine.HOUR)
    end_index = int(np.flatnonzero(flat)[0])+1
    ids = groups.index[:end_index]
    chosen = kids[kids.event_id.isin(ids)].copy()
    archive = engine.hourly.tested.admin.archive
    extra = set(engine._read_json(archive.EXP/'summary.json')['archived_additions'])
    extra.update(engine._read_json(engine.hourly.tested.admin.study.EXP/'summary.json')['new_archive_cohorts']['priced'])
    end = int(chosen.exit_bar_ms.max())+engine.HOUR
    with archive.market_quotes(extra):
        data = engine.wallet.prepare(None,18,archive.venue.START,end,assigned=(chosen,{}),quote_loader=archive.native.quotes)
        data = engine._expand_hourly(data,archive.native.quotes,0.2)
    data['funding_input_audit'] = {'scope':'first naturally flat interval of the actual frozen hourly wallet'}
    cfg['account']['expected_positions'] = len(ids)
    _,parents,equity,_,result = engine._run_scenario(data,tape.assign(funding_rate=0.),cfg,'exclude_boundary')
    history = pd.read_csv(engine.hourly.EXP/'funded_Binance.csv.gz').set_index('event_id').loc[parents.index]
    curve = pd.read_csv(engine.hourly.EXP/'hourly_Binance.csv.gz',index_col='hour_end_utc',parse_dates=True).loc[equity.index]
    caps = ['nav','open_nav','open_exit_nav','post_trim_nav','lower_nav','upper_nav']
    nav_error = float(np.abs(equity[caps].to_numpy()-curve[caps].to_numpy()).max())
    cash_error = float(np.abs(history.cash_pnl.to_numpy()-parents.cash_pnl.to_numpy()).max())
    if nav_error>=.00001 or cash_error>=.00001 or result['audit']['trim_order_count']<1:
        raise ValueError(f'Zero-rate account does not match the original prefix: {nav_error}, {cash_error}')
    return dict(positions=len(ids),child_reductions=result['audit']['trim_order_count'],
                end_utc=str(equity.index[-1]),hours=len(equity),all_six_nav_fields_max_error_usdt=nav_error,
                maximum_per_position_net_cash_difference_usdt=cash_error)


def verify_case(location):
    orders = pd.read_csv(location/'orders.csv.gz')
    parents = pd.read_csv(location/'funded_positions.csv.gz')
    flows = pd.read_csv(location/'applied_funding.csv.gz')
    eq = pd.read_csv(location/'hourly_equity.csv.gz',index_col='hour_end_utc',parse_dates=True)
    live = flows[flows.applied]
    cash = -live.quantity_used * live.funding_rate * live.mark_price
    if not np.allclose(cash,live.funding_cashflow_usdt,atol=1.e-9,rtol=1.e-10):
        raise ValueError('Funding dollars do not reconcile to actual position quantity and rate')
    if not np.isclose(1000+cash.sum()+orders.order_cashflow_usdt.sum(),eq.nav.iloc[-1],atol=.00001,rtol=1.e-9):
        raise ValueError('Fee-adjusted fills and funding dollars do not reconcile to terminal NAV')
    trims = orders[orders.is_trim]
    counts = parents.event_id.map(trims.groupby('event_id').size()).fillna(0).astype(int)
    # Initial native output counted the number of child legs with trims; reconstruct actual executions.
    if not np.array_equal(counts,parents.trim_order_count):
        parents['trim_order_count'] = counts
        engine._write_frame(location/'funded_positions.csv.gz',parents,index=False,compression={'method':'gzip','mtime':0})
    periods = engine._period_summary(parents,eq)
    return dict(periods=periods,funding_usdt=float(cash.sum()),applied_child_payments=len(live),
                trim_child_fills=len(trims),trim_parent_hours=len(trims.drop_duplicates(['event_id','hour_open_ms'])),
                transaction_fees_usdt=float(orders.fees.sum()),
                terminal_cash_plus_positions_reconciled=True)


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Already checked against the original wallet')
    parity = EXP/'zero_rate_parity.json'
    if not parity.exists():
        parity.write_text(json.dumps(zero_rate_parity(),indent=2,allow_nan=False)+'\n')
    scenarios = {}
    for mode,directory in [('native',ROOT_ACCOUNT),('adverse_market_5bp',ROOT_ACCOUNT/'market_slip_5bp')]:
        if not (directory/'funding_account_audit.json').is_file():
            raise FileNotFoundError('Await completed VectorBT scenario '+str(directory))
        scenarios[mode] = {label:verify_case(directory/label) for label in ('include_boundary','exclude_boundary')}
    hour_results = json.loads((engine.hourly.EXP/'summary.json').read_text())
    result = dict(venue='Binance USD-M',start_utc='2020-01-01T00:00:00Z',end_exclusive_utc='2026-09-24T00:00:00Z',
                  seed_usdt=1000,source_coverage=engine._read_json(EXP/'source_coverage.json'),
                  native_and_stressed=scenarios,fee_only_reference=hour_results['Binance']['metrics']['all'],
                  zero_funding_prefix=engine._read_json(parity),
                  limits='Market-fill price stress is a scenario. Historical lot sizes, market depth, and margin tiers are unmodeled. H1 open mark proxies used where no settlement mark is published. Some constituent price histories end before the final day close. 2025/26 retrospective chronological scores; no independent forward validation.')
    (EXP/'summary.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':
    main()
