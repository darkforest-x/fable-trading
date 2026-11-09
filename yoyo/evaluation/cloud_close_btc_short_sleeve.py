"""Let a native BTC bearish cloud flip claim otherwise unused real cash after V1.7 signals."""
import json

import numpy as np
import pandas as pd

from yoyo.evaluation import cloud_close_notice_tail_risk as source

correction = source.correction
parent = correction.parent
wallet = correction.wallet
EXP = source.EXP.parent/'exp-cloud-close-btc-short-sleeve-20261109-v1'
MAJOR = EXP.parent/'exp-cloud-close-major-cross-20261109-v1'


def main():
    if (EXP/'summary.json').exists():
        raise FileExistsError('Completed native bearish BTC sleeve')
    rows,src,_,_ = correction.pool()
    _,quotes,_ = source.inputs()
    macro = pd.read_csv(MAJOR/'native_paths_tp0.csv.gz',dtype={'symbol':str},float_precision='round_trip')
    macro = macro[macro.symbol.eq('BTC')&macro.side.eq(-1)&macro.entry_ms.ge(wallet.START)&macro.entry_ms.lt(wallet.END)].copy()
    # Put the extra signal behind every pre-existing simultaneous applicant.
    if not np.isfinite(rows.entry_order).all():
        raise ValueError('Existing opportunity priority must be finite')
    macro['entry_order'] = float(rows.entry_order.max())+1.
    if not np.allclose(macro.exit_fee_rate,wallet.TAKER) or not macro.entry_status.eq('executed').all():
        raise ValueError('Unpriced or non-market benchmark short reached admission')
    # All components of a major short use its identical exit. No short
    # doubling target exists in the reference native cash engine.
    extra = pd.concat([macro,macro.assign(tp_r=2.)],ignore_index=True)
    combined = pd.concat([rows,extra],ignore_index=True)
    result = parent.run_wallet(combined,quotes,src,EXP/'account')
    actual = pd.read_csv(EXP/'account/funded_1.5x.csv.gz',index_col='event_id')
    previous = pd.read_csv(source.BASE/'funded_1.5x.csv.gz',index_col='event_id')
    hits = actual[actual.index.isin(macro.event_id)]
    result.update(bearish_btc_candidates=len(macro),funded_btc_shorts=len(hits),
                  original_parents_displaced=len(set(previous.index)-set(actual.index)),
                  newly_funded_original=len(set(actual.index)-set(previous.index)-set(hits.index)),
                  actual_hedge_cash_pnl=float(hits.cash_pnl.sum()),originals_cash_pnl=float(actual.cash_pnl.sum()-hits.cash_pnl.sum()),
                  reference=json.loads((correction.EXP/'summary.json').read_text())['cases']['fee_only']['metrics']['all'])
    correction.archive.prices.safe_json(EXP/'summary.json',result)


if __name__=='__main__':
    main()
