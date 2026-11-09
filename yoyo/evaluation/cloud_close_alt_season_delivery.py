"""Settle newly held retired contracts and examine the inverse-regime boundary before reporting its cash."""
import gc
import json

import pandas as pd

from yoyo.evaluation import cloud_close_alt_season as season
from yoyo.evaluation import cloud_close_okx_retired_delivery as delivery
from yoyo.evaluation import cloud_close_verified_exposure as exposure

source = season.source
parent = season.parent
old = parent.old
EXP = season.EXP.parent/'exp-cloud-close-alt-season-delivery-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed relative-regime delivery assessment')
    _,quotes,src = source.inputs()
    costs = json.loads((old.DELIVERY/'config.json').read_text())
    prior = json.loads((season.EXP/'summary.json').read_text())
    results = {}
    for label,meta in prior['arms'].items():
        tape = pd.read_csv(season.EXP/label/'selected_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
        unresolved = pd.DataFrame(meta['new_or_uncertain_tail_exits']).drop_duplicates()
        evidence = {}
        for s,g in unresolved.groupby('symbol'):
            end = src[s][2]
            if not g.exit_ms.eq(end-parent.wallet.HOUR).all():
                raise ValueError(f'{s} native source stopped before its official termination')
            f = EXP/f'index_{s}.csv'
            if not f.is_file():
                m = delivery.index_data(dict(costs,symbols=[s]),end,directory=None)
                m.to_csv(f,index=False)
            m = pd.read_csv(f)
            price = float(m.close.mean())
            mask = tape.event_id.isin(g.event_id)&tape.exit_reason.eq('series_tail_close')
            tape.loc[mask,['exit_price','exit_reason','exit_fee_rate']] = [price,'delisting_settlement',0.]
            evidence[s] = dict(last_1h_index_close_mean=price,affected_legs=int(mask.sum()),sample_minutes=len(m))
        outdir = EXP/label
        outdir.mkdir(exist_ok=True)
        parent.save_execution_tape(tape,outdir)
        data,_ = old.parent.assemble(tape,old.changed_limits(tape,quotes),'OKX',quotes)
        market = old.exposure.underlying(data,quotes)
        checks = {}
        for ratio in ([1.5] if label=='following' else [1.5,1.]):
            for slip in [0.,.0005]:
                key = f'{ratio:g}x'+('_stress' if slip else '')
                # The inverse regime has a potential MOODENG boundary; the
                # decision uses the same minute ordering evidence as the leverage study.
                if label=='opposite':
                    name = 'inverse_'+key
                    checks[key] = exposure.simulate(data,market,quotes,pd.Series(ratio,index=market[-1].event_id),
                                                     slip,outdir,name)
                else:
                    checks[key] = old.exposure.simulate(data,market,ratio,slip,quote_loader=quotes,directory=outdir)
                print(label,key,checks[key].get('metrics',{}).get('all'),flush=True)
                gc.collect()
        results[label] = dict(cases=checks,delivery=evidence)
    source.correction.archive.prices.safe_json(EXP/'summary.json',dict(arms=results,
          caveat='The official final-hour index mean uses one-minute samples, zero delivery fee conditional where unpublished. Funding omitted.'))


if __name__=='__main__':
    main()
