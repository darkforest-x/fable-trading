"""Transfer one frozen post-doubling protection hypothesis to original Binance V1.7 with native funding."""
import gc
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_binance_capacity_funding as funding
from yoyo.evaluation import cloud_close_doubled_candle_low as stops

EXP = funding.EXP.parent/'exp-cloud-close-binance-doubled-stop-transfer-20261109-v1'


def scenario(prepared,data,no_settle,key,boundary,slip,*,directory=EXP):
    folder = directory/key
    saved = folder/'result.json'
    if saved.exists():
        recovered = json.loads(saved.read_text())
        parents = pd.read_csv(folder/'parents.csv.gz')
        expected = recovered['metrics']['all']['final_usdt']
        if len(parents)!=1583 or not np.isclose(1000+parents.cash_pnl.sum(),expected,rtol=0.,atol=.02):
            raise ValueError('Stored funding scenario no longer reconciles to actual parent ledger')
        return recovered
    folder.mkdir(exist_ok=True)
    children, parents, equity, flows, result = funding.relative._run_scenario(
        data,prepared['tape'],boundary,slip,prepared['multipliers'],prepared['previous_close'],
        prepared['native_open'],prepared['directions'],no_settle,hourly_cap_fraction=funding.HOURLY_CAP,
        entry_limits=prepared['entry_matrix'])
    result.pop('orders')
    audit = funding._cash_bridge(parents,flows,equity)
    reference = prepared['reference'].set_index('event_id')
    limits = prepared['capacity_audit']['entry_limits']
    keys = parents.set_index('event_id') if 'event_id' in parents else parents
    if (set(keys.index)!=set(reference.index) or len(children)!=4*len(reference) or
            not np.array_equal(keys.entry_ms, reference.entry_ms.reindex(keys.index)) or
            (keys.notional > limits.reindex(keys.index) + 1e-6).any()):
        raise ValueError('Changed Binance entry, count, or native cash budget')
    result['cash_bridge'] = audit
    keys.to_csv(folder/'parents.csv.gz',index_label='event_id')
    equity.to_csv(folder/'hourly.csv.gz',index_label='hour_end_utc')
    flows.to_csv(folder/'funding.csv.gz',index=False)
    funding._write_json(saved,result)
    print(key,result['metrics']['all'],audit['applied_funding_cashflow_usdt'],flush=True)
    return result


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed cross-venue doubled-winner account')
    config = funding._json(funding.CONFIG_PATH)
    funding._validate_config(config)
    p = funding._prepare_real_account(config)
    original = p['data']
    runners = p['market'][-1]
    routed,affected,_ = stops.doubling.route(original,p['quotes'],[1,1,3,3])
    events = []
    for r in runners.itertuples():
        if r.side==1:
            events.extend(stops.stop_path(r,*p['quotes'](r.symbol)))
    activations = pd.DataFrame(events)
    fixed = activations.loc[activations['mode'].eq('fixed')].copy()
    fixed.to_csv(EXP/'fixed_stop_activations.csv.gz',index=False)
    changes = fixed[fixed.changed].set_index('event_id')
    checked,n = stops.apply_stops(routed,p['quotes'],changes)
    outputs = {}
    half = funding._json(funding.EXP/'summary.json')
    for name,data in [('no_doubling_sale',routed),('fixed_low',checked)]:
        empty,coverage = funding.relative._check_covered_windows(data['selected'],p['tape'],p['months'])
        for field in ['expected_positions','expected_child_legs','exposure_map']:
            data[field] = original[field]
        data['funding_no_settlement_windows'] = empty
        data['funding_source_audit'] = coverage
        for slip in ([0.] if name=='no_doubling_sale' else [0.,.0005]):
            for boundary in ['include_boundary','exclude_boundary']:
                key = name+('_stress' if slip else '')+'_'+boundary
                outputs[key] = scenario(p,data,empty,key,boundary,slip)
                gc.collect()
    funding._write_json(EXP/'summary.json',dict(arms=outputs, original_baseline_file=str(funding.EXP/'summary.json'),
           native_positions=len(runners),changed_doubling_parents=len(affected),confirmed_doubles=len(fixed),
           fixed_stops_changed=len(changes),changed_children=n, reference_status=half['status']))


if __name__ == '__main__':
    main()
