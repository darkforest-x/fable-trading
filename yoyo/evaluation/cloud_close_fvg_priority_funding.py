"""Fund the frozen same-close FVG priority roster and export the native positions at risk."""
import gc
import json

import pandas as pd

from yoyo.evaluation import cloud_close_fvg_priority as priority
from yoyo.evaluation import cloud_close_hourly_trim_funding as replay
from yoyo.evaluation import cloud_close_trim_funding_inputs as source

admin = priority.admin
spot = priority.spot
EXP = admin.EXP.parent/'exp-cloud-close-fvg-priority-funding-20261109-v1'


def fund_ranked_roster(expected, rank, directory, settlement_sources):
    if (directory/'summary.json').exists():
        raise FileExistsError(f'Completed funding-aware study in {directory}')
    extra = set(json.loads((admin.archive.EXP/'summary.json').read_text())['archived_additions'])
    extra.update(json.loads((admin.study.EXP/'summary.json').read_text())['new_archive_cohorts']['priced'])
    spec,marks = spot.rank.market_spec(directory)
    for reference in settlement_sources:
        spec.update(json.loads((reference/'config.json').read_text())['additional_settlements'])
        marks.update(pd.read_csv(reference/'settlement_index_1m.csv').groupby('symbol')[['low','close','high']].mean().to_dict('index'))
    with admin.archive.market_quotes(extra):
        full = admin.archive.pool(extra)
        _,selected = admin.study.tenth.trend.inputs('Binance',full[~full.is_control & full.event_id.isin(expected)])
        paid = admin.adjusted_legs(selected,extra,spec)
        paid['entry_order'] = paid.event_id.map(rank)
        if paid.entry_order.isna().any():
            raise ValueError('Frozen funded IDs are missing a contemporaneous rank')
        data = admin.archive.native.prepare(admin.resolve_settlements(paid,marks,'proxy'))
        data = replay._expand_hourly(data,admin.archive.native.quotes,.2)
    legs = data['selected']
    if set(legs.event_id)!=set(expected) or not legs.groupby('event_id').size().eq(2).all():
        raise ValueError('Original FVG priority child positions or funded IDs differ')
    data['funding_input_audit'] = dict(frozen_priority=True,funded_position_ids_verified=len(expected))
    if not (directory/'source_coverage.json').exists():
        source.collect(legs,directory)
    tape = pd.read_csv(directory/'funding_tape.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    data['funding_no_settlement_windows'] = replay.verified_empty_windows(pd.read_csv(directory/'source_months.csv.gz'))
    result = {}
    for slip in (0.,.0005):
        cfg = dict(account=dict(new_order_exposure_fraction=.9,initial_cash_usdt=1000.,expected_positions=len(expected)),market_slip=slip)
        for boundary in ('include_boundary','exclude_boundary'):
            name = ('stress_' if slip else '')+boundary
            folder = directory/name
            folder.mkdir(exist_ok=True)
            receipt = folder/'metrics.json'
            if receipt.exists():
                result[name] = json.loads(receipt.read_text())
                continue
            child,parents,equity,flows,r = replay._run_scenario(data,tape,cfg,boundary)
            r['audit'].pop('orders_frame').to_csv(folder/'orders.csv.gz',index=False)
            for file,frame in [('funded_legs',child),('funded_positions',parents.reset_index()),('applied_funding',flows)]:
                frame.to_csv(folder/(file+'.csv.gz'),index=False)
            equity.to_csv(folder/'hourly_equity.csv.gz',index_label='hour_end_utc')
            receipt.write_text(json.dumps(r,indent=2,allow_nan=False)+'\n')
            result[name] = r
            del child,parents,equity,flows
            gc.collect()
    summary = dict(source=json.loads((directory/'source_coverage.json').read_text()),scenarios={
        name:dict(metrics=r['metrics'],funding_usdt=r['audit']['funding_cashflow_usdt'],
                  actual_child_payments=r['audit']['funding_rows_applied']) for name,r in result.items()})
    (directory/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(summary,indent=2),flush=True)


def main():
    rank = pd.read_csv(priority.EXP/'priority.csv.gz',usecols=['event_id','rank']).set_index('event_id')['rank']
    expected = pd.read_csv(priority.EXP/'funded.csv.gz',usecols=['event_id']).event_id
    fund_ranked_roster(expected, rank, EXP, (spot.vwap.EXP,spot.timing.EXP,priority.fvg.EXP))


if __name__=='__main__':
    main()
