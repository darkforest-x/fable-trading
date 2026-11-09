"""Buy V1.7 at an observable perpetual discount or sell at a premium versus the same spot coin."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_spot_lead as lead
from yoyo.evaluation import cloud_close_spot_repaired_wallet as repaired

EXP = lead.EXP.parent/'exp-cloud-close-perp-basis-20261109-v1'


def observations(panel):
    """Spot/futures prior Monday's H1 ranges and latest completed H1 closes of the confirmed 12H key."""
    ready = (panel.covered & panel.spot_monday_zero_trade_hours.eq(0) & panel.spot_key_zero_trade_hours.eq(0)
             & panel.spot_symbol.eq(panel.symbol+'USDT'))
    basis = pd.Series(np.nan,index=panel.index)
    ratios = {}
    for ticker,group in panel[ready].groupby('spot_symbol'):
        with np.load(lead.prices.history_slot(ticker)/'quotes.npz') as src:
            frame = pd.DataFrame(src['prices'],columns=lead.prices.OHLC,index=src['clock'])
        for r in group.itertuples():
            start = pd.Timestamp(r.week_start_utc).value//1_000_000
            end = pd.Timestamp(r.monday_end_utc).value//1_000_000
            last = r.entry_ms-lead.prices.HOUR
            monday = frame.loc[start:end-1]
            if (end>r.entry_ms or last not in frame.index or
                    not np.array_equal(monday.index,np.arange(start,end,lead.prices.HOUR))):
                continue
            comparable = (monday.high.max()+monday.low.min())/(r.spot_monday_high+r.spot_monday_low)
            ratios[r.Index]=comparable
            if not .98<=comparable<=1.02:
                continue
            basis.at[r.Index] = r.side*(frame.at[last,'close']/r.spot_key_close-1)
    return pd.DataFrame(dict(signed_basis=basis,prior_monday_scale=pd.Series(ratios)))


def main():
    previous = lead.EXP.parent/'exp-cloud-close-spot-repaired-wallet-20261109-v1'
    cfg = json.loads((previous/'config.json').read_text())
    frame = pd.read_csv(repaired.ROOT/cfg['inputs']['repaired_panel'],dtype={'symbol':str}).set_index('event_id',verify_integrity=True)
    obs = observations(frame)
    obs.index.name='event_id'
    obs.to_csv(EXP/'basis_observations.csv.gz')
    ok = obs.signed_basis.notna().astype('boolean')
    eligible = obs.signed_basis.le(-.001).astype('boolean').where(ok)
    decisions = pd.DataFrame(dict(covered=ok,discount=eligible),index=frame.index)
    sources = repaired._validate_settlement_sources(cfg)
    lead.wallet.compare_filter(decisions,'discount',EXP,sources,reuse_coverage=previous/'lead')


if __name__=='__main__':
    main()
