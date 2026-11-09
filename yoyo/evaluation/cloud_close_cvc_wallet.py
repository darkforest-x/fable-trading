"""Readmit the complete corrected V1.7 cash account after recovering one CVC traded-price hour."""
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_notice_tail_risk as source
from yoyo.evaluation import cloud_close_double_minute_risk as minute

correction = source.correction
parent = correction.parent
archive = correction.archive
wallet = correction.wallet
old = parent.old
PROOF = source.EXP.parent/'exp-cloud-close-cvc-trade-hour-20261109-v1'
EXP = source.EXP.parent/'exp-cloud-close-cvc-wallet-20261109-v1'
RISK = source.EXP.parent/'exp-cloud-close-cvc-wallet-minutes-20261109-v1'


def checked_quotes():
    proof = json.loads((PROOF/'source_check.json').read_text())
    if proof.get('verified') is not True:
        raise ValueError('CVC native tick recovery has not passed source admission')
    _,old_quotes,src = source.inputs()
    filepath = wallet.ROOT/proof['output_series_path']
    corrected = pd.read_csv(filepath,float_precision='round_trip')
    before,prices = old_quotes('CVC')
    extra = corrected[~corrected.time.isin(before)]
    stamp = pd.Timestamp('2022-10-26T03:00Z').value//1_000_000
    original = corrected[corrected.time.isin(before)]
    if not (extra.time.tolist()==[stamp] and np.array_equal(before,original.time)
            and np.array_equal(prices,original[archive.FIELDS].to_numpy())):
        raise ValueError('CVC price source edit was not limited to the verified hour')
    src['CVC'] = (filepath,None,src['CVC'][2])

    @lru_cache(maxsize=None)
    def quotes(s):
        if s=='CVC':
            return corrected.time.to_numpy(),corrected[archive.FIELDS].to_numpy()
        return old_quotes(s)

    return quotes,src


def pool():
    quotes,src = checked_quotes()
    prior,_,_,_ = correction.pool()
    ranks,new,meta = archive.retired_symbol('CVC',src['CVC'][2],hour_file=src['CVC'][0])
    full = pd.DataFrame(new)
    prior_file = archive.prices.CACHE/'series/CVC-USDT-SWAP/bars1h.csv.gz'
    _,old_bars = parent.repair.market.frames((prior_file,None,src['CVC'][2]))
    old_rank = archive.leadership.trailing_return(old_bars.reset_index())
    changed_clocks = ranks.dropna().index.symmetric_difference(old_rank.dropna().index)
    # Adding a tenth available name would require rebuilding other names'
    # source-screened pool. Verify that all affected clocks already qualify.
    coverage = {}
    for s in ['BTC','ETH','SOL','BNB','XRP','DOGE','ADA','LINK','LTC','AVAX','TRX','DOT','BCH','UNI']:
        _,bars = parent.repair.market.frames(src[s])
        coverage[s] = archive.leadership.trailing_return(bars.reset_index()).notna()
    peers = pd.DataFrame(coverage).reindex(changed_clocks).fillna(False).sum(axis=1)
    if peers.lt(10).any():
        raise ValueError('An affected opportunity clock needs a full new peer-universe screen')
    existing = prior[prior.symbol.eq('CVC')]
    # Unchanged clocks at which CVC was originally ineligible can be evaluated
    # via the actual complete 90d ranks and the same ten observed peer minimum.
    times = full.loc[full.entry_ms.isin(ranks.dropna().index),'entry_ms'].unique()
    lower = pd.DataFrame(coverage).reindex(times).fillna(False).sum(axis=1)
    if lower.lt(10).any():
        raise ValueError('CVC entry does not have ten audited other contemporaneous peers')
    eligible = full[full.entry_ms.isin(times)]
    changed,_ = archive.risk.hourly.tested.expiry.time_in_force(eligible)
    rows = pd.concat([prior[prior.symbol.ne('CVC')],changed],ignore_index=True)
    return rows,quotes,src,dict(cvc_source=meta,former_cvc_eligible=len(existing)//2,
               corrected_cvc_eligible=len(changed)//2,added_cvc_eligible=sorted(set(changed.event_id)-set(existing.event_id)),
               removed_cvc_eligible=sorted(set(existing.event_id)-set(changed.event_id)),changed_90d_clocks=len(changed_clocks))


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed CVC source repair and portfolio')
    rows,quotes,src,meta = pool()
    result = parent.run_wallet(rows,quotes,src,EXP/'account')
    ids = pd.read_csv(EXP/'account/funded_1.5x.csv.gz',usecols=['event_id']).event_id
    old_ids = pd.read_csv(source.BASE/'funded_1.5x.csv.gz',usecols=['event_id']).event_id
    result.update(**meta,added_funded=sorted(set(ids)-set(old_ids)),displaced_funded=sorted(set(old_ids)-set(ids)),
                  historical_funding='omitted')
    archive.prices.safe_json(EXP/'summary.json',result)


def check_minutes():
    if (RISK/'summary.json').exists():
        raise FileExistsError('Completed repaired CVC minute measurement')
    quotes,_ = checked_quotes()
    directory = EXP/'account'
    eq,capture = minute.snapshots(quotes,RISK,directory)
    metrics = json.loads((EXP/'summary.json').read_text())['cases']['extra_5bp']['metrics']['all']
    if (metrics['dd_mark_pct']>=50 or metrics['dd_bound_pct']<50):
        archive.prices.safe_json(RISK/'summary.json',dict(original_upper_pct=metrics['dd_bound_pct'],
            original_observed_pct=metrics['dd_mark_pct'],no_minute_refinement_needed=True,
            scope='Hourly evidence alone already determines the 50% decision.'))
        return
    if not (RISK/'post_trim_units.csv.gz').exists():
        tape = pd.read_csv(directory/'selected_paths.csv.gz',dtype={'symbol':str},float_precision='round_trip')
        data,_ = old.parent.assemble(tape,old.changed_limits(tape,quotes),'OKX',quotes)
        old.exposure.simulate(data,old.exposure.underlying(data,quotes),1.5,.0005,quote_loader=quotes,snapshot=capture)
    minute.refine(RISK,eq,quotes,metrics,scope='Native traded prices on the corrected CVC/ICX/LRC/WAVES portfolio. Historical funding, price-mark gaps and capacity remain unmodeled.')


if __name__=='__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--minutes',action='store_true')
    if p.parse_args().minutes:
        check_minutes()
    else:
        main()
