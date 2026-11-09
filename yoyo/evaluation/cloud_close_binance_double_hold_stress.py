"""Check adverse market fills on the native funded Binance runner without a doubling-price sale."""
from yoyo.evaluation import cloud_close_binance_doubled_stop_transfer as transfer

funding = transfer.funding
EXP = transfer.EXP.parent/'exp-cloud-close-binance-double-hold-stress-20261109-v1'


def main():
    if (EXP/'summary.json').is_file():
        raise FileExistsError('Completed stressed post-double original runner')
    cfg = funding._json(funding.CONFIG_PATH)
    funding._validate_config(cfg)
    p = funding._prepare_real_account(cfg)
    data, affected, _ = transfer.stops.doubling.route(p['data'],p['quotes'],[1,1,3,3])
    empty, coverage = funding.relative._check_covered_windows(data['selected'],p['tape'],p['months'])
    for k in ['expected_positions','expected_child_legs','exposure_map']:
        data[k] = p['data'][k]
    data.update(funding_no_settlement_windows=empty,funding_source_audit=coverage)
    results = {}
    for boundary in ['include_boundary','exclude_boundary']:
        results[boundary] = transfer.scenario(p,data,empty,boundary,boundary,.0005,directory=EXP)
    funding._write_json(EXP/'summary.json',dict(arms=results,native_positions=data['expected_positions'],
                                              affected_doubling_parents=len(affected)))


if __name__ == '__main__':
    main()
